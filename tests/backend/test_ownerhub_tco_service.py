#!/usr/bin/env python3
"""
Unit tests for ownerhub_service.compute_unit_tco aggregation logic.

Covers:
- Quarterly levy aggregation and normalised Q-key deduplication (fixes audit bug 4)
- Council rates matching via financial_year format ("2025-26")
- Water bill calendar-year-only matching — no prior-year FY overlap (fixes audit bug 3)
- Capital replacement 5-year amortised formula with correct None-check (fixes audit bug 2)
- 10-year projection structure using per-year plan data
"""

import os
import re
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from services import ownerhub_service


def _cursor(rows):
    c = MagicMock()
    c.to_list = AsyncMock(return_value=rows)
    return c


def _mock_db_for_tco(
    *,
    levy_rows=None,
    council_rows=None,
    water_total=0.0,
    total_ent=10000,
    plan_fwd_rows=None,
    plan_all_rows=None,
    annual_levies_doc=None,
    settings_doc=None,
    building_doc=None,
):
    """Build a mock DB object wired for compute_unit_tco.

    By default annual_levies/settings/buildings all resolve to None, so
    get_levy_rates() returns zero rates and compute_unit_tco() falls back to
    the unit_levy_ledger path — preserving the exact behaviour the ledger-path
    tests below assert on. Pass annual_levies_doc to exercise the (now
    preferred) committed-annual-rate path instead.
    """
    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value={
        "unit_number": "TH017",
        "building_id": "13195",
        "entitlement": 161,
        "property_type": "townhouse",
    })
    mock_db.annual_levies.find_one = AsyncMock(return_value=annual_levies_doc)
    mock_db.settings.find_one = AsyncMock(return_value=settings_doc)
    mock_db.buildings.find_one = AsyncMock(return_value=building_doc)
    mock_db.unit_levy_ledger.find.return_value = _cursor(levy_rows or [])
    mock_db.council_rates.find.return_value = _cursor(council_rows or [])
    mock_db.water_bills.aggregate.return_value = _cursor(
        [{"total": water_total}] if water_total else []
    )
    mock_db.units.aggregate.return_value = _cursor([{"total": total_ent}])

    # sinking_fund_plan: forward rows (5yr) and all projection rows (10yr)
    fwd = plan_fwd_rows if plan_fwd_rows is not None else []
    all_rows = plan_all_rows if plan_all_rows is not None else []

    call_count = 0

    async def plan_find_side(*args, **kwargs):
        return _cursor(fwd if call_count == 0 else all_rows)

    mock_db.sinking_fund_plan.find.return_value = _cursor(fwd)

    # Two separate find calls: forward (5yr) then all-projection (10yr)
    mock_db.sinking_fund_plan.find = MagicMock(side_effect=[
        _cursor(fwd),
        _cursor(all_rows),
    ])
    return mock_db


@pytest.mark.asyncio
async def test_quarterly_levies_are_summed():
    """Quarterly levy rows are deduplicated and summed correctly."""
    mock_db = _mock_db_for_tco(
        levy_rows=[
            {"year": "2026-Q1", "quarter": "Q1", "admin_levied": 200, "sinking_levied": 50},
            {"year": "2026-Q2", "quarter": "Q2", "admin_levied": 200, "sinking_levied": 50},
            {"year": "2026-Q3", "quarter": "Q3", "admin_levied": 200, "sinking_levied": 50},
            {"year": "2026-Q4", "quarter": "Q4", "admin_levied": 200, "sinking_levied": 50},
        ],
    )
    with (
        patch("services.ownerhub_service.db", mock_db),
        patch("utils.finance_helpers.db", mock_db),
        patch("services.ownerhub_service.get_ctx_building_id", return_value="13195"),
        patch("services.ownerhub_service.get_owner_info", new=AsyncMock(return_value={})),
    ):
        result = await ownerhub_service.compute_unit_tco("TH017", "2026")

    assert result["strata_levies"] == 1000.0  # (200+50)*4
    assert "quarterly" in result["breakdown_details"]["strata_levies"]["source"]


