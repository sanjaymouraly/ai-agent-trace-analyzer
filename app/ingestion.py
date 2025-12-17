"""Safe, version-aware importers for canonical JSON, JSONL and OTLP JSON."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.otlp import split_otlp_json
from app.schema import ExecutionTrace, utc_now

SECRET_KEY = re.compile(
    r"(?i)(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|secret|cookie|private[_-]?key)"
)
BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+")
SECRET_ASSIGNMENT = re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\b(\s*[:=]\s*)([^\s,;]+)")
API_TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b")


def redact(value: Any, *, enabled: bool = True, key: str = "") -> Any:
    """Return a recursively redacted copy. The stored original payload is not mutated."""
    if not enabled:
        return value
    if SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, enabled=enabled, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, enabled=enabled) for v in value]
    if isinstance(value, tuple):
        return [redact(v, enabled=enabled) for v in value]
    if isinstance(value, str):
        value = BEARER.sub(r"\1[REDACTED]", value)
        value = SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", value)
        return API_TOKEN.sub("[REDACTED]", value)
    return value


def _text(value: Any, default: str = "") -> str:
    return str(value) if value is not None else default


def _timestamp(value: Any) -> Any:
    """Normalize OTLP nanoseconds / Unix seconds; retain ISO values for Pydantic."""
    if value is None or value == "":
        return None
    try:
        numeric = int(value)
        if numeric > 10**14:  # OTLP Unix nanoseconds
            return datetime.fromtimestamp(numeric / 1_000_000_000, tz=timezone.utc).isoformat()
        if numeric > 10**11:  # Unix milliseconds
            return datetime.fromtimestamp(numeric / 1000, tz=timezone.utc).isoformat()
        if numeric > 10**8:
            return datetime.fromtimestamp(numeric, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    return value


def _status(value: Any, error: Any = None) -> str:
    if isinstance(value, dict):
        code = value.get("code")
        if code in (2, "2", "ERROR", "STATUS_CODE_ERROR"):
            return "error"
        if code in (1, "1", "OK", "STATUS_CODE_OK"):
            return "ok"
        value = value.get("message") or value.get("status")
    text = _text(value).lower()
    if error:
        return "error"
    if any(term in text for term in ("timeout", "timed_out")):
        return "timeout"
    if any(term in text for term in ("error", "failed", "failure", "exception")):
        return "error"
    if any(term in text for term in ("success", "completed", "ok", "done")):
        return "ok"
    if any(term in text for term in ("cancel",)):
        return "cancelled"
    if any(term in text for term in ("running", "in_progress")):
        return "running"
    return "unknown"


def _action_type(value: Any, name: str = "", tool_name: str | None = None) -> str:
    raw = _text(value).lower().replace(" ", "_")
    candidate = raw.split(".")[-1]
    aliases = {
        "tool": "tool",
        "tool_call": "tool",
        "function": "tool",
        "function_call": "tool",
        "llm": "model",
        "chat_model": "model",
        "model": "model",
        "generation": "model",
        "retriever": "retrieval",
        "retrieval": "retrieval",
        "vector_store": "retrieval",
        "chain": "agent",
        "agent": "agent",
        "workflow": "agent",
        "graph": "agent",
        "planner": "planning",
        "planning": "planning",
        "plan": "planning",
        "delegation": "delegation",
        "handoff": "delegation",
        "evaluation": "evaluation",
        "evaluator": "evaluation",
        "state": "state",
        "memory": "state",
    }
    if candidate in aliases:
        return aliases[candidate]
    if tool_name:
        return "tool"
    lower_name = name.lower()
    if any(t in lower_name for t in ("retriev", "search", "vector")):
        return "retrieval"
    if any(t in lower_name for t in ("tool", "function", "browser", "shell", "python", "api_call")):
        return "tool"
    if any(t in lower_name for t in ("llm", "model", "completion", "generation")):
        return "model"
    if any(t in lower_name for t in ("plan", "route", "decide")):
        return "planning"
    if any(t in lower_name for t in ("delegate", "handoff", "subagent")):
        return "delegation"
    return "unknown"


def _otlp_attributes(attrs: Any) -> dict[str, Any]:
    if isinstance(attrs, dict):
        return dict(attrs)
    result: dict[str, Any] = {}
    if not isinstance(attrs, list):
        return result
    for item in attrs:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        value = item.get("value", {})
        if isinstance(value, dict):
            for kind in ("stringValue", "intValue", "doubleValue", "boolValue", "arrayValue", "kvlistValue"):
                if kind in value:
                    v = value[kind]
                    if kind == "arrayValue" and isinstance(v, dict):
                        v = [x.get("stringValue", x) if isinstance(x, dict) else x for x in v.get("values", [])]
                    if kind == "kvlistValue" and isinstance(v, dict):
                        v = _otlp_attributes(v.get("values", []))
                    value = v
                    break
            else:
                value = value
        result[str(item["key"])] = value
    return result


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in ("{", "["):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
    return value


def _span_from_mapping(item: dict[str, Any], parent_defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    defaults = parent_defaults or {}
    attrs = dict(item.get("attributes") or {})
    # OTLP JSON uses a list of key/value objects. Canonical attributes remain a map.
    attrs = _otlp_attributes(attrs)
    if item.get("extra") is not None:
        attrs.setdefault("framework_extra", item.get("extra"))
    if item.get("execution_order") is not None:
        attrs.setdefault("producer_execution_order", item.get("execution_order"))
    links: list[str] = list(item.get("span_links", []) or [])
    for link in item.get("links", []) or []:
        if isinstance(link, dict) and link.get("spanId"):
            links.append(str(link["spanId"]))
    name = _text(item.get("name") or item.get("operation_name") or item.get("run_type") or item.get("type"), "unnamed action")
    tool = item.get("tool_name") or attrs.get("tool.name") or attrs.get("tool_name") or item.get("tool")
    if isinstance(tool, dict):
        tool = tool.get("name")
    tool = _text(tool) or None
    kind = (
        item.get("action_type")
        or item.get("span_kind")
        or attrs.get("openinference.span.kind")
        or attrs.get("span.kind")
        or item.get("run_type")
    )
    err = item.get("exception") or item.get("error") or attrs.get("exception.message")
    status_source = item.get("status") or item.get("status_code") or attrs.get("status")
    # LangSmith/LangChain commonly represent errors as an ``error`` field, and OTLP
    # stores exception text in span events / attributes.
    if not err:
        for event in item.get("events", []) or []:
            event_attrs = _otlp_attributes(event.get("attributes", [])) if isinstance(event, dict) else {}
            if isinstance(event, dict) and "exception" in _text(event.get("name")).lower():
                err = event_attrs.get("exception.message") or event_attrs.get("exception.type") or "exception event"
                break
    inputs = item.get("inputs", item.get("input", attrs.get("input.value", attrs.get("llm.prompt"))))
    outputs = item.get("outputs", item.get("output", attrs.get("output.value", attrs.get("llm.completion"))))
    token_usage = item.get("token_usage") or attrs.get("llm.token_count") or attrs.get("token_usage") or {}
    if not isinstance(token_usage, dict):
        token_usage = {"total_tokens": token_usage} if isinstance(token_usage, (int, float)) else {}
    if not token_usage:
        token_usage = {
            k: attrs[v]
            for k, v in {
                "prompt_tokens": "llm.token_count.prompt",
                "completion_tokens": "llm.token_count.completion",
                "total_tokens": "llm.token_count.total",
            }.items()
            if v in attrs
        }
    tool_call_id = item.get("tool_call_id") or attrs.get("tool_call_id") or attrs.get("tool.call.id")
    explicit_deps = item.get("explicit_dependencies") or item.get("depends_on") or attrs.get("depends_on") or []
    if isinstance(explicit_deps, str):
        explicit_deps = [explicit_deps]
    return {
        "trace_id": _text(item.get("trace_id") or defaults.get("trace_id")),
        "run_id": _text(item.get("run_id") or defaults.get("run_id")),
        "session_id": item.get("session_id") or defaults.get("session_id"),
        "task_id": item.get("task_id") or defaults.get("task_id"),
        "agent_id": item.get("agent_id") or defaults.get("agent_id") or attrs.get("agent_id"),
        "span_id": _text(item.get("span_id") or item.get("id"), uuid4().hex[:16]),
        "parent_span_id": item.get("parent_span_id") or item.get("parentSpanId") or item.get("parent_run_id"),
        "span_links": links,
        "action_id": item.get("action_id") or attrs.get("action_id"),
        "name": name,
        "action_type": _action_type(kind, name, tool),
        "start_time": _timestamp(
            item.get("start_time")
            or item.get("startTime")
            or item.get("start_time_unix_nano")
            or item.get("startTimeUnixNano")
            or item.get("start_time_ms")
        )
        or utc_now(),
        "end_time": _timestamp(
            item.get("end_time")
            or item.get("endTime")
            or item.get("end_time_unix_nano")
            or item.get("endTimeUnixNano")
            or item.get("end_time_ms")
        ),
        "status": _status(status_source, err),
        "inputs": _json_value(inputs),
        "outputs": _json_value(outputs),
        "tool_name": tool,
        "tool_call_id": _text(tool_call_id) or None,
        "model": _text(
            item.get("model") or attrs.get("llm.model_name") or attrs.get("gen_ai.request.model") or attrs.get("model")
        )
        or None,
        "provider": _text(
            item.get("provider") or attrs.get("llm.provider") or attrs.get("gen_ai.system") or attrs.get("provider")
        )
        or None,
        "token_usage": token_usage,
        "cost_usd": item.get("cost_usd") or attrs.get("cost.usd"),
        "retry_attempt": item.get("retry_attempt") or item.get("attempt") or attrs.get("retry.attempt"),
        "exception": _text(err) or None,
        "explicit_dependencies": [str(x) for x in explicit_deps],
        "artifacts": item.get("artifacts") or [],
        "attributes": attrs,
    }


def _from_otlp(payload: dict[str, Any]) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    warnings: list[str] = []
    resource_spans = payload.get("resourceSpans", []) or []
    for resource in resource_spans:
        resource_attrs = _otlp_attributes((resource.get("resource") or {}).get("attributes", []))
        for scope in resource.get("scopeSpans", resource.get("instrumentationLibrarySpans", [])) or []:
            scope_attrs = {
                **resource_attrs,
                **_otlp_attributes((scope.get("scope") or scope.get("instrumentationLibrary") or {}).get("attributes", [])),
            }
            for otlp_span in scope.get("spans", []) or []:
                item = dict(otlp_span)
                item["attributes"] = {**scope_attrs, **_otlp_attributes(item.get("attributes", []))}
                item.setdefault("trace_id", item.get("traceId"))
                item.setdefault("span_id", item.get("spanId"))
                item.setdefault("parentSpanId", item.get("parentSpanId"))
                normalized.append(item)
    if not normalized:
        warnings.append("OTLP payload contained no resourceSpans/scopeSpans spans.")
    # OTLP resources can contain multiple trace IDs; group them separately.
    trace_id = _text(normalized[0].get("trace_id"), uuid4().hex)
    run_id = _text(payload.get("run_id"), trace_id or uuid4().hex)
    return {
        "schema_version": "otlp/1.0",
        "trace_id": trace_id,
        "run_id": run_id,
        "task": _text(payload.get("task") or "OTLP imported execution"),
        "status": "unknown",
        "spans": normalized,
        "_warnings": warnings,
    }


def _from_langchain(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a LangChain/LangSmith-style run export without importing framework code."""
    run = dict(raw)
    trace_id = _text(run.get("trace_id") or run.get("session_id") or run.get("id"), uuid4().hex)
    root_id = _text(run.get("id") or run.get("run_id"), uuid4().hex)
    spans: list[dict[str, Any]] = []

    def visit(item: dict[str, Any], parent_id: str | None = None) -> None:
        rid = _text(item.get("id") or item.get("run_id"), uuid4().hex[:16])
        row = {
            "span_id": rid,
            "parent_span_id": item.get("parent_run_id") or parent_id,
            "trace_id": trace_id,
            "run_id": root_id,
            "name": item.get("name") or item.get("run_type") or "LangChain run",
            "run_type": item.get("run_type"),
            "start_time": item.get("start_time"),
            "end_time": item.get("end_time"),
            "inputs": item.get("inputs"),
            "outputs": item.get("outputs"),
            "error": item.get("error"),
            "status": "error" if item.get("error") else ("ok" if item.get("end_time") else "running"),
            "tool_name": item.get("tool_name"),
            "session_id": item.get("session_id"),
            "agent_id": item.get("agent_id"),
            "extra": item.get("extra"),
        }
        spans.append(row)
        for child in item.get("child_runs", []) or []:
            if isinstance(child, dict):
                visit(child, rid)

    visit(run)
    return {
        "schema_version": "langchain-run/1.0",
        "trace_id": trace_id,
        "run_id": root_id,
        "task_id": run.get("task_id"),
        "task": run.get("name") or run.get("run_type") or "LangChain execution",
        "session_id": run.get("session_id"),
        "status": "failure" if run.get("error") else "unknown",
        "final_answer": (run.get("outputs") or {}).get("output") if isinstance(run.get("outputs"), dict) else run.get("outputs"),
        "spans": spans,
    }


