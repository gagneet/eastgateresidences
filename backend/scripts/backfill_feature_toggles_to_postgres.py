from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import _db
from db_postgres.repos import config_repo
from db_postgres.session import async_session_context, set_tenant
from seeds.feature_toggles import DEFAULT_FEATURES

logger = logging.getLogger("backfill_feature_toggles_to_postgres")


def _normalise_list(value):
    """Generated function header.

    Function: _normalise_list
    Path: backend/scripts/backfill_feature_toggles_to_postgres.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _as_timestamp_param(value):
    """Generated function header.

    Function: _as_timestamp_param
    Path: backend/scripts/backfill_feature_toggles_to_postgres.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        normalised = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalised)
        except ValueError:
            return datetime.now(timezone.utc)
    if hasattr(value, "isoformat"):
        return value
    return datetime.now(timezone.utc)


async def _load_existing_pg_globals() -> dict[str, dict]:
    """Generated function header.

    Function: _load_existing_pg_globals
    Path: backend/scripts/backfill_feature_toggles_to_postgres.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    rows = await config_repo.list_resolved_feature_toggles(None)
    return {row["feature_key"]: row for row in rows}


async def _upsert_global_toggle(payload: dict) -> None:
    """Generated function header.

    Function: _upsert_global_toggle
    Path: backend/scripts/backfill_feature_toggles_to_postgres.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    async with async_session_context() as session:
        await session.execute(
            text(
                """
                INSERT INTO core.feature_toggles
                    (feature_key, feature_name, description, category, is_enabled,
                     icon, routes, allowed_roles, depends_on,
                     seeded_version, last_modified_by, last_modified_at, created_at)
                VALUES
                    (:feature_key, :feature_name, :description, :category, :is_enabled,
                     :icon, :routes, :allowed_roles, :depends_on,
                     :seeded_version, :last_modified_by,
                     CAST(:last_modified_at AS timestamptz),
                     CAST(:created_at AS timestamptz))
                ON CONFLICT (feature_key)
                DO UPDATE
                    SET feature_name = EXCLUDED.feature_name,
                        description = EXCLUDED.description,
                        category = EXCLUDED.category,
                        is_enabled = EXCLUDED.is_enabled,
                        icon = EXCLUDED.icon,
                        routes = EXCLUDED.routes,
                        allowed_roles = EXCLUDED.allowed_roles,
                        depends_on = EXCLUDED.depends_on,
                        seeded_version = EXCLUDED.seeded_version,
                        last_modified_by = COALESCE(EXCLUDED.last_modified_by, core.feature_toggles.last_modified_by),
                        last_modified_at = EXCLUDED.last_modified_at
                """
            ),
            payload,
        )


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/backfill_feature_toggles_to_postgres.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    mongo_globals = await _db.feature_toggles.find({"building_id": None}, {"_id": 0}).to_list(None)
    mongo_overrides = await _db.feature_toggles.find(
        {"building_id": {"$ne": None}},
        {"_id": 0},
    ).to_list(None)
    mongo_user_overrides = await _db.user_feature_access.find({}, {"_id": 0}).to_list(None)

    seed_by_key = {feature["feature_key"]: feature for feature in DEFAULT_FEATURES}
    mongo_by_key = {feature["feature_key"]: feature for feature in mongo_globals}
    existing_pg_by_key = await _load_existing_pg_globals()
    override_keys = {feature["feature_key"] for feature in mongo_overrides if feature.get("feature_key")}

    all_keys = sorted(set(seed_by_key) | set(mongo_by_key) | set(existing_pg_by_key) | override_keys)
    global_count = 0
    override_count = 0
    user_override_count = 0
    skipped_overrides = 0

    for feature_key in all_keys:
        seed_doc = seed_by_key.get(feature_key, {})
        mongo_doc = mongo_by_key.get(feature_key, {})
        existing_pg = existing_pg_by_key.get(feature_key, {})

        actor_id = await config_repo.resolve_actor_user_id(
            mongo_doc.get("updated_by") or existing_pg.get("updated_by"),
            require_existing=False,
        )
        created_at = mongo_doc.get("created_at") or existing_pg.get("created_at")
        updated_at = mongo_doc.get("updated_at") or existing_pg.get("updated_at") or created_at

        payload = {
            "feature_key": feature_key,
            "feature_name": mongo_doc.get("feature_name")
            or seed_doc.get("feature_name")
            or existing_pg.get("feature_name")
            or feature_key.replace("_", " ").title(),
            "description": mongo_doc.get("description")
            or seed_doc.get("description")
            or existing_pg.get("description"),
            "category": mongo_doc.get("category")
            or seed_doc.get("category")
            or existing_pg.get("category")
            or "system",
            "is_enabled": bool(
                mongo_doc.get("is_enabled")
                if "is_enabled" in mongo_doc else
                seed_doc.get("is_enabled")
                if "is_enabled" in seed_doc else
                existing_pg.get("is_enabled", False)
            ),
            "icon": mongo_doc.get("icon")
            or seed_doc.get("icon")
            or existing_pg.get("icon"),
            "routes": _normalise_list(
                mongo_doc.get("routes")
                if "routes" in mongo_doc else
                seed_doc.get("routes")
                if "routes" in seed_doc else
                existing_pg.get("routes")
            ),
            "allowed_roles": _normalise_list(
                mongo_doc.get("allowed_roles")
                or mongo_doc.get("roles")
                or seed_doc.get("allowed_roles")
                or seed_doc.get("roles")
                or existing_pg.get("allowed_roles")
            ),
            "depends_on": _normalise_list(
                mongo_doc.get("depends_on")
                if "depends_on" in mongo_doc else
                seed_doc.get("depends_on")
                if "depends_on" in seed_doc else
                existing_pg.get("depends_on")
            ),
            "seeded_version": 1,
            "last_modified_by": actor_id,
            "last_modified_at": _as_timestamp_param(updated_at),
            "created_at": _as_timestamp_param(created_at or updated_at),
        }
        await _upsert_global_toggle(payload)
        global_count += 1

    fallback_actor_id = await config_repo.resolve_actor_user_id(require_existing=True)
    for override in mongo_overrides:
        building_id = override.get("building_id")
        feature_key = override.get("feature_key")
        if not building_id or not feature_key:
            skipped_overrides += 1
            logger.warning("Skipping malformed override: %s", override)
            continue

        scheme = await config_repo.resolve_scheme_context(building_id)
        if scheme is None:
            skipped_overrides += 1
            logger.warning("Skipping override for unresolved building_id=%s", building_id)
            continue

        actor_id = await config_repo.resolve_actor_user_id(
            override.get("updated_by"),
            require_existing=True,
        ) or fallback_actor_id

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            await session.execute(
                text(
                    """
                    INSERT INTO core.feature_toggle_overrides
                        (tenant_id, scheme_id, feature_key, is_enabled, set_by, set_at, reason)
                    VALUES
                        (:tenant_id, :scheme_id, :feature_key, :is_enabled, :set_by,
                         CAST(:set_at AS timestamptz), :reason)
                    ON CONFLICT (scheme_id, feature_key)
                    DO UPDATE
                        SET is_enabled = EXCLUDED.is_enabled,
                            set_by = EXCLUDED.set_by,
                            set_at = EXCLUDED.set_at,
                            reason = EXCLUDED.reason
                    """
                ),
                {
                    "tenant_id": str(scheme["tenant_id"]),
                    "scheme_id": str(scheme["scheme_id"]),
                    "feature_key": feature_key,
                    "is_enabled": bool(override.get("is_enabled", override.get("enabled", True))),
                    "set_by": actor_id,
                    "set_at": _as_timestamp_param(override.get("updated_at")),
                    "reason": "Backfilled from Mongo feature_toggles override",
                },
            )
        override_count += 1

    for override in mongo_user_overrides:
        if not override.get("user_id") or not override.get("feature_key"):
            continue
        created = await config_repo.upsert_user_feature_override(
            override["user_id"],
            override["feature_key"],
            bool(override.get("is_enabled", True)),
            granted_by=override.get("granted_by"),
            notes=override.get("notes"),
        )
        if created is not None:
            user_override_count += 1

    logger.info(
        "Feature toggle backfill complete: globals=%s overrides=%s user_overrides=%s skipped_overrides=%s",
        global_count,
        override_count,
        user_override_count,
        skipped_overrides,
    )


if __name__ == "__main__":
    asyncio.run(main())
