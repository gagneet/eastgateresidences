"""
Defects Register Router — GAP-MNT-001
@featuretrace:defects — building defects register with statutory warranty clocks
Layer: router
Data flow: frontend/src/pages/dashboard/DefectsRegisterPage.jsx → /defects/* → defects (building-scoped).
Related: backend/cron/cron_compliance_calendar.py
          frontend/public/tech-docs/defects-register.html

Building defects register with statutory warranty clocks per jurisdiction:
  - ACT: UTMA 2011 / Building Act 2004 — typically 6-year defect liability
  - NSW: Home Building Act 1989 — major defects 6 years, minor defects 2 years
  - VIC: Domestic Building Contracts Act 1995 — 10-year major structural defects
  - QLD: Queensland Building and Construction Commission Act 1991 — 6+1 year DLP

Endpoints:
  POST   /defects                       — log a new defect
  GET    /defects                       — list defects (building-scoped, filterable)
  GET    /defects/{id}                  — get single defect
  PUT    /defects/{id}                  — update defect (status, resolution notes)
  DELETE /defects/{id}                  — soft-close (cancelled_at)
  GET    /defects/warranty-summary      — aggregated warranty clock status
  POST   /defects/{id}/photos           — attach photo URLs to a defect
  POST   /defects/{id}/notes            — append a timeline note

Legislation:
  - ACT Building Act 2004 s.89 — 6-year defect liability period
  - NSW Home Building Act 1989 s.18B — 6/2 year statutory warranty
  - VIC Domestic Building Contracts Act 1995 s.169B — 10-year major structural
  - QLD QBCC Act 1991 s.67C — 6-year + 1-year DLP
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone, date, timedelta
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from utils.auth import get_current_user, get_current_building, effective_role
from utils.route_guards import require_manager_surface
from utils.helpers import create_audit_log
from utils.permissions import require_feature

logger = logging.getLogger(__name__)
# Function scoping (2026-08-28). Router-level, so every route in this file is
# covered including the reads — Privacy Act APP 6 limits USE to the purpose of
# collection, and looking is a use. This narrows nobody until an agency sets
# core.management_entities.function_scoping_enabled; see
# docs/architecture/strata_management_staff_access_model.md.
router = APIRouter(
    prefix="/defects", tags=["Defects Register"],
    dependencies=[Depends(require_manager_surface("defects"))],
)

# ── Warranty periods by jurisdiction (years from practical completion) ────────
# These are legislated minimums; a contract may extend them.
_WARRANTY_PERIODS: Dict[str, Dict[str, int]] = {
    "ACT": {"major": 6, "minor": 6},
    "NSW": {"major": 6, "minor": 2},
    "VIC": {"major": 10, "minor": 2},
    "QLD": {"major": 6, "minor": 1},
    "WA": {"major": 6, "minor": 1},
    "SA": {"major": 6, "minor": 1},
    "TAS": {"major": 6, "minor": 1},
    "NT": {"major": 6, "minor": 1},
}

_DEFECT_STATUSES = {"open", "under_investigation", "remediated", "disputed", "closed", "cancelled"}
_DEFECT_CATEGORIES = {
    "structural", "waterproofing", "fire_safety", "mechanical", "electrical",
    "plumbing", "facade", "roofing", "lift", "common_area", "unit", "other",
}
_DEFECT_SEVERITY = {"critical", "major", "minor", "cosmetic"}


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/defects_register.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/defects_register.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_manager(user: dict) -> dict:
    # "chairman" is not a top-level role (removed migration 0025); effective_role()
    # returns "ec_member" for a chairman. "admin" is not a valid role name.
    """Generated function header.

    Function: _require_manager
    Path: backend/routers/defects_register.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    allowed = {"super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"}
    if effective_role(user) not in allowed:
        raise HTTPException(403, "Manager or EC access required.")
    return user


