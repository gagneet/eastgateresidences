# @featuretrace:smart-request — Workflow Requests router: handles smart request submission, triage, status, and timeline.
# Layer: router
# Data flow: ManagementDashboard → GET /workflow-requests/stats/triage → db.workflow_requests + db.users (building-scoped)
#            SmartRequestPage.jsx → POST /workflow-requests/smart → comms_intake_service.process_inbound()
#            → db.workflow_requests (building-scoped) + db.audit_logs.
# Related: frontend/src/pages/dashboard/SmartRequestPage.jsx
#           frontend/src/pages/dashboard/RequestsPage.jsx
#           frontend/src/app/(dashboard)/dashboard/page.tsx
#           frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx
#           backend/services/comms_intake_service.py

import asyncio
import logging
import html as html_lib
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query

from database import db
from models.community_os import (
    WorkflowRequestCreate,
    WorkflowRequestSmartCreate,
    WorkflowRequestResponse,
    WorkflowRequestStatus,
    WorkflowRequestStatusUpdate,
)
from models.user import UserRole
from utils.auth import get_current_user, get_current_building
from utils.helpers import create_audit_log
from utils.permissions import require_feature
from services.comms_intake_service import process_inbound

router = APIRouter(prefix="/workflow-requests", tags=["Workflow Requests"])
logger = logging.getLogger(__name__)

_MANAGER_ROLES = {
    UserRole.EC_MEMBER,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.SUPER_ADMIN,
}

_TERMINAL_STATUSES = {
    WorkflowRequestStatus.CLOSED,
    WorkflowRequestStatus.AUTO_RESOLVED,
    "completed",
    "cancelled",
}


def _is_manager(user: dict) -> bool:
    """Generated function header.

    Function: _is_manager
    Path: backend/routers/workflow_requests.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = user.get("effective_role") or user.get("role")
    return role in _MANAGER_ROLES


def _can_mark_test_data(user: dict) -> bool:
    """Generated function header.

    Function: _can_mark_test_data
    Path: backend/routers/workflow_requests.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = user.get("effective_role") or user.get("role")
    return role == UserRole.SUPER_ADMIN


