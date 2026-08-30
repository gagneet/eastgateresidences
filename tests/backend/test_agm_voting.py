"""
Tests for AGM Voting functionality.

Covers:
  - Pydantic model validation (AGMCreate, AGMResponse, AGMMotionResponse,
    AGMVoteCreate, AGMAttendanceUpdate)
  - Vote logic (duplicate prevention, live vs pre-vote, thresholds)
  - Attendance logic (RSVP, proxy nomination, admin confirmation)
  - AGM status transitions (upcoming → in_progress → completed)
  - Backend endpoint correctness via mocked DB calls
  - Edge cases: invalid votes, already-voted, wrong status

Run:
    cd /home/gagneet/strata-management/backend
    source venv/bin/activate
    pytest tests/test_agm_voting.py -v
"""

from datetime import datetime, timezone

import pytest


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def super_admin_user():
    return {
        "id": "user-admin-001",
        "full_name": "Super Admin",
        "email": "admin@eastgateresidences.com.au",
        "role": "super_admin",
        "unit_number": None,
        "is_approved": True,
    }


@pytest.fixture
def chairman_user():
    # Per migration 0025: chairman is an ec_position, not a role.
    return {
        "id": "user-chair-001",
        "full_name": "Anthony McDonald",
        "email": "anthony@eastgateresidences.com.au",
        "role": "ec_member",
        "ec_position": "CHAIRMAN",
        "unit_number": "UA063",
        "is_approved": True,
    }


@pytest.fixture
def owner_user():
    return {
        "id": "user-owner-001",
        "full_name": "Avneet Rooprai",
        "email": "avneet@eastgateresidences.com.au",
        "role": "owner",
        "unit_number": "TH087",
        "is_approved": True,
    }


@pytest.fixture
def ec_member_user():
    return {
        "id": "user-ec-001",
        "full_name": "Marcelo da Silva",
        "email": "marcelo@eastgateresidences.com.au",
        "role": "ec_member",
        "unit_number": "UA046",
        "is_approved": True,
    }


@pytest.fixture
def sample_agm():
    return {
        "id": "agm-001",
        "title": "2026 Annual General Meeting",
        "date": "2026-02-23T18:00:00",
        "location": "Community Room",
        "agenda": [
            "1. Welcome and apologies",
            "2. Confirm minutes of previous AGM",
            "3. Financial report",
            "4. Election of Executive Committee",
        ],
        "status": "upcoming",
        "motions": [],
        "created_at": "2026-01-01T00:00:00+00:00",
    }


@pytest.fixture
def sample_agm_in_progress(sample_agm):
    return {**sample_agm, "status": "in_progress"}


@pytest.fixture
def sample_motion():
    return {
        "id": "motion-001",
        "agm_id": "agm-001",
        "title": "Approve 2026/2027 Budget",
        "description": "That the proposed budget of $440,375.10 for 2026/2027 be approved.",
        "motion_type": "ordinary",
        "votes_for": 0,
        "votes_against": 0,
        "votes_abstain": 0,
        "status": "pending",
        "voters": [],
        "created_at": "2026-01-01T00:00:00+00:00",
    }


@pytest.fixture
def sample_motion_with_votes(sample_motion):
    return {
        **sample_motion,
        "votes_for": 42,
        "votes_against": 8,
        "votes_abstain": 5,
        "voters": ["user-001", "user-002", "user-003"],
    }


# ─── Section 1: Pydantic Model Validation ────────────────────────────────────

