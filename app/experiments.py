"""Strictly local, side-effect-free counterfactual checks for declared mock adapters.

Uploaded trace contents are never imported as code. Unknown adapters are abstained from.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from typing import Any

from app.analysis import reconstruct_graph
from app.ingestion import redact
from app.schema import ExecutionTrace

MAX_STEPS = 100
MAX_FIXTURE_BYTES = 64 * 1024
SAFE_ADAPTERS = {"mock_required_fields", "mock_type_check", "mock_copy"}


def execute_counterfactual(trace: ExecutionTrace, source_span_id: str, replacement_output: Any, title: str) -> dict[str, Any]:
    raw_fixture = json.dumps(replacement_output, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(raw_fixture.encode("utf-8")) > MAX_FIXTURE_BYTES:
        raise ValueError(f"Counterfactual fixture exceeds {MAX_FIXTURE_BYTES} bytes.")
    by_id = {s.span_id: s for s in trace.spans}
    if source_span_id not in by_id:
        raise ValueError(f"Span '{source_span_id}' is not present in run '{trace.run_id}'.")
    graph = reconstruct_graph(trace)
    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree: dict[str, int] = {sid: 0 for sid in by_id}
    for edge in graph["edges"]:
        if edge["dependency"]:
            adjacency[edge["source"]].append(edge["target"])
            indegree[edge["target"]] += 1
    descendants: list[str] = []
    seen = {source_span_id}
    queue = deque(adjacency[source_span_id])
    while queue:
        item = queue.popleft()
        if item in seen:
            continue
        seen.add(item)
        descendants.append(item)
        queue.extend(adjacency[item])
    order = {sid: i for i, sid in enumerate(graph["logical_order"])}
    descendants.sort(key=lambda sid: order.get(sid, len(order)))
    if len(descendants) > MAX_STEPS:
        raise ValueError(f"Experiment exceeds the {MAX_STEPS}-step sandbox cap.")

    outputs: dict[str, Any] = {source_span_id: replacement_output}
    steps: list[dict[str, Any]] = []
    for target_id in descendants:
        span = by_id[target_id]
        adapter = span.attributes.get("replay_adapter")
        spec = span.attributes.get("replay_spec") or {}
        parents = [e["source"] for e in graph["edges"] if e["target"] == target_id and e["dependency"]]
        upstream_id = next((parent for parent in parents if parent in outputs), None)
        input_value = outputs.get(upstream_id) if upstream_id else span.inputs
        if adapter not in SAFE_ADAPTERS:
            steps.append(
                {
                    "span_id": target_id,
                    "action": span.name,
                    "adapter": adapter,
                    "outcome": "not_executed",
                    "reason": "No allow-listed deterministic replay_adapter is declared; imported tools/actions are never called.",
                }
            )
            continue
        if adapter == "mock_required_fields":
            required = spec.get("required_fields", [])
            if not isinstance(required, list) or any(not isinstance(f, str) for f in required):
                steps.append(
                    {
                        "span_id": target_id,
                        "action": span.name,
                        "adapter": adapter,
                        "outcome": "invalid_replay_spec",
                        "reason": "required_fields must be a list of strings.",
                    }
                )
                continue
            present = [
                field
                for field in required
                if isinstance(input_value, dict) and field in input_value and input_value[field] is not None
            ]
            missing = [field for field in required if field not in present]
            passed = not missing
            simulated: dict[str, Any] = {
                "validation": "passed" if passed else "failed",
                "present_fields": present,
                "missing_fields": missing,
            }
            outputs[target_id] = simulated
            steps.append(
                {
                    "span_id": target_id,
                    "action": span.name,
                    "adapter": adapter,
                    "outcome": "passed" if passed else "failed",
                    "input_from_span": upstream_id,
                    "details": simulated,
                    "side_effects": False,
                }
            )
        elif adapter == "mock_type_check":
            expected = spec.get("expected_type")
            checks = {
                "object": lambda x: isinstance(x, dict),
                "array": lambda x: isinstance(x, list),
                "string": lambda x: isinstance(x, str),
                "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
                "boolean": lambda x: isinstance(x, bool),
                "non_null": lambda x: x is not None,
            }
            passed = expected in checks and checks[expected](input_value)
            simulated = {"expected_type": expected, "actual_type": type(input_value).__name__, "passed": passed}
            outputs[target_id] = simulated
            steps.append(
                {
                    "span_id": target_id,
                    "action": span.name,
                    "adapter": adapter,
                    "outcome": "passed" if passed else "failed",
                    "input_from_span": upstream_id,
                    "details": simulated,
                    "side_effects": False,
                }
            )
        else:  # mock_copy: explicitly declared deterministic passthrough only.
            outputs[target_id] = input_value
            steps.append(
                {
                    "span_id": target_id,
                    "action": span.name,
                    "adapter": adapter,
                    "outcome": "passed",
                    "input_from_span": upstream_id,
                    "details": {"copied_type": type(input_value).__name__},
                    "side_effects": False,
                }
            )

    executed = [s for s in steps if s["outcome"] in {"passed", "failed"}]
    all_passed = bool(executed) and all(s["outcome"] == "passed" for s in executed)
    has_failed = any(s["outcome"] == "failed" for s in executed)
    if not executed:
        status = "Insufficient Evidence"
        decision = "no_sandbox_adapter_ran"
    elif all_passed:
        status = "Experimentally Supported"
        decision = "declared_deterministic_downstream_checks_passed"
    elif has_failed:
        status = "Experimentally Refuted"
        decision = "at_least_one_declared_deterministic_downstream_check_failed"
    else:
        status = "Insufficient Evidence"
        decision = "incomplete_sandbox_chain"
    return {
        "title": title,
        "status": status,
        "decision": decision,
        "scope": "This result applies only to allow-listed local deterministic checks and the supplied fixture; it does not prove the full agent task would succeed.",
        "source_span_id": source_span_id,
        "source_run_id": trace.run_id,
        "fixture_sha256": hashlib.sha256(raw_fixture.encode("utf-8")).hexdigest(),
        "fixture_redacted": redact(replacement_output, enabled=True),
        "steps": steps,
        "limitations": [
            "No imported code, tool, network, filesystem, provider, or external side effect was executed.",
            "Only mock_required_fields, mock_type_check, and mock_copy adapters ran; other actions were abstained from.",
            "Passing these checks supports only the tested local invariant, not end-to-end causal proof.",
        ],
        "reproducibility": {
            "sandbox_version": "counterfactual-sandbox-1",
            "fixture_stored_redacted": True,
            "max_steps": MAX_STEPS,
        },
    }
