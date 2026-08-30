"""Post East Gate's (13195) missing FY2022 sinking-fund special-levy top-up.

BACKGROUND
══════════
Per the verified AGM CSV (`docs/finances/up13195_agm_proposed_spent_financials.csv`),
East Gate's real FY2022 sinking fund levy totalled **$95,050.00** — the
original $25,000 ($10,000 ordinary + $15,000 lift-replacement reserve) plus
a $70,000 top-up resolved at the 10 Nov 2022 General Meeting. Postgres
`finance.levy_items` for FY2022 sinking only totals **$10,000.00** — traced
to `migrations/postgres/migrate_building_13195.py`'s `_step_08_levy_runs_and_items()`,
which populated `finance.levy_items` directly from Mongo `unit_levy_ledger`'s
`sinking_levied` per-unit field; that field was itself short for 2022 (now
corrected in Mongo separately — see `import_agm_csv_financials.py` — but per
migration `_step_08`'s `ON CONFLICT ... DO NOTHING`, a plain re-run of that
migration would NOT retroactively fix the 87 already-posted rows, and both
the levy_items AND their matching journal entries are immutable once
posted). This script adds the missing **$85,050.00** as a genuinely new,
separate "special" levy run — it does not edit any existing row.

ALLOCATION METHOD (confirmed with the user 2026-07-25)
────────────────────────────────────────────────────────
Each lot's shortfall = its EXISTING FY2022 sinking `principal_cents` × 8.505
(i.e. scaling the existing $10,000 split up to $95,050 and taking the
difference) — this preserves whatever unit-entitlement weighting the
original $10,000 split already used, without needing a second, independent
entitlement source that could disagree with it. `owner_party_id` per lot is
copied from each lot's own existing 2022 levy_items row (not re-resolved
from `core.ownership_periods`), so the top-up is attributed to the exact
same owner as the original charge for that lot/year.

GL ACCOUNT FIX THIS DEPENDED ON
──────────────────────────────────
`FinancialCoreService.create_levy()` previously always credited GL `4000`
(Admin Levy Income) regardless of fund — would have silently misposted this
sinking levy as admin income. Fixed same day (this script's reason for
existing) to resolve the income account from the lot_levies' actual
fund_type (`4000` admin / `4001` sinking) — see `services/financial_core/service.py`.

Accounting period 2022 is OPEN (confirmed live) — posted via the ordinary
`create_levy()` path, no dual-control historical-posting authorisation
required. Attributed to the same placeholder identity already established
for this class of one-off correction
(`scripts/audits/east_gate_cutover_toggles.py`'s `SET_BY`).

USAGE
═════
    cd backend
    venv/bin/python3 scripts/fix_east_gate_2022_sinking_levy_shortfall.py            # dry-run (default)
    venv/bin/python3 scripts/fix_east_gate_2022_sinking_levy_shortfall.py --apply    # writes
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from services.financial_core.adapters.db_postgres.ledger_repo import (  # noqa: E402
    PostgresLedgerRepository,
)
from services.financial_core.adapters.db_postgres.outbox_repo import (  # noqa: E402
    PostgresOutboxRepository,
)
from services.financial_core.domain.entities import CreateLevyCommand, LotLevySpec, SchemeRef  # noqa: E402
from services.financial_core.service import FinancialCoreService  # noqa: E402

BUILDING_ID = "13195"
_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"
_POSTED_BY = uuid.UUID("7d532e51-a65e-42c9-8e0b-4ba885af2d61")  # scripts/audits/east_gate_cutover_toggles.py SET_BY

_EXISTING_TOTAL_CENTS = 1_000_000  # $10,000.00
_CORRECT_TOTAL_CENTS = 9_505_000  # $95,050.00
_SCALE_UP_FACTOR = _CORRECT_TOTAL_CENTS / _EXISTING_TOTAL_CENTS  # 9.505
_ISSUE_DATE = date(2022, 11, 10)  # Nov 2022 General Meeting (special levy resolved)
_DUE_DATE = date(2022, 12, 31)


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


async def _fetch_existing_2022_sinking_items(session, tenant_id: str, scheme_id: str):
    await set_tenant(session, tenant_id)
    return (
        await session.execute(
            text(
                """
                SELECT li.lot_id::text, li.owner_party_id::text, li.principal_cents, f.fund_id::text
                FROM finance.levy_items li
                JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                JOIN finance.funds f ON f.fund_id = li.fund_id
                WHERE lr.scheme_id = :sid AND lr.financial_year = '2022' AND f.fund_type = 'sinking'
                """
            ),
            {"sid": scheme_id},
        )
    ).fetchall()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write the correction (default: dry-run, prints plan only).")
    args = parser.parse_args()

    async with async_session_context() as session:
        tenant_id, scheme_id = await _resolve_scheme(session)
        rows = await _fetch_existing_2022_sinking_items(session, tenant_id, scheme_id)

    if not rows:
        print("ABORT — no existing FY2022 sinking levy_items found; nothing to scale from.")
        return 1

    existing_total = sum(r.principal_cents for r in rows)
    if existing_total != _EXISTING_TOTAL_CENTS:
        print(
            f"ABORT — existing FY2022 sinking total is ${existing_total / 100:,.2f}, expected "
            f"${_EXISTING_TOTAL_CENTS / 100:,.2f}. Data may have changed since this script was written — "
            f"investigate before proceeding."
        )
        return 1

    fund_id = rows[0].fund_id
    lot_levies = []
    for r in rows:
        shortfall_cents = round(r.principal_cents * (_SCALE_UP_FACTOR - 1))
        lot_levies.append((r.lot_id, r.owner_party_id, shortfall_cents))

    total_shortfall = sum(s for _, _, s in lot_levies)
    print(f"Existing FY2022 sinking total: ${existing_total / 100:,.2f} across {len(rows)} lots")
    print(f"Target total (CSV-verified):   ${_CORRECT_TOTAL_CENTS / 100:,.2f}")
    print(f"Shortfall to post:             ${total_shortfall / 100:,.2f} across {len(lot_levies)} lots")
    print(f"  levy_run_type='special', issue_date={_ISSUE_DATE}, due_date={_DUE_DATE}, fund=sinking")
    print(f"  sample (first 3): {lot_levies[:3]}")

    if abs(total_shortfall - (_CORRECT_TOTAL_CENTS - _EXISTING_TOTAL_CENTS)) > 100:  # >$1 rounding tolerance
        print(
            f"ABORT — rounded per-lot shortfalls sum to ${total_shortfall / 100:,.2f}, expected "
            f"${(_CORRECT_TOTAL_CENTS - _EXISTING_TOTAL_CENTS) / 100:,.2f}. Rounding drift too large — investigate."
        )
        return 1

    if not args.apply:
        print("\nDry-run only — no writes made. Re-run with --apply to write this correction.")
        return 0

    scheme_ref = SchemeRef(tenant_id=uuid.UUID(tenant_id), scheme_id=uuid.UUID(scheme_id))
    cmd = CreateLevyCommand(
        scheme_ref=scheme_ref,
        financial_year="2022",
        quarter_no=0,
        issue_date=_ISSUE_DATE,
        due_date=_DUE_DATE,
        lot_levies=[
            LotLevySpec(
                lot_id=uuid.UUID(lot_id), owner_party_id=uuid.UUID(owner_id),
                fund_id=uuid.UUID(fund_id), principal_cents=shortfall_cents, gst_cents=0,
            )
            for lot_id, owner_id, shortfall_cents in lot_levies
        ],
        idempotency_key=f"east_gate_2022_sinking_special_levy_topup:{BUILDING_ID}",
        is_test_data=False,
        levy_run_type="special",
    )

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        ledger = PostgresLedgerRepository(session)
        outbox = PostgresOutboxRepository(session)
        svc = FinancialCoreService(ledger, outbox)
        saved_items = await svc.create_levy(cmd)
        await session.commit()

    print(f"\nPosted: {len(saved_items)} new levy_items, ${sum(i.principal_cents for i in saved_items) / 100:,.2f} total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
