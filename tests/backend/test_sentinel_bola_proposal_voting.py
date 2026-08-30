"""
Sentinel tests: BOLA protection on proposal voting endpoint.

Covers:
  - Owner with active ownership link can vote for their own lot
  - Owner with legacy user_units record (no role_at_unit) can still vote
  - Owner cannot vote for a lot they don't own
  - user_units link with role_at_unit='tenant' is rejected (role_at_unit guard)
  - Super admin can vote for any lot without a user_units link
  - Inactive user_units link is rejected
  - Cross-tenant: ownership in Building B does not grant vote in Building A
  - Duplicate vote is rejected (409)
  - Voting on a closed proposal is rejected (400)
  - Unpermitted role (tenant) is rejected (403)
  - lot_id whitespace is normalised before DB lookup

Architecture note:
  cast_vote() delegates the ownership check to
  ``services.owner_service.is_user_current_owner_of_unit`` which runs its own
  MongoDB/Postgres queries (not through ``routers.proposals.db``).  Tests that
  exercise the BOLA gate mock ``routers.proposals.is_user_current_owner_of_unit``
  directly so the tests stay isolated from the owner-service internals while
  still validating the voting logic.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_sentinel_bola_proposal_voting.py -v
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def owner_user():
    return {
        "id": "user-owner-001",
        "full_name": "Avneet Rooprai",
        "email": "avneet@test.com",
        "role": "owner",
        "building_id": "13195",
        "is_approved": True,
    }


@pytest.fixture
def super_admin_user():
    return {
        "id": "user-admin-001",
        "full_name": "Super Admin",
        "email": "admin@test.com",
        "role": "super_admin",
        "building_id": "13195",
        "is_approved": True,
    }


@pytest.fixture
def tenant_user():
    return {
        "id": "user-tenant-001",
        "full_name": "Tenant User",
        "email": "tenant@test.com",
        "role": "tenant",
        "building_id": "13195",
        "is_approved": True,
    }


@pytest.fixture
def ec_member_user():
    return {
        "id": "user-ec-001",
        "full_name": "EC Member",
        "email": "ec@test.com",
        "role": "ec_member",
        "building_id": "13195",
        "is_approved": True,
    }


@pytest.fixture
def open_proposal():
    return {
        "id": "proposal-001",
        "building_id": "13195",
        "status": "open",
        "title": "Install EV Chargers",
        "votes_for": 0,
        "votes_against": 0,
        "votes_abstain": 0,
    }


@pytest.fixture
def closed_proposal(open_proposal):
    return {**open_proposal, "status": "passed"}


@pytest.fixture
def owner_user_unit():
    """Active owner link for unit 101, building 13195."""
    return {
        "user_id": "user-owner-001",
        "unit_number": "101",
        "building_id": "13195",
        "is_active": True,
        "role_at_unit": "owner",
    }


@pytest.fixture
def legacy_user_unit():
    """Active link without role_at_unit (legacy record)."""
    return {
        "user_id": "user-owner-001",
        "unit_number": "101",
        "building_id": "13195",
        "is_active": True,
        # role_at_unit intentionally absent
    }


def _make_vote_request(lot_id="101", vote="for"):
    req = MagicMock()
    req.lot_id = lot_id
    req.vote = vote
    return req


def _make_mock_db(proposal_doc, existing_vote=None):
    """Build a mock DB for cast_vote's proposal/vote queries.

    The user_units ownership check now goes through owner_service, not directly
    through proposals.db — mock is_user_current_owner_of_unit separately.
    """
    mock_db = MagicMock()
    mock_db.proposals.find_one = AsyncMock(return_value=proposal_doc)
    mock_db.proposal_votes.find_one = AsyncMock(return_value=existing_vote)
    mock_db.proposal_votes.insert_one = AsyncMock(return_value=MagicMock(inserted_id="vote-001"))
    mock_db.proposals.update_one = AsyncMock(return_value=MagicMock())
    return mock_db


# Shorthand patch targets
_OWNERSHIP_FN = "routers.proposals.is_user_current_owner_of_unit"
_PROPOSALS_DB = "routers.proposals.db"


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestCastVoteBOLAProtection:

    @pytest.mark.asyncio
    async def test_owner_with_valid_link_can_vote(self, owner_user, open_proposal):
        """Owner with an active ownership link may vote for their own lot."""
        from routers.proposals import cast_vote
        mock_db = _make_mock_db(open_proposal)
        with patch(_PROPOSALS_DB, mock_db), \
             patch(_OWNERSHIP_FN, new=AsyncMock(return_value=True)):
            result = await cast_vote(
                proposal_id="proposal-001",
                data=_make_vote_request("101", "for"),
                current_user=owner_user,
                building_id="13195",
            )
        assert result is not None
        mock_db.proposal_votes.insert_one.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_owner_with_legacy_link_no_role_at_unit_can_vote(self, owner_user, open_proposal):
        """Owner with a legacy user_units record (no role_at_unit) must still be able to vote.

        The owner_service handles legacy record detection internally; cast_vote sees
        a True result from is_user_current_owner_of_unit regardless.
        """
        from routers.proposals import cast_vote
        mock_db = _make_mock_db(open_proposal)
        with patch(_PROPOSALS_DB, mock_db), \
             patch(_OWNERSHIP_FN, new=AsyncMock(return_value=True)):
            result = await cast_vote(
                proposal_id="proposal-001",
                data=_make_vote_request("101", "for"),
                current_user=owner_user,
                building_id="13195",
            )
        assert result is not None
        mock_db.proposal_votes.insert_one.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_owner_cannot_vote_for_unowned_lot(self, owner_user, open_proposal):
        """Owner must be rejected when they have no ownership link for the requested lot."""
        from routers.proposals import cast_vote
        mock_db = _make_mock_db(open_proposal)
        with patch(_PROPOSALS_DB, mock_db), \
             patch(_OWNERSHIP_FN, new=AsyncMock(return_value=False)):
            with pytest.raises(HTTPException) as exc_info:
                await cast_vote(
                    proposal_id="proposal-001",
                    data=_make_vote_request("202", "for"),
                    current_user=owner_user,
                    building_id="13195",
                )
        assert exc_info.value.status_code == 403
        assert "ownership claim" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_tenant_role_rejected_before_ownership_check(self, tenant_user, open_proposal):
        """Users with the tenant role must be rejected at the role guard, not the ownership check."""
        from routers.proposals import cast_vote
        mock_ownership = AsyncMock(return_value=True)
        mock_db = _make_mock_db(open_proposal)
        with patch(_PROPOSALS_DB, mock_db), \
             patch(_OWNERSHIP_FN, mock_ownership):
            with pytest.raises(HTTPException) as exc_info:
                await cast_vote(
                    proposal_id="proposal-001",
                    data=_make_vote_request("101", "for"),
                    current_user=tenant_user,
                    building_id="13195",
                )
        assert exc_info.value.status_code == 403
        # Ownership service must NOT be called when the role guard fires first
        mock_ownership.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_super_admin_bypasses_ownership_check(self, super_admin_user, open_proposal):
        """Super admin must be able to record a vote for any lot without an ownership link."""
        from routers.proposals import cast_vote
        mock_ownership = AsyncMock(return_value=False)  # would deny — but must not be called
        mock_db = _make_mock_db(open_proposal)
        with patch(_PROPOSALS_DB, mock_db), \
             patch(_OWNERSHIP_FN, mock_ownership):
            result = await cast_vote(
                proposal_id="proposal-001",
                data=_make_vote_request("101", "for"),
                current_user=super_admin_user,
                building_id="13195",
            )
        assert result is not None
        mock_ownership.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_inactive_link_is_rejected(self, owner_user, open_proposal):
        """An inactive user_units record must not grant voting rights.

        The owner_service returns False when the query finds only inactive records.
        """
        from routers.proposals import cast_vote
        mock_db = _make_mock_db(open_proposal)
        with patch(_PROPOSALS_DB, mock_db), \
             patch(_OWNERSHIP_FN, new=AsyncMock(return_value=False)):
            with pytest.raises(HTTPException) as exc_info:
                await cast_vote(
                    proposal_id="proposal-001",
                    data=_make_vote_request("101", "for"),
                    current_user=owner_user,
                    building_id="13195",
                )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_tenant_role_at_unit_cannot_vote(self, owner_user, open_proposal):
        """A user whose user_units link has role_at_unit='tenant' must be denied.

        The owner_service's $or query on role_at_unit filters out tenant links
        and returns False.
        """
        from routers.proposals import cast_vote
        mock_db = _make_mock_db(open_proposal)
        with patch(_PROPOSALS_DB, mock_db), \
             patch(_OWNERSHIP_FN, new=AsyncMock(return_value=False)):
            with pytest.raises(HTTPException) as exc_info:
                await cast_vote(
                    proposal_id="proposal-001",
                    data=_make_vote_request("101", "for"),
                    current_user=owner_user,
                    building_id="13195",
                )
        assert exc_info.value.status_code == 403
        assert "ownership claim" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_cross_tenant_isolation(self, owner_user, open_proposal):
        """Ownership in Building B must not allow voting in Building A.

        is_user_current_owner_of_unit is called with building_id='13195' — if the
        owner only has a link in building '16244', it returns False.
        """
        from routers.proposals import cast_vote
        mock_ownership = AsyncMock(return_value=False)
        mock_db = _make_mock_db(open_proposal)
        with patch(_PROPOSALS_DB, mock_db), \
             patch(_OWNERSHIP_FN, mock_ownership):
            with pytest.raises(HTTPException) as exc_info:
                await cast_vote(
                    proposal_id="proposal-001",
                    data=_make_vote_request("101", "for"),
                    current_user=owner_user,
                    building_id="13195",  # Building A — owner only linked in Building B
                )
        assert exc_info.value.status_code == 403
        # Confirm the ownership check was called with the correct building_id (Building A)
        mock_ownership.assert_awaited_once()
        call_kwargs = mock_ownership.call_args.kwargs
        assert call_kwargs.get("building_id") == "13195"

    @pytest.mark.asyncio
    async def test_duplicate_vote_rejected(self, owner_user, open_proposal):
        """A lot that has already voted must be rejected with 409."""
        from routers.proposals import cast_vote
        existing = {"proposal_id": "proposal-001", "lot_id": "101", "vote": "for"}
        mock_db = _make_mock_db(open_proposal, existing_vote=existing)
        with patch(_PROPOSALS_DB, mock_db), \
             patch(_OWNERSHIP_FN, new=AsyncMock(return_value=True)):
            with pytest.raises(HTTPException) as exc_info:
                await cast_vote(
                    proposal_id="proposal-001",
                    data=_make_vote_request("101", "for"),
                    current_user=owner_user,
                    building_id="13195",
                )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_closed_proposal_rejected(self, owner_user, closed_proposal):
        """Voting on a non-open proposal must be rejected with 400."""
        from routers.proposals import cast_vote
        mock_db = _make_mock_db(closed_proposal)
        with patch(_PROPOSALS_DB, mock_db), \
             patch(_OWNERSHIP_FN, new=AsyncMock(return_value=True)):
            with pytest.raises(HTTPException) as exc_info:
                await cast_vote(
                    proposal_id="proposal-001",
                    data=_make_vote_request("101", "for"),
                    current_user=owner_user,
                    building_id="13195",
                )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_lot_id_whitespace_normalised(self, owner_user, open_proposal):
        """lot_id with surrounding whitespace must be stripped before the ownership lookup."""
        from routers.proposals import cast_vote
        mock_ownership = AsyncMock(return_value=True)
        mock_db = _make_mock_db(open_proposal)
        with patch(_PROPOSALS_DB, mock_db), \
             patch(_OWNERSHIP_FN, mock_ownership):
            await cast_vote(
                proposal_id="proposal-001",
                data=_make_vote_request("  101  ", "for"),
                current_user=owner_user,
                building_id="13195",
            )
        # Verify that the stripped lot_id was passed to the ownership service
        call_kwargs = mock_ownership.call_args.kwargs
        assert call_kwargs.get("unit_identifier") == "101"

    @pytest.mark.asyncio
    async def test_ownership_check_called_with_building_id(self, owner_user, open_proposal):
        """is_user_current_owner_of_unit must be called with building_id to prevent cross-tenant BOLA."""
        from routers.proposals import cast_vote
        mock_ownership = AsyncMock(return_value=True)
        mock_db = _make_mock_db(open_proposal)
        with patch(_PROPOSALS_DB, mock_db), \
             patch(_OWNERSHIP_FN, mock_ownership):
            await cast_vote(
                proposal_id="proposal-001",
                data=_make_vote_request("101", "for"),
                current_user=owner_user,
                building_id="13195",
            )
        mock_ownership.assert_awaited_once()
        call_kwargs = mock_ownership.call_args.kwargs
        assert call_kwargs.get("building_id") == "13195"
        assert call_kwargs.get("user_id") == "user-owner-001"
        assert call_kwargs.get("unit_identifier") == "101"

    @pytest.mark.asyncio
    async def test_ownership_check_called_with_correct_user_id(self, owner_user, open_proposal):
        """is_user_current_owner_of_unit must use the authenticated user's id, not the lot_id."""
        from routers.proposals import cast_vote
        mock_ownership = AsyncMock(return_value=True)
        mock_db = _make_mock_db(open_proposal)
        with patch(_PROPOSALS_DB, mock_db), \
             patch(_OWNERSHIP_FN, mock_ownership):
            await cast_vote(
                proposal_id="proposal-001",
                data=_make_vote_request("101", "for"),
                current_user=owner_user,
                building_id="13195",
            )
        call_kwargs = mock_ownership.call_args.kwargs
        assert call_kwargs.get("user_id") == owner_user["id"]

    @pytest.mark.asyncio
    async def test_ec_member_with_ownership_link_can_vote(self, ec_member_user, open_proposal):
        """EC member who owns a lot must be able to vote for that lot."""
        from routers.proposals import cast_vote
        mock_db = _make_mock_db(open_proposal)
        with patch(_PROPOSALS_DB, mock_db), \
             patch(_OWNERSHIP_FN, new=AsyncMock(return_value=True)):
            result = await cast_vote(
                proposal_id="proposal-001",
                data=_make_vote_request("101", "for"),
                current_user=ec_member_user,
                building_id="13195",
            )
        assert result is not None


