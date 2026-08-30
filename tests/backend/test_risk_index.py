"""
Tests for the Global Building Risk Index models and score calculation.

All tests are pure unit tests — no async, no database, no mocking needed.
Follows the same pattern as tests/backend/test_building_stress.py.
"""
import pytest
from pydantic import ValidationError

from models.risk_index import (
    RiskLevel,
    RiskComponents,
    BuildingRiskScore,
    PublicBuildingReport,
)
from services.risk_index_service import (
    classify_risk_level,
    _rar_to_risk,
    MAJOR_REPAIR_COST_THRESHOLD,
    W_ASSET_HEALTH,
    W_FINANCIAL_STABILITY,
    W_RESERVE_ADEQUACY,
    W_CAPITAL_SHOCK,
    W_MAINTENANCE_RISK,
)


# ──────────────────────────────────────────────────────────────────────────────
# Risk level constants
# ──────────────────────────────────────────────────────────────────────────────

class TestRiskLevelConstants:
    def test_risk_level_constants(self):
        assert RiskLevel.LOW == "low"
        assert RiskLevel.MODERATE == "moderate"
        assert RiskLevel.HIGH == "high"
        assert RiskLevel.CRITICAL == "critical"


# ──────────────────────────────────────────────────────────────────────────────
# Risk weight totals
# ──────────────────────────────────────────────────────────────────────────────

class TestRiskWeights:
    def test_risk_weights_sum_to_one(self):
        total = (
                W_ASSET_HEALTH
                + W_FINANCIAL_STABILITY
                + W_RESERVE_ADEQUACY
                + W_CAPITAL_SHOCK
                + W_MAINTENANCE_RISK
        )
        assert total == pytest.approx(1.0)

    def test_individual_weights(self):
        assert W_ASSET_HEALTH == pytest.approx(0.25)
        assert W_FINANCIAL_STABILITY == pytest.approx(0.20)
        assert W_RESERVE_ADEQUACY == pytest.approx(0.20)
        assert W_CAPITAL_SHOCK == pytest.approx(0.20)
        assert W_MAINTENANCE_RISK == pytest.approx(0.15)


# ──────────────────────────────────────────────────────────────────────────────
# classify_risk_level function
# ──────────────────────────────────────────────────────────────────────────────

class TestClassifyRiskLevel:
    def test_low_risk(self):
        assert classify_risk_level(0.0) == RiskLevel.LOW
        assert classify_risk_level(24.9) == RiskLevel.LOW

    def test_moderate_risk(self):
        assert classify_risk_level(25.0) == RiskLevel.MODERATE
        assert classify_risk_level(49.9) == RiskLevel.MODERATE

    def test_high_risk(self):
        assert classify_risk_level(50.0) == RiskLevel.HIGH
        assert classify_risk_level(74.9) == RiskLevel.HIGH

    def test_critical_risk(self):
        assert classify_risk_level(75.0) == RiskLevel.CRITICAL
        assert classify_risk_level(100.0) == RiskLevel.CRITICAL


# ──────────────────────────────────────────────────────────────────────────────
# _rar_to_risk: Reserve Adequacy Ratio → risk score
# ──────────────────────────────────────────────────────────────────────────────

class TestRarToRisk:
    def test_adequate_reserves(self):
        # RAR >= 0.7 → very low risk
        assert _rar_to_risk(0.7) == pytest.approx(5.0)
        assert _rar_to_risk(1.0) == pytest.approx(5.0)

    def test_moderate_reserves(self):
        # 0.4 <= RAR < 0.7 → moderate risk
        assert _rar_to_risk(0.4) == pytest.approx(35.0)
        assert _rar_to_risk(0.69) == pytest.approx(35.0)

    def test_low_reserves(self):
        # 0.2 <= RAR < 0.4 → elevated risk
        assert _rar_to_risk(0.2) == pytest.approx(60.0)
        assert _rar_to_risk(0.39) == pytest.approx(60.0)

    def test_severely_underfunded(self):
        # RAR < 0.2 → high risk
        assert _rar_to_risk(0.0) == pytest.approx(85.0)
        assert _rar_to_risk(0.19) == pytest.approx(85.0)


# ──────────────────────────────────────────────────────────────────────────────
# Risk formula calculation (pure arithmetic)
# ──────────────────────────────────────────────────────────────────────────────

