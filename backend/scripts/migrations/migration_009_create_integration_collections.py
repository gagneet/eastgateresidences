"""
migration_009_create_integration_collections.py

Creates indexes for the three collections introduced by the Financial
Integration Layer v2:

  integration_inbox
    - Unique compound index on (tenant_id, idempotency_key) — prevents duplicate
      inbound events from re-processing if the same payload is delivered twice.
    - TTL index on received_at (90 days) — automatically purges processed inbox
      entries; keeps storage bounded without manual housekeeping.
    - Compound index on (tenant_id, status) — fast queue drain queries filtered
      by processing status per tenant.

  event_log
    - Unique compound index on (tenant_id, event_id) — ensures each domain event
      is recorded exactly once per tenant (idempotent event sourcing).
    - Compound index on (tenant_id, occurred_at DESC) — supports time-ordered
      event replay and audit queries without a collection scan.

  mock_biller_allocations
    - Unique compound index on (tenant_id, scheme_id, lot_id, instalment_seq) —
      prevents duplicate allocation rows for the same lot/instalment combination
      within a levy scheme.

Idempotent: safe to re-run. create_index() with unique=True is a no-op when the
index already exists with the same key pattern and options.

Run:
    cd backend && python3 scripts/migrations/migration_009_create_integration_collections.py
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

backend_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(backend_dir))
load_dotenv(backend_dir / ".env")

MONGO_URL = os.getenv("MONGO_URL")
DB_NAME = os.getenv("DB_NAME")

if not MONGO_URL or not DB_NAME:
    raise ValueError("MONGO_URL and DB_NAME must be set in environment variables before running this migration.")


async def run() -> None:
    """Generated function header.

    Function: run
    Path: backend/scripts/migrations/migration_009_create_integration_collections.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    print(f"Migration 009 — creating indexes for integration collections on db={DB_NAME!r}")

    # ── integration_inbox ─────────────────────────────────────────────────────
    print("\n[1/3] integration_inbox ...")

    idx = await db.integration_inbox.create_index(
        [("tenant_id", 1), ("idempotency_key", 1)],
        unique=True,
        name="unique_tenant_idempotency_key",
    )
    print(f"  Created (or confirmed): {idx}")

    idx = await db.integration_inbox.create_index(
        [("received_at", 1)],
        expireAfterSeconds=7_776_000,  # 90 days
        name="ttl_received_at_90d",
    )
    print(f"  Created (or confirmed): {idx}")

    idx = await db.integration_inbox.create_index(
        [("tenant_id", 1), ("status", 1)],
        name="tenant_status",
    )
    print(f"  Created (or confirmed): {idx}")

    # ── event_log ─────────────────────────────────────────────────────────────
    print("\n[2/3] event_log ...")

    idx = await db.event_log.create_index(
        [("tenant_id", 1), ("event_id", 1)],
        unique=True,
        name="unique_tenant_event_id",
    )
    print(f"  Created (or confirmed): {idx}")

    idx = await db.event_log.create_index(
        [("tenant_id", 1), ("occurred_at", -1)],
        name="tenant_occurred_at_desc",
    )
    print(f"  Created (or confirmed): {idx}")

    # ── mock_biller_allocations ───────────────────────────────────────────────
    print("\n[3/3] mock_biller_allocations ...")

    idx = await db.mock_biller_allocations.create_index(
        [("tenant_id", 1), ("scheme_id", 1), ("lot_id", 1), ("instalment_seq", 1)],
        unique=True,
        name="unique_tenant_scheme_lot_instalment",
    )
    print(f"  Created (or confirmed): {idx}")

    print("\nMigration 009 complete.")
    client.close()


if __name__ == "__main__":
    asyncio.run(run())
