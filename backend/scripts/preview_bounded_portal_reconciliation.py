#!/usr/bin/env python3
"""Building-agnostic READ-ONLY preview of Workstream C's bounded portal reconciliation
classification (GAP-ONBOARD-004 A2).

WHAT IT DOES:
  For every unit with both a PG true-balance (services/finance_metrics/lot_true_balance.py,
  A1) and a staged portal snapshot balance, computes a per-unit "one period's levy" bound
  from finance.levy_items, then classifies the residual via
  services/reconstruction_generators/bounded_portal_reconciliation_engine.py::
  classify_bounded_adjustment() into reconciled / post_debit_adjustment / post_credit_receipt
  / halt_flag / no_portal_data. Prints the classification for every unit; never writes.

WHAT IT DOES NOT DO (by design, this script):
  It does NOT post anything. The two posting mechanisms this classifier's "post_*" decisions
  map to already exist and are proven (record_adjustment for the debit direction --
  scripts/data_repair/gap_fin_046_portal_evidenced_adjustment_20260806.py -- and
  record_historical_payment()+allocate_payment() for the credit direction -- see this
  session's docs/finances/GAP-FIN-036-A1-reconciliation-evidence-pack-2026-08-10.md). Wiring
  a generic poster that consumes this preview's "post_*" decisions and calls the appropriate
  command under dual control is the concrete next step, intentionally left as a separate,
  explicitly-scoped follow-up rather than bundled into this read-only preview.

Usage (from backend/):
    venv/bin/python3 scripts/preview_bounded_portal_reconciliation.py --building-id 13195 --year 2026
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import text  # noqa: E402

from database import db  # noqa: E402
from db_postgres.repos.config_repo import resolve_scheme_context  # noqa: E402
from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from services.finance_metrics.lot_true_balance import compute_lot_true_balances  # noqa: E402
from services.portal_ledger_reconciliation_service import _latest_portal_by_unit  # noqa: E402
from services.reconstruction_generators.bounded_portal_reconciliation_engine import (  # noqa: E402
    classify_bounded_adjustment,
)
from utils.unit_number import extract_lot_int  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse --building-id/--year/--bound-periods (see module docstring for the full contract)."""
    p = argparse.ArgumentParser(description="Read-only preview of bounded portal reconciliation classification.")
    p.add_argument("--building-id", required=True)
    p.add_argument("--year", required=True)
    p.add_argument("--bound-periods", type=float, default=1.0)
    return p.parse_args()


# Per-unit "one period's levy" bound: for each (lot, fund), average that fund's per-period
# charge for the year (total levied / number of levy_item rows, i.e. periods), then sum across
# funds -- a clean, defensible "one period's total levy across all funds" figure without needing
# to date-align periods across the admin and sinking funds.
#
# COALESCE(unit_number, lot_number): self-audit fix (2026-08-10) -- core.lots.unit_number IS
# NULLable at the schema level (unlike lot_number, which is NOT NULL). East Gate happens to have
# unit_number populated on every lot, but this script is explicitly building-agnostic (module
# docstring), and a lot with NULL unit_number would otherwise be keyed as the Python string
# "None" here (SQL COALESCE) while lot_to_unit below keys it as the Python value None -- a silent
# mismatch that always produces "no_portal_data" for that lot, hiding it from classification
# entirely rather than crashing loudly. Matches the same COALESCE-equivalent fallback pattern
# already established in lot_true_balance.py's _UNIT_OUTSTANDING_SQL.
_PERIOD_BOUND_SQL_SIMPLE = text(
    """
    SELECT l.lot_id::text AS lot_id, COALESCE(l.unit_number, l.lot_number) AS unit_number,
           SUM(fund_avg.avg_period_cents) AS period_bound_cents
    FROM (
        SELECT li.lot_id, li.fund_id,
               SUM(li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents)
               / GREATEST(COUNT(*), 1) AS avg_period_cents
        FROM finance.levy_items li
        JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
        WHERE li.scheme_id = CAST(:sid AS UUID) AND lr.financial_year = :fy
        GROUP BY li.lot_id, li.fund_id
    ) fund_avg
    JOIN core.lots l ON l.lot_id = fund_avg.lot_id
    GROUP BY l.lot_id, COALESCE(l.unit_number, l.lot_number)
    """
)


async def main() -> None:
    """Resolve the building's PG scheme + latest portal snapshot, compute each unit's PG true
    balance and one-period-levy bound, classify every unit via classify_bounded_adjustment(),
    and print only the non-reconciled/non-missing decisions (read-only; see module docstring)."""
    args = parse_args()

    scheme = await resolve_scheme_context(args.building_id)
    if not scheme:
        print(f"No PostgreSQL scheme found for building_id={args.building_id!r}.")
        return
    scheme_id = str(scheme["scheme_id"])
    tenant_id = str(scheme["tenant_id"])

    snapshot_date, portal_by_lot_int = await _latest_portal_by_unit(db, args.building_id, args.year)
    if snapshot_date is None:
        print(f"No staged portal snapshot for building_id={args.building_id!r} year={args.year!r}.")
        return

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)

        pg_balances = await compute_lot_true_balances(
            session, scheme_id=scheme_id, tenant_id=tenant_id, financial_year=args.year,
        )
        bound_rows = (
            await session.execute(_PERIOD_BOUND_SQL_SIMPLE, {"sid": scheme_id, "fy": args.year})
        ).fetchall()

    bound_by_unit = {row.unit_number: int(row.period_bound_cents or 0) for row in bound_rows}
    lot_to_unit: dict[str, str] = {}
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        rows = (
            await session.execute(
                text(
                    "SELECT lot_id::text, COALESCE(unit_number, lot_number) AS unit_number "
                    "FROM core.lots WHERE scheme_id = CAST(:sid AS UUID)"
                ),
                {"sid": scheme_id},
            )
        ).fetchall()
        lot_to_unit = {r.lot_id: r.unit_number for r in rows}

    print(f"Building        : {args.building_id} (scheme_id={scheme_id})")
    print(f"Year            : {args.year}")
    print(f"Portal snapshot : {snapshot_date}")
    print(f"Bound           : {args.bound_periods:g}x one-period levy")
    print()

    counts: dict[str, int] = {}
    for lot_id, unit_number in sorted(lot_to_unit.items(), key=lambda kv: kv[1]):
        pg_balance = pg_balances.get(lot_id)
        pg_cents = pg_balance.true_balance_cents if pg_balance is not None else None
        portal_cents = portal_by_lot_int.get(extract_lot_int(unit_number))
        bound_cents = bound_by_unit.get(unit_number)

        decision = classify_bounded_adjustment(
            unit_number=unit_number, pg_true_balance_cents=pg_cents,
            portal_balance_cents=portal_cents, period_bound_cents=bound_cents,
            bound_periods=args.bound_periods,
        )
        counts[decision.decision] = counts.get(decision.decision, 0) + 1
        if decision.decision != "reconciled" and decision.decision != "no_portal_data":
            print(f"  {unit_number:8} {decision.decision:<24} gap=${(decision.gap_cents or 0) / 100:>9,.2f}  {decision.reason}")

    print()
    print("Summary:", counts)
    print()
    print("READ-ONLY preview -- nothing posted. See module docstring for the posting follow-up.")


if __name__ == "__main__":
    asyncio.run(main())
