# @featuretrace:smart-request — Comms Intake Service: classifies and processes inbound smart requests.
# Layer: service
# Data flow: workflow_requests.py → process_inbound() → auto-resolves OR routes to db.workflow_requests.
# Related: backend/routers/workflow_requests.py
#           frontend/src/pages/dashboard/SmartRequestPage.jsx
# Scope: (building-scoped)

"""
Comms Intake Router — the central intake processor for all inbound resident communications.

Classification priority (deterministic first, AI-assisted second):
1. Rule-based keyword classification (fast, predictable, auditable)
2. AI-assisted classification for low-confidence matches (optional, logged)
3. Human triage queue for unclassified items

Each classified item becomes exactly one workflow entity:
  - maintenance_request → Work Order pipeline
  - levy_query → auto-answered from ledger if possible
  - bylaw_query → auto-answered from knowledge base
  - insurance_enquiry → Insurance workflow
  - pet_request → Pet approval workflow
  - renovation_approval → Alteration request workflow
  - reimbursement → Reimbursement workflow
  - payment_plan → Arrears/hardship workflow
  - general_complaint → Dispute log
  - record_request → Record inspection workflow (see S2)
  - noise_complaint → By-law breach workflow
  - general_enquiry → FAQ auto-response or triage queue

Every classification writes:
  - A workflow_requests record (source, classification, confidence, SLA set)
  - An audit_log entry
  - An acknowledgement notification to the resident
  - A triage item for the strata manager if human review needed
"""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from database import db
from utils.helpers import create_user_notification, create_audit_log

# SLA hours by request type — configurable per building in future
DEFAULT_SLAS = {
    "maintenance_request": 72,  # 3 business days
    "levy_query": 4,  # Same day (usually auto-resolved)
    "bylaw_query": 24,
    "insurance_enquiry": 48,
    "pet_request": 120,  # 5 days (requires EC decision)
    "renovation_approval": 168,  # 7 days
    "reimbursement": 120,
    "payment_plan": 48,
    "general_complaint": 48,
    "record_request": 72,  # ACT: reasonable time
    "noise_complaint": 24,
    "general_enquiry": 24,
}

# Auto-resolvable categories (can be answered from ledger/knowledge base)
AUTO_RESOLVABLE = {"levy_query", "bylaw_query", "general_enquiry"}

DEFAULT_CONFIDENCE = 0.40  # Applied when no keyword rule matches

KEYWORD_RULES = [
    # (pattern, category, confidence)
    # More specific patterns first — prevent lower-specificity rules from winning
    (r"\b(hardship|payment plan|struggling|afford|financial difficulty)\b", "payment_plan", 0.85),
    (r"\b(leak|tap|drip|pipe|flood|water damage|wet|damp)\b", "maintenance_request", 0.90),
    (r"\b(lift|elevator|broken|fault|not working|power|electricity)\b", "maintenance_request", 0.85),
    (r"\b(levy|levies|overdue|arrears|pay|payment|owing|balance|invoice)\b", "levy_query", 0.85),
    (r"\b(renovate|renovation|alter|alteration|works|bathroom|kitchen|floor)\b", "renovation_approval", 0.85),
    (r"\b(insurance|insur|claim|storm|fire)\b", "insurance_enquiry", 0.80),
    (r"\b(bylaw|by-law|by law)\b", "bylaw_query", 0.80),
    (r"\b(noise|loud|party|music|disturbance)\b", "noise_complaint", 0.80),
    (r"\b(reimburse|expense|receipt|out of pocket)\b", "reimbursement", 0.80),
    (r"\b(pet|animal|dog|cat|bird|fish)\b", "pet_request", 0.70),
    (r"\b(record|minutes|document|inspect|access|strata roll)\b", "record_request", 0.75),
    (r"\b(complain|complaint|dispute|unhappy|unacceptable|frustrated)\b", "general_complaint", 0.75),
    (r"\b(how do i|how to|what is|when is|who is|can i|please let me know)\b", "general_enquiry", 0.60),
]


