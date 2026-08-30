"""
Insurance renewal reminder cron.

Workflow served:
  - insurance_renewal_reminder : Alert strata managers and EC chairs when
    building insurance policies are due for renewal (90, 60, 30, 14 day warnings).

Scheduling (example):
  0 8 * * *   # Daily at 8am — check all policies for upcoming renewals
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import db
from utils.helpers import get_current_utc_iso

logger = logging.getLogger(__name__)

# Warning thresholds in days
RENEWAL_THRESHOLDS = [90, 60, 30, 14]


async def run_insurance_reminders(building_id: str | None = None) -> dict:
    """
    Scan insurance policies across all buildings and create renewal reminder
    notifications for policies expiring within the threshold windows.

    De-duplicates by not re-creating a reminder that was already sent for the
    same policy + threshold combination.
    """
    now = datetime.now(timezone.utc)
    buildings_query: dict = {}
    if building_id:
        buildings_query["building_id"] = building_id

    # Find all insurance policies
    policies = await db.insurance_policies.find(buildings_query, {"_id": 0}).to_list(500)
    reminders_created = 0
    policies_checked = len(policies)

    for policy in policies:
        bid = policy.get("building_id", "")
        policy_id = policy.get("id", "")
        expiry_str = policy.get("expiry_date") or policy.get("renewal_date")
        if not expiry_str:
            continue

        try:
            expiry_date = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            logger.warning("Invalid expiry_date for policy %s: %s", policy_id, expiry_str)
            continue

        days_until = (expiry_date - now).days

        for threshold in RENEWAL_THRESHOLDS:
            # Fire when we cross the threshold (within a 2-day window to avoid double-firing)
            if not (threshold - 1 <= days_until <= threshold + 1):
                continue

            # De-duplicate: check if a reminder for this threshold already exists
            existing = await db.notifications.find_one({
                "building_id": bid,
                "type": "insurance_renewal",
                "policy_id": policy_id,
                "threshold_days": threshold,
                "created_at": {"$regex": f"^{now.year}"},
            })
            if existing:
                continue

            policy_name = policy.get("policy_type") or policy.get("insurer") or "Building Insurance"
            insurer = policy.get("insurer", "your insurer")
            premium = policy.get("annual_premium", 0) or 0

            notification = {
                "building_id": bid,
                "type": "insurance_renewal",
                "policy_id": policy_id,
                "threshold_days": threshold,
                "title": f"Insurance Renewal Due in {days_until} Days",
                "message": (
                    f"{policy_name} (insurer: {insurer}) expires on "
                    f"{expiry_date.strftime('%d %B %Y')}. "
                    f"Annual premium: ${premium:,.2f}. "
                    "Please arrange renewal before the expiry date."
                ),
                "priority": "high" if threshold <= 30 else "medium",
                "expiry_date": expiry_str,
                "created_at": now.isoformat(),
                "status": "pending",
            }
            await db.notifications.insert_one(notification)
            reminders_created += 1
            logger.info(
                "Insurance renewal reminder (%d days) created for building %s policy %s",
                threshold, bid, policy_id,
            )
            break  # Only one threshold per run per policy

    return {
        "policies_checked": policies_checked,
        "reminders_created": reminders_created,
        "run_at": now.isoformat(),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Insurance renewal reminder cron")
    parser.add_argument("--building", default=None)
    args = parser.parse_args()


    async def main():
        """Generated function header.

        Function: main
        Path: backend/cron/cron_insurance.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        result = await run_insurance_reminders(args.building)
        print(result)


    asyncio.run(main())
