"""FastAPI application factory for TraceLens.

The API, the SPA dashboard and the health endpoints are served from a single
origin, so no CORS configuration is required for the default deployment.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.api import admin, ai, analysis, experiments, ingestion, traces
from app.config import settings
from app.observability import configure_logging, instrument_app

configure_logging()
logger = logging.getLogger("tracelens")

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST = ROOT / "frontend" / "dist"

PUBLIC_PATHS = {"/api/v1/health", "/api/v1/ready", "/openapi.json", "/docs", "/redoc"}


def safe_request_id(value: str | None) -> str:
    if value and len(value) <= 80 and value.isascii() and re.fullmatch(r"[A-Za-z0-9._:-]+", value):
        return value
    return uuid.uuid4().hex


class _RequestBodyTooLarge(Exception):
    """Internal signal used to stop oversized streamed request bodies."""


class SecurityAndLimitMiddleware:
    """Local-mode auth, rate limiting, bounded request bodies and safe headers."""

    def __init__(self, asgi_app):
        self.app = asgi_app
        self.requests: dict[str, deque[float]] = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        is_api_route = path.startswith("/api/v1/") or path == "/v1/traces"
        headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in scope.get("headers", [])}
        client = scope.get("client") or ("unknown", 0)
        request_id = safe_request_id(headers.get("x-request-id"))

        if is_api_route and path not in PUBLIC_PATHS and settings.auth_token:
            authorization = headers.get("authorization", "")
            presented = authorization[7:] if authorization.lower().startswith("bearer ") else ""
            if not presented:
                response = JSONResponse(
                    {"error": {"code": "unauthorized", "message": "A valid Bearer token is required."}, "request_id": request_id},
                    status_code=401,
                )
                await response(scope, receive, send)
                return

        if is_api_route and path not in PUBLIC_PATHS:
            key = str(client[0])
            now = time.monotonic()
            bucket = self.requests[key]
            while bucket and now - bucket[0] > 60:
                bucket.popleft()
            if len(bucket) >= settings.rate_limit_per_minute:
                response = JSONResponse(
                    {
                        "error": {"code": "rate_limited", "message": "Request limit reached. Retry in one minute."},
                        "request_id": request_id,
                    },
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
                await response(scope, receive, send)
                return
            bucket.append(now)

        max_request_bytes = settings.max_upload_bytes + (64 * 1024 if path == "/api/v1/ingest/file" else 0)
        body_limit_exceeded = False
        body_limit_response_sent = False
        body_limit_body = json.dumps(
            {
                "error": {
                    "code": "payload_too_large",
                    "message": f"Request exceeds the {max_request_bytes}-byte transport limit.",
                },
                "request_id": request_id,
            },
            separators=(",", ":"),
        ).encode("utf-8")

        async def send_with_headers(message):
            nonlocal body_limit_response_sent
            if message["type"] == "http.response.start":
                existing = list(message.get("headers", []))
                if body_limit_exceeded:
                    existing = [(k, v) for k, v in existing if k not in {b"content-type", b"content-length"}]
                    existing.extend(
                        [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body_limit_body)).encode("ascii")),
                        ]
                    )
                    message = {**message, "status": 413}
                existing.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"x-frame-options", b"SAMEORIGIN"),
                        (
                            b"content-security-policy",
                            b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                            b"connect-src 'self'; font-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'self'",
                        ),
                    ]
                )
                message["headers"] = existing
            elif message["type"] == "http.response.body" and body_limit_exceeded:
                if body_limit_response_sent:
                    return
                message = {**message, "body": body_limit_body, "more_body": False}
                body_limit_response_sent = True
            await send(message)

        content_length = headers.get("content-length")
        if is_api_route and content_length:
            try:
                declared_length = int(content_length)
            except ValueError:
                response = JSONResponse(
                    {
                        "error": {"code": "invalid_content_length", "message": "Content-Length must be an integer."},
                        "request_id": request_id,
                    },
                    status_code=400,
                )
                await response(scope, receive, send_with_headers)
                return
            if declared_length > max_request_bytes:
                response = JSONResponse(
                    {
                        "error": {
                            "code": "payload_too_large",
                            "message": f"Request exceeds the {max_request_bytes}-byte transport limit.",
                        },
                        "request_id": request_id,
                    },
                    status_code=413,
                )
                await response(scope, receive, send_with_headers)
                return

        request_bytes = 0

        async def receive_with_limit():
            nonlocal request_bytes, body_limit_exceeded
            message = await receive()
            if is_api_route and message["type"] == "http.request":
                request_bytes += len(message.get("body", b""))
                if request_bytes > max_request_bytes:
                    body_limit_exceeded = True
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, receive_with_limit, send_with_headers)
        except _RequestBodyTooLarge:
            response = JSONResponse(
                {
                    "error": {
                        "code": "payload_too_large",
                        "message": f"Request exceeds the {max_request_bytes}-byte transport limit.",
                    },
                    "request_id": request_id,
                },
                status_code=413,
            )
            await response(scope, receive, send_with_headers)


def _run_retention_cleanup() -> int:
    from datetime import datetime, timedelta, timezone

    if settings.trace_retention_days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.trace_retention_days)
    deleted = 0
    with db.session_scope() as session:
        for workspace in db.list_workspaces(session):
            deleted += db.delete_expired_runs(session, workspace["id"], cutoff)
    return deleted


async def _retention_worker() -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            deleted = await asyncio.to_thread(_run_retention_cleanup)
            if deleted:
                logger.info("retention.cleanup", extra={"tracelens_deleted": deleted})
        except Exception as exc:
            logger.error("retention.cleanup_failed", extra={"tracelens_exception": type(exc).__name__})


def _seed_demos() -> dict[str, int]:
    from app.analysis import classify_failures
    from app.ingestion import normalize_trace
    from examples.generate import generate_demo_traces

    created = updated = 0
    db.init_db()
    with db.session_scope() as session:
        workspace = db.ensure_workspace(session, settings.default_workspace, "Default workspace")
        for raw in generate_demo_traces():
            trace = normalize_trace(raw, source_format="demo-json")
            original = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
            import hashlib

            is_new = db.save_trace(
                session,
                trace,
                raw,
                "demo-json",
                hashlib.sha256(original.encode()).hexdigest(),
                workspace.id,
            )
            created += int(is_new)
            updated += int(not is_new)
            db.replace_analysis(session, workspace.id, trace.run_id, classify_failures(trace), [])
    return {"created": created, "updated": updated, "total": created + updated}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    with db.session_scope() as session:
        db.ensure_workspace(session, settings.default_workspace, "Default workspace")
    if settings.seed_demo:
        try:
            result = _seed_demos()
            logger.info("demo.seed", extra={"tracelens_demo": result})
        except Exception:
            logger.exception("demo.seed_failed")
    retention_task = None
    if settings.trace_retention_days > 0:
        try:
            deleted = await asyncio.to_thread(_run_retention_cleanup)
            if deleted:
                logger.info("retention.cleanup", extra={"tracelens_deleted": deleted})
        except Exception as exc:
            logger.error("retention.cleanup_failed", extra={"tracelens_exception": type(exc).__name__})
        retention_task = asyncio.create_task(_retention_worker())
    try:
        yield
    finally:
        if retention_task is not None:
            retention_task.cancel()
            try:
                await retention_task
            except asyncio.CancelledError:
                pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="TraceLens API",
        description=(
            "Evidence-backed analysis of autonomous AI agent execution traces. "
            "Offline deterministic rules are the default; imported content is data and is never executed."
        ),
        version=settings.app_version,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Filename"],
    )
    app.add_middleware(SecurityAndLimitMiddleware)

    app.include_router(admin.router)
    app.include_router(traces.router)
    app.include_router(analysis.router)
    app.include_router(experiments.router)
    app.include_router(ingestion.router)
    app.include_router(ai.router)

    if FRONTEND_DIST.is_dir():
        assets = FRONTEND_DIST / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            """Serve the built single-page application."""
            if full_path.startswith("api/") or full_path.startswith("v1/"):
                return JSONResponse({"error": {"code": "not_found", "message": "Unknown API route."}}, status_code=404)
            candidate = (FRONTEND_DIST / full_path).resolve()
            try:
                candidate.relative_to(FRONTEND_DIST.resolve())
            except ValueError:
                candidate = FRONTEND_DIST / "index.html"
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        async def missing_frontend():
            return JSONResponse(
                {
                    "error": {
                        "code": "frontend_not_built",
                        "message": (
                            "The dashboard bundle is not built. Run 'npm --prefix frontend ci && npm --prefix frontend run build' "
                            "or use the Vite dev server on http://localhost:5173."
                        ),
                    }
                },
                status_code=503,
            )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(request: Request, exc: RequestValidationError):
        request_id = safe_request_id(request.headers.get("x-request-id"))
        raw_errors = exc.errors()
        # Deliberately omit Pydantic's `input` and `ctx` fields: validation failures must never
        # echo raw request values, which may include secrets or private agent/tool content.
        safe_errors = []
        for error in raw_errors[:20]:
            location = error.get("loc", ())
            source = location[0] if location and location[0] in {"body", "query", "path", "header", "cookie"} else "request"
            safe_errors.append({"source": source, "type": str(error.get("type", "validation_error"))[:80]})
        return JSONResponse(
            {
                "error": {
                    "code": "request_validation_failed",
                    "message": "Request does not match the API schema.",
                    "details": safe_errors,
                    "details_truncated": len(raw_errors) > 20,
                },
                "request_id": request_id,
            },
            status_code=422,
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        detail = exc.detail
        request_id = safe_request_id(request.headers.get("x-request-id"))
        if isinstance(detail, dict) and "code" in detail:
            body = {"error": detail, "request_id": request_id}
        else:
            body = {"error": {"code": "http_error", "message": str(detail)}, "request_id": request_id}
        return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        request_id = safe_request_id(request.headers.get("x-request-id"))
        logger.error(
            "request.unhandled_error",
            extra={"tracelens_request_id": request_id, "tracelens_exception": type(exc).__name__},
        )
        return JSONResponse(
            {
                "error": {
                    "code": "internal_error",
                    "message": "Unexpected server error. Check server logs using the request ID; trace payloads are not logged.",
                },
                "request_id": request_id,
            },
            status_code=500,
        )

    instrument_app(app)
    return app


app = create_app()