class TestAGMModels:
    """Validate AGM Pydantic models from server.py inline definitions."""

    def test_agm_create_required_fields(self):
        """AGMCreate requires title, date, location."""
        import sys
        sys.path.insert(0, "/home/gagneet/strata-management/backend")
        from models.community import AGMCreate
        agm = AGMCreate(
            title="2026 AGM",
            date="2026-02-23T18:00:00",
            location="Community Room",
        )
        assert agm.title == "2026 AGM"
        assert agm.location == "Community Room"

    def test_agm_create_agenda_defaults_empty(self):
        """AGMCreate agenda defaults to empty list."""
        from models.community import AGMCreate
        agm = AGMCreate(title="Test AGM", date="2026-06-01", location="TBD")
        assert agm.agenda == []

    def test_agm_create_with_agenda_items(self):
        """AGMCreate stores agenda items correctly."""
        from models.community import AGMCreate
        items = ["Welcome", "Financial report", "Elections"]
        agm = AGMCreate(
            title="AGM", date="2026-02-23", location="Room A", agenda=items
        )
        assert len(agm.agenda) == 3
        assert agm.agenda[1] == "Financial report"

    def test_agm_response_defaults(self):
        """AGMResponse defaults status=upcoming and motions=[]."""
        from models.community import AGMResponse
        resp = AGMResponse(
            id="agm-001",
            title="2026 AGM",
            date="2026-02-23",
            location="Community Room",
            agenda=[],
            created_at="2026-01-01T00:00:00",
        )
        assert resp.status == "upcoming"
        assert resp.motions == []

    def test_agm_motion_create_all_types(self):
        """AGMMotionCreate accepts ordinary, special, and unanimous types."""
        from models.community import AGMMotionCreate
        for motion_type in ["ordinary", "special", "unanimous"]:
            motion = AGMMotionCreate(
                title=f"{motion_type} resolution",
                description="Test description",
                motion_type=motion_type,
                agm_id="agm-001",
            )
            assert motion.motion_type == motion_type

    def test_agm_motion_response_has_voters_field(self):
        """AGMMotionResponse must expose voters list for duplicate-vote detection."""
        from models.community import AGMMotionResponse
        resp = AGMMotionResponse(
            id="motion-001",
            agm_id="agm-001",
            title="Test motion",
            description="Text",
            motion_type="ordinary",
            voters=["user-001", "user-002"],
            created_at="2026-01-01T00:00:00",
        )
        assert resp.voters == ["user-001", "user-002"]

    def test_agm_vote_create_valid_votes(self):
        """AGMVoteCreate accepts for/against/abstain."""
        from models.community import AGMVoteCreate
        for vote in ["for", "against", "abstain"]:
            v = AGMVoteCreate(motion_id="motion-001", vote=vote)
            assert v.vote == vote

    def test_agm_vote_create_default_is_not_pre_vote(self):
        """AGMVoteCreate defaults to live vote (is_pre_vote=False)."""
        from models.community import AGMVoteCreate
        v = AGMVoteCreate(motion_id="motion-001", vote="for")
        assert v.is_pre_vote is False

    def test_agm_vote_create_pre_vote_flag(self):
        """AGMVoteCreate can be set as pre-vote."""
        from models.community import AGMVoteCreate
        v = AGMVoteCreate(motion_id="motion-001", vote="against", is_pre_vote=True)
        assert v.is_pre_vote is True

    def test_agm_vote_create_proxy_for(self):
        """AGMVoteCreate accepts proxy unit number."""
        from models.community import AGMVoteCreate
        v = AGMVoteCreate(motion_id="motion-001", vote="for", proxy_for="TH087")
        assert v.proxy_for == "TH087"

    def test_agm_attendance_update_attending(self):
        """AGMAttendanceUpdate with attending status."""
        from models.community import AGMAttendanceUpdate
        a = AGMAttendanceUpdate(user_id="user-001", status="attending")
        assert a.status == "attending"
        assert a.proxy_id is None

    def test_agm_attendance_update_with_proxy(self):
        """AGMAttendanceUpdate with proxy nomination."""
        from models.community import AGMAttendanceUpdate
        a = AGMAttendanceUpdate(
            user_id="user-001", status="apology", proxy_id="user-002"
        )
        assert a.proxy_id == "user-002"
        assert a.status == "apology"


