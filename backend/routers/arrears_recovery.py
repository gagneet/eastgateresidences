"""
Arrears Recovery Router

Manages the structured recovery sequence for overdue levy payments.
Works alongside arrears_risk.py (scoring) and payment_plans.py (Form 1 plans).

State machine (auto + manual):
  30+ days overdue → reminder_sent
  60+ days         → formal_demand_sent
  90+ days         → lod_issued  (LOD fee posted to unit ledger)
  120+ days        → debt_collector_referred / solicitor_referred (manager chooses)

  Active payment plan → suppresses standard recovery until plan completes/cancels.

Endpoints:
  GET  /arrears-recovery                       — list all recovery actions for building
  GET  /arrears-recovery/summary               — building-level counts and totals
  GET  /arrears-recovery/{unit_number}         — recovery history + current state for unit
  POST /arrears-recovery/{unit_number}/action  — manually trigger next action
  POST /arrears-recovery/{unit_number}/suppress — suppress automated recovery
  POST /arrears-recovery/{unit_number}/resume   — resume automated recovery
  POST /arrears-recovery/run-automation         — trigger automation pass (also called by cron)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from models.arrears_recovery import (
    ManualActionRequest,
    RecoveryActionType,
    RecoverySummary,
)
from utils.auth import get_current_user, get_current_building
from utils.permissions import require_feature
from utils.route_guards import require_manager_surface

logger = logging.getLogger(__name__)
# GAP-FT-004: Gate the arrears recovery router behind the arrears_recovery toggle.
# Two dependencies, two different questions. require_feature asks whether the
# BUILDING has arrears recovery switched on; require_manager_surface asks whether
# this manager's job covers arrears. Both must pass, and neither implies the other.
# The second narrows nobody until an agency sets function_scoping_enabled — see
# docs/architecture/strata_management_staff_access_model.md.
router = APIRouter(
    prefix="/arrears-recovery",
    tags=["Finance"],
    dependencies=[
        Depends(require_feature("arrears_recovery")),
        Depends(require_manager_surface("arrears")),
    ],
)

# Default LOD fee in cents — can be overridden via building settings
_DEFAULT_LOD_FEE_CENTS = 22000  # $220.00

# Days-overdue thresholds that trigger each action
_THRESHOLDS = {
    30: RecoveryActionType.REMINDER,
    60: RecoveryActionType.FORMAL_DEMAND,
    90: RecoveryActionType.LOD_ISSUED,
}


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/arrears_recovery.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/routers/arrears_recovery.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _require_finance(user: dict) -> None:
    """Generated function header.

    Function: _require_finance
    Path: backend/routers/arrears_recovery.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _role = user.get("effective_role") or user.get("role", "guest")
    if _role not in {"super_admin", "strata_manager", "ec_member", "admin_staff"}:
        raise HTTPException(403, "Finance access required.")


def _require_manager(user: dict) -> None:
    """Generated function header.

    Function: _require_manager
    Path: backend/routers/arrears_recovery.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _role = user.get("effective_role") or user.get("role", "guest")
    if _role not in {"super_admin", "strata_manager", "ec_member"}:
        raise HTTPException(403, "Manager access required for this recovery action.")


async def _latest_action(db, building_id: str, unit_number: str) -> Optional[dict]:
    """Generated function header.

    Function: _latest_action
    Path: backend/routers/arrears_recovery.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    actions = await db.arrears_recovery_actions.find(
        {"building_id": building_id, "unit_number": unit_number},
        {"_id": 0},
    ).sort("triggered_at", -1).limit(1).to_list(1)
    return actions[0] if actions else None


async def _is_suppressed(db, building_id: str, unit_number: str) -> bool:
    """Return True if this unit has an active suppression or active payment plan."""
    latest = await _latest_action(db, building_id, unit_number)
    if not latest:
        return False
    if latest["action_type"] == RecoveryActionType.SUPPRESSED:
        until = latest.get("suppressed_until")
        if until is None:
            return True
        try:
            until_dt = datetime.fromisoformat(until)
            if not until_dt.tzinfo:
                until_dt = until_dt.replace(tzinfo=timezone.utc)
            if until_dt > datetime.now(timezone.utc):
                return True
        except (ValueError, TypeError):
            if until > _now():
                return True
    if latest["action_type"] == RecoveryActionType.PAYMENT_PLAN:
        plan_id = latest.get("active_plan_id")
        if plan_id:
            plan = await db.payment_plans.find_one(
                {"id": plan_id, "building_id": building_id, "status": "active"}, {"_id": 0}
            )
            if plan:
                return True
    return False


