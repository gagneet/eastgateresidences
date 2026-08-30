"""
Property Taxes Pydantic models.

Covers two domains:
  - CouncilRates : ACT Revenue Office rates and land tax (cached from external API)
  - WaterBills   : Icon Water (ACT) bills, manually entered per unit
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Dict, Optional, Literal


# ─────────────────────────────────────────────────────────────────────────────
# Council Rates (ACT Revenue Office)
# ─────────────────────────────────────────────────────────────────────────────

class CouncilRateResponse(BaseModel):
    """
    Rates data for a single unit for a given financial year.

    Fields sourced from the ACT Revenue API or the council_rates cache collection.
    The land_tax_note is appended locally based on is_owner_occupied.

    ACT Revenue API fields mapped here:
      fixedCharge     → fixed_charge
      variableCharge  → variable_charge (alias: valuation_charge for card compat)
      fesl            → fesl (alias: pfesl for card compat)
      sfl             → sfl (alias: safer_families_levy for card compat)
      healthLevy      → health_levy (added 2023)
      total           → total_rates
    """

    model_config = ConfigDict(extra="ignore")

    unit_number: str
    financial_year: str  # e.g. "2025-26"
    cached_at: Optional[str] = None  # ISO timestamp of last cache refresh

    # AUV details — for strata, block_auv is the BLOCK-level AUV shared by all units
    block_auv: Optional[float] = None  # Block AUV (e.g. $4,200,000 for East Gate 2025)
    auv: Optional[float] = None  # Legacy alias — same as block_auv
    is_estimated_auv: bool = False  # False = confirmed via ACT calculator; True = admin-overridden pending notice
    unit_entitlement_pct: Optional[float] = None  # Unit's % of total block entitlement (e.g. 1.61)

    # Rate components (all in AUD)
    fixed_charge: Optional[float] = None  # $985 residential unit (incl. health levy)
    fixed_charge_rebate: Optional[float] = None
    variable_charge: Optional[float] = None  # f(blockAuv) × unitEntitlement%
    fesl: Optional[float] = None  # $426 Police, Fire & Emergency Services Levy
    sfl: Optional[float] = None  # $60 Safer Families Levy
    health_levy: Optional[float] = None  # $100 Health Levy (2023+)
    total_rates: Optional[float] = None  # Annual total

    # Rates quarterly amounts (computed from annual total using ACT quarterly % splits)
    rates_q1: Optional[float] = None  # Jul-Sep due 31 Aug  (25.2054%)
    rates_q2: Optional[float] = None  # Oct-Dec due 30 Nov  (25.2054%)
    rates_q3: Optional[float] = None  # Jan-Mar due 28 Feb  (24.6575%)
    rates_q4: Optional[float] = None  # Apr-Jun due 31 May  (24.9315%)

    # Legacy aliases for backwards compatibility
    valuation_charge: Optional[float] = None  # alias for variable_charge
    pfesl: Optional[float] = None  # alias for fesl
    safer_families_levy: Optional[float] = None  # alias for sfl

    # Land tax — applies to investment/rental properties only (not owner-occupied)
    land_tax_applicable: bool = False
    land_tax_note: Optional[str] = None
    land_tax_fixed: Optional[float] = None  # $1,693 fixed charge
    land_tax_variable: Optional[float] = None  # g(blockAuv) × unitEntitlement%
    land_tax_total: Optional[float] = None  # Annual total land tax
    land_tax_q1: Optional[float] = None  # Jul-Sep  (25.2054% of annual)
    land_tax_q2: Optional[float] = None  # Oct-Dec  (25.2054% of annual)
    land_tax_q3: Optional[float] = None  # Jan-Mar  (24.6575% of annual)
    land_tax_q4: Optional[float] = None  # Apr-Jun  (24.9315% of annual)

    # Payment tracking (rates only — land tax tracked separately)
    payment_status: str = "unpaid"  # "unpaid" | "partial" | "paid"
    total_paid: float = 0.0

    # Metadata
    source: str = "unavailable"  # "live" | "estimated" | "cache" | "unavailable"
    last_updated: Optional[str] = None  # display-friendly last update timestamp


class CouncilRatePaymentCreate(BaseModel):
    """Body for POST /api/council-rates/{unit_number}/payments."""

    payment_date: str  # ISO date string, e.g. "2025-08-15"
    amount: float = Field(..., gt=0)
    payment_type: Literal["full", "partial"]
    notes: Optional[str] = None
    financial_year: str  # e.g. "2025-26"


class CouncilRatePaymentResponse(BaseModel):
    """Response for a recorded council rate payment."""

    model_config = ConfigDict(extra="ignore")

    id: str
    unit_number: str
    financial_year: str
    payment_date: str
    amount: float
    payment_type: str
    notes: Optional[str] = None
    created_at: str
    created_by: str  # user id of recorder


# ─────────────────────────────────────────────────────────────────────────────
# Water Bills (Icon Water, ACT)
# ─────────────────────────────────────────────────────────────────────────────

class WaterBillCreate(BaseModel):
    """Body for POST /api/water-bills/{unit_number}."""

    quarter: str  # e.g. "Q1 2026"
    billing_period_start: str  # ISO date string
    billing_period_end: str  # ISO date string
    amount: float = Field(..., gt=0)
    due_date: str  # ISO date string
    water_usage_kl: Optional[float] = None  # Kilolitres consumed
    notes: Optional[str] = None


class WaterBillResponse(BaseModel):
    """Response model for a water bill record."""

    model_config = ConfigDict(extra="ignore")

    id: str
    unit_number: str
    quarter: str
    billing_period_start: str
    billing_period_end: str
    days: Optional[int] = None  # number of days in billing period
    amount: float
    due_date: str
    water_usage_kl: Optional[float] = None
    status: str  # "unpaid" | "partial" | "paid"
    amount_paid: float = 0.0
    balance_due: float  # amount - amount_paid
    payment_date: Optional[str] = None
    payment_reference: Optional[str] = None
    notes: Optional[str] = None
    created_at: str
    created_by: str  # user id of creator
    updated_at: Optional[str] = None
    # Calculated supply breakdown (water + sewerage supply charges, not including usage)
    supply_breakdown: Optional[Dict[str, Any]] = None
    # Icon Water (ACT) utility charges are GST-free under Australian tax law
    gst_free: bool = True
    gst_amount: float = 0.0


class WaterChargeEstimate(BaseModel):
    """Response for GET /api/water-bills/estimate — calculated supply charges."""

    start_date: str
    end_date: str
    days: int
    water_supply_charge: float  # Water supply: $0.6616/day
    sewerage_supply_charge: float  # Sewerage supply: $1.6773/day
    supply_total: float  # Combined supply total
    usage_kl: Optional[float] = None  # kL consumed if provided
    water_daily_rate: float
    sewer_daily_rate: float
    # Provider / jurisdiction / effective period for the rates above. Present so a
    # UI can say WHICH schedule a figure came from rather than typing a financial
    # year beside it by hand — see routers/water_bills.ICON_WATER_RATE_SCHEDULE.
    rate_schedule: Optional[Dict[str, Any]] = None
    note: Optional[str] = None


class WaterBillMarkPaidRequest(BaseModel):
    """Body for PATCH /api/water-bills/{bill_id}/mark-paid."""

    payment_date: str  # ISO date string
    payment_reference: Optional[str] = None


class WaterBillMarkPartialRequest(BaseModel):
    """Body for PATCH /api/water-bills/{bill_id}/mark-partial."""

    amount_paid: float = Field(..., gt=0)
    payment_date: str  # ISO date string
    notes: Optional[str] = None


__all__ = [
    "CouncilRateResponse",
    "CouncilRatePaymentCreate",
    "CouncilRatePaymentResponse",
    "WaterBillCreate",
    "WaterBillResponse",
    "WaterBillMarkPaidRequest",
    "WaterBillMarkPartialRequest",
]
