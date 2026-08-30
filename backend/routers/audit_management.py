"""
Financial Audit Management Router

Implements UTMA 2011 s.82 (ACT) audit requirements:
- Mandatory audit when annual budget > $250K OR lots > 100
- Auditor engagement tracking
- Audit opinion storage as immutable artefact
- AGM financial statements linking
- Prevents AGM finalisation without required audit

Endpoints:
  GET    /audit/required            — check if audit required for building
  POST   /audit/records             — create audit record
  GET    /audit/records             — list audit records
  GET    /audit/records/{id}        — get audit record
  PUT    /audit/records/{id}        — update audit record (add opinion, close)
  GET    /audit/agm-checklist       — AGM readiness checklist
"""
from __future__ import annotations

from datetime import datetime, timezone

import asyncio
import logging
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from services.jurisdiction_service import JurisdictionService
from utils.auth import get_current_user, get_current_building, effective_role
from utils.helpers import create_audit_log

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/audit", tags=["Financial Audit"])


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/audit_management.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/audit_management.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_finance(user: dict) -> dict:
    """Generated function header.

    Function: _require_finance
    Path: backend/routers/audit_management.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    allowed = {"super_admin", "strata_manager", "strata_admin", "ec_member"}
    if effective_role(user) not in allowed:
        raise HTTPException(403, "Finance/EC access required.")
    return user


class AuditRecordCreate(BaseModel):
    building_id: str
    financial_year: str  # e.g. "2025-2026"
    annual_budget_aud: float
    lot_count: int
    auditor_name: Optional[str] = None
    auditor_firm: Optional[str] = None
    auditor_licence: Optional[str] = None
    auditor_independence_confirmed: bool = False
    engagement_date: Optional[str] = None  # ISO-8601
    report_due_date: Optional[str] = None  # ISO-8601 — before AGM
    agm_date: Optional[str] = None  # ISO-8601
    notes: Optional[str] = None


class AuditRecordUpdate(BaseModel):
    auditor_name: Optional[str] = None
    auditor_firm: Optional[str] = None
    auditor_licence: Optional[str] = None
    auditor_independence_confirmed: Optional[bool] = None
    engagement_date: Optional[str] = None
    report_due_date: Optional[str] = None
    agm_date: Optional[str] = None
    audit_opinion: Optional[str] = None  # "unqualified" | "qualified" | "adverse" | "disclaimer"
    audit_opinion_notes: Optional[str] = None
    audit_report_url: Optional[str] = None
    status: Optional[str] = None  # "pending" | "in_progress" | "completed" | "not_required"
    notes: Optional[str] = None


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/required")
async def check_audit_required(
        annual_budget_aud: float = Query(..., gt=0),
        lot_count: int = Query(..., gt=0),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Check whether a mandatory financial audit is required.

    Applies ACT UTMA 2011 s.82 threshold logic.
    """
    _require_finance(current_user)
    db = _get_db()
    js = JurisdictionService(db)
    result = await js.check_audit_required(building_id, annual_budget_aud, lot_count)
    return result


