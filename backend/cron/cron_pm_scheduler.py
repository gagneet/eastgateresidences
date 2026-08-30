"""
GAP-COM-006: Preventive Maintenance Scheduler.

Reads `recurring_work_orders` (collection already exists with RecurringWorkOrderCreate model)
and instantiates actual work orders when their next_due date has arrived.

Run schedule: daily at 06:00 — `0 6 * * *`
Or trigger via: POST /maintenance/pm/trigger (admin endpoint in work_orders.py)

Idempotent: checks for an existing open work order with the same
`recurring_id` before creating a new one to prevent duplicates.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


def _today() -> str:
    """Generated function header.

    Function: _today
    Path: backend/cron/cron_pm_scheduler.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return date.today().isoformat()


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/cron/cron_pm_scheduler.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _next_due(last_due: str, frequency: str) -> str:
    """Calculate the next due date based on frequency."""
    try:
        base = date.fromisoformat(last_due)
    except ValueError:
        base = date.today()

    freq = frequency.lower()
    if freq == "daily":
        return (base + timedelta(days=1)).isoformat()
    elif freq == "weekly":
        return (base + timedelta(weeks=1)).isoformat()
    elif freq == "fortnightly":
        return (base + timedelta(weeks=2)).isoformat()
    elif freq == "monthly":
        # Add ~1 month
        month = base.month + 1
        year = base.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        day = min(base.day, [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
        return date(year, month, day).isoformat()
    elif freq == "quarterly":
        month = base.month + 3
        year = base.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        return date(year, month, base.day).isoformat()
    elif freq in ("biannual", "6-monthly"):
        month = base.month + 6
        year = base.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        return date(year, month, base.day).isoformat()
    elif freq in ("annual", "yearly"):
        return date(base.year + 1, base.month, base.day).isoformat()
    else:
        return (base + timedelta(days=30)).isoformat()


async def run_pm_scheduler(building_id: str | None = None) -> dict:
    """Instantiate work orders for any recurring PM schedules that are due.

    Args:
        building_id: Limit to one building. None = all buildings.

    Returns:
        Summary dict with {checked, created, skipped, errors}.
    """
    from database import db, set_ctx_building_id

    today = _today()
    checked = created = skipped = errors = 0

    # Get target buildings
    if building_id:
        building_ids = [building_id]
    else:
        buildings = await db._db.buildings.find(
            {"is_active": {"$ne": False}},
            {"plan_id": 1, "building_id": 1}
        ).to_list(100)
        building_ids = [
            b.get("building_id") or b.get("plan_id")
            for b in buildings
            if b.get("building_id") or b.get("plan_id")
        ]

    for bid in building_ids:
        set_ctx_building_id(bid)
        try:
            # Find recurring schedules due today or earlier
            cursor = db.recurring_work_orders.find({
                "building_id": bid,
                "is_active": {"$ne": False},
                "next_due": {"$lte": today},
            })
            schedules = await cursor.to_list(200)

            for schedule in schedules:
                checked += 1
                recurring_id = str(schedule.get("_id", ""))

                # Idempotency: skip if open work order already exists for this schedule
                existing = await db.maintenance_requests.find_one({
                    "building_id": bid,
                    "recurring_id": recurring_id,
                    "status": {"$in": ["open", "in_progress", "pending"]},
                })
                if existing:
                    skipped += 1
                    continue

                try:
                    work_order = {
                        "building_id": bid,
                        "title": schedule.get("title", "Scheduled maintenance"),
                        "description": (
                            f"[Auto-generated from PM schedule] {schedule.get('description', '')}"
                        ),
                        "category": schedule.get("category", "maintenance"),
                        "priority": schedule.get("priority", "medium"),
                        "status": "open",
                        "recurring_id": recurring_id,
                        "assigned_contractor": schedule.get("preferred_contractor"),
                        "submitted_by": "system",
                        "submitted_at": _now_iso(),
                        "created_at": _now_iso(),
                        "updated_at": _now_iso(),
                        "source": "pm_scheduler",
                        "location": schedule.get("location"),
                        "estimated_cost": schedule.get("estimated_cost"),
                    }
                    await db.maintenance_requests.insert_one(work_order)

                    # Advance the next_due date on the schedule
                    new_next_due = _next_due(
                        schedule.get("next_due", today),
                        schedule.get("frequency", "monthly"),
                    )
                    await db.recurring_work_orders.update_one(
                        {"_id": schedule["_id"]},
                        {"$set": {
                            "last_triggered": today,
                            "next_due": new_next_due,
                            "updated_at": _now_iso(),
                        }},
                    )
                    created += 1
                    logger.info("PM scheduler: created WO for schedule %s (building %s)", recurring_id, bid)

                except Exception as exc:
                    errors += 1
                    logger.error("PM scheduler: failed for schedule %s: %s", recurring_id, exc)

        except Exception as exc:
            logger.error("PM scheduler: building %s failed: %s", bid, exc)
            errors += 1

    summary = {
        "date": today,
        "buildings_processed": len(building_ids),
        "schedules_checked": checked,
        "work_orders_created": created,
        "skipped_existing": skipped,
        "errors": errors,
    }
    logger.info("PM scheduler run complete: %s", summary)
    return summary


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/cron/cron_pm_scheduler.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).parent.parent / ".env")

    logging.basicConfig(level=logging.INFO)
    result = await run_pm_scheduler()
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
