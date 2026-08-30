"""
Outbound Message Queue Console — GAP-COMMS-003 Phase 2
@featuretrace:outbound-message-queue — admin review console for held outgoing messages
Layer: router
Data flow: frontend/src/pages/dashboard/admin/OutboundQueuePage.jsx → /outbound-messages/*
           → outbound_messages + db.settings (building-scoped).
Related: backend/services/outbound_queue_service.py
          backend/cron/cron_outbound_queue.py
          tasks/GAP-COMMS-003-outbound-message-queue-and-activation.md

The operator-facing half of the queue. Nothing here transmits: releasing a message only
clears its hold, and the worker still re-checks every gate before it sends.

Endpoints:
  GET    /outbound-messages                 — list, filterable, building-scoped
  GET    /outbound-messages/summary         — counts per status, for the console header
  GET    /outbound-messages/{id}            — one message including its body
  POST   /outbound-messages/{id}/cancel     — drop a single message
  POST   /outbound-messages/{id}/release    — waive the remaining hold window
  POST   /outbound-messages/bulk-cancel     — drop a selected set
  GET    /outbound-messages/settings/queue  — per-building queue controls
  PUT    /outbound-messages/settings/queue  — enable/disable the queue or a category

RBAC: super_admin, strata_admin, strata_manager. Deliberately NOT ec_member — deciding
what mail leaves the building is an operational duty, not a committee governance one,
and the same boundary is already drawn for work-order approvals in CLAUDE.md.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from database import db
from models.outbound_message import MessageStatus
from models.user import UserRole
from services.outbound_queue_service import (
    COLLECTION,
    SETTINGS_KEY,
    SETTINGS_TYPE_FIELD,
    cancel as cancel_message,
    get_queue_settings,
    release_now,
    sendable_reason,
)
from utils.audit_search import parse_audit_query
from utils.auth import effective_role, get_current_building, get_current_user
from utils.helpers import create_audit_log

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/outbound-messages", tags=["Outbound Message Queue"])

# Operational roles. effective_role() is mandatory here: an elevated user carries their
# original role ("owner") on the record, so a raw current_user["role"] check would 403
# exactly the people this console is for (CLAUDE.md).
_QUEUE_ADMIN_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
}

# The body can be large and the list is polled; it is returned only by the detail route.
_LIST_PROJECTION = {
    "_id": 0, "html_body": 0, "text_body": 0,
}

# Search vocabulary for this collection. The GRAMMAR itself is shared with the audit
# log (utils/audit_search.parse_audit_query) so `field:value`, `field!=value`,
# `-field:value` and `field~=value` behave identically everywhere. Only the field names
# differ. SEARCH_HELP is served from here so the UI help panel cannot document syntax
# the parser does not accept — see docs/architecture/ui_table_and_search_conventions.md.
_SEARCH_FIELD_MAP = {
    "to": "to_email", "recipient": "to_email", "email": "to_email",
    "subject": "subject",
    "context": "context", "reason": "context",
    "status": "status",
    "category": "category",
    "channel": "channel",
    "error": "last_error",
    "attempts": "attempts",
}
_SEARCH_FREE_TEXT = ("to_email", "subject", "context")
_SEARCH_NUMERIC = {"attempts"}

SEARCH_HELP = {
    "syntax": [
        {"example": "levy", "means": "matches recipient, subject or context"},
        {"example": "status:held", "means": "only held messages"},
        {"example": "-status:sent", "means": "everything except sent"},
        {"example": "status!=sent", "means": "same, written the other way"},
        {"example": "subject~=arrears", "means": "subject contains 'arrears'"},
        {"example": "category:manual", "means": "only operator-composed messages"},
        {"example": "attempts:>=2", "means": "messages that have already failed twice"},
        {"example": 'to:"a@b.com" status:held', "means": "terms combine with AND"},
    ],
    "fields": sorted(set(_SEARCH_FIELD_MAP)),
}



async def _audit(action: str, current_user: dict, building_id: str,
                 message_id: str, details: Dict[str, Any]) -> None:
    """Write an audit entry with every field create_audit_log actually requires.

    resource_type, resource_id and user_name are REQUIRED positional parameters on
    utils.helpers.create_audit_log. Calling it with only action/user_id/details raises
    TypeError, which the route would surface as a 500 — so every cancel and release
    would have failed at the last step, after the state change had already been applied.
    Centralised here so the four call sites cannot drift apart again.
    """
    await create_audit_log(
        action=action,
        resource_type="outbound_message",
        resource_id=message_id,
        user_id=current_user.get("id", ""),
        user_name=current_user.get("full_name") or current_user.get("email", ""),
        details=details,
        building_id=building_id,
    )


def _require_queue_admin(current_user: dict) -> str:
    role = effective_role(current_user)
    if role not in _QUEUE_ADMIN_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Managing outgoing messages requires a manager or administrator role",
        )
    return role


class CancelRequest(BaseModel):
    reason: str = Field("", max_length=500)


class BulkCancelRequest(BaseModel):
    message_ids: List[str] = Field(..., min_length=1, max_length=500)
    reason: str = Field("", max_length=500)


class QueueSettingsUpdate(BaseModel):
    """Every field optional: the console patches one control at a time."""

    enabled: Optional[bool] = None
    hold_seconds: Optional[int] = Field(None, ge=0, le=3600)
    expiry_hours: Optional[int] = Field(None, ge=1, le=720)
    disabled_categories: Optional[List[str]] = None



def merge_search_filter(query: Dict[str, Any], parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a parsed search filter into a base query, re-homing any top-level $or.

    A bare search term makes the parser emit a TOP-LEVEL ``$or`` across the free-text
    fields. ``TenantScopedDatabase._inject_bid()`` refuses that: it cannot safely add
    ``building_id`` to an ``$or`` it does not control, so the query raises rather than
    leaking across buildings or silently narrowing. Nesting the same clause inside
    ``$and`` is accepted and means exactly the same thing.

    Extracted from the route so it can be tested directly — the failure only appears
    once someone types a bare word, which is easy to miss by exercising the endpoint
    with field filters alone.
    """
    merged = dict(query)
    if not parsed:
        return merged
    remaining = dict(parsed)
    or_clause = remaining.pop("$or", None)
    if or_clause:
        merged.setdefault("$and", []).append({"$or": or_clause})
    for key, value in remaining.items():
        if key == "$and":
            merged.setdefault("$and", []).extend(value)
        else:
            merged[key] = value
    return merged


