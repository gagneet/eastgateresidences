"""
NSW Strata Hub compliance reminder cron.

Workflow served:
  - nsw_strata_hub_reminder : Remind strata managers to submit annual returns
    and compliance reports to the NSW Strata Hub.

NOTE: East Gate Residences is in the ACT, not NSW. This module is provided
for multi-tenant support of NSW buildings. ACT buildings skip the NSW Hub checks
but will still receive general compliance reminders.

Scheduling (example):
  0 9 1 * *   # First of each month at 9am
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import db
from domain.jurisdictional_rules import rule_engine
from utils.helpers import get_current_utc_iso

logger = logging.getLogger(__name__)

# NSW Strata Hub annual return window (typically October–December)
NSW_HUB_DUE_MONTH = 12  # December
NSW_HUB_REMINDER_MONTHS_AHEAD = 2


async def run_nsw_hub_reminders(building_id: str | None = None) -> dict:
    """
    Check if any buildings have an upcoming NSW Strata Hub submission due
    and create notification records for the strata manager.

    For ACT buildings (building_id="13195" or jurisdiction="ACT"), skip
    the NSW-specific check but still log a general compliance reminder.
    """
    now = datetime.now(timezone.utc)
    buildings_query: dict = {"is_active": {"$ne": False}}
    if building_id:
        buildings_query["$or"] = [{"plan_id": building_id}, {"building_id": building_id}]

    buildings = await db._db.buildings.find(buildings_query, {"_id": 0}).to_list(100)
    if not buildings:
        if building_id:
            logger.warning(
                "No matching building found for NSW compliance reminder run: %s",
                building_id,
            )
        else:
            logger.warning("No active buildings found for NSW compliance reminder run")
        return {
            "reminders_created": 0,
            "buildings_checked": 0,
            "run_at": now.isoformat(),
        }

    reminders_created = 0

    for building in buildings:
        bid = building.get("plan_id") or building.get("building_id", "")
        jurisdiction = (building.get("jurisdiction") or "ACT").upper()

        # Use rule engine: any jurisdiction with strata_hub_report_months defined
        # gets hub-style reminders; all others get the general compliance reminder.
        try:
            jur_rules = rule_engine.get_effective_rules(jurisdiction)
        except ValueError:
            jur_rules = {}
        has_hub_requirement = jur_rules.get("strata_hub_report_months") is not None

        if has_hub_requirement:
            # Remind if we are within the configured months of the annual due date
            months_until_due = (NSW_HUB_DUE_MONTH - now.month) % 12
            if months_until_due > NSW_HUB_REMINDER_MONTHS_AHEAD:
                continue  # Not yet time to remind

            notification = {
                "building_id": bid,
                "type": "compliance_reminder",
                "sub_type": "nsw_strata_hub_annual_return",
                "title": "NSW Strata Hub Annual Return Due",
                "message": (
                    f"The NSW Strata Hub annual return for {bid} is due by "
                    f"31 December. Please log in to strataservices.nsw.gov.au "
                    f"and submit the return for the current financial year."
                ),
                "due_date": f"{now.year}-{NSW_HUB_DUE_MONTH:02d}-31",
                "created_at": now.isoformat(),
                "status": "pending",
            }
        else:
            # Non-hub jurisdiction (ACT and others): general compliance reminder (annual)
            # Only create once per year
            existing = await db.notifications.find_one({
                "building_id": bid,
                "type": "compliance_reminder",
                "sub_type": "annual_compliance_check",
                "created_at": {"$regex": f"^{now.year}"},
            })
            if existing:
                continue  # Already created this year

            notification = {
                "building_id": bid,
                "type": "compliance_reminder",
                "sub_type": "annual_compliance_check",
                "title": "Annual Compliance Check",
                "message": (
                    f"Reminder: complete the annual compliance review for {bid}. "
                    "Check insurance renewals, AGM requirements, and by-law currency."
                ),
                "due_date": f"{now.year}-12-31",
                "created_at": now.isoformat(),
                "status": "pending",
            }

        await db.notifications.insert_one(notification)
        reminders_created += 1
        logger.info("Compliance reminder created for building %s (%s)", bid, jurisdiction)

    return {
        "reminders_created": reminders_created,
        "buildings_checked": len(buildings),
        "run_at": now.isoformat(),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NSW Strata Hub compliance reminder cron")
    parser.add_argument("--building", default=None)
    args = parser.parse_args()


    async def main():
        """Generated function header.

        Function: main
        Path: backend/cron/cron_nsw_compliance.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        result = await run_nsw_hub_reminders(args.building)
        print(result)


    asyncio.run(main())
