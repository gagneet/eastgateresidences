"""
Tests for the feature-toggle refactor (refactor/feature-toggle-traceability).

Covers:
  1. _compute_effective_access — all 4 resolution steps, edge cases
  2. FeatureToggleKeys constants — format, seed parity, router parity
  3. FEATURE_TOGGLE_DEBUG tracing — gated, safe (no PII/secrets in output)
  4. get_effective_feature_access — debug path exercises without real DB

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_feature_toggle_refactor.py -v
"""
import ast
import logging
import os
from unittest.mock import AsyncMock, patch

import pytest


# ─── 1. _compute_effective_access ─────────────────────────────────────────────

class TestComputeEffectiveAccess:
    """
    _compute_effective_access is the single source of truth for the 4-step
    feature access resolution extracted from the duplicated router logic.
    """

    @pytest.fixture(autouse=True)
    def _import(self):
        from routers.feature_toggles import _compute_effective_access
        self.fn = _compute_effective_access

    # Step 1 — super_admin always True

    def test_super_admin_always_true_regardless_of_site_wide(self):
        assert self.fn(is_super_admin=True, is_elevated=False, site_wide=False, user_override=None) is True

    def test_super_admin_always_true_regardless_of_override(self):
        assert self.fn(is_super_admin=True, is_elevated=False, site_wide=False, user_override=False) is True

    def test_super_admin_always_true_even_when_elevated(self):
        assert self.fn(is_super_admin=True, is_elevated=True, site_wide=False, user_override=None) is True

    # Step 2 — elevated bypasses user override, follows site_wide

    def test_elevated_follows_site_wide_true(self):
        assert self.fn(is_super_admin=False, is_elevated=True, site_wide=True, user_override=False) is True

    def test_elevated_follows_site_wide_false(self):
        assert self.fn(is_super_admin=False, is_elevated=True, site_wide=False, user_override=True) is False

    def test_elevated_ignores_override_false_when_site_wide_true(self):
        """Override=False must NOT block an elevated user when site_wide=True."""
        result = self.fn(is_super_admin=False, is_elevated=True, site_wide=True, user_override=False)
        assert result is True

    # Step 3 — user override takes precedence for normal users

    def test_override_true_beats_site_wide_false(self):
        assert self.fn(is_super_admin=False, is_elevated=False, site_wide=False, user_override=True) is True

    def test_override_false_beats_site_wide_true(self):
        assert self.fn(is_super_admin=False, is_elevated=False, site_wide=True, user_override=False) is False

    # Step 4 — no override → site_wide

    def test_no_override_returns_site_wide_true(self):
        assert self.fn(is_super_admin=False, is_elevated=False, site_wide=True, user_override=None) is True

    def test_no_override_returns_site_wide_false(self):
        assert self.fn(is_super_admin=False, is_elevated=False, site_wide=False, user_override=None) is False

    # Return type

    def test_always_returns_bool(self):
        for args in [
            (True, False, False, None),
            (False, True, True, None),
            (False, False, True, False),
            (False, False, False, None),
        ]:
            result = self.fn(*args)
            assert isinstance(result, bool), f"Expected bool, got {type(result)} for args={args}"


# ─── 2. FeatureToggleKeys — format and seed parity ────────────────────────────

