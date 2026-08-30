from unittest.mock import AsyncMock, patch

import pytest


def _make_context(capital_required: float):
    return {
        "units": [
            {"unit_number": "UA001", "entitlement": 50},
            {"unit_number": "UA002", "entitlement": 50},
        ],
        "capital_schedule_by_year": {2027: capital_required},
        "allocation_shares_by_year": {2027: {"UA001": 0.5, "UA002": 0.5}},
        "group_units": {"All Lots": ["UA001", "UA002"]},
    }


@pytest.mark.asyncio
async def test_lss_fully_funded_scheme_high_score():
    from services.levy_stability_service import compute_levy_stability_snapshot

    special_levy = {
        "probability": 3,
        "per_unit_distribution": [100, 100, 100, 100],
        "group_summary": [{"group": "All Lots", "probability": 3}],
    }
    with patch("services.levy_stability_service.compute_special_levy_forecast", new_callable=AsyncMock,
               return_value=special_levy), \
            patch("services.levy_stability_service.get_capital_allocation_context", new_callable=AsyncMock,
                  return_value=_make_context(200000)), \
            patch("services.levy_stability_service._get_financial_baseline", new_callable=AsyncMock, return_value={
                "reserve_balance": 320000,
                "sinking_levy_income": 120000,
            }), \
            patch("services.levy_stability_service.get_intelligence_settings", new_callable=AsyncMock,
                  return_value={"inflation_rate": 0.03}), \
            patch("services.levy_stability_service.db") as mock_db:
        mock_db.levy_stability_snapshots.update_one = AsyncMock()

        result = await compute_levy_stability_snapshot("13195", force=True)

    assert result["levy_stability_score"] > 90


@pytest.mark.asyncio
async def test_lss_moderately_funded_scheme_mid_score():
    from services.levy_stability_service import compute_levy_stability_snapshot

    special_levy = {
        "probability": 15,
        "per_unit_distribution": [80, 120, 110, 90],
        "group_summary": [{"group": "All Lots", "probability": 15}],
    }
    with patch("services.levy_stability_service.compute_special_levy_forecast", new_callable=AsyncMock,
               return_value=special_levy), \
            patch("services.levy_stability_service.get_capital_allocation_context", new_callable=AsyncMock,
                  return_value=_make_context(200000)), \
            patch("services.levy_stability_service._get_financial_baseline", new_callable=AsyncMock, return_value={
                "reserve_balance": 210000,
                "sinking_levy_income": 70000,
            }), \
            patch("services.levy_stability_service.get_intelligence_settings", new_callable=AsyncMock,
                  return_value={"inflation_rate": 0.03}), \
            patch("services.levy_stability_service.db") as mock_db:
        mock_db.levy_stability_snapshots.update_one = AsyncMock()

        result = await compute_levy_stability_snapshot("13195", force=True)

    assert 70 <= result["levy_stability_score"] <= 85


@pytest.mark.asyncio
async def test_lss_underfunded_scheme_low_score():
    from services.levy_stability_service import compute_levy_stability_snapshot

    special_levy = {
        "probability": 50,
        "per_unit_distribution": [0, 5000, 15000, 20000],
        "group_summary": [{"group": "All Lots", "probability": 50}],
    }
    with patch("services.levy_stability_service.compute_special_levy_forecast", new_callable=AsyncMock,
               return_value=special_levy), \
            patch("services.levy_stability_service.get_capital_allocation_context", new_callable=AsyncMock,
                  return_value=_make_context(200000)), \
            patch("services.levy_stability_service._get_financial_baseline", new_callable=AsyncMock, return_value={
                "reserve_balance": 50000,
                "sinking_levy_income": 20000,
            }), \
            patch("services.levy_stability_service.get_intelligence_settings", new_callable=AsyncMock,
                  return_value={"inflation_rate": 0.03}), \
            patch("services.levy_stability_service.db") as mock_db:
        mock_db.levy_stability_snapshots.update_one = AsyncMock()

        result = await compute_levy_stability_snapshot("13195", force=True)

    assert result["levy_stability_score"] < 50
