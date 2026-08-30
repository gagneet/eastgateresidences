"""
tests/backend/test_resolve_levy_year_for_date.py

Regression coverage for a 2026-08-01 finding: financial_matching.py's
_post_payment_to_ledger() resolved the Mongo unit_levy_ledger mirror year via
routers.finance._resolve_default_levy_year(building_id) -- which always
returns the building's CURRENT levy year, ignoring the transaction's own
date entirely. Every historical-dated "allocate" decision was mirrored into
the current year's ledger doc regardless of when it actually happened
(confirmed live: 2,483 real 2021-2025 East Gate transactions were misfiled
into 2026's unit_levy_ledger this way).

resolve_levy_year_for_date() (utils/finance_helpers.py) is the shared, date-
parameterized fix: the same fy_start_month-aware calendar math as
_resolve_current_levy_year, applied to an arbitrary event date instead of
"today". Generic and building-agnostic -- not specific to East Gate or to
the bank-feed matching call site.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from utils.finance_helpers import resolve_levy_year_for_date

BUILDING_A = "13195"


def _mock_db(*, fy_start_month: int, annual_levies_years: list):
    db = MagicMock()
    db.settings.find_one = AsyncMock(
        return_value={"financial_year_start_month": fy_start_month}
    )
    db.annual_levies.distinct = AsyncMock(return_value=annual_levies_years)
    return db


@pytest.mark.asyncio
async def test_january_start_building_uses_calendar_year_of_the_event_date():
    """A January-FY building's 2023-03-11 transaction belongs to levy year
    2023, even though the building's CURRENT levy year (as of "today" in this
    test suite, 2026) is 2026 -- the exact distinction _resolve_default_levy_year
    collapses and the bug this test guards against."""
    db = _mock_db(fy_start_month=1, annual_levies_years=["2021", "2022", "2023", "2024", "2025", "2026"])

    with patch("utils.finance_helpers.db", db):
        year = await resolve_levy_year_for_date(BUILDING_A, date(2023, 3, 11))

    assert year == "2023"


@pytest.mark.asyncio
async def test_non_january_fy_start_rolls_the_event_date_into_the_prior_calendar_year():
    """A July-FY-start building's January transaction belongs to the FY that
    started the PRIOR July, not the calendar year of the date itself."""
    db = _mock_db(fy_start_month=7, annual_levies_years=["2024", "2025"])

    with patch("utils.finance_helpers.db", db):
        year = await resolve_levy_year_for_date(BUILDING_A, date(2026, 1, 15))

    assert year == "2025"  # FY2025 = Jul 2025 - Jun 2026, covers a Jan 2026 date


@pytest.mark.asyncio
async def test_falls_back_when_no_annual_levies_row_at_or_before_the_event_year():
    db = _mock_db(fy_start_month=1, annual_levies_years=["2026"])

    with patch("utils.finance_helpers.db", db):
        year = await resolve_levy_year_for_date(BUILDING_A, date(2019, 6, 1), fallback="2019")

    assert year == "2019"  # no annual_levies row <= 2019 exists; caller's fallback wins


@pytest.mark.asyncio
async def test_never_selects_a_year_later_than_the_events_own_levy_year():
    """A 2023-dated transaction must resolve to 2023, not 2026, even though
    2026 (the building's current levy year, and the newest annual_levies row)
    exists -- this is the precise behaviour _resolve_default_levy_year cannot
    provide, since it has no concept of "the event's own year" at all."""
    db = _mock_db(fy_start_month=1, annual_levies_years=["2023", "2024", "2025", "2026"])

    with patch("utils.finance_helpers.db", db):
        year = await resolve_levy_year_for_date(BUILDING_A, date(2023, 6, 1))

    assert year == "2023"
    assert year != "2026"