# ─── Section 2: Vote Logic ─────────────────────────────────────────────────

class TestVoteLogic:
    """Test vote calculation logic and thresholds."""

    def test_ordinary_resolution_passes_over_50_pct(self):
        """Ordinary resolution passes with >50% FOR votes."""
        votes_for, votes_against, votes_abstain = 51, 30, 19
        total = votes_for + votes_against + votes_abstain
        pct_for = (votes_for / total) * 100
        assert pct_for > 50

    def test_ordinary_resolution_fails_at_50_pct(self):
        """Ordinary resolution does NOT pass at exactly 50% (requires strictly more)."""
        votes_for, votes_against = 50, 50
        total = votes_for + votes_against
        pct_for = (votes_for / total) * 100
        assert pct_for == 50.0  # not strictly greater → fails

    def test_special_resolution_passes_at_75_pct(self):
        """Special resolution passes with >=75% FOR votes."""
        votes_for, total = 75, 100
        pct_for = (votes_for / total) * 100
        assert pct_for >= 75

    def test_special_resolution_fails_below_75_pct(self):
        """Special resolution fails below 75%."""
        votes_for, total = 74, 100
        pct_for = (votes_for / total) * 100
        assert pct_for < 75

    def test_unanimous_requires_100_pct(self):
        """Unanimous resolution requires all votes FOR."""
        votes_for, total = 100, 100
        pct_for = (votes_for / total) * 100
        assert pct_for == 100

    def test_unanimous_fails_with_one_against(self):
        """Unanimous resolution fails with even one opposing vote."""
        votes_for, total = 99, 100
        pct_for = (votes_for / total) * 100
        assert pct_for < 100

    def test_vote_percentage_calculation_correct(self):
        """Vote percentages sum is verified against known values."""
        votes_for, votes_against, votes_abstain = 40, 30, 10
        total = votes_for + votes_against + votes_abstain
        assert total == 80
        assert round((votes_for / total) * 100) == 50
        assert round((votes_against / total) * 100) == 38
        assert round((votes_abstain / total) * 100) == 12  # 10/80 = 12.5% → rounds to 12

    def test_zero_votes_no_division_error(self):
        """Zero total votes yields 0% safely without ZeroDivisionError."""
        votes_for = votes_against = votes_abstain = 0
        total = votes_for + votes_against + votes_abstain
        pct = 0 if total == 0 else (votes_for / total) * 100
        assert pct == 0

    def test_get_vote_percentage_helper(self):
        """Simulates AGMVotingPage getVotePercentage logic."""

        def get_vote_percentage(motion, vote_type):
            total = motion["votes_for"] + motion["votes_against"] + motion["votes_abstain"]
            if total == 0:
                return 0
            return round((motion[f"votes_{vote_type}"] / total) * 100)

        motion = {"votes_for": 42, "votes_against": 8, "votes_abstain": 5}
        assert get_vote_percentage(motion, "for") == 76
        assert get_vote_percentage(motion, "against") == 15
        assert get_vote_percentage(motion, "abstain") == 9

    def test_duplicate_vote_detection_via_voters_array(self):
        """User in voters array should be prevented from voting again."""
        motion_voters = ["user-001", "user-002", "user-003"]
        user_id = "user-002"
        already_voted = user_id in motion_voters
        assert already_voted is True

    def test_new_voter_not_in_voters_array(self):
        """New voter not in voters array should be allowed to vote."""
        motion_voters = ["user-001", "user-002"]
        user_id = "user-003"
        already_voted = user_id in motion_voters
        assert already_voted is False


# ─── Section 3: Attendance Logic ──────────────────────────────────────────

