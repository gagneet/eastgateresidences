# @featuretrace:morning-card — Computes the single most relevant dashboard action/insight card per user.
# Layer: service
# Data flow: MorningCard.tsx → GET /engagement/morning-card → engagement.py:get_morning_card →
#            this service → db.unit_levy_ledger / db.building_summaries / db.parcels / db.proposals /
#            db.savings_events / db.volunteer_events (building-scoped) → CTA link into a dashboard page.
# Related: frontend/src/components/dashboard/MorningCard.tsx
#           frontend/src/app/(dashboard)/dashboard/page.tsx
#           backend/routers/engagement.py
"""
Morning Card Service — computes the single most relevant action card for a user.
Priority: critical > action > savings > social > insight
"""
from datetime import datetime, timezone, timedelta

from typing import Optional

from database import db

# Kept in lockstep with routers/workflow_requests.py `_TERMINAL_STATUSES` /
# WorkflowRequestStatus.OVERDUE — the SLA card links straight into that endpoint's
# ?status=overdue view, so both sides must agree on what "overdue" means.
_TERMINAL_REQUEST_STATUSES = {"closed", "auto_resolved", "completed", "cancelled"}
_OVERDUE_STATUS = "overdue"

CARD_URGENCY = {
    "critical": 100,
    "action": 80,
    "savings": 60,
    "social": 40,
    "insight": 20,
}


