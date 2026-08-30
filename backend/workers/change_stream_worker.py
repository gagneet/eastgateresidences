"""
Change Stream Worker — watches MongoDB change streams on financial and
governance collections and triggers building_summaries recomputation.

Requires MongoDB replica set. Run as separate process:
    python -m workers.change_stream_worker
"""
import os
import sys
from pathlib import Path

import asyncio
import logging

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

WATCHED_COLLECTIONS = [
    "transactions",
    "work_orders",
    "volunteer_events",
    "savings_events",
    "proposals",
    "compliance_items",
]


async def watch_collection(async_db, collection_name: str) -> None:
    """Generated function header.

    Function: watch_collection
    Path: backend/workers/change_stream_worker.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    collection = async_db[collection_name]
    pipeline = [{"$match": {"operationType": {"$in": ["insert", "update", "replace"]}}}]
    try:
        async with await collection.watch(pipeline, full_document="updateLookup") as stream:
            logger.info("Watching collection: %s", collection_name)
            async for change in stream:
                doc = change.get("fullDocument") or {}
                building_id = doc.get("building_id")
                if building_id:
                    from workers.analytics_worker import recompute_building_summary
                    await recompute_building_summary(building_id)
    except Exception as e:
        logger.error("Change stream error on %s: %s", collection_name, e)


async def run() -> None:
    """Generated function header.

    Function: run
    Path: backend/workers/change_stream_worker.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logger.info("Change stream worker starting...")

    from dotenv import load_dotenv
    load_dotenv()

    mongo_url = os.getenv("MONGO_URL")
    db_name = os.getenv("DB_NAME")
    if not mongo_url or not db_name:
        logger.error("MONGO_URL and DB_NAME must be set")
        return

    from pymongo import AsyncMongoClient
    client = AsyncMongoClient(mongo_url)

    try:
        await client.admin.command("replSetGetStatus")
    except Exception as e:
        logger.warning("Replica set not available — change streams require replica set: %s", e)
        return

    async_db = client[db_name]
    tasks = [asyncio.create_task(watch_collection(async_db, coll)) for coll in WATCHED_COLLECTIONS]
    logger.info("Watching %d collections", len(WATCHED_COLLECTIONS))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(run())
