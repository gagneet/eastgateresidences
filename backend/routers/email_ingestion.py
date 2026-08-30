"""
Inbound email ingestion endpoint.

Receives webhook POST from email provider (Mailgun/SendGrid/Migadu).
Parses sender, subject, body, attachments.
Passes to comms_intake_service.process_inbound().

Currently: endpoint exists but logs to audit and queues for manual review.
Future: full provider integrations.

To activate with Mailgun: configure MX record for requests@eastgateresidences.com.au
and set MAILGUN_WEBHOOK_SIGNING_KEY in .env.
"""
import hashlib
import hmac
import os
import time

import logging
from fastapi import APIRouter, Request, HTTPException, Depends

from database import db
from request_context import set_ctx_building_id
from services.comms_intake_service import process_inbound

router = APIRouter(prefix="/ingestion", tags=["Email Ingestion"])
logger = logging.getLogger(__name__)

_MAILGUN_SIGNING_KEY = os.environ.get("MAILGUN_WEBHOOK_SIGNING_KEY", "")


def _verify_mailgun_signature(timestamp: str, token: str, signature: str) -> bool:
    """Verify Mailgun webhook HMAC-SHA256 signature."""
    if not _MAILGUN_SIGNING_KEY:
        # Signing key not configured — skip verification only when explicitly
        # opted out via env var (development only). Log a warning so it is
        # visible in production logs if misconfigured.
        skip = os.environ.get("EMAIL_WEBHOOK_SKIP_VERIFY", "").lower() == "true"
        if skip:
            logger.warning(
                "Mailgun webhook signature verification is disabled "
                "(EMAIL_WEBHOOK_SKIP_VERIFY=true). Do NOT use in production."
            )
        return skip
    # Replay-protection: reject timestamps older than 5 minutes
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except (ValueError, TypeError):
        return False
    expected = hmac.new(
        _MAILGUN_SIGNING_KEY.encode("utf-8"),
        f"{timestamp}{token}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/email/inbound")
async def receive_inbound_email(request: Request):
    """
    Inbound email webhook. Validates Mailgun HMAC-SHA256 signature.
    Extracts: from_email, subject, body_text, attachments.
    Looks up user by from_email → gets unit_number.
    Calls process_inbound().
    """
    # This is a foundation stub as per TASK C1-05
    try:
        data = await request.json()
    except Exception:
        # Some providers send form-data
        form_data = await request.form()
        data = dict(form_data)

    # Verify Mailgun webhook signature
    timestamp = str(data.get("timestamp", ""))
    token = str(data.get("token", ""))
    signature = str(data.get("signature", ""))
    if not _verify_mailgun_signature(timestamp, token, signature):
        logger.warning("Rejected inbound email webhook: invalid signature")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    sender_email = data.get("from") or data.get("sender")
    subject = data.get("subject", "No Subject")
    body = data.get("body-plain") or data.get("text") or data.get("body", "")

    # Attempt to find user and building.
    # Accept both the promoted real email (users.email) and the legacy portal
    # address (users.portal_email) so inbound messages work after email migration.
    user = await db._db.users.find_one(
        {"$or": [{"email": sender_email}, {"portal_email": sender_email}]},
        {"id": 1, "full_name": 1},
    )
    user_id = user["id"] if user else None

    # Attempt to find unit_number and building
    unit_number = None
    building_id = None
    if user_id:
        user_unit = await db._db.user_units.find_one({"user_id": user_id, "is_active": True})
        if user_unit:
            unit_number = user_unit.get("unit_number")
            building_id = user_unit.get("building_id")

    if not building_id:
        # Cannot route email to a specific building — reject rather than silently
        # polluting a hardcoded building's data (multi-tenant violation)
        logger.warning(f"Inbound email from {sender_email!r} cannot be resolved to any building — skipping ingestion")
        return {"status": "skipped", "reason": "sender_not_registered"}

    logger.info(f"Received inbound email from {sender_email}: {subject}")

    # Set building context so tenant-scoped collections work correctly
    set_ctx_building_id(building_id)

    # Process via intake service
    doc = await process_inbound(
        source_channel="email",
        sender_user_id=user_id,
        sender_email=sender_email,
        unit_number=unit_number,
        subject=subject,
        body=body,
        building_id=building_id
    )

    return {"status": "accepted", "id": doc["id"], "request_number": doc.get("request_number")}
