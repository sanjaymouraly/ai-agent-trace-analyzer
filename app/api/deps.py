"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import db
from app.config import settings
from app.security import Principal, resolve_principal


def get_session() -> Iterator[Session]:
    """Yield a database session scoped to a single request."""
    with db.session_scope() as session:
        yield session


def _api_key_lookup(session: Session):
    def lookup(raw_key: str) -> str | None:
        record = db.find_api_key(session, raw_key)
        return record.workspace_id if record is not None else None

    return lookup


def get_principal(request: Request, session: Session = Depends(get_session)) -> Principal:
    """Resolve the caller. Raises 401 when authentication is required and missing."""
    principal = resolve_principal(request.headers.get("authorization"), _api_key_lookup(session))
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "A valid Bearer token is required."},
        )
    request.state.principal = principal
    return principal


def current_workspace(principal: Principal = Depends(get_principal)) -> str:
    """The workspace every query in this request is restricted to."""
    return principal.workspace_id


@dataclass(frozen=True)
class ResponsePolicy:
    """The workspace's outbound data policy.

    ``redaction_enabled`` is a per-workspace setting. Turning it off requires
    an explicit, audited confirmation in *Settings*.
    """

    redaction_enabled: bool

    def visible(self, value: Any) -> Any:
        """Apply the workspace redaction policy to an outbound payload."""
        from app.ingestion import redact

        return redact(value, enabled=self.redaction_enabled)


def response_policy(
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
) -> ResponsePolicy:
    configured = db.get_setting(session, workspace_id, "redaction_enabled", settings.default_redaction)
    return ResponsePolicy(redaction_enabled=bool(configured))


__all__ = [
    "ResponsePolicy",
    "current_workspace",
    "get_principal",
    "get_session",
    "response_policy",
]
