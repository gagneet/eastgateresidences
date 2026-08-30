# @featuretrace:coexistence — Auth context, tenant scoping, and multi-tenant user resolution.
# Layer: model
# Data flow: request → get_current_user/get_current_building → auth context (building-scoped).
# Related: backend/database.py
#           backend/request_context.py
#           tests/backend/test_tenant_isolation_p0t01.py
# Toggle: auth always active; tenant resolution via feature gates for elevation/escalation

"""
Authentication Utilities

Functions for password hashing, JWT token management, and user authentication.
"""
import uuid
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt
from fastapi import HTTPException, Depends, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRATION_HOURS
from database import db

# DEFAULT_BUILDING_ID must be defined BEFORE any model imports to prevent
# a circular-import failure: models/finance.py imports this symbol during
# the auth.py module initialisation.
DEFAULT_BUILDING_ID = ""  # clean-break: no silent fallback to a specific building

from models.user import UserRole, normalize_user_role
from request_context import set_ctx_building_id, set_ctx_user_id, get_ctx_building_id


async def _backfill_legacy_unit_context(user: dict) -> None:
    """Bridge unit context for Postgres-authenticated sessions during the cutover.

    ``core.users`` has no unit columns and ``core.user_units`` is not yet
    populated (identity_core is still mongo_primary per domain_cutover_status),
    so a user fetched via ``identity_repo.get_user_by_id`` carries no
    ``unit_number`` / ``owned_units``. Route guards of the form
    ``unit_number != current_user.get("unit_number")`` then 403 owners on
    their own unit (observed on /finance/unit-dashboard-overview for
    co-owned East Gate lot TH087).

    MongoDB ``users`` remains the identity source of truth until cutover, so
    the missing fields are merged from the Mongo record matched by email.
    ``custom_permissions`` is included because get_user_permissions() reads it
    and the Postgres row only carries ``permission_overrides``.
    Remove this bridge when identity_core is promoted to postgres_primary.
    """
    if user.get("unit_number") and user.get("owned_units"):
        return
    email = (user.get("email") or "").strip().lower()
    if not email:
        return
    try:
        legacy = await db.users.find_one(
            {"$or": [{"email": email}, {"portal_email": email}]},
            {"_id": 0, "id": 1, "unit_number": 1, "owned_units": 1, "custom_permissions": 1},
        )
    except Exception:
        return
    if not legacy:
        return
    if not user.get("unit_number") and legacy.get("unit_number"):
        user["unit_number"] = legacy["unit_number"]
    if not user.get("owned_units") and legacy.get("owned_units"):
        user["owned_units"] = legacy["owned_units"]
    if not user.get("custom_permissions") and legacy.get("custom_permissions"):
        user["custom_permissions"] = legacy["custom_permissions"]
    if legacy.get("id"):
        # Mongo user id — lets routes that key Mongo collections on user_id
        # resolve records created before the Postgres login path existed.
        user["legacy_user_id"] = legacy["id"]


def _active_elevation(user: dict) -> dict | None:
    """Return temp_elevation dict if currently active, else None."""
    elev = user.get("temp_elevation")
    if not elev:
        return None
    try:
        exp = datetime.fromisoformat(elev["expires_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) < exp:
            return elev
    except Exception:
        pass
    return None


security = HTTPBearer(auto_error=False)

# Hard cap for guest JWT tokens: 364 days (< 1 year, matching the registration end-date limit)
GUEST_JWT_MAX_DAYS = 364


def hash_password(password: str) -> str:
    """Hash a password using bcrypt"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its bcrypt hash"""
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))


