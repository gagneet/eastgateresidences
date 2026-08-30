"""Reverse East Gate's (13195) 87 duplicate `portal_reconciliation:` GL credits.

BACKGROUND
══════════
Discovered 2026-08-02 while building GAP-FIN-030 Fix 4 (Postgres shadow-read for the
Levy Status / Transactions tabs): every East Gate lot's `finance.journal_entries` AR
history (account 1100) is a clean, correctly-offsetting itemized ledger for
2021-2026 — each year's "Historical levy run" debits are matched by "Payment from
lot" credits of the identical amount, netting to zero. On top of that already-correct
history, exactly one extra journal entry per lot (idempotency_key prefix
`portal_reconciliation:{building_id}:{unit}:{year}:{date}`, all dated 2026-08-01,
`source_type='payment_receipt'`) posted a SECOND, duplicate lump-sum credit
(`debit 1010 Undeposited Funds / credit 1100 Accounts Receivable`).

Each entry's own `metadata.note` confirms the mechanism: "Back-solved cumulative
reconciliation payment: brings this lot's PostgreSQL AR balance down to match the
real balance reported by Civium Strata portal scrape" — i.e. it posted Mongo
`unit_levy_ledger.total_paid` (or an equivalent scraped/back-solved cumulative
figure) as a brand-new GL receipt. `routers/finance.py`'s own
`_get_unit_dashboard_overview_mongo_fallback()` docstring (2026-08-01, same day)
already documents `total_paid` as unsafe for exactly this purpose: "back-solved from
the portal's live outstanding balance ... not a sum of individual observed payment
transactions ... cumulative payment history through the scrape date, not payments
received within this calendar year specifically." Posting it as a literal new GL
receipt on top of an already-reconciled itemized ledger double-counts every dollar
the owner ever paid.

Live-verified for TH087: correct closing balance (Mongo, bank-verified,
previously audited) is a $254.98 credit. With the duplicate entry included,
Postgres's AR closing balance read $25,238.02 credit -- ~100x wrong. Building-wide,
87 duplicate entries total $1,769,655.36 (confirmed: exactly one per lot, none
already reversed, all `status='posted'`).

NOT touched: 14 separate `match-decision:...:allocate` entries dated the same day
($3,721.46 total) are a different, legitimate mechanism (historical-reconstruction
match-review-queue auto-allocation, `source_type='payment_receipt'` but distinct
idempotency_key prefix and much smaller per-line amounts consistent with real
itemized payments) -- confirmed NOT part of this bug and explicitly excluded by the
`idempotency_key LIKE 'portal_reconciliation:%'` filter below.

MECHANISM
═════════
Uses `FinancialCoreService.reverse_entry()` (the same sanctioned, already-tested
reversal command used elsewhere in this codebase) for each of the 87 entries -- never
a raw UPDATE/DELETE (posted journal_entries are trigger-protected and hash-chained;
`reverse_entry` posts a new, fully mirrored, `reversal_of_id`-linked entry and never
mutates the original, preserving the immutability invariant and the audit trail).

USAGE
═════
    cd backend
    venv/bin/python3 scripts/reverse_east_gate_portal_reconciliation_duplicate_credits.py            # dry-run (default)
    venv/bin/python3 scripts/reverse_east_gate_portal_reconciliation_duplicate_credits.py --apply    # writes
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from services.financial_core.adapters.db_postgres.ledger_repo import (  # noqa: E402
    PostgresLedgerRepository,
)
from services.financial_core.adapters.db_postgres.outbox_repo import (  # noqa: E402
    PostgresOutboxRepository,
)
from services.financial_core.domain.entities import ReverseEntryCommand, SchemeRef  # noqa: E402
from services.financial_core.service import FinancialCoreService  # noqa: E402

BUILDING_ID = "13195"
_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"
_POSTED_BY = uuid.UUID("7d532e51-a65e-42c9-8e0b-4ba885af2d61")  # scripts/audits/east_gate_cutover_toggles.py SET_BY

_EXPECTED_COUNT = 87
_EXPECTED_TOTAL_CENTS = 176_965_536  # $1,769,655.36
_IDEMPOTENCY_PREFIX = "portal_reconciliation:"
_REASON = (
    "GAP-FIN-030: reverse duplicate portal_reconciliation credit -- posted a "
    "back-solved cumulative total_paid figure as a new GL receipt on top of an "
    "already-correctly-offsetting itemized historical ledger, double-counting "
    "every dollar previously paid. See scripts/reverse_east_gate_portal_"
    "reconciliation_duplicate_credits.py for full investigation."
)


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


async def _fetch_duplicate_entries(session, tenant_id: str, scheme_id: str):
    await set_tenant(session, tenant_id)
    return (
        await session.execute(
            text(
                """
                SELECT je.journal_entry_id::text, je.idempotency_key, l.unit_number,
                       jl.amount_cents
                FROM finance.journal_entries je
                JOIN finance.journal_lines jl ON jl.journal_entry_id = je.journal_entry_id
                JOIN finance.gl_accounts ga ON ga.gl_account_id = jl.gl_account_id
                JOIN core.lots l ON l.lot_id = jl.lot_id
                WHERE je.scheme_id = :sid
                  AND je.idempotency_key LIKE :prefix
                  AND je.status = 'posted'
                  AND ga.account_code = '1100'
                  AND jl.direction = 'credit'
                ORDER BY l.unit_number
                """
            ),
            {"sid": scheme_id, "prefix": f"{_IDEMPOTENCY_PREFIX}%"},
        )
    ).fetchall()


async def _fetch_already_reversed(session, scheme_id: str) -> int:
    result = await session.execute(
        text(
            """
            SELECT count(*) FROM finance.journal_entries je
            WHERE je.scheme_id = :sid AND je.reversal_of_id IN (
                SELECT journal_entry_id FROM finance.journal_entries
                WHERE scheme_id = :sid AND idempotency_key LIKE :prefix
            )
            """
        ),
        {"sid": scheme_id, "prefix": f"{_IDEMPOTENCY_PREFIX}%"},
    )
    return result.scalar() or 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write the reversals (default: dry-run, prints plan only).")
    args = parser.parse_args()

    async with async_session_context() as session:
        tenant_id, scheme_id = await _resolve_scheme(session)
        rows = await _fetch_duplicate_entries(session, tenant_id, scheme_id)
        already_reversed = await _fetch_already_reversed(session, scheme_id)

    if not rows:
        print("ABORT — no portal_reconciliation duplicate entries found; nothing to reverse.")
        return 1

    if already_reversed:
        print(f"ABORT — {already_reversed} of these entries already have a reversal posted. Investigate before re-running.")
        return 1

    total_cents = sum(r.amount_cents for r in rows)
    if len(rows) != _EXPECTED_COUNT or total_cents != _EXPECTED_TOTAL_CENTS:
        print(
            f"ABORT — found {len(rows)} entries totalling ${total_cents / 100:,.2f}; "
            f"expected {_EXPECTED_COUNT} totalling ${_EXPECTED_TOTAL_CENTS / 100:,.2f}. "
            f"Data may have changed since this script was written — investigate before proceeding."
        )
        return 1

    print(f"Found {len(rows)} duplicate portal_reconciliation credits, ${total_cents / 100:,.2f} total.")
    print(f"Sample (first 5): {[(r.unit_number, r.amount_cents) for r in rows[:5]]}")

    if not args.apply:
        print("\nDry-run only — no writes made. Re-run with --apply to write these reversals.")
        return 0

    scheme_ref = SchemeRef(tenant_id=uuid.UUID(tenant_id), scheme_id=uuid.UUID(scheme_id))
    reversed_count = 0
    reversed_total_cents = 0

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        ledger = PostgresLedgerRepository(session)
        outbox = PostgresOutboxRepository(session)
        svc = FinancialCoreService(ledger, outbox)

        for row in rows:
            cmd = ReverseEntryCommand(
                scheme_ref=scheme_ref,
                journal_entry_id=uuid.UUID(row.journal_entry_id),
                reason=_REASON,
                idempotency_key=f"gap_fin_030_reverse_portal_reconciliation:{row.journal_entry_id}",
            )
            result = await svc.reverse_entry(cmd)
            reversed_count += 1
            reversed_total_cents += row.amount_cents
            print(f"  {row.unit_number}: reversed {row.journal_entry_id} -> {result.reversal_entry_id}")

        await session.commit()

    print(f"\nReversed: {reversed_count} entries, ${reversed_total_cents / 100:,.2f} total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
