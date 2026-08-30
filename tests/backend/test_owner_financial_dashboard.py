"""Tests for owner financial dashboard (S4 - F1/F2)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BUILDING_ID = "13195"
UNIT = "TH087"


@pytest.mark.asyncio
async def test_levy_breakdown_per_category_sums_to_total():
    """Sum of per-category quarterly amounts equals total quarterly levy (within rounding)."""
    unit_doc = {
        "entitlement": 87,
        "lot_entitlement": 87,
        "balance_owing": 0,
        "balance_credit": 0,
    }
    budgets = [
        {"category_name": "Building insurance", "annual_amount": 100000, "fund_type": "admin"},
        {"category_name": "Management fee", "annual_amount": 80000, "fund_type": "admin"},
        {"category_name": "Sinking fund", "annual_amount": 60000, "fund_type": "sinking"},
    ]

    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value=unit_doc)
    mock_db.units.aggregate = MagicMock(
        return_value=MagicMock(to_list=AsyncMock(return_value=[{"_id": None, "total_uoe": 7569}])))
    # Service uses levy_categories (year="YYYY", name, budgeted_amount)
    levy_cats = [
        {"name": b["category_name"], "budgeted_amount": b["annual_amount"], "fund_type": b["fund_type"]}
        for b in budgets
    ]
    mock_db.levy_categories.find = MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=levy_cats)))
    mock_db.levy_categories.find_one = AsyncMock(return_value=None)
    mock_db.unit_levy_ledger.find = MagicMock(
        return_value=MagicMock(sort=MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[])))))

    with patch("services.owner_finance_service.db", mock_db):
        from services.owner_finance_service import get_levy_breakdown
        result = await get_levy_breakdown(UNIT, BUILDING_ID)

    categories = result["categories"]
    total_from_cats = sum(c["your_quarterly"] for c in categories)
    assert abs(total_from_cats - result.get("quarterly_levy", total_from_cats)) < 10


@pytest.mark.asyncio
async def test_per_lot_share_uses_correct_uoe_weight():
    """A unit with higher UOE gets a proportionally higher per-category amount."""
    budgets = [{"category_name": "Test category", "annual_amount": 100000, "fund_type": "admin"}]

    def make_mock_db(unit_doc):
        m = MagicMock()
        m.units.find_one = AsyncMock(return_value=unit_doc)
        m.units.aggregate = MagicMock(
            return_value=MagicMock(to_list=AsyncMock(return_value=[{"_id": None, "total_uoe": 8700}]))
        )
        # Service uses levy_categories (name, budgeted_amount, fund_type)
        levy_cats = [
            {"name": b["category_name"], "budgeted_amount": b["annual_amount"], "fund_type": b["fund_type"]}
            for b in budgets
        ]
        m.levy_categories.find = MagicMock(
            return_value=MagicMock(to_list=AsyncMock(return_value=levy_cats))
        )
        m.levy_categories.find_one = AsyncMock(return_value=None)
        m.unit_levy_ledger.find = MagicMock(
            return_value=MagicMock(sort=MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[])))))
        return m

    unit_high = {"entitlement": 120, "period_levy": 2000.0, "balance_owing": 0, "balance_credit": 0}
    unit_low = {"entitlement": 60, "period_levy": 1000.0, "balance_owing": 0, "balance_credit": 0}

    db_high = make_mock_db(unit_high)
    db_low = make_mock_db(unit_low)

    with patch("services.owner_finance_service.db", db_high):
        from services.owner_finance_service import get_levy_breakdown
        result_high = await get_levy_breakdown("TH071", BUILDING_ID)

    with patch("services.owner_finance_service.db", db_low):
        result_low = await get_levy_breakdown("TH072", BUILDING_ID)

    high_amount = result_high["categories"][0]["your_quarterly"]
    low_amount = result_low["categories"][0]["your_quarterly"]
    assert high_amount > low_amount
    assert abs(high_amount / low_amount - 120 / 60) < 0.01


@pytest.mark.asyncio
async def test_savings_per_lot_share_computed_correctly():
    """your_share_cents = total_saved_ytd_cents * (your_uoe / total_uoe)."""
    unit_doc = {"entitlement": 100}
    total_saved = 1_000_000
    total_uoe = 8700

    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value=unit_doc)
    mock_db.units.aggregate = MagicMock(
        return_value=MagicMock(to_list=AsyncMock(return_value=[{"_id": None, "total_uoe": total_uoe}]))
    )
    # Service now uses db.savings_events (tenant-scoped) + db.units.count_documents for lot count
    mock_db.savings_events.aggregate = MagicMock(
        return_value=MagicMock(to_list=AsyncMock(return_value=[{"_id": None, "total": total_saved}]))
    )
    mock_db.units.count_documents = AsyncMock(return_value=87)

    with patch("services.owner_finance_service.db", mock_db):
        from services.owner_finance_service import get_savings_per_lot
        result = await get_savings_per_lot(UNIT, BUILDING_ID)

    expected = round(total_saved * 100 / total_uoe)
    assert result["your_share_cents"] == expected
    assert result["total_saved_ytd_cents"] == total_saved


@pytest.mark.asyncio
async def test_owner_finance_resolves_unit_from_user_units_when_user_has_no_legacy_unit():
    """Owner finance endpoints should support canonical user_units links."""
    mock_db = MagicMock()
    mock_db.user_units.find_one = AsyncMock(return_value={"unit_number": UNIT})

    with patch("routers.owner_finance.db", mock_db):
        from routers.owner_finance import _resolve_owner_finance_unit

        result = await _resolve_owner_finance_unit(
            {"id": "u-owner", "role": "owner", "email": "owner@example.test"},
            BUILDING_ID,
        )

    assert result == UNIT
    mock_db.user_units.find_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_building_health_explanation_includes_all_components():
    """health-explanation endpoint returns all 4 components with plain_english text."""
    summary = {
        "building_id": BUILDING_ID,
        "health_score": 84,
        "arrears_rate_pct": 1.1,
        "sinking_fund_pct": 91.0,
        "open_work_orders": 0,
        "compliance_overdue_count": 1,
        "last_computed_at": "2026-03-31T00:00:00Z",
    }
    mock_db = MagicMock()
    mock_db.building_summaries.find_one = AsyncMock(return_value=summary)

    with patch("routers.owner_finance.db", mock_db):
        from routers.owner_finance import building_health_explanation
        user = {"id": "u1", "role": "owner", "unit_number": UNIT}
        result = await building_health_explanation(current_user=user, building_id=BUILDING_ID)

    # score=84 < 85, so grade should be "B"
    assert result["grade"] == "B"
    component_keys = {c["key"] for c in result["components"]}
    assert {"sinking_fund", "arrears", "maintenance", "compliance"} == component_keys
    for comp in result["components"]:
        assert comp.get("plain_english"), f"Missing plain_english for {comp['key']}"
