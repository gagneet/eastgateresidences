"""
Manager Contract Tracking Router

ACT UTMA 2011 s.60 — strata manager agreements must not exceed 3 years.
NSW SSMA 2015 s.49 — strata managing agency agreements must not exceed 3 years.

Endpoints:
  POST  /manager-contracts           — create contract
  GET   /manager-contracts           — list contracts (building-scoped, paginated)
  GET   /manager-contracts/expiring  — list contracts expiring soon
  GET   /manager-contracts/{id}      — get single contract
  PUT   /manager-contracts/{id}      — update contract

Legislation:
  - ACT UTMA 2011 s.60  — strata manager agreement max 3 years
  - NSW SSMA 2015 s.49  — managing agency agreement max 3 years, no auto-renewal
  - ACT UTMA 2011 s.61  — contract must be disclosed at a general meeting
  - ACT PSBA 2004       — strata manager licence (ACT Revenue Office)
  - NSW Fair Trading    — real estate licence with strata management endorsement

NOTE: /expiring route MUST appear before /{id} to avoid route shadowing.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import logging
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, Any, Dict

from utils.auth import get_current_user, get_current_building, effective_role
from utils.helpers import create_audit_log

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/manager-contracts", tags=["Manager Contracts"])


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/manager_contracts.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/manager_contracts.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_manager(user: dict) -> dict:
    # "chairman" removed as a top-level role (migration 0025); effective_role() returns
    # "ec_member" for chairmen. "admin" was never a valid UserRole — use "admin_staff".
    """Generated function header.

    Function: _require_manager
    Path: backend/routers/manager_contracts.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    allowed = {"super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"}
    if effective_role(user) not in allowed:
        raise HTTPException(403, "EC or manager access required.")
    return user


def _days_until(date_str: str) -> Optional[int]:
    """Generated function header.

    Function: _days_until
    Path: backend/routers/manager_contracts.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        exp = date.fromisoformat(date_str[:10])
        return (exp - date.today()).days
    except (ValueError, TypeError):
        return None


def _contract_status(end_date: str, terminated_at: Optional[str] = None) -> str:
    """Generated function header.

    Function: _contract_status
    Path: backend/routers/manager_contracts.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if terminated_at:
        return "terminated"
    days = _days_until(end_date)
    if days is None:
        return "unknown"
    if days < 0:
        return "expired"
    return "active"


# ─── Models ───────────────────────────────────────────────────────────────────

FEE_STRUCTURES = ["percentage_of_levies", "fixed_monthly", "fixed_annual", "hourly", "per_lot"]
CONTRACT_STATUSES = ["active", "expired", "terminated"]


class ManagerContractCreate(BaseModel):
    """Strata manager agreement record (ACT UTMA 2011 s.60)."""
    model_config = ConfigDict(extra="ignore")

    building_id: str
    manager_name: str
    manager_company: str
    abn: Optional[str] = None
    licence_number: Optional[str] = None
    licence_expiry_date: Optional[str] = None
    contract_start_date: str = Field(..., description="ISO-8601 date")
    contract_end_date: str = Field(..., description="ISO-8601 date")
    contract_duration_months: int = Field(..., ge=1)
    auto_renewal: bool = Field(False, description="Auto-renewal clauses are prohibited in ACT/NSW")
    termination_notice_days: int = Field(90, ge=0)
    annual_fee: Optional[float] = Field(None, ge=0)
    fee_structure: Optional[str] = Field(None, description=f"One of: {FEE_STRUCTURES}")
    disclosed_at_meeting: bool = Field(
        False,
        description="ACT UTMA 2011 s.61 — contract must be disclosed at a general meeting",
    )
    disclosure_meeting_date: Optional[str] = None
    resolution_number: Optional[str] = None
    document_url: Optional[str] = None
    notes: Optional[str] = None


class ManagerContractUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    manager_name: Optional[str] = None
    manager_company: Optional[str] = None
    abn: Optional[str] = None
    licence_number: Optional[str] = None
    licence_expiry_date: Optional[str] = None
    contract_end_date: Optional[str] = None
    contract_duration_months: Optional[int] = None
    auto_renewal: Optional[bool] = None
    termination_notice_days: Optional[int] = None
    annual_fee: Optional[float] = None
    fee_structure: Optional[str] = None
    disclosed_at_meeting: Optional[bool] = None
    disclosure_meeting_date: Optional[str] = None
    resolution_number: Optional[str] = None
    document_url: Optional[str] = None
    status: Optional[str] = None
    terminated_at: Optional[str] = None
    termination_reason: Optional[str] = None
    notes: Optional[str] = None


