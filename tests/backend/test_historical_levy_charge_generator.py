"""Unit tests for services/reconstruction_generators/historical_levy_charge_generator.py.

Follows the same boundary-function-patching convention as test_levy_generation_service.py —
every Mongo/Postgres read this generator performs is patched directly so these tests exercise
the allocation/dedup/gap-tolerance/date-cutoff logic without a database connection.
"""
from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from scripts.migrations.migration_027_randomize_east_gate_demo_bank_levies import UnitShare
from services.financial_core.domain.entities import SchemeRef
from services.reconstruction_generators.historical_levy_charge_generator import (
    LevyChargeGenerationError,
    generate_historical_levy_charge_plan,
)

BUILDING_ID = "16244"  # Sierra demo — never "13195" in tests
TENANT_ID = uuid.UUID("11111111-0000-0000-0000-000000000001")
SCHEME_ID = uuid.UUID("22222222-0000-0000-0000-000000000002")
SCHEME_REF = SchemeRef(tenant_id=TENANT_ID, scheme_id=SCHEME_ID)
LOT_1 = uuid.UUID("33333333-0000-0000-0000-000000000001")
LOT_2 = uuid.UUID("33333333-0000-0000-0000-000000000002")
LOT_3 = uuid.UUID("33333333-0000-0000-0000-000000000003")
OWNER_1 = uuid.UUID("44444444-0000-0000-0000-000000000001")

_GST_SETTINGS = {
    "gst_registered": True, "levy_gst_rate": 0.10, "effective_gst_rate": 0.10,
    "gst_multiplier": 1.10, "gst_label": "GST (10%)",
}
_MODULE = "services.reconstruction_generators.historical_levy_charge_generator"


def _patches(
        *, units, levies, lot_ids, already_charged=None, gst_settings=None, owner_status="resolved",
        uoe_schedules=None,
):
    return (
        patch(f"{_MODULE}._load_units", new=AsyncMock(return_value=units)),
        patch(f"{_MODULE}._load_levies", new=AsyncMock(return_value=levies)),
        patch(f"{_MODULE}._load_gst_settings", new=AsyncMock(return_value=gst_settings or _GST_SETTINGS)),
        patch(f"{_MODULE}._resolve_lot_ids", new=AsyncMock(return_value=lot_ids)),
        patch(f"{_MODULE}._load_existing_charged_periods", new=AsyncMock(return_value=already_charged or set())),
        patch(f"{_MODULE}._resolve_owner_or_placeholder", new=AsyncMock(return_value=(OWNER_1, owner_status))),
        # Empty dict (default) => every unit falls back to its today's `units.uoe` for every
        # year, matching this suite's pre-existing behaviour/assertions exactly. Tests that care
        # about per-year UOE resolution pass an explicit uoe_schedules mapping instead.
        patch(f"{_MODULE}._load_uoe_schedules", new=AsyncMock(return_value=uoe_schedules or {})),
    )


async def _run(*, from_year=2024, to_year=2024, as_of_date=date(2024, 12, 31), **kwargs):
    ctxs = _patches(**kwargs)
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], ctxs[6]:
        return await generate_historical_levy_charge_plan(
            mongo_db=object(), pg_session=object(), scheme_ref=SCHEME_REF, building_id=BUILDING_ID,
            from_year=from_year, to_year=to_year, as_of_date=as_of_date,
        )


class TestAllocationMath:
    @pytest.mark.asyncio
    async def test_allocation_sums_to_annual_control_exactly(self):
        # Uneven UOE across 3 units forces largest-remainder rounding.
        units = [
            UnitShare(unit_number="TH001", uoe=33.33),
            UnitShare(unit_number="TH002", uoe=33.33),
            UnitShare(unit_number="TH003", uoe=33.34),
        ]
        lot_ids = {"TH001": LOT_1, "TH002": LOT_2, "TH003": LOT_3}
        # $200,000 — deliberately ABOVE the $150,000 ATO non-profit GST registration threshold
        # (AUSTRALIAN_GST_NONPROFIT_TURNOVER_THRESHOLD_CENTS) so this test's own GST-inclusive
        # assertion isn't itself testing threshold behaviour — that's covered separately in
        # TestGstRegistrationThreshold below. A $1000 fixture would fall BELOW the threshold and
        # correctly generate zero GST, which isn't what this test is checking.
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 200000.0, "data_origin": "audited_pdf"}}

        plan = await _run(units=units, levies=levies, lot_ids=lot_ids)

        admin_lines = [l for l in plan.lines if l.fund_type == "admin"]
        # 4 quarters x 3 units = 12 lines; each quarter's 3 lines must sum exactly to that
        # quarter's share of $200,000 * 1.10 GST-inclusive (no cent lost/gained to rounding).
        assert len(admin_lines) == 12
        by_period: dict[str, int] = {}
        for line in admin_lines:
            by_period.setdefault(line.period, 0)
            by_period[line.period] += line.total_cents
        assert sum(by_period.values()) == 22000000  # $200,000 * 1.10, in cents, across the whole year


