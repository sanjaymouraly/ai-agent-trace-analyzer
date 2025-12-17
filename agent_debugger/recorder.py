"""Minimal framework-neutral SDK for recording custom agent actions."""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.schema import ActionType, ExecutionTrace, RunStatus, SpanStatus, TraceSpan

_current_span: contextvars.ContextVar[str | None] = contextvars.ContextVar("tracelens_current_span", default=None)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RecordedSpan:
    """Mutable handle yielded by :meth:`TraceRecorder.span`."""

    def __init__(self, span: TraceSpan):
        self.span = span

    @property
    def span_id(self) -> str:
        return self.span.span_id

    def set_output(self, output: Any) -> None:
        self.span.outputs = output

    def set_attribute(self, key: str, value: Any) -> None:
        self.span.attributes[key] = value

    def add_dependency(self, span_id: str) -> None:
        if span_id not in self.span.explicit_dependencies:
            self.span.explicit_dependencies.append(span_id)


class TraceRecorder:
    """Create a canonical run with nested, parallel-safe span scopes.

    Example::

        with TraceRecorder(task="summarize") as run:
            with run.span("search", action_type="tool", tool_name="search") as s:
                result = search(query)
                s.set_output(result)
            run.finish(status="success", final_answer="...")
    """

    def __init__(
        self,
        task: str,
        *,
        run_id: str | None = None,
        trace_id: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        environment: dict[str, Any] | None = None,
    ):
        self.task = task
        self.run_id = run_id or uuid4().hex
        self.trace_id = trace_id or uuid4().hex
        self.task_id = task_id or uuid4().hex
        self.session_id = session_id
        self.agent_id = agent_id
        self.started_at = _now()
        self.ended_at: datetime | None = None
        self.status = RunStatus.running
        self.final_answer: Any = None
        self.evaluation_results: dict[str, Any] = {}
        self.environment = dict(environment or {})
        self.spans: list[TraceSpan] = []
        self._lock = threading.RLock()

    @contextmanager
    def span(
        self,
        name: str,
        *,
        action_type: str | ActionType = ActionType.unknown,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        tool_name: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        inputs: Any = None,
        attributes: dict[str, Any] | None = None,
        explicit_dependencies: list[str] | None = None,
        action_id: str | None = None,
        tool_call_id: str | None = None,
        retry_attempt: int | None = None,
        token_usage: dict[str, int | float] | None = None,
    ) -> Iterator[RecordedSpan]:
        sid = span_id or uuid4().hex[:16]
        parent = parent_span_id if parent_span_id is not None else _current_span.get()
        start = _now()
        try:
            kind = ActionType(action_type)
        except ValueError:
            kind = ActionType.unknown
        span = TraceSpan(
            trace_id=self.trace_id,
            run_id=self.run_id,
            session_id=self.session_id,
            task_id=self.task_id,
            agent_id=self.agent_id,
            span_id=sid,
            parent_span_id=parent,
            name=name,
            action_type=kind,
            start_time=start,
            status=SpanStatus.running,
            inputs=inputs,
            tool_name=tool_name,
            model=model,
            provider=provider,
            attributes=dict(attributes or {}),
            explicit_dependencies=list(explicit_dependencies or []),
            action_id=action_id,
            tool_call_id=tool_call_id,
            retry_attempt=retry_attempt,
            token_usage=dict(token_usage or {}),
        )
        handle = RecordedSpan(span)
        with self._lock:
            if any(old.span_id == sid for old in self.spans):
                raise ValueError(f"Duplicate span_id: {sid}")
            self.spans.append(span)
        token = _current_span.set(sid)
        try:
            yield handle
        except BaseException as exc:
            span.exception = f"{type(exc).__name__}: {exc}"
            span.status = SpanStatus.error
            raise
        else:
            if span.status == SpanStatus.running:
                span.status = SpanStatus.ok
        finally:
            span.end_time = _now()
            _current_span.reset(token)

    def add_span(self, span: TraceSpan | dict[str, Any]) -> TraceSpan:
        item = span if isinstance(span, TraceSpan) else TraceSpan.model_validate(span)
        item.trace_id = self.trace_id
        item.run_id = self.run_id
        with self._lock:
            if any(old.span_id == item.span_id for old in self.spans):
                raise ValueError(f"Duplicate span_id: {item.span_id}")
            self.spans.append(item)
        return item

    def finish(
        self, *, status: str | RunStatus, final_answer: Any = None, evaluation_results: dict[str, Any] | None = None
    ) -> None:
        self.status = RunStatus(status)
        self.final_answer = final_answer
        self.evaluation_results = dict(evaluation_results or {})
        self.ended_at = _now()

    def to_dict(self) -> dict[str, Any]:
        ended_at = self.ended_at or max((s.end_time for s in self.spans if s.end_time), default=_now())
        model = ExecutionTrace(
            schema_version="1.0",
            trace_id=self.trace_id,
            run_id=self.run_id,
            session_id=self.session_id,
            task_id=self.task_id,
            task=self.task,
            agent_id=self.agent_id,
            started_at=self.started_at,
            ended_at=ended_at,
            status=self.status,
            final_answer=self.final_answer,
            evaluation_results=self.evaluation_results,
            environment=self.environment,
            spans=self.spans,
        )
        return model.model_dump(mode="json")

    def submit(self, client: Any = None, base_url: str | None = None) -> dict[str, Any]:
        """Send the recorded run to a TraceLens server.

        The trace is sent as data. The server never executes recorded content.
        """
        if client is None:
            from agent_debugger.client import TraceLensClient

            client = TraceLensClient(base_url=base_url)
        return client.submit_trace(self.to_dict())

    def __enter__(self) -> TraceRecorder:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc is not None:
            self.finish(status="failure", evaluation_results={"exception_type": type(exc).__name__})
        elif self.ended_at is None:
            self.finish(status="unknown")
