"""
Rental Certificates Router

ACT Unit Titles (Management) Act 2011 — Section 119A
Handles requesting, issuing, and downloading rental certificates for ACT compliance.

Permission matrix:
  - Request:      owner (own unit only), chairman, ec_member, strata_manager, super_admin
  - List all:     chairman, ec_member, strata_manager, super_admin
  - List own:     owner (filtered to their unit_number)
  - Issue/Update: chairman, ec_member, strata_manager, super_admin
  - Download PDF: requesting user, admins
"""

import html as html_lib
import io
import re
import uuid
from datetime import datetime, timezone, timedelta

import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from typing import List, Optional

from database import db
from models.rental_certificates import (
    RentalCertificateRequest,
    RentalCertificateIssueRequest,
    RentalCertificateUpdateRequest,
    RentalCertificateResponse,
    RentalCertificateStatus,
    RentalCertificateType,
    CERTIFICATE_FEE_SCHEDULE,
)
from models.user import UserRole
from services.owner_service import get_owner_info
from services.settings_service import get_general_settings_or_default
from utils.auth import get_current_user, get_current_building, effective_role
from utils.email import send_email_async
from utils.pdf_generator import BuildingSettingsIncompleteError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")

# Roles that can manage (issue/update) rental certificates
_MANAGE_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.EC_MEMBER,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
}

# Roles that can request a certificate (owners can only request for their own unit)
_REQUEST_ROLES = _MANAGE_ROLES | {UserRole.OWNER}


