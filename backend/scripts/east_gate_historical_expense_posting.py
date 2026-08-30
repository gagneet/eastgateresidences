#!/usr/bin/env python3
"""Post East Gate's (13195) verified 2021-2026(partial) category-level expenses into
Postgres as historical journal entries, via FinancialCoreService.record_historical_expense().

Source: docs/finances/eastgate_20XX_category_detail.csv (expense rows only -- income is handled
at the fund level via the Mongo annual_levies import, not posted as individual GL journal entries
here). Every figure has already been hand-verified this session against a primary source document
(LJ Hooker's 2021 report, the Corver and Co 2022 audit, Civium's 2023/2024/2025/2026 statements).

Granularity: one journal entry per category per year -- coarser than a real per-invoice ledger
(we don't have receipt-level detail for 2021-2023), but honest: each entry is directly traceable
to its source category CSV, never fabricated at a finer grain than the evidence supports.

GST: category totals from the source PDFs are GST-inclusive with no per-category GST/ex-GST
split documented anywhere in this session's evidence. Rather than invent an unverified 10% split,
every entry posts gst_cents=0 and the full amount as amount_cents -- the cash total is correct;
the GST/ex-GST decomposition is left unresolved pending a real source breakdown (matches this
repo's GAP-FIN-025 guidance: an unknown GST split must stay unresolved, not be assumed).

Dual control: HistoricalPostingAuthorisation requires two distinct real users. executed_by is the
System Financial Cutover identity genesis already created; approved_by is Gagneet Singh's
ec_member account (confirmed with the user this session).

Usage:
    cd backend
    venv/bin/python3 scripts/east_gate_historical_expense_posting.py            # dry-run (default)
    venv/bin/python3 scripts/east_gate_historical_expense_posting.py --apply    # writes
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from services.financial_core.adapters.db_postgres.ledger_repo import (  # noqa: E402
    PostgresLedgerRepository,
)
from services.financial_core.adapters.db_postgres.outbox_repo import (  # noqa: E402
    PostgresOutboxRepository,
)
from services.financial_core.domain.entities import (  # noqa: E402
    EntryDirection,
    HistoricalPostingAuthorisation,
    JournalEntry,
    JournalLine,
    RecordHistoricalExpenseCommand,
    SchemeRef,
)
from services.financial_core.genesis import resolve_scheme_fund_ids  # noqa: E402
from services.financial_core.service import FinancialCoreService  # noqa: E402

BUILDING_ID = "13195"
_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"
DOCS_DIR = Path(__file__).resolve().parents[2] / "docs" / "finances"

APPROVED_BY = uuid.UUID("85c9e3bc-9bca-5f77-b434-a732a6069b39")  # Gagneet Singh, ec_member
EXECUTED_BY = uuid.UUID("5dc9c793-df7d-5239-b87e-967c5442e114")  # System Financial Cutover

REASON = (
    "East Gate 2021-2026 historical financial rebuild: post verified category-level annual "
    "expenditure into Postgres GL, per docs/finances/reconcilation-2021-2022-finances.md and "
    "the audited/final source PDFs for 2022-2025. Approved by EC member Gagneet Singh."
)

# One period per calendar year 2021-2026; 2021-2025 are genuinely closed years, 2026 is the
# current (partial) year -- matches genesis's own 'open' treatment of the 2020 bootstrap period.
PERIOD_STATUS_BY_YEAR = {2021: "closed", 2022: "closed", 2023: "closed", 2024: "closed", 2025: "closed", 2026: "open"}

# 2026 is partial -- post its expenses effective on the same 8 April cutoff used throughout this
# rebuild, never a later/inferred date.
EFFECTIVE_DATE_BY_YEAR = {
    2021: date(2021, 12, 31), 2022: date(2022, 12, 31), 2023: date(2023, 12, 31),
    2024: date(2024, 12, 31), 2025: date(2025, 12, 31), 2026: date(2026, 4, 8),
}

FUND_NAME_TO_TYPE = {"administrative": "admin", "sinking": "sinking"}


def _dollars_to_cents(amount: str) -> int:
    return int((Decimal(amount) * 100).to_integral_value())


def _load_expense_rows() -> tuple[list[dict], list[dict]]:
    """Returns (expense_rows, credit_rows). Credit/refund rows (negative actual_amount --
    e.g. a GST refund or a disbursement reversal) cannot be posted via
    RecordHistoricalExpenseCommand (amount_cents must be > 0 -- a credit is a different
    transaction shape, not "a negative expense"); they're posted separately as income/recovery
    entries via _post_income_recovery_entry so the year's net position still ties out exactly."""
    expense_rows, credit_rows = [], []
    for year in range(2021, 2027):
        with (DOCS_DIR / f"eastgate_{year}_category_detail.csv").open() as f:
            for r in csv.DictReader(f):
                if r["section"] != "expense":
                    continue
                amount_cents = _dollars_to_cents(r["actual_amount"])
                if amount_cents == 0:
                    continue
                row = {
                    "year": year,
                    "fund_type": FUND_NAME_TO_TYPE[r["fund"].strip().lower()],
                    "category_name": r["category"].strip(),
                    "amount_cents": abs(amount_cents),
                    "source_file": r["source_file"],
                }
                (expense_rows if amount_cents > 0 else credit_rows).append(row)
    return expense_rows, credit_rows


