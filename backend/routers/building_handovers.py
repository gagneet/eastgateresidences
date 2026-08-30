"""
Building Handovers Router

Manages manager-to-manager handover when a strata management contract changes.

Endpoints:
  POST /handovers                              — initiate handover
  GET  /handovers                              — list handovers for building
  GET  /handovers/{handover_id}                — get handover with checklist
  PUT  /handovers/{handover_id}/checklist/{item_index} — mark checklist item done/undone
  POST /handovers/{handover_id}/export         — generate data export summary
  POST /handovers/{handover_id}/complete       — mark handover complete
  POST /handovers/{handover_id}/cancel         — cancel handover
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from models.building_handovers import (
    ChecklistItemUpdate,
    DEFAULT_CHECKLIST,
    HandoverCreate,
    HandoverResponse,
    HandoverStatus,
)
from utils.auth import get_current_user, get_current_building

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/handovers", tags=["Buildings"])


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/building_handovers.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/routers/building_handovers.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _require_manager(user: dict) -> None:
    """Generated function header.

    Function: _require_manager
    Path: backend/routers/building_handovers.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _role = user.get("effective_role") or user.get("role", "guest")
    if _role not in {"super_admin", "strata_manager"}:
        raise HTTPException(403, "Strata manager or chairman access required.")


def _completion_pct(checklist: list) -> float:
    """Generated function header.

    Function: _completion_pct
    Path: backend/routers/building_handovers.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not checklist:
        return 0.0
    done = sum(1 for item in checklist if item.get("completed"))
    return round(done / len(checklist) * 100, 1)


def _build_checklist() -> list:
    """Generated function header.

    Function: _build_checklist
    Path: backend/routers/building_handovers.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return [
        {
            **item,
            "completed": False,
            "completed_by": None,
            "completed_at": None,
            "notes": None,
        }
        for item in DEFAULT_CHECKLIST
    ]


