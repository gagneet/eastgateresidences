"""
Unit Tests: get_admin_fund_projections & get_sinking_fund_projections
======================================================================
Tests the data-driven projection logic using mocked DB calls.
Covers: aggregation pipeline usage, CPI fallback labels, compounding,
multi-tenant zero-base (no hardcoded amounts), years-slicing, empty sinking plan.

Run with:
    backend/venv/bin/python3 -m pytest tests/backend/test_fund_forecasts_unit.py -v
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _agg_cursor(result_list):
    """Mock for Motor aggregate().to_list(n)."""
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=result_list)
    return cursor


def _find_cursor(result_list):
    """Mock for Motor find().sort().to_list(n)."""
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=result_list)
    return cursor


# ─── get_admin_fund_projections ───────────────────────────────────────────────

class TestGetAdminFundProjections:

    @pytest.mark.asyncio
    async def test_uses_aggregate_not_find_to_list_200(self):
        """aggregate() must be called; find() must NOT be called on financial_forecasts."""
        from services.forecast_service import get_admin_fund_projections

        with patch("services.forecast_service.db") as mock_db, \
                patch("services.forecast_service.get_levy_proposed_amounts", return_value=(340870.0, 99504.0)):
            mock_db.annual_levies.find_one = AsyncMock(return_value={"proposed_admin_expenses": 340870.0})
            mock_db.financial_forecasts.aggregate = MagicMock(
                return_value=_agg_cursor([{"_id": None, "y1": 367000.0, "y2": 415000.0, "y3": 463000.0}])
            )

            await get_admin_fund_projections("13195", 2026, extra_years=0)

            mock_db.financial_forecasts.aggregate.assert_called_once()
            assert not mock_db.financial_forecasts.find.called

    @pytest.mark.asyncio
    async def test_category_data_sets_forecast_type_and_source(self):
        """Non-zero aggregation sums → type=forecast, source=category_forecasts."""
        from services.forecast_service import get_admin_fund_projections

        with patch("services.forecast_service.db") as mock_db, \
                patch("services.forecast_service.get_levy_proposed_amounts", return_value=(340870.0, 99504.0)):
            mock_db.annual_levies.find_one = AsyncMock(return_value={"proposed_admin_expenses": 340870.0})
            mock_db.financial_forecasts.aggregate = MagicMock(
                return_value=_agg_cursor([{"_id": None, "y1": 367000.0, "y2": 415000.0, "y3": 463000.0}])
            )

            result = await get_admin_fund_projections("13195", 2026, extra_years=0)

        for row in result[1:4]:
            assert row["type"] == "forecast", f"Expected forecast, got {row['type']}"
            assert row["source"] == "category_forecasts", f"Expected category_forecasts, got {row['source']}"

    @pytest.mark.asyncio
    async def test_missing_forecasts_sets_estimate_and_cpi_fallback(self):
        """Empty aggregation result → type=estimate, source=cpi_fallback."""
        from services.forecast_service import get_admin_fund_projections

        with patch("services.forecast_service.db") as mock_db, \
                patch("services.forecast_service.get_levy_proposed_amounts", return_value=(340870.0, 99504.0)):
            mock_db.annual_levies.find_one = AsyncMock(return_value={"proposed_admin_expenses": 340870.0})
            mock_db.financial_forecasts.aggregate = MagicMock(return_value=_agg_cursor([]))

            result = await get_admin_fund_projections("13195", 2026, extra_years=0)

        for row in result[1:4]:
            assert row["type"] == "estimate", f"Expected estimate, got {row['type']}"
            assert row["source"] == "cpi_fallback", f"Expected cpi_fallback, got {row['source']}"

    @pytest.mark.asyncio
    async def test_cpi_fallback_values_are_compounded(self):
        """CPI fallback must compound 3.5% from admin_base."""
        from services.forecast_service import get_admin_fund_projections, _CPI

        admin_base = 300000.0

        with patch("services.forecast_service.db") as mock_db, \
                patch("services.forecast_service.get_levy_proposed_amounts", return_value=(admin_base, 0.0)):
            mock_db.annual_levies.find_one = AsyncMock(return_value={"proposed_admin_expenses": admin_base})
            mock_db.financial_forecasts.aggregate = MagicMock(return_value=_agg_cursor([]))

            result = await get_admin_fund_projections("TEST_BUILDING", 2026, extra_years=0)

        assert result[0]["balance"] == admin_base
        assert result[1]["balance"] == round(admin_base * (1 + _CPI), 2)
        assert result[2]["balance"] == round(admin_base * ((1 + _CPI) ** 2), 2)
        assert result[3]["balance"] == round(admin_base * ((1 + _CPI) ** 3), 2)

    @pytest.mark.asyncio
    async def test_no_levy_doc_uses_zero_not_hardcoded_east_gate_value(self):
        """When no annual_levies doc exists, admin_base must be 0.0, not 340870."""
        from services.forecast_service import get_admin_fund_projections

        with patch("services.forecast_service.db") as mock_db, \
                patch("services.forecast_service.get_levy_proposed_amounts", return_value=(0.0, 0.0)):
            mock_db.annual_levies.find_one = AsyncMock(return_value=None)
            mock_db.financial_forecasts.aggregate = MagicMock(return_value=_agg_cursor([]))

            result = await get_admin_fund_projections("NEW_BUILDING", 2026, extra_years=0)

        assert result[0]["balance"] == 0.0, (
            f"Expected 0.0 for unknown building, got {result[0]['balance']} "
            "(hardcoded East Gate value must NOT be used for other buildings)"
        )

    @pytest.mark.asyncio
    async def test_base_year_row_always_has_actual_budget_type(self):
        from services.forecast_service import get_admin_fund_projections

        with patch("services.forecast_service.db") as mock_db, \
                patch("services.forecast_service.get_levy_proposed_amounts", return_value=(200000.0, 50000.0)):
            mock_db.annual_levies.find_one = AsyncMock(return_value={"proposed_admin_expenses": 200000.0})
            mock_db.financial_forecasts.aggregate = MagicMock(
                return_value=_agg_cursor([{"_id": None, "y1": 210000.0, "y2": 220000.0, "y3": 230000.0}])
            )

            result = await get_admin_fund_projections("ANY", 2026, extra_years=0)

        assert result[0]["type"] == "actual_budget"
        assert result[0]["source"] == "proposed_budget"

    @pytest.mark.asyncio
    async def test_extra_cpi_years_appended_as_estimate(self):
        """extra_years > 0 should append CPI-compounded estimate rows."""
        from services.forecast_service import get_admin_fund_projections

        with patch("services.forecast_service.db") as mock_db, \
                patch("services.forecast_service.get_levy_proposed_amounts", return_value=(340870.0, 99504.0)):
            mock_db.annual_levies.find_one = AsyncMock(return_value={"proposed_admin_expenses": 340870.0})
            mock_db.financial_forecasts.aggregate = MagicMock(
                return_value=_agg_cursor([{"_id": None, "y1": 367000.0, "y2": 415000.0, "y3": 463000.0}])
            )

            result = await get_admin_fund_projections("13195", 2026, extra_years=2)

        # base + Y1 + Y2 + Y3 + 2 extra = 6 rows
        assert len(result) == 6
        for row in result[4:]:
            assert row["type"] == "estimate"
            assert row["source"].startswith("cpi_")


# ─── get_sinking_fund_projections ─────────────────────────────────────────────

class TestGetSinkingFundProjections:

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_plan_data(self):
        """No sinking_fund_plan rows → returns [] (callers must handle fallback)."""
        from services.forecast_service import get_sinking_fund_projections

        with patch("services.forecast_service.db") as mock_db:
            mock_db.sinking_fund_plan.find.return_value = _find_cursor([])

            result = await get_sinking_fund_projections("NEW_BUILDING", max_years=15)

        assert result == []

    @pytest.mark.asyncio
    async def test_maps_closing_balance_to_balance_field(self):
        from services.forecast_service import get_sinking_fund_projections

        plan_row = {
            "year": 2026,
            "opening_balance": 200000.0,
            "closing_balance": 250000.0,
            "contribution": 80000.0,
            "expenditure": 30000.0,
            "categories": {},
            "category_labels": {},
            "is_actual": False,
        }

        with patch("services.forecast_service.db") as mock_db:
            mock_db.sinking_fund_plan.find.return_value = _find_cursor([plan_row])

            result = await get_sinking_fund_projections("13195", max_years=15)

        assert len(result) == 1
        assert result[0]["balance"] == 250000.0
        assert result[0]["source"] == "sinking_fund_plan"

    @pytest.mark.asyncio
    async def test_capital_events_only_include_nonzero_costs(self):
        from services.forecast_service import get_sinking_fund_projections

        plan_row = {
            "year": 2029,
            "opening_balance": 487000.0,
            "closing_balance": 194000.0,
            "contribution": 120000.0,
            "expenditure": 413000.0,
            "categories": {"repaint": 413000.0, "minor_works": 0},
            "category_labels": {"repaint": "Building Facade Repaint", "minor_works": "Minor Works"},
            "is_actual": False,
        }

        with patch("services.forecast_service.db") as mock_db:
            mock_db.sinking_fund_plan.find.return_value = _find_cursor([plan_row])

            result = await get_sinking_fund_projections("13195", max_years=15)

        events = result[0]["capital_events"]
        assert len(events) == 1
        assert events[0]["item"] == "Building Facade Repaint"
        assert events[0]["cost"] == 413000.0

    @pytest.mark.asyncio
    async def test_actual_rows_have_type_actual(self):
        from services.forecast_service import get_sinking_fund_projections

        plan_row = {
            "year": 2025,
            "opening_balance": 150000.0,
            "closing_balance": 212644.0,
            "contribution": 80000.0,
            "expenditure": 17356.0,
            "categories": {},
            "category_labels": {},
            "is_actual": True,
        }

        with patch("services.forecast_service.db") as mock_db:
            mock_db.sinking_fund_plan.find.return_value = _find_cursor([plan_row])

            result = await get_sinking_fund_projections("13195", max_years=15)

        assert result[0]["type"] == "actual"
        assert result[0]["is_actual"] is True
