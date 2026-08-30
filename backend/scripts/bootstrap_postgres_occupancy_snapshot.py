#!/usr/bin/env python3
"""Bootstrap East Gate occupancy into PostgreSQL analytics and cutover readiness.

This is an interim occupancy bridge. The product occupancy router is still
Mongo-backed, but this script moves the current explicit lot occupancy
classification into analytics.fact_occupancy_snapshot and records the
occupancy domain as ready for shadow mode. It does not promote the domain.

Source: Mongo units.occupancy_type / owner_occupied / tenanted / is_vacant
Target: analytics.fact_occupancy_snapshot

Usage:
    backend/venv/bin/python3 backend/scripts/bootstrap_postgres_occupancy_snapshot.py --building-id 13195 --dry-run
    backend/venv/bin/python3 backend/scripts/bootstrap_postgres_occupancy_snapshot.py --building-id 13195 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import asyncpg
import motor.motor_asyncio
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "backend" / ".env")

DATABASE_URL = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ.get("DB_NAME", "strataos_production")
BYPASS_TENANT_ID = "00000000-0000-0000-0000-000000000000"


def _classify(unit: dict) -> tuple[str, bool, str, float]:
    """Return occupancy_type, has_active_tenancy, source_field, confidence."""
    if unit.get("is_vacant"):
        return "vacant", False, "is_vacant", 1.0
    if unit.get("tenanted"):
        return "tenant", True, "tenanted", 1.0
    raw = str(unit.get("occupancy_type") or "").strip().lower()
    if raw in {"owner_occupied", "owner-occupied", "owner occupied"}:
        return "owner_occupied", False, "occupancy_type", 1.0
    if raw in {"rented", "tenant", "tenanted"}:
        return "tenant", True, "occupancy_type", 1.0
    if raw in {"investor", "investor_unknown"}:
        return "investor_unknown", False, "occupancy_type", 0.75
    if unit.get("owner_occupied"):
        return "owner_occupied", False, "owner_occupied", 1.0
    return "unknown", False, "fallback", 0.25


def _datetime_or_none(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


async def _resolve_scheme(conn: asyncpg.Connection, building_id: str) -> tuple[str, str]:
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS_TENANT_ID)
    row = await conn.fetchrow(
        """
        SELECT tenant_id::text, scheme_id::text
        FROM core.schemes
        WHERE scheme_number = $1
        LIMIT 1
        """,
        building_id,
    )
    if not row:
        raise RuntimeError(f"No core.schemes row found for building_id={building_id!r}")
    return row["tenant_id"], row["scheme_id"]


async def _current_cutover_mode(conn: asyncpg.Connection, building_id: str, domain: str) -> tuple[str, str]:
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS_TENANT_ID)
    row = await conn.fetchrow(
        """
        SELECT mode, readiness_status
        FROM core.domain_cutover_status
        WHERE building_id = $1
          AND domain = $2
          AND is_test_data = false
        LIMIT 1
        """,
        building_id,
        domain,
    )
    if not row:
        return "mongo_primary", "not_started"
    return row["mode"], row["readiness_status"]


async def run(*, building_id: str, dry_run: bool) -> None:
    mongo = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URL)
    db = mongo[DB_NAME]
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        tenant_id, scheme_id = await _resolve_scheme(conn, building_id)
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant_id)

        units = await db.units.find(
            {"building_id": building_id},
            {
                "_id": 1,
                "unit_number": 1,
                "lot_number": 1,
                "occupancy_type": 1,
                "owner_occupied": 1,
                "tenanted": 1,
                "is_vacant": 1,
                "is_test_data": 1,
                "updated_at": 1,
            },
        ).to_list(length=None)

        snapshot_date = date.today()
        rows: list[dict] = []
        counts: dict[str, int] = {}
        for unit in units:
            lot_number = str(unit.get("lot_number") or unit.get("unit_number") or "").strip()
            if not lot_number:
                continue
            occupancy_type, has_active_tenancy, source_field, confidence = _classify(unit)
            counts[occupancy_type] = counts.get(occupancy_type, 0) + 1
            rows.append(
                {
                    "lot_number": lot_number,
                    "occupancy_type": occupancy_type,
                    "has_active_tenancy": has_active_tenancy,
                    "is_test_data": bool(unit.get("is_test_data", False)),
                    "source_id": str(unit.get("_id")),
                    "source_updated_at": _datetime_or_none(unit.get("updated_at")),
                    "source_field": source_field,
                    "confidence": confidence,
                }
            )

        existing = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM analytics.fact_occupancy_snapshot
            WHERE scheme_id = $1::uuid
              AND snapshot_date = $2::date
            """,
            scheme_id,
            snapshot_date,
        )

        print(
            json.dumps(
                {
                    "building_id": building_id,
                    "tenant_id": tenant_id,
                    "scheme_id": scheme_id,
                    "snapshot_date": snapshot_date.isoformat(),
                    "source_units": len(units),
                    "prepared_rows": len(rows),
                    "existing_snapshot_rows": existing,
                    "counts": counts,
                    "dry_run": dry_run,
                },
                indent=2,
                sort_keys=True,
            )
        )

        if dry_run:
            return

        async with conn.transaction():
            await conn.execute(
                """
                UPDATE analytics.fact_occupancy_snapshot
                SET is_current = false
                WHERE scheme_id = $1::uuid
                  AND snapshot_date <> $2::date
                  AND is_current = true
                """,
                scheme_id,
                snapshot_date,
            )

            inserted = updated = 0
            for row in rows:
                result = await conn.execute(
                    """
                    UPDATE analytics.fact_occupancy_snapshot
                    SET occupancy_type = $4,
                        has_active_tenancy = $5,
                        is_test_data = $6,
                        source_system = 'postgres_bootstrap',
                        source_collection = 'units',
                        source_table = 'core.lots',
                        source_id = $7,
                        source_updated_at = $8,
                        ingested_at = now(),
                        confidence = $9,
                        is_current = true
                    WHERE scheme_id = $2::uuid
                      AND lot_number = $3
                      AND snapshot_date = $1::date
                    """,
                    snapshot_date,
                    scheme_id,
                    row["lot_number"],
                    row["occupancy_type"],
                    row["has_active_tenancy"],
                    row["is_test_data"],
                    row["source_id"],
                    row["source_updated_at"],
                    row["confidence"],
                )
                if result.endswith("0"):
                    await conn.execute(
                        """
                        INSERT INTO analytics.fact_occupancy_snapshot (
                            tenant_id, scheme_id, snapshot_date, lot_number,
                            occupancy_type, has_active_tenancy, is_test_data,
                            source_system, source_collection, source_table,
                            source_id, source_updated_at, ingested_at,
                            confidence, is_current
                        ) VALUES (
                            $1::uuid, $2::uuid, $3::date, $4,
                            $5, $6, $7,
                            'postgres_bootstrap', 'units', 'core.lots',
                            $8, $9, now(),
                            $10, true
                        )
                        """,
                        tenant_id,
                        scheme_id,
                        snapshot_date,
                        row["lot_number"],
                        row["occupancy_type"],
                        row["has_active_tenancy"],
                        row["is_test_data"],
                        row["source_id"],
                        row["source_updated_at"],
                        row["confidence"],
                    )
                    inserted += 1
                else:
                    updated += 1

            current_mode, current_readiness = await _current_cutover_mode(conn, building_id, "occupancy")
            readiness_update_applied = current_mode == "mongo_primary"
            summary = {
                "status": "pass",
                "source": "Mongo units explicit occupancy fields as base classification",
                "target": "analytics.fact_occupancy_snapshot",
                "snapshot_date": snapshot_date.isoformat(),
                "rows": len(rows),
                "inserted": inserted,
                "updated": updated,
                "counts": counts,
                "cutover_mode_at_apply": current_mode,
                "readiness_at_apply": current_readiness,
                "readiness_update_applied": readiness_update_applied,
                "checked_at": datetime.now(UTC).isoformat(),
            }
            p0_snapshot = json.dumps({"occupancy_foundation": summary})
            now = datetime.now(UTC)
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS_TENANT_ID)
            # Only a pre-shadow domain may be marked ready_for_shadow. Reruns
            # after promotion refresh the snapshot/audit evidence without
            # downgrading postgres_shadow/postgres_read readiness.
            if readiness_update_applied:
                await conn.execute(
                    """
                    UPDATE core.domain_cutover_status
                    SET readiness_status = 'ready_for_shadow',
                        notes = $1,
                        p0_snapshot = COALESCE(p0_snapshot, '{}'::jsonb) || $2::jsonb,
                        last_readiness_check_at = $3,
                        updated_at = $3
                    WHERE building_id = $4
                      AND domain = 'occupancy'
                      AND mode = 'mongo_primary'
                      AND is_test_data = false
                    """,
                    "Occupancy PostgreSQL snapshot bootstrapped from explicit lot occupancy classification",
                    p0_snapshot,
                    now,
                    building_id,
                )
            await conn.execute(
                """
                INSERT INTO core.cutover_audit_log (
                    id, tenant_id, building_id, domain, action,
                    from_mode, to_mode, actor_role, reason,
                    p0_snapshot, metadata, is_test_data, created_at
                ) VALUES (
                    $1::uuid, $2::uuid, $3, 'occupancy', 'readiness_checked',
                    $4, $4, 'system', $5,
                    $6::jsonb, '{}'::jsonb, false, $7
                )
                """,
                str(uuid4()),
                tenant_id,
                building_id,
                current_mode,
                "Occupancy PostgreSQL snapshot bootstrapped; ready for shadow",
                p0_snapshot,
                now,
            )

        print(
            json.dumps(
                {
                    "inserted": inserted,
                    "updated": updated,
                    "readiness_update_applied": readiness_update_applied,
                    "readiness_status": "ready_for_shadow" if readiness_update_applied else current_readiness,
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        await conn.close()
        mongo.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap PostgreSQL occupancy snapshot")
    parser.add_argument("--building-id", default="13195")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(building_id=args.building_id, dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