@router.get("")
async def list_outbound_messages(
    status: Optional[str] = Query(None, description="held|sending|sent|cancelled|expired|failed"),
    category: Optional[str] = Query(None, description="automated|manual"),
    channel: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="matches recipient, subject or context"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
) -> Dict[str, Any]:
    """List queued messages for this building, newest first."""
    _require_queue_admin(current_user)

    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    if category:
        query["category"] = category
    if channel:
        query["channel"] = channel
    unknown_fields: List[str] = []
    if search:
        # Shared grammar, this collection's vocabulary. Any $or the parser produces is
        # returned nested inside $and, which matters here: TenantScopedDatabase rejects
        # a TOP-LEVEL $or because it cannot safely inject building_id into it
        # (CLAUDE.md). A mistyped field is reported rather than silently ignored — a
        # typo that quietly matches everything reads as "no filter applied".
        parsed, unknown_fields = parse_audit_query(
            search,
            field_map=_SEARCH_FIELD_MAP,
            free_text_fields=_SEARCH_FREE_TEXT,
            numeric_fields=_SEARCH_NUMERIC,
            boolean_fields=set(),
        )
        query = merge_search_filter(query, parsed)

    rows = await db[COLLECTION].find(query, _LIST_PROJECTION) \
        .sort("created_at", -1).skip(offset).limit(limit).to_list(limit)

    # Explain WHY each held message is held, so the operator sees which gate to act on
    # rather than an inert "pending" they cannot reason about.
    settings = await get_queue_settings(building_id)
    now = datetime.now(timezone.utc)
    for row in rows:
        if row.get("status") == MessageStatus.HELD.value:
            ok, why = sendable_reason(row, settings, now=now)
            row["will_send_next_tick"] = ok
            row["hold_reason"] = why
        else:
            row["will_send_next_tick"] = False
            row["hold_reason"] = ""

    total = await db[COLLECTION].count_documents(query)
    return {
        "messages": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
        "queue_settings": settings,
        # Surfaced so the UI can warn on a typo instead of showing an unfiltered list.
        "unknown_fields": unknown_fields,
        "search_help": SEARCH_HELP,
    }


@router.get("/summary")
async def outbound_summary(
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
) -> Dict[str, Any]:
    """Counts per status for the console header.

    Statuses with no rows are returned as 0 deliberately: here a zero IS a measurement
    (the queue was queried and held nothing), which is different from the queue being
    unavailable. `queue_configured` distinguishes the two.
    """
    _require_queue_admin(current_user)

    counts = {s.value: 0 for s in MessageStatus}
    pipeline = [{"$group": {"_id": "$status", "n": {"$sum": 1}}}]
    async for row in db[COLLECTION].aggregate(pipeline):
        if row["_id"] in counts:
            counts[row["_id"]] = row["n"]

    settings = await get_queue_settings(building_id)
    configured = await db.settings.find_one({SETTINGS_TYPE_FIELD: SETTINGS_KEY}) is not None
    return {
        "building_id": building_id,
        "counts": counts,
        "queue_settings": settings,
        "queue_configured": configured,
    }


@router.get("/{message_id}")
async def get_outbound_message(
    message_id: str,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
) -> Dict[str, Any]:
    """One message, including the body an operator needs in order to judge it."""
    _require_queue_admin(current_user)
    row = await db[COLLECTION].find_one({"id": message_id}, {"_id": 0})
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")
    settings = await get_queue_settings(building_id)
    ok, why = sendable_reason(row, settings)
    row["will_send_next_tick"] = ok
    row["hold_reason"] = why
    return row


