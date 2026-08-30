"""
backend/routers/ap_approval.py — AP invoice approval queue for strata managers.

# @featuretrace:ap_automation — invoice approval lifecycle and state transitions.
# Layer: router
# Data flow: invoice_documents ← this router ← APApprovalQueuePage.tsx.
# Related: backend/domain/invoice_lifecycle.py
#           backend/routers/ap_supplier_upload.py
#           frontend/src/pages/dashboard/financial/APApprovalQueuePage.tsx
# Scope: (building-scoped)

Endpoints:
  GET  /ap/invoices                      — paginated invoice list by state
  GET  /ap/invoices/{invoice_id}         — full invoice detail
  POST /ap/invoices/{invoice_id}/submit  — draft → submitted
  POST /ap/invoices/{invoice_id}/validate — submitted → validated
  POST /ap/invoices/{invoice_id}/approve — validated → approved (jurisdictional gate)
  POST /ap/invoices/{invoice_id}/reject  — any → rejected
  POST /ap/invoices/{invoice_id}/schedule — approved → scheduled
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from database import db
from domain.invoice_lifecycle import (
    InvoiceState,
    InvoiceTransitionError,
    transition_invoice,
)
from utils.auth import get_current_building
from utils.permissions import require_feature
# GAP-SEC-005 group 2, tier A. Added as an ADDITIONAL dependency alongside each
# route's existing check, never as a replacement — the effective rule is the
# intersection, so this can only narrow access. See
# docs/security/group2_financial_route_tiers_2026_08_24.md
from services.capability_registry import require_capability

logger = logging.getLogger(__name__)
router = APIRouter()

_MANAGER_ROLES = {"super_admin", "strata_admin", "strata_manager"}


# ── Request / Response models ─────────────────────────────────────────────────

class InvoiceSummary(BaseModel):
    invoice_id: str
    state: str
    vendor_name: Optional[str]
    vendor_abn: Optional[str]
    invoice_number: Optional[str]
    issue_date: Optional[str]
    due_date: Optional[str]
    total_cents: Optional[int]
    abn_valid: Optional[bool]
    withholding_required: bool
    created_at: str


class InvoiceDetail(InvoiceSummary):
    gst_cents: Optional[int]
    is_tax_invoice: bool
    description: Optional[str]
    category: Optional[str]
    ocr_confidence: Optional[float]
    low_confidence_fields: list[str]
    abn_entity_name: Optional[str]
    abn_status: Optional[str]
    transitions: list[dict]
    submitted_by: Optional[str]


class TransitionRequest(BaseModel):
    notes: Optional[str] = None
    lot_count: int = 1
    budget_line_current_cents: int = 0
    budget_line_cap_cents: int = 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_manager(current_user: dict) -> None:
    """Generated function header.

    Function: _require_manager
    Path: backend/routers/ap_approval.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = current_user.get("effective_role") or current_user.get("role", "guest")
    if role not in _MANAGER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only strata managers and above may manage invoice approvals.",
        )


def _parse_oid(invoice_id: str) -> ObjectId:
    """Generated function header.

    Function: _parse_oid
    Path: backend/routers/ap_approval.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return ObjectId(invoice_id)
    except (InvalidId, Exception):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")


def _doc_to_summary(doc: dict) -> InvoiceSummary:
    """Generated function header.

    Function: _doc_to_summary
    Path: backend/routers/ap_approval.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return InvoiceSummary(
        invoice_id=str(doc["_id"]),
        state=doc.get("state", ""),
        vendor_name=doc.get("vendor_name"),
        vendor_abn=doc.get("vendor_abn"),
        invoice_number=doc.get("invoice_number"),
        issue_date=doc.get("issue_date"),
        due_date=doc.get("due_date"),
        total_cents=doc.get("total_cents"),
        abn_valid=doc.get("abn_valid"),
        withholding_required=bool(doc.get("withholding_required")),
        created_at=doc.get("created_at", ""),
    )


