#!/usr/bin/env python3
# @featuretrace:financial-onboarding — generic (building-agnostic) poster for the historical
#   levy CHARGE lines carried inside an APPROVED reconstruction batch's manifest
#   (ManifestLevyChargeLine), and the standalone unit_levy_ledger projection rebuild.
# Layer: script
# Data flow: demo_bank_reconstruction_manifests (Mongo, approved batch's levy_charge_lines) ->
#            FinancialCoreService.create_historical_levy() (Postgres GL) ->
#            services.unit_levy_ledger_service.rebuild_ledger_projection() (Mongo projection).
# Related: backend/routers/financial_onboarding.py (_build_manifest_for_batch — where
#          levy_charge_lines are generated and attached to the SAME manifest as the Demo Bank
#          income/expense rows)
#          backend/services/reconstruction_generators/historical_levy_charge_generator.py
#          backend/services/financial_core/service.py (create_historical_levy)
#          backend/services/unit_levy_ledger_service.py
#          backend/scripts/east_gate_historical_expense_posting.py (dual-control/argparse
#          conventions this script follows, generalised to any building)
#          docs/finances/next-phase-financial-recontruction-demo-bank-eastgate-2021-2026.md
#          docs/finances/reconcilation-2021-2022-finances-plan01.md
#          docs/finances/reconcilation-2021-2022-finances-plan02.md
#          tasks/GAP-FIN-026-historical-levy-charge-posting-pipeline.md
"""Post an APPROVED reconstruction batch's levy CHARGE lines into PostgreSQL, or rebuild the
unit_levy_ledger Mongo projection from already-posted charges.

This script does NOT create or approve a batch, and NEVER manufactures its own approval —
that would weaken the dual-control model this whole pipeline depends on
(docs/finances/reconcilation-2021-2022-finances-plan02.md point 2). A batch reaches
`approved` status entirely through the existing UI/API (create -> extract -> generate-preview
-> submit-review -> review -> approve, one dual-control step at a time, distinct reviewer and
approver). This script's `--apply` mode only ever posts what a human has already reviewed and
approved, and cross-checks that the posted totals reproduce that approved manifest exactly.

Usage:
    cd backend

    # Pure, batch-INDEPENDENT sanity-check preview (no writes) — recomputes a fresh plan
    # directly from annual_levies/units, NOT the same thing as viewing an actual batch's own
    # persisted (and possibly already-approved) manifest. Use the existing
    # GET .../reconstruction-batches/{batch_id}/manifest endpoint for that.
    venv/bin/python3 scripts/historical_levy_charge_posting.py \\
        --building-id 13195 --from-year 2021 --to-year 2026 --preview

    # Post an APPROVED batch's levy charges (requires batch.status == "approved").
    venv/bin/python3 scripts/historical_levy_charge_posting.py \\
        --building-id 13195 --batch-id <uuid> --executed-by <uuid> --apply

    # Rebuild the unit_levy_ledger Mongo projection from already-posted finance.levy_items —
    # no batch, no HistoricalPostingAuthorisation (posts no new accounting entry).
    venv/bin/python3 scripts/historical_levy_charge_posting.py \\
        --building-id 13195 --from-year 2021 --to-year 2026 --rebuild-ledger-projection
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict
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
    CreateHistoricalLevyCommand,
    HistoricalPostingAuthorisation,
    LotLevySpec,
    SchemeRef,
)
from services.financial_core.genesis import resolve_scheme_fund_ids  # noqa: E402
from services.financial_core.service import FinancialCoreService  # noqa: E402
from services.reconstruction_generators.historical_levy_charge_generator import (  # noqa: E402
    LevyChargeGenerationError,
    generate_historical_levy_charge_plan,
    summarize_by_year_fund_period,
)
from services.unit_levy_ledger_service import rebuild_ledger_projection  # noqa: E402

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"


async def _resolve_scheme(session, building_id: str) -> tuple[str, str]:
    """Generic scheme-number lookup — building_id IS the Postgres scheme_number for every
    building in this repo (see CLAUDE.md's Multi-Tenant Rules table)."""
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


def _group_charge_lines(charge_lines: list[dict]) -> dict[tuple, list[dict]]:
    """Group by (financial_year, fund_type, levy_type, period) — one create_historical_levy()
    call per group, matching that command's single-period shape (one quarter_no/issue_date/
    due_date per call). Per docs/finances/reconcilation-2021-2022-finances-plan02.md point 3."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for line in charge_lines:
        key = (line["financial_year"], line["fund_type"], line["levy_type"], line["period"])
        groups[key].append(line)
    return groups


async def run_apply(*, building_id: str, batch_id: str, executed_by: UUID, is_test_data: bool) -> None:
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

    manifest_doc = await _get_latest_manifest_doc(mongo_db, building_id, batch_id)
    if manifest_doc.get("manifest_hash") != batch_doc.get("manifest_hash"):
        raise SystemExit(
            "Manifest has changed since the batch was reviewed/approved — re-run review before "
            "applying (same defensive check the /approve endpoint itself performs)."
        )
    charge_lines = manifest_doc.get("levy_charge_lines") or []
    if not charge_lines:
        print(f"Batch {batch_id!r}'s manifest has zero levy_charge_lines — nothing to post.")
        mongo_client.close()
        return
    expected_total_cents = sum(int(l["total_cents"]) for l in charge_lines)
    manifest_recorded_total = (manifest_doc.get("calculation_configuration") or {}).get("levy_charge_total_cents")
    if manifest_recorded_total is not None and int(manifest_recorded_total) != expected_total_cents:
        raise SystemExit(
            f"Manifest integrity check failed: levy_charge_lines sum to {expected_total_cents} "
            f"cents but calculation_configuration.levy_charge_total_cents recorded "
            f"{manifest_recorded_total} — refusing to post against a manifest that disagrees "
            "with itself."
        )

    reason = (
        f"Historical levy charge posting for building {building_id}, batch {batch_id}: "
        f"{len(charge_lines)} approved charge line(s), reviewed_by={batch_doc.get('reviewed_by')}, "
        f"approved_by={approved_by}."
    )

    async with async_session_context() as session:
        tenant_id, scheme_id = await _resolve_scheme(session, building_id)
        await set_tenant(session, tenant_id)
        scheme_ref = SchemeRef(tenant_id=UUID(tenant_id), scheme_id=UUID(scheme_id))
        fund_ids = await resolve_scheme_fund_ids(session, scheme_ref)

        ledger = PostgresLedgerRepository(session)
        outbox = PostgresOutboxRepository(session)
        svc = FinancialCoreService(ledger, outbox)

        posted_lot_years: set[tuple[str, str]] = set()
        posted_total_cents = 0
        groups = _group_charge_lines(charge_lines)
        print(f"Posting {len(groups)} levy run(s) for {len(charge_lines)} charge line(s)...")

        for (financial_year, fund_type, levy_type, period), lines in sorted(groups.items()):
            authorisation = HistoricalPostingAuthorisation(
                reconstruction_batch_id=UUID(batch_id),
                reason=reason,
                approved_by=approved_by,
                executed_by=executed_by,
                approval_reference=f"batch:{batch_id}:approved_by:{approved_by}",
            )
            lot_levies = [
                LotLevySpec(
                    lot_id=UUID(l["lot_id"]),
                    owner_party_id=UUID(l["owner_party_id"]),
                    fund_id=fund_ids[fund_type],
                    principal_cents=int(l["principal_cents"]),
                    gst_cents=int(l["gst_cents"]),
                    owner_resolution_status=l.get("owner_resolution_status", "resolved"),
                )
                for l in lines
            ]
            first = lines[0]
            cmd = CreateHistoricalLevyCommand(
                scheme_ref=scheme_ref,
                financial_year=financial_year,
                quarter_no=int(first["quarter_no"]),
                issue_date=date.fromisoformat(first["issue_date"]),
                due_date=date.fromisoformat(first["due_date"]),
                lot_levies=lot_levies,
                authorisation=authorisation,
                idempotency_key=f"historical-levy:{building_id}:{financial_year}:{fund_type}:{levy_type}:{period}",
                is_test_data=is_test_data,
                levy_type=levy_type,
            )
            posted_items = await svc.create_historical_levy(cmd)
            posted_total_cents += sum(i.principal_cents + i.gst_cents for i in posted_items)
            for l in lines:
                posted_lot_years.add((l["unit_number"], financial_year))
            print(
                f"  {financial_year} {fund_type} {period}: {len(posted_items)} lot(s) posted "
                f"(run reused/created)."
            )

        if posted_total_cents != expected_total_cents:
            raise SystemExit(
                f"Post-apply integrity check failed: posted {posted_total_cents} cents but the "
                f"approved manifest's charge lines sum to {expected_total_cents} cents."
            )
        print(f"Posted total: {posted_total_cents} cents. Matches approved manifest exactly.")

        print(f"Refreshing unit_levy_ledger projection for {len(posted_lot_years)} lot/year(s)...")
        lot_id_by_unit = {l["unit_number"]: UUID(l["lot_id"]) for l in charge_lines}
        for unit_number, year in sorted(posted_lot_years):
            await rebuild_ledger_projection(
                mongo_db=mongo_db, pg_session=session, scheme_id=UUID(scheme_id),
                building_id=building_id, unit_number=unit_number,
                lot_id=lot_id_by_unit[unit_number], year=year,
            )

    mongo_client.close()
    print("Done.")


async def run_preview(*, building_id: str, from_year: int, to_year: int, as_of_date: date) -> None:
    mongo_uri, mongo_db_name = _mongo_url_and_db()
    mongo_client = AsyncIOMotorClient(mongo_uri)
    mongo_db = mongo_client[mongo_db_name]

    async with async_session_context() as session:
        tenant_id, scheme_id = await _resolve_scheme(session, building_id)
        await set_tenant(session, tenant_id)
        scheme_ref = SchemeRef(tenant_id=UUID(tenant_id), scheme_id=UUID(scheme_id))
        try:
            plan = await generate_historical_levy_charge_plan(
                mongo_db=mongo_db, pg_session=session, scheme_ref=scheme_ref, building_id=building_id,
                from_year=from_year, to_year=to_year, as_of_date=as_of_date,
            )
        except LevyChargeGenerationError as exc:
            mongo_client.close()
            raise SystemExit(f"Nothing to preview: {exc}")

    mongo_client.close()
    groups = summarize_by_year_fund_period(plan)
    print(f"Preview for building_id={building_id!r}, years {from_year}-{to_year}, as_of={as_of_date}:")
    print(f"  {len(plan.lines)} postable charge line(s), {len(plan.planned_not_posted)} planned_not_posted "
          f"(due after as_of_date), {plan.skipped_already_charged} already-charged (skipped).")
    print(f"  Total: {plan.total_principal_cents} cents principal + {plan.total_gst_cents} cents GST.")
    for g in groups:
        print(
            f"  {g['year']} {g['fund_type']:8s} {g['period']:3s}: {g['lot_count']:3d} lot(s), "
            f"{g['total_cents']:>10d} cents, unresolved_owner={g['unresolved_owner_count']}"
        )
    for w in plan.warnings:
        print(f"  WARNING: {w}")
    print("\nThis is a batch-INDEPENDENT sanity-check preview only — it does not reflect (and is "
          "not a substitute for) an actual batch's own persisted, reviewable manifest.")


async def run_rebuild_ledger_projection(*, building_id: str, from_year: int, to_year: int) -> None:
    mongo_uri, mongo_db_name = _mongo_url_and_db()
    mongo_client = AsyncIOMotorClient(mongo_uri)
    mongo_db = mongo_client[mongo_db_name]

    async with async_session_context() as session:
        tenant_id, scheme_id = await _resolve_scheme(session, building_id)
        await set_tenant(session, tenant_id)
        rows = await session.execute(
            text(
                """
                SELECT DISTINCT lot.lot_number, lr.financial_year
                FROM finance.levy_items li
                JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                JOIN core.lots lot ON lot.lot_id = li.lot_id
                WHERE li.scheme_id = :sid AND lr.financial_year BETWEEN :fy AND :ty
                """
            ),
            {"sid": scheme_id, "fy": str(from_year), "ty": str(to_year)},
        )
        plain_lot_numbers_by_year: dict[str, set[str]] = defaultdict(set)
        for lot_number, financial_year in rows.all():
            plain_lot_numbers_by_year[financial_year].add(str(lot_number))

        mongo_units = await mongo_db.units.find(
            {"building_id": building_id, "is_test_data": {"$ne": True}},
            {"unit_number": 1, "lot_number": 1, "_id": 0},
        ).to_list(None)
        lot_id_rows = await session.execute(
            text("SELECT lot_id::text, lot_number FROM core.lots WHERE scheme_id = :sid"), {"sid": scheme_id},
        )
        lot_id_by_plain_number = {str(n): UUID(i) for i, n in lot_id_rows.all()}
        unit_number_by_plain_lot_number: dict[str, str] = {}
        for doc in mongo_units:
            raw_lot_number = str(doc.get("lot_number") or "").strip()
            plain = raw_lot_number[3:] if raw_lot_number.upper().startswith("LOT") else raw_lot_number
            unit_number = str(doc.get("unit_number") or "").strip()
            if plain and unit_number:
                unit_number_by_plain_lot_number[plain] = unit_number

        refreshed = 0
        for financial_year, plain_lot_numbers in sorted(plain_lot_numbers_by_year.items()):
            for plain_lot_number in sorted(plain_lot_numbers):
                unit_number = unit_number_by_plain_lot_number.get(plain_lot_number)
                lot_id = lot_id_by_plain_number.get(plain_lot_number)
                if unit_number is None or lot_id is None:
                    continue
                await rebuild_ledger_projection(
                    mongo_db=mongo_db, pg_session=session, scheme_id=UUID(scheme_id),
                    building_id=building_id, unit_number=unit_number, lot_id=lot_id, year=financial_year,
                )
                refreshed += 1

    mongo_client.close()
    print(f"Rebuilt unit_levy_ledger projection for {refreshed} lot/year row(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--building-id", required=True)
    parser.add_argument("--from-year", type=int)
    parser.add_argument("--to-year", type=int)
    parser.add_argument("--as-of-date", type=date.fromisoformat, default=None)
    parser.add_argument("--batch-id")
    parser.add_argument("--executed-by", type=UUID)
    parser.add_argument("--is-test-data", action="store_true")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true", help="Batch-independent sanity-check preview. No writes.")
    mode.add_argument("--apply", action="store_true", help="Post an approved batch's levy charges.")
    mode.add_argument("--rebuild-ledger-projection", action="store_true", dest="rebuild_projection")
    args = parser.parse_args()

    if args.preview:
        if args.from_year is None or args.to_year is None:
            parser.error("--preview requires --from-year and --to-year")
        asyncio.run(run_preview(
            building_id=args.building_id, from_year=args.from_year, to_year=args.to_year,
            as_of_date=args.as_of_date or date.today(),
        ))
    elif args.apply:
        if not args.batch_id or not args.executed_by:
            parser.error("--apply requires --batch-id and --executed-by")
        asyncio.run(run_apply(
            building_id=args.building_id, batch_id=args.batch_id, executed_by=args.executed_by,
            is_test_data=args.is_test_data,
        ))
    elif args.rebuild_projection:
        if args.from_year is None or args.to_year is None:
            parser.error("--rebuild-ledger-projection requires --from-year and --to-year")
        asyncio.run(run_rebuild_ledger_projection(
            building_id=args.building_id, from_year=args.from_year, to_year=args.to_year,
        ))


if __name__ == "__main__":
    main()
