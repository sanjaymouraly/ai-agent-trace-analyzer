"""Structured logging and optional OpenTelemetry instrumentation.

The application is fully functional without OpenTelemetry: when the exporter
packages or the endpoint are missing, instrumentation is skipped and the app
continues to log structured JSON lines.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from app.config import settings

_configured = False


class JsonFormatter(logging.Formatter):
    """One JSON object per log line, suitable for log shippers."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key.startswith("tracelens_"):
                payload[key.removeprefix("tracelens_")] = value
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def instrument_app(app: Any) -> bool:
    """Attach OpenTelemetry instrumentation when it is configured and available."""
    if not settings.otel_exporter_otlp_endpoint:
        return False
    try:  # pragma: no cover - depends on optional packages
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create({"service.name": settings.otel_service_name, "service.version": settings.app_version})
        )
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        return True
    except Exception as exc:  # pragma: no cover - optional dependency path
        logging.getLogger("tracelens.otel").warning("OpenTelemetry instrumentation skipped: %s", type(exc).__name__)
        return False
