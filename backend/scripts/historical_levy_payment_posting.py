#!/usr/bin/env python3
# @featuretrace:financial-onboarding — generic (building-agnostic) poster for the DERIVED
#   historical levy PAYMENT (receipt) records that mirror already-posted levy CHARGES. This is
#   Workstream B increment 2 (the APPLY side of historical_levy_payment_generator.py's increment 1).
# Layer: script
# Data flow: finance.levy_items (posted charges) + core.lots + units (Mongo) ->
#            LevyChargePlan (reconstructed) ->
#            services.reconstruction_generators.historical_levy_payment_generator
#              .build_payment_plan_from_charge_plan() (skip set from active finance.receipts) ->
#            FinancialCoreService.record_historical_payment() + allocate_payment() (Postgres GL).
# Related: backend/scripts/historical_levy_charge_posting.py (the CHARGE-side twin whose
#          dual-control/argparse/session conventions this script follows)
#          backend/services/reconstruction_generators/historical_levy_payment_generator.py
#          backend/services/financial_core/service.py (record_historical_payment, allocate_payment)
#          docs/finances/onboarding-reconstruction-workflow-canonical-spec.md (§4b Workstream B)
#          docs/finances/next-phase-financial-recontruction-demo-bank-eastgate-2021-2026.md
# Toggle: historical_financial_reconstruction, historical_reconstruction_posting
# Collection: units, demo_bank_reconstruction_batches
# Table: finance.levy_items, finance.levy_runs, finance.funds, finance.receipts,
#        finance.receipt_allocations, finance.journal_entries, core.lots
"""Post DERIVED historical levy PAYMENTS (receipts) that mirror already-posted levy CHARGES.

Workstream B increment 2 — the APPLY/posting side of the preview-only generator shipped in
`services/reconstruction_generators/historical_levy_payment_generator.py`. For every posted levy
CHARGE line (`finance.levy_items`) that does not yet have an active receipt, this posts one
derived receipt at EXACTLY that charge's `total_cents` (GST-inclusive), then allocates it — so
`paid == charged, on time` (canonical spec §0), by construction. The amount is copied from the
posted charge, never re-derived, which is precisely why the ~10% GST-mismatch class of bug
(spec §4a #4) cannot recur here.

Why the plan is reconstructed from posted `finance.levy_items`, not the charge generator
--------------------------------------------------------------------------------------------
`historical_levy_charge_generator.generate_historical_levy_charge_plan()` deliberately SKIPS every
period already present in `finance.levy_items` (its `_load_existing_charged_periods` dedup), so once
the charges are posted it returns an EMPTY plan (or raises "everything already charged"). The paid
side must therefore mirror the posted charges themselves — which also makes the posted, approved
ledger the single source of the payment amounts, with no second re-derivation to drift from it.

Discipline (mirrors the CHARGE poster; canonical spec §4a #10, §4b)
---------------------------------------------------------------------
* BUILD is separate from APPLY: `--preview` only reads and prints; `--apply` posts.
* Dual control: `--apply` requires an *already-approved* reconstruction batch (this script never
  approves one itself) whose `approved_by` differs from `--executed-by`. The batch is the audited
  authorisation vehicle; the payment amounts come from the posted charges it produced.
* Non-canonical paid residue MUST be reversed first (spec §4a #10). `--apply` refuses without
  `--residue-reversed-ref` — an explicit, audited operator attestation (e.g. the id/path of the
  reversal script's audit record) that any non-canonical receipts have been reversed. There is no
  East-Gate-specific auto-detection here (that would be building-specific business logic, forbidden
  by the canonical spec) — the skip set below is the structural backstop that makes double-posting
  impossible regardless.
* No double-post: the skip set counts only ACTIVE (posted, non-reversed) receipts, so a reversed
  residue row correctly frees its period for a clean re-post, while a genuine existing receipt
  blocks it.
* Closed periods: reconstruction dates fall in closed accounting years (2021-2025), so posting
  goes through `record_historical_payment()` — the only path permitted to resolve a closed period,
  gated by the same `HistoricalPostingAuthorisation` the charge poster uses.
* Integer cents only; a post-apply reconciliation asserts the posted total equals the planned total.

Usage:
    cd backend

    # Preview (no writes): what derived receipts WOULD be posted for the posted charges.
    venv/bin/python3 scripts/historical_levy_payment_posting.py \\
        --building-id 13195 --from-year 2021 --to-year 2025 --preview

    # Apply: post the derived receipts for an APPROVED batch, after reversing residue.
    venv/bin/python3 scripts/historical_levy_payment_posting.py \\
        --building-id 13195 --batch-id <uuid> --executed-by <uuid> \\
        --residue-reversed-ref "reverse_duplicate_levy_payments_20260803.py run 2026-08-05" --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date
from uuid import UUID

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from scripts.migrations.migration_027_randomize_east_gate_demo_bank_levies import (  # noqa: E402
    _mongo_url_and_db,
)
from services.financial_core.adapters.db_postgres.ledger_repo import (  # noqa: E402
    PostgresLedgerRepository,
)
from services.financial_core.adapters.db_postgres.outbox_repo import (  # noqa: E402
    PostgresOutboxRepository,
)
from services.financial_core.domain.entities import (  # noqa: E402
    AllocatePaymentCommand,
    HistoricalPostingAuthorisation,
    PaymentChannel,
    RecordHistoricalPaymentCommand,
    SchemeRef,
)
from services.financial_core.service import FinancialCoreService  # noqa: E402
from services.reconstruction_generators.historical_levy_charge_generator import (  # noqa: E402
    LevyChargeLine,
    LevyChargePlan,
)
from services.reconstruction_generators.historical_levy_payment_generator import (  # noqa: E402
    build_payment_plan_from_charge_plan,
    summarize_by_year_fund_period,
)

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"

# Historical reconstructed levy payments have no known real-world channel (the source is an
# approved reconstruction of "paid on time", not an observed bank line). EFT is the neutral
# assumed channel — never a bank-feed-specific value that would imply reconciled evidence.
_RECONSTRUCTED_PAYMENT_CHANNEL = PaymentChannel.EFT


def _fund_bucket(fund_type: str) -> str:
    """Map a finance.funds.fund_type to the two-bucket ("admin"|"sinking") vocabulary the levy
    generators and the payment plan's dedup key use. Pure — unit-tested."""
    return "sinking" if fund_type in ("capital_works", "sinking") else "admin"


