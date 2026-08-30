# @featuretrace:land-tax — Council Rates router: fetches ACT Revenue rates and land tax via external API.
# Layer: router
# Data flow: LandTaxCard.jsx → /council-rates/{unit} → ACT Revenue API (24h cache) → db.council_rates.
# Related: frontend/src/components/dashboard/LandTaxCard.jsx
#           backend/services/tax_service.py
# Scope: (building-scoped)

"""
Council Rates router module -- ACT Revenue Office rates and land tax per unit.

Collections used:
  - council_rates          : 24-hour cache of ACT Revenue API responses per unit per year
  - council_rate_settings  : block AUV configuration set by admin (one per financial year)
  - council_rate_payments  : manually recorded rate payment history per unit

External API (2023+):
  - POST https://api.revenue.act.gov.au/calculator/v1/residentialRatesLandTax
    Body: { ratesYear, auv, unit, unitEntitlement, foreignOwnershipPercentage }

    IMPORTANT — for strata/unit-title properties:
      auv                = Average Unimproved Value (AUD) of the BLOCK (not per unit)
                           East Gate Residences, Section 75 Block 4: $4,200,000 (2025)
      unitEntitlement    = unit's share of block entitlement as a % (0-100)
                           e.g. TH017: entitlement=161, total=10000 → 1.61%
      ratesYear          = integer year, e.g. 2025 for FY 2025-26
      unit               = true for strata title, false for standalone
      foreignOwnershipPercentage = 0 for domestic ownership

    The API returns both `rates` AND `landTax` in one response:
      rates.variableCharge  = f(blockAuv) × unitEntitlement%  (marginal rate on block AUV)
      landTax.quarterOne/Two/Three/Four = quarterly land tax amounts

Auth rules:
  - Owners     : view/add-payment for their own unit only
  - EC / Chairman / Strata Manager / Super Admin : view and add-payment for any unit
"""

import asyncio
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer

from database import db
from models.property_taxes import (
    CouncilRateResponse,
    CouncilRatePaymentCreate,
    CouncilRatePaymentResponse,
)
from models.user import UserRole
from utils.auth import get_current_user, get_current_building, effective_role
from utils.permissions import get_user_permissions
from utils.helpers import create_audit_log, get_current_timestamp

# -------------------------------------------------------------------------

router = APIRouter(prefix="")
security = HTTPBearer(auto_error=False)

# Correct ACT Revenue Office API endpoint (uses api.revenue.act.gov.au, NOT www)
ACT_RATES_API = "https://api.revenue.act.gov.au/calculator/v1/residentialRatesLandTax"
CACHE_TTL_HOURS = 24
SETTINGS_COLLECTION = "council_rate_settings"

# Default block AUV — East Gate Residences, Section 75, Block 4, Denman Prospect ACT 2611.
# Verified via ACT Revenue calculator 2025. Per-building values are stored in
# council_rate_settings (set via PUT /api/council-rates/settings) and take priority.
_DEFAULT_BLOCK_AUV = 4_200_000.0

# Default total unit entitlement — East Gate Residences (sum of all 87 units = 10,000).
# Per-building value is stored in council_rate_settings.total_block_entitlement.
_TOTAL_BLOCK_ENTITLEMENT = 10_000

# ACT quarterly split percentages (non-leap year, used for both rates and land tax)
_QUARTERLY_SPLITS = {
    "q1": 25.2054,  # Jul–Sep
    "q2": 25.2054,  # Oct–Dec
    "q3": 24.6575,  # Jan–Mar
    "q4": 24.9315,  # Apr–Jun
}


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/routers/council_rates.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _financial_year_to_rates_year(financial_year: str) -> int:
    """
    Convert "2025-26" → 2025 (the integer year the ACT Revenue API expects).
    """
    try:
        return int(financial_year[:4])
    except (ValueError, TypeError):
        return 2025


def _is_privileged(user: dict) -> bool:
    """Return True for roles that can view any unit."""
    return effective_role(user) in (
        UserRole.SUPER_ADMIN,
        UserRole.STRATA_ADMIN,
        UserRole.EC_MEMBER,
        UserRole.STRATA_MANAGER,
    )


async def _get_council_settings(financial_year: str, building_id: str) -> dict:
    """
    Retrieve council rate settings document for the given financial year and building.
    Returns an empty dict if not yet configured.
    """
    doc = await db[SETTINGS_COLLECTION].find_one(
        {"financial_year": financial_year, "building_id": building_id}, {"_id": 0}
    )
    return doc or {}


