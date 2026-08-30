# @featuretrace:bi-analytics — Hierarchical BI feature toggle resolver: platform → management_entity → agency → building → safe default.
# Layer: service
# Data flow: /api/bi/* → bi_toggle_service.resolve_bi_feature_access → core.feature_toggles (PG)
#            + core.management_entities + core.scheme_management_assignments (PG) → BIResolvedAccess
# Related: backend/routers/bi.py
#          backend/services/bi_service.py
#          backend/services/management_hierarchy_service.py
#          backend/db_postgres/repos/config_repo.py
#          backend/alembic/versions/0053_agency_strata_manager_hierarchy.py
#          backend/alembic/versions/0056_management_entity_model.py
# Toggle: bi_analytics_enabled, bi_analytics_platform_enabled, bi_analytics_agency_enabled,
#         bi_analytics_building_enabled, bi_portfolio_analytics_enabled, bi_pg_primary_enabled,
#         bi_analytics_management_entity_enabled
# Tests: tests/backend/test_bi_phase2.py, tests/backend/test_management_hierarchy.py
"""BI feature toggle hierarchy and access resolver.

Resolution order (first match wins):
  1. Explicit building override
  2. Management entity override (self_managed_oc / independent_manager / agency)
  3. Agency override (legacy compatibility — maps to management entity when available)
  4. Platform default
  5. Hardcoded safe default = False

Each scope also controls what *scope* of data the user can see:
  - building         → single building BI (EC member, owner, internal manager)
  - agency           → all buildings under the agency (agency principal / manager)
  - management_entity→ all buildings assigned to any management entity type
  - portfolio        → assigned buildings only (strata manager — cross-agency or own)
  - platform         → all agencies, entities, and buildings (super admin only)

Management model behaviour:
  - agency_managed       → entity_type='agency'; agency toggle and entity toggle both resolve.
  - self_managed_oc      → entity_type='self_managed_oc'; portfolio BI for EC office bearers only.
  - independent_manager  → entity_type='independent_manager'; portfolio shows assigned buildings.
  - unknown/pending      → BI disabled; safe default.

The PG-primary flag is independent: it can be enabled per-building after ETL
parity checks pass, without affecting BI visibility itself.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Toggle key constants ───────────────────────────────────────────────────────
BI_PLATFORM_ENABLED = "bi_analytics_platform_enabled"
BI_AGENCY_ENABLED = "bi_analytics_agency_enabled"
BI_BUILDING_ENABLED = "bi_analytics_building_enabled"
BI_PORTFOLIO_ENABLED = "bi_portfolio_analytics_enabled"
BI_PG_PRIMARY = "bi_pg_primary_enabled"
# Legacy alias kept for backward-compat
BI_LEGACY_ENABLED = "bi_analytics_enabled"
# Management-entity-level toggle (resolution tier 2)
BI_MANAGEMENT_ENTITY_ENABLED = "bi_analytics_management_entity_enabled"

_SCOPE_PLATFORM = "platform"
_SCOPE_AGENCY = "agency"
_SCOPE_MANAGEMENT_ENTITY = "management_entity"
_SCOPE_PORTFOLIO = "portfolio"
_SCOPE_BUILDING = "building"

_SOURCE_BUILDING = "building_override"
_SOURCE_MANAGEMENT_ENTITY = "management_entity_override"
_SOURCE_AGENCY = "agency_override"
_SOURCE_PLATFORM = "platform_default"
_SOURCE_SAFE = "safe_default"


# ── Return type ───────────────────────────────────────────────────────────────

@dataclass
class BIResolvedAccess:
    enabled: bool
    scope: str                  # building | agency | portfolio | platform
    source: str                 # building_override | agency_override | platform_default | safe_default
    pg_primary: bool
    reason: str
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Generated function header.

        Function: BIResolvedAccess.as_dict
        Path: backend/services/bi_toggle_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return {
            "enabled": self.enabled,
            "scope": self.scope,
            "source": self.source,
            "pg_primary": self.pg_primary,
            "reason": self.reason,
            "warnings": self.warnings,
        }


# ── Resolver ──────────────────────────────────────────────────────────────────

async def resolve_bi_feature_access(
    *,
    user_id: str,
    role: str,
    effective_role: str,
    agency_id: str | None,
    building_id: str | None,
    management_entity_id: str | None = None,
    feature_key: str = BI_BUILDING_ENABLED,
) -> BIResolvedAccess:
    """Resolve BI feature access for a user at the appropriate scope.

    Resolution order (first match wins):
      1. building override
      2. management_entity override (self_managed_oc / independent_manager / agency)
      3. agency override (legacy compatibility)
      4. platform default
      5. safe default = False

    Returns a BIResolvedAccess with the resolved scope and whether BI is enabled.
    """
    warnings: list[str] = []

    # Resolve PG-primary flag independently
    pg_primary = await _resolve_pg_primary(building_id)

    # Auto-resolve management_entity_id from building when not supplied
    if not management_entity_id and building_id:
        management_entity_id = await _resolve_management_entity_id(building_id)

    # ── Super Admin: platform scope ────────────────────────────────────────────
    if effective_role == "super_admin":
        platform_on = await _resolve_platform_toggle(BI_PLATFORM_ENABLED)
        if not platform_on:
            platform_on = await _resolve_global_toggle(BI_LEGACY_ENABLED)
            if platform_on:
                warnings.append("Using legacy bi_analytics_enabled toggle; migrate to bi_analytics_platform_enabled")
        if platform_on:
            return BIResolvedAccess(
                enabled=True,
                scope=_SCOPE_PLATFORM,
                source=_SOURCE_PLATFORM,
                pg_primary=pg_primary,
                reason="super_admin: bi_analytics_platform_enabled is ON",
                warnings=warnings,
            )
        return BIResolvedAccess(
            enabled=False,
            scope=_SCOPE_PLATFORM,
            source=_SOURCE_SAFE,
            pg_primary=False,
            reason="super_admin: bi_analytics_platform_enabled is OFF",
            warnings=warnings,
        )

    # ── Agency Principal / Agency Manager: agency or management_entity scope ───
    if effective_role in {"strata_admin"}:
        # Prefer management entity resolution over legacy agency_id
        if management_entity_id:
            me_on = await _resolve_management_entity_toggle(management_entity_id)
            if me_on:
                entity_type = await _get_management_entity_type(management_entity_id)
                return BIResolvedAccess(
                    enabled=True,
                    scope=_SCOPE_MANAGEMENT_ENTITY,
                    source=_SOURCE_MANAGEMENT_ENTITY,
                    pg_primary=pg_primary,
                    reason=f"strata_admin: management_entity override ON (type={entity_type}, id={management_entity_id})",
                    warnings=warnings,
                )
        if agency_id:
            agency_on = await _resolve_agency_toggle(agency_id, BI_AGENCY_ENABLED)
            if agency_on:
                return BIResolvedAccess(
                    enabled=True,
                    scope=_SCOPE_AGENCY,
                    source=_SOURCE_AGENCY,
                    pg_primary=pg_primary,
                    reason=f"strata_admin: bi_analytics_agency_enabled is ON for agency={agency_id}",
                    warnings=warnings,
                )
        return BIResolvedAccess(
            enabled=False,
            scope=_SCOPE_AGENCY,
            source=_SOURCE_SAFE,
            pg_primary=False,
            reason="strata_admin: no agency or management_entity BI toggle enabled",
            warnings=warnings,
        )

    # ── Strata Manager: portfolio scope ────────────────────────────────────────
    if effective_role == "strata_manager":
        # Check portfolio toggle at platform level first
        portfolio_on = await _resolve_platform_toggle(BI_PORTFOLIO_ENABLED)
        if not portfolio_on and building_id:
            # Fall back to building-level check
            portfolio_on, used_legacy_building = await _resolve_building_toggle_with_source(building_id, BI_BUILDING_ENABLED)
            if used_legacy_building:
                warnings.append("Using legacy bi_analytics_enabled toggle; migrate to bi_analytics_building_enabled")
        if not portfolio_on:
            portfolio_on = await _resolve_global_toggle(BI_LEGACY_ENABLED)
            if portfolio_on:
                warnings.append("Using legacy bi_analytics_enabled toggle; migrate to bi_portfolio_analytics_enabled")
        if portfolio_on:
            return BIResolvedAccess(
                enabled=True,
                scope=_SCOPE_PORTFOLIO,
                source=_SOURCE_PLATFORM if not building_id else _SOURCE_BUILDING,
                pg_primary=pg_primary,
                reason="strata_manager: portfolio BI enabled",
                warnings=warnings,
            )
        return BIResolvedAccess(
            enabled=False,
            scope=_SCOPE_PORTFOLIO,
            source=_SOURCE_SAFE,
            pg_primary=False,
            reason="strata_manager: no portfolio BI toggle enabled",
            warnings=warnings,
        )

    # ── EC Member / internal manager: building scope ──────────────────────────
    if effective_role in {"ec_member", "strata_admin"}:
        if building_id:
            # Tier 1: building override
            bldg_on, used_legacy = await _resolve_building_toggle_with_source(building_id, BI_BUILDING_ENABLED)
            if used_legacy:
                warnings.append("Using legacy bi_analytics_enabled; migrate to bi_analytics_building_enabled")
            if bldg_on:
                return BIResolvedAccess(
                    enabled=True,
                    scope=_SCOPE_BUILDING,
                    source=_SOURCE_BUILDING,
                    pg_primary=pg_primary,
                    reason=f"ec_member: building override ON for {building_id}",
                    warnings=warnings,
                )
            # Tier 2: management entity override (self_managed_oc / independent_manager / agency)
            if management_entity_id:
                me_on = await _resolve_management_entity_toggle(management_entity_id)
                if me_on:
                    entity_type = await _get_management_entity_type(management_entity_id)
                    # self_managed_oc: EC office bearers with approved appointments get portfolio
                    if entity_type == "self_managed_oc":
                        has_appointment = await _user_has_active_appointment(user_id, building_id)
                        if has_appointment:
                            return BIResolvedAccess(
                                enabled=True,
                                scope=_SCOPE_MANAGEMENT_ENTITY,
                                source=_SOURCE_MANAGEMENT_ENTITY,
                                pg_primary=pg_primary,
                                reason=f"ec_member: self_managed_oc BI ON, user has active appointment for {building_id}",
                                warnings=warnings,
                            )
                        warnings.append("self_managed_oc BI requires an active ec_internal_strata_manager appointment")
                    else:
                        return BIResolvedAccess(
                            enabled=True,
                            scope=_SCOPE_MANAGEMENT_ENTITY,
                            source=_SOURCE_MANAGEMENT_ENTITY,
                            pg_primary=pg_primary,
                            reason=f"ec_member: management_entity override ON (type={entity_type})",
                            warnings=warnings,
                        )
            # Tier 3: agency override (legacy)
            if agency_id:
                agency_on = await _resolve_agency_toggle(agency_id, BI_AGENCY_ENABLED)
                if agency_on:
                    return BIResolvedAccess(
                        enabled=True,
                        scope=_SCOPE_BUILDING,
                        source=_SOURCE_AGENCY,
                        pg_primary=pg_primary,
                        reason=f"ec_member: agency override ON for agency={agency_id}",
                        warnings=warnings,
                    )
            # Tier 4: platform default
            platform_on = await _resolve_platform_toggle(BI_PLATFORM_ENABLED)
            if not platform_on:
                platform_on = await _resolve_global_toggle(BI_LEGACY_ENABLED)
                if platform_on:
                    warnings.append("Using legacy bi_analytics_enabled; migrate to bi_analytics_platform_enabled")
            if platform_on:
                return BIResolvedAccess(
                    enabled=True,
                    scope=_SCOPE_BUILDING,
                    source=_SOURCE_PLATFORM,
                    pg_primary=pg_primary,
                    reason="ec_member: platform default ON",
                    warnings=warnings,
                )
        return BIResolvedAccess(
            enabled=False,
            scope=_SCOPE_BUILDING,
            source=_SOURCE_SAFE,
            pg_primary=False,
            reason="ec_member: BI not enabled for this building",
            warnings=warnings,
        )

    # ── Owner / Tenant: building scope (read-only safe panels only) ────────────
    if effective_role in {"owner", "tenant"}:
        if building_id:
            bldg_on, used_legacy = await _resolve_building_toggle_with_source(building_id, BI_BUILDING_ENABLED)
            if used_legacy:
                warnings.append("Using legacy bi_analytics_enabled; migrate to bi_analytics_building_enabled")
            if bldg_on:
                return BIResolvedAccess(
                    enabled=True,
                    scope=_SCOPE_BUILDING,
                    source=_SOURCE_BUILDING,
                    pg_primary=pg_primary,
                    reason=f"owner/tenant: building BI enabled for {building_id}",
                    warnings=warnings + ["owner/tenant scope — financial details are restricted"],
                )
        return BIResolvedAccess(
            enabled=False,
            scope=_SCOPE_BUILDING,
            source=_SOURCE_SAFE,
            pg_primary=False,
            reason="owner/tenant: BI not enabled or no building context",
            warnings=warnings,
        )

    # ── Default: safe deny ────────────────────────────────────────────────────
    return BIResolvedAccess(
        enabled=False,
        scope=_SCOPE_BUILDING,
        source=_SOURCE_SAFE,
        pg_primary=False,
        reason=f"role={effective_role!r}: no BI access rule matched; safe default = False",
        warnings=warnings,
    )


# ── Agency-level access check ─────────────────────────────────────────────────

async def resolve_agency_access(
    *,
    user_id: str,
    effective_role: str,
    agency_id: str,
) -> bool:
    """Return True if the user can access agency-level BI data."""
    agency_on = await _resolve_agency_toggle(agency_id, BI_AGENCY_ENABLED)
    if not agency_on:
        agency_on = await _resolve_global_toggle(BI_LEGACY_ENABLED)
    if not agency_on:
        return False
    if effective_role == "super_admin":
        return True
    if effective_role in {"strata_admin", "strata_manager"}:
        return await _user_belongs_to_agency(user_id, agency_id)
    return False


async def resolve_manager_access(
    *,
    user_id: str,
    effective_role: str,
    strata_manager_id: str,
) -> bool:
    """Return True if the user can access data scoped to this strata manager profile."""
    portfolio_on = await _resolve_platform_toggle(BI_PORTFOLIO_ENABLED)
    if not portfolio_on:
        portfolio_on = await _resolve_global_toggle(BI_LEGACY_ENABLED)
    if not portfolio_on:
        return False
    if effective_role == "super_admin":
        return True
    if effective_role in {"strata_admin", "strata_manager"}:
        # Can access own profile or (if strata_admin) any profile in own agency
        own = await _get_strata_manager_profile_by_user(user_id)
        if own and str(own.get("strata_manager_id", "")) == strata_manager_id:
            return True
        if effective_role == "strata_admin":
            # Agency principal can see all managers in their agency
            profile = await _get_strata_manager_profile_by_id(strata_manager_id)
            if profile:
                agency_id = str(profile.get("agency_id", ""))
                return await _user_belongs_to_agency(user_id, agency_id)
    return False


async def get_manager_buildings(strata_manager_id: str) -> list[str]:
    """Return list of scheme_number strings for buildings assigned to this manager."""
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            rows = await session.execute(
                text("""
                    SELECT s.scheme_number
                    FROM core.building_manager_assignments bma
                    JOIN core.schemes s ON s.scheme_id = bma.scheme_id
                    WHERE bma.strata_manager_id = CAST(:mid AS UUID)
                      AND bma.status = 'active'
                      AND (s.is_archived IS NOT TRUE)
                      AND COALESCE(bma.is_test_data, FALSE) = FALSE
                """),
                {"mid": strata_manager_id},
            )
            return [r[0] for r in rows.fetchall()]
    except Exception as exc:
        logger.warning("get_manager_buildings(%s) failed: %s", strata_manager_id, exc)
        return []


async def get_agency_buildings(agency_id: str) -> list[str]:
    """Return list of scheme_number strings for buildings assigned to this agency."""
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            rows = await session.execute(
                text("""
                    SELECT s.scheme_number
                    FROM core.building_agency_assignments baa
                    JOIN core.schemes s ON s.scheme_id = baa.scheme_id
                    WHERE baa.agency_id = CAST(:aid AS UUID)
                      AND baa.status = 'active'
                      AND (s.is_archived IS NOT TRUE)
                      AND COALESCE(baa.is_test_data, FALSE) = FALSE
                """),
                {"aid": agency_id},
            )
            return [r[0] for r in rows.fetchall()]
    except Exception as exc:
        logger.warning("get_agency_buildings(%s) failed: %s", agency_id, exc)
        return []


async def get_all_active_buildings() -> list[str]:
    """Return all active scheme_number values (super admin only)."""
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            rows = await session.execute(
                text("""
                    SELECT scheme_number
                    FROM core.schemes
                    WHERE is_archived IS NOT TRUE
                      AND COALESCE(is_test_data, FALSE) = FALSE
                    ORDER BY scheme_number
                """)
            )
            return [r[0] for r in rows.fetchall()]
    except Exception as exc:
        logger.warning("get_all_active_buildings failed: %s", exc)
        # Fallback to MongoDB
        try:
            from database import db
            cursor = db.buildings.find(
                {"is_archived": {"$ne": True}},
                {"building_id": 1, "plan_id": 1},
            )
            docs = await cursor.to_list(200)
            return [
                str(d.get("building_id") or d.get("plan_id", ""))
                for d in docs
                if d.get("building_id") or d.get("plan_id")
            ]
        except Exception as exc2:
            logger.warning("get_all_active_buildings Mongo fallback failed: %s", exc2)
            return []


# ── Private helpers ───────────────────────────────────────────────────────────

async def _resolve_platform_toggle(key: str) -> bool:
    """Generated function header.

    Function: _resolve_platform_toggle
    Path: backend/services/bi_toggle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        from db_postgres.repos.config_repo import get_global_feature_toggle_state
        result = await get_global_feature_toggle_state(key, default=False)
        return bool(result)
    except Exception as exc:
        logger.debug("_resolve_platform_toggle(%s) error: %s", key, exc)
        return False


