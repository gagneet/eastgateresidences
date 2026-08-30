from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _Session:
    def __init__(self, row):
        self.row = row
        self.statements = []

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), params or {}))
        if "UPDATE core.parties" in str(statement):
            return _Result(None)
        return _Result(self.row)


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_update_email_preference_promotes_secondary_email():
    from routers.auth import EmailPreferenceRequest, update_email_preference

    scheme = {
        "tenant_id": "9e9d75c2-bd92-4695-8487-1592018c3af9",
        "scheme_id": "d565e4fa-bd11-4fb1-a213-82100ddc78ff",
    }
    row = SimpleNamespace(
        party_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        primary_email="primary@example.com",
        secondary_email="secondary@example.com",
    )
    session = _Session(row)
    current_user = {
        "id": "user-001",
        "user_uuid": "user-001",
        "email": "primary@example.com",
        "full_name": "Owner Example",
        "role": "owner",
        "is_active": True,
        "is_approved": True,
        "created_at": "2026-07-02T00:00:00Z",
    }

    with patch("routers.auth.POSTGRES_AVAILABLE", True), \
         patch("routers.auth.identity_repo.get_scheme_by_number", AsyncMock(return_value=scheme)), \
         patch("routers.auth.async_session_context", return_value=_SessionContext(session)), \
         patch("db_postgres.session.set_tenant", AsyncMock()), \
         patch("routers.auth.user_to_response", side_effect=lambda user: user):
        result = await update_email_preference(
            EmailPreferenceRequest(primary_email="secondary@example.com"),
            current_user=current_user,
            building_id="13195",
        )

    update_calls = [params for sql, params in session.statements if "UPDATE core.parties" in sql]
    assert update_calls == [
        {
            "new_primary": "secondary@example.com",
            "new_secondary": "primary@example.com",
            "party_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "tenant_id": scheme["tenant_id"],
        }
    ]
    assert result["primary_email"] == "secondary@example.com"
    assert result["secondary_email"] == "primary@example.com"


@pytest.mark.asyncio
async def test_update_email_preference_rejects_unknown_email():
    from routers.auth import EmailPreferenceRequest, update_email_preference

    scheme = {
        "tenant_id": "9e9d75c2-bd92-4695-8487-1592018c3af9",
        "scheme_id": "d565e4fa-bd11-4fb1-a213-82100ddc78ff",
    }
    row = SimpleNamespace(
        party_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        primary_email="primary@example.com",
        secondary_email="secondary@example.com",
    )
    session = _Session(row)

    with patch("routers.auth.POSTGRES_AVAILABLE", True), \
         patch("routers.auth.identity_repo.get_scheme_by_number", AsyncMock(return_value=scheme)), \
         patch("routers.auth.async_session_context", return_value=_SessionContext(session)), \
         patch("db_postgres.session.set_tenant", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await update_email_preference(
                EmailPreferenceRequest(primary_email="other@example.com"),
                current_user={"id": "user-001", "email": "primary@example.com"},
                building_id="13195",
            )

    assert exc_info.value.status_code == 400
    assert not any("UPDATE core.parties" in sql for sql, _params in session.statements)


@pytest.mark.asyncio
async def test_update_email_preference_keeps_existing_primary_without_update():
    from routers.auth import EmailPreferenceRequest, update_email_preference

    scheme = {
        "tenant_id": "9e9d75c2-bd92-4695-8487-1592018c3af9",
        "scheme_id": "d565e4fa-bd11-4fb1-a213-82100ddc78ff",
    }
    row = SimpleNamespace(
        party_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        primary_email="primary@example.com",
        secondary_email="secondary@example.com",
    )
    session = _Session(row)
    current_user = {
        "id": "user-001",
        "user_uuid": "user-001",
        "email": "primary@example.com",
        "full_name": "Owner Example",
        "role": "owner",
        "is_active": True,
        "is_approved": True,
        "created_at": "2026-07-02T00:00:00Z",
    }

    with patch("routers.auth.POSTGRES_AVAILABLE", True), \
         patch("routers.auth.identity_repo.get_scheme_by_number", AsyncMock(return_value=scheme)), \
         patch("routers.auth.async_session_context", return_value=_SessionContext(session)), \
         patch("db_postgres.session.set_tenant", AsyncMock()), \
         patch("routers.auth.user_to_response", side_effect=lambda user: user):
        result = await update_email_preference(
            EmailPreferenceRequest(primary_email="primary@example.com"),
            current_user=current_user,
            building_id="13195",
        )

    assert not any("UPDATE core.parties" in sql for sql, _params in session.statements)
    assert result["primary_email"] == "primary@example.com"
    assert result["secondary_email"] == "secondary@example.com"
