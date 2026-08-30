"""
Tests for insurance claims router.
All imports use `from routers.X import` (not `from backend.routers.X import`).
Patches use `patch("routers.X.Y")` (not `patch("backend.routers.X.Y")`).
"""
import uuid
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from models.insurance_claim import InsuranceClaimCreate, InsuranceClaimStatus
from models.user import UserRole
from routers.insurance_claims import (
    create_insurance_claim,
    get_insurance_claims,
    get_insurance_claim,
    update_claim_status,
)

BUILDING_ID = "13195"


def _make_user(role=UserRole.SUPER_ADMIN, **kwargs):
    return {"id": str(uuid.uuid4()), "full_name": "Test User", "role": role, **kwargs}


def _make_claim_doc(claim_id=None, **kwargs):
    cid = claim_id or str(uuid.uuid4())
    return {
        "id": cid,
        "building_id": BUILDING_ID,
        "claim_number": "CLM-2026-001",
        "insurer": "Zurich Insurance",
        "incident_date": "2026-02-01",
        "description": "Water damage in lobby",
        "status": InsuranceClaimStatus.REPORTED,
        "created_by": "user-1",
        "created_at": "2026-02-01T00:00:00Z",
        "updated_at": "2026-02-01T00:00:00Z",
        **kwargs,
    }


def _make_create_data(**kwargs):
    return InsuranceClaimCreate(
        building_id=BUILDING_ID,
        claim_number="CLM-2026-001",
        insurer="Zurich Insurance",
        incident_date="2026-02-01",
        description="Water damage in lobby",
        **kwargs,
    )


def _mock_db_for_claims(claim_doc=None):
    db = MagicMock()
    db.insurance_claims.insert_one = AsyncMock(return_value=MagicMock(inserted_id="x"))
    db.insurance_claims.find_one = AsyncMock(return_value=claim_doc)
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=[])
    db.insurance_claims.find.return_value = cursor
    db.insurance_claims.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    return db


