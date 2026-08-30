"""
Notification Worker — consumes all EastGate Redis Streams and dispatches
push/bell notifications for relevant event types.

Run as separate process:
    python -m workers.notification_worker

Consumer group: eastgate-notifications
"""
import json
import sys
from pathlib import Path

import asyncio
import logging

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.events import EventStream, EventType
from services.event_emitter import _get_redis, ensure_consumer_groups

logger = logging.getLogger(__name__)
CONSUMER_NAME = "notification-worker-1"
CONSUMER_GROUP = "eastgate-notifications"
BLOCK_MS = 5000

ALL_STREAMS = [s.value for s in EventStream]


async def handle_event(event_type: str, building_id: str, payload: dict) -> None:
    """Generated function header.

    Function: handle_event
    Path: backend/workers/notification_worker.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        et = EventType(event_type)
    except ValueError:
        return

    if et == EventType.PARCEL_LOGGED:
        await _handle_parcel_notification(building_id, payload)
    elif et == EventType.SLA_BREACH_DETECTED:
        logger.warning("SLA breach in building %s: %s", building_id, payload)
    elif et == EventType.LEASE_EXPIRY_WARNING:
        logger.info("Lease expiry warning in building %s: %s", building_id, payload)


async def _handle_parcel_notification(building_id: str, payload: dict) -> None:
    """Send bell notification to resident when a parcel is logged."""
    try:
        from database import db
        from utils.helpers import create_user_notification
        from request_context import set_ctx_building_id

        set_ctx_building_id(building_id)
        unit_number = payload.get("unit_number")
        if not unit_number:
            return

        unit = await db.units.find_one({"unit_number": unit_number, "building_id": building_id})
        if not unit:
            return

        carrier = payload.get("carrier", "")
        description = payload.get("description", "parcel")
        msg = f"A {description}{' from ' + carrier if carrier else ''} has arrived at reception for unit {unit_number}."

        for field in ("tenant_user_id", "tenant_id", "owner_user_id", "owner_id"):
            user_id = unit.get(field)
            if user_id:
                await create_user_notification(
                    user_id=user_id,
                    title="Parcel Received",
                    message=msg,
                    notification_type="parcel",
                    link="/community/parcels",
                    building_id=building_id,
                )
    except Exception as e:
        logger.error("Parcel notification failed: %s", e)


async def process_messages(redis_client) -> None:
    """Generated function header.

    Function: process_messages
    Path: backend/workers/notification_worker.py

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
                        await handle_event(
                            fields.get("event_type", ""),
                            fields.get("building_id", ""),
                            json.loads(fields.get("payload", "{}")),
                        )
                        await redis_client.xack(stream, CONSUMER_GROUP, msg_id)
                    except Exception as e:
                        logger.error("Error processing msg %s: %s", msg_id, e)
        except Exception as e:
            if "NOGROUP" not in str(e):
                logger.error("Error reading stream %s: %s", stream, e)


async def run() -> None:
    """Generated function header.

    Function: run
    Path: backend/workers/notification_worker.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logger.info("Notification worker starting...")
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
