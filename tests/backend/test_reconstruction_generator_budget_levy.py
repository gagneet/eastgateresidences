"""Unit tests for services/reconstruction_generators/budget_levy_generator.py.

generate_manifest_from_proposed_budget() reads from four boundary functions
(_load_units, _load_levies, _load_gst_settings from migration_027, plus
levy_generation_service._resolve_current_levy_items). All are patched directly so these
tests exercise the apportionment/grouping/dedup logic without a database connection —
mirroring tests/backend/test_levy_generation_service.py's fixture pattern.

Uses building_id "16244" (Sierra demo), never "13195" (East Gate) — this generator's whole
point is to work for a building that has none of East Gate's pre-seeded synthetic data.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from scripts.migrations.migration_027_randomize_east_gate_demo_bank_levies import UnitShare
from services.reconstruction_generators.budget_levy_generator import (
    GENERATION_METHOD,
    ReconstructionGenerationError,
    combined_account_ref,
    generate_manifest_from_proposed_budget,
)
from integrations.demo_bank.reconstruction_batch_schemas import ReconstructionBatch

BUILDING_ID = "16244"
TENANT_ID = uuid.UUID("11111111-0000-0000-0000-000000000001")
SCHEME_ID = uuid.UUID("22222222-0000-0000-0000-000000000002")


class _SchemeRef:
    def __init__(self, scheme_id):
        self.scheme_id = scheme_id
        self.tenant_id = TENANT_ID


SCHEME_REF = _SchemeRef(SCHEME_ID)

_GST_SETTINGS_ON = {
    "gst_registered": True, "levy_gst_rate": 0.10, "effective_gst_rate": 0.10,
    "gst_multiplier": 1.10, "gst_label": "GST (10%)",
}
_GST_SETTINGS_OFF = {
    "gst_registered": False, "levy_gst_rate": 0.10, "effective_gst_rate": 0.0,
    "gst_multiplier": 1.0, "gst_label": "GST (not registered)",
}


def _batch(**overrides) -> ReconstructionBatch:
    defaults = dict(
        batch_id="batch-1", building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
        reconstruction_method=GENERATION_METHOD, created_by="user-1", is_test_data=True,
    )
    defaults.update(overrides)
    return ReconstructionBatch(**defaults)


def _patches(*, units, levies, existing_items=None, gst_settings=None):
    return (
        patch("services.reconstruction_generators.budget_levy_generator._load_units", new=AsyncMock(return_value=units)),
        patch("services.reconstruction_generators.budget_levy_generator._load_levies", new=AsyncMock(return_value=levies)),
        patch("services.reconstruction_generators.budget_levy_generator._load_gst_settings", new=AsyncMock(return_value=gst_settings or _GST_SETTINGS_ON)),
        patch("services.reconstruction_generators.budget_levy_generator._resolve_current_levy_items", new=AsyncMock(return_value=existing_items or {})),
    )


async def _run(*, batch=None, **kwargs):
    p1, p2, p3, p4 = _patches(**kwargs)
    with p1, p2, p3, p4:
        return await generate_manifest_from_proposed_budget(
            mongo_db=object(), pg_session=object(), scheme_ref=SCHEME_REF,
            building_id=BUILDING_ID, batch=batch or _batch(),
        )


class TestFullGeneration:
    @pytest.mark.asyncio
    async def test_generates_combined_admin_sinking_rows_for_all_units(self):
        units = [UnitShare(unit_number="TH001", uoe=60.0), UnitShare(unit_number="TH002", uoe=40.0)]
        # $200,000/$100,000 — deliberately ABOVE the $150,000 ATO non-profit GST registration
        # threshold (AUSTRALIAN_GST_NONPROFIT_TURNOVER_THRESHOLD_CENTS) so this test exercises
        # GST math, not threshold behaviour (covered separately in
        # test_reconstruction_generator_budget_levy_gst_threshold.py).
        levies = {2024: {
            "year": "2024", "proposed_admin_expenses": 200000.0, "proposed_sinking_expenses": 100000.0,
            "data_origin": "audited_pdf",
        }}

        manifest = await _run(units=units, levies=levies)

        assert manifest.expected_transaction_count == 2 * 2 * 4  # 2 units x 2 funds x 4 quarters
        assert len(manifest.transactions) == manifest.expected_transaction_count
        assert manifest.generator_version == GENERATION_METHOD

        # Exact-cent-sum guarantee from _allocate_cents: sum per fund/year equals the
        # fund's annual GST-inclusive total, no rounding drift.
        admin_total = sum(t.amount_cents for t in manifest.transactions if t.fund_type == "admin")
        sinking_total = sum(t.amount_cents for t in manifest.transactions if t.fund_type == "sinking")
        assert admin_total == 22000000  # $200,000 * 1.10
        assert sinking_total == 11000000  # $100,000 * 1.10
        assert manifest.expected_credit_cents == admin_total + sinking_total

    @pytest.mark.asyncio
    async def test_combined_rows_share_account_ref_group_and_date(self):
        """The critical correctness point: admin+sinking rows for the same lot/quarter
        must share (account_ref, unit_number, posted_date, direction) or
        import_historical_reconstruction() silently drops the whole group instead of
        combining it — see the module's own docstring for why each fund's own
        ADMIN-/SINKING- account_ref must NOT be used as the row's account_ref."""
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        levies = {2024: {
            "year": "2024", "proposed_admin_expenses": 1000.0, "proposed_sinking_expenses": 500.0,
            "data_origin": "audited_pdf",
        }}

        manifest = await _run(units=units, levies=levies)

        q1_rows = [t for t in manifest.transactions if t.quarter == 1]
        assert len(q1_rows) == 2  # admin + sinking
        admin_row = next(t for t in q1_rows if t.fund_type == "admin")
        sinking_row = next(t for t in q1_rows if t.fund_type == "sinking")

        assert admin_row.payment_group_id == sinking_row.payment_group_id
        assert admin_row.account_ref == sinking_row.account_ref == combined_account_ref(BUILDING_ID)
        assert admin_row.posted_date == sinking_row.posted_date
        assert admin_row.unit_number == sinking_row.unit_number
        assert admin_row.direction == sinking_row.direction == "credit"
        # account_ref must NOT be the per-fund staging ref used by the legacy East-Gate path
        assert admin_row.account_ref != f"ADMIN-{BUILDING_ID}"
        assert sinking_row.account_ref != f"SINKING-{BUILDING_ID}"


