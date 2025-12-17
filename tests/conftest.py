"""Shared pytest fixtures.

Tests run against an isolated SQLite database and never touch a developer's
local or demo data. The environment is configured before the application is
imported so that ``app.config.settings`` picks it up.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

TEST_DB_PATH = Path(tempfile.gettempdir()) / "tracelens-test.db"
os.environ["TRACELENS_TEST_DATABASE_PATH"] = str(TEST_DB_PATH)
os.environ["DATABASE_URL"] = "sqlite:///" + TEST_DB_PATH.as_posix()
os.environ["SEED_DEMO"] = "false"
os.environ["DEFAULT_REDACTION"] = "true"
os.environ["APP_LLM_PROVIDER"] = "disabled"
os.environ["APP_SECRET_KEY"] = "test-secret-key-for-encrypted-credential-tests"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402


def _remove_database() -> None:
    for suffix in ["", "-wal", "-shm"]:
        candidate = Path(str(TEST_DB_PATH) + suffix)
        if candidate.exists():
            candidate.unlink()


@pytest.fixture()
def client():
    """A TestClient bound to a fresh, empty database."""
    _remove_database()
    db.dispose_engine()
    with TestClient(app) as test_client:
        yield test_client
    db.dispose_engine()


@pytest.fixture()
def seeded_client():
    """A TestClient with the deterministic demo workflows loaded."""

    _remove_database()
    db.dispose_engine()
    with TestClient(app) as test_client:
        response = test_client.post("/api/v1/demo/seed")
        assert response.status_code == 200, response.text
        yield test_client
    db.dispose_engine()


@pytest.fixture()
def sample_trace() -> dict:
    """A small, valid canonical trace with one tool failure."""
    return {
        "schema_version": "1.0",
        "trace_id": "trace-sample",
        "run_id": "sample-run",
        "task_id": "sample-task",
        "task": "Sample investigation",
        "agent_id": "sample-agent",
        "started_at": "2026-01-01T00:00:00Z",
        "ended_at": "2026-01-01T00:00:02Z",
        "status": "failure",
        "final_answer": None,
        "evaluation_results": {"passed": False},
        "spans": [
            {
                "span_id": "s-plan",
                "name": "Plan",
                "action_type": "planning",
                "start_time": "2026-01-01T00:00:00Z",
                "end_time": "2026-01-01T00:00:00.100Z",
                "status": "ok",
                "outputs": {"steps": ["call_tool"]},
                "model": "demo-model",
                "provider": "offline-fixture",
                "token_usage": {"total_tokens": 40},
            },
            {
                "span_id": "s-tool",
                "parent_span_id": "s-plan",
                "name": "Call tool",
                "action_type": "tool",
                "tool_name": "demo.tool",
                "start_time": "2026-01-01T00:00:00.200Z",
                "end_time": "2026-01-01T00:00:00.500Z",
                "status": "error",
                "exception": "Invalid arguments: expected integer 'count'",
                "inputs": {"count": "many"},
                "explicit_dependencies": ["s-plan"],
            },
            {
                "span_id": "s-final",
                "parent_span_id": "s-plan",
                "name": "Final answer",
                "action_type": "model",
                "start_time": "2026-01-01T00:00:00.600Z",
                "end_time": "2026-01-01T00:00:01.000Z",
                "status": "error",
                "exception": "Missing data: tool produced no output",
                "inputs": {"result": None},
                "explicit_dependencies": ["s-tool"],
                "attributes": {"missing_input": True},
            },
        ],
    }
