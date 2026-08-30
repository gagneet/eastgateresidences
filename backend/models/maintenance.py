"""
Maintenance-related Pydantic models.

Contains models for maintenance requests, contractors, purchase orders, and invoices.
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional


class MaintenanceStatus:
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"


class MaintenanceRequestCreate(BaseModel):
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=2000)
    category: str = Field(..., max_length=100)  # plumbing, electrical, structural, common_area, lift, fire_safety, etc.
    location: str = Field(..., max_length=200)
    priority: str = Field("normal", max_length=20)  # low, normal, high, urgent
    images: List[str] = Field(default_factory=list, max_items=10)
    is_test_data: bool = False


class MaintenanceRequestResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: str
    category: str
    location: str
    priority: str
    status: str
    images: List[str] = Field(default_factory=list)
    submitted_by: str
    submitted_by_name: str
    assigned_contractor: Optional[str] = None
    contractor_name: Optional[str] = None
    estimated_cost: Optional[float] = None
    actual_cost: Optional[float] = None
    purchase_order_id: Optional[str] = None
    invoice_id: Optional[str] = None
    work_order_ids: List[str] = Field(default_factory=list)
    approval_history: List[dict] = Field(default_factory=list)
    notes: List[dict] = Field(default_factory=list)
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None


class ContractorCreate(BaseModel):
    name: str = Field(..., max_length=200)
    abn: Optional[str] = Field(None, max_length=20)
    email: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    address: Optional[str] = Field(None, max_length=300)
    services: List[str] = Field(default_factory=list, max_items=20)  # plumbing, electrical, cleaning, etc.
    notes: Optional[str] = Field(None, max_length=1000)
    insurance_expiry: Optional[str] = Field(None, max_length=100)
    public_liability_expiry: Optional[str] = Field(None, max_length=100)
    workers_comp_expiry: Optional[str] = Field(None, max_length=100)


class ContractorResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    abn: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    services: List[str]
    notes: Optional[str] = None
    insurance_expiry: Optional[str] = None
    public_liability_expiry: Optional[str] = None
    workers_comp_expiry: Optional[str] = None
    rating: Optional[float] = None
    total_jobs: int
    total_spent: float
    created_at: str


class PurchaseOrderCreate(BaseModel):
    maintenance_request_id: str = Field(..., max_length=50)
    contractor_id: str = Field(..., max_length=50)
    description: str = Field(..., max_length=1000)
    amount: float
    due_date: Optional[str] = Field(None, max_length=100)


class PurchaseOrderResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    po_number: str
    maintenance_request_id: str
    contractor_id: str
    contractor_name: str
    description: str
    amount: float
    status: str  # draft, sent, accepted, completed, cancelled
    due_date: Optional[str] = None
    created_by: str
    created_at: str


class InvoiceCreate(BaseModel):
    purchase_order_id: str = Field(..., max_length=50)
    invoice_number: Optional[str] = Field(None, max_length=100)
    amount: float
    gst_amount: float = 0
    notes: Optional[str] = Field(None, max_length=1000)


class InvoiceResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    invoice_number: Optional[str] = None
    purchase_order_id: Optional[str] = None
    po_number: Optional[str] = None
    contractor_id: Optional[str] = None
    contractor_name: Optional[str] = None
    amount: float
    gst_amount: float = 0.0
    total_amount: float = 0.0
    status: str  # pending, approved, paid
    notes: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    paid_at: Optional[str] = None
    created_at: str


__all__ = [
    "MaintenanceStatus",
    "MaintenanceRequestCreate",
    "MaintenanceRequestResponse",
    "ContractorCreate",
    "ContractorResponse",
    "PurchaseOrderCreate",
    "PurchaseOrderResponse",
    "InvoiceCreate",
    "InvoiceResponse",
]
