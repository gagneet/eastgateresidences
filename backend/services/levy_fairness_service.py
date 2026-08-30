# @featuretrace:levy-fairness — asset-driven capital-cost allocation across benefit
#   groups (LBFI score, virtual cost centres, subsidy map). Distinct from the general
#   "levy" tag (levy charging/notices/reminders) — this is equitable cost-SPLITTING
#   across benefit groups (APARTMENTS_ONLY, TOWNHOUSES_ONLY, GARAGE_USERS, etc), not
#   levy charging itself.
# Layer: service
# Related: backend/routers/intelligence.py, backend/cron/cron_maintenance_intelligence.py
#          tests/backend/test_levy_fairness.py, tests/backend/test_lbfi_sinking_fund.py,
#          tests/backend/test_subsidy_map.py
# Collections: benefit_groups, building_assets, facilities, unit_levy_ledger, units
"""
Levy Fairness Service — Asset-Driven Cost Allocation Engine

Uses existing database collections only (no seeded/fake data):
  - unit_levy_ledger: actual levy amounts per unit (source of truth for totals)
  - building_assets + facilities: derive capital cost split by benefit group
  - benefit_groups: group definitions (APARTMENTS_ONLY, TOWNHOUSES_ONLY, ALL_LOTS)
  - units: unit entitlements and attributes

The 'Regenerate' button always produces correct results from real building data.
No dependency on facility_cost_centres collection.
"""
import logging
import statistics
from datetime import datetime, timezone, date

from pydantic import BaseModel
from typing import Dict, Any, List, Tuple

from database import db
from services.facility_allocation_engine import calculate_facility_allocation, get_unit_attributes
from services.cost_allocation_rules import (
    RuleNotApplicable,
    allocate_by_rule,
    load_rules_for_building,
    rule_reasoning,
)
from services.gst_service import get_building_levy_gst_settings
from services.levy_simulation_engine import apply_transition_caps
from services.monte_carlo_engine import run_monte_carlo_levy_simulation
from services.owner_service import get_all_unit_owners
from utils.finance_helpers import get_latest_levy_year, get_levy_rates

logger = logging.getLogger(__name__)


# ─── Subsidy Map Models ───────────────────────────────────────────────────────

class SubsidyMapEntry(BaseModel):
    unit_type: str  # "Apartment" | "Townhouse" | etc.
    facility_category: str  # "lift" | "pool" | "gym" | "fire_system" | "corridor_cleaning" | etc.
    facility_id: str
    facility_name: str
    benefit_group: str  # benefit group this facility belongs to
    annual_cost_cents: int  # total annual cost of this facility in cents
    uoe_share_cents: int  # what this unit type pays via flat UOE levy (cents)
    benefit_share_cents: int  # what this unit type should pay based on LBFI benefit model (cents)
    subsidy_amount_cents: int  # benefit_share - uoe_share (negative = paying excess)
    subsidy_direction: str  # "paying_excess" | "being_subsidised" | "fair"
    affected_units: List[str]  # unit_numbers in this unit type


class SubsidyMapResult(BaseModel):
    building_id: str
    financial_year: str
    total_cross_subsidy_cents: int  # aggregate excess paid by subsidising groups
    subsidy_map: List[SubsidyMapEntry]
    summary_by_unit_type: Dict[str, Any]  # {UnitType: {net_subsidy_cents, avg_per_unit_cents, units_count, role}}
    key_findings: List[str]  # human-readable summary lines
    computed_at: datetime


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/services/levy_fairness_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


async def _log_fairness_audit(
        action: str,
        user_email: str,
        user_role: str,
        building_id: str,
        details: Dict[str, Any] = None,
) -> None:
    """Write an immutable audit record to levy_fairness_audit collection."""
    try:
        await db.levy_fairness_audit.insert_one({
            "action": action,
            "user_email": user_email,
            "user_role": user_role,
            "building_id": building_id,
            "details": details or {},
            "timestamp": _now(),
        })
    except Exception as exc:
        logger.warning("levy_fairness_service: audit log write failed: %s", exc)


async def _configured_lot_groups(building_id: str) -> Dict[str, str]:
    """unit_number -> operator-configured group name. Empty when none are configured.

    Empty is a meaningful answer, not a failure: a building with no configured groups
    falls back to inference, which is the pre-existing behaviour. Never raises — a
    building with no PostgreSQL scheme, or an unreachable database, means "no
    configuration", and the fairness run continues on the fallback rather than 500-ing a
    page over a settings table.
    """
    try:
        from db_postgres.repos.config_repo import resolve_scheme_context
        from db_postgres.session import async_session_context, set_tenant
        from sqlalchemy import text as _sql

        scheme = await resolve_scheme_context(building_id)
        if not scheme or not scheme.get("tenant_id"):
            return {}
        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            rows = (await session.execute(
                _sql("""
                    SELECT l.unit_number, g.name
                      FROM core.lot_benefit_groups m
                      JOIN core.lots l ON l.lot_id = m.lot_id
                      JOIN core.benefit_groups g ON g.benefit_group_id = m.benefit_group_id
                     WHERE l.scheme_id = :sid
                """),
                {"sid": str(scheme["scheme_id"])},
            )).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "levy_fairness: could not read configured benefit groups for %s (%s) — "
            "falling back to inferred groups", building_id, exc,
        )
        return {}