def _require_at_least_owner(user: dict) -> dict:
    """Any authenticated resident or manager can log a defect."""
    allowed = {
        "super_admin", "strata_manager", "strata_admin",
        "ec_member", "admin_staff", "owner",
    }
    if effective_role(user) not in allowed:
        raise HTTPException(403, "Owner or manager access required.")
    return user


def _compute_warranty_deadline(
    practical_completion_date: Optional[str],
    jurisdiction: str,
    severity: str,
) -> Optional[str]:
    """Return ISO-8601 warranty expiry date, or None if completion date unknown."""
    if not practical_completion_date:
        return None
    try:
        completion = date.fromisoformat(practical_completion_date[:10])
    except (ValueError, TypeError):
        return None
    periods = _WARRANTY_PERIODS.get(jurisdiction.upper(), _WARRANTY_PERIODS["ACT"])
    defect_type = "major" if severity in ("critical", "major") else "minor"
    years = periods.get(defect_type, 6)
    # Add exact calendar years (handles leap years correctly — no day-drift)
    try:
        deadline = completion.replace(year=completion.year + years)
    except ValueError:
        # Feb 29 → Mar 1 in non-leap target year
        deadline = completion.replace(year=completion.year + years, month=3, day=1)
    return deadline.isoformat()


# ─── Models ───────────────────────────────────────────────────────────────────


class DefectCreate(BaseModel):
    """Log a new building defect."""

    model_config = ConfigDict(extra="ignore")

    building_id: str
    title: str = Field(..., min_length=5, max_length=300)
    description: str = Field(..., min_length=10, max_length=5000)
    category: str = Field(..., description=f"One of: {', '.join(sorted(_DEFECT_CATEGORIES))}")
    severity: str = Field(..., description="critical | major | minor | cosmetic")
    location: str = Field(..., max_length=200, description="Building location (e.g. 'Level 3 podium deck')")
    lot_number: Optional[str] = Field(None, max_length=20, description="Affected lot number if applicable")
    practical_completion_date: Optional[str] = Field(
        None, description="ISO-8601 date of builder's practical completion"
    )
    jurisdiction: str = Field("ACT", description="ACT | NSW | VIC | QLD | WA | SA | TAS | NT")
    builder_name: Optional[str] = Field(None, max_length=200)
    builder_licence: Optional[str] = Field(None, max_length=100)
    builder_contact_email: Optional[str] = Field(None, max_length=200)
    builder_contact_phone: Optional[str] = Field(None, max_length=50)
    discovered_date: Optional[str] = Field(None, description="ISO-8601 date defect was discovered")
    photo_urls: List[str] = Field(default_factory=list, max_length=20)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    notified_builder_at: Optional[str] = Field(None, description="ISO-8601 datetime builder was notified")
    notes: Optional[str] = Field(None, max_length=5000)


class DefectUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: Optional[str] = Field(None, min_length=5, max_length=300)
    description: Optional[str] = Field(None, min_length=10, max_length=5000)
    category: Optional[str] = None
    severity: Optional[str] = None
    location: Optional[str] = Field(None, max_length=200)
    lot_number: Optional[str] = Field(None, max_length=20)
    status: Optional[str] = None
    builder_name: Optional[str] = Field(None, max_length=200)
    builder_licence: Optional[str] = Field(None, max_length=100)
    builder_contact_email: Optional[str] = Field(None, max_length=200)
    builder_contact_phone: Optional[str] = Field(None, max_length=50)
    practical_completion_date: Optional[str] = None
    jurisdiction: Optional[str] = None
    discovered_date: Optional[str] = None
    notified_builder_at: Optional[str] = None
    remediated_at: Optional[str] = None
    resolution_notes: Optional[str] = Field(None, max_length=5000)
    notes: Optional[str] = Field(None, max_length=5000)


class DefectNoteCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    note: str = Field(..., min_length=2, max_length=3000)
    note_type: str = Field("general", description="general | builder_response | inspection | legal")


