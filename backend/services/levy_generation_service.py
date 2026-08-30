# @featuretrace:demo_bank — GST-aware UOE levy-item regeneration + reconciliation report.
# Layer: service
# Data flow: annual_levies (Mongo) + core.lots (Postgres) + finance.levy_items (Postgres) +
#            demo_bank_transactions (Mongo) → build_levy_regeneration_plan() (dry-run report) →
#            [human approval] → apply_levy_regeneration() → FinancialCoreService.bulk_upsert_historical_levy_items().
# Related: backend/services/financial_core/service.py
#          backend/scripts/migrations/migration_027_randomize_east_gate_demo_bank_levies.py
#          backend/services/gst_service.py
#          docs/migration/historical_ledger_reconciliation_plan01.md
#          docs/migration/historical_ledger_reconciliation_plan02.md
# Toggle: historical_financial_reconstruction
# Collection: annual_levies, demo_bank_transactions
# Table: core.lots, finance.levy_items, finance.levy_runs
"""GST-aware UOE levy-item regeneration and reconciliation reporting.

This is the dry-run-first gate for correcting `finance.levy_items` (which today
carries `gst_cents=0` for East Gate — see the historical ledger reconciliation
plans) so it reconciles against the GST-aware Demo Bank reconstruction before
any receipt/journal posting is allowed to run against it.

Reuses the exact apportionment functions migration_027 already uses for the
Demo Bank side (`_allocate_cents`, `_fund_models`, `_load_units`,
`_load_gst_settings`) rather than re-deriving equivalent logic with a different
UOE source — importing directly guarantees structural, not just numerical,
consistency between the two sides being reconciled.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Optional
from uuid import UUID

from scripts.migrations.migration_027_randomize_east_gate_demo_bank_levies import (
    QUARTERS,
    FundModel,
    UnitShare,
    _allocate_cents,
    _fund_models,
    _load_gst_settings,
    _load_levies,
    _load_units,
)
from services.financial_core.domain.entities import (
    BulkUpsertHistoricalLevyItemsCommand,
    HistoricalLevyItemSpec,
    SchemeRef,
)

logger = logging.getLogger(__name__)

_NAMESPACE_URL = "https://strataos.internal/levy-regeneration"


# ── Report shapes ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class YearFundReconciliation:
    year: str
    fund_type: str  # "admin" | "sinking"
    annual_levies_proposed_inc_gst_cents: int
    regenerated_levy_items_total_cents: int
    synthetic_bank_payment_total_cents: int
    existing_levy_items_total_cents: int
    variance_cents: int  # regenerated - annual_levies proposed
    within_tolerance: bool  # abs(variance) <= 1 cent per lot (largest-remainder rounding residual)


@dataclass(frozen=True)
class LevyRegenerationLine:
    year: str
    fund_type: str
    unit_number: str
    lot_id: Optional[UUID]
    principal_cents: int  # new, ex-GST
    gst_cents: int  # new
    existing_principal_cents: Optional[int]
    existing_gst_cents: Optional[int]
    existing_paid_cents: int
    action: Literal["insert", "update", "unchanged", "manual_review_overpaid"]


@dataclass(frozen=True)
class LevyReconciliationReport:
    building_id: str
    from_year: int
    to_year: int
    by_year_fund: list[YearFundReconciliation] = field(default_factory=list)
    lines: list[LevyRegenerationLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    journal_classification_summary: dict[str, int] = field(default_factory=dict)
    totals_reconcile: bool = False


@dataclass(frozen=True)
class LevyApplyResult:
    applied_item_count: int
    total_principal_cents: int
    total_gst_cents: int
    manual_review_count: int


# ── Postgres lookups ─────────────────────────────────────────────────────────

async def _resolve_lot_ids(pg_session, scheme_id: UUID, mongo_db, building_id: str) -> dict[str, UUID]:
    """Mongo units.unit_number (e.g. "TH071") -> Postgres lot_id.

    core.lots.lot_number stores plain digits ("71"), Mongo units.lot_number
    stores "LOT71", and Mongo units.unit_number stores "TH071"/"UA018" — three
    different formats for the same lot. _load_units() (migration_027, reused
    here) keys everything off unit_number, so that's the format callers of this
    function look values up by; the join goes through units.lot_number stripped
    of its "LOT" prefix to reach core.lots.lot_number.
    """
    from sqlalchemy import text

    pg_rows = await pg_session.execute(
        text("SELECT lot_id, lot_number FROM core.lots WHERE scheme_id = :sid"),
        {"sid": str(scheme_id)},
    )
    lot_id_by_plain_number = {str(lot_number): lot_id for lot_id, lot_number in pg_rows.all()}

    mongo_docs = await mongo_db.units.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}},
        {"unit_number": 1, "lot_number": 1, "_id": 0},
    ).to_list(None)

    result: dict[str, UUID] = {}
    for doc in mongo_docs:
        unit_number = str(doc.get("unit_number") or "").strip()
        raw_lot_number = str(doc.get("lot_number") or "").strip()
        plain_lot_number = raw_lot_number[3:] if raw_lot_number.upper().startswith("LOT") else raw_lot_number
        lot_id = lot_id_by_plain_number.get(plain_lot_number)
        if unit_number and lot_id is not None:
            result[unit_number] = lot_id
    return result


async def _resolve_current_levy_items(
    pg_session, scheme_id: UUID, mongo_db, building_id: str
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """(year, fund_type, unit_number) -> {levy_item_id, principal_cents, gst_cents, paid_cents}.

    Keyed by Mongo unit_number (not Postgres lot_number — see _resolve_lot_ids'
    docstring for the three-format mismatch) so lookups from build_levy_regeneration_plan
    (which iterates UnitShare.unit_number) resolve correctly.
    """
    from sqlalchemy import text

    rows = await pg_session.execute(
        text(
            """
            SELECT li.levy_item_id, li.principal_cents, li.gst_cents, li.paid_cents,
                   li.owner_party_id, li.fund_id,
                   lr.financial_year, f.fund_type::text AS fund_type, lot.lot_number
            FROM finance.levy_items li
            JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
            JOIN finance.funds f ON f.fund_id = li.fund_id
            JOIN core.lots lot ON lot.lot_id = li.lot_id
            WHERE li.scheme_id = :sid
            """
        ),
        {"sid": str(scheme_id)},
    )
    by_plain_lot_number: dict[str, list[tuple]] = {}
    for row in rows.all():
        by_plain_lot_number.setdefault(str(row[8]), []).append(row)

    mongo_docs = await mongo_db.units.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}},
        {"unit_number": 1, "lot_number": 1, "_id": 0},
    ).to_list(None)
    unit_number_by_plain_lot_number: dict[str, str] = {}
    for doc in mongo_docs:
        raw_lot_number = str(doc.get("lot_number") or "").strip()
        plain = raw_lot_number[3:] if raw_lot_number.upper().startswith("LOT") else raw_lot_number
        unit_number = str(doc.get("unit_number") or "").strip()
        if plain and unit_number:
            unit_number_by_plain_lot_number[plain] = unit_number

    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for plain_lot_number, rows_for_lot in by_plain_lot_number.items():
        unit_number = unit_number_by_plain_lot_number.get(plain_lot_number)
        if unit_number is None:
            continue
        for levy_item_id, principal, gst, paid, owner_party_id, fund_id, year, fund_type, _lot_number in rows_for_lot:
            fund = "sinking" if fund_type in ("capital_works", "sinking") else "admin"
            out[(str(year), fund, unit_number)] = {
                "levy_item_id": levy_item_id,
                "principal_cents": principal,
                "gst_cents": gst,
                "paid_cents": paid,
                "owner_party_id": owner_party_id,
                "fund_id": fund_id,
            }
    return out


async def _resolve_existing_levy_items_by_lot_id(
    pg_session, scheme_id: UUID
) -> dict[tuple[str, str, UUID], dict[str, Any]]:
    """(year, fund_type, lot_id) -> {owner_party_id, fund_id, ...}.

    Used by apply_levy_regeneration(), which already has each plan line's
    correctly-resolved lot_id (a real UUID — no format ambiguity), so this
    avoids re-threading the Mongo unit_number bridge through the apply path.
    """
    from sqlalchemy import text

    rows = await pg_session.execute(
        text(
            """
            SELECT li.lot_id, li.owner_party_id, li.fund_id,
                   lr.financial_year, f.fund_type::text AS fund_type
            FROM finance.levy_items li
            JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
            JOIN finance.funds f ON f.fund_id = li.fund_id
            WHERE li.scheme_id = :sid
            """
        ),
        {"sid": str(scheme_id)},
    )
    out: dict[tuple[str, str, UUID], dict[str, Any]] = {}
    for lot_id, owner_party_id, fund_id, year, fund_type in rows.all():
        fund = "sinking" if fund_type in ("capital_works", "sinking") else "admin"
        out[(str(year), fund, lot_id)] = {"owner_party_id": owner_party_id, "fund_id": fund_id}
    return out


async def _resolve_fund_ids(pg_session, scheme_id: UUID) -> dict[str, UUID]:
    """"admin"/"sinking" -> fund_id, for the given scheme."""
    from sqlalchemy import text

    rows = await pg_session.execute(
        text("SELECT fund_id, fund_type::text FROM finance.funds WHERE scheme_id = :sid"),
        {"sid": str(scheme_id)},
    )
    out: dict[str, UUID] = {}
    for fund_id, fund_type in rows.all():
        key = "sinking" if fund_type in ("capital_works", "sinking") else fund_type
        out.setdefault(key, fund_id)
    return out


async def _resolve_current_owner_party_ids(pg_session, scheme_id: UUID) -> dict[UUID, UUID]:
    """lot_id -> current owner party_id, via the bitemporal ownership_periods
    table (open-ended period, relationship='owner'). Used only when regenerating
    a levy_item that doesn't exist yet (action="insert") — existing rows already
    carry their own owner_party_id, which is preserved unchanged."""
    from sqlalchemy import text

    rows = await pg_session.execute(
        text(
            """
            SELECT DISTINCT ON (op.lot_id) op.lot_id, op.party_id
            FROM core.ownership_periods op
            JOIN core.lots lot ON lot.lot_id = op.lot_id
            WHERE lot.scheme_id = :sid
              AND op.valid_to IS NULL
              AND op.relationship = 'owner'
            ORDER BY op.lot_id, op.valid_from DESC
            """
        ),
        {"sid": str(scheme_id)},
    )
    return {lot_id: party_id for lot_id, party_id in rows.all()}


async def _resolve_levy_run_ids(pg_session, scheme_id: UUID) -> dict[str, UUID]:
    """financial_year -> levy_run_id (annual roll-up runs, quarter_no=0)."""
    from sqlalchemy import text

    rows = await pg_session.execute(
        text(
            "SELECT levy_run_id, financial_year FROM finance.levy_runs "
            "WHERE scheme_id = :sid AND quarter_no = 0"
        ),
        {"sid": str(scheme_id)},
    )
    return {str(year): run_id for run_id, year in rows.all()}


async def _synthetic_bank_totals_by_year_fund(mongo_db, building_id: str) -> dict[tuple[str, str], int]:
    """(year, fund) -> sum(amount_cents) across every reconstructed Demo Bank row, old or new
    generator. Bug found 2026-08-01: this always matched zero rows for East Gate because it
    filtered `source_type: "synthetic_from_budget"` (the OLDER migration_026/027/
    historical_levy_reconstruction_service.py generator's tag) and grouped by `$levy_year` (that
    same older generator's year field) -- neither exists on the CURRENT generator's output
    (budget_levy_generator.py/expense_category_generator.py, via the vendored package's
    import_historical_reconstruction(), which tags rows `source_type: "historical_reconstruction"`
    and doesn't persist a year field at all beyond free-text in `description`, backfilled here as
    `strata_year` by scripts/finances/backfill_demo_bank_strata_year.py). The Reconciliation
    Summary panel that reads this (HistoricalReconstructionPage.tsx) looked permanently "stuck on
    old data" because it was -- every call returned an empty dict, not a caching issue.

    `provenance_class: "reconstruction"` is the one tag BOTH the old and the new generator set
    (confirmed: migration_027_randomize_east_gate_demo_bank_levies.py:782 and the vendored
    ingestion.py both stamp it), so matching on that instead is correct for either generator, for
    any building. `$ifNull: ["$levy_year", "$strata_year"]` covers the old generator's field name
    and the new one's backfilled equivalent.

    `direction: "credit"` is mandatory, not optional: `amount_cents` is always stored as a
    positive magnitude regardless of credit/debit (the vendored ingestion.py docstring is explicit
    about this; `direction` carries the sign separately). "Bank payment total" here means owner
    LEVY PAYMENTS (income), compared elsewhere in this report against levy CHARGES -- the older
    `synthetic_from_budget` tag this replaced was, in practice, only ever written by income-only
    generators, so the original (broken) query was accidentally income-only too. Once
    `provenance_class` is used instead -- which the expense-side generator also sets -- omitting
    this filter blends expense debits into what must be an income-only total. Caught live
    2026-08-01: after backfilling missing 2021-2025 expense rows, this function's totals roughly
    tripled for years with the most expense activity, which is not what the field means.

    Fund classification cannot be a plain `account_ref` regex either, for the same reason: the
    current generator (budget_levy_generator.py) writes ALL owner-payment credit rows to one
    COMBINED `OPERATING-{building_id}` account_ref (owners make one payment per period covering
    both funds; see that module's own docstring), with the real admin/sinking split living only
    in each row's `allocations[]` line items. Classifying by account_ref alone (which correctly
    distinguishes `ADMIN-{building_id}`/`SINKING-{building_id}` for the older generator's rows)
    silently attributed 100% of the current generator's income to "admin" and 0% to "sinking".
    Caught live 2026-08-01: sinking-fund income disappeared entirely from this report's output for
    every year on the current generator. `$project`s each document into a normalised `lines[]` --
    its real `allocations` when present, else a single synthesised line from the top-level
    account_ref/amount_cents (the older generator's shape) -- then unwinds and groups on that.
    """
    pipeline = [
        {"$match": {
            "building_id": building_id, "provenance_class": "reconstruction",
            "direction": "credit", "is_archived": {"$ne": True},
        }},
        {"$project": {
            "year": {"$ifNull": ["$levy_year", "$strata_year"]},
            "lines": {
                "$cond": [
                    {"$gt": [{"$size": {"$ifNull": ["$allocations", []]}}, 0]},
                    "$allocations",
                    [{
                        "fund_type": {
                            "$cond": [{"$regexMatch": {"input": "$account_ref", "regex": "SINKING"}}, "sinking", "admin"]
                        },
                        "amount_cents": "$amount_cents",
                    }],
                ],
            },
        }},
        {"$unwind": "$lines"},
        {"$group": {
            "_id": {"year": "$year", "fund": "$lines.fund_type"},
            "total": {"$sum": "$lines.amount_cents"},
        }},
    ]
    # mongo_db here is the raw db._db (unwrapped TenantScopedDatabase), so
    # PyMongo 4.10's aggregate() is a bare coroutine returning a cursor — it
    # must be awaited before iterating. Going through the wrapped `db` object
    # instead would apply database.py's _AsyncAggregateCursorProxy shim
    # automatically, but every other call in this reconstruction pipeline
    # deliberately uses db._db (see financial_onboarding.py's _mongo()), so
    # this call site awaits explicitly rather than switching wrappers.
    totals: dict[tuple[str, str], int] = {}
    cursor = await mongo_db.demo_bank_transactions.aggregate(pipeline)
    async for row in cursor:
        key = (str(row["_id"]["year"]), row["_id"]["fund"])
        totals[key] = row["total"]
    return totals


# ── Public API ───────────────────────────────────────────────────────────────

async def build_levy_regeneration_plan(
    *, building_id: str, mongo_db, pg_session, scheme_ref: SchemeRef, from_year: int, to_year: int,
) -> LevyReconciliationReport:
    """Dry-run only — writes nothing. Computes the GST-aware UOE apportionment
    (identical method to the Demo Bank reconstruction) for `from_year..to_year`,
    and diffs it against both the current `finance.levy_items` rows and the
    already-generated synthetic Demo Bank payment totals.
    """
    units: list[UnitShare] = await _load_units(mongo_db, building_id)
    if not units:
        raise RuntimeError(f"No units with positive UOE found for building_id={building_id}")

    levies = await _load_levies(mongo_db, building_id, from_year, to_year)
    gst = await _load_gst_settings(mongo_db, building_id)
    gst_multiplier = float(gst["gst_multiplier"])

    lot_ids = await _resolve_lot_ids(pg_session, scheme_ref.scheme_id, mongo_db, building_id)
    existing_items = await _resolve_current_levy_items(pg_session, scheme_ref.scheme_id, mongo_db, building_id)
    bank_totals = await _synthetic_bank_totals_by_year_fund(mongo_db, building_id)

    report_by_year_fund: list[YearFundReconciliation] = []
    lines: list[LevyRegenerationLine] = []
    warnings: list[str] = []

    # provenance discontinuity — flagged, non-blocking (plan02 §11)
    data_origins = {str(y): levies[y].get("data_origin") for y in levies}
    origins_seq = [data_origins.get(str(y)) for y in range(from_year, to_year + 1)]
    if len(set(o for o in origins_seq if o)) > 2:
        warnings.append(
            f"unresolved_source_discontinuity: annual_levies.data_origin varies across "
            f"{from_year}-{to_year} ({origins_seq}) — ordinary reconstruction proceeds where "
            f"totals are known; this is a provenance gap, not a blocking error."
        )

    for year in range(from_year, to_year + 1):
        levy_doc = levies[year]
        weighted_keys = [((unit.unit_number, "annual"), unit.uoe) for unit in units]

        for fund_model in _fund_models(building_id, levy_doc, gst_multiplier):
            ex_allocation = _allocate_cents(fund_model.annual_ex_gst_cents, weighted_keys)
            inc_allocation = _allocate_cents(fund_model.annual_inc_gst_cents, weighted_keys)

            regenerated_total = 0
            existing_total = 0
            for unit in units:
                key = (unit.unit_number, "annual")
                principal_cents = ex_allocation[key]
                inc_cents = inc_allocation[key]
                gst_cents = inc_cents - principal_cents
                regenerated_total += inc_cents

                lookup_key = (str(year), fund_model.fund, unit.unit_number)
                existing = existing_items.get(lookup_key)
                lot_id = lot_ids.get(unit.unit_number)

                if existing is None:
                    action: str = "insert"
                    existing_principal = existing_gst = None
                    existing_paid = 0
                else:
                    existing_total += existing["principal_cents"] + existing["gst_cents"]
                    existing_principal = existing["principal_cents"]
                    existing_gst = existing["gst_cents"]
                    existing_paid = existing["paid_cents"]
                    if existing_paid > (principal_cents + gst_cents):
                        action = "manual_review_overpaid"
                    elif existing_principal == principal_cents and existing_gst == gst_cents:
                        action = "unchanged"
                    else:
                        action = "update"

                if lot_id is None:
                    warnings.append(f"No core.lots row found for unit_number={unit.unit_number!r} — excluded from apply set.")

                lines.append(LevyRegenerationLine(
                    year=str(year), fund_type=fund_model.fund, unit_number=unit.unit_number,
                    lot_id=lot_id, principal_cents=principal_cents, gst_cents=gst_cents,
                    existing_principal_cents=existing_principal, existing_gst_cents=existing_gst,
                    existing_paid_cents=existing_paid, action=action,  # type: ignore[arg-type]
                ))

            proposed_inc_gst_cents = fund_model.annual_inc_gst_cents
            variance = regenerated_total - proposed_inc_gst_cents
            # Rounding-residual tolerance only: largest-remainder allocation can be
            # off by at most 1 cent per lot from floating-point weight division.
            tolerance_cents = len(units)
            bank_total = bank_totals.get((str(year), fund_model.fund), 0)

            report_by_year_fund.append(YearFundReconciliation(
                year=str(year), fund_type=fund_model.fund,
                annual_levies_proposed_inc_gst_cents=proposed_inc_gst_cents,
                regenerated_levy_items_total_cents=regenerated_total,
                synthetic_bank_payment_total_cents=bank_total,
                existing_levy_items_total_cents=existing_total,
                variance_cents=variance,
                within_tolerance=abs(variance) <= tolerance_cents,
            ))

    overpaid_lines = [l for l in lines if l.action == "manual_review_overpaid"]
    if overpaid_lines:
        warnings.append(
            f"{len(overpaid_lines)} lot/year/fund rows have existing paid_cents exceeding "
            f"the regenerated total — excluded from auto-apply, require manual review."
        )

    totals_reconcile = all(yf.within_tolerance for yf in report_by_year_fund) and not overpaid_lines

    return LevyReconciliationReport(
        building_id=building_id, from_year=from_year, to_year=to_year,
        by_year_fund=report_by_year_fund, lines=lines, warnings=warnings,
        totals_reconcile=totals_reconcile,
    )


async def apply_levy_regeneration(
    *, scheme_ref: SchemeRef, pg_session, ledger_repo, outbox_repo, plan: LevyReconciliationReport,
    confirm: bool, override_reconciliation_mismatch: bool = False,
) -> LevyApplyResult:
    """Executes a previously-built plan via
    FinancialCoreService.bulk_upsert_historical_levy_items(). Refuses unless
    confirm=True and (plan.totals_reconcile or override_reconciliation_mismatch).
    Never called automatically — a separate, explicit, human-approved action
    (Phase 6 of the historical ledger reconciliation plan)."""
    from uuid import uuid5, NAMESPACE_URL as _NS
    from services.financial_core.service import FinancialCoreService

    if not confirm:
        raise ValueError("apply_levy_regeneration requires confirm=True")
    if not plan.totals_reconcile and not override_reconciliation_mismatch:
        raise ValueError(
            "Refusing to apply: plan.totals_reconcile is False. Pass "
            "override_reconciliation_mismatch=True to proceed anyway (requires a "
            "recorded justification in the audit log at the router layer)."
        )

    run_ids = await _resolve_levy_run_ids(pg_session, scheme_ref.scheme_id)
    fund_ids = await _resolve_fund_ids(pg_session, scheme_ref.scheme_id)
    existing_by_lot = await _resolve_existing_levy_items_by_lot_id(pg_session, scheme_ref.scheme_id)
    current_owners: Optional[dict[UUID, UUID]] = None  # lazily resolved only if an insert needs it
    namespace = uuid5(_NS, f"strataos:levy-regen:{plan.building_id}")

    specs: list[HistoricalLevyItemSpec] = []
    manual_review_count = 0
    for line in plan.lines:
        if line.action == "manual_review_overpaid":
            manual_review_count += 1
            continue
        if line.action == "unchanged" or line.lot_id is None:
            continue
        run_id = run_ids.get(line.year)
        if run_id is None:
            logger.warning("No levy_run for year=%s — skipping line unit=%s", line.year, line.unit_number)
            continue

        existing = existing_by_lot.get((line.year, line.fund_type, line.lot_id))
        if existing is not None:
            # UPDATE case: reuse the row's own owner_party_id/fund_id verbatim —
            # never re-derive them for an already-existing levy_item.
            owner_party_id = existing["owner_party_id"]
            fund_id = existing["fund_id"]
        else:
            # INSERT case: resolve fund_id by type and the lot's current owner.
            fund_id = fund_ids.get(line.fund_type)
            if fund_id is None:
                logger.warning("No finance.funds row for fund_type=%s — skipping line unit=%s", line.fund_type, line.unit_number)
                continue
            if current_owners is None:
                current_owners = await _resolve_current_owner_party_ids(pg_session, scheme_ref.scheme_id)
            owner_party_id = current_owners.get(line.lot_id)
            if owner_party_id is None:
                logger.warning("No current owner found for lot_id=%s — skipping line unit=%s", line.lot_id, line.unit_number)
                continue

        levy_item_id = uuid5(namespace, f"{line.year}:{line.fund_type}:{line.unit_number}")
        specs.append(HistoricalLevyItemSpec(
            levy_item_id=levy_item_id,
            levy_run_id=run_id,
            lot_id=line.lot_id,
            owner_party_id=owner_party_id,
            fund_id=fund_id,
            principal_cents=line.principal_cents,
            gst_cents=line.gst_cents,
        ))

    svc = FinancialCoreService(ledger_repo, outbox_repo)
    cmd = BulkUpsertHistoricalLevyItemsCommand(scheme_ref=scheme_ref, items=specs, confirm=True)
    saved = await svc.bulk_upsert_historical_levy_items(cmd)

    return LevyApplyResult(
        applied_item_count=len(saved),
        total_principal_cents=sum(i.principal_cents for i in saved),
        total_gst_cents=sum(i.gst_cents for i in saved),
        manual_review_count=manual_review_count,
    )


def _staging_year(raw: Any) -> int:
    """Normalise a staging `year` value to the calendar year it starts in.

    `historical_annual_levies.year` is copied verbatim from the operator's CSV, so
    it arrives as either "2025" or the Australian financial-year form "2025-2026".
    A bare int() raises ValueError on the latter and would surface as an unhandled
    500 from the regenerate endpoints. `int(str(y).split('-')[0])` is the idiom this
    codebase already uses for the same ambiguity (see get_latest_levy_year's
    callers); normalising also makes the key match `_resolve_current_levy_items`,
    which keys Postgres levy_items by the starting year alone.
    """
    text = str(raw).strip()
    if not text:
        raise ValueError("historical_annual_levies row has an empty 'year'.")
    return int(text.split("-")[0])


async def build_phase_f_prime_levy_plan(
    *, building_id: str, mongo_db, pg_session, scheme_ref: SchemeRef,
) -> LevyReconciliationReport:
    """Same apportionment core as build_levy_regeneration_plan, but sourced from
    the Phase F-Zero staging collections (historical_annual_levies /
    historical_levy_issuances) instead of live annual_levies — the
    ADR-022-documented-but-never-built Phase F-prime reconciler, generic for any
    newly onboarded building (not East-Gate-specific).
    """
    docs = await mongo_db.historical_annual_levies.find({"building_id": building_id}).to_list(None)
    if not docs:
        raise RuntimeError(
            f"No historical_annual_levies staging rows for building_id={building_id} — "
            f"run the onboarding CSV import (import-historical-financials) first."
        )
    years = sorted({_staging_year(d["year"]) for d in docs})
    from_year, to_year = years[0], years[-1]

    # historical_annual_levies rows are already split by fund_type; reshape into
    # the same {year: {"proposed_admin_expenses":..., "proposed_sinking_expenses":...}}
    # shape _load_levies/_fund_models expect, so the same apportionment core applies.
    reshaped: dict[int, dict[str, Any]] = {}
    for d in docs:
        year = _staging_year(d["year"])
        bucket = reshaped.setdefault(year, {"year": str(year), "building_id": building_id})
        field_name = "proposed_admin_expenses" if d["fund_type"] == "admin" else "proposed_sinking_expenses"
        bucket[field_name] = d.get("budget_proposed", 0.0)

    units: list[UnitShare] = await _load_units(mongo_db, building_id)
    gst = await _load_gst_settings(mongo_db, building_id)
    gst_multiplier = float(gst["gst_multiplier"])
    lot_ids = await _resolve_lot_ids(pg_session, scheme_ref.scheme_id, mongo_db, building_id)
    existing_items = await _resolve_current_levy_items(pg_session, scheme_ref.scheme_id, mongo_db, building_id)

    report_by_year_fund: list[YearFundReconciliation] = []
    lines: list[LevyRegenerationLine] = []
    warnings: list[str] = []

    for year in range(from_year, to_year + 1):
        levy_doc = reshaped.get(year)
        if levy_doc is None:
            warnings.append(f"No historical_annual_levies rows for year={year} — skipped.")
            continue
        weighted_keys = [((unit.unit_number, "annual"), unit.uoe) for unit in units]
        for fund_model in _fund_models(building_id, levy_doc, gst_multiplier):
            ex_allocation = _allocate_cents(fund_model.annual_ex_gst_cents, weighted_keys)
            inc_allocation = _allocate_cents(fund_model.annual_inc_gst_cents, weighted_keys)
            regenerated_total = 0
            existing_total = 0
            for unit in units:
                key = (unit.unit_number, "annual")
                principal_cents = ex_allocation[key]
                gst_cents = inc_allocation[key] - principal_cents
                regenerated_total += inc_allocation[key]
                lookup_key = (str(year), fund_model.fund, unit.unit_number)
                existing = existing_items.get(lookup_key)
                lot_id = lot_ids.get(unit.unit_number)
                if existing is None:
                    action = "insert"
                    existing_principal = existing_gst = None
                    existing_paid = 0
                else:
                    existing_total += existing["principal_cents"] + existing["gst_cents"]
                    existing_principal = existing["principal_cents"]
                    existing_gst = existing["gst_cents"]
                    existing_paid = existing["paid_cents"]
                    # Overpaid detection, identical to build_levy_regeneration_plan's.
                    # apply_levy_regeneration() skips a line ONLY on this exact action
                    # string, so omitting it here does not merely lose a warning — it
                    # silently auto-applies a rewrite over a lot that has already paid
                    # MORE than the regenerated charge, which is precisely the case a
                    # human must adjudicate. The two builders feed the same apply
                    # function and must therefore classify lines identically.
                    if existing_paid > (principal_cents + gst_cents):
                        action = "manual_review_overpaid"
                    elif existing_principal == principal_cents and existing_gst == gst_cents:
                        action = "unchanged"
                    else:
                        action = "update"
                if lot_id is None:
                    # Same disclosure the live builder makes: apply_levy_regeneration
                    # silently `continue`s on lot_id=None, so without this the row just
                    # vanishes from the applied set with nothing said about it.
                    warnings.append(
                        f"No core.lots row found for unit_number={unit.unit_number!r} — excluded from apply set."
                    )
                lines.append(LevyRegenerationLine(
                    year=str(year), fund_type=fund_model.fund, unit_number=unit.unit_number,
                    lot_id=lot_id, principal_cents=principal_cents, gst_cents=gst_cents,
                    existing_principal_cents=existing_principal, existing_gst_cents=existing_gst,
                    existing_paid_cents=existing_paid, action=action,  # type: ignore[arg-type]
                ))
            report_by_year_fund.append(YearFundReconciliation(
                year=str(year), fund_type=fund_model.fund,
                annual_levies_proposed_inc_gst_cents=fund_model.annual_inc_gst_cents,
                regenerated_levy_items_total_cents=regenerated_total,
                synthetic_bank_payment_total_cents=0,
                existing_levy_items_total_cents=existing_total,
                variance_cents=regenerated_total - fund_model.annual_inc_gst_cents,
                within_tolerance=abs(regenerated_total - fund_model.annual_inc_gst_cents) <= len(units),
            ))

    overpaid_lines = [line for line in lines if line.action == "manual_review_overpaid"]
    if overpaid_lines:
        warnings.append(
            f"{len(overpaid_lines)} lot/year/fund rows have existing paid_cents exceeding "
            f"the regenerated total — excluded from auto-apply, require manual review."
        )

    return LevyReconciliationReport(
        building_id=building_id, from_year=from_year, to_year=to_year,
        by_year_fund=report_by_year_fund, lines=lines, warnings=warnings,
        # `and not overpaid_lines` matches build_levy_regeneration_plan. Without it a
        # plan containing overpaid lots still reports totals_reconcile=True, so the
        # router's 422 gate passes and the operator is never asked to look.
        totals_reconcile=all(yf.within_tolerance for yf in report_by_year_fund) and not overpaid_lines,
    )
