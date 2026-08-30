# @featuretrace:nsw-compliance — NSW SSMA 2015 compliance: 6-monthly reports, Strata Hub returns, commission disclosures.
# Layer: router
# Data flow: NSWCompliancePage.jsx → GET/POST /nsw/reports + /nsw/commission-disclosures + /nsw/strata-hub-returns → db.nsw_reports + db.nsw_commission_disclosures + db.nsw_strata_hub_returns (building-scoped)
# Related: backend/cron/cron_compliance_calendar.py
#           backend/domain/jurisdictional_rules.py
#           frontend/public/tech-docs/nsw-compliance.html
#           tests/backend/test_nsw_compliance.py
# Toggle: nsw_compliance
# Collection: nsw_reports, nsw_commission_disclosures, nsw_strata_hub_returns
# Table: (planned Postgres migration for 10-yr retention enforcement)
"""
NSW Compliance Router

Strata Schemes Management Act 2015 (NSW) compliance endpoints.

Endpoints:
  POST  /nsw/reports                          — create 6-monthly strata manager report
  GET   /nsw/reports                          — list reports (building-scoped, paginated)
  GET   /nsw/reports/{id}                     — get single report
  PUT   /nsw/reports/{id}                     — update report
  GET   /nsw/strata-hub-export                — generate Strata Hub annual export data
  POST  /nsw/strata-hub-returns               — record Strata Hub annual return submission
  GET   /nsw/strata-hub-returns               — list annual return submissions
  GET   /nsw/strata-hub-returns/{id}          — get single return record
  GET   /nsw/levy-notice-hardship             — mandatory hardship statement for levy notices
  GET   /nsw/cwf-schedule.pdf                 — 10-year Capital Works Fund schedule (PDF)
  GET   /nsw/s184-certificate.pdf             — s.184 certificate for lot (PDF)
  POST  /nsw/commission-disclosures           — record insurance commission disclosure (SSMA 2015 s.264)
  GET   /nsw/commission-disclosures           — list commission disclosures (building-scoped)
  GET   /nsw/commission-disclosures/{id}      — get single disclosure record
  PUT   /nsw/commission-disclosures/{id}      — update disclosure record
  DELETE /nsw/commission-disclosures/{id}     — soft-delete (cancelled_at)

Legislation:
  - SSMA 2015 s.50   — strata manager functions disclosure
  - SSMA 2015 s.58   — 6-monthly report to owners corporation
  - SSMA 2015 s.182  — Strata Hub annual reporting requirements
  - SSMA 2015 s.264  — insurance commission/gift/rebate disclosure (penalty: $110k)
  - NSW Fair Trading — Debt Helpline hardship notice
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Any, Dict

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from utils.auth import get_current_user, get_current_building, effective_role
from utils.helpers import create_audit_log
from utils.permissions import require_feature

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/nsw", tags=["NSW Compliance"])


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/nsw_compliance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/nsw_compliance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db

    return db


def _require_manager(user: dict) -> dict:
    # "chairman" is not a top-level role (removed migration 0025); effective_role()
    # returns "ec_member" for a chairman. "admin" is not a valid role name.
    """Generated function header.

    Function: _require_manager
    Path: backend/routers/nsw_compliance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    allowed = {"super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"}
    if effective_role(user) not in allowed:
        raise HTTPException(403, "Strata manager or EC access required.")
    return user


# ─── Models ───────────────────────────────────────────────────────────────────


class FinancialSummary(BaseModel):
    opening_balance: float = 0.0
    total_levies_collected: float = 0.0
    total_expenditure: float = 0.0
    closing_balance: float = 0.0
    outstanding_levies: float = 0.0
    notes: Optional[str] = None


