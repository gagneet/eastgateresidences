"""
True Cost of Ownership Service — Phase 2

Computes the full annualised and period cost of holding a strata unit, including:
  - Admin and sinking levies (from unit_levy_ledger)
  - Council rates (from units.council_rates_cents or property-value estimate)
  - Water charges (from units.water_estimate_cents or fixed default)
  - Maintenance probability cost (from building asset health or % of property value)
  - Capital shock buffer (from capital_shock_service or % of property value)
  - CPI inflation projection over the holding period

All monetary values are stored and returned in integer cents.
"""
import logging
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from database import db
from utils.finance_helpers import get_levy_rates

logger = logging.getLogger(__name__)

_CPI_RATE = 0.035  # 3.5% compound CPI assumption
_WATER_DEFAULT_CENTS = 180_000  # $1,800 / year default
_MAINTENANCE_DEFAULT_RATE = 0.02  # 2% of property value if asset data unavailable
_CAPITAL_SHOCK_DEFAULT_RATE = 0.005  # 0.5% of property value if shock service unavailable
_COUNCIL_RATES_DEFAULT_RATE = 0.003  # 0.3% of property value if not stored


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/services/true_cost_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


# ── Pydantic models ────────────────────────────────────────────────────────

class TrueCostOfOwnershipInput(BaseModel):
    building_id: str
    unit_number: str
    holding_period_years: int = Field(..., ge=1, le=30)
    financial_year: str
    include_rates: bool = True
    include_water: bool = True
    include_maintenance_probability: bool = True
    include_capital_shock_buffer: bool = True


class TrueCostComponent(BaseModel):
    component_name: str
    annual_amount_cents: int
    total_for_period_cents: int
    notes: str
    data_source: str  # "actual" | "estimated" | "projected"
    confidence: float


class TrueCostOfOwnershipResult(BaseModel):
    building_id: str
    unit_number: str
    unit_type: str
    holding_period_years: int
    financial_year: str
    admin_levy_cents: int
    sinking_levy_cents: int
    council_rates_cents: int
    water_estimate_cents: int
    components: list[TrueCostComponent]
    capital_shock_buffer_cents: int
    maintenance_probability_cost_cents: int
    total_annual_cents: int
    total_for_period_cents: int
    cost_per_day_cents: int
    building_average_annual_cents: int
    unit_vs_building_avg_pct: float
    # GAP-FIN-065 Item A: the levy figures are the amount CHARGED to the unit
    # year-to-date (from unit_levy_ledger), NOT the full-year budget the finance
    # dashboard shows. For a partial year these differ (the dashboard is higher),
    # which is the single biggest source of "amounts don't match" confusion on this
    # page. Surface the basis so the UI can label it explicitly.
    levy_basis: str = "ytd_charged"
    levy_basis_label: str = "Levied year-to-date"
    computed_at: datetime
    assumptions: list[str]


# ── Internal helpers ───────────────────────────────────────────────────────

def _compound_period_total(annual_cents: int, years: int, cpi: float = _CPI_RATE) -> int:
    """
    Sum of CPI-inflated annual costs over `years`:
      S = annual * ((1+r)^n - 1) / r   (geometric series, r = cpi)
    Returns integer cents.
    """
    if cpi == 0 or years == 0:
        return annual_cents * years
    total = annual_cents * ((1 + cpi) ** years - 1) / cpi
    return int(round(total))


