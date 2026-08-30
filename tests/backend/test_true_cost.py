"""Tests for True Cost of Ownership Service — Phase 2."""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from services.true_cost_service import (
    TrueCostOfOwnershipInput,
    TrueCostOfOwnershipResult,
    _compound_period_total,
    _get_building_average,
    _get_capital_shock_buffer,
    _get_unit_levies,
    compute_true_cost_of_ownership,
)

BUILDING_ID = "13195"
UNIT_NUMBER = "TH087"
FINANCIAL_YEAR = "2026-2027"


@pytest.fixture(autouse=True)
def _default_ledger_levy_path():
    """GAP-FIN-065 Item A: true_cost now PREFERS the full-year annual_levies rate
    (get_levy_rates) and only falls back to the unit_levy_ledger YTD figure. These
    legacy tests assert the ledger path, so default get_levy_rates to zeros (no
    annual_levies doc) to keep them exercising that fallback. Full-year-path tests
    override this patch with real rates.
    """
    with patch(
        "services.true_cost_service.get_levy_rates",
        AsyncMock(return_value={"admin_annual": 0.0, "sinking_annual": 0.0}),
    ):
        yield


# ── Unit tests for helper functions ───────────────────────────────────────

class TestCompoundPeriodTotal:
    def test_1yr_equals_annual(self):
        """1-year total is simply the annual amount (no compound growth)."""
        annual = 1_000_000  # $10,000 in cents
        result = _compound_period_total(annual, 1, cpi=0.035)
        # For 1 year, the formula returns annual (or very close to it)
        assert result == annual or abs(result - annual) <= 1

    def test_5yr_greater_than_5x_annual(self):
        """5-year CPI-inflated total is greater than 5× the base annual."""
        annual = 1_000_000
        result = _compound_period_total(annual, 5, cpi=0.035)
        assert result > 5 * annual

    def test_10yr_greater_than_5yr(self):
        """10-year total is more than 5-year total."""
        annual = 1_000_000
        total_5 = _compound_period_total(annual, 5, cpi=0.035)
        total_10 = _compound_period_total(annual, 10, cpi=0.035)
        assert total_10 > total_5

    def test_result_is_integer(self):
        """Period total must be returned as integer cents."""
        result = _compound_period_total(50_000, 5, cpi=0.035)
        assert isinstance(result, int)

    def test_zero_annual_gives_zero(self):
        assert _compound_period_total(0, 5) == 0

    def test_zero_cpi_gives_exact_multiple(self):
        """With 0% CPI, 5yr total = 5 × annual."""
        annual = 100_000
        result = _compound_period_total(annual, 5, cpi=0.0)
        assert result == 5 * annual


# ── Input model tests ─────────────────────────────────────────────────────

class TestTrueCostInput:
    def test_valid_input(self):
        inp = TrueCostOfOwnershipInput(
            building_id=BUILDING_ID,
            unit_number=UNIT_NUMBER,
            holding_period_years=5,
            financial_year=FINANCIAL_YEAR,
        )
        assert inp.holding_period_years == 5
        assert inp.include_rates is True
        assert inp.include_water is True

    def test_holding_period_bounds(self):
        # Minimum 1 year
        inp = TrueCostOfOwnershipInput(
            building_id=BUILDING_ID,
            unit_number=UNIT_NUMBER,
            holding_period_years=1,
            financial_year=FINANCIAL_YEAR,
        )
        assert inp.holding_period_years == 1


# ── Mock DB helpers ────────────────────────────────────────────────────────