def _group_key(unit: Dict[str, Any]) -> str:
    """Generated function header.

    Function: _group_key
    Path: backend/services/levy_fairness_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    unit_type = (unit.get("unit_type") or unit.get("property_type") or "").lower()
    if "apartment" in unit_type:
        return "Apartment"
    if "townhouse" in unit_type:
        return "Townhouse"
    if "villa" in unit_type:
        return "Villa"
    if "retail" in unit_type:
        return "Retail"
    if "commercial" in unit_type:
        return "Commercial"
    # Derive from unit_number prefix (DB property_type is often empty)
    un = unit.get("unit_number", "")
    if un.upper().startswith("UA"):
        return "Apartment"
    if un.upper().startswith("TH"):
        return "Townhouse"
    return "Other"



# --------------------------------------------------------------------------------------
# Remedy catalogue
# --------------------------------------------------------------------------------------
# A fairness engine that only produces a dollar delta hands a committee an argument and
# no way to end it. Re-splitting the levy is the LAST resort: in the ACT it needs a
# special resolution under UTMA s.78(2)(b), it must be justified against the s.78(3)
# factors, and any owner may take it to ACAT for review. Most asymmetries have a
# structural fix that needs no resolution at all, because it changes the COST rather than
# the split -- and a cost that is measured no longer has to be argued about.
#
# The catalogue is keyed on the SHAPE of the asymmetry, never on a building. "A utility
# on one common meter" behaves the same in every scheme; "the townhouses have solar" does
# not generalise and is an input, not a rule.
_REMEDY_CATALOGUE = {
    "metered_utility": {
        "title": "Sub-meter the service and bill actual consumption",
        "detail": (
            "A utility read through a single common meter cannot be apportioned on "
            "evidence, so it defaults to entitlement regardless of who consumed it. "
            "Installing sub-meters converts the line from an estimate into a measured "
            "quantity, which moves the cost to the actual user without any change to the "
            "levy method."
        ),
        "requires_resolution": False,
        "evidence_produced": "per-group consumption readings",
    },
    "insured_asset": {
        "title": "Re-scope the sum insured, then let the premium follow it",
        "detail": (
            "Premium is apportioned on entitlement because the policy states one "
            "building sum insured. Ask the insurer to itemise the reinstatement value by "
            "structure. Where a group carries its own cover for its own structure or for "
            "an improvement it owns, that value is excluded from the corporation's sum "
            "insured and the premium falls for everyone -- the split stops being "
            "contested because the underlying cost has changed."
        ),
        "requires_resolution": False,
        "evidence_produced": "insurer's itemised reinstatement schedule",
    },
    "zoned_service_contract": {
        "title": "Scope the service contract to the zone it serves",
        "detail": (
            "One contract covering the whole site produces one invoice that must then be "
            "split by argument. Re-tendering it as separate scopes per zone produces "
            "separate invoices, each already attributable. The apportionment question "
            "disappears into the procurement."
        ),
        "requires_resolution": False,
        "evidence_produced": "per-zone invoices",
    },
    "access_controlled_facility": {
        "title": "Measure access before apportioning the facility",
        "detail": (
            "Where entry is already controlled electronically, the access system holds "
            "the usage evidence the apportionment needs. Extracting per-group counts "
            "turns an assumption into a driver a tribunal can check."
        ),
        "requires_resolution": False,
        "evidence_produced": "access-control usage counts",
    },
    "capital_plan_asymmetry": {
        "title": "Attribute each capital item to the lots it serves, in the plan itself",
        "detail": (
            "Where the ten-year plan schedules work that physically serves only some "
            "lots, the imbalance is created when the plan is written, not when the levy "
            "is struck. Recording the served group against each planned item makes the "
            "sinking fund self-documenting, and lets contributions be set per item "
            "rather than renegotiating the whole levy method."
        ),
        "requires_resolution": False,
        "evidence_produced": "per-item attribution in the capital works plan",
    },
    "class_contribution": {
        "title": "Set a class contribution for the specific cost line",
        "detail": (
            "UTMA s.78(2)(b) allows a determination that only owners in a stated class "
            "pay a stated contribution. This is narrower and far more defensible than "
            "changing the general method: it names one cost, one class, and one reason. "
            "It still requires a special resolution addressing the s.78(3) factors -- the "
            "nature of the buildings, the features and character of the units and common "
            "property, the purposes for which units are used and their likely impact on "
            "common property, and whether the burden is commensurate with that use."
        ),
        "requires_resolution": True,
        "evidence_produced": "special resolution and supporting evidence pack",
    },
    "immaterial": {
        "title": "Record the difference and take no action",
        "detail": (
            "The modelled difference is small enough that the cost and risk of changing "
            "the method exceed it. Recording that finding is itself a defensible "
            "decision, and it is the one a committee most often should reach."
        ),
        "requires_resolution": False,
        "evidence_produced": "minuted decision",
    },
}


def _build_remedies(impact_by_group, capital_outlook, cost_lines):
    """Turn measured asymmetries into the structural options that could remove them.

    Deliberately returns OPTIONS, never a recommendation to change the levy method.
    Which remedy a corporation takes is a decision for the owners; presenting one as
    the answer is how an engine ends up before a tribunal being asked what evidence it
    relied on.
    """
    remedies: list[dict] = []
    materiality = 0.01  # 1% of a group's current contribution

    for line in cost_lines:
        shape = line.get("shape")
        if not shape or shape not in _REMEDY_CATALOGUE:
            continue
        entry = _REMEDY_CATALOGUE[shape]
        remedies.append({
            "cost_line": line.get("name"),
            "shape": shape,
            "amount": line.get("cost"),
            "title": entry["title"],
            "detail": entry["detail"],
            "requires_resolution": entry["requires_resolution"],
            "evidence_produced": entry["evidence_produced"],
            "status": "available",
        })

    if capital_outlook and capital_outlook.get("groups"):
        skewed = [
            g for g in capital_outlook["groups"]
            if abs(g.get("delta", 0)) > max(1.0, abs(g.get("sinking_contribution", 0)) * materiality)
        ]
        if skewed:
            entry = _REMEDY_CATALOGUE["capital_plan_asymmetry"]
            remedies.append({
                "cost_line": "10-year capital works plan",
                "shape": "capital_plan_asymmetry",
                "amount": capital_outlook.get("planned_total"),
                "title": entry["title"],
                "detail": entry["detail"],
                "requires_resolution": False,
                "evidence_produced": entry["evidence_produced"],
                "status": "available",
            })

    material = [
        r for r in impact_by_group
        if abs(r.get("delta", 0)) > max(1.0, r.get("current_total", 0) * materiality)
    ]
    if material:
        entry = _REMEDY_CATALOGUE["class_contribution"]
        remedies.append({
            "cost_line": "general fund contribution method",
            "shape": "class_contribution",
            "amount": round(max(abs(r["delta"]) for r in material), 2),
            "title": entry["title"],
            "detail": entry["detail"],
            "requires_resolution": True,
            "evidence_produced": entry["evidence_produced"],
            "status": "last_resort",
        })
    else:
        entry = _REMEDY_CATALOGUE["immaterial"]
        remedies.append({
            "cost_line": "general fund contribution method",
            "shape": "immaterial",
            "amount": 0.0,
            "title": entry["title"],
            "detail": entry["detail"],
            "requires_resolution": False,
            "evidence_produced": entry["evidence_produced"],
            "status": "recommended",
        })
    return remedies


def _build_capital_outlook(capital_items, lot_groups, payment_shares, sinking_annual, total_ue):
    """The ten-year view, reconciled against ten years of sinking levy -- not one.

    The annual lens cannot show this. A lift replacement in 2031 and a carpet renewal in
    2033 are each invisible in any single year's levy, yet together they are the clearest
    statement a scheme makes about who its common property is FOR. Comparing them against
    one year of contributions is the error that produced the 2.85x ratio; comparing them
    against the matching horizon of sinking contributions is the correct question.
    """
    if not capital_items:
        return None
    years = [int(i["year"]) for i in capital_items if i.get("year")]
    if not years:
        return None
    horizon_years = max(1, max(years) - min(years) + 1)

    per_group_spend: dict[str, float] = {}
    for item in capital_items:
        for un, share in item["shares"].items():
            g = lot_groups.get(un, "Other")
            per_group_spend[g] = per_group_spend.get(g, 0.0) + share * item["cost"]

    per_group_sinking: dict[str, float] = {}
    for un, share in payment_shares.items():
        g = lot_groups.get(un, "Other")
        per_group_sinking[g] = per_group_sinking.get(g, 0.0) + share * sinking_annual * total_ue * horizon_years

    groups = []
    for g in sorted(set(per_group_spend) | set(per_group_sinking)):
        spend = per_group_spend.get(g, 0.0)
        contrib = per_group_sinking.get(g, 0.0)
        groups.append({
            "group": g,
            "planned_spend": round(spend, 2),
            "sinking_contribution": round(contrib, 2),
            "delta": round(spend - contrib, 2),
            "reasoning": {
                "basis": "capital_plan_attribution",
                "arithmetic": (
                    f"{g} is attributed ${spend:,.2f} of planned capital works over "
                    f"{horizon_years} year(s) and contributes ${contrib:,.2f} of sinking "
                    f"levy across the same period; difference ${spend - contrib:,.2f}"
                ),
                "editable": True,
                "overridden": False,
            },
        })

    return {
        "horizon_years": horizon_years,
        "first_year": min(years),
        "last_year": max(years),
        "planned_total": round(sum(i["cost"] for i in capital_items), 2),
        "sinking_total": round(sinking_annual * total_ue * horizon_years, 2),
        # The capital deltas do NOT net to zero, and that is a finding rather than a
        # defect: unlike the annual lens, planned spend and planned contributions are two
        # independent figures. A positive gap means the plan is not funded by the current
        # sinking levy at all -- which is a solvency question the committee must answer
        # BEFORE the fairness question, since no split of an insufficient fund is fair.
        "funding_gap": round(
            sum(i["cost"] for i in capital_items) - sinking_annual * total_ue * horizon_years, 2
        ),
        "attributed_items": sum(
            1 for i in capital_items if i.get("attribution") == "attributed"
        ),
        "unattributed_items": sum(
            1 for i in capital_items if i.get("attribution") == "entitlement_default"
        ),
        "unresolved_items": [
            {"name": i["name"], "cost": round(i["cost"], 2), "asset_ref": i.get("asset_ref")}
            for i in capital_items if i.get("attribution") == "unresolved_reference"
        ],
        # Named so a reader can tell an attributed item from one that defaulted to
        # entitlement because nobody has said who it serves.
        "items": [
            {
                "name": i["name"],
                "year": i["year"],
                "cost": round(i["cost"], 2),
                "attributed_to": sorted({
                    lot_groups.get(un, "Other") for un, sh in i["shares"].items() if sh > 0
                }),
                "attribution": i.get("attribution", "entitlement_default"),
            }
            for i in sorted(capital_items, key=lambda x: (x.get("year") or 0))
        ],
        "groups": groups,
    }

def _compute_lbfi(
        payment_shares: Dict[str, float],
        benefit_shares: Dict[str, float],
) -> Tuple[float, float]:
    """
    Σ=1 Normalised LBFI (product-hardening fix applied).

    Old formula (had group-size normalisation bias — equal weight per unit):
        D = 0.5 × Σ|p_i − b_i|,  LBFI = (1 − D) × 100

    New formula (Σ=1 normalised weighted ratio, corrects UOE-size bias):
        raw_i    = b_i / p_i          (benefit-to-payment ratio per unit)
        weight_i = p_i                (UOE share; Σ p_i = 1)
        index    = Σ(raw_i) / Σ(raw_i × weight_i)   normalised ratio
        D        = |index / n − 1|    deviation from perfect (0 = fair)
        LBFI     = max(0, (1 − D) × 100)

    At perfect fairness (b_i = p_i ∀ i):
        index = n,  D = 0,  LBFI = 100.

    High-entitlement units contribute proportionally more to D than
    low-entitlement units, eliminating the equal-count bias of the old formula.
    """
    keys = set(payment_shares.keys()) | set(benefit_shares.keys())
    active = [k for k in keys if payment_shares.get(k, 0.0) > 1e-9]
    if not active:
        return 100.0, 0.0

    raw_scores = [benefit_shares.get(k, 0.0) / payment_shares[k] for k in active]
    weights = [payment_shares[k] for k in active]

    numerator = sum(raw_scores)
    denominator = sum(r * w for r, w in zip(raw_scores, weights))  # = Σ(b_i) ≈ 1

    n = len(active)
    if denominator < 1e-9:
        return 0.0, 1.0

    index = numerator / denominator  # = Σ(b_i/p_i) / Σ(b_i)
    D = abs(index / n - 1.0)
    score = max(0.0, min(100.0, (1 - D) * 100))
    return round(score, 2), round(D, 4)


def _interpret_lbfi(score: float) -> str:
    """Generated function header.

    Function: _interpret_lbfi
    Path: backend/services/levy_fairness_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if score >= 90:
        return "Strong alignment between levy contributions and benefits."
    if score >= 80:
        return "Good alignment with minor cross-subsidies."
    if score >= 60:
        return "Moderate mismatch between payments and benefits."
    return "Strong distortions detected — significant cross-subsidies."