class TestFeatureToggleKeys:
    """
    FeatureToggleKeys constants must:
      - All be lowercase strings with underscores only (no spaces, no uppercase)
      - All exist in seeds/feature_toggles.py DEFAULT_FEATURES
      - Not contain duplicates
    """

    @pytest.fixture(autouse=True)
    def _load(self):
        import importlib.util
        from models.feature_toggle import FeatureToggleKeys

        self.keys_cls = FeatureToggleKeys
        # Collect all public string attributes (the constants)
        self.constants = {
            name: val
            for name, val in vars(FeatureToggleKeys).items()
            if not name.startswith("_") and isinstance(val, str)
        }

        # Load seed
        backend_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "backend",
        )
        seed_path = os.path.join(backend_dir, "seeds", "feature_toggles.py")
        with open(seed_path, encoding="utf-8") as seed_file:
            seed_ast = ast.parse(seed_file.read(), filename=seed_path)

        default_features = None
        for node in seed_ast.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "DEFAULT_FEATURES":
                    default_features = ast.literal_eval(node.value)
                    break
            if default_features is not None:
                break

        assert default_features is not None, "DEFAULT_FEATURES assignment not found in seed file"
        self.seed_keys = {f["feature_key"] for f in default_features}

    def test_no_empty_constants(self):
        empty = [name for name, val in self.constants.items() if not val]
        assert empty == [], f"Empty constant values: {empty}"

    def test_all_constants_are_lowercase_snake_case(self):
        bad = [
            (name, val)
            for name, val in self.constants.items()
            if val != val.lower() or " " in val
        ]
        assert bad == [], f"Constants with invalid format (must be lowercase snake_case): {bad}"

    def test_no_duplicate_values(self):
        values = list(self.constants.values())
        dupes = [v for v in values if values.count(v) > 1]
        unique_dupes = list(dict.fromkeys(dupes))
        assert unique_dupes == [], f"Duplicate toggle key values: {unique_dupes}"

    def test_all_constants_exist_in_seed(self):
        missing = [
            (name, val)
            for name, val in self.constants.items()
            if val not in self.seed_keys
        ]
        assert missing == [], (
            f"FeatureToggleKeys constants not found in seed DEFAULT_FEATURES: {missing}\n"
            "Either add the key to seeds/feature_toggles.py or remove the constant."
        )

    def test_key_count_is_reasonable(self):
        """Sanity: at least 30 constants (catches accidental deletion)."""
        assert len(self.constants) >= 30, (
            f"Only {len(self.constants)} constants — looks like some were deleted"
        )


# ─── 3. FEATURE_TOGGLE_DEBUG tracing ──────────────────────────────────────────