def normalize_trace(raw: Any, *, source_format: str = "json") -> ExecutionTrace:
    if not isinstance(raw, dict):
        raise ValueError("Each trace record must be a JSON object.")
    warnings: list[str] = []
    if "resourceSpans" in raw:
        normalized = _from_otlp(raw)
        warnings.extend(normalized.pop("_warnings", []))
    elif "child_runs" in raw and ("run_type" in raw or "inputs" in raw or "outputs" in raw):
        normalized = _from_langchain(raw)
        warnings.append(
            "Mapped LangChain/LangSmith run export using the documented run fields; framework-specific extras are retained as attributes."
        )
    else:
        normalized = dict(raw)
        # Common envelope aliases.
        normalized.setdefault("trace_id", normalized.get("id") or uuid4().hex)
        normalized.setdefault("run_id", normalized.get("execution_id") or normalized.get("trace_id"))
        normalized.setdefault("task", normalized.get("task_name") or normalized.get("name") or "Imported agent execution")
        normalized.setdefault("spans", normalized.get("actions") or normalized.get("events") or [])
        if "runs" in normalized and not normalized.get("spans"):
            if len(normalized["runs"]) == 1 and isinstance(normalized["runs"][0], dict):
                inner = normalized["runs"][0]
                inner.setdefault("trace_id", normalized["trace_id"])
                inner.setdefault("run_id", normalized["run_id"])
                normalized = inner
            else:
                raise ValueError("A trace envelope with multiple runs must be split into separate records before import.")
    if not isinstance(normalized.get("spans", []), list):
        raise ValueError("spans/actions must be a JSON array.")
    base = {k: v for k, v in normalized.items() if k not in {"actions", "events", "spans", "runs", "warnings"}}
    base.setdefault("schema_version", "1.0")
    base["provenance"] = {
        **(base.get("provenance") or {}),
        "source_format": source_format,
        "mapping": "otlp-json"
        if "resourceSpans" in raw
        else ("langchain-run" if "child_runs" in raw else "canonical-or-generic-json"),
    }
    raw_spans = normalized.get("spans", [])
    base["spans"] = [_span_from_mapping(item, base) for item in raw_spans if isinstance(item, dict)]
    if len(base["spans"]) != len(raw_spans):
        warnings.append("Non-object span rows were skipped; review the original payload before drawing conclusions.")
    for item in raw_spans:
        if isinstance(item, dict) and not any(
            item.get(k) for k in ("start_time", "startTime", "start_time_unix_nano", "startTimeUnixNano", "start_time_ms")
        ):
            warnings.append(
                f"Span {_text(item.get('span_id') or item.get('spanId') or item.get('id'), 'unknown')} had no start time; ingestion time was used for display order."
            )
    warnings.extend(normalized.get("import_warnings", []))
    if "_warnings" in normalized:
        warnings.extend(normalized["_warnings"])
    if len(base["spans"]) > 20_000:
        raise ValueError("Trace exceeds the 20,000-span per-run safety limit.")
    try:
        trace = ExecutionTrace.model_validate(base)
    except ValidationError as exc:
        # Pydantic's raw errors include untrusted input values; return only structural
        # metadata so malformed requests cannot echo secrets back through API errors.
        safe_errors = [
            {"location": ".".join(str(part) for part in error["loc"]), "message": error["msg"], "type": error["type"]}
            for error in exc.errors(include_url=False)[:20]
        ]
        raise ValueError(f"Trace schema validation failed: {safe_errors}") from exc
    # Event timestamps without timezone are interpreted as UTC, made explicit and warned.
    for time_field, value in [("started_at", trace.started_at), ("ended_at", trace.ended_at)]:
        if value is not None and value.tzinfo is None:
            setattr(trace, time_field, value.replace(tzinfo=timezone.utc))
            warnings.append(f"{time_field} had no timezone; interpreted as UTC.")
    for span in trace.spans:
        for field in ("start_time", "end_time"):
            value = getattr(span, field)
            if value is not None and value.tzinfo is None:
                setattr(span, field, value.replace(tzinfo=timezone.utc))
                warnings.append(f"Span {span.span_id} {field} had no timezone; interpreted as UTC.")
    trace.import_warnings = list(dict.fromkeys(warnings))
    for span in trace.spans:
        span.trace_id = trace.trace_id
        span.run_id = trace.run_id
    return trace


