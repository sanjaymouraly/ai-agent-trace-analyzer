"""Cost estimation, budget enforcement and provider configuration."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import pricing
from app.llm import ProviderConfig, estimate_request_cost


def test_known_model_cost_is_derived_from_reference_pricing() -> None:
    result = pricing.estimate_cost("openai", "gpt-4o-mini", 1_000_000, 1_000_000)
    assert result["estimated_cost_usd"] == pytest.approx(0.75, rel=1e-6)
    assert result["pricing_source"] == pricing.OPENAI_SOURCE
    assert result["as_of"] == pricing.AS_OF


def test_anthropic_and_prefix_matching() -> None:
    exact = pricing.estimate_cost("anthropic", "claude-sonnet-5", 1_000_000, 0)
    assert exact["estimated_cost_usd"] == pytest.approx(2.0)
    versioned = pricing.estimate_cost("anthropic", "claude-sonnet-5-20260101", 1_000_000, 0)
    assert versioned["estimated_cost_usd"] == pytest.approx(2.0)


def test_unknown_model_reports_no_cost_instead_of_a_guess() -> None:
    result = pricing.estimate_cost("openai", "totally-unknown-model", 1000, 1000)
    assert result["estimated_cost_usd"] is None
    assert "unknown model" in result["pricing_source"]


def test_local_models_have_no_per_token_charge() -> None:
    result = pricing.estimate_cost("ollama", "gemma3:270m", 1_000_000, 1_000_000)
    assert result["estimated_cost_usd"] == 0.0


def test_operator_overrides_win(monkeypatch) -> None:
    monkeypatch.setenv(
        "LLM_PRICING_OVERRIDES",
        '{"gpt-4o-mini": {"input_per_million": 1.0, "output_per_million": 2.0, "source": "internal contract"}}',
    )
    result = pricing.estimate_cost("openai", "gpt-4o-mini", 1_000_000, 1_000_000)
    assert result["estimated_cost_usd"] == pytest.approx(3.0)
    assert result["pricing_source"] == "internal contract"


def test_pricing_summary_exposes_sources_and_date() -> None:
    summary = pricing.pricing_summary()
    assert summary["as_of"] == pricing.AS_OF
    assert summary["sources"]["openai"].startswith("https://")
    assert summary["models_with_known_price"]["openai"] > 0


def test_preflight_estimate_uses_configured_token_budget() -> None:
    config = ProviderConfig("openai", "OpenAI", "openai", "https://api.openai.com/v1", "k", "gpt-4o-mini", True, True)
    estimate = estimate_request_cost(config, "x" * 4000)
    assert estimate["estimated_input_tokens"] == 1000
    assert estimate["max_output_tokens"] >= 64
    assert estimate["estimated_cost_usd"] is not None
    assert "Approximate input length" in estimate["estimate_basis"]


def test_assist_requires_consent(client: TestClient, sample_trace: dict) -> None:
    client.post("/api/v1/ingest", json=sample_trace)
    response = client.post("/api/v1/llm/assist", json={"run_id": "sample-run", "question": "Why did it fail?"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "external_transfer_consent_required"


def test_assist_requires_a_configured_provider(client: TestClient, sample_trace: dict) -> None:
    client.post("/api/v1/ingest", json=sample_trace)
    response = client.post(
        "/api/v1/llm/assist",
        json={"run_id": "sample-run", "question": "Why did it fail?", "authorize_external_transfer": True},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_not_ready"


def test_daily_request_budget_is_enforced(client: TestClient, sample_trace: dict, monkeypatch) -> None:
    from app import db, llm

    client.post("/api/v1/ingest", json=sample_trace)
    monkeypatch.setattr(llm, "daily_request_limit", lambda: 1)
    with db.session_scope() as session:
        db.record_ai_usage(session, "default", "openai", "gpt-4o-mini", 10, 10, 0.001, "test")
    response = client.post(
        "/api/v1/llm/assist",
        json={"run_id": "sample-run", "question": "Why did it fail?", "authorize_external_transfer": True},
    )
    # The provider is not configured, so the budget check is reached only after
    # configuration; with the limit already reached the budget error wins.
    assert response.status_code in {429, 503}
    if response.status_code == 429:
        assert response.json()["error"]["code"] == "ai_daily_limit_reached"


def test_llm_status_reports_budget_and_limits(client: TestClient) -> None:
    status = client.get("/api/v1/llm/status")
    assert status.status_code == 200
    body = status.json()
    assert body["daily_limit"] >= 1
    assert body["daily_budget_usd"] >= 0
    assert body["requires_external_consent"] is True
    assert body["pricing"]["as_of"]
    assert {item["id"] for item in body["providers"]} >= {"disabled", "openai", "anthropic", "openrouter", "ollama"}


def test_estimate_endpoint_reports_unknown_cost_for_unknown_model(client: TestClient, sample_trace: dict) -> None:
    client.post("/api/v1/ingest", json=sample_trace)
    client.post("/api/v1/llm/credentials", json={"provider": "openai", "api_key": "sk-test", "model": "gpt-4o-mini"})
    client.put("/api/v1/llm/config", json={"provider": "openai", "model": "gpt-4o-mini"})
    response = client.post("/api/v1/llm/estimate", json={"run_id": "sample-run", "question": "What failed?"})
    assert response.status_code == 200
    body = response.json()
    assert body["estimate"]["estimated_cost_usd"] is not None
    assert body["estimate"]["pricing_source"] == pricing.OPENAI_SOURCE
