from pydantic import BaseModel, ConfigDict, Field
from models.timestamps import IsoTimestamp
from typing import List, Optional


class WorkflowRequestStatus:
    AUTO_RESOLVED = "auto_resolved"
    AWAITING_REVIEW = "awaiting_review"
    IN_PROGRESS = "in_progress"
    AWAITING_RESIDENT = "awaiting_resident"
    CLOSED = "closed"
    OVERDUE = "overdue"


class WorkflowRequestType:
    MAINTENANCE = "maintenance"
    LEVY_QUERY = "levy_query"
    BYLAW_QUERY = "bylaw_query"
    DOCUMENT_REQUEST = "document_request"
    NOISE_COMPLAINT = "noise_complaint"


class ProposalStatus:
    DRAFT = "draft"
    OPEN = "open"
    PASSED = "passed"
    FAILED = "failed"
    WITHDRAWN = "withdrawn"
    ESCALATED_TO_AGM = "escalated_to_agm"


class VotingType:
    SIMPLE_MAJORITY = "simple_majority"
    SPECIAL_RESOLUTION = "special_resolution"
    UNANIMOUS = "unanimous"


class ProposalType:
    EXPENDITURE = "expenditure"
    BYLAW_CHANGE = "bylaw_change"
    IMPROVEMENT = "improvement"
    EMERGENCY = "emergency"


class SavingsCategory:
    VENDOR_COMPARISON = "vendor_comparison"
    VOLUNTEER = "volunteer"
    GROUP_BUY = "group_buy"
    INSURANCE = "insurance"
    ENERGY = "energy"
    DISPUTE_AVOIDED = "dispute_avoided"


class VolunteerEventStatus:
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class WorkflowRequestCreate(BaseModel):
    request_type: str
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=2000)
    lot_id: Optional[str] = None
    unit_number: Optional[str] = None
    priority: str = "normal"


class WorkflowRequestSmartCreate(BaseModel):
    subject: str = Field(..., max_length=200)
    body: str = Field(..., max_length=5000)
    unit_number: Optional[str] = None
    attachments: Optional[List[str]] = None
    is_test_data: bool = False


class WorkflowRequestResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    building_id: str
    request_number: Optional[str] = None
    request_type: str
    title: Optional[str] = None
    description: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    lot_id: Optional[str] = None
    unit_number: Optional[str] = None
    priority: str = "normal"
    status: str
    # Removed conflicting alias: `alias="submitted_by_user_id"` on `submitted_by` claimed
    # the same Mongo key as the explicit `submitted_by_user_id` field. On load, the alias
    # won the key, leaving `submitted_by_user_id` always None. On dump, two keys diverged.
    # Canonical field is `submitted_by_user_id`; `submitted_by` is now a plain alias name.
    submitted_by: Optional[str] = None
    submitted_by_user_id: Optional[str] = None
    submitted_by_email: Optional[str] = None
    assigned_to: Optional[str] = None
    assigned_to_name: Optional[str] = None
    resolution_notes: Optional[str] = None
    auto_resolution_attempted: bool = False
    auto_resolution_confidence: float = 0.0
    auto_resolved: bool = False
    auto_resolution_response: Optional[str] = None
    sla_hours: int = 48
    sla_due_at: Optional[str] = None
    sla_breached: bool = False
    needs_human_review: bool = False
    created_at: IsoTimestamp
    updated_at: IsoTimestamp
    closed_at: Optional[str] = None


class WorkflowRequestStatusUpdate(BaseModel):
    status: str
    resolution_notes: Optional[str] = None
    assigned_to: Optional[str] = None


class ProposalCreate(BaseModel):
    title: str = Field(..., max_length=300)
    description: str = Field(..., max_length=5000)
    proposal_type: str
    voting_type: str = VotingType.SIMPLE_MAJORITY
    amount_cents: Optional[int] = None
    voting_deadline: Optional[str] = None
    documents: List[str] = []


class ProposalResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    proposal_number: str
    title: str
    description: str
    proposal_type: str
    voting_type: str
    status: str
    amount_cents: Optional[int] = None
    voting_deadline: Optional[str] = None
    documents: List[str] = []
    created_by: str
    created_by_name: str
    votes_for: int = 0
    votes_against: int = 0
    votes_abstain: int = 0
    total_lots: int = 0
    outcome_notes: Optional[str] = None
    opened_at: Optional[str] = None
    closed_at: Optional[str] = None
    created_at: IsoTimestamp
    updated_at: IsoTimestamp


class ProposalStatusUpdate(BaseModel):
    status: str
    outcome_notes: Optional[str] = Field(None, max_length=2000)


class VoteRequest(BaseModel):
    lot_id: str
    vote: str  # for | against | abstain


class SavingsEventCreate(BaseModel):
    category: str
    description: str = Field(..., max_length=500)
    original_cost_cents: int
    final_cost_cents: int
    saving_method: str = Field(..., max_length=300)
    resident_summary: str = Field(..., max_length=1000)
    financial_year: str
    evidence_documents: List[str] = []


class SavingsEventResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    category: str
    description: str
    original_cost_cents: int
    final_cost_cents: int
    saved_cents: int
    saving_method: str
    resident_summary: str
    financial_year: str
    evidence_documents: List[str] = []
    recorded_by: str
    recorded_by_name: str
    created_at: IsoTimestamp
    updated_at: IsoTimestamp


class VolunteerEventCreate(BaseModel):
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=2000)
    event_date: str
    location: str = Field(..., max_length=200)
    max_volunteers: Optional[int] = None
    credit_cents_per_hour: int = 0
    estimated_hours: Optional[float] = None


class VolunteerEventResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: str
    event_date: str
    location: str
    status: str
    max_volunteers: Optional[int] = None
    credit_cents_per_hour: int
    estimated_hours: Optional[float] = None
    created_by: str
    created_by_name: str
    registered_count: int = 0
    credits_processed: bool = False
    created_at: IsoTimestamp
    updated_at: IsoTimestamp


class VolunteerRegistrationCreate(BaseModel):
    lot_id: str
    notes: Optional[str] = None


class VolunteerRegistrationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    volunteer_event_id: str
    user_id: str
    user_name: str
    lot_id: str
    notes: Optional[str] = None
    hours_worked: Optional[float] = None
    credit_applied_cents: int = 0
    created_at: str


class VolunteerCompleteRequest(BaseModel):
    apply_credits: bool = False
    credit_allocations: Optional[List[dict]] = None


class WorkflowRunResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    workflow_id: str
    building_id: str
    status: str  # running | completed | failed
    trigger: str
    items_processed: int = 0
    errors: List[str] = []
    started_at: str
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None
    metadata: dict = {}


class BuildingSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    building_id: str
    total_lots: int = 0
    occupied_lots: int = 0
    total_levies_ytd_cents: int = 0
    arrears_cents: int = 0
    arrears_lots: int = 0
    sinking_fund_balance_cents: int = 0
    admin_fund_balance_cents: int = 0
    open_maintenance_requests: int = 0
    overdue_work_orders: int = 0
    open_proposals: int = 0
    volunteer_events_ytd: int = 0
    savings_ytd_cents: int = 0
    health_score: Optional[int] = None
    health_grade: Optional[str] = None
    computed_at: str
    financial_year: str


Proposal = ProposalResponse
SavingsEvent = SavingsEventResponse
VolunteerEvent = VolunteerEventResponse
WorkflowRequest = WorkflowRequestResponse

__all__ = [
    "WorkflowRequestStatus",
    "WorkflowRequestType",
    "WorkflowRequestCreate",
    "WorkflowRequestResponse",
    "WorkflowRequestStatusUpdate",
    "ProposalStatus",
    "ProposalType",
    "VotingType",
    "ProposalCreate",
    "ProposalResponse",
    "ProposalStatusUpdate",
    "VoteRequest",
    "SavingsCategory",
    "SavingsEventCreate",
    "SavingsEventResponse",
    "VolunteerEventStatus",
    "VolunteerEventCreate",
    "VolunteerEventResponse",
    "VolunteerRegistrationCreate",
    "VolunteerRegistrationResponse",
    "VolunteerCompleteRequest",
    "WorkflowRunResponse",
    "BuildingSummary",
]