# ─── Regression: old tests that verified internal DB call args ────────────────

class TestBOLAImplementationContractViaOwnerService:
    """
    These tests verify that cast_vote delegates the ownership check to
    is_user_current_owner_of_unit and passes the correct arguments.
    They complement the integration-style tests above.
    """

    @pytest.mark.asyncio
    async def test_ownership_fn_not_called_for_tenant_role(self, tenant_user, open_proposal):
        """Role guard fires before the ownership check for tenant role."""
        from routers.proposals import cast_vote
        mock_ownership = AsyncMock(return_value=True)
        mock_db = _make_mock_db(open_proposal)
        with patch(_PROPOSALS_DB, mock_db), \
             patch(_OWNERSHIP_FN, mock_ownership):
            with pytest.raises(HTTPException):
                await cast_vote(
                    proposal_id="proposal-001",
                    data=_make_vote_request("101", "for"),
                    current_user=tenant_user,
                    building_id="13195",
                )
        mock_ownership.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ownership_fn_not_called_for_super_admin(self, super_admin_user, open_proposal):
        """Super admin bypasses the ownership check."""
        from routers.proposals import cast_vote
        mock_ownership = AsyncMock(return_value=False)
        mock_db = _make_mock_db(open_proposal)
        with patch(_PROPOSALS_DB, mock_db), \
             patch(_OWNERSHIP_FN, mock_ownership):
            await cast_vote(
                proposal_id="proposal-001",
                data=_make_vote_request("101", "for"),
                current_user=super_admin_user,
                building_id="13195",
            )
        mock_ownership.assert_not_awaited()