def _is_manager(user: dict) -> bool:
    """Generated function header.

    Function: _is_manager
    Path: backend/routers/rental_certificates.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return effective_role(user) in _MANAGE_ROLES


async def _get_next_cert_number() -> str:
    """Generate sequential certificate number in format RC-YYYY-NNNN."""
    year = datetime.now(timezone.utc).year
    prefix = f"RC-{year}-"
    count = await db.rental_certificates.count_documents(
        {"cert_number": {"$regex": f"^{re.escape(prefix)}"}}
    )
    return f"{prefix}{count + 1:04d}"


async def _auto_populate_financial_data(unit_number: str, building_id: str) -> dict:
    """
    Pull financial snapshot from DB collections for auto-population on issue.
    Returns dict of fields to merge into the certificate document.
    """
    data: dict = {}

    # Unit / owner data — canonical source: user_units → users, fallback units.owner_name
    unit = await db.units.find_one({"unit_number": unit_number, "building_id": building_id}, {"_id": 0})
    if unit:
        owner_info = await get_owner_info(unit_number, building_id)
        data["owner_name"] = owner_info["owner_name"]
        data["owner_name_b"] = owner_info.get("co_owner_name") or unit.get("owner_name_b")
        data["lot_number"] = unit.get("lot_number") or unit.get("lot")
        data["unit_entitlement"] = unit.get("unit_entitlement") or unit.get("uoe")
        data["property_type"] = unit.get("property_type") or unit.get("unit_type")

    # Latest annual levy rates — scoped to building
    latest_levy = await db.annual_levies.find_one(
        {"building_id": building_id}, sort=[("year", -1)]
    )
    if latest_levy and unit:
        uoe = data.get("unit_entitlement") or 0
        total_uoe = latest_levy.get("total_uoe") or 1
        admin_fund = latest_levy.get("admin_fund") or {}
        sinking_fund = latest_levy.get("sinking_fund") or {}
        total_levy = (
                admin_fund.get("levy_income", 0)
                + sinking_fund.get("levy_income", 0)
                or latest_levy.get("admin_levy_income", 0) + latest_levy.get("sink_levy_income", 0)
        )
        levy_annual = round((uoe / total_uoe) * total_levy, 2) if total_uoe else 0
        data["levy_annual"] = levy_annual
        data["levy_quarterly"] = round(levy_annual / 4, 2)
        data["admin_budget_current"] = admin_fund.get("levy_income", 0) or latest_levy.get("admin_levy_income", 0)
        data["sinking_fund_balance"] = sinking_fund.get("opening_balance", 0) or latest_levy.get(
            "sinking_opening_balance", 0)

    # Arrears from unit levy ledger — scoped to building
    ledger = await db.unit_levy_ledger.find_one(
        {"unit_number": unit_number, "building_id": building_id}, sort=[("year", -1)]
    )
    if ledger:
        net = ledger.get("net_balance", 0) or 0
        data["arrears_amount"] = max(0.0, float(net))
    else:
        # No ledger record — report 0 (units.balance_owing is a stale seed snapshot).
        data["arrears_amount"] = 0.0

    # EC members snapshot — scoped to building
    ec_cursor = db.ec_members.find({"building_id": building_id}, {"_id": 0, "name": 1, "role": 1})
    ec_members = await ec_cursor.to_list(length=20)
    data["ec_members_snapshot"] = ec_members

    return data


def _cert_doc_to_response(doc: dict, is_impersonated: bool = False) -> dict:
    """Convert raw MongoDB document to RentalCertificateResponse-compatible dict."""
    doc = dict(doc)
    doc["id"] = doc.pop("_id", doc.get("id", ""))

    if is_impersonated:
        # Mask PII in certificate response
        if doc.get("tenant_name"):
            doc["tenant_name"] = "REDACTED"
        if doc.get("owner_name"):
            doc["owner_name"] = "REDACTED"
        if doc.get("owner_name_b"):
            doc["owner_name_b"] = "REDACTED"
        if doc.get("requested_by_name") and "@" in doc["requested_by_name"]:
            # If name is an email, mask it
            name = doc["requested_by_name"]
            n, d = name.split("@", 1)
            doc["requested_by_name"] = f"{n[0]}***@{d}"
        elif doc.get("requested_by_name"):
            doc["requested_by_name"] = "REDACTED"

        if doc.get("issued_by_name") and "@" in doc["issued_by_name"]:
            name = doc["issued_by_name"]
            n, d = name.split("@", 1)
            doc["issued_by_name"] = f"{n[0]}***@{d}"
        elif doc.get("issued_by_name"):
            doc["issued_by_name"] = "REDACTED"

        if doc.get("ec_members_snapshot"):
            for member in doc["ec_members_snapshot"]:
                if member.get("name"):
                    member["name"] = "REDACTED"

    return doc


# ==================== ROUTES ====================

@router.post("/rental-certificates", status_code=201)
async def create_rental_certificate(
        data: RentalCertificateRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Request a new Rental Certificate.

    Owners may only request for their own unit_number.
    EC/admin can request on behalf of any unit.
    """
    role = effective_role(current_user)
    if role not in _REQUEST_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized to request rental certificates")

    # Owners can only request for their own unit
    if role == UserRole.OWNER:
        owner_unit = current_user.get("unit_number")
        if owner_unit != data.unit_number:
            raise HTTPException(
                status_code=403,
                detail="Owners may only request certificates for their own unit",
            )

    now = datetime.now(timezone.utc).isoformat()
    cert_id = str(uuid.uuid4())
    cert_number = await _get_next_cert_number()

    # Validate certificate_type
    valid_types = {RentalCertificateType.SALE, RentalCertificateType.RENTAL, RentalCertificateType.UPDATE,
                   RentalCertificateType.NSW_S184}
    cert_type = data.certificate_type if data.certificate_type in valid_types else RentalCertificateType.SALE
    fee_aud = CERTIFICATE_FEE_SCHEDULE.get(cert_type, 332.00)

    doc = {
        "_id": cert_id,
        "id": cert_id,
        "building_id": building_id,
        "cert_number": cert_number,
        "unit_number": data.unit_number,
        "tenant_name": data.tenant_name,
        "lease_start_date": data.lease_start_date,
        "lease_end_date": data.lease_end_date,
        "status": RentalCertificateStatus.REQUESTED,
        "requested_by": current_user["id"],
        "requested_by_name": current_user.get("full_name", current_user.get("email", "")),
        "requested_at": now,
        "issued_by": None,
        "issued_by_name": None,
        "issued_at": None,
        "expiry_date": None,
        "additional_notes": data.additional_notes,
        "arrears_amount": 0.0,
        "certificate_type": cert_type,
        "fee_aud": fee_aud,
        "tenancy_start_date": data.tenancy_start_date,
        "weekly_rent": data.weekly_rent,
        "created_at": now,
        "updated_at": now,
    }

    await db.rental_certificates.insert_one(doc)

    # Notify admin/EC roles for THIS building only.
    # users collection is global — go via memberships to scope to building.
    try:
        building_memberships = await db.memberships.find(
            {"building_id": building_id, "is_active": True},
            {"_id": 0, "user_id": 1},
        ).to_list(length=200)
        member_ids = [m["user_id"] for m in building_memberships if m.get("user_id")]
        admin_users = await db.users.find(
            {"id": {"$in": member_ids}, "status": "active", "role": {"$in": list(_MANAGE_ROLES)}},
            {"_id": 0, "email": 1, "mail_username": 1, "full_name": 1},
        ).to_list(length=50)

        for admin in admin_users:
            recipients = list({
                e for e in [admin.get("email"), admin.get("mail_username")] if e
            })
            for recipient in recipients:
                safe_unit_number = html_lib.escape(str(data.unit_number))
                safe_tenant_name = html_lib.escape(data.tenant_name)
                safe_lease_start = html_lib.escape(str(data.lease_start_date))
                safe_requested_by = html_lib.escape(doc['requested_by_name'])
                safe_cert_number = html_lib.escape(cert_number)
                await send_email_async(
                    to_email=recipient,
                    subject=f"[Rental Certificate] New request — Unit {data.unit_number}",
                    html_content=(
                        f"<p>A new rental certificate has been requested.</p>"
                        f"<p><b>Unit:</b> {safe_unit_number}<br>"
                        f"<b>Tenant:</b> {safe_tenant_name}<br>"
                        f"<b>Lease Start:</b> {safe_lease_start}<br>"
                        f"<b>Requested by:</b> {safe_requested_by}<br>"
                        f"<b>Certificate #:</b> {safe_cert_number}</p>"
                        f"<p>Please log in to issue the certificate.</p>"
                    ),
                    context=f"rental_cert_request:cert={cert_number}",
                )
    except Exception as e:
        logger.warning(f"Could not notify admins of rental cert request: {e}")

    return _cert_doc_to_response(doc, is_impersonated="impersonator_id" in current_user)


