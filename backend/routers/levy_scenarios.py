"""
Levy Scenario Modeller — API router.

Prefix: /api/levy-scenarios
Tags: Levy Scenarios

Roles allowed:
  - View: owner, ec_member, chairman, strata_manager, super_admin
  - Manage (create/edit/compute): owner, ec_member, chairman, strata_manager, super_admin
  - Apply UOE (permanent change): chairman, strata_manager, super_admin
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from typing import Optional

from database import db
from models.levy_scenarios import (
    LevyScenarioCreate,
    LevyScenarioUpdate,
    ScenarioStatus,
)
from routers.auth import get_current_user
from utils.auth import effective_role
from utils.finance_helpers import get_latest_levy_year, get_levy_proposed_amounts
from services.gst_service import get_building_levy_gst_settings
from services.levy_scenario_service import (
    ScenarioInputs,
    compute_scenario,
    resolve_units_to_groups,
)

router = APIRouter(prefix="/levy-scenarios", tags=["Levy Scenarios"])

# ─────────────────────────────────────────────────────────────────────────────
# Role sets
# ─────────────────────────────────────────────────────────────────────────────

SCENARIO_VIEW_ROLES = {"owner", "ec_member", "strata_admin", "strata_manager", "super_admin"}
SCENARIO_MANAGE_ROLES = {"owner", "ec_member", "strata_admin", "strata_manager", "super_admin"}
SCENARIO_APPLY_UOE_ROLES = {"ec_member", "strata_admin", "strata_manager", "super_admin"}


def _require_view(user: dict) -> dict:
    """Generated function header.

    Function: _require_view
    Path: backend/routers/levy_scenarios.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(user) not in SCENARIO_VIEW_ROLES:
        raise HTTPException(status_code=403, detail="View permission required.")
    return user


def _require_manage(user: dict) -> dict:
    """Generated function header.

    Function: _require_manage
    Path: backend/routers/levy_scenarios.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(user) not in SCENARIO_MANAGE_ROLES:
        raise HTTPException(status_code=403, detail="Manage permission required.")
    return user


def _require_apply_uoe(user: dict) -> dict:
    """Generated function header.

    Function: _require_apply_uoe
    Path: backend/routers/levy_scenarios.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(user) not in SCENARIO_APPLY_UOE_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Applying UOE overrides requires chairman, strata_manager, or super_admin role.",
        )
    return user


def _get_building_id(user: dict) -> str:
    """Generated function header.

    Function: _get_building_id
    Path: backend/routers/levy_scenarios.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    bid = user.get("building_id") or user.get("plan_id") or ""
    if not bid:
        raise HTTPException(status_code=400, detail="building_id missing from token.")
    return str(bid)


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/routers/levy_scenarios.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _oid(id_str: str) -> ObjectId:
    """Generated function header.

    Function: _oid
    Path: backend/routers/levy_scenarios.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=422, detail=f"Invalid id: {id_str}")


