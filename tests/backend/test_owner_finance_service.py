#!/usr/bin/env python3
"""
Unit tests for owner_finance_service.get_levy_breakdown.

Covers two eras of the same page:

1. The 2026-07-02 cross-page levy-figure audit: unit_levy_ledger lookup sorted by
   year (string) descending and took the first row, which picks a still-forming
   NEXT-FY ledger row (e.g. "2027") over the actual current year ("2026")
   whenever one exists. Fixed by constraining the ledger query to
   year <= current calendar year. This behavior is unchanged by (2) below and
   still covered here.

2. GAP-FIN-033 Part B1 (2026-08-02): the headline `quarterly_levy` figure used
   to prefer `unit_levy_ledger.period_levy` / `total_levied / 4`. Live-confirmed
   against real East Gate production data that `total_levied` is a YTD-to-date
   figure, not the annual total — dividing it by 4 under-reports the true
   quarterly amount by roughly half partway through the year. `quarterly_levy`
   is now computed the SAME way routers/finance.py::get_levy_status() computes
   it (Levy Payments page): rate-per-UOE x UOE x building's actual
   levy_collection_frequency, split via compute_period_installment_amounts().
   get_levy_rates() itself is mocked here (its own correctness is covered by
   finance.py's own test suite) — these tests cover get_levy_breakdown's wiring
   of that rate into the final quarterly_levy/quarterly_levy_source contract.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from services import owner_finance_service


def _cursor(rows):
    c = MagicMock()
    c.sort = MagicMock(return_value=c)
    c.to_list = AsyncMock(return_value=rows)
    return c


def _mock_db(*, unit=None, ledger_rows=None, levy_cats=None, total_uoe=10000):
    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value=unit or {
        "entitlement": 161, "lot_entitlement": None, "period_levy": None,
    })
    mock_db.units.aggregate.return_value = _cursor([{"total_uoe": total_uoe}])
    mock_db.levy_categories.find.return_value = _cursor(levy_cats or [])
    mock_db.levy_categories.find_one = AsyncMock(return_value=None)
    mock_db.unit_levy_ledger.find.return_value = _cursor(ledger_rows or [])
    # levy_collection_frequency lookup used by the rate-based tier (GAP-FIN-033 B1).
    mock_db.settings.find_one = AsyncMock(return_value={"levy_collection_frequency": "quarterly"})
    return mock_db


# Simple, hand-verifiable per-UOE rates: at uoe=161 (the mock unit's default
# entitlement), admin_annual + sinking_annual = $30.00/UOE x 161 = $4,830.00
# total annual — quarterly = $1,207.50, half-yearly = $2,415.00.
_TH017_RATES = {"admin_annual": 20.00, "sinking_annual": 10.00}
_TH017_ANNUAL = 4830.00


@pytest.mark.asyncio
async def test_ledger_lookup_ignores_future_year_row():
    """
    Regression test for the TH087 production bug: a "2027" unit_levy_ledger row
    must not be selected over the current year "2026" row just because it sorts
    higher as a string. ledger_year (used for balance/net figures) must reflect
    the real current-year row; quarterly_levy is independently rate-based
    (GAP-FIN-033 B1) and unaffected by which ledger row is selected.
    """
    ledger_2026 = {
        "unit_number": "TH017", "building_id": "13195", "year": "2026",
        "total_levied": 1772.51, "net_balance": 0.0,
    }
    ledger_2027 = {
        "unit_number": "TH017", "building_id": "13195", "year": "2027",
        "total_levied": 3087.38, "net_balance": -254.98,
    }

    mock_db = _mock_db(ledger_rows=[ledger_2026])  # query filters out 2027 itself
    with (
        patch("services.owner_finance_service.db", mock_db),
        patch("services.owner_finance_service.datetime") as mock_dt,
        patch("services.owner_finance_service.get_levy_rates", new=AsyncMock(return_value=_TH017_RATES)),
    ):
        mock_dt.now.return_value.year = 2026
        result = await owner_finance_service.get_levy_breakdown("TH017", "13195")

    assert result["quarterly_levy"] == 1207.50
    assert result["quarterly_levy_source"] == "rate_schedule"
    assert result["ledger_year"] == "2026"

    # Confirm the query itself constrains year <= current year (the actual fix),
    # not just that our fixture happened to omit the 2027 row.
    call_args = mock_db.unit_levy_ledger.find.call_args[0][0]
    assert call_args.get("year") == {"$lte": "2026"}, (
        f"unit_levy_ledger query must constrain year <= current year, got: {call_args}"
    )


@pytest.mark.asyncio
async def test_quarterly_levy_matches_canonical_annual_rate_divided_by_four():
    """
    For a quarterly-frequency building, quarterly_levy × 4 must equal the
    canonical annual strata levy (rate-per-UOE × UOE) for the same unit/year.
    """
    mock_db = _mock_db()
    with (
        patch("services.owner_finance_service.db", mock_db),
        patch("services.owner_finance_service.datetime") as mock_dt,
        patch("services.owner_finance_service.get_levy_rates", new=AsyncMock(return_value=_TH017_RATES)),
    ):
        mock_dt.now.return_value.year = 2026
        result = await owner_finance_service.get_levy_breakdown("TH017", "13195")

    assert round(result["quarterly_levy"] * 4, 2) == _TH017_ANNUAL


@pytest.mark.asyncio
async def test_quarterly_levy_uses_building_frequency_not_hardcoded_four():
    """
    A half-yearly building must split the annual rate into 2 periods, not 4 —
    this is the exact class of bug (hardcoded /4 regardless of the building's
    actual levy_collection_frequency) live-confirmed as the root cause
    candidate for the "Quarterly Levy shows half" production report.
    """
    mock_db = _mock_db()
    mock_db.settings.find_one = AsyncMock(return_value={"levy_collection_frequency": "half_yearly"})
    with (
        patch("services.owner_finance_service.db", mock_db),
        patch("services.owner_finance_service.datetime") as mock_dt,
        patch("services.owner_finance_service.get_levy_rates", new=AsyncMock(return_value=_TH017_RATES)),
    ):
        mock_dt.now.return_value.year = 2026
        result = await owner_finance_service.get_levy_breakdown("TH017", "13195")

    assert round(result["quarterly_levy"] * 2, 2) == _TH017_ANNUAL


@pytest.mark.asyncio
async def test_no_current_year_row_falls_back_to_most_recent_past_year():
    """When no current-year ledger row exists yet, fall back to the most recent
    PAST year for ledger_year/balance fields — never a future one. quarterly_levy
    itself is rate-based (GAP-FIN-033 B1) and always reflects the current year's
    rates regardless of which ledger year was found."""
    ledger_2025 = {
        "unit_number": "TH017", "building_id": "13195", "year": "2025",
        "total_levied": 6400.0, "net_balance": 0.0,
    }
    mock_db = _mock_db(ledger_rows=[ledger_2025])
    with (
        patch("services.owner_finance_service.db", mock_db),
        patch("services.owner_finance_service.datetime") as mock_dt,
        patch("services.owner_finance_service.get_levy_rates", new=AsyncMock(return_value=_TH017_RATES)),
    ):
        mock_dt.now.return_value.year = 2026
        result = await owner_finance_service.get_levy_breakdown("TH017", "13195")

    assert result["ledger_year"] == "2025"
    assert result["quarterly_levy"] == 1207.50


@pytest.mark.asyncio
async def test_malformed_rate_lookup_falls_back_to_unit_master_period_levy():
    """When rate data is present but malformed, fall back gracefully to
    units.period_levy rather than crashing the owner dashboard."""
    unit = {"entitlement": 161, "lot_entitlement": None, "period_levy": 999.99}
    mock_db = _mock_db(unit=unit)
    with (
        patch("services.owner_finance_service.db", mock_db),
        patch("services.owner_finance_service.datetime") as mock_dt,
        patch(
            "services.owner_finance_service.get_levy_rates",
            new=AsyncMock(return_value={"admin_annual": "not-a-number", "sinking_annual": 10.0}),
        ),
    ):
        mock_dt.now.return_value.year = 2026
        result = await owner_finance_service.get_levy_breakdown("TH017", "13195")

    assert result["quarterly_levy"] == 999.99
    assert result["quarterly_levy_source"] == "unit_master"


@pytest.mark.asyncio
async def test_rate_lookup_database_failure_is_not_swallowed():
    """Unexpected infrastructure failures must surface instead of silently
    downgrading to a less authoritative figure."""
    unit = {"entitlement": 161, "lot_entitlement": None, "period_levy": 999.99}
    mock_db = _mock_db(unit=unit)
    with (
        patch("services.owner_finance_service.db", mock_db),
        patch("services.owner_finance_service.datetime") as mock_dt,
        patch(
            "services.owner_finance_service.get_levy_rates",
            new=AsyncMock(side_effect=RuntimeError("database unavailable")),
        ),
    ):
        mock_dt.now.return_value.year = 2026
        with pytest.raises(RuntimeError, match="database unavailable"):
            await owner_finance_service.get_levy_breakdown("TH017", "13195")
