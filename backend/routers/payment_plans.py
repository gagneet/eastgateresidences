"""
GAP-JUR-NSW-002: NSW Form 1 Payment Plan (ss.83A–83C SSMA 2015).

When an owner is in arrears, they may request a formal payment plan. The owners
corporation must respond within 28 days. This router manages the full lifecycle.

Endpoints:
  POST   /payment-plans                    — owner or manager submits a plan request
  GET    /payment-plans                    — list plans (managers see all; owners see own)
  GET    /payment-plans/{id}               — get single plan
  POST   /payment-plans/{id}/approve       — manager approves and sets instalment schedule
  POST   /payment-plans/{id}/decline       — manager declines with reason
  PUT    /payment-plans/{id}/status        — update plan status (active → completed/cancelled)

Status flow:
  requested → approved → active → completed
           ↘ declined
           (any non-terminal) → cancelled
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Literal

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict

from utils.auth import get_current_user, get_current_building
from utils.helpers import create_audit_log
from utils.permissions import require_feature
# GAP-SEC-005 group 2, tier A. Added as an ADDITIONAL dependency alongside each
# route's existing check, never as a replacement — the effective rule is the
# intersection, so this can only narrow access. See
# docs/security/group2_financial_route_tiers_2026_08_24.md
from services.capability_registry import require_capability

logger = logging.getLogger(__name__)
# GAP-FT-004: Gate the payment plans router behind the payment_plans toggle.
router = APIRouter(
    prefix="/payment-plans",
    tags=["Finance"],
    dependencies=[Depends(require_feature("payment_plans"))],
)

PLAN_STATUSES = ["requested", "approved", "declined", "active", "completed", "cancelled"]
INSTALMENT_FREQUENCIES = ["weekly", "fortnightly", "monthly"]

_MANAGER_ROLES = {"super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"}


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/payment_plans.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/payment_plans.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_manager(user: dict) -> dict:
    """Generated function header.

    Function: _require_manager
    Path: backend/routers/payment_plans.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = user.get("effective_role") or user.get("role", "guest")
    if role not in _MANAGER_ROLES:
        raise HTTPException(403, "Manager access required.")
    return user


def _can_view(user: dict, plan: dict) -> bool:
    """Managers see all plans; owners see only their own."""
    role = user.get("effective_role") or user.get("role", "guest")
    if role in _MANAGER_ROLES:
        return True
    return plan.get("owner_id") == user.get("id")


# ── Models ────────────────────────────────────────────────────────────────────

class PaymentPlanCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    unit_number: str = Field(..., description="The lot/unit with arrears")
    outstanding_amount_cents: int = Field(..., gt=0, description="Total arrears in cents")
    requested_instalment_cents: int = Field(..., gt=0, description="Proposed instalment per period (cents)")
    instalment_frequency: str = Field(..., description=f"One of: {INSTALMENT_FREQUENCIES}")
    proposed_start_date: str = Field(..., description="ISO-8601 proposed start date")
    hardship_reason: Optional[str] = Field(None, max_length=2000, description="Optional hardship statement")
    notes: Optional[str] = Field(None, max_length=1000)


class PaymentPlanApprove(BaseModel):
    model_config = ConfigDict(extra="ignore")

    approved_instalment_cents: int = Field(..., gt=0)
    instalment_frequency: str = Field(..., description=f"One of: {INSTALMENT_FREQUENCIES}")
    instalment_count: int = Field(..., gt=0, description="Number of instalments in the plan")
    start_date: str = Field(..., description="ISO-8601 start date")
    notes: Optional[str] = Field(None, max_length=1000)


