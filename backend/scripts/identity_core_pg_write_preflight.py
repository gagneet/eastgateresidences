#!/usr/bin/env python3
# @featuretrace:postgres-identity-foundation — Read-only identity_core PG-write readiness preflight.
# Layer: script
# Data flow: CLI -> live Mongo/PG identity read models + cutover control plane -> redacted JSON/stdout report.
# Related: backend/services/owner_service.py
#          backend/services/owner_read_service.py
#          backend/services/ownership_shadow_read_service.py
#          backend/scripts/phase_d_shadow_status.py
"""Read-only preflight for promoting identity_core to postgres_write.

This script intentionally does not call record_identity_foundation_readiness(),
promote_domain(), rollback_domain(), or any bootstrap apply path. It answers the
operator question: "what still blocks identity_core from PG write right now?"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "backend" / ".env")
sys.path.insert(0, str(ROOT / "backend"))

from request_context import get_ctx_building_id, set_ctx_building_id  # noqa: E402
from database import db  # noqa: E402
from services import owner_service  # noqa: E402
from services.cutover_config_service import (  # noqa: E402
    FINANCIAL_SHADOW_READS_ENABLED,
    OWNER_READ_PG_ENABLED,
    is_cutover_feature_enabled,
)
from services.ownership_shadow_read_service import _redact  # noqa: E402
from services.shadow_read_service import classify_route_day  # noqa: E402

DATABASE_URL = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
DOMAIN = "identity_core"
BYPASS = "00000000-0000-0000-0000-000000000000"
CONTRACT_ROUTES = [
    "identity.users.list",
    "ownership.owner.current",
    "ownership.owner.all_units",
    "ownership.owner.agm_voters",
    "ownership.owner.membership_check",
]
TARGET_CLEAN_DAYS = 7


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _json_field(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _owner_keys(payload: dict[str, dict[str, Any]]) -> set[str]:
    return {str(key) for key in payload.keys()}


def _strip_owner_id(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_owner_id(val) for key, val in value.items() if key != "owner_id"}
    if isinstance(value, list):
        return [_strip_owner_id(item) for item in value]
    return value


async def _domain_state(conn: asyncpg.Connection, building_id: str) -> dict[str, Any]:
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)
    row = await conn.fetchrow(
        """
        SELECT mode, readiness_status, read_source, write_source, previous_mode,
               rollback_available, updated_at, p0_snapshot
        FROM core.domain_cutover_status
        WHERE building_id = $1 AND domain = $2
        """,
        building_id,
        DOMAIN,
    )
    if not row:
        return {"missing": True}
    out = dict(row)
    out["p0_snapshot"] = _json_field(out.get("p0_snapshot"))
    return {key: _json_ready(value) for key, value in out.items()}


async def _scheme_context(conn: asyncpg.Connection, building_id: str) -> dict[str, Any] | None:
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
    return dict(row) if row else None


async def _pg_identity_counts(conn: asyncpg.Connection, scheme: dict[str, Any]) -> dict[str, Any]:
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", scheme["tenant_id"])
    counts = await conn.fetchrow(
        """
        SELECT
          (SELECT COUNT(*) FROM core.lots WHERE scheme_id = $1::uuid) AS lots,
          (SELECT COUNT(*) FROM core.parties WHERE tenant_id = $2::uuid) AS parties,
          (SELECT COUNT(*) FROM core.users WHERE tenant_id = $2::uuid) AS users,
          (SELECT COUNT(*) FROM core.users WHERE tenant_id = $2::uuid AND party_id IS NOT NULL) AS users_with_party,
          (SELECT COUNT(*) FROM core.user_units WHERE scheme_id = $1::uuid AND valid_to IS NULL) AS user_units,
          (SELECT COUNT(*) FROM core.user_units WHERE scheme_id = $1::uuid AND valid_to IS NULL AND party_id IS NOT NULL) AS user_units_with_party,
          (SELECT COUNT(*) FROM core.ownership_periods WHERE scheme_id = $1::uuid AND recorded_to IS NULL) AS ownership_periods,
          (SELECT COUNT(*) FROM core.ownership_periods WHERE scheme_id = $1::uuid AND recorded_to IS NULL
             AND valid_from <= CURRENT_DATE AND (valid_to IS NULL OR valid_to > CURRENT_DATE)) AS current_ownership_periods,
          (SELECT MIN(updated_at) FROM core.lots WHERE scheme_id = $1::uuid) AS lots_min_updated_at,
          (SELECT MAX(updated_at) FROM core.lots WHERE scheme_id = $1::uuid) AS lots_max_updated_at,
          (SELECT MIN(updated_at) FROM core.parties WHERE tenant_id = $2::uuid) AS parties_min_updated_at,
          (SELECT MAX(updated_at) FROM core.parties WHERE tenant_id = $2::uuid) AS parties_max_updated_at
        """,
        scheme["scheme_id"],
        scheme["tenant_id"],
    )
    return {key: _json_ready(value) for key, value in dict(counts).items()}


async def _pg_owner_linkage(conn: asyncpg.Connection, scheme: dict[str, Any]) -> dict[str, Any]:
    rows = await conn.fetch(
        """
        SELECT
          COALESCE(l.unit_number, l.lot_number) AS unit_number,
          COUNT(*) AS owner_rows,
          COUNT(*) FILTER (WHERE u.user_id IS NOT NULL) AS owner_rows_with_user
        FROM core.ownership_periods op
        JOIN core.lots l ON l.lot_id = op.lot_id
        JOIN core.parties p ON p.party_id = op.owner_party_id
        LEFT JOIN LATERAL (
          SELECT user_id
          FROM core.users u
          WHERE u.party_id = p.party_id AND u.is_active = TRUE
          ORDER BY COALESCE(u.is_approved, false) DESC, u.created_at ASC
          LIMIT 1
        ) u ON TRUE
        WHERE op.scheme_id = $1::uuid
          AND op.recorded_to IS NULL
          AND op.valid_from <= CURRENT_DATE
          AND (op.valid_to IS NULL OR op.valid_to > CURRENT_DATE)
        GROUP BY COALESCE(l.unit_number, l.lot_number)
        ORDER BY COALESCE(l.unit_number, l.lot_number)
        """,
        scheme["scheme_id"],
    )
    missing_user_units = [
        r["unit_number"]
        for r in rows
        if int(r["owner_rows_with_user"] or 0) < int(r["owner_rows"] or 0)
    ]
    return {
        "units_with_current_owner_rows": len(rows),
        "units_with_owner_rows_missing_pg_user_link": len(missing_user_units),
        "sample_units_missing_pg_user_link": missing_user_units[:20],
    }


async def _shadow_summary(conn: asyncpg.Connection, building_id: str, hours: int) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = await conn.fetch(
        """
        SELECT route,
               COUNT(*) FILTER (WHERE resolved = FALSE) AS unresolved,
               COUNT(*) FILTER (WHERE resolved = FALSE AND divergence_score > 0) AS divergent_unresolved,
               COUNT(*) FILTER (WHERE diff_type = 'pg_unavailable') AS pg_unavailable,
               COUNT(*) FILTER (WHERE diff_type = 'shadow_ok' OR divergence_score = 0) AS shadow_ok,
               MAX(created_at) AS last_seen
        FROM core.shadow_diffs
        WHERE building_id = $1 AND domain = $2 AND created_at >= $3
        GROUP BY route
        ORDER BY route
        """,
        building_id,
        DOMAIN,
        since,
    )
    per_route = [
        {
            "route": row["route"],
            "unresolved": int(row["unresolved"] or 0),
            "divergent_unresolved": int(row["divergent_unresolved"] or 0),
            "pg_unavailable": int(row["pg_unavailable"] or 0),
            "shadow_ok": int(row["shadow_ok"] or 0),
            "last_seen": row["last_seen"].isoformat() if row["last_seen"] else None,
        }
        for row in rows
    ]
    clean_days: dict[str, dict[str, Any]] = {}
    today = datetime.now(UTC).date()
    for route in CONTRACT_ROUTES:
        streak = 0
        latest = None
        for delta in range(30):
            day = today - timedelta(days=delta)
            classification = await classify_route_day(
                building_id=building_id,
                domain=DOMAIN,
                route=route,
                coverage_date=day,
            )
            if delta == 0:
                latest = classification
            if classification == "no_traffic" and delta == 0:
                continue
            if classification != "clean":
                break
            streak += 1
        clean_days[route] = {"streak": streak, "latest_day_status": latest}
    return {
        "window_hours": hours,
        "per_route": per_route,
        "total_unresolved": sum(item["unresolved"] for item in per_route),
        "total_divergent_unresolved": sum(item["divergent_unresolved"] for item in per_route),
        "clean_days": clean_days,
        "routes_at_target": [
            route for route, detail in clean_days.items()
            if int(detail["streak"]) >= TARGET_CLEAN_DAYS
        ],
    }


async def _owner_payload_parity(building_id: str) -> dict[str, Any]:
    previous_building_id = get_ctx_building_id()
    set_ctx_building_id(building_id)
    try:
        mongo = await owner_service._mongo_get_all_unit_owners(building_id)
        pg = await owner_service._pg_get_all_unit_owners(building_id)
    finally:
        set_ctx_building_id(previous_building_id)

    mongo_redacted = _redact(mongo)
    pg_redacted = _redact(pg)
    mongo_keys = _owner_keys(mongo_redacted)
    pg_keys = _owner_keys(pg_redacted)
    common = sorted(mongo_keys & pg_keys)
    mismatched_common = [
        unit for unit in common
        if mongo_redacted.get(unit) != pg_redacted.get(unit)
    ]
    mismatched_excluding_owner_id = [
        unit for unit in common
        if _strip_owner_id(mongo_redacted.get(unit)) != _strip_owner_id(pg_redacted.get(unit))
    ]
    return {
        "mongo_unit_count": len(mongo_keys),
        "pg_unit_count": len(pg_keys),
        "missing_in_pg": sorted(mongo_keys - pg_keys)[:30],
        "missing_in_mongo": sorted(pg_keys - mongo_keys)[:30],
        "mismatched_common_unit_count": len(mismatched_common),
        "mismatched_common_excluding_owner_id_count": len(mismatched_excluding_owner_id),
        "sample_mismatched_common_units": mismatched_common[:30],
        "sample_mismatched_common_excluding_owner_id_units": mismatched_excluding_owner_id[:30],
    }


async def _access_decision_parity(building_id: str, scheme: dict[str, Any]) -> dict[str, Any]:
    previous_building_id = get_ctx_building_id()
    set_ctx_building_id(building_id)
    try:
        mappings = await db.user_units.find(
            {
                "building_id": building_id,
                "is_active": True,
                "$or": [
                    {"role_at_unit": "owner"},
                    {"role_at_unit": {"$exists": False}},
                    {"role_at_unit": None},
                ],
            },
            {"_id": 0, "user_id": 1, "unit_number": 1, "role_at_unit": 1},
        ).to_list(None)
        user_ids = sorted({m.get("user_id") for m in mappings if m.get("user_id")})
        users = await db.users.find(
            {"id": {"$in": user_ids}},
            {"_id": 0, "id": 1, "email": 1, "role": 1},
        ).to_list(len(user_ids) or 1)
    finally:
        set_ctx_building_id(previous_building_id)

    users_by_id = {user["id"]: user for user in users if user.get("id")}
    conn = await asyncpg.connect(DATABASE_URL)
    mismatches: list[str] = []
    missing_pg_user = 0
    checked = 0
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", scheme["tenant_id"])
        for mapping in mappings:
            user = users_by_id.get(mapping.get("user_id"))
            if not user or not owner_service._is_owner_candidate(mapping, user):
                continue
            email = str(user.get("email") or "").strip().lower()
            unit = str(mapping.get("unit_number") or "").strip()
            if not email or not unit:
                continue
            row = await conn.fetchrow(
                """
                SELECT user_id::text AS user_id
                FROM core.users
                WHERE tenant_id = $1::uuid
                  AND lower(email::text) = $2
                  AND is_active = TRUE
                ORDER BY is_approved DESC, created_at ASC
                LIMIT 1
                """,
                scheme["tenant_id"],
                email,
            )
            if not row:
                missing_pg_user += 1
                mismatches.append(unit)
                continue
            previous = get_ctx_building_id()
            set_ctx_building_id(building_id)
            try:
                mongo_allowed = await owner_service._mongo_is_user_current_owner_of_unit(
                    building_id,
                    str(mapping["user_id"]),
                    unit,
                    datetime.now(UTC).date(),
                )
            finally:
                set_ctx_building_id(previous)
            pg_allowed = await owner_service._pg_is_user_current_owner_of_unit(
                building_id,
                row["user_id"],
                unit,
                datetime.now(UTC).date(),
            )
            checked += 1
            if mongo_allowed != pg_allowed:
                mismatches.append(unit)
    finally:
        await conn.close()

    return {
        "checked_owner_access_links": checked,
        "missing_pg_user_for_mongo_owner_links": missing_pg_user,
        "access_decision_mismatch_count": len(mismatches),
        "sample_access_decision_mismatch_units": sorted(set(mismatches))[:30],
    }


def _promotion_blockers(report: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    state = report["domain_state"]
    already_promoted = state.get("mode") == "postgres_write" and state.get("readiness_status") == "promoted"
    if state.get("missing"):
        blockers.append("identity_core has no control-plane row")
    elif state.get("mode") not in {"postgres_read", "postgres_write"}:
        blockers.append(
            f"identity_core must be in postgres_read before postgres_write promotion; current mode={state.get('mode')}"
        )
    elif state.get("mode") == "postgres_read":
        blockers.append("identity_core is postgres_read and ready for postgres_write promotion")
    if state.get("readiness_status") == "rolled_back":
        blockers.append("identity_core readiness_status is rolled_back")

    shadow = report["shadow"]
    if shadow["total_divergent_unresolved"] and not already_promoted:
        blockers.append(f"identity_core has {shadow['total_divergent_unresolved']} unresolved divergent shadow diffs")
    missing_clean = [
        route for route in CONTRACT_ROUTES
        if int(shadow["clean_days"][route]["streak"]) < TARGET_CLEAN_DAYS
    ]
    if missing_clean and not already_promoted:
        blockers.append(f"identity_core lacks {TARGET_CLEAN_DAYS} clean days on routes: {', '.join(missing_clean)}")

    owner_parity = report["owner_payload_parity"]
    if owner_parity["missing_in_pg"] or owner_parity["missing_in_mongo"] or owner_parity["mismatched_common_excluding_owner_id_count"]:
        blockers.append("Mongo and PG owner payloads do not match")

    access = report["access_decision_parity"]
    if access["missing_pg_user_for_mongo_owner_links"] or access["access_decision_mismatch_count"]:
        blockers.append("Mongo and PG owner access decisions do not match")

    toggles = report["toggles"]
    if not toggles.get(OWNER_READ_PG_ENABLED):
        blockers.append(f"{OWNER_READ_PG_ENABLED} is disabled")
    if not toggles.get(FINANCIAL_SHADOW_READS_ENABLED):
        blockers.append(f"{FINANCIAL_SHADOW_READS_ENABLED} is disabled")
    return blockers


async def run(building_id: str, hours: int) -> dict[str, Any]:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        state = await _domain_state(conn, building_id)
        scheme = await _scheme_context(conn, building_id)
        if not scheme:
            return {
                "as_of": datetime.now(UTC).isoformat(),
                "building_id": building_id,
                "status": "blocked",
                "blockers": ["no matching core.schemes row"],
            }
        report = {
            "as_of": datetime.now(UTC).isoformat(),
            "building_id": building_id,
            "domain": DOMAIN,
            "domain_state": state,
            "scheme": scheme,
            "toggles": {
                OWNER_READ_PG_ENABLED: await is_cutover_feature_enabled(building_id, OWNER_READ_PG_ENABLED),
                FINANCIAL_SHADOW_READS_ENABLED: await is_cutover_feature_enabled(building_id, FINANCIAL_SHADOW_READS_ENABLED),
            },
            "pg_identity_counts": await _pg_identity_counts(conn, scheme),
            "pg_owner_linkage": await _pg_owner_linkage(conn, scheme),
            "shadow": await _shadow_summary(conn, building_id, hours),
            "owner_payload_parity": await _owner_payload_parity(building_id),
        }
        report["access_decision_parity"] = await _access_decision_parity(building_id, scheme)
        blockers = _promotion_blockers(report)
        already_promoted = (
            report["domain_state"].get("mode") == "postgres_write"
            and report["domain_state"].get("readiness_status") == "promoted"
        )
        report["status"] = "postgres_write_promoted" if already_promoted and not blockers else (
            "eligible_for_postgres_write_promotion" if not blockers else "blocked"
        )
        report["blockers"] = blockers
        return report
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only identity_core PG-write preflight.")
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--last-hours", type=int, default=168)
    parser.add_argument("--json", action="store_true", help="Emit full JSON report.")
    args = parser.parse_args()

    report = asyncio.run(run(args.building_id, args.last_hours))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return

    print(f"identity_core PG-write preflight for building {args.building_id}: {report['status']}")
    for blocker in report.get("blockers", []):
        print(f"- {blocker}")
    print(json.dumps({
        "domain_state": report.get("domain_state"),
        "toggles": report.get("toggles"),
        "pg_identity_counts": report.get("pg_identity_counts"),
        "pg_owner_linkage": report.get("pg_owner_linkage"),
        "owner_payload_parity": report.get("owner_payload_parity"),
        "access_decision_parity": report.get("access_decision_parity"),
        "shadow_totals": {
            "total_unresolved": report.get("shadow", {}).get("total_unresolved"),
            "total_divergent_unresolved": report.get("shadow", {}).get("total_divergent_unresolved"),
            "routes_at_target": report.get("shadow", {}).get("routes_at_target"),
        },
    }, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