def _resolve_benefit_group_units(bg: Dict[str, Any], units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Resolve which units belong to a benefit group.

    An explicit ``lot_numbers`` list always wins when set. Otherwise falls back to
    matching on the group's own ``name`` — this fallback previously had two real bugs
    (audit finding, 2026-08-19), both fixed here:

    1. ``"GARAGE" in name`` matched on ``car_spaces`` — every East Gate unit has
       ``car_spaces > 0`` (confirmed live: 87/87), so GARAGE_USERS silently resolved to
       every unit, identical to ALL_LOTS, instead of a distinct garage-access subset.
       ``garage_spaces`` (only 6/87 units > 0) is the field that actually distinguishes
       units with a real garage from units with only an open car space — use that instead.
    2. ``"BASEMENT" in name`` had no matching branch at all, so a group named
       BASEMENT_USERS with no explicit ``lot_numbers`` fell through to the final
       ``return units`` — every unit, again indistinguishable from ALL_LOTS. No dedicated
       "basement storage" field exists on ``units`` today, and BASEMENT_USERS' own
       description ("basement parking or storage access") overlaps with GARAGE_USERS'
       — East Gate's real basement-area facilities (HVAC, garage, garage access) are in
       fact all tagged to GARAGE_USERS today, not BASEMENT_USERS. Best available heuristic
       until a dedicated field exists: resolve BASEMENT the same way as GARAGE
       (``garage_spaces > 0``), rather than leaving it equivalent to "everyone."
    """
    lot_numbers = bg.get("lot_numbers") or []
    name = (bg.get("name") or "").upper()
    if lot_numbers:
        return [u for u in units if u.get("lot_number") in lot_numbers or u.get("unit_number") in lot_numbers]
    if "APARTMENT" in name:
        return [u for u in units if "apartment" in (u.get("unit_type") or "").lower()]
    if "TOWNHOUSE" in name:
        return [u for u in units if "townhouse" in (u.get("unit_type") or "").lower()]
    if "GARAGE" in name or "BASEMENT" in name:
        return [u for u in units if (u.get("garage_spaces") or 0) > 0]
    return units


def _compute_subsidy_map(
        payment_shares: Dict[str, float],
        benefit_shares: Dict[str, float],
        lot_groups: Dict[str, str],
        total_cost_basis: float,
        facility_costs: Dict[str, float],
        facility_benefit_shares: Dict[str, Dict[str, float]],
        facility_names: Dict[str, str],
) -> Dict[str, Any]:
    """Generated function header.

    Function: _compute_subsidy_map
    Path: backend/services/levy_fairness_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    keys = set(payment_shares.keys()) | set(benefit_shares.keys())
    deltas = {k: payment_shares.get(k, 0.0) - benefit_shares.get(k, 0.0) for k in keys}
    sum_delta = sum(deltas.values())

    group_net_subsidy: Dict[str, float] = {}
    group_counts: Dict[str, int] = {}
    for lot in keys:
        group = lot_groups.get(lot, "Other")
        group_net_subsidy[group] = group_net_subsidy.get(group, 0.0) + (deltas[lot] * total_cost_basis)
        group_counts[group] = group_counts.get(group, 0) + 1

    contributors = {g: v for g, v in group_net_subsidy.items() if v > 50}
    recipients = {g: v for g, v in group_net_subsidy.items() if v < -50}
    total_recipient_need = sum(abs(v) for v in recipients.values())

    flows = []
    if contributors and recipients and total_recipient_need > 0:
        for c, c_val in contributors.items():
            for r, r_val in recipients.items():
                flow_amount = c_val * (abs(r_val) / total_recipient_need)
                if flow_amount > 0:
                    flows.append({"from": c, "to": r, "amount": round(flow_amount, 2)})

    group_summary = []
    for group, net in group_net_subsidy.items():
        role = "Contributor" if net > 50 else "Recipient" if net < -50 else "Neutral"
        group_summary.append({
            "group": group,
            "count": group_counts.get(group, 0),
            "net_subsidy": round(net, 2),
            "role": role,
        })

    top_drivers = []
    for fac_id, cost in facility_costs.items():
        name = facility_names.get(fac_id, fac_id)
        top_drivers.append({"facility": name, "amount": round(cost, 2)})
    top_drivers.sort(key=lambda x: x["amount"], reverse=True)

    sanity = {
        "sum_delta": round(sum_delta, 4),
        "total_check": round(sum_delta * total_cost_basis, 2),
        "sum_delta_approx_zero": abs(sum_delta) < 0.01,
        "checks_passed": abs(sum_delta) < 0.01,
    }

    return {
        "flows": flows,
        "group_net_subsidy": group_net_subsidy,
        "group_summary": group_summary,
        "total_cost_basis": total_cost_basis,
        "top_drivers": top_drivers,
        "sanity": sanity,
    }


async def simulate_levy_fairness(building_id: str) -> Dict[str, Any]:
    """
    Backwards-compatible levy fairness engine used by legacy tests and cron jobs.
    """
    units = await db.units.find({"building_id": building_id}, {"_id": 0}).to_list(1000)
    if not units:
        return {"error": "No units found"}

    facilities = await db.facilities.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}}, {"_id": 0}
    ).to_list(500)
    assets = await db.building_assets.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}}, {"_id": 0}
    ).to_list(2000)
    benefit_groups_list = await db.benefit_groups.find({"building_id": building_id}, {"_id": 0}).to_list(500)
    benefit_groups = {bg["id"]: bg for bg in benefit_groups_list}
    unit_attributes = await get_unit_attributes(building_id)

    virtual_cost_centres = await _derive_virtual_cost_centres(building_id)
    facility_costs = {f["facility_id"]: f["annual_cost"] for f in virtual_cost_centres}
    facility_names = {f["facility_id"]: f["facility_name"] for f in virtual_cost_centres}

    facility_benefit_shares: Dict[str, Dict[str, float]] = {}

    # TWO COST BASES, NEVER ONE.
    #
    # Operating costs recur every year and are met from the ADMIN fund. Capital works
    # happen once, years apart, and are met from the SINKING fund. Adding them into a
    # single `benefit_totals` and comparing the result to one year of levy is what
    # produced East Gate's 2.85x — but simply deleting the capital side would be worse,
    # because the capital schedule is the STRONGEST evidence of asymmetric benefit a
    # scheme has. Carpet, internal painting and lift replacement appear in the plan for
    # one group of lots and never for the other; that asymmetry is visible over ten years
    # and invisible in any single one.
    #
    # So each base is kept whole, weights its OWN fund, and is reconciled against its own
    # horizon. See `capital_outlook` below for the ten-year view.
    operating_totals: Dict[str, float] = {u["unit_number"]: 0.0 for u in units}
    capital_totals: Dict[str, float] = {u["unit_number"]: 0.0 for u in units}
    operating_cost_basis = 0.0
    capital_cost_basis = 0.0
    capital_items: list[dict] = []

    # Operator-recorded apportionment rules. A rule expresses what a group TAG cannot: a
    # partly-shared cost with a measured driver, like a garage where one group holds 39 of
    # 139 bays. Where a rule exists it wins; where it does not, or cannot yield a
    # defensible split, the group-tag allocation stands unchanged.
    #
    # Groups here are keyed by BENEFIT GROUP, and lot_groups below is resolved further
    # down, so the rule pass needs its own resolution. It is the same call.
    _entitlements = {
        u["unit_number"]: float(u.get("entitlement", 0) or 0) for u in units
    }
    # Restricted to the units this run is actually working with. `_configured_lot_groups`
    # reads Postgres, and a lot can be assigned to a group there that is absent from
    # `units` here -- a lot deleted from Mongo, a stale assignment, a scheme mid-repair.
    # Allocating benefit to a unit the engine does not hold would credit cost to a lot
    # that gets no row, and the totals dict it lands in is keyed off `units`, so it
    # raises KeyError rather than quietly mis-stating a share. Intersecting is the fix in
    # both directions: nothing is dropped from `units`, and nothing is invented into it.
    _rule_lot_groups = {
        un: g for un, g in (await _configured_lot_groups(building_id)).items()
        if un in _entitlements
    }
    allocation_rules = await load_rules_for_building(building_id)
    rule_reasons: Dict[str, Dict[str, Any]] = {}

    def _apply_rule(cost_line: str, cost: float):
        """Rule-driven allocation for one cost line, or None to fall back."""
        rule = allocation_rules.get(cost_line)
        if not rule or not _rule_lot_groups:
            return None
        try:
            alloc = allocate_by_rule(rule, cost, _rule_lot_groups, _entitlements)
        except RuleNotApplicable as exc:
            # Reported, not silent: an operator who entered a rule and sees the old split
            # needs to know the rule was rejected and why.
            logger.info(
                "levy_fairness: rule for %r not applied (%s) — using benefit-group tags",
                cost_line, exc,
            )
            return None
        rule_reasons[cost_line] = rule_reasoning(rule, cost)
        return alloc

    for fac in virtual_cost_centres:
        _cost = float(fac.get("annual_cost", 0) or 0)
        allocation = _apply_rule(fac["facility_id"], _cost)
        if allocation is None:
            allocation = await calculate_facility_allocation(fac, units, benefit_groups, unit_attributes)
        if not allocation:
            continue
        total = sum(allocation.values()) or 1.0
        shares = {un: val / total for un, val in allocation.items()}
        facility_benefit_shares[fac["facility_id"]] = shares
        for un, val in allocation.items():
            operating_totals[un] += val
        operating_cost_basis += _cost

    schedule = await db.capital_replacement_schedule.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}}, {"_id": 0}
    ).to_list(500)
    _assets_by_id = {a["id"]: a for a in assets if a.get("id")}
    _facility_groups = {f["id"]: f.get("benefit_group_id") for f in facilities if f.get("id")}
    for item in schedule:
        cost = float(item.get("estimated_cost", 0) or 0)
        if cost <= 0:
            continue
        fac_id = item.get("facility_id") or item.get("asset_id") or item.get("asset_name") or "capital"
        facility_names.setdefault(fac_id, item.get("asset_name", "Capital Works"))
        facility_costs[fac_id] = facility_costs.get(fac_id, 0) + cost
        # RESOLVE THE SERVED GROUP BEFORE FALLING BACK TO ENTITLEMENT.
        #
        # A capital_replacement_schedule row carries no benefit_group_id of its own, so
        # `item.get(...)` was always None and every planned item -- lift motors, common
        # area carpets, tower facade -- allocated across the whole scheme on entitlement.
        # The attribution already exists one join away: the schedule shares `asset_id`
        # with building_assets, which DOES carry benefit_group_id, and each asset names
        # its facility, which carries one too.
        #
        # Without this join the ten-year view reports every item as serving everybody,
        # which is the precise opposite of what a capital plan is evidence of. It also
        # fails safe in the wrong direction: an unattributed item spreads a cost onto lots
        # it does not serve, silently, and reads as agreement.
        _asset_ref = item.get("asset_id") or ""
        _asset = _assets_by_id.get(_asset_ref)
        _group_id = (
            item.get("benefit_group_id")
            or (_asset or {}).get("benefit_group_id")
            or _facility_groups.get((_asset or {}).get("facility_id") or "")
        )
        # THREE outcomes, never two. "Nobody has said who this serves" and "this names an
        # asset that does not exist" both produce an entitlement split, and collapsing
        # them hides the second -- which is a data fault, not an open question.
        #
        # East Gate has exactly this: the plan's largest line, a $247,611.94 lift motor
        # replacement, references an asset flagged `is_test_data`. The asset read filters
        # test rows (correctly) so the reference dangles, and before this the item quietly
        # spread across all 87 lots including the 17 with no lift access.
        if fac_id in rule_reasons:
            _attribution = "attributed"
        elif _group_id:
            _attribution = "attributed"
        elif _asset_ref and _asset is None:
            _attribution = "unresolved_reference"
        else:
            _attribution = "entitlement_default"
        facility_stub = {
            "annual_cost": cost,
            "benefit_group_id": _group_id,
            "allocation_driver": "unit_entitlement",
        }
        allocation = _apply_rule(fac_id, cost)
        if allocation is None:
            allocation = await calculate_facility_allocation(
                facility_stub, units, benefit_groups, unit_attributes)
        if not allocation:
            continue
        total = sum(allocation.values()) or 1.0
        facility_benefit_shares[fac_id] = {un: val / total for un, val in allocation.items()}
        for un, val in allocation.items():
            capital_totals[un] += val
        capital_cost_basis += cost
        capital_items.append({
            "item_id": fac_id,
            "name": item.get("asset_name") or facility_names.get(fac_id, fac_id),
            "year": item.get("replacement_year"),
            "cost": cost,
            "shares": {un: val / total for un, val in allocation.items()},
            # "attributed" means somebody recorded who this work serves; "entitlement"
            # means nobody has, and it defaulted. A committee needs to see which, because
            # only the second is a question still open to them.
            "attribution": _attribution,
            "asset_ref": _asset_ref or None,
        })

    # Combined base, still reported, because the LBFI and the subsidy map are defined over
    # total benefit rather than per fund.
    benefit_totals = {
        un: operating_totals.get(un, 0.0) + capital_totals.get(un, 0.0)
        for un in operating_totals
    }
    total_cost_basis = operating_cost_basis + capital_cost_basis
    if total_cost_basis <= 0:
        total_cost_basis = sum(benefit_totals.values()) or 1.0

    # The cost base determines the WEIGHTS, never the AMOUNT.
    #
    # `benefit_totals` mixes annual facility costs with the ENTIRE multi-year capital
    # replacement schedule, while `payment_totals` below is a SINGLE year's levy. Treating
    # the first as a dollar figure comparable to the second is why East Gate reported
    # $440,375 of contributions against $1,254,874 of "benefit" — a 2.85x ratio in which
    # every group came out owing more, which is impossible for a redistribution.
    #
    # A fairness model answers "given this levy, who should bear what share of it" — so
    # the benefit figures are normalised to SHARES here and applied to the real levy total
    # further down. That makes the result zero-sum by construction rather than by
    # assertion, and it keeps the capital schedule doing the job it should: a lot that
    # benefits from more future capital works carries more of the sinking levy, without
    # the schedule inflating the total anyone pays.
    def _to_shares(totals: Dict[str, float]) -> Dict[str, float]:
        t = sum(totals.values())
        return {un: (v / t if t > 0 else 0.0) for un, v in totals.items()}

    benefit_shares = _to_shares(benefit_totals)
    operating_shares = _to_shares(operating_totals)
    capital_shares = _to_shares(capital_totals)

    latest_year = await get_latest_levy_year(building_id)
    levy_rates = await get_levy_rates(str(latest_year), building_id) if latest_year else {}
    total_rate = levy_rates.get("admin_annual", 0) + levy_rates.get("sinking_annual", 0)
    total_ue = sum(float(u.get("entitlement", 0) or 0) for u in units) or 1.0
    payment_totals = {
        u["unit_number"]: float(u.get("entitlement", 0) or 0) * total_rate
        for u in units
    }

    # The pool being redistributed: exactly what the levy raises this year. Every
    # benefit-weighted figure is a share of THIS, which is what makes the model zero-sum.
    _levy_pool = sum(payment_totals.values())
    # The two pools the redistribution actually acts on. Their sum is _levy_pool, so
    # weighting each by its own benefit base keeps the whole model zero-sum.
    _admin_pool = levy_rates.get("admin_annual", 0) * total_ue
    _sinking_pool = levy_rates.get("sinking_annual", 0) * total_ue
    total_paid = sum(payment_totals.values()) or 1.0
    payment_shares = {un: val / total_paid for un, val in payment_totals.items()}

    # No real current levy rate data (e.g. building/year with no issued levies) —
    # do not report benefit/subsidy figures allocated from a real facility budget
    # against this fake $0 payment baseline. Same defect class fixed in
    # simulate_levy_fairness_v2 above.
    if total_rate <= 0:
        logger.warning(
            "levy_fairness_service: building %s has no current levy rate data — "
            "returning zeroed benefit/subsidy figures instead of allocating the real "
            "facility budget against a fake $0 baseline",
            building_id,
        )
        benefit_totals = {un: 0.0 for un in benefit_totals}
        benefit_shares = {un: 0.0 for un in benefit_shares}
        operating_shares = {un: 0.0 for un in operating_shares}
        capital_shares = {un: 0.0 for un in capital_shares}
        total_cost_basis = 0.0
        operating_cost_basis = 0.0
        capital_cost_basis = 0.0
        capital_items = []
        facility_costs = {}
        facility_benefit_shares = {}

    current_score, D = _compute_lbfi(payment_shares, benefit_shares)
    lbfi_interpretation = _interpret_lbfi(current_score)

    # Configured groups take precedence over inference, always. `_group_key()` remains
    # only as the fallback for a building that has not configured any — and its output is
    # a physical descriptor ("Apartment"/"Townhouse") derived from a unit_type string or a
    # UA/TH prefix, which is not a basis for a contribution argument and collapses to one
    # group for a single-form scheme.
    configured = await _configured_lot_groups(building_id)
    lot_groups = {
        u["unit_number"]: configured.get(u["unit_number"]) or _group_key(u)
        for u in units
    }
    if configured:
        _unassigned = [u["unit_number"] for u in units if u["unit_number"] not in configured]
        if _unassigned:
            # Reported, never defaulted into a group: a default here silently changes who
            # subsidises whom, and the operator is mid-configuration, not in error.
            logger.info(
                "levy_fairness: %d lot(s) not assigned to a benefit group for building %s",
                len(_unassigned), building_id,
            )
    subsidy_map = _compute_subsidy_map(
        payment_shares, benefit_shares, lot_groups, total_cost_basis,
        facility_costs, facility_benefit_shares, facility_names,
    )

    group_data: Dict[str, Dict[str, float]] = {}
    for u in units:
        group = lot_groups.get(u["unit_number"], "Other")
        group_data.setdefault(group, {"current": 0.0, "benefit": 0.0})
        group_data[group]["current"] += payment_totals.get(u["unit_number"], 0.0)
        # The unit's benefit SHARE applied to the real levy total — not its raw
        # benefit_totals figure, which carries the multi-year capital schedule and is
        # therefore not comparable to a single year's contribution. See the
        # normalisation above.
        # PER-FUND weighting. The admin levy is redistributed by who drives the
        # recurring operating cost; the sinking levy by who the capital plan is FOR.
        # A single blended share would let a large facade item wash out the fact that
        # every lift dollar belongs to one group.
        _ue = float(u.get("entitlement", 0) or 0)
        group_data[group]["benefit"] += (
            operating_shares.get(u["unit_number"], 0.0) * _admin_pool
            + capital_shares.get(u["unit_number"], 0.0) * _sinking_pool
        )
        # Fall back to the blended share when a fund has no cost base of its own,
        # so a scheme with no capital plan still gets an answer rather than a $0 row.
        if operating_cost_basis <= 0 and capital_cost_basis <= 0:
            group_data[group]["benefit"] += (
                benefit_shares.get(u["unit_number"], 0.0) * _levy_pool
                - (operating_shares.get(u["unit_number"], 0.0) * _admin_pool
                   + capital_shares.get(u["unit_number"], 0.0) * _sinking_pool)
            )
        del _ue
    impact_by_group = []
    for group, data in group_data.items():
        current_total = data["current"]
        benefit_total = data["benefit"]
        delta = benefit_total - current_total
        lots_in_group = sum(1 for g in lot_groups.values() if g == group)

        # REASONING, carried on every row.
        #
        # A number without a stated basis is what a tribunal sets aside. ACAT overturned
        # the committee decision in Lanfranchi v Units Plan 806 because it "failed to
        # properly inform itself ... based on erroneous assumptions, not supported by
        # evidence or information" — which is precisely what an unexplained levy split
        # is. So each row carries what it was computed FROM, not only the result:
        #
        #   basis        — how the benefit share was derived
        #   driver       — the physical quantity behind it, when there is one
        #   arithmetic   — the calculation in words, so it can be checked by hand
        #   editable     — whether an operator may override the amount
        #   overridden   — whether they have
        #
        # `editable` is True because benefit is a determination the owners corporation
        # makes, not something this service can derive. Rendering it as fixed would
        # present a modelling assumption as a finding.
        impact_by_group.append({
            "group": group,
            "lots": lots_in_group,
            "current_total": round(current_total, 2),
            "benefit_total": round(benefit_total, 2),
            "delta": round(delta, 2),
            "change_pct": round((delta / current_total * 100) if current_total > 0 else 0, 1),
            "reasoning": {
                "basis": "facility_benefit_share",
                "driver": "modelled access to each costed facility",
                "arithmetic": (
                    f"{lots_in_group} lot(s) in {group}: currently contribute "
                    f"${current_total:,.2f}; benefit-weighted share of the same cost base "
                    f"is ${benefit_total:,.2f}; difference ${delta:,.2f}"
                ),
                "cost_base": round(total_cost_basis, 2),
                # Named so a reader can tell "no facilities were costed" from
                # "this group genuinely benefits from nothing".
                "cost_base_source": "facilities + capital items" if total_cost_basis > 0 else None,
                "editable": True,
                "overridden": False,
            },
        })

    # The ten-year lens, computed AFTER the groups are known and reconciled against its
    # own horizon rather than against one year's levy.
    capital_outlook = _build_capital_outlook(
        capital_items, lot_groups, payment_shares,
        levy_rates.get("sinking_annual", 0), total_ue,
    )

    # Cost lines whose SHAPE admits a structural remedy. Derived from the facility's own
    # category, so it generalises: any scheme with a metered utility or an insured asset
    # gets the same option, and a scheme without one gets nothing invented for it.
    _SHAPE_BY_CATEGORY = {
        "Electrical": "metered_utility",
        "Water": "metered_utility",
        "Utilities": "metered_utility",
        "Insurance": "insured_asset",
        "Grounds": "zoned_service_contract",
        "Landscaping": "zoned_service_contract",
        "Cleaning": "zoned_service_contract",
        "Security": "access_controlled_facility",
        "Vertical Transport": "access_controlled_facility",
    }
    cost_lines = []
    for fac in facilities:
        shape = _SHAPE_BY_CATEGORY.get(fac.get("category") or "")
        if not shape:
            continue
        cost_lines.append({
            "name": fac.get("name") or fac.get("id"),
            "shape": shape,
            "cost": round(float(facility_costs.get(fac.get("id"), 0) or 0), 2),
        })
    remedies = _build_remedies(impact_by_group, capital_outlook, cost_lines)

    # Phase 1 containment: give "we could not compute this" a vocabulary.
    #
    # `insufficient_levy_data` has existed and been raised correctly for some time, but
    # nothing consumed it — the page read the numbers beside it and rendered a confident
    # $0. A boolean is also too coarse to act on: it says something is wrong without
    # saying WHAT, so a reader cannot tell a scheme with no levy rates from one with no
    # facilities. `missing_inputs` names each one so the UI can list them and an operator
    # knows what to go and enter.
    #
    # `status` is deliberately a closed vocabulary rather than a free string:
    #   ready      — every input present; the numbers mean what they say
    #   incomplete — at least one input missing; numbers are NOT shown
    # A third state, `failed`, belongs to the transport layer and is set by the caller.
    _missing_inputs: list[str] = []
    if total_rate <= 0:
        _missing_inputs.append("levy_rates")
    if not facilities:
        _missing_inputs.append("facilities")
    if total_cost_basis <= 0:
        _missing_inputs.append("facility_cost_basis")
    if capital_outlook and capital_outlook.get("unresolved_items"):
        # A dangling asset reference is not a fairness question, it is a broken record --
        # and it silently spreads real money across lots the work does not serve.
        _missing_inputs.append("capital_items_unresolved")

    # ZERO-SUM ASSERTION. A fairness model REDISTRIBUTES one cost base between owners; it
    # cannot conjure money. So the benefit-weighted totals must sum to the same figure as
    # the current contributions, within rounding.
    #
    # On East Gate today they do not, and the gap is not marginal: current contributions
    # total ~$440,375 while the benefit side totals ~$1,254,874 — nearly 3x. Every group
    # comes out "should pay more", which is arithmetically impossible for a
    # redistribution and is the signature of comparing an ANNUAL levy against a
    # MULTI-YEAR capital cost base.
    #
    # This was invisible while the page rendered each group's row in isolation. Adding
    # per-row reasoning made it visible in one read, which is the argument for the
    # reasoning: a number you cannot check is a number nobody checks.
    #
    # Reported as a missing input rather than silently corrected, because scaling the
    # benefit side to fit would manufacture a plausible answer out of a model that is
    # measuring the wrong thing — the exact failure this rebuild exists to end.
    _sum_current = sum(row["current_total"] for row in impact_by_group)
    _sum_benefit = sum(row["benefit_total"] for row in impact_by_group)
    if _sum_current > 0 and abs(_sum_benefit - _sum_current) > max(1.0, _sum_current * 0.01):
        _missing_inputs.append("zero_sum_violation")
        logger.error(
            "levy_fairness: redistribution is not zero-sum for building %s — "
            "current=%.2f benefit=%.2f (ratio %.2fx). The benefit cost base and the levy "
            "base are measuring different periods or different scopes.",
            building_id, _sum_current, _sum_benefit,
            (_sum_benefit / _sum_current) if _sum_current else 0,
        )

    result = {
        "building_id": building_id,
        "computed_at": _now(),
        "status": "incomplete" if _missing_inputs else "ready",
        "missing_inputs": _missing_inputs,
        "reconciliation": {
            "sum_current": round(_sum_current, 2),
            "sum_benefit": round(_sum_benefit, 2),
            "zero_sum": abs(_sum_benefit - _sum_current) <= max(1.0, _sum_current * 0.01),
        },
        "insufficient_levy_data": total_rate <= 0,
        # The year the levy rates came from. The page used to print "FY2026" as a
        # literal, so every scheme and every future year read as East Gate's 2026.
        "financial_year": str(latest_year) if latest_year else None,
        # The two lenses, kept apart on purpose. `impact_by_group` answers "given this
        # year's levy, who should bear what share"; `capital_outlook` answers "over the
        # life of the plan, whose property is being renewed". Blending them is what
        # produced a 2.85x cost base against a single year of contributions.
        "capital_outlook": capital_outlook,
        "cost_base": {
            "operating_annual": round(operating_cost_basis, 2),
            "capital_plan_total": round(capital_cost_basis, 2),
            "admin_pool": round(_admin_pool, 2),
            "sinking_pool": round(_sinking_pool, 2),
        },
        # Structural options, not a recommendation. Changing the contribution method is
        # listed last and flagged `last_resort` because it is the only one that needs a
        # special resolution and the only one an owner can take to ACAT.
        "remedies": remedies,
        # Per cost line, the recorded rule that drove it -- driver, values, remainder,
        # evidence reference, and whether the measurement repeats. A row rendered without
        # this is a number a committee cannot check.
        "allocation_rules": rule_reasons,
        "current_fairness_score": current_score,
        "benefit_fairness_score": 100.0,
        "impact_by_group": impact_by_group,
        "lbfi": {
            "current_score": current_score,
            "benefit_score": 100.0,
            "D": D,
            "interpretation": lbfi_interpretation,
        },
        "subsidy_map": subsidy_map,
        "sanity": subsidy_map.get("sanity", {}),
    }

    result_doc = {k: v for k, v in result.items() if k != "building_id"}
    await db.levy_fairness_results.update_one(
        {"building_id": building_id}, {"$set": result_doc}, upsert=True
    )

    await db.levy_simulations.update_one(
        {"building_id": building_id, "scenario": "current"},
        {"$set": {"scenario": "current", "computed_at": _now()}},
        upsert=True,
    )
    await db.levy_simulations.update_one(
        {"building_id": building_id, "scenario": "benefit"},
        {"$set": {"scenario": "benefit", "computed_at": _now()}},
        upsert=True,
    )

    return result


