import html as html_lib
import uuid
from datetime import datetime, timezone

import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from database import db
from models.community_os import (
    VolunteerCompleteRequest,
    VolunteerEventCreate,
    VolunteerEventResponse,
    VolunteerEventStatus,
    VolunteerRegistrationCreate,
    VolunteerRegistrationResponse,
)
from models.user import UserRole
from services.volunteer_credits_service import apply_volunteer_credits
from utils.auth import get_current_user, get_current_building
from utils.helpers import create_audit_log

router = APIRouter(prefix="/volunteer", tags=["Volunteer"])
logger = logging.getLogger(__name__)

_MANAGER_ROLES = {
    UserRole.EC_MEMBER,
    UserRole.STRATA_MANAGER,
    UserRole.SUPER_ADMIN,
}


def _require_role(user: dict, allowed: set):
    """Generated function header.

    Function: _require_role
    Path: backend/routers/volunteer.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = user.get("effective_role") or user.get("role")
    if role not in allowed:
        raise HTTPException(status_code=403, detail="Insufficient permissions")


@router.get("", response_model=list)
async def list_volunteer_events(
        status: Optional[str] = Query(None),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: list_volunteer_events
    Path: backend/routers/volunteer.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query: dict = {"is_test_data": {"$ne": True}}
    if status:
        query["status"] = status
    cursor = db.volunteer_events.find(query, {"_id": 0})
    results = []
    async for doc in cursor:
        results.append(doc)
    return results


@router.post("", response_model=VolunteerEventResponse)
async def create_volunteer_event(
        data: VolunteerEventCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: create_volunteer_event
    Path: backend/routers/volunteer.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_role(current_user, _MANAGER_ROLES)
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "title": html_lib.escape(data.title),
        "description": html_lib.escape(data.description),
        "event_date": data.event_date,
        "location": html_lib.escape(data.location),
        "status": VolunteerEventStatus.SCHEDULED,
        "max_volunteers": data.max_volunteers,
        "credit_cents_per_hour": data.credit_cents_per_hour,
        "estimated_hours": data.estimated_hours,
        "created_by": current_user["id"],
        "created_by_name": current_user.get("full_name", ""),
        "registered_count": 0,
        "credits_processed": False,
        "created_at": now,
        "updated_at": now,
    }
    await db.volunteer_events.insert_one(doc)
    asyncio.create_task(
        create_audit_log("created", "volunteer_event", doc["id"], current_user["id"], current_user.get("full_name", ""),
                         {"title": doc["title"]}, building_id)
    )
    return VolunteerEventResponse(**doc)


@router.get("/{event_id}", response_model=VolunteerEventResponse)
async def get_volunteer_event(
        event_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_volunteer_event
    Path: backend/routers/volunteer.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await db.volunteer_events.find_one({"id": event_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Volunteer event not found")
    return VolunteerEventResponse(**doc)


@router.post("/{event_id}/register", response_model=VolunteerRegistrationResponse)
async def register_volunteer(
        event_id: str,
        data: VolunteerRegistrationCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: register_volunteer
    Path: backend/routers/volunteer.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = current_user.get("effective_role") or current_user.get("role")
    if role not in (UserRole.OWNER, UserRole.TENANT) | _MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    event = await db.volunteer_events.find_one({"id": event_id})
    if not event:
        raise HTTPException(status_code=404, detail="Volunteer event not found")
    if event["status"] != VolunteerEventStatus.SCHEDULED:
        raise HTTPException(status_code=400, detail="Event is not accepting registrations")

    existing = await db.volunteer_registrations.find_one(
        {"volunteer_event_id": event_id, "user_id": current_user["id"]})
    if existing:
        raise HTTPException(status_code=409, detail="Already registered for this event")

    max_v = event.get("max_volunteers")
    if max_v and event.get("registered_count", 0) >= max_v:
        raise HTTPException(status_code=400, detail="Event is full")

    now = datetime.now(timezone.utc).isoformat()
    reg = {
        "id": str(uuid.uuid4()),
        "volunteer_event_id": event_id,
        "user_id": current_user["id"],
        "user_name": current_user.get("full_name", ""),
        "lot_id": data.lot_id,
        "notes": data.notes,
        "hours_worked": None,
        "credit_applied_cents": 0,
        "created_at": now,
    }
    await db.volunteer_registrations.insert_one(reg)
    await db.volunteer_events.update_one({"id": event_id},
                                         {"$inc": {"registered_count": 1}, "$set": {"updated_at": now}})
    return VolunteerRegistrationResponse(**reg)


@router.put("/{event_id}/complete", response_model=VolunteerEventResponse)
async def complete_volunteer_event(
        event_id: str,
        data: VolunteerCompleteRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: complete_volunteer_event
    Path: backend/routers/volunteer.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_role(current_user, _MANAGER_ROLES)
    doc = await db.volunteer_events.find_one({"id": event_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Volunteer event not found")
    if doc["status"] != VolunteerEventStatus.SCHEDULED:
        raise HTTPException(status_code=400, detail="Event is not in scheduled state")

    now = datetime.now(timezone.utc).isoformat()
    update = {"status": VolunteerEventStatus.COMPLETED, "updated_at": now}
    await db.volunteer_events.update_one({"id": event_id}, {"$set": update})
    doc.update(update)

    if data.apply_credits:
        try:
            # apply_volunteer_credits reads participants + computes allocations from the event doc
            await apply_volunteer_credits(event_id, actor_user_id=current_user["id"])
            doc["credits_processed"] = True
        except ValueError as e:
            logger.error("Failed to apply volunteer credits: %s", e)
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error("Failed to apply volunteer credits: %s", e)
            raise HTTPException(status_code=500, detail=f"Credits failed: {e}")

    asyncio.create_task(
        create_audit_log("completed", "volunteer_event", event_id, current_user["id"],
                         current_user.get("full_name", ""), {"credits_applied": data.apply_credits}, building_id)
    )
    return VolunteerEventResponse(**doc)
