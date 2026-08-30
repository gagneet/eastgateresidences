#!/usr/bin/env python3
"""
Unit tests for routers/finance.py::get_available_years.

Regression coverage for the 2026-07-02 "Strata/Levy Year vs Financial Year"
audit: GET /years must never return a levy year that hasn't started yet under
the building's OWN levy-year cycle (financial_year_start_month), even when an
annual_levies row already exists for it (e.g. a premature next-year budget
import). See docs/architecture/financial_data_flow_live_map.md for the
full incident write-up.
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from routers import finance as finance_router


def _mock_db(years, fy_start_month):
    mock_db = MagicMock()
    mock_db.annual_levies.distinct = AsyncMock(return_value=years)
    return mock_db


class _FrozenDateTime(datetime):
    """datetime subclass with a fixed .now() for deterministic tests."""

    _frozen = datetime(2026, 7, 2, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._frozen if tz is None else cls._frozen.astimezone(tz)


def _freeze(dt: datetime):
    _FrozenDateTime._frozen = dt
    return _FrozenDateTime


@pytest.mark.asyncio
async def test_future_levy_year_excluded_calendar_building(pin_store):
    """
    Building with financial_year_start_month=1 (calendar-year levy cycle, e.g.
    East Gate): "today" is 2026-07-02, current levy year is 2026. A "2027" row
    (premature next-year import) must not appear in the response.
    """
    mock_db = _mock_db(["2027", "2026", "2025", "2024"], fy_start_month=1)
    with (
        # GET /years dispatches through the seam as of 2026-08-30. Unpinned, the handler
        # asks the control plane, gets PostgreSQL, reads the real levy_runs table and
        # ignores mock_db — surfacing as a wrong year list rather than an error. These
        # tests are about the not-yet-started FILTER, which runs in Python for either
        # store, so Mongo is the right pin.
        pin_store("mongo"),
        patch("routers.finance.db", mock_db),
        patch("routers.finance._get_general_settings", new=AsyncMock(
            return_value={"financial_year_start_month": 1}
        )),
        patch("routers.finance.datetime", _freeze(datetime(2026, 7, 2, tzinfo=timezone.utc))),
    ):
        years = await finance_router.get_available_years(current_user={}, building_id="13195")

    assert "2027" not in years
    assert years == ["2026", "2025", "2024"]


@pytest.mark.asyncio
async def test_current_levy_year_included(pin_store):
    """The current levy year itself must still be included, not excluded."""
    mock_db = _mock_db(["2027", "2026", "2025"], fy_start_month=1)
    with (
        # GET /years dispatches through the seam as of 2026-08-30. Unpinned, the handler
        # asks the control plane, gets PostgreSQL, reads the real levy_runs table and
        # ignores mock_db — surfacing as a wrong year list rather than an error. These
        # tests are about the not-yet-started FILTER, which runs in Python for either
        # store, so Mongo is the right pin.
        pin_store("mongo"),
        patch("routers.finance.db", mock_db),
        patch("routers.finance._get_general_settings", new=AsyncMock(
            return_value={"financial_year_start_month": 1}
        )),
        patch("routers.finance.datetime", _freeze(datetime(2026, 7, 2, tzinfo=timezone.utc))),
    ):
        years = await finance_router.get_available_years(current_user={}, building_id="13195")

    assert "2026" in years


@pytest.mark.asyncio
async def test_mid_year_start_month_boundary(pin_store):
    """
    A building configured with financial_year_start_month=7 has a July-start
    levy cycle. On 2026-07-02 (on/after the start month), the current levy year
    is 2026, so a "2027" row must still be excluded. On 2026-03-02 (before the
    start month), the current levy year is 2025, so even a "2026" row must be
    excluded.
    """
    mock_db = _mock_db(["2027", "2026", "2025"], fy_start_month=7)
    with (
        # GET /years dispatches through the seam as of 2026-08-30. Unpinned, the handler
        # asks the control plane, gets PostgreSQL, reads the real levy_runs table and
        # ignores mock_db — surfacing as a wrong year list rather than an error. These
        # tests are about the not-yet-started FILTER, which runs in Python for either
        # store, so Mongo is the right pin.
        pin_store("mongo"),
        patch("routers.finance.db", mock_db),
        patch("routers.finance._get_general_settings", new=AsyncMock(
            return_value={"financial_year_start_month": 7}
        )),
        patch("routers.finance.datetime", _freeze(datetime(2026, 7, 2, tzinfo=timezone.utc))),
    ):
        years_on_start = await finance_router.get_available_years(current_user={}, building_id="13195")
    assert years_on_start == ["2026", "2025"]

    mock_db2 = _mock_db(["2027", "2026", "2025"], fy_start_month=7)
    with (
        # GET /years dispatches through the seam as of 2026-08-30. Unpinned, the handler
        # asks the control plane, gets PostgreSQL, reads the real levy_runs table and
        # ignores mock_db — surfacing as a wrong year list rather than an error. These
        # tests are about the not-yet-started FILTER, which runs in Python for either
        # store, so Mongo is the right pin.
        pin_store("mongo"),
        patch("routers.finance.db", mock_db2),
        patch("routers.finance._get_general_settings", new=AsyncMock(
            return_value={"financial_year_start_month": 7}
        )),
        patch("routers.finance.datetime", _freeze(datetime(2026, 3, 2, tzinfo=timezone.utc))),
    ):
        years_before_start = await finance_router.get_available_years(current_user={}, building_id="13195")
    assert years_before_start == ["2025"]


@pytest.mark.asyncio
async def test_no_settings_doc_defaults_to_calendar_year(pin_store):
    """When no settings document exists, default to financial_year_start_month=1
    (calendar year) rather than raising or including every future year."""
    mock_db = _mock_db(["2027", "2026"], fy_start_month=1)
    with (
        # GET /years dispatches through the seam as of 2026-08-30. Unpinned, the handler
        # asks the control plane, gets PostgreSQL, reads the real levy_runs table and
        # ignores mock_db — surfacing as a wrong year list rather than an error. These
        # tests are about the not-yet-started FILTER, which runs in Python for either
        # store, so Mongo is the right pin.
        pin_store("mongo"),
        patch("routers.finance.db", mock_db),
        patch("routers.finance._get_general_settings", new=AsyncMock(return_value=None)),
        patch("routers.finance.datetime", _freeze(datetime(2026, 7, 2, tzinfo=timezone.utc))),
    ):
        years = await finance_router.get_available_years(current_user={}, building_id="13195")

    assert years == ["2026"]


@pytest.mark.asyncio
async def test_shared_latest_levy_year_excludes_future_rows():
    """Shared helper must follow the same current-levy-year rule as GET /years."""
    from utils import finance_helpers

    mock_db = MagicMock()
    mock_db.settings.find_one = AsyncMock(return_value={"financial_year_start_month": 1})
    mock_db.annual_levies.distinct = AsyncMock(return_value=["2027", "2026", "2025"])

    with (
        patch("utils.finance_helpers.db", mock_db),
        patch("utils.finance_helpers.datetime", _freeze(datetime(2026, 7, 2, tzinfo=timezone.utc))),
    ):
        year = await finance_helpers.get_latest_levy_year("13195")

    assert year == "2026"


@pytest.mark.asyncio
async def test_shared_latest_levy_year_defaults_to_calendar_when_settings_unavailable():
    """Settings lookup failures must not allow a future annual_levies row to win."""
    from utils import finance_helpers

    mock_db = MagicMock()
    mock_db.settings.find_one = AsyncMock(side_effect=RuntimeError("settings unavailable"))
    mock_db.annual_levies.distinct = AsyncMock(return_value=["2027", "2026"])

    with (
        patch("utils.finance_helpers.db", mock_db),
        patch("utils.finance_helpers.datetime", _freeze(datetime(2026, 7, 2, tzinfo=timezone.utc))),
    ):
        year = await finance_helpers.get_latest_levy_year("13195")

    assert year == "2026"


@pytest.mark.asyncio
async def test_upload_budget_actuals_skips_future_levy_year_rows():
    """Budget/actual imports must not recreate not-yet-started levy years."""

    class _Upload:
        filename = "budget.csv"

        async def read(self):
            return (
                b"year,category_id,category_name,planned,actual\n"
                b"2027,101,Admin future,100.00,50.00\n"
            )

    mock_db = MagicMock()
    mock_db.levy_categories.find_one = AsyncMock()
    mock_db.levy_categories.insert_one = AsyncMock()
    mock_db.annual_levies.find_one = AsyncMock()
    mock_db.annual_levies.insert_one = AsyncMock()

    with (
        patch("routers.finance.db", mock_db),
        patch("routers.finance.scan_upload", new=AsyncMock()),
        patch("routers.finance._resolve_current_levy_year", new=AsyncMock(return_value=2026)),
        patch("routers.finance.get_user_permissions", return_value=MagicMock(can_manage_finances=True)),
    ):
        result = await finance_router.upload_budget_actuals(
            file=_Upload(),
            current_user={"id": "manager-1", "role": "strata_manager"},
            building_id="13195",
        )

    assert result["imported"] == 0
    assert result["skipped"] == 1
    assert "has not started" in result["errors"][0]
    mock_db.levy_categories.insert_one.assert_not_called()
    mock_db.annual_levies.insert_one.assert_not_called()
