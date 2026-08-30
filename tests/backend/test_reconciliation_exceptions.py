"""Tests for services/reconstruction_generators/reconciliation_exceptions.py.

Deliberately not a propose/approve workflow — created directly, recorded in the caller's audit
log. The core correctness property under test: only effect_scope="annual_control_adjustment"
entries may ever change an accounting result (net_annual_control_adjustments()); every other
scope must have zero arithmetic effect.
"""
from __future__ import annotations

import pytest

from services.reconstruction_generators.reconciliation_exceptions import (
    EFFECT_SCOPES,
    REASON_CODES,
    ReconciliationExceptionError,
    create_exception,
    list_exceptions,
    net_annual_control_adjustments,
)

BUILDING_ID = "16244"


class _Cursor:
    def __init__(self, items):
        self._items = items

    def to_list(self, _n):
        async def _inner():
            return list(self._items)
        return _inner()


class _FakeCollection:
    def __init__(self):
        self._docs: list[dict] = []

    async def insert_one(self, doc):
        self._docs.append(dict(doc))

    def find(self, query, projection=None):
        def matches(doc):
            for k, v in query.items():
                if isinstance(v, dict) and "$gte" in v:
                    if not (v["$gte"] <= doc.get(k) <= v.get("$lte", v["$gte"])):
                        return False
                elif isinstance(v, dict) and "$ne" in v:
                    if doc.get(k) == v["$ne"]:
                        return False
                elif doc.get(k) != v:
                    return False
            return True
        return _Cursor([d for d in self._docs if matches(d)])


class _FakeMongoDb:
    def __init__(self):
        self.reconciliation_annual_exceptions = _FakeCollection()


class TestCreateException:
    @pytest.mark.asyncio
    async def test_create_defaults_is_test_data_false(self):
        db = _FakeMongoDb()
        doc = await create_exception(
            db, building_id=BUILDING_ID, year=2021, fund_type="admin", amount_cents=4071781,
            direction="credit", reason_code="OPENING_BALANCE_BRIDGE", effect_scope="opening_rollforward",
            note="2021->2022 opening bridge", created_by="user-1",
        )
        assert doc["is_test_data"] is False
        assert doc["id"]

    @pytest.mark.asyncio
    async def test_invalid_reason_code_rejected(self):
        db = _FakeMongoDb()
        with pytest.raises(ReconciliationExceptionError, match="reason_code"):
            await create_exception(
                db, building_id=BUILDING_ID, year=2021, fund_type="admin", amount_cents=100,
                direction="credit", reason_code="NOT_A_REAL_CODE", effect_scope="informational",
                note=None, created_by="user-1",
            )

    @pytest.mark.asyncio
    async def test_invalid_effect_scope_rejected(self):
        db = _FakeMongoDb()
        with pytest.raises(ReconciliationExceptionError, match="effect_scope"):
            await create_exception(
                db, building_id=BUILDING_ID, year=2021, fund_type="admin", amount_cents=100,
                direction="credit", reason_code="CUTOFF_MISMATCH", effect_scope="not_a_real_scope",
                note=None, created_by="user-1",
            )

    @pytest.mark.asyncio
    async def test_invalid_direction_rejected(self):
        db = _FakeMongoDb()
        with pytest.raises(ReconciliationExceptionError, match="direction"):
            await create_exception(
                db, building_id=BUILDING_ID, year=2021, fund_type="admin", amount_cents=100,
                direction="sideways", reason_code="CUTOFF_MISMATCH", effect_scope="informational",
                note=None, created_by="user-1",
            )


class TestListExceptions:
    @pytest.mark.asyncio
    async def test_filters_by_year_range_and_building(self):
        db = _FakeMongoDb()
        await create_exception(db, building_id=BUILDING_ID, year=2021, fund_type="admin", amount_cents=100, direction="credit", reason_code="CUTOFF_MISMATCH", effect_scope="informational", note=None, created_by="u1")
        await create_exception(db, building_id=BUILDING_ID, year=2025, fund_type="admin", amount_cents=100, direction="credit", reason_code="CUTOFF_MISMATCH", effect_scope="informational", note=None, created_by="u1")
        await create_exception(db, building_id="77001", year=2021, fund_type="admin", amount_cents=100, direction="credit", reason_code="CUTOFF_MISMATCH", effect_scope="informational", note=None, created_by="u1")

        result = await list_exceptions(db, building_id=BUILDING_ID, financial_year_start=2021, financial_year_end=2022)
        assert len(result) == 1
        assert result[0]["year"] == 2021

    @pytest.mark.asyncio
    async def test_excludes_test_data(self):
        db = _FakeMongoDb()
        await create_exception(db, building_id=BUILDING_ID, year=2021, fund_type="admin", amount_cents=100, direction="credit", reason_code="CUTOFF_MISMATCH", effect_scope="informational", note=None, created_by="u1", is_test_data=True)
        result = await list_exceptions(db, building_id=BUILDING_ID, financial_year_start=2021, financial_year_end=2021)
        assert result == []


class TestNettingOnlyAnnualControlAdjustmentScope:
    def test_only_annual_control_adjustment_scope_contributes(self):
        exceptions = [
            {"year": 2021, "fund_type": "admin", "amount_cents": 1000, "direction": "credit", "effect_scope": "informational"},
            {"year": 2021, "fund_type": "admin", "amount_cents": 2000, "direction": "credit", "effect_scope": "category_detail"},
            {"year": 2021, "fund_type": "admin", "amount_cents": 3000, "direction": "credit", "effect_scope": "opening_rollforward"},
            {"year": 2021, "fund_type": "admin", "amount_cents": 4000, "direction": "credit", "effect_scope": "annual_control_adjustment"},
        ]
        assert net_annual_control_adjustments(exceptions, year=2021, fund_type="admin") == 4000

    def test_debit_direction_subtracts(self):
        exceptions = [
            {"year": 2021, "fund_type": "admin", "amount_cents": 4000, "direction": "debit", "effect_scope": "annual_control_adjustment"},
        ]
        assert net_annual_control_adjustments(exceptions, year=2021, fund_type="admin") == -4000

    def test_no_matching_year_fund_contributes_nothing(self):
        exceptions = [
            {"year": 2022, "fund_type": "admin", "amount_cents": 4000, "direction": "credit", "effect_scope": "annual_control_adjustment"},
            {"year": 2021, "fund_type": "sinking", "amount_cents": 4000, "direction": "credit", "effect_scope": "annual_control_adjustment"},
        ]
        assert net_annual_control_adjustments(exceptions, year=2021, fund_type="admin") == 0

    def test_empty_list_is_zero(self):
        assert net_annual_control_adjustments([], year=2021, fund_type="admin") == 0


def test_reason_codes_and_effect_scopes_are_the_documented_sets():
    assert REASON_CODES == {
        "OPENING_BALANCE_BRIDGE", "ACTUAL_RECEIPTS_UNAVAILABLE", "CATEGORY_DETAIL_INCOMPLETE",
        "CASH_ACCRUAL_DIFFERENCE", "CUTOFF_MISMATCH", "OWNER_ARREARS_OR_CREDITS",
        "OTHER_INCOME_UNALLOCATED",
    }
    assert EFFECT_SCOPES == {"informational", "category_detail", "opening_rollforward", "annual_control_adjustment"}
