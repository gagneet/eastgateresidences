from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from utils.auth import get_current_user, get_current_building, get_optional_building
from services.morning_card_service import compute_morning_card
from services.tenancy_passport_service import generate_passport_token, verify_passport_token
from database import db
from models.user import UserRole
from utils.staff_membership_repair import repair_orphan_staff_memberships
from datetime import datetime, timezone, timedelta
import logging
from pydantic import BaseModel, Field
from models.timestamps import timestamp_sort_key

router = APIRouter(prefix="/engagement", tags=["Engagement & Attention"])
logger = logging.getLogger(__name__)

_MANAGER_ROLES = {
    UserRole.EC_MEMBER,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.SUPER_ADMIN,
}


def _is_manager(user: dict) -> bool:
    """
    Returns True if the user holds a management role.
    Uses effective_role (set by building-switcher middleware) when present,
    falling back to the static role field on the user document.
    """
    role = user.get("effective_role") or user.get("role")
    return role in _MANAGER_ROLES


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class SmartRequestCreate(BaseModel):
    subject: str = Field(..., max_length=300)
    body: str = Field(..., max_length=5000)
    unit_number: Optional[str] = None
    source_channel: str = "portal_form"
    is_test_data: bool = False


def _can_mark_test_data(user: dict) -> bool:
    """Generated function header.

    Function: _can_mark_test_data
    Path: backend/routers/engagement.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = user.get("effective_role") or user.get("role")
    return role == UserRole.SUPER_ADMIN


# ---------------------------------------------------------------------------
# POST /engagement/requests/smart
# ---------------------------------------------------------------------------


@router.post("/requests/smart", summary="Submit a smart intake request")
async def create_smart_request(
        data: SmartRequestCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Classify an inbound message, set SLA clock, attempt auto-resolution,
    send acknowledgement notification, and return the created request.
    """
    from services.comms_intake_service import process_inbound

    doc = await process_inbound(
        source_channel=data.source_channel,
        sender_user_id=current_user["id"],
        sender_email=current_user.get("email"),
        unit_number=data.unit_number or current_user.get("unit_number"),
        subject=data.subject,
        body=data.body,
        building_id=building_id,
        is_test_data=data.is_test_data and _can_mark_test_data(current_user),
    )

    return {
        "id": doc["id"],
        "request_number": doc["request_number"],
        "request_type": doc["request_type"],
        "status": doc["status"],
        "auto_resolved": doc["auto_resolved"],
        "resolution_message": doc.get("resolution_message"),
        "sla_due_at": doc["sla_due_at"],
        "sla_hours": doc["sla_hours"],
        "created_at": doc["created_at"],
    }


# ---------------------------------------------------------------------------
# GET /engagement/requests  (resident: own requests; manager: all)
# ---------------------------------------------------------------------------