@router.post("/{message_id}/cancel")
async def cancel_outbound_message(
    message_id: str,
    payload: CancelRequest,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
) -> Dict[str, Any]:
    """Drop a message so it is never sent."""
    _require_queue_admin(current_user)

    ok = await cancel_message(message_id, cancelled_by=current_user.get("id", ""),
                              building_id=building_id)
    if not ok:
        # Either it does not exist, or it already left the HELD state. Both mean the
        # operator's intent cannot be honoured, and saying so beats a misleading 200.
        current = await db[COLLECTION].find_one({"id": message_id}, {"_id": 0, "status": 1})
        if not current:
            raise HTTPException(status_code=404, detail="Message not found")
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_cancellable",
                "message": f"This message is already {current.get('status')} and can no longer be stopped.",
                "status_now": current.get("status"),
            },
        )

    if payload.reason:
        await db[COLLECTION].update_one({"id": message_id},
                                        {"$set": {"cancel_reason": payload.reason}})
    await _audit("outbound_message_cancelled", current_user, building_id, message_id,
                 {"reason": payload.reason})
    return {"success": True, "message_id": message_id, "status": MessageStatus.CANCELLED.value}


@router.post("/{message_id}/release")
async def release_outbound_message(
    message_id: str,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
) -> Dict[str, Any]:
    """Waive the remaining hold window.

    This does NOT bypass the disabled-queue or expiry gates — the worker re-checks both
    before sending. Only the undo delay is the operator's to waive; if release could
    override a disabled queue it would become a route around the control it belongs to.
    """
    _require_queue_admin(current_user)

    ok = await release_now(message_id, released_by=current_user.get("id", ""))
    if not ok:
        raise HTTPException(
            status_code=409,
            detail={"code": "not_releasable",
                    "message": "Only a held message can be released."},
        )
    await _audit("outbound_message_released", current_user, building_id, message_id, {})
    row = await db[COLLECTION].find_one({"id": message_id}, {"_id": 0})
    settings = await get_queue_settings(building_id)
    will_send, why = sendable_reason(row or {}, settings)
    # Reported honestly: a released message still sitting behind a disabled queue has
    # NOT been sent, and the console must not imply that it has.
    return {"success": True, "message_id": message_id,
            "will_send_next_tick": will_send, "hold_reason": why}


@router.post("/bulk-cancel")
async def bulk_cancel(
    payload: BulkCancelRequest,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
) -> Dict[str, Any]:
    """Drop a selected set. Reports per-message outcomes rather than a single verdict."""
    _require_queue_admin(current_user)

    cancelled: List[str] = []
    skipped: List[Dict[str, str]] = []
    for mid in payload.message_ids:
        if await cancel_message(mid, cancelled_by=current_user.get("id", ""),
                                building_id=building_id):
            cancelled.append(mid)
        else:
            row = await db[COLLECTION].find_one({"id": mid}, {"_id": 0, "status": 1})
            skipped.append({"message_id": mid,
                            "reason": f"already {row.get('status')}" if row else "not found"})

    if payload.reason and cancelled:
        await db[COLLECTION].update_many({"id": {"$in": cancelled}},
                                         {"$set": {"cancel_reason": payload.reason}})
    await _audit("outbound_messages_bulk_cancelled", current_user, building_id,
                 ",".join(cancelled)[:200] or "none",
                 {"cancelled": len(cancelled), "skipped": len(skipped),
                  "reason": payload.reason})
    return {"success": True, "cancelled": cancelled, "skipped": skipped,
            "cancelled_count": len(cancelled), "skipped_count": len(skipped)}


@router.get("/settings/queue")
async def get_queue_controls(
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
) -> Dict[str, Any]:
    _require_queue_admin(current_user)
    return {"building_id": building_id, "settings": await get_queue_settings(building_id)}


@router.put("/settings/queue")
async def update_queue_controls(
    payload: QueueSettingsUpdate,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
) -> Dict[str, Any]:
    """Enable or disable the queue, or mute a category, for this building.

    Enabling is what releases mail already held: the worker re-reads these controls on
    every tick, so anything still inside its 48-hour window goes out on the next pass
    with no replay step.

    This does NOT override EMAIL_SEND_DISABLED_ALL, which remains the outermost stop and
    is deliberately not settable from the UI — a platform-wide halt should require
    deliberate access to the deployment, not a checkbox.
    """
    _require_queue_admin(current_user)

    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No settings supplied")

    updates[SETTINGS_TYPE_FIELD] = SETTINGS_KEY
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    updates["updated_by"] = current_user.get("id")

    await db.settings.update_one(
        {SETTINGS_TYPE_FIELD: SETTINGS_KEY}, {"$set": updates}, upsert=True,
    )
    await _audit("outbound_queue_settings_updated", current_user, building_id,
                 SETTINGS_KEY, dict(updates))

    settings = await get_queue_settings(building_id)
    held = await db[COLLECTION].count_documents({"status": MessageStatus.HELD.value})
    return {"success": True, "settings": settings, "held_messages": held}
