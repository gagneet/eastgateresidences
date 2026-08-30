"""
Work Health & Safety (WHS) Compliance Router

WHS Act 2011 (Cth) compliance endpoints for contractor management
and safety incident tracking within the strata scheme.

Endpoints:
  POST  /whs/inductions         — record contractor site induction
  GET   /whs/inductions         — list inductions (building-scoped, paginated)
  PUT   /whs/inductions/{id}    — update / renew induction
  POST  /whs/swms               — create SWMS (Safe Work Method Statement) record
  GET   /whs/swms               — list SWMS records
  PUT   /whs/swms/{id}          — update SWMS
  POST  /whs/incidents          — log safety incident / near-miss
  GET   /whs/incidents          — list incidents (building-scoped, paginated)
  GET   /whs/dashboard          — WHS compliance summary dashboard

Legislation:
  - WHS Act 2011 (Cth) — primary duties of care (ss.17–29)
  - WHS Regulation 2017 — SWMS requirement for high-risk construction (r.291)
  - Safe Work Australia Code of Practice: Construction Work
  - Model WHS Regulations — notifiable incidents (s.35–38)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import asyncio
import logging
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, constr
from typing import Optional, List, Any, Dict

from models.user import UserRole
from utils.auth import get_current_user, get_current_building
from utils.helpers import create_audit_log
from utils.route_guards import OPERATIONAL_MANAGEMENT, assert_roles, require_manager_surface

logger = logging.getLogger(__name__)
# Function scoping (2026-08-28). Router-level, so every route in this file is
# covered including the reads — Privacy Act APP 6 limits USE to the purpose of
# collection, and looking is a use. This narrows nobody until an agency sets
# core.management_entities.function_scoping_enabled; see
# docs/architecture/strata_management_staff_access_model.md.
router = APIRouter(
    prefix="/whs", tags=["WHS Compliance"],
    dependencies=[Depends(require_manager_surface("whs"))],
)

INDUCTION_EXPIRY_DAYS = 365  # default: 1 year

SHORT_STR_200 = constr(max_length=200)  # Limit list size AND item size


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/whs.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/whs.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_whs(user: dict) -> dict:
    """Who may record inductions, SWMS and safety incidents.

    Built on utils.route_guards so the role set is checked against UserRole and the
    role is resolved through effective_role(), rather than restating both here.

    Same two dead strings as compliance_registers._require_manager, resolved the same
    way: admin_staff is restored (induction and SWMS upkeep is records administration,
    and every sibling register already admits them); "maintenance" is not, because the
    nearest real role is service_provider — the contractor whose own induction and SWMS
    these records are. A contractor may be the SUBJECT of a WHS record; they are not a
    keeper of the register.
    """
    return assert_roles(
        user,
        OPERATIONAL_MANAGEMENT | {UserRole.ADMIN_STAFF},
        detail="WHS manager or EC access required.",
    )


def _induction_status(expiry_date: str) -> str:
    """Generated function header.

    Function: _induction_status
    Path: backend/routers/whs.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        exp = date.fromisoformat(expiry_date[:10])
        delta = (exp - date.today()).days
        if delta < 0:
            return "expired"
        if delta <= 30:
            return "pending"
        return "current"
    except (ValueError, TypeError):
        return "unknown"


# ─── Models ───────────────────────────────────────────────────────────────────

INDUCTION_TYPES = [
    "general_site", "hot_works", "confined_space", "working_at_height",
    "electrical", "asbestos_awareness", "manual_handling",
]

INCIDENT_TYPES = ["near_miss", "injury", "property_damage", "environmental", "dangerous_incident"]

SWMS_STATUSES = ["draft", "reviewed", "approved", "expired", "superseded"]
INVESTIGATION_STATUSES = ["open", "under_investigation", "closed", "referred_to_regulator"]


