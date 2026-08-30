"""GAP-FIN-065 — GST-basis consistency in the intelligence services.

annual_levies.*.levy_income is EX-GST; the canonical finance pages present the inc-GST
payable amount (ex-GST x gst_multiplier). capital_shock_service and building_stress_service
previously read levy_income WITHOUT the multiplier, so their figures diverged from the
dashboard by ~the GST rate. These tests lock in the fix: the ex-GST income is converted to
the inc-GST payable basis before use.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestCapitalShockBaselineGst:
    @pytest.mark.asyncio
    async def test_baseline_income_is_converted_to_inc_gst(self):
        from services import capital_shock_service

        mock_db = MagicMock()
        mock_db.annual_levies.find_one = AsyncMock(
            return_value={
                "admin_fund": {"levy_income": 100000.0, "total_expenses": 0},
                "sinking_fund": {"levy_income": 50000.0, "closing_balance": 0, "total_expenses": 0},
            }
        )
        with patch("services.capital_shock_service.db", mock_db), patch(
            "services.capital_shock_service.get_latest_levy_year", AsyncMock(return_value="2026")
        ), patch(
            "services.capital_shock_service.get_building_levy_gst_settings",
            AsyncMock(return_value={"gst_multiplier": 1.1}),
        ):
            baseline = await capital_shock_service._get_financial_baseline("13195")

        # ex-GST 150,000 -> inc-GST 165,000 (contribution basis the reserve schedule uses)
        assert baseline["total_levy_income"] == pytest.approx(165000.0)
        assert baseline["sinking_levy_income"] == pytest.approx(55000.0)

    @pytest.mark.asyncio
    async def test_baseline_unregistered_building_multiplier_1_is_noop(self):
        from services import capital_shock_service

        mock_db = MagicMock()
        mock_db.annual_levies.find_one = AsyncMock(
            return_value={
                "admin_fund": {"levy_income": 100000.0},
                "sinking_fund": {"levy_income": 50000.0, "closing_balance": 0},
            }
        )
        with patch("services.capital_shock_service.db", mock_db), patch(
            "services.capital_shock_service.get_latest_levy_year", AsyncMock(return_value="2026")
        ), patch(
            "services.capital_shock_service.get_building_levy_gst_settings",
            AsyncMock(return_value={"gst_multiplier": 1.0}),  # not GST-registered
        ):
            baseline = await capital_shock_service._get_financial_baseline("13195")

        assert baseline["total_levy_income"] == pytest.approx(150000.0)
        assert baseline["sinking_levy_income"] == pytest.approx(50000.0)


class TestBuildingStressArrearsGst:
    @pytest.mark.asyncio
    async def test_arrears_denominator_is_inc_gst(self):
        """arrears_pct = inc-GST overdue / inc-GST annual income. The denominator must be
        converted from the ex-GST levy_income so the ratio is apples-to-apples."""
        from services import building_stress_service

        mock_db = MagicMock()
        # annual_levies.find_one(sort=...) -> ex-GST income totalling 100,000
        mock_db.annual_levies.find_one = AsyncMock(
            return_value={
                "admin_fund": {"levy_income": 80000.0},
                "sinking_fund": {"levy_income": 20000.0},
            }
        )
        # recency lookup: no last_payment rows -> every arrears unit counts as >30 days
        recency_cursor = MagicMock()
        recency_cursor.to_list = AsyncMock(return_value=[])
        mock_db.unit_levy_ledger.find.return_value = recency_cursor

        arrears_map = {"UA001": {"in_arrears": True, "arrears": 1100.0}}
        with patch("services.building_stress_service.db", mock_db), patch(
            "utils.finance_helpers.get_unit_arrears_map", AsyncMock(return_value=arrears_map)
        ), patch(
            "services.gst_service.get_building_levy_gst_settings",
            AsyncMock(return_value={"gst_multiplier": 1.1}),
        ):
            result = await building_stress_service._phase2_arrears_level("13195")

        # 1100 / (100000 * 1.1) = 0.01 exactly; the pre-fix bug gave 1100/100000 = 0.011.
        assert result["raw_value"] == pytest.approx(0.01, abs=1e-4)


class _AsyncIter:
    """Minimal async iterator so `async for x in db.coll.find(...)` can be mocked."""

    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class TestCapitalShockArrearsConcentrationGst:
    @pytest.mark.asyncio
    async def test_arrears_concentration_denominator_is_inc_gst(self):
        """GAP-FIN-065 Item C: arrears_concentration_index = inc-GST arrears /
        inc-GST levy income. The denominator (annual_levies.levy_income, EX-GST) must be
        converted with gst_multiplier — the same fix as building_stress's arrears ratio."""
        from services import capital_shock_service

        mock_db = MagicMock()
        mock_db.trust_accounts.find = MagicMock(return_value=_AsyncIter([]))
        mock_db.bank_statement_lines.aggregate = MagicMock(
            return_value=MagicMock(to_list=AsyncMock(return_value=[]))
        )
        # arrears outstanding = $1,100 inc-GST = 110,000 cents
        mock_db.trust_levy_schedules.aggregate = MagicMock(
            return_value=MagicMock(to_list=AsyncMock(return_value=[{"total": 110_000, "count": 5}]))
        )
        # ex-GST income totalling 100,000
        mock_db.annual_levies.find_one = AsyncMock(
            return_value={
                "admin_fund": {"levy_income": 80000.0, "total_expenses": 0},
                "sinking_fund": {"levy_income": 20000.0},
            }
        )
        with patch("services.capital_shock_service.db", mock_db), patch(
            "services.capital_shock_service.get_latest_levy_year", AsyncMock(return_value="2026")
        ), patch(
            "services.capital_shock_service.get_building_levy_gst_settings",
            AsyncMock(return_value={"gst_multiplier": 1.1}),
        ):
            signals = await capital_shock_service._get_trust_signals("13195")

        # 110,000 / (100,000 * 1.1 * 100) = 0.01 exactly.
        # Pre-fix bug: 110,000 / (100,000 * 100) = 0.011.
        assert signals["arrears_concentration_index"] == pytest.approx(0.01, abs=1e-4)

    @pytest.mark.asyncio
    async def test_arrears_concentration_unregistered_multiplier_1_is_noop(self):
        from services import capital_shock_service

        mock_db = MagicMock()
        mock_db.trust_accounts.find = MagicMock(return_value=_AsyncIter([]))
        mock_db.bank_statement_lines.aggregate = MagicMock(
            return_value=MagicMock(to_list=AsyncMock(return_value=[]))
        )
        mock_db.trust_levy_schedules.aggregate = MagicMock(
            return_value=MagicMock(to_list=AsyncMock(return_value=[{"total": 110_000, "count": 5}]))
        )
        mock_db.annual_levies.find_one = AsyncMock(
            return_value={
                "admin_fund": {"levy_income": 80000.0, "total_expenses": 0},
                "sinking_fund": {"levy_income": 20000.0},
            }
        )
        with patch("services.capital_shock_service.db", mock_db), patch(
            "services.capital_shock_service.get_latest_levy_year", AsyncMock(return_value="2026")
        ), patch(
            "services.capital_shock_service.get_building_levy_gst_settings",
            AsyncMock(return_value={"gst_multiplier": 1.0}),
        ):
            signals = await capital_shock_service._get_trust_signals("13195")

        # 110,000 / (100,000 * 1.0 * 100) = 0.011.
        assert signals["arrears_concentration_index"] == pytest.approx(0.011, abs=1e-4)


class TestLevyScenariosCurrentRatesFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_annual_levies_inc_gst_when_levies_empty(self):
        """GAP-FIN-065 Item B: db.levies is a legacy/unwritten collection. When it has no
        row, current rates come from annual_levies as owner-payable (inc-GST) = ex-GST
        proposed × gst_multiplier."""
        from routers import levy_scenarios

        mock_db = MagicMock()
        mock_db.levies.find_one = AsyncMock(return_value=None)  # legacy collection empty
        mock_db.annual_levies.find_one = AsyncMock(
            return_value={
                "total_uoe": 100,
                "admin_fund": {"levy_income": 100000.0},
                "sinking_fund": {"levy_income": 50000.0},
            }
        )
        with patch("routers.levy_scenarios.db", mock_db), patch(
            "routers.levy_scenarios.get_building_levy_gst_settings",
            AsyncMock(return_value={"gst_multiplier": 1.1}),
        ):
            rates = await levy_scenarios._get_current_levy_rates("13195", "2026-2027")

        assert rates["source"] == "annual_levies_inc_gst"
        # ex-GST 100,000 × 1.1 = 110,000 admin; rate = 110,000 / 100 UOE = 1,100
        assert rates["total_admin_budget"] == pytest.approx(110000.0)
        assert rates["total_sinking_budget"] == pytest.approx(55000.0)
        assert rates["current_admin_rate_per_uoe"] == pytest.approx(1100.0)

    @pytest.mark.asyncio
    async def test_legacy_levies_row_read_as_is_no_multiplier(self):
        """A legacy db.levies row is read as-is (unverifiable basis) — NOT multiplied."""
        from routers import levy_scenarios

        mock_db = MagicMock()
        mock_db.levies.find_one = AsyncMock(
            return_value={
                "total_uoe": 100,
                "admin_fund": {"total_levy": 120000.0},
                "sinking_fund": {"total_levy": 60000.0},
            }
        )
        with patch("routers.levy_scenarios.db", mock_db):
            rates = await levy_scenarios._get_current_levy_rates("13195", "2026-2027")

        assert rates["source"] == "levies_legacy"
        assert rates["total_admin_budget"] == pytest.approx(120000.0)
        assert rates["current_admin_rate_per_uoe"] == pytest.approx(1200.0)
