"""
Regression tests: every Mongo-fallback query in routers/external_api.py's
Building/Units and Finance endpoints must be scoped by building_id.

Root cause: these endpoints authenticate via API key (require_scope/
get_api_key_auth), which never calls set_ctx_building_id() — unlike the
JWT-based dependency chain used elsewhere in the app. `units`, `annual_levies`,
`unit_levy_ledger`, `levy_payments`, `levy_categories`, and
`financial_transactions` are all tenant-scoped collections
(backend/database.py TENANT_SCOPED_COLLECTIONS); TenantScopedDatabase raises
RuntimeError when no context is set and a query has no explicit building_id.
Before this fix, every one of these endpoints' Mongo-fallback branch queried
with no building_id at all — so they 500'd for every partner API caller,
rather than the "silent cross-tenant leak" a query without a building_id
filter would risk under different tenant-scoping conditions. These tests
assert the explicit building_id now present in every query, both to prove
the endpoints work again and to guard against the filter being silently
dropped in a future edit.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BUILDING_ID = "13195"
KEY_DOC = {"building_id": BUILDING_ID}


def _cursor(items):
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=items)
    cursor.skip.return_value = cursor
    cursor.limit.return_value = cursor
    return cursor


def _agg(items):
    agg = MagicMock()
    agg.to_list = AsyncMock(return_value=items)
    return agg


@pytest.mark.asyncio
async def test_list_units_scopes_query_by_building_id():
    from routers import external_api as ea

    with patch("routers.external_api.db") as mock_db:
        mock_db.units.count_documents = AsyncMock(return_value=0)
        mock_db.units.find.return_value = _cursor([])

        await ea.list_units(page=1, page_size=50, is_tenanted=None, key_doc=KEY_DOC)

    count_filter = mock_db.units.count_documents.call_args.args[0]
    find_filter = mock_db.units.find.call_args.args[0]
    assert count_filter["building_id"] == BUILDING_ID
    assert find_filter["building_id"] == BUILDING_ID


@pytest.mark.asyncio
async def test_get_unit_scopes_query_by_building_id():
    from routers import external_api as ea

    with patch("routers.external_api.db") as mock_db:
        mock_db.units.find_one = AsyncMock(return_value={"unit_number": "UA001"})

        await ea.get_unit(unit_number="UA001", key_doc=KEY_DOC)

    find_one_filter = mock_db.units.find_one.call_args.args[0]
    assert find_one_filter["building_id"] == BUILDING_ID
    assert find_one_filter["unit_number"] == "UA001"


def _levy_doc(year="2026", admin_income=100.0, sinking_income=50.0, admin_expense=0.0, sinking_expense=0.0):
    """Real annual_levies shape: `year` field, nested admin_fund/sinking_fund —
    NOT the flat `financial_year`/`admin_fund_income` shape this file's mocks used
    before the GAP-FIN-016 field-name bug fix (2026-07-21)."""
    return {
        "year": year,
        "admin_fund": {"levy_income": admin_income, "total_expenses": admin_expense},
        "sinking_fund": {"levy_income": sinking_income, "total_expenses": sinking_expense},
    }


@pytest.mark.asyncio
async def test_oc_summary_scopes_every_query_by_building_id():
    from routers import external_api as ea

    with (
        patch("routers.external_api.db") as mock_db,
        patch("routers.external_api.get_latest_levy_year", new=AsyncMock(return_value="2026")) as mock_latest,
        patch("routers.external_api.get_levy_fund_data", new=AsyncMock(return_value=_levy_doc())) as mock_fund,
        # get_oc_summary no longer aggregates unit_levy_ledger inline — it now
        # routes through the canonical get_building_arrears_summary() (GAP-FIN-040,
        # backend/routers/external_api.py:775-776). Mock at that boundary instead.
        patch(
            "utils.finance_helpers.get_building_arrears_summary",
            new=AsyncMock(return_value={"units_in_arrears": 0, "true_arrears_amount": 0.0}),
        ) as mock_arrears,
    ):
        mock_db.units.count_documents = AsyncMock(return_value=0)
        mock_db.levy_payments.aggregate.return_value = _agg([{"total": 30.0}])

        result = await ea.get_oc_summary(key_doc=KEY_DOC)

    # All three units.count_documents calls (total/tenanted/occupied) must be scoped.
    for call in mock_db.units.count_documents.call_args_list:
        filt = call.args[0]
        if "$and" in filt:
            # occupied query: building_id nested in $and alongside $or
            assert any(
                isinstance(c, dict) and c.get("building_id") == BUILDING_ID
                for c in filt["$and"]
            )
        else:
            assert filt["building_id"] == BUILDING_ID

    mock_latest.assert_awaited_once_with(BUILDING_ID)
    mock_fund.assert_awaited_once_with("2026", BUILDING_ID)

    payments_pipeline = mock_db.levy_payments.aggregate.call_args.args[0]
    assert payments_pipeline[0]["$match"]["building_id"] == BUILDING_ID
    assert payments_pipeline[0]["$match"]["year"] == "2026"

    # Arrears must be resolved through the canonical, building+year-scoped helper.
    mock_arrears.assert_awaited_once_with(BUILDING_ID, "2026")

    # Regression guard for the GAP-FIN-016 field-name bug: real annual_levies/
    # levy_payments field names must actually be read, not silently default to 0.
    assert result["total_levy_income_budgeted"] == 150.0  # 100.0 admin + 50.0 sinking
    assert result["total_levy_collected"] == 30.0
    assert result["collection_rate_pct"] == 20.0  # 30/150 * 100


@pytest.mark.asyncio
async def test_oc_levies_mongo_fallback_scopes_by_building_id():
    from routers import external_api as ea

    with (
        patch("routers.external_api.db") as mock_db,
        patch("routers.external_api.are_cutover_features_enabled", new=AsyncMock(return_value=False)),
        patch("routers.external_api.get_latest_levy_year", new=AsyncMock(return_value="2026")),
        patch("routers.external_api.get_levy_fund_data", new=AsyncMock(return_value=_levy_doc())),
    ):
        mock_db.levy_payments.aggregate.return_value = _agg([])

        result = await ea.get_oc_levies(financial_year=None, key_doc=KEY_DOC)

    payments_pipeline = mock_db.levy_payments.aggregate.call_args.args[0]
    assert payments_pipeline[0]["$match"]["building_id"] == BUILDING_ID
    assert payments_pipeline[0]["$match"]["year"] == "2026"
    assert result["financial_year"] == "2026"
    assert result["admin_fund_budgeted"] == 100.0
    assert result["sinking_fund_budgeted"] == 50.0


@pytest.mark.asyncio
async def test_finance_summary_mongo_fallback_scopes_all_queries_by_building_id():
    from routers import external_api as ea

    with (
        patch("routers.external_api.db") as mock_db,
        patch("routers.external_api.are_cutover_features_enabled", new=AsyncMock(return_value=False)),
        patch("routers.external_api.get_latest_levy_year", new=AsyncMock(return_value="2026")),
        patch(
            "routers.external_api.get_levy_fund_data",
            new=AsyncMock(return_value=_levy_doc(admin_expense=40.0, sinking_expense=10.0)),
        ),
        # get_finance_summary no longer aggregates unit_levy_ledger inline — it now
        # routes through the canonical get_building_arrears_summary() (GAP-FIN-040,
        # backend/routers/external_api.py:921-922). Mock at that boundary instead.
        patch(
            "utils.finance_helpers.get_building_arrears_summary",
            new=AsyncMock(return_value={"units_in_arrears": 0, "true_arrears_amount": 250.0}),
        ) as mock_arrears,
    ):
        mock_db.levy_payments.aggregate.return_value = _agg([])
        mock_db.financial_transactions.aggregate.return_value = _agg([])

        result = await ea.get_finance_summary(financial_year=None, key_doc=KEY_DOC)

    assert mock_db.levy_payments.aggregate.call_args.args[0][0]["$match"]["building_id"] == BUILDING_ID
    assert mock_db.financial_transactions.aggregate.call_args.args[0][0]["$match"]["building_id"] == BUILDING_ID
    mock_arrears.assert_awaited_once_with(BUILDING_ID, "2026")
    assert result["levy_arrears_total"] == 250.0
    assert result["total_income"] == 150.0  # 100.0 admin + 50.0 sinking
    assert result["total_expenses"] == 50.0  # 40.0 admin + 10.0 sinking


@pytest.mark.asyncio
async def test_get_budget_scopes_query_by_building_id():
    from routers import external_api as ea

    with (
        patch("routers.external_api.db") as mock_db,
        patch("routers.external_api.get_latest_levy_year", new=AsyncMock(return_value="2026")),
        patch("routers.external_api.get_levy_fund_data", new=AsyncMock(return_value=_levy_doc())),
    ):
        mock_db.levy_categories.find.return_value = _cursor([])

        await ea.get_budget(financial_year=None, fund_type="admin", key_doc=KEY_DOC)

    categories_filter = mock_db.levy_categories.find.call_args.args[0]
    assert categories_filter["building_id"] == BUILDING_ID
    assert categories_filter["year"] == "2026"
    # levy_categories stores the legacy long-form fund_type value.
    assert categories_filter["fund_type"] == "administrative"


@pytest.mark.asyncio
async def test_get_transactions_scopes_all_queries_by_building_id():
    from routers import external_api as ea

    with (
        patch("routers.external_api.db") as mock_db,
        patch("routers.external_api.get_latest_levy_year", new=AsyncMock(return_value="2026")),
    ):
        mock_db.levy_payments.count_documents = AsyncMock(return_value=0)
        mock_db.levy_payments.find.return_value = _cursor([])

        await ea.get_transactions(
            financial_year=None, unit_number=None, page=1, page_size=50, key_doc=KEY_DOC
        )

    count_filter = mock_db.levy_payments.count_documents.call_args.args[0]
    find_filter = mock_db.levy_payments.find.call_args.args[0]
    assert count_filter["building_id"] == BUILDING_ID
    assert count_filter["year"] == "2026"
    assert find_filter["building_id"] == BUILDING_ID
    assert find_filter["year"] == "2026"


@pytest.mark.asyncio
async def test_unit_levy_balance_mongo_fallback_scopes_by_building_id():
    from routers import external_api as ea

    with (
        patch("routers.external_api.db") as mock_db,
        patch("routers.external_api.are_cutover_features_enabled", new=AsyncMock(return_value=False)),
        patch("routers.external_api.get_latest_levy_year", new=AsyncMock(return_value="2026")),
    ):
        mock_db.unit_levy_ledger.find_one = AsyncMock(return_value={
            "total_opening": 0.0, "total_levied": 100.0, "total_paid": 40.0, "total_closing": 60.0,
        })

        result = await ea.get_unit_levy_balance(unit_number="UA001", financial_year=None, key_doc=KEY_DOC)

    ledger_filter = mock_db.unit_levy_ledger.find_one.call_args.args[0]
    assert ledger_filter["building_id"] == BUILDING_ID
    assert ledger_filter["unit_number"] == "UA001"
    assert ledger_filter["year"] == "2026"
    # Regression guard: real unit_levy_ledger field names (total_opening/total_levied/
    # total_paid/total_closing) must actually populate the response, not default to 0.
    assert result["levied_amount"] == 100.0
    assert result["paid_amount"] == 40.0
    assert result["closing_balance"] == 60.0


# ── Shared helper unit tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_annual_levy_resolves_year_and_delegates_to_canonical_helpers():
    from routers import external_api as ea

    with (
        patch("routers.external_api.get_latest_levy_year", new=AsyncMock(return_value="2025")) as mock_latest,
        patch("routers.external_api.get_levy_fund_data", new=AsyncMock(return_value=_levy_doc(year="2025"))) as mock_fund,
    ):
        result = await ea._get_annual_levy(BUILDING_ID, "2026")
        mock_fund.assert_awaited_with("2026", BUILDING_ID)
        mock_latest.assert_not_called()
        assert result["financial_year"] == "2026"

        mock_fund.reset_mock()
        result = await ea._get_annual_levy(BUILDING_ID, None)
        mock_latest.assert_awaited_once_with(BUILDING_ID)
        mock_fund.assert_awaited_with("2025", BUILDING_ID)
        assert result["financial_year"] == "2025"


@pytest.mark.asyncio
async def test_get_annual_levy_returns_none_when_no_years_exist():
    from routers import external_api as ea

    with patch("routers.external_api.get_latest_levy_year", new=AsyncMock(return_value=None)):
        assert await ea._get_annual_levy(BUILDING_ID, None) is None


@pytest.mark.asyncio
async def test_sum_levy_payments_and_actual_expenses_scope_by_building_id():
    from routers import external_api as ea

    with patch("routers.external_api.db") as mock_db:
        mock_db.levy_payments.aggregate.return_value = _agg([])
        mock_db.financial_transactions.aggregate.return_value = _agg([])

        await ea._sum_levy_payments(BUILDING_ID, "2026")
        await ea._sum_actual_expenses(BUILDING_ID, "2026")

    payments_match = mock_db.levy_payments.aggregate.call_args.args[0][0]["$match"]
    expenses_match = mock_db.financial_transactions.aggregate.call_args.args[0][0]["$match"]
    assert payments_match["building_id"] == BUILDING_ID
    assert payments_match["year"] == "2026"
    # financial_transactions genuinely stores `financial_year` (unlike the other
    # 4 collections this bug touched) — confirmed live 2026-07-21, do not "fix" this one.
    assert expenses_match["building_id"] == BUILDING_ID
    assert expenses_match["financial_year"] == "2026"


# ── Cross-tenant isolation ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_different_api_keys_query_different_buildings():
    """Two API keys bound to different buildings must never share a query filter."""
    from routers import external_api as ea

    other_building = "16244"
    with patch("routers.external_api.db") as mock_db:
        mock_db.units.count_documents = AsyncMock(return_value=0)
        mock_db.units.find.return_value = _cursor([])

        await ea.list_units(page=1, page_size=50, is_tenanted=None, key_doc=KEY_DOC)
        first_call_filter = mock_db.units.find.call_args.args[0]

        await ea.list_units(
            page=1, page_size=50, is_tenanted=None, key_doc={"building_id": other_building}
        )
        second_call_filter = mock_db.units.find.call_args.args[0]

    assert first_call_filter["building_id"] == BUILDING_ID
    assert second_call_filter["building_id"] == other_building
    assert first_call_filter["building_id"] != second_call_filter["building_id"]
