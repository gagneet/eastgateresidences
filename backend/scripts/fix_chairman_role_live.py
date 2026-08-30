"""One-off fix: update users/memberships with role='chairman' to role='ec_member'.

Migration 0025 removed 'chairman' as a top-level UserRole. The audit script
(pre_migration_audit.py) found 1 live user with role='chairman'. This script
corrects the live MongoDB records without touching any financial or structural
data.

Run once, then verify with: python3 scripts/pre_migration_audit.py

Usage:
    cd backend && python3 scripts/fix_chairman_role_live.py [--dry-run]
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / ".env")

client = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]

DRY_RUN = "--dry-run" in sys.argv
NOW = datetime.now(timezone.utc)


async def fix_users() -> int:
    """Generated function header.

    Function: fix_users
    Path: backend/scripts/fix_chairman_role_live.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    docs = await db.users.find(
        {"role": "chairman"},
        {"_id": 1, "email": 1, "full_name": 1, "building_id": 1, "ec_position": 1},
    ).to_list(None)

    if not docs:
        print("  users: no chairman records found — already clean")
        return 0

    for d in docs:
        print(
            f"  users: {d.get('email')} [{d.get('building_id')}]"
            f" role='chairman' → 'ec_member'"
            f" (ec_position will be set to CHAIRMAN)"
        )
        if not DRY_RUN:
            await db.users.update_one(
                {"_id": d["_id"]},
                {"$set": {
                    "role": "ec_member",
                    "ec_position": "CHAIRMAN",
                    "updated_at": NOW,
                }},
            )
    return len(docs)


async def fix_memberships() -> int:
    """Generated function header.

    Function: fix_memberships
    Path: backend/scripts/fix_chairman_role_live.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    docs = await db.memberships.find(
        {"role": "chairman"},
        {"_id": 1, "email": 1, "building_id": 1},
    ).to_list(None)

    if not docs:
        print("  memberships: no chairman records found — already clean")
        return 0

    for d in docs:
        print(f"  memberships: {d.get('email')} [{d.get('building_id')}] role='chairman' → 'ec_member'")
        if not DRY_RUN:
            await db.memberships.update_one(
                {"_id": d["_id"]},
                {"$set": {"role": "ec_member", "updated_at": NOW}},
            )
    return len(docs)


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/fix_chairman_role_live.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    mode = "DRY RUN" if DRY_RUN else "APPLY"
    print(f"\nfix_chairman_role_live.py [{mode}]")
    print("─" * 50)

    u = await fix_users()
    m = await fix_memberships()

    total = u + m
    if DRY_RUN:
        print(f"\n  Dry run complete — {total} record(s) would be updated. Re-run without --dry-run to apply.")
    else:
        print(f"\n  Done — {total} record(s) updated.")
        if total:
            print("  Re-run pre_migration_audit.py to verify.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        client.close()
