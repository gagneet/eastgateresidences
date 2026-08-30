"""
GAP-COM-005: Pool Safety Inspection Register.

Tracks statutory pool safety inspections required under Australian building codes
and state regulations (e.g. NSW Swimming Pools Act 1992, ACT Public Pools legislation).

Endpoints:
  POST   /compliance/pool-safety          — log an inspection
  GET    /compliance/pool-safety          — list inspections (building-scoped, paginated)
  GET    /compliance/pool-safety/{id}     — get single inspection record
  PUT    /compliance/pool-safety/{id}     — update (add certificate, change status)
  GET    /compliance/pool-safety/overdue  — entries where next_due is past
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, date
from dateutil.relativedelta import relativedelta
from typing import Optional, List

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict

from utils.auth import get_current_user, get_current_building
from utils.route_guards import require_manager_surface
from utils.helpers import create_audit_log

logger = logging.getLogger(__name__)
# Function scoping (2026-08-28). Router-level, so every route in this file is
# covered including the reads — Privacy Act APP 6 limits USE to the purpose of
# collection, and looking is a use. This narrows nobody until an agency sets
# core.management_entities.function_scoping_enabled; see
# docs/architecture/strata_management_staff_access_model.md.
router = APIRouter(
    prefix="/compliance/pool-safety", tags=["Compliance"],
    dependencies=[Depends(require_manager_surface("compliance"))],
)

INSPECTION_RESULTS = ["pass", "fail", "advisory", "conditional_pass", "not_inspected"]
POOL_TYPES = ["swimming_pool", "spa", "wading_pool", "hydrotherapy_pool", "other"]


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/pool_safety.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/pool_safety.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_manager(user: dict) -> dict:
    """Generated function header.

    Function: _require_manager
    Path: backend/routers/pool_safety.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    allowed = {"super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"}
    effective_role = user.get("effective_role") or user.get("role", "guest")
    if effective_role not in allowed:
        raise HTTPException(403, "Manager access required.")
    return user


def _require_view(user: dict) -> dict:
    """Generated function header.

    Function: _require_view
    Path: backend/routers/pool_safety.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    allowed = {"super_admin", "strata_manager", "strata_admin", "ec_member",
               "admin_staff", "owner", "tenant"}
    effective_role = user.get("effective_role") or user.get("role", "guest")
    if effective_role not in allowed:
        raise HTTPException(403, "Access denied.")
    return user


# ── Models ────────────────────────────────────────────────────────────────────

class PoolSafetyCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pool_name: str = Field(..., min_length=2, max_length=200, description="Descriptive name of the pool/facility")
    pool_type: str = Field(default="swimming_pool", description=f"One of: {POOL_TYPES}")
    inspection_date: str = Field(..., description="ISO-8601 date of inspection")
    inspector_name: str = Field(..., min_length=2, max_length=200)
    inspector_company: Optional[str] = None
    licence_number: Optional[str] = Field(None, description="Inspector accreditation/licence number")
    result: str = Field(default="not_inspected", description=f"One of: {INSPECTION_RESULTS}")
    defects_found: Optional[str] = Field(None, max_length=2000, description="Description of any defects identified")
    rectification_due: Optional[str] = Field(None, description="ISO-8601 date by which defects must be rectified")
    certificate_url: Optional[str] = None
    next_due: Optional[str] = Field(None, description="ISO-8601 date for next required inspection")
    interval_months: Optional[int] = Field(
        None, ge=1, le=120,
        description="If provided and next_due absent, auto-calculate next_due = inspection_date + interval_months",
    )
    notes: Optional[str] = Field(None, max_length=1000)


class PoolSafetyUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    result: Optional[str] = None
    defects_found: Optional[str] = None
    rectification_due: Optional[str] = None
    certificate_url: Optional[str] = None
    next_due: Optional[str] = None
    notes: Optional[str] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/overdue")
async def list_overdue_pool_inspections(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Return pool safety records where next_due is in the past and result is not 'pass'."""
    _require_view(current_user)
    db = _get_db()

    today = date.today().isoformat()
    query = {
        "building_id": building_id,
        "next_due": {"$lt": today, "$ne": None},
        "result": {"$ne": "pass"},
    }
    cursor = db.pool_safety_inspections.find(query).sort("next_due", 1)
    docs = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        try:
            doc["days_overdue"] = (date.today() - date.fromisoformat(doc["next_due"])).days
        except (ValueError, KeyError):
            doc["days_overdue"] = None
        docs.append(doc)

    return {"data": docs, "total": len(docs), "as_of": today}


