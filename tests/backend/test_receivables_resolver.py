"""
tests/backend/test_receivables_resolver.py — period-aware AR/receivable resolver.

GAP-FIN-015: bank-feed matching candidates were built from
`open_levy_cents = abs(latest unit_levy_ledger.net_balance)`, a single annual figure
that can never match a historical/reconstructed transaction once the current-year
balance has been paid down to zero. get_open_receivable_for_unit() must resolve the
amount owed for the RELEVANT period instead.
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

from services.receivables_resolver import get_open_receivable_for_unit

BUILDING_A = "13195"
BUILDING_B = "16244"

_SCHEDULE = [
    {"quarter": "Q1", "due_date": "2021-03-31"},
    {"quarter": "Q2", "due_date": "2021-06-01"},
    {"quarter": "Q3", "due_date": "2021-09-01"},
    {"quarter": "Q4", "due_date": "2021-12-01"},
]


def _mock_db(*, ledger_by_year: dict, latest_ledger: dict | None, levy_doc: dict | None):
    db = MagicMock()

    async def _find_ledger(query, *args, **kwargs):
        sort = kwargs.get("sort")
        if sort:
            return latest_ledger
        year = query.get("year")
        return ledger_by_year.get(year)

    db.unit_levy_ledger.find_one = AsyncMock(side_effect=_find_ledger)
    db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
    return db


@pytest.mark.asyncio
async def test_exact_amount_match_resolves_historical_period_not_current_balance():
    """A 2021 Q1 payment of $553.84 must resolve against the 2021 schedule, even
    though this unit's CURRENT (2026) net_balance is $0 — the exact bug GAP-FIN-015
    identified as blocking historical/reconstructed transaction matching."""
    ledger_2021 = {
        "building_id": BUILDING_A, "unit_number": "TH078", "year": "2021",
        "total_levied": 2215.36, "total_paid": 0.0, "net_balance": 2215.36,
    }
    latest_ledger_now_zero = {
        "building_id": BUILDING_A, "unit_number": "TH078", "year": "2026",
        "total_levied": 2400.00, "total_paid": 2400.00, "net_balance": 0.0,
    }
    db = _mock_db(
        ledger_by_year={"2021": ledger_2021},
        latest_ledger=latest_ledger_now_zero,
        levy_doc={"building_id": BUILDING_A, "year": "2021", "payment_schedule": _SCHEDULE},
    )
    with patch("services.receivables_resolver.db", db):
        result = await get_open_receivable_for_unit(
            building_id=BUILDING_A, unit_number="TH078",
            financial_year="2021", amount_cents=55384,
        )

    assert result["source"] == "levy_installment_schedule"
    assert result["confidence"] == "high"
    assert result["period"] == "Q1"
    assert result["open_receivable_cents"] == 55384
    assert result["fallback_used"] is False


@pytest.mark.asyncio
async def test_as_of_date_brackets_to_current_period_when_no_amount_hint():
    ledger_2021 = {
        "building_id": BUILDING_A, "unit_number": "TH078", "year": "2021",
        "total_levied": 2215.36, "total_paid": 0.0, "net_balance": 2215.36,
    }
    db = _mock_db(
        ledger_by_year={"2021": ledger_2021},
        latest_ledger=ledger_2021,
        levy_doc={"building_id": BUILDING_A, "year": "2021", "payment_schedule": _SCHEDULE},
    )
    with patch("services.receivables_resolver.db", db):
        result = await get_open_receivable_for_unit(
            building_id=BUILDING_A, unit_number="TH078",
            as_of_date=date(2021, 4, 15),
        )

    assert result["source"] == "levy_installment_schedule"
    assert result["confidence"] == "medium"
    assert result["period"] == "Q1"  # most recent due_date on/before 2021-04-15


@pytest.mark.asyncio
async def test_no_amount_or_date_falls_back_to_current_period_due():
    ledger = {
        "building_id": BUILDING_A, "unit_number": "U1", "year": "2026",
        "total_levied": 4000.0, "total_paid": 1000.0, "net_balance": 3000.0,
    }
    future_schedule = [
        {"quarter": "Q1", "due_date": "2026-01-01"},
        {"quarter": "Q2", "due_date": "2026-04-01"},
        {"quarter": "Q3", "due_date": "2099-09-01"},
        {"quarter": "Q4", "due_date": "2099-12-01"},
    ]
    db = _mock_db(
        ledger_by_year={"2026": ledger},
        latest_ledger=ledger,
        levy_doc={"building_id": BUILDING_A, "year": "2026", "payment_schedule": future_schedule},
    )
    with patch("services.receivables_resolver.db", db):
        result = await get_open_receivable_for_unit(
            building_id=BUILDING_A, unit_number="U1", financial_year="2026",
        )

    assert result["source"] == "current_period_due"
    assert result["confidence"] == "medium"
    assert result["fallback_used"] is False


@pytest.mark.asyncio
async def test_no_schedule_falls_back_to_that_years_ledger_balance():
    ledger_2019 = {
        "building_id": BUILDING_A, "unit_number": "U2", "year": "2019",
        "total_levied": 1000.0, "total_paid": 400.0, "net_balance": 600.0,
    }
    db = _mock_db(
        ledger_by_year={"2019": ledger_2019},
        latest_ledger={"year": "2026", "net_balance": 0.0},
        levy_doc=None,  # no annual_levies doc for 2019 at all
    )
    with patch("services.receivables_resolver.db", db):
        result = await get_open_receivable_for_unit(
            building_id=BUILDING_A, unit_number="U2", financial_year="2019",
        )

    assert result["source"] == "ledger_year_balance"
    assert result["confidence"] == "low"
    assert result["fallback_used"] is True
    assert result["open_receivable_cents"] == 60000


@pytest.mark.asyncio
async def test_no_ledger_row_for_year_falls_back_to_latest_year():
    db = _mock_db(
        ledger_by_year={},  # nothing for the requested year
        latest_ledger={"year": "2026", "net_balance": -150.25},  # credit balance
        levy_doc=None,
    )
    with patch("services.receivables_resolver.db", db):
        result = await get_open_receivable_for_unit(
            building_id=BUILDING_A, unit_number="U3", financial_year="2015",
        )

    assert result["source"] == "ledger_net_balance_fallback"
    assert result["confidence"] == "low"
    assert result["fallback_used"] is True
    assert result["open_receivable_cents"] == 15025
    assert result["financial_year"] == "2026"


@pytest.mark.asyncio
async def test_calendar_year_guess_retries_prior_year_for_non_january_fy():
    """A building whose financial year starts mid-year (e.g. July) can have a
    January transaction that actually belongs to the PRIOR calendar year's FY
    document. Guessing financial_year from as_of_date.year alone would find
    nothing for "2026" — the resolver must retry "2025" before falling back to
    latest-year net_balance."""
    non_jan_schedule = [
        {"quarter": "Q1", "due_date": "2025-07-01"},
        {"quarter": "Q2", "due_date": "2025-10-01"},
        {"quarter": "Q3", "due_date": "2026-01-01"},
        {"quarter": "Q4", "due_date": "2026-04-01"},
    ]
    ledger_2025 = {
        "building_id": BUILDING_A, "unit_number": "U9", "year": "2025",
        "total_levied": 4000.0, "total_paid": 0.0, "net_balance": 4000.0,
    }
    db = MagicMock()

    async def _find_ledger(query, *args, **kwargs):
        if kwargs.get("sort"):
            return {"year": "2099", "net_balance": 0.0}  # must NOT be used here
        return ledger_2025 if query.get("year") == "2025" else None

    async def _find_levy(query, *args, **kwargs):
        return {"payment_schedule": non_jan_schedule} if query.get("year") == "2025" else None

    db.unit_levy_ledger.find_one = AsyncMock(side_effect=_find_ledger)
    db.annual_levies.find_one = AsyncMock(side_effect=_find_levy)

    with patch("services.receivables_resolver.db", db):
        result = await get_open_receivable_for_unit(
            building_id=BUILDING_A, unit_number="U9", as_of_date=date(2026, 1, 15),
        )

    assert result["financial_year"] == "2025"
    assert result["source"] == "levy_installment_schedule"
    assert result["period"] == "Q3"  # due 2026-01-01, on/before 2026-01-15
    assert any("financial_year_start_month" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_current_period_due_uses_paid_this_year_not_contaminated_total_paid():
    """Regression for the 2026-08-01 finding: ledger.total_paid can be a
    back-solved, cumulative-since-inception figure (not scoped to this year) --
    confirmed live against a real East Gate unit's own reconciliation_note. If
    Tier 2 waterfalls that raw total_paid across the year's quarters, an
    inflated multi-year total marks every quarter "paid" and the resolver
    finds no overdue/unpaid/partial period to match an incoming payment
    against, even though one genuinely exists (net_balance says $3,000 is
    still owed). total_levied - net_balance ($1,000) is the correctly
    year-scoped figure and must be what Tier 2 waterfalls instead."""
    ledger = {
        "building_id": BUILDING_A, "unit_number": "U1", "year": "2026",
        "total_levied": 4000.0,
        "total_paid": 50000.0,  # contaminated: many years' cumulative payments
        "net_balance": 3000.0,  # real: only $1,000 of this year's $4,000 paid
    }
    future_schedule = [
        {"quarter": "Q1", "due_date": "2026-01-01"},
        {"quarter": "Q2", "due_date": "2026-04-01"},
        {"quarter": "Q3", "due_date": "2099-09-01"},
        {"quarter": "Q4", "due_date": "2099-12-01"},
    ]
    db = _mock_db(
        ledger_by_year={"2026": ledger},
        latest_ledger=ledger,
        levy_doc={"building_id": BUILDING_A, "year": "2026", "payment_schedule": future_schedule},
    )
    with patch("services.receivables_resolver.db", db):
        result = await get_open_receivable_for_unit(
            building_id=BUILDING_A, unit_number="U1", financial_year="2026",
        )

    # If total_paid=$50,000 had been waterfalled instead, every quarter would show
    # "paid" and due_quarter would be None, falling through to a Tier 3/4 fallback.
    assert result["source"] == "current_period_due"
    assert result["confidence"] == "medium"
    assert result["fallback_used"] is False


@pytest.mark.asyncio
async def test_cross_building_isolation_uses_scoped_building_id():
    """Building 16244 must never resolve against building 13195's ledger rows —
    assert every DB call carries the caller's own building_id, not a hardcoded one."""
    ledger = {
        "building_id": BUILDING_B, "unit_number": "U1", "year": "2026",
        "total_levied": 1000.0, "total_paid": 0.0, "net_balance": 1000.0,
    }
    db = _mock_db(ledger_by_year={"2026": ledger}, latest_ledger=ledger, levy_doc=None)

    with patch("services.receivables_resolver.db", db):
        await get_open_receivable_for_unit(
            building_id=BUILDING_B, unit_number="U1", financial_year="2026",
        )

    for call in db.unit_levy_ledger.find_one.await_args_list:
        query = call.args[0]
        assert query.get("building_id") == BUILDING_B
