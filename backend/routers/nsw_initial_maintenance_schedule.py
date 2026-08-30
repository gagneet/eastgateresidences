"""
GAP-JUR-NSW-005: Initial Maintenance Schedule (SSMA Schedule 3 / s.80).

# @featuretrace:nsw-initial-maintenance-schedule — SSMA initial maintenance schedule register.
# Layer: router
# Data flow: POST /nsw/initial-maintenance-schedule → nsw_initial_maintenance_schedules collection (building-scoped).
# Related: backend/routers/ppm.py (ongoing PPM schedules)
#           backend/routers/nsw_compliance.py (NSW compliance hub)
#           backend/domain/jurisdictional_rules.py (statutory deadlines)
#           frontend: /compliance/nsw-maintenance-schedule

Endpoints:
  POST   /nsw/initial-maintenance-schedule                   — create IMS for building
  GET    /nsw/initial-maintenance-schedule                   — list IMS versions
  GET    /nsw/initial-maintenance-schedule/current           — latest active version
  GET    /nsw/initial-maintenance-schedule/{ims_id}          — single version
  PUT    /nsw/initial-maintenance-schedule/{ims_id}          — update / AGM review
  POST   /nsw/initial-maintenance-schedule/{ims_id}/review   — record AGM review completion
  GET    /nsw/initial-maintenance-schedule/compliance        — compliance status

Statutory basis:
  SSMA 2015 s.80 + Schedule 3 — the initial maintenance schedule sets out common property
  items requiring maintenance, estimated costs, and responsible party. Must be prepared:
    - By the developer at plan registration (or within 6 months of plan creation), OR
    - By the owners corporation at the first AGM if the developer has not provided one.
  Must be reviewed at each AGM. Failure to maintain: $5,500 (OC) + ongoing obligations.
  Record retention: 7 years (SSMA 2015 s.184A) — never hard-delete.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, date, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from utils.auth import get_current_building, get_current_user
from utils.helpers import create_audit_log
from utils.permissions import require_feature

logger = logging.getLogger(__name__)

# GAP-JUR-NSW-005 feature gate.
router = APIRouter(
    prefix="/nsw/initial-maintenance-schedule",
    tags=["NSW Compliance"],
    dependencies=[Depends(require_feature("nsw_maintenance_schedule"))],
)

_RESPONSIBLE_PARTY_OPTIONS = ["owners_corporation", "lot_owner", "shared", "building_manager"]
_ITEM_CATEGORIES = [
    "fire_safety",
    "lift_and_escalator",
    "common_area_lighting",
    "roof_and_guttering",
    "facade_and_cladding",
    "plumbing_drainage",
    "electrical_common",
    "hvac_common",
    "pool_and_spa",
    "landscaping",
    "car_park",
    "security_systems",
    "intercom",
    "structural",
    "other",
]


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/nsw_initial_maintenance_schedule.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/nsw_initial_maintenance_schedule.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_manager(user: dict) -> dict:
    """Generated function header.

    Function: _require_manager
    Path: backend/routers/nsw_initial_maintenance_schedule.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _role = user.get("effective_role") or user.get("role", "guest")
    if _role not in {"super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"}:
        raise HTTPException(403, "Strata manager or chairman access required.")
    return user


