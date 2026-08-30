"""PUT /users/{id} must work for a Postgres-resident user.

Reported live 2026-08-28: assigning an EC position from /admin/users returned
404 for every EC member.

    PUT /api/users/51c9d008-20fd-5e5e-b7cf-a07a0befcf20 -> 404

GET /users unions both stores for a promoted building, so the admin list showed
people the update handler then could not find. Measured on East Gate: 119 of 119
active core.users rows had no Mongo `memberships` document and no Mongo `users`
document — all five EC members among them.

There were THREE separate 404s on that path, and fixing fewer than all three
leaves the bug: the membership gate, the user lookup, and the Mongo write's own
matched_count check.
"""
from unittest.mock import AsyncMock, patch

import pytest


class TestPgMembershipHelper:
    """_pg_membership_for_building resolves membership the way GET /users does."""

    @pytest.mark.asyncio
    async def test_active_role_assignment_is_membership(self):
        from server import _pg_membership_for_building
        uid = "51c9d008-20fd-5e5e-b7cf-a07a0befcf20"
        with patch("db_postgres.repos.identity_repo.get_scheme_by_number",
                   new=AsyncMock(return_value={"scheme_id": "d565e4fa-bd11-4fb1-a213-82100ddc78ff",
                                               "tenant_id": "9e9d75c2-bd92-4695-8487-1592018c3af9"})), \
             patch("db_postgres.repos.identity_repo.is_user_in_scheme",
                   new=AsyncMock(return_value=True)):
            result = await _pg_membership_for_building(uid, "13195")
        assert result is not None
        assert result["source"] == "postgres.user_role_assignments"

    @pytest.mark.asyncio
    async def test_no_active_assignment_is_not_membership(self):
        from server import _pg_membership_for_building
        with patch("db_postgres.repos.identity_repo.get_scheme_by_number",
                   new=AsyncMock(return_value={"scheme_id": "s", "tenant_id": "t"})), \
             patch("db_postgres.repos.identity_repo.is_user_in_scheme",
                   new=AsyncMock(return_value=False)):
            result = await _pg_membership_for_building(
                "51c9d008-20fd-5e5e-b7cf-a07a0befcf20", "13195")
        assert result is None

    @pytest.mark.asyncio
    async def test_building_with_no_postgres_scheme_yields_none(self):
        from server import _pg_membership_for_building
        with patch("db_postgres.repos.identity_repo.get_scheme_by_number",
                   new=AsyncMock(return_value=None)):
            result = await _pg_membership_for_building(
                "51c9d008-20fd-5e5e-b7cf-a07a0befcf20", "99999")
        assert result is None

    @pytest.mark.asyncio
    async def test_legacy_mongo_id_short_circuits_before_postgres(self):
        """Mongo-only buildings use non-UUID ids.

        Without the guard every such update reaches asyncpg, fails to bind, and logs a
        full SQL traceback as a WARNING for what is simply "not a Postgres building".
        The scheme lookup must not even be attempted.
        """
        from server import _pg_membership_for_building
        scheme_lookup = AsyncMock(return_value={"scheme_id": "s", "tenant_id": "t"})
        with patch("db_postgres.repos.identity_repo.get_scheme_by_number", new=scheme_lookup):
            result = await _pg_membership_for_building("legacy-mongo-id", "13195")
        assert result is None
        scheme_lookup.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_postgres_failure_is_not_fatal(self):
        """An unreachable Postgres means 'no PG membership', never a 500.

        Mongo-only buildings must keep working when the PG path is unavailable.
        """
        from server import _pg_membership_for_building
        with patch("db_postgres.repos.identity_repo.get_scheme_by_number",
                   new=AsyncMock(side_effect=RuntimeError("connection refused"))):
            result = await _pg_membership_for_building(
                "51c9d008-20fd-5e5e-b7cf-a07a0befcf20", "13195")
        assert result is None


class TestHandlerStillGuardsTheThreeGates:
    """Static guards: each of the three 404s must stay fixed.

    Asserted against the source because exercising the full handler needs the whole
    capability/permission dependency stack; these pin the specific lines that
    regressed, and each one alone reintroduces the bug.
    """

    @staticmethod
    def _handler_source() -> str:
        """Exactly the update_user handler — bounded by the next route decorator.

        A fixed character window silently truncated before the write section and made
        two of these tests assert against source they had never read.
        """
        from pathlib import Path
        src = (Path(__file__).resolve().parents[2] / "backend" / "server.py").read_text(encoding="utf-8")
        start = src.index('@api_router.put("/users/{user_id}", response_model=UserResponse)')
        nxt = src.index("\n@api_router.", start + 1)
        body = src[start:nxt]
        assert "db.users.update_one" in body, "handler slice missed the write section"
        return body

    def test_membership_gate_falls_back_to_postgres(self):
        src = self._handler_source()
        assert "_pg_membership_for_building(user_id, building_id)" in src, (
            "the membership gate no longer consults Postgres; every PG-resident user "
            "will 404 with 'User not found in this building' again"
        )

    def test_user_lookup_falls_back_to_postgres(self):
        src = self._handler_source()
        assert "find_user_by_id_for_admin as _pg_find_user" in src

    def test_mongo_matched_count_zero_is_not_a_404_for_a_pg_user(self):
        src = self._handler_source()
        assert "pg_only_user = result.matched_count == 0 and pg_user is not None" in src
        assert "if result.matched_count == 0 and not pg_only_user:" in src, (
            "an unconditional matched_count==0 -> 404 is back; the Mongo write rejects "
            "Postgres-resident users even once the gates above admit them"
        )

    def test_pg_profile_write_is_awaited_for_a_pg_only_user(self):
        """Fire-and-forget would report success without knowing the write landed.

        update_user_profile() signals failure by returning False rather than raising
        (footgun #23), so an unchecked create_task turns a silent failure into a 200.
        """
        src = self._handler_source()
        assert "if not await _pg_update_user(user_id, _pg_sync_fields):" in src
