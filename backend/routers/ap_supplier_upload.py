"""
backend/routers/ap_supplier_upload.py — Supplier invoice upload endpoint.

# @featuretrace:ap_automation — inbound AP invoice ingestion via OCR pipeline.
# Layer: router
# Data flow: supplier PDF → OCR extraction → ABN validation → duplicate check
#            → invoice_documents (draft) → AP approval queue.
# Related: backend/domain/invoice_lifecycle.py
#           backend/domain/duplicate_detection.py
#           backend/integrations/abn_validator.py
#           frontend/src/pages/supplier/InvoiceUploadPage.tsx
# Scope: (building-scoped)

POST /api/ap/supplier-upload/
  — requires can_submit_supplier_invoice permission
  — accepts multipart PDF upload
  — runs OCR, ABN validation, duplicate detection
  — creates draft invoice in invoice_documents
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from typing import Optional

from database import db
from integrations.registry import get_provider_registry
from utils.auth import get_current_building
from utils.permissions import get_user_permissions, require_feature

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB
_ALLOWED_MIME = {"application/pdf", "text/plain"}


# ── Response models ───────────────────────────────────────────────────────────

class UploadedInvoiceResponse(BaseModel):
    invoice_id: str
    state: str
    vendor_name: Optional[str]
    vendor_abn: Optional[str]
    invoice_number: Optional[str]
    issue_date: Optional[str]
    due_date: Optional[str]
    total_cents: Optional[int]
    gst_cents: Optional[int]
    is_tax_invoice: bool
    abn_valid: Optional[bool]
    abn_entity_name: Optional[str]
    withholding_required: bool
    duplicate_check: dict
    overall_ocr_confidence: float
    low_confidence_fields: list[str]


# ── POST /api/ap/supplier-upload/ ─────────────────────────────────────────────

@router.post("/ap/supplier-upload/", response_model=UploadedInvoiceResponse,
             status_code=status.HTTP_201_CREATED)
async def supplier_upload(
        file: UploadFile = File(...),
        current_user: dict = Depends(require_feature("ap_automation")),
        building_id: str = Depends(get_current_building),
):
    """Accept a supplier PDF, run OCR and validation, return draft invoice."""
    perms = get_user_permissions(current_user)
    if not perms.can_submit_supplier_invoice:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="You do not have permission to submit supplier invoices.")

    # Validate file type
    content_type = file.content_type or ""
    if content_type not in _ALLOWED_MIME:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported file type {content_type!r}. Upload a PDF or plain-text invoice.",
        )

    raw_bytes = await file.read()
    if len(raw_bytes) > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum size of {_MAX_FILE_BYTES // 1024 // 1024} MB.",
        )

    # ── OCR extraction ────────────────────────────────────────────────────────
    try:
        ocr = await get_provider_registry().get_ocr(building_id)
        extraction = await ocr.extract_invoice(raw_bytes, content_type)
    except Exception as e:
        logger.error("Supplier upload OCR extraction failed for building %s: %s", building_id, e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Invoice OCR extraction is temporarily unavailable. Please try again shortly.",
        )

    vendor_name = extraction.vendor_name.value
    vendor_abn = extraction.vendor_abn.value
    invoice_num = extraction.invoice_number.value
    issue_date = extraction.issue_date.value
    due_date = extraction.due_date.value
    total_cents = extraction.total_cents
    gst_cents = extraction.gst_cents
    is_tax_inv = extraction.is_tax_invoice
    raw_text = extraction.raw_text or ""
    overall_conf = extraction.overall_confidence

    low_conf = [
        field for field, fr in [
            ("vendor_name", extraction.vendor_name),
            ("vendor_abn", extraction.vendor_abn),
            ("invoice_number", extraction.invoice_number),
            ("issue_date", extraction.issue_date),
            ("due_date", extraction.due_date),
        ]
        if fr.confidence < 0.70
    ]

    # ── Content SHA-256 ───────────────────────────────────────────────────────
    from domain.duplicate_detection import content_sha256
    c_hash = content_sha256(raw_text)

    # ── ABN validation ────────────────────────────────────────────────────────
    from integrations.abn_validator import validate_abn
    abn_result = await validate_abn(
        vendor_abn or "",
        supply_amount_ex_gst_cents=(total_cents or 0) - (gst_cents or 0),
        db=db,
    )

    # ── Duplicate detection ───────────────────────────────────────────────────
    from domain.duplicate_detection import check_duplicate
    dup_result = await check_duplicate(
        tenant_id=building_id,
        vendor_abn=vendor_abn,
        invoice_number=invoice_num,
        total_cents=total_cents or 0,
        issue_date=issue_date,
        content_hash=c_hash,
        db=db,
    )

    if dup_result.is_duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Duplicate invoice detected.",
                "layer": dup_result.layer,
                "existing_invoice_id": dup_result.existing_invoice_id,
                "reason": dup_result.reason,
            },
        )

    # ── Create draft invoice document ─────────────────────────────────────────
    now = datetime.now(timezone.utc).isoformat()
    uploader_id = current_user.get("email") or str(current_user.get("_id", "unknown"))

    doc = {
        "tenant_id": building_id,
        "building_id": building_id,
        "state": "draft",
        "vendor_name": vendor_name,
        "vendor_abn": vendor_abn,
        "invoice_number": invoice_num,
        "issue_date": issue_date,
        "due_date": due_date,
        "total_cents": total_cents,
        "gst_cents": gst_cents,
        "is_tax_invoice": is_tax_inv,
        "description": None,
        "category": None,
        "template_id": None,
        "idempotency_key": None,
        "content_sha256": c_hash,
        "ocr_confidence": overall_conf,
        "low_confidence_fields": low_conf,
        "abn_valid": abn_result.is_valid,
        "abn_entity_name": abn_result.entity_name,
        "abn_status": abn_result.abn_status,
        "withholding_required": abn_result.withholding_required,
        "gst_registered": abn_result.gst_registered,
        "submitted_by": uploader_id,
        "transitions": [{
            "from_state": None,
            "to_state": "draft",
            "transitioned_at": now,
            "transitioned_by": uploader_id,
            "notes": "Created via supplier upload",
        }],
        "candidates_snapshot": None,
        "created_at": now,
        "updated_at": now,
        "is_test_data": False,
    }
    result = await db.invoice_documents.insert_one(doc)
    invoice_id = str(result.inserted_id)

    logger.info(
        "Draft invoice created: id=%s building=%s vendor=%s abn_valid=%s dup_layer=%s",
        invoice_id, building_id, vendor_name, abn_result.is_valid, dup_result.layer,
    )

    return UploadedInvoiceResponse(
        invoice_id=invoice_id,
        state="draft",
        vendor_name=vendor_name,
        vendor_abn=vendor_abn,
        invoice_number=invoice_num,
        issue_date=issue_date,
        due_date=due_date,
        total_cents=total_cents,
        gst_cents=gst_cents,
        is_tax_invoice=is_tax_inv,
        abn_valid=abn_result.is_valid,
        abn_entity_name=abn_result.entity_name,
        withholding_required=abn_result.withholding_required,
        duplicate_check={"is_duplicate": False, "layer": None},
        overall_ocr_confidence=overall_conf,
        low_confidence_fields=low_conf,
    )
