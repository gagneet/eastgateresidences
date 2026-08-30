"""
Compliance Registers — Fire, Lift, Asbestos, Pool Safety

Australian Standards compliance:
- Fire: AS 1851 (maintenance of fire protection systems)
- Lift: AS/NZS 1735 (lifts, escalators, moving walks)
- Asbestos: Safe Work Australia — Asbestos Management Plan
- Pool: State-specific pool safety legislation

Collections:
  compliance_registers   : register headers (one per building × type)
  compliance_register_items : individual items within a register
  compliance_events      : due-date events + alerts

Endpoints:
  POST   /compliance/registers                — create register
  GET    /compliance/registers                — list registers (building-scoped)
  GET    /compliance/registers/{reg_id}       — get register
  POST   /compliance/registers/{reg_id}/items — add item
  PUT    /compliance/registers/{reg_id}/items/{item_id} — update item
  GET    /compliance/events                   — upcoming compliance events
  GET    /compliance/dashboard                — compliance summary
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import asyncio
import logging
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Any, Dict, Optional

from models.user import UserRole
from utils.auth import get_current_user, get_current_building
from utils.helpers import create_audit_log, create_user_notification
from utils.route_guards import OPERATIONAL_MANAGEMENT, assert_roles, require_manager_surface

logger = logging.getLogger(__name__)
# Function scoping (2026-08-28). Router-level, so every route in this file is
# covered including the reads — Privacy Act APP 6 limits USE to the purpose of
# collection, and looking is a use. This narrows nobody until an agency sets
# core.management_entities.function_scoping_enabled; see
# docs/architecture/strata_management_staff_access_model.md.
router = APIRouter(
    prefix="/compliance", tags=["Compliance Registers"],
    dependencies=[Depends(require_manager_surface("compliance"))],
)


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/compliance_registers.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/compliance_registers.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_manager(user: dict) -> dict:
    """Who may maintain a statutory compliance register.

    Built on utils.route_guards so the role set is checked against UserRole and the
    role is resolved through effective_role(), rather than restating both here.

    Until 2026-08-28 this set also carried "admin" and "maintenance". Neither is a
    UserRole, so both conditions were dead and the guard was narrower than written.
    They are resolved differently, on purpose:

      "admin"       meant admin_staff, and it is restored. Keeping a compliance
                    register is records administration, and the four sibling
                    registers — pool_safety, essential_services, nsw_compliance,
                    defects_register — have always admitted admin_staff. This file
                    was the outlier.
      "maintenance" is NOT restored. The nearest real role is service_provider, an
                    external contractor, and admitting the contractor to the register
                    that records their own compliance is a different trust boundary.
                    What that string reached for is a maintenance FUNCTION inside the
                    managing agent, which is not a top-level role — see
                    docs/architecture/strata_management_staff_access_model.md.
    """
    return assert_roles(
        user,
        OPERATIONAL_MANAGEMENT | {UserRole.ADMIN_STAFF},
        detail="Strata manager or EC access required.",
    )


# ─── Models ───────────────────────────────────────────────────────────────────

REGISTER_TYPES = {
    "fire": {
        "standard": "AS 1851",
        "description": "Fire Protection System Maintenance Register",
        "legislation": "AS 1851:2012 — Maintenance of fire protection systems and equipment",
    },
    "lift": {
        "standard": "AS/NZS 1735",
        "description": "Lift and Escalator Safety Register",
        "legislation": "AS/NZS 1735 series — Lifts, escalators and moving walks",
    },
    "asbestos": {
        "standard": "SWA Asbestos Code",
        "description": "Asbestos Management Register",
        "legislation": "Safe Work Australia Code of Practice: Management and control of asbestos in the workplace",
    },
    "pool": {
        "standard": "State-specific",
        "description": "Pool Safety Register",
        "legislation": "State pool safety legislation (ACT: Public Pools Act, NSW: Swimming Pools Act 1992)",
    },
    "whs": {
        "standard": "WHS Act 2011",
        "description": "Work Health and Safety Register",
        "legislation": "Work Health and Safety Act 2011 (Cth)",
    },
}


class RegisterItemCreate(BaseModel):
    """A single item within a compliance register."""
    description: str
    location: Optional[str] = None  # e.g. "Level 1 - Fire Stairs"
    item_type: Optional[str] = None  # e.g. "extinguisher", "smoke_detector"
    standard_reference: Optional[str] = None  # e.g. "AS 1851-4.2"
    installed_date: Optional[str] = None
    last_inspected_date: Optional[str] = None
    next_inspection_date: Optional[str] = None
    inspector_name: Optional[str] = None
    inspector_company: Optional[str] = None
    inspector_licence: Optional[str] = None
    inspection_result: Optional[str] = None  # "pass" | "fail" | "conditional"
    defects_noted: Optional[str] = None
    remediation_due_date: Optional[str] = None
    remediation_completed: bool = False
    status: str = "active"  # active | decommissioned | replaced
    notes: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = {}


class ComplianceRegisterCreate(BaseModel):
    """Create a compliance register for a building."""
    building_id: str
    register_type: str  # fire | lift | asbestos | pool | whs
    title: Optional[str] = None
    responsible_person: Optional[str] = None  # name
    responsible_person_id: Optional[str] = None  # user_id
    review_frequency_months: int = 12
    last_reviewed_date: Optional[str] = None
    next_review_date: Optional[str] = None
    notes: Optional[str] = None


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/registers", status_code=201)
async def create_compliance_register(
        payload: ComplianceRegisterCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Create a compliance register for a building."""
    user = _require_manager(current_user)
    db = _get_db()

    if payload.register_type not in REGISTER_TYPES:
        raise HTTPException(
            400,
            f"Unknown register type '{payload.register_type}'. "
            f"Valid types: {list(REGISTER_TYPES.keys())}",
        )

    # Check for duplicate
    existing = await db.compliance_registers.find_one({
        "building_id": payload.building_id,
        "register_type": payload.register_type,
    })
    if existing:
        raise HTTPException(
            409,
            f"A {payload.register_type} register already exists for this building. "
            "Update the existing register instead.",
        )

    type_meta = REGISTER_TYPES[payload.register_type]
    doc = payload.model_dump()
    # Sentinel 🛡️: Force verified building context to prevent BOLA/IDOR spoofing.
    doc["building_id"] = building_id
    doc["title"] = payload.title or type_meta["description"]
    doc["standard"] = type_meta["standard"]
    doc["legislation"] = type_meta["legislation"]
    doc["item_count"] = 0
    doc["overdue_count"] = 0
    doc["created_at"] = _now_iso()
    doc["updated_at"] = _now_iso()
    doc["created_by"] = user["id"]

    result = await db.compliance_registers.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    asyncio.create_task(create_audit_log(
        action="compliance_register_created",
        user_id=user["id"],
        user_name=user.get("full_name", "Admin"),
        resource_type="compliance_register",
        resource_id=doc["id"],
        details={"register_type": payload.register_type},
        building_id=building_id,
    ))

    return doc


