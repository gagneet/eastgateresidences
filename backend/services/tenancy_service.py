"""
Tenancy Service — manages investment properties, leases, rent ledger,
inspections and tenant maintenance requests for OwnerHub.
"""
import logging
from datetime import date

import asyncio
from bson import ObjectId
from fastapi import HTTPException
from typing import Optional

from database import db
from utils.helpers import create_audit_log, get_current_utc_iso

logger = logging.getLogger(__name__)

OWNERHUB_STAFF_ROLES = {"strata_admin", "ec_member", "strata_manager", "admin_staff"}


def _effective_role(user: dict) -> str:
    """Generated function header.

    Function: _effective_role
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return user.get("effective_role") or user.get("role", "")


def _staff_share_clauses(user_id: str) -> list[dict]:
    """Generated function header.

    Function: _staff_share_clauses
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return [
        {"shared_staff_ids": user_id},
        {"shared_with_user_ids": user_id},
        {"allowed_user_ids": user_id},
        {"assigned_staff_ids": user_id},
    ]


def ownerhub_property_visibility_query(user: dict) -> dict:
    """Mongo filter for OwnerHub property visibility.

    Investment property records are private to the creating owner by default.
    Real estate agents and staff only see records explicitly assigned/shared to
    their user id. Super admins retain platform-wide support visibility.
    """
    role = _effective_role(user)
    user_id = user.get("id", "")
    if role == "super_admin":
        return {}
    if role == "real_estate_agent":
        return {"assigned_agent_ids": user_id}
    if role in OWNERHUB_STAFF_ROLES:
        return {"$or": _staff_share_clauses(user_id)}
    return {"owner_id": user_id}


def can_access_ownerhub_property(doc: dict, user: dict) -> bool:
    """Generated function header.

    Function: can_access_ownerhub_property
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = _effective_role(user)
    user_id = user.get("id", "")
    if role == "super_admin":
        return True
    if doc.get("owner_id") == user_id:
        return True
    if role == "real_estate_agent" and user_id in (doc.get("assigned_agent_ids") or []):
        return True
    if role in OWNERHUB_STAFF_ROLES:
        return any(user_id in (doc.get(key) or []) for key in (
            "shared_staff_ids",
            "shared_with_user_ids",
            "allowed_user_ids",
            "assigned_staff_ids",
        ))
    return False


# ── Helpers ────────────────────────────────────────────────────────────────

def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return get_current_utc_iso()


def _oid(id_str: str) -> ObjectId:
    """Generated function header.

    Function: _oid
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid ID: {id_str}")


def _doc(doc: dict) -> dict:
    """Normalise a MongoDB document: convert _id to id string."""
    if doc is None:
        return None
    d = dict(doc)
    if "_id" in d:
        d["id"] = str(d.pop("_id"))
    return d


# ── Properties ─────────────────────────────────────────────────────────────

