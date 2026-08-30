"""
Digital Twin Models — Pydantic v2

Models for modeling the building's physical assets, facilities, service zones,
and benefit groups for maintenance intelligence and cost allocation.
"""

from datetime import datetime

from pydantic import BaseModel, Field
from typing import Annotated, List, Optional


# ── Input length bounds (security audit 2026-08-26, finding 3) ────────────────
# These models are used BOTH to validate request bodies (the POST handlers splat
# the client dict into them) and as `response_model` for the GET handlers, so the
# bounds below are set well above anything a real asset register carries. The
# `benefit_groups`/`zones`/`facilities`/`building_assets` collections are empty in
# live Mongo, so nothing stored can trip them.
#
# The PUT handlers do NOT go through these models — see the *Update models at the
# bottom of this file, which routers/digital_twin.py uses to keep the same bounds
# on the update path.
MAX_CODE_LEN = 100     # ids, categories, ISO dates
MAX_NAME_LEN = 300     # names
MAX_TEXT_LEN = 5_000   # descriptions, notes
MAX_LOT_NUMBERS = 5_000  # a lot list is bounded by the building's lot count


class AllocationRule(BaseModel):
    """Rule for allocating costs based on benefit groups."""
    allocation_type: str = Field(..., max_length=MAX_CODE_LEN)  # unit_entitlement | equal_split | usage_based | fixed_percentage
    notes: Optional[str] = Field(None, max_length=MAX_TEXT_LEN)


class BenefitGroup(BaseModel):
    """Groups of lots that benefit from a particular facility or asset."""
    id: str = Field(..., max_length=MAX_CODE_LEN)
    building_id: str = Field(..., max_length=MAX_CODE_LEN)
    name: str = Field(..., max_length=MAX_NAME_LEN)  # ALL_LOTS | APARTMENTS_ONLY | TOWNHOUSES_ONLY | BASEMENT_USERS | RETAIL
    description: Optional[str] = Field(None, max_length=MAX_TEXT_LEN)
    # max_length on a list bounds the item COUNT in Pydantic v2; the annotated item
    # type bounds each entry.
    lot_numbers: List[Annotated[str, Field(max_length=MAX_CODE_LEN)]] = Field(
        default=[], max_length=MAX_LOT_NUMBERS)  # Empty if ALL_LOTS
    allocation_rule: AllocationRule
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class Zone(BaseModel):
    """Physical areas of the complex."""
    id: str = Field(..., max_length=MAX_CODE_LEN)
    building_id: str = Field(..., max_length=MAX_CODE_LEN)
    name: str = Field(..., max_length=MAX_NAME_LEN)  # Apartment Tower | Townhouse Block | Basement Garage | Shared Perimeter
    description: Optional[str] = Field(None, max_length=MAX_TEXT_LEN)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class Facility(BaseModel):
    """Aggregates multiple assets into a functional system."""
    id: str = Field(..., max_length=MAX_CODE_LEN)
    building_id: str = Field(..., max_length=MAX_CODE_LEN)
    name: str = Field(..., max_length=MAX_NAME_LEN)  # Lift System | Fire Safety Systems | Basement Ventilation
    category: str = Field(..., max_length=MAX_CODE_LEN)
    zone_id: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    benefit_group_id: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    notes: Optional[str] = Field(None, max_length=MAX_TEXT_LEN)
    health_score: float = 100.0
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class BuildingAsset(BaseModel):
    """Individual physical components within a facility."""
    id: str = Field(..., max_length=MAX_CODE_LEN)
    building_id: str = Field(..., max_length=MAX_CODE_LEN)
    name: str = Field(..., max_length=MAX_NAME_LEN)  # Roof | Lift Motor | Garage Door Motor | Water Pump
    category: str = Field(..., max_length=MAX_CODE_LEN)
    facility_id: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    zone_id: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    benefit_group_id: Optional[str] = Field(None, max_length=MAX_CODE_LEN)

    installation_date: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    expected_lifespan_years: int = 20
    replacement_cost_estimate: float = 0.0

    maintenance_frequency_months: int = 12
    last_service_date: Optional[str] = Field(None, max_length=MAX_CODE_LEN)

    risk_score: float = 0.0
    health_score: float = 100.0

    notes: Optional[str] = Field(None, max_length=MAX_TEXT_LEN)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class UsageMetric(BaseModel):
    """Metric for usage-based cost allocation (future use)."""
    id: str = Field(..., max_length=MAX_CODE_LEN)
    asset_id: str = Field(..., max_length=MAX_CODE_LEN)
    lot_id: str = Field(..., max_length=MAX_CODE_LEN)
    usage_value: float
    period_start: str = Field(..., max_length=MAX_CODE_LEN)
    period_end: str = Field(..., max_length=MAX_CODE_LEN)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# ── Partial update models ─────────────────────────────────────────────────────
# The PUT handlers in routers/digital_twin.py used to accept a bare `dict` and
# `$set` it verbatim, which both bypassed every bound above and let a caller write
# arbitrary new keys into the document. These models carry the same bounds as their
# full counterparts, with every field optional so a partial update still works.
# Immutable fields (id, building_id, created_at) are absent by construction.

class ZoneUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=MAX_NAME_LEN)
    description: Optional[str] = Field(None, max_length=MAX_TEXT_LEN)


class FacilityUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=MAX_NAME_LEN)
    category: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    zone_id: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    benefit_group_id: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    notes: Optional[str] = Field(None, max_length=MAX_TEXT_LEN)
    health_score: Optional[float] = None


class BuildingAssetUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=MAX_NAME_LEN)
    category: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    facility_id: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    zone_id: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    benefit_group_id: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    installation_date: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    expected_lifespan_years: Optional[int] = None
    replacement_cost_estimate: Optional[float] = None
    maintenance_frequency_months: Optional[int] = None
    last_service_date: Optional[str] = Field(None, max_length=MAX_CODE_LEN)
    risk_score: Optional[float] = None
    health_score: Optional[float] = None
    notes: Optional[str] = Field(None, max_length=MAX_TEXT_LEN)


class BenefitGroupUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=MAX_NAME_LEN)
    description: Optional[str] = Field(None, max_length=MAX_TEXT_LEN)
    lot_numbers: Optional[List[Annotated[str, Field(max_length=MAX_CODE_LEN)]]] = Field(
        None, max_length=MAX_LOT_NUMBERS)
    allocation_rule: Optional[AllocationRule] = None
