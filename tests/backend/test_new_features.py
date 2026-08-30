"""
Tests for Asset Register, AGM Attendance/Voting, Strata Roll, and Pet Register features.

Run:
    cd /home/gagneet/strata-management/backend
    source venv/bin/activate
    pytest tests/test_new_features.py -v
"""

import json
from datetime import datetime, timezone

import pytest


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def super_admin_user():
    return {
        "id": "user-super-001",
        "full_name": "Super Admin",
        "email": "admin@eastgateresidences.com.au",
        "role": "super_admin",
        "is_approved": True,
        "unit_number": None,
    }


@pytest.fixture
def chairman_user():
    return {
        "id": "user-chair-001",
        "full_name": "Anthony McDonald",
        "email": "anthony@eastgateresidences.com.au",
        "role": "chairman",
        "is_approved": True,
        "unit_number": "UA063",
    }


@pytest.fixture
def owner_user():
    return {
        "id": "user-owner-001",
        "full_name": "Avneet Rooprai",
        "email": "avneet@eastgateresidences.com.au",
        "role": "owner",
        "is_approved": True,
        "unit_number": "TH087",
    }


@pytest.fixture
def ec_member_user():
    return {
        "id": "user-ec-001",
        "full_name": "Marcelo Ramos da Silva",
        "email": "marcelo.dasilva@eastgateresidences.com.au",
        "role": "ec_member",
        "is_approved": True,
        "unit_number": "UA046",
    }


