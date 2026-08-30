"""
# @featuretrace:user-management — Auth, identity, and building-scoped session routing for portal users.
# Layer: router
# Data flow: login/session/building-switch endpoints → core.users/core.user_role_assignments + Mongo legacy fallback (scope param: building|global)
# @featuretrace:multi-unit-ownership — POST /auth/switch-unit, POST /auth/add-unit, GET /auth/my-units.
# Layer: router
# Data flow: UnitSwitcher → POST /auth/switch-unit → user_units link check → re-issued JWT with the
#            unit_number claim → get_current_user overrides user["unit_number"] (building-scoped).
# Toggle: multi_unit_ownership
# Related: backend/utils/auth.py
#          backend/utils/unit_number.py (authorise_owner_unit)
#          backend/server.py #user-management
#          frontend/src/contexts/AuthContext.tsx
#          frontend/src/hooks/useActiveUnit.ts
# Tests: tests/backend/test_multi_unit_ownership.py

Authentication router — single source of truth for all auth endpoints.

Endpoints handled here:
  POST  /auth/register               — new user registration (rate limited)
  POST  /auth/login                  — credential-based login (rate limited)
  GET   /auth/me                     — get current user profile
  POST  /auth/impersonate            — super-admin user impersonation
  POST  /auth/forgot-password        — request password reset link (rate limited)
  POST  /auth/reset-password         — consume reset token and set new password (rate limited)
  POST  /auth/change-password        — authenticated self-service password change (rate limited)
  GET   /auth/registration-decision  — show approve/reject confirmation form (token-based)
  POST  /auth/registration-decision  — submit approve/reject decision (rate limited)

  GET   /mail/access                 — retrieve mail credentials for eligible users
  PUT   /mail/update-password        — self-service mail password update
  PUT   /mail/admin-update-password  — super-admin update any user's mail password
  POST  /admin/reset-user-passwords  — super-admin unified portal+mail password reset

All endpoints in server.py that duplicated these have been removed.
Rate limiting uses the shared limiter from utils.rate_limit.
"""

import base64
import hashlib
import html as html_lib
import os
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus

import asyncio
import jwt as pyjwt
import logging
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List

from config import JWT_SECRET, JWT_ALGORITHM
from database import db
from models.user import (
    AuthResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    UserCreate,
    UserLogin,
    UserResponse,
    UserRole,
    UserStatus,
)
from services.settings_service import get_general_settings_or_default as _get_general_settings_or_default
from utils.activity_helper import log_activity
from utils.auth import (
    create_token,
    effective_role,
    get_current_user,
    get_current_building,
    get_optional_building,
    DEFAULT_BUILDING_ID,
    hash_password,
    normalize_user_role,
    verify_password,
)
from utils.crypto import encrypt_sensitive, decrypt_sensitive, is_encrypted
from utils.email import get_email_template, send_email_async
from utils.helpers import create_audit_log, get_portal_url
from utils.name_utils import check_owner_name_against_roll
from utils.permissions import get_user_permissions, require_feature, user_to_response
from utils.rate_limit import rate_limit
from utils.request_metadata import request_metadata

# Defined BEFORE the optional-import blocks below, which log from their
# `except ImportError` handlers. Previously `logger` was assigned further down
# the module, so a genuine Postgres import failure raised
# `NameError: name 'logger' is not defined` at module scope instead of the
# intended warning — and because server.py swallows router ImportErrors as a
# warning, that would have taken every auth route to a silent 404 rather than
# degrading to Mongo-only.
logger = logging.getLogger(__name__)

# Phase E: Postgres identity integration
try:
    from db_postgres.repos import identity_repo
    from db_postgres.repos import config_repo
    from db_postgres.session import async_session_context
    from request_context import set_ctx_building_id

    POSTGRES_AVAILABLE = True
except ImportError:
    config_repo = None  # type: ignore[assignment]
    logger.warning("Postgres identity repo not available; register will use MongoDB only")
    POSTGRES_AVAILABLE = False

# Optional geo / device-fingerprint utilities
try:
    from utils.geo import (
        generate_device_fingerprint,
        get_real_ip,
        lookup_geo,
        parse_user_agent,
    )

    GEO_AVAILABLE = True
except ImportError:
    GEO_AVAILABLE = False

# ── Router ───────────────────────────────────────────────────────────────────

# No prefix — server.py's api_router contributes /api
router = APIRouter(prefix="")

# ── Constants ────────────────────────────────────────────────────────────────

# Sentinel 🛡️: Valid bcrypt hash used to prevent user enumeration via timing attacks.
# This ensures that verify_password() always executes even if the user is not found.
DUMMY_HASH = "$2b$12$1LDWevE1s9vJ1LVZ88MUmus2jPjNU/EyN0BhWHs6Alz/2tOScFk5y"

# Optional BCC address for new-user registration notifications.
NOTIFY_BCC_EMAIL = os.environ.get("REGISTRATION_NOTIFY_BCC_EMAIL", "").strip()
NOTIFY_BCC_NAME = "Building Administrator"


# ── Request/Response models ──────────────────────────────────────────────────


class BuildingSwitchRequest(BaseModel):
    building_id: str


class UnitSwitchRequest(BaseModel):
    unit_number: str


class EmailPreferenceRequest(BaseModel):
    primary_email: EmailStr


class AddUnitRequest(BaseModel):
    unit_number: str = Field(..., max_length=20,
                             description="Additional unit number to link to this owner account")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class ImpersonateRequest(BaseModel):
    user_id: str
    building_id: str


class MailAccessResponse(BaseModel):
    mail_username: str
    mail_password: str
    mail_url: str
    has_access: bool


class AdminMailPasswordUpdate(BaseModel):
    mail_username: str
    mail_password: str = Field(..., min_length=8, max_length=128)


class MailPasswordUpdate(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=128)


class AdminUserPasswordReset(BaseModel):
    """Unified reset — portal login + mail_password in MongoDB + Migadu server."""
    identifier: str  # portal email OR mail_username
    new_password: str = Field(..., min_length=8, max_length=128)
    reset_portal: bool = True
    reset_mail: bool = True


class BuildingSelectionRequest(BaseModel):
    building_id: str


class ResidentRegistrationInviteCreate(BaseModel):
    role: str = Field(default=UserRole.OWNER)
    unit_number: str = Field(..., max_length=20)
    full_name: str = Field(..., min_length=1, max_length=200)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=20)
    note: Optional[str] = Field(default=None, max_length=500)
    expires_days: int = Field(default=14, ge=1, le=60)


# ── Shared helpers ───────────────────────────────────────────────────────────


# Thin alias over the shared resolver. server.py previously carried a
# byte-identical private copy of this logic; both now delegate to
# utils.helpers.get_portal_url so an added env var or changed precedence cannot
# drift between the two modules. Kept as a module-level name because it is
# referenced throughout this file and patched by name in tests.
_get_portal_url = get_portal_url


def _hash_registration_invite_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _registration_invite_url(token: str) -> str:
    return f"{_get_portal_url()}/register?invite={quote_plus(token)}"


_STAFF_REVIEWER_ROLES = {UserRole.STRATA_ADMIN, UserRole.ADMIN_STAFF, UserRole.STRATA_MANAGER}
_STAFF_ROLE_LABELS = {
    UserRole.STRATA_MANAGER: "Strata Manager",
    UserRole.ADMIN_STAFF: "Admin Staff",
    UserRole.REAL_ESTATE_AGENT: "Real Estate Agent",
    UserRole.SERVICE_PROVIDER: "Service Provider",
}
_STAFF_USERS_TABS = {
    UserRole.STRATA_MANAGER: "management",
    UserRole.ADMIN_STAFF: "management",
    UserRole.REAL_ESTATE_AGENT: "agents",
    UserRole.SERVICE_PROVIDER: "service",
}


def _normalize_staff_role_input(role: str) -> str:
    """Generated function header.

    Function: _normalize_staff_role_input
    Path: backend/routers/auth.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return UserRole.ADMIN_STAFF if role == "reception" else role


def _get_staff_role_label(role: str) -> str:
    """Generated function header.

    Function: _get_staff_role_label
    Path: backend/routers/auth.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return _STAFF_ROLE_LABELS.get(role, role.replace("_", " ").title())


def _get_users_tab_for_role(role: str) -> str:
    """Generated function header.

    Function: _get_users_tab_for_role
    Path: backend/routers/auth.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return _STAFF_USERS_TABS.get(role, "management")


async def _get_staff_registration_reviewers(building_id: str) -> List[dict]:
    """Generated function header.

    Function: _get_staff_registration_reviewers
    Path: backend/routers/auth.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    member_user_ids = await db.memberships.distinct("user_id", {"building_id": building_id})
    reviewers = await db.users.find(
        {
            "$or": [
                # Building-scoped reviewers: must hold an active membership of THIS
                # building, so one building's staff never review another's residents.
                {
                    "id": {"$in": member_user_ids},
                    "role": {"$in": list(_STAFF_REVIEWER_ROLES)},
                    "is_active": True,
                },
                # super_admin is a platform-wide role and deliberately does NOT
                # carry per-building memberships, so it cannot be matched by the
                # membership branch above and needs its own clause.
                {"role": UserRole.SUPER_ADMIN, "is_active": True},
            ]
        },
        {"_id": 0, "id": 1, "email": 1, "full_name": 1, "role": 1},
    ).to_list(100)
    deduped: List[dict] = []
    seen_ids = set()
    for reviewer in reviewers:
        reviewer_id = reviewer.get("id")
        if reviewer_id and reviewer_id not in seen_ids:
            deduped.append(reviewer)
            seen_ids.add(reviewer_id)
    return deduped


def _require_resident_invite_role(role: str) -> str:
    if role not in {UserRole.OWNER, UserRole.TENANT, UserRole.GUEST}:
        raise HTTPException(status_code=400, detail="Resident invites are only available for owners, tenants, and guests.")
    return role


def _can_send_resident_invite(user: dict) -> bool:
    role = effective_role(user)
    return role in {UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER}


async def _get_building_public_doc(building_id: str) -> dict:
    building = await db.buildings.find_one(
        {"id": building_id, "is_active": True},
        {"_id": 0, "id": 1, "name": 1, "address": 1, "slug": 1, "description": 1},
    )
    if building:
        return building
    settings_doc = await _get_general_settings_or_default(
        building_id,
        {"_id": 0},
        fallback_building_id=DEFAULT_BUILDING_ID,
        settings_db=db,
    )
    return {
        "id": building_id,
        "name": settings_doc.get("building_name") or "Building",
        "address": settings_doc.get("building_address") or "",
        "slug": settings_doc.get("building_slug") or building_id,
    }


def _reg_decision_html_result(
        title: str, heading: str, body: str,
        color: str = "#16a34a", portal_url: str = "",
        building_name: str = "Our Residences",
        building_address: str = ""
) -> HTMLResponse:
    """Generated function header.

    Function: _reg_decision_html_result
    Path: backend/routers/auth.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    esc = html_lib.escape(portal_url or "http://localhost:3000")
    return HTMLResponse(content=f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html_lib.escape(title)}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;background:#f1f5f9;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}}
  .card{{max-width:520px;width:100%;background:#fff;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,.12);overflow:hidden;text-align:center}}
  .top{{background:{color};color:#fff;padding:32px 24px}}
  .top h1{{margin:0;font-size:22px;font-weight:700}}
  .body{{padding:28px 32px 24px}}
  .body p{{font-size:15px;color:#475569;line-height:1.6;margin:0 0 16px}}
  .btn{{display:inline-block;padding:12px 28px;border-radius:8px;font-weight:700;font-size:14px;text-decoration:none;background:#2F4F4F;color:#fff;margin-top:8px}}
  .sig{{font-size:11px;color:#94a3b8;margin-top:20px;padding-top:14px;border-top:1px solid #f1f5f9;line-height:1.6}}
</style>
</head>
<body>
  <div class="card">
    <div class="top"><h1>{html_lib.escape(heading)}</h1></div>
    <div class="body">
      {body}
      <a href="{esc}" class="btn">Return to Portal</a>
      <div class="sig">{html_lib.escape(building_name)} | Executive Committee | Strata Manager<br>
      A: {html_lib.escape(building_address)}</div>
    </div>
  </div>
</body></html>""")


async def _validate_reg_decision_token(token: str, action: str, portal_url: str, b_name: str = "Our Residences",
                                       b_addr: str = ""):
    """Returns (token_doc, target_user, None) or (None, None, HTMLResponse)."""
    token_doc = await db.registration_approval_tokens.find_one({"token": token, "action": action})
    if not token_doc:
        return None, None, _reg_decision_html_result(
            "Link Not Found", "Link Not Found",
            "<p>This approval link is invalid or has already been used. Please visit the portal to manage approvals.</p>",
            "#dc2626", portal_url, b_name, b_addr
        )
    if token_doc.get("used"):
        return None, None, _reg_decision_html_result(
            "Already Actioned", "Already Actioned",
            "<p>This registration has already been reviewed. No further action is needed.</p>",
            "#ca8a04", portal_url, b_name, b_addr
        )
    try:
        expires_at = datetime.fromisoformat(token_doc.get("expires_at", "").replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires_at:
            return None, None, _reg_decision_html_result(
                "Link Expired", "Link Expired",
                "<p>This approval link has expired. Please log in to the portal to review this registration, or contact the Strata Manager.</p>",
                "#dc2626", portal_url, b_name, b_addr
            )
    except Exception:
        pass
    target = await db.users.find_one({"id": token_doc["user_id"]})
    if not target:
        return None, None, _reg_decision_html_result("Not Found", "User Not Found",
                                                     "<p>The registration record could not be found.</p>", "#dc2626",
                                                     portal_url, b_name, b_addr)
    if target.get("status") != "pending_owner_approval":
        return None, None, _reg_decision_html_result(
            "Already Reviewed", "Already Reviewed",
            "<p>This registration has already been reviewed. No further action is needed.</p>",
            "#ca8a04", portal_url, b_name, b_addr
        )
    return token_doc, target, None


async def _calculate_risk_score(user_id: Optional[str], current: dict) -> tuple:
    """Compare current login context against last successful login.
    Returns (risk_score: int, risk_flags: list[str]).
    """
    if not user_id:
        return 0, []
    try:
        last = await db.login_audit_logs.find_one(
            {"user_id": user_id, "status": "success"}, sort=[("attempted_at", -1)]
        )
        if not last:
            return 0, []

        score, flags = 0, []
        current_country = current.get("geo", {}).get("country_code", "")
        last_country = last.get("geo", {}).get("country_code", "")
        if current_country and last_country and current_country != last_country:
            score += 40
            flags.append("new_country")
        if current.get("device_fingerprint") != last.get("device_fingerprint"):
            score += 25
            flags.append("new_device")
        if current.get("ip_address") != last.get("ip_address"):
            score += 10
            flags.append("new_ip")
        try:
            login_hour = datetime.fromisoformat(
                current["attempted_at"].replace("Z", "+00:00")
            ).hour
            if login_hour < 5:
                score += 15
                flags.append("odd_time")
        except Exception:
            pass
        return min(score, 100), flags
    except Exception as e:
        logger.debug(f"Risk score calculation failed: {e}")
        return 0, []


async def _log_login_attempt(
        user: Optional[dict],
        email: str,
        request: Request,
        status_str: str,
        failure_reason: Optional[str] = None,
        risk_score: int = 0,
        risk_flags: Optional[list] = None,
) -> Optional[dict]:
    """Insert a login audit record. Never raises — audit failure must not block login."""
    try:
        # Resolve BOTH addresses. get_real_ip returns one conflated value, which
        # made an internal result ambiguous: no forwarded header, an untrusted
        # proxy whose header was ignored, and a genuinely local caller all look
        # identical. ip_fields keeps ip_address meaning exactly what it did, and
        # adds the pair that tells those cases apart.
        from utils.client_ip import ip_fields

        _ips = ip_fields(request)
        ip_address = _ips["ip_address"]

        from utils.login_signals import collect_login_signals, is_hosting_provider

        _expected_origins = tuple(
            o.strip() for o in (os.getenv("FRONTEND_URL", "") or "").split(",") if o.strip()
        )
        _signals = collect_login_signals(request, expected_origins=_expected_origins)

        ua_string = request.headers.get("User-Agent", "")

        if GEO_AVAILABLE:
            device_info = parse_user_agent(ua_string)
            device_fingerprint = generate_device_fingerprint(ip_address, ua_string)
            cf_country = request.headers.get("CF-IPCountry", None)
            geo = lookup_geo(ip_address, cf_country)
        else:
            device_info = {
                "browser": "Unknown", "browser_version": "",
                "os": "Unknown", "os_version": "", "device_type": "desktop",
            }
            device_fingerprint = ""
            geo = {
                "country_code": "AU", "country_name": "Australia",
                "city": "Unknown", "latitude": None, "longitude": None,
                "timezone": "UTC", "isp": "Unknown",
            }

        # Datacentre vs residential ASN. Not a verdict — VPNs are legitimate —
        # but a VPS login and a home login are not the same event, and the log
        # should not render them identically.
        _is_hosting = is_hosting_provider((geo or {}).get("isp"))

        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": user["id"] if user else None,
            "email": email,
            "ip_address": ip_address,
            # NULL on either is meaningful — "no public address established" is
            # the diagnosis worth surfacing, so neither is backfilled from the
            # other. See backend/utils/client_ip.py.
            "public_ip": _ips["public_ip"],
            "local_ip": _ips["local_ip"],
            # Extra request signals — Client Hints, language, Origin/Referer,
            # TLS termination, fetch metadata. All headers the browser already
            # sends; no client-side fingerprinting. See utils/login_signals.py
            # for why each one earns its place.
            "signals": _signals,
            "is_hosting_provider": _is_hosting,
            "status": status_str,
            "failure_reason": failure_reason,
            "user_agent": ua_string,
            "device_fingerprint": device_fingerprint,
            "device_info": device_info,
            "geo": geo,
            "risk_score": risk_score,
            "risk_flags": risk_flags or [],
            "cf_ray": request.headers.get("CF-Ray"),
            "attempted_at": now,
            "is_test_data": request.headers.get("X-Test-Data", "").lower() == "true",
        }
        await db.login_audit_logs.insert_one(doc)
        return doc
    except Exception as e:
        logger.warning(f"Failed to log login attempt: {e}")
        return None


async def _send_suspicious_login_email(user: dict, audit_doc: dict):
    """Send security alert when risk_score >= 50."""
    try:
        email = user.get("email", "")
        mail_alias = user.get("mail_username", "")
        recipients = list({e for e in [email, mail_alias] if e})

        geo = audit_doc.get("geo", {})
        device = audit_doc.get("device_info", {})
        safe_name = html_lib.escape(user.get("full_name", "Resident"))
        safe_ip = html_lib.escape(audit_doc.get("ip_address", "Unknown"))
        safe_city = html_lib.escape(geo.get("city", "Unknown"))
        safe_country = html_lib.escape(geo.get("country_name", "Unknown"))
        safe_browser = html_lib.escape(device.get("browser", "Unknown"))
        safe_os = html_lib.escape(device.get("os", "Unknown"))
        safe_time = html_lib.escape(audit_doc.get("attempted_at", ""))
        safe_flags = html_lib.escape(", ".join(audit_doc.get("risk_flags", [])))

        html_content = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px">
            <div style="background:#dc2626;color:white;padding:20px;border-radius:8px 8px 0 0">
                <h2 style="margin:0">New Login Detected</h2>
                <p style="margin:5px 0 0">StrataOS Security Alert</p>
            </div>
            <div style="background:#f9fafb;padding:24px;border:1px solid #e5e7eb;border-radius:0 0 8px 8px">
                <p>Hi {safe_name},</p>
                <p>We detected a new login to your account that looks different from your usual activity.</p>
                <table style="width:100%;border-collapse:collapse;margin:16px 0">
                    <tr><td style="padding:10px;font-weight:bold">IP Address</td><td style="padding:10px">{safe_ip}</td></tr>
                    <tr><td style="padding:10px;font-weight:bold">Location</td><td style="padding:10px">{safe_city}, {safe_country}</td></tr>
                    <tr><td style="padding:10px;font-weight:bold">Device</td><td style="padding:10px">{safe_browser} on {safe_os}</td></tr>
                    <tr><td style="padding:10px;font-weight:bold">Time</td><td style="padding:10px">{safe_time}</td></tr>
                    <tr><td style="padding:10px;font-weight:bold">Risk Flags</td><td style="padding:10px;color:#dc2626">{safe_flags}</td></tr>
                </table>
                <p style="color:#dc2626;font-weight:bold">If this wasn't you, please reset your password immediately.</p>
                <p>— StrataOS Security Team</p>
            </div>
        </div>"""
        for recipient in recipients:
            await send_email_async(recipient, "New Login Detected", html_content)
    except Exception as e:
        logger.warning(f"Failed to send suspicious login email: {e}")


async def _log_password_change(
        *,
        user_id: str,
        email: str,
        full_name: str,
        method: str,
        changed_by: str,
        changed_by_id: Optional[str] = None,
        changed_by_name: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        success: bool = True,
        failure_reason: Optional[str] = None,
):
    """Write a password change audit record. Never raises."""
    try:
        await db.password_change_audit.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "email": email,
            "full_name": full_name,
            "method": method,
            "changed_by": changed_by,
            "changed_by_id": changed_by_id,
            "changed_by_name": changed_by_name,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "success": success,
            "failure_reason": failure_reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass


# ── Multi-unit helpers ────────────────────────────────────────────────────────


async def _get_user_owned_units(user, building_id: str) -> list[str]:
    """Return all active unit_numbers for a user in the current building.

    building_id is used by the TenantScopedDatabase wrapper via request context;
    it is accepted as a parameter for call-site clarity, not passed explicitly
    to the query (which would bypass the plan_id legacy-alias $or injection).
    Sort: purely-numeric unit numbers numerically, alphanumeric ones lexicographically.

    ``user`` may be a full user dict (containing an "id" key) or a bare user_id
    string — both forms are accepted so callers do not need to wrap strings.
    """
    user_id = user if isinstance(user, str) else user["id"]
    cursor = db.user_units.find(
        {"user_id": user_id, "is_active": True},
        {"_id": 0, "unit_number": 1},
    )
    docs = await cursor.to_list(100)
    units = {d["unit_number"] for d in docs if d.get("unit_number")}

    # Legacy fallback: resolve ownership from units rows when user_units links
    # are missing (common in old East Gate snapshots).
    # Skip when caller passed a bare user_id string (no email/name available).
    if not units:
        email = (user.get("email") or "").strip() if isinstance(user, dict) else ""
        full_name = (user.get("full_name") or "").strip() if isinstance(user, dict) else ""
        if email or full_name:
            legacy_owner_filters: list[dict] = []
            if email:
                legacy_owner_filters.extend([
                    {"owner_email": email},
                    {"owner_email_b": email},
                ])
            if full_name:
                legacy_owner_filters.extend([
                    {"owner_name": full_name},
                    {"owner_name_b": full_name},
                ])

            if legacy_owner_filters:
                legacy_units = await db.units.find(
                    {
                        "building_id": building_id,
                        "$or": legacy_owner_filters,
                    },
                    {"_id": 0, "unit_number": 1},
                ).to_list(100)
                units.update(d["unit_number"] for d in legacy_units if d.get("unit_number"))

    units = list(units)
    units.sort(key=lambda u: (0, int(u)) if str(u).isdigit() else (1, u))
    return units


async def _enrich_owned_units(user: dict, building_id: str) -> dict:
    """Inject owned_units list into a user dict before passing to user_to_response."""
    user["owned_units"] = await _get_user_owned_units(user, building_id)
    if not user.get("unit_number") and user["owned_units"]:
        user["unit_number"] = user["owned_units"][0]
    return user


# ── Resident registration invites ─────────────────────────────────────────────


@router.post("/auth/registration-invites")
async def create_resident_registration_invite(
        body: ResidentRegistrationInviteCreate,
        background_tasks: BackgroundTasks,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Create a building-scoped resident sign-up link with prefilled registration data."""
    if not _can_send_resident_invite(current_user):
        raise HTTPException(status_code=403, detail="Only Strata Admins and Strata Managers can send resident invites.")

    invite_role = _require_resident_invite_role(body.role)
    unit_number = body.unit_number.strip()
    full_name = body.full_name.strip()
    if not unit_number:
        raise HTTPException(status_code=400, detail="Unit number is required.")
    if not full_name:
        raise HTTPException(status_code=400, detail="Name is required.")

    unit_doc = await db.units.find_one({"unit_number": unit_number}, {"_id": 0, "unit_number": 1})
    if not unit_doc:
        raise HTTPException(status_code=400, detail="Unit not found in this building.")

    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=body.expires_days)
    invite_id = str(uuid.uuid4())
    await db.resident_registration_invites.insert_one({
        "id": invite_id,
        "building_id": building_id,
        "token_hash": _hash_registration_invite_token(raw_token),
        "role": invite_role,
        "unit_number": unit_number,
        "full_name": full_name,
        "email": str(body.email) if body.email else None,
        "phone": body.phone or "",
        "note": body.note or "",
        "status": "pending",
        "created_by": current_user["id"],
        "created_by_name": current_user.get("full_name", ""),
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "used_at": None,
        "used_by_user_id": None,
    })

    invite_url = _registration_invite_url(raw_token)
    building = await _get_building_public_doc(building_id)
    if body.email:
        safe_name = html_lib.escape(full_name)
        safe_building = html_lib.escape(building.get("name") or "your building")
        safe_unit = html_lib.escape(unit_number)
        safe_url = html_lib.escape(invite_url)
        email_html = (
            f"<h2>Create your {safe_building} portal account</h2>"
            f"<p>Hello {safe_name},</p>"
            f"<p>You have been invited to create a portal account for Unit {safe_unit} at {safe_building}.</p>"
            f"<p><a href=\"{safe_url}\">Create your account</a></p>"
            f"<p>This link expires in {body.expires_days} days.</p>"
        )
        email_text = (
            f"Create your {building.get('name') or 'building'} portal account for Unit {unit_number}:\n"
            f"{invite_url}\n\nThis link expires in {body.expires_days} days."
        )
        background_tasks.add_task(
            send_email_async,
            str(body.email),
            f"Create your {building.get('name') or 'building'} portal account",
            email_html,
            email_text,
            context=f"resident_registration_invite:{invite_id}",
        )

    return {
        "id": invite_id,
        "invite_url": invite_url,
        "expires_at": expires_at.isoformat(),
        "email_sent": bool(body.email),
    }