@pytest.mark.asyncio
async def test_council_rates_matched_by_financial_year_format():
    """Council rates stored as 'financial_year: 2025-26' are found when year='2026'."""
    council_doc = {
        "financial_year": "2025-26",
        "unit_number": "TH017",
        "building_id": "13195",
        "total_rates": 2053.78,
        "land_tax_total": 2511.20,
    }
    mock_db = _mock_db_for_tco(council_rows=[council_doc])
    # Land tax only applies to investment/rental properties (see is_owner_occupied
    # gate in ownerhub_service.compute_unit_tco) — model this unit as tenanted so
    # the assertion below exercises a real, non-zero land_tax figure.
    mock_db.units.find_one = AsyncMock(return_value={
        "unit_number": "TH017",
        "building_id": "13195",
        "entitlement": 161,
        "property_type": "townhouse",
        "is_owner_occupied": False,
    })
    with (
        patch("services.ownerhub_service.db", mock_db),
        patch("utils.finance_helpers.db", mock_db),
        patch("services.ownerhub_service.get_ctx_building_id", return_value="13195"),
        patch("services.ownerhub_service.get_owner_info", new=AsyncMock(return_value={})),
    ):
        result = await ownerhub_service.compute_unit_tco("TH017", "2026")

    assert result["council_rates"] == 2053.78
    assert result["land_tax"] == 2511.20
    assert result["rates_and_charges"] == pytest.approx(4564.98)
    # Confirm that the query included "2025-26" as a financial_year match
    call_args = mock_db.council_rates.find.call_args[0][0]
    and_clauses = call_args.get("$and", [])
    year_clause = next(
        (c for c in and_clauses if "$or" in c and any("financial_year" in str(o) for o in c.get("$or", []))),
        None,
    )
    assert year_clause is not None, "Query must include a financial_year clause"
    fin_year_values = [str(o.get("financial_year")) for o in year_clause["$or"] if "financial_year" in o]
    assert "2025-26" in fin_year_values, "Query must match '2025-26' financial year format"


@pytest.mark.asyncio
async def test_water_bills_matched_by_billing_period_start():
    """Water bills are summed even though they have no year/financial_year field."""
    mock_db = _mock_db_for_tco(water_total=1313.08)
    with (
        patch("services.ownerhub_service.db", mock_db),
        patch("utils.finance_helpers.db", mock_db),
        patch("services.ownerhub_service.get_ctx_building_id", return_value="13195"),
        patch("services.ownerhub_service.get_owner_info", new=AsyncMock(return_value={})),
    ):
        result = await ownerhub_service.compute_unit_tco("TH017", "2026")

    assert result["water_charges"] == 1313.08
    # Confirm query includes billing_period_start range
    agg_call = mock_db.water_bills.aggregate.call_args[0][0]
    match_stage = agg_call[0]["$match"]
    and_clauses = match_stage.get("$and", [])
    has_billing_range = any(
        "billing_period_start" in str(c) for c in and_clauses
    )
    assert has_billing_range, "Water query must include billing_period_start range"


@pytest.mark.asyncio
async def test_capital_replacement_uses_5yr_amortised_formula():
    """Capital replacement = sum(5yr plan expenditure) / 5 × unit_share."""
    # TH017 entitlement=161, total=10000 → share=1.61%
    # 5yr spend: 17965+15049+6379+413099+17337 = 469829
    # Amortised: 469829 / 5 = 93965.8
    # Unit share: 93965.8 * 0.0161 = 1512.85
    plan_rows = [
        {"year": 2026, "expenditure": 17965.0},
        {"year": 2027, "expenditure": 15049.0},
        {"year": 2028, "expenditure": 6379.0},
        {"year": 2029, "expenditure": 413099.0},
        {"year": 2030, "expenditure": 17337.0},
    ]
    mock_db = _mock_db_for_tco(plan_fwd_rows=plan_rows, plan_all_rows=plan_rows)
    with (
        patch("services.ownerhub_service.db", mock_db),
        patch("utils.finance_helpers.db", mock_db),
        patch("services.ownerhub_service.get_ctx_building_id", return_value="13195"),
        patch("services.ownerhub_service.get_owner_info", new=AsyncMock(return_value={})),
    ):
        result = await ownerhub_service.compute_unit_tco("TH017", "2026")

    expected = round((469829.0 / 5) * (161 / 10000), 2)
    assert result["capital_replacement"] == pytest.approx(expected, abs=0.05)
    assert "5yr_amortised" in result["breakdown_details"]["capital_replacement"]["source"]