def _doc_to_detail(doc: dict) -> InvoiceDetail:
    """Generated function header.

    Function: _doc_to_detail
    Path: backend/routers/ap_approval.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return InvoiceDetail(
        invoice_id=str(doc["_id"]),
        state=doc.get("state", ""),
        vendor_name=doc.get("vendor_name"),
        vendor_abn=doc.get("vendor_abn"),
        invoice_number=doc.get("invoice_number"),
        issue_date=doc.get("issue_date"),
        due_date=doc.get("due_date"),
        total_cents=doc.get("total_cents"),
        gst_cents=doc.get("gst_cents"),
        is_tax_invoice=bool(doc.get("is_tax_invoice")),
        description=doc.get("description"),
        category=doc.get("category"),
        abn_valid=doc.get("abn_valid"),
        abn_entity_name=doc.get("abn_entity_name"),
        abn_status=doc.get("abn_status"),
        withholding_required=bool(doc.get("withholding_required")),
        ocr_confidence=doc.get("ocr_confidence"),
        low_confidence_fields=doc.get("low_confidence_fields", []),
        transitions=doc.get("transitions", []),
        submitted_by=doc.get("submitted_by"),
        created_at=doc.get("created_at", ""),
    )


async def _get_or_404(invoice_id: str) -> dict:
    """Generated function header.

    Function: _get_or_404
    Path: backend/routers/ap_approval.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    oid = _parse_oid(invoice_id)
    doc = await db.invoice_documents.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return doc


def _transition_error_to_http(exc: InvoiceTransitionError) -> HTTPException:
    """Generated function header.

    Function: _transition_error_to_http
    Path: backend/routers/ap_approval.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    msg = str(exc)
    if "not permitted" in msg:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg)
    if "s.102" in msg or "BCCM" in msg:
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=msg)
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg)


# ── GET /ap/invoices ──────────────────────────────────────────────────────────

@router.get("/ap/invoices", response_model=list[InvoiceSummary])
async def list_invoices(
        invoice_state: str = Query("validated", alias="status"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(require_feature("ap_automation")),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: list_invoices
    Path: backend/routers/ap_approval.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_manager(current_user)
    docs = await db.invoice_documents.find(
        {"state": invoice_state, "is_test_data": {"$ne": True}},
        sort=[("due_date", 1)],
    ).skip((page - 1) * page_size).limit(page_size).to_list(page_size)
    return [_doc_to_summary(d) for d in docs]


# ── GET /ap/invoices/{invoice_id} ─────────────────────────────────────────────

@router.get("/ap/invoices/{invoice_id}", response_model=InvoiceDetail)
async def get_invoice(
        invoice_id: str,
        current_user: dict = Depends(require_feature("ap_automation")),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_invoice
    Path: backend/routers/ap_approval.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_manager(current_user)
    doc = await _get_or_404(invoice_id)
    return _doc_to_detail(doc)


# ── POST /ap/invoices/{invoice_id}/submit ─────────────────────────────────────

@router.post("/ap/invoices/{invoice_id}/submit")
async def submit_invoice(
        invoice_id: str,
        body: TransitionRequest = TransitionRequest(),
        current_user: dict = Depends(require_feature("ap_automation")),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: submit_invoice
    Path: backend/routers/ap_approval.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_manager(current_user)
    authorised_by = current_user.get("email") or str(current_user.get("_id", "unknown"))
    try:
        await transition_invoice(
            invoice_id, InvoiceState.SUBMITTED, building_id, authorised_by,
            db=db, notes=body.notes,
        )
    except InvoiceTransitionError as exc:
        raise _transition_error_to_http(exc)
    return {"status": "ok", "state": InvoiceState.SUBMITTED}


# ── POST /ap/invoices/{invoice_id}/validate ───────────────────────────────────

@router.post("/ap/invoices/{invoice_id}/validate")
async def validate_invoice(
        invoice_id: str,
        body: TransitionRequest = TransitionRequest(),
        current_user: dict = Depends(require_feature("ap_automation")),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: validate_invoice
    Path: backend/routers/ap_approval.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_manager(current_user)
    authorised_by = current_user.get("email") or str(current_user.get("_id", "unknown"))

    # Re-run duplicate detection before advancing to validated
    doc = await _get_or_404(invoice_id)
    from domain.duplicate_detection import check_duplicate
    dup = await check_duplicate(
        tenant_id=building_id,
        vendor_abn=doc.get("vendor_abn"),
        invoice_number=doc.get("invoice_number"),
        total_cents=doc.get("total_cents", 0),
        issue_date=doc.get("issue_date"),
        content_hash=doc.get("content_sha256"),
        db=db,
        exclude_id=invoice_id,
    )
    if dup.is_duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Duplicate detected during validation.", "reason": dup.reason,
                    "existing_invoice_id": dup.existing_invoice_id},
        )

    try:
        await transition_invoice(
            invoice_id, InvoiceState.VALIDATED, building_id, authorised_by,
            db=db, notes=body.notes,
        )
    except InvoiceTransitionError as exc:
        raise _transition_error_to_http(exc)
    return {"status": "ok", "state": InvoiceState.VALIDATED}


# ── POST /ap/invoices/{invoice_id}/approve ────────────────────────────────────

@router.post("/ap/invoices/{invoice_id}/approve")
async def approve_invoice(
        invoice_id: str,
        body: TransitionRequest = TransitionRequest(),
        current_user: dict = Depends(require_feature("ap_automation")),
        building_id: str = Depends(get_current_building),
        # Approves an invoice for payment. Already manager-only inline; the
        # capability adds a recorded Decision and its masking obligations.
        _cap: dict = Depends(require_capability("building.finance.manage", building_from_context=True)),
):
    """Generated function header.

    Function: approve_invoice
    Path: backend/routers/ap_approval.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_manager(current_user)
    authorised_by = current_user.get("email") or str(current_user.get("_id", "unknown"))
    try:
        await transition_invoice(
            invoice_id, InvoiceState.APPROVED, building_id, authorised_by,
            db=db,
            notes=body.notes,
            lot_count=body.lot_count,
            budget_line_current_cents=body.budget_line_current_cents,
            budget_line_cap_cents=body.budget_line_cap_cents,
        )
    except InvoiceTransitionError as exc:
        raise _transition_error_to_http(exc)
    return {"status": "ok", "state": InvoiceState.APPROVED}


