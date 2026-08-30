# @featuretrace:feature-toggle-system — Feature access resolution: require_feature, require_approved_feature, get_effective_feature_access.
# Layer: service
# Data flow: routers/* → require_feature("key") → get_effective_feature_access(user, key)
#             → config_repo.resolve_feature_toggle(building_id, key) → core.feature_toggles (PG)
# Related: backend/routers/feature_toggles.py (_compute_effective_access mirrors this logic)
#           backend/db_postgres/repos/config_repo.py (resolve_feature_toggle)
#           backend/models/feature_toggle.py (FeatureToggleKeys constants)
# Table: core.feature_toggles, core.feature_toggle_overrides, core.users.permission_overrides
# Tests: tests/backend/test_sentinel_feature_toggle_hardening.py
#         tests/backend/test_gap_ft_router_toggle_enforcement.py
#         tests/backend/test_feature_toggle_refactor.py
#
# Resolution order (4-step, first match wins):
#   1. super_admin → always True
#   2. temp-elevated user → site_wide only (user overrides bypassed)
#   3. user-level override (permission_overrides JSONB) → override value
#   4. building/global site_wide default
#
# Debug: set FEATURE_TOGGLE_DEBUG=true to log resolution steps (key + source + result only; no PII).
#
# @featuretrace:user-management — Canonical user serialiser + permission resolver.
# Layer: service
# Data flow: server.py:get_users → user_to_response(user_dict) → UserResponse
#             user_dict.full_name (primary) → first_name + last_name (fallback)
# Related: backend/server.py (GET /users ~line 2177)
#           frontend/src/pages/dashboard/UsersPage.jsx
#           backend/models/user.py (UserResponse)
# Collection: users (global)
# ⚠️ DISPLAY NAME RULE: full_name is the canonical display field.
#    If blank, compose from first_name + last_name. This is the ONLY place
#    that rule is enforced — do not add it elsewhere.
"""
Permission Utilities

Helper functions for managing user permissions and roles.
"""
import logging
import os
from datetime import datetime, timezone

from fastapi import HTTPException, Depends
from typing import Callable

from models.user import Permission, UserRole, UserResponse, DEFAULT_PERMISSIONS, normalize_user_role
from utils.helpers import mask_email, mask_phone

logger = logging.getLogger(__name__)

# Set FEATURE_TOGGLE_DEBUG=true in the environment to enable per-resolution trace logging.
# Logs only: feature_key, resolved value, and resolution source. Never logs PII or secrets.
_TOGGLE_DEBUG: bool = os.getenv("FEATURE_TOGGLE_DEBUG", "").lower() in ("1", "true", "yes")


def _elevation_active(user: dict) -> bool:
    """Return True if user has a non-expired temp_elevation."""
    elev = user.get("temp_elevation")
    if not elev:
        return False
    try:
        exp = datetime.fromisoformat(elev["expires_at"].replace("Z", "+00:00"))
        return datetime.now(timezone.utc) < exp
    except Exception:
        return False


def get_user_permissions(user: dict) -> Permission:
    """
    Get the permissions for a user based on their role and custom permissions.
    Honours temporary elevation (effective_role injected by get_current_user, or
    computed from temp_elevation for users fetched via the users list endpoint).
    """
    # effective_role is set by get_current_user when elevation is active.
    # For users fetched via list endpoints we compute it here.
    role = normalize_user_role(user.get("effective_role") or user.get("role", UserRole.GUEST))
    if not user.get("effective_role") and _elevation_active(user):
        elev = user.get("temp_elevation", {})
        role = normalize_user_role(elev.get("role", UserRole.EC_MEMBER))

    # Unapproved users have limited permissions (elevated users bypass this)
    if not user.get("is_approved", False) and not _elevation_active(user):
        if role not in [UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN, UserRole.EC_MEMBER, UserRole.ADMIN_STAFF]:
            return DEFAULT_PERMISSIONS[UserRole.GUEST]

    base_permissions = DEFAULT_PERMISSIONS.get(role, DEFAULT_PERMISSIONS[UserRole.GUEST])

    # Apply custom permissions if any
    custom = user.get("custom_permissions", {})
    if custom:
        perm_dict = base_permissions.model_dump()
        perm_dict.update(custom)
        return Permission(**perm_dict)

    return base_permissions


