"""
Tenancy and OwnerHub data models.

Covers the full lifecycle of rental property management:
- Property listings (Eastgate units + external investment properties)
- Tenancy / lease records
- Rent transactions and ledger
- Routine, entry and exit inspections
- Tenant-submitted maintenance requests
- Property health snapshots
- True Cost of Ownership summaries
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List


class TenancyStatus:
    """Lifecycle states for a tenancy."""
    ACTIVE = "active"
    PENDING = "pending"  # Awaiting move-in
    EXPIRED = "expired"  # Lease ended, tenant vacated
    TERMINATED = "terminated"  # Early termination
    PERIODIC = "periodic"  # Month-to-month after fixed term


class PropertyType:
    """Type of investment property."""
    APARTMENT = "apartment"
    TOWNHOUSE = "townhouse"
    HOUSE = "house"
    UNIT = "unit"
    COMMERCIAL = "commercial"


class InspectionType:
    """Type of property inspection."""
    ENTRY = "entry"
    ROUTINE = "routine"
    EXIT = "exit"
    EMERGENCY = "emergency"


class MaintenanceUrgency:
    """Urgency classification for maintenance requests."""
    ROUTINE = "routine"  # Non-urgent; schedule within 2 weeks
    URGENT = "urgent"  # Schedule within 3 days
    EMERGENCY = "emergency"  # Attend immediately (safety/habitability)


class MaintenanceRequestStatus:
    """Status lifecycle for tenant maintenance requests."""
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    ASSIGNED = "assigned"  # Tradesperson assigned
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CLOSED = "closed"
    REJECTED = "rejected"


class RentPaymentMethod:
    """Accepted rent payment methods."""
    BANK_TRANSFER = "bank_transfer"
    DEFT = "deft"
    BPAY = "bpay"
    CASH = "cash"
    OTHER = "other"


# ── Property ────────────────────────────────────────────────────────────────

class PropertyCreate(BaseModel):
    """Create a new managed investment property."""
    address: str
    suburb: str
    state: str
    postcode: str
    property_type: str = PropertyType.APARTMENT
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    parking_spaces: Optional[int] = 0
    purchase_price: Optional[float] = None
    purchase_date: Optional[str] = None
    current_value: Optional[float] = None
    weekly_rent: Optional[float] = None
    # Eastgate-specific — link to a unit in the strata scheme
    eastgate_unit_number: Optional[str] = None
    # Agent assignment: list of real_estate_agent user IDs managing this property
    assigned_agent_ids: Optional[List[str]] = []
    # Explicit staff visibility: EC/strata/admin staff user IDs allowed by the owner.
    shared_staff_ids: Optional[List[str]] = []
    notes: Optional[str] = None


class PropertyUpdate(BaseModel):
    address: Optional[str] = None
    suburb: Optional[str] = None
    state: Optional[str] = None
    postcode: Optional[str] = None
    current_value: Optional[float] = None
    weekly_rent: Optional[float] = None
    assigned_agent_ids: Optional[List[str]] = None
    shared_staff_ids: Optional[List[str]] = None
    notes: Optional[str] = None


class PropertyResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    owner_id: str
    address: str
    suburb: str
    state: str
    postcode: str
    property_type: str
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    parking_spaces: Optional[int] = 0
    purchase_price: Optional[float] = None
    purchase_date: Optional[str] = None
    current_value: Optional[float] = None
    weekly_rent: Optional[float] = None
    eastgate_unit_number: Optional[str] = None
    assigned_agent_ids: List[str] = []
    shared_staff_ids: List[str] = []
    status: str = "vacant"
    notes: Optional[str] = None
    created_at: str
    updated_at: str


# ── Tenancy ────────────────────────────────────────────────────────────────

class TenancyCreate(BaseModel):
    """Create a new tenancy / lease."""
    property_id: Optional[str] = None  # Can be provided in URL path or request body
    tenant_name: str
    tenant_email: str
    tenant_phone: Optional[str] = None
    tenant_user_id: Optional[str] = None  # Link if tenant has a platform account
    lease_start: str  # ISO date string
    lease_end: str
    rent_amount: float  # Weekly rent
    bond_amount: float
    bond_number: Optional[str] = None
    payment_frequency: str = "weekly"  # weekly | fortnightly | monthly
    payment_method: str = RentPaymentMethod.BANK_TRANSFER
    notes: Optional[str] = None


class TenancyUpdate(BaseModel):
    lease_end: Optional[str] = None
    rent_amount: Optional[float] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class TenancyResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    property_id: str
    tenant_name: str
    tenant_email: str
    tenant_phone: Optional[str] = None
    tenant_user_id: Optional[str] = None
    lease_start: str
    lease_end: str
    rent_amount: float
    bond_amount: float
    bond_number: Optional[str] = None
    payment_frequency: str
    payment_method: str
    status: str
    days_until_expiry: Optional[int] = None
    arrears_amount: float = 0.0
    arrears_days: int = 0
    notes: Optional[str] = None
    created_at: str
    updated_at: str


# ── Rent Transaction ───────────────────────────────────────────────────────

class RentTransactionCreate(BaseModel):
    tenancy_id: str
    amount: float
    transaction_date: str
    type: str = "rent"  # rent | repair_deduction | bond | refund | other
    status: str = "paid"  # paid | overdue | partial
    method: str = RentPaymentMethod.BANK_TRANSFER
    reference: Optional[str] = None
    notes: Optional[str] = None


class RentTransactionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenancy_id: str
    amount: float
    transaction_date: str
    type: str
    status: str
    method: str
    reference: Optional[str] = None
    notes: Optional[str] = None
    created_at: str


# ── Inspection ─────────────────────────────────────────────────────────────

class InspectionCreate(BaseModel):
    property_id: str
    tenancy_id: Optional[str] = None
    inspection_type: str = InspectionType.ROUTINE
    scheduled_date: str
    inspector_name: Optional[str] = None
    notes: Optional[str] = None


class InspectionUpdate(BaseModel):
    completed_date: Optional[str] = None
    condition_score: Optional[int] = Field(None, ge=1, le=10)
    report_url: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None  # scheduled | completed | cancelled


class InspectionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    property_id: str
    tenancy_id: Optional[str] = None
    inspection_type: str
    scheduled_date: str
    completed_date: Optional[str] = None
    condition_score: Optional[int] = None
    report_url: Optional[str] = None
    inspector_name: Optional[str] = None
    notes: Optional[str] = None
    status: str = "scheduled"
    created_at: str


# ── Tenant Maintenance Request ──────────────────────────────────────────────

class MaintenanceRequestCreate(BaseModel):
    """Submitted by a tenant (Eastgate or external)."""
    property_id: Optional[str] = None  # For OwnerHub external properties
    eastgate_unit_number: Optional[str] = None  # For Eastgate in-building requests
    issue_title: str
    issue_description: str
    urgency: str = MaintenanceUrgency.ROUTINE
    photo_urls: Optional[List[str]] = []
    preferred_access_times: Optional[str] = None


class MaintenanceRequestUpdate(BaseModel):
    status: Optional[str] = None
    assigned_tradesperson: Optional[str] = None
    tradesperson_phone: Optional[str] = None
    cost_estimate: Optional[float] = None
    actual_cost: Optional[float] = None
    resolution_notes: Optional[str] = None
    scheduled_date: Optional[str] = None
    completed_date: Optional[str] = None


class MaintenanceRequestMessage(BaseModel):
    """A message in the request thread (bidirectional: tenant ↔ manager/owner/agent)."""
    content: str
    photo_urls: Optional[List[str]] = []


class MaintenanceRequestResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    submitted_by_user_id: str
    submitted_by_name: str
    property_id: Optional[str] = None
    eastgate_unit_number: Optional[str] = None
    issue_title: str
    issue_description: str
    urgency: str
    status: str
    photo_urls: List[str] = []
    assigned_tradesperson: Optional[str] = None
    tradesperson_phone: Optional[str] = None
    cost_estimate: Optional[float] = None
    actual_cost: Optional[float] = None
    resolution_notes: Optional[str] = None
    scheduled_date: Optional[str] = None
    completed_date: Optional[str] = None
    message_count: int = 0
    created_at: str
    updated_at: str


# ── Property Health Score ──────────────────────────────────────────────────

class PropertyHealthSnapshot(BaseModel):
    """Weekly computed property health score for an investment property."""
    model_config = ConfigDict(extra="ignore")
    property_id: str
    computed_at: str
    # Component scores (0–100 each)
    income_stability_score: float  # 25% weight — rent payments, arrears
    expense_volatility_score: float  # 15% weight — unexpected cost spikes
    maintenance_backlog_score: float  # 20% weight — open/pending repairs
    tenant_stability_score: float  # 15% weight — lease risk, tenure length
    capital_risk_score: float  # 15% weight — asset age, capital works
    compliance_score: float  # 10% weight — safety checks, inspections
    # Final composite
    health_score: float  # 0–100 overall
    risk_level: str  # "healthy" | "moderate" | "high" | "critical"
    risk_flags: List[str] = []


# ── True Cost of Ownership ─────────────────────────────────────────────────

class TrueOwnershipCostInput(BaseModel):
    """User-supplied inputs for TCO calculation."""
    property_id: Optional[str] = None  # Provided via URL path; optional in body
    year: Optional[str] = None  # e.g. "2025-2026"; defaults to current year
    annual_rental_income: Optional[float] = None  # Override auto-calculated
    mortgage_balance: Optional[float] = None
    interest_rate: Optional[float] = None  # e.g. 6.2 for 6.2%
    annual_insurance: Optional[float] = None
    council_rates: Optional[float] = None
    water_rates: Optional[float] = None
    property_mgmt_fee_pct: Optional[float] = None  # % of rent, e.g. 8.5
    vacancy_weeks: Optional[int] = 2  # Estimated weeks vacant per year


class TrueOwnershipCostResponse(BaseModel):
    """Full TCO breakdown for a property in a given year."""
    model_config = ConfigDict(extra="ignore")
    property_id: str
    year: str
    # Income
    gross_rental_income: float
    vacancy_loss: float
    net_rental_income: float
    # Fixed costs
    strata_levies: float = 0.0
    insurance: float = 0.0
    council_rates: float = 0.0
    water_rates: float = 0.0
    property_mgmt_fee: float = 0.0
    # Variable costs
    maintenance_costs: float = 0.0
    # Capital costs (unit share of building capital works)
    capital_replacement_cost: float = 0.0
    # Financing
    mortgage_interest: float = 0.0
    # Totals
    total_costs: float
    net_cashflow: float
    net_yield_pct: Optional[float] = None  # % of current property value
    ownership_score: float = 0.0  # 0–100 investment quality score
    # 10-year projection (list of {year, net_cashflow})
    ten_year_projection: List[dict] = []
    computed_at: str


__all__ = [
    "TenancyStatus", "PropertyType", "InspectionType",
    "MaintenanceUrgency", "MaintenanceRequestStatus", "RentPaymentMethod",
    "PropertyCreate", "PropertyUpdate", "PropertyResponse",
    "TenancyCreate", "TenancyUpdate", "TenancyResponse",
    "RentTransactionCreate", "RentTransactionResponse",
    "InspectionCreate", "InspectionUpdate", "InspectionResponse",
    "MaintenanceRequestCreate", "MaintenanceRequestUpdate",
    "MaintenanceRequestMessage", "MaintenanceRequestResponse",
    "PropertyHealthSnapshot",
    "TrueOwnershipCostInput", "TrueOwnershipCostResponse",
]