def _make_db():
    db = MagicMock()

    # Unit levy ledger — admin_levied/sinking_levied are stored in DOLLARS in the
    # real collection (confirmed against seeds/levy_ledger_east_gate.py, e.g.
    # admin_levied=2296.0). _get_unit_levies() converts to cents at the read
    # boundary, so the mock must supply dollar-scale values to exercise that path.
    db.unit_levy_ledger.find_one = AsyncMock(return_value={
        "admin_levied": 4_000.0,
        "sinking_levied": 2_000.0,
        "building_id": BUILDING_ID,
        "unit_number": UNIT_NUMBER,
    })

    # Units record
    db.units.find_one = AsyncMock(return_value={
        "unit_number": UNIT_NUMBER,
        "unit_type": "Townhouse",
        "building_id": BUILDING_ID,
        "council_rates_cents": 250_000,
        "water_estimate_cents": 180_000,
        "estimated_value_cents": 80_000_000,
    })

    # Building_assets (maintenance probability)
    db.building_assets.aggregate = MagicMock(return_value=MagicMock(
        to_list=AsyncMock(return_value=[
            {"avg_health": 70, "high_risk_count": 1, "total_count": 10}
        ])
    ))

    # Capital shock
    db.capital_shock_assessments.find_one = AsyncMock(return_value={
        "per_unit_buffer_cents": 50_000,
        "unit_number": UNIT_NUMBER,
    })

    # Building average aggregate — the real pipeline groups on key "avg" (dollars);
    # _get_building_average() converts to cents at the read boundary.
    db.unit_levy_ledger.aggregate = MagicMock(return_value=MagicMock(
        to_list=AsyncMock(return_value=[
            {"_id": None, "avg": 6_500.0}
        ])
    ))

    return db


class TestComputeTrueCostOfOwnership:
    async def test_returns_result_model(self):
        db = _make_db()
        inp = TrueCostOfOwnershipInput(
            building_id=BUILDING_ID,
            unit_number=UNIT_NUMBER,
            holding_period_years=5,
            financial_year=FINANCIAL_YEAR,
        )
        with patch("services.true_cost_service.db", db):
            result = await compute_true_cost_of_ownership(inp)
        assert isinstance(result, TrueCostOfOwnershipResult)

    async def test_total_for_period_greater_than_annual(self):
        db = _make_db()
        inp = TrueCostOfOwnershipInput(
            building_id=BUILDING_ID,
            unit_number=UNIT_NUMBER,
            holding_period_years=5,
            financial_year=FINANCIAL_YEAR,
        )
        with patch("services.true_cost_service.db", db):
            result = await compute_true_cost_of_ownership(inp)
        assert result.total_for_period_cents > result.total_annual_cents

    async def test_10yr_total_more_than_5yr(self):
        """CPI-adjusted 10yr total > 5yr total."""
        db = _make_db()

        async def get_result(years):
            inp = TrueCostOfOwnershipInput(
                building_id=BUILDING_ID,
                unit_number=UNIT_NUMBER,
                holding_period_years=years,
                financial_year=FINANCIAL_YEAR,
            )
            return await compute_true_cost_of_ownership(inp)

        with patch("services.true_cost_service.db", db):
            r5 = await get_result(5)
        db2 = _make_db()
        with patch("services.true_cost_service.db", db2):
            r10 = await get_result(10)

        assert r10.total_for_period_cents > r5.total_for_period_cents

    async def test_all_monetary_values_are_integers(self):
        db = _make_db()
        inp = TrueCostOfOwnershipInput(
            building_id=BUILDING_ID,
            unit_number=UNIT_NUMBER,
            holding_period_years=5,
            financial_year=FINANCIAL_YEAR,
        )
        with patch("services.true_cost_service.db", db):
            result = await compute_true_cost_of_ownership(inp)
        for field in (
                "admin_levy_cents", "sinking_levy_cents", "council_rates_cents",
                "water_estimate_cents", "capital_shock_buffer_cents",
                "maintenance_probability_cost_cents", "total_annual_cents",
                "total_for_period_cents", "cost_per_day_cents",
                "building_average_annual_cents",
        ):
            assert isinstance(getattr(result, field), int), f"{field} must be int"

    async def test_cost_per_day_reasonable(self):
        """cost_per_day_cents should be total_annual_cents / 365 (approx)."""
        db = _make_db()
        inp = TrueCostOfOwnershipInput(
            building_id=BUILDING_ID,
            unit_number=UNIT_NUMBER,
            holding_period_years=1,
            financial_year=FINANCIAL_YEAR,
        )
        with patch("services.true_cost_service.db", db):
            result = await compute_true_cost_of_ownership(inp)
        expected = result.total_annual_cents // 365
        assert abs(result.cost_per_day_cents - expected) <= 2

    async def test_assumptions_list_populated(self):
        db = _make_db()
        inp = TrueCostOfOwnershipInput(
            building_id=BUILDING_ID,
            unit_number=UNIT_NUMBER,
            holding_period_years=5,
            financial_year=FINANCIAL_YEAR,
        )
        with patch("services.true_cost_service.db", db):
            result = await compute_true_cost_of_ownership(inp)
        assert isinstance(result.assumptions, list)
        assert len(result.assumptions) > 0

    async def test_unit_not_found_raises_or_returns_graceful(self):
        db = _make_db()
        db.units.find_one = AsyncMock(return_value=None)
        db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
        inp = TrueCostOfOwnershipInput(
            building_id=BUILDING_ID,
            unit_number="NONEXISTENT",
            holding_period_years=1,
            financial_year=FINANCIAL_YEAR,
        )
        with patch("services.true_cost_service.db", db):
            try:
                result = await compute_true_cost_of_ownership(inp)
                # If it doesn't raise, it should return a result with assumptions about missing data
                assert len(result.assumptions) > 0
            except Exception as exc:
                # HTTPException with 404 is acceptable
                assert "404" in str(exc) or "not found" in str(exc).lower() or hasattr(exc, "status_code")

    async def test_building_id_in_result(self):
        db = _make_db()
        inp = TrueCostOfOwnershipInput(
            building_id=BUILDING_ID,
            unit_number=UNIT_NUMBER,
            holding_period_years=5,
            financial_year=FINANCIAL_YEAR,
        )
        with patch("services.true_cost_service.db", db):
            result = await compute_true_cost_of_ownership(inp)
        assert result.building_id == BUILDING_ID


