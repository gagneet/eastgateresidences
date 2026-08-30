"""
Resident Request Pydantic Models.

Contains models for all types of resident requests including:
- Insurance claims
- Insurance enquiries
- Pet requests
- Access control requests (fobs, garage remotes, access cards)
- Alteration requests (renovations, modifications)
- Reimbursement requests

All request types follow a similar pattern: Create model for submission,
Response model for returned data including status and review information.
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional, Dict


# ==================== INSURANCE CLAIM MODELS ====================

class InsuranceClaimCreate(BaseModel):
    """Model for creating a new insurance claim."""
    claim_type: str = Field(..., max_length=100)  # e.g., "property_damage", "water_leak", "fire", "theft"
    incident_date: str = Field(..., max_length=100)  # ISO date string when incident occurred
    description: str = Field(..., max_length=2000)  # Detailed description of the claim
    estimated_cost: float  # Estimated cost of damages
    attachments: Optional[List[str]] = Field(default_factory=list, max_items=10)  # Base64 encoded files or URLs


class InsuranceClaimResponse(BaseModel):
    """Complete insurance claim data including review status."""
    model_config = ConfigDict(extra="ignore")
    id: str  # UUID identifier
    claim_type: str
    incident_date: str
    description: str
    estimated_cost: float
    attachments: Optional[List[str]]
    status: str  # "submitted", "under_review", "approved", "rejected"
    submitted_by: str  # User ID
    submitted_by_name: str  # User full name
    claim_number: str  # Format: "CLM-{first8chars}"
    reviewed_by: Optional[str] = None  # User ID of reviewer
    reviewed_by_name: Optional[str] = None  # Reviewer full name
    reviewed_at: Optional[str] = None  # ISO datetime
    notes: List[Dict[str, str]] = []  # [{"by": str, "at": str, "content": str}]
    created_at: str  # ISO datetime
    updated_at: str  # ISO datetime


# ==================== INSURANCE ENQUIRY MODELS ====================

class InsuranceEnquiryCreate(BaseModel):
    """Model for creating an insurance-related enquiry."""
    subject: str = Field(..., max_length=200)  # Subject of the enquiry
    question: str = Field(..., max_length=2000)  # The actual question being asked


class InsuranceEnquiryResponse(BaseModel):
    """Complete insurance enquiry data including answer."""
    model_config = ConfigDict(extra="ignore")
    id: str  # UUID identifier
    subject: str
    question: str
    status: str  # "pending", "answered"
    submitted_by: str  # User ID
    submitted_by_name: str  # User full name
    answer: Optional[str] = None  # The answer provided by admin
    answered_by: Optional[str] = None  # User ID of answerer
    answered_by_name: Optional[str] = None  # Answerer full name
    answered_at: Optional[str] = None  # ISO datetime
    created_at: str  # ISO datetime
    updated_at: str  # ISO datetime


# ==================== PET REQUEST MODELS ====================

class PetRequestCreate(BaseModel):
    """Model for requesting permission to keep a pet."""
    pet_type: str = Field(..., max_length=50)  # e.g., "dog", "cat", "bird", "fish"
    pet_name: str = Field(..., max_length=100)  # Name of the pet
    breed: str = Field(..., max_length=100)  # Breed of the pet
    weight: Optional[float] = None  # Weight in kg
    age: Optional[int] = None  # Age in years
    description: Optional[str] = Field(None, max_length=1000)  # Description and reason for request
    reason: Optional[str] = Field(None, max_length=1000)  # Alias for description (used by frontend form)
    size: Optional[str] = Field(None, max_length=50)  # "small", "medium", "large"
    council_registration: Optional[str] = Field(None, max_length=100)  # Council registration number
    vaccination_current: Optional[bool] = None  # Vaccinations up to date
    house_trained: Optional[bool] = None  # Pet is house-trained
    vaccination_records: Optional[List[str]] = Field(default_factory=list, max_items=10)  # Base64 encoded documents
    pet_photo: Optional[str] = Field(None, max_length=2000000)  # Base64 encoded image

    def get_description(self) -> str:
        """Return description, falling back to reason field."""
        return self.description or self.reason or ""


class PetRequestResponse(BaseModel):
    """Complete pet request data including approval status."""
    model_config = ConfigDict(extra="ignore")
    id: str  # UUID identifier
    pet_type: str
    pet_name: str
    breed: str
    weight: Optional[float]
    age: Optional[int]
    description: str
    vaccination_records: Optional[List[str]]
    pet_photo: Optional[str]
    status: str  # "pending", "approved", "rejected"
    conditions: Optional[str] = None  # Approval conditions (e.g., "must be kept on leash")
    submitted_by: str  # User ID
    submitted_by_name: str  # User full name
    unit_number: str  # From current_user
    reviewed_by: Optional[str] = None  # User ID of reviewer
    reviewed_by_name: Optional[str] = None  # Reviewer full name
    reviewed_at: Optional[str] = None  # ISO datetime
    notes: List[Dict[str, str]] = []  # [{"by": str, "at": str, "content": str}]
    created_at: str  # ISO datetime
    updated_at: str  # ISO datetime


# ==================== ACCESS CONTROL REQUEST MODELS ====================

class AccessControlRequestCreate(BaseModel):
    """Model for requesting access control items (fobs, cards, remotes)."""
    access_type: str = Field(..., max_length=50)  # "fob", "garage_remote", "access_card", "visitor_card", "other"
    quantity: int  # Number of items requested
    reason: str = Field(..., max_length=500)  # Reason for request


class AccessControlRequestResponse(BaseModel):
    """Complete access control request data including cost and issuance."""
    model_config = ConfigDict(extra="ignore")
    id: str  # UUID identifier
    access_type: str
    quantity: int
    reason: str
    status: str  # "pending", "approved", "rejected", "issued"
    cost: float  # Auto-calculated (cost per item * quantity)
    submitted_by: str  # User ID
    submitted_by_name: str  # User full name
    unit_number: str  # From current_user
    approved_by: Optional[str] = None  # User ID of approver
    approved_by_name: Optional[str] = None  # Approver full name
    approved_at: Optional[str] = None  # ISO datetime
    issued_at: Optional[str] = None  # ISO datetime (when status = "issued")
    created_at: str  # ISO datetime
    updated_at: str  # ISO datetime


# ==================== ALTERATION REQUEST MODELS ====================

class AlterationRequestCreate(BaseModel):
    """Model for requesting approval for unit alterations/renovations."""
    alteration_type: str = Field(..., max_length=100)  # e.g., "flooring", "painting", "structural", "bathroom"
    description: str = Field(..., max_length=2000)  # Detailed description of proposed alterations
    contractor_details: Optional[str] = Field(None, max_length=500)  # Contractor name and contact info
    proposed_start_date: str = Field(..., max_length=100)  # ISO date string
    estimated_duration: str = Field(..., max_length=100)  # e.g., "2 weeks"
    plans_attachments: Optional[List[str]] = Field(default_factory=list, max_items=10)  # Base64 encoded plans/drawings


class AlterationRequestResponse(BaseModel):
    """Complete alteration request data including approval conditions."""
    model_config = ConfigDict(extra="ignore")
    id: str  # UUID identifier
    alteration_type: str
    description: str
    contractor_details: Optional[str]
    proposed_start_date: str
    estimated_duration: str
    plans_attachments: Optional[List[str]]
    status: str  # "pending", "approved", "rejected", "completed"
    conditions: Optional[str] = None  # Approval conditions
    submitted_by: str  # User ID
    submitted_by_name: str  # User full name
    unit_number: str  # From current_user
    reviewed_by: Optional[str] = None  # User ID of reviewer
    reviewed_by_name: Optional[str] = None  # Reviewer full name
    reviewed_at: Optional[str] = None  # ISO datetime
    approval_notes: List[Dict[str, str]] = []  # [{"by": str, "at": str, "content": str}]
    created_at: str  # ISO datetime
    updated_at: str  # ISO datetime


# ==================== REIMBURSEMENT REQUEST MODELS ====================

class ReimbursementRequestCreate(BaseModel):
    """Model for requesting reimbursement of expenses."""
    description: str = Field(..., max_length=1000)  # What the reimbursement is for
    amount: float  # Amount to be reimbursed
    expense_date: str = Field(..., max_length=100)  # Date expense was incurred
    category: str = Field(..., max_length=100)  # e.g., "common_area_repair", "emergency_expense"
    receipt_attachments: List[str] = Field(..., max_items=10)  # Base64 encoded receipts (required)
    bank_account_name: str = Field(..., max_length=200)  # Account holder name
    bsb: str = Field(..., max_length=20)  # Bank BSB number
    account_number: str = Field(..., max_length=50)  # Bank account number


class ReimbursementRequestResponse(BaseModel):
    """Complete reimbursement request data including payment status."""
    model_config = ConfigDict(extra="ignore")
    id: str  # UUID identifier
    description: str
    amount: float
    expense_date: str
    category: str
    receipt_attachments: List[str]
    bank_account_name: str
    bsb: str
    account_number: str
    status: str  # "pending", "approved", "rejected", "paid"
    submitted_by: str  # User ID
    submitted_by_name: str  # User full name
    unit_number: str  # From current_user
    approved_by: Optional[str] = None  # User ID of approver
    approved_by_name: Optional[str] = None  # Approver full name
    approved_at: Optional[str] = None  # ISO datetime
    paid_at: Optional[str] = None  # ISO datetime (when status = "paid")
    payment_reference: Optional[str] = None  # Payment reference number
    approval_notes: List[Dict[str, str]] = []  # [{"by": str, "at": str, "content": str}]
    created_at: str  # ISO datetime
    updated_at: str  # ISO datetime


__all__ = [
    # Insurance Claims
    "InsuranceClaimCreate",
    "InsuranceClaimResponse",
    # Insurance Enquiries
    "InsuranceEnquiryCreate",
    "InsuranceEnquiryResponse",
    # Pet Requests
    "PetRequestCreate",
    "PetRequestResponse",
    # Access Control
    "AccessControlRequestCreate",
    "AccessControlRequestResponse",
    # Alterations
    "AlterationRequestCreate",
    "AlterationRequestResponse",
    # Reimbursements
    "ReimbursementRequestCreate",
    "ReimbursementRequestResponse",
]
