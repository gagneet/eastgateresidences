"""
Event emitter for Redis Streams event bus.

Emits typed events to Redis Streams with graceful no-op fallback
when Redis is unavailable. All events include building_id for
consumer-side multi-tenant isolation.
"""
import json
import os
from datetime import datetime, timezone

import logging

logger = logging.getLogger(__name__)

REDIS_STREAM_MAXLEN = int(os.getenv("REDIS_STREAM_MAXLEN", "100000"))

_redis_client = None
_redis_checked = False


async def _get_redis():
    """Generated function header.

    Function: _get_redis
    Path: backend/services/event_emitter.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    global _redis_client, _redis_checked
    if _redis_checked:
        return _redis_client
    _redis_checked = True
    redis_url = os.getenv("REDIS_URL", "")
    if not redis_url:
        logger.debug("REDIS_URL not set — event emission disabled")
        return None
    try:
        import redis.asyncio as aioredis
        _redis_client = aioredis.from_url(redis_url, decode_responses=True)
        await _redis_client.ping()
        logger.info("Redis connected at %s", redis_url)
    except Exception as e:
        logger.warning("Redis unavailable, event emission disabled: %s", e)
        _redis_client = None
    return _redis_client


async def emit_event(
        stream: str,
        event_type: str,
        building_id: str,
        payload: dict,
        actor_user_id: str = "system",
) -> "str | None":
    """
    Emit an event to a Redis Stream.

    Returns the Redis message ID on success, None on failure or when Redis
    is unavailable. Failures are logged but never raise — event loss is
    acceptable; data integrity is maintained by the database layer.

    building_id is ALWAYS included so consumers can isolate per building.
    """
    client = await _get_redis()
    if client is None:
        return None

    message = {
        "event_type": str(event_type),
        "building_id": building_id,
        "actor_user_id": actor_user_id,
        "payload": json.dumps(payload, default=str),
        "emitted_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        msg_id = await client.xadd(
            str(stream),
            message,
            maxlen=REDIS_STREAM_MAXLEN,
            approximate=True,
        )
        return msg_id
    except Exception as e:
        logger.warning("emit_event failed stream=%s type=%s: %s", stream, event_type, e)
        return None


async def ensure_consumer_groups(streams_and_groups: list) -> None:
    """
    Ensure consumer groups exist for all specified stream/group pairs.
    Idempotent — safe to call multiple times.
    Called at worker startup.
    """
    client = await _get_redis()
    if client is None:
        return
    for stream, group in streams_and_groups:
        try:
            await client.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                logger.warning("xgroup_create failed stream=%s group=%s: %s", stream, group, e)
