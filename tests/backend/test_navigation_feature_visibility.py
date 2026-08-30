from unittest.mock import AsyncMock, patch

import pytest

from routers.navigation import get_nav_config


def _owner():
    return {
        "id": "owner-001",
        "role": "owner",
        "building_id": "16244",
        "full_name": "Owner Test",
    }


def _nav_config(item: dict):
    return {
        "role": "owner",
        "simple_items": [item],
        "advanced_items": [],
    }


def _nav_config_with_advanced(item: dict):
    return {
        "role": "owner",
        "simple_items": [],
        "advanced_items": [item],
    }


def _nav_item(item_id: str, route: str):
    return {
        "id": item_id,
        "label": item_id.replace("-", " ").title(),
        "route": route,
        "icon": "Wrench",
        "feature_flag": "",
        "permission_flag": "",
        "badge_source": "",
        "priority": 1,
    }


def _configure_mocks(mock_db, mock_feature_access, item: dict, feature_entries: list[dict]):
    mock_db.navigation_configs.find_one = AsyncMock(return_value=_nav_config(item))
    mock_db.user_nav_preferences.find_one = AsyncMock(return_value={})
    mock_db.adaptive_nav_scores.find_one = AsyncMock(return_value=None)
    mock_feature_access.return_value = feature_entries


@pytest.mark.asyncio
@patch("routers.navigation._resolve_effective_feature_access_entries", new_callable=AsyncMock)
@patch("routers.navigation.db")
async def test_nav_config_hides_route_matched_disabled_feature(mock_db, mock_feature_access):
    _configure_mocks(
        mock_db,
        mock_feature_access,
        _nav_item("maintenance", "/maintenance"),
        [{
            "feature_key": "maintenance",
            "effective_access": False,
            "routes": ["/maintenance"],
        }],
    )

    result = await get_nav_config(current_user=_owner(), building_id="16244")

    assert result["simple_items"] == []


@pytest.mark.asyncio
@patch("routers.navigation._resolve_effective_feature_access_entries", new_callable=AsyncMock)
@patch("routers.navigation.db")
async def test_nav_config_keeps_route_matched_enabled_feature(mock_db, mock_feature_access):
    _configure_mocks(
        mock_db,
        mock_feature_access,
        _nav_item("maintenance", "/maintenance"),
        [{
            "feature_key": "maintenance",
            "effective_access": True,
            "routes": ["/maintenance"],
        }],
    )

    result = await get_nav_config(current_user=_owner(), building_id="16244")

    assert [item["id"] for item in result["simple_items"]] == ["maintenance"]


@pytest.mark.asyncio
@patch("routers.navigation._resolve_effective_feature_access_entries", new_callable=AsyncMock)
@patch("routers.navigation.db")
async def test_nav_config_keeps_parent_route_visible_when_only_child_feature_disabled(mock_db, mock_feature_access):
    _configure_mocks(
        mock_db,
        mock_feature_access,
        _nav_item("finance", "/financials"),
        [
            {
                "feature_key": "finance",
                "effective_access": True,
                "routes": ["/financials"],
            },
            {
                "feature_key": "levy_kpi_dashboard",
                "effective_access": False,
                "routes": ["/financials/levy-kpi"],
            },
        ],
    )

    result = await get_nav_config(current_user=_owner(), building_id="16244")

    assert [item["id"] for item in result["simple_items"]] == ["finance"]


@pytest.mark.asyncio
@patch("routers.navigation._resolve_effective_feature_access_entries", new_callable=AsyncMock)
@patch("routers.navigation.db")
async def test_nav_config_hides_alias_route_when_feature_toggle_disabled(mock_db, mock_feature_access):
    _configure_mocks(
        mock_db,
        mock_feature_access,
        _nav_item("events", "/community/events"),
        [{
            "feature_key": "events",
            "effective_access": False,
            "routes": ["/community/events"],
        }],
    )

    result = await get_nav_config(current_user=_owner(), building_id="16244")

    assert result["simple_items"] == []


@pytest.mark.asyncio
@patch("routers.navigation._resolve_effective_feature_access_entries", new_callable=AsyncMock)
@patch("routers.navigation.db")
async def test_nav_config_hides_multi_match_route_when_any_matching_toggle_disabled(mock_db, mock_feature_access):
    _configure_mocks(
        mock_db,
        mock_feature_access,
        _nav_item("my-levies", "/financials/levy-payments"),
        [
            {
                "feature_key": "finance",
                "effective_access": True,
                "routes": ["/financials", "/financials/levy-payments"],
            },
            {
                "feature_key": "levy_payments",
                "effective_access": False,
                "routes": ["/financials/levy-payments"],
            },
        ],
    )

    result = await get_nav_config(current_user=_owner(), building_id="16244")

    assert result["simple_items"] == []


@pytest.mark.asyncio
@patch("routers.navigation._resolve_effective_feature_access_entries", new_callable=AsyncMock)
@patch("routers.navigation.db")
async def test_nav_config_hides_advanced_item_from_preferences(mock_db, mock_feature_access):
    item = _nav_item("reports", "/reports")
    mock_db.navigation_configs.find_one = AsyncMock(return_value=_nav_config_with_advanced(item))
    mock_db.user_nav_preferences.find_one = AsyncMock(return_value={"hidden_items": ["reports"]})
    mock_db.adaptive_nav_scores.find_one = AsyncMock(return_value=None)
    mock_feature_access.return_value = []

    result = await get_nav_config(current_user=_owner(), building_id="16244")

    assert result["advanced_items"] == []
    assert result["pinned_items"] == []
