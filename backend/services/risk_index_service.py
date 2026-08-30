"""
Global Building Risk Index Service.

Computes a 0–100 composite risk score for a building using:
  - asset_health_scores collection
  - financial_forecasts collection
  - sinking_fund_plan / annual_levies for reserve adequacy
  - capital_shock_risks collection
  - maintenance_forecasts collection

Risk formula:
  risk_score =
    0.25 * asset_health_component
  + 0.20 * financial_stability_component
  + 0.20 * reserve_adequacy_component
  + 0.20 * capital_shock_risk_component   (high shock score = high risk)
  + 0.15 * maintenance_risk_component

All inputs are normalised to 0–100 before combining.
A higher score means MORE risk.
"""
from datetime import datetime, timezone

import asyncio
from typing import Optional

from database import db
from models.risk_index import RiskComponents, BuildingRiskScore, PublicBuildingReport, RiskLevel
from utils.finance_helpers import get_latest_levy_year

# ── Weight constants ───────────────────────────────────────────────────────────
W_ASSET_HEALTH = 0.25
W_FINANCIAL_STABILITY = 0.20
W_RESERVE_ADEQUACY = 0.20
W_CAPITAL_SHOCK = 0.20
W_MAINTENANCE_RISK = 0.15

_TOTAL_WEIGHT = (
        W_ASSET_HEALTH
        + W_FINANCIAL_STABILITY
        + W_RESERVE_ADEQUACY
        + W_CAPITAL_SHOCK
        + W_MAINTENANCE_RISK
)
assert abs(_TOTAL_WEIGHT - 1.0) < 1e-9, "Risk weights must sum to 1.0"

# Fallback defaults when data is unavailable
_DEFAULT_ASSET_HEALTH_SCORE = 50.0
_DEFAULT_FINANCIAL_STABILITY = 50.0
_DEFAULT_RESERVE_ADEQUACY = 50.0
_DEFAULT_CAPITAL_SHOCK = 50.0
_DEFAULT_MAINTENANCE_RISK = 50.0

# Minimum estimated cost (AUD) for a capital event to be reported as a major repair
MAJOR_REPAIR_COST_THRESHOLD = 50_000


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/services/risk_index_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def classify_risk_level(score: float) -> str:
    """Classify a 0–100 composite risk score into a risk level string."""
    if score < 25:
        return RiskLevel.LOW
    if score < 50:
        return RiskLevel.MODERATE
    if score < 75:
        return RiskLevel.HIGH
    return RiskLevel.CRITICAL


# ── Component extractors ───────────────────────────────────────────────────────

async def _get_asset_health_component(building_id: str) -> float:
    """
    Returns an asset risk score 0–100 based on the latest asset health scores.
    Lower average health percentage → higher risk score.
    """
    docs = await db.asset_health_scores.find(
        {"building_id": building_id},
        {"health_score": 1, "_id": 0},
    ).to_list(length=500)

    if not docs:
        return _DEFAULT_ASSET_HEALTH_SCORE

    scores = [
        float(d["health_score"])
        for d in docs
        if d.get("health_score") is not None
    ]
    if not scores:
        return _DEFAULT_ASSET_HEALTH_SCORE

    avg_health = sum(scores) / len(scores)
    # health_score 0–100 where 100 = perfect; invert to get risk component
    return max(0.0, min(100.0, 100.0 - avg_health))


async def _get_financial_stability_component(building_id: str) -> float:
    """
    Returns a financial instability score 0–100 from financial_forecasts.
    Uses the forecast's risk level if present, or derives from variance ratio.
    """
    forecast = await db.financial_forecasts.find_one(
        {"building_id": building_id},
        sort=[("created_at", -1)],
    )
    if not forecast:
        return _DEFAULT_FINANCIAL_STABILITY

    # Many forecast documents store a `risk_score` or `risk_level` field
    if "risk_score" in forecast and forecast["risk_score"] is not None:
        return max(0.0, min(100.0, float(forecast["risk_score"])))

    risk_level = (forecast.get("risk_level") or "").lower()
    mapping = {"low": 15.0, "moderate": 40.0, "high": 65.0, "critical": 85.0}
    return mapping.get(risk_level, _DEFAULT_FINANCIAL_STABILITY)