@router.post("", status_code=201)
async def log_pool_inspection(
        payload: PoolSafetyCreate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Record a pool safety inspection event."""
    _require_manager(current_user)
    db = _get_db()

    if payload.pool_type not in POOL_TYPES:
        raise HTTPException(400, f"Invalid pool_type. Valid: {POOL_TYPES}")
    if payload.result not in INSPECTION_RESULTS:
        raise HTTPException(400, f"Invalid result. Valid: {INSPECTION_RESULTS}")

    try:
        insp_date = date.fromisoformat(payload.inspection_date)
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid inspection_date — must be ISO-8601 (YYYY-MM-DD).")

    next_due = payload.next_due
    if not next_due and payload.interval_months:
        next_due = (insp_date + relativedelta(months=payload.interval_months)).isoformat()

    if next_due:
        try:
            date.fromisoformat(next_due)
        except (ValueError, TypeError):
            raise HTTPException(400, "Invalid next_due — must be ISO-8601 (YYYY-MM-DD).")

    doc = {
        "building_id": building_id,
        "pool_name": payload.pool_name,
        "pool_type": payload.pool_type,
        "inspection_date": payload.inspection_date,
        "inspector_name": payload.inspector_name,
        "inspector_company": payload.inspector_company,
        "licence_number": payload.licence_number,
        "result": payload.result,
        "defects_found": payload.defects_found,
        "rectification_due": payload.rectification_due,
        "certificate_url": payload.certificate_url,
        "next_due": next_due,
        "notes": payload.notes,
        "created_by": current_user.get("id", "unknown"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    result = await db.pool_safety_inspections.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    asyncio.create_task(create_audit_log(
        action="pool_inspection_logged",
        resource_type="pool_safety_inspections",
        resource_id=doc["id"],
        user_id=current_user.get("id", "unknown"),
        user_name=current_user.get("full_name", ""),
        details={"pool_name": payload.pool_name, "result": payload.result},
        building_id=building_id,
    ))

    return doc


@router.get("")
async def list_pool_inspections(
        pool_name: Optional[str] = Query(None),
        result: Optional[str] = Query(None),
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """List pool safety inspection records for this building."""
    _require_view(current_user)
    db = _get_db()

    query: dict = {"building_id": building_id}
    if pool_name:
        query["pool_name"] = {"$regex": pool_name, "$options": "i"}
    if result:
        query["result"] = result

    date_filter: dict = {}
    if date_from:
        date_filter["$gte"] = date_from
    if date_to:
        date_filter["$lte"] = date_to
    if date_filter:
        query["inspection_date"] = date_filter

    total = await db.pool_safety_inspections.count_documents(query)
    skip = (page - 1) * page_size
    cursor = (
        db.pool_safety_inspections.find(query)
        .sort("inspection_date", -1)
        .skip(skip)
        .limit(page_size)
    )

    docs = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        docs.append(doc)

    return {"data": docs, "total": total, "page": page, "page_size": page_size}


@router.get("/{inspection_id}")
async def get_pool_inspection(
        inspection_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Get a single pool safety inspection record."""
    _require_view(current_user)
    db = _get_db()

    try:
        oid = ObjectId(inspection_id)
    except Exception:
        raise HTTPException(400, "Invalid inspection ID.")

    doc = await db.pool_safety_inspections.find_one({"_id": oid, "building_id": building_id})
    if not doc:
        raise HTTPException(404, "Inspection record not found.")

    doc["id"] = str(doc.pop("_id"))
    return doc


@router.put("/{inspection_id}")
async def update_pool_inspection(
        inspection_id: str,
        payload: PoolSafetyUpdate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Update an inspection record — typically to add certificate URL or record rectification."""
    _require_manager(current_user)
    db = _get_db()

    try:
        oid = ObjectId(inspection_id)
    except Exception:
        raise HTTPException(400, "Invalid inspection ID.")

    if payload.result and payload.result not in INSPECTION_RESULTS:
        raise HTTPException(400, f"Invalid result. Valid: {INSPECTION_RESULTS}")

    update = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not update:
        raise HTTPException(400, "No fields to update.")

    update["updated_at"] = _now_iso()
    update["updated_by"] = current_user.get("id", "unknown")

    result = await db.pool_safety_inspections.update_one(
        {"_id": oid, "building_id": building_id},
        {"$set": update},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Inspection record not found.")

    return {"message": "Inspection updated.", "inspection_id": inspection_id}
