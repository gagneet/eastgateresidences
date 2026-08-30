"""Tests for single-intake enforcement (GAP-FIN-037):
- services/single_intake_service.py — the Demo Bank staging helper + enforcement gate.
- routers/reconciliation.py::import_deft_csv — DEFT/BPAY CSV routes into Demo Bank when the
  disable_strata_sync_direct_write toggle is ON, and direct-writes to levy_payments when OFF.

All DB access is mocked; no live database required.
"""
from __future__ import annotations

from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.datastructures import UploadFile

from services.single_intake_service import (
    build_import_idempotency_key,
    is_single_intake_enforced,
    stage_cash_receipt_into_demo_bank,
)

BUILDING_ID = "16244"


class TestIdempotencyKey:
    def test_deterministic_and_prefixed(self):
        args = dict(
            building_id=BUILDING_ID, reference="DEFT001", posted_date="2026-01-15",
            amount_cents=11000, unit_number="UA067",
        )
        k1 = build_import_idempotency_key(**args)
        k2 = build_import_idempotency_key(**args)
        assert k1 == k2
        assert k1.startswith("import-")

    def test_differs_on_amount(self):
        base = dict(
            building_id=BUILDING_ID, reference="DEFT001", posted_date="2026-01-15",
            unit_number="UA067",
        )
        assert build_import_idempotency_key(amount_cents=11000, **base) != \
            build_import_idempotency_key(amount_cents=11001, **base)


class TestIsSingleIntakeEnforced:
    @pytest.mark.asyncio
    async def test_true_when_toggle_on(self):
        with patch("db_postgres.repos.config_repo.resolve_feature_toggle",
                   new=AsyncMock(return_value=True)) as m:
            assert await is_single_intake_enforced(BUILDING_ID) is True
        # Must resolve the pure building-level toggle with default False (not the access helper).
        _, kwargs = m.call_args
        assert kwargs.get("default") is False
        assert m.call_args[0][1] == "disable_strata_sync_direct_write"

    @pytest.mark.asyncio
    async def test_false_when_toggle_off_or_absent(self):
        with patch("db_postgres.repos.config_repo.resolve_feature_toggle",
                   new=AsyncMock(return_value=False)):
            assert await is_single_intake_enforced(BUILDING_ID) is False


class TestStageCashReceipt:
    @pytest.mark.asyncio
    async def test_stages_correct_demo_bank_doc(self):
        mongo_db = MagicMock()
        upsert_result = MagicMock(upserted_id="new-id")
        mongo_db.demo_bank_transactions.update_one = AsyncMock(return_value=upsert_result)

        inserted, idem = await stage_cash_receipt_into_demo_bank(
            mongo_db,
            building_id=BUILDING_ID, unit_number="UA067", amount_cents=11000,
            posted_date="2026-01-15", reference="DEFT001", description="DEFT payment",
            source_type="deft_settlement_import", payment_method="deft", year="2026",
            is_test_data=True,
        )
        assert inserted is True
        assert idem.startswith("import-")

        filt, update = mongo_db.demo_bank_transactions.update_one.call_args[0]
        assert filt == {"building_id": BUILDING_ID, "idempotency_key": idem}
        doc = update["$setOnInsert"]
        assert doc["amount_cents"] == 11000 and isinstance(doc["amount_cents"], int)
        assert doc["direction"] == "credit"
        assert doc["provenance_class"] == "external_import"
        assert doc["source_type"] == "deft_settlement_import"
        assert doc["is_synthetic"] is False
        assert doc["sync_status"] == "pending"
        assert doc["is_test_data"] is True
        assert doc["building_id"] == BUILDING_ID
        # upsert=True so re-import is idempotent, not a double-insert.
        assert mongo_db.demo_bank_transactions.update_one.call_args[1]["upsert"] is True

    @pytest.mark.asyncio
    async def test_duplicate_returns_not_inserted(self):
        mongo_db = MagicMock()
        mongo_db.demo_bank_transactions.update_one = AsyncMock(
            return_value=MagicMock(upserted_id=None)
        )
        inserted, _ = await stage_cash_receipt_into_demo_bank(
            mongo_db, building_id=BUILDING_ID, unit_number="UA067", amount_cents=11000,
            posted_date="2026-01-15", reference="DEFT001", description="x",
            source_type="deft_settlement_import",
        )
        assert inserted is False