async def _resolve_global_toggle(key: str) -> bool:
    """Generated function header.

    Function: _resolve_global_toggle
    Path: backend/services/bi_toggle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await _resolve_platform_toggle(key)


async def _resolve_agency_toggle(agency_id: str, key: str) -> bool:
    """Agency-level toggle resolver currently inherits platform defaults."""
    # For now, agencies inherit platform defaults. Future: add agency-specific overrides.
    return await _resolve_platform_toggle(key)


async def _resolve_building_toggle(building_id: str, key: str) -> bool:
    """Generated function header.

    Function: _resolve_building_toggle
    Path: backend/services/bi_toggle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    enabled, _used_legacy = await _resolve_building_toggle_with_source(building_id, key)
    return enabled


async def _resolve_building_toggle_with_source(building_id: str, key: str) -> tuple[bool, bool]:
    """Generated function header.

    Function: _resolve_building_toggle_with_source
    Path: backend/services/bi_toggle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        from services.cutover_config_service import is_cutover_feature_enabled
        result = await is_cutover_feature_enabled(building_id, key)
        if not result:
            # Also check legacy key
            legacy_result = await is_cutover_feature_enabled(building_id, BI_LEGACY_ENABLED)
            return bool(legacy_result), bool(legacy_result)
        return True, False
    except Exception as exc:
        logger.debug("_resolve_building_toggle(%s, %s) error: %s", building_id, key, exc)
        return False, False


async def _resolve_pg_primary(building_id: str | None) -> bool:
    """Generated function header.

    Function: _resolve_pg_primary
    Path: backend/services/bi_toggle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not building_id:
        return False
    try:
        from services.cutover_config_service import is_cutover_feature_enabled
        return bool(await is_cutover_feature_enabled(building_id, BI_PG_PRIMARY))
    except Exception:
        return False


