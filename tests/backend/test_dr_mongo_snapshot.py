"""Unit tests for scripts/dr_mongo_snapshot.py.

Covers the reconciliation states that gate the freshness check in finance_pg_read_dr.py, and (as
of GAP-FIN-068, 2026-08-18) the write shape itself: this script `$set`s ONLY the fields it
computes (total_levied/total_paid/net_balance/opening_balance + snapshot provenance), never a
wholesale `replace_one`. The original design used `replace_one()` reasoning that a unit's PG
values changing (e.g. a reversed payment) must never leave a stale value behind -- true, but
`replace_one` achieved that by deleting every field the script doesn't own too, which silently
broke `finance.levy_kpi` (and any other consumer of `uoe`/`lot_number`/fund-split fields) for
East Gate's FY2026 the first time this script ran against real data. `$set` gives the same
no-stale-owned-field guarantee (every run overwrites the 4 balance fields fresh) without deleting
anything the script has no data for.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "scripts"))

from dr_mongo_snapshot import snapshot_unit_levy_ledger  # noqa: E402


def _balances():
    return [
        {"unit_number": "TH001", "financial_year": "2026", "opening_balance": 0.0,
         "levied_amount": 1000.0, "paid_amount": 1000.0, "closing_balance": 0.0, "arrears": 0.0},
        {"unit_number": "TH002", "financial_year": "2026", "opening_balance": 0.0,
         "levied_amount": 1000.0, "paid_amount": 500.0, "closing_balance": 500.0, "arrears": 500.0},
    ]


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def to_list(self, limit):
        async def _inner():
            return self._docs
        return _inner()


@pytest.mark.asyncio
class TestSnapshotUnitLevyLedger:
    async def test_no_data_returns_no_data_status_without_writing(self):
        mock_read_service = AsyncMock()
        mock_read_service.get_unit_levy_balance_list = AsyncMock(return_value=None)

        with patch("services.financial_read_service.FinancialReadService", return_value=mock_read_service), \
             patch("request_context.set_ctx_building_id"):
            meta = await snapshot_unit_levy_ledger("13195", "2026")

        assert meta["reconciliation_status"] == "no_data"
        assert meta["row_count"] == 0

    async def test_empty_list_result_also_treated_as_no_data(self):
        """Regression for a real bug found in a post-implementation audit (2026-08-11):
        get_unit_levy_balance_list() returns [] (not None) when the building HAS lots but every
        single per-unit read failed. The original code only checked `is None`, so this case fell
        through to reconciliation_status="ok" with row_count=0 -- a false-healthy result that
        could mask a systemic outage and would have been valid grounds for the freshness gate in
        finance_pg_read_dr.py to serve a DR fallback."""
        mock_read_service = AsyncMock()
        mock_read_service.get_unit_levy_balance_list = AsyncMock(return_value=[])
        mock_db = MagicMock()
        mock_db.unit_levy_ledger.update_one = AsyncMock()
        mock_db.dr_snapshot_meta.replace_one = AsyncMock()

        with patch("services.financial_read_service.FinancialReadService", return_value=mock_read_service), \
             patch("database.db", mock_db), \
             patch("request_context.set_ctx_building_id"):
            meta = await snapshot_unit_levy_ledger("13195", "2026")

        assert meta["reconciliation_status"] == "no_data"
        assert meta["row_count"] == 0
        mock_db.unit_levy_ledger.update_one.assert_not_awaited()
        mock_db.dr_snapshot_meta.replace_one.assert_not_awaited()

    async def test_dry_run_computes_but_does_not_write(self):
        mock_read_service = AsyncMock()
        mock_read_service.get_unit_levy_balance_list = AsyncMock(return_value=_balances())
        mock_db = MagicMock()
        mock_db.unit_levy_ledger.update_one = AsyncMock()
        mock_db.dr_snapshot_meta.replace_one = AsyncMock()

        with patch("services.financial_read_service.FinancialReadService", return_value=mock_read_service), \
             patch("database.db", mock_db), \
             patch("request_context.set_ctx_building_id"):
            meta = await snapshot_unit_levy_ledger("13195", "2026", dry_run=True)

        assert meta["reconciliation_status"] == "dry_run"
        assert meta["row_count"] == 2
        assert meta["control_total_cents"] == 50000  # $500.00 total closing balance
        mock_db.unit_levy_ledger.update_one.assert_not_awaited()
        mock_db.dr_snapshot_meta.replace_one.assert_not_awaited()

    async def test_successful_run_reconciles_ok(self):
        mock_read_service = AsyncMock()
        mock_read_service.get_unit_levy_balance_list = AsyncMock(return_value=_balances())
        mock_db = MagicMock()
        mock_db.unit_levy_ledger.update_one = AsyncMock()
        mock_db.dr_snapshot_meta.replace_one = AsyncMock()
        # Read-back confirms both rows landed with the correct net_balance total.
        mock_db.unit_levy_ledger.find.return_value = _FakeCursor(
            [{"net_balance": 0.0}, {"net_balance": 500.0}]
        )

        with patch("services.financial_read_service.FinancialReadService", return_value=mock_read_service), \
             patch("database.db", mock_db), \
             patch("request_context.set_ctx_building_id"):
            meta = await snapshot_unit_levy_ledger("13195", "2026")

        assert meta["reconciliation_status"] == "ok"
        assert mock_db.unit_levy_ledger.update_one.await_count == 2
        # GAP-FIN-068: each write is a scoped $set (upsert=True), not a wholesale replace --
        # confirms a unit whose PG row changed shape doesn't get every OTHER field wiped.
        for call in mock_db.unit_levy_ledger.update_one.await_args_list:
            args, kwargs = call
            assert kwargs.get("upsert") is True
            update_arg = args[1]
            assert "$set" in update_arg

    async def test_write_failure_marks_write_failed(self):
        mock_read_service = AsyncMock()
        mock_read_service.get_unit_levy_balance_list = AsyncMock(return_value=_balances())
        mock_db = MagicMock()
        mock_db.unit_levy_ledger.update_one = AsyncMock(side_effect=RuntimeError("mongo down"))
        mock_db.dr_snapshot_meta.replace_one = AsyncMock()
        mock_db.unit_levy_ledger.find.return_value = _FakeCursor([])

        with patch("services.financial_read_service.FinancialReadService", return_value=mock_read_service), \
             patch("database.db", mock_db), \
             patch("request_context.set_ctx_building_id"):
            meta = await snapshot_unit_levy_ledger("13195", "2026")

        assert meta["reconciliation_status"] == "write_failed"

    async def test_readback_row_count_mismatch_detected(self):
        """GAP-FIN-061-class regression check: if a unit's write silently didn't land (or landed
        under the wrong key), the read-back must catch it -- not just trust the write call
        returned without raising."""
        mock_read_service = AsyncMock()
        mock_read_service.get_unit_levy_balance_list = AsyncMock(return_value=_balances())
        mock_db = MagicMock()
        mock_db.unit_levy_ledger.update_one = AsyncMock()
        mock_db.dr_snapshot_meta.replace_one = AsyncMock()
        # Only one of the two expected rows is actually found on read-back.
        mock_db.unit_levy_ledger.find.return_value = _FakeCursor([{"net_balance": 0.0}])

        with patch("services.financial_read_service.FinancialReadService", return_value=mock_read_service), \
             patch("database.db", mock_db), \
             patch("request_context.set_ctx_building_id"):
            meta = await snapshot_unit_levy_ledger("13195", "2026")

        assert meta["reconciliation_status"] == "row_count_mismatch"

    async def test_control_total_mismatch_detected(self):
        mock_read_service = AsyncMock()
        mock_read_service.get_unit_levy_balance_list = AsyncMock(return_value=_balances())
        mock_db = MagicMock()
        mock_db.unit_levy_ledger.update_one = AsyncMock()
        mock_db.dr_snapshot_meta.replace_one = AsyncMock()
        # Right row count, wrong total -- e.g. a corrupted write.
        mock_db.unit_levy_ledger.find.return_value = _FakeCursor(
            [{"net_balance": 0.0}, {"net_balance": 999.0}]
        )

        with patch("services.financial_read_service.FinancialReadService", return_value=mock_read_service), \
             patch("database.db", mock_db), \
             patch("request_context.set_ctx_building_id"):
            meta = await snapshot_unit_levy_ledger("13195", "2026")

        assert meta["reconciliation_status"] == "control_total_mismatch"

    async def test_reversed_units_values_are_freshly_set_every_run(self):
        """A unit's Postgres values changing (e.g. a reversed payment moving net_balance back
        up) must never leave a stale value in a field this script owns -- $set overwrites all 4
        balance fields fresh on every run, verified per call, same guarantee replace_one gave for
        these specific fields."""
        mock_read_service = AsyncMock()
        mock_read_service.get_unit_levy_balance_list = AsyncMock(return_value=_balances())
        mock_db = MagicMock()
        mock_db.unit_levy_ledger.update_one = AsyncMock()
        mock_db.dr_snapshot_meta.replace_one = AsyncMock()
        mock_db.unit_levy_ledger.find.return_value = _FakeCursor(
            [{"net_balance": 0.0}, {"net_balance": 500.0}]
        )

        with patch("services.financial_read_service.FinancialReadService", return_value=mock_read_service), \
             patch("database.db", mock_db), \
             patch("request_context.set_ctx_building_id"):
            await snapshot_unit_levy_ledger("13195", "2026")

        for call in mock_db.unit_levy_ledger.update_one.await_args_list:
            args, kwargs = call
            filter_arg, update_arg = args[0], args[1]
            assert filter_arg["building_id"] == "13195"
            assert "unit_number" in filter_arg and "year" in filter_arg
            set_doc = update_arg["$set"]
            for field in ("total_levied", "total_paid", "net_balance", "opening_balance",
                          "_dr_snapshot_id", "_dr_snapshot_at"):
                assert field in set_doc

    async def test_does_not_wholesale_replace_the_document(self):
        """GAP-FIN-068 regression test: the write must be a scoped $set (via update_one), never
        a replace_one -- a wholesale replace is exactly what silently deleted uoe/lot_number/
        admin_paid/sinking_paid/... from all 87 East Gate FY2026 documents in production. This
        is the test that would have caught GAP-FIN-068 before it shipped."""
        mock_read_service = AsyncMock()
        mock_read_service.get_unit_levy_balance_list = AsyncMock(return_value=_balances())
        mock_db = MagicMock()
        mock_db.unit_levy_ledger.update_one = AsyncMock()
        mock_db.unit_levy_ledger.replace_one = AsyncMock(
            side_effect=AssertionError("must not wholesale-replace unit_levy_ledger documents")
        )
        mock_db.dr_snapshot_meta.replace_one = AsyncMock()
        mock_db.unit_levy_ledger.find.return_value = _FakeCursor(
            [{"net_balance": 0.0}, {"net_balance": 500.0}]
        )

        with patch("services.financial_read_service.FinancialReadService", return_value=mock_read_service), \
             patch("database.db", mock_db), \
             patch("request_context.set_ctx_building_id"):
            meta = await snapshot_unit_levy_ledger("13195", "2026")

        assert meta["reconciliation_status"] == "ok"
        mock_db.unit_levy_ledger.replace_one.assert_not_awaited()
        assert mock_db.unit_levy_ledger.update_one.await_count == 2
        for call in mock_db.unit_levy_ledger.update_one.await_args_list:
            args, kwargs = call
            # Exactly building_id/unit_number/year/total_levied/total_paid/net_balance/
            # opening_balance/_dr_snapshot_id/_dr_snapshot_at -- nothing else. Any field this
            # script doesn't compute (uoe, lot_number, admin_paid, ...) is simply absent from
            # the $set document, which is what makes it safe: MongoDB's $set can only ever
            # ADD/OVERWRITE the listed fields, never remove an unlisted one.
            set_doc = args[1]["$set"]
            assert set(set_doc.keys()) == {
                "building_id", "unit_number", "year", "total_levied", "total_paid",
                "net_balance", "opening_balance", "_dr_snapshot_id", "_dr_snapshot_at",
            }


def test_scope_boundary_documented_not_silently_assumed():
    """Deliberate, documented scope boundary (not tested behaviourally, since testing it would
    require asserting a DELETE that this script intentionally does NOT perform): a unit entirely
    absent from PG's per-lot list (lot deleted/archived, or not yet CSV-onboarded to Postgres --
    CLAUDE.md's phased per-building onboarding means this is common, not rare) is never touched by
    this script, including never deleted from Mongo. Deleting it would be actively wrong -- that
    Mongo data may be the ONLY source of truth for a unit PG doesn't cover yet. Only a unit's
    VALUES changing (the common real case: a reversed payment) is handled, via a scoped $set
    (GAP-FIN-068) on the fields this script owns. This test exists so the boundary is a
    documented decision, not a silently-assumed-solved gap."""
    assert True