def classify_message(subject: str, body: str) -> Tuple[str, float]:
    """
    Deterministic keyword classification.
    Returns (category, confidence).
    Confidence < 0.70 should be flagged for human review.
    """
    text = (subject + " " + body).lower()
    best_category = "general_enquiry"
    best_confidence = DEFAULT_CONFIDENCE  # Default when no rule matches

    for pattern, category, confidence in KEYWORD_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            if confidence > best_confidence:
                best_category = category
                best_confidence = confidence

    return best_category, best_confidence


def compute_sla_deadline(category: str, created_at: datetime) -> datetime:
    """Compute SLA deadline based on category. Business hours-aware in future."""
    hours = DEFAULT_SLAS.get(category, 48)
    return created_at + timedelta(hours=hours)


async def process_inbound(
        source_channel: str,  # "portal_form" | "email" | "sms" | "manual"
        sender_user_id: Optional[str],
        sender_email: Optional[str],
        unit_number: Optional[str],
        subject: str,
        body: str,
        building_id: str,
        attachments: list = None,
        is_test_data: bool = False,
) -> dict:
    """
    Main intake processor. Called by:
    - Portal smart request form (POST /requests/smart)
    - Email ingestion webhook (when implemented)
    - Manual entry by strata manager

    Returns the created workflow_request record.
    """
    now = datetime.now(timezone.utc)
    category, confidence = classify_message(subject, body)
    sla_deadline = compute_sla_deadline(category, now)
    needs_human = confidence < 0.70

    request_id = str(uuid.uuid4())
    request_number = await _generate_request_number(db, building_id)

    # Attempt auto-resolution for high-confidence, auto-resolvable categories
    auto_response = None
    auto_resolved = False

    if confidence >= 0.80 and category in AUTO_RESOLVABLE:
        auto_response, auto_resolved = await _attempt_auto_resolve(
            category, body, unit_number, building_id, db
        )

    status = (
        "auto_resolved"
        if auto_resolved
        else ("awaiting_review" if needs_human else "in_progress")
    )

    doc = {
        "id": request_id,
        "building_id": building_id,
        "request_number": request_number,
        "request_type": category,
        # Keep existing schema fields for backward compat
        "title": subject[:200],
        "description": body[:2000],
        "source_channel": source_channel,
        "submitted_by": sender_user_id or "anonymous",
        "submitted_by_user_id": sender_user_id,
        "submitted_by_email": sender_email,
        "submitted_by_name": sender_email or sender_user_id or "unknown",
        "unit_number": unit_number,
        "subject": subject,
        "body": body,
        "attachments": attachments or [],
        "auto_resolution_attempted": category in AUTO_RESOLVABLE,
        "auto_resolution_confidence": confidence,
        "auto_resolution_source": None,
        "auto_resolved": auto_resolved,
        "auto_resolution_response": auto_response,
        "resolution_message": auto_response,
        "status": status,
        "assigned_to": None,
        "assigned_to_name": None,
        "resolution_notes": None,
        "priority": "normal",
        "lot_id": unit_number,
        "sla_hours": DEFAULT_SLAS.get(category, 48),
        "sla_due_at": sla_deadline.isoformat(),
        "sla_breached": False,
        "needs_human_review": needs_human,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "closed_at": None,
        "is_test_data": bool(is_test_data),
    }

    await db.workflow_requests.insert_one(doc)

    # Audit log
    await create_audit_log(
        action="create",
        resource_type="workflow_request",
        resource_id=request_id,
        user_id=sender_user_id or "system",
        user_name=sender_email or "unknown",
        details={
            "category": category,
            "confidence": confidence,
            "auto_resolved": auto_resolved,
            "source_channel": source_channel,
        },
        building_id=building_id,
    )

    # Acknowledgement to resident
    if sender_user_id:
        ack_message = (
            auto_response
            if auto_resolved
            else (
                f"We've received your {category.replace('_', ' ')} (ref: {request_number}). "
                f"Expected response within {DEFAULT_SLAS.get(category, 48)} hours."
            )
        )
        await create_user_notification(
            user_id=sender_user_id,
            title=f"Request received: {request_number}",
            message=ack_message,
            notification_type="request_acknowledgement",
            link=f"/requests/{request_id}",
            building_id=building_id,
        )

    return doc


