"""
Tests for trust signal logic in capital_shock_service.py.

Covers:
- _risk_level() thresholds
- _classify_csi() tier logic
- _classify_collapse_risk() with various year values
- _classify_sfar() status logic
- _median_baseline() with various inputs
- detect_capital_shocks() result contains trust_signals key (mocked db)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Async cursor mock helper ─────────────────────────────────────────────────

class _AsyncCursorMock:
    """Minimal Motor cursor mock supporting to_list(), sort(), and async-for."""

    def __init__(self, items=None):
        self._items = list(items or [])

    def to_list(self, n=None):
        async def _ret(_n):
            return self._items

        return _ret(n)

    def sort(self, *args, **kwargs):
        return self

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        for item in self._items:
            yield item


def _make_db_mock():
    """Return a MagicMock db whose common Motor patterns are properly async."""
    mock_db = MagicMock()

    empty_cursor = _AsyncCursorMock([])

    # All collection.find() → empty cursor
    for coll_name in (
            "capital_replacement_schedule",
            "sinking_fund_plan",
            "sinking_fund_capital_events",
            "trust_accounts",
    ):
        getattr(mock_db, coll_name).find.return_value = empty_cursor

    # find_one → None
    mock_db.maintenance_forecasts.find_one = AsyncMock(return_value=None)
    mock_db.annual_levies.find_one = AsyncMock(return_value=None)

    # aggregate().to_list() → []
    agg_cursor = _AsyncCursorMock([])
    mock_db.bank_statement_lines.aggregate.return_value = agg_cursor
    mock_db.trust_levy_schedules.aggregate.return_value = agg_cursor

    # update_one
    mock_db.capital_shock_risks.update_one = AsyncMock(return_value=MagicMock())

    return mock_db


# ─── _risk_level ─────────────────────────────────────────────────────────────

class TestRiskLevel:
    def _fn(self):
        from services.capital_shock_service import _risk_level
        return _risk_level

    def test_critical_at_exactly_50_pct(self):
        assert self._fn()(0.5) == "CRITICAL"

    def test_critical_above_50_pct(self):
        assert self._fn()(0.9) == "CRITICAL"
        assert self._fn()(1.0) == "CRITICAL"

    def test_high_at_25_to_49_pct(self):
        assert self._fn()(0.25) == "HIGH"
        assert self._fn()(0.35) == "HIGH"
        assert self._fn()(0.49) == "HIGH"

    def test_medium_at_10_to_24_pct(self):
        assert self._fn()(0.10) == "MEDIUM"
        assert self._fn()(0.15) == "MEDIUM"
        assert self._fn()(0.24) == "MEDIUM"

    def test_low_below_10_pct(self):
        assert self._fn()(0.0) == "LOW"
        assert self._fn()(0.05) == "LOW"
        assert self._fn()(0.09) == "LOW"

    def test_boundary_exactly_25_pct_is_high(self):
        assert self._fn()(0.25) == "HIGH"

    def test_boundary_exactly_10_pct_is_medium(self):
        assert self._fn()(0.10) == "MEDIUM"


# ─── _classify_csi ────────────────────────────────────────────────────────────

class TestClassifyCsi:
    def _fn(self):
        from services.capital_shock_service import _classify_csi
        return _classify_csi

    def test_below_0_5_is_normal(self):
        result = self._fn()(0.3)
        assert result["risk_level"] == "normal"
        assert result["shock_category"] == "normal_maintenance"

    def test_exactly_0_5_is_moderate(self):
        result = self._fn()(0.5)
        assert result["risk_level"] == "moderate"

    def test_1_5_is_moderate_spike(self):
        result = self._fn()(1.5)
        assert result["risk_level"] == "moderate"
        assert result["shock_category"] == "moderate_spike"

    def test_2_5_is_major_capital_event(self):
        result = self._fn()(2.5)
        assert result["risk_level"] == "major"
        assert result["shock_category"] == "major_capital_event"

    def test_exactly_2_0_is_major(self):
        result = self._fn()(2.0)
        assert result["risk_level"] == "major"

    def test_very_large_is_severe(self):
        result = self._fn()(100.0)
        assert result["risk_level"] == "severe"
        assert result["shock_category"] == "severe_capital_shock"

    def test_zero_is_normal(self):
        result = self._fn()(0.0)
        assert result["risk_level"] == "normal"

    def test_result_has_all_keys(self):
        result = self._fn()(1.0)
        assert "risk_level" in result
        assert "label" in result
        assert "shock_category" in result


# ─── _classify_collapse_risk ──────────────────────────────────────────────────

class TestClassifyCollapseRisk:
    def _fn(self):
        from services.capital_shock_service import _classify_collapse_risk
        return _classify_collapse_risk

    def test_none_is_stable(self):
        result = self._fn()(None)
        assert result["risk_level"] == "stable"

    def test_less_than_5_years_is_critical(self):
        assert self._fn()(1)["risk_level"] == "critical"
        assert self._fn()(4)["risk_level"] == "critical"

    def test_exactly_5_years_is_risk(self):
        assert self._fn()(5)["risk_level"] == "risk"

    def test_5_to_9_years_is_risk(self):
        assert self._fn()(7)["risk_level"] == "risk"
        assert self._fn()(9)["risk_level"] == "risk"

    def test_exactly_10_years_is_watch(self):
        assert self._fn()(10)["risk_level"] == "watch"

    def test_10_to_14_years_is_watch(self):
        assert self._fn()(12)["risk_level"] == "watch"
        assert self._fn()(14)["risk_level"] == "watch"

    def test_15_or_more_is_stable(self):
        assert self._fn()(15)["risk_level"] == "stable"
        assert self._fn()(20)["risk_level"] == "stable"

    def test_result_has_label(self):
        result = self._fn()(3)
        assert "label" in result
        assert "<5 years" in result["label"]


# ─── _classify_sfar ───────────────────────────────────────────────────────────

class TestClassifySfar:
    def _fn(self):
        from services.capital_shock_service import _classify_sfar
        return _classify_sfar

    def test_none_is_complete(self):
        result = self._fn()(None)
        assert result["status"] == "complete"

    def test_above_1_3_is_strong(self):
        result = self._fn()(1.5)
        assert result["status"] == "strong"
        assert self._fn()(2.0)["status"] == "strong"

    def test_exactly_1_3_is_adequate(self):
        # value > 1.3 → strong; 1.3 itself falls to >= 1.0 → adequate
        result = self._fn()(1.3)
        assert result["status"] == "adequate"

    def test_1_0_to_1_3_is_adequate(self):
        assert self._fn()(1.0)["status"] == "adequate"
        assert self._fn()(1.2)["status"] == "adequate"

    def test_0_8_to_0_99_is_underfunded(self):
        assert self._fn()(0.8)["status"] == "underfunded"
        assert self._fn()(0.9)["status"] == "underfunded"
        assert self._fn()(0.99)["status"] == "underfunded"

    def test_below_0_8_is_high_risk(self):
        assert self._fn()(0.7)["status"] == "high_risk"
        assert self._fn()(0.0)["status"] == "high_risk"

    def test_result_has_status_and_label(self):
        result = self._fn()(1.1)
        assert "status" in result
        assert "label" in result


# ─── _median_baseline ────────────────────────────────────────────────────────

class TestMedianBaseline:
    def _fn(self):
        from services.capital_shock_service import _median_baseline
        return _median_baseline

    def test_empty_list_returns_zero(self):
        assert self._fn()([]) == 0.0

    def test_all_zeros_returns_zero(self):
        assert self._fn()([0.0, 0.0, 0.0]) == 0.0

    def test_single_positive_value(self):
        assert self._fn()([100.0]) == 100.0

    def test_median_of_three(self):
        assert self._fn()([10.0, 20.0, 30.0]) == 20.0

    def test_zeros_are_excluded(self):
        # [0, 10, 20, 30] → clean = [10, 20, 30] → median = 20
        result = self._fn()([0.0, 10.0, 20.0, 30.0])
        assert result == 20.0

    def test_large_values(self):
        result = self._fn()([500000.0, 600000.0, 700000.0])
        assert result == 600000.0

    def test_returns_float(self):
        result = self._fn()([1.0, 2.0, 3.0])
        assert isinstance(result, float)

    def test_even_number_of_values(self):
        result = self._fn()([10.0, 20.0, 30.0, 40.0])
        assert result == 25.0


# ─── detect_capital_shocks trust_signals integration ─────────────────────────

class TestDetectCapitalShocksTrustSignals:
    """
    Verify that detect_capital_shocks returns a result dict containing trust_signals.
    All DB calls are mocked.
    """

    @pytest.mark.asyncio
    async def test_result_contains_trust_signals_key(self):
        """detect_capital_shocks must always set result['trust_signals']."""
        mock_db = _make_db_mock()

        with patch("services.capital_shock_service.db", mock_db), \
                patch("services.capital_shock_service.get_latest_levy_year",
                      new=AsyncMock(return_value=None)), \
                patch("services.capital_shock_service._get_trust_signals",
                      new=AsyncMock(return_value={"collection_status": "ok"})), \
                patch("services.maintenance_intelligence_service.get_intelligence_settings",
                      new=AsyncMock(return_value={"reserve_target_months": 12})):
            from services.capital_shock_service import detect_capital_shocks
            result = await detect_capital_shocks("building_test")

        assert "trust_signals" in result

    @pytest.mark.asyncio
    async def test_trust_signals_is_dict(self):
        """trust_signals value must be a dict even when DB is empty."""
        mock_db = _make_db_mock()

        with patch("services.capital_shock_service.db", mock_db), \
                patch("services.capital_shock_service.get_latest_levy_year",
                      new=AsyncMock(return_value=None)), \
                patch("services.capital_shock_service._get_trust_signals",
                      new=AsyncMock(return_value={})), \
                patch("services.maintenance_intelligence_service.get_intelligence_settings",
                      new=AsyncMock(return_value={})):
            from services.capital_shock_service import detect_capital_shocks
            result = await detect_capital_shocks("building_test")

        assert isinstance(result["trust_signals"], dict)

    @pytest.mark.asyncio
    async def test_trust_signals_defaults_when_signal_raises(self):
        """If _get_trust_signals raises, trust_signals falls back to {}."""
        mock_db = _make_db_mock()

        with patch("services.capital_shock_service.db", mock_db), \
                patch("services.capital_shock_service.get_latest_levy_year",
                      new=AsyncMock(return_value=None)), \
                patch("services.capital_shock_service._get_trust_signals",
                      new=AsyncMock(side_effect=Exception("Trust DB unavailable"))), \
                patch("services.maintenance_intelligence_service.get_intelligence_settings",
                      new=AsyncMock(return_value={})):
            from services.capital_shock_service import detect_capital_shocks
            result = await detect_capital_shocks("building_test")

        assert "trust_signals" in result
        assert isinstance(result["trust_signals"], dict)


# ─── _get_trust_signals direct tests ─────────────────────────────────────────

class TestGetTrustSignals:
    """
    Directly test _get_trust_signals with various DB states.
    """

    @pytest.mark.asyncio
    async def test_returns_dict_with_expected_keys(self):
        """_get_trust_signals must always return a dict with standard signal keys."""
        mock_db = MagicMock()
        mock_db.trust_accounts.find.return_value = _AsyncCursorMock([])
        mock_db.bank_statement_lines.aggregate.return_value = _AsyncCursorMock([])
        mock_db.trust_levy_schedules.aggregate.return_value = _AsyncCursorMock([])
        mock_db.annual_levies.find_one = AsyncMock(return_value=None)

        with patch("services.capital_shock_service.db", mock_db), \
                patch("services.capital_shock_service.get_latest_levy_year",
                      new=AsyncMock(return_value=None)):
            from services.capital_shock_service import _get_trust_signals
            signals = await _get_trust_signals("building_test")

        assert isinstance(signals, dict)
        assert "trust_cash_admin_cents" in signals
        assert "trust_cash_sinking_cents" in signals
        assert "total_unreconciled_cents" in signals
        assert "arrears_total_cents" in signals
        assert "collection_status" in signals

    @pytest.mark.asyncio
    async def test_empty_db_returns_zero_balances(self):
        """With no data, all numeric signals should be 0."""
        mock_db = MagicMock()
        mock_db.trust_accounts.find.return_value = _AsyncCursorMock([])
        mock_db.bank_statement_lines.aggregate.return_value = _AsyncCursorMock([])
        mock_db.trust_levy_schedules.aggregate.return_value = _AsyncCursorMock([])
        mock_db.annual_levies.find_one = AsyncMock(return_value=None)

        with patch("services.capital_shock_service.db", mock_db), \
                patch("services.capital_shock_service.get_latest_levy_year",
                      new=AsyncMock(return_value=None)):
            from services.capital_shock_service import _get_trust_signals
            signals = await _get_trust_signals("building_test")

        assert signals["trust_cash_admin_cents"] == 0
        assert signals["trust_cash_sinking_cents"] == 0
        assert signals["trust_cash_special_levy_cents"] == 0
        assert signals["total_unreconciled_cents"] == 0
        assert signals["arrears_total_cents"] == 0

    @pytest.mark.asyncio
    async def test_admin_account_balance_aggregated(self):
        """Admin-fund accounts should sum into trust_cash_admin_cents."""
        mock_db = MagicMock()
        admin_accounts = [
            {"account_type": "admin_fund", "current_balance_cents": 100000, "last_reconciled_at": None},
            {"account_type": "admin_fund_reserve", "current_balance_cents": 50000, "last_reconciled_at": None},
        ]
        mock_db.trust_accounts.find.return_value = _AsyncCursorMock(admin_accounts)
        mock_db.bank_statement_lines.aggregate.return_value = _AsyncCursorMock([])
        mock_db.trust_levy_schedules.aggregate.return_value = _AsyncCursorMock([])
        mock_db.annual_levies.find_one = AsyncMock(return_value=None)

        with patch("services.capital_shock_service.db", mock_db), \
                patch("services.capital_shock_service.get_latest_levy_year",
                      new=AsyncMock(return_value=None)):
            from services.capital_shock_service import _get_trust_signals
            signals = await _get_trust_signals("building_test")

        assert signals["trust_cash_admin_cents"] == 150000

    @pytest.mark.asyncio
    async def test_sinking_account_balance_aggregated(self):
        """Sinking/capital accounts should sum into trust_cash_sinking_cents."""
        mock_db = MagicMock()
        sinking_accounts = [
            {"account_type": "sinking_fund", "current_balance_cents": 200000, "last_reconciled_at": None},
        ]
        mock_db.trust_accounts.find.return_value = _AsyncCursorMock(sinking_accounts)
        mock_db.bank_statement_lines.aggregate.return_value = _AsyncCursorMock([])
        mock_db.trust_levy_schedules.aggregate.return_value = _AsyncCursorMock([])
        mock_db.annual_levies.find_one = AsyncMock(return_value=None)

        with patch("services.capital_shock_service.db", mock_db), \
                patch("services.capital_shock_service.get_latest_levy_year",
                      new=AsyncMock(return_value=None)):
            from services.capital_shock_service import _get_trust_signals
            signals = await _get_trust_signals("building_test")

        assert signals["trust_cash_sinking_cents"] == 200000

    @pytest.mark.asyncio
    async def test_unreconciled_items_aggregated_from_pipeline(self):
        """Unreconciled bank statement totals are read from aggregate pipeline."""
        mock_db = MagicMock()
        mock_db.trust_accounts.find.return_value = _AsyncCursorMock([])
        mock_db.bank_statement_lines.aggregate.return_value = _AsyncCursorMock(
            [{"total": 75000, "count": 5}]
        )
        mock_db.trust_levy_schedules.aggregate.return_value = _AsyncCursorMock([])
        mock_db.annual_levies.find_one = AsyncMock(return_value=None)

        with patch("services.capital_shock_service.db", mock_db), \
                patch("services.capital_shock_service.get_latest_levy_year",
                      new=AsyncMock(return_value=None)):
            from services.capital_shock_service import _get_trust_signals
            signals = await _get_trust_signals("building_test")

        assert signals["total_unreconciled_cents"] == 75000
        assert signals["unreconciled_count"] == 5

    @pytest.mark.asyncio
    async def test_partial_failure_sets_collection_status(self):
        """If an exception occurs mid-collection, status should reflect partial_failure."""
        mock_db = MagicMock()

        # trust_accounts.find() raises mid-iteration
        class _FailingCursor:
            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                raise Exception("Simulated DB error")
                yield  # make it a generator

        mock_db.trust_accounts.find.return_value = _FailingCursor()
        mock_db.bank_statement_lines.aggregate.return_value = _AsyncCursorMock([])
        mock_db.trust_levy_schedules.aggregate.return_value = _AsyncCursorMock([])

        with patch("services.capital_shock_service.db", mock_db), \
                patch("services.capital_shock_service.get_latest_levy_year",
                      new=AsyncMock(return_value=None)):
            from services.capital_shock_service import _get_trust_signals
            signals = await _get_trust_signals("building_test")

        assert signals["collection_status"] == "partial_failure"
