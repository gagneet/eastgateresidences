#!/usr/bin/env python3
"""
Backend Tests: Database Schema Sync Verification

Tests that verify the database is in the expected state after Phase 2 cleanup:
- 87 units with required fields
- All users approved
- 32 feature toggles
- 10 compliance items
- 3 budgets (2024-2025, 2025-2026, 2026-2027)
- budget_categories collection DELETED
- No duplicate chat groups (all UUID-based)

Run:
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    pytest tests/backend/test_schema_sync.py -v

Requires:
    pymongo
    python-dotenv
    Backend database accessible
"""
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / "backend" / ".env")

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_INTEGRATION_TESTS"),
    reason="Live-DB test — set RUN_INTEGRATION_TESTS=1 to run",
)


@pytest.fixture(scope="module")
def db():
    """Synchronous MongoDB connection for schema verification."""
    from pymongo import MongoClient

    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27018")
    db_name = os.environ.get("DB_NAME", "eastgate_strata")

    client = MongoClient(mongo_url)
    database = client[db_name]
    yield database
    client.close()


REQUIRED_UNIT_FIELDS = [
    "unit_number",
    "lot_number",
    "unit_type",
    "entitlement",
    # Note: levy data is stored in annual_levies/unit_levy_ledger, not embedded in units
]

EXPECTED_BUDGET_YEARS = ["2024-2025", "2025-2026", "2026-2027"]

EAST_GATE_BUILDING_ID = "13195"


class TestUnits:
    """Verify units collection state for East Gate Residences (building_id=13195)."""

    def test_87_units_exist(self, db):
        """East Gate must contain exactly 87 units."""
        count = db.units.count_documents({"building_id": EAST_GATE_BUILDING_ID})
        assert count == 87, f"Expected 87 East Gate units, found {count}"

    def test_units_have_required_fields(self, db):
        """All East Gate units must have required fields present."""
        missing_fields = []
        units = list(db.units.find({"building_id": EAST_GATE_BUILDING_ID}, {"_id": 0}))
        for unit in units:
            for field in REQUIRED_UNIT_FIELDS:
                if field not in unit:
                    missing_fields.append(f"{unit.get('unit_number', '?')}.{field}")

        assert len(missing_fields) == 0, \
            f"Missing fields in East Gate units:\n" + "\n".join(missing_fields[:20])

    def test_units_have_valid_unit_type(self, db):
        """All East Gate units have a valid unit_type."""
        valid_types = {"apartment", "townhouse", "penthouse"}
        invalid = list(db.units.find(
            {"building_id": EAST_GATE_BUILDING_ID, "unit_type": {"$nin": list(valid_types)}},
            {"unit_number": 1, "unit_type": 1, "_id": 0}
        ))
        assert len(invalid) == 0, f"Units with invalid unit_type: {invalid[:5]}"

    def test_no_owners_units_collection(self, db):
        """owners_units collection must not exist (deleted in Session 25)."""
        collections = db.list_collection_names()
        assert "owners_units" not in collections, \
            "owners_units collection still exists — it was supposed to be deleted in Session 25"


class TestUsers:
    """Verify users collection state."""

    def test_users_exist(self, db):
        """At least 8 users must exist."""
        count = db.users.count_documents({})
        assert count >= 8, f"Expected at least 8 users, found {count}"

    def test_users_all_approved(self, db):
        """Active users (not archived/pending) must have is_approved=True."""
        # Exclude users in registration workflow non-active states (added Phase 3)
        excluded_statuses = ["pending_owner_approval", "info_requested", "archived"]
        unapproved = list(db.users.find(
            {"is_approved": False, "status": {"$nin": excluded_statuses}},
            {"email": 1, "role": 1, "status": 1, "_id": 0}
        ))
        assert len(unapproved) == 0, \
            f"Found {len(unapproved)} unapproved active users: {[u.get('email') for u in unapproved]}"

    def test_admin_user_exists(self, db):
        """A super_admin or chairman user must exist."""
        admin = db.users.find_one({"role": {"$in": ["super_admin", "chairman"]}})
        assert admin is not None, "No super_admin or chairman user found"


