"""
Privacy Act 1988 (Cth) Compliance Router — Australian Privacy Principles (APPs)

Endpoints:
  POST  /privacy/consents           — record consent
  GET   /privacy/consents           — list consents (building-scoped)
  PUT   /privacy/consents/{id}      — update/withdraw consent
  POST  /privacy/breach-log         — log a data breach (NDB scheme)
  GET   /privacy/breach-log         — list breach log entries
  GET   /privacy/retention-schedule — data retention schedule
  POST  /privacy/access-request     — record an access/correction request

Legislation:
  - Privacy Act 1988 (Cth) — Australian Privacy Principles (APPs 1-13)
  - Notifiable Data Breaches (NDB) scheme — Part IIIC, Privacy Act 1988
  - APP 3  — Collection of solicited personal information
  - APP 5  — Notification of collection of personal information
  - APP 11 — Security of personal information
  - APP 12 — Access to personal information
  - ACT UTMA 2011 s.89 — 7-year minimum retention for strata records
"""
from __future__ import annotations

from datetime import datetime, timezone

import asyncio
import logging
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Any, Dict

from utils.auth import get_current_user, get_current_building, effective_role
from utils.helpers import create_audit_log, get_current_utc_iso

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/privacy", tags=["Privacy Compliance"])


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/privacy_compliance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/privacy_compliance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_admin(user: dict) -> dict:
    """Generated function header.

    Function: _require_admin
    Path: backend/routers/privacy_compliance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    allowed = {"super_admin", "strata_manager", "ec_member", "strata_admin"}
    if effective_role(user) not in allowed:
        raise HTTPException(403, "Administrator access required.")
    return user


def _require_ec(user: dict) -> dict:
    """Generated function header.

    Function: _require_ec
    Path: backend/routers/privacy_compliance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    allowed = {"super_admin", "strata_manager", "strata_admin", "ec_member"}
    if effective_role(user) not in allowed:
        raise HTTPException(403, "EC or manager access required.")
    return user


# ─── Models ───────────────────────────────────────────────────────────────────

CONSENT_TYPES = ["marketing", "data_sharing", "strata_roll", "photos", "third_party_disclosure"]
BREACH_SEVERITIES = ["eligible", "ineligible"]
DATA_TYPES = [
    "name", "address", "email", "phone", "financial_data",
    "identity_documents", "lot_entitlements", "levy_records", "photos",
]
ACCESS_REQUEST_TYPES = ["access", "correction", "deletion", "portability"]


class ConsentRecord(BaseModel):
    """APP 3/5 — consent to collect/use personal information."""
    model_config = ConfigDict(extra="ignore")

    user_id: str
    building_id: str
    consent_type: str = Field(..., description=f"One of: {CONSENT_TYPES}")
    given: bool = True
    given_at: Optional[str] = None
    withdrawn_at: Optional[str] = None
    ip_address: Optional[str] = None
    collection_purpose: Optional[str] = None
    notes: Optional[str] = None


class ConsentUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    given: Optional[bool] = None
    withdrawn_at: Optional[str] = None
    notes: Optional[str] = None


class DataBreachLog(BaseModel):
    """NDB scheme — Notifiable Data Breach log entry (Privacy Act 1988 Part IIIC)."""
    model_config = ConfigDict(extra="ignore")

    building_id: str
    detected_at: str = Field(..., description="ISO-8601 datetime breach was detected")
    description: str = Field(..., min_length=10)
    affected_count: int = Field(..., ge=0, description="Number of individuals affected")
    data_types: List[str] = Field(default_factory=list, description=f"Types of data exposed: {DATA_TYPES}")
    oaic_notified: bool = False
    oaic_notified_at: Optional[str] = None
    oaic_reference: Optional[str] = None
    individuals_notified: bool = False
    individuals_notified_at: Optional[str] = None
    remediation_steps: List[str] = Field(default_factory=list)
    severity: str = Field("eligible", description="eligible | ineligible (under NDB scheme)")
    root_cause: Optional[str] = None
    status: str = Field("open", pattern="^(open|under_review|resolved|reported)$")


class DataBreachUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    oaic_notified: Optional[bool] = None
    oaic_notified_at: Optional[str] = None
    oaic_reference: Optional[str] = None
    individuals_notified: Optional[bool] = None
    individuals_notified_at: Optional[str] = None
    remediation_steps: Optional[List[str]] = None
    severity: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class AccessRequest(BaseModel):
    """APP 12/13 — access and correction request."""
    model_config = ConfigDict(extra="ignore")

    building_id: str
    requestor_user_id: Optional[str] = None
    requestor_name: str
    requestor_email: str
    request_type: str = Field(..., description=f"One of: {ACCESS_REQUEST_TYPES}")
    data_description: str = Field(..., min_length=5, description="Description of data requested/to be corrected")
    received_at: Optional[str] = None
    due_at: Optional[str] = None
    status: str = Field("received", pattern="^(received|in_progress|completed|refused)$")
    refusal_reason: Optional[str] = None
    notes: Optional[str] = None


# ─── Static Retention Schedule ─────────────────────────────────────────────────

RETENTION_SCHEDULE = [
    {
        "collection_name": "financial_records",
        "display_name": "Financial Records (levies, transactions, budgets)",
        "retention_years": 7,
        "legal_basis": "ACT UTMA 2011 s.89; ATO record-keeping requirements",
        "destruction_method": "Secure deletion / shredding of physical copies",
        "notes": "Includes levy notices, receipts, bank statements, financial statements.",
    },
    {
        "collection_name": "meeting_minutes",
        "display_name": "Meeting Minutes (AGM, EGM, EC)",
        "retention_years": 7,
        "legal_basis": "ACT UTMA 2011 s.89; SSMA 2015 s.180",
        "destruction_method": "Secure deletion",
        "notes": "AGM and EGM minutes must be kept for 7 years.",
    },
    {
        "collection_name": "strata_roll",
        "display_name": "Strata Roll (owner, unit, entitlement data)",
        "retention_years": 7,
        "legal_basis": "ACT UTMA 2011 s.89; SSMA 2015 s.180",
        "destruction_method": "Secure deletion; remove personal data on owner transfer + 7 years",
        "notes": "APP 11 applies — must be kept secure and accurate.",
    },
    {
        "collection_name": "insurance_policies",
        "display_name": "Insurance Policies and Claims",
        "retention_years": 7,
        "legal_basis": "ACT UTMA 2011 s.89; statute of limitations",
        "destruction_method": "Secure deletion",
        "notes": "Policy documents and claim records.",
    },
    {
        "collection_name": "maintenance_records",
        "display_name": "Maintenance and Work Orders",
        "retention_years": 7,
        "legal_basis": "ACT UTMA 2011 s.89",
        "destruction_method": "Secure deletion",
        "notes": "Includes contractor invoices and reports.",
    },
    {
        "collection_name": "correspondence",
        "display_name": "Owner / Tenant Correspondence",
        "retention_years": 7,
        "legal_basis": "ACT UTMA 2011 s.89; Privacy Act APP 11",
        "destruction_method": "Secure deletion",
        "notes": "Includes complaints, disputes, and legal correspondence.",
    },
    {
        "collection_name": "contracts",
        "display_name": "Contracts (manager, suppliers, services)",
        "retention_years": 7,
        "legal_basis": "Limitation Act 1985 (ACT) s.11 — 6 year contract limitation + 1 year buffer",
        "destruction_method": "Secure deletion / shredding",
        "notes": "Retain for 7 years after contract expiry.",
    },
    {
        "collection_name": "bylaw_records",
        "display_name": "By-laws and Rule Changes",
        "retention_years": 15,
        "legal_basis": "ACT UTMA 2011 — ongoing; SSMA 2015 s.180",
        "destruction_method": "Keep permanently or 15 years after repeal",
        "notes": "By-laws registered with ACT Land Titles — permanent retention recommended.",
    },
    {
        "collection_name": "whs_records",
        "display_name": "Work Health & Safety Records",
        "retention_years": 5,
        "legal_basis": "WHS Act 2011 (Cth); Safe Work Australia Codes of Practice",
        "destruction_method": "Secure deletion",
        "notes": "Injury records: 30 years if involves exposure to dangerous substances.",
    },
    {
        "collection_name": "privacy_consents",
        "display_name": "Privacy Consent Records",
        "retention_years": 7,
        "legal_basis": "Privacy Act 1988 APP 11; evidence of lawful processing",
        "destruction_method": "Secure deletion after 7 years from withdrawal or last activity",
        "notes": "Must be retained to demonstrate lawful consent-based collection.",
    },
    {
        "collection_name": "data_breach_logs",
        "display_name": "Data Breach Log (NDB scheme)",
        "retention_years": 7,
        "legal_basis": "Privacy Act 1988 Part IIIC; OAIC guidelines",
        "destruction_method": "Secure deletion",
        "notes": "OAIC may require access to breach records; retain 7 years from resolution.",
    },
    {
        "collection_name": "user_accounts",
        "display_name": "User Accounts (owners, tenants, staff)",
        "retention_years": 7,
        "legal_basis": "Privacy Act 1988 APP 11; ATO ID verification requirements",
        "destruction_method": "Anonymise or delete PII; retain audit trail for 7 years",
        "notes": "De-identify personal data when account is closed; retain audit records.",
    },
    {
        "collection_name": "audit_logs",
        "display_name": "System Audit Logs",
        "retention_years": 7,
        "legal_basis": "Privacy Act 1988 APP 11; ISO 27001 recommended practice",
        "destruction_method": "Secure deletion",
        "notes": "Required for security investigations and regulatory compliance.",
    },
]


