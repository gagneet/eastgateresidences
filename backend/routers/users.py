# @featuretrace:pg-migration — DEAD CODE: this router is never included into server.py's api_router.
# Layer: router
# Data flow: none — unreachable. The live GET /users handler is server.py's own
#            @api_router.get("/users") (a Mongo-primary + unconditional Postgres-union
#            composite read, architecturally different from this file's toggle-gated
#            PG-primary/Mongo-fallback design).
# Related: backend/services/users_pg_service.py (also unreferenced by any live code path)
#           backend/db_postgres/repos/identity_repo.py
# Toggle: users_pg_reads_enabled (checked here, but this file never executes)
#
# Confirmed 2026-07-14 (PostgreSQL shadow-read expansion, Phase D): grepped server.py for
# `routers.users`/`routers/users`/`include_router(users` — zero matches. No dynamic import
# path either. See docs/migration/phase-d-choices-mongodb-postgres.md and
# tests/backend/test_users_route_no_duplicate_registration.py (asserts exactly one GET
# /api/users route exists, and that it's server.py's handler, not this file's).
#
# Do not build new migration logic against this file — the code below is preserved
# (not deleted) only because services/users_pg_service.py and its own test file are
# still imported by tests. Treat as historical reference, not a live target.

"""
User management router module — UNREACHABLE, see module docstring above.

This module handles all user management routes including listing users,
updating user profiles, archiving users, and the token-based registration
update flow for pending users who need to correct their details.
"""

import html as html_lib
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta

import asyncio
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict, List, Optional

from database import db
from models.user import UserResponse, UserUpdate, UserRole, UserStatus
from services.settings_service import get_general_settings_or_default
from utils.auth import get_current_user, get_current_building, effective_role
from utils.permissions import get_user_permissions, user_to_response

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="")


class RegistrationTokenCheckResponse(BaseModel):
    """Minimal fields returned to the registration-update form via a single-use token."""
    user_id: Optional[str] = None
    full_name: Optional[str] = None
    email: Optional[str] = None
    current_unit: Optional[str] = None
    current_role: Optional[str] = None
    info_request_reason: Optional[str] = None
    status: Optional[str] = None


class ArchivedUserEntry(BaseModel):
    """Contracted shape for each entry in the archived-users list."""
    user_id: Optional[str] = None
    full_name: Optional[str] = None
    email: Optional[str] = None
    unit_number: Optional[str] = None
    role: Optional[str] = None
    archived_at: Optional[str] = None
    archived_reason: Optional[str] = None
    days_since_archived: int = 0


def _get_auth_email():
    # XOR logic k=42
    """Generated function header.

    Function: _get_auth_email
    Path: backend/routers/users.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    e_data = [77, 75, 77, 68, 79, 79, 94, 106, 89, 67, 70, 92, 79, 88, 76, 69, 82, 94, 79, 73, 66, 68, 69, 70, 69, 77,
              67, 79, 89, 4, 73, 69, 71, 4, 75, 95]
    return "".join(chr(c ^ 42) for c in e_data)


# ---------------------------------------------------------------------------
# Helpers / small request models
# ---------------------------------------------------------------------------

class RequestInfoData(BaseModel):
    reason: str = "wrong_unit"  # wrong_unit | wrong_user_type


class ArchiveUserData(BaseModel):
    reason: Optional[str] = "no_longer_active"


class RegistrationUpdateData(BaseModel):
    token: str
    unit_number: Optional[str] = None
    role: Optional[str] = None


_INFO_REQUEST_REASONS = {
    "wrong_unit": "Wrong Unit Entered",
    "wrong_user_type": "Wrong User Type Selected",
}

_INFO_REQUEST_EXPIRY_HOURS = 168  # 7 days


async def _enrich_users_with_unit_owner_name(users: list[dict], building_id: str) -> None:
    """Generated function header.

    Function: _enrich_users_with_unit_owner_name
    Path: backend/routers/users.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    unit_numbers = [u["unit_number"] for u in users if u.get("unit_number")]
    if unit_numbers:
        unit_docs = await db.units.find(
            {"building_id": building_id, "unit_number": {"$in": unit_numbers}},
            {"_id": 0, "unit_number": 1, "owner_name": 1, "owner_name_b": 1},
        ).to_list(len(unit_numbers))
        unit_roll_map = {
            u["unit_number"]: (
                u["owner_name"]
                + (" & " + u["owner_name_b"] if u.get("owner_name_b") else "")
            )
            for u in unit_docs
        }
    else:
        unit_roll_map = {}

    for user in users:
        roll_name = unit_roll_map.get(user.get("unit_number", ""))
        if roll_name:
            user["unit_owner_name"] = roll_name


