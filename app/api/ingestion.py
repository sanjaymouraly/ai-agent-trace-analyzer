"""Ingestion endpoints: file upload, raw JSON, batch, validation, OTLP and demo seed."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app import db
from app.analysis import classify_failures
from app.api.deps import current_workspace, get_session
from app.config import settings
from app.ingestion import normalize_trace, parse_document
from app.schema import ExecutionTrace

logger = logging.getLogger("tracelens.ingestion")
router = APIRouter(tags=["ingestion"])


def _parse_import(data: bytes, filename: str) -> list[tuple[ExecutionTrace, Any]]:
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail={"code": "payload_too_large", "message": f"Upload exceeds the {settings.max_upload_bytes}-byte limit."},
        )
    try:
        records = parse_document(data, filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "trace_validation_failed", "message": str(exc)}) from exc
    if len(records) > settings.max_batch_records:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "batch_too_large",
                "message": f"Batch exceeds the {settings.max_batch_records}-record limit.",
            },
        )
    too_many = next((trace for trace, _ in records if len(trace.spans) > settings.max_spans_per_run), None)
    if too_many:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "span_limit_exceeded",
                "message": f"Run {too_many.run_id} exceeds configured span limit {settings.max_spans_per_run}.",
            },
        )
    if sum(len(trace.spans) for trace, _ in records) > settings.max_batch_spans:
        raise HTTPException(
            status_code=413,
            detail={"code": "batch_too_large", "message": f"Batch exceeds the {settings.max_batch_spans}-span processing cap."},
        )
    return records


async def _read_limited(request: Request) -> bytes:
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > settings.max_upload_bytes:
                raise HTTPException(
                    status_code=413,
                    detail={
                        "code": "payload_too_large",
                        "message": f"Upload exceeds the {settings.max_upload_bytes}-byte limit.",
                    },
                )
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail={"code": "invalid_content_length", "message": "Content-Length must be an integer."}
            ) from exc
    parts: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > settings.max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "payload_too_large",
                    "message": f"Upload exceeds the {settings.max_upload_bytes}-byte limit.",
                },
            )
        parts.append(chunk)
    return b"".join(parts)


def _save_records(session: Session, workspace_id: str, records: list[tuple[ExecutionTrace, Any]]) -> dict[str, Any]:
    inserted: list[str] = []
    updated: list[str] = []
    for trace, raw in records:
        serialized = json.dumps(raw, ensure_ascii=False, separators=(",", ":"), default=str)
        fmt = trace.provenance.get("source_format", "json")
        new = db.save_trace(
            session,
            trace,
            raw,
            fmt,
            hashlib.sha256(serialized.encode()).hexdigest(),
            workspace_id,
        )
        (inserted if new else updated).append(trace.run_id)
        # Pre-compute the deterministic findings so dashboards can aggregate cheaply.
        db.replace_analysis(
            session,
            workspace_id,
            trace.run_id,
            classify_failures(trace),
            [],
        )
    logger.info("trace import records=%d inserted=%d updated=%d", len(records), len(inserted), len(updated))
    return {
        "accepted": len(records),
        "inserted": inserted,
        "updated": updated,
        "workspace_id": workspace_id,
        "warnings": [
            {"run_id": trace.run_id, "warnings": trace.import_warnings} for trace, _ in records if trace.import_warnings
        ],
    }


@router.post("/api/v1/ingest")
async def ingest_json(
    request: Request,
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
):
    data = await _read_limited(request)
    records = _parse_import(data, request.headers.get("x-filename", "upload.json"))
    return _save_records(session, workspace_id, records)


@router.post("/api/v1/ingest/file")
async def ingest_file(
    file: UploadFile = File(...),  # noqa: B008
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
):
    filename = file.filename or "upload.json"
    data = await file.read(settings.max_upload_bytes + 1)
    await file.close()
    records = _parse_import(data, filename)
    result = _save_records(session, workspace_id, records)
    result["filename"] = Path(filename).name[:200]
    return result


@router.post("/api/v1/ingest/batch")
async def ingest_batch(
    request: Request,
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
):
    """Validated batch ingestion: each record is validated independently.

    A malformed record does not discard the valid ones; the response reports
    per-record outcomes.
    """
    data = await _read_limited(request)
    try:
        payload = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=422, detail={"code": "trace_validation_failed", "message": f"Invalid JSON payload: {exc}"}
        ) from exc
    if isinstance(payload, dict):
        raw_records = payload.get("traces")
        records_raw: list[Any] = raw_records if isinstance(raw_records, list) else [payload]
    elif isinstance(payload, list):
        records_raw = payload
    else:
        raise HTTPException(
            status_code=422,
            detail={"code": "trace_validation_failed", "message": "Batch payload must be a JSON array or object."},
        )
    accepted: list[tuple[ExecutionTrace, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, raw in enumerate(records_raw, 1):
        if not isinstance(raw, dict):
            rejected.append({"index": index, "code": "invalid_record", "message": "Record must be a JSON object."})
            continue
        try:
            trace = normalize_trace(raw, source_format="batch-json")
        except (ValueError, TypeError) as exc:
            rejected.append({"index": index, "code": "trace_validation_failed", "message": str(exc)})
            continue
        if len(trace.spans) > settings.max_spans_per_run:
            rejected.append(
                {
                    "index": index,
                    "code": "span_limit_exceeded",
                    "message": f"Run exceeds configured span limit {settings.max_spans_per_run}.",
                }
            )
            continue
        accepted.append((trace, raw))
    result = (
        _save_records(session, workspace_id, accepted)
        if accepted
        else {
            "accepted": 0,
            "inserted": [],
            "updated": [],
            "warnings": [],
        }
    )
    result["rejected"] = rejected
    result["submitted"] = len(records_raw)
    return result


@router.post("/api/v1/validate")
async def validate_import(request: Request):
    data = await _read_limited(request)
    records = _parse_import(data, request.headers.get("x-filename", "upload.json"))
    return {
        "valid": True,
        "record_count": len(records),
        "runs": [
            {
                "run_id": trace.run_id,
                "trace_id": trace.trace_id,
                "task": trace.task,
                "span_count": len(trace.spans),
                "status": trace.status.value,
                "warnings": trace.import_warnings,
            }
            for trace, _ in records
        ],
    }


@router.post("/v1/traces", summary="OTLP/HTTP JSON trace export ingestion")
async def ingest_otlp_http_json(
    request: Request,
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
):
    """Accept the OpenTelemetry JSON representation at the standard /v1/traces path.

    OTLP/HTTP JSON only; protobuf transport is not implemented in this release.
    """
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(
            status_code=415,
            detail={
                "code": "unsupported_media_type",
                "message": "This endpoint accepts OTLP/HTTP JSON (application/json) only; protobuf is unsupported.",
            },
        )
    data = await _read_limited(request)
    records = _parse_import(data, "otlp.json")
    if not records or any(trace.provenance.get("mapping") != "otlp-json" for trace, _ in records):
        raise HTTPException(
            status_code=422, detail={"code": "otlp_payload_required", "message": "Expected an OTLP JSON resourceSpans payload."}
        )
    return _save_records(session, workspace_id, records)


@router.post("/api/v1/demo/seed")
def seed_demo(
    session: Session = Depends(get_session),
    workspace_id: str = Depends(current_workspace),
):
    from examples.generate import generate_demo_traces

    created = updated = 0
    for raw in generate_demo_traces():
        trace = normalize_trace(raw, source_format="demo-json")
        original = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        is_new = db.save_trace(
            session,
            trace,
            raw,
            "demo-json",
            hashlib.sha256(original.encode()).hexdigest(),
            workspace_id,
        )
        created += int(is_new)
        updated += int(not is_new)
        db.replace_analysis(session, workspace_id, trace.run_id, classify_failures(trace), [])
    return {"created": created, "updated": updated, "total": created + updated}
