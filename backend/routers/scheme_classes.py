"""
Scheme Classes Router — Class A / Class B Scheme Split

Manages the formal splitting of a building into Class A (e.g. apartments)
and Class B (e.g. townhouses) dwelling classes with separate UOE pools and
per-category levy schedules, in compliance with the ACT Unit Titles Act 2001.

All write operations are restricted to the 4 management roles:
  super_admin, strata_manager, chairman, ec_member
Destructive deletes are further restricted to super_admin and strata_manager.

Prefix: /scheme-classes  (mounted at /api in server.py)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional

from database import db
from models.scheme_classes import (
    SchemeClassCreate,
    SchemeClassUpdate,
    SchemeClassResponse,
    ClassCategoryAllocationCreate,
    ClassCategoryAllocationUpdate,
    ClassCategoryAllocationResponse,
    SchemeSplitStatus,
    UOESummary,
    LevyImpactPreview,
    UnitLevyImpact,
)
from models.user import UserRole
from services.scheme_levy_service import (
    UnitRecord,
    CategoryAllocation,
    compute_class_split_levies,
    compute_class_rates,
    is_split_active,
)
from utils.auth import get_approved_user, get_current_user, get_current_building, effective_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scheme-classes", tags=["Scheme Classes"])

# Roles allowed to read and compute (all authenticated building members)
_MGMT_ROLES = [UserRole.SUPER_ADMIN, UserRole.STRATA_MANAGER, UserRole.EC_MEMBER]
# Roles allowed to write (create/update)
_EDIT_ROLES = _MGMT_ROLES
# Roles allowed to delete (destructive)
_DELETE_ROLES = [UserRole.SUPER_ADMIN, UserRole.STRATA_MANAGER]


def _now_utc() -> datetime:
    """Generated function header.

    Function: _now_utc
    Path: backend/routers/scheme_classes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc)


