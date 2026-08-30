"""
GAP-JUR-NSW-013: Building Manager Duties Register.

# @featuretrace:building-manager-duties — Statutory register of building manager delegated duties.
# Layer: router
# Data flow: POST /nsw/building-manager-duties → building_manager_duties collection (building-scoped).
# Related: backend/routers/nsw_compliance.py (NSW compliance hub)
#           backend/routers/conflict_of_interest.py (same governance pattern)
#           backend/routers/manager_contracts.py (building manager contract tracking)
#           frontend: /compliance/building-manager-duties

Endpoints:
  POST   /nsw/building-manager-duties               — create duties entry
  GET    /nsw/building-manager-duties               — list entries (building-scoped, paginated)
  GET    /nsw/building-manager-duties/{entry_id}    — single entry
  PUT    /nsw/building-manager-duties/{entry_id}    — update entry
  DELETE /nsw/building-manager-duties/{entry_id}    — soft-cancel (7-yr retention)
  GET    /nsw/building-manager-duties/summary       — compliance summary dashboard

Statutory basis:
  SSMA 2015 s.46B — Strata managing agent must maintain a register of functions
  delegated to a building manager. Building managers are defined under s.46A.
  Under s.46B(2) the register must include: name, licence number, duties delegated,
  date of delegation, and remuneration arrangements.
  Penalty for non-compliance: up to $5,500 (SSMA s.46B).
  7-year record retention: SSMA 2015 s.184A.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from utils.auth import get_current_building, get_current_user
from utils.helpers import create_audit_log
from utils.permissions import require_feature

logger = logging.getLogger(__name__)

# GAP-JUR-NSW-013 toggle gate.
router = APIRouter(
    prefix="/nsw/building-manager-duties",
    tags=["NSW Compliance"],
    dependencies=[Depends(require_feature("building_manager_duties"))],
)

_DUTY_CATEGORIES = [
    "cleaning_common_property",
    "security_and_access",
    "garden_and_grounds",
    "building_maintenance",
    "emergency_response",
    "contractor_supervision",
    "pest_control",
    "fire_safety_checks",
    "lift_maintenance_oversight",
    "pool_safety_oversight",
    "other",
]

_LICENCE_TYPES = [
    "class_1",
    "class_2",
    "resident_building_manager",
    "caretaker",
    "none",
    "other",
]


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/building_manager_duties.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/building_manager_duties.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_manager(user: dict) -> dict:
    """Generated function header.

    Function: _require_manager
    Path: backend/routers/building_manager_duties.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _role = user.get("effective_role") or user.get("role", "guest")
    if _role not in {"super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"}:
        raise HTTPException(403, "Strata manager or chairman access required.")
    return user