def _period_label(quarter_no: int) -> str:
    """A nominal period label from the run's 1-based period index. Only cosmetic / part of the
    payment idempotency key — the load-bearing dedup is by quarter_no (see receipt_dedup_key), so
    this stays correct for non-quarterly frequencies even though the "Q" prefix is nominal. Pure."""
    return f"Q{quarter_no}" if quarter_no else "P0"


async def _resolve_scheme(session, building_id: str) -> tuple[str, str]:
    """building_id IS the Postgres scheme_number for every building (CLAUDE.md Multi-Tenant Rules)."""
    # Bind the (hardcoded, non-user-controlled) bypass id as a parameter rather than f-string it —
    # defensive hygiene so the pattern is never copied with real input later.
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": _TENANT_BYPASS_ID}
    )
    result = await session.execute(
        text("SELECT tenant_id::text, scheme_id::text FROM core.schemes WHERE lower(scheme_number) = lower(:num)"),
        {"num": building_id},
    )
    row = result.first()
    if row is None:
        raise SystemExit(f"No core.schemes row found for building_id={building_id!r}")
    return row[0], row[1]


async def _get_batch_doc(mongo_db, building_id: str, batch_id: str) -> dict:
    doc = await mongo_db.demo_bank_reconstruction_batches.find_one(
        {"building_id": building_id, "batch_id": batch_id}
    )
    if doc is None:
        raise SystemExit(f"No reconstruction batch {batch_id!r} found for building_id={building_id!r}")
    return doc


async def _load_active_receipt_periods(session, scheme_id: str) -> set[tuple[UUID, int, str, int]]:
    """The (lot_id, year, fund, quarter_no) tuples that already have an ACTIVE receipt — the skip
    set consumed by build_payment_plan_from_charge_plan(existing_receipt_keys=...).

    Only posted, non-reversed receipts count (same active-receipt predicate as
    ledger_repo.find_active_receipt_by_bank_transaction): a reversed residue row therefore frees
    its period for a clean re-post, while a genuine existing receipt blocks a double-post.
    """
    rows = await session.execute(
        text(
            """
            SELECT DISTINCT r.lot_id::text, lr.financial_year, f.fund_type::text,
                            COALESCE(lr.quarter_no, 0)
            FROM finance.receipts r
            JOIN finance.journal_entries je ON je.journal_entry_id = r.journal_entry_id
            JOIN finance.receipt_allocations ra ON ra.receipt_id = r.receipt_id
            JOIN finance.levy_items li ON li.levy_item_id = ra.levy_item_id
            JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
            JOIN finance.funds f ON f.fund_id = li.fund_id
            WHERE r.scheme_id = :sid
              AND je.status = 'posted'
              AND je.reversal_of_id IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM finance.journal_entries rev
                  WHERE rev.reversal_of_id = je.journal_entry_id AND rev.status = 'posted'
              )
            """
        ),
        {"sid": str(scheme_id)},
    )
    out: set[tuple[UUID, int, str, int]] = set()
    for lot_id_text, financial_year, fund_type, quarter_no in rows.all():
        out.add((UUID(lot_id_text), int(financial_year), _fund_bucket(fund_type), int(quarter_no or 0)))
    return out


