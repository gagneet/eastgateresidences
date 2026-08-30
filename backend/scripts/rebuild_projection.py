# @featuretrace:scheduler — CLI script for on-demand and incremental projection rebuild (disaster recovery / post-bugfix).
# Layer: config
# Data flow: CLI → rebuild_projection() → levy_payments.aggregate → unit_levy_ledger.update_one (building-scoped).
# Related: backend/repositories/financial_repository.py (rebuild_projection function)
#           backend/workers/arq_tasks.py (projection_rebuild_task — ARQ-triggered equivalent)
"""
CLI: Rebuild a financial projection from the source of truth.

Usage:
    cd backend
    venv/bin/python3 scripts/rebuild_projection.py lot_balances_projection \\
        --building 13195

    venv/bin/python3 scripts/rebuild_projection.py unit_levy_ledger \\
        --building 16244 \\
        --since 6634e1a0000000000000000a

Supported projection names:
    lot_balances_projection   — alias for unit_levy_ledger
    unit_levy_ledger          — re-aggregates levy totals from levy_payments
    levy_payments_projection  — alias for unit_levy_ledger

This script is safe to run on production data — it only uses $upsert writes
and never deletes existing records. Run it after a bug fix that corrupted
projection data, or to verify consistency after a migration.

The --since flag restricts replay to events after a given ObjectId (useful
for incremental rebuild without a full replay).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

SUPPORTED = {"lot_balances_projection", "unit_levy_ledger", "levy_payments_projection"}


async def main(projection_name: str, building_id: str, since_event_id: str | None) -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/rebuild_projection.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if projection_name not in SUPPORTED:
        logger.error("Unknown projection '%s'. Supported: %s", projection_name, sorted(SUPPORTED))
        sys.exit(1)

    mongo_url = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DB_NAME", "strata_management")
    client = AsyncIOMotorClient(mongo_url)
    raw_db = client[db_name]

    # Optional: filter events by since_event_id
    events = []
    if since_event_id:
        from bson import ObjectId
        try:
            oid = ObjectId(since_event_id)
        except Exception:
            logger.error("Invalid --since ObjectId: %s", since_event_id)
            sys.exit(1)
        events = await raw_db.financial_events.find(
            {
                "building_id": building_id,
                "projection_target": projection_name,
                "_id": {"$gt": oid},
            }
        ).sort("_id", 1).to_list(50000)
        logger.info("Loaded %d events since %s", len(events), since_event_id)
    else:
        logger.info("Full rebuild (no --since filter)")

    from repositories.financial_repository import rebuild_projection
    rebuilt = await rebuild_projection(raw_db, projection_name, building_id, events)

    logger.info(
        "Rebuilt %d records for projection '%s' (building=%s)",
        rebuilt, projection_name, building_id,
    )
    client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild a financial projection from source data")
    parser.add_argument("projection_name", choices=sorted(SUPPORTED), help="Projection to rebuild")
    parser.add_argument("--building", required=True, dest="building_id", help="Building ID")
    parser.add_argument(
        "--since", default=None, dest="since_event_id",
        help="ObjectId — only replay events after this ID (incremental rebuild)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.projection_name, args.building_id, args.since_event_id))
