"""Outgoing-email kill switch, checked by every code path that can transmit mail.

Why this module exists separately from utils/email.py
-----------------------------------------------------
utils.email.send_email_async is the main choke point (103 call sites), but it is NOT the
only way this application sends mail. cron/cron_approval_escalation.py,
cron/cron_admin_auto_approve.py and cron/cron_expiration_check.py each build and transmit
their own messages via the Resend HTTP API or smtplib directly, never touching
send_email_async. A switch wired only into utils/email.py would look like a kill switch
while those three kept sending — the exact failure mode CLAUDE.md footgun #9b describes
(`disable_strata_sync_direct_write` guarded the API endpoint while the scraper's own write
path had no check at all).

So the gate lives here, dependency-light, and is imported by all four.

Primary control: the per-building feature toggle
------------------------------------------------
`email_notifications_enabled` (see backend/seeds/feature_toggles.py) is the switch an
operator flips in the admin UI. ON (the global default) means mail flows; turning it OFF
for a building suppresses every outgoing message for that building. It is carried as a
per-building override, so a site-wide bulk-enable cannot silently un-mute a building.

The environment variables below are a lower-level backstop for when the UI is not
available or a hard, unconditional stop is wanted. Env wins over the toggle.

Configuration (backend/.env)
----------------------------
    EMAIL_SEND_DISABLED_BUILDING_IDS   Comma-separated building ids whose mail is
                                       suppressed. Empty (default) = nothing suppressed.
    EMAIL_SEND_DISABLED_ALL            "true" suppresses every outgoing email platform
                                       wide, regardless of building.
    EMAIL_ALLOW_UNRESOLVED_BUILDING    "true" lets mail through when the recipient's
                                       building cannot be determined. Default FALSE while
                                       a blocklist is active — see "Fail closed" below.

Fail closed
-----------
When a blocklist is active and a message's building CANNOT be determined, the default is
to SUPPRESS it. A kill switch that leaks whenever the context is ambiguous is not a kill
switch, and the ambiguous cases are exactly the ones worth stopping: a levy notice sent
from a background worker with no request context resolves its building last, not first.
Set EMAIL_ALLOW_UNRESOLVED_BUILDING=true to invert that if it proves too broad.

Every suppression is logged at WARNING with the resolved reason, so a silent block is
always visible in the journal.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

# Keep in lockstep with the entry in backend/seeds/feature_toggles.py and the
# classification in backend/core/toggle_classification.py.
EMAIL_TOGGLE_KEY = "email_notifications_enabled"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _blocked_building_ids() -> set[str]:
    """Building ids whose mail is suppressed outright, from a COMMA-separated list.

    Warns on an entry that looks like it used the wrong separator. On 2026-08-27 the
    value was written `13195.16244`, which parses as one building literally named
    "13195.16244" — so neither building matched and the blocklist silently protected
    nothing. It was masked at the time by EMAIL_SEND_DISABLED_ALL, and would have opened
    the moment that was lifted.

    A malformed entry is deliberately still returned rather than dropped or repaired:
    guessing at intent could widen or narrow the block in ways the operator did not
    write. The warning names the entry so it can be corrected deliberately.
    """
    raw = os.getenv("EMAIL_SEND_DISABLED_BUILDING_IDS", "") or ""
    parsed = {part.strip() for part in raw.split(",") if part.strip()}
    for entry in parsed:
        if any(sep in entry for sep in (".", ";", " ", "|")):
            logger.warning(
                "EMAIL_SEND_DISABLED_BUILDING_IDS entry %r contains an embedded "
                "separator — the list is COMMA-separated, so this matches no building "
                "and suppresses nothing", entry,
            )
    return parsed


async def _building_for_recipient(to_email: str) -> str | None:
    """Best-effort: which building does this address belong to?

    `users` and `memberships` are both GLOBAL collections (verified against
    database.TENANT_SCOPED_COLLECTIONS), so these lookups need no ambient building
    context — which is the point, since the callers that most need this check are cron
    jobs that have none.

    The address is re.escape()d before being used as a $regex. Interpolating it raw was a
    real two-way bug: `.` is a regex wildcard, so "a.b@x" also matched "axb@x" and could
    resolve the WRONG user's building; and `+` is a quantifier, so a plus-addressed
    recipient like "owner+notices@x" failed to match ITSELF — the building came back
    unresolved and the message was NOT suppressed. A kill switch that leaks on
    plus-addressing is not a kill switch.
    """
    if not to_email:
        return None
    try:
        from database import db

        # Exact match first: hits the index directly and covers the common case.
        # The escaped case-insensitive form is the fallback for stored-casing drift.
        addr = to_email.strip()
        user = await db.users.find_one({"email": addr}, {"_id": 0, "id": 1, "building_id": 1})
        if not user:
            user = await db.users.find_one(
                {"email": {"$regex": f"^{re.escape(addr)}$", "$options": "i"}},
                {"_id": 0, "id": 1, "building_id": 1},
            )
        if user and user.get("building_id"):
            return str(user["building_id"])

        # A user with no building_id of their own may still hold a membership.
        if user and user.get("id"):
            membership = await db.memberships.find_one(
                {"user_id": user["id"], "is_active": True},
                {"_id": 0, "building_id": 1},
            )
            if membership and membership.get("building_id"):
                return str(membership["building_id"])

        # Last resort: a building's OWN settings contacts belong to that building even
        # when no user record does. This is not a nicety — a building whose user records
        # have been removed still names real addresses here (ec_email, notify_bcc_email,
        # and the sender), and without this they resolve to no building at all and escape
        # a per-building suppression entirely.
        #
        # Deliberately generic: it matches whichever building's settings names the
        # address, with no hardcoded building or domain. `settings` is tenant-scoped, so
        # it is read through db._db to search across buildings — the same raw-collection
        # pattern used elsewhere for cross-building reads.
        for field in ("ec_email", "notify_bcc_email", "sender_email", "manager_email",
                      "contact_email", "building_email"):
            doc = await db._db.settings.find_one(
                {field: {"$regex": f"^{re.escape(addr)}$", "$options": "i"}},
                {"_id": 0, "building_id": 1},
            )
            if doc and doc.get("building_id"):
                return str(doc["building_id"])
    except Exception as exc:  # the lookup failing must not itself decide to send
        logger.warning("email suppression: recipient building lookup failed: %s", exc)
        return None
    return None


def _ambient_building_id() -> str | None:
    try:
        from request_context import get_ctx_building_id

        return get_ctx_building_id()
    except Exception:
        return None



def _allowed_domains() -> set[str]:
    """Domains mail may be delivered to, from EMAIL_ALLOWED_DOMAINS.

    Empty (the default) means no domain restriction — the other gates decide.
    """
    raw = os.getenv("EMAIL_ALLOWED_DOMAINS", "") or ""
    return {d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()}


def _domain_of(address: str) -> str:
    return (address or "").strip().rsplit("@", 1)[-1].lower()


async def check_email_suppressed(
    to_email: str = "",
    building_id: str | None = None,
) -> tuple[bool, str]:
    """Return (suppressed, reason).

    Resolution order for the building: explicit argument, then the ambient request
    context, then the recipient's own user record. The explicit argument wins so a caller
    that already knows the building never pays for a database round-trip.
    """
    if _env_flag("EMAIL_SEND_DISABLED_ALL"):
        return True, "EMAIL_SEND_DISABLED_ALL is set"

    # Recipient-domain allowlist. Checked second — after the unconditional stop and
    # before ANY building resolution — so it holds for cron mail, for a recipient whose
    # building cannot be determined, and for every future send path, none of which can
    # reach a provider without passing through here.
    #
    # This is a stricter control than the building blocklist it sits above, and it fails
    # CLOSED on the property the operator actually cares about: with
    # EMAIL_ALLOWED_DOMAINS set, an address off the list is unsendable no matter which
    # building it belongs to, whether its queue is enabled, or whether someone later
    # lifts EMAIL_SEND_DISABLED_ALL. Restoring East Gate put ~100 real personal
    # addresses back into the database; rewriting them onto the building domain removed
    # them from user records, but a live control is what guarantees no message reaches a
    # real person by another route.
    #
    # Unset (the default) means no restriction, so existing deployments are unaffected.
    allowed = _allowed_domains()
    if allowed:
        domain = _domain_of(to_email)
        if domain not in allowed:
            shown = domain or "(no domain)"
            return True, f"recipient domain '{shown}' is not in EMAIL_ALLOWED_DOMAINS"

    blocked = _blocked_building_ids()

    # Resolve the building once; both the env blocklist and the toggle need it.
    resolved = building_id or _ambient_building_id()
    source = "explicit" if building_id else ("request context" if resolved else "")
    if not resolved:
        resolved = await _building_for_recipient(to_email)
        source = "recipient lookup" if resolved else ""

    if resolved and str(resolved) in blocked:
        return True, f"building {resolved} is in EMAIL_SEND_DISABLED_BUILDING_IDS (via {source})"

    if not resolved and blocked:
        if _env_flag("EMAIL_ALLOW_UNRESOLVED_BUILDING"):
            return False, ""
        return True, (
            "building could not be determined and a suppression list is active "
            "(fail-closed; set EMAIL_ALLOW_UNRESOLVED_BUILDING=true to allow these)"
        )

    # Per-building feature toggle — the UI-managed control.
    if resolved and not await _toggle_allows_email(str(resolved)):
        return True, (
            f"feature toggle 'email_notifications_enabled' is OFF for building {resolved} "
            f"(via {source})"
        )

    return False, ""


async def _toggle_allows_email(building_id: str) -> bool:
    """Resolve `email_notifications_enabled` for a building.

    Defaults to True on any failure. A toggle store that is unreachable must not silently
    mute a building's mail — the env blocklist above is the mechanism for a deliberate,
    unconditional stop, and it has already been evaluated by this point.
    """
    try:
        from db_postgres.repos import config_repo

        return bool(await config_repo.resolve_feature_toggle(
            building_id, EMAIL_TOGGLE_KEY, default=True,
        ))
    except Exception as exc:
        logger.warning(
            "email suppression: could not resolve %s for building %s (%s) — allowing send",
            EMAIL_TOGGLE_KEY, building_id, exc,
        )
        return True


async def suppress_if_blocked(
    to_email: str,
    subject: str = "",
    building_id: str | None = None,
    context: str = "",
) -> bool:
    """Check, log loudly, and return True when the caller must NOT send."""
    suppressed, reason = await check_email_suppressed(to_email, building_id)
    if suppressed:
        logger.warning(
            "EMAIL SUPPRESSED to=%s subject=%r context=%r — %s",
            to_email, subject[:120], context, reason,
        )
    return suppressed