def _enrich_contract(doc: dict) -> dict:
    """Add computed fields: days_until_expiry, is_compliant, max_duration_months."""
    end_date = doc.get("contract_end_date", "")
    terminated_at = doc.get("terminated_at")
    doc["days_until_expiry"] = _days_until(end_date)
    doc["status"] = _contract_status(end_date, terminated_at)

    # Jurisdiction-based max duration
    # Both ACT (UTMA 2011 s.60) and NSW (SSMA 2015 s.49) cap at 36 months
    max_months = 36
    doc["max_duration_months"] = max_months

    duration = doc.get("contract_duration_months", 0)
    licence_expiry = doc.get("licence_expiry_date")
    licence_days = _days_until(licence_expiry) if licence_expiry else None

    is_compliant = (
            duration <= max_months
            and not doc.get("auto_renewal", False)
            and (licence_days is None or licence_days >= 0)
    )
    doc["is_compliant"] = is_compliant

    compliance_issues = []
    if duration > max_months:
        compliance_issues.append(
            f"Contract duration {duration} months exceeds max {max_months} months (UTMA 2011 s.60)."
        )
    if doc.get("auto_renewal"):
        compliance_issues.append(
            "Auto-renewal clauses are prohibited (UTMA 2011 s.60; SSMA 2015 s.49)."
        )
    if licence_days is not None and licence_days < 0:
        compliance_issues.append(
            f"Strata manager licence expired {abs(licence_days)} days ago."
        )
    if not doc.get("disclosed_at_meeting"):
        compliance_issues.append(
            "Contract not yet disclosed at a general meeting (UTMA 2011 s.61)."
        )

    doc["compliance_issues"] = compliance_issues
    return doc


# ─── Routes (expiring BEFORE /{id} to avoid shadowing) ───────────────────────

@router.get("/expiring")
async def get_expiring_contracts(
        building_id: str = Depends(get_current_building),
        days_ahead: int = Query(default=90, ge=1, le=365),
        current_user: dict = Depends(get_current_user),
):
    """List strata manager contracts expiring within *days_ahead* days.

    Allows early initiation of the renewal/tender process (required under
    UTMA 2011 s.60 — must re-tender or obtain resolution before expiry).
    """
    _require_manager(current_user)
    db = _get_db()

    cutoff = (date.today() + timedelta(days=days_ahead)).isoformat()
    today_str = date.today().isoformat()

    contracts = []
    async for doc in db.manager_contracts.find({
        "building_id": building_id,
        "contract_end_date": {"$lte": cutoff, "$gte": today_str},
        "status": {"$ne": "terminated"},
    }):
        doc["id"] = str(doc.pop("_id"))
        contracts.append(_enrich_contract(doc))

    contracts.sort(key=lambda c: c.get("contract_end_date", ""))

    return {
        "data": contracts,
        "count": len(contracts),
        "days_ahead": days_ahead,
        "note": (
            "ACT UTMA 2011 s.60 / NSW SSMA 2015 s.49: Contracts cannot exceed 3 years. "
            "Renewal requires a fresh resolution at a general meeting."
        ),
    }