# ── Capital shock buffer regression coverage ──────────────────────────────
#
# detect_capital_shocks() returns "max_deficit", never "total_exposure" — a
# stale key name previously made _get_capital_shock_buffer() silently ignore
# real capital-shock projections and fall back to a flat 0.5%-of-property-value
# guess on every call. See services/capital_shock_service.py:detect_capital_shocks.

class TestGetCapitalShockBuffer:
    async def test_uses_real_max_deficit_when_available(self):
        db = MagicMock()
        db.units.count_documents = AsyncMock(return_value=10)
        unit = {"unit_number": UNIT_NUMBER, "entitlement": 1, "property_value_cents": 80_000_000}

        with patch("services.true_cost_service.db", db), \
                patch(
                    "services.capital_shock_service.detect_capital_shocks",
                    AsyncMock(return_value={"max_deficit": 500_000.0}),
                ):
            db.units.aggregate = MagicMock(return_value=MagicMock(
                to_list=AsyncMock(return_value=[{"_id": None, "total": 10.0}])
            ))
            cents, source, _note = await _get_capital_shock_buffer(BUILDING_ID, unit)

        # $500,000 total deficit, unit holds 1/10 of total entitlement → $50,000 = 5,000,000 cents.
        assert cents == 5_000_000
        assert source == "projected"

    async def test_falls_back_to_property_value_estimate_when_no_deficit(self):
        db = MagicMock()
        db.units.count_documents = AsyncMock(return_value=10)
        unit = {"unit_number": UNIT_NUMBER, "entitlement": 1, "property_value_cents": 80_000_000}

        with patch("services.true_cost_service.db", db), \
                patch(
                    "services.capital_shock_service.detect_capital_shocks",
                    AsyncMock(return_value={"max_deficit": 0.0}),
                ):
            cents, source, _note = await _get_capital_shock_buffer(BUILDING_ID, unit)

        # No deficit projected → 0.5% of property value estimate, not the real (zero) exposure.
        assert cents == int(80_000_000 * 0.005)
        assert source == "estimated"


# ── Dollars→cents boundary conversion regression coverage ─────────────────
#
# unit_levy_ledger.admin_levied/sinking_levied are stored in DOLLARS in the real
# collection (seeds/levy_ledger_east_gate.py: admin_levied=2296.0). Reading them
# straight into a *_cents field without converting under-counted every TCO figure
# built on levy data by ~100x. get_levy_trajectory in investor_intelligence.py
# already does this same *100 conversion for the sibling annual_levies fields.