@router.get("/registers")
async def list_compliance_registers(
        building_id: str = Depends(get_current_building),
        register_type: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
):
    """List compliance registers for a building."""
    _require_manager(current_user)
    db = _get_db()

    query: dict = {"building_id": building_id}
    if register_type:
        query["register_type"] = register_type

    registers = []
    async for doc in db.compliance_registers.find(query).sort("register_type", 1):
        doc["id"] = str(doc.pop("_id"))
        registers.append(doc)

    if registers:
        # Recompute overdue count for all registers in one batch to avoid O(N) database calls.
        today = str(date.today())
        register_ids = [reg["id"] for reg in registers]

        # Aggregate overdue counts for all relevant registers.
        pipeline = [
            {
                "$match": {
                    "register_id": {"$in": register_ids},
                    "next_inspection_date": {"$lt": today},
                    "status": "active",
                }
            },
            {"$group": {"_id": "$register_id", "count": {"$sum": 1}}},
        ]
        overdue_counts = {}
        async for result in db.compliance_register_items.aggregate(pipeline):
            overdue_counts[result["_id"]] = result["count"]

        for reg in registers:
            reg["overdue_count"] = overdue_counts.get(reg["id"], 0)

    return {"data": registers, "count": len(registers)}


@router.get("/registers/{register_id}")
async def get_compliance_register(
        register_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Get a compliance register with its items."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.compliance_registers.find_one({
        "_id": ObjectId(register_id),
        "building_id": building_id,
    })
    if not doc:
        raise HTTPException(404, "Compliance register not found.")
    doc["id"] = str(doc.pop("_id"))

    items = []
    async for item in db.compliance_register_items.find({"register_id": doc["id"]}).sort("description", 1):
        item["id"] = str(item.pop("_id"))
        items.append(item)
    doc["items"] = items

    return doc