def create_token(
        user_id: str,
        email: str,
        role: str,
        building_id: str = None,
        impersonator_id: str = None,
        end_date: str = None,
        organisation_id: str = None,
        unit_number: str = None,
        tenant_id: str = None,
) -> str:
    """Create a JWT token for a user, optionally including a building_id and impersonator claim.

    unit_number, when provided, overrides the user's stored unit_number for the session.
    This enables the UnitSwitcher — an owner with multiple units can switch active unit
    context without changing their primary unit in the database.

    For guest tokens an additional hard cap of GUEST_JWT_MAX_DAYS (364) days is enforced:
    the token will expire at the *earliest* of (standard expiry, end_date, now + 364 days).
    This prevents a guest from holding a perpetually-valid token even after their stay ends.
    """
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(hours=JWT_EXPIRATION_HOURS)

    role = normalize_user_role(role)

    if role == "guest":
        max_guest_expiry = now + timedelta(days=GUEST_JWT_MAX_DAYS)
        expiry = min(expiry, max_guest_expiry)
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                # Make timezone-aware if naive
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                expiry = min(expiry, end_dt)
            except (ValueError, AttributeError):
                pass  # Ignore malformed end_date; already capped at 364 days

    payload = {
        "user_id": user_id,
        "email": email,
        "role": role,
        "exp": expiry,
        "iat": now,
        "jti": str(uuid.uuid4()),
    }
    if building_id:
        payload["building_id"] = building_id
    if impersonator_id:
        payload["impersonator_id"] = impersonator_id
    if unit_number:
        payload["unit_number"] = unit_number
    if tenant_id:
        payload["tenant_id"] = tenant_id
    # Add organisation_id — defaults to single-building org for backward compatibility
    payload["organisation_id"] = organisation_id or "org-silverfox-001"

    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token"""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")



def _parse_revocation_cutoff(value) -> "datetime | None":
    """Normalise sessions_invalidated_at to an aware UTC datetime, or None.

    The value arrives as a datetime from the Mongo path and as an ISO string from the
    Postgres path (identity_repo serialises it through _iso). Returning None on
    anything unparseable deliberately fails OPEN: a malformed timestamp must not lock
    every user out of the platform. The revocation is a security nicety; the login
    system working is not.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    FastAPI dependency to get the current authenticated user.

    Postgres-first: if the JWT carries a ``tenant_id`` claim (issued by the
    new login endpoint) the user is fetched from ``core.users``.  Legacy JWTs
    without ``tenant_id`` fall back to the MongoDB ``users`` collection so
    existing sessions remain valid during the cutover window.

    Raises 401 if not authenticated or user not found.
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_token(credentials.credentials)
    user_id = payload["user_id"]
    tenant_id = payload.get("tenant_id")
    set_ctx_user_id(user_id)

    # ── Postgres path (new tokens) ──────────────────────────────────────────
    if tenant_id:
        from db_postgres.repos import identity_repo
        user = await identity_repo.get_user_by_id(user_id, tenant_id)
        if user:
            await _backfill_legacy_unit_context(user)
    else:
        # ── Legacy MongoDB path (old tokens, pre-cutover sessions) ─────────
        user = await db.users.find_one({"id": user_id}, {"_id": 0})
        if user:
            user["role"] = normalize_user_role(user.get("role"))

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # ── Session revocation ("sign out everywhere") ─────────────────────────
    # A stateless JWT is valid until it expires and nothing server-side stops it, so
    # without this check "sign out everywhere" could only ever be advice. The user
    # record carries the revocation instant; any token issued at or before it is dead
    # except the one explicitly spared — the device the user clicked the button on.
    #
    # Compared on `iat`, which every token carries (create_access_token sets it). A
    # token with no `iat` predates that and cannot be placed relative to the cutoff;
    # it is rejected rather than trusted, because the whole point is to end sessions
    # whose provenance the user is unsure of.
    _revoked_at = user.get("sessions_invalidated_at")
    if _revoked_at:
        _cutoff = _parse_revocation_cutoff(_revoked_at)
        if _cutoff is not None and payload.get("jti") != user.get("session_keep_jti"):
            _iat = payload.get("iat")
            _issued = (
                datetime.fromtimestamp(_iat, tz=timezone.utc)
                if isinstance(_iat, (int, float)) else None
            )
            if _issued is None or _issued <= _cutoff:
                raise HTTPException(
                    status_code=401,
                    detail="This session was signed out from another device. Please sign in again.",
                )

    if not user.get("is_active", True):
        raise HTTPException(status_code=401, detail="User account is deactivated")

    if user.get("status") == "archived":
        raise HTTPException(status_code=401,
                            detail="This account has been archived. Please contact the strata manager.")

    # Attach impersonator info if present in token
    if "impersonator_id" in payload:
        impersonator_id = payload["impersonator_id"]
        if tenant_id:
            from db_postgres.repos import identity_repo
            imp = await identity_repo.get_user_by_id(impersonator_id, tenant_id)
            if not imp or imp.get("role") != UserRole.SUPER_ADMIN or not imp.get("is_active", True):
                raise HTTPException(status_code=401, detail="Invalid impersonation session")
        else:
            imp = await db.users.find_one({"id": impersonator_id}, {"_id": 0, "role": 1, "is_active": 1})
            if not imp or imp.get("role") != UserRole.SUPER_ADMIN or not imp.get("is_active", True):
                raise HTTPException(status_code=401, detail="Invalid impersonation session")
        user["impersonator_id"] = impersonator_id

    # Apply temporary elevation — inject effective_role so permission checks honour it
    elev = _active_elevation(user)
    if elev:
        user["effective_role"] = normalize_user_role(elev.get("role", UserRole.EC_MEMBER))

    building_id = payload.get("building_id")
    if building_id:
        if tenant_id:
            from db_postgres.repos import identity_repo

            scheme = await identity_repo.get_scheme_by_id(building_id) or \
                     await identity_repo.get_scheme_by_number(building_id)
            if not scheme:
                raise HTTPException(status_code=403, detail="Building not found or inactive.")

            scheme_uuid = str(scheme["scheme_id"])
            plan_number = str(scheme.get("scheme_number") or scheme_uuid)
            role = user.get("effective_role") or user.get("role")
            if role == UserRole.SUPER_ADMIN:
                set_ctx_building_id(plan_number)
            else:
                if not await identity_repo.is_user_in_scheme(user_id, scheme_uuid, tenant_id):
                    raise HTTPException(status_code=403, detail="You do not have access to this building.")
                set_ctx_building_id(plan_number)
            user["building_id"] = plan_number
        else:
            # Legacy MongoDB path
            building = await db.buildings.find_one({"id": building_id, "is_active": True}, {"_id": 0})
            if not building:
                raise HTTPException(status_code=403, detail="Building not found or inactive.")
            if user.get("role") == UserRole.SUPER_ADMIN:
                set_ctx_building_id(building_id)
            else:
                membership = await db.memberships.find_one({
                    "user_id": user["id"],
                    "building_id": building_id,
                    "is_active": True
                })
                if not membership:
                    raise HTTPException(status_code=403, detail="You do not have access to this building.")
                set_ctx_building_id(building_id)

    jwt_unit = payload.get("unit_number")
    if jwt_unit:
        user["unit_number"] = jwt_unit

    return user