async def _get_reserve_adequacy_component(building_id: str) -> float:
    """
    Returns a reserve inadequacy score 0–100.
    RAR ≥ 0.7  → score ≈  5  (very low risk)
    RAR ≥ 0.4  → score ≈ 35
    RAR ≥ 0.2  → score ≈ 60
    RAR <  0.2 → score ≈ 85
    Falls back to annual_levies if sinking_fund_plan not found.
    """
    plan = await db.sinking_fund_plan.find_one(
        {"building_id": building_id},
        sort=[("year", -1)],
    )
    if plan and plan.get("reserve_balance") and plan.get("ten_year_forecast"):
        balance = float(plan["reserve_balance"])
        forecast = float(plan["ten_year_forecast"])
        if forecast > 0:
            rar = balance / forecast
            return _rar_to_risk(rar)

    # Fallback: use annual_levies
    year = await get_latest_levy_year(building_id)
    if not year:
        return _DEFAULT_RESERVE_ADEQUACY

    levy = await db.annual_levies.find_one(
        {"year": year, "building_id": building_id}, {"_id": 0}
    ) or {}

    balance = levy.get("sinking_fund", {}).get("closing_balance") or 0.0
    capital_doc = await db.capital_shock_risks.find_one(
        {"building_id": building_id}, sort=[("created_at", -1)]
    )
    ten_year = float(capital_doc.get("ten_year_capital_forecast", 0)) if capital_doc else 0.0

    if ten_year > 0:
        rar = float(balance) / ten_year
        return _rar_to_risk(rar)

    return _DEFAULT_RESERVE_ADEQUACY


def _rar_to_risk(rar: float) -> float:
    """Convert Reserve Adequacy Ratio to a 0–100 risk score (higher = worse)."""
    if rar >= 0.7:
        return 5.0
    if rar >= 0.4:
        return 35.0
    if rar >= 0.2:
        return 60.0
    return 85.0


async def _get_capital_shock_component(building_id: str) -> float:
    """
    Returns a capital shock risk score 0–100 from capital_shock_risks.
    High shock → high risk score.
    """
    doc = await db.capital_shock_risks.find_one(
        {"building_id": building_id},
        sort=[("created_at", -1)],
    )
    if not doc:
        return _DEFAULT_CAPITAL_SHOCK

    risk_level = (doc.get("risk_level") or "").lower()
    if risk_level == "critical":
        return 90.0
    if risk_level == "high":
        return 70.0
    if risk_level == "medium":
        return 45.0
    if risk_level == "low":
        return 20.0

    # Fallback: use capital_shock_index if present
    csi = doc.get("capital_shock_index")
    if csi is not None:
        # CSI scale typically 0–10; map to 0–100
        return max(0.0, min(100.0, float(csi) * 10.0))

    return _DEFAULT_CAPITAL_SHOCK


async def _get_maintenance_risk_component(building_id: str) -> float:
    """
    Returns a maintenance risk score 0–100 from maintenance_forecasts.
    """
    doc = await db.maintenance_forecasts.find_one(
        {"building_id": building_id},
        sort=[("created_at", -1)],
    )
    if not doc:
        return _DEFAULT_MAINTENANCE_RISK

    if "risk_score" in doc and doc["risk_score"] is not None:
        return max(0.0, min(100.0, float(doc["risk_score"])))

    risk_level = (doc.get("risk_level") or "").lower()
    mapping = {"low": 15.0, "moderate": 40.0, "high": 65.0, "critical": 85.0}
    return mapping.get(risk_level, _DEFAULT_MAINTENANCE_RISK)


async def _get_predicted_repairs(building_id: str) -> list[str]:
    """Return a list of major repair descriptions forecast within 5 years."""
    year_now = datetime.now(timezone.utc).year
    horizon = year_now + 5

    docs = await db.capital_replacement_schedule.find(
        {
            "building_id": building_id,
            "replacement_year": {"$lte": horizon},
            "estimated_cost": {"$gte": MAJOR_REPAIR_COST_THRESHOLD},
        },
        {"asset_name": 1, "replacement_year": 1, "estimated_cost": 1, "_id": 0},
    ).sort("replacement_year", 1).limit(10).to_list(length=10)

    repairs = []
    for d in docs:
        name = d.get("asset_name", "Unknown asset")
        year = d.get("replacement_year", "?")
        cost = d.get("estimated_cost", 0)
        repairs.append(f"{name} (~{year}, est. ${cost:,.0f})")
    return repairs


async def _get_levy_risk_label(building_id: str, risk_score: float) -> str:
    """Return a human-readable levy risk label based on risk score."""
    if risk_score >= 75:
        return "High risk of special levy"
    if risk_score >= 50:
        return "Moderate levy increase likely"
    if risk_score >= 25:
        return "Minor levy adjustment possible"
    return "Levy stable"


# ── Main computation ───────────────────────────────────────────────────────────

