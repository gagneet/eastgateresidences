from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from routers.feature_toggles import get_my_feature_access, get_user_feature_access_summary


@pytest.fixture(autouse=True)
def _patch_pg_toggle_repo(monkeypatch):
    import routers.feature_toggles as ft_router

    async def list_resolved_feature_toggles(building_id, category=None, only_enabled=False):
        global_query = {"building_id": None}
        if category:
            global_query["category"] = category
        if only_enabled:
            global_query["is_enabled"] = True

        global_toggles = await ft_router.db.feature_toggles.find(global_query).to_list(200)
        toggle_map = {toggle["feature_key"]: dict(toggle) for toggle in global_toggles}

        if building_id:
            override_query = {"building_id": building_id}
            if category:
                override_query["category"] = category
            overrides = await ft_router.db.feature_toggles.find(override_query).to_list(200)
            for override in overrides:
                toggle_map[override["feature_key"]] = {**toggle_map.get(override["feature_key"], {}), **override}

        return [
            {
                "id": str(toggle.get("_id", f"id_{toggle['feature_key']}")),
                "feature_key": toggle["feature_key"],
                "feature_name": toggle["feature_name"],
                "is_enabled": toggle.get("is_enabled", True),
                "category": toggle.get("category"),
                "routes": toggle.get("routes", []),
                "depends_on": toggle.get("depends_on", []),
            }
            for toggle in toggle_map.values()
        ]

    monkeypatch.setattr(ft_router.config_repo, "list_resolved_feature_toggles", list_resolved_feature_toggles)
    monkeypatch.setattr(ft_router.identity_repo, "find_user_by_id_for_admin", AsyncMock(return_value=None))


@pytest.mark.asyncio
@patch("routers.feature_toggles.db")
async def test_get_my_feature_access_parallel(mock_db):
    """Test that get_my_feature_access uses parallel queries."""
    mock_user = {"id": "user123", "role": "owner"}

    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[
        {"feature_key": "f1", "feature_name": "F1", "is_enabled": True, "category": "core"}
    ])
    mock_db.feature_toggles.find.return_value = mock_cursor

    mock_access_cursor = MagicMock()
    mock_access_cursor.to_list = AsyncMock(return_value=[])
    mock_db.user_feature_access.find.return_value = mock_access_cursor

    result = await get_my_feature_access(current_user=mock_user)

    assert len(result) == 1
    assert result[0].feature_key == "f1"
    assert mock_db.feature_toggles.find.called
    assert mock_db.user_feature_access.find.called


@pytest.mark.asyncio
@patch("routers.feature_toggles.db")
async def test_get_user_feature_access_summary_parallel(mock_db):
    """Test that get_user_feature_access_summary uses parallel queries."""
    mock_admin = {"id": "admin123", "role": "super_admin"}
    target_user_id = "user456"

    mock_db.users.find_one = AsyncMock(return_value={"id": target_user_id, "role": "owner"})

    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[
        {"feature_key": "f1", "feature_name": "F1", "is_enabled": True, "category": "core"}
    ])
    mock_db.feature_toggles.find.return_value = mock_cursor

    mock_access_cursor = MagicMock()
    mock_access_cursor.to_list = AsyncMock(return_value=[])
    mock_db.user_feature_access.find.return_value = mock_access_cursor

    result = await get_user_feature_access_summary(user_id=target_user_id, current_user=mock_admin)

    assert len(result) == 1
    assert result[0].feature_key == "f1"
    assert mock_db.users.find_one.called
    assert mock_db.feature_toggles.find.called
    assert mock_db.user_feature_access.find.called
