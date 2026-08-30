# @featuretrace:levy — Owner-facing levy breakdown, building-health, savings endpoints.
# Layer: router
# Data flow: frontend/src/pages/dashboard/MyFinancesPage.jsx -> GET /owner-finance/levy-breakdown?unit_number=,
#            /owner-finance/health-explanation, /owner-finance/savings-summary?unit_number= ->
#            services/owner_finance_service.py -> unit_levy_ledger + building_summaries (building-scoped).
# Related: backend/services/owner_finance_service.py
#           backend/utils/unit_number.py (authorise_owner_unit — gates the unit_number parameter)
#           frontend/src/pages/dashboard/MyFinancesPage.jsx
#           frontend/src/hooks/useActiveUnit.ts
# Collection: unit_levy_ledger, building_summaries, savings_events, user_units
# Tests: tests/backend/test_owner_finance.py, tests/backend/test_owner_finance_unit_scope.py
"""Owner Financial Dashboard endpoints."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from database import db
from services.owner_finance_service import get_levy_breakdown, get_savings_per_lot
from services.settings_service import get_unit_display_rules
from utils.auth import get_current_user, get_current_building
from utils.unit_number import (
    BlankUnitRequestError,
    UnitNotOwnedError,
    authorise_owner_unit,
)

router = APIRouter(prefix="/owner-finance", tags=["Owner Finance"])

OWNER_ROLES = {"owner", "strata_admin", "ec_member", "strata_manager", "super_admin"}


async def _unit_display_rules_safe(building_id: str) -> list:
    """Per-building unit display rules, or [] when settings are unavailable.

    Mirrors ``routers.finance._unit_display_rules_safe``: canonical unit
    resolution must degrade to generic candidate expansion rather than fail an
    owner-facing read when the settings source cannot be reached (startup races,
    mocked test databases).
    """
    try:
        return await get_unit_display_rules(building_id)
    except Exception:  # noqa: BLE001 — degrade to generic expansion, never fail the read
        return []


async def _resolve_owner_finance_unit(
        current_user: dict,
        building_id: str,
        requested_unit: str | None = None,
) -> str | None:
    """Resolve the unit shown on owner-facing finance cards.

    ``requested_unit`` is the unit the calling page is currently displaying —
    a multi-unit owner who switches units in the sidebar must get that unit's
    figures, not whichever one their account happens to default to
    (GAP-IDENTITY-UNIT-SWITCH-001). It is authorised against the caller's own
    active links before it is honoured; an unowned unit is a 403, never a
    silent fallback to the default unit, because quietly answering about a
    different unit than the one asked for is how a wrong figure gets trusted.

    With no parameter the behaviour is unchanged: newer owner data is linked
    through user_units; older accounts may still carry unit_number/lot_id
    directly on the user document.
    """
    if requested_unit is not None:
        # Rules are passed for the same reason every finance route passes them:
        # without them, candidate expansion falls back to two hardcoded prefixes
        # and a display variant in any other building would 403 its real owner.
        # BlankUnitRequestError / UnitNotOwnedError propagate — the callers map
        # them to HTTP 400 / 403 respectively.
        return await authorise_owner_unit(
            db,
            current_user,
            building_id,
            requested_unit,
            rules=await _unit_display_rules_safe(building_id),
        )

    direct_unit = current_user.get("unit_number") or current_user.get("lot_id")
    if direct_unit:
        return str(direct_unit)

    owned_units = current_user.get("owned_units") or []
    if owned_units:
        return str(owned_units[0])

    user_id = current_user.get("id")
    if user_id:
        link = await db.user_units.find_one(
            {
                "building_id": building_id,
                "user_id": user_id,
                "is_active": True,
            },
            {"_id": 0, "unit_number": 1},
            sort=[("is_primary", -1), ("created_at", 1)],
        )
        if link and link.get("unit_number"):
            return str(link["unit_number"])

    email = (current_user.get("email") or "").strip()
    full_name = (current_user.get("full_name") or "").strip()
    legacy_filters = []
    if email:
        legacy_filters.extend([{"owner_email": email}, {"owner_email_b": email}])
    if full_name:
        legacy_filters.extend([{"owner_name": full_name}, {"owner_name_b": full_name}])

    if legacy_filters:
        unit = await db.units.find_one(
            {"building_id": building_id, "$or": legacy_filters},
            {"_id": 0, "unit_number": 1},
        )
        if unit and unit.get("unit_number"):
            return str(unit["unit_number"])

    return None


class VolunteerCreditAllocation(BaseModel):
    """One participant's share of the credit pool."""
    model_config = ConfigDict(extra="ignore")

    user_id: str
    unit_number: str
    hours_contributed: float
    credit_cents: int


class VolunteerCreditApplyResponse(BaseModel):
    """POST /owner-finance/volunteer-events/{id}/apply-credits — credit-application summary."""
    model_config = ConfigDict(extra="ignore")

    event_id: str
    total_credit_cents: int
    credits_applied: int
    allocations: List[VolunteerCreditAllocation]


