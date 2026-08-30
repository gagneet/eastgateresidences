# @featuretrace:pg-migration — Building general-settings read service: PG-first with Mongo fallback.
# Layer: service
# Data flow: GET /settings → get_general_settings_or_default(building_id, skip_pg) → config_repo.get_building_setting (PG) → db.settings (Mongo fallback).
# Related: backend/routers/settings.py
#           backend/db_postgres/repos/config_repo.py
# Toggle: settings_pg_reads_enabled (child of financial_integration_layer_v2)

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from database import db
from db_postgres.repos import config_repo
from models.cutover_status import DataSource
from services.cutover_status_service import resolve_read_source, resolve_write_source

logger = logging.getLogger(__name__)

_GENERAL_SETTINGS_SORT = [("updated_at", -1), ("created_at", -1), ("_id", -1)]
GENERAL_SETTINGS_KEY = "general.settings"
UNIT_DISPLAY_SETTINGS_KEY = "unit_display.rules"


async def _settings_read_source_is_postgres(building_id: str) -> bool:
    try:
        return await resolve_read_source(building_id, "settings") == DataSource.postgres
    except Exception as exc:
        logger.warning(
            "settings read-source resolution failed for building_id=%s, defaulting to Mongo: %s",
            building_id,
            exc,
        )
        return False


async def _settings_write_source_is_postgres(building_id: str) -> bool:
    try:
        return await resolve_write_source(building_id, "settings") == DataSource.postgres
    except Exception as exc:
        logger.warning(
            "settings write-source resolution failed for building_id=%s, defaulting to Mongo-compatible write: %s",
            building_id,
            exc,
        )
        return False


def _general_settings_query(building_id: str) -> Dict[str, Any]:
    """Generated function header.

    Function: _general_settings_query
    Path: backend/services/settings_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "$and": [
            {"building_id": building_id},
            {
                "$or": [
                    {"type": {"$exists": False}},
                    {"type": None},
                    {"type": "general"},
                ],
            },
        ],
    }


async def _list_general_settings_docs(
        building_id: str,
        projection: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
        settings_db=None,
):
    """Generated function header.

    Function: _list_general_settings_docs
    Path: backend/services/settings_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    active_db = settings_db if settings_db is not None else db
    cursor = active_db.settings.find(_general_settings_query(building_id), projection).sort(_GENERAL_SETTINGS_SORT)
    return await cursor.to_list(limit)


