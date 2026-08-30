"""
Insurance Management Router

Endpoints:
  POST   /insurance/policies                    — create/renew policy
  GET    /insurance/policies                    — list policies (building-scoped)
  GET    /insurance/policies/{policy_id}        — get policy
  PUT    /insurance/policies/{policy_id}        — update policy
  DELETE /insurance/policies/{policy_id}        — expire/cancel policy
  GET    /insurance/compliance                  — compliance status for building
  GET    /insurance/agm-disclosure              — generate AGM insurance disclosure
  GET    /insurance/expiring                    — list policies expiring soon

ACT Compliance: UTMA 2011 s.100 — building insurance + $10M PLI mandatory
NSW Compliance: PSABAA 2002 s.47 — minimum 3 quotes per renewal
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import asyncio
import logging
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

import uuid

from models.insurance import (
    AGMInsuranceDisclosure,
    InsuranceBrokerCreate,
    InsuranceBrokerResponse,
    InsuranceBrokerUpdate,
    InsurancePolicyCreate,
    InsurancePolicyUpdate,
    InsurancePolicyResponse,
)
from services.jurisdiction_service import JurisdictionService
from utils.auth import get_current_user, get_current_building, effective_role
from utils.route_guards import require_manager_surface
from utils.helpers import create_audit_log

logger = logging.getLogger(__name__)
# Function scoping (2026-08-28). Router-level, so every route in this file is
# covered including the reads — Privacy Act APP 6 limits USE to the purpose of
# collection, and looking is a use. This narrows nobody until an agency sets
# core.management_entities.function_scoping_enabled; see
# docs/architecture/strata_management_staff_access_model.md.
router = APIRouter(
    prefix="/insurance", tags=["Insurance Management"],
    dependencies=[Depends(require_manager_surface("insurance"))],
)


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/insurance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/insurance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_finance(user: dict) -> dict:
    """Generated function header.

    Function: _require_finance
    Path: backend/routers/insurance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    allowed = {"super_admin", "strata_manager", "strata_admin", "ec_member"}
    if effective_role(user) not in allowed:
        raise HTTPException(403, "Finance/EC access required.")
    return user


def _days_until(expiry_str: str) -> Optional[int]:
    """Calculate days from today until expiry_str (ISO-8601 date)."""
    try:
        expiry = date.fromisoformat(expiry_str[:10])
        delta = (expiry - date.today()).days
        return delta
    except (ValueError, TypeError):
        return None


def _determine_status(expiry_str: str) -> str:
    """Generated function header.

    Function: _determine_status
    Path: backend/routers/insurance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    days = _days_until(expiry_str)
    if days is None:
        return "unknown"
    if days < 0:
        return "expired"
    if days <= 60:
        return "pending_renewal"
    return "active"


def _compliance_check(policy: dict, jurisdiction_rules: dict) -> str:
    """Return 'compliant', 'non_compliant', or 'warning'."""
    status = policy.get("status", "unknown")
    if status == "expired":
        return "non_compliant"
    if status == "pending_renewal":
        return "warning"
    p_type = policy.get("policy_type", "")
    if p_type == "public_liability":
        min_cover = jurisdiction_rules.get("public_liability_minimum_aud", {}).get("value", 10_000_000)
        cover = policy.get("public_liability_amount") or policy.get("cover_amount", 0)
        if cover < min_cover:
            return "non_compliant"
    return "compliant"


# ─── Policy CRUD ─────────────────────────────────────────────────────────────

@router.post("/policies", response_model=InsurancePolicyResponse, status_code=201)
async def create_insurance_policy(
        payload: InsurancePolicyCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Create or renew an insurance policy (NSW 3-quote/ACT PLI rules)."""
    _require_finance(current_user)
    db, js = _get_db(), JurisdictionService(_get_db())
    payload.building_id = building_id
    state = await js.get_building_state(building_id)

    if state == "NSW":
        min_q = await js.get_rule_value(building_id, "insurance_quotes_minimum", default=3, state="NSW")
        if len(payload.quotes) < min_q and not payload.quotes_waived_reason:
            raise HTTPException(400, f"NSW requires at least {min_q} quotes.")

    if state == "ACT" and payload.policy_type == "public_liability":
        min_c = await js.get_rule_value(building_id, "public_liability_minimum_aud", default=10_000_000, state="ACT")
        cover = payload.public_liability_amount or payload.cover_amount
        if cover < min_c:
            raise HTTPException(400, f"ACT public liability insurance must be at least ${min_c:,.0f}.")

    expiry, status = payload.expiry_date[:10], _determine_status(payload.expiry_date[:10])
    doc = payload.model_dump()
    doc.update({
        "status": status, "quotes_count": len(payload.quotes), "days_until_expiry": _days_until(expiry),
        "compliance_status": "compliant" if status == "active" else "warning",
        "created_at": _now_iso(), "updated_at": _now_iso(), "created_by": current_user["id"]
    })

    res = await db.insurance_policies.insert_one(doc)
    doc["id"] = str(res.inserted_id)
    asyncio.create_task(create_audit_log(
        "insurance_policy_created", "insurance_policy", doc["id"], current_user["id"],
        current_user.get("full_name", "Unknown"),
        {"type": payload.policy_type, "insurer": payload.insurer_name}, building_id
    ))
    return InsurancePolicyResponse(**doc)


