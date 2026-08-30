#!/usr/bin/env python3
"""
Data repair: populate full_name from first_name + last_name where blank.

@featuretrace:user-management
Layer: migration
Data flow: db.users (global) — read first_name + last_name, write full_name
Related: backend/utils/permissions.py (user_to_response display-name rule)
         frontend/src/pages/dashboard/UsersPage.jsx

WHY THIS SCRIPT EXISTS
-----------------------
The canonical display-name field for a user is `full_name`.
user_to_response() in utils/permissions.py now falls back to
`first_name + last_name` at read time, but the database should
always carry the correct value.

Some legacy admin scripts (and the previous session's DB patch)
wrote `first_name`/`last_name` without also setting `full_name`.
This one-time repair script finds all such records and fixes them.

SAFE TO RE-RUN: idempotent — only touches records where full_name is
blank AND first_name/last_name are non-empty.

Usage:
    cd backend
    python3 scripts/data_repair/repair_user_full_names.py [--dry-run]
"""
from __future__ import annotations

import asyncio
import sys
import os
from pathlib import Path

# Allow running from the backend directory
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from motor.motor_asyncio import AsyncIOMotorClient

DRY_RUN = "--dry-run" in sys.argv


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/data_repair/repair_user_full_names.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "strataos_production")
    if not mongo_url:
        print("ERROR: MONGO_URL not set in environment / .env", file=sys.stderr)
        sys.exit(1)

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    # Find all users where full_name is blank/missing
    cursor = db.users.find(
        {"$or": [
            {"full_name": ""},
            {"full_name": None},
            {"full_name": {"$exists": False}},
        ]},
        {"_id": 1, "id": 1, "email": 1, "full_name": 1, "first_name": 1, "last_name": 1},
    )
    all_blank = await cursor.to_list(None)

    fixable = [
        u for u in all_blank
        if (u.get("first_name") or "").strip() or (u.get("last_name") or "").strip()
    ]
    unfixable = [
        u for u in all_blank
        if not ((u.get("first_name") or "").strip() or (u.get("last_name") or "").strip())
    ]

    print(f"Users with blank full_name:       {len(all_blank)}")
    print(f"  Fixable (have first/last name): {len(fixable)}")
    print(f"  Cannot fix (no name at all):    {len(unfixable)}")
    print()

    if not fixable:
        print("Nothing to repair. ✓")
        return

    mode = "[DRY RUN] " if DRY_RUN else ""
    updated = 0
    for u in fixable:
        first = (u.get("first_name") or "").strip()
        last = (u.get("last_name") or "").strip()
        computed = f"{first} {last}".strip()
        print(f"  {mode}{u.get('email', u.get('id'))}: full_name ← {repr(computed)}")
        if not DRY_RUN:
            await db.users.update_one(
                {"_id": u["_id"]},
                {"$set": {"full_name": computed}},
            )
            updated += 1

    print()
    if DRY_RUN:
        print(f"Dry run complete — {len(fixable)} record(s) would be updated.")
        print("Re-run without --dry-run to apply.")
    else:
        print(f"Repair complete — {updated} record(s) updated. ✓")

    if unfixable:
        print()
        print(f"WARNING: {len(unfixable)} user(s) have no name data at all:")
        for u in unfixable:
            print(f"  {u.get('email', u.get('id'))}")
        print("These require manual intervention (contact the user to supply their name).")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
