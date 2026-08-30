"""
Global Building Risk Index router.

Authenticated endpoint:
  GET /intelligence/risk-index
      Returns the full risk score for the caller's building (auth required).
  POST /intelligence/risk-index/refresh
      Force-recompute the risk score (admin only).

Public endpoint (no auth, no PII):
  GET /intelligence/risk-index/public/{slug}
      Returns a public-safe building report for SEO / public pages.
"""
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Depends

from services.risk_index_service import (
    compute_building_risk_score,
    get_public_building_report,
)
from utils.auth import effective_role, get_current_user, get_current_building
from utils.helpers import create_audit_log

router = APIRouter(prefix="/intelligence/risk-index", tags=["Building Risk Index"])
logger = logging.getLogger(__name__)

_ALLOWED_ROLES = {
    "owner",
    "ec_member", "strata_admin",
    "strata_manager",
    "super_admin",
    "real_estate_agent",
}
_ADMIN_ROLES = {"super_admin", "ec_member", "strata_admin", "strata_manager"}


def _require_risk_access(current_user: dict = Depends(get_current_user)) -> dict:
    """Generated function header.

    Function: _require_risk_access
    Path: backend/routers/risk_index.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in _ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Access denied")
    return current_user


def _require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Generated function header.

    Function: _require_admin
    Path: backend/routers/risk_index.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# ── Authenticated endpoint ─────────────────────────────────────────────────────

@router.get("")
async def get_risk_index(
        current_user: dict = Depends(_require_risk_access),
        building_id: str = Depends(get_current_building),
):
    """
    Return the Global Building Risk Index for the caller's building.

    The score is derived from asset health, financial stability, reserve
    adequacy, capital shock risk, and maintenance forecasts.
    Requires an authenticated session with an appropriate role.
    """
    try:
        result = await compute_building_risk_score(building_id)
        return result.model_dump()
    except Exception as exc:
        logger.error("risk_index: compute failed for %s — %s", building_id, exc)
        raise HTTPException(status_code=500, detail="Risk score computation failed")


@router.post("/refresh")
async def refresh_risk_index(
        current_user: dict = Depends(_require_admin),
        building_id: str = Depends(get_current_building),
):
    """
    Force-recompute the risk index for the current building.
    Restricted to admin roles. Audit-logs the action asynchronously.
    """
    try:
        result = await compute_building_risk_score(building_id)
        asyncio.create_task(
            create_audit_log(
                user_id=current_user.get("id", ""),
                action="risk_index_refresh",
                resource_type="building_risk_index",
                resource_id=building_id,
                details={"risk_score": result.risk_score, "risk_level": result.risk_level},
            )
        )
        return result.model_dump()
    except Exception as exc:
        logger.error("risk_index: refresh failed for %s — %s", building_id, exc)
        raise HTTPException(status_code=500, detail="Risk score refresh failed")


# ── Public endpoint (no auth required) ────────────────────────────────────────

@router.get("/public/{slug}")
async def get_public_building_report_endpoint(slug: str):
    """
    Public building risk report — safe for SEO pages and public consumption.

    Does NOT expose:
    - owner / tenant names or contact details
    - financial transaction records
    - authentication or payment information

    Returns 404 if the building slug is not recognised.
    Rate-limited via the shared API middleware.
    """
    report = await get_public_building_report(slug)
    if report is None:
        raise HTTPException(status_code=404, detail="Building not found")
    return report.model_dump()