@router.post("/registers/{register_id}/items", status_code=201)
async def add_register_item(
        register_id: str,
        building_id: str = Depends(get_current_building),
        payload: RegisterItemCreate = ...,
        current_user: dict = Depends(get_current_user),
):
    """Add an item to a compliance register."""
    user = _require_manager(current_user)
    db = _get_db()

    reg = await db.compliance_registers.find_one({
        "_id": ObjectId(register_id),
        "building_id": building_id,
    })
    if not reg:
        raise HTTPException(404, "Compliance register not found.")

    doc = payload.model_dump()
    doc["register_id"] = register_id
    # Sentinel 🛡️: Force verified building context to prevent BOLA/IDOR spoofing.
    doc["building_id"] = building_id
    doc["created_at"] = _now_iso()
    doc["updated_at"] = _now_iso()
    doc["created_by"] = user["id"]

    result = await db.compliance_register_items.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    # Increment item_count on register
    await db.compliance_registers.update_one(
        {"_id": ObjectId(register_id)},
        {"$inc": {"item_count": 1}, "$set": {"updated_at": _now_iso()}},
    )

    # Create compliance event if inspection due date set
    if payload.next_inspection_date:
        await db.compliance_events.insert_one({
            "building_id": building_id,
            "register_id": register_id,
            "register_item_id": doc["id"],
            "type": f"{reg.get('register_type', 'unknown')}_inspection",
            "title": f"Inspection due: {payload.description}",
            "due_date": payload.next_inspection_date,
            "completed": False,
            "risk_level": "medium",
            "created_at": _now_iso(),
        })

    return doc


