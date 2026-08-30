# @featuretrace:nsw-commission-disclosures — Tests for GAP-JUR-NSW-007 insurance commission disclosure (building-scoped).
# Layer: test
# Data flow: tests → backend/routers/nsw_compliance.py → nsw_commission_disclosures / nsw_strata_hub_returns collections (building-scoped).
# Related: backend/routers/nsw_compliance.py
#           backend/seeds/feature_toggles.py (nsw_commission_disclosures, nsw_strata_hub_returns toggles)
# Collection: nsw_commission_disclosures
# Collection: nsw_strata_hub_returns
"""
Tests for GAP-JUR-NSW-007 (insurance commission disclosure) and
GAP-JUR-NSW-008 (Strata Hub annual return records) in nsw_compliance.py.

Multi-tenant isolation: building "16244" must not see "13195" data.
Role guard: manager/EC only; owner/guest get 403.
Soft-delete: cancelled_at set, hard-delete never used.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from request_context import set_ctx_building_id


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user(role="strata_manager", uid="mgr-1"):
    return {"id": uid, "role": role, "effective_role": role, "building_id": "13195"}


def _disclosure(bid="13195", disc_id="disc-1", cancelled=False):
    return {
        "_id": "oid-1",
        "id": disc_id,
        "building_id": bid,
        "disclosure_type": "commission",
        "insurer_name": "Zurich Australia",
        "policy_type": "building",
        "premium_cents": 250000,
        "commission_amount_cents": 12500,
        "commission_rate_pct": 5.0,
        "recipient_name": "Smith Strata Management",
        "disclosed_to": "owners_corporation",
        "disclosed_at": "2026-05-01T10:00:00+00:00",
        "notes": None,
        "created_at": "2026-05-01T10:00:00+00:00",
        "updated_at": "2026-05-01T10:00:00+00:00",
        "cancelled_at": "2026-05-02T00:00:00+00:00" if cancelled else None,
    }


def _hub_return(bid="13195", ret_id="ret-1"):
    return {
        "_id": "oid-2",
        "id": ret_id,
        "building_id": bid,
        "financial_year_end": "2025-12-31",
        "submission_method": "portal",
        "submission_reference": "SH-2026-001234",
        "submitted_by_name": "Alice Manager",
        "submitted_by_email": "alice@strata.com.au",
        "submitted_at": "2026-03-15T09:00:00+00:00",
        "lot_count": 81,
        "total_levies_cents": 34087002,
        "agm_date": "2026-02-10",
        "strata_manager_name": "Alice Manager",
        "strata_manager_licence": "SML-20001",
        "status": "submitted",
        "created_at": "2026-03-15T09:00:00+00:00",
        "updated_at": "2026-03-15T09:00:00+00:00",
    }


def _mock_db(doc=None, docs=None):
    db = MagicMock()
    # commission disclosures
    cursor_disc = MagicMock()
    cursor_disc.sort = MagicMock(return_value=cursor_disc)
    cursor_disc.skip = MagicMock(return_value=cursor_disc)
    cursor_disc.limit = MagicMock(return_value=cursor_disc)
    cursor_disc.to_list = AsyncMock(return_value=docs if docs is not None else ([doc] if doc else []))
    db.nsw_commission_disclosures.find.return_value = cursor_disc
    db.nsw_commission_disclosures.find_one = AsyncMock(return_value=doc)
    db.nsw_commission_disclosures.insert_one = AsyncMock(return_value=MagicMock(inserted_id="oid-1"))
    db.nsw_commission_disclosures.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    db.nsw_commission_disclosures.count_documents = AsyncMock(return_value=1 if doc else 0)
    # strata hub returns
    cursor_hub = MagicMock()
    cursor_hub.sort = MagicMock(return_value=cursor_hub)
    cursor_hub.skip = MagicMock(return_value=cursor_hub)
    cursor_hub.limit = MagicMock(return_value=cursor_hub)
    cursor_hub.to_list = AsyncMock(return_value=docs if docs is not None else ([doc] if doc else []))
    db.nsw_strata_hub_returns.find.return_value = cursor_hub
    db.nsw_strata_hub_returns.find_one = AsyncMock(return_value=doc)
    db.nsw_strata_hub_returns.insert_one = AsyncMock(return_value=MagicMock(inserted_id="oid-2"))
    db.nsw_strata_hub_returns.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    db.nsw_strata_hub_returns.count_documents = AsyncMock(return_value=1 if doc else 0)
    # audit log
    db.audit_logs.insert_one = AsyncMock(return_value=MagicMock(inserted_id="oid-audit"))
    return db


# ═══════════════════════════════════════════════════════════════════════════════
# GAP-JUR-NSW-007: Insurance Commission Disclosures
# ═══════════════════════════════════════════════════════════════════════════════

class TestCreateCommissionDisclosure:
    @pytest.mark.asyncio
    async def test_creates_disclosure_and_returns_201(self):
        set_ctx_building_id("13195")
        from routers.nsw_compliance import create_commission_disclosure, CommissionDisclosureCreate

        db = _mock_db()
        payload = CommissionDisclosureCreate(
            building_id="13195",
            disclosure_type="commission",
            insurer_name="Zurich Australia",
            premium_cents=250000,
            commission_amount_cents=12500,
            commission_rate_pct=5.0,
            recipient_name="Smith Strata Management",
            disclosed_to="owners_corporation",
        )
        with patch("routers.nsw_compliance._get_db", return_value=db):
            result = await create_commission_disclosure(
                payload=payload,
                building_id="13195",
                current_user=_user(),
            )

        assert result["building_id"] == "13195"
        assert result["insurer_name"] == "Zurich Australia"
        assert result["disclosure_type"] == "commission"
        assert result["commission_rate_pct"] == 5.0
        assert result["cancelled_at"] is None
        db.nsw_commission_disclosures.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_requires_manager_role(self):
        set_ctx_building_id("13195")
        from routers.nsw_compliance import create_commission_disclosure, CommissionDisclosureCreate
        from fastapi import HTTPException

        payload = CommissionDisclosureCreate(
            building_id="13195",
            disclosure_type="commission",
            insurer_name="Insurer",
            recipient_name="Agent",
            disclosed_to="owners_corporation",
        )
        with pytest.raises(HTTPException) as exc_info:
            await create_commission_disclosure(
                payload=payload,
                building_id="13195",
                current_user=_user(role="owner"),
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_multi_tenant_isolation_building_id_set_from_dependency(self):
        """building_id comes from Depends(get_current_building), not payload."""
        set_ctx_building_id("16244")
        from routers.nsw_compliance import create_commission_disclosure, CommissionDisclosureCreate

        db = _mock_db()
        payload = CommissionDisclosureCreate(
            building_id="16244",
            disclosure_type="gift",
            insurer_name="QBE Insurance",
            recipient_name="Manager B",
            disclosed_to="ec",
        )
        with patch("routers.nsw_compliance._get_db", return_value=db):
            result = await create_commission_disclosure(
                payload=payload,
                building_id="16244",
                current_user=_user(role="super_admin"),
            )
        assert result["building_id"] == "16244"

    @pytest.mark.asyncio
    async def test_ec_member_can_create(self):
        set_ctx_building_id("13195")
        from routers.nsw_compliance import create_commission_disclosure, CommissionDisclosureCreate

        db = _mock_db()
        payload = CommissionDisclosureCreate(
            building_id="13195",
            disclosure_type="rebate",
            insurer_name="Allianz",
            recipient_name="EC Chair",
            disclosed_to="agm",
        )
        with patch("routers.nsw_compliance._get_db", return_value=db):
            result = await create_commission_disclosure(
                payload=payload,
                building_id="13195",
                current_user=_user(role="ec_member"),
            )
        assert result["building_id"] == "13195"


class TestListCommissionDisclosures:
    @pytest.mark.asyncio
    async def test_lists_active_disclosures(self):
        set_ctx_building_id("13195")
        from routers.nsw_compliance import list_commission_disclosures

        disc = _disclosure()
        db = _mock_db(docs=[disc])
        with patch("routers.nsw_compliance._get_db", return_value=db):
            result = await list_commission_disclosures(
                building_id="13195",
                current_user=_user(),
                skip=0, limit=50, include_cancelled=False,
            )
        assert result["total"] >= 0
        assert "items" in result

    @pytest.mark.asyncio
    async def test_guest_cannot_list(self):
        set_ctx_building_id("13195")
        from routers.nsw_compliance import list_commission_disclosures
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await list_commission_disclosures(
                building_id="13195",
                current_user=_user(role="guest"),
                skip=0, limit=50, include_cancelled=False,
            )
        assert exc_info.value.status_code == 403


class TestGetCommissionDisclosure:
    @pytest.mark.asyncio
    async def test_returns_disclosure(self):
        set_ctx_building_id("13195")
        from routers.nsw_compliance import get_commission_disclosure

        disc = _disclosure()
        db = _mock_db(doc=disc)
        with patch("routers.nsw_compliance._get_db", return_value=db):
            result = await get_commission_disclosure(
                disclosure_id="disc-1",
                building_id="13195",
                current_user=_user(),
            )
        assert result["id"] == "disc-1"

    @pytest.mark.asyncio
    async def test_returns_404_when_not_found(self):
        set_ctx_building_id("13195")
        from routers.nsw_compliance import get_commission_disclosure
        from fastapi import HTTPException

        db = _mock_db(doc=None)
        with patch("routers.nsw_compliance._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc_info:
                await get_commission_disclosure(
                    disclosure_id="missing",
                    building_id="13195",
                    current_user=_user(),
                )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_cannot_access_other_buildings_disclosure(self):
        """find_one receives building_id="13195"; if doc has building_id="16244" it's
        not returned (TenantScopedDatabase enforces this at query level)."""
        set_ctx_building_id("13195")
        from routers.nsw_compliance import get_commission_disclosure
        from fastapi import HTTPException

        # Simulate cross-tenant query returning nothing (TenantScopedDatabase prevents it)
        db = _mock_db(doc=None)
        with patch("routers.nsw_compliance._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc_info:
                await get_commission_disclosure(
                    disclosure_id="disc-other-building",
                    building_id="13195",
                    current_user=_user(),
                )
        assert exc_info.value.status_code == 404


class TestUpdateCommissionDisclosure:
    @pytest.mark.asyncio
    async def test_updates_notes(self):
        set_ctx_building_id("13195")
        from routers.nsw_compliance import update_commission_disclosure, CommissionDisclosureUpdate

        disc = _disclosure()
        db = _mock_db(doc=disc)
        payload = CommissionDisclosureUpdate(notes="Disclosed at AGM on 2026-05-01")
        with patch("routers.nsw_compliance._get_db", return_value=db):
            result = await update_commission_disclosure(
                disclosure_id="disc-1",
                payload=payload,
                building_id="13195",
                current_user=_user(),
            )
        db.nsw_commission_disclosures.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_cannot_update_cancelled_disclosure(self):
        set_ctx_building_id("13195")
        from routers.nsw_compliance import update_commission_disclosure, CommissionDisclosureUpdate
        from fastapi import HTTPException

        disc = _disclosure(cancelled=True)
        db = _mock_db(doc=disc)
        payload = CommissionDisclosureUpdate(notes="Trying to update cancelled")
        with patch("routers.nsw_compliance._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc_info:
                await update_commission_disclosure(
                    disclosure_id="disc-1",
                    payload=payload,
                    building_id="13195",
                    current_user=_user(),
                )
        assert exc_info.value.status_code == 409


class TestCancelCommissionDisclosure:
    @pytest.mark.asyncio
    async def test_soft_cancels_disclosure(self):
        set_ctx_building_id("13195")
        from routers.nsw_compliance import cancel_commission_disclosure

        disc = _disclosure()
        db = _mock_db(doc=disc)
        with patch("routers.nsw_compliance._get_db", return_value=db):
            await cancel_commission_disclosure(
                disclosure_id="disc-1",
                building_id="13195",
                current_user=_user(),
            )
        call_args = db.nsw_commission_disclosures.update_one.call_args
        update_doc = call_args[0][1]
        assert "cancelled_at" in update_doc["$set"]
        assert update_doc["$set"]["cancelled_at"] is not None

    @pytest.mark.asyncio
    async def test_hard_delete_is_never_called(self):
        set_ctx_building_id("13195")
        from routers.nsw_compliance import cancel_commission_disclosure

        disc = _disclosure()
        db = _mock_db(doc=disc)
        db.nsw_commission_disclosures.delete_one = AsyncMock()
        with patch("routers.nsw_compliance._get_db", return_value=db):
            await cancel_commission_disclosure(
                disclosure_id="disc-1",
                building_id="13195",
                current_user=_user(),
            )
        db.nsw_commission_disclosures.delete_one.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# GAP-JUR-NSW-008: Strata Hub Annual Returns
# ═══════════════════════════════════════════════════════════════════════════════

class TestCreateStrataHubReturn:
    @pytest.mark.asyncio
    async def test_creates_return_record(self):
        set_ctx_building_id("13195")
        from routers.nsw_compliance import create_strata_hub_return, StrataHubReturnCreate

        db = _mock_db()
        payload = StrataHubReturnCreate(
            building_id="13195",
            financial_year_end="2025-12-31",
            submission_method="portal",
            submitted_by_name="Alice Manager",
            submission_reference="SH-2026-001234",
            lot_count=81,
            total_levies_cents=34087002,
            status="submitted",
        )
        with patch("routers.nsw_compliance._get_db", return_value=db):
            result = await create_strata_hub_return(
                payload=payload,
                building_id="13195",
                current_user=_user(),
            )
        assert result["building_id"] == "13195"
        assert result["financial_year_end"] == "2025-12-31"
        assert result["lot_count"] == 81
        assert result["status"] == "submitted"
        db.nsw_strata_hub_returns.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_owner_cannot_create(self):
        set_ctx_building_id("13195")
        from routers.nsw_compliance import create_strata_hub_return, StrataHubReturnCreate
        from fastapi import HTTPException

        payload = StrataHubReturnCreate(
            building_id="13195",
            financial_year_end="2025-12-31",
            submission_method="manual",
            submitted_by_name="Owner A",
        )
        with pytest.raises(HTTPException) as exc_info:
            await create_strata_hub_return(
                payload=payload,
                building_id="13195",
                current_user=_user(role="owner"),
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_submission_method_validated(self):
        """Invalid submission_method rejected by Pydantic at model creation."""
        from routers.nsw_compliance import StrataHubReturnCreate
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            StrataHubReturnCreate(
                building_id="13195",
                financial_year_end="2025-12-31",
                submission_method="fax",  # invalid
                submitted_by_name="Someone",
            )

    @pytest.mark.asyncio
    async def test_multi_tenant_isolation(self):
        set_ctx_building_id("16244")
        from routers.nsw_compliance import create_strata_hub_return, StrataHubReturnCreate

        db = _mock_db()
        payload = StrataHubReturnCreate(
            building_id="16244",
            financial_year_end="2025-06-30",
            submission_method="manual",
            submitted_by_name="Bob Admin",
        )
        with patch("routers.nsw_compliance._get_db", return_value=db):
            result = await create_strata_hub_return(
                payload=payload,
                building_id="16244",
                current_user=_user(role="super_admin", uid="admin-1"),
            )
        assert result["building_id"] == "16244"


class TestListStrataHubReturns:
    @pytest.mark.asyncio
    async def test_returns_paginated_list(self):
        set_ctx_building_id("13195")
        from routers.nsw_compliance import list_strata_hub_returns

        ret = _hub_return()
        db = _mock_db(docs=[ret])
        with patch("routers.nsw_compliance._get_db", return_value=db):
            result = await list_strata_hub_returns(
                building_id="13195",
                current_user=_user(),
                skip=0, limit=20,
            )
        assert "total" in result
        assert "items" in result


class TestGetStrataHubReturn:
    @pytest.mark.asyncio
    async def test_returns_record(self):
        set_ctx_building_id("13195")
        from routers.nsw_compliance import get_strata_hub_return

        ret = _hub_return()
        db = _mock_db(doc=ret)
        with patch("routers.nsw_compliance._get_db", return_value=db):
            result = await get_strata_hub_return(
                return_id="ret-1",
                building_id="13195",
                current_user=_user(),
            )
        assert result["id"] == "ret-1"
        assert result["financial_year_end"] == "2025-12-31"

    @pytest.mark.asyncio
    async def test_returns_404_not_found(self):
        set_ctx_building_id("13195")
        from routers.nsw_compliance import get_strata_hub_return
        from fastapi import HTTPException

        db = _mock_db(doc=None)
        with patch("routers.nsw_compliance._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc_info:
                await get_strata_hub_return(
                    return_id="missing",
                    building_id="13195",
                    current_user=_user(),
                )
        assert exc_info.value.status_code == 404


class TestUpdateStrataHubReturn:
    @pytest.mark.asyncio
    async def test_adds_confirmation_reference(self):
        set_ctx_building_id("13195")
        from routers.nsw_compliance import update_strata_hub_return, StrataHubReturnUpdate

        ret = _hub_return()
        db = _mock_db(doc=ret)
        payload = StrataHubReturnUpdate(submission_reference="SH-2026-999")
        with patch("routers.nsw_compliance._get_db", return_value=db):
            result = await update_strata_hub_return(
                return_id="ret-1",
                payload=payload,
                building_id="13195",
                current_user=_user(),
            )
        db.nsw_strata_hub_returns.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_status_rejected_by_pydantic(self):
        from routers.nsw_compliance import StrataHubReturnUpdate
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            StrataHubReturnUpdate(status="cancelled")  # not in allowed enum


# ── Feature toggle enforcement ─────────────────────────────────────────────────

class TestFeatureToggleEnforcement:
    """Verify NSW create audit logs include tenant-scoped building_id."""

    @pytest.mark.asyncio
    async def test_strata_hub_return_create_audit_log_includes_building_id(self):
        """Audit log for strata_hub_return_created must include building_id."""
        set_ctx_building_id("13195")
        from routers.nsw_compliance import create_strata_hub_return, StrataHubReturnCreate

        payload = StrataHubReturnCreate(
            building_id="13195",
            financial_year_end="2025-12-31",
            submission_method="portal",
            submitted_by_name="Jane Manager",
            lot_count=14,
            total_levies_cents=44087002,
        )
        db = _mock_db()
        with patch("routers.nsw_compliance._get_db", return_value=db), \
             patch("routers.nsw_compliance.create_audit_log", new_callable=AsyncMock) as mock_audit:
            await create_strata_hub_return(
                payload=payload,
                building_id="13195",
                current_user=_user(),
            )

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs.get("building_id") == "13195", (
            "Audit log must include building_id for tenant-scoped forensics"
        )

    @pytest.mark.asyncio
    async def test_commission_disclosure_create_audit_log_includes_building_id(self):
        """Audit log for nsw_commission_disclosure_created must include building_id."""
        set_ctx_building_id("13195")
        from routers.nsw_compliance import create_commission_disclosure, CommissionDisclosureCreate

        payload = CommissionDisclosureCreate(
            building_id="13195",
            disclosure_type="commission",
            insurer_name="Zurich",
            policy_type="building",
            premium_cents=200000,
            commission_amount_cents=10000,
            commission_rate_pct=5.0,
            recipient_name="Smith Strata",
        )
        db = _mock_db()
        with patch("routers.nsw_compliance._get_db", return_value=db), \
             patch("routers.nsw_compliance.create_audit_log", new_callable=AsyncMock) as mock_audit:
            await create_commission_disclosure(
                payload=payload,
                building_id="13195",
                current_user=_user(),
            )

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs.get("building_id") == "13195"
