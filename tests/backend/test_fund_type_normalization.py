"""
tests/backend/test_fund_type_normalization.py

Regression for a 2026-08-01 finding: levy_categories.fund_type is stored in the
canonical SHORT form ("admin"/"sinking") by every current writer (confirmed
live against every East Gate category document; this is also what
backend/routers/onboarding.py's generic CSV category-import path writes for
ANY building being onboarded, not an East-Gate-only quirk). Several read
sites in backend/routers/finance.py and
backend/utils/finance_helpers.py::get_expense_breakdown_by_category compared
against the legacy LONG form "administrative" directly, so the comparison
never matched -- admin fund actual/budgeted expense figures silently read
back as $0.00 for every year, on every building using the standard onboarding
flow.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)


def test_normalize_cats_fund_type_canonicalises_short_form_in_place():
    from routers.finance import _normalize_cats_fund_type

    cats = [
        {"name": "Accounting", "fund_type": "admin", "actual_amount": 100.0},
        {"name": "Landscaping", "fund_type": "sinking", "actual_amount": 200.0},
        {"name": "Legacy row", "fund_type": "administrative", "actual_amount": 50.0},
    ]
    result = _normalize_cats_fund_type(cats)

    assert result[0]["fund_type"] == "administrative"
    assert result[1]["fund_type"] == "sinking"
    assert result[2]["fund_type"] == "administrative"  # already-long-form rows are a no-op


def test_levy_category_fund_filter_matches_short_and_legacy_admin_forms():
    from routers.finance import _levy_category_fund_filter

    assert _levy_category_fund_filter("administrative") == {"$in": ["administrative", "admin"]}
    assert _levy_category_fund_filter("admin") == {"$in": ["administrative", "admin"]}
    assert _levy_category_fund_filter("sinking") == {"$in": ["sinking", "capital_works", "capital works"]}


@pytest.mark.asyncio
async def test_finance_charts_expense_by_category_nonzero_with_short_form_fund_type():
    """The exact live symptom reported 2026-08-01: /finance/charts' Admin Fund
    Expenses card read $0.00 for every year because every real category
    document uses fund_type="admin", not "administrative"."""
    from routers.finance import get_finance_charts

    mock_levy = {"year": "2026", "building_id": "13195"}
    mock_cats = [
        {"name": "Accounting costs", "fund_type": "admin", "actual_amount": 495.0, "budgeted_amount": 0.0},
        {"name": "Landscaping", "fund_type": "sinking", "actual_amount": 733.0, "budgeted_amount": 0.0},
    ]

    mock_db = MagicMock()
    mock_db.annual_levies.find_one = AsyncMock(return_value=mock_levy)
    mock_db.levy_categories.find.return_value.to_list = AsyncMock(return_value=mock_cats)
    mock_db.levy_payments.aggregate.return_value.to_list = AsyncMock(return_value=[])

    user = {"id": "u1", "role": "super_admin", "building_id": "13195",
            "is_active": True, "email": "a@test.com"}

    with patch("routers.finance.db", mock_db):
        result = await get_finance_charts(year="2026", current_user=user, building_id="13195")

    admin_expenses = result["expense_by_category"]["administrative"]
    sinking_expenses = result["expense_by_category"]["sinking"]
    assert admin_expenses, "Admin Fund Expenses must not be empty when a real 'admin'-fund_type category exists"
    assert admin_expenses[0]["value"] == pytest.approx(495.0)
    assert sinking_expenses[0]["value"] == pytest.approx(733.0)


@pytest.mark.asyncio
async def test_get_expense_breakdown_by_category_nonzero_with_short_form_fund_type():
    from utils.finance_helpers import get_expense_breakdown_by_category

    mock_cats = [
        {"name": "Accounting costs", "fund_type": "admin", "actual_amount": 495.0, "budgeted_amount": 0.0},
        {"name": "Landscaping", "fund_type": "sinking", "actual_amount": 733.0, "budgeted_amount": 0.0},
    ]
    mock_cursor = MagicMock()
    mock_cursor.sort.return_value.to_list = AsyncMock(return_value=mock_cats)
    mock_db = MagicMock()
    mock_db.levy_categories.find.return_value = mock_cursor

    with patch("utils.finance_helpers.db", mock_db):
        result = await get_expense_breakdown_by_category("2026", "13195")

    assert result["administrative"], "admin breakdown must not be empty for a real 'admin'-fund_type category"
    assert result["administrative"][0]["value"] == pytest.approx(495.0)
    assert result["sinking"][0]["value"] == pytest.approx(733.0)