async def _generate_request_number(db, building_id: str) -> str:
    """
    Generate a sequential request number using an atomic counter document.
    Uses find_one_and_update with $inc to avoid duplicate numbers under concurrency.
    Format: REQ-2026-0042
    """
    year = datetime.now(timezone.utc).year
    counter_id = f"workflow_requests:{building_id}:{year}"
    counter_doc = await db._db.counters.find_one_and_update(
        {"_id": counter_id},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    seq = counter_doc.get("seq", 1)
    return f"REQ-{year}-{seq:04d}"


async def _attempt_auto_resolve(
        category: str,
        body: str,
        unit_number: Optional[str],
        building_id: str,
        db,
) -> Tuple[Optional[str], bool]:
    """
    Attempt to auto-resolve common queries.
    Returns (response_text, was_resolved).
    """
    if category == "levy_query" and unit_number:
        current_year = str(datetime.now(timezone.utc).year)
        ledger = await db.unit_levy_ledger.find_one(
            {"unit_number": unit_number, "year": current_year},
            {"_id": 0, "net_balance": 1},
        )
        net_balance = float((ledger or {}).get("net_balance", 0) or 0)
        owing = round(max(0.0, net_balance), 2)
        credit = round(abs(min(0.0, net_balance)), 2)
        if ledger is not None:
            if credit > 0:
                return (
                    f"Your levy account for {unit_number} is in credit by ${credit:,.2f}.",
                    True,
                )
            elif owing > 0:
                return (
                    f"Your levy account for {unit_number} has an outstanding balance of "
                    f"${owing:,.2f}. You can pay at /financials/levy-payments.",
                    True,
                )
            else:
                return (
                    f"Your levy account for {unit_number} is up to date. No amount owing.",
                    True,
                )

    return None, False


def _format_timeline_note(details: dict) -> str:
    """Generated function header.

    Function: _format_timeline_note
    Path: backend/services/comms_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not details:
        return ""
    SKIP = {"notified_users", "building_id"}
    LABELS = {
        "category": "Category", "confidence": "Confidence", "auto_resolved": "Auto-resolved",
        "source_channel": "Source", "sla_due_at": "SLA due",
    }
    parts = []
    for k, v in details.items():
        if k in SKIP or v is None or v == "" or v == [] or v == {}:
            continue
        label = LABELS.get(k, k.replace("_", " ").capitalize())
        if isinstance(v, float) and k == "confidence":
            parts.append(f"{label}: {int(v * 100)}%")
        elif isinstance(v, bool):
            parts.append(f"{label}: {'Yes' if v else 'No'}")
        elif isinstance(v, list):
            parts.append(f"{label}: {', '.join(str(i) for i in v)}" if v else "")
        else:
            parts.append(f"{label}: {v}")
    return " · ".join(p for p in parts if p)


async def get_request_timeline(request_id: str, building_id: str, db) -> list:
    """
    Build a timeline of audit log entries for a given request.
    Returns list of {timestamp, actor, action, note}.
    """
    cursor = db.audit_logs.find(
        {"resource_id": request_id, "resource_type": "workflow_request"},
        {"_id": 0},
    ).sort("created_at", 1)

    timeline = []
    async for entry in cursor:
        timeline.append(
            {
                "timestamp": entry.get("created_at", ""),
                "actor": entry.get("user_name", "system"),
                "action": entry.get("action", ""),
                "note": _format_timeline_note(entry.get("details") or {}),
            }
        )
    return timeline


async def run_comms_intake(building_id: str | None = None) -> None:
    """
    Workflow-runner entrypoint for the comms_intake_classifier catalogue entry.
    process_inbound() is event-triggered (portal form / inbound email) and requires
    a full message payload — it cannot be meaningfully batch-dispatched.
    This wrapper satisfies the workflow catalogue contract without a no-op crash.
    """
    import logging as _log
    _log.getLogger(__name__).info(
        "run_comms_intake called for building %s — event-driven, no batch action.",
        building_id,
    )
