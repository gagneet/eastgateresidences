# @featuretrace:strata-web-portal-finance-ingest — Per-building Strata Web portal connection config
#   API: view/update masked config, and trigger a scrape+stage for the selected building.
# Layer: router
# Data flow: GET/PUT /settings/strata-web-portal <-> strata_web_portal_config_service; POST .../sync
#            -> (out-of-repo scraper if available) -> strata_web_portal_ingest.run() -> snapshot.
# Related: backend/services/strata_web_portal_config_service.py,
#          backend/scripts/ingest/strata_web_portal_ingest.py
# Collection: strata_web_portal_configs, staging_strata_web_snapshots
"""Per-building Strata Web portal connection settings + scrape trigger.

RBAC: super_admin or strata_admin (company admin) only — connection config is an
administrative trust boundary, not an operational one. The building is resolved from the request's
building context (the building switcher's X-Building-ID for super_admin), so an admin edits the
config for whichever building is currently selected.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from models.strata_web_portal import StrataWebPortalConfigResponse, StrataWebPortalConfigUpdate
from models.user import UserRole
from services.strata_web_portal_config_service import (
    get_portal_config_masked,
    get_portal_credentials,
    record_sync_result,
    upsert_portal_config,
)
from utils.auth import get_current_building, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Strata Web Portal"])

_ADMIN_ROLES = {UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN}


def _require_admin(current_user: dict) -> None:
    role = current_user.get("effective_role") or current_user.get("role", "guest")
    if role not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Super admin or strata admin role required.")


@router.get("/settings/strata-web-portal", response_model=StrataWebPortalConfigResponse)
async def get_strata_web_portal_config(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Return the current building's Strata Web portal config with the password masked
    (``password_set`` only — the password itself is never returned)."""
    _require_admin(current_user)
    return await get_portal_config_masked(building_id)


@router.put("/settings/strata-web-portal", response_model=StrataWebPortalConfigResponse)
async def update_strata_web_portal_config(
        data: StrataWebPortalConfigUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Create/update the current building's portal config. A provided password is encrypted at
    rest (Fernet); an omitted password leaves the stored one unchanged."""
    _require_admin(current_user)
    return await upsert_portal_config(
        building_id, data.model_dump(exclude_unset=True), updated_by=current_user.get("id"),
    )


@router.post("/settings/strata-web-portal/sync")
async def sync_strata_web_portal(
        year: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Trigger a scrape+stage for the current building.

    1. Resolve the building's ENABLED, decrypted credentials (403/400 if missing/disabled).
    2. If the out-of-repo scraper package is installed, invoke it to refresh the scraped Mongo
       collections (strata_owners/building_summaries/bank_accounts) for this building using its own
       config — otherwise skip that step (staging still runs over whatever was last scraped).
    3. Stage a dated snapshot via the in-repo ingest and record the outcome.
    """
    _require_admin(current_user)

    creds = await get_portal_credentials(building_id)
    if creds is None:
        raise HTTPException(
            status_code=400,
            detail="No enabled Strata Web portal config for this building. Add the connection "
                   "details and enable it first.",
        )
    if not (creds.get("portal_url") and creds.get("username") and creds.get("password")):
        raise HTTPException(status_code=400, detail="Portal config is incomplete (url/username/password).")

    if not year:
        from routers.finance import _resolve_default_levy_year
        from datetime import date
        year = await _resolve_default_levy_year(building_id) or str(date.today().year)

    scraped = False
    scrape_detail = "scraper package not installed — staged from previously-scraped collections"
    try:
        # Contract for an optional out-of-repo scraper package: a coroutine that logs into the
        # portal with these creds and writes strata_owners/building_summaries/bank_accounts for
        # building_id. No such package is currently wired into this repo — this stays an
        # ImportError-guarded hook rather than a real dependency (see strata_sync/ for the actual,
        # in-repo browser-scrape flow, which is a separate mechanism from this one).
        from strata_web.scraper import scrape_building_to_mongo  # type: ignore
        from database import db as _db
        await scrape_building_to_mongo(_db, **creds)
        scraped = True
        scrape_detail = "portal scraped via strata_web scraper"
    except ImportError:
        logger.info("Strata Web scraper package not available; staging from existing collections.")
    except Exception as exc:  # scraper present but failed — surface, don't crash the request
        await record_sync_result(building_id, status=f"scrape_failed: {type(exc).__name__}")
        raise HTTPException(status_code=502, detail=f"Portal scrape failed: {exc}") from exc

    # Stage the snapshot from the (freshly or previously) scraped collections.
    from scripts.ingest.strata_web_portal_ingest import run as ingest_run
    result = await ingest_run(building_id, str(year), None, dry_run=False)
    await record_sync_result(building_id, status="ok" if scraped else "staged_without_live_scrape")

    return {
        "building_id": building_id,
        "financial_year": str(year),
        "scraped": scraped,
        "detail": scrape_detail,
        "staged": result,
    }
