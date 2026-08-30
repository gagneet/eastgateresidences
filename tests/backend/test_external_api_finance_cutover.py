from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_get_finance_summary_uses_postgres_read_service_when_cutover_enabled():
    from routers import external_api

    pg_response = {
        "financial_year": "2025-2026",
        "admin_fund_budget": 1000.0,
        "admin_fund_actual": 800.0,
        "sinking_fund_budget": 500.0,
        "sinking_fund_actual": 400.0,
        "total_income": 1500.0,
        "total_expenses": 300.0,
        "net_position": 900.0,
        "levy_arrears_total": 200.0,
    }

    with (
        patch(
            "routers.external_api.resolve_building_id_for_key",
            new=AsyncMock(return_value="13195"),
        ),
        patch(
            "routers.external_api.are_cutover_features_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch.object(
            external_api._financial_read_service,
            "get_finance_summary",
            new=AsyncMock(return_value=pg_response),
        ) as mock_pg,
    ):
        result = await external_api.get_finance_summary(
            financial_year="2025-2026",
            key_doc={"id": "key-1"},
        )

    mock_pg.assert_awaited_once_with(
        building_id="13195",
        financial_year="2025-2026",
    )
    assert result == pg_response


@pytest.mark.asyncio
async def test_get_finance_summary_falls_back_to_mongo_when_cutover_disabled():
    from routers import external_api

    # Real annual_levies shape: nested admin_fund/sinking_fund (GAP-FIN-016
    # field-name bug fix, 2026-07-21) — _get_annual_levy is mocked directly here,
    # so this must match what the real (now-fixed) helper actually returns.
    levy_doc = {
        "financial_year": "2025-2026",
        "admin_fund": {"levy_income": 1000.0, "total_expenses": 200.0},
        "sinking_fund": {"levy_income": 500.0, "total_expenses": 100.0},
    }

    with (
        patch(
            "routers.external_api.resolve_building_id_for_key",
            new=AsyncMock(return_value="13195"),
        ),
        patch(
            "routers.external_api.are_cutover_features_enabled",
            new=AsyncMock(return_value=False),
        ),
        patch("routers.external_api._get_annual_levy", new=AsyncMock(return_value=levy_doc)),
        patch("routers.external_api._sum_levy_payments", new=AsyncMock(return_value=750.0)),
        patch("routers.external_api._sum_actual_expenses", new=AsyncMock(return_value=300.0)),
        # get_finance_summary's Mongo-fallback branch no longer aggregates
        # unit_levy_ledger inline — it routes through the canonical
        # get_building_arrears_summary() (GAP-FIN-040, external_api.py:921-922).
        patch(
            "utils.finance_helpers.get_building_arrears_summary",
            new=AsyncMock(return_value={"units_in_arrears": 1, "true_arrears_amount": 250.0}),
        ),
    ):
        result = await external_api.get_finance_summary(
            financial_year="2025-2026",
            key_doc={"id": "key-1"},
        )

    assert result["financial_year"] == "2025-2026"
    assert result["levy_arrears_total"] == 250.0


@pytest.mark.asyncio
async def test_get_unit_levy_balance_raises_404_when_postgres_has_no_row():
    from routers import external_api

    with (
        patch(
            "routers.external_api.resolve_building_id_for_key",
            new=AsyncMock(return_value="13195"),
        ),
        patch(
            "routers.external_api.are_cutover_features_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch.object(
            external_api._financial_read_service,
            "get_unit_levy_balance",
            new=AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await external_api.get_unit_levy_balance(
                unit_number="TH017",
                financial_year="2025-2026",
                key_doc={"id": "key-1"},
            )

    assert exc_info.value.status_code == 404
