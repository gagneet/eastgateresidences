# @featuretrace:trust-accounting — Tests for the one-off fix_13195_trust_account_metadata.py
# correction script's pure diff-computation logic. No real DB access — script's Mongo/
# Postgres I/O only happens inside main(), which this file does not call.
# Layer: test
# Related: backend/scripts/fix_13195_trust_account_metadata.py
#          docs/architecture/financial_field_level_traceability.md §9.1
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "scripts"
    / "fix_13195_trust_account_metadata.py"
)


def _load_script_module():
    """Import the script as a module without running main() (guarded by __main__)."""
    spec = importlib.util.spec_from_file_location("fix_13195_trust_account_metadata", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # backend/ is on sys.path via conftest.py for the sibling `from db_postgres.engine import ...`
    # import inside the script to resolve.
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script_module()


class TestComputeAccountDiff:
    def test_both_fields_changed(self, script):
        mongo_acct = {"bank_name": "Macquire Bank", "account_name": "New Name"}
        pg_current = {"bank_name": "strata_web_statement_manual", "account_name": "Old Name"}

        changed_fields, diff = script._compute_account_diff(
            "admin", "acct-1", mongo_acct, pg_current
        )

        assert changed_fields == {"bank_name": "Macquire Bank", "account_name": "New Name"}
        assert diff["trust_account_id"] == "acct-1"
        assert diff["fund_type"] == "admin"
        assert diff["bank_name"] == ("strata_web_statement_manual", "Macquire Bank")
        assert diff["account_name"] == ("Old Name", "New Name")

    def test_no_changes_when_postgres_already_matches_mongo(self, script):
        mongo_acct = {"bank_name": "Macquire Bank", "account_name": "Same Name"}
        pg_current = {"bank_name": "Macquire Bank", "account_name": "Same Name"}

        changed_fields, diff = script._compute_account_diff(
            "sinking", "acct-2", mongo_acct, pg_current
        )

        assert changed_fields == {}
        # diff still reports both candidate fields (as unchanged pairs) for the dry-run log.
        assert diff["bank_name"] == ("Macquire Bank", "Macquire Bank")
        assert diff["account_name"] == ("Same Name", "Same Name")

    def test_only_one_field_differs(self, script):
        mongo_acct = {"bank_name": "Macquire Bank", "account_name": "Same Name"}
        pg_current = {"bank_name": "strata_web_statement_manual", "account_name": "Same Name"}

        changed_fields, _diff = script._compute_account_diff(
            "admin", "acct-1", mongo_acct, pg_current
        )

        assert changed_fields == {"bank_name": "Macquire Bank"}
        assert "account_name" not in changed_fields

    def test_falsy_mongo_bank_name_is_never_a_candidate(self, script):
        """Empty/missing Mongo bank_name must never blank out a Postgres value —
        candidate_fields only includes fields with a truthy Mongo value."""
        mongo_acct = {"bank_name": "", "account_name": "New Name"}
        pg_current = {"bank_name": "Existing Bank", "account_name": "Old Name"}

        changed_fields, diff = script._compute_account_diff(
            "admin", "acct-1", mongo_acct, pg_current
        )

        assert "bank_name" not in changed_fields
        assert "bank_name" not in diff
        assert changed_fields == {"account_name": "New Name"}

    def test_missing_mongo_account_name_key_is_never_a_candidate(self, script):
        mongo_acct = {"bank_name": "Macquire Bank"}  # no account_name key at all
        pg_current = {"bank_name": "strata_web_statement_manual", "account_name": "Old Name"}

        changed_fields, diff = script._compute_account_diff(
            "admin", "acct-1", mongo_acct, pg_current
        )

        assert changed_fields == {"bank_name": "Macquire Bank"}
        assert "account_name" not in diff

    def test_masked_bsb_and_account_number_are_never_touched(self, script):
        """Regression guard: masked_bsb/masked_account_number must never appear in
        changed_fields or diff even if present on the Mongo doc — Mongo's bsb is
        unmasked and copying it in would overwrite a correctly-masked Postgres value
        (see module docstring)."""
        mongo_acct = {
            "bank_name": "Macquire Bank",
            "account_name": "New Name",
            "bsb": "182-266",  # unmasked — must be ignored entirely
            "masked_bsb": "18X-XX6",
            "account_number": "123456789",
        }
        pg_current = {"bank_name": "strata_web_statement_manual", "account_name": "Old Name"}

        changed_fields, diff = script._compute_account_diff(
            "admin", "acct-1", mongo_acct, pg_current
        )

        assert "bsb" not in changed_fields and "bsb" not in diff
        assert "masked_bsb" not in changed_fields and "masked_bsb" not in diff
        assert "account_number" not in changed_fields and "account_number" not in diff
        assert "masked_account_number" not in changed_fields and "masked_account_number" not in diff
        assert set(changed_fields) <= {"bank_name", "account_name"}

    def test_trust_account_id_is_stringified_in_diff(self, script):
        """trust_account_id may be a UUID object from a SQLAlchemy row — the diff
        must stringify it (used directly in the audit_events JSON payload)."""
        import uuid

        account_id = uuid.uuid4()
        mongo_acct = {"bank_name": "Macquire Bank"}
        pg_current = {"bank_name": "strata_web_statement_manual", "account_name": "X"}

        _changed_fields, diff = script._compute_account_diff(
            "admin", account_id, mongo_acct, pg_current
        )

        assert diff["trust_account_id"] == str(account_id)
        assert isinstance(diff["trust_account_id"], str)


class TestFundKeyMap:
    def test_admin_fund_maps_to_admin(self, script):
        assert script.FUND_KEY_MAP["admin_fund"] == "admin"

    def test_sinking_fund_maps_to_sinking(self, script):
        assert script.FUND_KEY_MAP["sinking_fund"] == "sinking"

    def test_capital_works_fund_also_maps_to_sinking(self, script):
        """Mongo sometimes labels the sinking account 'capital_works_fund' — both
        labels must resolve to the same Postgres fund_type key ('sinking')."""
        assert script.FUND_KEY_MAP["capital_works_fund"] == "sinking"


class TestScriptDoesNotRunMainOnImport:
    def test_importing_module_does_not_touch_any_database(self, script):
        """Guards against a regression where top-level code creates a Mongo/Postgres
        client at import time instead of inside main() — this test file must never
        need real DATABASE_URL/MONGO_URL credentials to run."""
        assert hasattr(script, "main")
        assert callable(script.main)
        # BUILDING_ID must remain the documented one-off scope constant.
        assert script.BUILDING_ID == "13195"
