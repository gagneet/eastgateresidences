"""
OwnerHub router — investment property management, tenancy lifecycle,
health scores and TCO endpoints for property owners and real estate agents.
"""
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional
from pydantic import BaseModel, Field
from request_context import get_ctx_building_id

from models.tenancy import (
    PropertyCreate, PropertyUpdate,
    TenancyCreate, TenancyUpdate,
    RentTransactionCreate,
    InspectionCreate, InspectionUpdate,
    TrueOwnershipCostInput,
)
from services import ownerhub_service as ohsvc
from services import tenancy_service as svc
from utils.auth import effective_role, get_current_user

router = APIRouter(prefix="/owner-hub", tags=["OwnerHub"])
logger = logging.getLogger(__name__)

ALLOWED_ROLES = {
    "owner", "real_estate_agent", "super_admin",
    "strata_admin", "ec_member", "strata_manager", "admin_staff"
}


def _require_ownerhub(current_user: dict = Depends(get_current_user)) -> dict:
    """Generated function header.

    Function: _require_ownerhub
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Access denied")
    return current_user


async def _get_tenancy_with_property_access(tenancy_id: str, current_user: dict) -> dict:
    """Generated function header.

    Function: _get_tenancy_with_property_access
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    tenancy = await svc.get_tenancy_by_id(tenancy_id)
    property_id = tenancy.get("property_id")
    if property_id:
        await svc.get_property_by_id(property_id, current_user)
    return tenancy


# ── Properties ─────────────────────────────────────────────────────────────