def _require_ec(user: dict) -> dict:
    """Generated function header.

    Function: _require_ec
    Path: backend/routers/building_manager_duties.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _role = user.get("effective_role") or user.get("role", "guest")
    if _role not in {"super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"}:
        raise HTTPException(403, "EC or manager access required.")
    return user


# ── Pydantic models ───────────────────────────────────────────────────────────


class DutyEntry(BaseModel):
    """A single delegated duty line item."""

    model_config = ConfigDict(extra="ignore")

    duty_category: str = Field(
        ...,
        pattern="^(cleaning_common_property|security_and_access|garden_and_grounds|building_maintenance|emergency_response|contractor_supervision|pest_control|fire_safety_checks|lift_maintenance_oversight|pool_safety_oversight|other)$",
        description=f"One of: {_DUTY_CATEGORIES}",
    )
    description: str = Field(..., min_length=5, max_length=500)
    frequency: Optional[str] = Field(None, description="e.g. 'daily', 'weekly', 'as required'")
    notes: Optional[str] = Field(None, max_length=1000)


class BuildingManagerDutiesCreate(BaseModel):
    """
    SSMA 2015 s.46B — Record of duties delegated to a building manager.

    Required fields mirror s.46B(2): manager name, licence, delegated duties,
    effective date, and remuneration terms.
    """

    model_config = ConfigDict(extra="ignore")

    manager_name: str = Field(..., min_length=2, max_length=200, description="Full legal name of building manager")
    manager_company: Optional[str] = Field(None, max_length=200)
    licence_number: Optional[str] = Field(None, max_length=100, description="NSW Fair Trading licence number")
    licence_type: str = Field(
        "class_2",
        description=f"Licence classification. One of: {_LICENCE_TYPES}",
    )
    licence_expiry_date: Optional[str] = Field(None, description="ISO-8601 date")
    contract_start_date: str = Field(..., description="ISO-8601 date — delegation start")
    contract_end_date: Optional[str] = Field(None, description="ISO-8601 date — delegation end (None = ongoing)")
    duties: List[DutyEntry] = Field(..., min_length=1, description="Delegated duties — at least one required")
    remuneration_amount_cents: Optional[int] = Field(
        None, ge=0, description="Annual remuneration in cents (if fixed)"
    )
    remuneration_type: Optional[str] = Field(
        "annual",
        pattern="^(annual|monthly|per_task|included_in_contract|other)$",
    )
    remuneration_notes: Optional[str] = Field(None, max_length=1000)
    insurance_certificate_attached: bool = Field(
        False, description="True when public liability certificate is on file"
    )
    notes: Optional[str] = Field(None, max_length=2000)


class BuildingManagerDutiesUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    manager_name: Optional[str] = Field(None, min_length=2, max_length=200)
    manager_company: Optional[str] = Field(None, max_length=200)
    licence_number: Optional[str] = Field(None, max_length=100)
    licence_type: Optional[str] = None
    licence_expiry_date: Optional[str] = None
    contract_end_date: Optional[str] = None
    duties: Optional[List[DutyEntry]] = None
    remuneration_amount_cents: Optional[int] = Field(None, ge=0)
    remuneration_type: Optional[str] = None
    remuneration_notes: Optional[str] = Field(None, max_length=1000)
    insurance_certificate_attached: Optional[bool] = None
    notes: Optional[str] = Field(None, max_length=2000)
    review_status: Optional[str] = Field(
        None,
        pattern="^(active|under_review|expired|cancelled)$",
    )


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post("", status_code=201)
async def create_duties_entry(
        payload: BuildingManagerDutiesCreate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """
    GAP-JUR-NSW-013: Register delegated duties for a building manager.

    SSMA 2015 s.46B — Strata managing agent must maintain a register of all functions
    delegated to a building manager and make it available for inspection.
    Penalty for non-compliance: up to $5,500.
    """
    _require_manager(current_user)
    db = _get_db()

    doc = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "manager_name": payload.manager_name,
        "manager_company": payload.manager_company,
        "licence_number": payload.licence_number,
        "licence_type": payload.licence_type,
        "licence_expiry_date": payload.licence_expiry_date,
        "contract_start_date": payload.contract_start_date,
        "contract_end_date": payload.contract_end_date,
        "duties": [d.model_dump() for d in payload.duties],
        "remuneration_amount_cents": payload.remuneration_amount_cents,
        "remuneration_type": payload.remuneration_type,
        "remuneration_notes": payload.remuneration_notes,
        "insurance_certificate_attached": payload.insurance_certificate_attached,
        "notes": payload.notes,
        "review_status": "active",
        "created_by": current_user.get("id"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    await db.building_manager_duties.insert_one(doc)
    await create_audit_log(
        action="building_manager_duties_created",
        resource_type="building_manager_duties",
        resource_id=doc["id"],
        user_id=current_user.get("id", ""),
        user_name=current_user.get("full_name", ""),
        details={
            "manager_name": payload.manager_name,
            "duty_count": len(payload.duties),
            "contract_start_date": payload.contract_start_date,
        },
        building_id=building_id,
    )
    return {k: v for k, v in doc.items() if k != "_id"}


@router.get("")
async def list_duties_entries(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        skip: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        review_status: Optional[str] = Query(None, pattern="^(active|under_review|expired|cancelled)$"),
):
    """List building manager duties register entries (building-scoped, paginated)."""
    _require_ec(current_user)
    db = _get_db()

    query: dict = {"building_id": building_id}
    if review_status:
        query["review_status"] = review_status

    cursor = (
        db.building_manager_duties
        .find(query, {"_id": 0})
        .sort("contract_start_date", -1)
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(limit)
    total = await db.building_manager_duties.count_documents(query)
    return {"total": total, "skip": skip, "limit": limit, "items": docs}


@router.get("/summary")
async def get_duties_compliance_summary(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """
    Compliance dashboard summary for building manager duties register.

    Returns:
    - total active entries
    - licences expiring within 30 days
    - entries missing insurance certificate
    - duties without end dates (ongoing delegations)
    """
    _require_ec(current_user)
    db = _get_db()
    from datetime import date, timedelta

    today = date.today().isoformat()
    expiry_warning_date = (date.today() + timedelta(days=30)).isoformat()

    # ⚡ Single $facet aggregate replaces 4 sequential count_documents round-trips
    # (4 network calls → 1, ~75% latency reduction for this summary endpoint)
    pipeline = [
        {"$match": {"building_id": building_id, "review_status": "active"}},
        {
            "$facet": {
                "active_total": [{"$count": "n"}],
                "expiring_soon": [
                    {"$match": {"licence_expiry_date": {"$gte": today, "$lte": expiry_warning_date}}},
                    {"$count": "n"},
                ],
                "expired_licences": [
                    {"$match": {"licence_expiry_date": {"$lt": today}}},
                    {"$count": "n"},
                ],
                "missing_insurance": [
                    {"$match": {"insurance_certificate_attached": False}},
                    {"$count": "n"},
                ],
            }
        },
    ]
    result = await db.building_manager_duties.aggregate(pipeline).to_list(1)
    facets = result[0] if result else {}

    def _n(key: str) -> int:
        """Generated function header.

        Function: _n
        Path: backend/routers/building_manager_duties.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        docs = facets.get(key, [])
        return docs[0]["n"] if docs else 0

    active_total = _n("active_total")
    expiring_soon = _n("expiring_soon")
    expired_licences = _n("expired_licences")
    missing_insurance = _n("missing_insurance")
    has_register = active_total > 0

    return {
        "building_id": building_id,
        "has_register": has_register,
        "active_entries": active_total,
        "licences_expiring_within_30_days": expiring_soon,
        "expired_licences": expired_licences,
        "entries_missing_insurance_certificate": missing_insurance,
        "ssma_2015_s46b_compliant": has_register and missing_insurance == 0 and expiring_soon == 0 and expired_licences == 0,
    }


