"""SDK and CLI tests."""

from __future__ import annotations

import json

import pytest

from agent_debugger.client import TraceLensClient
from agent_debugger.otel import trace_to_otlp
from agent_debugger.recorder import TraceRecorder
from app.analysis import reconstruct_graph
from app.ingestion import normalize_trace
from app.otlp import split_otlp_json


def test_sdk_records_nested_tool_call_and_roundtrips_canonical_schema():
    rec = TraceRecorder("sdk demo", run_id="sdk-run", trace_id="sdk-trace", task_id="sdk-task")
    with rec.span("plan", action_type="planning") as plan:
        plan.set_output({"steps": ["lookup"]})
        with rec.span(
            "lookup", action_type="tool", tool_name="demo.lookup", inputs={"q": "x"}, explicit_dependencies=[plan.span_id]
        ) as tool:
            tool.set_output({"answer": 42})
    rec.finish(status="success", final_answer="42", evaluation_results={"passed": True})
    raw = rec.to_dict()
    trace = normalize_trace(raw)
    assert trace.run_id == "sdk-run"
    assert len(trace.spans) == 2
    graph = reconstruct_graph(trace)
    assert any(e["kind"] == "parent_child" for e in graph["edges"])
    assert any(e["kind"] == "explicit_dependency" for e in graph["edges"])
    assert all(span.status.value == "ok" for span in trace.spans)


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/traces.db",
        "ftp://example.com",
        "http://user:pass@example.com",
        "http://example.com?token=leak",
        "http://example.com:99999",
    ],
)
def test_client_rejects_non_http_or_credential_bearing_urls(url):
    with pytest.raises(ValueError):
        TraceLensClient(base_url=url)


def test_sdk_exception_is_recorded_and_propagated():
    rec = TraceRecorder("failed sdk", run_id="sdk-fail")
    with pytest.raises(RuntimeError):
        with rec.span("unsafe attempt", action_type="tool", tool_name="fake"):
            raise RuntimeError("recorded exception")
    rec.finish(status="failure")
    trace = normalize_trace(rec.to_dict())
    assert trace.spans[0].status.value == "error"
    assert "recorded exception" in trace.spans[0].exception


def test_sdk_rejects_duplicate_span_ids():
    rec = TraceRecorder("duplicate", run_id="sdk-dup")
    with rec.span("one", span_id="same"):
        pass
    with pytest.raises(ValueError):
        rec.add_span({"span_id": "same", "name": "again", "action_type": "tool"})


def test_sdk_can_export_otlp_json():
    rec = TraceRecorder("otlp demo", run_id="sdk-otlp", trace_id="sdk-otlp-trace")
    with rec.span("tool call", action_type="tool", tool_name="demo.tool") as span:
        span.set_output({"ok": True})
    rec.finish(status="success")
    payload = trace_to_otlp(normalize_trace(rec.to_dict()))
    assert payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["spanId"]
    assert payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"] == "sdk-otlp-trace"
    # The generated payload must be ingestable again.
    records = split_otlp_json(payload)
    assert records


def test_cli_validate_and_parse_commands(tmp_path, capsys):
    from agent_debugger.cli import main

    trace = {
        "trace_id": "t",
        "run_id": "cli-run",
        "task": "CLI test",
        "status": "success",
        "spans": [],
    }
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(trace), encoding="utf-8")
    assert main(["validate", str(path)]) == 0
    output = capsys.readouterr().out
    assert "cli-run" in output
    assert json.loads(output)["valid"] is True


def test_cli_reports_missing_file(tmp_path, capsys):
    from agent_debugger.cli import main

    assert main(["validate", str(tmp_path / "missing.json")]) == 2
    assert "error:" in capsys.readouterr().err


def test_cli_compare_validates_run_count(capsys):
    from agent_debugger.cli import main

    with pytest.raises(SystemExit):
        main(["compare", "only-one"])


def test_pdf_export_preserves_the_binary_payload(tmp_path, monkeypatch):
    """A PDF export must be written byte-exact, not decoded as text."""
    from agent_debugger import cli as cli_module

    payload = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nbinary\x00bytes\n%%EOF\n"

    class FakeClient:
        def report_bytes(self, run_id: str, format: str) -> bytes:
            return payload

        def report(self, run_id: str, format: str) -> str:
            return "text report"

    monkeypatch.setattr(cli_module, "_client", lambda args: FakeClient())
    target = tmp_path / "report.pdf"
    assert cli_module.main(["export", "run-1", "--format", "pdf", "--output", str(target)]) == 0
    assert target.read_bytes() == payload

    text_target = tmp_path / "report.md"
    assert cli_module.main(["export", "run-1", "--format", "markdown", "--output", str(text_target)]) == 0
    assert text_target.read_text(encoding="utf-8") == "text report"