def _gen_id() -> str:
    """Generated function header.

    Function: _gen_id
    Path: backend/routers/scheme_classes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(uuid.uuid4())


async def _resolve_unit_numbers(
        building_id: str,
        unit_numbers: List[str],
        unit_type_prefixes: List[str],
) -> List[str]:
    """
    Return the final resolved list of unit_number values for a class.
    Explicit unit_numbers take priority; if empty, auto-assign by prefix.
    """
    if unit_numbers:
        return unit_numbers

    if unit_type_prefixes:
        # Query units collection for matching prefixes
        query = {
            "building_id": building_id,
            "$or": [{"unit_number": {"$regex": f"^{p}", "$options": "i"}} for p in unit_type_prefixes],
        }
        cursor = db.units.find(query, {"unit_number": 1})
        results = await cursor.to_list(length=None)
        return [r["unit_number"] for r in results]

    return []


async def _compute_total_uoe(building_id: str, unit_numbers: List[str]) -> float:
    """Sum entitlements for the given unit_number list."""
    if not unit_numbers:
        return 0.0
    cursor = db.units.find(
        {"building_id": building_id, "unit_number": {"$in": unit_numbers}},
        {"entitlement": 1}
    )
    rows = await cursor.to_list(length=None)
    return sum(float(r.get("entitlement") or 0) for r in rows)


def _doc_to_class_response(doc: dict) -> SchemeClassResponse:
    """Generated function header.

    Function: _doc_to_class_response
    Path: backend/routers/scheme_classes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return SchemeClassResponse(
        class_id=doc["class_id"],
        building_id=doc["building_id"],
        class_label=doc["class_label"],
        class_name=doc["class_name"],
        description=doc.get("description"),
        unit_numbers=doc.get("unit_numbers", []),
        unit_type_prefixes=doc.get("unit_type_prefixes", []),
        total_uoe=float(doc.get("total_uoe", 0)),
        unit_count=len(doc.get("unit_numbers", [])),
        effective_from=doc["effective_from"],
        is_active=doc.get("is_active", True),
        notes=doc.get("notes"),
        created_by=doc["created_by"],
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


async def _write_history(
        building_id: str,
        class_id: str,
        event_type: str,
        changed_by: str,
        snapshot: dict,
        notes: Optional[str] = None,
):
    """Generated function header.

    Function: _write_history
    Path: backend/routers/scheme_classes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await db.scheme_class_history.insert_one({
        "history_id": _gen_id(),
        "building_id": building_id,
        "class_id": class_id,
        "event_type": event_type,
        "changed_by": changed_by,
        "changed_at": _now_utc(),
        "snapshot": snapshot,
        "notes": notes,
    })


# ── Status endpoint ────────────────────────────────────────────────────────────

@router.get("/status", response_model=SchemeSplitStatus)
async def get_scheme_split_status(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building),
):
    """
    Returns whether a Class A/B split is configured and active for the building.
    Used by levy calculator, LBFI, and rental certificates to decide which
    calculation path to follow.
    """
    classes = await db.scheme_classes.find(
        {"building_id": bid, "is_active": True}, {"_id": 0}
    ).to_list(length=None)

    class_a_doc = next((c for c in classes if c["class_label"] == "class_a"), None)
    class_b_doc = next((c for c in classes if c["class_label"] == "class_b"), None)

    # A split is "active" when both classes exist and at least one has passed its effective_from
    now = _now_utc()
    split_active = bool(
        class_a_doc and class_b_doc and
        is_split_active(class_a_doc.get("effective_from"), now) and
        is_split_active(class_b_doc.get("effective_from"), now)
    )

    # Total UOE across all units in building
    all_units = await db.units.find({"building_id": bid}, {"entitlement": 1}).to_list(length=None)
    total_all = sum(float(u.get("entitlement") or 0) for u in all_units)

    effective_from = None
    if class_a_doc and class_b_doc:
        # The later of the two effective_from dates is when both are live
        ef_a = class_a_doc.get("effective_from")
        ef_b = class_b_doc.get("effective_from")
        if ef_a and ef_b:
            effective_from = max(ef_a, ef_b) if ef_a.tzinfo and ef_b.tzinfo else max(ef_a, ef_b)

    def _summary(doc: dict) -> UOESummary:
        """Generated function header.

        Function: _summary
        Path: backend/routers/scheme_classes.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return UOESummary(
            class_label=doc["class_label"],
            class_name=doc["class_name"],
            unit_count=len(doc.get("unit_numbers", [])),
            total_uoe=float(doc.get("total_uoe", 0)),
            effective_from=doc["effective_from"],
        )

    return SchemeSplitStatus(
        building_id=bid,
        split_active=split_active,
        effective_from=effective_from,
        class_a=_summary(class_a_doc) if class_a_doc else None,
        class_b=_summary(class_b_doc) if class_b_doc else None,
        total_all_lots_uoe=total_all,
    )


# ── Scheme Class CRUD ──────────────────────────────────────────────────────────

@router.get("", response_model=List[SchemeClassResponse])
async def list_scheme_classes(
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building),
):
    """List all defined scheme classes for this building (active and inactive)."""
    docs = await db.scheme_classes.find(
        {"building_id": bid}, {"_id": 0}
    ).sort("class_label", 1).to_list(length=None)
    return [_doc_to_class_response(d) for d in docs]


