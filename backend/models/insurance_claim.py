"""
Insurance Claim related Pydantic models.
"""

from pydantic import BaseModel, ConfigDict
from typing import Any, Dict, List, Optional


class InsuranceClaimStatus:
    REPORTED = "reported"
    UNDER_REVIEW = "under_review"
    LODGED = "lodged"
    IN_PROGRESS = "in_progress"
    FINALIZED = "finalized"
    REJECTED = "rejected"


class InsuranceClaimTimelineEntry(BaseModel):
    """A milestone in the claim lifecycle."""
    milestone: str  # e.g. "Claim lodged with insurer", "Assessor appointed"
    occurred_at: str  # ISO-8601
    recorded_by: str  # user_id
    notes: Optional[str] = None
    amount_cents: Optional[int] = None  # if this milestone involved a payment/reserve


class InsuranceClaimCreate(BaseModel):
    building_id: str
    claim_number: Optional[str] = None
    insurer: str
    policy_id: Optional[str] = None  # link to insurance_policies collection
    incident_date: str
    description: str
    location_description: Optional[str] = None
    estimated_cost_cents: Optional[int] = None
    excess_cents: Optional[int] = None
    linked_work_orders: List[str] = []
    attachments: List[str] = []


class InsuranceClaimUpdate(BaseModel):
    """Partial update model for claim details."""
    claim_number: Optional[str] = None
    settlement_amount_cents: Optional[int] = None
    reserve_amount_cents: Optional[int] = None
    excess_cents: Optional[int] = None
    estimated_cost_cents: Optional[int] = None
    assessor_name: Optional[str] = None
    assessor_contact: Optional[str] = None
    adjuster_reference: Optional[str] = None
    notes: Optional[str] = None


class InsuranceClaimResponse(InsuranceClaimCreate):
    model_config = ConfigDict(extra="ignore")
    id: str
    status: str = InsuranceClaimStatus.REPORTED
    settlement_amount_cents: Optional[int] = None  # final settlement agreed
    reserve_amount_cents: Optional[int] = None  # current reserve estimate
    assessor_name: Optional[str] = None
    assessor_contact: Optional[str] = None
    adjuster_reference: Optional[str] = None
    timeline: List[Dict[str, Any]] = []
    notes: Optional[str] = None
    created_by: str
    created_at: str
    updated_at: str