@router.post("/rental-certificates/rental-type", status_code=201)
async def create_rental_type_certificate(
        data: RentalCertificateRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Request a rental-type certificate (July 2024 Housing & Consumer Affairs
    Legislation Amendment Act).

    Convenience endpoint — forces certificate_type="rental" then delegates to
    the main create endpoint.  Owners may only request for their own unit.
    """
    role = effective_role(current_user)
    if role not in _REQUEST_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized to request rental certificates")

    if role == UserRole.OWNER and current_user.get("unit_number") != data.unit_number:
        raise HTTPException(
            status_code=403, detail="Owners may only request certificates for their own unit"
        )

    # Force rental type and delegate
    data.certificate_type = RentalCertificateType.RENTAL
    return await create_rental_certificate(data=data, current_user=current_user, building_id=building_id)


@router.get("/rental-certificates", response_model=List[RentalCertificateResponse])
async def list_rental_certificates(
        status: Optional[str] = None,
        unit_number: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    List rental certificates.

    - Managers see all certificates (optionally filtered).
    - Owners see only certificates for their own unit_number.
    """
    role = effective_role(current_user)
    if role not in _REQUEST_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized to view rental certificates")

    query: dict = {}

    if _is_manager(current_user):
        if unit_number:
            query["unit_number"] = unit_number
    else:
        # Owner: restrict to their own unit
        owner_unit = current_user.get("unit_number")
        if not owner_unit:
            return []
        query["unit_number"] = owner_unit

    if status:
        query["status"] = status

    cursor = db.rental_certificates.find(query, {"_id": 0}).sort("created_at", -1)
    docs = await cursor.to_list(length=200)
    is_imp = "impersonator_id" in current_user
    return [_cert_doc_to_response(d, is_impersonated=is_imp) for d in docs]


@router.get("/rental-certificates/unit/{unit_number}/current", response_model=Optional[RentalCertificateResponse])
async def get_current_certificate_for_unit(
        unit_number: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Return the most recent ISSUED certificate for a unit."""
    role = effective_role(current_user)
    if role not in _REQUEST_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Owners can only query their own unit
    if role == UserRole.OWNER and current_user.get("unit_number") != unit_number:
        raise HTTPException(status_code=403, detail="Not authorized for this unit")

    doc = await db.rental_certificates.find_one(
        {"unit_number": unit_number, "status": RentalCertificateStatus.ISSUED},
        {"_id": 0},
        sort=[("issued_at", -1)],
    )
    if not doc:
        return None
    return _cert_doc_to_response(doc, is_impersonated="impersonator_id" in current_user)


@router.get("/rental-certificates/{cert_id}", response_model=RentalCertificateResponse)
async def get_rental_certificate(
        cert_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Get a single rental certificate by ID."""
    role = effective_role(current_user)
    if role not in _REQUEST_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")

    doc = await db.rental_certificates.find_one({"id": cert_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Certificate not found")

    # Owners may only view their own unit's certificates
    if role == UserRole.OWNER and doc.get("unit_number") != current_user.get("unit_number"):
        raise HTTPException(status_code=403, detail="Not authorized for this certificate")

    return _cert_doc_to_response(doc, is_impersonated="impersonator_id" in current_user)


@router.put("/rental-certificates/{cert_id}/issue", response_model=RentalCertificateResponse)
async def issue_rental_certificate(
        cert_id: str,
        data: RentalCertificateIssueRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Issue (approve and generate) a rental certificate.

    Only EC/admin roles can call this endpoint.
    Auto-populates financial data from the database.
    Marks any previously ISSUED certs for the same unit as SUPERSEDED.
    """
    if not _is_manager(current_user):
        raise HTTPException(status_code=403, detail="Only EC/admin can issue certificates")

    doc = await db.rental_certificates.find_one({"id": cert_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Certificate not found")

    if doc.get("status") == RentalCertificateStatus.ISSUED:
        raise HTTPException(status_code=400, detail="Certificate is already issued")

    now = datetime.now(timezone.utc)
    expiry = (now + timedelta(days=5 * 365)).isoformat()
    now_str = now.isoformat()

    # Auto-populate financial + property data using the authenticated building context.
    financial_data = await _auto_populate_financial_data(doc["unit_number"], building_id)

    # Supersede any previous ISSUED cert for this unit within this building.
    await db.rental_certificates.update_many(
        {
            "unit_number": doc["unit_number"],
            "status": RentalCertificateStatus.ISSUED,
            "id": {"$ne": cert_id},
        },
        {"$set": {"status": RentalCertificateStatus.SUPERSEDED, "updated_at": now_str}},
    )

    # Build the update payload
    update_fields = {
        "status": RentalCertificateStatus.ISSUED,
        "issued_by": current_user["id"],
        "issued_by_name": current_user.get("full_name", current_user.get("email", "")),
        "issued_at": now_str,
        "expiry_date": expiry,
        "updated_at": now_str,
        **financial_data,
        **{k: v for k, v in data.model_dump().items() if v is not None},
    }

    await db.rental_certificates.update_one(
        {"id": cert_id}, {"$set": update_fields}
    )

    updated_doc = await db.rental_certificates.find_one({"id": cert_id}, {"_id": 0})

    # Email PDF to the requesting owner (both email addresses)
    try:
        requester = await db.users.find_one(
            {"id": doc["requested_by"]}, {"_id": 0, "email": 1, "mail_username": 1}
        )
        if requester:
            recipients = list({
                e for e in [requester.get("email"), requester.get("mail_username")] if e
            })
            for recipient in recipients:
                safe_cert_num = html_lib.escape(doc['cert_number'])
                safe_unit_num = html_lib.escape(str(doc['unit_number']))
                safe_tenant_name = html_lib.escape(doc['tenant_name'])
                safe_valid_until = html_lib.escape(expiry[:10])
                await send_email_async(
                    to_email=recipient,
                    subject=f"[Rental Certificate] {doc['cert_number']} — Unit {doc['unit_number']} — ISSUED",
                    html_content=(
                        f"<p>Your rental certificate has been issued and is ready for download.</p>"
                        f"<p><b>Certificate #:</b> {safe_cert_num}<br>"
                        f"<b>Unit:</b> {safe_unit_num}<br>"
                        f"<b>Tenant:</b> {safe_tenant_name}<br>"
                        f"<b>Valid Until:</b> {safe_valid_until}</p>"
                        f"<p>Please log in to the portal to download the PDF certificate.</p>"
                    ),
                    context=f"rental_cert_issued:cert={doc['cert_number']}",
                )
    except Exception as e:
        logger.warning(f"Could not email certificate notification: {e}")

    return _cert_doc_to_response(updated_doc, is_impersonated="impersonator_id" in current_user)


@router.put("/rental-certificates/{cert_id}/update", response_model=RentalCertificateResponse)
async def update_rental_certificate(
        cert_id: str,
        data: RentalCertificateUpdateRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Update fields on an existing (issued) certificate. EC/admin only."""
    if not _is_manager(current_user):
        raise HTTPException(status_code=403, detail="Only EC/admin can update certificates")

    doc = await db.rental_certificates.find_one({"id": cert_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Certificate not found")

    now_str = datetime.now(timezone.utc).isoformat()
    update_fields = {
        k: v for k, v in data.model_dump().items() if v is not None
    }
    update_fields["updated_at"] = now_str

    await db.rental_certificates.update_one(
        {"id": cert_id}, {"$set": update_fields}
    )

    updated_doc = await db.rental_certificates.find_one({"id": cert_id}, {"_id": 0})
    return _cert_doc_to_response(updated_doc, is_impersonated="impersonator_id" in current_user)


@router.get("/rental-certificates/{cert_id}/pdf")
async def download_rental_certificate_pdf(
        cert_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Download the Rental Certificate as a PDF (StreamingResponse).

    The requesting user and managers can download. PDF only available for ISSUED certs.
    """
    role = effective_role(current_user)
    if role not in _REQUEST_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")

    doc = await db.rental_certificates.find_one({"id": cert_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Certificate not found")

    # Owners can only download their own unit's certs
    if role == UserRole.OWNER and doc.get("unit_number") != current_user.get("unit_number"):
        raise HTTPException(status_code=403, detail="Not authorized for this certificate")

    if doc.get("status") != RentalCertificateStatus.ISSUED:
        raise HTTPException(
            status_code=400,
            detail="Certificate has not been issued yet — PDF not available",
        )

    # Get building name from settings
    settings = await get_general_settings_or_default(doc.get("building_id"), {"_id": 0})
    building_name = settings.get("building_name", "Our Residences")

    # Generate PDF
    try:
        from utils.pdf_generator import generate_rental_certificate_pdf
        pdf_bytes = generate_rental_certificate_pdf(doc, building_name, building_settings=settings)
    except BuildingSettingsIncompleteError:
        raise  # converted to 422 by app-level handler in server.py
    except Exception as e:
        logger.error(f"PDF generation failed for cert {cert_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate PDF")

    if not pdf_bytes:
        raise HTTPException(status_code=500, detail="PDF generation unavailable")

    filename = f"{doc['cert_number']}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/rental-certificates/fee-schedule")
async def get_certificate_fee_schedule(
        current_user: dict = Depends(get_current_user),
):
    """Return the current fee schedule for all certificate types, by jurisdiction."""
    return {
        "act": {
            "sale_s119a": CERTIFICATE_FEE_SCHEDULE[RentalCertificateType.SALE],
            "rental_s119a": CERTIFICATE_FEE_SCHEDULE[RentalCertificateType.RENTAL],
            "update": CERTIFICATE_FEE_SCHEDULE[RentalCertificateType.UPDATE],
            "legislation": "ACT Unit Titles (Management) Act 2011 — s.119A",
            "fee_determination": "ACT Government fee determination, effective July 2025",
        },
        "nsw": {
            "pre_sale_s184": CERTIFICATE_FEE_SCHEDULE[RentalCertificateType.NSW_S184],
            "legislation": "NSW Strata Schemes Management Act 2015 — s.184",
            "fee_determination": "NSW Fair Trading fee cap, effective July 2025",
        },
    }


@router.post("/rental-certificates/external-order", status_code=201)
async def external_certificate_order(
        data: RentalCertificateRequest,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """
    Conveyancer / solicitor orders a Section 119 (ACT) or Section 184 (NSW) certificate.

    Creates a certificate request in PENDING_PAYMENT status. The manager queue
    (GET /rental-certificates?status=pending_payment) shows outstanding external orders.
    """
    role = effective_role(current_user)
    # Managers can create on behalf of solicitors; owners can request their own
    if role not in {*_MANAGE_ROLES, UserRole.OWNER}:
        raise HTTPException(403, "Not authorised to submit an external certificate order.")

    valid_external_types = {RentalCertificateType.SALE, RentalCertificateType.NSW_S184}
    if data.certificate_type not in valid_external_types:
        raise HTTPException(400, f"External orders support types: {valid_external_types}")

    now = datetime.now(timezone.utc).isoformat()
    cert_id = str(uuid.uuid4())
    cert_number = await _get_next_cert_number()
    fee_aud = CERTIFICATE_FEE_SCHEDULE.get(data.certificate_type, 332.00)

    doc = {
        "_id": cert_id,
        "id": cert_id,
        "cert_number": cert_number,
        "building_id": building_id,
        "unit_number": data.unit_number,
        "tenant_name": data.tenant_name,
        "lease_start_date": data.lease_start_date,
        "lease_end_date": data.lease_end_date,
        "certificate_type": data.certificate_type,
        "status": RentalCertificateStatus.PENDING_PAYMENT,
        "fee_aud": fee_aud,
        "fee_paid": data.fee_paid,
        "fee_payment_method": data.fee_payment_method,
        "payment_reference": data.payment_reference,
        "requester_firm": data.requester_firm,
        "requester_email": data.requester_email,
        "requester_reference": data.requester_reference,
        "requested_by": current_user["id"],
        "requested_by_name": current_user.get("full_name", ""),
        "requested_at": now,
        "created_at": now,
        "updated_at": now,
        "additional_notes": data.additional_notes,
    }

    await db.rental_certificates.insert_one(doc)
    logger.info("External certificate order %s created: %s %s", cert_number, data.certificate_type, data.unit_number)

    return {
        "cert_id": cert_id,
        "cert_number": cert_number,
        "status": RentalCertificateStatus.PENDING_PAYMENT,
        "fee_aud": fee_aud,
        "message": "Certificate order received. It will be processed once payment is confirmed.",
    }
