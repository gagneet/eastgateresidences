"""
Document Requests Router

Endpoints:
  POST /document-requests                          — owner submits request
  GET  /document-requests                          — list (manager: all; owner: own)
  GET  /document-requests/overdue                  — requests past statutory deadline
  GET  /document-requests/{request_id}             — get single request
  POST /document-requests/{request_id}/fulfil      — manager fulfils
  POST /document-requests/{request_id}/deny        — manager denies
  POST /document-requests/{request_id}/cancel      — requester cancels
  POST /document-requests/auto-fulfil              — run auto-fulfilment pass
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta, date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from models.document_requests import (
    DocumentRequestCreate,
    DocumentRequestDeny,
    DocumentRequestFulfil,
    DocumentRequestResponse,
    DocumentRequestStatus,
    DocumentRequestType,
)
from utils.auth import get_current_user, get_current_building

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/document-requests", tags=["Documents"])

_DEADLINE_DAYS = 14  # ACT UTMA s.116 / NSW SSMA s.182


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/document_requests.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/routers/document_requests.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _due_by() -> str:
    """Generated function header.

    Function: _due_by
    Path: backend/routers/document_requests.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return (date.today() + timedelta(days=_DEADLINE_DAYS)).isoformat()


def _require_manager(user: dict) -> None:
    """Generated function header.

    Function: _require_manager
    Path: backend/routers/document_requests.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _role = user.get("effective_role") or user.get("role", "guest")
    if _role not in {"super_admin", "strata_manager", "ec_member", "admin_staff"}:
        raise HTTPException(403, "Manager access required.")


def _require_approved(user: dict) -> None:
    """Generated function header.

    Function: _require_approved
    Path: backend/routers/document_requests.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not user.get("is_approved"):
        raise HTTPException(403, "Approved user access required.")


def _enrich(doc: dict) -> dict:
    """Add computed is_overdue field."""
    if doc.get("status") in DocumentRequestStatus.OPEN:
        doc["is_overdue"] = doc.get("due_by", "9999") < date.today().isoformat()
    else:
        doc["is_overdue"] = False
    doc.pop("_id", None)
    return doc