# ── POST /ap/invoices/{invoice_id}/reject ─────────────────────────────────────

@router.post("/ap/invoices/{invoice_id}/reject")
async def reject_invoice(
        invoice_id: str,
        body: TransitionRequest = TransitionRequest(),
        current_user: dict = Depends(require_feature("ap_automation")),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: reject_invoice
    Path: backend/routers/ap_approval.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_manager(current_user)
    authorised_by = current_user.get("email") or str(current_user.get("_id", "unknown"))
    try:
        await transition_invoice(
            invoice_id, InvoiceState.REJECTED, building_id, authorised_by,
            db=db, notes=body.notes,
        )
    except InvoiceTransitionError as exc:
        raise _transition_error_to_http(exc)
    return {"status": "ok", "state": InvoiceState.REJECTED}


# ── POST /ap/invoices/{invoice_id}/schedule ───────────────────────────────────

@router.post("/ap/invoices/{invoice_id}/schedule")
async def schedule_invoice(
        invoice_id: str,
        body: TransitionRequest = TransitionRequest(),
        current_user: dict = Depends(require_feature("ap_automation")),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: schedule_invoice
    Path: backend/routers/ap_approval.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_manager(current_user)
    authorised_by = current_user.get("email") or str(current_user.get("_id", "unknown"))
    try:
        await transition_invoice(
            invoice_id, InvoiceState.SCHEDULED, building_id, authorised_by,
            db=db, notes=body.notes,
        )
    except InvoiceTransitionError as exc:
        raise _transition_error_to_http(exc)
    return {"status": "ok", "state": InvoiceState.SCHEDULED}