def user_to_response(user: dict, viewer: dict = None) -> UserResponse:
    """
    Convert a user dictionary from database to a UserResponse model.

    Args:
        user:   User dictionary from database.
        viewer: The authenticated user making the request.  When provided,
                PII fields (email, phone, address) are masked for all viewers
                who are NOT super_admin and are NOT viewing their own record.

    Returns:
        UserResponse model with permissions
    """
    permissions = get_user_permissions(user)

    # Check if this is an impersonated session
    is_impersonated = "impersonator_id" in user

    email = user["email"]
    # Single source of truth for display name: prefer full_name when non-blank,
    # fall back to "first_name last_name" composition.  This guards against legacy
    # records (and new registrations) that set first/last but not full_name.
    _raw_full = (user.get("full_name") or "").strip()
    if not _raw_full:
        _first = (user.get("first_name") or "").strip()
        _last = (user.get("last_name") or "").strip()
        _raw_full = f"{_first} {_last}".strip()
    full_name = _raw_full
    phone = user.get("phone")
    phone_home = user.get("phone_home")
    phone_mobile = user.get("phone_mobile")
    phone_business = user.get("phone_business")
    home_address = user.get("home_address")
    home_suburb = user.get("home_suburb")
    home_state = user.get("home_state")
    home_postcode = user.get("home_postcode")
    postal_address = user.get("postal_address")
    postal_suburb = user.get("postal_suburb")
    postal_state = user.get("postal_state")
    postal_postcode = user.get("postal_postcode")
    last_login_ip = user.get("last_login_ip")
    # An IP is personal information under the APPs, so the new pair follows the
    # SAME redaction path as last_login_ip. Adding a field here without adding it
    # to both redaction branches below would leak it to impersonating admins and
    # to non-super-admin viewers of another user's record.
    last_login_public_ip = user.get("last_login_public_ip")
    last_login_local_ip = user.get("last_login_local_ip")
    mail_username = user.get("mail_username")
    primary_email = user.get("primary_email")
    secondary_email = user.get("secondary_email")
    co_owner_email = user.get("co_owner_email")

    if is_impersonated:
        email = mask_email(email)
        if mail_username:
            mail_username = mask_email(mail_username)
        if primary_email:
            primary_email = mask_email(primary_email)
        if secondary_email:
            secondary_email = mask_email(secondary_email)
        if co_owner_email:
            co_owner_email = mask_email(co_owner_email)

        full_name = "Resident"
        phone = mask_phone(phone)
        phone_home = mask_phone(phone_home)
        phone_mobile = mask_phone(phone_mobile)
        phone_business = mask_phone(phone_business)

        # Mask addresses and location PII
        if home_address: home_address = "REDACTED"
        if home_suburb: home_suburb = "REDACTED"
        if home_state: home_state = "REDACTED"
        if home_postcode: home_postcode = "REDACTED"
        if postal_address: postal_address = "REDACTED"
        if postal_suburb: postal_suburb = "REDACTED"
        if postal_state: postal_state = "REDACTED"
        if postal_postcode: postal_postcode = "REDACTED"

        # Mask IPs
        if last_login_ip: last_login_ip = "REDACTED"
        if last_login_public_ip: last_login_public_ip = "REDACTED"
        if last_login_local_ip: last_login_local_ip = "REDACTED"

    elif viewer is not None:
        # PII masking for non-super_admin viewers looking at other users' records.
        # Super admins always see full PII.  Users always see their own full data.
        viewer_role = viewer.get("role", "")
        viewing_own = viewer.get("id") == user.get("id")
        if viewer_role != UserRole.SUPER_ADMIN and not viewing_own:
            email = mask_email(email)
            if mail_username:
                mail_username = mask_email(mail_username)
            if primary_email:
                primary_email = mask_email(primary_email)
            if secondary_email:
                secondary_email = mask_email(secondary_email)
            if co_owner_email:
                co_owner_email = mask_email(co_owner_email)
            phone = mask_phone(phone)
            phone_home = mask_phone(phone_home)
            phone_mobile = mask_phone(phone_mobile)
            phone_business = mask_phone(phone_business)
            if home_address: home_address = "REDACTED"
            if home_suburb: home_suburb = "REDACTED"
            if home_state: home_state = "REDACTED"
            if home_postcode: home_postcode = "REDACTED"
            if postal_address: postal_address = "REDACTED"
            if postal_suburb: postal_suburb = "REDACTED"
            if postal_state: postal_state = "REDACTED"
            if postal_postcode: postal_postcode = "REDACTED"
            if last_login_ip: last_login_ip = "REDACTED"
            if last_login_public_ip: last_login_public_ip = "REDACTED"
            if last_login_local_ip: last_login_local_ip = "REDACTED"

    return UserResponse(
        id=user["id"],
        email=email,
        full_name=full_name,
        unit_number=user.get("unit_number"),
        phone=phone,
        phone_home=phone_home,
        phone_mobile=phone_mobile,
        phone_business=phone_business,
        home_address=home_address,
        home_suburb=home_suburb,
        home_state=home_state,
        home_postcode=home_postcode,
        postal_same_as_home=user.get("postal_same_as_home", True),
        postal_address=postal_address,
        postal_suburb=postal_suburb,
        postal_state=postal_state,
        postal_postcode=postal_postcode,
        is_managing_agent=user.get("is_managing_agent", False),
        is_tenanted=user.get("is_tenanted", False),
        general_correspondence_email=user.get("general_correspondence_email", True),
        general_correspondence_post=user.get("general_correspondence_post", False),
        levy_notices_email=user.get("levy_notices_email", True),
        levy_notices_post=user.get("levy_notices_post", False),
        meeting_notices_email=user.get("meeting_notices_email", True),
        meeting_notices_post=user.get("meeting_notices_post", False),
        role=normalize_user_role(user.get("effective_role") or user["role"]),
        ec_position=user.get("ec_position"),
        temp_elevation=user.get("temp_elevation"),
        is_elevated=_elevation_active(user),
        is_active=user.get("is_active", True),
        is_approved=user.get("is_approved", False),
        status=user.get("status", "active"),
        info_request_reason=user.get("info_request_reason"),
        info_requested_at=user.get("info_requested_at"),
        archived_at=user.get("archived_at"),
        archived_by=user.get("archived_by"),
        archived_reason=user.get("archived_reason"),
        profile_image=user.get("profile_image"),
        mail_username=mail_username,
        mail_password="••••••••" if user.get("mail_password") else None,
        permissions=permissions,
        created_at=user["created_at"].isoformat() if isinstance(user["created_at"], datetime) else user["created_at"],
        last_login_at=user.get("last_login_at").isoformat() if isinstance(user.get("last_login_at"), datetime) else user.get("last_login_at"),
        last_login_ip=last_login_ip,
        last_login_public_ip=last_login_public_ip,
        last_login_local_ip=last_login_local_ip,
        is_name_flagged=user.get("is_name_flagged", False),
        flag_reason=user.get("flag_reason"),
        unit_owner_name=user.get("unit_owner_name"),
        co_owner_name=user.get("co_owner_name"),
        co_owner_email=co_owner_email,
        primary_email=primary_email,
        secondary_email=secondary_email,
        owned_units=user.get("owned_units", []),
    )