# ─────────────────────────────────────────────────────────────────────────────
# Helper: format scenario doc for API response
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(doc: dict) -> dict:
    """Generated function header.

    Function: _fmt
    Path: backend/routers/levy_scenarios.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc["id"] = str(doc.pop("_id"))
    doc["building_id"] = str(doc.get("building_id", ""))
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# Helper: fetch current levy rates for a building/year
# ─────────────────────────────────────────────────────────────────────────────

async def _get_current_levy_rates(building_id: str, financial_year: str) -> dict:
    """
    Returns {current_admin_rate_per_uoe, current_sinking_rate_per_uoe,
             total_admin_budget, total_sinking_budget, total_uoe}
    from the levies collection for the given year.
    Falls back to zeros if no levy record found.
    """
    # GAP-FIN-065 Item B: `db.levies` is a LEGACY collection — nothing in the current
    # codebase writes it (every levy writer targets `annual_levies` /
    # `historical_annual_levies`), so for any building onboarded through the current
    # pipeline it is empty and this lookup misses. When it does carry legacy rows their
    # GST basis is unverifiable, so we must NOT blindly apply a GST multiplier to
    # `total_levy` — it is read as-is. The authoritative source is `annual_levies`, from
    # which we derive the OWNER-PAYABLE (inc-GST) budget = ex-GST proposed × gst_multiplier
    # (the established convention; see finance_helpers.get_levy_proposed_amounts + the GST
    # rule in onboarding doc 04 §6). Scenario deltas are GST-invariant; only these absolute
    # "current" figures are affected, and they now match every other finance surface.
    levy = await db.levies.find_one({"building_id": building_id, "year": financial_year})
    if levy:
        total_uoe = float(levy.get("total_uoe") or 0.0) or 1.0
        admin_annual = float(levy.get("admin_fund", {}).get("total_levy", 0) or 0)
        sinking_annual = float(levy.get("sinking_fund", {}).get("total_levy", 0) or 0)
        return {
            "total_admin_budget": admin_annual,
            "total_sinking_budget": sinking_annual,
            "current_admin_rate_per_uoe": admin_annual / total_uoe,
            "current_sinking_rate_per_uoe": sinking_annual / total_uoe,
            "total_uoe": total_uoe,
            "source": "levies_legacy",
        }

    annual = await _get_annual_levy_budget(building_id, financial_year)
    if annual:
        return annual

    return {
        "total_admin_budget": 0.0,
        "total_sinking_budget": 0.0,
        "current_admin_rate_per_uoe": 0.0,
        "current_sinking_rate_per_uoe": 0.0,
        "total_uoe": 0.0,
        "source": "empty",
    }


async def _get_annual_levy_budget(building_id: str, financial_year: str) -> Optional[dict]:
    """Owner-payable (inc-GST) current-levy budget from the authoritative `annual_levies`.

    Resolves the annual_levies doc for the scenario year (which is stored as a single
    calendar year like "2026", while the scenario passes "2026-2027"), then converts the
    ex-GST proposed fund totals to the inc-GST payable basis with the building's
    gst_multiplier. Returns None if no annual_levies doc can be resolved.
    """
    year_key = (financial_year or "").split("-")[0].strip()
    levy_doc = None
    if year_key:
        levy_doc = await db.annual_levies.find_one({"building_id": building_id, "year": year_key}, {"_id": 0})
    if not levy_doc:
        latest = await get_latest_levy_year(building_id)
        if latest:
            levy_doc = await db.annual_levies.find_one(
                {"building_id": building_id, "year": str(latest)}, {"_id": 0}
            )
    if not levy_doc:
        return None

    total_uoe = float(levy_doc.get("total_uoe") or 0.0) or 1.0
    admin_ex, sinking_ex = get_levy_proposed_amounts(levy_doc, int(total_uoe))
    gst_multiplier = (await get_building_levy_gst_settings(building_id)).get("gst_multiplier", 1.0)
    admin_annual = float(admin_ex) * gst_multiplier
    sinking_annual = float(sinking_ex) * gst_multiplier
    return {
        "total_admin_budget": admin_annual,
        "total_sinking_budget": sinking_annual,
        "current_admin_rate_per_uoe": admin_annual / total_uoe,
        "current_sinking_rate_per_uoe": sinking_annual / total_uoe,
        "total_uoe": total_uoe,
        "source": "annual_levies_inc_gst",
    }


async def _get_unit_current_levies(building_id: str, financial_year: str, rates: Optional[dict] = None) -> dict:
    """
    Returns {unit_number: {current_admin_levy, current_sinking_levy}}
    from the unit_levy_ledger or computed from levy rates × entitlement.
    """
    units_task = db.units.find({"building_id": building_id}, {"unit_number": 1, "entitlement": 1}).to_list(1000)

    if rates is None:
        rates_task = _get_current_levy_rates(building_id, financial_year)
        rates, units = await asyncio.gather(rates_task, units_task)
    else:
        units = await units_task
    admin_rate = rates["current_admin_rate_per_uoe"]
    sinking_rate = rates["current_sinking_rate_per_uoe"]

    result = {}
    for unit in units:
        unum = unit["unit_number"]
        ue = float(unit.get("entitlement") or 0.0)
        result[unum] = {
            "current_admin_levy": admin_rate * ue,
            "current_sinking_levy": sinking_rate * ue,
        }
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/base-data")
async def get_base_data(
        financial_year: Optional[str] = Query(None, description="e.g. '2026-2027'"),
        current_user: dict = Depends(get_current_user),
):
    """
    Returns current building levy data to pre-populate a new scenario:
    units list with entitlements, current budgets, and available financial years.
    """
    _require_view(current_user)
    building_id = _get_building_id(current_user)

    # Determine target year
    if not financial_year:
        now_year = datetime.now().year
        financial_year = f"{now_year}-{now_year + 1}"

    # Performance Optimization⚡: Parallelize rate lookup and unit retrieval.
    rates_task = _get_current_levy_rates(building_id, financial_year)
    units_task = db.units.find(
        {"building_id": building_id},
        {"unit_number": 1, "entitlement": 1, "unit_type": 1, "scheme_class": 1},
    ).to_list(length=1000)

    rates, units_raw = await asyncio.gather(rates_task, units_task)

    units = [
        {
            "unit_number": u["unit_number"],
            "entitlement": float(u.get("entitlement") or 0.0),
            "unit_type": u.get("unit_type", ""),
            "scheme_class": u.get("scheme_class"),
        }
        for u in units_raw
    ]

    total_uoe = sum(u["entitlement"] for u in units)

    # Available years (from levies collection)
    years_cursor = await db.levies.distinct("year", {"building_id": building_id})
    available_years = sorted(years_cursor, reverse=True)

    return JSONResponse(content={"success": True, "data": {
        "building_id": building_id,
        "financial_year": financial_year,
        "total_admin_budget": rates["total_admin_budget"],
        "total_sinking_budget": rates["total_sinking_budget"],
        "current_admin_rate_per_uoe": rates["current_admin_rate_per_uoe"],
        "current_sinking_rate_per_uoe": rates["current_sinking_rate_per_uoe"],
        "total_uoe": total_uoe,
        "unit_count": len(units),
        "units": units,
        "available_years": available_years,
    }})


@router.get("")
async def list_scenarios(
        financial_year: Optional[str] = Query(None),
        limit: int = Query(50, le=200),
        skip: int = Query(0),
        current_user: dict = Depends(get_current_user),
):
    """List all scenarios for the building, newest first."""
    _require_view(current_user)
    building_id = _get_building_id(current_user)

    query: dict = {"building_id": building_id}
    if financial_year:
        query["financial_year"] = financial_year

    total = await db.levy_scenarios.count_documents(query)
    cursor = db.levy_scenarios.find(query).sort("created_at", -1).skip(skip).limit(limit)
    docs = await cursor.to_list(length=limit)

    items = []
    for doc in docs:
        d = _fmt(doc)
        # Strip unit_results from list to keep payload small
        if d.get("last_result"):
            d["last_result"] = {
                k: v for k, v in d["last_result"].items()
                if k != "unit_results"
            }
        items.append(d)

    return JSONResponse(content={"success": True, "data": {
        "total": total,
        "items": items,
    }})


@router.post("", status_code=201)
async def create_scenario(
        payload: LevyScenarioCreate,
        current_user: dict = Depends(get_current_user),
):
    """Create a new levy scenario."""
    _require_manage(current_user)
    building_id = _get_building_id(current_user)
    now = _now()
    performed_by = str(current_user.get("_id", ""))

    doc = {
        "building_id": building_id,
        "scenario_name": payload.scenario_name,
        "description": payload.description,
        "financial_year": payload.financial_year,
        "total_admin_budget": payload.total_admin_budget,
        "total_sinking_budget": payload.total_sinking_budget,
        "groups": [g.model_dump() for g in payload.groups],
        "status": ScenarioStatus.DRAFT.value,
        "last_result": None,
        "published_at": None,
        "published_by": None,
        "created_by": performed_by,
        "created_at": now,
        "updated_at": now,
    }
    res = await db.levy_scenarios.insert_one(doc)
    doc["_id"] = res.inserted_id
    return JSONResponse(content={"success": True, "data": _fmt(doc)}, status_code=201)


@router.get("/{scenario_id}")
async def get_scenario(
        scenario_id: str,
        current_user: dict = Depends(get_current_user),
):
    """Get a single scenario by ID, including full unit_results."""
    _require_view(current_user)
    building_id = _get_building_id(current_user)

    doc = await db.levy_scenarios.find_one({"_id": _oid(scenario_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    if str(doc.get("building_id")) != building_id and effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Scenario belongs to a different building.")

    return JSONResponse(content={"success": True, "data": _fmt(doc)})


@router.put("/{scenario_id}")
async def update_scenario(
        scenario_id: str,
        payload: LevyScenarioUpdate,
        current_user: dict = Depends(get_current_user),
):
    """Update scenario name, description, budgets, or group configuration."""
    _require_manage(current_user)
    building_id = _get_building_id(current_user)

    doc = await db.levy_scenarios.find_one({"_id": _oid(scenario_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    if str(doc.get("building_id")) != building_id and effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Scenario belongs to a different building.")
    if doc.get("status") == ScenarioStatus.PUBLISHED.value and effective_role(
            current_user) not in SCENARIO_APPLY_UOE_ROLES:
        raise HTTPException(status_code=409,
                            detail="Published scenarios can only be modified by chairman or strata_manager.")

    update_data: dict = {"updated_at": _now(), "status": ScenarioStatus.DRAFT.value}
    if payload.scenario_name is not None:
        update_data["scenario_name"] = payload.scenario_name
    if payload.description is not None:
        update_data["description"] = payload.description
    if payload.total_admin_budget is not None:
        update_data["total_admin_budget"] = payload.total_admin_budget
    if payload.total_sinking_budget is not None:
        update_data["total_sinking_budget"] = payload.total_sinking_budget
    if payload.groups is not None:
        update_data["groups"] = [g.model_dump() for g in payload.groups]
        update_data["last_result"] = None  # invalidate cached result

    await db.levy_scenarios.update_one({"_id": _oid(scenario_id)}, {"$set": update_data})
    updated = await db.levy_scenarios.find_one({"_id": _oid(scenario_id)})
    return JSONResponse(content={"success": True, "data": _fmt(updated)})


@router.delete("/{scenario_id}", status_code=204)
async def delete_scenario(
        scenario_id: str,
        current_user: dict = Depends(get_current_user),
):
    """Delete a scenario (manage roles only)."""
    _require_manage(current_user)
    building_id = _get_building_id(current_user)

    doc = await db.levy_scenarios.find_one({"_id": _oid(scenario_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    if str(doc.get("building_id")) != building_id and effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Scenario belongs to a different building.")

    await db.levy_scenarios.delete_one({"_id": _oid(scenario_id)})
    return Response(status_code=204)


@router.post("/{scenario_id}/clone", status_code=201)
async def clone_scenario(
        scenario_id: str,
        current_user: dict = Depends(get_current_user),
):
    """Clone a scenario as a new draft."""
    _require_manage(current_user)
    building_id = _get_building_id(current_user)

    doc = await db.levy_scenarios.find_one({"_id": _oid(scenario_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    if str(doc.get("building_id")) != building_id and effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Scenario belongs to a different building.")

    now = _now()
    clone = {
        k: v for k, v in doc.items()
        if k not in ("_id", "published_at", "published_by", "last_result")
    }
    clone.update({
        "scenario_name": f"{doc['scenario_name']} (copy)",
        "status": ScenarioStatus.DRAFT.value,
        "last_result": None,
        "published_at": None,
        "published_by": None,
        "created_by": str(current_user.get("_id", "")),
        "created_at": now,
        "updated_at": now,
    })
    res = await db.levy_scenarios.insert_one(clone)
    clone["_id"] = res.inserted_id
    return JSONResponse(content={"success": True, "data": _fmt(clone)}, status_code=201)


@router.post("/{scenario_id}/compute")
async def compute_scenario_endpoint(
        scenario_id: str,
        current_user: dict = Depends(get_current_user),
):
    """
    Run the levy computation for a scenario.
    Fetches current building unit data and levy rates, resolves groups, and
    computes per-unit levy amounts. Result is cached on the scenario document.
    """
    _require_manage(current_user)
    building_id = _get_building_id(current_user)

    doc = await db.levy_scenarios.find_one({"_id": _oid(scenario_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    if str(doc.get("building_id")) != building_id and effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Scenario belongs to a different building.")

    financial_year = doc["financial_year"]

    # Performance Optimization⚡: Parallelize data fetching for computation.
    # We fetch units and rates concurrently, then compute current levies in-memory
    # to eliminate redundant database trips for the same unit data.
    units_task = db.units.find(
        {"building_id": building_id},
        {"unit_number": 1, "entitlement": 1, "unit_type": 1},
    ).to_list(length=1000)
    rates_task = _get_current_levy_rates(building_id, financial_year)

    units_raw, rates = await asyncio.gather(units_task, rates_task)

    # Compute current levies in-memory from pre-fetched data
    admin_rate = rates["current_admin_rate_per_uoe"]
    sinking_rate = rates["current_sinking_rate_per_uoe"]
    current_levies = {
        u["unit_number"]: {
            "current_admin_levy": admin_rate * float(u.get("entitlement") or 0.0),
            "current_sinking_levy": sinking_rate * float(u.get("entitlement") or 0.0),
        }
        for u in units_raw
    }

    # Resolve groups
    groups_config = doc.get("groups", [])
    groups = resolve_units_to_groups(
        groups_config=groups_config,
        all_units=units_raw,
        uoe_overrides_by_group={},
        current_levy_by_unit=current_levies,
    )

    # Identify any units not covered by any group
    assigned_units = {u.unit_number for g in groups for u in g.units}
    all_unit_numbers = {u["unit_number"] for u in units_raw}
    unassigned = all_unit_numbers - assigned_units
    if unassigned:
        # Non-fatal: return warning so the UI can alert the user
        pass

    inputs = ScenarioInputs(
        total_admin_budget=doc.get("total_admin_budget", 0.0),
        total_sinking_budget=doc.get("total_sinking_budget", 0.0),
        groups=groups,
        current_admin_rate_per_uoe=rates["current_admin_rate_per_uoe"],
        current_sinking_rate_per_uoe=rates["current_sinking_rate_per_uoe"],
    )

    result = compute_scenario(inputs)
    result["unassigned_units"] = sorted(unassigned)
    if unassigned:
        result["warning"] = (
                f"{len(unassigned)} unit(s) not assigned to any group and will show "
                "zero levy in this scenario: " + ", ".join(sorted(unassigned))
        )

    # Cache result and mark as computed
    await db.levy_scenarios.update_one(
        {"_id": _oid(scenario_id)},
        {"$set": {
            "last_result": result,
            "status": ScenarioStatus.COMPUTED.value,
            "updated_at": _now(),
        }},
    )

    return JSONResponse(content={"success": True, "data": result})


@router.post("/{scenario_id}/apply-uoe")
async def apply_uoe_overrides(
        scenario_id: str,
        current_user: dict = Depends(get_current_user),
):
    """
    Apply UOE overrides from this scenario to the units collection.

    IMPORTANT: This is a permanent change. Validates that the total UOE
    is preserved (within 0.01 tolerance) before applying.
    Only chairman, strata_manager, and super_admin may call this endpoint.
    """
    _require_apply_uoe(current_user)
    building_id = _get_building_id(current_user)

    doc = await db.levy_scenarios.find_one({"_id": _oid(scenario_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    if str(doc.get("building_id")) != building_id and effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Scenario belongs to a different building.")

    last_result = doc.get("last_result")
    if not last_result:
        raise HTTPException(
            status_code=422,
            detail="Scenario must be computed first. Call /compute before /apply-uoe.",
        )

    if not last_result.get("uoe_total_preserved", False):
        uoe_diff = last_result.get("uoe_diff", "unknown")
        raise HTTPException(
            status_code=422,
            detail=f"UOE total is not preserved (diff={uoe_diff}). "
                   "Adjust overrides so total UOE across all groups equals the original.",
        )

    # Build update set from unit_results
    updates = []
    for ur in last_result.get("unit_results", []):
        if ur.get("uoe_changed"):
            updates.append((ur["unit_number"], ur["scenario_uoe"]))

    if not updates:
        return JSONResponse(content={"success": True, "data": {
            "message": "No UOE overrides to apply (no units had adjusted UOE).",
            "units_updated": 0,
        }})

    now = _now()
    performed_by = str(current_user.get("_id", ""))

    for unit_number, new_uoe in updates:
        await db.units.update_one(
            {"building_id": building_id, "unit_number": unit_number},
            {"$set": {
                "entitlement": new_uoe,
                "entitlement_updated_at": now,
                "entitlement_updated_by": performed_by,
                "entitlement_scenario_source": scenario_id,
            }},
        )

    # Mark scenario as published
    await db.levy_scenarios.update_one(
        {"_id": _oid(scenario_id)},
        {"$set": {
            "status": ScenarioStatus.PUBLISHED.value,
            "published_at": now,
            "published_by": performed_by,
            "updated_at": now,
        }},
    )

    return JSONResponse(content={"success": True, "data": {
        "message": f"Applied UOE overrides for {len(updates)} units.",
        "units_updated": len(updates),
        "published_at": now,
    }})
