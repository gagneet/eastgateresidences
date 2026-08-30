from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, _length):
        return self.rows


def _permissions(can_chat=True):
    return SimpleNamespace(can_chat=can_chat)


@pytest.mark.asyncio
async def test_create_conversation_rejects_stale_directory_member_without_leaking_id():
    from models.communication import ConversationCreate
    from routers.communication import create_conversation

    current_user = {"id": "user-current", "full_name": "Current User", "role": "owner"}
    data = ConversationCreate(is_group=False, member_ids=["stale-user-id"])

    with patch("routers.communication.get_user_permissions", return_value=_permissions()), \
         patch("routers.communication.db") as mock_db:
        mock_db.memberships.distinct = AsyncMock(return_value=["stale-user-id"])
        mock_db.conversations.find_one = AsyncMock(return_value=None)
        mock_db.users.find.return_value = _Cursor([
            {"id": "user-current", "full_name": "Current User", "profile_image": None},
        ])

        with pytest.raises(HTTPException) as exc_info:
            await create_conversation(data, current_user=current_user, building_id="13195")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "This resident is no longer available for chat. Refresh the directory and try again."
    assert "stale-user-id" not in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_conversation_reuses_existing_after_validating_members():
    from models.communication import ConversationCreate
    from routers.communication import create_conversation

    current_user = {"id": "user-current", "full_name": "Current User", "role": "owner"}
    data = ConversationCreate(is_group=False, member_ids=["resident-1"])
    existing = {
        "id": "conv-1",
        "building_id": "13195",
        "is_group": False,
        "member_ids": ["user-current", "resident-1"],
        "created_by": "user-current",
        "last_message": None,
        "created_at": "2026-07-14T00:00:00+00:00",
        "updated_at": "2026-07-14T00:00:00+00:00",
    }

    with patch("routers.communication.get_user_permissions", return_value=_permissions()), \
         patch("routers.communication.db") as mock_db:
        mock_db.memberships.distinct = AsyncMock(return_value=["resident-1"])
        mock_db.users.find.return_value = _Cursor([
            {"id": "user-current", "full_name": "Current User", "profile_image": None},
            {"id": "resident-1", "full_name": "Resident One", "profile_image": None},
        ])
        mock_db.conversations.find_one = AsyncMock(return_value=existing)
        mock_db.conversations.insert_one = AsyncMock()

        result = await create_conversation(data, current_user=current_user, building_id="13195")

    assert result.id == "conv-1"
    assert {m["id"] for m in result.members} == {"user-current", "resident-1"}
    mock_db.conversations.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_create_conversation_rejects_self_chat_before_database_writes():
    from models.communication import ConversationCreate
    from routers.communication import create_conversation

    current_user = {"id": "user-current", "full_name": "Current User", "role": "owner"}
    data = ConversationCreate(is_group=False, member_ids=["user-current"])

    with patch("routers.communication.get_user_permissions", return_value=_permissions()), \
         patch("routers.communication.db") as mock_db:
        mock_db.conversations.insert_one = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await create_conversation(data, current_user=current_user, building_id="13195")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Select another resident to start a chat"
    mock_db.conversations.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_directory_response_includes_chat_user_id():
    from server import get_resident_directory

    current_user = {"id": "user-current", "full_name": "Current User", "role": "owner"}
    directory_rows = [{
        "id": "resident-1",
        "chat_user_id": "resident-1",
        "building_id": "13195",
        "full_name": "Resident One",
        "unit_number": "UA101",
    }]
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=directory_rows)

    with patch("server.db") as mock_db:
        mock_db.memberships.aggregate = AsyncMock(return_value=cursor)
        result = await get_resident_directory(current_user=current_user, building_id="13195")

    assert result[0]["chat_user_id"] == result[0]["id"]
    assert result[0]["building_id"] == "13195"


@pytest.mark.asyncio
async def test_create_conversation_succeeds_when_initiator_has_no_mongo_user_document():
    """A Postgres-only initiator must still be able to start a chat.

    Identity is served by PostgreSQL for any building whose `identity_core` is
    promoted, and a `core.users` row is not guaranteed to have a `db.users` mirror.
    Super-admins in particular live in the PLATFORM tenant, not the building's, and
    may have no MongoDB user document at all — verified live 2026-08-28:
    `administrator@strataos.live` (the account in the reported 400) has no db.users
    row by id or by email, while `core.users` holds the row that authenticated the
    request.

    The old code resolved EVERY member, including the caller, from `db.users` and
    rejected the request when any id was unresolved. The caller's own id was the
    unresolved one, so `/community/directory` returned
    400 "This resident is no longer available for chat" — blaming the resident for a
    problem entirely on the initiator's side. Replayed against live data before the
    fix, `missing_member_ids` was exactly `[<the admin's own id>]`.

    `get_approved_user` has already established who the caller is; re-deriving that
    from a mirror store can only lose information.
    """
    from models.communication import ConversationCreate
    from routers.communication import create_conversation

    # No `db.users` document exists for this id — only the authenticated identity.
    current_user = {"id": "pg-only-admin", "full_name": "Administrator", "role": "super_admin"}
    data = ConversationCreate(is_group=False, member_ids=["resident-1"])

    with patch("routers.communication.get_user_permissions", return_value=_permissions()), \
         patch("routers.communication.db") as mock_db:
        mock_db.memberships.distinct = AsyncMock(return_value=["resident-1"])
        # Only the resident comes back; the initiator is absent, as in production.
        mock_db.users.find.return_value = _Cursor([
            {"id": "resident-1", "full_name": "Resident One", "profile_image": None},
        ])
        mock_db.conversations.find_one = AsyncMock(return_value=None)
        mock_db.conversations.insert_one = AsyncMock()

        result = await create_conversation(data, current_user=current_user, building_id="13195")

    assert {m["id"] for m in result.members} == {"pg-only-admin", "resident-1"}
    initiator = next(m for m in result.members if m["id"] == "pg-only-admin")
    assert initiator["full_name"] == "Administrator", (
        "The initiator's display name must come from the authenticated user, not a "
        "placeholder — the mirror row that would have supplied it does not exist."
    )
    mock_db.conversations.insert_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_conversation_still_rejects_unresolvable_participant_for_pg_only_initiator():
    """The initiator exemption must not weaken validation of the OTHER participant.

    Auto-resolving the caller is specifically about the caller. A requested
    participant that cannot be resolved is still a hard 400 — otherwise a stale
    directory row would open a conversation nobody can be addressed in.
    """
    from models.communication import ConversationCreate
    from routers.communication import create_conversation

    current_user = {"id": "pg-only-admin", "full_name": "Administrator", "role": "super_admin"}
    data = ConversationCreate(is_group=False, member_ids=["stale-user-id"])

    with patch("routers.communication.get_user_permissions", return_value=_permissions()), \
         patch("routers.communication.db") as mock_db:
        mock_db.memberships.distinct = AsyncMock(return_value=["stale-user-id"])
        mock_db.users.find.return_value = _Cursor([])  # neither party resolves
        mock_db.conversations.find_one = AsyncMock(return_value=None)
        mock_db.conversations.insert_one = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await create_conversation(data, current_user=current_user, building_id="13195")

    assert exc_info.value.status_code == 400
    mock_db.conversations.insert_one.assert_not_called()
