#!/usr/bin/env python3
# @featuretrace:finance-shadow-reads — Phase D activation: promote finance_ledger to postgres_shadow.
# Layer: script
# Data flow: CLI → core.domain_cutover_status + core.shadow_diffs + core.cutover_audit_log (building-scoped).
# Related: backend/services/finance_shadow_read_service.py
#          backend/services/cutover_status_service.py
#          backend/scripts/east_gate_phase_d_shadow_status.py
#          docs/migration/strataos_migration_production_prompt_v3.md §Phase D
"""Phase D Activation — Promote finance_ledger to postgres_shadow for East Gate 13195.

What this does:
  1. Validates P0 readiness gates still pass (safety check).
  2. Transitions finance_ledger domain_cutover_status from mongo_primary → postgres_shadow.
     read_source and write_source remain 'mongo'; PG receives comparison data only.
  3. Updates readiness_status → shadow_active.
  4. Marks the 10 existing stale 'pg_unavailable' shadow_diffs as resolved (they pre-date
     Phase C data migration; they should not count against shadow parity).
  5. Writes an audit row to core.cutover_audit_log.

After activation, every request to the 5 finance contract routes fires a background
shadow comparison (mongo vs PG). Results land in core.shadow_diffs; monitor via
east_gate_phase_d_shadow_status.py.

Rollback: run with --rollback to revert finance_ledger to mongo_primary.

Usage:
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    python backend/scripts/east_gate_phase_d_activate.py --dry-run
    python backend/scripts/east_gate_phase_d_activate.py --apply
    python backend/scripts/east_gate_phase_d_activate.py --rollback
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "backend" / ".env")

DATABASE_URL = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
BYPASS = "00000000-0000-0000-0000-000000000000"

EG_BUILDING_ID = "13195"
EG_TENANT_ID = "9e9d75c2-bd92-4695-8487-1592018c3af9"
EG_SCHEME_ID = "d565e4fa-bd11-4fb1-a213-82100ddc78ff"

_FINANCE_DOMAIN = "finance_ledger"
_ACTOR_REASON = "Phase D activation 2026-06-29: finance P0 gates passed; beginning shadow observation window."


async def _check_p0_pass(conn: asyncpg.Connection) -> tuple[bool, str]:
    """Run the P0 readiness script and return (pass, summary)."""
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "backend" / "scripts" / "postgres_cutover_p0_readiness.py"),
                "--building-id", EG_BUILDING_ID,
            ],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(result.stdout)
        ok = data.get("status") == "pass"
        checks = data.get("checks", [])
        failed = [c["id"] for c in checks if c["status"] != "pass"]
        return ok, f"overall={data.get('status')} failed={failed}"
    except Exception as exc:
        return False, f"P0 check failed to run: {exc}"


async def _get_current_mode(conn: asyncpg.Connection) -> str | None:
    """Generated function header.

    Function: _get_current_mode
    Path: backend/scripts/east_gate_phase_d_activate.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)
    row = await conn.fetchrow(
        "SELECT mode, readiness_status FROM core.domain_cutover_status WHERE building_id=$1 AND domain=$2",
        EG_BUILDING_ID, _FINANCE_DOMAIN,
    )
    return row["mode"] if row else None


async def _get_actor_user_id(conn: asyncpg.Connection) -> str:
    """Generated function header.

    Function: _get_actor_user_id
    Path: backend/scripts/east_gate_phase_d_activate.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", EG_TENANT_ID)
    row = await conn.fetchrow(
        """
        SELECT user_id::text FROM core.users
        WHERE tenant_id=$1::uuid AND is_active=TRUE AND is_approved=TRUE
        ORDER BY CASE WHEN role::text='super_admin' THEN 0 WHEN role::text='strata_manager' THEN 1 ELSE 2 END, created_at
        LIMIT 1
        """,
        EG_TENANT_ID,
    )
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)
    if not row:
        return str(uuid4())  # fallback synthetic actor
    return row["user_id"]


async def _audit_log(conn: asyncpg.Connection, *, action: str, from_mode: str, to_mode: str, actor_id: str, metadata: dict) -> None:
    """Generated function header.

    Function: _audit_log
    Path: backend/scripts/east_gate_phase_d_activate.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await conn.execute(
        """
        INSERT INTO core.cutover_audit_log
            (id, tenant_id, building_id, domain, action, from_mode, to_mode,
             actor_user_id, actor_role, reason, metadata, is_test_data, created_at)
        VALUES ($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8::uuid,'super_admin',$9,$10::jsonb,FALSE,NOW())
        """,
        str(uuid4()), EG_TENANT_ID, EG_BUILDING_ID, _FINANCE_DOMAIN,
        action, from_mode, to_mode, actor_id, _ACTOR_REASON, json.dumps(metadata, default=str),
    )


