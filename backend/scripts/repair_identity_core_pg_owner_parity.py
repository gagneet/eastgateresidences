#!/usr/bin/env python3
# @featuretrace:postgres-identity-foundation — Repair PG owner presentation/linkage parity from canonical Mongo identity.
# Layer: script
# Data flow: canonical Mongo owner_service + core.lots/ownership_periods/users -> targeted PG identity repair.
# Related: backend/services/owner_service.py
#          backend/services/owner_read_service.py
#          backend/scripts/identity_core_pg_write_preflight.py
"""Repair identity_core owner parity for a single building.

Default mode is dry-run. --apply performs narrowly-scoped updates:
1. core.parties legal_name/primary_email for current owner rows, using the
   canonical Mongo owner presentation for the corresponding unit slot.
2. core.users.party_id where an active PG user_unit gives exactly one
   unambiguous current owner party for that user.

No Mongo writes. No East Gate hardcoding beyond the caller-provided building id.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "backend" / ".env")
sys.path.insert(0, str(ROOT / "backend"))

from request_context import get_ctx_building_id, set_ctx_building_id  # noqa: E402
from services import owner_service  # noqa: E402
from services.identity_bootstrap_service import _stable_uuid  # noqa: E402

DATABASE_URL = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
BYPASS = "00000000-0000-0000-0000-000000000000"


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


async def _scheme_context(conn: asyncpg.Connection, building_id: str) -> dict[str, Any]:
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)
    bare = building_id[2:] if building_id.upper().startswith("UP") else building_id
    candidates = sorted({building_id, bare, f"UP{bare}"})
    row = await conn.fetchrow(
        """
        SELECT scheme_id::text AS scheme_id, tenant_id::text AS tenant_id, scheme_number
        FROM core.schemes
        WHERE scheme_number = ANY($1::text[])
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        candidates,
    )
    if not row:
        raise RuntimeError(f"No core.schemes row found for building_id={building_id!r}")
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", row["tenant_id"])
    return dict(row)


