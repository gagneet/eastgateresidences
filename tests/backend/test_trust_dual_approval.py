"""
Tests for GAP-FIN-011: ABA Batch Dual-Approval Threshold.

Covers:
- First approval below threshold → directly approved
- First approval of ABA batch ≥ $5,000 → pending_second_approval
- First approval of non-ABA batch ≥ $5,000 → directly approved (ABA-only gate)
- Second approval happy path
- Second approver must differ from first
- Second approval rejected on wrong status
- Permission guard uses effective_role (elevated owners pass)
- Multi-tenant isolation (batch not found for wrong building)
"""
from __future__ import annotations

import asyncio as _real_asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId


def _close_coro_task(coro, **kwargs):
    """Drop-in for asyncio.create_task in tests: closes the coroutine so Python
    GC does not emit 'coroutine was never awaited' RuntimeWarnings."""
    if _real_asyncio.iscoroutine(coro):
        coro.close()
    return MagicMock()


# ─── Shared fixtures ──────────────────────────────────────────────────────────

def _make_batch(
        batch_id: str = "aaaaaaaaaaaaaaaaaaaaaaaa",
        building_id: str = "13195",
        batch_type: str = "aba",
        total_amount: float = 6000.00,
        status: str = "pending",
        approved_by: str | None = None,
) -> dict:
    return {
        "_id": ObjectId(batch_id),
        "building_id": building_id,
        "batch_type": batch_type,
        "total_amount": total_amount,
        "status": status,
        "item_count": 2,
        "approved_by": approved_by,
        "items": [
            {"bsb": "082-001", "account_number": "111111111", "account_name": "Vendor A", "amount": 3000.00},
            {"bsb": "062-000", "account_number": "222222222", "account_name": "Vendor B", "amount": 3000.00},
        ],
        "description": "Test batch",
        "payment_date": "2026-04-20",
    }


def _make_db(batch: dict | None) -> MagicMock:
    db = MagicMock()
    db.trust_ledger_batches.find_one = AsyncMock(return_value=batch)
    db.trust_ledger_batches.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    return db


def _ec_user(user_id: str = "user-ec-001") -> dict:
    return {
        "id": user_id,
        "role": "ec_member",
        "effective_role": "ec_member",
        "full_name": "EC Member One",
        "building_id": "13195",
    }


@pytest.fixture(autouse=True)
def _pg_primary_write_block_disabled():
    """These tests exercise the legacy V1 trust dual-approval flow itself, not the
    trust_pg_ledger_enabled PG-primary guard (_block_v1_write_if_pg_primary) — without this,
    every V1 write here 409s once a building's trust reads are PostgreSQL-primary (true live
    for building 13195 since 2026-08-04), independent of whatever this test's own scenario is
    trying to exercise. Same mocking pattern as test_trust_cutover_reads.py."""
    with patch(
        "routers.trust_accounting.are_cutover_features_enabled",
        new_callable=AsyncMock, return_value=False,
    ):
        yield


# ─── First approval tests ─────────────────────────────────────────────────────

