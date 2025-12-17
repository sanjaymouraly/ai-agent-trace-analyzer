"""Persistence layer.

All reads and writes are scoped to a workspace. Callers never build SQL by
hand; the functions below translate the API's needs into ORM queries and
return plain dictionaries.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, and_, delete, desc, func, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import (
    AiUsageEvent,
    Annotation,
    ApiKey,
    AppSetting,
    AuditEvent,
    Base,
    Experiment,
    Finding,
    Hypothesis,
    Run,
    Span,
    Workspace,
    utcnow,
)

logger = logging.getLogger("tracelens.db")

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        url = settings.database_url
        connect_args: dict[str, Any] = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
            path = settings.sqlite_path
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
        from sqlalchemy import create_engine

        _engine = create_engine(
            url,
            future=True,
            echo=settings.sql_echo,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
        if url.startswith("sqlite"):

            @event_listen_for_engine(_engine)
            def _set_sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver callback
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=15000")
                cursor.close()

        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def event_listen_for_engine(target: Engine):
    from sqlalchemy import event

    return event.listens_for(target, "connect")


def session_factory() -> sessionmaker[Session]:
    engine()
    assert _session_factory is not None
    return _session_factory


def init_db() -> None:
    """Create tables for local development and keep Alembic's revision in sync.

    Production deployments run ``alembic upgrade head`` instead. To keep a
    development database that was created with ``create_all`` upgradeable, the
    Alembic revision table is stamped with the newest known revision when it
    is missing or empty.
    """
    bind = engine()
    Base.metadata.create_all(bind=bind)
    _stamp_alembic_revision(bind)


def _latest_revision() -> str | None:
    """The newest revision id found in the Alembic versions directory."""
    versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    if not versions_dir.is_dir():
        return None
    revisions: list[tuple[str, str]] = []
    for path in sorted(versions_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        match = re.search(r"^revision(?::\s*str)?\s*=\s*['\"]([^'\"]+)['\"]", path.read_text(encoding="utf-8"), re.M)
        if match:
            revisions.append((path.name, match.group(1)))
    return revisions[-1][1] if revisions else None


def _stamp_alembic_revision(bind: Engine) -> None:
    from sqlalchemy import inspect, text

    try:
        revision = _latest_revision()
        if not revision:
            return
        with bind.begin() as connection:
            if "alembic_version" not in inspect(bind).get_table_names():
                connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            row = connection.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).first()
            if row is None:
                connection.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": revision})
    except Exception as exc:  # pragma: no cover - non-fatal for the application
        logger.warning("Could not stamp the Alembic revision: %s", type(exc).__name__)


def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


@contextmanager
def session_scope() -> Iterator[Session]:
    factory = session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Workspaces and API keys
# --------------------------------------------------------------------------- #
def ensure_workspace(session: Session, slug: str, name: str = "") -> Workspace:
    existing = session.get(Workspace, slug)
    if existing is not None:
        return existing
    workspace = Workspace(id=slug, slug=slug, name=name or slug.replace("-", " ").title())
    session.add(workspace)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.get(Workspace, slug)
        if existing is None:
            raise
        return existing
    return workspace


def list_workspaces(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(select(Workspace).order_by(Workspace.slug)).scalars().all()
    return [{"id": row.id, "slug": row.slug, "name": row.name, "created_at": _iso(row.created_at)} for row in rows]


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def create_api_key(session: Session, workspace_id: str, name: str, raw_key: str) -> ApiKey:
    record = ApiKey(
        id=hashlib.sha256(f"{workspace_id}:{raw_key}:{utcnow().isoformat()}".encode()).hexdigest()[:32],
        workspace_id=workspace_id,
        name=name,
        key_hash=hash_api_key(raw_key),
        key_prefix=raw_key[:8],
    )
    session.add(record)
    session.flush()
    return record


def find_api_key(session: Session, raw_key: str) -> ApiKey | None:
    record = session.execute(select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw_key))).scalar_one_or_none()
    if record is None or record.disabled:
        return None
    record.last_used_at = utcnow()
    return record


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def get_setting(session: Session, workspace_id: str, key: str, default: Any = None) -> Any:
    row = session.get(AppSetting, {"workspace_id": workspace_id, "key": key})
    if row is None:
        return default
    try:
        return json.loads(row.value_json)
    except (TypeError, ValueError):
        return default


def set_setting(session: Session, workspace_id: str, key: str, value: Any) -> None:
    row = session.get(AppSetting, {"workspace_id": workspace_id, "key": key})
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    if row is None:
        session.add(AppSetting(workspace_id=workspace_id, key=key, value_json=serialized))
    else:
        row.value_json = serialized
        row.updated_at = utcnow()
    _audit(session, "settings.updated", None, {"key": key, "value": _redact_setting_value(key, value)}, workspace_id)


def _redact_setting_value(key: str, value: Any) -> Any:
    if any(part in key.lower() for part in ("key", "token", "secret", "password")):
        return "[REDACTED]"
    return value


# --------------------------------------------------------------------------- #
# Audit trail
# --------------------------------------------------------------------------- #
def _audit(
    session: Session,
    event_type: str,
    run_id: str | None,
    detail: dict[str, Any],
    workspace_id: str = "default",
    actor: str = "local-user",
) -> None:
    session.add(
        AuditEvent(
            workspace_id=workspace_id,
            event_type=event_type,
            run_id=run_id,
            actor=actor,
            detail_json=detail,
        )
    )


def audit_event(
    session: Session,
    event_type: str,
    run_id: str | None = None,
    detail: dict[str, Any] | None = None,
    actor: str = "local-user",
    workspace_id: str = "default",
) -> None:
    _audit(session, event_type, run_id, detail or {}, workspace_id, actor)


def audit_events(session: Session, workspace_id: str, limit: int = 100) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            select(AuditEvent)
            .where(AuditEvent.workspace_id == workspace_id)
            .order_by(desc(AuditEvent.created_at), desc(AuditEvent.id))
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": row.id,
            "event_type": row.event_type,
            "run_id": row.run_id,
            "actor": row.actor,
            "detail": row.detail_json,
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _token_total(trace: Any) -> int:
    total = 0
    for span in trace.spans:
        usage = span.token_usage or {}
        value = usage.get("total_tokens")
        if value is None:
            value = sum(v for k, v in usage.items() if isinstance(v, (int, float)) and k != "total_tokens")
        if isinstance(value, (int, float)):
            total += int(value)
    return total


def _span_rows(trace: Any, workspace_id: str, run_id: str) -> list[Span]:
    rows: list[Span] = []
    for span in trace.spans:
        usage = span.token_usage or {}
        total = usage.get("total_tokens")
        if total is None:
            total = sum(v for k, v in usage.items() if isinstance(v, (int, float)) and k != "total_tokens")
        rows.append(
            Span(
                workspace_id=workspace_id,
                run_id=run_id,
                span_id=span.span_id,
                parent_span_id=span.parent_span_id,
                action_id=span.action_id,
                action_type=span.action_type.value,
                name=span.name,
                tool_name=span.tool_name,
                status=span.status.value,
                start_time=span.start_time,
                end_time=span.end_time,
                duration_ms=span.duration_ms,
                model=span.model,
                provider=span.provider,
                retry_attempt=span.retry_attempt,
                exception=span.exception,
                cost_usd=span.cost_usd or 0.0,
                token_total=int(total) if isinstance(total, (int, float)) else 0,
                attributes_json=span.attributes or {},
                inputs_json=span.inputs,
                outputs_json=span.outputs,
            )
        )
    return rows


def save_trace(
    session: Session,
    trace: Any,
    original: Any,
    source_format: str,
    content_hash: str,
    workspace_id: str = "default",
) -> bool:
    """Insert or update a run atomically. Returns True only for a newly created run."""
    body = trace.model_dump(mode="json")
    providers = list(dict.fromkeys(s.provider for s in trace.spans if s.provider))
    models = list(dict.fromkeys(s.model for s in trace.spans if s.model))
    agents = list(dict.fromkeys(s.agent_id for s in trace.spans if s.agent_id))
    existing = session.execute(
        select(Run).where(Run.workspace_id == workspace_id, Run.run_id == trace.run_id)
    ).scalar_one_or_none()
    values = {
        "trace_id": trace.trace_id,
        "task_id": trace.task_id,
        "task": trace.task,
        "status": trace.status.value,
        "started_at": trace.started_at,
        "ended_at": trace.ended_at,
        "agent_id": trace.agent_id or (agents[0] if agents else None),
        "provider": ", ".join(providers) or None,
        "model": ", ".join(models) or None,
        "span_count": len(trace.spans),
        "token_total": _token_total(trace),
        "cost_usd": round(sum(s.cost_usd or 0.0 for s in trace.spans), 8),
        "duration_ms": trace.duration_ms,
        "normalized_json": json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str),
        "original_json": json.dumps(original, ensure_ascii=False, separators=(",", ":"), default=str),
        "source_format": source_format,
        "imported_at": utcnow(),
        "content_hash": content_hash,
        "environment_json": trace.environment or {},
        "evaluation_json": trace.evaluation_results or {},
        "import_warnings_json": list(trace.import_warnings or []),
    }
    if existing is None:
        run = Run(workspace_id=workspace_id, run_id=trace.run_id, **values)
        session.add(run)
    else:
        for key, value in values.items():
            setattr(existing, key, value)
    session.flush()
    # Replace span rows so re-ingestion is idempotent.
    session.execute(delete(Span).where(Span.workspace_id == workspace_id, Span.run_id == trace.run_id))
    session.add_all(_span_rows(trace, workspace_id, trace.run_id))
    _audit(
        session,
        "trace.imported" if existing is None else "trace.reingested",
        trace.run_id,
        {"source_format": source_format, "span_count": len(trace.spans), "content_hash": content_hash},
        workspace_id,
    )
    return existing is None


def _run_row(row: Run, include_trace: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {
        "run_id": row.run_id,
        "trace_id": row.trace_id,
        "task_id": row.task_id,
        "task": row.task,
        "status": row.status,
        "started_at": _iso(row.started_at),
        "ended_at": _iso(row.ended_at),
        "agent_id": row.agent_id,
        "provider": row.provider,
        "model": row.model,
        "span_count": row.span_count,
        "token_total": row.token_total,
        "cost_usd": row.cost_usd,
        "duration_ms": row.duration_ms,
        "source_format": row.source_format,
        "imported_at": _iso(row.imported_at),
        "environment": row.environment_json or {},
        "evaluation_results": row.evaluation_json or {},
        "import_warnings": row.import_warnings_json or [],
    }
    if include_trace:
        result["trace"] = json.loads(row.normalized_json)
    return result


def get_run(session: Session, workspace_id: str, run_id: str, *, include_original: bool = False) -> dict[str, Any] | None:
    row = session.execute(select(Run).where(Run.workspace_id == workspace_id, Run.run_id == run_id)).scalar_one_or_none()
    if row is None:
        return None
    result = _run_row(row)
    if include_original:
        result["original"] = json.loads(row.original_json)
    return result


def list_runs(
    session: Session,
    workspace_id: str = "default",
    *,
    limit: int = 50,
    offset: int = 0,
    query: str = "",
    status: str = "",
    provider: str = "",
    task: str = "",
    model: str = "",
    agent: str = "",
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    min_duration_ms: float | None = None,
    max_duration_ms: float | None = None,
) -> tuple[list[dict[str, Any]], int]:
    filters = [Run.workspace_id == workspace_id]
    if query:
        pattern = f"%{query}%"
        filters.append(
            or_(
                Run.task.ilike(pattern),
                Run.run_id.ilike(pattern),
                Run.trace_id.ilike(pattern),
                Run.task_id.ilike(pattern),
            )
        )
    if status:
        filters.append(Run.status == status)
    if provider:
        filters.append(Run.provider.ilike(f"%{provider}%"))
    if model:
        filters.append(Run.model.ilike(f"%{model}%"))
    if agent:
        filters.append(Run.agent_id.ilike(f"%{agent}%"))
    if task:
        filters.append(Run.task.ilike(f"%{task}%"))
    if started_after is not None:
        filters.append(Run.started_at >= started_after)
    if started_before is not None:
        filters.append(Run.started_at <= started_before)
    if min_duration_ms is not None:
        filters.append(Run.duration_ms >= min_duration_ms)
    if max_duration_ms is not None:
        filters.append(Run.duration_ms <= max_duration_ms)

    total = session.execute(select(func.count()).select_from(Run).where(*filters)).scalar_one()
    rows = (
        session.execute(select(Run).where(*filters).order_by(desc(Run.started_at), desc(Run.id)).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return [_run_row(row, include_trace=False) for row in rows], int(total)


def delete_run(session: Session, workspace_id: str, run_id: str) -> bool:
    row = session.execute(select(Run).where(Run.workspace_id == workspace_id, Run.run_id == run_id)).scalar_one_or_none()
    if row is None:
        return False
    session.execute(delete(Span).where(Span.workspace_id == workspace_id, Span.run_id == run_id))
    session.execute(delete(Finding).where(Finding.workspace_id == workspace_id, Finding.run_id == run_id))
    session.execute(delete(Hypothesis).where(Hypothesis.workspace_id == workspace_id, Hypothesis.run_id == run_id))
    session.execute(delete(Annotation).where(Annotation.workspace_id == workspace_id, Annotation.run_id == run_id))
    session.execute(delete(Experiment).where(Experiment.workspace_id == workspace_id, Experiment.run_id == run_id))
    session.delete(row)
    _audit(session, "trace.deleted", run_id, {}, workspace_id)
    return True


def delete_expired_runs(session: Session, workspace_id: str, cutoff: datetime) -> int:
    rows = session.execute(select(Run).where(Run.workspace_id == workspace_id, Run.imported_at < cutoff)).scalars().all()
    for row in rows:
        session.execute(delete(Span).where(Span.workspace_id == workspace_id, Span.run_id == row.run_id))
        session.execute(delete(Finding).where(Finding.workspace_id == workspace_id, Finding.run_id == row.run_id))
        session.execute(delete(Hypothesis).where(Hypothesis.workspace_id == workspace_id, Hypothesis.run_id == row.run_id))
        session.execute(delete(Annotation).where(Annotation.workspace_id == workspace_id, Annotation.run_id == row.run_id))
        session.execute(delete(Experiment).where(Experiment.workspace_id == workspace_id, Experiment.run_id == row.run_id))
        session.delete(row)
    if rows:
        _audit(
            session,
            "retention.expired_runs_deleted",
            None,
            {"deleted_runs": len(rows), "cutoff": _iso(cutoff)},
            workspace_id,
            actor="retention-worker",
        )
    return len(rows)


# --------------------------------------------------------------------------- #
# Findings and hypotheses
# --------------------------------------------------------------------------- #
def replace_analysis(
    session: Session,
    workspace_id: str,
    run_id: str,
    findings: list[dict[str, Any]],
    hypotheses: list[dict[str, Any]],
) -> None:
    session.execute(delete(Finding).where(Finding.workspace_id == workspace_id, Finding.run_id == run_id))
    session.execute(delete(Hypothesis).where(Hypothesis.workspace_id == workspace_id, Hypothesis.run_id == run_id))
    for item in findings:
        session.add(
            Finding(
                workspace_id=workspace_id,
                run_id=run_id,
                finding_id=str(item.get("finding_id", ""))[:200],
                rule_id=str(item.get("rule_id", ""))[:120],
                category=str(item.get("category", ""))[:80],
                status=str(item.get("status", "Observed"))[:40],
                confidence=float(item.get("confidence", 0.0)),
                span_ids_json=list(item.get("span_ids", [])),
                evidence_json=list(item.get("evidence", [])),
            )
        )
    for item in hypotheses:
        session.add(
            Hypothesis(
                workspace_id=workspace_id,
                run_id=run_id,
                hypothesis_id=str(item.get("hypothesis_id", ""))[:200],
                title=str(item.get("title", ""))[:1000],
                category=str(item.get("category", ""))[:80],
                status=str(item.get("status", "Inferred Hypothesis"))[:40],
                confidence=float(item.get("confidence", 0.0)),
                primary_span_id=item.get("primary_span_id"),
                span_ids_json=list(item.get("span_ids", [])),
                supporting_json=list(item.get("supporting_evidence", [])),
                contradicting_json=list(item.get("contradicting_evidence", [])),
                limitations_json=list(item.get("limitations", [])),
                suggested_experiment=item.get("suggested_experiment"),
            )
        )


def findings_for(session: Session, workspace_id: str, run_id: str) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            select(Finding)
            .where(Finding.workspace_id == workspace_id, Finding.run_id == run_id)
            .order_by(desc(Finding.confidence), Finding.id)
        )
        .scalars()
        .all()
    )
    return [
        {
            "finding_id": row.finding_id,
            "rule_id": row.rule_id,
            "category": row.category,
            "status": row.status,
            "confidence": row.confidence,
            "span_ids": row.span_ids_json,
            "evidence": row.evidence_json,
        }
        for row in rows
    ]


def hypotheses_for(session: Session, workspace_id: str, run_id: str) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            select(Hypothesis)
            .where(Hypothesis.workspace_id == workspace_id, Hypothesis.run_id == run_id)
            .order_by(desc(Hypothesis.confidence), Hypothesis.id)
        )
        .scalars()
        .all()
    )
    return [
        {
            "hypothesis_id": row.hypothesis_id,
            "title": row.title,
            "category": row.category,
            "status": row.status,
            "confidence": row.confidence,
            "primary_span_id": row.primary_span_id,
            "span_ids": row.span_ids_json,
            "supporting_evidence": row.supporting_json,
            "contradicting_evidence": row.contradicting_json,
            "limitations": row.limitations_json,
            "suggested_experiment": row.suggested_experiment,
        }
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# Annotations and experiments
# --------------------------------------------------------------------------- #
def add_annotation(session: Session, workspace_id: str, run_id: str, annotation: dict[str, Any]) -> dict[str, Any]:
    record = Annotation(
        workspace_id=workspace_id,
        run_id=run_id,
        finding_id=annotation.get("finding_id"),
        author=str(annotation.get("author", "reviewer"))[:120],
        note=annotation["note"],
        disposition=annotation.get("disposition"),
    )
    session.add(record)
    session.flush()
    _audit(
        session,
        "annotation.created",
        run_id,
        {"finding_id": record.finding_id, "disposition": record.disposition},
        workspace_id,
        actor=record.author,
    )
    return {
        "id": record.id,
        "run_id": run_id,
        "finding_id": record.finding_id,
        "author": record.author,
        "note": record.note,
        "disposition": record.disposition,
        "created_at": _iso(record.created_at),
    }


def annotations_for(session: Session, workspace_id: str, run_id: str) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            select(Annotation)
            .where(Annotation.workspace_id == workspace_id, Annotation.run_id == run_id)
            .order_by(desc(Annotation.created_at), desc(Annotation.id))
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": row.id,
            "run_id": row.run_id,
            "finding_id": row.finding_id,
            "author": row.author,
            "note": row.note,
            "disposition": row.disposition,
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ]


def save_experiment(
    session: Session,
    workspace_id: str,
    run_id: str,
    source_span_id: str,
    title: str,
    result: dict[str, Any],
    experiment_id: str,
) -> dict[str, Any]:
    record = Experiment(
        id=experiment_id,
        workspace_id=workspace_id,
        run_id=run_id,
        source_span_id=source_span_id,
        title=title,
        status=str(result.get("status", "Insufficient Evidence"))[:40],
        decision=str(result.get("decision", ""))[:80],
        result_json=result,
    )
    session.add(record)
    session.flush()
    _audit(
        session,
        "experiment.counterfactual",
        run_id,
        {
            "experiment_id": experiment_id,
            "source_span_id": source_span_id,
            "title": title,
            "status": record.status,
        },
        workspace_id,
    )
    return {
        "id": record.id,
        "run_id": run_id,
        "source_span_id": source_span_id,
        "title": title,
        "status": record.status,
        "decision": record.decision,
        "result": result,
        "created_at": _iso(record.created_at),
    }


def experiments_for(session: Session, workspace_id: str, run_id: str) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            select(Experiment)
            .where(Experiment.workspace_id == workspace_id, Experiment.run_id == run_id)
            .order_by(desc(Experiment.created_at))
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": row.id,
            "run_id": row.run_id,
            "source_span_id": row.source_span_id,
            "title": row.title,
            "status": row.status,
            "decision": row.decision,
            "result": row.result_json,
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# AI usage ledger
# --------------------------------------------------------------------------- #
def ai_calls_since(session: Session, workspace_id: str, since: datetime) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(AiUsageEvent)
            .where(AiUsageEvent.workspace_id == workspace_id, AiUsageEvent.created_at >= since)
        ).scalar_one()
    )


def ai_cost_since(session: Session, workspace_id: str, since: datetime) -> float:
    value = session.execute(
        select(func.coalesce(func.sum(AiUsageEvent.estimated_cost_usd), 0.0)).where(
            AiUsageEvent.workspace_id == workspace_id, AiUsageEvent.created_at >= since
        )
    ).scalar_one()
    return float(value or 0.0)


def record_ai_usage(
    session: Session,
    workspace_id: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    estimated_cost_usd: float,
    pricing_source: str,
    run_id: str | None = None,
) -> None:
    session.add(
        AiUsageEvent(
            workspace_id=workspace_id,
            run_id=run_id,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost_usd,
            pricing_source=pricing_source,
        )
    )


# --------------------------------------------------------------------------- #
# Analytics
# --------------------------------------------------------------------------- #
def dashboard_data(session: Session, workspace_id: str = "default") -> dict[str, Any]:
    runs = session.execute(select(Run).where(Run.workspace_id == workspace_id).order_by(desc(Run.started_at))).scalars().all()
    category_rows = session.execute(
        select(Finding.category, func.count())
        .where(Finding.workspace_id == workspace_id, Finding.category != "failure_propagation")
        .group_by(Finding.category)
    ).all()
    categories = {row[0]: int(row[1]) for row in category_rows}
    completed = [row for row in runs if row.status in ("success", "failure")]
    success_count = sum(1 for row in completed if row.status == "success")
    failure_count = sum(1 for row in completed if row.status == "failure")
    durations = [row.duration_ms for row in runs if row.duration_ms is not None]
    latency_series = [
        {
            "run_id": row.run_id,
            "task": row.task,
            "duration_ms": row.duration_ms or 0,
            "status": row.status,
            "started_at": _iso(row.started_at),
        }
        for row in runs[:30]
    ]
    return {
        "total_runs": len(runs),
        "successful_runs": success_count,
        "failed_runs": failure_count,
        "success_rate": round(100 * success_count / len(completed), 1) if completed else None,
        "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else None,
        "p95_duration_ms": round(_percentile(durations, 0.95), 1) if durations else None,
        "total_tokens": sum(row.token_total for row in runs),
        "total_cost_usd": round(sum(row.cost_usd or 0 for row in runs), 6),
        "failure_categories": categories,
        "failure_categories_runs_analyzed": len(runs),
        "recent_runs": [_run_row(row, include_trace=False) for row in runs[:8]],
        "latency_series": latency_series,
        "provider_counts": _count_values(row.provider for row in runs),
        "status_counts": _count_values(row.status for row in runs),
        "token_series": [
            {"run_id": row.run_id, "task": row.task, "tokens": row.token_total, "status": row.status} for row in runs[:30]
        ],
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _count_values(values: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        for part in str(value or "Unknown").split(", "):
            key = part.strip() or "Unknown"
            result[key] = result.get(key, 0) + 1
    return result


def tool_failure_analytics(session: Session, workspace_id: str = "default", limit: int = 50) -> dict[str, Any]:
    """Aggregate tool-level failure statistics across the workspace."""
    spans = (
        session.execute(select(Span).where(Span.workspace_id == workspace_id, Span.action_type.in_(["tool", "retrieval"])))
        .scalars()
        .all()
    )
    by_tool: dict[str, dict[str, Any]] = {}
    for span in spans:
        key = span.tool_name or span.name
        bucket = by_tool.setdefault(
            key,
            {
                "tool": key,
                "calls": 0,
                "errors": 0,
                "timeouts": 0,
                "ok": 0,
                "durations": [],
                "recovered": 0,
                "error_types": {},
            },
        )
        bucket["calls"] += 1
        failure_kind = _span_failure_kind(span)
        if failure_kind == "timeout":
            bucket["timeouts"] += 1
        elif failure_kind == "error":
            bucket["errors"] += 1
        elif span.status == "ok":
            bucket["ok"] += 1
        if span.duration_ms is not None:
            bucket["durations"].append(span.duration_ms)
        if span.exception:
            category = _error_category(span.exception)
            bucket["error_types"][category] = bucket["error_types"].get(category, 0) + 1

    # Recovery: an error/timeout span whose sibling retry with the same action succeeded.
    grouped: dict[tuple[str, str], list[Span]] = {}
    for span in spans:
        grouped.setdefault((span.run_id, span.tool_name or span.name), []).append(span)
    for group in grouped.values():
        group.sort(key=lambda s: s.start_time)
        for index, span in enumerate(group):
            if _span_failure_kind(span) is None:
                continue
            for later in group[index + 1 :]:
                if later.status == "ok":
                    key = span.tool_name or span.name
                    by_tool[key]["recovered"] += 1
                    break

    tools = []
    for bucket in by_tool.values():
        durations = bucket.pop("durations")
        median = round(_percentile(durations, 0.5), 2) if durations else None
        p95 = round(_percentile(durations, 0.95), 2) if durations else None
        failures = bucket["errors"] + bucket["timeouts"]
        tools.append(
            {
                **bucket,
                "failure_rate": round(failures / bucket["calls"], 4) if bucket["calls"] else 0.0,
                "median_duration_ms": median,
                "p95_duration_ms": p95,
                "max_duration_ms": round(max(durations), 2) if durations else None,
            }
        )
    tools.sort(key=lambda item: (-item["failure_rate"], -item["errors"], item["tool"]))
    error_totals: dict[str, int] = {}
    for item in tools:
        for category, count in item["error_types"].items():
            error_totals[category] = error_totals.get(category, 0) + count
    return {
        "tools": tools[:limit],
        "total_tool_calls": sum(item["calls"] for item in tools),
        "total_failures": sum(item["errors"] + item["timeouts"] for item in tools),
        "recovered_calls": sum(item["recovered"] for item in tools),
        "error_types": dict(sorted(error_totals.items(), key=lambda kv: -kv[1])),
        "latency_outliers": [
            {
                "run_id": span.run_id,
                "span_id": span.span_id,
                "tool": span.tool_name or span.name,
                "duration_ms": span.duration_ms,
                "status": span.status,
            }
            for span in spans
            if span.duration_ms is not None and span.duration_ms > 30_000
        ][:limit],
    }


def _span_failure_kind(span: Span) -> str | None:
    """Classify a recorded span failure.

    A producer that recorded only an exception message is still classified by
    its message, so a timeout is not silently counted as a generic error.
    """
    if span.status == "timeout":
        return "timeout"
    if span.exception:
        text = span.exception.lower()
        if "timeout" in text or "timed out" in text or "deadline" in text:
            return "timeout"
    if span.status == "error":
        return "error"
    if span.exception:
        return "error"
    return None


def _error_category(message: str) -> str:
    text = message.lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "permission" in text or "forbidden" in text or "unauthor" in text:
        return "permission"
    if "rate limit" in text or "429" in text:
        return "rate_limit"
    if "connection" in text or "network" in text:
        return "network"
    if "validation" in text or "invalid argument" in text:
        return "invalid_arguments"
    if "not found" in text or "404" in text:
        return "not_found"
    return "other"


def count_runs(session: Session, workspace_id: str = "default") -> int:
    return int(session.execute(select(func.count()).select_from(Run).where(Run.workspace_id == workspace_id)).scalar_one())


def workspace_has_runs(session: Session, workspace_id: str) -> bool:
    return count_runs(session, workspace_id) > 0


def run_age_cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=max(0, days))


def filter_by_workspace(statement: Any, model: Any, workspace_id: str) -> Any:
    return statement.where(model.workspace_id == workspace_id)


__all__ = [
    "add_annotation",
    "ai_calls_since",
    "ai_cost_since",
    "annotations_for",
    "audit_event",
    "audit_events",
    "count_runs",
    "create_api_key",
    "dashboard_data",
    "delete_expired_runs",
    "delete_run",
    "dispose_engine",
    "ensure_workspace",
    "engine",
    "experiments_for",
    "find_api_key",
    "findings_for",
    "get_run",
    "get_setting",
    "hypotheses_for",
    "init_db",
    "list_runs",
    "list_workspaces",
    "record_ai_usage",
    "replace_analysis",
    "save_experiment",
    "save_trace",
    "session_factory",
    "session_scope",
    "set_setting",
    "tool_failure_analytics",
    "workspace_has_runs",
    "hash_api_key",
    "utcnow",
    "and_",
    "DateTime",
]
