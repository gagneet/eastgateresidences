"""
Tests for Levy Payment Approval Workflow.

Covers:
- PATCH /levy-payments/{id}/verify  — confirm a pending payment
- PATCH /levy-payments/{id}/reject  — reject a pending payment
- DELETE /levy-payments/{id}        — delete payment records (tiered permissions)
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BUILDING_ID = "13195"


@pytest.fixture(autouse=True)
def _force_mongodb_routing():
    """Force the financial strangler to route all operations to MongoDB.

    These are unit tests that mock routers.finance.db — they must not hit
    the PostgreSQL path. The PG path requires valid UUIDs and a live DB
    connection, which breaks every test that uses fake IDs like 'pay-001'.
    """
    with patch("utils.financial_strangler.default_gate.is_postgres_enabled", new_callable=AsyncMock) as m:
        m.return_value = False
        yield


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _owner_user(unit_number="TH087"):
    return {
        "id": "user-owner-001",
        "email": "avneet@eastgateresidences.com.au",
        "full_name": "Avneet Rooprai",
        "role": "owner",
        "unit_number": unit_number,
        "permissions": {"can_view_finances": True, "can_manage_finances": False},
    }


def _manager_user(role="ec_member"):
    return {
        "id": "user-mgr-001",
        "email": "anthony@eastgateresidences.com.au",
        "full_name": "Anthony McDonald",
        "role": role,
        "unit_number": "UA063",
        "permissions": {"can_view_finances": True, "can_manage_finances": True},
    }


def _super_admin_user():
    return {
        "id": "user-admin-001",
        "email": "administrator@eastgateresidences.com.au",
        "full_name": "Super Admin",
        "role": "super_admin",
        "unit_number": None,
        "permissions": {"can_view_finances": True, "can_manage_finances": True},
    }


def _pending_payment(payment_id="pay-001", unit="TH087", owner_id="user-owner-001"):
    return {
        "id": payment_id,
        "unit_number": unit,
        "amount": 1200.50,
        "payment_method": "bpay",
        "payment_reference": "REF123",
        "quarter": "Q1",
        "year": "2026",
        "fund_type": None,
        "payment_type": "standard",
        "credit_amount": None,
        "status": "pending_verification",
        "notes": None,
        "receipt_number": "EG-2026-00001",
        "paid_by": owner_id,
        "confirmed_by": None,
        "confirmed_at": None,
        "rejected_by": None,
        "rejected_at": None,
        "rejection_reason": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _confirmed_payment(**kwargs):
    doc = _pending_payment(**kwargs)
    doc["status"] = "confirmed"
    doc["confirmed_by"] = "user-mgr-001"
    doc["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    return doc


def _rejected_payment(**kwargs):
    doc = _pending_payment(**kwargs)
    doc["status"] = "rejected"
    doc["rejected_by"] = "user-mgr-001"
    doc["rejected_at"] = datetime.now(timezone.utc).isoformat()
    doc["rejection_reason"] = "Not found in bank statement"
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# Utility: build a mock permissions object
# ─────────────────────────────────────────────────────────────────────────────

def _make_perms(can_manage=False, can_view=True):
    perms = MagicMock()
    perms.can_manage_finances = can_manage
    perms.can_view_finances = can_view
    return perms


# ─────────────────────────────────────────────────────────────────────────────
# Section 1: Verify Payment
# ─────────────────────────────────────────────────────────────────────────────

class TestVerifyPayment:
    """PATCH /levy-payments/{id}/verify"""

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @patch("routers.finance.create_audit_log", new_callable=AsyncMock)
    @patch("routers.finance._upsert_ledger_for_payment", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_verify_confirms_payment(self, mock_upsert, mock_audit, mock_db, mock_perms):
        """Successful verify updates status to confirmed."""
        from routers.finance import verify_levy_payment
        from models.finance import LevyPaymentVerify

        payment = _pending_payment()
        mock_perms.return_value = _make_perms(can_manage=True)
        mock_db.levy_payments.find_one = AsyncMock(return_value=payment)
        mock_db.levy_payments.update_one = AsyncMock()
        mock_db.user_units.find = MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[])))

        result = await verify_levy_payment("pay-001", LevyPaymentVerify(), _manager_user(), BUILDING_ID)

        assert result.status == "confirmed"
        assert result.confirmed_by == "user-mgr-001"
        mock_db.levy_payments.update_one.assert_called_once()
        call_args = mock_db.levy_payments.update_one.call_args
        assert call_args[0][1]["$set"]["status"] == "confirmed"

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @pytest.mark.asyncio
    async def test_verify_403_for_owner(self, mock_db, mock_perms):
        """Owners cannot verify payments."""
        from routers.finance import verify_levy_payment
        from models.finance import LevyPaymentVerify
        from fastapi import HTTPException

        mock_perms.return_value = _make_perms(can_manage=False)

        with pytest.raises(HTTPException) as exc:
            await verify_levy_payment("pay-001", LevyPaymentVerify(), _owner_user(), BUILDING_ID)
        assert exc.value.status_code == 403

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @pytest.mark.asyncio
    async def test_verify_404_unknown_id(self, mock_db, mock_perms):
        """404 when payment_id does not exist."""
        from routers.finance import verify_levy_payment
        from models.finance import LevyPaymentVerify
        from fastapi import HTTPException

        mock_perms.return_value = _make_perms(can_manage=True)
        mock_db.levy_payments.find_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await verify_levy_payment("nonexistent", LevyPaymentVerify(), _manager_user(), BUILDING_ID)
        assert exc.value.status_code == 404

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @pytest.mark.asyncio
    async def test_verify_400_already_confirmed(self, mock_db, mock_perms):
        """400 when payment is already confirmed."""
        from routers.finance import verify_levy_payment
        from models.finance import LevyPaymentVerify
        from fastapi import HTTPException

        mock_perms.return_value = _make_perms(can_manage=True)
        mock_db.levy_payments.find_one = AsyncMock(return_value=_confirmed_payment())

        with pytest.raises(HTTPException) as exc:
            await verify_levy_payment("pay-001", LevyPaymentVerify(), _manager_user(), BUILDING_ID)
        assert exc.value.status_code == 400

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @patch("routers.finance.create_audit_log", new_callable=AsyncMock)
    @patch("routers.finance._upsert_ledger_for_payment", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_verify_appends_notes_when_provided(self, mock_upsert, mock_audit, mock_db, mock_perms):
        """Notes from manager are appended to existing notes."""
        from routers.finance import verify_levy_payment
        from models.finance import LevyPaymentVerify

        payment = {**_pending_payment(), "notes": "Owner reported via email"}
        mock_perms.return_value = _make_perms(can_manage=True)
        mock_db.levy_payments.find_one = AsyncMock(return_value=payment)
        mock_db.levy_payments.update_one = AsyncMock()
        mock_db.user_units.find = MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[])))

        result = await verify_levy_payment("pay-001", LevyPaymentVerify(notes="Verified in NAB statement"),
                                           _manager_user(), BUILDING_ID)

        call_args = mock_db.levy_payments.update_one.call_args
        updated_notes = call_args[0][1]["$set"].get("notes", "")
        assert "Manager note" in updated_notes
        assert "Verified in NAB statement" in updated_notes


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: Reject Payment
# ─────────────────────────────────────────────────────────────────────────────

class TestRejectPayment:
    """PATCH /levy-payments/{id}/reject"""

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @patch("routers.finance.create_audit_log", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_reject_updates_status(self, mock_audit, mock_db, mock_perms):
        """Successful reject updates status to rejected with reason."""
        from routers.finance import reject_levy_payment
        from models.finance import LevyPaymentReject

        payment = _pending_payment()
        mock_perms.return_value = _make_perms(can_manage=True)
        mock_db.levy_payments.find_one = AsyncMock(return_value=payment)
        mock_db.levy_payments.update_one = AsyncMock()
        mock_db.user_units.find = MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[])))

        result = await reject_levy_payment(
            "pay-001",
            LevyPaymentReject(rejection_reason="Payment not found in bank statement"),
            _manager_user(),
            BUILDING_ID
        )

        assert result.status == "rejected"
        assert result.rejection_reason == "Payment not found in bank statement"
        assert result.rejected_by == "user-mgr-001"

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @pytest.mark.asyncio
    async def test_reject_400_empty_reason(self, mock_db, mock_perms):
        """400 when rejection_reason is empty."""
        from routers.finance import reject_levy_payment
        from models.finance import LevyPaymentReject
        from fastapi import HTTPException

        mock_perms.return_value = _make_perms(can_manage=True)
        mock_db.levy_payments.find_one = AsyncMock(return_value=_pending_payment())

        with pytest.raises(HTTPException) as exc:
            await reject_levy_payment("pay-001", LevyPaymentReject(rejection_reason="  "), _manager_user(), BUILDING_ID)
        assert exc.value.status_code == 400

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @pytest.mark.asyncio
    async def test_reject_404_unknown_id(self, mock_db, mock_perms):
        """404 when payment_id does not exist."""
        from routers.finance import reject_levy_payment
        from models.finance import LevyPaymentReject
        from fastapi import HTTPException

        mock_perms.return_value = _make_perms(can_manage=True)
        mock_db.levy_payments.find_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await reject_levy_payment("nonexistent", LevyPaymentReject(rejection_reason="test reason"), _manager_user(),
                                      BUILDING_ID)
        assert exc.value.status_code == 404

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @pytest.mark.asyncio
    async def test_reject_403_for_owner(self, mock_db, mock_perms):
        """Owners cannot reject payments."""
        from routers.finance import reject_levy_payment
        from models.finance import LevyPaymentReject
        from fastapi import HTTPException

        mock_perms.return_value = _make_perms(can_manage=False)

        with pytest.raises(HTTPException) as exc:
            await reject_levy_payment("pay-001", LevyPaymentReject(rejection_reason="a reason"), _owner_user(),
                                      BUILDING_ID)
        assert exc.value.status_code == 403

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @pytest.mark.asyncio
    async def test_reject_400_already_confirmed(self, mock_db, mock_perms):
        """400 when payment is already confirmed."""
        from routers.finance import reject_levy_payment
        from models.finance import LevyPaymentReject
        from fastapi import HTTPException

        mock_perms.return_value = _make_perms(can_manage=True)
        mock_db.levy_payments.find_one = AsyncMock(return_value=_confirmed_payment())

        with pytest.raises(HTTPException) as exc:
            await reject_levy_payment("pay-001", LevyPaymentReject(rejection_reason="test reason"), _manager_user(),
                                      BUILDING_ID)
        assert exc.value.status_code == 400

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @patch("routers.finance.create_audit_log", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_reject_stores_rejection_reason(self, mock_audit, mock_db, mock_perms):
        """Rejection reason is persisted to database."""
        from routers.finance import reject_levy_payment
        from models.finance import LevyPaymentReject

        payment = _pending_payment()
        mock_perms.return_value = _make_perms(can_manage=True)
        mock_db.levy_payments.find_one = AsyncMock(return_value=payment)
        mock_db.levy_payments.update_one = AsyncMock()
        mock_db.user_units.find = MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[])))

        reason = "Amount does not match statement"
        await reject_levy_payment("pay-001", LevyPaymentReject(rejection_reason=reason), _manager_user(), BUILDING_ID)

        call_args = mock_db.levy_payments.update_one.call_args
        assert call_args[0][1]["$set"]["rejection_reason"] == reason


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: Delete Payment
# ─────────────────────────────────────────────────────────────────────────────

class TestDeletePayment:
    """DELETE /levy-payments/{id}"""

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @patch("routers.finance.create_audit_log", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_owner_deletes_own_pending(self, mock_audit, mock_db, mock_perms):
        """Owner can delete their own pending_verification record."""
        from routers.finance import delete_levy_payment

        payment = _pending_payment(owner_id="user-owner-001")
        mock_perms.return_value = _make_perms(can_manage=False)
        mock_db.levy_payments.find_one = AsyncMock(return_value=payment)
        mock_db.levy_payments.delete_one = AsyncMock()

        result = await delete_levy_payment("pay-001", _owner_user(), BUILDING_ID)
        assert result["id"] == "pay-001"
        mock_db.levy_payments.delete_one.assert_called_once_with({"id": "pay-001", "building_id": BUILDING_ID})

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @pytest.mark.asyncio
    async def test_owner_403_for_other_unit_payment(self, mock_db, mock_perms):
        """Owner cannot delete another unit's payment."""
        from routers.finance import delete_levy_payment
        from fastapi import HTTPException

        # payment was made by a different user
        payment = _pending_payment(owner_id="user-other-999")
        mock_perms.return_value = _make_perms(can_manage=False)
        mock_db.levy_payments.find_one = AsyncMock(return_value=payment)

        with pytest.raises(HTTPException) as exc:
            await delete_levy_payment("pay-001", _owner_user(), BUILDING_ID)
        assert exc.value.status_code == 403

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @pytest.mark.asyncio
    async def test_owner_403_for_confirmed_record(self, mock_db, mock_perms):
        """Owner cannot delete a confirmed record."""
        from routers.finance import delete_levy_payment
        from fastapi import HTTPException

        payment = _confirmed_payment(owner_id="user-owner-001")
        mock_perms.return_value = _make_perms(can_manage=False)
        mock_db.levy_payments.find_one = AsyncMock(return_value=payment)

        with pytest.raises(HTTPException) as exc:
            await delete_levy_payment("pay-001", _owner_user(), BUILDING_ID)
        assert exc.value.status_code == 403

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @patch("routers.finance.create_audit_log", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_manager_deletes_any_pending(self, mock_audit, mock_db, mock_perms):
        """Manager (can_manage_finances) can delete any pending_verification record."""
        from routers.finance import delete_levy_payment

        # Different owner's payment
        payment = _pending_payment(owner_id="user-other-999")
        mock_perms.return_value = _make_perms(can_manage=True)
        mock_db.levy_payments.find_one = AsyncMock(return_value=payment)
        mock_db.levy_payments.delete_one = AsyncMock()

        result = await delete_levy_payment("pay-001", _manager_user(), BUILDING_ID)
        assert result["id"] == "pay-001"
        mock_db.levy_payments.delete_one.assert_called_once()

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @patch("routers.finance.create_audit_log", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_super_admin_deletes_confirmed(self, mock_audit, mock_db, mock_perms):
        """Super admin can delete confirmed records."""
        from routers.finance import delete_levy_payment

        payment = _confirmed_payment()
        mock_perms.return_value = _make_perms(can_manage=True)
        mock_db.levy_payments.find_one = AsyncMock(return_value=payment)
        mock_db.levy_payments.delete_one = AsyncMock()

        result = await delete_levy_payment("pay-001", _super_admin_user(), BUILDING_ID)
        assert result["id"] == "pay-001"
        mock_db.levy_payments.delete_one.assert_called_once()

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @pytest.mark.asyncio
    async def test_manager_403_for_confirmed_record(self, mock_db, mock_perms):
        """Non-super-admin manager cannot delete confirmed records."""
        from routers.finance import delete_levy_payment
        from fastapi import HTTPException

        payment = _confirmed_payment()
        mock_perms.return_value = _make_perms(can_manage=True)
        mock_db.levy_payments.find_one = AsyncMock(return_value=payment)

        with pytest.raises(HTTPException) as exc:
            await delete_levy_payment("pay-001", _manager_user(role="chairman"), BUILDING_ID)
        assert exc.value.status_code == 403

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @pytest.mark.asyncio
    async def test_delete_404_unknown_id(self, mock_db, mock_perms):
        """404 when payment_id does not exist."""
        from routers.finance import delete_levy_payment
        from fastapi import HTTPException

        mock_perms.return_value = _make_perms(can_manage=True)
        mock_db.levy_payments.find_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await delete_levy_payment("nonexistent", _manager_user(), BUILDING_ID)
        assert exc.value.status_code == 404

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @pytest.mark.asyncio
    async def test_unauthorized_user_403(self, mock_db, mock_perms):
        """Users without can_manage_finances and not owner get 403."""
        from routers.finance import delete_levy_payment
        from fastapi import HTTPException

        payment = _pending_payment(owner_id="user-other-999")
        mock_perms.return_value = _make_perms(can_manage=False)
        # tenant role (not owner, not manager)
        user = {**_owner_user(), "role": "tenant", "id": "user-tenant-001"}
        mock_db.levy_payments.find_one = AsyncMock(return_value=payment)

        with pytest.raises(HTTPException) as exc:
            await delete_levy_payment("pay-001", user, BUILDING_ID)
        assert exc.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# Section 4: Model validation
# ─────────────────────────────────────────────────────────────────────────────

class TestLevyPaymentModels:
    """LevyPaymentVerify, LevyPaymentReject model shapes."""

    def test_verify_model_optional_notes(self):
        from models.finance import LevyPaymentVerify
        m = LevyPaymentVerify()
        assert m.notes is None

    def test_verify_model_with_notes(self):
        from models.finance import LevyPaymentVerify
        m = LevyPaymentVerify(notes="Checked against statement")
        assert m.notes == "Checked against statement"

    def test_reject_model_requires_reason(self):
        from models.finance import LevyPaymentReject
        m = LevyPaymentReject(rejection_reason="Amount mismatch")
        assert m.rejection_reason == "Amount mismatch"
        assert m.notes is None

    def test_reject_model_with_notes(self):
        from models.finance import LevyPaymentReject
        m = LevyPaymentReject(rejection_reason="Amount mismatch", notes="See email thread")
        assert m.notes == "See email thread"

    def test_response_model_has_rejection_fields(self):
        from models.finance import LevyPaymentResponse
        # Fields should exist and default to None
        fields = LevyPaymentResponse.model_fields
        assert "rejected_by" in fields
        assert "rejected_at" in fields
        assert "rejection_reason" in fields

    def test_response_model_optional_rejection_fields(self):
        from models.finance import LevyPaymentResponse
        # rejected_by/at/reason should have defaults
        fields = LevyPaymentResponse.model_fields
        assert fields["rejected_by"].default is None
        assert fields["rejected_at"].default is None
        assert fields["rejection_reason"].default is None


# ─────────────────────────────────────────────────────────────────────────────
# Section 5: Audit log integration
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditLogIntegration:
    """Verify audit logs are created for each action."""

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @patch("routers.finance.create_audit_log")
    @patch("routers.finance._notify_payment_verified", new_callable=AsyncMock)
    @patch("routers.finance._upsert_ledger_for_payment", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_verify_creates_audit_log(self, mock_upsert, mock_notify, mock_audit, mock_db, mock_perms):
        """Verifying a payment creates an audit log entry."""
        from routers.finance import verify_levy_payment
        from models.finance import LevyPaymentVerify

        payment = _pending_payment()
        mock_perms.return_value = _make_perms(can_manage=True)
        mock_db.levy_payments.find_one = AsyncMock(return_value=payment)
        mock_db.levy_payments.update_one = AsyncMock()
        mock_db.user_units.find = MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[])))
        mock_audit.return_value = AsyncMock()

        await verify_levy_payment("pay-001", LevyPaymentVerify(), _manager_user(), BUILDING_ID)

        # audit log was scheduled via create_task
        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args[1]
        assert call_kwargs["action"] == "verified"
        assert call_kwargs["resource_type"] == "levy_payment"
        assert call_kwargs["resource_id"] == "pay-001"

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @patch("routers.finance.create_audit_log")
    @pytest.mark.asyncio
    async def test_reject_creates_audit_log(self, mock_audit, mock_db, mock_perms):
        """Rejecting a payment creates an audit log entry."""
        from routers.finance import reject_levy_payment
        from models.finance import LevyPaymentReject

        payment = _pending_payment()
        mock_perms.return_value = _make_perms(can_manage=True)
        mock_db.levy_payments.find_one = AsyncMock(return_value=payment)
        mock_db.levy_payments.update_one = AsyncMock()
        mock_db.user_units.find = MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[])))
        mock_audit.return_value = AsyncMock()

        await reject_levy_payment("pay-001", LevyPaymentReject(rejection_reason="Mismatch"), _manager_user(),
                                  BUILDING_ID)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args[1]
        assert call_kwargs["action"] == "rejected"
        assert call_kwargs["details"]["reason"] == "Mismatch"

    @patch("routers.finance.get_user_permissions")
    @patch("routers.finance.db")
    @patch("routers.finance.create_audit_log")
    @pytest.mark.asyncio
    async def test_delete_creates_audit_log(self, mock_audit, mock_db, mock_perms):
        """Deleting a payment creates an audit log entry."""
        from routers.finance import delete_levy_payment

        payment = _pending_payment(owner_id="user-owner-001")
        mock_perms.return_value = _make_perms(can_manage=False)
        mock_db.levy_payments.find_one = AsyncMock(return_value=payment)
        mock_db.levy_payments.delete_one = AsyncMock()
        mock_audit.return_value = AsyncMock()

        await delete_levy_payment("pay-001", _owner_user(), BUILDING_ID)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args[1]
        assert call_kwargs["action"] == "deleted"
        assert call_kwargs["details"]["prior_status"] == "pending_verification"


