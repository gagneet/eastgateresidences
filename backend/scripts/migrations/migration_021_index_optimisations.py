"""
Migration 021 — Index optimisations: remove redundant indexes + add missing compound indexes.

Idempotent. Safe to re-run.

Removes (orphan duplicate indexes now covered by financial_repository.ensure_indexes):
  - levy_payments.unit_number_1_year_1         (no building_id prefix)
  - unit_levy_ledger.unit_number_1_year_1      (no building_id prefix)

Adds (genuinely missing compound indexes across 7 collections):
  Financial:
  - financial_transactions: (building_id, lot_number, transaction_date DESC)
  - financial_transactions: (building_id, fund_type, transaction_date DESC)
  - levy_payments partial:  (building_id, unit_number) WHERE status != "paid"   [arrears]

  Bank / reconciliation:
  - bank_transactions: (building_id, transaction_date, amount_cents)
  - bank_transactions partial: (building_id, status) WHERE status = "unmatched"

  Trust:
  - trust_ledger_entries: (building_id, trust_account_id, created_at DESC)

  Insurance:
  - insurance_policies: (building_id, expiry_date)
  - insurance_policies: (building_id, policy_type)

  Operational:
  - maintenance_requests: (building_id, status, created_at DESC)
  - maintenance_requests: (building_id, assigned_contractor, status)
  - work_orders:          (building_id, status, priority, created_at DESC)
  - ownership_periods:    (building_id, owner_id, effective_from DESC)
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pymongo import AsyncMongoClient
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "strata_management")

# Old auto-generated names for the duplicate 2-key indexes created in server.py startup.
_LEGACY_LP_IDX = "unit_number_1_year_1"
_LEGACY_ULL_IDX = "unit_number_1_year_1"


async def _drop_if_exists(collection, index_name: str) -> None:
    """Drop an index by name only if it currently exists (idempotent)."""
    existing = await collection.index_information()
    if index_name in existing:
        await collection.drop_index(index_name)
        logger.info("Dropped legacy index: %s.%s", collection.name, index_name)
    else:
        logger.info("Legacy index already absent (skip): %s.%s", collection.name, index_name)


async def _create_if_missing(collection, keys, name: str, **kwargs) -> None:
    """Create a named index; skip if the name OR an identical key-spec already exists.

    MongoDB raises IndexOptionsConflict (code 85) when the same key spec is
    registered under a different name.  We detect this here and skip silently
    so the migration remains idempotent on databases that were indexed before
    this migration added the bid_* naming convention.
    """
    existing = await collection.index_information()
    if name in existing:
        logger.info("Index already exists (skip): %s.%s", collection.name, name)
        return
    # Normalise the target key spec for comparison
    target_keys = [tuple(k) for k in keys]
    for idx_name, idx_info in existing.items():
        existing_keys = [tuple(k) for k in idx_info.get("key", [])]
        if existing_keys == target_keys:
            logger.info(
                "Key spec already indexed as '%s' (skip renaming): %s.%s",
                idx_name, collection.name, name,
            )
            return
    await collection.create_index(keys, name=name, **kwargs)
    logger.info("Created index: %s.%s", collection.name, name)


async def run_migration() -> None:
    """Generated function header.

    Function: run_migration
    Path: backend/scripts/migrations/migration_021_index_optimisations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncMongoClient(MONGO_URL)
    db = client[DB_NAME]

    # ── 1. Remove redundant legacy indexes ───────────────────────────────────
    # These were created by server.py startup without building_id prefix.
    # financial_repository.ensure_indexes() already creates the correct
    # (building_id, unit_number, year) variants; these duplicates add write
    # overhead with no query benefit.
    await _drop_if_exists(db.levy_payments, _LEGACY_LP_IDX)
    await _drop_if_exists(db.unit_levy_ledger, _LEGACY_ULL_IDX)

    # ── 2. financial_transactions — Statement of Accounts + fund reports ─────
    await _create_if_missing(
        db.financial_transactions,
        [("building_id", 1), ("lot_number", 1), ("transaction_date", -1)],
        name="bid_lot_txdate_desc",
    )
    await _create_if_missing(
        db.financial_transactions,
        [("building_id", 1), ("fund_type", 1), ("transaction_date", -1)],
        name="bid_fund_txdate_desc",
    )

    # ── 3. levy_payments — compound index for arrears-by-unit query ──────────
    # NOTE: MongoDB 6.0 does not support $ne in partial filter expressions.
    # A compound (building_id, unit_number, status) index covers the arrears
    # dashboard query {building_id: X, unit_number: Y, status: {$ne: "paid"}}
    # and is compatible with all MongoDB 4.4+ versions.
    await _create_if_missing(
        db.levy_payments,
        [("building_id", 1), ("unit_number", 1), ("status", 1)],
        name="bid_unit_status",
    )

    # ── 4. bank_transactions — reconciliation lookup ──────────────────────────
    await _create_if_missing(
        db.bank_transactions,
        [("building_id", 1), ("transaction_date", 1), ("amount_cents", 1)],
        name="bid_date_amount",
    )
    await _create_if_missing(
        db.bank_transactions,
        [("building_id", 1), ("status", 1)],
        name="bid_status_unmatched_partial",
        partialFilterExpression={"status": "unmatched"},
    )

    # ── 5. trust_ledger_entries — trust balance aggregation ───────────────────
    await _create_if_missing(
        db.trust_ledger_entries,
        [("building_id", 1), ("trust_account_id", 1), ("created_at", -1)],
        name="bid_account_created_desc",
    )

    # ── 6. insurance_policies — renewal alerts + compliance filter ────────────
    await _create_if_missing(
        db.insurance_policies,
        [("building_id", 1), ("expiry_date", 1)],
        name="bid_expiry",
    )
    await _create_if_missing(
        db.insurance_policies,
        [("building_id", 1), ("policy_type", 1)],
        name="bid_policy_type",
    )

    # ── 7. maintenance_requests — SLA dashboard + contractor queue ────────────
    await _create_if_missing(
        db.maintenance_requests,
        [("building_id", 1), ("status", 1), ("created_at", -1)],
        name="bid_status_created_desc",
    )
    await _create_if_missing(
        db.maintenance_requests,
        [("building_id", 1), ("assigned_contractor", 1), ("status", 1)],
        name="bid_contractor_status",
    )

    # ── 8. work_orders — priority-sorted SLA queue ────────────────────────────
    await _create_if_missing(
        db.work_orders,
        [("building_id", 1), ("status", 1), ("priority", 1), ("created_at", -1)],
        name="bid_status_priority_created",
    )

    # ── 9. ownership_periods — owner's current holdings lookup ───────────────
    # Correct field name is `owner_id` (not `owner_user_id`). If the wrong index
    # was created by an earlier run, drop it first so we can recreate correctly.
    _op_existing = await db.ownership_periods.index_information()
    if "bid_owner_from_desc" in _op_existing:
        _wrong_spec = [("building_id", 1), ("owner_user_id", 1), ("effective_from", -1)]
        _actual_spec = [tuple(k) for k in _op_existing["bid_owner_from_desc"].get("key", [])]
        if _actual_spec == [tuple(k) for k in _wrong_spec]:
            await db.ownership_periods.drop_index("bid_owner_from_desc")
            logger.info("Dropped incorrect bid_owner_from_desc (had owner_user_id — recreating with owner_id)")
    await _create_if_missing(
        db.ownership_periods,
        [("building_id", 1), ("owner_id", 1), ("effective_from", -1)],
        name="bid_owner_from_desc",
    )

    logger.info("Migration 021 complete.")
    await client.close()


if __name__ == "__main__":
    asyncio.run(run_migration())
