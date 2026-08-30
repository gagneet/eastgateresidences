# @featuretrace:finance-postgres-read-cutover — unit tests for
# get_current_quarter_levy_total, the GAP-FIN-058 follow-up fix for
# finance.levy_kpi's "PG=2.0x Mongo" shadow divergence.
# Layer: test
# Related: backend/services/financial_read_service.py
#          backend/services/finance_shadow_read_service.py
"""Regression tests for get_current_quarter_levy_total().

Root cause this guards against: the OLD PG source (get_oc_levy_summary's
total_budgeted) summed EVERY levy_run raised for the financial year
(YTD-cumulative), while Mongo's quarter_billed_total_display is a flat
single-quarter target rate that never grows. The fix scopes the PG sum to only
the most-recently-issued levy_run's issue_date -- these tests pin that behaviour
so a future change can't silently reintroduce the whole-year sum.

Mocking pattern mirrors test_financial_read_service_oc_levy_summary.py's documented
gotcha: financial_read_service.py does a direct name import of async_session_context/
set_tenant, so the patch target is the CONSUMING module
("services.financial_read_service.X"), not "db_postgres.session.X".
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from datetime import date
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
        self.executed_params: list[dict] = []

    async def execute(self, statement, params=None):
        self.executed_sql.append(str(statement))
        self.executed_params.append(params or {})
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
async def test_sums_only_the_most_recently_issued_levy_run():
    """The real East Gate FY2026 shape: two issue dates (Q1 2026-03-31, Q2
    2026-06-30), each split across two levy_run rows (admin + sinking). Must sum
    ONLY the latest issue_date's rows ($110,093.78), never all four ($220,187.56)."""
    from services.financial_read_service import FinancialReadService

    latest_date = _FakeResult(scalar_value=date(2026, 6, 30))
    # Only the Q2 levy_items (admin + sinking) should be summed: 8521755 + 2487623.
    quarter_total = _FakeResult(scalar_value=11009378)
    session = _FakeSession([latest_date, quarter_total])
    svc = FinancialReadService()

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("services.financial_read_service.async_session_context", _session_context(session)),
        patch("services.financial_read_service.set_tenant", AsyncMock()),
        patch.object(FinancialReadService, "_get_financial_year_window", AsyncMock(return_value=_window())),
    ):
        result = await svc.get_current_quarter_levy_total(building_id="13195", financial_year="2026")

    assert result["quarter_billed_cents"] == 11009378
    assert result["quarter_billed_cents"] * 100 != 22018756 * 100  # sanity: not the whole-year sum
    assert result["issue_date"] == "2026-06-30"

    # Regression guard: the second query must filter on lr.issue_date, not just
    # financial_year -- that filter is the actual fix.
    assert "lr.issue_date = :max_issue_date" in session.executed_sql[1]


@pytest.mark.asyncio
async def test_returns_zero_when_no_levy_runs_exist_for_the_year():
    from services.financial_read_service import FinancialReadService

    no_runs = _FakeResult(scalar_value=None)
    session = _FakeSession([no_runs])
    svc = FinancialReadService()

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("services.financial_read_service.async_session_context", _session_context(session)),
        patch("services.financial_read_service.set_tenant", AsyncMock()),
        patch.object(FinancialReadService, "_get_financial_year_window", AsyncMock(return_value=_window())),
    ):
        result = await svc.get_current_quarter_levy_total(building_id="13195", financial_year="2026")

    assert result["quarter_billed_cents"] == 0
    assert result["issue_date"] is None
    # Only one query should run -- no point querying levy_items with no levy_run to scope to.
    assert len(session.executed_sql) == 1


@pytest.mark.asyncio
async def test_zeroed_when_no_financial_year_window():
    from services.financial_read_service import FinancialReadService

    svc = FinancialReadService()
    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch.object(FinancialReadService, "_get_financial_year_window", AsyncMock(return_value=None)),
    ):
        result = await svc.get_current_quarter_levy_total(building_id="13195", financial_year="2099")

    assert result == {"quarter_billed_cents": 0, "financial_year": "2099", "issue_date": None}
