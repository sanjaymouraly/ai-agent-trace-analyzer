"""Trace storage, exploration and export endpoints."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app import db
from app.api.deps import ResponsePolicy, current_workspace, get_session, response_policy
from app.api.models import AnnotationRequest
from app.reports import render_report, report_payload
from app.schema import ExecutionTrace

router = APIRouter(tags=["traces"])


def _ensure_run(session: Session, workspace_id: str, run_id: str) -> dict[str, Any]:
    record = db.get_run(session, workspace_id, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "run_not_found", "message": f"Run '{run_id}' was not found."})
    return record


@router.get("/api/v1/runs")
def list_runs(
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
    query: str = Query(default="", max_length=200),
    status: str = Query(default="", max_length=40),
    provider: str = Query(default="", max_length=120),
    task: str = Query(default="", max_length=200),
    model: str = Query(default="", max_length=120),
    agent: str = Query(default="", max_length=120),
    min_duration_ms: float | None = Query(default=None, ge=0),
    max_duration_ms: float | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    policy: ResponsePolicy = Depends(response_policy),
):
    items, total = db.list_runs(
        session,
        workspace_id,
        limit=limit,
        offset=offset,
        query=query,
        status=status,
        provider=provider,
        task=task,
        model=model,
        agent=agent,
        min_duration_ms=min_duration_ms,
        max_duration_ms=max_duration_ms,
    )
    return {
        "items": policy.visible(items),
        "total": total,
        "limit": limit,
        "offset": offset,
        "workspace_id": workspace_id,
    }


@router.get("/api/v1/runs/{run_id}")
def run_detail(
    run_id: str,
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
    policy: ResponsePolicy = Depends(response_policy),
):
    item = _ensure_run(session, workspace_id, run_id)
    item["annotations"] = db.annotations_for(session, workspace_id, run_id)
    item["experiments"] = db.experiments_for(session, workspace_id, run_id)
    item["findings"] = db.findings_for(session, workspace_id, run_id)
    item["hypotheses"] = db.hypotheses_for(session, workspace_id, run_id)
    return policy.visible(item)


@router.get("/api/v1/runs/{run_id}/timeline")
def run_timeline(
    run_id: str,
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
    policy: ResponsePolicy = Depends(response_policy),
):
    """Flat, chronologically ordered span list for the timeline view."""
    item = _ensure_run(session, workspace_id, run_id)
    spans = item["trace"].get("spans", [])
    ordered = sorted(spans, key=lambda s: (s.get("start_time") or "", s.get("span_id") or ""))
    return {
        "run_id": run_id,
        "started_at": item["started_at"],
        "duration_ms": item["duration_ms"],
        "spans": policy.visible(ordered),
    }


@router.get("/api/v1/runs/{run_id}/graph")
def run_graph(
    run_id: str,
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
    policy: ResponsePolicy = Depends(response_policy),
):
    """Dependency reconstruction for the interactive graph view."""
    from app.analysis import reconstruct_graph

    item = _ensure_run(session, workspace_id, run_id)
    trace = ExecutionTrace.model_validate(item["trace"])
    return policy.visible(reconstruct_graph(trace))


@router.post("/api/v1/runs/{run_id}/annotations")
def annotate(
    run_id: str,
    request: AnnotationRequest,
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
):
    _ensure_run(session, workspace_id, run_id)
    return db.add_annotation(session, workspace_id, run_id, request.model_dump())


@router.delete("/api/v1/runs/{run_id}")
def remove_run(
    run_id: str,
    confirm: bool = False,
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
):
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "confirmation_required",
                "message": "Pass confirm=true to permanently delete this run and its annotations.",
            },
        )
    if not db.delete_run(session, workspace_id, run_id):
        raise HTTPException(status_code=404, detail={"code": "run_not_found", "message": "Run was not found."})
    return {"deleted": True, "run_id": run_id}


@router.get("/api/v1/runs/{run_id}/report")
def export_report(
    run_id: str,
    format: str = Query(default="markdown", pattern="^(json|markdown|md|html|pdf)$"),
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
    policy: ResponsePolicy = Depends(response_policy),
):
    from app.analysis import analyze_trace

    record = _ensure_run(session, workspace_id, run_id)
    analysis = analyze_trace(ExecutionTrace.model_validate(record["trace"]))
    annotations = db.annotations_for(session, workspace_id, run_id)
    experiments = db.experiments_for(session, workspace_id, run_id)
    payload = report_payload(record, analysis, annotations, experiments)
    text, media_type = render_report(policy.visible(payload), format)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", run_id)[:80]
    extension = "md" if format in {"md", "markdown"} else format
    db.audit_event(session, "report.exported", run_id, {"format": format}, workspace_id=workspace_id)
    return Response(
        content=text,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="tracelens-{safe_id}.{extension}"'},
    )
