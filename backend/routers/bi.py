# @featuretrace:bi-analytics — BI Analytics router: canonical server-side aggregation endpoints.
# Layer: router
# Data flow: BIAnalyticsPage.tsx + ManagementDashboard + OwnerDashboard →
#            GET /api/bi/* → bi.py → bi_service.py → analytics.fact_* (PG) or MongoDB fallback.
#            analytics.fact_* is refreshed daily 02:15 AEST by
#            workers.scheduler:run_bi_analytics_etl → services/bi_etl_service.py
#            (also triggerable ad hoc via POST /bi/admin/etl/run).
# Related: backend/services/bi_service.py
#          backend/services/bi_etl_service.py
#          backend/workers/scheduler.py
#          backend/alembic/versions/0052_canonical_bi_schema.py
#          frontend/src/pages/dashboard/BIAnalyticsPage.tsx
# Toggle: bi_analytics_enabled
# Tests: tests/backend/test_bi_router.py
"""BI Analytics router — canonical aggregation endpoints.

Rule: EVERY endpoint reads from canonical facts (analytics schema) when the
      bi_analytics_enabled toggle is ON.  Falls back to MongoDB automatically
      via bi_service.py.  No direct MongoDB queries in this router file.

Auth: All building/* endpoints require strata_manager / ec_member / super_admin.
      Owner/* endpoints require ownership of the requested lot or super_admin.
      Portfolio/* endpoints require super_admin or strata_manager.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from utils.auth import get_approved_user, get_current_building, get_current_user
from models.user import UserRole
from services.capability_registry import assert_capability_hydrated, can_hydrated

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bi")

# ─────────────────────────────────────────────────────────────────────────────
# Role helpers (inline pattern per CLAUDE.md)
# ─────────────────────────────────────────────────────────────────────────────

_MANAGER_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.STRATA_ADMIN,
    UserRole.EC_MEMBER,
}


def _building_scope(current_user: dict, building_id: str) -> dict[str, str]:
    # organisation_id must describe the target building, not the caller. These
    # routes currently carry only building_id, so they require an explicit
    # building claim and do not infer organisation inheritance.
    """Generated function header.

    Function: _building_scope
    Path: backend/routers/bi.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {"building_id": building_id}


# ── Authorisation (GAP-SEC-014) ───────────────────────────────────────────────
# These helpers are async because they hydrate verified building claims before
# deciding. They previously called the synchronous assert_capability(), which
# hydrates nothing — so `_building_matches` had only the INHERITED building_id /
# current_building_id claims to test, and those come from the user's stored
# default_scheme_id whenever the JWT carries no building. A manager whose role
# assignment for a scheme had been revoked kept passing every check here.
#
# They stay helpers rather than becoming require_capability dependencies because
# `_require_manager` resolves its target building from the path when the route has
# one and from the session when it does not, which a dependency cannot express.


async def _require_manager(current_user: dict, building_id: str | None = None) -> None:
    """Require building BI visibility for the named building, else the session's."""
    target_building = building_id or current_user.get("building_id") or current_user.get("current_building_id") or ""
    await assert_capability_hydrated(
        current_user,
        "building.bi.view",
        _building_scope(current_user, target_building),
    )


async def _require_bi_manage(current_user: dict, building_id: str) -> None:
    """Require BI/ETL management rights over the named building."""
    await assert_capability_hydrated(
        current_user,
        "building.bi.manage",
        _building_scope(current_user, building_id),
    )


async def _require_bi_cutover(current_user: dict, building_id: str) -> None:
    """Require rights to inspect the named building's BI cutover readiness."""
    await assert_capability_hydrated(
        current_user,
        "building.bi.cutover.view",
        _building_scope(current_user, building_id),
    )


async def _require_platform_bi(current_user: dict) -> None:
    """Require platform-wide BI visibility (super admin)."""
    await assert_capability_hydrated(
        current_user,
        "platform.bi.view",
        {"platform_id": "platform"},
    )


async def _require_admin(current_user: dict) -> None:
    """Compatibility wrapper for existing direct guard tests."""
    await _require_platform_bi(current_user)


def _user_identity_values(current_user: dict) -> set[str]:
    """Return stable user identifiers present in mixed Mongo/PG auth payloads.

    BI owner routes still receive Mongo-style and Postgres-style tokens during
    cutover. Treat every known user id field as an alias, but never authorize
    an owner route unless the requested owner_id matches one of these values.
    """
    values = {
        current_user.get("id"),
        current_user.get("_id"),
        current_user.get("user_id"),
    }
    return {str(v) for v in values if v not in (None, "")}