class TestGetUnitLeviesDollarsToCents:
    async def test_converts_dollar_ledger_values_to_cents(self):
        db = MagicMock()
        db.unit_levy_ledger.find_one = AsyncMock(return_value={
            "admin_levied": 2_296.0,
            "sinking_levied": 804.0,
            "building_id": BUILDING_ID,
            "unit_number": UNIT_NUMBER,
        })
        with patch("services.true_cost_service.db", db):
            result = await _get_unit_levies(BUILDING_ID, UNIT_NUMBER)

        assert result["admin_levied"] == 229_600
        assert result["sinking_levied"] == 80_400
        assert result["source"] == "actual"

    async def test_missing_ledger_record_returns_zero_not_none(self):
        db = MagicMock()
        db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
        with patch("services.true_cost_service.db", db):
            result = await _get_unit_levies(BUILDING_ID, UNIT_NUMBER)

        assert result == {
            "admin_levied": 0, "sinking_levied": 0, "source": "missing", "basis": "ytd_charged",
        }


class TestGetBuildingAverageDollarsToCents:
    async def test_converts_primary_pipeline_average_to_cents(self):
        db = MagicMock()
        db.unit_levy_ledger.aggregate = MagicMock(return_value=MagicMock(
            to_list=AsyncMock(return_value=[{"_id": None, "avg": 6_500.0}])
        ))
        with patch("services.true_cost_service.db", db):
            result = await _get_building_average(BUILDING_ID, FINANCIAL_YEAR)

        assert result == 650_000

    async def test_converts_fallback_pipeline_average_to_cents(self):
        db = MagicMock()
        # Primary (year-scoped) pipeline returns nothing — falls back to the
        # all-time per-unit-max pipeline.
        empty_result = MagicMock(to_list=AsyncMock(return_value=[]))
        fallback_result = MagicMock(to_list=AsyncMock(return_value=[{"_id": None, "avg": 3_000.0}]))
        db.unit_levy_ledger.aggregate = MagicMock(side_effect=[empty_result, fallback_result])
        with patch("services.true_cost_service.db", db):
            result = await _get_building_average(BUILDING_ID, FINANCIAL_YEAR)

        assert result == 300_000

    async def test_no_data_returns_zero(self):
        db = MagicMock()
        empty_result = MagicMock(to_list=AsyncMock(return_value=[]))
        db.unit_levy_ledger.aggregate = MagicMock(return_value=empty_result)
        with patch("services.true_cost_service.db", db):
            result = await _get_building_average(BUILDING_ID, FINANCIAL_YEAR)

        assert result == 0


# ── Field-name regression coverage ─────────────────────────────────────────
#
# unit_levy_ledger's year field is "year", not "financial_year" — confirmed
# against repositories/financial_repository.py's own index definitions
# (create_index([("building_id", 1), ("year", 1), ...])). Querying/sorting on
# "financial_year" is not an error in MongoDB, just a silent no-op (every
# document is equally "missing" that field), so a plain result-shape assertion
# would not have caught this — these tests inspect the actual query arguments.

class TestUnitLeviesQueriesRealYearField:
    async def test_sorts_by_year_not_financial_year(self):
        db = MagicMock()
        db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
        with patch("services.true_cost_service.db", db):
            await _get_unit_levies(BUILDING_ID, UNIT_NUMBER)

        _, kwargs = db.unit_levy_ledger.find_one.call_args
        assert kwargs["sort"] == [("year", -1)]


class TestBuildingAverageQueriesRealYearField:
    async def test_primary_pipeline_matches_on_year_not_financial_year(self):
        db = MagicMock()
        empty_result = MagicMock(to_list=AsyncMock(return_value=[]))
        db.unit_levy_ledger.aggregate = MagicMock(return_value=empty_result)
        with patch("services.true_cost_service.db", db):
            await _get_building_average(BUILDING_ID, "2026")

        first_pipeline = db.unit_levy_ledger.aggregate.call_args_list[0].args[0]
        assert first_pipeline[0]["$match"] == {"building_id": BUILDING_ID, "year": "2026"}


