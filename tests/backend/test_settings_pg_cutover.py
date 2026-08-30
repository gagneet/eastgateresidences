from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.settings import SiteSettingsUpdate
from models.user import UserRole
from routers import settings as settings_router
from services.settings_service import (
    GENERAL_SETTINGS_KEY,
    UNIT_DISPLAY_SETTINGS_KEY,
    get_general_settings,
    get_unit_display_rules,
    upsert_general_settings,
    upsert_unit_display_rules,
)


class _FakeCutoverConn:
    def __init__(self, row):
        self.row = row
        self.executed = []

    async def execute(self, *args):
        self.executed.append(args)

    async def fetchrow(self, *args):
        self.executed.append(args)
        return self.row


@pytest.mark.asyncio
async def test_get_general_settings_prefers_postgres():
    mock_db = MagicMock()
    mock_db.settings.find_one = AsyncMock()

    with (
        patch(
            "services.settings_service.config_repo.get_building_setting",
            new=AsyncMock(return_value={"building_name": "PG Tower"}),
        ),
        patch("services.settings_service.db", mock_db),
    ):
        result = await get_general_settings("13195", {"_id": 0})

    assert result == {
        "building_id": "13195",
        "type": "general",
        "building_name": "PG Tower",
    }
    mock_db.settings.find_one.assert_not_called()


@pytest.mark.asyncio
async def test_get_general_settings_falls_back_to_mongo():
    mock_db = MagicMock()
    mock_db.settings.find_one = AsyncMock(
        return_value={"building_id": "13195", "type": "general", "building_name": "Mongo Tower"}
    )

    with (
        patch(
            "services.settings_service.config_repo.get_building_setting",
            new=AsyncMock(return_value=None),
        ),
        patch("services.settings_service.db", mock_db),
    ):
        result = await get_general_settings("13195", {"_id": 0})

    assert result["building_name"] == "Mongo Tower"
    mock_db.settings.find_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_general_settings_writes_postgres():
    postgres_get = AsyncMock(
        side_effect=[
            {"building_name": "Before"},
            {"building_name": "After", "building_id": "13195", "type": "general"},
        ]
    )
    postgres_upsert = AsyncMock()
    mock_cursor = MagicMock()
    mock_cursor.sort.return_value = mock_cursor
    mock_cursor.to_list = AsyncMock(return_value=[])
    mock_db = MagicMock()
    mock_db.settings.find.return_value = mock_cursor
    mock_db.settings.insert_one = AsyncMock()
    mock_db.settings.update_one = AsyncMock()
    mock_db.settings.delete_many = AsyncMock()

    with (
        patch("services.settings_service.config_repo.get_building_setting", new=postgres_get),
        patch("services.settings_service.config_repo.upsert_building_setting", new=postgres_upsert),
        patch("services.settings_service._settings_write_source_is_postgres", new=AsyncMock(return_value=False)),
        patch("services.settings_service.db", mock_db),
    ):
        result = await upsert_general_settings("13195", {"building_name": "After"})

    postgres_upsert.assert_awaited_once()
    mock_db.settings.insert_one.assert_awaited_once()
    upsert_args = postgres_upsert.await_args.args
    assert upsert_args[0] == "13195"
    assert upsert_args[1] == GENERAL_SETTINGS_KEY
    assert upsert_args[2]["building_name"] == "After"
    assert upsert_args[2]["building_id"] == "13195"
    assert upsert_args[2]["type"] == "general"
    assert result["building_name"] == "After"


@pytest.mark.asyncio
async def test_upsert_general_settings_skips_mongo_when_postgres_write_primary():
    postgres_get = AsyncMock(
        side_effect=[
            {"building_name": "Before"},
            {"building_name": "After", "building_id": "13195", "type": "general"},
        ]
    )
    postgres_upsert = AsyncMock()
    mock_db = MagicMock()
    mock_db.settings.find = MagicMock()
    mock_db.settings.insert_one = AsyncMock()
    mock_db.settings.update_one = AsyncMock()

    with (
        patch("services.settings_service.config_repo.get_building_setting", new=postgres_get),
        patch("services.settings_service.config_repo.upsert_building_setting", new=postgres_upsert),
        patch("services.settings_service._settings_write_source_is_postgres", new=AsyncMock(return_value=True)),
        patch("services.settings_service.db", mock_db),
    ):
        result = await upsert_general_settings("13195", {"building_name": "After"})

    postgres_upsert.assert_awaited_once()
    mock_db.settings.find.assert_not_called()
    mock_db.settings.insert_one.assert_not_called()
    mock_db.settings.update_one.assert_not_called()
    assert result["building_name"] == "After"


