"""Tests for services/finance_metrics/portal_snapshot_pg_writer.py (GAP-ONBOARD-004 B2b).

Pure mocked-session tests -- verifies the delete-then-insert upsert shape and that every
per_unit_balances line gets written, without a live DB.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.finance_metrics.portal_snapshot_pg_writer import write_pg_portal_snapshot

_SNAPSHOT = {
    "building_id": "13195",
    "snapshot_date": "2026-08-06",
    "financial_year": "2026",
    "source": "strata_web_portal_scrape",
    "raw_admin_fund_balance_cents": 1_340_031,
    "raw_sinking_fund_balance_cents": 14_661_169,
    "raw_arrears_total_cents": 785_130,
    "raw_credit_total_cents": 1_061_453,
    "raw_collection_rate": 92.5,
    "per_unit_balances": [
        {"lot_number": "1", "balance_cents": 76, "status": "ARREARS", "owner_name": "A Owner"},
        {"lot_number": "3", "balance_cents": -133796, "status": "CREDIT", "owner_name": "B Owner"},
    ],
    "is_test_data": False,
}


def _mock_session(*, lot_ids_by_number: dict[str, str] | None = None):
    """``lot_ids_by_number`` simulates get_lot_id_by_number()'s DB lookup (called once per
    per_unit_balances line) -- a lot_number absent from the dict resolves to None, matching a
    real not-yet-onboarded lot."""
    lot_ids_by_number = lot_ids_by_number or {}
    session = SimpleNamespace()
    calls: list[tuple[str, dict]] = []

    async def _execute(clause, params=None):
        sql = str(clause)
        params = params or {}
        calls.append((sql, params))
        if "INSERT INTO finance.portal_balance_snapshots" in sql:
            return SimpleNamespace(scalar=lambda: "snapshot-id-1")
        if "FROM core.lots" in sql:
            resolved = lot_ids_by_number.get(params.get("num"))
            row = (resolved,) if resolved else None
            return SimpleNamespace(fetchone=lambda: row)
        return SimpleNamespace(scalar=lambda: None)

    session.execute = AsyncMock(side_effect=_execute)
    return session, calls


class TestWritePgPortalSnapshot:
    @staticmethod
    def _classify(sql: str) -> str:
        if "DELETE FROM" in sql:
            return "DELETE"
        if "INSERT INTO finance.portal_balance_snapshots\n" in sql:
            return "INSERT_HEADER"
        if "FROM core.lots" in sql:
            return "LOT_LOOKUP"
        if "INSERT INTO finance.portal_balance_snapshot_lines" in sql:
            return "INSERT_LINE"
        raise AssertionError(f"Unclassified query: {sql[:80]}")

    @pytest.mark.asyncio
    async def test_deletes_existing_then_inserts_header_and_lines(self):
        session, calls = _mock_session()
        snapshot_id = await write_pg_portal_snapshot(
            session, _SNAPSHOT, scheme_id="scheme-1", tenant_id="tenant-1",
        )
        assert snapshot_id == "snapshot-id-1"

        kinds = [self._classify(sql) for sql, _ in calls]
        assert kinds[0] == "DELETE"  # delete-before-insert, never additive-only
        assert kinds.count("INSERT_LINE") == 2  # one per per_unit_balances entry
        assert kinds.count("LOT_LOOKUP") == 2  # one resolution attempt per line

    @pytest.mark.asyncio
    async def test_delete_scoped_by_scheme_year_and_snapshot_date(self):
        session, calls = _mock_session()
        await write_pg_portal_snapshot(session, _SNAPSHOT, scheme_id="scheme-1", tenant_id="tenant-1")
        delete_call = calls[0]
        assert delete_call[1]["sid"] == "scheme-1"
        assert delete_call[1]["fy"] == "2026"
        assert delete_call[1]["snapshot_date"] == "2026-08-06"

    @pytest.mark.asyncio
    async def test_header_amounts_pass_through_as_cents(self):
        session, calls = _mock_session()
        await write_pg_portal_snapshot(session, _SNAPSHOT, scheme_id="scheme-1", tenant_id="tenant-1")
        header_call = next(p for sql, p in calls if "INSERT INTO finance.portal_balance_snapshots\n" in sql)
        assert header_call["admin_cents"] == 1_340_031
        assert header_call["sinking_cents"] == 14_661_169
        assert header_call["source"] == "strata_web_portal_scrape"

    @pytest.mark.asyncio
    async def test_no_lines_when_per_unit_balances_empty(self):
        session, calls = _mock_session()
        empty_snapshot = {**_SNAPSHOT, "per_unit_balances": []}
        await write_pg_portal_snapshot(session, empty_snapshot, scheme_id="scheme-1", tenant_id="tenant-1")
        line_calls = [c for sql, c in calls if "INSERT INTO finance.portal_balance_snapshot_lines" in sql]
        assert line_calls == []


class TestLotIdResolution:
    """Self-audit fix (2026-08-10): lot_id used to be hardcoded NULL for every line. Now
    resolved via db_postgres.repos.ownership_repo.get_lot_id_by_number() -- the existing,
    canonical lot_number-or-unit_number lookup -- avoiding the exact "match the wrong column"
    incident already documented in portal_ledger_reconciliation_service.py's own docstring."""

    @pytest.mark.asyncio
    async def test_resolves_lot_id_when_lot_exists(self):
        session, calls = _mock_session(lot_ids_by_number={"1": "lot-uuid-1", "3": "lot-uuid-3"})
        await write_pg_portal_snapshot(session, _SNAPSHOT, scheme_id="scheme-1", tenant_id="tenant-1")

        line_calls = [p for sql, p in calls if "INSERT INTO finance.portal_balance_snapshot_lines" in sql]
        by_lot_number = {c["lot_number"]: c["lot_id"] for c in line_calls}
        assert by_lot_number == {"1": "lot-uuid-1", "3": "lot-uuid-3"}

    @pytest.mark.asyncio
    async def test_unresolvable_lot_gets_null_lot_id_not_an_error(self):
        """A lot_number the building hasn't been CSV-onboarded with yet -- missing, not a bug."""
        session, calls = _mock_session(lot_ids_by_number={})
        await write_pg_portal_snapshot(session, _SNAPSHOT, scheme_id="scheme-1", tenant_id="tenant-1")

        line_calls = [p for sql, p in calls if "INSERT INTO finance.portal_balance_snapshot_lines" in sql]
        assert all(c["lot_id"] is None for c in line_calls)
        assert len(line_calls) == 2  # both lines still inserted despite unresolved lot_id

    @pytest.mark.asyncio
    async def test_lot_lookup_scoped_to_the_right_scheme(self):
        session, calls = _mock_session(lot_ids_by_number={"1": "lot-uuid-1"})
        await write_pg_portal_snapshot(session, _SNAPSHOT, scheme_id="scheme-42", tenant_id="tenant-1")

        lookup_calls = [p for sql, p in calls if "FROM core.lots" in sql]
        assert all(c["sid"] == "scheme-42" for c in lookup_calls)

    def test_never_imports_financial_core(self):
        """Reconciliation evidence, never a journal source -- structural check that this
        module never imports financial_core (see module docstring)."""
        import ast
        import inspect
        import services.finance_metrics.portal_snapshot_pg_writer as mod

        tree = ast.parse(inspect.getsource(mod))
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        } | {
            node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not any("financial_core" in (m or "") for m in imported_modules)
