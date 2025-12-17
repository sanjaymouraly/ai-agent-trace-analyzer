"""Database layer and migration tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app import db

ROOT = Path(__file__).resolve().parents[1]


def test_alembic_migration_creates_the_expected_tables(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    env = {
        "DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    import sqlite3

    connection = sqlite3.connect(database)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    connection.close()
    for table in ("workspaces", "runs", "spans", "findings", "hypotheses", "annotations", "experiments", "audit_events"):
        assert table in tables


def test_runs_are_scoped_to_a_workspace() -> None:
    from app.ingestion import normalize_trace

    trace = normalize_trace(
        {
            "trace_id": "t",
            "run_id": "shared-run-id",
            "task": "Same run id in two workspaces",
            "status": "success",
            "spans": [],
        }
    )
    db.init_db()
    with db.session_scope() as session:
        db.ensure_workspace(session, "ws-a")
        db.ensure_workspace(session, "ws-b")
        db.save_trace(session, trace, trace.model_dump(mode="json"), "json", "hash-a", "ws-a")
        db.save_trace(session, trace, trace.model_dump(mode="json"), "json", "hash-b", "ws-b")
    with db.session_scope() as session:
        items_a, total_a = db.list_runs(session, "ws-a")
        items_b, total_b = db.list_runs(session, "ws-b")
        assert total_a == 1
        assert total_b == 1
        assert items_a[0]["run_id"] == items_b[0]["run_id"] == "shared-run-id"
        # Deleting in one workspace must not touch the other.
        assert db.delete_run(session, "ws-a", "shared-run-id") is True
    with db.session_scope() as session:
        assert db.list_runs(session, "ws-a")[1] == 0
        assert db.list_runs(session, "ws-b")[1] == 1


def test_span_rows_are_replaced_on_reingestion() -> None:
    from app.ingestion import normalize_trace

    raw = {
        "trace_id": "t",
        "run_id": "reingest",
        "task": "Re-ingestion",
        "status": "success",
        "spans": [
            {
                "span_id": "s1",
                "name": "one",
                "action_type": "tool",
                "start_time": "2026-01-01T00:00:00Z",
                "end_time": "2026-01-01T00:00:01Z",
                "status": "ok",
            }
        ],
    }
    trace = normalize_trace(raw)
    db.init_db()
    with db.session_scope() as session:
        db.save_trace(session, trace, raw, "json", "hash1", "ws-x")
        raw["spans"].append(
            {
                "span_id": "s2",
                "name": "two",
                "action_type": "tool",
                "start_time": "2026-01-01T00:00:01Z",
                "end_time": "2026-01-01T00:00:02Z",
                "status": "ok",
            }
        )
        updated = normalize_trace(raw)
        db.save_trace(session, updated, raw, "json", "hash2", "ws-x")
    with db.session_scope() as session:
        from sqlalchemy import func, select

        count = session.execute(
            select(func.count()).select_from(db.Span).where(db.Span.run_id == "reingest", db.Span.workspace_id == "ws-x")
        ).scalar_one()
        assert count == 2


def test_tool_failure_analytics_counts_recovery() -> None:
    from app.ingestion import normalize_trace

    raw = {
        "trace_id": "t",
        "run_id": "analytics",
        "task": "Tool analytics",
        "status": "failure",
        "spans": [
            {
                "span_id": "a1",
                "name": "call",
                "action_type": "tool",
                "tool_name": "flaky.tool",
                "start_time": "2026-01-01T00:00:00Z",
                "end_time": "2026-01-01T00:00:01Z",
                "status": "timeout",
                "exception": "Timeout after 1000ms",
            },
            {
                "span_id": "a2",
                "name": "call",
                "action_type": "tool",
                "tool_name": "flaky.tool",
                "start_time": "2026-01-01T00:00:01Z",
                "end_time": "2026-01-01T00:00:02Z",
                "status": "ok",
            },
        ],
    }
    trace = normalize_trace(raw)
    db.init_db()
    with db.session_scope() as session:
        db.save_trace(session, trace, raw, "json", "hash", "ws-tool")
    with db.session_scope() as session:
        analytics = db.tool_failure_analytics(session, "ws-tool")
        tool = next(item for item in analytics["tools"] if item["tool"] == "flaky.tool")
        assert tool["calls"] == 2
        assert tool["timeouts"] == 1
        assert tool["recovered"] == 1
        assert analytics["error_types"]["timeout"] == 1
