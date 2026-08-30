"""
Regression tests: GET /finance/summary must expose an unambiguous annual_levy_proposed
field on both admin_fund and sinking_fund whenever levy data exists.

admin_fund/sinking_fund in the response are spread directly from the raw
annual_levies document — their levy_income/total_income fields are YTD actuals,
not the full-year budget (CLAUDE.md documents this exact YTD-vs-annual mix-up as
a recurring bug source). annual_levy_proposed is computed via the canonical
get_levy_proposed_amounts() helper (already used consistently by analytics.py,
gst_bas.py, and most of finance.py) so any consumer needing "the real annual
total" has an unambiguous field, instead of risking levy_income being misread
as annual — as one dashboard page's "Levy Trend" chart already does for
/annual-levies (a separate endpoint, out of scope for this fix, but the same
risk class).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.permissions import Permission


def _cursor(rows):
    c = MagicMock()
    c.to_list = AsyncMock(return_value=rows)
    return c


def _make_mock_db(levy_doc, units=None, ledger_entries=None):
    """units: list of unit_number strings for the canonical roster used by
    get_ledger_quality() (GAP-FIN-014). Defaults to a single unit so
    ledger_quality computation has something to reconcile against.
    ledger_entries: optional list of (unit_number, net_balance) tuples used
    by get_ledger_quality()'s unit_levy_ledger.find() (distinct from the
    unit_levy_ledger.aggregate() pipeline mocked separately below, which
    feeds the legacy ledger-aggregate fields in unit_ledger_summary)."""
    units = units if units is not None else ["TH001"]
    ledger_entries = ledger_entries or []
    mock_db = MagicMock()
    mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
    mock_db.settings = MagicMock()
    mock_db.settings.find_one = AsyncMock(return_value={
        "grace_period_days": 14,
        "levy_due_months": [3, 6, 9, 12],
        "levy_due_day_type": "first",
    })
    mock_db.levy_categories.find = MagicMock(return_value=_cursor([]))
    mock_db.unit_levy_ledger.aggregate = MagicMock(return_value=_cursor([]))
    # Diagnostic re-check in get_finance_summary's empty-ledger-aggregate branch
    # (see routers/finance.py, Task #17) — always empty in this fixture, so the
    # branch always fires; count_documents must be an AsyncMock like every other
    # awaited DB call here.
    mock_db.unit_levy_ledger.count_documents = AsyncMock(return_value=0)
    mock_db.unit_levy_ledger.find = MagicMock(
        return_value=_cursor([{"unit_number": u, "net_balance": nb} for u, nb in ledger_entries])
    )
    mock_db.units.find = MagicMock(return_value=_cursor([{"unit_number": u} for u in units]))
    mock_db.levy_payments.aggregate = MagicMock(return_value=_cursor([]))
    mock_db.building_summaries.find_one = AsyncMock(return_value=None)
    return mock_db


async def _call(mock_db):
    from routers.finance import get_finance_summary
    # get_ledger_quality()/get_arrears_metrics() are imported from
    # utils.finance_helpers, which holds its own `db` module reference
    # (from `from database import db`) — patching routers.finance.db alone
    # does not intercept those calls, so both must be patched or these
    # helpers silently hit the real configured MongoDB instance.
    # building_id="13195" is real East Gate, which since 2026-08-09 is genuinely
    # PG-eligible for finance.summary -- pin the route to Mongo so these tests keep
    # exercising the Mongo-path computation under test against the hand-crafted
    # mock_db above, not real Postgres.
    with patch("routers.finance.db", mock_db), \
         patch("utils.finance_helpers.db", mock_db), \
         patch("routers.finance.get_user_permissions",
               return_value=Permission(can_view_finances=True)), \
         patch("routers.finance.get_finance_route_runtime_state", AsyncMock(return_value={
             "route_key": "finance.summary", "source": "mongo", "run_shadow": False,
             "eligible_for_postgres_read": False, "blocked_reason": "forced mongo for unit test",
             "domain_mode": "mongo_primary", "route_readiness": {"status": "not_started"},
         })):
        return await get_finance_summary(
            year="2026",
            current_user={"id": "u1", "building_id": "13195", "role": "super_admin"},
            building_id="13195",
        )


@pytest.mark.asyncio
async def test_annual_levy_proposed_present_and_correct_from_proposed_expenses():
    """Tier 1 of get_levy_proposed_amounts(): proposed_admin_expenses/
    proposed_sinking_expenses outrank the YTD levy_income fields."""
    levy_doc = {
        "year": "2026",
        "status": "partial_actual",
        "admin_fund": {"total_income": 15000.0, "levy_income": 15000.0},
        "sinking_fund": {"total_income": 5000.0, "levy_income": 5000.0},
        "payment_schedule": [],
        "proposed_admin_expenses": 340870.02,
        "proposed_sinking_expenses": 99504.90,
        "total_uoe": 1000,
    }
    result = await _call(_make_mock_db(levy_doc))

    assert result["admin_fund"]["annual_levy_proposed"] == 340870.02
    assert result["sinking_fund"]["annual_levy_proposed"] == 99504.90
    # And the raw YTD figure is still present, unaltered, alongside it.
    assert result["admin_fund"]["levy_income"] == 15000.0


@pytest.mark.asyncio
async def test_annual_levy_proposed_falls_back_to_fund_levy_income():
    """Tier 2 of get_levy_proposed_amounts(): no proposed_* fields → use
    admin_fund/sinking_fund.levy_income as the annual figure (older documents)."""
    levy_doc = {
        "year": "2026",
        "status": "confirmed",
        "admin_fund": {"total_income": 50000.0, "levy_income": 50000.0},
        "sinking_fund": {"total_income": 20000.0, "levy_income": 20000.0},
        "payment_schedule": [],
        "total_uoe": 1000,
    }
    result = await _call(_make_mock_db(levy_doc))

    assert result["admin_fund"]["annual_levy_proposed"] == 50000.0
    assert result["sinking_fund"]["annual_levy_proposed"] == 20000.0


@pytest.mark.asyncio
async def test_annual_levy_proposed_present_even_when_not_partial():
    """Unlike ytd_levy_income/ytd_total_income (only added when is_partial and
    proposed_* > 0), annual_levy_proposed must always be present — a fully
    confirmed, non-partial year still needs an unambiguous annual figure."""
    levy_doc = {
        "year": "2025",
        "status": "confirmed",
        "admin_fund": {"total_income": 100000.0, "levy_income": 100000.0},
        "sinking_fund": {"total_income": 40000.0, "levy_income": 40000.0},
        "payment_schedule": [],
        "total_uoe": 1000,
    }
    result = await _call(_make_mock_db(levy_doc))

    assert "annual_levy_proposed" in result["admin_fund"]
    assert "annual_levy_proposed" in result["sinking_fund"]
    # ytd_levy_income is correctly ABSENT here — status isn't "partial_actual".
    assert "ytd_levy_income" not in result["admin_fund"]


@pytest.mark.asyncio
async def test_annual_levy_total_uses_proposed_not_ytd_total_income():
    """GAP-FIN-014: unit_ledger_summary.annual_levy_total must equal the
    full-year proposed budget (admin + sinking), not the YTD
    admin_fund/sinking_fund.total_income sum — a partial year's YTD total
    understates the true annual figure."""
    levy_doc = {
        "year": "2026",
        "status": "partial_actual",
        "admin_fund": {"total_income": 15000.0, "levy_income": 15000.0},
        "sinking_fund": {"total_income": 5000.0, "levy_income": 5000.0},
        "payment_schedule": [],
        "proposed_admin_expenses": 340870.02,
        "proposed_sinking_expenses": 99504.90,
        "total_uoe": 1000,
    }
    result = await _call(_make_mock_db(levy_doc))

    assert result["unit_ledger_summary"]["annual_levy_total"] == round(340870.02 + 99504.90, 2)
    # The old (buggy) computation would have produced 15000 + 5000 = 20000.
    assert result["unit_ledger_summary"]["annual_levy_total"] != 20000.0


@pytest.mark.asyncio
async def test_summary_includes_ledger_quality_with_canonical_unit_count():
    """GAP-FIN-014: /finance/summary must expose ledger_quality with the
    canonical unit count derived from `units`, not a raw ledger row count."""
    levy_doc = {
        "year": "2026",
        "status": "confirmed",
        "admin_fund": {"total_income": 100000.0, "levy_income": 100000.0},
        "sinking_fund": {"total_income": 40000.0, "levy_income": 40000.0},
        "payment_schedule": [],
        "total_uoe": 1000,
    }
    units = [f"TH{n:03d}" for n in range(1, 88)]  # 87 canonical units
    ledger_entries = [(u, 0.0) for u in units]  # all 87 fully paid up
    result = await _call(_make_mock_db(levy_doc, units=units, ledger_entries=ledger_entries))

    assert "ledger_quality" in result
    assert result["ledger_quality"]["canonical_unit_count"] == 87
    assert result["ledger_quality"]["source"] == "units + unit_levy_ledger"
    assert "warnings" in result
    # units_paid_up + units_owing + units_credit must equal canonical_unit_count.
    uls = result["unit_ledger_summary"]
    assert uls["units_paid_up"] + uls["units_owing"] + uls["units_credit"] == 87
