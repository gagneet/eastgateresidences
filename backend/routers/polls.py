"""Quick Polls — non-binding micro-governance polls."""

import asyncio
import html
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from database import db
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from utils.auth import get_approved_user, get_current_building
from utils.helpers import create_audit_log

router = APIRouter(prefix="/polls", tags=["Polls"])


class PollCreate(BaseModel):
    question: str = Field(..., max_length=500)
    options: List[str] = Field(..., min_length=2, max_length=10)
    closes_at: Optional[str] = None
    is_anonymous: bool = False


@router.post("", status_code=201)
async def create_poll(
        body: PollCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """
    Create a new quick poll. Admin/EC only.
    Sentinel 🛡️: Uses get_approved_user to prevent unvetted access.
    Sentinel 🛡️: Uses db.quick_polls for automatic tenant isolation.
    """
    role = current_user.get("effective_role") or current_user.get("role")
    if role not in {
        "ec_member",
        "strata_admin",
        "strata_manager",
        "super_admin",
    }:
        raise HTTPException(status_code=403, detail="EC member or manager required")
    poll_id = str(uuid.uuid4())
    safe_question = html.escape(body.question)
    poll = {
        "id": poll_id,
        "building_id": building_id,
        "question": safe_question,
        "options": [
            {"id": str(uuid.uuid4()), "text": html.escape(opt), "votes": 0}
            for opt in body.options
        ],
        "closes_at": body.closes_at,
        "is_anonymous": body.is_anonymous,
        "status": "open",
        "created_by": current_user["id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_votes": 0,
        "voted_user_ids": [],
        "is_test_data": False,
    }
    await db.quick_polls.insert_one(poll)

    user_name = current_user.get("full_name") or current_user.get("email", "Unknown")
    asyncio.create_task(
        create_audit_log(
            action="created",
            resource_type="poll",
            resource_id=poll_id,
            user_id=current_user["id"],
            user_name=user_name,
            details={"question": safe_question},
            building_id=building_id,
        )
    )

    if "_id" in poll:
        del poll["_id"]
    # Exclude internal voter list from response
    poll.pop("voted_user_ids", None)
    return poll


@router.get("")
async def list_polls(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """
    List quick polls for the current building.
    Sentinel 🛡️: Uses get_approved_user to prevent unvetted access.
    Sentinel 🛡️: Explicit building_id filter as defense-in-depth.
    """
    # Exclude voted_user_ids to prevent privacy leak; inject has_voted per caller instead.
    projection = {"_id": 0, "voted_user_ids": 0}
    raw_polls = (
        await db.quick_polls.find(
            {"building_id": building_id, "is_test_data": {"$ne": True}},
            projection,
        )
        .sort("created_at", -1)
        .limit(20)
        .to_list(20)
    )
    # Re-fetch only whether the current user has voted (avoids exposing voter list).
    user_id = current_user["id"]
    poll_ids = [p["id"] for p in raw_polls]
    voted_cursor = db.quick_polls.find(
        {"building_id": building_id, "id": {"$in": poll_ids}, "voted_user_ids": user_id},
        {"_id": 0, "id": 1},
    )
    voted_ids = {p["id"] async for p in voted_cursor}
    for p in raw_polls:
        p["has_voted"] = p["id"] in voted_ids
    return raw_polls


@router.post("/{poll_id}/vote")
async def cast_vote(
        poll_id: str,
        option_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """
    Cast a vote in a quick poll.
    Sentinel 🛡️: Uses get_approved_user to prevent unvetted access.
    Sentinel 🛡️: Explicit building_id filter as defense-in-depth.
    Sentinel 🛡️: Atomic update prevents concurrent double-vote race condition.
    """
    poll = await db.quick_polls.find_one({"id": poll_id, "building_id": building_id})
    if not poll:
        raise HTTPException(status_code=404, detail="Poll not found")
    if poll.get("status") != "open":
        raise HTTPException(status_code=400, detail="Poll is closed")
    if current_user["id"] in poll.get("voted_user_ids", []):
        raise HTTPException(status_code=409, detail="Already voted")

    # Atomic update: status + dedup guard in the filter prevents race conditions.
    result = await db.quick_polls.update_one(
        {
            "id": poll_id,
            "building_id": building_id,
            "options.id": option_id,
            "status": "open",
            "voted_user_ids": {"$ne": current_user["id"]},
        },
        {
            "$inc": {"options.$.votes": 1, "total_votes": 1},
            "$addToSet": {"voted_user_ids": current_user["id"]},
        },
    )

    if result.modified_count == 0:
        # option_id was invalid or a concurrent vote was recorded between the read and update.
        raise HTTPException(status_code=400, detail="Invalid option or vote already recorded")

    user_name = current_user.get("full_name") or current_user.get("email", "Unknown")
    asyncio.create_task(
        create_audit_log(
            action="voted",
            resource_type="poll",
            resource_id=poll_id,
            user_id=current_user["id"],
            user_name=user_name,
            details={"option_id": option_id},
            building_id=building_id,
        )
    )

    return {"voted": True, "option_id": option_id}
