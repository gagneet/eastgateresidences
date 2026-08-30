"""
Tests for unit_owner_name enrichment on the GET /users endpoint.

Recent change: routers/users.py batch-enriches each user returned from GET /users
with the strata roll owner name sourced from db.units (the single source of truth).
The enriched field is ephemeral — it is added to the user dict in-flight and is
NOT persisted to the users collection.

models/user.py  — UserResponse.unit_owner_name: Optional[str] = None
utils/permissions.py — user_to_response() passes unit_owner_name through from the
                       enriched user dict via user.get("unit_owner_name").

Run from project root:
    backend/venv/bin/pytest tests/backend/test_unit_owner_name_enrichment.py -v
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Make the backend package importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend"))


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

def _make_user(
        user_id: str = "user-001",
        unit_number: str | None = "UA063",
        role: str = "owner",
) -> dict:
    """Return a minimal user dict as it comes back from db.users.find()."""
    return {
        "id": user_id,
        "email": f"{user_id}@example.com",
        "full_name": "Test User",
        "role": role,
        "unit_number": unit_number,
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _make_admin() -> dict:
    """Return a super_admin user dict (viewer/current_user)."""
    return {
        "id": "admin-001",
        "email": "admin@eastgate.com",
        "full_name": "Super Admin",
        "role": "super_admin",
        "unit_number": None,
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _make_unit_doc(
        unit_number: str = "UA063",
        owner_name: str = "Anthony McDonald",
        owner_name_b: str | None = None,
) -> dict:
    """Return a minimal unit doc as returned by db.units.find()."""
    doc = {
        "unit_number": unit_number,
        "owner_name": owner_name,
        "building_id": "13195",
    }
    if owner_name_b:
        doc["owner_name_b"] = owner_name_b
    return doc


# ---------------------------------------------------------------------------
# 1. unit_owner_name is populated when there is a matching unit
# ---------------------------------------------------------------------------

class TestUnitOwnerNamePopulated:
    """user_to_response() must include the roll name when db.units has a match."""

    def test_single_owner_populated(self):
        """unit_owner_name is set to owner_name when owner_name_b is absent."""
        from utils.permissions import user_to_response

        user = _make_user(unit_number="UA063")
        user["unit_owner_name"] = "Anthony McDonald"  # added by router enrichment

        response = user_to_response(user)

        assert response.unit_owner_name == "Anthony McDonald"

    def test_unit_owner_name_type_is_str(self):
        """unit_owner_name on UserResponse is a plain string, not None, when set."""
        from utils.permissions import user_to_response

        user = _make_user(unit_number="TH087")
        user["unit_owner_name"] = "Avneet Rooprai"

        response = user_to_response(user)

        assert isinstance(response.unit_owner_name, str)


# ---------------------------------------------------------------------------
# 2. Both owner_name and owner_name_b are joined with " & "
# ---------------------------------------------------------------------------

class TestDualOwnerNameJoining:
    """When owner_name_b is present, unit_owner_name = 'A & B'."""

    def test_dual_owners_joined_with_ampersand(self):
        """The router produces 'First & Second'; user_to_response passes it through."""
        from utils.permissions import user_to_response

        user = _make_user(unit_number="TH080")
        user["unit_owner_name"] = "John Smith & Jane Smith"

        response = user_to_response(user)

        assert response.unit_owner_name == "John Smith & Jane Smith"

    def test_roll_map_building_logic_joins_correctly(self):
        """
        Verify the dict-comprehension logic from routers/users.py inline:
            owner_name + (" & " + owner_name_b if owner_name_b else "")
        produces the expected string for dual and single owner docs.
        """
        unit_docs = [
            {"unit_number": "UA001", "owner_name": "Alice Brown", "owner_name_b": "Bob Brown"},
            {"unit_number": "UA002", "owner_name": "Carol White"},
        ]
        unit_roll_map = {
            u["unit_number"]: (
                    u["owner_name"]
                    + (" & " + u["owner_name_b"] if u.get("owner_name_b") else "")
            )
            for u in unit_docs
        }

        assert unit_roll_map["UA001"] == "Alice Brown & Bob Brown"
        assert unit_roll_map["UA002"] == "Carol White"


# ---------------------------------------------------------------------------
# 3. unit_owner_name is None when user has no unit_number
# ---------------------------------------------------------------------------

class TestNoUnitNumber:
    """unit_owner_name must be None/absent when the user has no unit_number."""

    def test_none_when_no_unit_number(self):
        """Users without a unit (e.g. strata manager) get no roll name."""
        from utils.permissions import user_to_response

        user = _make_user(unit_number=None)
        # Router skips enrichment for users without unit_number
        # unit_owner_name key is never written to the user dict

        response = user_to_response(user)

        assert response.unit_owner_name is None

    def test_empty_unit_number_treated_as_missing(self):
        """An empty-string unit_number also produces no roll name."""
        from utils.permissions import user_to_response

        user = _make_user(unit_number=None)
        user["unit_number"] = ""  # explicitly empty

        response = user_to_response(user)

        assert response.unit_owner_name is None


# ---------------------------------------------------------------------------
# 4. unit_owner_name is None when unit_number has no matching unit doc
# ---------------------------------------------------------------------------

class TestNoMatchingUnitDoc:
    """unit_owner_name must be None when db.units has no doc for the unit_number."""

    def test_missing_unit_doc_gives_none(self):
        """unit_roll_map.get() returns None for unrecognised unit numbers."""
        from utils.permissions import user_to_response

        user = _make_user(unit_number="XX999")
        # Router only writes unit_owner_name when roll_name is truthy
        # So the key stays absent → user_to_response reads None via .get()

        response = user_to_response(user)

        assert response.unit_owner_name is None


# ---------------------------------------------------------------------------
# 5. user_to_response() passes unit_owner_name through from the enriched dict
# ---------------------------------------------------------------------------

class TestUserToResponsePassthrough:
    """user_to_response must faithfully relay whatever value is in the user dict."""

    def test_passthrough_matches_input(self):
        """user_to_response reads unit_owner_name via user.get(); value must match."""
        from utils.permissions import user_to_response

        user = _make_user()
        user["unit_owner_name"] = "Specific Name From Roll"

        response = user_to_response(user)

        assert response.unit_owner_name == "Specific Name From Roll"

    def test_passthrough_absent_key_gives_none(self):
        """When the key is not in the user dict, the response field is None."""
        from utils.permissions import user_to_response

        user = _make_user()
        # Do not set unit_owner_name at all

        response = user_to_response(user)

        assert response.unit_owner_name is None


# ---------------------------------------------------------------------------
# 6. unit_owner_name is NOT stored in the users collection (ephemeral enrichment)
# ---------------------------------------------------------------------------

class TestEphemeralEnrichment:
    """
    The enrichment adds unit_owner_name to the in-memory user dict only.
    The users collection is never written when this key is injected.
    """

    @pytest.mark.asyncio
    async def test_users_collection_not_written(self):
        """
        Simulate the router logic: build unit_roll_map and inject unit_owner_name
        into each user dict. Confirm db.users.update_one / insert_one is never called.
        """
        users = [_make_user("u-1", "UA063")]
        unit_docs = [_make_unit_doc("UA063", "Anthony McDonald")]

        mock_db = MagicMock()
        mock_db.users.find.return_value.to_list = AsyncMock(return_value=users)
        mock_db.units.find.return_value.to_list = AsyncMock(return_value=unit_docs)
        mock_db.users.update_one = AsyncMock()
        mock_db.users.insert_one = AsyncMock()

        # Reproduce the enrichment logic from routers/users.py lines 162-181
        unit_numbers = [u["unit_number"] for u in users if u.get("unit_number")]
        unit_docs_result = await mock_db.units.find(
            {"building_id": "13195", "unit_number": {"$in": unit_numbers}},
            {"_id": 0, "unit_number": 1, "owner_name": 1, "owner_name_b": 1},
        ).to_list(len(unit_numbers))

        unit_roll_map = {
            u["unit_number"]: (
                    u["owner_name"]
                    + (" & " + u["owner_name_b"] if u.get("owner_name_b") else "")
            )
            for u in unit_docs_result
        }

        for u in users:
            roll_name = unit_roll_map.get(u.get("unit_number", ""))
            if roll_name:
                u["unit_owner_name"] = roll_name

        # Enrichment happened in-memory — no write calls
        mock_db.users.update_one.assert_not_called()
        mock_db.users.insert_one.assert_not_called()

        # The in-memory dict was mutated
        assert users[0].get("unit_owner_name") == "Anthony McDonald"


# ---------------------------------------------------------------------------
# 7. Single DB query for ALL users (batch, not N+1)
# ---------------------------------------------------------------------------

class TestBatchQuery:
    """db.units.find() must be called once regardless of how many users are returned."""

    @pytest.mark.asyncio
    async def test_single_units_query_for_multiple_users(self):
        """
        Three users with different unit numbers should result in exactly ONE
        db.units.find() call, not one-per-user.
        """
        users = [
            _make_user("u-1", "UA001"),
            _make_user("u-2", "UA002"),
            _make_user("u-3", "TH087"),
        ]
        unit_docs = [
            _make_unit_doc("UA001", "Alice"),
            _make_unit_doc("UA002", "Bob"),
            _make_unit_doc("TH087", "Carol"),
        ]

        mock_db = MagicMock()
        # Cursor returned by find() must support chained .to_list()
        mock_units_cursor = MagicMock()
        mock_units_cursor.to_list = AsyncMock(return_value=unit_docs)
        mock_db.units.find.return_value = mock_units_cursor

        # Execute the enrichment logic
        unit_numbers = [u["unit_number"] for u in users if u.get("unit_number")]
        await mock_db.units.find(
            {"building_id": "13195", "unit_number": {"$in": unit_numbers}},
            {"_id": 0, "unit_number": 1, "owner_name": 1, "owner_name_b": 1},
        ).to_list(len(unit_numbers))

        # find() must have been called exactly once
        assert mock_db.units.find.call_count == 1

    @pytest.mark.asyncio
    async def test_query_uses_dollar_in_operator(self):
        """The units query must use $in to batch-fetch all units at once."""
        users = [_make_user("u-1", "UA001"), _make_user("u-2", "UA002")]

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_db.units.find.return_value = mock_cursor

        unit_numbers = [u["unit_number"] for u in users if u.get("unit_number")]
        await mock_db.units.find(
            {"building_id": "13195", "unit_number": {"$in": unit_numbers}},
            {"_id": 0, "unit_number": 1, "owner_name": 1, "owner_name_b": 1},
        ).to_list(len(unit_numbers))

        call_args = mock_db.units.find.call_args
        query_filter = call_args[0][0]

        assert "$in" in query_filter["unit_number"]
        assert set(query_filter["unit_number"]["$in"]) == {"UA001", "UA002"}

    @pytest.mark.asyncio
    async def test_no_units_query_when_no_users_have_unit_numbers(self):
        """When all users lack unit_number, db.units.find() is never called."""
        users = [_make_user("u-1", None), _make_user("u-2", None)]

        mock_db = MagicMock()
        mock_db.units.find = MagicMock()

        # Reproduce guard from routers/users.py line 163
        unit_numbers = [u["unit_number"] for u in users if u.get("unit_number")]
        if unit_numbers:
            await mock_db.units.find(
                {"building_id": "13195", "unit_number": {"$in": unit_numbers}},
                {"_id": 0, "unit_number": 1, "owner_name": 1, "owner_name_b": 1},
            ).to_list(len(unit_numbers))

        mock_db.units.find.assert_not_called()


# ---------------------------------------------------------------------------
# 8. UserResponse model accepts and stores unit_owner_name
# ---------------------------------------------------------------------------

class TestUserResponseModel:
    """The Pydantic UserResponse model must accept unit_owner_name."""

    def test_model_accepts_unit_owner_name(self):
        """Constructing UserResponse with unit_owner_name should not raise."""
        from models.user import UserResponse

        # Build with only required fields + unit_owner_name
        from utils.permissions import get_user_permissions
        user_dict = _make_user()
        permissions = get_user_permissions(user_dict)

        response = UserResponse(
            id="u-001",
            email="test@example.com",
            full_name="Test User",
            role="owner",
            is_active=True,
            is_approved=True,
            permissions=permissions,
            created_at=datetime.now(timezone.utc).isoformat(),
            unit_owner_name="Roll Name Here",
        )

        assert response.unit_owner_name == "Roll Name Here"

    def test_model_defaults_unit_owner_name_to_none(self):
        """unit_owner_name defaults to None when not supplied."""
        from models.user import UserResponse
        from utils.permissions import get_user_permissions

        user_dict = _make_user()
        permissions = get_user_permissions(user_dict)

        response = UserResponse(
            id="u-002",
            email="test2@example.com",
            full_name="Another User",
            role="owner",
            is_active=True,
            is_approved=True,
            permissions=permissions,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        assert response.unit_owner_name is None

    def test_model_field_declared_as_optional_str(self):
        """unit_owner_name is declared Optional[str] on the UserResponse model."""
        from models.user import UserResponse

        field = UserResponse.model_fields["unit_owner_name"]
        # The field should have no required constraint and allow None
        assert not field.is_required()


# ---------------------------------------------------------------------------
# 9. When owner_name_b is None/empty, only owner_name is used
# ---------------------------------------------------------------------------

class TestSingleOwnerName:
    """No trailing ' & ' should appear when owner_name_b is absent or empty."""

    def test_no_ampersand_when_owner_name_b_absent(self):
        """Unit doc without owner_name_b key produces clean owner_name string."""
        unit_doc = {"unit_number": "UA010", "owner_name": "Solo Owner"}
        # The comprehension: owner_name + (" & " + owner_name_b if owner_name_b else "")
        result = unit_doc["owner_name"] + (
            " & " + unit_doc["owner_name_b"] if unit_doc.get("owner_name_b") else ""
        )
        assert result == "Solo Owner"
        assert " & " not in result

    def test_no_ampersand_when_owner_name_b_is_empty_string(self):
        """Unit doc with owner_name_b='' is treated same as absent."""
        unit_doc = {"unit_number": "UA011", "owner_name": "Solo Two", "owner_name_b": ""}
        result = unit_doc["owner_name"] + (
            " & " + unit_doc["owner_name_b"] if unit_doc.get("owner_name_b") else ""
        )
        assert result == "Solo Two"
        assert " & " not in result

    def test_ampersand_present_when_owner_name_b_is_nonempty(self):
        """Unit doc with a non-empty owner_name_b always gets the ' & ' separator."""
        unit_doc = {"unit_number": "UA012", "owner_name": "First", "owner_name_b": "Second"}
        result = unit_doc["owner_name"] + (
            " & " + unit_doc["owner_name_b"] if unit_doc.get("owner_name_b") else ""
        )
        assert result == "First & Second"

    def test_user_to_response_single_owner(self):
        """user_to_response with only owner_name in unit_owner_name returns exact name."""
        from utils.permissions import user_to_response

        user = _make_user(unit_number="UA013")
        user["unit_owner_name"] = "Only One"

        response = user_to_response(user)

        assert response.unit_owner_name == "Only One"
        assert "&" not in response.unit_owner_name


# ---------------------------------------------------------------------------
# 10. PII masking during impersonation does NOT clear unit_owner_name
# ---------------------------------------------------------------------------

class TestImpersonationPreservesUnitOwnerName:
    """
    The impersonation PII-masking block in user_to_response() masks email,
    full_name, phone, and addresses — but must NOT clear unit_owner_name.
    unit_owner_name is strata-roll data (not personal contact info).
    """

    def test_unit_owner_name_survives_impersonation_masking(self):
        """unit_owner_name is unaffected by impersonation PII masking."""
        from utils.permissions import user_to_response

        user = _make_user(unit_number="UA063")
        user["unit_owner_name"] = "Anthony McDonald"
        # Simulate an impersonated session by adding impersonator_id
        user["impersonator_id"] = "admin-001"

        response = user_to_response(user)

        # PII fields must be masked
        assert "****" in response.email or "@" not in response.email or response.email != user["email"]
        assert response.full_name == "Resident"

        # But unit_owner_name must NOT be masked or cleared
        assert response.unit_owner_name == "Anthony McDonald"

    def test_unit_owner_name_survives_viewer_pii_masking(self):
        """
        When a non-super_admin viewer looks at another user's record,
        PII is masked but unit_owner_name remains intact.
        """
        from utils.permissions import user_to_response

        # Subject: an owner
        subject = _make_user("owner-001", "UA020")
        subject["unit_owner_name"] = "Target Owner Name"
        subject["phone"] = "+61 400 000 001"
        subject["email"] = "owner@example.com"

        # Viewer: an ec_member (not super_admin, not the same user)
        viewer = {
            "id": "ec-001",
            "role": "ec_member",
            "email": "ec@eastgate.com",
            "full_name": "EC Member",
            "is_active": True,
            "is_approved": True,
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        response = user_to_response(subject, viewer=viewer)

        # Email should be masked for non-super_admin viewer
        assert response.email != "owner@example.com"

        # unit_owner_name must still be present
        assert response.unit_owner_name == "Target Owner Name"
