"""Settings, audit, workspace administration and health endpoints."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db
from app.api.deps import ResponsePolicy, current_workspace, get_principal, get_session, response_policy
from app.api.models import ApiKeyCreateRequest, RedactionRequest, WorkspaceCreateRequest
from app.config import settings
from app.security import Principal

router = APIRouter(tags=["settings"])


@router.get("/api/v1/health")
def health():
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "mode": "local-single-workspace" if not settings.auth_token else "authenticated-multi-workspace",
        "ai_enabled": False,
    }


@router.get("/api/v1/ready")
def readiness(session: Session = Depends(get_session)):
    try:
        session.execute(select(1)).scalar_one()
    except Exception as exc:  # pragma: no cover - exercised when the database is unavailable
        raise HTTPException(status_code=503, detail={"code": "not_ready", "message": "Database is not ready."}) from exc
    return {"status": "ready", "database": "ok", "app_version": settings.app_version}


@router.get("/api/v1/settings")
def get_settings(
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
    principal: Principal = Depends(get_principal),
    policy: ResponsePolicy = Depends(response_policy),
):
    from app.api.ai import _status

    ai = _status(session, workspace_id)
    if ai["ready"]:
        ai_status = f"{ai['provider_label']} is configured for consent-gated assistance."
    elif ai["provider"] == "disabled":
        ai_status = "AI assistance is disabled. Deterministic trace analysis remains fully available offline."
    else:
        ai_status = f"{ai['provider_label']} is selected but needs credentials or a model configuration."
    redaction = db.get_setting(session, workspace_id, "redaction_enabled", settings.default_redaction)
    return policy.visible(
        {
            "app_name": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "mode": "local-single-workspace" if not settings.auth_token else "authenticated-multi-workspace",
            "authentication_enabled": bool(settings.auth_token),
            "workspace_id": workspace_id,
            "redaction_enabled": bool(redaction),
            "default_redaction": settings.default_redaction,
            "ai_enabled": ai["ready"],
            "ai_status": ai_status,
            "llm": ai,
            "api_base": "/api/v1",
            "retention_days": settings.trace_retention_days,
            "retention": (
                f"Runs imported more than {settings.trace_retention_days} day(s) ago are deleted at startup and hourly. "
                "Set TRACE_RETENTION_DAYS=0 to disable."
                if settings.trace_retention_days > 0
                else "Automatic retention is disabled (TRACE_RETENTION_DAYS=0); delete runs explicitly or back up the database."
            ),
            "limits": {
                "max_upload_bytes": settings.max_upload_bytes,
                "max_spans_per_run": settings.max_spans_per_run,
                "max_batch_records": settings.max_batch_records,
                "rate_limit_per_minute": settings.rate_limit_per_minute,
            },
            "storage": {
                "backend": "postgresql" if not settings.is_sqlite else "sqlite",
                "redis_queue": bool(settings.redis_url),
            },
        }
    )


@router.put("/api/v1/settings/redaction")
def update_redaction(
    request: RedactionRequest,
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
    principal: Principal = Depends(get_principal),
):
    if not request.enabled and not request.confirm_sensitive:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "confirmation_required",
                "message": "Set confirm_sensitive=true to expose unredacted payload fields in API responses.",
            },
        )
    if not request.enabled and not principal.is_admin:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Only an administrator may disable payload redaction."},
        )
    db.set_setting(session, workspace_id, "redaction_enabled", request.enabled)
    return {
        "redaction_enabled": request.enabled,
        "warning": None if request.enabled else "Sensitive trace payloads may now appear in API responses and exports.",
    }


@router.get("/api/v1/audit")
def audit(
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
    policy: ResponsePolicy = Depends(response_policy),
):
    return policy.visible({"items": db.audit_events(session, workspace_id, limit)})


@router.get("/api/v1/workspaces")
def list_workspaces(
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Administrator access required."})
    return {"items": db.list_workspaces(session), "current": principal.workspace_id}


@router.post("/api/v1/workspaces")
def create_workspace(
    request: WorkspaceCreateRequest,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Administrator access required."})
    if not settings.allow_workspace_creation:
        raise HTTPException(
            status_code=403,
            detail={"code": "workspace_creation_disabled", "message": "Workspace creation is disabled on this deployment."},
        )
    workspace = db.ensure_workspace(session, request.slug, request.name)
    db.audit_event(session, "workspace.created", None, {"slug": request.slug}, workspace_id=request.slug, actor=principal.actor)
    return {"id": workspace.id, "slug": workspace.slug, "name": workspace.name}


@router.post("/api/v1/workspaces/{slug}/keys")
def create_workspace_key(
    slug: str,
    request: ApiKeyCreateRequest,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    """Create a workspace-scoped API key. The raw key is returned exactly once."""
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Administrator access required."})
    db.ensure_workspace(session, slug)
    raw_key = "tl_" + secrets.token_urlsafe(32)
    record = db.create_api_key(session, slug, request.name, raw_key)
    db.audit_event(
        session, "apikey.created", None, {"workspace": slug, "name": request.name}, workspace_id=slug, actor=principal.actor
    )
    return {
        "id": record.id,
        "workspace_id": slug,
        "name": record.name,
        "key": raw_key,
        "warning": "Store this key now. It is hashed on the server and cannot be retrieved again.",
    }
