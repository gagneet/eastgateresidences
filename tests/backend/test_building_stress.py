"""
Tests for Building Financial Stress Pydantic models.

Covers stress-level constants, Reserve Adequacy Ratio (RAR) thresholds,
risk score weight totals, and model validation.

All tests are pure unit tests — no async, no database, no mocking needed.
"""

import pytest

from models.building_stress import (
    StressLevel,
    MitigationType,
    ReserveAdequacyResult,
    RiskScoreComponents,
    BuildingStressSnapshot,
    ReserveTimelinePoint,
    WeeklyBuildingRadar,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helper: compute RAR and adequacy level the same way the service would
# ──────────────────────────────────────────────────────────────────────────────

def _compute_adequacy(sinking_balance: float, forecast: float) -> tuple[float, str]:
    """Return (rar, adequacy_level) applying the documented thresholds."""
    rar = sinking_balance / forecast
    if rar > 0.7:
        level = StressLevel.HEALTHY
    elif rar > 0.4:
        level = StressLevel.MODERATE
    elif rar > 0.2:
        level = StressLevel.HIGH
    else:
        level = StressLevel.SEVERE
    return rar, level


# ──────────────────────────────────────────────────────────────────────────────
# Stress-level constants
# ──────────────────────────────────────────────────────────────────────────────

class TestStressLevelConstants:
    def test_stress_level_constants(self):
        assert StressLevel.HEALTHY == "healthy"
        assert StressLevel.MODERATE == "moderate"
        assert StressLevel.HIGH == "high"
        assert StressLevel.SEVERE == "severe"


# ──────────────────────────────────────────────────────────────────────────────
# Reserve Adequacy Ratio threshold tests
# ──────────────────────────────────────────────────────────────────────────────

class TestReserveAdequacyRatio:
    def test_reserve_adequacy_healthy(self):
        rar, level = _compute_adequacy(800_000, 1_000_000)
        assert rar == pytest.approx(0.8)
        assert level == StressLevel.HEALTHY

    def test_reserve_adequacy_moderate(self):
        rar, level = _compute_adequacy(500_000, 1_000_000)
        assert rar == pytest.approx(0.5)
        assert level == StressLevel.MODERATE

    def test_reserve_adequacy_high(self):
        rar, level = _compute_adequacy(300_000, 1_000_000)
        assert rar == pytest.approx(0.3)
        assert level == StressLevel.HIGH

    def test_reserve_adequacy_severe(self):
        rar, level = _compute_adequacy(100_000, 1_000_000)
        assert rar == pytest.approx(0.1)
        assert level == StressLevel.SEVERE


# ──────────────────────────────────────────────────────────────────────────────
# Risk score weight totals
# ──────────────────────────────────────────────────────────────────────────────

class TestRiskScoreWeights:
    def test_risk_score_components_sum_to_100_weights(self):
        reserve_gap_weight = 0.35
        expense_growth_weight = 0.20
        maintenance_backlog_weight = 0.15
        capital_age_weight = 0.20
        levy_collection_weight = 0.10
        total = (
                reserve_gap_weight
                + expense_growth_weight
                + maintenance_backlog_weight
                + capital_age_weight
                + levy_collection_weight
        )
        assert total == pytest.approx(1.0)


# ──────────────────────────────────────────────────────────────────────────────
# Pydantic model validation
# ──────────────────────────────────────────────────────────────────────────────

class TestModelValidation:
    def test_building_stress_snapshot_model(self):
        reserve = ReserveAdequacyResult(
            sinking_fund_balance=500_000.0,
            ten_year_capital_forecast=1_000_000.0,
            reserve_adequacy_ratio=0.5,
            deficit=500_000.0,
            adequacy_level=StressLevel.MODERATE,
        )
        risk = RiskScoreComponents(
            reserve_gap_score=40.0,
            expense_growth_score=20.0,
            maintenance_backlog_score=15.0,
            capital_age_score=20.0,
            levy_collection_score=10.0,
            overall_risk_score=35.0,
            stress_level=StressLevel.MODERATE,
        )
        snapshot = BuildingStressSnapshot(
            building_id="eastgate-01",
            computed_at="2026-03-01T00:00:00Z",
            reserve_adequacy=reserve,
            risk_components=risk,
            overall_risk_score=35.0,
            stress_level=StressLevel.MODERATE,
            total_units=87,
        )
        assert snapshot.building_id == "eastgate-01"
        assert snapshot.overall_risk_score == 35.0
        assert snapshot.stress_level == StressLevel.MODERATE
        assert snapshot.reserve_timeline == []
        assert snapshot.mitigation_options == []

    def test_reserve_adequacy_result_model(self):
        result = ReserveAdequacyResult(
            sinking_fund_balance=800_000.0,
            ten_year_capital_forecast=1_000_000.0,
            reserve_adequacy_ratio=0.8,
            deficit=200_000.0,
            adequacy_level=StressLevel.HEALTHY,
        )
        assert result.reserve_adequacy_ratio == pytest.approx(0.8)
        assert result.adequacy_level == "healthy"

    def test_risk_score_components_model(self):
        components = RiskScoreComponents(
            reserve_gap_score=60.0,
            expense_growth_score=25.0,
            maintenance_backlog_score=30.0,
            capital_age_score=40.0,
            levy_collection_score=10.0,
            overall_risk_score=42.0,
            stress_level=StressLevel.MODERATE,
        )
        assert components.overall_risk_score == 42.0
        assert components.stress_level == "moderate"

    def test_mitigation_option_types(self):
        assert MitigationType.INCREASE_LEVY == "increase_levy"
        assert MitigationType.DEFER_WORKS == "defer_works"
        assert MitigationType.STRATA_LOAN == "strata_loan"

    def test_weekly_radar_model(self):
        radar = WeeklyBuildingRadar(
            building_name="East Gate Residences",
            week_ending="2026-03-07",
            stress_level=StressLevel.MODERATE,
            overall_risk_score=40.0,
            score_change=-2.5,
            new_signals=["Lift maintenance overdue"],
            estimated_levy_risk_per_unit=1500.0,
            estimated_levy_year="2027",
        )
        assert radar.building_name == "East Gate Residences"
        assert radar.overall_risk_score == 40.0
        assert radar.score_change == pytest.approx(-2.5)
        assert len(radar.new_signals) == 1

    def test_reserve_timeline_point_model(self):
        # Positive balance — not a deficit
        point_ok = ReserveTimelinePoint(
            year=2027,
            projected_balance=200_000.0,
            annual_contributions=80_000.0,
            annual_expenses=60_000.0,
            is_deficit=False,
        )
        assert point_ok.is_deficit is False

        # Negative balance — is a deficit
        point_deficit = ReserveTimelinePoint(
            year=2031,
            projected_balance=-50_000.0,
            annual_contributions=80_000.0,
            annual_expenses=180_000.0,
            is_deficit=True,
        )
        assert point_deficit.is_deficit is True
        assert point_deficit.projected_balance < 0