async def _derive_virtual_cost_centres(building_id: str) -> List[Dict[str, Any]]:
    """
    Derives virtual cost centres from real building_assets and facilities data.
    Uses annualised capital costs: annual_cost = replacement_cost / lifespan.

    Each asset's OWN ``benefit_group_id``, when set, decides which benefit group its
    cost rolls into — falling back to its facility's ``benefit_group_id`` only when the
    asset doesn't carry its own. This means a single facility CAN legitimately split
    across multiple cost centres if its assets are tagged to different groups (audit
    finding, 2026-08-19): the previous facility-only grouping silently allocated a
    $219,382 "Other Building Repaint" asset — tagged ``bg-all`` on the asset itself —
    entirely into its facade facility's ``bg-tower`` tag, because the facility's tag was
    the only one ever read. A facility with no linked assets still emits a single $0
    entry using its own tag, preserving the previous "every facility appears" guarantee.

    These are computed from actual asset records — NOT seeded/fake data. Test/demo
    records (``is_test_data: true``) are excluded — two assets in East Gate's live data
    were found explicitly self-labelled as demo content in their own ``notes`` field
    (a "capital shock" demo scenario and a maintenance-anomaly demo) but had never been
    flagged ``is_test_data``, so they were silently double-counting real replacement
    cost alongside the genuine asset of the same name.

    Returns [] if no facility/asset data found (caller falls back to UOE-only).
    """
    facilities = await db.facilities.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}}, {"_id": 0}
    ).to_list(500)
    assets = await db.building_assets.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}}, {"_id": 0}
    ).to_list(2000)

    if not facilities and not assets:
        return []

    facilities_by_id: Dict[str, Dict[str, Any]] = {f.get("id"): f for f in facilities}

    # Group assets by (facility_id, resolved benefit_group_id) -- not facility_id alone.
    grouped: Dict[Tuple[str, str], list] = {}
    facilities_with_assets: set = set()
    for a in assets:
        fid = a.get("facility_id")
        fac = facilities_by_id.get(fid)
        if not fid or fac is None:
            continue  # orphaned asset (no facility, or facility filtered out/missing) — excluded, not silently summed anywhere
        resolved_bg = a.get("benefit_group_id") or fac.get("benefit_group_id")
        grouped.setdefault((fid, resolved_bg), []).append(a)
        facilities_with_assets.add(fid)

    centres = []
    for (fid, bg_id), fac_assets in grouped.items():
        fac = facilities_by_id[fid]
        annual_cost = 0.0
        for a in fac_assets:
            lifespan = max(1, a.get("expected_lifespan_years", 20))
            replacement = float(a.get("replacement_cost_estimate", 0) or 0)
            annual_cost += replacement / lifespan

        centres.append({
            "facility_id": fid,
            "facility_name": fac.get("name", "Unnamed Facility"),
            "annual_cost": round(annual_cost, 2),
            "benefit_group_id": bg_id,
            "allocation_driver": "unit_entitlement",
            "enabled": True,
            "building_id": building_id,
        })

    # Facilities with no linked assets still get a single $0 entry under their own
    # tag, matching the previous behaviour where every facility always appeared.
    for fid, fac in facilities_by_id.items():
        if fid not in facilities_with_assets:
            centres.append({
                "facility_id": fid,
                "facility_name": fac.get("name", "Unnamed Facility"),
                "annual_cost": 0.0,
                "benefit_group_id": fac.get("benefit_group_id"),
                "allocation_driver": "unit_entitlement",
                "enabled": True,
                "building_id": building_id,
            })

    return centres


