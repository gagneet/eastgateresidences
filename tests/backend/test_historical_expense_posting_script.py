"""Unit tests for scripts/historical_expense_posting.py.

run_apply()'s dual-control/status/integrity guard clauses all execute BEFORE any Postgres
session is opened, so they're testable by patching only the Mongo client + the two batch/
manifest lookup helpers — no real database connection required. _extract_category_name() is a
pure function, tested directly against the exact description format
expense_category_generator.py produces.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.historical_expense_posting import _extract_category_name, run_apply

_MODULE = "scripts.historical_expense_posting"

BUILDING_ID = "16244"
BATCH_ID = str(uuid.uuid4())
APPROVED_BY = uuid.uuid4()
EXECUTED_BY = uuid.uuid4()


def _debit_row(**overrides) -> dict:
    defaults = dict(
        account_ref="ADMIN-16244", unit_number="", financial_year="2024", quarter=None,
        fund_type="admin", levy_component="ordinary", posted_date="2024-12-31",
        amount_cents=11000, amount_ex_gst_cents=10000, gst_cents=1000, direction="debit",
        assumption_code="actual_category_spend",
        description=(
            "Estimated 2024 spend summary: Cleaning (Admin Fund) — reported ANNUAL TOTAL, "
            "not a single dated transaction; posted_date is a summary effective date, not a "
            "real invoice/payment date."
        ),
        transaction_sequence=1, payment_group_id=None,
    )
    defaults.update(overrides)
    return defaults


def _credit_row(**overrides) -> dict:
    row = _debit_row(**overrides)
    row["direction"] = "credit"
    return row


class TestExtractCategoryName:
    def test_extracts_admin_fund_category(self):
        desc = (
            "Estimated 2023 spend summary: Cleaning (Admin Fund) — reported ANNUAL TOTAL, "
            "not a single dated transaction; posted_date is a summary effective date, not a "
            "real invoice/payment date."
        )
        assert _extract_category_name(desc) == "Cleaning"

    def test_extracts_sinking_fund_category(self):
        desc = "Estimated 2025 spend summary: Roof Repairs (Sinking Fund) — reported ANNUAL TOTAL."
        assert _extract_category_name(desc) == "Roof Repairs"

    def test_category_name_containing_fund_keyword_still_extracts_correctly(self):
        desc = "Estimated 2025 spend summary: Sinking Fund Contribution (Sinking Fund) — reported ANNUAL TOTAL."
        assert _extract_category_name(desc) == "Sinking Fund Contribution"

    def test_unparseable_description_falls_back_to_full_text(self):
        assert _extract_category_name("some other free text") == "some other free text"

    def test_empty_description_falls_back_to_unspecified(self):
        assert _extract_category_name("") == "Unspecified category"


def _mock_mongo(monkeypatch, batch_doc, manifest_doc=None):
    mongo_db = MagicMock()
    mock_client = MagicMock()
    mock_client.__getitem__.return_value = mongo_db
    monkeypatch.setattr(f"{_MODULE}.AsyncIOMotorClient", lambda *a, **k: mock_client)
    monkeypatch.setattr(f"{_MODULE}._mongo_url_and_db", lambda: ("mongodb://fake", "fake_db"))
    monkeypatch.setattr(f"{_MODULE}._get_batch_doc", AsyncMock(return_value=batch_doc))
    if manifest_doc is not None:
        monkeypatch.setattr(f"{_MODULE}._get_latest_manifest_doc", AsyncMock(return_value=manifest_doc))
    return mongo_db


class TestRunApplyGuardClauses:
    @pytest.mark.asyncio
    async def test_refuses_unapproved_batch(self, monkeypatch):
        _mock_mongo(monkeypatch, batch_doc={"status": "extracted", "batch_id": BATCH_ID})
        with pytest.raises(SystemExit, match="requires 'approved'"):
            await run_apply(building_id=BUILDING_ID, batch_id=BATCH_ID, executed_by=EXECUTED_BY, is_test_data=False)

    @pytest.mark.asyncio
    async def test_refuses_missing_approved_by(self, monkeypatch):
        _mock_mongo(monkeypatch, batch_doc={"status": "approved", "batch_id": BATCH_ID, "approved_by": None})
        with pytest.raises(SystemExit, match="no approved_by"):
            await run_apply(building_id=BUILDING_ID, batch_id=BATCH_ID, executed_by=EXECUTED_BY, is_test_data=False)

    @pytest.mark.asyncio
    async def test_refuses_same_approver_and_executor(self, monkeypatch):
        _mock_mongo(monkeypatch, batch_doc={
            "status": "approved", "batch_id": BATCH_ID, "approved_by": str(EXECUTED_BY),
        })
        with pytest.raises(SystemExit, match="dual control"):
            await run_apply(building_id=BUILDING_ID, batch_id=BATCH_ID, executed_by=EXECUTED_BY, is_test_data=False)

    @pytest.mark.asyncio
    async def test_refuses_stale_manifest_hash(self, monkeypatch):
        _mock_mongo(
            monkeypatch,
            batch_doc={
                "status": "approved", "batch_id": BATCH_ID, "approved_by": str(APPROVED_BY),
                "manifest_hash": "current-hash",
            },
            manifest_doc={"manifest_hash": "stale-hash", "transactions": [_debit_row()]},
        )
        with pytest.raises(SystemExit, match="Manifest has changed"):
            await run_apply(building_id=BUILDING_ID, batch_id=BATCH_ID, executed_by=EXECUTED_BY, is_test_data=False)

    @pytest.mark.asyncio
    async def test_refuses_when_manifest_disagrees_with_its_own_recorded_total(self, monkeypatch):
        _mock_mongo(
            monkeypatch,
            batch_doc={
                "status": "approved", "batch_id": BATCH_ID, "approved_by": str(APPROVED_BY),
                "manifest_hash": "h",
            },
            manifest_doc={
                "manifest_hash": "h",
                "transactions": [_debit_row(amount_cents=11000)],
                "expected_debit_cents": 999999,
            },
        )
        with pytest.raises(SystemExit, match="integrity check failed"):
            await run_apply(building_id=BUILDING_ID, batch_id=BATCH_ID, executed_by=EXECUTED_BY, is_test_data=False)

    @pytest.mark.asyncio
    async def test_no_debit_transactions_is_a_clean_noop(self, monkeypatch, capsys):
        """Manifest with only credit (income) rows and no debit rows -- nothing for this
        expense-only script to post, must not crash trying."""
        _mock_mongo(
            monkeypatch,
            batch_doc={
                "status": "approved", "batch_id": BATCH_ID, "approved_by": str(APPROVED_BY),
                "manifest_hash": "h",
            },
            manifest_doc={"manifest_hash": "h", "transactions": [_credit_row()]},
        )
        await run_apply(building_id=BUILDING_ID, batch_id=BATCH_ID, executed_by=EXECUTED_BY, is_test_data=False)
        assert "nothing to post" in capsys.readouterr().out.lower()

    @pytest.mark.asyncio
    async def test_credit_rows_never_counted_toward_expense_total(self, monkeypatch):
        """A manifest mixing income (credit) and expense (debit) rows -- as _build_manifest_for_batch
        always produces -- must only ever post/total the debit rows, never the credit ones."""
        _mock_mongo(
            monkeypatch,
            batch_doc={
                "status": "approved", "batch_id": BATCH_ID, "approved_by": str(APPROVED_BY),
                "manifest_hash": "h",
            },
            manifest_doc={
                "manifest_hash": "h",
                "transactions": [
                    _credit_row(amount_cents=500000),
                    _debit_row(amount_cents=11000, amount_ex_gst_cents=10000, gst_cents=1000),
                ],
                "expected_debit_cents": 11000,
            },
        )
        # Patch the Postgres-touching internals so this stays a pure guard-clause/filter test —
        # asserting on what gets passed to record_historical_expense, not a real DB round-trip.
        recorded_commands = []

        class _FakeSvc:
            async def record_historical_expense(self, cmd):
                recorded_commands.append(cmd)
                receipt = MagicMock()
                receipt.metadata = {}
                return receipt

        with (
            patch(f"{_MODULE}.async_session_context") as mock_session_ctx,
            patch(f"{_MODULE}._resolve_scheme", new=AsyncMock(return_value=(str(uuid.uuid4()), str(uuid.uuid4())))),
            patch(f"{_MODULE}.set_tenant", new=AsyncMock()),
            patch(f"{_MODULE}.resolve_scheme_fund_ids", new=AsyncMock(return_value={"admin": uuid.uuid4()})),
            patch(f"{_MODULE}.PostgresLedgerRepository"),
            patch(f"{_MODULE}.PostgresOutboxRepository"),
            patch(f"{_MODULE}.FinancialCoreService", return_value=_FakeSvc()),
        ):
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__.return_value = mock_session
            mock_session_ctx.return_value.__aexit__.return_value = False
            await run_apply(building_id=BUILDING_ID, batch_id=BATCH_ID, executed_by=EXECUTED_BY, is_test_data=False)

        assert len(recorded_commands) == 1
        assert recorded_commands[0].amount_cents == 10000
        assert recorded_commands[0].gst_cents == 1000
        assert recorded_commands[0].category_name == "Cleaning"
