#!/usr/bin/env python3
# @featuretrace:financial_integration_v2 — Posts the 362 FY2025 general-expense
#   bank_transactions rows into the real ledger via the privileged historical
#   reconstruction pathway (closed-period, dual-control-authorised).
# Layer: cron
# Data flow: finance.bank_transactions (reconstruction_batch_id IS NULL, amount_cents<0,
#            year=2025, not lot-tagged) -> finance.reconstruction_execution_batches(_items)
#            -> FinancialCoreService.record_historical_expense() -> finance.journal_entries
#            + finance.expense_transactions (building-scoped).
# Related: backend/services/financial_core/service.py (record_historical_expense)
#          backend/services/financial_core/domain/entities.py (RecordHistoricalExpenseCommand,
#            HistoricalPostingAuthorisation)
#          backend/scripts/reconstruction_batch_lib.py
#          backend/scripts/east_gate_lot_fee_charge_backfill.py
#          backend/alembic/versions/0073_accounting_period_integrity.py
#          backend/alembic/versions/0074_harden_recon_batches.py
#          tasks/GAP-FIN-018-east-gate-demo-bank-to-ledger-pipeline.md
"""Post East Gate's 362 FY2025 general-expense bank_transactions rows into
the real ledger, via an immutable, dual-control-approved reconstruction
batch and the privileged closed-period historical posting pathway.

FY2025 stays closed to ordinary posting — this script's target rows are
2025-dated but the *only* code path permitted to write them
(record_historical_expense()) requires an approved batch with a distinct
approver and executor, and is not reachable from any HTTP route.

Four modes, run in this order:
    --preview           Read-only report. Writes nothing.
    --create-manifest    Freezes the current eligible-row population into an
                          immutable batch + item snapshots (status=manifest_pending).
    --approve            Marks a batch approved (requires --batch-id, --approved-by,
                          --approval-reference; approved_by must differ from created_by).
    --apply              Posts every pending/failed item of an approved batch
                          (requires --batch-id, --executed-by; executed_by must
                          differ from approved_by). Resumable after a partial failure.

Usage:
    python3 scripts/east_gate_2025_expense_reconstruction.py --building-id 13195 --preview
    python3 scripts/east_gate_2025_expense_reconstruction.py --building-id 13195 \\
        --create-manifest --created-by <uuid>
    python3 scripts/east_gate_2025_expense_reconstruction.py --building-id 13195 \\
        --approve --batch-id <uuid> --approved-by <uuid> --approval-reference "EC minute 2026-08-01"
    python3 scripts/east_gate_2025_expense_reconstruction.py --building-id 13195 \\
        --apply --batch-id <uuid> --executed-by <uuid>
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import date
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
_BATCH_TYPE = "historical_expense"
_TARGET_PERIOD_LABEL = "2025"

_INSURANCE_RE = re.compile(r"insur", re.I)
_REPAIRS_RE = re.compile(r"repair|maintenance|locksmith|plumb|electrician|handyman", re.I)
_MGMT_FEES_RE = re.compile(r"management fee|strata manag|managing agent", re.I)
_LEGAL_RE = re.compile(r"legal|solicitor|lawyer|compliance", re.I)

# Expected figures from the task doc's own Step 5 scoping (2026-07-24) —
# population-gate assertion, not a hardcoded row selection. If live data has
# drifted from this since, the script aborts loudly rather than silently
# proceeding with a different population than was scoped/reviewed.
_EXPECTED_ROW_COUNT = 362
_EXPECTED_AMOUNT_CENTS = 33_798_638


def _categorize(description: str) -> str:
    if _INSURANCE_RE.search(description):
        return "insurance"
    if _REPAIRS_RE.search(description):
        return "repairs and maintenance"
    if _MGMT_FEES_RE.search(description):
        return "management fees"
    if _LEGAL_RE.search(description):
        return "legal and compliance"
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
                  AND amount_cents < 0 AND EXTRACT(YEAR FROM transaction_date) = 2025
                  AND NOT (description ILIKE '%lot#%' OR description ILIKE '%lot #%')
                ORDER BY transaction_date
                """
            ),
            {"tid": tenant_id},
        )
    ).fetchall()


async def _fetch_lot_fee_script_rows(session, tenant_id: str):
    """The exact population east_gate_lot_fee_charge_backfill.py selects —
    used only for the disjointness assertion, not posted by this script."""
    return (
        await session.execute(
            text(
                """
                SELECT bank_transaction_id::text
                FROM finance.bank_transactions
                WHERE tenant_id = :tid AND reconstruction_batch_id IS NULL
                  AND (description ILIKE '%lot#%' OR description ILIKE '%lot #%')
                """
            ),
            {"tid": tenant_id},
        )
    ).fetchall()


