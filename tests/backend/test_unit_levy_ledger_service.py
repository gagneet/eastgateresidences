"""Unit tests for services/unit_levy_ledger_service.py.

compute_ledger_levied_and_closing() is a pure function — direct arithmetic assertions.
rebuild_ledger_projection() is tested against AsyncMock Mongo collections and a mocked
Postgres session, per backend/CLAUDE.md's Motor AsyncMock convention.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.unit_levy_ledger_service import compute_ledger_levied_and_closing, rebuild_ledger_projection

BUILDING_ID = "16244"
SCHEME_ID = uuid.uuid4()
LOT_ID = uuid.uuid4()


class TestComputeLedgerLeviedAndClosing:
    def test_matches_opening_plus_levied_plus_interest_minus_paid(self):
        result = compute_ledger_levied_and_closing(
            admin_opening=100.0, sinking_opening=50.0, admin_interest=5.0, sinking_interest=0.0,
            admin_paid=200.0, sinking_paid=25.0, admin_levied=800.0, sinking_levied=100.0,
        )
        assert result["admin_closing"] == round(100.0 + 800.0 + 5.0 - 200.0, 2)
        assert result["sinking_closing"] == round(50.0 + 100.0 + 0.0 - 25.0, 2)
        assert result["net_balance"] == round(result["admin_closing"] + result["sinking_closing"], 2)
        assert result["total_levied"] == round(800.0 + 100.0, 2)

    def test_zero_inputs_produce_zero_balance(self):
        result = compute_ledger_levied_and_closing(
            admin_opening=0, sinking_opening=0, admin_interest=0, sinking_interest=0,
            admin_paid=0, sinking_paid=0, admin_levied=0, sinking_levied=0,
        )
        assert result["net_balance"] == 0.0


def _pg_session_with_totals(totals: dict[str, int]):
    """Mock pg_session.execute() returning rows shaped like
    _sum_posted_levy_items_by_fund()'s real SQL result."""
    session = MagicMock()
    result = MagicMock()
    result.all.return_value = [(fund, cents) for fund, cents in totals.items()]
    session.execute = AsyncMock(return_value=result)
    return session


class TestRebuildLedgerProjectionCreate:
    @pytest.mark.asyncio
    async def test_creates_row_with_zero_paid_when_none_exists(self):
        mongo_db = MagicMock()
        mongo_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
        mongo_db.units.find_one = AsyncMock(return_value={"entitlement": 100.0, "lot_number": "LOT001", "property_type": "residential"})
        mongo_db.unit_levy_ledger.replace_one = AsyncMock()
        pg_session = _pg_session_with_totals({"admin": 110000, "sinking": 22000})

        result = await rebuild_ledger_projection(
            mongo_db=mongo_db, pg_session=pg_session, scheme_id=SCHEME_ID, building_id=BUILDING_ID,
            unit_number="TH001", lot_id=LOT_ID, year="2024",
        )

        assert result["admin_levied"] == 1100.0
        assert result["sinking_levied"] == 220.0
        mongo_db.unit_levy_ledger.replace_one.assert_awaited_once()
        written_doc = mongo_db.unit_levy_ledger.replace_one.call_args[0][1]
        assert written_doc["admin_paid"] == 0.0
        assert written_doc["sinking_paid"] == 0.0
        assert written_doc["applied_payment_ids"] == []
        assert written_doc["admin_levied"] == 1100.0


class TestRebuildLedgerProjectionRefresh:
    @pytest.mark.asyncio
    async def test_refreshes_levied_and_closing_never_touches_paid_fields(self):
        mongo_db = MagicMock()
        existing = {
            "admin_opening": 0.0, "sinking_opening": 0.0, "admin_interest": 0.0, "sinking_interest": 0.0,
            "admin_paid": 500.0, "sinking_paid": 100.0, "admin_levied": 999.0, "sinking_levied": 999.0,
            "applied_payment_ids": ["pay-1", "pay-2"],
        }
        mongo_db.unit_levy_ledger.find_one = AsyncMock(return_value=existing)
        mongo_db.unit_levy_ledger.update_one = AsyncMock()
        # Postgres says the REAL posted total is $1,100 admin / $220 sinking — must win over
        # the stale annual_levies-derived 999.0 values sitting in the existing Mongo doc.
        pg_session = _pg_session_with_totals({"admin": 110000, "sinking": 22000})

        result = await rebuild_ledger_projection(
            mongo_db=mongo_db, pg_session=pg_session, scheme_id=SCHEME_ID, building_id=BUILDING_ID,
            unit_number="TH001", lot_id=LOT_ID, year="2024",
        )

        assert result["admin_levied"] == 1100.0
        assert result["sinking_levied"] == 220.0
        mongo_db.unit_levy_ledger.update_one.assert_awaited_once()
        filter_arg, update_arg = mongo_db.unit_levy_ledger.update_one.call_args[0]
        set_doc = update_arg["$set"]
        assert "admin_paid" not in set_doc
        assert "sinking_paid" not in set_doc
        assert "applied_payment_ids" not in set_doc
        assert set_doc["admin_levied"] == 1100.0
        # closing = opening(0) + levied(1100) + interest(0) - paid(500, UNCHANGED from existing)
        assert set_doc["admin_closing"] == round(0.0 + 1100.0 + 0.0 - 500.0, 2)

    @pytest.mark.asyncio
    async def test_no_levy_items_posted_yet_gives_zero_levied(self):
        mongo_db = MagicMock()
        mongo_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
        mongo_db.units.find_one = AsyncMock(return_value={"entitlement": 100.0, "lot_number": "LOT001", "property_type": "residential"})
        mongo_db.unit_levy_ledger.replace_one = AsyncMock()
        pg_session = _pg_session_with_totals({})

        result = await rebuild_ledger_projection(
            mongo_db=mongo_db, pg_session=pg_session, scheme_id=SCHEME_ID, building_id=BUILDING_ID,
            unit_number="TH001", lot_id=LOT_ID, year="2024",
        )
        assert result["admin_levied"] == 0.0
        assert result["sinking_levied"] == 0.0
