from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _cursor(items):
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=items)
    cursor.sort.return_value = cursor
    cursor.__aiter__.return_value = iter(items)
    return cursor


def test_parse_levy_gst_settings_supports_percent_inputs_and_toggle():
    from services.gst_service import parse_levy_gst_settings

    enabled = parse_levy_gst_settings({"gst_registered": True, "levy_gst_rate": 15})
    disabled = parse_levy_gst_settings({"gst_registered": False, "levy_gst_rate": 0.15})

    assert enabled["levy_gst_rate"] == pytest.approx(0.15)
    assert enabled["effective_gst_rate"] == pytest.approx(0.15)
    assert enabled["gst_label"] == "GST (15%)"
    assert disabled["effective_gst_rate"] == pytest.approx(0.0)
    assert disabled["gst_multiplier"] == pytest.approx(1.0)


def test_levy_rate_breakdown_prefers_proposed_budget_over_ytd_levy_income():
    from utils.finance_helpers import get_levy_rate_breakdown

    levy_doc = {
        "year": "2026",
        "building_id": "13195",
        "total_uoe": 100,
        "proposed_admin_expenses": 100.0,
        "proposed_sinking_expenses": 50.0,
        "admin_fund": {"levy_income": 120.0},
        "sinking_fund": {"levy_income": 80.0},
    }

    rates = get_levy_rate_breakdown(
        levy_doc,
        settings_doc={"gst_registered": True, "levy_gst_rate": 0.10},
    )

    assert rates["admin_ex_gst_annual"] == pytest.approx(1.0)
    assert rates["sinking_ex_gst_annual"] == pytest.approx(0.5)
    assert rates["total_payable_annual"] == pytest.approx(1.65)


def test_levy_rate_breakdown_falls_back_to_levy_income_when_proposed_missing():
    from utils.finance_helpers import get_levy_rate_breakdown

    levy_doc = {
        "year": "2025",
        "building_id": "13195",
        "total_uoe": 100,
        "admin_fund": {"levy_income": 120.0},
        "sinking_fund": {"levy_income": 80.0},
    }

    rates = get_levy_rate_breakdown(
        levy_doc,
        settings_doc={"gst_registered": True, "levy_gst_rate": 0.10},
    )

    assert rates["admin_ex_gst_annual"] == pytest.approx(1.2)
    assert rates["sinking_ex_gst_annual"] == pytest.approx(0.8)
    assert rates["total_payable_annual"] == pytest.approx(2.2)


def test_levy_rate_breakdown_falls_back_to_levy_income_when_proposed_zero():
    from utils.finance_helpers import get_levy_rate_breakdown

    levy_doc = {
        "year": "2025",
        "building_id": "13195",
        "total_uoe": 100,
        "proposed_admin_expenses": 0.0,
        "proposed_sinking_expenses": 0.0,
        "admin_fund": {"levy_income": 120.0},
        "sinking_fund": {"levy_income": 80.0},
    }

    rates = get_levy_rate_breakdown(
        levy_doc,
        settings_doc={"gst_registered": True, "levy_gst_rate": 0.10},
    )

    assert rates["admin_ex_gst_annual"] == pytest.approx(1.2)
    assert rates["sinking_ex_gst_annual"] == pytest.approx(0.8)
    assert rates["total_payable_annual"] == pytest.approx(2.2)


@pytest.mark.asyncio
async def test_get_levy_rates_derives_payable_amounts_from_ex_gst_fund_totals():
    from utils.finance_helpers import get_levy_rates

    levy_doc = {
        "year": "2026",
        "building_id": "13195",
        "total_uoe": 10000,
        "proposed_admin_expenses": 309882.00,
        "proposed_sinking_expenses": 90459.00,
        # Intentionally wrong legacy/raw values: payable rates must be derived
        # from the ex-GST fund totals above, not trusted from these fields.
        "admin_levy_per_uoe_annual": 999.0,
        "sinking_levy_per_uoe_annual": 888.0,
    }
    settings_doc = {"gst_registered": True, "levy_gst_rate": 0.10}

    with patch("utils.finance_helpers.db") as mock_db:
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
        mock_db.settings.find_one = AsyncMock(return_value=settings_doc)
        mock_db.buildings.find_one = AsyncMock(return_value={})

        rates = await get_levy_rates("2026", "13195")

    assert rates["admin_annual"] == pytest.approx(34.08702)
    assert rates["sinking_annual"] == pytest.approx(9.95049)
    assert rates["admin_quarterly"] == pytest.approx(8.521755)
    assert rates["sinking_quarterly"] == pytest.approx(2.4876225)
    assert rates["admin_annual_ex_gst"] == pytest.approx(30.9882)
    assert rates["sinking_annual_ex_gst"] == pytest.approx(9.0459)