def _is_terminal_status(status: str | None) -> bool:
    """Generated function header.

    Function: _is_terminal_status
    Path: backend/routers/workflow_requests.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return status in _TERMINAL_STATUSES


@router.get("", response_model=List[WorkflowRequestResponse])
async def list_workflow_requests(
        status: Optional[str] = Query(None),
        request_type: Optional[str] = Query(None),
        limit: Optional[int] = Query(None, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """List workflow requests. MongoDB is primary; PostgreSQL only when toggle-gated.

    MongoDB is the live operational store. The Postgres path (ops.cases) is only
    attempted when GOVERNANCE_READ_PG_ENABLED is on for this building.
    """
    is_manager = _is_manager(current_user)
    row_limit = limit if isinstance(limit, int) else None

    # PostgreSQL read path: only when governance cutover toggle is enabled.
    try:
        from services.cutover_config_service import is_cutover_feature_enabled, GOVERNANCE_READ_PG_ENABLED
        if await is_cutover_feature_enabled(building_id, GOVERNANCE_READ_PG_ENABLED):
            from db_postgres.repos.config_repo import resolve_scheme_context
            from db_postgres.session import async_session_context, set_tenant
            from sqlalchemy import text as pg_text

            scheme = await resolve_scheme_context(building_id)
            if scheme:
                sid = str(scheme["scheme_id"])
                tid = str(scheme["tenant_id"])
                current_user_id = str(current_user.get("id") or current_user.get("user_id") or "")

                where_parts = ["c.scheme_id = :sid", "c.deleted_at IS NULL", "c.is_test_data = FALSE"]
                params = {"sid": sid}

                if not is_manager:
                    where_parts.append("c.created_by = CAST(:current_user_id AS uuid)")
                    params["current_user_id"] = current_user_id

                if request_type:
                    where_parts.append("(c.category = :request_type OR c.source_type = :request_type)")
                    params["request_type"] = request_type

                if status == WorkflowRequestStatus.OVERDUE:
                    where_parts.append("c.due_at IS NOT NULL AND c.due_at < now()")
                    where_parts.append("c.status NOT IN ('completed', 'closed', 'cancelled')")
                elif status == WorkflowRequestStatus.AWAITING_REVIEW:
                    where_parts.append("c.status IN ('waiting_for_approval', 'triaged', 'waiting_for_info')")
                elif status == WorkflowRequestStatus.IN_PROGRESS:
                    where_parts.append("c.status IN ('assigned', 'in_progress', 'scheduled', 'resident_notified')")
                elif status == WorkflowRequestStatus.CLOSED:
                    where_parts.append("c.status IN ('completed', 'closed', 'cancelled')")
                elif status:
                    where_parts.append("c.status = :status")
                    params["status"] = status

                where_sql = " AND ".join(where_parts)
                limit_sql = "LIMIT :limit" if row_limit is not None else ""
                if row_limit is not None:
                    params["limit"] = row_limit

                async with async_session_context() as session:
                    await set_tenant(session, tid)
                    rows = await session.execute(
                        pg_text(f"""
                            SELECT
                                c.id::text AS id,
                                c.title,
                                c.description,
                                c.category AS request_type,
                                c.priority,
                                c.status,
                                c.created_by::text AS submitted_by_user_id,
                                c.assigned_to::text AS assigned_to,
                                c.created_at,
                                c.updated_at,
                                c.due_at,
                                l.lot_number::text AS unit_number,
                                CASE
                                    WHEN c.due_at IS NOT NULL
                                     AND c.due_at < now()
                                     AND c.status NOT IN ('completed', 'closed', 'cancelled')
                                    THEN TRUE ELSE FALSE
                                END AS sla_breached
                            FROM ops.cases c
                            LEFT JOIN LATERAL (
                                SELECT lot_number
                                FROM core.lots
                                WHERE scheme_id = c.scheme_id
                                  AND lot_id = ANY(c.related_lot_ids)
                                ORDER BY lot_number
                                LIMIT 1
                            ) l ON TRUE
                            WHERE {where_sql}
                            ORDER BY
                                CASE WHEN c.due_at IS NOT NULL AND c.due_at < now() THEN 0 ELSE 1 END,
                                c.priority DESC,
                                c.created_at DESC
                            {limit_sql}
                        """),
                        params,
                    )
                    pg_items = []
                    for row in rows.mappings().all():
                        created_at = row["created_at"]
                        updated_at = row["updated_at"]
                        due_at = row["due_at"]
                        pg_status = row["status"]
                        if row["sla_breached"]:
                            pg_status = WorkflowRequestStatus.OVERDUE
                        pg_items.append({
                            "id": row["id"],
                            "building_id": building_id,
                            "request_number": f"CASE-{row['id'][:8]}",
                            "request_type": row["request_type"] or "general_enquiry",
                            "title": row["title"],
                            "description": row["description"],
                            "subject": row["title"],
                            "body": row["description"],
                            "unit_number": row["unit_number"],
                            "priority": row["priority"] or "normal",
                            "status": pg_status,
                            "submitted_by_user_id": row["submitted_by_user_id"],
                            "assigned_to": row["assigned_to"],
                            "sla_due_at": due_at.isoformat() if due_at else None,
                            "sla_breached": bool(row["sla_breached"]),
                            "needs_human_review": pg_status in {"triaged", "waiting_for_approval", WorkflowRequestStatus.OVERDUE},
                            "created_at": created_at.isoformat() if created_at else datetime.now(timezone.utc).isoformat(),
                            "updated_at": updated_at.isoformat() if updated_at else datetime.now(timezone.utc).isoformat(),
                        })

                    if pg_items:
                        return pg_items
    except Exception as exc:
        logger.warning("workflow_requests.list: PostgreSQL path skipped or unavailable: %s", exc)

    # MongoDB primary path. TenantCollection injects building/plan compatibility filter automatically.
    query: dict = {"is_test_data": {"$ne": True}}
    if status == WorkflowRequestStatus.OVERDUE:
        query["$or"] = [
            {"sla_breached": True},
            {"status": WorkflowRequestStatus.OVERDUE},
        ]
        query["status"] = {"$nin": list(_TERMINAL_STATUSES)}
    elif status:
        query["status"] = status
    if request_type:
        query["request_type"] = request_type

    if not is_manager:
        query["submitted_by_user_id"] = current_user["id"]

    cursor = db.workflow_requests.find(query, {"_id": 0}).sort("created_at", -1)
    return await cursor.to_list(row_limit)


@router.post("", response_model=WorkflowRequestResponse)
async def create_workflow_request(
        data: WorkflowRequestCreate,
        _f: dict = Depends(require_feature("smart_requests")),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    # Backward compatibility for structured forms, but now routed through process_inbound
    """Generated function header.

    Function: create_workflow_request
    Path: backend/routers/workflow_requests.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await process_inbound(
        source_channel="portal_form",
        sender_user_id=current_user["id"],
        sender_email=current_user.get("email"),
        unit_number=data.unit_number,
        subject=data.title,
        body=data.description,
        building_id=building_id
    )
    # If priority was specified, update it
    if data.priority != "normal":
        await db.workflow_requests.update_one({"id": doc["id"]}, {"$set": {"priority": data.priority}})
        doc["priority"] = data.priority

    return WorkflowRequestResponse(**doc)


