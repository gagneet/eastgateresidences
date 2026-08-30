"""
Regression test: GET /finance/building-overview's MongoDB aggregation must
exclude is_test_data rows from unit_levy_ledger.

The Postgres path (and other analytics.py aggregations, e.g. get_diff_since,
get_activities) already filter is_test_data; this Mongo aggregation was the
odd one out, meaning seeded/demo data could inflate building-wide arrears and
collection-rate totals for Mongo-backed buildings but not Postgres ones.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.permissions import Permission


@pytest.mark.asyncio
async def test_unit_levy_ledger_aggregation_excludes_test_data():
    from routers.finance import get_building_fund_overview

    mock_db = MagicMock()
    mock_db.annual_levies.distinct = AsyncMock(return_value=["2026"])
    trend_cursor = MagicMock()
    trend_cursor.sort = MagicMock(return_value=trend_cursor)
    # routers/finance.py's actual query chain is
    # find(...).sort("year", -1).limit(8).to_list(8) — .limit() must be
    # mocked to return the same cursor object, or the chain breaks after
    # .limit() returns an unconfigured MagicMock whose .to_list() is not
    # awaitable (TypeError: object MagicMock can't be used in 'await'
    # expression). Missing here previously — a stale-test bug, not a
    # production bug: the production chain requires all four links mocked.
    trend_cursor.limit = MagicMock(return_value=trend_cursor)
    trend_cursor.to_list = AsyncMock(return_value=[])
    mock_db.annual_levies.find = MagicMock(return_value=trend_cursor)
    # Empty trend_docs (mocked above) means selected_levy falls through to
    # this find_one() call — also unmocked previously.
    mock_db.annual_levies.find_one = AsyncMock(return_value=None)

    agg_cursor = MagicMock()
    agg_cursor.to_list = AsyncMock(return_value=[])
    mock_db.unit_levy_ledger.aggregate = MagicMock(return_value=agg_cursor)

    with patch("routers.finance.db", mock_db), \
         patch("routers.finance.get_finance_route_runtime_state",
               AsyncMock(return_value={"source": "mongo"})), \
         patch("routers.finance.get_user_permissions",
               return_value=Permission(can_view_finances=True)):
        await get_building_fund_overview(
            year="2026",
            current_user={"id": "u1", "role": "owner"},
            building_id="13195",
        )

    assert mock_db.unit_levy_ledger.aggregate.call_count == 2
    for call in mock_db.unit_levy_ledger.aggregate.call_args_list:
        pipeline = call.args[0]
        match_stage = pipeline[0]["$match"]
        assert match_stage.get("is_test_data") == {"$ne": True}, (
            f"unit_levy_ledger aggregation missing is_test_data filter: {match_stage}"
        )
