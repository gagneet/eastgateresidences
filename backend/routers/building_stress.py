"""
Building Financial Stress router — reserve adequacy, risk scores, unit levy
impact and mitigation options for the building financial stress dashboard.
"""
from datetime import datetime, timezone, timedelta

import logging
from fastapi import APIRouter, HTTPException, Depends

from database import db
from services.building_stress_service import (
    compute_reserve_adequacy_ratio,
    compute_risk_score,
    compute_unit_levy_impact,
    generate_mitigation_options,
    project_reserve_timeline,
    compute_and_store_snapshot,
)
from utils.auth import get_current_user, get_current_building

router = APIRouter(prefix="/building-stress", tags=["Building Stress"])
logger = logging.getLogger(__name__)

ALLOWED_ROLES = {"owner", "strata_admin", "ec_member", "strata_manager", "super_admin"}
ADMIN_ROLES = {"super_admin", "ec_member", "strata_admin", "strata_manager"}


def _require_stress(current_user: dict = Depends(get_current_user)) -> dict:
    """Generated function header.

    Function: _require_stress
    Path: backend/routers/building_stress.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = current_user.get("effective_role") or current_user.get("role", "")
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Access denied")
    return current_user


def _require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Generated function header.

    Function: _require_admin
    Path: backend/routers/building_stress.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = current_user.get("effective_role") or current_user.get("role", "")
    if role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# ── Snapshot ───────────────────────────────────────────────────────────────

@router.get("/snapshot")
async def get_snapshot(
        current_user: dict = Depends(_require_stress),
        building_id: str = Depends(get_current_building)
):
    """Return the latest building stress snapshot; recompute if stale (>24 h)."""

    existing = await db.building_financial_health.find_one(
        {"building_id": building_id},
        sort=[("computed_at", -1)]
    )

    now = datetime.now(timezone.utc)
    if existing:
        try:
            computed_at = datetime.fromisoformat(
                existing["computed_at"].replace("Z", "+00:00")
            )
            if (now - computed_at) < timedelta(hours=24):
                existing.pop("_id", None)
                return existing
        except Exception as exc:
            logger.warning("building_stress: cached snapshot timestamp parse failed, recomputing: %s", exc)

    # Recompute
    return await compute_and_store_snapshot(building_id)


# ── Timeline ───────────────────────────────────────────────────────────────

@router.get("/timeline")
async def get_timeline(
        years: int = 10,
        current_user: dict = Depends(_require_stress),
        building_id: str = Depends(get_current_building)
):
    """10-year sinking fund reserve projection."""
    return await project_reserve_timeline(building_id, years=years)


# ── Unit Impact ────────────────────────────────────────────────────────────

@router.get("/unit-impact/{unit_number}")
async def get_unit_impact(
        unit_number: str,
        current_user: dict = Depends(_require_stress),
        building_id: str = Depends(get_current_building)
):
    """Projected special levy cost for a specific unit."""
    adequacy = await compute_reserve_adequacy_ratio(building_id)
    deficit = adequacy.get("deficit", 0.0)
    return await compute_unit_levy_impact(unit_number, building_id, deficit)


# ── Mitigation Options ─────────────────────────────────────────────────────

@router.get("/mitigation-options")
async def get_mitigation_options(
        current_user: dict = Depends(_require_stress),
        building_id: str = Depends(get_current_building)
):
    """List mitigation strategies to reduce building financial stress."""
    adequacy = await compute_reserve_adequacy_ratio(building_id)
    risk = await compute_risk_score(building_id)
    deficit = adequacy.get("deficit", 0.0)
    risk_score = risk.get("overall_risk_score", 0.0)
    return await generate_mitigation_options(building_id, deficit, risk_score)


# ── Admin Refresh ──────────────────────────────────────────────────────────

@router.post("/refresh")
async def refresh_snapshot(
        current_user: dict = Depends(_require_admin),
        building_id: str = Depends(get_current_building)
):
    """Admin-triggered full recalculation of the building stress snapshot."""
    return await compute_and_store_snapshot(building_id)