@router.get("/policies")
async def list_insurance_policies(
        building_id: str = Depends(get_current_building),
        policy_type: Optional[str] = None,
        policy_status: Optional[str] = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
):
    """List insurance policies for a building."""
    _require_finance(current_user)
    db = _get_db()

    query: dict = {"building_id": building_id}
    if policy_type:
        query["policy_type"] = policy_type
    if policy_status:
        query["status"] = policy_status

    skip = (page - 1) * page_size
    total = await db.insurance_policies.count_documents(query)
    cursor = db.insurance_policies.find(query).sort("expiry_date", 1).skip(skip).limit(page_size)

    policies = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        doc["days_until_expiry"] = _days_until(doc.get("expiry_date", ""))
        doc["status"] = _determine_status(doc.get("expiry_date", ""))
        policies.append(doc)

    return {"data": policies, "total": total, "page": page, "page_size": page_size}


@router.get("/policies/{policy_id}")
async def get_insurance_policy(
        policy_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Get a single insurance policy."""
    _require_finance(current_user)
    db = _get_db()

    doc = await db.insurance_policies.find_one({
        "_id": ObjectId(policy_id),
        "building_id": building_id,
    })
    if not doc:
        raise HTTPException(404, "Insurance policy not found.")
    doc["id"] = str(doc.pop("_id"))
    doc["days_until_expiry"] = _days_until(doc.get("expiry_date", ""))
    doc["status"] = _determine_status(doc.get("expiry_date", ""))
    return doc


@router.put("/policies/{policy_id}")
async def update_insurance_policy(
        policy_id: str, payload: InsurancePolicyUpdate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Update an insurance policy."""
    _require_finance(current_user)
    db = _get_db()
    # Normalise dict payloads (e.g. direct test calls) through the Pydantic schema
    # so validation and extra-field stripping behave identically to the HTTP path.
    if isinstance(payload, dict):
        payload = InsurancePolicyUpdate.model_validate(payload)
    upd = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    upd["updated_at"] = _now_iso()

    res = await db.insurance_policies.update_one({"_id": ObjectId(policy_id), "building_id": building_id},
                                                 {"$set": upd})
    if res.matched_count == 0:
        raise HTTPException(404, "Insurance policy not found.")

    asyncio.create_task(create_audit_log(
        "insurance_policy_updated", "insurance_policy", policy_id, current_user["id"],
        current_user.get("full_name", "Unknown"), {"fields": list(upd.keys())}, building_id
    ))
    return {"message": "Policy updated.", "policy_id": policy_id}


@router.delete("/policies/{policy_id}", status_code=204)
async def cancel_insurance_policy(
        policy_id: str, reason: Optional[str] = Query(None),
        building_id: str = Depends(get_current_building), current_user: dict = Depends(get_current_user),
):
    """Cancel or expire an insurance policy."""
    _require_finance(current_user)
    res = await _get_db().insurance_policies.update_one(
        {"_id": ObjectId(policy_id), "building_id": building_id},
        {"$set": {"status": "cancelled", "cancelled_at": _now_iso(), "cancelled_by": current_user["id"],
                  "cancellation_reason": reason, "updated_at": _now_iso()}}
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Insurance policy not found.")

    asyncio.create_task(create_audit_log(
        "insurance_policy_cancelled", "insurance_policy", policy_id, current_user["id"],
        current_user.get("full_name", "Unknown"), {"reason": reason}, building_id
    ))


@router.get("/compliance")
async def get_insurance_compliance(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Return insurance compliance status for a building.

    Checks:
    - Building insurance exists and is active
    - Public liability ≥ $10M (ACT) and active
    - No policies expired
    """
    _require_finance(current_user)
    db = _get_db()
    js = JurisdictionService(db)

    all_rules = await js.get_all_rules(building_id)
    state = await js.get_building_state(building_id)

    active_policies = []
    async for doc in db.insurance_policies.find({"building_id": building_id}, {"quotes": 0}):
        doc["id"] = str(doc.pop("_id"))
        doc["status"] = _determine_status(doc.get("expiry_date", ""))
        doc["compliance_status"] = _compliance_check(doc, all_rules)
        active_policies.append(doc)

    has_building = any(
        p["policy_type"] == "building" and p["status"] == "active"
        for p in active_policies
    )
    pli_policies = [
        p for p in active_policies
        if p["policy_type"] == "public_liability" and p["status"] == "active"
    ]
    min_pli = all_rules.get("public_liability_minimum_aud", {}).get("value", 10_000_000)
    pli_ok = any(
        (p.get("public_liability_amount") or p.get("cover_amount", 0)) >= min_pli
        for p in pli_policies
    )

    issues = []
    if not has_building:
        issues.append({
            "severity": "critical",
            "message": f"Building insurance not found or expired ({state} — UTMA 2011 s.100).",
        })
    if not pli_ok:
        issues.append({
            "severity": "critical",
            "message": f"Public liability insurance ≥ ${min_pli:,.0f} required ({state} — UTMA 2011 s.100(2)).",
        })

    expired = [p for p in active_policies if p["status"] == "expired"]
    for p in expired:
        issues.append({
            "severity": "critical",
            "message": f"Policy expired: {p.get('policy_type')} — {p.get('insurer_name')}.",
        })

    return {
        "building_id": building_id,
        "state": state,
        "compliant": len(issues) == 0,
        "has_building_insurance": has_building,
        "has_public_liability": pli_ok,
        "public_liability_minimum_aud": min_pli,
        "policies": active_policies,
        "issues": issues,
        "as_at": _now_iso(),
    }


@router.get("/agm-disclosure")
async def get_agm_insurance_disclosure(
        building_id: str = Depends(get_current_building),
        agm_date: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
):
    """Generate AGM insurance disclosure report.

    Required by UTMA 2011 s.100 — must be presented at each AGM.
    Includes: insurer, cover amount, premium, excess, expiry, commissions.
    """
    _require_finance(current_user)
    db = _get_db()

    policies = []
    total_premium = 0.0
    async for doc in db.insurance_policies.find({"building_id": building_id}, {"quotes": 0}):
        doc["id"] = str(doc.pop("_id"))
        doc["status"] = _determine_status(doc.get("expiry_date", ""))
        policies.append(doc)
        if doc["status"] == "active":
            total_premium += doc.get("premium_annual", 0)

    building_insurance = any(
        p["policy_type"] == "building" and p["status"] == "active"
        for p in policies
    )
    pli_policies = [p for p in policies if p["policy_type"] == "public_liability" and p["status"] == "active"]
    max_pli = max(
        (p.get("public_liability_amount") or p.get("cover_amount", 0) for p in pli_policies),
        default=0,
    )

    has_commissions = any(p.get("commission_amount") or p.get("commission_percent") for p in policies)

    return AGMInsuranceDisclosure(
        building_id=building_id,
        agm_date=agm_date or str(date.today()),
        policies=policies,
        total_premium=round(total_premium, 2),
        public_liability_amount=max_pli if max_pli else None,
        public_liability_meets_minimum=max_pli >= 10_000_000,
        building_insurance_exists=building_insurance,
        commissions_disclosed=has_commissions,
        generated_at=_now_iso(),
        generated_by=str(current_user["id"]),
    )


@router.get("/expiring")
async def get_expiring_policies(
        building_id: str = Depends(get_current_building),
        days_ahead: int = Query(default=90, ge=1, le=365),
        current_user: dict = Depends(get_current_user),
):
    """List policies expiring within *days_ahead* days."""
    _require_finance(current_user)
    db = _get_db()

    cutoff = (date.today() + timedelta(days=days_ahead)).isoformat()
    today_str = str(date.today())

    policies = []
    async for doc in db.insurance_policies.find({
        "building_id": building_id,
        "expiry_date": {"$lte": cutoff, "$gte": today_str},
    }, {"quotes": 0}):
        doc["id"] = str(doc.pop("_id"))
        doc["days_until_expiry"] = _days_until(doc.get("expiry_date", ""))
        policies.append(doc)

    return {"data": policies, "count": len(policies), "days_ahead": days_ahead}


# ─── Broker Management ────────────────────────────────────────────────────────

@router.post("/brokers", response_model=InsuranceBrokerResponse, status_code=201)
async def create_insurance_broker(
        payload: InsuranceBrokerCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Register an insurance broker for this building."""
    _require_finance(current_user)
    db = _get_db()
    broker_id = str(uuid.uuid4())
    now = _now_iso()
    doc = payload.model_dump()
    doc.update({
        "id": broker_id,
        "building_id": building_id,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "created_by": current_user["id"],
    })
    await db.insurance_brokers.insert_one(doc)
    asyncio.create_task(create_audit_log(
        "broker_created", "insurance_broker", broker_id, current_user["id"],
        current_user.get("full_name", "Unknown"),
        {"broker_name": payload.broker_name}, building_id,
    ))
    return InsuranceBrokerResponse(**doc)


@router.get("/brokers")
async def list_insurance_brokers(
        building_id: str = Depends(get_current_building),
        active_only: bool = Query(default=True),
        current_user: dict = Depends(get_current_user),
):
    """List insurance brokers for this building."""
    _require_finance(current_user)
    db = _get_db()
    query: dict = {"building_id": building_id}
    if active_only:
        query["is_active"] = True
    brokers = await db.insurance_brokers.find(query, {"_id": 0}).sort("broker_name", 1).to_list(100)
    return {"data": brokers, "count": len(brokers)}


@router.put("/brokers/{broker_id}", response_model=InsuranceBrokerResponse)
async def update_insurance_broker(
        broker_id: str,
        payload: InsuranceBrokerUpdate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Update broker details."""
    _require_finance(current_user)
    db = _get_db()
    upd = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    upd["updated_at"] = _now_iso()
    res = await db.insurance_brokers.update_one({"id": broker_id, "building_id": building_id}, {"$set": upd})
    if res.matched_count == 0:
        raise HTTPException(404, "Broker not found.")
    doc = await db.insurance_brokers.find_one({"id": broker_id, "building_id": building_id}, {"_id": 0})
    return InsuranceBrokerResponse(**doc)


@router.delete("/brokers/{broker_id}", status_code=204)
async def deactivate_insurance_broker(
        broker_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Deactivate a broker (soft delete — preserves history)."""
    _require_finance(current_user)
    res = await _get_db().insurance_brokers.update_one(
        {"id": broker_id, "building_id": building_id},
        {"$set": {"is_active": False, "updated_at": _now_iso()}},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Broker not found.")