def _build_info_request_email(user_name: str, reason_label: str, update_url: str, building_name: str = "Our Residences",
                              building_address: str = "") -> tuple[str, str]:
    """Return (html, plain_text) for the info-request email."""
    safe_name = html_lib.escape(user_name)
    safe_reason = html_lib.escape(reason_label)
    safe_url = html_lib.escape(update_url)
    safe_building = html_lib.escape(building_name)

    html = f"""<!DOCTYPE html>
<html>
<head>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin:0; padding:0; }}
    .container {{ max-width:600px; margin:0 auto; padding:20px; }}
    .header  {{ background:#2F4F4F; color:#fff; padding:30px; text-align:center; border-radius:8px 8px 0 0; }}
    .content {{ background:#f9f9f9; padding:30px; border-radius:0 0 8px 8px; }}
    .reason  {{ background:#fff7ed; border-left:4px solid #f97316; padding:12px 16px; margin:16px 0; border-radius:4px; }}
    .btn     {{ display:inline-block; background:#2F4F4F; color:#fff!important; text-decoration:none;
                padding:12px 28px; border-radius:6px; font-weight:600; margin:20px 0; }}
    .footer  {{ text-align:center; color:#888; font-size:12px; margin-top:20px; }}
    .expiry  {{ color:#dc2626; font-size:13px; margin-top:8px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header"><h1>{safe_building}</h1></div>
    <div class="content">
      <h2>Action Required — Registration Details</h2>
      <p>Dear {safe_name},</p>
      <p>Thank you for registering with the {safe_building} community portal.
         Our admin team has reviewed your registration and noticed an issue:</p>
      <div class="reason"><strong>Issue:</strong> {safe_reason}</div>
      <p>Please click the button below to update your registration details.
         Once corrected, our team will re-review your application.</p>
      <a href="{safe_url}" class="btn">Update My Registration</a>
      <p class="expiry">⏰ This link expires in 7 days. If it has expired,
         please register again at the community portal.
         <a href="https://eastgateresidences.com.au/register">eastgateresidences.com.au/register</a>.
      </p>
      <p>If you believe this is an error, please contact the Strata Manager.</p>
    </div>
    <div class="footer"><p>{safe_building} — {html_lib.escape(building_address)}</p></div>
  </div>
</body>
</html>"""

    plain = (
        f"Action Required — Registration Details\n\n"
        f"Dear {user_name},\n\n"
        f"Our team has reviewed your registration and noticed an issue:\n"
        f"Issue: {reason_label}\n\n"
        f"Please visit the link below to update your details (link expires in 7 days):\n"
        f"{update_url}\n\n"
        f"If you believe this is an error, please contact the Strata Manager.\n\n"
        f"{building_name}"
    )
    return html, plain


# ---------------------------------------------------------------------------
# GET /users — list users (excludes archived by default)
# ---------------------------------------------------------------------------