@pytest.mark.asyncio
async def test_quarterly_budget_uses_building_gst_settings():
    from routers import finance

    current_user = {"id": "admin-1", "role": "super_admin"}
    settings_doc = {
        "financial_year_start_month": 1,
        "levy_due_months": [3, 6, 9, 12],
        "levy_due_day_type": "first",
        "levy_collection_frequency": "quarterly",
        "gst_registered": True,
        "levy_gst_rate": 0.15,
    }
    levy_doc = {
        "year": "2026",
        "building_id": "13195",
        "proposed_admin_expenses": 100.0,
        "proposed_sinking_expenses": 50.0,
    }

    with (
        patch("routers.finance.db") as mock_db,
        patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings_doc)),
        patch("routers.finance.get_user_permissions", return_value=SimpleNamespace(can_view_finances=True)),
    ):
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
        mock_db.levy_categories.find.return_value = _cursor([])
        mock_db.unit_levy_ledger.find.return_value = _cursor([])
        mock_db.levy_payments.aggregate.return_value = _cursor([])

        result = await finance.get_quarterly_budget(
            year="2026",
            current_user=current_user,
            building_id="13195",
        )

    assert result["gst_registered"] is True
    assert result["gst_rate"] == pytest.approx(0.15)
    assert result["effective_gst_rate"] == pytest.approx(0.15)
    assert result["gst_label"] == "GST (15%)"
    assert result["annual_totals"]["gst"] == pytest.approx(22.5)
    assert result["annual_totals"]["total_inc_gst"] == pytest.approx(172.5)
    assert result["quarters"][0]["budgeted_gst"] == pytest.approx(5.62)
    assert result["quarters"][0]["budgeted_income_inc_gst"] == pytest.approx(43.12)


@pytest.mark.asyncio
async def test_quarterly_budget_prefers_proposed_budget_over_ytd_levy_income():
    from routers import finance

    current_user = {"id": "admin-1", "role": "super_admin"}
    settings_doc = {
        "financial_year_start_month": 1,
        "levy_due_months": [3, 6, 9, 12],
        "levy_due_day_type": "first",
        "levy_collection_frequency": "quarterly",
        "gst_registered": True,
        "levy_gst_rate": 0.10,
    }
    levy_doc = {
        "year": "2026",
        "building_id": "13195",
        "proposed_admin_expenses": 100.0,
        "proposed_sinking_expenses": 50.0,
        "admin_fund": {"levy_income": 120.0},
        "sinking_fund": {"levy_income": 80.0},
    }

    with (
        patch("routers.finance.db") as mock_db,
        patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings_doc)),
        patch("routers.finance.get_user_permissions", return_value=SimpleNamespace(can_view_finances=True)),
    ):
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
        mock_db.levy_categories.find.return_value = _cursor([])
        mock_db.unit_levy_ledger.find.return_value = _cursor([])
        mock_db.levy_payments.aggregate.return_value = _cursor([])

        result = await finance.get_quarterly_budget(
            year="2026",
            current_user=current_user,
            building_id="13195",
        )

    assert result["annual_totals"]["admin_ex_gst"] == pytest.approx(100.0)
    assert result["annual_totals"]["sinking_ex_gst"] == pytest.approx(50.0)
    assert result["annual_totals"]["total_inc_gst"] == pytest.approx(165.0)
    assert result["quarters"][0]["budgeted_income_ex_gst"] == pytest.approx(37.5)
    assert result["quarters"][0]["budgeted_income_inc_gst"] == pytest.approx(41.25)


