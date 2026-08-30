# @featuretrace:financial-onboarding — generic (building-agnostic) generator for the historical
#   levy CHARGE schedule (a receivable obligation), distinct from budget_levy_generator.py's
#   Demo Bank receipt reconstruction. Data flow: annual_levies + units (Mongo) + core.lots +
#   core.ownership_periods + finance.levy_items (Postgres, dedup) -> LevyChargeLine (building-scoped).
#   Related: budget_levy_generator.py, expense_category_generator.py, historical_levy_charge_posting.py.
# Layer: service
# Data flow: annual_levies + units + settings (Mongo) + core.lots + core.ownership_periods +
#            finance.levy_items (Postgres, dedup) -> LevyChargePlan ->
#            backend/scripts/historical_levy_charge_posting.py ->
#            FinancialCoreService.create_historical_levy() (Postgres GL posting).
#            (building-scoped)
# Related: backend/services/reconstruction_generators/budget_levy_generator.py (Demo Bank
#          receipt reconstruction — this module deliberately never writes demo_bank_transactions)
#          backend/services/levy_generation_service.py (same apportionment core, GST-correction
#          use case, no GL posting)
#          backend/scripts/migrations/migration_027_randomize_east_gate_demo_bank_levies.py
#          backend/routers/financial_matching.py (_post_payment_to_ledger — source of the
#          date-scoped owner-resolution SQL pattern reused here)
#          docs/finances/next-phase-financial-recontruction-demo-bank-eastgate-2021-2026.md
#          docs/finances/reconcilation-2021-2022-finances-plan01.md
# Toggle: historical_financial_reconstruction, historical_reconstruction_posting
# Collection: annual_levies, units, settings
# Table: core.lots, core.ownership_periods, core.parties, finance.levy_items, finance.levy_runs
"""Building-agnostic per-lot, per-period HISTORICAL LEVY CHARGE generator.

Distinguishes charges (receivable obligations) from Demo Bank receipts per
`docs/finances/next-phase-financial-recontruction-demo-bank-eastgate-2021-2026.md`'s "important
distinction": a levy charge is not itself a bank transaction. This module NEVER produces a
`ReconstructedTransactionRow` and NEVER writes `demo_bank_transactions` — its output
(`LevyChargeLine`) feeds `FinancialCoreService.create_historical_levy()` directly, via
`backend/scripts/historical_levy_charge_posting.py`, bypassing Demo Bank entirely.

Reuses (does not reimplement) the same building-agnostic apportionment core
`budget_levy_generator.py` and `levy_generation_service.py` already use from
`migration_027_randomize_east_gate_demo_bank_levies.py` — `_allocate_cents()` (integer-cents,
largest-remainder), the levy-frequency period helpers, and `_fund_models()`'s GST-aware ex-GST/
inc-GST split.

Own period-aware dedup — do not reuse levy_generation_service._resolve_current_levy_items()
--------------------------------------------------------------------------------------------
That function's returned dict is keyed `(year, fund_type, unit_number)` — no period component —
because its own loop over a lot's levy_item rows assigns into the same dict key for every row,
so a second/third/fourth period's row silently overwrites an earlier one. That is safe for
`budget_levy_generator.py`'s own coarse "skip the whole year if anything's posted" dedup, but
would be wrong here: this generator must be able to resume a partially-charged year (e.g. Q1-Q2
already posted, Q3-Q4 not yet) without silently treating the whole year as done or, worse,
silently re-charging Q1-Q2. `_load_existing_charged_periods()` below performs the equivalent
Mongo-unit_number-to-Postgres-lot_number bridge join but keeps every `(year, fund, unit,
quarter_no)` tuple distinct.

Ownership resolution — date-scoped, never drops a charge
----------------------------------------------------------
Reuses the exact `core.ownership_periods` query proven in
`routers/financial_matching.py::_post_payment_to_ledger` (including its `recorded_to IS NULL`
bitemporal-retraction exclusion — a real bug found and fixed there 2026-07-25). Unlike that
caller, `finance.levy_items.owner_party_id` is `NOT NULL` and there is no way to represent "no
receipt for this lot yet" the way a payment simply wouldn't exist — a levy charge must be raised
for every lot regardless of whether current ownership records are complete. When no primary
owner resolves for a lot at its issue_date, the charge is retained against a per-tenant,
idempotent, find-or-create sentinel placeholder party (`party_type='unresolved_placeholder'`)
rather than being dropped — dropping it would silently break every reconciliation total.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import text

from scripts.migrations.migration_027_randomize_east_gate_demo_bank_levies import (
    LEVY_FREQUENCIES,
    QUARTERS,
    _allocate_cents,
    _due_dates,
    _due_dates_for_periods,
    _fund_models,
    _load_gst_settings,
    _load_levies,
    _load_units,
    _periods_for_frequency,
)
from services.financial_core.domain.entities import SchemeRef
from services.levy_generation_service import _resolve_lot_ids

logger = logging.getLogger(__name__)

GENERATION_METHOD = "historical_levy_charge_schedule_from_approved_controls_v1"
_IDEMPOTENCY_NAMESPACE = uuid5(NAMESPACE_URL, "https://strataos.internal/historical-levy-charge")
_UNRESOLVED_OWNER_LEGAL_NAME = "Unresolved Historical Owner"


class LevyChargeGenerationError(ValueError):
    """Raised when the historical levy charge generator cannot build a plan.

    Deliberately a plain ValueError subclass (matches
    budget_levy_generator.ReconstructionGenerationError's own convention) so this module has no
    dependency on the reconstruction-batch workflow module.
    """


@dataclass(frozen=True)
class LevyChargeLine:
    """One per-lot, per-period levy CHARGE — a receivable obligation, never a bank transaction."""
    unit_number: str
    lot_id: UUID
    owner_party_id: UUID
    owner_resolution_status: str  # "resolved" | "unresolved"
    year: int
    fund_type: str  # "admin" | "sinking"
    levy_type: str  # "ordinary" | "special" | "adjustment" — only "ordinary" is generated today
    period: str  # "Q1".."Q4" / "H1"/"H2" / "M1".."M12" / "Y1"
    quarter_no: int  # 1-based position within that year's period list (finance.levy_runs.quarter_no)
    issue_date: date
    due_date: date
    uoe: float
    total_uoe: float
    principal_cents: int
    gst_cents: int
    total_cents: int
    source_annual_control: str
    derivation_type: str = "document_derived_levy_charge"
    idempotency_key: str = ""


@dataclass
class LevyChargePlan:
    building_id: str
    from_year: int
    to_year: int
    as_of_date: date
    lines: list[LevyChargeLine] = field(default_factory=list)
    # due_date > as_of_date — a fully approved future period, previewed but never posted by
    # default (docs/finances/reconcilation-2021-2022-finances-plan01.md item: don't reconcile
    # full-year charges against partial-year actual cash income).
    planned_not_posted: list[LevyChargeLine] = field(default_factory=list)
    skipped_already_charged: int = 0
    warnings: list[str] = field(default_factory=list)
    total_principal_cents: int = 0
    total_gst_cents: int = 0


def _idempotency_key(building_id: str, year: int, fund_type: str, levy_type: str, period: str, lot_id: UUID) -> str:
    raw = f"{building_id}:{year}:{fund_type}:{levy_type}:{period}:{lot_id}"
    return str(uuid5(_IDEMPOTENCY_NAMESPACE, raw))


async def _load_existing_charged_periods(
    pg_session, scheme_id: UUID, mongo_db, building_id: str,
) -> set[tuple[str, str, str, int]]:
    """(year, fund_type, unit_number, quarter_no) already present in finance.levy_items.

    See module docstring — deliberately NOT levy_generation_service._resolve_current_levy_items(),
    which collapses multiple periods into one dict entry per (year, fund, unit).
    """
    rows = await pg_session.execute(
        text(
            """
            SELECT lr.financial_year, f.fund_type::text AS fund_type, lot.lot_number, lr.quarter_no
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
        by_plain_lot_number.setdefault(str(row[2]), []).append(row)

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

    out: set[tuple[str, str, str, int]] = set()
    for plain_lot_number, rows_for_lot in by_plain_lot_number.items():
        unit_number = unit_number_by_plain_lot_number.get(plain_lot_number)
        if unit_number is None:
            continue
        for financial_year, fund_type, _lot_number, quarter_no in rows_for_lot:
            fund = "sinking" if fund_type in ("capital_works", "sinking") else "admin"
            out.add((str(financial_year), fund, unit_number, int(quarter_no or 0)))
    return out


async def _load_uoe_schedules(
        pg_session, scheme_id: UUID, lot_ids: set[UUID],
) -> dict[UUID, list[tuple[date, Optional[date], float]]]:
    """Per-lot UOE history from `compliance.entitlement_schedules` + `lot_entitlements`
    (migration 0005) — date-ranged, so a lot's entitlement can legitimately differ across years
    (a subdivision, a lot merger, a CMS amendment). Returns lot_id -> sorted list of
    (effective_from, effective_to, entitlement_units); empty list for a lot with no schedule row
    (the caller falls back to the lot's current `units.uoe`, gap-tolerant per this module's
    existing convention — see `_resolve_uoe_for_year`).
    """
    if not lot_ids:
        return {}
    rows = await pg_session.execute(
        text(
            """
            SELECT le.lot_id, es.effective_from, es.effective_to, le.entitlement_units
            FROM compliance.lot_entitlements le
            JOIN compliance.entitlement_schedules es
              ON es.entitlement_schedule_id = le.entitlement_schedule_id
            WHERE le.scheme_id = :sid AND le.lot_id = ANY(:lot_ids)
            ORDER BY le.lot_id, es.effective_from
            """
        ),
        {"sid": str(scheme_id), "lot_ids": [str(lid) for lid in lot_ids]},
    )
    by_lot: dict[UUID, list[tuple[date, Optional[date], float]]] = {}
    for row in rows.all():
        by_lot.setdefault(row[0], []).append((row[1], row[2], float(row[3])))
    return by_lot


def _resolve_uoe_for_year(
        schedules_by_lot: dict[UUID, list[tuple[date, Optional[date], float]]],
        lot_id: Optional[UUID],
        year: int,
        fallback_uoe: float,
        *,
        unit_number: str,
        warnings: list[str],
        _warned_units: set[str],
) -> float:
    """Resolve a lot's UOE for a specific year: the schedule row whose
    `[effective_from, effective_to)` covers 1 July of that financial year, else the lot's current
    `units.uoe` (today's value) as a gap-tolerant fallback — with a warning emitted once per unit
    (not once per period) so a preview isn't flooded with duplicate lines.
    """
    schedules = schedules_by_lot.get(lot_id) if lot_id else None
    if schedules:
        probe = date(year, 7, 1)  # mid financial-year — matches this codebase's FY convention
        for effective_from, effective_to, entitlement_units in schedules:
            if effective_from <= probe and (effective_to is None or probe < effective_to):
                return entitlement_units
    if unit_number not in _warned_units:
        _warned_units.add(unit_number)
        warnings.append(
            f"Unit {unit_number}: no compliance.entitlement_schedules row covers year {year} "
            "(or none exist for this lot at all) — falling back to the unit's current UOE for "
            "every year. If this unit's entitlement ever changed historically, populate "
            "compliance.entitlement_schedules/lot_entitlements to charge each year correctly."
        )
    return fallback_uoe


async def _get_or_create_unresolved_owner_party(pg_session, tenant_id: UUID) -> UUID:
    """Idempotent find-or-create, matching the lookup-then-insert pattern already proven by
    db_postgres/repos/ownership_repo.py::upsert_owner_party() — a dedicated party_type is used
    here (rather than reusing that function's hardcoded 'individual') so this sentinel is never
    confused with a real owner in later reporting/filtering."""
    result = await pg_session.execute(
        text(
            "SELECT party_id::text FROM core.parties "
            "WHERE tenant_id = :tid AND party_type = 'unresolved_placeholder' AND legal_name = :name "
            "LIMIT 1"
        ),
        {"tid": str(tenant_id), "name": _UNRESOLVED_OWNER_LEGAL_NAME},
    )
    row = result.first()
    if row:
        return UUID(row[0])

    result = await pg_session.execute(
        text(
            "INSERT INTO core.parties (tenant_id, party_type, legal_name, metadata) "
            "VALUES (:tid, 'unresolved_placeholder', :name, CAST(:meta AS jsonb)) "
            "RETURNING party_id::text"
        ),
        {
            "tid": str(tenant_id),
            "name": _UNRESOLVED_OWNER_LEGAL_NAME,
            "meta": json.dumps({"placeholder_reason": "unresolved_historical_ownership"}),
        },
    )
    return UUID(result.scalar())


async def _resolve_owner_or_placeholder(
    pg_session, scheme_ref: SchemeRef, lot_id: UUID, as_of: date,
) -> tuple[UUID, str]:
    """Date-scoped owner resolution at the charge's issue_date — same query shape as
    routers/financial_matching.py::_post_payment_to_ledger, including the recorded_to IS NULL
    bitemporal-retraction exclusion. Falls back to the sentinel placeholder party rather than
    dropping the charge."""
    result = await pg_session.execute(
        text(
            "SELECT owner_party_id::text FROM core.ownership_periods "
            "WHERE lot_id = :lot_id AND is_primary_owner = TRUE "
            "AND recorded_to IS NULL "
            "AND valid_from <= :as_of "
            "AND (valid_to IS NULL OR valid_to >= :as_of) "
            "LIMIT 1"
        ),
        {"lot_id": str(lot_id), "as_of": as_of},
    )
    row = result.first()
    if row:
        return UUID(row[0]), "resolved"

    placeholder_id = await _get_or_create_unresolved_owner_party(pg_session, scheme_ref.tenant_id)
    return placeholder_id, "unresolved"


async def generate_historical_levy_charge_plan(
    *,
    mongo_db,
    pg_session,
    scheme_ref: SchemeRef,
    building_id: str,
    from_year: int,
    to_year: int,
    as_of_date: date,
) -> LevyChargePlan:
    """Build a LevyChargePlan by generating ordinary per-lot levy charges from the approved
    budget. Pure computation — no writes. Safe to call repeatedly for both preview and the
    posting script's own planning step. Raises LevyChargeGenerationError if there's nothing to
    generate (no units, no annual_levies for the requested range, or every lot/year/fund/period
    is already charged).
    """
    warnings: list[str] = []
    units = await _load_units(mongo_db, building_id, warnings=warnings)
    if not units:
        raise LevyChargeGenerationError(
            f"No units with positive unit-of-entitlement found for building_id={building_id!r} "
            "— cannot generate levy charges without a unit entitlement schedule."
        )

    levies = await _load_levies(mongo_db, building_id, from_year, to_year, strict=False)
    missing_years = [year for year in range(from_year, to_year + 1) if year not in levies]
    if missing_years:
        warnings.append(
            f"No annual_levies (approved budget) found for year(s) {missing_years} — skipped, "
            "no charges generated for those years."
        )

    gst = await _load_gst_settings(mongo_db, building_id)
    gst_multiplier = float(gst["gst_multiplier"])

    lot_ids = await _resolve_lot_ids(pg_session, scheme_ref.scheme_id, mongo_db, building_id)
    already_charged = await _load_existing_charged_periods(pg_session, scheme_ref.scheme_id, mongo_db, building_id)

    # Per-year UOE (not a single frozen snapshot) — see _resolve_uoe_for_year. A unit's
    # entitlement can legitimately differ across years; charging every year at today's UOE would
    # be silently wrong for any unit whose entitlement ever changed.
    resolved_lot_ids = {lid for lid in lot_ids.values() if lid is not None}
    uoe_schedules_by_lot = await _load_uoe_schedules(pg_session, scheme_ref.scheme_id, resolved_lot_ids)
    _uoe_fallback_warned: set[str] = set()

    plan = LevyChargePlan(building_id=building_id, from_year=from_year, to_year=to_year, as_of_date=as_of_date)

    for year in range(from_year, to_year + 1):
        if year not in levies:
            continue
        levy_doc = levies[year]
        levy_frequency = str(levy_doc.get("levy_frequency") or "quarterly").strip().lower()
        is_quarterly = levy_frequency not in LEVY_FREQUENCIES or levy_frequency == "quarterly"
        periods = QUARTERS if is_quarterly else _periods_for_frequency(levy_frequency)
        due_dates = _due_dates(levy_doc, year) if is_quarterly else _due_dates_for_periods(year, periods)
        fund_models = _fund_models(building_id, levy_doc, gst_multiplier)
        if not fund_models:
            continue

        uoe_this_year: dict[str, float] = {
            unit.unit_number: _resolve_uoe_for_year(
                uoe_schedules_by_lot, lot_ids.get(unit.unit_number), year, unit.uoe,
                unit_number=unit.unit_number, warnings=warnings, _warned_units=_uoe_fallback_warned,
            )
            for unit in units
        }
        total_uoe_this_year = sum(uoe_this_year.values())

        weighted_keys = [
            ((unit.unit_number, period), uoe_this_year[unit.unit_number])
            for unit in units for period in periods
        ]

        for fund_model in fund_models:
            ex_allocation = _allocate_cents(fund_model.annual_ex_gst_cents, weighted_keys)
            inc_allocation = _allocate_cents(fund_model.annual_inc_gst_cents, weighted_keys)

            for period_index, period in enumerate(periods, start=1):
                due_date = due_dates[period]
                # No source field distinguishes an "issue" date from the due date for ordinary
                # levies in the data this repo has imported to date (_due_dates()/
                # _due_dates_for_periods() only ever resolve a due_date) — issue_date == due_date
                # is the honest choice rather than fabricating an offset the evidence doesn't
                # support (docs/finances/reconcilation-2021-2022-finances-plan01.md: "assign an
                # honest effective date ... at the granularity supported by the source").
                issue_date = due_date

                for unit in units:
                    dedup_key = (str(year), fund_model.fund, unit.unit_number, period_index)
                    if dedup_key in already_charged:
                        plan.skipped_already_charged += 1
                        continue

                    alloc_key = (unit.unit_number, period)
                    inc_cents = inc_allocation[alloc_key]
                    ex_cents = ex_allocation[alloc_key]
                    if inc_cents <= 0:
                        continue
                    gst_cents = inc_cents - ex_cents

                    lot_id = lot_ids.get(unit.unit_number)
                    if lot_id is None:
                        warnings.append(
                            f"Unit {unit.unit_number} has no resolvable core.lots row — charge skipped."
                        )
                        continue

                    owner_party_id, owner_status = await _resolve_owner_or_placeholder(
                        pg_session, scheme_ref, lot_id, issue_date,
                    )

                    line = LevyChargeLine(
                        unit_number=unit.unit_number,
                        lot_id=lot_id,
                        owner_party_id=owner_party_id,
                        owner_resolution_status=owner_status,
                        year=year,
                        fund_type=fund_model.fund,
                        levy_type="ordinary",
                        period=period,
                        quarter_no=period_index,
                        issue_date=issue_date,
                        due_date=due_date,
                        uoe=uoe_this_year[unit.unit_number],
                        total_uoe=total_uoe_this_year,
                        principal_cents=ex_cents,
                        gst_cents=gst_cents,
                        total_cents=inc_cents,
                        source_annual_control=f"annual_levies.{fund_model.basis} (year={year})",
                        idempotency_key=_idempotency_key(
                            building_id, year, fund_model.fund, "ordinary", period, lot_id,
                        ),
                    )
                    plan.total_principal_cents += ex_cents
                    plan.total_gst_cents += gst_cents
                    if due_date > as_of_date:
                        plan.planned_not_posted.append(line)
                    else:
                        plan.lines.append(line)

    if not plan.lines and not plan.planned_not_posted:
        raise LevyChargeGenerationError(
            f"Nothing to generate for building_id={building_id!r} years {from_year}-{to_year}: "
            "the approved budget resolves to zero across all funds, or every lot/year/fund/period "
            "is already charged."
        )

    plan.warnings = warnings
    return plan


def summarize_by_year_fund_period(plan: LevyChargePlan) -> list[dict]:
    """Group postable (not planned_not_posted) lines by (year, fund_type, period) with totals —
    the shape the preview endpoint and the posting script's own grouping-before-post step share."""
    groups: dict[tuple[int, str, str], dict] = {}
    for line in plan.lines:
        key = (line.year, line.fund_type, line.period)
        g = groups.setdefault(key, {
            "year": line.year, "fund_type": line.fund_type, "period": line.period,
            "quarter_no": line.quarter_no, "issue_date": line.issue_date.isoformat(),
            "due_date": line.due_date.isoformat(), "levy_type": line.levy_type,
            "lot_count": 0, "principal_cents": 0, "gst_cents": 0, "total_cents": 0,
            "unresolved_owner_count": 0,
        })
        g["lot_count"] += 1
        g["principal_cents"] += line.principal_cents
        g["gst_cents"] += line.gst_cents
        g["total_cents"] += line.total_cents
        if line.owner_resolution_status != "resolved":
            g["unresolved_owner_count"] += 1
    return sorted(groups.values(), key=lambda g: (g["year"], g["fund_type"], g["period"]))
