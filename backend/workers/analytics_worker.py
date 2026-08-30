# @featuretrace:community-hub — Recomputes building_summaries (incl. the health score) from events.
# Layer: worker
# Data flow: Redis Streams event → recompute_building_summary(building_id) →
#            work_orders / compliance_items / units / volunteer_events / arrears helper →
#            health_score_service.compute_building_health_score → building_summaries (building-scoped).
# Related: backend/services/health_score_service.py
#          backend/services/morning_card_service.py
#          backend/routers/community_dashboard.py
# Tests: tests/backend/test_building_health_missing_data.py
"""
Analytics Worker — consumes Redis Streams and triggers building_summaries
recomputation when relevant events arrive.

Run as separate process:
    python -m workers.analytics_worker

Consumer group: eastgate-analytics
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncio
import logging

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.events import EventStream, EventType
from services.event_emitter import _get_redis, ensure_consumer_groups

logger = logging.getLogger(__name__)
CONSUMER_NAME = "analytics-worker-1"
CONSUMER_GROUP = "eastgate-analytics"

#: Cap on how many open work orders are sampled for the mean-age input to the
#: health score. Bounds a per-event query; see _avg_open_work_order_age_days.
WORK_ORDER_AGE_SAMPLE_LIMIT = 500
BLOCK_MS = 5000

RECOMPUTE_TRIGGERS = {
    EventType.SAVINGS_EVENT_CREATED,
    EventType.VOLUNTEER_CREDITS_APPLIED,
    EventType.PROPOSAL_OUTCOME_RECORDED,
    EventType.WORK_ORDER_INVOICE_APPROVED,
    EventType.SLA_BREACH_DETECTED,
}

ALL_STREAMS = [s.value for s in EventStream]


async def _avg_open_work_order_age_days(db) -> float | None:
    """Mean age in days of currently-open work orders, or None if none are open.

    Returns None rather than 0 when nothing is open: a zero here previously read
    as "everything is brand new", scoring full marks on half the maintenance
    component for a building that simply had no work orders.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    ages: list[float] = []
    # Bounded. This runs on every recompute-triggering event, and an unbounded
    # cursor over a large building's open work orders would stream the lot each
    # time. A sample is enough for a mean that feeds one component of a 0-100
    # score; an exact figure would cost more than it is worth here.
    for wo in await db.work_orders.find(
        {"status": {"$in": ["new", "approved", "in_progress"]}},
        {"_id": 0, "created_at": 1},
    ).to_list(WORK_ORDER_AGE_SAMPLE_LIMIT):
        raw = wo.get("created_at")
        if not raw:
            continue
        try:
            created = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        ages.append(max(0.0, (now - created).total_seconds() / 86400))

    return sum(ages) / len(ages) if ages else None


async def _arrears_lots(building_id: str) -> int | None:
    """Units in arrears, via the canonical grace-aware helper.

    CLAUDE.md makes ``get_arrears_metrics``/``get_building_arrears_summary`` the
    only permitted source for an arrears figure — a locally re-derived count is
    the exact bug class that produced East Gate's "31 units / $1,469.49". A
    failure here returns None (component excluded), never 0.
    """
    try:
        from utils.finance_helpers import get_building_arrears_summary

        summary = await get_building_arrears_summary(building_id)
        return summary["units_in_arrears"]
    except Exception:  # noqa: BLE001 — unavailable, not zero
        logger.warning(
            "arrears unavailable for building=%s; excluding it from the health score",
            building_id, exc_info=True,
        )
        return None


