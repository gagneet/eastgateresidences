"""
Permission Service — Central RBAC + ABAC Authorization Engine

This is the NEW authorization system. It works alongside the existing
boolean-flag Permission system (backward compatible).

Key function: user_can(user_id, permission_slug, building_id, resource)

Permission evaluation order:
1. SUPER_ADMIN bypass — always allowed
2. Check user_roles collection for active, scoped role assignments
3. Merge permission slugs from role_permissions
4. Apply ABAC contextual rules (ownership, building scope, time bounds)
5. Return allow/deny with reason

NOTE: All MongoDB access in this module uses ._coll directly to bypass the
TenantCollection building_id auto-injection (we control filtering explicitly).
"""
from datetime import datetime, timezone

import logging
from dataclasses import dataclass, field
from typing import Optional, List, Set

try:
    from database import db
except ImportError:
    db = None  # type: ignore[assignment]

from models.user import UserRole

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 30

# Roles that receive unrestricted financial access
_FINANCE_ELEVATED_ROLES = frozenset({
    UserRole.SUPER_ADMIN,
    UserRole.EC_MEMBER, UserRole.STRATA_MANAGER,
})


@dataclass
class PermissionResult:
    allowed: bool
    reason: str
    matched_roles: List[str] = field(default_factory=list)


# ────────────────────────────────────────────────────────────────────────────
# 1. get_user_active_roles
# ────────────────────────────────────────────────────────────────────────────

async def get_user_active_roles(
        user_id: str,
        building_id: Optional[str] = None,
) -> List[dict]:
    """
    Fetch active role assignments for a user from db.user_roles.

    Applies:
    - user_id match
    - is_active == True
    - start_date <= now OR start_date is None
    - end_date > now OR end_date is None
    - building_id match OR global role (no building_id on assignment)
    """
    if db is None:
        return []

    now = datetime.now(timezone.utc)

    query: dict = {
        "user_id": user_id,
        "is_active": True,
        "$and": [
            {
                "$or": [
                    {"start_date": None},
                    {"start_date": {"$exists": False}},
                    {"start_date": {"$lte": now}},
                ]
            },
            {
                "$or": [
                    {"end_date": None},
                    {"end_date": {"$exists": False}},
                    {"end_date": {"$gt": now}},
                ]
            },
        ],
    }

    if building_id:
        query["$and"].append({
            "$or": [
                {"building_id": building_id},
                {"building_id": None},
                {"building_id": {"$exists": False}},
            ]
        })

    try:
        cursor = db.user_roles._coll.find(query, {"_id": 0})
        return await cursor.to_list(length=None)
    except Exception as exc:
        logger.error("get_user_active_roles error for %s: %s", user_id, exc)
        return []


# ────────────────────────────────────────────────────────────────────────────
# 2. get_role_permissions
# ────────────────────────────────────────────────────────────────────────────

async def get_role_permissions(role_id: str) -> List[str]:
    """
    Return list of permission slugs assigned to role_id from db.role_permissions.
    """
    if db is None:
        return []

    try:
        cursor = db.role_permissions._coll.find({"role_id": role_id}, {"_id": 0, "slug": 1})
        docs = await cursor.to_list(length=None)
        return [d["slug"] for d in docs if "slug" in d]
    except Exception as exc:
        logger.error("get_role_permissions error for role %s: %s", role_id, exc)
        return []


# ────────────────────────────────────────────────────────────────────────────
# 3. get_user_permission_slugs
# ────────────────────────────────────────────────────────────────────────────

