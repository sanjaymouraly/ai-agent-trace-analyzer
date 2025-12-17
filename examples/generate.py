"""Generate reproducible synthetic multi-step agent traces (no external calls)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BASE = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)


def _span(
    sid: str,
    name: str,
    kind: str,
    offset: int,
    duration: int,
    *,
    parent: str | None = None,
    deps: list[str] | None = None,
    status: str = "ok",
    tool: str | None = None,
    inputs: Any = None,
    outputs: Any = None,
    exception: str | None = None,
    attrs: dict[str, Any] | None = None,
    retry: int | None = None,
    action_id: str | None = None,
    call_id: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    tokens: int = 0,
) -> dict[str, Any]:
    t = BASE - timedelta(days=1)
    # ``offset`` and ``duration`` are both milliseconds relative to run start.
    start = t + timedelta(milliseconds=offset)
    end = start + timedelta(milliseconds=duration)
    return {
        "span_id": sid,
        "parent_span_id": parent,
        "action_id": action_id,
        "name": name,
        "action_type": kind,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "status": status,
        "inputs": inputs,
        "outputs": outputs,
        "tool_name": tool,
        "tool_call_id": call_id,
        "model": model,
        "provider": provider,
        "token_usage": {"total_tokens": tokens} if tokens else {},
        "retry_attempt": retry,
        "exception": exception,
        "explicit_dependencies": deps or [],
        "attributes": attrs or {},
    }


def _run(
    run_id: str,
    task_id: str,
    task: str,
    status: str,
    spans: list[dict[str, Any]],
    *,
    day: int = 1,
    final: Any = None,
    evaluation: dict[str, Any] | None = None,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = BASE - timedelta(days=day)
    shift = timedelta(days=day - 1)
    for span in spans:
        span["start_time"] = (datetime.fromisoformat(span["start_time"]) - shift).isoformat()
        span["end_time"] = (datetime.fromisoformat(span["end_time"]) - shift).isoformat()
    _extend_parents_to_children(spans)
    end_values = [datetime.fromisoformat(s["end_time"]) for s in spans]
    ended = max(end_values) if end_values else started
    return {
        "schema_version": "1.0",
        "trace_id": f"trace-{run_id}",
        "run_id": run_id,
        "session_id": f"session-{task_id}",
        "task_id": task_id,
        "task": task,
        "agent_id": "demo-orchestrator",
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "status": status,
        "final_answer": final,
        "evaluation_results": evaluation or {},
        "environment": {"dataset": "synthetic-demo", "environment": "local-demo", **(environment or {})},
        "spans": spans,
    }


def _extend_parents_to_children(spans: list[dict[str, Any]]) -> None:
    """Make parent spans cover their children.

    Real instrumented agents open a parent span before the child work starts
    and close it afterwards. The synthetic fixtures are authored per-action,
    so the parent window is widened here to stay consistent with that model.
    """
    by_id = {span["span_id"]: span for span in spans}
    children: dict[str, list[str]] = {}
    for span in spans:
        parent = span.get("parent_span_id")
        if parent and parent in by_id:
            children.setdefault(parent, []).append(span["span_id"])

    def latest_end(span_id: str, seen: set[str]) -> datetime:
        if span_id in seen:
            return datetime.fromisoformat(by_id[span_id]["end_time"])
        seen.add(span_id)
        end = datetime.fromisoformat(by_id[span_id]["end_time"])
        for child in children.get(span_id, []):
            end = max(end, latest_end(child, seen))
        return end

    for span in spans:
        parent = span.get("parent_span_id")
        if not parent or parent not in by_id:
            continue
        parent_span = by_id[parent]
        child_end = latest_end(span["span_id"], set())
        if datetime.fromisoformat(parent_span["end_time"]) < child_end:
            parent_span["end_time"] = child_end.isoformat()


def generate_demo_traces() -> list[dict[str, Any]]:
    traces: list[dict[str, Any]] = []
    # Successful reference for the invalid-argument investigation.
    traces.append(
        _run(
            "demo-orders-reference",
            "weekly-orders",
            "Summarize orders from the last seven days",
            "success",
            [
                _span(
                    "ord-p1",
                    "Plan order summary",
                    "planning",
                    0,
                    31,
                    outputs={"steps": ["search", "summarize"]},
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=105,
                ),
                _span(
                    "ord-q1",
                    "Search orders",
                    "tool",
                    80,
                    118,
                    parent="ord-p1",
                    deps=["ord-p1"],
                    tool="orders.search",
                    action_id="search-orders",
                    call_id="call-order-1",
                    inputs={"from": "2026-10-10", "to": "2026-10-17", "limit": 100},
                    outputs={"orders": [{"id": "O-104", "amount": 48.5}, {"id": "O-105", "amount": 72.0}]},
                    attrs={"argument_schema_valid": True},
                ),
                _span(
                    "ord-s1",
                    "Summarize order totals",
                    "model",
                    230,
                    205,
                    parent="ord-p1",
                    deps=["ord-q1"],
                    inputs={"orders_from_span": "ord-q1"},
                    outputs={"count": 2, "total": 120.5},
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=210,
                ),
                _span(
                    "ord-f1",
                    "Final answer",
                    "evaluation",
                    460,
                    19,
                    deps=["ord-s1"],
                    outputs="Two orders total $120.50.",
                    attrs={"evaluation_passed": True},
                ),
            ],
            final="Two orders total $120.50.",
            evaluation={"passed": True, "score": 1.0},
            day=1,
        )
    )

    traces.append(
        _run(
            "demo-orders-invalid-args",
            "weekly-orders",
            "Summarize orders from the last seven days",
            "failure",
            [
                _span(
                    "bad-p1",
                    "Plan order summary",
                    "planning",
                    0,
                    28,
                    outputs={"steps": ["search", "summarize"]},
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=98,
                ),
                _span(
                    "bad-q1",
                    "Search orders",
                    "tool",
                    70,
                    44,
                    parent="bad-p1",
                    deps=["bad-p1"],
                    status="error",
                    tool="orders.search",
                    action_id="search-orders",
                    call_id="call-order-bad",
                    inputs={"from_date": "last 7 days", "limit": "lots"},
                    exception="Invalid arguments: expected ISO date for 'from' and 'to'; 'limit' must be an integer",
                    attrs={"argument_schema_valid": False},
                ),
                _span(
                    "bad-s1",
                    "Summarize order totals",
                    "model",
                    150,
                    71,
                    parent="bad-p1",
                    deps=["bad-q1"],
                    status="error",
                    inputs={"orders": None},
                    exception="Missing data: no orders output was supplied",
                    attrs={
                        "missing_input": True,
                        "replay_adapter": "mock_required_fields",
                        "replay_spec": {"required_fields": ["orders"]},
                    },
                ),
                _span(
                    "bad-f1",
                    "Evaluate final answer",
                    "evaluation",
                    240,
                    14,
                    deps=["bad-s1"],
                    status="error",
                    inputs={"summary": None},
                    exception="Evaluation failed: expected a non-empty order summary",
                    attrs={"missing_input": True},
                ),
            ],
            final=None,
            evaluation={"passed": False, "reason": "No order summary produced."},
            day=1,
        )
    )

    traces.append(
        _run(
            "demo-timeout-recovered",
            "inventory-status",
            "Check inventory status with retry",
            "success",
            [
                _span(
                    "retry-p1",
                    "Plan stock check",
                    "planning",
                    0,
                    25,
                    outputs={"tool": "inventory.lookup"},
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=65,
                ),
                _span(
                    "retry-t1",
                    "Inventory lookup attempt 1",
                    "tool",
                    65,
                    7000,
                    parent="retry-p1",
                    deps=["retry-p1"],
                    status="timeout",
                    tool="inventory.lookup",
                    action_id="inventory-lookup",
                    retry=1,
                    call_id="inv-call-1",
                    inputs={"sku": "SKU-900"},
                    exception="Read timed out after 7 seconds",
                ),
                _span(
                    "retry-t2",
                    "Inventory lookup attempt 2",
                    "tool",
                    7350,
                    166,
                    parent="retry-p1",
                    deps=["retry-p1"],
                    tool="inventory.lookup",
                    action_id="inventory-lookup",
                    retry=2,
                    call_id="inv-call-2",
                    inputs={"sku": "SKU-900"},
                    outputs={"sku": "SKU-900", "available": 14},
                ),
                _span(
                    "retry-f1",
                    "Report stock",
                    "model",
                    7600,
                    88,
                    deps=["retry-t2"],
                    inputs={"inventory_from_span": "retry-t2"},
                    outputs="Fourteen units are available.",
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=84,
                ),
            ],
            final="Fourteen units are available.",
            evaluation={"passed": True},
            day=1,
        )
    )

    traces.append(
        _run(
            "demo-rag-stale-context",
            "refund-policy",
            "Answer whether a 45-day-old purchase is refundable",
            "failure",
            [
                _span(
                    "rag-p1",
                    "Plan policy question",
                    "planning",
                    0,
                    24,
                    outputs={"steps": ["retrieve policy", "answer"]},
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=75,
                ),
                _span(
                    "rag-r1",
                    "Retrieve refund policy",
                    "retrieval",
                    55,
                    93,
                    parent="rag-p1",
                    deps=["rag-p1"],
                    tool="policy.vector_search",
                    inputs={"query": "refund window"},
                    outputs={"document_id": "policy-2023", "text": "Returns accepted within 90 days."},
                    attrs={
                        "expected_document_id": "policy-2026",
                        "document_id": "policy-2023",
                        "retrieval_relevant": False,
                        "retrieval_score": 0.31,
                    },
                ),
                _span(
                    "rag-a1",
                    "Compose policy answer",
                    "model",
                    195,
                    230,
                    parent="rag-p1",
                    deps=["rag-r1"],
                    inputs={"context_from_span": "rag-r1"},
                    outputs="Yes, returns are allowed within 90 days.",
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=242,
                ),
                _span(
                    "rag-e1",
                    "Policy answer evaluation",
                    "evaluation",
                    450,
                    20,
                    deps=["rag-a1"],
                    status="error",
                    inputs={"answer": "Yes, returns are allowed."},
                    exception="Evaluation failed: answer used superseded policy; current policy is 30 days",
                    attrs={"evaluation_passed": False},
                ),
            ],
            final="Yes, returns are allowed within 90 days.",
            evaluation={"passed": False, "reason": "Used superseded policy revision."},
            day=1,
        )
    )

    traces.append(
        _run(
            "demo-wrong-tool",
            "invoice-lookup",
            "Find invoice INV-244 for Acme",
            "failure",
            [
                _span(
                    "tool-p1",
                    "Select lookup action",
                    "planning",
                    0,
                    45,
                    outputs={"expected_tool": "invoice_search"},
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=90,
                ),
                _span(
                    "tool-c1",
                    "Search customer directory",
                    "tool",
                    80,
                    128,
                    parent="tool-p1",
                    deps=["tool-p1"],
                    tool="customer.search",
                    inputs={"name": "Acme", "invoice": "INV-244"},
                    outputs={"customer_id": "C-22"},
                    attrs={"expected_tool": "invoice_search"},
                ),
                _span(
                    "tool-a1",
                    "Format invoice result",
                    "model",
                    240,
                    63,
                    deps=["tool-c1"],
                    status="error",
                    inputs={"invoice": None},
                    exception="Missing data: invoice lookup result was not found",
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=72,
                ),
            ],
            final=None,
            evaluation={"passed": False},
            day=2,
        )
    )

    traces.append(
        _run(
            "demo-corrupt-state",
            "customer-onboarding",
            "Create account then verify persisted profile",
            "failure",
            [
                _span(
                    "state-p1",
                    "Plan onboarding",
                    "planning",
                    0,
                    33,
                    outputs={"steps": ["create", "verify"]},
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=80,
                ),
                _span(
                    "state-c1",
                    "Create customer profile",
                    "tool",
                    75,
                    172,
                    parent="state-p1",
                    deps=["state-p1"],
                    tool="crm.create_profile",
                    inputs={"email": "demo@example.invalid", "schema_version": 2},
                    outputs={"customer_id": "C-808", "state_version": 2},
                    attrs={"state_version": 2},
                ),
                _span(
                    "state-v1",
                    "Verify profile state",
                    "state",
                    290,
                    12,
                    deps=["state-c1"],
                    status="error",
                    inputs={"customer_id": "C-808", "state_version": 1},
                    exception="State checksum mismatch: consumer read version 1 after producer wrote version 2",
                    attrs={"state_valid": False, "consumed_invalid_output": True},
                ),
                _span(
                    "state-f1",
                    "Report onboarding result",
                    "model",
                    350,
                    60,
                    deps=["state-v1"],
                    status="error",
                    inputs={"profile": None},
                    exception="Missing data: verified profile not available",
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=40,
                ),
            ],
            final=None,
            evaluation={"passed": False},
            day=2,
        )
    )

    traces.append(
        _run(
            "demo-delegation-timeout",
            "market-research",
            "Research two markets using specialist agents",
            "failure",
            [
                _span(
                    "multi-o1",
                    "Research orchestrator",
                    "agent",
                    0,
                    8600,
                    outputs={"delegates": ["market-a", "market-b"]},
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=100,
                    attrs={"agent_id": "orchestrator"},
                ),
                _span(
                    "multi-d1",
                    "Delegate market A",
                    "delegation",
                    50,
                    20,
                    parent="multi-o1",
                    deps=["multi-o1"],
                    outputs={"agent": "analyst-a"},
                    attrs={"agent_id": "orchestrator", "target_agent_id": "analyst-a"},
                ),
                _span(
                    "multi-d2",
                    "Delegate market B",
                    "delegation",
                    60,
                    18,
                    parent="multi-o1",
                    deps=["multi-o1"],
                    outputs={"agent": "analyst-b"},
                    attrs={"agent_id": "orchestrator", "target_agent_id": "analyst-b"},
                ),
                _span(
                    "multi-a1",
                    "Analyst A query market data",
                    "tool",
                    160,
                    300,
                    parent="multi-d1",
                    deps=["multi-d1"],
                    tool="market-data.query",
                    inputs={"market": "A"},
                    outputs={"growth": 0.12},
                    attrs={"agent_id": "analyst-a"},
                ),
                _span(
                    "multi-b1",
                    "Analyst B query market data",
                    "tool",
                    170,
                    8000,
                    parent="multi-d2",
                    deps=["multi-d2"],
                    status="timeout",
                    tool="market-data.query",
                    inputs={"market": "B"},
                    exception="Deadline exceeded while waiting for market B",
                    attrs={"agent_id": "analyst-b"},
                ),
                _span(
                    "multi-f1",
                    "Merge specialist results",
                    "model",
                    8300,
                    180,
                    deps=["multi-a1", "multi-b1"],
                    status="error",
                    inputs={"market_a": "multi-a1", "market_b": None},
                    exception="Missing data: specialist B result absent",
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=55,
                ),
            ],
            final=None,
            evaluation={"passed": False},
            day=2,
        )
    )

    traces.append(
        _run(
            "demo-parallel-success",
            "supply-forecast",
            "Combine parallel supplier and demand signals",
            "success",
            [
                _span(
                    "par-p1",
                    "Plan supply forecast",
                    "planning",
                    0,
                    40,
                    outputs={"parallel": True},
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=81,
                ),
                _span(
                    "par-s1",
                    "Read supplier feed",
                    "tool",
                    80,
                    750,
                    deps=["par-p1"],
                    tool="supplier.api",
                    inputs={"region": "south"},
                    outputs={"lead_days": 3},
                ),
                _span(
                    "par-d1",
                    "Read demand forecast",
                    "tool",
                    90,
                    520,
                    deps=["par-p1"],
                    tool="forecast.api",
                    inputs={"region": "south"},
                    outputs={"units": 120},
                ),
                _span(
                    "par-i1",
                    "Read inventory snapshot",
                    "tool",
                    95,
                    680,
                    deps=["par-p1"],
                    tool="inventory.api",
                    inputs={"region": "south"},
                    outputs={"units": 86},
                ),
                _span(
                    "par-m1",
                    "Merge forecast signals",
                    "model",
                    900,
                    140,
                    deps=["par-s1", "par-d1", "par-i1"],
                    inputs={"supplier": "par-s1", "demand": "par-d1", "inventory": "par-i1"},
                    outputs={"recommended_order": 45},
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=110,
                ),
            ],
            final="Recommend ordering 45 units.",
            evaluation={"passed": True},
            day=3,
        )
    )

    traces.append(
        _run(
            "demo-final-answer-failure",
            "support-refund",
            "Answer customer refund eligibility",
            "failure",
            [
                _span(
                    "fin-p1",
                    "Plan refund check",
                    "planning",
                    0,
                    25,
                    outputs={"steps": ["lookup", "answer"]},
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=65,
                ),
                _span(
                    "fin-t1",
                    "Fetch purchase record",
                    "tool",
                    60,
                    120,
                    deps=["fin-p1"],
                    tool="orders.get",
                    inputs={"order_id": "O-301"},
                    outputs={"purchase_date": "2026-09-20", "amount": 36},
                ),
                _span(
                    "fin-a1",
                    "Draft refund answer",
                    "model",
                    220,
                    160,
                    deps=["fin-t1"],
                    inputs={"order": "fin-t1"},
                    outputs="Your order is not eligible because it is over 30 days old.",
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=155,
                ),
                _span(
                    "fin-e1",
                    "Evaluate refund answer",
                    "evaluation",
                    400,
                    22,
                    deps=["fin-a1"],
                    status="error",
                    inputs={"answer": "..."},
                    exception="Evaluation failed: policy requires escalation for damaged-item exceptions",
                    attrs={"evaluation_passed": False},
                ),
            ],
            final="Your order is not eligible because it is over 30 days old.",
            evaluation={"passed": False, "reason": "Escalation policy omitted."},
            day=3,
        )
    )

    traces.append(
        _run(
            "demo-permission-denied",
            "account-access",
            "Read tenant billing configuration",
            "failure",
            [
                _span(
                    "perm-p1",
                    "Plan billing lookup",
                    "planning",
                    0,
                    20,
                    outputs={"tool": "billing.get_config"},
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=45,
                ),
                _span(
                    "perm-t1",
                    "Read billing configuration",
                    "tool",
                    55,
                    32,
                    deps=["perm-p1"],
                    status="error",
                    tool="billing.get_config",
                    inputs={"tenant_id": "tenant-42"},
                    exception="403 Forbidden: service identity lacks billing.read permission",
                ),
                _span(
                    "perm-f1",
                    "Report billing state",
                    "model",
                    100,
                    45,
                    deps=["perm-t1"],
                    status="error",
                    exception="Missing data: billing configuration unavailable",
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=20,
                ),
            ],
            final=None,
            evaluation={"passed": False},
            day=4,
        )
    )

    traces.append(
        _run(
            "demo-ambiguous",
            "knowledge-answer",
            "Answer an internal knowledge question",
            "failure",
            [
                _span(
                    "amb-p1",
                    "Plan knowledge search",
                    "planning",
                    0,
                    25,
                    outputs={"query": "benefits"},
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=56,
                ),
                _span(
                    "amb-r1",
                    "Search knowledge base",
                    "retrieval",
                    60,
                    76,
                    deps=["amb-p1"],
                    tool="knowledge.search",
                    inputs={"query": "benefits"},
                    outputs={"snippets": ["A handbook excerpt"]},
                ),
                _span(
                    "amb-a1",
                    "Generate final answer",
                    "model",
                    170,
                    135,
                    deps=["amb-r1"],
                    inputs={"context": "amb-r1"},
                    outputs="Please contact the relevant team.",
                    model="gpt-demo",
                    provider="offline-fixture",
                    tokens=99,
                ),
            ],
            final="Please contact the relevant team.",
            evaluation={},
            day=4,
        )
    )
    return traces


def write_fixtures(destination: Path | None = None) -> list[Path]:
    directory = destination or Path(__file__).resolve().parent / "traces"
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for trace in generate_demo_traces():
        path = directory / f"{trace['run_id']}.json"
        path.write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    for file in write_fixtures():
        print(file)