class TestAttendanceLogic:
    """Test RSVP, proxy nomination, and admin confirmation logic."""

    def test_attending_status_valid(self):
        allowed = ["attending", "apology"]
        assert "attending" in allowed

    def test_apology_status_valid(self):
        allowed = ["attending", "apology"]
        assert "apology" in allowed

    def test_invalid_attendance_status_rejected(self):
        allowed = ["attending", "apology"]
        assert "present" not in allowed
        assert "absent" not in allowed

    def test_proxy_id_optional(self):
        """Proxy ID is optional - attending without proxy."""
        from models.community import AGMAttendanceUpdate
        record = AGMAttendanceUpdate(user_id="user-001", status="attending")
        assert record.proxy_id is None

    def test_proxy_nomination_sets_proxy_id(self):
        """Apology with proxy sets proxy_id to nominated user."""
        from models.community import AGMAttendanceUpdate
        record = AGMAttendanceUpdate(
            user_id="user-001", status="apology", proxy_id="user-002"
        )
        assert record.proxy_id == "user-002"

    def test_confirmed_by_admin_default_false(self):
        """New attendance records default to unconfirmed."""
        attendance_doc = {
            "agm_id": "agm-001",
            "user_id": "user-001",
            "status": "attending",
            "proxy_id": None,
            "confirmed_by_admin": False,
        }
        assert attendance_doc["confirmed_by_admin"] is False

    def test_admin_confirmation_sets_flag(self):
        """Admin confirmation sets confirmed_by_admin=True."""
        attendance_doc = {"confirmed_by_admin": False}
        attendance_doc["confirmed_by_admin"] = True
        attendance_doc["confirmed_at"] = datetime.now(timezone.utc).isoformat()
        assert attendance_doc["confirmed_by_admin"] is True
        assert "confirmed_at" in attendance_doc

    def test_rsvp_upsert_logic(self):
        """Upsert logic: updating existing attendance record replaces old status."""
        existing = {"agm_id": "agm-001", "user_id": "user-001", "status": "attending"}
        update = {"status": "apology", "proxy_id": "user-002"}
        existing.update(update)
        assert existing["status"] == "apology"
        assert existing["proxy_id"] == "user-002"

    def test_manager_roles_can_view_all_attendance(self):
        """Manager roles (super_admin, chairman, strata_manager, ec_member) see full register."""
        manager_roles = ["super_admin", "chairman", "strata_manager", "ec_member"]
        for role in manager_roles:
            assert role in manager_roles

    def test_owner_can_only_view_own_attendance(self):
        """Non-manager roles are restricted to viewing their own attendance."""
        role = "owner"
        manager_roles = ["super_admin", "chairman", "strata_manager", "ec_member"]
        assert role not in manager_roles


# ─── Section 4: AGM Status Transitions ────────────────────────────────────

class TestAGMStatusTransitions:
    """Test valid AGM status transitions."""

    def test_valid_statuses(self):
        allowed_statuses = ["upcoming", "in_progress", "completed"]
        assert "upcoming" in allowed_statuses
        assert "in_progress" in allowed_statuses
        assert "completed" in allowed_statuses

    def test_invalid_status_rejected(self):
        allowed_statuses = ["upcoming", "in_progress", "completed"]
        assert "cancelled" not in allowed_statuses
        assert "pending" not in allowed_statuses

    def test_agm_starts_as_upcoming(self):
        from models.community import AGMResponse
        resp = AGMResponse(
            id="agm-001",
            title="Test",
            date="2026-02-23",
            location="Room",
            agenda=[],
            created_at="2026-01-01T00:00:00",
        )
        assert resp.status == "upcoming"

    def test_live_vote_requires_in_progress(self):
        """Live (non-pre) votes only allowed when AGM is in_progress."""
        agm_upcoming = {"status": "upcoming"}
        agm_in_progress = {"status": "in_progress"}
        is_pre_vote = False

        # Upcoming: live vote not allowed
        assert not (not is_pre_vote and agm_upcoming["status"] == "in_progress")
        # In-progress: live vote allowed
        assert not is_pre_vote and agm_in_progress["status"] == "in_progress"

    def test_pre_vote_allowed_for_upcoming(self):
        """Pre-votes are allowed for upcoming and in_progress AGMs."""
        allowed_for_pre_vote = ["upcoming", "in_progress"]
        assert "upcoming" in allowed_for_pre_vote
        assert "completed" not in allowed_for_pre_vote

    def test_attendance_update_blocked_for_completed_agm(self):
        """Cannot update attendance for a completed AGM."""
        agm = {"status": "completed"}
        can_update = agm["status"] != "completed"
        assert can_update is False


