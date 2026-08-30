"""
Migration 016 — Rename 'chairman' role to 'strata_admin'

The 'chairman' role has been superseded by 'strata_admin', which carries
identical permissions.  'Chairman' is now only an ec_position value held by
EC Members (ec_member role + ec_position='CHAIRMAN').

What this migration does:
  1. Finds every user with role='chairman'.
  2. Sets role='strata_admin' and ec_position='CHAIRMAN' (unless ec_position
     is already set to something else by a previous admin action).
  3. Writes an audit log entry for each changed user.

Safe to run multiple times (idempotent — skips users already on building_admin).

Run:
  cd backend && python3 scripts/migrations/migration_016_chairman_to_building_admin.py

To do a dry-run (no writes):
  DRY_RUN=1 python3 scripts/migrations/migration_016_chairman_to_building_admin.py
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

import motor.motor_asyncio

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DRY_RUN = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")

MIGRATION_ID = "migration_016_chairman_to_building_admin"


async def run():
    """Generated function header.

    Function: run
    Path: backend/scripts/migrations/migration_016_chairman_to_building_admin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = motor.motor_asyncio.AsyncIOMotorClient(os.getenv("MONGO_URL"))
    db = client[os.getenv("DB_NAME", "strata")]

    now = datetime.now(timezone.utc).isoformat()

    # Find all users still carrying the legacy 'chairman' role
    chairman_users = await db.users.find({"role": "chairman"}).to_list(500)
    logger.info(
        f"Found {len(chairman_users)} user(s) with role='chairman' — DRY_RUN={DRY_RUN}"
    )

    updated = 0
    for user in chairman_users:
        uid = user.get("id") or str(user["_id"])
        email = user.get("email", uid)
        building_id = user.get("building_id", "unknown")
        existing_pos = user.get("ec_position")

        # Preserve an already-set ec_position; default to CHAIRMAN for former chairmen
        new_ec_position = existing_pos if existing_pos else "CHAIRMAN"

        logger.info(
            f"  {'[DRY]' if DRY_RUN else '[UPDATE]'} {email} "
            f"(building={building_id}) chairman → building_admin  ec_position={new_ec_position}"
        )

        if not DRY_RUN:
            await db.users.update_one(
                {"_id": user["_id"]},
                {
                    "$set": {
                        "role": "strata_admin",
                        "ec_position": new_ec_position,
                        "migrated_at": now,
                        "migrated_by": MIGRATION_ID,
                    }
                },
            )
            await db.audit_logs.insert_one(
                {
                    "action": "role_migration",
                    "migration": MIGRATION_ID,
                    "user_id": uid,
                    "email": email,
                    "building_id": building_id,
                    "from_role": "chairman",
                    "to_role": "strata_admin",
                    "ec_position_set": new_ec_position,
                    "created_at": now,
                }
            )
            updated += 1

    logger.info(
        f"Migration complete — {updated} user(s) updated"
        if not DRY_RUN
        else f"Dry-run complete — {len(chairman_users)} user(s) would be updated"
    )
    client.close()


if __name__ == "__main__":
    asyncio.run(run())
