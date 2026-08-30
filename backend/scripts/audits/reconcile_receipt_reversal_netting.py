#!/usr/bin/env python3
# @featuretrace:finance_reconciliation — read-only reversal-netting evidence gate.
# Layer: script (audit)
# Data flow: reads finance.receipts / finance.levy_items / finance.receipt_allocations
#            (all GROSS) and finance.journal_lines/journal_entries (reversal-aware GL)
#            for one building+year and reports the divergence caused by reversals that
#            were posted to the GL only.
"""Read-only receipt/allocation vs GL-net "collected" divergence reporter.

GAP-FIN-045 / GAP-FIN-040 FU-4. Does NOT mutate either database.

## Why this exists

`FinancialCoreService.reverse_entry()` posts a mirror **journal entry** linked via
``finance.journal_entries.reversal_of_id`` and — by design — never touches the
receipt side:

  * ``finance.receipts.amount_cents``           — has NO reversed/void marker (CHECK > 0)
  * ``finance.levy_items.paid_cents``           — running total, only ever incremented
                                                  (``ledger_repo.save_allocations``); never
                                                  decremented by a reversal
  * ``finance.receipt_allocations.allocated_cents`` — insert-only, CHECK > 0

So after a reversal (e.g. the 2026-08-02 East Gate duplicate-credit reversal, 87
journal entries), the GL is correct (net of reversal) while ``paid_cents`` /
``allocated_cents`` / ``receipts`` remain **gross**. Any read path that sums the
gross fields over-reports "collected" by exactly the reversed amount. Confirmed
gross-summing LIVE paths:

  * ``get_oc_levy_summary.total_collected``  (SUM paid_cents)  -> external_api /oc/levies
  * ``get_finance_summary`` admin/sinking actuals (SUM allocated_cents) -> external_api /finance/summary
  * ``bank_feeds.py`` per-lot ``receipts_cents``               (SUM receipts.amount_cents)

and gross-summing SHADOW-only paths (the source of a false ~$2M shadow-read gap):

  * ``get_transactions_for_year`` / ``get_receipt_totals``    -> finance_shadow_read_service

The canonical reversal-aware figure is the posted-``journal_lines`` netting pattern
already used by ``get_fund_balances`` and ``get_oc_levy_summary.total_outstanding``.

This reporter prints, per building+year, every "collected" figure side by side plus
the total of un-netted reversal journal entries, so the exact inflation is provable
against live data before ANY read-path change or data repair is attempted. It is the
reconciliation-evidence gate the finance-completion rules require.

## Figures reported (all integer cents)

  gross_receipts            SUM(finance.receipts.amount_cents) in the FY window
  gross_paid_cents          SUM(finance.levy_items.paid_cents) for the FY's levy_runs
  gross_allocated_cents     SUM(finance.receipt_allocations.allocated_cents) for those items
  gl_bank_net              SUM(debit-credit) on bank accounts (1010/1011), posted, in window
                           — reversal-aware TOTAL cash-in (levies + interest + other)
  gl_ar_collected_net      SUM(credit-debit) on AR (1100) from posted receipt/reversal
                           entries in window — reversal-aware levy cash applied to AR
  reversal_entries          count + summed |amount| of posted journal_entries with
                            reversal_of_id IS NOT NULL in the window
  posting_gap              gross_receipts - gl_ar_collected_net (should be ~0 if every
                           receipt posted to the GL and no reversals are un-netted)

## Verdict

  OK                        gross ≈ GL-net within TOLERANCE_CENTS (no un-netted reversal
                            inflation detected)
  INFLATED_BY_REVERSALS     gross figures exceed the GL-net figure by ~ the reversal total
                            — the read paths that sum gross fields over-report "collected";
                            remediation owed (see GAP-FIN-045 doc)

## Usage

    cd backend
    venv/bin/python3 scripts/audits/reconcile_receipt_reversal_netting.py --building-id 13195
    venv/bin/python3 scripts/audits/reconcile_receipt_reversal_netting.py --building-id 13195 --year 2026 --json

## Exit codes

    0  reconciled (gross ≈ GL-net) OR building legitimately has no receipts
    1  gross figures inflated vs GL-net beyond tolerance (reversal-netting gap present)
    2  could not resolve building / year / scheme, or PG unavailable
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from request_context import set_ctx_building_id  # noqa: E402
from utils.finance_helpers import get_latest_levy_year  # noqa: E402

# GL account codes (match services/financial_read_service.py + genesis.py).
_AR_ACCOUNT_CODE = "1100"          # Levy Receivable / Accounts Receivable
_BANK_ACCOUNT_CODES = ("1010", "1011")  # Trust bank / Undeposited Funds (admin + capital works)

# Divergence under this many cents is treated as reconciled (rounding / 4c allocation noise).
TOLERANCE_CENTS = 100


async def _fy_window(building_id: str, year: str):
    """Resolve (financial_year, starts_on, ends_on) for the building+year, or None."""
    from services.financial_read_service import FinancialReadService  # noqa: E402

    svc = FinancialReadService()
    # _get_financial_year_window is the same helper the live read paths use.
    window = await svc._get_financial_year_window(building_id, year)  # noqa: SLF001
    if window is None:
        return None
    return window


async def _collect(building_id: str, year: str) -> dict:
    from sqlalchemy import text  # noqa: E402

    from db_postgres.repos.config_repo import resolve_scheme_context  # noqa: E402
    from db_postgres.session import async_session_context, set_tenant  # noqa: E402

    scheme = await resolve_scheme_context(building_id)
    if not scheme:
        return {"error": "no scheme row for building"}
    window = await _fy_window(building_id, year)
    if window is None:
        return {"error": f"no financial-year window for {building_id}/{year}"}

    scheme_id = str(scheme["scheme_id"])
    tenant_id = str(scheme["tenant_id"])
    params = {
        "scheme_id": scheme_id,
        "financial_year": window.financial_year,
        "starts_on": window.starts_on,
        "ends_on": window.ends_on,
        "ar": _AR_ACCOUNT_CODE,
        "banks": list(_BANK_ACCOUNT_CODES),
    }

    out: dict = {"financial_year": window.financial_year}
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)

        async def scalar(sql: str) -> int:
            return int((await session.execute(text(sql), params)).scalar() or 0)

        # --- GROSS receipt-side figures ---
        out["gross_receipts"] = await scalar(
            """
            SELECT COALESCE(SUM(amount_cents), 0) FROM finance.receipts
            WHERE scheme_id = :scheme_id
              AND received_on >= :starts_on AND received_on <= :ends_on
            """
        )
        out["gross_paid_cents"] = await scalar(
            """
            SELECT COALESCE(SUM(li.paid_cents), 0)
            FROM finance.levy_items li
            JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
            WHERE li.scheme_id = :scheme_id AND lr.financial_year = :financial_year
            """
        )
        out["gross_allocated_cents"] = await scalar(
            """
            SELECT COALESCE(SUM(ra.allocated_cents), 0)
            FROM finance.receipt_allocations ra
            JOIN finance.levy_items li ON li.levy_item_id = ra.levy_item_id
            JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
            WHERE li.scheme_id = :scheme_id AND lr.financial_year = :financial_year
            """
        )

        # --- GL-net (reversal-aware) figures ---
        # Total cash into the bank/undeposited accounts (debit adds, credit removes);
        # a reversal of a receipt credits the bank account and nets out. This is TOTAL
        # cash-in (levies + interest + other income), not levy-only.
        out["gl_bank_net"] = await scalar(
            """
            SELECT COALESCE(SUM(
                CASE WHEN jl.direction = 'debit' THEN jl.amount_cents ELSE -jl.amount_cents END
            ), 0)
            FROM finance.journal_lines jl
            JOIN finance.journal_entries je ON je.journal_entry_id = jl.journal_entry_id
            JOIN finance.gl_accounts ga ON ga.gl_account_id = jl.gl_account_id
            WHERE je.scheme_id = :scheme_id
              AND ga.account_code = ANY(:banks)
              AND je.status = 'posted'
              AND je.effective_on >= :starts_on AND je.effective_on <= :ends_on
            """
        )
        # Levy cash applied to Accounts Receivable (credit reduces AR = payment; reversal
        # debits AR back and nets out). Charges DEBIT AR, so isolate to receipt+reversal
        # source entries to avoid netting the original charge in.
        out["gl_ar_collected_net"] = await scalar(
            """
            SELECT COALESCE(SUM(
                CASE WHEN jl.direction = 'credit' THEN jl.amount_cents ELSE -jl.amount_cents END
            ), 0)
            FROM finance.journal_lines jl
            JOIN finance.journal_entries je ON je.journal_entry_id = jl.journal_entry_id
            JOIN finance.gl_accounts ga ON ga.gl_account_id = jl.gl_account_id
            WHERE je.scheme_id = :scheme_id
              AND ga.account_code = :ar
              AND je.status = 'posted'
              AND je.source_type IN ('payment_receipt', 'reversal')
              AND je.effective_on >= :starts_on AND je.effective_on <= :ends_on
            """
        )

        # --- Reversal totals (posted, in window) ---
        rev_row = (await session.execute(
            text(
                """
                SELECT COUNT(*) AS c, COALESCE(SUM(
                    (SELECT COALESCE(SUM(jl.amount_cents), 0) FROM finance.journal_lines jl
                     WHERE jl.journal_entry_id = je.journal_entry_id AND jl.direction = 'debit')
                ), 0) AS s
                FROM finance.journal_entries je
                WHERE je.scheme_id = :scheme_id
                  AND je.reversal_of_id IS NOT NULL
                  AND je.status = 'posted'
                  AND je.effective_on >= :starts_on AND je.effective_on <= :ends_on
                """
            ),
            params,
        )).one()
        out["reversal_entries"] = {"count": int(rev_row.c), "sum_cents": int(rev_row.s)}

    # --- Derived divergence ---
    out["posting_gap"] = out["gross_receipts"] - out["gl_ar_collected_net"]
    out["paid_vs_gl_gap"] = out["gross_paid_cents"] - out["gl_ar_collected_net"]
    out["scheme_id"] = scheme_id
    return out


def _verdict(rep: dict) -> str:
    if rep.get("error"):
        return "ERROR"
    if rep["gross_receipts"] == 0 and rep["gross_paid_cents"] == 0:
        return "OK"  # nothing to reconcile
    # If gross figures exceed the reversal-aware GL figure by more than tolerance AND
    # there are un-netted reversals of a comparable size, flag it.
    worst_gap = max(abs(rep["posting_gap"]), abs(rep["paid_vs_gl_gap"]))
    if worst_gap > TOLERANCE_CENTS:
        return "INFLATED_BY_REVERSALS"
    return "OK"


async def run(building_id: str, year: str | None, as_json: bool) -> int:
    set_ctx_building_id(building_id)
    resolved_year = year or await get_latest_levy_year(building_id)
    if not resolved_year:
        print(f"ERROR: could not resolve a levy year for building {building_id}", file=sys.stderr)
        return 2

    try:
        rep = await _collect(building_id, str(resolved_year))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: reconciliation failed: {exc!r}", file=sys.stderr)
        return 2

    if rep.get("error"):
        print(f"ERROR: {rep['error']}", file=sys.stderr)
        return 2

    rep["building_id"] = building_id
    rep["verdict"] = _verdict(rep)

    if as_json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        def aud(c: int) -> str:
            return f"${c / 100:,.2f}"

        print(f"Receipt/reversal-netting reconciliation — building {building_id}, "
              f"FY {rep['financial_year']}")
        print("  GROSS (receipt-side, NOT reversal-aware):")
        print(f"    finance.receipts.amount_cents          = {aud(rep['gross_receipts'])}")
        print(f"    finance.levy_items.paid_cents          = {aud(rep['gross_paid_cents'])}")
        print(f"    finance.receipt_allocations.allocated  = {aud(rep['gross_allocated_cents'])}")
        print("  GL-NET (reversal-aware, journal_lines):")
        print(f"    bank accounts {'/'.join(_BANK_ACCOUNT_CODES)} net cash-in     = {aud(rep['gl_bank_net'])}")
        print(f"    AR {_AR_ACCOUNT_CODE} collected (receipt+reversal) = {aud(rep['gl_ar_collected_net'])}")
        print("  Reversals (posted, in window):")
        print(f"    count = {rep['reversal_entries']['count']}   sum = {aud(rep['reversal_entries']['sum_cents'])}")
        print("  Divergence:")
        print(f"    gross_receipts - GL_AR_collected  = {aud(rep['posting_gap'])}")
        print(f"    gross_paid     - GL_AR_collected  = {aud(rep['paid_vs_gl_gap'])}")
        print(f"  VERDICT: {rep['verdict']}"
              + ("  <- gross read paths over-report 'collected'; reversal-netting owed (GAP-FIN-045)"
                 if rep["verdict"] == "INFLATED_BY_REVERSALS" else ""))

    return 1 if rep["verdict"] == "INFLATED_BY_REVERSALS" else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only receipt/allocation vs GL-net collected divergence reporter (GAP-FIN-045)"
    )
    parser.add_argument("--building-id", required=True, help="building_id / scheme number, e.g. 13195")
    parser.add_argument("--year", default=None, help="levy year (default: latest started year)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    return asyncio.run(run(args.building_id, args.year, args.json))


if __name__ == "__main__":
    raise SystemExit(main())
