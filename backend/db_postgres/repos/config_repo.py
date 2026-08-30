"""Postgres-backed config helpers for settings, feature toggles, and user overrides.

This module is the canonical read/write path for PostgreSQL-backed runtime
configuration. It resolves legacy ``building_id`` / scheme-number identifiers
into the Postgres ``(tenant_id, scheme_id)`` pair needed for RLS-scoped tables.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text

from db_postgres.session import async_session_context, set_tenant
from db_postgres.repos import identity_repo
from core.toggle_classification import (
    MOCK_BOUNDARY_SAFETY_METADATA,
    PROTECTED_TOGGLE_SAFETY_METADATA,
    assert_global_disable_allowed,
    assert_global_enable_allowed,
    get_toggle_class,
    is_disable_protected_toggle,
    is_protected_toggle,
)

logger = logging.getLogger(__name__)

_BYPASS_UUID = "00000000-0000-0000-0000-000000000000"


def _row_to_dict(row) -> dict[str, Any]:
    """Generated function header.

    Function: _row_to_dict
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return dict(row._mapping) if hasattr(row, "_mapping") else dict(row)


def _is_uuid(value: Any) -> bool:
    """Generated function header.

    Function: _is_uuid
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not value:
        return False
    try:
        UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


async def _set_bypass(session) -> None:
    """Generated function header.

    Function: _set_bypass
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await session.execute(
        text("SELECT set_config('app.tenant_id', :u, true)"),
        {"u": _BYPASS_UUID},
    )


async def _fallback_actor_user_id() -> str:
    """Generated function header.

    Function: _fallback_actor_user_id
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    async with async_session_context() as session:
        await _set_bypass(session)
        row = (
            await session.execute(
                text(
                    """
                    SELECT user_id::text
                    FROM core.users
                    WHERE role = CAST('super_admin' AS core.user_role)
                      AND is_active = TRUE
                    ORDER BY created_at
                    LIMIT 1
                    """
                )
            )
        ).first()
        if row:
            return str(row[0])

        row = (
            await session.execute(
                text(
                    """
                    SELECT user_id::text
                    FROM core.users
                    ORDER BY created_at
                    LIMIT 1
                    """
                )
            )
        ).first()
        if row:
            return str(row[0])

    raise RuntimeError("No PostgreSQL users are available to attribute config changes.")


async def resolve_actor_user_id(
        actor_user_id: str | None = None,
        actor_email: str | None = None,
        *,
        require_existing: bool = False,
) -> str | None:
    """Generated function header.

    Function: resolve_actor_user_id
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if _is_uuid(actor_user_id):
        actor = await identity_repo.find_user_by_id_for_admin(str(actor_user_id))
        if actor:
            return str(actor["id"])

    if actor_email:
        actor = await identity_repo.find_user_by_email_for_admin(actor_email)
        if actor:
            return str(actor["id"])

    if require_existing:
        return await _fallback_actor_user_id()
    return None


def extract_feature_access_entries(permission_overrides: Any) -> dict[str, dict[str, Any]]:
    """Generated function header.

    Function: extract_feature_access_entries
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not isinstance(permission_overrides, dict):
        return {}

    raw_entries = permission_overrides.get("feature_access")
    if not isinstance(raw_entries, dict):
        return {}

    entries: dict[str, dict[str, Any]] = {}
    for feature_key, value in raw_entries.items():
        if isinstance(value, bool):
            entries[feature_key] = {"is_enabled": value}
            continue

        if not isinstance(value, dict):
            continue

        enabled = value.get("is_enabled")
        if isinstance(enabled, bool):
            entries[feature_key] = {
                "is_enabled": enabled,
                "granted_by": value.get("granted_by"),
                "granted_at": value.get("granted_at"),
                "notes": value.get("notes"),
            }

    return entries


def extract_feature_access_map(permission_overrides: Any) -> dict[str, bool]:
    """Generated function header.

    Function: extract_feature_access_map
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        feature_key: bool(entry["is_enabled"])
        for feature_key, entry in extract_feature_access_entries(permission_overrides).items()
    }