def _require_ec(user: dict) -> dict:
    """Generated function header.

    Function: _require_ec
    Path: backend/routers/nsw_initial_maintenance_schedule.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _role = user.get("effective_role") or user.get("role", "guest")
    if _role not in {"super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"}:
        raise HTTPException(403, "EC or manager access required.")
    return user


# ── Models ────────────────────────────────────────────────────────────────────


class MaintenanceItem(BaseModel):
    """A single common property maintenance line item."""

    model_config = ConfigDict(extra="ignore")

    item_number: Optional[str] = Field(None, max_length=20, description="e.g. '1', 'A1', 'FIRE-01'")
    description: str = Field(..., min_length=5, max_length=500)
    category: str = Field(
        ...,
        pattern="^(fire_safety|lift_and_escalator|common_area_lighting|roof_and_guttering|facade_and_cladding|plumbing_drainage|electrical_common|hvac_common|pool_and_spa|landscaping|car_park|security_systems|intercom|structural|other)$",
        description=f"One of: {_ITEM_CATEGORIES}",
    )
    location: Optional[str] = Field(None, max_length=200, description="e.g. 'Level 2 lobby', 'Rooftop'")
    frequency: str = Field(
        ..., description="Maintenance frequency: 'monthly', 'quarterly', 'annually', 'as_required', 'biennial'"
    )
    estimated_annual_cost_cents: int = Field(..., ge=0, description="Annual cost estimate in cents")
    responsible_party: str = Field(
        "owners_corporation",
        pattern="^(owners_corporation|lot_owner|shared|building_manager)$",
        description=f"Who is responsible. One of: {_RESPONSIBLE_PARTY_OPTIONS}",
    )
    first_due_date: Optional[str] = Field(None, description="ISO-8601 date of first maintenance due")
    statutory_reference: Optional[str] = Field(
        None, max_length=200, description="e.g. 'EP&A Act Schedule 5 — fire safety'"
    )
    notes: Optional[str] = Field(None, max_length=1000)


class IMSCreate(BaseModel):
    """
    Create an Initial Maintenance Schedule under SSMA 2015 Schedule 3.

    The IMS must be prepared by the developer at plan registration, or by
    the owners corporation at the first AGM. It must cover all common property
    maintenance obligations for the building.
    """

    model_config = ConfigDict(extra="ignore")

    prepared_by: str = Field(
        ..., min_length=2, max_length=200,
        description="Name of entity that prepared this IMS (developer, OC, or managing agent)"
    )
    prepared_by_type: str = Field(
        ...,
        pattern="^(developer|owners_corporation|strata_manager)$",
        description="Entity type that prepared this IMS",
    )
    strata_plan_registration_date: Optional[str] = Field(
        None, description="ISO-8601 date — when the strata plan was registered with NSW LRS"
    )
    plan_number: Optional[str] = Field(None, max_length=50, description="e.g. 'SP12345'")
    effective_date: str = Field(..., description="ISO-8601 date — when this IMS takes effect")
    review_at_agm_date: Optional[str] = Field(
        None, description="ISO-8601 date of the AGM at which this IMS will be/was reviewed"
    )
    items: List[MaintenanceItem] = Field(..., min_length=1, description="Common property items")
    total_estimated_annual_cost_cents: Optional[int] = Field(
        None, ge=0, description="Auto-computed if None; override if itemised total differs"
    )
    notes: Optional[str] = Field(None, max_length=3000)


class IMSUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    review_at_agm_date: Optional[str] = None
    items: Optional[List[MaintenanceItem]] = None
    notes: Optional[str] = Field(None, max_length=3000)
    status: Optional[str] = Field(None, pattern="^(active|superseded|archived)$")


class AGMReviewLog(BaseModel):
    """Record the outcome of an AGM review of the IMS."""

    model_config = ConfigDict(extra="ignore")

    agm_date: str = Field(..., description="ISO-8601 date of AGM")
    reviewed_by: str = Field(..., min_length=2, max_length=200)
    outcome: str = Field(
        ...,
        pattern="^(no_changes|updated|items_added|items_removed|superseded_by_new)$",
    )
    superseded_ims_id: Optional[str] = Field(
        None, description="If outcome=superseded_by_new, provide the new IMS id"
    )
    notes: Optional[str] = Field(None, max_length=2000)


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post("", status_code=201)
async def create_initial_maintenance_schedule(
        payload: IMSCreate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """
    GAP-JUR-NSW-005: Create an Initial Maintenance Schedule.

    SSMA 2015 Schedule 3 requires the OC (or developer at registration) to
    prepare an IMS covering all common property maintenance obligations.
    The IMS must be reviewed at each AGM.
    """
    _require_manager(current_user)
    db = _get_db()

    total_cents = payload.total_estimated_annual_cost_cents
    if total_cents is None:
        total_cents = sum(item.estimated_annual_cost_cents for item in payload.items)

    doc = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "prepared_by": payload.prepared_by,
        "prepared_by_type": payload.prepared_by_type,
        "strata_plan_registration_date": payload.strata_plan_registration_date,
        "plan_number": payload.plan_number,
        "effective_date": payload.effective_date,
        "review_at_agm_date": payload.review_at_agm_date,
        "items": [item.model_dump() for item in payload.items],
        "total_estimated_annual_cost_cents": total_cents,
        "item_count": len(payload.items),
        "notes": payload.notes,
        "status": "active",
        "agm_review_log": [],
        "created_by": current_user.get("id"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    # Supersede any previous active IMS for this building
    await db.nsw_initial_maintenance_schedules.update_many(
        {"building_id": building_id, "status": "active"},
        {"$set": {"status": "superseded", "updated_at": _now_iso()}},
    )

    await db.nsw_initial_maintenance_schedules.insert_one(doc)
    await create_audit_log(
        action="nsw_ims_created",
        resource_type="nsw_initial_maintenance_schedules",
        resource_id=doc["id"],
        user_id=current_user.get("id", ""),
        user_name=current_user.get("full_name", ""),
        details={
            "prepared_by_type": payload.prepared_by_type,
            "item_count": len(payload.items),
            "effective_date": payload.effective_date,
            "total_estimated_annual_cost_cents": total_cents,
        },
        building_id=building_id,
    )
    return {k: v for k, v in doc.items() if k != "_id"}


@router.get("/current")
async def get_current_ims(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Get the current active Initial Maintenance Schedule."""
    _require_ec(current_user)
    db = _get_db()

    doc = await db.nsw_initial_maintenance_schedules.find_one(
        {"building_id": building_id, "status": "active"},
        {"_id": 0},
        sort=[("effective_date", -1)],
    )
    if not doc:
        return {
            "building_id": building_id,
            "has_ims": False,
            "message": "No active Initial Maintenance Schedule found. SSMA 2015 Schedule 3 requires one.",
        }
    return {**doc, "has_ims": True}