async def _current_owner_rows(conn: asyncpg.Connection, scheme_id: str) -> dict[str, list[dict[str, Any]]]:
    rows = await conn.fetch(
        """
        SELECT
          l.unit_number,
          l.lot_number,
          op.ownership_period_id::text AS ownership_period_id,
          op.owner_party_id::text AS party_id,
          p.legal_name,
          p.primary_email,
          op.is_primary_owner,
          COALESCE(op.ownership_share, 1.0) AS ownership_share
        FROM core.ownership_periods op
        JOIN core.lots l ON l.lot_id = op.lot_id
        JOIN core.parties p ON p.party_id = op.owner_party_id
        WHERE op.scheme_id = $1::uuid
          AND op.recorded_to IS NULL
          AND op.valid_from <= CURRENT_DATE
          AND (op.valid_to IS NULL OR op.valid_to > CURRENT_DATE)
        ORDER BY COALESCE(l.unit_number, l.lot_number),
                 op.is_primary_owner DESC,
                 op.ownership_share DESC,
                 lower(COALESCE(p.primary_email, p.legal_name))
        """,
        scheme_id,
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        unit = str(row["unit_number"] or row["lot_number"])
        grouped.setdefault(unit, []).append(dict(row))
    return grouped


async def _desired_owner_slots(building_id: str) -> dict[str, list[dict[str, str | None]]]:
    previous = get_ctx_building_id()
    set_ctx_building_id(building_id)
    try:
        all_owners = await owner_service._mongo_get_all_unit_owners(building_id)
    finally:
        set_ctx_building_id(previous)

    desired: dict[str, list[dict[str, str | None]]] = {}
    for unit, payload in all_owners.items():
        slots = [
            {
                "name": _clean(payload.get("owner_name")),
                "email": _clean(payload.get("owner_email")),
            }
        ]
        secondary_name = _clean(payload.get("owner_name_b") or payload.get("co_owner_name"))
        secondary_email = _clean(payload.get("owner_email_b") or payload.get("co_owner_email"))
        if secondary_name or secondary_email:
            slots.append({"name": secondary_name, "email": secondary_email})
        desired[str(unit)] = slots
    return desired


async def _party_repairs(
    conn: asyncpg.Connection,
    building_id: str,
    scheme: dict[str, Any],
    apply: bool,
) -> dict[str, Any]:
    scheme_id = scheme["scheme_id"]
    tenant_id = scheme["tenant_id"]
    current = await _current_owner_rows(conn, scheme_id)
    desired = await _desired_owner_slots(building_id)
    updates: list[dict[str, Any]] = []
    additions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for unit, desired_slots in desired.items():
        pg_rows = current.get(unit) or []
        if not pg_rows:
            skipped.append({"unit": unit, "reason": "no_current_pg_owner_rows"})
            continue
        if len(desired_slots) > len(pg_rows):
            if len(pg_rows) == 1 and len(desired_slots) == 2 and desired_slots[1]["name"]:
                secondary = desired_slots[1]
                party_id = _stable_uuid(
                    "party",
                    tenant_id,
                    f"identity-repair:{building_id}:{unit}:secondary:{secondary['name']}:{secondary['email'] or ''}",
                )
                ownership_period_id = _stable_uuid(
                    "ownership-period",
                    scheme_id,
                    pg_rows[0]["ownership_period_id"],
                    party_id,
                    "identity-repair-secondary",
                )
                additions.append(
                    {
                        "unit": unit,
                        "lot_number": pg_rows[0]["lot_number"],
                        "party_id": party_id,
                        "ownership_period_id": ownership_period_id,
                        "lot_owner_period_id": pg_rows[0]["ownership_period_id"],
                        "set_legal_name": secondary["name"],
                        "set_primary_email": secondary["email"],
                    }
                )
            else:
                skipped.append({"unit": unit, "reason": "mongo_has_more_owner_slots_than_pg"})
                continue
        for idx, slot in enumerate(desired_slots[:len(pg_rows)]):
            row = pg_rows[idx]
            desired_name = slot["name"]
            desired_email = slot["email"]
            if not desired_name:
                continue
            if row.get("legal_name") == desired_name and (row.get("primary_email") or None) == desired_email:
                continue
            updates.append(
                {
                    "unit": unit,
                    "slot": idx + 1,
                    "party_id": row["party_id"],
                    "set_legal_name": desired_name,
                    "set_primary_email": desired_email,
                }
            )

    if apply:
        for update in updates:
            await conn.execute(
                """
                UPDATE core.parties
                SET legal_name = $2::text,
                    primary_email = $3::text,
                    metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
                      'identity_core_repair', jsonb_build_object(
                        'source', 'mongo_owner_service',
                        'building_id', $4::text,
                        'unit', $5::text,
                        'repaired_at', $6::text
                      )
                    ),
                    updated_at = NOW()
                WHERE party_id = $1::uuid
                """,
                update["party_id"],
                update["set_legal_name"],
                update["set_primary_email"],
                building_id,
                update["unit"],
                datetime.now(UTC).isoformat(),
            )
        for addition in additions:
            await conn.execute(
                """
                INSERT INTO core.parties (
                    party_id, tenant_id, party_type, legal_name, primary_email,
                    metadata, status, source_system, source_collection, source_record_id,
                    source_external_id, imported_at
                ) VALUES (
                    $1::uuid, $2::uuid, 'individual', $3::text, $4::text,
                    jsonb_build_object(
                      'identity_core_repair', jsonb_build_object(
                        'source', 'mongo_owner_service',
                        'building_id', $5::text,
                        'unit', $6::text,
                        'repaired_at', $7::text
                      )
                    ),
                    'active', 'mongo', 'units', $6::text, $8::text, NOW()
                )
                ON CONFLICT (party_id) DO UPDATE SET
                    legal_name = EXCLUDED.legal_name,
                    primary_email = EXCLUDED.primary_email,
                    metadata = core.parties.metadata || EXCLUDED.metadata,
                    updated_at = NOW()
                """,
                addition["party_id"],
                tenant_id,
                addition["set_legal_name"],
                addition["set_primary_email"],
                building_id,
                addition["unit"],
                datetime.now(UTC).isoformat(),
                f"identity-repair:{building_id}:{addition['unit']}:secondary",
            )
            await conn.execute(
                """
                UPDATE core.ownership_periods
                SET ownership_share = 0.5,
                    is_primary_owner = TRUE
                WHERE ownership_period_id = $1::uuid
                """,
                addition["lot_owner_period_id"],
            )
            await conn.execute(
                """
                INSERT INTO core.ownership_periods (
                    ownership_period_id, tenant_id, scheme_id, lot_id, owner_party_id,
                    valid_from, valid_to, ownership_share, is_primary_owner,
                    source_system, source_collection, source_record_id, notes, imported_at
                )
                SELECT
                    $1::uuid, tenant_id, scheme_id, lot_id, $2::uuid,
                    valid_from, valid_to, 0.5, FALSE,
                    'mongo', 'units', $3, 'identity_core owner parity repair', NOW()
                FROM core.ownership_periods
                WHERE ownership_period_id = $4::uuid
                ON CONFLICT DO NOTHING
                """,
                addition["ownership_period_id"],
                addition["party_id"],
                addition["unit"],
                addition["lot_owner_period_id"],
            )

    return {
        "party_updates": len(updates),
        "secondary_owner_additions": len(additions),
        "party_update_units": sorted({u["unit"] for u in updates}),
        "secondary_owner_addition_units": sorted({u["unit"] for u in additions}),
        "skipped": skipped[:30],
        "applied": apply,
    }


async def _user_party_repairs(conn: asyncpg.Connection, scheme_id: str, apply: bool) -> dict[str, Any]:
    candidates = await conn.fetch(
        """
        WITH current_owner_user_units AS (
          SELECT DISTINCT uu.user_id, uu.party_id
          FROM core.user_units uu
          JOIN core.ownership_periods op
            ON op.scheme_id = uu.scheme_id
           AND op.lot_id = uu.lot_id
           AND op.owner_party_id = uu.party_id
           AND op.recorded_to IS NULL
           AND op.valid_from <= CURRENT_DATE
           AND (op.valid_to IS NULL OR op.valid_to > CURRENT_DATE)
          WHERE uu.scheme_id = $1::uuid
            AND uu.valid_to IS NULL
            AND uu.party_id IS NOT NULL
            AND uu.relationship = 'owner'
        ),
        unambiguous AS (
          SELECT user_id, MIN(party_id::text) AS party_id, COUNT(DISTINCT party_id) AS party_count
          FROM current_owner_user_units
          GROUP BY user_id
        )
        SELECT u.user_id::text AS user_id, u.party_id::text AS existing_party_id, ua.party_id
        FROM unambiguous ua
        JOIN core.users u ON u.user_id = ua.user_id
        WHERE ua.party_count = 1
          AND u.is_active = TRUE
          AND u.party_id IS DISTINCT FROM ua.party_id::uuid
        ORDER BY u.user_id::text
        """,
        scheme_id,
    )
    updates = [dict(row) for row in candidates]
    if apply:
        for update in updates:
            await conn.execute(
                """
                UPDATE core.users
                SET party_id = $2::uuid,
                    updated_at = NOW()
                WHERE user_id = $1::uuid
                """,
                update["user_id"],
                update["party_id"],
            )
    return {
        "user_party_updates": len(updates),
        "applied": apply,
    }


async def run(building_id: str, apply: bool) -> dict[str, Any]:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        scheme = await _scheme_context(conn, building_id)
        async with conn.transaction():
            party_result = await _party_repairs(conn, building_id, scheme, apply)
            user_result = await _user_party_repairs(conn, scheme["scheme_id"], apply)
            if not apply:
                raise RuntimeError("__dry_run_rollback__")
    except RuntimeError as exc:
        if str(exc) != "__dry_run_rollback__":
            raise
    finally:
        await conn.close()

    # Re-open for a clean dry-run report after the rollback path.
    if not apply:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            scheme = await _scheme_context(conn, building_id)
            party_result = await _party_repairs(conn, building_id, scheme, False)
            user_result = await _user_party_repairs(conn, scheme["scheme_id"], False)
        finally:
            await conn.close()

    return {
        "building_id": building_id,
        "mode": "apply" if apply else "dry_run",
        "party_repair": party_result,
        "user_party_repair": user_result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair PG owner parity from canonical Mongo identity.")
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.building_id, args.apply)), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
