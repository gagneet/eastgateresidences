import uuid
from datetime import datetime, timezone

import logging

from database import db
from utils.helpers import create_notifications_batch
from utils.workflow_runner import workflow_run

logger = logging.getLogger(__name__)


def get_current_utc_iso() -> str:
    """Generated function header.

    Function: get_current_utc_iso
    Path: backend/services/volunteer_credits_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


async def apply_volunteer_credits(event_id: str, actor_user_id: str) -> dict:
    """
    Apply levy credits proportional to hours contributed for all confirmed participants
    in a volunteer event.

    Returns a summary dict with total_credit_cents, credits_applied count, and allocations.
    Raises ValueError if credits already applied or event not found.
    """
    # Use tenant-scoped collection; caller must have set building context
    event = await db.volunteer_events.find_one({"id": event_id})
    if not event:
        raise ValueError(f"Volunteer event {event_id!r} not found")
    if event.get("credits_applied"):
        raise ValueError("Credits already applied for this event")
    building_id = event.get("building_id", "")

    # Collect confirmed participants with hours
    participants = [
        p for p in event.get("participants", [])
        if p.get("confirmed") and p.get("hours_contributed", 0) > 0
    ]

    estimated_cost = event.get("estimated_contractor_cost_cents", 0) or 0
    actual_cost = event.get("actual_cost_cents", 0) or 0
    total_credit_cents = max(0, estimated_cost - actual_cost)

    total_hours = sum(p.get("hours_contributed", 0) for p in participants)

    # Build proportional allocations
    allocations = []
    remaining = total_credit_cents
    for i, p in enumerate(participants):
        if total_hours > 0:
            if i < len(participants) - 1:
                credit = round(total_credit_cents * p["hours_contributed"] / total_hours)
                remaining -= credit
            else:
                # Last participant absorbs rounding remainder
                credit = remaining
        else:
            credit = 0
        allocations.append({
            "user_id": p["user_id"],
            "unit_number": p.get("unit_number", ""),
            "hours_contributed": p["hours_contributed"],
            "credit_cents": credit,
        })

    now = get_current_utc_iso()
    financial_year = f"FY{datetime.now(timezone.utc).year}-{str(datetime.now(timezone.utc).year + 1)[2:]}"

    async with db._db.client.start_session() as session:
        async with await session.start_transaction():
            # Update levy ledger for each participant
            for alloc in allocations:
                if alloc["credit_cents"] <= 0:
                    continue
                # Include building_id in filter to prevent cross-building ledger updates
                await db._db.unit_levy_ledger.update_one(
                    {"unit_number": alloc["unit_number"], "building_id": building_id},
                    {
                        "$inc": {"volunteer_credit_cents": alloc["credit_cents"]},
                        "$set": {"updated_at": now},
                        "$setOnInsert": {
                            "id": str(uuid.uuid4()),
                            "unit_number": alloc["unit_number"],
                            "building_id": building_id, "created_at": now,
                        },
                    },
                    upsert=True,
                    session=session,
                )

            # Write journal entries
            journal_entries = [
                {
                    "id": str(uuid.uuid4()),
                    "unit_number": alloc["unit_number"],
                    "user_id": alloc["user_id"],
                    "entry_type": "volunteer_credit",
                    "amount_cents": alloc["credit_cents"],
                    "reference_id": event_id,
                    "reference_type": "volunteer_event",
                    "description": f"Volunteer credit — {event.get('title', event_id)}",
                    "created_by": actor_user_id,
                    "created_at": now,
                }
                for alloc in allocations
                if alloc["credit_cents"] > 0
            ]
            if journal_entries:
                await db._db.journal_entries.insert_many(journal_entries, session=session)

            # Record savings event — include building_id for tenant scoping
            await db._db.savings_events.insert_one({
                "id": str(uuid.uuid4()),
                "building_id": building_id,
                "category": "volunteer",
                "work_type": event.get("work_type", ""),
                "reference_id": event_id,
                "reference_type": "volunteer_event",
                "amount_saved_cents": total_credit_cents,
                "estimated_contractor_cost_cents": estimated_cost,
                "actual_cost_cents": actual_cost,
                "financial_year": financial_year,
                "verified": True,
                "verified_by": actor_user_id,
                "created_at": now,
                "is_test_data": False,
            }, session=session)

            # Mark event as credits applied (use tenant-scoped path)
            await db.volunteer_events.update_one(
                {"id": event_id},
                {"$set": {"credits_applied": True, "credits_applied_at": now, "credits_applied_by": actor_user_id}},
            )

    # Write workflow run record
    async with workflow_run("volunteer_credit_application", event.get("building_id", ""), "manual") as run:
        run.items_processed = len(allocations)
        run.add_artefact("volunteer_event", event_id)

    # Send notifications
    notifications = [
        {
            "user_id": alloc["user_id"],
            "type": "volunteer_credit_applied",
            "title": "Volunteer levy credit applied",
            "message": (
                f"${alloc['credit_cents'] / 100:.2f} levy credit applied to your account "
                f"for volunteering at '{event.get('title', 'community event')}'."
            ),
            "reference_id": event_id,
            "reference_type": "volunteer_event",
        }
        for alloc in allocations
        if alloc["credit_cents"] > 0
    ]
    if notifications:
        await create_notifications_batch(notifications)

    logger.info(
        "Volunteer credits applied: event=%s allocations=%d total_cents=%d actor=%s",
        event_id, len(allocations), total_credit_cents, actor_user_id,
    )

    return {
        "event_id": event_id,
        "total_credit_cents": total_credit_cents,
        "credits_applied": len([a for a in allocations if a["credit_cents"] > 0]),
        "allocations": allocations,
    }
