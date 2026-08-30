# @featuretrace:by-law-breach-register — By-law breach / dispute register API (GAP-OPS-005).
# Layer: router
# Data flow: ByLawBreachPage.tsx -> /by-law-breach/reports (+ /status, /notice, /export,
#            /search-help) -> by_law_breach_reports (building-scoped); the same collection
#            is counted by community_dashboard._build_health_data for Building Pulse's
#            dispute axis.
# Related: backend/models/by_law_breach.py
#          backend/routers/community_dashboard.py
#          backend/services/health_score_service.py
#          backend/utils/audit_search.py  (BREACH_FIELD_MAP / BREACH_SEARCH_HELP)
#          frontend/src/pages/dashboard/ByLawBreachPage.tsx
#
# LESSON (2026-08-27): this register shipped complete and routed, but with no page, no nav
# entry and no feature toggle. Nothing could reach it, so it stayed empty, so the health
# score reported the dispute axis as unavailable -- and that was written up as "there is no
# disputes register". A routed endpoint with no way in is indistinguishable from a missing
# feature. Check for a route/toggle/nav entry before concluding a capability is absent.
"""
By-law Breach and Dispute Router

Endpoints:
  POST /by-law-breach/reports                          — submit breach report
  GET  /by-law-breach/reports                          — list reports
  GET  /by-law-breach/reports/{report_id}              — get single report
  POST /by-law-breach/reports/{report_id}/acknowledge  — manager acknowledges
  POST /by-law-breach/reports/{report_id}/notice       — issue formal/courtesy notice
  POST /by-law-breach/reports/{report_id}/status       — advance state
  GET  /by-law-breach/reports/{report_id}/export       — tribunal preparation export
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from models.by_law_breach import (
    BreachNotice,
    BreachSeverity,
    BreachStatus,
    BreachStatusUpdate,
    ByLawBreachCreate,
    ByLawBreachResponse,
)
from utils.audit_search import (
    BREACH_BOOLEAN_FIELDS,
    BREACH_FIELD_MAP,
    BREACH_FREE_TEXT_FIELDS,
    BREACH_NUMERIC_FIELDS,
    BREACH_SEARCH_HELP,
    parse_audit_query,
)
from utils.auth import get_current_user, get_current_building

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/by-law-breach", tags=["Community"])


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/by_law_breach.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/routers/by_law_breach.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _require_manager(user: dict) -> None:
    """Generated function header.

    Function: _require_manager
    Path: backend/routers/by_law_breach.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _role = user.get("effective_role") or user.get("role", "guest")
    if _role not in {"super_admin", "strata_manager", "ec_member"}:
        raise HTTPException(403, "Manager access required.")


def _require_approved(user: dict) -> None:
    """Generated function header.

    Function: _require_approved
    Path: backend/routers/by_law_breach.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not user.get("is_approved"):
        raise HTTPException(403, "Approved user required.")


@router.post("/reports", status_code=201)
async def submit_breach_report(
        payload: ByLawBreachCreate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Any approved resident or manager submits a by-law breach report."""
    _require_approved(current_user)

    if payload.severity not in {BreachSeverity.MINOR, BreachSeverity.MODERATE, BreachSeverity.SERIOUS}:
        raise HTTPException(400, "severity must be minor, moderate, or serious.")

    db = _get_db()
    report_id = str(uuid.uuid4())
    now = _now()

    doc = {
        "id": report_id,
        "reporter_id": current_user["id"],
        "reporter_unit": current_user.get("unit_number"),
        **payload.model_dump(),
        "building_id": building_id,  # auth overrides any payload value
        "status": BreachStatus.REPORTED,
        "notices": [],
        "created_at": now,
        "updated_at": now,
    }

    await db.by_law_breach_reports.insert_one(doc)
    logger.info("Breach report %s submitted for unit %s in building %s", report_id, payload.alleged_unit, building_id)
    return {"report_id": report_id, "status": BreachStatus.REPORTED,
            "message": "Breach report submitted. A manager will review within 5 business days."}


