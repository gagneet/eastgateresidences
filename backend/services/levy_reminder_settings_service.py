# @featuretrace:levy — Levy reminder settings: Postgres-first read/write with MongoDB fallback; ForceMongoDb sentinel for tests.
# Layer: service
# Data flow: levy reminder cron / PUT /levy-reminder-settings → get/upsert_levy_reminder_settings → core.building_settings (Postgres) + levy_reminder_settings (Mongo) (building-scoped).
# Related: backend/routers/finance.py
#           backend/cron/cron_levy_reminders.py
#           backend/db_postgres/repos/config_repo.py
# Table: core.building_settings
# Collection: levy_reminder_settings
from __future__ import annotations

import logging
from typing import Any, Optional

from db_postgres.repos import config_repo

logger = logging.getLogger(__name__)

LEVY_REMINDER_SETTINGS_KEY = "finance.levy_reminders"

DEFAULT_LEVY_REMINDER_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "pre_due_days": [14, 7, 3],
    "overdue_days": [1, 7, 14, 30],
    "overdue_threshold_cents": 100,
    "channels": {"email": True, "in_app": True, "sms": False},
    "max_escalation_tier": 4,
}

# Sentinel type used by callers that want to force the MongoDB path in tests
# without depending on unittest.mock internals.
class _ForceMongoDb:
    """Pass an instance of this class as settings_db to skip Postgres reads."""


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/services/levy_reminder_settings_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db

    return db


def _use_postgres(settings_db=None) -> bool:
    """Return True when the caller has not explicitly opted out of Postgres reads.

    The caller signals a Mongo-only path by passing a ``_ForceMongoDb``
    instance as ``settings_db``.  Any real Motor collection object (or None,
    which triggers the default Motor DB) will use the Postgres-first path.
    Previously this checked ``isinstance(settings_db, Mock)`` which coupled
    production code to ``unittest.mock`` and broke when non-Mock test doubles
    were used.
    """
    return not isinstance(settings_db, _ForceMongoDb)


def normalise_levy_reminder_settings(
    building_id: str,
    document: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Generated function header.

    Function: normalise_levy_reminder_settings
    Path: backend/services/levy_reminder_settings_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not isinstance(document, dict):
        return None

    normalised = dict(document)
    reminder_days = normalised.get("reminder_days")
    if normalised.get("pre_due_days") is None and isinstance(reminder_days, list):
        normalised["pre_due_days"] = reminder_days

    channels = normalised.get("channels")
    if not isinstance(channels, dict):
        channels = {}

    merged = {
        **DEFAULT_LEVY_REMINDER_SETTINGS,
        **normalised,
        "channels": {
            **DEFAULT_LEVY_REMINDER_SETTINGS["channels"],
            **channels,
        },
    }
    merged["building_id"] = building_id
    merged["reminder_days"] = list(merged.get("pre_due_days") or [])
    return merged


async def _get_postgres_levy_reminder_settings(building_id: str) -> Optional[dict[str, Any]]:
    """Read levy reminder settings from Postgres core.building_settings.

    Returns None (never raises) so the caller can safely fall back to MongoDB
    when Postgres is unavailable, misconfigured, or the scheme is not yet
    onboarded.
    """
    try:
        settings_doc = await config_repo.get_building_setting(
            building_id,
            LEVY_REMINDER_SETTINGS_KEY,
            default=None,
        )
        return normalise_levy_reminder_settings(building_id, settings_doc)
    except Exception as exc:
        logger.warning(
            "levy_reminder_settings: Postgres read failed for building %s, "
            "falling back to MongoDB: %s",
            building_id,
            exc,
        )
        return None


async def get_levy_reminder_settings(
    building_id: str,
    *,
    settings_db=None,
) -> Optional[dict[str, Any]]:
    """Generated function header.

    Function: get_levy_reminder_settings
    Path: backend/services/levy_reminder_settings_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if _use_postgres(settings_db):
        postgres_settings = await _get_postgres_levy_reminder_settings(building_id)
        if postgres_settings:
            return postgres_settings

    active_db = settings_db if (settings_db is not None and not isinstance(settings_db, _ForceMongoDb)) else _get_db()

    collection_doc = await active_db.levy_reminder_settings.find_one(
        {"building_id": building_id},
        {"_id": 0},
    )
    normalised_collection = normalise_levy_reminder_settings(building_id, collection_doc)
    if normalised_collection:
        return normalised_collection

    legacy_doc = await active_db.settings.find_one(
        {"building_id": building_id, "type": "levy_reminder_settings"},
        {"_id": 0},
    )
    return normalise_levy_reminder_settings(building_id, legacy_doc)


async def get_levy_reminder_settings_or_default(
    building_id: str,
    *,
    settings_db=None,
) -> dict[str, Any]:
    """Generated function header.

    Function: get_levy_reminder_settings_or_default
    Path: backend/services/levy_reminder_settings_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    settings_doc = await get_levy_reminder_settings(building_id, settings_db=settings_db)
    return settings_doc or normalise_levy_reminder_settings(
        building_id,
        {"building_id": building_id},
    ) or dict(DEFAULT_LEVY_REMINDER_SETTINGS)


def _build_postgres_payload(settings_doc: dict[str, Any]) -> dict[str, Any]:
    """Generated function header.

    Function: _build_postgres_payload
    Path: backend/services/levy_reminder_settings_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    payload = dict(settings_doc)
    payload.pop("reminder_days", None)
    return payload


def _build_legacy_settings_payload(settings_doc: dict[str, Any]) -> dict[str, Any]:
    """Generated function header.

    Function: _build_legacy_settings_payload
    Path: backend/services/levy_reminder_settings_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    payload = dict(settings_doc)
    payload["type"] = "levy_reminder_settings"
    payload["reminder_days"] = list(payload.get("pre_due_days") or [])
    return payload


async def upsert_levy_reminder_settings(
    building_id: str,
    update_dict: dict[str, Any],
    *,
    updated_by: str | None = None,
    settings_db=None,
) -> dict[str, Any]:
    """Generated function header.

    Function: upsert_levy_reminder_settings
    Path: backend/services/levy_reminder_settings_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    existing = await get_levy_reminder_settings(building_id, settings_db=settings_db) or {}
    merged = normalise_levy_reminder_settings(
        building_id,
        {
            **existing,
            **update_dict,
            "building_id": building_id,
            "updated_by": updated_by or update_dict.get("updated_by") or existing.get("updated_by"),
            "updated_at": update_dict.get("updated_at") or existing.get("updated_at"),
        },
    )
    if merged is None:
        raise RuntimeError(f"Could not normalise levy reminder settings for building_id={building_id!r}.")

    postgres_payload = _build_postgres_payload(merged)
    if _use_postgres(settings_db):
        await config_repo.upsert_building_setting(
            building_id,
            LEVY_REMINDER_SETTINGS_KEY,
            postgres_payload,
            set_by=updated_by,
        )

    active_db = settings_db if (settings_db is not None and not isinstance(settings_db, _ForceMongoDb)) else _get_db()
    await active_db.levy_reminder_settings.update_one(
        {"building_id": building_id},
        {"$set": postgres_payload},
        upsert=True,
    )
    await active_db.settings.update_one(
        {"building_id": building_id, "type": "levy_reminder_settings"},
        {"$set": _build_legacy_settings_payload(merged)},
        upsert=True,
    )

    return merged
