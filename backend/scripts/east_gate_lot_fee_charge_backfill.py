#!/usr/bin/env python3
# @featuretrace:demo_bank — Posts the 109 per-lot recoverable-charge bank_transactions
#   (arrears/notice fees, rental certificates, access-hardware replacements) that
#   routers/financial_matching.py's match_review_queue correctly excluded as
#   "not a lot payment" — they're on-charges to a specific lot, not building expenses.
# Layer: script
# Data flow: finance.bank_transactions (reconstruction_batch_id IS NULL, "Lot# NN" in
#            description, amount_cents<0, dated 2025) -> finance.reconstruction_execution_
#            batches(_items) -> FinancialCoreService.charge_historical_lot_fee() (AR recharge)
#            + record_historical_expense() (cash side, non-reversals only) ->
#            finance.levy_items + finance.journal_entries (building-scoped).
# Related: backend/services/financial_core/service.py (charge_historical_lot_fee,
#            record_historical_expense)
#          backend/services/financial_core/domain/entities.py (ChargeHistoricalLotFeeCommand,
#            RecordHistoricalExpenseCommand, HistoricalPostingAuthorisation)
#          backend/scripts/reconstruction_batch_lib.py
#          backend/scripts/east_gate_2025_expense_reconstruction.py
#          backend/alembic/versions/0072_recovered_fees_gl_account.py
#          backend/alembic/versions/0073_accounting_period_integrity.py
#          backend/alembic/versions/0074_harden_recon_batches.py
#          tasks/GAP-FIN-018-east-gate-demo-bank-to-ledger-pipeline.md
"""Backfill the 109 per-lot recoverable-charge rows into the real ledger, via
an immutable, dual-control-approved reconstruction batch and the privileged
closed-period historical posting pathway (FY2025 stays closed to ordinary
posting — see east_gate_2025_expense_reconstruction.py's module docstring
for the full rationale).

All 109 rows are dated in 2025. 108 are new charges (cash-expense journal +
lot recharge journal, both posted); 1 is a tribunal-ordered (ACAT) reversal
of an earlier charge, which posts ONLY the recharge-reversal journal — no
new cash outflow occurred, so no expense leg is posted for it.

Four modes, run in this order — see east_gate_2025_expense_reconstruction.py
for the full flow description:
    --preview / --create-manifest / --approve / --apply

Usage:
    python3 scripts/east_gate_lot_fee_charge_backfill.py --building-id 13195 --preview
    python3 scripts/east_gate_lot_fee_charge_backfill.py --building-id 13195 \\
        --create-manifest --created-by <uuid>
    python3 scripts/east_gate_lot_fee_charge_backfill.py --building-id 13195 \\
        --approve --batch-id <uuid> --approved-by <uuid> --approval-reference "EC minute 2026-08-01"
    python3 scripts/east_gate_lot_fee_charge_backfill.py --building-id 13195 \\
        --apply --batch-id <uuid> --executed-by <uuid>
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from services.financial_core.adapters.db_postgres.ledger_repo import (  # noqa: E402
    PostgresLedgerRepository,
)
from services.financial_core.adapters.db_postgres.outbox_repo import (  # noqa: E402
    PostgresOutboxRepository,
)
from services.financial_core.domain.entities import (  # noqa: E402
    ChargeHistoricalLotFeeCommand,
    HistoricalPostingAuthorisation,
    RecordHistoricalExpenseCommand,
    SchemeRef,
)
from services.financial_core.service import FinancialCoreService  # noqa: E402
from scripts.reconstruction_batch_lib import (  # noqa: E402
    BatchOrchestrationError,
    approve_batch,
    assert_pending_load_not_silently_empty,
    begin_apply,
    check_item_drift,
    create_manifest_batch,
    finalize_batch_status,
    load_pending_items,
    mark_item_applied,
    mark_item_failed,
    validate_executor,
)

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"
_BATCH_TYPE = "historical_lot_fee"
_TARGET_PERIOD_LABEL = "2025"

_LOT_RE = re.compile(r"lot#\s*(\d+)|lot #\s*(\d+)", re.I)
_RECOVERY_RE = re.compile(r"arrears|final notice|legal notice|debt collection|acat|solicitor", re.I)
_REVERSAL_RE = re.compile(r"reversal", re.I)
_ACCESS_HARDWARE_RE = re.compile(r"swipe|key|fob|remote|access", re.I)

# Population-gate assertion figures, from the task doc's Lot-Charge Design
# section (2026-07-24): 76 recovery + 15 certificates + 18 access-hardware =
# 109 rows, $9,160.37, of which 1 (Lot 60, ACAT reversal) is a reversal.
_EXPECTED_ROW_COUNT = 109
_EXPECTED_AMOUNT_CENTS = 916_037


def _classify(description: str) -> tuple[str | None, str, bool]:
    lot_match = _LOT_RE.search(description)
    lot_number = (lot_match.group(1) or lot_match.group(2)) if lot_match else None
    is_reversal = bool(_REVERSAL_RE.search(description))
    charge_type = "recovery" if _RECOVERY_RE.search(description) else "adjustment"
    return lot_number, charge_type, is_reversal


def _cash_side_category(description: str) -> str:
    """Category for the OC's actual cash outflow (record_historical_expense
    side) — distinct from charge_type, which governs the recharge side only.
    A locksmith call-out for a replacement key/fob is a real
    repairs-and-maintenance cost even though it's recharged as an
    "adjustment" (not a debt-recovery cost)."""
    if _ACCESS_HARDWARE_RE.search(description):
        return "repairs and maintenance"
    return "administration expenses"


async def _resolve_scheme(session, building_id: str) -> tuple[str, str]:
    await session.execute(text(f"SELECT set_config('app.tenant_id', '{_TENANT_BYPASS_ID}', true)"))
    row = (
        await session.execute(
            text("SELECT tenant_id::text, scheme_id::text FROM core.schemes WHERE lower(scheme_number) = lower(:num)"),
            {"num": building_id},
        )
    ).fetchone()
    if row is None:
        raise RuntimeError(f"No core.schemes row for building_id={building_id!r}")
    return row[0], row[1]


async def _fetch_eligible_rows(session, tenant_id: str):
    return (
        await session.execute(
            text(
                """
                SELECT bank_transaction_id::text, transaction_date, description, amount_cents
                FROM finance.bank_transactions
                WHERE tenant_id = :tid AND reconstruction_batch_id IS NULL
                  AND amount_cents < 0
                  AND (description ILIKE '%lot#%' OR description ILIKE '%lot #%')
                ORDER BY transaction_date
                """
            ),
            {"tid": tenant_id},
        )
    ).fetchall()


async def _fetch_expense_script_rows(session, tenant_id: str):
    """The exact population east_gate_2025_expense_reconstruction.py selects
    — used only for the disjointness assertion."""
    return (
        await session.execute(
            text(
                """
                SELECT bank_transaction_id::text
                FROM finance.bank_transactions
                WHERE tenant_id = :tid AND reconstruction_batch_id IS NULL
                  AND amount_cents < 0 AND EXTRACT(YEAR FROM transaction_date) = 2025
                  AND NOT (description ILIKE '%lot#%' OR description ILIKE '%lot #%')
                """
            ),
            {"tid": tenant_id},
        )
    ).fetchall()


async def _resolve_fund_id(session, scheme_id: str) -> str | None:
    row = (
        await session.execute(
            text("SELECT fund_id::text FROM finance.funds WHERE scheme_id = :sid AND fund_type = 'admin' LIMIT 1"),
            {"sid": scheme_id},
        )
    ).fetchone()
    return row[0] if row else None


async def _resolve_lot_and_owner(session, scheme_id: str, lot_number: str, as_of):
    lot_row = (
        await session.execute(
            text("SELECT lot_id::text FROM core.lots WHERE scheme_id = :sid AND lot_number = :ln"),
            {"sid": scheme_id, "ln": lot_number},
        )
    ).fetchone()
    if lot_row is None:
        return None, None, f"lot_number {lot_number} not found"
    lot_id = lot_row[0]

    owner_row = (
        await session.execute(
            text(
                """
                SELECT owner_party_id::text FROM core.ownership_periods
                WHERE lot_id = :lot_id AND is_primary_owner = TRUE
                  AND valid_from <= :as_of AND (valid_to IS NULL OR valid_to >= :as_of)
                LIMIT 1
                """
            ),
            {"lot_id": lot_id, "as_of": as_of},
        )
    ).fetchone()
    if owner_row is None:
        return lot_id, None, f"no owner as of {as_of} for lot {lot_number}"
    return lot_id, owner_row[0], None


def _print_report(rows) -> None:
    total_cents = sum(abs(r.amount_cents) for r in rows)
    recovery, adjustment, reversals, unresolved = [], [], [], []
    for r in rows:
        lot_number, charge_type, is_reversal = _classify(r.description)
        if lot_number is None:
            unresolved.append(r)
        elif is_reversal:
            reversals.append(r)
        elif charge_type == "recovery":
            recovery.append(r)
        else:
            adjustment.append(r)

    print(f"Eligible rows: {len(rows)}, total ${total_cents / 100:,.2f}")
    print(f"  recovery (arrears/notice/debt-collection): {len(recovery)}, "
          f"${sum(abs(r.amount_cents) for r in recovery) / 100:,.2f}")
    print(f"  adjustment (certificates/access-hardware): {len(adjustment)}, "
          f"${sum(abs(r.amount_cents) for r in adjustment) / 100:,.2f}")
    print(f"  reversals: {len(reversals)}, ${sum(abs(r.amount_cents) for r in reversals) / 100:,.2f}")
    if unresolved:
        print(f"  UNRESOLVED lot number: {len(unresolved)} — {[r.bank_transaction_id for r in unresolved]}")


async def cmd_preview(building_id: str) -> int:
    async with async_session_context() as session:
        tenant_id, scheme_id = await _resolve_scheme(session, building_id)
        await set_tenant(session, tenant_id)
        rows = await _fetch_eligible_rows(session, tenant_id)
        expense_rows = await _fetch_expense_script_rows(session, tenant_id)

    overlap = {r.bank_transaction_id for r in rows} & {r.bank_transaction_id for r in expense_rows}
    print(f"Disjointness check vs east_gate_2025_expense_reconstruction.py's population: overlap={len(overlap)}")
    if overlap:
        print(f"FAIL — {len(overlap)} bank_transaction_id(s) claimed by both scripts: {sorted(overlap)[:10]}")
        return 1

    _print_report(rows)
    print()
    print(f"Expected (per task doc, GAP-FIN-018 Lot-Charge Design): {_EXPECTED_ROW_COUNT} rows, "
          f"${_EXPECTED_AMOUNT_CENTS / 100:,.2f}")
    actual_total = sum(abs(r.amount_cents) for r in rows)
    if len(rows) != _EXPECTED_ROW_COUNT or actual_total != _EXPECTED_AMOUNT_CENTS:
        print(
            f"WARNING: live population ({len(rows)} rows, ${actual_total / 100:,.2f}) differs from "
            f"expected — --create-manifest will refuse to proceed until this is investigated."
        )
    print()
    print("Preview only — nothing written. Run --create-manifest to freeze this population.")
    return 0


async def cmd_create_manifest(building_id: str, created_by: str) -> int:
    async with async_session_context() as session:
        tenant_id, scheme_id = await _resolve_scheme(session, building_id)
        await set_tenant(session, tenant_id)
        rows = await _fetch_eligible_rows(session, tenant_id)
        expense_rows = await _fetch_expense_script_rows(session, tenant_id)

    overlap = {r.bank_transaction_id for r in rows} & {r.bank_transaction_id for r in expense_rows}
    if overlap:
        print(f"ABORT — {len(overlap)} bank_transaction_id(s) claimed by both scripts. No batch created.")
        return 1

    actual_total = sum(abs(r.amount_cents) for r in rows)
    if len(rows) != _EXPECTED_ROW_COUNT or actual_total != _EXPECTED_AMOUNT_CENTS:
        print(
            f"ABORT — live population ({len(rows)} rows, ${actual_total / 100:,.2f}) does not match "
            f"expected ({_EXPECTED_ROW_COUNT} rows, ${_EXPECTED_AMOUNT_CENTS / 100:,.2f}). "
            f"No batch created — investigate the discrepancy first."
        )
        return 1

    items = []
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        for r in rows:
            lot_number, charge_type, is_reversal = _classify(r.description)
            if lot_number is None:
                print(f"ABORT — bank_transaction_id={r.bank_transaction_id} has no resolvable lot number. No batch created.")
                return 1
            lot_id, owner_party_id, err = await _resolve_lot_and_owner(session, scheme_id, lot_number, r.transaction_date)
            if err:
                print(f"ABORT — bank_transaction_id={r.bank_transaction_id}: {err}. No batch created.")
                return 1
            item_type = "lot_fee_reversal" if is_reversal else "lot_fee_charge"
            items.append({
                "bank_transaction_id": r.bank_transaction_id, "transaction_date": r.transaction_date,
                "description": r.description, "amount_cents": r.amount_cents,
                "category_name": charge_type, "lot_number": lot_number, "item_type": item_type,
            })

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        batch_id = await create_manifest_batch(
            session, tenant_id=tenant_id, scheme_id=scheme_id, batch_type=_BATCH_TYPE,
            target_period_label=_TARGET_PERIOD_LABEL,
            description="East Gate FY2025 per-lot recoverable-charge historical reconstruction (GAP-FIN-018)",
            created_by=created_by, items=items,
        )

    print(f"Manifest created: batch_id={batch_id}")
    print(f"  {len(items)} rows, ${sum(abs(i['amount_cents']) for i in items) / 100:,.2f}")
    print("Review this report against East Gate's approved FY2025 AGM financial statements, then:")
    print(f"  --approve --batch-id {batch_id} --approved-by <uuid> --approval-reference <text>")
    return 0


async def cmd_approve(building_id: str, batch_id: str, approved_by: str, approval_reference: str) -> int:
    async with async_session_context() as session:
        tenant_id, scheme_id = await _resolve_scheme(session, building_id)
        await set_tenant(session, tenant_id)
        try:
            await approve_batch(
                session, tenant_id=tenant_id, batch_id=batch_id, approved_by=approved_by,
                approval_reference=approval_reference,
            )
        except BatchOrchestrationError as exc:
            print(f"ABORT — {exc}")
            return 1
    print(f"Batch {batch_id} approved by {approved_by}.")
    return 0


async def cmd_apply(building_id: str, batch_id: str, executed_by: str) -> int:
    async with async_session_context() as session:
        tenant_id, scheme_id = await _resolve_scheme(session, building_id)
        await set_tenant(session, tenant_id)
        try:
            batch = await begin_apply(session, tenant_id, batch_id)
        except BatchOrchestrationError as exc:
            print(f"ABORT — {exc}")
            return 1

        try:
            await validate_executor(tenant_id, batch["approved_by"], executed_by)
        except BatchOrchestrationError as exc:
            print(f"ABORT — {exc}")
            return 1

        pending_items = await load_pending_items(session, tenant_id, batch_id)
        try:
            await assert_pending_load_not_silently_empty(
                session, tenant_id, batch_id, batch["expected_row_count"], len(pending_items)
            )
        except BatchOrchestrationError as exc:
            print(f"ABORT — {exc}")
            return 1
        fund_id = await _resolve_fund_id(session, scheme_id)

    print(f"Applying batch {batch_id}: {len(pending_items)} pending/failed item(s) to process.")

    scheme_ref = SchemeRef(tenant_id=tenant_id, scheme_id=scheme_id)
    posted, failed = 0, 0

    for item in pending_items:
        phase1_error: Exception | None = None
        charge_type = item["category_name"]  # stored here at manifest time — recovery|adjustment
        is_reversal = item["item_type"] == "lot_fee_reversal"

        async with async_session_context() as session:
            await set_tenant(session, tenant_id)
            drift = await check_item_drift(session, tenant_id, item)
            if drift:
                await mark_item_failed(session, tenant_id, item["batch_item_id"], RuntimeError(drift))
                failed += 1
                print(f"FAILED (drift) {item['bank_transaction_id']}: {drift}")
                continue

            lot_id, owner_party_id, err = await _resolve_lot_and_owner(
                session, scheme_id, item["lot_number"], item["transaction_date"]
            )
            if err:
                await mark_item_failed(session, tenant_id, item["batch_item_id"], RuntimeError(err))
                failed += 1
                print(f"FAILED {item['bank_transaction_id']}: {err}")
                continue

            authorisation = HistoricalPostingAuthorisation(
                reconstruction_batch_id=batch_id, reason=batch["description"],
                approved_by=batch["approved_by"], executed_by=executed_by,
                approval_reference=batch["approval_reference"] or "",
                evidence_reference=f"bank_transaction_id={item['bank_transaction_id']}",
            )
            signed_amount = -abs(item["amount_cents"]) if is_reversal else abs(item["amount_cents"])

            charge_cmd = ChargeHistoricalLotFeeCommand(
                scheme_ref=scheme_ref, lot_id=lot_id, owner_party_id=owner_party_id, fund_id=fund_id,
                charge_type=charge_type, amount_cents=signed_amount, gst_cents=0,
                transaction_date=item["transaction_date"], description=item["description"],
                authorisation=authorisation, bank_transaction_id=item["bank_transaction_id"],
                idempotency_key=f"historical_lot_fee_recharge:{item['bank_transaction_id']}",
                is_test_data=False,
            )

            try:
                ledger = PostgresLedgerRepository(session)
                outbox = PostgresOutboxRepository(session)
                svc = FinancialCoreService(ledger, outbox)

                recharge_journal_entry_id = await svc.charge_historical_lot_fee(charge_cmd)

                expense_journal_entry_id = None
                if not is_reversal:
                    # A tribunal-ordered reversal credits back an earlier
                    # charge — no new OC cash outflow, so no expense leg.
                    expense_cmd = RecordHistoricalExpenseCommand(
                        scheme_ref=scheme_ref, category_name=_cash_side_category(item["description"]),
                        amount_cents=abs(item["amount_cents"]), gst_cents=0,
                        transaction_date=item["transaction_date"], description=item["description"],
                        bank_transaction_id=item["bank_transaction_id"],
                        idempotency_key=f"historical_expense:{item['bank_transaction_id']}",
                        is_test_data=False, authorisation=authorisation,
                    )
                    expense = await svc.record_historical_expense(expense_cmd)
                    expense_journal_entry_id = str(expense.journal_entry_id)

                await mark_item_applied(
                    session, tenant_id, item["batch_item_id"],
                    expense_journal_entry_id=expense_journal_entry_id,
                    recharge_journal_entry_id=str(recharge_journal_entry_id),
                )
                await session.commit()
                posted += 1
                print(
                    f"POSTED {item['bank_transaction_id']}: recharge={recharge_journal_entry_id} "
                    f"expense={expense_journal_entry_id}"
                )
            except Exception as exc:  # noqa: BLE001 — recorded per-item, not fatal to the batch
                await session.rollback()
                phase1_error = exc

        if phase1_error is not None:
            async with async_session_context() as session:
                await set_tenant(session, tenant_id)
                await mark_item_failed(session, tenant_id, item["batch_item_id"], phase1_error)
            failed += 1
            print(f"FAILED {item['bank_transaction_id']}: {phase1_error}")

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        final_status = await finalize_batch_status(session, tenant_id, batch_id, applied_by=executed_by)

    print(f"posted={posted} failed={failed} batch_status={final_status}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--building-id", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--create-manifest", action="store_true")
    mode.add_argument("--approve", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--created-by")
    parser.add_argument("--batch-id")
    parser.add_argument("--approved-by")
    parser.add_argument("--approval-reference")
    parser.add_argument("--executed-by")
    args = parser.parse_args()

    if args.preview:
        return asyncio.run(cmd_preview(args.building_id))
    if args.create_manifest:
        if not args.created_by:
            parser.error("--create-manifest requires --created-by")
        return asyncio.run(cmd_create_manifest(args.building_id, args.created_by))
    if args.approve:
        if not (args.batch_id and args.approved_by and args.approval_reference):
            parser.error("--approve requires --batch-id, --approved-by, --approval-reference")
        return asyncio.run(cmd_approve(args.building_id, args.batch_id, args.approved_by, args.approval_reference))
    if args.apply:
        if not (args.batch_id and args.executed_by):
            parser.error("--apply requires --batch-id, --executed-by")
        return asyncio.run(cmd_apply(args.building_id, args.batch_id, args.executed_by))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
