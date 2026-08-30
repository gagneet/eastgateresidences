# @featuretrace:owner-activation — Issue per-owner activation links and mark accounts unclaimed.
# Layer: service
# Data flow: script/admin -> issue_activation() -> password_resets + core.users.requires_activation
#            -> send_email_async -> outbound queue -> owner (building-scoped).
# Related: backend/routers/auth.py
#          backend/db_postgres/repos/identity_repo.py
#          backend/services/outbound_queue_service.py
#          tasks/GAP-COMMS-003-outbound-message-queue-and-activation.md
"""Owner activation: give a restored account back to the person it belongs to.

A restored owner has a record but has never chosen a password on this platform. The
flow is deliberately the password-reset flow rather than a bespoke one:

    mark unclaimed -> email a unique link -> owner sets a password -> account opens

Reusing ``password_resets`` means the consume path, its expiry check, its single-use
flag and its rate limiting are all the ones already in production, rather than a second
credential-bearing code path written for this feature. The only difference is the
lifetime: a password reset is valid for an hour because the user just asked for it,
while an activation link is sent unprompted and has to survive someone opening their
email a week later.

The link is unique per user — a shared link would let whoever opened it first claim
somebody else's unit.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from database import db

logger = logging.getLogger(__name__)

# Sent unprompted, so it must outlive a holiday. A reset the user just requested keeps
# its 1-hour window; this is a different situation with different risk.
ACTIVATION_TTL_DAYS = 14

# Marks the row so an activation can be told apart from an ordinary reset in the audit
# trail. The consume path in routers/auth.py ignores it, which is the point: one code
# path, one set of expiry and single-use guarantees.
ACTIVATION_PURPOSE = "owner_activation"


def _portal_url() -> str:
    from routers.auth import _get_portal_url

    return _get_portal_url()


async def issue_activation(user: dict[str, Any], *, building_id: str) -> Optional[str]:
    """Create a fresh single-use activation link for one user.

    Any earlier unused activation token for the same user is invalidated first. Two live
    links for one account is a needless second key to the same door, and it makes
    "did they use the one I sent?" unanswerable.
    """
    email = (user.get("email") or "").strip()
    user_id = user.get("id")
    if not email or not user_id:
        logger.warning("cannot issue activation for a user with no id/email: %r", user_id)
        return None

    await db.password_resets.update_many(
        {"user_id": user_id, "used": False, "purpose": ACTIVATION_PURPOSE},
        {"$set": {"used": True,
                  "invalidated_at": datetime.now(timezone.utc).isoformat(),
                  "invalidated_reason": "superseded_by_new_activation"}},
    )

    token = secrets.token_urlsafe(32)
    await db.password_resets.insert_one({
        "token": token,
        "user_id": user_id,
        "email": email,
        "purpose": ACTIVATION_PURPOSE,
        "building_id": building_id,
        "expires_at": (datetime.now(timezone.utc)
                       + timedelta(days=ACTIVATION_TTL_DAYS)).isoformat(),
        "used": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return f"{_portal_url()}/reset-password?token={token}"


def build_activation_email(user: dict[str, Any], link: str, building_name: str) -> tuple[str, str]:
    """Body for the activation email.

    Says plainly that the account already exists and that no password has been set,
    because the alternative reading — "someone has created an account in my name" —
    is exactly what makes people ignore or report the message.
    """
    name = (user.get("full_name") or "").strip() or "Owner"
    unit = (user.get("unit_number") or "").strip()
    unit_line = f" for Unit {unit}" if unit else ""

    html = (
        f"<p>Hello {name},</p>"
        f"<p>An owner account{unit_line} has been set up for you on the "
        f"{building_name} portal. It is ready to use, but no password has been set on "
        f"it yet — for security, we never set one on your behalf.</p>"
        f"<p><a href=\"{link}\">Set your password and activate your account</a></p>"
        f"<p>This link is unique to you and can be used once. It expires in "
        f"{ACTIVATION_TTL_DAYS} days. Until you use it, the account cannot be signed "
        f"into — including by anyone else.</p>"
        f"<p>Once you are in, you can ask the building manager to correct your name or "
        f"unit details from your profile page.</p>"
    )
    text = (
        f"Hello {name},\n\n"
        f"An owner account{unit_line} has been set up for you on the {building_name} "
        f"portal. No password has been set on it yet.\n\n"
        f"Set your password: {link}\n\n"
        f"This link is unique to you, can be used once, and expires in "
        f"{ACTIVATION_TTL_DAYS} days. Until then the account cannot be signed into.\n"
    )
    return html, text


async def mark_unclaimed(email: str) -> bool:
    """Flag an account as awaiting activation in Postgres.

    Postgres is what login consults, so this is the flag that actually blocks a sign-in.
    Returns False for a Mongo-only account — legitimate, not an error.
    """
    from db_postgres.repos import identity_repo

    return await identity_repo.set_requires_activation(email, True)