async def _load_unit_number_by_lot_id(mongo_db, session, scheme_id: str, building_id: str) -> dict[UUID, str]:
    """Bridge core.lots.lot_number <-> Mongo units.unit_number (same strip-"LOT"-prefix join the
    charge generator's _load_existing_charged_periods uses). Falls back to the plain lot_number for
    any lot with no matching unit doc — unit_number is cosmetic on the payment line, never a key."""
    lot_rows = await session.execute(
        text("SELECT lot_id::text, lot_number FROM core.lots WHERE scheme_id = :sid"),
        {"sid": str(scheme_id)},
    )
    plain_by_lot_id: dict[UUID, str] = {}
    for lot_id_text, lot_number in lot_rows.all():
        raw = str(lot_number or "").strip()
        plain = raw[3:] if raw.upper().startswith("LOT") else raw
        plain_by_lot_id[UUID(lot_id_text)] = plain

    mongo_docs = await mongo_db.units.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}},
        {"unit_number": 1, "lot_number": 1, "_id": 0},
    ).to_list(None)
    unit_number_by_plain: dict[str, str] = {}
    for doc in mongo_docs:
        raw = str(doc.get("lot_number") or "").strip()
        plain = raw[3:] if raw.upper().startswith("LOT") else raw
        unit_number = str(doc.get("unit_number") or "").strip()
        if plain and unit_number:
            unit_number_by_plain[plain] = unit_number

    return {
        lot_id: unit_number_by_plain.get(plain, plain)
        for lot_id, plain in plain_by_lot_id.items()
    }


async def _reconstruct_charge_plan_from_posted_items(
    *, mongo_db, session, scheme_ref: SchemeRef, building_id: str,
    from_year: int, to_year: int, as_of_date: date,
) -> LevyChargePlan:
    """Rebuild a LevyChargePlan whose `.lines` are the ALREADY-POSTED charges in finance.levy_items,
    so build_payment_plan_from_charge_plan() derives one receipt per posted charge at its exact
    total. Charges due after as_of_date go to planned_not_posted (no payment fabricated for a period
    not yet due — mirrors the charge generator's own as_of gate)."""
    unit_by_lot = await _load_unit_number_by_lot_id(mongo_db, session, str(scheme_ref.scheme_id), building_id)

    rows = await session.execute(
        text(
            """
            SELECT li.lot_id::text, li.owner_party_id::text, lr.financial_year, f.fund_type::text,
                   lr.levy_run_type, COALESCE(lr.quarter_no, 0), lr.issue_date, lr.due_date,
                   li.principal_cents, li.gst_cents, li.owner_resolution_status
            FROM finance.levy_items li
            JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
            JOIN finance.funds f ON f.fund_id = li.fund_id
            WHERE li.scheme_id = :sid AND lr.financial_year BETWEEN :fy AND :ty
            ORDER BY lr.financial_year, COALESCE(lr.quarter_no, 0), f.fund_type, li.lot_id
            """
        ),
        {"sid": str(scheme_ref.scheme_id), "fy": str(from_year), "ty": str(to_year)},
    )

    plan = LevyChargePlan(building_id=building_id, from_year=from_year, to_year=to_year, as_of_date=as_of_date)
    for row in rows.all():
        (lot_id_text, owner_party_text, financial_year, fund_type, levy_run_type, quarter_no,
         issue_date, due_date, principal_cents, gst_cents, owner_status) = row
        total_cents = int(principal_cents) + int(gst_cents)
        if total_cents <= 0:
            continue
        lot_id = UUID(lot_id_text)
        line = LevyChargeLine(
            unit_number=unit_by_lot.get(lot_id, lot_id_text),
            lot_id=lot_id,
            owner_party_id=UUID(owner_party_text),
            owner_resolution_status=owner_status or "resolved",
            year=int(financial_year),
            fund_type=_fund_bucket(fund_type),
            levy_type=levy_run_type or "ordinary",
            period=_period_label(int(quarter_no or 0)),
            quarter_no=int(quarter_no or 0),
            issue_date=issue_date,
            due_date=due_date,
            uoe=0.0,          # not consumed by the payment generator (amount is copied from total_cents)
            total_uoe=0.0,    # "
            principal_cents=int(principal_cents),
            gst_cents=int(gst_cents),
            total_cents=total_cents,
            source_annual_control=f"finance.levy_items (posted charge, year={financial_year})",
        )
        if due_date > as_of_date:
            plan.planned_not_posted.append(line)
        else:
            plan.lines.append(line)
    return plan