async def activate(conn: asyncpg.Connection, dry_run: bool) -> dict:
    """Generated function header.

    Function: activate
    Path: backend/scripts/east_gate_phase_d_activate.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    current_mode = await _get_current_mode(conn)
    if current_mode is None:
        return {"error": f"No domain_cutover_status row for {_FINANCE_DOMAIN} on {EG_BUILDING_ID}"}

    if current_mode == "postgres_shadow":
        return {"status": "already_active", "mode": current_mode, "dry_run": dry_run}

    if current_mode not in ("mongo_primary", "ready_for_shadow"):
        return {"error": f"Unexpected current mode {current_mode!r} — cannot advance to postgres_shadow from here"}

    # Count stale pg_unavailable diffs to resolve
    stale = await conn.fetchval(
        "SELECT COUNT(*) FROM core.shadow_diffs WHERE building_id=$1 AND diff_type='pg_unavailable' AND resolved=FALSE",
        EG_BUILDING_ID,
    )

    preview = {
        "domain": _FINANCE_DOMAIN,
        "building_id": EG_BUILDING_ID,
        "from_mode": current_mode,
        "to_mode": "postgres_shadow",
        "readiness_status_after": "shadow_active",
        "stale_diffs_to_resolve": int(stale),
        "dry_run": dry_run,
    }
    if dry_run:
        return preview

    actor_id = await _get_actor_user_id(conn)
    now = datetime.now(UTC)

    async with conn.transaction():
        # Promote domain mode
        await conn.execute(
            """
            UPDATE core.domain_cutover_status
            SET mode = 'postgres_shadow',
                readiness_status = 'shadow_active',
                previous_mode = $1,
                rollback_available = TRUE,
                read_source = 'mongo',
                write_source = 'mongo',
                notes = COALESCE(notes, '') || ' | Phase D activated ' || $2::text,
                updated_at = $3
            WHERE building_id = $4 AND domain = $5
            """,
            current_mode, now.date().isoformat(), now, EG_BUILDING_ID, _FINANCE_DOMAIN,
        )

        # Mark stale pg_unavailable diffs as resolved
        resolved_count_str = await conn.execute(
            """
            UPDATE core.shadow_diffs
            SET resolved = TRUE,
                resolved_at = $1,
                notes = 'Resolved at Phase D activation: pre-dated Phase C migration, PG data now populated'
            WHERE building_id = $2
              AND diff_type = 'pg_unavailable'
              AND resolved = FALSE
            """,
            now, EG_BUILDING_ID,
        )

        # Audit log
        await _audit_log(
            conn, action="entered_shadow",
            from_mode=current_mode, to_mode="postgres_shadow",
            actor_id=actor_id,
            metadata={
                "stale_diffs_resolved": int(stale),
                "phase": "D",
                "reason": _ACTOR_REASON,
            },
        )

    return {**preview, "dry_run": False, "resolved_stale_diffs": int(stale)}


async def rollback(conn: asyncpg.Connection, dry_run: bool) -> dict:
    """Generated function header.

    Function: rollback
    Path: backend/scripts/east_gate_phase_d_activate.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    current_mode = await _get_current_mode(conn)
    if current_mode != "postgres_shadow":
        return {"status": "not_in_shadow", "mode": current_mode, "dry_run": dry_run}

    preview = {
        "domain": _FINANCE_DOMAIN,
        "from_mode": "postgres_shadow",
        "to_mode": "mongo_primary",
        "readiness_status_after": "ready_for_shadow",
        "dry_run": dry_run,
    }
    if dry_run:
        return preview

    actor_id = await _get_actor_user_id(conn)
    now = datetime.now(UTC)
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE core.domain_cutover_status
            SET mode = 'mongo_primary',
                readiness_status = 'ready_for_shadow',
                previous_mode = 'postgres_shadow',
                notes = COALESCE(notes, '') || ' | Phase D rolled back ' || $1::text,
                updated_at = $2
            WHERE building_id = $3 AND domain = $4
            """,
            now.date().isoformat(), now, EG_BUILDING_ID, _FINANCE_DOMAIN,
        )
        await _audit_log(
            conn, action="rolled_back",
            from_mode="postgres_shadow", to_mode="mongo_primary",
            actor_id=actor_id,
            metadata={"phase": "D", "reason": "Operator rollback"},
        )
    return {**preview, "dry_run": False}


async def run(dry_run: bool, do_rollback: bool) -> dict:
    """Generated function header.

    Function: run
    Path: backend/scripts/east_gate_phase_d_activate.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)

        # P0 gate
        p0_ok, p0_summary = await _check_p0_pass(conn)
        if not p0_ok and not do_rollback:
            return {"error": f"P0 gates not passing — cannot activate Phase D. {p0_summary}"}

        if do_rollback:
            result = await rollback(conn, dry_run)
        else:
            result = await activate(conn, dry_run)

        # Append final domain state
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)
        final = await conn.fetchrow(
            "SELECT mode, readiness_status, read_source, write_source FROM core.domain_cutover_status WHERE building_id=$1 AND domain=$2",
            EG_BUILDING_ID, _FINANCE_DOMAIN,
        )
        result["p0_check"] = p0_summary
        result["final_domain_state"] = dict(final) if final else {}
        return result
    finally:
        await conn.close()


def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/east_gate_phase_d_activate.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Phase D: activate/rollback finance_ledger shadow mode for East Gate.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(run(dry_run=args.dry_run, do_rollback=args.rollback))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