async def get_user_permission_slugs(
        user_id: str,
        building_id: Optional[str] = None,
) -> Set[str]:
    """
    Aggregate all permission slugs for a user across active role assignments.

    - Checks db.permissions_cache first (30 s TTL).
    - Handles role inheritance via the `inherits_from` field on role documents.
    """
    if db is None:
        return set()

    # ── Cache check ──────────────────────────────────────────────────────────
    cache_key = {"user_id": user_id, "building_id": building_id}
    try:
        cached = await db.permissions_cache._coll.find_one(cache_key, {"_id": 0})
        if cached:
            cached_at = cached.get("cached_at")
            if isinstance(cached_at, datetime):
                if cached_at.tzinfo is None:
                    cached_at = cached_at.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - cached_at).total_seconds()
                if age < CACHE_TTL_SECONDS:
                    return set(cached.get("slugs", []))
    except Exception as exc:
        logger.debug("permissions_cache read error: %s", exc)

    # ── Fetch active roles ───────────────────────────────────────────────────
    assignments = await get_user_active_roles(user_id, building_id)
    slugs: Set[str] = set()
    seen_roles: Set[str] = set()

    async def _collect_slugs_for_role(role_id: str) -> None:
        """Generated function header.

        Function: _collect_slugs_for_role
        Path: backend/services/permission_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if role_id in seen_roles:
            return
        seen_roles.add(role_id)

        role_slugs = await get_role_permissions(role_id)
        slugs.update(role_slugs)

        # Role inheritance — follow `inherits_from` links
        try:
            role_doc = await db.roles._coll.find_one(
                {"id": role_id}, {"_id": 0, "inherits_from": 1}
            )
            if role_doc:
                parent = role_doc.get("inherits_from")
                if isinstance(parent, list):
                    for p in parent:
                        await _collect_slugs_for_role(p)
                elif isinstance(parent, str):
                    await _collect_slugs_for_role(parent)
        except Exception as exc:
            logger.debug("role inheritance fetch error for %s: %s", role_id, exc)

    for assignment in assignments:
        rid = assignment.get("role_id")
        if rid:
            await _collect_slugs_for_role(rid)

    # ── Write cache ──────────────────────────────────────────────────────────
    try:
        now = datetime.now(timezone.utc)
        await db.permissions_cache._coll.update_one(
            cache_key,
            {"$set": {**cache_key, "slugs": list(slugs), "cached_at": now}},
            upsert=True,
        )
    except Exception as exc:
        logger.debug("permissions_cache write error: %s", exc)

    return slugs


# ────────────────────────────────────────────────────────────────────────────
# 4. apply_abac_rules
# ────────────────────────────────────────────────────────────────────────────

async def apply_abac_rules(
        user_id: str,
        permission_slug: str,
        building_id: Optional[str],
        resource: Optional[dict],
) -> Optional[bool]:
    """
    Evaluate Attribute-Based Access Control rules.

    Returns:
        True  — force allow (overrides RBAC deny)
        False — force deny  (overrides RBAC allow)
        None  — no ABAC rule applies; use RBAC result
    """
    if db is None:
        return None

    # ── Building scope mismatch ───────────────────────────────────────────────
    if resource and building_id:
        resource_bid = resource.get("building_id")
        if resource_bid and resource_bid != building_id:
            return False

    # ── building.config — SUPER_ADMIN only ───────────────────────────────────
    if permission_slug == "building.config":
        try:
            user = await db.users._coll.find_one({"id": user_id}, {"_id": 0, "role": 1})
            if not user or user.get("role") != UserRole.SUPER_ADMIN:
                return False
        except Exception as exc:
            logger.error("ABAC building.config user fetch error: %s", exc)
            return False
        return True

    # ── financial.view — EC/managers get all; owners redirected to view_own ───
    if permission_slug == "financial.view":
        try:
            user = await db.users._coll.find_one({"id": user_id}, {"_id": 0, "role": 1})
            if user and user.get("role") in _FINANCE_ELEVATED_ROLES:
                return None  # elevated role — let RBAC decide
            # Owner-level: only allowed if they own a unit in the building
            return await _check_owns_unit(user_id, building_id)
        except Exception as exc:
            logger.error("ABAC financial.view error for user %s: %s", user_id, exc)
            return None

    # ── financial.view_own — user must own a unit ─────────────────────────────
    if permission_slug == "financial.view_own":
        return await _check_owns_unit(user_id, building_id)

    return None


async def _check_owns_unit(user_id: str, building_id: Optional[str]) -> Optional[bool]:
    """Return True if user owns any unit in the building, False if not, None on error.

    Checks by owner_id first (new system). Falls back to matching the user's
    unit_number profile field against the units collection (legacy data where
    units are linked by owner_name, not owner_id).
    """
    if db is None:
        return None
    try:
        # Primary: check by explicit owner_id (new-style linkage)
        unit_filter: dict = {"owner_id": user_id}
        if building_id:
            unit_filter["building_id"] = building_id
        unit = await db.units._coll.find_one(unit_filter, {"_id": 0, "id": 1})
        if unit:
            return True

        # Fallback: units imported from Excel use owner_name, not owner_id.
        # Derive ownership from the user's unit_number profile field.
        user_doc = await db.users._coll.find_one({"id": user_id}, {"_id": 0, "unit_number": 1})
        if user_doc and user_doc.get("unit_number"):
            fallback_filter: dict = {"unit_number": user_doc["unit_number"]}
            if building_id:
                fallback_filter["building_id"] = building_id
            unit = await db.units._coll.find_one(fallback_filter, {"_id": 0, "id": 1})
            return bool(unit)

        return False
    except Exception as exc:
        logger.error("_check_owns_unit error for user %s: %s", user_id, exc)
        return None


# ────────────────────────────────────────────────────────────────────────────
# 5. user_can
# ────────────────────────────────────────────────────────────────────────────

async def user_can(
        user_id: str,
        permission_slug: str,
        building_id: Optional[str] = None,
        resource: Optional[dict] = None,
) -> PermissionResult:
    """
    Central authorization check.

    Evaluation order:
    1. Fetch user — 404 guard
    2. SUPER_ADMIN bypass
    3. temp_elevation: active elevation grants its role's permission slugs
    4. RBAC: check user_permission_slugs
    5. ABAC: contextual override (can flip RBAC result)
    """
    if db is None:
        return PermissionResult(allowed=False, reason="Database unavailable")

    # ── 1. Fetch user ─────────────────────────────────────────────────────────
    try:
        user = await db.users._coll.find_one({"id": user_id}, {"_id": 0})
    except Exception as exc:
        logger.error("user_can: user fetch failed for %s: %s", user_id, exc)
        return PermissionResult(allowed=False, reason="User fetch error")

    if not user:
        return PermissionResult(allowed=False, reason="User not found")

    # ── 2. SUPER_ADMIN bypass ─────────────────────────────────────────────────
    if user.get("role") == UserRole.SUPER_ADMIN:
        return PermissionResult(
            allowed=True,
            reason="SUPER_ADMIN bypass",
            matched_roles=[UserRole.SUPER_ADMIN],
        )

    # ── 3. Temporary elevation check ─────────────────────────────────────────
    elev = user.get("temp_elevation")
    if elev:
        try:
            exp_str = elev.get("expires_at", "")
            exp = datetime.fromisoformat(exp_str.replace("Z", "+00:00")) if exp_str else None
            if exp and datetime.now(timezone.utc) < exp:
                elevated_role_id = elev.get("role")
                if elevated_role_id:
                    elevated_slugs = await get_role_permissions(elevated_role_id)
                    if permission_slug in elevated_slugs:
                        return PermissionResult(
                            allowed=True,
                            reason=f"temp_elevation via role '{elevated_role_id}'",
                            matched_roles=[elevated_role_id],
                        )
        except Exception as exc:
            logger.debug("user_can: temp_elevation parse error for %s: %s", user_id, exc)

    # ── 4. RBAC slug check ────────────────────────────────────────────────────
    slugs = await get_user_permission_slugs(user_id, building_id)
    rbac_allowed = permission_slug in slugs

    # Collect matched role IDs for the audit trail (best-effort)
    matched: List[str] = []
    if rbac_allowed:
        try:
            assignments = await get_user_active_roles(user_id, building_id)
            for assignment in assignments:
                rid = assignment.get("role_id")
                if rid:
                    role_slugs = await get_role_permissions(rid)
                    if permission_slug in role_slugs:
                        matched.append(rid)
        except Exception as exc:
            logger.debug("user_can: matched-role resolution error: %s", exc)

    # ── 5. ABAC contextual override ───────────────────────────────────────────
    abac_result = await apply_abac_rules(user_id, permission_slug, building_id, resource)

    if abac_result is True:
        return PermissionResult(
            allowed=True,
            reason="ABAC rule: force allow",
            matched_roles=matched,
        )
    if abac_result is False:
        return PermissionResult(
            allowed=False,
            reason="ABAC rule: force deny",
            matched_roles=matched,
        )

    # ABAC returned None — honour RBAC result
    if rbac_allowed:
        return PermissionResult(
            allowed=True,
            reason=f"RBAC: slug '{permission_slug}' matched",
            matched_roles=matched,
        )

    return PermissionResult(
        allowed=False,
        reason=f"No role grants '{permission_slug}'",
    )


# ────────────────────────────────────────────────────────────────────────────
# 6. sync_user_relationships
# ────────────────────────────────────────────────────────────────────────────

async def sync_user_relationships(user_id: str) -> None:
    """
    Idempotently create/update relationship_tuples from a user's current
    unit ownership and committee roles.

    Tuples written:
    - (user:{id}, owner_of, unit:{unit_number})  — for each owned unit
    - (unit:{unit_number}, part_of, building:{building_id})
    - (user:{id}, committee_member, building:{building_id})  — for EC roles
    """
    if db is None:
        return

    try:
        user = await db.users._coll.find_one({"id": user_id}, {"_id": 0, "role": 1})
        if not user:
            return

        now = datetime.now(timezone.utc).isoformat()
        tuples_to_write: List[dict] = []

        # ── Unit ownership ────────────────────────────────────────────────────
        try:
            owned_units = await db.units._coll.find(
                {"owner_id": user_id},
                {"_id": 0, "unit_number": 1, "building_id": 1},
            ).to_list(length=None)
        except Exception as exc:
            logger.debug("sync_user_relationships: units query error: %s", exc)
            owned_units = []

        for unit in owned_units:
            unit_number = unit.get("unit_number")
            bid = unit.get("building_id")
            if not unit_number or not bid:
                continue

            tuples_to_write.append({
                "subject": f"user:{user_id}",
                "relation": "owner_of",
                "object": f"unit:{unit_number}",
                "building_id": bid,
                "updated_at": now,
            })
            tuples_to_write.append({
                "subject": f"unit:{unit_number}",
                "relation": "part_of",
                "object": f"building:{bid}",
                "building_id": bid,
                "updated_at": now,
            })

        # ── Committee membership ───────────────────────────────────────────────
        if user.get("role") in {UserRole.EC_MEMBER}:
            try:
                memberships = await db.memberships._coll.find(
                    {"user_id": user_id, "is_active": True},
                    {"_id": 0, "building_id": 1},
                ).to_list(length=None)
            except Exception as exc:
                logger.debug("sync_user_relationships: memberships query error: %s", exc)
                memberships = []

            for m in memberships:
                bid = m.get("building_id")
                if bid:
                    tuples_to_write.append({
                        "subject": f"user:{user_id}",
                        "relation": "committee_member",
                        "object": f"building:{bid}",
                        "building_id": bid,
                        "updated_at": now,
                    })

        # ── Upsert all tuples ─────────────────────────────────────────────────
        for tpl in tuples_to_write:
            key = {k: tpl[k] for k in ("subject", "relation", "object")}
            try:
                await db.relationship_tuples._coll.update_one(
                    key, {"$set": tpl}, upsert=True
                )
            except Exception as exc:
                logger.error(
                    "sync_user_relationships: upsert error %s: %s", key, exc
                )

    except Exception as exc:
        logger.error("sync_user_relationships failed for user %s: %s", user_id, exc)


# ────────────────────────────────────────────────────────────────────────────
# 7. legacy_to_slug_permissions
# ────────────────────────────────────────────────────────────────────────────

_LEGACY_TO_SLUGS: dict = {
    "can_view_finances": {"financial.view", "financial.view_own"},
    "can_manage_finances": {"financial.manage", "financial.approve_invoice", "financial.export"},
    "can_view_documents": {"documents.view"},
    "can_upload_documents": {"documents.upload"},
    "can_manage_document_permissions": {"documents.manage", "documents.delete"},
    "can_manage_users": {"users.view", "users.manage", "users.invite"},
    "can_post_announcements": {"announcements.create", "announcements.manage"},
    "can_send_notifications": {"notifications.send"},
    "can_view_meetings": {"meetings.view"},
    "can_manage_meetings": {"meetings.manage"},
    "can_chat": {"chat.access"},
    "can_access_email": {"email.access"},
    "can_manage_requests": {"maintenance.manage", "workorder.approve"},
    "can_manage_tenancy": {"tenancy.manage"},
    "can_view_schedule": {"schedule.view"},
}


def legacy_to_slug_permissions(legacy_permissions: dict) -> Set[str]:
    """
    Map existing boolean Permission flags to RBAC permission slugs.
    Only flags that are True contribute slugs.
    """
    slugs: Set[str] = set()
    for flag, value in legacy_permissions.items():
        if value and flag in _LEGACY_TO_SLUGS:
            slugs.update(_LEGACY_TO_SLUGS[flag])
    return slugs


__all__ = [
    "PermissionResult",
    "get_user_active_roles",
    "get_role_permissions",
    "get_user_permission_slugs",
    "apply_abac_rules",
    "user_can",
    "sync_user_relationships",
    "legacy_to_slug_permissions",
]