async def run_preview(*, building_id: str, from_year: int, to_year: int, as_of_date: date) -> None:
    mongo_uri, mongo_db_name = _mongo_url_and_db()
    mongo_client = AsyncIOMotorClient(mongo_uri)
    mongo_db = mongo_client[mongo_db_name]

    async with async_session_context() as session:
        tenant_id, scheme_id = await _resolve_scheme(session, building_id)
        await set_tenant(session, tenant_id)
        scheme_ref = SchemeRef(tenant_id=UUID(tenant_id), scheme_id=UUID(scheme_id))

        charge_plan = await _reconstruct_charge_plan_from_posted_items(
            mongo_db=mongo_db, session=session, scheme_ref=scheme_ref, building_id=building_id,
            from_year=from_year, to_year=to_year, as_of_date=as_of_date,
        )
        skip_set = await _load_active_receipt_periods(session, scheme_id)

    mongo_client.close()

    payment_plan = build_payment_plan_from_charge_plan(charge_plan, existing_receipt_keys=frozenset(skip_set))
    groups = summarize_by_year_fund_period(payment_plan)
    print(f"Preview for building_id={building_id!r}, years {from_year}-{to_year}, as_of={as_of_date}:")
    print(f"  {len(charge_plan.lines)} posted charge line(s) due on/before as_of; "
          f"{len(payment_plan.lines)} derived receipt(s) to post, "
          f"{payment_plan.skipped_existing_receipt} skipped (already have an active receipt).")
    print(f"  Total to post: {payment_plan.total_amount_cents} cents "
          f"(paid == charged, by construction).")
    for g in groups:
        print(f"  {g['year']} {g['fund_type']:8s} {g['period']:3s}: {g['receipt_count']:3d} receipt(s), "
              f"{g['total_cents']:>10d} cents")
    for w in payment_plan.warnings:
        print(f"  WARNING: {w}")
    print("\nPreview only — no receipts were posted. Use --apply (with an approved batch and "
          "--residue-reversed-ref) to post.")


