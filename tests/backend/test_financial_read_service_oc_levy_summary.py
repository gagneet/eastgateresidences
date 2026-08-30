# @featuretrace:financial_integration_v2 — Regression test for get_oc_levy_summary's
# total_collected computation.
# Layer: test
# Related: backend/services/financial_read_service.py
#          docs/fixes/finance_task17_summary_shadow_divergence_2026-07-13.md
"""Regression test for the 2026-07-13 total_collected fix.

get_oc_levy_summary()'s total_collected used to sum finance.receipts within a
date window derived from finance.levy_runs.issue_date/due_date. Every
financial year has exactly one levy_run row with issue_date == due_date (a
nominal placeholder, no per-quarter issuance schedule in this data), so that
window collapsed to a single day and the receipts-by-date query always
summed to 0 (confirmed live for East Gate, 2026-07-13) — this was the
finance.building_overview shadow-diff total_paid=0-vs-mongo divergence.

The fix sums finance.levy_items.paid_cents instead (already fund-allocated
via finance.receipt_allocations, matching what the live building-overview/
unit-dashboard routes use for the same figure), removing the date-window
dependency entirely. This test guards against regressing to the broken
receipts-by-date query.

Mocking note (self-correcting an error caught while writing this test):
financial_read_service.py does `from db_postgres.session import
async_session_context, set_tenant` — a direct name import, which binds a
local reference in that module's namespace. Patching
"db_postgres.session.async_session_context" (the pattern used by
test_dashboard_pg_first.py, whose module-under-test imports the *module*
rather than the name) does NOT affect that local binding — the first version
of this test used that wrong target and silently fell through to the REAL
database (confirmed via a manual debug run: SQLAlchemy raised a live
asyncpg.exceptions.DataError against an actual Postgres connection, using
this test's placeholder scheme_id). The correct target for a direct name
import is the *consuming* module: "services.financial_read_service.
async_session_context". config_repo IS imported as a module reference
(`from db_postgres.repos import config_repo`), so patching
"db_postgres.repos.config_repo.resolve_scheme_context" is correct as-is.
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
    """Supports the Result accessor methods get_oc_levy_summary uses.

    fetchall() rows must support ATTRIBUTE access (row.fund_type, not
    row["fund_type"]) — get_oc_levy_summary never calls .mappings(), so it
    receives plain SQLAlchemy Row objects, which support dot access. Plain
    dicts do NOT support this and would raise AttributeError; use
    SimpleNamespace for row fixtures, not dicts.
    """

    def __init__(self, rows=None, scalar_value=None):
        self._rows = rows or []
        self._scalar_value = scalar_value

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._scalar_value


class _FakeSession:
    """Returns queued results in call order (one per session.execute() call)."""

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
    # Deliberately a single-day window (issue_date == due_date) — the exact
    # real-data shape that broke the old receipts-by-date query. The fix must
    # not depend on this window covering any particular range.
    return FinancialYearWindow(
        financial_year="2026", starts_on=date(2026, 7, 1), ends_on=date(2026, 7, 1),
    )


@pytest.mark.asyncio
async def test_total_collected_sums_levy_items_paid_cents_not_receipts():
    """The fix: total_collected must come from a finance.levy_items query, and
    must NOT depend on finance.receipts at all (the query that structurally
    could never match given this data's single-day FY window)."""
    from services.financial_read_service import FinancialReadService

    budget_rows = _FakeResult(rows=[
        SimpleNamespace(fund_type="admin", budgeted_cents=8521725),
        SimpleNamespace(fund_type="sinking", budgeted_cents=2487649),
    ])
    # This is the fixed query result: SUM(li.paid_cents) for the year.
    paid_cents_total = _FakeResult(scalar_value=10167173)
    outstanding = _FakeResult(scalar_value=139801418)
    periods = _FakeResult(rows=[])

    session = _FakeSession([budget_rows, paid_cents_total, outstanding, periods])
    svc = FinancialReadService()

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("services.financial_read_service.async_session_context", _session_context(session)),
        patch("services.financial_read_service.set_tenant", AsyncMock()),
        patch.object(FinancialReadService, "_get_financial_year_window", AsyncMock(return_value=_window())),
    ):
        result = await svc.get_oc_levy_summary(building_id="13195", financial_year="2026")

    assert result is not None
    # $101,671.73 — matches paid_cents_total exactly (cents -> AUD).
    assert result["total_collected"] == 101671.73

    # Regression guard: exactly 4 queries run, and the SECOND one (the one
    # total_collected reads from) must reference finance.levy_items and must
    # NOT be a finance.receipts query.
    assert len(session.executed_sql) == 4
    collected_sql = session.executed_sql[1]
    assert "finance.levy_items" in collected_sql
    assert "finance.receipts" not in collected_sql