@router.put("/registers/{register_id}/items/{item_id}")
async def update_register_item(
        register_id: str,
        item_id: str,
        building_id: str = Depends(get_current_building),
        payload: dict = ...,
        current_user: dict = Depends(get_current_user),
):
    """Update a compliance register item (record inspection result, next date, etc.)."""
    user = _require_manager(current_user)
    db = _get_db()

    immutable = {"_id", "id", "register_id", "building_id", "created_at", "created_by"}
    update = {k: v for k, v in payload.items() if k not in immutable}
    update["updated_at"] = _now_iso()
    update["last_updated_by"] = user["id"]

    # Fetch item before update so we can diff inspection_result in the audit log.
    old_item = await db.compliance_register_items.find_one(
        {"_id": ObjectId(item_id), "register_id": register_id, "building_id": building_id},
        {"inspection_result": 1, "responsible_person_id": 1},
    )

    result = await db.compliance_register_items.update_one(
        {"_id": ObjectId(item_id), "register_id": register_id, "building_id": building_id},
        {"$set": update},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Register item not found.")

    # Update compliance event if next inspection date changed
    if "next_inspection_date" in update:
        await db.compliance_events.update_many(
            {"register_item_id": item_id},
            {"$set": {
                "due_date": update["next_inspection_date"],
                "completed": update.get("inspection_result") is not None,
            }},
        )

    # Audit log — required for AS 1851 / WHS Act regulator submission.
    old_result = old_item.get("inspection_result") if old_item else None
    new_result = update.get("inspection_result")
    asyncio.create_task(create_audit_log(
        action="item_updated",
        resource_type="compliance_register_item",
        resource_id=item_id,
        user_id=user["id"],
        user_name=user.get("full_name", user.get("name", "")),
        details={
            "register_id": register_id,
            "old_inspection_result": old_result,
            "new_inspection_result": new_result,
            "updated_fields": list(update.keys()),
        },
        building_id=building_id,
    ))

    # Notify the responsible person immediately when a FAIL result is recorded.
    if new_result == "fail":
        responsible_id = (
            update.get("responsible_person_id")
            or (old_item.get("responsible_person_id") if old_item else None)
        )
        if responsible_id:
            asyncio.create_task(create_user_notification(
                user_id=responsible_id,
                title="Compliance Inspection: FAIL recorded",
                message="An inspection result of FAIL has been recorded for a compliance item. Immediate action may be required.",
                notification_type="compliance",
                link="/compliance",
                building_id=building_id,
            ))

    return {"message": "Item updated.", "item_id": item_id}


# ─── Events & Dashboard ───────────────────────────────────────────────────────

@router.get("/events")
async def get_compliance_events(
        building_id: str = Depends(get_current_building),
        days_ahead: int = Query(default=90, ge=1, le=365),
        completed: Optional[bool] = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        current_user: dict = Depends(get_current_user),
):
    """List upcoming compliance events."""
    # Read access for all approved users (events are building-wide dates everyone should see)
    # Write/admin operations on individual events still require manager role
    db = _get_db()

    cutoff = (date.today() + timedelta(days=days_ahead)).isoformat()
    query: dict = {
        "building_id": building_id,
        "due_date": {"$lte": cutoff},
    }
    if completed is not None:
        query["completed"] = completed

    skip = (page - 1) * page_size
    total = await db.compliance_events.count_documents(query)
    cursor = db.compliance_events.find(query).sort("due_date", 1).skip(skip).limit(page_size)

    events = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        # Compute days until due
        try:
            due = date.fromisoformat(doc["due_date"][:10])
            doc["days_until_due"] = (due - date.today()).days
        except (ValueError, KeyError):
            doc["days_until_due"] = None
        events.append(doc)

    return {"data": events, "total": total, "page": page}


@router.get("/dashboard")
async def get_compliance_dashboard(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Compliance dashboard — summary of all registers and upcoming events."""
    _require_manager(current_user)
    db = _get_db()

    today = str(date.today())
    next_30 = (date.today() + timedelta(days=30)).isoformat()
    next_90 = (date.today() + timedelta(days=90)).isoformat()

    # Parallelize register fetching and event counting to reduce latency.
    # 1. Fetch all registers for the building.
    registers_cursor = db.compliance_registers.find({"building_id": building_id})
    registers = [doc async for doc in registers_cursor]

    # 2. Parallelize batch counts for overdue items and partitioned event counts.
    async def get_overdue_item_counts():
        """Generated function header.

        Function: get_overdue_item_counts
        Path: backend/routers/compliance_registers.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if not registers:
            return {}
        reg_ids = [str(r["_id"]) for r in registers]
        pipeline = [
            {
                "$match": {
                    "register_id": {"$in": reg_ids},
                    "next_inspection_date": {"$lt": today},
                    "status": "active",
                }
            },
            {"$group": {"_id": "$register_id", "count": {"$sum": 1}}},
        ]
        counts = {}
        async for res in db.compliance_register_items.aggregate(pipeline):
            counts[res["_id"]] = res["count"]
        return counts

    async def get_event_stats():
        # Use $facet to retrieve multiple event counts in a single database round-trip.
        """Generated function header.

        Function: get_event_stats
        Path: backend/routers/compliance_registers.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        pipeline = [
            {"$match": {"building_id": building_id, "completed": False}},
            {
                "$facet": {
                    "overdue": [{"$match": {"due_date": {"$lt": today}}}, {"$count": "count"}],
                    "due_30": [{"$match": {"due_date": {"$gte": today, "$lte": next_30}}}, {"$count": "count"}],
                    "due_90": [{"$match": {"due_date": {"$gte": today, "$lte": next_90}}}, {"$count": "count"}],
                }
            }
        ]
        agg_result = await db.compliance_events.aggregate(pipeline).to_list(1)
        facet = agg_result[0] if agg_result else {}
        return {
            "overdue": facet.get("overdue", [{}])[0].get("count", 0) if facet.get("overdue") else 0,
            "due_30": facet.get("due_30", [{}])[0].get("count", 0) if facet.get("due_30") else 0,
            "due_90": facet.get("due_90", [{}])[0].get("count", 0) if facet.get("due_90") else 0,
        }

    overdue_item_counts, event_stats = await asyncio.gather(
        get_overdue_item_counts(),
        get_event_stats()
    )

    # Register summary
    register_summary = {}
    for reg in registers:
        reg_id = str(reg["_id"])
        register_summary[reg["register_type"]] = {
            "exists": True,
            "item_count": reg.get("item_count", 0),
            "overdue_count": overdue_item_counts.get(reg_id, 0),
            "standard": reg.get("standard"),
        }

    # Add missing registers
    for reg_type in REGISTER_TYPES:
        if reg_type not in register_summary:
            register_summary[reg_type] = {"exists": False, "standard": REGISTER_TYPES[reg_type]["standard"]}

    # Extract event counts
    overdue_events = event_stats["overdue"]
    due_30 = event_stats["due_30"]
    due_90 = event_stats["due_90"]

    has_critical = overdue_events > 0 or any(
        not v.get("exists") for v in register_summary.values()
    )

    return {
        "building_id": building_id,
        "as_at": _now_iso(),
        "overall_status": "critical" if has_critical else "ok",
        "registers": register_summary,
        "events": {
            "overdue": overdue_events,
            "due_in_30_days": due_30,
            "due_in_90_days": due_90,
        },
        "register_types_required": list(REGISTER_TYPES.keys()),
        "register_types_present": [k for k, v in register_summary.items() if v.get("exists")],
        "register_types_missing": [k for k, v in register_summary.items() if not v.get("exists")],
    }
