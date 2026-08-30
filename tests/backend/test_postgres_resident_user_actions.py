# @featuretrace:user-management — admin actions must find PostgreSQL-resident users.
# Layer: test
# Data flow: server.resolve_target_user_and_membership -> Mongo + core.users (building-scoped).
# Related: backend/server.py
#          backend/routers/auth.py (admin_reset_user_passwords)
"""Every id GET /users renders from PostgreSQL was unknown to the actions beside it.

Measured live on East Gate 2026-08-29: all 125 active `core.users` rows have no matching
MongoDB `users` document — the two stores assign different ids to the same person
(footgun #24). `POST /users/{id}/request-profile-info` therefore returned 404 for every
user the admin page listed from PostgreSQL, which is the reported bug.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_postgres_resident_user_actions.py -q
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
SERVER = ROOT / "backend" / "server.py"
AUTH = ROOT / "backend" / "routers" / "auth.py"

# Handlers that took a MongoDB-only lookup and 404'd on PostgreSQL-resident users.
PREVIOUSLY_BROKEN = (
    "elevate_user", "reject_user", "request_user_info",
    "request_profile_info", "owner_registration_decision",
)


def _handler_source(name: str) -> str:
    tree = ast.parse(SERVER.read_text())
    lines = SERVER.read_text().splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    raise AssertionError(f"handler {name} not found")


class TestEveryAdminActionResolvesBothStores:
    @pytest.mark.parametrize("handler", PREVIOUSLY_BROKEN)
    def test_handler_uses_the_shared_resolver(self, handler):
        assert "resolve_target_user_and_membership" in _handler_source(handler), (
            f"{handler} still looks the user up in MongoDB only, so it 404s on every "
            f"PostgreSQL-resident user the admin page lists"
        )

    @pytest.mark.parametrize("handler", PREVIOUSLY_BROKEN)
    def test_handler_no_longer_does_its_own_mongo_lookup(self, handler):
        source = _handler_source(handler)
        assert 'db.memberships.find_one({"building_id": building_id, "user_id": user_id})' not in source


class TestTheResolver:
    @pytest.mark.asyncio
    async def test_a_mongo_user_is_returned_without_touching_postgres(self):
        """Unpromoted, Mongo-only buildings must stay on exactly their current path."""
        import server

        fake_db = MagicMock()
        fake_db.memberships.find_one = AsyncMock(return_value={"user_id": "u1"})
        fake_db.users.find_one = AsyncMock(return_value={"id": "u1", "email": "a@b.c"})
        pg = AsyncMock()
        with patch.object(server, "db", fake_db), \
             patch("db_postgres.repos.identity_repo.find_user_by_id_for_admin", new=pg):
            user, membership = await server.resolve_target_user_and_membership("u1", "13195")
        assert user["id"] == "u1" and membership
        pg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_postgres_only_user_is_found(self):
        """The reported bug: present in core.users, absent from Mongo entirely."""
        import server

        fake_db = MagicMock()
        fake_db.memberships.find_one = AsyncMock(return_value=None)
        fake_db.users.find_one = AsyncMock(return_value=None)
        with patch.object(server, "db", fake_db), \
             patch("db_postgres.repos.identity_repo.find_user_by_id_for_admin",
                   new=AsyncMock(return_value={"id": "pg1", "email": "x@y.z"})), \
             patch.object(server, "_pg_membership_for_building",
                          new=AsyncMock(return_value={"role": "strata_manager"})):
            user, membership = await server.resolve_target_user_and_membership("pg1", "13195")
        assert user["email"] == "x@y.z"
        assert membership is not None

    @pytest.mark.asyncio
    async def test_an_unknown_user_is_still_absent_from_both(self):
        import server

        fake_db = MagicMock()
        fake_db.memberships.find_one = AsyncMock(return_value=None)
        fake_db.users.find_one = AsyncMock(return_value=None)
        with patch.object(server, "db", fake_db), \
             patch("db_postgres.repos.identity_repo.find_user_by_id_for_admin",
                   new=AsyncMock(return_value=None)):
            user, membership = await server.resolve_target_user_and_membership("nope", "13195")
        assert user is None and membership is None

    @pytest.mark.asyncio
    async def test_an_unreachable_postgres_leaves_the_mongo_answer_standing(self):
        """Not an error: an unpromoted building has no Postgres answer to give."""
        import server

        fake_db = MagicMock()
        fake_db.memberships.find_one = AsyncMock(return_value={"user_id": "u1"})
        fake_db.users.find_one = AsyncMock(return_value=None)
        with patch.object(server, "db", fake_db), \
             patch("db_postgres.repos.identity_repo.find_user_by_id_for_admin",
                   new=AsyncMock(side_effect=RuntimeError("pg down"))):
            user, membership = await server.resolve_target_user_and_membership("u1", "13195")
        assert user is None
        assert membership == {"user_id": "u1"}


class TestWritesAssertTheyLanded:
    def test_elevation_fails_loudly_when_the_mongo_row_does_not_exist(self):
        """update_one does not raise on zero matches. Elevation lives on the Mongo
        document, so for a PG-only user it silently did nothing and the handler then
        built a response from None."""
        source = _handler_source("elevate_user")
        assert "_elevate_result.matched_count == 0" in source
        assert "status_code=409" in source

    def test_the_admin_password_reset_writes_postgres(self):
        """Login resolves core.users FIRST. A Mongo-only password write updates a record
        authentication never consults — the admin is told it worked, the old password
        keeps working and the new one does not."""
        source = AUTH.read_text()
        assert "set_password_hash as _pg_set_password" in source
        assert "_pg_ok = await _pg_set_password" in source

    def test_the_admin_password_reset_finds_postgres_only_users(self):
        source = AUTH.read_text()
        assert "find_user_for_auth as _pg_find" in source

    def test_a_reset_that_lands_in_neither_store_is_an_error_not_a_success(self):
        source = AUTH.read_text()
        assert "did not land in either store" in source


class TestTheSamePersonHasTwoIds:
    """The audit finding that the id-only fix was incomplete.

    "All 125 active core.users rows have no matching Mongo document" is true BY ID and
    misleading as a description: 120 of those 125 emails DO exist in Mongo, 116 of them
    with a membership. Only FIVE accounts are genuinely PostgreSQL-only.

    So an id-only resolution fixes the 404 and then quietly gets everything downstream
    wrong — it reports "PostgreSQL only" for people whose Mongo row is sitting there, and
    every write keyed on the PostgreSQL uuid matches nothing. footgun #24 says exactly
    this: match on id OR email, then resolve the Mongo row's OWN id before writing.
    """

    @pytest.mark.asyncio
    async def test_a_same_email_mongo_row_is_found_and_its_id_carried(self):
        import server

        fake_db = MagicMock()
        fake_db.memberships.find_one = AsyncMock(
            side_effect=[None, {"user_id": "mongo-id", "role": "strata_manager"}]
        )
        fake_db.users.find_one = AsyncMock(
            side_effect=[None, {"id": "mongo-id", "email": "a@b.c"}]
        )
        with patch.object(server, "db", fake_db), \
             patch("db_postgres.repos.identity_repo.find_user_by_id_for_admin",
                   new=AsyncMock(return_value={"id": "pg-uuid", "email": "a@b.c"})):
            user, membership = await server.resolve_target_user_and_membership("pg-uuid", "13195")

        assert user["id"] == "pg-uuid", "PostgreSQL stays the system of record"
        assert user["mongo_id"] == "mongo-id", "but the Mongo id must be carried for writes"
        assert membership is not None, "the membership under the Mongo id counts"

    @pytest.mark.asyncio
    async def test_a_genuinely_postgres_only_account_carries_no_mongo_id(self):
        """Five accounts really are PG-only; they must be distinguishable from the 120."""
        import server

        fake_db = MagicMock()
        fake_db.memberships.find_one = AsyncMock(return_value=None)
        fake_db.users.find_one = AsyncMock(return_value=None)
        with patch.object(server, "db", fake_db), \
             patch("db_postgres.repos.identity_repo.find_user_by_id_for_admin",
                   new=AsyncMock(return_value={"id": "pg-uuid", "email": "solo@b.c"})), \
             patch.object(server, "_pg_membership_for_building",
                          new=AsyncMock(return_value={"role": "owner"})):
            user, _ = await server.resolve_target_user_and_membership("pg-uuid", "13195")

        assert user.get("mongo_id") is None

    @pytest.mark.asyncio
    async def test_a_mongo_resident_user_is_not_given_a_redundant_email_lookup(self):
        """When the id lookup already found the Mongo row there is nothing to reconcile."""
        import server

        fake_db = MagicMock()
        fake_db.memberships.find_one = AsyncMock(return_value={"user_id": "u1"})
        fake_db.users.find_one = AsyncMock(return_value={"id": "u1", "email": "a@b.c"})
        with patch.object(server, "db", fake_db):
            user, membership = await server.resolve_target_user_and_membership("u1", "13195")

        assert user["id"] == "u1"
        assert "mongo_id" not in user
        assert fake_db.users.find_one.await_count == 1, "no second lookup needed"


class TestWritesTargetTheMongoRowsOwnId:
    def test_elevation_writes_the_mongo_id_not_the_postgres_uuid(self):
        source = _handler_source("elevate_user")
        assert '_mongo_target_id = target.get("mongo_id")' in source
        assert 'db.users.update_one(\n        {"id": _mongo_target_id}' in source or \
               '{"id": _mongo_target_id}' in source

    def test_the_409_no_longer_claims_postgres_only_for_everyone(self):
        """That message was false for 120 of 125 users."""
        source = _handler_source("elevate_user")
        assert "exists only in PostgreSQL" not in source
        assert "no MongoDB record" in source
