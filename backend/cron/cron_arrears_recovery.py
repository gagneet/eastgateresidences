"""
Cron: Daily Arrears Recovery Automation

Runs once daily for each active building. Calls the /arrears-recovery/run-automation
endpoint logic directly (no HTTP round-trip) for each building.

Schedule: run daily at 08:00 AEST (22:00 UTC previous day).

Usage:
  python3 cron/cron_arrears_recovery.py
  python3 cron/cron_arrears_recovery.py --building 13195
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger("cron_arrears_recovery")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


async def run_for_building(building_id: str) -> dict:
    """Generated function header.

    Function: run_for_building
    Path: backend/cron/cron_arrears_recovery.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    from request_context import set_ctx_building_id
    from services.arrears_analytics import compute_arrears_risk
    from models.arrears_recovery import RecoveryActionType
    import uuid

    set_ctx_building_id(building_id)

    _DEFAULT_LOD_FEE_CENTS = 22000
    _THRESHOLDS = {
        30: RecoveryActionType.REMINDER,
        60: RecoveryActionType.FORMAL_DEMAND,
        90: RecoveryActionType.LOD_ISSUED,
    }

    now_iso = datetime.now(timezone.utc).isoformat()

    async def _is_suppressed(unit_number: str) -> bool:
        """Generated function header.

        Function: _is_suppressed
        Path: backend/cron/cron_arrears_recovery.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        actions = await db.arrears_recovery_actions.find(
            {"building_id": building_id, "unit_number": unit_number}, {"_id": 0}
        ).sort("triggered_at", -1).limit(1).to_list(1)
        if not actions:
            return False
        latest = actions[0]
        if latest["action_type"] == RecoveryActionType.SUPPRESSED:
            until = latest.get("suppressed_until")
            if until is None or until > now_iso:
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

    async def _latest_action(unit_number: str):
        """Generated function header.

        Function: _latest_action
        Path: backend/cron/cron_arrears_recovery.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        actions = await db.arrears_recovery_actions.find(
            {"building_id": building_id, "unit_number": unit_number}, {"_id": 0}
        ).sort("triggered_at", -1).limit(1).to_list(1)
        return actions[0] if actions else None

    units = await compute_arrears_risk(db, building_id)
    triggered, skipped = [], []

    for unit in units:
        unit_number = unit["unit_number"]
        days_overdue = unit.get("days_overdue", unit.get("days_since_last_payment", 0))
        amount_cents = int(unit["outstanding_aud"] * 100)

        if await _is_suppressed(unit_number):
            skipped.append(unit_number)
            continue

        target_action = None
        for threshold_days, action_type in sorted(_THRESHOLDS.items()):
            if days_overdue >= threshold_days:
                target_action = action_type

        if target_action is None:
            skipped.append(unit_number)
            continue

        latest = await _latest_action(unit_number)
        latest_type = latest["action_type"] if latest else None
        if latest_type == target_action:
            skipped.append(unit_number)
            continue

        action_id = str(uuid.uuid4())
        fee_cents = _DEFAULT_LOD_FEE_CENTS if target_action == RecoveryActionType.LOD_ISSUED else 0

        doc = {
            "id": action_id,
            "building_id": building_id,
            "unit_number": unit_number,
            "action_type": target_action,
            "triggered_at": now_iso,
            "triggered_by": "cron",
            "days_overdue_at_trigger": days_overdue,
            "amount_overdue_cents": amount_cents,
            "fee_posted_cents": fee_cents,
            "letter_template_used": target_action if target_action in {
                RecoveryActionType.REMINDER,
                RecoveryActionType.FORMAL_DEMAND,
                RecoveryActionType.LOD_ISSUED,
            } else None,
        }
        await db.arrears_recovery_actions.insert_one(doc)

        if fee_cents > 0:
            try:
                await db.unit_levy_ledger.update_one(
                    {"building_id": building_id, "unit_number": unit_number},
                    {"$push": {
                        "charges": {
                            "charge_type": "lod_fee",
                            "amount_cents": fee_cents,
                            "description": "Letter of Demand — recovery fee",
                            "charged_at": now_iso,
                            "recovery_action_id": action_id,
                        }
                    }, "$set": {"updated_at": now_iso}},
                )
            except Exception as exc:
                logger.warning("LOD fee post failed for %s/%s: %s", building_id, unit_number, exc)

        triggered.append({"unit_number": unit_number, "action": target_action})
        logger.info("[%s] %s → %s (%d days overdue)", building_id, unit_number, target_action, days_overdue)

    logger.info("[%s] automation complete: %d triggered, %d skipped", building_id, len(triggered), len(skipped))
    return {"building_id": building_id, "triggered": len(triggered), "skipped": len(skipped)}


async def main(target_building: str | None = None):
    """Generated function header.

    Function: main
    Path: backend/cron/cron_arrears_recovery.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    from request_context import set_ctx_building_id

    if target_building:
        building_ids = [target_building]
    else:
        # Fetch all active, non-archived buildings
        set_ctx_building_id(None)
        buildings = await db.buildings.find(
            {"is_archived": {"$ne": True}, "is_active": {"$ne": False}},
            {"id": 1, "_id": 0},
        ).to_list(200)
        building_ids = [b["id"] for b in buildings if b.get("id")]

    logger.info("Running arrears recovery for %d building(s)", len(building_ids))
    for bid in building_ids:
        try:
            await run_for_building(bid)
        except Exception as exc:
            logger.error("Error processing building %s: %s", bid, exc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--building", help="Run for a single building_id only")
    args = parser.parse_args()
    asyncio.run(main(args.building))
