"""Enable (or verify) all financial cutover feature toggles for East Gate (13195).

Idempotent — safe to run multiple times. Uses ON CONFLICT DO UPDATE so
re-running after a partial failure leaves the database in the correct state.

Applied 2026-05-11 after parity audit confirmed 11/11 domains match between
MongoDB and PostgreSQL for building_id=13195.

Usage:
    cd backend
    set -a && source .env && set +a
    python3 scripts/audits/east_gate_cutover_toggles.py [--verify-only]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT_DIR = Path(__file__).parent.parent.parent
load_dotenv(ROOT_DIR / ".env")

SCHEME_ID = "d565e4fa-bd11-4fb1-a213-82100ddc78ff"
TENANT_ID = "9e9d75c2-bd92-4695-8487-1592018c3af9"
SET_BY    = "7d532e51-a65e-42c9-8e0b-4ba885af2d61"  # gagneet@silverfoxtechnologies.com.au
REASON    = (
    "Parity audit confirmed 11/11 domains match between MongoDB and PostgreSQL "
    "for East Gate Residences (building_id=13195). Full PG cutover authorised 2026-05-11."
)

TOGGLES = [
    "financial_integration_layer_v2",        # umbrella — all child toggles require this
    "financial_pg_writes_enabled",           # new transactions written to Postgres
    "financial_pg_reads_enabled",            # financial APIs served from Postgres
    "financial_shadow_reads_enabled",        # parallel Mongo+PG read for divergence logging
    "owner_read_pg_enabled",                 # owner/lot data from Postgres
    "trust_pg_ledger_enabled",               # trust ledger from Postgres
    "trust_reconciliation_pg_enabled",       # trust reconciliation from Postgres
    "external_api_finance_pg_enabled",       # external-facing finance API from Postgres
    "onboarding_current_balance_adapters_enabled",  # onboarding balance adapters
]


async def run(verify_only: bool) -> int:
    """Generated function header.

    Function: run
    Path: backend/scripts/audits/east_gate_cutover_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        import asyncpg
    except ImportError:
        print("asyncpg not installed: pip install asyncpg", file=sys.stderr)
        return 1

    db_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    pg = await asyncpg.connect(db_url)

    await pg.execute(f"SELECT set_config('app.tenant_id', '{TENANT_ID}', false)")

    if not verify_only:
        for key in TOGGLES:
            await pg.execute(
                """
                INSERT INTO core.feature_toggle_overrides
                    (tenant_id, scheme_id, feature_key, is_enabled, set_by, set_at, reason)
                VALUES ($1, $2, $3, TRUE, $4, NOW(), $5)
                ON CONFLICT (scheme_id, feature_key)
                DO UPDATE SET
                    is_enabled = TRUE,
                    set_by     = EXCLUDED.set_by,
                    set_at     = NOW(),
                    reason     = EXCLUDED.reason
                """,
                TENANT_ID, SCHEME_ID, key, SET_BY, REASON,
            )

    # Verify via the resolver function (authoritative gate used by the application)
    print(f"\n{'Verifying' if verify_only else 'Applied and verifying'} cutover toggles for East Gate (scheme_id={SCHEME_ID}):\n")
    failures = 0
    for key in TOGGLES:
        val = await pg.fetchval(
            "SELECT core.feature_toggle_resolved($1, $2)", SCHEME_ID, key
        )
        mark = "✅" if val else "❌"
        if not val:
            failures += 1
        print(f"  {mark}  {key}")

    print()
    if failures == 0:
        print("  ✅  All cutover toggles enabled — East Gate is on Postgres.")
    else:
        print(f"  ❌  {failures} toggle(s) not enabled — check feature_toggle_overrides.")

    await pg.close()
    return 0 if failures == 0 else 1


def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/audits/east_gate_cutover_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="East Gate financial cutover toggle control")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify current state; do not write any overrides",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.verify_only)))


if __name__ == "__main__":
    main()
