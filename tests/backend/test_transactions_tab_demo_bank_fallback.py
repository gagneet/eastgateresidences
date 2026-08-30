"""Regression test for the empty Financial Transactions tab: Demo Bank imports, Strata Web scrapes,
and historical-year rebuilds all write to `demo_bank_transactions`, but the tab's endpoints only read
`expense_transactions`/`income_transactions` (+ a `financial_transactions` fallback). The fallback now
also surfaces `demo_bank_transactions` when the legacy mirrors are empty.
Covers routers/finance.py::_fallback_financial_transactions.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _cursor(rows):
    c = MagicMock()
    c.to_list = AsyncMock(return_value=rows)
    c.sort = MagicMock(return_value=c)
    return c


@pytest.mark.asyncio
async def test_surfaces_demo_bank_when_legacy_mirrors_empty():
    from routers.finance import _fallback_financial_transactions

    mock_db = MagicMock()
    mock_db.financial_transactions.find.return_value = _cursor([])  # legacy mirror empty
    demo_rows = [{
        "building_id": "16244", "direction": "credit", "amount_cents": 55000,
        "posted_date": "2026-03-15", "levy_year": "2026", "description": "Levy payment UA067",
        "source_type": "deft_settlement_import",
    }]
    mock_db.demo_bank_transactions.find.return_value = _cursor(demo_rows)

    with patch("routers.finance.db", mock_db):
        out = await _fallback_financial_transactions(
            building_id="16244", year="2026", transaction_type="income",
        )

    assert len(out) == 1
    assert out[0]["amount"] == 550.0             # amount_cents -> dollars
    assert out[0]["date"] == "2026-03-15"        # posted_date, not created_at
    assert out[0]["financial_year"] == "2026"    # from levy_year
    # Demo Bank query was building-scoped, direction-correct, and excluded archived rows.
    demo_query = mock_db.demo_bank_transactions.find.call_args[0][0]
    assert demo_query["building_id"] == "16244"
    assert demo_query["direction"] == "credit"
    assert demo_query["is_archived"] == {"$ne": True}


@pytest.mark.asyncio
async def test_expense_tab_queries_debit_direction():
    from routers.finance import _fallback_financial_transactions

    mock_db = MagicMock()
    mock_db.financial_transactions.find.return_value = _cursor([])
    mock_db.demo_bank_transactions.find.return_value = _cursor([])

    with patch("routers.finance.db", mock_db):
        await _fallback_financial_transactions(
            building_id="16244", year="2026", transaction_type="expense",
        )

    demo_query = mock_db.demo_bank_transactions.find.call_args[0][0]
    assert demo_query["direction"] == "debit"


@pytest.mark.asyncio
async def test_prefers_financial_transactions_and_never_double_counts():
    """When the legacy mirror has rows, Demo Bank is NOT queried — no duplication."""
    from routers.finance import _fallback_financial_transactions

    mock_db = MagicMock()
    ft_rows = [{
        "building_id": "16244", "direction": "credit", "amount": 100.0,
        "date": "2026-01-01", "financial_year": "2026",
    }]
    mock_db.financial_transactions.find.return_value = _cursor(ft_rows)
    mock_db.demo_bank_transactions.find.return_value = _cursor([{"unused": True}])

    with patch("routers.finance.db", mock_db):
        out = await _fallback_financial_transactions(
            building_id="16244", year="2026", transaction_type="income",
        )

    assert len(out) == 1
    assert out[0]["amount"] == 100.0
    mock_db.demo_bank_transactions.find.assert_not_called()


@pytest.mark.asyncio
async def test_income_rows_group_under_a_single_levy_category():
    """Demo Bank income rows carry no category_name field; the tab collapsed them all into one
    null category. Income is a single 'Levy' category — grouping, not per-row noise."""
    from routers.finance import _fallback_financial_transactions

    mock_db = MagicMock()
    mock_db.financial_transactions.find.return_value = _cursor([])
    demo_rows = [
        {"building_id": "16244", "direction": "credit", "amount_cents": 55000,
         "posted_date": "2026-03-15", "levy_year": "2026", "description": "Levy payment UA067"},
        {"building_id": "16244", "direction": "credit", "amount_cents": 42000,
         "posted_date": "2026-04-01", "levy_year": "2026", "description": "BPAY receipt UA012"},
    ]
    mock_db.demo_bank_transactions.find.return_value = _cursor(demo_rows)

    with patch("routers.finance.db", mock_db):
        out = await _fallback_financial_transactions(
            building_id="16244", year="2026", transaction_type="income",
        )

    assert len(out) == 2
    assert {r["category_name"] for r in out} == {"Levy"}


@pytest.mark.asyncio
async def test_expense_rows_take_category_from_description_parser():
    """An expense's category is embedded in its description ('... spend summary: X (Admin Fund)')
    and parsed by the canonical _extract_category_name — the SAME parser the posting service uses."""
    from routers.finance import _fallback_financial_transactions

    mock_db = MagicMock()
    mock_db.financial_transactions.find.return_value = _cursor([])
    demo_rows = [
        {"building_id": "16244", "direction": "debit", "amount_cents": 120000,
         "posted_date": "2024-06-30", "levy_year": "2024",
         "description": "Estimated 2024 spend summary: Cleaning (Admin Fund) — reconstructed"},
        {"building_id": "16244", "direction": "debit", "amount_cents": 80000,
         "posted_date": "2024-07-15", "levy_year": "2024",
         "description": "Estimated 2024 spend summary: Building Insurance (Sinking Fund) — reconstructed"},
    ]
    mock_db.demo_bank_transactions.find.return_value = _cursor(demo_rows)

    with patch("routers.finance.db", mock_db):
        out = await _fallback_financial_transactions(
            building_id="16244", year="2024", transaction_type="expense",
        )

    by_cat = {r["category_name"] for r in out}
    assert by_cat == {"Cleaning", "Building Insurance"}


@pytest.mark.asyncio
async def test_expense_row_without_parseable_description_falls_back_to_fund_label():
    """A row with neither a category_name nor a 'spend summary: X (Fund)' description falls back to
    the fund label rather than dumping the raw description as a category."""
    from routers.finance import _fallback_financial_transactions

    mock_db = MagicMock()
    mock_db.financial_transactions.find.return_value = _cursor([])
    demo_rows = [
        {"building_id": "16244", "direction": "debit", "amount_cents": 30000,
         "posted_date": "2024-02-01", "levy_year": "2024",
         "description": "Cheque 10231", "fund_type": "admin"},
    ]
    mock_db.demo_bank_transactions.find.return_value = _cursor(demo_rows)

    with patch("routers.finance.db", mock_db):
        out = await _fallback_financial_transactions(
            building_id="16244", year="2024", transaction_type="expense",
        )

    assert out[0]["category_name"] == "Admin Fund expense"


@pytest.mark.asyncio
async def test_explicit_category_name_field_is_respected():
    """If a row already carries a category_name, it wins over any parsing/fallback."""
    from routers.finance import _fallback_financial_transactions

    mock_db = MagicMock()
    mock_db.financial_transactions.find.return_value = _cursor([])
    demo_rows = [
        {"building_id": "16244", "direction": "debit", "amount_cents": 30000,
         "posted_date": "2024-02-01", "levy_year": "2024",
         "category_name": "Gardening", "description": "whatever"},
    ]
    mock_db.demo_bank_transactions.find.return_value = _cursor(demo_rows)

    with patch("routers.finance.db", mock_db):
        out = await _fallback_financial_transactions(
            building_id="16244", year="2024", transaction_type="expense",
        )

    assert out[0]["category_name"] == "Gardening"


@pytest.mark.asyncio
async def test_cross_building_isolation_in_demo_bank_query():
    from routers.finance import _fallback_financial_transactions

    mock_db = MagicMock()
    mock_db.financial_transactions.find.return_value = _cursor([])
    mock_db.demo_bank_transactions.find.return_value = _cursor([])

    with patch("routers.finance.db", mock_db):
        await _fallback_financial_transactions(
            building_id="13195", year="2025", transaction_type="income",
        )

    demo_query = mock_db.demo_bank_transactions.find.call_args[0][0]
    assert demo_query["building_id"] == "13195"  # never leaks another building
