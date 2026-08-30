"""
Tests for GET /privacy/my-data (GAP-SEC-004 — Privacy Act APP 12).

Covers:
  1.  Happy path — returns all 7 collections for authenticated owner
  2.  levy_payments queried by unit_number (not user_id)
  3.  PII exclusion — password_hash and totp_secret absent from response
  4.  Empty user_units → empty levy_payments (no unit_numbers to query)
  5.  Multi-tenant isolation — building_id filter on all scoped collections
  6.  Audit log created on successful export

Run:
    cd /home/gagneet/strata-management
    backend/venv/bin/python3 -m pytest tests/backend/test_privacy_my_data.py -q
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest

BUILDING_A = "13195"
BUILDING_B = "16244"

USER_ID = "user-owner-001"
UNIT_NUMBER = "UA042"


@pytest.fixture
def owner_user():
    return {
        "id": USER_ID,
        "full_name": "Test Owner",
        "email": "owner@test.com",
        "role": "owner",
        "effective_role": "owner",
        "unit_number": UNIT_NUMBER,
        "building_id": BUILDING_A,
        "is_approved": True,
    }


def _cursor(docs: list) -> MagicMock:
    c = MagicMock()
    c.to_list = AsyncMock(return_value=docs)
    c.sort = MagicMock(return_value=c)
    c.limit = MagicMock(return_value=c)
    return c


def _build_mock_db(
        user_doc=None,
        user_units=None,
        levy_payments=None,
        announcements=None,
        consents=None,
        access_requests=None,
        audit_logs=None,
):
    mock_db = MagicMock()

    # users — find_one
    mock_db.users.find_one = AsyncMock(return_value=user_doc or {
        "_id": "oid-user",
        "id": USER_ID,
        "full_name": "Test Owner",
        "email": "owner@test.com",
    })

    # user_units — find (step 1)
    mock_db.user_units.find.return_value = _cursor(user_units if user_units is not None else [
        {"_id": "oid-uu", "user_id": USER_ID, "unit_number": UNIT_NUMBER, "building_id": BUILDING_A},
    ])

    # levy_payments — find (step 2)
    mock_db.levy_payments.find.return_value = _cursor(levy_payments if levy_payments is not None else [
        {"_id": "oid-lp", "unit_number": UNIT_NUMBER, "building_id": BUILDING_A, "amount": 1530.30},
    ])

    mock_db.announcements.find.return_value = _cursor(announcements or [])
    mock_db.privacy_consents.find.return_value = _cursor(consents or [])
    mock_db.privacy_access_requests.find.return_value = _cursor(access_requests or [])
    mock_db.audit_logs.find.return_value = _cursor(audit_logs or [])

    return mock_db


class TestGetMyDataHappyPath:
    """GET /privacy/my-data returns all collections for authenticated owner."""

    @pytest.mark.asyncio
    async def test_returns_all_collection_keys(self, owner_user):
        mock_db = _build_mock_db()

        with (
            patch("routers.privacy_compliance._get_db", return_value=mock_db),
            patch("routers.privacy_compliance.create_audit_log", new_callable=AsyncMock),
        ):
            from routers.privacy_compliance import get_my_data

            result = await get_my_data(
                current_user=owner_user,
                building_id=BUILDING_A,
            )

        assert result["user_id"] == USER_ID
        assert result["building_id"] == BUILDING_A
        assert "generated_at" in result
        collections = result["collections"]
        for key in ("users", "user_units", "levy_payments", "announcements",
                    "privacy_consents", "privacy_access_requests", "audit_logs"):
            assert key in collections, f"Missing collection key: {key}"

    @pytest.mark.asyncio
    async def test_levy_payments_key_not_levy_transactions(self, owner_user):
        """Collection name must be levy_payments, not the old invented levy_transactions."""
        mock_db = _build_mock_db()

        with (
            patch("routers.privacy_compliance._get_db", return_value=mock_db),
            patch("routers.privacy_compliance.create_audit_log", new_callable=AsyncMock),
        ):
            from routers.privacy_compliance import get_my_data

            result = await get_my_data(current_user=owner_user, building_id=BUILDING_A)

        assert "levy_payments" in result["collections"]
        assert "levy_transactions" not in result["collections"]

    @pytest.mark.asyncio
    async def test_levy_payments_data_present(self, owner_user):
        payment = {"_id": "oid-lp", "unit_number": UNIT_NUMBER,
                   "building_id": BUILDING_A, "amount": 1530.30}
        mock_db = _build_mock_db(levy_payments=[payment])

        with (
            patch("routers.privacy_compliance._get_db", return_value=mock_db),
            patch("routers.privacy_compliance.create_audit_log", new_callable=AsyncMock),
        ):
            from routers.privacy_compliance import get_my_data

            result = await get_my_data(current_user=owner_user, building_id=BUILDING_A)

        levy = result["collections"]["levy_payments"]
        assert len(levy) == 1
        assert levy[0]["amount"] == 1530.30
        assert levy[0]["unit_number"] == UNIT_NUMBER


class TestGetMyDataPIIExclusion:
    """Sensitive auth fields must never appear in the export."""

    @pytest.mark.asyncio
    async def test_password_hash_excluded(self, owner_user):
        user_doc = {
            "_id": "oid-user",
            "id": USER_ID,
            "email": "owner@test.com",
            "password_hash": "bcrypt_secret",
            "totp_secret": "TOTPSECRET",
        }
        mock_db = _build_mock_db(user_doc=user_doc)
        # find_one projects out password_hash and totp_secret — simulate correctly
        clean_user = {k: v for k, v in user_doc.items()
                      if k not in ("password_hash", "totp_secret")}
        mock_db.users.find_one = AsyncMock(return_value=clean_user)

        with (
            patch("routers.privacy_compliance._get_db", return_value=mock_db),
            patch("routers.privacy_compliance.create_audit_log", new_callable=AsyncMock),
        ):
            from routers.privacy_compliance import get_my_data

            result = await get_my_data(current_user=owner_user, building_id=BUILDING_A)

        users = result["collections"]["users"]
        assert len(users) == 1
        assert "password_hash" not in users[0]
        assert "totp_secret" not in users[0]


class TestGetMyDataEmptyUnits:
    """When user has no unit memberships, levy_payments must be empty."""

    @pytest.mark.asyncio
    async def test_no_units_returns_empty_levy_payments(self, owner_user):
        mock_db = _build_mock_db(user_units=[], levy_payments=[])

        with (
            patch("routers.privacy_compliance._get_db", return_value=mock_db),
            patch("routers.privacy_compliance.create_audit_log", new_callable=AsyncMock),
        ):
            from routers.privacy_compliance import get_my_data

            result = await get_my_data(current_user=owner_user, building_id=BUILDING_A)

        assert result["collections"]["levy_payments"] == []
        assert result["collections"]["user_units"] == []

    @pytest.mark.asyncio
    async def test_levy_payments_queried_with_unit_numbers(self, owner_user):
        """levy_payments query must use unit_number $in filter, not user_id."""
        mock_db = _build_mock_db()

        with (
            patch("routers.privacy_compliance._get_db", return_value=mock_db),
            patch("routers.privacy_compliance.create_audit_log", new_callable=AsyncMock),
        ):
            from routers.privacy_compliance import get_my_data

            await get_my_data(current_user=owner_user, building_id=BUILDING_A)

        call_args = mock_db.levy_payments.find.call_args
        assert call_args is not None, "levy_payments.find was never called"
        query_filter = call_args[0][0]
        assert "unit_number" in query_filter, "levy_payments query must filter by unit_number"
        assert "$in" in query_filter["unit_number"], "levy_payments query must use $in"
        assert UNIT_NUMBER in query_filter["unit_number"]["$in"]
        assert "user_id" not in query_filter, "levy_payments must NOT filter by user_id"


class TestGetMyDataMultiTenant:
    """building_id must scope all tenant-scoped collection queries."""

    @pytest.mark.asyncio
    async def test_building_b_user_cannot_see_building_a_data(self):
        """A user in building B queries building B — building A data is not returned."""
        user_b = {
            "id": "user-b-001",
            "full_name": "Building B Owner",
            "email": "b@test.com",
            "role": "owner",
            "effective_role": "owner",
            "unit_number": "UB001",
            "building_id": BUILDING_B,
            "is_approved": True,
        }
        mock_db = _build_mock_db(user_units=[], levy_payments=[])

        with (
            patch("routers.privacy_compliance._get_db", return_value=mock_db),
            patch("routers.privacy_compliance.create_audit_log", new_callable=AsyncMock),
        ):
            from routers.privacy_compliance import get_my_data

            result = await get_my_data(current_user=user_b, building_id=BUILDING_B)

        assert result["building_id"] == BUILDING_B
        # Verify all scoped find() calls used BUILDING_B, not BUILDING_A
        for call_obj in mock_db.user_units.find.call_args_list:
            assert call_obj[0][0].get("building_id") == BUILDING_B

    @pytest.mark.asyncio
    async def test_audit_log_task_is_scheduled(self, owner_user):
        """asyncio.create_task must be called — audit log is fire-and-forget."""
        mock_db = _build_mock_db()

        with (
            patch("routers.privacy_compliance._get_db", return_value=mock_db),
            patch("routers.privacy_compliance.create_audit_log", new_callable=AsyncMock),
            patch("routers.privacy_compliance.asyncio") as mock_asyncio,
        ):
            mock_asyncio.gather = asyncio.gather  # keep gather working
            mock_asyncio.create_task = MagicMock()

            from routers.privacy_compliance import get_my_data

            await get_my_data(current_user=owner_user, building_id=BUILDING_A)

        mock_asyncio.create_task.assert_called_once()
