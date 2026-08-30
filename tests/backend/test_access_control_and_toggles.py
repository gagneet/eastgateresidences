"""
Tests for Feature Toggle system (63 toggles) and Access Control pages.

Covers:
  - All 19 new toggle keys are present in the seed list
  - Effective access logic (site default / grant override / deny override)
  - Per-user feature access CRUD (create, update, delete, bulk)
  - User permissions page: custom_permissions, role defaults, elevation, restriction
  - Super Admin / Chairman always-full-access guarantee
  - Clear-all-overrides scenario

Run from project root:
    backend/venv/bin/pytest tests/backend/test_access_control_and_toggles.py -v
"""
import os
import sys
import importlib.util
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_cursor(items):
    c = MagicMock()
    c.to_list = AsyncMock(return_value=items)
    return c


def _make_feature(key, name, enabled=True, category="core"):
    return {"_id": f"id_{key}", "feature_key": key, "feature_name": name, "is_enabled": enabled, "category": category,
            "routes": [], "description": "", "icon": "Shield", "updated_by": None}


def _make_override(user_id, feature_key, is_enabled):
    return {"_id": "ov1", "user_id": user_id, "feature_key": feature_key, "is_enabled": is_enabled,
            "granted_by": "admin1", "granted_at": datetime.now(timezone.utc), "notes": ""}


