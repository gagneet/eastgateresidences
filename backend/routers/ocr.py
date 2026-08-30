"""Building-scoped OCR routes.

# @featuretrace:document_ocr — Authenticated OCR workspace for documents, invoices, and meeting inputs.
# Layer: router
# Data flow: OCRPage.jsx → /ocr/jobs → scan_upload → ProviderRegistry.get_ocr(building_id)
#            → ocr_jobs / ocr_results.
# Related: backend/services/ocr/unlimited_ocr_client.py
#           backend/integrations/registry.py
#           frontend/src/pages/dashboard/OCRPage.jsx
# Scope: (building-scoped)
"""
from __future__ import annotations

import base64
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from database import db
from integrations.registry import get_provider_registry
from services.ocr.unlimited_ocr_client import UNLIMITED_OCR, get_unlimited_ocr_config, raw_text_sha256
from utils.auth import get_current_building
from utils.file_scan import scan_upload
from utils.permissions import require_feature

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ocr")

_ALLOWED_MIME = {"application/pdf", "image/jpeg", "image/png", "image/webp", "text/plain"}
_SOURCE_TYPES = {"general_document", "invoice", "quote", "bank_statement", "agm_minutes", "rates_notice", "insurance", "contract", "maintenance_evidence"}


class OCRProviderHealth(BaseModel):
    id: str
    label: str
    available: bool
    selectable: bool
    runtime_mode: str
    unavailable_reason: Optional[str] = None
    max_pages: int
    max_bytes: int
    timeout_seconds: int


class OCRJobResponse(BaseModel):
    id: str
    building_id: str
    source_type: str
    source_collection: Optional[str] = None
    source_document_id: Optional[str] = None
    provider: str
    requested_provider: str
    status: str
    mime_type: str
    file_name: str
    file_size_bytes: int
    requested_by: str
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    result_ref: Optional[str] = None
    raw_text_sha256: Optional[str] = None
    model_version: Optional[str] = None
    runtime_mode: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)


class OCRResultResponse(BaseModel):
    job: OCRJobResponse
    result: Optional[dict[str, Any]] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _actor_id(current_user: dict) -> str:
    return str(current_user.get("id") or current_user.get("_id") or current_user.get("email") or "unknown")


def _can_use_ocr(current_user: dict) -> bool:
    role = current_user.get("effective_role") or current_user.get("role", "guest")
    return role in {"super_admin", "strata_admin", "strata_manager", "ec_member", "owner", "tenant", "real_estate_agent", "admin_staff"}


def _serialize_job(doc: dict[str, Any]) -> OCRJobResponse:
    copied = dict(doc)
    copied.pop("_id", None)
    return OCRJobResponse(**copied)


@router.get("/providers", response_model=list[OCRProviderHealth])
async def list_ocr_providers(
        current_user: dict = Depends(require_feature("document_ocr")),
        building_id: str = Depends(get_current_building),
):
    if not _can_use_ocr(current_user):
        raise HTTPException(status_code=403, detail="OCR access required")

    registry = get_provider_registry()
    registered = registry.list_registered().get("ocr", [])
    unlimited = get_unlimited_ocr_config()
    return [
        OCRProviderHealth(
            id="mock_ocr",
            label="Mock OCR (deterministic fallback)",
            available="mock_ocr" in registered,
            selectable=False,
            runtime_mode="fallback",
            unavailable_reason=None if "mock_ocr" in registered else "Fallback OCR provider is not registered",
            max_pages=1,
            max_bytes=20 * 1024 * 1024,
            timeout_seconds=30,
        ),
        OCRProviderHealth(
            id=UNLIMITED_OCR,
            label="Unlimited OCR (Advanced Document Parsing)",
            available=unlimited.is_available and UNLIMITED_OCR in registered,
            selectable=unlimited.is_available and UNLIMITED_OCR in registered,
            runtime_mode=unlimited.backend,
            unavailable_reason=None if unlimited.is_available and UNLIMITED_OCR in registered else (unlimited.unavailable_reason or "Provider is not registered"),
            max_pages=unlimited.max_pages,
            max_bytes=unlimited.max_bytes,
            timeout_seconds=unlimited.timeout_seconds,
        ),
    ]


