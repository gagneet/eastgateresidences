from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.levy_reminder_settings_service import (
    LEVY_REMINDER_SETTINGS_KEY,
    get_levy_reminder_settings,
    upsert_levy_reminder_settings,
)


@pytest.mark.asyncio
async def test_get_levy_reminder_settings_prefers_postgres():
    mock_db = MagicMock()
    mock_db.levy_reminder_settings.find_one = AsyncMock()
    mock_db.settings.find_one = AsyncMock()

    with (
        patch(
            "services.levy_reminder_settings_service.config_repo.get_building_setting",
            new=AsyncMock(return_value={"enabled": False, "pre_due_days": [21, 7]}),
        ),
        patch("services.levy_reminder_settings_service._get_db", return_value=mock_db),
    ):
        result = await get_levy_reminder_settings("13195")

    assert result["building_id"] == "13195"
    assert result["enabled"] is False
    assert result["pre_due_days"] == [21, 7]
    assert result["reminder_days"] == [21, 7]
    mock_db.levy_reminder_settings.find_one.assert_not_called()
    mock_db.settings.find_one.assert_not_called()


@pytest.mark.asyncio
async def test_get_levy_reminder_settings_falls_back_to_mongo_collection():
    mock_db = MagicMock()
    mock_db.levy_reminder_settings.find_one = AsyncMock(
        return_value={"building_id": "13195", "enabled": False, "overdue_days": [5]}
    )
    mock_db.settings.find_one = AsyncMock(return_value=None)

    with (
        patch(
            "services.levy_reminder_settings_service.config_repo.get_building_setting",
            new=AsyncMock(return_value=None),
        ),
        patch("services.levy_reminder_settings_service._get_db", return_value=mock_db),
    ):
        result = await get_levy_reminder_settings("13195")

    assert result["enabled"] is False
    assert result["overdue_days"] == [5]
    mock_db.levy_reminder_settings.find_one.assert_awaited_once()
    mock_db.settings.find_one.assert_not_called()


@pytest.mark.asyncio
async def test_get_levy_reminder_settings_falls_back_when_postgres_read_errors():
    mock_db = MagicMock()
    mock_db.levy_reminder_settings.find_one = AsyncMock(
        return_value={"building_id": "13195", "enabled": True, "pre_due_days": [30, 7]}
    )
    mock_db.settings.find_one = AsyncMock(return_value=None)

    with (
        patch(
            "services.levy_reminder_settings_service.config_repo.get_building_setting",
            new=AsyncMock(side_effect=RuntimeError("pg unavailable")),
        ),
        patch("services.levy_reminder_settings_service._get_db", return_value=mock_db),
    ):
        result = await get_levy_reminder_settings("13195")

    assert result["pre_due_days"] == [30, 7]
    mock_db.levy_reminder_settings.find_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_levy_reminder_settings_falls_back_to_legacy_settings_doc():
    mock_db = MagicMock()
    mock_db.levy_reminder_settings.find_one = AsyncMock(return_value=None)
    mock_db.settings.find_one = AsyncMock(
        return_value={"building_id": "13195", "type": "levy_reminder_settings", "reminder_days": [10, 3]}
    )

    with (
        patch(
            "services.levy_reminder_settings_service.config_repo.get_building_setting",
            new=AsyncMock(return_value=None),
        ),
        patch("services.levy_reminder_settings_service._get_db", return_value=mock_db),
    ):
        result = await get_levy_reminder_settings("13195")

    assert result["pre_due_days"] == [10, 3]
    assert result["reminder_days"] == [10, 3]
    mock_db.settings.find_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_levy_reminder_settings_writes_postgres_and_mongo_mirrors():
    mock_db = MagicMock()
    mock_db.levy_reminder_settings.find_one = AsyncMock(return_value=None)
    mock_db.settings.find_one = AsyncMock(return_value=None)
    mock_db.levy_reminder_settings.update_one = AsyncMock()
    mock_db.settings.update_one = AsyncMock()
    pg_upsert = AsyncMock()

    with (
        patch(
            "services.levy_reminder_settings_service.config_repo.get_building_setting",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "services.levy_reminder_settings_service.config_repo.upsert_building_setting",
            new=pg_upsert,
        ),
        patch("services.levy_reminder_settings_service._get_db", return_value=mock_db),
    ):
        result = await upsert_levy_reminder_settings(
            "13195",
            {"enabled": False, "pre_due_days": [14, 7]},
            updated_by="user-1",
        )

    pg_args = pg_upsert.await_args.args
    assert pg_args[0] == "13195"
    assert pg_args[1] == LEVY_REMINDER_SETTINGS_KEY
    assert pg_args[2]["enabled"] is False
    assert pg_args[2]["pre_due_days"] == [14, 7]
    assert "reminder_days" not in pg_args[2]
    mock_db.levy_reminder_settings.update_one.assert_awaited_once()
    mock_db.settings.update_one.assert_awaited_once()
    assert result["updated_by"] == "user-1"


@pytest.mark.asyncio
async def test_legacy_finance_get_maps_pg_shape_to_legacy_response():
    from routers.finance import get_levy_reminder_settings as get_legacy_settings

    current_user = {"id": "user-1"}
    permissions = SimpleNamespace(can_send_notifications=True)

    with (
        patch("routers.finance.get_user_permissions", return_value=permissions),
        patch(
            "routers.finance.get_pg_levy_reminder_settings",
            new=AsyncMock(return_value={"enabled": True, "pre_due_days": [14, 5], "last_sent": "2026-05-01"}),
        ),
    ):
        result = await get_legacy_settings(current_user=current_user, building_id="13195")

    assert result == {
        "building_id": "13195",
        "enabled": True,
        "reminder_days": [14, 5],
        "last_sent": "2026-05-01",
    }


@pytest.mark.asyncio
async def test_legacy_finance_put_maps_reminder_days_to_pre_due_days():
    from routers.finance import update_levy_reminder_settings as update_legacy_settings

    current_user = {"id": "user-1"}
    permissions = SimpleNamespace(can_send_notifications=True)

    with (
        patch("routers.finance.get_user_permissions", return_value=permissions),
        patch(
            "routers.finance.upsert_pg_levy_reminder_settings",
            new=AsyncMock(),
        ) as mock_upsert,
    ):
        result = await update_legacy_settings(
            data={"enabled": True, "reminder_days": [21, 7]},
            current_user=current_user,
            building_id="13195",
        )

    assert result["message"] == "Levy reminder settings updated"
    upsert_args = mock_upsert.await_args.args
    assert upsert_args[0] == "13195"
    assert upsert_args[1]["pre_due_days"] == [21, 7]
    assert upsert_args[1]["enabled"] is True
