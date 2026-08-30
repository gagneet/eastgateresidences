"""
GAP-FIN-011 (outstanding scope): Payment Approval Workflow Model.

Covers special/ad-hoc payments above $10,000 that are NOT ABA batches —
e.g. large contractor invoices, insurance premiums, emergency capital works.

ABA batch dual-approval (≥$5k) is handled in routers/trust_accounting.py.
This model covers the `payment_approvals` collection for non-ABA high-value payments.

Collection: payment_approvals (building-scoped)

Status flow:
  draft → pending_approval → pending_second_approval → approved | rejected
"""
from __future__ import annotations

from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict

SPECIAL_PAYMENT_DUAL_THRESHOLD = 10_000.00  # Require 2 approvers above this value

PAYMENT_STATUSES = [
    "draft",
    "pending_approval",
    "pending_second_approval",
    "approved",
    "rejected",
    "cancelled",
]

PAYMENT_CATEGORIES = [
    "contractor_invoice",
    "insurance_premium",
    "emergency_capital_works",
    "legal_fees",
    "utility_arrears",
    "other",
]


class SpecialPaymentCreate(BaseModel):
    """Create a special (non-ABA) payment approval request."""
    model_config = ConfigDict(extra="ignore")

    payee_name: str = Field(..., min_length=2, max_length=500)
    payee_bsb: str = Field(..., max_length=20, description="BSB in NNN-NNN format")
    payee_account_number: str = Field(..., min_length=6, max_length=9)
    amount: float = Field(..., gt=0, description="Payment amount in AUD")
    category: str = Field(..., max_length=64, description=f"One of: {PAYMENT_CATEGORIES}")
    description: str = Field(..., min_length=10, max_length=2000)
    invoice_reference: Optional[str] = Field(None, max_length=300)
    invoice_url: Optional[str] = Field(None, max_length=2048, description="Link to uploaded invoice document")
    payment_date: str = Field(..., max_length=64, description="Requested payment date (ISO-8601)")
    fund_type: str = Field(default="admin", max_length=64, description="admin | sinking | capital")
    # max_length on a list field bounds the item count in Pydantic v2 (`max_items` was removed).
    supporting_documents: List[str] = Field(default_factory=list, max_length=100)
    notes: Optional[str] = Field(None, max_length=5_000)


class SpecialPaymentApproval(BaseModel):
    """Approve or reject a special payment."""
    model_config = ConfigDict(extra="ignore")

    approved: bool = Field(..., description="True = approve, False = reject")
    reason: Optional[str] = Field(None, max_length=5_000, description="Required when rejecting")


class SpecialPaymentResponse(BaseModel):
    """Response shape for a payment approval record."""
    model_config = ConfigDict(extra="ignore")

    id: str
    building_id: str
    payee_name: str
    amount: float
    category: str
    description: str
    status: str
    requires_dual_approval: bool
    submitted_by: Optional[str] = None
    first_approved_by: Optional[str] = None
    first_approved_at: Optional[str] = None
    second_approved_by: Optional[str] = None
    second_approved_at: Optional[str] = None
    rejected_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    payment_date: Optional[str] = None
    created_at: str
    updated_at: str
