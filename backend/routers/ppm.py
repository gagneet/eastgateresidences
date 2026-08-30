# @featuretrace:ppm — Planned preventative maintenance schedules and compliance health.
# Layer: router
# Data flow: GET /ppm/dashboard -> maintenance_schedules (MongoDB, building-scoped)
#            -> PPMDashboardSummary -> MaintenancePage + ManagerDashboard PPM Health tile.
# Related: backend/models/ppm.py
#          frontend/src/pages/dashboard/MaintenancePage.tsx
#          frontend/src/pages/dashboard/ManagerDashboard.jsx
# Tests: tests/backend/test_ppm.py

"""
PPM (Preventive Maintenance Plan) Router.

Building: resolved from JWT (multi-tenant via get_current_building dependency).
Provides CRUD and completion-logging endpoints for the maintenance_schedules collection.

Endpoint summary:
  GET  /ppm                      — list items (filtered, paginated)
  GET  /ppm/sections             — distinct sections with counts
  GET  /ppm/dashboard            — compliance health summary
  GET  /ppm/upcoming             — items due within N days
  GET  /ppm/overdue              — all overdue items
  GET  /ppm/{schedule_id}        — full detail
  PUT  /ppm/{schedule_id}        — update vendor/notes (managers only)
  POST /ppm/{schedule_id}/complete — log completion (managers only)
"""

import html as html_lib
import uuid
from datetime import datetime, timezone, timedelta

import logging
import nh3
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional

from database import db
from models.ppm import (
    PPMScheduleUpdate,
    PPMCompletionLog,
    PPMDashboardSummary,
)
from models.user import UserRole
from utils.auth import get_current_user, get_current_building, DEFAULT_BUILDING_ID, effective_role
from utils.helpers import create_audit_log
from utils.permissions import require_feature

logger = logging.getLogger(__name__)

# GAP-FT-004: Gate the PPM router behind the ppm_schedule feature toggle.
router = APIRouter(
    prefix="/ppm",
    tags=["PPM"],
    dependencies=[Depends(require_feature("ppm_schedule"))],
)

_DEFAULT_BUILDING_ID = DEFAULT_BUILDING_ID  # use canonical constant; never hardcode

_MANAGER_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.EC_MEMBER,
    UserRole.STRATA_MANAGER,
}


def _is_manager(user: dict) -> bool:
    # GAP-FT-012: Use effective_role() instead of raw user["role"] so that
    # temp-elevated users (e.g. owner elevated to ec_member) pass this check.
    """Generated function header.

    Function: _is_manager
    Path: backend/routers/ppm.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return effective_role(user) in _MANAGER_ROLES


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/ppm.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _compute_status(next_due_date_str: str) -> str:
    """Recompute status based on next_due_date relative to today."""
    try:
        next_due = datetime.fromisoformat(next_due_date_str.replace("Z", "+00:00"))
        if next_due.tzinfo is None:
            next_due = next_due.replace(tzinfo=timezone.utc)
        today = datetime.now(timezone.utc)
        delta = (next_due.date() - today.date()).days
        if delta < 0:
            return "overdue"
        elif delta <= 30:
            return "due_soon"
        return "scheduled"
    except Exception:
        return "scheduled"


# ─── GET /ppm ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[dict])
async def list_ppm_items(
        section: Optional[str] = None,
        status: Optional[str] = None,
        frequency: Optional[str] = None,
        is_compliance: Optional[bool] = None,
        skip: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """List all PPM items for the current building with optional filters."""
    query: dict = {"building_id": building_id}
    if section:
        query["section"] = section
    if status:
        if status not in {"overdue", "due_soon", "scheduled"}:
            raise HTTPException(status_code=400, detail="status must be one of: overdue, due_soon, scheduled")
        query["status"] = status
    if frequency:
        query["frequency"] = frequency
    if is_compliance is not None:
        query["is_compliance"] = is_compliance

    try:
        cursor = db.maintenance_schedules.find(query, {"_id": 0}).sort("next_due_date", 1).skip(skip).limit(limit)
        items = await cursor.to_list(length=limit)
    except Exception as exc:
        logger.error("list_ppm_items DB error for building %s: %s", building_id, exc)
        raise HTTPException(status_code=503, detail="Failed to load maintenance schedules")

    is_mgr = _is_manager(current_user)
    if is_mgr:
        return items
    # Owners/tenants get limited fields only — vendor contact and logs are manager-only
    limited_fields = {
        "id", "schedule_id", "section", "description",
        "inspection_type", "frequency", "next_due_date", "status", "is_compliance",
    }
    return [{k: v for k, v in item.items() if k in limited_fields} for item in items]


# ─── GET /ppm/sections ────────────────────────────────────────────────────────

@router.get("/sections", response_model=List[dict])
async def list_ppm_sections(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Return distinct sections with item_count and compliance_count."""
    pipeline = [
        {"$match": {"building_id": building_id}},
        {
            "$group": {
                "_id": "$section",
                "item_count": {"$sum": 1},
                "compliance_count": {
                    "$sum": {"$cond": [{"$eq": ["$is_compliance", True]}, 1, 0]}
                },
                "overdue_count": {
                    "$sum": {"$cond": [{"$eq": ["$status", "overdue"]}, 1, 0]}
                },
            }
        },
        {"$project": {"section": "$_id", "_id": 0, "item_count": 1, "compliance_count": 1, "overdue_count": 1}},
        {"$sort": {"section": 1}},
    ]
    try:
        result = await db.maintenance_schedules.aggregate(pipeline).to_list(length=50)
    except Exception as exc:
        logger.error("list_ppm_sections aggregation error for building %s: %s", building_id, exc)
        raise HTTPException(status_code=503, detail="Failed to load PPM sections")
    return result