@router.get("/compliance")
async def get_ims_compliance_status(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """
    GAP-JUR-NSW-005 compliance status.

    Returns statutory compliance state:
    - whether an IMS exists and is active
    - when the next AGM review is due
    - items overdue for first maintenance
    """
    _require_ec(current_user)
    db = _get_db()

    today_str = date.today().isoformat()

    active_ims = await db.nsw_initial_maintenance_schedules.find_one(
        {"building_id": building_id, "status": "active"},
        {"_id": 0},
        sort=[("effective_date", -1)],
    )

    has_ims = active_ims is not None
    review_overdue = False
    items_overdue_count = 0

    if active_ims:
        review_date = active_ims.get("review_at_agm_date")
        if review_date and review_date < today_str:
            review_overdue = True

        items = active_ims.get("items", [])
        items_overdue_count = sum(
            1 for item in items
            if item.get("first_due_date") and item["first_due_date"] < today_str
        )

    return {
        "building_id": building_id,
        "ssma_2015_schedule_3_compliant": has_ims,
        "has_active_ims": has_ims,
        "agm_review_overdue": review_overdue,
        "items_overdue_first_maintenance": items_overdue_count,
        "ims_id": active_ims.get("id") if active_ims else None,
        "effective_date": active_ims.get("effective_date") if active_ims else None,
        "next_review_date": active_ims.get("review_at_agm_date") if active_ims else None,
    }


@router.get("")
async def list_ims_versions(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        skip: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
):
    """List all IMS versions for this building (newest first)."""
    _require_ec(current_user)
    db = _get_db()

    cursor = (
        db.nsw_initial_maintenance_schedules
        .find({"building_id": building_id}, {"_id": 0, "items": 0})
        .sort("effective_date", -1)
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(limit)
    total = await db.nsw_initial_maintenance_schedules.count_documents({"building_id": building_id})
    return {"total": total, "skip": skip, "limit": limit, "items": docs}


@router.get("/{ims_id}")
async def get_ims(
        ims_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Get a single IMS version including all maintenance items."""
    _require_ec(current_user)
    db = _get_db()

    doc = await db.nsw_initial_maintenance_schedules.find_one(
        {"id": ims_id, "building_id": building_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(404, "Initial Maintenance Schedule not found.")
    return doc


@router.put("/{ims_id}")
async def update_ims(
        ims_id: str,
        payload: IMSUpdate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Update an IMS (e.g. pre-AGM review preparation, add/remove items)."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.nsw_initial_maintenance_schedules.find_one(
        {"id": ims_id, "building_id": building_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(404, "Initial Maintenance Schedule not found.")

    updates = payload.model_dump(exclude_unset=True)
    if "items" in updates and updates["items"] is not None:
        item_list = updates["items"]
        updates["items"] = [
            i.model_dump() if hasattr(i, "model_dump") else i for i in item_list
        ]
        updates["item_count"] = len(updates["items"])
        updates["total_estimated_annual_cost_cents"] = sum(
            i.get("estimated_annual_cost_cents", 0) for i in updates["items"]
        )
    updates["updated_at"] = _now_iso()
    updates["updated_by"] = current_user.get("id")

    await db.nsw_initial_maintenance_schedules.update_one(
        {"id": ims_id, "building_id": building_id}, {"$set": updates}
    )
    await create_audit_log(
        action="nsw_ims_updated",
        resource_type="nsw_initial_maintenance_schedules",
        resource_id=ims_id,
        user_id=current_user.get("id", ""),
        user_name=current_user.get("full_name", ""),
        details={"fields_changed": list(updates.keys())},
        building_id=building_id,
    )
    return {**doc, **updates}


@router.post("/{ims_id}/review", status_code=201)
async def record_agm_review(
        ims_id: str,
        payload: AGMReviewLog,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """
    Record that this IMS was reviewed at an AGM (SSMA 2015 s.80(5)).

    Appends an AGM review log entry. If outcome is 'superseded_by_new',
    marks this IMS as superseded.
    """
    _require_manager(current_user)
    db = _get_db()

    doc = await db.nsw_initial_maintenance_schedules.find_one(
        {"id": ims_id, "building_id": building_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(404, "Initial Maintenance Schedule not found.")
    if payload.outcome == "superseded_by_new" and not payload.superseded_ims_id:
        raise HTTPException(422, "superseded_ims_id is required when outcome is superseded_by_new.")

    review_entry = {
        **payload.model_dump(),
        "recorded_by": current_user.get("id"),
        "recorded_at": _now_iso(),
    }

    update_fields: dict = {
        "updated_at": _now_iso(),
        "last_reviewed_at": _now_iso(),
    }
    if payload.outcome == "superseded_by_new":
        update_fields["status"] = "superseded"

    await db.nsw_initial_maintenance_schedules.update_one(
        {"id": ims_id, "building_id": building_id},
        {
            "$push": {"agm_review_log": review_entry},
            "$set": update_fields,
        },
    )
    await create_audit_log(
        action="nsw_ims_agm_review_recorded",
        resource_type="nsw_initial_maintenance_schedules",
        resource_id=ims_id,
        user_id=current_user.get("id", ""),
        user_name=current_user.get("full_name", ""),
        details={"agm_date": payload.agm_date, "outcome": payload.outcome},
        building_id=building_id,
    )
    return {**review_entry, "ims_id": ims_id}