def require_permission(permission_name: str, error_message: str = "Not authorized") -> Callable:
    """
    Create a FastAPI dependency that checks if user has a specific permission.

    This decorator reduces the 95+ repeated permission check blocks throughout
    the codebase, centralizing permission validation logic.

    Args:
        permission_name: Name of the permission to check (e.g., "can_view_finances")
        error_message: Custom error message for permission denied (default: "Not authorized")

    Returns:
        Callable: FastAPI dependency function that validates permission and returns the user

    Usage:
        from utils.permissions import require_permission
        from fastapi import Depends

        @router.get("/protected")
        async def protected_route(current_user: dict = Depends(require_permission("can_view_finances"))):
            # User is guaranteed to have can_view_finances permission
            # The current_user is passed through from the dependency
            ...

    Example - Old way (repeated 95+ times):
        permissions = get_user_permissions(current_user)
        if not permissions.can_view_finances:
            raise HTTPException(status_code=403, detail="Not authorized")

    Example - New way (reusable):
        @router.get("/data")
        async def get_data(current_user: dict = Depends(require_permission("can_view_finances"))):
            # Permission already checked, proceed with logic
            ...
    """
    from utils.auth import get_current_user

    async def permission_checker(current_user: dict = Depends(get_current_user)):
        """Generated function header.

        Function: permission_checker
        Path: backend/utils/permissions.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        permissions = get_user_permissions(current_user)
        if not getattr(permissions, permission_name, False):
            raise HTTPException(status_code=403, detail=error_message)
        return current_user

    return permission_checker


async def get_effective_feature_access(user: dict, feature_key: str) -> bool:
    """
    Determine if a user has effective access to a specific feature.

    Resolution order (first match wins):
    1. Super Admins always have access (effective_role covers elevation).
    2. Temp-elevated users follow site-wide only (user overrides bypassed).
    3. Explicit user-level override takes precedence.
    4. Fall back to building/global site-wide default.

    Debug: set FEATURE_TOGGLE_DEBUG=true to log resolution steps without PII.
    Mirror: routers/feature_toggles.py:_compute_effective_access — keep in sync.
    """
    from db_postgres.repos import config_repo
    from utils.auth import effective_role

    # 1. Super Admins always have access (effective_role covers any elevation)
    if effective_role(user) == UserRole.SUPER_ADMIN:
        if _TOGGLE_DEBUG:
            logger.debug("feature_toggle resolve: key=%s result=True source=super_admin", feature_key)
        return True

    building_id = user.get("building_id") or user.get("building")
    site_wide = await config_repo.resolve_feature_toggle(building_id, feature_key, default=True)

    permission_overrides = user.get("permission_overrides") or {}
    feature_overrides = config_repo.extract_feature_access_entries(permission_overrides)
    override_entry = feature_overrides.get(feature_key)
    override_value = override_entry.get("is_enabled") if override_entry else None

    # 2. Elevated users follow site-wide only (bypassing overrides)
    if _elevation_active(user):
        if _TOGGLE_DEBUG:
            logger.debug(
                "feature_toggle resolve: key=%s result=%s source=site_wide(elevated)",
                feature_key, site_wide,
            )
        return site_wide

    # 3. Individual overrides take precedence
    if override_value is not None:
        if _TOGGLE_DEBUG:
            logger.debug(
                "feature_toggle resolve: key=%s result=%s source=user_override",
                feature_key, override_value,
            )
        return bool(override_value)

    # 4. Fallback to site-wide
    if _TOGGLE_DEBUG:
        logger.debug(
            "feature_toggle resolve: key=%s result=%s source=site_wide",
            feature_key, site_wide,
        )
    return site_wide


def require_feature(feature_key: str) -> Callable:
    """
    FastAPI dependency that ensures a feature is enabled for the current user.
    Uses get_current_user to support all roles including service_provider.
    For community/resident-only features, use require_approved_feature instead.
    """
    from utils.auth import get_current_user

    async def feature_checker(current_user: dict = Depends(get_current_user)):
        """Generated function header.

        Function: feature_checker
        Path: backend/utils/permissions.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if not await get_effective_feature_access(current_user, feature_key):
            raise HTTPException(
                status_code=403,
                # The global exception handler preserves this typed code so the
                # frontend can show a feature-disabled recovery state, not a
                # generic forbidden or not-found page.
                detail={
                    "code": "FEATURE_DISABLED",
                    "message": "This feature is not available for your building yet.",
                    "feature_key": feature_key,
                    "retryable": False,
                },
            )
        return current_user

    return feature_checker


