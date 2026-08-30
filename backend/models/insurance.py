"""
Insurance Policy Management Models

Covers:
- Building insurance at replacement value (UTMA 2011 s.100 — ACT mandatory)
- Public liability ≥ $10M (UTMA 2011 s.100(2) — ACT mandatory)
- Insurance renewal workflow with 3 quotes (NSW PSABAA 2002 s.47)
- Commission/benefit disclosure register
- AGM insurance disclosure report generation
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Dict, List, Optional


class PolicyType(str):
    BUILDING = "building"  # Replacement value cover
    PUBLIC_LIABILITY = "public_liability"  # Common property liability
    VOLUNTARY_WORKERS = "voluntary_workers"
    FIDELITY_GUARANTEE = "fidelity_guarantee"
    OFFICE_BEARERS = "office_bearers"
    MACHINERY_BREAKDOWN = "machinery_breakdown"
    OTHER = "other"


class PolicyStatus(str):
    ACTIVE = "active"
    EXPIRED = "expired"
    PENDING_RENEWAL = "pending_renewal"
    CANCELLED = "cancelled"


class InsuranceQuote(BaseModel):
    """A single insurance quote (NSW: minimum 3 required per renewal)."""
    insurer_name: str
    broker_name: Optional[str] = None
    premium_amount: float = Field(..., gt=0)
    excess_amount: float = 0.0
    cover_amount: float = Field(..., gt=0)
    commission_percent: Optional[float] = None
    commission_amount: Optional[float] = None
    broker_fee: Optional[float] = None
    taxes_levies: Optional[float] = None  # Stamp duty, ESL, etc.
    commission_recipient: Optional[str] = None  # who receives commission
    connected_to_agent: Optional[bool] = None  # NSW disclosure: is insurer connected to agent?
    quote_reference: Optional[str] = None
    quote_date: Optional[str] = None  # ISO-8601
    valid_until: Optional[str] = None  # ISO-8601
    recommended: bool = False
    notes: Optional[str] = None


class InsurancePolicyCreate(BaseModel):
    """Create / renew an insurance policy."""
    building_id: str
    policy_type: str  # PolicyType
    insurer_name: str
    policy_number: str
    cover_amount: float = Field(..., gt=0)
    public_liability_amount: Optional[float] = None  # for public_liability type
    premium_annual: float = Field(..., gt=0)
    excess_amount: float = 0.0
    start_date: str  # ISO-8601
    expiry_date: str  # ISO-8601
    # Disclosure fields
    commission_percent: Optional[float] = None
    commission_amount: Optional[float] = None
    broker_name: Optional[str] = None
    broker_fee: Optional[float] = None
    financial_benefits: Optional[str] = None  # text description of any other benefits
    connected_insurer: Optional[bool] = None
    # NSW workflow: quotes obtained
    quotes: List[InsuranceQuote] = []
    quotes_waived_reason: Optional[str] = None  # if fewer than 3 quotes in NSW
    # Renewal trigger
    renewal_reminder_days: int = 60  # alert days before expiry
    notes: Optional[str] = None


class InsurancePolicyResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    building_id: str
    policy_type: str
    insurer_name: str
    policy_number: str
    cover_amount: float
    public_liability_amount: Optional[float] = None
    premium_annual: float
    excess_amount: float
    start_date: str
    expiry_date: str
    status: str
    commission_percent: Optional[float] = None
    commission_amount: Optional[float] = None
    broker_name: Optional[str] = None
    broker_fee: Optional[float] = None
    financial_benefits: Optional[str] = None
    connected_insurer: Optional[bool] = None
    quotes: List[Dict[str, Any]] = []
    quotes_count: int = 0
    renewal_reminder_days: int = 60
    days_until_expiry: Optional[int] = None
    compliance_status: str = "unknown"  # compliant | non_compliant | warning
    notes: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None
    created_by: str


class AGMInsuranceDisclosure(BaseModel):
    """Insurance disclosure summary for AGM packs (UTMA 2011 s.100 requirement)."""
    building_id: str
    agm_date: str
    policies: List[Dict[str, Any]]
    total_premium: float
    public_liability_amount: Optional[float]
    public_liability_meets_minimum: bool
    building_insurance_exists: bool
    commissions_disclosed: bool
    generated_at: str
    generated_by: str


class InsurancePolicyUpdate(BaseModel):
    """Partial update for an insurance policy — all fields optional."""
    insurer_name: Optional[str] = None
    policy_number: Optional[str] = None
    cover_amount: Optional[float] = None
    public_liability_amount: Optional[float] = None
    premium_annual: Optional[float] = None
    excess_amount: Optional[float] = None
    start_date: Optional[str] = None
    expiry_date: Optional[str] = None
    commission_percent: Optional[float] = None
    commission_amount: Optional[float] = None
    broker_name: Optional[str] = None
    broker_fee: Optional[float] = None
    financial_benefits: Optional[str] = None
    connected_insurer: Optional[bool] = None
    renewal_reminder_days: Optional[int] = None
    notes: Optional[str] = None


class InsuranceBrokerCreate(BaseModel):
    """Register an insurance broker used by this building."""
    building_id: str = ""  # set from auth
    broker_name: str
    company_name: Optional[str] = None
    licence_number: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    afsl_number: Optional[str] = None  # Australian Financial Services Licence
    commission_disclosure: Optional[str] = None  # text description of commission arrangements
    notes: Optional[str] = None


class InsuranceBrokerUpdate(BaseModel):
    """Partial update for a broker record — all fields optional."""
    broker_name: Optional[str] = None
    company_name: Optional[str] = None
    licence_number: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    afsl_number: Optional[str] = None
    commission_disclosure: Optional[str] = None
    notes: Optional[str] = None


class InsuranceBrokerResponse(InsuranceBrokerCreate):
    model_config = ConfigDict(extra="ignore")
    id: str
    is_active: bool = True
    created_at: str
    updated_at: Optional[str] = None
    created_by: str
