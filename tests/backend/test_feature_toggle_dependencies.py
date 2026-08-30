"""
Tests for Feature Toggle Dependency Intelligence

Covers:
- depends_on field present in model, create, update, response
- Seed data: all toggles have depends_on field
- _toggle_to_response includes depends_on
- PUT endpoint returns affected_dependents when disabling
- Dependency map correctness (well-known parent-child pairs)
- MongoDB array query for depends_on lookups
"""

import os
import sys
import importlib.util
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)


def _load_default_features():
    spec = importlib.util.spec_from_file_location(
        "test_feature_toggles_seed",
        os.path.join(_backend_dir, "seeds", "feature_toggles.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DEFAULT_FEATURES


DEFAULT_FEATURES = _load_default_features()

from models.feature_toggle import (
    FeatureToggle, FeatureToggleCreate, FeatureToggleUpdate, FeatureToggleResponse
)
from routers.feature_toggles import (
    _toggle_to_response, update_feature_toggle, get_all_feature_toggles
)


# ─── Helper factories ────────────────────────────────────────────────────────

def make_toggle(feature_key="finance", is_enabled=True, depends_on=None):
    return {
        "_id": ObjectId(),
        "feature_key": feature_key,
        "feature_name": feature_key.replace("_", " ").title(),
        "description": f"Test {feature_key}",
        "is_enabled": is_enabled,
        "category": "financial",
        "icon": "DollarSign",
        "routes": [],
        "depends_on": depends_on or [],
        "building_id": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }


# ─── Model Tests ─────────────────────────────────────────────────────────────

class TestFeatureToggleModels:
    def test_feature_toggle_has_depends_on(self):
        t = FeatureToggle(feature_key="f", feature_name="F")
        assert hasattr(t, "depends_on")
        assert t.depends_on == []

    def test_feature_toggle_create_has_depends_on(self):
        c = FeatureToggleCreate(feature_key="f", feature_name="F", depends_on=["finance"])
        assert c.depends_on == ["finance"]

    def test_feature_toggle_update_has_optional_depends_on(self):
        u = FeatureToggleUpdate(is_enabled=False)
        assert u.depends_on is None  # Not set

        u2 = FeatureToggleUpdate(is_enabled=False, depends_on=["levy_fairness"])
        assert u2.depends_on == ["levy_fairness"]

    def test_feature_toggle_response_has_depends_on(self):
        r = FeatureToggleResponse(
            id=str(ObjectId()),
            feature_key="f",
            feature_name="F",
            is_enabled=True,
            depends_on=["finance"],
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        assert r.depends_on == ["finance"]

    def test_depends_on_defaults_to_empty_list(self):
        """All models default depends_on to [] — not None."""
        t = FeatureToggle(feature_key="x", feature_name="X")
        c = FeatureToggleCreate(feature_key="x", feature_name="X")
        assert t.depends_on == []
        assert c.depends_on == []


# ─── Router Helper Tests ─────────────────────────────────────────────────────

class TestToggleToResponse:
    def test_includes_depends_on_empty(self):
        toggle = make_toggle("finance", depends_on=[])
        resp = _toggle_to_response(toggle)
        assert resp.depends_on == []

    def test_includes_depends_on_list(self):
        toggle = make_toggle("levy_fairness_owner", depends_on=["levy_fairness"])
        resp = _toggle_to_response(toggle)
        assert resp.depends_on == ["levy_fairness"]

    def test_missing_depends_on_defaults_to_empty(self):
        """Backward-compat: DB docs without depends_on field should not crash."""
        toggle = make_toggle("old_toggle")
        del toggle["depends_on"]  # Simulate old doc without the field
        resp = _toggle_to_response(toggle)
        assert resp.depends_on == []

    def test_multiple_parents(self):
        toggle = make_toggle("rental_certificates", depends_on=["compliance", "owner_units_management"])
        resp = _toggle_to_response(toggle)
        assert "compliance" in resp.depends_on
        assert "owner_units_management" in resp.depends_on
        assert len(resp.depends_on) == 2


# ─── PUT Endpoint — affected_dependents ──────────────────────────────────────

class TestUpdateFeatureToggleAffectedDependents:
    @pytest.mark.asyncio
    async def test_disabling_parent_returns_affected_dependents(self):
        """When disabling finance, levy_fairness_owner (enabled, depends_on finance) should appear."""
        parent = make_toggle("finance", is_enabled=True)
        child = make_toggle("levy_fairness_owner", is_enabled=True, depends_on=["finance"])
        updated = {**parent, "is_enabled": False}

        with patch("routers.feature_toggles.config_repo.get_global_feature_toggle", AsyncMock(return_value=parent)), \
             patch("routers.feature_toggles.config_repo.update_global_feature_toggle", AsyncMock(return_value=updated)), \
             patch("routers.feature_toggles.config_repo.list_resolved_feature_toggles", AsyncMock(return_value=[child])), \
             patch("routers.feature_toggles.refresh_rate_limit_config", AsyncMock()):
            admin = {"id": "admin1", "role": "super_admin"}
            result = await update_feature_toggle(
                feature_key="finance",
                toggle_data=FeatureToggleUpdate(is_enabled=False),
                building_id=None,
                current_user=admin,
            )

        assert "affected_dependents" in result
        keys = [d["feature_key"] for d in result["affected_dependents"]]
        assert "levy_fairness_owner" in keys

    @pytest.mark.asyncio
    async def test_enabling_has_no_affected_dependents(self):
        """When enabling a feature, affected_dependents is always []."""
        parent = make_toggle("finance", is_enabled=False)
        updated = {**parent, "is_enabled": True}

        with patch("routers.feature_toggles.config_repo.get_global_feature_toggle", AsyncMock(return_value=parent)), \
             patch("routers.feature_toggles.config_repo.update_global_feature_toggle", AsyncMock(return_value=updated)), \
             patch("routers.feature_toggles.refresh_rate_limit_config", AsyncMock()):
            admin = {"id": "admin1", "role": "super_admin"}
            result = await update_feature_toggle(
                feature_key="finance",
                toggle_data=FeatureToggleUpdate(is_enabled=True),
                building_id=None,
                current_user=admin,
            )

        assert result["affected_dependents"] == []

    @pytest.mark.asyncio
    async def test_disabling_leaf_returns_no_dependents(self):
        """Disabling levy_fairness_owner (no children) returns empty affected_dependents."""
        leaf = make_toggle("levy_fairness_owner", is_enabled=True, depends_on=["levy_fairness"])
        updated = {**leaf, "is_enabled": False}

        with patch("routers.feature_toggles.config_repo.get_global_feature_toggle", AsyncMock(return_value=leaf)), \
             patch("routers.feature_toggles.config_repo.update_global_feature_toggle", AsyncMock(return_value=updated)), \
             patch("routers.feature_toggles.config_repo.list_resolved_feature_toggles", AsyncMock(return_value=[])), \
             patch("routers.feature_toggles.refresh_rate_limit_config", AsyncMock()):
            admin = {"id": "admin1", "role": "super_admin"}
            result = await update_feature_toggle(
                feature_key="levy_fairness_owner",
                toggle_data=FeatureToggleUpdate(is_enabled=False),
                building_id=None,
                current_user=admin,
            )
        assert result["affected_dependents"] == []


# ─── Seed Data Correctness ────────────────────────────────────────────────────

class TestSeedDependencies:
    """Validate well-known dependency relationships in the seed data."""

    def _get_seed_map(self):
        return {f["feature_key"]: f for f in DEFAULT_FEATURES}

    def test_all_toggles_have_depends_on_field(self):
        missing = [f["feature_key"] for f in DEFAULT_FEATURES if "depends_on" not in f]
        assert missing == [], f"Missing depends_on: {missing}"

    def test_finance_is_root(self):
        m = self._get_seed_map()
        assert m["finance"]["depends_on"] == []

    def test_trust_accounting_depends_on_finance(self):
        """GAP-FT-001: trust_accounting was added; must depend on finance."""
        m = self._get_seed_map()
        assert "trust_accounting" in m, "trust_accounting seed key is missing"
        assert "finance" in m["trust_accounting"]["depends_on"], (
            "trust_accounting should depend on finance"
        )

    def test_levy_fairness_depends_on_finance(self):
        m = self._get_seed_map()
        assert "finance" in m["levy_fairness"]["depends_on"]

    def test_levy_fairness_sub_features_depend_on_levy_fairness(self):
        m = self._get_seed_map()
        sub_keys = [
            "levy_fairness_owner", "levy_fairness_explain", "levy_fairness_confidence",
            "levy_fairness_distribution", "levy_fairness_snapshots",
            "levy_fairness_audit", "levy_fairness_cross_subsidy", "subsidy_map",
        ]
        for key in sub_keys:
            assert key in m, f"Missing toggle: {key}"
            assert "levy_fairness" in m[key]["depends_on"], f"{key} should depend on levy_fairness"

    def test_maintenance_is_root(self):
        m = self._get_seed_map()
        assert m["maintenance"]["depends_on"] == []

    def test_work_orders_depends_on_maintenance(self):
        m = self._get_seed_map()
        assert "maintenance" in m["work_orders"]["depends_on"]

    def test_defects_depends_on_maintenance(self):
        m = self._get_seed_map()
        assert "maintenance" in m["defects"]["depends_on"]

    def test_asset_register_depends_on_maintenance(self):
        m = self._get_seed_map()
        assert "maintenance" in m["asset_register"]["depends_on"]

    def test_maintenance_intelligence_depends_on_asset_register(self):
        m = self._get_seed_map()
        assert "asset_register" in m["maintenance_intelligence"]["depends_on"]

    def test_digital_twin_depends_on_asset_register(self):
        m = self._get_seed_map()
        assert "asset_register" in m["digital_twin"]["depends_on"]

    def test_email_cluster(self):
        m = self._get_seed_map()
        for key in ["webmail", "email_preferences", "manual_email", "manage_mail_passwords"]:
            assert "email" in m[key]["depends_on"], f"{key} should depend on email"
        assert m["webmail"]["routes"] == ["/my-communications"]
        assert m["email_preferences"]["routes"] == ["/my-communications"]
        assert m["manual_email"]["routes"] == ["/admin/communications"]
        assert m["email_settings"]["routes"] == ["/admin/communications"]
        assert m["manage_mail_passwords"]["routes"] == ["/admin/communications"]

    def test_chat_cluster(self):
        m = self._get_seed_map()
        assert "chat" in m["private_messages"]["depends_on"]

    def test_notices_cluster_is_canonical(self):
        m = self._get_seed_map()
        assert m["notices"]["routes"] == ["/community/notices"]
        assert "announcements" not in m["notices"]["depends_on"]
        assert m["announcements"]["routes"] == []

    def test_user_management_cluster(self):
        m = self._get_seed_map()
        for key in ["owner_name_verification", "tenant_approvals", "change_requests",
                    "owner_transfers", "tenant_renewals", "expired_accounts",
                    "user_permissions", "user_feature_permissions"]:
            assert "user_management" in m[key]["depends_on"], f"{key} should depend on user_management"

    def test_owner_transfers_toggle_matches_review_surface(self):
        m = self._get_seed_map()
        owner_transfers = m["owner_transfers"]

        assert owner_transfers["is_enabled"] is True
        assert owner_transfers["category"] == "governance"
        assert "/admin/owner-transfers" in owner_transfers["routes"]
        assert "user_management" in owner_transfers["depends_on"]

    def test_proposals_depends_on_meetings(self):
        m = self._get_seed_map()
        assert "meetings" in m["proposals"]["depends_on"]

    def test_rental_certificates_multi_parent(self):
        m = self._get_seed_map()
        assert "compliance" in m["rental_certificates"]["depends_on"]
        assert "owner_units_management" in m["rental_certificates"]["depends_on"]

    def test_strata_roll_depends_on_owner_units_management(self):
        m = self._get_seed_map()
        assert "owner_units_management" in m["strata_roll"]["depends_on"]

    def test_owner_hub_cluster(self):
        m = self._get_seed_map()
        for key in ["property_health_score", "real_estate_agent_portal"]:
            assert "owner_hub" in m[key]["depends_on"], f"{key} should depend on owner_hub"

    def test_finance_intelligence_cluster(self):
        m = self._get_seed_map()
        assert "finance" in m["finance_intelligence"]["depends_on"]
        for key in ["building_financial_stress", "stress_score", "investor_dashboard",
                    "insurance_lending_hooks"]:
            assert "finance_intelligence" in m[key]["depends_on"], \
                f"{key} should depend on finance_intelligence"

    def test_building_risk_index_depends_on_market_intelligence(self):
        m = self._get_seed_map()
        assert "strata_market_intelligence" in m["building_risk_index"]["depends_on"]

    def test_all_depends_on_keys_exist_in_seed(self):
        """Every key referenced in depends_on must itself exist in the seed."""
        all_keys = {f["feature_key"] for f in DEFAULT_FEATURES}
        for feature in DEFAULT_FEATURES:
            for dep_key in feature.get("depends_on", []):
                assert dep_key in all_keys, \
                    f"Toggle '{feature['feature_key']}' references unknown parent '{dep_key}'"

    def test_no_self_references(self):
        """A toggle must not list itself in depends_on."""
        for f in DEFAULT_FEATURES:
            assert f["feature_key"] not in f.get("depends_on", []), \
                f"Toggle '{f['feature_key']}' references itself in depends_on"

    def test_no_circular_dependencies_two_levels(self):
        """Simple cycle check: parent cannot depend on child."""
        m = self._get_seed_map()
        for feature in m.values():
            for parent_key in feature.get("depends_on", []):
                if parent_key in m:
                    assert feature["feature_key"] not in m[parent_key].get("depends_on", []), \
                        f"Circular dependency: {feature['feature_key']} ↔ {parent_key}"

    def test_all_root_features_have_empty_depends_on(self):
        """Known root features should have no parents."""
        m = self._get_seed_map()
        roots = ["finance", "maintenance", "email", "chat", "announcements",
                 "news", "user_management", "owner_units_management",
                 "meetings", "compliance", "owner_hub", "strata_market_intelligence",
                 "documents", "marketplace", "events", "bookings", "emergency"]
        for key in roots:
            if key in m:
                assert m[key]["depends_on"] == [], f"Root toggle '{key}' should have empty depends_on"


# ─── GET endpoint — depends_on in response ────────────────────────────────────

class TestGetTogglesReturnsDependsOn:
    @pytest.mark.asyncio
    async def test_list_endpoint_includes_depends_on(self):
        toggle = make_toggle("levy_fairness", depends_on=["finance"])

        with patch("routers.feature_toggles.config_repo.list_resolved_feature_toggles", AsyncMock(return_value=[toggle])):
            super_admin = {"id": "a1", "role": "super_admin"}
            results = await get_all_feature_toggles(
                category=None, building_id=None, current_user=super_admin
            )
        assert len(results) == 1
        assert results[0].depends_on == ["finance"]

    @pytest.mark.asyncio
    async def test_old_doc_without_depends_on_returns_empty_list(self):
        toggle = make_toggle("finance")
        del toggle["depends_on"]  # Old doc without field

        with patch("routers.feature_toggles.config_repo.list_resolved_feature_toggles", AsyncMock(return_value=[toggle])):
            super_admin = {"id": "a1", "role": "super_admin"}
            results = await get_all_feature_toggles(
                category=None, building_id=None, current_user=super_admin
            )
        assert results[0].depends_on == []
