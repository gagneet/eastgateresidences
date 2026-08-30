"""
Tests for all fixes shipped in the MongoDB→PostgreSQL cutover audit session.

Covers:
  1.  confirm_invoice — doc.get('status') guard (no KeyError on missing field)
  2.  confirm_invoice — atomicity: Mongo tx rolled back when invoice update fails
  3.  confirm_invoice — PG path suppressed when adapter returns source='mongo'
  4.  confirm_invoice — PG path accepted when adapter returns source='postgres'
  5.  record_expense_transaction — returns source='mongo' when flag is OFF
  6.  record_expense_transaction — delegates to PG path when flag is ON
  7.  _record_expense_postgres — gst_cents=0 on credit journal line (not duplicated)
  8.  GET /auth/me — co_owner_email propagated in user_to_response
  9.  GET /auth/me — MongoDB units query is scoped by building_id
  10. analytics_pg_service — sinking_fund query has no li.is_test_data reference
  11. analytics_pg_service — levy_benchmarks query has no li.is_test_data reference
  12. analytics_pg_service — get_budget_variance_pg raises RuntimeError (Phase G)

Multi-tenant invariant: every test that creates mocked DB state uses both
BUILDING_A="13195" and BUILDING_B="16244" to assert data isolation where
relevant.

No data is written to a real database; all DB interactions are mocked.
Test data that IS inserted (none here) would be tagged is_test_data=True
and removed in teardown — per AGENTS.md convention.
"""
from __future__ import annotations

import sys
import os
from pathlib import Path
import inspect

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

BACKEND_PATH = str(Path(__file__).resolve().parents[2] / "backend")
if BACKEND_PATH not in sys.path:
    sys.path.insert(0, BACKEND_PATH)

os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("ENCRYPTION_KEY", "uJw9lYkG9btQfIp7q0M6vFlxnPVO92jYbP4P5Ih6QqQ=")

from request_context import set_ctx_building_id  # noqa: E402

BUILDING_A = "13195"
BUILDING_B = "16244"
INVOICE_ID = "aaaaaaaa-0000-0000-0000-000000000001"


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _finance_user(role: str = "strata_manager") -> dict:
    return {"id": "user-mgr", "role": role, "effective_role": role, "building_id": BUILDING_A}


def _pending_invoice_doc(**overrides) -> dict:
    base = {
        "id": INVOICE_ID,
        "building_id": BUILDING_A,
        "status": "pending_review",
        "document_type": "invoice",
        "file_name": "inv.pdf",
        "file_type": "application/pdf",
        "file_size_bytes": 512,
        "ocr_source": "claude_vision",
        "ocr_confidence": 0.9,
        "parsed_data": {},
        "confirmed_data": None,
        "transaction_id": None,
        "receipt_generated": False,
        "receipt_data": None,
        "created_at": "2026-04-01T00:00:00+00:00",
        "created_by": "user-mgr",
    }
    base.update(overrides)
    return base