class DefectPhotoAdd(BaseModel):
    model_config = ConfigDict(extra="ignore")
    photo_urls: List[str] = Field(..., min_length=1, max_length=20)
    label: Optional[str] = Field(None, max_length=200)


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.post("", status_code=201)
async def create_defect(
        payload: DefectCreate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        _toggle: dict = Depends(require_feature("defects")),
):
    """GAP-MNT-001: Log a new building defect with statutory warranty clock.

    Any owner or manager can log a defect. The warranty expiry deadline is
    computed from the practical completion date and jurisdiction.
    """
    _require_at_least_owner(current_user)
    db = _get_db()

    if payload.category not in _DEFECT_CATEGORIES:
        raise HTTPException(422, f"Invalid category. Allowed: {', '.join(sorted(_DEFECT_CATEGORIES))}")
    if payload.severity not in _DEFECT_SEVERITY:
        raise HTTPException(422, f"Invalid severity. Allowed: {', '.join(_DEFECT_SEVERITY)}")

    warranty_deadline = _compute_warranty_deadline(
        payload.practical_completion_date,
        payload.jurisdiction,
        payload.severity,
    )
    # Days remaining on warranty clock (None if no completion date)
    warranty_days_remaining: Optional[int] = None
    if warranty_deadline:
        try:
            dl = date.fromisoformat(warranty_deadline)
            warranty_days_remaining = (dl - date.today()).days
        except ValueError:
            pass

    defect_id = str(uuid.uuid4())
    doc = {
        "id": defect_id,
        "building_id": building_id,
        "title": payload.title,
        "description": payload.description,
        "category": payload.category,
        "severity": payload.severity,
        "location": payload.location,
        "lot_number": payload.lot_number,
        "status": "open",
        "jurisdiction": payload.jurisdiction.upper(),
        "practical_completion_date": payload.practical_completion_date,
        "warranty_deadline": warranty_deadline,
        "warranty_days_remaining": warranty_days_remaining,
        "builder_name": payload.builder_name,
        "builder_licence": payload.builder_licence,
        "builder_contact_email": payload.builder_contact_email,
        "builder_contact_phone": payload.builder_contact_phone,
        "discovered_date": payload.discovered_date or _now_iso()[:10],
        "notified_builder_at": payload.notified_builder_at,
        "remediated_at": None,
        "resolution_notes": None,
        "photo_urls": payload.photo_urls,
        "attachments": payload.attachments,
        "notes": payload.notes,
        "timeline": [
            {
                "at": _now_iso(),
                "by": current_user.get("id"),
                "note": "Defect logged",
                "note_type": "general",
            }
        ],
        "logged_by": current_user.get("id"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "cancelled_at": None,
    }
    result = await db.defects.insert_one(doc)

    await create_audit_log(
        action="defect_logged",
        resource_type="defects",
        resource_id=defect_id,
        user_id=current_user.get("id", ""),
        user_name=current_user.get("full_name", ""),
        details={"category": payload.category, "severity": payload.severity, "title": payload.title[:80]},
        building_id=building_id,
    )
    return doc


@router.get("")
async def list_defects(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        status: Optional[str] = Query(None, description="Filter by status"),
        category: Optional[str] = Query(None, description="Filter by category"),
        severity: Optional[str] = Query(None, description="Filter by severity"),
        _toggle: dict = Depends(require_feature("defects")),
        search: Optional[str] = Query(
            None, max_length=200,
            description="Case-insensitive text search across title, description, location, and builder name",
        ),
        warranty_expiring_days: Optional[int] = Query(
            None, ge=0, le=3650,
            description="Return defects whose warranty expires within this many days",
        ),
        include_cancelled: bool = Query(False),
        skip: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
):
    """List all defects for this building with optional filters."""
    _require_at_least_owner(current_user)
    db = _get_db()

    query: Dict[str, Any] = {"building_id": building_id}
    if not include_cancelled:
        query["cancelled_at"] = None
    if status:
        if status not in _DEFECT_STATUSES:
            raise HTTPException(422, f"Invalid status. Allowed: {', '.join(_DEFECT_STATUSES)}")
        query["status"] = status
    if category:
        query["category"] = category
    if severity:
        query["severity"] = severity
    if search:
        # Use re.escape to prevent regex injection from unsanitised user input
        pattern = re.escape(search)
        query["$or"] = [
            {"title": {"$regex": pattern, "$options": "i"}},
            {"description": {"$regex": pattern, "$options": "i"}},
            {"location": {"$regex": pattern, "$options": "i"}},
            {"builder_name": {"$regex": pattern, "$options": "i"}},
        ]
    if warranty_expiring_days is not None:
        cutoff = (date.today() + timedelta(days=warranty_expiring_days)).isoformat()
        query["warranty_deadline"] = {"$lte": cutoff, "$ne": None}

    cursor = db.defects.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit)
    docs = await cursor.to_list(limit)
    total = await db.defects.count_documents(query)
    return {"total": total, "skip": skip, "limit": limit, "items": docs}