# ── GAP-FIN-065 Item A: full-year (annual_levies) basis is preferred ──────────
#
# true_cost projects the FULL annual holding cost, so it must use the full-year
# owner-payable (inc-GST) levy from annual_levies (via get_levy_rates), NOT the
# amount charged year-to-date in unit_levy_ledger. The ledger is only a fallback.

class TestFullYearLevyBasisPreferred:
    async def test_unit_levies_use_full_year_rate_when_available(self):
        db = MagicMock()
        # Ledger has a (lower) YTD figure that must NOT be used when a rate exists.
        db.unit_levy_ledger.find_one = AsyncMock(return_value={
            "admin_levied": 100.0, "sinking_levied": 50.0,
            "building_id": BUILDING_ID, "unit_number": UNIT_NUMBER,
        })
        rates = {"admin_annual": 1_000.0, "sinking_annual": 400.0}  # inc-GST per-UOE $/year
        with patch("services.true_cost_service.db", db), \
                patch("services.true_cost_service.get_levy_rates", AsyncMock(return_value=rates)):
            result = await _get_unit_levies(BUILDING_ID, UNIT_NUMBER, "2026", entitlement=10.0)

        # admin = 1000 × 10 × 100 cents; sinking = 400 × 10 × 100 cents
        assert result["admin_levied"] == 1_000_000
        assert result["sinking_levied"] == 400_000
        assert result["basis"] == "full_year_annual"
        assert result["source"] == "actual"
        # The ledger find_one must not have been consulted on the full-year path.
        db.unit_levy_ledger.find_one.assert_not_called()

    async def test_unit_levies_fall_back_to_ledger_when_no_rate(self):
        db = MagicMock()
        db.unit_levy_ledger.find_one = AsyncMock(return_value={
            "admin_levied": 2_296.0, "sinking_levied": 804.0,
            "building_id": BUILDING_ID, "unit_number": UNIT_NUMBER,
        })
        with patch("services.true_cost_service.db", db), \
                patch("services.true_cost_service.get_levy_rates",
                      AsyncMock(return_value={"admin_annual": 0.0, "sinking_annual": 0.0})):
            result = await _get_unit_levies(BUILDING_ID, UNIT_NUMBER, "2026", entitlement=10.0)

        assert result["admin_levied"] == 229_600
        assert result["sinking_levied"] == 80_400
        assert result["basis"] == "ytd_charged"

    async def test_building_average_uses_full_year_rate_times_avg_entitlement(self):
        db = MagicMock()
        db.units.aggregate = MagicMock(return_value=MagicMock(
            to_list=AsyncMock(return_value=[{"_id": None, "avg_ent": 8.0}])
        ))
        rates = {"admin_annual": 1_000.0, "sinking_annual": 400.0}
        with patch("services.true_cost_service.db", db), \
                patch("services.true_cost_service.get_levy_rates", AsyncMock(return_value=rates)):
            result = await _get_building_average(BUILDING_ID, "2026")

        # (1000 + 400) × 8 × 100 cents
        assert result == 1_120_000

    async def test_result_exposes_full_year_levy_basis(self):
        db = _make_db()
        db.units.find_one = AsyncMock(return_value={
            "unit_number": UNIT_NUMBER, "unit_type": "Townhouse", "building_id": BUILDING_ID,
            "entitlement": 10.0, "property_value_cents": 80_000_000,
        })
        db.units.aggregate = MagicMock(return_value=MagicMock(
            to_list=AsyncMock(return_value=[{"_id": None, "avg_ent": 8.0}])
        ))
        inp = TrueCostOfOwnershipInput(
            building_id=BUILDING_ID, unit_number=UNIT_NUMBER,
            holding_period_years=5, financial_year="2026",
        )
        rates = {"admin_annual": 1_000.0, "sinking_annual": 400.0}
        with patch("services.true_cost_service.db", db), \
                patch("services.true_cost_service.get_levy_rates", AsyncMock(return_value=rates)):
            result = await compute_true_cost_of_ownership(inp)

        assert result.levy_basis == "full_year_annual"
        assert "inc-GST" in result.levy_basis_label or "Full-year" in result.levy_basis_label
        assert result.admin_levy_cents == 1_000_000
        assert result.sinking_levy_cents == 400_000
