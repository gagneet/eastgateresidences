# @featuretrace:cutover-control-plane — P0.3 toggle safety hardening tests.
# Layer: test
# Data flow: pytest → core.toggle_classification / config_repo guards / routers.feature_toggles → ProtectedToggleError / 403 (global)
# Related: backend/core/toggle_classification.py
#          backend/db_postgres/repos/config_repo.py
#          backend/routers/feature_toggles.py
#          backend/alembic/versions/0058_toggle_safety_backfill.py
# Tests: this file
"""P0.3 toggle safety hardening — classification, guards, and metadata invariants.

Background: on 2026-06-09 a blanket enable-all pass flipped every row in
core.feature_toggles to TRUE, including bi_pg_primary_enabled (a PG-primary
data-source toggle whose seed of record requires is_enabled=False,
allowed_roles={super_admin}, depends_on={bi_analytics_enabled}). These tests
pin the controls that prevent recurrence:

  1. Canonical classification covers every cutover-class key the runtime gates.
  2. Application write paths refuse to globally enable protected toggles.
  3. Protected toggles can never persist with weaker safety metadata than
     the classification declares.
  4. The seed of record keeps protected toggles globally disabled.
"""
import importlib.util
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from core.toggle_classification import (
    PROTECTED_TOGGLE_CLASSES,
    PROTECTED_TOGGLE_KEYS,
    PROTECTED_TOGGLE_SAFETY_METADATA,
    TOGGLE_CLASSIFICATION,
    ProtectedToggleError,
    ToggleClass,
    assert_global_enable_allowed,
    get_toggle_class,
    is_protected_toggle,
)
from db_postgres.repos import config_repo


# ─────────────────────────────────────────────────────────────────────────────
# 1. Classification completeness and consistency
# ─────────────────────────────────────────────────────────────────────────────

class TestClassificationCompleteness:
    def test_all_required_classes_exist(self):
        """The P0.3 spec mandates these classes at minimum."""
        required = {
            "visibility", "ui_only", "experimental", "admin_only",
            "cutover_sensitive", "data_source_primary", "shadow_read",
            "shadow_write", "finance_write", "trust_write",
        }
        assert {c.value for c in ToggleClass} >= required

    def test_every_cutover_runtime_key_is_protected(self):
        """Keys the cutover umbrella gates at runtime must be classified protected."""
        from services.cutover_config_service import (
            _CHILD_FEATURE_KEYS,
            UMBRELLA_FEATURE_KEY,
        )
        for key in _CHILD_FEATURE_KEYS | {UMBRELLA_FEATURE_KEY}:
            assert is_protected_toggle(key), f"{key} is not classified protected"

    def test_bi_pg_primary_is_protected_data_source_primary(self):
        from services.bi_toggle_service import BI_PG_PRIMARY
        assert is_protected_toggle(BI_PG_PRIMARY)
        assert get_toggle_class(BI_PG_PRIMARY) is ToggleClass.DATA_SOURCE_PRIMARY

    def test_legacy_alias_keys_are_protected(self):
        from services.cutover_config_service import LEGACY_FEATURE_KEY_ALIASES
        for alias, canonical in LEGACY_FEATURE_KEY_ALIASES.items():
            if is_protected_toggle(canonical):
                assert is_protected_toggle(alias) or alias not in TOGGLE_CLASSIFICATION, (
                    f"alias {alias} of protected {canonical} is classified non-protected"
                )
        # The two aliases that exist as real seed entries must be explicit.
        assert is_protected_toggle("financial_core.read_from_postgres")
        assert is_protected_toggle("financial_core.shadow_read_postgres")

    def test_safety_metadata_covers_exactly_protected_keys(self):
        assert set(PROTECTED_TOGGLE_SAFETY_METADATA) == set(PROTECTED_TOGGLE_KEYS)

    def test_all_protected_metadata_requires_super_admin(self):
        for key, meta in PROTECTED_TOGGLE_SAFETY_METADATA.items():
            assert "super_admin" in meta["allowed_roles"], key

    def test_bi_pg_primary_safety_metadata(self):
        meta = PROTECTED_TOGGLE_SAFETY_METADATA["bi_pg_primary_enabled"]
        assert meta["allowed_roles"] == ["super_admin"]
        assert meta["depends_on"] == ["bi_analytics_enabled"]

    def test_unclassified_key_defaults_to_visibility_and_unprotected(self):
        assert get_toggle_class("maintenance") is ToggleClass.VISIBILITY
        assert not is_protected_toggle("maintenance")

    def test_visibility_class_is_not_protected(self):
        assert ToggleClass.VISIBILITY not in PROTECTED_TOGGLE_CLASSES
        assert ToggleClass.UI_ONLY not in PROTECTED_TOGGLE_CLASSES
        assert ToggleClass.EXPERIMENTAL not in PROTECTED_TOGGLE_CLASSES
        assert ToggleClass.ADMIN_ONLY not in PROTECTED_TOGGLE_CLASSES


