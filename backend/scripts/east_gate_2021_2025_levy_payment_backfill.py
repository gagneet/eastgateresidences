#!/usr/bin/env python3
"""East Gate (13195) 2021-2025 historical levy-PAYMENT backfill.

Source: demo_bank_transactions (direction=credit, not archived). Per explicit,
repeated user direction (2026-08-01/02): Demo Bank transactions ARE the real
per-levy-per-lot dated transactional data for this building -- there are no
real historical bank records for Dec 2020-2025 and there never will be; when
a real bank connects, THOSE details get reconciled against this, not the
other way around.

Posting dates: computed from the building's own configured quarterly due-date
schedule (utils.finance_helpers.compute_period_due_dates), NOT the source
transaction's own effective_date/posted_date field. Confirmed live 2026-08-01:
a fraction of these records (independent of assumption_code -- annual_lump_sum,
half_yearly_lump_sum, late, arrears_catch_up all affected) carry batch-
generation-order dates that don't correspond to their own stated quarter label
-- e.g. TH087's 2024 "Q1" transaction is dated AFTER its "Q3" and "Q4"
transactions. Every record's own description states its real quarter number
and total period count verbatim ("Q2 2024 ... 4 quarterly period(s)"), which
is what this script parses and uses -- confirmed uniform across every
assumption_code sampled (all say "4 quarterly period(s)", matching this
building's actual levy_due_months=[3,6,9,12] configuration).

Not addressed here (separate, larger, not-yet-approved fix): 2021's real
billing cadence was half-yearly (2 periods), not quarterly (4) -- the
reconstruction generator's annual_levies.levy_frequency=None default bug
(found 2026-08-01, flagged not fixed) means even 2021's own transaction
descriptions say "4 quarterly period(s)". This script uses that stated period
count as-is per the user's specific ask (fix the DATE ordering, not the
period-count), and does not re-derive 2021's true half-yearly structure.

Posts via FinancialCoreService.record_historical_payment() -- the only
supported way to post into East Gate's closed 2021-2025 accounting periods
(2020 genesis and 2026 current are the only open periods). Dual control:
executed_by = System Financial Cutover, approved_by = Gagneet Singh (ec_member)
-- the same two identities already used by
scripts/east_gate_historical_expense_posting.py.

Usage:
    cd backend
    venv/bin/python3 scripts/east_gate_2021_2025_levy_payment_backfill.py                 # dry-run (default)
    venv/bin/python3 scripts/east_gate_2021_2025_levy_payment_backfill.py --apply
    venv/bin/python3 scripts/east_gate_2021_2025_levy_payment_backfill.py --apply --year 2021
    venv/bin/python3 scripts/east_gate_2021_2025_levy_payment_backfill.py --apply --limit 20
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import text  # noqa: E402

from database import db as mongo_db  # noqa: E402
from db_postgres.repos.config_repo import resolve_scheme_context  # noqa: E402
from db_postgres.repos.ownership_repo import get_lot_id_by_number  # noqa: E402
from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from request_context import set_ctx_building_id  # noqa: E402
from services.financial_core import get_financial_core_service  # noqa: E402
from services.financial_core.domain.entities import (  # noqa: E402
    HistoricalPostingAuthorisation,
    PaymentChannel,
    RecordHistoricalPaymentCommand,
    SchemeRef,
)
from utils.finance_helpers import compute_period_due_dates  # noqa: E402

_BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_DIR / ".env")

BUILDING_ID = "13195"
YEARS = ["2021", "2022", "2023", "2024", "2025"]

set_ctx_building_id(BUILDING_ID)

APPROVED_BY = uuid.UUID("85c9e3bc-9bca-5f77-b434-a732a6069b39")  # Gagneet Singh, ec_member
EXECUTED_BY = uuid.UUID("5dc9c793-df7d-5239-b87e-967c5442e114")  # System Financial Cutover

REASON = (
    "East Gate 2021-2025 historical levy-payment backfill: Demo Bank credit "
    "transactions treated as the real per-lot per-period transactional record "
    "per explicit user direction (no real bank feed exists or will exist for "
    "this period); dates re-derived from each transaction's own stated quarter "
    "label mapped to the building's real configured due-date schedule, not the "
    "source record's own (confirmed unreliable) effective_date field."
)

_QUARTER_RE = re.compile(r"\bQ(\d+)\s+(\d{4})\b")
_TOTAL_PERIODS_RE = re.compile(r"(\d+)\s+quarterly period")


def _parse_period(description: str) -> tuple[int, int] | None:
    """Extract (quarter_number, total_periods) from a Demo Bank transaction's
    own description text. Returns None if either cannot be parsed -- callers
    must skip and report, never guess."""
    q_match = _QUARTER_RE.search(description or "")
    n_match = _TOTAL_PERIODS_RE.search(description or "")
    if not q_match or not n_match:
        return None
    return int(q_match.group(1)), int(n_match.group(1))


async def _get_settings() -> dict:
    doc = await mongo_db.settings.find_one(
        {
            "building_id": BUILDING_ID,
            "$or": [{"type": {"$exists": False}}, {"type": None}, {"type": "general"}],
        },
        {"_id": 0},
    )
    return doc or {}


async def main(apply: bool, only_year: str | None, limit: int | None):
    settings_doc = await _get_settings()
    due_months = settings_doc.get("levy_due_months") or [3, 6, 9, 12]
    due_day_type = settings_doc.get("levy_due_day_type") or "custom"
    due_day = settings_doc.get("levy_due_day")
    custom_dates = settings_doc.get("levy_due_custom_dates") or {}
    fy_start_month = int(settings_doc.get("financial_year_start_month") or 1)

    years = [only_year] if only_year else YEARS

    candidates = []
    unparseable = []
    for year in years:
        year_int = int(year)
        cats_query = {
            "building_id": BUILDING_ID,
            "strata_year": year,
            "direction": "credit",
            "is_archived": {"$ne": True},
        }
        async for tx in mongo_db.demo_bank_transactions.find(cats_query):
            parsed = _parse_period(tx.get("description", ""))
            if not parsed:
                unparseable.append(tx["_id"])
                continue
            quarter_num, total_periods = parsed
            due_dates = compute_period_due_dates(
                year_int, due_months, due_day_type, due_day, total_periods,
                custom_dates, fy_start_month=fy_start_month,
            )
            if quarter_num < 1 or quarter_num > len(due_dates):
                unparseable.append(tx["_id"])
                continue
            received_on = date.fromisoformat(due_dates[quarter_num - 1])
            candidates.append({
                "tx": tx,
                "year": year,
                "quarter_num": quarter_num,
                "total_periods": total_periods,
                "received_on": received_on,
            })

    total_cents = sum(c["tx"]["amount_cents"] for c in candidates)
    print(f"Parsed {len(candidates)} candidate payments across {years}, "
          f"total ${total_cents / 100:,.2f}")
    print(f"Unparseable (skipped): {len(unparseable)}")
    if candidates:
        sample = candidates[0]
        print(f"Sample: unit={sample['tx']['unit_number']} year={sample['year']} "
              f"Q{sample['quarter_num']}/{sample['total_periods']} "
              f"amount=${sample['tx']['amount_cents']/100:,.2f} "
              f"received_on={sample['received_on']}")

    if not apply:
        print("\nDry run -- not posting anything. Re-run with --apply to post.")
        return

    scheme = await resolve_scheme_context(BUILDING_ID)
    scheme_ref = SchemeRef(tenant_id=scheme["tenant_id"], scheme_id=scheme["scheme_id"])

    target = candidates[:limit] if limit else candidates
    posted, failed = 0, []
    lot_id_cache: dict[str, uuid.UUID] = {}

    # One fresh session PER ITEM, not one shared session for the whole run --
    # a single duplicate-key error (e.g. re-running after a partial prior run)
    # poisons a SQLAlchemy session's transaction for every statement after it
    # (PendingRollbackError) unless explicitly rolled back, which would falsely
    # report every subsequent genuinely-new item as failed. Matches
    # _post_payment_to_ledger()'s own per-call session pattern in
    # financial_matching.py -- proven safe for exactly this kind of batch loop.
    for i, c in enumerate(target, 1):
        tx = c["tx"]
        unit_number = tx["unit_number"]
        try:
            async with async_session_context() as session:
                await set_tenant(session, str(scheme["tenant_id"]))
                svc = get_financial_core_service(session)

                if unit_number not in lot_id_cache:
                    pg_lot_id_str = await get_lot_id_by_number(session, str(scheme["scheme_id"]), unit_number)
                    if not pg_lot_id_str:
                        raise RuntimeError(f"lot/unit_number={unit_number} not found in core.lots")
                    lot_id_cache[unit_number] = uuid.UUID(pg_lot_id_str)
                pg_lot_id = lot_id_cache[unit_number]

                owner_result = await session.execute(
                    text(
                        "SELECT owner_party_id::text FROM core.ownership_periods "
                        "WHERE lot_id = :lot_id AND is_primary_owner = TRUE "
                        "AND recorded_to IS NULL "
                        "AND valid_from <= :received_on "
                        "AND (valid_to IS NULL OR valid_to >= :received_on) "
                        "LIMIT 1"
                    ),
                    {"lot_id": str(pg_lot_id), "received_on": c["received_on"]},
                )
                owner_row = owner_result.first()
                if not owner_row:
                    raise RuntimeError(
                        f"no primary owner found for lot_number={unit_number} as of {c['received_on']}"
                    )
                payer_party_id = uuid.UUID(owner_row.owner_party_id)

                reconstruction_batch_id = None
                raw_batch_id = tx.get("reconstruction_batch_id")
                if raw_batch_id:
                    try:
                        reconstruction_batch_id = uuid.UUID(str(raw_batch_id))
                    except ValueError:
                        reconstruction_batch_id = None

                authorisation = HistoricalPostingAuthorisation(
                    reconstruction_batch_id=reconstruction_batch_id or uuid.uuid4(),
                    reason=REASON,
                    approved_by=APPROVED_BY,
                    executed_by=EXECUTED_BY,
                    approval_reference="east-gate-2021-2025-levy-payment-backfill",
                    evidence_reference=str(tx.get("source_batch_id") or tx.get("_id")),
                )
                cmd = RecordHistoricalPaymentCommand(
                    scheme_ref=scheme_ref,
                    payer_party_id=payer_party_id,
                    lot_id=pg_lot_id,
                    channel=PaymentChannel.BANK_TRANSFER,
                    received_on=c["received_on"],
                    amount_cents=tx["amount_cents"],
                    authorisation=authorisation,
                    external_reference=tx.get("external_transaction_id"),
                    idempotency_key=f"east-gate-historical-payment:{tx['_id']}",
                    is_test_data=False,
                    metadata={
                        "source": "demo_bank_transactions",
                        "source_doc_id": str(tx["_id"]),
                        "quarter": f"Q{c['quarter_num']}",
                        "strata_year": c["year"],
                    },
                )
                receipt = await svc.record_historical_payment(cmd)
                posted += 1
                if i % 50 == 0 or i == len(target):
                    print(f"  [{i}/{len(target)}] posted, receipt={receipt.receipt_id}")
        except Exception as e:
            failed.append((str(tx["_id"]), unit_number, c["year"], c["quarter_num"], str(e)))
            print(f"  [{i}/{len(target)}] FAILED unit={unit_number} year={c['year']} "
                  f"Q{c['quarter_num']}: {e}")

    print(f"\nDone. Posted: {posted}/{len(target)}. Failed: {len(failed)}")
    if failed:
        print("First 10 failures:")
        for doc_id, unit_number, year, q, err in failed[:10]:
            print(f"  {doc_id} unit={unit_number} year={year} Q{q}: {err}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--year", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(main(args.apply, args.year, args.limit))