class ContractorInductionCreate(BaseModel):
    """WHS contractor site induction record."""
    model_config = ConfigDict(extra="ignore")

    building_id: str
    contractor_name: str
    company_name: Optional[str] = None
    abn: Optional[str] = None
    induction_date: str = Field(..., description="ISO-8601 date")
    expiry_date: Optional[str] = Field(None, description="ISO-8601 date — defaults to 1 year from induction_date")
    induction_type: str = Field("general_site", description=f"One of: {INDUCTION_TYPES}")
    trained_by: Optional[str] = None
    training_provider: Optional[str] = None
    site_rules_acknowledged: bool = True
    ppe_requirements: List[str] = Field(
        default_factory=list,
        description="PPE required on site (e.g. hard hat, hi-vis, steel cap boots)",
    )
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    licence_number: Optional[str] = None
    licence_expiry: Optional[str] = None
    insurance_confirmed: bool = False
    notes: Optional[str] = None


class ContractorInductionUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    induction_date: Optional[str] = None
    expiry_date: Optional[str] = None
    induction_type: Optional[str] = None
    trained_by: Optional[str] = None
    site_rules_acknowledged: Optional[bool] = None
    ppe_requirements: Optional[List[str]] = None
    licence_number: Optional[str] = None
    licence_expiry: Optional[str] = None
    insurance_confirmed: Optional[bool] = None
    notes: Optional[str] = None


class SWMSCreate(BaseModel):
    """Safe Work Method Statement (WHS Regulation 2017 r.291)."""
    model_config = ConfigDict(extra="ignore")

    building_id: str
    contractor_name: str
    company_name: Optional[str] = None
    work_description: str = Field(..., min_length=10)
    high_risk_work: bool = Field(
        False,
        description="True if this is high-risk construction work (SWMS mandatory under r.291)",
    )
    work_start_date: str = Field(..., description="ISO-8601 date")
    work_end_date: Optional[str] = None
    work_location: Optional[str] = None
    hazards_identified: List[str] = Field(default_factory=list)
    control_measures: List[str] = Field(default_factory=list)
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    status: str = Field("draft", pattern="^(draft|reviewed|approved|expired|superseded)$")
    document_url: Optional[str] = None
    notes: Optional[str] = None


class SWMSUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    work_description: Optional[str] = None
    high_risk_work: Optional[bool] = None
    work_start_date: Optional[str] = None
    work_end_date: Optional[str] = None
    hazards_identified: Optional[List[str]] = None
    control_measures: Optional[List[str]] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    status: Optional[str] = None
    document_url: Optional[str] = None
    notes: Optional[str] = None


class WHSIncidentCreate(BaseModel):
    """WHS safety incident / near-miss log (WHS Act 2011 ss.35–38)."""
    model_config = ConfigDict(extra="ignore")

    building_id: str = Field(..., max_length=50)
    incident_date: str = Field(..., max_length=100, description="ISO-8601 datetime of incident")
    incident_type: str = Field(..., max_length=50, description=f"One of: {INCIDENT_TYPES}")
    description: str = Field(..., min_length=10, max_length=5000)
    location: Optional[str] = Field(None, max_length=200, description="Specific location on site")
    injured_party: Optional[str] = Field(None, max_length=200)
    injured_party_type: Optional[str] = Field(
        None,
        max_length=50,
        description="owner | tenant | contractor | visitor | employee",
    )
    first_aid_given: bool = False
    first_aider_name: Optional[str] = Field(None, max_length=200)
    medical_treatment_required: bool = False
    notified_authority: bool = False
    notified_authority_name: Optional[str] = Field(
        None,
        max_length=200,
        description="e.g. WorkSafe ACT, SafeWork NSW — required for notifiable incidents",
    )
    notified_authority_at: Optional[str] = Field(None, max_length=100)
    notified_authority_reference: Optional[str] = Field(None, max_length=100)
    investigation_status: str = Field("open", max_length=50, description=f"One of: {INVESTIGATION_STATUSES}")
    corrective_actions: List[str] = Field(default_factory=list, max_length=50)
    witness_names: List[str] = Field(default_factory=list, max_length=50)
    photos_url: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = Field(None, max_length=2000)


class WHSIncidentUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    investigation_status: Optional[str] = Field(None, max_length=50)
    corrective_actions: Optional[List[str]] = Field(None, max_length=50)
    notified_authority: Optional[bool] = None
    notified_authority_name: Optional[str] = Field(None, max_length=200)
    notified_authority_at: Optional[str] = Field(None, max_length=100)
    notified_authority_reference: Optional[str] = Field(None, max_length=100)
    first_aid_given: Optional[bool] = None
    medical_treatment_required: Optional[bool] = None
    photos_url: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = Field(None, max_length=2000)