async def run_apply(
    *, building_id: str, batch_id: str, executed_by: UUID, residue_reversed_ref: str,
    from_year: int, to_year: int, as_of_date: date, is_test_data: bool,
) -> None:
    # ---- Dual-control + residue guards: all BEFORE any Postgres session is opened, so they are
    #      unit-testable by patching only the Mongo client + _get_batch_doc (see the tests).
    if not residue_reversed_ref or not residue_reversed_ref.strip():
        raise SystemExit(
            "--apply requires --residue-reversed-ref: an audited reference (e.g. the reversal "
            "script's run id/path) attesting that any non-canonical paid residue has been reversed "
            "first (canonical spec §4a #10). Refusing to post derived payments without it."
        )

    mongo_uri, mongo_db_name = _mongo_url_and_db()
    mongo_client = AsyncIOMotorClient(mongo_uri)
    mongo_db = mongo_client[mongo_db_name]

    batch_doc = await _get_batch_doc(mongo_db, building_id, batch_id)
    if batch_doc.get("status") != "approved":
        raise SystemExit(
            f"Batch {batch_id!r} has status={batch_doc.get('status')!r}; --apply requires "
            "'approved' — review and approve it through the existing UI/API first. This script "
            "never progresses a batch's status itself."
        )
    approved_by_raw = batch_doc.get("approved_by")
    if not approved_by_raw:
        raise SystemExit(f"Batch {batch_id!r} has no approved_by — cannot establish dual control.")
    approved_by = UUID(str(approved_by_raw))
    if approved_by == executed_by:
        raise SystemExit(
            f"--executed-by ({executed_by}) must differ from the batch's approved_by "
            f"({approved_by}) — dual control requires two distinct identities."
        )

    reason = (
        f"Historical levy PAYMENT posting for building {building_id}, batch {batch_id}: derived "
        f"receipts mirroring posted charges (paid == charged). approved_by={approved_by}, "
        f"residue_reversed_ref={residue_reversed_ref!r}."
    )

    async with async_session_context() as session:
        tenant_id, scheme_id = await _resolve_scheme(session, building_id)
        await set_tenant(session, tenant_id)
        scheme_ref = SchemeRef(tenant_id=UUID(tenant_id), scheme_id=UUID(scheme_id))

        charge_plan = await _reconstruct_charge_plan_from_posted_items(
            mongo_db=mongo_db, session=session, scheme_ref=scheme_ref, building_id=building_id,
            from_year=from_year, to_year=to_year, as_of_date=as_of_date,
        )
        skip_set = await _load_active_receipt_periods(session, scheme_id)
        payment_plan = build_payment_plan_from_charge_plan(
            charge_plan, existing_receipt_keys=frozenset(skip_set)
        )

        if not payment_plan.lines:
            print(f"Nothing to post for building {building_id!r}: every posted charge in "
                  f"{from_year}-{to_year} already has an active receipt "
                  f"({payment_plan.skipped_existing_receipt} skipped).")
            mongo_client.close()
            return

        ledger = PostgresLedgerRepository(session)
        outbox = PostgresOutboxRepository(session)
        svc = FinancialCoreService(ledger, outbox)

        posted_total_cents = 0
        # payment_plan.lines preserve the charge plan's (year, quarter_no, fund, lot) ordering, so
        # a lot's receipts post oldest-first — matching allocate_payment()'s FIFO over that lot's
        # open levy items, so each receipt settles the period it mirrors.
        print(f"Posting {len(payment_plan.lines)} derived receipt(s) "
              f"({payment_plan.skipped_existing_receipt} skipped as already-receipted)...")
        for line in payment_plan.lines:
            authorisation = HistoricalPostingAuthorisation(
                reconstruction_batch_id=UUID(batch_id),
                reason=reason,
                approved_by=approved_by,
                executed_by=executed_by,
                approval_reference=f"batch:{batch_id}:approved_by:{approved_by}",
                evidence_reference=residue_reversed_ref,
            )
            cmd = RecordHistoricalPaymentCommand(
                scheme_ref=scheme_ref,
                payer_party_id=line.owner_party_id,
                lot_id=line.lot_id,
                channel=_RECONSTRUCTED_PAYMENT_CHANNEL,
                received_on=line.payment_date,
                amount_cents=line.amount_cents,
                authorisation=authorisation,
                external_reference=line.idempotency_key,
                idempotency_key=line.idempotency_key,
                is_test_data=is_test_data,
                metadata={
                    "source_tag": line.source_tag,
                    "derivation_type": line.derivation_type,
                    "financial_year": line.year,
                    "fund_type": line.fund_type,
                    "period": line.period,
                    "quarter_no": line.quarter_no,
                    "residue_reversed_ref": residue_reversed_ref,
                },
                reconstruction_batch_id=UUID(batch_id),
            )
            receipt = await svc.record_historical_payment(cmd)
            await svc.allocate_payment(
                AllocatePaymentCommand(scheme_ref=scheme_ref, receipt_id=receipt.receipt_id)
            )
            posted_total_cents += line.amount_cents

        if posted_total_cents != payment_plan.total_amount_cents:
            raise SystemExit(
                f"Post-apply integrity check failed: posted {posted_total_cents} cents but the "
                f"payment plan totalled {payment_plan.total_amount_cents} cents."
            )

    mongo_client.close()
    print(f"Posted {len(payment_plan.lines)} receipt(s), {posted_total_cents} cents. "
          "Matches the derived plan exactly. Done.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--building-id", required=True)
    parser.add_argument("--from-year", type=int, required=True)
    parser.add_argument("--to-year", type=int, required=True)
    parser.add_argument("--as-of-date", type=date.fromisoformat, default=None)
    parser.add_argument("--batch-id")
    parser.add_argument("--executed-by", type=UUID)
    parser.add_argument("--residue-reversed-ref",
                        help="Audited attestation that non-canonical paid residue was reversed first "
                             "(required for --apply; canonical spec §4a #10).")
    parser.add_argument("--is-test-data", action="store_true")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true", help="Read-only preview of derived receipts. No writes.")
    mode.add_argument("--apply", action="store_true", help="Post the derived receipts for an approved batch.")
    args = parser.parse_args()

    if args.preview:
        asyncio.run(run_preview(
            building_id=args.building_id, from_year=args.from_year, to_year=args.to_year,
            as_of_date=args.as_of_date or date.today(),
        ))
    elif args.apply:
        if not args.batch_id or not args.executed_by:
            parser.error("--apply requires --batch-id and --executed-by")
        asyncio.run(run_apply(
            building_id=args.building_id, batch_id=args.batch_id, executed_by=args.executed_by,
            residue_reversed_ref=args.residue_reversed_ref or "",
            from_year=args.from_year, to_year=args.to_year,
            as_of_date=args.as_of_date or date.today(), is_test_data=args.is_test_data,
        ))


if __name__ == "__main__":
    main()
