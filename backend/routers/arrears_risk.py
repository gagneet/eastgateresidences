"""
GAP-FIN-006: Predictive Arrears Risk API.

Exposes the rule-based risk scoring from services/arrears_analytics.py.

Endpoints:
  GET  /arrears-risk              — scored risk list for all overdue units
  GET  /arrears-risk/{unit}       — single unit risk score
  GET  /arrears-risk/summary      — building-level summary (band distribution)
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException

from utils.auth import get_current_user, get_current_building
from utils.permissions import require_feature

logger = logging.getLogger(__name__)
# GAP-FT-004: Gate the predictive arrears risk router behind the arrears_risk toggle.
router = APIRouter(
    prefix="/arrears-risk",
    tags=["Finance"],
    dependencies=[Depends(require_feature("arrears_risk"))],
)


def _require_finance(user: dict) -> dict:
    """Generated function header.

    Function: _require_finance
    Path: backend/routers/arrears_risk.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    allowed = {"super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"}
    effective_role = user.get("effective_role") or user.get("role", "guest")
    if effective_role not in allowed:
        raise HTTPException(403, "Finance access required.")
    return user


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/arrears_risk.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


@router.get("/summary")
async def arrears_risk_summary(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Band distribution and totals for the building's arrears risk profile."""
    _require_finance(current_user)
    from services.arrears_analytics import compute_arrears_risk
    db = _get_db()

    units = await compute_arrears_risk(db, building_id)

    bands = {"CRITICAL": 0, "HIGH": 0, "MODERATE": 0, "LOW": 0}
    total_outstanding = 0.0
    hardship_count = 0
    form1_eligible = 0

    for u in units:
        bands[u["band"]] = bands.get(u["band"], 0) + 1
        total_outstanding += u.get("outstanding_aud", 0)
        if u.get("hardship_flag"):
            hardship_count += 1
        if u.get("form1_eligible"):
            form1_eligible += 1

    return {
        "building_id": building_id,
        "units_with_arrears": len(units),
        "total_outstanding_aud": round(total_outstanding, 2),
        "band_distribution": bands,
        "hardship_flagged": hardship_count,
        "form1_eligible": form1_eligible,
    }


@router.get("")
async def list_arrears_risk(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Risk-scored list of all units with outstanding levies, sorted by score descending."""
    _require_finance(current_user)
    from services.arrears_analytics import compute_arrears_risk
    db = _get_db()

    units = await compute_arrears_risk(db, building_id)
    return {"data": units, "total": len(units)}


@router.get("/{unit_number}")
async def unit_arrears_risk(
        unit_number: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Risk score for a single unit."""
    _require_finance(current_user)
    from services.arrears_analytics import compute_arrears_risk
    db = _get_db()

    all_units = await compute_arrears_risk(db, building_id)
    match = next((u for u in all_units if u["unit_number"] == unit_number), None)
    if not match:
        # Unit exists but has no arrears — return zero-risk
        return {
            "unit_number": unit_number,
            "outstanding_aud": 0.0,
            "score": 0,
            "band": "LOW",
            "signals": [],
            "hardship_flag": False,
            "form1_eligible": False,
        }
    return match