class PaymentPlanDecline(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reason: str = Field(..., min_length=10, max_length=2000)


class PaymentPlanStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["active", "completed", "cancelled"]
    notes: Optional[str] = None


class PaymentPlanResponse(BaseModel):
    """Full payment plan document — return shape for POST and GET endpoints."""
    model_config = ConfigDict(extra="ignore")

    id: str
    building_id: str
    unit_number: str
    owner_id: Optional[str] = None
    owner_name: Optional[str] = None
    outstanding_amount_cents: int
    requested_instalment_cents: int
    instalment_frequency: str
    proposed_start_date: str
    hardship_reason: Optional[str] = None
    notes: Optional[str] = None
    status: str
    response_due_by: Optional[str] = None
    approved_instalment_cents: Optional[int] = None
    instalment_count: Optional[int] = None
    start_date: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    declined_by: Optional[str] = None
    declined_at: Optional[str] = None
    decline_reason: Optional[str] = None
    submitted_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: str
    updated_at: str


class PaymentPlanActionResponse(BaseModel):
    """Ack for approve/decline/status-update endpoints."""
    model_config = ConfigDict(extra="ignore")

    message: str
    plan_id: str


def _clean(doc: dict) -> dict:
    """Generated function header.

    Function: _clean
    Path: backend/routers/payment_plans.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc.pop("_id", None)  # strip MongoDB internal id; keep "id" UUID field
    return doc


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("", status_code=201, response_model=PaymentPlanResponse)
async def create_payment_plan(
        payload: PaymentPlanCreate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Submit a NSW Form 1 payment plan request (s.83A SSMA 2015).

    Any authenticated user may submit for their own unit. Managers may submit
    on behalf of an owner. The owners corporation has 28 days to respond.
    """
    if payload.instalment_frequency not in INSTALMENT_FREQUENCIES:
        raise HTTPException(400, f"Invalid frequency. Valid: {INSTALMENT_FREQUENCIES}")

    db = _get_db()
    now = _now_iso()
    user_id = current_user.get("id") or current_user.get("_id", "unknown")

    doc = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "unit_number": payload.unit_number,
        "owner_id": user_id,
        "owner_name": current_user.get("full_name", ""),
        "outstanding_amount_cents": payload.outstanding_amount_cents,
        "requested_instalment_cents": payload.requested_instalment_cents,
        "instalment_frequency": payload.instalment_frequency,
        "proposed_start_date": payload.proposed_start_date,
        "hardship_reason": payload.hardship_reason,
        "notes": payload.notes,
        "status": "requested",
        "response_due_by": None,  # set by manager at approval
        "approved_instalment_cents": None,
        "instalment_count": None,
        "start_date": None,
        "approved_by": None,
        "approved_at": None,
        "declined_by": None,
        "declined_at": None,
        "decline_reason": None,
        "submitted_by": user_id,
        "created_at": now,
        "updated_at": now,
    }

    await db.payment_plans.insert_one(doc)

    asyncio.create_task(create_audit_log(
        action="payment_plan_requested",
        resource_type="payment_plans",
        resource_id=doc["id"],
        user_id=user_id,
        user_name=current_user.get("full_name", ""),
        details={"unit_number": payload.unit_number, "outstanding_cents": payload.outstanding_amount_cents},
        building_id=building_id,
    ))

    logger.info("Payment plan %s requested for unit %s (building %s)", doc["id"], payload.unit_number, building_id)
    return _clean(doc)