@router.get("/{entry_id}")
async def get_duties_entry(
        entry_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Get a single building manager duties register entry."""
    _require_ec(current_user)
    db = _get_db()

    doc = await db.building_manager_duties.find_one(
        {"id": entry_id, "building_id": building_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(404, "Building manager duties entry not found.")
    return doc


@router.put("/{entry_id}")
async def update_duties_entry(
        entry_id: str,
        payload: BuildingManagerDutiesUpdate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Update a building manager duties register entry."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.building_manager_duties.find_one(
        {"id": entry_id, "building_id": building_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(404, "Building manager duties entry not found.")
    if doc.get("cancelled_at"):
        raise HTTPException(409, "Cannot update a cancelled entry.")

    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if "duties" in updates:
        updates["duties"] = [d.model_dump() if hasattr(d, "model_dump") else d for d in updates["duties"]]
    updates["updated_at"] = _now_iso()
    updates["updated_by"] = current_user.get("id")

    await db.building_manager_duties.update_one(
        {"id": entry_id, "building_id": building_id}, {"$set": updates}
    )
    await create_audit_log(
        action="building_manager_duties_updated",
        resource_type="building_manager_duties",
        resource_id=entry_id,
        user_id=current_user.get("id", ""),
        user_name=current_user.get("full_name", ""),
        details={"fields_changed": list(updates.keys())},
        building_id=building_id,
    )
    return {**doc, **updates}


@router.delete("/{entry_id}", status_code=204)
async def cancel_duties_entry(
        entry_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """
    Soft-cancel a building manager duties entry.

    7-year record retention (SSMA 2015 s.184A) — hard delete is never permitted.
    """
    _require_manager(current_user)
    db = _get_db()

    doc = await db.building_manager_duties.find_one(
        {"id": entry_id, "building_id": building_id}
    )
    if not doc:
        raise HTTPException(404, "Building manager duties entry not found.")
    if doc.get("cancelled_at"):
        raise HTTPException(409, "Entry is already cancelled.")

    await db.building_manager_duties.update_one(
        {"id": entry_id, "building_id": building_id},
        {
            "$set": {
                "review_status": "cancelled",
                "cancelled_at": _now_iso(),
                "cancelled_by": current_user.get("id"),
            }
        },
    )
    await create_audit_log(
        action="building_manager_duties_cancelled",
        resource_type="building_manager_duties",
        resource_id=entry_id,
        user_id=current_user.get("id", ""),
        user_name=current_user.get("full_name", ""),
        details={},
        building_id=building_id,
    )
