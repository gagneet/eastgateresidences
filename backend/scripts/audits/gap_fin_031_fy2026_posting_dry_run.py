#!/usr/bin/env python3
"""
GAP-FIN-031 (Section 3a) — dry-run allocation report for posting FY2026 (or any
levy year) bank_transactions into finance.receipts.

READ-ONLY. No writes, no ledger posting. Building-generic (takes --building-id,
--year), matches the per-building levy-year boundary via the same settings the
live routes use (financial_year_start_month, levy_collection_frequency,
levy_due_months/day_type/custom_dates) — never assumes calendar-year or
East-Gate-specific config.

For every FY-year `finance.bank_transactions` row with no matching
`finance.receipts` row (by bank_transaction_id or legacy external_reference),
classifies it into exactly one bucket:

  - eligible          : lot resolves in core.lots, amount is positive, and the
                         levy period the row's description names (extracted via
                         "Q<n> <year>") has a due date on or before today. Safe
                         to post now via the sanctioned FinancialCoreService path.
  - not_yet_due        : the named period's due date is in the future relative to
                         today. EXCLUDED from posting per the standing "no future
                         dates or data, unless explicitly informed" rule — this
                         is not a data-quality problem, it's money for an
                         obligation that hasn't come due yet.
  - unmatched_no_lot   : unit_number on the row does not resolve to a core.lots
                         row for this scheme — cannot determine who to credit.
  - unparseable_period : description doesn't match the expected "Q<n> <year>"
                         pattern — cannot determine due-date eligibility, held
                         out of the eligible bucket rather than guessed.

Also computes, per lot, the resulting `paid_this_year`-equivalent figure if the
`eligible` bucket were posted on top of what's already receipted for that lot
this year, and flags any lot where that would exceed the lot's annual levied
total (finance.levy_items) — this is the "overpayments/credits ... flagged for
human review" bucket from the original GAP-FIN-031 ask, not a blocking error.

Usage:
    backend/venv/bin/python3 backend/scripts/audits/gap_fin_031_fy2026_posting_dry_run.py \
        --building-id 13195 --year 2026 [--out report.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import sqlalchemy as sa

_PERIOD_RE = re.compile(r"\bQ(\d+)\s+(\d{4})\b")


async def _load_building_settings(building_id: str) -> dict:
    from database import db
    from request_context import set_ctx_building_id

    set_ctx_building_id(building_id)
    settings_doc = await db.settings.find_one(
        {
            "building_id": building_id,
            "$or": [{"type": {"$exists": False}}, {"type": None}, {"type": "general"}],
        },
        {"_id": 0},
        sort=[("updated_at", -1), ("created_at", -1), ("_id", -1)],
    )
    return settings_doc or {}


def _num_periods(levy_collection_frequency: str) -> int:
    return {"monthly": 12, "quarterly": 4, "half_yearly": 2, "yearly": 1}.get(
        levy_collection_frequency, 4
    )


def _period_due_date(levy_year: int, quarter_num: int, due_dates: list[str]) -> date | None:
    """due_dates is the FY-ordered list from compute_period_due_dates(); quarter_num
    is the 1-based period number as it appears in the row's own "Q<n> <year>" label
    (which is the FY-relative period index, not necessarily the calendar quarter)."""
    idx = quarter_num - 1
    if 0 <= idx < len(due_dates):
        try:
            return date.fromisoformat(str(due_dates[idx])[:10])
        except (ValueError, TypeError):
            return None
    return None


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--building-id", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--out", default=None, help="Optional path to write full JSON report")
    args = parser.parse_args()

    from utils.finance_helpers import compute_period_due_dates
    from db_postgres.repos import config_repo
    from db_postgres.session import async_session_context, set_tenant
    from db_postgres.repos.ownership_repo import get_lot_id_by_number

    settings_doc = await _load_building_settings(args.building_id)
    fy_start_month = int(settings_doc.get("financial_year_start_month", 1))
    levy_collection_frequency = settings_doc.get("levy_collection_frequency", "quarterly")
    num_periods = _num_periods(levy_collection_frequency)
    levy_due_months = settings_doc.get("levy_due_months") or []
    levy_due_day_type = settings_doc.get("levy_due_day_type") or "first"
    levy_due_day = settings_doc.get("levy_due_day")
    levy_due_custom_dates = settings_doc.get("levy_due_custom_dates")

    today = date.today()

    scheme = await config_repo.resolve_scheme_context(args.building_id)
    if scheme is None:
        print(json.dumps({"error": f"no Postgres scheme for building_id={args.building_id}"}))
        return

    buckets = {
        "eligible": [],
        "not_yet_due": [],
        "unmatched_no_lot": [],
        "unparseable_period": [],
        # Rows labeled for a PRIOR levy year (e.g. "Q4 2025" arrears catch-up,
        # dated in 2026) are out of scope for a --year 2026 pass: 2021-2025 is a
        # separately reconciled, already-human-reviewed domain per standing
        # instruction — do not redo it. If FY2025 genuinely has an unresolved gap,
        # that is a distinct, deliberate decision for a --year 2025 pass, not an
        # automatic sweep-in here. This is what the overpayment_review flag below
        # was catching in an early run of this script: several lots' Mongo
        # opening_arrears for the target year is $0.00 (2025 already closed clean)
        # while a "Q4 2025 catch-up" row dated in 2026 would still double-credit it.
        "prior_year_out_of_scope": [],
    }
    per_lot_eligible_cents: dict[str, int] = {}
    per_lot_already_receipted_cents: dict[str, int] = {}
    per_lot_levied_cents: dict[str, int] = {}
    unit_to_lot_cache: dict[str, str | None] = {}

    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))

        # Due-date cache per FY-year label seen in descriptions (may span the
        # prior FY for arrears-catch-up rows, e.g. "Q4 2025" seen inside a 2026 run).
        due_dates_by_levy_year: dict[int, list[str]] = {}

        rows_result = await session.execute(
            sa.text(
                """
                SELECT bt.bank_transaction_id, bt.transaction_date, bt.amount_cents,
                       bt.unit_number, bt.description, bt.reconstruction_batch_id
                FROM finance.bank_transactions bt
                WHERE bt.scheme_id = :scheme_id
                  AND bt.transaction_date >= CAST(:year || '-01-01' AS date)
                  AND bt.transaction_date < CAST((CAST(:year AS int) + 1) || '-01-01' AS date)
                  AND bt.amount_cents > 0
                  AND NOT EXISTS (
                      SELECT 1 FROM finance.receipts r
                      WHERE r.scheme_id = bt.scheme_id
                        AND (r.bank_transaction_id = bt.bank_transaction_id
                             OR r.external_reference = CAST(bt.bank_transaction_id AS text))
                  )
                ORDER BY bt.unit_number, bt.transaction_date
                """
            ),
            {"scheme_id": scheme["scheme_id"], "year": args.year},
        )
        rows = rows_result.mappings().all()

        for row in rows:
            row_out = {
                "bank_transaction_id": str(row["bank_transaction_id"]),
                "transaction_date": row["transaction_date"].isoformat(),
                "amount_cents": row["amount_cents"],
                "unit_number": row["unit_number"],
                "reconstruction_batch_id": str(row["reconstruction_batch_id"]) if row["reconstruction_batch_id"] else None,
            }

            m = _PERIOD_RE.search(row["description"] or "")
            if not m:
                buckets["unparseable_period"].append(row_out)
                continue
            period_num, period_year = int(m.group(1)), int(m.group(2))
            row_out["period"] = f"Q{period_num} {period_year}"

            if period_year != int(args.year):
                buckets["prior_year_out_of_scope"].append(row_out)
                continue

            if period_year not in due_dates_by_levy_year:
                due_dates_by_levy_year[period_year] = compute_period_due_dates(
                    period_year, levy_due_months, levy_due_day_type, levy_due_day,
                    num_periods, levy_due_custom_dates, fy_start_month=fy_start_month,
                )
            due_date = _period_due_date(period_year, period_num, due_dates_by_levy_year[period_year])
            row_out["period_due_date"] = due_date.isoformat() if due_date else None

            if due_date is None:
                buckets["unparseable_period"].append(row_out)
                continue
            if due_date > today:
                buckets["not_yet_due"].append(row_out)
                continue

            unit_number = row["unit_number"]
            if unit_number not in unit_to_lot_cache:
                unit_to_lot_cache[unit_number] = await get_lot_id_by_number(
                    session, str(scheme["scheme_id"]), unit_number
                ) if unit_number else None
            lot_id = unit_to_lot_cache[unit_number]
            if not lot_id:
                buckets["unmatched_no_lot"].append(row_out)
                continue

            buckets["eligible"].append(row_out)
            per_lot_eligible_cents[unit_number] = per_lot_eligible_cents.get(unit_number, 0) + row["amount_cents"]

        # Already-receipted (the 40 already posted) + annual levied, per lot, for
        # the overpayment-review flag.
        already_result = await session.execute(
            sa.text(
                """
                SELECT l.unit_number, sum(r.amount_cents) AS receipted_cents
                FROM finance.receipts r
                JOIN core.lots l ON l.lot_id = r.lot_id
                WHERE r.scheme_id = :scheme_id
                  AND r.channel = 'bank_transfer'
                  AND r.received_on >= CAST(:year || '-01-01' AS date)
                  AND r.received_on < CAST((CAST(:year AS int) + 1) || '-01-01' AS date)
                GROUP BY l.unit_number
                """
            ),
            {"scheme_id": scheme["scheme_id"], "year": args.year},
        )
        for r in already_result.mappings():
            per_lot_already_receipted_cents[r["unit_number"]] = int(r["receipted_cents"] or 0)

        levied_result = await session.execute(
            sa.text(
                """
                SELECT l.unit_number,
                       sum(li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents) AS levied_cents
                FROM finance.levy_items li
                JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                JOIN core.lots l ON l.lot_id = li.lot_id
                WHERE li.scheme_id = :scheme_id AND lr.financial_year = :year
                GROUP BY l.unit_number
                """
            ),
            {"scheme_id": scheme["scheme_id"], "year": args.year},
        )
        for r in levied_result.mappings():
            per_lot_levied_cents[r["unit_number"]] = int(r["levied_cents"] or 0)

    overpayment_review = []
    for unit_number, eligible_cents in per_lot_eligible_cents.items():
        resulting_cents = eligible_cents + per_lot_already_receipted_cents.get(unit_number, 0)
        levied_cents = per_lot_levied_cents.get(unit_number, 0)
        if levied_cents and resulting_cents > levied_cents:
            overpayment_review.append({
                "unit_number": unit_number,
                "resulting_paid_cents": resulting_cents,
                "levied_cents": levied_cents,
                "excess_cents": resulting_cents - levied_cents,
            })

    summary = {
        "building_id": args.building_id,
        "year": args.year,
        "as_of": today.isoformat(),
        "levy_collection_frequency": levy_collection_frequency,
        "counts": {k: len(v) for k, v in buckets.items()},
        "amounts_cents": {
            k: sum(r["amount_cents"] for r in v) for k, v in buckets.items()
        },
        "overpayment_review_count": len(overpayment_review),
        "overpayment_review_sample": overpayment_review[:20],
    }
    print(json.dumps(summary, indent=2))

    if args.out:
        full = dict(summary)
        full["buckets"] = buckets
        full["overpayment_review"] = overpayment_review
        Path(args.out).write_text(json.dumps(full, indent=2, default=str))
        print(f"\nFull report written to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
