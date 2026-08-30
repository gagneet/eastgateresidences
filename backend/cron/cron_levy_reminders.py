"""
Levy reminders and arrears notification cron.

Workflows served:
  - levy_run_quarterly    : Trigger quarterly levy run (generate levy notices for all units).
  - arrears_notice_tier1  : Send first-tier arrears notices for overdue levies (7+ days).
  - levy_arrears_notifier : Send escalated arrears notices and create audit records.

Scheduling (examples):
  - levy_run_quarterly    : 0 8 1 1,4,7,10 *  (first day of each quarter)
  - arrears_notice_tier1  : 0 9 * * 1          (every Monday 9am)
  - levy_arrears_notifier : 0 10 1,15 * *       (1st and 15th of each month)
"""
import asyncio
import logging
import sys
import os
from datetime import datetime, timezone, timedelta

# Add project root to sys.path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import db
from db_postgres.repos import config_repo
from services.levy_reminder_settings_service import (
    get_levy_reminder_settings_or_default,
)
from utils.helpers import get_current_utc_iso

logger = logging.getLogger(__name__)


async def _is_feature_enabled(building_id: str, feature_key: str) -> bool:
    """Resolve feature flags via the Postgres-backed toggle repo."""
    value = await config_repo.resolve_feature_toggle(
        building_id,
        feature_key,
        default=True,
    )
    return True if value is None else bool(value)


async def _get_buildings() -> list:
    """Return all active building IDs."""
    buildings = await db._db.buildings.find(
        {"is_active": {"$ne": False}}, {"_id": 0, "plan_id": 1, "building_id": 1}
    ).to_list(100)
    return [b.get("plan_id") or b.get("building_id") for b in buildings if b.get("plan_id") or b.get("building_id")]


async def run_levy_run(building_id: str | None = None) -> dict:
    """
    Quarterly levy run: identify units with levies due and generate levy notices.
    Operates across all buildings (or a specific building_id when provided).
    Returns a summary dict.
    """
    now = get_current_utc_iso()
    buildings = [building_id] if building_id else await _get_buildings()
    total_notices = 0

    for bid in buildings:
        # GAP-FT-003: Respect the levy_reminders feature toggle. If disabled
        # for this building (or globally), skip it — consistent with what the
        # Feature Toggles admin UI implies when the toggle is turned off.
        if not await _is_feature_enabled(bid, "levy_reminders"):
            logger.info("levy_reminders feature toggle disabled for building %s — skipping levy run", bid)
            continue

        # Find units with outstanding levies for the current financial year
        year = str(datetime.now(timezone.utc).year)
        ledger_docs = await db.unit_levy_ledger.find(
            {"building_id": bid, "year": {"$regex": f"^{year}"}},
            {"_id": 0, "unit_number": 1, "admin_balance": 1, "sinking_balance": 1, "lot_number": 1}
        ).to_list(None)

        for ledger in ledger_docs:
            admin_bal = ledger.get("admin_balance", 0) or 0
            sinking_bal = ledger.get("sinking_balance", 0) or 0
            outstanding = round(admin_bal + sinking_bal, 2)
            if outstanding <= 0:
                continue  # No outstanding levies for this unit

            # Upsert levy notice record to avoid duplicates on repeated runs
            levy_notice_id = f"levy-notice-{bid}-{ledger['unit_number']}-{year}"
            await db.notifications.update_one(
                {"id": levy_notice_id},
                {
                    "$set": {
                        "id": levy_notice_id,
                        "building_id": bid,
                        "type": "levy_notice",
                        "unit_number": ledger.get("unit_number"),
                        "lot_number": ledger.get("lot_number"),
                        "amount_due": outstanding,
                        "financial_year": year,
                        "created_at": now,
                        "status": "generated",
                    }
                },
                upsert=True,
            )
            total_notices += 1

    logger.info("Levy run complete: %d notices generated for %d buildings", total_notices, len(buildings))
    return {"notices_generated": total_notices, "buildings_processed": len(buildings), "run_at": now}


# Hardcoded thresholds only used when settings absent AND tier > 1 is requested directly
_TIER_THRESHOLDS = {1: (7, 30), 2: (30, 90), 3: (90, 9999)}