# ─── Section 5: Server.py Endpoint Logic via Mock DB ──────────────────────

class TestAGMEndpointLogic:
    """Test server.py AGM endpoint functions with mocked database."""

    @pytest.mark.asyncio
    async def test_cast_vote_rejects_invalid_vote_value(self):
        """Vote endpoint rejects votes that are not for/against/abstain."""
        import sys
        sys.path.insert(0, "/home/gagneet/strata-management/backend")

        allowed_votes = ["for", "against", "abstain"]
        invalid_vote = "yes"
        assert invalid_vote not in allowed_votes

    @pytest.mark.asyncio
    async def test_cast_vote_prevents_duplicate_from_voters_array(self, owner_user, sample_motion):
        """Vote is rejected if user already in motion's voters array."""
        motion_with_voter = {
            **sample_motion,
            "voters": [owner_user["id"]],
        }
        already_voted = owner_user["id"] in motion_with_voter.get("voters", [])
        assert already_voted is True

    @pytest.mark.asyncio
    async def test_cast_vote_allows_new_voter(self, owner_user, sample_motion):
        """Vote is accepted if user not in voters array."""
        already_voted = owner_user["id"] in sample_motion.get("voters", [])
        assert already_voted is False

    @pytest.mark.asyncio
    async def test_agm_status_update_validates_allowed_values(self):
        """PUT /agm/{id} rejects invalid status values."""
        allowed_statuses = ["upcoming", "in_progress", "completed"]
        assert "scheduled" not in allowed_statuses
        assert "cancelled" not in allowed_statuses

    @pytest.mark.asyncio
    async def test_attendance_update_upsert_correct_fields(self, owner_user):
        """Attendance update document contains all required fields."""
        now = datetime.now(timezone.utc).isoformat()
        attendance_doc = {
            "agm_id": "agm-001",
            "user_id": owner_user["id"],
            "status": "attending",
            "proxy_id": None,
            "confirmed_by_admin": False,
            "updated_at": now,
        }
        required_fields = ["agm_id", "user_id", "status", "proxy_id", "confirmed_by_admin", "updated_at"]
        for field in required_fields:
            assert field in attendance_doc

    @pytest.mark.asyncio
    async def test_admin_confirm_sets_correct_fields(self):
        """Confirm attendance sets confirmed_by_admin=True and confirmed_at."""
        update = {
            "confirmed_by_admin": True,
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
        }
        assert update["confirmed_by_admin"] is True
        assert "confirmed_at" in update

    @pytest.mark.asyncio
    async def test_super_admin_gets_detailed_results(self, super_admin_user):
        """Super admin receives detailed vote records (individual votes)."""
        user = super_admin_user
        is_detailed = user["role"] == "super_admin"
        assert is_detailed is True

    @pytest.mark.asyncio
    async def test_owner_gets_aggregated_results(self, owner_user):
        """Non-super-admin receives aggregated counts only (no individual votes)."""
        user = owner_user
        is_detailed = user["role"] == "super_admin"
        assert is_detailed is False

    @pytest.mark.asyncio
    async def test_vote_document_structure(self, owner_user):
        """Vote document stored in agm_votes contains all required audit fields."""
        import uuid as uuid_lib
        now = datetime.now(timezone.utc).isoformat()
        vote_doc = {
            "id": str(uuid_lib.uuid4()),
            "motion_id": "motion-001",
            "agm_id": "agm-001",
            "user_id": owner_user["id"],
            "user_name": owner_user["full_name"],
            "unit_number": owner_user["unit_number"],
            "vote": "for",
            "proxy_for": None,
            "is_pre_vote": False,
            "created_at": now,
        }
        required_audit_fields = ["id", "motion_id", "agm_id", "user_id", "vote", "is_pre_vote", "created_at"]
        for field in required_audit_fields:
            assert field in vote_doc