def _normalise_feature_toggle_row(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_feature_toggle_row
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    return {
        "id": str(data.get("override_id") or data.get("toggle_id") or data.get("id")),
        "feature_key": data["feature_key"],
        "feature_name": data["feature_name"],
        "description": data.get("description"),
        "is_enabled": bool(data.get("is_enabled", False)),
        "category": data.get("category"),
        "icon": data.get("icon"),
        "routes": list(data.get("routes") or []),
        "depends_on": list(data.get("depends_on") or []),
        "allowed_roles": list(data.get("allowed_roles") or []),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at") or data.get("last_modified_at") or data.get("created_at"),
        "updated_by": (
            str(data["updated_by"]) if data.get("updated_by") else
            str(data["set_by"]) if data.get("set_by") else
            str(data["last_modified_by"]) if data.get("last_modified_by") else
            None
        ),
    }


async def resolve_scheme_context(building_id: str) -> dict | None:
    """Resolve a legacy building identifier to the Postgres scheme context."""
    if not building_id:
        return None

    scheme = await identity_repo.get_scheme_by_id(building_id)
    if scheme is None:
        scheme = await identity_repo.get_scheme_by_number(building_id)
    return scheme


async def resolve_feature_toggle(
        building_id: str,
        feature_key: str,
        default: bool | None = False,
) -> bool | None:
    """Resolve a Postgres feature toggle for a building."""
    scheme = await resolve_scheme_context(building_id)
    if scheme is None:
        return await get_global_feature_toggle_state(feature_key, default=default)

    async with async_session_context() as session:
        await set_tenant(session, scheme["tenant_id"])
        result = await session.execute(
            text(
                """
                SELECT core.feature_toggle_resolved(:scheme_id, :feature_key)
                """
            ),
            {"scheme_id": str(scheme["scheme_id"]), "feature_key": feature_key},
        )
        value = result.scalar()

    if value is None:
        return default
    return bool(value)


async def get_global_feature_toggle_state(
        feature_key: str,
        *,
        default: bool | None = False,
) -> bool | None:
    """Generated function header.

    Function: get_global_feature_toggle_state
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    row = await get_global_feature_toggle(feature_key)
    if row is None:
        return default
    return bool(row.get("is_enabled", False))


async def list_resolved_feature_toggles(
        building_id: str | None,
        category: str | None = None,
        only_enabled: bool = False,
) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_resolved_feature_toggles
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scheme = await resolve_scheme_context(building_id) if building_id else None
    params: dict[str, Any] = {}
    filters: list[str] = []
    if category is not None:
        filters.append("ft.category = :category")
        params["category"] = category
    if only_enabled:
        filters.append(
            "COALESCE(o.is_enabled, ft.is_enabled) = TRUE"
            if scheme is not None else
            "ft.is_enabled = TRUE"
        )
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

    async with async_session_context() as session:
        if scheme is not None:
            await set_tenant(session, scheme["tenant_id"])
            result = await session.execute(
                text(
                    f"""
                    SELECT ft.toggle_id,
                           o.override_id,
                           ft.feature_key,
                           ft.feature_name,
                           ft.description,
                           COALESCE(o.is_enabled, ft.is_enabled) AS is_enabled,
                           ft.category,
                           ft.icon,
                           ft.routes,
                           ft.depends_on,
                           ft.allowed_roles,
                           ft.created_at,
                           COALESCE(o.set_at, ft.last_modified_at, ft.created_at) AS updated_at,
                           COALESCE(o.set_by::text, ft.last_modified_by::text) AS updated_by
                    FROM core.feature_toggles ft
                    LEFT JOIN core.feature_toggle_overrides o
                      ON o.scheme_id = :scheme_id
                     AND o.feature_key = ft.feature_key
                    {where_clause}
                    ORDER BY ft.feature_key
                    """
                ),
                {"scheme_id": str(scheme["scheme_id"]), **params},
            )
        else:
            result = await session.execute(
                text(
                    f"""
                    SELECT ft.toggle_id,
                           NULL::uuid AS override_id,
                           ft.feature_key,
                           ft.feature_name,
                           ft.description,
                           ft.is_enabled,
                           ft.category,
                           ft.icon,
                           ft.routes,
                           ft.depends_on,
                           ft.allowed_roles,
                           ft.created_at,
                           ft.last_modified_at AS updated_at,
                           ft.last_modified_by::text AS updated_by
                    FROM core.feature_toggles ft
                    {where_clause}
                    ORDER BY ft.feature_key
                    """
                ),
                params,
            )

        rows = result.fetchall()

    return [_normalise_feature_toggle_row(row) for row in rows]


async def get_resolved_feature_toggle(
        feature_key: str,
        building_id: str | None = None,
) -> dict[str, Any] | None:
    """Generated function header.

    Function: get_resolved_feature_toggle
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scheme = await resolve_scheme_context(building_id) if building_id else None
    async with async_session_context() as session:
        if scheme is not None:
            await set_tenant(session, scheme["tenant_id"])
            result = await session.execute(
                text(
                    """
                    SELECT ft.toggle_id,
                           o.override_id,
                           ft.feature_key,
                           ft.feature_name,
                           ft.description,
                           COALESCE(o.is_enabled, ft.is_enabled) AS is_enabled,
                           ft.category,
                           ft.icon,
                           ft.routes,
                           ft.depends_on,
                           ft.allowed_roles,
                           ft.created_at,
                           COALESCE(o.set_at, ft.last_modified_at, ft.created_at) AS updated_at,
                           COALESCE(o.set_by::text, ft.last_modified_by::text) AS updated_by
                    FROM core.feature_toggles ft
                    LEFT JOIN core.feature_toggle_overrides o
                      ON o.scheme_id = :scheme_id
                     AND o.feature_key = ft.feature_key
                    WHERE ft.feature_key = :feature_key
                    LIMIT 1
                    """
                ),
                {"scheme_id": str(scheme["scheme_id"]), "feature_key": feature_key},
            )
        else:
            result = await session.execute(
                text(
                    """
                    SELECT ft.toggle_id,
                           NULL::uuid AS override_id,
                           ft.feature_key,
                           ft.feature_name,
                           ft.description,
                           ft.is_enabled,
                           ft.category,
                           ft.icon,
                           ft.routes,
                           ft.depends_on,
                           ft.allowed_roles,
                           ft.created_at,
                           ft.last_modified_at AS updated_at,
                           ft.last_modified_by::text AS updated_by
                    FROM core.feature_toggles ft
                    WHERE ft.feature_key = :feature_key
                    LIMIT 1
                    """
                ),
                {"feature_key": feature_key},
            )

        row = result.fetchone()

    return _normalise_feature_toggle_row(row) if row else None


async def get_global_feature_toggle(feature_key: str) -> dict[str, Any] | None:
    """Generated function header.

    Function: get_global_feature_toggle
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    async with async_session_context() as session:
        result = await session.execute(
            text(
                """
                SELECT toggle_id,
                       feature_key,
                       feature_name,
                       description,
                       is_enabled,
                       category,
                       icon,
                       routes,
                       depends_on,
                       allowed_roles,
                       created_at,
                       last_modified_at AS updated_at,
                       last_modified_by::text AS updated_by
                FROM core.feature_toggles
                WHERE feature_key = :feature_key
                LIMIT 1
                """
            ),
            {"feature_key": feature_key},
        )
        row = result.fetchone()

    return _normalise_feature_toggle_row(row) if row else None


def _merge_protected_safety_metadata(feature_key: str, field: str, value: list | None) -> list:
    """Return ``value`` with the classification-required entries unioned in.

    Protected toggles must never persist with weaker safety metadata than
    core.toggle_classification declares (P0.3: production rows had empty
    allowed_roles/depends_on, so the super_admin-only contract existed
    nowhere in the live database).
    """
    required = (
        PROTECTED_TOGGLE_SAFETY_METADATA.get(feature_key)
        or MOCK_BOUNDARY_SAFETY_METADATA.get(feature_key)
        or {}
    ).get(field, [])
    merged = list(value or [])
    merged.extend(entry for entry in required if entry not in merged)
    return merged


async def _protected_global_enable_graduated(feature_key: str) -> bool:
    """Has live cutover state made a global enable of this key a non-decision?

    Imported lazily: services.toggle_graduation_service reads the control plane
    through cutover_status_service, which imports this module. A module-level
    import would be a cycle.

    Fails closed on any error — a protected key stays protected unless the control
    plane positively proves every production building is already PostgreSQL-
    authoritative for the domains it routes.
    """
    try:
        from services.toggle_graduation_service import is_graduated
        return await is_graduated(feature_key)
    except Exception as exc:
        logger.warning(
            "config_repo: graduation check failed for %s (%s); staying protected",
            feature_key, exc,
        )
        return False


async def create_global_feature_toggle(
        payload: dict[str, Any],
        *,
        actor_user_id: str | None = None,
        actor_email: str | None = None,
        _allow_protected_global_enable: bool = False,
        _allow_global_mock_disable: bool = False,
) -> dict[str, Any]:
    """Generated function header.

    Function: create_global_feature_toggle
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if bool(payload.get("is_enabled", True)):
        assert_global_enable_allowed(
            payload["feature_key"],
            _allow_protected_global_enable=_allow_protected_global_enable,
            graduated=await _protected_global_enable_graduated(payload["feature_key"]),
        )
    else:
        # Mock-boundary keys invert the rule: OFF is the direction that reaches a
        # live financial institution, so it is the one that needs permission.
        assert_global_disable_allowed(
            payload["feature_key"],
            _allow_global_mock_disable=_allow_global_mock_disable,
        )
    actor_uuid = await resolve_actor_user_id(actor_user_id, actor_email)
    async with async_session_context() as session:
        result = await session.execute(
            text(
                """
                INSERT INTO core.feature_toggles
                    (feature_key, feature_name, description, category, is_enabled,
                     icon, routes, allowed_roles, depends_on,
                     last_modified_by, last_modified_at, created_at)
                VALUES
                    (:feature_key, :feature_name, :description, :category, :is_enabled,
                     :icon, :routes, :allowed_roles, :depends_on,
                     :actor_user_id, NOW(), NOW())
                RETURNING toggle_id,
                          feature_key,
                          feature_name,
                          description,
                          is_enabled,
                          category,
                          icon,
                          routes,
                          depends_on,
                          allowed_roles,
                          created_at,
                          last_modified_at AS updated_at,
                          last_modified_by::text AS updated_by
                """
            ),
            {
                "feature_key": payload["feature_key"],
                "feature_name": payload["feature_name"],
                "description": payload.get("description"),
                "category": payload.get("category"),
                "is_enabled": bool(payload.get("is_enabled", True)),
                "icon": payload.get("icon"),
                "routes": list(payload.get("routes") or []),
                "allowed_roles": _merge_protected_safety_metadata(
                    payload["feature_key"], "allowed_roles",
                    payload.get("allowed_roles") or payload.get("roles"),
                ),
                "depends_on": _merge_protected_safety_metadata(
                    payload["feature_key"], "depends_on", payload.get("depends_on"),
                ),
                "actor_user_id": actor_uuid,
            },
        )
        row = result.fetchone()

    return _normalise_feature_toggle_row(row)


async def update_global_feature_toggle(
        feature_key: str,
        payload: dict[str, Any],
        *,
        actor_user_id: str | None = None,
        actor_email: str | None = None,
        _allow_protected_global_enable: bool = False,
        _allow_global_mock_disable: bool = False,
) -> dict[str, Any] | None:
    """Generated function header.

    Function: update_global_feature_toggle
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if payload.get("is_enabled") is True:
        assert_global_enable_allowed(
            feature_key,
            _allow_protected_global_enable=_allow_protected_global_enable,
            graduated=await _protected_global_enable_graduated(feature_key),
        )
    elif payload.get("is_enabled") is False:
        # See create_global_feature_toggle: for a mock-boundary key the guarded
        # direction is off, not on. `is False` rather than a falsy check so a payload
        # that simply omits is_enabled is not treated as a disable.
        assert_global_disable_allowed(
            feature_key,
            _allow_global_mock_disable=_allow_global_mock_disable,
        )
    actor_uuid = await resolve_actor_user_id(actor_user_id, actor_email)
    assignments = []
    params: dict[str, Any] = {"feature_key": feature_key, "actor_user_id": actor_uuid}

    for field in ("feature_name", "description", "category", "icon"):
        if field in payload:
            assignments.append(f"{field} = :{field}")
            params[field] = payload[field]

    for field in ("is_enabled", "routes", "depends_on", "allowed_roles"):
        if field in payload:
            assignments.append(f"{field} = :{field}")
            value = payload[field]
            if field == "routes":
                value = list(value or [])
            elif field in {"depends_on", "allowed_roles"}:
                value = _merge_protected_safety_metadata(feature_key, field, value)
            params[field] = value

    if not assignments:
        return await get_global_feature_toggle(feature_key)

    assignments.append("last_modified_by = COALESCE(:actor_user_id, last_modified_by)")
    assignments.append("last_modified_at = NOW()")

    async with async_session_context() as session:
        result = await session.execute(
            text(
                f"""
                UPDATE core.feature_toggles
                SET {", ".join(assignments)}
                WHERE feature_key = :feature_key
                RETURNING toggle_id,
                          feature_key,
                          feature_name,
                          description,
                          is_enabled,
                          category,
                          icon,
                          routes,
                          depends_on,
                          allowed_roles,
                          created_at,
                          last_modified_at AS updated_at,
                          last_modified_by::text AS updated_by
                """
            ),
            params,
        )
        row = result.fetchone()

    return _normalise_feature_toggle_row(row) if row else None


async def upsert_feature_toggle_override(
        building_id: str,
        feature_key: str,
        is_enabled: bool,
        *,
        actor_user_id: str | None = None,
        actor_email: str | None = None,
        reason: str | None = None,
) -> dict[str, Any]:
    """Generated function header.

    Function: upsert_feature_toggle_override
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scheme = await resolve_scheme_context(building_id)
    if scheme is None:
        raise RuntimeError(
            f"Could not resolve Postgres scheme context for building_id={building_id!r}."
        )

    if is_enabled and is_protected_toggle(feature_key):
        logger.warning(
            "PROTECTED TOGGLE PROMOTION: '%s' (%s) enabled per-building for building_id=%s "
            "by actor=%s reason=%r — readiness gates per feature-toggle-governance.md §5 "
            "must have passed.",
            feature_key, get_toggle_class(feature_key).value, building_id,
            actor_email or actor_user_id, reason,
        )

    if not is_enabled and is_disable_protected_toggle(feature_key):
        # The mirror of the warning above, for the toggles whose dangerous direction is
        # off. This is the line that records a building being pointed at a real
        # financial institution, so it is logged at the same level and in the same
        # shape — without it, going live would be the only promotion in this table that
        # left no operational trace.
        logger.warning(
            "MOCK BOUNDARY CROSSED: '%s' (%s) disabled per-building for building_id=%s "
            "by actor=%s reason=%r — this building's financial integrations will now "
            "contact LIVE providers.",
            feature_key, get_toggle_class(feature_key).value, building_id,
            actor_email or actor_user_id, reason,
        )

    actor_uuid = await resolve_actor_user_id(actor_user_id, actor_email, require_existing=True)
    async with async_session_context() as session:
        await set_tenant(session, scheme["tenant_id"])
        await session.execute(
            text(
                """
                INSERT INTO core.feature_toggle_overrides
                    (tenant_id, scheme_id, feature_key, is_enabled, set_by, set_at, reason)
                VALUES
                    (:tenant_id, :scheme_id, :feature_key, :is_enabled, :set_by, NOW(), :reason)
                ON CONFLICT (scheme_id, feature_key)
                DO UPDATE
                    SET is_enabled = EXCLUDED.is_enabled,
                        set_by = EXCLUDED.set_by,
                        set_at = NOW(),
                        reason = EXCLUDED.reason
                """
            ),
            {
                "tenant_id": str(scheme["tenant_id"]),
                "scheme_id": str(scheme["scheme_id"]),
                "feature_key": feature_key,
                "is_enabled": bool(is_enabled),
                "set_by": actor_uuid,
                "reason": reason,
            },
        )

    resolved = await get_resolved_feature_toggle(feature_key, building_id)
    if resolved is None:
        raise RuntimeError(f"Failed to resolve feature override for {feature_key!r}.")
    return resolved


async def delete_feature_toggle_override(building_id: str, feature_key: str) -> bool:
    """Generated function header.

    Function: delete_feature_toggle_override
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scheme = await resolve_scheme_context(building_id)
    if scheme is None:
        return False

    async with async_session_context() as session:
        await set_tenant(session, scheme["tenant_id"])
        result = await session.execute(
            text(
                """
                DELETE FROM core.feature_toggle_overrides
                WHERE scheme_id = :scheme_id
                  AND feature_key = :feature_key
                """
            ),
            {"scheme_id": str(scheme["scheme_id"]), "feature_key": feature_key},
        )

    return result.rowcount > 0


async def delete_global_feature_toggle(feature_key: str) -> bool:
    """Generated function header.

    Function: delete_global_feature_toggle
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    async with async_session_context() as session:
        await _set_bypass(session)
        await session.execute(
            text("DELETE FROM core.feature_toggle_overrides WHERE feature_key = :feature_key"),
            {"feature_key": feature_key},
        )
        result = await session.execute(
            text("DELETE FROM core.feature_toggles WHERE feature_key = :feature_key"),
            {"feature_key": feature_key},
        )

    return result.rowcount > 0


async def get_user_feature_override_entries(user_id: str) -> list[dict[str, Any]] | None:
    """Generated function header.

    Function: get_user_feature_override_entries
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    user = await identity_repo.find_user_by_id_for_admin(user_id)
    if user is None:
        return None

    entries = extract_feature_access_entries(user.get("permission_overrides") or {})
    results: list[dict[str, Any]] = []
    for feature_key, entry in sorted(entries.items()):
        results.append({
            "id": f"{user_id}:{feature_key}",
            "user_id": user_id,
            "feature_key": feature_key,
            "is_enabled": bool(entry["is_enabled"]),
            "granted_by": entry.get("granted_by"),
            "granted_at": entry.get("granted_at"),
            "notes": entry.get("notes"),
        })
    return results


async def get_user_feature_override_map(user_id: str) -> dict[str, bool] | None:
    """Generated function header.

    Function: get_user_feature_override_map
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    user = await identity_repo.find_user_by_id_for_admin(user_id)
    if user is None:
        return None
    return extract_feature_access_map(user.get("permission_overrides") or {})


async def upsert_user_feature_override(
        user_id: str,
        feature_key: str,
        is_enabled: bool,
        *,
        granted_by: str | None = None,
        granted_by_email: str | None = None,
        notes: str | None = None,
) -> dict[str, Any] | None:
    """Generated function header.

    Function: upsert_user_feature_override
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    user = await identity_repo.find_user_by_id_for_admin(user_id)
    if user is None:
        return None

    permission_overrides = dict(user.get("permission_overrides") or {})
    feature_access = extract_feature_access_entries(permission_overrides)
    actor_id = await resolve_actor_user_id(granted_by, granted_by_email)
    now = datetime.now(timezone.utc).isoformat()
    feature_access[feature_key] = {
        "is_enabled": bool(is_enabled),
        "granted_by": actor_id,
        "granted_at": now,
        "notes": notes,
    }
    permission_overrides["feature_access"] = feature_access

    async with async_session_context() as session:
        await _set_bypass(session)
        await session.execute(
            text(
                """
                UPDATE core.users
                SET permission_overrides = CAST(:permission_overrides AS jsonb),
                    updated_at = NOW()
                WHERE user_id = :user_id
                """
            ),
            {
                "user_id": str(user_id),
                "permission_overrides": json.dumps(permission_overrides, default=str),
            },
        )

    return {
        "id": f"{user_id}:{feature_key}",
        "user_id": user_id,
        "feature_key": feature_key,
        "is_enabled": bool(is_enabled),
        "granted_by": actor_id,
        "granted_at": now,
        "notes": notes,
    }


async def delete_user_feature_override(user_id: str, feature_key: str) -> bool | None:
    """Generated function header.

    Function: delete_user_feature_override
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    user = await identity_repo.find_user_by_id_for_admin(user_id)
    if user is None:
        return None

    permission_overrides = dict(user.get("permission_overrides") or {})
    feature_access = extract_feature_access_entries(permission_overrides)
    if feature_key not in feature_access:
        return False

    feature_access.pop(feature_key, None)
    permission_overrides["feature_access"] = feature_access

    async with async_session_context() as session:
        await _set_bypass(session)
        await session.execute(
            text(
                """
                UPDATE core.users
                SET permission_overrides = CAST(:permission_overrides AS jsonb),
                    updated_at = NOW()
                WHERE user_id = :user_id
                """
            ),
            {
                "user_id": str(user_id),
                "permission_overrides": json.dumps(permission_overrides, default=str),
            },
        )

    return True


async def clear_user_feature_override_from_all_users(feature_key: str) -> int:
    """Generated function header.

    Function: clear_user_feature_override_from_all_users
    Path: backend/db_postgres/repos/config_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    async with async_session_context() as session:
        await _set_bypass(session)
        result = await session.execute(
            text(
                """
                UPDATE core.users
                SET permission_overrides = CASE
                    WHEN jsonb_typeof(permission_overrides -> 'feature_access') = 'object'
                    THEN jsonb_set(
                        permission_overrides,
                        '{feature_access}',
                        (permission_overrides -> 'feature_access') - :feature_key,
                        true
                    )
                    ELSE permission_overrides
                END,
                updated_at = NOW()
                WHERE COALESCE(permission_overrides -> 'feature_access', '{}'::jsonb) ? :feature_key
                """
            ),
            {"feature_key": feature_key},
        )

    return result.rowcount or 0


async def get_building_setting(
        building_id: str,
        setting_key: str,
        default: Any = None,
) -> Any:
    """Fetch a Postgres building setting by key."""
    scheme = await resolve_scheme_context(building_id)
    if scheme is None:
        return default

    async with async_session_context() as session:
        await set_tenant(session, scheme["tenant_id"])
        result = await session.execute(
            text(
                """
                SELECT setting_value
                FROM core.building_settings
                WHERE scheme_id = :scheme_id
                  AND setting_key = :setting_key
                LIMIT 1
                """
            ),
            {
                "scheme_id": str(scheme["scheme_id"]),
                "setting_key": setting_key,
            },
        )
        value = result.scalar()

    return default if value is None else value


async def upsert_building_setting(
        building_id: str,
        setting_key: str,
        setting_value: Any,
        *,
        set_by: str | None = None,
) -> dict:
    """Insert or update a Postgres building setting and return the scheme context."""
    scheme = await resolve_scheme_context(building_id)
    if scheme is None:
        raise RuntimeError(
            f"Could not resolve Postgres scheme context for building_id={building_id!r}."
        )

    payload = json.dumps(setting_value, default=str)
    async with async_session_context() as session:
        await set_tenant(session, scheme["tenant_id"])
        await session.execute(
            text(
                """
                INSERT INTO core.building_settings
                    (tenant_id, scheme_id, setting_key, setting_value, set_by, set_at)
                VALUES
                    (:tenant_id, :scheme_id, :setting_key, CAST(:setting_value AS jsonb), :set_by, NOW())
                ON CONFLICT (scheme_id, setting_key)
                DO UPDATE
                    SET setting_value = EXCLUDED.setting_value,
                        set_by = COALESCE(EXCLUDED.set_by, core.building_settings.set_by),
                        set_at = NOW()
                """
            ),
            {
                "tenant_id": str(scheme["tenant_id"]),
                "scheme_id": str(scheme["scheme_id"]),
                "setting_key": setting_key,
                "setting_value": payload,
                "set_by": str(set_by) if set_by else None,
            },
        )

    return scheme