class NSWReportCreate(BaseModel):
    """6-monthly strata manager report — SSMA 2015 s.58."""

    model_config = ConfigDict(extra="ignore")

    building_id: str
    report_period_from: str = Field(
        ..., description="ISO-8601 date — start of reporting period"
    )
    report_period_to: str = Field(
        ..., description="ISO-8601 date — end of reporting period"
    )
    report_type: str = Field("6_monthly", pattern="^(6_monthly|annual)$")

    functions_performed: List[str] = Field(
        default_factory=list,
        description="List of strata manager functions performed during period (SSMA 2015 s.50)",
    )
    financial_summary: FinancialSummary = Field(default_factory=FinancialSummary)
    maintenance_completed: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Maintenance works completed during period",
    )
    meetings_held: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Meetings held during period (type, date, quorum, resolutions count)",
    )
    strata_hub_submitted: bool = False
    strata_hub_submitted_at: Optional[str] = None
    status: str = Field("draft", pattern="^(draft|submitted)$")
    notes: Optional[str] = None


class NSWReportUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    functions_performed: Optional[List[str]] = None
    financial_summary: Optional[FinancialSummary] = None
    maintenance_completed: Optional[List[Dict[str, Any]]] = None
    meetings_held: Optional[List[Dict[str, Any]]] = None
    strata_hub_submitted: Optional[bool] = None
    strata_hub_submitted_at: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


# ─── NSW 6-Monthly Reports ─────────────────────────────────────────────────────