async def _resolve_scheme(session) -> tuple[str, str]:
    await session.execute(text(f"SELECT set_config('app.tenant_id', '{_TENANT_BYPASS_ID}', true)"))
    row = (
        await session.execute(
            text("SELECT tenant_id::text, scheme_id::text FROM core.schemes WHERE lower(scheme_number) = lower(:num)"),
            {"num": BUILDING_ID},
        )
    ).fetchone()
    if row is None:
        raise RuntimeError(f"No core.schemes row for building_id={BUILDING_ID!r}")
    return row[0], row[1]


# genesis.py's own DEFAULT_GL_ACCOUNTS only creates 5 codes (1000/1010/1100/3100/4000/4003) --
# East Gate's real established chart (confirmed this session against
# backend/scripts/backfill_east_gate_journal_fund_id.py and this repo's pre-wipe GL convention)
# has 11 more that were "never added here or to any migration" per genesis.py's own comment.
_MISSING_GL_ACCOUNTS = (
    ("1011", "Trust Bank Account - Capital Works", "asset"),
    ("1200", "GST Receivable", "asset"),
    ("2100", "Levy in Advance", "liability"),
    ("2200", "GST Payable", "liability"),
    ("4001", "Capital Works Levy Income", "income"),
    ("4002", "Interest Income", "income"),
    ("5000", "Administration Expenses", "expense"),
    ("5001", "Insurance", "expense"),
    ("5002", "Repairs and Maintenance", "expense"),
    ("5003", "Management Fees", "expense"),
    ("5004", "Legal and Compliance", "expense"),
)


async def _ensure_missing_gl_accounts(session, tenant_id: str, scheme_id: str) -> None:
    for account_code, account_name, account_type in _MISSING_GL_ACCOUNTS:
        await session.execute(
            text(
                """
                INSERT INTO finance.gl_accounts
                    (gl_account_id, tenant_id, scheme_id, account_code, account_name, account_type, is_control_account, status, created_at)
                SELECT :gl_account_id, :tenant_id, :scheme_id, :account_code, :account_name,
                       CAST(:account_type AS finance.account_type), FALSE,
                       CAST('active' AS core.record_status), NOW()
                WHERE NOT EXISTS (
                    SELECT 1 FROM finance.gl_accounts WHERE scheme_id = :scheme_id AND account_code = :account_code
                )
                """
            ),
            {
                "gl_account_id": str(uuid.uuid4()), "tenant_id": tenant_id, "scheme_id": scheme_id,
                "account_code": account_code, "account_name": account_name, "account_type": account_type,
            },
        )