def _owner_id_matches_current_user(owner_id: str, current_user: dict) -> bool:
    """Generated function header.

    Function: _owner_id_matches_current_user
    Path: backend/routers/bi.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(owner_id) in _user_identity_values(current_user)


async def _owner_has_user_unit(
    *,
    owner_id: str,
    building_id: str,
    lot_number: str | None,
    current_user: dict,
) -> bool:
    """Validate owner scope against user_units when token claims are incomplete.

    The fast path is the unit_number claim because many owner tokens carry only
    one unit. The Mongo user_units lookup keeps multi-unit owners working while
    still requiring both an owner-id match and building scope. Failure to read
    the compatibility collection denies access; it must not fail open.
    """
    if not _owner_id_matches_current_user(owner_id, current_user):
        return False

    claim_building = current_user.get("building_id")
    claim_unit = current_user.get("unit_number") or current_user.get("lot_number")
    if claim_building and str(claim_building) == str(building_id):
        if lot_number is None or str(claim_unit) == str(lot_number):
            return True

    user_ids = list(_user_identity_values(current_user))
    if not user_ids:
        return False

    try:
        from database import db

        query: dict[str, Any] = {
            "building_id": building_id,
            "$or": [
                {"user_id": {"$in": user_ids}},
                {"owner_id": {"$in": user_ids}},
            ],
            "status": {"$ne": "inactive"},
        }
        if lot_number is not None:
            query["$and"] = [{
                "$or": [
                    {"unit_number": str(lot_number)},
                    {"lot_number": str(lot_number)},
                ]
            }]
        return await db.user_units.find_one(query, {"_id": 1}) is not None
    except Exception as exc:
        logger.warning("Owner BI scope lookup failed closed: %s", exc)
        return False


async def _require_owner_or_manager_scope(
    *,
    owner_id: str,
    building_id: str,
    current_user: dict,
    lot_number: str | None = None,
) -> str:
    """Authorize BI owner endpoints with one scoped decision point.

    Managers retain the existing broad BI visibility. Plain owners must match
    the owner_id path parameter and must be linked to the requested building,
    plus the requested lot when the endpoint is lot-specific.
    """
    role = current_user.get("effective_role") or current_user.get("role", "guest")
    if role in _MANAGER_ROLES and await can_hydrated(
        current_user,
        "building.bi.view",
        _building_scope(current_user, building_id),
    ):
        return role
    if role == UserRole.OWNER and await _owner_has_user_unit(
        owner_id=owner_id,
        building_id=building_id,
        lot_number=lot_number,
        current_user=current_user,
    ):
        return role
    raise HTTPException(status_code=403, detail="Access denied")


def _ok(data: Any, *, building_id: str | None = None) -> dict:
    """Generated function header.

    Function: _ok
    Path: backend/routers/bi.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "data": data,
        "building_id": building_id,
        "source": data.get("source") if isinstance(data, dict) else (
            data[0].get("source") if data and isinstance(data, list) else "unknown"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Building endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/building/{building_id}/financial-summary")
async def building_financial_summary(
    building_id: str,
    financial_year: str | None = Query(None, description="e.g. '2026'"),
    current_user: dict = Depends(get_approved_user),
):
    """Financial KPI summary: levy collection %, arrears total, fund balances.

    Arrears + Collection Rate are ALWAYS computed live from MongoDB
    (unit_levy_ledger via utils.finance_helpers, never from analytics.fact_*)
    — deliberate, per GAP-FIN-040: the ETL'd fact_arrears_snapshot cannot be
    trusted for this headline correctness-critical figure. Fund balances
    (admin/sinking fund, income) come from analytics.fact_financial_balance
    when bi_pg_primary_enabled is on for this building, refreshed by the
    "bi_analytics_etl" scheduled job (workers.scheduler, daily 02:15 AEST),
    else live MongoDB. See services/bi_service.py:get_financial_summary for
    the exact split. Grain: one row per building per year.
    """
    await _require_manager(current_user, building_id)
    from services.bi_service import get_financial_summary
    data = await get_financial_summary(building_id, financial_year)
    return _ok(data, building_id=building_id)


@router.get("/building/{building_id}/levy-collection-trend")
async def building_levy_collection_trend(
    building_id: str,
    months: int = Query(12, ge=3, le=36),
    current_user: dict = Depends(get_approved_user),
):
    """Monthly levy collection trend: charged vs paid by month.

    Source: analytics.fact_levy_charge + fact_levy_payment.
    Refresh: event-driven (on receipt).
    """
    await _require_manager(current_user, building_id)
    from services.bi_service import get_levy_collection_trend
    data = await get_levy_collection_trend(building_id, months=months)
    return _ok(data, building_id=building_id)


@router.get("/building/{building_id}/arrears-hotspots")
async def building_arrears_hotspots(
    building_id: str,
    min_days_overdue: int = Query(0, ge=0),
    current_user: dict = Depends(get_approved_user),
):
    """Per-lot arrears table sorted by outstanding amount.

    Source: analytics.fact_arrears_snapshot (latest snapshot).
    Refresh: nightly ETL.
    """
    await _require_manager(current_user, building_id)
    from services.bi_service import get_arrears_hotspots
    data = await get_arrears_hotspots(building_id, min_days_overdue=min_days_overdue)
    return _ok(data, building_id=building_id)


@router.get("/building/{building_id}/capex-vs-plan")
async def building_capex_vs_plan(
    building_id: str,
    years: int = Query(10, ge=1, le=20),
    current_user: dict = Depends(get_approved_user),
):
    """Capital works planned vs actual spend by year.

    Source: analytics.fact_capex_plan + fact_capex_actual.
    Refresh: nightly ETL.
    """
    await _require_manager(current_user, building_id)
    from services.bi_service import get_capex_vs_plan
    data = await get_capex_vs_plan(building_id, years=years)
    return _ok(data, building_id=building_id)


@router.get("/building/{building_id}/sinking-fund-projection")
async def building_sinking_fund_projection(
    building_id: str,
    years: int = Query(10, ge=5, le=20),
    current_user: dict = Depends(get_approved_user),
):
    """10-year sinking fund balance projection with shortfall risk flags.

    Source: analytics.fact_financial_balance (sinking/capital_works fund rows).
    Refresh: nightly ETL.
    """
    await _require_manager(current_user, building_id)
    from services.bi_service import get_sinking_fund_projection
    data = await get_sinking_fund_projection(building_id, years=years)
    return _ok(data, building_id=building_id)


@router.get("/building/{building_id}/health-trend")
async def building_health_trend(
    building_id: str,
    days: int = Query(90, ge=7, le=365),
    current_user: dict = Depends(get_approved_user),
):
    """Building health score trend with explainable drivers.

    Source: analytics.fact_building_health_snapshot.
    Refresh: nightly ETL.
    """
    await _require_manager(current_user, building_id)
    from services.bi_service import get_health_trend
    data = await get_health_trend(building_id, days=days)
    return _ok(data, building_id=building_id)


@router.get("/building/{building_id}/maintenance-costs")
async def building_maintenance_costs(
    building_id: str,
    financial_year: str | None = Query(None),
    current_user: dict = Depends(get_approved_user),
):
    """Maintenance cost by category/vendor with SLA breach count.

    Source: analytics.fact_work_order.
    Refresh: event-driven (on work order update).
    """
    await _require_manager(current_user, building_id)
    from services.bi_service import get_maintenance_costs
    data = await get_maintenance_costs(building_id, financial_year=financial_year)
    return _ok(data, building_id=building_id)


@router.get("/building/{building_id}/compliance-status")
async def building_compliance_status(
    building_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """RAG compliance grid by register/inspection type.

    Source: analytics.fact_compliance_event (latest per register).
    Refresh: event-driven (on compliance update).
    """
    await _require_manager(current_user, building_id)
    from services.bi_service import get_compliance_status
    data = await get_compliance_status(building_id)
    return _ok(data, building_id=building_id)


@router.get("/building/{building_id}/utility-trend")
async def building_utility_trend(
    building_id: str,
    months: int = Query(12, ge=3, le=36),
    current_user: dict = Depends(get_approved_user),
):
    """Monthly utility spend by type (water, electricity, gas, council rates) with spike flags.

    Source: analytics.fact_utility_bill.
    Refresh: event-driven (on bill import).
    """
    await _require_manager(current_user, building_id)
    from services.bi_service import get_utility_trend
    data = await get_utility_trend(building_id, months=months)
    return _ok(data, building_id=building_id)


@router.get("/building/{building_id}/occupancy-mix")
async def building_occupancy_mix(
    building_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Owner-occupier / tenant / investor / vacant breakdown.

    Source: analytics.fact_occupancy_snapshot (latest snapshot).
    Refresh: nightly ETL.
    """
    await _require_manager(current_user, building_id)
    from services.bi_service import get_occupancy_mix
    data = await get_occupancy_mix(building_id)
    return _ok(data, building_id=building_id)


@router.get("/building/{building_id}/request-heatmap")
async def building_request_heatmap(
    building_id: str,
    months: int = Query(12, ge=3, le=24),
    current_user: dict = Depends(get_approved_user),
):
    """Smart request count by category × month with recurring pattern flags.

    Source: analytics.fact_smart_request.
    Refresh: event-driven (on request submission).
    """
    await _require_manager(current_user, building_id)
    from services.bi_service import get_request_heatmap
    data = await get_request_heatmap(building_id, months=months)
    return _ok(data, building_id=building_id)


@router.get("/building/{building_id}/asset-risk")
async def building_asset_risk(
    building_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Assets by condition score with replacement risk and remaining life.

    Source: analytics.fact_asset_condition_snapshot (latest per asset).
    Refresh: monthly ETL.
    """
    await _require_manager(current_user, building_id)
    from services.bi_service import get_asset_risk
    data = await get_asset_risk(building_id)
    return _ok(data, building_id=building_id)


# ─────────────────────────────────────────────────────────────────────────────
# Owner endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/owner/{owner_id}/levy-history")
async def owner_levy_history(
    owner_id: str,
    lot_number: str = Query(..., description="Lot number e.g. '42'"),
    building_id: str = Query(...),
    years: int = Query(5, ge=1, le=10),
    current_user: dict = Depends(get_approved_user),
):
    """Levy charge + payment history for a specific lot.

    Source: analytics.fact_levy_charge + fact_levy_payment.
    Permission: owner of the lot OR manager/admin.
    """
    await _require_owner_or_manager_scope(
        owner_id=owner_id,
        building_id=building_id,
        lot_number=lot_number,
        current_user=current_user,
    )

    from services.bi_service import get_owner_levy_history
    data = await get_owner_levy_history(building_id, lot_number, years=years)
    return _ok(data, building_id=building_id)


@router.get("/owner/{owner_id}/arrears-status")
async def owner_arrears_status(
    owner_id: str,
    lot_number: str = Query(...),
    building_id: str = Query(...),
    current_user: dict = Depends(get_approved_user),
):
    """Current arrears position for a specific lot.

    Source: analytics.fact_arrears_snapshot.
    """
    await _require_owner_or_manager_scope(
        owner_id=owner_id,
        building_id=building_id,
        lot_number=lot_number,
        current_user=current_user,
    )

    from services.bi_service import get_owner_arrears_status
    data = await get_owner_arrears_status(building_id, lot_number)
    return _ok(data, building_id=building_id)


@router.get("/owner/{owner_id}/total-cost-of-ownership")
async def owner_total_cost_of_ownership(
    owner_id: str,
    lot_number: str = Query(...),
    building_id: str = Query(...),
    years: int = Query(5, ge=1, le=10),
    current_user: dict = Depends(get_approved_user),
):
    """True cost of ownership: levies + water + council rates + insurance share.

    Source: analytics.fact_true_cost_ownership.
    """
    await _require_owner_or_manager_scope(
        owner_id=owner_id,
        building_id=building_id,
        lot_number=lot_number,
        current_user=current_user,
    )

    from services.bi_service import get_owner_total_cost_of_ownership
    data = await get_owner_total_cost_of_ownership(building_id, lot_number, years=years)
    return _ok(data, building_id=building_id)


@router.get("/owner/{owner_id}/building-health-context")
async def owner_building_health_context(
    owner_id: str,
    building_id: str = Query(...),
    current_user: dict = Depends(get_approved_user),
):
    """Latest building health snapshot in owner context (summary-level, no lot PII).

    Source: analytics.fact_building_health_snapshot.
    """
    await _require_owner_or_manager_scope(
        owner_id=owner_id,
        building_id=building_id,
        current_user=current_user,
    )

    from services.bi_service import get_health_trend
    data = await get_health_trend(building_id, days=90)
    # Strip PII-adjacent fields for pure owner view
    owner_safe = [
        {
            "date": r["date"],
            "overall_score": r["overall_score"],
            "health_label": r["health_label"],
            "financial_score": r["financial_score"],
            "maintenance_score": r["maintenance_score"],
            "compliance_score": r["compliance_score"],
            "source": r.get("source"),
        }
        for r in data
    ]
    return _ok(owner_safe, building_id=building_id)


@router.get("/owner/{owner_id}/capex-schedule")
async def owner_capex_schedule(
    owner_id: str,
    building_id: str = Query(...),
    years: int = Query(10, ge=1, le=15),
    current_user: dict = Depends(get_approved_user),
):
    """Upcoming capital works schedule visible to owners (no cost if not manager).

    Source: analytics.fact_capex_plan.
    """
    role = await _require_owner_or_manager_scope(
        owner_id=owner_id,
        building_id=building_id,
        current_user=current_user,
    )

    from services.bi_service import get_capex_vs_plan
    data = await get_capex_vs_plan(building_id, years=years)
    is_manager = role in _MANAGER_ROLES
    # Owners see schedule without actual cost line
    if not is_manager:
        data = [{"year": r["year"], "planned": r["planned"], "is_forecast": r["is_forecast"]} for r in data]
    return _ok(data, building_id=building_id)


# ─────────────────────────────────────────────────────────────────────────────
# Investor endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/owner/{owner_id}/yield-estimate")
async def owner_yield_estimate(
    owner_id: str,
    lot_number: str = Query(...),
    building_id: str = Query(...),
    current_user: dict = Depends(get_approved_user),
):
    """Gross/net yield estimate for an investor lot.

    Source: analytics.fact_investor_yield_snapshot.
    """
    await _require_owner_or_manager_scope(
        owner_id=owner_id,
        building_id=building_id,
        lot_number=lot_number,
        current_user=current_user,
    )

    if await _bi_toggle_on(building_id):
        try:
            from sqlalchemy import text
            from db_postgres.session import async_session_context, set_tenant
            from db_postgres.repos import identity_repo as _id_repo

            scheme = await _id_repo.get_scheme_by_number(building_id)
            if not scheme:
                raise RuntimeError("No scheme")
            scheme_id = str(scheme["scheme_id"])
            tenant_id = str(scheme["tenant_id"])

            async with async_session_context() as session:
                await set_tenant(session, tenant_id)
                rows = await session.execute(
                    text("""
                        SELECT snapshot_quarter, gross_rental_income_cents,
                               total_strata_costs_cents, net_income_cents,
                               gross_yield_pct, net_yield_pct, vacancy_weeks
                        FROM analytics.fact_investor_yield_snapshot
                        WHERE scheme_id = CAST(:sid AS UUID)
                          AND lot_number = :lot
                          AND COALESCE(is_test_data, FALSE) = FALSE
                        ORDER BY snapshot_quarter DESC
                        LIMIT 8
                    """),
                    {"sid": scheme_id, "lot": lot_number},
                )
                from services.bi_service import _cents
                data = [
                    {
                        "quarter": r[0],
                        "gross_rental_income": _cents(r[1]),
                        "strata_costs": _cents(r[2]),
                        "net_income": _cents(r[3]),
                        "gross_yield_pct": float(r[4] or 0),
                        "net_yield_pct": float(r[5] or 0),
                        "vacancy_weeks": float(r[6] or 0),
                        "source": "postgres_bi",
                    }
                    for r in rows.fetchall()
                ]
                return _ok(data, building_id=building_id)
        except Exception as e:
            logger.info("yield_estimate PG unavailable: %s", e)

    # Mongo fallback via investor intelligence service
    try:
        from services.investor_intelligence_service import get_investor_summary
        data = await get_investor_summary(building_id, lot_number)
        return _ok(data or [], building_id=building_id)
    except Exception:
        return _ok([], building_id=building_id)


@router.get("/owner/{owner_id}/investment-risk")
async def owner_investment_risk(
    owner_id: str,
    lot_number: str = Query(...),
    building_id: str = Query(...),
    current_user: dict = Depends(get_approved_user),
):
    """Investment risk signals: sinking fund shortfall, arrears neighbours, capex slippage.

    Source: analytics.fact_* (aggregated risk signals).
    """
    await _require_owner_or_manager_scope(
        owner_id=owner_id,
        building_id=building_id,
        lot_number=lot_number,
        current_user=current_user,
    )

    from services.bi_service import get_sinking_fund_projection, get_arrears_hotspots, get_asset_risk
    sf = await get_sinking_fund_projection(building_id, years=10)
    own_arrears = await get_owner_arrears_status_safe(building_id, lot_number)
    assets = await get_asset_risk(building_id)
    critical_assets = [a for a in assets if a.get("risk_band") in ("high", "critical")]

    return _ok({
        "sinking_fund_shortfall": sf.get("shortfall_risk", False),
        "shortfall_years": sf.get("shortfall_years", []),
        "own_lot_in_arrears": own_arrears.get("in_arrears", False),
        "critical_assets_count": len(critical_assets),
        "risk_score": _compute_investment_risk_score(sf, own_arrears, critical_assets),
        "source": "computed",
    }, building_id=building_id)


async def get_owner_arrears_status_safe(building_id: str, lot_number: str) -> dict:
    """Generated function header.

    Function: get_owner_arrears_status_safe
    Path: backend/routers/bi.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        from services.bi_service import get_owner_arrears_status
        return await get_owner_arrears_status(building_id, lot_number)
    except Exception:
        return {}


def _compute_investment_risk_score(sf: dict, arrears: dict, critical_assets: list) -> str:
    """Generated function header.

    Function: _compute_investment_risk_score
    Path: backend/routers/bi.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    score = 0
    if sf.get("shortfall_risk"):
        score += 3
    if arrears.get("in_arrears"):
        score += 2
    score += min(len(critical_assets), 3)
    if score >= 6:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


@router.get("/building/{building_id}/sinking-fund-risk")
async def building_sinking_fund_risk(
    building_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Sinking fund shortfall risk assessment for investors and EC.

    Source: analytics.fact_financial_balance.
    """
    role = current_user.get("effective_role") or current_user.get("role", "guest")
    if await can_hydrated(current_user, "building.bi.view", _building_scope(current_user, building_id)):
        pass
    elif role == UserRole.OWNER:
        owner_ids = _user_identity_values(current_user)
        if not owner_ids or not await _owner_has_user_unit(
            owner_id=next(iter(owner_ids)),
            building_id=building_id,
            lot_number=None,
            current_user=current_user,
        ):
            raise HTTPException(status_code=403, detail="Access denied")
    else:
        raise HTTPException(status_code=403, detail="Access denied")

    from services.bi_service import get_sinking_fund_projection
    data = await get_sinking_fund_projection(building_id, years=10)
    return _ok(data, building_id=building_id)


# ─────────────────────────────────────────────────────────────────────────────
# Portfolio endpoints
# ─────────────────────────────────────────────────────────────────────────────

async def _resolve_portfolio_buildings(org_id: str, current_user: dict) -> list[str]:
    """Resolve list of building_ids for a portfolio/org."""
    from database import db
    await assert_capability_hydrated(
        current_user,
        "organisation.portfolio.view",
        {"organisation_id": org_id},
    )
    role = current_user.get("effective_role") or current_user.get("role", "guest")
    # For super_admin: return all active buildings; otherwise limit to managed buildings
    if role == UserRole.SUPER_ADMIN:
        cursor = db.buildings.find(
            {"is_archived": {"$ne": True}},
            {"building_id": 1, "plan_id": 1}
        )
        docs = await cursor.to_list(100)
        return [str(d.get("building_id") or d.get("plan_id", "")) for d in docs if d.get("building_id") or d.get("plan_id")]
    # Strata managers: buildings they manage
    cursor = db.memberships.find(
        {"user_id": str(current_user.get("_id")), "status": "active"},
        {"building_id": 1}
    )
    docs = await cursor.to_list(50)
    return [str(d.get("building_id", "")) for d in docs if d.get("building_id")]


@router.get("/portfolio/{org_id}/arrears-hotspots")
async def portfolio_arrears_hotspots(
    org_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Cross-building arrears hotspots for portfolio.

    Source: analytics.fact_arrears_snapshot aggregated per building.
    """
    building_ids = await _resolve_portfolio_buildings(org_id, current_user)
    from services.bi_service import get_portfolio_arrears_hotspots
    data = await get_portfolio_arrears_hotspots(org_id, building_ids)
    return {"data": data, "building_count": len(building_ids)}


@router.get("/portfolio/{org_id}/compliance-overdue")
async def portfolio_compliance_overdue(
    org_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Cross-building overdue compliance by building.

    Source: analytics.fact_compliance_event.
    """
    building_ids = await _resolve_portfolio_buildings(org_id, current_user)
    from services.bi_service import get_portfolio_compliance_overdue
    data = await get_portfolio_compliance_overdue(org_id, building_ids)
    return {"data": data, "building_count": len(building_ids)}


@router.get("/portfolio/{org_id}/health-ranking")
async def portfolio_health_ranking(
    org_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Building health ranking across portfolio.

    Source: analytics.fact_building_health_snapshot.
    """
    building_ids = await _resolve_portfolio_buildings(org_id, current_user)
    from services.bi_service import get_portfolio_health_ranking
    data = await get_portfolio_health_ranking(org_id, building_ids)
    return {"data": data, "building_count": len(building_ids)}


@router.get("/portfolio/{org_id}/budget-vs-actual")
async def portfolio_budget_vs_actual(
    org_id: str,
    financial_year: str | None = Query(None),
    current_user: dict = Depends(get_approved_user),
):
    """Cross-building budget vs actual spend for the portfolio.

    Source: analytics.fact_financial_balance.
    """
    building_ids = await _resolve_portfolio_buildings(org_id, current_user)
    results = []
    for bid in building_ids:
        try:
            from services.bi_service import get_financial_summary
            summary = await get_financial_summary(bid, financial_year)
            results.append({
                "building_id": bid,
                "admin_income": summary.get("admin_income"),
                "admin_expenses": None,  # from fact_financial_balance
                "levy_collection_rate": summary.get("levy_collection_rate"),
                "source": summary.get("source"),
            })
        except Exception:
            results.append({"building_id": bid, "error": "UNKNOWN"})
    return {"data": results, "building_count": len(building_ids)}


@router.get("/portfolio/{org_id}/maintenance-risk")
async def portfolio_maintenance_risk(
    org_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Cross-building maintenance cost and SLA breach risk.

    Source: analytics.fact_work_order.
    """
    building_ids = await _resolve_portfolio_buildings(org_id, current_user)
    results = []
    for bid in building_ids:
        try:
            from services.bi_service import get_maintenance_costs
            costs = await get_maintenance_costs(bid)
            results.append({
                "building_id": bid,
                "open_work_orders": costs.get("open_work_orders"),
                "sla_breaches": costs.get("sla_breaches"),
                "total_cost": costs.get("total_cost"),
                "source": costs.get("source"),
            })
        except Exception:
            results.append({"building_id": bid, "error": "UNKNOWN"})
    results.sort(key=lambda x: int(x.get("sla_breaches") or 0), reverse=True)
    return {"data": results, "building_count": len(building_ids)}


@router.get("/portfolio/{org_id}/upcoming-compliance")
async def portfolio_upcoming_compliance(
    org_id: str,
    days_ahead: int = Query(30, ge=7, le=90),
    current_user: dict = Depends(get_approved_user),
):
    """Cross-building compliance due in next N days.

    Source: analytics.fact_compliance_event.
    """
    building_ids = await _resolve_portfolio_buildings(org_id, current_user)
    results = []
    for bid in building_ids:
        try:
            from services.bi_service import get_compliance_status
            status = await get_compliance_status(bid)
            results.append({
                "building_id": bid,
                "total_overdue": status.get("total_overdue"),
                "total_pending": status.get("total_pending"),
                "overall_status": status.get("overall_status"),
                "source": status.get("source"),
            })
        except Exception:
            results.append({"building_id": bid, "error": "UNKNOWN"})
    results.sort(key=lambda x: (x.get("overall_status") == "red", x.get("total_overdue") or 0), reverse=True)
    return {"data": results, "building_count": len(building_ids)}


@router.get("/portfolio/{org_id}/comparison")
async def portfolio_comparison(
    org_id: str,
    financial_year: str | None = Query(None),
    current_user: dict = Depends(get_approved_user),
):
    """Side-by-side comparison of key metrics across portfolio buildings.

    Source: analytics.fact_* (aggregated per building).
    """
    building_ids = await _resolve_portfolio_buildings(org_id, current_user)
    comparison = []
    for bid in building_ids:
        try:
            from services.bi_service import get_financial_summary, get_compliance_status
            fin = await get_financial_summary(bid, financial_year)
            comp = await get_compliance_status(bid)
            comparison.append({
                "building_id": bid,
                "levy_collection_rate": fin.get("levy_collection_rate"),
                "lots_in_arrears": fin.get("lots_in_arrears"),
                "total_arrears": fin.get("total_arrears"),
                "admin_fund_balance": fin.get("admin_fund_balance"),
                "sinking_fund_balance": fin.get("sinking_fund_balance"),
                "compliance_overdue": comp.get("total_overdue"),
                "compliance_status": comp.get("overall_status"),
            })
        except Exception:
            comparison.append({"building_id": bid, "error": "UNKNOWN"})
    return {"data": comparison, "building_count": len(building_ids)}


# ─────────────────────────────────────────────────────────────────────────────
# Alert endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/building/{building_id}/alerts")
async def building_alerts(
    building_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Evaluate all alert rules for a building and return active alerts.

    Rules: arrears_escalation, compliance_due, utility_spike, health_score_drop.
    Source: analytics.fact_* (PG, refreshed by the daily bi_analytics_etl job)
    when bi_pg_primary_enabled is on for this building, else a live MongoDB
    fallback per rule (bi_service.evaluate_*_alerts) — alerts must keep firing
    during the cutover window rather than silently going quiet.
    """
    await _require_manager(current_user, building_id)
    from services.bi_service import (
        evaluate_arrears_alerts,
        evaluate_compliance_alerts,
        evaluate_utility_spike_alerts,
        evaluate_health_drop_alerts,
    )
    import asyncio
    results = await asyncio.gather(
        evaluate_arrears_alerts(building_id),
        evaluate_compliance_alerts(building_id),
        evaluate_utility_spike_alerts(building_id),
        evaluate_health_drop_alerts(building_id),
        return_exceptions=True,
    )
    alerts = []
    for r in results:
        if isinstance(r, list):
            alerts.extend(r)
        elif isinstance(r, Exception):
            logger.warning("Alert evaluation error: %s", r)

    return {
        "building_id": building_id,
        "alert_count": len(alerts),
        "alerts": alerts,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ETL status endpoint (admin only)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/admin/etl-status")
async def bi_etl_status(
    current_user: dict = Depends(get_approved_user),
):
    """BI ETL run history and data freshness report.

    Source: analytics.bi_etl_runs.
    Permission: super_admin only.
    """
    await _require_platform_bi(current_user)
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context, set_tenant

        async with async_session_context() as session:
            # ETL admin does not need tenant context (no RLS on bi_etl_runs)
            rows = await session.execute(
                text("""
                    SELECT target_table, MAX(completed_at) AS last_run,
                           SUM(rows_inserted) AS total_inserted,
                           COUNT(*) FILTER (WHERE status = 'error') AS error_runs
                    FROM analytics.bi_etl_runs
                    GROUP BY target_table
                    ORDER BY last_run DESC NULLS LAST
                """)
            )
            return {
                "tables": [
                    {
                        "table": r[0],
                        "last_run": str(r[1]) if r[1] else None,
                        "total_inserted": int(r[2] or 0),
                        "error_count": int(r[3] or 0),
                    }
                    for r in rows.fetchall()
                ]
            }
    except Exception as e:
        logger.warning("ETL status query failed: %s", e)
        return {"tables": [], "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Toggle helper (used by yield endpoint above)
# ─────────────────────────────────────────────────────────────────────────────

async def _bi_toggle_on(building_id: str) -> bool:
    """Generated function header.

    Function: _bi_toggle_on
    Path: backend/routers/bi.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        from services.bi_service import _toggle_on
        return await _toggle_on(building_id)
    except Exception:
        return False


def _meta(scope: str, source: str, pg_primary: bool, warnings: list | None = None) -> dict:
    """Standard response metadata block."""
    from datetime import datetime, timezone
    return {
        "scope": scope,
        "source": source,
        "pg_primary": pg_primary,
        "freshness": datetime.now(timezone.utc).isoformat(),
        "warnings": warnings or [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Cutover readiness endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/building/{building_id}/cutover-status")
async def building_cutover_status(
    building_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """BI cutover readiness report for a specific building.

    Checks dimensions, facts, ETL freshness, and parity against operational source.
    Use this before enabling bi_pg_primary_enabled for a building.

    Permission: super_admin, strata_admin, or assigned strata_manager only.
    """
    await _require_bi_cutover(current_user, building_id)
    role = current_user.get("effective_role") or current_user.get("role", "guest")
    if role == UserRole.STRATA_MANAGER:
        from services.bi_toggle_service import _get_strata_manager_profile_by_user, get_manager_buildings

        user_id = str(current_user.get("_id", ""))
        profile = await _get_strata_manager_profile_by_user(user_id)
        strata_manager_id = str((profile or {}).get("strata_manager_id", ""))
        if not strata_manager_id:
            raise HTTPException(status_code=403, detail="No active strata manager profile for this user")
        assigned_buildings = await get_manager_buildings(strata_manager_id)
        if building_id not in set(assigned_buildings):
            raise HTTPException(status_code=403, detail="Cutover status is limited to your assigned buildings")

    from services.bi_service import get_bi_cutover_status
    result = await get_bi_cutover_status(building_id)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Feature toggle resolution endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/access")
async def bi_access_check(
    building_id: str | None = Query(None),
    agency_id: str | None = Query(None),
    current_user: dict = Depends(get_approved_user),
):
    """Resolve BI feature access for the current user.

    Returns scope, enabled state, pg_primary flag, and resolution source.
    Used by the frontend to decide which BI pages to show and what banners to display.
    """
    from services.bi_toggle_service import resolve_bi_feature_access
    effective_role = current_user.get("effective_role") or current_user.get("role", "guest")
    user_id = str(current_user.get("_id", ""))
    access = await resolve_bi_feature_access(
        user_id=user_id,
        role=current_user.get("role", "guest"),
        effective_role=effective_role,
        agency_id=agency_id,
        building_id=building_id,
    )
    return access.as_dict()


# ─────────────────────────────────────────────────────────────────────────────
# Agency endpoints
# ─────────────────────────────────────────────────────────────────────────────

async def _require_agency_access(agency_id: str, current_user: dict) -> list[str]:
    """Verify access to agency BI and return the agency's building_ids."""
    from services.management_hierarchy_service import parse_uuid_or_400
    from services.bi_toggle_service import resolve_agency_access, get_agency_buildings

    agency_id = parse_uuid_or_400(agency_id, "agency_id")
    effective_role = current_user.get("effective_role") or current_user.get("role", "guest")
    user_id = str(current_user.get("_id", ""))
    allowed = await resolve_agency_access(
        user_id=user_id,
        effective_role=effective_role,
        agency_id=agency_id,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="No access to this agency's BI data")
    return await get_agency_buildings(agency_id)


@router.get("/agency/{agency_id}/summary")
async def agency_summary(
    agency_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """High-level financial summary across all buildings in the agency.

    Source: analytics.fact_* aggregated per building then rolled up.
    """
    building_ids = await _require_agency_access(agency_id, current_user)
    from services.bi_service import get_agency_summary
    data = await get_agency_summary(agency_id, building_ids)
    return {
        "data": data,
        "meta": _meta("agency", "analytics_pg", False),
    }


@router.get("/agency/{agency_id}/buildings")
async def agency_buildings(
    agency_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """List all buildings assigned to this agency."""
    building_ids = await _require_agency_access(agency_id, current_user)
    return {
        "data": [{"building_id": bid} for bid in building_ids],
        "building_count": len(building_ids),
        "meta": _meta("agency", "operational_pg", False),
    }


@router.get("/agency/{agency_id}/financial-summary")
async def agency_financial_summary(
    agency_id: str,
    financial_year: str | None = Query(None),
    current_user: dict = Depends(get_approved_user),
):
    """Per-building financial summaries for the agency.

    Source: analytics.fact_levy_charge + fact_financial_balance per building.
    """
    building_ids = await _require_agency_access(agency_id, current_user)
    from services.bi_service import get_financial_summary
    results = []
    for bid in building_ids:
        try:
            fin = await get_financial_summary(bid, financial_year)
            results.append({"building_id": bid, **fin})
        except Exception as exc:
            results.append({"building_id": bid, "error": str(exc)})
    return {
        "data": results,
        "agency_id": agency_id,
        "meta": _meta("agency", "analytics_pg", False),
    }


@router.get("/agency/{agency_id}/arrears-hotspots")
async def agency_arrears_hotspots(
    agency_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Top arrears lots across all agency buildings.

    Source: analytics.fact_arrears_snapshot per building.
    """
    building_ids = await _require_agency_access(agency_id, current_user)
    from services.bi_service import get_arrears_hotspots
    all_lots: list[dict] = []
    for bid in building_ids:
        try:
            lots = await get_arrears_hotspots(bid)
            for lot in lots:
                lot["building_id"] = bid
            all_lots.extend(lots)
        except Exception:
            pass
    all_lots.sort(key=lambda x: float(x.get("total_outstanding") or 0), reverse=True)
    return {
        "data": all_lots[:50],
        "total_count": len(all_lots),
        "meta": _meta("agency", "analytics_pg", False),
    }


@router.get("/agency/{agency_id}/compliance-overdue")
async def agency_compliance_overdue(
    agency_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Overdue compliance items across all agency buildings.

    Source: analytics.fact_compliance_event per building.
    """
    building_ids = await _require_agency_access(agency_id, current_user)
    from services.bi_service import get_portfolio_compliance_overdue
    data = await get_portfolio_compliance_overdue(agency_id, building_ids)
    return {
        "data": data,
        "meta": _meta("agency", "analytics_pg", False),
    }


@router.get("/agency/{agency_id}/health-ranking")
async def agency_health_ranking(
    agency_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Buildings ranked by health score within the agency.

    Source: analytics.fact_building_health_snapshot per building.
    """
    building_ids = await _require_agency_access(agency_id, current_user)
    from services.bi_service import get_agency_health_ranking
    data = await get_agency_health_ranking(agency_id, building_ids)
    return {
        "data": data,
        "meta": _meta("agency", "analytics_pg", False),
    }


@router.get("/agency/{agency_id}/maintenance-risk")
async def agency_maintenance_risk(
    agency_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Maintenance cost and SLA breach risk across agency buildings.

    Source: analytics.fact_work_order per building.
    """
    building_ids = await _require_agency_access(agency_id, current_user)
    from services.bi_service import get_maintenance_costs
    results = []
    for bid in building_ids:
        try:
            costs = await get_maintenance_costs(bid)
            results.append({
                "building_id": bid,
                "open_work_orders": costs.get("open_work_orders"),
                "sla_breaches": costs.get("sla_breaches"),
                "total_cost": costs.get("total_cost"),
                "source": costs.get("source"),
            })
        except Exception:
            results.append({"building_id": bid, "error": "UNKNOWN"})
    results.sort(key=lambda x: int(x.get("sla_breaches") or 0), reverse=True)
    return {
        "data": results,
        "meta": _meta("agency", "analytics_pg", False),
    }


@router.get("/agency/{agency_id}/budget-vs-actual")
async def agency_budget_vs_actual(
    agency_id: str,
    financial_year: str | None = Query(None),
    current_user: dict = Depends(get_approved_user),
):
    """Budget vs actual spend across agency buildings.

    Source: analytics.fact_financial_balance per building.
    """
    building_ids = await _require_agency_access(agency_id, current_user)
    from services.bi_service import get_financial_summary
    results = []
    for bid in building_ids:
        try:
            fin = await get_financial_summary(bid, financial_year)
            results.append({
                "building_id": bid,
                "admin_income": fin.get("admin_income"),
                "admin_expenses": fin.get("admin_expenses"),
                "levy_collection_rate": fin.get("levy_collection_rate"),
                "source": fin.get("source"),
            })
        except Exception:
            results.append({"building_id": bid, "error": "UNKNOWN"})
    return {
        "data": results,
        "meta": _meta("agency", "analytics_pg", False),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Strata Manager endpoints
# ─────────────────────────────────────────────────────────────────────────────

async def _require_manager_access(strata_manager_id: str, current_user: dict) -> list[str]:
    """Verify access to this strata manager's BI data and return their building_ids."""
    from services.management_hierarchy_service import parse_uuid_or_400
    from services.bi_toggle_service import resolve_manager_access, get_manager_buildings

    strata_manager_id = parse_uuid_or_400(strata_manager_id, "strata_manager_id")
    effective_role = current_user.get("effective_role") or current_user.get("role", "guest")
    user_id = str(current_user.get("_id", ""))
    allowed = await resolve_manager_access(
        user_id=user_id,
        effective_role=effective_role,
        strata_manager_id=strata_manager_id,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="No access to this strata manager's BI data")
    return await get_manager_buildings(strata_manager_id)


@router.get("/manager/{strata_manager_id}/summary")
async def manager_summary(
    strata_manager_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """High-level summary across all buildings assigned to this strata manager."""
    building_ids = await _require_manager_access(strata_manager_id, current_user)
    from services.bi_service import get_agency_summary
    data = await get_agency_summary(strata_manager_id, building_ids)
    return {
        "data": {**data, "strata_manager_id": strata_manager_id},
        "meta": _meta("portfolio", "analytics_pg", False),
    }


@router.get("/manager/{strata_manager_id}/buildings")
async def manager_buildings(
    strata_manager_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """List buildings assigned to this strata manager."""
    building_ids = await _require_manager_access(strata_manager_id, current_user)
    return {
        "data": [{"building_id": bid} for bid in building_ids],
        "building_count": len(building_ids),
        "meta": _meta("portfolio", "operational_pg", False),
    }


@router.get("/manager/{strata_manager_id}/arrears-hotspots")
async def manager_arrears_hotspots(
    strata_manager_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Top arrears lots across this manager's assigned buildings."""
    building_ids = await _require_manager_access(strata_manager_id, current_user)
    from services.bi_service import get_arrears_hotspots
    all_lots: list[dict] = []
    for bid in building_ids:
        try:
            lots = await get_arrears_hotspots(bid)
            for lot in lots:
                lot["building_id"] = bid
            all_lots.extend(lots)
        except Exception:
            pass
    all_lots.sort(key=lambda x: float(x.get("total_outstanding") or 0), reverse=True)
    return {
        "data": all_lots[:50],
        "total_count": len(all_lots),
        "meta": _meta("portfolio", "analytics_pg", False),
    }


@router.get("/manager/{strata_manager_id}/compliance-overdue")
async def manager_compliance_overdue(
    strata_manager_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Overdue compliance across this manager's buildings."""
    building_ids = await _require_manager_access(strata_manager_id, current_user)
    from services.bi_service import get_portfolio_compliance_overdue
    data = await get_portfolio_compliance_overdue(strata_manager_id, building_ids)
    return {
        "data": data,
        "meta": _meta("portfolio", "analytics_pg", False),
    }


@router.get("/manager/{strata_manager_id}/health-ranking")
async def manager_health_ranking(
    strata_manager_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Building health ranking for this manager's portfolio."""
    building_ids = await _require_manager_access(strata_manager_id, current_user)
    from services.bi_service import get_agency_health_ranking
    data = await get_agency_health_ranking(strata_manager_id, building_ids)
    return {
        "data": data,
        "meta": _meta("portfolio", "analytics_pg", False),
    }


@router.get("/manager/{strata_manager_id}/workload")
async def manager_workload(
    strata_manager_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Workload summary: open work orders, SLA breaches, compliance overdue.

    Source: analytics.fact_work_order + fact_compliance_event per building.
    """
    building_ids = await _require_manager_access(strata_manager_id, current_user)
    from services.bi_service import get_manager_workload
    data = await get_manager_workload(strata_manager_id, building_ids)
    return {
        "data": data,
        "meta": _meta("portfolio", "analytics_pg", False),
    }


@router.get("/manager/{strata_manager_id}/upcoming-actions")
async def manager_upcoming_actions(
    strata_manager_id: str,
    days_ahead: int = Query(30, ge=7, le=90),
    current_user: dict = Depends(get_approved_user),
):
    """Upcoming compliance and PPM actions across assigned buildings."""
    building_ids = await _require_manager_access(strata_manager_id, current_user)
    from services.bi_service import get_compliance_status
    results = []
    for bid in building_ids:
        try:
            comp = await get_compliance_status(bid)
            results.append({
                "building_id": bid,
                "total_overdue": comp.get("total_overdue"),
                "total_pending": comp.get("total_pending"),
                "overall_status": comp.get("overall_status"),
            })
        except Exception:
            results.append({"building_id": bid, "error": "UNKNOWN"})
    results.sort(
        key=lambda x: (x.get("overall_status") == "red", x.get("total_overdue") or 0),
        reverse=True,
    )
    return {
        "data": results,
        "meta": _meta("portfolio", "analytics_pg", False),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Platform endpoints (super_admin only)
# ─────────────────────────────────────────────────────────────────────────────

async def _require_super_admin(current_user: dict) -> None:
    """Platform BI guard for the /platform/* endpoints."""
    await _require_platform_bi(current_user)


@router.get("/platform/summary")
async def platform_summary(
    current_user: dict = Depends(get_approved_user),
):
    """Platform-wide BI summary across all agencies and buildings.

    Source: analytics.fact_* aggregated across all active schemes.
    Permission: super_admin only.
    """
    await _require_super_admin(current_user)
    from services.bi_service import get_platform_summary
    data = await get_platform_summary()
    return {
        "data": data,
        "meta": _meta("platform", "analytics_pg", False),
    }


@router.get("/platform/agencies")
async def platform_agencies(
    current_user: dict = Depends(get_approved_user),
):
    """List all agencies on the platform with building counts.

    Source: core.agencies + core.building_agency_assignments.
    Permission: super_admin only.
    """
    await _require_super_admin(current_user)
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            rows = await session.execute(text("""
                SELECT
                    a.agency_id::text,
                    a.name,
                    a.status,
                    a.is_self_managed,
                    COUNT(DISTINCT baa.scheme_id) AS building_count
                FROM core.agencies a
                LEFT JOIN core.building_agency_assignments baa
                    ON baa.agency_id = a.agency_id AND baa.status = 'active'
                WHERE COALESCE(a.is_test_data, FALSE) = FALSE
                GROUP BY a.agency_id, a.name, a.status, a.is_self_managed
                ORDER BY a.name
            """))
            agencies = [
                {
                    "agency_id": r[0],
                    "name": r[1],
                    "status": r[2],
                    "is_self_managed": r[3],
                    "building_count": int(r[4] or 0),
                }
                for r in rows.fetchall()
            ]
    except Exception as exc:
        logger.warning("platform_agencies PG query failed: %s", exc)
        agencies = []
    return {
        "data": agencies,
        "meta": _meta("platform", "operational_pg", False),
    }


@router.get("/platform/buildings")
async def platform_buildings(
    current_user: dict = Depends(get_approved_user),
):
    """List all active buildings on the platform.

    Source: core.schemes.
    Permission: super_admin only.
    """
    await _require_super_admin(current_user)
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            rows = await session.execute(text("""
                SELECT
                    s.scheme_id::text,
                    s.scheme_number,
                    s.scheme_name,
                    s.is_demo,
                    a.agency_id::text,
                    a.name AS agency_name
                FROM core.schemes s
                LEFT JOIN core.building_agency_assignments baa
                    ON baa.scheme_id = s.scheme_id AND baa.status = 'active'
                LEFT JOIN core.agencies a ON a.agency_id = baa.agency_id
                WHERE s.is_archived IS NOT TRUE
                  AND COALESCE(s.is_test_data, FALSE) = FALSE
                ORDER BY s.scheme_name
            """))
            buildings = [
                {
                    "scheme_id": r[0],
                    "building_id": r[1],
                    "name": r[2],
                    "is_demo": r[3],
                    "agency_id": r[4],
                    "agency_name": r[5],
                }
                for r in rows.fetchall()
            ]
    except Exception as exc:
        logger.warning("platform_buildings PG query failed: %s", exc)
        buildings = []
    return {
        "data": buildings,
        "meta": _meta("platform", "operational_pg", False),
    }


@router.get("/platform/feature-adoption")
async def platform_feature_adoption(
    current_user: dict = Depends(get_approved_user),
):
    """BI feature toggle adoption across all buildings.

    Shows which buildings have bi_analytics_enabled, bi_analytics_building_enabled,
    bi_pg_primary_enabled enabled.

    Permission: super_admin only.
    """
    await _require_super_admin(current_user)
    from services.bi_toggle_service import get_all_active_buildings, _resolve_building_toggle
    building_ids = await get_all_active_buildings()
    results = []
    for bid in building_ids:
        try:
            bi_on = await _resolve_building_toggle(bid, "bi_analytics_enabled")
            pg_on = await _resolve_building_toggle(bid, "bi_pg_primary_enabled")
            results.append({
                "building_id": bid,
                "bi_analytics_enabled": bi_on,
                "bi_pg_primary_enabled": pg_on,
            })
        except Exception:
            results.append({"building_id": bid, "error": "UNKNOWN"})
    enabled_count = sum(1 for r in results if r.get("bi_analytics_enabled"))
    pg_primary_count = sum(1 for r in results if r.get("bi_pg_primary_enabled"))
    return {
        "data": {
            "total_buildings": len(building_ids),
            "bi_enabled_count": enabled_count,
            "pg_primary_count": pg_primary_count,
            "adoption_rate_pct": round(enabled_count / max(len(building_ids), 1) * 100, 1),
            "buildings": results,
        },
        "meta": _meta("platform", "operational_pg", False),
    }


@router.get("/platform/data-freshness")
async def platform_data_freshness(
    current_user: dict = Depends(get_approved_user),
):
    """ETL data freshness by building and table.

    Source: analytics.bi_etl_runs.
    Permission: super_admin only.
    """
    await _require_super_admin(current_user)
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            rows = await session.execute(text("""
                SELECT
                    scheme_id::text,
                    target_table,
                    MAX(completed_at) AS last_run,
                    EXTRACT(EPOCH FROM (now() - MAX(completed_at)))/3600 AS hours_since,
                    COUNT(*) FILTER (WHERE status = 'error') AS error_count
                FROM analytics.bi_etl_runs
                GROUP BY scheme_id, target_table
                ORDER BY hours_since DESC NULLS LAST
                LIMIT 200
            """))
            freshness = [
                {
                    "scheme_id": r[0],
                    "target_table": r[1],
                    "last_run": str(r[2]) if r[2] else None,
                    "hours_since": round(float(r[3] or 0), 1),
                    "error_count": int(r[4] or 0),
                    "status": "ok" if (r[3] or 999) < 48 else "stale",
                }
                for r in rows.fetchall()
            ]
    except Exception as exc:
        logger.warning("platform_data_freshness failed: %s", exc)
        freshness = []
    return {
        "data": freshness,
        "meta": _meta("platform", "analytics_pg", False),
    }


@router.get("/platform/etl-health")
async def platform_etl_health(
    current_user: dict = Depends(get_approved_user),
):
    """ETL error rates and run health across all tables.

    Source: analytics.bi_etl_runs.
    Permission: super_admin only.
    """
    await _require_super_admin(current_user)
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            rows = await session.execute(text("""
                SELECT
                    target_table,
                    COUNT(*) AS total_runs,
                    COUNT(*) FILTER (WHERE status = 'success') AS success_runs,
                    COUNT(*) FILTER (WHERE status = 'error') AS error_runs,
                    MAX(completed_at) AS last_run,
                    SUM(rows_inserted) AS total_rows_inserted
                FROM analytics.bi_etl_runs
                GROUP BY target_table
                ORDER BY error_runs DESC, target_table
            """))
            health = [
                {
                    "target_table": r[0],
                    "total_runs": int(r[1] or 0),
                    "success_runs": int(r[2] or 0),
                    "error_runs": int(r[3] or 0),
                    "last_run": str(r[4]) if r[4] else None,
                    "total_rows_inserted": int(r[5] or 0),
                    "error_rate_pct": round(
                        int(r[3] or 0) / max(int(r[1] or 1), 1) * 100, 1
                    ),
                }
                for r in rows.fetchall()
            ]
    except Exception as exc:
        logger.warning("platform_etl_health failed: %s", exc)
        health = []
    return {
        "data": health,
        "meta": _meta("platform", "analytics_pg", False),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Management Entity endpoints — unified view across all management models
# Backward-compatible: agency endpoints remain; these add management_entity scope.
# ─────────────────────────────────────────────────────────────────────────────

async def _require_management_entity_access(
    management_entity_id: str, current_user: dict
) -> list[str]:
    """Verify user can access this management entity and return its building list.

    Security: validates UUID, checks toggle availability, enforces role-based
    entity access (toggle gate + membership/appointment check), and emits an
    audit event for denied access.

    UUIDs are identifiers, not authorisation — explicit access checks are always
    performed regardless of whether the caller knows the entity UUID.
    """
    from services.management_hierarchy_service import parse_uuid_or_400
    from services.bi_toggle_service import (
        resolve_management_entity_access,
        get_management_entity_buildings,
    )

    # Validate UUID before any DB interaction
    management_entity_id = parse_uuid_or_400(management_entity_id, "management_entity_id")

    role = current_user.get("effective_role") or current_user.get("role", "guest")
    user_id = str(current_user.get("_id") or current_user.get("user_id", ""))

    allowed = await resolve_management_entity_access(
        user_id=user_id,
        effective_role=role,
        management_entity_id=management_entity_id,
    )
    if not allowed:
        # Emit denial audit event
        from services.management_hierarchy_service import _emit_access_audit_event
        await _emit_access_audit_event(
            actor_user_id=user_id,
            effective_role=role,
            scheme_id=None,
            management_entity_id=management_entity_id,
            target_user_id=None,
            action="read",
            allowed=False,
            reason=f"BI access not enabled or not permitted for management entity {management_entity_id}",
        )
        raise HTTPException(
            status_code=403,
            detail=f"BI access not enabled for management entity {management_entity_id}",
        )
    return await get_management_entity_buildings(management_entity_id)


@router.get("/management-entity/{management_entity_id}/summary")
async def management_entity_summary(
    management_entity_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """High-level portfolio summary across all buildings under this management entity.

    Supports agency, self_managed_oc, and independent_manager entity types.
    """
    building_ids = await _require_management_entity_access(management_entity_id, current_user)
    from services.bi_service import get_agency_summary
    data = await get_agency_summary(management_entity_id, building_ids)
    summary = {k: v for k, v in data.items() if k != "agency_id"}
    return {
        "data": {**summary, "management_entity_id": management_entity_id},
        "meta": _meta("management_entity", "analytics_pg", False),
    }


@router.get("/management-entity/{management_entity_id}/buildings")
async def management_entity_buildings(
    management_entity_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """List buildings assigned to this management entity."""
    building_ids = await _require_management_entity_access(management_entity_id, current_user)
    return {
        "management_entity_id": management_entity_id,
        "building_ids": building_ids,
        "count": len(building_ids),
        "meta": _meta("management_entity", "operational_pg", False),
    }


@router.get("/management-entity/{management_entity_id}/financial-summary")
async def management_entity_financial_summary(
    management_entity_id: str,
    financial_year: str | None = Query(None),
    current_user: dict = Depends(get_approved_user),
):
    """Aggregated financial KPIs across all buildings in this management entity."""
    building_ids = await _require_management_entity_access(management_entity_id, current_user)
    from services.bi_service import get_portfolio_financial_summary
    data = await get_portfolio_financial_summary(management_entity_id, building_ids, financial_year)
    return {
        "management_entity_id": management_entity_id,
        "data": data,
        "meta": _meta("management_entity", "analytics_pg", False),
    }


@router.get("/management-entity/{management_entity_id}/health-ranking")
async def management_entity_health_ranking(
    management_entity_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Building health ranking across all buildings under this management entity."""
    building_ids = await _require_management_entity_access(management_entity_id, current_user)
    from services.bi_service import get_agency_health_ranking
    data = await get_agency_health_ranking(management_entity_id, building_ids)
    return {
        "management_entity_id": management_entity_id,
        "data": data,
        "meta": _meta("management_entity", "analytics_pg", False),
    }


@router.get("/management-entity/{management_entity_id}/compliance-overdue")
async def management_entity_compliance_overdue(
    management_entity_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Overdue compliance items across all buildings in this management entity."""
    building_ids = await _require_management_entity_access(management_entity_id, current_user)
    from services.bi_service import get_portfolio_compliance_overdue
    data = await get_portfolio_compliance_overdue(management_entity_id, building_ids)
    return {
        "management_entity_id": management_entity_id,
        "data": data,
        "meta": _meta("management_entity", "analytics_pg", False),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Admin: trigger ETL run for a building
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/admin/etl/run")
async def bi_etl_run(
    building_id: str,
    current_user: dict = Depends(get_approved_user),
):
    """Trigger a full nightly ETL run for a specific building.

    Permission: super_admin or strata_admin only.
    """
    await _require_bi_manage(current_user, building_id)
    try:
        from services.bi_etl_service import run_nightly_etl
        result = await run_nightly_etl(building_id)
        return {"status": "ok", "building_id": building_id, "result": result}
    except Exception as exc:
        logger.error("bi_etl_run failed for %s: %s", building_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))