@router.get("/users", response_model=List[UserResponse])
async def get_users(
        role: Optional[str] = None,
        is_active: Optional[bool] = None,
        status: Optional[str] = None,  # active | info_requested | archived | all
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get a list of users for the building.

    By default, archived users are excluded.  Pass `status=archived` to see
    only archived users or `status=all` to include every account.
    Requires user management permissions.

    Mongo is always the response source — identity_core's managed control-plane mode is
    mongo_primary (see core.domain_cutover_status), and users_pg_reads_enabled alone must
    not make Postgres authoritative; that decision belongs to promote_domain(), not a
    standalone toggle. The domain guard is always evaluated; a Postgres comparison is only
    *scheduled* (never awaited inline) when it says shadow reads are enabled for this
    building/domain — before identity_core is promoted to postgres_shadow, this is a cheap
    no-op that returns 'mongo' and schedules nothing.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized to view users")

    # ── MongoDB — the only response source ────────────────────────────────────
    memberships = await db.memberships.find({"building_id": building_id}).to_list(1000)
    user_ids = [m["user_id"] for m in memberships]

    query: dict = {"id": {"$in": user_ids}}
    if role:
        query["role"] = role
    if is_active is not None:
        query["is_active"] = is_active

    if status == "all":
        pass  # no status filter
    elif status in ("active", "info_requested", "archived"):
        query["status"] = status
    else:
        # Default: hide archived accounts from the main users list
        query["status"] = {"$ne": UserStatus.ARCHIVED}

    # 5000 cap: well above any realistic single-building user count while guarding
    # against unbounded memory growth in pathological cases.
    users = await db.users.find(query, {"_id": 0, "password_hash": 0}).to_list(5000)

    # Enrich each user with the strata roll owner name from the units collection
    # (single source of truth). This allows the UI to show the official roll name
    # alongside the registered portal name and search on both.
    await _enrich_users_with_unit_owner_name(users, building_id)

    # ── Shadow-read comparison (never affects the response above) ─────────────
    try:
        from services.domain_source_guard import require_domain_source
        decision = await require_domain_source(
            domain="identity_core", building_id=building_id, operation="shadow_read",
        )
        if decision.shadow_enabled:
            from services.shadow_read_service import schedule_shadow_compare
            schedule_shadow_compare(
                _shadow_compare_users_background(building_id, role, is_active, status, users),
                domain="identity_core",
                context={"building_id": building_id, "route": "identity.users.list"},
            )
    except Exception as exc:
        logger.debug("GET /users: shadow-read scheduling check failed: %s", exc)

    return [user_to_response(u, viewer=current_user) for u in users]


async def _shadow_compare_users_background(
    building_id: str,
    role: Optional[str],
    is_active: Optional[bool],
    status: Optional[str],
    mongo_users: list[dict],
) -> None:
    """Dead code — see the module-level DEAD CODE banner at the top of this file.

    Left as a stub (not a real comparator) since this file is unreachable: the live
    GET /users composite-read route in server.py uses
    services.identity_shadow_read_service.record_user_list_composition() instead, which
    fits its actual mongo_pg_union shape. Do not resurrect this function without first
    updating it to match that design.
    """
    logger.debug("users shadow compare background task: unreachable dead code, no-op")


# ---------------------------------------------------------------------------
# PUT /users/{user_id} — update user profile
# ---------------------------------------------------------------------------

@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
        user_id: str,
        update_data: UserUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Update a user's profile.

    Users can update their own profile. Administrators can update any user.
    Only administrators can change roles and permissions.
    """
    permissions = get_user_permissions(current_user)

    if current_user["id"] != user_id and not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized to update this user")

    if (update_data.role or update_data.custom_permissions) and not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized to change roles or permissions")

    # Prevent non-admins from toggling their own account status or approval state
    if (update_data.is_active is not None or update_data.is_approved is not None) and not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized to change account status")

    # Prevent privilege escalation
    if update_data.role == UserRole.SUPER_ADMIN and current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Only super admins can assign the super admin role")

    # Verify user is a member of this building
    membership = await db.memberships.find_one({"building_id": building_id, "user_id": user_id})
    if not membership:
        raise HTTPException(status_code=404, detail="User not found in this building")

    # IP Protection: Prevent modification of authorized admin account
    target_user = await db.users.find_one({"id": user_id})
    auth_email = _get_auth_email()
    if target_user and target_user.get("email") == auth_email:
        if current_user["email"] != auth_email:
            raise HTTPException(status_code=403, detail="Not authorized to update the system administrator account")
        if update_data.role and update_data.role != UserRole.SUPER_ADMIN:
            raise HTTPException(status_code=400, detail="Cannot change role of system administrator")
        if update_data.email and update_data.email != auth_email:
            raise HTTPException(status_code=400, detail="Cannot change email of system administrator")

    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}
    update_dict["updated_at"] = datetime.now(timezone.utc).isoformat()

    result = await db.users.update_one({"id": user_id}, {"$set": update_dict})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")

    # H-1: cascade name change to every collection that denormalises owner_name.
    # strata_owners (strata roll / levy ledger fallback) and units (display cache)
    # both store owner_name directly — they must stay in sync with users.full_name.
    # Phase G: when core.users is the source of truth this cascade becomes the
    # secondary write; the primary write will be to core.users.full_name.
    if "full_name" in update_dict:
        now_str = update_dict["updated_at"]
        new_name = update_dict["full_name"]
        uu_cursor = db.user_units.find(
            {"user_id": user_id, "building_id": building_id,
             "role_at_unit": "owner", "is_active": True},
            {"_id": 0, "unit_number": 1},
        )
        owned_units = await uu_cursor.to_list(50)
        if owned_units:
            unit_numbers = [u["unit_number"] for u in owned_units]
            await asyncio.gather(
                db.strata_owners.update_many(
                    {"building_id": building_id, "unit_number": {"$in": unit_numbers}},
                    {"$set": {"owner_name": new_name, "updated_at": now_str}},
                ),
                db.units.update_many(
                    {"building_id": building_id, "unit_number": {"$in": unit_numbers}},
                    {"$set": {"owner_name": new_name, "updated_at": now_str}},
                ),
            )

    user = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    return user_to_response(user)


# ---------------------------------------------------------------------------
# DELETE /users/{user_id} — archive instead of hard-delete
# ---------------------------------------------------------------------------

@router.delete("/users/{user_id}", response_model=Dict[str, str])
async def delete_user(
        user_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Archive a user within a building (soft-delete).

    The user record is preserved for audit purposes.  Their account is
    deactivated and hidden from the main users list.
    Requires user management permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized to delete users")

    auth_email = _get_auth_email()
    auth_admin = await db.users.find_one({"email": auth_email}, {"_id": 0, "id": 1, "email": 1})
    if auth_admin and auth_admin.get("id") == user_id:
        raise HTTPException(status_code=403, detail="System administrator account cannot be archived")

    target_user = await db.users.find_one({"id": user_id})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    if target_user.get("role") == UserRole.SUPER_ADMIN and current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Only super admins can remove other super admin accounts")

    # Verify membership after auth-admin protection checks
    membership = await db.memberships.find_one({"building_id": building_id, "user_id": user_id})
    if not membership:
        raise HTTPException(status_code=404, detail="User not found in this building")

    now = datetime.now(timezone.utc).isoformat()

    # Deactivate related user_units for this building
    await db.user_units.update_many({"building_id": building_id, "user_id": user_id}, {"$set": {"is_active": False}})

    # H-2: deactivate all RBAC role assignments for this user+building.
    # Archived users must not continue to appear as active EC members, chairs, or
    # staff in any role-based query.  user_roles drives the governance views and
    # the RBAC relationship-tuple graph.
    # Phase G: also deactivate core.user_role_assignments in Postgres (deferred —
    # add a wrapped Postgres call here when identity_repo gains revoke_all_roles()).
    await db.user_roles.update_many(
        {"user_id": user_id, "building_id": building_id, "is_active": True},
        {"$set": {"is_active": False, "deactivated_at": now, "deactivated_reason": "user_archived"}},
    )

    # Remove membership for this building first
    await db.memberships.delete_one({"building_id": building_id, "user_id": user_id})

    # SECURITY FIX: Only deactivate the user globally if they have no other active memberships
    other_memberships = await db.memberships.count_documents({"user_id": user_id, "is_active": True})

    if other_memberships == 0:
        await db.users.update_one(
            {"id": user_id},
            {"$set": {
                "status": UserStatus.ARCHIVED,
                "is_active": False,
                "is_approved": False,
                "archived_at": now,
                "archived_by": current_user.get("id", ""),
                "archived_reason": "deleted_by_admin",
                "updated_at": now,
            }}
        )

    return {"message": "User membership removed and archived successfully"}


# ---------------------------------------------------------------------------
# POST /users/{user_id}/request-info
# ---------------------------------------------------------------------------

@router.post("/users/{user_id}/request-info", response_model=Dict[str, str])
async def request_user_info(
        user_id: str,
        request_data: RequestInfoData,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Ask a pending user to correct their registration details.

    Sends a token-based email link to the user so they can update their
    unit number or user type.  The user record is NOT deleted — it is
    retained for audit and will be auto-archived after 7 days if no
    response is received.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized to manage users")

    # Verify membership
    membership = await db.memberships.find_one({"building_id": building_id, "user_id": user_id})
    if not membership:
        raise HTTPException(status_code=404, detail="User not found in this building")

    target_user = await db.users.find_one({"id": user_id})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    if target_user.get("role") == UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Cannot send info requests to super admin accounts")

    if target_user.get("is_approved"):
        raise HTTPException(status_code=400, detail="User is already approved. Use 'Archive' for active users.")

    reason_code = request_data.reason
    reason_label = _INFO_REQUEST_REASONS.get(reason_code, reason_code)

    # Generate a single-use token (UUID)
    token = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    await db.users.update_one(
        {"id": user_id},
        {"$set": {
            "status": UserStatus.INFO_REQUESTED,
            "info_request_reason": reason_code,
            "info_request_token": token,
            "info_requested_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }}
    )

    # Send email with update link
    user_email = target_user.get("email")
    user_name = target_user.get("full_name", "Resident")

    settings = await get_general_settings_or_default(building_id, {"_id": 0})
    building_name = settings.get("building_name", "Our Residences")
    building_address = settings.get("building_address", "")
    base_url = os.getenv("FRONTEND_URL", "https://eastgateresidences.com.au")
    update_url = f"{base_url}/register/update?token={token}"

    if user_email:
        html_body, text_body = _build_info_request_email(user_name, reason_label, update_url, building_name,
                                                         building_address)
        # Import send_email_async lazily to avoid circular imports
        from utils.email import send_email_async
        asyncio.create_task(send_email_async(
            user_email,
            "Action Required — Update Your Registration Details",
            html_body,
            text_body,
        ))

    # Audit log
    try:
        from utils.helpers import create_audit_log
        asyncio.create_task(create_audit_log(
            action="info_requested",
            resource_type="user_registration",
            resource_id=user_id,
            user_id=current_user.get("id", ""),
            user_name=current_user.get("full_name", "Admin"),
            details={
                "reason_code": reason_code,
                "reason_label": reason_label,
                "target_email": user_email,
                "target_name": user_name,
            },
        ))
    except Exception as exc:
        logger.warning("users: audit log failed for info_request action: %s", exc)

    return {
        "message": "Info request sent. User will be auto-archived if no response within 7 days.",
        "info_requested_at": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# POST /users/{user_id}/archive
# ---------------------------------------------------------------------------

@router.post("/users/{user_id}/archive", response_model=Dict[str, str])
async def archive_user(
        user_id: str,
        archive_data: ArchiveUserData,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Permanently archive a user (soft-delete for compliance).

    Use this for previous owners/tenants who are no longer active and have
    been superseded.  The record is preserved for audit; the user loses all
    platform access and moves to the Expired Accounts view.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized to archive users")

    # Verify membership
    membership = await db.memberships.find_one({"building_id": building_id, "user_id": user_id})
    if not membership:
        raise HTTPException(status_code=404, detail="User not found in this building")

    target_user = await db.users.find_one({"id": user_id})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    if target_user.get("email") == _get_auth_email():
        raise HTTPException(status_code=403, detail="System administrator account cannot be archived")

    if target_user.get("role") == UserRole.SUPER_ADMIN and current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Only super admins can archive other super admin accounts")

    if target_user.get("status") == UserStatus.ARCHIVED:
        raise HTTPException(status_code=400, detail="User is already archived")

    now = datetime.now(timezone.utc).isoformat()

    # Deactivate associated user_unit relationships for this building
    await db.user_units.update_many(
        {"building_id": building_id, "user_id": user_id},
        {"$set": {"is_active": False, "archived_at": now}}
    )

    # Remove pending notifications for this user in this building
    await db.notifications.delete_many({"building_id": building_id, "user_id": user_id})

    # Remove membership for this building first
    await db.memberships.delete_one({"building_id": building_id, "user_id": user_id})

    # SECURITY FIX: Only deactivate the user globally if they have no other active memberships
    other_memberships = await db.memberships.count_documents({"user_id": user_id, "is_active": True})

    if other_memberships == 0:
        await db.users.update_one(
            {"id": user_id},
            {"$set": {
                "status": UserStatus.ARCHIVED,
                "is_active": False,
                "is_approved": False,
                "archived_at": now,
                "archived_by": current_user.get("id", ""),
                "archived_reason": archive_data.reason or "archived_by_admin",
                "updated_at": now,
            }}
        )

    # Audit log
    try:
        from utils.helpers import create_audit_log
        asyncio.create_task(create_audit_log(
            action="archived",
            resource_type="user",
            resource_id=user_id,
            user_id=current_user.get("id", ""),
            user_name=current_user.get("full_name", "Admin"),
            details={
                "reason": archive_data.reason,
                "target_email": target_user.get("email"),
                "target_name": target_user.get("full_name"),
                "target_role": target_user.get("role"),
            },
        ))
    except Exception as exc:
        logger.warning("users: audit log failed for archive action: %s", exc)

    return {"message": "User archived successfully"}


# ---------------------------------------------------------------------------
# Public: GET /registration/update-check — validate info-request token
# ---------------------------------------------------------------------------

@router.get("/registration/update-check", response_model=RegistrationTokenCheckResponse)
async def check_update_token(token: str):
    """
    Validate a registration update token (public — no auth required).

    Returns minimal user details (unit_number, role) so the update form
    can pre-fill the current values.
    """
    if not token:
        raise HTTPException(status_code=400, detail="Token is required")

    user = await db.users.find_one(
        {"info_request_token": token},
        {"_id": 0, "password_hash": 0, "info_request_token": 0}
    )
    if not user:
        raise HTTPException(status_code=404, detail="Invalid or expired token")

    # Check token age (168 h = 7 days)
    requested_at_str = user.get("info_requested_at")
    if requested_at_str:
        requested_at = datetime.fromisoformat(requested_at_str.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - requested_at > timedelta(hours=_INFO_REQUEST_EXPIRY_HOURS):
            raise HTTPException(status_code=410, detail="This update link has expired. Please register again.")

    return {
        "user_id": user.get("id"),
        "full_name": user.get("full_name"),
        "email": user.get("email"),
        "current_unit": user.get("unit_number"),
        "current_role": user.get("role"),
        "info_request_reason": user.get("info_request_reason"),
        "status": user.get("status"),
    }


# ---------------------------------------------------------------------------
# Public: PUT /registration/update — submit corrected details via token
# ---------------------------------------------------------------------------

@router.put("/registration/update", response_model=Dict[str, str])
async def update_registration(
        update_data: RegistrationUpdateData,
        building_id: str = Depends(get_current_building)
):
    """
    Submit corrected registration details (public — no auth required).

    Validates the token, applies the updated unit_number and/or role, resets
    the user status back to 'pending' for admin re-review, and clears the
    single-use token.
    """
    token = (update_data.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token is required")

    user = await db.users.find_one({"info_request_token": token})
    if not user:
        raise HTTPException(status_code=404, detail="Invalid or expired token")

    # Validate token age
    requested_at_str = user.get("info_requested_at")
    if requested_at_str:
        requested_at = datetime.fromisoformat(requested_at_str.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - requested_at > timedelta(hours=_INFO_REQUEST_EXPIRY_HOURS):
            raise HTTPException(status_code=410, detail="This update link has expired. Please register again.")

    # Validate allowable roles for self-registration
    allowed_roles = {UserRole.OWNER, UserRole.TENANT, UserRole.GUEST}
    if update_data.role and update_data.role not in allowed_roles:
        raise HTTPException(status_code=400, detail="Invalid role selection")

    # If a new unit is provided, verify it exists in this building
    if update_data.unit_number:
        unit = await db.units.find_one({"building_id": building_id, "unit_number": update_data.unit_number})
        if not unit:
            raise HTTPException(status_code=400, detail="Unit not found. Please select a valid unit.")

    changes: dict = {
        "status": "pending",  # back to pending for admin re-review
        "info_request_token": None,  # consume the single-use token
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if update_data.unit_number:
        changes["unit_number"] = update_data.unit_number
    if update_data.role:
        changes["role"] = update_data.role

    await db.users.update_one({"id": user["id"]}, {"$set": changes})

    return {
        "message": "Your registration details have been updated. Our team will review and be in touch shortly."
    }


# ---------------------------------------------------------------------------
# GET /admin/archived-users — list archived users for expired-accounts page
# ---------------------------------------------------------------------------

@router.get("/admin/archived-users", response_model=List[ArchivedUserEntry])
async def get_archived_users(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Return all archived users for the Expired Accounts admin page for this building.
    Requires super_admin, chairman, or ec_member role.
    """
    if effective_role(current_user) not in [
        UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER
    ]:
        raise HTTPException(status_code=403, detail="Not authorized to view archived users")

    # Archived users are those whose membership for this building was removed or marked inactive
    # For now, let's look at user_units that are inactive in this building
    inactive_units = await db.user_units.find({"building_id": building_id, "is_active": False}).to_list(500)
    user_ids = list({u["user_id"] for u in inactive_units})

    users = await db.users.find(
        {"id": {"$in": user_ids}},
        {"_id": 0, "password_hash": 0, "info_request_token": 0}
    ).sort("archived_at", -1).to_list(500)

    result = []
    for u in users:
        archived_at = u.get("archived_at")
        days_since = 0
        if archived_at:
            dt = datetime.fromisoformat(archived_at.replace("Z", "+00:00"))
            days_since = (datetime.now(timezone.utc) - dt).days

        result.append({
            "user_id": u.get("id"),
            "full_name": u.get("full_name"),
            "email": u.get("email"),
            "unit_number": u.get("unit_number"),
            "role": u.get("role"),
            "archived_at": archived_at,
            "archived_reason": u.get("archived_reason"),
            "days_since_archived": days_since,
        })

    return result


__all__ = ["router"]
