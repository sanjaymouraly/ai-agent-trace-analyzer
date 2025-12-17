"""OpenTelemetry export helper.

TraceLens can ingest OTLP/HTTP JSON payloads. This module converts a canonical
TraceLens trace into an OTLP JSON resource so that an existing OpenTelemetry
collector (or a collector's file exporter) can feed the debugger.

The OpenTelemetry SDK is **not** a dependency of TraceLens: the conversion is
pure data manipulation and works without the SDK installed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.schema import ExecutionTrace

OTLP_VERSION = "1.0.0"


def _nanos(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return str(int(value.timestamp() * 1_000_000_000))


def _attributes(values: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            encoded: dict[str, Any] = {"boolValue": value}
        elif isinstance(value, int):
            encoded = {"intValue": str(value)}
        elif isinstance(value, float):
            encoded = {"doubleValue": value}
        else:
            encoded = {"stringValue": str(value)}
        result.append({"key": key, "value": encoded})
    return result


def _status_code(status: str) -> int:
    return {"ok": 1, "error": 2, "timeout": 2, "cancelled": 2}.get(status, 0)


def trace_to_otlp(trace: ExecutionTrace) -> dict[str, Any]:
    """Convert a canonical trace into an OTLP/JSON export payload."""
    spans: list[dict[str, Any]] = []
    for span in trace.spans:
        attributes: dict[str, Any] = {
            "gen_ai.system": span.provider,
            "gen_ai.request.model": span.model,
            "gen_ai.tool.name": span.tool_name,
            "openinference.span.kind": span.action_type.value,
            "tracelens.action_id": span.action_id,
            "tracelens.retry_attempt": span.retry_attempt,
        }
        attributes.update({str(key): value for key, value in (span.attributes or {}).items()})
        entry: dict[str, Any] = {
            "traceId": trace.trace_id,
            "spanId": span.span_id,
            "name": span.name,
            "kind": 1 if span.action_type.value in {"tool", "retrieval"} else 3,
            "startTimeUnixNano": _nanos(span.start_time),
            "endTimeUnixNano": _nanos(span.end_time or span.start_time),
            "attributes": _attributes(attributes),
            "status": {"code": _status_code(span.status.value)},
        }
        if span.parent_span_id:
            entry["parentSpanId"] = span.parent_span_id
        if span.exception:
            entry["events"] = [
                {
                    "name": "exception",
                    "timeUnixNano": _nanos(span.end_time or span.start_time),
                    "attributes": _attributes({"exception.message": span.exception}),
                }
            ]
        spans.append(entry)
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": _attributes(
                        {
                            "service.name": trace.agent_id or "agent",
                            "tracelens.task": trace.task,
                            "tracelens.task_id": trace.task_id,
                            "tracelens.session_id": trace.session_id,
                        }
                    )
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "tracelens.sdk", "version": "1.0.0"},
                        "spans": spans,
                    }
                ],
            }
        ]
    }


def write_otlp_file(trace: ExecutionTrace, path: str) -> str:
    """Write an OTLP/JSON export to disk. Returns the written path."""
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(trace_to_otlp(trace), indent=2, ensure_ascii=False), encoding="utf-8")
    return str(target)
