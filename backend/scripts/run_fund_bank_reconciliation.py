#!/usr/bin/env python3
"""Building-agnostic fund-level bank-reconciliation bridge builder (GAP-ONBOARD-004 A3).

WHAT IT DOES:
  For a building's Admin and Sinking funds, computes the GL-computed side of the
  opening -> movements -> closing bridge (opening_balance_cents, levied_cents,
  received_cents, expense_cents, interest_cents, adjustment_cents, computed_closing_cents)
  for one or more financial years, and UPSERTs the result into
  finance.fund_bank_reconciliations. See services/finance_metrics/fund_bank_reconciliation_
  engine.py for the full arithmetic + attribution rules.

WHAT IT DOES NOT DO:
  * It never touches external_balance_cents/variance_cents/status beyond leaving them
    NULL/'unreconciled' -- there is no external Bank/Trust anchor wired to this engine yet
    (that is B2b, a separate task). "Missing" here means "no external evidence yet", not "$0".
  * It never posts to the GL and never calls financial_core -- this table is a reconciliation
    CONTROL (a comparison record), not a journal source (contract rule 43).
  * It never mutates levy_items/receipts/expense_transactions -- read-only against those.

SAFETY:
  * Dry-run by DEFAULT (prints the computed bridge per fund/year, writes nothing).
  * --apply required to UPSERT into finance.fund_bank_reconciliations.
  * Idempotent: the table's (scheme_id, fund_id, financial_year, period_label) unique
    constraint makes a re-run UPSERT to the same values, never duplicate.
  * Because this is additive/read-derived and never posts a journal or mutates any other
    table, no dual-control (--authorised-by/--executed-by) gate is required here, unlike
    scripts that post to the GL (e.g. reconcile_portal_ledger.py).

Usage (from backend/):
    venv/bin/python3 scripts/run_fund_bank_reconciliation.py --building-id 13195 \
        --from-year 2021 --to-year 2026                      # dry-run
    venv/bin/python3 scripts/run_fund_bank_reconciliation.py --building-id 13195 \
        --from-year 2021 --to-year 2026 --apply              # writes
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from db_postgres.repos.config_repo import resolve_scheme_context  # noqa: E402
from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from services.finance_metrics.fund_bank_reconciliation_engine import (  # noqa: E402
    compute_fund_bank_reconciliation,
    persist_fund_bank_reconciliation,
)


def parse_args() -> argparse.Namespace:
    """Parse --building-id/--from-year/--to-year/--apply (see module docstring for the full contract)."""
    p = argparse.ArgumentParser(description="Build the fund-level bank-reconciliation bridge for a building.")
    p.add_argument("--building-id", required=True, help="Building/scheme number (no default).")
    p.add_argument("--from-year", required=True, type=int)
    p.add_argument("--to-year", required=True, type=int)
    p.add_argument("--apply", action="store_true", default=False, help="Write to finance.fund_bank_reconciliations (default: dry-run).")
    return p.parse_args()


async def main() -> None:
    """Resolve the building's PG scheme, then compute+print (and optionally persist) the
    fund-bank-reconciliation bridge for each year in [--from-year, --to-year], ascending."""
    args = parse_args()
    dry_run = not args.apply

    scheme = await resolve_scheme_context(args.building_id)
    if not scheme:
        print(f"No PostgreSQL scheme found for building_id={args.building_id!r} -- nothing to do.")
        return
    scheme_id = str(scheme["scheme_id"])
    tenant_id = str(scheme["tenant_id"])

    print(f"Building        : {args.building_id} (scheme_id={scheme_id})")
    print(f"Years           : {args.from_year}..{args.to_year}")
    print(f"Mode            : {'DRY-RUN' if dry_run else '*** APPLY -- WILL WRITE fund_bank_reconciliations ***'}")
    print()

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)

        # Years must be processed in ascending order so each year's opening can roll forward
        # from the prior year's just-computed closing. In dry-run nothing is persisted between
        # years, so prior_closing_override carries each year's in-memory result forward to the
        # next -- without it every year past the first would wrongly read back as "genesis $0"
        # in a multi-year preview, even though --apply would chain them correctly via the DB.
        prior_closing_override: dict[str, int] = {}
        for year in range(args.from_year, args.to_year + 1):
            lines = await compute_fund_bank_reconciliation(
                session, scheme_id=scheme_id, tenant_id=tenant_id, financial_year=str(year),
                prior_closing_override=prior_closing_override,
            )
            prior_closing_override = {line.fund_id: line.computed_closing_cents for line in lines}
            for line in lines:
                variance_note = ""
                if line.unattributed_adjustment_cents:
                    variance_note += f" [unattributed_adjustment=${line.unattributed_adjustment_cents / 100:.2f}]"
                if line.receipt_allocation_gap_cents:
                    variance_note += f" [receipt_allocation_gap=${line.receipt_allocation_gap_cents / 100:.2f}]"
                print(
                    f"  FY{line.financial_year} {line.fund_type:<8} "
                    f"opening=${line.opening_balance_cents / 100:>10.2f} ({line.opening_provenance:<15}) "
                    f"received=${line.received_cents / 100:>10.2f} "
                    f"expense=${line.expense_cents / 100:>10.2f} "
                    f"closing=${line.computed_closing_cents / 100:>10.2f}"
                    f"{variance_note}"
                )
            if not dry_run:
                ids = await persist_fund_bank_reconciliation(session, lines, tenant_id=tenant_id)
                await session.commit()
                # RLS footgun (CLAUDE.md): SET LOCAL app.tenant_id is scoped to the transaction
                # that just ended with commit() -- the next statement runs in a fresh
                # transaction with NO tenant context, so every RLS-scoped query would silently
                # return zero rows (not an error) for the rest of the loop unless re-set here.
                await set_tenant(session, tenant_id)
                print(f"    -> upserted {len(ids)} row(s) for FY{year}")

    print()
    print("DRY-RUN: no changes written. Re-run with --apply to persist." if dry_run else "Applied.")


if __name__ == "__main__":
    asyncio.run(main())