async def _get_unit_info(unit_number: str, building_id: str) -> dict:
    """
    Fetch unit entitlement and type from the units collection.
    Returns a dict with 'entitlement' and 'unit_type' (or None if not found).
    """
    unit = await db.units.find_one(
        {"unit_number": unit_number.upper(), "building_id": building_id},
        {"_id": 0, "entitlement": 1, "unit_type": 1, "is_owner_occupied": 1},
    )
    return unit or {}


def _get_block_auv(settings: dict) -> tuple[float, bool]:
    """
    Return the block AUV to pass to the ACT Revenue API.

    For strata properties, the AUV is for the ENTIRE BLOCK, not per unit.
    All units in the building share the same block AUV.

    Priority:
      1. settings["block_auv"]  — admin-configured from the official rates notice
      2. _DEFAULT_BLOCK_AUV     — East Gate fallback only; other buildings must have
                                  a council_rate_settings record before AUV is correct

    Returns (block_auv: float, is_estimated: bool).
    is_estimated is False once admin enters the real AUV from the rates notice.
    """
    if settings.get("block_auv"):
        is_configured = bool(settings.get("is_configured"))
        return float(settings["block_auv"]), not is_configured
    return _DEFAULT_BLOCK_AUV, False  # default is confirmed, not estimated