@router.get("/warranty-summary")
async def get_warranty_summary(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        _toggle: dict = Depends(require_feature("defects")),
):
    """Aggregated warranty clock status for the building.

    Returns counts of defects grouped by warranty urgency:
    overdue (warranty expired), expiring_soon (≤90 days), active, no_warranty.
    """
    _require_manager(current_user)
    db = _get_db()

    today = date.today().isoformat()
    ninety_days = (date.today() + timedelta(days=90)).isoformat()

    pipeline = [
        {"$match": {"building_id": building_id, "cancelled_at": None, "status": {"$nin": ["closed", "cancelled"]}}},
        {"$group": {
            "_id": {
                "$switch": {
                    "branches": [
                        {"case": {"$eq": ["$warranty_deadline", None]}, "then": "no_warranty"},
                        {"case": {"$lt": ["$warranty_deadline", today]}, "then": "overdue"},
                        {"case": {"$lte": ["$warranty_deadline", ninety_days]}, "then": "expiring_soon"},
                    ],
                    "default": "active",
                }
            },
            "count": {"$sum": 1},
        }},
    ]
    result = await db.defects.aggregate(pipeline).to_list(10)
    summary = {"overdue": 0, "expiring_soon": 0, "active": 0, "no_warranty": 0}
    for row in result:
        key = row.get("_id", "no_warranty")
        if key in summary:
            summary[key] = row["count"]

    # Total open defects by severity
    severity_pipeline = [
        {"$match": {"building_id": building_id, "cancelled_at": None, "status": {"$nin": ["closed", "cancelled"]}}},
        {"$group": {"_id": "$severity", "count": {"$sum": 1}}},
    ]
    severity_result = await db.defects.aggregate(severity_pipeline).to_list(10)
    severity_counts = {row["_id"]: row["count"] for row in severity_result}

    return {
        "building_id": building_id,
        "warranty_clock": summary,
        "by_severity": severity_counts,
        "as_of": today,
    }


