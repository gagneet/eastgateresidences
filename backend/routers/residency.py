"""Tenancy Passport and Residency Score endpoints."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from database import db
from services.tenancy_passport_service import (
    compute_residency_score,
    generate_passport_pdf,
    verify_passport_token,
)
from utils.auth import get_current_user, get_current_building

router = APIRouter(prefix="/residency", tags=["Residency"])

PASSPORT_ROLES = {"owner", "tenant", "strata_admin", "ec_member", "strata_manager", "super_admin"}


@router.get("/my-passport")
async def get_my_passport(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_my_passport
    Path: backend/routers/residency.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = current_user.get("effective_role") or current_user.get("role")
    if role not in PASSPORT_ROLES:
        raise HTTPException(status_code=403, detail="Access denied")
    unit_number = current_user.get("unit_number") or current_user.get("lot_id")
    if not unit_number:
        raise HTTPException(status_code=400, detail="No unit associated")
    score_data = await compute_residency_score(current_user["id"], unit_number, building_id)

    # Get tenancy dates
    unit = await db.units.find_one({"unit_number": unit_number}, {"_id": 0, "tenant_since": 1})
    lease_start = (unit or {}).get("tenant_since", "N/A") if unit else "N/A"

    return {
        "unit_number": unit_number,
        "building_name": "East Gate Residences",
        "lease_start": lease_start,
        "lease_end": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        **score_data,
    }


@router.get("/my-passport/download")
async def download_passport(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: download_passport
    Path: backend/routers/residency.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = current_user.get("effective_role") or current_user.get("role")
    if role not in PASSPORT_ROLES:
        raise HTTPException(status_code=403, detail="Access denied")
    unit_number = current_user.get("unit_number") or current_user.get("lot_id")
    if not unit_number:
        raise HTTPException(status_code=400, detail="No unit associated")

    score_data = await compute_residency_score(current_user["id"], unit_number, building_id)
    unit = await db.units.find_one({"unit_number": unit_number}, {"_id": 0, "tenant_since": 1})
    lease_start = (unit or {}).get("tenant_since", "N/A") if unit else "N/A"

    pdf_bytes = generate_passport_pdf(
        resident_name=current_user.get("full_name", "Resident"),
        unit_number=unit_number,
        building_name="East Gate Residences",
        lease_start=lease_start,
        lease_end=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        score=score_data["score"],
        score_components=score_data["components"],
        noise_incidents=score_data["noise_incident_count"],
        maintenance_count=score_data["maintenance_request_count"],
        user_id=current_user["id"],
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="tenancy-passport-{unit_number}.pdf"'},
    )


@router.get("/verify/{token}")
async def verify_passport(token: str):
    """PUBLIC endpoint — no auth required."""
    payload = verify_passport_token(token)
    if not payload:
        raise HTTPException(status_code=404, detail="Invalid or expired passport token")
    # Return partial name from DB
    user = await db._db.users.find_one(
        {"id": payload["user_id"]}, {"_id": 0, "full_name": 1}
    )
    full_name = (user or {}).get("full_name", "Resident") if user else "Resident"
    parts = full_name.split()
    partial_name = f"{parts[0][0]}. {parts[-1]}" if len(parts) >= 2 else full_name[:2] + "."
    return {
        "valid": True,
        "resident_name_partial": partial_name,
        "unit_number": payload["unit_number"],
        "score": payload["score"],
        "issued_at": payload.get("issued_at", ""),
        "building_name": "East Gate Residences, Denman Prospect ACT",
        "pitch": "This passport was issued using the EastGate Community OS platform. "
                 "Visit eastgateresidences.com.au/for-strata-managers",
    }
