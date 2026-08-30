"""
Document Request Models

State machine:
  submitted → auto_fulfilled  (document is public and auto-matched)
  submitted → manager_review  (requires manager to locate and attach document)
  manager_review → fulfilled  (manager attaches document)
  manager_review → denied     (manager declines with reason)
  any → cancelled             (requester withdraws)

Statutory deadlines:
  ACT UTMA 2011 s.116 — 14 days to provide access to records on request
  NSW SSMA 2015 s.182 — 14 days to provide inspection of records
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from typing import Optional


class DocumentRequestStatus:
    SUBMITTED = "submitted"
    AUTO_FULFILLED = "auto_fulfilled"
    MANAGER_REVIEW = "manager_review"
    FULFILLED = "fulfilled"
    DENIED = "denied"
    CANCELLED = "cancelled"

    ALL = [SUBMITTED, AUTO_FULFILLED, MANAGER_REVIEW, FULFILLED, DENIED, CANCELLED]
    OPEN = [SUBMITTED, MANAGER_REVIEW]


class DocumentRequestType:
    """Standard document types that owners commonly request."""
    AGM_MINUTES = "agm_minutes"
    FINANCIAL_STATEMENTS = "financial_statements"
    INSURANCE_CERTIFICATE = "insurance_certificate"
    BUILDING_PLANS = "building_plans"
    BY_LAWS = "by_laws"
    OWNERS_ROLL = "owners_roll"
    LEVY_NOTICE = "levy_notice"
    MAINTENANCE_RECORDS = "maintenance_records"
    MEETING_NOTICES = "meeting_notices"
    OTHER = "other"

    ALL = [
        AGM_MINUTES, FINANCIAL_STATEMENTS, INSURANCE_CERTIFICATE, BUILDING_PLANS,
        BY_LAWS, OWNERS_ROLL, LEVY_NOTICE, MAINTENANCE_RECORDS, MEETING_NOTICES, OTHER,
    ]
    # Types that are automatically fulfilled if a matching public document exists
    AUTO_FULFIL_ELIGIBLE = {AGM_MINUTES, BY_LAWS, MEETING_NOTICES, INSURANCE_CERTIFICATE}


# Statutory deadline days by jurisdiction
_DEADLINE_DAYS = {"ACT": 14, "NSW": 14, "default": 14}


class DocumentRequestCreate(BaseModel):
    """Owner submits a document request."""
    document_type: str  # DocumentRequestType.*
    description: str  # what specifically they need
    date_range_from: Optional[str] = None  # ISO date — e.g. for "minutes from 2024"
    date_range_to: Optional[str] = None
    urgency: Optional[str] = None  # "standard" | "urgent"
    notes: Optional[str] = None


class DocumentRequestFulfil(BaseModel):
    """Manager fulfils a request — attaches document."""
    document_id: Optional[str] = None  # id from documents collection
    fulfil_notes: Optional[str] = None  # optional message to requester


class DocumentRequestDeny(BaseModel):
    """Manager denies a request with a reason."""
    denial_reason: str


class DocumentRequestResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    building_id: str
    requester_id: str
    requester_unit: Optional[str] = None
    document_type: str
    description: str
    date_range_from: Optional[str] = None
    date_range_to: Optional[str] = None
    urgency: Optional[str] = "standard"
    status: str
    requested_at: str
    due_by: str  # statutory deadline (requested_at + 14 days)
    fulfilled_at: Optional[str] = None
    fulfilled_by: Optional[str] = None
    document_id: Optional[str] = None  # fulfilled document reference
    fulfil_notes: Optional[str] = None
    denial_reason: Optional[str] = None
    notes: Optional[str] = None
    is_overdue: bool = False