@router.post("", status_code=201)
async def submit_document_request(
        payload: DocumentRequestCreate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Owner or tenant submits a document request."""
    _require_approved(current_user)

    if payload.document_type not in DocumentRequestType.ALL:
        raise HTTPException(400, f"Unknown document_type. Choose from: {DocumentRequestType.ALL}")

    db = _get_db()
    request_id = str(uuid.uuid4())
    now = _now()

    doc = {
        "id": request_id,
        "building_id": building_id,
        "requester_id": current_user["id"],
        "requester_unit": current_user.get("unit_number"),
        **payload.model_dump(),
        "status": DocumentRequestStatus.SUBMITTED,
        "requested_at": now,
        "due_by": _due_by(),
        "created_at": now,
        "updated_at": now,
    }

    await db.document_requests.insert_one(doc)

    # Attempt auto-fulfilment for eligible types
    if payload.document_type in DocumentRequestType.AUTO_FULFIL_ELIGIBLE:
        auto_doc = await db.documents.find_one({
            "building_id": building_id,
            "category": payload.document_type,
            "is_private": {"$ne": True},
        }, {"_id": 0})
        if auto_doc:
            await db.document_requests.update_one(
                {"id": request_id, "building_id": building_id},
                {"$set": {
                    "status": DocumentRequestStatus.AUTO_FULFILLED,
                    "document_id": auto_doc.get("id"),
                    "fulfilled_at": now,
                    "fulfilled_by": "system",
                    "updated_at": now,
                }},
            )
            logger.info("Auto-fulfilled document request %s with doc %s", request_id, auto_doc.get("id"))
            return {"request_id": request_id, "status": DocumentRequestStatus.AUTO_FULFILLED,
                    "document_id": auto_doc.get("id"), "message": "Document found and provided automatically."}

    # Move to manager review
    await db.document_requests.update_one(
        {"id": request_id, "building_id": building_id},
        {"$set": {"status": DocumentRequestStatus.MANAGER_REVIEW, "updated_at": now}},
    )
    return {"request_id": request_id, "status": DocumentRequestStatus.MANAGER_REVIEW,
            "due_by": doc["due_by"], "message": "Request submitted. A manager will respond within 14 days."}


@router.get("", response_model=List[DocumentRequestResponse])
async def list_document_requests(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        status: Optional[str] = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(30, ge=1, le=100),
):
    """List document requests. Managers see all; owners see only their own."""
    _require_approved(current_user)
    db = _get_db()
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    is_manager = _role in {"super_admin", "strata_manager", "ec_member", "admin_staff"}

    query: dict = {"building_id": building_id}
    if not is_manager:
        query["requester_id"] = current_user["id"]
    if status:
        query["status"] = status

    skip = (page - 1) * page_size
    docs = await db.document_requests.find(query, {"_id": 0}).sort("requested_at", -1).skip(skip).limit(
        page_size).to_list(page_size)
    return [DocumentRequestResponse(**_enrich(d)) for d in docs]


@router.get("/overdue", response_model=List[DocumentRequestResponse])
async def list_overdue_requests(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """List open requests past their 14-day statutory deadline (manager only)."""
    _require_manager(current_user)
    db = _get_db()

    today = date.today().isoformat()
    docs = await db.document_requests.find({
        "building_id": building_id,
        "status": {"$in": DocumentRequestStatus.OPEN},
        "due_by": {"$lt": today},
    }, {"_id": 0}).sort("due_by", 1).to_list(200)

    return [DocumentRequestResponse(**_enrich(d)) for d in docs]


@router.get("/{request_id}", response_model=DocumentRequestResponse)
async def get_document_request(
        request_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Get a single document request."""
    _require_approved(current_user)
    db = _get_db()
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    is_manager = _role in {"super_admin", "strata_manager", "ec_member", "admin_staff"}

    query = {"id": request_id, "building_id": building_id}
    if not is_manager:
        query["requester_id"] = current_user["id"]

    doc = await db.document_requests.find_one(query, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Document request not found.")
    return DocumentRequestResponse(**_enrich(doc))


@router.post("/{request_id}/fulfil", status_code=200)
async def fulfil_document_request(
        request_id: str,
        payload: DocumentRequestFulfil,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Manager fulfils a document request by attaching a document."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.document_requests.find_one({"id": request_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Document request not found.")
    if doc["status"] not in DocumentRequestStatus.OPEN:
        raise HTTPException(400, f"Request is already {doc['status']}.")

    now = _now()
    await db.document_requests.update_one(
        {"id": request_id, "building_id": building_id},
        {"$set": {
            "status": DocumentRequestStatus.FULFILLED,
            "fulfilled_at": now,
            "fulfilled_by": current_user["id"],
            "document_id": payload.document_id,
            "fulfil_notes": payload.fulfil_notes,
            "updated_at": now,
        }},
    )
    return {"message": "Request fulfilled.", "request_id": request_id}


@router.post("/{request_id}/deny", status_code=200)
async def deny_document_request(
        request_id: str,
        payload: DocumentRequestDeny,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Manager denies a document request with a reason."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.document_requests.find_one({"id": request_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Document request not found.")
    if doc["status"] not in DocumentRequestStatus.OPEN:
        raise HTTPException(400, f"Request is already {doc['status']}.")

    now = _now()
    await db.document_requests.update_one(
        {"id": request_id, "building_id": building_id},
        {"$set": {
            "status": DocumentRequestStatus.DENIED,
            "denial_reason": payload.denial_reason,
            "fulfilled_by": current_user["id"],
            "updated_at": now,
        }},
    )
    return {"message": "Request denied.", "request_id": request_id}


@router.post("/{request_id}/cancel", status_code=200)
async def cancel_document_request(
        request_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Requester cancels their own document request."""
    _require_approved(current_user)
    db = _get_db()

    query = {"id": request_id, "building_id": building_id}
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in {"super_admin", "strata_manager", "ec_member"}:
        query["requester_id"] = current_user["id"]

    doc = await db.document_requests.find_one(query, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Document request not found.")
    if doc["status"] not in DocumentRequestStatus.OPEN:
        raise HTTPException(400, f"Request is already {doc['status']}.")

    await db.document_requests.update_one(
        {"id": request_id, "building_id": building_id},
        {"$set": {"status": DocumentRequestStatus.CANCELLED, "updated_at": _now()}},
    )
    return {"message": "Request cancelled.", "request_id": request_id}


@router.post("/auto-fulfil", status_code=200)
async def run_auto_fulfilment(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """
    Run auto-fulfilment pass: match all SUBMITTED/MANAGER_REVIEW requests of eligible
    types against public documents in the documents collection.
    """
    _require_manager(current_user)
    db = _get_db()

    open_requests = await db.document_requests.find({
        "building_id": building_id,
        "status": {"$in": DocumentRequestStatus.OPEN},
        "document_type": {"$in": list(DocumentRequestType.AUTO_FULFIL_ELIGIBLE)},
    }, {"_id": 0}).to_list(500)

    fulfilled, skipped = 0, 0
    now = _now()

    for req in open_requests:
        auto_doc = await db.documents.find_one({
            "building_id": building_id,
            "category": req["document_type"],
            "is_private": {"$ne": True},
        }, {"_id": 0})

        if auto_doc:
            await db.document_requests.update_one(
                {"id": req["id"], "building_id": building_id},
                {"$set": {
                    "status": DocumentRequestStatus.AUTO_FULFILLED,
                    "document_id": auto_doc.get("id"),
                    "fulfilled_at": now,
                    "fulfilled_by": "system",
                    "updated_at": now,
                }},
            )
            fulfilled += 1
        else:
            skipped += 1

    return {"fulfilled": fulfilled, "skipped": skipped, "run_at": now}