@router.get("")
async def list_payment_plans(
        status: Optional[str] = Query(None),
        unit_number: Optional[str] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """List payment plans. Managers see all; owners see their own."""
    db = _get_db()
    role = current_user.get("effective_role") or current_user.get("role", "guest")

    query: dict = {"building_id": building_id}
    if role not in _MANAGER_ROLES:
        query["owner_id"] = current_user.get("id")
    if status:
        if status not in PLAN_STATUSES:
            raise HTTPException(400, f"Invalid status. Valid: {PLAN_STATUSES}")
        query["status"] = status
    if unit_number:
        query["unit_number"] = unit_number

    total = await db.payment_plans.count_documents(query)
    skip = (page - 1) * page_size
    cursor = db.payment_plans.find(query).sort("created_at", -1).skip(skip).limit(page_size)

    docs = []
    async for doc in cursor:
        docs.append(_clean(doc))

    return {"data": docs, "total": total, "page": page, "page_size": page_size}


@router.get("/{plan_id}")
async def get_payment_plan(
        plan_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: get_payment_plan
    Path: backend/routers/payment_plans.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    db = _get_db()
    doc = await db.payment_plans.find_one({"id": plan_id, "building_id": building_id})
    if not doc:
        raise HTTPException(404, "Payment plan not found.")
    if not _can_view(current_user, doc):
        raise HTTPException(403, "Access denied.")
    return _clean(doc)


@router.post("/{plan_id}/approve", response_model=PaymentPlanActionResponse)
async def approve_payment_plan(
        plan_id: str,
        payload: PaymentPlanApprove,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        # Approves an arrears payment plan — a binding financial commitment.
        _cap: dict = Depends(require_capability("building.finance.manage", building_from_context=True)),
):
    """Approve a payment plan request. Manager only. Responds within 28-day window."""
    _require_manager(current_user)
    if payload.instalment_frequency not in INSTALMENT_FREQUENCIES:
        raise HTTPException(400, f"Invalid frequency. Valid: {INSTALMENT_FREQUENCIES}")

    db = _get_db()
    doc = await db.payment_plans.find_one({"id": plan_id, "building_id": building_id})
    if not doc:
        raise HTTPException(404, "Payment plan not found.")
    if doc["status"] != "requested":
        raise HTTPException(409, f"Plan is in '{doc['status']}' — can only approve 'requested' plans.")

    user_id = current_user.get("id") or current_user.get("_id", "unknown")
    now = _now_iso()
    updates = {
        "status": "approved",
        "approved_instalment_cents": payload.approved_instalment_cents,
        "instalment_frequency": payload.instalment_frequency,
        "instalment_count": payload.instalment_count,
        "start_date": payload.start_date,
        "approved_by": user_id,
        "approved_at": now,
        "notes": payload.notes,
        "updated_at": now,
    }

    await db.payment_plans.update_one({"id": plan_id, "building_id": building_id}, {"$set": updates})
    asyncio.create_task(create_audit_log(
        action="payment_plan_approved",
        resource_type="payment_plans",
        resource_id=plan_id,
        user_id=user_id,
        user_name=current_user.get("full_name", ""),
        details={"instalment_cents": payload.approved_instalment_cents, "count": payload.instalment_count},
        building_id=building_id,
    ))
    logger.info("Payment plan %s approved by %s", plan_id, user_id)
    return {"message": "Payment plan approved.", "plan_id": plan_id}


@router.post("/{plan_id}/decline", response_model=PaymentPlanActionResponse)
async def decline_payment_plan(
        plan_id: str,
        payload: PaymentPlanDecline,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Decline a payment plan request. Manager only."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.payment_plans.find_one({"id": plan_id, "building_id": building_id})
    if not doc:
        raise HTTPException(404, "Payment plan not found.")
    if doc["status"] != "requested":
        raise HTTPException(409, f"Plan is in '{doc['status']}' — can only decline 'requested' plans.")

    user_id = current_user.get("id") or current_user.get("_id", "unknown")
    now = _now_iso()
    updates = {
        "status": "declined",
        "decline_reason": payload.reason,
        "declined_by": user_id,
        "declined_at": now,
        "updated_at": now,
    }

    await db.payment_plans.update_one({"id": plan_id, "building_id": building_id}, {"$set": updates})
    asyncio.create_task(create_audit_log(
        action="payment_plan_declined",
        resource_type="payment_plans",
        resource_id=plan_id,
        user_id=user_id,
        user_name=current_user.get("full_name", ""),
        details={"reason": payload.reason[:100]},
        building_id=building_id,
    ))
    logger.info("Payment plan %s declined by %s", plan_id, user_id)
    return {"message": "Payment plan declined.", "plan_id": plan_id}


@router.put("/{plan_id}/status", response_model=PaymentPlanActionResponse)
async def update_plan_status(
        plan_id: str,
        payload: PaymentPlanStatusUpdate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Update plan status to active, completed, or cancelled. Manager only."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.payment_plans.find_one({"id": plan_id, "building_id": building_id})
    if not doc:
        raise HTTPException(404, "Payment plan not found.")

    terminal = {"declined", "completed", "cancelled"}
    if doc["status"] in terminal:
        raise HTTPException(409, f"Plan is in terminal status '{doc['status']}' — cannot update.")

    user_id = current_user.get("id") or current_user.get("_id", "unknown")
    now = _now_iso()
    updates = {"status": payload.status, "updated_at": now, "updated_by": user_id}
    if payload.notes:
        updates["notes"] = payload.notes

    await db.payment_plans.update_one({"id": plan_id, "building_id": building_id}, {"$set": updates})
    asyncio.create_task(create_audit_log(
        action=f"payment_plan_{payload.status}",
        resource_type="payment_plans",
        resource_id=plan_id,
        user_id=user_id,
        user_name=current_user.get("full_name", ""),
        details={"new_status": payload.status},
        building_id=building_id,
    ))
    return {"message": f"Payment plan status updated to '{payload.status}'.", "plan_id": plan_id}
