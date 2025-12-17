import json

import pytest

from app.ingestion import normalize_trace, parse_document, redact


def test_canonical_roundtrip_preserves_unknown_attributes_and_duplicate_detection():
    raw = {
        "trace_id": "t1",
        "run_id": "r1",
        "task": "demo",
        "status": "success",
        "spans": [
            {
                "span_id": "s1",
                "name": "search",
                "action_type": "tool",
                "status": "ok",
                "start_time": "2026-01-01T00:00:00Z",
                "end_time": "2026-01-01T00:00:01Z",
                "tool_name": "search",
                "outputs": {"ids": [1]},
                "attributes": {"vendor.custom": "kept"},
            }
        ],
    }
    trace = normalize_trace(raw)
    assert trace.run_id == "r1"
    assert trace.spans[0].attributes["vendor.custom"] == "kept"
    assert trace.spans[0].outputs == {"ids": [1]}
    duplicate = {**raw, "spans": [raw["spans"][0], raw["spans"][0]]}
    with pytest.raises(ValueError, match="unique"):
        normalize_trace(duplicate)


def test_json_and_jsonl_batch_validation_and_duplicates():
    a = {"trace_id": "t-a", "run_id": "r-a", "spans": []}
    b = {"trace_id": "t-b", "run_id": "r-b", "spans": []}
    records = parse_document(json.dumps([a, b]).encode(), "batch.json")
    assert [x[0].run_id for x in records] == ["r-a", "r-b"]
    lines = (json.dumps(a) + "\n" + json.dumps(b) + "\n").encode()
    assert len(parse_document(lines, "batch.jsonl")) == 2
    with pytest.raises(ValueError, match="Duplicate run_id"):
        parse_document((json.dumps(a) + "\n" + json.dumps(a)).encode(), "dup.jsonl")
    with pytest.raises(ValueError, match="line 2"):
        parse_document((json.dumps(a) + "\nnot json").encode(), "bad.jsonl")


def test_otlp_json_adapter_maps_parent_ids_attributes_and_exception():
    payload = {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "agent-worker"}}]},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "abc",
                                "spanId": "root1",
                                "name": "agent",
                                "startTimeUnixNano": "1760000000000000000",
                                "endTimeUnixNano": "1760000000100000000",
                                "attributes": [{"key": "openinference.span.kind", "value": {"stringValue": "CHAIN"}}],
                            },
                            {
                                "traceId": "abc",
                                "spanId": "tool1",
                                "parentSpanId": "root1",
                                "name": "fetch",
                                "startTimeUnixNano": "1760000000200000000",
                                "endTimeUnixNano": "1760000000300000000",
                                "status": {"code": 2},
                                "attributes": [
                                    {"key": "tool.name", "value": {"stringValue": "fetch"}},
                                    {"key": "exception.message", "value": {"stringValue": "403 forbidden"}},
                                ],
                            },
                        ]
                    }
                ],
            }
        ]
    }
    trace = normalize_trace(payload, source_format="json")
    assert trace.trace_id == "abc"
    assert trace.spans[0].action_type.value == "agent"
    assert trace.spans[1].parent_span_id == "root1"
    assert trace.spans[1].status.value == "error"
    assert trace.spans[1].tool_name == "fetch"
    assert trace.spans[1].exception == "403 forbidden"


def test_langchain_nested_run_adapter():
    raw = {
        "id": "root",
        "trace_id": "trace",
        "name": "planner",
        "run_type": "chain",
        "inputs": {},
        "outputs": {},
        "child_runs": [{"id": "child", "name": "lookup", "run_type": "tool", "inputs": {"q": "x"}, "outputs": {"v": 1}}],
    }
    trace = normalize_trace(raw)
    assert len(trace.spans) == 2
    child = next(s for s in trace.spans if s.span_id == "child")
    assert child.parent_span_id == "root"
    assert child.action_type.value == "tool"
    assert trace.provenance["mapping"] == "langchain-run"


def test_bad_times_and_malformed_top_level_fail_gracefully():
    with pytest.raises(ValueError, match="must be a JSON object"):
        normalize_trace([1, 2])
    with pytest.raises(ValueError, match="Invalid JSON"):
        parse_document(b"{oops", "bad.json")
    with pytest.raises(ValueError, match="UTF-8"):
        parse_document(b"\xff", "bad.json")


def test_redaction_is_recursive_non_mutating_and_does_not_mask_safe_values():
    source = {
        "password": "abc",
        "headers": {"authorization": "Bearer token", "content-type": "json"},
        "note": "Authorization: safe",
    }
    redacted = redact(source)
    assert redacted["password"] == "[REDACTED]"
    assert redacted["headers"]["authorization"] == "[REDACTED]"
    assert redacted["headers"]["content-type"] == "json"
    assert source["password"] == "abc"
    assert redact(source, enabled=False) is source
    message = "provider error: api_key=sk-abcdefghijklmnopqrstuvwxyz0123456789 Bearer secret-token-123"
    safe = redact(message)
    assert "abcdefghijklmnopqrstuvwxyz" not in safe
    assert "secret-token-123" not in safe
    assert "[REDACTED]" in safe


def test_shipped_example_fixtures_are_ingestible():
    """Every fixture in examples/traces must load through the real pipeline."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "examples" / "traces"
    files = sorted(root.glob("*.json")) + sorted(root.glob("*.jsonl"))
    assert files, "the example fixtures must be present"
    for path in files:
        records = parse_document(path.read_bytes(), path.name)
        assert records, f"{path.name} produced no records"
        for trace, _ in records:
            assert trace.run_id
            assert trace.spans, f"{path.name}: {trace.run_id} has no spans"


def test_otlp_example_fixture_maps_back_to_the_canonical_actions():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "examples" / "traces"
    records = parse_document((root / "otlp-demo.json").read_bytes(), "otlp-demo.json")
    assert len(records) == 1
    trace = records[0][0]
    assert trace.provenance["mapping"] == "otlp-json"
    assert {span.action_type.value for span in trace.spans} >= {"tool", "model"}