async def get_optional_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    FastAPI dependency to get the current user if authenticated, None otherwise.
    Does not raise exceptions.
    """
    if not credentials:
        return None
    try:
        payload = decode_token(credentials.credentials)
        tenant_id = payload.get("tenant_id")
        if tenant_id:
            from db_postgres.repos import identity_repo
            return await identity_repo.get_user_by_id(payload["user_id"], tenant_id)
        else:
            user = await db.users.find_one({"id": payload["user_id"]}, {"_id": 0})
            if user:
                user["role"] = normalize_user_role(user.get("role"))
            return user
    except Exception:
        return None


def effective_role(user: dict) -> str:
    """Return the user's effective role, honouring an active temp_elevation.

    Canonical home for the role-resolution logic that was duplicated in
    server.py:_effective_role (now a forwarder), routers/digital_twin.py,
    utils/permissions.py, routers/analytics.py, and 30+ other call sites.

    Temporarily elevated users expose their elevated role via
    user["effective_role"] (set by get_current_user when the elevation is
    active). Unelevated users fall back to their base role.
    """
    raw = user.get("effective_role") or user.get("role", UserRole.GUEST)
    return normalize_user_role(raw)


def is_approved_user(user: dict) -> bool:
    """
    Check if a user is approved or holds an administrative role.
    Only Owners and Tenants can be "approved" residents.
    Administrative roles (Super Admin, Strata Admin, EC Member, Strata Manager,
    Admin Staff) are auto-approved.
    Guests and Service Providers are never considered "approved" residents for community data.
    Temporary elevation is honoured via effective_role.
    """
    if not user:
        return False

    # Use effective_role when a temporary elevation is active; keep roles canonical.
    role = normalize_user_role(user.get("effective_role") or user.get("role"))

    # 1. Administrative roles are always considered "approved"
    # frozenset gives O(1) constant-time lookup — avoids linear scan on a list.
    _ADMIN_ROLES = frozenset({
        UserRole.SUPER_ADMIN,
        UserRole.STRATA_ADMIN,
        UserRole.EC_MEMBER,
        UserRole.STRATA_MANAGER,
        UserRole.ADMIN_STAFF,
    })
    if role in _ADMIN_ROLES:
        return True

    # 2. Only Owners and Tenants can be "approved" residents for community-wide data access
    if role not in frozenset({UserRole.OWNER, UserRole.TENANT}):
        return False

    # 3. Check the explicit approval flag for legitimate residents
    return user.get("is_approved", False)


async def get_approved_user(current_user: dict = Depends(get_current_user)):
    """
    Dependency to ensure user is approved before accessing restricted features.
    Exempts administrative roles (Super Admin, Strata Admin, EC Member,
    Strata Manager, Admin Staff).
    """
    if not is_approved_user(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending approval. Please wait for administrator approval.",
        )
    return current_user


async def get_current_building(
        request: Request,
        current_user: dict = Depends(get_current_user),
        credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str:
    """
    Extract building_id from the JWT token and verify membership.

    Postgres path (JWT has ``tenant_id``):
      - ``building_id`` in the JWT is a ``scheme_id`` UUID string.
      - Super admins may override via ``X-Building-ID`` header (accepts UUID or plan_number).
      - Non-SA users must have an active ``user_role_assignments`` row for the scheme.

    Legacy MongoDB path (JWT without ``tenant_id``):
      - Falls back to ``db.memberships`` for membership check.

    Raises 403 if no building context or no membership.
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_token(credentials.credentials)
    tenant_id = payload.get("tenant_id")
    building_id = payload.get("building_id")

    # Fallback: user's default building from profile
    if not building_id:
        building_id = current_user.get("building_id")

    # Postgres path
    if tenant_id:
        from db_postgres.repos import identity_repo

        async def _resolve_pg_building_context(value: str | None) -> tuple[str, str] | None:
            """Generated function header.

            Function: _resolve_pg_building_context
            Path: backend/utils/auth.py

            Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
            """
            if not value:
                return None
            scheme = await identity_repo.get_scheme_by_id(value) or \
                     await identity_repo.get_scheme_by_number(value)
            if not scheme:
                return None
            scheme_uuid = str(scheme["scheme_id"])
            # Return the legacy plan number for Mongo tenant-scoped code paths.
            plan_number = str(scheme.get("scheme_number") or scheme_uuid)
            return scheme_uuid, plan_number

        effective = current_user.get("effective_role") or current_user.get("role")
        if effective == UserRole.SUPER_ADMIN:
            header_bid = request.headers.get("X-Building-ID")
            if header_bid:
                # Accept UUID (scheme_id) or plan_number string; always return
                # the plan_number so MongoDB tenant-scoped queries keep working.
                resolved = await _resolve_pg_building_context(header_bid)
                if not resolved:
                    raise HTTPException(status_code=403, detail="Building not found or inactive.")
                _, building_id = resolved
            elif building_id:
                # JWT already carries a plan_number from a prior switch-building call.
                # Verify it still resolves to an active scheme.
                resolved = await _resolve_pg_building_context(building_id)
                if not resolved:
                    raise HTTPException(status_code=403, detail="Building not found or inactive.")
                _, building_id = resolved
            if not building_id:
                raise HTTPException(status_code=403, detail="No building context. Please select a building.")
            set_ctx_building_id(building_id)
            return building_id

        # Fallback 2: single-scheme auto-resolve for non-SA
        if not building_id:
            scheme_ids = await identity_repo.get_user_scheme_ids(current_user["id"], tenant_id)
            if len(scheme_ids) == 1:
                building_id = scheme_ids[0]

        if not building_id:
            raise HTTPException(status_code=403, detail="No building context. Please select a building.")

        resolved = await _resolve_pg_building_context(building_id)
        if not resolved:
            raise HTTPException(status_code=403, detail="Building not found or inactive.")
        scheme_uuid, plan_number = resolved

        ctx_bid = get_ctx_building_id()
        if ctx_bid == plan_number:
            return ctx_bid

        if not await identity_repo.is_user_in_scheme(current_user["id"], scheme_uuid, tenant_id):
            raise HTTPException(status_code=403, detail="You do not have access to this building.")

        set_ctx_building_id(plan_number)
        return plan_number

    # ── Legacy MongoDB path ──────────────────────────────────────────────────
    if not building_id:
        memberships = await db.memberships.find(
            {"user_id": current_user["id"], "is_active": True}, {"building_id": 1}
        ).to_list(5)
        if len(memberships) == 1:
            building_id = memberships[0]["building_id"]

    if not building_id:
        raise HTTPException(status_code=403, detail="No building context. Please select a building.")

    effective = current_user.get("effective_role") or current_user.get("role")
    if effective == UserRole.SUPER_ADMIN:
        header_bid = request.headers.get("X-Building-ID")
        if header_bid:
            building_id = header_bid
        ctx_bid = get_ctx_building_id()
        if ctx_bid == building_id:
            return ctx_bid
        building = await db.buildings.find_one({"id": building_id, "is_active": True}, {"_id": 0})
        if not building:
            raise HTTPException(status_code=403, detail="Building not found or inactive.")
        set_ctx_building_id(building_id)
        return building_id

    ctx_bid = get_ctx_building_id()
    if ctx_bid == building_id:
        return ctx_bid

    membership = await db.memberships.find_one({
        "user_id": current_user["id"],
        "building_id": building_id,
        "is_active": True
    })
    if not membership:
        raise HTTPException(status_code=403, detail="You do not have access to this building.")

    set_ctx_building_id(building_id)
    return building_id


