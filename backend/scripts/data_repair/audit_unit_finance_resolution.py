"""Audit owner/unit finance resolution for a user account.

# @featuretrace:finance-owner-dashboard — diagnostics for owner account → unit → levy ledger resolution.
# Layer: seed
# Data flow: users/user_units/units → unit_levy_ledger/levy_payments/strata_owners/lot_financial_summary (building-scoped).
# Related: backend/utils/unit_number.py
#           backend/scripts/data_repair/repair_eastgate_unit_number_canonicalisation.py
# Collection: unit_levy_ledger

Why this exists
---------------
Owner dashboard finance bugs are usually not missing payment rows. They are often
caused by one of these mismatches:

1. The user profile stores a display unit value such as ``87`` or ``U87`` while
   finance collections store canonical values such as ``TH087``.
2. Co-owner records exist in ``units.owner_name_b`` / ``owner_email_b`` or
   ``user_units`` with missing ``role_at_unit`` and are not picked up by strict
   owner-only queries.
3. The dashboard reads a stale derived collection such as ``lot_financial_summary``
   instead of ``unit_levy_ledger``.
4. Strata Web scraper snapshots were stored in ``strata_owners`` but the derived
   payments were not synced into ``unit_levy_ledger`` for the canonical unit key.

Example — Unit 87 East Gate (connection defaults come from backend/.env
``MONGO_URL`` + ``DB_NAME``; the production database is ``strataos_production``):

    python3 backend/scripts/data_repair/audit_unit_finance_resolution.py \
        --building-id 13195 \
        --email avneet@eastgateresidences.com.au \
        --unit 87 \
        --year 2026

Optional repair for safe relationship backfill only:

    ... --fix-user-unit-role

This repair only sets missing ``role_at_unit`` to ``owner`` for matching active
user_units rows. It does not alter payments or balances.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from pprint import pprint
from typing import Any
from urllib.parse import urlparse

from motor.motor_asyncio import AsyncIOMotorClient


def _db_name(uri: str, fallback: str) -> str:
    """Generated function header.

    Function: _db_name
    Path: backend/scripts/data_repair/audit_unit_finance_resolution.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parsed = urlparse(uri)
    if parsed.path and parsed.path != "/":
        return parsed.path.lstrip("/").split("?")[0]
    return fallback


