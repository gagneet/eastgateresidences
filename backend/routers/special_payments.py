"""
GAP-FIN-011 (outstanding scope): Special Payment Approval Router.

Covers non-ABA high-value payments above $10,000 — e.g. large contractor invoices,
insurance premiums, emergency capital works. Payments >= SPECIAL_PAYMENT_DUAL_THRESHOLD
require two separate approvers; below that threshold a single approval suffices.

ABA batch dual-approval (>=5k) remains in routers/trust_accounting.py.
This router covers the `payment_approvals` collection.

Status flow:
  pending_approval → (first approve) → pending_second_approval | approved | rejected
  pending_second_approval → (second approve) → approved | rejected
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from models.payment_approvals import (
    PAYMENT_STATUSES,
    SPECIAL_PAYMENT_DUAL_THRESHOLD,
    SpecialPaymentApproval,
    SpecialPaymentCreate,
    SpecialPaymentResponse,
)
from utils.auth import get_current_building, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/special-payments", tags=["Finance"])

_SUBMIT_ROLES = {"super_admin", "strata_manager", "ec_member", "strata_admin", "treasurer", "admin"}
_APPROVE_ROLES = {"super_admin", "strata_manager", "ec_member", "strata_admin", "treasurer"}


def _require_submit(user: dict) -> dict:
    """Generated function header.

    Function: _require_submit
    Path: backend/routers/special_payments.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = user.get("effective_role") or user.get("role", "guest")
    if role not in _SUBMIT_ROLES:
        raise HTTPException(403, "Finance access required to submit special payments.")
    return user


