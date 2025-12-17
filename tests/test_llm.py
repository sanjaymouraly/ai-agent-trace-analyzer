import json

import httpx
import pytest

from app import db
from app.llm import (
    MAX_PROVIDER_RESPONSE_BYTES,
    ProviderConfig,
    ProviderError,
    _provider_url,
    _request_json,
    build_evidence_packet,
    complete,
    discover_models,
    public_status,
    validate_citations,
)


@pytest.fixture()
def session():
    db.init_db()
    with db.session_scope() as db_session:
        yield db_session


def test_public_provider_status_never_exposes_api_keys(monkeypatch, session):
    secret = "never-return-this-provider-secret"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setenv("OPENAI_MODEL", "model-test")
    status = public_status(session, "default")
    encoded = json.dumps(status)
    assert status["provider"] == "disabled"
    assert status["providers"][1]["configured"] is True
    assert secret not in encoded
    assert status["daily_limit"] >= 1
    assert status["requires_external_consent"] is True


def test_provider_selection_and_completion_are_provider_specific(monkeypatch):
    monkeypatch.setattr("app.llm._provider_url", lambda config: (config.base_url, False))
    captured = []

    def fake_request(method, url, headers, body=None):
        captured.append((method, url, headers, body))
        return {
            "choices": [{"message": {"content": "Evidence is limited [span-a]."}}],
            "usage": {"prompt_tokens": 17, "completion_tokens": 8},
        }

    monkeypatch.setattr("app.llm._request_json", fake_request)
    config = ProviderConfig("openai", "OpenAI", "openai", "https://api.openai.com/v1", "not-a-real-key", "test-model", True, True)
    result = complete(config, "system", "user")
    method, url, headers, body = captured[0]
    assert method == "POST" and url.endswith("/chat/completions")
    assert headers["Authorization"] == "Bearer not-a-real-key"
    assert body["model"] == "test-model" and body["messages"][0]["role"] == "system"
    assert result["answer"] == "Evidence is limited [span-a]."
    assert result["usage"] == {"input_tokens": 17, "output_tokens": 8, "total_tokens": 25}
    assert "not-a-real-key" not in json.dumps(result)