@router.get("/reports", response_model=List[ByLawBreachResponse])
async def list_breach_reports(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        status: Optional[str] = None,
        alleged_unit: Optional[str] = None,
        search: Optional[str] = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(30, ge=1, le=100),
):
    """List breach reports. Managers see all; residents see only their own submissions.

    `search` accepts the shared record-search grammar (`status:escalated`,
    `-status:resolved`, `severity:major unit:UA042`, `description~=parking`). It reuses
    `utils.audit_search`'s parser with this register's vocabulary rather than a second
    parser, so the syntax the `?` panel documents cannot drift from what is accepted.

    An unrecognised field is reported in `X-Search-Unknown-Fields`, never silently
    dropped: a typo that matches every row reads as "no filter applied", which is the
    worst possible outcome for a register of legal matters.
    """
    _require_approved(current_user)
    db = _get_db()
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    is_manager = _role in {"super_admin", "strata_manager", "ec_member"}

    query: dict = {"building_id": building_id}
    if not is_manager:
        query["reporter_id"] = current_user["id"]
    if status:
        query["status"] = status
    if alleged_unit and is_manager:
        query["alleged_unit"] = alleged_unit

    unknown_fields: list[str] = []
    if search:
        search_filter, unknown_fields = parse_audit_query(
            search,
            field_map=BREACH_FIELD_MAP,
            free_text_fields=BREACH_FREE_TEXT_FIELDS,
            numeric_fields=BREACH_NUMERIC_FIELDS,
            boolean_fields=BREACH_BOOLEAN_FIELDS,
        )
        if search_filter:
            # Merge under $and so a search clause can never overwrite the building or
            # reporter scoping above — those are security boundaries, not preferences.
            query = {"$and": [query, search_filter]}

    skip = (page - 1) * page_size
    docs = await db.by_law_breach_reports.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(
        page_size).to_list(page_size)
    reports = [ByLawBreachResponse(**d) for d in docs]
    if unknown_fields:
        return JSONResponse(
            content=[r.model_dump() for r in reports],
            headers={"X-Search-Unknown-Fields": ",".join(unknown_fields)},
        )
    return reports


@router.get("/search-help")
async def get_breach_search_help(current_user: dict = Depends(get_current_user)):
    """Search syntax help for the breach register, served from the parser itself.

    Returns `{summary, examples[], operators[], fields[]}`. `fields` is the exact set of
    aliases `GET /by-law-breach/reports?search=` accepts, and `examples` are queries that
    are asserted in CI to parse without unknown fields.

    `docs/architecture/ui_table_and_search_conventions.md` requires the help to come from
    the parser: help text maintained separately documents syntax that may not work. Build
    a client-side help panel from this response rather than hard-coding a copy of it.
    """
    _require_approved(current_user)
    return BREACH_SEARCH_HELP


