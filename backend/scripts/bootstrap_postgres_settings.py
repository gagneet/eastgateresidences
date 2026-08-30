#!/usr/bin/env python3
"""Bootstrap East Gate settings into PostgreSQL and mark settings ready for shadow.

Source: Mongo settings documents for general, levy reminders, and unit display.
Target: core.building_settings

Usage:
    backend/venv/bin/python3 backend/scripts/bootstrap_postgres_settings.py --building-id 13195 --dry-run
    backend/venv/bin/python3 backend/scripts/bootstrap_postgres_settings.py --building-id 13195 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import motor.motor_asyncio
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "backend" / ".env")

DATABASE_URL = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ.get("DB_NAME", "strataos_production")
BYPASS_TENANT_ID = "00000000-0000-0000-0000-000000000000"

GENERAL_SETTINGS_KEY = "general.settings"
LEVY_REMINDER_SETTINGS_KEY = "finance.levy_reminders"
UNIT_DISPLAY_SETTINGS_KEY = "unit_display.rules"


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _clean_mongo_doc(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(doc, dict):
        return None
    return {key: value for key, value in doc.items() if key != "_id"}


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=_json_safe)


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


async def _load_payloads(db, building_id: str) -> dict[str, dict[str, Any]]:
    general = _clean_mongo_doc(
        await db.settings.find_one(
            {
                "building_id": building_id,
                "$or": [
                    {"type": {"$exists": False}},
                    {"type": None},
                    {"type": "general"},
                ],
            },
            sort=[("updated_at", -1), ("created_at", -1), ("_id", -1)],
        )
    )
    levy = _clean_mongo_doc(
        await db.settings.find_one({"building_id": building_id, "type": "levy_reminder_settings"})
    )
    unit_display = _clean_mongo_doc(
        await db.settings.find_one({"building_id": building_id, "type": "unit_display"})
    )

    payloads: dict[str, dict[str, Any]] = {}
    if general:
        payloads[GENERAL_SETTINGS_KEY] = general
    if levy:
        payloads[LEVY_REMINDER_SETTINGS_KEY] = levy
    if unit_display:
        payloads[UNIT_DISPLAY_SETTINGS_KEY] = unit_display
    return payloads


async def run(*, building_id: str, dry_run: bool) -> None:
    mongo = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URL)
    db = mongo[DB_NAME]
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        tenant_id, scheme_id = await _resolve_scheme(conn, building_id)
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant_id)
        payloads = await _load_payloads(db, building_id)

        existing_rows = await conn.fetch(
            """
            SELECT setting_key, setting_value
            FROM core.building_settings
            WHERE scheme_id = $1::uuid
              AND setting_key = ANY($2::text[])
            ORDER BY setting_key
            """,
            scheme_id,
            list(payloads.keys()) or [GENERAL_SETTINGS_KEY],
        )
        existing = {row["setting_key"]: _as_dict(row["setting_value"]) for row in existing_rows}
        comparisons: dict[str, dict[str, Any]] = {}
        for key, payload in payloads.items():
            pg_payload = existing.get(key)
            payload_keys = set(payload.keys())
            pg_keys = set(pg_payload.keys()) if isinstance(pg_payload, dict) else set()
            mismatches = []
            if isinstance(pg_payload, dict):
                mismatches = [
                    field
                    for field in sorted(payload_keys | pg_keys)
                    if _canonical(payload.get(field)) != _canonical(pg_payload.get(field))
                ]
            comparisons[key] = {
                "source_key_count": len(payload_keys),
                "pg_key_count": len(pg_keys),
                "missing_in_pg": sorted(payload_keys - pg_keys),
                "extra_in_pg": sorted(pg_keys - payload_keys),
                "mismatch_count": len(mismatches),
                "mismatch_keys": mismatches[:50],
            }

        print(
            json.dumps(
                {
                    "building_id": building_id,
                    "tenant_id": tenant_id,
                    "scheme_id": scheme_id,
                    "prepared_settings": sorted(payloads.keys()),
                    "comparisons": comparisons,
                    "dry_run": dry_run,
                },
                indent=2,
                sort_keys=True,
                default=_json_safe,
            )
        )

        if dry_run:
            return

        async with conn.transaction():
            for key, payload in payloads.items():
                await conn.execute(
                    """
                    INSERT INTO core.building_settings
                        (tenant_id, scheme_id, setting_key, setting_value, set_at)
                    VALUES
                        ($1::uuid, $2::uuid, $3, $4::jsonb, now())
                    ON CONFLICT (scheme_id, setting_key)
                    DO UPDATE
                        SET setting_value = EXCLUDED.setting_value,
                            set_at = now()
                    """,
                    tenant_id,
                    scheme_id,
                    key,
                    json.dumps(payload, default=_json_safe),
                )

            current_mode, current_readiness = await _current_cutover_mode(conn, building_id, "settings")
            readiness_update_applied = current_mode == "mongo_primary"
            summary = {
                "status": "pass",
                "source": "Mongo settings documents",
                "target": "core.building_settings",
                "settings_keys": sorted(payloads.keys()),
                "cutover_mode_at_apply": current_mode,
                "readiness_at_apply": current_readiness,
                "readiness_update_applied": readiness_update_applied,
                "checked_at": datetime.now(UTC).isoformat(),
            }
            p0_snapshot = json.dumps({"settings_foundation": summary})
            now = datetime.now(UTC)
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS_TENANT_ID)
            # Only a pre-shadow domain may be marked ready_for_shadow. Reruns
            # after promotion refresh the settings payload/audit evidence
            # without creating an inconsistent postgres_shadow + ready row.
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
                      AND domain = 'settings'
                      AND mode = 'mongo_primary'
                      AND is_test_data = false
                    """,
                    "Settings bootstrap loaded general, levy reminder, and unit-display payloads into core.building_settings.",
                    p0_snapshot,
                    now,
                    building_id,
                )
            await conn.execute(
                """
                INSERT INTO core.cutover_audit_log (
                    tenant_id, building_id, domain, action, from_mode, to_mode,
                    actor_role, reason, metadata, is_test_data, created_at
                ) VALUES (
                    $1::uuid, $2, 'settings', 'readiness_checked',
                    $3, $3, 'system',
                    $4, $5::jsonb, false, $6
                )
                """,
                tenant_id,
                building_id,
                current_mode,
                "Bootstrapped settings data into PostgreSQL core.building_settings.",
                json.dumps(summary),
                now,
            )

        print(
            json.dumps(
                {
                    "applied": True,
                    "readiness_update_applied": readiness_update_applied,
                    "readiness_status": "ready_for_shadow" if readiness_update_applied else current_readiness,
                    "upserted_settings": sorted(payloads.keys()),
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        await conn.close()
        mongo.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--building-id", default="13195")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(run(building_id=args.building_id, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
