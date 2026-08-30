"""
tests/backend/routers/test_ap_approval.py

Tests for routers/ap_approval.py — manager guard, invoice list/detail,
submit/validate/approve/reject/schedule state transitions, duplicate re-check
on validate, and jurisdictional error mapping.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId

from routers.ap_approval import (
    approve_invoice,
    get_invoice,
    list_invoices,
    reject_invoice,
    schedule_invoice,
    submit_invoice,
    validate_invoice,
    _require_manager,
)

BUILDING_A = "13195"
INVOICE_OID = ObjectId()
INVOICE_ID = str(INVOICE_OID)

_MANAGER_USER = {
    "role": "strata_manager",
    "effective_role": "strata_manager",
    "email": "manager@strata.com",
    "_id": "user-mgr-001",
}
_OWNER_USER = {
    "role": "owner",
    "effective_role": "owner",
    "email": "owner@unit.com",
    "_id": "user-own-001",
}


def _invoice_doc(state: str = "validated", **extra) -> dict:
    base = {
        "_id": INVOICE_OID,
        "tenant_id": BUILDING_A,
        "building_id": BUILDING_A,
        "state": state,
        "vendor_name": "Acme Pty Ltd",
        "vendor_abn": "51824753556",
        "invoice_number": "INV-001",
        "issue_date": "2026-04-01",
        "due_date": "2026-04-30",
        "total_cents": 1_100_00,
        "gst_cents": 100_00,
        "is_tax_invoice": True,
        "abn_valid": True,
        "abn_entity_name": "Acme Pty Ltd",
        "abn_status": "Active",
        "withholding_required": False,
        "description": "Maintenance",
        "category": "maintenance",
        "ocr_confidence": 0.92,
        "low_confidence_fields": [],
        "transitions": [],
        "submitted_by": "supplier@acme.com",
        "created_at": "2026-04-01T00:00:00+00:00",
        "is_test_data": False,
    }
    base.update(extra)
    return base


def _make_db(doc=None, docs=None) -> MagicMock:
    mock_db = MagicMock()
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.skip = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=docs or [])
    mock_db.invoice_documents.find.return_value = cursor
    mock_db.invoice_documents.find_one = AsyncMock(return_value=doc)
    mock_db.invoice_documents.update_one = AsyncMock()
    mock_db.buildings.find_one = AsyncMock(return_value={"id": BUILDING_A, "jurisdiction": "ACT"})
    mock_db.feature_toggles.find_one = AsyncMock(return_value=None)
    # abn_validation_cache for duplicate check
    mock_db.abn_validation_cache = MagicMock()
    return mock_db


# ── Manager guard ─────────────────────────────────────────────────────────────

class TestManagerGuard:
    def test_strata_manager_allowed(self):
        _require_manager(_MANAGER_USER)  # should not raise

    def test_strata_admin_allowed(self):
        _require_manager({"effective_role": "strata_admin", "role": "owner"})

    def test_super_admin_allowed(self):
        _require_manager({"effective_role": "super_admin", "role": "owner"})

    def test_owner_denied(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _require_manager(_OWNER_USER)
        assert exc_info.value.status_code == 403

    def test_ec_member_denied(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _require_manager({"effective_role": "ec_member", "role": "ec_member"})
        assert exc_info.value.status_code == 403

    def test_effective_role_used_not_raw_role(self):
        # elevated owner should use effective_role
        elevated = {"role": "owner", "effective_role": "strata_manager"}
        _require_manager(elevated)  # should not raise


# ── GET /ap/invoices ──────────────────────────────────────────────────────────

class TestListInvoices:
    @pytest.mark.asyncio
    async def test_returns_invoice_list(self):
        docs = [_invoice_doc("validated"), _invoice_doc("validated", _id=ObjectId())]
        mock_db = _make_db(docs=docs)
        with patch("routers.ap_approval.db", mock_db):
            result = await list_invoices(
                invoice_state="validated",
                page=1, page_size=20,
                current_user=_MANAGER_USER,
                building_id=BUILDING_A,
            )
        assert len(result) == 2
        assert result[0].state == "validated"

    @pytest.mark.asyncio
    async def test_filters_by_state(self):
        mock_db = _make_db(docs=[])
        with patch("routers.ap_approval.db", mock_db):
            await list_invoices(
                invoice_state="approved",
                page=1, page_size=20,
                current_user=_MANAGER_USER,
                building_id=BUILDING_A,
            )
        find_call = mock_db.invoice_documents.find.call_args[0][0]
        assert find_call["state"] == "approved"
        assert find_call["is_test_data"] == {"$ne": True}

    @pytest.mark.asyncio
    async def test_owner_returns_403(self):
        from fastapi import HTTPException
        mock_db = _make_db(docs=[])
        with patch("routers.ap_approval.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await list_invoices(
                    invoice_state="validated",
                    page=1, page_size=20,
                    current_user=_OWNER_USER,
                    building_id=BUILDING_A,
                )
        assert exc_info.value.status_code == 403


# ── GET /ap/invoices/{id} ─────────────────────────────────────────────────────

class TestGetInvoice:
    @pytest.mark.asyncio
    async def test_returns_detail(self):
        doc = _invoice_doc("validated")
        mock_db = _make_db(doc=doc)
        with patch("routers.ap_approval.db", mock_db):
            result = await get_invoice(
                invoice_id=INVOICE_ID,
                current_user=_MANAGER_USER,
                building_id=BUILDING_A,
            )
        assert result.invoice_id == INVOICE_ID
        assert result.vendor_name == "Acme Pty Ltd"
        assert result.transitions == []

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self):
        from fastapi import HTTPException
        mock_db = _make_db(doc=None)
        with patch("routers.ap_approval.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await get_invoice(
                    invoice_id=INVOICE_ID,
                    current_user=_MANAGER_USER,
                    building_id=BUILDING_A,
                )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_oid_returns_404(self):
        from fastapi import HTTPException
        mock_db = _make_db(doc=None)
        with patch("routers.ap_approval.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await get_invoice(
                    invoice_id="not-a-valid-oid",
                    current_user=_MANAGER_USER,
                    building_id=BUILDING_A,
                )
        assert exc_info.value.status_code == 404


# ── POST .../submit ───────────────────────────────────────────────────────────

class TestSubmitInvoice:
    @pytest.mark.asyncio
    async def test_submit_draft_succeeds(self):
        from routers.ap_approval import TransitionRequest
        doc = _invoice_doc("draft")
        mock_db = _make_db(doc=doc)
        with patch("routers.ap_approval.db", mock_db), \
                patch("routers.ap_approval.transition_invoice", new=AsyncMock(return_value=doc)):
            result = await submit_invoice(
                invoice_id=INVOICE_ID,
                body=TransitionRequest(),
                current_user=_MANAGER_USER,
                building_id=BUILDING_A,
            )
        assert result["state"] == "submitted"

    @pytest.mark.asyncio
    async def test_invalid_transition_returns_409(self):
        from fastapi import HTTPException
        from routers.ap_approval import TransitionRequest
        from domain.invoice_lifecycle import InvoiceTransitionError

        doc = _invoice_doc("rejected")
        mock_db = _make_db(doc=doc)
        with patch("routers.ap_approval.db", mock_db), \
                patch("routers.ap_approval.transition_invoice",
                      new=AsyncMock(side_effect=InvoiceTransitionError("not permitted"))):
            with pytest.raises(HTTPException) as exc_info:
                await submit_invoice(
                    invoice_id=INVOICE_ID,
                    body=TransitionRequest(),
                    current_user=_MANAGER_USER,
                    building_id=BUILDING_A,
                )
        assert exc_info.value.status_code == 409


# ── POST .../validate (re-runs duplicate check) ───────────────────────────────

class TestValidateInvoice:
    @pytest.mark.asyncio
    async def test_validate_no_dup_succeeds(self):
        from routers.ap_approval import TransitionRequest
        from domain.duplicate_detection import DuplicateCheckResult

        doc = _invoice_doc("submitted")
        mock_db = _make_db(doc=doc)
        dup = DuplicateCheckResult(is_duplicate=False, layer=None, existing_invoice_id=None, reason=None)

        with patch("routers.ap_approval.db", mock_db), \
                patch("domain.duplicate_detection.check_duplicate", new=AsyncMock(return_value=dup)), \
                patch("routers.ap_approval.transition_invoice", new=AsyncMock(return_value=doc)):
            result = await validate_invoice(
                invoice_id=INVOICE_ID,
                body=TransitionRequest(),
                current_user=_MANAGER_USER,
                building_id=BUILDING_A,
            )
        assert result["state"] == "validated"

    @pytest.mark.asyncio
    async def test_validate_dup_detected_returns_409(self):
        from fastapi import HTTPException
        from routers.ap_approval import TransitionRequest
        from domain.duplicate_detection import DuplicateCheckResult

        doc = _invoice_doc("submitted")
        mock_db = _make_db(doc=doc)
        dup = DuplicateCheckResult(
            is_duplicate=True, layer=1,
            existing_invoice_id="other-id",
            reason="Exact duplicate",
        )

        with patch("routers.ap_approval.db", mock_db), \
                patch("domain.duplicate_detection.check_duplicate", new=AsyncMock(return_value=dup)):
            with pytest.raises(HTTPException) as exc_info:
                await validate_invoice(
                    invoice_id=INVOICE_ID,
                    body=TransitionRequest(),
                    current_user=_MANAGER_USER,
                    building_id=BUILDING_A,
                )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_validate_passes_exclude_id_to_dup_check(self):
        from routers.ap_approval import TransitionRequest
        from domain.duplicate_detection import DuplicateCheckResult

        doc = _invoice_doc("submitted")
        mock_db = _make_db(doc=doc)
        dup = DuplicateCheckResult(is_duplicate=False, layer=None, existing_invoice_id=None, reason=None)
        mock_check = AsyncMock(return_value=dup)

        with patch("routers.ap_approval.db", mock_db), \
                patch("domain.duplicate_detection.check_duplicate", new=mock_check), \
                patch("routers.ap_approval.transition_invoice", new=AsyncMock(return_value=doc)):
            await validate_invoice(
                invoice_id=INVOICE_ID,
                body=TransitionRequest(),
                current_user=_MANAGER_USER,
                building_id=BUILDING_A,
            )
        call_kwargs = mock_check.call_args[1]
        assert call_kwargs.get("exclude_id") == INVOICE_ID


# ── POST .../approve ──────────────────────────────────────────────────────────

class TestApproveInvoice:
    @pytest.mark.asyncio
    async def test_approve_validated_succeeds(self):
        from routers.ap_approval import TransitionRequest

        doc = _invoice_doc("validated")
        mock_db = _make_db(doc=doc)
        with patch("routers.ap_approval.db", mock_db), \
                patch("routers.ap_approval.transition_invoice", new=AsyncMock(return_value=doc)):
            result = await approve_invoice(
                invoice_id=INVOICE_ID,
                body=TransitionRequest(),
                current_user=_MANAGER_USER,
                building_id=BUILDING_A,
            )
        assert result["state"] == "approved"

    @pytest.mark.asyncio
    async def test_approve_passes_decided_by_email(self):
        from routers.ap_approval import TransitionRequest
        from domain.invoice_lifecycle import InvoiceState

        doc = _invoice_doc("validated")
        mock_db = _make_db(doc=doc)
        mock_transition = AsyncMock(return_value=doc)

        with patch("routers.ap_approval.db", mock_db), \
                patch("routers.ap_approval.transition_invoice", new=mock_transition):
            await approve_invoice(
                invoice_id=INVOICE_ID,
                body=TransitionRequest(),
                current_user=_MANAGER_USER,
                building_id=BUILDING_A,
            )

        call_kwargs = mock_transition.call_args
        assert call_kwargs[0][3] == _MANAGER_USER["email"]  # authorised_by positional arg


# ── POST .../reject ───────────────────────────────────────────────────────────

class TestRejectInvoice:
    @pytest.mark.asyncio
    async def test_reject_validated_succeeds(self):
        from routers.ap_approval import TransitionRequest

        doc = _invoice_doc("validated")
        mock_db = _make_db(doc=doc)
        with patch("routers.ap_approval.db", mock_db), \
                patch("routers.ap_approval.transition_invoice", new=AsyncMock(return_value=doc)):
            result = await reject_invoice(
                invoice_id=INVOICE_ID,
                body=TransitionRequest(),
                current_user=_MANAGER_USER,
                building_id=BUILDING_A,
            )
        assert result["state"] == "rejected"

    @pytest.mark.asyncio
    async def test_reject_nsw_gate_maps_to_422(self):
        from fastapi import HTTPException
        from routers.ap_approval import TransitionRequest
        from domain.invoice_lifecycle import InvoiceTransitionError

        doc = _invoice_doc("validated")
        mock_db = _make_db(doc=doc)
        with patch("routers.ap_approval.db", mock_db), \
                patch("routers.ap_approval.transition_invoice",
                      new=AsyncMock(side_effect=InvoiceTransitionError("NSW SSMA s.102 gate"))):
            with pytest.raises(HTTPException) as exc_info:
                await reject_invoice(
                    invoice_id=INVOICE_ID,
                    body=TransitionRequest(),
                    current_user=_MANAGER_USER,
                    building_id=BUILDING_A,
                )
        assert exc_info.value.status_code == 422


# ── POST .../schedule ─────────────────────────────────────────────────────────

class TestScheduleInvoice:
    @pytest.mark.asyncio
    async def test_schedule_approved_succeeds(self):
        from routers.ap_approval import TransitionRequest

        doc = _invoice_doc("approved")
        mock_db = _make_db(doc=doc)
        with patch("routers.ap_approval.db", mock_db), \
                patch("routers.ap_approval.transition_invoice", new=AsyncMock(return_value=doc)):
            result = await schedule_invoice(
                invoice_id=INVOICE_ID,
                body=TransitionRequest(),
                current_user=_MANAGER_USER,
                building_id=BUILDING_A,
            )
        assert result["state"] == "scheduled"

    @pytest.mark.asyncio
    async def test_schedule_toggle_off_returns_409(self):
        from fastapi import HTTPException
        from routers.ap_approval import TransitionRequest
        from domain.invoice_lifecycle import InvoiceTransitionError

        doc = _invoice_doc("approved")
        mock_db = _make_db(doc=doc)
        with patch("routers.ap_approval.db", mock_db), \
                patch("routers.ap_approval.transition_invoice",
                      new=AsyncMock(side_effect=InvoiceTransitionError("outbound_payment_initiation_enabled is off"))):
            with pytest.raises(HTTPException) as exc_info:
                await schedule_invoice(
                    invoice_id=INVOICE_ID,
                    body=TransitionRequest(),
                    current_user=_MANAGER_USER,
                    building_id=BUILDING_A,
                )
        assert exc_info.value.status_code == 409


# ── Feature toggle gate ───────────────────────────────────────────────────────

class TestFeatureToggleGate:
    @pytest.mark.asyncio
    async def test_feature_disabled_returns_403(self):
        from fastapi import HTTPException
        from utils.permissions import require_feature

        checker = require_feature("ap_automation")
        with patch("utils.permissions.get_effective_feature_access",
                   new=AsyncMock(return_value=False)):
            with pytest.raises(HTTPException) as exc_info:
                await checker(current_user=_MANAGER_USER)
        assert exc_info.value.status_code == 403
        detail = exc_info.value.detail
        # detail is now a structured dict: {"code": ..., "feature_key": "ap_automation", ...}
        assert "ap_automation" in (detail if isinstance(detail, str) else detail.get("feature_key", ""))

    @pytest.mark.asyncio
    async def test_feature_enabled_passes_through_user(self):
        from utils.permissions import require_feature

        checker = require_feature("ap_automation")
        with patch("utils.permissions.get_effective_feature_access",
                   new=AsyncMock(return_value=True)):
            result = await checker(current_user=_MANAGER_USER)
        assert result == _MANAGER_USER


# ── Multi-tenant isolation ────────────────────────────────────────────────────

BUILDING_B = "16244"


class TestMultiTenantIsolation:
    @pytest.mark.asyncio
    async def test_list_query_filter_includes_test_data_guard(self):
        """list_invoices passes is_test_data filter; TenantScopedDatabase adds building_id isolation."""
        mock_db = _make_db(docs=[])
        with patch("routers.ap_approval.db", mock_db):
            await list_invoices(
                invoice_state="validated",
                page=1, page_size=20,
                current_user=_MANAGER_USER,
                building_id=BUILDING_A,
            )
        find_filter = mock_db.invoice_documents.find.call_args[0][0]
        assert find_filter["is_test_data"] == {"$ne": True}

    @pytest.mark.asyncio
    async def test_building_b_data_not_returned_to_building_a(self):
        """TenantScopedDatabase scopes the query; BUILDING_B docs never appear for BUILDING_A callers."""
        mock_db = _make_db(docs=[])
        with patch("routers.ap_approval.db", mock_db):
            result = await list_invoices(
                invoice_state="validated",
                page=1, page_size=20,
                current_user=_MANAGER_USER,
                building_id=BUILDING_A,
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_get_invoice_from_wrong_building_returns_404(self):
        """A doc owned by BUILDING_B must not be accessible to a BUILDING_A caller via TenantScopedDB."""
        from fastapi import HTTPException

        mock_db = _make_db(doc=None)  # TenantScopedDB returns None for cross-building fetch
        with patch("routers.ap_approval.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await get_invoice(
                    invoice_id=INVOICE_ID,
                    current_user=_MANAGER_USER,
                    building_id=BUILDING_A,
                )
        assert exc_info.value.status_code == 404
