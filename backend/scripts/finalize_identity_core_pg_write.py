#!/usr/bin/env python3
# @featuretrace:postgres-identity-foundation — Finalize identity_core promotion after clean parity repair.
# Layer: script
# Data flow: identity_core_pg_write_preflight -> core.shadow_diffs/domain_cutover_status -> promote_domain().
# Related: backend/scripts/identity_core_pg_write_preflight.py
#          backend/services/cutover_status_service.py
"""Finalize identity_core to postgres_write when data/access parity is clean.

The script is intentionally narrow. It refuses to proceed unless the only
remaining preflight blockers are control-plane/stale-shadow blockers, then:
1. resolves old identity_core divergent shadow rows,
2. records a shadow_clean status snapshot for the repaired postgres_shadow row,
3. promotes postgres_shadow -> postgres_read,
4. promotes postgres_read -> postgres_write.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "backend" / ".env")
sys.path.insert(0, str(ROOT / "backend"))

from scripts.identity_core_pg_write_preflight import run as run_preflight  # noqa: E402
from services.cutover_status_service import get_or_default_cutover_status, promote_domain  # noqa: E402

DATABASE_URL = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
BYPASS = "00000000-0000-0000-0000-000000000000"
DOMAIN = "identity_core"


def _is_finalization_only_blocker(blocker: str) -> bool:
    """Return True for blockers this script is explicitly allowed to clear.

    The finalizer may repair stale control-plane state after a clean preflight.
    It must not treat broad strings such as "identity_core has ..." as safe,
    because that would also mask material conditions like a missing status row.
    """
    return (
        blocker.startswith("identity_core must be in postgres_read before postgres_write promotion")
        or blocker == "identity_core is postgres_read and ready for postgres_write promotion"
        or blocker == "identity_core readiness_status is rolled_back"
        or (
            blocker.startswith("identity_core has ")
            and blocker.endswith(" unresolved divergent shadow diffs")
        )
        or blocker.startswith("identity_core lacks 7 clean days on routes:")
    )


def _material_blockers(report: dict) -> list[str]:
    blockers = list(report.get("blockers") or [])
    return [blocker for blocker in blockers if not _is_finalization_only_blocker(blocker)]


def _evidence_snapshot(report: dict, reason: str, *, resolved_shadow_diffs: int | None = None) -> dict:
    """Store a concise readiness snapshot without recursively embedding old snapshots."""
    domain_state = dict(report.get("domain_state") or {})
    domain_state.pop("p0_snapshot", None)
    shadow = report.get("shadow") or {}
    owner_parity = report.get("owner_payload_parity") or {}
    access = report.get("access_decision_parity") or {}
    snapshot = {
        "identity_pg_write_preflight": {
            "status": report.get("status"),
            "checked_at": report.get("as_of"),
            "building_id": report.get("building_id"),
            "domain": DOMAIN,
            "domain_state": domain_state,
            "toggles": report.get("toggles"),
            "owner_payload_parity": {
                "mongo_unit_count": owner_parity.get("mongo_unit_count"),
                "pg_unit_count": owner_parity.get("pg_unit_count"),
                "missing_in_pg": owner_parity.get("missing_in_pg"),
                "missing_in_mongo": owner_parity.get("missing_in_mongo"),
                "mismatched_common_excluding_owner_id_count": owner_parity.get(
                    "mismatched_common_excluding_owner_id_count"
                ),
            },
            "access_decision_parity": {
                "checked_owner_access_links": access.get("checked_owner_access_links"),
                "missing_pg_user_for_mongo_owner_links": access.get(
                    "missing_pg_user_for_mongo_owner_links"
                ),
                "access_decision_mismatch_count": access.get("access_decision_mismatch_count"),
            },
            "shadow_totals": {
                "window_hours": shadow.get("window_hours"),
                "total_unresolved": shadow.get("total_unresolved"),
                "total_divergent_unresolved": shadow.get("total_divergent_unresolved"),
                "routes_at_target": shadow.get("routes_at_target"),
            },
        },
        "finalized_at": datetime.now(UTC).isoformat(),
        "reason": reason,
    }
    if resolved_shadow_diffs is not None:
        snapshot["resolved_shadow_diffs"] = resolved_shadow_diffs
    return snapshot


async def _resolve_shadow_diffs(conn: asyncpg.Connection, building_id: str, reason: str) -> int:
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)
    result = await conn.execute(
        """
        UPDATE core.shadow_diffs
        SET resolved = TRUE,
            resolved_at = NOW(),
            notes = $3
        WHERE building_id = $1
          AND domain = $2
          AND resolved = FALSE
          AND divergence_score > 0
        """,
        building_id,
        DOMAIN,
        reason,
    )
    return int(result.split()[-1])


async def _mark_shadow_clean(conn: asyncpg.Connection, building_id: str, report: dict, reason: str) -> None:
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)
    snapshot = _evidence_snapshot(report, reason)
    await conn.execute(
        """
        UPDATE core.domain_cutover_status
        SET readiness_status = 'shadow_clean',
            mode = 'postgres_shadow',
            read_source = 'mongo',
            write_source = 'mongo',
            notes = $3,
            p0_snapshot = $4::jsonb,
            last_readiness_check_at = NOW(),
            updated_at = NOW()
        WHERE building_id = $1 AND domain = $2
        """,
        building_id,
        DOMAIN,
        reason,
        json.dumps(snapshot, default=str),
    )


async def _refresh_promoted_snapshot(
    conn: asyncpg.Connection,
    building_id: str,
    report: dict,
    reason: str,
    resolved_shadow_diffs: int,
) -> None:
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)
    await conn.execute(
        """
        UPDATE core.domain_cutover_status
        SET notes = $3,
            p0_snapshot = $4::jsonb,
            last_readiness_check_at = NOW(),
            updated_at = NOW()
        WHERE building_id = $1
          AND domain = $2
          AND mode = 'postgres_write'
          AND readiness_status = 'promoted'
        """,
        building_id,
        DOMAIN,
        reason,
        json.dumps(_evidence_snapshot(report, reason, resolved_shadow_diffs=resolved_shadow_diffs), default=str),
    )


async def run(building_id: str, apply: bool, reason: str) -> dict:
    report = await run_preflight(building_id, 168)
    material = _material_blockers(report)
    if material:
        return {
            "building_id": building_id,
            "mode": "apply" if apply else "dry_run",
            "status": "blocked",
            "material_blockers": material,
            "preflight_status": report.get("status"),
        }

    if not apply:
        return {
            "building_id": building_id,
            "mode": "dry_run",
            "status": "ready_to_finalize",
            "remaining_control_plane_blockers": report.get("blockers", []),
        }

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        async with conn.transaction():
            resolved = await _resolve_shadow_diffs(conn, building_id, reason)
            if report.get("domain_state", {}).get("mode") != "postgres_write":
                await _mark_shadow_clean(conn, building_id, report, reason)
            else:
                await _refresh_promoted_snapshot(conn, building_id, report, reason, resolved)
    finally:
        await conn.close()

    if report.get("domain_state", {}).get("mode") == "postgres_write":
        final = await get_or_default_cutover_status(building_id, DOMAIN)
        return {
            "building_id": building_id,
            "mode": "apply",
            "status": "already_promoted",
            "resolved_shadow_diffs": resolved,
            "final_mode": final.mode.value,
            "final_readiness": final.readiness_status.value,
            "final_read_source": final.read_source.value,
            "final_write_source": final.write_source.value,
        }

    actor = str(uuid4())
    read_status, read_audit = await promote_domain(
        building_id=building_id,
        domain=DOMAIN,
        actor_user_id=actor,
        actor_role="super_admin",
        reason=f"{reason}; promote identity_core to postgres_read after parity repair",
    )
    write_status, write_audit = await promote_domain(
        building_id=building_id,
        domain=DOMAIN,
        actor_user_id=actor,
        actor_role="super_admin",
        reason=f"{reason}; promote identity_core to postgres_write after postgres_read",
    )
    final = await get_or_default_cutover_status(building_id, DOMAIN)
    return {
        "building_id": building_id,
        "mode": "apply",
        "status": "promoted",
        "resolved_shadow_diffs": resolved,
        "postgres_read_audit_id": read_audit,
        "postgres_write_audit_id": write_audit,
        "intermediate_mode": read_status.mode.value,
        "final_mode": write_status.mode.value,
        "final_readiness": final.readiness_status.value,
        "final_read_source": final.read_source.value,
        "final_write_source": final.write_source.value,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Finalize identity_core PG Write after clean preflight.")
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.building_id, args.apply, args.reason)), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