def require_approved_feature(feature_key: str) -> Callable:
    """
    FastAPI dependency that ensures a feature is enabled AND the user is an
    approved/vetted resident or administrator.
    Use this for community-only features that must not be accessible to
    unapproved accounts, guests, or service providers.
    """
    from utils.auth import get_approved_user

    async def feature_checker(current_user: dict = Depends(get_approved_user)):
        """Generated function header.

        Function: feature_checker
        Path: backend/utils/permissions.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if not await get_effective_feature_access(current_user, feature_key):
            raise HTTPException(
                status_code=403,
                # The global exception handler preserves this typed code so the
                # frontend can show a feature-disabled recovery state, not a
                # generic forbidden or not-found page.
                detail={
                    "code": "FEATURE_DISABLED",
                    "message": "This feature is not available for your building yet.",
                    "feature_key": feature_key,
                    "retryable": False,
                },
            )
        return current_user

    return feature_checker


def require_role(allowed_roles) -> Callable:
    """
    Create a FastAPI dependency that checks if user has one of the allowed roles.

    Args:
        allowed_roles: Single role string or list of allowed role strings

    Returns:
        Callable: FastAPI dependency function that validates role and returns the user

    Usage:
        @router.post("/admin-only")
        async def admin_route(current_user: dict = Depends(require_role(UserRole.SUPER_ADMIN))):
            # User is guaranteed to be super_admin
            ...

        @router.post("/ec-or-admin")
        async def ec_route(current_user: dict = Depends(require_role([UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN]))):
            # User is guaranteed to be super_admin or strata_admin
            ...
    """
    from utils.auth import get_current_user, effective_role

    # Normalize to list
    if isinstance(allowed_roles, str):
        allowed_roles = [allowed_roles]

    async def role_checker(current_user: dict = Depends(get_current_user)):
        # Honour temp_elevation: an owner temporarily elevated to ec_member must
        # pass a require_role(EC_MEMBER) guard. Reading raw user["role"] was a
        # silent 403 for every elevated user across 9 feature_toggle admin
        # endpoints. See docs/fixes/platform_gaps_audit.md §F-001.
        """Generated function header.

        Function: role_checker
        Path: backend/utils/permissions.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        user_role = effective_role(current_user)
        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Required role: {', '.join(allowed_roles)}"
            )
        return current_user

    return role_checker


