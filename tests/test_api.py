"""API contract tests: ingestion, analysis, comparison, exports and errors."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def test_health_and_readiness(client: TestClient) -> None:
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    ready = client.get("/api/v1/ready")
    assert ready.status_code == 200
    assert ready.json()["database"] == "ok"


def test_ingest_and_list_runs(client: TestClient, sample_trace: dict) -> None:
    response = client.post("/api/v1/ingest", json=sample_trace)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] == 1
    assert body["inserted"] == ["sample-run"]

    listing = client.get("/api/v1/runs")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1


def test_ingest_is_idempotent(client: TestClient, sample_trace: dict) -> None:
    client.post("/api/v1/ingest", json=sample_trace)
    second = client.post("/api/v1/ingest", json=sample_trace)
    assert second.status_code == 200
    assert second.json()["updated"] == ["sample-run"]
    assert second.json()["inserted"] == []
    assert client.get("/api/v1/runs").json()["total"] == 1


def test_ingest_rejects_malformed_trace(client: TestClient) -> None:
    response = client.post("/api/v1/ingest", json={"schema_version": "1.0", "run_id": "", "spans": []})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "trace_validation_failed"


def test_validate_endpoint_does_not_store(client: TestClient, sample_trace: dict) -> None:
    response = client.post("/api/v1/validate", json=sample_trace)
    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert client.get("/api/v1/runs").json()["total"] == 0


def test_batch_ingest_reports_per_record_outcomes(client: TestClient, sample_trace: dict) -> None:
    payload = {"traces": [sample_trace, {"run_id": "broken", "spans": "not-a-list"}]}
    response = client.post("/api/v1/ingest/batch", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 1
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["index"] == 2


def test_analysis_exposes_evidence_levels(client: TestClient, sample_trace: dict) -> None:
    client.post("/api/v1/ingest", json=sample_trace)
    response = client.get("/api/v1/runs/sample-run/analysis")
    assert response.status_code == 200
    body = response.json()
    assert body["earliest_observed_error"]["span_id"] == "s-tool"
    assert body["earliest_observed_error"]["category"] == "invalid_tool_arguments"
    assert body["root_cause_hypotheses"], "expected at least one ranked hypothesis"
    assert all(h["status"] in body["evidence_statuses"] for h in body["root_cause_hypotheses"])
    assert body["missing_information"], "expected missing-information list"
    assert body["evidence_policy"]


def test_analysis_reports_propagation(client: TestClient, sample_trace: dict) -> None:
    client.post("/api/v1/ingest", json=sample_trace)
    body = client.get("/api/v1/runs/sample-run/analysis").json()
    point = body["earliest_plausible_failure_propagation_point"]
    assert point is not None
    assert point["span_id"] == "s-tool"
    assert "s-final" in point["affected_span_ids"]


def test_graph_endpoint(client: TestClient, sample_trace: dict) -> None:
    client.post("/api/v1/ingest", json=sample_trace)
    body = client.get("/api/v1/runs/sample-run/graph").json()
    assert {node["span_id"] for node in body["nodes"]} == {"s-plan", "s-tool", "s-final"}
    kinds = {edge["kind"] for edge in body["edges"]}
    assert "explicit_dependency" in kinds


def test_compare_two_runs(client: TestClient, sample_trace: dict) -> None:
    client.post("/api/v1/ingest", json=sample_trace)
    reference = json.loads(json.dumps(sample_trace))
    reference["run_id"] = "sample-run-2"
    reference["trace_id"] = "trace-sample-2"
    reference["status"] = "success"
    reference["evaluation_results"] = {"passed": True}
    for span in reference["spans"]:
        if span["span_id"] == "s-tool":
            span["status"] = "ok"
            span["exception"] = None
    client.post("/api/v1/ingest", json=reference)

    response = client.post("/api/v1/compare", json={"run_ids": ["sample-run", "sample-run-2"]})
    assert response.status_code == 200
    body = response.json()
    assert body["baseline_run_id"] == "sample-run"
    assert body["pairwise"][0]["first_divergence"] is not None


def test_compare_requires_two_runs(client: TestClient, sample_trace: dict) -> None:
    client.post("/api/v1/ingest", json=sample_trace)
    response = client.post("/api/v1/compare", json={"run_ids": ["sample-run"]})
    assert response.status_code == 422


def test_annotations_are_append_only(client: TestClient, sample_trace: dict) -> None:
    client.post("/api/v1/ingest", json=sample_trace)
    response = client.post(
        "/api/v1/runs/sample-run/annotations",
        json={"author": "qa", "note": "Checked the tool schema", "disposition": "confirmed"},
    )
    assert response.status_code == 200
    detail = client.get("/api/v1/runs/sample-run").json()
    assert len(detail["annotations"]) == 1
    assert detail["trace"]["spans"], "annotations must not alter the original trace"


def test_report_exports_in_every_format(client: TestClient, sample_trace: dict) -> None:
    client.post("/api/v1/ingest", json=sample_trace)
    markdown = client.get("/api/v1/runs/sample-run/report?format=markdown")
    assert markdown.status_code == 200
    assert "Investigation report" in markdown.text
    html = client.get("/api/v1/runs/sample-run/report?format=html")
    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    payload = client.get("/api/v1/runs/sample-run/report?format=json")
    assert payload.status_code == 200
    assert payload.json()["report_schema"] == "tracelens-investigation/1.0"
    pdf = client.get("/api/v1/runs/sample-run/report?format=pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-")


def test_delete_requires_confirmation(client: TestClient, sample_trace: dict) -> None:
    client.post("/api/v1/ingest", json=sample_trace)
    refused = client.delete("/api/v1/runs/sample-run")
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "confirmation_required"
    deleted = client.delete("/api/v1/runs/sample-run?confirm=true")
    assert deleted.status_code == 200
    assert client.get("/api/v1/runs/sample-run").status_code == 404


def test_unknown_run_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/runs/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


def test_settings_and_audit(client: TestClient, sample_trace: dict) -> None:
    client.post("/api/v1/ingest", json=sample_trace)
    settings = client.get("/api/v1/settings")
    assert settings.status_code == 200
    assert settings.json()["ai_enabled"] is False
    audit = client.get("/api/v1/audit")
    assert audit.status_code == 200
    assert any(event["event_type"] == "trace.imported" for event in audit.json()["items"])


def test_redaction_hides_secrets(client: TestClient) -> None:
    trace = {
        "schema_version": "1.0",
        "trace_id": "trace-secret",
        "run_id": "secret-run",
        "task": "Secret handling",
        "started_at": "2026-01-01T00:00:00Z",
        "status": "success",
        "spans": [
            {
                "span_id": "s1",
                "name": "call api",
                "action_type": "tool",
                "start_time": "2026-01-01T00:00:00Z",
                "end_time": "2026-01-01T00:00:01Z",
                "status": "ok",
                "inputs": {"api_key": "sk-abcdefghijklmnopqrstuvwx", "note": "token=abcdefghijklmnop"},
                "outputs": {"Authorization": "Bearer abcdefghijklmnopqrst"},
            }
        ],
    }
    client.post("/api/v1/ingest", json=trace)
    body = client.get("/api/v1/runs/secret-run").json()
    serialized = json.dumps(body)
    assert "sk-abcdefghijklmnopqrstuvwx" not in serialized
    assert "[REDACTED]" in serialized


def test_rate_limit_is_enforced(client: TestClient) -> None:
    from app.config import settings as app_settings

    original = app_settings.rate_limit_per_minute
    app_settings.rate_limit_per_minute = 3
    try:
        statuses = [client.get("/api/v1/runs").status_code for _ in range(6)]
    finally:
        app_settings.rate_limit_per_minute = original
    assert 429 in statuses


def test_openapi_documents_endpoints(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    for path in (
        "/api/v1/runs",
        "/api/v1/ingest",
        "/api/v1/compare",
        "/api/v1/runs/{run_id}/analysis",
        "/api/v1/llm/assist",
    ):
        assert path in paths
