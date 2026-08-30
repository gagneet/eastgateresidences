#!/usr/bin/env python3
# @featuretrace:financial-onboarding — generic (building-agnostic) poster for the historical
#   category EXPENSE (debit) lines carried inside an APPROVED reconstruction batch's manifest,
#   mirroring historical_levy_charge_posting.py's pattern for the expense side of the ledger.
# Layer: script
# Data flow: demo_bank_reconstruction_manifests (Mongo, approved batch's debit transactions) ->
#            FinancialCoreService.record_historical_expense() (Postgres GL).
# Related: backend/services/reconstruction_generators/expense_category_generator.py
#          backend/scripts/historical_levy_charge_posting.py (pattern this script mirrors)
#          backend/scripts/east_gate_historical_expense_posting.py (the bespoke, East-Gate-only
#          predecessor this generalises — that script's already-posted GL entries stand and are
#          NOT reversed or reposted by this one; this exists for buildings that don't have one)
#          tasks/GAP-FIN-035-collection-rate-2026-parity-expense-pipeline.md (Item 4)
"""Post an APPROVED reconstruction batch's category-expense (debit) lines into PostgreSQL as
historical GL journal entries, via FinancialCoreService.record_historical_expense().

Like historical_levy_charge_posting.py, this script does NOT create or approve a batch, and
NEVER manufactures its own approval. --apply only ever posts what a human has already reviewed
and approved through the existing UI/API, and cross-checks that posted totals reproduce the
approved manifest's expense total exactly.

Sources amounts from the batch's own MONGO MANIFEST (transactions filtered to direction="debit"),
not from Postgres finance.bank_transactions post-sync — the manifest carries the correctly
GST-split amount_ex_gst_cents/gst_cents that expense_category_generator.py already computed,
whereas finance.bank_transactions only stores one flattened amount_cents column. This is the
same reasoning historical_levy_charge_posting.py already applies to levy charges (never
materialised into Demo Bank in the first place).

Usage:
    cd backend

    # Pure, batch-INDEPENDENT sanity-check preview (no writes) — recomputes a fresh plan directly
    # from levy_categories, NOT the same thing as viewing an actual batch's own persisted manifest.
    venv/bin/python3 scripts/historical_expense_posting.py \\
        --building-id 13195 --from-year 2021 --to-year 2026 --preview

    # Post an APPROVED batch's expense debit lines (requires batch.status == "approved").
    venv/bin/python3 scripts/historical_expense_posting.py \\
        --building-id 13195 --batch-id <uuid> --executed-by <uuid> --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
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
from services.financial_core.domain.entities import SchemeRef  # noqa: E402
from services.financial_core.genesis import resolve_scheme_fund_ids  # noqa: E402
from services.financial_core.service import FinancialCoreService  # noqa: E402
from services.reconstruction_generators.expense_category_generator import (  # noqa: E402
    generate_expense_manifest_from_categories,
)
# Shared posting core — the single source of truth for the dual-control / manifest-integrity /
# GST-split / post-apply-total logic. This CLI is now a thin wrapper over it, so it can never
# drift from the in-app endpoint (routers/financial_onboarding.py::post_reconstruction_batch_expenses).
# _extract_category_name is re-exported here so this module's existing unit tests still import it.
from services.reconstruction_generators.expense_posting_service import (  # noqa: E402, F401
    ExpensePostingError,
    _extract_category_name,
    post_expense_plan,
    validate_batch_authorisation,
    validate_manifest_and_extract_debits,
)

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"


async def _resolve_scheme(session, building_id: str) -> tuple[str, str]:
    await session.execute(text(f"SELECT set_config('app.tenant_id', '{_TENANT_BYPASS_ID}', true)"))
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


async def _get_latest_manifest_doc(mongo_db, building_id: str, batch_id: str) -> dict:
    doc = await mongo_db.demo_bank_reconstruction_manifests.find_one(
        {"building_id": building_id, "batch_id": batch_id},
        sort=[("version", -1)],
    )
    if doc is None:
        raise SystemExit(f"No manifest found for batch {batch_id!r} — run generate-preview/submit-review first.")
    return doc


async def run_apply(*, building_id: str, batch_id: str, executed_by: UUID, is_test_data: bool) -> None:
    """CLI wrapper: enforce the CLI's own status policy (batch must be ``approved``), then delegate
    all governance/integrity/posting logic to the shared ``expense_posting_service`` core so this
    can never drift from the in-app endpoint. ExpensePostingError (dual-control, manifest drift,
    self-inconsistent manifest, post-apply mismatch) is surfaced as SystemExit for the CLI."""
    mongo_uri, mongo_db_name = _mongo_url_and_db()
    mongo_client = AsyncIOMotorClient(mongo_uri)
    mongo_db = mongo_client[mongo_db_name]

    try:
        batch_doc = await _get_batch_doc(mongo_db, building_id, batch_id)
        if batch_doc.get("status") != "approved":
            raise SystemExit(
                f"Batch {batch_id!r} has status={batch_doc.get('status')!r}; --apply requires "
                "'approved' — review and approve it through the existing UI/API first. This script "
                "never progresses a batch's status itself."
            )

        # Dual-control gate is manifest-free — reject a bad approver before fetching the manifest.
        try:
            approved_by = validate_batch_authorisation(batch_doc=batch_doc, executed_by=executed_by)
        except ExpensePostingError as exc:
            raise SystemExit(str(exc)) from exc

        manifest_doc = await _get_latest_manifest_doc(mongo_db, building_id, batch_id)
        try:
            plan = validate_manifest_and_extract_debits(
                batch_doc=batch_doc, manifest_doc=manifest_doc,
                approved_by=approved_by, building_id=building_id, batch_id=batch_id,
            )
        except ExpensePostingError as exc:
            raise SystemExit(str(exc)) from exc

        if not plan.expense_rows:
            print(f"Batch {batch_id!r}'s manifest has zero debit (expense) transactions — nothing to post.")
            return

        async with async_session_context() as session:
            tenant_id, scheme_id = await _resolve_scheme(session, building_id)
            await set_tenant(session, tenant_id)
            scheme_ref = SchemeRef(tenant_id=UUID(tenant_id), scheme_id=UUID(scheme_id))
            fund_ids = await resolve_scheme_fund_ids(session, scheme_ref)

            ledger = PostgresLedgerRepository(session)
            outbox = PostgresOutboxRepository(session)
            svc = FinancialCoreService(ledger, outbox)

            print(f"Posting {len(plan.expense_rows)} category-expense line(s)...")
            try:
                result = await post_expense_plan(
                    svc=svc, plan=plan, building_id=building_id, batch_id=batch_id,
                    scheme_ref=scheme_ref, fund_ids=fund_ids,
                    executed_by=executed_by, is_test_data=is_test_data,
                )
            except ExpensePostingError as exc:
                raise SystemExit(str(exc)) from exc

        print(f"Posted total: {result.posted_total_cents} cents. Matches approved manifest exactly.")
        print(f"Done. New postings: {result.posted}. Idempotent replays skipped: {result.replayed}.")
    finally:
        mongo_client.close()


async def run_preview(*, building_id: str, from_year: int, to_year: int) -> None:
    """Batch-INDEPENDENT sanity-check preview — recomputes fresh from levy_categories, pure
    computation, no writes. Not the same as viewing an actual batch's own persisted manifest."""
    mongo_uri, mongo_db_name = _mongo_url_and_db()
    mongo_client = AsyncIOMotorClient(mongo_uri)
    mongo_db = mongo_client[mongo_db_name]

    class _FakeBatch:
        financial_year_start = from_year
        financial_year_end = to_year

    rows, warnings = await generate_expense_manifest_from_categories(
        mongo_db=mongo_db, building_id=building_id, batch=_FakeBatch(),
    )
    mongo_client.close()

    total_by_year: dict[str, int] = {}
    for r in rows:
        total_by_year[r.financial_year] = total_by_year.get(r.financial_year, 0) + r.amount_cents
    grand_total_cents = sum(r.amount_cents for r in rows)

    print(f"Preview for building_id={building_id!r}, years {from_year}-{to_year}:")
    print(f"  {len(rows)} postable expense line(s), total ${grand_total_cents / 100:,.2f}")
    for year in sorted(total_by_year):
        print(f"  {year}: ${total_by_year[year] / 100:,.2f}")
    for w in warnings:
        print(f"  WARNING: {w}")
    print("\nThis is a batch-INDEPENDENT sanity-check preview only — it does not reflect (and is "
          "not a substitute for) an actual batch's own persisted, reviewable manifest.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--building-id", required=True)
    parser.add_argument("--from-year", type=int)
    parser.add_argument("--to-year", type=int)
    parser.add_argument("--batch-id")
    parser.add_argument("--executed-by", type=UUID)
    parser.add_argument("--is-test-data", action="store_true")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true", help="Batch-independent sanity-check preview. No writes.")
    mode.add_argument("--apply", action="store_true", help="Post an approved batch's expense debit lines.")
    args = parser.parse_args()

    if args.preview:
        if args.from_year is None or args.to_year is None:
            parser.error("--preview requires --from-year and --to-year")
        asyncio.run(run_preview(building_id=args.building_id, from_year=args.from_year, to_year=args.to_year))
    elif args.apply:
        if not args.batch_id or not args.executed_by:
            parser.error("--apply requires --batch-id and --executed-by")
        asyncio.run(run_apply(
            building_id=args.building_id, batch_id=args.batch_id, executed_by=args.executed_by,
            is_test_data=args.is_test_data,
        ))


if __name__ == "__main__":
    main()
