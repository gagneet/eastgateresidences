"""
Tests for services/ownership_service.py — bitemporal lot ownership resolution.
No DB access: all Motor methods are mocked with AsyncMock.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from services.ownership_service import (
    resolve_owner,
    get_ownership_history,
    record_ownership_transfer,
)


def _dt(year, month, day):
    return datetime(year, month, day, tzinfo=timezone.utc)


def _make_db(owner_record=None, history=None):
    mock_db = MagicMock()
    mock_db.ownership_periods.find_one = AsyncMock(return_value=owner_record)
    mock_db.ownership_periods.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
    ins = MagicMock()
    ins.inserted_id = "new_id"
    mock_db.ownership_periods.insert_one = AsyncMock(return_value=ins)
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=history or [])
    mock_db.ownership_periods.find = MagicMock(return_value=cursor)
    return mock_db


# ── resolve_owner ─────────────────────────────────────────────────────────────

class TestResolveOwner:
    @pytest.mark.asyncio
    async def test_returns_owner_within_period(self):
        record = {
            "building_id": "13195", "lot_id": "5",
            "owner_name": "Jane Smith",
            "effective_from": _dt(2023, 1, 1),
            "effective_to": None,
            "is_test_data": True,
        }
        mock_db = _make_db(owner_record=record)
        result = await resolve_owner("5", "13195", _dt(2025, 6, 1), db=mock_db)
        assert result is not None
        assert result["owner_name"] == "Jane Smith"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_record(self):
        mock_db = _make_db(owner_record=None)
        result = await resolve_owner("99", "13195", _dt(2025, 6, 1), db=mock_db)
        assert result is None

    @pytest.mark.asyncio
    async def test_naive_datetime_converted_to_utc(self):
        record = {"building_id": "13195", "lot_id": "5", "owner_name": "A", "is_test_data": True}
        mock_db = _make_db(owner_record=record)
        naive_dt = datetime(2025, 6, 1)  # no tzinfo
        result = await resolve_owner("5", "13195", naive_dt, db=mock_db)
        # Should not raise; query was made with UTC-aware datetime
        call_filter = mock_db.ownership_periods.find_one.call_args[0][0]
        assert call_filter["effective_from"]["$lte"].tzinfo is not None

    @pytest.mark.asyncio
    async def test_cross_building_isolation(self):
        mock_db = _make_db(owner_record=None)
        await resolve_owner("5", "16244", _dt(2025, 6, 1), db=mock_db)
        call_filter = mock_db.ownership_periods.find_one.call_args[0][0]
        assert call_filter["building_id"] == "16244"


# ── get_ownership_history ─────────────────────────────────────────────────────

class TestGetOwnershipHistory:
    @pytest.mark.asyncio
    async def test_returns_ordered_history(self):
        history = [
            {"lot_id": "7", "building_id": "13195", "owner_name": "Old Owner", "is_test_data": True},
            {"lot_id": "7", "building_id": "13195", "owner_name": "New Owner", "is_test_data": True},
        ]
        mock_db = _make_db(history=history)
        result = await get_ownership_history("7", "13195", db=mock_db)
        assert len(result) == 2
        assert result[0]["owner_name"] == "Old Owner"

    @pytest.mark.asyncio
    async def test_empty_history_returns_empty_list(self):
        mock_db = _make_db(history=[])
        result = await get_ownership_history("99", "13195", db=mock_db)
        assert result == []


# ── record_ownership_transfer ─────────────────────────────────────────────────

class TestRecordOwnershipTransfer:
    @pytest.mark.asyncio
    async def test_records_new_transfer(self):
        mock_db = _make_db()
        await record_ownership_transfer(
            lot_id="5",
            building_id="13195",
            new_owner={"owner_name": "Incoming Buyer", "is_test_data": True},
            settlement_date=_dt(2025, 5, 1),
            recorded_by="manager@test.com",
            db=mock_db,
        )
        mock_db.ownership_periods.insert_one.assert_called_once()
        inserted = mock_db.ownership_periods.insert_one.call_args[0][0]
        assert inserted["owner_name"] == "Incoming Buyer"
        assert inserted["building_id"] == "13195"

    @pytest.mark.asyncio
    async def test_idempotent_on_duplicate_settlement_date(self):
        # find_one returns existing record for same settlement date
        existing = {
            "building_id": "13195", "lot_id": "5",
            "owner_name": "Incoming Buyer",
            "effective_from": _dt(2025, 5, 1),
            "is_test_data": True,
        }
        mock_db = _make_db(owner_record=existing)
        await record_ownership_transfer(
            lot_id="5",
            building_id="13195",
            new_owner={"owner_name": "Incoming Buyer", "is_test_data": True},
            settlement_date=_dt(2025, 5, 1),
            recorded_by="manager@test.com",
            db=mock_db,
        )
        mock_db.ownership_periods.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_naive_settlement_date_converted(self):
        mock_db = _make_db()
        naive = datetime(2025, 5, 1)
        await record_ownership_transfer(
            lot_id="5",
            building_id="13195",
            new_owner={"owner_name": "Buyer", "is_test_data": True},
            settlement_date=naive,
            recorded_by="manager@test.com",
            db=mock_db,
        )
        inserted = mock_db.ownership_periods.insert_one.call_args[0][0]
        assert inserted["effective_from"].tzinfo is not None

    @pytest.mark.asyncio
    async def test_cross_building_isolation(self):
        mock_db = _make_db()
        await record_ownership_transfer(
            lot_id="5",
            building_id="16244",
            new_owner={"owner_name": "Buyer", "is_test_data": True},
            settlement_date=_dt(2025, 5, 1),
            recorded_by="manager@test.com",
            db=mock_db,
        )
        inserted = mock_db.ownership_periods.insert_one.call_args[0][0]
        assert inserted["building_id"] == "16244"