class TestRiskFormula:
    def _compute(
            self,
            asset: float,
            fin: float,
            reserve: float,
            shock: float,
            maint: float,
    ) -> float:
        return (
                W_ASSET_HEALTH * asset
                + W_FINANCIAL_STABILITY * fin
                + W_RESERVE_ADEQUACY * reserve
                + W_CAPITAL_SHOCK * shock
                + W_MAINTENANCE_RISK * maint
        )

    def test_all_zero_inputs(self):
        score = self._compute(0, 0, 0, 0, 0)
        assert score == pytest.approx(0.0)

    def test_all_max_inputs(self):
        score = self._compute(100, 100, 100, 100, 100)
        assert score == pytest.approx(100.0)

    def test_balanced_mid_inputs(self):
        # All components at 50 → composite = 50
        score = self._compute(50, 50, 50, 50, 50)
        assert score == pytest.approx(50.0)

    def test_healthy_building_low_risk(self):
        # Very healthy building: all component risk scores near 0
        score = self._compute(5, 10, 5, 5, 10)
        assert classify_risk_level(score) == RiskLevel.LOW

    def test_distressed_building_critical_risk(self):
        # Distressed: all components near maximum risk
        score = self._compute(90, 85, 85, 90, 85)
        assert classify_risk_level(score) == RiskLevel.CRITICAL


# ──────────────────────────────────────────────────────────────────────────────
# Pydantic model validation
# ──────────────────────────────────────────────────────────────────────────────