async def compute_building_risk_score(building_id: str) -> BuildingRiskScore:
    """
    Compute and return the Global Building Risk Index for a single building.

    All DB queries are scoped by building_id (multi-tenancy guarantee).
    """
    building = await db.buildings.find_one(
        {"building_id": building_id},
        {"name": 1, "address": 1, "slug": 1, "_id": 0},
    )
    if not building:
        # Fallback to buildings collection indexed differently
        building = await db.buildings.find_one(
            {"id": building_id},
            {"name": 1, "address": 1, "slug": 1, "_id": 0},
        ) or {}

    building_name = building.get("name", "Unknown Building")
    building_address = building.get("address", "")
    slug = building.get("slug", building_id)

    # Fetch all components concurrently for performance
    (
        asset_score,
        fin_score,
        reserve_score,
        shock_score,
        maint_score,
    ) = await asyncio.gather(
        _get_asset_health_component(building_id),
        _get_financial_stability_component(building_id),
        _get_reserve_adequacy_component(building_id),
        _get_capital_shock_component(building_id),
        _get_maintenance_risk_component(building_id),
    )

    # capital_shock_risk_inverse: high shock index = high risk score
    composite = (
            W_ASSET_HEALTH * asset_score
            + W_FINANCIAL_STABILITY * fin_score
            + W_RESERVE_ADEQUACY * reserve_score
            + W_CAPITAL_SHOCK * shock_score
            + W_MAINTENANCE_RISK * maint_score
    )
    composite = round(max(0.0, min(100.0, composite)), 2)
    risk_level = classify_risk_level(composite)

    predicted_repairs, levy_risk, capital_shock_doc = await asyncio.gather(
        _get_predicted_repairs(building_id),
        _get_levy_risk_label(building_id, composite),
        db.capital_shock_risks.find_one(
            {"building_id": building_id},
            sort=[("created_at", -1)],
        ),
    )

    capital_shock_index = None
    if capital_shock_doc:
        capital_shock_index = capital_shock_doc.get("capital_shock_index")

    return BuildingRiskScore(
        building_id=building_id,
        building_name=building_name,
        building_address=building_address,
        slug=slug,
        risk_score=composite,
        risk_level=risk_level,
        components=RiskComponents(
            asset_health_score=round(asset_score, 2),
            financial_stability_score=round(fin_score, 2),
            reserve_adequacy_score=round(reserve_score, 2),
            capital_shock_risk_score=round(shock_score, 2),
            maintenance_risk_score=round(maint_score, 2),
        ),
        predicted_major_repairs=predicted_repairs,
        predicted_levy_risk=levy_risk,
        capital_shock_index=float(capital_shock_index) if capital_shock_index is not None else None,
        computed_at=_now(),
    )


async def get_public_building_report(slug: str) -> Optional[PublicBuildingReport]:
    """
    Build a PUBLIC-SAFE building report from the risk score.

    - No owner/tenant names
    - No financial transaction details
    - No contact information
    Returns None if the building slug is not found.
    """
    building = await db.buildings.find_one(
        {"slug": slug},
        {"building_id": 1, "name": 1, "address": 1, "slug": 1, "_id": 0},
    )
    if not building:
        # Try by `id` field
        building = await db.buildings.find_one(
            {"id": slug},
            {"building_id": 1, "name": 1, "address": 1, "slug": 1, "_id": 0},
        )
    if not building:
        return None

    building_id = building.get("building_id") or building.get("id") or slug
    risk = await compute_building_risk_score(building_id)

    # Reserve adequacy level (public-friendly label, no dollar amounts)
    reserve_score = risk.components.reserve_adequacy_score
    if reserve_score <= 35:
        reserve_level = "adequate"
        reserve_ratio = None
    elif reserve_score <= 60:
        reserve_level = "moderate"
        reserve_ratio = None
    else:
        reserve_level = "underfunded"
        reserve_ratio = None

    # Human-readable summaries
    def _fin_summary(score: float) -> str:
        """Generated function header.

        Function: _fin_summary
        Path: backend/services/risk_index_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if score < 25:
            return "Financially stable with strong reserves."
        if score < 50:
            return "Generally stable finances with some areas to watch."
        if score < 75:
            return "Financial health requires attention."
        return "Financial health is under significant pressure."

    def _maint_summary(score: float) -> str:
        """Generated function header.

        Function: _maint_summary
        Path: backend/services/risk_index_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if score < 25:
            return "Maintenance is up to date with no major items outstanding."
        if score < 50:
            return "Routine maintenance items identified; no urgent concerns."
        if score < 75:
            return "Several maintenance items require attention soon."
        return "Significant maintenance backlog with urgent items identified."

    def _capital_summary(score: float, repairs: list) -> str:
        """Generated function header.

        Function: _capital_summary
        Path: backend/services/risk_index_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if not repairs and score < 50:
            return "No major capital replacements anticipated in the near term."
        if repairs:
            return f"{len(repairs)} major capital event(s) forecast within 5 years."
        return "Capital replacement planning is in progress."

    return PublicBuildingReport(
        building_name=risk.building_name,
        building_address=risk.building_address,
        slug=risk.slug,
        risk_score=risk.risk_score,
        risk_level=risk.risk_level,
        financial_health_summary=_fin_summary(risk.components.financial_stability_score),
        reserve_adequacy_level=reserve_level,
        reserve_adequacy_ratio=reserve_ratio,
        maintenance_forecast_summary=_maint_summary(risk.components.maintenance_risk_score),
        predicted_major_repairs=risk.predicted_major_repairs or [],
        capital_replacement_summary=_capital_summary(
            risk.components.capital_shock_risk_score,
            risk.predicted_major_repairs or [],
        ),
        capital_shock_index=risk.capital_shock_index,
        predicted_levy_risk=risk.predicted_levy_risk,
        computed_at=risk.computed_at,
    )