@pytest.mark.asyncio
async def test_quarterly_budget_marks_overdue_when_past_due_and_unpaid():
    """A quarter whose due date has passed AND still has an unpaid balance must be
    "overdue", not merely "past" — "past" alone was indistinguishable from a fully
    paid, closed-out quarter and hid genuine arrears from the Financial Overview
    charts (users saw a neutral badge instead of a signal that collection is needed).
    """
    from routers import finance

    current_user = {"id": "admin-1", "role": "super_admin"}
    settings_doc = {
        "financial_year_start_month": 1,
        "levy_due_months": [3, 6, 9, 12],
        "levy_due_day_type": "first",
        "levy_collection_frequency": "quarterly",
        "gst_registered": False,
        "levy_gst_rate": 0.0,
    }
    levy_doc = {
        "year": "2026",
        "building_id": "13195",
        "proposed_admin_expenses": 100.0,
        "proposed_sinking_expenses": 50.0,
    }
    # Real unit_levy_ledger documents carry annual totals only -- there is no
    # per-quarter quarters_charged field in production (confirmed live 2026-08-01:
    # nothing in the live application ever writes it). unit_levy_ledger.total_levied
    # is YTD CHARGED-TO-DATE, not the full annual committed levy (CLAUDE.md /
    # GAP-FIN-033 Part B1 -- the "halved!" false alarm) -- get_quarterly_budget's
    # Mongo-fallback waterfall (routers/finance.py schedule_for_waterfall) correctly
    # splits it only across quarters whose due date has already started, NOT across
    # all 4 configured quarters (splitting across all 4 would halve Q1/Q2's real
    # amounts mid-year). With 2 due quarters (Q1 2026-03-01, Q2 2026-06-01, both
    # before "now") and $80 already charged for those two, each quarter's split is
    # $40, not $20. net_balance = 80 - 65 = 15 means $65 of the $80 due so far has
    # been paid, which waterfalls as Q1 ($40) fully covered and Q2 ($40) covered
    # $25 of the way, leaving $15 outstanding.
    ledger_entries = [
        {"total_levied": 80.0, "net_balance": 15.0},
    ]

    with (
        patch("routers.finance.db") as mock_db,
        patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings_doc)),
        patch("routers.finance.get_user_permissions", return_value=SimpleNamespace(can_view_finances=True)),
    ):
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
        mock_db.levy_categories.find.return_value = _cursor([])
        mock_db.unit_levy_ledger.find.return_value = _cursor(ledger_entries)
        mock_db.levy_payments.aggregate.return_value = _cursor([])

        result = await finance.get_quarterly_budget(
            year="2026",
            current_user=current_user,
            building_id="13195",
        )

    q1, q2 = result["quarters"][0], result["quarters"][1]
    assert q1["outstanding"] == pytest.approx(0.0)
    assert q1["status"] == "past"
    assert q2["outstanding"] == pytest.approx(15.0)
    assert q2["status"] == "overdue"
    # Both quarters draw from the same real year -- has_ledger_data must be True
    # for both, not dependent on the now-nonexistent quarters_charged field.
    assert q1["has_ledger_data"] is True
    assert q2["has_ledger_data"] is True


@pytest.mark.asyncio
async def test_quarterly_budget_has_ledger_data_false_when_year_has_no_real_ledger_data():
    """Regression for the 2026-08-01 finding: unit_levy_ledger.quarters_charged is
    never set by any live code path (confirmed against 522 real East Gate
    documents spanning 6 years) -- grouping by it made has_ledger_data permanently
    False for every quarter, on every building. The fix must key off whether the
    year has any real levied amount at all, not off that dead field."""
    from routers import finance

    current_user = {"id": "admin-1", "role": "super_admin"}
    settings_doc = {
        "financial_year_start_month": 1,
        "levy_due_months": [3, 6, 9, 12],
        "levy_due_day_type": "first",
        "levy_collection_frequency": "quarterly",
        "gst_registered": False,
        "levy_gst_rate": 0.0,
    }
    levy_doc = {"year": "2026", "building_id": "13195"}

    with (
        patch("routers.finance.db") as mock_db,
        patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings_doc)),
        patch("routers.finance.get_user_permissions", return_value=SimpleNamespace(can_view_finances=True)),
    ):
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
        mock_db.levy_categories.find.return_value = _cursor([])
        mock_db.unit_levy_ledger.find.return_value = _cursor([])  # no real ledger rows for this year
        mock_db.levy_payments.aggregate.return_value = _cursor([])

        result = await finance.get_quarterly_budget(
            year="2026", current_user=current_user, building_id="13195",
        )

    assert all(q["has_ledger_data"] is False for q in result["quarters"])