@router.get("/auth/registration-invites/{token}")
@rate_limit("rate_limit_registration_invite_lookup", 20)
async def get_resident_registration_invite(request: Request, token: str):
    """Resolve an opaque resident registration invite for public sign-up prefill.

    Unauthenticated: the opaque token is the only credential, so this is rate
    limited like every other public auth endpoint in this module. It discloses
    building name/address plus the invited resident's name, unit, email and
    phone, which is exactly the prefill the sign-up page needs but is also
    worth not leaving open to unbounded probing.
    """
    token_hash = _hash_registration_invite_token(token)
    invite = await db.resident_registration_invites.find_one(
        {"token_hash": token_hash, "status": "pending"},
        {"_id": 0, "token_hash": 0},
    )
    if not invite:
        raise HTTPException(status_code=404, detail="Registration invite not found or already used.")

    try:
        expires_at = datetime.fromisoformat(str(invite.get("expires_at")).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=404, detail="Registration invite is invalid.")
    if expires_at < datetime.now(timezone.utc):
        await db.resident_registration_invites.update_one(
            {"id": invite["id"]},
            {"$set": {"status": "expired", "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        raise HTTPException(status_code=410, detail="Registration invite has expired.")

    building_id = invite.get("building_id")
    if not building_id:
        raise HTTPException(status_code=404, detail="Registration invite is invalid.")
    building = await _get_building_public_doc(building_id)
    return {
        "id": invite["id"],
        "building": building,
        "role": invite.get("role") or UserRole.OWNER,
        "unit_number": invite.get("unit_number") or "",
        "full_name": invite.get("full_name") or "",
        "email": invite.get("email") or "",
        "phone": invite.get("phone") or "",
        "expires_at": invite.get("expires_at"),
    }


# ── Registration ─────────────────────────────────────────────────────────────


@router.post("/auth/register", response_model=AuthResponse)
@rate_limit("rate_limit_register", 5)
async def register(request: Request, user_data: UserCreate, background_tasks: BackgroundTasks,
                   _building_id: str = Depends(get_optional_building)):
    """
    Register a new user.

    Creates a new user account. New users require approval before gaining full
    access. Returns an authentication token with the pending-approval user object.
    """
    invite_doc = None
    if user_data.invite_token:
        invite_doc = await db.resident_registration_invites.find_one(
            {"token_hash": _hash_registration_invite_token(user_data.invite_token), "status": "pending"},
            {"_id": 0},
        )
        if not invite_doc:
            raise HTTPException(status_code=400, detail="Registration invite not found or already used.")
        try:
            invite_expires_at = datetime.fromisoformat(str(invite_doc.get("expires_at")).replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Registration invite is invalid.")
        if invite_expires_at < datetime.now(timezone.utc):
            await db.resident_registration_invites.update_one(
                {"id": invite_doc["id"]},
                {"$set": {"status": "expired", "updated_at": datetime.now(timezone.utc).isoformat()}},
            )
            raise HTTPException(status_code=400, detail="Registration invite has expired.")
        if str(invite_doc.get("building_id")) != str(_building_id):
            raise HTTPException(status_code=400, detail="Registration invite is for a different building.")
        if str(invite_doc.get("unit_number") or "") != str(user_data.unit_number or ""):
            raise HTTPException(status_code=400, detail="Registration invite is for a different unit.")
        invite_role = invite_doc.get("role")
        if invite_role and invite_role != user_data.role:
            raise HTTPException(status_code=400, detail="Registration invite is for a different user type.")

    # ⚡ Optimized by Bolt: Parallelize initial validation queries
    # Stage 1: Check existing user and fetch admins (needed for all paths)
    # ⚡ Optimized by Bolt: Parallelize initial validation queries
    # Stage 1: Check existing user (needed for all paths)
    # Accept both real email and portal email for duplicate detection.
    _reg_email = user_data.email
    existing = await db.users.find_one(
        {"$or": [{"email": _reg_email}, {"portal_email": _reg_email}]}
    )

    _claiming_existing = None  # set to the existing doc when claiming an imported account
    if existing:
        if existing.get("status") == "archived":
            now_ts = datetime.now(timezone.utc).isoformat()
            # Preserve the original archived record intact for audit (7-year retention).
            # New registration intent is stored in pending_return_details so the admin
            # can see old vs. new side-by-side when deciding whether to restore.
            # password_hash is the one field we update directly: avoids forcing a password
            # reset after admin restore, since the user just set a fresh password.
            _new_end_date = getattr(user_data, "end_date", None)
            _pending_details: dict = {
                "full_name": user_data.full_name,
                "phone": user_data.phone or "",
                "unit_number": user_data.unit_number or "",
                "by_laws_acknowledged": user_data.by_laws_acknowledged or False,
                "requested_at": now_ts,
            }
            if _new_end_date:
                _pending_details["end_date"] = _new_end_date
            _return_update = {
                "return_requested_at": now_ts,
                "updated_at": now_ts,
                "password_hash": hash_password(user_data.password),
                "pending_return_details": _pending_details,
            }
            # Run DB update and building-scoped reviewer fetch concurrently.
            _, admin_users = await asyncio.gather(
                db.users.update_one({"id": existing["id"]}, {"$set": _return_update}),
                _get_staff_registration_reviewers(_building_id),
            )
            if admin_users:
                returning_role = existing.get("role", "user").capitalize()
                _old_unit = existing.get("unit_number") or ""
                _req_unit = user_data.unit_number or ""
                _unit_label = f"Unit {_req_unit}" if _req_unit else (f"Unit {_old_unit}" if _old_unit else "")
                # Build old-vs-new detail rows for email — all values escaped individually.
                _rows = f"<tr><td><strong>Name (new)</strong></td><td>{html_lib.escape(user_data.full_name)}</td></tr>"
                if existing.get("full_name") != user_data.full_name:
                    _rows += f"<tr><td><strong>Name (old)</strong></td><td>{html_lib.escape(existing.get('full_name', ''))}</td></tr>"
                if _req_unit:
                    _rows += f"<tr><td><strong>Unit (requested)</strong></td><td>{html_lib.escape(_req_unit)}</td></tr>"
                if _old_unit:
                    _rows += f"<tr><td><strong>Unit (previous)</strong></td><td>{html_lib.escape(_old_unit)}</td></tr>"
                if _new_end_date:
                    _rows += f"<tr><td><strong>End Date</strong></td><td>{html_lib.escape(str(_new_end_date))}</td></tr>"
                notifications = []
                for admin in admin_users:
                    notifications.append({
                        "id": str(uuid.uuid4()),
                        "user_id": admin["id"],
                        "title": f"Returning {returning_role}: {user_data.full_name}",
                        "message": (
                            f"{user_data.full_name} ({existing['email']}) is a previously archived "
                            f"{returning_role.lower()} requesting to return"
                            f"{' to ' + _unit_label if _unit_label else ''}. "
                            f"Previous visit data is preserved. "
                            f"Restore their account from the Expired Accounts page."
                        ),
                        "type": "returning_user",
                        "related_id": existing["id"],
                        "link": "/admin/expired-accounts",
                        "is_read": False,
                        "created_at": now_ts,
                    })
                await db.user_notifications.insert_many(notifications)
                portal_url = _get_portal_url()
                safe_portal_url = html_lib.escape(portal_url)
                email_html = (
                    f"<h2>Returning {html_lib.escape(returning_role)} Account Request</h2>"
                    f"<p><strong>{html_lib.escape(user_data.full_name)}</strong> ({html_lib.escape(existing['email'])}) "
                    f"was previously archived and has requested to return. "
                    f"The original visit record is preserved; new details are shown below:</p>"
                    f"<table cellpadding='4'>{_rows}</table>"
                    f"<p>Please review and restore their account from the "
                    f"<a href=\"{safe_portal_url}/admin/expired-accounts\">Expired &amp; Archived Accounts</a> page.</p>"
                )
                email_text = (
                    f"Returning {returning_role}: {user_data.full_name} ({existing['email']}) has requested to return"
                    f"{' to ' + _unit_label if _unit_label else ''}. "
                    f"Original visit record preserved. Restore: {portal_url}/admin/expired-accounts"
                )
                email_subject = f"Returning {returning_role} Request: {user_data.full_name}"
                sent_emails = set()
                for admin in admin_users:
                    addr = admin.get("email")
                    if addr and addr not in sent_emails:
                        background_tasks.add_task(send_email_async, addr, email_subject, email_html, email_text,
                                                  context=f"returning_user:{existing['id']}")
                        sent_emails.add(addr)
                if NOTIFY_BCC_EMAIL and NOTIFY_BCC_EMAIL not in sent_emails:
                    background_tasks.add_task(send_email_async, NOTIFY_BCC_EMAIL, email_subject, email_html, email_text,
                                              context=f"returning_user_bcc:{existing['id']}")
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "archived_user_return_request",
                    "message": (
                        "Your account was previously archived. Your request to return has been sent to "
                        "the administration team. You will be notified once your account has been reviewed."
                    ),
                    "returning_user": True,
                }
            )

        # Unblocking gate: fully-formed accounts (password + unit) that are still pending
        # approval would receive a confusing "try to login" message when they really can't.
        # Instead, try to auto-approve via strata-roll name match; if that fails, ensure
        # the Strata Manager has been notified and return a meaningful pending message.
        if (
                existing.get("password_hash")
                and existing.get("unit_number")
                and existing.get("status") != "archived"
                and not existing.get("is_approved")
        ):
            _ex_id = existing.get("id") or str(existing.get("_id", ""))
            _pend_filter = {"id": _ex_id} if existing.get("id") else {"_id": existing["_id"]}
            _ex_role = existing.get("role", "")
            _pend_tab_map = {"owner": "owners", "tenant": "residents", "guest": "residents"}
            _pend_tab = _pend_tab_map.get(_ex_role, "owners")
            _pend_role_label = (_ex_role or "User").capitalize()

            # Only attempt strata-roll auto-approval for owners — the roll contains owner
            # names, not tenant/guest names, so matching a tenant against it would be wrong.
            _pend_match = False
            if _ex_role == "owner":
                _pend_unit = await db.units.find_one({"unit_number": existing["unit_number"]})
                _pend_rp = (_pend_unit.get("owner_name") or "").strip() if _pend_unit else ""
                _pend_rs = (_pend_unit.get("owner_name_b") or "").strip() if _pend_unit else ""
                _pend_match = (
                        bool(_pend_rp or _pend_rs)
                        and check_owner_name_against_roll(user_data.full_name, _pend_rp, _pend_rs)
                )

            if _pend_match:
                await db.users.update_one(
                    _pend_filter,
                    {"$set": {
                        "is_approved": True,
                        "is_active": True,
                        "is_name_flagged": False,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }}
                )
                # Ensure a membership exists so login works immediately after approval.
                _pend_user_id = existing.get("id") or str(existing.get("_id", ""))
                if _pend_user_id:
                    _pend_mem = await db.memberships.find_one({
                        "user_id": _pend_user_id,
                        "building_id": _building_id,
                        "is_active": True,
                    })
                    if not _pend_mem:
                        await db.memberships.insert_one({
                            "id": str(uuid.uuid4()),
                            "user_id": _pend_user_id,
                            "building_id": _building_id,
                            "roles": [existing.get("role", "owner")],
                            "is_active": True,
                            "is_primary": True,
                            "units": [existing["unit_number"]],
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        })
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "pending_now_approved",
                        "message": (
                            f"Your identity has been verified against the strata roll. "
                            f"Your account for Unit {existing['unit_number']} is now active — please sign in."
                        ),
                        "unit_number": existing["unit_number"],
                    }
                )
            else:
                # Name not on strata roll, no roll data, or non-owner role — ensure admins
                # have been notified. Idempotent: only re-notify after 24 h gap.
                _cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
                _recent = await db.user_notifications.find_one({
                    "related_id": _ex_id,
                    "type": "user_approval",
                    "created_at": {"$gte": _cutoff},
                })
                if not _recent:
                    _notif_admins = await _get_staff_registration_reviewers(_building_id)
                    if _notif_admins:
                        _encoded_pend = quote_plus(existing.get("full_name", ""))
                        _pend_link = f"/admin/users?tab={_pend_tab}&search={_encoded_pend}"
                        await db.user_notifications.insert_many([
                            {
                                "id": str(uuid.uuid4()),
                                "user_id": a["id"],
                                "title": (
                                    f"Pending {_pend_role_label} Account — Approval Required "
                                    f"(Unit {existing['unit_number']})"
                                ),
                                "message": (
                                    f"{existing.get('full_name')} ({existing.get('email')}) is registered "
                                    f"as {_pend_role_label} for Unit {existing['unit_number']} "
                                    "but has not been approved. Please review and approve their account."
                                ),
                                "type": "user_approval",
                                "related_id": _ex_id,
                                "link": _pend_link,
                                "is_read": False,
                                "created_at": datetime.now(timezone.utc).isoformat(),
                            }
                            for a in _notif_admins
                        ])
                        _pend_portal = _get_portal_url()
                        _pend_html = (
                            f"<p><strong>{html_lib.escape(existing.get('full_name', ''))} "
                            f"({html_lib.escape(existing.get('email', ''))})</strong> is registered as "
                            f"{html_lib.escape(_pend_role_label)} for Unit "
                            f"{html_lib.escape(str(existing['unit_number']))} but is pending approval. "
                            f"<a href=\"{html_lib.escape(_pend_portal + _pend_link)}\">Review and approve</a> "
                            f"their account.</p>"
                        )
                        for _a in _notif_admins:
                            _addr = _a.get("email")
                            if _addr:
                                background_tasks.add_task(
                                    send_email_async, _addr,
                                    f"Pending Approval — {existing.get('full_name')} (Unit {existing['unit_number']})",
                                    _pend_html,
                                    context=f"pending_approval_reminder:{_ex_id}"
                                )
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "pending_approval",
                        "message": (
                            "Your account is awaiting approval by the Strata Manager. "
                            "You will receive an email once it has been reviewed. "
                            "If you have not heard back, please contact building management."
                        ),
                        "unit_number": existing.get("unit_number", ""),
                    }
                )

        # ⚠️ ACCOUNT-TAKEOVER SURFACE — read before testing this endpoint.
        # This branch does NOT 409. It merges the submitted registration into an EXISTING
        # user: sets their password, activates them, and overwrites full_name. Any imported
        # owner stub qualifies, because imports carry no password_hash. That is intended
        # (it is how a real owner claims the record created for them), but it means POSTing
        # a real person's email here silently takes over their account. On 2026-08-27 a
        # diagnostic call did exactly that to two East Gate owners, replacing a real name
        # with "Test Person"; it was reverted from the 2026-08-21 backup. Probe this route
        # only with an address that cannot belong to anyone (e.g. probe+x@example.invalid).
        #
        # Claim path is taken for two cases:
        #   (a) No password_hash — MRI/import stub with no active login credentials.
        #   (b) password_hash set but unit_number is blank — account was partially created
        #       (e.g. admin-created without assigning a unit) and cannot meaningfully log in.
        # In both cases merging the submitted registration data completes the account.
        if not existing.get("password_hash") or not existing.get("unit_number"):
            _claiming_existing = existing
        else:
            # User has a real account with a password.

            # Multi-unit owner path: active owner trying to register a new unit in the same building.
            if (
                    user_data.role == "owner"
                    and existing.get("role") == "owner"
                    and existing.get("is_active", True)
                    and existing.get("status") != "archived"
                    and user_data.unit_number
            ):
                already_linked = await db.user_units.find_one({
                    "user_id": existing.get("id", ""),
                    "unit_number": user_data.unit_number,
                    "is_active": True,
                })
                if already_linked:
                    # Repair: if the user has no active membership (e.g. imported stub or
                    # data migration gap), create one so their login actually works.
                    _ex_user_id = existing.get("id", "")
                    if _ex_user_id:
                        _existing_mem = await db.memberships.find_one({
                            "user_id": _ex_user_id,
                            "building_id": _building_id,
                            "is_active": True,
                        })
                        if not _existing_mem:
                            await db.memberships.insert_one({
                                "id": str(uuid.uuid4()),
                                "user_id": _ex_user_id,
                                "building_id": _building_id,
                                "roles": [existing.get("role", "owner")],
                                "is_active": True,
                                "is_primary": True,
                                "units": [user_data.unit_number],
                                "created_at": datetime.now(timezone.utc).isoformat(),
                            })
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "already_registered",
                            "message": (
                                f"You are already registered for Unit {user_data.unit_number}. "
                                "Please try to login, or reset your password."
                            ),
                            "unit_number": user_data.unit_number,
                        }
                    )
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "owner_exists_add_unit",
                        "message": (
                            "You already have an owner account. Please log in and add additional units "
                            "from your Profile → My Units page."
                        ),
                        "existing_owner": True,
                    }
                )

            _existing_unit = existing.get("unit_number") or ""
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "already_registered",
                    "message": (
                        f"You are already registered{' for Unit ' + _existing_unit if _existing_unit else ''}. "
                        "Please try to login, or reset your password."
                    ),
                    "unit_number": _existing_unit,
                }
            )
    else:
        _claiming_existing = None

    if user_data.role not in ["owner", "tenant", "guest"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid role. Public registration is only allowed for owners, tenants, and guests."
        )

    if user_data.role in ["tenant", "guest"] and not user_data.by_laws_acknowledged:
        raise HTTPException(status_code=400, detail="You must acknowledge the by-laws before registering")

    if user_data.role == "guest":
        if not user_data.end_date:
            raise HTTPException(status_code=400, detail="Guests must specify an end date for their stay")
        try:
            from datetime import date
            end_date_obj = datetime.fromisoformat(user_data.end_date.replace('Z', '+00:00')).date()
            days_diff = (end_date_obj - date.today()).days
            if days_diff < 1:
                raise HTTPException(status_code=400, detail="End date must be in the future")
            if days_diff >= 365:
                raise HTTPException(status_code=400,
                                    detail=f"Guest stays must be less than 365 days (requested: {days_diff} days)")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid date format: {str(e)}")

    # Stage 2: Validate unit and owners (only for new users)
    has_unit = bool(user_data.unit_number)
    potential_owner_approval = user_data.role in ["tenant", "guest"] and has_unit
    unit_doc = None
    unit_owners = []

    if has_unit:
        val_tasks = [db.units.find_one({"unit_number": user_data.unit_number})]
        if potential_owner_approval:
            val_tasks.append(db.users.find({
                "unit_number": user_data.unit_number,
                "role": "owner",
                "is_active": True,
                "is_approved": True
            }).to_list(10))

        val_results = await asyncio.gather(*val_tasks)
        unit_doc = val_results[0]
        if potential_owner_approval and len(val_results) > 1:
            unit_owners = val_results[1]

    if has_unit and not unit_doc:
        raise HTTPException(status_code=400, detail="Invalid unit number. Please select a valid unit.")

    # Validate additional units (owners only; deduplicate; max 10 total including primary)
    additional_units: list[str] = []
    _additional_unit_numbers = getattr(user_data, "additional_unit_numbers", None) or []
    if user_data.role == "owner" and _additional_unit_numbers:
        # Allow at most 9 additional when a primary unit is present (10 total cap)
        _max_additional = 9 if user_data.unit_number else 10
        if len(_additional_unit_numbers) > _max_additional:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum {_max_additional} additional units allowed (10 total per account)."
            )
        seen = {user_data.unit_number} if user_data.unit_number else set()
        for extra_un in _additional_unit_numbers[:_max_additional]:
            extra_un = extra_un.strip()
            if not extra_un or extra_un in seen:
                continue
            extra_doc = await db.units.find_one({"unit_number": extra_un}, {"_id": 0, "unit_number": 1})
            if not extra_doc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid additional unit number: {extra_un}. Please select valid units."
                )
            additional_units.append(extra_un)
            seen.add(extra_un)

    # When claiming an imported account, reuse the existing record's id.
    user_id = (
        (_claiming_existing.get("id") or str(_claiming_existing["_id"]))
        if _claiming_existing
        else str(uuid.uuid4())
    )
    now = datetime.now(timezone.utc).isoformat()
    requires_owner_approval = potential_owner_approval and bool(unit_owners)

    # Owner approval: auto-approve when the submitted name matches the strata roll (primary
    # or secondary). Both owners on a joint-ownership unit are verified the same way.
    # Requires non-empty roll data — missing strata data → pending admin verification.
    # Tenants and guests always require explicit admin approval.
    _roll_primary = (unit_doc.get("owner_name") or "").strip() if unit_doc else ""
    _roll_secondary = (unit_doc.get("owner_name_b") or "").strip() if unit_doc else ""
    _owner_matches_roll = (
            user_data.role == "owner"
            and bool(_roll_primary or _roll_secondary)
            and check_owner_name_against_roll(user_data.full_name, _roll_primary, _roll_secondary)
    )
    is_approved = _owner_matches_roll
    initial_status = (
        UserStatus.PENDING_OWNER_APPROVAL if requires_owner_approval else UserStatus.ACTIVE
    )

    user_doc = {
        "id": user_id,
        "email": user_data.email,
        "password_hash": hash_password(user_data.password),
        "full_name": user_data.full_name,
        "unit_number": user_data.unit_number,
        "phone": user_data.phone,
        "role": user_data.role,
        "is_active": True,
        "is_approved": is_approved,
        "status": initial_status,
        "owner_approved": False,
        "owner_approved_by": None,
        "owner_approved_at": None,
        "profile_image": None,
        "custom_permissions": {},
        "by_laws_acknowledged": user_data.by_laws_acknowledged or False,
        "by_laws_acknowledgment_date": now if user_data.by_laws_acknowledged else None,
        "created_at": now,
        "updated_at": now
    }

    # Flag owners whose name doesn't appear on the strata roll at all.
    # Already-approved owners (matched the roll) are never flagged.
    if user_data.role == "owner" and unit_doc and not _owner_matches_roll:
        user_doc["is_name_flagged"] = True
        user_doc["flag_reason"] = "name_mismatch"

    # ⚡ Optimized by Bolt: Parallelize persistence operations
    # Always create a membership record so the user appears in get_users (which joins from memberships).
    # users and memberships are GLOBAL_COLLECTIONS — building_id must be injected explicitly here.
    all_units = ([user_data.unit_number] if user_data.unit_number else []) + additional_units
    membership_doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "building_id": _building_id,
        "roles": [user_data.role],
        "is_active": True,
        "is_primary": True,
        "units": all_units,
        "created_at": now,
    }
    _claim_membership_inserted = False  # tracks whether we inserted a membership during a claim
    if _claiming_existing:
        # Claim an imported account: update the existing record rather than inserting a new one.
        # Ensure the record has a string `id` field (MRI-imported docs may only have `_id`).
        _claim_filter = (
            {"id": user_id} if _claiming_existing.get("id")
            else {"_id": _claiming_existing["_id"]}
        )
        _claim_set = {k: v for k, v in user_doc.items() if k != "_id"}
        persistence_tasks = [db.users.update_one(_claim_filter, {"$set": _claim_set})]
        # Only create a membership if none exists for this user+building yet.
        _existing_mem = await db.memberships.find_one(
            {"user_id": user_id, "building_id": _building_id, "is_active": True}
        )
        if not _existing_mem:
            persistence_tasks.append(db.memberships.insert_one(membership_doc))
            _claim_membership_inserted = True
    else:
        persistence_tasks = [db.users.insert_one(user_doc), db.memberships.insert_one(membership_doc)]
    user_unit_ids: list[str] = []
    acknowledgment_id = None

    if user_data.unit_number:
        from datetime import date
        role_at_unit = user_data.role if user_data.role in ["owner", "tenant", "guest"] else "guest"
        start_date = date.today()
        end_date = None
        expiration_date = None
        auto_expire_enabled = False
        if user_data.role == "guest" and user_data.end_date:
            end_date = user_data.end_date
            auto_expire_enabled = True
        elif user_data.role == "tenant":
            expiration_date = (start_date + timedelta(days=366)).isoformat()
            auto_expire_enabled = True

        primary_uu_id = str(uuid.uuid4())
        user_unit_ids.append(primary_uu_id)
        persistence_tasks.append(db.user_units.insert_one({
            "id": primary_uu_id,
            "user_id": user_id,
            "unit_number": user_data.unit_number,
            "role_at_unit": role_at_unit,
            "start_date": start_date.isoformat(),
            "end_date": end_date,
            "actual_end_date": None,
            "is_active": is_approved,
            "is_primary": True,
            "lease_document_id": None,
            "lease_start_date": None,
            "lease_end_date": None,
            "auto_expire_enabled": auto_expire_enabled,
            "expiration_date": expiration_date,
            "guest_type": None,
            "host_user_id": None,
            "approved_by": None,
            "approved_date": None,
            "approval_notes": None,
            "created_at": now,
            "updated_at": now
        }))

        # Additional units (owners only) — non-primary user_units records
        for extra_un in additional_units:
            extra_uu_id = str(uuid.uuid4())
            user_unit_ids.append(extra_uu_id)
            persistence_tasks.append(db.user_units.insert_one({
                "id": extra_uu_id,
                "user_id": user_id,
                "unit_number": extra_un,
                "role_at_unit": "owner",
                "start_date": start_date.isoformat(),
                "end_date": None,
                "actual_end_date": None,
                "is_active": is_approved,
                "is_primary": False,
                "lease_document_id": None,
                "lease_start_date": None,
                "lease_end_date": None,
                "auto_expire_enabled": False,
                "expiration_date": None,
                "guest_type": None,
                "host_user_id": None,
                "approved_by": None,
                "approved_date": None,
                "approval_notes": None,
                "created_at": now,
                "updated_at": now
            }))

    if user_data.by_laws_acknowledged:
        acknowledgment_id = str(uuid.uuid4())
        persistence_tasks.append(db.by_laws_acknowledgments.insert_one({
            "id": acknowledgment_id,
            "user_id": user_id,
            "by_laws_version": "2026-v1",
            "acknowledged_date": now,
            "ip_address": None,
            "user_agent": None,
            "by_laws_content_snapshot": None,
            "is_current": True,
            "created_at": now
        }))

    try:
        await asyncio.gather(*persistence_tasks)
    except Exception as e:
        if _claiming_existing:
            # Fully restore the pre-existing record to its state before the claim attempt.
            # $set all original fields; $unset any fields that user_doc added but the
            # original did not have (e.g. phone, by_laws_acknowledged, status on import stubs).
            _revert_filter = (
                {"id": user_id} if _claiming_existing.get("id")
                else {"_id": _claiming_existing["_id"]}
            )
            _original_fields = {k: v for k, v in _claiming_existing.items() if k != "_id"}
            _new_fields_to_unset = {
                k: "" for k in user_doc if k != "_id" and k not in _original_fields
            }
            _revert_op: dict = {"$set": _original_fields}
            if _new_fields_to_unset:
                _revert_op["$unset"] = _new_fields_to_unset
            rollback_tasks = [db.users.update_one(_revert_filter, _revert_op)]
            # If we inserted a new membership during this claim attempt, remove it.
            if _claim_membership_inserted:
                rollback_tasks.append(db.memberships.delete_one({"id": membership_doc["id"]}))
        else:
            rollback_tasks = [db.users.delete_one({"id": user_id})]
        for _uu_id in user_unit_ids:
            rollback_tasks.append(db.user_units.delete_one({"id": _uu_id}))
        if acknowledgment_id:
            rollback_tasks.append(db.by_laws_acknowledgments.delete_one({"id": acknowledgment_id}))
        await asyncio.gather(*rollback_tasks)
        raise HTTPException(status_code=500, detail=f"Registration failed during record creation: {str(e)}")

    # Phase E: Postgres user creation (after MongoDB persistence succeeds)
    # Resolve tenant_id for Postgres user context
    _postgres_tenant_id = None
    if POSTGRES_AVAILABLE:
        try:
            # Resolve the building's REAL tenant from core.schemes rather than deriving
            # one. `uuid5(NAMESPACE_DNS, f"building-{building_id}")` was used here, which
            # for East Gate yields 928bd124-2840-57a6-9168-8991ccbe82ff while the actual
            # tenant is 9e9d75c2-bd92-4695-8487-1592018c3af9. No such row exists in
            # core.tenants, so users_tenant_id_fkey rejected every insert, the except
            # below swallowed it as a warning, and NO registration has ever produced a
            # core.users row — despite this function's docstring promising that "new
            # accounts (seeded or registered after Phase C) are always in Postgres".
            # Login survived only because it falls back to MongoDB when the Postgres
            # lookup misses. building_id is the scheme_number (e.g. "13195").
            import uuid as uuid_module
            _ns = uuid_module.NAMESPACE_DNS
            _postgres_tenant_id = None
            if _building_id:
                _scheme = await identity_repo.get_scheme_by_number(str(_building_id))
                if _scheme and _scheme.get("tenant_id"):
                    _postgres_tenant_id = str(_scheme["tenant_id"])
                else:
                    # A building with no scheme row cannot have a tenant. Skip the write
                    # rather than invent an id that will fail the foreign key anyway.
                    logger.warning(
                        "Phase E: no core.schemes row for building %s — skipping Postgres "
                        "user creation for %s", _building_id, user_data.email,
                    )
            else:
                # Platform tenant (for super admin context, no building in scope).
                _postgres_tenant_id = str(uuid_module.uuid5(_ns, "strataos-platform-tenant"))

        except Exception as pg_err:
            logger.warning(f"Phase E: tenant resolution failed for {user_data.email}: {pg_err}")
            _postgres_tenant_id = None

    if POSTGRES_AVAILABLE and _postgres_tenant_id:
        try:

            # Create Postgres user (idempotent: duplicate email returns existing user_id)
            _pg_user_id = await identity_repo.create_user_for_registration(
                email=user_data.email,
                password_hash=user_doc["password_hash"],
                full_name=user_data.full_name,
                role=user_data.role,
                tenant_id=_postgres_tenant_id,
                phone=user_data.phone,
                is_approved=is_approved,
                is_test_data=False,
            )
            logger.info(
                f"Phase E: Created Postgres user {_pg_user_id} for {user_data.email} (tenant_id: {_postgres_tenant_id})")
        except Exception as pg_err:
            # Log but don't fail: Postgres is optional during Phase E transition
            logger.warning(f"Phase E: Postgres user creation failed for {user_data.email}: {str(pg_err)}")
            _postgres_tenant_id = None

    # ── Notification routing ─────────────────────────────────────────────────
    portal_url = _get_portal_url()

    if requires_owner_approval and unit_owners:
        safe_full_name = html_lib.escape(user_data.full_name)
        safe_email = html_lib.escape(user_data.email)
        safe_role = html_lib.escape(user_data.role.capitalize())
        safe_unit = html_lib.escape(str(user_data.unit_number))
        approval_page_url = f"{portal_url}/requests/tenant-approvals"

        owner_notifications = [
            {
                "id": str(uuid.uuid4()),
                "user_id": owner_user["id"],
                "title": f"Action Required: New {safe_role} Registration for Your Unit",
                "message": f"{safe_full_name} has registered as a {safe_role.lower()} for Unit {safe_unit}. Please review and approve or reject this registration.",
                "type": "tenant_approval_required",
                "related_id": user_id,
                "link": "/requests/tenant-approvals",
                "is_read": False,
                "created_at": now
            }
            for owner_user in unit_owners
        ]
        if owner_notifications:
            await db.user_notifications.insert_many(owner_notifications)

        admin_users_fyi = await _get_staff_registration_reviewers(_building_id)
        encoded_name = quote_plus(user_data.full_name)
        users_link_fyi = f"/admin/users?tab=residents&search={encoded_name}"

        # Sentinel 🛡️: Define branding variables for notifications (Fixes F821 undefined name)
        settings_doc = await _get_general_settings_or_default(
            _building_id,
            {"_id": 0},
            fallback_building_id=DEFAULT_BUILDING_ID,
            settings_db=db,
        )
        b_name = settings_doc.get("building_name", "Our Residences")
        b_addr = settings_doc.get("building_address", "")
        safe_b_name = html_lib.escape(b_name)
        safe_b_addr = html_lib.escape(b_addr)

        confirm_html = f"""<!DOCTYPE html>
<html><head>
  <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;background:#f5f5f5}}
    .container{{max-width:600px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.08)}}
    .header{{background:#2F4F4F;color:white;padding:32px 30px;text-align:center}}
    .header h1{{margin:0;font-size:22px;font-weight:700}}
    .content{{padding:32px 30px}}
    .info{{background:#eff6ff;border-left:4px solid #2563eb;padding:14px 18px;margin:20px 0;border-radius:6px;font-size:14px;line-height:1.6}}
    .btn-row{{display:flex;gap:12px;margin:24px 0}}
    .btn{{display:inline-block;padding:13px 28px;border-radius:8px;font-weight:700;font-size:15px;text-decoration:none;text-align:center}}
    .btn-approve{{background:#16a34a;color:#fff}}
    .btn-view{{background:#2563eb;color:#fff}}
    .note{{font-size:13px;color:#64748b;margin-top:20px}}
    .footer{{text-align:center;color:#94a3b8;font-size:12px;padding:20px 30px;background:#f8fafc}}
  </style>
</head>
<body>
  <div class="container">
    <div class="header"><h1>{safe_b_name}</h1></div>
    <div class="content">
      <h2 style="margin-top:0;font-size:20px;">New {safe_role} Registration for Your Unit</h2>
      <p>A new {safe_role.lower()} has registered for <strong>Unit {safe_unit}</strong> and requires your approval before their account is activated.</p>
      <div class="info">
        <p style="margin:0"><strong>Name:</strong> {safe_full_name}</p>
        <p style="margin:4px 0 0"><strong>Email:</strong> {safe_email}</p>
        <p style="margin:4px 0 0"><strong>Role:</strong> {safe_role}</p>
        <p style="margin:4px 0 0"><strong>Unit:</strong> {safe_unit}</p>
      </div>
      <div class="btn-row">
        <a href="{html_lib.escape(approval_page_url)}" class="btn btn-approve">Review &amp; Approve</a>
        <a href="{html_lib.escape(approval_page_url)}" class="btn btn-view">View in Portal</a>
      </div>
      <p class="note">If you did not authorise this person to live in your unit, you can reject this registration from the portal.</p>
    </div>
    <div class="footer">{safe_b_name} · {safe_b_addr}</div>
  </div>
</body></html>"""
        confirm_text = (
            f"Action Required: New {user_data.role.capitalize()} Registration for Unit {user_data.unit_number}\n\n"
            f"Name: {user_data.full_name}\nEmail: {user_data.email}\n\n"
            f"Please visit the portal to approve or reject: {approval_page_url}"
        )
        for owner_user in unit_owners:
            if owner_user.get("email"):
                background_tasks.add_task(
                    send_email_async, owner_user["email"],
                    f"Action Required: New {user_data.role.capitalize()} Registered for Unit {user_data.unit_number}",
                    confirm_html, confirm_text, context=f"new_registration_owner:{user_id}"
                )

        if admin_users_fyi:
            fyi_notifications = [
                {
                    "id": str(uuid.uuid4()),
                    "user_id": admin["id"],
                    "title": f"New {safe_role} Registered — Awaiting Owner Approval",
                    "message": f"{safe_full_name} ({safe_email}) registered for Unit {safe_unit}. Awaiting unit owner approval.",
                    "type": "user_approval",
                    "related_id": user_id,
                    "link": users_link_fyi,
                    "is_read": False,
                    "created_at": now
                }
                for admin in admin_users_fyi
            ]
            await db.user_notifications.insert_many(fyi_notifications)

        fyi_portal_link = f"{portal_url}/admin/users?tab=residents&search={encoded_name}"
        # Get settings for branding
        settings_doc = await _get_general_settings_or_default(
            _building_id,
            {"_id": 0},
            fallback_building_id=DEFAULT_BUILDING_ID,
            settings_db=db,
        )
        b_name = settings_doc.get("building_name", "Our Residences")
        b_addr = settings_doc.get("building_address", "")
        safe_b_name = html_lib.escape(b_name)
        safe_b_addr = html_lib.escape(b_addr)

        fyi_html = f"""<!DOCTYPE html><html><head>
  <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;background:#f5f5f5}}
    .c{{max-width:600px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.08)}}
    .h{{background:#2F4F4F;color:#fff;padding:28px 30px;text-align:center}}.h h1{{margin:0;font-size:20px}}
    .b{{padding:28px 30px}}.info{{background:#fefce8;border-left:4px solid #ca8a04;padding:12px 16px;margin:16px 0;border-radius:6px;font-size:14px;line-height:1.6}}
    .btn{{display:inline-block;padding:11px 24px;border-radius:8px;font-weight:700;font-size:14px;text-decoration:none;background:#2563eb;color:#fff;margin-top:16px}}
    .f{{text-align:center;color:#94a3b8;font-size:12px;padding:16px;background:#f8fafc}}
  </style>
</head><body>
  <div class="c">
    <div class="h"><h1>{safe_b_name}</h1></div>
    <div class="b">
      <h2 style="margin-top:0;font-size:18px">FYI: New {safe_role} Registered — Awaiting Owner Approval</h2>
      <p>A new {safe_role.lower()} has registered for <strong>Unit {safe_unit}</strong>. The unit owner has been notified and must approve before the account is activated.</p>
      <div class="info">
        <p style="margin:0"><strong>Name:</strong> {safe_full_name}</p>
        <p style="margin:4px 0 0"><strong>Email:</strong> {safe_email}</p>
        <p style="margin:4px 0 0"><strong>Role:</strong> {safe_role}</p>
        <p style="margin:4px 0 0"><strong>Unit:</strong> {safe_unit}</p>
      </div>
      <a href="{html_lib.escape(fyi_portal_link)}" class="btn">View Registration in Portal</a>
    </div>
    <div class="f">{safe_b_name} · {safe_b_addr}</div>
  </div>
</body></html>"""
        fyi_subject = f"FYI: New {user_data.role.capitalize()} Registered for Unit {user_data.unit_number}"
        sent_fyi = {o.get("email") for o in unit_owners if o.get("email")}
        for admin in (admin_users_fyi or []):
            addr = admin.get("email")
            if addr and addr not in sent_fyi:
                background_tasks.add_task(send_email_async, addr, fyi_subject, fyi_html,
                                          f"FYI: New {user_data.role.capitalize()} for Unit {user_data.unit_number}. View: {fyi_portal_link}",
                                          context=f"new_registration_admin_fyi:{user_id}")
                sent_fyi.add(addr)
        if NOTIFY_BCC_EMAIL and NOTIFY_BCC_EMAIL not in sent_fyi:
            background_tasks.add_task(send_email_async, NOTIFY_BCC_EMAIL, fyi_subject, fyi_html,
                                      f"FYI: New {user_data.role.capitalize()} for Unit {user_data.unit_number}.",
                                      context=f"new_registration_cc:{user_id}")
    else:
        admin_users = await _get_staff_registration_reviewers(_building_id)
        if admin_users:
            safe_fn = html_lib.escape(user_data.full_name)
            safe_em = html_lib.escape(user_data.email)
            safe_ro = html_lib.escape(user_data.role.capitalize())
            safe_un = html_lib.escape(user_data.unit_number or "Not provided")
            # Branding variables required by admin email template below
            _settings_doc = await _get_general_settings_or_default(
                _building_id,
                {"_id": 0},
                fallback_building_id=DEFAULT_BUILDING_ID,
                settings_db=db,
            )
            safe_b_name = html_lib.escape(_settings_doc.get("building_name") or "Building")
            safe_b_addr = html_lib.escape(_settings_doc.get("building_address", ""))
            _role_tab_map = {"owner": "owners", "tenant": "residents", "guest": "residents",
                             "service_provider": "service"}
            _reg_tab = _role_tab_map.get(user_data.role, "owners")
            _encoded = quote_plus(user_data.full_name)
            portal_link_admin = f"{portal_url}/admin/users?tab={_reg_tab}&search={_encoded}"

            # Three notification variants based on role + approval state.
            _unit_label = user_data.unit_number or "Not provided"
            if is_approved:
                # Owner name matched strata roll — account active immediately.
                _notif_title = f"New Owner Registered — Unit {_unit_label}"
                _notif_msg = (
                    f"{user_data.full_name} ({user_data.email}) registered as owner for "
                    f"Unit {_unit_label}. Name verified against strata roll. Account is active."
                )
                _email_subject = f"New Owner Registration — {user_data.full_name}"
                _h2 = "New Owner Registration (Auto-Approved)"
                _p = (
                    f"A new owner has registered for Unit {safe_un}. "
                    "Their name matched the strata roll and the account is active."
                )
                _btn = "View Owner Profile"
            elif user_data.role == "owner":
                # Owner name did not match roll or no roll data — needs admin verification.
                _notif_title = f"New Owner Registration — Verification Required (Unit {_unit_label})"
                _notif_msg = (
                    f"{user_data.full_name} ({user_data.email}) registered as owner for "
                    f"Unit {_unit_label}. Name could not be verified against the strata roll. "
                    "Please review and approve."
                )
                _email_subject = f"Owner Verification Required — {user_data.full_name}"
                _h2 = "New Owner Registration — Verification Required"
                _p = (
                    f"A new owner has registered for Unit {safe_un}. "
                    "Their name could not be automatically verified against the strata roll. "
                    "Please review their details and approve or reject their account."
                )
                _btn = "Review &amp; Approve"
            else:
                # Tenant or guest — standard admin approval flow.
                _notif_title = "New User Registration — Approval Required"
                _notif_msg = (
                    f"{user_data.full_name} ({user_data.email}) has registered as "
                    f"{user_data.role} and requires approval. Unit: {_unit_label}"
                )
                _email_subject = "New User Registration — Approval Required"
                _h2 = "New User Registration — Approval Required"
                _p = f"A new {safe_ro} has registered on the portal and is awaiting your approval."
                _btn = "Review &amp; Approve"

            notifications_to_create = [
                {
                    "id": str(uuid.uuid4()),
                    "user_id": admin["id"],
                    "title": _notif_title,
                    "message": _notif_msg,
                    "type": "user_approval",
                    "related_id": user_id,
                    "link": f"/admin/users?tab={_reg_tab}&search={_encoded}",
                    "is_read": False,
                    "created_at": now
                }
                for admin in admin_users
            ]
            await db.user_notifications.insert_many(notifications_to_create)

            admin_html = f"""<!DOCTYPE html><html><head>
  <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;background:#f5f5f5}}
    .c{{max-width:600px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.08)}}
    .h{{background:#2F4F4F;color:#fff;padding:28px 30px;text-align:center}}.h h1{{margin:0;font-size:20px}}
    .b{{padding:28px 30px}}.info{{background:#eff6ff;border-left:4px solid #2563eb;padding:12px 16px;margin:16px 0;border-radius:6px;font-size:14px;line-height:1.6}}
    .btn{{display:inline-block;padding:11px 24px;border-radius:8px;font-weight:700;font-size:14px;text-decoration:none;background:#16a34a;color:#fff;margin-top:16px}}
    .f{{text-align:center;color:#94a3b8;font-size:12px;padding:16px;background:#f8fafc}}
  </style>
</head><body>
  <div class="c">
    <div class="h"><h1>{safe_b_name}</h1></div>
    <div class="b">
      <h2 style="margin-top:0;font-size:18px">{_h2}</h2>
      <p>{_p}</p>
      <div class="info">
        <p style="margin:0"><strong>Name:</strong> {safe_fn}</p>
        <p style="margin:4px 0 0"><strong>Email:</strong> {safe_em}</p>
        <p style="margin:4px 0 0"><strong>Role:</strong> {safe_ro}</p>
        <p style="margin:4px 0 0"><strong>Unit:</strong> {safe_un}</p>
      </div>
      <a href="{html_lib.escape(portal_link_admin)}" class="btn">{_btn}</a>
    </div>
    <div class="f">{safe_b_name} · {safe_b_addr}</div>
  </div>
</body></html>"""
            sent_admin = set()
            for admin in admin_users:
                addr = admin.get("email")
                if addr and addr not in sent_admin:
                    background_tasks.add_task(
                        send_email_async, addr,
                        _email_subject,
                        admin_html,
                        f"New {user_data.role} registration from {user_data.full_name}. View at: {portal_link_admin}",
                        context=f"new_registration_admin:{user_id}"
                    )
                    sent_admin.add(addr)
            if NOTIFY_BCC_EMAIL and NOTIFY_BCC_EMAIL not in sent_admin:
                background_tasks.add_task(
                    send_email_async, NOTIFY_BCC_EMAIL,
                    _email_subject,
                    admin_html, f"New {user_data.role} registration from {user_data.full_name}.",
                    context=f"new_registration_cc:{user_id}"
                )

    token = create_token(user_id, user_data.email, user_data.role,
                         building_id=_building_id,
                         tenant_id=_postgres_tenant_id if _postgres_tenant_id else None,
                         end_date=user_data.end_date if user_data.role == "guest" else None)
    asyncio.create_task(log_activity(
        activity_type="resident",
        title=f"New Resident Joined: {user_data.full_name}",
        entity_id=user_id,
        priority=5,
        metadata={"role": user_data.role, "unit": user_data.unit_number,
                  "additional_units": additional_units}
    ))

    # ── Registration confirmation email to the registrant ─────────────────────
    _reg_portal_url = _get_portal_url()
    _reg_b_name = (await _get_general_settings_or_default(
        _building_id, {"_id": 0}, fallback_building_id=DEFAULT_BUILDING_ID, settings_db=db
    )).get("building_name", "Our Residences")
    _safe_reg_name = html_lib.escape(user_data.full_name)
    _safe_reg_role = html_lib.escape(user_data.role.capitalize())
    _safe_reg_unit = html_lib.escape(str(user_data.unit_number or ""))
    _safe_b = html_lib.escape(_reg_b_name)

    if is_approved:
        _conf_subject = f"Welcome to {_reg_b_name} — Account Active"
        _conf_heading = "Your Account is Active"
        _conf_body = (
            f"Hi {_safe_reg_name},<br><br>"
            f"Your <strong>{_safe_reg_role}</strong> account for {_safe_b} (Unit {_safe_reg_unit}) "
            f"is now active. You can sign in to the portal straight away."
        )
        _conf_cta_label = "Sign In to the Portal"
        _conf_cta_url = f"{_reg_portal_url}/dashboard"
    elif initial_status == "pending_owner_approval":
        _conf_subject = f"Registration Received — Awaiting Owner Approval"
        _conf_heading = "Registration Received"
        _conf_body = (
            f"Hi {_safe_reg_name},<br><br>"
            f"We've received your <strong>{_safe_reg_role}</strong> registration for "
            f"{_safe_b}, Unit {_safe_reg_unit}.<br><br>"
            f"The unit owner has been notified and needs to approve your request. "
            f"You'll receive another email once the owner responds."
        )
        _conf_cta_label = "Visit the Portal"
        _conf_cta_url = f"{_reg_portal_url}/register"
    else:
        _conf_subject = f"Registration Received — {_reg_b_name}"
        _conf_heading = "Registration Received"
        _conf_body = (
            f"Hi {_safe_reg_name},<br><br>"
            f"We've received your <strong>{_safe_reg_role}</strong> registration for "
            f"{_safe_b}, Unit {_safe_reg_unit}.<br><br>"
            f"The Strata Manager will review your details and activate your account shortly. "
            f"You'll receive an email once approved."
        )
        _conf_cta_label = "Visit the Portal"
        _conf_cta_url = f"{_reg_portal_url}/register"

    _conf_html = f"""<!DOCTYPE html><html><head>
  <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;background:#f5f5f5}}
    .c{{max-width:600px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.08)}}
    .h{{background:#2F4F4F;color:#fff;padding:28px 30px;text-align:center}}.h h1{{margin:0;font-size:20px}}
    .b{{padding:28px 30px;font-size:14px;color:#475569;line-height:1.7}}
    .btn{{display:inline-block;padding:12px 26px;border-radius:8px;font-weight:700;font-size:14px;text-decoration:none;background:#2F4F4F;color:#fff;margin-top:18px}}
    .f{{text-align:center;color:#94a3b8;font-size:12px;padding:16px;background:#f8fafc}}
  </style>
</head><body>
  <div class="c">
    <div class="h"><h1>{_safe_b}</h1></div>
    <div class="b">
      <h2 style="margin-top:0;font-size:18px;color:#1e293b">{_conf_heading}</h2>
      <p>{_conf_body}</p>
      <a href="{html_lib.escape(_conf_cta_url)}" class="btn">{_conf_cta_label}</a>
    </div>
    <div class="f">{_safe_b}</div>
  </div>
</body></html>"""
    _conf_text = (
            f"{_conf_heading}\n\n"
            + _conf_body.replace("<br><br>", "\n\n").replace("<br>", "\n")
            .replace(f"<strong>{_safe_reg_role}</strong>", user_data.role.capitalize())
            .replace(f"<strong>{_safe_reg_name}</strong>", user_data.full_name)
            + f"\n\n{_conf_cta_url}"
    )
    background_tasks.add_task(
        send_email_async, user_data.email, _conf_subject, _conf_html, _conf_text,
        context=f"registration_confirmation:{user_id}"
    )

    if invite_doc:
        await db.resident_registration_invites.update_one(
            {"id": invite_doc["id"], "status": "pending"},
            {"$set": {
                "status": "used",
                "used_at": datetime.now(timezone.utc).isoformat(),
                "used_by_user_id": user_id,
                "submitted_email": user_data.email,
                "submitted_full_name": user_data.full_name,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
        )

    # Only surface units that are actually active (mirrors is_active=is_approved on insert)
    user_doc["owned_units"] = all_units if is_approved else []
    return AuthResponse(token=token, user=user_to_response(user_doc))


# ── Login ─────────────────────────────────────────────────────────────────────


@router.post("/auth/login", response_model=AuthResponse)
@rate_limit("rate_limit_login", 10)
async def login(request: Request, credentials: UserLogin):
    """Authenticate and return a JWT token. Rate limited to 10 requests/minute per IP.

    Postgres-first: looks up the user in core.users via the SECURITY DEFINER
    bootstrap function.  Falls back to the legacy MongoDB path for accounts
    that exist only in Mongo (pre-cutover).  New accounts (seeded or registered
    after Phase C) are always in Postgres.
    """
    from db_postgres.repos import identity_repo as _id_repo

    _email_q = credentials.email
    _pg_user = await _id_repo.find_user_for_auth(_email_q)
    _using_pg = _pg_user is not None

    # Sentinel 🛡️: Constant-time login to prevent user enumeration via timing attacks.
    if _using_pg:
        stored_hash = _pg_user.get("password_hash") or DUMMY_HASH
    else:
        # Legacy MongoDB fallback
        _mongo_user = await db.users.find_one(
            {"$or": [{"email": _email_q}, {"portal_email": _email_q}]}, {"_id": 0}
        )
        stored_hash = (_mongo_user or {}).get("password_hash") or \
                      (_mongo_user or {}).get("hashed_password") or DUMMY_HASH

    is_valid = verify_password(credentials.password, stored_hash)
    user = _pg_user if _using_pg else (_mongo_user if not _using_pg else None)

    if not user or not is_valid:
        await _log_login_attempt(user, credentials.email, request, "failed",
                                 failure_reason="invalid_credentials")
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Defence in depth: an ``is_test_data`` account must never authenticate on a
    # real deployment. The flag was designed as a cleanup marker, not an auth
    # gate, so nothing here checked it — and when the pytest sweep turned out to
    # skip core.users entirely, the result was 1,772 active super_admin accounts
    # in production sharing the constant password committed in
    # tests/backend/test_invitation_rls_bypass.py. Cleaning up the rows fixes
    # today; this makes a future leak inert rather than exploitable.
    #
    # Gated on APP_ENV so the test suite, which logs in AS these accounts on
    # purpose, keeps working. Checked after the password verify so it cannot be
    # used to probe which addresses exist.
    if os.getenv("APP_ENV", "").lower() == "production":
        _is_test_account = bool(user.get("is_test_data"))
        if not _is_test_account and _using_pg:
            # The Postgres auth function does not return is_test_data; ask separately.
            try:
                _is_test_account = await _id_repo.is_test_data_account(credentials.email)
            except Exception:  # never let this check lock out real users
                logger.exception("is_test_data guard failed; allowing login to proceed")
                _is_test_account = False
        if _is_test_account:
            await _log_login_attempt(user, credentials.email, request, "failed",
                                     failure_reason="test_data_account_in_production")
            raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.get("is_active", True):
        await _log_login_attempt(user, credentials.email, request, "failed",
                                 failure_reason="account_deactivated")
        raise HTTPException(status_code=401, detail="Account is deactivated")

    if user.get("status") == "archived":
        await _log_login_attempt(user, credentials.email, request, "failed",
                                 failure_reason="account_archived")
        raise HTTPException(status_code=401,
                            detail="This account has been archived. Please contact the strata manager.")

    # ── Activation gate (GAP-COMMS-003 Phase 3) ──────────────────────────────
    # An account that exists but has never been claimed by its owner must not open,
    # even with a correct password.
    #
    # Restoring East Gate's owners made this concrete. MongoDB held 106 of them as
    # is_active=False with no password_hash — unusable, which was the intent. Postgres
    # held the SAME accounts as is_active=TRUE, is_approved=TRUE, carrying their
    # pre-purge password hashes. Login resolves Postgres first, so every check below
    # this point passed and an owner's old password opened an unclaimed account.
    #
    # Checked AFTER the password verify, deliberately: answering before it would let
    # anyone probe which addresses are awaiting activation.
    try:
        _activation = await _id_repo.activation_state(credentials.email)
    except Exception:
        # Never let this lock out real users if the lookup itself fails.
        logger.exception("activation gate lookup failed; allowing login to proceed")
        _activation = None

    if _activation and _activation.get("requires_activation"):
        await _log_login_attempt(user, credentials.email, request, "failed",
                                 failure_reason="activation_required")
        raise HTTPException(
            status_code=403,
            detail={
                "code": "activation_required",
                "message": (
                    "Your account has been set up, but it has not been activated yet. "
                    "Please use the activation link that was emailed to you to set your "
                    "own password. If you no longer have it, request a new one below."
                ),
                # The frontend routes on this rather than parsing the message.
                "next_step": "reset_password",
            },
        )

    _role = normalize_user_role(user.get("role", ""))
    if _role in {UserRole.OWNER, UserRole.TENANT} and not user.get("is_approved"):
        await _log_login_attempt(user, credentials.email, request, "failed",
                                 failure_reason="pending_approval")
        raise HTTPException(
            status_code=403,
            detail={
                "code": "pending_approval",
                "message": (
                    "Your account is pending approval by the Strata Manager. "
                    "You will receive an email once your registration has been reviewed. "
                    "If you have not heard back, please contact building management."
                ),
            }
        )

    audit_doc = await _log_login_attempt(user, credentials.email, request, "success")

    now_ts = datetime.now(timezone.utc).isoformat()
    login_ip = (audit_doc or {}).get("ip_address") or None
    # Carried through from the audit row rather than re-resolved, so the user
    # record and the security log can never disagree about the same login.
    login_public_ip = (audit_doc or {}).get("public_ip") or None
    login_local_ip = (audit_doc or {}).get("local_ip") or None

    if _using_pg:
        # Postgres: update last_login_at / ip; fire-and-forget risk scoring
        await _id_repo.update_last_login(
            user["id"], user["tenant_id"], login_ip,
            public_ip=login_public_ip, local_ip=login_local_ip,
        )
        risk_task = _calculate_risk_score(user["id"], audit_doc) if audit_doc else None
        if risk_task:
            risk_score, risk_flags = await risk_task
            if risk_score >= 50:
                audit_doc["risk_score"] = risk_score
                audit_doc["risk_flags"] = risk_flags
                asyncio.create_task(_send_suspicious_login_email(user, audit_doc))
    else:
        login_update: dict = {"last_login_at": now_ts}
        if login_ip:
            login_update["last_login_ip"] = login_ip
        # Written unconditionally, including None: a login that established no
        # public address must CLEAR any stale one from a previous session rather
        # than leaving the dashboard showing an address this login did not use.
        login_update["last_login_public_ip"] = login_public_ip
        login_update["last_login_local_ip"] = login_local_ip
        update_task = db.users.update_one({"id": user["id"]}, {"$set": login_update})
        risk_task = _calculate_risk_score(user["id"], audit_doc) if audit_doc else None
        if risk_task:
            _, (risk_score, risk_flags) = await asyncio.gather(update_task, risk_task)
            if risk_score >= 50:
                audit_doc["risk_score"] = risk_score
                audit_doc["risk_flags"] = risk_flags
                asyncio.create_task(_send_suspicious_login_email(user, audit_doc))
        else:
            await update_task

    user = {
        **user,
        "last_login_at": now_ts,
        **({"last_login_ip": login_ip} if login_ip else {}),
        "last_login_public_ip": login_public_ip,
        "last_login_local_ip": login_local_ip,
    }

    # Resolve building_id for the JWT
    scoped_building_id = None
    if _using_pg:
        # Postgres: use default_scheme_id or single-scheme auto-resolve
        scoped_building_id = user.get("building_id")
        if not scoped_building_id:
            scheme_ids = await _id_repo.get_user_scheme_ids(user["id"], user["tenant_id"])
            if len(scheme_ids) == 1:
                scoped_building_id = scheme_ids[0]
    else:
        # Legacy MongoDB
        user_building_id = user.get("building_id")
        if user_building_id:
            scoped_building_id = user_building_id
        else:
            memberships = await db.memberships.find(
                {"user_id": user["id"], "is_active": True}, {"building_id": 1}
            ).to_list(10)
            if len(memberships) == 1:
                scoped_building_id = memberships[0]["building_id"]
        # Legacy tokens get no DEFAULT_BUILDING_ID — callers must supply building context
        # (removed to prevent silent wrong-building routing)

    # For guests: look up end_date for JWT hard-cap
    guest_end_date = None
    if user.get("role") == "guest":
        if not _using_pg:
            unit_rel = await db.user_units.find_one(
                {"user_id": user["id"], "role_at_unit": "guest", "is_active": True},
                {"_id": 0, "end_date": 1},
            )
            if unit_rel:
                guest_end_date = unit_rel.get("end_date")

    token = create_token(
        user_id=user["id"],
        email=user["email"],
        role=user["role"],
        building_id=scoped_building_id,
        end_date=guest_end_date,
        organisation_id=user.get("organisation_id", "org-silverfox-001"),
        tenant_id=user.get("tenant_id") if _using_pg else None,
    )

    # ── TOTP check ───────────────────────────────────────────────────────────
    # If TOTP is enabled on this account, issue a short-lived "pending" token
    # instead of a full session token.  The client must call
    # POST /auth/totp/challenge with this pending token + a valid TOTP code
    # to receive the full JWT.
    if user.get("totp_enabled") and user.get("totp_secret_encrypted"):
        pending_payload = {
            "sub": user["id"],
            "type": "totp_pending",
            "email": user["email"],
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        }
        pending_token = pyjwt.encode(pending_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        return JSONResponse(
            status_code=200,
            content={"status": "totp_required", "totp_token": pending_token},
        )

    return AuthResponse(token=token, user=user_to_response(user))


@router.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user),
                 building_id: str | None = Depends(get_optional_building)):
    """Return the currently authenticated user's profile.

    Building context is **optional** — this is the "who am I" endpoint and
    must work for any authenticated user, including super admins on a fresh
    install (no buildings exist yet) or before they pick one in the
    building switcher. Building-scoped enrichment (co-owner name, owned
    units) is skipped when no building is in scope.

    Owner-read path selection:
      - owner_read_pg_enabled + identity_core PG-read state → Postgres ownership_periods
      - otherwise → MongoDB units (legacy)
    """
    # Resolve owner_read_pg_enabled toggle (only when building context is present)
    _use_pg_owner_read = False
    if building_id and POSTGRES_AVAILABLE:
        try:
            from services.cutover_config_service import (
                is_cutover_feature_enabled,
                OWNER_READ_PG_ENABLED,
            )
            from services.domain_source_guard import require_domain_source

            if await is_cutover_feature_enabled(building_id, OWNER_READ_PG_ENABLED):
                decision = await require_domain_source(
                    domain="identity_core",
                    building_id=building_id,
                    operation="read",
                    requested_source="postgres",
                )
                _use_pg_owner_read = decision.postgres_allowed
        except Exception:
            _use_pg_owner_read = False

    unit_number = current_user.get("unit_number")
    if unit_number and building_id:
        if _use_pg_owner_read:
            # --- Postgres path: read co-owner from ownership_periods ---
            try:
                from db_postgres.repos import identity_repo as _id_repo
                from db_postgres.repos import ownership_repo as _own_repo
                from db_postgres.session import async_session_context

                # Resolve scheme_id for this building
                scheme = await _id_repo.get_scheme_by_number(building_id)
                if scheme:
                    from sqlalchemy import text
                    from db_postgres.session import set_tenant
                    async with async_session_context() as _pg_session:
                        # Set RLS tenant context before any query on tenant-scoped tables.
                        await set_tenant(_pg_session, str(scheme["tenant_id"]))
                        # Find the lot for this unit
                        lot_id = await _own_repo.get_lot_id_by_number(
                            _pg_session, str(scheme["scheme_id"]), unit_number
                        )
                        if lot_id:
                            # Fetch all current owners for this lot
                            result = await _pg_session.execute(
                                text("""
                                    SELECT p.legal_name AS full_name,
                                           p.primary_email AS email,
                                           p.secondary_email,
                                           op.is_primary_owner
                                    FROM core.ownership_periods op
                                    JOIN core.parties p ON p.party_id = op.owner_party_id
                                    WHERE op.lot_id = :lid
                                      AND op.valid_to IS NULL
                                      AND op.recorded_to IS NULL
                                    ORDER BY op.is_primary_owner DESC
                                """),
                                {"lid": lot_id},
                            )
                            owners = [dict(r._mapping) for r in result.fetchall()]
                            if owners:
                                user_email = current_user.get("email", "").lower()
                                self_owner = next(
                                    (
                                        owner for owner in owners
                                        if user_email in {
                                            (owner.get("email") or "").lower(),
                                            (owner.get("secondary_email") or "").lower(),
                                        }
                                    ),
                                    None,
                                )
                                if self_owner:
                                    current_user["primary_email"] = self_owner.get("email") or None
                                    current_user["secondary_email"] = self_owner.get("secondary_email") or None
                                    co_owner = next((owner for owner in owners if owner is not self_owner), None)
                                    if co_owner:
                                        current_user["co_owner_name"] = co_owner.get("full_name")
                                        current_user["co_owner_email"] = co_owner.get("email") or None
            except Exception as _pg_err:
                logger.warning("auth/me PG owner enrichment failed, no co-owner data: %s", _pg_err)
        else:
            # --- MongoDB legacy path ---
            try:
                unit = await db.units.find_one(
                    # building_id scoping is mandatory — unit numbers are not globally unique
                    # across buildings and the TenantScopedDatabase wrapper does not scope
                    # db.units automatically for this inline call.
                    {"unit_number": unit_number, "building_id": building_id},
                    {"_id": 0, "owner_name": 1, "owner_email": 1, "owner_name_b": 1, "owner_email_b": 1},
                )
                if unit and unit.get("owner_name_b"):
                    user_email = current_user.get("email", "").lower()
                    primary_email = unit.get("owner_email", "").lower()
                    if user_email == primary_email:
                        current_user["co_owner_name"] = unit["owner_name_b"]
                        current_user["co_owner_email"] = unit.get("owner_email_b") or None
                    else:
                        current_user["co_owner_name"] = unit.get("owner_name")
                        current_user["co_owner_email"] = unit.get("owner_email") or None
            except Exception:
                pass

    # Enrich with all units this user owns in this building (skip if no building)
    if building_id:
        if _use_pg_owner_read and POSTGRES_AVAILABLE:
            # Postgres path: read owned units from user_role_assignments + core.lots
            try:
                from db_postgres.repos import identity_repo as _id_repo
                user_uuid = current_user.get("user_uuid") or current_user.get("id")
                tenant_id = current_user.get("tenant_id")
                if user_uuid and tenant_id:
                    # Resolve scheme for the building
                    scheme = await _id_repo.get_scheme_by_number(building_id)
                    if scheme:
                        from sqlalchemy import text
                        from db_postgres.session import async_session_context
                        async with async_session_context() as _pg_session:
                            from db_postgres.session import set_tenant
                            await set_tenant(_pg_session, str(scheme["tenant_id"]))
                            # Use the email directly as a parameter to avoid an
                            # implicit cross-join with core.users.  A JOIN on
                            # user_id with no relation to op/p produces every
                            # lot in the scheme × 1 user row, filtered on email
                            # equality — functionally correct but wasteful and
                            # potentially duplicates rows if the user table ever
                            # returns multiple rows for the same uid under RLS.
                            result = await _pg_session.execute(
                                text("""
                                    SELECT COALESCE(l.unit_number, l.lot_number) AS unit_number
                                    FROM core.ownership_periods op
                                    JOIN core.lots l ON l.lot_id = op.lot_id
                                    JOIN core.parties p ON p.party_id = op.owner_party_id
                                    WHERE l.scheme_id = CAST(:sid AS UUID)
                                      AND op.valid_to IS NULL
                                      AND op.recorded_to IS NULL
                                      AND (
                                          LOWER(p.primary_email) = LOWER(:user_email)
                                          OR LOWER(COALESCE(p.secondary_email, '')) = LOWER(:user_email)
                                      )
                                    ORDER BY l.lot_number
                                """),
                                {
                                    "sid": str(scheme["scheme_id"]),
                                    "user_email": current_user.get("email", ""),
                                },
                            )
                            pg_units = [r[0] for r in result.fetchall() if r[0]]
                            if pg_units:
                                current_user["owned_units"] = pg_units
                            else:
                                await _enrich_owned_units(current_user, building_id)
                    else:
                        await _enrich_owned_units(current_user, building_id)
                else:
                    await _enrich_owned_units(current_user, building_id)
            except Exception as _pg_err:
                logger.warning("auth/me PG owned_units enrichment failed, falling back to Mongo: %s", _pg_err)
                await _enrich_owned_units(current_user, building_id)
        else:
            await _enrich_owned_units(current_user, building_id)

    return user_to_response(current_user)


@router.post("/auth/email-preference", response_model=UserResponse)
async def update_email_preference(
        data: EmailPreferenceRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Promote one of the authenticated owner's contact emails to primary."""
    if not POSTGRES_AVAILABLE:
        raise HTTPException(status_code=503, detail="Postgres identity store is not available")

    scheme = await identity_repo.get_scheme_by_number(building_id)
    if not scheme:
        raise HTTPException(status_code=404, detail="Building identity record not found")

    requested_email = str(data.primary_email).strip().lower()
    user_email = (current_user.get("email") or "").strip().lower()
    user_uuid = current_user.get("user_uuid") or current_user.get("id")

    from sqlalchemy import text
    from db_postgres.session import set_tenant

    async with async_session_context() as pg_session:
        await set_tenant(pg_session, str(scheme["tenant_id"]))
        result = await pg_session.execute(
            text(
                """
                SELECT p.party_id::text AS party_id,
                       p.primary_email,
                       p.secondary_email
                FROM core.parties p
                LEFT JOIN core.users u
                  ON u.party_id = p.party_id
                 AND u.tenant_id = p.tenant_id
                 AND u.is_active = TRUE
                WHERE p.tenant_id = CAST(:tenant_id AS UUID)
                  AND p.party_type = 'person'
                  AND (
                      u.user_id::text = :user_uuid
                      OR LOWER(COALESCE(u.email, '')) = LOWER(:user_email)
                      OR LOWER(COALESCE(p.primary_email, '')) = LOWER(:user_email)
                      OR LOWER(COALESCE(p.secondary_email, '')) = LOWER(:user_email)
                  )
                ORDER BY
                  CASE WHEN u.user_id::text = :user_uuid THEN 0 ELSE 1 END,
                  CASE WHEN LOWER(COALESCE(p.primary_email, '')) = LOWER(:requested_email) THEN 0 ELSE 1 END
                LIMIT 1
                """
            ),
            {
                "tenant_id": str(scheme["tenant_id"]),
                "user_uuid": str(user_uuid or ""),
                "user_email": user_email,
                "requested_email": requested_email,
            },
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Owner contact record not found")

        current_primary = (row.primary_email or "").strip().lower()
        current_secondary = (row.secondary_email or "").strip().lower()
        allowed = {email for email in (current_primary, current_secondary) if email}
        if requested_email not in allowed:
            raise HTTPException(status_code=400, detail="Select one of your saved contact emails")

        if requested_email == current_secondary:
            await pg_session.execute(
                text(
                    """
                    UPDATE core.parties
                    SET primary_email = :new_primary,
                        secondary_email = :new_secondary,
                        updated_at = now()
                    WHERE party_id = CAST(:party_id AS UUID)
                      AND tenant_id = CAST(:tenant_id AS UUID)
                    """
                ),
                {
                    "new_primary": current_secondary,
                    "new_secondary": current_primary or None,
                    "party_id": str(row.party_id),
                    "tenant_id": str(scheme["tenant_id"]),
                },
            )
            current_user["primary_email"] = current_secondary
            current_user["secondary_email"] = current_primary or None
        else:
            current_user["primary_email"] = current_primary
            current_user["secondary_email"] = current_secondary or None

    return user_to_response(current_user)


@router.get("/auth/memberships")
async def get_memberships(current_user: dict = Depends(get_current_user)):
    """Retrieve all building memberships for the authenticated user."""
    if effective_role(current_user) == UserRole.SUPER_ADMIN:
        # Super admin sees all non-archived active buildings.
        # CLAUDE.md: all active-building queries MUST filter is_archived.
        buildings = await db.buildings.find(
            {"is_active": True, "is_archived": {"$ne": True}}, {"_id": 0}
        ).to_list(1000)
        # Transform buildings into membership-like objects
        return [
            {
                "id": f"sa_{b['id']}",
                "user_id": current_user["id"],
                "building_id": b["id"],
                "building_slug": b.get("slug", ""),
                "building_name": b["name"],
                "roles": [UserRole.SUPER_ADMIN],
                "units": [],
                "is_primary": False
            }
            for b in buildings
        ]

    memberships = await db.memberships.find({"user_id": current_user["id"]}, {"_id": 0}).to_list(100)
    return memberships


@router.post("/auth/select-building", response_model=AuthResponse)
async def select_building(
        data: BuildingSelectionRequest,
        current_user: dict = Depends(get_current_user)
):
    """
    Switch building context and receive a new scoped JWT.
    Validates that the user has a valid membership for the building.
    """
    building = await db.buildings.find_one({"id": data.building_id})
    if not building:
        raise HTTPException(status_code=404, detail="Building not found")

    # Check membership; building_role may be overridden by the membership record
    building_role = current_user["role"]
    if effective_role(current_user) != UserRole.SUPER_ADMIN:
        membership = await db.memberships.find_one({
            "user_id": current_user["id"],
            "building_id": data.building_id
        })
        if not membership:
            raise HTTPException(
                status_code=403,
                detail="You do not have access to this building."
            )
        # Use building-specific role if defined, otherwise fall back to global role
        if membership.get("roles"):
            building_role = membership["roles"][0]

    # Issue scoped token; for guests honour their stay end_date as JWT expiry cap
    guest_end_date = None
    if building_role == "guest":
        unit_rel = await db.user_units.find_one(
            {"user_id": current_user["id"], "role_at_unit": "guest", "is_active": True},
            {"_id": 0, "end_date": 1},
        )
        if unit_rel:
            guest_end_date = unit_rel.get("end_date")

    # Issue scoped token
    token = create_token(
        user_id=current_user["id"],
        email=current_user["email"],
        role=building_role,
        building_id=data.building_id,
        end_date=guest_end_date,
        tenant_id=current_user.get("tenant_id"),
    )

    if effective_role(current_user) == UserRole.SUPER_ADMIN:
        asyncio.create_task(create_audit_log(
            action="super_admin_building_access",
            resource_type="building",
            resource_id=data.building_id,
            user_id=current_user["id"],
            user_name=current_user["full_name"],
            details={"building_name": building.get("name"), "source": "select_building"},
            building_id=data.building_id,
        ))
    elif effective_role(current_user) == UserRole.ADMIN_STAFF:
        asyncio.create_task(create_audit_log(
            action="admin_staff_building_access",
            resource_type="building",
            resource_id=data.building_id,
            user_id=current_user["id"],
            user_name=current_user["full_name"],
            details={"building_name": building.get("name"), "source": "select_building"},
            building_id=data.building_id,
        ))

    return AuthResponse(token=token, user=user_to_response(current_user))


# ── Impersonation ─────────────────────────────────────────────────────────────


@router.post("/auth/impersonate", response_model=AuthResponse)
@rate_limit("rate_limit_impersonate", 10)
async def impersonate_user(request: Request, data: ImpersonateRequest, current_user: dict = Depends(get_current_user)):
    """Allow Super Admin to impersonate another user in a specific building. Does NOT update last_login_at."""
    if effective_role(current_user) != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only Super Admins can impersonate users")

    # Check impersonation toggle — prefer PG config_repo; fall back to Mongo if
    # Postgres is unavailable (config_repo is None when the Phase E import failed).
    if config_repo is not None:
        feature_enabled = await config_repo.get_global_feature_toggle_state(
            "impersonation",
            default=True,
        )
    else:
        _mongo_toggle = await db.feature_toggles.find_one({"feature_key": "impersonation"})
        feature_enabled = (_mongo_toggle or {}).get("is_enabled", True)
    if not feature_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User impersonation is currently disabled")

    target_user = await db.users.find_one({"id": data.user_id}, {"_id": 0})
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found")

    if target_user["role"] == UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Super Admins cannot impersonate other Super Admins")

    # Verify building membership for the target user
    membership = await db.memberships.find_one({"building_id": data.building_id, "user_id": data.user_id})
    if not membership:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="User is not a member of the selected building")

    if not target_user.get("is_active", True) or target_user.get("status") == "archived":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Cannot impersonate inactive or archived users")

    # Use effective_role so temporarily elevated users are impersonated at their
    # elevated role level, not their base role.
    _target_role = target_user.get("effective_role") or target_user.get("role", "owner")
    # Token MUST include building_id to maintain isolation during impersonation
    token = create_token(
        target_user["id"],
        target_user["email"],
        _target_role,
        building_id=data.building_id,
        impersonator_id=current_user["id"],
        tenant_id=target_user.get("tenant_id"),
    )

    # Fire-and-forget audit log — do not block the impersonation response on a
    # logging failure (mirrors the pattern used in select_building above).
    asyncio.create_task(create_audit_log(
        action="impersonate_start",
        resource_type="user",
        resource_id=target_user["id"],
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"target_email": target_user["email"], "building_id": data.building_id},
    ))

    # SECURITY: Inject impersonator_id so user_to_response performs PII masking
    target_user["impersonator_id"] = current_user["id"]

    return AuthResponse(token=token, user=user_to_response(target_user))


# ── Password management ────────────────────────────────────────────────────────


@router.post("/auth/forgot-password")
@rate_limit("rate_limit_forgot_password", 5)
async def request_password_reset(request: Request, data: PasswordResetRequest, background_tasks: BackgroundTasks):
    """Request a password reset link. Always returns success to prevent email enumeration."""
    _reset_email = data.email
    user = await db.users.find_one(
        {"$or": [{"email": _reset_email}, {"portal_email": _reset_email}]}, {"_id": 0}
    )
    if not user:
        return {"message": "If the email exists, a reset link will be sent"}

    reset_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    await db.password_resets.insert_one({
        "token": reset_token,
        "user_id": user["id"],
        "email": data.email,
        "expires_at": expires_at.isoformat(),
        "used": False,
    })

    reset_link = f"{_get_portal_url()}/reset-password?token={reset_token}"

    html_body, text_body = get_email_template("password_reset", reset_link=reset_link)
    background_tasks.add_task(
        send_email_async, data.email, "Reset Your Password", html_body, text_body
    )
    return {"message": "If the email exists, a reset link will be sent"}


@router.post("/auth/reset-password")
@rate_limit("rate_limit_reset_password", 5)
async def reset_password(request: Request, data: PasswordResetConfirm):
    """Consume a password reset token and update the password."""
    reset_record = await db.password_resets.find_one({"token": data.token, "used": False}, {"_id": 0})
    if not reset_record:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    expires_at = datetime.fromisoformat(reset_record["expires_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=400, detail="Reset token has expired")

    user = await db.users.find_one({"id": reset_record["user_id"]}, {"_id": 0, "hashed_password": 0})

    new_hash = hash_password(data.new_password)
    await db.users.update_one(
        {"id": reset_record["user_id"]},
        {
            "$set": {
                "password_hash": new_hash,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            "$unset": {"hashed_password": ""},
        },
    )
    await db.password_resets.update_one({"token": data.token}, {"$set": {"used": True}})

    # Propagate to Postgres and clear the activation gate. Login resolves core.users
    # FIRST, so writing the hash to MongoDB alone leaves the old password working and
    # the new one rejected — see identity_repo.set_password_hash. Non-fatal: a
    # Mongo-only account legitimately has no core.users row.
    _reset_email = (reset_record.get("email") or (user or {}).get("email") or "").strip()
    if _reset_email:
        try:
            from db_postgres.repos import identity_repo as _pw_repo

            await _pw_repo.set_password_hash(_reset_email, new_hash)
            # Setting your own password IS the activation step; nothing else clears it.
            await _pw_repo.mark_activated(_reset_email)
        except Exception:
            logger.exception("Postgres password/activation sync failed for %s", _reset_email)

    metadata = request_metadata(request)
    await _log_password_change(
        user_id=reset_record["user_id"],
        email=reset_record.get("email", ""),
        full_name=user.get("full_name", "") if user else "",
        method="password_reset",
        changed_by="reset_token",
        ip_address=metadata.ip_address,
        user_agent=metadata.user_agent,
    )
    return {"message": "Password reset successfully"}


@router.post("/auth/change-password")
@rate_limit("rate_limit_change_password", 10)
async def change_password(
        request: Request,
        data: ChangePasswordRequest,
        current_user: dict = Depends(get_current_user),
):
    """Authenticated self-service password change."""
    user = await db.users.find_one({"id": current_user["id"]}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    stored_hash = user.get("password_hash") or user.get("hashed_password")
    metadata = request_metadata(request)
    ip = metadata.ip_address
    ua = metadata.user_agent

    if not stored_hash or not verify_password(data.current_password, stored_hash):
        await _log_password_change(
            user_id=user["id"], email=user.get("email", ""), full_name=user.get("full_name", ""),
            method="change_password", changed_by="self", ip_address=ip, user_agent=ua,
            success=False, failure_reason="current_password_incorrect",
        )
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    new_hash = hash_password(data.new_password)
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "password_hash": new_hash,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            "$unset": {"hashed_password": ""},
        },
    )

    # Same Postgres-first problem as the reset path: without this the user's new
    # password is rejected at login while their old one still works.
    if user.get("email"):
        try:
            from db_postgres.repos import identity_repo as _pw_repo

            await _pw_repo.set_password_hash(user["email"], new_hash)
        except Exception:
            logger.exception("Postgres password sync failed for %s", user.get("email"))

    await _log_password_change(
        user_id=user["id"], email=user.get("email", ""), full_name=user.get("full_name", ""),
        method="change_password", changed_by="self", ip_address=ip, user_agent=ua,
    )
    return {"message": "Password changed successfully"}


# ── Registration decision (token-based, no login required) ────────────────────


@router.get("/auth/registration-decision", response_class=HTMLResponse)
@rate_limit("rate_limit_registration_decision", 10)
async def show_registration_decision_form(request: Request, token: str, action: str):
    """Render the approve/reject confirmation form for owner email links."""
    _portal_url = _get_portal_url()

    settings_doc = await _get_general_settings_or_default(DEFAULT_BUILDING_ID, {"_id": 0}, settings_db=db)
    b_name = settings_doc.get("building_name", "Our Residences")
    b_addr = settings_doc.get("building_address", "")

    if action not in ("approve", "reject"):
        return _reg_decision_html_result("Invalid Request", "Invalid Action",
                                         "<p>The link you followed is not valid. Please contact the Strata Manager.</p>",
                                         "#dc2626", _portal_url, b_name, b_addr)

    token_doc, target, err = await _validate_reg_decision_token(token, action, _portal_url, b_name, b_addr)
    if err:
        return err

    settings_doc = await _get_general_settings_or_default(
        target.get("building_id"),
        {"_id": 0},
        fallback_building_id=DEFAULT_BUILDING_ID,
        settings_db=db,
    )
    b_name = settings_doc.get("building_name", b_name)
    b_addr = settings_doc.get("building_address", b_addr)

    safe_name = html_lib.escape(target.get("full_name", ""))
    safe_unit = html_lib.escape(str(target.get("unit_number") or ""))
    safe_role = html_lib.escape((target.get("role") or "").capitalize())
    safe_email = html_lib.escape(target.get("email", ""))
    safe_token = html_lib.escape(token)
    is_guest = target.get("role") == "guest"
    post_url = html_lib.escape(f"{_portal_url}/api/auth/registration-decision")

    if action == "approve":
        instructions_section = ""
        if is_guest:
            instructions_section = """
      <div style="margin:18px 0 0;text-align:left">
        <label style="display:block;font-size:13px;font-weight:600;color:#374151;margin-bottom:6px">
          Instructions for your guest <span style="font-weight:400;color:#94a3b8">(optional)</span>
        </label>
        <textarea name="instructions" rows="4"
          placeholder="e.g. Park in bay 12, bin collection is Tuesday, no smoking anywhere on premises..."
          style="width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;
                 color:#374151;resize:vertical;font-family:inherit;box-sizing:border-box"></textarea>
        <p style="font-size:12px;color:#94a3b8;margin:6px 0 0">These instructions will be emailed to your guest upon approval.</p>
      </div>"""
        guide_link = html_lib.escape(f"{_portal_url}/user-guides/quick_role_guest.html")
        return HTMLResponse(content=f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Approve Registration</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;background:#f1f5f9;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}}
  .card{{max-width:520px;width:100%;background:#fff;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,.12);overflow:hidden}}
  .top{{background:#16a34a;color:#fff;padding:28px 24px;text-align:center}}
  .top h1{{margin:0;font-size:20px;font-weight:700}}
  .body{{padding:24px 28px}}
  .info{{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:14px 16px;margin-bottom:16px;font-size:14px;color:#374151;line-height:1.7}}
  .info p{{margin:0}}
  .btn-approve{{width:100%;padding:13px;background:#16a34a;color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:700;cursor:pointer;margin-top:16px}}
  .btn-approve:hover{{background:#15803d}}
  .sig{{font-size:11px;color:#94a3b8;margin-top:20px;padding-top:14px;border-top:1px solid #f1f5f9;line-height:1.6;text-align:center}}
</style>
</head><body>
  <div class="card">
    <div class="top"><h1>Approve Registration</h1></div>
    <div class="body">
      <div class="info">
        <p><strong>Name:</strong> {safe_name}</p>
        <p><strong>Email:</strong> {safe_email}</p>
        <p><strong>Role:</strong> {safe_role}</p>
        <p><strong>Unit:</strong> {safe_unit}</p>
      </div>
      <p style="font-size:14px;color:#475569">By approving, you confirm this person has your permission to access the building portal as a {safe_role} for Unit {safe_unit}.</p>
      {instructions_section}
      <form method="POST" action="{post_url}">
        <input type="hidden" name="token" value="{safe_token}"/>
        <input type="hidden" name="action" value="approve"/>
        <button type="submit" class="btn-approve">&#10003; &nbsp;Confirm Approval</button>
      </form>
      {'<p style="font-size:12px;color:#64748b;margin-top:12px;text-align:center">Guest Quick Guide: <a href="' + guide_link + '" style="color:#16a34a">east gate guest information</a></p>' if is_guest else ''}
      <div class="sig">{html_lib.escape(b_name)} | Executive Committee | Strata Manager<br>
      A: {html_lib.escape(b_addr)}</div>
    </div>
  </div>
</body></html>""")

    else:  # reject
        return HTMLResponse(content=f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Decline Registration</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;background:#f1f5f9;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}}
  .card{{max-width:520px;width:100%;background:#fff;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,.12);overflow:hidden}}
  .top{{background:#dc2626;color:#fff;padding:28px 24px;text-align:center}}
  .top h1{{margin:0;font-size:20px;font-weight:700}}
  .body{{padding:24px 28px}}
  .info{{background:#fef2f2;border:1px solid #fecaca;border-radius:10px;padding:14px 16px;margin-bottom:16px;font-size:14px;color:#374151;line-height:1.7}}
  .info p{{margin:0}}
  .btn-reject{{width:100%;padding:13px;background:#dc2626;color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:700;cursor:pointer;margin-top:16px}}
  .btn-reject:hover{{background:#b91c1c}}
  .sig{{font-size:11px;color:#94a3b8;margin-top:20px;padding-top:14px;border-top:1px solid #f1f5f9;line-height:1.6;text-align:center}}
</style>
</head><body>
  <div class="card">
    <div class="top"><h1>Decline Registration</h1></div>
    <div class="body">
      <div class="info">
        <p><strong>Name:</strong> {safe_name}</p>
        <p><strong>Email:</strong> {safe_email}</p>
        <p><strong>Role:</strong> {safe_role}</p>
        <p><strong>Unit:</strong> {safe_unit}</p>
      </div>
      <div style="margin:0 0 4px;text-align:left">
        <label style="display:block;font-size:13px;font-weight:600;color:#374151;margin-bottom:6px">
          Reason for declining <span style="font-weight:400;color:#94a3b8">(optional — sent to applicant)</span>
        </label>
        <textarea name="reason" rows="3"
          placeholder="e.g. I did not authorise this registration, please contact me directly."
          style="width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;
                 color:#374151;resize:vertical;font-family:inherit;box-sizing:border-box"></textarea>
      </div>
      <form method="POST" action="{post_url}">
        <input type="hidden" name="token" value="{safe_token}"/>
        <input type="hidden" name="action" value="reject"/>
        <button type="submit" class="btn-reject">&#10007; &nbsp;Confirm Decline</button>
      </form>
      <div class="sig">{html_lib.escape(b_name)} | Executive Committee | Strata Manager<br>
      A: {html_lib.escape(b_addr)}</div>
    </div>
  </div>
</body></html>""")


@router.post("/auth/registration-decision", response_class=HTMLResponse)
@rate_limit("rate_limit_registration_decision", 10)
async def process_registration_decision_via_token(
        request: Request,
        background_tasks: BackgroundTasks,
        token: str = Form(...),
        action: str = Form(...),
        instructions: str = Form(default="", max_length=1000),
        reason: str = Form(default="", max_length=1000),
):
    """Submit owner approve/reject decision via single-use token. No login required."""
    _portal_url = _get_portal_url()

    settings_doc = await _get_general_settings_or_default(DEFAULT_BUILDING_ID, {"_id": 0}, settings_db=db)
    b_name = settings_doc.get("building_name", "Our Residences")
    b_addr = settings_doc.get("building_address", "")

    if action not in ("approve", "reject"):
        return _reg_decision_html_result("Invalid Request", "Invalid Action",
                                         "<p>The link you followed is not valid. Please contact the Strata Manager.</p>",
                                         "#dc2626", _portal_url, b_name, b_addr)

    token_doc, target, err = await _validate_reg_decision_token(token, action, _portal_url, b_name, b_addr)
    if err:
        return err

    settings_doc = await _get_general_settings_or_default(
        target.get("building_id"),
        {"_id": 0},
        fallback_building_id=DEFAULT_BUILDING_ID,
        settings_db=db,
    )
    b_name = settings_doc.get("building_name", b_name)
    b_addr = settings_doc.get("building_address", b_addr)

    now = datetime.now(timezone.utc).isoformat()
    await db.registration_approval_tokens.update_one(
        {"token": token}, {"$set": {"used": True, "used_at": now}}
    )

    safe_name = html_lib.escape(target.get("full_name", ""))
    safe_unit = html_lib.escape(str(target.get("unit_number") or ""))
    safe_role = html_lib.escape((target.get("role") or "").capitalize())
    user_id = target["id"]
    _role_str = target.get("role", "guest")
    _safe_building_name = html_lib.escape(b_name)

    if action == "approve":
        update_fields = {
            "status": "active",
            "owner_approved": True,
            "owner_approved_at": now,
            "updated_at": now,
        }
        if instructions and instructions.strip():
            update_fields["owner_instructions"] = instructions.strip()
        await db.users.update_one({"id": user_id}, {"$set": update_fields})

        # Notify the applicant
        _guide_file = {"guest": "quick_role_guest.html", "tenant": "quick_role_tenant.html"}.get(_role_str,
                                                                                                 "quick_role_owner.html")
        _guide_url = html_lib.escape(f"{_portal_url}/user-guides/{_guide_file}")
        _has_instructions = bool(instructions and instructions.strip())
        _instructions_html = (
            f'<p style="color:#374151;font-size:14px;line-height:1.6">Your host has provided the following instructions:</p>'
            f'<div style="background:#fff;border-left:4px solid #16a34a;padding:14px 18px;margin:16px 0;border-radius:6px;font-size:14px;color:#374151;line-height:1.7;white-space:pre-wrap">'
            f'{html_lib.escape(instructions.strip())}</div>'
        ) if _has_instructions else ""
        _instructions_text = f"\n\nHost instructions:\n{instructions.strip()}" if _has_instructions else ""
        _sig_html = (
            '<div style="border-top:1px solid #e2e8f0;margin-top:24px;padding-top:16px;'
            'font-size:12px;color:#64748b;line-height:1.8">'
            f'<p style="margin:0;font-weight:600;color:#2F4F4F;font-size:13px">{_safe_building_name} | Building Management</p>'
            f'<p style="margin:3px 0 0">Portal: <a href="{html_lib.escape(_portal_url)}" style="color:#2563eb;text-decoration:none">{html_lib.escape(_portal_url)}</a></p>'
            '</div>'
        )
        applicant_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
  <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;padding:0;background:#f1f5f9}}
    .wrap{{max-width:600px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 32px rgba(0,0,0,.10)}}
    .hdr{{background:#2F4F4F;color:#fff;padding:30px;text-align:center}}
    .hdr h1{{margin:0;font-size:22px;font-weight:700}}
    .body{{padding:28px 30px}}
    .badge{{display:inline-block;background:#16a34a;color:#fff;font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px}}
    .btn{{display:inline-block;padding:13px 28px;border-radius:8px;font-weight:700;font-size:14px;text-decoration:none;background:#2F4F4F;color:#fff;margin-top:18px}}
    .footer{{background:#f8fafc;padding:20px 30px}}
  </style>
</head><body>
  <div class="wrap">
    <div class="hdr"><h1>{_safe_building_name}</h1></div>
    <div class="body">
      <span class="badge">Registration Approved</span>
      <h2 style="margin-top:0;font-size:18px;color:#1e293b">Welcome, {html_lib.escape(target.get("full_name", ""))}!</h2>
      <p style="font-size:14px;color:#475569;line-height:1.6">
        Your <strong>{safe_role}</strong> registration for Unit <strong>{safe_unit}</strong>
        has been approved by the unit owner.
      </p>
      {_instructions_html}
      <p style="color:#374151;font-size:14px;line-height:1.6">The Strata Manager is completing the final review. You will receive another email once your account is fully activated.</p>
      <p style="font-size:14px;color:#475569;line-height:1.6">
        In the meantime, review your <a href="{_guide_url}" style="color:#2563eb">{safe_role} Quick Guide</a>
        to get familiar with the portal and building rules.
      </p>
      <a href="{html_lib.escape(_portal_url)}" class="btn">Visit the Portal</a>
    </div>
    <div class="footer">{_sig_html}</div>
  </div>
</body></html>"""
        if target.get("email"):
            background_tasks.add_task(
                send_email_async, target["email"],
                f"Your {safe_role} Registration is Approved — Unit {target.get('unit_number')}",
                applicant_html,
                f"Your {_role_str} registration for Unit {target.get('unit_number')} has been approved by the unit owner.{_instructions_text}",
                context=f"owner_approved_notify_{_role_str}:{user_id}",
            )

        # Notify admins that activation is needed
        admin_users_list = await _get_staff_registration_reviewers(target.get("building_id") or DEFAULT_BUILDING_ID)
        if admin_users_list:
            _review_link = f"{_portal_url}/admin/users?tab=residents&search={quote_plus(target.get('full_name', ''))}"
            _s_review_link = html_lib.escape(_review_link)
            await db.user_notifications.insert_many([
                {
                    "id": str(uuid.uuid4()),
                    "user_id": adm["id"],
                    "title": "Registration Approved by Owner — Awaiting Your Approval",
                    "message": (
                        f"{target.get('full_name')} ({target.get('email')}) registered as "
                        f"{_role_str} for Unit {target.get('unit_number')} "
                        "has been approved by the unit owner. Please review and activate."
                    ),
                    "type": "user_approval",
                    "related_id": user_id,
                    "link": f"/admin/users?tab=residents&search={quote_plus(target.get('full_name', ''))}",
                    "is_read": False,
                    "created_at": now,
                }
                for adm in admin_users_list
            ])
            adm_html = (
                f'<div style="font-family:sans-serif;max-width:600px;margin:0 auto">'
                f'<div style="background:#2F4F4F;color:#fff;padding:24px;text-align:center;border-radius:8px 8px 0 0"><h2 style="margin:0">{_safe_building_name}</h2></div>'
                f'<div style="background:#f9f9f9;padding:28px;border-radius:0 0 8px 8px">'
                f'<h3 style="margin-top:0">Action Required: Registration Approved by Owner</h3>'
                f'<p>The unit owner has approved {html_lib.escape(target.get("full_name", ""))} ({html_lib.escape(target.get("email", ""))}) for Unit {safe_unit}. Please review and activate their account.</p>'
                f'<p><a href="{_s_review_link}" style="background:#16a34a;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:700">Activate Account</a></p>'
                f'</div></div>'
            )
            for adm in admin_users_list:
                if adm.get("email"):
                    background_tasks.add_task(
                        send_email_async, adm["email"],
                        f"Action Required: {target.get('full_name')} — Owner Approved, Awaiting Activation",
                        adm_html,
                        f"Owner approved {target.get('full_name')} for Unit {target.get('unit_number')}. Review at: {_review_link}",
                        context=f"owner_approved_token:{user_id}",
                    )

        return _reg_decision_html_result(
            "Approved", "Registration Approved",
            f"<p>You have approved <strong>{safe_name}</strong>'s registration as a {safe_role} for Unit {safe_unit}.</p>"
            f"<p>The Strata Manager has been notified and will complete the account activation shortly.</p>",
            "#16a34a", _portal_url, b_name, b_addr
        )

    else:  # reject
        # Cascade: deactivate user_units for the target building
        # Note: in this token-based flow, building context is derived from target.get("building_id")
        t_bid = target.get("building_id") or DEFAULT_BUILDING_ID
        await db.user_units.update_many(
            {"building_id": t_bid, "user_id": user_id, "is_active": True},
            {"$set": {"is_active": False, "actual_end_date": now}},
        )
        await db.notifications.delete_many({"building_id": t_bid, "user_id": user_id})

        # Remove membership for this building first
        await db.memberships.delete_one({"building_id": t_bid, "user_id": user_id})

        # SECURITY FIX: Only deactivate the user globally if they have no other active memberships
        other_memberships = await db.memberships.count_documents({"user_id": user_id, "is_active": True})

        if other_memberships == 0:
            await db.users.update_one(
                {"id": user_id},
                {"$set": {
                    "status": "archived",
                    "is_active": False,
                    "is_approved": False,
                    "archived_at": now,
                    "archived_reason": "owner_rejected_via_email",
                    "updated_at": now,
                }},
            )

        if target.get("email"):
            _reason_block = ""
            if reason and reason.strip():
                _reason_block = (
                    f'<div style="background:#fff;border-left:4px solid #dc2626;padding:12px 16px;margin:12px 0;'
                    f'border-radius:6px;font-size:14px;color:#374151;line-height:1.6;white-space:pre-wrap">'
                    f'{html_lib.escape(reason.strip())}</div>'
                )
            background_tasks.add_task(
                send_email_async, target["email"],
                "Registration Status Update",
                (
                    f'<div style="font-family:sans-serif;max-width:600px;margin:0 auto">'
                    f'<div style="background:#2F4F4F;color:#fff;padding:24px;text-align:center;border-radius:8px 8px 0 0"><h2 style="margin:0">{_safe_building_name}</h2></div>'
                    f'<div style="background:#f9f9f9;padding:28px;border-radius:0 0 8px 8px">'
                    f'<h3 style="margin-top:0">Registration Update for Unit {safe_unit}</h3>'
                    f'<p>Unfortunately, your registration as a {safe_role} for Unit {safe_unit} could not be confirmed by the unit owner.</p>'
                    f'{_reason_block}'
                    f'<p>If you believe this is an error, please contact building management.</p>'
                    f'</div></div>'
                ),
                f"Your registration for Unit {target.get('unit_number')} was declined. Contact building management if you believe this is an error.",
                context=f"owner_rejected_token:{user_id}",
            )

        return _reg_decision_html_result(
            "Declined", "Request Declined",
            f"<p>You have declined <strong>{safe_name}</strong>'s registration request for Unit {safe_unit}.</p>"
            f"<p>They have been notified. No further action is required.</p>",
            "#dc2626", _portal_url, b_name, b_addr
        )


# ── Mail access ────────────────────────────────────────────────────────────────


@router.get("/mail/access", response_model=MailAccessResponse)
async def get_mail_access(current_user: dict = Depends(get_current_user)):
    """Return mail credentials for eligible users (owners, EC, chairman, admin)."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_access_email:
        raise HTTPException(status_code=403, detail="Email access is only available to property owners")

    mail_username = current_user.get("mail_username")
    raw_password = current_user.get("mail_password")

    if not mail_username or not raw_password:
        raise HTTPException(status_code=404, detail="Mail account not configured for this user")

    # Decrypt if stored encrypted; fall back gracefully during migration window
    try:
        mail_password = decrypt_sensitive(raw_password) if is_encrypted(raw_password) else raw_password
    except Exception:
        mail_password = raw_password

    return MailAccessResponse(
        mail_username=mail_username,
        mail_password=mail_password,
        mail_url=os.environ.get("WEBMAIL_URL") or os.environ.get("MAIL_URL") or "",
        has_access=True,
    )


@router.put("/mail/update-password")
@rate_limit("rate_limit_change_password", 10)
async def update_mail_password(
        request: Request,
        data: MailPasswordUpdate,
        current_user: dict = Depends(get_current_user)
):
    """Self-service mail password update (DB only — Migadu sync via admin endpoint)."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_access_email:
        raise HTTPException(status_code=403, detail="No email access")

    await db.users.update_one(
        {"id": current_user["id"]},
        {"$set": {"mail_password": encrypt_sensitive(data.new_password)}},
    )
    return {"success": True, "message": "Mail password updated"}


@router.put("/mail/admin-update-password")
async def admin_update_mail_password(
        data: AdminMailPasswordUpdate, current_user: dict = Depends(get_current_user)
):
    """Super admin: update any user's mail password in MongoDB."""
    if effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Only super admins can update user mail passwords")

    user = await db.users.find_one({"mail_username": data.mail_username})
    if not user:
        raise HTTPException(status_code=404, detail=f"User not found with mail username: {data.mail_username}")

    result = await db.users.update_one(
        {"mail_username": data.mail_username},
        {"$set": {"mail_password": encrypt_sensitive(data.mail_password)}},
    )
    if result.modified_count == 0:
        return {"success": True, "message": f"Password already set for {data.mail_username}",
                "user_email": user.get("email"), "user_name": user.get("full_name")}

    return {"success": True, "message": f"Mail password updated for {data.mail_username}",
            "user_email": user.get("email"), "user_name": user.get("full_name")}


@router.post("/admin/reset-user-passwords")
async def admin_reset_user_passwords(
        data: AdminUserPasswordReset, current_user: dict = Depends(get_current_user)
):
    """Super admin: unified portal login + mail password reset (DB + Migadu API)."""
    if effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Only super admins can reset user passwords")

    ident = data.identifier.strip().lower()
    user = await db.users.find_one({"$or": [{"email": ident}, {"mail_username": ident}]}, {"_id": 0})

    # PostgreSQL-resident users have no Mongo document at all — measured 2026-08-29, all
    # 125 active core.users rows for East Gate. Without this the admin sees the account on
    # /admin/users and gets "No user found with email ..." when trying to reset it, which
    # is the reported bug.
    if not user:
        from db_postgres.repos.identity_repo import find_user_for_auth as _pg_find

        pg_user = await _pg_find(ident)
        if pg_user:
            user = dict(pg_user)
    if not user:
        raise HTTPException(status_code=404, detail=f"No user found with email or mail username: {data.identifier}")

    now = datetime.now(timezone.utc).isoformat()
    results = {
        "user_name": user.get("full_name", ""),
        "user_email": user.get("email", ""),
        "mail_username": user.get("mail_username", ""),
        "portal_login": {"status": "skipped", "detail": ""},
        "mail_db": {"status": "skipped", "detail": ""},
        "migadu_server": {"status": "skipped", "detail": ""},
    }

    if data.reset_portal:
        try:
            new_hash = hash_password(data.new_password)
            # This handler's Mongo lookup is BY EMAIL, so when a Mongo row exists
            # `user["id"]` is already that row's own id and the update below finds it —
            # unlike the id-keyed handlers in server.py, where the same person's
            # PostgreSQL uuid matched nothing (footgun #24). `mongo_id` is honoured only
            # so this stays correct if the resolution above is ever changed to an
            # id-based one; today it is always absent and the fallback is what runs.
            #
            # For the five genuinely PostgreSQL-only accounts there is no Mongo row and
            # `matched_count` is 0 — correct, and the PostgreSQL write below is the one
            # that matters, because login reads core.users first.
            _mongo_id = user.get("mongo_id") or user.get("id")
            _mongo_result = await db.users.update_one(
                {"id": _mongo_id},
                {"$set": {"password_hash": new_hash, "updated_at": now},
                 "$unset": {"hashed_password": ""}},
            )

            # POSTGRES IS WHAT LOGIN READS. /auth/login resolves core.users first and only
            # falls back to Mongo, so a Mongo-only password write updates a record
            # authentication never consults: the admin is told the reset succeeded, the
            # old password keeps working and the new one does not. README §"Postgres Is
            # What Login Reads" records three earlier password paths with this defect;
            # this was the fourth.
            #
            # Awaited and checked rather than fire-and-forget: a credential change that
            # silently fails is the one case where reporting success is worst.
            from db_postgres.repos.identity_repo import set_password_hash as _pg_set_password

            _pg_ok = await _pg_set_password(user.get("email") or ident, new_hash)
            if not _pg_ok and _mongo_result.matched_count == 0:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Password reset did not land in either store — the account was "
                        "not updated. No password has been changed."
                    ),
                )
            await db.password_resets.update_many(
                {"user_id": user["id"], "used": False},
                {"$set": {"used": True, "invalidated_at": now, "invalidated_reason": "admin_reset"}},
            )
            # Postgres is what login reads; a Mongo-only write here would hand the user
            # a password that does not open their account.
            if user.get("email"):
                from db_postgres.repos import identity_repo as _pw_repo

                await _pw_repo.set_password_hash(user["email"], new_hash)
            results["portal_login"] = {"status": "updated", "detail": "Portal login password changed"}
        except Exception as exc:
            results["portal_login"] = {"status": "error", "detail": str(exc)}

    mail_username = user.get("mail_username", "")
    if data.reset_mail and mail_username:
        try:
            await db.users.update_one(
                {"id": user["id"]},
                {"$set": {"mail_password": encrypt_sensitive(data.new_password), "updated_at": now}},
            )
            results["mail_db"] = {"status": "updated", "detail": f"Mail password record updated for {mail_username}"}
        except Exception as exc:
            results["mail_db"] = {"status": "error", "detail": str(exc)}

        try:
            from utils.email import get_email_settings
            email_settings = await get_email_settings()
            migadu_api_key = email_settings.get("migadu_api_key") or os.getenv("MIGADU_API_KEY", "")
            migadu_admin_email = email_settings.get("migadu_admin_email") or os.getenv("MIGADU_ADMIN_EMAIL", "")
            migadu_domain = email_settings.get("migadu_domain") or os.getenv("MIGADU_DOMAIN", "")

            if not migadu_api_key or not migadu_admin_email:
                results["migadu_server"] = {"status": "skipped", "detail": "Migadu API key not configured."}
            else:
                local_part = mail_username.split("@")[0]
                _creds = base64.b64encode(f"{migadu_admin_email}:{migadu_api_key}".encode()).decode()
                import httpx
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.put(
                        f"https://api.migadu.com/v1/domains/{migadu_domain}/mailboxes/{local_part}/",
                        headers={"Authorization": f"Basic {_creds}", "Content-Type": "application/json"},
                        json={"password": data.new_password},
                    )
                if resp.status_code in (200, 201, 204):
                    results["migadu_server"] = {"status": "updated",
                                                "detail": f"Migadu mailbox password changed for {mail_username}"}
                else:
                    results["migadu_server"] = {"status": "error",
                                                "detail": f"Migadu API returned {resp.status_code}: {resp.text[:200]}"}
        except Exception as exc:
            results["migadu_server"] = {"status": "error", "detail": str(exc)}
    elif data.reset_mail and not mail_username:
        results["mail_db"] = {"status": "skipped", "detail": "User has no mail_username"}

    asyncio.create_task(create_audit_log(
        action="admin_password_reset",
        resource_type="user",
        resource_id=user["id"],
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={
            "target_user": user.get("full_name"),
            "target_email": user.get("email"),
            "portal_reset": results["portal_login"]["status"],
            "mail_db_reset": results["mail_db"]["status"],
            "migadu_reset": results["migadu_server"]["status"],
        },
    ))

    any_updated = any(
        r["status"] == "updated" for r in [results["portal_login"], results["mail_db"], results["migadu_server"]])
    return {"success": any_updated, "results": results}


@router.get("/buildings/all", response_model=List[dict])
async def get_all_buildings(
        include_archived: bool = False,
        current_user: dict = Depends(get_current_user),
):
    """Return all buildings in the system. Super Admin only.
    Pass include_archived=true to also return archived buildings (audit/revive use cases)."""
    if effective_role(current_user) != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Super Admin only")
    query = {} if include_archived else {"is_archived": {"$ne": True}}
    docs = await db.buildings.find(query, {"_id": 0}).to_list(500)
    for doc in docs:
        if "building_id" not in doc:
            doc["building_id"] = doc.get("plan_number", "")
    return docs


@router.get("/buildings/me", response_model=List[dict])
async def get_my_buildings(current_user: dict = Depends(get_current_user)):
    """Return all schemes (buildings) the current user has access to.

    Backed by Postgres ``core.schemes`` after the Mongo→PG identity cutover.
    Per-role visibility:
      - Super Admin: every active scheme platform-wide (cross-tenant).
      - Anyone else: every scheme they hold a role assignment for, across
        all tenants — supports multi-unit / multi-building owners on a
        single login (Owner with units in two buildings under different
        Strata Management Organisations sees both).

    Response shape preserves the legacy frontend contract:
      ``{id, building_id, name, tenant_id, tenant_name, jurisdiction,
         is_demo, is_active}`` where ``id`` is the scheme UUID and
      ``building_id`` is the human-readable plan number (e.g. "13195").
    """
    import asyncio as _asyncio

    from db_postgres.repos import identity_repo as _id_repo
    from services.settings_service import get_general_settings_or_default
    from utils.currency import currency_config

    if effective_role(current_user) == UserRole.SUPER_ADMIN:
        rows = await _id_repo.list_all_active_schemes()
    else:
        rows = await _id_repo.list_schemes_for_user(current_user["id"])

    # Currency travels WITH the building, because that is the only scope at which
    # the answer is stable: a super_admin switching between an Australian and a
    # New Zealand scheme must see each one's own money, and a single app-wide
    # setting cannot express that.
    #
    # Resolved here rather than in the frontend because general settings are
    # PG-served for a promoted building and Mongo-served otherwise
    # (settings_service handles that routing); the client must not have to know.
    # gather() rather than a loop so N buildings cost one round-trip's latency,
    # and the projection keeps it to two fields.
    async def _currency_for(building_id: str) -> dict:
        try:
            settings = await get_general_settings_or_default(
                building_id, projection={"currency_code": 1, "currency_locale": 1}
            )
        except Exception:  # noqa: BLE001 - a settings read must never fail the
            # building list; the user would be locked out of switching buildings
            # over a display preference. Fall back to the documented default.
            settings = {}
        return currency_config(settings)

    currencies = await _asyncio.gather(
        *[_currency_for(r["scheme_number"]) for r in rows]
    )

    return [
        {
            "id": r["scheme_id"],
            "building_id": r["scheme_number"],
            "name": r["scheme_name"],
            "tenant_id": r["tenant_id"],
            "tenant_name": r["tenant_name"],
            "jurisdiction": r["jurisdiction"],
            "is_demo": r["is_demo"],
            "is_active": r["is_active"],
            # ISO-4217 code + BCP-47 locale. The frontend derives the SYMBOL from
            # these; it is never sent a bare "$", which is ambiguous across AUD,
            # NZD, USD, SGD and HKD.
            **currency,
        }
        for r, currency in zip(rows, currencies)
    ]


@router.post("/auth/switch-building")
async def switch_building(data: BuildingSwitchRequest, current_user: dict = Depends(get_current_user)):
    """Switch the current session to a different building context.

    Postgres-backed access check:
      - Super Admin: any active scheme is fair game (cross-tenant admin).
      - Anyone else: must hold an active role assignment on the target scheme.
    Accepts either a scheme UUID or a legacy plan-number string in
    ``data.building_id`` (matches the building-switcher UI contract).
    """
    from db_postgres.repos import identity_repo as _id_repo

    target = data.building_id
    scheme = await _id_repo.get_scheme_by_id(target) or await _id_repo.get_scheme_by_number(target)
    if not scheme:
        raise HTTPException(status_code=404, detail="Building not found")

    scheme_uuid = str(scheme["scheme_id"])
    # JWT carries the plan number (e.g. "13195") not the UUID so that MongoDB
    # tenant-scoped queries keep using the human-readable partition key.
    jwt_building_id = scheme.get("scheme_number") or scheme_uuid
    building = {
        "id": scheme_uuid,
        "building_id": jwt_building_id,
        "name": scheme.get("scheme_name"),
    }

    # Verify access for non-SAs
    if effective_role(current_user) != UserRole.SUPER_ADMIN:
        # Cross-tenant membership check via the user's role assignments
        accessible = await _id_repo.list_schemes_for_user(current_user["id"])
        if not any(str(s["scheme_id"]) == scheme_uuid for s in accessible):
            raise HTTPException(status_code=403, detail="You do not have access to this building")

    # Create a NEW token with the building_id claim; honour guest end_date cap
    _g_end_date = None
    if effective_role(current_user) == "guest":
        _gu = await db.user_units.find_one(
            {"user_id": current_user["id"], "role_at_unit": "guest", "is_active": True},
            {"_id": 0, "end_date": 1},
        )
        if _gu:
            _g_end_date = _gu.get("end_date")
    token = create_token(current_user["id"], current_user["email"], current_user["role"],
                         building_id=jwt_building_id,
                         end_date=_g_end_date, tenant_id=current_user.get("tenant_id"))

    if effective_role(current_user) == UserRole.SUPER_ADMIN:
        asyncio.create_task(create_audit_log(
            action="super_admin_building_access",
            resource_type="building",
            resource_id=jwt_building_id,
            user_id=current_user["id"],
            user_name=current_user["full_name"],
            details={"building_name": building.get("name"), "scheme_id": scheme_uuid, "source": "switch_building"},
            building_id=jwt_building_id,
        ))
    elif effective_role(current_user) == UserRole.ADMIN_STAFF:
        asyncio.create_task(create_audit_log(
            action="admin_staff_building_access",
            resource_type="building",
            resource_id=jwt_building_id,
            user_id=current_user["id"],
            user_name=current_user["full_name"],
            details={"building_name": building.get("name"), "scheme_id": scheme_uuid, "source": "switch_building"},
            building_id=jwt_building_id,
        ))

    # Return new token and building info
    return {
        "token": token,
        "user": user_to_response(current_user),
        "building": building
    }


# ── Multi-unit: switch active unit, list units, add unit ─────────────────────


@router.post("/auth/switch-unit")
async def switch_unit(
        data: UnitSwitchRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        _feature: dict = Depends(require_feature("multi_unit_ownership")),
):
    """
    Switch the active unit context for a multi-unit owner.

    Re-issues a JWT with unit_number set to the requested unit. The new token
    makes all owner-scoped endpoints (financials, levy ledger, notifications)
    reflect the switched unit without mutating the database.
    """
    # Verify the user is actually linked to this unit in the current building
    link = await db.user_units.find_one({
        "user_id": current_user["id"],
        "unit_number": data.unit_number,
        "is_active": True,
    })
    if not link:
        raise HTTPException(status_code=403, detail="You are not linked to this unit")

    token = create_token(
        user_id=current_user["id"],
        email=current_user["email"],
        role=current_user["role"],
        building_id=building_id,
        unit_number=data.unit_number,
        tenant_id=current_user.get("tenant_id"),
    )
    current_user["unit_number"] = data.unit_number
    await _enrich_owned_units(current_user, building_id)
    return {"token": token, "user": user_to_response(current_user), "unit_number": data.unit_number}


@router.get("/auth/my-units")
async def get_my_units(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        _feature: dict = Depends(require_feature("multi_unit_ownership")),
):
    """Return all units the authenticated user is actively linked to in the current building."""
    cursor = db.user_units.find(
        {"user_id": current_user["id"], "is_active": True},
        {"_id": 0, "unit_number": 1, "role_at_unit": 1, "is_primary": 1, "start_date": 1},
    )
    docs = await cursor.to_list(50)
    # Enrich with unit details
    unit_numbers = [d["unit_number"] for d in docs if d.get("unit_number")]
    if not unit_numbers:
        return []
    unit_docs = await db.units.find(
        {"unit_number": {"$in": unit_numbers}},
        {"_id": 0, "unit_number": 1, "unit_type": 1, "entitlement": 1, "floor_level": 1},
    ).to_list(50)
    unit_map = {u["unit_number"]: u for u in unit_docs}
    result = []
    for doc in docs:
        un = doc.get("unit_number")
        if not un:
            continue
        info = unit_map.get(un, {})
        result.append({
            "unit_number": un,
            "role_at_unit": doc.get("role_at_unit"),
            "is_primary": doc.get("is_primary", False),
            "unit_type": info.get("unit_type"),
            "entitlement": info.get("entitlement"),
            "floor_level": info.get("floor_level"),
            "start_date": doc.get("start_date"),
            "is_active_session": un == current_user.get("unit_number"),
        })
    result.sort(key=lambda r: (0 if r["is_primary"] else 1, r["unit_number"]))
    return result


@router.post("/auth/add-unit")
async def add_unit(
        data: AddUnitRequest,
        background_tasks: BackgroundTasks,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        _feature: dict = Depends(require_feature("multi_unit_ownership")),
):
    """
    Allow an authenticated owner to link an additional unit to their account.

    Creates a new user_units record and updates the membership units list.
    Admins are notified so they can verify the ownership claim.
    """
    _role = effective_role(current_user)
    if _role not in {"owner", "strata_admin", "ec_member", "strata_manager", "super_admin"}:
        raise HTTPException(status_code=403, detail="Only owners and building staff can add additional units")

    unit_number = data.unit_number.strip()

    # Verify the unit exists in this building
    unit_doc = await db.units.find_one({"unit_number": unit_number}, {"_id": 0, "unit_number": 1})
    if not unit_doc:
        raise HTTPException(status_code=400, detail="Unit not found in this building")

    # Prevent duplicate links — check both active and pending (is_active=False) claims
    existing_link = await db.user_units.find_one({
        "user_id": current_user["id"],
        "unit_number": unit_number,
    })
    if existing_link:
        if existing_link.get("is_active"):
            raise HTTPException(status_code=409, detail="You are already linked to this unit")
        raise HTTPException(status_code=409,
                            detail="A pending claim for this unit already exists awaiting admin verification")

    now = datetime.now(timezone.utc).isoformat()
    from datetime import date as _date
    new_uu_id = str(uuid.uuid4())
    await asyncio.gather(
        db.user_units.insert_one({
            "id": new_uu_id,
            "user_id": current_user["id"],
            "unit_number": unit_number,
            "role_at_unit": "owner",
            "start_date": _date.today().isoformat(),
            "end_date": None,
            "actual_end_date": None,
            "is_active": False,  # pending admin ownership verification before granting access
            "is_primary": False,
            "lease_document_id": None,
            "lease_start_date": None,
            "lease_end_date": None,
            "auto_expire_enabled": False,
            "expiration_date": None,
            "guest_type": None,
            "host_user_id": None,
            "approved_by": None,
            "approved_date": None,
            "approval_notes": None,
            "created_at": now,
            "updated_at": now,
        }),
        db.memberships.update_one(
            {"user_id": current_user["id"], "building_id": building_id},
            {"$addToSet": {"units": unit_number}},
        ),
    )

    # Notify only admins who are members of this building (prevents cross-tenant leakage)
    admin_users = await _get_staff_registration_reviewers(building_id)
    notifs = [
        {
            "id": str(uuid.uuid4()),
            "user_id": admin["id"],
            "title": f"Unit Claim: {current_user['full_name']} added Unit {unit_number}",
            "message": (
                f"{current_user['full_name']} ({current_user['email']}) has added Unit {unit_number} "
                f"to their owner account. Please verify this ownership claim."
            ),
            "type": "unit_claim",
            "related_id": current_user["id"],
            "link": f"/admin/users?search={current_user['email']}",
            "is_read": False,
            "created_at": now,
        }
        for admin in admin_users
    ]
    if notifs:
        await db.user_notifications.insert_many(notifs)

    # Email admins — same urgency as a new registration; in-app alone is insufficient
    # because admins may not open the portal before ownership verification is needed.
    portal_url = _get_portal_url()
    safe_name = html_lib.escape(current_user.get("full_name", ""))
    safe_email = html_lib.escape(current_user.get("email", ""))
    safe_unit = html_lib.escape(unit_number)
    safe_portal = html_lib.escape(portal_url)
    email_subject = f"Unit Claim Verification Needed: {current_user.get('full_name', '')} — Unit {unit_number}"
    email_html = (
        f"<h2>Unit Ownership Claim</h2>"
        f"<p><strong>{safe_name}</strong> ({safe_email}) has linked Unit <strong>{safe_unit}</strong> "
        f"to their existing owner account.</p>"
        f"<p>Please verify this ownership claim and approve or reject the link from the "
        f"<a href=\"{safe_portal}/admin/users?search={safe_email}\">Users page</a>.</p>"
    )
    email_text = (
        f"Unit Ownership Claim: {current_user.get('full_name', '')} ({current_user.get('email', '')}) "
        f"linked Unit {unit_number}. Verify at: {portal_url}/admin/users?search={current_user.get('email', '')}"
    )
    sent_emails: set = set()
    for admin in admin_users:
        addr = admin.get("email")
        if addr and addr not in sent_emails:
            background_tasks.add_task(
                send_email_async, addr, email_subject, email_html, email_text,
                context=f"unit_claim:{current_user['id']}:{unit_number}",
            )
            sent_emails.add(addr)
    if NOTIFY_BCC_EMAIL and NOTIFY_BCC_EMAIL not in sent_emails:
        background_tasks.add_task(
            send_email_async, NOTIFY_BCC_EMAIL, email_subject, email_html, email_text,
            context=f"unit_claim_bcc:{current_user['id']}:{unit_number}",
        )

    owned = await _get_user_owned_units(current_user["id"], building_id)
    current_user["owned_units"] = owned
    return {
        "message": f"Unit {unit_number} added to your account. An administrator has been notified to verify your ownership.",
        "unit_number": unit_number,
        "owned_units": owned,
        "user": user_to_response(current_user),
    }


# ---------------------------------------------------------------------------
# PUBLIC: Buildings list (for /register page)
# ---------------------------------------------------------------------------

@router.get("/auth/buildings")
async def list_public_buildings():
    """
    Returns active buildings for the public registration page.
    No authentication required — returns only safe public fields.
    """
    buildings = await db.buildings.find(
        {"is_active": True},
        {"_id": 0, "id": 1, "name": 1, "address": 1, "slug": 1, "description": 1}
    ).to_list(50)
    # Fallback for single-building setups with no buildings collection
    if not buildings:
        buildings = [{
            "id": DEFAULT_BUILDING_ID,
            "name": "Building",
            "address": "",
            "slug": DEFAULT_BUILDING_ID,
        }]
    return buildings


# ---------------------------------------------------------------------------
# PUBLIC: Staff / Professional self-registration
# ---------------------------------------------------------------------------

STAFF_SELF_REG_ROLES = {
    "strata_manager",
    "admin_staff",
    "real_estate_agent",
    "service_provider",
}

SUPER_ADMIN_CREATEABLE_STAFF_ROLES = {
    "strata_manager",
    "admin_staff",
    "real_estate_agent",
    "service_provider",
}

MANAGER_CREATEABLE_STAFF_ROLES = {
    "service_provider",
}


class StaffRegisterRequest(BaseModel):
    email: str = Field(..., max_length=100)
    password: str = Field(..., max_length=128)
    full_name: str = Field(..., max_length=200)
    role: str = Field(..., max_length=50)
    phone: Optional[str] = Field(None, max_length=20)
    organisation: Optional[str] = Field(None, max_length=200)  # company / agency name
    professional_licence: Optional[str] = Field(None, max_length=100)  # REA licence, strata licence, etc.
    building_id: Optional[str] = None
    terms_accepted: bool = False


class AdminCreateStaffRequest(BaseModel):
    email: str = Field(..., max_length=100)
    password: str = Field(..., max_length=128)
    full_name: str = Field(..., max_length=200)
    role: str = Field(..., max_length=50)  # must be in ADMIN_CREATEABLE_STAFF_ROLES
    phone: Optional[str] = Field(None, max_length=20)
    organisation: Optional[str] = Field(None, max_length=200)
    professional_licence: Optional[str] = Field(None, max_length=100)
    building_id: Optional[str] = None
    send_welcome_email: bool = True


@router.post("/auth/register/staff")
@rate_limit("rate_limit_register", 5)
async def register_staff(request: Request, data: StaffRegisterRequest):
    """
    Staff / professional self-registration.
    Roles allowed: strata_manager, admin_staff, real_estate_agent, service_provider.
    Account enters the standard pending-admin approval queue for management review.
    """
    data.role = _normalize_staff_role_input(data.role)
    if data.role not in STAFF_SELF_REG_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Role '{data.role}' cannot self-register. Allowed: {sorted(STAFF_SELF_REG_ROLES)}"
        )
    if not data.terms_accepted:
        raise HTTPException(status_code=400, detail="You must accept the terms and conditions.")
    if len(data.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    # Normalise email
    email = data.email.strip().lower()
    existing = await db.users.find_one({"$or": [{"email": email}, {"portal_email": email}]})
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    building_id = (data.building_id or "").strip()
    if not building_id:
        raise HTTPException(status_code=400, detail="Building is required for staff registration.")

    building = await db.buildings.find_one({"id": building_id, "is_active": True}, {"_id": 0, "id": 1})
    if not building:
        raise HTTPException(status_code=400, detail="Selected building was not found or is inactive.")

    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    role_label = _get_staff_role_label(data.role)
    users_link = f"/admin/users?tab={_get_users_tab_for_role(data.role)}&search={quote_plus(data.full_name.strip())}"
    review_url = f"{_get_portal_url()}{users_link}"
    user_doc = {
        "id": user_id,
        "email": email,
        "password_hash": hash_password(data.password),
        "full_name": data.full_name.strip(),
        "role": data.role,
        "phone": data.phone or "",
        "organisation": data.organisation or "",
        "professional_licence": data.professional_licence or "",
        "building_id": building_id,
        "is_active": False,  # Admin must activate
        "is_approved": False,
        "status": UserStatus.ACTIVE,  # active bucket + is_approved=False drives the admin approval queue
        "terms_accepted": True,
        "terms_accepted_at": now,
        "created_at": now,
        "updated_at": now,
    }
    membership_doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "building_id": building_id,
        "roles": [data.role],
        "is_active": True,
        "is_primary": True,
        "units": [],
        "created_at": now,
    }

    try:
        await asyncio.gather(
            db.users.insert_one(user_doc),
            db.memberships.insert_one(membership_doc),
        )
    except Exception as exc:
        await asyncio.gather(
            db.users.delete_one({"id": user_id}),
            db.memberships.delete_one({"user_id": user_id, "building_id": building_id}),
        )
        raise HTTPException(status_code=500, detail=f"Registration failed during record creation: {exc}")

    reviewers = await _get_staff_registration_reviewers(building_id)
    if reviewers:
        notifications = [
            {
                "id": str(uuid.uuid4()),
                "user_id": reviewer["id"],
                "title": f"New {role_label} Registration Requires Review",
                "message": (
                    f"{data.full_name.strip()} ({email}) registered as {role_label}. "
                    "Review and approve the account from Resident Management."
                ),
                "type": "user_approval",
                "related_id": user_id,
                "link": users_link,
                "is_read": False,
                "created_at": now,
                "building_id": building_id,
            }
            for reviewer in reviewers
        ]
        await db.user_notifications.insert_many(notifications)

    sent_emails = set()
    for reviewer in reviewers:
        recipient = reviewer.get("email")
        if not recipient or recipient in sent_emails:
            continue
        try:
            body = (
                f"<p>A new <strong>{role_label}</strong> has registered and is awaiting activation:</p>"
                f"<ul>"
                f"<li><strong>Name:</strong> {html_lib.escape(data.full_name)}</li>"
                f"<li><strong>Email:</strong> {html_lib.escape(email)}</li>"
                f"<li><strong>Organisation:</strong> {html_lib.escape(data.organisation or '—')}</li>"
                f"<li><strong>Licence:</strong> {html_lib.escape(data.professional_licence or '—')}</li>"
                f"</ul>"
                f"<p><a href='{html_lib.escape(review_url)}'>Open Resident Management to review this registration.</a></p>"
            )
            await send_email_async(
                to_email=recipient,
                subject=f"New {role_label} Registration — Action Required",
                html_content=body,
            )
            sent_emails.add(recipient)
        except Exception:
            pass  # Email failure should not block registration

    asyncio.create_task(create_audit_log(
        action="staff_self_registration",
        resource_type="user",
        resource_id=user_id,
        user_id=user_id,
        user_name=data.full_name,
        details={"role": data.role, "organisation": data.organisation, "email": email},
        building_id=building_id,
    ))

    return {
        "message": (
            f"Registration received. Your {role_label} account is pending administrator activation. "
            "You will receive an email once your account is approved."
        ),
        "status": "pending_approval",
        "role": data.role,
    }


@router.post("/admin/create-staff-user")
async def admin_create_staff_user(
        data: AdminCreateStaffRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Super admins can directly create any staff role.
    Building managers can directly create service_provider accounts for buildings they can manage.
    """
    data.role = _normalize_staff_role_input(data.role)

    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized to create staff accounts.")

    requester_role = _normalize_staff_role_input(current_user.get("effective_role") or current_user.get("role"))
    allowed_roles = (
        SUPER_ADMIN_CREATEABLE_STAFF_ROLES
        if requester_role == UserRole.SUPER_ADMIN
        else MANAGER_CREATEABLE_STAFF_ROLES
    )
    if data.role not in allowed_roles:
        raise HTTPException(
            status_code=400,
            detail=f"Role '{data.role}' cannot be created via this endpoint. Allowed: {sorted(allowed_roles)}"
        )

    if len(data.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    email = data.email.strip().lower()
    existing = await db.users.find_one({"$or": [{"email": email}, {"portal_email": email}]})
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    target_building_id = (data.building_id or building_id or DEFAULT_BUILDING_ID).strip()
    target_building = await db.buildings.find_one(
        {"id": target_building_id, "is_active": True},
        {"_id": 0, "id": 1},
    )
    if not target_building:
        raise HTTPException(status_code=400, detail="Selected building was not found or is inactive.")

    if requester_role != UserRole.SUPER_ADMIN:
        membership = await db.memberships.find_one({
            "user_id": current_user.get("id"),
            "building_id": target_building_id,
            "is_active": True,
        })
        if not membership:
            raise HTTPException(status_code=403, detail="You can only create service users for your building.")

        membership_roles = {
            _normalize_staff_role_input(role)
            for role in membership.get("roles", [])
            if isinstance(role, str)
        }
        if not membership_roles.intersection(_STAFF_REVIEWER_ROLES):
            raise HTTPException(
                status_code=403,
                detail="You must be a building manager in the selected building to create service users.",
            )
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    role_label = _get_staff_role_label(data.role)

    user_doc = {
        "id": user_id,
        "email": email,
        "password_hash": hash_password(data.password),
        "full_name": data.full_name.strip(),
        "role": data.role,
        "phone": data.phone or "",
        "organisation": data.organisation or "",
        "professional_licence": data.professional_licence or "",
        "building_id": target_building_id,
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "terms_accepted": True,
        "terms_accepted_at": now,
        "created_at": now,
        "updated_at": now,
        "created_by_admin": current_user.get("id"),
    }
    membership_doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "building_id": target_building_id,
        "roles": [data.role],
        "is_active": True,
        "is_primary": True,
        "units": [],
        "created_at": now,
    }

    try:
        await asyncio.gather(
            db.users.insert_one(user_doc),
            db.memberships.insert_one(membership_doc),
        )
    except Exception as exc:
        await asyncio.gather(
            db.users.delete_one({"id": user_id}),
            db.memberships.delete_one({"user_id": user_id, "building_id": target_building_id}),
        )
        raise HTTPException(status_code=500, detail=f"Staff account creation failed during record creation: {exc}")

    if data.send_welcome_email:
        try:
            building_doc = await _get_building_public_doc(target_building_id)
            safe_building = html_lib.escape(building_doc.get("name") or "Building")
            safe_name = html_lib.escape(data.full_name.strip())
            safe_role = html_lib.escape(role_label)
            body = (
                f"<p>Hi {safe_name},</p>"
                f"<p>Your <strong>{safe_role}</strong> account for {safe_building} has been created by an administrator.</p>"
                f"<p><strong>Email:</strong> {html_lib.escape(email)}<br/>"
                f"<strong>Temporary Password:</strong> {html_lib.escape(data.password)}</p>"
                f"<p>Please <a href='{_get_portal_url()}/login'>log in</a> and change your password immediately.</p>"
                f"<p>{safe_building} Platform</p>"
            )
            await send_email_async(
                to_email=email,
                subject=f"Your {safe_building} {safe_role} Account",
                html_content=body,
            )
        except Exception:
            pass  # Don't block creation if email fails

    asyncio.create_task(create_audit_log(
        action="admin_create_staff_user",
        resource_type="user",
        resource_id=user_id,
        user_id=current_user.get("id", ""),
        user_name=current_user.get("full_name", ""),
        details={"created_user": email, "role": data.role, "organisation": data.organisation},
        building_id=target_building_id,
    ))

    return {
        "message": f"{role_label} account created successfully.",
        "user_id": user_id,
        "email": email,
        "role": data.role,
        "is_active": True,
        "welcome_email_sent": data.send_welcome_email,
    }


__all__ = ["router"]
