"""TraceLens command-line importer, inspector and analyzer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agent_debugger.client import TraceLensClient, TraceLensError


def _dump(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _client(args: argparse.Namespace) -> TraceLensClient:
    return TraceLensClient(
        base_url=getattr(args, "url", None),
        token=getattr(args, "token", None),
        timeout=getattr(args, "timeout", 20),
    )


def cmd_import(args: argparse.Namespace) -> int:
    result = _client(args).upload_file(args.file)
    _dump(result)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from app.ingestion import parse_document

    path = Path(args.file)
    records = parse_document(path.read_bytes(), path.name)
    result = {
        "valid": True,
        "record_count": len(records),
        "runs": [
            {
                "run_id": trace.run_id,
                "trace_id": trace.trace_id,
                "task": trace.task,
                "status": trace.status.value,
                "span_count": len(trace.spans),
                "warnings": trace.import_warnings,
            }
            for trace, _ in records
        ],
    }
    _dump(result)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    result = _client(args).list_runs(
        query=args.query,
        status=args.status,
        provider=args.provider,
        limit=args.limit,
        offset=args.offset,
    )
    if args.json:
        _dump(result)
    else:
        print(f"{result['total']} total run(s)")
        for run in result["items"]:
            print(f"{run['status']:9} {run['run_id']:32} {run['task']} ({run['span_count']} spans)")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    _dump(_client(args).get_run(args.run_id))
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    if Path(args.input).is_file():
        from app.analysis import analyze_trace
        from app.ingestion import parse_document

        path = Path(args.input)
        records = parse_document(path.read_bytes(), path.name)
        if len(records) != 1:
            raise ValueError("Local analyze expects a file containing exactly one run. Use import for batches.")
        result = analyze_trace(records[0][0])
    else:
        result = _client(args).analyze(args.input)
    if args.format == "json":
        _dump(result)
    else:
        print(result.get("summary", ""))
        print(f"Outcome: {result.get('run_outcome')} · rules {result.get('rules_version')}")
        earliest = result.get("earliest_observed_error")
        if earliest:
            print(f"Earliest observed error: {earliest['category']} at span {earliest.get('span_id') or '(run level)'}")
        propagation = result.get("earliest_plausible_failure_propagation_point")
        if propagation:
            affected = ", ".join(propagation.get("affected_span_ids", []))
            print(f"Earliest propagation point: {propagation.get('span_id')} → {affected or 'no downstream symptom'}")
        for hypothesis in result.get("root_cause_hypotheses", []):
            print(f"- [{hypothesis['status']}] {hypothesis['title']} (evidence strength {hypothesis['confidence']:.2f})")
            for evidence in hypothesis.get("supporting_evidence", []):
                print(f"    + {evidence}")
            for evidence in hypothesis.get("contradicting_evidence", []):
                print(f"    - {evidence}")
            for limitation in hypothesis.get("limitations", []):
                print(f"    ! {limitation}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    _dump(_client(args).compare(args.run_ids))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    out = Path(args.output)
    if args.format == "pdf":
        # Binary payloads are written as bytes; decoding them as text corrupts the file.
        out.write_bytes(_client(args).report_bytes(args.run_id, "pdf"))
    else:
        out.write_text(_client(args).report(args.run_id, args.format), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    _dump(_client(args).health())
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    _dump(_client(args).tool_analytics())
    return 0


def cmd_overview(args: argparse.Namespace) -> int:
    _dump(_client(args).overview())
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    _dump(_client(args).llm_status())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracelens",
        description="Import, inspect, analyze and compare AI agent execution traces.",
    )
    parser.add_argument("--url", help="TraceLens URL (default: TRACELENS_URL or http://127.0.0.1:8000)")
    parser.add_argument("--token", help="Bearer token (default: TRACELENS_TOKEN; avoid shell history on shared systems)")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout in seconds")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import", help="Upload a JSON or JSONL file through the validated ingestion API")
    p.add_argument("file")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("validate", help="Validate a local JSON/JSONL file without storing it")
    p.add_argument("file")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("list", help="List runs in the server index")
    p.add_argument("--query", default="")
    p.add_argument("--status", default="")
    p.add_argument("--provider", default="")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("inspect", help="Fetch a stored run record")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("analyze", help="Analyze a local trace file or a stored run ID")
    p.add_argument("input")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("compare", help="Compare 2–8 stored run IDs")
    p.add_argument("run_ids", nargs="+", help="Stored run IDs")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("export", help="Export a server-side investigation report")
    p.add_argument("run_id")
    p.add_argument("--format", choices=["json", "markdown", "html", "pdf"], default="markdown")
    p.add_argument("--output", required=True)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("health", help="Check API connectivity")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("tools", help="Show tool failure analytics")
    p.set_defaults(func=cmd_tools)

    p = sub.add_parser("overview", help="Show workspace overview metrics")
    p.set_defaults(func=cmd_overview)

    p = sub.add_parser("ai-status", help="Show the optional AI provider status")
    p.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "compare" and not 2 <= len(args.run_ids) <= 8:
            parser.error("compare requires 2–8 run IDs")
        return args.func(args)
    except (TraceLensError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
