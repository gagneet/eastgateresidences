"""Unit tests for GET /levy-categories/budget-summary (GAP-FIN-041).

The spending-categories page showed a `$0` proposed budget for imported buildings because the
per-category `budgeted_amount` is genuinely $0 there — the proposed budget is stored only at FUND
level in `annual_levies`. This endpoint surfaces the honest fund-level proposed budget + variance.

Covers routers/finance.py::get_levy_categories_budget_summary.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _cursor(rows):
    c = MagicMock()
    c.to_list = AsyncMock(return_value=rows)
    c.sort = MagicMock(return_value=c)
    return c


_PERMS = MagicMock(can_view_finances=True, can_manage_finances=True)


async def _call(mock_db, *, year="2025", building_id="16244"):
    from routers.finance import get_levy_categories_budget_summary
    with patch("routers.finance.db", mock_db), \
         patch("routers.finance.get_user_permissions", return_value=_PERMS), \
         patch(
             "routers.finance._get_actual_overrides_from_financial_transactions",
             AsyncMock(return_value=({}, {})),
         ):
        return await get_levy_categories_budget_summary(
            year=year, current_user={"id": "u1", "role": "strata_manager"},
            building_id=building_id,
        )


@pytest.mark.asyncio
async def test_fund_level_proposed_used_when_categories_not_itemised():
    """Imported year: categories carry actuals only (budgeted_amount=0); the fund-level proposed
    from annual_levies is surfaced instead of a misleading $0."""
    mock_db = MagicMock()
    mock_db.annual_levies.find_one = AsyncMock(return_value={
        "year": "2025", "building_id": "16244",
        "proposed_admin_expenses": 120000.0, "proposed_sinking_expenses": 60000.0,
    })
    mock_db.levy_categories.find.return_value = _cursor([
        {"id": "c1", "name": "Cleaning", "fund_type": "administrative",
         "budgeted_amount": 0.0, "actual_amount": 50000.0},
        {"id": "c2", "name": "Roof reserve", "fund_type": "sinking",
         "budgeted_amount": 0.0, "actual_amount": 20000.0},
    ])

    out = await _call(mock_db)

    assert out["available"] is True
    admin = out["administrative"]
    assert admin["proposed_budget"] == 120000.0
    assert admin["actual"] == 50000.0
    assert admin["variance"] == -70000.0          # under budget
    assert admin["has_itemised_budget"] is False
    assert admin["budget_source"] == "annual_levies"
    assert out["sinking"]["proposed_budget"] == 60000.0


@pytest.mark.asyncio
async def test_fund_level_is_authoritative_over_partial_itemisation():
    """The fund-level proposed from annual_levies is the AUTHORITATIVE total (the same figure
    /financials shows). Per-category budgets are a breakdown that can be INCOMPLETE for a
    year, so a partial itemised sum must NOT override the fund-level total. This is the exact East
    Gate LY2023 bug: admin itemises $91,355 of a $221,316 adopted budget — the page must show
    $221,316 (matching /finance), with the itemised remainder surfaced separately."""
    mock_db = MagicMock()
    mock_db.annual_levies.find_one = AsyncMock(return_value={
        "year": "2023", "building_id": "16244",
        "proposed_admin_expenses": 221316.0, "proposed_sinking_expenses": 60958.0,
    })
    mock_db.levy_categories.find.return_value = _cursor([
        {"id": "c1", "name": "Management Fees", "fund_type": "administrative",
         "budgeted_amount": 41355.0, "actual_amount": 40000.0},
        {"id": "c2", "name": "Insurance", "fund_type": "administrative",
         "budgeted_amount": 50000.0, "actual_amount": 49000.0},
    ])

    out = await _call(mock_db, year="2023")

    admin = out["administrative"]
    assert admin["has_itemised_budget"] is True
    assert admin["proposed_budget"] == 221316.0     # fund-level authoritative, NOT the 91,355 sum
    assert admin["itemised_total"] == 91355.0        # breakdown still reported
    assert admin["unitemised_remainder"] == 129961.0  # 221316 - 91355
    assert admin["budget_source"] == "annual_levies"
    # Sinking has no itemised categories at all → fund-level used, no remainder note.
    assert out["sinking"]["proposed_budget"] == 60958.0
    assert out["sinking"]["has_itemised_budget"] is False
    assert out["sinking"]["unitemised_remainder"] is None


@pytest.mark.asyncio
async def test_opening_and_closing_balance_surfaced():
    """Fund opening/closing balance (annual_levies.<fund>_fund) is passed through for the page's
    fund-position banner — most useful for the Sinking reserve. Missing → None, never $0."""
    mock_db = MagicMock()
    mock_db.annual_levies.find_one = AsyncMock(return_value={
        "year": "2025", "building_id": "16244",
        "proposed_admin_expenses": 100000.0, "proposed_sinking_expenses": 99262.0,
        "admin_fund": {"opening_balance": 15000.0, "closing_balance": 22000.0},
        "sinking_fund": {"opening_balance": 180000.0, "closing_balance": 240000.0},
    })
    mock_db.levy_categories.find.return_value = _cursor([])

    out = await _call(mock_db, year="2025")

    assert out["administrative"]["opening_balance"] == 15000.0
    assert out["administrative"]["closing_balance"] == 22000.0
    assert out["sinking"]["opening_balance"] == 180000.0
    assert out["sinking"]["closing_balance"] == 240000.0


@pytest.mark.asyncio
async def test_balances_read_from_cents_fields():
    """Some importers store balances as integer cents (opening_balance_cents/closing_balance_cents),
    not dollar floats. These must be converted, not shown as 'Not on record'. This is the exact
    East Gate regression: opening_balance was stored only as *_cents so the banner read empty."""
    mock_db = MagicMock()
    mock_db.annual_levies.find_one = AsyncMock(return_value={
        "year": "2024", "building_id": "16244",
        "proposed_admin_expenses": 100000.0, "proposed_sinking_expenses": 70795.0,
        "admin_fund": {"opening_balance_cents": 1500000, "closing_balance_cents": 2200000},
        "sinking_fund": {"opening_balance_cents": 18000000, "closing_balance_cents": 24000000},
    })
    mock_db.levy_categories.find.return_value = _cursor([])

    out = await _call(mock_db, year="2024")

    assert out["administrative"]["opening_balance"] == 15000.0
    assert out["administrative"]["closing_balance"] == 22000.0
    assert out["sinking"]["opening_balance"] == 180000.0
    assert out["sinking"]["closing_balance"] == 240000.0


@pytest.mark.asyncio
async def test_opening_balance_falls_back_to_prior_year_close():
    """When a year's own opening field isn't stored, opening = prior year's closing balance
    (opening[y] == closing[y-1]), so the banner shows a real reserve rather than 'Not on record'."""
    def _find(query, _proj=None):
        y = query.get("year")
        if y == "2025":
            return {"year": "2025", "building_id": "16244",
                    "proposed_admin_expenses": 100000.0,
                    "admin_fund": {"closing_balance": 30000.0}}       # no opening on this year
        if y == "2024":
            return {"year": "2024", "building_id": "16244",
                    "admin_fund": {"closing_balance": 25000.0}}       # prior year's close
        return None

    mock_db = MagicMock()
    mock_db.annual_levies.find_one = AsyncMock(side_effect=_find)
    mock_db.levy_categories.find.return_value = _cursor([])

    out = await _call(mock_db, year="2025")

    assert out["administrative"]["closing_balance"] == 30000.0
    assert out["administrative"]["opening_balance"] == 25000.0        # derived from FY2024 close


@pytest.mark.asyncio
async def test_missing_annual_levies_is_reported_not_zeroed():
    """No annual_levies doc → budget is 'missing' (available=False, proposed_budget=None),
    never silently shown as $0 (finance-integrity rule)."""
    mock_db = MagicMock()
    mock_db.annual_levies.find_one = AsyncMock(return_value=None)
    mock_db.levy_categories.find.return_value = _cursor([
        {"id": "c1", "name": "Cleaning", "fund_type": "administrative",
         "budgeted_amount": 0.0, "actual_amount": 50000.0},
    ])

    out = await _call(mock_db)

    assert out["available"] is False
    admin = out["administrative"]
    assert admin["proposed_budget"] is None
    assert admin["variance"] is None
    assert admin["budget_source"] == "missing"
    assert admin["actual"] == 50000.0              # actual is still real


@pytest.mark.asyncio
async def test_building_scoped_queries():
    """Both DB reads must be scoped to the caller's building_id — never leak another tenant."""
    mock_db = MagicMock()
    mock_db.annual_levies.find_one = AsyncMock(return_value=None)
    mock_db.levy_categories.find.return_value = _cursor([])

    await _call(mock_db, year="2025", building_id="13195")

    assert mock_db.annual_levies.find_one.call_args[0][0]["building_id"] == "13195"
    assert mock_db.levy_categories.find.call_args[0][0]["building_id"] == "13195"
