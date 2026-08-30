#!/usr/bin/env python3
"""East Gate (13195) 2026 Q1/Q2 historical levy-PAYMENT backfill.

GAP-FIN-035 Item 1 — 2026's Demo Bank transactions must get the same
itemized-payment treatment 2021-2025 already got (see
east_gate_2021_2025_levy_payment_backfill.py), instead of the current
single back-solved unit_levy_ledger figure (reconciliation_source:
"strata_web_portal_scrape"). This script does NOT touch or remove that
portal-scrape reconciliation — it stays as the verification signal
(compare itemized total vs live portal balance), not the source.

Unlike 2021-2025, 2026 is an OPEN accounting period (the current levy
year), not closed — so this posts via the ordinary
FinancialCoreService.record_payment() / RecordPaymentCommand path, NOT
record_historical_payment() (which is reserved for closed periods and
requires a HistoricalPostingAuthorisation record_payment() does not need).

Dedup safeguard (mandatory, not optional): this exact pathway has caused
THREE real duplicate-posting incidents for East Gate FY2026 in the past
week (a $1,769,655.36 duplicate GL credit; 22 duplicate receipt postings
from a bank_transaction_id-only exact-match check that missed a real
mislabeled-duplicate source row; 20 pre-existing bad future-dated
receipts). Before posting ANY row, this script re-runs the exact
multi-signal dedup check from
scripts/audits/gap_fin_035_2026_backfill_dedup_check.py (bank_transaction_id
column, external_reference in either UUID or "gap_fin_031_derived:<uuid>"
form, and journal_entries.idempotency_key in "gap-fin-031-derived:<uuid>"
form) and skips any Mongo doc where ANY linked Postgres bank_transaction_id
already matches one of those signals. A doc is only posted if NONE of its
linked bank_transaction_id rows show any prior posting signal.

Usage:
    cd backend
    venv/bin/python3 scripts/east_gate_2026_levy_payment_backfill.py                 # dry-run (default)
    venv/bin/python3 scripts/east_gate_2026_levy_payment_backfill.py --apply
    venv/bin/python3 scripts/east_gate_2026_levy_payment_backfill.py --apply --limit 20
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
    PaymentChannel,
    RecordPaymentCommand,
    SchemeRef,
)
from utils.finance_helpers import compute_period_due_dates  # noqa: E402

_BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_DIR / ".env")

BUILDING_ID = "13195"
YEAR = "2026"

set_ctx_building_id(BUILDING_ID)

APPROVED_BY = uuid.UUID("85c9e3bc-9bca-5f77-b434-a732a6069b39")  # Gagneet Singh, ec_member
POSTED_BY = uuid.UUID("5dc9c793-df7d-5239-b87e-967c5442e114")  # System Financial Cutover

_QUARTER_RE = re.compile(r"\bQ(\d+)\s+(\d{4})\b")
_TOTAL_PERIODS_RE = re.compile(r"(\d+)\s+quarterly period")


def _parse_period(description: str) -> tuple[int, int] | None:
    """Extract (quarter_number, total_periods) from a Demo Bank transaction's
    own description text. Returns None if either cannot be parsed -- callers
    must skip and report, never guess. Same logic as the 2021-2025 script."""
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


async def _find_already_posted_bank_transaction_ids(session, all_uuids: list[str]) -> set[str]:
    """Mirror of gap_fin_035_2026_backfill_dedup_check.py's multi-signal
    dedup query -- checked live here, not assumed from a prior run, since
    new postings can land between the dry-run check and this script running.
    """
    if not all_uuids:
        return set()
    result = await session.execute(
        text("""
            SELECT
                bt.bank_transaction_id::text AS bank_transaction_id,
                r.receipt_id IS NOT NULL AS has_receipt_by_bank_tx_id,
                r2.receipt_id IS NOT NULL AS has_receipt_by_external_ref_uuid,
                r3.receipt_id IS NOT NULL AS has_receipt_by_gap_fin_031_prefix,
                je.journal_entry_id IS NOT NULL AS has_journal_by_idempotency_key
            FROM finance.bank_transactions bt
            LEFT JOIN finance.receipts r
                ON r.bank_transaction_id = bt.bank_transaction_id
            LEFT JOIN finance.receipts r2
                ON r2.external_reference = bt.bank_transaction_id::text
            LEFT JOIN finance.receipts r3
                ON r3.external_reference = 'gap_fin_031_derived:' || bt.bank_transaction_id::text
            LEFT JOIN finance.journal_entries je
                ON je.idempotency_key = 'gap-fin-031-derived:' || bt.bank_transaction_id::text
            WHERE bt.bank_transaction_id = ANY(:uuids)
        """),
        {"uuids": all_uuids},
    )
    posted = set()
    for row in result.mappings().all():
        if (
            row["has_receipt_by_bank_tx_id"]
            or row["has_receipt_by_external_ref_uuid"]
            or row["has_receipt_by_gap_fin_031_prefix"]
            or row["has_journal_by_idempotency_key"]
        ):
            posted.add(row["bank_transaction_id"])
    return posted


async def main(apply: bool, limit: int | None):
    settings_doc = await _get_settings()
    due_months = settings_doc.get("levy_due_months") or [3, 6, 9, 12]
    due_day_type = settings_doc.get("levy_due_day_type") or "custom"
    due_day = settings_doc.get("levy_due_day")
    custom_dates = settings_doc.get("levy_due_custom_dates") or {}
    fy_start_month = int(settings_doc.get("financial_year_start_month") or 1)
    year_int = int(YEAR)

    mongo_docs = await mongo_db._db.demo_bank_transactions.find(
        {
            "building_id": BUILDING_ID,
            "strata_year": YEAR,
            "direction": "credit",
            "is_archived": {"$ne": True},
            "is_test_data": {"$ne": True},
        }
    ).to_list(2000)

    scheme = await resolve_scheme_context(BUILDING_ID)
    if not scheme:
        print(f"No Postgres scheme found for building_id={BUILDING_ID} -- aborting.")
        return
    scheme_ref = SchemeRef(tenant_id=scheme["tenant_id"], scheme_id=scheme["scheme_id"])

    # Live dedup check -- mandatory, not skippable. Explode each doc's
    # comma-separated finance_bank_transaction_ref, check every linked
    # Postgres UUID, and only proceed for docs where NONE are already posted.
    all_uuids: list[str] = []
    doc_to_uuids: dict[str, list[str]] = {}
    for d in mongo_docs:
        raw_ref = d.get("finance_bank_transaction_ref") or ""
        uuids = [u.strip() for u in str(raw_ref).split(",") if u.strip()]
        doc_to_uuids[str(d["_id"])] = uuids
        all_uuids.extend(uuids)

    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))
        already_posted_uuids = await _find_already_posted_bank_transaction_ids(session, all_uuids)

    today = date.today()
    candidates = []
    skipped_already_posted = []
    skipped_unparseable = []
    skipped_partial = []
    skipped_not_yet_due = []
    for d in mongo_docs:
        doc_id = str(d["_id"])
        linked_uuids = doc_to_uuids.get(doc_id, [])
        posted_count = sum(1 for u in linked_uuids if u in already_posted_uuids)
        if linked_uuids and posted_count == len(linked_uuids):
            skipped_already_posted.append(doc_id)
            continue
        if linked_uuids and 0 < posted_count < len(linked_uuids):
            # Mixed state -- a real data-quality flag, never auto-post the
            # unposted remainder without a human decision first.
            skipped_partial.append(doc_id)
            continue

        parsed = _parse_period(d.get("description", ""))
        if not parsed:
            skipped_unparseable.append(doc_id)
            continue
        quarter_num, total_periods = parsed
        due_dates = compute_period_due_dates(
            year_int, due_months, due_day_type, due_day, total_periods,
            custom_dates, fy_start_month=fy_start_month,
        )
        if quarter_num < 1 or quarter_num > len(due_dates):
            skipped_unparseable.append(doc_id)
            continue
        received_on = date.fromisoformat(due_dates[quarter_num - 1])
        if received_on > today:
            # No future-dated postings, ever, without explicit instruction
            # (GAP-FIN-033 standing rule). A period whose due date hasn't
            # arrived yet is not a real, confirmed obligation to backfill --
            # posting it now would also directly recreate the exact
            # advance-payment-before-due-date problem GAP-FIN-035 Item 5 was
            # built to stop counting as "collected."
            skipped_not_yet_due.append(str(d["_id"]))
            continue
        candidates.append({
            "tx": d,
            "quarter_num": quarter_num,
            "total_periods": total_periods,
            "received_on": received_on,
        })

    total_cents = sum(c["tx"]["amount_cents"] for c in candidates)
    print(f"Mongo demo_bank_transactions ({YEAR}, credit, not archived): {len(mongo_docs)} total")
    print(f"Already posted (skipped): {len(skipped_already_posted)}")
    print(f"Partially posted (skipped -- needs human review, not auto-posted): {len(skipped_partial)}")
    if skipped_partial:
        print(f"  sample: {skipped_partial[:5]}")
    print(f"Unparseable period (skipped): {len(skipped_unparseable)}")
    print(f"Not yet due as of {today} (skipped -- no future-dated postings): {len(skipped_not_yet_due)}")
    print(f"Candidates for posting: {len(candidates)}, total ${total_cents / 100:,.2f}")
    if candidates:
        sample = candidates[0]
        print(f"Sample: unit={sample['tx']['unit_number']} "
              f"Q{sample['quarter_num']}/{sample['total_periods']} "
              f"amount=${sample['tx']['amount_cents']/100:,.2f} "
              f"received_on={sample['received_on']}")

    if not apply:
        print("\nDry run -- not posting anything. Re-run with --apply to post.")
        return

    target = candidates[:limit] if limit else candidates
    posted, failed = 0, []
    lot_id_cache: dict[str, uuid.UUID] = {}

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

                cmd = RecordPaymentCommand(
                    scheme_ref=scheme_ref,
                    payer_party_id=payer_party_id,
                    lot_id=pg_lot_id,
                    channel=PaymentChannel.BANK_TRANSFER,
                    received_on=c["received_on"],
                    amount_cents=tx["amount_cents"],
                    external_reference=tx.get("external_transaction_id"),
                    idempotency_key=f"east-gate-2026-payment-backfill:{tx['_id']}",
                    is_test_data=False,
                    posted_by=POSTED_BY,
                    approved_by=APPROVED_BY,
                    reconstruction_batch_id=reconstruction_batch_id,
                    metadata={
                        "source": "demo_bank_transactions",
                        "source_doc_id": str(tx["_id"]),
                        "quarter": f"Q{c['quarter_num']}",
                        "strata_year": YEAR,
                        "backfill": "gap_fin_035_item_1",
                    },
                )
                receipt = await svc.record_payment(cmd)
                posted += 1
                if i % 25 == 0 or i == len(target):
                    print(f"  [{i}/{len(target)}] posted, receipt={receipt.receipt_id}")
        except Exception as e:
            failed.append((str(tx["_id"]), unit_number, c["quarter_num"], str(e)))
            print(f"  [{i}/{len(target)}] FAILED unit={unit_number} Q{c['quarter_num']}: {e}")

    print(f"\nDone. Posted: {posted}/{len(target)}. Failed: {len(failed)}")
    if failed:
        print("First 10 failures:")
        for doc_id, unit_number, q, err in failed[:10]:
            print(f"  {doc_id} unit={unit_number} Q{q}: {err}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(main(args.apply, args.limit))