class TestDedupAndResume:
    @pytest.mark.asyncio
    async def test_all_four_quarters_survive_dedup_when_one_is_already_charged(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        lot_ids = {"TH001": LOT_1}
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf"}}
        # Q1 (period_index 1) already charged for this lot/year/fund.
        already_charged = {("2024", "admin", "TH001", 1)}

        plan = await _run(units=units, levies=levies, lot_ids=lot_ids, already_charged=already_charged)

        periods_generated = {l.period for l in plan.lines}
        assert periods_generated == {"Q2", "Q3", "Q4"}
        assert plan.skipped_already_charged == 1

    @pytest.mark.asyncio
    async def test_partial_year_resume_posts_only_missing_periods(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        lot_ids = {"TH001": LOT_1}
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf"}}
        already_charged = {("2024", "admin", "TH001", 1), ("2024", "admin", "TH001", 2)}

        plan = await _run(units=units, levies=levies, lot_ids=lot_ids, already_charged=already_charged)

        assert {l.period for l in plan.lines} == {"Q3", "Q4"}
        assert plan.skipped_already_charged == 2


class TestGapTolerance:
    @pytest.mark.asyncio
    async def test_missing_year_in_range_warns_and_continues(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        lot_ids = {"TH001": LOT_1}
        # Only 2024 present; range requested is 2023-2024.
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf"}}

        plan = await _run(from_year=2023, to_year=2024, units=units, levies=levies, lot_ids=lot_ids)

        assert any("2023" in w for w in plan.warnings)
        assert all(l.year == 2024 for l in plan.lines)

    @pytest.mark.asyncio
    async def test_no_units_raises(self):
        with pytest.raises(LevyChargeGenerationError):
            await _run(units=[], levies={}, lot_ids={})


class TestIdempotencyKeyDeterminism:
    @pytest.mark.asyncio
    async def test_same_inputs_produce_same_idempotency_key(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        lot_ids = {"TH001": LOT_1}
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf"}}

        plan_a = await _run(units=units, levies=levies, lot_ids=lot_ids)
        plan_b = await _run(units=units, levies=levies, lot_ids=lot_ids)

        keys_a = sorted(l.idempotency_key for l in plan_a.lines)
        keys_b = sorted(l.idempotency_key for l in plan_b.lines)
        assert keys_a == keys_b
        assert len(set(keys_a)) == len(keys_a)  # every line's key is unique within one plan


class TestUnresolvedOwner:
    @pytest.mark.asyncio
    async def test_unresolved_owner_still_produces_a_charge_line(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        lot_ids = {"TH001": LOT_1}
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf"}}

        plan = await _run(units=units, levies=levies, lot_ids=lot_ids, owner_status="unresolved")

        assert len(plan.lines) == 4  # 4 quarters, admin only fund (sinking is 0 here)
        assert all(l.owner_resolution_status == "unresolved" for l in plan.lines)


class TestAsOfDateCutoff:
    @pytest.mark.asyncio
    async def test_future_due_dates_are_planned_not_posted(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        lot_ids = {"TH001": LOT_1}
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf"}}

        # Cut off after Q1 only — Q2-Q4 fall after the cutoff.
        plan = await _run(units=units, levies=levies, lot_ids=lot_ids, as_of_date=date(2024, 3, 31))

        assert {l.period for l in plan.lines} == {"Q1"}
        assert {l.period for l in plan.planned_not_posted} == {"Q2", "Q3", "Q4"}


class TestGstRegistrationThreshold:
    """ATO ID 2016/1's $150,000 non-profit GST registration threshold — a real, generalizable
    rule found missing 2026-07-31 after a user cross-checked East Gate's 2021 figures ($138,460
    total turnover, correctly zero GST) against the source CSV before approving a real posting
    batch. Not a one-off East Gate exception: the same threshold applies to any building this
    generator runs for."""

    @pytest.mark.asyncio
    async def test_below_threshold_year_charges_zero_gst(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        lot_ids = {"TH001": LOT_1}
        # $138,460 total (admin only, sinking absent/zero) — below the $150,000 threshold.
        levies = {2021: {"year": "2021", "proposed_admin_expenses": 138460.0, "data_origin": "audited_pdf"}}

        plan = await _run(from_year=2021, to_year=2021, as_of_date=date(2021, 12, 31), units=units, levies=levies, lot_ids=lot_ids)

        assert len(plan.lines) == 4  # 4 quarters, admin only
        assert all(l.gst_cents == 0 for l in plan.lines)
        assert sum(l.total_cents for l in plan.lines) == 13846000  # $138,460.00, no GST added

    @pytest.mark.asyncio
    async def test_at_or_above_threshold_year_charges_gst(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        lot_ids = {"TH001": LOT_1}
        # $150,000 exactly — at the threshold, GST applies (">=" not ">").
        levies = {2021: {"year": "2021", "proposed_admin_expenses": 150000.0, "data_origin": "audited_pdf"}}

        plan = await _run(from_year=2021, to_year=2021, as_of_date=date(2021, 12, 31), units=units, levies=levies, lot_ids=lot_ids)

        assert sum(l.gst_cents for l in plan.lines) > 0
        assert sum(l.total_cents for l in plan.lines) == 16500000  # $150,000 * 1.10

    @pytest.mark.asyncio
    async def test_combined_admin_and_sinking_turnover_counts_toward_threshold(self):
        """$100,000 admin + $60,000 sinking = $160,000 combined — above threshold even though
        neither fund alone is; GST must apply to both funds."""
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        lot_ids = {"TH001": LOT_1}
        levies = {2021: {
            "year": "2021", "proposed_admin_expenses": 100000.0, "proposed_sinking_expenses": 60000.0,
            "data_origin": "audited_pdf",
        }}

        plan = await _run(from_year=2021, to_year=2021, as_of_date=date(2021, 12, 31), units=units, levies=levies, lot_ids=lot_ids)

        assert sum(l.gst_cents for l in plan.lines) > 0

    @pytest.mark.asyncio
    async def test_explicit_registration_fact_overrides_threshold_inference(self):
        """A future, evidence-backed per-year fact must win over the stateless threshold
        inference — e.g. a scheme voluntarily registered below threshold, or one that stayed
        registered per the ATO's 12-month minimum despite a later dip below $150k."""
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        lot_ids = {"TH001": LOT_1}
        levies = {2021: {
            "year": "2021", "proposed_admin_expenses": 50000.0,  # well below threshold
            "gst_registered_this_year": True,  # but explicitly registered anyway
            "data_origin": "audited_pdf",
        }}

        plan = await _run(from_year=2021, to_year=2021, as_of_date=date(2021, 12, 31), units=units, levies=levies, lot_ids=lot_ids)

        assert sum(l.gst_cents for l in plan.lines) > 0


class TestPerYearUoe:
    """A unit's UOE can legitimately differ across years (subdivision, lot merger, CMS
    amendment) — the generator must charge each year at that year's own entitlement, not a
    single frozen snapshot. See onboarding-reconstruction-workflow-canonical-spec.md §4a #1."""

    @pytest.mark.asyncio
    async def test_schedule_hit_uses_the_year_specific_uoe_not_todays(self):
        # TH001's entitlement was 50.0 through 2021, then increased to 100.0 from 2022 onward
        # (e.g. a lot merger). units.uoe (today's value, via UnitShare) is 100.0 — if the
        # generator used that for 2021 too, 2021 would be charged at double its true share.
        units = [
            UnitShare(unit_number="TH001", uoe=100.0),
            UnitShare(unit_number="TH002", uoe=100.0),
        ]
        lot_ids = {"TH001": LOT_1, "TH002": LOT_2}
        uoe_schedules = {
            LOT_1: [
                (date(2020, 1, 1), date(2022, 1, 1), 50.0),
                (date(2022, 1, 1), None, 100.0),
            ],
        }
        levies = {
            2021: {"year": "2021", "proposed_admin_expenses": 150000.0, "data_origin": "audited_pdf"},
            2022: {"year": "2022", "proposed_admin_expenses": 150000.0, "data_origin": "audited_pdf"},
        }

        plan = await _run(
            from_year=2021, to_year=2022, as_of_date=date(2022, 12, 31),
            units=units, levies=levies, lot_ids=lot_ids, uoe_schedules=uoe_schedules,
        )

        lines_2021 = [l for l in plan.lines if l.year == 2021 and l.unit_number == "TH001"]
        lines_2022 = [l for l in plan.lines if l.year == 2022 and l.unit_number == "TH001"]
        assert all(l.uoe == 50.0 for l in lines_2021)
        assert all(l.uoe == 100.0 for l in lines_2022)
        # total_uoe (the apportionment denominator) must also reflect that year's true total —
        # 150.0 in 2021 (50+100), 200.0 in 2022 (100+100) — not a single frozen 200.0 throughout.
        assert lines_2021[0].total_uoe == 150.0
        assert lines_2022[0].total_uoe == 200.0
        # TH001 gets a strictly smaller share of the same $150k annual control in 2021 (50/150)
        # than in 2022 (100/200) — proves the year-specific UOE actually drove the $ allocation,
        # not just the recorded metadata field.
        assert sum(l.total_cents for l in lines_2021) < sum(l.total_cents for l in lines_2022)

    @pytest.mark.asyncio
    async def test_no_schedule_falls_back_to_current_uoe_gap_tolerant(self):
        units = [UnitShare(unit_number="TH001", uoe=75.0)]
        lot_ids = {"TH001": LOT_1}
        levies = {2021: {"year": "2021", "proposed_admin_expenses": 150000.0, "data_origin": "audited_pdf"}}

        plan = await _run(
            from_year=2021, to_year=2021, as_of_date=date(2021, 12, 31),
            units=units, levies=levies, lot_ids=lot_ids, uoe_schedules={},
        )

        assert all(l.uoe == 75.0 for l in plan.lines)
        assert any("no compliance.entitlement_schedules row covers year 2021" in w for w in plan.warnings)