async def _get_levy_history(building_id: str, years: int = 6) -> List[Dict[str, Any]]:
    """
    Returns historical levy totals (GST-inclusive) for the last N years. Scoped to building.
    - Completed years: sum of unit_levy_ledger.total_levied (authoritative).
    - Current/partial year: proposed annual budget × 1.10 (GST) from annual_levies.
      The ledger only has YTD data (Q1), so we use the proposed rate as the annualised figure.
    Used by frontend to populate the Levy History chart.
    """
    gst_config = await get_building_levy_gst_settings(building_id)
    gst_multiplier = gst_config["gst_multiplier"]
    current_year = date.today().year
    history = []
    for y in range(current_year - years + 1, current_year + 1):
        if y < current_year:
            # Completed year: use authoritative ledger total (GST-inclusive)
            agg = await db.unit_levy_ledger.aggregate([
                {"$match": {"building_id": building_id, "year": str(y)}},
                {"$group": {"_id": None, "total": {"$sum": "$total_levied"}}}
            ]).to_list(1)
            if agg and agg[0].get("total", 0) > 0:
                history.append({"year": str(y), "total": round(float(agg[0]["total"]), 2)})
        else:
            # Current/partial year: use proposed annual budget (GST-inclusive)
            levy_doc = await db.annual_levies.find_one(
                {"building_id": building_id, "year": str(y)}, {"_id": 0}
            )
            if levy_doc:
                proposed_admin = float(levy_doc.get("proposed_admin_expenses") or 0)
                proposed_sinking = float(levy_doc.get("proposed_sinking_expenses") or 0)
                annual_total_incl_gst = round((proposed_admin + proposed_sinking) * gst_multiplier, 2)
                if annual_total_incl_gst > 0:
                    history.append({"year": str(y), "total": annual_total_incl_gst, "is_proposed": True})
    return history