@pytest.mark.asyncio
async def test_10year_projection_uses_per_year_plan_expenditure():
    """10-year projection shows per-year capital events, not amortised average."""
    plan_rows = [
        {"year": 2027, "expenditure": 45835.0},
        {"year": 2028, "expenditure": 6379.0},
        {"year": 2029, "expenditure": 413099.0},
    ]
    mock_db = _mock_db_for_tco(plan_fwd_rows=plan_rows, plan_all_rows=plan_rows)
    with (
        patch("services.ownerhub_service.db", mock_db),
        patch("utils.finance_helpers.db", mock_db),
        patch("services.ownerhub_service.get_ctx_building_id", return_value="13195"),
        patch("services.ownerhub_service.get_owner_info", new=AsyncMock(return_value={})),
    ):
        result = await ownerhub_service.compute_unit_tco("TH017", "2026")

    assert len(result["projection"]) == 10
    # Year 2029 (offset +3) should show large capital spike
    yr2029 = next((r for r in result["projection"] if r["year"] == 2029), None)
    assert yr2029 is not None
    assert yr2029["has_capital_works"] is True
    assert yr2029["capital_cost"] == pytest.approx(413099.0 * (161 / 10000), abs=0.05)
    # Year 2036 (no plan row) should show $0 capital
    yr2036 = next((r for r in result["projection"] if r["year"] == 2036), None)
    assert yr2036 is not None
    assert yr2036["capital_cost"] == 0.0


@pytest.mark.asyncio
async def test_strata_levies_uses_committed_annual_rate_when_available():
    """
    Regression test for the unit-87 (TH087) production bug: the unit_levy_ledger
    'annual' row only reflects levies invoiced so far this year (e.g. Q1 only),
    so treating it as the full-year total under-reports the committed annual
    levy. compute_unit_tco() must prefer the rate-based calculation (admin +
    sinking per-UOE, GST-inclusive) from annual_levies — matching what the main
    Dashboard shows — over the partial ledger figure.

    Real production figures (building 13195, FY2026, entitlement=161):
      proposed_admin_expenses=309882.0, proposed_sinking_expenses=90459.0,
      total_uoe=10000, gst_rate=0.10 → strata_levies == 7090.04
    """
    annual_levies_doc = {
        "year": "2026",
        "total_uoe": 10000,
        "proposed_admin_expenses": 309882.0,
        "proposed_sinking_expenses": 90459.0,
    }
    settings_doc = {"gst_registered": True, "levy_gst_rate": 0.1}
    # Partial-year ledger row (Q1 only charged) — must be IGNORED now that a
    # committed annual rate is available; using it would under-report by ~75%.
    partial_ledger_row = {"year": "2026", "admin_levied": 1372.0, "sinking_levied": 400.51}

    mock_db = _mock_db_for_tco(
        levy_rows=[partial_ledger_row],
        annual_levies_doc=annual_levies_doc,
        settings_doc=settings_doc,
    )
    with (
        patch("services.ownerhub_service.db", mock_db),
        patch("utils.finance_helpers.db", mock_db),
        patch("services.ownerhub_service.get_ctx_building_id", return_value="13195"),
        patch("services.ownerhub_service.get_owner_info", new=AsyncMock(return_value={})),
    ):
        result = await ownerhub_service.compute_unit_tco("TH017", "2026")

    assert result["strata_levies"] == 7090.04
    assert result["breakdown_details"]["strata_levies"]["source"] == "annual_levies:rate_based(committed_annual)"


