# @featuretrace:community-hub — Community Dashboard API: aggregates building-wide stats for the Community Hub page.
# Layer: router
# Data flow: CommunityPage.jsx → /community-dashboard/* → community dashboard aggregation
#            → units, maintenance_requests, workflow_requests, pet_requests, amenity_bookings,
#            proposals, volunteer_events, building_summaries (building-scoped).
# Related: frontend/src/pages/dashboard/CommunityPage.jsx
#           backend/services/health_score_service.py
#           backend/services/savings_engine.py

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from database import db
from models.community_os import BuildingSummary
from models.user import UserRole
from utils.auth import get_current_user, get_current_building
from services.health_score_service import compute_building_health_score
from services.savings_engine import get_savings_summary

router = APIRouter(prefix="/community-dashboard", tags=["Community Dashboard"])
logger = logging.getLogger(__name__)

_ADMIN_ROLES = {
    UserRole.EC_MEMBER,
    UserRole.STRATA_MANAGER,
    UserRole.SUPER_ADMIN,
}


def _require_admin(user: dict):
    """Generated function header.

    Function: _require_admin
    Path: backend/routers/community_dashboard.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = user.get("effective_role") or user.get("role")
    if role not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _build_health_data(building_id: str) -> dict:
    """
    Fetch building health and community metrics.
    Performance Optimization⚡: Parallelized 10+ sequential DB calls into a single asyncio.gather block.
    """
    now = datetime.now(timezone.utc)

    pipeline_sinking = [
        {"$match": {"building_id": building_id}},
        {"$group": {"_id": None, "total": {"$sum": "$balance_cents"}}}
    ]

    async def _canonical_arrears_lots():
        # Grace-aware canonical count (GAP-FIN-040) — never the stale
        # units.arrears_balance seed field; agrees with /finance/summary et al.
        from utils.finance_helpers import get_building_arrears_summary
        summary = await get_building_arrears_summary(building_id)
        return summary["units_in_arrears"]

    # Standard counts
    tasks = [
        db.units.count_documents({"building_id": building_id}),
        _canonical_arrears_lots(),
        db.work_orders.count_documents({"building_id": building_id, "status": "overdue"}),
        db.work_orders.count_documents(
            {"building_id": building_id, "status": {"$in": ["open", "in_progress", "overdue"]}}),
        # Total work orders ever recorded. The health score needs this to tell
        # "nothing is overdue" apart from "nothing is tracked" — without it a
        # building with no work orders scored perfect maintenance.
        db.work_orders.count_documents({"building_id": building_id}),
        db.proposals.count_documents({"building_id": building_id, "status": "open"}),
        db.volunteer_events.count_documents({"building_id": building_id, "status": "completed"}),
        # Total volunteer events ever recorded, for the same reason work_orders_total
        # exists above: it is the only way the engagement axis can tell "no events
        # have been completed" apart from "this building has never used volunteer
        # events at all". Without it an empty collection scored engagement 0/100 —
        # a full red bar asserting a disengaged community on the basis of no data.
        db.volunteer_events.count_documents({"building_id": building_id}),
        db.workflow_requests.count_documents(
            {"building_id": building_id, "status": {"$nin": ["closed", "auto_resolved"]}}),
        db.pet_requests.count_documents({"building_id": building_id, "status": "approved"}),
        db.amenity_bookings.count_documents({
            "building_id": building_id,
            "start_time": {"$gte": now.isoformat()}
        })
    ]

    # Parallelize all counts and the aggregation
    # Bolt ⚡: In Motor, aggregate() returns a cursor (async generator), which is NOT a coroutine.
    # We must await its results separately or wrap it in a coroutine.
    async def get_sinking_balance():
        """Generated function header.

        Function: get_sinking_balance
        Path: backend/routers/community_dashboard.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        sinking_res = []
        async for row in db.sinking_fund_accounts.aggregate(pipeline_sinking):
            sinking_res.append(row)
        return sinking_res

    # Compliance register, appended AFTER the existing entries so every results[N]
    # index above keeps its meaning. Both counts are needed, not just the overdue one:
    # _compliance() treats "0 overdue with no register size" as unavailable, because
    # "0 of 0 overdue" is an empty register rather than a clean bill of health.
    # @featuretrace:by-law-breach-register — Dispute axis input for Building Pulse.
    # Layer: router
    # Data flow: by_law_breach_reports (building-scoped) -> disputes_unresolved/_total
    #            -> data["open_disputes"] -> health_score_service._dispute.
    # Related: backend/models/by_law_breach.py, backend/services/health_score_service.py
    #
    # Dispute register, appended on the same principle. Two counts again, for the same
    # reason: a building that has never filed a breach report has an EMPTY register, not
    # a clean one, and scoring it 100/100 on disputes would hand 10% of the health score
    # to a building we have no dispute evidence for at all. Only a register with history
    # can report a meaningful zero.
    from models.by_law_breach import BreachStatus

    tasks = list(tasks) + [
        db.compliance_items.count_documents({"building_id": building_id, "status": "overdue"}),
        db.compliance_items.count_documents({"building_id": building_id}),
        db.by_law_breach_reports.count_documents(
            {"building_id": building_id, "status": {"$in": BreachStatus.UNRESOLVED}}
        ),
        db.by_law_breach_reports.count_documents({"building_id": building_id}),
    ]

    results = await asyncio.gather(*tasks, get_sinking_balance())

    total_lots = results[0]
    arrears_lots = results[1]
    overdue_wo = results[2]
    open_wo = results[3]
    work_orders_total = results[4]
    open_proposals = results[5]
    volunteer_ytd = results[6]
    volunteer_total = results[7]
    open_workflows = results[8]  # smart requests; NOT disputes (see below)
    registered_pets = results[9]
    upcoming_bookings = results[10]
    compliance_overdue = results[11]
    compliance_total = results[12]
    disputes_unresolved = results[13]
    disputes_total = results[14]
    sinking_res = results[15]

    sinking_balance = sinking_res[0].get("total", 0) / 100 if sinking_res and len(sinking_res) > 0 else 0

    return {
        "total_lots": total_lots,
        "sinking_fund_balance": sinking_balance,
        # The 10-year capital-works forecast was derived as `sinking_balance * 1.2`,
        # which made sinking adequacy a constant 0.833 for every building with any
        # balance at all — the metric could not detect an underfunded reserve
        # because the target moved with the balance. There is no forecast source
        # wired here, so it is now explicitly unavailable and the adequacy half of
        # the financial component is excluded rather than fabricated.
        "capital_works_10yr_forecast": None,
        "arrears_lots": arrears_lots,
        "overdue_work_orders": overdue_wo,
        "open_work_orders": open_wo,
        "work_orders_total": work_orders_total,
        # No work-order age rollup and no compliance register query here; both
        # were hardcoded (14 days, 0 overdue) and both scored well. None keeps
        # them out of the score. /community/health-score and the analytics worker
        # deliberately report different coverage rather than pretending to the
        # same completeness.
        "avg_work_order_age_days": None,
        # Queried, not hardcoded. Until 2026-08-27 this was None, and before
        # 2026-08-24 it was a literal 0 — which scored full marks for every building
        # regardless of its register. East Gate has 16 items, 10 of them overdue, so
        # the axis now reports a real measurement instead of "unavailable".
        #
        # compliance_items_total accompanies it deliberately: without a register size,
        # _compliance() cannot distinguish "nothing overdue" from "nothing tracked",
        # and correctly refuses to score the second.
        "compliance_items_overdue": compliance_overdue,
        "compliance_items_total": compliance_total,
        "vote_participation_rate": None,
        "volunteer_events_ytd": volunteer_ytd,
        # None when the building has never recorded a volunteer event, so the
        # engagement axis reports "unavailable" instead of a measured zero.
        "volunteer_events_total": volunteer_total,
        # open_workflows is a count of open workflow requests, not disputes. It was
        # once passed as `open_disputes`, so ordinary maintenance requests depressed the
        # dispute score; it is deliberately not used here.
        #
        # The real source is the by-law breach register (GAP-OPS-005), which has existed
        # and been routed the whole time but was never read by anything — so this stayed
        # hardcoded None and Building Pulse showed dispute as permanently unavailable.
        #
        # `disputes_total == 0` means the register is EMPTY, which is not the same as a
        # building with no disputes: it is a building that has never recorded one, and we
        # have no evidence either way. That stays None so _dispute() drops the component
        # and redistributes its weight. Once any report exists — including resolved ones —
        # a count of zero unresolved is a real, earned zero.
        "open_disputes": disputes_unresolved if disputes_total > 0 else None,
        "disputes_total": disputes_total,
        "registered_pets": registered_pets,
        "open_smart_requests": open_workflows,
        "upcoming_bookings": upcoming_bookings,
        "open_proposals": open_proposals  # Bolt ⚡: Return this to avoid redundant fetch in _compute_summary
    }