def test_provider_adapters_use_expected_http_routes_headers_and_payloads(monkeypatch):
    calls = []

    def handler(request):
        payload = json.loads(request.content) if request.method == "POST" else None
        calls.append((request.method, request.url.path, request.headers, payload))
        if request.method == "GET":
            response = (
                {"models": [{"name": "ollama:local"}]}
                if request.url.path.endswith("/api/tags")
                else {"data": [{"id": "model-a"}, {"id": "model-b"}]}
            )
        elif request.url.path.endswith("/messages"):
            response = {
                "content": [{"type": "text", "text": "Observed [span-1]."}],
                "usage": {"input_tokens": 2, "output_tokens": 3},
            }
        elif request.url.path.endswith("/api/chat"):
            response = {"message": {"content": "Observed [span-1]."}, "prompt_eval_count": 2, "eval_count": 3}
        else:
            response = {
                "choices": [{"message": {"content": "Observed [span-1]."}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            }
        return httpx.Response(200, json=response)

    real_client = httpx.Client
    monkeypatch.setattr("app.llm._provider_url", lambda config: (config.base_url, False))
    monkeypatch.setattr(
        "app.llm.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    configs = [
        ProviderConfig("openai", "OpenAI", "openai", "https://api.openai.com/v1", "test-openai-key", "gpt-test", True, True),
        ProviderConfig(
            "anthropic", "Anthropic", "anthropic", "https://api.anthropic.com/v1", "test-anthropic-key", "claude-test", True, True
        ),
        ProviderConfig(
            "openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1", "test-router-key", "router-test", True, True
        ),
        ProviderConfig("ollama", "Ollama", "ollama", "http://127.0.0.1:11434", "", "local-test", True, False),
        ProviderConfig(
            "openai-compatible",
            "Custom",
            "openai",
            "https://custom.example/api/v1",
            "test-custom-key",
            "custom-test",
            True,
            False,
        ),
    ]
    for config in configs:
        assert complete(config, "system message", "user message")["answer"] == "Observed [span-1]."
        assert discover_models(config) == (["ollama:local"] if config.key == "ollama" else ["model-a", "model-b"])

    post_calls = [call for call in calls if call[0] == "POST"]
    expected_paths = [
        "/v1/chat/completions",
        "/v1/messages",
        "/api/v1/chat/completions",
        "/api/chat",
        "/api/v1/chat/completions",
    ]
    assert [call[1] for call in post_calls] == expected_paths
    for config, (_, _, headers, body) in zip(configs, post_calls, strict=True):
        if config.key == "anthropic":
            assert headers.get("x-api-key") == config.api_key
            assert headers.get("anthropic-version") == "2023-06-01"
            assert body["max_tokens"] >= 64 and body["system"] == "system message"
        elif config.key == "ollama":
            assert headers.get("authorization") is None
            assert body["options"]["num_predict"] >= 64
        else:
            assert headers.get("authorization") == f"Bearer {config.api_key}"
            token_field = "max_completion_tokens" if config.key == "openai" else "max_tokens"
            assert body[token_field] >= 64
    get_paths = [call[1] for call in calls if call[0] == "GET"]
    assert get_paths == ["/v1/models", "/v1/models", "/api/v1/models", "/api/tags", "/api/v1/models"]


def test_anthropic_and_ollama_response_parsing(monkeypatch):
    monkeypatch.setattr("app.llm._provider_url", lambda config: (config.base_url, False))
    responses = iter(
        [
            {"content": [{"type": "text", "text": "Claude answer."}], "usage": {"input_tokens": 4, "output_tokens": 5}},
            {"message": {"content": "Ollama answer."}, "prompt_eval_count": 7, "eval_count": 3},
        ]
    )
    monkeypatch.setattr("app.llm._request_json", lambda *args, **kwargs: next(responses))
    anthropic = ProviderConfig(
        "anthropic", "Anthropic", "anthropic", "https://api.anthropic.com/v1", "secret", "claude-test", True, True
    )
    ollama = ProviderConfig("ollama", "Ollama", "ollama", "http://127.0.0.1:11434", "", "local-test", True, False)
    assert complete(anthropic, "system", "user")["usage"]["total_tokens"] == 9
    assert complete(ollama, "system", "user")["answer"] == "Ollama answer."


def test_evidence_packet_is_bounded_and_redacts_sensitive_values():
    trace = {
        "run_id": "safe-run",
        "trace_id": "safe-trace",
        "task": "Check a record",
        "status": "failure",
        "spans": [
            {
                "span_id": "span-a",
                "name": "lookup",
                "status": "error",
                "action_type": "tool",
                "inputs": {"api_key": "trace-secret-value", "query": "safe query"},
                "outputs": {"Authorization": "Bearer abc123"},
                "exception": "permission denied",
            }
        ],
    }
    analysis = {
        "earliest_observed_error": {"span_id": "span-a", "category": "authentication_or_permission_failure"},
        "findings": [],
        "root_cause_hypotheses": [],
        "graph": {"critical_path": {"span_ids": ["span-a"]}},
    }
    packet, span_ids = build_evidence_packet(trace, analysis, "What failed?")
    assert len(packet) <= 20_000
    assert "trace-secret-value" not in packet
    assert "abc123" not in packet
    assert "[REDACTED]" in packet
    assert span_ids == ["span-a"]


def test_citation_check_distinguishes_known_and_unknown_ids():
    result = validate_citations("Observed [span-a] but not [invented-span].", ["span-a"])
    assert result["valid_span_ids"] == ["span-a"]
    assert result["unrecognized_ids"] == ["invented-span"]


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [(302, "provider_redirect_blocked"), (400, "provider_rejected_request")],
)
def test_provider_http_errors_do_not_echo_response_bodies_or_credentials(monkeypatch, status_code, expected_code):
    secret = "provider-error-body-secret"

    class FakeResponse:
        is_success = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __init__(self):
            self.status_code = status_code

        def iter_bytes(self):
            yield json.dumps({"error": secret}).encode()

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("app.llm.httpx.Client", lambda **kwargs: FakeClient())
    with pytest.raises(ProviderError) as error:
        _request_json("GET", "https://provider.example/models", {"Authorization": f"Bearer {secret}"})
    assert error.value.code == expected_code
    assert secret not in str(error.value)


def test_provider_response_bytes_are_bounded(monkeypatch):
    def install_stream(body: bytes) -> None:
        class FakeResponse:
            status_code = 200
            is_success = True

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def iter_bytes(self):
                yield body

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def stream(self, *args, **kwargs):
                return FakeResponse()

        monkeypatch.setattr("app.llm.httpx.Client", lambda **kwargs: FakeClient())

    install_stream(b'{"ok":true}')
    assert _request_json("GET", "https://provider.example/models", {}) == {"ok": True}
    install_stream(b"x" * (MAX_PROVIDER_RESPONSE_BYTES + 1))
    with pytest.raises(ProviderError) as error:
        _request_json("GET", "https://provider.example/models", {})
    assert error.value.code == "provider_response_too_large"


def test_custom_provider_blocks_private_addresses_by_default(monkeypatch):
    monkeypatch.delenv("APP_LLM_ALLOW_PRIVATE_BASE_URL", raising=False)
    private = ProviderConfig("openai-compatible", "Custom", "openai", "https://127.0.0.1/v1", "", "model", True, False)
    with pytest.raises(ProviderError) as error:
        _provider_url(private)
    assert error.value.code == "provider_url_rejected"


def test_custom_provider_requires_https_without_explicit_private_opt_in(monkeypatch):
    monkeypatch.delenv("APP_LLM_ALLOW_PRIVATE_BASE_URL", raising=False)
    private_http = ProviderConfig("openai-compatible", "Custom", "openai", "http://example.com/v1", "", "model", True, False)
    with pytest.raises(ProviderError) as error:
        _provider_url(private_http)
    assert error.value.code == "provider_url_rejected"
