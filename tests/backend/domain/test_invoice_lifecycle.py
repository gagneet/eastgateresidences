"""
tests/backend/domain/test_invoice_lifecycle.py

Tests for domain/invoice_lifecycle.py — state machine transitions,
jurisdictional gates, mandatory field checks, payment toggle guard.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId

from domain.invoice_lifecycle import (
    InvoiceState,
    InvoiceTransitionError,
    _assert_transition_allowed,
    _check_mandatory_fields,
    transition_invoice,
)

BUILDING_ACT = "13195"
BUILDING_NSW = "99001"
BUILDING_QLD = "99002"
INVOICE_OID = ObjectId()
INVOICE_ID = str(INVOICE_OID)
AUTHORISED = "manager@test.com"


def _make_db(doc: dict, building: dict | None = None, toggle: dict | None = None) -> MagicMock:
    mock_db = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[])

    # find_one dispatch based on collection
    async def _find_one(query, *args, **kwargs):
        return doc

    async def _building_find_one(query, *args, **kwargs):
        return building

    async def _toggle_find_one(query, *args, **kwargs):
        return toggle

    mock_db.invoice_documents.find_one = AsyncMock(side_effect=_find_one)
    mock_db.invoice_documents.update_one = AsyncMock()
    mock_db.buildings.find_one = AsyncMock(side_effect=_building_find_one)
    mock_db.feature_toggles.find_one = AsyncMock(side_effect=_toggle_find_one)
    return mock_db


def _invoice(state: str, **extra) -> dict:
    base = {
        "_id": INVOICE_OID,
        "tenant_id": BUILDING_ACT,
        "building_id": BUILDING_ACT,
        "state": state,
        "vendor_abn": "51824753556",
        "vendor_name": "Acme Pty Ltd",
        "invoice_number": "INV-001",
        "issue_date": "2026-04-01",
        "due_date": "2026-04-30",
        "total_cents": 110_00,
        "gst_cents": 10_00,
        "description": "Maintenance services",
        "abn_valid": True,
        "transitions": [],
    }
    base.update(extra)
    return base


# ── _assert_transition_allowed ────────────────────────────────────────────────

class TestTransitionAllowed:
    def test_draft_to_submitted_allowed(self):
        _assert_transition_allowed(InvoiceState.DRAFT, InvoiceState.SUBMITTED)

    def test_draft_to_approved_not_allowed(self):
        with pytest.raises(InvoiceTransitionError, match="not permitted"):
            _assert_transition_allowed(InvoiceState.DRAFT, InvoiceState.APPROVED)

    def test_validated_to_approved_allowed(self):
        _assert_transition_allowed(InvoiceState.VALIDATED, InvoiceState.APPROVED)

    def test_reconciled_is_terminal(self):
        with pytest.raises(InvoiceTransitionError, match="not permitted"):
            _assert_transition_allowed(InvoiceState.RECONCILED, InvoiceState.PAID)

    def test_rejected_is_terminal(self):
        with pytest.raises(InvoiceTransitionError, match="not permitted"):
            _assert_transition_allowed(InvoiceState.REJECTED, InvoiceState.SUBMITTED)

    def test_failed_to_scheduled_allowed(self):
        _assert_transition_allowed(InvoiceState.FAILED, InvoiceState.SCHEDULED)

    def test_on_hold_to_submitted_allowed(self):
        _assert_transition_allowed(InvoiceState.ON_HOLD, InvoiceState.SUBMITTED)


# ── _check_mandatory_fields ───────────────────────────────────────────────────

class TestMandatoryFields:
    def test_complete_doc_passes(self):
        _check_mandatory_fields(_invoice("draft"))  # should not raise

    def test_missing_vendor_name_raises(self):
        doc = _invoice("draft", vendor_name=None)
        with pytest.raises(InvoiceTransitionError, match="vendor_name"):
            _check_mandatory_fields(doc)

    def test_missing_description_raises(self):
        doc = _invoice("draft", description=None)
        with pytest.raises(InvoiceTransitionError, match="description"):
            _check_mandatory_fields(doc)

    def test_multiple_missing_fields_listed(self):
        doc = _invoice("draft", vendor_name=None, invoice_number=None, description=None)
        with pytest.raises(InvoiceTransitionError) as exc_info:
            _check_mandatory_fields(doc)
        msg = str(exc_info.value)
        assert "invoice_number" in msg
        assert "vendor_name" in msg


# ── transition_invoice — happy paths ──────────────────────────────────────────

class TestTransitionInvoiceHappyPath:
    @pytest.mark.asyncio
    async def test_draft_to_submitted(self):
        doc = _invoice("draft")
        mock_db = _make_db(doc)
        mock_db.invoice_documents.find_one = AsyncMock(return_value=doc)
        await transition_invoice(INVOICE_ID, InvoiceState.SUBMITTED, BUILDING_ACT, AUTHORISED, db=mock_db)
        mock_db.invoice_documents.update_one.assert_called_once()
        call_args = mock_db.invoice_documents.update_one.call_args[0]
        assert call_args[1]["$set"]["state"] == "submitted"

    @pytest.mark.asyncio
    async def test_submitted_to_validated(self):
        doc = _invoice("submitted")
        mock_db = _make_db(doc)
        mock_db.invoice_documents.find_one = AsyncMock(return_value=doc)
        await transition_invoice(INVOICE_ID, InvoiceState.VALIDATED, BUILDING_ACT, AUTHORISED, db=mock_db)
        mock_db.invoice_documents.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_validated_to_rejected(self):
        doc = _invoice("validated")
        mock_db = _make_db(doc)
        mock_db.invoice_documents.find_one = AsyncMock(return_value=doc)
        await transition_invoice(INVOICE_ID, InvoiceState.REJECTED, BUILDING_ACT, AUTHORISED, db=mock_db)
        call_args = mock_db.invoice_documents.update_one.call_args[0]
        assert call_args[1]["$set"]["state"] == "rejected"

    @pytest.mark.asyncio
    async def test_transition_records_history(self):
        doc = _invoice("draft")
        mock_db = _make_db(doc)
        mock_db.invoice_documents.find_one = AsyncMock(return_value=doc)
        await transition_invoice(
            INVOICE_ID, InvoiceState.SUBMITTED, BUILDING_ACT, AUTHORISED, db=mock_db, notes="Test note"
        )
        push_arg = mock_db.invoice_documents.update_one.call_args[0][1]["$push"]["transitions"]
        assert push_arg["from_state"] == "draft"
        assert push_arg["to_state"] == "submitted"
        assert push_arg["transitioned_by"] == AUTHORISED
        assert push_arg["notes"] == "Test note"

    @pytest.mark.asyncio
    async def test_not_found_raises_transition_error(self):
        mock_db = MagicMock()
        mock_db.invoice_documents.find_one = AsyncMock(return_value=None)
        with pytest.raises(InvoiceTransitionError, match="not found"):
            await transition_invoice(INVOICE_ID, InvoiceState.SUBMITTED, BUILDING_ACT, AUTHORISED, db=mock_db)


# ── NSW jurisdictional gate ───────────────────────────────────────────────────

class TestNSWJurisdictionalGate:
    @pytest.mark.asyncio
    async def test_nsw_within_budget_cap_passes(self):
        doc = _invoice("validated", total_cents=5_000_00)
        mock_db = _make_db(doc)
        mock_db.invoice_documents.find_one = AsyncMock(return_value=doc)
        mock_db.buildings.find_one = AsyncMock(return_value={"id": BUILDING_NSW, "jurisdiction": "NSW"})
        mock_db.feature_toggles.find_one = AsyncMock(return_value=None)
        # budget: cap=6000_00, current=0, total=5000_00 → 0% overage — should pass
        await transition_invoice(
            INVOICE_ID, InvoiceState.APPROVED, BUILDING_NSW, AUTHORISED,
            db=mock_db,
            budget_line_cap_cents=6_000_00,
            budget_line_current_cents=0,
        )
        mock_db.invoice_documents.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_nsw_over_budget_cap_blocked(self):
        doc = _invoice("validated", total_cents=2_000_00)
        mock_db = _make_db(doc)
        mock_db.invoice_documents.find_one = AsyncMock(return_value=doc)
        mock_db.buildings.find_one = AsyncMock(return_value={"id": BUILDING_NSW, "jurisdiction": "NSW"})
        # cap=1000_00, current=500_00, total=2000_00 → 150% over cap
        with pytest.raises(InvoiceTransitionError, match="SSMA 2015 s.102"):
            await transition_invoice(
                INVOICE_ID, InvoiceState.APPROVED, BUILDING_NSW, AUTHORISED,
                db=mock_db,
                budget_line_cap_cents=1_000_00,
                budget_line_current_cents=500_00,
            )

    @pytest.mark.asyncio
    async def test_nsw_no_cap_configured_passes(self):
        doc = _invoice("validated", total_cents=99_999_00)
        mock_db = _make_db(doc)
        mock_db.invoice_documents.find_one = AsyncMock(return_value=doc)
        mock_db.buildings.find_one = AsyncMock(return_value={"id": BUILDING_NSW, "jurisdiction": "NSW"})
        mock_db.feature_toggles.find_one = AsyncMock(return_value=None)
        # budget_line_cap_cents=0 means no cap configured — should pass
        await transition_invoice(
            INVOICE_ID, InvoiceState.APPROVED, BUILDING_NSW, AUTHORISED,
            db=mock_db,
            budget_line_cap_cents=0,
        )
        mock_db.invoice_documents.update_one.assert_called_once()


# ── QLD jurisdictional gate ───────────────────────────────────────────────────

class TestQLDJurisdictionalGate:
    @pytest.mark.asyncio
    async def test_qld_within_committee_cap_passes(self):
        doc = _invoice("validated", total_cents=100_00)  # $100
        mock_db = _make_db(doc)
        mock_db.invoice_documents.find_one = AsyncMock(return_value=doc)
        mock_db.buildings.find_one = AsyncMock(return_value={"id": BUILDING_QLD, "jurisdiction": "QLD"})
        mock_db.feature_toggles.find_one = AsyncMock(return_value=None)
        # lot_count=10 → cap = $200 × 10 = $2000 — $100 is within cap
        await transition_invoice(
            INVOICE_ID, InvoiceState.APPROVED, BUILDING_QLD, AUTHORISED,
            db=mock_db, lot_count=10,
        )
        mock_db.invoice_documents.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_qld_exceeds_committee_cap_blocked(self):
        doc = _invoice("validated", total_cents=3_000_00)  # $3000
        mock_db = _make_db(doc)
        mock_db.invoice_documents.find_one = AsyncMock(return_value=doc)
        mock_db.buildings.find_one = AsyncMock(return_value={"id": BUILDING_QLD, "jurisdiction": "QLD"})
        # lot_count=10 → cap = $200 × 10 = $2000 — $3000 exceeds cap
        with pytest.raises(InvoiceTransitionError, match="BCCM"):
            await transition_invoice(
                INVOICE_ID, InvoiceState.APPROVED, BUILDING_QLD, AUTHORISED,
                db=mock_db, lot_count=10,
            )

    @pytest.mark.asyncio
    async def test_act_jurisdiction_no_gate(self):
        doc = _invoice("validated", total_cents=99_999_00)
        mock_db = _make_db(doc)
        mock_db.invoice_documents.find_one = AsyncMock(return_value=doc)
        mock_db.buildings.find_one = AsyncMock(return_value={"id": BUILDING_ACT, "jurisdiction": "ACT"})
        mock_db.feature_toggles.find_one = AsyncMock(return_value=None)
        # ACT has no jurisdictional cap
        await transition_invoice(INVOICE_ID, InvoiceState.APPROVED, BUILDING_ACT, AUTHORISED, db=mock_db)
        mock_db.invoice_documents.update_one.assert_called_once()


# ── Payment scheduling toggle ─────────────────────────────────────────────────

class TestPaymentSchedulingToggle:
    @pytest.mark.asyncio
    async def test_approved_to_scheduled_toggle_off_blocked(self):
        doc = _invoice("approved")
        mock_db = _make_db(doc)
        mock_db.invoice_documents.find_one = AsyncMock(return_value=doc)
        mock_db.buildings.find_one = AsyncMock(return_value=None)
        mock_db.feature_toggles.find_one = AsyncMock(return_value=None)
        with patch(
            "domain.invoice_lifecycle.config_repo.resolve_feature_toggle",
            new=AsyncMock(return_value=False),
        ), pytest.raises(InvoiceTransitionError, match="outbound_payment_initiation_enabled"):
            await transition_invoice(INVOICE_ID, InvoiceState.SCHEDULED, BUILDING_ACT, AUTHORISED, db=mock_db)

    @pytest.mark.asyncio
    async def test_approved_to_scheduled_toggle_on_passes(self):
        doc = _invoice("approved")
        mock_db = _make_db(doc)
        mock_db.invoice_documents.find_one = AsyncMock(return_value=doc)
        mock_db.buildings.find_one = AsyncMock(return_value=None)
        with patch(
            "domain.invoice_lifecycle.config_repo.resolve_feature_toggle",
            new=AsyncMock(return_value=True),
        ):
            await transition_invoice(INVOICE_ID, InvoiceState.SCHEDULED, BUILDING_ACT, AUTHORISED, db=mock_db)
        mock_db.invoice_documents.update_one.assert_called_once()
