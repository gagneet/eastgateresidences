"""
Pydantic models for the Global Building Risk Index.

Risk score range: 0–100 (lower = less risk).
"""
from pydantic import BaseModel, Field
from typing import List, Optional


class RiskLevel:
    """Risk level classification constants."""
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class RiskComponents(BaseModel):
    """Individual weighted risk component scores (each 0–100)."""
    asset_health_score: float = Field(0.0, ge=0.0, le=100.0,
                                      description="Asset health component score (weight 0.25)")
    financial_stability_score: float = Field(0.0, ge=0.0, le=100.0,
                                             description="Financial stability component score (weight 0.20)")
    reserve_adequacy_score: float = Field(0.0, ge=0.0, le=100.0,
                                          description="Reserve adequacy component score (weight 0.20)")
    capital_shock_risk_score: float = Field(0.0, ge=0.0, le=100.0,
                                            description="Capital shock risk score (weight 0.20; higher = greater shock risk)")
    maintenance_risk_score: float = Field(0.0, ge=0.0, le=100.0,
                                          description="Maintenance forecast risk component score (weight 0.15)")


class BuildingRiskScore(BaseModel):
    """Full building risk score result persisted to / returned from the API."""
    building_id: str
    building_name: str
    building_address: str
    slug: str

    risk_score: float = Field(..., ge=0.0, le=100.0,
                              description="Composite risk score 0–100 (lower = healthier)")
    risk_level: str = Field(...,
                            description="low | moderate | high | critical")

    components: RiskComponents

    # Derived signals
    predicted_major_repairs: Optional[List[str]] = Field(default_factory=list,
                                                         description="List of major repairs forecast within 5 years")
    predicted_levy_risk: Optional[str] = None
    capital_shock_index: Optional[float] = None

    computed_at: str


class PublicBuildingReport(BaseModel):
    """Public-safe building report — NO PII, no financial transactions."""
    building_name: str
    building_address: str
    slug: str

    risk_score: float
    risk_level: str

    # Financial health summary (no individual transaction data)
    financial_health_summary: str
    reserve_adequacy_level: str
    reserve_adequacy_ratio: Optional[float] = None

    # Maintenance forecast (no contractor/owner data)
    maintenance_forecast_summary: str
    predicted_major_repairs: List[str] = Field(default_factory=list)

    # Capital replacement overview
    capital_replacement_summary: str
    capital_shock_index: Optional[float] = None

    predicted_levy_risk: Optional[str] = None

    computed_at: str