async def _ensure_accounting_periods(session, tenant_id: str, scheme_id: str) -> None:
    for year, status in PERIOD_STATUS_BY_YEAR.items():
        await session.execute(
            text(
                """
                INSERT INTO finance.accounting_periods
                    (period_id, tenant_id, scheme_id, period_label, starts_on, ends_on, status, created_at)
                SELECT :period_id, :tenant_id, :scheme_id, :period_label, :starts_on, :ends_on, :status, NOW()
                WHERE NOT EXISTS (
                    SELECT 1 FROM finance.accounting_periods
                    WHERE scheme_id = :scheme_id AND period_label = :period_label
                )
                """
            ),
            {
                "period_id": str(uuid.uuid4()), "tenant_id": tenant_id, "scheme_id": scheme_id,
                "period_label": str(year), "starts_on": date(year, 1, 1), "ends_on": date(year, 12, 31),
                "status": status,
            },
        )


_RECOVERY_INCOME_GL_CODE = "4003"  # Recovered Fees & Collection Costs -- closest existing
# income account for a credit/refund with no dedicated GL code of its own in this chart.


async def _post_income_recovery_entry(
        ledger: PostgresLedgerRepository, *, scheme_ref: SchemeRef, category_name: str,
        amount_cents: int, transaction_date: date, fund_id, idempotency_key: str,
        authorisation: HistoricalPostingAuthorisation,
) -> bool:
    """Post a credit/refund row as DR bank (1010) / CR Recovered Fees & Collection Costs (4003)
    -- mirrors FinancialCoreService._post_expense_journal's shape and idempotency pattern, but
    there is no public command for a generic (non-lot-specific) historical income posting, so
    this constructs the JournalEntry directly via the same repository primitives.
    Returns True if newly posted, False if an idempotent replay was detected."""
    existing_id = await ledger.find_journal_entry_id_by_idempotency_key(scheme_ref, idempotency_key)
    if existing_id:
        return False

    resolved_period = await ledger.get_accounting_period_for_date(
        scheme_ref, transaction_date, permitted_statuses=frozenset({"open", "closed"})
    )
    income_gl_id = await ledger.get_gl_account(scheme_ref, _RECOVERY_INCOME_GL_CODE)
    bank_gl_id = await ledger.get_gl_account(scheme_ref, "1010")

    entry = JournalEntry(
        scheme_ref=scheme_ref,
        period_id=resolved_period.period_id,
        source_type="historical_income_recovery",
        narration=f"{category_name} -- credit/refund reclassified from negative expense row",
        effective_on=transaction_date,
        lines=[
            JournalLine(gl_account_id=bank_gl_id, direction=EntryDirection.DEBIT, amount_cents=amount_cents),
            JournalLine(gl_account_id=income_gl_id, direction=EntryDirection.CREDIT, amount_cents=amount_cents),
        ],
        fund_id=fund_id,
        idempotency_key=idempotency_key,
        is_test_data=False,
        posted_by=authorisation.executed_by,
        approved_by=authorisation.approved_by,
        metadata={
            "source_category": category_name,
            "reason": "negative_expense_row_reclassified_as_recovery_income",
            "historical_authorisation": {
                "reconstruction_batch_id": str(authorisation.reconstruction_batch_id),
                "reason": authorisation.reason,
                "approved_by": str(authorisation.approved_by),
                "executed_by": str(authorisation.executed_by),
                "approval_reference": authorisation.approval_reference,
            },
        },
    )
    saved_entry = await ledger.save_journal_entry(entry)
    await ledger.post_journal_entry(saved_entry.journal_entry_id, scheme_ref)
    return True


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write the postings (default: dry-run, prints plan only).")
    args = parser.parse_args()

    rows, credit_rows = _load_expense_rows()
    total_cents_by_fund = {}
    for r in rows:
        total_cents_by_fund[r["fund_type"]] = total_cents_by_fund.get(r["fund_type"], 0) + r["amount_cents"]
    net_total_cents_by_fund = dict(total_cents_by_fund)
    for r in credit_rows:
        net_total_cents_by_fund[r["fund_type"]] = net_total_cents_by_fund.get(r["fund_type"], 0) - r["amount_cents"]

    print(f"Loaded {len(rows)} expense category rows + {len(credit_rows)} credit/refund rows across 2021-2026.")
    for fund_type in sorted(total_cents_by_fund):
        gross = total_cents_by_fund[fund_type]
        net = net_total_cents_by_fund[fund_type]
        print(f"  {fund_type}: gross expense ${gross / 100:,.2f}, net after credits ${net / 100:,.2f}")
    if credit_rows:
        print("  Credit/refund rows (posted as income/recovery entries):")
        for r in credit_rows:
            print(f"    {r['year']} {r['fund_type']} {r['category_name']}: ${r['amount_cents'] / 100:,.2f}")

    if not args.apply:
        print("\nDry-run only -- no writes made. Re-run with --apply to post these entries.")
        return 0

    reconstruction_batch_id = uuid.uuid4()
    authorisation = HistoricalPostingAuthorisation(
        reconstruction_batch_id=reconstruction_batch_id,
        reason=REASON,
        approved_by=APPROVED_BY,
        executed_by=EXECUTED_BY,
        approval_reference=f"east-gate-2021-2026-rebuild:{reconstruction_batch_id}",
    )

    async with async_session_context() as session:
        tenant_id, scheme_id = await _resolve_scheme(session)
        await set_tenant(session, tenant_id)
        await _ensure_accounting_periods(session, tenant_id, scheme_id)
        await _ensure_missing_gl_accounts(session, tenant_id, scheme_id)
        await session.commit()

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        scheme_ref = SchemeRef(tenant_id=uuid.UUID(tenant_id), scheme_id=uuid.UUID(scheme_id))
        fund_ids = await resolve_scheme_fund_ids(session, scheme_ref)
        ledger = PostgresLedgerRepository(session)
        outbox = PostgresOutboxRepository(session)
        svc = FinancialCoreService(ledger, outbox)

        posted = 0
        skipped_replays = 0
        for r in rows:
            fund_id = fund_ids.get(r["fund_type"])
            if fund_id is None:
                print(f"ABORT -- no fund_id resolved for fund_type={r['fund_type']!r}")
                await session.rollback()
                return 1
            normalised_category = " ".join(r["category_name"].lower().split())
            idempotency_key = f"east_gate_historical_expense:{r['year']}:{r['fund_type']}:{normalised_category}"
            cmd = RecordHistoricalExpenseCommand(
                scheme_ref=scheme_ref,
                category_name=r["category_name"],
                amount_cents=r["amount_cents"],
                gst_cents=0,
                transaction_date=EFFECTIVE_DATE_BY_YEAR[r["year"]],
                authorisation=authorisation,
                fund_id=fund_id,
                financial_year=str(r["year"]),
                description=f"{r['category_name']} -- FY{r['year']} annual total",
                idempotency_key=idempotency_key,
                is_test_data=False,
                metadata={"source_file": r["source_file"]},
            )
            expense = await svc.record_historical_expense(cmd)
            if expense.metadata.get("replayed"):
                skipped_replays += 1
            else:
                posted += 1

        credit_posted = 0
        credit_skipped = 0
        for r in credit_rows:
            fund_id = fund_ids.get(r["fund_type"])
            if fund_id is None:
                print(f"ABORT -- no fund_id resolved for fund_type={r['fund_type']!r}")
                await session.rollback()
                return 1
            normalised_category = " ".join(r["category_name"].lower().split())
            idempotency_key = f"east_gate_historical_income_recovery:{r['year']}:{r['fund_type']}:{normalised_category}"
            is_new = await _post_income_recovery_entry(
                ledger, scheme_ref=scheme_ref, category_name=r["category_name"],
                amount_cents=r["amount_cents"], transaction_date=EFFECTIVE_DATE_BY_YEAR[r["year"]],
                fund_id=fund_id, idempotency_key=idempotency_key, authorisation=authorisation,
            )
            if is_new:
                credit_posted += 1
            else:
                credit_skipped += 1

        await session.commit()

    print(f"\nPosted: {posted} new expense journal entries, {skipped_replays} idempotent replays skipped.")
    print(f"Posted: {credit_posted} new income/recovery journal entries, {credit_skipped} idempotent replays skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