# ─────────────────────────────────────────────────────────────────────────────
# Section 6: _upsert_ledger_for_payment — fund_type-respecting split
# ─────────────────────────────────────────────────────────────────────────────

def _existing_ledger_doc(**overrides):
    doc = {
        "year": "2026",
        "building_id": BUILDING_ID,
        "unit_number": "TH087",
        "admin_paid": 0.0,
        "sinking_paid": 0.0,
        "total_paid": 0.0,
        "admin_opening": 0.0,
        "sinking_opening": 0.0,
        "admin_interest": 0.0,
        "sinking_interest": 0.0,
        "admin_levied": 0.0,
        "sinking_levied": 0.0,
        "uoe": 0,
        "applied_payment_ids": [],
    }
    doc.update(overrides)
    return doc


class TestUpsertLedgerForPaymentFundSplit:
    """_upsert_ledger_for_payment must respect an explicit fund_type instead of
    always ratio-splitting by the current levy rate — see finance.py:429."""

    @patch("routers.finance.db")
    @pytest.mark.asyncio
    async def test_fund_type_sinking_puts_full_amount_in_sinking(self, mock_db):
        from routers.finance import _upsert_ledger_for_payment

        mock_db.annual_levies.find_one = AsyncMock(return_value={
            "admin_levy_per_uoe_annual": 800.0, "sinking_levy_per_uoe_annual": 200.0,
        })
        mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=_existing_ledger_doc())
        mock_db.unit_levy_ledger.update_one = AsyncMock()
        mock_db.units.update_one = AsyncMock()

        await _upsert_ledger_for_payment(
            unit_number="TH087", year="2026", amount=500.0,
            payment_id="pay-sink-1", building_id=BUILDING_ID, fund_type="sinking",
        )

        set_doc = mock_db.unit_levy_ledger.update_one.call_args[0][1]["$set"]
        assert set_doc["sinking_paid"] == 500.0
        assert set_doc["admin_paid"] == 0.0
        assert set_doc["total_paid"] == 500.0

    @patch("routers.finance.db")
    @pytest.mark.asyncio
    async def test_fund_type_administrative_puts_full_amount_in_admin(self, mock_db):
        from routers.finance import _upsert_ledger_for_payment

        mock_db.annual_levies.find_one = AsyncMock(return_value={
            "admin_levy_per_uoe_annual": 800.0, "sinking_levy_per_uoe_annual": 200.0,
        })
        mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=_existing_ledger_doc())
        mock_db.unit_levy_ledger.update_one = AsyncMock()
        mock_db.units.update_one = AsyncMock()

        await _upsert_ledger_for_payment(
            unit_number="TH087", year="2026", amount=500.0,
            payment_id="pay-admin-1", building_id=BUILDING_ID, fund_type="administrative",
        )

        set_doc = mock_db.unit_levy_ledger.update_one.call_args[0][1]["$set"]
        assert set_doc["admin_paid"] == 500.0
        assert set_doc["sinking_paid"] == 0.0
        assert set_doc["total_paid"] == 500.0

    @patch("routers.finance.db")
    @pytest.mark.asyncio
    async def test_fund_type_none_preserves_ratio_split(self, mock_db):
        """Regression guard: callers with no fund_type concept (Demo Bank matching
        queue, settlement-report imports) must keep today's ratio-split behaviour."""
        from routers.finance import _upsert_ledger_for_payment

        mock_db.annual_levies.find_one = AsyncMock(return_value={
            "admin_levy_per_uoe_annual": 800.0, "sinking_levy_per_uoe_annual": 200.0,
        })
        mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=_existing_ledger_doc())
        mock_db.unit_levy_ledger.update_one = AsyncMock()
        mock_db.units.update_one = AsyncMock()

        await _upsert_ledger_for_payment(
            unit_number="TH087", year="2026", amount=500.0,
            payment_id="pay-combined-1", building_id=BUILDING_ID, fund_type=None,
        )

        set_doc = mock_db.unit_levy_ledger.update_one.call_args[0][1]["$set"]
        # admin_ratio = 800 / (800+200) = 0.8
        assert set_doc["admin_paid"] == 400.0
        assert set_doc["sinking_paid"] == 100.0
        assert set_doc["total_paid"] == 500.0

    @patch("routers.finance.db")
    @pytest.mark.asyncio
    async def test_fund_type_unrecognized_falls_back_to_ratio_split(self, mock_db):
        """An unrecognized fund_type string must not silently misattribute
        the whole amount — it should fall back to the safe ratio-split."""
        from routers.finance import _upsert_ledger_for_payment

        mock_db.annual_levies.find_one = AsyncMock(return_value={
            "admin_levy_per_uoe_annual": 800.0, "sinking_levy_per_uoe_annual": 200.0,
        })
        mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=_existing_ledger_doc())
        mock_db.unit_levy_ledger.update_one = AsyncMock()
        mock_db.units.update_one = AsyncMock()

        await _upsert_ledger_for_payment(
            unit_number="TH087", year="2026", amount=500.0,
            payment_id="pay-typo-1", building_id=BUILDING_ID, fund_type="Admin",
        )

        set_doc = mock_db.unit_levy_ledger.update_one.call_args[0][1]["$set"]
        assert set_doc["admin_paid"] == 400.0
        assert set_doc["sinking_paid"] == 100.0
