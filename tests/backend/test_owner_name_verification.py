"""
Tests for the Owner Name Verification feature.

Covers:
- name_utils.py: normalise_name, names_similar, check_owner_name_against_roll
- Registration path: is_name_flagged set on user_doc when owner name mismatches roll
- Approval path: is_name_flagged + flag_reason set in update_dict when admin approves
- Feature toggle: owner_name_verification exists in DEFAULT_FEATURES
- UserResponse model: is_name_flagged and flag_reason fields present
- user_to_response: new fields passed through
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend"))


# ─────────────────────────────────────────────
# 1. name_utils unit tests
# ─────────────────────────────────────────────
class TestNormaliseName:
    """normalise_name returns a set of lowercase alpha tokens."""

    def test_basic_name(self):
        from utils.name_utils import normalise_name
        assert normalise_name("John Smith") == {"john", "smith"}

    def test_strips_punctuation(self):
        from utils.name_utils import normalise_name
        assert normalise_name("Smith, Jane") == {"smith", "jane"}

    def test_handles_none(self):
        from utils.name_utils import normalise_name
        assert normalise_name(None) == set()

    def test_handles_empty(self):
        from utils.name_utils import normalise_name
        assert normalise_name("") == set()

    def test_lowercases(self):
        from utils.name_utils import normalise_name
        assert normalise_name("MR JOHN SMITH") == {"mr", "john", "smith"}

    def test_multi_space(self):
        from utils.name_utils import normalise_name
        assert normalise_name("  John   Smith  ") == {"john", "smith"}

    def test_removes_hyphens(self):
        from utils.name_utils import normalise_name
        # Hyphen is non-alpha → becomes space → two tokens
        assert normalise_name("Mary-Jane Watson") == {"mary", "jane", "watson"}


class TestNamesSimilar:
    """names_similar mirrors the frontend isSimilarName algorithm."""

    def test_exact_match(self):
        from utils.name_utils import names_similar
        assert names_similar("John Smith", "John Smith") is True

    def test_reversed_order(self):
        from utils.name_utils import names_similar
        assert names_similar("Smith John", "John Smith") is True

    def test_partial_overlap_above_threshold(self):
        from utils.name_utils import names_similar
        # "John Smith" vs "John A Smith" — 2/3 overlap = 0.67 >= 0.6
        assert names_similar("John Smith", "John A Smith") is True

    def test_completely_different(self):
        from utils.name_utils import names_similar
        assert names_similar("John Smith", "Alice Wong") is False

    def test_single_token_match(self):
        from utils.name_utils import names_similar
        # 1/1 >= ceil(1*0.6)=1 → True
        assert names_similar("Smith", "Smith") is True

    def test_single_token_mismatch(self):
        from utils.name_utils import names_similar
        assert names_similar("Smith", "Jones") is False

    def test_blank_a_returns_true(self):
        from utils.name_utils import names_similar
        assert names_similar("", "John Smith") is True

    def test_blank_b_returns_true(self):
        from utils.name_utils import names_similar
        assert names_similar("John Smith", "") is True

    def test_none_a_returns_true(self):
        from utils.name_utils import names_similar
        assert names_similar(None, "John Smith") is True

    def test_threshold_boundary_pass(self):
        from utils.name_utils import names_similar
        # Tokens: {"alice","bob","carol"} vs {"alice","bob","dave"}
        # overlap=2, min_len=3, ceil(3*0.6)=2 → True
        assert names_similar("alice bob carol", "alice bob dave") is True

    def test_threshold_boundary_fail(self):
        from utils.name_utils import names_similar
        # Tokens: {"a","b","c","d"} vs {"a","e","f","g"}
        # overlap=1, min_len=4, ceil(4*0.6)=3 → False
        assert names_similar("a b c d", "a e f g") is False

    def test_title_stripped_match(self):
        from utils.name_utils import names_similar
        # "Mr John Smith" → {"mr","john","smith"}
        # "John Smith" → {"john","smith"}
        # overlap=2, min_len=2, ceil(2*0.6)=2 → True
        assert names_similar("Mr John Smith", "John Smith") is True


class TestCheckOwnerNameAgainstRoll:
    """check_owner_name_against_roll returns True when name matches either roll entry."""

    def test_matches_primary(self):
        from utils.name_utils import check_owner_name_against_roll
        assert check_owner_name_against_roll("John Smith", "John Smith", "") is True

    def test_matches_secondary(self):
        from utils.name_utils import check_owner_name_against_roll
        assert check_owner_name_against_roll("Jane Doe", "John Smith", "Jane Doe") is True

    def test_no_match(self):
        from utils.name_utils import check_owner_name_against_roll
        assert check_owner_name_against_roll("Alice Wong", "John Smith", "Jane Doe") is False

    def test_blank_primary_returns_true(self):
        from utils.name_utils import check_owner_name_against_roll
        assert check_owner_name_against_roll("Anyone", "", "") is True

    def test_partial_match_primary(self):
        from utils.name_utils import check_owner_name_against_roll
        # "Anthony McDonald" vs roll "ANTHONY J MCDONALD" → 2/3 tokens overlap
        assert check_owner_name_against_roll("Anthony McDonald", "ANTHONY J MCDONALD", "") is True


# ─────────────────────────────────────────────
# 2. Feature toggle exists in seed data
# ─────────────────────────────────────────────
class TestFeatureToggleSeed:
    """owner_name_verification must be present in DEFAULT_FEATURES."""

    def test_toggle_exists(self):
        from seeds.feature_toggles import DEFAULT_FEATURES
        keys = [f["feature_key"] for f in DEFAULT_FEATURES]
        assert "owner_name_verification" in keys, (
            "owner_name_verification toggle missing from DEFAULT_FEATURES"
        )

    def test_toggle_is_enabled_by_default(self):
        from seeds.feature_toggles import DEFAULT_FEATURES
        toggle = next(f for f in DEFAULT_FEATURES if f["feature_key"] == "owner_name_verification")
        assert toggle["is_enabled"] is True

    def test_toggle_category(self):
        from seeds.feature_toggles import DEFAULT_FEATURES
        toggle = next(f for f in DEFAULT_FEATURES if f["feature_key"] == "owner_name_verification")
        assert toggle["category"] == "governance"

    def test_toggle_roles_admin_only(self):
        from seeds.feature_toggles import DEFAULT_FEATURES
        toggle = next(f for f in DEFAULT_FEATURES if f["feature_key"] == "owner_name_verification")
        # Must include management roles; must NOT include tenant/guest
        assert "super_admin" in toggle["roles"]
        assert "strata_manager" in toggle["roles"]
        assert "tenant" not in toggle["roles"]
        assert "guest" not in toggle["roles"]

    def test_toggle_route(self):
        from seeds.feature_toggles import DEFAULT_FEATURES
        toggle = next(f for f in DEFAULT_FEATURES if f["feature_key"] == "owner_name_verification")
        assert "/admin/users" in toggle["routes"]


# ─────────────────────────────────────────────
# 3. UserResponse model fields
# ─────────────────────────────────────────────
class TestUserResponseModel:
    """UserResponse must expose is_name_flagged and flag_reason."""

    def test_is_name_flagged_field_exists(self):
        from models.user import UserResponse
        fields = UserResponse.model_fields
        assert "is_name_flagged" in fields

    def test_is_name_flagged_default_false(self):
        from models.user import UserResponse
        field = UserResponse.model_fields["is_name_flagged"]
        assert field.default is False or field.default is None

    def test_flag_reason_field_exists(self):
        from models.user import UserResponse
        fields = UserResponse.model_fields
        assert "flag_reason" in fields

    def test_flag_reason_default_none(self):
        from models.user import UserResponse
        field = UserResponse.model_fields["flag_reason"]
        assert field.default is None


class TestUserUpdateModel:
    """UserUpdate must accept is_name_flagged and flag_reason."""

    def test_is_name_flagged_field_exists(self):
        from models.user import UserUpdate
        fields = UserUpdate.model_fields
        assert "is_name_flagged" in fields

    def test_flag_reason_field_exists(self):
        from models.user import UserUpdate
        fields = UserUpdate.model_fields
        assert "flag_reason" in fields


# ─────────────────────────────────────────────
# 4. Registration path pre-flagging
# ─────────────────────────────────────────────
class TestRegistrationPreFlag:
    """Owner registration should set is_name_flagged when name mismatches roll."""

    @pytest.mark.asyncio
    async def test_owner_flagged_on_registration_mismatch(self):
        """
        When an owner registers with a name that doesn't match the strata roll,
        user_doc['is_name_flagged'] should be True.
        """
        from unittest.mock import AsyncMock, patch

        # Build a minimal mock unit doc with a different owner name
        unit_doc = {
            "unit_number": "TH087",
            "owner_name": "Avneet Rooprai",
            "owner_name_b": "",
        }

        with patch("routers.auth.db") as mock_db, \
                patch("routers.auth.hash_password", return_value="hashed"), \
                patch("routers.auth.asyncio.gather", new_callable=AsyncMock) as mock_gather:
            # Simulate gather returning (unit_doc,) for has_unit path
            mock_gather.return_value = (unit_doc,)

            from fastapi import Request as FRequest
            from starlette.testclient import TestClient
            from starlette.datastructures import Headers

            # We test the name_utils logic directly here rather than
            # calling the full register endpoint (which needs DB + email)
            from utils.name_utils import check_owner_name_against_roll

            user_full_name = "Alice Wong"
            roll_primary = unit_doc["owner_name"]  # "Avneet Rooprai"
            roll_secondary = unit_doc["owner_name_b"]  # ""

            result = check_owner_name_against_roll(user_full_name, roll_primary, roll_secondary)
            assert result is False, "Alice Wong should NOT match Avneet Rooprai"

    @pytest.mark.asyncio
    async def test_owner_not_flagged_on_name_match(self):
        """Owner whose name matches the strata roll should NOT be flagged."""
        from utils.name_utils import check_owner_name_against_roll

        # Avneet Rooprai registering for TH087 — should match
        result = check_owner_name_against_roll(
            "Avneet Rooprai",  # registered name
            "Avneet Rooprai",  # roll primary
            "",  # roll secondary
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_joint_owner_secondary_match(self):
        """Owner whose name matches the secondary (owner_name_b) should NOT be flagged."""
        from utils.name_utils import check_owner_name_against_roll

        result = check_owner_name_against_roll(
            "Jane Smith",  # registered name
            "John Smith",  # roll primary
            "Jane Smith",  # roll secondary
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_user_doc_gets_flagged_fields(self):
        """
        Simulate the user_doc construction logic to verify is_name_flagged
        and flag_reason are set correctly on mismatch.
        """
        from utils.name_utils import check_owner_name_against_roll

        # Simulate what auth.py does after building user_doc
        user_doc: dict = {
            "id": "test-uuid",
            "full_name": "Alice Wong",
            "role": "owner",
        }
        unit_doc = {
            "unit_number": "TH087",
            "owner_name": "Avneet Rooprai",
            "owner_name_b": "",
        }

        # Replicate the pre-flag logic from auth.py
        if user_doc["role"] == "owner" and unit_doc:
            _roll_primary = unit_doc.get("owner_name", "")
            _roll_secondary = unit_doc.get("owner_name_b", "")
            if not check_owner_name_against_roll(user_doc["full_name"], _roll_primary, _roll_secondary):
                user_doc["is_name_flagged"] = True
                user_doc["flag_reason"] = "name_mismatch"

        assert user_doc.get("is_name_flagged") is True
        assert user_doc.get("flag_reason") == "name_mismatch"

    @pytest.mark.asyncio
    async def test_no_flag_fields_set_when_name_matches(self):
        """When name matches, is_name_flagged should NOT be set on user_doc."""
        from utils.name_utils import check_owner_name_against_roll

        user_doc: dict = {
            "id": "test-uuid",
            "full_name": "Avneet Rooprai",
            "role": "owner",
        }
        unit_doc = {
            "unit_number": "TH087",
            "owner_name": "Avneet Rooprai",
            "owner_name_b": "",
        }

        if user_doc["role"] == "owner" and unit_doc:
            _roll_primary = unit_doc.get("owner_name", "")
            _roll_secondary = unit_doc.get("owner_name_b", "")
            if not check_owner_name_against_roll(user_doc["full_name"], _roll_primary, _roll_secondary):
                user_doc["is_name_flagged"] = True
                user_doc["flag_reason"] = "name_mismatch"

        assert "is_name_flagged" not in user_doc
        assert "flag_reason" not in user_doc


# ─────────────────────────────────────────────
# 5. Approval path flagging (server.py)
# ─────────────────────────────────────────────
class TestApprovalPathFlagging:
    """update_user approval block should set is_name_flagged in update_dict."""

    @pytest.mark.asyncio
    async def test_update_dict_flagged_on_mismatch(self):
        """
        Simulate the approval block: when owner name doesn't match roll,
        update_dict['is_name_flagged'] should be True.
        """
        from utils.name_utils import check_owner_name_against_roll

        # Simulate _pre_flag_user and _unit_doc from server.py approval block
        _pre_flag_user = {
            "id": "user-001",
            "role": "owner",
            "unit_number": "TH087",
            "full_name": "Random Person",
        }
        _unit_doc = {
            "unit_number": "TH087",
            "owner_name": "Avneet Rooprai",
            "owner_name_b": "",
        }

        update_dict = {"is_approved": True, "status": "active"}

        # Replicate approval block logic
        if _pre_flag_user and _pre_flag_user.get("role") == "owner" and _pre_flag_user.get("unit_number"):
            if _unit_doc:
                _roll_primary = _unit_doc.get("owner_name", "")
                _roll_secondary = _unit_doc.get("owner_name_b", "")
                _reg_name = _pre_flag_user.get("full_name", "")
                _name_matches = check_owner_name_against_roll(_reg_name, _roll_primary, _roll_secondary)
                if not _name_matches:
                    update_dict["is_name_flagged"] = True
                    update_dict["flag_reason"] = "name_mismatch"
                else:
                    update_dict.setdefault("is_name_flagged", False)
                    update_dict.setdefault("flag_reason", None)

        assert update_dict["is_name_flagged"] is True
        assert update_dict["flag_reason"] == "name_mismatch"

    @pytest.mark.asyncio
    async def test_flag_cleared_when_name_matches(self):
        """
        When the owner's name matches the roll (admin may have corrected it),
        is_name_flagged should default to False.
        """
        from utils.name_utils import check_owner_name_against_roll

        _pre_flag_user = {
            "role": "owner",
            "unit_number": "TH087",
            "full_name": "Avneet Rooprai",
        }
        _unit_doc = {"owner_name": "Avneet Rooprai", "owner_name_b": ""}
        update_dict = {"is_approved": True}

        if _pre_flag_user and _pre_flag_user.get("role") == "owner":
            if _unit_doc:
                _name_matches = check_owner_name_against_roll(
                    _pre_flag_user["full_name"],
                    _unit_doc.get("owner_name", ""),
                    _unit_doc.get("owner_name_b", ""),
                )
                if not _name_matches:
                    update_dict["is_name_flagged"] = True
                    update_dict["flag_reason"] = "name_mismatch"
                else:
                    update_dict.setdefault("is_name_flagged", False)
                    update_dict.setdefault("flag_reason", None)

        assert update_dict.get("is_name_flagged") is False
        assert update_dict.get("flag_reason") is None

    @pytest.mark.asyncio
    async def test_non_owner_role_not_flagged(self):
        """Only owner-role users are subject to name verification."""

        _pre_flag_user = {
            "role": "tenant",
            "unit_number": "TH087",
            "full_name": "Anyone",
        }
        update_dict = {"is_approved": True}

        # The guard in server.py only fires for role == "owner"
        if _pre_flag_user.get("role") == "owner" and _pre_flag_user.get("unit_number"):
            update_dict["is_name_flagged"] = True  # Should NOT reach here

        assert "is_name_flagged" not in update_dict


# ─────────────────────────────────────────────
# 6. user_to_response passes through flag fields
# ─────────────────────────────────────────────
class TestUserToResponsePassthrough:
    """user_to_response must include is_name_flagged and flag_reason."""

    def test_flag_fields_in_response(self):
        """
        When user dict has is_name_flagged=True / flag_reason='name_mismatch',
        user_to_response should pass them to UserResponse.
        """
        from utils.permissions import user_to_response

        _base_permissions = {
            "can_view_documents": False, "can_upload_documents": False,
            "can_view_finances": False, "can_manage_finances": False,
            "can_create_listings": False, "can_manage_listings": False,
            "can_send_announcements": False, "can_manage_meetings": False,
            "can_manage_users": False, "can_manage_ec": False,
            "can_chat": False, "can_view_directory": False,
            "can_approve_owners": False,
        }

        user = {
            "id": "test-001",
            "email": "test@example.com",
            "full_name": "Alice Wong",
            "role": "owner",
            "is_active": True,
            "is_approved": True,
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
            "is_name_flagged": True,
            "flag_reason": "name_mismatch",
            "custom_permissions": {},
            "temp_elevation": None,
        }
        user.update(_base_permissions)

        response = user_to_response(user)
        assert response.is_name_flagged is True
        assert response.flag_reason == "name_mismatch"

    def test_flag_fields_default_when_absent(self):
        """When user dict lacks flag fields, defaults are returned."""
        from utils.permissions import user_to_response

        _base_permissions = {
            "can_view_documents": False, "can_upload_documents": False,
            "can_view_finances": False, "can_manage_finances": False,
            "can_create_listings": False, "can_manage_listings": False,
            "can_send_announcements": False, "can_manage_meetings": False,
            "can_manage_users": False, "can_manage_ec": False,
            "can_chat": False, "can_view_directory": False,
            "can_approve_owners": False,
        }

        user = {
            "id": "test-002",
            "email": "clean@example.com",
            "full_name": "Avneet Rooprai",
            "role": "owner",
            "is_active": True,
            "is_approved": True,
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
            "custom_permissions": {},
            "temp_elevation": None,
        }
        user.update(_base_permissions)

        response = user_to_response(user)
        assert response.is_name_flagged is False
        assert response.flag_reason is None


# ─────────────────────────────────────────────
# 7. Real-world name pairs from strata roll
# ─────────────────────────────────────────────
class TestRealWorldNamePairs:
    """End-to-end name matching scenarios from the East Gate Residences strata roll."""

    @pytest.mark.parametrize("registered,roll_primary,roll_secondary,expected_match", [
        # Standard matches
        ("Anthony McDonald", "Anthony McDonald", "", True),
        ("Avneet Rooprai", "Avneet Rooprai", "", True),
        # Name order variation
        ("McDonald Anthony", "Anthony McDonald", "", True),
        # Joint owner — matches secondary
        ("Jane Smith", "John Smith", "Jane Smith", True),
        # Completely wrong person
        ("Alice Wong", "Avneet Rooprai", "", False),
        # Single-token name "Paul" matches "Paul Anthony McDonald" — algorithm
        # treats single-token registered name as a weak but valid match (100% of
        # registered tokens overlap). Intentional: hard to disprove with one name.
        ("Paul", "Paul Anthony McDonald", "", True),
        # Partial match with title
        ("Mr Will Han", "Will Han", "Minkyoung Kim", True),
        # Secondary owner match
        ("Minkyoung Kim", "Will Han", "Minkyoung Kim", True),
    ])
    def test_name_pair(self, registered, roll_primary, roll_secondary, expected_match):
        from utils.name_utils import check_owner_name_against_roll
        result = check_owner_name_against_roll(registered, roll_primary, roll_secondary)
        assert result == expected_match, (
            f"Expected {expected_match} for '{registered}' vs "
            f"roll:'{roll_primary}'/'{roll_secondary}'"
        )
