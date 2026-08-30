"""
backend/scripts/migrations/migration_026_synthesize_historical_levy_payments.py

# @featuretrace:demo_bank — CLI wrapper for historical levy-payment synthesis.
# Layer: migration
# Data flow: CLI args → services.historical_levy_reconstruction_service.synthesize_historical_levy_payments()
#            → demo_bank_transactions (Mongo staging, source_type=synthetic_from_budget) (building-scoped)
# Related: backend/services/historical_levy_reconstruction_service.py (the actual logic — this file
#          is a thin shim, same pattern as the other CLI-invoked scripts left behind by the
#          c9755410 demo-integrations extraction)
#          backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py
#          backend/routers/financial_matching.py  (POST /financial/matching/historical-reconstruction/run
#          — same logic, triggerable without shell access)
# Collection: annual_levies, units, demo_bank_transactions

WHY THIS EXISTS
---------------
A building's MongoDB may have no levy_payment records prior to its StrataOS
migration. The historical payment data was recorded outside StrataOS and was
never imported. However, annual_levies budget documents exist for each year, and
each unit's unit-of-entitlement (UOE) value is known.

This script generates ONE synthetic levy payment per unit per quarter per year,
using:
    amount = unit_uoe × levy_per_uoe_quarterly   (admin fund)
    amount = unit_uoe × levy_per_uoe_quarterly   (sinking fund)

Each transaction is tagged:
    source_type = "synthetic_from_budget"
    is_synthetic = True
    is_test_data = False   (this is real demo data, not a throwaway fixture)

2026-07-13: the actual synthesis logic (quarter-schedule handling, idempotency,
unit/levy loading) moved to
`backend/services/historical_levy_reconstruction_service.py` — GAP-FIN-015
criterion 6 asked for a reusable, building-agnostic, importable service rather
than a one-off script with its own private DB connection. This file is now a
thin CLI wrapper: argument parsing + Mongo connection setup only, calling that
service. Building-agnostic already (this script never hardcoded East Gate's
building_id — `--building-id` was always required), so behavior is unchanged.

QUARTERS
--------
Standard strata quarter payment dates (East Gate pays quarterly in advance):
  Q1: 01 Jan   Q2: 01 Apr   Q3: 01 Jul   Q4: 01 Oct

Special case — 2021 (13-month opening year):
  Dec 2020 opening quarter  →  payment_date = 2020-12-01
  Q1-Q4 2021                →  normal quarterly dates

IDEMPOTENCY
-----------
Each record uses the same SHA-256 idempotency_key scheme as migration_024.
Running this script twice produces no duplicates.

PREREQUISITES
-------------
  1. migration_023_demo_bank_indexes.py (unique index on idempotency_key)
  2. MONGO_URL / MONGODB_URI must point to port 27018 (auth'd instance)
     OR script auto-reads from backend/.env

RUN
---
  python3 backend/scripts/migrations/migration_026_synthesize_historical_levy_payments.py --dry-run
  python3 backend/scripts/migrations/migration_026_synthesize_historical_levy_payments.py

FLAGS
-----
  --building-id   Override building_id (default: env DEFAULT_BUILDING_ID or prompt)
  --from-year     First year to synthesise (default: 2021)
  --to-year       Last year to synthesise (default: 2024, excludes years with real data)
  --include-2025  Also synthesise 2025 (only useful if levy_payments are sparse for that year)
  --dry-run       Print what would be written without touching MongoDB
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_backend = os.path.normpath(os.path.join(_here, "..", ".."))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import motor.motor_asyncio

from services.historical_levy_reconstruction_service import synthesize_historical_levy_payments

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _read_env_from_file() -> dict[str, str]:
    """Read key=value pairs from backend/.env."""
    env_path = os.path.join(_backend, ".env")
    result: dict[str, str] = {}
    if not os.path.exists(env_path):
        return result
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            result[key.strip()] = val.strip().strip('"').strip("'")
    return result


def _get_mongo_url() -> tuple[str, str]:
    """Return (mongo_url, db_name) from env or backend/.env."""
    env = dict(os.environ)
    dot_env = _read_env_from_file()
    merged = {**dot_env, **env}  # shell env takes priority
    mongo_url = merged.get("MONGODB_URI") or merged.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = merged.get("MONGODB_DB") or merged.get("DB_NAME", "strataos_production")
    return mongo_url, db_name


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/migrations/migration_026_synthesize_historical_levy_payments.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(
        description="Synthesise historical Demo Bank levy payments from annual_levies budgets"
    )
    parser.add_argument(
        "--building-id",
        default=os.environ.get("DEFAULT_BUILDING_ID") or "",
    )
    parser.add_argument("--from-year", type=int, default=2021)
    parser.add_argument("--to-year", type=int, default=2024)
    parser.add_argument(
        "--include-2025",
        action="store_true",
        help="Also synthesise 2025 (only if levy_payments are sparse for that year)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    building_id = args.building_id.strip()
    if not building_id:
        building_id = input("building_id (e.g. 13195): ").strip()
    if not building_id:
        print("ERROR: --building-id is required")
        sys.exit(1)

    to_year = args.to_year
    if args.include_2025:
        to_year = max(to_year, 2025)

    mongo_url, db_name = _get_mongo_url()
    logger.info("Connecting to %s / %s", mongo_url.split("@")[-1], db_name)

    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    try:
        result = await synthesize_historical_levy_payments(
            db,
            building_id=building_id,
            from_year=args.from_year,
            to_year=to_year,
            dry_run=args.dry_run,
        )
        print("\n-- Synthesis Result --------------------------")
        for k, v in result.items():
            print(f"  {k}: {v}")
        if args.dry_run:
            print("\n  [DRY RUN] No records written. Re-run without --dry-run to apply.")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