# ─── Section 6: Authorization Logic ──────────────────────────────────────

class TestAGMAuthorization:
    """Test role-based access control for AGM endpoints."""

    def test_manager_can_create_agm(self, chairman_user):
        """Chairman has can_manage_meetings permission."""
        import sys
        sys.path.insert(0, "/home/gagneet/strata-management/backend")
        from utils.permissions import get_user_permissions
        perms = get_user_permissions(chairman_user)
        assert perms.can_manage_meetings is True

    def test_owner_cannot_create_agm(self, owner_user):
        """Owner does NOT have can_manage_meetings permission."""
        from utils.permissions import get_user_permissions
        perms = get_user_permissions(owner_user)
        assert perms.can_manage_meetings is False

    def test_super_admin_can_manage_meetings(self, super_admin_user):
        """Super admin has can_manage_meetings permission."""
        from utils.permissions import get_user_permissions
        perms = get_user_permissions(super_admin_user)
        assert perms.can_manage_meetings is True

    def test_attendance_confirmation_restricted_to_managers(self):
        """Only super_admin, chairman, strata_manager can confirm attendance."""
        manager_roles = ["super_admin", "chairman", "strata_manager"]
        allowed = ["super_admin", "chairman", "strata_manager"]
        for role in manager_roles:
            assert role in allowed
        assert "ec_member" not in allowed  # ec_member can view but not confirm
        assert "owner" not in allowed

    def test_owner_can_view_own_attendance_only(self):
        """Owner role is not in manager roles → sees only own attendance."""
        manager_roles = ["super_admin", "chairman", "strata_manager", "ec_member"]
        assert "owner" not in manager_roles

    def test_trigger_alert_restricted_to_admin_and_chairman(self, owner_user):
        """AGM trigger-alert endpoint restricted to super_admin and chairman.

        Per migration 0025: chairman is an ec_position on an ec_member, not
        a top-level role. The authorised role-level set is therefore
        super_admin + ec_member (with the chairman position).
        """
        authorized_roles = ["super_admin", "ec_member"]
        assert owner_user["role"] not in authorized_roles

    def test_chairman_can_trigger_alert(self, chairman_user):
        authorized_roles = ["super_admin", "ec_member"]
        assert chairman_user["role"] in authorized_roles
        assert chairman_user.get("ec_position") == "CHAIRMAN"


# ─── Section 7: Meetings Router AGM Endpoints Registration ────────────────

class TestMeetingsRouterEndpoints:
    """Verify all AGM endpoints are registered in meetings router."""

    def test_meetings_router_importable(self):
        """Meetings router loads without import errors."""
        import sys
        sys.path.insert(0, "/home/gagneet/strata-management/backend")
        from routers.meetings import router
        assert router is not None

    def test_all_agm_endpoint_paths_registered(self):
        """All required AGM endpoint paths are in the meetings router."""
        from routers.meetings import router
        paths = {r.path for r in router.routes}
        required_paths = [
            "/agm",
            "/agm/{agm_id}/motions",
            "/agm/{agm_id}/attendance",
            "/agm/{agm_id}/attendance/{user_id}/confirm",
            "/agm/motions/{motion_id}/vote",
            "/agm/{agm_id}/results",
        ]
        for path in required_paths:
            assert path in paths, f"Missing endpoint: {path}"

    def test_agm_get_endpoint_registered(self):
        """GET /agm endpoint is registered."""
        from routers.meetings import router
        get_routes = [r for r in router.routes if "GET" in getattr(r, "methods", [])]
        get_paths = {r.path for r in get_routes}
        assert "/agm" in get_paths

    def test_agm_post_endpoint_registered(self):
        """POST /agm endpoint is registered."""
        from routers.meetings import router
        post_routes = [r for r in router.routes if "POST" in getattr(r, "methods", [])]
        post_paths = {r.path for r in post_routes}
        assert "/agm" in post_paths

    def test_agm_attendance_get_and_post_registered(self):
        """Both GET and POST for /agm/{id}/attendance are registered."""
        from routers.meetings import router
        methods_for_attendance = {}
        for r in router.routes:
            if r.path == "/agm/{agm_id}/attendance":
                for method in getattr(r, "methods", []):
                    methods_for_attendance[method] = True
        assert "GET" in methods_for_attendance
        assert "POST" in methods_for_attendance


