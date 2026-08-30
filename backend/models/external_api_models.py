"""
External API Pydantic Models

Request/response models for the versioned external API (/api/v1/…).
"""
from pydantic import BaseModel, Field
from typing import Annotated, List, Optional, Any, Dict


# Per-item bound for the scope/event string lists below. Without it `max_length` on the
# list caps the item COUNT only, leaving each item free to carry a multi-megabyte string.
ScopeStr = Annotated[str, Field(max_length=200)]


# ── API Key Management ─────────────────────────────────────────────────────────

class APIKeyCreate(BaseModel):
    """Request body for creating a new API key."""
    name: str = Field(..., min_length=1, max_length=120, description="Human-readable label for the key")
    # max_length on a list field bounds the item count in Pydantic v2 (`max_items` was removed);
    # per-item length is bounded by the annotated item type below.
    scopes: List[ScopeStr] = Field(default_factory=list, max_length=100,
                                   description="List of permission scopes granted to this key")
    expires_at: Optional[str] = Field(None, max_length=64,
                                      description="ISO 8601 expiry timestamp; null means never expires")
    description: Optional[str] = Field(None, max_length=500)


class APIKeyResponse(BaseModel):
    """API key details returned from list / create endpoints (key_hash is never exposed)."""
    id: str
    name: str
    scopes: List[str]
    is_active: bool
    created_at: str
    expires_at: Optional[str] = None
    last_used_at: Optional[str] = None
    description: Optional[str] = None
    created_by: str


class APIKeyCreateResponse(APIKeyResponse):
    """Returned once on creation — includes the raw key that the caller must store."""
    raw_key: str


# ── Webhook Management ─────────────────────────────────────────────────────────

class WebhookCreate(BaseModel):
    """Request body for registering an outbound webhook."""
    url: str = Field(..., max_length=2048, description="HTTPS endpoint to deliver event payloads to")
    events: List[ScopeStr] = Field(
        ...,
        max_length=100,
        description=(
            "Event types to subscribe to, e.g. "
            "['maintenance.created', 'levy.paid', 'work_order.updated']"
        ),
    )
    secret: Optional[str] = Field(
        None, max_length=256,
        description="Optional HMAC secret used to sign payloads (X-Webhook-Signature header)"
    )
    description: Optional[str] = Field(None, max_length=500)


class WebhookResponse(BaseModel):
    id: str
    url: str
    events: List[str]
    is_active: bool
    created_at: str
    description: Optional[str] = None
    created_by_key: str  # API key id that registered the webhook


# ── Building & Units ──────────────────────────────────────────────────────────

class BuildingInfoResponse(BaseModel):
    building_name: str
    address: Optional[str] = None
    total_units: int
    strata_plan: Optional[str] = None
    building_manager: Optional[str] = None
    contact_email: Optional[str] = None


class UnitSummary(BaseModel):
    unit_number: str
    unit_type: Optional[str] = None
    floor: Optional[str] = None
    is_tenanted: bool = False
    entitlement: Optional[float] = None
    owner_name: Optional[str] = None


class UnitDetail(UnitSummary):
    unit_id: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    car_spaces: Optional[int] = None
    levy_balance: Optional[float] = None


class UnitLevyBalance(BaseModel):
    unit_number: str
    financial_year: str
    opening_balance: float = 0.0
    levied_amount: float = 0.0
    paid_amount: float = 0.0
    closing_balance: float = 0.0
    arrears: float = 0.0


