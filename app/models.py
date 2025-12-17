"""SQLAlchemy ORM models.

The schema is portable across SQLite (local development) and PostgreSQL
(production). JSON columns hold the canonical, lossless trace representation;
the relational columns exist so that filtering, analytics and pagination can
happen in the database instead of in Python.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    """A project/workspace boundary. All trace data is scoped to one workspace."""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="workspace", cascade="all, delete-orphan")


class ApiKey(Base):
    """Hashed API key. The raw key is shown once at creation time and never stored."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)

    workspace: Mapped[Workspace] = relationship(back_populates="api_keys")


class Run(Base):
    """One agent execution."""

    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "run_id", name="uq_runs_workspace_run"),
        Index("ix_runs_ws_started", "workspace_id", "started_at"),
        Index("ix_runs_ws_status", "workspace_id", "status"),
        Index("ix_runs_ws_task", "workspace_id", "task"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(120), index=True)
    trace_id: Mapped[str] = mapped_column(String(120), index=True)
    task_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    task: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(240), nullable=True)
    model: Mapped[str | None] = mapped_column(String(240), nullable=True)
    span_count: Mapped[int] = mapped_column(Integer, default=0)
    token_total: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    normalized_json: Mapped[str] = mapped_column(Text, default="{}")
    original_json: Mapped[str] = mapped_column(Text, default="{}")
    source_format: Mapped[str] = mapped_column(String(60), default="json")
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    environment_json: Mapped[dict] = mapped_column(JSON, default=dict)
    evaluation_json: Mapped[dict] = mapped_column(JSON, default=dict)
    import_warnings_json: Mapped[list] = mapped_column(JSON, default=list)


class Span(Base):
    """Normalized span rows for database-side analytics and filtering."""

    __tablename__ = "spans"
    __table_args__ = (
        UniqueConstraint("workspace_id", "run_id", "span_id", name="uq_spans_identity"),
        Index("ix_spans_ws_tool", "workspace_id", "tool_name"),
        Index("ix_spans_ws_status", "workspace_id", "status"),
        Index("ix_spans_ws_type", "workspace_id", "action_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(120), index=True)
    span_id: Mapped[str] = mapped_column(String(120))
    parent_span_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    action_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    action_type: Mapped[str] = mapped_column(String(32), default="unknown")
    name: Mapped[str] = mapped_column(Text, default="")
    tool_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(200), nullable=True)
    retry_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exception: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    token_total: Mapped[int] = mapped_column(Integer, default=0)
    attributes_json: Mapped[dict] = mapped_column(JSON, default=dict)
    inputs_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    outputs_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Finding(Base):
    """A rule-based observation produced by the deterministic analysis engine."""

    __tablename__ = "findings"
    __table_args__ = (Index("ix_findings_ws_run", "workspace_id", "run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(120), index=True)
    finding_id: Mapped[str] = mapped_column(String(200))
    rule_id: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40), default="Observed")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    span_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    evidence_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Hypothesis(Base):
    """A ranked root-cause candidate. Always labelled as inference, never as proof."""

    __tablename__ = "hypotheses"
    __table_args__ = (Index("ix_hypotheses_ws_run", "workspace_id", "run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(120), index=True)
    hypothesis_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(40), default="Inferred Hypothesis")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    primary_span_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    span_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    supporting_json: Mapped[list] = mapped_column(JSON, default=list)
    contradicting_json: Mapped[list] = mapped_column(JSON, default=list)
    limitations_json: Mapped[list] = mapped_column(JSON, default=list)
    suggested_experiment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Annotation(Base):
    """Append-only reviewer note. Original trace evidence is never modified."""

    __tablename__ = "annotations"
    __table_args__ = (Index("ix_annotations_ws_run", "workspace_id", "run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(120), index=True)
    finding_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    author: Mapped[str] = mapped_column(String(120), default="reviewer")
    note: Mapped[str] = mapped_column(Text, default="")
    disposition: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Experiment(Base):
    """Result of a controlled, side-effect-free local experiment."""

    __tablename__ = "experiments"
    __table_args__ = (Index("ix_experiments_ws_run", "workspace_id", "run_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(120), index=True)
    source_span_id: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="Insufficient Evidence")
    decision: Mapped[str] = mapped_column(String(80), default="")
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    """Audit trail for imports, changes, AI calls and experiments."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_ws_created", "workspace_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True, default="default")
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    run_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    actor: Mapped[str] = mapped_column(String(120), default="local-user")
    detail_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AppSetting(Base):
    """Per-workspace key/value settings (provider selection, redaction flag, ...)."""

    __tablename__ = "app_settings"

    workspace_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, default="null")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StoredSecret(Base):
    """Encrypted provider credential or endpoint entered through the UI.

    Only ciphertext is stored. Keys are write-only from the API: the plaintext
    is returned exactly once, when it is created.
    """

    __tablename__ = "stored_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(60))
    ciphertext: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("workspace_id", "provider", "name", name="uq_secret_identity"),)


class AiUsageEvent(Base):
    """Ledger entry for every consented AI request, used for budgets and cost reporting."""

    __tablename__ = "ai_usage_events"
    __table_args__ = (Index("ix_aiusage_ws_created", "workspace_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(200))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    pricing_source: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