# ─────────────────────────────────────────────────────────────────────────────
# 2. Guard primitive
# ─────────────────────────────────────────────────────────────────────────────

class TestAssertGlobalEnableAllowed:
    def test_protected_key_raises(self):
        with pytest.raises(ProtectedToggleError) as exc:
            assert_global_enable_allowed("bi_pg_primary_enabled")
        assert "bi_pg_primary_enabled" in str(exc.value)
        assert "data_source_primary" in str(exc.value)

    def test_every_protected_key_raises(self):
        for key in PROTECTED_TOGGLE_KEYS:
            with pytest.raises(ProtectedToggleError):
                assert_global_enable_allowed(key)

    def test_non_protected_key_passes(self):
        assert_global_enable_allowed("maintenance")
        assert_global_enable_allowed("powerhouse_conversations")  # experimental, not protected

    def test_escape_hatch_passes(self):
        assert_global_enable_allowed(
            "bi_pg_primary_enabled", _allow_protected_global_enable=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. config_repo write-path guards
# ─────────────────────────────────────────────────────────────────────────────

_DB_SENTINEL = RuntimeError("sentinel: guard passed, DB layer reached")


def _patch_db_boundary():
    """Make the first DB-touching call raise a sentinel so tests can prove the
    guard either blocked before the DB (ProtectedToggleError) or passed through
    (sentinel)."""
    return patch.object(
        config_repo, "resolve_actor_user_id",
        AsyncMock(side_effect=_DB_SENTINEL),
    )


def _patch_graduation(graduated: bool):
    """Pin the state-derived `graduated` permission.

    `_protected_global_enable_graduated` reads LIVE `core.domain_cutover_status`
    via toggle_graduation_service. Left unstubbed, these tests assert the guard's
    behaviour but actually measure whichever domains happen to be promoted in the
    developer's database — so promoting East Gate's finance_ledger on 2026-08-28
    turned two of them red without any code changing.

    Both states are worth asserting and neither should depend on the environment,
    so graduation is pinned explicitly here.
    """
    return patch.object(
        config_repo, "_protected_global_enable_graduated",
        AsyncMock(return_value=graduated),
    )


class TestConfigRepoGuards:
    async def test_update_global_enable_protected_raises_before_db(self):
        """Not graduated: the gate holds and nothing reaches the DB."""
        with _patch_db_boundary(), _patch_graduation(False):
            with pytest.raises(ProtectedToggleError):
                await config_repo.update_global_feature_toggle(
                    "bi_pg_primary_enabled", {"is_enabled": True},
                )

    async def test_update_global_enable_protected_allowed_once_graduated(self):
        """Graduated: the gate has been SATISFIED, not overridden.

        Every domain the key routes is already authoritative in PostgreSQL, so
        the enable proceeds to the DB. This is the state East Gate reached on
        2026-08-28 when the four withheld domain_cutover_status rows were
        restored — and asserting it here is what stops that legitimate change
        from looking like a guard regression.
        """
        with _patch_db_boundary(), _patch_graduation(True):
            with pytest.raises(RuntimeError, match="sentinel"):
                await config_repo.update_global_feature_toggle(
                    "bi_pg_primary_enabled", {"is_enabled": True},
                )

    async def test_update_global_disable_protected_is_allowed(self):
        with _patch_db_boundary(), _patch_graduation(False):
            with pytest.raises(RuntimeError, match="sentinel"):
                await config_repo.update_global_feature_toggle(
                    "bi_pg_primary_enabled", {"is_enabled": False},
                )

    async def test_update_global_enable_protected_escape_hatch(self):
        with _patch_db_boundary(), _patch_graduation(False):
            with pytest.raises(RuntimeError, match="sentinel"):
                await config_repo.update_global_feature_toggle(
                    "financial_pg_writes_enabled",
                    {"is_enabled": True},
                    _allow_protected_global_enable=True,
                )

    async def test_update_global_enable_unprotected_is_allowed(self):
        with _patch_db_boundary():
            with pytest.raises(RuntimeError, match="sentinel"):
                await config_repo.update_global_feature_toggle(
                    "maintenance", {"is_enabled": True},
                )

    async def test_create_protected_defaults_to_enabled_and_raises(self):
        """create defaults is_enabled=True — creating a protected key without
        explicitly disabling it must be refused."""
        with _patch_db_boundary(), _patch_graduation(False):
            with pytest.raises(ProtectedToggleError):
                await config_repo.create_global_feature_toggle(
                    {"feature_key": "trust_pg_ledger_enabled", "feature_name": "x"},
                )

    async def test_create_protected_disabled_is_allowed(self):
        with _patch_db_boundary(), _patch_graduation(False):
            with pytest.raises(RuntimeError, match="sentinel"):
                await config_repo.create_global_feature_toggle(
                    {
                        "feature_key": "trust_pg_ledger_enabled",
                        "feature_name": "x",
                        "is_enabled": False,
                    },
                )


class TestSafetyMetadataFloor:
    def test_empty_metadata_backfilled_for_protected_key(self):
        merged = config_repo._merge_protected_safety_metadata(
            "bi_pg_primary_enabled", "allowed_roles", [],
        )
        assert merged == ["super_admin"]
        merged = config_repo._merge_protected_safety_metadata(
            "bi_pg_primary_enabled", "depends_on", None,
        )
        assert merged == ["bi_analytics_enabled"]

    def test_existing_entries_preserved_and_floor_added(self):
        merged = config_repo._merge_protected_safety_metadata(
            "trust_pg_ledger_enabled", "allowed_roles", ["strata_admin"],
        )
        assert "strata_admin" in merged
        assert "super_admin" in merged

    def test_no_duplicates_when_floor_already_present(self):
        merged = config_repo._merge_protected_safety_metadata(
            "bi_pg_primary_enabled", "allowed_roles", ["super_admin"],
        )
        assert merged == ["super_admin"]

    def test_unprotected_key_unchanged(self):
        merged = config_repo._merge_protected_safety_metadata(
            "maintenance", "allowed_roles", ["owner"],
        )
        assert merged == ["owner"]


# ─────────────────────────────────────────────────────────────────────────────
# 4. Router behaviour — 403 on protected global enable; override path intact
# ─────────────────────────────────────────────────────────────────────────────

_SUPER_ADMIN = {"id": "u-1", "email": "sa@example.com", "role": "super_admin"}


class TestRouterProtectedToggle:
    async def test_put_global_enable_protected_returns_403(self):
        from routers.feature_toggles import update_feature_toggle
        from models.feature_toggle import FeatureToggleUpdate

        with patch("routers.feature_toggles.config_repo") as repo:
            repo.get_global_feature_toggle = AsyncMock(
                return_value={"feature_key": "bi_pg_primary_enabled"},
            )
            repo.update_global_feature_toggle = AsyncMock(
                side_effect=ProtectedToggleError(
                    "bi_pg_primary_enabled", ToggleClass.DATA_SOURCE_PRIMARY,
                ),
            )
            with pytest.raises(HTTPException) as exc:
                await update_feature_toggle(
                    feature_key="bi_pg_primary_enabled",
                    toggle_data=FeatureToggleUpdate(is_enabled=True),
                    building_id=None,
                    current_user=_SUPER_ADMIN,
                )
        assert exc.value.status_code == 403
        assert "feature_toggle_overrides" in exc.value.detail

    async def test_per_building_protected_promotion_logs_warning(self, caplog):
        from db_postgres.repos import config_repo

        with patch.object(config_repo, "resolve_scheme_context", AsyncMock(return_value={"tenant_id": "t", "scheme_id": "s"})), \
             patch.object(config_repo, "resolve_actor_user_id", AsyncMock(side_effect=_DB_SENTINEL)):
            with pytest.raises(RuntimeError, match="sentinel"):
                await config_repo.upsert_feature_toggle_override(
                    "13195",
                    "bi_pg_primary_enabled",
                    True,
                    actor_email="sa.com",
                    reason="test promotion",
                )

        assert "PROTECTED TOGGLE PROMOTION" in caplog.text
        assert "bi_pg_primary_enabled" in caplog.text

    async def test_put_per_building_override_enable_still_allowed(self):
        """Per-building promotion (the sanctioned path) must keep working."""
        from routers.feature_toggles import update_feature_toggle
        from models.feature_toggle import FeatureToggleUpdate

        resolved = {
            "feature_key": "bi_pg_primary_enabled",
            "feature_name": "BI PG Primary",
            "is_enabled": True,
            "routes": [],
            "depends_on": [],
        }
        with patch("routers.feature_toggles.config_repo") as repo:
            repo.get_global_feature_toggle = AsyncMock(
                return_value={"feature_key": "bi_pg_primary_enabled"},
            )
            repo.upsert_feature_toggle_override = AsyncMock(return_value=resolved)
            result = await update_feature_toggle(
                feature_key="bi_pg_primary_enabled",
                toggle_data=FeatureToggleUpdate(is_enabled=True),
                building_id="13195",
                current_user=_SUPER_ADMIN,
            )
        repo.upsert_feature_toggle_override.assert_awaited_once()
        assert result["is_enabled"] is True

    async def test_post_create_protected_enabled_returns_403(self):
        from routers.feature_toggles import create_feature_toggle
        from models.feature_toggle import FeatureToggleCreate

        with patch("routers.feature_toggles.config_repo") as repo:
            repo.get_global_feature_toggle = AsyncMock(return_value=None)
            repo.create_global_feature_toggle = AsyncMock(
                side_effect=ProtectedToggleError(
                    "trust_pg_ledger_enabled", ToggleClass.TRUST_WRITE,
                ),
            )
            with pytest.raises(HTTPException) as exc:
                await create_feature_toggle(
                    toggle_data=FeatureToggleCreate(
                        feature_key="trust_pg_ledger_enabled",
                        feature_name="Trust PG Ledger",
                        is_enabled=True,
                    ),
                    current_user=_SUPER_ADMIN,
                )
        assert exc.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# 5. Seed of record stays safe + migration sanity
# ─────────────────────────────────────────────────────────────────────────────

class TestSeedOfRecordInvariants:
    def test_protected_keys_in_seed_are_globally_disabled(self):
        from seeds.feature_toggles import DEFAULT_FEATURES
        seeded = {f["feature_key"]: f for f in DEFAULT_FEATURES}
        for key in PROTECTED_TOGGLE_KEYS:
            if key not in seeded:
                continue  # e.g. settings/users PG-read gates are runtime-only keys
            entry = seeded[key]
            assert entry["is_enabled"] is False, (
                f"seed must keep protected toggle {key} globally disabled"
            )
            assert "super_admin" in entry.get("roles", []), (
                f"seed entry for protected toggle {key} must be super_admin-only"
            )

    def test_bi_pg_primary_seed_matches_classification(self):
        from seeds.feature_toggles import DEFAULT_FEATURES
        entry = next(
            f for f in DEFAULT_FEATURES if f["feature_key"] == "bi_pg_primary_enabled"
        )
        assert entry["is_enabled"] is False
        assert entry["roles"] == ["super_admin"]
        assert entry["depends_on"] == ["bi_analytics_enabled"]


class TestMigration0058:
    def test_revision_id_fits_alembic_version_column(self):
        # Parsed from source: importing the module would resolve `from alembic
        # import op` against backend/alembic/ (the migrations dir), not the lib.
        import re
        from pathlib import Path
        src = (
            Path(__file__).resolve().parents[2]
            / "backend" / "alembic" / "versions" / "0058_toggle_safety_backfill.py"
        ).read_text()
        revision = re.search(r'^revision = "([^"]+)"', src, re.M).group(1)
        down_revision = re.search(r'^down_revision = "([^"]+)"', src, re.M).group(1)
        assert len(revision) <= 32  # core.alembic_version is varchar(32)
        assert down_revision == "0057_scheme_manager_appointments"
        assert '_FORCE_DISABLE_KEYS = ["bi_pg_primary_enabled"]' in src


class TestAlembicRevisionIds:
    def test_all_revision_ids_fit_alembic_version_column(self):
        import re
        from pathlib import Path

        versions_dir = (
            Path(__file__).resolve().parents[2] / "backend" / "alembic" / "versions"
        )
        offenders: list[str] = []
        for path in versions_dir.glob("*.py"):
            match = re.search(r'^revision = "([^"]+)"', path.read_text(), re.M)
            if match and len(match.group(1)) > 32:
                offenders.append(f"{path.name}:{match.group(1)}")

        assert offenders == []


class TestMigration0077:
    def test_levy_item_grace_deadline_backfill_has_non_null_fallback(self):
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[2]
            / "backend"
            / "alembic"
            / "versions"
            / "0077_arrears_grace_meta.py"
        ).read_text()

        assert "SET grace_deadline_date = COALESCE((" in src
        assert "(CURRENT_DATE + INTERVAL '14 days')::DATE" in src
        assert '"grace_deadline_date",\n        existing_type=sa.Date(),\n        nullable=False' in src