def _confirm_body():
    from models.invoice_models import InvoiceConfirmRequest
    return InvoiceConfirmRequest(
        vendor_name="Acme Plumbing",
        invoice_number="INV-999",
        invoice_date="2026-04-15",
        subtotal=200.0,
        gst=20.0,
        total=220.0,
        fund="admin",
        category="Plumbing",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. doc.get('status') guard — no KeyError when status field is absent
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestConfirmInvoiceStatusGuard:
    """doc.get('status') must not raise KeyError when the field is absent."""

    async def test_missing_status_field_does_not_raise_keyerror(self):
        """A legacy invoice doc without a 'status' key should not crash the
        already-confirmed check; it should proceed to the normal flow."""
        set_ctx_building_id(BUILDING_A)
        from routers.invoices import confirm_invoice

        # Doc has no 'status' key — previously raised KeyError
        doc_no_status = _pending_invoice_doc()
        del doc_no_status["status"]

        tx = {"id": "tx-new", "building_id": BUILDING_A}
        confirmed = {**_pending_invoice_doc(), "status": "confirmed", "transaction_id": "tx-new"}

        with patch("routers.invoices.repo.get_invoice", AsyncMock(return_value=doc_no_status)), \
             patch("routers.invoices.repo.insert_transaction", AsyncMock(return_value=tx)), \
             patch("routers.invoices.generate_invoice_receipt_pdf", return_value=b"%PDF"), \
             patch("routers.invoices.repo.update_invoice", AsyncMock(return_value=confirmed)), \
             patch("routers.invoices.is_cutover_feature_enabled", AsyncMock(return_value=False)), \
             patch("routers.invoices.db") as mock_db:
            mock_db.buildings.find_one = AsyncMock(return_value={"name": "Test Building"})
            result = await confirm_invoice(
                invoice_id=INVOICE_ID,
                body=_confirm_body(),
                current_user=_finance_user(),
                building_id=BUILDING_A,
            )

        assert result.status == "confirmed"

    async def test_confirmed_status_returns_409(self):
        """A doc with status='confirmed' must return 409 regardless of key style."""
        from fastapi import HTTPException
        from routers.invoices import confirm_invoice
        set_ctx_building_id(BUILDING_A)

        with patch("routers.invoices.repo.get_invoice",
                   AsyncMock(return_value=_pending_invoice_doc(status="confirmed"))):
            with pytest.raises(HTTPException) as exc:
                await confirm_invoice(INVOICE_ID, _confirm_body(), _finance_user(), BUILDING_A)
        assert exc.value.status_code == 409


# ─────────────────────────────────────────────────────────────────────────────
# 2. Atomicity — Mongo transaction rolled back when invoice update fails
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestConfirmInvoiceAtomicRollback:
    """When update_invoice() raises after insert_transaction() succeeds,
    the transaction document must be deleted from financial_transactions."""

    async def test_mongo_tx_rolled_back_on_update_failure(self):
        from fastapi import HTTPException
        from routers.invoices import confirm_invoice
        set_ctx_building_id(BUILDING_A)

        tx = {"id": "tx-doomed", "building_id": BUILDING_A}

        mock_db = MagicMock()
        mock_db.buildings.find_one = AsyncMock(return_value={"name": "Test Building"})
        mock_db.financial_transactions.delete_one = AsyncMock()

        with patch("routers.invoices.repo.get_invoice",
                   AsyncMock(return_value=_pending_invoice_doc())), \
             patch("routers.invoices.repo.insert_transaction", AsyncMock(return_value=tx)), \
             patch("routers.invoices.generate_invoice_receipt_pdf", return_value=b"%PDF"), \
             patch("routers.invoices.repo.update_invoice",
                   AsyncMock(side_effect=RuntimeError("Mongo write failure"))), \
             patch("routers.invoices.is_cutover_feature_enabled", AsyncMock(return_value=False)), \
             patch("routers.invoices.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await confirm_invoice(INVOICE_ID, _confirm_body(), _finance_user(), BUILDING_A)

        assert exc.value.status_code == 500
        # The orphaned transaction must have been deleted
        mock_db.financial_transactions.delete_one.assert_awaited_once_with({"id": "tx-doomed"})

    async def test_pg_path_does_not_attempt_mongo_rollback(self):
        """When the PG adapter confirms a write (source='postgres'), there is
        no MongoDB transaction to roll back — the delete must NOT be called."""
        from fastapi import HTTPException
        from routers.invoices import confirm_invoice
        set_ctx_building_id(BUILDING_A)

        pg_result = {"source": "postgres", "journal_entry_id": "jeid-0001"}

        mock_db = MagicMock()
        mock_db.buildings.find_one = AsyncMock(return_value={"name": "Test Building"})
        mock_db.financial_transactions.delete_one = AsyncMock()

        # FinancialCoreAdapter is imported locally inside confirm_invoice,
        # so we patch it at its source module.
        mock_adapter = MagicMock()
        mock_adapter.record_expense_transaction = AsyncMock(return_value=pg_result)

        with patch("routers.invoices.repo.get_invoice",
                   AsyncMock(return_value=_pending_invoice_doc())), \
             patch("routers.invoices.is_cutover_feature_enabled", AsyncMock(return_value=True)), \
             patch("services.financial_core.adapter.FinancialCoreAdapter",
                   return_value=mock_adapter), \
             patch("routers.invoices.generate_invoice_receipt_pdf", return_value=b"%PDF"), \
             patch("routers.invoices.repo.update_invoice",
                   AsyncMock(side_effect=RuntimeError("Mongo write failure"))), \
             patch("routers.invoices.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await confirm_invoice(INVOICE_ID, _confirm_body(), _finance_user(), BUILDING_A)

        assert exc.value.status_code == 500
        # No Mongo rollback on the PG path
        mock_db.financial_transactions.delete_one.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────
# 3 & 4. PG path — source check prevents silent Mongo suppression
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestConfirmInvoicePGPathRouting:
    """The invoice confirm endpoint checks adapter source before suppressing
    the MongoDB financial_transactions insert."""

    async def test_adapter_source_mongo_falls_through_to_mongo_insert(self):
        """If the adapter returns source='mongo' (flag internally off), the
        MongoDB insert_transaction must still be called."""
        from routers.invoices import confirm_invoice
        set_ctx_building_id(BUILDING_A)

        pg_result_mongo = {"source": "mongo"}
        tx = {"id": "tx-mongo-fallback"}
        confirmed = {**_pending_invoice_doc(), "status": "confirmed", "transaction_id": "tx-mongo-fallback"}

        insert_mock = AsyncMock(return_value=tx)
        mock_adapter = MagicMock()
        mock_adapter.record_expense_transaction = AsyncMock(return_value=pg_result_mongo)

        mock_db = MagicMock()
        mock_db.buildings.find_one = AsyncMock(return_value={"name": "Test"})

        with patch("routers.invoices.repo.get_invoice",
                   AsyncMock(return_value=_pending_invoice_doc())), \
             patch("routers.invoices.is_cutover_feature_enabled", AsyncMock(return_value=True)), \
             patch("services.financial_core.adapter.FinancialCoreAdapter",
                   return_value=mock_adapter), \
             patch("routers.invoices.repo.insert_transaction", insert_mock), \
             patch("routers.invoices.generate_invoice_receipt_pdf", return_value=b"%PDF"), \
             patch("routers.invoices.repo.update_invoice", AsyncMock(return_value=confirmed)), \
             patch("routers.invoices.db", mock_db):
            result = await confirm_invoice(
                INVOICE_ID, _confirm_body(), _finance_user(), BUILDING_A
            )

        # Mongo insert WAS called because adapter returned source='mongo'
        insert_mock.assert_awaited_once()
        assert result.status == "confirmed"

    async def test_adapter_source_postgres_suppresses_mongo_insert(self):
        """When adapter confirms source='postgres', insert_transaction must NOT
        be called — the outbox relay mirrors it asynchronously."""
        from routers.invoices import confirm_invoice
        set_ctx_building_id(BUILDING_A)

        pg_result = {"source": "postgres", "journal_entry_id": "jeid-abc123"}
        confirmed = {**_pending_invoice_doc(), "status": "confirmed", "transaction_id": "jeid-abc123"}
        insert_mock = AsyncMock(return_value={"id": "should-not-be-called"})

        mock_adapter = MagicMock()
        mock_adapter.record_expense_transaction = AsyncMock(return_value=pg_result)

        mock_db = MagicMock()
        mock_db.buildings.find_one = AsyncMock(return_value={"name": "Test"})

        with patch("routers.invoices.repo.get_invoice",
                   AsyncMock(return_value=_pending_invoice_doc())), \
             patch("routers.invoices.is_cutover_feature_enabled", AsyncMock(return_value=True)), \
             patch("services.financial_core.adapter.FinancialCoreAdapter",
                   return_value=mock_adapter), \
             patch("routers.invoices.repo.insert_transaction", insert_mock), \
             patch("routers.invoices.generate_invoice_receipt_pdf", return_value=b"%PDF"), \
             patch("routers.invoices.repo.update_invoice", AsyncMock(return_value=confirmed)), \
             patch("routers.invoices.db", mock_db):
            result = await confirm_invoice(
                INVOICE_ID, _confirm_body(), _finance_user(), BUILDING_A
            )

        # Mongo insert was NOT called — PG adapter took responsibility
        insert_mock.assert_not_awaited()
        # transaction_id is the journal_entry_id from PG
        assert result.transaction_id == "jeid-abc123"

    async def test_adapter_exception_falls_back_to_mongo(self):
        """If the PG adapter raises, the Mongo insert_transaction path runs."""
        from routers.invoices import confirm_invoice
        set_ctx_building_id(BUILDING_A)

        tx = {"id": "tx-fallback-on-pg-error"}
        confirmed = {**_pending_invoice_doc(), "status": "confirmed", "transaction_id": "tx-fallback-on-pg-error"}
        insert_mock = AsyncMock(return_value=tx)

        mock_adapter = MagicMock()
        mock_adapter.record_expense_transaction = AsyncMock(
            side_effect=RuntimeError("PG unavailable")
        )

        mock_db = MagicMock()
        mock_db.buildings.find_one = AsyncMock(return_value={"name": "Test"})

        with patch("routers.invoices.repo.get_invoice",
                   AsyncMock(return_value=_pending_invoice_doc())), \
             patch("routers.invoices.is_cutover_feature_enabled", AsyncMock(return_value=True)), \
             patch("services.financial_core.adapter.FinancialCoreAdapter",
                   return_value=mock_adapter), \
             patch("routers.invoices.repo.insert_transaction", insert_mock), \
             patch("routers.invoices.generate_invoice_receipt_pdf", return_value=b"%PDF"), \
             patch("routers.invoices.repo.update_invoice", AsyncMock(return_value=confirmed)), \
             patch("routers.invoices.db", mock_db):
            result = await confirm_invoice(
                INVOICE_ID, _confirm_body(), _finance_user(), BUILDING_A
            )

        # Fell back to Mongo after PG error
        insert_mock.assert_awaited_once()
        assert result.status == "confirmed"


# ─────────────────────────────────────────────────────────────────────────────
# 5 & 6. record_expense_transaction — toggle routing
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestRecordExpenseTransactionToggle:
    """FinancialCoreAdapter.record_expense_transaction() routes correctly."""

    _EXPENSE_PARAMS = dict(
        amount_dollars=220.0,
        gst_dollars=20.0,
        description="Invoice INV-999 — Acme Plumbing",
        fund_type="admin",
        category_name="Plumbing",
        financial_year="2026",
        vendor_name="Acme Plumbing",
        invoice_number="INV-999",
        transaction_date="2026-04-15",
        created_by="user-mgr",
        idempotency_key="inv-confirm-" + INVOICE_ID,
        is_test_data=False,
    )

    async def test_returns_mongo_source_when_flag_off(self):
        from services.financial_core.adapter import FinancialCoreAdapter
        adapter = FinancialCoreAdapter(BUILDING_A)

        with patch(
            "services.financial_core.adapter._is_financial_core_enabled",
            AsyncMock(return_value=False),
        ):
            result = await adapter.record_expense_transaction(**self._EXPENSE_PARAMS)

        assert result["source"] == "mongo"

    async def test_calls_postgres_path_when_flag_on(self):
        from services.financial_core.adapter import FinancialCoreAdapter
        adapter = FinancialCoreAdapter(BUILDING_A)

        mock_pg = AsyncMock(return_value={"journal_entry_id": "jeid-xyz", "source": "postgres"})

        with patch("services.financial_core.adapter._is_financial_core_enabled",
                   AsyncMock(return_value=True)), \
             patch.object(adapter, "_record_expense_postgres", mock_pg):
            result = await adapter.record_expense_transaction(**self._EXPENSE_PARAMS)

        assert result["source"] == "postgres"
        assert result["journal_entry_id"] == "jeid-xyz"
        mock_pg.assert_awaited_once()

    async def test_forwarded_fields_match_input(self):
        """All fields must be forwarded to the PG path unchanged."""
        from services.financial_core.adapter import FinancialCoreAdapter
        adapter = FinancialCoreAdapter(BUILDING_A)
        captured: list[dict] = []

        async def _capture(**kw):
            captured.append(kw)
            return {"journal_entry_id": "jeid-cap", "source": "postgres"}

        with patch("services.financial_core.adapter._is_financial_core_enabled",
                   AsyncMock(return_value=True)), \
             patch.object(adapter, "_record_expense_postgres", _capture):
            await adapter.record_expense_transaction(**self._EXPENSE_PARAMS)

        assert captured[0]["amount_dollars"] == 220.0
        assert captured[0]["gst_dollars"] == 20.0
        assert captured[0]["fund_type"] == "admin"
        assert captured[0]["idempotency_key"] == "inv-confirm-" + INVOICE_ID
        assert captured[0]["is_test_data"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 7. _record_expense_postgres — gst_cents=0 on credit line
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestRecordExpensePostgresJournalLines:
    """The credit journal line must always carry gst_cents=0 to prevent
    double-counting GST in financial reports.

    _record_expense_postgres() was rewritten (2026-07-17, historical ledger
    reconciliation work) to route through FinancialCoreService.record_expense()
    instead of raw SQL — see services/financial_core/adapter.py and
    tests/backend/test_financial_core_service.py::TestRecordExpense for the
    service-level coverage this mirrors. These two tests now mock at the
    LedgerRepository boundary (matching MockLedgerRepository in
    test_financial_core_service.py) rather than inspecting raw SQL params,
    since the adapter no longer issues any."""

    @staticmethod
    def _make_mock_ledger():
        """Minimal in-memory LedgerRepository stub — just enough for
        record_expense() to run end to end and capture the posted entry."""
        import uuid as _uuid
        from services.financial_core.domain.entities import JournalEntry

        state = {"entries": {}}

        class _MockLedger:
            async def save_journal_entry(self, entry: JournalEntry) -> JournalEntry:
                eid = entry.journal_entry_id or _uuid.uuid4()
                entry.journal_entry_id = eid
                state["entries"][eid] = entry
                return entry

            async def post_journal_entry(self, entry_id, scheme_ref):
                return state["entries"][entry_id]

            async def get_current_accounting_period(self, scheme_ref):
                return _uuid.UUID("66666666-0000-0000-0000-000000000006")

            async def get_accounting_period_for_date(self, scheme_ref, effective_on, *, permitted_statuses=frozenset({"open"})):
                from datetime import date as _date
                from services.financial_core.domain.entities import AccountingPeriod
                return AccountingPeriod(
                    period_id=_uuid.UUID("66666666-0000-0000-0000-000000000006"),
                    period_label="open-all-time", starts_on=_date(1900, 1, 1), ends_on=_date(2100, 12, 31),
                    status="open",
                )

            async def get_gl_account(self, scheme_ref, account_code):
                mapping = {
                    "1010": _uuid.UUID("99999999-0000-0000-0000-000000000009"),
                    "5000": _uuid.UUID("aaaaaaaa-0000-0000-0000-00000000000a"),
                    "5100": _uuid.UUID("aaaaaaaa-0000-0000-0000-000000000510"),
                }
                return mapping.get(account_code, _uuid.uuid4())

            async def get_fund_type(self, scheme_ref, fund_id):
                return "admin"

            async def save_expense(self, expense):
                return expense

            async def find_journal_entry_id_by_idempotency_key(self, scheme_ref, idempotency_key):
                return None

        return _MockLedger(), state

    async def test_credit_line_gst_is_zero(self):
        from services.financial_core.adapter import FinancialCoreAdapter
        from services.financial_core.domain.entities import SchemeRef, EntryDirection
        import uuid

        adapter = FinancialCoreAdapter(BUILDING_A)
        scheme_ref = SchemeRef(
            tenant_id=uuid.UUID("11111111-0000-0000-0000-000000000000"),
            scheme_id=uuid.UUID("22222222-0000-0000-0000-000000000000"),
        )
        mock_ledger, state = self._make_mock_ledger()
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(adapter, "_resolve_scheme_ref", AsyncMock(return_value=scheme_ref)), \
             patch("db_postgres.session.async_session_context", return_value=mock_session), \
             patch("db_postgres.session.set_tenant", AsyncMock()), \
             patch("services.financial_core.genesis.resolve_scheme_fund_ids",
                   AsyncMock(return_value={"admin": uuid.UUID("33333333-0000-0000-0000-000000000003")})), \
             patch("services.financial_core.adapters.db_postgres.ledger_repo.PostgresLedgerRepository",
                   return_value=mock_ledger), \
             patch("services.financial_core.adapters.db_postgres.outbox_repo.PostgresOutboxRepository",
                   return_value=AsyncMock(publish=AsyncMock())):
            await adapter._record_expense_postgres(
                amount_dollars=200.0,
                gst_dollars=20.0,
                description="Test invoice",
                fund_type="admin",
                category_name="Maintenance",
                financial_year="2026",
                vendor_name="Test Co",
                invoice_number="INV-T01",
                transaction_date="2026-04-15",
                created_by="user-1",
                idempotency_key="idem-test-001",
                is_test_data=False,
            )

        entry = next(iter(state["entries"].values()))
        debit_line = next(l for l in entry.lines if l.direction == EntryDirection.DEBIT)
        credit_line = next(l for l in entry.lines if l.direction == EntryDirection.CREDIT)

        assert str(entry.fund_id) == "33333333-0000-0000-0000-000000000003"
        assert debit_line.gst_cents == 2000, (
            f"Debit line should have gst=2000 (cents), got {debit_line.gst_cents}"
        )
        assert credit_line.gst_cents == 0, (
            f"Credit line must have gst=0, got {credit_line.gst_cents}"
        )

    async def test_debit_amount_equals_credit_amount(self):
        """The debit and credit amount_cents must be equal (balanced entry)."""
        from services.financial_core.adapter import FinancialCoreAdapter
        from services.financial_core.domain.entities import SchemeRef
        import uuid

        adapter = FinancialCoreAdapter(BUILDING_A)
        scheme_ref = SchemeRef(
            tenant_id=uuid.UUID("11111111-0000-0000-0000-000000000000"),
            scheme_id=uuid.UUID("22222222-0000-0000-0000-000000000000"),
        )
        mock_ledger, state = self._make_mock_ledger()
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(adapter, "_resolve_scheme_ref", AsyncMock(return_value=scheme_ref)), \
             patch("db_postgres.session.async_session_context", return_value=mock_session), \
             patch("db_postgres.session.set_tenant", AsyncMock()), \
             patch("services.financial_core.genesis.resolve_scheme_fund_ids",
                   AsyncMock(return_value={"admin": uuid.UUID("33333333-0000-0000-0000-000000000003")})), \
             patch("services.financial_core.adapters.db_postgres.ledger_repo.PostgresLedgerRepository",
                   return_value=mock_ledger), \
             patch("services.financial_core.adapters.db_postgres.outbox_repo.PostgresOutboxRepository",
                   return_value=AsyncMock(publish=AsyncMock())):
            await adapter._record_expense_postgres(
                amount_dollars=300.0,
                gst_dollars=30.0,
                description="Balanced entry test",
                fund_type="admin",
                category_name="General",
                financial_year="2026",
                vendor_name="Test",
                invoice_number=None,
                transaction_date="2026-04-01",
                created_by="user-1",
                idempotency_key=None,
                is_test_data=False,
            )

        entry = next(iter(state["entries"].values()))
        assert entry.debit_total_cents == entry.credit_total_cents, (
            f"Debit and credit totals must be equal; got "
            f"debit={entry.debit_total_cents} credit={entry.credit_total_cents}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 8. GET /auth/me — co_owner_email propagated in user_to_response
# ─────────────────────────────────────────────────────────────────────────────

class TestUserToResponseCoOwnerEmail:
    """user_to_response() must pass co_owner_email through to UserResponse."""

    def test_co_owner_email_present_in_response(self):
        from utils.permissions import user_to_response

        user = {
            "id": "u-001",
            "email": "primary@example.com",
            "full_name": "Primary Owner",
            "role": "owner",
            "is_active": True,
            "is_approved": True,
            "status": "active",
            "created_at": "2024-01-01T00:00:00+00:00",
            "co_owner_name": "Joint Owner",
            "co_owner_email": "joint@example.com",
            "owned_units": ["5"],
        }
        response = user_to_response(user)
        assert response.co_owner_name == "Joint Owner"
        assert response.co_owner_email == "joint@example.com"

    def test_co_owner_email_none_when_not_set(self):
        from utils.permissions import user_to_response

        user = {
            "id": "u-002",
            "email": "solo@example.com",
            "full_name": "Solo Owner",
            "role": "owner",
            "is_active": True,
            "is_approved": True,
            "status": "active",
            "created_at": "2024-01-01T00:00:00+00:00",
        }
        response = user_to_response(user)
        assert response.co_owner_email is None

    def test_co_owner_email_not_dropped_when_co_owner_name_absent(self):
        """Edge case: co_owner_email can be set even if co_owner_name is absent."""
        from utils.permissions import user_to_response

        user = {
            "id": "u-003",
            "email": "owner@example.com",
            "full_name": "Owner",
            "role": "owner",
            "is_active": True,
            "is_approved": True,
            "status": "active",
            "created_at": "2024-01-01T00:00:00+00:00",
            "co_owner_email": "co@example.com",
            # co_owner_name deliberately absent
        }
        response = user_to_response(user)
        assert response.co_owner_email == "co@example.com"
        assert response.co_owner_name is None


# ─────────────────────────────────────────────────────────────────────────────
# 9. GET /auth/me — MongoDB units query scoped by building_id
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGetMeUnitsQueryBuildingScope:
    """The db.units.find_one call in the Mongo co-owner path must include
    building_id to prevent cross-tenant data exposure."""

    async def test_units_query_includes_building_id(self):
        from routers.auth import get_me
        set_ctx_building_id(BUILDING_A)

        user = {
            "id": "u-owner",
            "email": "primary@example.com",
            "full_name": "Primary Owner",
            "role": "owner",
            "effective_role": "owner",
            "is_active": True,
            "is_approved": True,
            "status": "active",
            "unit_number": "5",
            "building_id": BUILDING_A,
            "created_at": "2024-01-01T00:00:00+00:00",
        }

        units_find_one = AsyncMock(return_value=None)  # no co-owner on this unit
        user_units_cursor = MagicMock()
        user_units_cursor.to_list = AsyncMock(return_value=[])

        mock_db = MagicMock()
        mock_db.units.find_one = units_find_one
        mock_db.units.find.return_value.to_list = AsyncMock(return_value=[])
        mock_db.user_units.find.return_value = user_units_cursor

        with patch("routers.auth.db", mock_db), \
             patch("routers.auth.POSTGRES_AVAILABLE", False):
            await get_me(current_user=user, building_id=BUILDING_A)

        # The query filter must include building_id
        assert units_find_one.await_count >= 1
        query_filter = units_find_one.await_args[0][0]
        assert "building_id" in query_filter, (
            f"db.units.find_one called without building_id in filter: {query_filter}"
        )
        assert query_filter["building_id"] == BUILDING_A
        assert query_filter["unit_number"] == "5"

    async def test_units_query_different_buildings_use_their_own_building_id(self):
        """Multi-tenant: a user in BUILDING_B queries units of BUILDING_B only."""
        from routers.auth import get_me
        set_ctx_building_id(BUILDING_B)

        user_b = {
            "id": "u-b",
            "email": "b@example.com",
            "full_name": "Building B Owner",
            "role": "owner",
            "effective_role": "owner",
            "is_active": True,
            "is_approved": True,
            "status": "active",
            "unit_number": "10",
            "building_id": BUILDING_B,
            "created_at": "2024-01-01T00:00:00+00:00",
        }

        units_find_one = AsyncMock(return_value=None)
        user_units_cursor = MagicMock()
        user_units_cursor.to_list = AsyncMock(return_value=[])

        mock_db = MagicMock()
        mock_db.units.find_one = units_find_one
        mock_db.units.find.return_value.to_list = AsyncMock(return_value=[])
        mock_db.user_units.find.return_value = user_units_cursor

        with patch("routers.auth.db", mock_db), \
             patch("routers.auth.POSTGRES_AVAILABLE", False):
            await get_me(current_user=user_b, building_id=BUILDING_B)

        query_filter = units_find_one.await_args[0][0]
        assert query_filter["building_id"] == BUILDING_B, (
            f"Expected building_id={BUILDING_B}, got {query_filter.get('building_id')}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 10 & 11. analytics_pg_service — no li.is_test_data in levy queries
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyticsPgServiceQueryShape:
    """Validate the SQL strings in analytics_pg_service to ensure they do not
    reference li.is_test_data (a column that does not exist on finance.levy_items
    or finance.levy_runs in the ORM model)."""

    def test_sinking_fund_forecast_no_li_is_test_data(self):
        """Inspect the SQL source for is_test_data on levy_items/levy_runs."""
        import inspect
        import services.analytics_pg_service as svc

        source = inspect.getsource(svc.get_sinking_fund_forecast_pg)
        # Must not reference the non-existent column on levy_items
        assert "li.is_test_data" not in source, (
            "get_sinking_fund_forecast_pg references li.is_test_data which "
            "does not exist in the PgLevyItem ORM model"
        )
        # The allowed reference is je.is_test_data on journal_entries
        # (that column IS in PgJournalEntry)
        assert "je.is_test_data" in source or "is_test_data" not in source, (
            "Expected je.is_test_data or no is_test_data in sinking fund query"
        )

    def test_levy_benchmarks_no_li_is_test_data(self):
        import inspect
        import services.analytics_pg_service as svc

        source = inspect.getsource(svc.get_levy_benchmarks_pg)
        assert "li.is_test_data" not in source, (
            "get_levy_benchmarks_pg references li.is_test_data which "
            "does not exist in the PgLevyItem ORM model"
        )

    def test_expense_breakdown_uses_je_is_test_data(self):
        """expense_breakdown queries journal_entries where is_test_data IS valid."""
        import inspect
        import services.analytics_pg_service as svc

        source = inspect.getsource(svc.get_expense_breakdown_pg)
        # This query uses je.is_test_data which is valid
        assert "je.is_test_data" in source


# ─────────────────────────────────────────────────────────────────────────────
# 12. analytics_pg_service — get_budget_variance_pg raises RuntimeError
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestBudgetVariancePgRaisesForPhaseG:
    """get_budget_variance_pg must raise RuntimeError so the analytics.py
    caller falls back to MongoDB (where real budget data lives).  Returning
    fabricated data (budgeted=0, variance_pct=100) for every row is worse
    than no data."""

    async def test_raises_runtime_error_even_when_journal_data_exists(self):
        """Even if journal rows are present, the function raises because budget
        amounts are not yet stored per-GL-account in Postgres."""
        from services.analytics_pg_service import get_budget_variance_pg
        from services.financial_core.domain.entities import SchemeRef
        import uuid

        scheme = {"scheme_id": uuid.UUID("33333333-0000-0000-0000-000000000000"),
                  "tenant_id": uuid.UUID("44444444-0000-0000-0000-000000000000")}

        # Mock session that returns a fake journal row
        mock_row = MagicMock()
        mock_row.__iter__ = MagicMock(return_value=iter(["Plumbing", "admin", 50000]))
        mock_row.__getitem__ = MagicMock(side_effect=lambda i: ["Plumbing", "admin", 50000][i])

        mock_result = MagicMock()
        mock_result.fetchall = MagicMock(return_value=[mock_row])

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        # async_session_context is a local import inside analytics_pg_service functions;
        # patch it at the canonical db_postgres.session location.
        with patch("services.analytics_pg_service._resolve_scheme", AsyncMock(return_value=scheme)), \
             patch("db_postgres.session.async_session_context", return_value=mock_session), \
             patch("db_postgres.session.set_tenant", AsyncMock()):
            with pytest.raises(RuntimeError) as exc:
                await get_budget_variance_pg(BUILDING_A, "2026")

        assert "Phase G" in str(exc.value) or "budget" in str(exc.value).lower()

    async def test_raises_runtime_error_when_no_rows(self):
        """Also raises when there are no journal rows (different code path)."""
        from services.analytics_pg_service import get_budget_variance_pg
        import uuid

        scheme = {"scheme_id": uuid.UUID("33333333-0000-0000-0000-000000000000"),
                  "tenant_id": uuid.UUID("44444444-0000-0000-0000-000000000000")}

        mock_result = MagicMock()
        mock_result.fetchall = MagicMock(return_value=[])

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("services.analytics_pg_service._resolve_scheme", AsyncMock(return_value=scheme)), \
             patch("db_postgres.session.async_session_context", return_value=mock_session), \
             patch("db_postgres.session.set_tenant", AsyncMock()):
            with pytest.raises(RuntimeError):
                await get_budget_variance_pg(BUILDING_A, "2026")

    async def test_caller_analytics_falls_back_to_mongo_on_runtime_error(self):
        """The analytics.py endpoint must catch RuntimeError from the PG service
        and use the MongoDB budget_variance path instead.

        GAP-FIN-052 item 2 (2026-08-06): the endpoint now resolves its source via
        the governed finance route cutover engine, not the old inline
        ``is_cutover_feature_enabled`` gate — so this test forces the PG branch by
        mocking the engine to return source='postgres', then asserts the PG
        service's RuntimeError falls back to MongoDB."""
        from routers.analytics import get_budget_variance
        set_ctx_building_id(BUILDING_A)

        with patch("routers.analytics.get_finance_route_runtime_state",
                   AsyncMock(return_value={"source": "postgres"})), \
             patch("services.analytics_pg_service.get_budget_variance_pg",
                   AsyncMock(side_effect=RuntimeError("Phase G not ready"))), \
             patch("routers.analytics.db") as mock_db:
            cursor = MagicMock()
            cursor.sort.return_value = cursor
            cursor.to_list = AsyncMock(return_value=[{
                "name": "Plumbing", "fund_type": "admin",
                "budgeted_amount": 5000.0, "actual_amount": 4200.0,
            }])
            mock_db.levy_categories.find.return_value = cursor

            result = await get_budget_variance(
                year="2026",
                current_user=_finance_user(),
                building_id=BUILDING_A,
            )

        # Result came from MongoDB — has the real budgeted amount
        assert len(result) == 1
        assert result[0]["budgeted"] == 5000.0
        assert result[0]["variance"] == -800.0
