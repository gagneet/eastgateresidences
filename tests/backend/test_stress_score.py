"""Tests for Building Financial Stress Score — Phase 2 (7-component model)."""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from services.building_stress_service import (
    STRESS_COMPONENTS,
    compute_phase2_stress_score,
)

BUILDING_ID = "13195"
OTHER_BUILDING_ID = "16244"


# ── Component configuration tests ─────────────────────────────────────────

class TestStressComponents:
    def test_seven_components_defined(self):
        """Exactly 7 components must be present."""
        assert len(STRESS_COMPONENTS) == 7

    def test_weights_sum_to_one(self):
        """Component weights must sum to 1.0 (within floating-point tolerance)."""
        total = sum(c["weight"] for c in STRESS_COMPONENTS.values())
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, expected 1.0"

    def test_required_component_keys(self):
        required = {
            "reserve_adequacy",
            "arrears_level",
            "arrears_concentration",
            "unreconciled_exposure",
            "maintenance_pressure",
            "liquidity_coverage",
            "capital_shock_indicator",
        }
        assert required == set(STRESS_COMPONENTS.keys())

    def test_each_component_has_weight_and_description(self):
        for name, comp in STRESS_COMPONENTS.items():
            assert "weight" in comp, f"{name} missing 'weight'"
            assert "description" in comp, f"{name} missing 'description'"
            assert 0 < comp["weight"] <= 1.0, f"{name} weight out of range"


# ── Category threshold tests ───────────────────────────────────────────────

class TestCategoryThresholds:
    """Category boundaries: 0–25 healthy, 26–50 watch, 51–75 elevated, 76–100 critical."""

    def _mock_score_result(self, score: float) -> dict:
        """Build a minimal result dict as returned by compute_phase2_stress_score."""
        components = {
            k: {
                "raw_value": 0.1,
                "normalised_score": score,
                "weight": v["weight"],
                "weighted_contribution": score * v["weight"],
                "status": "ok",
                "explanation": "test",
            }
            for k, v in STRESS_COMPONENTS.items()
        }
        return {
            "building_id": BUILDING_ID,
            "score": score,
            "components": components,
            "data_completeness": 1.0,
            "graceful_degradation_note": None,
        }

    def test_score_20_is_healthy(self):
        from services.building_stress_service import _stress_category
        assert _stress_category(20.0) == "healthy"

    def test_score_25_is_healthy(self):
        from services.building_stress_service import _stress_category
        assert _stress_category(25.0) == "healthy"

    def test_score_26_is_watch(self):
        from services.building_stress_service import _stress_category
        assert _stress_category(26.0) == "watch"

    def test_score_50_is_watch(self):
        from services.building_stress_service import _stress_category
        assert _stress_category(50.0) == "watch"

    def test_score_51_is_elevated(self):
        from services.building_stress_service import _stress_category
        assert _stress_category(51.0) == "elevated"

    def test_score_75_is_elevated(self):
        from services.building_stress_service import _stress_category
        assert _stress_category(75.0) == "elevated"

    def test_score_76_is_critical(self):
        from services.building_stress_service import _stress_category
        assert _stress_category(76.0) == "critical"

    def test_score_100_is_critical(self):
        from services.building_stress_service import _stress_category
        assert _stress_category(100.0) == "critical"


# ── Full computation tests (mocked DB) ────────────────────────────────────

def _make_cursor_mock(return_value=None):
    """Helper: return a MagicMock that behaves like a Motor cursor (supports .to_list)."""
    return MagicMock(to_list=AsyncMock(return_value=return_value or []))