class DefectSummary(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    status: str
    location: Optional[str] = None
    reported_at: str
    resolved_at: Optional[str] = None


class DefectCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    location: Optional[str] = Field(None, max_length=200)
    priority: Optional[str] = Field(None, pattern="^(low|medium|high|urgent)$")


# ── Owners Corporation ─────────────────────────────────────────────────────────

class OCPerformanceSummary(BaseModel):
    total_units: int
    occupied_units: int
    tenanted_units: int
    owner_occupier_units: int
    current_financial_year: str
    total_levy_income_budgeted: float = 0.0
    total_levy_collected: float = 0.0
    collection_rate_pct: float = 0.0
    units_in_arrears: int = 0


class OCLevySummary(BaseModel):
    financial_year: str
    admin_fund_budgeted: float = 0.0
    sinking_fund_budgeted: float = 0.0
    total_budgeted: float = 0.0
    total_collected: float = 0.0
    total_outstanding: float = 0.0
    levy_periods: List[Dict[str, Any]] = Field(default_factory=list)


# ── Finance / Accounts ────────────────────────────────────────────────────────

class FinanceSummaryResponse(BaseModel):
    financial_year: str
    admin_fund_budget: float = 0.0
    admin_fund_actual: float = 0.0
    sinking_fund_budget: float = 0.0
    sinking_fund_actual: float = 0.0
    total_income: float = 0.0
    total_expenses: float = 0.0
    net_position: float = 0.0
    levy_arrears_total: float = 0.0


class BudgetCategoryResponse(BaseModel):
    category_name: str
    fund_type: str
    budgeted_amount: float = 0.0
    actual_amount: float = 0.0
    variance: float = 0.0


class TransactionSummary(BaseModel):
    id: str
    transaction_type: str
    amount: float
    description: Optional[str] = None
    transaction_date: str
    unit_number: Optional[str] = None
    reference: Optional[str] = None


# ── Maintenance & Defects ─────────────────────────────────────────────────────

class MaintenanceRequestSummary(BaseModel):
    id: str
    title: str
    category: Optional[str] = None
    priority: Optional[str] = None
    status: str
    location: Optional[str] = None
    submitted_at: str
    updated_at: Optional[str] = None
    work_order_ids: List[str] = Field(default_factory=list)


class MaintenanceStatusUpdate(BaseModel):
    status: str = Field(..., max_length=64, description="New status value")
    notes: Optional[str] = Field(None, max_length=1000)


# ── Work Orders ───────────────────────────────────────────────────────────────

class WorkOrderSummary(BaseModel):
    id: str
    title: str
    maintenance_request_id: Optional[str] = None
    status: str
    priority: Optional[str] = None
    assigned_vendor: Optional[str] = None
    vendor_name: Optional[str] = None
    estimated_cost: Optional[float] = None
    actual_cost: Optional[float] = None
    created_at: str
    updated_at: Optional[str] = None
    completion_date: Optional[str] = None


# ── Service Provider ──────────────────────────────────────────────────────────

class QuoteSubmit(BaseModel):
    work_order_id: str = Field(..., max_length=100)
    amount: float = Field(..., gt=0)
    description: str = Field(..., min_length=10, max_length=2000)
    valid_until: Optional[str] = Field(None, max_length=64, description="ISO 8601 date the quote expires")
    notes: Optional[str] = Field(None, max_length=1000)


class QuoteResponse(BaseModel):
    id: str
    work_order_id: str
    amount: float
    description: str
    status: str
    submitted_at: str
    valid_until: Optional[str] = None


class InvoiceSubmit(BaseModel):
    work_order_id: str = Field(..., max_length=100)
    amount: float = Field(..., gt=0)
    gst_amount: Optional[float] = Field(None, ge=0)
    description: str = Field(..., min_length=5, max_length=2000)
    invoice_number: str = Field(..., min_length=1, max_length=100)
    invoice_date: str = Field(..., max_length=64, description="ISO 8601 invoice date")
    notes: Optional[str] = Field(None, max_length=1000)


class InvoiceStatusResponse(BaseModel):
    id: str
    work_order_id: str
    invoice_number: str
    amount: float
    gst_amount: Optional[float] = None
    status: str
    submitted_at: str
    approved_at: Optional[str] = None
    paid_at: Optional[str] = None


# ── Generic ──────────────────────────────────────────────────────────────────

class PaginationMeta(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int


class PaginatedResponse(BaseModel):
    data: List[Any]
    meta: PaginationMeta