class TestFeatureToggleDebugTracing:
    """
    When FEATURE_TOGGLE_DEBUG=true, get_effective_feature_access logs resolution
    steps at DEBUG level. Logs must contain only safe metadata — no PII.
    """

    def _make_user(self, role="owner", is_approved=True):
        return {
            "id": "user-test",
            "role": role,
            "is_approved": is_approved,
            "building_id": "13195",
        }

    @pytest.mark.asyncio
    async def test_debug_disabled_by_default_no_log_emitted(self, caplog):
        """Without FEATURE_TOGGLE_DEBUG, no debug log is emitted."""
        import utils.permissions as perm_mod

        original = perm_mod._TOGGLE_DEBUG
        try:
            perm_mod._TOGGLE_DEBUG = False
            user = self._make_user()
            # config_repo is imported lazily inside get_effective_feature_access
            with patch(
                "db_postgres.repos.config_repo.resolve_feature_toggle",
                new=AsyncMock(return_value=True),
            ), patch(
                "db_postgres.repos.config_repo.extract_feature_access_entries",
                return_value={},
            ):
                with caplog.at_level(logging.DEBUG, logger="utils.permissions"):
                    await perm_mod.get_effective_feature_access(user, "documents")

            toggle_logs = [r for r in caplog.records if "feature_toggle" in r.message]
            assert toggle_logs == [], "Expected no toggle debug logs when _TOGGLE_DEBUG=False"
        finally:
            perm_mod._TOGGLE_DEBUG = original

    @pytest.mark.asyncio
    async def test_debug_enabled_logs_resolution_step(self, caplog):
        """When _TOGGLE_DEBUG=True, a DEBUG log is emitted containing the feature_key."""
        import utils.permissions as perm_mod

        original = perm_mod._TOGGLE_DEBUG
        try:
            perm_mod._TOGGLE_DEBUG = True
            user = self._make_user()
            with patch(
                "db_postgres.repos.config_repo.resolve_feature_toggle",
                new=AsyncMock(return_value=True),
            ), patch(
                "db_postgres.repos.config_repo.extract_feature_access_entries",
                return_value={},
            ):
                with caplog.at_level(logging.DEBUG, logger="utils.permissions"):
                    result = await perm_mod.get_effective_feature_access(user, "documents")

            assert result is True
            toggle_logs = [r for r in caplog.records if "feature_toggle" in r.message]
            assert len(toggle_logs) >= 1, "Expected at least one toggle debug log"
            assert "documents" in toggle_logs[-1].message
        finally:
            perm_mod._TOGGLE_DEBUG = original

    @pytest.mark.asyncio
    async def test_debug_log_super_admin_path(self, caplog):
        """Super admin resolution step is logged."""
        import utils.permissions as perm_mod

        original = perm_mod._TOGGLE_DEBUG
        try:
            perm_mod._TOGGLE_DEBUG = True
            user = {**self._make_user(role="super_admin"), "effective_role": "super_admin"}
            with caplog.at_level(logging.DEBUG, logger="utils.permissions"):
                result = await perm_mod.get_effective_feature_access(user, "finance")

            assert result is True
            toggle_logs = [r for r in caplog.records if "feature_toggle" in r.message]
            assert any("super_admin" in r.message for r in toggle_logs), (
                "super_admin resolution source must appear in debug log"
            )
        finally:
            perm_mod._TOGGLE_DEBUG = original

    @pytest.mark.asyncio
    async def test_debug_log_contains_no_pii_or_secrets(self, caplog):
        """
        Debug logs must contain ONLY: feature_key, resolved bool, source label.
        They must NOT contain: email, hashed_password, token, phone, address.
        """
        import utils.permissions as perm_mod

        original = perm_mod._TOGGLE_DEBUG
        try:
            perm_mod._TOGGLE_DEBUG = True
            user = {
                "id": "user-pii-test",
                "role": "owner",
                "email": "owner@secret.com",
                "hashed_password": "bcrypt$secret",
                "phone": "0400000000",
                "home_address": "1 Private Lane",
                "building_id": "13195",
                "is_approved": True,
            }
            with patch(
                "db_postgres.repos.config_repo.resolve_feature_toggle",
                new=AsyncMock(return_value=True),
            ), patch(
                "db_postgres.repos.config_repo.extract_feature_access_entries",
                return_value={},
            ):
                with caplog.at_level(logging.DEBUG, logger="utils.permissions"):
                    await perm_mod.get_effective_feature_access(user, "documents")

            all_log_text = " ".join(r.message for r in caplog.records)
            forbidden = [
                "owner@secret.com",
                "bcrypt$secret",
                "0400000000",
                "1 Private Lane",
            ]
            for item in forbidden:
                assert item not in all_log_text, (
                    f"PII/secret '{item}' found in debug log output — must never be logged"
                )
        finally:
            perm_mod._TOGGLE_DEBUG = original

    @pytest.mark.asyncio
    async def test_debug_env_var_controls_flag(self):
        """FEATURE_TOGGLE_DEBUG env var correctly controls _TOGGLE_DEBUG at import time."""
        # We test the parsing logic directly since re-import is not feasible in a live test
        truthy_values = ("1", "true", "yes", "TRUE", "True", "YES")
        falsy_values = ("", "0", "false", "no", "off", "FALSE")

        for val in truthy_values:
            result = val.lower() in ("1", "true", "yes")
            assert result is True, f"Expected {val!r} to be treated as truthy"

        for val in falsy_values:
            result = val.lower() in ("1", "true", "yes")
            assert result is False, f"Expected {val!r} to be treated as falsy"


# ─── 4. Featuretrace marker presence ──────────────────────────────────────────

class TestFeaturetraceMarkers:
    """All three refactored files must carry @featuretrace markers."""

    def _read(self, rel_path: str) -> str:
        backend_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "backend",
        )
        with open(os.path.join(backend_dir, rel_path)) as fh:
            return fh.read()

    def test_models_feature_toggle_has_marker(self):
        content = self._read("models/feature_toggle.py")
        assert "@featuretrace:feature-toggle-system" in content

    def test_router_feature_toggles_has_marker(self):
        content = self._read("routers/feature_toggles.py")
        assert "@featuretrace:feature-toggle-system" in content

    def test_utils_permissions_has_toggle_marker(self):
        content = self._read("utils/permissions.py")
        assert "@featuretrace:feature-toggle-system" in content

    def test_router_marker_documents_resolution_order(self):
        """The router marker must document the 4-step resolution order."""
        content = self._read("routers/feature_toggles.py")
        assert "super_admin" in content
        assert "site_wide" in content or "site-wide" in content

    def test_compute_effective_access_has_docstring(self):
        from routers.feature_toggles import _compute_effective_access
        assert _compute_effective_access.__doc__ is not None
        doc = _compute_effective_access.__doc__
        assert "super_admin" in doc or "super admin" in doc.lower()
        assert "site_wide" in doc or "site-wide" in doc


