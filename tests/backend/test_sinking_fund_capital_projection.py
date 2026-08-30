# @featuretrace:finance-reserve-forecast — Guards the capital-schedule projection.
# Layer: test
# Data flow: capital_replacement_schedule + annual_levies -> get_capital_replacement_projection
#            (building-scoped).
# Related: backend/services/forecast_service.py, backend/routers/analytics.py
"""
Tests for the sinking fund projection sourced from `capital_replacement_schedule`.

Covers the defect these were written for: East Gate has no `sinking_fund_plan` (the
documented primary source) but does hold 18 real assets in `capital_replacement_schedule`.
The endpoint previously ignored that schedule entirely and returned a flat CPI line with
`contributions: 0.0` / `expenses: 0.0` for every year — figures the dashboard then rendered
as a confident "$0.00".

The invariants under test are the missing-vs-zero rule and the "never fabricate a
building-specific figure" rule from CLAUDE.md:

  * a year with no asset due is a REAL 0.0 (the schedule exists and lists nothing)
  * an absent approved levy is None, never 0.0
  * every emitted figure traces to `estimated_cost` or `proposed_amount_cents`
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.forecast_service import get_capital_replacement_projection


def _mock_db(assets, levy_doc):
    """Build a Motor-shaped mock. find() returns a cursor whose to_list is awaitable."""
    db = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=assets)
    db.capital_replacement_schedule.find = MagicMock(return_value=cursor)
    db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
    return db


def _asset(name, year, cost, building_id="13195"):
    return {
        "building_id": building_id,
        "asset_id": name.lower().replace(" ", "-"),
        "asset_name": name,
        "replacement_year": year,
        "estimated_cost": cost,
    }


_LEVY = {"year": 2026, "sinking_fund": {"proposed_amount_cents": 9045900}}


@pytest.mark.asyncio
async def test_returns_none_when_no_schedule():
    """No schedule at all -> caller must be free to fall through to its own branch."""
    with patch("services.forecast_service.db", _mock_db([], _LEVY)):
        assert await get_capital_replacement_projection("13195", 2026, 10, 100.0) is None


@pytest.mark.asyncio
async def test_expenses_summed_into_replacement_year():
    """Two assets due the same year sum; the events list names both, dearest first."""
    assets = [_asset("Garage Door Motor A", 2027, 4635.0), _asset("Fire Alarm Panel", 2027, 41200.0)]
    with patch("services.forecast_service.db", _mock_db(assets, _LEVY)):
        r = await get_capital_replacement_projection("13195", 2026, 3, 166969.06)

    y2027 = r["projection"][0]
    assert y2027["year"] == 2027
    assert y2027["expenses"] == 45835.00
    assert [e["item"] for e in y2027["events"]] == ["Fire Alarm Panel", "Garage Door Motor A"]


@pytest.mark.asyncio
async def test_year_with_no_asset_is_a_real_zero_not_none():
    """The schedule exists and lists nothing for 2028 -> that is a known $0, not missing."""
    with patch("services.forecast_service.db", _mock_db([_asset("Lift", 2027, 1000.0)], _LEVY)):
        r = await get_capital_replacement_projection("13195", 2026, 3, 0.0)

    assert r["projection"][1]["year"] == 2028
    assert r["projection"][1]["expenses"] == 0.0     # real zero
    assert r["projection"][1]["expenses"] is not None
    assert r["projection"][1]["events"] == []


@pytest.mark.asyncio
async def test_contribution_converted_from_cents_once():
    """proposed_amount_cents is genuine integer cents on this document; 9045900 -> 90459.00."""
    with patch("services.forecast_service.db", _mock_db([_asset("Lift", 2027, 0.0)], _LEVY)):
        r = await get_capital_replacement_projection("13195", 2026, 2, 0.0)

    assert r["contribution"] == 90459.00
    assert r["contribution_year"] == 2026
    assert all(row["contributions"] == 90459.00 for row in r["projection"])


@pytest.mark.asyncio
async def test_absent_levy_yields_none_contribution_never_zero():
    """No approved levy -> unknown. Emitting 0.0 here is what the UI rendered as $0.00."""
    with patch("services.forecast_service.db", _mock_db([_asset("Lift", 2027, 500.0)], None)):
        r = await get_capital_replacement_projection("13195", 2026, 2, 1000.0)

    assert r["contribution"] is None
    assert all(row["contributions"] is None for row in r["projection"])
    # The unknown contribution must not be silently added into the running balance.
    assert r["projection"][0]["closing_balance"] == 500.00   # 1000 - 500, no phantom credit


@pytest.mark.asyncio
async def test_levy_present_but_no_proposed_amount_is_unknown():
    """A sinking_fund block without proposed_amount_cents is still unknown, not zero."""
    levy = {"year": 2026, "sinking_fund": {"closing_balance": 166969.06}}
    with patch("services.forecast_service.db", _mock_db([_asset("Lift", 2027, 0.0)], levy)):
        r = await get_capital_replacement_projection("13195", 2026, 2, 0.0)
    assert r["contribution"] is None


@pytest.mark.asyncio
async def test_running_balance_compounds_across_years():
    """Each year opens on the prior year's close — the drawdown must carry forward."""
    assets = [_asset("Repaint", 2029, 456317.33)]
    with patch("services.forecast_service.db", _mock_db(assets, _LEVY)):
        r = await get_capital_replacement_projection("13195", 2026, 4, 166969.06)

    rows = {row["year"]: row for row in r["projection"]}
    assert rows[2027]["closing_balance"] == 257428.06        # 166969.06 + 90459
    assert rows[2028]["closing_balance"] == 347887.06
    assert rows[2029]["opening_balance"] == 347887.06
    # Goes negative: the fund cannot cover its own scheduled works.
    assert rows[2029]["closing_balance"] == pytest.approx(-17971.27, abs=0.01)


