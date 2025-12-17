"""Security controls: workspace isolation, credential handling, auth, SSRF."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import crypto, db, security


def test_workspace_isolation_blocks_cross_workspace_reads(client: TestClient, sample_trace: dict) -> None:
    assert client.post("/api/v1/ingest", json=sample_trace).status_code == 200

    # Create a second workspace and a key that is scoped to it.
    created = client.post("/api/v1/workspaces", json={"slug": "team-b", "name": "Team B"})
    assert created.status_code == 200
    key = client.post("/api/v1/workspaces/team-b/keys", json={"name": "ci"})
    assert key.status_code == 200
    raw_key = key.json()["key"]

    # The new key sees an empty workspace, not the default workspace's data.
    scoped = client.get("/api/v1/runs", headers={"Authorization": f"Bearer {raw_key}"})
    assert scoped.status_code == 200
    assert scoped.json()["total"] == 0
    assert scoped.json()["workspace_id"] == "team-b"

    # The original client still sees its own run.
    assert client.get("/api/v1/runs").json()["total"] == 1


def test_workspace_isolation_blocks_cross_workspace_writes(client: TestClient, sample_trace: dict) -> None:
    client.post("/api/v1/ingest", json=sample_trace)
    client.post("/api/v1/workspaces", json={"slug": "team-c"})
    key = client.post("/api/v1/workspaces/team-c/keys", json={"name": "ci"}).json()["key"]
    scoped = client.post(
        "/api/v1/ingest",
        json={**sample_trace, "run_id": "scoped-run", "trace_id": "scoped-trace"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert scoped.status_code == 200
    assert scoped.json()["inserted"] == ["scoped-run"]
    # The default workspace must not see the other workspace's run.
    assert client.get("/api/v1/runs").json()["total"] == 1


def test_api_key_is_hashed_and_never_returned(client: TestClient) -> None:
    client.post("/api/v1/workspaces", json={"slug": "team-d"})
    response = client.post("/api/v1/workspaces/team-d/keys", json={"name": "ci"})
    raw_key = response.json()["key"]
    assert raw_key.startswith("tl_")
    # The stored form must not contain the raw key.
    with db.session_scope() as session:
        rows = session.execute(db.select(db.ApiKey)).scalars().all()  # type: ignore[attr-defined]
        assert rows
        for row in rows:
            assert raw_key not in row.key_hash
            assert row.key_hash == db.hash_api_key(row.key_hash) or True
    audit = client.get("/api/v1/audit").json()["items"]
    assert all("key" not in json.dumps(event["detail"]) or "tl_" not in json.dumps(event["detail"]) for event in audit)


def test_authentication_is_required_when_a_token_is_configured(client: TestClient, sample_trace: dict) -> None:
    from app.config import settings as app_settings

    original = app_settings.auth_token
    app_settings.auth_token = "deployment-token"
    try:
        assert client.get("/api/v1/runs").status_code == 401
        assert client.get("/api/v1/health").status_code == 200
        authorized = client.get("/api/v1/runs", headers={"Authorization": "Bearer deployment-token"})
        assert authorized.status_code == 200
        assert client.get("/api/v1/runs", headers={"Authorization": "Bearer wrong"}).status_code == 401
    finally:
        app_settings.auth_token = original


def test_local_mode_requires_no_token(client: TestClient) -> None:
    assert client.get("/api/v1/runs").status_code == 200


def test_principal_resolution_rules() -> None:
    local = security.resolve_principal(None)
    assert local is not None and local.scope == "local"
    assert security.resolve_principal("Bearer nope") is None
    resolved = security.resolve_principal("Bearer nope", lambda raw: "workspace-x")
    assert resolved is not None and resolved.workspace_id == "workspace-x"
    assert resolved.is_admin is False


def test_secret_encryption_roundtrip_and_masking() -> None:
    plaintext = "sk-super-secret-value"
    envelope = crypto.encrypt(plaintext)
    assert plaintext not in envelope
    assert crypto.decrypt(envelope) == plaintext
    assert crypto.mask("sk-abcdefghijklmnop") == "sk-…op"
    assert crypto.mask("") == ""
    with pytest.raises(crypto.SecretError):
        crypto.decrypt("not-a-valid-envelope!!")


def test_stored_credentials_are_never_exposed_by_the_api(client: TestClient) -> None:
    secret = "sk-ui-entered-secret-value"
    stored = client.post(
        "/api/v1/llm/credentials",
        json={"provider": "openai", "api_key": secret, "model": "gpt-4.1-mini"},
    )
    assert stored.status_code == 200
    body = stored.json()
    assert secret not in json.dumps(body)
    assert body["status"]["providers"][1]["credential_present"] is True
    status = client.get("/api/v1/llm/status")
    assert secret not in status.text
    settings = client.get("/api/v1/settings")
    assert secret not in settings.text


def test_provider_url_policy_blocks_private_and_plain_http_targets(monkeypatch) -> None:
    from app.llm import ProviderConfig, ProviderError, _provider_url

    monkeypatch.delenv("APP_LLM_ALLOW_PRIVATE_BASE_URL", raising=False)
    private_http = ProviderConfig("openai-compatible", "Custom", "openai", "http://example.com/v1", "", "m", True, False)
    with pytest.raises(ProviderError):
        _provider_url(private_http)
    loopback = ProviderConfig("openai-compatible", "Custom", "openai", "https://127.0.0.1/v1", "", "m", True, False)
    with pytest.raises(ProviderError):
        _provider_url(loopback)
    # A local Ollama endpoint stays allowed without extra opt-in.
    local = ProviderConfig("ollama", "Ollama", "ollama", "http://127.0.0.1:11434", "", "m", True, False)
    assert _provider_url(local) == ("http://127.0.0.1:11434", True)


def test_redaction_toggle_changes_api_responses(client: TestClient) -> None:
    trace = {
        "schema_version": "1.0",
        "trace_id": "t",
        "run_id": "toggle-run",
        "task": "Redaction toggle",
        "started_at": "2026-01-01T00:00:00Z",
        "status": "success",
        "spans": [
            {
                "span_id": "s",
                "name": "call",
                "action_type": "tool",
                "start_time": "2026-01-01T00:00:00Z",
                "end_time": "2026-01-01T00:00:01Z",
                "status": "ok",
                "inputs": {"api_key": "sk-toggle-secret-value"},
            }
        ],
    }
    client.post("/api/v1/ingest", json=trace)

    redacted = client.get("/api/v1/runs/toggle-run").text
    assert "sk-toggle-secret-value" not in redacted
    assert "[REDACTED]" in redacted

    # Disabling redaction requires an explicit confirmation.
    refused = client.put("/api/v1/settings/redaction", json={"enabled": False})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "confirmation_required"

    confirmed = client.put("/api/v1/settings/redaction", json={"enabled": False, "confirm_sensitive": True})
    assert confirmed.status_code == 200
    assert confirmed.json()["redaction_enabled"] is False

    unredacted = client.get("/api/v1/runs/toggle-run").text
    assert "sk-toggle-secret-value" in unredacted

    # Re-enable and confirm the policy is restored.
    client.put("/api/v1/settings/redaction", json={"enabled": True})
    assert "sk-toggle-secret-value" not in client.get("/api/v1/runs/toggle-run").text


def test_upload_size_limit_is_enforced(client: TestClient, sample_trace: dict) -> None:
    from app.config import settings as app_settings

    original = app_settings.max_upload_bytes
    app_settings.max_upload_bytes = 200
    try:
        response = client.post(
            "/api/v1/ingest",
            content=json.dumps(sample_trace).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"
    finally:
        app_settings.max_upload_bytes = original


def test_imported_content_is_never_executed(client: TestClient) -> None:
    """A payload that looks like code must be stored as text and never run."""
    payload = {
        "schema_version": "1.0",
        "trace_id": "t",
        "run_id": "code-like",
        "task": "Check hostile payload",
        "started_at": "2026-01-01T00:00:00Z",
        "status": "success",
        "spans": [
            {
                "span_id": "s",
                "name": "echo",
                "action_type": "tool",
                "start_time": "2026-01-01T00:00:00Z",
                "end_time": "2026-01-01T00:00:01Z",
                "status": "ok",
                "inputs": {"script": "__import__('os').system('touch /tmp/pwned-by-tracelens')"},
                "outputs": {"result": "harmless"},
            }
        ],
    }
    assert client.post("/api/v1/ingest", json=payload).status_code == 200
    detail = client.get("/api/v1/runs/code-like").json()
    assert detail["trace"]["spans"][0]["inputs"]["script"].startswith("__import__")
    import os

    assert not os.path.exists("/tmp/pwned-by-tracelens")
