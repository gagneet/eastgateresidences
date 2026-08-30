# @featuretrace:outbound-message-queue — Enqueue, release, cancel and expire outbound messages.
# Layer: service
# Data flow: callers -> enqueue() -> outbound_messages (held) -> worker claim/send -> sent|failed|expired (building-scoped).
# Related: backend/models/outbound_message.py
#          backend/utils/email.py
#          backend/utils/email_suppression.py
#          tasks/GAP-COMMS-003-outbound-message-queue-and-activation.md
"""The outbound message queue.

Nothing here transmits. This module owns the row and its state machine; the worker
performs the provider call. Keeping the two apart is what allows the admin console to
cancel a message safely — there is exactly one moment where a row becomes un-cancellable
(the atomic HELD -> SENDING claim) and it lives in `claim_for_send`.

Ordering rule for the send decision, outermost first:

    EMAIL_SEND_DISABLED_ALL      env kill switch, unchanged, still wins over everything
    per-building queue enabled   the admin-managed control this task adds
    hold window elapsed          the Gmail-style undo delay
    expiry window still open     48h, after which the body is redacted

The first three are re-evaluated on every worker tick. That is the whole mechanism
behind "enable email within 48 hours and the held mail goes out": no replay, no
re-enqueue, just a gate that stops failing.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from database import db
from models.outbound_message import (
    DEFAULT_EXPIRY_HOURS,
    DEFAULT_HOLD_SECONDS,
    MessageCategory,
    MessageChannel,
    MessageStatus,
    expires_at_iso,
    hold_until_iso,
    now_iso,
)

logger = logging.getLogger(__name__)

COLLECTION = "outbound_messages"

# Discriminator for this feature's row in the building-scoped `db.settings` collection
# (`db.site_settings` is rate-limits only — CLAUDE.md).
#
# The field is `type`, NOT a bespoke `setting_type`. settings_service treats a doc whose
# `type` is absent, None, or "general" as the building's GENERAL settings document, so a
# row carrying some other key and no `type` would be picked up as general config and
# merged into it. Typed rows in this collection (levy reminders, unit display rules) all
# use `type`, and this must too.
SETTINGS_TYPE_FIELD = "type"
SETTINGS_KEY = "outbound_queue"

# Retry budget before a provider failure becomes terminal. Deliberately small: a message
# nobody can deliver should surface in the console, not churn silently.
MAX_ATTEMPTS = 3


async def get_queue_settings(building_id: str) -> dict[str, Any]:
    """Per-building queue controls, with safe defaults when unconfigured.

    Defaults to ENABLED. A building that has never opened the settings page must not
    have its mail silently held — the env kill switch is the mechanism for a deliberate
    stop, and it has already been evaluated by the time this is consulted.
    """
    # Read with an EXPLICIT building_id against the raw collection rather than relying
    # on the ambient request context. `settings` is tenant-scoped, so the wrapper needs
    # a context that a cron or background task does not have — and the failure mode is
    # not a crash but a silent fall back to defaults, which would quietly ignore a
    # building's configured hold and expiry for every cron-originated message.
    #
    # Deliberately not set_ctx_building_id(): mutating ambient context inside a helper
    # can leak into whatever else is running on the same task.
    doc = None
    try:
        raw = db._db["settings"] if hasattr(db, "_db") else db["settings"]
        doc = await raw.find_one({"building_id": building_id,
                                  SETTINGS_TYPE_FIELD: SETTINGS_KEY})
    except Exception as exc:
        logger.warning("outbound queue settings unreadable for %s (%s) — using defaults",
                       building_id, exc)
    doc = doc or {}
    return {
        "enabled": bool(doc.get("enabled", True)),
        "hold_seconds": int(doc.get("hold_seconds", DEFAULT_HOLD_SECONDS)),
        "expiry_hours": int(doc.get("expiry_hours", DEFAULT_EXPIRY_HOURS)),
        # Categories an operator has explicitly muted, e.g. ["automated"].
        "disabled_categories": list(doc.get("disabled_categories", [])),
    }


def resolve_hold_seconds(requested: Optional[int], configured: int) -> int:
    """The building's configured hold is a FLOOR, never a ceiling.

    A caller may ask to hold a message longer than the building's window, but may not
    ask for less. Gmail enforces its undo window server-side for the same reason: a
    hold a caller can shorten is not a control, it is a suggestion.
    """
    if requested is None:
        return max(0, configured)
    return max(0, configured, requested)


async def enqueue(
    *,
    building_id: str,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: Optional[str] = None,
    context: str = "",
    category: MessageCategory = MessageCategory.AUTOMATED,
    channel: MessageChannel = MessageChannel.EMAIL,
    to_user_id: Optional[str] = None,
    created_by: Optional[str] = None,
    hold_seconds: Optional[int] = None,
    is_test_data: bool = False,
) -> dict[str, Any]:
    """Persist a message in HELD state. Never transmits."""
    settings = await get_queue_settings(building_id)
    hold = resolve_hold_seconds(hold_seconds, settings["hold_seconds"])
    now = datetime.now(timezone.utc)

    row = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "channel": channel.value if isinstance(channel, MessageChannel) else str(channel),
        "category": category.value if isinstance(category, MessageCategory) else str(category),
        "to_email": to_email,
        "to_user_id": to_user_id,
        "subject": subject,
        "html_body": html_body,
        "text_body": text_body,
        "context": context,
        "status": MessageStatus.HELD.value,
        "hold_until": hold_until_iso(hold, now=now),
        "expires_at": expires_at_iso(settings["expiry_hours"], now=now),
        "attempts": 0,
        "last_error": None,
        "created_at": now.isoformat(),
        "created_by": created_by,
        "sent_at": None,
        "cancelled_at": None,
        "cancelled_by": None,
        "released_by": None,
        "redacted_at": None,
        "is_test_data": is_test_data,
    }
    await db[COLLECTION].insert_one(dict(row))
    logger.info("outbound queued id=%s to=%s context=%s hold=%ss",
                row["id"], to_email, context, hold)
    return row


def sendable_reason(message: dict[str, Any], settings: dict[str, Any],
                    *, now: Optional[datetime] = None) -> tuple[bool, str]:
    """Decide whether one message may go right now, and say why not.

    Returned as a reason string rather than a bare bool so the console can show an
    operator exactly which gate is holding a message, instead of an undifferentiated
    "pending" that gives them nothing to act on.
    """
    from models.outbound_message import is_due, is_expired

    if message.get("status") != MessageStatus.HELD.value:
        return False, f"status is {message.get('status')}"
    if is_expired(message, now=now):
        return False, "expired"
    if not settings.get("enabled", True):
        return False, "queue disabled for this building"
    if message.get("category") in set(settings.get("disabled_categories") or []):
        return False, f"category '{message.get('category')}' is disabled for this building"
    if not is_due(message, now=now):
        return False, "still inside the hold window"
    return True, ""


async def claim_for_send(message_id: str) -> bool:
    """Atomically move HELD -> SENDING. The single point of no return.

    Conditioning the update on the current status is what stops two workers sending the
    same message, and what makes "cancel" safe: a cancel that lands first wins, and a
    cancel that lands after this returns True is correctly refused.
    """
    res = await db[COLLECTION].update_one(
        {"id": message_id, "status": MessageStatus.HELD.value},
        {"$set": {"status": MessageStatus.SENDING.value, "updated_at": now_iso()}},
    )
    return res.modified_count == 1


async def mark_sent(message_id: str, provider: str = "") -> None:
    await db[COLLECTION].update_one(
        {"id": message_id},
        {"$set": {"status": MessageStatus.SENT.value, "sent_at": now_iso(),
                  "last_error": None, "provider": provider}},
    )


async def mark_attempt_failed(message_id: str, error: str, attempts: int) -> None:
    """Back to HELD for another try, or FAILED once the budget is spent."""
    terminal = attempts >= MAX_ATTEMPTS
    await db[COLLECTION].update_one(
        {"id": message_id},
        {"$set": {
            "status": (MessageStatus.FAILED if terminal else MessageStatus.HELD).value,
            "last_error": str(error)[:500],
            "attempts": attempts,
            "updated_at": now_iso(),
        }},
    )


async def return_to_held(message_id: str, *, reason: str) -> None:
    """Put a claimed message back to HELD without consuming a retry attempt.

    For a message the send layer REFUSED on policy grounds — the kill switch, or the
    recipient-domain allowlist — as opposed to one a provider failed to deliver.

    The distinction is the whole point. Both look like "not sent" at the call site, but a
    policy refusal can be undone (lift the switch, add a domain) and a provider failure
    generally cannot. Counting a refusal as an attempt burns the retry budget in ninety
    seconds on a 30-second tick and lands the message in FAILED, which is untrue and
    unrecoverable — an operator would open the console to a wall of failures for messages
    that were correctly held. `attempts` is therefore left alone; the message waits for
    the policy to change or for its own 48-hour expiry, whichever comes first.
    """
    await db[COLLECTION].update_one(
        {"id": message_id, "status": MessageStatus.SENDING.value},
        {"$set": {"status": MessageStatus.HELD.value,
                  "last_error": f"held: {reason}"[:500],
                  "updated_at": now_iso()}},
    )


async def cancel(message_id: str, *, cancelled_by: str, building_id: str) -> bool:
    """Operator drops a message. Only possible while it is still HELD."""
    res = await db[COLLECTION].update_one(
        {"id": message_id, "status": MessageStatus.HELD.value},
        {"$set": {"status": MessageStatus.CANCELLED.value,
                  "cancelled_at": now_iso(), "cancelled_by": cancelled_by}},
    )
    if res.modified_count == 1:
        logger.info("outbound cancelled id=%s by=%s", message_id, cancelled_by)
        return True
    return False


async def release_now(message_id: str, *, released_by: str) -> bool:
    """Skip the remaining hold window. Does NOT bypass the enabled/expiry gates.

    Only the undo delay is the operator's to waive. A message held because the
    building's email is switched off must stay held — otherwise this endpoint would
    quietly become a way around the very control it belongs to.
    """
    res = await db[COLLECTION].update_one(
        {"id": message_id, "status": MessageStatus.HELD.value},
        {"$set": {"hold_until": now_iso(), "released_by": released_by}},
    )
    return res.modified_count == 1


async def expire_stale(building_id: str, *, now: Optional[datetime] = None) -> int:
    """Expire messages whose release window closed, and redact their bodies.

    The operator asked for these to be "deleted within 48 hours". They are made
    unsendable and stripped of their content, but the row survives as an audit stub:
    ACT/NSW seven-year retention (CLAUDE.md) forbids hard-deleting a record of a
    communication that was generated. Redaction removes the PII, which is the part that
    actually matters for sitting in a queue nobody is going to send.
    """
    ref = (now or datetime.now(timezone.utc)).isoformat()
    res = await db[COLLECTION].update_many(
        {"status": {"$in": [MessageStatus.HELD.value, MessageStatus.SENDING.value]},
         "expires_at": {"$lte": ref}},
        {"$set": {"status": MessageStatus.EXPIRED.value,
                  "html_body": "", "text_body": "",
                  "redacted_at": ref,
                  "last_error": "expired unsent; body redacted"}},
    )
    if res.modified_count:
        logger.info("outbound expired+redacted %s message(s) for building %s",
                    res.modified_count, building_id)
    return res.modified_count