class TestRoundingResidual:
    @pytest.mark.asyncio
    async def test_exact_sum_holds_when_total_does_not_divide_evenly(self):
        """The prior full-generation test used UOE/amounts that happen to divide evenly
        across all 12 (unit x quarter) buckets, so it never actually exercised
        _allocate_cents's largest-remainder residual distribution — an off-by-one-cent
        bug there would not have been caught. This uses 3 equal-UOE units and a proposed
        amount deliberately NOT divisible by 12, forcing several buckets to differ."""
        units = [UnitShare(unit_number=f"TH00{i}", uoe=1.0) for i in range(1, 4)]
        # $200,000.03 — deliberately ABOVE the $150,000 ATO non-profit GST registration threshold
        # (so GST still applies, exercising this test's real purpose) while still NOT divisible by
        # 12 (preserves the largest-remainder residual-distribution exercise the original $100.03
        # fixture was chosen for).
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 200000.03, "data_origin": "audited_pdf"}}

        manifest = await _run(units=units, levies=levies)

        ex_gst_amounts = [t.amount_ex_gst_cents for t in manifest.transactions]
        inc_amounts = [t.amount_cents for t in manifest.transactions]

        assert sum(ex_gst_amounts) == 20000003  # round($200,000.03 * 100), exact — no rounding drift
        assert sum(inc_amounts) == 22000003  # round($200,000.03 * 1.10 * 100)
        # Proves the residual was actually redistributed across buckets (not every
        # unit/quarter share is identical, despite all 3 units having equal UOE) —
        # a naive floor()-only implementation would fail this by summing short.
        assert len(set(ex_gst_amounts)) > 1
        assert len(set(inc_amounts)) > 1
        for t in manifest.transactions:
            assert t.amount_cents == t.amount_ex_gst_cents + t.gst_cents