async def _fetch_from_act_api(unit_number: str, financial_year: str, building_id: str) -> Optional[dict]:
    """
    Call the ACT Revenue residential rates + land tax calculator API.

    For strata properties, `auv` is the BLOCK AUV (shared across all units in the
    building). The API applies the unit entitlement % internally to derive the
    unit's individual share of both the rates variable charge and land tax.

    Returns the raw API response dict annotated with helper keys, or None on error.
    """
    settings = await _get_council_settings(financial_year, building_id)
    unit_info = await _get_unit_info(unit_number, building_id)
    if not unit_info:
        return None

    entitlement = unit_info.get("entitlement")
    if not entitlement:
        return None

    block_auv, is_estimated = _get_block_auv(settings)
    total_entitlement = float(settings.get("total_block_entitlement") or _TOTAL_BLOCK_ENTITLEMENT)
    ue_pct = round((entitlement / total_entitlement) * 100, 4)
    rates_year = _financial_year_to_rates_year(financial_year)

    payload = {
        "ratesYear": rates_year,
        "auv": round(block_auv, 2),  # BLOCK AUV — not per-unit
        "unit": True,  # strata title scheme
        "unitEntitlement": ue_pct,
        "foreignOwnershipPercentage": 0,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(ACT_RATES_API, json=payload)
            if resp.status_code == 200:
                raw = resp.json()
                raw["_block_auv"] = block_auv
                raw["_is_estimated_block_auv"] = is_estimated
                raw["_ue_pct"] = ue_pct
                return raw
            print(f"[council_rates] ACT API returned {resp.status_code} for {unit_number}: {resp.text[:200]}")
    except Exception as exc:
        print(f"[council_rates] ACT API error for {unit_number}: {exc}")
    return None


def _parse_act_response(raw: dict) -> dict:
    """
    Map ACT Revenue API response to our internal cache schema.

    ACT Revenue API response structure (confirmed 2025):
      {
        "rates": {
          "fixedCharge":        float,   -- $985 (residential unit, incl. health levy)
          "fixedChargeRebate":  float,   -- typically 0
          "variableCharge":     float,   -- f(blockAuv) × unitEntitlement%
          "fesl":               float,   -- $426 Police, Fire & Emergency Services Levy
          "sfl":                float,   -- $60 Safer Families Levy
          "healthLevy":         float,   -- $100 ACT Health Levy
          "total":              float    -- sum of all above components
        },
        "landTax": {
          "fixedCharge":              float,   -- $1,693
          "variableCharge":           float,   -- g(blockAuv) × unitEntitlement%
          "foreignOwnershipSurcharge":float,
          "quarterOne":               float,   -- Jul-Sep  (25.2054% of annual)
          "quarterTwo":               float,   -- Oct-Dec  (25.2054% of annual)
          "quarterThree":             float,   -- Jan-Mar  (24.6575% of annual)
          "quarterFour":              float,   -- Apr-Jun  (24.9315% of annual)
          "total":                    float
        }
      }

    For 2025, TH017 (block AUV=$4,200,000, UE=1.61%) produces:
      Rates: $985 + $482.78 + $426 + $60 + $100 = $2,053.78
      Land Tax: $1,693 + $818.20 = $2,511.20
    """
    rates = raw.get("rates") or {}
    land_tax = raw.get("landTax") or {}
    block_auv = raw.get("_block_auv")
    is_estimated = raw.get("_is_estimated_block_auv", False)
    ue_pct = raw.get("_ue_pct")

    total_rates = rates.get("total")

    # Compute quarterly rates amounts using ACT standard quarterly splits.
    # The API returns rates as an annual total only; quarterly split is calculated locally.
    if total_rates:
        rates_q1 = round(total_rates * _QUARTERLY_SPLITS["q1"] / 100, 2)
        rates_q2 = round(total_rates * _QUARTERLY_SPLITS["q2"] / 100, 2)
        rates_q3 = round(total_rates * _QUARTERLY_SPLITS["q3"] / 100, 2)
        rates_q4 = round(total_rates - rates_q1 - rates_q2 - rates_q3, 2)  # remainder avoids rounding drift
    else:
        rates_q1 = rates_q2 = rates_q3 = rates_q4 = None

    return {
        "block_auv": block_auv,
        "is_estimated_auv": is_estimated,  # kept as is_estimated_auv for model compat
        "unit_entitlement_pct": ue_pct,
        # Rate components
        "fixed_charge": rates.get("fixedCharge"),
        "fixed_charge_rebate": rates.get("fixedChargeRebate"),
        "variable_charge": rates.get("variableCharge"),
        "valuation_charge": rates.get("variableCharge"),  # legacy alias
        "fesl": rates.get("fesl"),
        "pfesl": rates.get("fesl"),  # legacy alias
        "sfl": rates.get("sfl"),
        "safer_families_levy": rates.get("sfl"),  # legacy alias
        "health_levy": rates.get("healthLevy"),
        "total_rates": total_rates,
        # Rates quarterly amounts (computed locally from annual total)
        "rates_q1": rates_q1,  # Jul-Sep due 31 Aug
        "rates_q2": rates_q2,  # Oct-Dec due 30 Nov
        "rates_q3": rates_q3,  # Jan-Mar due 28 Feb
        "rates_q4": rates_q4,  # Apr-Jun due 31 May
        # Land tax — from API (quarterly amounts provided directly by ACT Revenue API)
        "land_tax_total": land_tax.get("total"),
        "land_tax_fixed": land_tax.get("fixedCharge"),
        "land_tax_variable": land_tax.get("variableCharge"),
        "land_tax_q1": land_tax.get("quarterOne"),  # Jul-Sep  25.2054%
        "land_tax_q2": land_tax.get("quarterTwo"),  # Oct-Dec  25.2054%
        "land_tax_q3": land_tax.get("quarterThree"),  # Jan-Mar  24.6575%
        "land_tax_q4": land_tax.get("quarterFour"),  # Apr-Jun  24.9315%
    }


# -------------------------------------------------------------------------
# GET /api/council-rates/settings  (MUST be before /{unit_number} to avoid path conflict)
# -------------------------------------------------------------------------

@router.get("/council-rates/settings")
async def get_council_rate_settings(
        financial_year: str = Query("2025-26"),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Retrieve council rate AUV configuration for a financial year.
    Privileged roles only.
    """
    if not _is_privileged(current_user):
        raise HTTPException(status_code=403, detail="Not authorized to view rate settings")

    settings = await _get_council_settings(financial_year, building_id)
    if not settings:
        return {
            "financial_year": financial_year,
            "block_auv": None,
            "is_configured": False,
            "total_block_entitlement": _TOTAL_BLOCK_ENTITLEMENT,
            "note": (
                "No block AUV configured for this building and financial year. "
                "Update with the official AUV from the rates notice via PUT /council-rates/settings."
            ),
        }
    return {**settings, "is_configured": True}


# -------------------------------------------------------------------------
# PUT /api/council-rates/settings
# -------------------------------------------------------------------------

@router.put("/council-rates/settings")
async def update_council_rate_settings(
        body: dict,
        financial_year: str = Query("2025-26"),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Update council rate AUV configuration for a financial year.
    Privileged finance roles only (Super Admin / Strata Admin / Strata Manager / EC Member),
    or any role with can_manage_finances.

    Accepted body fields:
      - default_townhouse_auv   : float
      - default_apartment_auv   : float
      - units                   : dict  { "TH017": { "auv": 420000 }, ... }
    """
    permissions = get_user_permissions(current_user)
    if not _is_privileged(current_user) and not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized to update rate settings")

    allowed = {"block_auv", "is_configured", "total_block_entitlement"}
    update = {k: v for k, v in body.items() if k in allowed}
    update["financial_year"] = financial_year
    update["updated_at"] = _now()
    update["updated_by"] = current_user.get("id", "")

    await db[SETTINGS_COLLECTION].update_one(
        {"financial_year": financial_year, "building_id": building_id},
        {"$set": update},
        upsert=True,
    )

    # Invalidate cache so next request re-fetches live data
    await db.council_rates.delete_many({"financial_year": financial_year, "building_id": building_id})

    return {"success": True, "message": "Council rate settings updated. Cache cleared."}


# -------------------------------------------------------------------------
# GET /api/council-rates/{unit_number}
# -------------------------------------------------------------------------

@router.get("/council-rates/{unit_number}", response_model=CouncilRateResponse)
async def get_council_rates(
        unit_number: str,
        financial_year: str = Query("2025-26", description="Financial year, e.g. 2025-26"),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Fetch ACT council rates for a unit.

    Returns live data from the ACT Revenue API when the cache is stale (>24 h).
    Falls back to cached data on API failure.  Returns source: live | cache | unavailable.

    Owners may only query their own unit number.
    Privileged roles may query any unit.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized to view finances")

    # Ownership gate for non-privileged users
    if not _is_privileged(current_user):
        owner_unit = current_user.get("unit_number", "")
        if owner_unit.upper() != unit_number.upper():
            raise HTTPException(
                status_code=403,
                detail="You may only view council rates for your own unit",
            )

    unit_number = unit_number.upper()

    # ----- Check cache -----
    cached = await db.council_rates.find_one(
        {"unit_number": unit_number, "financial_year": financial_year, "building_id": building_id},
        {"_id": 0},
    )

    cache_valid = False
    if cached:
        try:
            cached_ts = datetime.fromisoformat(cached["cached_at"])
            if datetime.now(timezone.utc) - cached_ts < timedelta(hours=CACHE_TTL_HOURS):
                cache_valid = True
        except (KeyError, ValueError):
            pass

    # Helper: land-tax note for investment properties
    async def _unit_is_investment(unum: str) -> bool:
        """Generated function header.

        Function: _unit_is_investment
        Path: backend/routers/council_rates.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        unit_doc = await db.units.find_one({"unit_number": unum, "building_id": building_id},
                                           {"_id": 0, "is_owner_occupied": 1})
        return not (unit_doc or {}).get("is_owner_occupied", True) if unit_doc else False

    def _build_land_tax(is_investment: bool):
        """Generated function header.

        Function: _build_land_tax
        Path: backend/routers/council_rates.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if is_investment:
            return (
                True,
                "This unit appears to be an investment/rental property. "
                "ACT land tax may apply — please consult the ACT Revenue Office or your accountant.",
            )
        return False, None

    # ----- Serve from cache if valid -----
    if cache_valid and cached:
        is_inv = await _unit_is_investment(unit_number)
        land_tax_applicable, land_tax_note = _build_land_tax(is_inv)
        # Fetch payment totals for this unit/year
        total_paid, payment_status = await _get_payment_summary(unit_number, financial_year, building_id,
                                                                cached.get("total_rates"))
        return CouncilRateResponse(
            **cached,
            land_tax_applicable=land_tax_applicable,
            land_tax_note=land_tax_note,
            payment_status=payment_status,
            total_paid=total_paid,
            source="cache",
        )

    # ----- Attempt live fetch -----
    raw = await _fetch_from_act_api(unit_number, financial_year, building_id)
    is_inv = await _unit_is_investment(unit_number)
    land_tax_applicable, land_tax_note = _build_land_tax(is_inv)

    if raw:
        parsed = _parse_act_response(raw)
        now_iso = _now()
        cache_doc = {
            "unit_number": unit_number,
            "financial_year": financial_year,
            "building_id": building_id,
            "cached_at": now_iso,
            "auv": parsed.get("block_auv"),  # keep auv populated for model compat
            **parsed,
        }
        cache_set = {k: v for k, v in cache_doc.items() if k not in ("unit_number", "financial_year", "building_id")}
        await db.council_rates.update_one(
            {"unit_number": unit_number, "financial_year": financial_year, "building_id": building_id},
            {"$set": cache_set},
            upsert=True,
        )
        total_paid, payment_status = await _get_payment_summary(unit_number, financial_year, building_id,
                                                                parsed.get("total_rates"))
        return CouncilRateResponse(
            **cache_doc,
            land_tax_applicable=land_tax_applicable,
            land_tax_note=land_tax_note,
            payment_status=payment_status,
            total_paid=total_paid,
            source="live" if not parsed.get("is_estimated_auv") else "estimated",
        )

    # ----- Fall back to stale cache -----
    if cached:
        total_paid, payment_status = await _get_payment_summary(unit_number, financial_year, building_id,
                                                                cached.get("total_rates"))
        return CouncilRateResponse(
            **cached,
            land_tax_applicable=land_tax_applicable,
            land_tax_note=land_tax_note,
            payment_status=payment_status,
            total_paid=total_paid,
            source="cache",
        )

    return CouncilRateResponse(
        unit_number=unit_number,
        financial_year=financial_year,
        land_tax_applicable=land_tax_applicable,
        land_tax_note=land_tax_note,
        source="unavailable",
    )


async def _get_payment_summary(unit_number: str, financial_year: str, building_id: str, total_rates: Optional[float]) -> \
tuple[float, str]:
    """
    Calculate total payments and payment status for a unit/year.
    Returns (total_paid, payment_status).
    """
    payments = await db.council_rate_payments.find(
        {"unit_number": unit_number, "financial_year": financial_year, "building_id": building_id},
        {"_id": 0, "amount": 1},
    ).to_list(200)

    total_paid = sum(p.get("amount", 0) for p in payments)

    if total_paid == 0:
        payment_status = "unpaid"
    elif total_rates and total_paid >= total_rates:
        payment_status = "paid"
    else:
        payment_status = "partial"

    return round(total_paid, 2), payment_status


# -------------------------------------------------------------------------
# POST /api/council-rates/{unit_number}/payments
# -------------------------------------------------------------------------

@router.post("/council-rates/{unit_number}/payments", response_model=CouncilRatePaymentResponse)
async def record_council_rate_payment(
        unit_number: str,
        data: CouncilRatePaymentCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Record a council rate payment against a unit.

    Owners may record payments only for their own unit (requires can_view_finances).
    Privileged roles require can_manage_finances.
    """
    permissions = get_user_permissions(current_user)
    unit_number = unit_number.upper()

    if _is_privileged(current_user):
        if not permissions.can_manage_finances:
            raise HTTPException(status_code=403, detail="Not authorized to manage finances")
    else:
        if not permissions.can_view_finances:
            raise HTTPException(status_code=403, detail="Not authorized to record payments")
        owner_unit = current_user.get("unit_number", "")
        if owner_unit.upper() != unit_number:
            raise HTTPException(
                status_code=403,
                detail="You may only record payments for your own unit",
            )

    payment_id = str(uuid.uuid4())
    now_iso = _now()

    payment_doc = {
        "id": payment_id,
        "building_id": building_id,
        "unit_number": unit_number,
        "financial_year": data.financial_year,
        "payment_date": data.payment_date,
        "amount": data.amount,
        "payment_type": data.payment_type,
        "notes": data.notes,
        "created_at": now_iso,
        "created_by": current_user["id"],
    }

    await db.council_rate_payments.insert_one(payment_doc)

    asyncio.create_task(create_audit_log(
        action="council_rate_payment_recorded",
        resource_type="council_rate_payment",
        resource_id=payment_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={
            "unit_number": unit_number,
            "amount": data.amount,
            "financial_year": data.financial_year,
            "payment_type": data.payment_type,
        },
    ))

    return CouncilRatePaymentResponse(**payment_doc)


# -------------------------------------------------------------------------
# GET /api/council-rates/{unit_number}/payments
# -------------------------------------------------------------------------

@router.get("/council-rates/{unit_number}/payments", response_model=List[CouncilRatePaymentResponse])
async def list_council_rate_payments(
        unit_number: str,
        financial_year: Optional[str] = Query(None, description="Filter by financial year"),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    List recorded council rate payments for a unit.

    Owners may only list payments for their own unit (requires can_view_finances).
    Privileged roles may list payments for any unit.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized to view finances")

    unit_number = unit_number.upper()

    if not _is_privileged(current_user):
        owner_unit = current_user.get("unit_number", "")
        if owner_unit.upper() != unit_number:
            raise HTTPException(
                status_code=403,
                detail="You may only view payments for your own unit",
            )

    query: dict = {"unit_number": unit_number, "building_id": building_id}
    if financial_year:
        query["financial_year"] = financial_year

    payments = (
        await db.council_rate_payments.find(query, {"_id": 0})
        .sort("payment_date", -1)
        .to_list(200)
    )
    return [CouncilRatePaymentResponse(**p) for p in payments]