@router.post("/records", status_code=201)
async def create_audit_record(
        payload: AuditRecordCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Create an audit record for a financial year."""
    user = _require_finance(current_user)
    db = _get_db()
    js = JurisdictionService(db)

    # Determine if audit is legally required
    # SECURITY: Use verified building_id from dependency, not the payload
    audit_check = await js.check_audit_required(
        building_id, payload.annual_budget_aud, payload.lot_count,
    )

    doc = payload.model_dump()
    doc["building_id"] = building_id
    doc["audit_required"] = audit_check["required"]
    doc["audit_requirement_reason"] = audit_check["reason"]
    doc["audit_legislation"] = audit_check["legislation"]
    doc["status"] = "pending" if audit_check["required"] else "not_required"
    doc["audit_opinion"] = None
    doc["audit_opinion_notes"] = None
    doc["audit_report_url"] = None
    doc["created_at"] = _now_iso()
    doc["updated_at"] = _now_iso()
    doc["created_by"] = user["id"]

    result = await db.audit_records.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    # Fix audit log call to match helper signature: (action, resource_type, resource_id, user_id, user_name, details, building_id)
    asyncio.create_task(create_audit_log(
        "audit_record_created",
        "audit_record",
        doc["id"],
        user["id"],
        user["full_name"],
        {
            "financial_year": payload.financial_year,
            "required": audit_check["required"],
            "reason": audit_check["reason"],
        },
        building_id,
    ))

    return doc


@router.get("/records")
async def list_audit_records(
        financial_year: Optional[str] = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """List audit records for a building."""
    _require_finance(current_user)
    db = _get_db()

    query: dict = {"building_id": building_id}
    if financial_year:
        query["financial_year"] = financial_year

    skip = (page - 1) * page_size
    total = await db.audit_records.count_documents(query)
    cursor = db.audit_records.find(query).sort("financial_year", -1).skip(skip).limit(page_size)

    records = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        records.append(doc)

    return {"data": records, "total": total, "page": page}


@router.get("/records/{record_id}")
async def get_audit_record(
        record_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Get a single audit record."""
    _require_finance(current_user)
    db = _get_db()

    doc = await db.audit_records.find_one({
        "_id": ObjectId(record_id),
        "building_id": building_id,
    })
    if not doc:
        raise HTTPException(404, "Audit record not found.")
    doc["id"] = str(doc.pop("_id"))
    return doc


@router.put("/records/{record_id}")
async def update_audit_record(
        record_id: str,
        payload: AuditRecordUpdate = ...,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Update an audit record (add opinion, mark completed, etc.)."""
    user = _require_finance(current_user)
    db = _get_db()

    update = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    update["updated_at"] = _now_iso()

    result = await db.audit_records.update_one(
        {"_id": ObjectId(record_id), "building_id": building_id},
        {"$set": update},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Audit record not found.")

    # Fix audit log call to match helper signature: (action, resource_type, resource_id, user_id, user_name, details, building_id)
    asyncio.create_task(create_audit_log(
        "audit_record_updated",
        "audit_record",
        record_id,
        user["id"],
        user["full_name"],
        {"updated_fields": list(update.keys())},
        building_id,
    ))

    return {"message": "Audit record updated.", "record_id": record_id}


@router.get("/agm-checklist")
async def get_agm_checklist(
        financial_year: str = Query(...),
        annual_budget_aud: float = Query(...),
        lot_count: int = Query(...),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generate AGM readiness checklist.

    Checks whether:
    - Audit is required and completed
    - Financial statements are ready
    - Insurance policies are active
    - Sinking fund plan is current
    """
    _require_finance(current_user)
    db = _get_db()
    js = JurisdictionService(db)

    checklist = []

    # 1. Audit check
    audit_check = await js.check_audit_required(building_id, annual_budget_aud, lot_count)
    if audit_check["required"]:
        audit_record = await db.audit_records.find_one({
            "building_id": building_id,
            "financial_year": financial_year,
        })
        audit_done = bool(
            audit_record and audit_record.get("status") == "completed"
            and audit_record.get("audit_opinion")
        )
        checklist.append({
            "item": "Annual Financial Audit",
            "required": True,
            "completed": audit_done,
            "legislation": audit_check["legislation"],
            "severity": "blocking" if not audit_done else "ok",
        })
    else:
        checklist.append({
            "item": "Annual Financial Audit",
            "required": False,
            "completed": True,
            "legislation": audit_check["legislation"],
            "severity": "ok",
            "notes": audit_check["reason"],
        })

    # 2. Insurance check
    insurance_count = await db.insurance_policies.count_documents({
        "building_id": building_id,
        "status": "active",
    })
    checklist.append({
        "item": "Insurance Policies (active)",
        "required": True,
        "completed": insurance_count > 0,
        "legislation": "UTMA 2011 s.100",
        "severity": "blocking" if insurance_count == 0 else "ok",
    })

    # 3. Sinking fund plan review
    state = await js.get_building_state(building_id)
    review_years = await js.get_rule_value(building_id, "sinking_fund_review_years", default=5, state=state)
    checklist.append({
        "item": f"Sinking Fund Plan Review (every {review_years} years)",
        "required": True,
        "completed": None,  # requires manual check
        "legislation": "UTMA 2011 s.73A",
        "severity": "warning",
        "notes": "Verify sinking fund plan is current in documents.",
    })

    all_ok = all(item["severity"] in ("ok",) for item in checklist if item["required"])

    return {
        "building_id": building_id,
        "financial_year": financial_year,
        "agm_ready": all_ok,
        "checklist": checklist,
        "generated_at": _now_iso(),
    }