@pytest.mark.asyncio
async def test_total_collected_is_zero_when_no_levy_items_paid():
    """Sanity check: a genuine zero (nothing paid yet) still returns 0 cleanly,
    so this fix doesn't turn a real "nothing paid" state into an error."""
    from services.financial_read_service import FinancialReadService

    budget_rows = _FakeResult(rows=[SimpleNamespace(fund_type="admin", budgeted_cents=100000)])
    paid_cents_total = _FakeResult(scalar_value=0)
    outstanding = _FakeResult(scalar_value=100000)
    periods = _FakeResult(rows=[])

    session = _FakeSession([budget_rows, paid_cents_total, outstanding, periods])
    svc = FinancialReadService()

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("services.financial_read_service.async_session_context", _session_context(session)),
        patch("services.financial_read_service.set_tenant", AsyncMock()),
        patch.object(FinancialReadService, "_get_financial_year_window", AsyncMock(return_value=_window())),
    ):
        result = await svc.get_oc_levy_summary(building_id="13195", financial_year="2026")

    assert result["total_collected"] == 0.0


@pytest.mark.asyncio
async def test_consolidated_fund_balances_read_latest_bi_fact_rows():
    """Dashboard fund balances should come from the consolidated BI fact.

    The fact row's closing_balance_cents is an as-of balance written by ETL,
    not the projected levy-year closing amount.
    """
    from services.financial_read_service import FinancialReadService

    session = _FakeSession([
        _FakeResult(rows=[
            SimpleNamespace(
                fund_type="admin",
                opening_balance_cents=3433227,
                levy_income_cents=7869826,
                other_income_cents=0,
                expenses_cents=14562822,
                closing_balance_cents=918744,
                bank_balance_cents=918744,
                reconciliation_gap_cents=0,
                period_date=date(2026, 7, 14),
                ingested_at=None,
            ),
            SimpleNamespace(
                fund_type="sinking",
                opening_balance_cents=22590400,
                levy_income_cents=2297343,
                other_income_cents=0,
                expenses_cents=7208422,
                closing_balance_cents=19333703,
                bank_balance_cents=19333703,
                reconciliation_gap_cents=0,
                period_date=date(2026, 7, 14),
                ingested_at=None,
            ),
        ])
    ])
    svc = FinancialReadService()

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("services.financial_read_service.async_session_context", _session_context(session)),
        patch("services.financial_read_service.set_tenant", AsyncMock()),
    ):
        result = await svc.get_consolidated_fund_balances(
            building_id="13195",
            financial_year="2026",
            as_of_date=date(2026, 7, 14),
        )

    assert result is not None
    assert result["source"] == "analytics.fact_financial_balance"
    assert result["admin_balance_cents"] == 918744
    assert result["sinking_balance_cents"] == 19333703
    assert result["total_balance_cents"] == 20252447
    assert "analytics.fact_financial_balance" in session.executed_sql[0]
    assert "period_date <= :cutoff" in session.executed_sql[0]
