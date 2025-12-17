"""Runtime configuration.

Settings are read from environment variables (optionally from a local ``.env``).
Secrets never reach the browser: the frontend only ever sees booleans that
describe whether a credential is *present*.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


class Settings(BaseSettings):
    """Application settings.

    Attribute names map to uppercase environment variables by default
    (``database_url`` -> ``DATABASE_URL``).
    """

    model_config = SettingsConfigDict(
        env_file=(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "TraceLens"
    app_version: str = "1.0.0"
    environment: Literal["local", "development", "production"] = "local"
    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = f"sqlite:///{ROOT / 'data' / 'tracelens.db'}"
    sql_echo: bool = False

    auth_token: str = ""
    auth_required_paths: tuple[str, ...] = ("/api/v1/",)
    default_workspace: str = "default"
    allow_workspace_creation: bool = True

    cors_origins: tuple[str, ...] = ("http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:5173")

    max_upload_bytes: int = 10 * 1024 * 1024
    max_spans_per_run: int = 20_000
    max_batch_records: int = 500
    max_batch_spans: int = 100_000
    rate_limit_per_minute: int = 240
    trace_retention_days: int = 0
    default_redaction: bool = True
    seed_demo: bool = True

    # Background processing: Redis is optional, an in-process worker is the fallback.
    redis_url: str = ""
    worker_enabled: bool = True
    worker_queue_size: int = 256

    # Optional OpenTelemetry export.
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "tracelens"

    # Optional AI assistance. Disabled by default; offline analysis always works.
    llm_provider: str = "disabled"
    llm_max_daily_requests: int = 20
    llm_max_requests_per_minute: int = 5
    llm_timeout_seconds: int = 20
    llm_max_completion_tokens: int = 800
    llm_max_evidence_tokens: int = 8000
    llm_daily_budget_usd: float = 1.0
    llm_monthly_budget_usd: float = 10.0
    llm_allow_private_base_url: bool = False
    llm_require_consent: bool = True

    openai_api_key: str = ""
    openai_model: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = ""
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_api_key: str = ""
    ollama_model: str = ""
    openai_compatible_base_url: str = ""
    openai_compatible_api_key: str = ""
    openai_compatible_model: str = ""

    secret_key: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return _csv(value)
        return value

    @field_validator("auth_required_paths", mode="before")
    @classmethod
    def _split_paths(cls, value: object) -> object:
        if isinstance(value, str):
            return _csv(value)
        return value

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlite_path(self) -> Path | None:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            return None
        raw = self.database_url[len(prefix) :]
        path = Path(raw)
        return path if path.is_absolute() else (ROOT / path).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def reload_settings() -> Settings:
    """Re-read environment configuration (used by tests and the CLI)."""
    get_settings.cache_clear()
    return get_settings()


def data_dir() -> Path:
    path = settings.sqlite_path
    if path is not None:
        return path.parent
    return ROOT / "data"


def secret_store_path() -> Path:
    """Location of the auto-generated secret-key file used when APP_SECRET_KEY is unset."""
    return data_dir() / ".secret_key"


def resolve_secret_key() -> str:
    """Return the encryption key, generating and persisting one when needed.

    The key never leaves the server. When ``APP_SECRET_KEY`` is provided it is
    used verbatim, which is the recommended configuration for production.
    """
    if settings.secret_key:
        return settings.secret_key
    path = secret_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    from secrets import token_urlsafe

    value = token_urlsafe(48)
    path.write_text(value, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - platform dependent
        pass
    return value
