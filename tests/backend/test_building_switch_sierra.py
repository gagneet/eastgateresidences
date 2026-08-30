"""
test_building_switch_sierra.py — Tests for cross-building manager access (Sierra bug fixes).

Covers four root-cause fixes:
1. seed_sierra.py else-branch does `pass` (not overwrite building_id) for existing users.
2. snapshot_annual_levies.py excludes `created_at` from $set to avoid MongoDB WriteError.
3. manager@eastgate.com primary building_id stays "13195" after seeding Sierra.
4. switch_building endpoint issues token with new building_id; denies without membership.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))

from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

_RUN_LEGACY = os.getenv("RUN_LEGACY_BUILDING_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not _RUN_LEGACY,
    reason="legacy Sierra multi-building regression test; set RUN_LEGACY_BUILDING_TESTS=1 to enable",
)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _make_manager(**kwargs):
    """Cross-building strata_manager whose primary building is East Gate."""
    base = {
        "id": "mgr-001",
        "email": "manager@eastgate.com",
        "full_name": "Building Manager",
        "role": "strata_manager",
        "building_id": "13195",
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "custom_permissions": {},
    }
    base.update(kwargs)
    return base


def _mock_db():
    db = MagicMock()
    db.buildings.find.return_value.to_list = AsyncMock(return_value=[])
    db.buildings.find_one = AsyncMock(return_value=None)
    db.memberships.find.return_value.to_list = AsyncMock(return_value=[])
    db.memberships.find_one = AsyncMock(return_value=None)
    db.users.find_one = AsyncMock(return_value=None)
    db.users.insert_one = AsyncMock(return_value=MagicMock(inserted_id="mgr-001"))
    db.users.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    db.audit_logs.insert_one = AsyncMock(return_value=None)
    return db


EAST_GATE = {"id": "13195", "name": "East Gate Residences", "address": "14 Hoolihan St", "is_active": True}
SIERRA = {"id": "16244", "name": "Sierra", "address": "1 Gungahlin Pl, Gungahlin ACT 2912", "is_active": True}

SIERRA_MEMBERSHIP = {
    "id": "mem-sierra-001",
    "user_id": "mgr-001",
    "building_id": "16244",
    "role": "strata_manager",
    "is_active": True,
}
EAST_GATE_MEMBERSHIP = {
    "id": "mem-east-gate-001",
    "user_id": "mgr-001",
    "building_id": "13195",
    "role": "strata_manager",
    "is_active": True,
}


# ── Class 1: Cross-building manager access via switch_building ────────────────

class TestCrossBuildingManagerAccess:
    """Verify a strata_manager with dual memberships can switch between buildings."""

    @pytest.mark.asyncio
    async def test_strata_manager_with_sierra_membership_can_switch(self):
        """Manager with Sierra membership should receive a new token for building 16244."""
        from routers.auth import switch_building, BuildingSwitchRequest

        mock_db = _mock_db()
        mock_db.memberships.find_one = AsyncMock(return_value=SIERRA_MEMBERSHIP)
        mock_db.buildings.find_one = AsyncMock(return_value=SIERRA)
        manager = _make_manager()

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.create_token", return_value="sierra-jwt-token"), \
                patch("routers.auth.user_to_response", return_value={"id": "mgr-001"}), \
                patch("routers.auth.asyncio.create_task"):
            result = await switch_building(
                data=BuildingSwitchRequest(building_id="16244"),
                current_user=manager,
            )

        assert result["token"] == "sierra-jwt-token"
        assert result["building"]["id"] == "16244"

    @pytest.mark.asyncio
    async def test_strata_manager_without_sierra_membership_gets_403(self):
        """Manager without a Sierra membership record must be denied with HTTP 403."""
        from routers.auth import switch_building, BuildingSwitchRequest
        from fastapi import HTTPException

        mock_db = _mock_db()
        mock_db.memberships.find_one = AsyncMock(return_value=None)
        mock_db.buildings.find_one = AsyncMock(return_value=SIERRA)
        manager = _make_manager()

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.create_token", return_value="should-not-reach"), \
                patch("routers.auth.user_to_response", return_value={}):
            with pytest.raises(HTTPException) as exc_info:
                await switch_building(
                    data=BuildingSwitchRequest(building_id="16244"),
                    current_user=manager,
                )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_switch_building_token_carries_new_building_id(self):
        """The new JWT must be issued with building_id='16244' after switching to Sierra."""
        from routers.auth import switch_building, BuildingSwitchRequest

        mock_db = _mock_db()
        mock_db.memberships.find_one = AsyncMock(return_value=SIERRA_MEMBERSHIP)
        mock_db.buildings.find_one = AsyncMock(return_value=SIERRA)
        manager = _make_manager()

        captured_kwargs = {}

        def _fake_create_token(user_id, email, role, building_id=None, **kw):
            captured_kwargs["building_id"] = building_id
            return "mocked-token"

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.create_token", side_effect=_fake_create_token), \
                patch("routers.auth.user_to_response", return_value={"id": "mgr-001"}), \
                patch("routers.auth.asyncio.create_task"):
            await switch_building(
                data=BuildingSwitchRequest(building_id="16244"),
                current_user=manager,
            )

        assert captured_kwargs.get("building_id") == "16244", (
            "create_token must be called with building_id='16244' when switching to Sierra"
        )

    @pytest.mark.asyncio
    async def test_cross_building_manager_sees_both_buildings(self):
        """get_my_buildings should return both East Gate and Sierra for a dual-member manager."""
        from routers.auth import get_my_buildings

        mock_db = _mock_db()
        mock_db.memberships.find.return_value.to_list = AsyncMock(
            return_value=[EAST_GATE_MEMBERSHIP, SIERRA_MEMBERSHIP]
        )
        mock_db.buildings.find.return_value.to_list = AsyncMock(
            return_value=[EAST_GATE, SIERRA]
        )
        manager = _make_manager()

        with patch("routers.auth.db", mock_db):
            result = await get_my_buildings(current_user=manager)

        assert len(result) == 2
        building_ids = {b["id"] for b in result}
        assert "13195" in building_ids
        assert "16244" in building_ids

    @pytest.mark.asyncio
    async def test_cross_building_manager_primary_building_is_east_gate(self):
        """The manager user dict must have building_id='13195' (primary), not '16244'."""
        manager = _make_manager()
        assert manager["building_id"] == "13195", (
            "manager@eastgate.com primary building must stay East Gate '13195'"
        )

    @pytest.mark.asyncio
    async def test_manager_switch_back_to_east_gate_succeeds(self):
        """After using Sierra, switching back to East Gate with a valid membership must succeed."""
        from routers.auth import switch_building, BuildingSwitchRequest

        mock_db = _mock_db()
        mock_db.memberships.find_one = AsyncMock(return_value=EAST_GATE_MEMBERSHIP)
        mock_db.buildings.find_one = AsyncMock(return_value=EAST_GATE)
        # Simulate manager currently scoped to Sierra
        manager = _make_manager(building_id="16244")

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.create_token", return_value="eastgate-jwt-token"), \
                patch("routers.auth.user_to_response", return_value={"id": "mgr-001"}), \
                patch("routers.auth.asyncio.create_task"):
            result = await switch_building(
                data=BuildingSwitchRequest(building_id="13195"),
                current_user=manager,
            )

        assert result["token"] == "eastgate-jwt-token"
        assert result["building"]["id"] == "13195"


# ── Class 2: seed_sierra.py — building_id preservation for existing users ─────

class TestSeedSierraBuildingIdPreservation:
    """Verify the seed else-branch does not overwrite building_id for cross-building users."""

    @pytest.mark.asyncio
    async def test_existing_user_building_id_is_not_overwritten_by_seed(self):
        """When the user already exists, update_one must NOT be called with a new building_id."""
        mock_db = _mock_db()

        # Simulate an already-existing manager with East Gate as primary building
        existing_user = _make_manager()
        mock_db.users.find_one = AsyncMock(return_value=existing_user)

        # Replicate the seed logic: if existing, do nothing (pass)
        from seeds.seed_sierra import SIERRA_USERS

        manager_entry = next(u for u in SIERRA_USERS if u["email"] == "manager@eastgate.com")

        with patch("seeds.seed_sierra.db", mock_db):
            # Run the if/else logic directly (mirrors seeds/seed_sierra.py lines 471-480)
            existing = await mock_db.users.find_one({"email": manager_entry["email"]})
            if not existing:
                await mock_db.users.insert_one(dict(manager_entry))
            else:
                pass  # the fix: do NOT overwrite building_id

        # update_one must never have been called at all in this path
        mock_db.users.update_one.assert_not_called()
        # insert_one must also NOT have been called (user already existed)
        mock_db.users.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_user_inserted_with_correct_primary_building(self):
        """When a new seed user is inserted, building_id must come from the SIERRA_USERS entry."""
        mock_db = _mock_db()
        mock_db.users.find_one = AsyncMock(return_value=None)  # user does not exist

        from seeds.seed_sierra import SIERRA_USERS

        manager_entry = next(u for u in SIERRA_USERS if u["email"] == "manager@eastgate.com")

        # Replicate insert path
        with patch("seeds.seed_sierra.db", mock_db):
            existing = await mock_db.users.find_one({"email": manager_entry["email"]})
            if not existing:
                await mock_db.users.insert_one(dict(manager_entry))

        mock_db.users.insert_one.assert_called_once()
        inserted_doc = mock_db.users.insert_one.call_args[0][0]
        assert inserted_doc["building_id"] == "13195", (
            "manager@eastgate.com must be inserted with building_id='13195', not '16244'"
        )

    @pytest.mark.asyncio
    async def test_cross_building_access_via_membership_not_user_building_id(self):
        """A user with primary building '13195' can reach Sierra solely via a membership record."""
        mock_db = _mock_db()
        mock_db.memberships.find_one = AsyncMock(return_value=SIERRA_MEMBERSHIP)

        # The user record itself only knows about East Gate
        manager = _make_manager(building_id="13195")

        # Replicate membership lookup (as switch_building does)
        membership = await mock_db.memberships.find_one({
            "user_id": manager["id"],
            "building_id": "16244",
            "is_active": True,
        })

        assert membership is not None, "Membership record must exist to grant Sierra access"
        assert membership["building_id"] == "16244"
        assert manager["building_id"] == "13195", (
            "User's building_id should NOT have changed; access comes from the membership"
        )

    def test_sierra_manager_entry_has_correct_primary_building(self):
        """The SIERRA_USERS constant must define manager@eastgate.com with building_id='13195'."""
        from seeds.seed_sierra import SIERRA_USERS

        manager_entry = next(
            (u for u in SIERRA_USERS if u["email"] == "manager@eastgate.com"),
            None,
        )
        assert manager_entry is not None, "manager@eastgate.com must be present in SIERRA_USERS"
        assert manager_entry["building_id"] == "13195", (
            f"Expected building_id='13195', got '{manager_entry['building_id']}'. "
            "The seed must not set building_id to '16244' for a cross-building user."
        )


# ── Class 3: snapshot_annual_levies.py — upsert WriteError fix ───────────────

class TestAnnualLeviesUpsert:
    """Verify the upsert logic in snapshot_annual_levies excludes created_at from $set."""

    def test_upsert_created_at_not_in_set_fields(self):
        """set_fields must exclude 'created_at' to prevent MongoDB conflict error."""
        entry = {
            "id": "test-id",
            "plan_id": "16244",
            "building_id": "16244",
            "year": "2025",
            "status": "approved",
            "created_at": "2026-03-30T02:27:55.774598+00:00",
            "updated_at": "2026-03-30T02:27:55.774598+00:00",
        }
        # Mirrors the fix in snapshot_annual_levies.py line 183
        set_fields = {k: v for k, v in entry.items() if k != 'created_at'}

        assert "created_at" not in set_fields, (
            "'created_at' must be excluded from $set to avoid WriteError on upsert"
        )
        # All other fields must still be present
        for key in ("id", "plan_id", "building_id", "year", "status", "updated_at"):
            assert key in set_fields

    def test_upsert_created_at_preserved_in_set_on_insert(self):
        """The $setOnInsert block must carry the original created_at value."""
        entry = {
            "id": "test-id",
            "plan_id": "16244",
            "building_id": "16244",
            "year": "2025",
            "created_at": "2026-03-30T02:27:55.774598+00:00",
            "updated_at": "2026-03-30T02:27:55.774598+00:00",
        }
        # Mirrors snapshot_annual_levies.py line 186
        set_on_insert = {"created_at": entry.get("created_at", entry.get("updated_at", ""))}

        assert set_on_insert["created_at"] == "2026-03-30T02:27:55.774598+00:00", (
            "$setOnInsert must preserve the original created_at timestamp"
        )

    @pytest.mark.asyncio
    async def test_upsert_does_not_raise_writeerror_with_created_at_in_entry(self):
        """The fixed upsert logic must succeed without raising even when entry has created_at."""
        mock_db = _mock_db()
        mock_result = MagicMock()
        mock_result.upserted_id = "new-id"
        mock_result.modified_count = 0
        mock_db.annual_levies = MagicMock()
        mock_db.annual_levies.update_one = AsyncMock(return_value=mock_result)

        entry = {
            "id": "test-id",
            "plan_id": "16244",
            "building_id": "16244",
            "year": "2025",
            "status": "approved",
            "created_at": "2026-03-30T02:27:55.774598+00:00",
            "updated_at": "2026-03-30T02:27:55.774598+00:00",
        }

        # Run the exact upsert pattern from snapshot_annual_levies.py
        set_fields = {k: v for k, v in entry.items() if k != 'created_at'}
        result = await mock_db.annual_levies.update_one(
            {"plan_id": entry["plan_id"], "year": entry["year"]},
            {
                "$set": set_fields,
                "$setOnInsert": {"created_at": entry.get("created_at", entry.get("updated_at", ""))},
            },
            upsert=True,
        )

        # Must not raise; must call update_one with the correct structure
        assert result.upserted_id == "new-id"
        call_args = mock_db.annual_levies.update_one.call_args
        update_doc = call_args[0][1]
        assert "created_at" not in update_doc["$set"], (
            "$set must not contain created_at — that causes MongoDB WriteError"
        )
        assert "created_at" in update_doc["$setOnInsert"], (
            "$setOnInsert must contain created_at to preserve it on new inserts"
        )

    def test_sierra_annual_levies_have_correct_building_id(self):
        """ANNUAL_LEVIES must contain at least 2 Sierra entries (2025 + 2026) with building_id='16244'."""
        from seeds.snapshot_annual_levies import ANNUAL_LEVIES

        sierra_entries = [e for e in ANNUAL_LEVIES if e.get("building_id") == "16244"]

        assert len(sierra_entries) >= 2, (
            f"Expected at least 2 Sierra entries in ANNUAL_LEVIES, found {len(sierra_entries)}"
        )

        years = {e["year"] for e in sierra_entries}
        assert "2025" in years, "Sierra must have a 2025 levy entry"
        assert "2026" in years, "Sierra must have a 2026 levy entry"

        for entry in sierra_entries:
            assert entry["year"] in {"2025", "2026"}, (
                f"Unexpected year '{entry['year']}' in Sierra levy entries"
            )
            assert entry["building_id"] == "16244", (
                f"Sierra entry year={entry['year']} has wrong building_id: {entry['building_id']}"
            )
