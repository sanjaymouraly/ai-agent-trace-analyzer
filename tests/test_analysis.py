from app.analysis import analyze_trace, classify_failures, reconstruct_graph
from app.comparison import compare_traces
from app.experiments import execute_counterfactual
from app.ingestion import normalize_trace
from examples.generate import generate_demo_traces

TRACES = {raw["run_id"]: normalize_trace(raw, source_format="demo-json") for raw in generate_demo_traces()}


def test_dependency_edges_are_typed_and_chronology_is_not_causality():
    trace = TRACES["demo-parallel-success"]
    graph = reconstruct_graph(trace)
    sibling_edges = [e for e in graph["edges"] if {e["source"], e["target"]} <= {"par-s1", "par-d1", "par-i1"}]
    assert sibling_edges == []
    assert any(e["source"] == "par-p1" and e["target"] == "par-s1" and e["dependency"] for e in graph["edges"])
    assert graph["critical_path_supported"] is True
    assert graph["critical_path"]


def test_invalid_arguments_classified_and_linked_to_downstream_failure():
    trace = TRACES["demo-orders-invalid-args"]
    result = analyze_trace(trace)
    assert result["earliest_observed_error"]["span_id"] == "bad-q1"
    assert result["earliest_observed_error"]["category"] == "invalid_tool_arguments"
    invalid = next(f for f in result["findings"] if f["span_ids"] == ["bad-q1"])
    assert invalid["status"] == "Observed"
    assert "bad-s1" in result["impact"]["bad-q1"]
    assert result["root_cause_hypotheses"]
    assert result["root_cause_hypotheses"][0]["status"] == "Inferred Hypothesis"
    assert "does not prove" in " ".join(result["root_cause_hypotheses"][0]["limitations"])


def test_timeout_failure_that_recovers_is_not_a_root_cause_hypothesis():
    result = analyze_trace(TRACES["demo-timeout-recovered"])
    assert any(f["category"] == "tool_timeout" and f["status"] == "Observed" for f in result["findings"])
    assert result["run_outcome"] == "success"
    assert result["root_cause_hypotheses"] == []
    assert "completed successfully" in result["summary"]


def test_successful_tool_response_is_not_assumed_correct():
    trace = TRACES["demo-final-answer-failure"]
    findings = classify_failures(trace)
    assert any(f["category"] == "evaluation_or_final_answer_failure" for f in findings)
    assert not any(f["category"] == "tool_execution_exception" for f in findings)
    assert trace.spans[1].status.value == "ok"


def test_retrieval_relevance_metadata_is_evidence_labeled():
    result = analyze_trace(TRACES["demo-rag-stale-context"])
    finding = next(f for f in result["findings"] if f["category"] == "retrieval_context_error")
    assert finding["span_ids"] == ["rag-r1"]
    assert finding["status"] in {"Observed", "Inferred Hypothesis"}


def test_ambiguous_failure_abstains_without_inventing_a_span_cause():
    result = analyze_trace(TRACES["demo-ambiguous"])
    assert result["earliest_observed_error"] is None
    assert result["root_cause_hypotheses"][0]["category"] == "unknown_or_insufficient_evidence"
    assert result["root_cause_hypotheses"][0]["confidence"] <= 0.2


def test_compare_identifies_earliest_input_divergence_and_preserves_retry_occurrences():
    result = compare_traces([TRACES["demo-orders-reference"], TRACES["demo-orders-invalid-args"]])
    pair = result["pairwise"][0]
    divergence = pair["first_divergence"]
    assert divergence["status"] == "Observed"
    assert divergence["left_span_id"] == "ord-q1"
    assert divergence["right_span_id"] == "bad-q1"
    assert any(d["field"] == "inputs" for d in divergence["differences"])
    retry = compare_traces([TRACES["demo-timeout-recovered"], TRACES["demo-timeout-recovered"]])
    assert (
        sum(1 for item in retry["pairwise"][0]["aligned_actions"] if item.get("left", {}).get("tool_name") == "inventory.lookup")
        == 2
    )