@router.post("", status_code=201)
async def create_manager_contract(
        payload: ManagerContractCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Create a strata manager contract record (ACT UTMA 2011 s.60).

    Validates that contract duration does not exceed 36 months (3 years).
    Warns if auto-renewal clause is present (prohibited in ACT and NSW).
    """
    user = _require_manager(current_user)
    db = _get_db()

    # ACT/NSW enforcement: max 3-year (36-month) duration
    from services.jurisdiction_service import JurisdictionService
    js = JurisdictionService(db)
    state = await js.get_building_state(building_id)
    max_years = await js.get_rule_value(
        building_id,
        "manager_contract_max_years",
        default=3,
        state=state,
    )
    max_months = max_years * 12

    if payload.contract_duration_months > max_months:
        raise HTTPException(
            400,
            f"{state} law limits strata manager contracts to {max_months} months "
            f"(UTMA 2011 s.60 / SSMA 2015 s.49). "
            f"Requested: {payload.contract_duration_months} months.",
        )

    if payload.auto_renewal:
        raise HTTPException(
            400,
            "Auto-renewal clauses are prohibited in strata manager agreements "
            "(UTMA 2011 s.60; SSMA 2015 s.49). Set auto_renewal to false.",
        )

    doc = payload.model_dump()
    # Sentinel 🛡️: Force verified building context to prevent BOLA/IDOR spoofing.
    doc["building_id"] = building_id
    doc["state"] = state
    doc["max_duration_months"] = max_months
    doc["status"] = _contract_status(payload.contract_end_date)
    doc["created_at"] = _now_iso()
    doc["updated_at"] = _now_iso()
    doc["created_by"] = user["id"]

    result = await db.manager_contracts.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc = _enrich_contract(doc)

    await create_audit_log(
        action="manager_contract_created",
        user_id=user["id"],
        user_name=user.get("full_name", "Admin"),
        resource_type="manager_contract",
        resource_id=doc["id"],
        details={
            "manager_name": payload.manager_name,
            "manager_company": payload.manager_company,
            "contract_start_date": payload.contract_start_date,
            "contract_end_date": payload.contract_end_date,
            "duration_months": payload.contract_duration_months,
        },
        building_id=building_id,
    )

    return doc


@router.get("")
async def list_manager_contracts(
        building_id: str = Depends(get_current_building),
        status: Optional[str] = Query(None, description="active | expired | terminated"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
):
    """List strata manager contracts (building-scoped, paginated)."""
    _require_manager(current_user)
    db = _get_db()

    query: Dict[str, Any] = {"building_id": building_id}
    if status:
        query["status"] = status

    skip = (page - 1) * page_size
    total = await db.manager_contracts.count_documents(query)
    cursor = db.manager_contracts.find(query).sort("contract_start_date", -1).skip(skip).limit(page_size)

    contracts = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        contracts.append(_enrich_contract(doc))

    return {
        "data": contracts,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


@router.get("/{contract_id}")
async def get_manager_contract(
        contract_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Get a single strata manager contract."""
    _require_manager(current_user)
    db = _get_db()

    try:
        oid = ObjectId(contract_id)
    except Exception:
        raise HTTPException(400, "Invalid contract ID format.")

    doc = await db.manager_contracts.find_one({"_id": oid, "building_id": building_id})
    if not doc:
        raise HTTPException(404, "Manager contract not found.")

    doc["id"] = str(doc.pop("_id"))
    return _enrich_contract(doc)


@router.put("/{contract_id}")
async def update_manager_contract(
        contract_id: str,
        building_id: str = Depends(get_current_building),
        payload: ManagerContractUpdate = ...,
        current_user: dict = Depends(get_current_user),
):
    """Update a strata manager contract (e.g., terminate, add document URL)."""
    user = _require_manager(current_user)
    db = _get_db()

    try:
        oid = ObjectId(contract_id)
    except Exception:
        raise HTTPException(400, "Invalid contract ID format.")

    update = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not update:
        raise HTTPException(400, "No fields to update.")

    # Re-validate duration if being changed
    if "contract_duration_months" in update and update["contract_duration_months"] > 36:
        raise HTTPException(
            400,
            f"Contract duration {update['contract_duration_months']} months exceeds max 36 months.",
        )

    if update.get("auto_renewal") is True:
        raise HTTPException(400, "Auto-renewal clauses are prohibited in strata manager agreements.")

    update["updated_at"] = _now_iso()
    update["updated_by"] = user["id"]

    result = await db.manager_contracts.update_one(
        {"_id": oid, "building_id": building_id},
        {"$set": update},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Manager contract not found.")

    await create_audit_log(
        action="manager_contract_updated",
        user_id=user["id"],
        user_name=user.get("full_name", "Admin"),
        resource_type="manager_contract",
        resource_id=contract_id,
        details={"updated_fields": list(update.keys())},
        building_id=building_id,
    )

    return {"message": "Contract updated.", "contract_id": contract_id}
