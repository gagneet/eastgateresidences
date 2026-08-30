"""
Building Financial Stress models.

Supports the Building Financial Stress Early Warning System:
- Reserve Adequacy Ratio (RAR) calculation
- Special Levy Risk Score (0-100)
- Unit-level cost impact
- 10-year reserve timeline projection
- Mitigation option recommendations
- Weekly building radar for owners
"""

from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class StressLevel:
    """Risk classification thresholds."""
    HEALTHY = "healthy"  # Score < 30,  RAR > 0.7
    MODERATE = "moderate"  # Score 30-55, RAR 0.4-0.7
    HIGH = "high"  # Score 55-75, RAR 0.2-0.4
    SEVERE = "severe"  # Score > 75,  RAR < 0.2


class MitigationType:
    """Types of mitigation strategies."""
    INCREASE_LEVY = "increase_levy"
    DEFER_WORKS = "defer_works"
    STRATA_LOAN = "strata_loan"
    SPECIAL_LEVY = "special_levy"
    PARTIAL_WORKS = "partial_works"


# ── Reserve Adequacy ───────────────────────────────────────────────────────

class ReserveAdequacyResult(BaseModel):
    """Result of the Reserve Adequacy Ratio calculation."""
    sinking_fund_balance: float
    ten_year_capital_forecast: float
    reserve_adequacy_ratio: float  # RAR = balance / forecast
    deficit: float  # forecast - balance (negative = surplus)
    adequacy_level: str  # StressLevel constant


# ── Risk Score Components ──────────────────────────────────────────────────

class RiskScoreComponents(BaseModel):
    """Individual weighted components that feed into the overall risk score."""
    reserve_gap_score: float  # 35% — how underfunded is the sinking fund
    expense_growth_score: float  # 20% — trend in building expenses
    maintenance_backlog_score: float  # 15% — deferred repairs accumulation
    capital_age_score: float  # 20% — infrastructure age (lift, roof, pumps)
    levy_collection_score: float  # 10% — arrears / collection rate
    # Derived
    overall_risk_score: float  # 0–100 weighted composite
    stress_level: str  # StressLevel constant


# ── Unit-Level Impact ──────────────────────────────────────────────────────

class UnitLevyImpact(BaseModel):
    """Projected special levy cost for a specific unit."""
    unit_number: str
    entitlement_units: Optional[float] = None
    total_entitlement_units: Optional[float] = None
    proportional_share_pct: float  # Unit's % share of building costs
    projected_special_levy: float  # $ amount
    probability_pct: float  # Likelihood of this levy materialising
    estimated_year: Optional[str] = None  # When levy is likely


# ── Mitigation Options ─────────────────────────────────────────────────────

class MitigationOption(BaseModel):
    """A recommended action to reduce building financial stress."""
    option_type: str  # MitigationType constant
    title: str
    description: str
    annual_cost_per_unit: Optional[float] = None  # Extra $ per unit per year
    total_building_cost: Optional[float] = None
    reduces_risk_by: Optional[float] = None  # Points reduction in risk score
    avoids_special_levy: bool = False
    feasibility: str = "medium"  # low | medium | high


# ── Reserve Timeline ───────────────────────────────────────────────────────

class ReserveTimelinePoint(BaseModel):
    """Projected sinking fund balance for a single year."""
    year: int
    projected_balance: float
    annual_contributions: float
    annual_expenses: float
    is_deficit: bool


# ── Building Financial Health Snapshot ────────────────────────────────────

class BuildingStressSnapshot(BaseModel):
    """
    Stored result of the monthly building financial stress calculation.
    Persisted to the building_financial_health MongoDB collection.
    """
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    building_id: str
    computed_at: str
    # Core metrics
    reserve_adequacy: ReserveAdequacyResult
    risk_components: RiskScoreComponents
    # Aggregated
    overall_risk_score: float  # 0–100
    stress_level: str  # StressLevel constant
    # Projections
    projected_deficit_year: Optional[str] = None
    projected_special_levy_per_unit: Optional[float] = None
    special_levy_probability_pct: float = 0.0
    reserve_timeline: List[ReserveTimelinePoint] = []
    mitigation_options: List[MitigationOption] = []
    # Signals that changed since last snapshot
    new_risk_flags: List[str] = []
    # Unit count used for per-unit calculations
    total_units: int = 0


# ── Response Models ────────────────────────────────────────────────────────

class BuildingStressResponse(BaseModel):
    """API response for the building stress dashboard."""
    model_config = ConfigDict(extra="ignore")
    building_id: str
    computed_at: str
    overall_risk_score: float
    stress_level: str
    reserve_adequacy_ratio: float
    sinking_fund_balance: float
    ten_year_capital_forecast: float
    deficit: float
    projected_deficit_year: Optional[str] = None
    projected_special_levy_per_unit: Optional[float] = None
    special_levy_probability_pct: float
    risk_components: RiskScoreComponents
    reserve_timeline: List[ReserveTimelinePoint] = []
    mitigation_options: List[MitigationOption] = []
    new_risk_flags: List[str] = []
    total_units: int


class WeeklyBuildingRadar(BaseModel):
    """Weekly digest of building financial health for owner emails."""
    building_name: str
    week_ending: str
    stress_level: str
    overall_risk_score: float
    score_change: float  # Change from previous week
    new_signals: List[str] = []
    estimated_levy_risk_per_unit: Optional[float] = None
    estimated_levy_year: Optional[str] = None


__all__ = [
    "StressLevel", "MitigationType",
    "ReserveAdequacyResult", "RiskScoreComponents",
    "UnitLevyImpact", "MitigationOption",
    "ReserveTimelinePoint", "BuildingStressSnapshot",
    "BuildingStressResponse", "WeeklyBuildingRadar",
]