async def _get_building_settings(building_id: str) -> dict:
    """Read per-building levy reminder settings from Postgres with Mongo fallback."""
    return await get_levy_reminder_settings_or_default(building_id, settings_db=db)


async def run_levy_reminders(building_id: str | None = None, tier: int = 1) -> dict:
    """
    Send levy arrears reminders, respecting per-building cadence settings.

    When settings are present, overdue_days from levy_reminder_settings determines
    which units receive reminders (any unit whose balance has been overdue for a number
    of days that matches overdue_days).  The tier parameter is used as a fallback when
    no settings document exists.

    Tier 1 (default): overdue 7–30 days → friendly reminder email.
    Tier 2: overdue 30–90 days → formal notice.
    Tier 3: overdue 90+ days → refer to strata manager / EC.
    """
    now = datetime.now(timezone.utc)
    buildings = [building_id] if building_id else await _get_buildings()
    reminders_sent = 0

    for bid in buildings:
        # GAP-FT-003: Check the feature_toggles collection FIRST. This is the
        # authoritative on/off switch controlled via the Feature Toggles admin UI.
        # The levy_reminder_settings.enabled gate below is a secondary cadence
        # control — both must be on to send reminders.
        if not await _is_feature_enabled(bid, "levy_reminders"):
            logger.info("levy_reminders feature toggle disabled for building %s — skipping", bid)
            continue

        # GAP-FIN-012: read cadence config per building
        bld_settings = await _get_building_settings(bid)

        if not bld_settings.get("enabled", True):
            logger.info("Levy reminders disabled for building %s — skipping", bid)
            continue

        threshold_cents = int(bld_settings.get("overdue_threshold_cents", 100))
        overdue_days_list: list[int] = bld_settings.get("overdue_days") or []

        # Fallback to tier thresholds when no overdue_days configured
        if not overdue_days_list:
            min_days, max_days = _TIER_THRESHOLDS.get(tier, (7, 30))
        else:
            min_days, max_days = min(overdue_days_list), max(overdue_days_list) + 1

        year = str(now.year)
        overdue = await db.unit_levy_ledger.find(
            {
                "building_id": bid,
                "year": {"$regex": f"^{year}"},
                "$or": [
                    {"admin_balance": {"$gt": 0}},
                    {"sinking_balance": {"$gt": 0}},
                ],
            },
            {"_id": 0, "unit_number": 1, "admin_balance": 1, "sinking_balance": 1, "due_date": 1}
        ).to_list(None)

        for ledger in overdue:
            due_date_str = ledger.get("due_date")
            if not due_date_str:
                continue
            try:
                due_date = datetime.fromisoformat(due_date_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue

            days_overdue = (now - due_date).days

            # Check against configured overdue_days list or tier range
            if overdue_days_list:
                if days_overdue not in overdue_days_list:
                    continue
            else:
                if not (min_days <= days_overdue < max_days):
                    continue

            outstanding_cents = int(round(
                ((ledger.get("admin_balance") or 0) + (ledger.get("sinking_balance") or 0)) * 100
            ))
            if outstanding_cents < threshold_cents:
                continue

            outstanding = round(outstanding_cents / 100, 2)
            await db.email_sent_log.insert_one({
                "building_id": bid,
                "unit_number": ledger["unit_number"],
                "type": f"levy_arrears_tier{tier}",
                "days_overdue": days_overdue,
                "amount_outstanding": outstanding,
                "sent_at": now.isoformat(),
                "status": "queued",
            })
            reminders_sent += 1

    logger.info("Levy reminders tier=%d: %d reminders queued", tier, reminders_sent)
    return {"tier": tier, "reminders_sent": reminders_sent, "run_at": now.isoformat()}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Levy reminders cron")
    parser.add_argument("--action", choices=["levy_run", "reminders"], default="reminders")
    parser.add_argument("--tier", type=int, default=1, help="Reminder tier (1, 2, or 3)")
    parser.add_argument("--building", default=None, help="Specific building_id (optional)")
    args = parser.parse_args()


    async def main():
        """Generated function header.

        Function: main
        Path: backend/cron/cron_levy_reminders.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if args.action == "levy_run":
            result = await run_levy_run(args.building)
        else:
            result = await run_levy_reminders(args.building, args.tier)
        print(result)


    asyncio.run(main())