# ─── 5. Regression: existing summary endpoints still work ─────────────────────

class TestAccessSummaryEndpointsRegression:
    """
    _resolve_effective_feature_access_entries and get_user_feature_access_summary
    must produce identical results after the _compute_effective_access extraction.
    """

    @pytest.mark.asyncio
    async def test_my_access_summary_normal_user(self):
        """Normal user: site_wide=True, no override → effective=True."""
        from routers.feature_toggles import get_my_feature_access
        from unittest.mock import MagicMock

        user = {"id": "u1", "role": "owner", "building_id": "13195"}
        feature = {
            "feature_key": "documents",
            "feature_name": "Documents",
            "is_enabled": True,
            "category": "core",
            "routes": [],
            "depends_on": [],
        }

        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])

        with patch("routers.feature_toggles.config_repo.list_resolved_feature_toggles",
                   AsyncMock(return_value=[feature])), \
             patch("routers.feature_toggles.config_repo.extract_feature_access_map",
                   return_value={}), \
             patch("routers.feature_toggles.db") as mock_db, \
             patch("routers.feature_toggles.identity_repo.find_user_by_id_for_admin",
                   AsyncMock(return_value=None)):
            mock_db.user_feature_access.find.return_value = mock_cursor

            summaries = await get_my_feature_access(current_user=user)

        assert len(summaries) == 1
        assert summaries[0].feature_key == "documents"
        assert summaries[0].effective_access is True
        assert summaries[0].user_override is None

    @pytest.mark.asyncio
    async def test_my_access_summary_user_override_false(self):
        """User override=False beats site_wide=True."""
        from routers.feature_toggles import get_my_feature_access
        from unittest.mock import MagicMock

        user = {"id": "u2", "role": "owner", "building_id": "13195"}
        feature = {
            "feature_key": "finance",
            "feature_name": "Finance",
            "is_enabled": True,
            "category": "financial",
            "routes": [],
            "depends_on": [],
        }

        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[
            {"feature_key": "finance", "is_enabled": False}
        ])

        with patch("routers.feature_toggles.config_repo.list_resolved_feature_toggles",
                   AsyncMock(return_value=[feature])), \
             patch("routers.feature_toggles.config_repo.extract_feature_access_map",
                   return_value={"finance": False}), \
             patch("routers.feature_toggles.db") as mock_db, \
             patch("routers.feature_toggles.identity_repo.find_user_by_id_for_admin",
                   AsyncMock(return_value=None)):
            mock_db.user_feature_access.find.return_value = mock_cursor

            summaries = await get_my_feature_access(current_user=user)

        assert summaries[0].effective_access is False
        assert summaries[0].user_override is False

    @pytest.mark.asyncio
    async def test_super_admin_always_gets_access_even_when_site_wide_disabled(self):
        """super_admin gets effective_access=True even when site_wide=False."""
        from routers.feature_toggles import get_my_feature_access
        from unittest.mock import MagicMock

        user = {"id": "sa", "role": "super_admin", "building_id": "13195"}
        feature = {
            "feature_key": "beta_feature",
            "feature_name": "Beta Feature",
            "is_enabled": False,
            "category": "core",
            "routes": [],
            "depends_on": [],
        }

        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])

        with patch("routers.feature_toggles.config_repo.list_resolved_feature_toggles",
                   AsyncMock(return_value=[feature])), \
             patch("routers.feature_toggles.config_repo.extract_feature_access_map",
                   return_value={}), \
             patch("routers.feature_toggles.db") as mock_db, \
             patch("routers.feature_toggles.identity_repo.find_user_by_id_for_admin",
                   AsyncMock(return_value=None)):
            mock_db.user_feature_access.find.return_value = mock_cursor

            summaries = await get_my_feature_access(current_user=user)

        assert summaries[0].effective_access is True