@pytest.mark.asyncio
async def test_quarterly_budget_collected_ytd_uses_net_balance_not_raw_total_paid():
    """Regression: annual_totals.total_collected_ytd summed raw unit_levy_ledger
    total_paid directly, which can be a back-solved, cumulative-since-inception
    figure rather than one scoped to this year (same root cause as the 2026-08-01
    paid_this_year fix). total_levied - net_balance is the correctly year-scoped
    figure and must be what this endpoint reports as "collected"."""
    from routers import finance

    current_user = {"id": "admin-1", "role": "super_admin"}
    settings_doc = {
        "financial_year_start_month": 1,
        "levy_due_months": [3, 6, 9, 12],
        "levy_due_day_type": "first",
        "levy_collection_frequency": "quarterly",
        "gst_registered": False,
        "levy_gst_rate": 0.0,
    }
    levy_doc = {"year": "2026", "building_id": "13195"}
    # Contaminated total_paid (many years' cumulative) vs the real net_balance
    # (only $30 of this year's $100 remains unpaid -> $70 really collected this year).
    ledger_entries = [
        {"total_levied": 100.0, "total_paid": 5000.0, "net_balance": 30.0},
    ]

    with (
        patch("routers.finance.db") as mock_db,
        patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings_doc)),
        patch("routers.finance.get_user_permissions", return_value=SimpleNamespace(can_view_finances=True)),
    ):
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
        mock_db.levy_categories.find.return_value = _cursor([])
        mock_db.unit_levy_ledger.find.return_value = _cursor(ledger_entries)
        mock_db.levy_payments.aggregate.return_value = _cursor([])

        result = await finance.get_quarterly_budget(
            year="2026", current_user=current_user, building_id="13195",
        )

    assert result["annual_totals"]["total_collected_ytd"] == pytest.approx(70.0)
    assert result["annual_totals"]["total_outstanding_ytd"] == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_budget_vs_actual_uses_financial_transactions_for_ocr_confirmed_invoices():
    from routers import finance

    current_user = {"id": "admin-1", "role": "super_admin"}
    levy_categories = [{
        "id": "cat-admin-maint",
        "name": "General Maintenance",
        "year": "2026",
        "fund_type": "administrative",
        "budgeted_amount": 1200.0,
        # Legacy denormalized value intentionally stale
        "actual_amount": 0.0,
    }]
    tx_rollup = [{
        "_id": {
            "fund_type": "admin",
            "category_id": None,
            "category_name": "General Maintenance",
        },
        "total": 450.0,
    }]

    with (
        patch("routers.finance.db") as mock_db,
        patch("routers.finance.get_user_permissions", return_value=SimpleNamespace(can_view_finances=True)),
    ):
        mock_db.levy_categories.find.return_value = _cursor(levy_categories)
        mock_db.financial_transactions.aggregate.return_value = _cursor(tx_rollup)

        result = await finance.get_budget_vs_actual(
            year="2026",
            current_user=current_user,
            building_id="13195",
        )

    assert result["administrative"][0]["budget"] == pytest.approx(1200.0)
    assert result["administrative"][0]["actual"] == pytest.approx(450.0)