# ─── GET /ppm/dashboard ───────────────────────────────────────────────────────

@router.get("/dashboard", response_model=PPMDashboardSummary)
async def ppm_dashboard(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Return PPM compliance health dashboard summary."""
    try:
        total_items = await db.maintenance_schedules.count_documents({"building_id": building_id})
        overdue_count = await db.maintenance_schedules.count_documents(
            {"building_id": building_id, "status": "overdue"}
        )
        due_soon_count = await db.maintenance_schedules.count_documents(
            {"building_id": building_id, "status": "due_soon"}
        )
        compliance_total = await db.maintenance_schedules.count_documents(
            {"building_id": building_id, "is_compliance": True}
        )
        compliance_overdue_count = await db.maintenance_schedules.count_documents(
            {"building_id": building_id, "is_compliance": True, "status": "overdue"}
        )
    except Exception as exc:
        logger.error("ppm_dashboard count error for building %s: %s", building_id, exc)
        raise HTTPException(status_code=503, detail="Failed to load PPM dashboard")

    # Health score: % of compliance items that are NOT overdue.
    #
    # With no compliance items at all there is no score to report. Returning
    # 100.0 here read as "fully compliant" on every dashboard, so a building
    # that tracked nothing looked identical to one that tracked everything and
    # was perfectly on time — the most dangerous possible reading for a SAFETY
    # metric. None is returned instead and rendered as an explicit
    # "no items tracked" empty state by the callers.
    health_score = (
        round((compliance_total - compliance_overdue_count) / compliance_total * 100, 1)
        if compliance_total
        else None
    )

    return PPMDashboardSummary(
        total_items=total_items,
        overdue_count=overdue_count,
        due_soon_count=due_soon_count,
        compliance_total=compliance_total,
        compliance_overdue_count=compliance_overdue_count,
        health_score=health_score,
    )


# ─── GET /ppm/upcoming ────────────────────────────────────────────────────────

@router.get("/upcoming", response_model=List[dict])
async def ppm_upcoming(
        days: int = Query(30, ge=1, le=365),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Return items due within the next N days."""
    today = datetime.now(timezone.utc)
    cutoff = (today + timedelta(days=days)).date().isoformat()
    today_str = today.date().isoformat()

    try:
        cursor = db.maintenance_schedules.find(
            {
                "building_id": building_id,
                "next_due_date": {"$gte": today_str, "$lte": cutoff},
            },
            {"_id": 0},
        ).sort("next_due_date", 1)
        return await cursor.to_list(length=500)
    except Exception as exc:
        logger.error("ppm_upcoming DB error for building %s: %s", building_id, exc)
        raise HTTPException(status_code=503, detail="Failed to load upcoming PPM items")


# ─── GET /ppm/overdue ─────────────────────────────────────────────────────────

@router.get("/overdue", response_model=List[dict])
async def ppm_overdue(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Return all overdue items with days_overdue computed."""
    try:
        cursor = db.maintenance_schedules.find(
            {"building_id": building_id, "status": "overdue"},
            {"_id": 0},
        ).sort("next_due_date", 1)
        items = await cursor.to_list(length=500)
    except Exception as exc:
        logger.error("ppm_overdue DB error for building %s: %s", building_id, exc)
        raise HTTPException(status_code=503, detail="Failed to load overdue PPM items")

    today = datetime.now(timezone.utc).date()
    result = []
    for item in items:
        try:
            due_str = item.get("next_due_date", "")
            due = datetime.fromisoformat(due_str.replace("Z", "+00:00")).date()
            days_overdue = (today - due).days
        except Exception:
            days_overdue = 0
        result.append({**item, "days_overdue": days_overdue})
    return result


# ─── GET /ppm/{schedule_id} ───────────────────────────────────────────────────

@router.get("/{schedule_id}", response_model=dict)
async def get_ppm_item(
        schedule_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Return full detail of a single PPM item including completion_log."""
    try:
        item = await db.maintenance_schedules.find_one(
            {"building_id": building_id, "schedule_id": schedule_id}, {"_id": 0}
        )
    except Exception as exc:
        logger.error("get_ppm_item DB error for %s: %s", schedule_id, exc)
        raise HTTPException(status_code=503, detail="Failed to load PPM item")
    if item is None:
        raise HTTPException(status_code=404, detail="PPM schedule item not found")
    return item


# ─── PUT /ppm/{schedule_id} ───────────────────────────────────────────────────

@router.put("/{schedule_id}", response_model=dict)
async def update_ppm_item(
        schedule_id: str,
        data: PPMScheduleUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Update vendor details, notes, or next_due_date on a PPM item."""
    if not _is_manager(current_user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    try:
        item = await db.maintenance_schedules.find_one(
            {"building_id": building_id, "schedule_id": schedule_id}, {"_id": 0}
        )
    except Exception as exc:
        logger.error("update_ppm_item find error for %s: %s", schedule_id, exc)
        raise HTTPException(status_code=503, detail="Failed to load PPM item")
    if item is None:
        raise HTTPException(status_code=404, detail="PPM schedule item not found")

    updates: dict = {k: v for k, v in data.model_dump(exclude_unset=True).items()}
    # SECURITY: sanitize text fields to prevent stored XSS
    if "vendor_name" in updates and updates["vendor_name"]:
        updates["vendor_name"] = html_lib.escape(str(updates["vendor_name"]))
    if "vendor_contact" in updates and updates["vendor_contact"]:
        updates["vendor_contact"] = html_lib.escape(str(updates["vendor_contact"]))
    if "notes" in updates and updates["notes"]:
        updates["notes"] = nh3.clean(str(updates["notes"]))
    now = _now_iso()
    updates["updated_at"] = now

    # Recompute status if next_due_date changed
    if "next_due_date" in updates:
        updates["status"] = _compute_status(updates["next_due_date"])

    try:
        await db.maintenance_schedules.update_one(
            {"building_id": building_id, "schedule_id": schedule_id},
            {"$set": updates},
        )

        # Sync open calendar events when due date is rescheduled
        if "next_due_date" in updates:
            await db.events.update_many(
                {
                    "building_id": building_id,
                    "event_type": "ppm",
                    "ppm_schedule_id": schedule_id,
                    "completed_at": {"$exists": False},
                },
                {"$set": {"start_date": updates["next_due_date"], "updated_at": now}},
            )
    except Exception as exc:
        logger.error("update_ppm_item write error for %s: %s", schedule_id, exc)
        raise HTTPException(status_code=503, detail="Failed to update PPM item")

    await create_audit_log(
        action="ppm_update",
        resource_type="ppm_schedule",
        resource_id=schedule_id,
        user_id=current_user["id"],
        user_name=current_user.get("full_name", ""),
        details={"updates": updates},
    )

    updated = await db.maintenance_schedules.find_one(
        {"building_id": building_id, "schedule_id": schedule_id}, {"_id": 0}
    )
    return updated


# ─── POST /ppm/{schedule_id}/complete ─────────────────────────────────────────

@router.post("/{schedule_id}/complete", response_model=dict)
async def complete_ppm_item(
        schedule_id: str,
        data: PPMCompletionLog,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Log completion of a PPM maintenance task.

    - Appends to completion_log
    - Auto-recomputes next_due_date = completion_date + frequency_days
    - Sets status='scheduled'
    - Marks current calendar event completed; creates next occurrence
    - Creates audit_log entry
    """
    if not _is_manager(current_user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    try:
        item = await db.maintenance_schedules.find_one(
            {"building_id": building_id, "schedule_id": schedule_id}, {"_id": 0}
        )
    except Exception as exc:
        logger.error("complete_ppm_item find error for %s: %s", schedule_id, exc)
        raise HTTPException(status_code=503, detail="Failed to load PPM item")
    if item is None:
        raise HTTPException(status_code=404, detail="PPM schedule item not found")

    # Parse completion_date and compute next_due_date
    try:
        completion_dt = datetime.fromisoformat(data.completion_date.replace("Z", "+00:00"))
        if completion_dt.tzinfo is None:
            completion_dt = completion_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid completion_date format. Use ISO date string.")

    frequency_days = item.get("frequency_days", 365)
    next_due_dt = completion_dt + timedelta(days=frequency_days)
    next_due_date_str = next_due_dt.date().isoformat()
    new_status = _compute_status(next_due_date_str)

    log_entry = {
        "date": data.completion_date,
        "completed_by": html_lib.escape(data.completed_by),
        "notes": nh3.clean(data.notes) if data.notes else None,
        "certificate_ref": html_lib.escape(data.certificate_ref) if data.certificate_ref else None,
        "logged_by_user_id": current_user["id"],
        "logged_at": _now_iso(),
    }

    now = _now_iso()
    try:
        await db.maintenance_schedules.update_one(
            {"building_id": building_id, "schedule_id": schedule_id},
            {
                "$set": {
                    "last_completed_date": data.completion_date,
                    "next_due_date": next_due_date_str,
                    "status": new_status,
                    "updated_at": now,
                },
                "$push": {"completion_log": log_entry},
            },
        )

        # Mark current calendar event as completed
        await db.events.update_many(
            {
                "building_id": building_id,
                "event_type": "ppm",
                "ppm_schedule_id": schedule_id,
                "completed_at": {"$exists": False},
            },
            {"$set": {"completed_at": now}},
        )

        # Create next calendar event
        section = item.get("section", "")
        description = item.get("description", "")
        title = f"{section}: {description}"[:80]

        is_compliance = item.get("is_compliance", False)
        inspection_type = item.get("inspection_type", "")
        capital_years = item.get("capital_years", [])
        current_year = next_due_dt.year

        if is_compliance:
            color = "red"
        elif "Licenced Contractor" in str(inspection_type):
            color = "orange"
        elif current_year in capital_years:
            color = "purple"
        else:
            color = "blue"

        new_event_id = str(uuid.uuid4())
        await db.events.insert_one(
            {
                "id": new_event_id,
                "building_id": building_id,
                "event_type": "ppm",
                "title": title,
                "description": description,
                "start_date": next_due_date_str,
                "is_public": False,
                "is_recurring": True,
                "ppm_schedule_id": schedule_id,
                "metadata": {"color": color, "section": section},
                "source": "ppm",
                "created_by": current_user["id"],
                "created_at": now,
            }
        )
    except Exception as exc:
        logger.error("complete_ppm_item write error for %s: %s", schedule_id, exc)
        raise HTTPException(status_code=503, detail="Failed to save completion")

    await create_audit_log(
        action="ppm_complete",
        resource_type="ppm_schedule",
        resource_id=schedule_id,
        user_id=current_user["id"],
        user_name=current_user.get("full_name", ""),
        details={
            "completion_date": data.completion_date,
            "next_due_date": next_due_date_str,
            "certificate_ref": data.certificate_ref,
        },
    )

    updated = await db.maintenance_schedules.find_one(
        {"building_id": building_id, "schedule_id": schedule_id}, {"_id": 0}
    )
    return updated
