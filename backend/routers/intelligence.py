"""
Intelligence Router — Maintenance and Multi-domain Analytics
"""

from datetime import datetime, timezone
import logging

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional, Dict

from database import db
from models.user import UserRole
from services.capital_shock_service import detect_capital_shocks
from services.levy_fairness_service import simulate_levy_fairness_v2, compute_subsidy_map
from services.levy_stability_service import compute_levy_stability_snapshot, recompute_levy_stability_snapshot
from services.maintenance_intelligence_service import (
    recompute_all_maintenance_intelligence,
    simulate_levy_stabilization
)
from services.special_levy_service import compute_special_levy_forecast, recompute_special_levy_forecast
from services.finance_route_cutover_service import get_finance_route_runtime_state
from utils.auth import get_approved_user, get_current_user, get_current_building, effective_role

router = APIRouter(prefix="/intelligence")
logger = logging.getLogger(__name__)


@router.get("/summary")
async def get_intelligence_summary(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Get the building-wide intelligence summary."""
    summary = await db.intelligence_summary.find_one({"building_id": bid}, {"_id": 0})
    if not summary:
        from services.maintenance_intelligence_service import update_building_intelligence_summary
        summary = await update_building_intelligence_summary(bid)
    return summary


@router.post("/recompute")
async def trigger_recompute(
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building)
):
    """Force recompute of all intelligence metrics (Admin only)."""
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.EC_MEMBER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    await recompute_all_maintenance_intelligence(bid)
    return {"message": "Intelligence recompute started"}


@router.get("/maintenance-forecast")
async def get_maintenance_forecast(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Predictive maintenance for next 12 months."""
    forecast = await db.maintenance_forecasts.find_one({
        "building_id": bid,
        "year": datetime.now().year
    }, {"_id": 0})
    return forecast


# ─── Levy Fairness V2 ─────────────────────────────────────────────────────────

class LevySimulationRequest(BaseModel):
    max_change_percent: Optional[float] = None
    max_change_amount: Optional[float] = None
    run_monte_carlo: bool = True


class SpecialLevyScenarioRequest(BaseModel):
    sinking_fund_increase_per_unit: Optional[float] = None
    defer_capital_years: Optional[int] = None
    loan_amount: Optional[float] = None
    loan_interest_rate: Optional[float] = None
    loan_term_years: Optional[int] = None
    horizon_years: Optional[int] = None


async def _levy_fairness_postgres(bid: str) -> Optional[Dict]:
    """Compute the levy-fairness response from the PostgreSQL finance ledger.

    Returns the PG response dict, or None when this building has no scheme
    context or no entitlement-bearing lots in Postgres yet (a valid state for a
    building whose ledger has not been onboarded — the caller then falls back to
    MongoDB). Only invoked when the finance route cutover engine resolves this
    route to ``postgres`` (see get_levy_fairness_v2), so it is subject to the
    same shadow-soak/critical-diff gates as every other governed finance route.
    """
    from db_postgres.repos.config_repo import resolve_scheme_context
    from db_postgres.session import async_session_context, set_tenant
    from sqlalchemy import text

    scheme = await resolve_scheme_context(bid)
    if not scheme:
        return None
    sid = str(scheme["scheme_id"])
    tid = str(scheme["tenant_id"])
    async with async_session_context() as session:
        await set_tenant(session, tid)
        rows = await session.execute(
            text("""
                SELECT
                    l.unit_number,
                    COALESCE(l.entitlement, 0) AS entitlement,
                    COALESCE(SUM(li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents), 0) AS levied_cents
                FROM core.lots l
                LEFT JOIN finance.levy_items li ON li.lot_id = l.lot_id
                WHERE l.scheme_id = CAST(:sid AS UUID)
                GROUP BY l.unit_number, l.entitlement
                HAVING COALESCE(l.entitlement, 0) > 0
            """),
            {"sid": sid},
        )
        lot_rows = rows.fetchall()

    if not lot_rows:
        return None

    per_ent = []
    unit_rows = []
    for row in lot_rows:
        entitlement = float(row.entitlement or 0)
        levied = float(row.levied_cents or 0) / 100
        ratio = (levied / entitlement) if entitlement > 0 else 0.0
        per_ent.append(ratio)
        unit_rows.append({
            "unit_number": row.unit_number,
            "entitlement": entitlement,
            "levied": round(levied, 2),
            "ratio": ratio,
        })

    mean_ratio = sum(per_ent) / len(per_ent) if per_ent else 0.0
    variance = sum((x - mean_ratio) ** 2 for x in per_ent) / len(per_ent) if per_ent else 0.0
    std_dev = variance ** 0.5
    cv = (std_dev / mean_ratio) if mean_ratio > 0 else 0.0
    fairness_score = round(max(0.0, min(100.0, 100.0 - (cv * 100.0))), 1)

    impact = []
    for u in unit_rows:
        expected = mean_ratio * u["entitlement"]
        net = round(u["levied"] - expected, 2)
        impact.append({"group_name": f"Unit {u['unit_number']}", "net_subsidy": net})
    impact.sort(key=lambda g: abs(g["net_subsidy"]), reverse=True)

    return {
        "lbfi": {
            "current_score": fairness_score,
            "D": round(std_dev, 4),
        },
        "impact_by_group": impact[:5],
        # No per-facility cost-centre breakdown exists on the Postgres
        # path (that's a MongoDB-only concept), so the biggest per-unit
        # subsidy distortions are the closest available "driver" list.
        "top_drivers": [
            {"name": g["group_name"], "amount": abs(g["net_subsidy"])}
            for g in impact[:5]
            if g["net_subsidy"]
        ],
        "source": "postgres",
    }


@router.get("/levy-model")
@router.get("/levy-fairness")
async def get_levy_fairness_v2(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Next-Gen Facility Cost Centre Allocation Engine.

    Source is governed by the shared finance route cutover engine
    (get_finance_route_runtime_state, route_key ``intelligence.levy_fairness``),
    exactly like every other finance route — GAP-FIN-052 item 2 replaced this
    handler's former inline try-Postgres/fall-back-to-Mongo logic, which gated
    only on ``financial_pg_reads_enabled`` and bypassed the shadow-soak and
    critical-diff gates. While the route is not promoted (postgres_read_supported
    False — no PG shadow comparator exists yet), it resolves to MongoDB; the
    Postgres read model activates automatically once the route is promoted.
    """
    try:
        route_state = await get_finance_route_runtime_state(
            building_id=bid, route_key="intelligence.levy_fairness",
        )
        source = route_state.get("source", "mongo")
    except Exception as exc:
        # The governance engine being unavailable must never take down a
        # Mongo-primary route — default to the safe primary source.
        logger.info(
            "intelligence/levy-fairness: cutover engine unavailable (%s), defaulting to MongoDB",
            exc,
        )
        source = "mongo"

    if source == "postgres":
        try:
            pg_result = await _levy_fairness_postgres(bid)
            if pg_result is not None:
                return pg_result
        except Exception as exc:
            logger.info(
                "intelligence/levy-fairness: Postgres path failed (%s), falling back to MongoDB",
                exc,
            )

    result = await db.levy_fairness_results_v2.find_one({"building_id": bid}, {"_id": 0})
    if not result:
        result = await simulate_levy_fairness_v2(bid)
    return result


@router.get("/levy-fairness/demo")
async def get_levy_fairness_demo(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Demonstration mode using the current building's model (Plan 13195)."""
    result = await db.levy_fairness_results_v2.find_one({"building_id": bid}, {"_id": 0})
    if not result:
        result = await simulate_levy_fairness_v2(bid)
    return result


@router.post("/levy-simulation")
@router.post("/levy-fairness/recompute")
async def recompute_levy_fairness_v2(
        payload: Optional[LevySimulationRequest] = None,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building)
):
    """Force-recompute next-gen levy fairness model and simulations."""
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.EC_MEMBER,
                                                                                    UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    p = payload.dict() if payload else {}
    result = await simulate_levy_fairness_v2(
        bid,
        max_change_percent=p.get("max_change_percent"),
        max_change_amount=p.get("max_change_amount"),
        run_monte_carlo=p.get("run_monte_carlo", True)
    )
    from services.levy_fairness_service import _log_fairness_audit
    await _log_fairness_audit(
        "model_regenerated",
        current_user.get("email", ""),
        (current_user.get("effective_role") or current_user.get("role", "")),
        bid,
        {"max_change_percent": p.get("max_change_percent"), "max_change_amount": p.get("max_change_amount")},
    )
    return result


@router.get("/levy-facilities")
@router.get("/capital-works/funding-position")
async def get_capital_funding_position(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Ten-year capital plan against ten years of sinking levy, per benefit group.

    Solvency, not fairness, and it precedes the fairness question: no split of an
    insufficient fund is fair. Unlike the annual redistribution these two figures are
    independent, so their difference is a real position rather than an arithmetic
    residual -- `funding_gap` positive means the plan is not funded by the current levy.

    Returns `null` for `capital_outlook` when the scheme has no dated capital plan. That
    is a distinct state from a funded one and the caller must not render it as $0.
    """
    from services.levy_fairness_service import simulate_levy_fairness

    result = await simulate_levy_fairness(bid)
    return {
        "building_id": bid,
        "computed_at": result.get("computed_at"),
        # Carried through so a page can refuse to present a position the engine itself
        # declined to stand behind -- e.g. while a capital item names a missing asset.
        "status": result.get("status"),
        "missing_inputs": result.get("missing_inputs", []),
        "capital_outlook": result.get("capital_outlook"),
    }


@router.get("/levy-fairness/facilities")
async def get_levy_facilities(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Get facility cost centres."""
    return await db.facility_cost_centres.find({"building_id": bid}, {"_id": 0}).to_list(500)


@router.get("/levy-groups")
@router.get("/levy-fairness/groups")
async def get_levy_groups(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Get benefit groups."""
    return await db.benefit_groups.find({"building_id": bid}, {"_id": 0}).to_list(500)


# ─── Benefit Group CRUD ───────────────────────────────────────────────────────

class BenefitGroupCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    group_type: str = "custom"  # global | apartment | townhouse | custom
    allocation_driver: str = "unit_entitlement"
    unit_prefixes: List[str] = []  # e.g. ["UA"] or ["U"] — flexible prefix matching
    unit_number_range: Optional[Dict[str, str]] = None  # {"min": "UA001", "max": "UA070"}
    lot_numbers: List[str] = []  # explicit membership list (highest priority)


class BenefitGroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    group_type: Optional[str] = None
    allocation_driver: Optional[str] = None
    unit_prefixes: Optional[List[str]] = None
    unit_number_range: Optional[Dict[str, str]] = None
    lot_numbers: Optional[List[str]] = None


@router.post("/levy-fairness/groups")
async def create_benefit_group(
        payload: BenefitGroupCreate,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building)
):
    """Create a new benefit group. Management roles (Super Admin, Chairman, Strata Manager, EC Member)."""
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.EC_MEMBER,
                                                                                    UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized — management role required")
    import uuid
    doc = payload.dict()
    doc["id"] = f"bg-{uuid.uuid4().hex[:8]}"
    doc["building_id"] = bid
    doc["allocation_rule"] = {"allocation_type": doc["allocation_driver"]}
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    doc["updated_at"] = doc["created_at"]
    await db.benefit_groups.insert_one(doc)
    doc.pop("_id", None)
    from services.levy_fairness_service import _log_fairness_audit
    await _log_fairness_audit("group_created", current_user.get("email", ""),
                              (current_user.get("effective_role") or current_user.get("role", "")), bid,
                              {"group_id": doc["id"], "name": doc["name"]})
    return doc


@router.put("/levy-fairness/groups/{group_id}")
async def update_benefit_group(
        group_id: str,
        payload: BenefitGroupUpdate,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building)
):
    """Update an existing benefit group. Management roles (Super Admin, Chairman, Strata Manager, EC Member)."""
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.EC_MEMBER,
                                                                                    UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized — management role required")
    updates = payload.dict(exclude_none=True)
    if "allocation_driver" in updates:
        updates["allocation_rule"] = {"allocation_type": updates["allocation_driver"]}
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.benefit_groups.update_one(
        {"id": group_id, "building_id": bid},
        {"$set": updates}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Benefit group not found")
    from services.levy_fairness_service import _log_fairness_audit
    await _log_fairness_audit("group_updated", current_user.get("email", ""),
                              (current_user.get("effective_role") or current_user.get("role", "")), bid,
                              {"group_id": group_id, "changes": list(updates.keys())})
    return {"message": "Updated"}


@router.delete("/levy-fairness/groups/{group_id}")
async def delete_benefit_group(
        group_id: str,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building)
):
    """Delete a benefit group. Super Admin or Strata Manager only. Blocked if in use by facilities."""
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized — Super Admin or Strata Manager required")
    # Safety check: is this group referenced by any facilities in building_assets/facilities?
    in_use = await db.facilities.count_documents({"building_id": bid, "benefit_group_id": group_id})
    if in_use > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Group is in use by {in_use} facilit{'y' if in_use == 1 else 'ies'} — reassign them first"
        )
    result = await db.benefit_groups.delete_one({"id": group_id, "building_id": bid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Benefit group not found")
    from services.levy_fairness_service import _log_fairness_audit
    await _log_fairness_audit("group_deleted", current_user.get("email", ""),
                              (current_user.get("effective_role") or current_user.get("role", "")), bid,
                              {"group_id": group_id})
    return {"message": "Deleted"}


@router.get("/levy-fairness/groups/{group_id}/preview")
async def preview_benefit_group_members(
        group_id: str,
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Return list of unit numbers that match this benefit group's rules."""
    bg = await db.benefit_groups.find_one({"id": group_id, "building_id": bid}, {"_id": 0})
    if not bg:
        raise HTTPException(status_code=404, detail="Benefit group not found")
    units = await db.units.find({"building_id": bid}, {"_id": 0, "unit_number": 1}).to_list(1000)
    if not units:
        units = await db.units.find({}, {"_id": 0, "unit_number": 1}).to_list(1000)
    # Re-use allocation engine matching logic
    from services.facility_allocation_engine import calculate_facility_allocation
    fake_fac = {"facility_id": "_preview", "annual_cost": 1.0,
                "benefit_group_id": group_id, "allocation_driver": "equal_split"}
    alloc = await calculate_facility_allocation(fake_fac, units, {group_id: bg}, {})
    return {"group_id": group_id, "matched_units": sorted(alloc.keys()), "count": len(alloc)}


# ─── Explain-This-Result Endpoint ─────────────────────────────────────────────

@router.get("/levy-fairness/unit/{unit_number}/explain")
async def explain_unit_levy(
        unit_number: str,
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Per-unit breakdown explaining why levy changed."""
    # Owners can only explain their own unit
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.EC_MEMBER,
                                                                                    UserRole.STRATA_MANAGER]:
        user_unit = current_user.get("unit_number")
        if user_unit and user_unit != unit_number:
            raise HTTPException(status_code=403, detail="You can only view your own unit")

    result = await db.levy_fairness_results_v2.find_one({"building_id": bid}, {"_id": 0, "unit_impact": 1})
    if not result:
        raise HTTPException(status_code=404, detail="No fairness model found — click Regenerate")
    unit_data = next((u for u in (result.get("unit_impact") or []) if u["unit_number"] == unit_number), None)
    if not unit_data:
        raise HTTPException(status_code=404, detail=f"Unit {unit_number} not found in model")
    return unit_data


# ─── Scenario Snapshots ───────────────────────────────────────────────────────

class SnapshotCreate(BaseModel):
    name: str
    description: Optional[str] = ""


@router.post("/levy-fairness/snapshots")
async def create_snapshot(
        payload: SnapshotCreate,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building)
):
    """Save current fairness model as a named snapshot. Management roles (Super Admin, Chairman, Strata Manager, EC Member)."""
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.EC_MEMBER,
                                                                                    UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized — management role required")
    import uuid
    current = await db.levy_fairness_results_v2.find_one({"building_id": bid}, {"_id": 0})
    if not current:
        raise HTTPException(status_code=404, detail="No current model to snapshot")
    doc = {
        "snapshot_id": f"snap-{uuid.uuid4().hex[:10]}",
        "name": payload.name,
        "description": payload.description,
        "building_id": bid,
        "created_by": current_user.get("email"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": current,
    }
    await db.levy_fairness_snapshots.insert_one(doc)
    doc.pop("_id", None)
    doc.pop("data", None)  # don't return full data in list
    from services.levy_fairness_service import _log_fairness_audit
    await _log_fairness_audit("snapshot_created", current_user.get("email", ""),
                              (current_user.get("effective_role") or current_user.get("role", "")), bid,
                              {"snapshot_id": doc["snapshot_id"], "name": payload.name})
    return doc


@router.get("/levy-fairness/snapshots")
async def list_snapshots(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """List all saved snapshots."""
    docs = await db.levy_fairness_snapshots.find(
        {"building_id": bid},
        {"_id": 0, "data": 0}
    ).sort("created_at", -1).to_list(50)
    return docs


@router.get("/levy-fairness/snapshots/{snapshot_id}")
async def get_snapshot(
        snapshot_id: str,
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Get a specific snapshot by ID."""
    doc = await db.levy_fairness_snapshots.find_one(
        {"snapshot_id": snapshot_id, "building_id": bid}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return doc


@router.delete("/levy-fairness/snapshots/{snapshot_id}")
async def delete_snapshot(
        snapshot_id: str,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building)
):
    """Delete a snapshot. Super Admin or Strata Manager only."""
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized — Super Admin or Strata Manager required")
    result = await db.levy_fairness_snapshots.delete_one({"snapshot_id": snapshot_id, "building_id": bid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return {"message": "Deleted"}


@router.post("/levy-fairness/snapshots/{snapshot_id}/restore")
async def restore_snapshot(
        snapshot_id: str,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building)
):
    """Restore a snapshot as the current active model. Management roles (Super Admin, Chairman, Strata Manager, EC Member)."""
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.EC_MEMBER,
                                                                                    UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized — management role required")
    doc = await db.levy_fairness_snapshots.find_one({"snapshot_id": snapshot_id, "building_id": bid})
    if not doc:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    restored_data = doc["data"]
    restored_data["restored_from_snapshot"] = snapshot_id
    restored_data["restored_at"] = datetime.now(timezone.utc).isoformat()
    restored_data["restored_by"] = current_user.get("email")
    restore_doc = {k: v for k, v in restored_data.items() if k != "building_id"}
    await db.levy_fairness_results_v2.update_one(
        {"building_id": bid}, {"$set": restore_doc}, upsert=True
    )
    from services.levy_fairness_service import _log_fairness_audit
    await _log_fairness_audit("snapshot_restored", current_user.get("email", ""),
                              (current_user.get("effective_role") or current_user.get("role", "")), bid,
                              {"snapshot_id": snapshot_id, "snapshot_name": doc.get("name")})
    return {"message": "Snapshot restored as active model", "snapshot_id": snapshot_id}


# ─── Subsidy Map (Workstream E2) ──────────────────────────────────────────────

@router.get("/levy-fairness/subsidy-map")
async def get_subsidy_map(
        year: Optional[str] = Query(None, description="Financial year e.g. '2026'. Defaults to latest."),
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building),
):
    """
    Subsidy Map: which unit types cross-subsidise which facilities and by how much per year.
    Returns SubsidyMapResult with all monetary values in integer cents.
    """
    from utils.finance_helpers import get_latest_levy_year
    financial_year = year or str(await get_latest_levy_year(bid))
    cache_key = f"{bid}:{financial_year}"
    cached = await db.subsidy_map_cache.find_one({"cache_key": cache_key}, {"_id": 0, "result": 1})
    if cached:
        return cached["result"]
    result = await compute_subsidy_map(bid, financial_year)
    await db.subsidy_map_cache.update_one(
        {"cache_key": cache_key},
        {"$set": {"cache_key": cache_key, "building_id": bid, "result": result.dict(),
                  "computed_at": result.computed_at.isoformat()}},
        upsert=True,
    )
    return result


@router.get("/levy-fairness/subsidy-map/unit/{unit_number}")
async def get_subsidy_map_for_unit(
        unit_number: str,
        year: Optional[str] = Query(None),
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building),
):
    """Per-unit view of the subsidy map — which facilities this unit over/under-pays for."""
    # Owners can only view their own unit
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [
        UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER
    ]:
        user_unit = current_user.get("unit_number")
        if user_unit and user_unit != unit_number:
            raise HTTPException(status_code=403, detail="You can only view your own unit")

    from utils.finance_helpers import get_latest_levy_year
    financial_year = year or str(await get_latest_levy_year(bid))

    # Resolve unit type
    unit_doc = await db.units.find_one(
        {"building_id": bid, "unit_number": unit_number}, {"_id": 0, "unit_type": 1, "unit_number": 1}
    )
    if not unit_doc:
        raise HTTPException(status_code=404, detail=f"Unit {unit_number} not found")

    cache_key = f"{bid}:{financial_year}"
    cached = await db.subsidy_map_cache.find_one({"cache_key": cache_key}, {"_id": 0, "result": 1})
    if cached:
        full = cached["result"]
    else:
        result = await compute_subsidy_map(bid, financial_year)
        full = result.dict()

    from services.levy_fairness_service import _group_key
    unit_type = _group_key(unit_doc)
    unit_entries = [e for e in full.get("subsidy_map", []) if e["unit_type"] == unit_type]
    unit_summary = full.get("summary_by_unit_type", {}).get(unit_type, {})

    return {
        "unit_number": unit_number,
        "unit_type": unit_type,
        "financial_year": financial_year,
        "summary": unit_summary,
        "facility_entries": unit_entries,
        "key_findings": full.get("key_findings", []),
    }


@router.post("/levy-fairness/subsidy-map/recalculate")
async def recalculate_subsidy_map(
        year: Optional[str] = Query(None),
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building),
):
    """Force recalculate and cache the subsidy map. All management roles."""
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.EC_MEMBER,
                                                                                    UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    from utils.finance_helpers import get_latest_levy_year
    financial_year = year or str(await get_latest_levy_year(bid))

    result = await compute_subsidy_map(bid, financial_year)
    cache_key = f"{bid}:{financial_year}"
    await db.subsidy_map_cache.update_one(
        {"cache_key": cache_key},
        {"$set": {"cache_key": cache_key, "building_id": bid, "result": result.dict(),
                  "computed_at": result.computed_at.isoformat()}},
        upsert=True,
    )
    from services.levy_fairness_service import _log_fairness_audit
    await _log_fairness_audit(
        "subsidy_map_recalculated", current_user.get("email", ""),
        (current_user.get("effective_role") or current_user.get("role", "")), bid, {"financial_year": financial_year}
    )
    return {
        "message": "Subsidy map recalculated",
        "financial_year": financial_year,
        "total_cross_subsidy_cents": result.total_cross_subsidy_cents,
        "key_findings": result.key_findings,
    }


# ─── Audit Log ────────────────────────────────────────────────────────────────

@router.get("/levy-fairness/audit")
async def get_fairness_audit_log(
        limit: int = Query(50, le=200),
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building)
):
    """Get audit trail for levy fairness changes. All management roles."""
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.EC_MEMBER,
                                                                                    UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")
    records = await db.levy_fairness_audit.find(
        {"building_id": bid}, {"_id": 0}
    ).sort("timestamp", -1).limit(limit).to_list(limit)
    return records


# ─── Cross-Subsidy CSV Export ─────────────────────────────────────────────────

@router.get("/levy-fairness/cross-subsidy-report.csv")
async def download_cross_subsidy_csv(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Download cross-subsidy report as CSV."""
    from fastapi.responses import StreamingResponse
    from io import StringIO
    import csv
    result = await db.levy_fairness_results_v2.find_one({"building_id": bid}, {"_id": 0, "cross_subsidy_report": 1})
    if not result or not result.get("cross_subsidy_report"):
        raise HTTPException(status_code=404, detail="No cross-subsidy data — run Regenerate first")

    report = result["cross_subsidy_report"]
    buf = StringIO()
    writer = csv.writer(buf)

    # Group summary section
    writer.writerow(["Cross-Subsidy Analysis — Group Summary"])
    writer.writerow(
        ["Group", "Units", "Current Total ($)", "Fair Total ($)", "Net Subsidy ($)", "Net/Unit ($)", "Role"])
    for row in report.get("group_rows", []):
        writer.writerow([row["group"], row["unit_count"], row["current_total"], row["fair_total"],
                         row["net_subsidy"], row["net_subsidy_per_unit"], row["role"]])

    writer.writerow([])
    writer.writerow(["Facility Cost Breakdown"])
    writer.writerow(["Facility", "Annual Cost ($)", "% of Total"])
    for row in report.get("facility_rows", []):
        writer.writerow([row["facility_name"], row["annual_cost"], row["pct_of_total"]])

    content = buf.getvalue()
    filename = f"EastGate_CrossSubsidy_{datetime.now().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class FacilityCostCentreUpdate(BaseModel):
    facility_name: str
    annual_cost: float
    benefit_group_id: str
    allocation_driver: str = "unit_entitlement"
    enabled: bool = True


@router.post("/levy-fairness/facilities")
async def create_facility_cost_centre(
        payload: FacilityCostCentreUpdate,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building)
):
    """Generated function header.

    Function: create_facility_cost_centre
    Path: backend/routers/intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.EC_MEMBER,
                                                                                    UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized — management role required")
    import uuid
    doc = payload.dict()
    doc["facility_id"] = f"fac-{uuid.uuid4().hex[:8]}"
    doc["building_id"] = bid
    await db.facility_cost_centres.insert_one(doc)
    doc.pop("_id", None)
    from services.levy_fairness_service import _log_fairness_audit
    await _log_fairness_audit("facility_created", current_user.get("email", ""),
                              (current_user.get("effective_role") or current_user.get("role", "")), bid,
                              {"facility_id": doc["facility_id"], "name": doc.get("name", "")})
    return doc


@router.put("/levy-fairness/facilities/{facility_id}")
async def update_facility_cost_centre(
        facility_id: str,
        payload: FacilityCostCentreUpdate,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building)
):
    """Generated function header.

    Function: update_facility_cost_centre
    Path: backend/routers/intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.EC_MEMBER,
                                                                                    UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized — management role required")
    await db.facility_cost_centres.update_one(
        {"building_id": bid, "facility_id": facility_id},
        {"$set": payload.dict()}
    )
    from services.levy_fairness_service import _log_fairness_audit
    await _log_fairness_audit("facility_updated", current_user.get("email", ""),
                              (current_user.get("effective_role") or current_user.get("role", "")), bid,
                              {"facility_id": facility_id, "changes": list(payload.dict().keys())})
    return {"message": "Updated"}


@router.delete("/levy-fairness/facilities/{facility_id}")
async def delete_facility_cost_centre(
        facility_id: str,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building)
):
    """Generated function header.

    Function: delete_facility_cost_centre
    Path: backend/routers/intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized — Super Admin or Strata Manager required")
    await db.facility_cost_centres.delete_one({"building_id": bid, "facility_id": facility_id})
    from services.levy_fairness_service import _log_fairness_audit
    await _log_fairness_audit("facility_deleted", current_user.get("email", ""),
                              (current_user.get("effective_role") or current_user.get("role", "")), bid,
                              {"facility_id": facility_id})
    return {"message": "Deleted"}


# ─── Legacy/Other Endpoints ───────────────────────────────────────────────────

@router.get("/levy-fairness/unit-impact")
async def get_levy_unit_impact(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Per-unit levy impact."""
    res = await db.levy_fairness_results_v2.find_one({"building_id": bid}, {"_id": 0, "unit_impact": 1})
    return res.get("unit_impact", []) if res else []


@router.get("/levy-fairness/agm-report.pdf")
async def download_agm_report(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Generate and stream the AGM Levy Equity Analysis PDF."""
    from fastapi.responses import StreamingResponse
    from services.levy_fairness_pdf_service import generate_agm_report_pdf

    pdf_bytes = await generate_agm_report_pdf(bid)
    if not pdf_bytes:
        raise HTTPException(status_code=503, detail="PDF generation unavailable")

    from io import BytesIO
    filename = f"EastGate_LevyEquity_{datetime.now().strftime('%Y%m%d')}.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/levy-fairness/agm-presentation.pptx")
async def download_agm_presentation(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Generate and stream the AGM Levy Equity Analysis PPTX."""
    from fastapi.responses import StreamingResponse
    from services.levy_fairness_pdf_service import generate_agm_presentation

    pptx_bytes = await generate_agm_presentation(bid)
    if not pptx_bytes:
        raise HTTPException(status_code=503, detail="PPTX generation unavailable")

    from io import BytesIO
    filename = f"EastGate_LevyEquity_{datetime.now().strftime('%Y%m%d')}.pptx"
    return StreamingResponse(
        BytesIO(pptx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/levy-fairness/impact.csv")
async def download_impact_csv(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Generate and stream the Levy Impact CSV."""
    from fastapi.responses import StreamingResponse
    from services.levy_fairness_pdf_service import generate_impact_csv

    csv_content = await generate_impact_csv(bid)
    if not csv_content:
        raise HTTPException(status_code=503, detail="CSV generation unavailable")

    from io import BytesIO
    filename = f"EastGate_LevyImpact_{datetime.now().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        BytesIO(csv_content.encode()),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _capital_shock_postgres(bid: str) -> Optional[Dict]:
    """Compute the capital-shock response from the PostgreSQL ledger/ops tables.

    Returns the PG response dict, or None when this building has no scheme
    context or no approved-budget work orders in Postgres yet (the caller then
    falls back to MongoDB). Only invoked when the finance route cutover engine
    resolves ``intelligence.capital_shock`` to ``postgres`` (see
    get_capital_shock), so it is subject to the same governed gates as every
    other finance route.
    """
    from db_postgres.repos.config_repo import resolve_scheme_context
    from db_postgres.session import async_session_context, set_tenant
    from sqlalchemy import text

    scheme = await resolve_scheme_context(bid)
    if not scheme:
        return None
    sid = str(scheme["scheme_id"])
    tid = str(scheme["tenant_id"])
    async with async_session_context() as session:
        await set_tenant(session, tid)
        rows = await session.execute(
            text("""
                SELECT
                    wo.created_at,
                    wo.approved_budget_cents,
                    wr.summary
                FROM ops.work_orders wo
                LEFT JOIN ops.work_requests wr ON wr.work_request_id = wo.work_request_id
                WHERE wo.scheme_id = CAST(:sid AS UUID)
                  AND wo.approved_budget_cents IS NOT NULL
                  AND wo.approved_budget_cents > 0
                ORDER BY wo.created_at DESC
                LIMIT 10
            """),
            {"sid": sid},
        )
        work_rows = rows.fetchall()

    if not work_rows:
        return None

    shock_rows = []
    for r in work_rows[:5]:
        estimated = round(float(r.approved_budget_cents or 0) / 100, 2)
        year = r.created_at.year if r.created_at else datetime.now().year
        risk = "high" if estimated >= 50000 else "medium" if estimated >= 15000 else "low"
        shock_rows.append({
            "year": year,
            "description": r.summary or "Capital works item",
            "estimated_cost": estimated,
            "risk_level": risk,
            # Alias so frontend consumers reading `severity` (the field
            # name used on the MongoDB path) get a value here too.
            "severity": risk,
        })

    next_shock = shock_rows[0] if shock_rows else None
    return {
        "capital_shock_index": {
            "rows": shock_rows,
            "next_shock": next_shock,
        },
        "source": "postgres",
    }


@router.get("/capital-shock")
async def get_capital_shock(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Predict capital shock risk based on reserves and replacement schedule.

    Source is governed by the shared finance route cutover engine
    (get_finance_route_runtime_state, route_key ``intelligence.capital_shock``) —
    GAP-FIN-052 item 2 replaced this handler's former inline PG-fallback logic,
    which gated only on ``financial_pg_reads_enabled`` and bypassed the
    shadow-soak/critical-diff gates. While the route is not promoted, it resolves
    to MongoDB; the Postgres read model activates once the route is promoted.
    """
    try:
        route_state = await get_finance_route_runtime_state(
            building_id=bid, route_key="intelligence.capital_shock",
        )
        source = route_state.get("source", "mongo")
    except Exception as exc:
        logger.info(
            "intelligence/capital-shock: cutover engine unavailable (%s), defaulting to MongoDB",
            exc,
        )
        source = "mongo"

    if source == "postgres":
        try:
            pg_result = await _capital_shock_postgres(bid)
            if pg_result is not None:
                return pg_result
        except Exception as exc:
            logger.info(
                "intelligence/capital-shock: Postgres path failed (%s), falling back to MongoDB",
                exc,
            )

    result = await db.capital_shock_risks.find_one({"building_id": bid}, {"_id": 0})
    if not result:
        result = await detect_capital_shocks(bid)
    return result


@router.get("/special-levy-forecast")
async def get_special_levy_forecast(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Predict probability, timing, and size of special levies."""
    return await compute_special_levy_forecast(bid)


@router.post("/special-levy-forecast/recompute")
async def recompute_special_levy(
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building)
):
    """Generated function header.

    Function: recompute_special_levy
    Path: backend/routers/intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.EC_MEMBER,
                                                                                    UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")
    return await recompute_special_levy_forecast(bid, force=True)


@router.post("/special-levy-forecast/what-if")
async def special_levy_what_if(
        payload: SpecialLevyScenarioRequest,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building)
):
    """Generated function header.

    Function: special_levy_what_if
    Path: backend/routers/intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.EC_MEMBER,
                                                                                    UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")
    return await compute_special_levy_forecast(bid, force=True, scenario=payload.dict(exclude_none=True))


@router.get("/special-levy-report")
async def download_special_levy_report(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Generate and stream the Special Levy Risk Report."""
    from fastapi.responses import StreamingResponse
    from io import BytesIO
    from services.report_service import generate_special_levy_report

    pdf_bytes = await generate_special_levy_report(bid)
    if not pdf_bytes:
        raise HTTPException(status_code=503, detail="PDF generation unavailable")

    filename = f"EastGate_SpecialLevyRisk_{datetime.now().strftime('%Y%m%d')}.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/levy-stability")
async def get_levy_stability(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Compute levy stability score and component breakdown."""
    return await compute_levy_stability_snapshot(bid)


@router.post("/levy-stability/recompute")
async def recompute_levy_stability(
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building)
):
    """Generated function header.

    Function: recompute_levy_stability
    Path: backend/routers/intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.EC_MEMBER,
                                                                                    UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")
    return await recompute_levy_stability_snapshot(bid, force=True)


@router.post("/levy-stability/what-if")
async def levy_stability_what_if(
        payload: SpecialLevyScenarioRequest,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building)
):
    """Generated function header.

    Function: levy_stability_what_if
    Path: backend/routers/intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if (current_user.get("effective_role") or current_user.get("role", "")) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.EC_MEMBER,
                                                                                    UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")
    return await compute_levy_stability_snapshot(bid, force=True, scenario=payload.dict(exclude_none=True))


@router.get("/maintenance-risks")
async def get_maintenance_risks(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Maintenance anomaly list for attention-needed assets."""
    return await db.maintenance_anomalies.find({"building_id": bid}, {"_id": 0}).to_list(50)


class CapitalWorkItemUpdate(BaseModel):
    asset_name: str
    replacement_year: int
    estimated_cost: float
    category: str = ""
    asset_id: Optional[str] = None
    # Which lots fund this line item. Omit (or send null) to keep whatever the
    # row already carries; send "" to clear it deliberately. Without these two
    # fields declared here, Pydantic dropped them from every payload and the
    # replace below flattened the entire plan to ALL_LOTS.
    benefit_group_id: Optional[str] = None
    facility_id: Optional[str] = None


class CapitalWorksUpdateRequest(BaseModel):
    items: List[CapitalWorkItemUpdate]


_PLAN_EDIT_ROLES = {"super_admin", "strata_admin", "strata_manager", "ec_member"}


def _require_capital_plan_edit(current_user: dict) -> None:
    """Generated function header.

    Function: _require_capital_plan_edit
    Path: backend/routers/intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in _PLAN_EDIT_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Only Chairman, EC Member, Strata Manager and Super Admin can edit the capital works plan"
        )


@router.get("/capital-works")
async def get_capital_works_schedule(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """10-year capital replacement plan."""
    return await db.capital_replacement_schedule.find({"building_id": bid}, {"_id": 0}).sort("replacement_year",
                                                                                             1).to_list(100)


@router.put("/capital-works")
async def update_capital_works_schedule(
        payload: CapitalWorksUpdateRequest,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building)
):
    """Replace the capital replacement schedule (plan-edit roles only)."""
    _require_capital_plan_edit(current_user)
    now = datetime.now(timezone.utc).isoformat()
    items = [
        {**item.dict(), "building_id": bid, "source": "manual", "updated_at": now}
        for item in payload.items
    ]
    from repositories.digital_twin_repository import update_capital_schedule
    await update_capital_schedule(bid, items)
    # Invalidate capital shock cache so it gets recomputed on next request
    await db.capital_shock_risks.delete_many({"building_id": bid})
    return {"count": len(items), "message": "Capital works schedule updated"}


@router.get("/levy-stabilization")
async def get_levy_simulation(
        reserve_target: int = Query(12),
        change_limit: float = Query(5.0),
        inflation: float = Query(0.03),
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building)
):
    """Simulate levy stabilization paths."""
    return await simulate_levy_stabilization(
        building_id=bid,
        reserve_target_months=reserve_target,
        levy_change_limit=change_limit,
        inflation_rate=inflation
    )


@router.get("/health-history/{asset_id}")
async def get_asset_health_history(asset_id: str, current_user: dict = Depends(get_approved_user)):
    """Fetch health score history for a specific asset."""
    return await db.asset_health_scores.find({"asset_id": asset_id}).sort("updated_at", -1).limit(20).to_list(20)
