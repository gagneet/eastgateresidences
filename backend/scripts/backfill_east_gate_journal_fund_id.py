"""Backfill `fund_id` on `finance.gl_accounts`, `finance.journal_entries`, and
`finance.expense_transactions` for building 13195 (East Gate Residences).

BACKGROUND
══════════
Neither column had ever been populated for East Gate as of 2026-07-25 (when this
script was authored). `services/financial_core/genesis.py`'s `DEFAULT_GL_ACCOUNTS`
never sets `fund_id` for any account, and the 2026-07-24 historical-reconstruction
scripts (`east_gate_levy_income_reconstruction.py`, Step 5's direct
`FinancialCoreService.record_expense()` calls, see
`tasks/GAP-FIN-018-east-gate-demo-bank-to-ledger-pipeline.md`) never passed a
`fund_id` through either. Confirmed live 2026-07-25: all 16 `gl_accounts` rows
and 3,341/3,361 `journal_entries` rows were NULL.

This had a real, live consequence, independently rediscovered 2026-08-09 while
verifying the finance.summary Postgres read-cutover: `financial_read_service.py`'s
`get_fund_balances()` and the new `get_fund_expense_totals()` both join
`finance.gl_accounts ga ON ga.fund_id = f.fund_id`. With every `gl_accounts.fund_id`
NULL, the join never matched, so `get_fund_balances()` silently returned $0.00 for
every fund, and `get_fund_expense_totals()` reported East Gate's entire
$145,652.65 FY2026 expense total as `unassigned_expense_cents` instead of
splitting it by fund. The `gl_accounts.fund_id` portion of this script was applied
2026-08-09 (9 rows) and confirmed live to fix both. `expense_transactions.fund_id`
was already fully backfilled by an earlier partial run by the time of that
re-check (0 pending). `journal_entries.fund_id` still had a real gap at that
point — not because it was already correct, but because this script's
`historical_levy_run` mapping below used to read `levy_charge`, a source_type
that no longer exists in live data (renamed/superseded since 2026-07-25) — see
the `journal_entries.fund_id` bullet below for the corrected mapping.

SCOPE — what this script backfills, and what it deliberately does NOT
───────────────────────────────────────────────────────────────────
`gl_accounts.fund_id` — set only for the 10 fund-specific accounts, derived
from account semantics (chart-of-accounts naming) plus confirmed zero live
`journal_lines` usage on the ambiguous ones, so this is pure metadata with no
data-shape risk:
    4000 Levy Income                        -> admin
    4001 Capital Works Levy Income           -> sinking
    4003 Recovered Fees & Collection Costs   -> admin
    5000-5004 (all 5 expense accounts)       -> admin (no sinking expense
                                                 account exists yet)
    1011 Trust Bank Account - Capital Works  -> sinking
Left NULL, on purpose: 1000/1010/1100/1200/2100/2200/3100 — shared control
accounts (one physical BOQ bank account, one AR ledger, one GST
position serves both funds at East Gate; see the 2020 inaugural minutes).
Forcing these to one fund would misrepresent the real accounting, not just
leave a gap. 4002 Interest Income is also left NULL — no live journal_lines
reference it yet and no code path establishes whether trust interest posts
per-fund or admin-only.

`expense_transactions.fund_id` — set for all 163 rows, same GL-account
derivation as the `expense` journal_entries below (in fact these are the
exact same 163 underlying transactions, viewed through a different table).
`expense_transactions` carries no immutability trigger, so this is a plain
`UPDATE`, no trigger disable needed.

`journal_entries.fund_id` — set only for `source_type IN ('historical_levy_run',
'expense')`, both confirmed 100% mechanically derivable with zero ambiguity:
  - historical_levy_run (40 rows as of 2026-08-09; the original 957-row
    'levy_charge' this comment described has since been renamed/superseded
    by this source_type -- there are zero 'levy_charge' rows in live data
    now): every one touches exactly one of GL 4000/4001 on its income line
    (plus the shared 1100 AR control account, correctly ignored -- verified
    live, 0 exceptions: 22 rows -> admin via 4000, 18 rows -> sinking via
    4001) -> copy that account's fund.
  - expense (238 rows as of 2026-08-09, already fully backfilled by an
    earlier partial run): every one debits one of GL 5000-5004, all
    admin-only -> admin fund_id for all.
Both source_types are immutable (`status='posted'`), so this requires a
narrow, explicit `ALTER TABLE ... DISABLE/ENABLE TRIGGER
trg_prevent_posted_journal_update`, touching only the `fund_id` column on
only these rows — the same precedent already used for `journal_lines` in
`backend/migrations/postgres/migrate_building_13195.py`.

Left NULL, on purpose: `payment_receipt` (2,175 rows) and `receipt` (46
rows). `FinancialCoreService.record_payment()` builds its journal entry with
no fund_id at all, by design — a receipt records cash against AR *before*
fund allocation; the fund is only known once `allocate_payment()` splits it
across specific `finance.levy_items` (`fund_id NOT NULL`) via
`finance.receipt_allocations`. Confirmed live: 717 of 2,037 allocated
receipts (35%) span *both* Admin and Sinking in one receipt (owners commonly
pay one combined amount). Forcing a single fund_id onto the parent journal
entry would misrepresent those 717. `genesis`/`annual_carry_forward` entries
already correctly set fund_id and are left untouched.

USAGE
═════
    cd backend
    venv/bin/python3 scripts/backfill_east_gate_journal_fund_id.py            # dry-run (default)
    venv/bin/python3 scripts/backfill_east_gate_journal_fund_id.py --apply    # writes + audit log

Requires DATABASE_URL in backend/.env (loaded automatically via config.py).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from db_postgres.engine import get_engine, dispose_engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("backfill_east_gate_journal_fund_id")

BUILDING_ID = "13195"  # One-off correction scoped to this scheme only.
_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"

# account_code -> fund_type. Anything not listed here is left NULL on purpose
# (see module docstring).
GL_ACCOUNT_FUND_MAP = {
    "4000": "admin",
    "4001": "sinking",
    "4003": "admin",
    "5000": "admin",
    "5001": "admin",
    "5002": "admin",
    "5003": "admin",
    "5004": "admin",
    "1011": "sinking",
}

# journal_entries.source_type values eligible for backfill, and the GL-code
# set whose fund uniquely determines the entry's fund_id. Verified live: every
# eligible entry touches exactly one code from its set.
JOURNAL_SOURCE_TYPES = {
    "historical_levy_run": {"4000", "4001"},
    "expense": {"5000", "5001", "5002", "5003", "5004"},
}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write the backfill (default: dry-run, prints plan only).")
    args = parser.parse_args()

    engine = get_engine()
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f"SET app.tenant_id = '{_TENANT_BYPASS_ID}'"))
            scheme_row = (
                await conn.execute(
                    text("SELECT tenant_id, scheme_id FROM core.schemes WHERE scheme_number = :bid"),
                    {"bid": BUILDING_ID},
                )
            ).first()
            if scheme_row is None:
                logger.error("No core.schemes row found for scheme_number=%s — aborting.", BUILDING_ID)
                return
            tenant_id, scheme_id = scheme_row

        async with engine.connect() as conn:
            # finance.* has no RLS bypass clause (CLAUDE.md) — real tenant_id required.
            # Postgres SET does not accept bind params; safe here since tenant_id came
            # from a prior trusted query, not user input.
            await conn.execute(text(f"SET app.tenant_id = '{tenant_id}'"))

            fund_rows = (
                await conn.execute(
                    text("SELECT fund_id, fund_type::text AS fund_type FROM finance.funds WHERE scheme_id = :sid"),
                    {"sid": str(scheme_id)},
                )
            ).all()
            fund_id_by_type = {r.fund_type: r.fund_id for r in fund_rows}
            for needed in ("admin", "sinking"):
                if needed not in fund_id_by_type:
                    logger.error("No finance.funds row with fund_type=%s for scheme %s — aborting.", needed, scheme_id)
                    return

            # ── 1. gl_accounts plan ──────────────────────────────────────
            gl_rows = (
                await conn.execute(
                    text(
                        "SELECT gl_account_id, account_code, account_name, fund_id "
                        "FROM finance.gl_accounts WHERE scheme_id = :sid ORDER BY account_code"
                    ),
                    {"sid": str(scheme_id)},
                )
            ).all()
            gl_updates = []
            for row in gl_rows:
                target_fund_type = GL_ACCOUNT_FUND_MAP.get(row.account_code)
                if target_fund_type is None:
                    continue
                target_fund_id = fund_id_by_type[target_fund_type]
                if row.fund_id == target_fund_id:
                    continue
                if row.fund_id is not None:
                    logger.warning(
                        "gl_account %s (%s) already has fund_id=%s, different from planned %s (%s) — "
                        "skipping rather than overwriting.",
                        row.account_code, row.account_name, row.fund_id, target_fund_id, target_fund_type,
                    )
                    continue
                gl_updates.append((row.gl_account_id, row.account_code, row.account_name, target_fund_id, target_fund_type))

            logger.info("gl_accounts.fund_id plan (%d change(s)):", len(gl_updates))
            for _gid, code, name, _fid, ftype in gl_updates:
                logger.info("  %s %-40s -> %s", code, name, ftype)

            # ── 1b. expense_transactions plan ────────────────────────────
            # Same accounts as the 'expense' journal_entries below (5000-5004,
            # all admin) — expense_transactions carries its own gl_account_id,
            # so this is derived the same way but is a separate table/column.
            et_rows = (
                await conn.execute(
                    text(
                        """
                        SELECT et.expense_id, ga.account_code
                        FROM finance.expense_transactions et
                        JOIN finance.gl_accounts ga ON ga.gl_account_id = et.gl_account_id
                        WHERE et.scheme_id = :sid AND et.fund_id IS NULL
                        """
                    ),
                    {"sid": str(scheme_id)},
                )
            ).all()
            et_updates = []
            et_summary: dict[str, int] = {}
            for row in et_rows:
                target_fund_type = GL_ACCOUNT_FUND_MAP.get(row.account_code)
                if target_fund_type is None:
                    logger.warning(
                        "expense_transactions %s uses gl_account_code=%s with no fund mapping — skipping.",
                        row.expense_id, row.account_code,
                    )
                    continue
                et_updates.append((row.expense_id, fund_id_by_type[target_fund_type]))
                et_summary[target_fund_type] = et_summary.get(target_fund_type, 0) + 1

            logger.info("expense_transactions.fund_id plan (%d change(s)):", len(et_updates))
            for ftype, count in sorted(et_summary.items()):
                logger.info("  %s: %d row(s)", ftype, count)

            # ── 2. journal_entries plan ──────────────────────────────────
            je_updates: list[tuple[str, str]] = []  # (journal_entry_id, fund_id)
            je_summary: dict[str, int] = {}
            for source_type, gl_codes in JOURNAL_SOURCE_TYPES.items():
                placeholders = ", ".join(f"'{c}'" for c in gl_codes)  # fixed literal set, not user input
                rows = (
                    await conn.execute(
                        text(
                            f"""
                            SELECT je.journal_entry_id, array_agg(DISTINCT ga.account_code) AS codes
                            FROM finance.journal_entries je
                            JOIN finance.journal_lines jl ON jl.journal_entry_id = je.journal_entry_id
                            JOIN finance.gl_accounts ga ON ga.gl_account_id = jl.gl_account_id
                            WHERE je.scheme_id = :sid AND je.source_type = :st AND je.fund_id IS NULL
                              AND ga.account_code IN ({placeholders})
                            GROUP BY je.journal_entry_id
                            """
                        ),
                        {"sid": str(scheme_id), "st": source_type},
                    )
                ).all()
                for r in rows:
                    matched_codes = [c for c in r.codes if c in GL_ACCOUNT_FUND_MAP]
                    if len(matched_codes) != 1:
                        logger.warning(
                            "journal_entry %s (source_type=%s) touches %d fund-determining GL code(s) "
                            "(%s) — expected exactly 1, skipping rather than guessing.",
                            r.journal_entry_id, source_type, len(matched_codes), matched_codes,
                        )
                        continue
                    fund_type = GL_ACCOUNT_FUND_MAP[matched_codes[0]]
                    je_updates.append((r.journal_entry_id, fund_id_by_type[fund_type]))
                    je_summary[f"{source_type}:{fund_type}"] = je_summary.get(f"{source_type}:{fund_type}", 0) + 1

            logger.info("journal_entries.fund_id plan (%d change(s)):", len(je_updates))
            for key, count in sorted(je_summary.items()):
                logger.info("  %s: %d row(s)", key, count)

            if not gl_updates and not je_updates and not et_updates:
                logger.info("Nothing to do — already backfilled.")
                return

            if not args.apply:
                logger.info("Dry-run only — no writes made. Re-run with --apply to write these changes.")
                return

            # ── 3. Apply gl_accounts + expense_transactions (no trigger involved) ─
            for gl_account_id, code, name, target_fund_id, _ftype in gl_updates:
                await conn.execute(
                    text("UPDATE finance.gl_accounts SET fund_id = :fid WHERE gl_account_id = :gid"),
                    {"fid": str(target_fund_id), "gid": str(gl_account_id)},
                )
            for expense_id, fund_id in et_updates:
                await conn.execute(
                    text("UPDATE finance.expense_transactions SET fund_id = :fid WHERE expense_id = :eid"),
                    {"fid": str(fund_id), "eid": str(expense_id)},
                )

            # ── 4. Apply journal_entries — scoped trigger disable/enable ─
            if je_updates:
                await conn.execute(
                    text("ALTER TABLE finance.journal_entries DISABLE TRIGGER trg_prevent_posted_journal_update")
                )
                try:
                    for journal_entry_id, fund_id in je_updates:
                        await conn.execute(
                            text("UPDATE finance.journal_entries SET fund_id = :fid WHERE journal_entry_id = :jid"),
                            {"fid": str(fund_id), "jid": str(journal_entry_id)},
                        )
                finally:
                    await conn.execute(
                        text("ALTER TABLE finance.journal_entries ENABLE TRIGGER trg_prevent_posted_journal_update")
                    )

            await conn.execute(
                text(
                    "INSERT INTO core.audit_events "
                    "(tenant_id, scheme_id, entity_type, entity_id, action, event_payload) "
                    "VALUES (:tenant_id, :scheme_id, 'finance_fund_id_backfill', :scheme_id, "
                    "'finance.fund_id.backfilled', CAST(:payload AS jsonb))"
                ),
                {
                    "tenant_id": str(tenant_id),
                    "scheme_id": str(scheme_id),
                    "payload": json.dumps({
                        "reason": "fund_id never populated by genesis.py or the 2026-07-24 historical "
                                  "reconstruction scripts; caused get_fund_balances() to silently return "
                                  "$0.00 for every fund via its gl_accounts.fund_id join.",
                        "gl_accounts_updated": len(gl_updates),
                        "expense_transactions_updated": len(et_updates),
                        "expense_transactions_by_type": et_summary,
                        "journal_entries_updated": len(je_updates),
                        "journal_entries_by_type": je_summary,
                        "source": "scripts/backfill_east_gate_journal_fund_id.py",
                    }),
                },
            )
            await conn.commit()
            logger.info(
                "Applied %d gl_accounts + %d expense_transactions + %d journal_entries fund_id updates, "
                "logged audit_events row.",
                len(gl_updates), len(et_updates), len(je_updates),
            )
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
