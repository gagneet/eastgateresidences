"""MongoDB read-only adapter for shadow validation (LegacyReadPort).

Used ONLY during the dual-read phase to compare Postgres computed values
against MongoDB computed values. Never used for writes.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from database import db as mongo_db
from request_context import set_ctx_building_id


class MongoLegacyReadAdapter:
    """Implements LegacyReadPort using the existing Motor database wrapper."""

    async def get_lot_balance_cents(
            self, building_id: str, lot_id: str, as_of_date: date
    ) -> int:
        """Return computed lot balance from MongoDB unit_levy_ledger (in cents)."""
        set_ctx_building_id(building_id)
        ledger = await mongo_db.unit_levy_ledger.find_one(
            {"lot_id": lot_id, "is_test_data": {"$ne": True}}
        )
        if ledger is None:
            return 0
        outstanding = ledger.get("outstanding_amount", 0)
        # MongoDB stores amounts as floats in dollars — convert to cents
        return int(round(float(outstanding) * 100))

    async def get_trust_account_balance_cents(
            self, building_id: str, trust_account_id: str
    ) -> int:
        """Return trust account current balance from MongoDB (in cents)."""
        set_ctx_building_id(building_id)
        account = await mongo_db.trust_accounts_v2.find_one(
            {"_id": trust_account_id, "is_test_data": {"$ne": True}}
        )
        if account is None:
            return 0
        balance = account.get("current_balance", 0)
        return int(round(float(balance) * 100))
