"""
Regression test (GAP-DASH-001 P0-6): GET /finance/building-overview's Mongo path
must report `units_in_arrears` as the grace-aware canonical count from
get_arrears_metrics() (via _compute_grace_aware_arrears), NOT the raw
`net_balance > 0` tally.

The raw tally over-counts units that are still inside their instalment grace
window, producing the "31 in arrears vs 14 True Arrears" unit-count/dollar-total
mismatch class that CLAUDE.md Rule 10 forbids: the count and the dollar total on
the same screen must share one basis. /finance/summary, /arrears/detail and
/stats/building-kpis already use the grace-aware count; this pins building-overview
to the same basis.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _agg_cursor(rows):
    cur = MagicMock()
    cur.to_list = AsyncMock(return_value=rows)
    return cur


@pytest.mark.asyncio
async def test_building_overview_units_in_arrears_is_grace_aware():
    from routers import finance as finance_mod

    # Raw net_balance>0 tally would be 31; grace-aware canonical count is 14.
    RAW_COUNT = 31
    GRACE_AWARE_COUNT = 14

    mock_db = MagicMock()
    # 1st aggregate = fund-total pipeline; 2nd aggregate = raw units-in-arrears count.
    main_agg = {
        "admin_levied": 0.0, "admin_paid": 0.0,
        "sinking_levied": 0.0, "sinking_paid": 0.0,
        "total_levied": 1000.0, "total_paid": 500.0,
        "total_opening_arrears": 0.0, "total_outstanding": 500.0,
        "net_balance_signed_sum": 0.0, "admin_closing_sum": 0.0, "sinking_closing_sum": 0.0,
    }
    mock_db.unit_levy_ledger.aggregate = MagicMock(
        side_effect=[_agg_cursor([main_agg]), _agg_cursor([{"count": RAW_COUNT}])]
    )
    trend_cursor = MagicMock()
    trend_cursor.sort = MagicMock(return_value=trend_cursor)
    trend_cursor.limit = MagicMock(return_value=trend_cursor)
    trend_cursor.to_list = AsyncMock(return_value=[])
    mock_db.annual_levies.find = MagicMock(return_value=trend_cursor)
    mock_db.annual_levies.find_one = AsyncMock(return_value=None)

    crm = {
        "admin_fund": {"collected_to_date": 0.0, "collection_rate_pct": 0.0},
        "sinking_fund": {"collected_to_date": 0.0, "collection_rate_pct": 0.0},
        "collected_to_date": 0.0,
        # Real get_collection_rate_metrics() (finance_helpers.py:1181-1185) always
        # includes these top-level keys and the route reads them unconditionally.
        # collection_rate_pct joined the list on 2026-08-27, when building-overview began
        # exposing metric 1 (the due-date rate) alongside metrics 2 and 3 — the mock
        # omitting it is what surfaced this.
        "collected_in_advance": 0.0,
        "due_to_date": 1000.0,
        "collection_rate_pct": 0.0,
    }

    grace_mock = AsyncMock(return_value={
        "units_owing": GRACE_AWARE_COUNT,
        "total_outstanding": 500.0,
        "true_arrears_amount": 500.0,
        "in_grace_amount": 0.0,
        "total_credit_amount": 0.0,
        "credit_unit_count": 0,
    })

    with patch.object(finance_mod, "db", mock_db), \
         patch.object(finance_mod, "get_collection_rate_metrics", AsyncMock(return_value=crm)), \
         patch.object(finance_mod, "_resolve_current_levy_year", AsyncMock(return_value=2026)), \
         patch.object(finance_mod, "_get_general_settings", AsyncMock(return_value={})), \
         patch.object(finance_mod, "_compute_grace_aware_arrears", grace_mock), \
         patch.object(finance_mod._financial_read_service, "get_consolidated_fund_balances",
                      AsyncMock(return_value=None)):
        result = await finance_mod._get_building_overview_mongo_fallback("13195", "2026")

    # The surfaced count is the grace-aware one, never the raw tally.
    assert result["units_in_arrears"] == GRACE_AWARE_COUNT
    assert result["units_in_arrears"] != RAW_COUNT

    # And it was derived through the canonical helper, building-scoped, seeded with
    # the raw count as the fallback basis.
    grace_mock.assert_awaited_once()
    kwargs = grace_mock.await_args.kwargs
    assert kwargs["building_id"] == "13195"
    assert kwargs["raw_units_owing"] == RAW_COUNT


@pytest.mark.asyncio
async def test_building_overview_units_in_arrears_falls_back_on_grace_error():
    """If grace resolution fails, the card degrades to the raw count rather than erroring."""
    from routers import finance as finance_mod

    RAW_COUNT = 9
    mock_db = MagicMock()
    main_agg = {
        "admin_levied": 0.0, "admin_paid": 0.0, "sinking_levied": 0.0, "sinking_paid": 0.0,
        "total_levied": 1000.0, "total_paid": 500.0,
        "total_opening_arrears": 0.0, "total_outstanding": 500.0,
        "net_balance_signed_sum": 0.0, "admin_closing_sum": 0.0, "sinking_closing_sum": 0.0,
    }
    mock_db.unit_levy_ledger.aggregate = MagicMock(
        side_effect=[_agg_cursor([main_agg]), _agg_cursor([{"count": RAW_COUNT}])]
    )
    trend_cursor = MagicMock()
    trend_cursor.sort = MagicMock(return_value=trend_cursor)
    trend_cursor.limit = MagicMock(return_value=trend_cursor)
    trend_cursor.to_list = AsyncMock(return_value=[])
    mock_db.annual_levies.find = MagicMock(return_value=trend_cursor)
    mock_db.annual_levies.find_one = AsyncMock(return_value=None)

    crm = {
        "admin_fund": {"collected_to_date": 0.0, "collection_rate_pct": 0.0},
        "sinking_fund": {"collected_to_date": 0.0, "collection_rate_pct": 0.0},
        "collected_to_date": 0.0,
        # Real get_collection_rate_metrics() (finance_helpers.py:1181-1185) always
        # includes these top-level keys and the route reads them unconditionally.
        # collection_rate_pct joined the list on 2026-08-27, when building-overview began
        # exposing metric 1 (the due-date rate) alongside metrics 2 and 3 — the mock
        # omitting it is what surfaced this.
        "collected_in_advance": 0.0,
        "due_to_date": 1000.0,
        "collection_rate_pct": 0.0,
    }

    with patch.object(finance_mod, "db", mock_db), \
         patch.object(finance_mod, "get_collection_rate_metrics", AsyncMock(return_value=crm)), \
         patch.object(finance_mod, "_resolve_current_levy_year", AsyncMock(return_value=2026)), \
         patch.object(finance_mod, "_get_general_settings", AsyncMock(return_value={})), \
         patch.object(finance_mod, "_compute_grace_aware_arrears",
                      AsyncMock(side_effect=RuntimeError("grace boom"))), \
         patch.object(finance_mod._financial_read_service, "get_consolidated_fund_balances",
                      AsyncMock(return_value=None)):
        result = await finance_mod._get_building_overview_mongo_fallback("13195", "2026")

    assert result["units_in_arrears"] == RAW_COUNT
