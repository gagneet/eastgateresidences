"""Event type and stream definitions for the StrataOS event bus."""
from enum import Enum


class EventStream(str, Enum):
    FINANCIAL = "strataos:financial"
    GOVERNANCE = "strataos:governance"
    OPERATIONS = "strataos:operations"
    COMMUNITY = "strataos:community"


class EventType(str, Enum):
    # Financial
    LEVY_CREDIT_APPLIED = "levy_credit_applied"
    VOLUNTEER_CREDITS_APPLIED = "volunteer_credits_applied"
    SAVINGS_EVENT_CREATED = "savings_event_created"
    WORK_ORDER_INVOICE_APPROVED = "work_order_invoice_approved"
    # Governance
    PROPOSAL_CREATED = "proposal_created"
    PROPOSAL_VOTE_CAST = "proposal_vote_cast"
    PROPOSAL_OUTCOME_RECORDED = "proposal_outcome_recorded"
    # Operations
    REQUEST_CREATED = "request_created"
    REQUEST_AUTO_RESOLVED = "request_auto_resolved"
    REQUEST_ESCALATED = "request_escalated"
    WORK_ORDER_STATUS_CHANGED = "work_order_status_changed"
    PARCEL_LOGGED = "parcel_logged"
    SLA_BREACH_DETECTED = "sla_breach_detected"
    # Community
    VOLUNTEER_EVENT_CREATED = "volunteer_event_created"
    VOLUNTEER_REGISTERED = "volunteer_registered"
    LEASE_EXPIRY_WARNING = "lease_expiry_warning"
    BUILDING_SUMMARY_RECOMPUTED = "building_summary_recomputed"


# Stream routing — which stream each event type belongs to
EVENT_STREAM_MAP: dict[EventType, EventStream] = {
    EventType.LEVY_CREDIT_APPLIED: EventStream.FINANCIAL,
    EventType.VOLUNTEER_CREDITS_APPLIED: EventStream.FINANCIAL,
    EventType.SAVINGS_EVENT_CREATED: EventStream.FINANCIAL,
    EventType.WORK_ORDER_INVOICE_APPROVED: EventStream.FINANCIAL,
    EventType.PROPOSAL_CREATED: EventStream.GOVERNANCE,
    EventType.PROPOSAL_VOTE_CAST: EventStream.GOVERNANCE,
    EventType.PROPOSAL_OUTCOME_RECORDED: EventStream.GOVERNANCE,
    EventType.REQUEST_CREATED: EventStream.OPERATIONS,
    EventType.REQUEST_AUTO_RESOLVED: EventStream.OPERATIONS,
    EventType.REQUEST_ESCALATED: EventStream.OPERATIONS,
    EventType.WORK_ORDER_STATUS_CHANGED: EventStream.OPERATIONS,
    EventType.PARCEL_LOGGED: EventStream.OPERATIONS,
    EventType.SLA_BREACH_DETECTED: EventStream.OPERATIONS,
    EventType.VOLUNTEER_EVENT_CREATED: EventStream.COMMUNITY,
    EventType.VOLUNTEER_REGISTERED: EventStream.COMMUNITY,
    EventType.LEASE_EXPIRY_WARNING: EventStream.COMMUNITY,
    EventType.BUILDING_SUMMARY_RECOMPUTED: EventStream.COMMUNITY,
}
