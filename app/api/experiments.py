"""Controlled, side-effect-free experiment endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import db
from app.api.deps import ResponsePolicy, current_workspace, get_session, response_policy
from app.api.models import ExperimentRequest
from app.experiments import execute_counterfactual
from app.schema import ExecutionTrace

router = APIRouter(tags=["experiments"])


@router.get("/api/v1/runs/{run_id}/experiments")
def list_experiments(
    run_id: str,
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
    policy: ResponsePolicy = Depends(response_policy),
):
    record = db.get_run(session, workspace_id, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "run_not_found", "message": "Run was not found."})
    return {"items": policy.visible(db.experiments_for(session, workspace_id, run_id))}


@router.post("/api/v1/experiments/counterfactual")
def counterfactual(
    request: ExperimentRequest,
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
    policy: ResponsePolicy = Depends(response_policy),
):
    """Rerun the deterministic downstream checks against a replacement fixture.

    Only allow-listed local validators run. No imported tool, network call or
    external side effect is executed.
    """
    if not request.confirm_side_effect_free:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "confirmation_required",
                "message": "Explicitly confirm this local, side-effect-free deterministic fixture test.",
            },
        )
    record = db.get_run(session, workspace_id, request.run_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "run_not_found", "message": "Run was not found."})
    trace = ExecutionTrace.model_validate(record["trace"])
    try:
        result = execute_counterfactual(trace, request.source_span_id, request.replacement_output, request.title)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "experiment_rejected", "message": str(exc)}) from exc
    return db.save_experiment(
        session,
        workspace_id,
        request.run_id,
        request.source_span_id,
        request.title,
        policy.visible(result),
        uuid.uuid4().hex,
    )
