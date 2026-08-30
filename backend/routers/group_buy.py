"""Community Group Buy — scope: 'building' (portfolio scope reserved for future)."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from database import db
from utils.auth import get_approved_user, get_current_building

router = APIRouter(prefix="/group-buy", tags=["Group Buy"])


class GroupBuyCreate(BaseModel):
    title: str = Field(..., max_length=300)
    description: str = Field(..., max_length=2000)
    product_url: Optional[str] = None
    minimum_participants: int = Field(2, ge=2, le=100)
    closes_at: Optional[str] = None
    scope: str = Field("building", pattern="^(building|portfolio)$")


@router.post("", status_code=201)
async def create_group_buy(
        body: GroupBuyCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: create_group_buy
    Path: backend/routers/group_buy.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    gb = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "title": body.title,
        "description": body.description,
        "product_url": body.product_url,
        "minimum_participants": body.minimum_participants,
        "closes_at": body.closes_at,
        "scope": body.scope,
        "status": "open",
        "participants": [],
        "created_by": current_user["id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "is_test_data": False,
    }
    await db.group_buying_campaigns.insert_one(gb)
    del gb["_id"]
    return gb


@router.get("")
async def list_group_buys(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: list_group_buys
    Path: backend/routers/group_buy.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    items = await db.group_buying_campaigns.find(
        {"building_id": building_id, "status": "open", "is_test_data": {"$ne": True}}, {"_id": 0}
    ).sort("created_at", -1).limit(20).to_list(20)
    return items


@router.post("/{gb_id}/join")
async def join_group_buy(
        gb_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: join_group_buy
    Path: backend/routers/group_buy.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    gb = await db.group_buying_campaigns.find_one({"id": gb_id, "building_id": building_id})
    if not gb:
        raise HTTPException(status_code=404, detail="Group buy not found")
    if gb.get("status") != "open":
        raise HTTPException(status_code=400, detail="Group buy is closed")
    participants = gb.get("participants", [])
    if current_user["id"] in [p.get("user_id") for p in participants]:
        raise HTTPException(status_code=409, detail="Already joined")
    await db.group_buying_campaigns.update_one(
        {"id": gb_id},
        {"$push": {"participants": {
            "user_id": current_user["id"],
            "joined_at": datetime.now(timezone.utc).isoformat(),
        }}}
    )
    return {"joined": True}