@pytest.mark.asyncio
async def test_quarterly_budget_uses_financial_transactions_actuals_for_expense_totals():
    from routers import finance

    current_user = {"id": "admin-1", "role": "super_admin"}
    settings_doc = {
        "financial_year_start_month": 1,
        "levy_due_months": [3, 6, 9, 12],
        "levy_due_day_type": "first",
        "levy_collection_frequency": "quarterly",
        "gst_registered": True,
        "levy_gst_rate": 0.10,
    }
    levy_doc = {
        "year": "2026",
        "building_id": "13195",
        "proposed_admin_expenses": 100.0,
        "proposed_sinking_expenses": 50.0,
    }
    cats = [
        {
            "id": "cat-admin-maint",
            "name": "General Maintenance",
            "year": "2026",
            "fund_type": "administrative",
            "budgeted_amount": 200.0,
            "actual_amount": 0.0,
        },
        {
            "id": "cat-sink-roof",
            "name": "Roof Works",
            "year": "2026",
            "fund_type": "sinking",
            "budgeted_amount": 100.0,
            "actual_amount": 0.0,
        },
    ]
    tx_rollup = [
        {
            "_id": {
                "fund_type": "admin",
                "category_id": None,
                "category_name": "General Maintenance",
            },
            "total": 300.0,
        },
        {
            "_id": {
                "fund_type": "sinking",
                "category_id": None,
                "category_name": "Roof Works",
            },
            "total": 200.0,
        },
    ]

    with (
        patch("routers.finance.db") as mock_db,
        patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings_doc)),
        patch("routers.finance.get_user_permissions", return_value=SimpleNamespace(can_view_finances=True)),
    ):
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
        mock_db.levy_categories.find.return_value = _cursor(cats)
        mock_db.unit_levy_ledger.find.return_value = _cursor([])
        mock_db.levy_payments.aggregate.return_value = _cursor([])
        mock_db.financial_transactions.aggregate.return_value = _cursor(tx_rollup)

        result = await finance.get_quarterly_budget(
            year="2026",
            current_user=current_user,
            building_id="13195",
        )

    assert result["annual_totals"]["total_expenses_budgeted"] == pytest.approx(300.0)
    assert result["annual_totals"]["total_expenses_actual"] == pytest.approx(500.0)
    assert result["quarters"][0]["actual_expenses"] == pytest.approx(125.0)


@pytest.mark.asyncio
async def test_levy_calculator_uses_ex_gst_budgets_not_ambiguous_stored_uoe_rates():
    from routers import finance

    current_user = {"id": "admin-1", "role": "super_admin"}
    settings_doc = {
        "financial_year_start_month": 1,
        "levy_due_months": [3, 6, 9, 12],
        "levy_due_day_type": "first",
        "levy_collection_frequency": "quarterly",
        "gst_registered": True,
        "levy_gst_rate": 0.15,
    }
    levy_doc = {
        "year": "2026",
        "building_id": "13195",
        "status": "draft",
        "total_uoe": 100,
        "proposed_admin_expenses": 100.0,
        "proposed_sinking_expenses": 50.0,
        # Deliberately inconsistent legacy values. The calculator must derive
        # rates from the ex-GST fund totals instead of trusting these fields.
        "admin_levy_per_uoe_annual": 9.0,
        "sinking_levy_per_uoe_annual": 9.0,
    }
    units = [{
        "unit_number": "1",
        "unit_type": "Apartment",
        "entitlement": 100,
        "scheme_class": None,
    }]

    with (
        patch("routers.finance.db") as mock_db,
        patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings_doc)),
        patch("routers.finance.get_user_permissions", return_value=SimpleNamespace(can_view_finances=True)),
    ):
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
        mock_db.units.find.return_value = _cursor(units)
        mock_db.scheme_classes.find.return_value = _cursor([])
        mock_db.buildings.find_one = AsyncMock(return_value={})

        result = await finance.calculate_levies(
            year="2026",
            current_user=current_user,
            building_id="13195",
        )

    assert result["admin_per_uoe_annual"] == pytest.approx(1.0)
    assert result["sinking_per_uoe_annual"] == pytest.approx(0.5)
    assert result["admin_per_uoe_payable_annual"] == pytest.approx(1.15)
    assert result["sinking_per_uoe_payable_annual"] == pytest.approx(0.575)
    assert result["total_per_uoe_payable_annual"] == pytest.approx(1.725)
    assert result["levies"][0]["admin_annual"] == pytest.approx(115.0)
    assert result["levies"][0]["sinking_annual"] == pytest.approx(57.5)
    assert result["levies"][0]["total_annual"] == pytest.approx(172.5)


