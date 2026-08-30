from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.feature_toggle import FeatureToggleCreate, UserFeatureAccessCreate
from routers import feature_toggles as feature_toggle_router
from utils.permissions import get_effective_feature_access
from utils.rate_limit import refresh_rate_limit_config


@pytest.mark.asyncio
async def test_get_all_feature_toggles_uses_postgres_repo():
    current_user = {"id": "admin-1", "role": "super_admin"}

    with patch(
        "routers.feature_toggles.config_repo.list_resolved_feature_toggles",
        new=AsyncMock(return_value=[{
            "id": "toggle-1",
            "feature_key": "documents",
            "feature_name": "Documents",
            "is_enabled": True,
            "category": "core",
            "routes": ["/documents"],
            "depends_on": [],
        }]),
    ) as mock_list:
        result = await feature_toggle_router.get_all_feature_toggles(current_user=current_user)

    mock_list.assert_awaited_once()
    assert result[0].feature_key == "documents"


@pytest.mark.asyncio
async def test_create_feature_toggle_uses_postgres_repo():
    current_user = {"id": "admin-1", "role": "super_admin", "email": "admin@example.com"}

    with (
        patch(
            "routers.feature_toggles.config_repo.get_global_feature_toggle",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "routers.feature_toggles.config_repo.create_global_feature_toggle",
            new=AsyncMock(return_value={
                "id": "toggle-1",
                "feature_key": "chat",
                "feature_name": "Chat",
                "description": "Team chat",
                "is_enabled": True,
                "category": "communication",
                "icon": "MessageCircle",
                "routes": ["/community/chat"],
                "depends_on": [],
            }),
        ) as mock_create,
    ):
        result = await feature_toggle_router.create_feature_toggle(
            FeatureToggleCreate(
                feature_key="chat",
                feature_name="Chat",
                description="Team chat",
                category="communication",
                icon="MessageCircle",
                routes=["/community/chat"],
            ),
            current_user=current_user,
        )

    mock_create.assert_awaited_once()
    assert result.feature_key == "chat"


@pytest.mark.asyncio
async def test_create_user_feature_access_uses_postgres_for_pg_user():
    current_user = {"id": "admin-1", "role": "super_admin", "email": "admin@example.com"}

    with (
        patch(
            "routers.feature_toggles.identity_repo.find_user_by_id_for_admin",
            new=AsyncMock(return_value={"id": "user-1", "role": "owner", "permission_overrides": {}}),
        ),
        patch(
            "routers.feature_toggles.config_repo.get_global_feature_toggle",
            new=AsyncMock(return_value={"id": "toggle-1", "feature_key": "chat", "feature_name": "Chat"}),
        ),
        patch(
            "routers.feature_toggles.config_repo.upsert_user_feature_override",
            new=AsyncMock(return_value={
                "id": "user-1:chat",
                "user_id": "user-1",
                "feature_key": "chat",
                "is_enabled": False,
                "granted_by": "admin-1",
                "granted_at": "2026-05-12T00:00:00+00:00",
                "notes": "disabled",
            }),
        ) as mock_upsert,
        patch("routers.feature_toggles.db", new=MagicMock()) as mock_db,
    ):
        result = await feature_toggle_router.create_user_feature_access(
            user_id="user-1",
            access_data=UserFeatureAccessCreate(
                user_id="user-1",
                feature_key="chat",
                is_enabled=False,
                notes="disabled",
            ),
            current_user=current_user,
        )

    mock_upsert.assert_awaited_once()
    assert result.feature_key == "chat"
    assert result.is_enabled is False
    assert not mock_db.user_feature_access.insert_one.called


@pytest.mark.asyncio
async def test_get_effective_feature_access_uses_pg_toggle_and_permission_overrides():
    user = {
        "id": "user-1",
        "role": "owner",
        "building_id": "13195",
        "permission_overrides": {
            "feature_access": {
                "chat": {
                    "is_enabled": False,
                    "granted_by": "admin-1",
                }
            }
        },
    }

    with patch(
        "db_postgres.repos.config_repo.resolve_feature_toggle",
        new=AsyncMock(return_value=True),
    ) as mock_resolve:
        result = await get_effective_feature_access(user, "chat")

    mock_resolve.assert_awaited_once_with("13195", "chat", default=True)
    assert result is False


@pytest.mark.asyncio
async def test_refresh_rate_limit_config_reads_pg_toggle():
    mock_site_settings = MagicMock()
    mock_site_settings.find_one = AsyncMock(return_value={})
    mock_db = MagicMock(site_settings=mock_site_settings)

    with (
        patch(
            "db_postgres.repos.config_repo.get_global_feature_toggle_state",
            new=AsyncMock(return_value=False),
        ) as mock_toggle_state,
        patch("database.db", new=mock_db),
    ):
        state = await refresh_rate_limit_config()

    mock_toggle_state.assert_awaited_once_with("rate_limiting", default=True)
    assert state["enabled"] is False
