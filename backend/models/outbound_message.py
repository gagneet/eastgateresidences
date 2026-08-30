# @featuretrace:outbound-message-queue — Status machine and schema for held outbound messages.
# Layer: model
# Data flow: send_email_async / crons -> outbound_queue_service.enqueue -> outbound_messages -> worker -> provider (building-scoped).
# Related: backend/services/outbound_queue_service.py
#          backend/utils/email.py
#          backend/utils/email_suppression.py
#          tasks/GAP-COMMS-003-outbound-message-queue-and-activation.md
"""Schema for the outbound message queue.

Every outgoing message is persisted BEFORE any provider call, so that an operator can
see it, hold it, and drop it. This is the transactional-outbox shape: the row is the
source of truth and a worker moves it, rather than the caller transmitting inline.

Two properties are load-bearing and easy to lose in a refactor:

1. **The body is stored.** The predecessor audit log (`email_sent_log`) recorded only
   recipient, subject and context. That is why a suppressed email could never be
   released later — the content to send had never been kept anywhere. `html_body` is
   therefore required at enqueue, not optional.
2. **The send gate is evaluated by the WORKER, not the caller.** A message blocked
   because its building has email disabled simply stays `HELD` and is retried on the
   next tick. Enabling email releases everything still inside its expiry window with
   no replay machinery, which is exactly the "goes out if enabled within 48 hours"
   requirement.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

# Gmail offers 5/10/20/30s and enforces the choice server-side. 30s is its maximum and
# the value the operator asked for, so it is our default rather than the shortest option.
DEFAULT_HOLD_SECONDS = 30

# How long a blocked message stays releasable before it is expired and its body redacted.
DEFAULT_EXPIRY_HOURS = 48


class MessageStatus(str, Enum):
    """Terminal states are SENT, CANCELLED, EXPIRED and FAILED."""

    HELD = "held"            # inside the undo window, or blocked by a disabled queue
    SENDING = "sending"      # claimed by a worker; guards against double-send
    SENT = "sent"
    CANCELLED = "cancelled"  # dropped by an admin before it went
    EXPIRED = "expired"      # still blocked when the expiry window closed; body redacted
    FAILED = "failed"        # provider rejected it after the retry budget


class MessageChannel(str, Enum):
    EMAIL = "email"
    # Deliberately an enum from day one: the console is specified as "messages and
    # emails", so SMS/push land here rather than in a parallel queue with its own rules.
    SMS = "sms"
    PUSH = "push"


class MessageCategory(str, Enum):
    AUTOMATED = "automated"  # cron/system-generated: levy notices, SLA breaches
    MANUAL = "manual"        # a human pressed send; the Gmail-style undo case


class OutboundMessage(BaseModel):
    """One queued message. Persisted to the building-scoped `outbound_messages`."""

    id: str
    building_id: str
    channel: MessageChannel = MessageChannel.EMAIL
    category: MessageCategory = MessageCategory.AUTOMATED

    to_email: str
    to_user_id: Optional[str] = None
    subject: str = ""
    html_body: str = ""
    text_body: Optional[str] = None
    # Kept out of the row itself: attachments can be megabytes and the queue is polled.
    attachment_refs: list[str] = Field(default_factory=list)

    context: str = ""
    status: MessageStatus = MessageStatus.HELD

    hold_until: str = ""
    expires_at: str = ""

    attempts: int = 0
    last_error: Optional[str] = None

    created_at: str = ""
    created_by: Optional[str] = None
    sent_at: Optional[str] = None
    cancelled_at: Optional[str] = None
    cancelled_by: Optional[str] = None
    released_by: Optional[str] = None
    redacted_at: Optional[str] = None

    is_test_data: bool = False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hold_until_iso(hold_seconds: int, *, now: Optional[datetime] = None) -> str:
    base = now or datetime.now(timezone.utc)
    return (base + timedelta(seconds=max(0, hold_seconds))).isoformat()


def expires_at_iso(expiry_hours: int = DEFAULT_EXPIRY_HOURS,
                   *, now: Optional[datetime] = None) -> str:
    base = now or datetime.now(timezone.utc)
    return (base + timedelta(hours=max(0, expiry_hours))).isoformat()


def is_due(message: dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    """True when the hold window has elapsed and the row is still sendable.

    Compared as ISO-8601 strings the way the rest of this codebase stores timestamps.
    That is only safe because every value written here is UTC and timezone-aware, which
    makes the strings lexicographically ordered; a naive local timestamp would sort
    wrongly and release a message early.
    """
    if message.get("status") != MessageStatus.HELD.value:
        return False
    ref = (now or datetime.now(timezone.utc)).isoformat()
    return str(message.get("hold_until") or "") <= ref


def is_expired(message: dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    """True once the release window has closed on a message that never got out."""
    if message.get("status") not in {MessageStatus.HELD.value, MessageStatus.SENDING.value}:
        return False
    ref = (now or datetime.now(timezone.utc)).isoformat()
    expires = str(message.get("expires_at") or "")
    return bool(expires) and expires <= ref
