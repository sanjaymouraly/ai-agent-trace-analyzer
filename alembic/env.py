"""Alembic environment for TraceLens.

The migration URL is taken from ``DATABASE_URL`` so that the same commands work
against SQLite (local development) and PostgreSQL (production).
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", os.getenv("DATABASE_URL", settings.database_url).replace("%", "%%"))
target_metadata = Base.metadata


def _ensure_sqlite_directory(url: str) -> None:
    """Create the parent directory of a SQLite database file if needed."""
    if not url.startswith("sqlite"):
        return
    raw = url.split("sqlite:///", 1)[-1]
    if not raw or raw == ":memory:":
        return
    Path(raw).expanduser().parent.mkdir(parents=True, exist_ok=True)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    _ensure_sqlite_directory(config.get_main_option("sqlalchemy.url"))
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # SQLite cannot ALTER most columns in place; batch mode rewrites tables.
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
