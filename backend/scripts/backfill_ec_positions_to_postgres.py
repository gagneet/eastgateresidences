"""Backfill EC office-bearer positions from MongoDB into Postgres identity.

The canonical identity model stores top-level committee membership as
``role='ec_member'`` and the office-bearer position as
``core.user_role_assignments.ec_position``.  Legacy committee profile data lives
in MongoDB ``ec_members.position``.  This script reconciles the two stores so
auth/current-user responses can expose the correct EC position after the
MongoDB -> Postgres identity cutover.

Usage:
    cd backend
    python3 scripts/backfill_ec_positions_to_postgres.py --building-id 13195 --dry-run
    python3 scripts/backfill_ec_positions_to_postgres.py --building-id 13195 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from db_postgres.session import async_session_context, set_tenant  # noqa: E402

_BYPASS_UUID = "00000000-0000-0000-0000-000000000000"

POSITION_MAP = {
    "chair": "CHAIRMAN",
    "chairman": "CHAIRMAN",
    "chairperson": "CHAIRMAN",
    "president": "CHAIRMAN",
    "secretary": "SECRETARY",
    "treasurer": "TREASURER",
    "member": "MEMBER",
    "committee member": "MEMBER",
    "commitee member": "MEMBER",  # legacy misspelling in East Gate data
    "ec member": "MEMBER",
}


def _normalise_position(value: str | None) -> str | None:
    """Generated function header.

    Function: _normalise_position
    Path: backend/scripts/backfill_ec_positions_to_postgres.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not value:
        return None
    raw = str(value).strip().lower()
    for needle, canonical in POSITION_MAP.items():
        if needle in raw:
            return canonical
    return None


async def _scheme_for_building(session, building_id: str) -> dict | None:
    """Generated function header.

    Function: _scheme_for_building
    Path: backend/scripts/backfill_ec_positions_to_postgres.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": _BYPASS_UUID})
    row = (await session.execute(
        text(
            """
            SELECT scheme_id::TEXT AS scheme_id, tenant_id::TEXT AS tenant_id, scheme_number, scheme_name
            FROM core.schemes
            WHERE (scheme_number = :bid OR scheme_id::TEXT = :bid)
              AND status = 'active'
              AND COALESCE(is_test_data, FALSE) = FALSE
            LIMIT 1
            """
        ),
        {"bid": str(building_id)},
    )).fetchone()
    return dict(row._mapping) if row else None


async def _update_position(session, *, scheme: dict, email: str, ec_position: str, apply: bool) -> dict:
    """Generated function header.

    Function: _update_position
    Path: backend/scripts/backfill_ec_positions_to_postgres.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await set_tenant(session, scheme["tenant_id"])
    row = (await session.execute(
        text(
            """
            SELECT u.user_id::TEXT, u.email::TEXT, u.full_name, ura.assignment_id::TEXT,
                   ura.role::TEXT, ura.ec_position
            FROM core.users u
            JOIN core.user_role_assignments ura
              ON ura.user_id = u.user_id
             AND ura.scheme_id = CAST(:scheme_id AS UUID)
             AND ura.is_active = TRUE
            WHERE u.tenant_id = CAST(:tenant_id AS UUID)
              AND LOWER(u.email::TEXT) = LOWER(:email)
            ORDER BY ura.granted_at DESC
            LIMIT 1
            """
        ),
        {"tenant_id": scheme["tenant_id"], "scheme_id": scheme["scheme_id"], "email": email},
    )).fetchone()
    if not row:
        return {"email": email, "status": "missing_pg_assignment", "target_ec_position": ec_position}

    current = dict(row._mapping)
    if current.get("role") != "ec_member":
        return {**current, "status": "not_ec_member", "target_ec_position": ec_position}
    if current.get("ec_position") == ec_position:
        return {**current, "status": "already_ok", "target_ec_position": ec_position}

    if apply:
        await session.execute(
            text(
                """
                UPDATE core.user_role_assignments
                SET ec_position = :ec_position
                WHERE assignment_id = CAST(:assignment_id AS UUID)
                """
            ),
            {"assignment_id": current["assignment_id"], "ec_position": ec_position},
        )
    return {**current, "status": "updated" if apply else "would_update", "target_ec_position": ec_position}


async def main() -> int:
    """Generated function header.

    Function: main
    Path: backend/scripts/backfill_ec_positions_to_postgres.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Backfill EC positions from Mongo ec_members to Postgres role assignments")
    parser.add_argument("--building-id", action="append", help="Building/scheme number or scheme UUID. Repeatable.")
    parser.add_argument("--apply", action="store_true", help="Apply updates. Default is dry-run.")
    parser.add_argument("--dry-run", action="store_true", help="Explicit dry-run mode.")
    args = parser.parse_args()

    building_ids = args.building_id or ["13195"]
    apply = bool(args.apply and not args.dry_run)

    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    try:
        async with async_session_context() as session:
            for building_id in building_ids:
                scheme = await _scheme_for_building(session, building_id)
                if not scheme:
                    print(f"{building_id}: no active Postgres scheme found")
                    continue
                print(f"{building_id}: {scheme['scheme_name']} ({scheme['scheme_id']}) mode={'APPLY' if apply else 'DRY-RUN'}")

                docs = await db.ec_members.find({"building_id": str(building_id)}, {"_id": 0}).to_list(500)
                if not docs:
                    print("  no Mongo ec_members records found")
                    continue
                for doc in docs:
                    email = (doc.get("email") or "").strip().lower()
                    target = _normalise_position(doc.get("position"))
                    if not email or not target:
                        print(f"  skip email={email or '<missing>'} position={doc.get('position')!r}")
                        continue
                    result = await _update_position(session, scheme=scheme, email=email, ec_position=target, apply=apply)
                    print(
                        f"  {email}: {result['status']} "
                        f"current={result.get('ec_position')} target={target} role={result.get('role')}"
                    )
    finally:
        mongo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