def parse_document(data: bytes, filename: str = "upload.json") -> list[tuple[ExecutionTrace, Any]]:
    """Parse JSON or JSONL, returning normalized records alongside exact original objects."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Uploaded traces must be UTF-8 encoded.") from exc
    ext = filename.lower().rsplit(".", 1)[-1]
    rows: list[Any]
    if ext == "jsonl":
        rows = []
        for line_no, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {line_no}: {exc.msg} (column {exc.colno}).") from exc
        fmt = "jsonl"
    elif ext == "json":
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno}).") from exc
        if isinstance(value, dict) and "resourceSpans" in value:
            rows = split_otlp_json(value)
            fmt = "otlp-json"
        else:
            if isinstance(value, list):
                rows = value
            elif isinstance(value, dict) and isinstance(value.get("traces"), list):
                rows = value["traces"]
            elif (
                isinstance(value, dict)
                and isinstance(value.get("runs"), list)
                and value.get("runs")
                and all(isinstance(r, dict) for r in value["runs"])
            ):
                rows = value["runs"]
            else:
                rows = [value]
            fmt = "json"
    else:
        raise ValueError("Only .json and .jsonl trace uploads are supported.")
    if not rows:
        raise ValueError("The uploaded file contains no trace records.")
    result: list[tuple[ExecutionTrace, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows, 1):
        try:
            trace = normalize_trace(raw, source_format=fmt)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Record {index}: {exc}") from exc
        if trace.run_id in seen:
            raise ValueError(f"Duplicate run_id '{trace.run_id}' occurs more than once in this file.")
        seen.add(trace.run_id)
        result.append((trace, raw))
    return result
