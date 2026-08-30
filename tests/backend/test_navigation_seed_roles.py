import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))


CANONICAL_NAV_ROLES = {
    "super_admin",
    "strata_admin",
    "strata_manager",
    "ec_member",
    "admin_staff",
    "owner",
    "tenant",
    "real_estate_agent",
    "service_provider",
    "guest",
}

LEGACY_NAV_ROLES = {"chairman", "reception"}


def test_navigation_seed_configs_use_canonical_roles_only():
    from seeds.navigation_configs import NAV_CONFIGS

    roles = set(NAV_CONFIGS)
    strata_admin_ids = {
        item["id"]
        for item in (
            NAV_CONFIGS["strata_admin"]["simple_items"]
            + NAV_CONFIGS["strata_admin"]["advanced_items"]
        )
    }

    assert CANONICAL_NAV_ROLES <= roles
    assert roles.isdisjoint(LEGACY_NAV_ROLES)
    assert {"portfolio", "users", "settings"} <= strata_admin_ids
    assert "owner-view" not in strata_admin_ids


def test_snapshot_navigation_seed_configs_use_canonical_roles_only():
    from seeds.snapshot_navigation_configs import NAVIGATION_CONFIGS

    roles = {entry["role"] for entry in NAVIGATION_CONFIGS}
    ids = {entry["id"] for entry in NAVIGATION_CONFIGS}
    strata_admin = next(entry for entry in NAVIGATION_CONFIGS if entry["role"] == "strata_admin")
    strata_admin_ids = {
        item["id"]
        for item in strata_admin["simple_items"] + strata_admin["advanced_items"]
    }

    assert CANONICAL_NAV_ROLES <= roles
    assert roles.isdisjoint(LEGACY_NAV_ROLES)
    assert "nav-strata_admin" in ids
    assert "nav-admin_staff" in ids
    assert "nav-chairman" not in ids
    assert "nav-reception" not in ids
    assert {"portfolio", "users", "settings"} <= strata_admin_ids
    assert "owner-view" not in strata_admin_ids


def test_navigation_seed_configs_do_not_duplicate_menu_ids_per_role():
    from seeds.navigation_configs import NAV_CONFIGS

    for role, config in NAV_CONFIGS.items():
        ids = [
            item["id"]
            for item in config["simple_items"] + config["advanced_items"]
        ]
        assert len(ids) == len(set(ids)), f"duplicate navigation item IDs for role={role}"


def test_navigation_seed_configs_expose_onboarding_to_platform_roles():
    from seeds.navigation_configs import NAV_CONFIGS

    expected_items = {
        "building-onboarding": (
            "/admin/portfolio/onboard",
            "building_onboarding",
            "",
        ),
        "quick-building-setup": (
            "/admin/buildings/onboard",
            "building_onboarding",
            "",
        ),
        "financial-onboarding": (
            "/admin/financial-onboarding",
            "historical_financial_reconstruction",
            "can_manage_finances",
        ),
    }
    for role in ("super_admin", "strata_admin", "strata_manager"):
        items = (
            NAV_CONFIGS[role]["simple_items"]
            + NAV_CONFIGS[role]["advanced_items"]
        )
        by_id = {item["id"]: item for item in items}
        for item_id, (route, feature_flag, permission_flag) in expected_items.items():
            onboarding_item = by_id.get(item_id)
            assert onboarding_item is not None, f"missing {item_id} nav item for role={role}"
            assert onboarding_item["route"] == route
            assert onboarding_item["feature_flag"] == feature_flag
            assert onboarding_item["permission_flag"] == permission_flag


def test_snapshot_navigation_seed_configs_do_not_duplicate_menu_ids_per_role():
    from seeds.snapshot_navigation_configs import NAVIGATION_CONFIGS

    for entry in NAVIGATION_CONFIGS:
        ids = [
            item["id"]
            for item in entry["simple_items"] + entry["advanced_items"]
        ]
        assert len(ids) == len(set(ids)), f"duplicate navigation item IDs for role={entry['role']}"


def test_snapshot_navigation_seed_configs_expose_onboarding_to_platform_roles():
    from seeds.snapshot_navigation_configs import NAVIGATION_CONFIGS

    by_role = {entry["role"]: entry for entry in NAVIGATION_CONFIGS}
    expected_items = {
        "building-onboarding": (
            "/admin/portfolio/onboard",
            "building_onboarding",
            "",
        ),
        "quick-building-setup": (
            "/admin/buildings/onboard",
            "building_onboarding",
            "",
        ),
        "financial-onboarding": (
            "/admin/financial-onboarding",
            "historical_financial_reconstruction",
            "can_manage_finances",
        ),
    }
    for role in ("super_admin", "strata_admin", "strata_manager"):
        entry = by_role[role]
        items = entry["simple_items"] + entry["advanced_items"]
        by_id = {item["id"]: item for item in items}
        for item_id, (route, feature_flag, permission_flag) in expected_items.items():
            onboarding_item = by_id.get(item_id)
            assert onboarding_item is not None, f"missing snapshot {item_id} nav item for role={role}"
            assert onboarding_item["route"] == route
            assert onboarding_item["feature_flag"] == feature_flag
            assert onboarding_item["permission_flag"] == permission_flag