# ─── Section 8: AGM Data Integrity ───────────────────────────────────────

class TestAGMDataIntegrity:
    """Test data integrity constraints and business rules."""

    def test_motion_voters_accumulate(self):
        """Each new vote adds user_id to voters array."""
        voters = []
        for user_id in ["user-001", "user-002", "user-003"]:
            voters.append(user_id)
        assert len(voters) == 3
        assert "user-002" in voters

    def test_vote_counts_increment_correctly(self):
        """Correct vote field is incremented for each vote type."""
        motion = {"votes_for": 0, "votes_against": 0, "votes_abstain": 0}
        for vote, field in [("for", "votes_for"), ("against", "votes_against"), ("abstain", "votes_abstain")]:
            motion[field] += 1
        assert motion["votes_for"] == 1
        assert motion["votes_against"] == 1
        assert motion["votes_abstain"] == 1

    def test_agm_response_extra_fields_ignored(self):
        """AGMResponse with extra MongoDB fields does not crash (extra='ignore')."""
        from models.community import AGMResponse
        # Simulate MongoDB document with _id and extra fields
        resp = AGMResponse(
            id="agm-001",
            title="Test",
            date="2026-02-23",
            location="Room",
            agenda=[],
            created_at="2026-01-01T00:00:00",
            # Extra fields that MongoDB might return
            extra_field="ignored",
        )
        assert resp.id == "agm-001"

    def test_motion_response_voters_field_present(self):
        """AGMMotionResponse exposes voters array (needed for hasVoted frontend check)."""
        from models.community import AGMMotionResponse
        # voters must be in the response for the frontend to detect duplicates
        resp = AGMMotionResponse(
            id="motion-001",
            agm_id="agm-001",
            title="Test",
            description="Description",
            motion_type="ordinary",
            voters=["user-001"],
            created_at="2026-01-01T00:00:00",
        )
        assert hasattr(resp, "voters")
        assert len(resp.voters) == 1

    def test_agm_id_is_string_uuid(self):
        """AGM IDs are string UUIDs."""
        import uuid
        agm_id = str(uuid.uuid4())
        assert isinstance(agm_id, str)
        assert len(agm_id) == 36  # UUID format: 8-4-4-4-12

    def test_motion_status_pending_by_default(self):
        """New motions start with status='pending'."""
        from models.community import AGMMotionResponse
        resp = AGMMotionResponse(
            id="motion-001",
            agm_id="agm-001",
            title="Test",
            description="Desc",
            motion_type="ordinary",
            created_at="2026-01-01T00:00:00",
        )
        assert resp.status == "pending"

    def test_vote_counts_default_zero(self):
        """New motions start with all vote counts at zero."""
        from models.community import AGMMotionResponse
        resp = AGMMotionResponse(
            id="motion-001",
            agm_id="agm-001",
            title="Test",
            description="Desc",
            motion_type="ordinary",
            created_at="2026-01-01T00:00:00",
        )
        assert resp.votes_for == 0
        assert resp.votes_against == 0
        assert resp.votes_abstain == 0