def _compute_model_confidence(
        units: List[Dict[str, Any]],
        virtual_cost_centres: List[Dict[str, Any]],
        ledger_entries: List[Dict[str, Any]],
        benefit_groups: Dict[str, Any],
        total_asset_budget: float,
        actual_total: float,
) -> Dict[str, Any]:
    """
    Score model confidence on 4 factors (0-100 each, averaged):
    1. Levy data coverage: % units with real ledger data
    2. Asset data quality: asset budget vs levy total ratio (capped at 1)
    3. Benefit group granularity: how many non-ALL_LOTS groups exist
    4. Historical consistency: how many years of levy data available
    """
    ledger_units = {e["unit_number"] for e in ledger_entries if float(e.get("total_levied") or 0) > 0}
    levy_coverage = len(ledger_units) / max(1, len(units)) * 100

    asset_ratio = min(1.0, total_asset_budget / max(1, actual_total)) * 100 if total_asset_budget > 0 else 0.0

    specific_groups = [bg for bg in benefit_groups.values()
                       if bg.get("group_type") not in ("global",) and
                       not (bg.get("name") or "").upper().startswith("ALL")]
    group_score = min(100.0, len(specific_groups) * 25.0)  # 4+ groups = 100

    # confidence band
    score = round((levy_coverage + asset_ratio + group_score) / 3, 1)
    band = "High" if score >= 75 else "Medium" if score >= 50 else "Low"

    factors = []
    if levy_coverage < 80:
        factors.append(f"Only {round(levy_coverage)}% of units have ledger records — remainder estimated via UOE share")
    if asset_ratio < 50:
        factors.append("Asset replacement cost data covers <50% of total levy budget")
    if len(specific_groups) == 0:
        factors.append("No specific benefit groups defined — all costs allocated by UOE (pure entitlement model)")
    if not factors:
        factors.append("All data sources complete and consistent")

    return {"score": score, "band": band, "factors": factors,
            "levy_coverage_pct": round(levy_coverage, 1),
            "asset_data_score": round(asset_ratio, 1),
            "group_granularity_score": round(group_score, 1)}


