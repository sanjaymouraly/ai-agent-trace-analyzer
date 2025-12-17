"""Retry-aware deterministic execution alignment. No semantic matching is implied."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

from app.analysis import reconstruct_graph
from app.schema import ExecutionTrace, TraceSpan

MAX_SPANS_PER_RUN = 5000

_SUFFIX = re.compile(r"(?:\s*#?\s*(?:attempt|retry)\s*\d+|\s*\(retry\s*\d+\))$", re.I)


def _action_key(span: TraceSpan) -> tuple[str, str]:
    kind = span.action_type.value
    label = span.tool_name or _SUFFIX.sub("", span.name.strip().lower())
    return kind, re.sub(r"\s+", " ", label)


def _json_equal(a: Any, b: Any) -> bool:
    try:
        return json.dumps(a, sort_keys=True, ensure_ascii=False, default=str) == json.dumps(
            b, sort_keys=True, ensure_ascii=False, default=str
        )
    except (TypeError, ValueError):
        return a == b


def _span_metrics(span: TraceSpan) -> dict[str, Any]:
    return {
        "span_id": span.span_id,
        "name": span.name,
        "action_type": span.action_type.value,
        "tool_name": span.tool_name,
        "status": span.status.value,
        "inputs": span.inputs,
        "outputs": span.outputs,
        "model": span.model,
        "provider": span.provider,
        "retry_attempt": span.retry_attempt,
        "duration_ms": round(span.duration_ms, 3) if span.duration_ms is not None else None,
        "token_usage": span.token_usage,
        "cost_usd": span.cost_usd,
    }


def _diff_span(a: TraceSpan, b: TraceSpan) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    for field in (
        "name",
        "action_type",
        "tool_name",
        "status",
        "inputs",
        "outputs",
        "model",
        "provider",
        "retry_attempt",
        "token_usage",
        "cost_usd",
    ):
        left = getattr(a, field)
        right = getattr(b, field)
        if hasattr(left, "value"):
            left = left.value
        if hasattr(right, "value"):
            right = right.value
        if not _json_equal(left, right):
            diffs.append({"field": field, "left": left, "right": right})
    if a.duration_ms is not None and b.duration_ms is not None and abs(a.duration_ms - b.duration_ms) > 0.001:
        diffs.append(
            {
                "field": "duration_ms",
                "left": round(a.duration_ms, 3),
                "right": round(b.duration_ms, 3),
                "delta_ms": round(b.duration_ms - a.duration_ms, 3),
            }
        )
    return diffs


def _aligned_pair(left: TraceSpan, right: TraceSpan, left_run: str, right_run: str) -> dict[str, Any]:
    diffs = _diff_span(left, right)
    return {
        "kind": "changed" if diffs else "matched",
        "left_run_id": left_run,
        "right_run_id": right_run,
        "left_span_id": left.span_id,
        "right_span_id": right.span_id,
        "left": _span_metrics(left),
        "right": _span_metrics(right),
        "differences": diffs,
    }


def _align_two(left: ExecutionTrace, right: ExecutionTrace) -> dict[str, Any]:
    left_spans = sorted(left.spans, key=lambda s: (s.start_time, s.span_id))
    right_spans = sorted(right.spans, key=lambda s: (s.start_time, s.span_id))
    matcher = SequenceMatcher(a=[_action_key(s) for s in left_spans], b=[_action_key(s) for s in right_spans], autojunk=False)
    aligned: list[dict[str, Any]] = []
    first_divergence: dict[str, Any] | None = None
    first_timing_only: dict[str, Any] | None = None
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for a, b in zip(left_spans[i1:i2], right_spans[j1:j2], strict=True):
                item = _aligned_pair(a, b, left.run_id, right.run_id)
                aligned.append(item)
                if item["kind"] == "changed":
                    if any(diff["field"] not in {"duration_ms", "token_usage", "cost_usd"} for diff in item["differences"]):
                        if first_divergence is None:
                            first_divergence = _divergence(item, "changed_fields", a, b, left, right)
                    elif first_timing_only is None:
                        first_timing_only = _divergence(item, "resource_only", a, b, left, right)
        elif tag == "replace":
            lchunk = left_spans[i1:i2]
            rchunk = right_spans[j1:j2]
            paired = min(len(lchunk), len(rchunk))
            for k in range(paired):
                item = _aligned_pair(lchunk[k], rchunk[k], left.run_id, right.run_id)
                item["kind"] = "changed" if _action_key(lchunk[k]) != _action_key(rchunk[k]) or item["differences"] else "matched"
                if _action_key(lchunk[k]) != _action_key(rchunk[k]):
                    item["differences"].insert(
                        0, {"field": "action_identity", "left": _action_key(lchunk[k]), "right": _action_key(rchunk[k])}
                    )
                aligned.append(item)
                if first_divergence is None:
                    first_divergence = _divergence(item, "changed_action", lchunk[k], rchunk[k], left, right)
            for span in lchunk[paired:]:
                item = {
                    "kind": "missing_in_right",
                    "left_run_id": left.run_id,
                    "right_run_id": right.run_id,
                    "left_span_id": span.span_id,
                    "right_span_id": None,
                    "left": _span_metrics(span),
                    "right": None,
                    "differences": [],
                }
                aligned.append(item)
                if first_divergence is None:
                    first_divergence = _divergence(item, "missing_action", span, None, left, right)
            for span in rchunk[paired:]:
                item = {
                    "kind": "added_in_right",
                    "left_run_id": left.run_id,
                    "right_run_id": right.run_id,
                    "left_span_id": None,
                    "right_span_id": span.span_id,
                    "left": None,
                    "right": _span_metrics(span),
                    "differences": [],
                }
                aligned.append(item)
                if first_divergence is None:
                    first_divergence = _divergence(item, "added_action", None, span, left, right)
        elif tag == "delete":
            for span in left_spans[i1:i2]:
                item = {
                    "kind": "missing_in_right",
                    "left_run_id": left.run_id,
                    "right_run_id": right.run_id,
                    "left_span_id": span.span_id,
                    "right_span_id": None,
                    "left": _span_metrics(span),
                    "right": None,
                    "differences": [],
                }
                aligned.append(item)
                if first_divergence is None:
                    first_divergence = _divergence(item, "missing_action", span, None, left, right)
        elif tag == "insert":
            for span in right_spans[j1:j2]:
                item = {
                    "kind": "added_in_right",
                    "left_run_id": left.run_id,
                    "right_run_id": right.run_id,
                    "left_span_id": None,
                    "right_span_id": span.span_id,
                    "left": None,
                    "right": _span_metrics(span),
                    "differences": [],
                }
                aligned.append(item)
                if first_divergence is None:
                    first_divergence = _divergence(item, "added_action", None, span, left, right)

    # If action identity and values align, dependency topology can still differ.
    left_graph = reconstruct_graph(left)
    right_graph = reconstruct_graph(right)
    left_parent = {s.span_id: s.parent_span_id for s in left.spans}
    right_parent = {s.span_id: s.parent_span_id for s in right.spans}
    topology_changed = False
    matched = [item for item in aligned if item.get("left_span_id") and item.get("right_span_id")]
    left_to_right = {item["left_span_id"]: item["right_span_id"] for item in matched}
    for item in matched:
        lp = left_parent.get(item["left_span_id"])
        rp = right_parent.get(item["right_span_id"])
        if (left_to_right.get(lp) if lp else None) != rp:
            topology_changed = True
            if first_divergence is None:
                first_divergence = {
                    "kind": "hierarchy_change",
                    "left_span_id": item["left_span_id"],
                    "right_span_id": item["right_span_id"],
                    "left_parent_span_id": lp,
                    "right_parent_span_id": rp,
                    "classification": "parent_hierarchy_differs",
                    "status": "Observed",
                    "causal_claim": False,
                    "note": "Span nesting differs. Hierarchy difference alone does not establish a behavioral defect.",
                }
                break
    if first_divergence is None:
        first_divergence = first_timing_only
    metrics = {
        "left": _run_metrics(left),
        "right": _run_metrics(right),
        "duration_delta_ms": _delta(left.duration_ms, right.duration_ms),
        "token_delta": _delta(_tokens(left), _tokens(right)),
        "cost_delta_usd": _delta(_cost(left), _cost(right)),
    }
    # Keep reconstruction metadata in the response for inspection, not as a claim
    # that timestamp order determines correct execution.
    return {
        "left_run_id": left.run_id,
        "right_run_id": right.run_id,
        "aligned_actions": aligned,
        "first_divergence": first_divergence,
        "divergence_note": "First structural/value difference in deterministic chronological alignment. Different valid paths or orderings are not labeled incorrect without dependency/outcome evidence.",
        "topology_changed": topology_changed,
        "metrics": metrics,
        "left_order": left_graph["chronological_order"],
        "right_order": right_graph["chronological_order"],
        "alignment_method": "Exact normalized action identity (type + tool/name), retry occurrences retained, deterministic sequence alignment; no LLM semantic matching.",
    }


def _divergence(
    item: dict[str, Any],
    kind: str,
    left: TraceSpan | None,
    right: TraceSpan | None,
    left_run: ExecutionTrace,
    right_run: ExecutionTrace,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "left_span_id": left.span_id if left else None,
        "right_span_id": right.span_id if right else None,
        "left_action": _span_metrics(left) if left else None,
        "right_action": _span_metrics(right) if right else None,
        "differences": item.get("differences", []),
        "status": "Observed",
        "causal_claim": False,
        "classification": "structural_or_value_divergence",
        "note": "A divergence is observed; it is not necessarily erroneous. Validate task equivalence, declared dependencies and outcome evidence.",
        "trace_ids": [left_run.trace_id, right_run.trace_id],
    }


def _tokens(trace: ExecutionTrace) -> int:
    total = 0
    for span in trace.spans:
        usage = span.token_usage
        total += int(
            usage.get("total_tokens") or sum(v for k, v in usage.items() if isinstance(v, (int, float)) and k != "total_tokens")
        )
    return total


def _cost(trace: ExecutionTrace) -> float:
    return round(sum(s.cost_usd or 0 for s in trace.spans), 6)


def _run_metrics(trace: ExecutionTrace) -> dict[str, Any]:
    return {
        "run_id": trace.run_id,
        "trace_id": trace.trace_id,
        "task": trace.task,
        "status": trace.status.value,
        "duration_ms": round(trace.duration_ms, 3) if trace.duration_ms is not None else None,
        "tokens": _tokens(trace),
        "cost_usd": _cost(trace),
        "model_provider": list(
            dict.fromkeys(f"{s.provider or '?'} / {s.model or '?'}" for s in trace.spans if s.model or s.provider)
        ),
    }


def _delta(a: float | int | None, b: float | int | None) -> float | int | None:
    if a is None or b is None:
        return None
    return round(b - a, 3) if isinstance(a, float) or isinstance(b, float) else int(b - a)


def compare_traces(traces: list[ExecutionTrace]) -> dict[str, Any]:
    if len(traces) < 2:
        raise ValueError("At least two executions are required.")
    if any(len(trace.spans) > MAX_SPANS_PER_RUN for trace in traces):
        raise ValueError(f"Comparison is capped at {MAX_SPANS_PER_RUN} spans per run to bound deterministic alignment cost.")
    baseline = traces[0]
    comparisons = [_align_two(baseline, other) for other in traces[1:]]
    first = next((c["first_divergence"] for c in comparisons if c["first_divergence"]), None)
    return {
        "baseline_run_id": baseline.run_id,
        "run_ids": [t.run_id for t in traces],
        "task_equivalence": "not_assumed",
        "pairwise": comparisons,
        "first_divergence": first,
        "comparison_note": "The first run is an alignment baseline, not a gold trajectory. Compare outcomes and dependency evidence; valid agents may take different paths.",
    }