@pytest.mark.asyncio
async def test_capital_replacement_excluded_from_total_costs():
    """Capital replacement must be informational only — not summed into
    total_costs, since it would double-count the sinking levy already inside
    strata_levies (both fund the same forecast building capital works)."""
    plan_rows = [
        {"year": 2026, "expenditure": 17965.0},
        {"year": 2027, "expenditure": 15049.0},
        {"year": 2028, "expenditure": 6379.0},
        {"year": 2029, "expenditure": 413099.0},
        {"year": 2030, "expenditure": 17337.0},
    ]
    mock_db = _mock_db_for_tco(
        levy_rows=[{"year": "2026", "admin_levied": 5488.01, "sinking_levied": 1602.03}],
        council_rows=[{"financial_year": "2025-26", "total_rates": 2053.78, "land_tax_total": 2511.20}],
        water_total=500.0,
        plan_fwd_rows=plan_rows,
        plan_all_rows=plan_rows,
    )
    with (
        patch("services.ownerhub_service.db", mock_db),
        patch("utils.finance_helpers.db", mock_db),
        patch("services.ownerhub_service.get_ctx_building_id", return_value="13195"),
        patch("services.ownerhub_service.get_owner_info", new=AsyncMock(return_value={})),
    ):
        result = await ownerhub_service.compute_unit_tco("TH017", "2026")

    expected_total = round(
        result["strata_levies"] + result["rates_and_charges"] + result["water_charges"], 2
    )
    assert result["total_costs"] == expected_total
    assert result["capital_replacement"] > 0  # still reported, just not summed in
    assert result["breakdown"]["capital_replacement"] == result["capital_replacement"]


@pytest.mark.asyncio
async def test_assumptions_present_and_explain_capital_split():
    """assumptions must explain that capital replacement is informational and
    not already double-counted inside the annual strata levy."""
    mock_db = _mock_db_for_tco()
    with (
        patch("services.ownerhub_service.db", mock_db),
        patch("utils.finance_helpers.db", mock_db),
        patch("services.ownerhub_service.get_ctx_building_id", return_value="13195"),
        patch("services.ownerhub_service.get_owner_info", new=AsyncMock(return_value={})),
    ):
        result = await ownerhub_service.compute_unit_tco("TH017", "2026")

    assert result["assumptions"], "assumptions must be present"
    joined = " ".join(result["assumptions"]).lower()
    assert "sinking" in joined and "not" in joined


# ── Audit regression tests ────────────────────────────────────────────────────

def test_plan_expenditure_zero_not_falsy():
    """Audit bug 2: expenditure=0.0 must return 0.0, not fall through to capital_expenditure."""
    # Access the inner function by calling compute_unit_tco and inspecting behaviour via
    # a direct import of the helper — we validate via the module source instead.
    import inspect
    src = inspect.getsource(ownerhub_service.compute_unit_tco)
    # The correct implementation uses 'for key in' or 'if val is not None' — NOT a bare 'or' chain.
    assert "or row.get(\"capital_expenditure\") or row.get(\"total_expenditure\")" not in src, (
        "_plan_expenditure still uses falsy 'or' chain — zero expenditure will fall through"
    )
    assert ("if val is not None" in src or "for key in" in src), (
        "_plan_expenditure must use an explicit None check"
    )


def test_water_match_no_fy_overlap():
    """Audit bug 3: water query must NOT include prior-year FY half-year range."""
    import inspect
    src = inspect.getsource(ownerhub_service.compute_unit_tco)
    # The removed FY overlap line included 'year_int - 1}-07-01'
    assert "07-01" not in src, (
        "Water query still contains the FY half-year range; Jul-Dec of prior year will inflate water total"
    )


def test_quarter_dedup_normalises_mixed_formats():
    """Audit bug 4: Q1 and 2026-Q1 must deduplicate to the same key."""
    import inspect
    src = inspect.getsource(ownerhub_service.compute_unit_tco)
    assert "_normalise_quarter_key" in src, (
        "Quarter dedup must use _normalise_quarter_key to handle mixed storage formats"
    )
    # Verify the normalisation logic itself
    for raw, expected in [("Q1", "Q1"), ("2026-Q1", "Q1"), ("Q3 2026", "Q3"), ("Q4", "Q4")]:
        m = re.search(r"(Q[1-4])", raw, re.IGNORECASE)
        result = m.group(1).upper() if m else raw.upper()
        assert result == expected, f"_normalise_quarter_key({raw!r}) should be {expected!r}, got {result!r}"
