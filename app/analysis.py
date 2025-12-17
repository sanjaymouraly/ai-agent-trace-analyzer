"""Deterministic graph reconstruction and evidence-backed failure analysis.

Rules expose facts and limitations; they do not establish causality from time order.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from datetime import datetime, timezone
from heapq import heapify, heappop, heappush
from typing import Any

from app.schema import ExecutionTrace, TraceSpan

RULE_VERSION = "rules-1.1.0"

#: Canonical failure taxonomy. Every rule emits one of these categories so the
#: UI, API and evaluation harness can rely on a stable vocabulary.
FAILURE_CATEGORIES = (
    "planning_or_orchestration_error",
    "incorrect_tool_selection",
    "invalid_tool_arguments",
    "tool_execution_exception",
    "tool_timeout",
    "rate_limiting",
    "network_failure",
    "authentication_or_permission_failure",
    "missing_or_malformed_external_data",
    "retrieval_context_error",
    "state_dependency_inconsistency",
    "schema_or_parsing_failure",
    "model_api_failure",
    "retry_loop_or_resource_exhaustion",
    "evaluation_or_final_answer_failure",
    "failure_propagation",
    "unknown_or_insufficient_evidence",
)

#: Evidence labels used across findings, hypotheses and experiments.
EVIDENCE_STATUSES = (
    "Observed",
    "Inferred Hypothesis",
    "Experimentally Supported",
    "Experimentally Refuted",
    "Confirmed Under Specified Test Conditions",
    "Insufficient Evidence",
)

MAX_RETRY_ATTEMPTS = 3

AUTH_RE = re.compile(r"(?i)(unauthori[sz]ed|forbidden|permission|access denied|invalid api key|authentication|401|403)")
RATE_RE = re.compile(r"(?i)(rate.?limit|too many requests|\b429\b|quota exceeded)")
TIMEOUT_RE = re.compile(r"(?i)(timeout|timed out|deadline exceeded|read timed out)")
NETWORK_RE = re.compile(r"(?i)(connection reset|connection refused|dns|network|socket|temporarily unavailable|\b5\d\d\b)")
ARG_RE = re.compile(
    r"(?i)(invalid argument|invalid parameter|missing required|validation error|schema mismatch|unexpected keyword|bad request|malformed argument)"
)
PARSE_RE = re.compile(r"(?i)(json decode|parse error|malformed response|invalid json|schema validation|could not parse)")
MISSING_RE = re.compile(r"(?i)(not found|missing (?:record|data|output|field)|no results|empty response|does not exist)")
RESOURCE_RE = re.compile(r"(?i)(out of memory|token limit|context length|resource exhausted|disk full|too many retries)")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _span_dict(span: TraceSpan) -> dict[str, Any]:
    return span.model_dump(mode="json") | {"duration_ms": span.duration_ms}


def _sort_key(span: TraceSpan) -> tuple[datetime, str]:
    stamp = span.start_time
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp, span.span_id


def _iter_values(value: Any, key: str = ""):
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k), v
            yield from _iter_values(v, str(k))
    elif isinstance(value, list):
        for v in value:
            yield key, v
            yield from _iter_values(v, key)


def _dependencies_from_payload(span: TraceSpan, known_ids: set[str]) -> list[str]:
    candidates: list[str] = list(span.explicit_dependencies)
    attrs = span.attributes or {}
    for key in ("input_from", "consumes_span_ids", "upstream_span_ids", "depends_on", "dependency_ids", "source_span_id"):
        value = attrs.get(key)
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, list):
            candidates.extend(str(x) for x in value)
    for key, value in _iter_values(span.inputs):
        lower = key.lower()
        if (
            lower in {"span_id", "source_span_id", "input_from_span", "output_from_span", "upstream_span_id"}
            and isinstance(value, str)
            and value in known_ids
        ):
            candidates.append(value)
        if lower in {"depends_on", "input_from", "source_span_ids"} and isinstance(value, list):
            candidates.extend(str(x) for x in value if str(x) in known_ids)
    return list(dict.fromkeys(x for x in candidates if x))


def reconstruct_graph(trace: ExecutionTrace) -> dict[str, Any]:
    """Return chronological nodes and typed edges; only dependency edges drive impact."""
    spans = sorted(trace.spans, key=_sort_key)
    by_id = {s.span_id: s for s in spans}
    known = set(by_id)
    edges: list[dict[str, Any]] = []
    edge_keys: set[tuple[str, str, str]] = set()
    warnings: list[dict[str, Any]] = []

    def add_edge(source: str, target: str, kind: str, dependency: bool, evidence: str) -> None:
        if source == target:
            warnings.append(
                {"type": "self_reference", "span_id": target, "message": f"Span {target} references itself via {kind}."}
            )
            return
        key = (source, target, kind)
        if key not in edge_keys:
            edge_keys.add(key)
            edges.append({"source": source, "target": target, "kind": kind, "dependency": dependency, "evidence": evidence})

    for span in spans:
        if span.parent_span_id:
            if span.parent_span_id in known:
                add_edge(
                    span.parent_span_id,
                    span.span_id,
                    "parent_child",
                    False,
                    "Explicit parent_span_id; denotes hierarchy, not by itself data causation.",
                )
            else:
                warnings.append(
                    {
                        "type": "orphan_span",
                        "span_id": span.span_id,
                        "reference": span.parent_span_id,
                        "message": f"Parent span {span.parent_span_id} is absent from this run.",
                    }
                )
        for dep in _dependencies_from_payload(span, known):
            if dep in known:
                add_edge(dep, span.span_id, "explicit_dependency", True, "Producer-declared depends_on/input reference.")
            else:
                warnings.append(
                    {
                        "type": "broken_dependency",
                        "span_id": span.span_id,
                        "reference": dep,
                        "message": f"Declared upstream span {dep} is absent from this run.",
                    }
                )
        for link in span.span_links:
            if link in known:
                add_edge(
                    link,
                    span.span_id,
                    "span_link",
                    False,
                    "OpenTelemetry span link; association is not treated as data dependency.",
                )
            else:
                warnings.append(
                    {
                        "type": "broken_span_link",
                        "span_id": span.span_id,
                        "reference": link,
                        "message": f"Linked span {link} is absent from this run.",
                    }
                )

    calls: dict[str, list[TraceSpan]] = defaultdict(list)
    for span in spans:
        call_id = span.tool_call_id or span.attributes.get("tool_call_id") or span.attributes.get("tool.call.id")
        if call_id:
            calls[str(call_id)].append(span)
    for call_id, group in calls.items():
        producers = [s for s in group if s.action_type.value in {"model", "planning", "agent"}]
        consumers = [s for s in group if s.action_type.value in {"tool", "retrieval"}]
        for producer in producers:
            for consumer in consumers:
                if producer.span_id != consumer.span_id:
                    add_edge(producer.span_id, consumer.span_id, "tool_call_reference", True, f"Shared tool_call_id {call_id}.")

    # Preserve each retry occurrence. Repeated call IDs or action IDs are not collapsed.
    for call_id, group in calls.items():
        if len(group) > 2:
            warnings.append(
                {
                    "type": "duplicate_tool_call_id",
                    "reference": call_id,
                    "message": f"{len(group)} spans share tool_call_id {call_id}; inspect the producer's retry/call identity semantics.",
                }
            )

    adjacency: dict[str, list[str]] = defaultdict(list)
    dependency_edges = [e for e in edges if e["dependency"]]
    for edge in dependency_edges:
        adjacency[edge["source"]].append(edge["target"])
    for span in spans:
        if span.end_time and span.end_time < span.start_time:
            warnings.append(
                {"type": "impossible_timeline", "span_id": span.span_id, "message": "Span end_time precedes start_time."}
            )
        if span.action_type.value in {"tool", "retrieval"} and span.status.value == "ok" and span.outputs is None:
            warnings.append(
                {
                    "type": "missing_output",
                    "span_id": span.span_id,
                    "message": "Tool/retrieval reports success but has no recorded output.",
                }
            )
        if span.parent_span_id in by_id:
            parent = by_id[span.parent_span_id]
            if parent.end_time and span.start_time > parent.end_time:
                warnings.append(
                    {
                        "type": "child_outside_parent",
                        "span_id": span.span_id,
                        "reference": parent.span_id,
                        "message": "Child starts after its recorded parent ended; clock skew or incomplete parent timing may explain this.",
                    }
                )

    in_degree = {span_id: 0 for span_id in by_id}
    for targets in adjacency.values():
        for target in targets:
            in_degree[target] += 1
    # Heap-based Kahn order is O((V+E) log V); timestamp is only a tie-breaker.
    priority = {s.span_id: i for i, s in enumerate(spans)}
    ready = [(priority[node], node) for node, degree in in_degree.items() if degree == 0]
    heapify(ready)
    topo: list[str] = []
    while ready:
        _, node = heappop(ready)
        topo.append(node)
        for target in adjacency.get(node, []):
            in_degree[target] -= 1
            if in_degree[target] == 0:
                heappush(ready, (priority[target], target))
    cyclic: list[list[str]] = []
    if len(topo) != len(by_id):
        unresolved = [node for node, degree in in_degree.items() if degree > 0]
        cyclic.append(unresolved)
        warnings.append(
            {
                "type": "dependency_cycle",
                "span_ids": unresolved,
                "message": "The dependency graph has a cycle or depends on one; unresolved component is shown for review.",
            }
        )
        topo = [s.span_id for s in spans]
        warnings.append(
            {
                "type": "topological_fallback",
                "message": "Acyclic order could not be computed; chronological display is used, not asserted as causal.",
            }
        )

    critical = _critical_path(spans, dependency_edges, cyclic)
    hierarchy_children = {edge["target"] for edge in edges if edge["kind"] == "parent_child"}
    return {
        "nodes": [_span_dict(s) for s in spans],
        "edges": edges,
        "chronological_order": [s.span_id for s in spans],
        "logical_order": topo,
        "roots": [s.span_id for s in spans if s.span_id not in hierarchy_children],
        "warnings": warnings,
        "critical_path": critical,
        "critical_path_ms": critical["duration_ms"] if critical else None,
        "critical_path_supported": bool(dependency_edges) and not cyclic,
    }


def _critical_path(spans: list[TraceSpan], edges: list[dict[str, Any]], cycles: list[list[str]]) -> dict[str, Any] | None:
    if not edges or cycles:
        return None
    by_id = {s.span_id: s for s in spans}
    adj: dict[str, list[str]] = defaultdict(list)
    predecessors: dict[str, list[str]] = defaultdict(list)
    indegree = {s.span_id: 0 for s in spans}
    for edge in edges:
        if edge["target"] not in adj[edge["source"]]:
            adj[edge["source"]].append(edge["target"])
            predecessors[edge["target"]].append(edge["source"])
            indegree[edge["target"]] += 1
    order = {span.span_id: index for index, span in enumerate(spans)}
    queue = [(order[node], node) for node, degree in indegree.items() if degree == 0]
    heapify(queue)
    topo: list[str] = []
    while queue:
        _, node = heappop(queue)
        topo.append(node)
        for child in adj.get(node, []):
            indegree[child] -= 1
            if indegree[child] == 0:
                heappush(queue, (order[child], child))
    if len(topo) != len(spans):
        return None
    scores: dict[str, float] = {}
    previous: dict[str, str | None] = {}
    for node in topo:
        duration = by_id[node].duration_ms or 0.0
        parent = max(predecessors[node], key=lambda candidate: scores[candidate]) if predecessors[node] else None
        scores[node] = duration + (scores[parent] if parent else 0.0)
        previous[node] = parent
    end = max(scores, key=lambda key: scores[key]) if scores else None
    if end is None:
        return None
    path: list[str] = []
    cursor: str | None = end
    while cursor is not None:
        path.append(cursor)
        cursor = previous[cursor]
    path.reverse()
    return {
        "span_ids": path,
        "duration_ms": round(scores[end], 3),
        "basis": "Longest path over explicit dependency/tool-call-reference edges only. Recorded span durations are used; unsynchronized clocks can affect estimates.",
    }


def _classify(span: TraceSpan) -> tuple[str, str, float, str] | None:
    attrs = span.attributes or {}
    text = " ".join(
        str(v) for v in (span.exception, span.outputs, attrs.get("error"), attrs.get("error.message")) if v is not None
    )
    # A producer-supplied invalid state flag is explicit evidence even when the span
    # also carries an exception; preserve that more specific classification.
    if attrs.get("state_valid") is False or attrs.get("state_inconsistent") is True:
        return ("state_dependency_inconsistency", "Observed", 0.88, "Producer-supplied state validity flag is false.")
    if span.status.value not in {"error", "timeout"} and not span.exception and not attrs.get("error"):
        # Narrow, explicit quality checks: do not infer correctness from an OK tool response.
        expected_tool = attrs.get("expected_tool")
        if expected_tool and span.tool_name and str(expected_tool) != str(span.tool_name):
            return (
                "incorrect_tool_selection",
                "Inferred Hypothesis",
                0.58,
                f"Recorded expected_tool={expected_tool!r} differs from called tool={span.tool_name!r}; the expected-tool annotation may be wrong.",
            )
        if attrs.get("relevant") is False or attrs.get("retrieval_relevant") is False:
            return ("retrieval_context_error", "Observed", 0.84, "Trace explicitly records the retrieved context as irrelevant.")
        expected_doc = attrs.get("expected_document_id")
        used_doc = attrs.get("document_id") or attrs.get("retrieved_document_id")
        if expected_doc and used_doc and str(expected_doc) != str(used_doc):
            return (
                "retrieval_context_error",
                "Inferred Hypothesis",
                0.62,
                f"Expected document {expected_doc!r} differs from retrieved document {used_doc!r}; relevance/ground-truth metadata should be verified.",
            )
        if span.action_type.value in {"tool", "retrieval"} and span.status.value == "ok" and span.outputs is None:
            return (
                "missing_or_malformed_external_data",
                "Inferred Hypothesis",
                0.55,
                "Tool reports success but the recorded output is absent; output capture may be incomplete.",
            )
        return None
    if AUTH_RE.search(text):
        category = "authentication_or_permission_failure"
    elif RATE_RE.search(text):
        category = "rate_limiting"
    elif TIMEOUT_RE.search(text) or span.status.value == "timeout":
        category = "tool_timeout" if span.action_type.value in {"tool", "retrieval"} else "model_or_action_timeout"
    elif NETWORK_RE.search(text):
        category = "network_failure"
    elif ARG_RE.search(text):
        category = "invalid_tool_arguments" if span.action_type.value in {"tool", "retrieval"} else "schema_or_parsing_failure"
    elif PARSE_RE.search(text):
        category = "schema_or_parsing_failure"
    elif MISSING_RE.search(text):
        category = "missing_or_malformed_external_data"
    elif RESOURCE_RE.search(text):
        category = "retry_loop_or_resource_exhaustion"
    elif span.action_type.value in {"planning", "agent", "delegation"}:
        category = "planning_or_orchestration_error"
    elif span.action_type.value == "model":
        category = "model_api_failure"
    elif span.action_type.value == "evaluation":
        category = "evaluation_or_final_answer_failure"
    elif span.action_type.value == "tool" or span.tool_name:
        category = "tool_execution_exception"
    else:
        category = "unknown_or_insufficient_evidence"
    if category == "model_or_action_timeout":
        category = "tool_timeout" if span.action_type.value in {"tool", "retrieval"} else "model_api_failure"
    return (category, "Observed", 0.94 if span.exception else 0.82, text[:400] or f"Recorded span status is {span.status.value}.")


def _run_level_findings(trace: ExecutionTrace) -> list[dict[str, Any]]:
    """Findings that only become visible when the whole run is considered.

    These findings never carry a span anchor unless the run itself recorded
    the evidence, so they cannot masquerade as a localized error.
    """
    findings: list[dict[str, Any]] = []
    run_failed = trace.status.value == "failure" or trace.evaluation_results.get("passed") is False
    evaluation_failed = trace.evaluation_results.get("passed") is False

    # Retry loop / resource exhaustion: the same logical action retried repeatedly.
    attempts: dict[tuple[str, str], list[TraceSpan]] = defaultdict(list)
    for span in trace.spans:
        key = (span.action_id or "", span.tool_name or span.name)
        attempts[key].append(span)
    for (action_id, label), group in attempts.items():
        if len(group) < MAX_RETRY_ATTEMPTS:
            continue
        failures = [s for s in group if s.status.value in {"error", "timeout"}]
        if len(failures) < 2:
            continue
        span_ids = [s.span_id for s in group]
        findings.append(
            {
                "finding_id": f"retry-loop:{action_id or label}",
                "rule_id": "run.retry_loop.v1",
                "category": "retry_loop_or_resource_exhaustion",
                "status": "Observed",
                "confidence": 0.9,
                "calibration_note": "The repetition of an action is observed; the reason for the repetition is inferred.",
                "span_ids": span_ids,
                "trace_ids": [trace.trace_id],
                "evidence": [
                    {
                        "span_id": s.span_id,
                        "field": "status/retry_attempt",
                        "value": f"status={s.status.value} attempt={s.retry_attempt}",
                    }
                    for s in group
                ],
                "supporting_evidence": [
                    f"Action '{label}' was attempted {len(group)} time(s) with {len(failures)} recorded failure(s)."
                ],
                "contradicting_evidence": [],
                "limitations": [
                    "A repeated action can be legitimate (e.g. polling) rather than a failure loop.",
                    "The trace does not record why the retry policy fired.",
                ],
                "suggested_investigation": "Inspect the retry/backoff policy and whether the retried input was ever changed.",
            }
        )

    if not run_failed:
        return findings

    tool_spans = [s for s in trace.spans if s.action_type.value in {"tool", "retrieval"}]
    tool_failures = [s for s in tool_spans if s.status.value in {"error", "timeout"}]
    span_level_errors = [s for s in trace.spans if s.status.value in {"error", "timeout"}]
    answer = trace.final_answer
    empty_answer = answer is None or (isinstance(answer, str) and not answer.strip())

    if not tool_failures and not span_level_errors:
        if empty_answer:
            findings.append(
                {
                    "finding_id": f"final-answer-missing:{trace.run_id}",
                    "rule_id": "run.missing_final_answer.v1",
                    "category": "evaluation_or_final_answer_failure",
                    "status": "Observed",
                    "confidence": 0.85,
                    "calibration_note": "The absence of a final answer is observed; the reason is not.",
                    "span_ids": [],
                    "trace_ids": [trace.trace_id],
                    "evidence": [
                        {
                            "span_id": "",
                            "field": "final_answer",
                            "value": f"Run finished with status '{trace.status.value}' and no recorded final answer.",
                        }
                    ],
                    "supporting_evidence": ["The run is marked failed and no final answer was recorded."],
                    "contradicting_evidence": [],
                    "limitations": [
                        "The final answer may have been produced but not captured by the instrumentation.",
                    ],
                    "suggested_investigation": "Check whether the terminating step records its output; then re-run with output capture enabled.",
                }
            )
        elif evaluation_failed:
            findings.append(
                {
                    "finding_id": f"final-answer-rejected:{trace.run_id}",
                    "rule_id": "run.final_answer_evaluation_failed.v1",
                    "category": "evaluation_or_final_answer_failure",
                    "status": "Observed",
                    "confidence": 0.88,
                    "calibration_note": "The evaluation verdict is observed; the cause of the rejection is not.",
                    "span_ids": [],
                    "trace_ids": [trace.trace_id],
                    "evidence": [
                        {
                            "span_id": "",
                            "field": "evaluation_results",
                            "value": json.dumps(trace.evaluation_results, default=str)[:400],
                        }
                    ],
                    "supporting_evidence": [
                        "All tool calls completed without an error, but the recorded evaluation did not pass."
                    ],
                    "contradicting_evidence": [],
                    "limitations": [
                        "A successful tool response does not establish that the produced answer is correct.",
                        "The evaluation criteria are not part of the trace payload.",
                    ],
                    "suggested_investigation": "Replay the final answer against the evaluator rubric and inspect the retrieved context that reached the model.",
                }
            )
        else:
            # The run failed, nothing in it failed, and no evaluation verdict was
            # recorded. The correct behaviour is to abstain, not to invent a cause.
            findings.append(
                {
                    "finding_id": f"unlocalized-failure:{trace.run_id}",
                    "rule_id": "run.unlocalized_failure.v1",
                    "category": "unknown_or_insufficient_evidence",
                    "status": "Observed",
                    "confidence": 0.6,
                    "calibration_note": "The failure is observed; no span-level evidence localizes it.",
                    "span_ids": [],
                    "trace_ids": [trace.trace_id],
                    "evidence": [
                        {
                            "span_id": "",
                            "field": "status",
                            "value": f"Run status is {trace.status.value} with no recorded tool, span or evaluation error.",
                        }
                    ],
                    "supporting_evidence": ["No span in this run carries an error, exception or timeout status."],
                    "contradicting_evidence": [],
                    "limitations": [
                        "The failing boundary may not be instrumented in this trace.",
                        "No causal conclusion is supported by the available evidence.",
                    ],
                    "suggested_investigation": "Add structured error, evaluator and model-response instrumentation, then reproduce the run.",
                }
            )
    return findings


def classify_failures(trace: ExecutionTrace, graph: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    graph = graph or reconstruct_graph(trace)
    findings: list[dict[str, Any]] = []
    for span in sorted(trace.spans, key=_sort_key):
        match = _classify(span)
        if not match:
            continue
        category, status, confidence, evidence = match
        explicit = status == "Observed"
        findings.append(
            {
                "finding_id": f"{span.span_id}:{category}",
                "rule_id": f"{category}.v1",
                "category": category,
                "status": status,
                "confidence": confidence,
                "calibration_note": "Rule confidence indicates evidence strength, not a calibrated probability of causation."
                if explicit
                else "Heuristic confidence is not a calibrated probability.",
                "span_ids": [span.span_id],
                "trace_ids": [trace.trace_id],
                "evidence": [{"span_id": span.span_id, "field": "exception/status/attributes", "value": evidence}],
                "supporting_evidence": [evidence],
                "contradicting_evidence": [],
                "limitations": [
                    "A single span can be incomplete or mis-instrumented.",
                    "Chronological order alone is not treated as causation.",
                ],
                "suggested_investigation": _suggest(category),
            }
        )

    by_id = {s.span_id: s for s in trace.spans}
    dep_adj: dict[str, list[str]] = defaultdict(list)
    for edge in graph["edges"]:
        if edge["dependency"]:
            dep_adj[edge["source"]].append(edge["target"])
    findings.extend(_run_level_findings(trace))
    observed = [f for f in findings if f["status"] == "Observed"]
    for finding in observed:
        if not finding.get("span_ids"):
            continue
        source = finding["span_ids"][0]
        descendants = _descendants(source, dep_adj)
        impacted = [
            sid
            for sid in descendants
            if sid in by_id
            and (
                by_id[sid].status.value in {"error", "timeout"}
                or by_id[sid].attributes.get("missing_input")
                or by_id[sid].attributes.get("consumed_invalid_output")
            )
        ]
        if impacted:
            finding["downstream_effects"] = impacted
            findings.append(
                {
                    "finding_id": f"propagation:{source}",
                    "rule_id": "dependency.failure_propagation.v1",
                    "category": "failure_propagation",
                    "status": "Inferred Hypothesis",
                    "confidence": 0.7,
                    "calibration_note": "Dependency-connected impact is evidence of propagation, not causal proof.",
                    "span_ids": [source, *impacted],
                    "trace_ids": [trace.trace_id],
                    "evidence": [
                        {"span_id": sid, "field": "dependency/status", "value": by_id[sid].status.value}
                        for sid in [source, *impacted]
                    ],
                    "supporting_evidence": [f"Declared/recorded dependency path connects {source} to {', '.join(impacted)}."],
                    "contradicting_evidence": [],
                    "limitations": [
                        "The edge proves recorded linkage, not semantic necessity.",
                        "An unrecorded recovery path may exist.",
                    ],
                    "suggested_investigation": "Inspect the consumer inputs and rerun only the isolated downstream step with a recorded valid fixture.",
                }
            )
    return findings


def _descendants(root: str, adjacency: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    seen = {root}
    queue = deque(adjacency.get(root, []))
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        out.append(node)
        queue.extend(adjacency.get(node, []))
    return out


def _suggest(category: str) -> str:
    suggestions = {
        "invalid_tool_arguments": "Compare the serialized arguments with the tool's validated schema; test a corrected fixture in an isolated downstream-only replay.",
        "tool_timeout": "Inspect tool latency, deadline propagation, and retry/backoff settings; determine whether a later attempt recovered.",
        "authentication_or_permission_failure": "Verify the credential identity and least-privilege policy in the same environment; do not include secrets in the trace.",
        "rate_limiting": "Check provider quota and request concurrency; verify retry-after handling and whether retries amplified load.",
        "network_failure": "Correlate with service/network health and retry metadata; timing alone cannot locate the responsible component.",
        "schema_or_parsing_failure": "Validate the exact raw response against the expected schema and record the parser/model version.",
        "retrieval_context_error": "Inspect retrieved document IDs, rank scores, filter settings, and corpus version against a labeled relevant-context fixture.",
        "incorrect_tool_selection": "Review the expected-tool annotation and policy state; compare against a successful run before treating this as a defect.",
        "state_dependency_inconsistency": "Compare producer output to consumer input and state version/checksum; isolate the state transition before replay.",
    }
    return suggestions.get(
        category,
        "Inspect the referenced span and surrounding dependency edges; gather missing inputs before asserting a root cause.",
    )


def analyze_trace(trace: ExecutionTrace, reference: ExecutionTrace | None = None) -> dict[str, Any]:
    graph = reconstruct_graph(trace)
    findings = classify_failures(trace, graph)
    dep_adj: dict[str, list[str]] = defaultdict(list)
    for edge in graph["edges"]:
        if edge["dependency"]:
            dep_adj[edge["source"]].append(edge["target"])
    observed = [f for f in findings if f["status"] == "Observed" and f["category"] != "failure_propagation"]
    span_by_id = {s.span_id: s for s in trace.spans}
    anchored = [f for f in observed if any(sid in span_by_id for sid in f.get("span_ids", []))]
    earliest = None
    if anchored:

        def _finding_start(finding: dict[str, Any]) -> tuple[datetime, str]:
            for sid in finding.get("span_ids", []):
                span = span_by_id.get(sid)
                if span is not None:
                    return _sort_key(span)
            return (datetime.max.replace(tzinfo=timezone.utc), finding.get("finding_id", ""))

        earliest_finding = min(anchored, key=_finding_start)
        anchor = next((sid for sid in earliest_finding.get("span_ids", []) if sid in span_by_id), None)
        earliest = {
            "span_id": anchor,
            "category": earliest_finding["category"],
            "status": "Observed",
            "finding_id": earliest_finding["finding_id"],
            "timestamp": _iso(span_by_id[anchor].start_time) if anchor else None,
            "rule_id": earliest_finding.get("rule_id"),
            "note": (
                "The earliest explicitly recorded error. It marks the first observed anomaly, "
                "not necessarily the root cause of the run outcome."
            ),
        }

    failed = trace.status.value == "failure" or bool(trace.evaluation_results.get("passed") is False)
    candidates: list[dict[str, Any]] = []
    if failed:
        for finding in findings:
            if finding["status"] not in {"Observed", "Inferred Hypothesis"} or finding["category"] == "failure_propagation":
                continue
            root_id = finding["span_ids"][0] if finding.get("span_ids") else None
            span = span_by_id.get(root_id) if root_id else None
            if root_id and span is None:
                continue
            if span is None:
                # Run-level observation without an anchor span: keep it as a lower
                # ranked candidate that explicitly lacks a localized action.
                candidates.append(
                    {
                        "hypothesis_id": f"hypothesis:{finding['finding_id']}",
                        "title": finding["category"].replace("_", " ").capitalize(),
                        "category": finding["category"],
                        "status": "Inferred Hypothesis",
                        # Without a named action the hypothesis strength is capped low:
                        # an unnamed cause is not evidence of a localized defect.
                        "confidence": round(min(0.2, max(0.1, float(finding.get("confidence", 0.4)) * 0.25)), 2),
                        "primary_span_id": None,
                        "span_ids": [],
                        "supporting_evidence": [
                            f"{finding['status']} finding {finding['finding_id']}: {finding['supporting_evidence'][0]}"
                        ],
                        "contradicting_evidence": finding.get("contradicting_evidence", []),
                        "missing_information": [
                            "Span-level instrumentation of the failing boundary",
                            "Evaluator output or rubric",
                        ],
                        "limitations": [
                            "No single action can be named as the cause from the available evidence.",
                            "Alternative causes may be missing from the captured trace.",
                        ],
                        "suggested_experiment": finding["suggested_investigation"],
                        "evidence_finding_ids": [finding["finding_id"]],
                    }
                )
                continue
            descendants = _descendants(root_id, dep_adj) if root_id else []
            connected_bad = [
                sid
                for sid in descendants
                if sid
                in {
                    s.span_id
                    for s in trace.spans
                    if s.status.value in {"error", "timeout"}
                    or s.attributes.get("missing_input")
                    or s.attributes.get("consumed_invalid_output")
                }
            ]
            # This is a ranking score, not a probability: an upstream anomaly with
            # dependency-connected downstream symptoms outranks a terminal symptom.
            # The individual evidence label and limitations remain visible.
            severity = (0.65 if finding["status"] == "Observed" else 0.52) + min(0.35, len(connected_bad) * 0.22)
            # A successful retry of the same action is counter-evidence to treating an
            # intermediate failure as the root cause of a failed run.
            same_action = [
                s
                for s in trace.spans
                if s.span_id != root_id
                and (
                    (span.action_id and s.action_id == span.action_id)
                    or (span.tool_name and s.tool_name == span.tool_name and s.inputs == span.inputs)
                )
            ]
            recovered = any(s.status.value == "ok" for s in same_action)
            contradictions = [
                f"A later equivalent attempt {s.span_id} completed successfully." for s in same_action if s.status.value == "ok"
            ]
            if recovered:
                severity -= 0.35
            candidates.append(
                {
                    "hypothesis_id": f"hypothesis:{root_id}:{finding['category']}",
                    "title": f"{finding['category'].replace('_', ' ').capitalize()} at {span.name}",
                    "category": finding["category"],
                    "status": "Inferred Hypothesis",
                    "confidence": round(max(0.15, min(0.9, severity)), 2),
                    "primary_span_id": root_id,
                    "span_ids": [root_id, *connected_bad],
                    "supporting_evidence": [
                        f"{finding['status']} finding {finding['finding_id']}: {finding['supporting_evidence'][0]}"
                    ]
                    + ([f"Dependency-linked downstream failures: {', '.join(connected_bad)}."] if connected_bad else []),
                    "contradicting_evidence": contradictions,
                    "missing_information": ["Task-level expected outcome or evaluator details."]
                    if not trace.evaluation_results
                    else [],
                    "limitations": [
                        "Association/dependency evidence does not prove that changing this action changes the final outcome.",
                        "Alternative causes may be missing from the captured trace.",
                    ],
                    "suggested_experiment": finding["suggested_investigation"],
                    "evidence_finding_ids": [finding["finding_id"]],
                }
            )
        candidates.sort(
            key=lambda c: (
                -c["confidence"],
                next(
                    (_sort_key(s) for s in trace.spans if s.span_id == c["primary_span_id"]),
                    (datetime.max.replace(tzinfo=timezone.utc), ""),
                ),
            )
        )
        if not candidates:
            candidates.append(
                {
                    "hypothesis_id": f"hypothesis:unknown:{trace.run_id}",
                    "title": "Failure is observed, but the available trace does not localize a cause",
                    "category": "unknown_or_insufficient_evidence",
                    "status": "Inferred Hypothesis",
                    "confidence": 0.15,
                    "primary_span_id": None,
                    "span_ids": [],
                    "supporting_evidence": ["Run-level status is failure but no span-level failure rule matched."],
                    "contradicting_evidence": [],
                    "missing_information": [
                        "Final evaluator details",
                        "External tool response bodies",
                        "Expected vs actual state at each transition",
                    ],
                    "limitations": ["The trace may have omitted the failing boundary.", "No causal conclusion is supported."],
                    "suggested_experiment": "Add structured error and evaluation instrumentation, then reproduce in an isolated environment.",
                    "evidence_finding_ids": [],
                }
            )

    comparison = None
    if reference:
        from app.comparison import compare_traces

        comparison = compare_traces([reference, trace])
    impact_roots: dict[str, list[str]] = {}
    for finding in observed:
        for root in finding.get("span_ids", []):
            if root not in span_by_id:
                continue
            descendants = _descendants(root, dep_adj)
            if descendants:
                impact_roots[root] = descendants

    propagation = next((f for f in findings if f["category"] == "failure_propagation"), None)
    propagation_point = None
    if propagation:
        propagation_point = {
            "span_id": propagation["span_ids"][0] if propagation.get("span_ids") else None,
            "affected_span_ids": propagation.get("span_ids", [])[1:],
            "status": propagation["status"],
            "rule_id": propagation["rule_id"],
            "confidence": propagation["confidence"],
            "note": (
                "Earliest point where a recorded failure is connected to downstream symptoms by a declared "
                "dependency. The link proves recorded linkage, not semantic necessity."
            ),
        }

    summary = _summary(trace, findings, candidates, earliest)
    return {
        "run_id": trace.run_id,
        "trace_id": trace.trace_id,
        "rules_version": RULE_VERSION,
        "summary": summary,
        "earliest_observed_error": earliest,
        "earliest_observed_divergence": comparison.get("first_divergence") if comparison else None,
        "earliest_plausible_failure_propagation": propagation,
        "earliest_plausible_failure_propagation_point": propagation_point,
        "findings": findings,
        "root_cause_hypotheses": candidates,
        "graph": graph,
        "impact": impact_roots,
        "reference_comparison": comparison,
        "run_outcome": trace.status.value,
        "categories": list(FAILURE_CATEGORIES),
        "evidence_statuses": list(EVIDENCE_STATUSES),
        "missing_information": _missing_information(trace, findings),
        "suggested_experiments": _suggest_experiments(trace, candidates),
        "evidence_policy": (
            "Observed means present in the imported record. Inferred Hypothesis means a deterministic heuristic "
            "interpretation. Experimentally Supported/Refuted means a controlled local test was executed. "
            "No causal proof is claimed without a controlled test under stated conditions."
        ),
    }


def _missing_information(trace: ExecutionTrace, findings: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    if not trace.evaluation_results:
        missing.append("Evaluator output or rubric: the trace does not record how the final answer was scored.")
    if not any(span.status.value in {"error", "timeout"} for span in trace.spans):
        missing.append("Span-level error records: no span carries an error or timeout status.")
    if not trace.environment:
        missing.append("Environment metadata (version, region, credential scope) needed to compare runs.")
    if not any(span.token_usage for span in trace.spans):
        missing.append("Token usage, needed for cost and regression analysis.")
    if not any(span.outputs is not None for span in trace.spans):
        missing.append("Tool outputs, needed to check whether a successful response was actually correct.")
    if not missing:
        missing.append("Task-level success criteria beyond the recorded run status.")
    return missing


def _suggest_experiments(trace: ExecutionTrace, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    experiments: list[dict[str, Any]] = []
    for candidate in candidates[:4]:
        experiments.append(
            {
                "hypothesis_id": candidate.get("hypothesis_id"),
                "title": candidate.get("title"),
                "proposal": candidate.get("suggested_experiment"),
                "span_ids": candidate.get("span_ids", []),
                "safety": (
                    "Run against a recorded fixture in the isolated local sandbox. No imported tool, network call "
                    "or external side effect is executed."
                ),
            }
        )
    return experiments


def _summary(
    trace: ExecutionTrace, findings: list[dict[str, Any]], hypotheses: list[dict[str, Any]], earliest: dict[str, Any] | None
) -> str:
    status = trace.status.value
    if status == "success":
        if findings:
            return f"Run completed successfully with {len(findings)} diagnostic finding(s); inspect whether observed errors recovered before attributing failure."
        return "Run completed successfully. No deterministic failure rule matched; a successful status does not establish answer correctness."
    if status == "failure" or trace.evaluation_results.get("passed") is False:
        if earliest:
            location = f" at span {earliest['span_id']}" if earliest.get("span_id") else " (run-level observation)"
            return (
                f"Run failed. Earliest recorded error is {earliest['category']}{location}; "
                "ranked candidates remain hypotheses until tested."
            )
        return "Run failed, but the trace contains no explicit span error. Root-cause localization is inconclusive with current evidence."
    return f"Run status is {status}. {len(findings)} rule-based finding(s) are available; outcome confidence is limited by incomplete run metadata."