@pytest.mark.asyncio
async def test_get_unit_display_rules_uses_postgres_when_settings_read_primary():
    rules = [{"prefix": "TH", "min": 71, "max": 87, "pad": 3}]
    mock_db = MagicMock()
    mock_db.settings.find_one = AsyncMock()

    with (
        patch(
            "services.settings_service.config_repo.get_building_setting",
            new=AsyncMock(return_value={"building_id": "13195", "type": "unit_display", "rules": rules}),
        ),
        patch("services.settings_service._settings_read_source_is_postgres", new=AsyncMock(return_value=True)),
        patch("services.settings_service.db", mock_db),
    ):
        result = await get_unit_display_rules("13195")

    assert result == rules
    mock_db.settings.find_one.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_unit_display_rules_skips_mongo_when_postgres_write_primary():
    rules = [{"prefix": "TH", "min": 71, "max": 87, "pad": 3}]
    postgres_get = AsyncMock(return_value={"building_id": "13195", "type": "unit_display", "rules": rules})
    postgres_upsert = AsyncMock()
    mock_db = MagicMock()
    mock_db.settings.update_one = AsyncMock()

    with (
        patch("services.settings_service.config_repo.get_building_setting", new=postgres_get),
        patch("services.settings_service.config_repo.upsert_building_setting", new=postgres_upsert),
        patch("services.settings_service._settings_write_source_is_postgres", new=AsyncMock(return_value=True)),
        patch("services.settings_service.db", mock_db),
    ):
        result = await upsert_unit_display_rules("13195", rules)

    postgres_upsert.assert_awaited_once_with(
        "13195",
        UNIT_DISPLAY_SETTINGS_KEY,
        {"building_id": "13195", "type": "unit_display", "rules": rules},
    )
    mock_db.settings.update_one.assert_not_called()
    assert result == rules


@pytest.mark.asyncio
async def test_update_site_settings_uses_general_settings_service():
    current_user = {
        "id": "user-1",
        "role": UserRole.SUPER_ADMIN,
        "email": "admin@example.com",
    }

    with (
        patch(
            "routers.settings.get_user_permissions",
            return_value=SimpleNamespace(can_manage_users=True),
        ),
        patch(
            "routers.settings.upsert_general_settings",
            new=AsyncMock(),
        ) as mock_upsert_general_settings,
        patch(
            "routers.settings.get_site_settings",
            new=AsyncMock(return_value={"building_name": "PG Tower"}),
        ),
    ):
        result = await settings_router.update_site_settings(
            SiteSettingsUpdate(building_name="PG Tower"),
            current_user=current_user,
            building_id="13195",
        )

    assert result["building_name"] == "PG Tower"
    mock_upsert_general_settings.assert_awaited_once()
    upsert_args = mock_upsert_general_settings.await_args.args
    assert upsert_args[0] == "13195"
    assert upsert_args[1]["building_name"] == "PG Tower"


@pytest.mark.asyncio
async def test_settings_bootstrap_reads_current_cutover_mode_without_defaulting_shadow_to_primary(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("MONGO_URL", "mongodb://localhost:27017")
    from scripts.bootstrap_postgres_settings import _current_cutover_mode

    conn = _FakeCutoverConn({"mode": "postgres_shadow", "readiness_status": "shadow_active"})

    mode, readiness = await _current_cutover_mode(conn, "13195", "settings")

    assert mode == "postgres_shadow"
    assert readiness == "shadow_active"


@pytest.mark.asyncio
async def test_settings_bootstrap_defaults_missing_cutover_row_to_pre_shadow_state(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("MONGO_URL", "mongodb://localhost:27017")
    from scripts.bootstrap_postgres_settings import _current_cutover_mode

    conn = _FakeCutoverConn(None)

    mode, readiness = await _current_cutover_mode(conn, "13195", "settings")

    assert mode == "mongo_primary"
    assert readiness == "not_started"
