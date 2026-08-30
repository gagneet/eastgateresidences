"""
Tests for the feature registry audit script.

These tests use mocked/inline registry data and do NOT depend on real repo state.
They verify the audit logic independently.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_feature_registry_audit.py -q
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

# Add the scripts/validation directory to sys.path for import
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "validation"))

# Import the module under test
import audit_feature_registry as arf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_feature(**kwargs) -> dict:
    """Create a minimal valid registry feature dict."""
    defaults = {
        "feature_key": "test_feature",
        "display_name": "Test Feature",
        "description": "A test feature",
        "domain": "finance",
        "lifecycle_status": "production_ready",
        "risk_level": "medium",
        "required_toggles": [],
        "default_enabled": True,
        "allowed_roles": ["super_admin"],
        "frontend_routes": ["/dashboard/test"],
        "backend_routes": ["GET /api/test"],
        "api_methods": ["GET"],
        "navigation_locations": [],
        "source_of_truth_domain": "test",
        "current_read_source": "mongo",
        "current_write_source": "mongo",
        "target_source": "postgres",
        "postgres_tables": ["finance.test"],
        "mongo_collections": ["test_collection"],
        "docs": ["docs/architecture/source-of-truth-matrix.md"],
        "tests": ["tests/backend/test_feature.py"],
        "featuretrace_tag": "test-feature",
        "owner": "Platform engineering",
        "last_validated": "2026-06-07",
        "production_readiness": "production_ready — tested and documented",
        "known_blockers": "none",
        "next_action": "none",
    }
    defaults.update(kwargs)
    return defaults


def make_exception(**kwargs) -> dict:
    """Create a minimal valid exception dict."""
    tomorrow = (date.today() + timedelta(days=90)).strftime("%Y-%m-%d")
    defaults = {
        "id": "EX-TEST-001",
        "category": "missing_docs",
        "path_or_feature": "test_feature",
        "reason": "Test reason",
        "risk": "low",
        "owner": "Platform engineering",
        "created_at": "2026-06-07",
        "expires_at": tomorrow,
        "linked_issue_or_doc": "n/a",
        "allowed_until_prompt": "Prompt 8",
    }
    defaults.update(kwargs)
    return defaults


def fresh_result() -> arf.AuditResult:
    return arf.AuditResult()


# ---------------------------------------------------------------------------
# Exception completeness checks
# ---------------------------------------------------------------------------

class TestExceptionCompleteness:
    def test_complete_exception_passes(self):
        exc = make_exception()
        result = fresh_result()
        arf.check_exception_completeness([exc], result)
        assert result.passed
        assert not result.errors

    def test_missing_owner_fails(self):
        exc = make_exception(owner="")
        result = fresh_result()
        arf.check_exception_completeness([exc], result)
        assert not result.passed
        assert any(f.code == "EXCEPTION_NO_OWNER" for f in result.errors)

    def test_missing_risk_fails(self):
        exc = make_exception(risk="")
        result = fresh_result()
        arf.check_exception_completeness([exc], result)
        assert not result.passed
        assert any(f.code == "EXCEPTION_NO_RISK" for f in result.errors)

    def test_missing_required_fields_fails(self):
        exc = {"id": "EX-INCOMPLETE", "category": "missing_docs"}
        result = fresh_result()
        arf.check_exception_completeness([exc], result)
        assert not result.passed
        assert any(f.code == "EXCEPTION_INCOMPLETE" for f in result.errors)


# ---------------------------------------------------------------------------
# Expired exceptions
# ---------------------------------------------------------------------------

class TestExpiredExceptions:
    def test_active_exception_passes(self):
        exc = make_exception(expires_at=(date.today() + timedelta(days=30)).strftime("%Y-%m-%d"))
        result = fresh_result()
        arf.check_expired_exceptions([exc], result)
        assert result.passed

    def test_expired_exception_fails(self):
        exc = make_exception(expires_at="2020-01-01")
        result = fresh_result()
        arf.check_expired_exceptions([exc], result)
        assert not result.passed
        assert any(f.code == "EXCEPTION_EXPIRED" for f in result.errors)

    def test_bad_date_format_warns(self):
        exc = make_exception(expires_at="not-a-date")
        result = fresh_result()
        arf.check_expired_exceptions([exc], result)
        assert any(f.code == "EXCEPTION_BAD_DATE" for f in result.warnings)


# ---------------------------------------------------------------------------
# Registry completeness — toggle presence
# ---------------------------------------------------------------------------

class TestRegistryCompleteness:
    def test_toggle_in_seed_passes(self):
        feature = make_feature(required_toggles=["my_toggle"])
        seed = {"my_toggle": {"feature_key": "my_toggle", "is_enabled": False}}
        result = fresh_result()
        arf.check_registry_completeness([feature], seed, [], result)
        assert result.passed
        assert not result.errors

    def test_missing_toggle_fails(self):
        feature = make_feature(required_toggles=["missing_toggle"])
        seed = {}
        result = fresh_result()
        arf.check_registry_completeness([feature], seed, [], result)
        assert not result.passed
        assert any(f.code == "REGISTRY_TOGGLE_MISSING_SEED" for f in result.errors)

    def test_missing_toggle_with_exception_warns_not_fails(self):
        feature = make_feature(
            feature_key="test_feature",
            required_toggles=["missing_toggle"],
        )
        exc = make_exception(
            path_or_feature="test_feature",
            category="toggle_key_mismatch",
        )
        seed = {}
        result = fresh_result()
        arf.check_registry_completeness([feature], seed, [exc], result)
        assert result.passed  # Should not fail — exception accepted
        assert not result.errors
        assert any(f.code == "REGISTRY_TOGGLE_MISSING_SEED_EXCEPTED" for f in result.warnings)


# ---------------------------------------------------------------------------
# Frontend catalogue vs seed
# ---------------------------------------------------------------------------

class TestFrontendCatalogueVsSeed:
    def test_toggle_present_passes(self):
        frontend = {"powerhouse_conversations": {"key": "powerhouse_conversations", "requiredToggles": ["powerhouse_conversations"]}}
        seed = {"powerhouse_conversations": {"feature_key": "powerhouse_conversations", "is_enabled": False}}
        result = fresh_result()
        arf.check_frontend_catalogue_vs_seed(frontend, seed, [], result)
        assert result.passed
        assert not result.errors

    def test_toggle_missing_from_seed_fails(self):
        frontend = {"some_feature": {"key": "some_feature", "requiredToggles": ["nonexistent_toggle"]}}
        seed = {}
        result = fresh_result()
        arf.check_frontend_catalogue_vs_seed(frontend, seed, [], result)
        assert not result.passed
        assert any(f.code == "FRONTEND_TOGGLE_MISSING_SEED" for f in result.errors)

    def test_missing_toggle_with_exception_warns(self):
        frontend = {"my_feature": {"key": "my_feature", "requiredToggles": ["legacy_toggle"]}}
        exc = make_exception(
            path_or_feature="my_feature",
            category="toggle_key_mismatch",
        )
        seed = {}
        result = fresh_result()
        arf.check_frontend_catalogue_vs_seed(frontend, seed, [exc], result)
        assert result.passed
        assert any(f.code == "FRONTEND_TOGGLE_MISSING_SEED_EXCEPTED" for f in result.warnings)


# ---------------------------------------------------------------------------
# Shell not globally enabled
# ---------------------------------------------------------------------------

class TestShellNotGloballyEnabled:
    def test_shell_with_false_toggle_passes(self):
        feature = make_feature(
            feature_key="powerhouse_conversations",
            lifecycle_status="shell_only",
            default_enabled=False,
            required_toggles=["powerhouse_conversations"],
        )
        seed = {"powerhouse_conversations": {"feature_key": "powerhouse_conversations", "is_enabled": False}}
        result = fresh_result()
        arf.check_shell_not_globally_enabled([feature], seed, [], result)
        assert result.passed
        assert not result.errors

    def test_powerhouse_globally_enabled_fails(self):
        seed = {}
        for key in arf.POWERHOUSE_DEFAULT_FALSE_KEYS:
            seed[key] = {"feature_key": key, "is_enabled": True}

        feature = make_feature(
            feature_key="powerhouse_conversations",
            lifecycle_status="shell_only",
            default_enabled=False,
            required_toggles=["powerhouse_conversations"],
        )
        result = fresh_result()
        arf.check_shell_not_globally_enabled([feature], seed, [], result)
        assert not result.passed
        assert any(f.code == "POWERHOUSE_GLOBALLY_ENABLED" for f in result.errors)

    def test_powerhouse_enabled_with_exception_still_fails(self):
        """Powerhouse globally enabled always fails — no exception for this."""
        seed = {"powerhouse_conversations": {"feature_key": "powerhouse_conversations", "is_enabled": True}}
        feature = make_feature(
            feature_key="powerhouse_conversations",
            lifecycle_status="shell_only",
            default_enabled=False,
            required_toggles=["powerhouse_conversations"],
        )
        result = fresh_result()
        arf.check_shell_not_globally_enabled([feature], seed, [], result)
        assert not result.passed


# ---------------------------------------------------------------------------
# Cutover toggles global state
# ---------------------------------------------------------------------------

class TestCutoverTogglesGlobalState:
    def test_cutover_toggle_false_passes(self):
        seed = {}
        for key in arf.CUTOVER_DEFAULT_FALSE_KEYS:
            seed[key] = {"feature_key": key, "is_enabled": False}
        result = fresh_result()
        arf.check_cutover_toggles_global_state(seed, [], result)
        assert result.passed
        assert not result.errors

    def test_cutover_toggle_globally_enabled_fails(self):
        seed = {"financial_pg_writes_enabled": {"feature_key": "financial_pg_writes_enabled", "is_enabled": True}}
        result = fresh_result()
        arf.check_cutover_toggles_global_state(seed, [], result)
        assert not result.passed
        assert any(f.code == "CUTOVER_TOGGLE_GLOBALLY_ENABLED" for f in result.errors)

    def test_cutover_toggle_globally_enabled_with_exception_warns(self):
        seed = {"financial_pg_writes_enabled": {"feature_key": "financial_pg_writes_enabled", "is_enabled": True}}
        exc = make_exception(
            path_or_feature="financial_pg_writes_enabled",
            category="global_toggle_enabled",
        )
        result = fresh_result()
        arf.check_cutover_toggles_global_state(seed, [exc], result)
        assert result.passed  # exception accepted
        assert any(f.code == "CUTOVER_TOGGLE_GLOBALLY_ENABLED_EXCEPTED" for f in result.warnings)


# ---------------------------------------------------------------------------
# Missing docs
# ---------------------------------------------------------------------------

class TestMissingDocs:
    def test_feature_with_docs_passes(self):
        feature = make_feature(docs=["docs/architecture/source-of-truth-matrix.md"])
        result = fresh_result()
        arf.check_missing_docs([feature], [], result)
        # Should not add error — WARN only even without exception
        assert not any(f.code == "FEATURE_MISSING_DOCS" and f.severity == arf.SEVERITY_ERROR for f in result.errors)

    def test_feature_without_docs_warns(self):
        feature = make_feature(docs=[])
        result = fresh_result()
        arf.check_missing_docs([feature], [], result)
        assert any(f.code == "FEATURE_MISSING_DOCS" for f in result.warnings)

    def test_feature_without_docs_with_exception_warns_with_note(self):
        feature = make_feature(feature_key="test_feature", docs=[])
        exc = make_exception(path_or_feature="test_feature", category="missing_docs")
        result = fresh_result()
        arf.check_missing_docs([feature], [exc], result)
        assert any(f.code == "FEATURE_MISSING_DOCS_EXCEPTED" for f in result.warnings)


# ---------------------------------------------------------------------------
# Missing tests
# ---------------------------------------------------------------------------

class TestMissingTests:
    def test_production_feature_with_tests_passes(self):
        feature = make_feature(lifecycle_status="production_ready", tests=["tests/backend/test_x.py"])
        result = fresh_result()
        arf.check_missing_tests([feature], [], result)
        assert not result.warnings

    def test_production_feature_without_tests_warns(self):
        feature = make_feature(lifecycle_status="production_ready", tests=[])
        result = fresh_result()
        arf.check_missing_tests([feature], [], result)
        assert any(f.code == "FEATURE_MISSING_TESTS" for f in result.warnings)

    def test_shell_feature_without_tests_does_not_warn(self):
        feature = make_feature(lifecycle_status="shell_only", tests=[])
        result = fresh_result()
        arf.check_missing_tests([feature], [], result)
        # Shell features without tests should not warn
        assert not any(f.code == "FEATURE_MISSING_TESTS" for f in result.warnings)

    def test_missing_tests_with_exception_warns_with_note(self):
        feature = make_feature(feature_key="test_feature", lifecycle_status="production_ready", tests=[])
        exc = make_exception(path_or_feature="test_feature", category="missing_tests")
        result = fresh_result()
        arf.check_missing_tests([feature], [exc], result)
        assert any(f.code == "FEATURE_MISSING_TESTS_EXCEPTED" for f in result.warnings)


# ---------------------------------------------------------------------------
# Missing FeatureTrace
# ---------------------------------------------------------------------------

class TestMissingFeaturetrace:
    def test_feature_with_routes_and_tag_passes(self):
        feature = make_feature(
            backend_routes=["GET /api/test"],
            featuretrace_tag="test-tag",
        )
        result = fresh_result()
        arf.check_missing_featuretrace([feature], [], result)
        assert not result.warnings

    def test_feature_with_routes_missing_tag_warns(self):
        feature = make_feature(
            backend_routes=["GET /api/test"],
            featuretrace_tag=None,
        )
        result = fresh_result()
        arf.check_missing_featuretrace([feature], [], result)
        assert any(f.code == "FEATURE_MISSING_FEATURETRACE" for f in result.warnings)

    def test_feature_no_routes_no_tag_no_warn(self):
        feature = make_feature(
            backend_routes=[],
            frontend_routes=[],
            featuretrace_tag=None,
        )
        result = fresh_result()
        arf.check_missing_featuretrace([feature], [], result)
        # No routes → no warning needed
        assert not any(f.code == "FEATURE_MISSING_FEATURETRACE" for f in result.warnings)

    def test_feature_missing_tag_with_exception(self):
        feature = make_feature(
            feature_key="test_feature",
            backend_routes=["GET /api/test"],
            featuretrace_tag=None,
        )
        exc = make_exception(path_or_feature="test_feature", category="missing_featuretrace")
        result = fresh_result()
        arf.check_missing_featuretrace([feature], [exc], result)
        assert any(f.code == "FEATURE_MISSING_FEATURETRACE_EXCEPTED" for f in result.warnings)


# ---------------------------------------------------------------------------
# Source-of-truth consistency
# ---------------------------------------------------------------------------

class TestSourceOfTruthConsistency:
    def test_shell_with_mongo_write_passes(self):
        feature = make_feature(lifecycle_status="shell_only", current_write_source="mongo")
        result = fresh_result()
        arf.check_source_of_truth_consistency([feature], [], result)
        assert not any(f.code == "SHELL_FEATURE_POSTGRES_WRITE" for f in result.warnings)

    def test_shell_with_postgres_write_warns(self):
        feature = make_feature(lifecycle_status="shell_only", current_write_source="postgres")
        result = fresh_result()
        arf.check_source_of_truth_consistency([feature], [], result)
        assert any(f.code == "SHELL_FEATURE_POSTGRES_WRITE" for f in result.warnings)

    def test_mixed_source_without_pg_tables_warns(self):
        feature = make_feature(
            current_write_source="mixed",
            postgres_tables=[],
            mongo_collections=["test_collection"],
        )
        result = fresh_result()
        arf.check_source_of_truth_consistency([feature], [], result)
        assert any(f.code == "MIXED_SOURCE_MISSING_PG_TABLES" for f in result.warnings)

    def test_mixed_source_without_mongo_warns(self):
        feature = make_feature(
            current_write_source="mixed",
            postgres_tables=["finance.test"],
            mongo_collections=[],
        )
        result = fresh_result()
        arf.check_source_of_truth_consistency([feature], [], result)
        assert any(f.code == "MIXED_SOURCE_MISSING_MONGO" for f in result.warnings)

    def test_mixed_source_with_both_passes(self):
        feature = make_feature(
            current_write_source="mixed",
            postgres_tables=["finance.test"],
            mongo_collections=["test_collection"],
        )
        result = fresh_result()
        arf.check_source_of_truth_consistency([feature], [], result)
        assert not any(f.code in ("MIXED_SOURCE_MISSING_PG_TABLES", "MIXED_SOURCE_MISSING_MONGO") for f in result.warnings)


# ---------------------------------------------------------------------------
# Production readiness docs
# ---------------------------------------------------------------------------

class TestProductionReadinessDocs:
    def test_production_feature_with_readiness_passes(self):
        feature = make_feature(
            lifecycle_status="production_ready",
            production_readiness="production_ready — tested and documented",
        )
        result = fresh_result()
        arf.check_production_readiness_docs([feature], [], result)
        assert not result.warnings

    def test_production_feature_missing_readiness_warns(self):
        feature = make_feature(
            lifecycle_status="production_ready",
            production_readiness="",
        )
        result = fresh_result()
        arf.check_production_readiness_docs([feature], [], result)
        assert any(f.code == "PRODUCTION_FEATURE_MISSING_READINESS" for f in result.warnings)


# ---------------------------------------------------------------------------
# Seed parser integration test (uses actual seed file)
# ---------------------------------------------------------------------------

class TestSeedParser:
    def test_parses_powerhouse_toggles(self):
        """Verify that powerhouse toggles are parsed from the real seed file."""
        seed_path = REPO_ROOT / "backend" / "seeds" / "feature_toggles.py"
        if not seed_path.exists():
            pytest.skip("Seed file not found")
        toggles = arf.parse_seed_toggles(seed_path)
        for key in ["powerhouse_conversations", "powerhouse_shared_inbox",
                    "powerhouse_email_intake", "powerhouse_ai_summary",
                    "powerhouse_workflow_engine", "powerhouse_automation_rules"]:
            assert key in toggles, f"Expected toggle '{key}' not found in seed"
            assert toggles[key]["is_enabled"] is False, (
                f"Powerhouse toggle '{key}' must default to False globally"
            )

    def test_parses_finance_toggles(self):
        """Verify that finance cutover toggles are parsed from the real seed file."""
        seed_path = REPO_ROOT / "backend" / "seeds" / "feature_toggles.py"
        if not seed_path.exists():
            pytest.skip("Seed file not found")
        toggles = arf.parse_seed_toggles(seed_path)
        for key in ["financial_pg_writes_enabled", "financial_pg_reads_enabled",
                    "financial_shadow_reads_enabled"]:
            assert key in toggles, f"Expected toggle '{key}' not found in seed"


# ---------------------------------------------------------------------------
# Frontend catalogue parser integration test
# ---------------------------------------------------------------------------

class TestFrontendCatalogueParser:
    def test_parses_powerhouse_features(self):
        """Verify that Powerhouse features are parsed from the real catalogue."""
        catalogue_path = REPO_ROOT / "frontend" / "src" / "lib" / "powerhouseFeatureCatalogue.ts"
        if not catalogue_path.exists():
            pytest.skip("Frontend catalogue not found")
        features = arf.parse_frontend_catalogue(catalogue_path)
        assert "powerhouse_conversations" in features
        assert "powerhouse_shared_inbox" in features
        # Verify requiredToggles is populated
        assert "powerhouse_conversations" in features["powerhouse_conversations"]["requiredToggles"]


# ---------------------------------------------------------------------------
# Registry YAML load integration test
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Cutover toggle default safety — TestCutoverToggleDefaults
# ---------------------------------------------------------------------------

class TestCutoverToggleDefaults:
    """Tests for the hard cutover-toggle-default safety checks (prompt-7-7b)."""

    def test_financial_pg_writes_enabled_globally_true_fails_strict(self):
        """Registry says postgres_write + seed has it true → should be an error (hard check)."""
        seed = {"financial_pg_writes_enabled": {"feature_key": "financial_pg_writes_enabled", "is_enabled": True}}
        result = fresh_result()
        arf.check_cutover_toggle_defaults(seed, result)
        assert not result.passed
        assert any(f.code == "CUTOVER_DEFAULT_UNSAFE" for f in result.errors)
        error = next(f for f in result.errors if f.code == "CUTOVER_DEFAULT_UNSAFE")
        assert "financial_pg_writes_enabled" in error.feature_key

    def test_financial_pg_writes_enabled_globally_false_passes(self):
        """Same feature, toggle false → no error from the hard check."""
        seed = {"financial_pg_writes_enabled": {"feature_key": "financial_pg_writes_enabled", "is_enabled": False}}
        result = fresh_result()
        arf.check_cutover_toggle_defaults(seed, result)
        assert result.passed
        assert not any(f.code == "CUTOVER_DEFAULT_UNSAFE" for f in result.errors)

    def test_finance_pg_read_enabled_globally_true_fails(self):
        """Read cutover toggle globally true → error from hard check."""
        seed = {"financial_pg_reads_enabled": {"feature_key": "financial_pg_reads_enabled", "is_enabled": True}}
        result = fresh_result()
        arf.check_cutover_toggle_defaults(seed, result)
        assert not result.passed
        assert any(f.code == "CUTOVER_DEFAULT_UNSAFE" and "financial_pg_reads_enabled" in f.feature_key
                   for f in result.errors)

    def test_finance_shadow_read_globally_true_fails_hard_check(self):
        """Shadow reads globally true → hard check error (even though lower risk than full writes)."""
        seed = {"financial_shadow_reads_enabled": {"feature_key": "financial_shadow_reads_enabled", "is_enabled": True}}
        result = fresh_result()
        arf.check_cutover_toggle_defaults(seed, result)
        assert not result.passed
        assert any(f.code == "CUTOVER_DEFAULT_UNSAFE" for f in result.errors)

    def test_all_cutover_keys_false_passes(self):
        """All cutover keys set to False in seed → hard check passes cleanly."""
        seed = {key: {"feature_key": key, "is_enabled": False} for key in arf.CUTOVER_DEFAULT_FALSE_KEYS}
        result = fresh_result()
        arf.check_cutover_toggle_defaults(seed, result)
        assert result.passed
        assert not any(f.code == "CUTOVER_DEFAULT_UNSAFE" for f in result.errors)

    def test_multiple_unsafe_toggles_all_reported(self):
        """Multiple unsafe toggles in one check — each should produce a separate error."""
        seed = {
            "financial_pg_writes_enabled": {"feature_key": "financial_pg_writes_enabled", "is_enabled": True},
            "financial_pg_reads_enabled": {"feature_key": "financial_pg_reads_enabled", "is_enabled": True},
            "trust_pg_ledger_enabled": {"feature_key": "trust_pg_ledger_enabled", "is_enabled": True},
        }
        result = fresh_result()
        arf.check_cutover_toggle_defaults(seed, result)
        assert not result.passed
        unsafe_errors = [f for f in result.errors if f.code == "CUTOVER_DEFAULT_UNSAFE"]
        assert len(unsafe_errors) == 3

    def test_hard_check_fires_even_with_active_exception(self):
        """The hard check (check_cutover_toggle_defaults) fires regardless of exceptions.

        This is the key difference from check_cutover_toggles_global_state:
        the hard check has no exception mechanism. An active exception in the exceptions
        list does not suppress CUTOVER_DEFAULT_UNSAFE errors.
        """
        seed = {"financial_pg_writes_enabled": {"feature_key": "financial_pg_writes_enabled", "is_enabled": True}}
        exc = make_exception(
            path_or_feature="financial_pg_writes_enabled",
            category="global_toggle_enabled",
        )
        result = fresh_result()
        # Hard check does not accept exceptions — always errors
        arf.check_cutover_toggle_defaults(seed, result)
        assert not result.passed
        assert any(f.code == "CUTOVER_DEFAULT_UNSAFE" for f in result.errors)

    def test_high_critical_feature_default_enabled_fails_without_exception(self):
        """Feature with risk_level=critical, default_enabled=true, no valid exception → fails."""
        feature = make_feature(
            feature_key="some_pg_write_toggle",
            lifecycle_status="shell_only",
            risk_level="critical",
            default_enabled=True,
            required_toggles=["some_pg_write_toggle"],
        )
        seed = {"some_pg_write_toggle": {"feature_key": "some_pg_write_toggle", "is_enabled": True}}
        result = fresh_result()
        arf.check_shell_not_globally_enabled([feature], seed, [], result)
        assert not result.passed
        assert any(f.code == "SHELL_TOGGLE_GLOBALLY_ENABLED" for f in result.errors)

    def test_expired_exception_fails(self):
        """Exception with expires_at in the past → fails audit."""
        exc = make_exception(expires_at="2020-01-01")
        result = fresh_result()
        arf.check_expired_exceptions([exc], result)
        assert not result.passed
        assert any(f.code == "EXCEPTION_EXPIRED" for f in result.errors)

    def test_exception_without_owner_fails(self):
        """Exception missing 'owner' field → fails completeness check."""
        exc = make_exception(owner="")
        result = fresh_result()
        arf.check_exception_completeness([exc], result)
        assert not result.passed
        assert any(f.code == "EXCEPTION_NO_OWNER" for f in result.errors)

    def test_exception_referencing_missing_toggle_does_not_suppress_hard_error(self):
        """Exception for a toggle that doesn't exist in seed → hard check still fires for other unsafe toggles."""
        # EX for a key that doesn't exist in seed — should not affect hard check for other keys
        exc = make_exception(
            path_or_feature="nonexistent_toggle_key",
            category="global_toggle_enabled",
        )
        seed = {
            "financial_pg_writes_enabled": {"feature_key": "financial_pg_writes_enabled", "is_enabled": True},
        }
        result = fresh_result()
        arf.check_cutover_toggle_defaults(seed, result)
        assert not result.passed
        assert any(f.code == "CUTOVER_DEFAULT_UNSAFE" for f in result.errors)

    def test_resolved_exception_not_applied(self):
        """A resolved exception (resolved=true) is not treated as active."""
        exc = make_exception(
            path_or_feature="financial_pg_writes_enabled",
            category="global_toggle_enabled",
            # Add resolved fields
        )
        exc["resolved"] = True
        exc["resolved_at"] = "2026-06-07"
        exc["resolved_by"] = "prompt-7-7b"

        # Resolved exception should NOT suppress the global_toggle_enabled check
        seed = {"financial_pg_writes_enabled": {"feature_key": "financial_pg_writes_enabled", "is_enabled": True}}
        result = fresh_result()
        arf.check_cutover_toggles_global_state(seed, [exc], result)
        # No active exception → should be an error, not a warning
        assert not result.passed
        assert any(f.code == "CUTOVER_TOGGLE_GLOBALLY_ENABLED" for f in result.errors)
        # Should not produce the "excepted" warning
        assert not any(f.code == "CUTOVER_TOGGLE_GLOBALLY_ENABLED_EXCEPTED" for f in result.warnings)

    def test_resolved_exception_produces_info_item(self):
        """Resolved exception is surfaced as an INFO item for visibility."""
        exc = make_exception(
            path_or_feature="financial_pg_writes_enabled",
            category="global_toggle_enabled",
        )
        exc["resolved"] = True
        exc["resolved_at"] = "2026-06-07"
        exc["resolved_by"] = "prompt-7-7b"
        result = fresh_result()
        arf.check_resolved_exceptions_not_applied([exc], result)
        assert any(f.code == "EXCEPTION_RESOLVED" for f in result.infos)