@router.get("/{class_id}", response_model=SchemeClassResponse)
async def get_scheme_class(
        class_id: str,
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_scheme_class
    Path: backend/routers/scheme_classes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await db.scheme_classes.find_one({"building_id": bid, "class_id": class_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Scheme class not found")
    return _doc_to_class_response(doc)


@router.post("", response_model=SchemeClassResponse, status_code=201)
async def create_scheme_class(
        payload: SchemeClassCreate,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building),
):
    """
    Define a new scheme class (Class A or Class B) for the building.
    A class_label can only exist once per building at any time — creating
    a duplicate label replaces the previous definition (use update instead).
    """
    if effective_role(current_user) not in _EDIT_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized to manage scheme classes")

    # Check for duplicate active class_label
    existing = await db.scheme_classes.find_one({
        "building_id": bid,
        "class_label": payload.class_label,
        "is_active": True,
    }, {"_id": 0})
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"An active {payload.class_label} class already exists. Use PUT to update it."
        )

    resolved_units = await _resolve_unit_numbers(bid, payload.unit_numbers, payload.unit_type_prefixes)
    total_uoe = await _compute_total_uoe(bid, resolved_units)

    now = _now_utc()
    class_id = _gen_id()
    doc = {
        "class_id": class_id,
        "building_id": bid,
        "class_label": payload.class_label,
        "class_name": payload.class_name,
        "description": payload.description,
        "unit_numbers": resolved_units,
        "unit_type_prefixes": payload.unit_type_prefixes,
        "total_uoe": total_uoe,
        "effective_from": payload.effective_from,
        "is_active": True,
        "notes": payload.notes,
        "created_by": current_user["email"],
        "created_at": now,
        "updated_at": now,
    }
    await db.scheme_classes.insert_one({k: v for k, v in doc.items() if k != "_id"})

    await _write_history(bid, class_id, "created", current_user["email"], {k: v for k, v in doc.items() if k != "_id"})

    # Update scheme_class field on individual units
    if resolved_units:
        await db.units.update_many(
            {"building_id": bid, "unit_number": {"$in": resolved_units}},
            {"$set": {"scheme_class": str(payload.class_label)}}
        )

    return _doc_to_class_response(doc)


@router.put("/{class_id}", response_model=SchemeClassResponse)
async def update_scheme_class(
        class_id: str,
        payload: SchemeClassUpdate,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building),
):
    """Update an existing scheme class definition."""
    if effective_role(current_user) not in _EDIT_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized to manage scheme classes")

    doc = await db.scheme_classes.find_one({"building_id": bid, "class_id": class_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Scheme class not found")

    updates: dict = {"updated_at": _now_utc()}

    if payload.class_name is not None:
        updates["class_name"] = payload.class_name
    if payload.description is not None:
        updates["description"] = payload.description
    if payload.effective_from is not None:
        updates["effective_from"] = payload.effective_from
    if payload.is_active is not None:
        updates["is_active"] = payload.is_active
    if payload.notes is not None:
        updates["notes"] = payload.notes

    # If unit assignment changed, re-resolve and recompute UOE
    if payload.unit_numbers is not None or payload.unit_type_prefixes is not None:
        new_unit_numbers = payload.unit_numbers if payload.unit_numbers is not None else doc["unit_numbers"]
        new_prefixes = payload.unit_type_prefixes if payload.unit_type_prefixes is not None else doc[
            "unit_type_prefixes"]
        resolved_units = await _resolve_unit_numbers(bid, new_unit_numbers, new_prefixes)
        total_uoe = await _compute_total_uoe(bid, resolved_units)
        updates["unit_numbers"] = resolved_units
        updates["unit_type_prefixes"] = new_prefixes
        updates["total_uoe"] = total_uoe

        # Clear old assignments and re-apply
        old_units = doc.get("unit_numbers", [])
        removed = set(old_units) - set(resolved_units)
        added = set(resolved_units) - set(old_units)
        class_label = doc["class_label"]
        if removed:
            await db.units.update_many(
                {"building_id": bid, "unit_number": {"$in": list(removed)}},
                {"$unset": {"scheme_class": ""}}
            )
        if added:
            await db.units.update_many(
                {"building_id": bid, "unit_number": {"$in": list(added)}},
                {"$set": {"scheme_class": class_label}}
            )

    await db.scheme_classes.update_one({"class_id": class_id}, {"$set": updates})
    updated_doc = await db.scheme_classes.find_one({"class_id": class_id}, {"_id": 0})

    await _write_history(bid, class_id, "updated", current_user["email"], updated_doc)
    return _doc_to_class_response(updated_doc)


@router.delete("/{class_id}", status_code=204)
async def delete_scheme_class(
        class_id: str,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building),
):
    """
    Deactivate a scheme class.  Hard-delete is not permitted — the record is
    retained for audit purposes.  Unit scheme_class assignments are cleared.
    """
    if effective_role(current_user) not in _DELETE_ROLES:
        raise HTTPException(status_code=403, detail="Only super_admin or strata_manager can remove scheme classes")

    doc = await db.scheme_classes.find_one({"building_id": bid, "class_id": class_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Scheme class not found")

    now = _now_utc()
    await db.scheme_classes.update_one(
        {"class_id": class_id},
        {"$set": {"is_active": False, "updated_at": now}}
    )

    # Clear scheme_class from assigned units
    unit_numbers = doc.get("unit_numbers", [])
    if unit_numbers:
        await db.units.update_many(
            {"building_id": bid, "unit_number": {"$in": unit_numbers}},
            {"$unset": {"scheme_class": ""}}
        )

    await _write_history(bid, class_id, "deactivated", current_user["email"], {**doc, "is_active": False})


# ── Category Allocations ───────────────────────────────────────────────────────

@router.get("/allocations/{financial_year}", response_model=List[ClassCategoryAllocationResponse])
async def list_category_allocations(
        financial_year: str,
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building),
):
    """List all category allocations for the given financial year."""
    docs = await db.class_category_allocations.find(
        {"building_id": bid, "financial_year": financial_year}, {"_id": 0}
    ).sort("category_name", 1).to_list(length=None)
    return [_alloc_to_response(d) for d in docs]


@router.post("/allocations", response_model=ClassCategoryAllocationResponse, status_code=201)
async def create_category_allocation(
        payload: ClassCategoryAllocationCreate,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building),
):
    """
    Define how a budget category's cost is split between classes.
    If an allocation already exists for this category+year, it is replaced.
    """
    if effective_role(current_user) not in _EDIT_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized to manage category allocations")

    now = _now_utc()
    allocation_id = _gen_id()

    # Upsert: if same category+year exists, replace it
    doc = {
        "allocation_id": allocation_id,
        "building_id": bid,
        "financial_year": payload.financial_year,
        "category_id": payload.category_id,
        "category_name": payload.category_name,
        "fund": payload.fund,
        "allocation_type": payload.allocation_type,
        "class_a_pct": payload.class_a_pct,
        "class_b_pct": payload.class_b_pct,
        "amount_cents": payload.amount_cents,
        "effective_from": payload.effective_from,
        "notes": payload.notes,
        "created_by": current_user["email"],
        "created_at": now,
        "updated_at": now,
    }
    await db.class_category_allocations.replace_one(
        {"building_id": bid, "financial_year": payload.financial_year, "category_id": payload.category_id},
        doc,
        upsert=True,
    )
    # Fetch back to get the actual stored doc (in case upsert reused _id)
    stored = await db.class_category_allocations.find_one(
        {"building_id": bid, "financial_year": payload.financial_year, "category_id": payload.category_id},
        {"_id": 0}
    )
    return _alloc_to_response(stored)


@router.put("/allocations/{allocation_id}", response_model=ClassCategoryAllocationResponse)
async def update_category_allocation(
        allocation_id: str,
        payload: ClassCategoryAllocationUpdate,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building),
):
    """Generated function header.

    Function: update_category_allocation
    Path: backend/routers/scheme_classes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in _EDIT_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")

    doc = await db.class_category_allocations.find_one(
        {"building_id": bid, "allocation_id": allocation_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Allocation not found")

    updates: dict = {"updated_at": _now_utc()}
    if payload.allocation_type is not None:
        updates["allocation_type"] = payload.allocation_type
    if payload.class_a_pct is not None:
        updates["class_a_pct"] = payload.class_a_pct
    if payload.class_b_pct is not None:
        updates["class_b_pct"] = payload.class_b_pct
    if payload.amount_cents is not None:
        updates["amount_cents"] = payload.amount_cents
    if payload.notes is not None:
        updates["notes"] = payload.notes

    await db.class_category_allocations.update_one({"allocation_id": allocation_id}, {"$set": updates})
    updated = await db.class_category_allocations.find_one({"allocation_id": allocation_id}, {"_id": 0})
    return _alloc_to_response(updated)


@router.delete("/allocations/{allocation_id}", status_code=204)
async def delete_category_allocation(
        allocation_id: str,
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building),
):
    """Generated function header.

    Function: delete_category_allocation
    Path: backend/routers/scheme_classes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in _DELETE_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized to delete allocations")

    result = await db.class_category_allocations.delete_one(
        {"building_id": bid, "allocation_id": allocation_id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Allocation not found")


# ── Levy impact preview ────────────────────────────────────────────────────────

@router.post("/preview-levy-impact", response_model=LevyImpactPreview)
async def preview_levy_impact(
        financial_year: str = Query(..., description="e.g. '2026-2027'"),
        current_user: dict = Depends(get_current_user),
        bid: str = Depends(get_current_building),
):
    """
    Compute a what-if preview of how the Class A/B split would change each
    unit's levy for the specified financial year, compared to the current
    single-pool calculation.

    Uses the currently stored class definitions and category allocations.
    Does NOT write any data.
    """
    if effective_role(current_user) not in _MGMT_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Load units
    unit_docs = await db.units.find(
        {"building_id": bid},
        {"unit_number": 1, "entitlement": 1, "scheme_class": 1}
    ).to_list(length=None)

    units = [UnitRecord(u["unit_number"], u.get("entitlement", 0), u.get("scheme_class")) for u in unit_docs]
    total_ue_all = sum(u.entitlement for u in units) or 1

    # Load category allocations for the year
    alloc_docs = await db.class_category_allocations.find(
        {"building_id": bid, "financial_year": financial_year}, {"_id": 0}
    ).to_list(length=None)

    allocations = [
        CategoryAllocation(
            d["category_id"], d["category_name"], d["fund"],
            d["allocation_type"], d["class_a_pct"], d["class_b_pct"], d["amount_cents"]
        ) for d in alloc_docs
    ]

    # Also add any levy categories NOT covered by a class allocation → treat as all_lots
    # levy_categories uses `year` (e.g. "2026") and `fund_type` ("administrative"/"sinking")
    covered_ids = {d["category_id"] for d in alloc_docs}
    year_key = financial_year.split("-")[0]  # "2026-2027" → "2026"
    levy_cat_docs = await db.levy_categories.find(
        {"building_id": bid, "year": year_key},
        {"_id": 0, "id": 1, "name": 1, "fund_type": 1, "budgeted_amount": 1}
    ).to_list(length=None)

    for b in levy_cat_docs:
        cat_id = b.get("id") or b.get("name", "unknown")
        if cat_id and cat_id not in covered_ids:
            # Map fund_type: "administrative" → "admin", "sinking" → "sinking"
            raw_fund = b.get("fund_type", "administrative")
            fund = "sinking" if "sinking" in raw_fund.lower() else "admin"
            amount_dollars = float(b.get("budgeted_amount") or 0)
            allocations.append(CategoryAllocation(
                cat_id, b.get("name", "Unknown"), fund,
                "all_lots", 50.0, 50.0, int(amount_dollars * 100)
            ))

    # Compute class-split levy
    admin_split, sinking_split = compute_class_split_levies(units, allocations)

    # Compute current single-pool levy (all_lots for all categories)
    total_budget_admin = sum(a.amount for a in allocations if a.fund == "admin")
    total_budget_sinking = sum(a.amount for a in allocations if a.fund == "sinking")

    rates = compute_class_rates(units, allocations)

    unit_impacts = []
    class_a_total = class_b_total = 0.0

    for u in units:
        projected = admin_split[u.unit_number] + sinking_split[u.unit_number]
        current = (total_budget_admin + total_budget_sinking) * (u.entitlement / total_ue_all)
        delta = projected - current
        delta_pct = (delta / current * 100) if current else 0.0
        unit_impacts.append(UnitLevyImpact(
            unit_number=u.unit_number,
            scheme_class=u.scheme_class,
            current_levy=round(current, 2),
            projected_levy=round(projected, 2),
            delta=round(delta, 2),
            delta_pct=round(delta_pct, 2),
        ))
        if u.scheme_class == "class_a":
            class_a_total += projected
        elif u.scheme_class == "class_b":
            class_b_total += projected

    grand_total = class_a_total + class_b_total

    return LevyImpactPreview(
        building_id=bid,
        financial_year=financial_year,
        class_a_rate_per_ue=round(rates.get("class_a_admin_rate", 0) + rates.get("class_a_sinking_rate", 0), 4),
        class_b_rate_per_ue=round(rates.get("class_b_admin_rate", 0) + rates.get("class_b_sinking_rate", 0), 4),
        all_lots_rate_per_ue=round(rates.get("all_lots_admin_rate", 0) + rates.get("all_lots_sinking_rate", 0), 4),
        units=sorted(unit_impacts, key=lambda x: x.unit_number),
        class_a_total_levy=round(class_a_total, 2),
        class_b_total_levy=round(class_b_total, 2),
        grand_total_levy=round(grand_total, 2),
    )


# ── History ────────────────────────────────────────────────────────────────────

@router.get("/{class_id}/history")
async def get_class_history(
        class_id: str,
        current_user: dict = Depends(get_approved_user),
        bid: str = Depends(get_current_building),
):
    """Return the full audit history for a scheme class."""
    docs = await db.scheme_class_history.find(
        {"building_id": bid, "class_id": class_id}, {"_id": 0}
    ).sort("changed_at", -1).to_list(length=None)
    return docs


# ── Helpers ────────────────────────────────────────────────────────────────────

def _alloc_to_response(doc: dict) -> ClassCategoryAllocationResponse:
    """Generated function header.

    Function: _alloc_to_response
    Path: backend/routers/scheme_classes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return ClassCategoryAllocationResponse(
        allocation_id=doc["allocation_id"],
        building_id=doc["building_id"],
        financial_year=doc["financial_year"],
        category_id=doc["category_id"],
        category_name=doc["category_name"],
        fund=doc["fund"],
        allocation_type=doc["allocation_type"],
        class_a_pct=float(doc.get("class_a_pct", 50)),
        class_b_pct=float(doc.get("class_b_pct", 50)),
        amount_cents=int(doc.get("amount_cents", 0)),
        effective_from=doc["effective_from"],
        notes=doc.get("notes"),
        created_by=doc["created_by"],
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )
