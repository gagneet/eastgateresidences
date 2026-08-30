import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from services.adaptive_nav_service import get_nudge_for_user  # noqa: E402


def _advanced_item(item_id: str, priority: int) -> dict:
    return {
        "id": item_id,
        "label": item_id.replace("-", " ").title(),
        "route": f"/dashboard/{item_id}",
        "priority": priority,
        "discovery_hint": f"Try {item_id}",
    }


def _nav_config() -> dict:
    return {
        "advanced_items": [
            _advanced_item("reports", 1),
            _advanced_item("documents", 2),
            _advanced_item("finance", 3),
        ],
    }


def _mock_db():
    class MockDb:
        user_nav_preferences = type(
            "Prefs",
            (),
            {"update_one": AsyncMock(return_value=None)},
        )()

    return MockDb()


@pytest.mark.asyncio
async def test_nudge_returns_first_priority_candidate_without_rotation_cursor():
    mock_db = _mock_db()

    with patch("database.db", mock_db):
        result = await get_nudge_for_user(
            user_id="user-001",
            role="owner",
            building_id="building-001",
            nav_config=_nav_config(),
            prefs={},
            login_count=10,
        )

    assert result["feature_id"] == "reports"
    mock_db.user_nav_preferences.update_one.assert_awaited_once()
    update_payload = mock_db.user_nav_preferences.update_one.await_args.args[1]
    assert update_payload["$set"]["last_nudge_feature_id"] == "reports"


@pytest.mark.asyncio
async def test_nudge_rotates_after_last_served_feature():
    mock_db = _mock_db()

    with patch("database.db", mock_db):
        result = await get_nudge_for_user(
            user_id="user-001",
            role="owner",
            building_id="building-001",
            nav_config=_nav_config(),
            prefs={"last_nudge_feature_id": "reports"},
            login_count=10,
        )

    assert result["feature_id"] == "documents"
    update_payload = mock_db.user_nav_preferences.update_one.await_args.args[1]
    assert update_payload["$set"]["last_nudge_feature_id"] == "documents"


@pytest.mark.asyncio
async def test_nudge_rotation_wraps_to_first_candidate():
    mock_db = _mock_db()

    with patch("database.db", mock_db):
        result = await get_nudge_for_user(
            user_id="user-001",
            role="owner",
            building_id="building-001",
            nav_config=_nav_config(),
            prefs={"last_nudge_feature_id": "finance"},
            login_count=10,
        )

    assert result["feature_id"] == "reports"


@pytest.mark.asyncio
async def test_nudge_rotation_skips_seen_features():
    mock_db = _mock_db()

    with patch("database.db", mock_db):
        result = await get_nudge_for_user(
            user_id="user-001",
            role="owner",
            building_id="building-001",
            nav_config=_nav_config(),
            prefs={
                "last_nudge_feature_id": "reports",
                "features_seen": {"documents": True},
            },
            login_count=10,
        )

    assert result["feature_id"] == "finance"


@pytest.mark.asyncio
async def test_nudge_dismissal_cooldown_suppresses_rotation():
    mock_db = _mock_db()

    with patch("database.db", mock_db):
        result = await get_nudge_for_user(
            user_id="user-001",
            role="owner",
            building_id="building-001",
            nav_config=_nav_config(),
            prefs={
                "last_nudge_feature_id": "reports",
                "last_nudge_dismissed_at": datetime.now(timezone.utc).isoformat(),
            },
            login_count=10,
        )

    assert result is None
    mock_db.user_nav_preferences.update_one.assert_not_awaited()
