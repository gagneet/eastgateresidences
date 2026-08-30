"""
ACT Strata Market Intelligence Router
Endpoints for suburb-level strata analytics: density heatmap, clusters, TAM, true-cost, stress predictor.
Accessible to: super_admin, strata_manager
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from database import db
from models.user import UserRole
from utils.auth import get_current_user
from utils.route_guards import require_roles

router = APIRouter(prefix="/market-intelligence")

_ALLOWED_ROLES = {UserRole.SUPER_ADMIN, UserRole.STRATA_MANAGER}


# Market intelligence is platform/operator tooling, not building governance.
#
# Built with require_roles() rather than a hand-rolled guard: the role set is
# validated against UserRole at import time, and the role is resolved through
# effective_role() rather than the raw `role` field. This guard read the raw role
# until 2026-08-28 — harmless here because neither allowed role is one elevation
# grants, but the same shape 403'd elevated EC members elsewhere, so the pattern
# does not stay in the tree.
_require_market_intel_role = require_roles(
    *_ALLOWED_ROLES,
    detail="Super Admin or Strata Manager role required",
)


@router.get("/suburbs")
async def list_suburbs(
        district: Optional[str] = None,
        min_strata_pct: Optional[float] = None,
        growth_pressure: Optional[str] = None,
        sort_by: str = "strata_dwellings",
        order: int = -1,
        limit: int = Query(default=120, le=200),
        current_user: dict = Depends(_require_market_intel_role),
):
    """All ACT suburbs with market intelligence data."""
    flt: dict = {}
    if district:        flt["district"] = district
    if growth_pressure: flt["growth_pressure"] = growth_pressure
    if min_strata_pct is not None:
        flt["strata_pct"] = {"$gte": min_strata_pct}

    cursor = db.act_suburbs.find(flt, {"_id": 0}).sort(sort_by, order).limit(limit)
    return await cursor.to_list(length=limit)


@router.get("/districts")
async def get_districts(
        current_user: dict = Depends(_require_market_intel_role),
):
    """District-level aggregated strata statistics."""
    pipeline = [
        {"$match": {"suburb": {"$ne": ""}}},
        {"$group": {
            "_id": "$district",
            "suburbs": {"$sum": 1},
            "total_strata": {"$sum": "$strata_dwellings"},
            "total_dwellings": {"$sum": "$total_dwellings"},
            "total_buildings": {"$sum": "$strata_buildings"},
            "avg_building_size": {"$avg": "$avg_strata_building_size"},
            "avg_density_score": {"$avg": "$density_score"},
            "tam_total": {"$sum": "$tam_estimate"},
        }},
        {"$addFields": {
            "strata_pct": {
                "$cond": [
                    {"$gt": ["$total_dwellings", 0]},
                    {"$divide": ["$total_strata", "$total_dwellings"]},
                    0
                ]
            }
        }},
        {"$sort": {"total_strata": -1}},
    ]
    result = await db.act_suburbs.aggregate(pipeline).to_list(length=50)
    for r in result:
        r["district"] = r.pop("_id")
    return result


@router.get("/heatmap")
async def get_heatmap(
        current_user: dict = Depends(_require_market_intel_role),
):
    """Strata density heatmap data — all suburbs with coordinates proxies."""
    cursor = db.act_suburbs.find(
        {"strata_dwellings": {"$gt": 0}},
        {"_id": 0, "suburb": 1, "district": 1, "postcode": 1,
         "strata_dwellings": 1, "strata_pct": 1, "strata_buildings": 1,
         "density_score": 1, "units_concentration": 1, "growth_pressure": 1,
         "likelihood_score": 1}
    ).sort("density_score", -1)
    suburbs = await cursor.to_list(length=200)

    # Compute quintile bands for choropleth
    scores = [s["density_score"] for s in suburbs if s.get("density_score") is not None]
    if scores:
        scores_sorted = sorted(scores)
        n = len(scores_sorted)
        q1 = scores_sorted[n // 5]
        q2 = scores_sorted[2 * n // 5]
        q3 = scores_sorted[3 * n // 5]
        q4 = scores_sorted[4 * n // 5]
        for s in suburbs:
            ds = s.get("density_score", 0)
            if ds >= q4:
                s["band"] = "Very High"
            elif ds >= q3:
                s["band"] = "High"
            elif ds >= q2:
                s["band"] = "Moderate"
            elif ds >= q1:
                s["band"] = "Low"
            else:
                s["band"] = "Very Low"

    return {"suburbs": suburbs, "total": len(suburbs)}


@router.get("/clusters")
async def get_clusters(
        top_n: int = Query(default=20, le=50),
        current_user: dict = Depends(_require_market_intel_role),
):
    """Largest strata clusters by total strata dwellings and building count."""
    cursor = db.act_suburbs.find(
        {"strata_buildings": {"$gt": 5}},
        {"_id": 0, "suburb": 1, "district": 1, "postcode": 1,
         "strata_dwellings": 1, "strata_buildings": 1, "avg_strata_building_size": 1,
         "strata_pct": 1, "units_concentration": 1, "likelihood_score": 1,
         "tam_estimate": 1, "growth_pressure": 1, "housing_character": 1}
    ).sort("strata_dwellings", -1).limit(top_n)
    return await cursor.to_list(length=top_n)


@router.get("/tam")
async def get_tam(
        current_user: dict = Depends(_require_market_intel_role),
):
    """Total Addressable Market analysis — ACT wide and per district."""
    # ACT-wide
    pipeline_total = [
        {"$match": {"suburb": {"$ne": ""}}},
        {"$group": {
            "_id": None,
            "total_strata_dwellings": {"$sum": "$strata_dwellings"},
            "total_strata_buildings": {"$sum": "$strata_buildings"},
            "total_suburbs": {"$sum": 1},
            "tam_gross": {"$sum": "$tam_estimate"},
        }}
    ]
    total = await db.act_suburbs.aggregate(pipeline_total).to_list(length=1)
    total_doc = total[0] if total else {}
    total_doc.pop("_id", None)

    # Per district breakdown
    pipeline_district = [
        {"$match": {"suburb": {"$ne": ""}}},
        {"$group": {
            "_id": "$district",
            "strata_buildings": {"$sum": "$strata_buildings"},
            "strata_dwellings": {"$sum": "$strata_dwellings"},
            "tam_estimate": {"$sum": "$tam_estimate"},
            "suburbs": {"$sum": 1},
        }},
        {"$sort": {"tam_estimate": -1}},
    ]
    districts = await db.act_suburbs.aggregate(pipeline_district).to_list(length=20)
    for d in districts:
        d["district"] = d.pop("_id")

    # Top high-impact suburbs (TAM leaders)
    cursor = db.act_suburbs.find(
        {"strata_buildings": {"$gte": 10}},
        {"_id": 0, "suburb": 1, "district": 1, "strata_buildings": 1,
         "strata_dwellings": 1, "tam_estimate": 1, "likelihood_score": 1}
    ).sort("tam_estimate", -1).limit(10)
    top_suburbs = await cursor.to_list(length=10)

    # Assumptions note
    assumptions = {
        "management_fee_per_building_pa": 80000,
        "platform_fee_per_unit_pa": 120,
        "description": "TAM = strata_buildings × $80k/yr management fee. Platform TAM = strata_dwellings × $120/yr."
    }
    platform_tam = total_doc.get("total_strata_dwellings", 0) * 120

    return {
        "totals": {**total_doc, "platform_tam": platform_tam},
        "by_district": districts,
        "top_suburbs": top_suburbs,
        "assumptions": assumptions,
    }


@router.get("/true-cost")
async def get_true_cost(
        current_user: dict = Depends(_require_market_intel_role),
):
    """True Cost of Ownership analysis by suburb — levy risk, maintenance risk, capital shock."""
    cursor = db.act_suburbs.find(
        {"strata_dwellings": {"$gt": 0}},
        {"_id": 0, "suburb": 1, "district": 1, "postcode": 1,
         "strata_pct": 1, "strata_dwellings": 1, "avg_strata_building_size": 1,
         "indicative_unit_price": 1, "gross_yield": 1, "vacancy_risk": 1,
         "investor_share": 1, "growth_pressure": 1, "urban_type": 1,
         "likelihood_score": 1, "density_score": 1, "housing_character": 1}
    ).sort("strata_dwellings", -1)
    suburbs = await cursor.to_list(length=200)

    def levy_risk(s):
        """Generated function header.

        Function: levy_risk
        Path: backend/routers/market_intelligence.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        sp = s.get("strata_pct", 0) or 0
        gp = s.get("growth_pressure", "")
        if sp >= 0.7 and gp == "High":  return "High"
        if sp >= 0.5 or gp == "High":   return "Moderate"
        return "Low"

    def maintenance_risk(s):
        """Generated function header.

        Function: maintenance_risk
        Path: backend/routers/market_intelligence.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        ut = s.get("urban_type", "")
        avg = s.get("avg_strata_building_size", 0) or 0
        if "Town Centre" in ut and avg >= 50:   return "High"
        if "Inner Urban" in ut and avg >= 20:   return "Moderate"
        return "Low"

    def capital_shock_risk(s):
        """Generated function header.

        Function: capital_shock_risk
        Path: backend/routers/market_intelligence.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        avg = s.get("avg_strata_building_size", 0) or 0
        sp = s.get("strata_pct", 0) or 0
        if avg >= 90 and sp >= 0.7:  return "Moderate"
        if avg >= 50 and sp >= 0.5:  return "Low-Moderate"
        return "Low"

    result = []
    for s in suburbs:
        result.append({
            "suburb": s["suburb"],
            "district": s.get("district"),
            "postcode": s.get("postcode"),
            "avg_levies": levy_risk(s),
            "maintenance_risk": maintenance_risk(s),
            "capital_shock_risk": capital_shock_risk(s),
            "indicative_unit_price": s.get("indicative_unit_price"),
            "gross_yield": s.get("gross_yield"),
            "vacancy_risk": s.get("vacancy_risk"),
            "investor_share": s.get("investor_share"),
            "growth_pressure": s.get("growth_pressure"),
            "strata_pct": s.get("strata_pct"),
        })
    return result