async def create_property(owner_id: str, data: dict) -> dict:
    """Generated function header.

    Function: create_property
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    now = _now()
    doc = {
        **data,
        "owner_id": owner_id,
        "status": "vacant",
        "assigned_agent_ids": data.get("assigned_agent_ids") or [],
        "shared_staff_ids": data.get("shared_staff_ids") or [],
        "created_at": now,
        "updated_at": now,
    }
    result = await db.owner_properties.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    asyncio.create_task(create_audit_log(
        user_id=owner_id, user_name="", action="create_property",
        resource_type="property", resource_id=doc["id"],
        details={"address": data.get("address")}
    ))
    return doc


async def get_properties_for_owner(owner_id: str) -> list:
    """
    Get all properties for a specific owner.
    Performance Optimization⚡: Eliminated redundant two-step fetch to reduce database round-trips from 2 to 1.
    """
    cursor = db.owner_properties.find({"owner_id": owner_id})
    docs = await cursor.to_list(200)
    return [_doc(d) for d in docs]


async def get_property_by_id(property_id: str, requesting_user: dict) -> dict:
    """Generated function header.

    Function: get_property_by_id
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await db.owner_properties.find_one({"_id": _oid(property_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Property not found")
    doc = _doc(doc)
    if not can_access_ownerhub_property(doc, requesting_user):
        raise HTTPException(status_code=403, detail="Access denied")
    return doc


async def update_property(property_id: str, updates: dict, requesting_user: dict) -> dict:
    """Generated function header.

    Function: update_property
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await get_property_by_id(property_id, requesting_user)
    updates["updated_at"] = _now()
    await db.owner_properties.update_one(
        {"_id": _oid(property_id)},
        {"$set": updates}
    )
    asyncio.create_task(create_audit_log(
        user_id=requesting_user.get("id"), user_name="", action="update_property",
        resource_type="property", resource_id=property_id,
        details={"fields": list(updates.keys())}
    ))
    return {**doc, **updates}


async def delete_property(property_id: str, owner_id: str) -> bool:
    """Generated function header.

    Function: delete_property
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await db.owner_properties.delete_one(
        {"_id": _oid(property_id), "owner_id": owner_id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Property not found or not owned by user")
    asyncio.create_task(create_audit_log(
        user_id=owner_id, user_name="", action="delete_property",
        resource_type="property", resource_id=property_id, details={}
    ))
    return True


# ── Tenancies ─────────────────────────────────────────────────────────────

async def create_tenancy(property_id: str, data: dict, owner_id: str) -> dict:
    """Generated function header.

    Function: create_tenancy
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    now = _now()
    doc = {
        **data,
        "property_id": property_id,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    result = await db.tenancies.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    # Mark property as occupied
    await db.owner_properties.update_one(
        {"_id": _oid(property_id)},
        {"$set": {"status": "occupied", "updated_at": now}}
    )
    asyncio.create_task(create_audit_log(
        user_id=owner_id, user_name="", action="create_tenancy",
        resource_type="tenancy", resource_id=doc["id"],
        details={"property_id": property_id}
    ))
    return doc


async def get_tenancy_by_id(tenancy_id: str) -> dict:
    """Generated function header.

    Function: get_tenancy_by_id
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await db.tenancies.find_one({"_id": _oid(tenancy_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Tenancy not found")
    return _doc(doc)


async def get_tenancies_for_property(property_id: str) -> list:
    """Generated function header.

    Function: get_tenancies_for_property
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    cursor = db.tenancies.find({"property_id": property_id})
    docs = await cursor.to_list(100)
    return [_doc(d) for d in docs]


async def get_active_tenancy(property_id: str) -> Optional[dict]:
    """Generated function header.

    Function: get_active_tenancy
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await db.tenancies.find_one({
        "property_id": property_id,
        "status": {"$in": ["active", "periodic", "pending"]}
    })
    return _doc(doc) if doc else None


async def update_tenancy(tenancy_id: str, updates: dict) -> dict:
    """Generated function header.

    Function: update_tenancy
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await get_tenancy_by_id(tenancy_id)
    updates["updated_at"] = _now()
    await db.tenancies.update_one(
        {"_id": _oid(tenancy_id)},
        {"$set": updates}
    )
    return {**doc, **updates}


async def compute_arrears(tenancy_id: str, tenancy_doc: Optional[dict] = None) -> dict:
    """
    Calculate rent arrears status for a tenancy.
    Performance Optimization⚡: Accepts optional tenancy_doc to prevent redundant DB fetches.
    """
    tenancy = tenancy_doc or await get_tenancy_by_id(tenancy_id)
    rent_amount = tenancy.get("rent_amount", 0)
    payment_frequency = tenancy.get("payment_frequency", "weekly")

    # Determine expected period in days
    freq_days = {"weekly": 7, "fortnightly": 14, "monthly": 30}.get(payment_frequency, 7)

    # Find most recent paid rent transaction
    cursor = db.rent_transactions.find(
        {"tenancy_id": tenancy_id, "type": "rent", "status": "paid"},
        sort=[("transaction_date", -1)]
    )
    recent = await cursor.to_list(1)

    today = date.today()
    arrears_amount = 0.0
    arrears_days = 0

    if recent:
        last_paid_str = recent[0].get("transaction_date", "")
        try:
            last_paid = date.fromisoformat(last_paid_str[:10])
        except Exception:
            last_paid = today
        days_since = (today - last_paid).days
        if days_since > freq_days:
            arrears_days = days_since - freq_days
            daily_rent = rent_amount / freq_days
            arrears_amount = round(daily_rent * arrears_days, 2)
    else:
        # No payments at all; check lease start
        lease_start_str = tenancy.get("lease_start", "")
        try:
            lease_start = date.fromisoformat(lease_start_str[:10])
            arrears_days = max(0, (today - lease_start).days)
            daily_rent = rent_amount / freq_days
            arrears_amount = round(daily_rent * arrears_days, 2)
        except Exception:
            arrears_days = 0
            arrears_amount = 0.0

    # Determine arrears stage
    if arrears_days == 0:
        stage = "ok"
        notice_type = None
    elif arrears_days <= 3:
        stage = "reminder"
        notice_type = "payment_reminder"
    elif arrears_days <= 7:
        stage = "warning"
        notice_type = "warning_notice"
    elif arrears_days <= 14:
        stage = "breach"
        notice_type = "breach_of_agreement"
    else:
        stage = "tribunal"
        notice_type = "notice_to_vacate"

    return {
        "tenancy_id": tenancy_id,
        "arrears_amount": arrears_amount,
        "arrears_days": arrears_days,
        "arrears_stage": stage,
        "notice_type": notice_type,
    }


# ── Rent Ledger ────────────────────────────────────────────────────────────

async def record_rent_payment(tenancy_id: str, data: dict) -> dict:
    """Generated function header.

    Function: record_rent_payment
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    now = _now()
    doc = {
        **data,
        "tenancy_id": tenancy_id,
        "type": data.get("type", "rent"),
        "status": data.get("status", "paid"),
        "created_at": now,
    }
    result = await db.rent_transactions.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return doc


async def get_rent_ledger(tenancy_id: str, limit: int = 50) -> list:
    """Generated function header.

    Function: get_rent_ledger
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    cursor = db.rent_transactions.find(
        {"tenancy_id": tenancy_id},
        sort=[("transaction_date", -1)]
    ).limit(limit)
    docs = await cursor.to_list(limit)
    return [_doc(d) for d in docs]


# ── Inspections ────────────────────────────────────────────────────────────

async def create_inspection(data: dict, created_by: str) -> dict:
    """Generated function header.

    Function: create_inspection
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    now = _now()
    doc = {
        **data,
        "status": "scheduled",
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.property_inspections.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return doc


async def get_inspections_for_property(property_id: str) -> list:
    """Generated function header.

    Function: get_inspections_for_property
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    cursor = db.property_inspections.find(
        {"property_id": property_id},
        sort=[("scheduled_date", -1)]
    )
    docs = await cursor.to_list(100)
    return [_doc(d) for d in docs]


async def get_inspections_for_properties(
        property_ids: list,
        skip: int = 0,
        limit: int = 100
) -> list:
    """Fetch all inspections for multiple properties in a single query."""
    if not property_ids:
        return []

    # Robust matching: ensure property_id can be matched as string or ObjectId
    oids = []
    for pid in property_ids:
        try:
            oids.append(ObjectId(pid))
        except Exception as exc:
            logger.warning("tenancy_service: could not cast property_id %r to ObjectId: %s", pid, exc)

    query = {
        "$or": [
            {"property_id": {"$in": property_ids}},
            {"property_id": {"$in": oids}}
        ]
    }

    cursor = db.property_inspections.find(
        query,
        sort=[("scheduled_date", -1)]
    ).skip(skip).limit(limit)

    docs = await cursor.to_list(limit)
    return [_doc(d) for d in docs]


async def update_inspection(inspection_id: str, updates: dict) -> dict:
    """Generated function header.

    Function: update_inspection
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await db.property_inspections.find_one({"_id": _oid(inspection_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Inspection not found")
    doc = _doc(doc)
    updates["updated_at"] = _now()
    await db.property_inspections.update_one(
        {"_id": _oid(inspection_id)},
        {"$set": updates}
    )
    return {**doc, **updates}


# ── Tenant Maintenance ─────────────────────────────────────────────────────

async def create_maintenance_request(data: dict, submitted_by: dict) -> dict:
    """Generated function header.

    Function: create_maintenance_request
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    now = _now()
    doc = {
        **data,
        "submitted_by_user_id": submitted_by.get("id"),
        "submitted_by_name": submitted_by.get("full_name", ""),
        "status": "submitted",
        "message_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.tenant_maintenance_requests.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    asyncio.create_task(create_audit_log(
        user_id=submitted_by.get("id"), user_name="", action="create_maintenance_request",
        resource_type="tenant_maintenance_request", resource_id=doc["id"],
        details={"urgency": data.get("urgency")}
    ))
    return doc


async def get_maintenance_requests(filters: dict, requesting_user: dict) -> list:
    """Generated function header.

    Function: get_maintenance_requests
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = _effective_role(requesting_user)
    user_id = requesting_user.get("id", "")

    query = dict(filters)

    if role == "tenant":
        query["submitted_by_user_id"] = user_id
    elif role != "super_admin":
        prop_visibility = ownerhub_property_visibility_query(requesting_user)
        props = await db.owner_properties.find(prop_visibility, {"_id": 1}).to_list(500)
        prop_ids = [str(p["_id"]) for p in props]
        query["property_id"] = {"$in": prop_ids}
    # Super admins retain platform support visibility across all OwnerHub maintenance.

    cursor = db.tenant_maintenance_requests.find(
        query, sort=[("created_at", -1)]
    )
    docs = await cursor.to_list(500)
    return [_doc(d) for d in docs]


async def update_maintenance_request(request_id: str, updates: dict, requesting_user: dict) -> dict:
    """Generated function header.

    Function: update_maintenance_request
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await db.tenant_maintenance_requests.find_one({"_id": _oid(request_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Maintenance request not found")
    doc = _doc(doc)
    updates["updated_at"] = _now()
    await db.tenant_maintenance_requests.update_one(
        {"_id": _oid(request_id)},
        {"$set": updates}
    )
    asyncio.create_task(create_audit_log(
        user_id=requesting_user.get("id"), user_name="", action="update_maintenance_request",
        resource_type="tenant_maintenance_request", resource_id=request_id,
        details={"fields": list(updates.keys())}
    ))
    return {**doc, **updates}


async def add_maintenance_message(request_id: str, message: dict, author: dict) -> dict:
    """Generated function header.

    Function: add_maintenance_message
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    now = _now()
    msg_doc = {
        **message,
        "request_id": request_id,
        "author_id": author.get("id"),
        "author_name": author.get("full_name", ""),
        "author_role": author.get("role", ""),
        "created_at": now,
    }
    result = await db.maintenance_request_messages.insert_one(msg_doc)
    msg_doc["id"] = str(result.inserted_id)
    msg_doc.pop("_id", None)
    # Increment message_count on the request
    await db.tenant_maintenance_requests.update_one(
        {"_id": _oid(request_id)},
        {"$inc": {"message_count": 1}, "$set": {"updated_at": now}}
    )
    return msg_doc


async def get_maintenance_messages(request_id: str) -> list:
    """Generated function header.

    Function: get_maintenance_messages
    Path: backend/services/tenancy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    cursor = db.maintenance_request_messages.find(
        {"request_id": request_id},
        sort=[("created_at", 1)]
    )
    docs = await cursor.to_list(200)
    return [_doc(d) for d in docs]