# ─── Contractor Inductions ────────────────────────────────────────────────────

@router.post("/inductions", status_code=201)
async def create_induction(
        payload: ContractorInductionCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Record a contractor site induction (WHS Act 2011 s.19 — duty to ensure safe workplace)."""
    user = _require_whs(current_user)
    db = _get_db()

    if payload.induction_type not in INDUCTION_TYPES:
        raise HTTPException(400, f"Invalid induction_type. Valid types: {INDUCTION_TYPES}")

    doc = payload.model_dump()
    doc["building_id"] = building_id

    # Default expiry to 1 year from induction date
    if not doc.get("expiry_date"):
        try:
            induction_dt = date.fromisoformat(payload.induction_date[:10])
            doc["expiry_date"] = (induction_dt + timedelta(days=INDUCTION_EXPIRY_DAYS)).isoformat()
        except (ValueError, TypeError):
            doc["expiry_date"] = None

    doc["status"] = _induction_status(doc.get("expiry_date", ""))
    doc["created_at"] = _now_iso()
    doc["updated_at"] = _now_iso()
    doc["created_by"] = user["id"]

    result = await db.whs_inductions.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    asyncio.create_task(create_audit_log(
        action="whs_induction_created",
        resource_type="whs_induction",
        resource_id=doc["id"],
        user_id=user["id"],
        user_name=user.get("full_name", "Admin"),
        details={
            "contractor_name": payload.contractor_name,
            "induction_type": payload.induction_type,
            "induction_date": payload.induction_date,
        },
        building_id=building_id,
    ))

    return doc


@router.get("/inductions")
async def list_inductions(
        building_id: str = Depends(get_current_building),
        status: Optional[str] = Query(None, description="current | expired | pending"),
        induction_type: Optional[str] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
):
    """List contractor inductions (building-scoped, paginated)."""
    _require_whs(current_user)
    db = _get_db()

    query: Dict[str, Any] = {"building_id": building_id}
    if induction_type:
        query["induction_type"] = induction_type

    skip = (page - 1) * page_size
    total_raw = await db.whs_inductions.count_documents(query)
    cursor = db.whs_inductions.find(query).sort("induction_date", -1).skip(skip).limit(page_size)

    inductions = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        doc["status"] = _induction_status(doc.get("expiry_date", ""))
        if not status or doc["status"] == status:
            inductions.append(doc)

    # Recalculate total when status filter applied client-side
    total = total_raw if not status else len(inductions)

    return {
        "data": inductions,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total_raw + page_size - 1) // page_size,
    }


@router.put("/inductions/{induction_id}")
async def update_induction(
        induction_id: str,
        building_id: str = Depends(get_current_building),
        payload: ContractorInductionUpdate = ...,
        current_user: dict = Depends(get_current_user),
):
    """Update or renew a contractor induction."""
    user = _require_whs(current_user)
    db = _get_db()

    try:
        oid = ObjectId(induction_id)
    except Exception:
        raise HTTPException(400, "Invalid induction ID format.")

    update = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not update:
        raise HTTPException(400, "No fields to update.")

    # Recalculate status if expiry_date is updated
    if "expiry_date" in update:
        update["status"] = _induction_status(update["expiry_date"])

    update["updated_at"] = _now_iso()
    update["updated_by"] = user["id"]

    result = await db.whs_inductions.update_one(
        {"_id": oid, "building_id": building_id},
        {"$set": update},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Induction record not found.")

    asyncio.create_task(create_audit_log(
        action="whs_induction_updated",
        resource_type="whs_induction",
        resource_id=induction_id,
        user_id=user["id"],
        user_name=user.get("full_name", "Admin"),
        details={"updated_fields": list(update.keys())},
        building_id=building_id,
    ))

    return {"message": "Induction updated.", "induction_id": induction_id}


# ─── SWMS ─────────────────────────────────────────────────────────────────────

@router.post("/swms", status_code=201)
async def create_swms(
        payload: SWMSCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Create a SWMS record (WHS Regulation 2017 r.291 — required for high-risk construction work)."""
    user = _require_whs(current_user)
    db = _get_db()

    doc = payload.model_dump()
    doc["building_id"] = building_id
    doc["created_at"] = _now_iso()
    doc["updated_at"] = _now_iso()
    doc["created_by"] = user["id"]

    if payload.high_risk_work:
        doc["high_risk_note"] = (
            "HIGH-RISK CONSTRUCTION WORK — A SWMS must be prepared before work commences "
            "(WHS Regulation 2017 r.291). The principal contractor must review and keep the SWMS."
        )

    result = await db.whs_swms.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    asyncio.create_task(create_audit_log(
        action="whs_swms_created",
        resource_type="whs_swms",
        resource_id=doc["id"],
        user_id=user["id"],
        user_name=user.get("full_name", "Admin"),
        details={
            "contractor_name": payload.contractor_name,
            "high_risk_work": payload.high_risk_work,
            "work_start_date": payload.work_start_date,
            "status": payload.status,
        },
        building_id=building_id,
    ))

    return doc


@router.get("/swms")
async def list_swms(
        building_id: str = Depends(get_current_building),
        status: Optional[str] = Query(None),
        high_risk_only: bool = Query(False),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
):
    """List SWMS records (building-scoped, paginated)."""
    _require_whs(current_user)
    db = _get_db()

    query: Dict[str, Any] = {"building_id": building_id}
    if status:
        query["status"] = status
    if high_risk_only:
        query["high_risk_work"] = True

    skip = (page - 1) * page_size
    total = await db.whs_swms.count_documents(query)
    cursor = db.whs_swms.find(query).sort("work_start_date", -1).skip(skip).limit(page_size)

    swms_list = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        swms_list.append(doc)

    return {
        "data": swms_list,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


@router.put("/swms/{swms_id}")
async def update_swms(
        swms_id: str,
        building_id: str = Depends(get_current_building),
        payload: SWMSUpdate = ...,
        current_user: dict = Depends(get_current_user),
):
    """Update a SWMS record (e.g., approve, mark superseded)."""
    user = _require_whs(current_user)
    db = _get_db()

    try:
        oid = ObjectId(swms_id)
    except Exception:
        raise HTTPException(400, "Invalid SWMS ID format.")

    update = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not update:
        raise HTTPException(400, "No fields to update.")

    update["updated_at"] = _now_iso()
    update["updated_by"] = user["id"]

    result = await db.whs_swms.update_one(
        {"_id": oid, "building_id": building_id},
        {"$set": update},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "SWMS record not found.")

    asyncio.create_task(create_audit_log(
        action="whs_swms_updated",
        resource_type="whs_swms",
        resource_id=swms_id,
        user_id=user["id"],
        user_name=user.get("full_name", "Admin"),
        details={"updated_fields": list(update.keys())},
        building_id=building_id,
    ))

    return {"message": "SWMS updated.", "swms_id": swms_id}


# ─── Safety Incidents ─────────────────────────────────────────────────────────

@router.post("/incidents", status_code=201)
async def log_incident(
        payload: WHSIncidentCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Log a safety incident or near-miss (WHS Act 2011 ss.35–38 — notifiable incidents).

    Notifiable incidents (death, serious injury, dangerous incident) must be
    reported to the WHS regulator immediately by the fastest means possible,
    then in writing within 48 hours.
    """
    user = _require_whs(current_user)
    db = _get_db()

    if payload.incident_type not in INCIDENT_TYPES:
        raise HTTPException(400, f"Invalid incident_type. Valid types: {INCIDENT_TYPES}")

    doc = payload.model_dump()
    doc["building_id"] = building_id
    doc["created_at"] = _now_iso()
    doc["updated_at"] = _now_iso()
    doc["created_by"] = user["id"]

    if payload.incident_type == "dangerous_incident" and not payload.notified_authority:
        doc["authority_notification_required"] = True
        doc["notification_note"] = (
            "NOTIFIABLE INCIDENT — Must be reported to the WHS regulator immediately "
            "(WHS Act 2011 s.38). Preserve the incident site until inspector attends or gives clearance."
        )

    result = await db.whs_incidents.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    asyncio.create_task(create_audit_log(
        action="whs_incident_logged",
        resource_type="whs_incident",
        resource_id=doc["id"],
        user_id=user["id"],
        user_name=user.get("full_name", "Admin"),
        details={
            "incident_type": payload.incident_type,
            "incident_date": payload.incident_date,
            "notified_authority": payload.notified_authority,
        },
        building_id=building_id,
    ))

    return doc


@router.get("/incidents")
async def list_incidents(
        building_id: str = Depends(get_current_building),
        incident_type: Optional[str] = Query(None),
        investigation_status: Optional[str] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
):
    """List WHS incidents (building-scoped, paginated)."""
    _require_whs(current_user)
    db = _get_db()

    query: Dict[str, Any] = {"building_id": building_id}
    if incident_type:
        query["incident_type"] = incident_type
    if investigation_status:
        query["investigation_status"] = investigation_status

    skip = (page - 1) * page_size
    total = await db.whs_incidents.count_documents(query)
    cursor = db.whs_incidents.find(query).sort("incident_date", -1).skip(skip).limit(page_size)

    incidents = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        incidents.append(doc)

    return {
        "data": incidents,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


# ─── WHS Dashboard ────────────────────────────────────────────────────────────

@router.get("/dashboard")
async def get_whs_dashboard(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Return WHS compliance summary for a building.

    Performance Optimization⚡: Consolidated 8 sequential database round-trips and
    an O(N) manual loop into parallelized aggregation pipelines using $facet.
    Includes:
    - Induction counts by status (current / expired / pending)
    - SWMS counts by status
    - Open incidents requiring investigation
    - Expiring inductions in next 30 days
    - High-risk work without approved SWMS

    Performance Optimization⚡: Consolidated 9 database round-trips into 3 parallelized
    aggregation pipelines using $facet and $group to reduce dashboard latency.
    """
    _require_whs(current_user)
    db = _get_db()

    # Performance Optimization⚡: Consolidated 8 sequential DB calls and O(N) loop into 3 parallel aggregations.
    today = date.today()
    today_str = today.isoformat()
    threshold_str = (today + timedelta(days=30)).isoformat()

    # 1. Inductions Pipeline: handles status counts and 'expiring soon' items in one round-trip.
    induction_pipeline = [
        {"$match": {"building_id": building_id}},
        {"$facet": {
            "counts": [
                {"$addFields": {
                    "expiry_prefix": {"$substr": [{"$ifNull": ["$expiry_date", ""]}, 0, 10]}
                }},
                {"$group": {
                    "_id": None,
                    "total": {"$sum": 1},
                    "expired": {"$sum": {"$cond": [
                        {"$and": [{"$ne": ["$expiry_prefix", ""]}, {"$lt": ["$expiry_prefix", today_str]}]}, 1, 0
                    ]}},
                    "current": {"$sum": {"$cond": [
                        {"$and": [{"$ne": ["$expiry_prefix", ""]}, {"$gt": ["$expiry_prefix", threshold_str]}]}, 1, 0
                    ]}}
                }}
            ],
            "expiring_soon": [
                {"$addFields": {
                    "expiry_prefix": {"$substr": [{"$ifNull": ["$expiry_date", ""]}, 0, 10]}
                }},
                {"$match": {"$and": [{"expiry_prefix": {"$ne": ""}}, {"expiry_prefix": {"$gte": today_str}},
                                     {"expiry_prefix": {"$lte": threshold_str}}]}},
                {"$project": {"id": {"$toString": "$_id"}, "contractor_name": 1, "expiry_date": 1, "expiry_prefix": 1}}
            ]
        }}
    ]

    # 2. SWMS Pipeline: consolidates multiple status counts.
    swms_pipeline = [
        {"$match": {"building_id": building_id}},
        {"$facet": {
            "total": [{"$count": "count"}],
            "draft": [{"$match": {"status": "draft"}}, {"$count": "count"}],
            "approved": [{"$match": {"status": "approved"}}, {"$count": "count"}],
            "high_risk_unapproved": [
                {"$match": {"high_risk_work": True, "status": {"$ne": "approved"}}}, {"$count": "count"}
            ]
        }}
    ]

    # 3. Incident Pipeline: consolidates investigative and authority notification counts.
    incidents_pipeline = [
        {"$match": {"building_id": building_id}},
        {"$facet": {
            "total": [{"$count": "count"}],
            "open": [{"$match": {"investigation_status": "open"}}, {"$count": "count"}],
            "authority_pending": [
                {"$match": {"authority_notification_required": True, "notified_authority": False}}, {"$count": "count"}
            ]
        }}
    ]

    # Execute all three aggregations in parallel to minimize cumulative IO wait.
    results = await asyncio.gather(
        db.whs_inductions.aggregate(induction_pipeline).to_list(1),
        db.whs_swms.aggregate(swms_pipeline).to_list(1),
        db.whs_incidents.aggregate(incidents_pipeline).to_list(1)
    )

    ind_data = results[0][0] if results[0] else {}
    swms_data = results[1][0] if results[1] else {}
    inc_data = results[2][0] if results[2] else {}

    # Extract induction results
    ind_counts = ind_data.get("counts", [{}])[0] if ind_data.get("counts") else {}
    induction_total = ind_counts.get("total", 0)
    induction_expired_count = ind_counts.get("expired", 0)
    induction_current_count = ind_counts.get("current", 0)

    induction_expiring_soon = []
    for item in ind_data.get("expiring_soon", []):
        try:
            exp_date = date.fromisoformat(item["expiry_prefix"])
            induction_expiring_soon.append({
                "id": item["id"],
                "contractor_name": item.get("contractor_name"),
                "expiry_date": item.get("expiry_date"),
                "days_until_expiry": (exp_date - today).days
            })
        except (ValueError, KeyError):
            pass

    # Extract SWMS results
    swms_total = swms_data.get("total", [{}])[0].get("count", 0) if swms_data.get("total") else 0
    swms_draft = swms_data.get("draft", [{}])[0].get("count", 0) if swms_data.get("draft") else 0
    swms_approved = swms_data.get("approved", [{}])[0].get("count", 0) if swms_data.get("approved") else 0
    high_risk_unapproved = (
        swms_data.get("high_risk_unapproved", [{}])[0].get("count", 0) if swms_data.get("high_risk_unapproved") else 0
    )

    # Extract Incident results
    incidents_total = inc_data.get("total", [{}])[0].get("count", 0) if inc_data.get("total") else 0
    incidents_open = inc_data.get("open", [{}])[0].get("count", 0) if inc_data.get("open") else 0
    incidents_authority_pending = (
        inc_data.get("authority_pending", [{}])[0].get("count", 0) if inc_data.get("authority_pending") else 0
    )

    # Construct issues
    issues = []
    if induction_expired_count > 0:
        issues.append({
            "severity": "warning",
            "message": f"{induction_expired_count} inductions have expired.",
        })
    if high_risk_unapproved > 0:
        issues.append({
            "severity": "critical",
            "message": f"{high_risk_unapproved} high-risk SWMS unapproved.",
        })
    if incidents_authority_pending > 0:
        issues.append({
            "severity": "critical",
            "message": f"{incidents_authority_pending} incidents require notification.",
        })
    if incidents_open > 0:
        issues.append({
            "severity": "warning",
            "message": f"{incidents_open} investigations still open.",
        })

    is_compliant = len([i for i in issues if i["severity"] == "critical"]) == 0

    return {
        "building_id": building_id,
        "as_at": _now_iso(),
        "compliant": is_compliant,
        "inductions": {
            "total": induction_total,
            "current": induction_current_count,
            "expired": induction_expired_count,
            "pending_renewal": max(0, induction_total - induction_current_count - induction_expired_count),
            "expiring_in_30_days": induction_expiring_soon,
        },
        "swms": {
            "total": swms_total,
            "draft": swms_draft,
            "approved": swms_approved,
            "high_risk_unapproved": high_risk_unapproved,
        },
        "incidents": {
            "total": incidents_total,
            "open": incidents_open,
            "authority_notification_pending": incidents_authority_pending,
        },
        "issues": issues,
    }