def require_permission_v2(permission_slug: str, building_id_param: str = None,
                          error_message: str = "Not authorized") -> Callable:
    """
    New slug-based permission dependency for the RBAC+ABAC system.

    Checks the new `user_roles` / `role_permissions` collections via `permission_service.user_can()`.
    Falls back gracefully to legacy boolean permission check if the new system has no data.

    Args:
        permission_slug: New-style permission slug (e.g. "financial.view", "committee.vote")
        building_id_param: Name of path/query param that carries building_id (optional)
        error_message: Custom 403 message

    Usage:
        @router.get("/finances")
        async def get_finances(
            current_user: dict = Depends(require_permission_v2("financial.view"))
        ):
            ...
    """
    from utils.auth import get_current_user, effective_role
    from fastapi import Request

    async def permission_checker(
            request: Request,
            current_user: dict = Depends(get_current_user),
    ):
        # SUPER_ADMIN always bypassed (effective_role handles temp_elevation)
        """Generated function header.

        Function: permission_checker
        Path: backend/utils/permissions.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if effective_role(current_user) == UserRole.SUPER_ADMIN:
            return current_user

        # Determine building_id from path/query params or user context
        building_id = None
        if building_id_param:
            building_id = request.path_params.get(building_id_param) or request.query_params.get(building_id_param)
        if not building_id:
            building_id = current_user.get("building_id")

        # Try new permission service first
        try:
            from services.permission_service import user_can
            result = await user_can(
                user_id=current_user["id"],
                permission_slug=permission_slug,
                building_id=building_id,
            )
            if result.allowed:
                return current_user
            # New system explicitly denied — honour it
            raise HTTPException(status_code=403, detail=error_message)
        except ImportError:
            pass
        except HTTPException:
            raise
        except Exception:
            pass

        # Fallback: map slug to legacy boolean permission
        try:
            from models.rbac_models import PERMISSION_SLUGS
            from services.permission_service import legacy_to_slug_permissions
            permissions = get_user_permissions(current_user)
            legacy_slugs = legacy_to_slug_permissions(permissions.model_dump())
            if permission_slug in legacy_slugs:
                return current_user
        except Exception:
            pass

        raise HTTPException(status_code=403, detail=error_message)

    return permission_checker


__all__ = [
    'get_user_permissions',
    'user_to_response',
    'require_permission',
    'require_permission_v2',
    'require_role',
    'require_feature',
    'require_approved_feature',
    'get_effective_feature_access',
]
