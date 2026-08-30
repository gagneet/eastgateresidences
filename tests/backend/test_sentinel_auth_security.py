"""
Security tests for impersonation PII masking and registration decision enhancements.
"""

import inspect
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from starlette.requests import Request as StarletteRequest
from fastapi import HTTPException

from models.user import UserRole
from routers.auth import impersonate_user, show_registration_decision_form, process_registration_decision_via_token


def _make_request(path: str = "/api/auth/impersonate") -> StarletteRequest:
    scope = {
        "type": "http", "method": "POST", "path": path,
        "query_string": b"", "headers": [], "client": ("127.0.0.1", 12345),
    }
    return StarletteRequest(scope)


@pytest.mark.asyncio
async def test_impersonate_user_pii_masking_in_initial_response():
    """Verify that the initial response from impersonate_user has PII masked."""
    mock_admin = {
        "id": "admin-1",
        "role": UserRole.SUPER_ADMIN,
        "full_name": "Admin User"
    }

    mock_target = {
        "id": "user-1",
        "email": "target@example.com",
        "full_name": "Target User",
        "role": UserRole.OWNER,
        "is_active": True,
        "status": "active",
        "created_at": "2024-01-01T00:00:00Z",
        "phone": "+61400000000",
        "home_address": "123 Secret St"
    }

    mock_data = MagicMock()
    mock_data.user_id = "user-1"
    mock_data.building_id = "13195"

    with patch("routers.auth.db") as mock_db, \
            patch("routers.auth.create_token", return_value="mock-token"), \
            patch("routers.auth.create_audit_log", AsyncMock()), \
            patch(
                "routers.auth.config_repo.get_global_feature_toggle_state",
                new=AsyncMock(return_value=True),
            ):
        # Mock target user fetch
        mock_db.users.find_one = AsyncMock(return_value=mock_target)
        mock_db.memberships.find_one = AsyncMock(return_value={
            "building_id": "13195",
            "user_id": "user-1",
            "is_active": True,
        })

        response = await impersonate_user(request=_make_request(), data=mock_data, current_user=mock_admin)

        # Verify PII masking was triggered
        assert response.user.full_name == "Resident"
        assert response.user.email == "t***@example.com"
        assert response.user.phone == "+6******00"
        assert response.user.home_address == "REDACTED"


@pytest.mark.asyncio
async def test_impersonate_user_falls_back_to_mongo_toggle_when_pg_config_unavailable():
    mock_admin = {
        "id": "admin-1",
        "role": UserRole.SUPER_ADMIN,
        "full_name": "Admin User",
    }
    mock_target = {
        "id": "user-1",
        "email": "target@example.com",
        "full_name": "Target User",
        "role": UserRole.OWNER,
        "is_active": True,
        "status": "active",
        "created_at": "2024-01-01T00:00:00Z",
    }
    mock_data = MagicMock()
    mock_data.user_id = "user-1"
    mock_data.building_id = "13195"

    with (
        patch("routers.auth.config_repo", None),
        patch("routers.auth.db") as mock_db,
        patch("routers.auth.create_token", return_value="mock-token"),
        patch("routers.auth.create_audit_log", AsyncMock()),
    ):
        mock_db.feature_toggles.find_one = AsyncMock(return_value={"is_enabled": True})
        mock_db.users.find_one = AsyncMock(return_value=mock_target)
        mock_db.memberships.find_one = AsyncMock(return_value={
            "building_id": "13195",
            "user_id": "user-1",
            "is_active": True,
        })

        response = await impersonate_user(request=_make_request(), data=mock_data, current_user=mock_admin)

    assert response.token == "mock-token"
    mock_db.feature_toggles.find_one.assert_awaited_once_with({"feature_key": "impersonation"})


@pytest.mark.asyncio
async def test_impersonate_user_raises_when_pg_toggle_disabled():
    mock_admin = {
        "id": "admin-1",
        "role": UserRole.SUPER_ADMIN,
        "full_name": "Admin User",
    }
    mock_data = MagicMock()
    mock_data.user_id = "user-1"
    mock_data.building_id = "13195"

    with patch(
        "routers.auth.config_repo.get_global_feature_toggle_state",
        new=AsyncMock(return_value=False),
    ):
        with pytest.raises(HTTPException) as exc:
            await impersonate_user(request=_make_request(), data=mock_data, current_user=mock_admin)

    assert exc.value.status_code == 403
    assert "disabled" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_registration_decision_rate_limiting_decorators():
    """Verify rate limiting decorators are present on registration decision endpoints."""
    # show_registration_decision_form (GET)
    source_get = inspect.getsource(show_registration_decision_form)
    assert "@rate_limit" in source_get

    # process_registration_decision_via_token (POST)
    source_post = inspect.getsource(process_registration_decision_via_token)
    assert "@rate_limit" in source_post


@pytest.mark.asyncio
async def test_registration_decision_endpoints_have_request_param():
    """Verify endpoints accept Request parameter for rate limiting."""
    # GET endpoint
    sig_get = inspect.signature(show_registration_decision_form)
    assert "request" in sig_get.parameters

    # POST endpoint
    sig_post = inspect.signature(process_registration_decision_via_token)
    assert "request" in sig_post.parameters


def test_registration_decision_post_input_constraints():
    """Verify Form parameters have max_length constraints."""
    sig = inspect.signature(process_registration_decision_via_token)

    instructions_param = sig.parameters["instructions"]
    # In FastAPI/Pydantic v2, max_length is stored in metadata
    assert any(getattr(m, "max_length", None) == 1000 for m in instructions_param.default.metadata)

    reason_param = sig.parameters["reason"]
    assert any(getattr(m, "max_length", None) == 1000 for m in reason_param.default.metadata)