async def _user_belongs_to_agency(user_id: str, agency_id: str) -> bool:
    """Generated function header.

    Function: _user_belongs_to_agency
    Path: backend/services/bi_toggle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT 1 FROM core.agency_memberships
                    WHERE agency_id = CAST(:aid AS UUID)
                      AND user_id = CAST(:uid AS UUID)
                      AND status = 'active'
                    LIMIT 1
                """),
                {"aid": agency_id, "uid": user_id},
            )
            return row.fetchone() is not None
    except Exception as exc:
        logger.debug("_user_belongs_to_agency(%s, %s) error: %s", user_id, agency_id, exc)
        return False


async def _get_strata_manager_profile_by_user(user_id: str) -> dict | None:
    """Generated function header.

    Function: _get_strata_manager_profile_by_user
    Path: backend/services/bi_toggle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT strata_manager_id::text, agency_id::text, status
                    FROM core.strata_manager_profiles
                    WHERE user_id = CAST(:uid AS UUID)
                      AND status = 'active'
                    LIMIT 1
                """),
                {"uid": user_id},
            )
            r = row.fetchone()
            if r:
                return {"strata_manager_id": r[0], "agency_id": r[1], "status": r[2]}
    except Exception as exc:
        logger.debug("_get_strata_manager_profile_by_user(%s) error: %s", user_id, exc)
    return None