async def _get_unit_levies(building_id: str, unit_number: str, financial_year: str = "", entitlement: float = 0.0) -> dict:
    """Admin and sinking levy for a unit, in integer cents.

    GAP-FIN-065 Item A — basis fix. This service projects the FULL annual holding
    cost, so it must use the full-year owner-payable (inc-GST) levy, NOT the
    amount charged year-to-date. Preference order:

      1. Full-year annual from `annual_levies` via
         ``get_levy_rates(financial_year) × entitlement`` — the SAME inc-GST source
         the finance dashboard and the owner-hub TCO page use (see
         ``ownerhub_service.compute_unit_tco``). ``basis="full_year_annual"``.
      2. Fallback: ``unit_levy_ledger.admin_levied/sinking_levied`` — the amount
         CHARGED year-to-date, only used when no annual_levies rate doc exists for
         the year (so worst case matches the pre-fix behaviour).
         ``basis="ytd_charged"``.

    unit_levy_ledger.admin_levied/sinking_levied are stored in DOLLARS (confirmed
    against live East Gate seed data — seeds/levy_ledger_east_gate.py has
    admin_levied=2296.0). This service's contract is integer cents throughout, so
    the conversion happens once, here, at the read boundary. The ledger's year
    field is "year" (not "financial_year") — confirmed against
    repositories/financial_repository.py's index definitions.
    """
    if financial_year and entitlement > 0:
        try:
            rates = await get_levy_rates(financial_year, building_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("true_cost_service: get_levy_rates failed: %s", exc)
            rates = {}
        admin_annual = float(rates.get("admin_annual", 0) or 0)
        sinking_annual = float(rates.get("sinking_annual", 0) or 0)
        if admin_annual > 0 or sinking_annual > 0:
            return {
                "admin_levied": int(round(admin_annual * entitlement * 100)),
                "sinking_levied": int(round(sinking_annual * entitlement * 100)),
                "source": "actual",
                "basis": "full_year_annual",
            }

    doc = await db.unit_levy_ledger.find_one(
        {"building_id": building_id, "unit_number": unit_number},
        sort=[("year", -1)]
    )
    if not doc:
        return {"admin_levied": 0, "sinking_levied": 0, "source": "missing", "basis": "ytd_charged"}
    return {
        "admin_levied": int(round(float(doc.get("admin_levied", 0) or 0) * 100)),
        "sinking_levied": int(round(float(doc.get("sinking_levied", 0) or 0) * 100)),
        "source": "actual",
        "basis": "ytd_charged",
    }


async def _get_unit_record(building_id: str, unit_number: str) -> dict:
    """Fetch the units document for property-level fields."""
    return await db.units.find_one(
        {"building_id": building_id, "unit_number": unit_number},
        {"_id": 0}
    ) or {}


async def _get_council_rates(unit: dict) -> tuple[int, str, str]:
    """
    Return (council_rates_cents, data_source, note).
    Prefers stored value; falls back to 0.3% of property_value_cents.
    """
    stored = unit.get("council_rates_cents")
    if stored and int(stored) > 0:
        return int(stored), "actual", "From unit record."
    pv = int(unit.get("property_value_cents", 0) or 0)
    if pv > 0:
        estimated = int(pv * _COUNCIL_RATES_DEFAULT_RATE)
        return estimated, "estimated", f"Estimated at {_COUNCIL_RATES_DEFAULT_RATE * 100:.1f}% of property value."
    return 0, "estimated", "Property value not available; rates excluded."


async def _get_water_estimate(unit: dict) -> tuple[int, str, str]:
    """Return (water_cents, data_source, note)."""
    stored = unit.get("water_estimate_cents")
    if stored and int(stored) > 0:
        return int(stored), "actual", "From unit record."
    return _WATER_DEFAULT_CENTS, "estimated", f"Default estimate of ${_WATER_DEFAULT_CENTS / 100:,.0f}/year."


async def _get_maintenance_probability_cost(building_id: str, unit: dict, financial_year: str = "") -> tuple[int, str, str]:
    """
    Derive maintenance probability cost from building asset health score.
    Falls back to 2% of property value.
    """
    try:
        from services.health_service import compute_financial_health
        health = await compute_financial_health(building_id)
        health_score = float(health.get("overall_score", 50) or 50)
        # Higher health score → lower maintenance probability
        risk_factor = max(0.005, (100 - health_score) / 100 * 0.04)
    except Exception:
        risk_factor = _MAINTENANCE_DEFAULT_RATE
        health_score = None

    pv = int(unit.get("property_value_cents", 0) or 0)
    if pv > 0:
        cost = int(pv * risk_factor)
        note = (
            f"Maintenance probability: {risk_factor * 100:.1f}% of property value "
            f"(building health score: {health_score:.0f}/100)."
            if health_score is not None
            else f"Default {_MAINTENANCE_DEFAULT_RATE * 100:.0f}% of property value (health data unavailable)."
        )
        source = "estimated" if health_score is None else "projected"
        return cost, source, note

    # No property value — use levy-based proxy (10% of annual levies)
    levies = await _get_unit_levies(
        building_id, unit.get("unit_number", ""), financial_year, float(unit.get("entitlement") or 0.0)
    )
    levy_total = levies["admin_levied"] + levies["sinking_levied"]
    cost = int(levy_total * 0.10)
    return cost, "estimated", "Estimated at 10% of annual levies (property value unavailable)."


async def _get_capital_shock_buffer(building_id: str, unit: dict) -> tuple[int, str, str]:
    """
    Per-unit capital shock buffer from capital_shock_service.
    Falls back to 0.5% of property value.
    """
    try:
        from services.capital_shock_service import detect_capital_shocks
        shock = await detect_capital_shocks(building_id)
        total_shock = float(shock.get("max_deficit", 0) or 0)
        total_units_count = await db.units.count_documents({"building_id": building_id})
        if total_units_count > 0 and total_shock > 0:
            entitlement = float(unit.get("entitlement", 1) or 1)
            agg = await db.units.aggregate([
                {"$match": {"building_id": building_id}},
                {"$group": {"_id": None, "total": {"$sum": "$entitlement"}}}
            ]).to_list(1)
            total_ent = float(agg[0]["total"]) if agg else float(total_units_count)
            per_unit = int(total_shock * (entitlement / max(total_ent,
                                                            1)) * 100)  # total_exposure is in dollars; multiply by 100 to convert to cents
            return per_unit, "projected", "Capital shock exposure per unit from capital shock service."
    except Exception as exc:
        logger.warning("true_cost_service: capital shock per-unit computation failed: %s", exc)

    pv = int(unit.get("property_value_cents", 0) or 0)
    if pv > 0:
        cost = int(pv * _CAPITAL_SHOCK_DEFAULT_RATE)
        return cost, "estimated", f"Default {_CAPITAL_SHOCK_DEFAULT_RATE * 100:.1f}% of property value."
    return 0, "estimated", "Capital shock buffer unavailable (no property value)."


async def _get_building_average(building_id: str, financial_year: str) -> int:
    """Average total annual levy (admin + sinking) across all units in the building.

    GAP-FIN-065 Item A: prefer the full-year owner-payable (inc-GST) basis so this
    average is apples-to-apples with the per-unit figure from _get_unit_levies
    (otherwise unit_vs_building_avg_pct would compare full-year vs YTD). Since the
    per-UOE rate is uniform, the average = (admin_annual + sinking_annual) ×
    average entitlement. Fall back to the unit_levy_ledger YTD aggregate only when no
    annual_levies rate doc exists — matching _get_unit_levies' own fallback.

    unit_levy_ledger.admin_levied/sinking_levied are stored in DOLLARS (see
    _get_unit_levies docstring above) — convert to cents at the read boundary.
    """
    try:
        rates = await get_levy_rates(financial_year, building_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("true_cost_service: building-average get_levy_rates failed: %s", exc)
        rates = {}
    admin_annual = float(rates.get("admin_annual", 0) or 0)
    sinking_annual = float(rates.get("sinking_annual", 0) or 0)
    if admin_annual > 0 or sinking_annual > 0:
        ent_agg = await db.units.aggregate([
            {"$match": {"building_id": building_id}},
            {"$group": {"_id": None, "avg_ent": {"$avg": "$entitlement"}}}
        ]).to_list(1)
        avg_ent = float(ent_agg[0]["avg_ent"]) if ent_agg and ent_agg[0].get("avg_ent") else 0.0
        if avg_ent > 0:
            return int(round((admin_annual + sinking_annual) * avg_ent * 100))

    pipeline = [
        {"$match": {"building_id": building_id, "year": financial_year}},
        {"$group": {
            "_id": None,
            "avg": {"$avg": {"$add": ["$admin_levied", "$sinking_levied"]}}
        }}
    ]
    res = await db.unit_levy_ledger.aggregate(pipeline).to_list(1)
    if res and res[0].get("avg"):
        return int(round(float(res[0]["avg"]) * 100))
    # Fallback: all levy ledger records regardless of year
    pipeline2 = [
        {"$match": {"building_id": building_id}},
        {"$group": {"_id": "$unit_number",
                    "admin": {"$max": "$admin_levied"},
                    "sinking": {"$max": "$sinking_levied"}}},
        {"$group": {"_id": None, "avg": {"$avg": {"$add": ["$admin", "$sinking"]}}}}
    ]
    res2 = await db.unit_levy_ledger.aggregate(pipeline2).to_list(1)
    return int(round(float(res2[0]["avg"]) * 100)) if res2 and res2[0].get("avg") else 0


# ── Public API ─────────────────────────────────────────────────────────────

async def compute_true_cost_of_ownership(inp: TrueCostOfOwnershipInput) -> TrueCostOfOwnershipResult:
    """
    Compute True Cost of Ownership for a unit over a given holding period.

    All monetary values are integer cents.
    CPI inflation of 3.5% is applied as a geometric series for period totals.
    """
    assumptions: list[str] = [
        f"CPI inflation assumed at {_CPI_RATE * 100:.1f}% per annum (compound).",
    ]

    unit = await _get_unit_record(inp.building_id, inp.unit_number)
    unit_type = unit.get("unit_type") or unit.get("property_type") or "unknown"

    # ── Levies ────────────────────────────────────────────────────────────
    levies = await _get_unit_levies(
        inp.building_id, inp.unit_number, inp.financial_year, float(unit.get("entitlement") or 0.0)
    )
    admin_levy_cents = levies["admin_levied"]
    sinking_levy_cents = levies["sinking_levied"]

    # GAP-FIN-065 Item A: state the levy basis so it can never be mistaken for a
    # different figure than the dashboard. full_year_annual = full-year owner-payable
    # (inc-GST) budget, matching the dashboard; ytd_charged = only charged so far this
    # year (the legacy fallback), which is lower for a partly-billed year.
    levy_basis = levies.get("basis", "ytd_charged")
    if levy_basis == "full_year_annual":
        levy_basis_label = "Full-year budget (inc-GST)"
        assumptions.append(
            "Levy amounts are the FULL-YEAR owner-payable (GST-inclusive) budget for "
            "this unit, sourced from annual_levies — the same figure shown on the "
            "finance dashboard."
        )
    else:
        levy_basis_label = "Levied year-to-date"
        assumptions.append(
            "Levy amounts are the total LEVIED YEAR-TO-DATE for this unit (from "
            "unit_levy_ledger), GST-inclusive — no full-year annual_levies budget was "
            "available. This is what the unit has been charged so far in the financial "
            "year, so it can be lower than the full-year levy budget shown on the "
            "finance dashboard; the difference is the not-yet-charged portion of the "
            "year, not a discrepancy."
        )

    if levies["source"] == "missing":
        assumptions.append("No levy record found; levy costs set to zero.")

    # ── Optional components ────────────────────────────────────────────────
    council_rates_cents = 0
    water_estimate_cents = 0
    maintenance_probability_cost_cents = 0
    capital_shock_buffer_cents = 0

    components: list[TrueCostComponent] = []

    if levy_basis == "full_year_annual":
        _admin_note = "Full-year admin fund levy for this unit (GST-inclusive), matching the dashboard."
        _sinking_note = "Full-year sinking fund levy for this unit (GST-inclusive), matching the dashboard."
    else:
        _admin_note = ("Admin fund levy charged to this unit year-to-date (GST-inclusive); "
                       "may be less than the full-year budget shown on the dashboard.")
        _sinking_note = ("Sinking fund levy charged to this unit year-to-date (GST-inclusive); "
                         "may be less than the full-year budget shown on the dashboard.")

    # Admin levy component
    components.append(TrueCostComponent(
        component_name="Admin Fund Levy",
        annual_amount_cents=admin_levy_cents,
        total_for_period_cents=_compound_period_total(admin_levy_cents, inp.holding_period_years),
        notes=_admin_note,
        data_source=levies["source"],
        confidence=0.95 if levies["source"] == "actual" else 0.5,
    ))

    # Sinking levy component
    components.append(TrueCostComponent(
        component_name="Sinking Fund Levy",
        annual_amount_cents=sinking_levy_cents,
        total_for_period_cents=_compound_period_total(sinking_levy_cents, inp.holding_period_years),
        notes=_sinking_note,
        data_source=levies["source"],
        confidence=0.95 if levies["source"] == "actual" else 0.5,
    ))

    if inp.include_rates:
        council_rates_cents, cr_source, cr_note = await _get_council_rates(unit)
        if council_rates_cents > 0:
            components.append(TrueCostComponent(
                component_name="Council Rates",
                annual_amount_cents=council_rates_cents,
                total_for_period_cents=_compound_period_total(council_rates_cents, inp.holding_period_years),
                notes=cr_note,
                data_source=cr_source,
                confidence=0.9 if cr_source == "actual" else 0.6,
            ))
            if cr_source == "estimated":
                assumptions.append(f"Council rates: {cr_note}")

    if inp.include_water:
        water_estimate_cents, w_source, w_note = await _get_water_estimate(unit)
        components.append(TrueCostComponent(
            component_name="Water & Sewerage",
            annual_amount_cents=water_estimate_cents,
            total_for_period_cents=_compound_period_total(water_estimate_cents, inp.holding_period_years),
            notes=w_note,
            data_source=w_source,
            confidence=0.8 if w_source == "actual" else 0.5,
        ))
        if w_source == "estimated":
            assumptions.append(f"Water: {w_note}")

    if inp.include_maintenance_probability:
        maintenance_probability_cost_cents, m_source, m_note = await _get_maintenance_probability_cost(
            inp.building_id, unit, inp.financial_year
        )
        if maintenance_probability_cost_cents > 0:
            components.append(TrueCostComponent(
                component_name="Maintenance Probability Cost",
                annual_amount_cents=maintenance_probability_cost_cents,
                total_for_period_cents=_compound_period_total(
                    maintenance_probability_cost_cents, inp.holding_period_years
                ),
                notes=m_note,
                data_source=m_source,
                confidence=0.6,
            ))
            assumptions.append(f"Maintenance: {m_note}")

    if inp.include_capital_shock_buffer:
        capital_shock_buffer_cents, cs_source, cs_note = await _get_capital_shock_buffer(
            inp.building_id, unit
        )
        if capital_shock_buffer_cents > 0:
            components.append(TrueCostComponent(
                component_name="Capital Shock Buffer",
                annual_amount_cents=capital_shock_buffer_cents,
                total_for_period_cents=_compound_period_total(
                    capital_shock_buffer_cents, inp.holding_period_years
                ),
                notes=cs_note,
                data_source=cs_source,
                confidence=0.55,
            ))
            assumptions.append(f"Capital shock: {cs_note}")

    # ── Totals ─────────────────────────────────────────────────────────────
    total_annual_cents = (
            admin_levy_cents
            + sinking_levy_cents
            + council_rates_cents
            + water_estimate_cents
            + maintenance_probability_cost_cents
            + capital_shock_buffer_cents
    )
    total_for_period_cents = _compound_period_total(total_annual_cents, inp.holding_period_years)
    cost_per_day_cents = int(total_annual_cents / 365) if total_annual_cents > 0 else 0

    # ── Building average ───────────────────────────────────────────────────
    building_average_annual_cents = await _get_building_average(inp.building_id, inp.financial_year)
    if building_average_annual_cents > 0:
        unit_vs_avg = round(
            (total_annual_cents - building_average_annual_cents) / building_average_annual_cents * 100, 2
        )
    else:
        unit_vs_avg = 0.0

    return TrueCostOfOwnershipResult(
        building_id=inp.building_id,
        unit_number=inp.unit_number,
        unit_type=unit_type,
        holding_period_years=inp.holding_period_years,
        financial_year=inp.financial_year,
        admin_levy_cents=admin_levy_cents,
        sinking_levy_cents=sinking_levy_cents,
        council_rates_cents=council_rates_cents,
        water_estimate_cents=water_estimate_cents,
        components=components,
        capital_shock_buffer_cents=capital_shock_buffer_cents,
        maintenance_probability_cost_cents=maintenance_probability_cost_cents,
        total_annual_cents=total_annual_cents,
        total_for_period_cents=total_for_period_cents,
        cost_per_day_cents=cost_per_day_cents,
        building_average_annual_cents=building_average_annual_cents,
        unit_vs_building_avg_pct=unit_vs_avg,
        levy_basis=levy_basis,
        levy_basis_label=levy_basis_label,
        computed_at=datetime.now(timezone.utc),
        assumptions=assumptions,
    )
