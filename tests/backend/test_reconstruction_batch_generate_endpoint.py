"""Tests for POST .../reconstruction-batches/{batch_id}/generate in routers/financial_onboarding.py.

The endpoint calls integrations.demo_bank.ingestion.import_historical_reconstruction(),
whose installed implementation dereferences `db._db.demo_bank_transactions`. These tests lock the
router to the wrapped TenantScopedDatabase object from database.py rather than the raw AsyncDatabase
helper it uses for its own direct Mongo collection access.

Also covers the post-hoc synthetic-transaction labeling step added afterward: every
materialised row must be stamped is_synthetic/basis/actual_payment_confirmed/
excluded_from_cash_reconciliation, and the batch must NOT be marked "generated" if that
labeling write doesn't verifiably reach every row.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.financial_onboarding import router
from utils.auth import get_current_building, get_current_user

BUILDING_ID = "16244"
BATCH_ID = "batch-1"


def _user(role: str = "strata_admin") -> dict:
    return {"id": "user-1", "full_name": "Test User", "role": role, "effective_role": role}


def _build_app(user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_building] = lambda: BUILDING_ID
    return TestClient(app)


class _FakeBatch:
    """Stand-in for a ReconstructionBatch already in status=approved."""

    def __init__(self):
        self.batch_id = BATCH_ID
        self.building_id = BUILDING_ID
        self.status = "approved"
        self.reconstruction_method = "generate_from_budget_v1"
        self.is_test_data = False

    def model_dump(self, mode="json"):
        return {"batch_id": BATCH_ID, "status": "generated"}


def _patched_generate(*, imported_count: int, labeling_matched_count: int):
    """Common patch set for exercising generate_reconstruction_batch with a controllable
    imported_count (from import_historical_reconstruction) and labeling_matched_count (from
    the post-hoc update_many stamping step) — the two numbers this endpoint compares."""
    fake_manifest_doc = {"batch_id": BATCH_ID, "building_id": BUILDING_ID, "manifest_id": "m1"}
    fake_manifest = MagicMock()

    fake_mongo_db = MagicMock()
    fake_mongo_db.demo_bank_reconstruction_manifests.find_one = AsyncMock(return_value=fake_manifest_doc)
    fake_mongo_db.demo_bank_reconstruction_batches.update_one = AsyncMock()

    import_mock = AsyncMock(return_value={
        "batch_id": BATCH_ID, "import_status": "completed", "imported_count": imported_count,
        "skipped_count": 0, "error_count": 0, "duplicate_batch": False, "message": "ok",
    })

    labeling_result = MagicMock()
    labeling_result.matched_count = labeling_matched_count
    fake_wrapped_db = MagicMock()
    fake_wrapped_db._db.demo_bank_transactions.update_many = AsyncMock(return_value=labeling_result)

    return fake_mongo_db, import_mock, fake_wrapped_db


class TestGenerateEndpointPassesWrappedDb:
    def test_import_historical_reconstruction_receives_wrapped_db_not_raw_mongo_db(self):
        """The core regression guard: import_historical_reconstruction's first positional
        arg must be the module-level `db` object (routers.financial_onboarding.db), never
        the `_mongo(building_id)` result — that mismatch is exactly what caused the live
        failure (0 imported, 100% error_count) despite the batch being marked "generated"."""
        client = _build_app(_user("strata_admin"))
        fake_mongo_db, import_mock, fake_wrapped_db = _patched_generate(imported_count=5, labeling_matched_count=5)
        fake_manifest = MagicMock()

        with patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True)), \
             patch("routers.financial_onboarding._assert_building_access", new=AsyncMock(return_value=BUILDING_ID)), \
             patch("routers.financial_onboarding._mongo", return_value=fake_mongo_db), \
             patch("routers.financial_onboarding.db", fake_wrapped_db), \
             patch("services.reconstruction_batch_service._get_batch_doc", new=AsyncMock(return_value={"status": "approved"})), \
             patch("services.reconstruction_batch_service._doc_to_batch", return_value=_FakeBatch()), \
             patch("services.reconstruction_batch_service._transition", new=AsyncMock()), \
             patch("services.reconstruction_batch_service.mark_generated", new=AsyncMock(return_value=_FakeBatch())), \
             patch("integrations.demo_bank.reconstruction_batch_schemas.ReconstructionManifest", return_value=fake_manifest), \
             patch("services.reconstruction_generators.budget_levy_generator.ensure_generation_account", new=AsyncMock(return_value="OPERATING-16244")), \
             patch("integrations.demo_bank.ingestion.import_historical_reconstruction", import_mock), \
             patch("routers.financial_onboarding.create_audit_log", new=AsyncMock()):
            resp = client.post(
                f"/api/financials/onboarding/building/{BUILDING_ID}/reconstruction-batches/{BATCH_ID}/generate",
            )

        assert resp.status_code == 200
        import_mock.assert_awaited_once()
        called_db_arg = import_mock.call_args[0][0]
        assert called_db_arg is fake_wrapped_db, (
            "import_historical_reconstruction must receive the wrapped db object, never the "
            "raw _mongo(building_id) result — that mismatch is exactly what caused every row "
            "to error out in production."
        )


class TestSyntheticLabelingReliability:
    def test_labeling_call_targets_correct_filter_and_field_values(self):
        client = _build_app(_user("strata_admin"))
        fake_mongo_db, import_mock, fake_wrapped_db = _patched_generate(imported_count=3, labeling_matched_count=3)
        fake_manifest = MagicMock()

        with patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True)), \
             patch("routers.financial_onboarding._assert_building_access", new=AsyncMock(return_value=BUILDING_ID)), \
             patch("routers.financial_onboarding._mongo", return_value=fake_mongo_db), \
             patch("routers.financial_onboarding.db", fake_wrapped_db), \
             patch("services.reconstruction_batch_service._get_batch_doc", new=AsyncMock(return_value={"status": "approved"})), \
             patch("services.reconstruction_batch_service._doc_to_batch", return_value=_FakeBatch()), \
             patch("services.reconstruction_batch_service._transition", new=AsyncMock()), \
             patch("services.reconstruction_batch_service.mark_generated", new=AsyncMock(return_value=_FakeBatch())), \
             patch("integrations.demo_bank.reconstruction_batch_schemas.ReconstructionManifest", return_value=fake_manifest), \
             patch("services.reconstruction_generators.budget_levy_generator.ensure_generation_account", new=AsyncMock(return_value="OPERATING-16244")), \
             patch("integrations.demo_bank.ingestion.import_historical_reconstruction", import_mock), \
             patch("routers.financial_onboarding.create_audit_log", new=AsyncMock()):
            resp = client.post(
                f"/api/financials/onboarding/building/{BUILDING_ID}/reconstruction-batches/{BATCH_ID}/generate",
            )

        assert resp.status_code == 200
        fake_wrapped_db._db.demo_bank_transactions.update_many.assert_awaited_once()
        call_args = fake_wrapped_db._db.demo_bank_transactions.update_many.call_args
        filter_arg, update_arg = call_args[0]
        assert filter_arg == {
            "building_id": BUILDING_ID,
            "$or": [
                {"source_batch_id": BATCH_ID},
                {"reconstruction_batch_id": BATCH_ID},
            ],
        }
        set_fields = update_arg["$set"]
        assert set_fields["is_synthetic"] is True
        assert set_fields["basis"] == "synthetic"
        assert set_fields["actual_payment_confirmed"] is False
        assert set_fields["excluded_from_cash_reconciliation"] is True

    def test_matched_count_mismatch_blocks_mark_generated_and_returns_409(self):
        """The reliability guard: if the labeling update doesn't reach every imported row, the
        batch must NOT be marked generated — it stays at generation_ready, which the existing
        state machine already refuses to let sync/review/approve move past."""
        client = _build_app(_user("strata_admin"))
        fake_mongo_db, import_mock, fake_wrapped_db = _patched_generate(imported_count=10, labeling_matched_count=7)
        fake_manifest = MagicMock()
        mark_generated_mock = AsyncMock(return_value=_FakeBatch())

        with patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True)), \
             patch("routers.financial_onboarding._assert_building_access", new=AsyncMock(return_value=BUILDING_ID)), \
             patch("routers.financial_onboarding._mongo", return_value=fake_mongo_db), \
             patch("routers.financial_onboarding.db", fake_wrapped_db), \
             patch("services.reconstruction_batch_service._get_batch_doc", new=AsyncMock(return_value={"status": "approved"})), \
             patch("services.reconstruction_batch_service._doc_to_batch", return_value=_FakeBatch()), \
             patch("services.reconstruction_batch_service._transition", new=AsyncMock()), \
             patch("services.reconstruction_batch_service.mark_generated", mark_generated_mock), \
             patch("integrations.demo_bank.reconstruction_batch_schemas.ReconstructionManifest", return_value=fake_manifest), \
             patch("services.reconstruction_generators.budget_levy_generator.ensure_generation_account", new=AsyncMock(return_value="OPERATING-16244")), \
             patch("integrations.demo_bank.ingestion.import_historical_reconstruction", import_mock), \
             patch("routers.financial_onboarding.create_audit_log", new=AsyncMock()):
            resp = client.post(
                f"/api/financials/onboarding/building/{BUILDING_ID}/reconstruction-batches/{BATCH_ID}/generate",
            )

        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "LABELING_INCOMPLETE"
        mark_generated_mock.assert_not_called()
        # A diagnostic labeling_verified=False record was written for operator visibility.
        fake_mongo_db.demo_bank_reconstruction_batches.update_one.assert_awaited_once()
        diag_filter, diag_update = fake_mongo_db.demo_bank_reconstruction_batches.update_one.call_args[0]
        assert diag_update["$set"]["labeling_verified"] is False

    def test_matched_count_equal_marks_generated_and_stamps_labeling_verified_true(self):
        client = _build_app(_user("strata_admin"))
        fake_mongo_db, import_mock, fake_wrapped_db = _patched_generate(imported_count=4, labeling_matched_count=4)
        fake_manifest = MagicMock()
        mark_generated_mock = AsyncMock(return_value=_FakeBatch())

        with patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True)), \
             patch("routers.financial_onboarding._assert_building_access", new=AsyncMock(return_value=BUILDING_ID)), \
             patch("routers.financial_onboarding._mongo", return_value=fake_mongo_db), \
             patch("routers.financial_onboarding.db", fake_wrapped_db), \
             patch("services.reconstruction_batch_service._get_batch_doc", new=AsyncMock(return_value={"status": "approved"})), \
             patch("services.reconstruction_batch_service._doc_to_batch", return_value=_FakeBatch()), \
             patch("services.reconstruction_batch_service._transition", new=AsyncMock()), \
             patch("services.reconstruction_batch_service.mark_generated", mark_generated_mock), \
             patch("integrations.demo_bank.reconstruction_batch_schemas.ReconstructionManifest", return_value=fake_manifest), \
             patch("services.reconstruction_generators.budget_levy_generator.ensure_generation_account", new=AsyncMock(return_value="OPERATING-16244")), \
             patch("integrations.demo_bank.ingestion.import_historical_reconstruction", import_mock), \
             patch("routers.financial_onboarding.create_audit_log", new=AsyncMock()):
            resp = client.post(
                f"/api/financials/onboarding/building/{BUILDING_ID}/reconstruction-batches/{BATCH_ID}/generate",
            )

        assert resp.status_code == 200
        mark_generated_mock.assert_awaited_once()
        diag_filter, diag_update = fake_mongo_db.demo_bank_reconstruction_batches.update_one.call_args[0]
        assert diag_update["$set"]["labeling_verified"] is True

    def test_zero_imported_count_never_blocks_on_labeling(self):
        """A manifest with zero transactions materialised (nothing to label) must not be
        treated as a labeling failure — 0 == 0 is a match, not a mismatch."""
        client = _build_app(_user("strata_admin"))
        fake_mongo_db, import_mock, fake_wrapped_db = _patched_generate(imported_count=0, labeling_matched_count=0)
        fake_manifest = MagicMock()
        mark_generated_mock = AsyncMock(return_value=_FakeBatch())

        with patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True)), \
             patch("routers.financial_onboarding._assert_building_access", new=AsyncMock(return_value=BUILDING_ID)), \
             patch("routers.financial_onboarding._mongo", return_value=fake_mongo_db), \
             patch("routers.financial_onboarding.db", fake_wrapped_db), \
             patch("services.reconstruction_batch_service._get_batch_doc", new=AsyncMock(return_value={"status": "approved"})), \
             patch("services.reconstruction_batch_service._doc_to_batch", return_value=_FakeBatch()), \
             patch("services.reconstruction_batch_service._transition", new=AsyncMock()), \
             patch("services.reconstruction_batch_service.mark_generated", mark_generated_mock), \
             patch("integrations.demo_bank.reconstruction_batch_schemas.ReconstructionManifest", return_value=fake_manifest), \
             patch("services.reconstruction_generators.budget_levy_generator.ensure_generation_account", new=AsyncMock(return_value="OPERATING-16244")), \
             patch("integrations.demo_bank.ingestion.import_historical_reconstruction", import_mock), \
             patch("routers.financial_onboarding.create_audit_log", new=AsyncMock()):
            resp = client.post(
                f"/api/financials/onboarding/building/{BUILDING_ID}/reconstruction-batches/{BATCH_ID}/generate",
            )

        assert resp.status_code == 200
        mark_generated_mock.assert_awaited_once()