@pytest.mark.asyncio
async def test_unusable_replacement_year_is_skipped_not_fatal():
    """A malformed year must not crash the projection or land in an arbitrary bucket."""
    assets = [_asset("Good", 2027, 100.0), _asset("Bad", None, 999.0), _asset("Worse", "n/a", 888.0)]
    with patch("services.forecast_service.db", _mock_db(assets, _LEVY)):
        r = await get_capital_replacement_projection("13195", 2026, 2, 0.0)

    assert r["assets_total"] == 3        # all three counted as on record
    assert r["assets_scheduled"] == 1    # only one could be placed on the timeline
    assert sum(row["expenses"] for row in r["projection"]) == 100.0


@pytest.mark.asyncio
async def test_missing_estimated_cost_treated_as_zero_cost_asset():
    """A scheduled asset with no costing still belongs on the timeline as an event."""
    with patch("services.forecast_service.db", _mock_db([_asset("Uncosted", 2027, None)], _LEVY)):
        r = await get_capital_replacement_projection("13195", 2026, 2, 0.0)

    assert r["projection"][0]["expenses"] == 0.0
    assert r["projection"][0]["events"] == [{"item": "Uncosted", "cost": 0.0}]


@pytest.mark.asyncio
async def test_query_is_building_scoped():
    """The find() filter must carry building_id — this collection is tenant-scoped."""
    db = _mock_db([_asset("Lift", 2027, 1.0)], _LEVY)
    with patch("services.forecast_service.db", db):
        await get_capital_replacement_projection("16244", 2026, 2, 0.0)

    assert db.capital_replacement_schedule.find.call_args[0][0]["building_id"] == "16244"
    assert db.annual_levies.find_one.call_args[0][0]["building_id"] == "16244"


@pytest.mark.asyncio
async def test_assets_outside_horizon_are_excluded_from_projection():
    """A 2040 asset must not be folded into the last in-horizon year."""
    assets = [_asset("Near", 2027, 100.0), _asset("Far", 2040, 500000.0)]
    with patch("services.forecast_service.db", _mock_db(assets, _LEVY)):
        r = await get_capital_replacement_projection("13195", 2026, 3, 0.0)

    assert sum(row["expenses"] for row in r["projection"]) == 100.0
    assert all(row["year"] <= 2029 for row in r["projection"])
