# @featuretrace:building-manager-duties — Tests for GAP-JUR-NSW-013 building manager duties register (building-scoped).
# Layer: test
# Data flow: tests → backend/routers/building_manager_duties.py → building_manager_duties collection.
# Related: backend/routers/building_manager_duties.py
#           backend/routers/nsw_compliance.py (NSW compliance hub)
#           backend/seeds/feature_toggles.py (building_manager_duties toggle)
"""
Tests for GAP-JUR-NSW-013: Building Manager Duties Register.

router: backend/routers/building_manager_duties.py
collection: building_manager_duties (tenant-scoped)
toggle: building_manager_duties
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException


# ── Fixtures ──────────────────────────────────────────────────────────────────

BUILDING_A = "13195"
BUILDING_B = "16244"


def _manager_user(building_id: str = BUILDING_A, role: str = "strata_manager") -> dict:
    return {"id": "usr-mgr-1", "full_name": "Test Manager", "role": role,
            "effective_role": role, "building_id": building_id}


def _ec_user(building_id: str = BUILDING_A) -> dict:
    return {"id": "usr-ec-1", "full_name": "EC Member", "role": "ec_member",
            "effective_role": "ec_member", "building_id": building_id}


def _duty_payload() -> dict:
    return {
        "manager_name": "John Building Manager",
        "manager_company": "ABC Building Services",
        "licence_number": "10012345",
        "licence_type": "class_2",
        "licence_expiry_date": "2027-06-30",
        "contract_start_date": "2026-01-01",
        "contract_end_date": None,
        "duties": [
            {
                "duty_category": "cleaning_common_property",
                "description": "Daily cleaning of lobbies and corridors",
                "frequency": "daily",
                "notes": None,
            },
            {
                "duty_category": "security_and_access",
                "description": "Monitor CCTV and manage access control",
                "frequency": "weekly",
                "notes": "Check logs every Monday",
            },
        ],
        "remuneration_amount_cents": 8400000,
        "remuneration_type": "annual",
        "insurance_certificate_attached": True,
        "notes": "Initial registration of duties under SSMA 2015 s.46B",
    }


def _make_db(docs: list | None = None) -> MagicMock:
    db = MagicMock()
    docs = docs or []

    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.skip = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=docs)

    db.building_manager_duties.find = MagicMock(return_value=cursor)
    db.building_manager_duties.find_one = AsyncMock(return_value=None)
    db.building_manager_duties.insert_one = AsyncMock(return_value=MagicMock(inserted_id="x"))
    db.building_manager_duties.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    db.building_manager_duties.update_many = AsyncMock()
    db.building_manager_duties.count_documents = AsyncMock(return_value=len(docs))
    return db


# ── Unit tests: role guards ───────────────────────────────────────────────────

class TestRoleGuards:
    def test_require_manager_passes_for_strata_manager(self):
        from routers.building_manager_duties import _require_manager
        user = _manager_user(role="strata_manager")
        assert _require_manager(user) == user

    def test_require_manager_passes_for_super_admin(self):
        from routers.building_manager_duties import _require_manager
        user = _manager_user(role="super_admin")
        assert _require_manager(user) == user

    def test_require_manager_rejects_owner(self):
        from routers.building_manager_duties import _require_manager
        with pytest.raises(HTTPException) as exc:
            _require_manager({"role": "owner", "effective_role": "owner"})
        assert exc.value.status_code == 403

    def test_require_ec_passes_for_ec_member(self):
        from routers.building_manager_duties import _require_ec
        user = _ec_user()
        assert _require_ec(user) == user

    def test_require_ec_rejects_guest(self):
        from routers.building_manager_duties import _require_ec
        with pytest.raises(HTTPException) as exc:
            _require_ec({"role": "guest", "effective_role": "guest"})
        assert exc.value.status_code == 403

    def test_effective_role_used_for_elevated_users(self):
        """Elevated owner (acting as ec_member) must pass the EC guard."""
        from routers.building_manager_duties import _require_ec
        user = {"role": "owner", "effective_role": "ec_member"}
        assert _require_ec(user) == user


# ── Integration-style tests: endpoint logic ───────────────────────────────────

class TestCreateDutiesEntry:
    @pytest.mark.asyncio
    async def test_creates_document_with_correct_fields(self):
        from routers.building_manager_duties import create_duties_entry
        from routers.building_manager_duties import BuildingManagerDutiesCreate

        db = _make_db()
        user = _manager_user()
        payload = BuildingManagerDutiesCreate(**_duty_payload())

        with patch("routers.building_manager_duties._get_db", return_value=db), \
             patch("routers.building_manager_duties.create_audit_log", new_callable=AsyncMock):
            result = await create_duties_entry(payload, building_id=BUILDING_A, current_user=user)

        assert result["building_id"] == BUILDING_A
        assert result["manager_name"] == "John Building Manager"
        assert result["review_status"] == "active"
        assert len(result["duties"]) == 2
        assert result["remuneration_amount_cents"] == 8400000
        db.building_manager_duties.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_rejects_owner_role(self):
        from routers.building_manager_duties import create_duties_entry, BuildingManagerDutiesCreate
        payload = BuildingManagerDutiesCreate(**_duty_payload())
        owner = {"role": "owner", "effective_role": "owner"}
        with pytest.raises(HTTPException) as exc:
            await create_duties_entry(payload, building_id=BUILDING_A, current_user=owner)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_building_id_injected_from_dependency(self):
        from routers.building_manager_duties import create_duties_entry, BuildingManagerDutiesCreate
        db = _make_db()
        payload = BuildingManagerDutiesCreate(**_duty_payload())
        user = _manager_user(building_id=BUILDING_B)

        with patch("routers.building_manager_duties._get_db", return_value=db), \
             patch("routers.building_manager_duties.create_audit_log", new_callable=AsyncMock):
            result = await create_duties_entry(payload, building_id=BUILDING_B, current_user=user)

        assert result["building_id"] == BUILDING_B

    @pytest.mark.asyncio
    async def test_audit_log_called_on_create(self):
        from routers.building_manager_duties import create_duties_entry, BuildingManagerDutiesCreate
        db = _make_db()
        payload = BuildingManagerDutiesCreate(**_duty_payload())
        user = _manager_user()

        with patch("routers.building_manager_duties._get_db", return_value=db), \
             patch("routers.building_manager_duties.create_audit_log", new_callable=AsyncMock) as mock_audit:
            await create_duties_entry(payload, building_id=BUILDING_A, current_user=user)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs["action"] == "building_manager_duties_created"
        assert call_kwargs["building_id"] == BUILDING_A


class TestListDutiesEntries:
    @pytest.mark.asyncio
    async def test_returns_paginated_results(self):
        from routers.building_manager_duties import list_duties_entries
        doc = {"id": "d-1", "building_id": BUILDING_A, "manager_name": "Jane", "review_status": "active"}
        db = _make_db(docs=[doc])
        user = _ec_user()

        with patch("routers.building_manager_duties._get_db", return_value=db):
            result = await list_duties_entries(
                building_id=BUILDING_A, current_user=user, skip=0, limit=20, review_status=None
            )

        assert result["total"] == 1
        assert len(result["items"]) == 1

    @pytest.mark.asyncio
    async def test_multi_tenant_isolation(self):
        """Data for building A must not appear for building B."""
        from routers.building_manager_duties import list_duties_entries
        db = _make_db(docs=[])
        user_b = _ec_user(building_id=BUILDING_B)

        with patch("routers.building_manager_duties._get_db", return_value=db):
            result = await list_duties_entries(
                building_id=BUILDING_B, current_user=user_b, skip=0, limit=20, review_status=None
            )

        find_call_filter = db.building_manager_duties.find.call_args[0][0]
        assert find_call_filter["building_id"] == BUILDING_B

    @pytest.mark.asyncio
    async def test_filters_by_review_status(self):
        from routers.building_manager_duties import list_duties_entries
        db = _make_db(docs=[])
        user = _ec_user()

        with patch("routers.building_manager_duties._get_db", return_value=db):
            await list_duties_entries(
                building_id=BUILDING_A, current_user=user, skip=0, limit=20, review_status="active"
            )

        find_call_filter = db.building_manager_duties.find.call_args[0][0]
        assert find_call_filter.get("review_status") == "active"


class TestGetDutiesEntry:
    @pytest.mark.asyncio
    async def test_returns_404_when_not_found(self):
        from routers.building_manager_duties import get_duties_entry
        db = _make_db()
        db.building_manager_duties.find_one = AsyncMock(return_value=None)
        user = _ec_user()

        with patch("routers.building_manager_duties._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await get_duties_entry("nonexistent-id", building_id=BUILDING_A, current_user=user)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_document_when_found(self):
        from routers.building_manager_duties import get_duties_entry
        doc = {"id": "e-1", "building_id": BUILDING_A, "manager_name": "Test"}
        db = _make_db()
        db.building_manager_duties.find_one = AsyncMock(return_value=doc)
        user = _ec_user()

        with patch("routers.building_manager_duties._get_db", return_value=db):
            result = await get_duties_entry("e-1", building_id=BUILDING_A, current_user=user)

        assert result["id"] == "e-1"


def _make_summary_aggregate_mock(active: int = 0, expiring: int = 0, expired: int = 0, missing_ins: int = 0):
    """Return a mock aggregate cursor that emits one $facet document."""
    facet_doc = {
        "active_total": [{"n": active}] if active else [],
        "expiring_soon": [{"n": expiring}] if expiring else [],
        "expired_licences": [{"n": expired}] if expired else [],
        "missing_insurance": [{"n": missing_ins}] if missing_ins else [],
    }
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[facet_doc])
    return cursor


class TestComplianceSummary:
    @pytest.mark.asyncio
    async def test_no_entries_returns_not_compliant(self):
        from routers.building_manager_duties import get_duties_compliance_summary
        db = _make_db()
        # $facet aggregate returns empty buckets → no active entries
        db.building_manager_duties.aggregate = MagicMock(
            return_value=_make_summary_aggregate_mock(active=0)
        )
        user = _ec_user()

        with patch("routers.building_manager_duties._get_db", return_value=db):
            result = await get_duties_compliance_summary(building_id=BUILDING_A, current_user=user)

        assert result["has_register"] is False
        assert result["ssma_2015_s46b_compliant"] is False

    @pytest.mark.asyncio
    async def test_compliant_when_active_and_insured(self):
        from routers.building_manager_duties import get_duties_compliance_summary
        db = _make_db()
        # 1 active entry, 0 expiring, 0 expired, 0 missing insurance → compliant
        db.building_manager_duties.aggregate = MagicMock(
            return_value=_make_summary_aggregate_mock(active=1, expiring=0, expired=0, missing_ins=0)
        )
        user = _ec_user()

        with patch("routers.building_manager_duties._get_db", return_value=db):
            result = await get_duties_compliance_summary(building_id=BUILDING_A, current_user=user)

        assert result["has_register"] is True
        assert result["ssma_2015_s46b_compliant"] is True


class TestSoftCancel:
    @pytest.mark.asyncio
    async def test_cannot_cancel_already_cancelled(self):
        from routers.building_manager_duties import cancel_duties_entry
        doc = {"id": "e-1", "building_id": BUILDING_A, "cancelled_at": "2026-01-01T00:00:00Z"}
        db = _make_db()
        db.building_manager_duties.find_one = AsyncMock(return_value=doc)
        user = _manager_user()

        with patch("routers.building_manager_duties._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await cancel_duties_entry("e-1", building_id=BUILDING_A, current_user=user)
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_soft_cancel_sets_status_fields(self):
        from routers.building_manager_duties import cancel_duties_entry
        doc = {"id": "e-1", "building_id": BUILDING_A}
        db = _make_db()
        db.building_manager_duties.find_one = AsyncMock(return_value=doc)
        user = _manager_user()

        with patch("routers.building_manager_duties._get_db", return_value=db), \
             patch("routers.building_manager_duties.create_audit_log", new_callable=AsyncMock):
            await cancel_duties_entry("e-1", building_id=BUILDING_A, current_user=user)

        update_call = db.building_manager_duties.update_one.call_args
        set_fields = update_call[0][1]["$set"]
        assert set_fields["review_status"] == "cancelled"
        assert "cancelled_at" in set_fields
        assert "cancelled_by" in set_fields

    @pytest.mark.asyncio
    async def test_hard_delete_never_called(self):
        """Verify that delete_one is never called — 7-year retention."""
        from routers.building_manager_duties import cancel_duties_entry
        doc = {"id": "e-1", "building_id": BUILDING_A}
        db = _make_db()
        db.building_manager_duties.find_one = AsyncMock(return_value=doc)
        db.building_manager_duties.delete_one = AsyncMock()
        user = _manager_user()

        with patch("routers.building_manager_duties._get_db", return_value=db), \
             patch("routers.building_manager_duties.create_audit_log", new_callable=AsyncMock):
            await cancel_duties_entry("e-1", building_id=BUILDING_A, current_user=user)

        db.building_manager_duties.delete_one.assert_not_called()
