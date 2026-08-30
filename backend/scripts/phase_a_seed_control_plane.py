#!/usr/bin/env python3
"""Phase A — Control Plane Seed & Orphan Tenant Quarantine.

Reads production state and writes explicit domain_cutover_status rows for East Gate
(building_id 13195) across all live domains, eliminating missing-state defaulting.
Also quarantines the 138 orphan tenants that have no linked schemes.

Safety:
  - No data is deleted.
  - Orphan tenants are archived (status='archived'), not deleted.
  - domain_cutover_status rows are upserted with ON CONFLICT; existing rows are NOT
    downgraded (mode stays at whatever the current value is if already beyond mongo_primary).
  - building_id=99999 row is flagged is_test_data=TRUE.
  - All changes are wrapped in explicit transactions; --dry-run exits before commit.

Usage:
    cd backend
    venv/bin/python3 scripts/phase_a_seed_control_plane.py --dry-run
    venv/bin/python3 scripts/phase_a_seed_control_plane.py --apply
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

DATABASE_URL = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
BYPASS = "00000000-0000-0000-0000-000000000000"

# East Gate production constants (from live DB)
EG_BUILDING_ID = "13195"
EG_TENANT_ID = "9e9d75c2-bd92-4695-8487-1592018c3af9"
EG_SCHEME_ID = "d565e4fa-bd11-4fb1-a213-82100ddc78ff"

# Domains to seed for East Gate (canonical names; aliases resolved by domain_source_guard)
# mode = mongo_primary  → source still Mongo, but row is EXPLICIT (no missing-state defaulting)
# readiness_status = not_started → honest; identity is done, finance is not yet
# Valid readiness_status values from DB constraint:
# unknown, not_started, shadow_active, shadow_passing, shadow_clean,
# promoted, rolled_back, blocked
# (identity_ready/evidence_ready/genesis_ready/ready_for_shadow exist in Python enum
#  but are NOT in the DB CHECK constraint — use not_started for those stages)
DOMAINS_TO_SEED = [
    # Already has a row with readiness=blocked — update to not_started (identity IS done)
    # Using not_started because identity_ready is not in the DB constraint yet
    ("identity_core", "not_started", "Identity foundation complete 2026-06-29: lots=87 parties=250 users=12"),
    # Finance — levy charges migrated but no evidence/genesis journals yet (Phase C)
    ("finance_ledger", "not_started", "Finance migration in progress; evidence and genesis journals pending (Phase C)"),
    # Trust
    ("trust_ledger", "not_started", "Trust accounting — pending Phase C evidence and genesis"),
    ("trust_reconciliation", "not_started", "Trust reconciliation — pending Phase C"),
    # Governance / operational / settings — seed explicit rows so guard never defaults
    ("governance", "not_started", "Governance domain — no PG migration yet"),
    ("settings", "not_started", "Settings domain — no PG migration yet"),
    ("occupancy", "not_started", "Occupancy domain — no PG migration yet"),
]

# Orphan tenant names that are clearly test/trial/seed noise
# All have is_test_data=FALSE but no linked schemes.  We archive them.
_PRODUCTION_TENANT_IDS = {EG_TENANT_ID, "2c5ed28b-5e37-5e6e-ac92-6ee46051b778"}


def _now() -> datetime:
    """Generated function header.

    Function: _now
    Path: backend/scripts/phase_a_seed_control_plane.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(UTC)