class TestFirstApproval:

    @pytest.mark.asyncio
    async def test_below_threshold_goes_directly_to_approved(self):
        """ABA batch under $5,000 skips dual-approval gate."""
        batch = _make_batch(total_amount=4999.99)
        db = _make_db(batch)
        with patch("routers.trust_accounting._get_db", return_value=db), \
                patch("routers.trust_accounting.create_audit_log", new_callable=AsyncMock), \
                patch("routers.trust_accounting.asyncio") as mock_async:
            mock_async.create_task = _close_coro_task
            from routers.trust_accounting import approve_payment_batch
            result = await approve_payment_batch(
                batch_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                building_id="13195",
                current_user=_ec_user(),
            )
        assert result["status"] == "approved"
        assert result["dual_approval_required"] is False

    @pytest.mark.asyncio
    async def test_aba_at_threshold_triggers_dual_approval(self):
        """ABA batch at exactly $5,000 requires second approver."""
        batch = _make_batch(total_amount=5000.00)
        db = _make_db(batch)
        with patch("routers.trust_accounting._get_db", return_value=db), \
                patch("routers.trust_accounting.create_audit_log", new_callable=AsyncMock), \
                patch("routers.trust_accounting.asyncio") as mock_async:
            mock_async.create_task = _close_coro_task
            from routers.trust_accounting import approve_payment_batch
            result = await approve_payment_batch(
                batch_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                building_id="13195",
                current_user=_ec_user(),
            )
        assert result["status"] == "pending_second_approval"
        assert result["dual_approval_required"] is True
        assert "second approver" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_aba_above_threshold_triggers_dual_approval(self):
        """ABA batch over $5,000 requires second approver."""
        batch = _make_batch(total_amount=12500.00)
        db = _make_db(batch)
        with patch("routers.trust_accounting._get_db", return_value=db), \
                patch("routers.trust_accounting.create_audit_log", new_callable=AsyncMock), \
                patch("routers.trust_accounting.asyncio") as mock_async:
            mock_async.create_task = _close_coro_task
            from routers.trust_accounting import approve_payment_batch
            result = await approve_payment_batch(
                batch_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                building_id="13195",
                current_user=_ec_user(),
            )
        assert result["status"] == "pending_second_approval"
        assert result["dual_approval_required"] is True

    @pytest.mark.asyncio
    async def test_non_aba_above_threshold_goes_directly_to_approved(self):
        """Non-ABA batches (e.g. manual) skip dual approval even above threshold."""
        batch = _make_batch(batch_type="manual", total_amount=8000.00)
        db = _make_db(batch)
        with patch("routers.trust_accounting._get_db", return_value=db), \
                patch("routers.trust_accounting.create_audit_log", new_callable=AsyncMock), \
                patch("routers.trust_accounting.asyncio") as mock_async:
            mock_async.create_task = _close_coro_task
            from routers.trust_accounting import approve_payment_batch
            result = await approve_payment_batch(
                batch_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                building_id="13195",
                current_user=_ec_user(),
            )
        assert result["status"] == "approved"
        assert result["dual_approval_required"] is False

    @pytest.mark.asyncio
    async def test_approve_rejects_non_pending_status(self):
        """Already-approved batch must not be approved again."""
        from fastapi import HTTPException
        batch = _make_batch(status="approved")
        db = _make_db(batch)
        with patch("routers.trust_accounting._get_db", return_value=db):
            from routers.trust_accounting import approve_payment_batch
            with pytest.raises(HTTPException) as exc_info:
                await approve_payment_batch(
                    batch_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                    building_id="13195",
                    current_user=_ec_user(),
                )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_approve_404_for_missing_batch(self):
        """Returns 404 when batch_id not found."""
        from fastapi import HTTPException
        db = _make_db(None)
        with patch("routers.trust_accounting._get_db", return_value=db):
            from routers.trust_accounting import approve_payment_batch
            with pytest.raises(HTTPException) as exc_info:
                await approve_payment_batch(
                    batch_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                    building_id="13195",
                    current_user=_ec_user(),
                )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_rejects_unauthorized_role(self):
        """Owner without elevation is rejected by finance role guard."""
        from fastapi import HTTPException
        batch = _make_batch()
        db = _make_db(batch)
        owner_user = {"id": "u-owner", "role": "owner", "effective_role": None, "full_name": "Test Owner"}
        with patch("routers.trust_accounting._get_db", return_value=db):
            from routers.trust_accounting import approve_payment_batch
            with pytest.raises(HTTPException) as exc_info:
                await approve_payment_batch(
                    batch_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                    building_id="13195",
                    current_user=owner_user,
                )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_approve_elevated_owner_passes_finance_guard(self):
        """Temporarily elevated owner (effective_role=ec_member) passes finance guard."""
        batch = _make_batch(total_amount=1000.00)
        db = _make_db(batch)
        elevated_owner = {
            "id": "u-elevated",
            "role": "owner",
            "effective_role": "ec_member",
            "full_name": "Elevated Owner",
        }
        with patch("routers.trust_accounting._get_db", return_value=db), \
                patch("routers.trust_accounting.create_audit_log", new_callable=AsyncMock), \
                patch("routers.trust_accounting.asyncio") as mock_async:
            mock_async.create_task = _close_coro_task
            from routers.trust_accounting import approve_payment_batch
            result = await approve_payment_batch(
                batch_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                building_id="13195",
                current_user=elevated_owner,
            )
        assert result["status"] == "approved"


# ─── Second approval tests ────────────────────────────────────────────────────