async def _get_strata_manager_profile_by_id(strata_manager_id: str) -> dict | None:
    """Generated function header.

    Function: _get_strata_manager_profile_by_id
    Path: backend/services/bi_toggle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT strata_manager_id::text, user_id::text, agency_id::text, status
                    FROM core.strata_manager_profiles
                    WHERE strata_manager_id = CAST(:mid AS UUID)
                    LIMIT 1
                """),
                {"mid": strata_manager_id},
            )
            r = row.fetchone()
            if r:
                return {
                    "strata_manager_id": r[0],
                    "user_id": r[1],
                    "agency_id": r[2],
                    "status": r[3],
                }
    except Exception as exc:
        logger.debug("_get_strata_manager_profile_by_id(%s) error: %s", strata_manager_id, exc)
    return None


# ── Management entity helpers (added in sprint: hierarchy-normalisation) ──────

async def _resolve_management_entity_id(building_id: str) -> str | None:
    """Resolve the primary management_entity_id for a building (by scheme_number)."""
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT sma.management_entity_id::text
                    FROM core.scheme_management_assignments sma
                    JOIN core.schemes s ON s.scheme_id = sma.scheme_id
                    WHERE s.scheme_number = :bid
                      AND sma.status IN ('active', 'pending')
                      AND sma.is_primary = TRUE
                      AND COALESCE(sma.is_test_data, FALSE) = FALSE
                    ORDER BY sma.created_at DESC
                    LIMIT 1
                """),
                {"bid": building_id},
            )
            r = row.fetchone()
            return r[0] if r else None
    except Exception as exc:
        logger.debug("_resolve_management_entity_id(%s) error: %s", building_id, exc)
        return None


async def _resolve_management_entity_toggle(management_entity_id: str) -> bool:
    """Management entity inherits platform default for now.

    Future: add per-entity toggle overrides in core.feature_toggles
    with management_entity_id scope column.
    """
    return await _resolve_platform_toggle(BI_MANAGEMENT_ENTITY_ENABLED)


async def _get_management_entity_type(management_entity_id: str) -> str:
    """Generated function header.

    Function: _get_management_entity_type
    Path: backend/services/bi_toggle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT entity_type FROM core.management_entities
                    WHERE management_entity_id = CAST(:eid AS UUID)
                    LIMIT 1
                """),
                {"eid": management_entity_id},
            )
            r = row.fetchone()
            return r[0] if r else "unknown"
    except Exception as exc:
        logger.debug("_get_management_entity_type(%s) error: %s", management_entity_id, exc)
        return "unknown"


async def _user_has_active_appointment(user_id: str, building_id: str) -> bool:
    """True if the user has an active ec_internal_strata_manager or building_manager
    appointment for the given building (by scheme_number)."""
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT 1
                    FROM core.scheme_manager_appointments sma
                    JOIN core.schemes s ON s.scheme_id = sma.scheme_id
                    WHERE s.scheme_number = :bid
                      AND sma.user_id = CAST(:uid AS UUID)
                      AND sma.status = 'active'
                      AND sma.approved_by_ec = TRUE
                      AND sma.appointment_type IN (
                          'ec_internal_strata_manager', 'building_manager'
                      )
                      AND COALESCE(sma.is_test_data, FALSE) = FALSE
                    LIMIT 1
                """),
                {"bid": building_id, "uid": user_id},
            )
            return row.fetchone() is not None
    except Exception as exc:
        logger.debug("_user_has_active_appointment(%s, %s) error: %s", user_id, building_id, exc)
        return False


