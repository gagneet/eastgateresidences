"""
Tests for scripts/data_cleanup/remove_legacy_demo_buildings.py

All async operations are driven via asyncio.run() inside synchronous test
functions — avoids pytest-asyncio session-loop issues with mocked Motor clients.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts" / "data_cleanup"))

TARGET_BIDS = {"16244", "18932"}
PROTECTED_BID = "13195"

# Minimal fake TENANT_SCOPED_COLLECTIONS for tests
_FAKE_COLLECTIONS = {
    "units", "annual_levies", "announcements", "levy_payments",
    "levy_plans", "notifications", "work_orders", "documents",
}


# ---------------------------------------------------------------------------
# Mock Motor helpers
# ---------------------------------------------------------------------------

def _mock_cursor(docs: list) -> MagicMock:
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=docs)
    return cursor


def _mock_collection(count: int = 0, docs: list | None = None) -> MagicMock:
    col = MagicMock()
    col.count_documents = AsyncMock(return_value=count)
    col.delete_many = AsyncMock(return_value=MagicMock(deleted_count=count))
    col.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    col.find = MagicMock(return_value=_mock_cursor(docs or []))
    return col


def _make_mock_db(
    units_east_gate: int = 87,
    user_docs: list | None = None,
) -> MagicMock:
    db = MagicMock()

    async def _count_units(query):
        bid = query.get("building_id", "")
        if bid == PROTECTED_BID:
            return units_east_gate
        if bid in TARGET_BIDS:
            return 10
        return 0

    units_col = MagicMock()
    units_col.count_documents = AsyncMock(side_effect=_count_units)
    units_col.delete_many = AsyncMock(return_value=MagicMock(deleted_count=10))
    units_col.delete_one = AsyncMock()
    units_col.update_one = AsyncMock()
    units_col.find = MagicMock(return_value=_mock_cursor([]))

    users_col = _mock_collection(count=len(user_docs or []), docs=user_docs or [])

    def _getitem(name):
        if name == "units":
            return units_col
        if name == "users":
            return users_col
        return _mock_collection(count=3)

    db.__getitem__ = MagicMock(side_effect=_getitem)
    return db


# ---------------------------------------------------------------------------
# Load module under test (patching backend imports at load time)
# ---------------------------------------------------------------------------

import importlib.util as _iutil

def _load_mod():
    spec = _iutil.spec_from_file_location(
        "remove_legacy_demo_buildings",
        ROOT / "scripts" / "data_cleanup" / "remove_legacy_demo_buildings.py",
    )
    m = _iutil.module_from_spec(spec)
    with patch.dict("sys.modules", {
        "motor": MagicMock(),
        "motor.motor_asyncio": MagicMock(),
        "config": MagicMock(settings=MagicMock(MONGO_URL="mongodb://localhost", DB_NAME="test")),
        "database": MagicMock(TENANT_SCOPED_COLLECTIONS=_FAKE_COLLECTIONS),
        "asyncpg": MagicMock(),
    }):
        spec.loader.exec_module(m)
    return m


mod = _load_mod()

# Convenience: run an async coroutine in the test
_run = asyncio.run


# ---------------------------------------------------------------------------
# Dry-run produces no mutations
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_does_not_call_delete_many(self):
        db = _make_mock_db()

        async def _inner():
            with patch.object(mod, "_get_mongo_db", AsyncMock(return_value=db)), \
                 patch.object(mod, "_get_pg_conn", AsyncMock(side_effect=Exception("no pg"))):
                await mod.run_cleanup(
                    dry_run=True, resume_from=None,
                    _tenant_scoped_collections=_FAKE_COLLECTIONS,
                )

        _run(_inner())
        # delete_many must not have been called on any collection
        for coll_name in _FAKE_COLLECTIONS:
            col = db[coll_name]
            assert col.delete_many.call_count == 0, (
                f"delete_many called on {coll_name} during dry-run"
            )

    def test_dry_run_does_not_call_delete_one(self):
        db = _make_mock_db()

        async def _inner():
            with patch.object(mod, "_get_mongo_db", AsyncMock(return_value=db)), \
                 patch.object(mod, "_get_pg_conn", AsyncMock(side_effect=Exception("no pg"))):
                await mod.run_cleanup(
                    dry_run=True, resume_from=None,
                    _tenant_scoped_collections=_FAKE_COLLECTIONS,
                )

        _run(_inner())
        assert db["users"].delete_one.call_count == 0


# ---------------------------------------------------------------------------
# East Gate protection
# ---------------------------------------------------------------------------

class TestEastGateProtection:
    def test_protected_building_id_constant(self):
        assert mod.PROTECTED_BUILDING_ID == "13195"

    def test_target_building_ids_do_not_include_east_gate(self):
        assert "13195" not in mod.TARGET_BUILDING_IDS

    def test_verify_east_gate_intact_passes_with_87_units(self):
        db = _make_mock_db(units_east_gate=87)

        async def _inner():
            return await mod._verify_east_gate_intact(db)

        result = _run(_inner())
        assert result is True

    def test_verify_east_gate_intact_fails_with_low_count(self):
        db = _make_mock_db(units_east_gate=5)

        async def _inner():
            return await mod._verify_east_gate_intact(db)

        result = _run(_inner())
        assert result is False

    def test_cleanup_aborts_if_east_gate_check_fails(self):
        db = _make_mock_db(units_east_gate=5)

        async def _inner():
            with patch.object(mod, "_get_mongo_db", AsyncMock(return_value=db)):
                await mod.run_cleanup(
                    dry_run=True, resume_from=None,
                    _tenant_scoped_collections=_FAKE_COLLECTIONS,
                )

        with pytest.raises(SystemExit):
            _run(_inner())


# ---------------------------------------------------------------------------
# Collection enumeration
# ---------------------------------------------------------------------------

class TestCollectionEnumeration:
    def test_target_building_ids_match_expected(self):
        assert mod.TARGET_BUILDING_IDS == {"16244", "18932"}

    def test_all_fake_collections_queried_in_dry_run(self):
        queried = set()
        db = _make_mock_db()
        original = db.__getitem__.side_effect

        def tracking(name):
            queried.add(name)
            return original(name)

        db.__getitem__ = MagicMock(side_effect=tracking)

        async def _inner():
            with patch.object(mod, "_get_mongo_db", AsyncMock(return_value=db)), \
                 patch.object(mod, "_get_pg_conn", AsyncMock(side_effect=Exception("no pg"))):
                await mod.run_cleanup(
                    dry_run=True, resume_from=None,
                    _tenant_scoped_collections=_FAKE_COLLECTIONS,
                )

        _run(_inner())
        for coll in _FAKE_COLLECTIONS:
            assert coll in queried, f"'{coll}' was not queried during dry-run"


# ---------------------------------------------------------------------------
# Global user handling
# ---------------------------------------------------------------------------

class TestGlobalUserHandling:
    def test_sole_target_user_is_deleted(self):
        sole = {
            "_id": "u001", "email": "sole@sierra.test",
            "building_id": "16244", "memberships": [],
        }
        db = _make_mock_db(user_docs=[sole])

        async def _inner():
            return await mod._handle_global_users(db, TARGET_BIDS, dry_run=False)

        stats = _run(_inner())
        assert stats["deleted"] == 1
        assert stats["membership_removed"] == 0
        db["users"].delete_one.assert_called_once()

    def test_multi_building_user_gets_membership_stripped(self):
        multi = {
            "_id": "u002", "email": "multi@example.test",
            "building_id": "16244",
            "memberships": [
                {"building_id": "16244", "role": "owner"},
                {"building_id": "99999", "role": "owner"},
            ],
        }
        db = _make_mock_db(user_docs=[multi])

        async def _inner():
            return await mod._handle_global_users(db, TARGET_BIDS, dry_run=False)

        stats = _run(_inner())
        assert stats["deleted"] == 0
        assert stats["membership_removed"] == 1
        db["users"].delete_one.assert_not_called()
        db["users"].update_one.assert_called_once()
        surviving = db["users"].update_one.call_args[0][1]["$set"]["memberships"]
        assert all(m["building_id"] != "16244" for m in surviving)

    def test_dry_run_does_not_mutate_users(self):
        sole = {
            "_id": "u003", "email": "t@sierra.test",
            "building_id": "16244", "memberships": [],
        }
        db = _make_mock_db(user_docs=[sole])

        async def _inner():
            await mod._handle_global_users(db, TARGET_BIDS, dry_run=True)

        _run(_inner())
        db["users"].delete_one.assert_not_called()
        db["users"].update_one.assert_not_called()

    def test_unrelated_user_is_skipped(self):
        unrelated = {
            "_id": "u004", "email": "east@eastgate.test",
            "building_id": "13195",
            "memberships": [{"building_id": "13195", "role": "owner"}],
        }
        db = _make_mock_db(user_docs=[unrelated])

        async def _inner():
            return await mod._handle_global_users(db, TARGET_BIDS, dry_run=False)

        stats = _run(_inner())
        assert stats["deleted"] == 0
        assert stats["membership_removed"] == 0
        assert stats["skipped"] == 1


# ---------------------------------------------------------------------------
# Resume-from flag
# ---------------------------------------------------------------------------

class TestResumeFrom:
    def test_resume_from_skips_earlier_collections(self):
        processed = []
        db = _make_mock_db()

        async def _tracking_count(db_, coll, bids):
            processed.append(coll)
            return 0

        async def _inner():
            with patch.object(mod, "_get_mongo_db", AsyncMock(return_value=db)), \
                 patch.object(mod, "_get_pg_conn", AsyncMock(side_effect=Exception("no pg"))), \
                 patch.object(mod, "_count_mongo_collection", _tracking_count):
                await mod.run_cleanup(
                    dry_run=True,
                    resume_from="levy_payments",
                    _tenant_scoped_collections=_FAKE_COLLECTIONS,
                )

        _run(_inner())
        sorted_colls = sorted(_FAKE_COLLECTIONS)
        resume_idx = sorted_colls.index("levy_payments")
        for coll in sorted_colls[:resume_idx]:
            assert coll not in processed, f"'{coll}' should have been skipped"
        assert "levy_payments" in processed


# ---------------------------------------------------------------------------
# Confirmation guard (sync — no async needed)
# ---------------------------------------------------------------------------

class TestConfirmationGuard:
    def test_wrong_input_returns_false(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda: "16244")
        assert mod._prompt_confirmation() is False

    def test_correct_input_returns_true(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda: "16244 18932")
        assert mod._prompt_confirmation() is True

    def test_reversed_order_also_accepted(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda: "18932 16244")
        assert mod._prompt_confirmation() is True

    def test_extra_ids_rejected(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda: "16244 18932 13195")
        assert mod._prompt_confirmation() is False


# ---------------------------------------------------------------------------
# Post-cleanup verification
# ---------------------------------------------------------------------------

class TestPostCleanupVerification:
    def test_target_count_checked_after_live_run(self):
        """After --confirm, count_documents is called to verify targets are gone."""
        db = _make_mock_db()
        count_calls = []

        async def _tracking_count(query):
            # Handle both {"building_id": x} and {"$or": [{"building_id": x}, ...]}
            bid = query.get("building_id", "")
            if not bid and "$or" in query:
                bid = query["$or"][0].get("building_id", "")
            count_calls.append(bid)
            if bid == PROTECTED_BID:
                return 87
            return 0

        db["units"].count_documents = AsyncMock(side_effect=_tracking_count)

        async def _patched_delete(db_, coll, bids):
            return 3

        async def _inner():
            with patch.object(mod, "_get_mongo_db", AsyncMock(return_value=db)), \
                 patch.object(mod, "_get_pg_conn", AsyncMock(side_effect=Exception("no pg"))), \
                 patch.object(mod, "_delete_mongo_collection", _patched_delete), \
                 patch.object(mod, "_handle_global_users", AsyncMock(return_value={"deleted": 0, "membership_removed": 0, "skipped": 0})), \
                 patch.object(mod, "_handle_global_buildings", AsyncMock(return_value=0)), \
                 patch.object(mod, "_handle_global_feature_toggles", AsyncMock(return_value=0)), \
                 patch.object(mod, "_handle_global_memberships", AsyncMock(return_value=0)), \
                 patch.object(mod, "_handle_global_navigation_configs", AsyncMock(return_value=0)), \
                 patch.object(mod, "_handle_global_building_invitations", AsyncMock(return_value=0)), \
                 patch.object(mod, "_handle_user_feature_access", AsyncMock(return_value=0)):
                await mod.run_cleanup(
                    dry_run=False, resume_from=None,
                    _tenant_scoped_collections=_FAKE_COLLECTIONS,
                )

        _run(_inner())
        # Verification step calls count_documents for each target building
        assert any(bid in count_calls for bid in TARGET_BIDS), (
            "Post-cleanup verification did not check target building counts"
        )
