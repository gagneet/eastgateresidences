"""
Financial Intelligence Models — Pydantic v2

Collections:
  - financial_forecasts: multi-year expense projections per category
  - financial_anomalies: detected budget/payment anomalies
  - lot_financial_summary: true cost of ownership per lot
  - financial_documents: ingested PDFs and processing status
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated, List, Optional, Dict, Any


# ─────────────────────────────────────────────────────────────────────────────
# Financial Forecasts
# ─────────────────────────────────────────────────────────────────────────────

class FinancialForecast(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    financial_year: str
    fund_type: str  # "administrative" | "sinking"
    category: str
    projection_year_1: float
    projection_year_2: float
    projection_year_3: float
    method: str  # "linear" | "inflation" | "capital_works"
    confidence_score: float  # 0.0 - 1.0, R² for linear
    assumptions: Dict[str, Any]
    created_at: str


class ForecastResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    financial_year: str
    fund_type: str
    category: str
    projection_year_1: float
    projection_year_2: float
    projection_year_3: float
    method: str
    confidence_score: float
    assumptions: Dict[str, Any]
    created_at: str


# ─────────────────────────────────────────────────────────────────────────────
# Financial Anomalies
# ─────────────────────────────────────────────────────────────────────────────

class FinancialAnomaly(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    financial_year: str
    fund_type: Optional[str] = None
    category: Optional[str] = None
    lot_number: Optional[str] = None
    anomaly_type: str  # "budget_overrun" | "unbudgeted" | "spike" | "arrears" | "interest_spike" | "deficit" | "cashflow_risk"
    severity: str  # "low" | "medium" | "high" | "critical"
    deviation_pct: Optional[float] = None
    expected_value: Optional[float] = None
    actual_value: Optional[float] = None
    description: str
    detected_at: str
    resolved: bool = False


class AnomalyResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    financial_year: str
    fund_type: Optional[str] = None
    category: Optional[str] = None
    lot_number: Optional[str] = None
    anomaly_type: str
    severity: str
    deviation_pct: Optional[float] = None
    expected_value: Optional[float] = None
    actual_value: Optional[float] = None
    description: str
    detected_at: str
    resolved: bool


# ─────────────────────────────────────────────────────────────────────────────
# Lot Financial Summary (True Cost of Ownership)
# ─────────────────────────────────────────────────────────────────────────────

class LotFinancialSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")
    lot_number: str
    financial_year: str
    total_admin_paid: float = 0
    total_sinking_paid: float = 0
    special_levies: float = 0
    total_council: float = 0
    total_water: float = 0
    total_land_tax: float = 0
    total_interest: float = 0
    total_cost: float = 0
    cost_per_uoe: float = 0
    arrears_flag: bool = False
    risk_flag: bool = False
    created_at: str


class LotSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    lot_number: str
    financial_year: str
    total_admin_paid: float
    total_sinking_paid: float
    special_levies: float
    total_council: float
    total_water: float
    total_land_tax: float
    total_interest: float
    total_cost: float
    cost_per_uoe: float
    arrears_flag: bool
    risk_flag: bool
    created_at: str


# ─────────────────────────────────────────────────────────────────────────────
# Financial Documents (PDF Ingestion)
# ─────────────────────────────────────────────────────────────────────────────

class FinancialDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    file_name: str
    financial_year: str
    document_type: str  # "budget" | "actual" | "audit" | "statement"
    processing_status: str  # "pending" | "processing" | "complete" | "error"
    categories_mapped: int = 0
    categories_new: int = 0
    errors: List[str] = []
    file_hash: str
    uploaded_by: str
    uploaded_at: str


class FinancialDocumentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    file_name: str
    financial_year: str
    document_type: str
    processing_status: str
    categories_mapped: int
    categories_new: int
    errors: List[str]
    file_hash: str
    uploaded_by: str
    uploaded_at: str


# ─────────────────────────────────────────────────────────────────────────────
# Levy Simulation
# ─────────────────────────────────────────────────────────────────────────────

class LevySimulationRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    year: str
    increase_pct: Annotated[float, Field(ge=-5.0, le=50.0, description="Levy adjustment % (-5 to +50)")]


class UnitSimulationResult(BaseModel):
    unit_number: str
    current_annual: float
    new_annual: float
    impact: float
    entitlement: int


class LevySimulationResponse(BaseModel):
    year: str
    increase_pct: float
    current_total_collection: float
    new_total_collection: float
    surplus_deficit: float
    per_unit_samples: List[UnitSimulationResult]
    all_units: List[UnitSimulationResult]


# ─────────────────────────────────────────────────────────────────────────────
# Financial Health Score
# ─────────────────────────────────────────────────────────────────────────────

class HealthScoreBreakdown(BaseModel):
    surplus_ratio: float = 0  # 0-20 pts
    arrears_pct: float = 0  # 0-20 pts
    cashflow_buffer: float = 0  # 0-15 pts
    forecast_stability: float = 0  # 0-15 pts
    budget_discipline: float = 0  # 0-15 pts
    expense_volatility: float = 0  # 0-15 pts


class HealthScoreResponse(BaseModel):
    year: str
    score: float  # 0-100
    breakdown: HealthScoreBreakdown
    risk_level: str  # "excellent" | "good" | "moderate" | "at_risk" | "critical"
    details: Dict[str, Any] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Cashflow Projection
# ─────────────────────────────────────────────────────────────────────────────

class CashflowMonth(BaseModel):
    month: str  # "Jan 2026"
    month_number: int  # 1-12
    expected_income: float
    expected_expenses: float
    net_cashflow: float
    cumulative_balance: float
    is_risk_month: bool


class CashflowProjectionResponse(BaseModel):
    year: str
    months: List[CashflowMonth]
    annual_income: float
    annual_expenses: float
    min_balance: float
    risk_months: List[str]
    opening_balance: float


# ─────────────────────────────────────────────────────────────────────────────
# Special Levy Prediction & Levy Stability
# ─────────────────────────────────────────────────────────────────────────────

class SpecialLevyForecast(BaseModel):
    model_config = ConfigDict(extra="ignore")
    forecast_id: str
    building_id: Optional[str] = None
    year: int
    probability: float
    median_amount: float
    p90_amount: float
    p95_amount: float
    worst_case: float
    simulation_runs: int
    generated_at: str
    horizon_years: int
    shock_year_distribution: Dict[int, float] = {}
    reserve_fan: List[Dict[str, Any]] = []
    per_unit_distribution: List[float] = []
    thresholds: Dict[str, float] = {}
    group_summary: List[Dict[str, Any]] = []
    explanation: Optional[str] = None


class LevyStabilitySnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    building_id: Optional[str] = None
    year: int
    reserve_score: float
    shock_score: float
    volatility_score: float
    funding_score: float
    levy_stability_score: float
    generated_at: str
    horizon_years: int
    components: Dict[str, float] = {}
    group_scores: List[Dict[str, Any]] = []
    explanation: Optional[str] = None