async def recompute_building_summary(building_id: str) -> None:
    """Recompute the building_summaries document for one building."""
    try:
        from database import db
        from services.health_score_service import compute_building_health_score
        from request_context import set_ctx_building_id

        set_ctx_building_id(building_id)

        open_wos = await db.work_orders.count_documents(
            {"status": {"$in": ["new", "approved", "in_progress"]}}
        )
        overdue_wos = await db.work_orders.count_documents({
            "status": {"$in": ["new", "approved", "in_progress"]},
            "due_date": {"$lt": datetime.now(timezone.utc).isoformat()},
        })
        open_requests = await db.workflow_requests.count_documents(
            {"status": {"$in": ["awaiting_review", "in_progress"]}, "is_test_data": {"$ne": True}}
        )
        open_proposals = await db.proposals.count_documents(
            {"status": "open", "is_test_data": {"$ne": True}}
        )
        compliance_overdue = await db.compliance_items.count_documents({"status": "overdue"})

        units = await db.units.find({}, {"_id": 0, "id": 1}).to_list(200)
        total_lots = len(units)

        fy_year = datetime.now(timezone.utc).year
        fy = f"FY{fy_year}-{str(fy_year + 1)[-2:]}"
        savings_ytd = 0
        async for e in db.savings_events.find(
                {"financial_year": fy, "is_test_data": {"$ne": True}}, {"amount_saved_cents": 1}
        ):
            savings_ytd += e.get("amount_saved_cents", 0)

        # Health inputs. Seven of these used to be hardcoded literals —
        # sinking_fund_balance=0, capital_works_10yr_forecast=1, arrears_lots=0,
        # avg_work_order_age_days=7, vote_participation_rate=0.5,
        # volunteer_events_ytd=0, open_disputes=0 — and total_lots/open_work_orders
        # were floored to 1 with max(). Together they made a building with NO DATA
        # AT ALL score 75/100 Grade B, reproducibly. Anything not genuinely
        # measurable is now None, which the scorer treats as "unavailable" and
        # excludes, rather than as a flattering zero.
        work_orders_total = await db.work_orders.count_documents({})
        compliance_total = await db.compliance_items.count_documents({})
        volunteer_ytd = await db.volunteer_events.count_documents({"status": "completed"})

        health_data = {
            "total_lots": total_lots,
            "work_orders_total": work_orders_total,
            "open_work_orders": open_wos,
            "overdue_work_orders": overdue_wos,
            "avg_work_order_age_days": await _avg_open_work_order_age_days(db),
            "compliance_items_total": compliance_total,
            "compliance_items_overdue": compliance_overdue,
            "volunteer_events_ytd": volunteer_ytd,
            "arrears_lots": await _arrears_lots(building_id),
            # Not measurable from any collection this worker has: there is no
            # capital-works forecast source wired here, no vote-participation
            # rollup, and no disputes register. None keeps them OUT of the score
            # instead of contributing full marks.
            "sinking_fund_balance": None,
            "capital_works_10yr_forecast": None,
            "vote_participation_rate": None,
            "open_disputes": None,
        }
        health = compute_building_health_score(health_data)

        now = datetime.now(timezone.utc).isoformat()
        summary = {
            "building_id": building_id,
            "open_work_orders": open_wos,
            "overdue_work_orders": overdue_wos,
            "open_requests": open_requests,
            "active_proposals": open_proposals,
            "compliance_items_overdue": compliance_overdue,
            "savings_ytd_cents": savings_ytd,
            "total_lots": total_lots,
            # Both naming conventions are written deliberately. This worker wrote
            # building_health_score + health_grade, while morning_card_service read
            # building_health_score + building_health_GRADE — so the grade was
            # always missing and the card rendered "Building health: 75/100
            # (Grade )" and "Your building is in  condition." Writing both keeps
            # every existing reader working; the pair is asserted by
            # tests/backend/test_building_health_missing_data.py so they cannot
            # drift apart again.
            "building_health_score": health["score"],
            "building_health_grade": health["grade"],
            "health_score": health["score"],
            "health_grade": health["grade"],
            "health_status": health["status"],
            "health_components": health["components"],
            "health_unavailable_components": health["unavailable_components"],
            "health_coverage": health["coverage"],
            "computed_at": now,
            "compute_trigger": "event",
        }

        await db.building_summaries.update_one(
            {"building_id": building_id},
            {"$set": summary},
            upsert=True,
        )
        # %s not %d — the score is legitimately None when there is too little
        # data to publish one, and %d would raise on it.
        logger.info(
            "Summary recomputed: building=%s score=%s status=%s unavailable=%s",
            building_id, health["score"], health["status"],
            ",".join(health["unavailable_components"]) or "none",
        )

    except Exception as e:
        logger.error("Failed to recompute summary for building %s: %s", building_id, e)


async def process_messages(redis_client) -> None:
    """Generated function header.

    Function: process_messages
    Path: backend/workers/analytics_worker.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for stream in ALL_STREAMS:
        try:
            messages = await redis_client.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername=CONSUMER_NAME,
                streams={stream: ">"},
                count=100,
                block=BLOCK_MS,
            )
            if not messages:
                continue
            for _stream, msg_list in messages:
                for msg_id, fields in msg_list:
                    try:
                        event_type = fields.get("event_type", "")
                        building_id = fields.get("building_id", "")
                        try:
                            et = EventType(event_type)
                        except ValueError:
                            await redis_client.xack(stream, CONSUMER_GROUP, msg_id)
                            continue
                        if et in RECOMPUTE_TRIGGERS and building_id:
                            await recompute_building_summary(building_id)
                        await redis_client.xack(stream, CONSUMER_GROUP, msg_id)
                    except Exception as e:
                        logger.error("Error processing msg %s: %s", msg_id, e)
        except Exception as e:
            if "NOGROUP" not in str(e):
                logger.error("Error reading stream %s: %s", stream, e)


async def run() -> None:
    """Generated function header.

    Function: run
    Path: backend/workers/analytics_worker.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logger.info("Analytics worker starting...")
    await ensure_consumer_groups([(s, CONSUMER_GROUP) for s in ALL_STREAMS])
    redis = await _get_redis()
    if redis is None:
        logger.error("Redis not available — set REDIS_URL.")
        return
    logger.info("Listening on %d streams", len(ALL_STREAMS))
    while True:
        await process_messages(redis)
        await asyncio.sleep(0.1)


if __name__ == "__main__":
    asyncio.run(run())