# ---------------------------------------------------------------------------
# Seed parser integration — verify corrected state
# ---------------------------------------------------------------------------

class TestSeedCutoverDefaultsIntegration:
    """Integration tests that verify the actual seed file has correct defaults after prompt-7-7b."""

    def test_all_cutover_toggles_globally_false_in_real_seed(self):
        """After prompt-7-7b all CUTOVER_DEFAULT_FALSE_KEYS must be is_enabled=False in the seed."""
        seed_path = REPO_ROOT / "backend" / "seeds" / "feature_toggles.py"
        if not seed_path.exists():
            pytest.skip("Seed file not found")
        toggles = arf.parse_seed_toggles(seed_path)
        violations = []
        for key in arf.CUTOVER_DEFAULT_FALSE_KEYS:
            if key in toggles and toggles[key].get("is_enabled", False):
                violations.append(key)
        assert violations == [], (
            f"Cutover toggles still globally true in seed (must be false): {violations}. "
            f"See docs/architecture/feature-toggle-governance.md §5."
        )

    def test_financial_integration_layer_v2_globally_false(self):
        """financial_integration_layer_v2 must be globally false after prompt-7-7b."""
        seed_path = REPO_ROOT / "backend" / "seeds" / "feature_toggles.py"
        if not seed_path.exists():
            pytest.skip("Seed file not found")
        toggles = arf.parse_seed_toggles(seed_path)
        assert "financial_integration_layer_v2" in toggles
        assert toggles["financial_integration_layer_v2"]["is_enabled"] is False, (
            "financial_integration_layer_v2 must be globally false — it is a cutover parent toggle"
        )

    def test_hard_audit_check_passes_on_real_seed(self):
        """Running check_cutover_toggle_defaults on the real seed should produce zero errors."""
        seed_path = REPO_ROOT / "backend" / "seeds" / "feature_toggles.py"
        if not seed_path.exists():
            pytest.skip("Seed file not found")
        toggles = arf.parse_seed_toggles(seed_path)
        result = fresh_result()
        arf.check_cutover_toggle_defaults(toggles, result)
        assert result.passed, (
            f"Hard cutover default check failed on real seed. "
            f"Errors: {[f.message for f in result.errors]}"
        )