async def run(dry_run: bool) -> None:
    """Generated function header.

    Function: run
    Path: backend/scripts/phase_a_seed_control_plane.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)

        # ─── 1. Audit orphan tenants ──────────────────────────────────────────
        orphans = await conn.fetch("""
            SELECT tenant_id::text, tenant_name, status, created_at
            FROM core.tenants t
            WHERE NOT EXISTS (SELECT 1 FROM core.schemes s WHERE s.tenant_id = t.tenant_id)
            ORDER BY tenant_name, created_at
        """)
        print(f"\n[A.2] Orphan tenant quarantine — {len(orphans)} rows to archive:")
        for o in orphans[:5]:
            print(f"  {o['tenant_name']!r} ({o['tenant_id']}) created={o['created_at'].date()}")
        if len(orphans) > 5:
            print(f"  ... and {len(orphans) - 5} more")

        # Verify we are NOT touching production tenants
        orphan_ids = [o["tenant_id"] for o in orphans]
        overlap = set(orphan_ids) & _PRODUCTION_TENANT_IDS
        if overlap:
            print(f"[ERROR] Production tenant IDs found in orphan list: {overlap}")
            sys.exit(1)

        # ─── 2. Audit domain_cutover_status ──────────────────────────────────
        existing_dcs = await conn.fetch("""
            SELECT id::text, building_id, domain, mode, readiness_status, is_test_data
            FROM core.domain_cutover_status
            ORDER BY building_id, domain
        """)
        print(f"\n[A.3] Current domain_cutover_status ({len(existing_dcs)} rows):")
        for r in existing_dcs:
            print(f"  building_id={r['building_id']} domain={r['domain']} mode={r['mode']} "
                  f"readiness={r['readiness_status']} is_test_data={r['is_test_data']}")

        # building_id=99999 should be flagged test
        has_99999 = any(r["building_id"] == "99999" for r in existing_dcs)
        print(f"\n  building_id=99999 present: {has_99999} → will be marked is_test_data=TRUE")

        # Check which domains already exist for 13195
        existing_eg_domains = {r["domain"]: dict(r) for r in existing_dcs if r["building_id"] == EG_BUILDING_ID}
        print(f"\n  East Gate domains already seeded: {sorted(existing_eg_domains.keys())}")
        missing = [d for d, _, _ in DOMAINS_TO_SEED if d not in existing_eg_domains]
        print(f"  Domains to INSERT: {missing}")
        to_update = [d for d, _, _ in DOMAINS_TO_SEED if d in existing_eg_domains]
        print(f"  Domains already present (notes will be updated): {to_update}")

        if dry_run:
            print("\n[DRY RUN] No writes performed. Re-run with --apply to execute.")
            return

        # ─── BEGIN TRANSACTION ────────────────────────────────────────────────
        async with conn.transaction():
            now = _now()

            # A.2 — Archive orphan tenants
            archived_cnt = await conn.execute("""
                UPDATE core.tenants
                SET status = 'archived', updated_at = $1
                WHERE tenant_id = ANY($2::uuid[])
                  AND status != 'archived'
            """, now, orphan_ids)
            print(f"\n[A.2] Archived {archived_cnt} orphan tenants.")

            # A.3a — Flag building_id=99999 row as test data
            if has_99999:
                upd = await conn.execute("""
                    UPDATE core.domain_cutover_status
                    SET is_test_data = TRUE, updated_at = $1,
                        notes = COALESCE(notes, '') || ' [quarantined 2026-06-29: non-production building_id]'
                    WHERE building_id = '99999'
                """, now)
                print(f"[A.3a] Quarantined building_id=99999 rows: {upd}")

            # A.3b — Seed explicit domain_cutover_status for East Gate
            inserted = 0
            updated_notes = 0
            for domain, readiness_status, notes in DOMAINS_TO_SEED:
                existing = existing_eg_domains.get(domain)
                if existing:
                    # Do NOT downgrade mode. Update readiness_status and notes for identity_core
                    # since Phase B identity is confirmed complete (87 lots, 250 parties, etc.)
                    await conn.execute("""
                        UPDATE core.domain_cutover_status
                        SET readiness_status = $1,
                            notes = COALESCE(notes, $2),
                            updated_at = $3
                        WHERE building_id = $4 AND domain = $5
                          AND (readiness_status = 'blocked' OR notes IS NULL)
                    """, readiness_status, notes, now, EG_BUILDING_ID, domain)
                    updated_notes += 1
                    print(f"  [skip insert] {domain} already at mode={existing['mode']} — "
                          f"readiness_status→{readiness_status}, notes updated")
                else:
                    row_id = str(uuid4())
                    await conn.execute("""
                        INSERT INTO core.domain_cutover_status (
                            id, tenant_id, building_id, scheme_id, domain,
                            read_source, write_source, mode,
                            readiness_status, previous_mode, rollback_available,
                            notes, p0_snapshot, is_test_data, created_at, updated_at
                        ) VALUES (
                            $1::uuid, $2::uuid, $3, $4::uuid, $5,
                            'mongo', 'mongo', 'mongo_primary',
                            $6, 'mongo_primary', TRUE,
                            $7, '{}'::jsonb, FALSE, $8, $8
                        )
                        ON CONFLICT (building_id, domain) DO NOTHING
                    """, row_id, EG_TENANT_ID, EG_BUILDING_ID, EG_SCHEME_ID, domain,
                         readiness_status, notes, now)
                    inserted += 1
                    print(f"  [inserted] {domain} readiness={readiness_status}")

            print(f"\n[A.3b] domain_cutover_status: {inserted} inserted, {updated_notes} notes updated.")

        # ─── Verify final state ───────────────────────────────────────────────
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)
        final = await conn.fetch("""
            SELECT building_id, domain, mode, readiness_status, is_test_data
            FROM core.domain_cutover_status
            WHERE is_test_data = FALSE
            ORDER BY building_id, domain
        """)
        print(f"\n[RESULT] domain_cutover_status production rows ({len(final)}):")
        for r in final:
            print(f"  building_id={r['building_id']} domain={r['domain']} "
                  f"mode={r['mode']} readiness={r['readiness_status']}")

        archived_count = await conn.fetchval("SELECT COUNT(*) FROM core.tenants WHERE status='archived'")
        active_count = await conn.fetchval("SELECT COUNT(*) FROM core.tenants WHERE status='active'")
        print(f"\n[RESULT] core.tenants: active={active_count}, archived={archived_count}")

        print("\n[Phase A] Complete. All domains seeded; orphan tenants quarantined.")

    finally:
        await conn.close()


def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/phase_a_seed_control_plane.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Phase A — Control plane seed + orphan quarantine")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--dry-run", action="store_true")
    grp.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