@router.post("/reports", status_code=201)
async def create_nsw_report(
        payload: NSWReportCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Create a 6-monthly strata manager report (SSMA 2015 s.58).

    Strata managers must provide this report to the owners corporation within
    4 weeks before or at each AGM, or within 14 days of request.
    """
    _require_manager(current_user)
    db = _get_db()

    doc = payload.model_dump()
    # Sentinel 🛡️: Force verified building context to prevent BOLA/IDOR spoofing.
    doc["building_id"] = building_id
    doc["financial_summary"] = payload.financial_summary.model_dump()
    doc["created_at"] = _now_iso()
    doc["updated_at"] = _now_iso()
    doc["created_by"] = current_user["id"]

    result = await db.nsw_reports.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    await create_audit_log(
        action="nsw_report_created",
        user_id=current_user["id"],
        user_name=current_user.get("full_name", "Strata Manager"),
        resource_type="nsw_report",
        resource_id=doc["id"],
        details={
            "report_type": payload.report_type,
            "period_from": payload.report_period_from,
            "period_to": payload.report_period_to,
            "status": payload.status,
        },
        building_id=building_id,
    )

    return doc


@router.get("/reports")
async def list_nsw_reports(
        building_id: str = Depends(get_current_building),
        report_type: Optional[str] = Query(None, description="Filter: 6_monthly | annual"),
        status: Optional[str] = Query(None, description="Filter: draft | submitted"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
):
    """List NSW 6-monthly reports for a building (building-scoped, paginated)."""
    _require_manager(current_user)
    db = _get_db()

    query: Dict[str, Any] = {"building_id": building_id}
    if report_type:
        query["report_type"] = report_type
    if status:
        query["status"] = status

    skip = (page - 1) * page_size
    total = await db.nsw_reports.count_documents(query)
    cursor = (
        db.nsw_reports.find(query)
        .sort("report_period_from", -1)
        .skip(skip)
        .limit(page_size)
    )

    reports = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        reports.append(doc)

    return {
        "data": reports,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


@router.get("/reports/{report_id}")
async def get_nsw_report(
        report_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Get a single NSW 6-monthly report."""
    _require_manager(current_user)
    db = _get_db()

    try:
        oid = ObjectId(report_id)
    except Exception:
        raise HTTPException(400, "Invalid report ID format.")

    doc = await db.nsw_reports.find_one({"_id": oid, "building_id": building_id})
    if not doc:
        raise HTTPException(404, "NSW report not found.")

    doc["id"] = str(doc.pop("_id"))
    return doc


@router.put("/reports/{report_id}")
async def update_nsw_report(
        report_id: str,
        building_id: str = Depends(get_current_building),
        payload: NSWReportUpdate = ...,
        current_user: dict = Depends(get_current_user),
):
    """Update an NSW 6-monthly report (e.g., submit after draft)."""
    _require_manager(current_user)
    db = _get_db()

    try:
        oid = ObjectId(report_id)
    except Exception:
        raise HTTPException(400, "Invalid report ID format.")

    update = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not update:
        raise HTTPException(400, "No fields to update.")

    if "financial_summary" in update and isinstance(
            update["financial_summary"], FinancialSummary
    ):
        update["financial_summary"] = update["financial_summary"].model_dump()

    update["updated_at"] = _now_iso()
    update["updated_by"] = current_user["id"]

    result = await db.nsw_reports.update_one(
        {"_id": oid, "building_id": building_id},
        {"$set": update},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "NSW report not found.")

    await create_audit_log(
        action="nsw_report_updated",
        user_id=current_user["id"],
        user_name=current_user.get("full_name", "Strata Manager"),
        resource_type="nsw_report",
        resource_id=report_id,
        details={"updated_fields": list(update.keys())},
        building_id=building_id,
    )

    return {"message": "NSW report updated.", "report_id": report_id}


# ─── Strata Hub Export ─────────────────────────────────────────────────────────


@router.get("/strata-hub-export")
async def get_strata_hub_export(
        building_id: str = Depends(get_current_building),
        financial_year: Optional[str] = Query(None, description="e.g. 2024-2025"),
        current_user: dict = Depends(get_current_user),
):
    """Generate NSW Strata Hub annual reporting data export.

    Collects all required fields for NSW Fair Trading's Strata Hub portal
    (SSMA 2015 s.182). Strata managers must submit annually within 3 months
    of the strata scheme's financial year end.

    Returns structured data ready for manual entry or API submission to Strata Hub.
    """
    _require_manager(current_user)
    db = _get_db()

    building = (
        await db.buildings.find_one({"_id": ObjectId(building_id)})
        if ObjectId.is_valid(building_id)
        else None
    )
    if not building:
        building = await db.buildings.find_one({"building_id": building_id})

    scheme_name = (building or {}).get("name", "Unknown Scheme")
    lot_count = (building or {}).get("total_lots") or (building or {}).get(
        "lot_count", 0
    )
    address = (building or {}).get("address", {})

    # Resolve most recent AGM
    agm_doc = await db.meetings.find_one(
        {"building_id": building_id, "meeting_type": "agm"},
        sort=[("meeting_date", -1)],
    )
    agm_date = (agm_doc or {}).get("meeting_date")

    # Resolve financial year end from budgets collection
    fy_doc = await db.budgets.find_one(
        {"building_id": building_id},
        sort=[("financial_year", -1)],
    )
    fy_end = (fy_doc or {}).get("end_date") or (fy_doc or {}).get("financial_year_end")

    # Resolve total levies from levy records
    levy_query: Dict[str, Any] = {"building_id": building_id}
    if financial_year:
        levy_query["financial_year"] = financial_year
    total_levies = 0.0
    async for levy in db.levies.find(levy_query):
        total_levies += levy.get("annual_amount", 0) or 0

    # Active strata manager
    manager_doc = await db.users.find_one(
        {"building_id": building_id, "role": "strata_manager"},
        sort=[("created_at", -1)],
    )
    manager_name = ""
    manager_licence = ""
    if manager_doc:
        manager_name = f"{manager_doc.get('first_name', '')} {manager_doc.get('last_name', '')}".strip()
        manager_licence = manager_doc.get("licence_number", "")

    # Insurance summary
    insurance_doc = await db.insurance_policies.find_one(
        {"building_id": building_id, "policy_type": "building"},
        sort=[("created_at", -1)],
    )
    insurer_name = (insurance_doc or {}).get("insurer_name", "")
    insurance_expiry = (insurance_doc or {}).get("expiry_date", "")

    return {
        "building_id": building_id,
        "financial_year": financial_year or "current",
        "generated_at": _now_iso(),
        "strata_hub_fields": {
            "scheme_name": scheme_name,
            "lot_count": lot_count,
            "address_street": (
                address.get("street", "") if isinstance(address, dict) else str(address)
            ),
            "address_suburb": (
                address.get("suburb", "") if isinstance(address, dict) else ""
            ),
            "address_state": "NSW",
            "financial_year_end": fy_end or "",
            "total_levies_collected": round(total_levies, 2),
            "agm_date": agm_date or "",
            "strata_manager_name": manager_name,
            "strata_manager_licence": manager_licence,
            "insurer_name": insurer_name,
            "insurance_expiry": insurance_expiry,
        },
        "submission_deadline_note": (
            "SSMA 2015 s.182 — Submit to NSW Strata Hub within 3 months of financial year end. "
            "Penalty for non-compliance: up to $5,500 for an owners corporation."
        ),
        "strata_hub_url": "https://www.stratahub.nsw.gov.au/",
    }


# ─── Levy Notice Hardship Statement ───────────────────────────────────────────


@router.get("/levy-notice-hardship")
async def get_levy_notice_hardship(
        current_user: dict = Depends(get_current_user),
):
    """Return mandatory NSW hardship information statement for levy notices.

    NSW Fair Trading requires every levy notice issued under SSMA 2015 s.83
    to include information about the NSW Debt Helpline and hardship provisions.
    This endpoint returns the prescribed text and contact details.
    """
    _ = current_user  # auth check only

    return {
        "heading": "Important Information About Your Levy Notice",
        "hardship_statement": (
            "If you are experiencing financial hardship and are unable to pay this levy by the due date, "
            "you should contact the owners corporation or strata manager as soon as possible to discuss "
            "a payment arrangement. Owners corporations may enter into payment plans under the "
            "Strata Schemes Management Act 2015."
        ),
        "debt_helpline": {
            "name": "NSW Debt Helpline",
            "phone": "1800 007 007",
            "website": "https://ndh.org.au/",
            "hours": "Monday–Friday, 9:30am–4:30pm",
            "description": (
                "The NSW Debt Helpline provides free, independent financial counselling. "
                "Financial counsellors can help you understand your options and negotiate with creditors."
            ),
        },
        "legal_basis": {
            "legislation": "Strata Schemes Management Act 2015 (NSW)",
            "section": "s.83 — Recovery of unpaid contributions",
            "nsw_fair_trading_guidance": "https://www.fairtrading.nsw.gov.au/housing-and-property/strata-and-community-title",
        },
        "interest_notice": (
            "Interest may be charged on overdue levies in accordance with the owners corporation by-laws "
            "or at the rate prescribed under the Strata Schemes Management Regulation 2016."
        ),
        "generated_at": _now_iso(),
    }


# ─── GAP-JUR-NSW-004: 10-Year Capital Works Fund PDF ──────────────────────────


@router.get("/cwf-schedule.pdf", response_class=Response)
async def get_cwf_schedule_pdf(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """GAP-JUR-NSW-004: Download the 10-year Capital Works Fund schedule as a PDF.

    Prescribed form under SSMA 2015 ss.75 & 79. Data sourced from
    sinking_fund_plan and db.settings for the building.
    """
    _require_manager(current_user)
    db = _get_db()

    from utils.pdf_generator import generate_nsw_cwf_schedule_pdf

    building_settings = await db.settings.find_one({"building_id": building_id}, {"_id": 0}) or {}
    building = await db.buildings.find_one({"building_id": building_id}, {"_id": 0, "name": 1}) or {}
    building_name = building.get("name", "Our Building")

    sinking_fund_years = await db.sinking_fund_plan.find(
        {"building_id": building_id}, {"_id": 0}
    ).sort("year", 1).to_list(20)

    try:
        pdf_bytes = generate_nsw_cwf_schedule_pdf(
            building_settings=building_settings,
            sinking_fund_years=sinking_fund_years,
            building_name=building_name,
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="cwf-schedule-{building_id}.pdf"'},
    )


# ─── GAP-JUR-NSW-006: s.184 Strata Information Certificate PDF ────────────────


@router.get("/s184-certificate.pdf", response_class=Response)
async def get_s184_certificate_pdf(
        lot_number: str = Query(..., description="Lot number for the certificate"),
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """GAP-JUR-NSW-006: Download the NSW s.184 Strata Information Certificate as a PDF.

    Includes mandatory embedded-network disclosure (SSMA 2015 s.184(1)(h) as amended 2024).
    """
    _require_manager(current_user)
    db = _get_db()

    from utils.pdf_generator import generate_nsw_s184_certificate_pdf

    building_settings = await db.settings.find_one({"building_id": building_id}, {"_id": 0}) or {}
    building = await db.buildings.find_one({"building_id": building_id}, {"_id": 0, "name": 1}) or {}
    building_name = building.get("name", "Our Building")

    unit = await db.units.find_one(
        {"building_id": building_id, "$or": [{"lot_number": lot_number}, {"unit_number": lot_number}]},
        {"_id": 0},
    )
    if not unit:
        raise HTTPException(404, f"Lot {lot_number} not found for this building.")

    # Canonical owner resolution: user_units → users first; units.owner_name is a
    # last-resort compatibility fallback only (may be stale or absent for newer units).
    owner_name = ""
    uu = await db.user_units.find_one(
        {"building_id": building_id, "unit_number": unit.get("unit_number")},
        {"_id": 0, "user_id": 1},
    )
    if uu:
        usr = await db.users.find_one({"id": uu["user_id"]}, {"_id": 0, "full_name": 1})
        owner_name = (usr or {}).get("full_name", "")
    if not owner_name:
        owner_name = unit.get("owner_name", "")

    # Resolve levy arrears
    levy_ledger = await db.unit_levy_ledger.find_one(
        {"building_id": building_id, "unit_number": unit.get("unit_number")},
        {"_id": 0, "admin_balance": 1, "sinking_balance": 1},
    ) or {}
    admin_bal = levy_ledger.get("admin_balance") or 0
    sinking_bal = levy_ledger.get("sinking_balance") or 0
    levies_owing_cents = int(round((admin_bal + sinking_bal) * 100))

    # Insurance
    insurance_doc = await db.insurance_policies.find_one(
        {"building_id": building_id, "policy_type": "building"},
        sort=[("created_at", -1)],
    ) or {}

    # Strata manager
    manager_doc = await db.users.find_one(
        {"building_id": building_id, "role": "strata_manager"},
        sort=[("created_at", -1)],
    ) or {}

    cert_data = {
        "lot_number": lot_number,
        "unit_number": unit.get("unit_number", lot_number),
        "owner_name": owner_name,
        "issued_at": _now_iso(),
        "cert_number": str(uuid.uuid4())[:8].upper(),
        "levies_owing_cents": levies_owing_cents,
        "has_embedded_network": building_settings.get("has_embedded_network", False),
        "embedded_networks": building_settings.get("embedded_networks", []),
        "strata_manager_name": manager_doc.get("full_name", ""),
        "strata_manager_email": manager_doc.get("email", ""),
        "strata_manager_phone": manager_doc.get("phone", ""),
        "by_laws_registered": True,
        "insurance_insurer": insurance_doc.get("insurer_name", ""),
        "insurance_policy_number": insurance_doc.get("policy_number", ""),
        "insurance_expiry": insurance_doc.get("expiry_date", ""),
    }

    try:
        pdf_bytes = generate_nsw_s184_certificate_pdf(
            building_settings=building_settings,
            cert_data=cert_data,
            building_name=building_name,
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))

    safe_lot = "".join(c for c in lot_number if c.isalnum() or c in "-_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="s184-{safe_lot}-{building_id}.pdf"'},
    )


# ─── GAP-JUR-NSW-007: Insurance Commission Disclosure (SSMA 2015 s.264) ────────
# A strata managing agent must disclose any commission, gift or rebate received
# from an insurer or broker for placing or renewing building insurance.
# Penalty for non-disclosure: up to $110,000.


class CommissionDisclosureCreate(BaseModel):
    """SSMA 2015 s.264 — insurance commission/gift/rebate disclosure."""

    model_config = ConfigDict(extra="ignore")

    building_id: str
    disclosure_type: str = Field(
        ...,
        pattern="^(commission|gift|rebate|other)$",
        description="Nature of the financial benefit received",
    )
    insurer_name: str = Field(..., min_length=2, max_length=200)
    broker_name: Optional[str] = Field(None, max_length=200)
    policy_number: Optional[str] = Field(None, max_length=100)
    policy_type: str = Field(
        "building",
        description="building | contents | liability | other",
    )
    policy_inception_date: Optional[str] = Field(
        None, description="ISO-8601 date"
    )
    premium_cents: Optional[int] = Field(
        None, ge=0, description="Total premium charged (cents)"
    )
    commission_amount_cents: Optional[int] = Field(
        None, ge=0, description="Commission/gift amount (cents)"
    )
    commission_rate_pct: Optional[float] = Field(
        None, ge=0, le=100, description="Commission rate as percentage of premium"
    )
    recipient_name: str = Field(
        ...,
        description="Name of agent or person who received the commission/gift",
    )
    disclosed_to: str = Field(
        "owners_corporation",
        description="Who was informed: owners_corporation | ec | agm",
    )
    disclosed_at: Optional[str] = Field(
        None,
        description="ISO-8601 datetime when disclosed to OC/EC/AGM",
    )
    notes: Optional[str] = Field(None, max_length=2000)


class CommissionDisclosureUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    disclosure_type: Optional[str] = Field(None, pattern="^(commission|gift|rebate|other)$")
    insurer_name: Optional[str] = Field(None, min_length=2, max_length=200)
    broker_name: Optional[str] = Field(None, max_length=200)
    policy_number: Optional[str] = Field(None, max_length=100)
    policy_type: Optional[str] = None
    policy_inception_date: Optional[str] = None
    premium_cents: Optional[int] = Field(None, ge=0)
    commission_amount_cents: Optional[int] = Field(None, ge=0)
    commission_rate_pct: Optional[float] = Field(None, ge=0, le=100)
    recipient_name: Optional[str] = None
    disclosed_to: Optional[str] = None
    disclosed_at: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=2000)


@router.post("/commission-disclosures", status_code=201)
async def create_commission_disclosure(
        payload: CommissionDisclosureCreate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        _toggle: dict = Depends(require_feature("nsw_commission_disclosures")),
):
    """GAP-JUR-NSW-007: Record an insurance commission/gift/rebate disclosure.

    SSMA 2015 s.264 requires a strata managing agent to disclose any commission,
    gift or rebate received from an insurer or broker for placing or renewing
    building insurance. Failure to disclose: penalty up to $110,000.
    """
    _require_manager(current_user)
    db = _get_db()

    doc = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "disclosure_type": payload.disclosure_type,
        "insurer_name": payload.insurer_name,
        "broker_name": payload.broker_name,
        "policy_number": payload.policy_number,
        "policy_type": payload.policy_type,
        "policy_inception_date": payload.policy_inception_date,
        "premium_cents": payload.premium_cents,
        "commission_amount_cents": payload.commission_amount_cents,
        "commission_rate_pct": payload.commission_rate_pct,
        "recipient_name": payload.recipient_name,
        "disclosed_to": payload.disclosed_to,
        "disclosed_at": payload.disclosed_at or _now_iso(),
        "notes": payload.notes,
        "created_by": current_user.get("id"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "cancelled_at": None,
    }
    result = await db.nsw_commission_disclosures.insert_one(doc)
    await create_audit_log(
        action="nsw_commission_disclosure_created",
        resource_type="nsw_commission_disclosures",
        resource_id=doc["id"],
        user_id=current_user.get("id", ""),
        user_name=current_user.get("full_name", ""),
        details={"insurer_name": payload.insurer_name, "disclosure_type": payload.disclosure_type},
        building_id=building_id,
    )
    return doc


@router.get("/commission-disclosures")
async def list_commission_disclosures(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        skip: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
        include_cancelled: bool = Query(False),
        _toggle: dict = Depends(require_feature("nsw_commission_disclosures")),
):
    """List all insurance commission disclosures for this building (SSMA 2015 s.264)."""
    _require_manager(current_user)
    db = _get_db()

    query: Dict[str, Any] = {"building_id": building_id}
    if not include_cancelled:
        query["cancelled_at"] = None
    cursor = db.nsw_commission_disclosures.find(query, {"_id": 0}).sort("disclosed_at", -1).skip(skip).limit(limit)
    docs = await cursor.to_list(limit)
    total = await db.nsw_commission_disclosures.count_documents(query)
    return {"total": total, "skip": skip, "limit": limit, "items": docs}


@router.get("/commission-disclosures/{disclosure_id}")
async def get_commission_disclosure(
        disclosure_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        _toggle: dict = Depends(require_feature("nsw_commission_disclosures")),
):
    """Get a single insurance commission disclosure."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.nsw_commission_disclosures.find_one(
        {"id": disclosure_id, "building_id": building_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(404, "Commission disclosure not found.")
    return doc


@router.put("/commission-disclosures/{disclosure_id}")
async def update_commission_disclosure(
        disclosure_id: str,
        payload: CommissionDisclosureUpdate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        _toggle: dict = Depends(require_feature("nsw_commission_disclosures")),
):
    """Update a commission disclosure record."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.nsw_commission_disclosures.find_one(
        {"id": disclosure_id, "building_id": building_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(404, "Commission disclosure not found.")
    if doc.get("cancelled_at"):
        raise HTTPException(409, "Cannot update a cancelled disclosure.")

    updates = payload.model_dump(exclude_unset=True)
    updates["updated_at"] = _now_iso()
    await db.nsw_commission_disclosures.update_one(
        {"id": disclosure_id, "building_id": building_id}, {"$set": updates}
    )
    return {**doc, **updates}


@router.delete("/commission-disclosures/{disclosure_id}", status_code=204)
async def cancel_commission_disclosure(
        disclosure_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        _toggle: dict = Depends(require_feature("nsw_commission_disclosures")),
):
    """Soft-cancel a commission disclosure (7-year retention, never hard-delete)."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.nsw_commission_disclosures.find_one(
        {"id": disclosure_id, "building_id": building_id}
    )
    if not doc:
        raise HTTPException(404, "Commission disclosure not found.")
    await db.nsw_commission_disclosures.update_one(
        {"id": disclosure_id, "building_id": building_id},
        {"$set": {"cancelled_at": _now_iso(), "cancelled_by": current_user.get("id")}},
    )


# ─── GAP-JUR-NSW-008: Strata Hub Annual Return Submission Tracking ─────────────
# SSMA 2015 s.182 — owners corporations must submit an annual return to Strata Hub.
# Penalty: up to $5,500 (OC) + $3/lot annually.  Due within 3 months of FY end.


class StrataHubReturnCreate(BaseModel):
    """SSMA 2015 s.182 — annual Strata Hub return submission record."""

    model_config = ConfigDict(extra="ignore")

    building_id: str
    financial_year_end: str = Field(..., description="ISO-8601 date — financial year end date")
    submission_method: str = Field(
        "manual",
        pattern="^(manual|api|portal)$",
        description="How the return was submitted",
    )
    submission_reference: Optional[str] = Field(
        None, max_length=200, description="Strata Hub confirmation reference number"
    )
    submitted_by_name: str = Field(..., description="Full name of person who submitted")
    submitted_by_email: Optional[str] = Field(None, max_length=200)
    submitted_at: Optional[str] = Field(
        None, description="ISO-8601 datetime of actual submission"
    )
    lot_count: Optional[int] = Field(None, ge=1, description="Number of lots reported")
    total_levies_cents: Optional[int] = Field(
        None, ge=0, description="Total levies collected (cents), as reported"
    )
    agm_date: Optional[str] = Field(None, description="ISO-8601 AGM date reported")
    strata_manager_name: Optional[str] = Field(None, max_length=200)
    strata_manager_licence: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = Field(None, max_length=2000)
    status: str = Field("submitted", pattern="^(draft|submitted|amended)$")


class StrataHubReturnUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    submission_reference: Optional[str] = Field(None, max_length=200)
    submitted_by_name: Optional[str] = None
    submitted_by_email: Optional[str] = None
    submitted_at: Optional[str] = None
    lot_count: Optional[int] = Field(None, ge=1)
    total_levies_cents: Optional[int] = Field(None, ge=0)
    agm_date: Optional[str] = None
    strata_manager_name: Optional[str] = None
    strata_manager_licence: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=2000)
    status: Optional[str] = Field(None, pattern="^(draft|submitted|amended)$")


@router.post("/strata-hub-returns", status_code=201)
async def create_strata_hub_return(
        payload: StrataHubReturnCreate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        _toggle: dict = Depends(require_feature("nsw_strata_hub_returns")),
):
    """GAP-JUR-NSW-008: Record a Strata Hub annual return submission.

    SSMA 2015 s.182 — owners corporations must submit an annual return to NSW
    Strata Hub within 3 months of financial year end. Non-compliance: up to
    $5,500 per owners corporation + $3/lot per annum.
    """
    _require_manager(current_user)
    db = _get_db()

    doc = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "financial_year_end": payload.financial_year_end,
        "submission_method": payload.submission_method,
        "submission_reference": payload.submission_reference,
        "submitted_by_name": payload.submitted_by_name,
        "submitted_by_email": payload.submitted_by_email,
        "submitted_at": payload.submitted_at or _now_iso(),
        "lot_count": payload.lot_count,
        "total_levies_cents": payload.total_levies_cents,
        "agm_date": payload.agm_date,
        "strata_manager_name": payload.strata_manager_name,
        "strata_manager_licence": payload.strata_manager_licence,
        "notes": payload.notes,
        "status": payload.status,
        "created_by": current_user.get("id"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    result = await db.nsw_strata_hub_returns.insert_one(doc)
    await create_audit_log(
        action="strata_hub_return_created",
        resource_type="nsw_strata_hub_returns",
        resource_id=doc["id"],
        user_id=current_user.get("id", ""),
        user_name=current_user.get("full_name", ""),
        details={
            "financial_year_end": payload.financial_year_end,
            "submission_method": payload.submission_method,
            "status": payload.status,
        },
        building_id=building_id,
    )
    return doc


@router.get("/strata-hub-returns")
async def list_strata_hub_returns(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        skip: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        _toggle: dict = Depends(require_feature("nsw_strata_hub_returns")),
):
    """List Strata Hub annual return records for this building."""
    _require_manager(current_user)
    db = _get_db()

    cursor = db.nsw_strata_hub_returns.find(
        {"building_id": building_id}, {"_id": 0}
    ).sort("financial_year_end", -1).skip(skip).limit(limit)
    docs = await cursor.to_list(limit)
    total = await db.nsw_strata_hub_returns.count_documents({"building_id": building_id})
    return {"total": total, "skip": skip, "limit": limit, "items": docs}


@router.get("/strata-hub-returns/{return_id}")
async def get_strata_hub_return(
        return_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        _toggle: dict = Depends(require_feature("nsw_strata_hub_returns")),
):
    """Get a single Strata Hub annual return record."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.nsw_strata_hub_returns.find_one(
        {"id": return_id, "building_id": building_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(404, "Strata Hub return record not found.")
    return doc


@router.put("/strata-hub-returns/{return_id}")
async def update_strata_hub_return(
        return_id: str,
        payload: StrataHubReturnUpdate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        _toggle: dict = Depends(require_feature("nsw_strata_hub_returns")),
):
    """Update a Strata Hub annual return record (e.g. add confirmation reference)."""
    _require_manager(current_user)
    db = _get_db()

    doc = await db.nsw_strata_hub_returns.find_one(
        {"id": return_id, "building_id": building_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(404, "Strata Hub return record not found.")

    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    updates["updated_at"] = _now_iso()
    await db.nsw_strata_hub_returns.update_one(
        {"id": return_id, "building_id": building_id}, {"$set": updates}
    )
    return {**doc, **updates}