@router.get("/levy-breakdown")
async def levy_breakdown(
        unit_number: Optional[str] = Query(
            None,
            description=(
                "Unit to report on. Owner-facing pages pass the unit currently active in "
                "the sidebar unit switcher so a multi-unit owner sees the unit they are "
                "looking at. Must be a unit the caller is actively linked to (403 "
                "otherwise). Omit to use the account's own unit."
            ),
        ),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Where does my money go? Per-category levy breakdown for the logged-in owner."""
    role = current_user.get("effective_role") or current_user.get("role")
    if role not in OWNER_ROLES:
        raise HTTPException(status_code=403, detail="Owner access required")
    try:
        resolved_unit = await _resolve_owner_finance_unit(current_user, building_id, unit_number)
    except BlankUnitRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except UnitNotOwnedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    if not resolved_unit:
        raise HTTPException(status_code=400, detail="No unit associated with this account")
    return await get_levy_breakdown(resolved_unit, building_id)


@router.get("/savings-summary")
async def savings_summary(
        unit_number: Optional[str] = Query(
            None,
            description=(
                "Unit to report on. Owner-facing pages pass the unit currently active in "
                "the sidebar unit switcher so a multi-unit owner sees the unit they are "
                "looking at. Must be a unit the caller is actively linked to (403 "
                "otherwise). Omit to use the account's own unit."
            ),
        ),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Owner's personalised share of building savings YTD."""
    role = current_user.get("effective_role") or current_user.get("role")
    if role not in OWNER_ROLES:
        raise HTTPException(status_code=403, detail="Owner access required")
    try:
        resolved_unit = await _resolve_owner_finance_unit(current_user, building_id, unit_number)
    except BlankUnitRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except UnitNotOwnedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    if not resolved_unit:
        raise HTTPException(status_code=400, detail="No unit associated")
    return await get_savings_per_lot(resolved_unit, building_id)


@router.get("/health-explanation")
async def building_health_explanation(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Plain-English building health summary for owners.

    Building-wide, not per-unit — it takes no ``unit_number`` parameter, and
    switching units must not make it refetch or change.
    """
    role = current_user.get("effective_role") or current_user.get("role")
    if role not in OWNER_ROLES:
        raise HTTPException(status_code=403, detail="Owner access required")

    # Use the TenantCollection wrapper (db.building_summaries), not the raw
    # Motor collection (db._db.building_summaries), so tenant isolation logic
    # is honoured.  The explicit building_id filter is kept for clarity.
    summary = await db.building_summaries.find_one(
        {"building_id": building_id},
        {"_id": 0}
    )
    if not summary:
        return {"health_score": None, "grade": "N/A", "components": [], "is_authoritative_finance_metric": False}

    # A summary row can exist while the score itself is unmeasured: the writer stores
    # None for "not computed" (East Gate sits at health_coverage 0.1 with no financial
    # inputs restored). `.get(key, 0)` does NOT default here — the key is present and
    # holds None — so this used to reach `None >= 85` and raise, returning HTTP 500.
    # Defaulting to 0 would be worse than the crash: it grades a building D on the
    # strength of data nobody has. Report it as unmeasured, exactly as the no-summary
    # branch above does.
    score = summary.get("health_score")
    if score is None:
        return {
            "building_id": building_id,
            "health_score": None,
            "grade": "N/A",
            "components": [],
            "last_computed_at": summary.get("last_computed_at") or summary.get("computed_at"),
            "is_authoritative_finance_metric": False,
        }

    grade = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D"

    arrears = summary.get("arrears_rate_pct", 0) or 0
    sinking_pct = summary.get("sinking_fund_pct", 0) or 0
    open_wos = summary.get("open_work_orders", 0) or 0
    compliance_overdue = summary.get("compliance_overdue_count", 0) or 0

    components = [
        {
            "key": "sinking_fund",
            "label": "Sinking fund",
            "score_pct": sinking_pct,
            "status": "good" if sinking_pct >= 80 else "warning" if sinking_pct >= 50 else "poor",
            "plain_english": (
                    f"{sinking_pct:.0f}% of forecast reserve needs funded"
                    + (
                        " — excellent" if sinking_pct >= 90 else " — healthy" if sinking_pct >= 80 else " — below target")
            ),
        },
        {
            "key": "arrears",
            "label": "Arrears rate",
            "score_pct": max(0, 100 - arrears * 10),
            "status": "good" if arrears < 2 else "warning" if arrears < 5 else "poor",
            "plain_english": (
                f"Only {arrears:.1f}% of lots have unpaid levies"
                if arrears < 2
                else f"{arrears:.1f}% arrears rate (industry avg 4-6%)"
            ),
        },
        {
            "key": "maintenance",
            "label": "Maintenance",
            "score_pct": 100 if open_wos == 0 else max(40, 100 - open_wos * 5),
            "status": "good" if open_wos == 0 else "warning",
            "plain_english": (
                "All work orders within SLA"
                if open_wos == 0
                else f"{open_wos} open work orders"
            ),
        },
        {
            "key": "compliance",
            "label": "Compliance",
            "score_pct": 100 if compliance_overdue == 0 else 60,
            "status": "good" if compliance_overdue == 0 else "warning",
            "plain_english": (
                "All compliance items current"
                if compliance_overdue == 0
                else f"{compliance_overdue} compliance item{'s' if compliance_overdue > 1 else ''} "
                     f"{'are' if compliance_overdue > 1 else 'is'} overdue"
            ),
        },
    ]

    return {
        "building_id": building_id,
        "health_score": score,
        "grade": grade,
        "components": components,
        "last_computed_at": summary.get("last_computed_at"),
        "health_score_history": summary.get("health_score_history", []),
        # GAP-FIN-014: building_summaries is a non-canonical, scraper/derived
        # source — never an authoritative finance figure. Frontend must caption
        # this accordingly rather than presenting it as ledger-grade truth.
        "is_authoritative_finance_metric": False,
    }


@router.post("/volunteer-events/{event_id}/apply-credits", response_model=VolunteerCreditApplyResponse)
async def apply_volunteer_event_credits(
        event_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Apply levy credits for a completed volunteer event."""
    role = current_user.get("effective_role") or current_user.get("role")
    if role not in {"strata_admin", "ec_member", "strata_manager", "super_admin"}:
        raise HTTPException(status_code=403, detail="EC member or manager required")
    from services.volunteer_credits_service import apply_volunteer_credits
    try:
        return await apply_volunteer_credits(event_id, current_user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
