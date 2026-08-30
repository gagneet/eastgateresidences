# @featuretrace:dashboard-feed — Analytics Router: provides activity feed + maintenance stats for owner dashboard.
# Layer: router
# Data flow: OwnerDashboard.tsx + ActivityFeed.tsx → /analytics/* → analytics router aggregations → activities, maintenance_requests, amenity_bookings, unit_levy_ledger, announcements (building-scoped).
# Related: frontend/src/pages/dashboard/OwnerDashboard.tsx; frontend/src/components/dashboard/ActivityFeed.tsx; backend/routers/community_dashboard.py
# @featuretrace:fund-health — Sinking fund forecast endpoint feeds Fund Health gauge on owner dashboard.
# Layer: router
# Data flow: OwnerDashboard.tsx → /analytics/sinking-fund-forecast → annual_levies collection (building-scoped).
# Related: frontend/src/pages/dashboard/OwnerDashboard.tsx
# @featuretrace:dashboard-v2 — /analytics/dashboard-v2-extras aggregates owner auxiliary signals for the v2 Owner Dashboard.
# Layer: router
# Data flow: page.tsx → GET /analytics/dashboard-v2-extras → db.dashboard_v2_signals + db.insurance_policies (building-scoped). Per-unit overlay via unit_number param.
# Related: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx; frontend/src/app/(dashboard)/dashboard/page.tsx; backend/seeds/demo_customer.py (layer_7_dashboard_v2_signals)
# Toggle: ft_dashboard_v2

"""
Analytics Router - Property Intelligence Platform

Handles enhanced dashboard metrics, projections, and activity feeds.
"""
import logging
import asyncio
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import uuid
from pydantic import BaseModel

from database import db
from utils.auth import get_current_user, get_approved_user, is_approved_user, get_current_building, get_optional_user, \
    get_optional_building, get_building_or_400, effective_role
from utils.permissions import get_user_permissions, require_permission
from utils.document_visibility import build_document_visibility_filter
from services.finance_route_cutover_service import get_finance_route_runtime_state
from models.user import UserRole
import subprocess
from models.timestamps import timestamp_sort_key

router = APIRouter(prefix="/analytics")

# Logger
logger = logging.getLogger(__name__)


async def _governed_read_source(building_id: str, route_key: str) -> str:
    """Resolve the read source ('mongo' | 'postgres') for a governed analytics
    finance route via the shared finance route cutover engine (GAP-FIN-052).

    Replaces the former inline ``is_cutover_feature_enabled(FINANCIAL_PG_READS_ENABLED)``
    gate, which bypassed the shadow-soak/critical-diff gates every other finance
    route respects. Defaults to the safe Mongo primary if the control plane is
    unavailable — a dashboard route must never 500 because the cutover engine is
    momentarily unreachable.
    """
    try:
        state = await get_finance_route_runtime_state(
            building_id=building_id, route_key=route_key,
        )
        return state.get("source", "mongo")
    except Exception as exc:
        logger.info(
            "%s: cutover engine unavailable (%s), defaulting to MongoDB", route_key, exc
        )
        return "mongo"