def test_graph_reports_orphans_broken_references_and_cycles():
    raw = {
        "trace_id": "t",
        "run_id": "r",
        "task": "cycle",
        "status": "failure",
        "spans": [
            {
                "span_id": "a",
                "name": "a",
                "action_type": "tool",
                "start_time": "2026-01-01T00:00:00Z",
                "status": "ok",
                "parent_span_id": "missing",
                "depends_on": ["b"],
            },
            {
                "span_id": "b",
                "name": "b",
                "action_type": "tool",
                "start_time": "2026-01-01T00:00:01Z",
                "status": "ok",
                "depends_on": ["a", "absent"],
            },
        ],
    }
    graph = reconstruct_graph(normalize_trace(raw))
    kinds = {warning["type"] for warning in graph["warnings"]}
    assert {"orphan_span", "broken_dependency", "dependency_cycle", "topological_fallback"} <= kinds
    assert graph["critical_path"] is None


def test_counterfactual_uses_only_allowlisted_local_adapter_and_records_scope():
    trace = TRACES["demo-orders-invalid-args"]
    result = execute_counterfactual(trace, "bad-q1", {"orders": [{"id": "fixture"}]}, "valid fixture")
    assert result["status"] == "Experimentally Supported"
    assert result["steps"][0]["span_id"] == "bad-s1"
    assert result["steps"][0]["outcome"] == "passed"
    assert result["reproducibility"]["fixture_stored_redacted"] is True
    assert "No imported code" in result["limitations"][0]


def test_unknown_counterfactual_adapter_abstains_and_never_executes():
    trace = TRACES["demo-orders-invalid-args"].model_copy(deep=True)
    target = next(s for s in trace.spans if s.span_id == "bad-s1")
    target.attributes["replay_adapter"] = "arbitrary_uploaded_code"
    result = execute_counterfactual(trace, "bad-q1", {"orders": []}, "unknown adapter")
    assert result["status"] == "Insufficient Evidence"
    assert result["steps"][0]["outcome"] == "not_executed"


def test_large_dependency_chain_avoids_recursive_cycle_walk_and_path_quadratic_memory():
    spans = []
    for index in range(1500):
        span = {
            "span_id": f"s{index}",
            "name": "step",
            "action_type": "tool",
            "status": "ok",
            "start_time": f"2026-01-01T00:00:{index % 60:02d}Z",
            "end_time": f"2026-01-01T00:00:{index % 60:02d}.001Z",
        }
        if index:
            span["depends_on"] = [f"s{index - 1}"]
        spans.append(span)
    trace = normalize_trace({"trace_id": "large", "run_id": "large", "status": "success", "spans": spans})
    graph = reconstruct_graph(trace)
    assert len(graph["logical_order"]) == 1500
    assert graph["critical_path"] is not None
    assert graph["critical_path"]["span_ids"][0] == "s0"
    assert graph["critical_path"]["span_ids"][-1] == "s1499"


def test_missing_outputs_and_orphan_edges_are_reported_but_never_ordered_by_time():
    raw = {
        "trace_id": "t",
        "run_id": "r",
        "status": "success",
        "spans": [
            {
                "span_id": "z",
                "name": "orphan",
                "action_type": "tool",
                "status": "ok",
                "start_time": "2026-01-01T00:00:00Z",
                "parent_span_id": "absent",
            },
            {"span_id": "a", "name": "later", "action_type": "agent", "status": "ok", "start_time": "2026-01-01T00:00:02Z"},
        ],
    }
    graph = reconstruct_graph(normalize_trace(raw))
    assert any(w["type"] == "orphan_span" for w in graph["warnings"])
    assert any(w["type"] == "missing_output" for w in graph["warnings"])
    assert not graph["edges"]