async def compute_morning_card(user_id: str, user_role: str, unit_number: Optional[str],
                               building_id: str) -> dict:
    """Build the personalised "morning card" summary for a user.

    Tenant scoping note: reads go through TenantScopedDatabase, which injects
    building_id from the ambient request context. `building_id` is required
    here so a caller can never silently inherit another building's context;
    it previously defaulted to "13195", hardcoding East Gate.
    """
    now = datetime.now(timezone.utc)
    candidates = []

    # --- S5 addition: SLA breach for managers ---
    _MANAGER_ROLES = {"strata_manager", "super_admin", "strata_admin", "ec_member"}
    if user_role in _MANAGER_ROLES:
        # Must match GET /workflow-requests?status=overdue, or the card promises a
        # count the page it links to cannot show. That endpoint's MONGO path treats
        # "overdue" as (sla_breached OR status=="overdue") AND status not terminal,
        # with the terminal set including "completed"/"cancelled" — which this count
        # previously omitted, so a completed-but-breached request produced a
        # "1 request past SLA" card over an empty queue.
        #
        # KNOWN LIMITATION, not currently reachable: list_workflow_requests also has
        # a PostgreSQL branch (ops.cases) taken when `governance_read_pg_enabled` is
        # on for the building, and that branch defines overdue as
        # `due_at < now()` — a different predicate against a different store. This
        # card has no PG path and always counts Mongo. Verified 2026-08-20 that the
        # toggle is FALSE for every building, so both sides read Mongo today and
        # agree. If governance_read_pg_enabled is ever enabled, this count must gain
        # the same PG branch or the card will diverge from its own queue again.
        breached = await db.workflow_requests.count_documents({
            "$or": [{"sla_breached": True}, {"status": _OVERDUE_STATUS}],
            "status": {"$nin": list(_TERMINAL_REQUEST_STATUSES)},
            "is_test_data": {"$ne": True},
        })
        if breached > 0:
            candidates.append({
                "urgency": "critical",
                "score": CARD_URGENCY["critical"] + breached,
                "title": f"{breached} request{'s' if breached > 1 else ''} past SLA",
                "description": "Overdue owner requests damage trust. Each day delayed increases dispute risk.",
                "cta_label": "Handle now",
                # /requests renders the request FORM CATALOGUE by default — the
                # tracking list (which honours ?status=) lives behind
                # ?tab=my-requests. For a manager that list is the whole
                # building's queue, not their own requests (see `_is_manager` in
                # routers/workflow_requests.py), which is what this card counts.
                "cta_link": "/requests?tab=my-requests&status=overdue",
                "icon": "AlertTriangle",
                "card_type": "sla_breach_manager"
            })

    # --- CRITICAL: Levy overdue ---
    # Read from unit_levy_ledger.net_balance (live source of truth) — NOT units.balance_owing
    # which is a seeded snapshot and is never updated when payments are recorded.
    if user_role in ("owner", "ec_member", "strata_admin", "strata_manager", "super_admin") and unit_number:
        current_year = str(now.year)
        ledger = await db.unit_levy_ledger.find_one(
            {"building_id": building_id, "unit_number": unit_number, "year": current_year},
            {"_id": 0, "net_balance": 1}
        )
        owing = round(max(0.0, float((ledger or {}).get("net_balance", 0))), 2) if ledger else 0.0
        if owing > 0.01:
            candidates.append({
                "urgency": "critical",
                "score": CARD_URGENCY["critical"],
                "title": f"Levy overdue: ${owing:,.2f}",
                "description": "Your levy account has an outstanding balance. Late payment accrues statutory interest at 10% pa under the UTM Act.",
                "cta_label": "Pay now",
                "cta_link": "/financials/levy-payments",
                "icon": "AlertTriangle",
                "card_type": "levy_overdue"
            })

    # --- ACTION: Uncollected parcel ---
    if unit_number:
        parcel = await db.parcels.find_one(
            {"unit_number": unit_number, "status": "received", "is_test_data": {"$ne": True}},
            {"_id": 0, "id": 1, "carrier": 1, "received_date": 1}
        )
        if parcel:
            carrier = (parcel.get("carrier") or "courier").replace("_", " ").title()
            try:
                received = datetime.fromisoformat(parcel["received_date"].replace("Z", "+00:00"))
                days_waiting = (now - received).days
            except Exception:
                days_waiting = 0
            waiting_str = f"{days_waiting}d" if days_waiting > 0 else "today"
            candidates.append({
                "urgency": "action",
                "score": CARD_URGENCY["action"] + min(days_waiting, 5) * 2,
                "title": f"📦 {carrier} parcel waiting ({waiting_str})",
                "description": "Your parcel is at reception. Collect during staffed hours.",
                "cta_label": "View parcels",
                "cta_link": "/community/parcels",
                "icon": "Package",
                "card_type": "parcel_waiting"
            })

    # --- ACTION: Vote closing within 48h ---
    open_proposal = await db.proposals.find_one(
        {
            "status": "open",
            "voting_closes_at": {
                "$lte": (now + timedelta(hours=48)).isoformat(),
                "$gte": now.isoformat(),
            },
            "is_test_data": {"$ne": True},
        },
        {"_id": 0, "id": 1, "title": 1, "voting_closes_at": 1, "votes": 1},
    )
    if open_proposal and user_role == "owner":
        user_voted = any(v.get("user_id") == user_id for v in open_proposal.get("votes", []))
        if not user_voted:
            try:
                closes_at = datetime.fromisoformat(open_proposal["voting_closes_at"].replace("Z", "+00:00"))
                hours_left = max(0, (closes_at - now).total_seconds() / 3600)
            except Exception:
                hours_left = 24
            candidates.append({
                "urgency": "action",
                "score": CARD_URGENCY["action"] + (48 - hours_left),
                "title": f"Vote closing in {int(hours_left)}h",
                "description": f"{open_proposal['title']} — your vote hasn't been recorded yet.",
                "cta_label": "Vote now",
                "cta_link": "/governance/proposals",
                "icon": "Vote",
                "card_type": "vote_closing"
            })

    # --- ACTION: Pending work order approvals (managers) ---
    if user_role in ("strata_manager", "strata_admin", "super_admin", "ec_member"):
        pending_wo = await db.work_orders.count_documents(
            {"status": "pending_approval", "is_test_data": {"$ne": True}}
        )
        if pending_wo > 0:
            candidates.append({
                "urgency": "action",
                "score": CARD_URGENCY["action"],
                "title": f"{pending_wo} work order{'s' if pending_wo != 1 else ''} awaiting approval",
                "description": "Contractors are waiting. Delays increase building risk.",
                "cta_label": "Review now",
                "cta_link": "/requests/my-approvals",
                "icon": "ClipboardCheck",
                "card_type": "pending_approvals"
            })

    # --- SAVINGS: New savings event (S5: check shown_to) ---
    recent_saving_unshown = await db.savings_events.find_one(
        {
            "verified": True,
            "created_at": {"$gte": (now - timedelta(hours=24)).isoformat()},
            "shown_to": {"$not": {"$elemMatch": {"$eq": user_id}}},
            "is_test_data": {"$ne": True},
        }
    )
    if recent_saving_unshown:
        saved = (recent_saving_unshown.get("amount_saved_cents", 0) or 0) / 100
        per_lot = saved / 87
        candidates.append({
            "urgency": "savings",
            "score": CARD_URGENCY["savings"] + 10,
            "title": f"${saved:,.0f} saved for the building",
            "description": f"Your share: ~${per_lot:,.0f}. {recent_saving_unshown.get('resident_summary', '')}",
            "cta_label": "See savings",
            "cta_link": "/financials/savings",
            "icon": "TrendingDown",
            "card_type": "new_savings_milestone"
        })
        await db.savings_events.update_one(
            {"id": recent_saving_unshown["id"]},
            {"$addToSet": {"shown_to": user_id}}
        )
    else:
        # Fallback: recent savings (last 7 days)
        recent_saving = await db.savings_events.find_one(
            {"verified": True, "date": {"$gte": (now - timedelta(days=7)).isoformat()},
             "is_test_data": {"$ne": True}},
            {"_id": 0, "resident_summary": 1, "amount_saved_cents": 1},
            sort=[("date", -1)],
        )
        if recent_saving:
            saved = (recent_saving.get("amount_saved_cents", 0) or 0) / 100
            if saved > 0:
                candidates.append({
                    "urgency": "savings",
                    "score": CARD_URGENCY["savings"],
                    "title": f"${saved:,.0f} saved for the building this week",
                    "description": recent_saving.get("resident_summary",
                                                     "Competitive tendering saved money for your building."),
                    "cta_label": "See all savings",
                    "cta_link": "/financials/savings",
                    "icon": "TrendingDown",
                    "card_type": "savings_achieved"
                })

    # --- SOCIAL: Open volunteer event ---
    volunteer_event = await db.volunteer_events.find_one(
        {"status": "open", "scheduled_date": {"$gte": now.isoformat()},
         "is_test_data": {"$ne": True}},
        {"_id": 0, "id": 1, "title": 1, "estimated_contractor_cost_cents": 1},
        sort=[("scheduled_date", 1)],
    )
    if volunteer_event:
        cost = volunteer_event.get("estimated_contractor_cost_cents", 0) or 0
        value_str = f" — earn up to ${cost / 100:,.0f} in levy credits" if cost else ""
        candidates.append({
            "urgency": "social",
            "score": CARD_URGENCY["social"],
            "title": f"Help wanted: {volunteer_event['title']}",
            "description": f"Earn levy credits by helping your building{value_str}.",
            "cta_label": "See details",
            "cta_link": "/community/volunteer",
            "icon": "Users",
            "card_type": "volunteer_open"
        })

    # --- INSIGHT: Building health score ---
    #
    # This card is only offered when there is a real score to show. It used to
    # render whatever was in the summary document, which produced
    # "Building health: 75/100 (Grade )" and "Your building is in  condition."
    # on a platform with no data:
    #
    #   * the score was a genuine 75 — that is what the old formula returned for
    #     an empty building, because absent inputs scored full marks; and
    #   * the grade was blank because the writer stored `health_grade` while this
    #     reader asked for `building_health_grade`, so the lookup always missed.
    #
    # Both are fixed at source. This guard is the belt-and-braces: an empty or
    # unrecognised grade must suppress the card, never render a sentence with a
    # hole in it.
    summary = await db.building_summaries.find_one(
        {},
        {
            "_id": 0,
            "building_health_score": 1,
            "building_health_grade": 1,
            "health_score": 1,
            "health_grade": 1,
            "health_status": 1,
        },
    )
    if summary:
        score = summary.get("building_health_score")
        if score is None:
            score = summary.get("health_score")
        grade = summary.get("building_health_grade") or summary.get("health_grade") or ""
        grade_label = {"A": "excellent", "B": "good", "C": "watch", "D": "risk"}.get(
            str(grade).strip().upper()[:1], ""
        )
        # health_status must be present AND "ok". Documents written before the
        # 2026-08-24 rewrite carry no health_status at all, and their stored
        # scores came from the formula that graded empty buildings at 75 — so
        # absence of the marker means "computed by the untrusted formula", not
        # "fine". Requiring it makes the fix self-healing: a stale row stays
        # suppressed until the analytics worker recomputes it.
        publishable = (
            summary.get("health_status") == "ok"
            and isinstance(score, (int, float))
            and bool(grade_label)
        )
        if publishable:
            candidates.append({
                "urgency": "insight",
                "score": CARD_URGENCY["insight"],
                "title": f"Building health: {score}/100 (Grade {grade})",
                "description": f"Your building is in {grade_label} condition. Tap to see the breakdown.",
                "cta_label": "See health score",
                "cta_link": "/intelligence/building-health",
                "icon": "Activity",
                "card_type": "health_score"
            })

    if not candidates:
        return {
            "urgency": "insight",
            "title": "Welcome back to East Gate",
            "description": "Everything is up to date. Your building is running smoothly.",
            "cta_label": "View dashboard",
            "cta_link": "/dashboard",
            "icon": "Home",
            "card_type": "default"
        }

    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]
    best.pop("score", None)
    return best