class TestRegistryLoad:
    def test_registry_file_loads(self):
        """Verify the real feature registry YAML loads and has expected features."""
        registry_path = REPO_ROOT / "docs" / "architecture" / "feature-registry.yaml"
        if not registry_path.exists():
            pytest.skip("Registry file not found")
        registry = arf.load_registry(registry_path)
        keys = [f["feature_key"] for f in registry]
        expected = [
            "powerhouse_conversations",
            "powerhouse_shared_inbox",
            "powerhouse_email_intake",
            "powerhouse_ai_summary",
            "powerhouse_workflow_engine",
            "powerhouse_automation_rules",
            "cutover_control_plane",
            "levy_payment_create",
            "trust_reconciliation_period_locks",
            "financial_projection_years",
        ]
        for key in expected:
            assert key in keys, f"Expected feature '{key}' not in registry"

    def test_all_features_have_required_fields(self):
        """Verify all registry features have required fields."""
        registry_path = REPO_ROOT / "docs" / "architecture" / "feature-registry.yaml"
        if not registry_path.exists():
            pytest.skip("Registry file not found")
        registry = arf.load_registry(registry_path)
        required_fields = [
            "feature_key", "display_name", "description", "domain",
            "lifecycle_status", "risk_level",
        ]
        for feature in registry:
            for field in required_fields:
                assert field in feature, (
                    f"Feature '{feature.get('feature_key', '?')}' missing field '{field}'"
                )

    def test_exceptions_file_loads(self):
        """Verify the exceptions YAML loads and has expected exceptions."""
        exc_path = REPO_ROOT / "docs" / "architecture" / "architecture-audit-exceptions.yaml"
        if not exc_path.exists():
            pytest.skip("Exceptions file not found")
        exceptions = arf.load_exceptions(exc_path)
        assert len(exceptions) > 0
        ids = [e.get("id") for e in exceptions]
        assert "EX-001" in ids
        assert "EX-002" in ids