@router.get("/properties")
async def list_properties(
        skip: int = 0,
        limit: int = 50,
        enrich: bool = False,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: list_properties
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    role = effective_role(current_user)
    user_id = current_user.get("id", "")
    docs = await db.owner_properties.find(
        svc.ownerhub_property_visibility_query(current_user)
    ).skip(skip).limit(limit).to_list(limit)
    props = [_doc(d) for d in docs]

    if not enrich:
        return props

    # Enrich each property concurrently with active tenancy and health score
    async def _enrich(prop: dict) -> dict:
        """Generated function header.

        Function: _enrich
        Path: backend/routers/ownerhub.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        prop_id = prop.get("id")
        tenancy = await svc.get_active_tenancy(prop_id) if prop_id else None
        if tenancy:
            prop["tenant_name"] = tenancy.get("tenant_name") or tenancy.get("tenant_full_name") or tenancy.get(
                "lessee_name")
            prop["lease_end"] = tenancy.get("lease_end") or tenancy.get("end_date") or tenancy.get("lease_end_date")
            prop["tenancy_status"] = tenancy.get("status", "active")
        else:
            prop["tenant_name"] = None
            prop["lease_end"] = None
            prop["tenancy_status"] = "vacant"
        try:
            health = await ohsvc.compute_property_health_score(prop_id, prop)
            prop["health_score"] = health.get("health_score") if isinstance(health, dict) else None
            prop["health_label"] = health.get("risk_level") if isinstance(health, dict) else None
        except Exception:
            logger.exception(
                "Failed to compute health score for property_id=%s (user_id=%s, role=%s)",
                prop_id,
                user_id,
                role,
            )
            prop["health_score"] = None
            prop["health_label"] = None
        return prop

    enriched = await asyncio.gather(*(_enrich(p) for p in props))
    return list(enriched)


@router.post("/properties", status_code=201)
async def create_property(
        data: PropertyCreate,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: create_property
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = current_user.get("role", "")
    if role not in ("owner", "super_admin"):
        raise HTTPException(status_code=403, detail="Only owners or super_admin can create properties")
    owner_id = current_user.get("id")
    return await svc.create_property(owner_id, data.model_dump(exclude_none=True))


@router.get("/properties/{property_id}")
async def get_property(
        property_id: str,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: get_property
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.get_property_by_id(property_id, current_user)


@router.put("/properties/{property_id}")
async def update_property(
        property_id: str,
        data: PropertyUpdate,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: update_property
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.update_property(
        property_id, data.model_dump(exclude_none=True), current_user
    )


@router.delete("/properties/{property_id}", status_code=204)
async def delete_property(
        property_id: str,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: delete_property
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = current_user.get("role", "")
    if role not in ("owner", "super_admin"):
        raise HTTPException(status_code=403, detail="Only owners or super_admin can delete properties")
    await svc.delete_property(property_id, current_user.get("id"))


# ── Tenancies ─────────────────────────────────────────────────────────────

@router.get("/properties/{property_id}/tenancy")
async def get_active_tenancy(
        property_id: str,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: get_active_tenancy
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await svc.get_property_by_id(property_id, current_user)  # access check
    tenancy = await svc.get_active_tenancy(property_id)
    if not tenancy:
        raise HTTPException(status_code=404, detail="No active tenancy found")
    arrears = await svc.compute_arrears(tenancy["id"])
    return {**tenancy, **arrears}


@router.post("/properties/{property_id}/tenancy", status_code=201)
async def create_tenancy_for_property(
        property_id: str,
        data: TenancyCreate,
        current_user: dict = Depends(_require_ownerhub)
):
    """Create a tenancy for a property. Mirrors POST /tenancies but scoped to property URL."""
    await svc.get_property_by_id(property_id, current_user)  # access check
    payload = data.model_dump(exclude_none=True)
    payload["property_id"] = property_id
    return await svc.create_tenancy(property_id, payload, current_user.get("id"))


@router.get("/properties/{property_id}/tenancies")
async def list_tenancies(
        property_id: str,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: list_tenancies
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await svc.get_property_by_id(property_id, current_user)  # access check
    return await svc.get_tenancies_for_property(property_id)


@router.post("/tenancies", status_code=201)
async def create_tenancy(
        data: TenancyCreate,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: create_tenancy
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not data.property_id:
        raise HTTPException(status_code=422, detail="property_id is required in body for this endpoint")
    await svc.get_property_by_id(data.property_id, current_user)  # access check
    return await svc.create_tenancy(data.property_id, data.model_dump(exclude_none=True), current_user.get("id"))


@router.get("/tenancies/{tenancy_id}")
async def get_tenancy(
        tenancy_id: str,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: get_tenancy
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    tenancy = await _get_tenancy_with_property_access(tenancy_id, current_user)
    arrears = await svc.compute_arrears(tenancy_id)
    return {**tenancy, **arrears}


@router.put("/tenancies/{tenancy_id}")
async def update_tenancy(
        tenancy_id: str,
        data: TenancyUpdate,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: update_tenancy
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await _get_tenancy_with_property_access(tenancy_id, current_user)
    return await svc.update_tenancy(tenancy_id, data.model_dump(exclude_none=True))


@router.post("/tenancies/{tenancy_id}/payments", status_code=201)
async def record_payment(
        tenancy_id: str,
        data: RentTransactionCreate,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: record_payment
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await _get_tenancy_with_property_access(tenancy_id, current_user)
    return await svc.record_rent_payment(tenancy_id, data.model_dump(exclude_none=True))


@router.get("/tenancies/{tenancy_id}/ledger")
async def get_ledger(
        tenancy_id: str,
        limit: int = 50,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: get_ledger
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await _get_tenancy_with_property_access(tenancy_id, current_user)
    return await svc.get_rent_ledger(tenancy_id, limit=limit)


@router.get("/properties/{property_id}/ledger")
async def get_property_ledger(
        property_id: str,
        limit: int = 50,
        current_user: dict = Depends(_require_ownerhub)
):
    """Return ledger for the active tenancy of a property (convenience endpoint)."""
    await svc.get_property_by_id(property_id, current_user)  # access check
    tenancy = await svc.get_active_tenancy(property_id)
    if not tenancy:
        return []
    return await svc.get_rent_ledger(tenancy["id"], limit=limit)


# ── Health Score & TCO ─────────────────────────────────────────────────────

@router.get("/properties/{property_id}/health-score")
async def get_health_score(
        property_id: str,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: get_health_score
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await svc.get_property_by_id(property_id, current_user)
    snapshot = await ohsvc.compute_property_health_score(property_id)
    asyncio.create_task(ohsvc.store_health_snapshot(property_id, snapshot))
    return snapshot


@router.get("/properties/{property_id}/tco")
async def get_tco(
        property_id: str,
        year: Optional[str] = None,
        vacancy_weeks: Optional[int] = 2,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: get_tco
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await svc.get_property_by_id(property_id, current_user)
    inputs = {"year": year or "", "vacancy_weeks": vacancy_weeks}
    return await ohsvc.compute_tco(property_id, inputs)


@router.post("/properties/{property_id}/tco")
async def compute_tco(
        property_id: str,
        data: TrueOwnershipCostInput,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: compute_tco
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await svc.get_property_by_id(property_id, current_user)
    try:
        return await ohsvc.compute_tco(property_id, data.model_dump(exclude_none=True))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("TCO computation failed for property_id=%s", property_id)
        raise HTTPException(status_code=500, detail="TCO computation failed") from exc


@router.get("/unit-tco")
async def get_unit_tco(
        unit_number: str,
        year: Optional[str] = None,
        vacancy_weeks: Optional[int] = 2,
        current_user: dict = Depends(_require_ownerhub)
):
    """
    Compute TCO for a strata unit directly
    (admin view — no property registration needed).
    """
    role = current_user.get("role", "")
    # Non-admin owners can only see their own unit
    if role not in ("super_admin", "strata_admin", "ec_member", "strata_manager", "real_estate_agent",
                    "admin_staff"):
        user_unit = current_user.get("unit_number", "")
        if user_unit != unit_number:
            raise HTTPException(status_code=403, detail="You can only view your own unit")
    return await ohsvc.compute_unit_tco(
        unit_number,
        year or str(__import__("datetime").date.today().year),
        vacancy_weeks,
        user_id=current_user.get("id"),
    )


class UnitMortgageInput(BaseModel):
    """Owner's mortgage for a unit, entered on the True Cost of Ownership view.
    Balance is in DOLLARS from the UI and converted to integer cents at this boundary."""
    mortgage_balance: float = Field(..., ge=0, description="Outstanding mortgage balance in dollars")
    interest_rate: float = Field(..., ge=0, le=100, description="Annual interest rate as a percentage")
    repayment_type: str = Field("principal_and_interest")


def _resolve_owner_building_id(current_user: dict) -> str:
    return get_ctx_building_id() or current_user.get("building_id") or ""


def _assert_owns_unit(current_user: dict, unit_number: str) -> None:
    """Mortgage is private financial data — only the unit's own owner may read/write it.
    Elevated operational roles do NOT get to see an owner's personal mortgage."""
    if str(current_user.get("unit_number", "")).upper() != str(unit_number).upper():
        raise HTTPException(status_code=403, detail="You can only manage the mortgage for your own unit")


@router.get("/unit-mortgage/{unit_number}")
async def get_unit_mortgage(unit_number: str, current_user: dict = Depends(_require_ownerhub)):
    """Return the authenticated owner's saved mortgage for this unit (or nulls if none)."""
    _assert_owns_unit(current_user, unit_number)
    building_id = _resolve_owner_building_id(current_user)
    rec = await ohsvc.get_unit_mortgage(building_id, unit_number, current_user.get("id"))
    if not rec:
        return {"unit_number": str(unit_number).upper(), "mortgage_balance": None,
                "interest_rate": None, "repayment_type": None}
    return {
        "unit_number": rec["unit_number"],
        "mortgage_balance": round(float(rec.get("mortgage_balance_cents") or 0) / 100, 2),
        "interest_rate": rec.get("interest_rate"),
        "repayment_type": rec.get("repayment_type"),
        "updated_at": rec.get("updated_at"),
    }


@router.put("/unit-mortgage/{unit_number}")
async def put_unit_mortgage(unit_number: str, payload: UnitMortgageInput,
                            current_user: dict = Depends(_require_ownerhub)):
    """Save (upsert) the authenticated owner's mortgage for this unit."""
    _assert_owns_unit(current_user, unit_number)
    building_id = _resolve_owner_building_id(current_user)
    try:
        rec = await ohsvc.save_unit_mortgage(
            building_id=building_id,
            unit_number=unit_number,
            user_id=current_user.get("id"),
            mortgage_balance_cents=int(round(payload.mortgage_balance * 100)),
            interest_rate=payload.interest_rate,
            repayment_type=payload.repayment_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "unit_number": rec["unit_number"],
        "mortgage_balance": round(float(rec["mortgage_balance_cents"]) / 100, 2),
        "interest_rate": rec["interest_rate"],
        "repayment_type": rec["repayment_type"],
        "updated_at": rec["updated_at"],
    }


@router.get("/units")
async def list_strata_units(
        skip: int = 0,
        limit: int = 100,
        current_user: dict = Depends(_require_ownerhub)
):
    """List strata units for TCO unit selector (admin view)."""
    from database import db
    role = current_user.get("role", "")
    if role in ("super_admin", "strata_admin", "ec_member", "strata_manager", "admin_staff",
                "real_estate_agent"):
        units = await db.units.find(
            {},
            {"_id": 0, "unit_number": 1, "owner_name": 1, "property_type": 1, "entitlement": 1}
        ).skip(skip).limit(limit).to_list(limit)
    else:
        user_unit = current_user.get("unit_number", "")
        units = await db.units.find(
            {"unit_number": user_unit},
            {"_id": 0, "unit_number": 1, "owner_name": 1, "property_type": 1, "entitlement": 1}
        ).to_list(1)
    return units


# ── Inspections ────────────────────────────────────────────────────────────

@router.get("/inspections")
async def list_all_inspections(
        skip: int = 0,
        limit: int = 100,
        current_user: dict = Depends(_require_ownerhub)
):
    """Return all inspections across the current user's accessible properties."""
    from database import db

    prop_ids = [
        str(p["_id"])
        async for p in db.owner_properties.find(svc.ownerhub_property_visibility_query(current_user), {"_id": 1})
    ]

    if not prop_ids:
        return []

    # Performance Optimization⚡: Eliminate N+1 query pattern via batch service call.
    # This reduces database round-trips from O(N) to O(1).
    return await svc.get_inspections_for_properties(
        prop_ids, skip=skip, limit=limit
    )


@router.get("/properties/{property_id}/inspections")
async def list_inspections(
        property_id: str,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: list_inspections
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await svc.get_property_by_id(property_id, current_user)
    return await svc.get_inspections_for_property(property_id)


@router.post("/properties/{property_id}/inspections", status_code=201)
async def create_inspection(
        property_id: str,
        data: InspectionCreate,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: create_inspection
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await svc.get_property_by_id(property_id, current_user)
    payload = data.model_dump(exclude_none=True)
    payload["property_id"] = property_id
    return await svc.create_inspection(payload, current_user.get("id"))


@router.put("/inspections/{inspection_id}")
async def update_inspection(
        inspection_id: str,
        data: InspectionUpdate,
        current_user: dict = Depends(_require_ownerhub)
):
    """Generated function header.

    Function: update_inspection
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.update_inspection(inspection_id, data.model_dump(exclude_none=True))


# ── Weekly Radar ───────────────────────────────────────────────────────────

@router.get("/weekly-radar")
async def weekly_radar(current_user: dict = Depends(_require_ownerhub)):
    """Generated function header.

    Function: weekly_radar
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    props = await db.owner_properties.find(
        svc.ownerhub_property_visibility_query(current_user), {"_id": 1}
    ).to_list(500)

    # Performance Optimization⚡: Parallelized radar generation to reduce cumulative latency.
    # This speeds up response for multi-property users from O(N) to O(1) concurrent requests.
    tasks = [ohsvc.generate_weekly_radar(str(p["_id"])) for p in props]
    radar_results = await asyncio.gather(*tasks, return_exceptions=True)

    results = []
    for i, radar in enumerate(radar_results):
        if isinstance(radar, Exception):
            logger.warning(f"Radar failed for property {str(props[i]['_id'])}: {radar}")
        else:
            results.append(radar)

    return results


# ── Helper ─────────────────────────────────────────────────────────────────

def _doc(doc: dict) -> dict:
    """Generated function header.

    Function: _doc
    Path: backend/routers/ownerhub.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if doc is None:
        return None
    d = dict(doc)
    if "_id" in d:
        d["id"] = str(d.pop("_id"))
    return d
