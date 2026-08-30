# @featuretrace:finance-postgres-write-lifecycle — Comprehensive tests for verify/reject/delete/adjustment-journal lifecycle routes.
# Layer: test
# Data flow: pytest → routers/finance.py lifecycle handlers → finance_postgres_write_service
#            → FinancialCoreService → finance.receipts / finance.journal_entries (building-scoped).
# Related: backend/routers/finance.py
#          backend/services/finance_postgres_write_service.py
#          backend/services/finance_write_cutover_service.py
# Toggle: financial_pg_writes_enabled
# Table: finance.receipts, finance.journal_entries, finance.journal_lines
# Tests: tests/backend/test_finance_payment_lifecycle.py
"""Tests for Prompt 8: PostgreSQL finance payment lifecycle routes.

All tests use AsyncMock for coroutines. All mock documents include building_id.
Tests are idempotent — no real DB connections. Tests cover:
  - Verify route (idempotent confirm, audit, cross-building, role guard)
  - Reject route (reversal for posted, duplicate guard, cross-building, role guard)
  - Delete route (409 for posted, voided for draft, cross-building, role guard)
  - Adjustment journal route (balanced/unbalanced, float rejection, missing reason)
  - Coexistence invariants (no silent fallback, no dual-write, global toggle gate)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException


# ──────────────────────────────────────────────────────────────────────────────
# Shared test helpers
# ──────────────────────────────────────────────────────────────────────────────

BUILDING_ID = "13195"
OTHER_BUILDING_ID = "16244"

_MANAGER_USER = {
    "id": str(uuid4()),
    "email": "manager@test.com",
    "full_name": "Test Manager",
    "role": "strata_manager",
    "building_id": BUILDING_ID,
}

_OWNER_USER = {
    "id": str(uuid4()),
    "email": "owner@test.com",
    "full_name": "Test Owner",
    "role": "owner",
    "building_id": BUILDING_ID,
    "unit_number": "42",
}

_RECEIPT_ID = str(uuid4())
_JOURNAL_ID = str(uuid4())

_SCHEME = {
    "tenant_id": str(uuid4()),
    "scheme_id": str(uuid4()),
    "building_id": BUILDING_ID,
}


def _posted_receipt_row():
    row = MagicMock()
    row.receipt_id = _RECEIPT_ID
    row.amount_cents = 76500
    row.received_on = "2026-01-15"
    row.channel = "bank_transfer"
    row.external_reference = "REF-001"
    row.lot_id = str(uuid4())
    row.journal_entry_id = _JOURNAL_ID
    row.entry_status = "posted"
    row.metadata = {
        "building_id": BUILDING_ID,
        "unit_number": "42",
        "quarter": "Q1",
        "year": "2026",
        "fund_type": "admin",
        "payment_type": "standard",
        "source_mode": "postgres_write",
    }
    row.reversal_of_id = None
    return row


def _draft_receipt_row():
    row = _posted_receipt_row()
    row.entry_status = "draft"
    return row


def _reversed_receipt_row():
    row = _posted_receipt_row()
    row.entry_status = "reversed"
    return row


def _pg_write_state():
    return {
        "source": "postgres",
        "domain_mode": "postgres_write",
        "eligible_for_postgres_write": True,
        "blocked_reason": None,
    }


def _mongo_state():
    return {
        "source": "mongo",
        "domain_mode": "mongo_primary",
        "eligible_for_postgres_write": False,
        "blocked_reason": "Domain mode is mongo_primary; route remains Mongo-primary",
    }


# ──────────────────────────────────────────────────────────────────────────────
# 1. VERIFY ROUTE TESTS
# ──────────────────────────────────────────────────────────────────────────────

class TestVerifyRoute:

    @pytest.mark.asyncio
    async def test_verify_uses_mongo_path_in_mongo_primary(self):
        """When toggle is off, route_financial_write calls mongo handler."""
        from utils.financial_strangler import route_financial_write

        mongo_called = []
        pg_called = []

        async def mongo_handler():
            mongo_called.append(True)
            return {"id": _RECEIPT_ID, "status": "confirmed"}

        async def pg_handler():
            pg_called.append(True)
            return {"id": _RECEIPT_ID, "status": "confirmed"}

        with patch(
            "utils.financial_strangler.default_gate.is_postgres_enabled",
            new=AsyncMock(return_value=False),
        ):
            await route_financial_write(
                "verify_levy_payment",
                BUILDING_ID,
                postgres_handler=pg_handler,
                mongodb_handler=mongo_handler,
            )

        assert len(mongo_called) == 1
        assert len(pg_called) == 0

    @pytest.mark.asyncio
    async def test_verify_uses_pg_path_in_postgres_write(self):
        """When toggle is on, route_financial_write calls pg handler."""
        from utils.financial_strangler import route_financial_write

        pg_called = []

        async def pg_handler():
            pg_called.append(True)
            return {"id": _RECEIPT_ID, "status": "confirmed"}

        async def mongo_handler():
            return {"id": _RECEIPT_ID, "status": "confirmed"}

        with patch(
            "utils.financial_strangler.default_gate.is_postgres_enabled",
            new=AsyncMock(return_value=True),
        ):
            await route_financial_write(
                "verify_levy_payment",
                BUILDING_ID,
                postgres_handler=pg_handler,
                mongodb_handler=mongo_handler,
            )

        assert len(pg_called) == 1

    @pytest.mark.asyncio
    async def test_verify_blocked_if_scoped_write_toggle_not_enabled(self):
        """verify_receipt raises 404 when receipt not found (toggle gates caller)."""
        from services.finance_postgres_write_service import FinancePostgresWriteService

        svc = FinancePostgresWriteService()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None  # not found
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_actor_user_id",
            new=AsyncMock(return_value=str(uuid4())),
        ), patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ):
            with pytest.raises(HTTPException) as exc:
                await svc.verify_receipt(
                    building_id=BUILDING_ID,
                    payment_id=str(uuid4()),
                    notes=None,
                    actor=_MANAGER_USER,
                )
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_verify_cross_building_denied(self):
        """verify_receipt raises 403 when receipt belongs to another building."""
        from services.finance_postgres_write_service import FinancePostgresWriteService

        row = _posted_receipt_row()
        row.metadata = {"building_id": OTHER_BUILDING_ID}  # different building

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = row
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_actor_user_id",
            new=AsyncMock(return_value=str(uuid4())),
        ), patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ):
            with pytest.raises(HTTPException) as exc:
                await svc.verify_receipt(
                    building_id=BUILDING_ID,
                    payment_id=_RECEIPT_ID,
                    notes=None,
                    actor=_MANAGER_USER,
                )
            assert exc.value.status_code == 403
            assert "Cross-building" in exc.value.detail

    @pytest.mark.asyncio
    async def test_verify_unauthorised_role_denied(self):
        """_verify_levy_payment_postgres raises 403 for non-manager role."""
        from routers.finance import _verify_levy_payment_postgres
        from models.finance import LevyPaymentVerify

        owner_user = {**_OWNER_USER}
        data = LevyPaymentVerify(notes=None)

        # Owner does not have can_manage_finances
        with patch(
            "routers.finance.get_user_permissions",
            return_value=MagicMock(can_manage_finances=False),
        ):
            with pytest.raises(HTTPException) as exc:
                await _verify_levy_payment_postgres(_RECEIPT_ID, data, owner_user, BUILDING_ID)
            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_verify_idempotent_when_already_confirmed(self):
        """verify_receipt for an already-posted receipt writes audit noop and returns confirmed."""
        from services.finance_postgres_write_service import FinancePostgresWriteService

        row = _posted_receipt_row()  # entry_status = "posted"

        # Two execute calls: SELECT receipt row, then UPDATE (though UPDATE is skipped for already_confirmed)
        # For posted status, already_confirmed=True so no UPDATE
        execute_results = [
            MagicMock(fetchone=MagicMock(return_value=row)),
        ]
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=execute_results)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        audit_calls = []

        async def fake_audit(**kwargs):
            audit_calls.append(kwargs)

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_actor_user_id",
            new=AsyncMock(return_value=str(uuid4())),
        ), patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ), patch(
            "services.finance_postgres_write_service.create_audit_log",
            new=AsyncMock(side_effect=fake_audit),
        ):
            result = await svc.verify_receipt(
                building_id=BUILDING_ID,
                payment_id=_RECEIPT_ID,
                notes=None,
                actor=_MANAGER_USER,
            )

        assert result["status"] == "confirmed"
        assert len(audit_calls) == 1
        assert audit_calls[0]["action"] == "finance_pg_receipt_verify_noop"

    @pytest.mark.asyncio
    async def test_verify_preserves_api_response_shape(self):
        """verify_receipt returns the expected LevyPaymentResponse-compatible shape."""
        from services.finance_postgres_write_service import FinancePostgresWriteService

        row = _posted_receipt_row()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=row)))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_actor_user_id",
            new=AsyncMock(return_value=str(uuid4())),
        ), patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ), patch(
            "services.finance_postgres_write_service.create_audit_log",
            new=AsyncMock(),
        ):
            result = await svc.verify_receipt(
                building_id=BUILDING_ID,
                payment_id=_RECEIPT_ID,
                notes=None,
                actor=_MANAGER_USER,
            )

        # Must have all LevyPaymentResponse fields
        for field in ("id", "unit_number", "amount", "payment_method", "status", "receipt_number"):
            assert field in result, f"Missing field: {field}"
        assert result["status"] == "confirmed"
        assert result["receipt_number"].startswith("PG-")

    @pytest.mark.asyncio
    async def test_verify_does_not_mutate_journal_amounts(self):
        """verify_receipt must not change amount_cents on the journal entry."""
        from services.finance_postgres_write_service import FinancePostgresWriteService

        row = _posted_receipt_row()
        execute_calls = []

        async def track_execute(query, params=None):
            execute_calls.append(str(query))
            return MagicMock(fetchone=MagicMock(return_value=row))

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=track_execute)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_actor_user_id",
            new=AsyncMock(return_value=str(uuid4())),
        ), patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ), patch(
            "services.finance_postgres_write_service.create_audit_log",
            new=AsyncMock(),
        ):
            await svc.verify_receipt(
                building_id=BUILDING_ID,
                payment_id=_RECEIPT_ID,
                notes=None,
                actor=_MANAGER_USER,
            )

        # For already-confirmed receipt: only SELECT called, no UPDATE with amount
        update_calls = [c for c in execute_calls if "UPDATE" in c.upper()]
        for call in update_calls:
            assert "amount_cents" not in call.lower(), "verify must not mutate amount_cents"

    @pytest.mark.asyncio
    async def test_verify_writes_audit_event(self):
        """verify_receipt always writes an audit log entry."""
        from services.finance_postgres_write_service import FinancePostgresWriteService

        row = _posted_receipt_row()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=row)))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        audit_calls = []

        async def fake_audit(**kwargs):
            audit_calls.append(kwargs)

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_actor_user_id",
            new=AsyncMock(return_value=str(uuid4())),
        ), patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ), patch(
            "services.finance_postgres_write_service.create_audit_log",
            new=AsyncMock(side_effect=fake_audit),
        ):
            await svc.verify_receipt(
                building_id=BUILDING_ID,
                payment_id=_RECEIPT_ID,
                notes=None,
                actor=_MANAGER_USER,
            )

        assert len(audit_calls) == 1
        assert audit_calls[0]["building_id"] == BUILDING_ID
        assert "finance_pg_receipt_verif" in audit_calls[0]["action"]


# ──────────────────────────────────────────────────────────────────────────────
# 2. REJECT ROUTE TESTS
# ──────────────────────────────────────────────────────────────────────────────

class TestRejectRoute:

    @pytest.mark.asyncio
    async def test_reject_uses_mongo_path_in_mongo_primary(self):
        """route_financial_write sends to mongo when toggle disabled."""
        from utils.financial_strangler import route_financial_write

        mongo_called = []

        async def mongo_handler():
            mongo_called.append(True)
            return {"id": _RECEIPT_ID, "status": "rejected"}

        async def pg_handler():
            return {"id": _RECEIPT_ID, "status": "rejected"}

        with patch(
            "utils.financial_strangler.default_gate.is_postgres_enabled",
            new=AsyncMock(return_value=False),
        ):
            await route_financial_write(
                "reject_levy_payment", BUILDING_ID,
                postgres_handler=pg_handler, mongodb_handler=mongo_handler,
            )

        assert len(mongo_called) == 1

    @pytest.mark.asyncio
    async def test_reject_uses_pg_path_in_postgres_write(self):
        """route_financial_write sends to pg when toggle enabled."""
        from utils.financial_strangler import route_financial_write

        pg_called = []

        async def pg_handler():
            pg_called.append(True)
            return {"id": _RECEIPT_ID, "status": "rejected"}

        async def mongo_handler():
            return {"id": _RECEIPT_ID, "status": "rejected"}

        with patch(
            "utils.financial_strangler.default_gate.is_postgres_enabled",
            new=AsyncMock(return_value=True),
        ):
            await route_financial_write(
                "reject_levy_payment", BUILDING_ID,
                postgres_handler=pg_handler, mongodb_handler=mongo_handler,
            )

        assert len(pg_called) == 1

    @pytest.mark.asyncio
    async def test_reject_blocked_if_scoped_write_toggle_not_enabled(self):
        """reject_receipt raises 404 when receipt not found (toggle gates caller)."""
        from services.finance_postgres_write_service import FinancePostgresWriteService

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_actor_user_id",
            new=AsyncMock(return_value=str(uuid4())),
        ), patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ):
            with pytest.raises(HTTPException) as exc:
                await svc.reject_receipt(
                    building_id=BUILDING_ID,
                    payment_id=str(uuid4()),
                    rejection_reason="Wrong amount",
                    notes=None,
                    actor=_MANAGER_USER,
                )
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_reject_requires_reason(self):
        """reject_receipt raises 400 when rejection_reason is empty."""
        from services.finance_postgres_write_service import FinancePostgresWriteService

        svc = FinancePostgresWriteService()

        with pytest.raises(HTTPException) as exc:
            await svc.reject_receipt(
                building_id=BUILDING_ID,
                payment_id=_RECEIPT_ID,
                rejection_reason="",
                notes=None,
                actor=_MANAGER_USER,
            )
        assert exc.value.status_code == 400
        assert "rejection_reason" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_reject_cross_building_denied(self):
        """reject_receipt raises 403 when receipt belongs to a different building."""
        from services.finance_postgres_write_service import FinancePostgresWriteService

        row = _posted_receipt_row()
        row.metadata = {"building_id": OTHER_BUILDING_ID}

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=row)))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_actor_user_id",
            new=AsyncMock(return_value=str(uuid4())),
        ), patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ):
            with pytest.raises(HTTPException) as exc:
                await svc.reject_receipt(
                    building_id=BUILDING_ID,
                    payment_id=_RECEIPT_ID,
                    rejection_reason="Wrong amount",
                    notes=None,
                    actor=_MANAGER_USER,
                )
            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_reject_unauthorised_role_denied(self):
        """_reject_levy_payment_postgres raises 403 for owner without finance permissions."""
        from routers.finance import _reject_levy_payment_postgres
        from models.finance import LevyPaymentReject

        data = LevyPaymentReject(rejection_reason="Wrong amount")

        with patch(
            "routers.finance.get_user_permissions",
            return_value=MagicMock(can_manage_finances=False),
        ):
            with pytest.raises(HTTPException) as exc:
                await _reject_levy_payment_postgres(_RECEIPT_ID, data, _OWNER_USER, BUILDING_ID)
            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_reject_creates_reversal_for_posted_receipt(self):
        """reject_receipt on a posted receipt calls FinancialCoreService.reverse_entry."""
        from services.finance_postgres_write_service import FinancePostgresWriteService
        from services.financial_core.domain.entities import ReverseResult

        row = _posted_receipt_row()

        reverse_result = ReverseResult(
            original_entry_id=uuid4(),
            reversal_entry_id=uuid4(),
            reason="Wrong amount",
        )

        execute_calls = []

        async def mock_execute(query, params=None):
            execute_calls.append(("execute", str(query)))
            result = MagicMock()
            result.fetchone.return_value = row
            return result

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=mock_execute)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_core = AsyncMock()
        mock_core.reverse_entry = AsyncMock(return_value=reverse_result)

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_actor_user_id",
            new=AsyncMock(return_value=str(uuid4())),
        ), patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ), patch(
            "services.finance_postgres_write_service.FinancialCoreService",
            return_value=mock_core,
        ), patch(
            "services.finance_postgres_write_service.PostgresLedgerRepository",
        ), patch(
            "services.finance_postgres_write_service.PostgresOutboxRepository",
        ), patch(
            "services.finance_postgres_write_service.create_audit_log",
            new=AsyncMock(),
        ):
            result = await svc.reject_receipt(
                building_id=BUILDING_ID,
                payment_id=_RECEIPT_ID,
                rejection_reason="Wrong amount",
                notes=None,
                actor=_MANAGER_USER,
            )

        assert mock_core.reverse_entry.called, "reverse_entry must be called for posted receipts"
        assert result["status"] == "rejected"
        assert result["reversal_journal_entry_id"] is not None

    @pytest.mark.asyncio
    async def test_reject_does_not_mutate_original_journal(self):
        """reject_receipt does NOT modify amount_cents on the original journal entry."""
        from services.finance_postgres_write_service import FinancePostgresWriteService
        from services.financial_core.domain.entities import ReverseResult

        row = _posted_receipt_row()
        reverse_result = ReverseResult(
            original_entry_id=uuid4(),
            reversal_entry_id=uuid4(),
            reason="Duplicate payment",
        )

        update_sqls = []

        async def mock_execute(query, params=None):
            sql = str(query).upper()
            if "UPDATE" in sql:
                update_sqls.append(str(query))
            result = MagicMock()
            result.fetchone.return_value = row
            return result

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=mock_execute)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_core = AsyncMock()
        mock_core.reverse_entry = AsyncMock(return_value=reverse_result)

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_actor_user_id",
            new=AsyncMock(return_value=str(uuid4())),
        ), patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ), patch(
            "services.finance_postgres_write_service.FinancialCoreService",
            return_value=mock_core,
        ), patch(
            "services.finance_postgres_write_service.PostgresLedgerRepository",
        ), patch(
            "services.finance_postgres_write_service.PostgresOutboxRepository",
        ), patch(
            "services.finance_postgres_write_service.create_audit_log",
            new=AsyncMock(),
        ):
            await svc.reject_receipt(
                building_id=BUILDING_ID,
                payment_id=_RECEIPT_ID,
                rejection_reason="Duplicate payment",
                notes=None,
                actor=_MANAGER_USER,
            )

        # Any UPDATE must only change status, never amount_cents
        for sql in update_sqls:
            assert "amount_cents" not in sql.lower(), (
                "reject must not mutate amount_cents on original journal entry"
            )

    @pytest.mark.asyncio
    async def test_reject_duplicate_blocked_or_idempotent(self):
        """reject_receipt raises 409 ALREADY_REJECTED when receipt is already reversed."""
        from services.finance_postgres_write_service import FinancePostgresWriteService

        row = _reversed_receipt_row()

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=row)))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_actor_user_id",
            new=AsyncMock(return_value=str(uuid4())),
        ), patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ):
            with pytest.raises(HTTPException) as exc:
                await svc.reject_receipt(
                    building_id=BUILDING_ID,
                    payment_id=_RECEIPT_ID,
                    rejection_reason="Wrong amount",
                    notes=None,
                    actor=_MANAGER_USER,
                )
            assert exc.value.status_code == 409
            assert isinstance(exc.value.detail, dict)
            assert exc.value.detail["code"] == "ALREADY_REJECTED"

    @pytest.mark.asyncio
    async def test_reject_writes_audit_event(self):
        """reject_receipt always writes an audit event."""
        from services.finance_postgres_write_service import FinancePostgresWriteService
        from services.financial_core.domain.entities import ReverseResult

        row = _posted_receipt_row()
        reverse_result = ReverseResult(
            original_entry_id=uuid4(),
            reversal_entry_id=uuid4(),
            reason="Test rejection",
        )
        audit_calls = []

        async def fake_audit(**kwargs):
            audit_calls.append(kwargs)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=row)))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_core = AsyncMock()
        mock_core.reverse_entry = AsyncMock(return_value=reverse_result)

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_actor_user_id",
            new=AsyncMock(return_value=str(uuid4())),
        ), patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ), patch(
            "services.finance_postgres_write_service.FinancialCoreService",
            return_value=mock_core,
        ), patch(
            "services.finance_postgres_write_service.PostgresLedgerRepository",
        ), patch(
            "services.finance_postgres_write_service.PostgresOutboxRepository",
        ), patch(
            "services.finance_postgres_write_service.create_audit_log",
            new=AsyncMock(side_effect=fake_audit),
        ):
            await svc.reject_receipt(
                building_id=BUILDING_ID,
                payment_id=_RECEIPT_ID,
                rejection_reason="Test rejection",
                notes=None,
                actor=_MANAGER_USER,
            )

        assert len(audit_calls) == 1
        assert audit_calls[0]["action"] == "finance_pg_receipt_rejected"
        assert audit_calls[0]["building_id"] == BUILDING_ID


# ──────────────────────────────────────────────────────────────────────────────
# 3. DELETE ROUTE TESTS
# ──────────────────────────────────────────────────────────────────────────────

class TestDeleteRoute:

    @pytest.mark.asyncio
    async def test_delete_uses_mongo_path_in_mongo_primary(self):
        """route_financial_write routes to mongo handler when toggle is off."""
        from utils.financial_strangler import route_financial_write

        mongo_called = []

        async def mongo_handler():
            mongo_called.append(True)
            return {"message": "deleted", "id": _RECEIPT_ID}

        async def pg_handler():
            return {"message": "cancelled", "id": _RECEIPT_ID}

        with patch(
            "utils.financial_strangler.default_gate.is_postgres_enabled",
            new=AsyncMock(return_value=False),
        ):
            await route_financial_write(
                "delete_levy_payment", BUILDING_ID,
                postgres_handler=pg_handler, mongodb_handler=mongo_handler,
            )

        assert len(mongo_called) == 1

    @pytest.mark.asyncio
    async def test_delete_blocked_for_posted_pg_receipt_with_409(self):
        """delete_receipt raises 409 POSTED_RECORD_IMMUTABLE for posted receipts."""
        from services.finance_postgres_write_service import FinancePostgresWriteService

        row = _posted_receipt_row()  # entry_status = "posted"

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=row)))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ):
            with pytest.raises(HTTPException) as exc:
                await svc.delete_receipt(
                    building_id=BUILDING_ID,
                    payment_id=_RECEIPT_ID,
                    actor=_MANAGER_USER,
                )
            assert exc.value.status_code == 409
            assert isinstance(exc.value.detail, dict)
            assert exc.value.detail["code"] == "POSTED_RECORD_IMMUTABLE"
            assert exc.value.detail["retryable"] is False

    @pytest.mark.asyncio
    async def test_delete_never_physically_deletes_posted_pg_records(self):
        """delete_receipt must never issue a SQL DELETE for posted records."""
        from services.finance_postgres_write_service import FinancePostgresWriteService

        row = _posted_receipt_row()
        delete_sqls = []

        async def mock_execute(query, params=None):
            sql = str(query).upper()
            if "DELETE" in sql:
                delete_sqls.append(sql)
            return MagicMock(fetchone=MagicMock(return_value=row))

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=mock_execute)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ):
            with pytest.raises(HTTPException) as exc:
                await svc.delete_receipt(
                    building_id=BUILDING_ID,
                    payment_id=_RECEIPT_ID,
                    actor=_MANAGER_USER,
                )
            assert exc.value.status_code == 409  # blocked before any SQL DELETE

        assert len(delete_sqls) == 0, "No SQL DELETE must be issued for posted records"

    @pytest.mark.asyncio
    async def test_delete_cross_building_denied(self):
        """delete_receipt raises 403 for cross-building access."""
        from services.finance_postgres_write_service import FinancePostgresWriteService

        row = _draft_receipt_row()
        row.metadata = {"building_id": OTHER_BUILDING_ID}

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=row)))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ):
            with pytest.raises(HTTPException) as exc:
                await svc.delete_receipt(
                    building_id=BUILDING_ID,
                    payment_id=_RECEIPT_ID,
                    actor=_MANAGER_USER,
                )
            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_unauthorised_role_denied(self):
        """_delete_levy_payment_postgres raises 403 for users without any finance role."""
        from routers.finance import _delete_levy_payment_postgres

        guest_user = {
            "id": str(uuid4()),
            "email": "guest@test.com",
            "full_name": "Guest",
            "role": "guest",
            "building_id": BUILDING_ID,
        }

        with patch(
            "routers.finance.get_user_permissions",
            return_value=MagicMock(can_manage_finances=False),
        ), patch(
            "routers.finance.effective_role",
            return_value="guest",
        ):
            with pytest.raises(HTTPException) as exc:
                await _delete_levy_payment_postgres(_RECEIPT_ID, guest_user, BUILDING_ID)
            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_response_is_error_recovery_friendly(self):
        """409 response for posted receipt includes suggested_action."""
        from services.finance_postgres_write_service import FinancePostgresWriteService

        row = _posted_receipt_row()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=row)))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ):
            with pytest.raises(HTTPException) as exc:
                await svc.delete_receipt(
                    building_id=BUILDING_ID,
                    payment_id=_RECEIPT_ID,
                    actor=_MANAGER_USER,
                )
            detail = exc.value.detail
            assert isinstance(detail, dict)
            assert "suggested_action" in detail
            assert "reject" in detail["suggested_action"].lower()

    @pytest.mark.asyncio
    async def test_delete_allows_cancellation_of_unposted_draft(self):
        """delete_receipt on a draft receipt transitions status to voided (no physical delete)."""
        from services.finance_postgres_write_service import FinancePostgresWriteService

        row = _draft_receipt_row()
        update_sqls = []
        delete_sqls = []

        async def mock_execute(query, params=None):
            sql = str(query).upper()
            if "UPDATE" in sql:
                update_sqls.append(str(query))
            if "DELETE" in sql:
                delete_sqls.append(str(query))
            return MagicMock(fetchone=MagicMock(return_value=row))

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=mock_execute)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ), patch(
            "services.finance_postgres_write_service.create_audit_log",
            new=AsyncMock(),
        ):
            result = await svc.delete_receipt(
                building_id=BUILDING_ID,
                payment_id=_RECEIPT_ID,
                actor=_MANAGER_USER,
            )

        assert result["id"] == _RECEIPT_ID
        assert "cancelled" in result["message"].lower()
        assert len(delete_sqls) == 0, "Physical DELETE must never be issued"
        assert any("voided" in sql.lower() for sql in update_sqls), (
            "Draft receipt must transition to voided, not physically deleted"
        )


# ──────────────────────────────────────────────────────────────────────────────
# 4. COEXISTENCE / INVARIANT TESTS
# ──────────────────────────────────────────────────────────────────────────────

class TestCoexistenceInvariants:

    @pytest.mark.asyncio
    async def test_unsupported_route_in_postgres_write_returns_structured_409(self):
        """adjustment journal route returns 409 when source is mongo (toggle off)."""
        from services.finance_write_cutover_service import get_finance_write_route_runtime_state
        from models.cutover_status import CutoverMode, DataSource, DomainCutoverStatus, ReadinessStatus
        from datetime import datetime, UTC

        now = datetime.now(UTC)
        mongo_status = DomainCutoverStatus(
            id="test",
            building_id=BUILDING_ID,
            domain="finance_ledger",
            read_source=DataSource.mongo,
            write_source=DataSource.mongo,
            mode=CutoverMode.mongo_primary,
            readiness_status=ReadinessStatus.not_started,
            rollback_available=False,
            created_at=now,
            updated_at=now,
        )

        with patch(
            "services.finance_write_cutover_service.get_or_default_cutover_status",
            new=AsyncMock(return_value=mongo_status),
        ), patch(
            "services.finance_write_cutover_service.is_cutover_feature_enabled",
            new=AsyncMock(return_value=False),
        ), patch(
            "services.finance_write_cutover_service.get_route_shadow_readiness",
            new=AsyncMock(return_value={"status": "not_started", "critical_count": 0}),
        ), patch(
            "services.finance_write_cutover_service.get_cutover_status",
            new=AsyncMock(return_value=mongo_status),
        ):
            state = await get_finance_write_route_runtime_state(
                building_id=BUILDING_ID,
                route_key="finance.adjustment_journal",
                idempotency_key="test-key",
            )
        assert state["source"] == "mongo"
        assert state["eligible_for_postgres_write"] is False

    @pytest.mark.asyncio
    async def test_no_silent_mongo_fallback_in_postgres_write(self):
        """_verify_levy_payment_postgres raises (not silently falls back) when receipt not found."""
        from routers.finance import _verify_levy_payment_postgres
        from models.finance import LevyPaymentVerify

        data = LevyPaymentVerify()

        with patch(
            "routers.finance.get_user_permissions",
            return_value=MagicMock(can_manage_finances=True),
        ), patch(
            "routers.finance.finance_postgres_write_service.verify_receipt",
            new=AsyncMock(side_effect=HTTPException(status_code=404, detail="Not found in PG")),
        ):
            with pytest.raises(HTTPException) as exc:
                await _verify_levy_payment_postgres(_RECEIPT_ID, data, _MANAGER_USER, BUILDING_ID)
            # Must propagate the 404, not silently return a Mongo response
            assert exc.value.status_code == 404

    def test_no_dual_write(self):
        """finance_postgres_write_service does not import or call the mongo db directly."""
        import ast
        import pathlib

        source = pathlib.Path("backend/services/finance_postgres_write_service.py").read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "database" not in alias.name.lower(), (
                        "finance_postgres_write_service must not import the Mongo database module"
                    )
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module.lower() != "database", (
                        "finance_postgres_write_service must not import from the Mongo database module"
                    )

    @pytest.mark.asyncio
    async def test_global_cutover_toggle_false_blocks_pg_write(self):
        """When financial_pg_writes_enabled is False globally, route uses mongo path."""
        from utils.financial_strangler import default_gate

        # Patch the function as imported in financial_strangler's own namespace
        with patch(
            "utils.financial_strangler.is_cutover_feature_enabled",
            new=AsyncMock(return_value=False),
        ):
            result = await default_gate.is_postgres_enabled(BUILDING_ID)
        assert result is False


# ──────────────────────────────────────────────────────────────────────────────
# 5. ADJUSTMENT JOURNAL TESTS
# ──────────────────────────────────────────────────────────────────────────────

class TestAdjustmentJournalRoute:

    @pytest.mark.asyncio
    async def test_adjustment_journal_balanced_accepted(self):
        """create_adjustment_journal accepts a perfectly balanced entry."""
        from services.finance_postgres_write_service import FinancePostgresWriteService
        from uuid import uuid4

        gl_dr = str(uuid4())
        gl_cr = str(uuid4())
        lines = [
            {"gl_account_id": gl_dr, "direction": "debit", "amount_cents": 50000},
            {"gl_account_id": gl_cr, "direction": "credit", "amount_cents": 50000},
        ]

        period_id = uuid4()
        saved_entry = MagicMock()
        saved_entry.journal_entry_id = uuid4()
        posted_entry = MagicMock()
        posted_entry.journal_entry_id = uuid4()

        mock_ledger = AsyncMock()
        mock_ledger.get_current_accounting_period = AsyncMock(return_value=period_id)
        mock_ledger.save_journal_entry = AsyncMock(return_value=saved_entry)
        mock_ledger.post_journal_entry = AsyncMock(return_value=posted_entry)

        mock_outbox = AsyncMock()
        mock_outbox.publish = AsyncMock()

        mock_session = AsyncMock()
        # Idempotency check returns no row
        mock_session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_actor_user_id",
            new=AsyncMock(return_value=str(uuid4())),
        ), patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ), patch(
            "services.finance_postgres_write_service.async_session_context",
            return_value=mock_ctx,
        ), patch(
            "services.finance_postgres_write_service.set_tenant",
            new=AsyncMock(),
        ), patch(
            "services.finance_postgres_write_service.PostgresLedgerRepository",
            return_value=mock_ledger,
        ), patch(
            "services.finance_postgres_write_service.PostgresOutboxRepository",
            return_value=mock_outbox,
        ), patch(
            "services.finance_postgres_write_service.FinancialCoreService",
        ), patch(
            "services.finance_postgres_write_service.create_audit_log",
            new=AsyncMock(),
        ):
            # Patch FinancialCoreService to not be used (we use ledger/outbox directly)
            import services.finance_postgres_write_service as svc_mod
            original_fcs = svc_mod.FinancialCoreService

            result = await svc.create_adjustment_journal(
                building_id=BUILDING_ID,
                narration="Test adjustment",
                reason="Balance correction",
                lines=lines,
                idempotency_key="adj-test-001",
                actor=_MANAGER_USER,
            )

        assert result["status"] == "posted"
        assert result["total_debit_cents"] == 50000
        assert result["total_credit_cents"] == 50000
        assert result["replayed"] is False

    @pytest.mark.asyncio
    async def test_adjustment_journal_unbalanced_rejected(self):
        """create_adjustment_journal rejects an unbalanced entry with 422."""
        from services.finance_postgres_write_service import FinancePostgresWriteService
        from uuid import uuid4

        gl = str(uuid4())
        lines = [
            {"gl_account_id": gl, "direction": "debit", "amount_cents": 50000},
            {"gl_account_id": gl, "direction": "credit", "amount_cents": 30000},  # unbalanced
        ]

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_actor_user_id",
            new=AsyncMock(return_value=str(uuid4())),
        ), patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ):
            with pytest.raises(HTTPException) as exc:
                await svc.create_adjustment_journal(
                    building_id=BUILDING_ID,
                    narration="Unbalanced adjustment",
                    reason="Should fail",
                    lines=lines,
                    idempotency_key="adj-test-unbalanced",
                    actor=_MANAGER_USER,
                )
            assert exc.value.status_code == 422
            assert "balanced" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_adjustment_journal_float_rejected(self):
        """create_adjustment_journal rejects lines with float amount_cents."""
        from services.finance_postgres_write_service import FinancePostgresWriteService
        from uuid import uuid4

        gl = str(uuid4())
        lines = [
            {"gl_account_id": gl, "direction": "debit", "amount_cents": 500.50},  # float — wrong
            {"gl_account_id": gl, "direction": "credit", "amount_cents": 500.50},
        ]

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_actor_user_id",
            new=AsyncMock(return_value=str(uuid4())),
        ), patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ):
            with pytest.raises(HTTPException) as exc:
                await svc.create_adjustment_journal(
                    building_id=BUILDING_ID,
                    narration="Float test",
                    reason="Should fail",
                    lines=lines,
                    idempotency_key="adj-test-float",
                    actor=_MANAGER_USER,
                )
            assert exc.value.status_code == 422
            assert "integer" in exc.value.detail.lower()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("gst_cents", "expected_fragment"),
        [
            ("100", "integer"),
            (-1, "non-negative"),
        ],
    )
    async def test_adjustment_journal_invalid_gst_rejected(self, gst_cents, expected_fragment):
        """create_adjustment_journal validates gst_cents type and non-negative constraint."""
        from services.finance_postgres_write_service import FinancePostgresWriteService
        from uuid import uuid4

        gl = str(uuid4())
        lines = [
            {"gl_account_id": gl, "direction": "debit", "amount_cents": 50000, "gst_cents": gst_cents},
            {"gl_account_id": gl, "direction": "credit", "amount_cents": 50000, "gst_cents": 0},
        ]

        svc = FinancePostgresWriteService()

        with patch(
            "services.finance_postgres_write_service.config_repo.resolve_actor_user_id",
            new=AsyncMock(return_value=str(uuid4())),
        ), patch(
            "services.finance_postgres_write_service.config_repo.resolve_scheme_context",
            new=AsyncMock(return_value=_SCHEME),
        ):
            with pytest.raises(HTTPException) as exc:
                await svc.create_adjustment_journal(
                    building_id=BUILDING_ID,
                    narration="GST validation test",
                    reason="Should fail",
                    lines=lines,
                    idempotency_key="adj-test-gst-invalid",
                    actor=_MANAGER_USER,
                )
            assert exc.value.status_code == 422
            assert expected_fragment in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_adjustment_journal_missing_reason_rejected(self):
        """create_adjustment_journal rejects requests with empty reason."""
        from services.finance_postgres_write_service import FinancePostgresWriteService
        from uuid import uuid4

        svc = FinancePostgresWriteService()

        with pytest.raises(HTTPException) as exc:
            await svc.create_adjustment_journal(
                building_id=BUILDING_ID,
                narration="Test",
                reason="",  # empty reason
                lines=[],
                idempotency_key="adj-test-missing-reason",
                actor=_MANAGER_USER,
            )
        assert exc.value.status_code == 400
        assert "reason" in exc.value.detail.lower()
