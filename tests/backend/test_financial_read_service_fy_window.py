# @featuretrace:financial_integration_v2 — Regression test for
# _get_financial_year_window's calendar-year-derived boundaries.
# Layer: test
# Related: backend/services/financial_read_service.py
"""Regression test for the 2026-08-01 financial-year-window fix.

_get_financial_year_window() used to derive starts_on/ends_on as
MIN(issue_date)/MAX(due_date) across whatever finance.levy_runs happen to
exist for the year. For East Gate FY2026 (only Q1/Q2 levy_runs generated so
far), that resolved ends_on to 2026-06-30 -- silently excluding any GL
activity dated after that (e.g. a payment posted in August) from
get_unit_levy_balance's closing_balance/arrears query. Confirmed live: a
reconciliation payment journal entry, correctly posted and verified present
via a raw unwindowed AR-account query, was invisible to get_unit_levy_balance
purely because of this truncated window.

The fix derives starts_on/ends_on directly from the financial_year string
instead of from levy_runs' issue_date/due_date, so the window always covers
the whole year regardless of how many quarters have been generated.

2026-08-01 follow-up: the first version of this fix hardcoded Jan 1 - Dec 31
unconditionally, which only looked correct because East Gate's own Levy Year
happens to be calendar-year. A strata building's Levy Year does not have to
start 1 January (`db.settings.financial_year_start_month`, default 7,
already models this per building elsewhere in the codebase). The window is
now derived from the building's actual FY start month, fetched via
`config_repo.get_building_setting`.
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
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeSession:
    def __init__(self, result):
        self._result = result
        self.executed_sql = []
        self.executed_params = []

    async def execute(self, statement, params=None):
        self.executed_sql.append(str(statement))
        self.executed_params.append(params)
        return self._result


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


@pytest.mark.asyncio
async def test_window_spans_full_calendar_year_regardless_of_levy_run_dates():
    """Even though the fake levy_runs row's issue_date/due_date only cover a
    single quarter (mirroring East Gate's real Q1/Q2-only FY2026 data), the
    resolved window must span the whole calendar year."""
    from services.financial_read_service import FinancialReadService

    # Only financial_year is read from the row now — issue_date/due_date are
    # deliberately NOT part of the SELECT anymore, but a row shape with only
    # that column proves the fix doesn't secretly still depend on the others.
    row = SimpleNamespace(financial_year="2026")
    session = _FakeSession(_FakeResult(row=row))
    svc = FinancialReadService()

    with (
        patch.object(FinancialReadService, "_resolve_scheme", AsyncMock(return_value=_scheme())),
        patch("services.financial_read_service.async_session_context", _session_context(session)),
        patch("services.financial_read_service.set_tenant", AsyncMock()),
        patch("services.financial_read_service.config_repo.get_building_setting", AsyncMock(return_value=None)),
    ):
        window = await svc._get_financial_year_window("13195", "2026")

    assert window is not None
    assert window.starts_on == date(2026, 1, 1)
    assert window.ends_on == date(2026, 12, 31)
    # Regression guard: the query must not select issue_date/due_date at all.
    assert "issue_date" not in session.executed_sql[0]
    assert "due_date" not in session.executed_sql[0]


@pytest.mark.asyncio
async def test_window_returns_none_when_no_levy_runs_exist():
    """A genuine miss makes THREE execute() calls (exact match, leading-year
    fallback, then the available-years diagnostic query added by the
    2026-08-01 follow-up) — _FakeSession/_FakeResult only implement
    fetchone() and predate that third call. Needs the _SeqSession/_SeqResult
    doubles (defined further down this file) that support fetchall() too."""
    from services.financial_read_service import FinancialReadService

    session = _SeqSession([_SeqResult([]), _SeqResult([]), _SeqResult([])])
    svc = FinancialReadService()

    with (
        patch.object(FinancialReadService, "_resolve_scheme", AsyncMock(return_value=_scheme())),
        patch("services.financial_read_service.async_session_context", _session_context(session)),
        patch("services.financial_read_service.set_tenant", AsyncMock()),
    ):
        window = await svc._get_financial_year_window("13195", "2026")

    assert window is None


@pytest.mark.asyncio
async def test_window_handles_hyphenated_financial_year_labels():
    """Some buildings label financial years like "2026-27" rather than a bare
    calendar year — the leading year component must still parse cleanly."""
    from services.financial_read_service import FinancialReadService

    row = SimpleNamespace(financial_year="2026-27")
    session = _FakeSession(_FakeResult(row=row))
    svc = FinancialReadService()

    with (
        patch.object(FinancialReadService, "_resolve_scheme", AsyncMock(return_value=_scheme())),
        patch("services.financial_read_service.async_session_context", _session_context(session)),
        patch("services.financial_read_service.set_tenant", AsyncMock()),
        patch("services.financial_read_service.config_repo.get_building_setting", AsyncMock(return_value=None)),
    ):
        window = await svc._get_financial_year_window("13195", "2026-27")

    assert window.starts_on == date(2026, 1, 1)
    assert window.ends_on == date(2026, 12, 31)


@pytest.mark.asyncio
async def test_window_respects_non_january_fy_start_month():
    """A building whose Levy Year starts mid-calendar-year (e.g. July, the AU
    tax-year convention and this repo's own settings default) must NOT get
    the Jan-Dec window -- that was the regression: hardcoding calendar-year
    semantics only looked correct because East Gate's own Levy Year happens
    to be Jan-Dec."""
    from services.financial_read_service import FinancialReadService

    row = SimpleNamespace(financial_year="2026")
    session = _FakeSession(_FakeResult(row=row))
    svc = FinancialReadService()

    with (
        patch.object(FinancialReadService, "_resolve_scheme", AsyncMock(return_value=_scheme())),
        patch("services.financial_read_service.async_session_context", _session_context(session)),
        patch("services.financial_read_service.set_tenant", AsyncMock()),
        patch(
            "services.financial_read_service.config_repo.get_building_setting",
            AsyncMock(return_value={"financial_year_start_month": 7}),
        ),
    ):
        window = await svc._get_financial_year_window("16244", "2026")

    assert window.starts_on == date(2026, 7, 1)
    assert window.ends_on == date(2027, 6, 30)


@pytest.mark.asyncio
async def test_fy_start_month_is_cached_within_ttl():
    """FinancialReadService is held as a module-level singleton in routers/finance.py,
    routers/external_api.py, and finance_shadow_read_service.py, and
    get_building_finance_pg_dashboard() alone fans out to 4 sub-calls that each independently
    re-derive the FY window for the same building+year -- without caching, a single dashboard
    load would hit config_repo.get_building_setting() up to 5x for a value that changes maybe
    once a year. The second call within the TTL window must not re-query."""
    from services.financial_read_service import FinancialReadService

    svc = FinancialReadService()
    get_setting_mock = AsyncMock(return_value={"financial_year_start_month": 7})

    with patch("services.financial_read_service.config_repo.get_building_setting", get_setting_mock):
        first = await svc._get_fy_start_month("13195")
        second = await svc._get_fy_start_month("13195")

    assert first == 7
    assert second == 7
    get_setting_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_fy_start_month_cache_is_per_instance_not_shared():
    """Each module holding its own FinancialReadService() singleton must not accidentally share
    cache state through class-level mutable defaults."""
    from services.financial_read_service import FinancialReadService

    svc_a = FinancialReadService()
    svc_b = FinancialReadService()

    with patch(
        "services.financial_read_service.config_repo.get_building_setting",
        AsyncMock(return_value={"financial_year_start_month": 7}),
    ):
        await svc_a._get_fy_start_month("13195")

    assert svc_a._fy_start_month_cache
    assert svc_b._fy_start_month_cache == {}


# ---------------------------------------------------------------------------
# get_building_finance_pg_dashboard resilience (pg_unavailable root-cause fix)
#
# Previously the 4 sub-calls were gathered with return_exceptions=False, so a
# single throwing sub-call sank the whole payload into an opaque "pg_unavailable"
# with no clue which one failed — the top blocker on building_overview/summary
# read promotion. The fix gathers with return_exceptions=True, logs the failing
# sub-call BY NAME, and still returns None (never a partial/zeroed payload).
# ---------------------------------------------------------------------------

def _window():
    from services.financial_read_service import FinancialYearWindow
    return FinancialYearWindow(
        financial_year="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31),
    )


@pytest.mark.asyncio
async def test_pg_dashboard_builds_payload_when_all_sub_calls_succeed():
    from services.financial_read_service import FinancialReadService

    svc = FinancialReadService()
    with (
        patch.object(FinancialReadService, "_get_financial_year_window",
                     AsyncMock(return_value=_window())),
        patch.object(FinancialReadService, "get_oc_levy_summary",
                     AsyncMock(return_value={"total_budgeted": 100.0, "total_collected": 60.0,
                                             "total_outstanding": 40.0})),
        patch.object(FinancialReadService, "get_arrears_summary",
                     AsyncMock(return_value={"total_arrears_cents": 4000, "units_in_arrears": 3})),
        patch.object(FinancialReadService, "get_receipt_totals",
                     AsyncMock(return_value={"total_receipts_cents": 6000})),
        patch.object(FinancialReadService, "get_fund_balances",
                     AsyncMock(return_value={"admin_balance_cents": 111, "sinking_balance_cents": 222,
                                             "total_balance_cents": 333})),
    ):
        result = await svc.get_building_finance_pg_dashboard(building_id="13195", financial_year="2026")

    assert result is not None
    assert result["source"] == "postgres"
    assert result["levy_budgeted_cents"] == 10000
    assert result["total_arrears_cents"] == 4000
    assert result["units_in_arrears"] == 3
    assert result["total_fund_balance_cents"] == 333


@pytest.mark.asyncio
async def test_pg_dashboard_returns_none_and_names_failing_sub_call():
    """A single throwing sub-call must return None (no partial payload) and log
    the culprit by name — the diagnostic that unblocks the field-divergence fix."""
    from services.financial_read_service import FinancialReadService

    svc = FinancialReadService()
    with (
        patch.object(FinancialReadService, "_get_financial_year_window",
                     AsyncMock(return_value=_window())),
        patch.object(FinancialReadService, "get_oc_levy_summary",
                     AsyncMock(return_value={"total_budgeted": 100.0})),
        patch.object(FinancialReadService, "get_arrears_summary",
                     AsyncMock(side_effect=RuntimeError("boom in arrears query"))),
        patch.object(FinancialReadService, "get_receipt_totals",
                     AsyncMock(return_value={"total_receipts_cents": 6000})),
        patch.object(FinancialReadService, "get_fund_balances",
                     AsyncMock(return_value={"total_balance_cents": 333})),
        patch("services.financial_read_service.logger") as mock_logger,
    ):
        result = await svc.get_building_finance_pg_dashboard(building_id="13195", financial_year="2026")

    assert result is None  # never a partial payload with a zeroed field
    # the failing sub-call is named in a warning so ops/logs can pinpoint the bug
    warned = " ".join(str(c.args) for c in mock_logger.warning.call_args_list)
    assert "get_arrears_summary" in warned


@pytest.mark.asyncio
async def test_pg_dashboard_returns_none_when_no_financial_year_window():
    from services.financial_read_service import FinancialReadService

    svc = FinancialReadService()
    with patch.object(FinancialReadService, "_get_financial_year_window",
                      AsyncMock(return_value=None)):
        result = await svc.get_building_finance_pg_dashboard(building_id="13195", financial_year="2026")

    assert result is None


# ---------------------------------------------------------------------------
# _get_financial_year_window: leading-year fallback + diagnostic logging.
# The silent pg_unavailable root cause: an exact financial_year match that
# missed returned None with no log. Now it retries on the leading calendar year
# (same-year format variance) and, on a genuine miss, logs the available years.
# ---------------------------------------------------------------------------

class _SeqResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _SeqSession:
    """Returns a preset result per execute() call, in order."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    async def execute(self, statement, params=None):
        result = self._results[self.calls] if self.calls < len(self._results) else _SeqResult([])
        self.calls += 1
        return result


@pytest.mark.asyncio
async def test_fy_window_falls_back_to_leading_year_on_format_variance():
    """Requested '2026' but stored '2026-27': the leading-year fallback resolves
    the SAME year instead of returning None (the pg_unavailable root cause)."""
    from services.financial_read_service import FinancialReadService

    session = _SeqSession([
        _SeqResult([]),  # exact financial_year='2026' → miss
        _SeqResult([SimpleNamespace(financial_year="2026-27")]),  # leading-year fallback → hit
    ])
    svc = FinancialReadService()

    with (
        patch.object(FinancialReadService, "_resolve_scheme", AsyncMock(return_value=_scheme())),
        patch("services.financial_read_service.async_session_context", _session_context(session)),
        patch("services.financial_read_service.set_tenant", AsyncMock()),
        patch("services.financial_read_service.config_repo.get_building_setting", AsyncMock(return_value=None)),
        patch("services.financial_read_service.logger") as mock_logger,
    ):
        window = await svc._get_financial_year_window("13195", "2026")

    assert window is not None
    assert window.financial_year == "2026-27"
    assert window.starts_on == date(2026, 1, 1)
    assert window.ends_on == date(2026, 12, 31)
    warned = " ".join(str(c.args) for c in mock_logger.warning.call_args_list)
    assert "leading year" in warned


@pytest.mark.asyncio
async def test_fy_window_logs_available_years_on_genuine_miss():
    """No levy_run for the requested year in any format → None, and the log names
    both the requested value and the years that DO exist (conclusive diagnosis)."""
    from services.financial_read_service import FinancialReadService

    session = _SeqSession([
        _SeqResult([]),  # exact → miss
        _SeqResult([]),  # leading-year fallback → miss
        _SeqResult([SimpleNamespace(financial_year="2024"), SimpleNamespace(financial_year="2025")]),  # available
    ])
    svc = FinancialReadService()

    with (
        patch.object(FinancialReadService, "_resolve_scheme", AsyncMock(return_value=_scheme())),
        patch("services.financial_read_service.async_session_context", _session_context(session)),
        patch("services.financial_read_service.set_tenant", AsyncMock()),
        patch("services.financial_read_service.logger") as mock_logger,
    ):
        window = await svc._get_financial_year_window("13195", "2026")

    assert window is None
    warned = " ".join(str(c.args) for c in mock_logger.warning.call_args_list)
    assert "2024" in warned and "2025" in warned


@pytest.mark.asyncio
async def test_fy_window_exact_match_does_not_trigger_fallback():
    """Happy path unchanged: an exact match never runs the fallback query."""
    from services.financial_read_service import FinancialReadService

    session = _SeqSession([_SeqResult([SimpleNamespace(financial_year="2026")])])
    svc = FinancialReadService()

    with (
        patch.object(FinancialReadService, "_resolve_scheme", AsyncMock(return_value=_scheme())),
        patch("services.financial_read_service.async_session_context", _session_context(session)),
        patch("services.financial_read_service.set_tenant", AsyncMock()),
        patch("services.financial_read_service.config_repo.get_building_setting", AsyncMock(return_value=None)),
    ):
        window = await svc._get_financial_year_window("13195", "2026")

    assert window is not None
    assert window.financial_year == "2026"
    assert session.calls == 1  # exact match only; no fallback/available queries