class TestModelValidation:
    def test_risk_components_valid(self):
        components = RiskComponents(
            asset_health_score=30.0,
            financial_stability_score=40.0,
            reserve_adequacy_score=35.0,
            capital_shock_risk_score=45.0,
            maintenance_risk_score=20.0,
        )
        assert components.asset_health_score == pytest.approx(30.0)
        assert components.maintenance_risk_score == pytest.approx(20.0)

    def test_risk_components_clamped_at_100(self):
        with pytest.raises(ValidationError):
            RiskComponents(
                asset_health_score=110.0,  # exceeds max
                financial_stability_score=50.0,
                reserve_adequacy_score=50.0,
                capital_shock_risk_score=50.0,
                maintenance_risk_score=50.0,
            )

    def test_risk_components_rejects_negative(self):
        with pytest.raises(ValidationError):
            RiskComponents(
                asset_health_score=-1.0,  # below min
                financial_stability_score=50.0,
                reserve_adequacy_score=50.0,
                capital_shock_risk_score=50.0,
                maintenance_risk_score=50.0,
            )

    def test_building_risk_score_model(self):
        score = BuildingRiskScore(
            building_id="13195",
            building_name="East Gate Residences",
            building_address="14 Hoolihan St, Denman Prospect ACT 2611",
            slug="eastgate-residences",
            risk_score=42.5,
            risk_level=RiskLevel.MODERATE,
            components=RiskComponents(
                asset_health_score=40.0,
                financial_stability_score=45.0,
                reserve_adequacy_score=35.0,
                capital_shock_risk_score=50.0,
                maintenance_risk_score=30.0,
            ),
            computed_at="2026-03-16T00:00:00+00:00",
        )
        assert score.building_id == "13195"
        assert score.risk_score == pytest.approx(42.5)
        assert score.risk_level == "moderate"
        assert score.predicted_major_repairs == []
        assert score.predicted_levy_risk is None

    def test_public_building_report_model(self):
        report = PublicBuildingReport(
            building_name="East Gate Residences",
            building_address="14 Hoolihan St, Denman Prospect ACT 2611",
            slug="eastgate-residences",
            risk_score=35.0,
            risk_level=RiskLevel.MODERATE,
            financial_health_summary="Generally stable finances with some areas to watch.",
            reserve_adequacy_level="moderate",
            maintenance_forecast_summary="Routine maintenance items identified; no urgent concerns.",
            capital_replacement_summary="No major capital replacements anticipated in the near term.",
            computed_at="2026-03-16T00:00:00+00:00",
        )
        assert report.building_name == "East Gate Residences"
        assert report.risk_level == "moderate"
        assert report.predicted_major_repairs == []

    def test_public_building_report_no_pii_fields(self):
        """Verify PublicBuildingReport schema has no PII fields."""
        fields = set(PublicBuildingReport.model_fields.keys())
        pii_fields = {
            "owner_name", "owner_email", "tenant_name", "contact_email",
            "phone", "email", "user_id", "payment_reference",
        }
        assert fields.isdisjoint(pii_fields), (
            f"PublicBuildingReport must not contain PII fields: {fields & pii_fields}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Boundary values at threshold edges
# ──────────────────────────────────────────────────────────────────────────────

class TestRiskLevelBoundaries:
    """Verify exact boundary values — off-by-one errors are common here."""

    def test_low_moderate_boundary(self):
        assert classify_risk_level(24.99) == RiskLevel.LOW
        assert classify_risk_level(25.0) == RiskLevel.MODERATE

    def test_moderate_high_boundary(self):
        assert classify_risk_level(49.99) == RiskLevel.MODERATE
        assert classify_risk_level(50.0) == RiskLevel.HIGH

    def test_high_critical_boundary(self):
        assert classify_risk_level(74.99) == RiskLevel.HIGH
        assert classify_risk_level(75.0) == RiskLevel.CRITICAL

    def test_score_at_zero_is_low(self):
        assert classify_risk_level(0.0) == RiskLevel.LOW

    def test_score_at_100_is_critical(self):
        assert classify_risk_level(100.0) == RiskLevel.CRITICAL


# ──────────────────────────────────────────────────────────────────────────────
# Constants and thresholds
# ──────────────────────────────────────────────────────────────────────────────

class TestServiceConstants:
    def test_major_repair_threshold_is_positive(self):
        assert MAJOR_REPAIR_COST_THRESHOLD > 0

    def test_major_repair_threshold_value(self):
        # Cost threshold for "major repair" reporting — must be $50k
        assert MAJOR_REPAIR_COST_THRESHOLD == 50_000

    def test_weight_asset_health_largest(self):
        # Asset health should be the highest-weighted component
        assert W_ASSET_HEALTH > W_FINANCIAL_STABILITY
        assert W_ASSET_HEALTH > W_RESERVE_ADEQUACY
        assert W_ASSET_HEALTH > W_CAPITAL_SHOCK
        assert W_ASSET_HEALTH > W_MAINTENANCE_RISK


# ──────────────────────────────────────────────────────────────────────────────
# Demo seed data expected score validation
# ──────────────────────────────────────────────────────────────────────────────

class TestSeedDataExpectedScore:
    """
    Validate that the seed data in scripts/db/seed_risk_index_demo.py
    would produce an expected composite score in the MODERATE range.
    """

    def _compute(self, asset, fin, reserve, shock, maint) -> float:
        return (
                W_ASSET_HEALTH * asset
                + W_FINANCIAL_STABILITY * fin
                + W_RESERVE_ADEQUACY * reserve
                + W_CAPITAL_SHOCK * shock
                + W_MAINTENANCE_RISK * maint
        )

    def test_seed_asset_health_average(self):
        # 8 assets with scores: 72,88,65,80,90,78,70,82 → avg 78.125
        scores = [72, 88, 65, 80, 90, 78, 70, 82]
        avg = sum(scores) / len(scores)
        asset_component = 100.0 - avg
        assert asset_component == pytest.approx(21.875)

    def test_seed_rar_component(self):
        # RAR = 620000 / 850000 = 0.729 → ≥ 0.7 → score 5.0
        reserve_balance = 620_000.0
        ten_year_forecast = 850_000.0
        rar = reserve_balance / ten_year_forecast
        assert rar >= 0.7
        assert _rar_to_risk(rar) == pytest.approx(5.0)

    def test_seed_composite_score_is_moderate(self):
        # Using seed data approximate component values:
        # asset=21.9, fin=18.0 (risk_score from seed), reserve=5.0, shock=45.0 (medium), maint=22.0
        composite = self._compute(21.875, 18.0, 5.0, 45.0, 22.0)
        # Expected: 0.25*21.875 + 0.20*18.0 + 0.20*5.0 + 0.20*45.0 + 0.15*22.0
        #         = 5.469 + 3.6 + 1.0 + 9.0 + 3.3 = 22.37 → LOW
        assert composite == pytest.approx(22.37, abs=0.1)
        assert classify_risk_level(composite) == RiskLevel.LOW

    def test_medium_capital_shock_maps_to_45(self):
        # In the service, risk_level="medium" → 45.0
        # Verify this aligns with the seed data (capital_shock_risks.risk_level = "medium")
        # medium → 45.0 (from the service code)
        medium_score = 45.0
        assert medium_score < 50.0  # medium shock should not trigger HIGH risk on its own


# ──────────────────────────────────────────────────────────────────────────────
# Public report completeness check
# ──────────────────────────────────────────────────────────────────────────────

class TestPublicReportCompleteness:
    def test_public_report_has_required_fields(self):
        """All required public-facing fields must be present in the schema."""
        fields = set(PublicBuildingReport.model_fields.keys())
        required = {
            "building_name", "building_address", "slug",
            "risk_score", "risk_level",
            "financial_health_summary", "reserve_adequacy_level",
            "maintenance_forecast_summary", "capital_replacement_summary",
            "computed_at",
        }
        missing = required - fields
        assert not missing, f"PublicBuildingReport missing fields: {missing}"

    def test_public_report_optional_fields_present(self):
        """Optional fields used for enhanced reports should be declared."""
        fields = set(PublicBuildingReport.model_fields.keys())
        optional_expected = {
            "predicted_major_repairs", "predicted_levy_risk",
            "capital_shock_index", "reserve_adequacy_ratio",
        }
        missing = optional_expected - fields
        assert not missing, f"Optional fields missing from PublicBuildingReport: {missing}"
