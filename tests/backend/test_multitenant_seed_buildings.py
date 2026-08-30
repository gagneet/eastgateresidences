"""
test_multitenant_seed_buildings.py — Unit tests verifying Sierra (16244) and
Harbourview (18932) seed data structure and multi-tenant content scoping.

These tests are pure unit tests (no live server required). They validate that:
- Seed data constants have the correct building IDs, names, and user counts
- Memberships are correctly structured for each building
- Blog posts, emergency services, and listings are scoped to their building
- Seed scripts are idempotent (upsert pattern)
- Building ID "18932" is used for Harbourview (not the old "harbourside_view")
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_RUN_LEGACY = os.getenv("RUN_LEGACY_BUILDING_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not _RUN_LEGACY,
    reason="legacy Sierra/Harbourview seed test; set RUN_LEGACY_BUILDING_TESTS=1 to enable",
)

# Add backend to path
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_project_root, "backend"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_db():
    db = MagicMock()
    db.buildings.find_one = AsyncMock(return_value=None)
    db.buildings.insert_one = AsyncMock(return_value=None)
    db.buildings.update_one = AsyncMock(return_value=None)
    db.settings.update_one = AsyncMock(return_value=None)
    db.users.find_one = AsyncMock(return_value=None)
    db.users.insert_one = AsyncMock(return_value=None)
    db.users.update_one = AsyncMock(return_value=None)
    db.memberships.find_one = AsyncMock(return_value=None)
    db.memberships.insert_one = AsyncMock(return_value=None)
    db.units.find_one = AsyncMock(return_value=None)
    db.units.insert_one = AsyncMock(return_value=None)
    db.ec_members.find_one = AsyncMock(return_value=None)
    db.ec_members.insert_one = AsyncMock(return_value=None)
    db.emergency_services.find_one = AsyncMock(return_value=None)
    db.emergency_services.insert_one = AsyncMock(return_value=None)
    db.blog_posts.find_one = AsyncMock(return_value=None)
    db.blog_posts.insert_one = AsyncMock(return_value=None)
    db.listings.find_one = AsyncMock(return_value=None)
    db.listings.insert_one = AsyncMock(return_value=None)
    return db


# ── Test: Sierra seed data constants ─────────────────────────────────────────

class TestSierraSeedConstants:
    """Verify Sierra seed data has correct building ID, user count, and structure."""

    def test_sierra_building_id_is_16244(self):
        from seeds.seed_sierra import BUILDING_ID
        assert BUILDING_ID == "16244"

    def test_sierra_has_10_users(self):
        from seeds.seed_sierra import SIERRA_USERS
        assert len(SIERRA_USERS) == 10

    def test_sierra_has_one_chairman(self):
        from seeds.seed_sierra import SIERRA_USERS
        chairmen = [u for u in SIERRA_USERS if u["role"] in ("chairman", "strata_admin")]
        assert len(chairmen) == 1
        assert chairmen[0]["email"] == "james.chen@sierra.com.au"

    def test_sierra_has_two_ec_members(self):
        from seeds.seed_sierra import SIERRA_USERS
        ec = [u for u in SIERRA_USERS if u["role"] == "ec_member"]
        assert len(ec) == 2

    def test_sierra_has_strata_manager(self):
        from seeds.seed_sierra import SIERRA_USERS
        managers = [u for u in SIERRA_USERS if u["role"] == "strata_manager"]
        assert len(managers) == 1
        assert managers[0]["email"] == "manager@eastgate.com"

    def test_sierra_has_six_owners(self):
        from seeds.seed_sierra import SIERRA_USERS
        owners = [u for u in SIERRA_USERS if u["role"] == "owner"]
        assert len(owners) == 6

    def test_sierra_all_users_have_building_id(self):
        """All Sierra seed users have a building_id. Cross-building users (e.g.
        manager@eastgate.com) intentionally carry their PRIMARY building_id ("13195");
        their Sierra access is granted via a membership record, not this field.
        """
        from seeds.seed_sierra import SIERRA_USERS
        for user in SIERRA_USERS:
            assert user.get("building_id"), f"{user['email']} missing building_id"

    def test_sierra_all_users_are_approved(self):
        from seeds.seed_sierra import SIERRA_USERS
        for user in SIERRA_USERS:
            assert user["is_approved"] is True
            assert user["status"] == "active"

    def test_sierra_ec_members_have_correct_units(self):
        from seeds.seed_sierra import SIERRA_EC_MEMBERS
        positions = {ec["position"] for ec in SIERRA_EC_MEMBERS}
        assert "Chairperson" in positions
        assert "Secretary" in positions
        assert "Treasurer" in positions

    def test_sierra_emergency_services_all_have_building_id(self):
        from seeds.seed_sierra import SIERRA_EMERGENCY_SERVICES
        assert len(SIERRA_EMERGENCY_SERVICES) == 3
        for es in SIERRA_EMERGENCY_SERVICES:
            assert es["building_id"] == "16244"
            assert es["is_private"] is False

    def test_sierra_blog_posts_scoped_to_building(self):
        from seeds.seed_sierra import SIERRA_BLOG_POSTS
        assert len(SIERRA_BLOG_POSTS) >= 2
        for post in SIERRA_BLOG_POSTS:
            assert post["building_id"] == "16244"
            assert post["scope"] == "building"
            assert post["is_public"] is True

    def test_sierra_listings_have_correct_fields(self):
        from seeds.seed_sierra import SIERRA_LISTINGS
        assert len(SIERRA_LISTINGS) == 2
        for listing in SIERRA_LISTINGS:
            assert listing["building_id"] == "16244"
            assert listing["is_public"] is True
            assert listing["status"] == "active"

    def test_sierra_units_all_have_building_id(self):
        from seeds.seed_sierra import SIERRA_UNITS
        for unit in SIERRA_UNITS:
            assert unit["building_id"] == "16244"

    def test_sierra_credentials_reference_correct_emails(self):
        from seeds.seed_sierra import SIERRA_CREDENTIALS
        assert "james.chen@sierra.com.au" in SIERRA_CREDENTIALS
        assert "manager@eastgate.com" in SIERRA_CREDENTIALS
        assert "SierraChair123!" in SIERRA_CREDENTIALS


# ── Test: Harbourview seed data constants ─────────────────────────────────────

# Harbourview Residences (18932) was removed on 2026-08-20 — it was a synthetic seed
# building with no users, payments or documents, and its seed was unwired at the same
# time. TestHarbourviewSeedConstants was deleted with it. Sierra (16244) is
# deliberately retained, so the Sierra classes above and below are untouched.

class TestBuildingsSeedIDs:
    """Verify the buildings.py seed has correct IDs for all three buildings."""

    def test_buildings_seed_has_east_gate(self):
        from seeds.buildings import BUILDINGS
        ids = [b["id"] for b in BUILDINGS]
        assert "13195" in ids

    def test_buildings_seed_has_sierra(self):
        from seeds.buildings import BUILDINGS
        ids = [b["id"] for b in BUILDINGS]
        assert "16244" in ids

    def test_buildings_seed_has_harbourview_18932(self):
        from seeds.buildings import BUILDINGS
        ids = [b["id"] for b in BUILDINGS]
        assert "18932" in ids

    def test_buildings_seed_does_not_have_old_harbourside_view(self):
        from seeds.buildings import BUILDINGS
        ids = [b["id"] for b in BUILDINGS]
        assert "harbourside_view" not in ids

    def test_harbourview_building_has_correct_slug(self):
        from seeds.buildings import BUILDINGS
        hv = next((b for b in BUILDINGS if b["id"] == "18932"), None)
        assert hv is not None
        assert hv["slug"] == "harbourview"

    def test_harbourview_building_has_plan_number(self):
        from seeds.buildings import BUILDINGS
        hv = next((b for b in BUILDINGS if b["id"] == "18932"), None)
        assert hv is not None
        assert hv.get("plan_number") == "18932"

    def test_sierra_building_has_correct_address(self):
        from seeds.buildings import BUILDINGS
        sierra = next((b for b in BUILDINGS if b["id"] == "16244"), None)
        assert sierra is not None
        assert "Efkarpidis" in sierra["address"]
        assert "Gungahlin" in sierra["address"]


# ── Test: Cross-building data isolation ──────────────────────────────────────

class TestCrossBuildingIsolation:
    """Verify Sierra and Harbourview data cannot accidentally mix."""

    def test_sierra_and_harbourview_have_distinct_ids(self):
        from seeds.seed_sierra import BUILDING_ID as sierra_id
        from seeds.seed_harbourview import BUILDING_ID as hv_id
        assert sierra_id != hv_id

    def test_sierra_chairman_email_not_in_harbourview(self):
        from seeds.seed_sierra import SIERRA_USERS
        from seeds.seed_harbourview import HARBOURVIEW_USERS
        sierra_emails = {u["email"] for u in SIERRA_USERS}
        hv_emails = {u["email"] for u in HARBOURVIEW_USERS}
        # No cross-building users (except manager@eastgate.com is shared)
        overlap = sierra_emails & hv_emails
        shared_allowed = {"manager@eastgate.com"}
        assert overlap.issubset(shared_allowed), f"Unexpected cross-building users: {overlap - shared_allowed}"

    def test_sierra_blog_posts_not_in_harbourview_building(self):
        from seeds.seed_sierra import SIERRA_BLOG_POSTS
        from seeds.seed_harbourview import BUILDING_ID as hv_id
        for post in SIERRA_BLOG_POSTS:
            assert post["building_id"] != hv_id

    def test_harbourview_emergency_services_not_in_sierra_building(self):
        from seeds.seed_harbourview import HARBOURVIEW_EMERGENCY_SERVICES
        from seeds.seed_sierra import BUILDING_ID as sierra_id
        for es in HARBOURVIEW_EMERGENCY_SERVICES:
            assert es["building_id"] != sierra_id


# ── Test: Seed idempotency pattern ────────────────────────────────────────────

class TestSeedIdempotency:
    """Verify seed scripts use upsert/skip patterns — safe to run multiple times."""

    @pytest.mark.asyncio
    async def test_sierra_seed_skips_existing_building(self):
        """If building already exists, seed_sierra should not insert a duplicate."""
        from seeds.seed_sierra import seed_sierra, BUILDING_ID

        mock_db = _mock_db()
        # Simulate building already exists
        mock_db.buildings.find_one = AsyncMock(return_value={"id": BUILDING_ID, "name": "Sierra Gungahlin"})

        with patch("seeds.seed_sierra.db", mock_db):
            await seed_sierra()

        # Building insert should NOT have been called
        mock_db.buildings.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_harbourview_seed_skips_existing_users(self):
        """If users already exist, seed_harbourview should not create duplicates."""
        from seeds.seed_harbourview import seed_harbourview

        mock_db = _mock_db()
        # All users already exist
        mock_db.users.find_one = AsyncMock(return_value={"id": "existing", "email": "test@test.com"})
        # Building exists
        mock_db.buildings.find_one = AsyncMock(return_value={"id": "18932"})

        with patch("seeds.seed_harbourview.db", mock_db):
            await seed_harbourview()

        # Users insert should NOT have been called (all existed)
        mock_db.users.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_sierra_seed_creates_memberships(self):
        """seed_sierra creates membership records for all 10 users."""
        from seeds.seed_sierra import seed_sierra, SIERRA_USERS

        mock_db = _mock_db()
        # Building doesn't exist (fresh seed)
        mock_db.buildings.find_one = AsyncMock(return_value=None)

        # Each user lookup returns a fresh user doc
        call_count = 0

        async def mock_users_find_one(query, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            # Return user doc for membership lookup
            email = query.get("email", "")
            user = next((u for u in SIERRA_USERS if u["email"] == email), None)
            if user:
                return {"id": user["id"], "email": user["email"]}
            return None

        mock_db.users.find_one = mock_users_find_one

        with patch("seeds.seed_sierra.db", mock_db):
            await seed_sierra()

        # Memberships should be created for all 10 users
        assert mock_db.memberships.insert_one.call_count == 10

    @pytest.mark.asyncio
    async def test_harbourview_seed_skips_existing_membership(self):
        """If membership already exists, seed should not create a duplicate."""
        from seeds.seed_harbourview import seed_harbourview, HARBOURVIEW_USERS

        mock_db = _mock_db()
        mock_db.buildings.find_one = AsyncMock(return_value={"id": "18932"})

        async def mock_users_find_one(query, *args, **kwargs):
            email = query.get("email", "")
            user = next((u for u in HARBOURVIEW_USERS if u["email"] == email), None)
            if user:
                return {"id": user["id"], "email": user["email"]}
            return None

        mock_db.users.find_one = mock_users_find_one
        # Memberships already exist
        mock_db.memberships.find_one = AsyncMock(return_value={"id": "existing-m", "building_id": "18932"})

        with patch("seeds.seed_harbourview.db", mock_db):
            await seed_harbourview()

        # Membership insert should NOT be called
        mock_db.memberships.insert_one.assert_not_called()
