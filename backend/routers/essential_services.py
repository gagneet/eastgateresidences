"""
GAP-COM-008: Essential Services Log (cooling tower / HVAC / backflow / lift).

Records compliance-driven service events for building essential services.
Each entry links to a building_asset and carries the certificate, technician,
service date, and next-due calculation.

Endpoints:
  POST   /essential-services              — log a service event
  GET    /essential-services              — list log entries (paginated, filterable)
  GET    /essential-services/overdue      — entries where next_due is in the past
  GET    /essential-services/{id}         — get single entry
  PUT    /essential-services/{id}         — update entry (add certificate URL, etc.)
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
    prefix="/essential-services", tags=["Compliance"],
    dependencies=[Depends(require_manager_surface("compliance"))],
)

SERVICE_TYPES = [
    "cooling_tower", "hvac", "backflow_prevention", "lift",
    "fire_pump", "emergency_lighting", "exit_lighting", "other",
]

SERVICE_STATUSES = ["compliant", "non_compliant", "advisory", "pending_certificate"]


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/essential_services.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/essential_services.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_manager(user: dict) -> dict:
    """Generated function header.

    Function: _require_manager
    Path: backend/routers/essential_services.py

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
    Path: backend/routers/essential_services.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    allowed = {"super_admin", "strata_manager", "strata_admin", "ec_member",
               "admin_staff", "owner", "tenant"}
    effective_role = user.get("effective_role") or user.get("role", "guest")
    if effective_role not in allowed:
        raise HTTPException(403, "Access denied.")
    return user


# ── Models ────────────────────────────────────────────────────────────────────

class EssentialServiceCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    service_type: str = Field(..., description=f"One of: {SERVICE_TYPES}")
    asset_id: Optional[str] = Field(None, description="building_assets._id reference")
    asset_name: str = Field(..., min_length=2, max_length=200, description="Human-readable asset name")
    service_date: str = Field(..., description="ISO-8601 date of service")
    technician: str = Field(..., min_length=2, max_length=200)
    company: Optional[str] = None
    licence_number: Optional[str] = Field(None, description="Technician licence/accreditation number")
    status: str = Field(default="compliant", description=f"One of: {SERVICE_STATUSES}")
    certificate_url: Optional[str] = None
    next_due: Optional[str] = Field(None, description="ISO-8601 date — next service due")
    # Convenience: auto-calculate next_due from service_date + interval_months
    interval_months: Optional[int] = Field(
        None, ge=1, le=120,
        description="If provided and next_due is absent, auto-calculates next_due = service_date + interval_months",
    )
    notes: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class EssentialServiceUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Optional[str] = None
    certificate_url: Optional[str] = None
    next_due: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def log_service_event(
        payload: EssentialServiceCreate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Record an essential service compliance event."""
    _require_manager(current_user)
    db = _get_db()

    if payload.service_type not in SERVICE_TYPES:
        raise HTTPException(400, f"Invalid service_type. Valid: {SERVICE_TYPES}")
    if payload.status not in SERVICE_STATUSES:
        raise HTTPException(400, f"Invalid status. Valid: {SERVICE_STATUSES}")

    # Auto-calculate next_due if interval_months provided and next_due absent
    next_due = payload.next_due
    if not next_due and payload.interval_months:
        try:
            svc_date = date.fromisoformat(payload.service_date)
            next_date = svc_date + relativedelta(months=payload.interval_months)
            next_due = next_date.isoformat()
        except ValueError:
            pass  # Bad date format — leave next_due null

    doc = {
        "building_id": building_id,
        "service_type": payload.service_type,
        "asset_id": payload.asset_id,
        "asset_name": payload.asset_name,
        "service_date": payload.service_date,
        "technician": payload.technician,
        "company": payload.company,
        "licence_number": payload.licence_number,
        "status": payload.status,
        "certificate_url": payload.certificate_url,
        "next_due": next_due,
        "notes": payload.notes,
        "tags": payload.tags,
        "created_by": current_user.get("id", "unknown"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    result = await db.essential_services_log.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    asyncio.create_task(create_audit_log(
        action="essential_service_logged",
        resource_type="essential_services_log",
        resource_id=doc["id"],
        user_id=current_user.get("id", "unknown"),
        user_name=current_user.get("full_name", ""),
        details={"service_type": payload.service_type, "status": payload.status, "asset_name": payload.asset_name},
        building_id=building_id,
    ))

    return doc


@router.get("")
async def list_service_log(
        service_type: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """List essential service log entries for this building."""
    _require_view(current_user)
    db = _get_db()

    query: dict = {"building_id": building_id}
    if service_type:
        query["service_type"] = service_type
    if status:
        query["status"] = status

    date_filter: dict = {}
    if date_from:
        date_filter["$gte"] = date_from
    if date_to:
        date_filter["$lte"] = date_to
    if date_filter:
        query["service_date"] = date_filter

    total = await db.essential_services_log.count_documents(query)
    skip = (page - 1) * page_size
    cursor = (
        db.essential_services_log.find(query)
        .sort("service_date", -1)
        .skip(skip)
        .limit(page_size)
    )

    docs = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        docs.append(doc)

    return {"data": docs, "total": total, "page": page, "page_size": page_size}


@router.get("/overdue")
async def list_overdue_services(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Return all essential services where next_due is in the past (overdue compliance items)."""
    _require_view(current_user)
    db = _get_db()

    today = date.today().isoformat()
    query = {
        "building_id": building_id,
        "next_due": {"$lt": today, "$ne": None},
        "status": {"$ne": "compliant"},
    }

    cursor = db.essential_services_log.find(query).sort("next_due", 1)
    docs = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        # Calculate days overdue
        try:
            overdue_days = (date.today() - date.fromisoformat(doc["next_due"])).days
            doc["days_overdue"] = overdue_days
        except (ValueError, KeyError):
            doc["days_overdue"] = None
        docs.append(doc)

    return {"data": docs, "total": len(docs), "as_of": today}


@router.get("/{entry_id}")
async def get_service_entry(
        entry_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Get a single essential service log entry."""
    _require_view(current_user)
    db = _get_db()

    try:
        oid = ObjectId(entry_id)
    except Exception:
        raise HTTPException(400, "Invalid entry ID.")

    doc = await db.essential_services_log.find_one({"_id": oid, "building_id": building_id})
    if not doc:
        raise HTTPException(404, "Entry not found.")

    doc["id"] = str(doc.pop("_id"))
    return doc


@router.put("/{entry_id}")
async def update_service_entry(
        entry_id: str,
        payload: EssentialServiceUpdate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Update a service log entry — typically to add certificate URL or change status."""
    _require_manager(current_user)
    db = _get_db()

    try:
        oid = ObjectId(entry_id)
    except Exception:
        raise HTTPException(400, "Invalid entry ID.")

    if payload.status and payload.status not in SERVICE_STATUSES:
        raise HTTPException(400, f"Invalid status. Valid: {SERVICE_STATUSES}")

    update = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not update:
        raise HTTPException(400, "No fields to update.")

    update["updated_at"] = _now_iso()
    update["updated_by"] = current_user.get("id", "unknown")

    result = await db.essential_services_log.update_one(
        {"_id": oid, "building_id": building_id},
        {"$set": update},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Entry not found.")

    asyncio.create_task(create_audit_log(
        action="essential_service_updated", resource_type="essential_services_log",
        resource_id=entry_id,
        user_id=current_user.get("id", "unknown"),
        user_name=current_user.get("full_name", "unknown"),
        details={k: v for k, v in update.items() if k not in ("updated_at", "updated_by")},
        building_id=building_id
    ))

    return {"message": "Entry updated.", "entry_id": entry_id}
