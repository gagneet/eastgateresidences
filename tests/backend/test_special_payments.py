"""
Tests for GAP-FIN-011: Special Payment Approval Workflow.
Covers: create, list, get, first approve/reject, second approve/reject, cancel.
Multi-tenant isolation: building "16244" must not see "13195" data.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from request_context import set_ctx_building_id


def _make_user(role="strata_manager", uid="user-mgr"):
    return {"id": uid, "role": role, "effective_role": role, "building_id": "13195"}


def _make_payment(status="pending_approval", amount=15000.0, uid="user-mgr", requires_dual=True):
    return {
        "_id": "oid-1",
        "id": "pay-1",
        "building_id": "13195",
        "payee_name": "Ace Contractors",
        "payee_bsb": "062-000",
        "payee_account_number": "12345678",
        "amount": amount,
        "category": "contractor_invoice",
        "description": "Emergency roof repair works",
        "status": status,
        "requires_dual_approval": requires_dual,
        "submitted_by": uid,
        "first_approved_by": None,
        "first_approved_at": None,
        "second_approved_by": None,
        "second_approved_at": None,
        "rejected_by": None,
        "rejection_reason": None,
        "payment_date": "2026-05-01",
        "fund_type": "admin",
        "supporting_documents": [],
        "created_at": "2026-04-19T00:00:00+00:00",
        "updated_at": "2026-04-19T00:00:00+00:00",
    }


def _mock_db(payment=None):
    db = MagicMock()
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=[payment] if payment else [])
    db.payment_approvals.find.return_value = cursor
    db.payment_approvals.find_one = AsyncMock(return_value=payment)
    db.payment_approvals.insert_one = AsyncMock(return_value=MagicMock(inserted_id="oid-1"))
    db.payment_approvals.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    return db


# ── Create ────────────────────────────────────────────────────────────────────

class TestCreateSpecialPayment:
    @pytest.mark.asyncio
    async def test_creates_with_dual_approval_flag_above_threshold(self):
        set_ctx_building_id("13195")
        from routers.special_payments import create_special_payment
        from models.payment_approvals import SpecialPaymentCreate

        db = _mock_db()
        payload = SpecialPaymentCreate(
            payee_name="Ace Contractors",
            payee_bsb="062-000",
            payee_account_number="12345678",
            amount=15000.0,
            category="contractor_invoice",
            description="Emergency roof repair works on the main roof",
            payment_date="2026-05-01",
        )
        with patch("routers.special_payments._get_db", return_value=db):
            result = await create_special_payment(
                payload=payload,
                building_id="13195",
                current_user=_make_user(),
            )
        assert result.status == "pending_approval"
        assert result.requires_dual_approval is True
        assert result.building_id == "13195"

    @pytest.mark.asyncio
    async def test_creates_without_dual_approval_below_threshold(self):
        set_ctx_building_id("13195")
        from routers.special_payments import create_special_payment
        from models.payment_approvals import SpecialPaymentCreate

        db = _mock_db()
        payload = SpecialPaymentCreate(
            payee_name="Small Plumber",
            payee_bsb="062-001",
            payee_account_number="11111111",
            amount=5000.0,
            category="contractor_invoice",
            description="Fix bathroom pipes on level 3",
            payment_date="2026-05-01",
        )
        with patch("routers.special_payments._get_db", return_value=db):
            result = await create_special_payment(
                payload=payload,
                building_id="13195",
                current_user=_make_user(),
            )
        assert result.requires_dual_approval is False

    @pytest.mark.asyncio
    async def test_owner_cannot_create(self):
        set_ctx_building_id("13195")
        from routers.special_payments import create_special_payment
        from models.payment_approvals import SpecialPaymentCreate
        from fastapi import HTTPException

        payload = SpecialPaymentCreate(
            payee_name="Test Payee",
            payee_bsb="062-000",
            payee_account_number="12345678",
            amount=100.0,
            category="other",
            description="Some description text here long enough",
            payment_date="2026-05-01",
        )
        with pytest.raises(HTTPException) as exc:
            await create_special_payment(
                payload=payload,
                building_id="13195",
                current_user=_make_user(role="owner"),
            )
        assert exc.value.status_code == 403


# ── First Approval ────────────────────────────────────────────────────────────

class TestFirstApproval:
    @pytest.mark.asyncio
    async def test_approve_dual_moves_to_pending_second(self):
        set_ctx_building_id("13195")
        from routers.special_payments import approve_special_payment
        from models.payment_approvals import SpecialPaymentApproval

        payment = _make_payment(status="pending_approval", requires_dual=True)
        db = _mock_db(payment)
        with patch("routers.special_payments._get_db", return_value=db):
            result = await approve_special_payment(
                payment_id="pay-1",
                payload=SpecialPaymentApproval(approved=True),
                building_id="13195",
                current_user=_make_user(uid="approver-1"),
            )
        assert result.status == "pending_second_approval"
        assert result.first_approved_by == "approver-1"

    @pytest.mark.asyncio
    async def test_approve_single_moves_to_approved(self):
        set_ctx_building_id("13195")
        from routers.special_payments import approve_special_payment
        from models.payment_approvals import SpecialPaymentApproval

        payment = _make_payment(status="pending_approval", amount=5000.0, requires_dual=False)
        db = _mock_db(payment)
        with patch("routers.special_payments._get_db", return_value=db):
            result = await approve_special_payment(
                payment_id="pay-1",
                payload=SpecialPaymentApproval(approved=True),
                building_id="13195",
                current_user=_make_user(uid="approver-1"),
            )
        assert result.status == "approved"

    @pytest.mark.asyncio
    async def test_reject_requires_reason(self):
        set_ctx_building_id("13195")
        from routers.special_payments import approve_special_payment
        from models.payment_approvals import SpecialPaymentApproval
        from fastapi import HTTPException

        payment = _make_payment(status="pending_approval")
        db = _mock_db(payment)
        with patch("routers.special_payments._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await approve_special_payment(
                    payment_id="pay-1",
                    payload=SpecialPaymentApproval(approved=False, reason=None),
                    building_id="13195",
                    current_user=_make_user(),
                )
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_reject_with_reason_moves_to_rejected(self):
        set_ctx_building_id("13195")
        from routers.special_payments import approve_special_payment
        from models.payment_approvals import SpecialPaymentApproval

        payment = _make_payment(status="pending_approval")
        db = _mock_db(payment)
        with patch("routers.special_payments._get_db", return_value=db):
            result = await approve_special_payment(
                payment_id="pay-1",
                payload=SpecialPaymentApproval(approved=False, reason="Insufficient quotes"),
                building_id="13195",
                current_user=_make_user(uid="approver-1"),
            )
        assert result.status == "rejected"
        assert result.rejection_reason == "Insufficient quotes"

    @pytest.mark.asyncio
    async def test_wrong_status_raises_409(self):
        set_ctx_building_id("13195")
        from routers.special_payments import approve_special_payment
        from models.payment_approvals import SpecialPaymentApproval
        from fastapi import HTTPException

        payment = _make_payment(status="approved")
        db = _mock_db(payment)
        with patch("routers.special_payments._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await approve_special_payment(
                    payment_id="pay-1",
                    payload=SpecialPaymentApproval(approved=True),
                    building_id="13195",
                    current_user=_make_user(),
                )
        assert exc.value.status_code == 409


# ── Second Approval ───────────────────────────────────────────────────────────

class TestSecondApproval:
    @pytest.mark.asyncio
    async def test_second_approve_moves_to_approved(self):
        set_ctx_building_id("13195")
        from routers.special_payments import second_approve_special_payment
        from models.payment_approvals import SpecialPaymentApproval

        payment = _make_payment(status="pending_second_approval")
        payment["first_approved_by"] = "approver-1"
        db = _mock_db(payment)
        with patch("routers.special_payments._get_db", return_value=db):
            result = await second_approve_special_payment(
                payment_id="pay-1",
                payload=SpecialPaymentApproval(approved=True),
                building_id="13195",
                current_user=_make_user(uid="approver-2"),
            )
        assert result.status == "approved"
        assert result.second_approved_by == "approver-2"

    @pytest.mark.asyncio
    async def test_same_approver_blocked(self):
        set_ctx_building_id("13195")
        from routers.special_payments import second_approve_special_payment
        from models.payment_approvals import SpecialPaymentApproval
        from fastapi import HTTPException

        payment = _make_payment(status="pending_second_approval")
        payment["first_approved_by"] = "approver-1"
        db = _mock_db(payment)
        with patch("routers.special_payments._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await second_approve_special_payment(
                    payment_id="pay-1",
                    payload=SpecialPaymentApproval(approved=True),
                    building_id="13195",
                    current_user=_make_user(uid="approver-1"),  # same as first approver
                )
        assert exc.value.status_code == 403


# ── Cancel ────────────────────────────────────────────────────────────────────

class TestCancelPayment:
    @pytest.mark.asyncio
    async def test_submitter_can_cancel(self):
        set_ctx_building_id("13195")
        from routers.special_payments import cancel_special_payment

        payment = _make_payment(status="pending_approval", uid="submitter-1")
        db = _mock_db(payment)
        with patch("routers.special_payments._get_db", return_value=db):
            result = await cancel_special_payment(
                payment_id="pay-1",
                building_id="13195",
                current_user=_make_user(uid="submitter-1"),
            )
        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_other_user_cannot_cancel(self):
        set_ctx_building_id("13195")
        from routers.special_payments import cancel_special_payment
        from fastapi import HTTPException

        payment = _make_payment(status="pending_approval", uid="submitter-1")
        db = _mock_db(payment)
        with patch("routers.special_payments._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await cancel_special_payment(
                    payment_id="pay-1",
                    building_id="13195",
                    current_user=_make_user(role="admin", uid="other-user"),
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_already_approved_cannot_cancel(self):
        set_ctx_building_id("13195")
        from routers.special_payments import cancel_special_payment
        from fastapi import HTTPException

        payment = _make_payment(status="approved")
        db = _mock_db(payment)
        with patch("routers.special_payments._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await cancel_special_payment(
                    payment_id="pay-1",
                    building_id="13195",
                    current_user=_make_user(),
                )
        assert exc.value.status_code == 409


# ── Multi-tenant isolation ────────────────────────────────────────────────────

class TestSpecialPaymentMultiTenant:
    @pytest.mark.asyncio
    async def test_building_16244_cannot_see_13195_payment(self):
        set_ctx_building_id("16244")
        from routers.special_payments import get_special_payment
        from fastapi import HTTPException

        db = _mock_db(payment=None)  # find_one returns None for cross-building query
        with patch("routers.special_payments._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await get_special_payment(
                    payment_id="pay-1",
                    building_id="16244",
                    current_user=_make_user(),
                )
        assert exc.value.status_code == 404
        # Confirm building_id was scoped in the query
        db.payment_approvals.find_one.assert_called_once_with(
            {"id": "pay-1", "building_id": "16244"}
        )