class TestCreateInsuranceClaim:
    @pytest.mark.asyncio
    async def test_creates_claim_successfully(self):
        data = _make_create_data()
        user = _make_user()
        mock_db = _mock_db_for_claims()

        with patch("routers.insurance_claims._get_db", return_value=mock_db), \
                patch("routers.insurance_claims.get_user_permissions") as mock_perms, \
                patch("routers.insurance_claims.create_audit_log", new_callable=AsyncMock):
            mock_perms.return_value = MagicMock(can_manage_finances=True)
            result = await create_insurance_claim(data, current_user=user, building_id=BUILDING_ID)

            assert result.claim_number == "CLM-2026-001"
            assert result.insurer == "Zurich Insurance"
            assert result.status == InsuranceClaimStatus.REPORTED
            mock_db.insurance_claims.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_created_by_is_current_user(self):
        data = _make_create_data()
        user = _make_user()
        mock_db = _mock_db_for_claims()

        with patch("routers.insurance_claims._get_db", return_value=mock_db), \
                patch("routers.insurance_claims.get_user_permissions") as mock_perms, \
                patch("routers.insurance_claims.create_audit_log", new_callable=AsyncMock):
            mock_perms.return_value = MagicMock(can_manage_finances=True)
            result = await create_insurance_claim(data, current_user=user, building_id=BUILDING_ID)

            assert result.created_by == user["id"]

    @pytest.mark.asyncio
    async def test_unauthorized_user_raises_403(self):
        from fastapi import HTTPException
        data = _make_create_data()
        user = _make_user(role=UserRole.OWNER)

        with patch("routers.insurance_claims.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_finances=False)

            with pytest.raises(HTTPException) as exc:
                await create_insurance_claim(data, current_user=user, building_id=BUILDING_ID)

            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_status_defaults_to_reported(self):
        data = _make_create_data()
        user = _make_user()
        mock_db = _mock_db_for_claims()

        with patch("routers.insurance_claims._get_db", return_value=mock_db), \
                patch("routers.insurance_claims.get_user_permissions") as mock_perms, \
                patch("routers.insurance_claims.create_audit_log", new_callable=AsyncMock):
            mock_perms.return_value = MagicMock(can_manage_finances=True)
            result = await create_insurance_claim(data, current_user=user, building_id=BUILDING_ID)

            assert result.status == InsuranceClaimStatus.REPORTED

    @pytest.mark.asyncio
    async def test_audit_log_is_created(self):
        data = _make_create_data()
        user = _make_user()
        mock_db = _mock_db_for_claims()

        with patch("routers.insurance_claims._get_db", return_value=mock_db), \
                patch("routers.insurance_claims.get_user_permissions") as mock_perms, \
                patch("routers.insurance_claims.create_audit_log", new_callable=AsyncMock) as mock_audit:
            mock_perms.return_value = MagicMock(can_manage_finances=True)
            await create_insurance_claim(data, current_user=user, building_id=BUILDING_ID)

            mock_audit.assert_called_once()
            # New router uses positional args: (action, resource_type, resource_id, ...)
            call_args = mock_audit.call_args[0]
            assert call_args[0] == "created"
            assert call_args[1] == "insurance_claim"


class TestGetInsuranceClaims:
    @pytest.mark.asyncio
    async def test_returns_list_of_claims(self):
        user = _make_user()
        claim1 = _make_claim_doc()
        claim2 = _make_claim_doc(claim_number="CLM-2026-002")
        mock_db = _mock_db_for_claims()
        mock_cursor = MagicMock()
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=[claim1, claim2])
        mock_db.insurance_claims.find.return_value = mock_cursor

        with patch("routers.insurance_claims._get_db", return_value=mock_db), \
                patch("routers.insurance_claims.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_view_finances=True)
            result = await get_insurance_claims(current_user=user, building_id=BUILDING_ID)

            assert len(result) == 2
            assert result[0].claim_number == "CLM-2026-001"

    @pytest.mark.asyncio
    async def test_unauthorized_raises_403(self):
        from fastapi import HTTPException
        user = _make_user(role=UserRole.TENANT)

        with patch("routers.insurance_claims.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_view_finances=False)

            with pytest.raises(HTTPException) as exc:
                await get_insurance_claims(current_user=user, building_id=BUILDING_ID)

            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_claims(self):
        user = _make_user()
        mock_db = _mock_db_for_claims()
        mock_cursor = MagicMock()
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_db.insurance_claims.find.return_value = mock_cursor

        with patch("routers.insurance_claims._get_db", return_value=mock_db), \
                patch("routers.insurance_claims.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_view_finances=True)
            result = await get_insurance_claims(current_user=user, building_id=BUILDING_ID)

            assert result == []


class TestGetInsuranceClaim:
    @pytest.mark.asyncio
    async def test_returns_claim_by_id(self):
        user = _make_user()
        cid = str(uuid.uuid4())
        doc = _make_claim_doc(claim_id=cid)
        mock_db = _mock_db_for_claims(claim_doc=doc)

        with patch("routers.insurance_claims._get_db", return_value=mock_db), \
                patch("routers.insurance_claims.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_view_finances=True)
            result = await get_insurance_claim(cid, current_user=user, building_id=BUILDING_ID)

            assert result.id == cid
            mock_db.insurance_claims.find_one.assert_called_once_with(
                {"id": cid, "building_id": BUILDING_ID}, {"_id": 0}
            )

    @pytest.mark.asyncio
    async def test_missing_claim_raises_404(self):
        from fastapi import HTTPException
        user = _make_user()
        mock_db = _mock_db_for_claims(claim_doc=None)

        with patch("routers.insurance_claims._get_db", return_value=mock_db), \
                patch("routers.insurance_claims.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_view_finances=True)

            with pytest.raises(HTTPException) as exc:
                await get_insurance_claim("nonexistent", current_user=user, building_id=BUILDING_ID)

            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unauthorized_raises_403(self):
        from fastapi import HTTPException
        user = _make_user(role=UserRole.TENANT)

        with patch("routers.insurance_claims.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_view_finances=False)

            with pytest.raises(HTTPException) as exc:
                await get_insurance_claim("any-id", current_user=user, building_id=BUILDING_ID)

            assert exc.value.status_code == 403


class TestUpdateClaimStatus:
    @pytest.mark.asyncio
    async def test_updates_status_successfully(self):
        user = _make_user()
        cid = str(uuid.uuid4())
        mock_db = _mock_db_for_claims()
        mock_db.insurance_claims.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

        with patch("routers.insurance_claims._get_db", return_value=mock_db), \
                patch("routers.insurance_claims.get_user_permissions") as mock_perms, \
                patch("routers.insurance_claims.create_audit_log", new_callable=AsyncMock):
            mock_perms.return_value = MagicMock(can_manage_finances=True)
            result = await update_claim_status(
                cid, InsuranceClaimStatus.UNDER_REVIEW, current_user=user, building_id=BUILDING_ID
            )

            mock_db.insurance_claims.update_one.assert_called_once()
            assert "updated" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_missing_claim_raises_404(self):
        from fastapi import HTTPException
        user = _make_user()
        mock_db = _mock_db_for_claims()
        mock_db.insurance_claims.update_one = AsyncMock(return_value=MagicMock(matched_count=0))

        with patch("routers.insurance_claims._get_db", return_value=mock_db), \
                patch("routers.insurance_claims.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_finances=True)

            with pytest.raises(HTTPException) as exc:
                await update_claim_status(
                    "nonexistent", InsuranceClaimStatus.UNDER_REVIEW, current_user=user, building_id=BUILDING_ID
                )

            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unauthorized_raises_403(self):
        from fastapi import HTTPException
        user = _make_user(role=UserRole.OWNER)

        with patch("routers.insurance_claims.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_finances=False)

            with pytest.raises(HTTPException) as exc:
                await update_claim_status(
                    "any-id", InsuranceClaimStatus.UNDER_REVIEW, current_user=user, building_id=BUILDING_ID
                )

            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_invalid_status_raises_400(self):
        from fastapi import HTTPException
        user = _make_user()

        with patch("routers.insurance_claims.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_finances=True)

            with pytest.raises(HTTPException) as exc:
                await update_claim_status(
                    "any-id", "invalid_status", current_user=user, building_id=BUILDING_ID
                )

            assert exc.value.status_code == 400