def _mock_full_db():
    """Return a mock DB with enough data for all 7 components."""
    db = MagicMock()

    # financial_years: sinking fund + admin fund (called by _phase2_reserve_adequacy,
    # _phase2_arrears_concentration, _phase2_liquidity_coverage)
    async def mock_fy_find_one(query, *args, **kwargs):
        fund = (query or {}).get("fund_type", "")
        if fund == "sinking":
            return {"closing_balance": 500_000, "budgeted_expenditure": 100_000}
        if fund == "admin":
            return {"closing_balance": 200_000, "budgeted_expenditure": 80_000}
        return None

    db.financial_years.find_one = AsyncMock(side_effect=mock_fy_find_one)

    # annual_levies: sinking + admin fund totals (called by _phase2_reserve_adequacy,
    # _phase2_arrears_level, _phase2_liquidity_coverage)
    db.annual_levies.find_one = AsyncMock(return_value={
        "admin_fund": {"total_expenses": 80_000, "levy_income": 200_000},
        "sinking_fund": {"total_expenses": 50_000, "levy_income": 100_000},
    })

    # unit_levy_ledger.aggregate: overdue check + levy total + arrears concentration
    db.unit_levy_ledger.aggregate = MagicMock(return_value=_make_cursor_mock(
        [{"total_overdue": 25_000, "total": 500_000, "max_arrears": 10_000, "total_arrears": 25_000}]
    ))

    # unit_levy_ledger.find: last-payment recency lookup in _phase2_arrears_level
    # (no matching mock was ever added when that call was introduced -- MagicMock's
    # default auto-mocked .find(...).to_list(...) is not awaitable, so this silently
    # broke every test in this class with "object MagicMock can't be used in 'await'
    # expression", not a production bug -- production's own await chain is correct).
    db.unit_levy_ledger.find = MagicMock(return_value=_make_cursor_mock([]))

    # financial_transactions.aggregate: fallback for unreconciled exposure + expense totals
    db.financial_transactions.aggregate = MagicMock(return_value=_make_cursor_mock([]))

    # bank_reconciliations.find_one: fallback for unreconciled exposure
    db.bank_reconciliations.find_one = AsyncMock(return_value=None)

    # building_assets.find: used by _phase2_maintenance_pressure
    db.building_assets.find = MagicMock(return_value=_make_cursor_mock([
        {"condition": "good"},
        {"condition": "poor"},  # 1 high-risk out of 2 total
    ]))

    # digital_twin.find: also checked in _phase2_maintenance_pressure
    db.digital_twin.find = MagicMock(return_value=_make_cursor_mock([]))

    # capital_shock_assessments.find_one: used by _phase2_capital_shock_indicator fallback
    db.capital_shock_assessments.find_one = AsyncMock(return_value={
        "capital_shock_index": 0.3
    })

    # capital_works.aggregate: used in capital shock computation
    db.capital_works.aggregate = MagicMock(return_value=_make_cursor_mock(
        [{"total": 800_000}]
    ))

    # building_financial_health_p2: trend lookup + persistence write
    db.building_financial_health_p2.find_one = AsyncMock(return_value=None)
    db.building_financial_health_p2.update_one = AsyncMock(return_value=None)

    return db


class TestComputePhase2StressScore:
    async def test_returns_all_required_keys(self):
        db = _mock_full_db()
        with patch("services.building_stress_service.db", db):
            result = await compute_phase2_stress_score(BUILDING_ID)
        for key in ("building_id", "score", "category", "components", "data_completeness", "computed_at"):
            assert key in result, f"Missing key: {key}"

    async def test_score_is_between_0_and_100(self):
        db = _mock_full_db()
        with patch("services.building_stress_service.db", db):
            result = await compute_phase2_stress_score(BUILDING_ID)
        assert 0.0 <= result["score"] <= 100.0

    async def test_category_is_valid_string(self):
        db = _mock_full_db()
        with patch("services.building_stress_service.db", db):
            result = await compute_phase2_stress_score(BUILDING_ID)
        assert result["category"] in ("healthy", "watch", "elevated", "critical")

    async def test_data_completeness_between_0_and_1(self):
        db = _mock_full_db()
        with patch("services.building_stress_service.db", db):
            result = await compute_phase2_stress_score(BUILDING_ID)
        assert 0.0 <= result["data_completeness"] <= 1.0

    async def test_graceful_degradation_on_empty_db(self):
        """When all DB queries return None/empty, data_completeness <= 1.0 and no crash."""
        db = MagicMock()
        db.financial_years.find_one = AsyncMock(return_value=None)
        db.annual_levies.find_one = AsyncMock(return_value=None)
        db.unit_levy_ledger.aggregate = MagicMock(return_value=_make_cursor_mock([]))
        db.unit_levy_ledger.find = MagicMock(return_value=_make_cursor_mock([]))
        db.financial_transactions.aggregate = MagicMock(return_value=_make_cursor_mock([]))
        db.bank_reconciliations.find_one = AsyncMock(return_value=None)
        db.building_assets.find = MagicMock(return_value=_make_cursor_mock([]))
        db.digital_twin.find = MagicMock(return_value=_make_cursor_mock([]))
        db.capital_shock_assessments.find_one = AsyncMock(return_value=None)
        db.capital_works.aggregate = MagicMock(return_value=_make_cursor_mock([]))
        db.building_financial_health_p2.find_one = AsyncMock(return_value=None)
        db.building_financial_health_p2.update_one = AsyncMock(return_value=None)

        with patch("services.building_stress_service.db", db):
            result = await compute_phase2_stress_score(BUILDING_ID)
        # When no data, should return a valid result (graceful degradation — no crash)
        assert "score" in result
        assert 0.0 <= result["score"] <= 100.0
        assert result["data_completeness"] <= 1.0

    async def test_components_contain_seven_entries(self):
        db = _mock_full_db()
        with patch("services.building_stress_service.db", db):
            result = await compute_phase2_stress_score(BUILDING_ID)
        assert len(result["components"]) == 7

    async def test_building_id_in_result(self):
        db = _mock_full_db()
        with patch("services.building_stress_service.db", db):
            result = await compute_phase2_stress_score(BUILDING_ID)
        assert result["building_id"] == BUILDING_ID
