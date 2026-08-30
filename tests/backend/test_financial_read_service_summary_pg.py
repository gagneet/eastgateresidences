# @featuretrace:finance-postgres-read-cutover — unit tests for the two new
# FinancialReadService methods backing GET /finance/summary's Postgres override
# (get_fund_expense_totals, get_canonical_ledger_quality).
# Layer: test
# Related: backend/services/financial_read_service.py
"""Regression tests for get_fund_expense_totals / get_canonical_ledger_quality.

Mocking pattern mirrors test_financial_read_service_oc_levy_summary.py's documented
gotcha: financial_read_service.py does `from db_postgres.session import
async_session_context, set_tenant` (a direct name import), so the correct patch target
is the CONSUMING module ("services.financial_read_service.async_session_context"), not
"db_postgres.session.async_session_context".
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


class _FakeResult:
    def __init__(self, rows=None, scalar_value=None):
        self._rows = rows or []
        self._scalar_value = scalar_value

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._scalar_value


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.executed_sql: list[str] = []

    async def execute(self, statement, params=None):
        self.executed_sql.append(str(statement))
        if not self._results:
            return _FakeResult()
        return self._results.pop(0)


def _session_context(session):
    @asynccontextmanager
    async def _ctx():
        yield session
    return _ctx


def _scheme():
    return {
        "scheme_id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
    }


def _window():
    from services.financial_read_service import FinancialYearWindow
    return FinancialYearWindow(
        financial_year="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31),
    )


@pytest.mark.asyncio
async def test_fund_expense_totals_splits_by_fund_type():
    from services.financial_read_service import FinancialReadService

    rows = _FakeResult(rows=[
        SimpleNamespace(fund_type="admin", expense_cents=250000),
        SimpleNamespace(fund_type="sinking", expense_cents=75000),
    ])
    session = _FakeSession([rows])
    svc = FinancialReadService()

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("services.financial_read_service.async_session_context", _session_context(session)),
        patch("services.financial_read_service.set_tenant", AsyncMock()),
        patch.object(FinancialReadService, "_get_financial_year_window", AsyncMock(return_value=_window())),
    ):
        result = await svc.get_fund_expense_totals(building_id="13195", financial_year="2026")

    assert result["admin_expense_cents"] == 250000
    assert result["sinking_expense_cents"] == 75000
    assert result["unassigned_expense_cents"] == 0
    assert result["total_expense_cents"] == 325000
    sql = session.executed_sql[0]
    assert "finance.expense_transactions" in sql
    assert "COALESCE(et.fund_id, je.fund_id, ga.fund_id)" in sql


@pytest.mark.asyncio
async def test_fund_expense_totals_folds_capital_works_into_sinking():
    from services.financial_read_service import FinancialReadService

    rows = _FakeResult(rows=[
        SimpleNamespace(fund_type="sinking", expense_cents=50000),
        SimpleNamespace(fund_type="capital_works", expense_cents=20000),
    ])
    session = _FakeSession([rows])
    svc = FinancialReadService()

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("services.financial_read_service.async_session_context", _session_context(session)),
        patch("services.financial_read_service.set_tenant", AsyncMock()),
        patch.object(FinancialReadService, "_get_financial_year_window", AsyncMock(return_value=_window())),
    ):
        result = await svc.get_fund_expense_totals(building_id="13195", financial_year="2026")

    assert result["sinking_expense_cents"] == 70000


@pytest.mark.asyncio
async def test_fund_expense_totals_reports_unassigned_bucket_separately():
    """A NULL fund_type (expense GL account with no fund_id) must be its own
    bucket, never silently folded into admin/sinking."""
    from services.financial_read_service import FinancialReadService

    rows = _FakeResult(rows=[
        SimpleNamespace(fund_type="admin", expense_cents=100000),
        SimpleNamespace(fund_type=None, expense_cents=5000),
    ])
    session = _FakeSession([rows])
    svc = FinancialReadService()

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("services.financial_read_service.async_session_context", _session_context(session)),
        patch("services.financial_read_service.set_tenant", AsyncMock()),
        patch.object(FinancialReadService, "_get_financial_year_window", AsyncMock(return_value=_window())),
    ):
        result = await svc.get_fund_expense_totals(building_id="13195", financial_year="2026")

    assert result["admin_expense_cents"] == 100000
    assert result["unassigned_expense_cents"] == 5000
    assert result["total_expense_cents"] == 105000


@pytest.mark.asyncio
async def test_fund_expense_totals_zeroed_when_no_financial_year_window():
    from services.financial_read_service import FinancialReadService

    svc = FinancialReadService()
    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch.object(FinancialReadService, "_get_financial_year_window", AsyncMock(return_value=None)),
    ):
        result = await svc.get_fund_expense_totals(building_id="13195", financial_year="2099")

    assert result == {
        "admin_expense_cents": 0, "sinking_expense_cents": 0,
        "unassigned_expense_cents": 0, "total_expense_cents": 0, "financial_year": "2099",
    }


@pytest.mark.asyncio
async def test_canonical_ledger_quality_classifies_owing_credit_paid_up():
    from services.financial_read_service import FinancialReadService

    rows = _FakeResult(rows=[
        SimpleNamespace(lot_id="lot-1", unit_number="UA101", net_cents=50000, item_count=4),
        SimpleNamespace(lot_id="lot-2", unit_number="UA102", net_cents=-2000, item_count=4),
        SimpleNamespace(lot_id="lot-3", unit_number="UA103", net_cents=0, item_count=4),
    ])
    session = _FakeSession([rows])
    svc = FinancialReadService()

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("services.financial_read_service.async_session_context", _session_context(session)),
        patch("services.financial_read_service.set_tenant", AsyncMock()),
    ):
        result = await svc.get_canonical_ledger_quality(building_id="13195", financial_year="2026")

    assert result["canonical_unit_count"] == 3
    assert result["canonical_status_counts"] == {"paid_up": 1, "owing": 1, "credit": 1}
    assert result["missing_ledger_units"] == []
    assert result["is_unit_count_consistent"] is True
    # Structurally impossible under the lot_id FK, not unimplemented -- see docstring.
    assert result["duplicate_ledger_units"] == []
    assert result["extra_ledger_units"] == []
    assert result["malformed_ledger_row_count"] == 0


@pytest.mark.asyncio
async def test_canonical_ledger_quality_reports_lots_with_no_levy_items_as_missing():
    """A lot with item_count=0 (no finance.levy_items row this year, e.g. a newly
    added lot) is reported as missing, never silently counted as paid-up."""
    from services.financial_read_service import FinancialReadService

    rows = _FakeResult(rows=[
        SimpleNamespace(lot_id="lot-1", unit_number="UA101", net_cents=0, item_count=4),
        SimpleNamespace(lot_id="lot-2", unit_number="UA999", net_cents=0, item_count=0),
    ])
    session = _FakeSession([rows])
    svc = FinancialReadService()

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("services.financial_read_service.async_session_context", _session_context(session)),
        patch("services.financial_read_service.set_tenant", AsyncMock()),
    ):
        result = await svc.get_canonical_ledger_quality(building_id="13195", financial_year="2026")

    assert result["canonical_unit_count"] == 2
    assert result["missing_ledger_units"] == ["UA999"]
    assert result["is_unit_count_consistent"] is False
    # The matched lot is still classified normally -- only the missing one is excluded.
    assert result["canonical_status_counts"] == {"paid_up": 1, "owing": 0, "credit": 0}