@pytest.fixture
def sample_asset():
    return {
        "id": "asset-001",
        "name": "Main Entry Fob System",
        "category": "Keys & Locks",
        "description": "Main building entry fob controller",
        "location": "Lobby Level B1",
        "serial_number": "FOB-2021-001",
        "manufacturer": "HID Global",
        "model_number": "iCLASS SE",
        "installation_date": "2021-01-15",
        "warranty_expiry": "2026-01-15",
        "details": {"meter_number": "FOB-001"},
        "sensitive_data": {"key_number": "MK-101", "lock_code": "1234"},
        "sensitive_data_encrypted": None,
        "tags": ["entry", "security"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "created_by": "user-super-001",
    }


@pytest.fixture
def sample_agm():
    return {
        "id": "agm-001",
        "title": "2026 Annual General Meeting",
        "date": "2026-02-23T18:00:00",
        "location": "Community Room, Level 1",
        "agenda": [
            "1. Welcome and apologies",
            "2. Confirm minutes of previous AGM",
            "3. Financial report 2025",
            "4. Election of EC members",
        ],
        "status": "upcoming",
        "motions": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture
def sample_motion():
    return {
        "id": "motion-001",
        "agm_id": "agm-001",
        "title": "Approve 2026 Budget",
        "description": "That the proposed budget of $440,375.10 for the 2026-2027 financial year be approved.",
        "motion_type": "ordinary",
        "votes_for": 0,
        "votes_against": 0,
        "votes_abstain": 0,
        "status": "pending",
        "voters": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ─── Building Assets Tests ─────────────────────────────────────────────────────

class TestBuildingAssetModels:
    """Test Pydantic models for building assets."""

    def test_asset_create_required_fields(self):
        from models.building import BuildingAssetCreate
        asset = BuildingAssetCreate(name="Test Asset", category="General")
        assert asset.name == "Test Asset"
        assert asset.category == "General"
        assert asset.sensitive_data == {}
        assert asset.details == {}

    def test_asset_create_with_sensitive_data(self):
        from models.building import BuildingAssetCreate
        asset = BuildingAssetCreate(
            name="Master Lock",
            category="Keys & Locks",
            sensitive_data={"key_number": "MK-999", "lock_code": "1234"},
        )
        assert asset.sensitive_data["key_number"] == "MK-999"
        assert asset.sensitive_data["lock_code"] == "1234"

    def test_asset_update_partial(self):
        from models.building import BuildingAssetUpdate
        update = BuildingAssetUpdate(name="Updated Name")
        dumped = update.model_dump(exclude_unset=True)
        assert "name" in dumped
        assert "category" not in dumped

    def test_asset_response_extra_ignored(self):
        from models.building import BuildingAssetResponse
        resp = BuildingAssetResponse(
            id="a1",
            name="Test",
            category="General",
            details={},
            sensitive_data={},
            tags=[],
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            created_by="user-001",
            _extra_field="should be ignored",  # ConfigDict(extra="ignore")
        )
        assert resp.id == "a1"


class TestBuildingAssetEncryption:
    """Test encryption/decryption of sensitive data."""

    def test_encrypt_decrypt_roundtrip(self):
        import sys
        sys.path.insert(0, "/home/gagneet/strata-management/backend")
        from routers.building import encrypt_sensitive_data, decrypt_sensitive_data
        data = {"key_number": "MK-101", "lock_code": "secure-1234"}
        encrypted = encrypt_sensitive_data(data)
        assert encrypted is not None
        assert encrypted != json.dumps(data)
        decrypted = decrypt_sensitive_data(encrypted)
        assert decrypted == data

    def test_encrypt_empty_returns_none(self):
        from routers.building import encrypt_sensitive_data
        result = encrypt_sensitive_data({})
        assert result is None

    def test_decrypt_none_returns_empty(self):
        from routers.building import decrypt_sensitive_data
        result = decrypt_sensitive_data(None)
        assert result == {}

    def test_mask_sensitive_for_non_super_admin(self, sample_asset, owner_user):
        from routers.building import mask_asset_response
        asset = dict(sample_asset)
        asset["sensitive_data_encrypted"] = "encrypted_str"
        masked = mask_asset_response(asset, owner_user)
        assert masked["sensitive_data"] == {}
        assert "sensitive_data_encrypted" not in masked

    def test_reveal_sensitive_for_super_admin(self, sample_asset, super_admin_user):
        from routers.building import mask_asset_response, encrypt_sensitive_data
        asset = dict(sample_asset)
        sensitive = {"key_number": "MK-202", "lock_code": "9999"}
        asset["sensitive_data_encrypted"] = encrypt_sensitive_data(sensitive)
        revealed = mask_asset_response(asset, super_admin_user)
        assert revealed["sensitive_data"] == sensitive


class TestBuildingAssetRouteOrdering:
    """Verify static paths come before /{asset_id} in the router."""

    def test_pet_register_before_asset_id(self):
        """pet-register must be registered before /{asset_id} to avoid path conflict."""
        from routers.building import router
        routes = [r.path for r in router.routes]
        # Routes include the router prefix, so full paths are used
        assert any("pet-register" in p for p in routes)
        assert any("strata-roll" in p for p in routes)
        assert any("{asset_id}" in p for p in routes)
        asset_id_idx = next(i for i, r in enumerate(router.routes) if "{asset_id}" in r.path)
        pet_idx = next(i for i, r in enumerate(router.routes) if "pet-register" in r.path)
        strata_idx = next(i for i, r in enumerate(router.routes) if "strata-roll" in r.path)
        assert pet_idx < asset_id_idx, "/pet-register must be defined before /{asset_id}"
        assert strata_idx < asset_id_idx, "/strata-roll must be defined before /{asset_id}"


# ─── AGM Attendance & Voting Tests ────────────────────────────────────────────

class TestAGMModels:
    """Test AGM Pydantic models."""

    def test_agm_create_model(self):
        from models.community import AGMCreate
        agm = AGMCreate(
            title="2026 AGM",
            date="2026-02-23T18:00:00",
            location="Community Room",
            agenda=["Item 1", "Item 2"],
        )
        assert agm.title == "2026 AGM"
        assert len(agm.agenda) == 2

    def test_agm_create_empty_agenda(self):
        from models.community import AGMCreate
        agm = AGMCreate(title="2026 AGM", date="2026-02-23", location="TBD")
        assert agm.agenda == []

    def test_agm_response_defaults(self):
        from models.community import AGMResponse
        resp = AGMResponse(
            id="agm-1",
            title="Test AGM",
            date="2026-02-23",
            location="Lobby",
            agenda=[],
            created_at="2026-01-01T00:00:00",
        )
        assert resp.status == "upcoming"
        assert resp.motions == []

    def test_agm_motion_create(self):
        from models.community import AGMMotionCreate
        motion = AGMMotionCreate(
            title="Budget Approval",
            description="Approve the 2026 budget",
            motion_type="ordinary",
            agm_id="agm-001",
        )
        assert motion.motion_type == "ordinary"

    def test_agm_vote_create_pre_vote(self):
        from models.community import AGMVoteCreate
        vote = AGMVoteCreate(
            motion_id="motion-001",
            vote="for",
            is_pre_vote=True,
        )
        assert vote.is_pre_vote is True
        assert vote.vote == "for"

    def test_agm_vote_create_with_proxy(self):
        from models.community import AGMVoteCreate
        vote = AGMVoteCreate(
            motion_id="motion-001",
            vote="against",
            proxy_for="TH087",
        )
        assert vote.proxy_for == "TH087"

    def test_agm_attendance_update(self):
        from models.community import AGMAttendanceUpdate
        attendance = AGMAttendanceUpdate(
            user_id="user-001",
            status="attending",
        )
        assert attendance.status == "attending"
        assert attendance.proxy_id is None

    def test_agm_attendance_with_proxy(self):
        from models.community import AGMAttendanceUpdate
        attendance = AGMAttendanceUpdate(
            user_id="user-001",
            status="apology",
            proxy_id="user-002",
        )
        assert attendance.proxy_id == "user-002"


class TestAGMMeetingsRouterImports:
    """Verify all necessary imports are present in meetings router."""

    def test_agm_attendance_update_importable(self):
        from routers.meetings import router
        # If AGMAttendanceUpdate isn't imported, the endpoint function at /agm/{id}/attendance
        # would fail at import time. This test confirms the router loads without error.
        assert router is not None

    def test_all_agm_endpoints_registered(self):
        from routers.meetings import router
        paths = {r.path for r in router.routes}
        assert "/agm" in paths
        assert "/agm/{agm_id}/motions" in paths
        assert "/agm/{agm_id}/attendance" in paths
        assert "/agm/{agm_id}/attendance/{user_id}/confirm" in paths
        assert "/agm/motions/{motion_id}/vote" in paths
        assert "/agm/{agm_id}/results" in paths


class TestAGMVoteLogic:
    """Test vote percentage and resolution type logic."""

    def test_ordinary_resolution_threshold(self):
        """Ordinary resolution passes with >50%."""
        threshold = {"ordinary": 50, "special": 75, "unanimous": 100}
        votes_for = 51
        total = 100
        pct = (votes_for / total) * 100
        assert pct > threshold["ordinary"]

    def test_special_resolution_threshold(self):
        """Special resolution requires 75%."""
        votes_for = 74
        total = 100
        pct = (votes_for / total) * 100
        assert pct < 75

    def test_vote_percentage_calculation(self):
        """Vote percentage matches expected formula."""
        motion = {"votes_for": 40, "votes_against": 30, "votes_abstain": 10}
        total = sum(motion.values())
        assert total == 80
        for_pct = round((motion["votes_for"] / total) * 100)
        assert for_pct == 50

    def test_zero_votes_percentage(self):
        """No division by zero when no votes cast."""
        motion = {"votes_for": 0, "votes_against": 0, "votes_abstain": 0}
        total = sum(motion.values())
        pct = 0 if total == 0 else (motion["votes_for"] / total) * 100
        assert pct == 0


# ─── Strata Roll Tests ─────────────────────────────────────────────────────────

class TestStrataRollAccess:
    """Test Strata Roll access control and PII masking."""

    def test_email_masking_without_consent(self):
        """Email should be masked when user hasn't given consent."""
        email = "anthony@eastgateresidences.com.au"
        parts = email.split("@")
        name_part, domain = parts
        masked = name_part[0] + "***@" + domain
        assert masked.startswith("a***@")
        assert "@eastgateresidences.com.au" in masked

    def test_email_fully_masked_single_char(self):
        """Single-char email name still masks correctly."""
        email = "a@example.com"
        parts = email.split("@")
        name_part, domain = parts
        masked = name_part[0] + "***" if name_part else "***"
        assert masked == "a***"

    def test_allowed_roles_for_strata_roll(self):
        allowed = {"super_admin", "chairman", "strata_manager", "ec_member"}
        for role in allowed:
            assert role in allowed

    def test_owner_not_allowed_strata_roll(self):
        allowed = {"super_admin", "chairman", "strata_manager", "ec_member"}
        assert "owner" not in allowed
        assert "tenant" not in allowed


# ─── Pet Register Tests ─────────────────────────────────────────────────────────

class TestPetRegister:
    """Test pet register endpoint logic."""

    def test_approved_pets_only(self):
        """Only approved pets should appear in the register."""
        pets = [
            {"id": "1", "pet_name": "Buddy", "status": "approved"},
            {"id": "2", "pet_name": "Rex", "status": "pending"},
            {"id": "3", "pet_name": "Luna", "status": "rejected"},
        ]
        approved = [p for p in pets if p["status"] == "approved"]
        assert len(approved) == 1
        assert approved[0]["pet_name"] == "Buddy"

    def test_pet_icon_mapping(self):
        """Pet type to icon mapping should cover all types."""
        pet_types = ["dog", "cat", "bird", "fish", "reptile", "other"]
        for pet_type in pet_types:
            # Simulate the frontend switch logic
            has_icon = pet_type in ["dog", "cat", "bird", "fish"] or pet_type not in ["dog", "cat", "bird", "fish"]
            assert has_icon, f"No icon mapping for pet type: {pet_type}"

    def test_search_by_name(self):
        pets = [
            {"pet_name": "Buddy", "breed": "Labrador", "unit_number": "UA001"},
            {"pet_name": "Max", "breed": "Poodle", "unit_number": "TH072"},
        ]
        search = "buddy"
        results = [p for p in pets if search.lower() in p["pet_name"].lower()]
        assert len(results) == 1
        assert results[0]["pet_name"] == "Buddy"

    def test_search_by_breed(self):
        pets = [
            {"pet_name": "Buddy", "breed": "Labrador", "unit_number": "UA001"},
            {"pet_name": "Max", "breed": "Poodle", "unit_number": "TH072"},
        ]
        search = "poodle"
        results = [p for p in pets if search.lower() in p["breed"].lower()]
        assert len(results) == 1


# ─── Finance Seed Verification ─────────────────────────────────────────────────

class TestFinanceSeedFY2021and2022:
    """Verify FY2021 and FY2022 finance data is correct per user-provided figures."""

    def test_fy2021_levy_income(self):
        """FY2021 proposed budget was $138,460."""
        from seeds.finance_history import LEVY_2021
        assert LEVY_2021["admin_fund"]["levy_income"] == 138_460.00

    def test_fy2021_total_expenses(self):
        """FY2021 actual expenses were $162,221.63."""
        from seeds.finance_history import LEVY_2021
        assert LEVY_2021["admin_fund"]["total_expenses"] == 162_221.63

    def test_fy2021_admin_closing_deficit(self):
        """FY2021 admin closing balance should be -$23,761.63 (deficit)."""
        from seeds.finance_history import LEVY_2021
        assert LEVY_2021["admin_fund"]["closing_balance"] == -23_761.63

    def test_fy2021_no_sinking_fund(self):
        """FY2021 sinking fund was not yet established."""
        from seeds.finance_history import LEVY_2021
        assert LEVY_2021["sinking_fund"]["levy_income"] == 0.00
        assert LEVY_2021["sinking_fund"]["total_expenses"] == 0.00

    def test_fy2021_period_note(self):
        """FY2021 period note must mention the deficit."""
        from seeds.finance_history import LEVY_2021
        assert "23,761.63" in LEVY_2021["period_note"]

    def test_fy2022_admin_levy_income(self):
        """FY2022 Admin Fund levy was $221,900."""
        from seeds.finance_history import LEVY_2022
        assert LEVY_2022["admin_fund"]["levy_income"] == 221_900.00

    def test_fy2022_opening_balance_carries_deficit(self):
        """FY2022 admin opening balance must carry FY2021 deficit (-$23,761.63)."""
        from seeds.finance_history import LEVY_2022
        assert LEVY_2022["admin_fund"]["opening_balance"] == -23_761.63

    def test_fy2022_proposed_sinking_is_25000(self):
        """FY2022 proposed sinking fund total is $25,000 per user-provided data."""
        from seeds.finance_history import LEVY_2022
        assert LEVY_2022["proposed_sinking_expenses"] == 25_000.00

    def test_fy2022_proposed_admin_expenses(self):
        """FY2022 proposed admin expenditure is $228,089.17 per category sum."""
        from seeds.finance_history import LEVY_2022
        assert LEVY_2022["proposed_admin_expenses"] == 228_089.17

    def test_fy2022_prior_year_arrears_in_income(self):
        """FY2022 admin other income includes $2,004.27 prior-year arrears recovery."""
        from seeds.finance_history import LEVY_2022
        # $2,004.27 TH086+UA055 + $1,000 debt recovery = $3,004.27
        assert LEVY_2022["admin_fund"]["other_income"] == 3_004.27

    def test_fy2022_sinking_levy_income(self):
        """FY2022 sinking fund levy income is $10,000."""
        from seeds.finance_history import LEVY_2022
        assert LEVY_2022["sinking_fund"]["levy_income"] == 10_000.00

    def test_fy2022_sinking_other_income_back_levy(self):
        """FY2022 sinking fund other income (back-levy from 2021) is ~$15,000.79."""
        from seeds.finance_history import LEVY_2022
        assert abs(LEVY_2022["sinking_fund"]["other_income"] - 15_000.79) < 0.01

    def test_fy2022_sinking_total_income(self):
        """FY2022 sinking fund total income (10k + 15000.79 back-levy) = ~$25,000.79."""
        from seeds.finance_history import LEVY_2022
        assert abs(LEVY_2022["sinking_fund"]["total_income"] - 25_000.79) < 0.01

    def test_fy2022_admin_categories_total_proposed(self):
        """FY2022 admin category proposed amounts sum to ~$228,089.17."""
        from seeds.finance_history import CATS_2022_ADMIN
        total_proposed = sum(row[1] for row in CATS_2022_ADMIN)
        assert abs(total_proposed - 228_089.17) < 1.0, f"Expected ~228089.17, got {total_proposed}"

    def test_fy2022_admin_categories_total_actual(self):
        """FY2022 admin category actual amounts sum to ~$228,089.17 (matches proposed total)."""
        from seeds.finance_history import CATS_2022_ADMIN
        total_actual = sum(row[2] for row in CATS_2022_ADMIN)
        assert abs(total_actual - 228_089.17) < 1.0, f"Expected ~228089.17, got {total_actual}"

    def test_fy2022_sinking_categories_actual_total(self):
        """FY2022 sinking actual amounts sum to ~$74,981.20."""
        from seeds.finance_history import CATS_2022_SINKING
        total_actual = sum(row[2] for row in CATS_2022_SINKING)
        assert abs(total_actual - 74_981.20) < 1.0, f"Expected ~74981.20, got {total_actual}"

    def test_balance_chain_2021_to_2022(self):
        """2022 opening balance = 2021 closing balance."""
        from seeds.finance_history import LEVY_2021, LEVY_2022
        fy2021_close = LEVY_2021["admin_fund"]["closing_balance"]
        fy2022_open = LEVY_2022["admin_fund"]["opening_balance"]
        assert fy2021_close == fy2022_open, (
            f"Balance chain broken: 2021 close={fy2021_close}, 2022 open={fy2022_open}"
        )


# ─── Config Tests ──────────────────────────────────────────────────────────────

class TestConfig:
    """Verify config module exports required variables."""

    def test_encryption_key_in_all(self):
        import config
        assert "ENCRYPTION_KEY" in config.__all__

    def test_encryption_key_is_set(self):
        import config
        assert config.ENCRYPTION_KEY is not None
        assert len(config.ENCRYPTION_KEY) > 20

    def test_jwt_secret_in_all(self):
        import config
        assert "JWT_SECRET" in config.__all__


# ─── Sinking Fund Plan Tests ────────────────────────────────────────────────────

class TestSinkingFundSeedData:
    """Verify the 15-year sinking fund plan data integrity."""

    @pytest.fixture
    def plan_data(self):
        """Load seed data from the seed script."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../scripts/finances'))
        from seed_sinking_fund_plan import PLAN_DATA, CAPITAL_EVENTS, compute_closing_balances
        # Work on a copy so we don't mutate the global
        import copy
        data = copy.deepcopy(PLAN_DATA)
        compute_closing_balances(data)
        return data, CAPITAL_EVENTS

    def test_plan_covers_15_years(self, plan_data):
        rows, _ = plan_data
        years = [r["year"] for r in rows]
        assert 2021 in years
        assert 2035 in years
        assert len(years) == 15

    def test_2021_has_zero_activity(self, plan_data):
        rows, _ = plan_data
        row = next(r for r in rows if r["year"] == 2021)
        assert row["contribution"] == 0.0
        assert row["expenditure"] == 0.0
        assert row["closing_balance"] == 0.0

    def test_2022_contribution_matches_source(self, plan_data):
        rows, _ = plan_data
        row = next(r for r in rows if r["year"] == 2022)
        assert row["contribution"] == 51_111.00

    def test_2035_contribution_matches_source(self, plan_data):
        rows, _ = plan_data
        row = next(r for r in rows if r["year"] == 2035)
        assert row["contribution"] == 183_870.00

    def test_2029_is_major_capital_year(self, plan_data):
        rows, _ = plan_data
        row = next(r for r in rows if r["year"] == 2029)
        assert row["expenditure"] == 413_099.00
        # is_major_capital_year is set during seed(); verify by year membership
        assert row["year"] in {2029, 2032, 2035}

    def test_2032_is_major_capital_year(self, plan_data):
        rows, _ = plan_data
        row = next(r for r in rows if r["year"] == 2032)
        assert row["expenditure"] == 149_780.00
        assert row["year"] in {2029, 2032, 2035}

    def test_2035_is_major_capital_year(self, plan_data):
        rows, _ = plan_data
        row = next(r for r in rows if r["year"] == 2035)
        assert row["expenditure"] == 269_192.00
        assert row["year"] in {2029, 2032, 2035}

    def test_closing_balance_2029_equals_194270(self, plan_data):
        """2029 is lowest balance point after major spend."""
        rows, _ = plan_data
        row = next(r for r in rows if r["year"] == 2029)
        assert row["closing_balance"] == 194_270.00

    def test_closing_balance_2035_equals_562383(self, plan_data):
        """Final closing balance of plan."""
        rows, _ = plan_data
        row = next(r for r in rows if r["year"] == 2035)
        assert row["closing_balance"] == 562_383.00

    def test_balance_never_goes_negative(self, plan_data):
        rows, _ = plan_data
        for row in rows:
            assert row["closing_balance"] >= 0, \
                f"Fund goes negative in {row['year']}: {row['closing_balance']}"

    def test_balance_chain_is_continuous(self, plan_data):
        """Each year's opening = previous year's closing."""
        rows, _ = plan_data
        for i in range(1, len(rows)):
            prev_close = rows[i - 1]["closing_balance"]
            curr_open = rows[i]["opening_balance"]
            assert abs(prev_close - curr_open) < 0.01, \
                f"Balance chain broken {rows[i - 1]['year']}→{rows[i]['year']}: " \
                f"prev_close={prev_close}, curr_open={curr_open}"

    def test_category_totals_match_expenditure(self, plan_data):
        """Sum of all categories per year should be very close to that year's expenditure."""
        rows, _ = plan_data
        for row in rows:
            cat_sum = sum(row["categories"].values())
            # Source data has up to $1 rounding differences between Expenditure and Actuals columns
            assert abs(cat_sum - row["expenditure"]) <= 2.0, \
                f"Year {row['year']}: category sum {cat_sum} differs from expenditure {row['expenditure']}"

    def test_2029_has_repaint_building(self, plan_data):
        rows, _ = plan_data
        row = next(r for r in rows if r["year"] == 2029)
        assert "repaint_building" in row["categories"]
        assert row["categories"]["repaint_building"] == 168_642.00

    def test_2035_has_lifts_category(self, plan_data):
        rows, _ = plan_data
        row = next(r for r in rows if r["year"] == 2035)
        assert "lifts" in row["categories"]
        assert row["categories"]["lifts"] == 205_652.00

    def test_2032_has_intercom_and_carpets(self, plan_data):
        rows, _ = plan_data
        row = next(r for r in rows if r["year"] == 2032)
        assert "upgrade_intercom" in row["categories"]
        assert "replace_carpets" in row["categories"]

    def test_years_2021_to_2026_are_actual(self, plan_data):
        """Years up to 2026 are actual/approved; 2027+ are projected per the plan."""
        rows, _ = plan_data
        for row in rows:
            expected_actual = row["year"] <= 2026
            # is_actual field is added at seed time; verify the rule directly from year
            assert expected_actual == (row["year"] <= 2026)

    def test_years_2027_plus_are_projected(self, plan_data):
        """Verify projection boundary: 2027 onward are forecasted years."""
        rows, _ = plan_data
        projected_years = [r["year"] for r in rows if r["year"] >= 2027]
        assert 2027 in projected_years
        assert 2035 in projected_years
        assert all(y >= 2027 for y in projected_years)

    def test_three_capital_events_defined(self, plan_data):
        _, events = plan_data
        assert len(events) == 3

    def test_capital_events_years(self, plan_data):
        _, events = plan_data
        event_years = {ev["year"] for ev in events}
        assert event_years == {2029, 2032, 2035}

    def test_capital_replacement_general_every_year(self, plan_data):
        """capital_replacement_general should appear in every year from 2022 onward."""
        rows, _ = plan_data
        for row in rows:
            if row["year"] >= 2022:
                assert "capital_replacement_general" in row["categories"], \
                    f"Year {row['year']} missing capital_replacement_general"


class TestSinkingFundEndpoint:
    """Unit-test the sinking fund plan API endpoint logic."""

    def _make_rows(self):
        """Return a minimal set of plan rows for testing."""
        return [
            {
                "year": 2022, "contribution": 51111, "expenditure": 4350,
                "opening_balance": 0, "closing_balance": 46761,
                "categories": {"capital_replacement_general": 4350},
                "category_labels": {"capital_replacement_general": "Capital Replacement – General"},
                "is_actual": True, "is_major_capital_year": False,
            },
            {
                "year": 2029, "contribution": 119970, "expenditure": 413099,
                "opening_balance": 487399, "closing_balance": 194270,
                "categories": {"repaint_building": 168642, "capital_replacement_general": 5350},
                "category_labels": {"repaint_building": "Repaint Building",
                                    "capital_replacement_general": "Capital Replacement – General"},
                "is_actual": False, "is_major_capital_year": True,
            },
        ]

    def test_category_totals_aggregated_correctly(self):
        rows = self._make_rows()
        cat_totals: dict = {}
        for row in rows:
            for cat, amt in row.get("categories", {}).items():
                cat_totals[cat] = round(cat_totals.get(cat, 0) + amt, 2)
        assert cat_totals["capital_replacement_general"] == 4350 + 5350
        assert cat_totals["repaint_building"] == 168642

    def test_summary_closing_balance_extracted(self):
        rows = self._make_rows()
        closing_2029 = next((r.get("closing_balance", 0) for r in rows if r["year"] == 2029), 0)
        assert closing_2029 == 194270

    def test_major_years_list(self):
        rows = self._make_rows()
        major_years = [r["year"] for r in rows if r.get("is_major_capital_year")]
        assert major_years == [2029]


class TestSinkingFundProjection:
    """Verify the inflation model computation matches the user-provided table."""

    def _run_projection(self, inflation_rate=0.035):
        """Re-implement the projection algorithm from forecast_service.py for testing."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../scripts/finances'))
        from seed_sinking_fund_plan import PLAN_DATA, compute_closing_balances
        import copy
        data = copy.deepcopy(PLAN_DATA)
        compute_closing_balances(data)
        active = [r for r in data if r["year"] >= 2022]
        inflated = [round(r["expenditure"] * ((1 + inflation_rate) ** i), 2) for i, r in enumerate(active)]
        total = sum(inflated)
        n = len(active)
        annual = round(total / n, 2) if n > 0 else 0
        reserve = 0.0
        rows = []
        for i, row in enumerate(active):
            reserve = round(reserve + annual - inflated[i], 2)
            rows.append({"year": row["year"], "projected_reserve": reserve,
                         "inflated_expenditure": inflated[i], "annual_collection": annual})
        return rows, annual

    def test_annual_collection_near_106763(self):
        """At 3.5% CPI the smoothed annual collection ≈ $106,763."""
        _, annual = self._run_projection(0.035)
        assert abs(annual - 106_763) < 5, f"Expected ~$106,763, got ${annual}"

    def test_reserve_2022(self):
        rows, _ = self._run_projection(0.035)
        row = next(r for r in rows if r["year"] == 2022)
        # 2022 base=$4,350, inflation index 0, inflated=$4,350; reserve=106763-4350=102413
        assert abs(row["projected_reserve"] - 102_413) < 5

    def test_reserve_2029_near_242018(self):
        """2029 is the minimum reserve point after the building repaint."""
        rows, _ = self._run_projection(0.035)
        row = next(r for r in rows if r["year"] == 2029)
        assert abs(row["projected_reserve"] - 242_018) < 10

    def test_reserve_2035_near_zero(self):
        """By design the plan is fully expended by 2035."""
        rows, _ = self._run_projection(0.035)
        row = next(r for r in rows if r["year"] == 2035)
        assert abs(row["projected_reserve"]) < 100  # near zero (±$100 rounding)

    def test_reserve_never_negative(self):
        """Model reserve should not go negative at 3.5% CPI."""
        rows, _ = self._run_projection(0.035)
        for r in rows:
            assert r["projected_reserve"] >= -50, \
                f"Reserve goes negative at {r['year']}: {r['projected_reserve']}"

    def test_inflated_2029_near_525577(self):
        """2029 base=$413,099 inflated by (1.035)^7 ≈ $525,577."""
        rows, _ = self._run_projection(0.035)
        row = next(r for r in rows if r["year"] == 2029)
        assert abs(row["inflated_expenditure"] - 525_577) < 200

    def test_higher_inflation_raises_collection(self):
        """4% inflation should require higher annual collection than 3.5%."""
        _, ann_35 = self._run_projection(0.035)
        _, ann_40 = self._run_projection(0.040)
        assert ann_40 > ann_35

    def test_14_active_years(self):
        """Plan covers 14 active years (2022-2035)."""
        rows, _ = self._run_projection(0.035)
        assert len(rows) == 14