@router.get("/{defect_id}")
async def get_defect(
        defect_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        _toggle: dict = Depends(require_feature("defects")),
):
    """Get a single defect record."""
    _require_at_least_owner(current_user)
    db = _get_db()

    doc = await db.defects.find_one({"id": defect_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Defect not found.")
    return doc


@router.put("/{defect_id}")
async def update_defect(
        defect_id: str,
        payload: DefectUpdate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        _toggle: dict = Depends(require_feature("defects")),
):
    """Update a defect — status, resolution notes, builder details, etc."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.defects.find_one({"id": defect_id, "building_id": building_id})
    if not doc:
        raise HTTPException(404, "Defect not found.")
    if doc.get("cancelled_at"):
        raise HTTPException(409, "Cannot update a cancelled defect.")

    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}

    if "status" in updates and updates["status"] not in _DEFECT_STATUSES:
        raise HTTPException(422, f"Invalid status. Allowed: {', '.join(_DEFECT_STATUSES)}")
    if "category" in updates and updates["category"] not in _DEFECT_CATEGORIES:
        raise HTTPException(422, f"Invalid category.")
    if "severity" in updates and updates["severity"] not in _DEFECT_SEVERITY:
        raise HTTPException(422, f"Invalid severity.")

    # Recompute warranty deadline if completion date or jurisdiction changed
    new_completion = updates.get("practical_completion_date", doc.get("practical_completion_date"))
    new_jurisdiction = updates.get("jurisdiction", doc.get("jurisdiction", "ACT"))
    new_severity = updates.get("severity", doc.get("severity", "major"))
    if "practical_completion_date" in updates or "jurisdiction" in updates or "severity" in updates:
        new_deadline = _compute_warranty_deadline(new_completion, new_jurisdiction, new_severity)
        updates["warranty_deadline"] = new_deadline
        if new_deadline:
            try:
                updates["warranty_days_remaining"] = (date.fromisoformat(new_deadline) - date.today()).days
            except ValueError:
                pass

    updates["updated_at"] = _now_iso()
    await db.defects.update_one(
        {"id": defect_id, "building_id": building_id}, {"$set": updates}
    )
    return {**{k: v for k, v in doc.items() if k != "_id"}, **updates}


@router.delete("/{defect_id}", status_code=204)
async def cancel_defect(
        defect_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        _toggle: dict = Depends(require_feature("defects")),
):
    """Soft-cancel a defect (7-year retention, ACT UTMA 2011 ss.115–116)."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.defects.find_one({"id": defect_id, "building_id": building_id})
    if not doc:
        raise HTTPException(404, "Defect not found.")
    await db.defects.update_one(
        {"id": defect_id, "building_id": building_id},
        {"$set": {
            "cancelled_at": _now_iso(),
            "cancelled_by": current_user.get("id"),
            "status": "cancelled",
        }},
    )


@router.post("/{defect_id}/notes", status_code=201)
async def add_defect_note(
        defect_id: str,
        payload: DefectNoteCreate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        _toggle: dict = Depends(require_feature("defects")),
):
    """Append a timeline note to a defect (manager, EC member, or owner)."""
    _require_at_least_owner(current_user)
    db = _get_db()

    doc = await db.defects.find_one({"id": defect_id, "building_id": building_id}, {"_id": 0, "id": 1, "cancelled_at": 1})
    if not doc:
        raise HTTPException(404, "Defect not found.")
    if doc.get("cancelled_at"):
        raise HTTPException(409, "Cannot add notes to a cancelled defect.")

    entry = {
        "at": _now_iso(),
        "by": current_user.get("id"),
        "by_name": current_user.get("full_name", ""),
        "note": payload.note,
        "note_type": payload.note_type,
    }
    await db.defects.update_one(
        {"id": defect_id, "building_id": building_id},
        {"$push": {"timeline": entry}, "$set": {"updated_at": _now_iso()}},
    )
    return entry


@router.post("/{defect_id}/photos", status_code=201)
async def add_defect_photos(
        defect_id: str,
        payload: DefectPhotoAdd,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        _toggle: dict = Depends(require_feature("defects")),
):
    """Attach additional photo URLs to a defect record."""
    _require_at_least_owner(current_user)
    db = _get_db()

    doc = await db.defects.find_one({"id": defect_id, "building_id": building_id}, {"_id": 0, "id": 1, "cancelled_at": 1})
    if not doc:
        raise HTTPException(404, "Defect not found.")
    if doc.get("cancelled_at"):
        raise HTTPException(409, "Cannot add photos to a cancelled defect.")

    await db.defects.update_one(
        {"id": defect_id, "building_id": building_id},
        {
            "$push": {"photo_urls": {"$each": payload.photo_urls}},
            "$set": {"updated_at": _now_iso()},
        },
    )
    return {"defect_id": defect_id, "added": len(payload.photo_urls)}