class TestDedup:
    @pytest.mark.asyncio
    async def test_paid_period_skipped_and_warned(self):
        units = [UnitShare(unit_number="TH001", uoe=60.0), UnitShare(unit_number="TH002", uoe=40.0)]
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf"}}
        existing = {("2024", "admin", "TH001"): {
            "levy_item_id": uuid.uuid4(), "principal_cents": 60000, "gst_cents": 6000,
            "paid_cents": 66000, "owner_party_id": uuid.uuid4(), "fund_id": uuid.uuid4(),
        }}

        manifest = await _run(units=units, levies=levies, existing_items=existing)

        assert not any(t.unit_number == "TH001" for t in manifest.transactions)
        assert any(t.unit_number == "TH002" for t in manifest.transactions)
        assert manifest.calculation_configuration["skipped_paid_unit_year_fund_count"] == 1
        assert any("Skipped 1 lot/year/fund" in w for w in manifest.warnings)

    @pytest.mark.asyncio
    async def test_zero_paid_cents_does_not_skip(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf"}}
        existing = {("2024", "admin", "TH001"): {
            "levy_item_id": uuid.uuid4(), "principal_cents": 100000, "gst_cents": 10000,
            "paid_cents": 0, "owner_party_id": uuid.uuid4(), "fund_id": uuid.uuid4(),
        }}

        manifest = await _run(units=units, levies=levies, existing_items=existing)
        assert any(t.unit_number == "TH001" for t in manifest.transactions)


class TestGst:
    @pytest.mark.asyncio
    async def test_gst_not_registered_zero_gst_component(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf"}}

        manifest = await _run(units=units, levies=levies, gst_settings=_GST_SETTINGS_OFF)

        for t in manifest.transactions:
            assert t.gst_cents == 0
            assert t.amount_cents == t.amount_ex_gst_cents
        assert manifest.expected_credit_cents == 100000  # $1000, no GST uplift

    @pytest.mark.asyncio
    async def test_gst_registered_amount_equals_ex_gst_plus_gst(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf"}}

        manifest = await _run(units=units, levies=levies)
        for t in manifest.transactions:
            assert t.amount_cents == t.amount_ex_gst_cents + t.gst_cents


class TestErrorPaths:
    @pytest.mark.asyncio
    async def test_no_units_raises(self):
        with pytest.raises(ReconstructionGenerationError, match="No units"):
            await _run(units=[], levies={2024: {"year": "2024", "proposed_admin_expenses": 1000.0}})

    @pytest.mark.asyncio
    async def test_missing_annual_levies_for_all_requested_years_raises(self):
        """Gap-tolerant behaviour (see _load_levies(strict=False)): a missing annual_levies
        doc no longer raises directly — the year is skipped with a warning instead. But if
        EVERY requested year is missing, there's nothing left to generate at all, so the
        function still raises, via the same "Nothing to generate" path as the
        all-zero/all-already-paid cases, not a distinct "missing annual_levies" message."""
        with pytest.raises(ReconstructionGenerationError, match="Nothing to generate"):
            await _run(units=[UnitShare(unit_number="TH001", uoe=100.0)], levies={})

    @pytest.mark.asyncio
    async def test_partial_year_range_skips_missing_year_with_warning(self):
        """A building with annual_levies for ONLY some of the requested years is the expected
        partial-onboarding case, not an error — the present year still generates rows, and the
        absent year is surfaced as a manifest warning rather than failing the whole batch."""
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf"}}
        batch = _batch(financial_year_start=2023, financial_year_end=2024)

        manifest = await _run(units=units, levies=levies, batch=batch)

        assert manifest.transactions  # 2024 still generated
        assert all(t.financial_year == "2024" for t in manifest.transactions)
        assert any("2023" in w and "annual_levies" in w for w in manifest.warnings)

    @pytest.mark.asyncio
    async def test_all_periods_already_paid_raises(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf"}}
        existing = {("2024", "admin", "TH001"): {
            "levy_item_id": uuid.uuid4(), "principal_cents": 100000, "gst_cents": 10000,
            "paid_cents": 110000, "owner_party_id": uuid.uuid4(), "fund_id": uuid.uuid4(),
        }}
        with pytest.raises(ReconstructionGenerationError, match="Nothing to generate"):
            await _run(units=units, levies=levies, existing_items=existing)


class TestLevyFrequency:
    """levy_frequency (annual_levies field, populated by gap_tolerant_financial_import.py) —
    a building may bill monthly/quarterly/half_yearly/annually, and may change frequency
    between years (confirmed real: East Gate itself billed half-yearly 2021-2022 before
    switching to quarterly from 2023). Default/unspecified must remain byte-identical to the
    pre-existing quarterly-only behaviour — every other test in this file proves that by
    never setting levy_frequency at all and still passing unchanged."""

    @pytest.mark.asyncio
    async def test_half_yearly_produces_2_periods_per_unit_not_4(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        levies = {2024: {
            "year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf",
            "levy_frequency": "half_yearly",
        }}
        manifest = await _run(units=units, levies=levies)
        assert manifest.expected_transaction_count == 2  # 1 unit x 1 fund x 2 half-years
        quarters_seen = sorted(t.quarter for t in manifest.transactions)
        assert quarters_seen == [1, 2]

    @pytest.mark.asyncio
    async def test_monthly_produces_12_periods_per_unit(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        levies = {2024: {
            "year": "2024", "proposed_admin_expenses": 1200.0, "data_origin": "audited_pdf",
            "levy_frequency": "monthly",
        }}
        manifest = await _run(units=units, levies=levies)
        assert manifest.expected_transaction_count == 12

    @pytest.mark.asyncio
    async def test_annual_produces_1_period_per_unit(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        levies = {2024: {
            "year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf",
            "levy_frequency": "annual",
        }}
        manifest = await _run(units=units, levies=levies)
        assert manifest.expected_transaction_count == 1

    @pytest.mark.asyncio
    async def test_exact_cent_sum_holds_for_non_quarterly_frequency(self):
        """The _allocate_cents largest-remainder guarantee must still hold regardless of
        period count — this is genuinely exercised here since 100.03 doesn't divide evenly
        across 3 half-yearly periods either."""
        units = [UnitShare(unit_number=f"TH00{i}", uoe=1.0) for i in range(1, 4)]
        levies = {2024: {
            "year": "2024", "proposed_admin_expenses": 100.03, "data_origin": "audited_pdf",
            "levy_frequency": "half_yearly",
        }}
        manifest = await _run(units=units, levies=levies)
        assert sum(t.amount_ex_gst_cents for t in manifest.transactions) == 10003

    @pytest.mark.asyncio
    async def test_unrecognised_frequency_falls_back_to_quarterly(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        levies = {2024: {
            "year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf",
            "levy_frequency": "fortnightly",
        }}
        manifest = await _run(units=units, levies=levies)
        assert manifest.expected_transaction_count == 4

    @pytest.mark.asyncio
    async def test_non_quarterly_assumption_code_names_the_frequency(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        levies = {2024: {
            "year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf",
            "levy_frequency": "half_yearly",
        }}
        manifest = await _run(units=units, levies=levies)
        assert all(t.assumption_code == "half_yearly_regular" for t in manifest.transactions)


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_identical_inputs_produce_identical_manifest_hash(self):
        units = [UnitShare(unit_number="TH001", uoe=60.0), UnitShare(unit_number="TH002", uoe=40.0)]
        levies = {2024: {
            "year": "2024", "proposed_admin_expenses": 1000.0, "proposed_sinking_expenses": 500.0,
            "data_origin": "audited_pdf",
        }}
        batch = _batch()

        manifest_1 = await _run(units=units, levies=levies, batch=batch)
        manifest_2 = await _run(units=units, levies=levies, batch=batch)

        assert manifest_1.manifest_hash == manifest_2.manifest_hash
        assert manifest_1.expected_transaction_count == manifest_2.expected_transaction_count