def _require_approve(user: dict) -> dict:
    """Generated function header.

    Function: _require_approve
    Path: backend/routers/special_payments.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = user.get("effective_role") or user.get("role", "guest")
    if role not in _APPROVE_ROLES:
        raise HTTPException(403, "Treasurer or manager role required to approve payments.")
    return user


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/special_payments.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/routers/special_payments.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _doc_to_response(doc: dict) -> SpecialPaymentResponse:
    """Generated function header.

    Function: _doc_to_response
    Path: backend/routers/special_payments.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc_id = str(doc.get("_id", doc.get("id", "")))
    return SpecialPaymentResponse(
        id=doc_id,
        building_id=doc["building_id"],
        payee_name=doc["payee_name"],
        amount=doc["amount"],
        category=doc["category"],
        description=doc["description"],
        status=doc["status"],
        requires_dual_approval=doc.get("requires_dual_approval", False),
        submitted_by=doc.get("submitted_by"),
        first_approved_by=doc.get("first_approved_by"),
        first_approved_at=doc.get("first_approved_at"),
        second_approved_by=doc.get("second_approved_by"),
        second_approved_at=doc.get("second_approved_at"),
        rejected_by=doc.get("rejected_by"),
        rejection_reason=doc.get("rejection_reason"),
        payment_date=doc.get("payment_date"),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


@router.post("", response_model=SpecialPaymentResponse, status_code=201)
async def create_special_payment(
        payload: SpecialPaymentCreate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Submit a new special payment request. Created directly as pending_approval."""
    _require_submit(current_user)
    db = _get_db()

    now = _now()
    requires_dual = payload.amount >= SPECIAL_PAYMENT_DUAL_THRESHOLD
    user_id = current_user.get("id") or current_user.get("_id", "unknown")

    doc = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "payee_name": payload.payee_name,
        "payee_bsb": payload.payee_bsb,
        "payee_account_number": payload.payee_account_number,
        "amount": payload.amount,
        "category": payload.category,
        "description": payload.description,
        "invoice_reference": payload.invoice_reference,
        "invoice_url": payload.invoice_url,
        "payment_date": payload.payment_date,
        "fund_type": payload.fund_type,
        "supporting_documents": payload.supporting_documents,
        "notes": payload.notes,
        "status": "pending_approval",
        "requires_dual_approval": requires_dual,
        "submitted_by": user_id,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.payment_approvals.insert_one(doc)
    doc["_id"] = result.inserted_id
    logger.info("Special payment created: %s (building %s, amount %.2f)", doc["id"], building_id, payload.amount)
    return _doc_to_response(doc)


@router.get("", response_model=dict)
async def list_special_payments(
        status: Optional[str] = None,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """List special payment requests for this building, optionally filtered by status."""
    _require_submit(current_user)
    db = _get_db()

    query: dict = {"building_id": building_id}
    if status:
        if status not in PAYMENT_STATUSES:
            raise HTTPException(400, f"Invalid status. Must be one of: {PAYMENT_STATUSES}")
        query["status"] = status

    cursor = db.payment_approvals.find(query).sort("created_at", -1)
    docs = await cursor.to_list(200)
    return {"data": [_doc_to_response(d).model_dump() for d in docs], "total": len(docs)}


@router.get("/{payment_id}", response_model=SpecialPaymentResponse)
async def get_special_payment(
        payment_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Retrieve a single special payment by ID."""
    _require_submit(current_user)
    db = _get_db()

    doc = await db.payment_approvals.find_one({"id": payment_id, "building_id": building_id})
    if not doc:
        raise HTTPException(404, "Special payment not found.")
    return _doc_to_response(doc)


@router.post("/{payment_id}/approve", response_model=SpecialPaymentResponse)
async def approve_special_payment(
        payment_id: str,
        payload: SpecialPaymentApproval,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """
    First approval action. Approving moves to pending_second_approval (if dual required)
    or directly to approved (if single approval suffices). Rejecting moves to rejected.
    """
    _require_approve(current_user)
    db = _get_db()

    doc = await db.payment_approvals.find_one({"id": payment_id, "building_id": building_id})
    if not doc:
        raise HTTPException(404, "Special payment not found.")
    if doc["status"] != "pending_approval":
        raise HTTPException(409, f"Payment is in '{doc['status']}' — expected pending_approval.")

    if not payload.approved and not payload.reason:
        raise HTTPException(422, "Rejection reason is required when rejecting.")

    user_id = current_user.get("id") or current_user.get("_id", "unknown")
    now = _now()

    if payload.approved:
        new_status = "pending_second_approval" if doc.get("requires_dual_approval") else "approved"
        updates = {
            "status": new_status,
            "first_approved_by": user_id,
            "first_approved_at": now,
            "updated_at": now,
        }
    else:
        updates = {
            "status": "rejected",
            "rejected_by": user_id,
            "rejection_reason": payload.reason,
            "updated_at": now,
        }

    await db.payment_approvals.update_one({"id": payment_id, "building_id": building_id}, {"$set": updates})
    doc.update(updates)
    logger.info("Special payment %s %s by %s", payment_id, updates["status"], user_id)
    return _doc_to_response(doc)


@router.post("/{payment_id}/second-approve", response_model=SpecialPaymentResponse)
async def second_approve_special_payment(
        payment_id: str,
        payload: SpecialPaymentApproval,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """
    Second approval action. Only valid for payments in pending_second_approval.
    The second approver cannot be the same person as the first approver.
    """
    _require_approve(current_user)
    db = _get_db()

    doc = await db.payment_approvals.find_one({"id": payment_id, "building_id": building_id})
    if not doc:
        raise HTTPException(404, "Special payment not found.")
    if doc["status"] != "pending_second_approval":
        raise HTTPException(409, f"Payment is in '{doc['status']}' — expected pending_second_approval.")

    user_id = current_user.get("id") or current_user.get("_id", "unknown")
    if doc.get("first_approved_by") == user_id:
        raise HTTPException(403, "Second approver must be a different person from the first approver.")

    if not payload.approved and not payload.reason:
        raise HTTPException(422, "Rejection reason is required when rejecting.")

    now = _now()
    if payload.approved:
        updates = {
            "status": "approved",
            "second_approved_by": user_id,
            "second_approved_at": now,
            "updated_at": now,
        }
    else:
        updates = {
            "status": "rejected",
            "rejected_by": user_id,
            "rejection_reason": payload.reason,
            "updated_at": now,
        }

    await db.payment_approvals.update_one({"id": payment_id, "building_id": building_id}, {"$set": updates})
    doc.update(updates)
    logger.info("Special payment %s final status: %s (by %s)", payment_id, updates["status"], user_id)
    return _doc_to_response(doc)


@router.post("/{payment_id}/cancel", response_model=SpecialPaymentResponse)
async def cancel_special_payment(
        payment_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Cancel a pending payment. Only the submitter or a manager can cancel."""
    _require_submit(current_user)
    db = _get_db()

    doc = await db.payment_approvals.find_one({"id": payment_id, "building_id": building_id})
    if not doc:
        raise HTTPException(404, "Special payment not found.")
    if doc["status"] in ("approved", "rejected", "cancelled"):
        raise HTTPException(409, f"Cannot cancel a payment in '{doc['status']}' status.")

    user_id = current_user.get("id") or current_user.get("_id", "unknown")
    role = current_user.get("effective_role") or current_user.get("role", "guest")
    is_manager = role in _APPROVE_ROLES
    if doc.get("submitted_by") != user_id and not is_manager:
        raise HTTPException(403, "Only the submitter or a manager can cancel this payment.")

    now = _now()
    updates = {"status": "cancelled", "updated_at": now}
    await db.payment_approvals.update_one({"id": payment_id, "building_id": building_id}, {"$set": updates})
    doc.update(updates)
    return _doc_to_response(doc)
