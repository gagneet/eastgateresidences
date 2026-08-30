# @featuretrace:strata-web-portal-finance-ingest — Per-building Strata Web portal connection config:
#   encrypted-at-rest credential storage + masked reads + the scraper-boundary credential accessor.
# Layer: service
# Data flow: PUT /settings/strata-web-portal -> strata_web_portal_configs (Mongo, building-scoped,
#            password Fernet-encrypted). Scraper boundary: get_portal_credentials() -> out-of-repo
#            scraper (not present in this repo; see routers/strata_web_portal.py's ImportError
#            fallback) which populates strata_owners/building_summaries/bank_accounts, then
#            strata_web_portal_ingest.py stages snapshots.
# Related: backend/routers/strata_web_portal.py, backend/scripts/ingest/strata_web_portal_ingest.py,
#          backend/utils/encryption.py
# Collection: strata_web_portal_configs
"""Per-building Strata Web portal connection config.

The password is encrypted at rest with Fernet (utils.encryption) and is NEVER returned by any API
— masked reads expose only ``password_set``. The single decrypted-credential accessor
(``get_portal_credentials``) is for the scraper boundary ONLY and must never be wired to an HTTP
response. Uses the raw ``db._db`` collection with an explicit building_id filter so it is safe to
call from scripts/cron outside a request's tenant context.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from database import db
from utils.encryption import decrypt_field, encrypt_field

_COLLECTION = "strata_web_portal_configs"
# Persisted field holding the Fernet ciphertext. The plaintext "password" only ever exists
# transiently in a request body or the scraper-boundary accessor's return value.
_ENC_FIELD = "portal_password_enc"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _raw(building_id: str) -> Optional[dict]:
    return await db._db[_COLLECTION].find_one({"building_id": building_id}, {"_id": 0})


async def get_portal_config_masked(building_id: str) -> dict[str, Any]:
    """Masked config for API responses — password NEVER included, only ``password_set``."""
    doc = await _raw(building_id) or {}
    return {
        "building_id": building_id,
        "portal_url": doc.get("portal_url"),
        "scheme_number": doc.get("scheme_number"),
        "username": doc.get("username"),
        "password_set": bool(doc.get(_ENC_FIELD)),
        "enabled": bool(doc.get("enabled", False)),
        "last_synced_at": doc.get("last_synced_at"),
        "last_sync_status": doc.get("last_sync_status"),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


async def upsert_portal_config(building_id: str, data: dict, *, updated_by: Optional[str]) -> dict[str, Any]:
    """Upsert a building's portal config. Only provided fields change. A provided ``password`` is
    encrypted into ``portal_password_enc``; an omitted/empty password leaves the stored one intact.
    Returns the masked config."""
    update: dict[str, Any] = {"building_id": building_id, "updated_at": _now_iso()}
    if updated_by:
        update["updated_by"] = updated_by
    for f in ("portal_url", "scheme_number", "username", "enabled"):
        if data.get(f) is not None:
            update[f] = data[f]
    pw = data.get("password")
    if pw:  # write-only, encrypt at rest; never stored or logged in plaintext
        update[_ENC_FIELD] = encrypt_field(str(pw))
    await db._db[_COLLECTION].update_one(
        {"building_id": building_id}, {"$set": update}, upsert=True,
    )
    return await get_portal_config_masked(building_id)


async def record_sync_result(building_id: str, *, status: str, synced_at: Optional[str] = None) -> None:
    """Stamp the last sync outcome (called by the sync trigger / ingest). Never touches credentials."""
    await db._db[_COLLECTION].update_one(
        {"building_id": building_id},
        {"$set": {"last_sync_status": status, "last_synced_at": synced_at or _now_iso()}},
        upsert=True,
    )


async def get_portal_credentials(building_id: str) -> Optional[dict[str, Any]]:
    """SCRAPER-BOUNDARY ONLY. Return decrypted connection details for an ENABLED building, or None.

    The out-of-repo scraper calls this to know which portal, scheme
    and credentials to use for the selected building — the per-building replacement for the global
    PORTAL_EMAIL/PORTAL_PASSWORD env vars. NEVER return this dict from an HTTP endpoint.
    """
    doc = await _raw(building_id)
    if not doc or not doc.get("enabled"):
        return None
    return {
        "building_id": building_id,
        "portal_url": doc.get("portal_url"),
        "scheme_number": doc.get("scheme_number"),
        "username": doc.get("username"),
        "password": decrypt_field(doc.get(_ENC_FIELD)),
    }