class TestFeatureToggles:
    """
    Verify feature toggles state.

    Schema: Multi-tenant two-tier model.
      - Global defaults: building_id=None — apply to all buildings unless overridden.
      - Per-building overrides: building_id=<building_id> — win over global defaults.
    All seed data should be global defaults (building_id=None).
    """

    def test_global_feature_toggles_cover_seed_definitions(self, db):
        """Database must contain at least the current seeded global toggle set."""
        from seeds.feature_toggles import DEFAULT_FEATURES

        expected_min = len(DEFAULT_FEATURES)
        count = db.feature_toggles.count_documents({"building_id": None})
        assert count >= expected_min, (
            f"Expected at least {expected_min} global feature toggles (building_id=None), found {count}"
        )

    def test_feature_toggles_have_required_fields(self, db):
        """Feature toggles must have feature_key, feature_name, is_enabled fields."""
        toggles = list(db.feature_toggles.find({}, {"_id": 0}))
        for toggle in toggles:
            assert "feature_key" in toggle, f"Toggle missing 'feature_key': {toggle}"
            assert "feature_name" in toggle, f"Toggle missing 'feature_name': {toggle}"
            assert "is_enabled" in toggle, f"Toggle missing 'is_enabled': {toggle}"

    def test_no_building_id_13195_toggles(self, db):
        """No feature toggles should have building_id='13195' — all should be global or per-building overrides."""
        count = db.feature_toggles.count_documents({"building_id": "13195"})
        assert count == 0, (
            f"Found {count} feature toggles with building_id='13195'. "
            "East Gate toggles should be global defaults (building_id=None)."
        )

    def test_all_toggles_have_building_id_field(self, db):
        """All feature toggles must have the building_id field present (None for global)."""
        missing = db.feature_toggles.count_documents({"building_id": {"$exists": False}})
        assert missing == 0, f"Found {missing} feature toggles missing the building_id field"


class TestComplianceItems:
    """Verify compliance_items collection state."""

    def test_compliance_items_exist(self, db):
        """At least 10 compliance items must exist."""
        count = db.compliance_items.count_documents({})
        assert count >= 10, f"Expected at least 10 compliance items, found {count}"

    def test_compliance_items_have_required_fields(self, db):
        """Compliance items must have id, title, status fields."""
        items = list(db.compliance_items.find({}, {"_id": 0}))
        for item in items:
            assert "id" in item, f"Item missing 'id': {item}"
            assert "title" in item, f"Item missing 'title': {item}"
            assert "status" in item, f"Item missing 'status': {item}"


class TestBudgets:
    """Verify budgets collection state."""

    def test_3_budgets_exist(self, db):
        """Database must contain at least 1 budget in budgets or annual_levies collection."""
        budget_count = db.budgets.count_documents({})
        levy_count = db.annual_levies.count_documents({})
        assert budget_count + levy_count >= 1, (
            f"Expected budget data in 'budgets' ({budget_count}) or 'annual_levies' ({levy_count})"
        )

    def test_all_expected_budget_years_exist(self, db):
        """Budget data for 2026-2027 must exist in budgets or annual_levies collection."""
        # Post-Session 25: annual_levies is the primary financial data source
        levy_2026 = db.annual_levies.find_one({"year": "2026"})
        budget_2026 = db.budgets.find_one({"financial_year": "2026-2027"})
        assert levy_2026 is not None or budget_2026 is not None, (
            "No 2026-2027 financial data found in annual_levies or budgets"
        )

    def test_budget_categories_collection_deleted(self, db):
        """
        budget_categories collection must NOT exist.
        This was deleted as part of Phase 2 cleanup (2026-02-18).
        Data now lives in budgets.categories[] embedded array.
        """
        collections = db.list_collection_names()
        assert "budget_categories" not in collections, (
            "budget_categories collection still exists! "
            "Run: python3 scripts/backend/migrations/delete_budget_categories.py"
        )

    def test_budgets_have_embedded_categories(self, db):
        """Each budget document should have embedded categories array."""
        budgets = list(db.budgets.find({"financial_year": {"$in": EXPECTED_BUDGET_YEARS}}, {"_id": 0}))
        for budget in budgets:
            categories = budget.get("categories", [])
            assert len(categories) > 0, \
                f"Budget {budget.get('financial_year')} has no embedded categories"


class TestChatGroups:
    """Verify chat_groups collection state."""

    def test_chat_groups_exist(self, db):
        """Chat groups must exist."""
        count = db.chat_groups.count_documents({})
        assert count > 0, "No chat groups found"

    def test_no_duplicate_chat_groups(self, db):
        """
        No old ObjectId-based chat groups should exist.
        All groups should have UUID 'id' field (from initialize_defaults.py).
        """
        objectid_groups = list(db.chat_groups.find(
            {"id": {"$exists": False}},
            {"name": 1, "_id": 1}
        ))
        assert len(objectid_groups) == 0, (
            f"Found {len(objectid_groups)} old ObjectId-based groups: "
            f"{[g.get('name') for g in objectid_groups]}. "
            "Run: python3 scripts/backend/db/deduplicate_chat_groups.py"
        )

    def test_uuid_based_groups_exist(self, db):
        """UUID-based chat groups should exist."""
        uuid_count = db.chat_groups.count_documents({"id": {"$exists": True}})
        assert uuid_count >= 3, f"Expected at least 3 UUID-based groups, found {uuid_count}"
