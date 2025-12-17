"""Analysis, comparison and analytics endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import db
from app.analysis import analyze_trace
from app.api.deps import ResponsePolicy, current_workspace, get_session, response_policy
from app.api.models import CompareRequest
from app.comparison import compare_traces
from app.schema import ExecutionTrace

router = APIRouter(tags=["analysis"])


def _trace_for(session: Session, workspace_id: str, run_id: str) -> dict[str, Any]:
    record = db.get_run(session, workspace_id, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "run_not_found", "message": f"Run '{run_id}' was not found."})
    return record


@router.get("/api/v1/overview")
def overview(
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
    policy: ResponsePolicy = Depends(response_policy),
):
    return policy.visible(db.dashboard_data(session, workspace_id))


@router.get("/api/v1/tool-analytics")
def tool_analytics(
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
    policy: ResponsePolicy = Depends(response_policy),
):
    return policy.visible(db.tool_failure_analytics(session, workspace_id, limit=limit))


@router.get("/api/v1/runs/{run_id}/analysis")
def run_analysis(
    run_id: str,
    reference_run_id: str | None = None,
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
    policy: ResponsePolicy = Depends(response_policy),
):
    record = _trace_for(session, workspace_id, run_id)
    trace = ExecutionTrace.model_validate(record["trace"])
    reference = None
    if reference_run_id:
        reference_record = _trace_for(session, workspace_id, reference_run_id)
        reference = ExecutionTrace.model_validate(reference_record["trace"])
    try:
        result = analyze_trace(trace, reference)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "analysis_rejected", "message": str(exc)}) from exc
    result["annotations"] = db.annotations_for(session, workspace_id, run_id)
    result["experiments"] = db.experiments_for(session, workspace_id, run_id)
    # Persist the deterministic output so dashboards can aggregate without recomputing.
    db.replace_analysis(session, workspace_id, run_id, result["findings"], result["root_cause_hypotheses"])
    return policy.visible(result)


@router.post("/api/v1/compare")
def compare(
    request: CompareRequest,
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
    policy: ResponsePolicy = Depends(response_policy),
):
    traces = [ExecutionTrace.model_validate(_trace_for(session, workspace_id, run_id)["trace"]) for run_id in request.run_ids]
    try:
        result = compare_traces(traces)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "comparison_rejected", "message": str(exc)}) from exc
    db.audit_event(
        session,
        "comparison.performed",
        None,
        {"run_ids": request.run_ids},
        workspace_id=workspace_id,
    )
    return policy.visible(result)
