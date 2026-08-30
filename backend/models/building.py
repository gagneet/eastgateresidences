"""
Building-related Pydantic models.

Contains models for building assets, registers, compliance tracking, and multi-tenancy.

@featuretrace:building — Building/tenant entity model.
Layer: model
Data flow: frontend settings pages → /api/buildings/* → buildings collection (global).
Related: backend/routers/buildings.py, backend/database.py (TENANT_SCOPED_COLLECTIONS)
"""

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional, Dict, Any


class StateJurisdiction(str, Enum):
    """Australian state/territory jurisdiction — determines levy calculation rules.

    QLD requires dual CSLE/ISLE entitlement schedules (BCCM Act 1997 s46).
    All states permit combined bank accounts with sub-ledger tracking.
    NSW 10% p.a. statutory interest on overdue levies (SSMA 2015 s85).
    """
    NSW = "NSW"
    VIC = "VIC"
    QLD = "QLD"
    ACT = "ACT"
    WA = "WA"
    SA = "SA"
    TAS = "TAS"
    NT = "NT"


class BuildingSettings(BaseModel):
    """Configuration for a specific building/tenant."""
    levy_due_months: List[int] = [3, 6, 9, 12]
    levy_due_day_type: str = "last"  # first | middle | last | custom
    levy_due_custom_dates: Dict[str, str] = {}
    financial_year_start: str = "07-01"  # MM-DD
    timezone: str = "Australia/Sydney"
    currency: str = "AUD"
    email_domain: Optional[str] = None
    migadu_domain: Optional[str] = None
    features: Dict[str, bool] = {
        "finance": True,
        "maintenance": True,
        "chat": True,
        "marketplace": True,
        "ai_intelligence": True,
        "benchmarking": False
    }


class BuildingCreate(BaseModel):
    """Model for creating a new building/tenant."""
    name: str
    slug: str
    address: str
    state: Optional[str] = None
    country: str = "Australia"
    timezone: str = "Australia/Sydney"
    units_count: Optional[int] = 0
    settings: BuildingSettings = BuildingSettings()
    # Jurisdiction & compliance fields
    strata_plan_number: Optional[str] = Field(None, description="e.g. SP13195 — registered with Land Registry")
    state_jurisdiction: Optional[StateJurisdiction] = Field(None, description="Determines levy calculation rules")
    gst_registered: bool = Field(False,
                                 description="ATO GST registration: mandatory when turnover >$150k (non-profit OC)")
    aggregate_unit_entitlement: Optional[int] = Field(None,
                                                      description="Total UOE across all lots — sum of units.entitlement")
    financial_year_end_month: int = Field(6, ge=1, le=12,
                                          description="Month number when financial year ends (default: June=6)")


class BuildingResponse(BaseModel):
    """Complete building/tenant data."""
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    slug: str
    address: str
    state: Optional[str] = None
    country: str
    timezone: str
    units_count: int
    settings: BuildingSettings
    created_at: str
    updated_at: str
    # Branding / Specifics
    logo_url: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    website: Optional[str] = None
    # Jurisdiction & compliance (Phase A1 additions)
    strata_plan_number: Optional[str] = None
    state_jurisdiction: Optional[StateJurisdiction] = None
    gst_registered: bool = False
    aggregate_unit_entitlement: Optional[int] = None
    financial_year_end_month: int = 6


class MembershipResponse(BaseModel):
    """Mapping of a user to a building with roles and units."""
    model_config = ConfigDict(extra="ignore")
    id: str
    user_id: str
    building_id: str
    building_slug: str
    building_name: str
    roles: List[str]
    units: List[str]
    is_primary: bool = False
    created_at: str


class BuildingAssetCreate(BaseModel):
    """Model for creating a new building asset."""
    name: str
    category: str  # e.g., "Keys & Locks", "Utilities", "HVAC", "Fire Safety", "Security", "General"
    description: Optional[str] = None
    location: Optional[str] = None
    serial_number: Optional[str] = None
    manufacturer: Optional[str] = None
    model_number: Optional[str] = None
    installation_date: Optional[str] = None
    warranty_expiry: Optional[str] = None

    # Specific details (e.g., Meter Numbers)
    details: Optional[Dict[str, Any]] = {}

    # Sensitive information (e.g., Master Key numbers, Lock codes)
    # Only viewable by authorized roles
    sensitive_data: Optional[Dict[str, Any]] = {}

    tags: Optional[List[str]] = []


class BuildingAssetResponse(BaseModel):
    """Complete building asset data."""
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    category: str
    description: Optional[str] = None
    location: Optional[str] = None
    serial_number: Optional[str] = None
    manufacturer: Optional[str] = None
    model_number: Optional[str] = None
    installation_date: Optional[str] = None
    warranty_expiry: Optional[str] = None
    details: Dict[str, Any] = {}
    sensitive_data: Dict[str, Any] = {}
    tags: List[str] = []
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by: Optional[str] = None


class BuildingAssetUpdate(BaseModel):
    """Model for updating an existing building asset."""
    name: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    serial_number: Optional[str] = None
    manufacturer: Optional[str] = None
    model_number: Optional[str] = None
    installation_date: Optional[str] = None
    warranty_expiry: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    sensitive_data: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