@router.post("/smart", response_model=WorkflowRequestResponse)
async def create_smart_workflow_request(
        data: WorkflowRequestSmartCreate,
        _f: dict = Depends(require_feature("smart_requests")),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Smart intake for unstructured requests."""
    doc = await process_inbound(
        source_channel="portal_form",
        sender_user_id=current_user["id"],
        sender_email=current_user.get("email"),
        unit_number=data.unit_number,
        subject=data.subject,
        body=data.body,
        attachments=data.attachments,
        building_id=building_id,
        is_test_data=data.is_test_data and _can_mark_test_data(current_user),
    )
    return WorkflowRequestResponse(**doc)


@router.get("/{request_id}", response_model=WorkflowRequestResponse)
async def get_workflow_request(
        request_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    # TenantCollection scopes the lookup; relying on it preserves legacy plan_id-only docs.
    """Generated function header.

    Function: get_workflow_request
    Path: backend/routers/workflow_requests.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await db.workflow_requests.find_one({"id": request_id, "is_test_data": {"$ne": True}}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")
    if not _is_manager(current_user) and doc.get("submitted_by_user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    return WorkflowRequestResponse(**doc)


@router.get("/{request_id}/status")
async def get_workflow_request_status(
        request_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Returns request status with a timeline built from audit logs."""
    doc = await db.workflow_requests.find_one({"id": request_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")

    if not _is_manager(current_user) and doc.get("submitted_by_user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Performance Optimization⚡: Using to_list() for faster retrieval
    audit_entries = await db.audit_logs.find(
        {"resource_type": "workflow_request", "resource_id": request_id},
        {"_id": 0}
    ).sort("created_at", 1).to_list(100)

    timeline = []
    for entry in audit_entries:
        timeline.append({
            "timestamp": entry["created_at"],
            "actor": entry["user_name"],
            "action": entry["action"],
            "note": entry.get("details", {}).get("note") if isinstance(entry.get("details"), dict) else None
        })

    return {
        "id": doc["id"],
        "request_number": doc.get("request_number"),
        "status": doc["status"],
        "request_type": doc["request_type"],
        "submitted_at": doc["created_at"],
        "sla_due_at": doc.get("sla_due_at"),
        "sla_breached": doc.get("sla_breached", False),
        "assigned_to_name": doc.get("assigned_to_name"),
        "last_updated": doc["updated_at"],
        "resolution_summary": doc.get("resolution_notes"),
        "auto_resolution_response": doc.get("auto_resolution_response"),
        "timeline": timeline
    }


@router.get("/stats/triage")
async def get_triage_stats(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Returns triage ROI metrics for the manager dashboard.

    MongoDB is the primary source. PostgreSQL (ops.cases) is only consulted for
    supplemental auto-resolution metrics when GOVERNANCE_READ_PG_ENABLED is on.
    """
    if not _is_manager(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    now = datetime.now(timezone.utc)
    today_start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start_dt = now - timedelta(days=7)
    today_start = today_start_dt.isoformat()
    week_start = week_start_dt.isoformat()

    async def _count_pending_registrations() -> int:
        # Keep parity with admin stats logic:
        # pending = is_approved=false and status not in (archived, pending_owner_approval)
        """Generated function header.

        Function: _count_pending_registrations
        Path: backend/routers/workflow_requests.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        rows = await db.memberships.aggregate([
            {"$match": {"building_id": building_id, "is_test_data": {"$ne": True}}},
            {"$group": {"_id": "$user_id"}},
            {
                "$lookup": {
                    "from": "users",
                    "localField": "_id",
                    "foreignField": "id",
                    "as": "user_info",
                }
            },
            {"$unwind": "$user_info"},
            {
                "$match": {
                    "user_info.is_test_data": {"$ne": True},
                    "user_info.is_approved": False,
                    "user_info.status": {"$nin": ["archived", "pending_owner_approval"]},
                }
            },
            {"$count": "count"},
        ]).to_list(1)
        if not rows:
            return 0
        return int(rows[0].get("count") or 0)

    # ── MongoDB primary path ──────────────────────────────────────────────────
    tasks = [
        db.workflow_requests.count_documents({
            "status": WorkflowRequestStatus.AUTO_RESOLVED,
            "created_at": {"$gte": today_start},
            "is_test_data": {"$ne": True},
        }),
        db.workflow_requests.count_documents({
            "created_at": {"$gte": week_start},
            "is_test_data": {"$ne": True},
        }),
        db.workflow_requests.count_documents({
            "status": WorkflowRequestStatus.AUTO_RESOLVED,
            "created_at": {"$gte": week_start},
            "is_test_data": {"$ne": True},
        }),
        db.workflow_requests.count_documents({
            "status": {"$in": [WorkflowRequestStatus.OVERDUE, WorkflowRequestStatus.AWAITING_REVIEW]},
            "is_test_data": {"$ne": True},
        }),
        db.workflow_requests.count_documents({
            "status": {"$nin": [WorkflowRequestStatus.CLOSED, WorkflowRequestStatus.AUTO_RESOLVED, "completed", "cancelled"]},
            "is_test_data": {"$ne": True},
        }),
        db.workflow_requests.count_documents({
            "$or": [
                {"sla_breached": True},
                {"status": WorkflowRequestStatus.OVERDUE},
            ],
            "status": {"$nin": [WorkflowRequestStatus.CLOSED, WorkflowRequestStatus.AUTO_RESOLVED, "completed", "cancelled"]},
            "is_test_data": {"$ne": True},
        }),
        db.units.count_documents({"building_id": building_id}),
        _count_pending_registrations(),
    ]

    results = await asyncio.gather(*tasks)
    auto_resolved_today = results[0]
    total_week = results[1]
    auto_resolved_week = results[2]
    pending_total = results[3]
    open_total = results[4]
    sla_breaches = results[5]
    total_lots = results[6]
    pending_registrations = results[7]

    deflection_rate = (auto_resolved_week / total_week * 100) if total_week > 0 else 0

    mongo_response = {
        "auto_resolved_today": auto_resolved_today,
        "deflection_rate_pct": round(deflection_rate, 1),
        "total_received_week": total_week,
        "pending_total": pending_total,
        "open_total": open_total,
        "sla_breaches": sla_breaches,
        "total_lots": total_lots,
        "pending_registrations": pending_registrations,
        "source": "mongodb",
    }

    # ── Optional Postgres supplement (auto-resolution counts) ─────────────────
    # Only attempted when governance cutover toggle is ON. Merges pg metrics
    # into the MongoDB response — never replaces it wholesale.
    try:
        from services.cutover_config_service import is_cutover_feature_enabled, GOVERNANCE_READ_PG_ENABLED
        if await is_cutover_feature_enabled(building_id, GOVERNANCE_READ_PG_ENABLED):
            from db_postgres.repos.config_repo import resolve_scheme_context
            from db_postgres.session import async_session_context, set_tenant
            from sqlalchemy import text as pg_text

            scheme = await resolve_scheme_context(building_id)
            if scheme:
                sid = str(scheme["scheme_id"])
                tid = str(scheme["tenant_id"])
                async with async_session_context() as session:
                    await set_tenant(session, tid)
                    row = (await session.execute(
                        pg_text("""
                            SELECT
                                COUNT(*) FILTER (WHERE created_at >= :week_start)::int AS total_received_week,
                                COUNT(*) FILTER (
                                    WHERE created_at >= :today_start
                                      AND status IN ('completed', 'closed')
                                )::int AS auto_resolved_today,
                                COUNT(*) FILTER (
                                    WHERE created_at >= :week_start
                                      AND status IN ('completed', 'closed')
                                )::int AS auto_resolved_week,
                                COUNT(*) FILTER (
                                    WHERE due_at IS NOT NULL
                                      AND due_at < now()
                                      AND status NOT IN ('completed', 'closed', 'cancelled')
                                )::int AS sla_breaches
                            FROM ops.cases
                            WHERE scheme_id = :sid
                              AND deleted_at IS NULL
                              AND is_test_data = FALSE
                        """),
                        {"sid": sid, "today_start": today_start_dt, "week_start": week_start_dt},
                    )).mappings().first()

                    if row:
                        pg_total_week = int(row["total_received_week"] or 0)
                        pg_auto_today = int(row["auto_resolved_today"] or 0)
                        pg_auto_week = int(row["auto_resolved_week"] or 0)
                        pg_sla_breaches = int(row["sla_breaches"] or 0)
                        mongo_response["sla_breaches"] = max(int(mongo_response.get("sla_breaches") or 0), pg_sla_breaches)

                        if pg_total_week > 0:
                            mongo_response["auto_resolved_today"] = pg_auto_today
                            mongo_response["deflection_rate_pct"] = round(
                                (pg_auto_week / pg_total_week * 100) if pg_total_week else 0, 1
                            )
                            mongo_response["total_received_week"] = pg_total_week

                        if pg_total_week > 0 or pg_sla_breaches > 0:
                            mongo_response["source"] = "mongodb+postgresql"
    except Exception as exc:
        logger.warning("workflow_requests.triage_stats: Postgres supplement unavailable: %s", exc)

    return mongo_response


@router.put("/{request_id}/status", response_model=WorkflowRequestResponse)
async def update_request_status(
        request_id: str,
        data: WorkflowRequestStatusUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: update_request_status
    Path: backend/routers/workflow_requests.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not _is_manager(current_user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    doc = await db.workflow_requests.find_one({"id": request_id, "is_test_data": {"$ne": True}})
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")

    now = datetime.now(timezone.utc).isoformat()
    update = {"status": data.status, "updated_at": now}
    if data.resolution_notes is not None:
        update["resolution_notes"] = data.resolution_notes
    if data.assigned_to is not None:
        # Get assigned user name
        assigned_user = await db.users.find_one({"id": data.assigned_to})
        if assigned_user:
            update["assigned_to"] = data.assigned_to
            update["assigned_to_name"] = assigned_user.get("full_name")

    if _is_terminal_status(data.status):
        update["closed_at"] = now
        update["sla_breached"] = False
        update["needs_human_review"] = False

    await db.workflow_requests.update_one({"id": request_id}, {"$set": update})
    doc.update(update)

    asyncio.create_task(
        create_audit_log(
            action="status_changed",
            resource_type="workflow_request",
            resource_id=request_id,
            user_id=current_user["id"],
            user_name=current_user.get("full_name", ""),
            details={"new_status": data.status, "note": data.resolution_notes},
            building_id=building_id
        )
    )
    return WorkflowRequestResponse(**doc)
