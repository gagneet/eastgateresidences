#!/usr/bin/env python3
"""Ingest AGM building-level interest / late-fee income as financial_transactions.

Building-agnostic. The AGM audited financials record "Interest on Overdues" and "Late Fees" as
per-YEAR, per-FUND income totals (not per unit) — e.g. East Gate 2025 Admin $721.66. These are
real owner charges but cannot be split per unit from the source documents. This script creates one
building-level `financial_transactions` row (transaction_type="interest", unit_number=None) per
(year, fund) from the already-imported `levy_categories` income lines, so building income/reports
are correct and the figures flow through the transaction pipeline.

Deliberately EXCLUDES "Interest on Investments" — that is the owners corporation's OWN bank/
investment interest EARNED, not an owner overdue charge, and must not be conflated.

Idempotent (deterministic uuid5 id per building/year/fund), dry-run by DEFAULT. --apply mutates
production financial data and, per the Financial Evidence Gateway rules, needs explicit
authorisation — NOT run as part of building this script.

Usage (from backend/):
    venv/bin/python3 scripts/ingest/ingest_agm_interest_income.py --building-id 13195
    venv/bin/python3 scripts/ingest/ingest_agm_interest_income.py --building-id 13195 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

# Owner overdue interest / late fees — NOT "interest on investment(s)" (OC's own earnings).
_INTEREST_INCOME_RE = re.compile(r"interest on overdue|late fee", re.IGNORECASE)
_EXCLUDE_RE = re.compile(r"investment", re.IGNORECASE)
_NAMESPACE = uuid.UUID("6f1d2c3a-0000-5000-a000-a6d1e17e2e00")  # stable, script-local


def _norm_fund(f: str) -> str:
    f = (f or "").lower()
    return "sinking" if (f.startswith("sink") or f == "capital_works") else "admin"


async def _get_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.getenv("MONGO_URL"))
    return client[os.getenv("DB_NAME", "strataos_production")]


async def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest AGM interest/late-fee income as transactions.")
    ap.add_argument("--building-id", required=True)
    ap.add_argument("--apply", action="store_true", default=False,
                    help="Write financial_transactions. Default is dry-run.")
    args = ap.parse_args()
    dry_run = not args.apply
    building_id = args.building_id
    now = datetime.now(timezone.utc)

    db = await _get_db()

    # Read the imported AGM category lines for this building.
    cats = await db.levy_categories.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}}, {"_id": 0},
    ).to_list(None)

    # Aggregate the interest/late-fee income lines per (year, fund).
    per_year_fund: dict[tuple[str, str], float] = {}
    for c in cats:
        name = str(c.get("name") or "")
        if not _INTEREST_INCOME_RE.search(name) or _EXCLUDE_RE.search(name):
            continue
        # income lines only (skip a matching expense like "Arrears Recovery Costs")
        if str(c.get("kind") or c.get("type") or "income").lower() == "expense":
            continue
        year = str(c.get("year") or c.get("financial_year") or "")
        if not year:
            continue
        fund = _norm_fund(str(c.get("fund_type") or ""))
        amount = float(c.get("actual_amount") or 0.0)
        if amount <= 0:
            continue
        per_year_fund[(year, fund)] = round(per_year_fund.get((year, fund), 0.0) + amount, 2)

    print(f"\n{'='*66}\n  AGM interest/late-fee income ingestion — building {building_id}")
    print(f"  mode: {'DRY-RUN' if dry_run else '*** APPLY — WRITES financial_transactions ***'}\n{'='*66}")
    if not per_year_fund:
        print("  No interest-on-overdues / late-fee income lines found in levy_categories.")
        return

    written = 0
    for (year, fund), amount in sorted(per_year_fund.items()):
        tx_id = str(uuid.uuid5(_NAMESPACE, f"{building_id}:{year}:{fund}:agm_interest"))
        print(f"    {year} {fund:<8} interest/late-fee income: ${amount:,.2f}  (id={tx_id[:8]})")
        if dry_run:
            continue
        # Idempotent: skip if this deterministic row already exists.
        existing = await db.financial_transactions.find_one({"id": tx_id}, {"_id": 1})
        if existing:
            continue
        await db.financial_transactions.insert_one({
            "id": tx_id,
            "building_id": building_id,
            "plan_id": building_id,
            "financial_year": year,
            "fund_type": fund,
            "transaction_type": "interest",
            "category_name": "Interest on Overdues / Late Fees",
            "unit_number": None,
            "lot_number": None,
            "amount": amount,
            "gst_amount": 0.0,
            "description": f"AGM {year} {fund} interest/late fees on overdue levies (building-level total)",
            "reference": "agm_category_import",
            "transaction_date": f"{year}-12-31T00:00:00",
            "source": "agm_category_import",
            "is_test_data": False,
            "created_at": now.isoformat(),
        })
        written += 1

    total = round(sum(per_year_fund.values()), 2)
    print(f"\n  Source total across all years/funds: ${total:,.2f}")
    if dry_run:
        print("  DRY-RUN: nothing written. Re-run with --apply (with authorisation) to ingest.\n")
    else:
        print(f"  Wrote {written} building-level interest transactions.\n")


if __name__ == "__main__":
    asyncio.run(main())
