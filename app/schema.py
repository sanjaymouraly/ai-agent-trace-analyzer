"""Versioned canonical execution trace model.

Unknown producer fields are preserved in ``attributes``/``raw`` rather than silently
interpreted as facts. Imported content is data only and is never executed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

SCHEMA_VERSION = "1.0"


class RunStatus(str, Enum):
    success = "success"
    failure = "failure"
    partial = "partial"
    running = "running"
    unknown = "unknown"


class SpanStatus(str, Enum):
    ok = "ok"
    error = "error"
    timeout = "timeout"
    running = "running"
    cancelled = "cancelled"
    unknown = "unknown"


class ActionType(str, Enum):
    agent = "agent"
    planning = "planning"
    model = "model"
    tool = "tool"
    retrieval = "retrieval"
    delegation = "delegation"
    evaluation = "evaluation"
    state = "state"
    unknown = "unknown"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TraceSpan(BaseModel):
    model_config = ConfigDict(extra="allow")

    trace_id: str = ""
    run_id: str = ""
    session_id: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    span_id: str = Field(default_factory=lambda: uuid4().hex[:16])
    parent_span_id: str | None = None
    span_links: list[str] = Field(default_factory=list)
    action_id: str | None = None
    name: str = "unnamed action"
    action_type: ActionType = ActionType.unknown
    start_time: datetime = Field(default_factory=utc_now)
    end_time: datetime | None = None
    status: SpanStatus = SpanStatus.unknown
    inputs: Any = None
    outputs: Any = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    model: str | None = None
    provider: str | None = None
    token_usage: dict[str, int | float] = Field(default_factory=dict)
    cost_usd: float | None = None
    retry_attempt: int | None = None
    exception: str | None = None
    explicit_dependencies: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def parse_time(cls, value: Any) -> Any:
        if value is None or isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            # Canonical numeric timestamps are seconds; adapters handle OTLP nanos.
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @field_validator("cost_usd")
    @classmethod
    def nonnegative_cost(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("cost_usd must be non-negative")
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_ms(self) -> float | None:
        if self.end_time is None:
            return None
        return max(0.0, (self.end_time - self.start_time).total_seconds() * 1000)


class ExecutionTrace(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = SCHEMA_VERSION
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str | None = None
    task_id: str | None = None
    task: str = "Untitled agent task"
    agent_id: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    status: RunStatus = RunStatus.unknown
    final_answer: Any = None
    evaluation_results: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)
    spans: list[TraceSpan] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    import_warnings: list[str] = Field(default_factory=list)

    @field_validator("started_at", "ended_at", mode="before")
    @classmethod
    def parse_time(cls, value: Any) -> Any:
        if value is None or isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @model_validator(mode="after")
    def validate_identity_and_spans(self) -> ExecutionTrace:
        if not self.trace_id.strip() or not self.run_id.strip():
            raise ValueError("trace_id and run_id must not be empty")
        ids = [span.span_id for span in self.spans]
        if len(ids) != len(set(ids)):
            raise ValueError("span_id values must be unique within a run")
        for span in self.spans:
            if span.trace_id and span.trace_id != self.trace_id:
                raise ValueError(f"span {span.span_id} belongs to another trace_id")
            if span.run_id and span.run_id != self.run_id:
                raise ValueError(f"span {span.span_id} belongs to another run_id")
        return self

    @property
    def duration_ms(self) -> float | None:
        if self.ended_at is None:
            return None
        return max(0.0, (self.ended_at - self.started_at).total_seconds() * 1000)
