"""Admin invitations router — Postgres-only.

Endpoints
---------
POST /admin/invitations/send
    Send an invitation to a new user. Role-matrix enforced:
    - super_admin  → any role, any tenant (pass ``tenant_id`` in body to cross-invite)
    - strata_admin → strata_manager / admin_staff, own tenant only
    - strata_manager → admin_staff, own tenant only

POST /admin/invitations/{invitation_id}/resend
    Generate a fresh token and re-send the invitation email.
    Caller must own the invitation's tenant (super_admin may specify any tenant_id).

POST /onboarding/claim/{token}
    Invited user sets a password and creates their account.
    Finds the invitation by SHA-256 hash, creates ``core.users`` +
    ``core.user_role_assignments``, marks the invitation claimed.

No MongoDB writes.

Audit
-----
All three endpoints append to the hash-chained ``core.audit_events`` trail via
``services.authorisation_audit.record_event``. They are the platform's
access-granting path — an invitation creates an account and assigns a role — so
they are exactly the actions a forensic reader needs and, until GAP-SEC-014's
sibling finding was raised, none of them recorded anything at all.

Postgres, NOT ``utils.helpers.create_audit_log``, and deliberately so: that
helper writes to the Mongo ``audit_logs`` collection, which is TENANT-SCOPED.
``TenantCollection.insert_one`` raises when there is no building context and no
``building_id`` on the document, and ``create_audit_log`` swallows that
exception and returns "". On ``POST /onboarding/claim/{token}`` — unauthenticated,
so no building context is ever set — it would therefore have logged nothing
while appearing to work. An invitation is tenant/scheme-scoped in any case, not
building-scoped, and this router is Postgres-only.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from db_postgres.repos.identity_repo import (
    create_invitation,
    find_invitation_by_id,
    find_invitation_by_token,
    claim_invitation,
    refresh_invitation_token,
    create_user,
    add_role_assignment,
)
from services.authorisation_audit import PROVISIONING_ENTITY_TYPE, record_event
from utils.auth import get_current_user, hash_password, effective_role
from models.user import UserRole
from utils.email import send_email_async

router = APIRouter(tags=["Invitations"])
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Request / Response models
# ──────────────────────────────────────────────────────────────────────────────

class SendInvitationRequest(BaseModel):
    email: EmailStr
    role: str = Field(..., description="Target user role, e.g. 'strata_admin', 'strata_manager', 'owner'")
    scheme_id: Optional[str] = Field(None, description="UUID of the scheme to invite into; None for global roles")
    tenant_id: Optional[str] = Field(
        None,
        description="Target tenant UUID. Required for super_admin cross-tenant invites; "
                    "ignored for non-super-admin callers (their JWT tenant is always used).",
    )
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    expires_days: int = Field(default=7, ge=1, le=30)


class ClaimInvitationRequest(BaseModel):
    password: str = Field(..., min_length=8, description="Password to set for the new account")
    first_name: Optional[str] = None
    last_name: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────────
# Role-matrix constants and helpers
# ──────────────────────────────────────────────────────────────────────────────

_VALID_ROLES = {
    UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN, UserRole.EC_MEMBER,
    UserRole.STRATA_MANAGER, UserRole.ADMIN_STAFF, UserRole.OWNER,
    UserRole.TENANT, UserRole.REAL_ESTATE_AGENT, UserRole.SERVICE_PROVIDER,
    UserRole.GUEST,
}

# Roles each caller level is permitted to invite
_INVITE_MATRIX: dict[str, set[str]] = {
    UserRole.SUPER_ADMIN:    _VALID_ROLES,                             # any role
    UserRole.STRATA_ADMIN:   {UserRole.STRATA_MANAGER, UserRole.ADMIN_STAFF},
    UserRole.STRATA_MANAGER: {UserRole.ADMIN_STAFF},
}

_STRONG_PASSWORD_RE = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?]).{8,}$"
)


def _check_can_invite(caller: dict, target_role: str) -> str:
    """Enforce role matrix.  Returns normalised ``caller_role`` string.

    - super_admin may invite any role into any tenant.
    - strata_admin may invite strata_manager / admin_staff into their own tenant.
    - strata_manager may invite admin_staff into their own tenant.
    - All other roles are rejected with 403.
    """
    caller_role = effective_role(caller)
    allowed = _INVITE_MATRIX.get(caller_role)
    if allowed is None:
        raise HTTPException(status_code=403, detail="You do not have permission to send invitations.")
    if target_role not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"A '{caller_role}' cannot invite a '{target_role}'. "
                   f"Allowed targets: {sorted(allowed)}",
        )
    return caller_role


def _validate_role(role: str) -> str:
    """Generated function header.

    Function: _validate_role
    Path: backend/routers/admin_invitations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if role not in _VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role '{role}'. Valid values: {sorted(_VALID_ROLES)}")
    return role