@router.post("", status_code=201)
async def initiate_handover(
        payload: HandoverCreate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Initiate a manager handover for this building."""
    _require_manager(current_user)
    db = _get_db()

    # Only one active handover per building at a time
    existing = await db.building_handovers.find_one({
        "building_id": building_id,
        "status": {"$in": HandoverStatus.OPEN},
    }, {"_id": 0})
    if existing:
        raise HTTPException(409,
                            f"An active handover already exists (id: {existing['id']}). Complete or cancel it first.")

    handover_id = str(uuid.uuid4())
    now = _now()
    checklist = _build_checklist()

    doc = {
        "id": handover_id,
        **payload.model_dump(),
        "building_id": building_id,  # auth overrides any payload value
        "status": HandoverStatus.INITIATED,
        "checklist": checklist,
        "initiated_at": now,
        "initiated_by": current_user["id"],
        "created_at": now,
        "updated_at": now,
    }

    await db.building_handovers.insert_one(doc)
    logger.info("Handover %s initiated for building %s", handover_id, building_id)
    return {
        "handover_id": handover_id,
        "status": HandoverStatus.INITIATED,
        "checklist_items": len(checklist),
        "message": "Handover initiated. Complete all checklist items before marking as done.",
    }


@router.get("", response_model=List[HandoverResponse])
async def list_handovers(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        status: Optional[str] = None,
):
    """List all handovers for this building."""
    _require_manager(current_user)
    db = _get_db()

    query: dict = {"building_id": building_id}
    if status:
        query["status"] = status

    docs = await db.building_handovers.find(query, {"_id": 0}).sort("initiated_at", -1).to_list(50)
    for d in docs:
        d["checklist_completion_pct"] = _completion_pct(d.get("checklist", []))
    return [HandoverResponse(**d) for d in docs]


@router.get("/{handover_id}", response_model=HandoverResponse)
async def get_handover(
        handover_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Get a single handover with full checklist."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.building_handovers.find_one({"id": handover_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Handover not found.")
    doc["checklist_completion_pct"] = _completion_pct(doc.get("checklist", []))
    return HandoverResponse(**doc)


@router.put("/{handover_id}/checklist/{item_index}", status_code=200)
async def update_checklist_item(
        handover_id: str,
        item_index: int = Path(..., ge=0),
        payload: ChecklistItemUpdate = ...,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Mark a checklist item as completed or not completed."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.building_handovers.find_one({"id": handover_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Handover not found.")
    if doc["status"] not in HandoverStatus.OPEN:
        raise HTTPException(400, f"Handover is {doc['status']} — cannot update checklist.")

    checklist = doc.get("checklist", [])
    if item_index >= len(checklist):
        raise HTTPException(400, f"item_index {item_index} out of range (checklist has {len(checklist)} items).")

    now = _now()
    checklist[item_index]["completed"] = payload.completed
    checklist[item_index]["completed_by"] = current_user["id"] if payload.completed else None
    checklist[item_index]["completed_at"] = now if payload.completed else None
    checklist[item_index]["notes"] = payload.notes

    # Auto-advance to in_progress when first item is ticked
    new_status = doc["status"]
    if payload.completed and doc["status"] == HandoverStatus.INITIATED:
        new_status = HandoverStatus.IN_PROGRESS

    await db.building_handovers.update_one(
        {"id": handover_id, "building_id": building_id},
        {"$set": {"checklist": checklist, "status": new_status, "updated_at": now}},
    )
    return {
        "item_index": item_index,
        "completed": payload.completed,
        "completion_pct": _completion_pct(checklist),
    }


@router.post("/{handover_id}/export", status_code=200)
async def generate_export(
        handover_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """
    Generate a data export summary package for the outgoing manager.
    Returns structured JSON covering strata roll, financials, maintenance,
    contracts, and compliance deadlines.
    """
    _require_manager(current_user)
    db = _get_db()

    doc = await db.building_handovers.find_one({"id": handover_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Handover not found.")

    now = _now()

    # Fetch all snapshot data concurrently
    (
        units,
        annual_levy,
        trust_accounts,
        open_maintenance,
        contracts,
        compliance,
        insurance,
    ) = await asyncio.gather(
        db.units.find(
            {"building_id": building_id},
            {"_id": 0, "unit_number": 1, "owner_name": 1, "lot_number": 1},
        ).to_list(500),
        db.annual_levies.find_one(
            {"building_id": building_id},
            {"_id": 0, "admin_fund_budget": 1, "sinking_fund_budget": 1, "year": 1},
        ),
        db.trust_accounts_v2.find(
            {"building_id": building_id},
            {"_id": 0, "account_name": 1, "account_type": 1, "opening_balance_cents": 1},
        ).to_list(20),
        db.maintenance_requests.find(
            {"building_id": building_id, "status": {"$in": ["open", "in_progress", "pending_quote"]}},
            {"_id": 0, "title": 1, "status": 1, "priority": 1, "unit_number": 1, "created_at": 1},
        ).sort("created_at", -1).to_list(100),
        db.manager_contracts.find(
            {"building_id": building_id, "status": {"$in": ["active", "pending"]}},
            {"_id": 0, "contract_type": 1, "contractor_name": 1, "expiry_date": 1},
        ).to_list(50),
        db.compliance_items.find(
            {"building_id": building_id, "status": {"$ne": "completed"}},
            {"_id": 0, "title": 1, "due_date": 1, "category": 1},
        ).sort("due_date", 1).to_list(50),
        db.insurance_policies.find(
            {"building_id": building_id, "status": "active"},
            {"_id": 0, "policy_type": 1, "insurer_name": 1, "expiry_date": 1, "premium_annual": 1},
        ).to_list(20),
    )
    units.sort(key=lambda u: int(u["unit_number"]) if str(u.get("unit_number", "")).isdigit() else float("inf"))

    export = {
        "handover_id": handover_id,
        "building_id": building_id,
        "export_type": "handover_package",
        "generated_at": now,
        "generated_by": current_user.get("full_name", current_user["id"]),
        "strata_roll": {"unit_count": len(units), "units": units},
        "financial_summary": annual_levy or {},
        "trust_accounts": trust_accounts,
        "open_maintenance_requests": {"count": len(open_maintenance), "items": open_maintenance},
        "active_contracts": {"count": len(contracts), "items": contracts},
        "compliance_deadlines": {"count": len(compliance), "items": compliance},
        "insurance_policies": {"count": len(insurance), "items": insurance},
    }

    await db.building_handovers.update_one(
        {"id": handover_id, "building_id": building_id},
        {"$set": {"export_generated_at": now, "updated_at": now}},
    )
    return export


@router.post("/{handover_id}/complete", status_code=200)
async def complete_handover(
        handover_id: str,
        notes: Optional[str] = Query(None),
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Mark a handover as complete. Warns if checklist is not 100% done."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.building_handovers.find_one({"id": handover_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Handover not found.")
    if doc["status"] not in HandoverStatus.OPEN:
        raise HTTPException(400, f"Handover is already {doc['status']}.")

    checklist = doc.get("checklist", [])
    pct = _completion_pct(checklist)
    now = _now()

    await db.building_handovers.update_one(
        {"id": handover_id, "building_id": building_id},
        {"$set": {
            "status": HandoverStatus.COMPLETED,
            "completed_at": now,
            "completed_by": current_user["id"],
            "notes": notes or doc.get("notes"),
            "updated_at": now,
        }},
    )
    logger.info("Handover %s completed for building %s (%.0f%% checklist done)", handover_id, building_id, pct)
    return {
        "message": "Handover marked as complete.",
        "handover_id": handover_id,
        "checklist_completion_pct": pct,
        "warning": None if pct == 100.0 else f"Checklist was only {pct}% complete at time of sign-off.",
    }


@router.post("/{handover_id}/cancel", status_code=200)
async def cancel_handover(
        handover_id: str,
        reason: Optional[str] = Query(None),
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Cancel an in-progress handover."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.building_handovers.find_one({"id": handover_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Handover not found.")
    if doc["status"] not in HandoverStatus.OPEN:
        raise HTTPException(400, f"Handover is already {doc['status']}.")

    now = _now()
    await db.building_handovers.update_one(
        {"id": handover_id, "building_id": building_id},
        {"$set": {
            "status": HandoverStatus.CANCELLED,
            "cancellation_reason": reason,
            "cancelled_at": now,
            "cancelled_by": current_user["id"],
            "updated_at": now,
        }},
    )
    return {"message": "Handover cancelled.", "handover_id": handover_id}