@router.get("/requests", summary="List requests (scoped by role)")
async def list_engagement_requests(
        status: Optional[str] = Query(None),
        request_type: Optional[str] = Query(None),
        limit: int = Query(50, le=200),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: list_engagement_requests
    Path: backend/routers/engagement.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query: dict = {"is_test_data": {"$ne": True}}
    if status:
        query["status"] = status
    if request_type:
        query["request_type"] = request_type

    if not _is_manager(current_user):
        query["submitted_by"] = current_user["id"]

    results = []
    cursor = db.workflow_requests.find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
    async for doc in cursor:
        results.append(doc)
    return results


# ---------------------------------------------------------------------------
# GET /engagement/requests/{id}/status  — resident self-service status timeline
# ---------------------------------------------------------------------------


@router.get("/requests/{request_id}/status", summary="Get request status timeline")
async def get_request_status(
        request_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Returns the request details plus a full audit-log-based timeline.
    Residents can only see their own requests; managers see all.
    """
    try:
        doc = await db.workflow_requests.find_one(
            {"id": request_id, "building_id": building_id, "is_test_data": {"$ne": True}}, {"_id": 0}
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Request not found")

        if not _is_manager(current_user) and doc.get("submitted_by") != current_user["id"]:
            raise HTTPException(status_code=403, detail="Access denied")

        from services.comms_intake_service import get_request_timeline

        try:
            timeline = await get_request_timeline(request_id, building_id, db)
        except Exception as e:
            logger.warning(f"Failed to fetch timeline for request {request_id}: {e}")
            timeline = []

        is_terminal = doc.get("status") in {"closed", "auto_resolved", "completed", "cancelled"}
        return {
            "id": doc.get("id", request_id),
            "request_number": doc.get("request_number"),
            "status": doc.get("status", "unknown"),
            "request_type": doc.get("request_type"),
            "subject": doc.get("subject") or doc.get("title"),
            "submitted_at": doc.get("created_at"),
            "sla_due_at": doc.get("sla_due_at"),
            "sla_breached": bool(doc.get("sla_breached", False)) and not is_terminal,
            "assigned_to_name": doc.get("assigned_to_name"),
            "last_updated": doc.get("updated_at"),
            "resolution_summary": doc.get("resolution_notes") or doc.get("resolution_message"),
            "auto_resolved": doc.get("auto_resolved", False),
            "auto_resolution_response": doc.get("auto_resolution_response"),
            "timeline": timeline,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching request status for {request_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve request status")


# ---------------------------------------------------------------------------
# GET /engagement/triage  — manager triage dashboard
# ---------------------------------------------------------------------------


@router.get("/triage", summary="Manager triage queue (overdue + needs-review)")
async def get_triage_queue(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_triage_queue
    Path: backend/routers/engagement.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not _is_manager(current_user):
        raise HTTPException(status_code=403, detail="Manager access required")

    now = datetime.now(timezone.utc).isoformat()

    # Overdue items
    overdue = []
    async for doc in db.workflow_requests.find(
            {
                "sla_breached": True,
                "status": {"$nin": ["closed", "auto_resolved"]},
                "is_test_data": {"$ne": True},
            },
            {"_id": 0, "id": 1, "request_number": 1, "request_type": 1,
             "unit_number": 1, "subject": 1, "sla_due_at": 1, "status": 1},
    ).sort("sla_due_at", 1).limit(20):
        doc["urgency"] = "overdue"
        overdue.append(doc)

    # Needs human review (not breached, but low-confidence classification)
    needs_review = []
    async for doc in db.workflow_requests.find(
            {
                "needs_human_review": True,
                "sla_breached": {"$ne": True},
                "status": {"$nin": ["closed", "auto_resolved"]},
                "is_test_data": {"$ne": True},
            },
            {"_id": 0, "id": 1, "request_number": 1, "request_type": 1,
             "unit_number": 1, "subject": 1, "sla_due_at": 1, "status": 1,
             "auto_resolution_confidence": 1},
    ).sort("created_at", -1).limit(20):
        doc["urgency"] = "review"
        needs_review.append(doc)

    # Due soon (within 2 hours, not yet breached)
    two_hours = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    due_soon = []
    async for doc in db.workflow_requests.find(
            {
                "sla_due_at": {"$lt": two_hours, "$gt": now},
                "sla_breached": {"$ne": True},
                "status": {"$nin": ["closed", "auto_resolved"]},
                "is_test_data": {"$ne": True},
            },
            {"_id": 0, "id": 1, "request_number": 1, "request_type": 1,
             "unit_number": 1, "subject": 1, "sla_due_at": 1, "status": 1},
    ).sort("sla_due_at", 1).limit(10):
        doc["urgency"] = "urgent"
        due_soon.append(doc)

    return {
        "overdue": overdue,
        "needs_review": needs_review,
        "due_soon": due_soon,
        "total_attention_needed": len(overdue) + len(needs_review) + len(due_soon),
    }


# ---------------------------------------------------------------------------
# GET /engagement/stats  — auto-resolve and deflection rate metrics
# ---------------------------------------------------------------------------


@router.get("/stats", summary="Comms intake deflection stats")
async def get_engagement_stats(
        days: int = Query(7, le=90),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Returns deflection rate and auto-resolve counts for the trailing N days
    (default: 7). All counts exclude `is_test_data: true` documents.
    Building scope is enforced via TenantScopedDatabase.
    """
    if not _is_manager(current_user):
        raise HTTPException(status_code=403, detail="Manager access required")

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()

    # base_q covers trailing-N-days and excludes test data in all counts
    base_q: dict = {"created_at": {"$gte": since}, "is_test_data": {"$ne": True}}

    total = await db.workflow_requests.count_documents(base_q)
    auto_resolved = await db.workflow_requests.count_documents(
        {**base_q, "auto_resolved": True}
    )
    auto_resolved_today = await db.workflow_requests.count_documents(
        {
            "auto_resolved": True,
            "created_at": {"$gte": today_start},
            "is_test_data": {"$ne": True},
        }
    )
    sla_breached = await db.workflow_requests.count_documents(
        {**base_q, "sla_breached": True}
    )

    deflection_rate = round(auto_resolved / total * 100, 1) if total else 0.0

    return {
        "period_days": days,
        "total_received": total,
        "auto_resolved": auto_resolved,
        "auto_resolved_today": auto_resolved_today,
        "deflection_rate_pct": deflection_rate,
        "sla_breached": sla_breached,
        "human_handled": total - auto_resolved,
    }


# ── S5: Morning Card ──────────────────────────────────────────────────────────

@router.get("/morning-card", summary="Single most relevant action card for this user")
async def get_morning_card(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_morning_card
    Path: backend/routers/engagement.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from utils.workflow_runner import workflow_run
    user_id = current_user["id"]
    user_role = current_user.get("effective_role") or current_user.get("role", "owner")
    unit_number = current_user.get("unit_number") or current_user.get("lot_id")
    async with workflow_run("morning_card_served", building_id, f"user:{user_id}") as run:
        run.trigger_type = "api"
        card = await compute_morning_card(user_id, user_role, unit_number, building_id)
        run.items_processed = 1
        run.add_artefact("card_served", card.get("card_type", "unknown"))
    return card


# ── S5: Building Pulse ────────────────────────────────────────────────────────

WORKFLOW_DISPLAY_NAMES = {
    "sla_breach_check": "SLA breach check",
    "building_summary_recompute": "Building health recompute",
    "proposal_auto_close": "Proposal auto-close",
    "compliance_deadline_check": "Compliance deadline check",
    "delete_expired_guest_tokens": "Expired guest cleanup",
}


@router.get("/building-pulse", summary="Recent building activity feed")
async def get_building_pulse(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_building_pulse
    Path: backend/routers/engagement.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    now = _dt.now(_tz.utc)
    week_ago = now - _td(days=7)
    events = []

    # Completed work orders
    completed_wos = await db.work_orders.find(
        {"status": "completed", "updated_at": {"$gte": week_ago.isoformat()},
         "is_test_data": {"$ne": True}},
        {"_id": 0, "title": 1, "updated_at": 1},
    ).sort("updated_at", -1).limit(2).to_list(2)
    for wo in completed_wos:
        events.append({"emoji": "✅", "text": f"Completed: {wo['title'][:50]}",
                       "timestamp": wo.get("updated_at", ""), "type": "maintenance"})

    # Recent announcements
    recent_anns = await db.announcements.find(
        {"created_at": {"$gte": week_ago.isoformat()}, "is_test_data": {"$ne": True}},
        {"_id": 0, "title": 1, "created_at": 1},
    ).sort("created_at", -1).limit(2).to_list(2)
    for ann in recent_anns:
        events.append({"emoji": "📢", "text": ann["title"][:60],
                       "timestamp": ann.get("created_at", ""), "type": "announcement"})

    # Volunteer events with participants
    vol_events = await db.volunteer_events.find(
        {"status": "open", "scheduled_date": {"$gte": now.isoformat()},
         "is_test_data": {"$ne": True}},
        {"_id": 0, "title": 1, "participants": 1},
    ).limit(1).to_list(1)
    for ve in vol_events:
        count = len(ve.get("participants") or [])
        if count > 0:
            events.append({
                "emoji": "🙋",
                "text": f"{count} neighbour{'s' if count != 1 else ''} signed up: {ve['title'][:40]}",
                "timestamp": now.isoformat(), "type": "volunteer"
            })

    # Recent savings
    saving = await db.savings_events.find_one(
        {"verified": True, "date": {"$gte": week_ago.isoformat()},
         "is_test_data": {"$ne": True}},
        {"_id": 0, "amount_saved_cents": 1, "date": 1},
        sort=[("date", -1)],
    )
    if saving:
        saved = (saving.get("amount_saved_cents") or 0) / 100
        if saved > 0:
            events.append({"emoji": "💰", "text": f"${saved:,.0f} saved through competitive tendering",
                           "timestamp": saving.get("date", ""), "type": "savings"})

    # Recent workflow automations (S5 addition)
    recent_runs = await db.workflow_runs.find(
        {"status": "success", "items_processed": {"$gt": 0},
         "started_at": {"$gte": week_ago.isoformat()}, "is_test_data": {"$ne": True}},
        {"_id": 0, "workflow_id": 1, "items_processed": 1, "started_at": 1},
    ).sort("started_at", -1).limit(2).to_list(2)
    for run in recent_runs:
        wf_name = WORKFLOW_DISPLAY_NAMES.get(run["workflow_id"], run["workflow_id"])
        events.append({
            "emoji": "⚙️",
            "text": f"Platform handled: {wf_name} ({run['items_processed']} items)",
            "timestamp": run["started_at"], "type": "automation"
        })

    # Same mixed-type hazard as the activity feed — see models/timestamps.
    events.sort(key=lambda e: timestamp_sort_key(e.get("timestamp")), reverse=True)
    return {"events": events[:5], "building_name": "East Gate Residences"}


@router.get("/property-finance/{unit_number}")
async def get_unit_finance_metrics(
        unit_number: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Investment metrics for a unit."""
    # Ensure user is owner of the unit or admin
    if current_user.get("role") != "super_admin" and current_user.get("unit_number") != unit_number:
        raise HTTPException(status_code=403, detail="Access denied to property finance data")

    from services.property_finance_service import get_property_finance_metrics
    return await get_property_finance_metrics(unit_number, building_id)


@router.post("/tenancy-passport/generate")
async def generate_passport(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generates a tenancy passport verification token with computed residency score."""
    from services.tenancy_passport_service import compute_residency_score
    unit_number = current_user.get("unit_number", "")
    # Compute the residency score (async — reads from DB)
    score_data = await compute_residency_score(current_user["id"], unit_number, building_id)
    # generate_passport_token is synchronous (pure JWT encode, no I/O)
    token = generate_passport_token(current_user["id"], unit_number, score_data["score"])
    return {
        "token": token,
        "verification_url": f"/verify-passport/{token}",
        "score": score_data["score"],
        "grade": score_data["grade"],
    }


@router.get("/tenancy-passport/verify/{token}")
async def verify_passport(token: str):
    """Public endpoint to verify a tenancy passport."""
    # verify_passport_token is synchronous (pure JWT decode, no I/O)
    result = verify_passport_token(token)
    if not result:
        raise HTTPException(status_code=404, detail="Invalid or expired token")
    return result


@router.get("/nav/badges")
async def get_nav_badges(
        current_user: dict = Depends(get_current_user),
        building_id: str | None = Depends(get_optional_building)
):
    """Counts for navigation badges (unread counts, etc).

    Returns zeroed counts when no building is in scope (super-admin
    pre-building-switch state) so the dashboard chrome can render without
    breaking on a 403.
    """
    if not building_id:
        return {
            "notifications": 0,
            "requests": 0,
            "notices": 0,
            "compliance": 0,
            "tenant_approvals": 0,
            "user_approvals": 0,
            "chat": 0,
        }

    user_id = current_user["id"]

    await repair_orphan_staff_memberships(building_id)

    # 1. Unread notifications
    unread_notifs = await db.user_notifications.count_documents({
        "user_id": user_id,
        "is_read": False
    })

    # 2. Open requests
    open_requests = await db.workflow_requests.count_documents({
        "submitted_by_user_id": user_id,
        "status": {"$nin": ["closed", "auto_resolved"]}
    })

    # 3. Total visible notices for this user — matches what the Notices page actually renders
    # (non-expired + role-scoped; acknowledgment NOT filtered so badge = page count)
    now_iso = datetime.now(timezone.utc).isoformat()
    user_role = current_user.get("effective_role") or current_user.get("role", "")
    unread_notices = await db.notices.count_documents({"$and": [
        {"building_id": building_id, "is_test_data": {"$ne": True}},
        {"$or": [{"expires_at": None}, {"expires_at": {"$gt": now_iso}}]},
        {"$or": [
            {"target_roles": None},
            {"target_roles": []},
            {"target_roles": user_role},
        ]},
    ]})

    # 4. Overdue compliance items (admin/EC only — returns 0 for regular users)
    _mgmt_roles = {UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER}
    overdue_compliance = 0
    effective_role = current_user.get("effective_role") or current_user.get("role")
    if effective_role in _mgmt_roles:
        now_iso = datetime.now(timezone.utc).isoformat()
        overdue_compliance = await db.compliance_items.count_documents({
            "building_id": building_id,
            "status": {"$in": ["overdue", "pending"]},
            "due_date": {"$lt": now_iso}
        })

    # 5. Pending tenant/guest approvals (owners see their unit; managers see all)
    tenant_approvals = 0
    if effective_role in {"owner", "strata_admin", "ec_member", "strata_manager", "super_admin"}:
        tenant_query: dict = {
            "building_id": building_id,
            "status": {"$in": ["pending_owner_approval", "pending"]},
            "role": {"$in": ["tenant", "guest"]},
            "is_approved": False,
            "is_test_data": {"$ne": True},
        }
        if effective_role == "owner":
            unit = current_user.get("unit_number") or current_user.get("lot_id")
            if unit:
                tenant_query["unit_number"] = unit
        tenant_approvals = await db.users.count_documents(tenant_query)

    # 6. Pending admin approvals for the Resident Management queue (management only)
    user_approvals = 0
    if effective_role in _mgmt_roles:
        pending_user_pipeline = [
            {"$match": {"building_id": building_id}},
            {"$group": {"_id": "$user_id"}},
            {"$lookup": {
                "from": "users",
                "localField": "_id",
                "foreignField": "id",
                "as": "user_info",
            }},
            {"$unwind": "$user_info"},
            {"$match": {
                "user_info.is_approved": False,
                "user_info.status": {"$nin": ["archived", "pending_owner_approval"]},
                "user_info.is_test_data": {"$ne": True},
            }},
            {"$count": "count"},
        ]
        user_approval_result = await db.memberships.aggregate(pending_user_pipeline).to_list(1)
        if user_approval_result:
            user_approvals = user_approval_result[0].get("count", 0)

    return {
        "notifications": unread_notifs,
        "requests": open_requests,
        "notices": unread_notices,
        "compliance": overdue_compliance,
        "tenant_approvals": tenant_approvals,
        "user_approvals": user_approvals,
        "chat": 0
    }