def _resolve_tenant(caller: dict, caller_role: str, body_tenant_id: Optional[str] = None) -> str:
    """Resolve the target tenant_id for an invite or resend operation.

    - super_admin: may use ``body_tenant_id`` to invite into any tenant.
      Falls back to their own JWT ``tenant_id`` when not specified.
    - All other callers: JWT ``tenant_id`` is always used; ``body_tenant_id`` is ignored.
    """
    if caller_role == UserRole.SUPER_ADMIN and body_tenant_id:
        return str(body_tenant_id)
    tenant_id = caller.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context. Ensure your account is assigned to a tenant.")
    return str(tenant_id)


# ──────────────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────────────

def _audit_invitation(
        request: Request = None,
        *,
        action: str,
        invitation_id,
        tenant_id: str,
        scheme_id,
        actor_user_id,
        payload: dict,
) -> None:
    """Append one provisioning event to the hash-chained core.audit_events trail.

    Never raises. An audit failure must not turn a successful invitation into a
    500 — the grant has already happened by the time this is called, so aborting
    would leave the system state and the caller's view disagreeing. record_event
    is itself non-blocking (it appends to the single writer's queue), returns
    False rather than throwing, and reports its own drops; the log line here
    covers the remaining case where something upstream of it fails.
    """
    try:
        queued = record_event(
            PROVISIONING_ENTITY_TYPE,
            action,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            scheme_id=scheme_id,
            entity_id=invitation_id,
            payload=payload,
            ip_address=(request.client.host if request and request.client else None),
            user_agent=(request.headers.get("user-agent") if request else None),
        )
        if not queued:
            logger.error("AUDIT GAP: %s for invitation %s was not recorded", action, invitation_id)
    except Exception:  # noqa: BLE001 — auditing must never fail the request
        logger.exception("AUDIT GAP: %s for invitation %s raised", action, invitation_id)