class TestSecondApproval:

    @pytest.mark.asyncio
    async def test_second_approve_happy_path(self):
        """Second approver (different person) finalises the batch."""
        batch = _make_batch(status="pending_second_approval", approved_by="user-ec-001")
        db = _make_db(batch)
        second_user = _ec_user("user-ec-002")
        with patch("routers.trust_accounting._get_db", return_value=db), \
                patch("routers.trust_accounting.create_audit_log", new_callable=AsyncMock), \
                patch("routers.trust_accounting.asyncio") as mock_async:
            mock_async.create_task = _close_coro_task
            from routers.trust_accounting import second_approve_payment_batch
            result = await second_approve_payment_batch(
                batch_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                building_id="13195",
                current_user=second_user,
            )
        assert result["status"] == "approved"
        assert "second approval complete" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_second_approve_rejects_same_person(self):
        """The same person cannot be both first and second approver."""
        from fastapi import HTTPException
        batch = _make_batch(status="pending_second_approval", approved_by="user-ec-001")
        db = _make_db(batch)
        same_user = _ec_user("user-ec-001")
        with patch("routers.trust_accounting._get_db", return_value=db):
            from routers.trust_accounting import second_approve_payment_batch
            with pytest.raises(HTTPException) as exc_info:
                await second_approve_payment_batch(
                    batch_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                    building_id="13195",
                    current_user=same_user,
                )
        assert exc_info.value.status_code == 403
        assert "different person" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_second_approve_rejects_pending_status(self):
        """Second-approve endpoint only accepts pending_second_approval status."""
        from fastapi import HTTPException
        batch = _make_batch(status="pending")
        db = _make_db(batch)
        with patch("routers.trust_accounting._get_db", return_value=db):
            from routers.trust_accounting import second_approve_payment_batch
            with pytest.raises(HTTPException) as exc_info:
                await second_approve_payment_batch(
                    batch_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                    building_id="13195",
                    current_user=_ec_user("user-ec-002"),
                )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_second_approve_rejects_already_approved(self):
        """Already-approved batch is rejected on second-approve."""
        from fastapi import HTTPException
        batch = _make_batch(status="approved")
        db = _make_db(batch)
        with patch("routers.trust_accounting._get_db", return_value=db):
            from routers.trust_accounting import second_approve_payment_batch
            with pytest.raises(HTTPException) as exc_info:
                await second_approve_payment_batch(
                    batch_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                    building_id="13195",
                    current_user=_ec_user("user-ec-002"),
                )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_second_approve_404_for_missing_batch(self):
        """Returns 404 for non-existent batch."""
        from fastapi import HTTPException
        db = _make_db(None)
        with patch("routers.trust_accounting._get_db", return_value=db):
            from routers.trust_accounting import second_approve_payment_batch
            with pytest.raises(HTTPException) as exc_info:
                await second_approve_payment_batch(
                    batch_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                    building_id="13195",
                    current_user=_ec_user("user-ec-002"),
                )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_second_approve_rejects_unauthorized_role(self):
        """Tenant cannot perform second approval."""
        from fastapi import HTTPException
        batch = _make_batch(status="pending_second_approval", approved_by="user-ec-001")
        db = _make_db(batch)
        tenant = {"id": "u-tenant", "role": "tenant", "effective_role": None, "full_name": "Tenant User"}
        with patch("routers.trust_accounting._get_db", return_value=db):
            from routers.trust_accounting import second_approve_payment_batch
            with pytest.raises(HTTPException) as exc_info:
                await second_approve_payment_batch(
                    batch_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                    building_id="13195",
                    current_user=tenant,
                )
        assert exc_info.value.status_code == 403


# ─── Multi-tenant isolation ───────────────────────────────────────────────────

class TestDualApprovalMultiTenant:

    @pytest.mark.asyncio
    async def test_batch_not_visible_across_buildings(self):
        """Batch belonging to 13195 must not be found when queried from 16244."""
        from fastapi import HTTPException
        # DB returns None because building_id filter doesn't match
        db = _make_db(None)
        with patch("routers.trust_accounting._get_db", return_value=db):
            from routers.trust_accounting import approve_payment_batch
            with pytest.raises(HTTPException) as exc_info:
                await approve_payment_batch(
                    batch_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                    building_id="16244",
                    current_user=_ec_user(),
                )
        assert exc_info.value.status_code == 404