def _normalise_general_settings_doc(
        building_id: str,
        document: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Generated function header.

    Function: _normalise_general_settings_doc
    Path: backend/services/settings_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not isinstance(document, dict):
        return None

    normalised = dict(document)
    normalised.setdefault("building_id", building_id)
    normalised.setdefault("type", "general")
    return normalised


def _apply_projection(
        document: Optional[Dict[str, Any]],
        projection: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Generated function header.

    Function: _apply_projection
    Path: backend/services/settings_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if document is None or not projection:
        return document

    includes = {key for key, value in projection.items() if value and key != "_id"}
    excludes = {key for key, value in projection.items() if not value}

    if includes:
        projected = {key: value for key, value in document.items() if key in includes}
        if projection.get("_id", 1) and "_id" in document:
            projected["_id"] = document["_id"]
        return projected

    projected = dict(document)
    for key in excludes:
        projected.pop(key, None)
    return projected


async def _get_postgres_general_settings(
        building_id: str,
        projection: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Generated function header.

    Function: _get_postgres_general_settings
    Path: backend/services/settings_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    settings_doc = await config_repo.get_building_setting(
        building_id,
        GENERAL_SETTINGS_KEY,
        default=None,
    )
    normalised = _normalise_general_settings_doc(building_id, settings_doc)
    return _apply_projection(normalised, projection)


async def get_general_settings(
        building_id: str,
        projection: Optional[Dict[str, Any]] = None,
        settings_db=None,
        skip_pg: bool = False,
) -> Optional[Dict[str, Any]]:
    """Return the general settings document for *building_id*.

    Args:
        skip_pg: When True, bypass the PostgreSQL read path entirely and query
            MongoDB directly. Used by routers that gate PG access behind a feature
            toggle: when the toggle is OFF they pass ``skip_pg=True`` so no PG
            connection is attempted. Default False (PG-first, Mongo fallback).
    """
    if not skip_pg and (settings_db is None or settings_db is db):
        postgres_settings = await _get_postgres_general_settings(building_id, projection)
        if postgres_settings:
            return postgres_settings

    active_db = settings_db if settings_db is not None else db
    mongo_settings = await active_db.settings.find_one(
        _general_settings_query(building_id),
        projection,
        sort=_GENERAL_SETTINGS_SORT,
    )
    return _normalise_general_settings_doc(building_id, mongo_settings)


async def get_general_settings_or_default(
        building_id: Optional[str],
        projection: Optional[Dict[str, Any]] = None,
        fallback_building_id: Optional[str] = None,
        settings_db=None,
        skip_pg: bool = False,
) -> Dict[str, Any]:
    """Generated function header.

    Function: get_general_settings_or_default
    Path: backend/services/settings_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    resolved_building_id = building_id or fallback_building_id
    if not resolved_building_id:
        return {}
    return await get_general_settings(
        resolved_building_id, projection, settings_db=settings_db, skip_pg=skip_pg
    ) or {}


async def _upsert_general_settings_mongo(
        building_id: str,
        update_dict: Dict[str, Any],
) -> Dict[str, Any]:
    """Generated function header.

    Function: _upsert_general_settings_mongo
    Path: backend/services/settings_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    docs = await _list_general_settings_docs(building_id, {"_id": 1}, limit=None, settings_db=db)
    payload = {"building_id": building_id, "type": "general", **update_dict}

    if docs:
        primary_id = docs[0]["_id"]
        await db.settings.update_one({"_id": primary_id}, {"$set": payload})

        duplicate_ids = [doc["_id"] for doc in docs[1:]]
        if duplicate_ids:
            await db.settings.delete_many({"_id": {"$in": duplicate_ids}})
    else:
        await db.settings.insert_one(payload)

    return payload


async def upsert_general_settings(
        building_id: str,
        update_dict: Dict[str, Any],
        settings_db=None,
) -> Dict[str, Any]:
    """Generated function header.

    Function: upsert_general_settings
    Path: backend/services/settings_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if settings_db is None or settings_db is db:
        existing = await get_general_settings(building_id, {"_id": 0}, settings_db=settings_db) or {}
        payload = {
            **existing,
            **update_dict,
            "building_id": building_id,
            "type": "general",
        }
        await config_repo.upsert_building_setting(building_id, GENERAL_SETTINGS_KEY, payload)
        if await _settings_write_source_is_postgres(building_id):
            return await get_general_settings(building_id, {"_id": 0}, settings_db=settings_db) or payload
        await _upsert_general_settings_mongo(building_id, payload)
        return await get_general_settings(building_id, {"_id": 0}, settings_db=settings_db) or {}

    active_db = settings_db if settings_db is not None else db
    docs = await _list_general_settings_docs(building_id, {"_id": 1}, limit=None, settings_db=active_db)
    payload = {"building_id": building_id, "type": "general", **update_dict}

    if docs:
        primary_id = docs[0]["_id"]
        await active_db.settings.update_one({"_id": primary_id}, {"$set": payload})

        duplicate_ids = [doc["_id"] for doc in docs[1:]]
        if duplicate_ids:
            await active_db.settings.delete_many({"_id": {"$in": duplicate_ids}})
    else:
        await active_db.settings.insert_one(payload)

    return await get_general_settings(building_id, {"_id": 0}, settings_db=active_db) or {}


UNIT_DISPLAY_TYPE = "unit_display"


async def _get_postgres_unit_display_rules(building_id: str) -> list[Dict[str, Any]]:
    settings_doc = await config_repo.get_building_setting(
        building_id,
        UNIT_DISPLAY_SETTINGS_KEY,
        default=None,
    )
    rules = (settings_doc or {}).get("rules") if isinstance(settings_doc, dict) else None
    return rules if isinstance(rules, list) else []


async def get_unit_display_rules(building_id: str, settings_db=None) -> list[Dict[str, Any]]:
    """Return the per-building unit display prefix rules (empty when unset).

    Rules shape: ``[{"prefix": "TH", "min": 71, "max": 87, "pad": 3}, ...]``.
    Lots are stored/displayed with the matching rule's prefix + zero-padded
    number; buildings without rules keep their raw stored unit numbers.
    """
    if settings_db is None or settings_db is db:
        try:
            if await _settings_read_source_is_postgres(building_id):
                return await _get_postgres_unit_display_rules(building_id)
        except Exception as exc:
            logger.warning(
                "settings unit-display Postgres read failed for building_id=%s, falling back to Mongo: %s",
                building_id,
                exc,
            )

    active_db = settings_db if settings_db is not None else db
    doc = await active_db.settings.find_one(
        {"building_id": building_id, "type": UNIT_DISPLAY_TYPE},
        {"_id": 0, "rules": 1},
    )
    rules = (doc or {}).get("rules")
    return rules if isinstance(rules, list) else []


async def upsert_unit_display_rules(
        building_id: str,
        rules: list[Dict[str, Any]],
        settings_db=None,
) -> list[Dict[str, Any]]:
    """Persist the per-building unit display prefix rules (validated by the router)."""
    if settings_db is None or settings_db is db:
        payload = {"building_id": building_id, "type": UNIT_DISPLAY_TYPE, "rules": rules}
        await config_repo.upsert_building_setting(building_id, UNIT_DISPLAY_SETTINGS_KEY, payload)
        if await _settings_write_source_is_postgres(building_id):
            return await _get_postgres_unit_display_rules(building_id)

    active_db = settings_db if settings_db is not None else db
    await active_db.settings.update_one(
        {"building_id": building_id, "type": UNIT_DISPLAY_TYPE},
        {"$set": {"building_id": building_id, "type": UNIT_DISPLAY_TYPE, "rules": rules}},
        upsert=True,
    )
    return await get_unit_display_rules(building_id, settings_db=active_db)


async def ensure_general_settings_index(settings_db=None) -> None:
    """Generated function header.

    Function: ensure_general_settings_index
    Path: backend/services/settings_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    active_db = settings_db if settings_db is not None else db
    await active_db.settings.create_index(
        [("building_id", 1), ("type", 1)],
        unique=True,
        partialFilterExpression={"type": "general"},
        name="unique_general_settings_per_building",
    )