async def _run_ocr_job(
        *, job_id: str, building_id: str, raw_bytes: bytes, content_type: str, file_name: str,
        requested_provider: str, source_document_id: Optional[str], source_collection: Optional[str],
        actor: str, is_test_data: bool,
) -> None:
    """Runs the actual OCR extraction out-of-band via FastAPI BackgroundTasks — create_ocr_job's
    HTTP response must never block on a potentially long-running provider call (the
    private_endpoint backend's default timeout is 1200s). Per the Architecture Decision in
    tasks/GAP-OCR-001-unlimited-ocr-provider-per-building.md, OCR inference must never run
    synchronously inside the main request/response cycle, and OCR failures must not destabilize
    the endpoint that queued them — this function owns its own try/except and always leaves the
    job in a terminal status (completed/needs_review/failed), never raises back to the caller."""
    registry = get_provider_registry()
    result_id = str(uuid.uuid4())
    try:
        ocr = await registry.get_ocr(building_id)
        extraction = await ocr.extract_invoice(raw_bytes, content_type)
        raw_hash = raw_text_sha256(extraction)
        result_doc = {
            "id": result_id,
            "building_id": building_id,
            "job_id": job_id,
            "provider": extraction.provider,
            "mime_type": content_type,
            "file_name": file_name,
            "result": extraction.model_dump(),
            "raw_text_sha256": raw_hash,
            "source_document_id": source_document_id,
            "source_collection": source_collection,
            "created_at": _now(),
            "created_by": actor,
            "is_test_data": is_test_data,
        }
        await db.ocr_results.insert_one(result_doc)
        update = {
            "provider": extraction.provider,
            "status": "needs_review" if extraction.overall_confidence < 0.85 else "completed",
            "completed_at": _now(),
            "result_ref": result_id,
            "raw_text_sha256": raw_hash,
            "confidence": extraction.overall_confidence,
        }
    except Exception as exc:
        update = {
            "provider": requested_provider,
            "status": "failed",
            "completed_at": _now(),
            "error_code": "ocr_provider_failed",
            "error_message": str(exc),
            "confidence": 0.0,
        }

    try:
        await db.ocr_jobs.update_one({"id": job_id, "building_id": building_id}, {"$set": update})
    except Exception:
        # If even the terminal-status write itself fails (transient DB error), the job would
        # otherwise be stuck in "processing" forever — silently contradicting this function's
        # own "always reaches a terminal status" contract. There is no further fallback store
        # to write to; log at CRITICAL so it surfaces to alerting rather than vanishing, and
        # the frontend's bounded poll (~60s) still gives up gracefully instead of hanging.
        logger.critical(
            "OCR job %s (building=%s) failed to persist its terminal status %r — job will "
            "remain stuck in a non-terminal status until manually corrected.",
            job_id, building_id, update.get("status"), exc_info=True,
        )


