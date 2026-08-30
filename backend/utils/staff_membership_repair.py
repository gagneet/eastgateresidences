from datetime import datetime, timezone
import uuid

from database import db
from models.user import UserRole
from models.user import UserStatus

STAFF_MEMBERSHIP_REPAIR_ROLES = {
    UserRole.STRATA_MANAGER,
    UserRole.ADMIN_STAFF,
    UserRole.REAL_ESTATE_AGENT,
    UserRole.SERVICE_PROVIDER,
}


async def repair_orphan_staff_memberships(building_id: str) -> int:
    """
    Backfill missing membership rows for pre-fix staff accounts.

    Historical staff self-registrations could create a user record without the
    membership row that powers manager/admin list views and pending counts.
    This helper safely upserts those missing memberships so existing pending
    staff become visible without a manual database repair.
    """
    if not building_id:
        return 0

    existing_user_ids = await db.memberships.distinct("user_id", {"building_id": building_id})
    staff_users = await db.users.find(
        {
            "building_id": building_id,
            "role": {"$in": list(STAFF_MEMBERSHIP_REPAIR_ROLES)},
            "status": {"$ne": "archived"},
        },
        {"_id": 0, "id": 1, "role": 1, "created_at": 1, "status": 1, "is_approved": 1},
    ).to_list(100)

    repaired = 0
    now = datetime.now(timezone.utc).isoformat()
    for user in staff_users:
        if user["id"] not in existing_user_ids:
            membership_doc = {
                "id": str(uuid.uuid4()),
                "user_id": user["id"],
                "building_id": building_id,
                "roles": [user["role"]],
                "is_active": True,
                "is_primary": True,
                "units": [],
                "created_at": user.get("created_at") or now,
            }
            result = await db.memberships.update_one(
                {"user_id": user["id"], "building_id": building_id},
                {"$setOnInsert": membership_doc},
                upsert=True,
            )
            if result.upserted_id is not None:
                repaired += 1
                existing_user_ids.append(user["id"])

        if user.get("status") == UserStatus.PENDING_OWNER_APPROVAL and not user.get("is_approved", False):
            await db.users.update_one(
                {"id": user["id"]},
                {"$set": {"status": UserStatus.ACTIVE, "updated_at": now}},
            )

    return repaired
