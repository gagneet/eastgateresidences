"""
Migration: Create indexes for all new collections introduced in the
Gap Closure implementation (Phase 0-3).

Collections and indexes:
  trust_ledger_accounts    : building_id, fund_type
  trust_ledger_entries     : building_id, fund_type, date, status
  trust_ledger_batches     : building_id, status
  bank_transactions        : building_id, external_tx_id (unique), matched, date
  bank_feed_runs           : building_id, run_id (unique)
  organisations            : name
  organisation_buildings   : organisation_id, building_id (unique compound)
  organisation_members     : organisation_id, user_id (unique compound)
  insurance_policies       : building_id, policy_type, expiry_date, status
  audit_records            : building_id, financial_year (unique compound)
  compliance_registers     : building_id, register_type (unique compound)
  compliance_register_items: register_id, building_id, next_inspection_date
  compliance_events        : building_id, due_date, completed
  jurisdiction_config      : building_id (unique)
  manager_contracts        : building_id, status
  nsw_reports              : building_id, report_period_to
  privacy_consents         : building_id, user_id, consent_type
  data_breach_log          : building_id, detected_at
  whs_inductions           : building_id, expiry_date
  whs_swms                 : building_id, work_start_date
  whs_incidents            : building_id, incident_date
  decisions                : building_id, decision_number (unique), meeting_date

Run with:
  cd /path/to/backend
  venv/bin/python3 -m scripts.migrations.001_create_gap_closure_indexes
"""
from __future__ import annotations

import sys
from pathlib import Path

import asyncio
import logging

# Ensure backend root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import motor.motor_asyncio

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def create_indexes(db) -> None:
    """Create all required indexes for gap closure collections."""

    INDEXES = [
        # Trust Accounting
        ("trust_ledger_accounts", [("building_id", 1), ("fund_type", 1)], {}),
        ("trust_ledger_accounts", [("account_code", 1), ("building_id", 1)], {"unique": True}),
        ("trust_ledger_entries", [("building_id", 1), ("fund_type", 1)], {}),
        ("trust_ledger_entries", [("building_id", 1), ("date", -1)], {}),
        ("trust_ledger_entries", [("building_id", 1), ("status", 1)], {}),
        ("trust_ledger_batches", [("building_id", 1), ("status", 1)], {}),
        ("trust_ledger_batches", [("building_id", 1), ("created_at", -1)], {}),
        # Bank Feeds
        ("bank_transactions", [("building_id", 1), ("external_tx_id", 1)], {"unique": True}),
        ("bank_transactions", [("building_id", 1), ("matched", 1)], {}),
        ("bank_transactions", [("building_id", 1), ("date", -1)], {}),
        ("bank_transactions", [("building_id", 1), ("fund_type", 1)], {}),
        ("bank_feed_runs", [("run_id", 1)], {"unique": True}),
        ("bank_feed_runs", [("building_id", 1), ("started_at", -1)], {}),
        # Organisations
        ("organisations", [("name", 1)], {}),
        ("organisation_buildings", [("organisation_id", 1), ("building_id", 1)], {"unique": True}),
        ("organisation_members", [("organisation_id", 1), ("user_id", 1)], {"unique": True}),
        # Insurance
        ("insurance_policies", [("building_id", 1), ("policy_type", 1)], {}),
        ("insurance_policies", [("building_id", 1), ("expiry_date", 1)], {}),
        ("insurance_policies", [("building_id", 1), ("status", 1)], {}),
        # Audit
        ("audit_records", [("building_id", 1), ("financial_year", 1)], {"unique": True}),
        # Compliance Registers
        ("compliance_registers", [("building_id", 1), ("register_type", 1)], {"unique": True}),
        ("compliance_register_items", [("register_id", 1)], {}),
        ("compliance_register_items", [("building_id", 1), ("next_inspection_date", 1)], {}),
        ("compliance_events", [("building_id", 1), ("due_date", 1)], {}),
        ("compliance_events", [("building_id", 1), ("completed", 1)], {}),
        # Jurisdiction
        ("jurisdiction_config", [("building_id", 1)], {"unique": True}),
        # Manager Contracts
        ("manager_contracts", [("building_id", 1), ("status", 1)], {}),
        ("manager_contracts", [("building_id", 1), ("contract_end_date", 1)], {}),
        # NSW Compliance
        ("nsw_reports", [("building_id", 1), ("report_period_to", -1)], {}),
        # Privacy
        ("privacy_consents", [("building_id", 1), ("user_id", 1), ("consent_type", 1)], {"unique": True}),
        ("data_breach_log", [("building_id", 1), ("detected_at", -1)], {}),
        # WHS
        ("whs_inductions", [("building_id", 1), ("expiry_date", 1)], {}),
        ("whs_swms", [("building_id", 1), ("work_start_date", -1)], {}),
        ("whs_incidents", [("building_id", 1), ("incident_date", -1)], {}),
        # Decisions
        ("decisions", [("building_id", 1), ("decision_number", 1)], {"unique": True}),
        ("decisions", [("building_id", 1), ("meeting_date", -1)], {}),
        ("decisions", [("building_id", 1), ("resolution_type", 1)], {}),
    ]

    created = 0
    skipped = 0
    for collection, keys, options in INDEXES:
        try:
            index_name = await db[collection].create_index(keys, **options)
            logger.info("✅  %-40s → %s", collection, index_name)
            created += 1
        except Exception as exc:
            if "already exists" in str(exc).lower() or "IndexKeySpecsConflict" in str(type(exc).__name__):
                logger.debug("   %-40s → already exists, skipping", collection)
                skipped += 1
            else:
                logger.warning("⚠️  %-40s → %s", collection, exc)
                skipped += 1

    logger.info("\n✅  Migration complete: %d indexes created, %d skipped.", created, skipped)


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/migrations/001_create_gap_closure_indexes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    import os
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent.parent / ".env")
    mongo_url = os.getenv("MONGO_URL", os.getenv("MONGODB_URL", "mongodb://localhost:27017"))
    db_name = os.getenv("DB_NAME", "strata_production")

    logger.info("Connecting to MongoDB: %s / %s", mongo_url, db_name)
    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    await create_indexes(db)
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