@router.post("/jobs", response_model=OCRJobResponse, status_code=status.HTTP_201_CREATED)
async def create_ocr_job(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        source_type: str = Form("general_document"),
        source_collection: Optional[str] = Form(None),
        source_document_id: Optional[str] = Form(None),
        provider: str = Form("auto"),
        is_test_data: bool = Form(False),
        current_user: dict = Depends(require_feature("document_ocr")),
        building_id: str = Depends(get_current_building),
):
    if not _can_use_ocr(current_user):
        raise HTTPException(status_code=403, detail="OCR access required")
    if source_type not in _SOURCE_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid source_type. Valid options: {sorted(_SOURCE_TYPES)}")

    content_type = file.content_type or "application/octet-stream"
    if content_type not in _ALLOWED_MIME:
        raise HTTPException(status_code=422, detail=f"Unsupported file type {content_type!r}")

    raw_bytes = await file.read()
    unlimited = get_unlimited_ocr_config()
    max_bytes = unlimited.max_bytes if provider == UNLIMITED_OCR else 20 * 1024 * 1024
    if len(raw_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds maximum OCR size of {max_bytes} bytes")

    await scan_upload(raw_bytes, context="ocr", filename=file.filename or "ocr-upload")

    requested_provider = provider
    started_at = _now()
    job_id = str(uuid.uuid4())
    actor = _actor_id(current_user)
    file_name = file.filename or "ocr-upload"

    job = {
        "id": job_id,
        "building_id": building_id,
        "source_type": source_type,
        "source_collection": source_collection,
        "source_document_id": source_document_id,
        "provider": "pending",
        "requested_provider": requested_provider,
        "status": "processing",
        "mime_type": content_type,
        "file_name": file_name,
        "file_size_bytes": len(raw_bytes),
        "page_count": None,
        "requested_by": actor,
        "created_at": started_at,
        "started_at": started_at,
        "completed_at": None,
        "error_code": None,
        "error_message": None,
        "result_ref": None,
        "raw_text_sha256": None,
        "model_version": unlimited.model_version if requested_provider == UNLIMITED_OCR else None,
        "runtime_mode": unlimited.default_mode if requested_provider == UNLIMITED_OCR else "sync",
        "confidence": 0.0,
        "is_test_data": is_test_data,
    }
    await db.ocr_jobs.insert_one(job)

    # Dispatched, not awaited — the HTTP response returns immediately with status="processing";
    # the frontend polls GET /ocr/jobs/{id} until a terminal status is reached. See _run_ocr_job's
    # own docstring for why this must never be awaited inline here.
    background_tasks.add_task(
        _run_ocr_job,
        job_id=job_id, building_id=building_id, raw_bytes=raw_bytes, content_type=content_type,
        file_name=file_name, requested_provider=requested_provider,
        source_document_id=source_document_id, source_collection=source_collection,
        actor=actor, is_test_data=is_test_data,
    )
    return _serialize_job(job)


@router.get("/jobs", response_model=list[OCRJobResponse])
async def list_ocr_jobs(
        limit: int = 25,
        current_user: dict = Depends(require_feature("document_ocr")),
        building_id: str = Depends(get_current_building),
):
    if not _can_use_ocr(current_user):
        raise HTTPException(status_code=403, detail="OCR access required")
    cursor = db.ocr_jobs.find({"building_id": building_id, "is_test_data": {"$ne": True}}).sort("created_at", -1)
    docs = await cursor.to_list(max(1, min(limit, 100)))
    return [_serialize_job(doc) for doc in docs]


@router.get("/jobs/{job_id}", response_model=OCRResultResponse)
async def get_ocr_job(
        job_id: str,
        current_user: dict = Depends(require_feature("document_ocr")),
        building_id: str = Depends(get_current_building),
):
    if not _can_use_ocr(current_user):
        raise HTTPException(status_code=403, detail="OCR access required")
    job = await db.ocr_jobs.find_one({"id": job_id, "building_id": building_id, "is_test_data": {"$ne": True}})
    if not job:
        raise HTTPException(status_code=404, detail="OCR job not found")
    result = None
    if job.get("result_ref"):
        result_doc = await db.ocr_results.find_one({"id": job["result_ref"], "building_id": building_id})
        if result_doc:
            result = {
                "id": result_doc.get("id"),
                "provider": result_doc.get("provider"),
                "raw_text_sha256": result_doc.get("raw_text_sha256"),
                "result": result_doc.get("result"),
            }
    return OCRResultResponse(job=_serialize_job(job), result=result)


@router.post("/jobs/{job_id}/publish-document")
async def publish_ocr_result_to_documents(
        job_id: str,
        title: Optional[str] = Form(None),
        category: str = Form("ocr_extracted"),
        current_user: dict = Depends(require_feature("document_ocr")),
        building_id: str = Depends(get_current_building),
):
    if not _can_use_ocr(current_user):
        raise HTTPException(status_code=403, detail="OCR access required")
    # Not filtered on is_test_data here (unlike list/get) — a test/k6 script exercising the full
    # upload->extract->publish pipeline needs this step reachable for a job it created; the
    # resulting document is itself correctly stamped is_test_data below, same as the job it came
    # from, so it stays excluded from real document-library listings.
    job = await db.ocr_jobs.find_one({"id": job_id, "building_id": building_id})
    if not job or not job.get("result_ref"):
        raise HTTPException(status_code=404, detail="Completed OCR result not found")
    result_doc = await db.ocr_results.find_one({"id": job["result_ref"], "building_id": building_id})
    if not result_doc:
        raise HTTPException(status_code=404, detail="OCR result not found")

    result_payload = result_doc.get("result") or {}
    raw_text = result_payload.get("raw_text") or ""
    document_id = str(uuid.uuid4())
    now = _now()
    content = raw_text.encode("utf-8")
    await db.documents.insert_one({
        "id": document_id,
        "building_id": building_id,
        "title": title or f"OCR extraction - {job.get('file_name')}",
        "description": f"OCR text extracted from job {job_id}",
        "category": category,
        "folder_path": "/OCR",
        "filename": f"{job_id}.txt",
        "file_name": f"{job_id}.txt",
        "content_type": "text/plain",
        "mime_type": "text/plain",
        "file_size": len(content),
        "file_size_bytes": len(content),
        "file_data": base64.b64encode(content).decode("utf-8"),
        "content_hash": hashlib.sha256(content).hexdigest(),
        "source": "document_ocr",
        "source_ocr_job_id": job_id,
        "uploaded_by": _actor_id(current_user),
        "created_by": _actor_id(current_user),
        "created_at": now,
        "updated_at": now,
        "is_private": True,
        "is_test_data": bool(job.get("is_test_data", False)),
    })
    await db.ocr_jobs.update_one(
        {"id": job_id, "building_id": building_id},
        {"$set": {"source_collection": "documents", "source_document_id": document_id, "updated_at": now}},
    )
    return {"document_id": document_id, "source_collection": "documents", "source_document_id": document_id}

