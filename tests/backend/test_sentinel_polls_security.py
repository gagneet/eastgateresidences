"""
Sentinel tests: polls router security hardening.

Covers:
  - Route registration
  - Unapproved users are rejected (get_approved_user gate)
  - Explicit building_id filter in list and vote queries (BOLA defense-in-depth)
  - voted_user_ids excluded from public list response (privacy)
  - has_voted computed field present in list response
  - html.escape applied to question and option text (stored XSS prevention)
  - Atomic double-vote protection via update_one filter
  - Closed poll rejects votes
  - Missing full_name falls back to email in audit log (no silent audit drop)
  - Cross-building isolation: poll from building A not visible to building B

Run:
    cd /home/gagneet/strata-management/backend
    venv/bin/python3 -m pytest tests/backend/test_sentinel_polls_security.py -v
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend"))

BUILDING_A = "13195"
BUILDING_B = "16244"


async def _aiter(items):
    """Async generator helper — Motor cursors support 'async for'."""
    for item in items:
        yield item


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def approved_owner():
    return {
        "id": "user-owner-001",
        "full_name": "Jane Owner",
        "email": "jane@example.com",
        "role": "owner",
        "is_approved": True,
        "building_id": BUILDING_A,
    }


@pytest.fixture
def approved_owner_no_name():
    return {
        "id": "user-owner-noname",
        "full_name": "",
        "email": "noname@example.com",
        "role": "owner",
        "is_approved": True,
        "building_id": BUILDING_A,
    }


@pytest.fixture
def unapproved_user():
    return {
        "id": "user-unapproved-001",
        "full_name": "Pending User",
        "email": "pending@example.com",
        "role": "owner",
        "is_approved": False,
        "building_id": BUILDING_A,
    }


@pytest.fixture
def chairman_user():
    return {
        "id": "user-chair-001",
        "full_name": "Chair Person",
        "email": "chair@example.com",
        "role": "ec_member",
        "is_approved": True,
        "building_id": BUILDING_A,
    }


@pytest.fixture
def open_poll():
    return {
        "id": "poll-001",
        "building_id": BUILDING_A,
        "question": "Best facility?",
        "options": [
            {"id": "opt-a", "text": "Pool", "votes": 0},
            {"id": "opt-b", "text": "Gym", "votes": 0},
        ],
        "status": "open",
        "is_anonymous": False,
        "total_votes": 0,
        "voted_user_ids": [],
        "created_by": "user-chair-001",
        "created_at": "2026-04-01T00:00:00+00:00",
        "is_test_data": False,
    }


@pytest.fixture
def closed_poll():
    return {
        "id": "poll-closed-001",
        "building_id": BUILDING_A,
        "question": "Past event?",
        "options": [
            {"id": "opt-x", "text": "Yes", "votes": 5},
            {"id": "opt-y", "text": "No", "votes": 2},
        ],
        "status": "closed",
        "is_anonymous": False,
        "total_votes": 7,
        "voted_user_ids": ["user-owner-001"],
        "created_by": "user-chair-001",
        "created_at": "2026-03-01T00:00:00+00:00",
        "is_test_data": False,
    }


# ─── Route registration ────────────────────────────────────────────────────────

class TestPollsRouterRegistration:
    def test_router_importable(self):
        from routers.polls import router
        assert router is not None

    def test_list_polls_route_registered(self):
        from routers.polls import router
        paths = {r.path for r in router.routes}
        assert "/polls" in paths

    def test_create_poll_route_registered(self):
        from routers.polls import router
        post_routes = [
            r for r in router.routes if "POST" in getattr(r, "methods", [])
        ]
        assert any(r.path == "/polls" for r in post_routes)

    def test_vote_route_registered(self):
        from routers.polls import router
        paths = {r.path for r in router.routes}
        assert "/polls/{poll_id}/vote" in paths


# ─── Approved-user gate ────────────────────────────────────────────────────────

class TestApprovedUserGate:
    """Unapproved users must be rejected with 403."""

    @pytest.mark.asyncio
    async def test_list_polls_rejects_unapproved(self, unapproved_user):
        from fastapi import HTTPException
        from utils.auth import get_approved_user

        with pytest.raises(HTTPException) as exc_info:
            await get_approved_user(current_user=unapproved_user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_approved_owner_passes_gate(self, approved_owner):
        from utils.auth import get_approved_user

        result = await get_approved_user(current_user=approved_owner)
        assert result["id"] == approved_owner["id"]

    @pytest.mark.asyncio
    async def test_list_polls_requires_approved_dependency(self):
        """list_polls endpoint uses get_approved_user as dependency."""
        from routers.polls import list_polls
        import inspect
        sig = inspect.signature(list_polls)
        dep_defaults = [
            p.default for p in sig.parameters.values()
            if hasattr(p.default, "dependency")
        ]
        from utils.auth import get_approved_user
        dep_funcs = [d.dependency for d in dep_defaults]
        assert get_approved_user in dep_funcs

    @pytest.mark.asyncio
    async def test_cast_vote_requires_approved_dependency(self):
        """cast_vote endpoint uses get_approved_user as dependency."""
        from routers.polls import cast_vote
        import inspect
        sig = inspect.signature(cast_vote)
        dep_defaults = [
            p.default for p in sig.parameters.values()
            if hasattr(p.default, "dependency")
        ]
        from utils.auth import get_approved_user
        dep_funcs = [d.dependency for d in dep_defaults]
        assert get_approved_user in dep_funcs


# ─── Privacy: voted_user_ids must not leak ─────────────────────────────────────

class TestVoterPrivacy:
    @pytest.mark.asyncio
    async def test_list_polls_excludes_voted_user_ids(self, approved_owner, open_poll):
        """voted_user_ids must not appear in the public list response."""
        from routers.polls import list_polls

        poll_without_voters = {k: v for k, v in open_poll.items() if k != "voted_user_ids"}
        cursor_mock = MagicMock()
        cursor_mock.sort.return_value = cursor_mock
        cursor_mock.limit.return_value = cursor_mock
        cursor_mock.to_list = AsyncMock(return_value=[poll_without_voters])

        with patch("routers.polls.db") as mock_db:
            mock_db.quick_polls.find.side_effect = [cursor_mock, _aiter([])]
            result = await list_polls(
                current_user=approved_owner,
                building_id=BUILDING_A,
            )

        assert isinstance(result, list)
        for poll in result:
            assert "voted_user_ids" not in poll

    @pytest.mark.asyncio
    async def test_list_polls_includes_has_voted_field(self, approved_owner, open_poll):
        """Response must include has_voted computed boolean."""
        from routers.polls import list_polls

        poll_without_voters = {k: v for k, v in open_poll.items() if k != "voted_user_ids"}
        cursor_mock = MagicMock()
        cursor_mock.sort.return_value = cursor_mock
        cursor_mock.limit.return_value = cursor_mock
        cursor_mock.to_list = AsyncMock(return_value=[poll_without_voters])

        with patch("routers.polls.db") as mock_db:
            mock_db.quick_polls.find.side_effect = [cursor_mock, _aiter([{"id": "poll-001"}])]
            result = await list_polls(
                current_user=approved_owner,
                building_id=BUILDING_A,
            )

        assert len(result) == 1
        assert "has_voted" in result[0]
        assert result[0]["has_voted"] is True


# ─── XSS prevention ───────────────────────────────────────────────────────────

class TestXSSPrevention:
    @pytest.mark.asyncio
    async def test_create_poll_escapes_question(self, chairman_user):
        """html.escape must be applied to question text before persistence."""
        from routers.polls import PollCreate, create_poll

        body = PollCreate(
            question="<script>alert('xss')</script>",
            options=["Yes", "No"],
        )

        captured = {}

        async def fake_insert(doc):
            captured["doc"] = doc
            return MagicMock(inserted_id="fake")

        with patch("routers.polls.db") as mock_db, \
                patch("routers.polls.asyncio.create_task"):
            mock_db.quick_polls.insert_one = AsyncMock(side_effect=fake_insert)
            await create_poll(
                body=body,
                current_user=chairman_user,
                building_id=BUILDING_A,
            )

        assert "<script>" not in captured["doc"]["question"]
        assert "&lt;script&gt;" in captured["doc"]["question"]

    @pytest.mark.asyncio
    async def test_create_poll_escapes_options(self, chairman_user):
        """html.escape must be applied to each option text before persistence."""
        from routers.polls import PollCreate, create_poll

        body = PollCreate(
            question="Choose one",
            options=["<b>Bold</b>", "Normal"],
        )

        captured = {}

        async def fake_insert(doc):
            captured["doc"] = doc
            return MagicMock(inserted_id="fake")

        with patch("routers.polls.db") as mock_db, \
                patch("routers.polls.asyncio.create_task"):
            mock_db.quick_polls.insert_one = AsyncMock(side_effect=fake_insert)
            await create_poll(
                body=body,
                current_user=chairman_user,
                building_id=BUILDING_A,
            )

        option_texts = [o["text"] for o in captured["doc"]["options"]]
        assert "<b>" not in option_texts[0]
        assert "&lt;b&gt;" in option_texts[0]
        assert option_texts[1] == "Normal"


# ─── BOLA: explicit building_id filters ───────────────────────────────────────

class TestExplicitBuildingIdFilters:
    @pytest.mark.asyncio
    async def test_list_polls_passes_building_id_to_find(self, approved_owner, open_poll):
        """list_polls must pass building_id in the find() filter explicitly."""
        from routers.polls import list_polls

        poll_without_voters = {k: v for k, v in open_poll.items() if k != "voted_user_ids"}
        cursor_mock = MagicMock()
        cursor_mock.sort.return_value = cursor_mock
        cursor_mock.limit.return_value = cursor_mock
        cursor_mock.to_list = AsyncMock(return_value=[poll_without_voters])

        with patch("routers.polls.db") as mock_db:
            mock_db.quick_polls.find.side_effect = [cursor_mock, _aiter([])]
            await list_polls(current_user=approved_owner, building_id=BUILDING_A)

        first_call_filter = mock_db.quick_polls.find.call_args_list[0][0][0]
        assert first_call_filter.get("building_id") == BUILDING_A

    @pytest.mark.asyncio
    async def test_cast_vote_find_one_includes_building_id(self, approved_owner, open_poll):
        """cast_vote find_one must include building_id to prevent cross-tenant lookup."""
        from routers.polls import cast_vote

        with patch("routers.polls.db") as mock_db, \
                patch("routers.polls.asyncio.create_task"):
            mock_db.quick_polls.find_one = AsyncMock(return_value=open_poll)
            update_result = MagicMock()
            update_result.modified_count = 1
            mock_db.quick_polls.update_one = AsyncMock(return_value=update_result)

            await cast_vote(
                poll_id="poll-001",
                option_id="opt-a",
                current_user=approved_owner,
                building_id=BUILDING_A,
            )

        find_filter = mock_db.quick_polls.find_one.call_args[0][0]
        assert find_filter.get("building_id") == BUILDING_A

    @pytest.mark.asyncio
    async def test_cast_vote_update_includes_building_id(self, approved_owner, open_poll):
        """cast_vote update_one must include building_id in filter (TOCTOU defense)."""
        from routers.polls import cast_vote

        with patch("routers.polls.db") as mock_db, \
                patch("routers.polls.asyncio.create_task"):
            mock_db.quick_polls.find_one = AsyncMock(return_value=open_poll)
            update_result = MagicMock()
            update_result.modified_count = 1
            mock_db.quick_polls.update_one = AsyncMock(return_value=update_result)

            await cast_vote(
                poll_id="poll-001",
                option_id="opt-a",
                current_user=approved_owner,
                building_id=BUILDING_A,
            )

        update_filter = mock_db.quick_polls.update_one.call_args[0][0]
        assert update_filter.get("building_id") == BUILDING_A


# ─── Vote logic: closed / duplicate / atomic ──────────────────────────────────

class TestVoteLogic:
    @pytest.mark.asyncio
    async def test_cast_vote_rejects_closed_poll(self, approved_owner, closed_poll):
        from fastapi import HTTPException
        from routers.polls import cast_vote

        with patch("routers.polls.db") as mock_db:
            mock_db.quick_polls.find_one = AsyncMock(return_value=closed_poll)
            with pytest.raises(HTTPException) as exc:
                await cast_vote(
                    poll_id="poll-closed-001",
                    option_id="opt-x",
                    current_user=approved_owner,
                    building_id=BUILDING_A,
                )
        assert exc.value.status_code == 400
        assert "closed" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_cast_vote_rejects_duplicate_vote(self, approved_owner, open_poll):
        from fastapi import HTTPException
        from routers.polls import cast_vote

        already_voted = dict(open_poll, voted_user_ids=[approved_owner["id"]])

        with patch("routers.polls.db") as mock_db:
            mock_db.quick_polls.find_one = AsyncMock(return_value=already_voted)
            with pytest.raises(HTTPException) as exc:
                await cast_vote(
                    poll_id="poll-001",
                    option_id="opt-a",
                    current_user=approved_owner,
                    building_id=BUILDING_A,
                )
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_cast_vote_invalid_option_returns_400(self, approved_owner, open_poll):
        """If update modified_count==0 (invalid option_id), 400 is returned."""
        from fastapi import HTTPException
        from routers.polls import cast_vote

        with patch("routers.polls.db") as mock_db:
            mock_db.quick_polls.find_one = AsyncMock(return_value=open_poll)
            update_result = MagicMock()
            update_result.modified_count = 0
            mock_db.quick_polls.update_one = AsyncMock(return_value=update_result)

            with pytest.raises(HTTPException) as exc:
                await cast_vote(
                    poll_id="poll-001",
                    option_id="opt-nonexistent",
                    current_user=approved_owner,
                    building_id=BUILDING_A,
                )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_cast_vote_update_filter_includes_atomic_dedup_guard(
            self, approved_owner, open_poll
    ):
        """update_one filter must include status=open and voted_user_ids $ne guard."""
        from routers.polls import cast_vote

        with patch("routers.polls.db") as mock_db, \
                patch("routers.polls.asyncio.create_task"):
            mock_db.quick_polls.find_one = AsyncMock(return_value=open_poll)
            update_result = MagicMock()
            update_result.modified_count = 1
            mock_db.quick_polls.update_one = AsyncMock(return_value=update_result)

            await cast_vote(
                poll_id="poll-001",
                option_id="opt-a",
                current_user=approved_owner,
                building_id=BUILDING_A,
            )

        update_filter = mock_db.quick_polls.update_one.call_args[0][0]
        assert update_filter.get("status") == "open"
        assert update_filter.get("voted_user_ids") == {"$ne": approved_owner["id"]}

    @pytest.mark.asyncio
    async def test_cast_vote_returns_404_for_missing_poll(self, approved_owner):
        from fastapi import HTTPException
        from routers.polls import cast_vote

        with patch("routers.polls.db") as mock_db:
            mock_db.quick_polls.find_one = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc:
                await cast_vote(
                    poll_id="nonexistent",
                    option_id="opt-a",
                    current_user=approved_owner,
                    building_id=BUILDING_A,
                )
        assert exc.value.status_code == 404


# ─── Audit log fallback ────────────────────────────────────────────────────────

class TestAuditLogFallback:
    @pytest.mark.asyncio
    async def test_create_poll_uses_email_when_full_name_empty(self, chairman_user):
        """user_name in audit log falls back to email when full_name is blank."""
        from routers.polls import PollCreate, create_poll

        user_no_name = dict(chairman_user, full_name="")
        body = PollCreate(question="Test?", options=["A", "B"])

        audit_calls = []
        pending_tasks = []

        async def fake_audit(**kwargs):
            audit_calls.append(kwargs)

        async def fake_insert(doc):
            return MagicMock(inserted_id="fake")

        with patch("routers.polls.db") as mock_db, \
                patch("routers.polls.create_audit_log", side_effect=fake_audit), \
                patch("routers.polls.asyncio.create_task", side_effect=pending_tasks.append):
            mock_db.quick_polls.insert_one = AsyncMock(side_effect=fake_insert)
            await create_poll(body=body, current_user=user_no_name, building_id=BUILDING_A)

        for task in pending_tasks:
            await task

        assert len(audit_calls) == 1
        assert audit_calls[0]["user_name"] == chairman_user["email"]

    @pytest.mark.asyncio
    async def test_cast_vote_uses_email_when_full_name_empty(
            self, approved_owner_no_name, open_poll
    ):
        """vote audit log falls back to email when full_name is blank."""
        from routers.polls import cast_vote

        audit_calls = []
        pending_tasks = []

        async def fake_audit(**kwargs):
            audit_calls.append(kwargs)

        with patch("routers.polls.db") as mock_db, \
                patch("routers.polls.create_audit_log", side_effect=fake_audit), \
                patch("routers.polls.asyncio.create_task", side_effect=pending_tasks.append):
            mock_db.quick_polls.find_one = AsyncMock(return_value=open_poll)
            update_result = MagicMock()
            update_result.modified_count = 1
            mock_db.quick_polls.update_one = AsyncMock(return_value=update_result)

            await cast_vote(
                poll_id="poll-001",
                option_id="opt-a",
                current_user=approved_owner_no_name,
                building_id=BUILDING_A,
            )

        for task in pending_tasks:
            await task

        assert len(audit_calls) == 1
        assert audit_calls[0]["user_name"] == approved_owner_no_name["email"]


# ─── Cross-building isolation ─────────────────────────────────────────────────

class TestCrossBuildingIsolation:
    @pytest.mark.asyncio
    async def test_cast_vote_uses_caller_building_not_poll_building(
            self, approved_owner, open_poll
    ):
        """building_id used in find_one and update_one must come from the
        authenticated session (get_current_building), not from the request body."""
        from routers.polls import cast_vote

        with patch("routers.polls.db") as mock_db, \
                patch("routers.polls.asyncio.create_task"):
            mock_db.quick_polls.find_one = AsyncMock(return_value=None)

            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await cast_vote(
                    poll_id="poll-001",
                    option_id="opt-a",
                    current_user=approved_owner,
                    building_id=BUILDING_B,  # different building than poll
                )
            assert exc.value.status_code == 404

        find_filter = mock_db.quick_polls.find_one.call_args[0][0]
        assert find_filter.get("building_id") == BUILDING_B