# ─── Consent Endpoints ────────────────────────────────────────────────────────

@router.post("/consents", status_code=201)
async def record_consent(
        payload: ConsentRecord,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Record privacy consent from a user (APP 3 — collection of personal information)."""
    user = _require_ec(current_user)
    db = _get_db()

    if payload.consent_type not in CONSENT_TYPES:
        raise HTTPException(400, f"Invalid consent_type. Valid types: {CONSENT_TYPES}")

    doc = payload.model_dump()
    # Sentinel 🛡️: Force verified building context to prevent BOLA/IDOR spoofing.
    doc["building_id"] = building_id
    doc["given_at"] = doc.get("given_at") or _now_iso()
    doc["created_at"] = _now_iso()
    doc["updated_at"] = _now_iso()
    doc["recorded_by"] = user["id"]

    result = await db.privacy_consents.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    asyncio.create_task(create_audit_log(
        action="privacy_consent_recorded",
        user_id=user["id"],
        user_name=user.get("full_name", "Admin"),
        resource_type="privacy_consent",
        resource_id=doc["id"],
        details={
            "consent_type": payload.consent_type,
            "given": payload.given,
            "subject_user_id": payload.user_id,
        },
        building_id=building_id,
    ))

    return doc


@router.get("/consents")
async def list_consents(
        building_id: str = Depends(get_current_building),
        user_id: Optional[str] = Query(None),
        consent_type: Optional[str] = Query(None),
        given: Optional[bool] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
):
    """List privacy consent records (building-scoped, paginated)."""
    _require_admin(current_user)
    db = _get_db()

    query: Dict[str, Any] = {"building_id": building_id}
    if user_id:
        query["user_id"] = user_id
    if consent_type:
        query["consent_type"] = consent_type
    if given is not None:
        query["given"] = given

    skip = (page - 1) * page_size
    total = await db.privacy_consents.count_documents(query)
    cursor = db.privacy_consents.find(query).sort("given_at", -1).skip(skip).limit(page_size)

    consents = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        consents.append(doc)

    return {
        "data": consents,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


@router.put("/consents/{consent_id}")
async def update_consent(
        consent_id: str,
        building_id: str = Depends(get_current_building),
        payload: ConsentUpdate = ...,
        current_user: dict = Depends(get_current_user),
):
    """Update or withdraw a consent record (APP 3 — consent withdrawal)."""
    user = _require_ec(current_user)
    db = _get_db()

    try:
        oid = ObjectId(consent_id)
    except Exception:
        raise HTTPException(400, "Invalid consent ID format.")

    update = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not update:
        raise HTTPException(400, "No fields to update.")

    # If withdrawing consent, record timestamp
    if update.get("given") is False and "withdrawn_at" not in update:
        update["withdrawn_at"] = _now_iso()

    update["updated_at"] = _now_iso()
    update["updated_by"] = user["id"]

    result = await db.privacy_consents.update_one(
        {"_id": oid, "building_id": building_id},
        {"$set": update},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Consent record not found.")

    asyncio.create_task(create_audit_log(
        action="privacy_consent_updated",
        user_id=user["id"],
        user_name=user.get("full_name", "Admin"),
        resource_type="privacy_consent",
        resource_id=consent_id,
        details={"updated_fields": list(update.keys()), "withdrawn": update.get("given") is False},
        building_id=building_id,
    ))

    return {"message": "Consent updated.", "consent_id": consent_id}


# ─── Data Breach Log ──────────────────────────────────────────────────────────

@router.post("/breach-log", status_code=201)
async def log_data_breach(
        payload: DataBreachLog,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Log a data breach under the NDB scheme (Privacy Act 1988 Part IIIC).

    Eligible data breaches must be notified to OAIC and affected individuals
    as soon as practicable, and within 30 days of becoming aware.
    """
    user = _require_admin(current_user)
    db = _get_db()

    doc = payload.model_dump()
    # Sentinel 🛡️: Force verified building context to prevent BOLA/IDOR spoofing.
    doc["building_id"] = building_id
    doc["created_at"] = _now_iso()
    doc["updated_at"] = _now_iso()
    doc["created_by"] = user["id"]

    # NDB 30-day notification reminder
    if payload.severity == "eligible" and not payload.oaic_notified:
        doc["oaic_notification_due"] = True
        doc["ndb_note"] = (
            "ELIGIBLE DATA BREACH — You must notify the OAIC and affected individuals "
            "as soon as practicable (Privacy Act 1988 s.26WK). "
            "Failure to notify: civil penalty up to $50M."
        )

    result = await db.data_breach_log.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    asyncio.create_task(create_audit_log(
        action="data_breach_logged",
        user_id=user["id"],
        user_name=user.get("full_name", "Admin"),
        resource_type="data_breach",
        resource_id=doc["id"],
        details={
            "severity": payload.severity,
            "affected_count": payload.affected_count,
            "data_types": payload.data_types,
            "oaic_notified": payload.oaic_notified,
        },
        building_id=building_id,
    ))

    return doc


@router.get("/breach-log")
async def list_breach_log(
        building_id: str = Depends(get_current_building),
        severity: Optional[str] = Query(None, description="eligible | ineligible"),
        status: Optional[str] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
):
    """List data breach log entries (building-scoped, paginated)."""
    _require_admin(current_user)
    db = _get_db()

    query: Dict[str, Any] = {"building_id": building_id}
    if severity:
        query["severity"] = severity
    if status:
        query["status"] = status

    skip = (page - 1) * page_size
    total = await db.data_breach_log.count_documents(query)
    cursor = db.data_breach_log.find(query).sort("detected_at", -1).skip(skip).limit(page_size)

    entries = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        entries.append(doc)

    return {
        "data": entries,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


@router.put("/breach-log/{breach_id}")
async def update_breach_log(
        breach_id: str,
        building_id: str = Depends(get_current_building),
        payload: DataBreachUpdate = ...,
        current_user: dict = Depends(get_current_user),
):
    """Update a data breach log entry (e.g., mark OAIC notified, add remediation steps)."""
    user = _require_admin(current_user)
    db = _get_db()

    try:
        oid = ObjectId(breach_id)
    except Exception:
        raise HTTPException(400, "Invalid breach ID format.")

    update = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not update:
        raise HTTPException(400, "No fields to update.")

    update["updated_at"] = _now_iso()
    update["updated_by"] = user["id"]

    result = await db.data_breach_log.update_one(
        {"_id": oid, "building_id": building_id},
        {"$set": update},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Breach log entry not found.")

    asyncio.create_task(create_audit_log(
        action="data_breach_updated",
        user_id=user["id"],
        user_name=user.get("full_name", "Admin"),
        resource_type="data_breach",
        resource_id=breach_id,
        details={"updated_fields": list(update.keys())},
        building_id=building_id,
    ))

    return {"message": "Breach log updated.", "breach_id": breach_id}


# ─── Retention Schedule ───────────────────────────────────────────────────────

@router.get("/retention-schedule")
async def get_retention_schedule(
        current_user: dict = Depends(get_current_user),
):
    """Return the platform-wide data retention schedule.

    Retention periods are derived from:
    - ACT UTMA 2011 s.89 (7-year minimum for strata records)
    - Privacy Act 1988 APP 11 (security of personal information)
    - ATO record-keeping requirements (5–7 years)
    - WHS Act 2011 (5 years for safety records)
    - Limitation Act 1985 (ACT) (6 years for contract disputes)
    """
    _ = current_user  # auth check only

    return {
        "schedule": RETENTION_SCHEDULE,
        "default_minimum_years": 7,
        "legal_basis_summary": (
            "ACT UTMA 2011 s.89 mandates 7-year retention for all owners corporation records. "
            "Privacy Act 1988 APP 11 requires reasonable security measures. "
            "Records must be destroyed securely when retention periods expire."
        ),
        "generated_at": _now_iso(),
    }


# ─── Access / Correction Requests ─────────────────────────────────────────────

@router.post("/access-request", status_code=201)
async def create_access_request(
        payload: AccessRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Record an access or correction request (APP 12/13).

    Under APP 12, individuals have the right to access personal information held
    about them. Under APP 13, they may request corrections. Requests must be
    responded to within 30 days (APP 12.5).
    """
    user = _require_ec(current_user)
    db = _get_db()

    if payload.request_type not in ACCESS_REQUEST_TYPES:
        raise HTTPException(400, f"Invalid request_type. Valid types: {ACCESS_REQUEST_TYPES}")

    doc = payload.model_dump()
    # Sentinel 🛡️: Force verified building context to prevent BOLA/IDOR spoofing.
    doc["building_id"] = building_id
    doc["received_at"] = doc.get("received_at") or _now_iso()
    doc["created_at"] = _now_iso()
    doc["updated_at"] = _now_iso()
    doc["created_by"] = user["id"]

    # APP 12.5 — 30 day response deadline
    if not doc.get("due_at"):
        from datetime import timedelta
        received = datetime.fromisoformat(doc["received_at"].replace("Z", "+00:00"))
        doc["due_at"] = (received + timedelta(days=30)).isoformat()

    doc["app_note"] = (
        "APP 12.5 — Response required within 30 days of receiving request. "
        "If refusing access, provide written reasons and information about complaint avenues."
    )

    result = await db.privacy_access_requests.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    asyncio.create_task(create_audit_log(
        action="privacy_access_request_created",
        user_id=user["id"],
        user_name=user.get("full_name", "Admin"),
        resource_type="privacy_access_request",
        resource_id=doc["id"],
        details={
            "request_type": payload.request_type,
            "requestor_name": payload.requestor_name,
            "status": payload.status,
        },
        building_id=building_id,
    ))

    return doc


@router.get("/my-data")
async def get_my_data(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Return all personal data held about the authenticated user (APP 12 — right of access).

    Aggregates data from every collection that stores personal information and returns
    it as a single portable JSON export.  Sensitive auth fields (password_hash, totp_secret)
    are always excluded.
    """
    db = _get_db()
    uid = current_user["id"]

    # ── Helper: drop MongoDB _id; preserve any existing business id field ────
    def _clean(docs: list) -> list:
        """Generated function header.

        Function: _clean
        Path: backend/routers/privacy_compliance.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        out = []
        for d in docs:
            d.pop("_id", None)
            out.append(d)
        return out

    # Step 1: fetch user + unit memberships (unit_numbers needed for levy_payments query).
    # Sentinel 🛡️: building_id on every scoped collection prevents cross-building leakage.
    user_doc, raw_units = await asyncio.gather(
        db.users.find_one({"id": uid}, {"password_hash": 0, "totp_secret": 0}),
        db.user_units.find({"user_id": uid, "building_id": building_id}).to_list(100),
    )

    unit_numbers = [u["unit_number"] for u in raw_units if u.get("unit_number")]

    # Step 2: run remaining 5 queries concurrently — levy_payments queried by unit_number.
    (
        raw_levy,
        raw_announcements,
        raw_consents,
        raw_access_reqs,
        raw_audit,
    ) = await asyncio.gather(
        db.levy_payments.find(
            {"unit_number": {"$in": unit_numbers}, "building_id": building_id}
        ).sort("created_at", -1).limit(200).to_list(200),
        db.announcements.find({"created_by": uid, "building_id": building_id}).sort("created_at", -1).limit(50).to_list(
            50),
        db.privacy_consents.find({"user_id": uid, "building_id": building_id}).sort("given_at", -1).to_list(100),
        db.privacy_access_requests.find({"requestor_user_id": uid, "building_id": building_id}).sort("received_at",
                                                                                                     -1).to_list(100),
        db.audit_logs.find({"user_id": uid, "building_id": building_id}).sort("created_at", -1).limit(100).to_list(100),
    )

    if user_doc:
        user_doc.pop("_id", None)  # drop ObjectId; preserve the business id field
    users_data = [user_doc] if user_doc else []
    user_units_data = _clean(raw_units)
    levy_txns_data = _clean(raw_levy)
    announcements_data = _clean(raw_announcements)
    consents_data = _clean(raw_consents)
    access_requests_data = _clean(raw_access_reqs)
    audit_logs_data = _clean(raw_audit)

    # ── Audit the export itself ────────────────────────────────────────────────
    asyncio.create_task(create_audit_log(
        action="privacy_my_data_export",
        user_id=uid,
        user_name=current_user.get("full_name", "User"),
        resource_type="privacy_data_export",
        resource_id=uid,
        details={
            "collections_queried": [
                "users", "user_units", "levy_payments",
                "announcements", "privacy_consents",
                "privacy_access_requests", "audit_logs",
            ],
            "record_counts": {
                "users": len(users_data),
                "user_units": len(user_units_data),
                "levy_payments": len(levy_txns_data),
                "announcements": len(announcements_data),
                "privacy_consents": len(consents_data),
                "privacy_access_requests": len(access_requests_data),
                "audit_logs": len(audit_logs_data),
            },
        },
        building_id=building_id,
    ))

    return {
        "user_id": uid,
        "building_id": building_id,
        "generated_at": get_current_utc_iso(),
        "collections": {
            "users": users_data,
            "user_units": user_units_data,
            "levy_payments": levy_txns_data,
            "announcements": announcements_data,
            "privacy_consents": consents_data,
            "privacy_access_requests": access_requests_data,
            "audit_logs": audit_logs_data,
        },
    }


@router.get("/access-requests")
async def list_access_requests(
        building_id: str = Depends(get_current_building),
        request_type: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
):
    """List access/correction requests (building-scoped, paginated)."""
    _require_admin(current_user)
    db = _get_db()

    query: Dict[str, Any] = {"building_id": building_id}
    if request_type:
        query["request_type"] = request_type
    if status:
        query["status"] = status

    skip = (page - 1) * page_size
    total = await db.privacy_access_requests.count_documents(query)
    cursor = db.privacy_access_requests.find(query).sort("received_at", -1).skip(skip).limit(page_size)

    requests = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        requests.append(doc)

    return {
        "data": requests,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }
