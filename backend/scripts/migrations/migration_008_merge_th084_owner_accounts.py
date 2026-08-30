"""
migration_008_merge_th084_owner_accounts.py

TH084 (East Gate, building 13195) has two user accounts for the same owner
(Shouvik Aditya):

  Account A  id=f0fc49f5-b4c3-49ec-b145-06a1fe79e3ca
             email=g.ranganathan@eastgateresidences.com.au
             is_approved=False  (scraped/seed ghost — never logged in)

  Account B  id=7b43ac19-f44c-4ee4-a579-7dae0e9c481b
             email=adityashouvik@gmail.com
             is_approved=True   (owner's real self-registered account)

Currently the active user_units row points at Account A, so Account B
(the real owner) is locked out of the platform entirely.

This migration:
  1. Activates Account B's user_units row for TH084 (is_active=True, is_primary=True)
  2. Removes all user_units rows for Account A on TH084
  3. Removes the orphaned user_units row with user_id=None on TH084
  4. Deletes Account A from the users collection (ghost account:
     never approved, 0 notifications, 0 levy_payments, no linked data)

Idempotent: safe to re-run. Each step checks current state before writing.

Run:
    cd backend && python3 scripts/migrations/migration_008_merge_th084_owner_accounts.py
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

backend_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(backend_dir))
load_dotenv(backend_dir / ".env")

MONGO_URL = os.getenv("MONGO_URL")
DB_NAME = os.getenv("DB_NAME")

BUILDING_ID = "13195"
UNIT_NUMBER = "TH084"

ACCOUNT_A_ID = "f0fc49f5-b4c3-49ec-b145-06a1fe79e3ca"  # scraped ghost — DELETE
ACCOUNT_B_ID = "7b43ac19-f44c-4ee4-a579-7dae0e9c481b"  # real owner — KEEP


async def run():
    """Generated function header.

    Function: run
    Path: backend/scripts/migrations/migration_008_merge_th084_owner_accounts.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    now = datetime.now(timezone.utc).isoformat()

    print(f"Merging TH084 owner accounts for building {BUILDING_ID}")

    # ── Safety check: confirm Account A truly has no linked data ──────────────
    print("\n[1/5] Safety check — verifying Account A has no linked data ...")
    checks = [
        ("notifications", {"user_id": ACCOUNT_A_ID}),
        ("levy_payments", {"building_id": BUILDING_ID, "owner_id": ACCOUNT_A_ID}),
        ("maintenance_requests", {"building_id": BUILDING_ID, "submitted_by": ACCOUNT_A_ID}),
        ("announcements", {"building_id": BUILDING_ID, "created_by": ACCOUNT_A_ID}),
        ("documents", {"building_id": BUILDING_ID, "uploaded_by": ACCOUNT_A_ID}),
    ]
    safe_to_delete = True
    for coll_name, query in checks:
        count = await db[coll_name].count_documents(query)
        status = "OK (0)" if count == 0 else f"WARNING: {count} records found"
        print(f"  {coll_name:30s}: {status}")
        if count > 0:
            safe_to_delete = False

    if not safe_to_delete:
        print("\nERROR: Account A has linked data — aborting. Review above before proceeding.")
        client.close()
        return

    # ── Step 2: Verify Account B exists and is approved ──────────────────────
    print("\n[2/5] Verifying Account B (real owner) ...")
    user_b = await db.users.find_one({"id": ACCOUNT_B_ID},
                                     {"full_name": 1, "email": 1, "is_approved": 1, "role": 1})
    if not user_b:
        print(f"  ERROR: Account B ({ACCOUNT_B_ID}) not found — aborting.")
        client.close()
        return
    print(f"  Found: full_name={user_b.get('full_name')!r}  email={user_b.get('email')!r}"
          f"  approved={user_b.get('is_approved')}  role={user_b.get('role')!r}")
    if not user_b.get("is_approved"):
        print("  WARNING: Account B is not approved — proceeding anyway, but verify this is correct.")

    # ── Step 3: Remove Account A's rows and orphaned rows FIRST ─────────────
    # Must happen before activating Account B due to unique index:
    # unique_active_primary_owner_per_unit on (building_id, unit_number, role_at_unit)
    print("\n[3/5] Removing Account A's user_units rows and orphaned rows for TH084 ...")
    res_a = await db.user_units.delete_many(
        {"building_id": BUILDING_ID, "unit_number": UNIT_NUMBER, "user_id": ACCOUNT_A_ID})
    print(f"  Deleted {res_a.deleted_count} user_units row(s) for Account A")

    res_none = await db.user_units.delete_many(
        {"building_id": BUILDING_ID, "unit_number": UNIT_NUMBER, "user_id": None})
    print(f"  Deleted {res_none.deleted_count} orphaned user_units row(s) with user_id=None")

    # ── Step 4: Activate Account B's user_units row ───────────────────────────
    print("\n[4/5] Activating Account B's user_units row for TH084 ...")
    existing_b = await db.user_units.find_one(
        {"building_id": BUILDING_ID, "unit_number": UNIT_NUMBER, "user_id": ACCOUNT_B_ID})
    if existing_b:
        if existing_b.get("is_active") and existing_b.get("is_primary"):
            print("  Already active and primary — skipped")
        else:
            res = await db.user_units.update_one(
                {"building_id": BUILDING_ID, "unit_number": UNIT_NUMBER, "user_id": ACCOUNT_B_ID},
                {"$set": {"is_active": True, "is_primary": True,
                          "role_at_unit": "owner", "updated_at": now}},
            )
            print(f"  Updated {res.modified_count} row: is_active=True, is_primary=True")
    else:
        # No existing row — create one
        await db.user_units.insert_one({
            "building_id": BUILDING_ID,
            "unit_number": UNIT_NUMBER,
            "user_id": ACCOUNT_B_ID,
            "role_at_unit": "owner",
            "is_active": True,
            "is_primary": True,
            "created_at": now,
            "updated_at": now,
        })
        print("  Inserted new active user_units row for Account B")

    # ── Step 5: Delete Account A from users ───────────────────────────────────
    print("\n[5/5] Deleting Account A (ghost scraped account) from users ...")
    user_a = await db.users.find_one({"id": ACCOUNT_A_ID}, {"full_name": 1, "email": 1, "is_approved": 1})
    if not user_a:
        print("  Account A already deleted — skipped")
    elif user_a.get("is_approved"):
        print(f"  ABORT: Account A is_approved=True — refusing to delete an approved account.")
        print(f"  Manual review required for id={ACCOUNT_A_ID}")
    else:
        res_del = await db.users.delete_one({"id": ACCOUNT_A_ID})
        print(f"  Deleted Account A: full_name={user_a.get('full_name')!r}  "
              f"email={user_a.get('email')!r}  ({res_del.deleted_count} doc)")

    # ── Final verification ────────────────────────────────────────────────────
    print("\n[Verify] Final user_units state for TH084:")
    rows = await db.user_units.find(
        {"building_id": BUILDING_ID, "unit_number": UNIT_NUMBER}).to_list(None)
    for r in rows:
        uid = r.get("user_id")
        u = await db.users.find_one({"id": uid}, {"full_name": 1, "email": 1}) if uid else None
        fname = u.get("full_name") if u else "NOT FOUND"
        email = u.get("email") if u else "N/A"
        print(f"  user_id={uid}  role={r.get('role_at_unit')!r}  "
              f"active={r.get('is_active')}  primary={r.get('is_primary')}  "
              f"name={fname!r}  email={email!r}")

    print("\nMigration complete.")
    client.close()


if __name__ == "__main__":
    asyncio.run(run())