@router.get("/stress-predictor")
async def get_stress_predictor(
        current_user: dict = Depends(_require_market_intel_role),
):
    """
    Strata Financial Stress Predictor — identifies suburbs likely to need special levies,
    have underfunded sinking funds, or high capital expenditure risk.
    """
    cursor = db.act_suburbs.find(
        {"strata_dwellings": {"$gt": 0}},
        {"_id": 0, "suburb": 1, "district": 1, "strata_pct": 1,
         "avg_strata_building_size": 1, "strata_buildings": 1,
         "investor_share": 1, "vacancy_risk": 1, "growth_pressure": 1,
         "urban_type": 1, "density_score": 1, "likelihood_score": 1}
    ).sort("density_score", -1)
    suburbs = await cursor.to_list(length=200)

    def special_levy_risk(s):
        """Generated function header.

        Function: special_levy_risk
        Path: backend/routers/market_intelligence.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        sp = s.get("strata_pct", 0) or 0
        avg = s.get("avg_strata_building_size", 0) or 0
        inv = s.get("investor_share", 0) or 0
        # High investor share + large buildings = special levy pressure
        score = (sp * 40) + (min(avg, 100) / 100 * 30) + (inv * 30)
        if score >= 65: return "High"
        if score >= 40: return "Moderate"
        return "Low"

    def sinking_fund_risk(s):
        """Generated function header.

        Function: sinking_fund_risk
        Path: backend/routers/market_intelligence.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        avg = s.get("avg_strata_building_size", 0) or 0
        vac = s.get("vacancy_risk", "")
        # Larger buildings with high vacancy = underfunded sinking fund risk
        if avg >= 80 and vac in ("High", "Moderate"): return "High"
        if avg >= 40 and vac == "Moderate":            return "Moderate"
        return "Low"

    def capex_risk(s):
        """Generated function header.

        Function: capex_risk
        Path: backend/routers/market_intelligence.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        ut = s.get("urban_type", "")
        gp = s.get("growth_pressure", "")
        avg = s.get("avg_strata_building_size", 0) or 0
        if "Town Centre" in ut and avg >= 80:   return "High"
        if gp == "High" and avg >= 40:          return "Moderate"
        return "Low"

    result = []
    for s in suburbs:
        slr = special_levy_risk(s)
        sfr = sinking_fund_risk(s)
        cxr = capex_risk(s)
        # Overall stress score
        risk_map = {"High": 3, "Moderate": 2, "Low-Moderate": 1, "Low": 0}
        stress = risk_map.get(slr, 0) + risk_map.get(sfr, 0) + risk_map.get(cxr, 0)
        if stress >= 7:
            overall = "Critical"
        elif stress >= 5:
            overall = "High"
        elif stress >= 3:
            overall = "Moderate"
        elif stress >= 1:
            overall = "Low"
        else:
            overall = "Stable"

        result.append({
            "suburb": s["suburb"],
            "district": s.get("district"),
            "special_levy_risk": slr,
            "sinking_fund_risk": sfr,
            "capex_risk": cxr,
            "overall_stress": overall,
            "density_score": s.get("density_score"),
            "strata_buildings": s.get("strata_buildings"),
        })
    _order = ["Stable", "Low", "Moderate", "High", "Critical"]
    result.sort(key=lambda x: -_order.index(x["overall_stress"]) if x["overall_stress"] in _order else 0)
    return result


@router.get("/growth-map")
async def get_growth_map(
        current_user: dict = Depends(_require_market_intel_role),
):
    """Platform growth strategy — high-impact strata clusters for expansion."""
    cursor = db.act_suburbs.find(
        {"growth_pressure": "High", "strata_buildings": {"$gte": 5}},
        {"_id": 0, "suburb": 1, "district": 1, "strata_buildings": 1,
         "strata_dwellings": 1, "likelihood_score": 1, "tam_estimate": 1,
         "units_concentration": 1, "housing_character": 1}
    ).sort("strata_dwellings", -1).limit(20)
    high_growth = await cursor.to_list(length=20)

    # Top 5 districts by growth potential
    pipeline = [
        {"$match": {"growth_pressure": {"$in": ["High", "Moderate"]}}},
        {"$group": {
            "_id": "$district",
            "high_growth_suburbs": {"$sum": {"$cond": [{"$eq": ["$growth_pressure", "High"]}, 1, 0]}},
            "strata_buildings": {"$sum": "$strata_buildings"},
            "strata_dwellings": {"$sum": "$strata_dwellings"},
            "tam_estimate": {"$sum": "$tam_estimate"},
            "avg_likelihood": {"$avg": "$likelihood_score"},
        }},
        {"$sort": {"high_growth_suburbs": -1, "strata_buildings": -1}},
        {"$limit": 6},
    ]
    districts = await db.act_suburbs.aggregate(pipeline).to_list(length=6)
    for d in districts:
        d["district"] = d.pop("_id")

    return {"high_growth_suburbs": high_growth, "top_districts": districts}