@router.get("/building-summary")
async def get_building_summary(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_building_summary
    Path: backend/routers/community_dashboard.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await db.building_summaries.find_one({"building_id": building_id}, {"_id": 0})
    if doc:
        # Stored summaries written before the 2026-08-24 health-score rewrite carry
        # a score produced by the formula that graded an EMPTY building at 75/100,
        # and no `health_status` marker. Serving one unchanged would keep showing
        # that number until the analytics worker happened to run. Strip the health
        # fields from any document that predates the marker so the caller sees
        # "not computed" rather than a fabricated grade.
        if doc.get("health_status") is None:
            for stale in (
                "building_health_score", "building_health_grade",
                "health_score", "health_grade", "health_components",
            ):
                doc.pop(stale, None)
            doc["health_status"] = "insufficient_data"
        return doc

    summary = await _compute_summary(building_id)
    return summary


@router.get("/health-score")
async def get_health_score(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_health_score
    Path: backend/routers/community_dashboard.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = await _build_health_data(building_id)
    return compute_building_health_score(data)


@router.post("/recompute")
async def recompute_building_summary(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: recompute_building_summary
    Path: backend/routers/community_dashboard.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_admin(current_user)
    summary = await _compute_summary(building_id)
    return {"status": "ok", "summary": summary}


async def _compute_summary(building_id: str) -> dict:
    """
    Compute building-wide summary metrics for persistence.
    Performance Optimization⚡: Parallelized independent lookups (maintenance + savings + health data).
    """
    now = datetime.now(timezone.utc)
    financial_year = str(now.year) if now.month >= 7 else str(now.year - 1)
    today_iso = now.date().isoformat()

    async def _levy_totals():
        """Generated function header.

        Function: _levy_totals
        Path: backend/routers/community_dashboard.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        # Canonical grace-aware arrears (GAP-FIN-040); total_levied stays a raw
        # ledger roll-up (not an arrears calc).
        from utils.finance_helpers import get_building_arrears_summary
        pipeline = [
            {"$match": {"building_id": building_id, "year": financial_year}},
            {"$group": {"_id": None, "total_levied": {"$sum": "$total_levied"}}},
        ]
        rows = await db.unit_levy_ledger.aggregate(pipeline).to_list(1)
        total_levied = rows[0].get("total_levied", 0.0) if rows else 0.0
        arrears = await get_building_arrears_summary(building_id, financial_year)
        return {"total_levied": total_levied, "arrears": arrears["true_arrears_amount"]}

    async def _next_compliance():
        """Generated function header.

        Function: _next_compliance
        Path: backend/routers/community_dashboard.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        item = await db.compliance_items.find_one(
            {"building_id": building_id, "status": {"$nin": ["completed"]}, "due_date": {"$gte": today_iso}},
            {"_id": 0, "title": 1, "due_date": 1},
            sort=[("due_date", 1)],
        )
        if not item:
            return None
        return f"{item['title']} ({item['due_date'][:10]})"

    tasks = [
        _build_health_data(building_id),
        get_savings_summary(building_id, financial_year, db),
        db.maintenance_requests.count_documents(
            {"building_id": building_id, "status": {"$nin": ["completed", "rejected"]}}),
        _levy_totals(),
        _next_compliance(),
    ]

    results = await asyncio.gather(*tasks)
    health_data = results[0]
    savings = results[1]
    open_maintenance = results[2]
    levy_totals = results[3]
    next_compliance_item = results[4]

    health = compute_building_health_score(health_data)

    total_lots = health_data["total_lots"]
    total_levies_ytd_cents = int(levy_totals["total_levied"] * 100)
    arrears_cents = int(levy_totals["arrears"] * 100)
    arrears_rate = (
        round(levy_totals["arrears"] / levy_totals["total_levied"] * 100, 1)
        if levy_totals["total_levied"] > 0 else 0.0
    )

    summary = {
        "id": building_id + "_summary",
        "building_id": building_id,
        "total_lots": total_lots,
        "occupied_lots": total_lots,
        "total_levies_ytd_cents": total_levies_ytd_cents,
        "arrears_cents": arrears_cents,
        "arrears_rate": arrears_rate,
        "arrears_lots": health_data["arrears_lots"],
        "sinking_fund_balance_cents": int(health_data["sinking_fund_balance"] * 100),
        "admin_fund_balance_cents": 0,
        "open_maintenance_requests": open_maintenance,
        "overdue_work_orders": health_data["overdue_work_orders"],
        "open_proposals": health_data.get("open_proposals", 0),
        "volunteer_events_ytd": health_data["volunteer_events_ytd"],
        "savings_ytd_cents": savings["ytd_saved_cents"],
        "health_score": health["score"],
        "health_grade": health["grade"],
        "next_compliance_item": next_compliance_item,
        # Community extras
        "registered_pets": health_data.get("registered_pets", 0),
        "open_smart_requests": health_data.get("open_smart_requests", 0),
        "upcoming_bookings": health_data.get("upcoming_bookings", 0),
        "computed_at": now.isoformat(),
        "financial_year": financial_year,
    }

    await db.building_summaries.update_one(
        {"building_id": building_id},
        {"$set": summary},
        upsert=True,
    )
    return summary