# ── Public management entity access helpers ───────────────────────────────────

async def resolve_management_entity_access(
    *,
    user_id: str,
    effective_role: str,
    management_entity_id: str,
) -> bool:
    """Return True if the user can access management-entity-scoped BI data.

    Rules:
      - super_admin: always True (subject to platform toggle).
      - strata_admin/strata_manager: must belong to the entity's agency
        or have an active appointment under this entity.
      - EC-internal managers: must have an active approved appointment.
    """
    me_on = await _resolve_management_entity_toggle(management_entity_id)
    if not me_on:
        me_on = await _resolve_global_toggle(BI_LEGACY_ENABLED)
    if not me_on:
        return False
    if effective_role == "super_admin":
        return True
    if effective_role in {"strata_admin", "strata_manager"}:
        # Check legacy agency membership
        entity_type = await _get_management_entity_type(management_entity_id)
        if entity_type == "agency":
            agency_id = await _get_agency_id_for_entity(management_entity_id)
            if agency_id and await _user_belongs_to_agency(user_id, agency_id):
                return True
        # Check appointment
        return await _user_has_appointment_for_entity(user_id, management_entity_id)
    if effective_role == "ec_member":
        return await _user_has_appointment_for_entity(user_id, management_entity_id)
    return False


async def get_management_entity_buildings(management_entity_id: str) -> list[str]:
    """Return scheme_number list for all buildings assigned to this management entity."""
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            rows = await session.execute(
                text("""
                    SELECT s.scheme_number
                    FROM core.scheme_management_assignments sma
                    JOIN core.schemes s ON s.scheme_id = sma.scheme_id
                    WHERE sma.management_entity_id = CAST(:eid AS UUID)
                      AND sma.status = 'active'
                      AND (s.is_archived IS NOT TRUE)
                      AND COALESCE(sma.is_test_data, FALSE) = FALSE
                    ORDER BY s.scheme_number
                """),
                {"eid": management_entity_id},
            )
            return [r[0] for r in rows.fetchall()]
    except Exception as exc:
        logger.warning("get_management_entity_buildings(%s) failed: %s", management_entity_id, exc)
        return []


