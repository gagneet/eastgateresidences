"""
Meetings router module.

This module handles all meeting-related routes including meetings, todos,
and AGM (Annual General Meeting) functionality with motions and voting.
"""

import uuid
from datetime import datetime, timezone

import asyncio
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import List, Optional

from database import db
from models.community import (
    MeetingCreate,
    MeetingResponse,
    TodoCreate,
    TodoResponse,
    AGMCreate,
    AGMResponse,
    AGMMotionCreate,
    AGMMotionResponse,
    AGMVoteCreate,
    AGMAttendanceUpdate,
    coalesce_agm_response,
)
from models.user import UserRole
from utils.activity_helper import log_activity
from utils.auth import get_approved_user, get_current_building, effective_role
from utils.helpers import broadcast_user_notification, create_audit_log
from utils.permissions import get_user_permissions

# Create router
router = APIRouter(prefix="")

# Security
security = HTTPBearer(auto_error=False)


# ==================== MEETING ROUTES ====================

@router.post("/meetings", response_model=MeetingResponse)
async def create_meeting(
        meeting: MeetingCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create a new meeting.
    
    Creates a new meeting with the specified details.
    Requires manage meetings permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized to create meetings")

    meeting_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    meeting_doc = {
        "id": meeting_id,
        **meeting.model_dump(),
        "minutes": None,
        "status": "scheduled",
        "created_by": current_user["id"],
        "created_at": now,
        "updated_at": now
    }

    await db.meetings.insert_one(meeting_doc)

    asyncio.create_task(create_audit_log(
        action="created", resource_type="meeting", resource_id=meeting_id,
        user_id=current_user["id"], user_name=current_user["full_name"],
        details={"title": meeting.title, "date": meeting.meeting_date},
        building_id=building_id
    ))

    # Broadcast notification
    asyncio.create_task(broadcast_user_notification(
        recipient_roles=["all"],
        title="New Meeting Scheduled",
        message=f"A new meeting '{meeting.title}' has been scheduled for {meeting.meeting_date}.",
        notification_type="meeting",
        link="/governance/meetings",
        building_id=building_id
    ))

    return MeetingResponse(**meeting_doc)


@router.get("/meetings", response_model=List[MeetingResponse])
async def get_meetings(
        status: Optional[str] = None,
        limit: Optional[int] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get all meetings.

    Retrieves all meetings, optionally filtered by status and capped by limit.
    Requires view meetings permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_meetings:
        raise HTTPException(status_code=403, detail="Not authorized to view meetings")

    query = {"building_id": building_id}
    if status:
        query["status"] = status

    # Callers asking for scheduled meetings (dashboard "next meeting" widgets) want the
    # nearest upcoming one first — a scheduled meeting hasn't happened yet, so ascending
    # order surfaces it correctly. Other/no status filter keeps the historical
    # most-recent-first order. Previously always descending, so `?status=scheduled&limit=1`
    # returned the furthest-future meeting instead of the next one, and `limit` itself was
    # silently ignored (FastAPI drops unrecognised query params rather than erroring).
    sort_direction = 1 if status == "scheduled" else -1
    cap = min(limit, 100) if limit and limit > 0 else 100
    meetings = await db.meetings.find(query, {"_id": 0}).sort("meeting_date", sort_direction).to_list(cap)
    return [MeetingResponse(**m) for m in meetings]


@router.get("/meetings/{meeting_id}", response_model=MeetingResponse)
async def get_meeting(
        meeting_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get a specific meeting by ID.
    
    Retrieves detailed information about a specific meeting.
    Requires view meetings permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    meeting = await db.meetings.find_one({"id": meeting_id, "building_id": building_id}, {"_id": 0})
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    return MeetingResponse(**meeting)


@router.put("/meetings/{meeting_id}", response_model=MeetingResponse)
async def update_meeting(
        meeting_id: str,
        minutes: Optional[str] = None,
        status: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Update a meeting.
    
    Updates meeting details such as minutes and status.
    Requires manage meetings permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    update_dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if minutes is not None:
        update_dict["minutes"] = minutes
    if status is not None:
        update_dict["status"] = status

    await db.meetings.update_one({"id": meeting_id, "building_id": building_id}, {"$set": update_dict})

    meeting = await db.meetings.find_one({"id": meeting_id, "building_id": building_id}, {"_id": 0})
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    asyncio.create_task(create_audit_log(
        action="updated", resource_type="meeting", resource_id=meeting_id,
        user_id=current_user["id"], user_name=current_user["full_name"],
        details={k: v for k, v in update_dict.items() if k != "updated_at"},
        building_id=building_id
    ))

    return MeetingResponse(**meeting)


# ==================== TODO ROUTES ====================

@router.post("/todos", response_model=TodoResponse)
async def create_todo(
        todo: TodoCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create a new todo item.
    
    Creates a new todo item with optional assignment to a user.
    Requires manage meetings permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized to create todos")

    todo_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Get assigned user name if exists
    assigned_to_name = None
    if todo.assigned_to:
        # Sentinel 🛡️: Verify user is an active member of this building before fetching name (BOLA Protection)
        membership = await db.memberships.find_one({
            "building_id": building_id,
            "user_id": todo.assigned_to,
            "is_active": True,
        })
        if not membership:
            raise HTTPException(status_code=400, detail="Assigned user does not belong to this building")

        assigned_user = await db.users.find_one({"id": todo.assigned_to}, {"_id": 0, "full_name": 1})
        if assigned_user:
            assigned_to_name = assigned_user["full_name"]

    todo_doc = {
        "id": todo_id,
        "building_id": building_id,
        **todo.model_dump(),
        "assigned_to_name": assigned_to_name,
        "status": "pending",
        "created_by": current_user["id"],
        "created_at": now,
        "updated_at": now
    }

    await db.todos.insert_one(todo_doc)
    return TodoResponse(**todo_doc)


@router.get("/todos", response_model=List[TodoResponse])
async def get_todos(
        status: Optional[str] = None,
        meeting_id: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get all todos.
    
    Retrieves all todos, optionally filtered by status or meeting ID.
    Requires view meetings permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    query = {"building_id": building_id}
    if status:
        query["status"] = status
    if meeting_id:
        query["meeting_id"] = meeting_id

    todos = await db.todos.find(query, {"_id": 0}).sort("due_date", 1).to_list(1000)
    return [TodoResponse(**t) for t in todos]


@router.put("/todos/{todo_id}", response_model=TodoResponse)
async def update_todo(
        todo_id: str,
        status: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Update a todo item.
    
    Updates the status of a todo item.
    Requires manage meetings permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    update_dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if status:
        update_dict["status"] = status

    await db.todos.update_one({"id": todo_id, "building_id": building_id}, {"$set": update_dict})

    todo = await db.todos.find_one({"id": todo_id, "building_id": building_id}, {"_id": 0})
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found")

    return TodoResponse(**todo)


@router.delete("/todos/{todo_id}")
async def delete_todo(
        todo_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Delete a todo item.
    
    Removes a todo item by ID.
    Requires manage meetings permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.todos.delete_one({"id": todo_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Todo not found")

    return {"message": "Todo deleted successfully"}


# ==================== AGM ROUTES ====================

@router.post("/agm", response_model=AGMResponse)
async def create_agm(
        data: AGMCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create a new AGM (Annual General Meeting).
    
    Creates a new AGM event with the specified details.
    Requires manage meetings permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    agm_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    agm_doc = {
        "id": agm_id,
        "building_id": building_id,
        **data.model_dump(),
        "status": "upcoming",
        "motions": [],
        "created_at": now
    }

    await db.agm.insert_one(agm_doc)

    asyncio.create_task(create_audit_log(
        action="created", resource_type="agm", resource_id=agm_id,
        user_id=current_user["id"], user_name=current_user["full_name"],
        details={"title": data.title, "date": data.date},
        building_id=building_id
    ))

    # Broadcast notification
    asyncio.create_task(broadcast_user_notification(
        recipient_roles=["owner", "strata_admin", "ec_member", "super_admin", "strata_manager"],
        title="New AGM Scheduled",
        message=f"A new AGM has been scheduled for {data.date}. Please review the details and motions.",
        notification_type="agm",
        link="/governance/agm",
        building_id=building_id
    ))

    # Log to community activity feed
    asyncio.create_task(log_activity(
        activity_type="vote",
        title=f"AGM Scheduled: {data.title}",
        entity_id=agm_id,
        priority=1,
        metadata={"date": data.date}
    ))

    return AGMResponse(**agm_doc)


@router.get("/agm", response_model=List[AGMResponse])
async def get_agms(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get all AGMs.

    Retrieves all AGM events, sorted by date.
    """
    agms = await db.agm.find({"building_id": building_id}, {"_id": 0}).sort("date", -1).to_list(20)
    # Shared resilient builder: a legacy/dirty AGM doc (e.g. null agenda) never
    # 500s the list. See models.community.coalesce_agm_response.
    return [r for a in agms if (r := coalesce_agm_response(a)) is not None]


@router.post("/agm/{agm_id}/motions", response_model=AGMMotionResponse)
async def create_motion(
        agm_id: str,
        data: AGMMotionCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create a new motion for an AGM.
    
    Creates a new motion to be voted on during an AGM.
    Requires manage meetings permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    motion_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    motion_doc = {
        "id": motion_id,
        "building_id": building_id,
        "agm_id": agm_id,
        "title": data.title,
        "description": data.description,
        "motion_type": data.motion_type,
        "votes_for": 0,
        "votes_against": 0,
        "votes_abstain": 0,
        "status": "pending",
        "voters": [],
        "created_at": now
    }

    await db.agm_motions.insert_one(motion_doc)

    asyncio.create_task(create_audit_log(
        action="motion_created", resource_type="agm_motion", resource_id=motion_id,
        user_id=current_user["id"], user_name=current_user["full_name"],
        details={"agm_id": agm_id, "title": data.title, "motion_type": data.motion_type},
        building_id=building_id
    ))

    # Log to community activity feed
    asyncio.create_task(log_activity(
        activity_type="vote",
        title=f"New AGM Motion: {data.title}",
        entity_id=motion_id,
        priority=2,
        metadata={"agm_id": agm_id}
    ))

    return AGMMotionResponse(**motion_doc)


@router.get("/agm/{agm_id}/motions", response_model=List[AGMMotionResponse])
async def get_motions(
        agm_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get all motions for an AGM.
    
    Retrieves all motions associated with a specific AGM.
    """
    motions = await db.agm_motions.find({"agm_id": agm_id, "building_id": building_id}, {"_id": 0}).to_list(50)
    return [AGMMotionResponse(**m) for m in motions]


@router.get("/agm/{agm_id}/attendance")
async def get_agm_attendance(
        agm_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get attendance list for an AGM with enriched user details.
    Performance Optimization⚡: Using a single MongoDB aggregation pipeline with $lookup to join user details.
    This eliminates the N+1 query pattern on the frontend and reduces database round-trips.
    """
    # 1. Validate AGM exists to prevent enumeration attacks
    agm = await db.agm.find_one({"id": agm_id, "building_id": building_id})
    if not agm:
        raise HTTPException(status_code=404, detail="AGM not found")

    manager_roles = [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]
    is_manager = effective_role(current_user) in manager_roles

    if not is_manager:
        # Authorization: Owners can only see their own attendance record for this AGM
        query = {"agm_id": agm_id, "user_id": current_user["id"], "building_id": building_id}
    else:
        # Managers see all attendees for this AGM
        query = {"agm_id": agm_id, "building_id": building_id}

    # Performance Note: 'users.id' is a unique indexed field, ensuring O(1) lookup per record.
    # The pipeline is limited to 200 records to maintain consistent performance.
    pipeline = [
        {"$match": query},
        # Join with users collection for attendee details
        {"$lookup": {
            "from": "users",
            "localField": "user_id",
            "foreignField": "id",
            "as": "user_details"
        }},
        # Join with users collection for proxy details (if any)
        {"$lookup": {
            "from": "users",
            "localField": "proxy_id",
            "foreignField": "id",
            "as": "proxy_details"
        }},
        {"$addFields": {
            "user_info": {"$arrayElemAt": ["$user_details", 0]},
            "proxy_info": {"$arrayElemAt": ["$proxy_details", 0]}
        }},
        # Data Integrity: Filter out orphaned records where the referenced user no longer exists
        {"$match": {
            "user_info": {"$ne": None}
        }},
        {"$project": {
            "_id": 0,
            "agm_id": 1,
            "user_id": 1,
            "status": 1,
            "proxy_id": 1,
            "confirmed_by_admin": 1,
            "confirmed_at": 1,
            "updated_at": 1,
            "full_name": "$user_info.full_name",
            "unit_number": "$user_info.unit_number",
            "proxy_name": "$proxy_info.full_name",
            "proxy_unit": "$proxy_info.unit_number"
        }}
    ]

    attendance = await db.agm_attendance.aggregate(pipeline).to_list(200)
    return attendance


@router.post("/agm/{agm_id}/attendance")
async def update_attendance(
        agm_id: str,
        data: AGMAttendanceUpdate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Update attendance status for a user."""
    # Owners can update their own status
    if current_user["id"] != data.user_id and effective_role(current_user) not in [
            UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    now = datetime.now(timezone.utc).isoformat()

    # Sentinel 🛡️: Verify user and proxy are active members of this building (BOLA Protection)
    async def _none_coro():
        """Generated function header.

        Function: _none_coro
        Path: backend/routers/meetings.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return None

    user_membership_task = db.memberships.find_one({
        "building_id": building_id,
        "user_id": data.user_id,
        "is_active": True,
    })
    proxy_membership_task = (
        db.memberships.find_one({
            "building_id": building_id,
            "user_id": data.proxy_id,
            "is_active": True,
        })
        if data.proxy_id else _none_coro()
    )

    user_membership, proxy_membership = await asyncio.gather(user_membership_task, proxy_membership_task)

    if not user_membership:
        raise HTTPException(status_code=400, detail="User does not belong to this building")
    if data.proxy_id and not proxy_membership:
        raise HTTPException(status_code=400, detail="Proxy user does not belong to this building")

    attendance_doc = {
        "agm_id": agm_id,
        "user_id": data.user_id,
        "status": data.status,
        "proxy_id": data.proxy_id,
        "confirmed_by_admin": False,
        "updated_at": now,
        "building_id": building_id
    }

    await db.agm_attendance.update_one(
        {"agm_id": agm_id, "user_id": data.user_id, "building_id": building_id},
        {"$set": attendance_doc},
        upsert=True
    )

    return {"message": "Attendance updated"}


@router.post("/agm/{agm_id}/attendance/{user_id}/confirm")
async def confirm_attendance(
        agm_id: str,
        user_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Admin confirms user is present at the AGM."""
    if effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    await db.agm_attendance.update_one(
        {"agm_id": agm_id, "user_id": user_id, "building_id": building_id},
        {"$set": {"confirmed_by_admin": True, "confirmed_at": datetime.now(timezone.utc).isoformat()}}
    )

    return {"message": "Attendance confirmed"}


@router.post("/agm/motions/{motion_id}/vote")
async def cast_vote(
        motion_id: str,
        data: AGMVoteCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Cast a vote on a motion.
    
    Records a vote (for, against, or abstain) on an AGM motion.
    Sentinel 🛡️: Enforces BOLA protection and lot-based voting integrity.
    """
    motion = await db.agm_motions.find_one({"id": motion_id, "building_id": building_id}, {"_id": 0})
    if not motion:
        raise HTTPException(status_code=404, detail="Motion not found")

    agm_id = motion["agm_id"]
    agm = await db.agm.find_one({"id": agm_id, "building_id": building_id})
    if not agm:
        raise HTTPException(status_code=404, detail="AGM not found")

    # Determine the target unit for this vote
    target_unit = data.proxy_for or current_user.get("unit_number")
    if not target_unit:
        raise HTTPException(status_code=400, detail="Target unit number required for voting")

    # Sentinel 🛡️: BOLA Protection — Verify that the voter is authorized for the target unit.
    if effective_role(current_user) != UserRole.SUPER_ADMIN:
        # 1. Check direct ownership
        is_owner = await db.user_units.find_one({
            "building_id": building_id,
            "user_id": current_user["id"],
            "unit_number": target_unit,
            "is_active": True,
            "$or": [
                {"role_at_unit": "owner"},
                {"role_at_unit": {"$exists": False}},
                {"role_at_unit": None},
            ],
        })

        authorized = bool(is_owner)

        # 2. Check proxy authorization if not the direct owner.
        # Find all owners of target_unit first, then check if any of them granted proxy to
        # the current user. This handles units with multiple owners correctly — find_one on
        # proxy_id alone would return an arbitrary grantor and reject valid proxy votes.
        if not authorized:
            target_unit_owners = await db.user_units.find({
                "building_id": building_id,
                "unit_number": target_unit,
                "is_active": True,
                "$or": [
                    {"role_at_unit": "owner"},
                    {"role_at_unit": {"$exists": False}},
                    {"role_at_unit": None},
                ],
            }).to_list(length=None)
            owner_ids = [o["user_id"] for o in target_unit_owners if o.get("user_id")]

            if owner_ids:
                proxy_authorized = await db.agm_attendance.find_one({
                    "building_id": building_id,
                    "agm_id": agm_id,
                    "proxy_id": current_user["id"],
                    "user_id": {"$in": owner_ids},
                })
                if proxy_authorized:
                    authorized = True

        if not authorized:
            raise HTTPException(
                status_code=403,
                detail=f"You are not authorized to vote for Unit {target_unit}"
            )

    # Validate vote value BEFORE any write — an invalid value must not be stored.
    allowed_votes = ["for", "against", "abstain"]
    if data.vote not in allowed_votes:
        raise HTTPException(status_code=400, detail=f"Invalid vote value. Must be one of: {', '.join(allowed_votes)}")

    # Check if live voting is allowed (only if AGM is in progress)
    if not data.is_pre_vote and agm.get("status") != "in_progress":
        raise HTTPException(status_code=400, detail="Live voting only allowed when AGM is in progress")

    now = datetime.now(timezone.utc).isoformat()
    vote_doc = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "motion_id": motion_id,
        "agm_id": agm_id,
        "user_id": current_user["id"],
        "user_name": current_user["full_name"],
        "unit_number": target_unit,
        "vote": data.vote,
        "proxy_for": data.proxy_for,
        "is_pre_vote": data.is_pre_vote,
        "created_at": now
    }

    # Sentinel 🛡️: Atomic per-lot voting integrity — $setOnInsert upsert eliminates the
    # find_one → insert_one race condition that could allow duplicate votes under concurrency.
    result = await db.agm_votes.update_one(
        {"building_id": building_id, "motion_id": motion_id, "unit_number": target_unit},
        {"$setOnInsert": vote_doc},
        upsert=True
    )
    if result.upserted_id is None:
        raise HTTPException(status_code=409, detail=f"A vote has already been cast for Unit {target_unit}")

    vote_field = f"votes_{data.vote}"
    await db.agm_motions.update_one(
        {"id": motion_id},
        {
            "$inc": {vote_field: 1},
            "$push": {"voters": current_user["id"]}
        }
    )

    asyncio.create_task(create_audit_log(
        action="vote_cast", resource_type="agm_vote", resource_id=vote_doc["id"],
        user_id=current_user["id"], user_name=current_user["full_name"],
        details={"motion_id": motion_id, "agm_id": agm_id, "unit_number": target_unit,
                 "vote": data.vote, "is_pre_vote": data.is_pre_vote},
        building_id=building_id
    ))

    return {"message": "Vote recorded", "unit_number": target_unit}


@router.get("/agm/{agm_id}/results")
async def get_detailed_results(
        agm_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Get detailed voting results. Only Super Admin can see individual votes."""
    if current_user["role"] != UserRole.SUPER_ADMIN:
        # Others only get aggregated results which are already in the motions
        motions = await db.agm_motions.find({"agm_id": agm_id, "building_id": building_id},
                                            {"_id": 0, "voters": 0}).to_list(50)
        return {"detailed": False, "motions": motions}

    # Super Admin gets everything
    motions = await db.agm_motions.find({"agm_id": agm_id, "building_id": building_id}, {"_id": 0}).to_list(50)
    votes = await db.agm_votes.find({"agm_id": agm_id, "building_id": building_id}, {"_id": 0}).to_list(1000)

    return {
        "detailed": True,
        "motions": motions,
        "votes": votes
    }


__all__ = ["router"]
