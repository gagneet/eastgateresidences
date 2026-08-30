# @featuretrace:feature-toggle-system — CRUD API for feature toggles and per-user access overrides.
# Layer: router
# Data flow: Frontend/admin UI → GET|PUT|DELETE /api/feature-toggles/* → config_repo → core.feature_toggles (PG)
#             Per-user overrides: POST /api/feature-toggles/users/{id} → core.users.permission_overrides (PG)
#             Legacy fallback: db.user_feature_access (Mongo) for unresolved Postgres user IDs
# Related: backend/models/feature_toggle.py (FeatureToggleKeys + schemas)
#           backend/utils/permissions.py (require_feature, get_effective_feature_access)
#           backend/db_postgres/repos/config_repo.py (all PG reads/writes)
#           backend/seeds/feature_toggles.py (default toggle catalogue)
# Table: core.feature_toggles, core.feature_toggle_overrides, core.users.permission_overrides
# Collection: user_feature_access (Mongo legacy — fallback only)
# Tests: tests/backend/test_feature_toggle_dependencies.py
#         tests/backend/test_bolt_feature_toggles.py
#         tests/backend/test_sentinel_feature_toggle_hardening.py
#         tests/backend/test_gap_ft_router_toggle_enforcement.py
#         tests/backend/test_feature_toggle_refactor.py
#
# Resolution order (first match wins):
#   1. super_admin → always True
#   2. temp-elevated user → site_wide only (user overrides ignored)
#   3. user-level override → override value
#   4. building/global site_wide default
"""
Feature Toggle Router - Manage feature flags and user access.

Multi-tenant feature toggle system:
  - Global defaults: PostgreSQL core.feature_toggles
  - Per-building overrides: PostgreSQL core.feature_toggle_overrides
  - Per-user overrides: PostgreSQL core.users.permission_overrides
  - Legacy Mongo user overrides remain as a compatibility fallback for
    unresolved/non-Postgres user ids during the transition.
"""

from datetime import datetime, timezone

import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import Annotated, Dict, List, Optional

# The base, not ProtectedToggleError: a blocked global DISABLE of a mock-boundary
# toggle raises MockBoundaryToggleError, which is a sibling rather than a subclass.
# Catching only ProtectedToggleError turned that refusal into a 500.
from core.toggle_classification import ToggleWriteBlockedError
from database import db
from db_postgres.repos import config_repo, identity_repo
from models.feature_toggle import (
    BulkUserFeatureUpdate,
    FeatureAccessSummary,
    FeatureToggleCreate,
    FeatureToggleResponse,
    FeatureToggleUpdate,
    UserFeatureAccessCreate,
    UserFeatureAccessResponse,
)
from models.user import UserRole
from services.capability_registry import require_capability
from utils.auth import effective_role, get_current_user
from utils.rate_limit import refresh_rate_limit_config

router = APIRouter(prefix="/feature-toggles", tags=["Feature Toggles"])


def _as_iso(value) -> str:
    """Return an ISO 8601 string regardless of whether value is already a str or a datetime."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.isoformat()


def _toggle_to_response(toggle: dict) -> FeatureToggleResponse:
    """Generated function header.

    Function: _toggle_to_response
    Path: backend/routers/feature_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return FeatureToggleResponse(
        id=str(toggle.get("id") or toggle.get("_id", "")),
        feature_key=toggle["feature_key"],
        feature_name=toggle["feature_name"],
        description=toggle.get("description"),
        is_enabled=toggle.get("is_enabled", True),
        category=toggle.get("category"),
        icon=toggle.get("icon"),
        routes=toggle.get("routes", []),
        depends_on=toggle.get("depends_on", []),
        created_at=_as_iso(toggle.get("created_at")) or datetime.now(timezone.utc).isoformat(),
        updated_at=_as_iso(toggle.get("updated_at")) or datetime.now(timezone.utc).isoformat(),
        updated_by=toggle.get("updated_by"),
    )


def _legacy_user_access_to_response(access: dict) -> UserFeatureAccessResponse:
    """Generated function header.

    Function: _legacy_user_access_to_response
    Path: backend/routers/feature_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    granted_at = access.get("granted_at", datetime.now(timezone.utc))
    return UserFeatureAccessResponse(
        id=str(access["_id"]),
        user_id=access["user_id"],
        feature_key=access["feature_key"],
        is_enabled=access["is_enabled"],
        granted_by=access.get("granted_by"),
        granted_at=_as_iso(granted_at),
        notes=access.get("notes"),
    )


def _pg_user_access_to_response(access: dict) -> UserFeatureAccessResponse:
    """Generated function header.

    Function: _pg_user_access_to_response
    Path: backend/routers/feature_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return UserFeatureAccessResponse(
        id=str(access["id"]),
        user_id=access["user_id"],
        feature_key=access["feature_key"],
        is_enabled=access["is_enabled"],
        granted_by=access.get("granted_by"),
        granted_at=_as_iso(access.get("granted_at")) or datetime.now(timezone.utc).isoformat(),
        notes=access.get("notes"),
    )