_CSV = (
    "Date,Reference,Description,Amount,UnitNumber\n"
    "2026-01-15,DEFTREF001,DEFT payment,110.00,UA067\n"
    "2026-01-16,DEFTREF002,BPAY payment,220.00,TH087\n"
)

_R = "routers.reconciliation"
_SVC = "services.single_intake_service"


def _upload():
    return UploadFile(BytesIO(_CSV.encode("utf-8")), filename="deft.csv")


def _base_patches(monkeypatch):
    monkeypatch.setattr(f"{_R}.get_general_settings_or_default",
                        AsyncMock(return_value={"plan_number": "13195"}))
    monkeypatch.setattr(f"{_R}.scan_upload", AsyncMock())
    monkeypatch.setattr(f"{_R}.create_audit_log", AsyncMock())


class TestDeftImportEnforcement:
    @pytest.mark.asyncio
    async def test_enforced_routes_into_demo_bank_no_direct_write(self, monkeypatch):
        from routers.reconciliation import import_deft_csv

        _base_patches(monkeypatch)
        monkeypatch.setattr(f"{_SVC}.is_single_intake_enforced", AsyncMock(return_value=True))
        stage = AsyncMock(return_value=(True, "import-key"))
        monkeypatch.setattr(f"{_SVC}.stage_cash_receipt_into_demo_bank", stage)
        # Direct-write collection must NEVER be touched on the enforced path.
        mock_db = MagicMock()
        mock_db.levy_payments.insert_one = AsyncMock()
        mock_db._db = MagicMock()
        monkeypatch.setattr(f"{_R}.db", mock_db)

        result = await import_deft_csv(
            year="2026", file=_upload(),
            current_user={"id": "u1", "full_name": "Op"}, building_id=BUILDING_ID,
        )

        assert result.routed_to_demo_bank is True
        assert result.staged_count == 2
        assert result.imported_count == 0
        assert stage.await_count == 2
        mock_db.levy_payments.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_enforced_counts_duplicate_when_already_staged(self, monkeypatch):
        from routers.reconciliation import import_deft_csv

        _base_patches(monkeypatch)
        monkeypatch.setattr(f"{_SVC}.is_single_intake_enforced", AsyncMock(return_value=True))
        # First row new, second row already-staged (duplicate).
        monkeypatch.setattr(
            f"{_SVC}.stage_cash_receipt_into_demo_bank",
            AsyncMock(side_effect=[(True, "k1"), (False, "k2")]),
        )
        mock_db = MagicMock()
        mock_db._db = MagicMock()
        monkeypatch.setattr(f"{_R}.db", mock_db)

        result = await import_deft_csv(
            year="2026", file=_upload(),
            current_user={"id": "u1", "full_name": "Op"}, building_id=BUILDING_ID,
        )
        assert result.staged_count == 1
        assert result.duplicate_count == 1

    @pytest.mark.asyncio
    async def test_disabled_uses_direct_write_path(self, monkeypatch):
        from routers.reconciliation import import_deft_csv

        _base_patches(monkeypatch)
        monkeypatch.setattr(f"{_SVC}.is_single_intake_enforced", AsyncMock(return_value=False))
        stage = AsyncMock()
        monkeypatch.setattr(f"{_SVC}.stage_cash_receipt_into_demo_bank", stage)

        mock_db = MagicMock()
        mock_db.levy_payments.find_one = AsyncMock(return_value=None)  # no duplicates
        mock_db.levy_payments.insert_one = AsyncMock()
        monkeypatch.setattr(f"{_R}.db", mock_db)
        monkeypatch.setattr("routers.finance._upsert_ledger_for_payment", AsyncMock())

        result = await import_deft_csv(
            year="2026", file=_upload(),
            current_user={"id": "u1", "full_name": "Op"}, building_id=BUILDING_ID,
        )

        assert result.routed_to_demo_bank is False
        assert result.imported_count == 2
        assert result.staged_count == 0
        assert mock_db.levy_payments.insert_one.await_count == 2
        stage.assert_not_called()