# ─────────────────────────────────────────────────────────────────────────────
# 6. Strict protected drift audit + deployment preflight gate
# ─────────────────────────────────────────────────────────────────────────────

def _load_toggle_drift_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "audits" / "toggle_drift.py"
    spec = importlib.util.spec_from_file_location("toggle_drift_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestStrictProtectedDriftAudit:
    async def test_strict_protected_fails_unsafe_metadata(self, monkeypatch):
        drift = _load_toggle_drift_module()
        monkeypatch.setattr(drift, "_HAS_ASYNCPG", True)
        monkeypatch.setattr(drift, "_build_seed_map", lambda: {"bi_pg_primary_enabled": False})

        async def globals_ok():
            return {"bi_pg_primary_enabled": False}

        async def unsafe_metadata():
            return {"bi_pg_primary_enabled": {"allowed_roles": [], "depends_on": []}}

        monkeypatch.setattr(drift, "_collect_pg_globals", globals_ok)
        monkeypatch.setattr(drift, "_collect_pg_safety_metadata", unsafe_metadata)

        result = await drift.run_audit(skip_mongo=True, strict_protected=True)

        assert result["has_drift"] is True
        violations = result["pg"]["protected_metadata_violations"]
        assert {v["field"] for v in violations} == {"allowed_roles", "depends_on"}

    async def test_strict_protected_passes_corrected_rows(self, monkeypatch):
        drift = _load_toggle_drift_module()
        monkeypatch.setattr(drift, "_HAS_ASYNCPG", True)
        monkeypatch.setattr(drift, "_build_seed_map", lambda: {"bi_pg_primary_enabled": False})

        async def globals_ok():
            return {"bi_pg_primary_enabled": False}

        async def safe_metadata():
            return {
                "bi_pg_primary_enabled": {
                    "allowed_roles": ["super_admin"],
                    "depends_on": ["bi_analytics_enabled"],
                }
            }

        monkeypatch.setattr(drift, "_collect_pg_globals", globals_ok)
        monkeypatch.setattr(drift, "_collect_pg_safety_metadata", safe_metadata)

        result = await drift.run_audit(skip_mongo=True, strict_protected=True)

        assert result["has_drift"] is False
        assert result["pg"]["protected_metadata_violations"] == []

    async def test_strict_protected_fails_closed_when_pg_audit_errors(self, monkeypatch):
        drift = _load_toggle_drift_module()
        monkeypatch.setattr(drift, "_HAS_ASYNCPG", True)
        monkeypatch.setattr(drift, "_build_seed_map", lambda: {"bi_pg_primary_enabled": False})

        async def broken_pg():
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(drift, "_collect_pg_globals", broken_pg)

        result = await drift.run_audit(skip_mongo=True, strict_protected=True)

        assert result["has_drift"] is True
        assert "error" in result["pg"]


class TestDeploymentProtectedTogglePreflight:
    def _make_fake_deploy_root(self, tmp_path, exit_code: int):
        backend_bin = tmp_path / "backend" / "venv" / "bin"
        backend_bin.mkdir(parents=True)
        audit = tmp_path / "toggle_drift.py"
        audit.write_text("# fake audit\n")
        python = backend_bin / "python"
        python.write_text(textwrap.dedent(f"""\
            #!/bin/sh
            printf '%s\n' "$@" > "{tmp_path / 'python_args.txt'}"
            exit {exit_code}
        """))
        python.chmod(python.stat().st_mode | stat.S_IXUSR)
        return audit

    def _run_gate(self, tmp_path, audit):
        deploy = Path(__file__).resolve().parents[2] / "scripts" / "deployment" / "deploy.sh"
        command = textwrap.dedent(f"""\
            source "{deploy}"
            PROJECT_ROOT="{tmp_path}"
            BACKEND_DIR="{tmp_path / 'backend'}"
            PROTECTED_TOGGLE_DRIFT_AUDIT="{audit}"
            DEPLOYMENT_LOG="{tmp_path / 'deploy.log'}"
            enforce_protected_toggle_drift_gate
        """)
        return subprocess.run(["bash", "-c", command], text=True, capture_output=True)

    def test_deployment_preflight_invokes_strict_protected_validation(self, tmp_path):
        audit = self._make_fake_deploy_root(tmp_path, exit_code=0)

        result = self._run_gate(tmp_path, audit)

        assert result.returncode == 0, result.stderr + result.stdout
        args = (tmp_path / "python_args.txt").read_text()
        assert str(audit) in args
        assert "--strict-protected" in args

    def test_deployment_preflight_blocks_on_validation_failure(self, tmp_path):
        audit = self._make_fake_deploy_root(tmp_path, exit_code=23)

        result = self._run_gate(tmp_path, audit)

        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "Protected feature-toggle drift detected" in combined