async def _resolve_toggles_for_building(
        effective_building_id: Optional[str],
        category: Optional[str] = None,
        only_enabled: bool = False,
) -> List[dict]:
    """Generated function header.

    Function: _resolve_toggles_for_building
    Path: backend/routers/feature_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await config_repo.list_resolved_feature_toggles(
        effective_building_id,
        category=category,
        only_enabled=only_enabled,
    )


async def _get_single_toggle(feature_key: str, building_id: Optional[str] = None) -> Optional[dict]:
    """Generated function header.

    Function: _get_single_toggle
    Path: backend/routers/feature_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await config_repo.get_resolved_feature_toggle(feature_key, building_id)


def _get_user_building_id(current_user: dict) -> Optional[str]:
    """Extract the building_id associated with a user (from their unit or explicit field)."""
    return current_user.get("building_id") or current_user.get("building") or None


def _compute_effective_access(
        is_super_admin: bool,
        is_elevated: bool,
        site_wide: bool,
        user_override: Optional[bool],
) -> bool:
    """
    Single source of truth for the 4-step feature access resolution.

    Steps (first match wins):
      1. super_admin always has access.
      2. Temp-elevated users follow site_wide only (user overrides bypassed).
      3. Explicit user override takes precedence.
      4. Fall back to site_wide.

    Callers: _resolve_effective_feature_access_entries, get_user_feature_access_summary.
    Mirror:  utils/permissions.py:get_effective_feature_access uses the same logic
             directly against config_repo — keep both in sync if the policy changes.
    """
    if is_super_admin:
        return True
    if is_elevated:
        return site_wide
    if user_override is not None:
        return user_override
    return site_wide


def _get_pg_override_map(current_user: dict) -> dict[str, bool]:
    """Generated function header.

    Function: _get_pg_override_map
    Path: backend/routers/feature_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return config_repo.extract_feature_access_map(current_user.get("permission_overrides") or {})


async def _get_user_override_map(current_user: dict) -> dict[str, bool]:
    """Generated function header.

    Function: _get_user_override_map
    Path: backend/routers/feature_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    pg_map = _get_pg_override_map(current_user)
    if pg_map:
        return pg_map

    user_id = str(current_user.get("id") or "")
    if not user_id:
        return {}

    legacy_overrides = await db.user_feature_access.find({"user_id": user_id}).to_list(100)
    return {override["feature_key"]: override["is_enabled"] for override in legacy_overrides}


async def _resolve_effective_feature_access_entries(
        current_user: dict,
        building_id: Optional[str] = None,
) -> List[dict]:
    """
    Resolve effective feature access for a user in a building context.

    Returns merged feature toggle documents augmented with:
      - site_wide_enabled
      - user_override
      - effective_access
    """
    effective_building_id = building_id if building_id is not None else _get_user_building_id(current_user)

    all_features_task = _resolve_toggles_for_building(effective_building_id)
    user_overrides_task = _get_user_override_map(current_user)

    all_features, override_map = await asyncio.gather(all_features_task, user_overrides_task)

    from utils.permissions import _elevation_active
    is_elevated = _elevation_active(current_user)
    is_super_admin = effective_role(current_user) == UserRole.SUPER_ADMIN

    resolved_features: List[dict] = []
    for feature in all_features:
        site_wide = feature.get("is_enabled", True)
        user_override = override_map.get(feature["feature_key"])
        effective = _compute_effective_access(is_super_admin, is_elevated, site_wide, user_override)

        resolved_features.append({
            **feature,
            "site_wide_enabled": site_wide,
            "user_override": user_override,
            "effective_access": effective,
        })

    return resolved_features


