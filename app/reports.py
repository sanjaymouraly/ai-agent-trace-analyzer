"""Self-contained investigation report serializers (JSON, Markdown, HTML, PDF)."""

from __future__ import annotations

import html
import json
from typing import Any


def report_payload(
    run: dict[str, Any],
    analysis: dict[str, Any],
    annotations: list[dict[str, Any]],
    experiments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    trace = run["trace"]
    return {
        "report_schema": "tracelens-investigation/1.0",
        "generated_at": run.get("imported_at"),
        "run": {
            k: run.get(k)
            for k in (
                "run_id",
                "trace_id",
                "task_id",
                "task",
                "status",
                "started_at",
                "span_count",
                "token_total",
                "cost_usd",
                "duration_ms",
                "source_format",
                "provider",
                "model",
                "agent_id",
            )
        },
        "summary": analysis["summary"],
        "analysis_rules_version": analysis["rules_version"],
        "evidence_policy": analysis["evidence_policy"],
        "earliest_observed_error": analysis["earliest_observed_error"],
        "earliest_observed_divergence": analysis["earliest_observed_divergence"],
        "earliest_plausible_failure_propagation_point": analysis.get("earliest_plausible_failure_propagation_point"),
        "root_cause_hypotheses": analysis["root_cause_hypotheses"],
        "findings": analysis["findings"],
        "missing_information": analysis.get("missing_information", []),
        "suggested_experiments": analysis.get("suggested_experiments", []),
        "timeline": [
            {
                "span_id": s["span_id"],
                "parent_span_id": s.get("parent_span_id"),
                "name": s["name"],
                "action_type": s["action_type"],
                "status": s["status"],
                "start_time": s["start_time"],
                "end_time": s.get("end_time"),
                "duration_ms": s.get("duration_ms"),
                "tool_name": s.get("tool_name"),
                "exception": s.get("exception"),
            }
            for s in trace.get("spans", [])
        ],
        "dependency_graph": {
            "nodes": analysis["graph"]["nodes"],
            "edges": analysis["graph"]["edges"],
            "warnings": analysis["graph"]["warnings"],
            "critical_path": analysis["graph"]["critical_path"],
            "critical_path_supported": analysis["graph"].get("critical_path_supported"),
        },
        "annotations": annotations,
        "experiments": experiments or [],
        "reproducibility": {
            "source_format": run.get("source_format"),
            "schema_version": trace.get("schema_version"),
            "rules_version": analysis["rules_version"],
            "import_warnings": trace.get("import_warnings", []),
            "experiment_status": (
                f"{len(experiments)} controlled local experiment(s) recorded."
                if experiments
                else "No replay was run by report generation."
            ),
        },
    }


def render_report(payload: dict[str, Any], fmt: str) -> tuple[str | bytes, str]:
    fmt = fmt.lower()
    if fmt == "json":
        return json.dumps(payload, indent=2, ensure_ascii=False), "application/json; charset=utf-8"
    md = _markdown(payload)
    if fmt in {"markdown", "md"}:
        return md, "text/markdown; charset=utf-8"
    if fmt == "html":
        title = html.escape(payload["run"]["task"])
        body = "\n".join(
            f"<pre>{html.escape(line)}</pre>" if line.startswith("  ") else f"<p>{html.escape(line)}</p>" if line else "<br>"
            for line in md.splitlines()
        )
        doc = f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Investigation — {title}</title><style>body{{font:15px/1.6 system-ui,sans-serif;max-width:980px;margin:48px auto;padding:0 24px;color:#17202e}}h1,h2{{letter-spacing:-.03em}}pre{{white-space:pre-wrap;background:#f4f6fa;padding:12px;border-radius:8px}}code{{font-family:ui-monospace,monospace}}</style><body><h1>TraceLens investigation</h1>{body}</body></html>'
        return doc, "text/html; charset=utf-8"
    if fmt == "pdf":
        from app.pdf import build_pdf

        data = build_pdf(md.splitlines(), title=f"TraceLens report - {payload['run']['task']}")
        return data, "application/pdf"
    raise ValueError("format must be one of: json, markdown, html, pdf")


def _markdown(p: dict[str, Any]) -> str:
    r = p["run"]
    lines = [
        f"# Investigation report: {r['task']}",
        "",
        f"- **Run:** `{r['run_id']}`  ",
        f"- **Trace:** `{r['trace_id']}`  ",
        f"- **Status:** {r['status']}  ",
        f"- **Started:** {r['started_at']}  ",
        f"- **Spans:** {r['span_count']}  ",
        f"- **Tokens:** {r['token_total']}  ",
        f"- **Analysis rules:** `{p['analysis_rules_version']}`",
        "",
        "## Summary",
        "",
        p["summary"],
        "",
        p["evidence_policy"],
        "",
        "## Earliest observed error",
        "",
    ]
    if p["earliest_observed_error"]:
        e = p["earliest_observed_error"]
        lines += [f"- **{e['category']}** at span `{e['span_id']}` ({e.get('timestamp')})", ""]
    else:
        lines += ["No explicit span error matched the deterministic rules.", ""]
    lines += ["## Root-cause hypotheses", ""]
    if p["root_cause_hypotheses"]:
        for h in p["root_cause_hypotheses"]:
            lines += [
                f"### {h['title']}",
                "",
                f"- Status: **{h['status']}**; rule confidence: {h['confidence']}",
                f"- Evidence spans: {', '.join(f'`{s}`' for s in h.get('span_ids', [])) or 'none'}",
                "- Supporting evidence:",
            ]
            lines += [f"  - {item}" for item in h.get("supporting_evidence", [])]
            lines += ["- Contradicting evidence:"] + (
                [f"  - {item}" for item in h.get("contradicting_evidence", [])] or ["  - None recorded."]
            )
            lines += (
                [f"- Suggested experiment: {h.get('suggested_experiment', 'None')}", "- Limitations:"]
                + [f"  - {item}" for item in h.get("limitations", [])]
                + [""]
            )
    else:
        lines += ["No root-cause hypotheses were generated.", ""]
    lines += ["## Findings", "", "| Category | Label | Span(s) | Confidence | Evidence |", "|---|---|---|---:|---|"]
    for f in p["findings"]:
        evidence = "; ".join(e["value"] for e in f.get("evidence", []))
        evidence = evidence.replace("|", "\\|")
        spans = ", ".join(f"`{x}`" for x in f.get("span_ids", []))
        lines.append(f"| {f['category']} | {f['status']} | {spans} | {f['confidence']:.2f} | {evidence} |")
    lines += ["", "## Timeline", "", "| Span | Action | Type | Status | Duration ms | Parent |", "|---|---|---|---|---:|---|"]
    for s in p["timeline"]:
        action_name = s["name"].replace("|", "\\|")
        lines.append(
            f"| `{s['span_id']}` | {action_name} | {s['action_type']} | {s['status']} | {s.get('duration_ms') or ''} | `{s.get('parent_span_id') or ''}` |"
        )
    lines += ["", "## Dependencies and reconstruction warnings", ""]
    for e in p["dependency_graph"]["edges"]:
        lines.append(
            f"- `{e['source']}` → `{e['target']}` ({e['kind']}; dependency={str(e['dependency']).lower()}): {e['evidence']}"
        )
    if not p["dependency_graph"]["edges"]:
        lines.append("No explicit graph edges were reconstructed.")
    for warning in p["dependency_graph"]["warnings"]:
        lines.append(f"- Warning: {warning['message']}")
    lines += [
        "",
        "## Reproducibility",
        "",
        f"- Source format: {p['reproducibility']['source_format']}",
        f"- Schema version: {p['reproducibility']['schema_version']}",
        f"- Replay: {p['reproducibility']['experiment_status']}",
        "- Import warnings:",
    ]
    lines += [f"  - {w}" for w in p["reproducibility"]["import_warnings"]] or ["  - None."]
    lines += ["", "---", "Generated by TraceLens. Inferred hypotheses are not causal proof.", ""]
    return "\n".join(lines)
