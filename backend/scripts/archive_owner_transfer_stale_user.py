"""
archive_owner_transfer_stale_user.py

One-off cleanup: archives a previous owner who was not removed during an
ownership transfer that predated the batch-archive fix (commits 0e91d9e1,
1c4d9b03).

Context:
    UA038 (building 13195) was transferred to a new owner.  The previous
    owner kikham2015@gmail.com was not archived because the transfer was
    approved before the batch-archive logic was in place.

Safe to re-run (idempotent — no-ops if already archived).

    cd backend && python3 scripts/archive_owner_transfer_stale_user.py

To preview without writing:
    cd backend && python3 scripts/archive_owner_transfer_stale_user.py --dry-run
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import AsyncMongoClient

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))
load_dotenv(backend_dir / ".env")

MONGO_URL = os.getenv("MONGO_URL")
DB_NAME = os.getenv("DB_NAME")

if not MONGO_URL or not DB_NAME:
    raise ValueError("MONGO_URL and DB_NAME must be set before running this script.")

DRY_RUN = "--dry-run" in sys.argv

STALE_EMAIL = "kikham2015@gmail.com"
BUILDING_ID = "13195"
UNIT_NUMBER = "UA038"
ARCHIVED_BY = "system_script"
ARCHIVED_REASON = "owner_transfer_complete"


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/archive_owner_transfer_stale_user.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncMongoClient(MONGO_URL)
    db = client[DB_NAME]
    now = datetime.now(timezone.utc).isoformat()

    print(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    print(f"Target: {STALE_EMAIL} / building {BUILDING_ID} / unit {UNIT_NUMBER}\n")

    # 1. Find the user record
    user = await db.users.find_one({"email": STALE_EMAIL}, {"_id": 0})
    if not user:
        print("User not found — nothing to do.")
        return

    user_id = user["id"]
    print(
        f"Found user {user_id} ({user.get('full_name', '—')}), status={user.get('status')}, is_active={user.get('is_active')}")

    if user.get("status") == "archived":
        print("User already archived — no changes needed.")
        return

    # 2. Deactivate user_units record(s) for this unit
    uu_filter = {
        "building_id": BUILDING_ID,
        "user_id": user_id,
        "unit_number": UNIT_NUMBER,
        "role_at_unit": "owner",
    }
    uu_docs = await db.user_units.find(uu_filter, {"_id": 0}).to_list(10)
    print(f"user_units records for this unit: {len(uu_docs)}")
    for d in uu_docs:
        print(f"  id={d.get('id')} is_active={d.get('is_active')} is_primary={d.get('is_primary')}")

    if not DRY_RUN:
        r = await db.user_units.update_many(
            uu_filter,
            {"$set": {
                "is_active": False,
                "is_primary": False,
                "actual_end_date": now[:10],
                "updated_at": now,
            }},
        )
        print(f"user_units deactivated: {r.modified_count}")

    # 3. Check if user still has active ownership of any OTHER unit in this building.
    # Exclude the target unit so the check is correct in both live mode (where we just
    # deactivated it) and dry-run mode (where we skipped the deactivation).
    still_active = await db.user_units.find_one({
        "building_id": BUILDING_ID,
        "user_id": user_id,
        "unit_number": {"$ne": UNIT_NUMBER},
        "role_at_unit": "owner",
        "is_active": True,
    })
    if still_active:
        print(
            f"User still has active ownership of unit {still_active.get('unit_number')} — skipping membership/global archive.")
        return

    # 4. Archive the building membership
    mem_filter = {"building_id": BUILDING_ID, "user_id": user_id}
    mem = await db.memberships.find_one(mem_filter, {"_id": 0})
    print(f"Membership: {mem}")

    if not DRY_RUN and mem and mem.get("is_active"):
        r = await db.memberships.update_many(
            mem_filter,
            {"$set": {
                "is_active": False,
                "archived_at": now,
                "archived_reason": ARCHIVED_REASON,
                "updated_at": now,
            }},
        )
        print(f"Memberships archived: {r.modified_count}")

    # 5. Check cross-building — only globally archive if no other active memberships
    other_active = await db.memberships.find_one({
        "user_id": user_id,
        "is_active": True,
        "building_id": {"$ne": BUILDING_ID},
    })
    if other_active:
        print(f"User has active membership in building {other_active.get('building_id')} — not globally archiving.")
        return

    # 6. Globally archive the user record
    print("No other active memberships — will globally archive user.")
    if not DRY_RUN:
        r = await db.users.update_one(
            {"id": user_id},
            {"$set": {
                "status": "archived",
                "is_active": False,
                "archived_at": now,
                "archived_by": ARCHIVED_BY,
                "archived_reason": ARCHIVED_REASON,
                "updated_at": now,
            }},
        )
        print(f"User globally archived: modified={r.modified_count}")

    print("\nDone." if not DRY_RUN else "\nDry run complete — no changes written.")
    client.close()


asyncio.run(main())