async def _find_target_user(user_id: str) -> dict | None:
    """Generated function header.

    Function: _find_target_user
    Path: backend/routers/feature_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    pg_user = await identity_repo.find_user_by_id_for_admin(user_id)
    if pg_user:
        return {**pg_user, "_source": "postgres"}

    mongo_user = await db.users.find_one({"id": user_id}, {"_id": 0, "hashed_password": 0})
    if mongo_user:
        mongo_user["_source"] = "mongo"
    return mongo_user


# ============================================================================
# SITE-WIDE FEATURE TOGGLE ENDPOINTS
# ============================================================================

@router.get("/", response_model=List[FeatureToggleResponse])
async def get_all_feature_toggles(
        category: Optional[str] = None,
        building_id: Annotated[Optional[str], Query(description="Override building context (super_admin only)")] = None,
        current_user: dict = Depends(get_current_user),
):
    """
    Get all feature toggles with two-tier resolution.

    Super admin can pass `building_id` to view effective toggles for a specific building.
    Other admins see toggles resolved for their own building.
    Non-super-admins only see enabled features.
    """
    is_super_admin = effective_role(current_user) == UserRole.SUPER_ADMIN

    if building_id is not None and not is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super admins can specify a building_id",
        )

    effective_building = building_id if is_super_admin and building_id is not None else _get_user_building_id(
        current_user)
    only_enabled = not is_super_admin

    toggles = await _resolve_toggles_for_building(effective_building, category, only_enabled)
    return [_toggle_to_response(toggle) for toggle in toggles]


@router.get("/{feature_key}", response_model=FeatureToggleResponse)
async def get_feature_toggle(
        feature_key: str,
        current_user: dict = Depends(get_current_user),
):
    """
    Get a specific feature toggle by key.
    Checks per-building override first, falls back to global default.
    """
    building_id = _get_user_building_id(current_user)
    toggle = await _get_single_toggle(feature_key, building_id)

    if not toggle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Feature toggle '{feature_key}' not found",
        )

    return _toggle_to_response(toggle)


@router.post("/", response_model=FeatureToggleResponse, status_code=status.HTTP_201_CREATED)
async def create_feature_toggle(
        toggle_data: FeatureToggleCreate,
        current_user: dict = Depends(require_capability(
            "platform.feature_flags.manage",
            scope_values={"platform_id": "platform"},
        )),
):
    """Create a new global feature toggle. Super admin only."""
    existing = await config_repo.get_global_feature_toggle(toggle_data.feature_key)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Feature toggle '{toggle_data.feature_key}' already exists",
        )

    try:
        created_toggle = await config_repo.create_global_feature_toggle(
            toggle_data.model_dump(),
            actor_user_id=str(current_user.get("id")) if current_user.get("id") else None,
            actor_email=current_user.get("email"),
        )
    except ToggleWriteBlockedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    if toggle_data.feature_key == "rate_limiting":
        await refresh_rate_limit_config()

    return _toggle_to_response(created_toggle)


@router.put("/{feature_key}")
async def update_feature_toggle(
        feature_key: str,
        toggle_data: FeatureToggleUpdate,
        building_id: Annotated[
            Optional[str], Query(description="Update per-building override (super_admin only)")] = None,
        current_user: dict = Depends(require_capability(
            "platform.feature_flags.manage",
            scope_values={"platform_id": "platform"},
        )),
):
    """
    Update a feature toggle. Super admin only.

    If `building_id` is provided: upserts a per-building override.
    If no `building_id`: updates the global default.

    When disabling a feature, returns `affected_dependents` — a list of enabled features
    that declare this feature in their `depends_on`. The frontend uses this to prompt a
    cascade-disable dialog; this endpoint does NOT auto-cascade.
    """
    global_toggle = await config_repo.get_global_feature_toggle(feature_key)
    if not global_toggle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Feature toggle '{feature_key}' not found",
        )

    update_data = {k: v for k, v in toggle_data.model_dump(exclude_unset=True).items() if v is not None}

    if building_id:
        if "is_enabled" in update_data:
            updated_toggle = await config_repo.upsert_feature_toggle_override(
                building_id,
                feature_key,
                bool(update_data["is_enabled"]),
                actor_user_id=str(current_user.get("id")) if current_user.get("id") else None,
                actor_email=current_user.get("email"),
            )
        else:
            updated_toggle = await config_repo.get_resolved_feature_toggle(feature_key, building_id)
    else:
        payload = dict(update_data)
        try:
            updated_toggle = await config_repo.update_global_feature_toggle(
                feature_key,
                payload,
                actor_user_id=str(current_user.get("id")) if current_user.get("id") else None,
                actor_email=current_user.get("email"),
            )
        except ToggleWriteBlockedError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    if not updated_toggle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Feature toggle '{feature_key}' not found",
        )

    if feature_key == "rate_limiting":
        await refresh_rate_limit_config()

    affected_dependents: List[Dict] = []
    if toggle_data.is_enabled is False:
        dependent_toggles = await config_repo.list_resolved_feature_toggles(None)
        affected_dependents = [
            {"feature_key": toggle["feature_key"], "feature_name": toggle["feature_name"]}
            for toggle in dependent_toggles
            if toggle.get("is_enabled", True)
            and feature_key in (toggle.get("depends_on") or [])
            and toggle["feature_key"] != feature_key
        ]

    response_data = _toggle_to_response(updated_toggle).model_dump()
    response_data["affected_dependents"] = affected_dependents
    return response_data


@router.delete("/{feature_key}/override/{bid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_building_override(
        feature_key: str,
        bid: str,
        current_user: dict = Depends(require_capability(
            "platform.feature_flags.manage",
            scope_values={"platform_id": "platform"},
        )),
):
    """
    Delete a per-building override, reverting to global default. Super admin only.
    """
    deleted = await config_repo.delete_feature_toggle_override(bid, feature_key)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No per-building override found for '{feature_key}' / building '{bid}'",
        )


@router.delete("/{feature_key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_feature_toggle(
        feature_key: str,
        current_user: dict = Depends(require_capability(
            "platform.feature_flags.manage",
            scope_values={"platform_id": "platform"},
        )),
):
    """Delete a global feature toggle and all its per-building overrides. Super admin only."""
    deleted = await config_repo.delete_global_feature_toggle(feature_key)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Feature toggle '{feature_key}' not found",
        )

    await config_repo.clear_user_feature_override_from_all_users(feature_key)
    await db.user_feature_access.delete_many({"feature_key": feature_key})

    if feature_key == "rate_limiting":
        await refresh_rate_limit_config()


# ============================================================================
# USER FEATURE ACCESS ENDPOINTS (Per-user overrides)
# ============================================================================

@router.get("/users/{user_id}", response_model=List[UserFeatureAccessResponse])
async def get_user_feature_access(
        user_id: str,
        current_user: dict = Depends(require_capability(
            "platform.feature_flags.manage",
            scope_values={"platform_id": "platform"},
        )),
):
    """Get all feature access overrides for a specific user."""
    target_user = await _find_target_user(user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if target_user["_source"] == "postgres":
        access_list = await config_repo.get_user_feature_override_entries(user_id) or []
        return [_pg_user_access_to_response(access) for access in access_list]

    access_list = await db.user_feature_access.find({"user_id": user_id}).to_list(100)
    return [_legacy_user_access_to_response(access) for access in access_list]


@router.post("/users/{user_id}", response_model=UserFeatureAccessResponse, status_code=status.HTTP_201_CREATED)
async def create_user_feature_access(
        user_id: str,
        access_data: UserFeatureAccessCreate,
        current_user: dict = Depends(require_capability(
            "platform.feature_flags.manage",
            scope_values={"platform_id": "platform"},
        )),
):
    """Create or update user feature access override."""
    target_user = await _find_target_user(user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    feature = await config_repo.get_global_feature_toggle(access_data.feature_key)
    if not feature:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Feature '{access_data.feature_key}' not found",
        )

    if target_user["_source"] == "postgres":
        created_access = await config_repo.upsert_user_feature_override(
            user_id,
            access_data.feature_key,
            access_data.is_enabled,
            granted_by=str(current_user.get("id")) if current_user.get("id") else None,
            granted_by_email=current_user.get("email"),
            notes=access_data.notes,
        )
        if created_access is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        return _pg_user_access_to_response(created_access)

    existing = await db.user_feature_access.find_one({
        "user_id": user_id,
        "feature_key": access_data.feature_key,
    })

    now = datetime.now(timezone.utc)
    access_dict = {
        "user_id": user_id,
        "feature_key": access_data.feature_key,
        "is_enabled": access_data.is_enabled,
        "granted_by": str(current_user["id"]),
        "granted_at": now,
        "notes": access_data.notes,
    }

    if existing:
        await db.user_feature_access.update_one(
            {"_id": existing["_id"]},
            {"$set": access_dict},
        )
        access_id = existing["_id"]
    else:
        result = await db.user_feature_access.insert_one(access_dict)
        access_id = result.inserted_id

    created_access = await db.user_feature_access.find_one({"_id": access_id})
    return _legacy_user_access_to_response(created_access)


@router.delete("/users/{user_id}/{feature_key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_feature_access(
        user_id: str,
        feature_key: str,
        current_user: dict = Depends(require_capability(
            "platform.feature_flags.manage",
            scope_values={"platform_id": "platform"},
        )),
):
    """Remove user feature access override (revert to site-wide setting)."""
    target_user = await _find_target_user(user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if target_user["_source"] == "postgres":
        deleted = await config_repo.delete_user_feature_override(user_id, feature_key)
        if deleted is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User feature access override not found",
            )
        return

    result = await db.user_feature_access.delete_one({
        "user_id": user_id,
        "feature_key": feature_key,
    })

    if result.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User feature access override not found",
        )


@router.post("/users/bulk", status_code=status.HTTP_200_OK)
async def bulk_update_user_feature_access(
        bulk_data: BulkUserFeatureUpdate,
        current_user: dict = Depends(require_capability(
            "platform.feature_flags.manage",
            scope_values={"platform_id": "platform"},
        )),
):
    """Bulk update feature access for multiple users."""
    feature = await config_repo.get_global_feature_toggle(bulk_data.feature_key)
    if not feature:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Feature '{bulk_data.feature_key}' not found",
        )

    now = datetime.now(timezone.utc)
    updates_count = 0

    for user_id in bulk_data.user_ids:
        target_user = await _find_target_user(user_id)
        if not target_user:
            continue

        if target_user["_source"] == "postgres":
            created = await config_repo.upsert_user_feature_override(
                user_id,
                bulk_data.feature_key,
                bulk_data.is_enabled,
                granted_by=str(current_user.get("id")) if current_user.get("id") else None,
                granted_by_email=current_user.get("email"),
                notes=bulk_data.notes,
            )
            if created is not None:
                updates_count += 1
            continue

        await db.user_feature_access.update_one(
            {"user_id": user_id, "feature_key": bulk_data.feature_key},
            {
                "$set": {
                    "user_id": user_id,
                    "feature_key": bulk_data.feature_key,
                    "is_enabled": bulk_data.is_enabled,
                    "granted_by": str(current_user["id"]),
                    "granted_at": now,
                    "notes": bulk_data.notes,
                }
            },
            upsert=True,
        )
        updates_count += 1

    return {
        "message": f"Updated feature access for {updates_count} users",
        "feature_key": bulk_data.feature_key,
        "is_enabled": bulk_data.is_enabled,
    }


# ============================================================================
# FEATURE ACCESS SUMMARY ENDPOINTS
# ============================================================================

@router.get("/access-summary/me", response_model=List[FeatureAccessSummary])
async def get_my_feature_access(
        current_user: dict = Depends(get_current_user),
):
    """
    Get effective feature access for the current user.
    Applies two-tier building resolution for global defaults and per-building overrides.
    """
    resolved_features = await _resolve_effective_feature_access_entries(current_user)
    return [
        FeatureAccessSummary(
            feature_key=feature["feature_key"],
            feature_name=feature["feature_name"],
            site_wide_enabled=feature["site_wide_enabled"],
            user_override=feature["user_override"],
            effective_access=feature["effective_access"],
            category=feature.get("category"),
        )
        for feature in resolved_features
    ]


@router.get("/access-summary/{user_id}", response_model=List[FeatureAccessSummary])
async def get_user_feature_access_summary(
        user_id: str,
        current_user: dict = Depends(require_capability(
            "platform.feature_flags.manage",
            scope_values={"platform_id": "platform"},
        )),
):
    """Get effective feature access for a specific user."""
    target_user = await _find_target_user(user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    building_id = _get_user_building_id(target_user)
    all_features = await _resolve_toggles_for_building(building_id)

    if target_user["_source"] == "postgres":
        override_map = config_repo.extract_feature_access_map(target_user.get("permission_overrides") or {})
    else:
        user_overrides = await db.user_feature_access.find({"user_id": user_id}).to_list(100)
        override_map = {override["feature_key"]: override["is_enabled"] for override in user_overrides}

    from utils.permissions import _elevation_active

    is_elevated = _elevation_active(target_user)
    is_super_admin = effective_role(target_user) == UserRole.SUPER_ADMIN
    summaries = []
    for feature in all_features:
        site_wide = feature.get("is_enabled", True)
        user_override = override_map.get(feature["feature_key"])
        effective = _compute_effective_access(is_super_admin, is_elevated, site_wide, user_override)

        summaries.append(FeatureAccessSummary(
            feature_key=feature["feature_key"],
            feature_name=feature["feature_name"],
            site_wide_enabled=site_wide,
            user_override=user_override,
            effective_access=effective,
            category=feature.get("category"),
        ))

    return summaries
