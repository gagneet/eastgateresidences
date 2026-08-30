"""Building Safety Feed — safety events log."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import db
from utils.auth import get_current_user, get_current_building

router = APIRouter(prefix="/safety", tags=["Safety"])

MANAGER_ROLES = {"strata_admin", "ec_member", "strata_manager", "super_admin"}


class SafetyEventCreate(BaseModel):
    event_type: str = Field(...,
                            pattern="^(access_incident|noise_complaint|property_damage|suspicious_activity|fire_alarm|lift_fault|water_leak)$")
    severity: str = Field("info", pattern="^(info|minor|major)$")
    location: str = Field(..., max_length=200)
    description: str = Field(..., max_length=500)
    visibility: str = Field("all_residents", pattern="^(all_residents|ec_only)$")


@router.get("/events")
async def list_safety_events(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: list_safety_events
    Path: backend/routers/safety.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = current_user.get("effective_role") or current_user.get("role", "owner")
    vis_filter: dict = {"visibility": "all_residents"}
    if role in MANAGER_ROLES:
        vis_filter = {}  # managers see all

    events = await db._db.safety_events.find(
        {**vis_filter, "building_id": building_id, "is_test_data": {"$ne": True}},
        {"_id": 0},
    ).sort("created_at", -1).limit(30).to_list(30)
    return events


@router.post("/events", status_code=201)
async def create_safety_event(
        body: SafetyEventCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: create_safety_event
    Path: backend/routers/safety.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = current_user.get("effective_role") or current_user.get("role")
    if role not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="EC member or manager required")
    event = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        **body.model_dump(),
        "reported_by": current_user["id"],
        "is_resolved": False,
        "resolved_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "is_test_data": False,
    }
    await db._db.safety_events.insert_one(event)
    event.pop("_id", None)
    return event


@router.patch("/events/{event_id}/resolve")
async def resolve_safety_event(
        event_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: resolve_safety_event
    Path: backend/routers/safety.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = current_user.get("effective_role") or current_user.get("role")
    if role not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Manager access required")
    result = await db._db.safety_events.update_one(
        {"id": event_id, "building_id": building_id},
        {"$set": {"is_resolved": True, "resolved_at": datetime.now(timezone.utc).isoformat()}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"resolved": True}
