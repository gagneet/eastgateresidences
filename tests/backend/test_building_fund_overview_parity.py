"""
GAP-FIN-016 Item C regression guard: _get_building_overview_mongo_fallback's
fund_health/total_obligations/levies_paid_pct now delegate to the canonical
domain formula (current_year_collection_rate()/raw_percentage()) instead of
hand-rolled arithmetic. These tests lock in the exact live-verified East Gate
(13195) values captured before/after the 2026-07-21 refactor — see
tasks/GAP-FIN-016-financial-calculation-consolidation-phase2.md ("Item C").
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BUILDING_ID = "13195"


def _agg(items):
    agg = MagicMock()
    agg.to_list = AsyncMock(return_value=items)
    return agg


def _cursor(items):
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=items)
    return cursor


@pytest.mark.asyncio
async def test_mongo_fallback_reproduces_live_east_gate_2026_value():
    """Live-verified 2026-07-21 against real East Gate unit_levy_ledger data
    (year=2026): total_levied=110093.74, total_opening_arrears=1928.04,
    total_outstanding=15284.25 -> fund_health=86.4, levies_paid_pct=86.1.
    fund_health/levies_paid_pct/total_obligations are unchanged by GAP-FIN-035
    and still reproduced byte-for-byte.

    admin_fund/sinking_fund.collection_rate (GAP-FIN-035, 2026-08-03) now come
    from get_collection_rate_metrics() — a per-unit-clamped, due-date figure —
    instead of the old admin_levied/admin_closing_sum aggregate. That old
    aggregate is exactly the advance-payment-leak formula being fixed, so this
    test mocks get_collection_rate_metrics() directly rather than reproducing
    its per-unit input shape; the 6.2/63.2 values are preserved here only as
    stable, arbitrary mock outputs, not as a claim that the two formulas agree
    on real data."""
    from routers import finance as finance_mod

    # units_in_arrears now routes through _compute_grace_aware_arrears() (CLAUDE.md
    # Rule 10 / GAP-DASH-001 P0-6 — never the raw net_balance>0 tally) — see
    # test_building_overview_arrears_grace.py for the dedicated regression test.
    # Mock it explicitly so this test doesn't depend on whatever live-ish Mongo data
    # happens to exist for 13195/2026 (it previously silently fell through to that,
    # which is how this test kept asserting a stale raw count of 18 while the real
    # code returned the documented-correct grace-aware count, 14).
    grace_mock = AsyncMock(return_value={
        "units_owing": 14,
        "total_outstanding": 15284.25,
        "true_arrears_amount": 15284.25,
        "in_grace_amount": 0.0,
        "total_credit_amount": 0.0,
        "credit_unit_count": 0,
    })

    ledger_agg_result = [{
        "admin_levied": 85217.25, "admin_paid": 5263.68,
        "sinking_levied": 24876.49, "sinking_paid": 15727.66,
        "total_levied": 110093.74, "total_paid": 20991.34,
        "total_opening_arrears": 1928.04, "total_outstanding": 15284.25,
        # Counts of units whose per-fund split is actually recorded. The $group stage
        # emits these because $sum returns 0 for an all-null group, so the summed value
        # alone cannot distinguish "charged nothing" from "no split recorded" — East
        # Gate's 2026 ledger is the second case on all 87 rows. Non-zero here because
        # this fixture DOES carry a split, so the per-fund figures and rates are
        # expected to report real values rather than None.
        "admin_levied_known": 87, "sinking_levied_known": 87,
    }]
    collection_rate_metrics = {
        "collection_rate_pct": 15.0, "due_to_date": 110093.74,
        "collected_to_date": 16500.0, "collected_in_advance": 0.0,
        "admin_fund": {
            "collection_rate_pct": 6.2, "due_to_date": 85217.25,
            "collected_to_date": 5263.68, "collected_in_advance": 0.0,
        },
        "sinking_fund": {
            "collection_rate_pct": 63.2, "due_to_date": 24876.49,
            "collected_to_date": 15727.66, "collected_in_advance": 0.0,
        },
    }

    with (
        patch("routers.finance.db") as mock_db,
        patch("routers.finance._resolve_current_levy_year", new=AsyncMock(return_value=2026)),
        patch(
            "routers.finance._financial_read_service.get_consolidated_fund_balances",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "routers.finance.get_collection_rate_metrics",
            new=AsyncMock(return_value=collection_rate_metrics),
        ),
        patch.object(finance_mod, "_compute_grace_aware_arrears", grace_mock),
    ):
        mock_db.unit_levy_ledger.aggregate.side_effect = [
            _agg(ledger_agg_result),  # main totals pipeline
            _agg([{"count": 18}]),  # raw units_in_arrears pipeline — the fallback
            # basis _compute_grace_aware_arrears is seeded with; the grace-aware
            # mock above (14) is what actually surfaces in units_in_arrears.
        ]
        mock_db.annual_levies.find.return_value = _cursor([])
        mock_db.annual_levies.find_one = AsyncMock(return_value=None)

        result = await finance_mod._get_building_overview_mongo_fallback(BUILDING_ID, "2026")

    assert result["total_obligations"] == 112021.78  # 110093.74 + 1928.04
    assert result["fund_health"] == 86.4
    assert result["levies_paid_pct"] == 86.1
    assert result["units_in_arrears"] == 14  # grace-aware count from the mock above, not the raw 18
    # GAP-FIN-035 (2026-08-03): sourced directly from the mocked
    # get_collection_rate_metrics() result above, not re-derived here.
    assert result["admin_fund"]["collection_rate"] == 6.2
    assert result["sinking_fund"]["collection_rate"] == 63.2


@pytest.mark.asyncio
async def test_mongo_fallback_fund_health_floored_at_zero_when_outstanding_exceeds_obligations():
    """net_collected = max(0, obligations - outstanding) — must not go negative
    (and therefore fund_health must not go negative) even when a building's
    outstanding balance somehow exceeds its total obligations."""
    from routers import finance as finance_mod

    ledger_agg_result = [{
        "admin_levied": 1000.0, "admin_paid": 0.0,
        "sinking_levied": 0.0, "sinking_paid": 0.0,
        "total_levied": 1000.0, "total_paid": 0.0,
        "total_opening_arrears": 0.0, "total_outstanding": 5000.0,
    }]

    with (
        patch("routers.finance.db") as mock_db,
        patch("routers.finance._resolve_current_levy_year", new=AsyncMock(return_value=2026)),
        patch(
            "routers.finance._financial_read_service.get_consolidated_fund_balances",
            new=AsyncMock(return_value=None),
        ),
        # GAP-FIN-035: isolate from a real DB connection — this test does not
        # assert admin_rate/sinking_rate, so the exact values here are unused.
        patch(
            "routers.finance.get_collection_rate_metrics",
            new=AsyncMock(return_value={
                "collection_rate_pct": 0.0, "due_to_date": 1000.0,
                "collected_to_date": 0.0, "collected_in_advance": 0.0,
                "admin_fund": {"collection_rate_pct": 0.0, "due_to_date": 1000.0,
                               "collected_to_date": 0.0, "collected_in_advance": 0.0},
                "sinking_fund": {"collection_rate_pct": 0.0, "due_to_date": 0.0,
                                  "collected_to_date": 0.0, "collected_in_advance": 0.0},
            }),
        ),
    ):
        mock_db.unit_levy_ledger.aggregate.side_effect = [
            _agg(ledger_agg_result),
            _agg([{"count": 1}]),
        ]
        mock_db.annual_levies.find.return_value = _cursor([])
        mock_db.annual_levies.find_one = AsyncMock(return_value=None)

        result = await finance_mod._get_building_overview_mongo_fallback(BUILDING_ID, "2026")

    assert result["fund_health"] == 0.0
    assert result["total_obligations"] == 1000.0


@pytest.mark.asyncio
async def test_mongo_fallback_zero_levied_returns_zero_not_error():
    from routers import finance as finance_mod

    with (
        patch("routers.finance.db") as mock_db,
        patch("routers.finance._resolve_current_levy_year", new=AsyncMock(return_value=2026)),
        patch(
            "routers.finance._financial_read_service.get_consolidated_fund_balances",
            new=AsyncMock(return_value=None),
        ),
        # GAP-FIN-035: isolate from a real DB connection.
        patch(
            "routers.finance.get_collection_rate_metrics",
            new=AsyncMock(return_value={
                "collection_rate_pct": 0.0, "due_to_date": 0.0,
                "collected_to_date": 0.0, "collected_in_advance": 0.0,
                "admin_fund": {"collection_rate_pct": 0.0, "due_to_date": 0.0,
                               "collected_to_date": 0.0, "collected_in_advance": 0.0},
                "sinking_fund": {"collection_rate_pct": 0.0, "due_to_date": 0.0,
                                  "collected_to_date": 0.0, "collected_in_advance": 0.0},
            }),
        ),
    ):
        mock_db.unit_levy_ledger.aggregate.side_effect = [_agg([]), _agg([])]
        mock_db.annual_levies.find.return_value = _cursor([])
        mock_db.annual_levies.find_one = AsyncMock(return_value=None)

        result = await finance_mod._get_building_overview_mongo_fallback(BUILDING_ID, "2026")

    assert result["fund_health"] == 0.0
    assert result["levies_paid_pct"] == 0.0
    assert result["total_obligations"] == 0.0