async def _post_lod_fee(db, building_id: str, unit_number: str, fee_cents: int, action_id: str) -> None:
    """Post LOD fee as a charge on the unit levy ledger."""
    try:
        now = _now()
        await db.unit_levy_ledger.update_one(
            {"building_id": building_id, "unit_number": unit_number},
            {"$push": {
                "charges": {
                    "charge_type": "lod_fee",
                    "amount_cents": fee_cents,
                    "description": "Letter of Demand — recovery fee",
                    "charged_at": now,
                    "recovery_action_id": action_id,
                }
            }, "$set": {"updated_at": now}},
        )
    except Exception as exc:
        logger.warning("Failed to post LOD fee for %s/%s: %s", building_id, unit_number, exc)


async def _record_action(
        db,
        building_id: str,
        unit_number: str,
        action_type: str,
        triggered_by: str,
        days_overdue: int,
        amount_cents: int,
        fee_cents: int = 0,
        notes: Optional[str] = None,
        suppressed_until: Optional[str] = None,
        active_plan_id: Optional[str] = None,
) -> dict:
    """Generated function header.

    Function: _record_action
    Path: backend/routers/arrears_recovery.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    action_id = str(uuid.uuid4())
    doc = {
        "id": action_id,
        "building_id": building_id,
        "unit_number": unit_number,
        "action_type": action_type,
        "triggered_at": _now(),
        "triggered_by": triggered_by,
        "days_overdue_at_trigger": days_overdue,
        "amount_overdue_cents": amount_cents,
        "fee_posted_cents": fee_cents,
        "notes": notes,
        "suppressed_until": suppressed_until,
        "active_plan_id": active_plan_id,
        "letter_template_used": action_type if action_type in {
            RecoveryActionType.REMINDER,
            RecoveryActionType.FORMAL_DEMAND,
            RecoveryActionType.LOD_ISSUED,
        } else None,
    }
    await db.arrears_recovery_actions.insert_one({**doc})
    return doc


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/summary")
async def recovery_summary(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Building-level counts and totals for all active recovery actions."""
    _require_finance(current_user)
    db = _get_db()

    pipeline = [
        {"$match": {"building_id": building_id}},
        {"$sort": {"unit_number": 1, "triggered_at": -1}},
        {"$group": {"_id": "$unit_number", "latest": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$latest"}},
    ]
    latest_per_unit = await db.arrears_recovery_actions.aggregate(pipeline).to_list(500)

    breakdown: dict = {}
    total_amount = 0
    suppressed_count = 0
    plan_count = 0

    for doc in latest_per_unit:
        at = doc.get("action_type", "unknown")
        breakdown[at] = breakdown.get(at, 0) + 1
        total_amount += doc.get("amount_overdue_cents", 0)
        if at == RecoveryActionType.SUPPRESSED:
            suppressed_count += 1
        if at == RecoveryActionType.PAYMENT_PLAN:
            plan_count += 1

    return RecoverySummary(
        building_id=building_id,
        total_units_in_recovery=len(latest_per_unit),
        breakdown=breakdown,
        total_amount_overdue_cents=total_amount,
        units_suppressed=suppressed_count,
        units_on_payment_plan=plan_count,
        generated_at=_now(),
    )


@router.get("")
async def list_recovery_actions(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        action_type: Optional[str] = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
):
    """List recovery actions for this building — most recent first, one row per unit."""
    _require_finance(current_user)
    db = _get_db()

    # Aggregate to latest action per unit
    pipeline = [
        {"$match": {"building_id": building_id}},
        {"$sort": {"unit_number": 1, "triggered_at": -1}},
        {"$group": {"_id": "$unit_number", "latest": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$latest"}},
    ]
    if action_type:
        pipeline.append({"$match": {"action_type": action_type}})
    pipeline.append({"$sort": {"triggered_at": -1}})
    pipeline.append({"$skip": (page - 1) * page_size})
    pipeline.append({"$limit": page_size})

    rows = await db.arrears_recovery_actions.aggregate(pipeline).to_list(page_size)
    for r in rows:
        r.pop("_id", None)

    return {"data": rows, "page": page, "page_size": page_size}


@router.get("/{unit_number}")
async def get_unit_recovery(
        unit_number: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Full recovery history + current state for a single unit."""
    _require_finance(current_user)
    db = _get_db()

    history = await db.arrears_recovery_actions.find(
        {"building_id": building_id, "unit_number": unit_number}, {"_id": 0}
    ).sort("triggered_at", -1).to_list(200)

    current = history[0] if history else None
    suppressed = await _is_suppressed(db, building_id, unit_number)

    return {
        "unit_number": unit_number,
        "building_id": building_id,
        "current_action": current.get("action_type") if current else None,
        "suppressed": suppressed,
        "history": history,
    }


@router.post("/{unit_number}/action", status_code=201)
async def trigger_manual_action(
        unit_number: str,
        payload: ManualActionRequest,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Manually trigger a recovery action for a unit."""
    if payload.action_type in RecoveryActionType.MANAGER_ONLY:
        _require_manager(current_user)
    else:
        _require_finance(current_user)

    if payload.action_type not in RecoveryActionType.ALL:
        raise HTTPException(400, f"Invalid action_type. Choose from: {RecoveryActionType.ALL}")

    db = _get_db()

    # Resolve current outstanding balance from arrears analytics
    from services.arrears_analytics import compute_arrears_risk
    units = await compute_arrears_risk(db, building_id)
    unit_data = next((u for u in units if u["unit_number"] == unit_number), None)
    amount_cents = int((unit_data["outstanding_aud"] * 100)) if unit_data else 0
    days_overdue = unit_data.get("days_overdue", 0) if unit_data else 0

    fee_cents = 0
    if payload.action_type == RecoveryActionType.LOD_ISSUED:
        fee_cents = payload.fee_override_cents if payload.fee_override_cents is not None else _DEFAULT_LOD_FEE_CENTS

    doc = await _record_action(
        db, building_id, unit_number,
        action_type=payload.action_type,
        triggered_by=current_user["id"],
        days_overdue=days_overdue,
        amount_cents=amount_cents,
        fee_cents=fee_cents,
        notes=payload.notes,
        suppressed_until=payload.suppressed_until,
    )

    if fee_cents > 0:
        await _post_lod_fee(db, building_id, unit_number, fee_cents, doc["id"])

    return {"message": f"Recovery action '{payload.action_type}' recorded.", "action_id": doc["id"]}


@router.post("/{unit_number}/suppress", status_code=200)
async def suppress_recovery(
        unit_number: str,
        until: Optional[str] = Query(None,
                                     description="ISO-8601 date — suppress until this date. Omit for indefinite."),
        notes: Optional[str] = Query(None),
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Suppress automated recovery for this unit (manual hold)."""
    _require_manager(current_user)
    db = _get_db()

    await _record_action(
        db, building_id, unit_number,
        action_type=RecoveryActionType.SUPPRESSED,
        triggered_by=current_user["id"],
        days_overdue=0, amount_cents=0,
        notes=notes, suppressed_until=until,
    )
    return {"message": "Recovery suppressed.", "unit_number": unit_number, "until": until}


@router.post("/{unit_number}/resume", status_code=200)
async def resume_recovery(
        unit_number: str,
        notes: Optional[str] = Query(None),
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Resume automated recovery after a suppression."""
    _require_manager(current_user)
    db = _get_db()

    # Clear suppression by setting suppressed_until to past
    await db.arrears_recovery_actions.update_many(
        {"building_id": building_id, "unit_number": unit_number,
         "action_type": RecoveryActionType.SUPPRESSED},
        {"$set": {"suppressed_until": "2000-01-01T00:00:00+00:00"}},
    )
    return {"message": "Recovery resumed.", "unit_number": unit_number}


@router.post("/run-automation", status_code=200)
async def run_recovery_automation(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """
    Trigger the automation pass for this building.
    Evaluates every overdue unit and fires the appropriate next action.
    Called by cron_arrears_recovery.py daily; can also be triggered manually.
    """
    _require_manager(current_user)
    db = _get_db()

    from services.arrears_analytics import compute_arrears_risk
    units = await compute_arrears_risk(db, building_id)

    triggered = []
    skipped = []

    for unit in units:
        unit_number = unit["unit_number"]
        days_overdue = unit.get("days_overdue", unit.get("days_since_last_payment", 0))
        amount_cents = int(unit["outstanding_aud"] * 100)

        if await _is_suppressed(db, building_id, unit_number):
            skipped.append({"unit_number": unit_number, "reason": "suppressed"})
            continue

        # Determine which action should fire next
        latest = await _latest_action(db, building_id, unit_number)
        latest_type = latest["action_type"] if latest else None

        target_action = None
        for threshold_days, action_type in sorted(_THRESHOLDS.items()):
            if days_overdue >= threshold_days:
                target_action = action_type

        if target_action is None or target_action == latest_type:
            skipped.append({"unit_number": unit_number, "reason": "no new action needed"})
            continue

        # Don't re-fire the same action type unless the debt has changed significantly
        if latest_type == target_action:
            skipped.append({"unit_number": unit_number, "reason": "already at this stage"})
            continue

        fee_cents = 0
        if target_action == RecoveryActionType.LOD_ISSUED:
            fee_cents = _DEFAULT_LOD_FEE_CENTS

        doc = await _record_action(
            db, building_id, unit_number,
            action_type=target_action,
            triggered_by="system",
            days_overdue=days_overdue,
            amount_cents=amount_cents,
            fee_cents=fee_cents,
        )

        if fee_cents > 0:
            await _post_lod_fee(db, building_id, unit_number, fee_cents, doc["id"])

        triggered.append({"unit_number": unit_number, "action": target_action})
        logger.info("Recovery action %s triggered for %s/%s", target_action, building_id, unit_number)

    return {
        "building_id": building_id,
        "triggered": len(triggered),
        "skipped": len(skipped),
        "actions": triggered,
        "run_at": _now(),
    }