def _backend_env() -> dict[str, str]:
    """Read backend/.env so the script's defaults match the running backend."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    values: dict[str, str] = {}
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


try:
    from utils.unit_number import unit_number_candidates as _unit_candidates
except ImportError:  # Allow running from repository root without PYTHONPATH tweaks.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from utils.unit_number import unit_number_candidates as _unit_candidates


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/data_repair/audit_unit_finance_resolution.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Audit owner/unit finance resolution")
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--email", required=True)
    parser.add_argument("--unit", default=None, help="Optional asserted unit number, e.g. 87 or TH087")
    parser.add_argument("--year", default="2026")
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--mongo-db", default=None)
    parser.add_argument("--fix-user-unit-role", action="store_true")
    args = parser.parse_args()

    backend_env = _backend_env()
    uri = (args.mongo_uri or os.environ.get("MONGODB_URI") or os.environ.get("MONGO_URL")
           or backend_env.get("MONGO_URL") or "mongodb://127.0.0.1:27017")
    db_name = (args.mongo_db or os.environ.get("MONGODB_DB") or os.environ.get("DB_NAME")
               or backend_env.get("DB_NAME") or _db_name(uri, "strataos_production"))
    client = AsyncIOMotorClient(uri)
    db = client[db_name]

    email = args.email.strip().lower()
    building_id = args.building_id
    year = args.year

    print(f"Mongo database: {db_name}")
    print(f"Building: {building_id}; email: {email}; asserted unit: {args.unit}; year: {year}\n")

    users = await db.users.find(
        {"$or": [{"email": email}, {"portal_email": email}, {"owner_email": email}]},
        {"_id": 0, "id": 1, "email": 1, "portal_email": 1, "full_name": 1, "role": 1, "unit_number": 1,
         "owned_units": 1, "is_active": 1, "is_approved": 1, "status": 1},
    ).to_list(20)
    print("== users ==")
    pprint(users)

    user_ids = [u.get("id") for u in users if u.get("id")]
    relationship_query: dict[str, Any] = {"building_id": building_id}
    if user_ids:
        relationship_query["user_id"] = {"$in": user_ids}
    user_units = await db.user_units.find(
        relationship_query,
        {"_id": 0, "id": 1, "user_id": 1, "unit_number": 1, "role_at_unit": 1,
         "is_active": 1, "is_primary": 1, "start_date": 1, "actual_end_date": 1, "end_date": 1, "notes": 1},
    ).to_list(50)
    print("\n== user_units for user ==")
    pprint(user_units)

    unit_values: list[str] = []
    for u in users:
        unit_values.extend(_unit_candidates(u.get("unit_number")))
        for owned in u.get("owned_units") or []:
            unit_values.extend(_unit_candidates(owned))
    for rel in user_units:
        unit_values.extend(_unit_candidates(rel.get("unit_number")))
    unit_values.extend(_unit_candidates(args.unit))
    unit_values = list(dict.fromkeys(unit_values))

    print("\n== unit key candidates ==")
    pprint(unit_values)

    units = await db.units.find(
        {"building_id": building_id, "$or": [
            {"unit_number": {"$in": unit_values}},
            {"lot_number": {"$in": unit_values}},
            {"owner_email": email},
            {"owner_email_b": email},
        ]},
        {"_id": 0, "unit_number": 1, "lot_number": 1, "entitlement": 1,
         "owner_name": 1, "owner_email": 1, "owner_name_b": 1, "owner_email_b": 1},
    ).to_list(20)
    print("\n== matching units ==")
    pprint(units)

    canonical_units = list(dict.fromkeys([u.get("unit_number") for u in units if u.get("unit_number")] + unit_values))

    ledger = await db.unit_levy_ledger.find(
        {"building_id": building_id, "year": year, "unit_number": {"$in": canonical_units}},
        {"_id": 0},
    ).sort("unit_number", 1).to_list(50)
    print("\n== unit_levy_ledger matches ==")
    pprint(ledger)

    payments = await db.levy_payments.find(
        {"building_id": building_id, "year": year, "unit_number": {"$in": canonical_units}},
        {"_id": 0, "unit_number": 1, "amount": 1, "amount_cents": 1, "payment_date": 1,
         "quarter": 1, "status": 1, "source": 1, "receipt_number": 1, "notes": 1},
    ).sort("payment_date", 1).to_list(100)
    print("\n== levy_payments matches ==")
    pprint(payments)

    snapshots = await db.strata_owners.find(
        {"building_id": building_id, "$or": [
            {"unit_number": {"$in": canonical_units}},
            {"lot": {"$in": canonical_units}},
            {"owner_email": email},
            {"owner_email_b": email},
        ]},
        {"_id": 0},
    ).to_list(20)
    print("\n== strata_owners / Strata Web snapshot matches ==")
    pprint(snapshots)

    derived = await db.lot_financial_summary.find(
        {"building_id": building_id, "year": year, "unit_number": {"$in": canonical_units}},
        {"_id": 0},
    ).to_list(20)
    print("\n== lot_financial_summary matches (derived; not canonical) ==")
    pprint(derived)

    print("\n== findings ==")
    if not ledger and units:
        print("PROBLEM: Unit exists but no ledger row matched. Check unit_number canonicalisation and year.")
    if ledger and not payments:
        print("NOTE: Ledger has payment totals but no levy_payments detail matched. This can happen after scraper-derived ledger updates.")
    for rel in user_units:
        if rel.get("is_active") and rel.get("role_at_unit") in (None, ""):
            print(f"PROBLEM: active user_units row {rel.get('id')} has missing role_at_unit; owner resolution may skip it.")
    for l in ledger:
        print(
            f"Ledger {l.get('unit_number')}: total_paid={l.get('total_paid')} "
            f"net_balance={l.get('net_balance')} strata_web_balance_snapshot={l.get('strata_web_balance_snapshot')}"
        )

    if args.fix_user_unit_role and user_ids:
        result = await db.user_units.update_many(
            {
                "building_id": building_id,
                "user_id": {"$in": user_ids},
                "unit_number": {"$in": canonical_units},
                "is_active": True,
                "$or": [{"role_at_unit": {"$exists": False}}, {"role_at_unit": None}, {"role_at_unit": ""}],
            },
            {"$set": {"role_at_unit": "owner", "updated_by": "audit_unit_finance_resolution.py"}},
        )
        print(f"\nRepair applied: matched={result.matched_count}, modified={result.modified_count}")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
