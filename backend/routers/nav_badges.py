"""Nav Badges — lightweight counts for sidebar notification badges."""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from database import db
from utils.auth import get_current_user, get_current_building

router = APIRouter(prefix="/nav", tags=["Navigation"])


@router.get("/badges", summary="Notification badge counts for sidebar nav items")
async def get_nav_badges(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Returns badge counts for sidebar nav items.
    Called once on page load; SSE events trigger a refresh.
    All counts exclude is_test_data: True.
    """
    user_id = current_user["id"]
    role = current_user.get("effective_role") or current_user.get("role", "owner")
    unit_number = current_user.get("unit_number") or current_user.get("lot_id")

    # Performance Optimization⚡: Parallelize all badge count queries to reduce database round-trips from 4 to 1.
    tasks = []
    task_keys = []

    # 1. Parcels waiting for this unit
    if unit_number:
        tasks.append(db.parcels.count_documents(
            {"unit_number": unit_number, "status": "received", "is_test_data": {"$ne": True}}
        ))
        task_keys.append("parcels")

    # 2. Open proposals user hasn't voted on
    if role in ("owner", "strata_admin", "ec_member", "strata_manager", "super_admin"):
        now = datetime.now(timezone.utc).isoformat()
        tasks.append(db.proposals.find(
            {"status": "open", "voting_closes_at": {"$gte": now},
             "is_test_data": {"$ne": True}},
            {"_id": 0, "votes": 1},
        ).to_list(50))
        task_keys.append("proposals")

    # 3. SLA breaches (managers only)
    if role in ("strata_manager", "super_admin", "ec_member", "strata_admin"):
        tasks.append(db.workflow_requests.count_documents(
            {"sla_breached": True, "status": {"$nin": ["closed", "auto_resolved", "completed", "cancelled"]},
             "is_test_data": {"$ne": True}}
        ))
        task_keys.append("requests")

    # 4. Unread notifications
    tasks.append(db.user_notifications.count_documents(
        {"user_id": user_id, "is_read": False}
    ))
    task_keys.append("notifications")

    results = await asyncio.gather(*tasks)

    badges: dict = {}
    for key, result in zip(task_keys, results):
        if key == "proposals":
            unvoted = sum(
                1 for p in result
                if not any(v.get("user_id") == user_id for v in p.get("votes", []))
            )
            if unvoted > 0:
                badges["proposals"] = unvoted
        elif result > 0:
            badges[key] = result

    return {"badges": badges}