async def _get_agency_id_for_entity(management_entity_id: str) -> str | None:
    """Generated function header.

    Function: _get_agency_id_for_entity
    Path: backend/services/bi_toggle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT source_agency_id::text FROM core.management_entities
                    WHERE management_entity_id = CAST(:eid AS UUID)
                    LIMIT 1
                """),
                {"eid": management_entity_id},
            )
            r = row.fetchone()
            return r[0] if r else None
    except Exception as exc:
        logger.debug("_get_agency_id_for_entity(%s) error: %s", management_entity_id, exc)
        return None


async def _user_has_appointment_for_entity(user_id: str, management_entity_id: str) -> bool:
    """Generated function header.

    Function: _user_has_appointment_for_entity
    Path: backend/services/bi_toggle_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        async with async_session_context() as session:
            row = await session.execute(
                text("""
                    SELECT 1 FROM core.scheme_manager_appointments
                    WHERE management_entity_id = CAST(:eid AS UUID)
                      AND user_id = CAST(:uid AS UUID)
                      AND status = 'active'
                      AND approved_by_ec = TRUE
                    LIMIT 1
                """),
                {"eid": management_entity_id, "uid": user_id},
            )
            return row.fetchone() is not None
    except Exception as exc:
        logger.debug(
            "_user_has_appointment_for_entity(%s, %s) error: %s",
            user_id, management_entity_id, exc,
        )
        return False