@router.post("/admin/invitations/send", status_code=201)
async def send_invitation(
        body: SendInvitationRequest,
        current_user: dict = Depends(get_current_user),
        # LAST, and defaulted: this router's suite calls these endpoint functions
        # directly and positionally, so a new parameter anywhere earlier silently
        # captures current_user. FastAPI injects on the `Request` annotation
        # regardless of the default, so the served behaviour is unchanged.
        request: Request = None,
):
    """Send an invitation email to a prospective user.

    Role matrix (enforced server-side):

    | Caller           | May invite                               | Tenant scope     |
    |------------------|------------------------------------------|------------------|
    | super_admin      | any role                                 | any tenant       |
    | strata_admin     | strata_manager, admin_staff              | own tenant only  |
    | strata_manager   | admin_staff                              | own tenant only  |

    The invitation is stored as a SHA-256-hashed token in ``core.user_invitations``.
    The raw (unhashed) token is sent to the recipient in the email.
    """
    role = _validate_role(body.role)
    caller_role = _check_can_invite(current_user, role)
    tenant_id = _resolve_tenant(current_user, caller_role, body_tenant_id=body.tenant_id)

    invitation_id, raw_token = await create_invitation(
        tenant_id=tenant_id,
        email=str(body.email),
        invited_role=role,
        scheme_id=body.scheme_id,
        invited_by=current_user["id"],
        ttl_hours=body.expires_days * 24,
        prefill={"first_name": body.first_name, "last_name": body.last_name},
    )

    claim_url = f"/onboarding/claim/{raw_token}"

    # Recorded BEFORE the email, and independently of whether it is delivered.
    # The security-material act is the grant of a claimable role, which has
    # already happened by this line; delivery is an operational detail carried in
    # the payload. Auditing after the send would omit the grant whenever the mail
    # provider was down — the case where the record matters most.
    _audit_invitation(
        request,
        action="invitation.sent",
        invitation_id=invitation_id,
        tenant_id=tenant_id,
        scheme_id=body.scheme_id,
        actor_user_id=current_user.get("id"),
        payload={
            "invited_email": str(body.email),
            "invited_role": role,
            "caller_role": caller_role,
            "expires_days": body.expires_days,
            "cross_tenant": bool(body.tenant_id) and caller_role == UserRole.SUPER_ADMIN,
        },
    )

    email_delivered = True
    try:
        await send_email_async(
            to_email=body.email,
            subject="You've been invited to StrataOS",
            body=_build_invitation_email(
                to_name=f"{body.first_name or ''} {body.last_name or ''}".strip() or str(body.email),
                claim_url=claim_url,
                role=role,
                expires_days=body.expires_days,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        email_delivered = False
        logger.warning("Invitation email send failed: %s", exc)

    if not email_delivered:
        _audit_invitation(
            request,
            action="invitation.email_failed",
            invitation_id=invitation_id,
            tenant_id=tenant_id,
            scheme_id=body.scheme_id,
            actor_user_id=current_user.get("id"),
            payload={"invited_email": str(body.email), "invited_role": role},
        )

    return {
        "message": f"Invitation sent to {body.email}.",
        "invitation_id": str(invitation_id),
    }


@router.post("/admin/invitations/{invitation_id}/resend", status_code=200)
async def resend_invitation(
        invitation_id: str,
        current_user: dict = Depends(get_current_user),
        request: Request = None,  # last + defaulted — see send_invitation
):
    """Re-send an invitation with a fresh token and extended expiry (72 h).

    Generates a new raw token (overwriting the stored hash), sends a new
    email, and returns the send count (1 on success).  Caller must have
    permission to invite the target role (same matrix as /send).
    """
    caller_role = effective_role(current_user)
    tenant_id = _resolve_tenant(current_user, caller_role)

    invite = await find_invitation_by_id(invitation_id, tenant_id)
    if not invite:
        raise HTTPException(status_code=404, detail="Invitation not found, already claimed, or cancelled.")

    target_role = str(invite["invited_role"])
    _check_can_invite(current_user, target_role)

    raw_token = await refresh_invitation_token(invitation_id, tenant_id, ttl_hours=72)
    claim_url = f"/onboarding/claim/{raw_token}"

    to_name = (
        f"{invite.get('prefill_first_name', '') or ''} {invite.get('prefill_last_name', '') or ''}".strip()
        or str(invite["email"])
    )

    # A resend MINTS A NEW TOKEN and invalidates the old one — it is a fresh
    # credential for the same role grant, not a duplicate notification. That is
    # why it is audited as its own event rather than folded into the original.
    _audit_invitation(
        request,
        action="invitation.resent",
        invitation_id=invitation_id,
        tenant_id=tenant_id,
        scheme_id=invite.get("scheme_id"),
        actor_user_id=current_user.get("id"),
        payload={
            "invited_email": str(invite["email"]),
            "invited_role": target_role,
            "caller_role": caller_role,
            "new_ttl_hours": 72,
        },
    )

    emails_sent = 0
    try:
        await send_email_async(
            to_email=str(invite["email"]),
            subject="Your StrataOS invitation (resent)",
            body=_build_invitation_email(
                to_name=to_name,
                claim_url=claim_url,
                role=target_role,
                expires_days=3,
            ),
        )
        emails_sent = 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("Resend email failed for invitation %s: %s", invitation_id, exc)

    return {
        "message": "Invitation resent." if emails_sent else "Token refreshed but email delivery failed.",
        "invitation_id": invitation_id,
        "emails_sent": emails_sent,
    }


@router.post("/onboarding/claim/{token}", status_code=201)
async def claim_invitation_endpoint(
        token: str,
        body: ClaimInvitationRequest,
        request: Request = None,
):
    """Claim an invitation and create the user account.

    This endpoint is unauthenticated — the token IS the credential.
    After successful claim the user is created in ``core.users`` and
    assigned the role from the invitation.

    Returns the new user's ID so the caller can redirect to login.
    """
    if not _STRONG_PASSWORD_RE.match(body.password):
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters and contain uppercase, lowercase, digit, and special character.",
        )

    invite = await find_invitation_by_token(token)
    if not invite:
        raise HTTPException(status_code=404, detail="Invitation not found or already claimed.")

    tenant_id = str(invite["tenant_id"])
    pw_hash = hash_password(body.password)

    first_name = body.first_name or invite.get("prefill_first_name") or ""
    last_name = body.last_name or invite.get("prefill_last_name") or ""
    full_name = f"{first_name} {last_name}".strip() or str(invite["email"])

    user_id = await create_user(
        data={
            "email": str(invite["email"]),
            "full_name": full_name,
            "first_name": first_name,
            "last_name": last_name,
            "password_hash": pw_hash,
            "role": str(invite["invited_role"]),
            "is_active": True,
            "is_approved": True,
        },
        tenant_id=tenant_id,
    )

    await add_role_assignment(
        user_id=str(user_id),
        role=str(invite["invited_role"]),
        tenant_id=tenant_id,
        scheme_id=str(invite["scheme_id"]) if invite.get("scheme_id") else None,
    )

    await claim_invitation(str(invite["invitation_id"]), str(user_id), tenant_id)

    # The single most material event in this file: an account now exists and
    # holds a role. The actor is the new user — the token bearer acted, and this
    # endpoint is unauthenticated, so there is no other identity to attribute it
    # to. ``invited_by`` in the payload preserves the other half of the chain:
    # who granted the access that was just taken up.
    #
    # NOTE: a claim attempt with an unknown or spent token cannot be recorded
    # here. It 404s above, and with no invitation there is no tenant_id — and
    # core.audit_events is chained and RLS-scoped per tenant. Detecting token
    # guessing therefore needs a different mechanism than this trail; it is
    # called out in tasks/GAP-SEC-015.
    _audit_invitation(
        request,
        action="invitation.claimed",
        invitation_id=invite["invitation_id"],
        tenant_id=tenant_id,
        scheme_id=invite.get("scheme_id"),
        actor_user_id=user_id,
        payload={
            "invited_email": str(invite["email"]),
            "granted_role": str(invite["invited_role"]),
            "created_user_id": str(user_id),
            "invited_by": str(invite.get("invited_by") or ""),
        },
    )

    return {
        "message": "Account created successfully. You can now log in.",
        "user_id": str(user_id),
        "email": str(invite["email"]),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Email template
# ──────────────────────────────────────────────────────────────────────────────

def _build_invitation_email(to_name: str, claim_url: str, role: str, expires_days: int) -> str:
    """Generated function header.

    Function: _build_invitation_email
    Path: backend/routers/admin_invitations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role_display = role.replace("_", " ").title()
    return f"""
Hello {to_name},

You have been invited to join StrataOS as a <strong>{role_display}</strong>.

Click the link below to set your password and activate your account:

  {claim_url}

This invitation expires in {expires_days} day(s).

If you did not expect this invitation, please ignore this email.

— The StrataOS Team
""".strip()