async def get_optional_building(
        request: Request = None,
        credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str | None:
    """
    Public-safe building resolver for unauthenticated endpoints.
    Resolution order:
      1. building_id in JWT token (authenticated user)
      2. X-Building-ID request header
      3. building_id query parameter

    Returns None when no building context is present (previously returned
    DEFAULT_BUILDING_ID which silently routed to East Gate — removed).
    """
    bid = None

    if credentials:
        try:
            payload = decode_token(credentials.credentials)
            bid = payload.get("building_id")
        except Exception:
            pass

    if not bid and request is not None:
        bid = request.headers.get("X-Building-ID") or request.query_params.get("building_id")

    if bid:
        set_ctx_building_id(bid)
    return bid


async def get_building_or_400(
        request: Request = None,
        credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """
    Hardened building resolver for public endpoints that query tenant-scoped collections.

    Same as get_optional_building() but raises HTTP 400 instead of returning None.
    """
    bid = None

    if credentials:
        try:
            payload = decode_token(credentials.credentials)
            bid = payload.get("building_id")
        except Exception:
            pass

    if not bid and request is not None:
        bid = request.headers.get("X-Building-ID") or request.query_params.get("building_id")

    if not bid:
        raise HTTPException(
            status_code=400,
            detail="Building context required: supply X-Building-ID header or ?building_id= query parameter.",
        )

    set_ctx_building_id(bid)
    return bid


def is_impersonating(user: dict | None) -> bool:
    """Return True when the session is an admin impersonating another user."""
    return bool(user and "impersonator_id" in user)


__all__ = [
    'security',
    'hash_password',
    'verify_password',
    'create_token',
    'decode_token',
    'effective_role',
    'get_current_user',
    'get_optional_user',
    'get_approved_user',
    'get_current_building',
    'get_optional_building',
    'get_building_or_400',
    'DEFAULT_BUILDING_ID',
    'is_impersonating',
    'GUEST_JWT_MAX_DAYS',
]