@pytest.mark.asyncio
async def test_levy_kpi_uses_payable_quarter_target_not_ex_gst_budget_denominator():
    from routers import finance

    current_user = {"id": "admin-1", "role": "super_admin"}
    settings_doc = {
        "gst_registered": True,
        "levy_gst_rate": 0.15,
    }
    levy_doc = {
        "year": "2026",
        "building_id": "13195",
        "total_uoe": 100,
        "proposed_admin_expenses": 100.0,
        "proposed_sinking_expenses": 50.0,
    }
    ledger_docs = [{
        "unit_number": "1",
        "lot_number": "1",
        "uoe": 100,
        "net_balance": 43.12,
        "total_paid": 0.0,
        "admin_paid": 0.0,
        "sinking_paid": 0.0,
        "admin_opening": 0.0,
        "sinking_opening": 0.0,
    }]

    with (
        patch("routers.finance.db") as mock_db,
        patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings_doc)),
        patch("routers.finance.get_user_permissions", return_value=SimpleNamespace(can_view_finances=True)),
    ):
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
        mock_db.buildings.find_one = AsyncMock(return_value={})
        mock_db.unit_levy_ledger.find.return_value = _cursor(ledger_docs)
        mock_db.expense_transactions.aggregate.return_value.to_list = AsyncMock(return_value=[])

        result = await finance.get_levy_kpi(
            year="2026",
            current_user=current_user,
            building_id="13195",
        )

    assert result["quarter_billed_total_display"] == pytest.approx(43.12)
    assert result["current_quarter_collected_total"] == pytest.approx(0.0)
    assert result["collection_rate"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_resolve_category_actual_normalises_fund_type_from_category_doc():
    """
    Regression: fund_type in category_doc may be an unnormalized alias (e.g. "admin")
    while dict keys in tx_actual_by_name are normalized via legacy_fund_type(normalise_fund_type(...))
    to "administrative".  Without normalization the name-fallback lookup silently fails and
    returns the stale actual_amount=0.0 instead of the live transaction total.
    """
    from routers.finance import _resolve_category_actual_amount

    # Simulates a category document that stores fund_type as the short alias "admin".
    category_doc = {
        "id": "",           # no category_id → forces name-match path
        "name": "General Maintenance",
        "fund_type": "admin",   # unnormalized alias
        "actual_amount": 0.0,   # stale legacy value
    }
    # Keys in tx_actual_by_name are always in legacy form ("administrative" or "sinking")
    # because _get_actual_overrides_from_financial_transactions normalizes them.
    tx_actual_by_name: dict = {("administrative", "general maintenance"): 450.0}

    result = _resolve_category_actual_amount(category_doc, {}, tx_actual_by_name)

    assert result == pytest.approx(450.0), (
        "Expected the normalized 'admin' → 'administrative' lookup to match and return 450.0"
    )


@pytest.mark.asyncio
async def test_resolve_category_actual_normalises_fund_type_for_id_match():
    """
    Same normalization regression for the category-id match path.
    """
    from routers.finance import _resolve_category_actual_amount

    category_doc = {
        "id": "cat-admin-1",
        "name": "General Maintenance",
        "fund_type": "admin",   # unnormalized alias
        "actual_amount": 0.0,
    }
    tx_actual_by_id: dict = {("administrative", "cat-admin-1"): 750.0}

    result = _resolve_category_actual_amount(category_doc, tx_actual_by_id, {})

    assert result == pytest.approx(750.0), (
        "Expected the normalized 'admin' → 'administrative' id-lookup to return 750.0"
    )


@pytest.mark.asyncio
async def test_get_actual_overrides_fails_closed_on_aggregation_error():
    """
    _get_actual_overrides_from_financial_transactions must return ({}, {}) and log a warning
    rather than propagating an exception when the DB aggregation fails.
    """
    from routers import finance  # ensure the module is imported before patching
    with patch("routers.finance.db") as mock_db:
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(side_effect=RuntimeError("DB unavailable"))
        mock_db.financial_transactions.aggregate.return_value = mock_cursor

        by_id, by_name = await finance._get_actual_overrides_from_financial_transactions(
            year="2026",
            building_id="13195",
        )

    assert by_id == {}
    assert by_name == {}