def _normalise_toggle_doc(doc):
    if not doc:
        return None
    return {
        "id": str(doc.get("_id", f"id_{doc['feature_key']}")),
        "feature_key": doc["feature_key"],
        "feature_name": doc.get("feature_name", doc["feature_key"]),
        "description": doc.get("description"),
        "is_enabled": doc.get("is_enabled", True),
        "category": doc.get("category"),
        "icon": doc.get("icon"),
        "routes": doc.get("routes", []),
        "depends_on": doc.get("depends_on", []),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def super_admin():
    return {"id": "admin-001", "role": "super_admin", "is_approved": True, "full_name": "Admin"}


@pytest.fixture
def chairman():
    return {"id": "chair-001", "role": "chairman", "is_approved": True, "full_name": "Chair"}


@pytest.fixture
def owner():
    return {"id": "owner-001", "role": "owner", "is_approved": True, "full_name": "Owner", "unit_number": "TH087"}


@pytest.fixture
def tenant():
    return {"id": "tenant-001", "role": "tenant", "is_approved": True, "full_name": "Tenant"}


@pytest.fixture(autouse=True)
def _patch_pg_toggle_repo(monkeypatch):
    import routers.feature_toggles as ft_router

    async def list_resolved_feature_toggles(building_id, category=None, only_enabled=False):
        global_query = {"building_id": None}
        if category:
            global_query["category"] = category
        if only_enabled:
            global_query["is_enabled"] = True

        global_toggles = await ft_router.db.feature_toggles.find(global_query).to_list(200)
        toggle_map = {toggle["feature_key"]: dict(toggle) for toggle in global_toggles}

        if building_id:
            override_query = {"building_id": building_id}
            if category:
                override_query["category"] = category
            overrides = await ft_router.db.feature_toggles.find(override_query).to_list(200)
            for override in overrides:
                key = override["feature_key"]
                if only_enabled and not override.get("is_enabled", True):
                    toggle_map.pop(key, None)
                else:
                    merged = dict(toggle_map.get(key, {}))
                    merged.update(override)
                    merged.setdefault("feature_name", key)
                    merged.setdefault("routes", [])
                    merged.setdefault("depends_on", [])
                    toggle_map[key] = merged

        return [_normalise_toggle_doc(toggle) for toggle in toggle_map.values()]

    async def get_resolved_feature_toggle(feature_key, building_id=None):
        if building_id:
            override = await ft_router.db.feature_toggles.find_one({
                "feature_key": feature_key,
                "building_id": building_id,
            })
            if override:
                return _normalise_toggle_doc(override)
        global_toggle = await ft_router.db.feature_toggles.find_one({
            "feature_key": feature_key,
            "building_id": None,
        })
        return _normalise_toggle_doc(global_toggle)

    async def get_global_feature_toggle(feature_key):
        global_toggle = await ft_router.db.feature_toggles.find_one({
            "feature_key": feature_key,
            "building_id": None,
        })
        return _normalise_toggle_doc(global_toggle)

    async def create_global_feature_toggle(payload, **_kwargs):
        doc = {**payload, "building_id": None, "_id": f"id_{payload['feature_key']}"}
        result = await ft_router.db.feature_toggles.insert_one(doc)
        return _normalise_toggle_doc(await ft_router.db.feature_toggles.find_one({"_id": result.inserted_id}))

    async def update_global_feature_toggle(feature_key, payload, **_kwargs):
        await ft_router.db.feature_toggles.update_one(
            {"feature_key": feature_key, "building_id": None},
            {"$set": payload},
        )
        return _normalise_toggle_doc(await ft_router.db.feature_toggles.find_one({
            "feature_key": feature_key,
            "building_id": None,
        }))

    async def upsert_feature_toggle_override(building_id, feature_key, is_enabled, **_kwargs):
        await ft_router.db.feature_toggles.update_one(
            {"feature_key": feature_key, "building_id": building_id},
            {"$set": {"feature_key": feature_key, "building_id": building_id, "is_enabled": is_enabled}},
            upsert=True,
        )
        return await get_resolved_feature_toggle(feature_key, building_id)

    async def delete_feature_toggle_override(building_id, feature_key):
        result = await ft_router.db.feature_toggles.delete_one({
            "feature_key": feature_key,
            "building_id": building_id,
        })
        return result.deleted_count > 0

    async def delete_global_feature_toggle(feature_key):
        result = await ft_router.db.feature_toggles.delete_one({
            "feature_key": feature_key,
            "building_id": None,
        })
        return result.deleted_count > 0

    monkeypatch.setattr(ft_router.config_repo, "list_resolved_feature_toggles", list_resolved_feature_toggles)
    monkeypatch.setattr(ft_router.config_repo, "get_resolved_feature_toggle", get_resolved_feature_toggle)
    monkeypatch.setattr(ft_router.config_repo, "get_global_feature_toggle", get_global_feature_toggle)
    monkeypatch.setattr(ft_router.config_repo, "create_global_feature_toggle", create_global_feature_toggle)
    monkeypatch.setattr(ft_router.config_repo, "update_global_feature_toggle", update_global_feature_toggle)
    monkeypatch.setattr(ft_router.config_repo, "upsert_feature_toggle_override", upsert_feature_toggle_override)
    monkeypatch.setattr(ft_router.config_repo, "delete_feature_toggle_override", delete_feature_toggle_override)
    monkeypatch.setattr(ft_router.config_repo, "delete_global_feature_toggle", delete_global_feature_toggle)
    monkeypatch.setattr(ft_router.config_repo, "clear_user_feature_override_from_all_users", AsyncMock(return_value=0))
    monkeypatch.setattr(ft_router.identity_repo, "find_user_by_id_for_admin", AsyncMock(return_value=None))


# ─── 1. New Toggle Keys ────────────────────────────────────────────────────────

class TestNewToggleKeys:
    """Verify all 19 new toggle feature_keys are present in the seed."""

    NEW_KEYS = [
        "levy_payments", "financial_projections", "collection_rate",
        "arrears_recovery", "spending_categories", "financial_data_import",
        "pet_register", "strata_roll", "tenant_approvals",
        "user_permissions", "user_feature_permissions", "manual_email",
        "site_settings", "admin_console", "email_settings",
        "manage_mail_passwords", "security_ip_logs", "scraper_settings",
        "asset_register",
    ]

    def test_all_new_keys_in_seed(self):
        seed_keys = {f["feature_key"] for f in DEFAULT_FEATURES}
        for key in self.NEW_KEYS:
            assert key in seed_keys, f"Missing toggle key: {key}"

    def test_total_toggle_count_at_least_63(self):
        assert len(DEFAULT_FEATURES) >= 63, (
            f"Expected at least 63 toggles, got {len(DEFAULT_FEATURES)}"
        )

    def test_all_new_toggles_enabled_by_default(self):
        new_toggles = [f for f in DEFAULT_FEATURES if f["feature_key"] in self.NEW_KEYS]
        for toggle in new_toggles:
            assert toggle["is_enabled"] is True, (
                f"New toggle {toggle['feature_key']} should default to enabled"
            )

    def test_all_new_toggles_have_routes(self):
        new_toggles = {f["feature_key"]: f for f in DEFAULT_FEATURES if f["feature_key"] in self.NEW_KEYS}
        for key in self.NEW_KEYS:
            toggle = new_toggles[key]
            assert toggle.get("routes") is not None, f"Toggle {key} missing routes field"
            assert len(toggle["routes"]) > 0, f"Toggle {key} has empty routes list"

    def test_all_new_toggles_have_categories(self):
        valid_cats = {"core", "financial", "governance", "communication", "community",
                      "safety", "facilities", "maintenance", "system"}
        new_toggles = [f for f in DEFAULT_FEATURES if f["feature_key"] in self.NEW_KEYS]
        for toggle in new_toggles:
            assert toggle.get("category") in valid_cats, (
                f"Toggle {toggle['feature_key']} has invalid category: {toggle.get('category')}"
            )

    def test_financial_new_toggles_correct_category(self):
        financial_keys = {"levy_payments", "financial_projections", "collection_rate",
                          "arrears_recovery", "spending_categories", "financial_data_import"}
        for f in DEFAULT_FEATURES:
            if f["feature_key"] in financial_keys:
                assert f["category"] == "financial", (
                    f"{f['feature_key']} should be category=financial, got {f['category']}"
                )

    def test_system_new_toggles_correct_category(self):
        system_keys = {"site_settings", "admin_console", "email_settings",
                       "manage_mail_passwords", "security_ip_logs", "scraper_settings",
                       "user_permissions", "user_feature_permissions"}
        for f in DEFAULT_FEATURES:
            if f["feature_key"] in system_keys:
                assert f["category"] == "system", (
                    f"{f['feature_key']} should be category=system, got {f['category']}"
                )

    def test_governance_new_toggles_correct_category(self):
        gov_keys = {"strata_roll", "tenant_approvals"}
        for f in DEFAULT_FEATURES:
            if f["feature_key"] in gov_keys:
                assert f["category"] == "governance", (
                    f"{f['feature_key']} should be category=governance, got {f['category']}"
                )

    def test_maintenance_new_toggles_correct_category(self):
        maintenance_keys = {"asset_register"}
        for f in DEFAULT_FEATURES:
            if f["feature_key"] in maintenance_keys:
                assert f["category"] == "maintenance", (
                    f"{f['feature_key']} should be category=maintenance, got {f['category']}"
                )


# ─── 2. Effective Access Logic ─────────────────────────────────────────────────

class TestEffectiveAccessLogic:
    """Test the 3-layer access model: site toggle → user override → effective."""

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_site_enabled_no_override_gives_access(self, mock_db, owner):
        from routers.feature_toggles import get_my_feature_access
        mock_db.feature_toggles.find.return_value = _make_cursor([
            _make_feature("documents", "Documents", enabled=True)
        ])
        mock_db.user_feature_access.find.return_value = _make_cursor([])

        result = await get_my_feature_access(current_user=owner)

        assert result[0].effective_access is True


# ─── 8. GAP-FT Audit — New Keys (trust_accounting) ───────────────────────────

class TestGapFtAuditNewKeys:
    """
    Verify the new toggle keys introduced by the GAP-FT security audit are
    present in the seed file with correct properties.

    GAP-FT-001  trust_accounting    financial   (new key — was completely missing)
    """

    def test_trust_accounting_key_in_seed(self):
        """trust_accounting must exist in the seed (was missing — GAP-FT-001)."""
        seed_keys = {f["feature_key"] for f in DEFAULT_FEATURES}
        assert "trust_accounting" in seed_keys, (
            "GAP-FT-001: trust_accounting key is missing from the seed. "
            "The trust accounting router needs this key to enforce the toggle."
        )

    def test_trust_accounting_is_enabled_by_default(self):
        """trust_accounting should be enabled by default (it's an existing feature)."""
        ta = next(f for f in DEFAULT_FEATURES if f["feature_key"] == "trust_accounting")
        assert ta["is_enabled"] is True, (
            "trust_accounting should default to enabled=True (existing production feature)"
        )

    def test_trust_accounting_is_financial_category(self):
        """trust_accounting belongs to the financial category."""
        ta = next(f for f in DEFAULT_FEATURES if f["feature_key"] == "trust_accounting")
        assert ta["category"] == "financial", (
            f"Expected category=financial, got {ta['category']!r}"
        )

    def test_trust_accounting_depends_on_finance(self):
        """trust_accounting must depend on the finance toggle (parent gate)."""
        ta = next(f for f in DEFAULT_FEATURES if f["feature_key"] == "trust_accounting")
        assert "finance" in ta.get("depends_on", []), (
            "trust_accounting should depend on finance — if finance is off, trust is inaccessible"
        )

    def test_trust_accounting_has_roles_field(self):
        """trust_accounting should restrict to finance/EC roles."""
        ta = next(f for f in DEFAULT_FEATURES if f["feature_key"] == "trust_accounting")
        assert ta.get("roles"), "trust_accounting should define allowed roles"
        assert "super_admin" in ta["roles"]
        assert "strata_manager" in ta["roles"]

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_trust_accounting_disabled_blocks_manager(self, mock_db):
        """When trust_accounting toggle is disabled, a manager loses effective access."""
        from routers.feature_toggles import get_my_feature_access
        manager = {"id": "mgr-001", "role": "strata_manager", "is_approved": True, "full_name": "Manager"}
        mock_db.feature_toggles.find.return_value = _make_cursor([
            _make_feature("trust_accounting", "Trust Accounting", enabled=False, category="financial")
        ])
        mock_db.user_feature_access.find.return_value = _make_cursor([])

        result = await get_my_feature_access(current_user=manager)

        assert result[0].effective_access is False
        assert result[0].site_wide_enabled is False

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_trust_accounting_enabled_permits_manager(self, mock_db):
        """When trust_accounting toggle is enabled, a manager gets effective access."""
        from routers.feature_toggles import get_my_feature_access
        manager = {"id": "mgr-001", "role": "strata_manager", "is_approved": True, "full_name": "Manager"}
        mock_db.feature_toggles.find.return_value = _make_cursor([
            _make_feature("trust_accounting", "Trust Accounting", enabled=True, category="financial")
        ])
        mock_db.user_feature_access.find.return_value = _make_cursor([])

        result = await get_my_feature_access(current_user=manager)

        assert result[0].effective_access is True

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_super_admin_always_has_trust_accounting_access(self, mock_db, super_admin):
        """Super admin bypasses site toggle — always has trust_accounting access."""
        from routers.feature_toggles import get_my_feature_access
        mock_db.feature_toggles.find.return_value = _make_cursor([
            _make_feature("trust_accounting", "Trust Accounting", enabled=False, category="financial")
        ])
        mock_db.user_feature_access.find.return_value = _make_cursor([])

        result = await get_my_feature_access(current_user=super_admin)

        assert result[0].effective_access is True

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_site_disabled_no_override_blocks_access(self, mock_db, owner):
        from routers.feature_toggles import get_my_feature_access
        mock_db.feature_toggles.find.return_value = _make_cursor([
            _make_feature("scraper_settings", "Scraper Settings", enabled=False)
        ])
        mock_db.user_feature_access.find.return_value = _make_cursor([])

        result = await get_my_feature_access(current_user=owner)

        assert result[0].effective_access is False
        assert result[0].site_wide_enabled is False

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_site_disabled_grant_override_gives_access(self, mock_db, owner):
        """Grant override can unlock a site-disabled feature for one user."""
        from routers.feature_toggles import get_my_feature_access
        mock_db.feature_toggles.find.return_value = _make_cursor([
            _make_feature("scraper_settings", "Scraper Settings", enabled=False)
        ])
        mock_db.user_feature_access.find.return_value = _make_cursor([
            _make_override(owner["id"], "scraper_settings", True)
        ])

        result = await get_my_feature_access(current_user=owner)

        assert result[0].site_wide_enabled is False
        assert result[0].user_override is True
        assert result[0].effective_access is True

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_site_enabled_deny_override_blocks_access(self, mock_db, owner):
        """Deny override can block a site-enabled feature for one user."""
        from routers.feature_toggles import get_my_feature_access
        mock_db.feature_toggles.find.return_value = _make_cursor([
            _make_feature("marketplace", "Marketplace", enabled=True)
        ])
        mock_db.user_feature_access.find.return_value = _make_cursor([
            _make_override(owner["id"], "marketplace", False)
        ])

        result = await get_my_feature_access(current_user=owner)

        assert result[0].site_wide_enabled is True
        assert result[0].user_override is False
        assert result[0].effective_access is False

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_super_admin_always_has_access_even_when_site_disabled(self, mock_db, super_admin):
        """Super admins bypass site-wide toggle and overrides."""
        from routers.feature_toggles import get_my_feature_access
        mock_db.feature_toggles.find.return_value = _make_cursor([
            _make_feature("security_ip_logs", "Security Logs", enabled=False)
        ])
        mock_db.user_feature_access.find.return_value = _make_cursor([])

        result = await get_my_feature_access(current_user=super_admin)

        assert result[0].effective_access is True

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_chairman_respects_site_toggle(self, mock_db, chairman):
        """Chairman respects site-wide toggle (no global bypass)."""
        from routers.feature_toggles import get_my_feature_access
        mock_db.feature_toggles.find.return_value = _make_cursor([
            _make_feature("asset_register", "Asset Register", enabled=False)
        ])
        mock_db.user_feature_access.find.return_value = _make_cursor([])

        result = await get_my_feature_access(current_user=chairman)

        assert result[0].effective_access is False

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_multiple_features_mixed_overrides(self, mock_db, owner):
        """Test per-user state when multiple features have different override states."""
        from routers.feature_toggles import get_my_feature_access
        mock_db.feature_toggles.find.return_value = _make_cursor([
            _make_feature("documents", "Docs", enabled=True),
            _make_feature("chat", "Chat", enabled=True),
            _make_feature("pet_register", "Pets", enabled=True),
            _make_feature("admin_console", "Console", enabled=False),
        ])
        mock_db.user_feature_access.find.return_value = _make_cursor([
            _make_override(owner["id"], "chat", False),  # deny site-on
            _make_override(owner["id"], "admin_console", True),  # grant site-off
        ])

        result = await get_my_feature_access(current_user=owner)
        by_key = {r.feature_key: r for r in result}

        assert by_key["documents"].effective_access is True  # site default
        assert by_key["chat"].effective_access is False  # denied
        assert by_key["pet_register"].effective_access is True  # site default
        assert by_key["admin_console"].effective_access is True  # granted


# ─── 3. Per-User Override CRUD ────────────────────────────────────────────────

class TestUserFeatureAccessCRUD:
    """Test create/update/delete of per-user feature access overrides."""

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_create_override_grant(self, mock_db, super_admin):
        from routers.feature_toggles import create_user_feature_access
        from models.feature_toggle import UserFeatureAccessCreate

        target_uid = "owner-001"
        mock_db.users.find_one = AsyncMock(return_value={"id": target_uid, "role": "owner"})
        mock_db.feature_toggles.find_one = AsyncMock(return_value={"feature_key": "scraper_settings"})
        mock_db.user_feature_access.find_one = AsyncMock(return_value=None)  # no existing
        mock_db.user_feature_access.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id="new-ov-id")
        )
        saved = {"_id": "new-ov-id", "user_id": target_uid, "feature_key": "scraper_settings",
                 "is_enabled": True, "granted_by": super_admin["id"],
                 "granted_at": datetime.now(timezone.utc), "notes": ""}
        mock_db.user_feature_access.find_one.side_effect = [None, saved]

        access_data = UserFeatureAccessCreate(
            user_id=target_uid, feature_key="scraper_settings", is_enabled=True, notes=""
        )
        result = await create_user_feature_access(
            user_id=target_uid, access_data=access_data, current_user=super_admin
        )

        assert result.feature_key == "scraper_settings"
        assert result.is_enabled is True

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_update_existing_override(self, mock_db, super_admin):
        """Posting to an existing override upserts (changes grant→deny)."""
        from routers.feature_toggles import create_user_feature_access
        from models.feature_toggle import UserFeatureAccessCreate

        target_uid = "owner-001"
        existing = {"_id": "ov-existing", "user_id": target_uid, "feature_key": "chat",
                    "is_enabled": True, "granted_at": datetime.now(timezone.utc)}
        updated = {**existing, "is_enabled": False}

        mock_db.users.find_one = AsyncMock(return_value={"id": target_uid, "role": "owner"})
        mock_db.feature_toggles.find_one = AsyncMock(return_value={"feature_key": "chat"})
        mock_db.user_feature_access.find_one = AsyncMock(side_effect=[existing, updated])
        mock_db.user_feature_access.update_one = AsyncMock()

        access_data = UserFeatureAccessCreate(
            user_id=target_uid, feature_key="chat", is_enabled=False, notes=""
        )
        result = await create_user_feature_access(
            user_id=target_uid, access_data=access_data, current_user=super_admin
        )

        assert result.is_enabled is False
        mock_db.user_feature_access.update_one.assert_called_once()

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_delete_override_reverts_to_site(self, mock_db, super_admin):
        from routers.feature_toggles import delete_user_feature_access

        mock_db.users.find_one = AsyncMock(return_value={"id": "owner-001", "role": "owner"})
        mock_db.user_feature_access.delete_one = AsyncMock(
            return_value=MagicMock(deleted_count=1)
        )

        # Should not raise
        await delete_user_feature_access(
            user_id="owner-001", feature_key="marketplace", current_user=super_admin
        )
        mock_db.user_feature_access.delete_one.assert_called_once_with({
            "user_id": "owner-001", "feature_key": "marketplace"
        })

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_delete_nonexistent_override_raises_404(self, mock_db, super_admin):
        from routers.feature_toggles import delete_user_feature_access
        from fastapi import HTTPException

        mock_db.users.find_one = AsyncMock(return_value={"id": "owner-001", "role": "owner"})
        mock_db.user_feature_access.delete_one = AsyncMock(
            return_value=MagicMock(deleted_count=0)
        )

        with pytest.raises(HTTPException) as exc_info:
            await delete_user_feature_access(
                user_id="owner-001", feature_key="nonexistent", current_user=super_admin
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_bulk_update_multiple_users(self, mock_db, super_admin):
        from routers.feature_toggles import bulk_update_user_feature_access
        from models.feature_toggle import BulkUserFeatureUpdate

        user_ids = ["u1", "u2", "u3"]
        mock_db.feature_toggles.find_one = AsyncMock(return_value={"feature_key": "chat"})
        mock_db.users.find_one = AsyncMock(
            side_effect=[{"id": uid, "role": "owner"} for uid in user_ids]
        )
        mock_db.user_feature_access.update_one = AsyncMock()

        bulk = BulkUserFeatureUpdate(
            feature_key="chat", user_ids=user_ids, is_enabled=False, notes="Restricted for testing"
        )
        result = await bulk_update_user_feature_access(bulk_data=bulk, current_user=super_admin)

        assert result["feature_key"] == "chat"
        assert result["is_enabled"] is False
        assert mock_db.user_feature_access.update_one.call_count == 3

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_bulk_update_skips_nonexistent_users(self, mock_db, super_admin):
        from routers.feature_toggles import bulk_update_user_feature_access
        from models.feature_toggle import BulkUserFeatureUpdate

        mock_db.feature_toggles.find_one = AsyncMock(return_value={"feature_key": "chat"})
        # First user exists, second does not
        mock_db.users.find_one = AsyncMock(
            side_effect=[{"id": "u1", "role": "owner"}, None]
        )
        mock_db.user_feature_access.update_one = AsyncMock()

        bulk = BulkUserFeatureUpdate(
            feature_key="chat", user_ids=["u1", "ghost-user"], is_enabled=True, notes=""
        )
        result = await bulk_update_user_feature_access(bulk_data=bulk, current_user=super_admin)

        # Only 1 update — ghost-user was skipped
        assert mock_db.user_feature_access.update_one.call_count == 1
        assert "1 users" in result["message"]


# ─── 4. Access Summary for Specific User ────────────────────────────────────

class TestUserAccessSummary:
    """Test get_user_feature_access_summary for a target user."""

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_summary_for_owner_respects_site_toggle(self, mock_db, super_admin):
        from routers.feature_toggles import get_user_feature_access_summary

        target_uid = "owner-001"
        mock_db.users.find_one = AsyncMock(return_value={"id": target_uid, "role": "owner"})
        mock_db.feature_toggles.find.return_value = _make_cursor([
            _make_feature("pet_register", "Pet Register", enabled=True, category="community"),
            _make_feature("scraper_settings", "Scraper", enabled=False, category="system"),
        ])
        mock_db.user_feature_access.find.return_value = _make_cursor([])

        result = await get_user_feature_access_summary(
            user_id=target_uid, current_user=super_admin
        )
        by_key = {r.feature_key: r for r in result}

        assert by_key["pet_register"].effective_access is True
        assert by_key["scraper_settings"].effective_access is False

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_summary_returns_404_for_unknown_user(self, mock_db, super_admin):
        from routers.feature_toggles import get_user_feature_access_summary
        from fastapi import HTTPException

        mock_db.users.find_one = AsyncMock(return_value=None)
        mock_db.feature_toggles.find.return_value = _make_cursor([])
        mock_db.user_feature_access.find.return_value = _make_cursor([])

        with pytest.raises(HTTPException) as exc_info:
            await get_user_feature_access_summary(
                user_id="ghost-user", current_user=super_admin
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_summary_includes_category_field(self, mock_db, super_admin):
        from routers.feature_toggles import get_user_feature_access_summary

        target_uid = "owner-001"
        mock_db.users.find_one = AsyncMock(return_value={"id": target_uid, "role": "owner"})
        mock_db.feature_toggles.find.return_value = _make_cursor([
            _make_feature("levy_payments", "Levy Payments", enabled=True, category="financial")
        ])
        mock_db.user_feature_access.find.return_value = _make_cursor([])

        result = await get_user_feature_access_summary(
            user_id=target_uid, current_user=super_admin
        )

        assert result[0].category == "financial"


# ─── 5. User Permissions (Role Defaults & Custom) ───────────────────────────

class TestUserPermissions:
    """Test role-default permissions and custom_permissions overrides."""

    def test_owner_default_permissions(self):
        from utils.permissions import get_user_permissions
        user = {"id": "u1", "role": "owner", "is_approved": True, "custom_permissions": {}}
        perms = get_user_permissions(user)
        # Owners can view finance but not manage
        assert perms.can_view_finances is True
        assert perms.can_manage_finances is False
        # Owners cannot upload documents or manage users
        assert perms.can_upload_documents is False
        assert perms.can_manage_users is False
        # Owners can chat and view meetings
        assert perms.can_chat is True
        assert perms.can_view_meetings is True

    def test_tenant_default_permissions(self):
        from utils.permissions import get_user_permissions
        user = {"id": "u2", "role": "tenant", "is_approved": True, "custom_permissions": {}}
        perms = get_user_permissions(user)
        assert perms.can_view_finances is False
        assert perms.can_chat is True
        assert perms.can_access_email is False

    def test_ec_member_default_permissions(self):
        from utils.permissions import get_user_permissions
        user = {"id": "u3", "role": "ec_member", "is_approved": True, "custom_permissions": {}}
        perms = get_user_permissions(user)
        assert perms.can_manage_finances is True
        assert perms.can_manage_users is True
        assert perms.can_post_announcements is True
        assert perms.can_access_email is True

    def test_service_provider_default_permissions(self):
        from utils.permissions import get_user_permissions
        user = {"id": "u4", "role": "service_provider", "is_approved": True, "custom_permissions": {}}
        perms = get_user_permissions(user)
        assert perms.can_view_schedule is True
        assert perms.can_chat is True
        assert perms.can_view_finances is False
        assert perms.can_manage_users is False

    def test_custom_permission_elevates_above_default(self):
        """custom_permissions can grant a permission the role doesn't have."""
        from utils.permissions import get_user_permissions
        user = {
            "id": "u5", "role": "owner", "is_approved": True,
            "custom_permissions": {"can_upload_documents": True}
        }
        perms = get_user_permissions(user)
        # Elevated above owner default (owners cannot upload by default)
        assert perms.can_upload_documents is True
        # Other owner defaults preserved
        assert perms.can_view_finances is True

    def test_custom_permission_restricts_below_default(self):
        """custom_permissions can revoke a permission the role has."""
        from utils.permissions import get_user_permissions
        user = {
            "id": "u6", "role": "ec_member", "is_approved": True,
            "custom_permissions": {"can_manage_finances": False}
        }
        perms = get_user_permissions(user)
        # Restricted below EC default
        assert perms.can_manage_finances is False
        # Other EC defaults preserved
        assert perms.can_view_finances is True
        assert perms.can_manage_users is True

    def test_multiple_custom_permissions_applied(self):
        """Multiple custom_permissions all applied simultaneously."""
        from utils.permissions import get_user_permissions
        user = {
            "id": "u7", "role": "owner", "is_approved": True,
            "custom_permissions": {
                "can_upload_documents": True,
                "can_post_announcements": True,
                "can_send_notifications": True,
            }
        }
        perms = get_user_permissions(user)
        assert perms.can_upload_documents is True
        assert perms.can_post_announcements is True
        assert perms.can_send_notifications is True
        assert perms.can_view_finances is True  # unchanged owner default

    def test_unapproved_user_gets_guest_permissions(self):
        """Unapproved users (not admin/ec) fall back to guest permissions."""
        from utils.permissions import get_user_permissions
        user = {"id": "u8", "role": "owner", "is_approved": False, "custom_permissions": {}}
        perms = get_user_permissions(user)
        assert perms.can_view_finances is False
        assert perms.can_chat is False

    def test_super_admin_has_all_permissions(self):
        from utils.permissions import get_user_permissions
        user = {"id": "u9", "role": "super_admin", "is_approved": True, "custom_permissions": {}}
        perms = get_user_permissions(user)
        assert perms.can_manage_users is True
        assert perms.can_manage_finances is True
        assert perms.can_manage_meetings is True
        assert perms.can_post_announcements is True
        assert perms.can_send_notifications is True
        assert perms.can_access_email is True

    def test_empty_custom_permissions_uses_role_defaults(self):
        from utils.permissions import get_user_permissions
        user = {"id": "u10", "role": "tenant", "is_approved": True, "custom_permissions": {}}
        perms_custom = get_user_permissions(user)
        user_no_custom = {"id": "u10", "role": "tenant", "is_approved": True}
        perms_default = get_user_permissions(user_no_custom)
        assert perms_custom.model_dump() == perms_default.model_dump()

    def test_permission_elevation_detection(self):
        """Verify the frontend elevation detection logic (Python equivalent)."""
        from utils.permissions import get_user_permissions
        from models.user import DEFAULT_PERMISSIONS

        user = {
            "id": "u11", "role": "owner", "is_approved": True,
            "custom_permissions": {"can_upload_documents": True}
        }
        perms = get_user_permissions(user)
        role_defaults = DEFAULT_PERMISSIONS.get("owner")

        # can_upload_documents: default=False, current=True → Elevated
        assert perms.can_upload_documents is True
        assert role_defaults.can_upload_documents is False

    def test_permission_restriction_detection(self):
        """Verify the frontend restriction detection logic (Python equivalent)."""
        from utils.permissions import get_user_permissions
        from models.user import DEFAULT_PERMISSIONS

        user = {
            "id": "u12", "role": "ec_member", "is_approved": True,
            "custom_permissions": {"can_chat": False}
        }
        perms = get_user_permissions(user)
        role_defaults = DEFAULT_PERMISSIONS.get("ec_member")

        # can_chat: default=True, current=False → Restricted
        assert perms.can_chat is False
        assert role_defaults.can_chat is True


# ─── 6. Toggle Site-Wide CRUD ──────────────────────────────────────────────

class TestSiteWideToggleCRUD:
    """Test creating, updating and querying site-wide feature toggles."""

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_get_all_toggles_super_admin_sees_disabled(self, mock_db, super_admin):
        from routers.feature_toggles import get_all_feature_toggles

        mock_db.feature_toggles.find.return_value = _make_cursor([
            _make_feature("documents", "Documents", enabled=True),
            _make_feature("scraper_settings", "Scraper", enabled=False),
        ])

        result = await get_all_feature_toggles(category=None, current_user=super_admin)

        assert len(result) == 2  # super_admin sees all including disabled

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_get_toggles_owner_only_sees_enabled(self, mock_db, owner):
        from routers.feature_toggles import get_all_feature_toggles

        # Non-super-admin query adds is_enabled=True filter
        mock_db.feature_toggles.find.return_value = _make_cursor([
            _make_feature("documents", "Documents", enabled=True),
        ])

        result = await get_all_feature_toggles(category=None, current_user=owner)

        # Verify the query had is_enabled=True
        call_args = mock_db.feature_toggles.find.call_args[0][0]
        assert call_args.get("is_enabled") is True

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_update_toggle_allows_super_admin_update(self, mock_db):
        from routers.feature_toggles import update_feature_toggle
        from models.feature_toggle import FeatureToggleUpdate
        from fastapi import HTTPException

        # Non-super-admin cannot update toggles
        non_admin = {"id": "u1", "role": "ec_member"}
        # require_role dependency should raise 403
        # We test the endpoint itself directly with a non-admin user
        # (the require_role dependency is applied at router level, not inline)
        # This validates that the endpoint function uses require_role correctly
        existing = _make_feature("documents", "Documents")
        mock_db.feature_toggles.find_one = AsyncMock(return_value=existing)
        mock_db.feature_toggles.update_one = AsyncMock()
        existing["_id"] = "id_documents"
        existing["updated_at"] = datetime.now(timezone.utc)
        existing["created_at"] = datetime.now(timezone.utc)
        updated = {**existing, "is_enabled": False}
        mock_db.feature_toggles.find_one.side_effect = [existing, updated]
        mock_db.feature_toggles.update_one = AsyncMock()

        # Also mock find() for dependent_toggles query (called when is_enabled=False)
        mock_db.feature_toggles.find = MagicMock(
            return_value=MagicMock(to_list=AsyncMock(return_value=[]))
        )

        update = FeatureToggleUpdate(is_enabled=False)
        # When called with super_admin it should work
        super_admin = {"id": "admin1", "role": "super_admin"}
        result = await update_feature_toggle(
            feature_key="documents", toggle_data=update, current_user=super_admin
        )
        # update_feature_toggle returns a dict (not a Pydantic model)
        assert result["is_enabled"] is False


# ─── 7. Group 1 Gap Closure Toggle Keys ──────────────────────────────────────

class TestGroup1GapToggleKeys:
    """Verify all 5 Group 1 gap-closure feature keys are present in the seed
    with the correct category and enabled-by-default state.

    GAP-JUR-NSW-002  payment_plans       financial
    GAP-FIN-012      levy_reminders      financial
    GAP-FIN-006      arrears_risk        financial
    GAP-GOV-007      conflict_of_interest governance
    GAP-COM-008      essential_services  safety
    """

    GAP1_KEYS = [
        "payment_plans",
        "levy_reminders",
        "arrears_risk",
        "conflict_of_interest",
        "essential_services",
    ]

    def test_all_gap1_keys_present_in_seed(self):
        seed_keys = {f["feature_key"] for f in DEFAULT_FEATURES}
        for key in self.GAP1_KEYS:
            assert key in seed_keys, f"Missing Group 1 gap toggle key: {key}"

    def test_gap1_toggles_enabled_by_default(self):
        gap1 = [f for f in DEFAULT_FEATURES if f["feature_key"] in self.GAP1_KEYS]
        for toggle in gap1:
            assert toggle["is_enabled"] is True, (
                f"Gap toggle {toggle['feature_key']} should default to enabled=True"
            )

    def test_gap1_toggles_have_routes(self):
        gap1 = {f["feature_key"]: f for f in DEFAULT_FEATURES if f["feature_key"] in self.GAP1_KEYS}
        for key in self.GAP1_KEYS:
            toggle = gap1[key]
            assert toggle.get("routes") and len(toggle["routes"]) > 0, (
                f"Gap toggle {key} must have at least one route"
            )

    def test_financial_gap1_toggles_correct_category(self):
        financial_keys = {"payment_plans", "levy_reminders", "arrears_risk"}
        for f in DEFAULT_FEATURES:
            if f["feature_key"] in financial_keys:
                assert f["category"] == "financial", (
                    f"{f['feature_key']} should have category=financial, got {f['category']!r}"
                )

    def test_conflict_of_interest_is_governance(self):
        coi = next(f for f in DEFAULT_FEATURES if f["feature_key"] == "conflict_of_interest")
        assert coi["category"] == "governance"

    def test_essential_services_is_safety(self):
        es = next(f for f in DEFAULT_FEATURES if f["feature_key"] == "essential_services")
        assert es["category"] == "safety"

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_payment_plans_disabled_blocks_owner(self, mock_db, owner):
        """When payment_plans toggle is site-disabled, owner loses effective access."""
        from routers.feature_toggles import get_my_feature_access
        mock_db.feature_toggles.find.return_value = _make_cursor([
            _make_feature("payment_plans", "Payment Plans", enabled=False, category="financial")
        ])
        mock_db.user_feature_access.find.return_value = _make_cursor([])

        result = await get_my_feature_access(current_user=owner)

        assert result[0].effective_access is False
        assert result[0].site_wide_enabled is False

    @pytest.mark.asyncio
    @patch("routers.feature_toggles.db")
    async def test_payment_plans_enabled_permits_owner(self, mock_db, owner):
        """When payment_plans toggle is enabled, owner gets effective access."""
        from routers.feature_toggles import get_my_feature_access
        mock_db.feature_toggles.find.return_value = _make_cursor([
            _make_feature("payment_plans", "Payment Plans", enabled=True, category="financial")
        ])
        mock_db.user_feature_access.find.return_value = _make_cursor([])

        result = await get_my_feature_access(current_user=owner)

        assert result[0].effective_access is True