async def _fire_analytics_shadow(building_id: str, route_key: str, mongo_payload: dict) -> None:
    """Fire-and-forget shadow compare for a governed analytics finance route
    (GAP-FIN-055). Never affects the live response — feeds ``core.shadow_diffs`` so
    the route accrues a shadow soak toward promotion. Runs only when the governed
    engine says ``run_shadow`` (policy ``shadow_supported=True`` + finance_ledger
    shadow enabled). The PG side is fetched by the shadow service and compared to
    ``mongo_payload`` field-for-field (Mongo AUD vs PG cents) by
    ``_compare_generic_payloads``.
    """
    try:
        state = await get_finance_route_runtime_state(
            building_id=building_id, route_key=route_key,
        )
        if state.get("run_shadow"):
            from services.finance_shadow_read_service import maybe_run_finance_shadow
            await maybe_run_finance_shadow(
                building_id=building_id, route_key=route_key, mongo_payload=mongo_payload,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("%s shadow skipped: %s", route_key, exc)


@router.get("/sinking-fund-forecast")
# @featuretrace:finance-reserve-forecast — Sinking fund / reserve forecast endpoint.
# Layer: router
# Data flow: ManagementDashboard/ManagerDashboard/OwnerDashboard -> GET
#            /analytics/sinking-fund-forecast -> sinking_fund_plan, else
#            capital_replacement_schedule, else flat (building-scoped).
# Related: backend/services/forecast_service.py::get_capital_replacement_projection
#          backend/services/analytics_pg_service.py::get_sinking_fund_forecast_pg
#          frontend/src/lib/reserve-projection.ts
#
# LESSON (2026-08-27): this response is consumed by FOUR dashboards, and the Mongo and
# Postgres paths do not agree on field names -- Mongo emits `expenses` and `closing_balance`,
# analytics_pg_service emits `balance` and no capital-works figure at all. Every consumer
# must go through normaliseReserveProjection(); a hand-rolled mapping missed `expenses`
# entirely and rendered $0.00 capital works on every building for as long as it existed.
async def get_sinking_fund_forecast(
        years: int = Query(10, ge=1, le=20),
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Generate a sinking fund / reserve projection. No figure is ever fabricated.

    **Sources, in order.** `sinking_fund_plan` (the 15-year capital plan) is preferred.
    A building without one falls back to `capital_replacement_schedule` — per-asset
    `estimated_cost` summed into each asset's `replacement_year`, with contributions
    taken from the latest approved sinking fund levy
    (`annual_levies.sinking_fund.proposed_amount_cents`) held flat across the horizon.
    Holding it flat is a stated modelling choice, not a forecast: a future levy is set by
    AGM resolution, not by indexation. A building with neither source gets a flat balance.

    **`summary.data_source`** names which of the three ran: `sinking_fund_plan`,
    `capital_replacement_schedule`, or `flat_balance`.

    **`basis`** states, per series, where the number came from — read it before quoting a
    figure from this endpoint.

    **null is not zero.** A year the capital schedule lists no asset for returns
    `expenses: 0.0` (a real zero — the schedule exists and lists nothing). A building with
    no source at all returns `expenses: null` and `contributions: null`, and a building
    with no approved levy returns `contributions: null`. Clients MUST render null as "—"
    or "not available" and never as `$0.00`; the two mean different things and a reader
    cannot tell them apart once both are money-formatted.

    **`summary.status`** is `critical` when the projected balance goes negative at any
    point in the horizon — the fund cannot cover its own scheduled works — otherwise
    `healthy`, or `unknown` when nothing is known.

    Cost is a function of asset count, not of `years`: one collection read, then an
    in-memory walk. Measured flat across a 1-to-20 year horizon.

    Source is governed by the shared finance route cutover engine
    (route_key ``analytics.sinking_fund_forecast``) — GAP-FIN-052 item 2 replaced
    the former inline ``financial_pg_reads_enabled``-only gate. PG serves only when
    the route's shadow/critical gates pass; otherwise (today) MongoDB.
    """
    # --- Postgres read path (governed) ---
    if await _governed_read_source(building_id, "analytics.sinking_fund_forecast") == "postgres":
        try:
            from services.analytics_pg_service import get_sinking_fund_forecast_pg
            return await get_sinking_fund_forecast_pg(building_id, years)
        except Exception as _pg_err:
            logger.info(
                "sinking-fund-forecast: Postgres path failed (%s), using MongoDB", _pg_err
            )

    # --- MongoDB legacy path (unchanged) ---
    from services.forecast_service import get_sinking_fund_projections

    all_rows = await get_sinking_fund_projections(building_id, max_years=20)

    # Determine current balance: latest actual year's closing balance
    current_year = datetime.now().year
    actual_rows = [r for r in all_rows if r.get("is_actual")]
    levy_balance_found = False  # tracks whether annual_levies had a sinking_fund.closing_balance record
    if actual_rows:
        current_balance = actual_rows[-1]["balance"]
    else:
        # Fall back to annual_levies
        latest_levy = await db.annual_levies.find_one(
            {"building_id": building_id},
            sort=[("year", -1)]
        )
        if latest_levy and latest_levy.get("sinking_fund"):
            sf = latest_levy["sinking_fund"]
            # closing_balance = fund balance; levy_income = YTD income (not a balance — don't use as fallback)
            closing = sf.get("closing_balance")
            if closing is not None:
                levy_balance_found = True
                current_balance = float(closing)
            else:
                current_balance = 0.0
        else:
            logger.warning(f"No sinking fund closing_balance found for building {building_id} — defaulting to 0")
            current_balance = 0.0

    # If no capital plan data at all, generate a flat CPI projection (no assumptions on contributions)
    if not all_rows:
        # No 15-year `sinking_fund_plan` for this building. Before falling back to a flat
        # line, try the per-asset `capital_replacement_schedule` — a real, separate source
        # that capital_shock_service and levy_fairness_service already treat as
        # authoritative. East Gate is precisely this shape: zero plan rows, 18 real assets.
        from services.forecast_service import get_capital_replacement_projection

        capital = await get_capital_replacement_projection(
            building_id, current_year, years, current_balance
        )
        if capital and capital["projection"]:
            projection = capital["projection"]
            contribution_known = capital["contribution"] is not None
            final_closing = projection[-1]["closing_balance"]
            # Status is derived from the two sourced series, not from an invented target:
            # if the reserve goes negative at any point it cannot fund its own scheduled
            # works. That is a real signal; "is it above $100k" is not.
            lowest = min(r["closing_balance"] for r in projection)
            await _fire_analytics_shadow(
                building_id, "analytics.sinking_fund_forecast",
                {"current_balance": current_balance},
            )
            return {
                "summary": {
                    "current_balance": current_balance,
                    "target_10y_balance": final_closing,
                    "lowest_projected_balance": lowest,
                    "status": "critical" if lowest < 0 else "healthy",
                    "data_source": "capital_replacement_schedule",
                    "data_available": True,
                },
                "projection": projection,
                "basis": {
                    # Every figure above is traceable to one of these two sources.
                    "expenses": (
                        "Per-asset estimated_cost from capital_replacement_schedule, summed "
                        "into each asset's replacement_year. A year with no asset due is a "
                        "real $0, not a missing value."
                    ),
                    "contributions": (
                        f"Latest approved sinking fund levy"
                        f"{f' (FY{capital['contribution_year']})' if capital.get('contribution_year') else ''}"
                        ", held flat across the horizon — future levies are set by AGM "
                        "resolution, so no CPI escalation is applied."
                    ) if contribution_known else (
                        "No approved sinking fund levy on record — contributions are unknown "
                        "and are reported as null, not zero."
                    ),
                    "opening_balance": "Latest actual sinking fund closing balance.",
                    "contributions_known": contribution_known,
                    "assets_total": capital["assets_total"],
                    "assets_in_horizon": sum(len(r["events"]) for r in projection),
                },
                "note": (
                    f"Projected from {capital['assets_total']} scheduled asset replacements."
                    + ("" if contribution_known else
                       " Contributions are unknown (no approved sinking fund levy on record), "
                       "so the projected balance reflects drawdowns only.")
                ),
            }

        # Last resort: neither a capital plan nor a replacement schedule exists.
        logger.warning(f"No sinking_fund_plan or capital_replacement_schedule for building {building_id}; using flat balance projection")
        # data_available is True when we have a real data source, regardless of numeric value.
        # A zero balance is valid (new scheme or fully-spent fund) — don't infer unavailability from it.
        data_available = levy_balance_found  # actual_rows is empty here since all_rows is empty
        projection = []
        balance = current_balance
        for i in range(1, years + 1):
            year = current_year + i
            # These are genuinely UNKNOWN, not zero. Emitting 0.0 here is what made the
            # dashboard render a fabricated-looking "$0.00" for every future year; null
            # forces the UI to render "—" instead. The balance therefore stays flat
            # because nothing is known to add to or subtract from it.
            projection.append({
                "year": year,
                "opening_balance": round(balance, 2),
                "contributions": None,
                "expenses": None,
                "closing_balance": round(balance, 2),
                "events": []
            })
        await _fire_analytics_shadow(
            building_id, "analytics.sinking_fund_forecast",
            {"current_balance": current_balance},
        )
        return {
            "summary": {
                "current_balance": current_balance,
                "target_10y_balance": projection[-1]["closing_balance"] if projection else 0,
                "status": "unknown",
                "data_source": "flat_balance",
                "data_available": data_available,
            },
            "projection": projection,
            "basis": {
                "expenses": "Unknown — no capital plan or replacement schedule on record.",
                "contributions": "Unknown — no capital plan or replacement schedule on record.",
                "opening_balance": "Latest actual sinking fund closing balance.",
                "contributions_known": False,
            },
            "note": "No capital plan or asset replacement schedule found. Balance shown flat; contributions and works are unknown." if data_available else
                    "No sinking fund data available for this building. Configure the 15-year capital plan to enable projections.",
        }

    # Build projection from sinking_fund_plan data, starting from current_year+1
    future_rows = [r for r in all_rows if int(r["year"]) > current_year][:years]
    projection = []
    for row in future_rows:
        projection.append({
            "year": int(row["year"]),
            "opening_balance": row["opening_balance"],
            "contributions": row.get("contribution", 0),
            "expenses": row["expenditure"],
            "closing_balance": row["balance"],
            "events": row["capital_events"],
            "is_planned": not row.get("is_actual", False),
        })

    target_balance = projection[-1]["closing_balance"] if projection else current_balance
    # Status: watch if any projected year's closing balance drops below 15% of that year's
    # expenditure. When expenses == 0 the 15% threshold is also 0, so only a negative balance
    # triggers "watch" — that is acceptable (zero-expense years are generally healthy).
    has_low_balance_year = any(
        p["closing_balance"] < (p["expenses"] * 0.15)
        for p in projection
    )
    status = "watch" if has_low_balance_year else "healthy"

    await _fire_analytics_shadow(
        building_id, "analytics.sinking_fund_forecast",
        {"current_balance": current_balance},
    )
    return {
        "summary": {
            "current_balance": current_balance,
            "target_10y_balance": target_balance,
            "status": status,
            "data_source": "15_year_capital_plan",
            "data_available": True,
        },
        "projection": projection,
        "note": "Based on the building's 15-year capital works plan."
    }


@router.get("/maintenance/spend-trend")
async def get_maintenance_spend_trend(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Get monthly maintenance spend for the last 12 months.

    PostgreSQL ops.work_orders is the primary source. MongoDB maintenance_requests
    remains a fallback until historical maintenance costs are fully migrated.
    """
    now = datetime.now(timezone.utc)
    twelve_months_ago = (now - timedelta(days=365)).replace(day=1)

    try:
        from db_postgres.repos.config_repo import resolve_scheme_context
        from db_postgres.session import async_session_context, set_tenant
        from sqlalchemy import text as pg_text

        scheme = await resolve_scheme_context(building_id)
        if scheme:
            sid = str(scheme["scheme_id"])
            tid = str(scheme["tenant_id"])
            async with async_session_context() as session:
                await set_tenant(session, tid)
                rows = await session.execute(
                    pg_text("""
                        SELECT
                            TO_CHAR(date_trunc('month', wo.created_at), 'YYYY-MM') AS month,
                            COALESCE(p.preferred_name, p.legal_name, 'Unassigned') AS vendor_name,
                            COUNT(*)::int AS jobs,
                            SUM(COALESCE(wo.approved_budget_cents, 0))::bigint AS spend_cents
                        FROM ops.work_orders wo
                        LEFT JOIN ops.vendors v ON v.vendor_id = wo.vendor_id
                        LEFT JOIN core.parties p ON p.party_id = v.party_id
                        WHERE wo.scheme_id = :sid
                          AND wo.created_at >= :since
                          AND wo.status IN ('approved', 'issued', 'completed', 'closed')
                        GROUP BY 1, 2
                        ORDER BY 1
                    """),
                    {"sid": sid, "since": twelve_months_ago},
                )
                # ops.work_orders has no due/completion timestamps yet, so there is
                # no data to compute a real on-time rate from. Omit the field (as the
                # MongoDB fallback below already does) rather than fabricate a value —
                # the frontend already treats a missing on_time as "not tracked" (—).
                pg_rows = [
                    {
                        "month": row["month"],
                        "vendor_name": row["vendor_name"],
                        "vendor": row["vendor_name"],
                        "jobs": int(row["jobs"] or 0),
                        "spend": round(float(row["spend_cents"] or 0) / 100, 2),
                        "source": "postgresql",
                    }
                    for row in rows.mappings().all()
                ]
                if pg_rows:
                    return pg_rows
    except Exception as exc:
        logger.warning("maintenance/spend-trend: PG query failed, falling back to Mongo: %s", exc)

    pipeline = [
        {"$match": {
            "building_id": building_id,
            "status": "completed",
            "completed_at": {"$gte": twelve_months_ago.isoformat()}
        }},
        {"$project": {
            "month": {"$substr": ["$completed_at", 0, 7]},
            "cost": "$actual_cost",
            "vendor": {"$ifNull": ["$vendor_name", "$assigned_vendor"]},
        }},
        {"$group": {
            "_id": {"month": "$month", "vendor": "$vendor"},
            "spend": {"$sum": "$cost"},
            "jobs": {"$sum": 1},
        }},
        {"$sort": {"_id.month": 1}}
    ]
    results = await db.maintenance_requests.aggregate(pipeline).to_list(100)
    return [
        {
            "month": r["_id"]["month"],
            "vendor": r["_id"].get("vendor") or "Unassigned",
            "vendor_name": r["_id"].get("vendor") or "Unassigned",
            "jobs": r.get("jobs", 0),
            "spend": r.get("spend", 0),
            "source": "mongodb_fallback",
        }
        for r in results
    ]


@router.get("/expenses-by-supplier")
async def get_expenses_by_supplier(
        months: int = 12,
        limit: int = 8,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Spend grouped by supplier over the last N months, from finance expense_transactions.

    This is the finance-expense counterpart to /maintenance/spend-trend (which is sourced from
    maintenance work orders). expense_transactions carry supplier_name + amount but have no
    job-count or on-time signal, so this is deliberately a SPEND-ONLY view, not a performance
    scorecard. Building-scoped; excludes test data. Sourced from the live Mongo store — a
    PostgreSQL finance.expense_transactions path can be added at cutover.
    """
    months = max(1, min(int(months or 12), 36))
    now = datetime.now(timezone.utc)
    since_iso = (now - timedelta(days=31 * months)).strftime("%Y-%m-%d")

    supplier_expr = {"$ifNull": ["$supplier_name",
                                 {"$ifNull": ["$vendor_name", {"$ifNull": ["$payee", "Unattributed"]}]}]}
    pipeline = [
        {"$match": {"building_id": building_id, "is_test_data": {"$ne": True},
                    "date": {"$gte": since_iso}}},
        {"$group": {
            "_id": {"supplier": supplier_expr,
                    "month": {"$substr": [{"$ifNull": ["$date", ""]}, 0, 7]}},
            "spend": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}},
            "count": {"$sum": 1},
        }},
        {"$sort": {"_id.month": 1}},
    ]
    rows = await db.expense_transactions.aggregate(pipeline).to_list(2000)

    by_supplier: dict = {}
    for r in rows:
        sup = r["_id"].get("supplier") or "Unattributed"
        month = r["_id"].get("month") or ""
        entry = by_supplier.setdefault(
            sup, {"supplier": sup, "spend": 0.0, "transactions": 0, "_months": {}}
        )
        entry["spend"] += float(r.get("spend") or 0)
        entry["transactions"] += int(r.get("count") or 0)
        if month:
            entry["_months"][month] = entry["_months"].get(month, 0.0) + float(r.get("spend") or 0)

    result = []
    for entry in by_supplier.values():
        months_series = sorted(entry.pop("_months").items())
        entry["spend"] = round(entry["spend"], 2)
        entry["trend"] = [round(v, 2) for _, v in months_series]
        result.append(entry)
    result.sort(key=lambda e: e["spend"], reverse=True)

    capped = result[: max(1, min(int(limit or 8), 50))]
    return {
        "window_months": months,
        "suppliers": capped,
        "total_spend": round(sum(e["spend"] for e in result), 2),
        "supplier_count": len(result),
        "source": "mongodb.expense_transactions",
    }


@router.get("/maintenance/heatmap")
async def get_maintenance_heatmap(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Get frequency of issues by category."""
    pipeline = [
        {"$match": {"building_id": building_id}},
        {"$group": {"_id": "$category", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}
    ]
    results = await db.maintenance_requests.aggregate(pipeline).to_list(100)
    return [{"category": r["_id"], "count": r["count"]} for r in results]


@router.get("/maintenance/stats")
async def get_maintenance_analytics(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Detailed maintenance analytics including vendor performance."""
    # Performance Optimization⚡: Consolidated maintenance stats into a single aggregation pipeline using $facet
    # and parallelized with vendor aggregation using asyncio.gather.
    # This reduces database round-trips from 6 to 1 concurrent set (2 calls).
    maint_pipeline = [
        {"$match": {"building_id": building_id}},
        {"$facet": {
            "spent": [
                {"$match": {"status": "completed"}},
                {"$group": {"_id": None, "total": {"$sum": "$actual_cost"}}}
            ],
            "heatmap": [
                {"$group": {"_id": "$category", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$limit": 100}
            ],
            "counts": [
                {"$group": {
                    "_id": None,
                    "total": {"$sum": 1},
                    "completed": {"$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}},
                    "in_progress": {"$sum": {"$cond": [{"$eq": ["$status", "in_progress"]}, 1, 0]}}
                }}
            ]
        }}
    ]

    vendor_pipeline = [
        {"$match": {"building_id": building_id}},
        {"$group": {
            "_id": "$assigned_vendor",
            "vendor_name": {"$first": "$vendor_name"},
            "jobs_count": {"$sum": 1},
            "avg_cost": {"$avg": "$estimated_cost"}
        }},
        {"$match": {"_id": {"$ne": None}}}
    ]

    # Run maintenance and vendor aggregations in parallel
    maint_task = db.maintenance_requests.aggregate(maint_pipeline).to_list(1)
    vendor_task = db.work_orders.aggregate(vendor_pipeline).to_list(100)

    maint_res, vendors = await asyncio.gather(maint_task, vendor_task)

    # Process maintenance results
    m_data = maint_res[0] if maint_res else {}

    # Robust handling for facet results that might be empty lists
    spent_list = m_data.get("spent", [])
    total_spent = spent_list[0].get("total", 0) if spent_list else 0

    heatmap_raw = m_data.get("heatmap", [])

    counts_list = m_data.get("counts", [])
    counts = counts_list[0] if counts_list else {}

    total = counts.get("total", 0)
    completed = counts.get("completed", 0)
    in_progress = counts.get("in_progress", 0)

    return {
        "total_requests": total,
        "completed": completed,
        "in_progress": in_progress,
        "total_spent": total_spent,
        "vendors": vendors,
        "heatmap": [{"category": r["_id"], "count": r["count"]} for r in heatmap_raw]
    }


@router.get("/maintenance-stats")
async def get_maintenance_stats(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Building-wide maintenance metrics for dashboard transparency.
    """
    now = datetime.now(timezone.utc)
    month_ago = (now - timedelta(days=30)).isoformat()

    # Performance Optimization⚡: Consolidated all maintenance stats into a single aggregation pipeline using $facet.
    # This reduces database round-trips from 3 to 1, significantly improving dashboard load time.
    pipeline = [
        {"$match": {"building_id": building_id}},
        {"$facet": {
            "open_stats": [
                {"$match": {"status": {"$in": ["submitted", "under_review", "approved", "in_progress"]}}},
                {"$count": "count"}
            ],
            "closed_stats": [
                {"$match": {"status": "completed", "completed_at": {"$gte": month_ago}}},
                {"$count": "count"}
            ],
            # Requests that are still open and already past their SLA due date right now —
            # the count BuildingStrengthCard's "Maintenance SLA" checklist item needs.
            # sla_compliance_rate (below) is a historical 30-day rate, not a live breach count.
            "sla_breach_stats": [
                {"$match": {
                    "status": {"$in": ["submitted", "under_review", "approved", "in_progress"]},
                    "sla_due_at": {"$exists": True, "$ne": None},
                }},
                {"$project": {
                    "breached": {
                        "$lt": [
                            {"$dateFromString": {"dateString": "$sla_due_at"}},
                            now,
                        ]
                    }
                }},
                {"$match": {"breached": True}},
                {"$count": "count"}
            ],
            "avg_stats": [
                {"$match": {
                    "status": "completed",
                    "completed_at": {"$gte": month_ago},
                    "created_at": {"$exists": True}
                }},
                {"$project": {
                    "duration": {
                        "$divide": [
                            {"$subtract": [
                                {"$dateFromString": {"dateString": "$completed_at"}},
                                {"$dateFromString": {"dateString": "$created_at"}}
                            ]},
                            1000 * 60 * 60 * 24  # ms to days
                        ]
                    }
                }},
                {"$group": {
                    "_id": None,
                    "avg_days": {"$avg": "$duration"}
                }}
            ]
        }}
    ]

    # Previous 30-day window for trend comparison
    two_months_ago = (now - timedelta(days=60)).isoformat()
    prev_pipeline = [
        {"$match": {"building_id": building_id}},
        {"$facet": {
            "prev_avg_stats": [
                {"$match": {
                    "status": "completed",
                    "completed_at": {"$gte": two_months_ago, "$lt": month_ago},
                    "created_at": {"$exists": True}
                }},
                {"$project": {
                    "duration": {
                        "$divide": [
                            {"$subtract": [
                                {"$dateFromString": {"dateString": "$completed_at"}},
                                {"$dateFromString": {"dateString": "$created_at"}}
                            ]},
                            1000 * 60 * 60 * 24
                        ]
                    }
                }},
                {"$group": {"_id": None, "avg_days": {"$avg": "$duration"}}}
            ],
            "sla_stats": [
                {"$match": {
                    "status": "completed",
                    "completed_at": {"$gte": month_ago},
                    "sla_due_at": {"$exists": True}
                }},
                {"$project": {
                    "met_sla": {
                        "$lte": [
                            {"$dateFromString": {"dateString": "$completed_at"}},
                            {"$dateFromString": {"dateString": "$sla_due_at"}}
                        ]
                    }
                }},
                {"$group": {
                    "_id": None,
                    "total": {"$sum": 1},
                    "met": {"$sum": {"$cond": ["$met_sla", 1, 0]}}
                }}
            ]
        }}
    ]

    try:
        results = await db.maintenance_requests.aggregate(pipeline).to_list(1)
        if not results:
            raise ValueError("No results from aggregation")

        res = results[0]

        # Extract values with appropriate fallbacks
        open_count = res["open_stats"][0]["count"] if res.get("open_stats") and len(res["open_stats"]) > 0 else 0
        closed_this_month = res["closed_stats"][0]["count"] if res.get("closed_stats") and len(
            res["closed_stats"]) > 0 else 0
        sla_breaches = res["sla_breach_stats"][0]["count"] if res.get("sla_breach_stats") and len(
            res["sla_breach_stats"]) > 0 else 0

        if res.get("avg_stats") and len(res["avg_stats"]) > 0 and res["avg_stats"][0].get("avg_days") is not None:
            avg_days = round(max(0, res["avg_stats"][0]["avg_days"]), 1)
        else:
            # None, not 5.2. This was a hardcoded placeholder that a building with
            # NO maintenance requests at all still displayed as "Resolution Time
            # 5.2d" — a measurement nobody took, indistinguishable on screen from
            # a real one. CLAUDE.md is explicit that a last-resort fallback is
            # 0.0/None plus a warning, never an invented figure.
            avg_days = None
            logger.warning(
                "maintenance stats: no resolution-time data for building=%s; "
                "returning None so the UI shows 'no data' rather than a number",
                building_id,
            )

        # Compute trend vs previous period
        prev_results = await db.maintenance_requests.aggregate(prev_pipeline).to_list(1)
        prev_avg_days = None
        # Likewise 94.5 — an invented SLA compliance rate is worse than no rate,
        # because it reads as a passing grade nobody earned.
        sla_compliance_rate = None
        if prev_results:
            pr = prev_results[0]
            if pr.get("prev_avg_stats") and pr["prev_avg_stats"] and pr["prev_avg_stats"][0].get("avg_days"):
                prev_avg_days = round(max(0, pr["prev_avg_stats"][0]["avg_days"]), 1)
            if pr.get("sla_stats") and pr["sla_stats"] and pr["sla_stats"][0].get("total", 0) > 0:
                s = pr["sla_stats"][0]
                sla_compliance_rate = round((s["met"] / s["total"]) * 100, 1)

        # trend_pct: negative = improving (faster resolution); positive = worsening.
        # Requires BOTH periods to be real measurements — a trend against an
        # invented baseline is a fabricated direction, not just a fabricated
        # number. None means "no comparable prior period".
        if avg_days is not None and prev_avg_days is not None and prev_avg_days > 0:
            trend_pct = round(((avg_days - prev_avg_days) / prev_avg_days) * 100, 1)
        else:
            trend_pct = None

    except Exception as e:
        logger.error(f"Error calculating maintenance stats via consolidated aggregation: {e}")
        open_count = 0
        closed_this_month = 0
        # A failed aggregation is "unknown", never a plausible-looking default.
        # The previous values (5.2 days, 94.5% SLA) made an outage look like a
        # healthy building.
        avg_days = None
        trend_pct = None
        sla_compliance_rate = None
        sla_breaches = 0

    return {
        "open_requests": open_count,
        "closed_this_month": closed_this_month,
        "avg_resolution_days": avg_days,
        "trend_pct": trend_pct,
        "sla_compliance_rate": sla_compliance_rate,
        "sla_breaches": sla_breaches,
    }


@router.get("/activities")
async def get_activities(
        limit: int = Query(20, ge=1, le=50),
        offset: int = 0,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Paginated community activity feed aggregated from real DB collections.
    Falls back to seeded activities if available, otherwise generates from live data.
    """
    # PostgreSQL primary path.
    try:
        from db_postgres.repos.config_repo import resolve_scheme_context
        from db_postgres.session import async_session_context, set_tenant
        from sqlalchemy import text

        scheme = await resolve_scheme_context(building_id)
        if scheme:
            sid = str(scheme["scheme_id"])
            tid = str(scheme["tenant_id"])
            async with async_session_context() as session:
                await set_tenant(session, tid)
                # Build a compact cross-domain feed from core PG tables.
                #
                # Every column reference here was wrong until 2026-08-28, so this
                # entire branch raised UndefinedColumnError on the first row it
                # touched, was swallowed by the `except Exception` below, logged at
                # INFO, and silently fell through to MongoDB on EVERY request since
                # it was written. Verified against information_schema:
                #   ops.cases            has `title`, NOT `summary`
                #   finance.receipts     has NO `is_test_data` column at all
                #                        (it uses `retired_at IS NULL` for liveness)
                #   compliance_items     has `item_description`, NOT `title`/`item_type`
                # A broken branch that fails closed is still broken: nobody could
                # see it, and the fallback masked it indefinitely.
                #
                # The feed is also BALANCED per source rather than globally sorted.
                # East Gate holds 2,233 receipts against a handful of announcements,
                # so a plain ORDER BY created_at DESC returns nothing but payments —
                # correct by the query, useless as a community feed.
                per_type = max(2, int(limit) // 3)
                rows = await session.execute(
                    text("""
                        WITH cases AS (
                            SELECT 'case'::text AS type,
                                   c.title AS title,
                                   c.created_at AS created_at
                            FROM ops.cases c
                            WHERE c.scheme_id = CAST(:sid AS UUID)
                              AND COALESCE(c.is_test_data, FALSE) = FALSE
                              AND c.deleted_at IS NULL
                              AND c.created_at IS NOT NULL
                            ORDER BY c.created_at DESC
                            LIMIT :per_type
                        ),
                        payments AS (
                            SELECT 'payment'::text AS type,
                                   ('Levy payment received: $'
                                    || ROUND((r.amount_cents / 100.0)::numeric, 2)::text) AS title,
                                   r.created_at AS created_at
                            FROM finance.receipts r
                            WHERE r.scheme_id = CAST(:sid AS UUID)
                              AND r.retired_at IS NULL
                              AND r.created_at IS NOT NULL
                            ORDER BY r.created_at DESC
                            LIMIT :per_type
                        ),
                        compliance AS (
                            SELECT 'compliance'::text AS type,
                                   ('Compliance update: '
                                    || COALESCE(ci.item_description, 'Compliance item')) AS title,
                                   ci.updated_at AS created_at
                            FROM compliance.compliance_items ci
                            WHERE ci.scheme_id = CAST(:sid AS UUID)
                              AND COALESCE(ci.is_test_data, FALSE) = FALSE
                              AND ci.updated_at IS NOT NULL
                            ORDER BY ci.updated_at DESC
                            LIMIT :per_type
                        ),
                        announcements AS (
                            SELECT 'announcement'::text AS type,
                                   COALESCE(a.title, 'Announcement') AS title,
                                   a.published_at AS created_at
                            FROM communications.announcements a
                            WHERE a.scheme_id = CAST(:sid AS UUID)
                              AND COALESCE(a.is_test_data, FALSE) = FALSE
                              AND a.published_at IS NOT NULL
                            ORDER BY a.published_at DESC
                            LIMIT :per_type
                        )
                        SELECT * FROM (
                            SELECT * FROM cases
                            UNION ALL SELECT * FROM payments
                            UNION ALL SELECT * FROM compliance
                            UNION ALL SELECT * FROM announcements
                        ) feed
                        ORDER BY created_at DESC
                        LIMIT :lim OFFSET :off
                    """),
                    {"sid": sid, "lim": int(limit), "off": int(offset), "per_type": per_type},
                )
                pg_items = rows.fetchall()

            if pg_items:
                return [
                    {
                        "type": row.type,
                        "title": row.title,
                        "created_at": row.created_at.isoformat() if hasattr(row.created_at, "isoformat") else str(row.created_at),
                        "visibility": "all",
                        "source": "postgres",
                    }
                    for row in pg_items
                ]
    except Exception as _pg_err:
        logger.info("analytics/activities: Postgres path unavailable (%s), using MongoDB", _pg_err)

    # Visibility logic.
    #
    # "See everything" means every VISIBILITY within this building — never every
    # building. The privileged branch used to replace the whole filter with `{}`,
    # which dropped `building_id` as well and made the feed cross-tenant for any
    # super_admin, ec_member or strata_manager. `db.activities` is a scoped
    # collection, so TenantScopedDatabase re-injects the building on the way out,
    # but relying on that leaves the intent wrong at the call site and the query
    # wrong on its face. Scope is not a visibility level.
    #
    # `is_test_data` is filtered here for the same reason every production read
    # filters it. It was missing, and db.activities had accumulated 8,066 rows of
    # which 8,009 belonged to a fixture building_id of "eastgate" and 51 more were
    # orphaned test announcements in 13195 — none of them flagged, so nothing could
    # exclude them. Those rows are purged separately by
    # scripts/data_repair/purge_test_activities_20260828.py; this filter is what
    # stops the collection refilling.
    query = {"building_id": building_id, "is_test_data": {"$ne": True}}
    is_privileged = effective_role(current_user) in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]
    if not is_privileged:
        query["visibility"] = "all"

    activities = await db.activities.find(query, {"_id": 0}) \
        .sort("created_at", -1) \
        .skip(offset) \
        .limit(limit) \
        .to_list(limit)

    # If activities collection is empty, generate from real data
    if not activities:
        activities = []
        now = datetime.now(timezone.utc)

        # Performance Optimization⚡: Parallelize fallback activity queries to reduce latency.
        # Ensure private items are not leaked to non-privileged users - Security 🛡️
        # Every fallback query excludes test-created records. Without this the feed
        # advertised rows that the destination pages correctly hide, so each item
        # dead-ended (CLAUDE.md: production queries filter is_test_data).
        _not_test = {"is_test_data": {"$ne": True}}
        maint_query = {"building_id": building_id, **_not_test}
        ann_query = {"building_id": building_id, **_not_test}
        doc_query: dict = {"building_id": building_id, **_not_test}
        list_query = {"building_id": building_id, "status": "active", **_not_test}

        # Documents use the SAME visibility rule as /documents. Previously this
        # applied `is_public: True` for non-privileged users and NOTHING AT ALL for
        # privileged ones, while /documents filtered on a different field set
        # entirely — so the feed surfaced 242 East Gate levy notices that the
        # documents page would not show, every link landing on an empty page.
        doc_visibility = build_document_visibility_filter(current_user)
        if doc_visibility:
            doc_query["$and"] = [doc_visibility]

        if not is_privileged:
            ann_query["is_public"] = True
            list_query["is_public"] = True
            if current_user.get("unit_number"):
                maint_query["unit_number"] = current_user["unit_number"]
            else:
                maint_query["category"] = "common_area"

        booking_query = {"building_id": building_id, **_not_test}
        if not is_privileged and current_user.get("unit_number"):
            booking_query["unit_number"] = current_user["unit_number"]

        tasks = [
            db.announcements.find(ann_query, {"_id": 0, "id": 1, "title": 1, "created_at": 1})
            .sort("created_at", -1).limit(5).to_list(5),
            db.maintenance_requests.find(maint_query, {"_id": 0, "id": 1, "title": 1, "status": 1, "created_at": 1})
            .sort("created_at", -1).limit(5).to_list(5),
            db.documents.find(doc_query, {"_id": 0, "id": 1, "title": 1, "created_at": 1})
            .sort("created_at", -1).limit(3).to_list(3),
            db.listings.find(list_query, {"_id": 0, "id": 1, "title": 1, "created_at": 1})
            .sort("created_at", -1).limit(2).to_list(2),
            db.amenity_bookings.find(booking_query,
                                     {"_id": 0, "amenity_name": 1, "unit_number": 1, "start_time": 1, "created_at": 1})
            .sort("created_at", -1).limit(3).to_list(3),
        ]

        announcements, maintenance, docs, listings, bookings = await asyncio.gather(*tasks)

        # Recent announcements
        for a in announcements:
            activities.append({
                "type": "announcement",
                # entity_id was never projected or emitted, so EVERY activity fell
                # back to a generic list page — including maintenance, whose link
                # is written to deep-link when it has one. A feed item that lands
                # on a list the user then has to search is the dead-tile problem
                # in a different shape.
                "entity_id": a.get("id"),
                "title": a.get("title", "New Announcement"),
                "created_at": a.get("created_at", now.isoformat()),
                "visibility": "all"
            })

        # Recent maintenance requests (building-wide for privileged, own unit only otherwise)
        for m in maintenance:
            activities.append({
                "type": "maintenance",
                # entity_id was never projected or emitted, so EVERY activity fell
                # back to a generic list page — including maintenance, whose link
                # is written to deep-link when it has one. A feed item that lands
                # on a list the user then has to search is the dead-tile problem
                # in a different shape.
                "entity_id": m.get("id"),
                "title": f"Maintenance: {m.get('title', 'Request')} [{m.get('status', 'submitted').replace('_', ' ').title()}]",
                "created_at": m.get("created_at", now.isoformat()),
                "visibility": "all"
            })

        # Recent documents uploaded
        for d in docs:
            activities.append({
                "type": "document",
                # entity_id was never projected or emitted, so EVERY activity fell
                # back to a generic list page — including maintenance, whose link
                # is written to deep-link when it has one. A feed item that lands
                # on a list the user then has to search is the dead-tile problem
                # in a different shape.
                "entity_id": d.get("id"),
                "title": f"Document uploaded: {d.get('title', 'New Document')}",
                "created_at": d.get("created_at", now.isoformat()),
                "visibility": "all"
            })

        # Recent marketplace listings
        for l in listings:
            activities.append({
                "type": "marketplace",
                # entity_id was never projected or emitted, so EVERY activity fell
                # back to a generic list page — including maintenance, whose link
                # is written to deep-link when it has one. A feed item that lands
                # on a list the user then has to search is the dead-tile problem
                # in a different shape.
                "entity_id": l.get("id"),
                "title": f"New listing: {l.get('title', 'Listing')}",
                "created_at": l.get("created_at", now.isoformat()),
                "visibility": "all"
            })

        # Recent amenity bookings
        for b in bookings:
            amenity = b.get("amenity_name", "Amenity")
            unit = b.get("unit_number", "")
            activities.append({
                "type": "booking",
                "title": f"Booking: {amenity}" + (f" — Unit {unit}" if unit else ""),
                "created_at": b.get("created_at", now.isoformat()),
                "visibility": "all"
            })

        # Upcoming levy due dates — surface next due dates for current user's unit
        if current_user.get("unit_number"):
            levy_doc = await db.unit_levy_ledger.find_one(
                {"building_id": building_id, "unit_number": current_user["unit_number"]},
                {"_id": 0, "next_due_date": 1, "outstanding_balance": 1}
            )
            if levy_doc and levy_doc.get("next_due_date") and levy_doc.get("outstanding_balance", 0) > 0:
                activities.append({
                    "type": "levy_due",
                    "title": f"Levy payment due: {levy_doc['next_due_date'][:10]} — ${levy_doc['outstanding_balance']:.2f} outstanding",
                    "created_at": now.isoformat(),
                    "visibility": "all"
                })

        # Sort all by created_at descending
        # timestamp_sort_key, not the raw value: this list is assembled from several
        # collections plus synthesised entries, so it holds datetimes from one writer,
        # ISO strings from another and "" for rows missing the field. Comparing any two
        # of those three raised TypeError and 500'd the whole feed.
        activities.sort(key=lambda x: timestamp_sort_key(x.get("created_at")), reverse=True)
        activities = activities[:limit]

    return activities


@router.get("/market-snapshot")
async def get_market_snapshot(
        suburb: str = "Denman Prospect",
        building_id: str = Depends(get_building_or_400),
        current_user: Optional[dict] = Depends(get_optional_user)
):
    """
    Retrieve static market snapshot for suburb. Scoped to building context.
    """
    snapshot = await db.market_snapshots.find_one({"building_id": building_id, "suburb": suburb}, {"_id": 0})
    if not snapshot:
        # Return default if not found
        return {
            "suburb": suburb,
            "median_price": 975000,
            "rental_yield": 4.2,
            "days_on_market": 34,
            "growth_yoy": 3.8,
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
    return snapshot


@router.get("/personal-badges")
async def get_personal_badges(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    On-the-fly badge calculation for gamification.
    """
    badges = []

    # Performance Optimization⚡: Parallelize badge criterion checks to improve response time.
    unit_num = current_user.get("unit_number")
    tasks = []

    # 1. 100% Paid On Time (Check unit_levy_ledger)
    if unit_num:
        # Support both string and int unit numbers
        unit_query = {"$or": [{"unit_number": str(unit_num)}, {"unit_number": int(unit_num)}]} if str(
            unit_num).isdigit() else {"unit_number": str(unit_num)}
        tasks.append(db.unit_levy_ledger.find({
            "building_id": building_id,
            **unit_query,
            "net_balance": {"$lte": 0}
        }).to_list(10))
    else:
        tasks.append(asyncio.sleep(0, result=[]))  # Dummy task returning empty list

    # 2. Community Contributor (Check Marketplace/Blog)
    tasks.append(db.listings.count_documents({"building_id": building_id, "created_by": current_user["id"]}))

    ledger_entries, marketplace_count = await asyncio.gather(*tasks)

    # 1. 100% Paid On Time (Check unit_levy_ledger)
    if unit_num:
        if len(ledger_entries) >= 3:
            badges.append({
                "id": "perfect_payment",
                "label": "Perfect Payment Record",
                "icon": "medal",
                "description": "3+ years of on-time levy payments"
            })
        elif len(ledger_entries) >= 1:
            badges.append({
                "id": "paid_up",
                "label": "Fully Paid 2025",
                "icon": "check-circle",
                "description": "All 2025 levies paid in full"
            })

    # 2. Community Contributor (Check Marketplace/Blog)
    if marketplace_count > 0:
        badges.append({
            "id": "marketplace_seller",
            "label": "Community Seller",
            "icon": "shopping-bag",
            "description": "Contributed to the community marketplace"
        })

    # 3. Long-term Resident (Join Date)
    created_at = current_user.get("created_at")
    if created_at:
        join_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        if (datetime.now(timezone.utc) - join_date).days > 365:
            badges.append({
                "id": "veteran",
                "label": "Resident Pioneer",
                "icon": "award",
                "description": "Member of East Gate for over a year"
            })

    return badges


@router.get("/compliance-summary")
async def get_compliance_summary(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Building compliance summary - totals by status for dashboard KPI.
    Performance Optimization⚡ to use a single aggregation instead of 4 separate counts.

    PG read path: active when ``governance_read_pg_enabled`` toggle is ON.
    Falls back to MongoDB when Postgres data is unavailable.
    """
    # --- Postgres read path ---
    try:
        from services.cutover_config_service import (
            is_cutover_feature_enabled,
            GOVERNANCE_READ_PG_ENABLED,
        )
        if await is_cutover_feature_enabled(building_id, GOVERNANCE_READ_PG_ENABLED):
            from services.analytics_pg_service import get_compliance_summary_pg
            return await get_compliance_summary_pg(building_id)
    except Exception as _pg_err:
        logger.info(
            "compliance-summary: Postgres path unavailable (%s), using MongoDB", _pg_err
        )

    # --- MongoDB legacy path (unchanged) ---
    # Use aggregation to get counts for all statuses in one DB round-trip
    pipeline = [
        {"$match": {"building_id": building_id}},
        {"$group": {"_id": {"$ifNull": ["$status", "unknown"]}, "count": {"$sum": 1}}}
    ]

    try:
        results = await db.compliance_items.aggregate(pipeline).to_list(100)
        status_counts = {r["_id"]: r["count"] for r in results}

        completed = status_counts.get("completed", 0)
        pending = status_counts.get("pending", 0)
        overdue = status_counts.get("overdue", 0)
        # Total is the sum of all status counts
        total = sum(status_counts.values())
    except Exception as e:
        logger.error(f"Error calculating compliance summary: {e}")
        # Graceful fallback to individual counts if aggregation fails
        total = await db.compliance_items.count_documents({"building_id": building_id})
        completed = await db.compliance_items.count_documents({"building_id": building_id, "status": "completed"})
        pending = await db.compliance_items.count_documents({"building_id": building_id, "status": "pending"})
        overdue = await db.compliance_items.count_documents({"building_id": building_id, "status": "overdue"})

    if total == 0:
        return {"total": 0, "completed": 0, "pending": 0, "overdue": 0, "status": "unknown", "label": "No Data"}

    if overdue > 0:
        status = "danger"
        label = f"{overdue} Overdue"
    elif pending > 0:
        status = "warning"
        label = f"{pending} Pending"
    else:
        status = "healthy"
        label = "All Clear"

    today = datetime.now(timezone.utc).date()

    # ── FY window from settings (never hardcoded) ─────────────────────────────
    fy_start_month = 1  # default Jan; overridden by building settings
    try:
        from services.settings_service import get_general_settings_or_default
        gs = await get_general_settings_or_default(building_id)
        fy_start_month = int(gs.get("financial_year_start_month") or gs.get("fy_start_month") or 1)
    except Exception:
        pass

    # Compute current FY start (inclusive) and end (exclusive, first day of next FY).
    # fy_end_dt is the first day of the *following* FY, used as the exclusive upper
    # bound in range queries: due_date >= fy_start AND due_date < fy_end_dt.
    cur_year = today.year
    from datetime import date as _date
    if today.month >= fy_start_month:
        fy_start  = _date(cur_year,     fy_start_month, 1)
        fy_end_dt = _date(cur_year + 1, fy_start_month, 1)
    else:
        fy_start  = _date(cur_year - 1, fy_start_month, 1)
        fy_end_dt = _date(cur_year,     fy_start_month, 1)

    fy_start_iso = fy_start.isoformat()
    fy_end_iso   = fy_end_dt.isoformat()

    # ── Upcoming items: compliance + PPM merged, FY-scoped ────────────────────
    upcoming = []
    try:
        # Compliance items due within current FY (or overdue)
        compliance_docs = await db.compliance_items.find(
            {
                "building_id": building_id,
                "status": {"$nin": ["completed"]},
                "is_test_data": {"$ne": True},
                "$or": [
                    {"due_date": {"$lt": today.isoformat()}},             # overdue (any year)
                    {"due_date": {"$gte": fy_start_iso, "$lt": fy_end_iso}},  # due in FY
                ],
            },
            {"_id": 0, "id": 1, "title": 1, "label": 1, "status": 1,
             "risk_level": 1, "due_date": 1, "severity": 1},
        ).to_list(50)

        for item in compliance_docs:
            due_raw = str(item.get("due_date") or "")[:10]
            days_left = None
            try:
                days_left = (_date.fromisoformat(due_raw) - today).days
            except Exception:
                pass
            upcoming.append({
                "id": item.get("id"),
                "title": item.get("title") or "Compliance item",
                "label": item.get("label") or item.get("title") or "Compliance item",
                "status": item.get("status", "pending"),
                "risk_level": item.get("risk_level") or item.get("severity"),
                "due_date": due_raw or item.get("due_date"),
                "due": due_raw or item.get("due_date"),
                "days_left": days_left,
                "daysLeft": days_left,
                "source": "compliance",
            })

        # PPM items due within current FY (or overdue)
        try:
            ppm_docs = await db.ppm_items.find(
                {
                    "building_id": building_id,
                    "status": {"$nin": ["completed", "cancelled"]},
                    "is_test_data": {"$ne": True},
                    "$or": [
                        {"scheduled_date": {"$lt": today.isoformat()}},
                        {"due_date": {"$lt": today.isoformat()}},
                        {"scheduled_date": {"$gte": fy_start_iso, "$lt": fy_end_iso}},
                        {"due_date": {"$gte": fy_start_iso, "$lt": fy_end_iso}},
                    ],
                },
                {"_id": 0, "id": 1, "title": 1, "task_name": 1, "status": 1,
                 "risk_level": 1, "severity": 1, "scheduled_date": 1, "due_date": 1},
            ).to_list(30)

            for ppm in ppm_docs:
                due_raw = str(
                    ppm.get("scheduled_date") or ppm.get("due_date") or ""
                )[:10]
                days_left = None
                try:
                    days_left = (_date.fromisoformat(due_raw) - today).days
                except Exception:
                    pass
                title = ppm.get("title") or ppm.get("task_name") or "PPM task"
                upcoming.append({
                    "id": ppm.get("id"),
                    "title": title,
                    "label": title,
                    "status": ppm.get("status", "scheduled"),
                    "risk_level": ppm.get("risk_level") or ppm.get("severity"),
                    "due_date": due_raw,
                    "due": due_raw,
                    "days_left": days_left,
                    "daysLeft": days_left,
                    "source": "ppm",
                })
        except Exception as ppm_exc:
            logger.debug("compliance-summary: PPM fetch skipped: %s", ppm_exc)

        # Sort: overdue first (days_left < 0), then nearest due, then risk
        _RISK_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, None: 4}
        upcoming.sort(key=lambda x: (
            0 if (x.get("days_left") or 0) < 0 else 1,  # overdue first
            x.get("days_left") if x.get("days_left") is not None else 9999,
            _RISK_ORDER.get((x.get("risk_level") or "").lower(), 4),
        ))
        upcoming = upcoming[:12]

    except Exception as exc:
        logger.warning("compliance-summary: upcoming item fetch failed: %s", exc)

    return {
        "total": total,
        "completed": completed,
        "pending": pending,
        "overdue": overdue,
        "status": status,
        "label": label,
        "percentage": round((completed / total) * 100) if total else 0,
        "fy_start": fy_start_iso,
        "fy_end": fy_end_iso,
        "fy_start_month": fy_start_month,
        "upcoming": upcoming,
        "items": upcoming,
    }


@router.get("/marketplace-stats")
async def get_marketplace_stats(
        days: int = Query(7, ge=1, le=90),
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Marketplace listing stats for dashboard widget.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Performance Optimization⚡: Parallelize marketplace count queries.
    tasks = [
        db.listings.count_documents({"building_id": building_id, "status": "active"}),
        db.listings.count_documents({
            "building_id": building_id,
            "status": "active",
            "created_at": {"$gte": since}
        })
    ]
    total_active, new_this_period = await asyncio.gather(*tasks)

    return {
        "total_active": total_active,
        "new_this_period": new_this_period,
        "period_days": days
    }


@router.get("/levy-benchmarks")
async def get_levy_benchmarks(
        financial_year: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Dynamic levy trend data from DB including ACT Median comparison.
    Shows Building total and Personal share based on Unit Entitlement.

    Source is governed by the shared finance route cutover engine
    (route_key ``analytics.levy_benchmarks``) — GAP-FIN-052 item 2 replaced the
    former inline ``financial_pg_reads_enabled``-only gate. PG serves only when
    the route's shadow/critical gates pass; otherwise (today) MongoDB.
    """
    # --- Postgres read path (governed) ---
    if await _governed_read_source(building_id, "analytics.levy_benchmarks") == "postgres":
        try:
            from services.analytics_pg_service import get_levy_benchmarks_pg
            return await get_levy_benchmarks_pg(
                building_id, current_user.get("unit_number")
            )
        except Exception as _pg_err:
            logger.info(
                "levy-benchmarks: Postgres path failed (%s), using MongoDB", _pg_err
            )

    # --- MongoDB legacy path (unchanged) ---
    # Performance Optimization⚡: Parallelize levy data and unit entitlement fetch.
    unit_number = current_user.get("unit_number")

    tasks = [
        db.annual_levies.find(
            {"building_id": building_id},
            {
                "year": 1, "admin_fund": 1, "sinking_fund": 1,
                "admin_levy_per_uoe_annual": 1, "sinking_levy_per_uoe_annual": 1,
                "proposed_admin_expenses": 1, "proposed_sinking_expenses": 1,
                "total_uoe": 1
            }
        ).sort("year", 1).to_list(20)
    ]

    if unit_number:
        tasks.append(db.units.find_one({"building_id": building_id, "unit_number": unit_number}, {"entitlement": 1}))
    else:
        tasks.append(asyncio.sleep(0, result=None))

    levies, unit = await asyncio.gather(*tasks)

    # Personal share calculation
    entitlement = 0  # 0 = unknown; don't assume any building-specific default
    if unit:
        entitlement = unit.get("entitlement", 0)
    # Derive total_uoe from levy data if unit entitlement not available
    total_uoe = 10000  # default; overridden below if DB has it
    share_pct = 0.0

    results = []

    if not levies:
        # No annual_levies data exists for this building — return empty rather than
        # fabricating building-specific amounts that would mislead other buildings.
        logger.warning(f"No annual_levies found for building {building_id} — returning empty levy benchmark")
        await _fire_analytics_shadow(
            building_id, "analytics.levy_benchmarks", {"total_levied": 0.0},
        )
        return results

    from utils.finance_helpers import get_levy_proposed_amounts
    for l in levies:
        year = l.get("year", "Unknown")
        levy_total_uoe = l.get("total_uoe") or total_uoe
        admin_income, sinking_income = get_levy_proposed_amounts(l)

        # Recalculate share_pct using this year's total_uoe if entitlement is known
        if entitlement > 0 and levy_total_uoe > 0:
            share_pct = entitlement / levy_total_uoe

        building_total = round(admin_income + sinking_income, 2)
        results.append({
            "year": year,
            "Building": building_total,
            "ACTMedian": round(building_total * 0.94, 2),
            "Admin": round(admin_income, 2),
            "Sinking": round(sinking_income, 2),
            "Personal": round(building_total * share_pct, 2),
            "PersonalAdmin": round(admin_income * share_pct, 2),
            "PersonalSinking": round(sinking_income * share_pct, 2)
        })

    await _fire_analytics_shadow(
        building_id, "analytics.levy_benchmarks",
        {"total_levied": round(sum(float(r.get("Building", 0) or 0) for r in results), 2)},
    )
    return results


@router.get("/expense-breakdown")
async def get_expense_breakdown(
        financial_year: Optional[str] = Query(None),
        year: Optional[str] = Query(None),
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Building-wide expense breakdown by category for management dashboards.
    Accepts both ?financial_year= (frontend) and ?year= (legacy) query params.

    Source is governed by the shared finance route cutover engine
    (route_key ``analytics.expense_breakdown``) — GAP-FIN-052 item 2 replaced the
    former inline ``financial_pg_reads_enabled``-only gate. PG serves only when
    the route's shadow/critical gates pass; otherwise (today) MongoDB.
    """
    _year = financial_year or year or "2026"

    # --- Postgres read path (governed) ---
    if await _governed_read_source(building_id, "analytics.expense_breakdown") == "postgres":
        try:
            from services.analytics_pg_service import get_expense_breakdown_pg
            return await get_expense_breakdown_pg(building_id, _year)
        except Exception as _pg_err:
            logger.info(
                "expense-breakdown: Postgres path failed (%s), using MongoDB", _pg_err
            )

    # --- MongoDB legacy path (unchanged) ---
    from utils.finance_helpers import get_expense_breakdown_by_category
    mongo_result = await get_expense_breakdown_by_category(_year, building_id)
    total_expense = sum(
        float(i.get("amount", 0) or 0) for i in mongo_result if isinstance(i, dict)
    )
    await _fire_analytics_shadow(
        building_id, "analytics.expense_breakdown",
        {"year": str(_year), "total_expense": round(total_expense, 2)},
    )
    return mongo_result


@router.get("/budget-variance")
async def get_budget_variance(
        year: str = Query("2026"),
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Budget vs Actual variance analysis building-wide.
    Includes categories with actual spending even if they had no budgeted amount.

    Source is governed by the shared finance route cutover engine
    (route_key ``analytics.budget_variance``) — GAP-FIN-052 item 2 replaced the
    former inline ``financial_pg_reads_enabled``-only gate. PG serves only when
    the route's shadow/critical gates pass; otherwise (today) MongoDB.
    """
    # --- Postgres read path (governed) ---
    if await _governed_read_source(building_id, "analytics.budget_variance") == "postgres":
        try:
            from services.analytics_pg_service import get_budget_variance_pg
            return await get_budget_variance_pg(building_id, year)
        except Exception as _pg_err:
            logger.info(
                "budget-variance: Postgres path failed (%s), using MongoDB", _pg_err
            )

    # --- MongoDB legacy path (unchanged) ---
    cats = await db.levy_categories.find(
        {"building_id": building_id, "year": year}, {"_id": 0}
    ).sort("fund_type", 1).to_list(200)

    variance_data = []
    for c in cats:
        budgeted = c.get("budgeted_amount", 0)
        actual = c.get("actual_amount", 0)

        if budgeted == 0 and actual == 0:
            continue

        variance = actual - budgeted
        variance_pct = (variance / budgeted * 100) if budgeted > 0 else (100.0 if actual > 0 else 0.0)

        variance_data.append({
            "category": c.get("name"),
            "fund": c.get("fund_type"),
            "budgeted": budgeted,
            "actual": actual,
            "variance": round(variance, 2),
            "variance_pct": round(variance_pct, 1),
            "is_unbudgeted": budgeted == 0 and actual > 0
        })

    return variance_data


@router.get("/occupancy-trends")
async def get_occupancy_trends(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Return occupancy trends from November 2020 to the current month.
    Owners are assumed to have been in place since 20 November 2020.
    """
    from datetime import date

    # Performance Optimization⚡: Parallelize unit count queries.
    tasks = [
        db.units.count_documents({"building_id": building_id}),
        db.units.count_documents({
            "building_id": building_id,
            "owner_name": {"$exists": True, "$ne": None, "$ne": ""}
        })
    ]
    total_units, occupied_units = await asyncio.gather(*tasks)

    # 0.0 when there are no units, not 94.5. A building with no units has an
    # occupancy rate of nothing — inventing a healthy-looking 94.5% is the same
    # "absence scores well" failure that made an empty building rate 75/100 on
    # the health score. CLAUDE.md: a last-resort fallback is 0.0 plus a warning.
    if total_units > 0:
        current_rate = occupied_units / total_units * 100
    else:
        current_rate = 0.0
        logger.warning(
            "occupancy trend: building=%s has no units; reporting 0%% rather than "
            "a placeholder", building_id,
        )

    start_date = date(2020, 11, 1)
    today = date.today()
    start_rate = 60.0

    trends = []
    cur = start_date
    while cur <= today:
        months_elapsed = (cur.year - start_date.year) * 12 + (cur.month - start_date.month)
        total_months = (today.year - start_date.year) * 12 + (today.month - start_date.month)
        progress = min(1.0, months_elapsed / max(1, total_months))
        rate = start_rate + (current_rate - start_rate) * progress
        trends.append({
            "month": cur.strftime("%b %Y"),
            "occupancy": round(min(100, rate), 1),
            "target": 98.0
        })
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)

    return trends


@router.get("/fund-forecasts")
async def get_fund_forecasts(
        years: int = Query(5, ge=1, le=10),
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Data-driven fund health forecasts.
    Admin: per-category CPI/regression forecasts (financial_forecasts collection) + CPI 3.5% for Y4+.
    Sinking: 15-year capital plan closing balances (sinking_fund_plan collection).
    No static percentage growth assumptions.
    """
    from utils.finance_helpers import get_latest_levy_year
    from services.forecast_service import get_admin_fund_projections, get_sinking_fund_projections

    base_year_str = await get_latest_levy_year(building_id) or "2025"
    base_year = int(base_year_str.split('-')[0]) if '-' in base_year_str else int(base_year_str)

    # Extra years = total requested minus the 3 that financial_forecasts covers
    extra_cpi_years = max(0, years - 3)
    admin_forecast = await get_admin_fund_projections(building_id, base_year, extra_years=extra_cpi_years)
    # Enforce the requested horizon: service always returns base + Y1/Y2/Y3 (+ extra CPI years);
    # trim here so the response length matches the `years` query parameter exactly.
    admin_forecast = admin_forecast[:years + 1]
    sinking_rows = await get_sinking_fund_projections(building_id, max_years=20)

    # Limit sinking to years+1 entries starting from base_year
    sinking_forecast = [
        r for r in sinking_rows
        if int(r["year"]) >= base_year and int(r["year"]) <= base_year + years
    ]
    # If capital plan data is missing, fall back to CPI on sinking reserve closing balance.
    # IMPORTANT: get_levy_proposed_amounts() returns annual INCOME (levy contribution),
    # NOT the reserve balance. We must use closing_balance from annual_levies for the base.
    if not sinking_forecast:
        levy_doc = await db.annual_levies.find_one({"building_id": building_id, "year": base_year_str})
        sinking_base = 0.0
        if levy_doc:
            sf = levy_doc.get("sinking_fund", {})
            sinking_base = float(sf.get("closing_balance") or 0.0)
        if sinking_base == 0.0:
            logger.warning(
                f"No sinking fund closing_balance for building {building_id}; "
                "CPI fallback will start from 0.0"
            )
        sinking_forecast = [
            {"year": str(base_year + i), "balance": round(sinking_base * (1.035 ** i), 2),
             "type": "estimate", "source": "cpi_fallback"}
            for i in range(years + 1)
        ]

    return {
        "admin": admin_forecast,
        "sinking": sinking_forecast,
        "data_sources": {
            "admin": "per_category_forecasts_and_cpi",
            "sinking": "15_year_capital_plan" if sinking_rows else "cpi_fallback"
        }
    }


@router.get("/compliance-details")
async def get_compliance_details(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Detailed list of compliance items for management popup.

    PG read path: active when ``governance_read_pg_enabled`` toggle is ON.
    Falls back to MongoDB when Postgres data is unavailable.
    """
    # Only management roles (honours temp elevation via effective_role)
    if effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # --- Postgres read path ---
    try:
        from services.cutover_config_service import (
            is_cutover_feature_enabled,
            GOVERNANCE_READ_PG_ENABLED,
        )
        if await is_cutover_feature_enabled(building_id, GOVERNANCE_READ_PG_ENABLED):
            from services.analytics_pg_service import get_compliance_details_pg
            return await get_compliance_details_pg(building_id)
    except Exception as _pg_err:
        logger.info(
            "compliance-details: Postgres path unavailable (%s), using MongoDB", _pg_err
        )

    # --- MongoDB legacy path (unchanged) ---
    items = await db.compliance_items.find({"building_id": building_id}, {"_id": 0}).sort("due_date", 1).to_list(100)
    return items


# --- Utility Usage Schema ---

class UtilityUsageCreate(BaseModel):
    unit_number: str
    month: str  # e.g. "2026-03"
    electricity_kwh: float
    water_kl: Optional[float] = None
    gas_mj: Optional[float] = None


@router.get("/utility-usage/{unit_number}")
async def get_utility_usage(
        unit_number: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Fetch owner's utility usage data and building averages.
    """
    # Authorization check - users can only view their own unit data unless admin/manager
    if current_user.get("unit_number") != unit_number and effective_role(current_user) not in [UserRole.SUPER_ADMIN,
                                                                                               UserRole.STRATA_MANAGER,
                                                                                               UserRole.EC_MEMBER]:
        raise HTTPException(status_code=403, detail="Not authorized to view this unit's data")

    usage = await db.utility_usage.find_one(
        {"building_id": building_id, "unit_number": unit_number},
        sort=[("month", -1)],
        projection={"_id": 0}
    )

    if not usage:
        usage = {
            "unit_number": unit_number,
            "month": datetime.now().strftime("%Y-%m"),
            "electricity_kwh": 410.0,
            "water_kl": 12.5,
            "gas_mj": 450.0
        }

    return {
        **usage,
        "building_avg_kwh": 340.0,
        "building_avg_water": 14.2,
        "building_avg_gas": 480.0,
        "efficiency_note": "You use 12% less water than building average." if (usage.get(
            "water_kl") or 15) < 14.2 else "Consider low-flow shower heads to save water."
    }


@router.post("/utility-usage")
async def post_utility_usage(
        data: UtilityUsageCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Save owner's monthly utility usage.
    """
    if not current_user.get("unit_number") and effective_role(current_user) not in [UserRole.SUPER_ADMIN,
                                                                                    UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    doc = data.model_dump()
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    doc_set = {k: v for k, v in doc.items() if k not in ("building_id", "unit_number", "month")}
    doc_set["updated_at"] = doc["updated_at"]

    await db.utility_usage.update_one(
        {"building_id": building_id, "unit_number": data.unit_number, "month": data.month},
        {"$set": doc_set},
        upsert=True
    )

    return {"status": "success", "message": "Usage data recorded"}


@router.get("/personal-insights")
async def get_personal_insights(current_user: dict = Depends(get_approved_user)):
    """
    Personalized insight bubbles for dashboard retention.
    """
    return [
        {"id": "levy_comparison", "text": "You paid $120 less levy than ACT median this year.", "type": "financial",
         "icon": "trending-down"},
        {"id": "property_value", "text": "Property value up 3.8% in Denman Prospect this year.", "type": "market",
         "icon": "home"},
        {"id": "utility_saving", "text": "Lowering thermostat by 1°C could save you $15/mo.", "type": "utility",
         "icon": "zap"}
    ]


@router.get("/financial-health")
async def get_financial_health_metrics(
        year: str = Query("2026"),
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    True Cost of Ownership and Reserve Adequacy metrics.
    """
    from services.health_service import compute_financial_health
    from services.lot_finance_service import compute_building_cost_distribution

    # Bolt ⚡: Parallelize metrics computation
    # compute_financial_health's signature is (year, building_id) — was previously called
    # with the two positional args transposed, yielding a wrong/empty health result here.
    tasks = [
        compute_financial_health(year, building_id),
        compute_building_cost_distribution(building_id, year)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    health = results[0] if not isinstance(results[0], Exception) else {}
    costs = results[1] if not isinstance(results[1], Exception) else []

    # Calculate Reserve Adequacy %
    # Formula: (Sinking Fund Closing Balance / 10-Year Projected Maintenance) * 100
    # For now using health score components as a proxy for adequacy
    adequacy = health.get("breakdown", {}).get("surplus_ratio", 0) * 5  # Scale to 100

    # Calculate True Cost of Ownership (Average)
    avg_cost = 0
    if costs:
        total_building_cost = sum(c.get("total_cost", 0) for c in costs)
        avg_cost = total_building_cost / len(costs)

    return {
        "year": year,
        "health_score": health.get("score", 0),
        "reserve_adequacy_pct": round(min(100, adequacy), 1),
        "avg_cost_of_ownership": round(avg_cost, 2),
        "risk_level": health.get("risk_level", "unknown"),
        "top_cost_lots": costs[:5] if costs else []
    }


@router.get("/tax-summary/{unit_number}")
async def get_tax_summary_route(
        unit_number: str,
        financial_year: str = Query("2025-26"),
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Generate and return a consolidated Tax Summary PDF."""
    from services.tax_service import generate_tax_summary
    from fastapi.responses import StreamingResponse
    import io

    # Ownership Check
    if effective_role(current_user) == UserRole.OWNER and current_user.get("unit_number") != unit_number:
        raise HTTPException(status_code=403, detail="Not authorized to view this unit's tax summary")

    pdf_bytes = await generate_tax_summary(unit_number, financial_year, building_id)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=Tax_Summary_{unit_number}_{financial_year}.pdf"}
    )


@router.get("/ping")
async def ping_host(
        host: str = Query(..., pattern=r"^[a-zA-Z0-9.-]+$"),
        current_user: dict = Depends(require_permission("can_manage_users"))
):
    """
    Check if a host is reachable. Admin only.
    Bolt ⚡ Security: Enforced strict regex validation for host and added timeout.
    """
    try:
        # Using -c 1 to send only one packet
        result = subprocess.run(
            ["ping", "-c", "1", host],
            capture_output=True,
            text=True,
            timeout=5
        )
        return {
            "host": host,
            "reachable": result.returncode == 0,
            "output": result.stdout if result.returncode == 0 else result.stderr
        }
    except subprocess.TimeoutExpired:
        return {"host": host, "reachable": False, "error": "Ping timed out"}
    except Exception as e:
        return {"host": host, "reachable": False, "error": str(e)}


# ── Dashboard v2 endpoints ─────────────────────────────────────────────────────
# All three endpoints below use PostgreSQL as the primary data source, with a
# graceful MongoDB fallback during the pre-cutover period when PG tables may be
# empty. Once the MongoDB→PostgreSQL data cutover executes the fallback paths
# become dead code and can be removed.

@router.get("/diff-since")
async def get_diff_since(
    since: Optional[str] = Query(None, description="ISO-8601 timestamp of last dashboard visit; defaults to 3 days ago"),
    year: Optional[str] = Query(None, description="Financial year used for the arrears snapshot"),
    current_user: dict = Depends(get_approved_user),
    building_id: str = Depends(get_current_building)
):
    """
    Return a compact summary of what changed since <since> timestamp.
    Used by the 'Since your last visit' strip on the Manager Dashboard v2.
    Always returns 5 key summary cards (ARREARS, SLA, CASH, AGM, COMPLIANCE) even when counts are 0.

    Primary source: PostgreSQL (ops.cases, finance.receipts,
    communications.announcements, ops.task_sla_events).
    Fallback: MongoDB collections (workflow_requests, levy_payments,
    compliance_items, announcements) when PG returns zero across all counts.
    """
    from db_postgres.repos.config_repo import resolve_scheme_context
    from db_postgres.session import async_session_context, set_tenant
    from sqlalchemy import text as pg_text

    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            since_dt = datetime.now(timezone.utc) - timedelta(days=3)
    else:
        since_dt = datetime.now(timezone.utc) - timedelta(days=3)

    now = datetime.now(timezone.utc)
    days_since = max(1, round((now - since_dt).total_seconds() / 86400))

    # ── 1. Try PostgreSQL ──────────────────────────────────────────────────────
    pg_workflow_count = 0
    pg_payment_count = 0
    pg_compliance_count = 0
    pg_announcement_count = 0
    pg_sla_breaches = 0
    pg_source = False

    try:
        scheme = await resolve_scheme_context(building_id)
        if scheme:
            sid = str(scheme["scheme_id"])
            tid = str(scheme["tenant_id"])
            async with async_session_context() as session:
                await set_tenant(session, tid)

                # ops.cases — new service requests / cases since last visit
                row = await session.execute(
                    pg_text("""
                        SELECT COUNT(*)
                        FROM ops.cases
                        WHERE scheme_id = :sid
                          AND created_at > :since
                          AND is_test_data = FALSE
                    """),
                    {"sid": sid, "since": since_dt},
                )
                pg_workflow_count = row.scalar() or 0

                # finance.receipts — payments received since last visit
                row = await session.execute(
                    pg_text("""
                        SELECT COUNT(*)
                        FROM finance.receipts
                        WHERE scheme_id = :sid
                          AND created_at > :since
                          AND COALESCE(is_test_data, FALSE) = FALSE
                    """),
                    {"sid": sid, "since": since_dt},
                )
                pg_payment_count = row.scalar() or 0

                # communications.announcements — new notices since last visit
                row = await session.execute(
                    pg_text("""
                        SELECT COUNT(*)
                        FROM compliance.compliance_items
                        WHERE scheme_id = :sid
                          AND updated_at > :since
                          AND COALESCE(is_test_data, FALSE) = FALSE
                    """),
                    {"sid": sid, "since": since_dt},
                )
                pg_compliance_count = row.scalar() or 0

                row = await session.execute(
                    pg_text("""
                        SELECT COUNT(*)
                        FROM communications.announcements
                        WHERE scheme_id = :sid
                          AND published_at > :since
                          AND COALESCE(is_test_data, FALSE) = FALSE
                    """),
                    {"sid": sid, "since": since_dt},
                )
                pg_announcement_count = row.scalar() or 0

                # ops.task_sla_events — SLA breaches raised since last visit
                row = await session.execute(
                    pg_text("""
                        SELECT COUNT(*)
                        FROM ops.task_sla_events
                        WHERE scheme_id = :sid
                          AND event_type = 'breached'
                          AND breached_at > :since
                          AND is_test_data = FALSE
                    """),
                    {"sid": sid, "since": since_dt},
                )
                pg_sla_breaches = row.scalar() or 0

            # Use PG counts when at least one table has data (not pre-cutover empty)
            pg_total = (
                pg_workflow_count
                + pg_payment_count
                + pg_compliance_count
                + pg_announcement_count
                + pg_sla_breaches
            )
            if pg_total > 0:
                pg_source = True
    except Exception as exc:
        logger.warning("diff-since: PG query failed, falling back to Mongo: %s", exc)

    # ── 2. MongoDB fallback — used only when PG tables are empty pre-cutover ───
    if not pg_source:
        async def _mongo_count(collection_name: str, extra_filter: dict = None) -> int:
            """Generated function header.

            Function: _mongo_count
            Path: backend/routers/analytics.py

            Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
            """
            try:
                q = {
                    "created_at": {"$gt": since_dt.isoformat()},
                    "is_test_data": {"$ne": True},
                }
                if extra_filter:
                    q.update(extra_filter)
                return await db[collection_name].count_documents(q)
            except Exception:
                return 0

        results = await asyncio.gather(
            _mongo_count("workflow_requests"),
            _mongo_count("levy_payments"),
            _mongo_count("compliance_items"),
            _mongo_count("announcements"),
            db.workflow_requests.count_documents({
                "sla_breached": True,
                "updated_at": {"$gte": since_dt.isoformat()},
                "is_test_data": {"$ne": True},
            }),
            return_exceptions=True,
        )
        workflow_count   = results[0] if not isinstance(results[0], Exception) else 0
        payment_count    = results[1] if not isinstance(results[1], Exception) else 0
        compliance_count = results[2] if not isinstance(results[2], Exception) else 0
        announcement_count = results[3] if not isinstance(results[3], Exception) else 0
        sla_breaches     = results[4] if not isinstance(results[4], Exception) else 0
        source_label = "mongodb_fallback"
    else:
        workflow_count     = int(pg_workflow_count)
        payment_count      = int(pg_payment_count)
        compliance_count   = int(pg_compliance_count)
        announcement_count = int(pg_announcement_count)
        sla_breaches       = int(pg_sla_breaches)
        source_label = "postgresql"

    # ── 3. AGM/SGM upcoming check (Mongo — always available pre-cutover) ─────
    agm_count = 0
    agm_label = "No meeting scheduled"
    try:
        now_iso = now.isoformat()
        future_30 = (now + timedelta(days=30)).isoformat()
        agm_doc = await db.meetings.find_one(
            {
                "meeting_type": {"$in": [
                    "agm", "sgm", "AGM", "SGM",
                    "Annual General Meeting", "Special General Meeting",
                ]},
                "$or": [
                    {"date": {"$gte": now_iso, "$lte": future_30}},
                    {"meeting_date": {"$gte": now_iso, "$lte": future_30}},
                ],
                "is_test_data": {"$ne": True},
            },
            {"_id": 0, "meeting_type": 1, "date": 1, "meeting_date": 1, "title": 1},
        )
        if agm_doc:
            agm_count = 1
            mt = agm_doc.get("meeting_type", "Meeting").upper()
            d = agm_doc.get("date") or agm_doc.get("meeting_date", "")
            agm_label = f"{mt} scheduled {d[:10] if d else 'soon'}"
    except Exception:
        pass

    # ── 3.5. Arrears snapshot (Mongo — selected-year net_balance, always) ─────
    # The frontend passes its selected financial year so the "Since your last
    # visit" arrears card cannot drift to a different levy year. Cash Position
    # displays the /arrears/detail true-arrears contract instead.
    arrears_total = 0.0
    arrears_units = 0
    try:
        from datetime import date as _date
        arrears_year = str(year).strip() if year else None
        if not arrears_year:
            levy_head = await db.annual_levies.find_one(
                {"building_id": building_id, "is_test_data": {"$ne": True}},
                {"year": 1, "_id": 0},
                sort=[("year", -1)],
            )
            arrears_year = str(levy_head["year"]) if levy_head else str(_date.today().year)
        # Canonical grace-aware arrears (GAP-FIN-040) — one source shared with
        # /finance/summary, /stats/building-kpis and the arrears board, so the
        # manager "since last visit" card can never diverge from them.
        from utils.finance_helpers import get_building_arrears_summary
        _arrears = await get_building_arrears_summary(building_id, arrears_year)
        arrears_total = _arrears["true_arrears_amount"]
        arrears_units = _arrears["units_in_arrears"]
    except Exception:
        pass

    # ── 4. Always return 5 key summary cards: ARREARS, SLA, CASH, AGM, COMPLIANCE
    # Cards appear regardless of value so managers always see the building pulse.
    items: list[dict] = [
        {
            "kind": "ARREARS",
            "tone": "rose" if arrears_total > 0 else "slate",
            "label": (
                f"${arrears_total:,.0f} outstanding"
                if arrears_total > 0
                else "No arrears"
            ),
            "value": (
                f"{arrears_units} unit{'s' if arrears_units != 1 else ''}"
                if arrears_units > 0
                else "All clear"
            ),
        },
        {
            "kind": "SLA",
            "tone": "rose" if sla_breaches > 0 else "slate",
            "label": (
                f"{sla_breaches} SLA breach{'es' if sla_breaches != 1 else ''}"
                if sla_breaches > 0
                else "No SLA breaches"
            ),
            "value": f"{sla_breaches} urgent" if sla_breaches > 0 else "All clear",
        },
        {
            "kind": "CASH",
            "tone": "mint" if payment_count > 0 else "slate",
            "label": (
                f"{payment_count} payment{'s' if payment_count != 1 else ''} received"
                if payment_count > 0
                else "No payments today"
            ),
            "value": f"+{payment_count}" if payment_count > 0 else "—",
        },
        {
            "kind": "AGM",
            "tone": "indigo" if agm_count > 0 else "slate",
            "label": agm_label,
            "value": "Due soon" if agm_count > 0 else "None in 30d",
        },
        {
            "kind": "COMPLIANCE",
            "tone": "amber" if compliance_count > 0 else "slate",
            "label": (
                f"{compliance_count} item{'s' if compliance_count != 1 else ''} updated"
                if compliance_count > 0
                else "Compliance clear"
            ),
            "value": str(compliance_count) if compliance_count > 0 else "✓",
        },
    ]

    return {
        "since": since_dt.isoformat(),
        "days_since": days_since,
        "items": items[:5],
        "counts": {
            "new_requests":      int(workflow_count),
            "payments_received": int(payment_count),
            "compliance_updates": int(compliance_count),
            "announcements":     int(announcement_count),
            "sla_breaches":      int(sla_breaches),
            "agm_upcoming":      int(agm_count),
            "total_arrears":     float(arrears_total),
            "units_in_arrears":  int(arrears_units),
            "arrears_year":      str(arrears_year) if arrears_year else None,
        },
        "source": source_label,
    }


@router.get("/levy-allocation-breakdown")
async def get_levy_allocation_breakdown(
    year: str = Query(None, description="Financial year, e.g. '2026'"),
    current_user: dict = Depends(get_approved_user),
    building_id: str = Depends(get_current_building)
):
    """
    Return levy budget split by expenditure category as percentages + dollar amounts.
    Used by the 'Where your levy goes' donut on the Owner Dashboard v2.

    Uses MongoDB annual_levies as the canonical source for levy totals and an
    embedded category_breakdown when present. Otherwise it falls back to the
    standard model split, with levy totals always read via
    get_levy_proposed_amounts() — never via non-existent admin_levy_annual /
    sinking_levy_annual fields.
    """
    from utils.finance_helpers import get_latest_levy_year, get_levy_proposed_amounts

    # PostgreSQL primary path — governed by the shared finance route cutover
    # engine (route_key ``analytics.levy_allocation_breakdown``). GAP-FIN-052
    # follow-up: this route previously gated only on
    # ``is_cutover_feature_enabled(FINANCIAL_PG_READS_ENABLED)``, which served
    # Postgres whenever the per-building toggle was on, bypassing the
    # shadow-soak / critical-diff gates every other governed finance route
    # respects. It now resolves its source the same way as the four sibling
    # analytics routes; PG serves only when the route's gates pass, else Mongo.
    try:
        if await _governed_read_source(
            building_id, "analytics.levy_allocation_breakdown"
        ) == "postgres":
            from db_postgres.repos.config_repo import resolve_scheme_context
            from db_postgres.session import async_session_context, set_tenant
            from sqlalchemy import text

            scheme = await resolve_scheme_context(building_id)
            if scheme:
                sid = str(scheme["scheme_id"])
                tid = str(scheme["tenant_id"])
                _year = str(year) if year else None
                async with async_session_context() as session:
                    await set_tenant(session, tid)
                    if not _year:
                        year_row = await session.execute(
                            text("""
                                SELECT financial_year
                                FROM finance.levy_runs
                                WHERE scheme_id = CAST(:sid AS UUID)
                                ORDER BY issue_date DESC
                                LIMIT 1
                            """),
                            {"sid": sid},
                        )
                        _year = year_row.scalar()

                    if _year:
                        rows = await session.execute(
                            text("""
                                SELECT
                                    f.fund_type::text AS fund_type,
                                    COALESCE(SUM(li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents), 0) AS amount_cents
                                FROM finance.levy_items li
                                JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                                JOIN finance.funds f ON f.fund_id = li.fund_id
                                -- GAP-FIN-062 (2026-08-19 follow-up audit): same posted-only gate
                                -- as routers/finance.py's levy_result -- this query shares the
                                -- exact same shape and was missed in the original coordinated fix.
                                JOIN finance.journal_entries je ON je.journal_entry_id = li.journal_entry_id
                                  AND je.status = 'posted'
                                WHERE li.scheme_id = CAST(:sid AS UUID)
                                  AND lr.financial_year = :fy
                                GROUP BY f.fund_type
                            """),
                            {"sid": sid, "fy": str(_year)},
                        )
                        fund_rows = rows.fetchall()
                        if fund_rows:
                            admin_annual = 0.0
                            sinking_annual = 0.0
                            for r in fund_rows:
                                amount = round(float(r.amount_cents or 0) / 100, 2)
                                if r.fund_type == "admin":
                                    admin_annual += amount
                                elif r.fund_type in ("sinking", "capital_works"):
                                    sinking_annual += amount

                            total_annual = round(admin_annual + sinking_annual, 2)
                            categories = []
                            model = [
                                ("Insurance", 0.22),
                                ("Sinking Fund", None),
                                ("Management Fee", 0.09),
                                ("Cleaning & Grounds", 0.14),
                                ("Maintenance", 0.16),
                                ("Utilities", 0.07),
                                ("Other", 0.04),
                            ]
                            admin_weight_total = sum(pct for _, pct in model if pct is not None) or 1.0
                            for name, pct in model:
                                if pct is None:
                                    amt = round(sinking_annual, 2)
                                else:
                                    amt = round(admin_annual * (pct / admin_weight_total), 2)
                                categories.append({
                                    "name": name,
                                    "amount": amt,
                                    "pct": round((amt / total_annual) * 100, 1) if total_annual else 0.0,
                                    "color": None,
                                })

                            return {
                                "year": str(_year),
                                "total_annual": total_annual,
                                "admin_annual": round(admin_annual, 2),
                                "sinking_annual": round(sinking_annual, 2),
                                "categories": categories,
                                "source": "postgres",
                            }
    except Exception as _pg_err:
        logger.info(
            "levy-allocation-breakdown: Postgres path unavailable (%s), using MongoDB",
            _pg_err,
        )

    if not year:
        year = await get_latest_levy_year(building_id)

    # ── 1. MongoDB annual_levies for levy totals (canonical source pre-cutover) ─
    # IMPORTANT: use get_levy_proposed_amounts() — NOT levy_doc.get("admin_levy_annual")
    # which does not exist on annual_levies documents.
    levy_doc = None
    admin_annual  = 0.0
    sinking_annual = 0.0

    try:
        levy_doc = await db.annual_levies.find_one({"year": str(year)})
        if levy_doc:
            admin_annual, sinking_annual = get_levy_proposed_amounts(levy_doc)
    except Exception as exc:
        logger.warning("levy-allocation-breakdown: Mongo levy fetch failed: %s", exc)

    total_annual = admin_annual + sinking_annual

    # ── 2. Build category breakdown ───────────────────────────────────────────
    categories: list[dict] = []

    # Prefer an explicit category_breakdown embedded in the annual_levies doc
    if levy_doc and levy_doc.get("category_breakdown"):
        raw = levy_doc["category_breakdown"]
        cat_total = sum(v for v in raw.values() if isinstance(v, (int, float))) or 1.0
        for name, amount in raw.items():
            if isinstance(amount, (int, float)) and amount > 0:
                categories.append({
                    "name":   name.replace("_", " ").title(),
                    "amount": round(float(amount), 2),
                    "pct":    round((amount / cat_total) * 100, 1),
                    "color":  None,  # frontend assigns colours
                })

    if not categories:
        # Standard strata allocation model (from strata_metrics_spec_v2).
        # Sinking fund bucket uses the actual sinking levy amount; remaining
        # admin fund categories are split proportionally within admin spend.
        PALETTE = ["#4F46E5", "#16A34A", "#F59E0B", "#0EA5E9", "#7C3AED", "#E11D48", "#64748B"]
        model = [
            ("Insurance",         0.22),
            ("Sinking Fund",      None),       # uses exact sinking_annual
            ("Management Fee",    0.09),
            ("Cleaning & Grounds",0.14),
            ("Maintenance",       0.16),
            ("Utilities",         0.07),
            ("Other",             0.04),
        ]
        admin_weight_total = sum(pct for _, pct in model if pct is not None) or 1.0
        # Admin-only base (total minus sinking) for proportional categories.
        # Normalize the admin-side model weights so the category amounts consume the
        # full admin levy and the donut percentages sum to the full total_annual.
        admin_base = admin_annual if total_annual > 0 else 0.0
        for idx, (name, pct) in enumerate(model):
            if pct is None:
                # Sinking Fund: use exact value
                amt = round(sinking_annual, 2)
                cat_pct = round((amt / total_annual) * 100, 1) if total_annual else 0.0
            else:
                amt = round(admin_base * (pct / admin_weight_total), 2)
                cat_pct = round((amt / total_annual) * 100, 1) if total_annual else 0.0
            categories.append({
                "name":   name,
                "amount": amt,
                "pct":    cat_pct,
                "color":  PALETTE[idx],
            })

    # Fire-and-forget shadow compare (never affects this response; see GAP-FIN-055).
    await _fire_analytics_shadow(
        building_id, "analytics.levy_allocation_breakdown",
        {
            "year": str(year),
            "admin_annual": round(admin_annual, 2),
            "sinking_annual": round(sinking_annual, 2),
            "total_annual": round(total_annual, 2),
        },
    )

    return {
        "year":           str(year),
        "total_annual":   round(total_annual, 2),
        "admin_annual":   round(admin_annual, 2),
        "sinking_annual": round(sinking_annual, 2),
        "categories":     categories,
        "source": "category_breakdown" if (levy_doc and levy_doc.get("category_breakdown")) else "model",
    }


@router.get("/my-streak")
async def get_my_streak(
    unit_number: Optional[str] = Query(None),
    current_user: dict = Depends(get_approved_user),
    building_id: str = Depends(get_current_building)
):
    """
    Return the authenticated owner's levy payment streak and on-time history.
    Used by PaymentStreakCard on the Owner Dashboard v2.

    PostgreSQL is used to resolve the selected owner lot, entitlement share,
    and quarter payment status. This route intentionally does not fall back to
    MongoDB; empty PG ledger rows return zero streak data.

    Response shape (matches PaymentStreakCard props):
      {
        "streak":          <int>,
        "total_quarters":  <int>,
        "on_time_count":   <int>,
        "on_time_pct":     <float>,
        "recent_quarters": [ {year, quarter, on_time, status}, ... ],
        "entitlement_pct": <float>,   # lot share of building UOE, %
        "source":          "postgres_ledger" | "postgres_ledger_unavailable"
      }
    """
    from db_postgres.repos.config_repo import resolve_scheme_context
    from db_postgres.session import async_session_context, set_tenant
    from sqlalchemy import text as pg_text

    # current_user["id"] is the canonical user ID key across all routers
    # (set by identity_repo._normalise_user → "id": str(user_id)).
    # "user_id" is the JWT payload field name; "id" is what the user doc carries.
    user_id  = str(current_user.get("id") or current_user.get("user_id", ""))
    unit_num = unit_number or current_user.get("unit_number", "")

    # ── 1. Resolve selected lot and entitlement from PostgreSQL ────────────────
    entitlement_pct = 0.0
    recent_quarters: list[dict] = []
    source_label = "postgres_ledger"
    try:
        scheme = await resolve_scheme_context(building_id)
        if scheme:
            sid = str(scheme["scheme_id"])
            tid = str(scheme["tenant_id"])
            async with async_session_context() as session:
                await set_tenant(session, tid)

                # Resolve lot_id from core.user_units → core.lots. When a selected
                # unit is provided, bind the result to that unit so multi-unit
                # owners do not see another lot's entitlement share.
                lot_row = await session.execute(
                    pg_text("""
                        SELECT l.lot_id,
                               l.lot_number,
                               l.entitlement_units,
                               l.scheme_id,
                               (
                                   SELECT SUM(l2.entitlement_units)
                                   FROM core.lots l2
                                   WHERE l2.scheme_id = :sid
                               ) AS total_entitlement
                        FROM core.user_units uu
                        JOIN core.lots l ON l.lot_id = uu.lot_id
                        WHERE uu.user_id = :uid
                          AND uu.scheme_id = :sid
                          AND uu.relationship = 'owner'
                          AND uu.valid_to IS NULL
                          AND (:unit_number = '' OR l.unit_number = :unit_number OR l.lot_number = :unit_number)
                        LIMIT 1
                    """),
                    {"sid": sid, "uid": user_id, "unit_number": str(unit_num or "")},
                )
                lot = lot_row.mappings().first()

                if lot:
                    total_ent = float(lot["total_entitlement"] or 1)
                    my_ent    = float(lot["entitlement_units"] or 0)
                    entitlement_pct = round((my_ent / total_ent) * 100, 4) if total_ent else 0.0
                    # LIMIT was 8 -- silently capped `streak` (the count of CONSECUTIVE on-time
                    # quarters below) at 8 for any owner whose real streak is longer, since the
                    # query never looked back far enough to find where it would actually break.
                    # Not a display-window limit -- it directly bounded the correctness of the
                    # number shown. 60 covers 15 years of quarterly billing (or an owner with a
                    # mixed quarterly/half-yearly history, like East Gate's first year) comfortably
                    # for a single lot's own levy_items, which is a small, bounded row count.
                    quarter_rows = await session.execute(
                        pg_text("""
                            SELECT
                                lr.financial_year,
                                COALESCE(lr.quarter_no, 0) AS quarter_no,
                                lr.due_date,
                                COALESCE(SUM(li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents), 0) AS levied_cents,
                                COALESCE(SUM(li.paid_cents), 0) AS paid_cents
                            FROM finance.levy_items li
                            JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                            -- GAP-FIN-062 (2026-08-19 follow-up audit): same posted-only gate as
                            -- the annual-totals query above -- missed in the original fix.
                            JOIN finance.journal_entries je ON je.journal_entry_id = li.journal_entry_id
                              AND je.status = 'posted'
                            WHERE li.scheme_id = :sid
                              AND li.lot_id = :lot_id
                            GROUP BY lr.financial_year, lr.quarter_no, lr.due_date
                            ORDER BY lr.financial_year DESC, lr.quarter_no DESC
                            LIMIT 60
                        """),
                        {"sid": sid, "lot_id": str(lot["lot_id"])},
                    )
                    today = datetime.now(timezone.utc).date()
                    for row in quarter_rows.fetchall():
                        levied = int(row.levied_cents or 0)
                        paid = int(row.paid_cents or 0)
                        due = row.due_date
                        if levied > 0 and paid >= levied:
                            status = "paid"
                        elif paid > 0:
                            status = "partial"
                        elif due and due < today:
                            status = "overdue"
                        else:
                            status = "unpaid"
                        recent_quarters.append({
                            "year": str(row.financial_year),
                            "quarter": str(row.quarter_no or ""),
                            "on_time": status == "paid",
                            "status": status,
                        })
    except Exception as exc:
        logger.warning("my-streak: PG ledger query failed: %s", exc)
        source_label = "postgres_ledger_unavailable"

    # The SQL query above returns newest levy periods first. PaymentStreakCard
    # consumes recent_quarters in that same newest-first order so its leftmost
    # dots match the consecutive streak count computed below.
    ordered_quarters = recent_quarters
    streak = 0
    for quarter in recent_quarters:
        if quarter.get("on_time"):
            streak += 1
        else:
            break
    on_time_count = sum(1 for quarter in recent_quarters if quarter.get("on_time"))
    total_quarters = len(recent_quarters)
    streak_data = {
        "streak": streak,
        "total_quarters": total_quarters,
        "on_time_count": on_time_count,
        "on_time_pct": round((on_time_count / total_quarters) * 100, 1) if total_quarters else 0.0,
        "recent_quarters": ordered_quarters,
    }

    return {
        **streak_data,
        "entitlement_pct": entitlement_pct,
        "source":          source_label,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Dashboard v2 extras
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/dashboard-v2-extras")
async def get_dashboard_v2_extras(
    unit_number: Optional[str] = Query(None),
    current_user: dict = Depends(get_approved_user),
    building_id: str = Depends(get_current_building),
):
    """
    Aggregate auxiliary signals consumed by Owner Dashboard v2 components when
    no first-class endpoint exists yet (true-cost categories, market signals
    with comparable sales, building emergency contacts, owner badges, owner
    open requests). Buildings without seeded extras return an empty payload —
    the frontend renders graceful empty/CTA states.

    Source of truth: `db.dashboard_v2_signals` documents keyed by `building_id`
    and optional `unit_number`. Demo seed lives in backend/seeds/demo_customer.py.
    """
    # Per-unit data is restricted: only the owner of the unit or a building-level
    # role (super_admin / strata_manager / strata_admin / ec_member) may request
    # a different unit's signals. Matches the existing guard pattern used by
    # /analytics/utility-usage/{unit_number} and /analytics/tax-summary/{unit_number}.
    if unit_number and current_user.get("unit_number") != unit_number:
        role = effective_role(current_user)
        if role not in {
            UserRole.SUPER_ADMIN,
            UserRole.STRATA_MANAGER,
            UserRole.STRATA_ADMIN,
            UserRole.EC_MEMBER,
        }:
            raise HTTPException(
                status_code=403,
                detail="Not authorized to view this unit's data",
            )

    signals_collection = db.dashboard_v2_signals

    # Building-wide signals (no unit_number) and real insurance policy — run in parallel.
    bld_doc_coro = signals_collection.find_one({"building_id": building_id, "scope": "building"})
    # Most recent active/pending-renewal policy for this building (owner-safe: only expiry date exposed).
    insurance_policy_coro = db.insurance_policies.find_one(
        {"building_id": building_id, "status": {"$in": ["active", "pending_renewal"]}},
        {"expiry_date": 1, "_id": 0},
        sort=[("expiry_date", -1)],
    )

    # Unit-specific overlay (cost categories, badges, requests)
    unit_doc_coro = signals_collection.find_one({
        "building_id": building_id,
        "scope": "unit",
        "unit_number": unit_number,
    }) if unit_number else None

    # Gather parallel reads; unit_doc_coro may be None (not a coroutine) so handle separately.
    if unit_doc_coro is not None:
        bld_doc, live_policy, unit_doc = await asyncio.gather(
            bld_doc_coro, insurance_policy_coro, unit_doc_coro
        )
    else:
        bld_doc, live_policy = await asyncio.gather(bld_doc_coro, insurance_policy_coro)
        unit_doc = None

    def _strip_mongo(doc: Dict[str, Any]) -> Dict[str, Any]:
        """Generated function header.

        Function: _strip_mongo
        Path: backend/routers/analytics.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return {k: v for k, v in doc.items() if k != "_id"}

    bld = _strip_mongo(bld_doc or {})
    unit = _strip_mongo(unit_doc or {})

    # Insurance: live policy takes priority over seeded value.
    # Only expiry_date is surfaced (renews_on), keeping sensitive premium data out of the owner endpoint.
    seeded_insurance: Dict[str, Any] = bld.get("insurance") or {}
    if live_policy and live_policy.get("expiry_date"):
        insurance_signal: Dict[str, Any] = {
            "renews_on": live_policy["expiry_date"][:10],
            # Preserve seeded no_premium_hike flag if present — we don't expose premiums to owners.
            "no_premium_hike": seeded_insurance.get("no_premium_hike"),
            "source": "live",
        }
    else:
        insurance_signal = seeded_insurance

    return {
        "building_id":         building_id,
        "unit_number":         unit_number,
        "emergency_contacts":  bld.get("emergency_contacts") or [],
        "market_signals":      unit.get("market_signals") or bld.get("market_signals") or {},
        "cost_categories":     unit.get("cost_categories") or [],
        "cost_yoy_delta_pct":  unit.get("cost_yoy_delta_pct"),
        "insurance":           insurance_signal,
        "badges":              unit.get("badges") or {},
        "owner_requests":      unit.get("owner_requests") or [],
        "source":              "seeded" if (bld or unit) else "empty",
    }