def _build_distribution_histogram(
        unit_impact: List[Dict[str, Any]],
        num_buckets: int = 10,
) -> Dict[str, Any]:
    """
    Build histogram of current vs proposed levy distribution across units.
    Returns bucket boundaries and counts for before/after comparison chart.
    """
    if not unit_impact:
        return {"buckets": [], "current_counts": [], "proposed_counts": []}

    all_values = [u["current_levy"] for u in unit_impact] + [u["proposed_levy"] for u in unit_impact]
    lo = min(all_values)
    hi = max(all_values)
    if hi <= lo:
        hi = lo + 1

    step = (hi - lo) / num_buckets
    boundaries = [round(lo + i * step, 2) for i in range(num_buckets + 1)]

    def _bucket(values: List[float]) -> List[int]:
        """Generated function header.

        Function: _bucket
        Path: backend/services/levy_fairness_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        counts = [0] * num_buckets
        for v in values:
            idx = min(num_buckets - 1, int((v - lo) / step))
            counts[idx] += 1
        return counts

    current_counts = _bucket([u["current_levy"] for u in unit_impact])
    proposed_counts = _bucket([u["proposed_levy"] for u in unit_impact])

    # Summary stats
    curr = [u["current_levy"] for u in unit_impact]
    prop = [u["proposed_levy"] for u in unit_impact]
    current_stats = {"mean": round(statistics.mean(curr), 2), "median": round(statistics.median(curr), 2),
                     "stdev": round(statistics.stdev(curr) if len(curr) > 1 else 0, 2)}
    proposed_stats = {"mean": round(statistics.mean(prop), 2), "median": round(statistics.median(prop), 2),
                      "stdev": round(statistics.stdev(prop) if len(prop) > 1 else 0, 2)}

    return {
        "boundaries": boundaries,
        "current_counts": current_counts,
        "proposed_counts": proposed_counts,
        "current_stats": current_stats,
        "proposed_stats": proposed_stats,
        "equity_improvement": round(current_stats["stdev"] - proposed_stats["stdev"], 2),
    }


def _build_cross_subsidy_report(
        unit_impact: List[Dict[str, Any]],
        virtual_cost_centres: List[Dict[str, Any]],
        group_summary: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Per-facility cross-subsidy breakdown: which groups pay into vs benefit from each facility.
    Returns rows suitable for a CSV-style report.
    """
    # Per-group totals from unit_impact
    group_totals: Dict[str, Dict[str, float]] = {}
    for u in unit_impact:
        g = u["unit_type"]
        if g not in group_totals:
            group_totals[g] = {"current": 0.0, "fair": 0.0, "proposed": 0.0, "count": 0}
        group_totals[g]["current"] += u["current_levy"]
        group_totals[g]["fair"] += u["fair_levy"]
        group_totals[g]["proposed"] += u["proposed_levy"]
        group_totals[g]["count"] += 1

    total_current = sum(v["current"] for v in group_totals.values())
    total_fair = sum(v["fair"] for v in group_totals.values())

    rows = []
    for g, d in group_totals.items():
        current_share_pct = round(d["current"] / total_current * 100, 2) if total_current > 0 else 0
        fair_share_pct = round(d["fair"] / total_fair * 100, 2) if total_fair > 0 else 0
        net_subsidy = round(d["current"] - d["fair"], 2)
        rows.append({
            "group": g,
            "unit_count": d["count"],
            "current_total": round(d["current"], 2),
            "fair_total": round(d["fair"], 2),
            "proposed_total": round(d["proposed"], 2),
            "current_share_pct": current_share_pct,
            "fair_share_pct": fair_share_pct,
            "net_subsidy": net_subsidy,
            "net_subsidy_per_unit": round(net_subsidy / d["count"], 2) if d["count"] > 0 else 0,
            "role": "Contributor" if net_subsidy > 50 else "Recipient" if net_subsidy < -50 else "Neutral",
        })

    # Facility-level breakdown
    facility_rows = []
    for fac in virtual_cost_centres:
        facility_rows.append({
            "facility_name": fac.get("facility_name", "Unknown"),
            "annual_cost": round(float(fac.get("annual_cost", 0)), 2),
            "benefit_group_id": fac.get("benefit_group_id"),
            "pct_of_total": round(float(fac.get("annual_cost", 0)) / total_current * 100,
                                  2) if total_current > 0 else 0,
        })
    facility_rows.sort(key=lambda x: x["annual_cost"], reverse=True)

    return {
        "group_rows": rows,
        "facility_rows": facility_rows,
        "total_current": round(total_current, 2),
        "total_fair": round(total_fair, 2),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def simulate_levy_fairness_v2(
        building_id: str,
        max_change_percent: float = None,
        max_change_amount: float = None,
        run_monte_carlo: bool = True
) -> Dict[str, Any]:
    """
    Computes fair levy allocation using real building data only.

    Data sources (no fake/seeded collections used):
      - unit_levy_ledger: current actual levies per unit (source of truth)
      - building_assets + facilities: capital cost split by benefit group
      - benefit_groups: group definitions (APARTMENTS_ONLY, TOWNHOUSES_ONLY, ALL_LOTS)
      - units: unit entitlements and attributes
    """
    # 1. Load units
    units = await db.units.find({"building_id": building_id}, {"_id": 0}).to_list(1000)
    if not units:
        return {"error": "No units found"}

    unit_attributes = await get_unit_attributes(building_id)

    # 2. Derive virtual cost centres from REAL asset data
    # These reflect the actual capital split between apartment-specific and all-lots costs
    virtual_cost_centres = await _derive_virtual_cost_centres(building_id)

    # Load benefit groups (used to filter units per facility)
    benefit_groups_list = await db.benefit_groups.find({"building_id": building_id}, {"_id": 0}).to_list(500)
    benefit_groups = {bg["id"]: bg for bg in benefit_groups_list}

    total_asset_budget = sum(f.get("annual_cost", 0) for f in virtual_cost_centres)

    # 3. Get current levies — annual (GST-inclusive) per unit.
    # For the current/partial year the ledger only contains YTD data (one quarter).
    # We use the proposed annual rate from annual_levies so the fairness analysis
    # operates on a full-year basis.  Completed years use the ledger as authoritative.
    latest_year = await get_latest_levy_year(building_id)
    # get_latest_levy_year returns a string (e.g. "2026"); convert to int before comparing
    # to date.today().year (which is int) to avoid a TypeError on '>=' between str and int.
    latest_year_int = int(str(latest_year).split('-')[0]) if latest_year else 0
    is_current_year = (latest_year_int >= date.today().year)

    # Always fetch the ledger — _compute_model_confidence() needs it for its levy-coverage
    # metric regardless of whether this is a completed or current/partial year.
    ledger = await db.unit_levy_ledger.find(
        {"building_id": building_id, "year": str(latest_year)},
        {"_id": 0, "unit_number": 1, "total_levied": 1}
    ).to_list(1000)

    if is_current_year:
        # Current/partial year: derive annual levy per unit from normalized owner-payable rates.
        levy_rates = await get_levy_rates(str(latest_year), building_id)
        combined_rate_incl_gst = levy_rates.get("admin_annual", 0) + levy_rates.get("sinking_annual", 0)
        current_levy_map = {
            u["unit_number"]: round(combined_rate_incl_gst * float(u.get("entitlement", 1) or 1), 2)
            for u in units
        }
    else:
        # Completed year: ledger is authoritative (full year total_levied)
        current_levy_map = {
            entry["unit_number"]: float(entry["total_levied"] or 0)
            for entry in ledger
        }

    # Ensure every unit has a current levy (entitlement-share fallback for missing units)
    total_ue = sum(float(u.get("entitlement", 1) or 1) for u in units)
    actual_total = sum(current_levy_map.values())
    for u in units:
        un = u["unit_number"]
        if un not in current_levy_map:
            u_ent = float(u.get("entitlement", 1) or 1)
            current_levy_map[un] = (u_ent / total_ue) * actual_total if total_ue > 0 else 0
    actual_total = sum(current_levy_map.values())

    # 4. Scale virtual cost centres to match actual levy total
    # This preserves the PROPORTIONAL split from real assets (e.g. 37.8% apt-specific capital)
    # while ensuring totals always match the real levy data. "Regenerate" is therefore stable.
    if total_asset_budget > 1.0 and actual_total > 1.0:
        scale = actual_total / total_asset_budget
        virtual_cost_centres = [{**f, "annual_cost": f["annual_cost"] * scale} for f in virtual_cost_centres]
        total_budget = actual_total
    elif actual_total > 1.0:
        # No asset data: fall back to pure UOE allocation (all-lots)
        total_budget = actual_total
        virtual_cost_centres = [{
            "facility_id": "all-lots-common",
            "facility_name": "All Common Costs (UOE Allocation)",
            "annual_cost": actual_total,
            "benefit_group_id": None,
            "allocation_driver": "unit_entitlement",
            "building_id": building_id,
        }]
    else:
        # No real current levy data (e.g. building/year with no issued levies).
        # Do NOT proceed to allocate the real facility budget in virtual_cost_centres
        # against this fake $0 baseline — that manufactures large "underpaying" net
        # subsidies out of a real facility budget compared to nothing. Clear it so
        # every downstream benefit/subsidy figure derives honestly to zero, and flag
        # the result explicitly instead (see result["insufficient_levy_data"] below).
        logger.warning(
            "levy_fairness_service: building %s has no current levy data (actual_total=%.2f) — "
            "returning zeroed benefit/subsidy figures instead of allocating the real facility "
            "budget against a fake $0 baseline",
            building_id, actual_total,
        )
        virtual_cost_centres = []
        total_budget = 0.0
        # total_asset_budget was computed above (line ~698) from the real facility
        # budget BEFORE this branch — it is a separate variable from
        # virtual_cost_centres and is NOT reset by clearing that list. Left
        # unchanged, it still flows into _compute_model_confidence() below, whose
        # asset_ratio = min(1.0, total_asset_budget / max(1, actual_total)) * 100
        # divides a real, large budget by a fake near-zero denominator and reports
        # a spuriously maxed-out "asset_data_score" — the same class of defect
        # (real number ÷ fake $0-ish baseline) this whole branch exists to prevent,
        # just surfacing in the confidence score instead of a dollar figure.
        total_asset_budget = 0.0

    # 5. Compute fair benefit allocation per unit
    unit_benefit_map: Dict[str, float] = {u["unit_number"]: 0.0 for u in units}
    facility_allocations = []

    for fac in virtual_cost_centres:
        alloc = await calculate_facility_allocation(fac, units, benefit_groups, unit_attributes)
        facility_allocations.append({
            "facility_id": fac["facility_id"],
            "facility_name": fac["facility_name"],
            "allocation": alloc,
            "annual_cost": fac["annual_cost"],
        })
        for un, amount in alloc.items():
            unit_benefit_map[un] += amount

    # 6. Apply transition caps (optional phased rollout)
    proposed_levy_map = await apply_transition_caps(
        unit_benefit_map, current_levy_map, max_change_percent, max_change_amount
    )

    # 7. Revenue neutrality — proposed total must equal current total
    # This is a redistribution, not a reduction; every unit cannot simultaneously decrease
    proposed_total = sum(proposed_levy_map.values())
    if proposed_total > 0.01 and actual_total > 0.01:
        rn_scale = actual_total / proposed_total
        proposed_levy_map = {k: v * rn_scale for k, v in proposed_levy_map.items()}

    # 8. Compute unit-level metrics (SEI = distortion between CURRENT and BENEFIT)
    unit_impact = []
    total_abs_distortion = 0.0

    # Bulk-fetch canonical owner names once (avoids N×3 DB queries per unit)
    all_owners = await get_all_unit_owners(building_id)

    # Build per-unit facility drivers
    unit_drivers: Dict[str, List[Dict]] = {u["unit_number"]: [] for u in units}
    for fa in facility_allocations:
        for un2, amt in fa["allocation"].items():
            if amt > 0.01:
                unit_drivers[un2].append({
                    "facility": fa["facility_name"],
                    "amount": round(amt, 2),
                    "benefit_group_id": None,
                })

    for u in units:
        un = u["unit_number"]
        levy = proposed_levy_map.get(un, 0.0)
        benefit = unit_benefit_map.get(un, 0.0)
        current = current_levy_map.get(un, 0.0)

        unit_lbfi = (levy / benefit) if benefit > 0.01 else 1.0
        sei = (abs(current - benefit) / current) if current > 0.01 else 0.0
        total_abs_distortion += abs(current - benefit)

        unit_impact.append({
            "unit_number": un,
            "unit_type": _group_key(u),
            "owner_name": all_owners.get(un, {}).get("owner_name") or u.get("owner_name", "Owner"),
            "entitlement": float(u.get("entitlement", 0) or 0),
            "current_levy": round(current, 2),
            "fair_levy": round(benefit, 2),
            "proposed_levy": round(levy, 2),
            "change": round(levy - current, 2),
            "change_pct": round(((levy - current) / current * 100) if current > 0.01 else 0, 2),
            "lbfi": round(unit_lbfi, 2),
            "sei": round(sei, 4),
            "drivers": unit_drivers.get(un, []),
        })

    # 9. Scheme-level metrics
    sei_scheme = total_abs_distortion / actual_total if actual_total > 0.01 else 0
    lei_score = max(0, 100 * (1 - sei_scheme))

    # LBFI (Σ=1 normalised, via shared _compute_lbfi helper):
    _total_divisor = actual_total if actual_total > 0.01 else 1.0
    current_shares = {
        u["unit_number"]: current_levy_map.get(u["unit_number"], 0) / _total_divisor
        for u in units
    }
    benefit_shares = {
        u["unit_number"]: unit_benefit_map.get(u["unit_number"], 0) / _total_divisor
        for u in units
    }
    current_lbfi_score, D = _compute_lbfi(current_shares, benefit_shares)

    lbfi_interpretation = (
        "Near-perfect fairness under current UE model." if current_lbfi_score >= 98 else
        "Minor cross-subsidies detected." if current_lbfi_score >= 94 else
        "Moderate cross-subsidies between unit types." if current_lbfi_score >= 88 else
        "Significant cross-subsidies detected — some unit types pay for facilities they don't use."
    )

    # 10. Subsidy map by group
    group_data: Dict[str, Any] = {}
    for u in units:
        g = _group_key(u)
        un = u["unit_number"]
        if g not in group_data:
            group_data[g] = {"current": 0.0, "benefit": 0.0, "count": 0, "ue": 0.0}
        group_data[g]["current"] += current_levy_map.get(un, 0)
        group_data[g]["benefit"] += unit_benefit_map.get(un, 0)
        group_data[g]["count"] += 1
        group_data[g]["ue"] += float(u.get("entitlement", 0) or 0)

    group_summary = []
    for g, d in group_data.items():
        net = round(d["current"] - d["benefit"], 2)  # positive = overpaying
        group_summary.append({
            "group": g,
            "count": d["count"],
            "ue": round(d["ue"], 1),
            "current_total": round(d["current"], 2),
            "benefit_total": round(d["benefit"], 2),
            "net_subsidy": net,
            "change_pct": round(
                ((d["benefit"] - d["current"]) / d["current"] * 100)
                if d["current"] > 0.01 else 0, 1
            ),
            "role": "Contributor" if net > 50 else "Recipient" if net < -50 else "Neutral",
        })

    # Subsidy flows between groups
    flows = []
    contributors = [g for g in group_summary if g["net_subsidy"] > 50]
    recipients = [g for g in group_summary if g["net_subsidy"] < -50]
    total_recipient_need = sum(abs(r["net_subsidy"]) for r in recipients)
    for c in contributors:
        for r in recipients:
            if total_recipient_need > 0:
                flow_amount = c["net_subsidy"] * abs(r["net_subsidy"]) / total_recipient_need
                if flow_amount > 100:
                    flows.append({"from": c["group"], "to": r["group"], "amount": round(flow_amount, 2)})

    # 11. Monte Carlo simulation (predictive reserve risk)
    simulation_results = None
    if run_monte_carlo:
        cap_schedule = await db.capital_replacement_schedule.find(
            {"building_id": building_id}, {"_id": 0}
        ).to_list(500)
        reserve_doc = await db.financial_summary.find_one({"building_id": building_id})
        current_reserve = float(reserve_doc.get("reserve_balance", 0) if reserve_doc else 0)

        simulation_results = run_monte_carlo_levy_simulation(
            total_budget, current_reserve, cap_schedule
        )

    # 12. Levy history from real ledger data (replaces hardcoded frontend constants)
    levy_history = await _get_levy_history(building_id, years=6)

    # 13. Model confidence, distribution histogram and cross-subsidy report
    confidence = _compute_model_confidence(
        units, virtual_cost_centres, ledger, benefit_groups, total_asset_budget, actual_total
    )
    levy_distribution = _build_distribution_histogram(unit_impact)
    cross_subsidy_report = _build_cross_subsidy_report(unit_impact, virtual_cost_centres, group_summary)

    result = {
        "building_id": building_id,
        "computed_at": _now(),
        "insufficient_levy_data": actual_total <= 1.0,
        "total_budget": round(total_budget, 2),
        "lei_score": round(lei_score, 1),
        "sei_scheme": round(sei_scheme, 4),
        "unit_impact": unit_impact,
        "simulation": simulation_results,
        "levy_history": levy_history,
        "facility_breakdown": [
            {
                "facility_id": f["facility_id"],
                "facility_name": f["facility_name"],
                "annual_cost": round(f["annual_cost"], 2),
                "benefit_group_id": f.get("benefit_group_id"),
            }
            for f in virtual_cost_centres
        ],
        # LevyFairnessCard's "Top subsidy drivers" bar list reads name/amount — the
        # highest-cost facilities are the biggest levers on subsidy distortion, so
        # they're the most useful "driver" list available from this model.
        "top_drivers": sorted(
            [
                {"name": f["facility_name"], "amount": round(f["annual_cost"], 2)}
                for f in virtual_cost_centres
                if f.get("annual_cost")
            ],
            key=lambda d: d["amount"],
            reverse=True,
        )[:5],
        "lbfi": {
            "current_score": current_lbfi_score,
            "benefit_score": 100,
            "D": round(D, 4),
            "fairness_gain": round(100 - current_lbfi_score, 1),
            "interpretation": lbfi_interpretation,
        },
        "subsidy_map": {
            "flows": flows,
            "group_summary": group_summary,
            "total_cost_basis": round(actual_total, 2),
        },
        "impact_by_group": group_summary,
        "confidence": confidence,
        "levy_distribution": levy_distribution,
        "cross_subsidy_report": cross_subsidy_report,
    }

    result_v2_doc = {k: v for k, v in result.items() if k != "building_id"}
    await db.levy_fairness_results_v2.update_one(
        {"building_id": building_id}, {"$set": result_v2_doc}, upsert=True
    )
    return result


# ─── Subsidy Map ──────────────────────────────────────────────────────────────

def _categorise_facility(facility_name: str) -> str:
    """Map a free-text facility name to a canonical category slug."""
    n = facility_name.lower()
    if "lift" in n or "elevator" in n:
        return "lift"
    if "pool" in n or "swimming" in n:
        return "pool"
    if "gym" in n or "fitness" in n:
        return "gym"
    if "fire" in n:
        return "fire_system"
    if "corridor" in n or "hallway" in n or "passage" in n:
        return "corridor_cleaning"
    if "garden" in n or "landscape" in n or "ground" in n:
        return "landscaping"
    if "roof" in n:
        return "roof"
    if "car" in n or "park" in n:
        return "parking"
    if "intercom" in n or "security" in n or "cctv" in n:
        return "security"
    return "common_area"


async def compute_subsidy_map(building_id: str, financial_year: str) -> SubsidyMapResult:
    """
    Compute cross-subsidy map: which unit types over/under-pay for each facility.

    Returns SubsidyMapResult with per-facility, per-unit-type subsidy breakdown in
    integer cents. All monetary values are in cents (never floats).

    Algorithm:
      1. Derive virtual cost centres from real building_assets / facilities data
         and scale them to match actual levy totals for the requested year.
      2. For each facility × unit_type pair:
           uoe_share_cents   = facility_cost × (group_ue / total_ue)
           benefit_share_cents = facility_cost × (group_benefit / total_benefit)
           subsidy = benefit_share − uoe_share
             positive → unit type receives more benefit than it pays (being_subsidised)
             negative → unit type pays more than its benefit (paying_excess)
      3. Aggregate to building-level summary and generate key findings.
    """
    # 1. Load base data
    units = await db.units.find({"building_id": building_id}, {"_id": 0}).to_list(1000)
    if not units:
        return SubsidyMapResult(
            building_id=building_id,
            financial_year=financial_year,
            total_cross_subsidy_cents=0,
            subsidy_map=[],
            summary_by_unit_type={},
            key_findings=["No units found for this building."],
            computed_at=datetime.now(timezone.utc),
        )

    benefit_groups_list = await db.benefit_groups.find(
        {"building_id": building_id}, {"_id": 0}
    ).to_list(500)
    benefit_groups = {bg["id"]: bg for bg in benefit_groups_list}
    unit_attributes = await get_unit_attributes(building_id)

    # 2. Derive virtual cost centres (annualised from real assets)
    virtual_cost_centres = await _derive_virtual_cost_centres(building_id)

    # 3. Get actual levy total for the requested financial year
    ledger = await db.unit_levy_ledger.find(
        {"building_id": building_id, "year": str(financial_year)},
        {"_id": 0, "unit_number": 1, "total_levied": 1},
    ).to_list(1000)
    current_levy_map = {e["unit_number"]: float(e.get("total_levied") or 0) for e in ledger}
    actual_total = sum(current_levy_map.values())

    total_ue = sum(float(u.get("entitlement", 1) or 1) for u in units)

    # Fallback: no ledger for requested year — use annual_levies rates
    if actual_total < 1.0:
        levy_rates = await get_levy_rates(str(financial_year), building_id)
        rate = levy_rates.get("admin_annual", 0) + levy_rates.get("sinking_annual", 0)
        for u in units:
            current_levy_map[u["unit_number"]] = float(u.get("entitlement", 1) or 1) * rate
        actual_total = sum(current_levy_map.values())

    # 4. Scale virtual cost centres to actual levy total (preserves proportional split)
    total_asset_budget = sum(f.get("annual_cost", 0) for f in virtual_cost_centres)
    if total_asset_budget > 1.0 and actual_total > 1.0:
        scale = actual_total / total_asset_budget
        virtual_cost_centres = [
            {**f, "annual_cost": f["annual_cost"] * scale} for f in virtual_cost_centres
        ]
    elif actual_total > 1.0 and not virtual_cost_centres:
        virtual_cost_centres = [{
            "facility_id": "all-lots-common",
            "facility_name": "All Common Costs (UOE Allocation)",
            "annual_cost": actual_total,
            "benefit_group_id": None,
            "allocation_driver": "unit_entitlement",
            "building_id": building_id,
        }]

    # 5. Group units by type and collect their unit_numbers + UOE totals
    unit_type_data: Dict[str, Dict[str, Any]] = {}
    for u in units:
        ut = _group_key(u)
        if ut not in unit_type_data:
            unit_type_data[ut] = {"ue": 0.0, "unit_numbers": []}
        unit_type_data[ut]["ue"] += float(u.get("entitlement", 1) or 1)
        unit_type_data[ut]["unit_numbers"].append(u["unit_number"])

    # 6. Build per-facility, per-unit-type subsidy entries (values in cents)
    entries: List[SubsidyMapEntry] = []
    unit_type_net_cents: Dict[str, int] = {ut: 0 for ut in unit_type_data}

    for fac in virtual_cost_centres:
        annual_cost_dollars = float(fac.get("annual_cost", 0) or 0)
        if annual_cost_dollars < 0.01:
            continue
        annual_cost_cents = round(annual_cost_dollars * 100)

        # Benefit allocation: who benefits from this facility
        alloc = await calculate_facility_allocation(fac, units, benefit_groups, unit_attributes)
        total_benefit = sum(alloc.values()) or 1.0

        fac_name = fac.get("facility_name", fac["facility_id"])
        category = _categorise_facility(fac_name)

        for ut, ut_data in unit_type_data.items():
            group_benefit = sum(alloc.get(un, 0.0) for un in ut_data["unit_numbers"])
            group_ue = ut_data["ue"]

            benefit_share_cents = round((group_benefit / total_benefit) * annual_cost_cents)
            uoe_share_cents = (
                round((group_ue / total_ue) * annual_cost_cents) if total_ue > 0 else 0
            )
            subsidy_amount_cents = benefit_share_cents - uoe_share_cents

            # < $0.50 tolerance → fair
            if abs(subsidy_amount_cents) < 50:
                direction = "fair"
            elif subsidy_amount_cents > 0:
                direction = "being_subsidised"  # receives more benefit than it pays
            else:
                direction = "paying_excess"  # pays more than its benefit

            unit_type_net_cents[ut] = unit_type_net_cents.get(ut, 0) + subsidy_amount_cents

            entries.append(SubsidyMapEntry(
                unit_type=ut,
                facility_category=category,
                facility_id=fac["facility_id"],
                facility_name=fac_name,
                benefit_group=fac.get("benefit_group_id") or "ALL_LOTS",
                annual_cost_cents=annual_cost_cents,
                uoe_share_cents=uoe_share_cents,
                benefit_share_cents=benefit_share_cents,
                subsidy_amount_cents=subsidy_amount_cents,
                subsidy_direction=direction,
                affected_units=ut_data["unit_numbers"],
            ))

    # 7. Build summary by unit type
    summary_by_unit_type: Dict[str, Any] = {}
    for ut, net_cents in unit_type_net_cents.items():
        count = len(unit_type_data[ut]["unit_numbers"])
        if net_cents > 5000:  # > $50 net benefit received
            role = "being_subsidised"
        elif net_cents < -5000:  # > $50 net excess paid
            role = "paying_excess"
        else:
            role = "fair"
        summary_by_unit_type[ut] = {
            "net_subsidy_cents": net_cents,
            "avg_per_unit_cents": round(net_cents / count) if count > 0 else 0,
            "units_count": count,
            "role": role,
        }

    # 8. Total cross-subsidy = sum of excess paid by all "paying_excess" groups
    total_cross_subsidy_cents = sum(
        abs(v["net_subsidy_cents"])
        for v in summary_by_unit_type.values()
        if v["net_subsidy_cents"] < -5000
    )

    # 9. Key findings (human-readable)
    key_findings: List[str] = []
    for ut, data in sorted(summary_by_unit_type.items(), key=lambda x: x[1]["net_subsidy_cents"]):
        net_dollars = abs(data["net_subsidy_cents"]) / 100
        avg_dollars = abs(data["avg_per_unit_cents"]) / 100
        if data["role"] == "paying_excess":
            key_findings.append(
                f"{ut}s over-contribute ${net_dollars:,.0f}/year "
                f"(~${avg_dollars:,.0f}/unit/year) for facilities they don't use."
            )
        elif data["role"] == "being_subsidised":
            key_findings.append(
                f"{ut}s receive ${net_dollars:,.0f}/year more benefit than paid via UOE levy."
            )

    # Top facility driver
    paying_entries = [e for e in entries if e.subsidy_direction == "paying_excess"]
    if paying_entries:
        top = sorted(paying_entries, key=lambda x: abs(x.subsidy_amount_cents), reverse=True)[0]
        key_findings.append(
            f"Largest driver: {top.facility_name} — "
            f"${abs(top.subsidy_amount_cents) / 100:,.0f}/year cross-subsidy."
        )

    if not key_findings:
        key_findings = ["No significant cross-subsidies detected — levy allocation is near-equitable."]

    return SubsidyMapResult(
        building_id=building_id,
        financial_year=financial_year,
        total_cross_subsidy_cents=total_cross_subsidy_cents,
        subsidy_map=entries,
        summary_by_unit_type=summary_by_unit_type,
        key_findings=key_findings,
        computed_at=datetime.now(timezone.utc),
    )
