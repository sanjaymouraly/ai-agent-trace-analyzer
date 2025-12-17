"""Controlled counterfactual experiments: isolation, safety and evidence labelling."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.experiments import MAX_FIXTURE_BYTES, execute_counterfactual
from app.ingestion import normalize_trace
from examples.generate import generate_demo_traces

TRACES = {raw["run_id"]: normalize_trace(raw) for raw in generate_demo_traces()}


def test_counterfactual_runs_only_allow_listed_deterministic_adapters() -> None:
    trace = TRACES["demo-orders-invalid-args"]
    result = execute_counterfactual(trace, "bad-q1", {"orders": [{"id": "A1"}]}, "fixture with orders")
    executed = [step for step in result["steps"] if step["outcome"] in {"passed", "failed"}]
    assert executed, "the allow-listed downstream validator should have run"
    for step in executed:
        assert step.get("side_effects") is False
    assert result["status"] in {"Experimentally Supported", "Experimentally Refuted", "Insufficient Evidence"}
    assert any("No imported code" in limitation for limitation in result["limitations"])


def test_counterfactual_refuses_unknown_span() -> None:
    with pytest.raises(ValueError, match="not present"):
        execute_counterfactual(TRACES["demo-orders-invalid-args"], "does-not-exist", {}, "t")


def test_counterfactual_rejects_oversized_fixtures() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        execute_counterfactual(TRACES["demo-orders-invalid-args"], "bad-q1", "x" * (MAX_FIXTURE_BYTES + 10), "t")


def test_counterfactual_abstains_from_actions_without_a_declared_adapter() -> None:
    trace = TRACES["demo-orders-invalid-args"]
    result = execute_counterfactual(trace, "bad-q1", {"orders": []}, "t")
    abstained = [step for step in result["steps"] if step["outcome"] == "not_executed"]
    assert abstained, "actions without an allow-listed adapter must not be executed"
    assert all("never called" in step["reason"] for step in abstained)


def test_counterfactual_records_reproducibility_metadata() -> None:
    result = execute_counterfactual(TRACES["demo-orders-invalid-args"], "bad-q1", {"orders": []}, "t")
    assert len(result["fixture_sha256"]) == 64
    assert result["reproducibility"]["sandbox_version"]
    assert result["reproducibility"]["max_steps"] > 0
    assert result["scope"]


def test_counterfactual_redacts_the_stored_fixture() -> None:
    trace = TRACES["demo-orders-invalid-args"]
    result = execute_counterfactual(trace, "bad-q1", {"api_key": "sk-secret-value", "orders": []}, "t")
    assert "sk-secret-value" not in str(result["fixture_redacted"])


def test_experiment_endpoint_requires_explicit_confirmation(client: TestClient) -> None:
    from examples.generate import generate_demo_traces

    for raw in generate_demo_traces():
        client.post("/api/v1/ingest", json=raw)
    refused = client.post(
        "/api/v1/experiments/counterfactual",
        json={"run_id": "demo-orders-invalid-args", "source_span_id": "bad-q1", "replacement_output": {"orders": []}},
    )
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "confirmation_required"


def test_experiment_endpoint_stores_and_lists_results(client: TestClient) -> None:
    from examples.generate import generate_demo_traces

    for raw in generate_demo_traces():
        client.post("/api/v1/ingest", json=raw)
    response = client.post(
        "/api/v1/experiments/counterfactual",
        json={
            "run_id": "demo-orders-invalid-args",
            "source_span_id": "bad-q1",
            "replacement_output": {"orders": [{"id": "A1"}]},
            "title": "Corrected fixture",
            "confirm_side_effect_free": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"Experimentally Supported", "Experimentally Refuted", "Insufficient Evidence"}
    listing = client.get("/api/v1/runs/demo-orders-invalid-args/experiments")
    assert listing.status_code == 200
    assert len(listing.json()["items"]) == 1