@router.get("/reports/{report_id}", response_model=ByLawBreachResponse)
async def get_breach_report(
        report_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Get a single breach report."""
    _require_approved(current_user)
    db = _get_db()
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    is_manager = _role in {"super_admin", "strata_manager", "ec_member"}

    query = {"id": report_id, "building_id": building_id}
    if not is_manager:
        query["reporter_id"] = current_user["id"]

    doc = await db.by_law_breach_reports.find_one(query, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Breach report not found.")
    return ByLawBreachResponse(**doc)


@router.post("/reports/{report_id}/acknowledge", status_code=200)
async def acknowledge_breach_report(
        report_id: str,
        notes: Optional[str] = Query(None),
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Manager acknowledges receipt of a breach report."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.by_law_breach_reports.find_one({"id": report_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Breach report not found.")
    if doc["status"] != BreachStatus.REPORTED:
        raise HTTPException(400, f"Report is already {doc['status']}.")

    now = _now()
    await db.by_law_breach_reports.update_one(
        {"id": report_id, "building_id": building_id},
        {"$set": {
            "status": BreachStatus.ACKNOWLEDGED,
            "acknowledged_at": now,
            "acknowledged_by": current_user["id"],
            "notes": notes or doc.get("notes"),
            "updated_at": now,
        }},
    )
    return {"message": "Breach report acknowledged.", "report_id": report_id}


@router.post("/reports/{report_id}/notice", status_code=201)
async def issue_breach_notice(
        report_id: str,
        notice: BreachNotice,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Issue a courtesy or formal notice to the allegedly breaching unit."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.by_law_breach_reports.find_one({"id": report_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Breach report not found.")
    if doc["status"] in BreachStatus.TERMINAL:
        raise HTTPException(400, f"Cannot issue notice — report is {doc['status']}.")

    notice_type = notice.notice_type
    if notice_type not in {"courtesy", "formal"}:
        raise HTTPException(400, "notice_type must be 'courtesy' or 'formal'.")

    notice_doc = {**notice.model_dump(), "issued_by": current_user["id"], "issued_at": _now()}
    new_status = (
        BreachStatus.COURTESY_NOTICE_SENT if notice_type == "courtesy"
        else BreachStatus.FORMAL_NOTICE_SENT
    )

    now = _now()
    await db.by_law_breach_reports.update_one(
        {"id": report_id, "building_id": building_id},
        {
            "$push": {"notices": notice_doc},
            "$set": {"status": new_status, "updated_at": now},
        },
    )
    return {"message": f"{notice_type.capitalize()} notice issued.", "new_status": new_status}


@router.post("/reports/{report_id}/status", status_code=200)
async def update_breach_status(
        report_id: str,
        payload: BreachStatusUpdate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Advance the breach report to a new state (manager only)."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.by_law_breach_reports.find_one({"id": report_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Breach report not found.")

    current_status = doc["status"]
    allowed = BreachStatus.TRANSITIONS.get(current_status, [])
    if payload.new_status not in allowed:
        raise HTTPException(400, f"Cannot transition from '{current_status}' to '{payload.new_status}'. "
                                 f"Allowed: {allowed}")

    now = _now()
    upd: dict = {"status": payload.new_status, "updated_at": now}
    if payload.notes:
        upd["notes"] = payload.notes
    if payload.new_status in {BreachStatus.RESOLVED, BreachStatus.TRIBUNAL_REFERRED}:
        upd["resolved_at"] = now
        upd["resolution_outcome"] = payload.resolution_outcome
    if payload.new_status == BreachStatus.ESCALATED:
        upd["escalated_at"] = now
        upd["escalation_target"] = payload.escalation_target or "ACAT"

    await db.by_law_breach_reports.update_one(
        {"id": report_id, "building_id": building_id}, {"$set": upd}
    )
    return {"message": f"Status updated to '{payload.new_status}'.", "report_id": report_id}


@router.get("/reports/{report_id}/export")
async def export_breach_report(
        report_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """
    Tribunal preparation export — returns structured bundle of all breach evidence,
    notices, timeline, and relevant by-law text for ACAT/NCAT filing.
    """
    _require_manager(current_user)
    db = _get_db()

    doc = await db.by_law_breach_reports.find_one({"id": report_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Breach report not found.")

    # Fetch relevant by-law text
    by_law_doc = await db.by_laws.find_one({"building_id": building_id}, {"_id": 0})
    by_law_text = None
    if by_law_doc and doc.get("by_law_section"):
        by_law_text = f"Relevant section: {doc['by_law_section']}"

    # Fetch attached evidence document metadata (single batched query, not N+1)
    evidence_docs = []
    if doc.get("evidence_file_ids"):
        evidence_docs = await db.documents.find(
            {"id": {"$in": doc["evidence_file_ids"]}, "building_id": building_id},
            {"_id": 0, "title": 1, "category": 1, "created_at": 1},
        ).to_list(50)

    return {
        "report_id": report_id,
        "building_id": building_id,
        "export_type": "tribunal_preparation",
        "jurisdiction_note": "ACT: ACAT jurisdiction — ACT Civil and Administrative Tribunal. "
                             "NSW: NCAT jurisdiction — NSW Civil and Administrative Tribunal.",
        "breach_report": doc,
        "by_law_text": by_law_text,
        "evidence_documents": evidence_docs,
        "notice_count": len(doc.get("notices", [])),
        "notices": doc.get("notices", []),
        "timeline": [
            {"event": "Reported", "at": doc.get("created_at")},
            *[{"event": f"{n['notice_type'].capitalize()} notice issued", "at": n.get("issued_at")}
              for n in doc.get("notices", [])],
            *([{"event": "Escalated", "at": doc.get("escalated_at")}] if doc.get("escalated_at") else []),
            *([{"event": f"Resolved: {doc.get('resolution_outcome')}", "at": doc.get("resolved_at")}]
              if doc.get("resolved_at") else []),
        ],
        "generated_at": _now(),
        "generated_by": current_user.get("full_name", current_user["id"]),
    }
