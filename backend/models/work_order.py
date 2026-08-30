"""
Work Order related Pydantic models.

Contains models for Work Orders, Quotes, Communications, Attachments,
Approvals, and Invoices within the Work Order lifecycle.
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional


class WorkOrderStatus:
    NEW = "new"
    AWAITING_QUOTES = "awaiting_quotes"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    AWAITING_INVOICE = "awaiting_invoice"
    INVOICE_PENDING = "invoice_pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class QuoteStatus:
    SUBMITTED = "submitted"
    REVIEWED = "reviewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class WorkOrderApprovalDecision:
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_MORE_QUOTES = "request_more_quotes"
    REQUEST_INFORMATION = "request_information"


class WorkOrderBase(BaseModel):
    maintenance_request_id: str
    is_emergency: bool = False
    emergency_override: bool = False
    building_id: str
    lot_number: Optional[str] = Field(None, max_length=50)
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=2000)
    priority: str = Field("normal", max_length=20)  # low, normal, high, urgent
    supplier_type: str
    estimated_cost: Optional[float] = None
    requires_approval: bool = False

    # Digital Twin Linkage
    asset_id: Optional[str] = None
    facility_id: Optional[str] = None
    zone_id: Optional[str] = None
    benefit_group_id: Optional[str] = None

    # GAP-MNT-004: Safe Work Method Statement (WHS Act compliance)
    swms_required: bool = False  # True for high-risk construction work (WHS Regulation 2017)
    swms_document_id: Optional[str] = None  # references documents collection

    # GAP-MNT-005: Before / after inspection photos
    before_photos: List[str] = Field(default_factory=list)  # document IDs
    after_photos: List[str] = Field(default_factory=list)  # document IDs


class WorkOrderCreate(WorkOrderBase):
    pass


class WorkOrderSWMSUpdate(BaseModel):
    """GAP-MNT-004: Attach or replace the SWMS document on a work order."""
    swms_document_id: str = Field(..., description="ID of the uploaded SWMS document")


class WorkOrderPhotoAdd(BaseModel):
    """GAP-MNT-005: Add a photo document ID to before_photos or after_photos."""
    document_id: str = Field(..., description="ID of the uploaded photo document")


class WorkOrderResponse(WorkOrderBase):
    model_config = ConfigDict(extra="ignore")
    id: str
    status: str
    created_by: str
    assigned_vendor: Optional[str] = None
    vendor_name: Optional[str] = None
    approval_status: Optional[str] = None
    completion_date: Optional[str] = None
    created_at: str
    updated_at: str


class QuoteCreate(BaseModel):
    vendor_id: str
    work_order_id: Optional[str] = None
    amount: float
    description: str = Field(..., max_length=2000)
    attachments: List[str] = Field(default_factory=list, max_items=10)


class QuoteResponse(QuoteCreate):
    model_config = ConfigDict(extra="ignore")
    id: str
    submitted_date: str
    selected: bool = False
    status: str = QuoteStatus.SUBMITTED
    vendor_name: Optional[str] = None
    vendor_rating: Optional[float] = None


class WorkOrderCommunicationCreate(BaseModel):
    work_order_id: str
    recipient: str = Field(..., max_length=100)
    subject: str = Field(..., max_length=200)
    message: str = Field(..., max_length=5000)
    attachments: List[str] = Field(default_factory=list, max_items=10)
    source_type: str = Field("internal_message",
                             max_length=50)  # email, internal_message, sms, contractor_update, system_notification


class WorkOrderCommunicationResponse(WorkOrderCommunicationCreate):
    model_config = ConfigDict(extra="ignore")
    id: str
    sender: str
    sender_name: Optional[str] = None
    timestamp: str


class WorkOrderAttachmentCreate(BaseModel):
    work_order_id: str
    file_url: str = Field(..., max_length=500)
    file_type: str = Field(..., max_length=50)
    attachment_type: str = Field("photo",
                                 max_length=50)  # photo, report, invoice, quote, compliance_document, certificate


class WorkOrderAttachmentResponse(WorkOrderAttachmentCreate):
    model_config = ConfigDict(extra="ignore")
    id: str
    uploaded_by: str
    uploaded_by_name: Optional[str] = None
    created_at: str


class WorkOrderApprovalCreate(BaseModel):
    work_order_id: str
    decision: str = Field(..., max_length=50)
    comment: Optional[str] = Field(None, max_length=1000)


class WorkOrderApprovalResponse(WorkOrderApprovalCreate):
    model_config = ConfigDict(extra="ignore")
    id: str
    approver_id: str
    approver_name: Optional[str] = None
    role: str
    ec_position: Optional[str] = None
    timestamp: str


class CommitteeResolution(BaseModel):
    id: str
    work_order_id: str
    title: str
    description: str
    approved_amount: float
    approvals: List[dict]
    generated_at: str


class WorkOrderInvoiceCreate(BaseModel):
    work_order_id: str
    vendor_id: str
    invoice_number: str = Field(..., max_length=100)
    amount: float
    gst: float = 0
    attachments: List[str] = Field(default_factory=list, max_items=10)
    submitted_date: Optional[str] = None
    completion_photos: List[str] = Field(default_factory=list, max_items=10)
    compliance_certificates: List[str] = Field(default_factory=list, max_items=10)

    # Cost Allocation Linkage
    asset_id: Optional[str] = None
    facility_id: Optional[str] = None
    cost_center_id: Optional[str] = None
    benefit_group_id: Optional[str] = None


class WorkOrderInvoiceResponse(WorkOrderInvoiceCreate):
    model_config = ConfigDict(extra="ignore")
    id: str
    approval_status: str = "submitted"  # submitted, pending_approval, approved, rejected
    payment_status: str = "pending"  # pending, paid
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    paid_at: Optional[str] = None
    warnings: List[str] = []


class RecurringWorkOrderCreate(BaseModel):
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=2000)
    supplier_type: str = Field(..., max_length=50)
    assigned_vendor: str
    estimated_cost: float
    frequency: str = Field(..., max_length=20)  # monthly, quarterly, yearly
    start_date: str
    building_id: str


class RecurringWorkOrderResponse(RecurringWorkOrderCreate):
    model_config = ConfigDict(extra="ignore")
    id: str
    last_generated_at: Optional[str] = None
    created_at: str
