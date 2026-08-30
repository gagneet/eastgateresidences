from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_external_api_toggle_gate_uses_postgres_repo():
    from routers.external_api import _check_external_api_enabled

    with patch(
        "routers.external_api.config_repo.get_global_feature_toggle_state",
        new=AsyncMock(return_value=False),
    ):
        with pytest.raises(HTTPException) as exc:
            await _check_external_api_enabled()

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_occupancy_toggle_gate_uses_postgres_repo():
    from routers.occupancy import _require_occupancy_access

    with patch(
        "routers.occupancy.config_repo.resolve_feature_toggle",
        new=AsyncMock(return_value=False),
    ):
        with pytest.raises(HTTPException) as exc:
            await _require_occupancy_access(current_user={"id": "user-1"}, building_id="13195")

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_invoice_lifecycle_feature_gate_uses_postgres_repo():
    from domain.invoice_lifecycle import _feature_enabled

    with patch(
        "domain.invoice_lifecycle.config_repo.resolve_feature_toggle",
        new=AsyncMock(return_value=True),
    ):
        result = await _feature_enabled(db=None, building_id="13195", key="ap_automation")

    assert result is True


@pytest.mark.asyncio
async def test_arq_toggle_guard_uses_postgres_repo():
    from workers.arq_tasks import _toggle_on

    with (
        patch("workers.arq_tasks.set_ctx_building_id"),
        patch(
            "workers.arq_tasks.config_repo.resolve_feature_toggle",
            new=AsyncMock(return_value=True),
        ) as mock_resolve,
    ):
        result = await _toggle_on("13195")

    assert result is True
    mock_resolve.assert_awaited_once_with("13195", "financial_integration_layer_v2", default=False)


@pytest.mark.asyncio
async def test_scheduler_arq_toggle_guard_uses_postgres_repo():
    from workers.scheduler import _arq_toggle_on

    with patch(
        "db_postgres.repos.config_repo.resolve_feature_toggle",
        new=AsyncMock(return_value=True),
    ) as mock_resolve:
        result = await _arq_toggle_on("13195")

    assert result is True
    mock_resolve.assert_awaited_once_with("13195", "financial_integration_layer_v2", default=False)