def _print_report(rows) -> None:
    total_cents = sum(abs(r.amount_cents) for r in rows)
    by_category: dict[str, list] = {}
    for r in rows:
        by_category.setdefault(_categorize(r.description), []).append(r)

    print(f"Eligible rows: {len(rows)}, total ${total_cents / 100:,.2f}")
    for cat, items in sorted(by_category.items(), key=lambda kv: -sum(abs(i.amount_cents) for i in kv[1])):
        cat_total = sum(abs(i.amount_cents) for i in items)
        print(f"  {cat}: {len(items)} rows, ${cat_total / 100:,.2f}")


async def cmd_preview(building_id: str) -> int:
    async with async_session_context() as session:
        tenant_id, scheme_id = await _resolve_scheme(session, building_id)
        await set_tenant(session, tenant_id)
        rows = await _fetch_eligible_rows(session, tenant_id)
        lot_fee_rows = await _fetch_lot_fee_script_rows(session, tenant_id)

    overlap = {r.bank_transaction_id for r in rows} & {r.bank_transaction_id for r in lot_fee_rows}
    print(f"Disjointness check vs east_gate_lot_fee_charge_backfill.py's population: overlap={len(overlap)}")
    if overlap:
        print(f"FAIL — {len(overlap)} bank_transaction_id(s) claimed by both scripts: {sorted(overlap)[:10]}")
        return 1

    _print_report(rows)
    print()
    print(f"Expected (per task doc, GAP-FIN-018): {_EXPECTED_ROW_COUNT} rows, "
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
        lot_fee_rows = await _fetch_lot_fee_script_rows(session, tenant_id)

    overlap = {r.bank_transaction_id for r in rows} & {r.bank_transaction_id for r in lot_fee_rows}
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

    # _categorize() always returns a string (falls back to "administration
    # expenses" as its catch-all) — there is no "unclassifiable" case to
    # guard against here, unlike the lot-fee script's _classify(), which can
    # genuinely fail to extract a lot number. Every row gets a deterministic
    # classification by construction.
    items = [
        {
            "bank_transaction_id": r.bank_transaction_id, "transaction_date": r.transaction_date,
            "description": r.description, "amount_cents": r.amount_cents,
            "category_name": _categorize(r.description), "lot_number": None, "item_type": "general_expense",
        }
        for r in rows
    ]

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        batch_id = await create_manifest_batch(
            session, tenant_id=tenant_id, scheme_id=scheme_id, batch_type=_BATCH_TYPE,
            target_period_label=_TARGET_PERIOD_LABEL,
            description="East Gate FY2025 general-expense historical reconstruction (GAP-FIN-018)",
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

    print(f"Applying batch {batch_id}: {len(pending_items)} pending/failed item(s) to process.")

    scheme_ref = SchemeRef(tenant_id=tenant_id, scheme_id=scheme_id)
    posted, failed = 0, 0

    for item in pending_items:
        phase1_error: Exception | None = None

        async with async_session_context() as session:
            await set_tenant(session, tenant_id)
            drift = await check_item_drift(session, tenant_id, item)
            if drift:
                await mark_item_failed(session, tenant_id, item["batch_item_id"], RuntimeError(drift))
                failed += 1
                print(f"FAILED (drift) {item['bank_transaction_id']}: {drift}")
                continue

            authorisation = HistoricalPostingAuthorisation(
                reconstruction_batch_id=batch_id, reason=batch["description"],
                approved_by=batch["approved_by"], executed_by=executed_by,
                approval_reference=batch["approval_reference"] or "",
                evidence_reference=f"bank_transaction_id={item['bank_transaction_id']}",
            )
            cmd = RecordHistoricalExpenseCommand(
                scheme_ref=scheme_ref, category_name=item["category_name"], amount_cents=abs(item["amount_cents"]),
                gst_cents=0, transaction_date=item["transaction_date"],
                description=item["description"], bank_transaction_id=item["bank_transaction_id"],
                idempotency_key=f"historical_expense:{item['bank_transaction_id']}",
                is_test_data=False, authorisation=authorisation,
            )

            try:
                ledger = PostgresLedgerRepository(session)
                outbox = PostgresOutboxRepository(session)
                svc = FinancialCoreService(ledger, outbox)
                expense = await svc.record_historical_expense(cmd)
                await mark_item_applied(
                    session, tenant_id, item["batch_item_id"],
                    expense_journal_entry_id=str(expense.journal_entry_id), recharge_journal_entry_id=None,
                )
                await session.commit()
                posted += 1
                print(f"POSTED {item['bank_transaction_id']}: journal={expense.journal_entry_id}")
            except Exception as exc:  # noqa: BLE001 — recorded per-item, not fatal to the batch
                await session.rollback()
                phase1_error = exc

        if phase1_error is not None:
            # Phase 2: a FRESH transaction, separate from the one that just
            # rolled back — see reconstruction_batch_lib.mark_item_failed's
            # docstring for why this can't be the same session/transaction.
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
