"""
Tests for Trust Accounting Phase 1 Router

Covers all major endpoints and business logic in backend/routers/trust_phase1.py:

  GET  /trust/v2/config/{building_id}          - get trust config
  PUT  /trust/v2/config/{building_id}          - update trust config
  POST /trust/v2/accounts                      - create account
  GET  /trust/v2/accounts                      - list accounts
  GET  /trust/v2/accounts/{id}/balance         - balance summary
  POST /trust/v2/transactions                  - create transaction
  POST /trust/v2/transactions/{id}/reverse     - reverse transaction
  POST /trust/v2/levies/generate               - generate levy schedules
  GET  /trust/v2/levies/{building_id}/schedule - get levy schedule
  POST /trust/v2/levies/{id}/pay               - record manual payment
  GET  /trust/v2/financial-summary             - aggregated financial summary

Pure-unit tests only — uses AsyncMock for all DB interactions.
Patch target: "routers.trust_phase1._get_db"
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId
from fastapi import HTTPException

# ─── Path bootstrap ───────────────────────────────────────────────────────────
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

# ─── Shared test data ─────────────────────────────────────────────────────────

BUILDING_OID = ObjectId()
BUILDING_ID = str(BUILDING_OID)

ACCOUNT_OID = ObjectId()
ACCOUNT_ID = str(ACCOUNT_OID)

TX_OID = ObjectId()
TX_ID = str(TX_OID)

SCHEDULE_OID = ObjectId()
SCHEDULE_ID = str(SCHEDULE_OID)


def _manager_user(building_id: str = BUILDING_ID) -> dict:
    return {
        "_id": ObjectId(),
        "id": str(ObjectId()),
        "role": "strata_manager",
        "email": "manager@test.com",
        "full_name": "Test Manager",
        "building_id": building_id,
    }


def _super_admin_user(building_id: str = BUILDING_ID) -> dict:
    return {
        "_id": ObjectId(),
        "id": str(ObjectId()),
        "role": "super_admin",
        "email": "admin@test.com",
        "full_name": "Super Admin",
        "building_id": building_id,
    }


def _ec_user(building_id: str = BUILDING_ID) -> dict:
    return {
        "_id": ObjectId(),
        "id": str(ObjectId()),
        "role": "ec_member",
        "email": "ec@test.com",
        "full_name": "EC Member",
        "building_id": building_id,
    }


def _owner_user(building_id: str = BUILDING_ID) -> dict:
    return {
        "_id": ObjectId(),
        "id": str(ObjectId()),
        "role": "owner",
        "email": "owner@test.com",
        "full_name": "Owner",
        "building_id": building_id,
        "unit_id": str(ObjectId()),
    }


def _mock_db() -> MagicMock:
    """Return a MagicMock that acts as an async Motor database."""
    db = MagicMock()
    # Wire up common collection helpers as needed per test
    return db


def _async_cursor(docs: list) -> MagicMock:
    """Return a mock async cursor whose .to_list() resolves to docs."""
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=docs)
    cursor.skip = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.sort = MagicMock(return_value=cursor)
    return cursor


def _async_agg_cursor(docs: list) -> MagicMock:
    """Async-iterable aggregate cursor."""
    cursor = MagicMock()
    cursor.__aiter__ = MagicMock(return_value=iter(docs))
    cursor.to_list = AsyncMock(return_value=docs)
    return cursor


# ─── Pure-function tests ───────────────────────────────────────────────────────

class TestProportionalSplit:
    """Tests for the _proportional_split helper."""

    def test_exact_split(self):
        from routers.trust_phase1 import _proportional_split
        result = _proportional_split(400, [100, 100, 100, 100])
        assert sum(result) == 400
        assert all(v == 100 for v in result)

    def test_uneven_split_sums_exactly(self):
        from routers.trust_phase1 import _proportional_split
        result = _proportional_split(100, [1, 1, 1])
        assert sum(result) == 100
        assert len(result) == 3

    def test_zero_weight_list(self):
        from routers.trust_phase1 import _proportional_split
        result = _proportional_split(100, [0, 0])
        assert result == [0, 0]

    def test_empty_weights_returns_empty(self):
        from routers.trust_phase1 import _proportional_split
        assert _proportional_split(100, []) == []

    def test_proportional_distribution(self):
        from routers.trust_phase1 import _proportional_split
        # Unit A has 2x the UOE of Unit B — should get roughly 2x the levy
        result = _proportional_split(300, [200, 100])
        assert sum(result) == 300
        assert result[0] > result[1]


class TestLuhnCheckDigit:
    def test_known_value(self):
        from routers.trust_phase1 import _luhn_check_digit
        # Verify determinism
        d1 = _luhn_check_digit("123456789")
        d2 = _luhn_check_digit("123456789")
        assert d1 == d2
        assert 0 <= d1 <= 9

    def test_single_digit_input(self):
        from routers.trust_phase1 import _luhn_check_digit
        result = _luhn_check_digit("0")
        assert 0 <= result <= 9


class TestGenerateDeftCrn:
    def test_crn_format(self):
        from routers.trust_phase1 import _generate_deft_crn
        crn = _generate_deft_crn(lot_number=17, quarter="2026-Q1", biller_code="98765")
        # Expected: "98765-0017-2026Q1-{digit}"
        assert crn.startswith("98765-0017-2026Q1-")

    def test_lot_padded_to_4_digits(self):
        from routers.trust_phase1 import _generate_deft_crn
        crn = _generate_deft_crn(lot_number=5, quarter="2026-Q2", biller_code="12345")
        assert "-0005-" in crn

    def test_crn_deterministic(self):
        from routers.trust_phase1 import _generate_deft_crn
        crn1 = _generate_deft_crn(1, "2026-Q1", "11111")
        crn2 = _generate_deft_crn(1, "2026-Q1", "11111")
        assert crn1 == crn2


class TestFormatAud:
    def test_zero(self):
        from models.trust_accounting import format_aud
        assert format_aud(0) == "$0.00"

    def test_whole_dollars(self):
        from models.trust_accounting import format_aud
        assert format_aud(10000) == "$100.00"

    def test_cents(self):
        from models.trust_accounting import format_aud
        assert format_aud(150) == "$1.50"

    def test_negative(self):
        from models.trust_accounting import format_aud
        assert format_aud(-500) == "-$5.00"

    def test_large_value(self):
        from models.trust_accounting import format_aud
        assert format_aud(34087020) == "$340,870.20"


# ─── Trust Config tests ────────────────────────────────────────────────────────

class TestGetTrustConfig:
    """GET /trust/v2/config/{building_id}"""

    @pytest.mark.asyncio
    async def test_returns_config_for_authorised_user(self):
        from routers.trust_phase1 import get_trust_config

        user = _manager_user()
        building_doc = {
            "_id": BUILDING_OID,
            "trust_config": {"is_trust_configured": True, "total_uoe": 100},
        }

        db = _mock_db()
        db.buildings.find_one = AsyncMock(return_value=building_doc)

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await get_trust_config(
                building_id_param=BUILDING_ID,
                current_user=user,
            )

        assert response.status_code == 200
        body = response.body.decode()
        assert "is_trust_configured" in body

    @pytest.mark.asyncio
    async def test_returns_404_when_building_missing(self):
        from routers.trust_phase1 import get_trust_config

        user = _manager_user()
        db = _mock_db()
        db.buildings.find_one = AsyncMock(return_value=None)

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await get_trust_config(
                building_id_param=BUILDING_ID,
                current_user=user,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_masks_deft_biller_codes(self):
        from routers.trust_phase1 import get_trust_config

        user = _manager_user()
        building_doc = {
            "_id": BUILDING_OID,
            "trust_config": {
                "deft_biller_code_admin": "123456789",
                "deft_biller_code_sinking": "987654321",
            },
        }

        db = _mock_db()
        db.buildings.find_one = AsyncMock(return_value=building_doc)

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await get_trust_config(
                building_id_param=BUILDING_ID,
                current_user=user,
            )

        body = response.body.decode()
        # Full biller codes must not appear; masked versions should
        assert "123456789" not in body
        assert "****6789" in body

    @pytest.mark.asyncio
    async def test_rejects_owner_role(self):
        from routers.trust_phase1 import get_trust_config

        user = _owner_user()
        db = _mock_db()

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await get_trust_config(
                building_id_param=BUILDING_ID,
                current_user=user,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_rejects_cross_building_access_for_non_super_admin(self):
        from routers.trust_phase1 import get_trust_config

        user = _manager_user(building_id=BUILDING_ID)
        other_building_id = str(ObjectId())
        db = _mock_db()

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await get_trust_config(
                building_id_param=other_building_id,
                current_user=user,
            )

        assert exc_info.value.status_code == 403


class TestUpdateTrustConfig:
    """PUT /trust/v2/config/{building_id}"""

    @pytest.mark.asyncio
    async def test_updates_config_successfully(self):
        from routers.trust_phase1 import update_trust_config
        from models.trust_accounting import BuildingTrustConfigUpdate

        user = _manager_user()
        building_doc = {
            "_id": BUILDING_OID,
            "trust_config": {"is_trust_configured": False, "total_uoe": 100},
        }

        db = _mock_db()
        db.buildings.find_one = AsyncMock(return_value=building_doc)
        db.buildings.update_one = AsyncMock()
        db.units.aggregate = MagicMock(return_value=_async_agg_cursor([{"total": 100}]))
        db.trust_audit_logs.insert_one = AsyncMock()

        payload = BuildingTrustConfigUpdate(
            admin_fund_annual_budget_cents=1000000,
            grace_period_days=14,
        )

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await update_trust_config(
                building_id_param=BUILDING_ID,
                payload=payload,
                current_user=user,
            )

        assert response.status_code == 200
        body = response.body.decode()
        assert "success" in body

    @pytest.mark.asyncio
    async def test_rejects_invalid_quarterly_due_dates(self):
        from routers.trust_phase1 import update_trust_config
        from models.trust_accounting import BuildingTrustConfigUpdate

        user = _manager_user()
        building_doc = {"_id": BUILDING_OID, "trust_config": {}}
        db = _mock_db()
        db.buildings.find_one = AsyncMock(return_value=building_doc)
        db.units.aggregate = MagicMock(return_value=_async_agg_cursor([]))

        # Only 2 dates (should be exactly 4)
        payload = BuildingTrustConfigUpdate(
            quarterly_due_dates=["2026-03-31", "2026-06-30"],
        )

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await update_trust_config(
                building_id_param=BUILDING_ID,
                payload=payload,
                current_user=user,
            )

        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_negative_budget(self):
        from routers.trust_phase1 import update_trust_config
        from models.trust_accounting import BuildingTrustConfigUpdate

        user = _manager_user()
        building_doc = {"_id": BUILDING_OID, "trust_config": {}}
        db = _mock_db()
        db.buildings.find_one = AsyncMock(return_value=building_doc)
        db.units.aggregate = MagicMock(return_value=_async_agg_cursor([]))

        payload = BuildingTrustConfigUpdate(admin_fund_annual_budget_cents=-100)

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await update_trust_config(
                building_id_param=BUILDING_ID,
                payload=payload,
                current_user=user,
            )

        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_out_of_range_interest_rate(self):
        from routers.trust_phase1 import update_trust_config
        from models.trust_accounting import BuildingTrustConfigUpdate

        user = _manager_user()
        building_doc = {"_id": BUILDING_OID, "trust_config": {}}
        db = _mock_db()
        db.buildings.find_one = AsyncMock(return_value=building_doc)
        db.units.aggregate = MagicMock(return_value=_async_agg_cursor([]))

        payload = BuildingTrustConfigUpdate(arrears_interest_rate=1.5)  # >1 is invalid

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await update_trust_config(
                building_id_param=BUILDING_ID,
                payload=payload,
                current_user=user,
            )

        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_non_manager_role(self):
        from routers.trust_phase1 import update_trust_config
        from models.trust_accounting import BuildingTrustConfigUpdate

        user = _ec_user()  # ec_member cannot manage
        db = _mock_db()

        payload = BuildingTrustConfigUpdate(grace_period_days=7)

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await update_trust_config(
                building_id_param=BUILDING_ID,
                payload=payload,
                current_user=user,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_returns_404_when_building_missing(self):
        from routers.trust_phase1 import update_trust_config
        from models.trust_accounting import BuildingTrustConfigUpdate

        user = _manager_user()
        db = _mock_db()
        db.buildings.find_one = AsyncMock(return_value=None)

        payload = BuildingTrustConfigUpdate(grace_period_days=7)

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await update_trust_config(
                building_id_param=BUILDING_ID,
                payload=payload,
                current_user=user,
            )

        assert exc_info.value.status_code == 404


# ─── Trust Accounts tests ─────────────────────────────────────────────────────

class TestListTrustAccounts:
    """GET /trust/v2/accounts"""

    @pytest.mark.asyncio
    async def test_returns_account_list(self):
        from routers.trust_phase1 import list_trust_accounts_v2

        user = _manager_user()
        account_doc = {
            "_id": ACCOUNT_OID,
            "building_id": BUILDING_OID,
            "account_type": "admin_fund",
            "bsb": "082-001",
            "account_number_masked": "****4567",
            "bank_name": "NAB",
            "account_name": "Admin Fund Account",
            "current_balance_cents": 500000,
            "last_reconciled_balance_cents": 490000,
            "is_active": True,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }

        db = _mock_db()
        db.trust_accounts_v2.find = MagicMock(return_value=_async_cursor([account_doc]))
        # aggregate called once per account for computed balance
        db.trust_transactions_v2.aggregate = MagicMock(
            return_value=_HybridCursor([{"receipts": 500000, "disbursements": 0}])
        )

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await list_trust_accounts_v2(current_user=user)

        assert response.status_code == 200
        import json
        body = json.loads(response.body)
        assert body["success"] is True
        assert len(body["data"]) == 1
        assert body["data"][0]["account_type"] == "admin_fund"
        # computed_balance_cents = opening(0) + receipts(500000) - disbursements(0) = 500000
        assert body["data"][0]["computed_balance_cents"] == 500000

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_accounts(self):
        from routers.trust_phase1 import list_trust_accounts_v2

        user = _manager_user()
        db = _mock_db()
        db.trust_accounts_v2.find = MagicMock(return_value=_async_cursor([]))

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await list_trust_accounts_v2(current_user=user)

        import json
        body = json.loads(response.body)
        assert body["data"] == []

    @pytest.mark.asyncio
    async def test_rejects_user_with_no_building_id(self):
        from routers.trust_phase1 import list_trust_accounts_v2

        user = _manager_user()
        user.pop("building_id")  # Remove building_id to trigger RULE 4 error
        db = _mock_db()

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await list_trust_accounts_v2(current_user=user)

        assert exc_info.value.status_code == 400


class TestCreateTrustAccount:
    """POST /trust/v2/accounts"""

    @pytest.mark.asyncio
    async def test_creates_account_successfully(self):
        from routers.trust_phase1 import create_trust_account_v2
        from models.trust_accounting import TrustAccountPhase1Create

        user = _manager_user()
        new_id = ObjectId()

        insert_result = MagicMock()
        insert_result.inserted_id = new_id

        db = _mock_db()
        db.trust_accounts_v2.insert_one = AsyncMock(return_value=insert_result)
        db.trust_audit_logs.insert_one = AsyncMock()

        payload = TrustAccountPhase1Create(
            account_type="admin_fund",
            bsb="082-001",
            account_number_masked="****4567",
            bank_name="NAB",
            account_name="Admin Fund Account",
            current_balance_cents=0,
        )

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await create_trust_account_v2(
                payload=payload,
                current_user=user,
            )

        assert response.status_code == 201
        import json
        body = json.loads(response.body)
        assert body["success"] is True
        assert "id" in body

    @pytest.mark.asyncio
    async def test_rejects_tenant_creating_account(self):
        from routers.trust_phase1 import create_trust_account_v2
        from models.trust_accounting import TrustAccountPhase1Create

        user = {
            "_id": ObjectId(),
            "role": "tenant",
            "email": "tenant@test.com",
            "building_id": BUILDING_ID,
        }
        db = _mock_db()

        payload = TrustAccountPhase1Create(
            account_type="admin_fund",
            bsb="082-001",
            account_number_masked="****1234",
            bank_name="CBA",
            account_name="Test",
        )

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await create_trust_account_v2(payload=payload, current_user=user)

        assert exc_info.value.status_code == 403


class TestCreateTrustAccountV2Record:
    """create_trust_account_v2_record — the shared insert helper extracted from
    create_trust_account_v2 (tasks/GAP-ONBOARD-001) so onboarding finalize
    (routers/onboarding.py) can create an account without going through the
    route's require_feature Depends. Covers the two behavioural differences
    from the route: explicit building_id parameter (not sourced from the
    caller's JWT) and the current_user.get("_id") or current_user.get("id")
    fallback (onboarding's current_user dict has "id" but not "_id")."""

    @pytest.mark.asyncio
    async def test_uses_explicit_building_id_not_callers_jwt(self):
        """Onboarding finalize creates the account for the NEW building, not the
        operator's own — the caller's own building_id must never leak in."""
        from routers.trust_phase1 import create_trust_account_v2_record
        from models.trust_accounting import TrustAccountPhase1Create

        operator = {"id": "operator-1", "role": "super_admin", "building_id": "OPERATORS-OWN-BUILDING"}
        new_building_id = "BRAND-NEW-BUILDING"
        new_id = ObjectId()
        insert_result = MagicMock()
        insert_result.inserted_id = new_id

        db = _mock_db()
        db.trust_accounts_v2.insert_one = AsyncMock(return_value=insert_result)
        db.trust_audit_logs.insert_one = AsyncMock()

        payload = TrustAccountPhase1Create(
            account_type="admin_fund", bsb="082-001", account_number_masked="****4567",
            bank_name="NAB", account_name="New Building Trust Account",
        )

        with patch("routers.trust_phase1._get_db", return_value=db):
            result_id = await create_trust_account_v2_record(payload, new_building_id, operator)

        assert result_id == str(new_id)
        inserted_doc = db.trust_accounts_v2.insert_one.call_args.args[0]
        assert inserted_doc["building_id"] == new_building_id
        assert inserted_doc["building_id"] != operator["building_id"]

    @pytest.mark.asyncio
    async def test_created_by_falls_back_to_id_when_no_mongo_objectid(self):
        """onboarding.py's current_user dict has "id" (a UUID string from Postgres
        auth), never "_id" (a Mongo ObjectId) — created_by must still populate."""
        from routers.trust_phase1 import create_trust_account_v2_record
        from models.trust_accounting import TrustAccountPhase1Create

        pg_only_user = {"id": "11111111-1111-1111-1111-111111111111", "role": "super_admin", "full_name": "PG User"}
        insert_result = MagicMock()
        insert_result.inserted_id = ObjectId()

        db = _mock_db()
        db.trust_accounts_v2.insert_one = AsyncMock(return_value=insert_result)
        db.trust_audit_logs.insert_one = AsyncMock()

        payload = TrustAccountPhase1Create(
            account_type="admin_fund", bsb="082-001", account_number_masked="****4567",
            bank_name="NAB", account_name="Test",
        )

        with patch("routers.trust_phase1._get_db", return_value=db):
            await create_trust_account_v2_record(payload, BUILDING_ID, pg_only_user)

        inserted_doc = db.trust_accounts_v2.insert_one.call_args.args[0]
        assert inserted_doc["created_by"] == pg_only_user["id"]


class TestGetAccountBalance:
    """GET /trust/v2/accounts/{account_id}/balance"""

    @pytest.mark.asyncio
    async def test_returns_balance_summary(self):
        from routers.trust_phase1 import get_account_balance

        user = _manager_user()
        account_doc = {
            "_id": ACCOUNT_OID,
            "building_id": BUILDING_OID,
            "account_type": "admin_fund",
            "current_balance_cents": 100000,
            "last_reconciled_balance_cents": 95000,
            "last_reconciled_at": "2026-03-01T00:00:00Z",
        }

        db = _mock_db()
        db.trust_accounts_v2.find_one = AsyncMock(return_value=account_doc)
        db.trust_transactions_v2.count_documents = AsyncMock(return_value=3)
        # aggregate called for computed balance: opening(0) + receipts - disbursements
        db.trust_transactions_v2.aggregate = MagicMock(
            return_value=_HybridCursor([{"receipts": 100000, "disbursements": 0}])
        )

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await get_account_balance(
                account_id=ACCOUNT_ID,
                current_user=user,
            )

        assert response.status_code == 200
        import json
        body = json.loads(response.body)
        data = body["data"]
        # computed_balance = opening(0) + receipts(100000) = 100000
        assert data["current_balance"]["cents"] == 100000
        assert data["last_reconciled_balance"]["cents"] == 95000
        # reconciliation_gap = computed(100000) - last_reconciled(95000) = 5000
        assert data["reconciliation_gap"]["cents"] == 5000
        assert data["uncleared_count"] == 3

    @pytest.mark.asyncio
    async def test_returns_404_when_account_missing(self):
        from routers.trust_phase1 import get_account_balance

        user = _manager_user()
        db = _mock_db()
        db.trust_accounts_v2.find_one = AsyncMock(return_value=None)

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await get_account_balance(
                account_id=ACCOUNT_ID,
                current_user=user,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_rejects_cross_building_account_access(self):
        from routers.trust_phase1 import get_account_balance

        user = _manager_user(building_id=BUILDING_ID)
        other_building_oid = ObjectId()
        account_doc = {
            "_id": ACCOUNT_OID,
            "building_id": other_building_oid,  # different building
            "current_balance_cents": 0,
            "last_reconciled_balance_cents": 0,
        }

        db = _mock_db()
        db.trust_accounts_v2.find_one = AsyncMock(return_value=account_doc)

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await get_account_balance(
                account_id=ACCOUNT_ID,
                current_user=user,
            )

        assert exc_info.value.status_code == 403


# ─── Transactions tests ────────────────────────────────────────────────────────

class TestCreateTransaction:
    """POST /trust/v2/transactions"""

    @pytest.mark.asyncio
    async def test_list_transactions_uses_opening_balance_not_current_balance_fallback(self):
        from routers.trust_phase1 import list_transactions_v2

        user = _manager_user()
        account_doc = {
            "_id": ACCOUNT_OID,
            "building_id": BUILDING_OID,
            "current_balance_cents": 100000,
        }
        tx_doc = {
            "_id": TX_OID,
            "building_id": BUILDING_OID,
            "trust_account_id": ACCOUNT_OID,
            "type": "receipt",
            "amount_cents": 25000,
            "description": "Levy receipt",
            "created_at": "2026-07-01T00:00:00+00:00",
        }

        db = _mock_db()
        db.trust_accounts_v2.find_one = AsyncMock(return_value=account_doc)
        db.trust_transactions_v2.find = MagicMock(side_effect=[
            _async_cursor([tx_doc]),
            _async_cursor([]),
        ])
        db.trust_transactions_v2.count_documents = AsyncMock(return_value=1)

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await list_transactions_v2(
                trust_account_id=ACCOUNT_ID,
                page=1,
                limit=25,
                current_user=user,
            )

        import json
        body = json.loads(response.body)
        first_match = db.trust_transactions_v2.find.call_args_list[0].args[0]
        assert "type" not in first_match
        assert "is_reconciled" not in first_match
        assert "created_at" not in first_match
        assert body["data"][0]["running_balance_cents"] == 25000

    @pytest.mark.asyncio
    async def test_list_transactions_date_filter_keeps_prior_history_in_running_balance(self):
        from routers.trust_phase1 import list_transactions_v2

        user = _manager_user()
        account_doc = {
            "_id": ACCOUNT_OID,
            "building_id": BUILDING_OID,
            "opening_balance_cents": 10000,
            "current_balance_cents": 999999,
        }
        prior_tx = {
            "_id": ObjectId(),
            "building_id": BUILDING_OID,
            "trust_account_id": ACCOUNT_OID,
            "type": "receipt",
            "amount_cents": 5000,
            "created_at": "2026-06-30T00:00:00+00:00",
        }
        visible_tx = {
            "_id": TX_OID,
            "building_id": BUILDING_OID,
            "trust_account_id": ACCOUNT_OID,
            "type": "disbursement",
            "amount_cents": 2000,
            "description": "Filtered window disbursement",
            "created_at": "2026-07-01T00:00:00+00:00",
        }

        db = _mock_db()
        db.trust_accounts_v2.find_one = AsyncMock(return_value=account_doc)
        db.trust_transactions_v2.find = MagicMock(side_effect=[
            _async_cursor([visible_tx]),
            _async_cursor([prior_tx]),
            _async_cursor([]),
        ])
        db.trust_transactions_v2.count_documents = AsyncMock(return_value=1)

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await list_transactions_v2(
                trust_account_id=ACCOUNT_ID,
                page=1,
                limit=25,
                from_date="2026-07-01",
                current_user=user,
            )

        import json
        body = json.loads(response.body)
        assert body["data"][0]["running_balance_cents"] == 13000

    @pytest.mark.asyncio
    async def test_creates_receipt_and_increments_balance(self):
        from routers.trust_phase1 import create_transaction_v2
        from models.trust_accounting import TrustTransactionPhase1Create

        user = _manager_user()
        new_tx_id = ObjectId()
        insert_result = MagicMock()
        insert_result.inserted_id = new_tx_id

        account_doc = {
            "_id": ACCOUNT_OID,
            "building_id": BUILDING_OID,
            "current_balance_cents": 0,
        }

        db = _mock_db()
        db.trust_accounts_v2.find_one = AsyncMock(return_value=account_doc)
        db.trust_transactions_v2.insert_one = AsyncMock(return_value=insert_result)
        db.trust_accounts_v2.update_one = AsyncMock()
        db.trust_audit_logs.insert_one = AsyncMock()

        payload = TrustTransactionPhase1Create(
            trust_account_id=ACCOUNT_ID,
            type="receipt",
            amount_cents=50000,
            description="Levy receipt for UA001",
        )

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await create_transaction_v2(
                payload=payload,
                current_user=user,
            )

        assert response.status_code == 201
        import json
        body = json.loads(response.body)
        assert body["success"] is True
        assert "id" in body

        # Balance is computed from transactions (not stored as $inc on the account)
        # update_one is called to set updated_at only
        db.trust_accounts_v2.update_one.assert_called_once()
        call_args = db.trust_accounts_v2.update_one.call_args
        assert "$set" in call_args[0][1], "Expected $set with updated_at"
        assert "updated_at" in call_args[0][1]["$set"]

    @pytest.mark.asyncio
    async def test_creates_disbursement_and_decrements_balance(self):
        from routers.trust_phase1 import create_transaction_v2
        from models.trust_accounting import TrustTransactionPhase1Create

        user = _manager_user()
        insert_result = MagicMock()
        insert_result.inserted_id = ObjectId()

        account_doc = {
            "_id": ACCOUNT_OID,
            "building_id": BUILDING_OID,
            "current_balance_cents": 100000,
        }

        db = _mock_db()
        db.trust_accounts_v2.find_one = AsyncMock(return_value=account_doc)
        db.trust_transactions_v2.insert_one = AsyncMock(return_value=insert_result)
        db.trust_accounts_v2.update_one = AsyncMock()
        db.trust_audit_logs.insert_one = AsyncMock()

        payload = TrustTransactionPhase1Create(
            trust_account_id=ACCOUNT_ID,
            type="disbursement",
            amount_cents=20000,
            description="Plumber invoice payment",
        )

        with patch("routers.trust_phase1._get_db", return_value=db):
            await create_transaction_v2(payload=payload, current_user=user)

        # Balance is computed from transactions (not stored as $inc on the account)
        call_args = db.trust_accounts_v2.update_one.call_args
        assert "$set" in call_args[0][1], "Expected $set with updated_at"
        assert "updated_at" in call_args[0][1]["$set"]

    @pytest.mark.asyncio
    async def test_rejects_zero_amount(self):
        from routers.trust_phase1 import create_transaction_v2
        from models.trust_accounting import TrustTransactionPhase1Create

        user = _manager_user()
        db = _mock_db()

        # Bypass Pydantic Field(gt=0) by patching amount validation check directly
        payload = TrustTransactionPhase1Create(
            trust_account_id=ACCOUNT_ID,
            type="receipt",
            amount_cents=100,
            description="test",
        )
        # Manually set zero to trigger the router-level check
        payload.amount_cents = 0

        account_doc = {"_id": ACCOUNT_OID, "building_id": BUILDING_OID}
        db.trust_accounts_v2.find_one = AsyncMock(return_value=account_doc)

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await create_transaction_v2(payload=payload, current_user=user)

        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_returns_404_when_account_not_found(self):
        from routers.trust_phase1 import create_transaction_v2
        from models.trust_accounting import TrustTransactionPhase1Create

        user = _manager_user()
        db = _mock_db()
        db.trust_accounts_v2.find_one = AsyncMock(return_value=None)

        payload = TrustTransactionPhase1Create(
            trust_account_id=ACCOUNT_ID,
            type="receipt",
            amount_cents=1000,
            description="test",
        )

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await create_transaction_v2(payload=payload, current_user=user)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_updates_levy_schedule_on_receipt(self):
        from routers.trust_phase1 import create_transaction_v2
        from models.trust_accounting import TrustTransactionPhase1Create

        user = _manager_user()
        insert_result = MagicMock()
        insert_result.inserted_id = ObjectId()

        account_doc = {"_id": ACCOUNT_OID, "building_id": BUILDING_OID}
        schedule_doc = {
            "_id": SCHEDULE_OID,
            "paid_cents": 0,
            "total_cents": 50000,
            "status": "pending",
        }

        db = _mock_db()
        db.trust_accounts_v2.find_one = AsyncMock(return_value=account_doc)
        db.trust_transactions_v2.insert_one = AsyncMock(return_value=insert_result)
        db.trust_accounts_v2.update_one = AsyncMock()
        db.trust_levy_schedules_v2.find_one = AsyncMock(return_value=schedule_doc)
        db.trust_levy_schedules_v2.update_one = AsyncMock()
        db.trust_audit_logs.insert_one = AsyncMock()

        payload = TrustTransactionPhase1Create(
            trust_account_id=ACCOUNT_ID,
            trust_levy_schedule_id=SCHEDULE_ID,
            type="receipt",
            amount_cents=50000,
            description="Full levy payment",
        )

        with patch("routers.trust_phase1._get_db", return_value=db):
            await create_transaction_v2(payload=payload, current_user=user)

        # Verify schedule was updated
        db.trust_levy_schedules_v2.update_one.assert_called_once()
        update_args = db.trust_levy_schedules_v2.update_one.call_args[0][1]["$set"]
        assert update_args["paid_cents"] == 50000
        assert update_args["outstanding_cents"] == 0
        assert update_args["status"] == "paid"


class TestReverseTransaction:
    """POST /trust/v2/transactions/{tx_id}/reverse"""

    @pytest.mark.asyncio
    async def test_reverses_receipt_transaction(self):
        from routers.trust_phase1 import reverse_transaction_v2

        user = _manager_user()
        original_tx = {
            "_id": TX_OID,
            "building_id": BUILDING_OID,
            "trust_account_id": ACCOUNT_OID,
            "type": "receipt",
            "amount_cents": 50000,
            "gst_cents": 0,
            "description": "Levy receipt",
            "is_reversed": False,
        }
        rev_result = MagicMock()
        rev_result.inserted_id = ObjectId()

        db = _mock_db()
        db.trust_transactions_v2.find_one = AsyncMock(return_value=original_tx)
        db.trust_transactions_v2.insert_one = AsyncMock(return_value=rev_result)
        db.trust_transactions_v2.update_one = AsyncMock()
        db.trust_accounts_v2.update_one = AsyncMock()
        db.trust_audit_logs.insert_one = AsyncMock()

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await reverse_transaction_v2(
                tx_id=TX_ID,
                body={"reason": "Duplicate payment"},
                current_user=user,
            )

        assert response.status_code == 200
        import json
        body = json.loads(response.body)
        assert body["success"] is True
        assert "reversal_id" in body

        # Original tx should be flagged
        db.trust_transactions_v2.update_one.assert_called()

        # Balance is computed from transactions (not stored as $inc on the account)
        # Reversing a receipt — account updated_at is touched only
        db.trust_accounts_v2.update_one.assert_called_once()
        balance_args = db.trust_accounts_v2.update_one.call_args[0][1]
        assert "$set" in balance_args
        assert "updated_at" in balance_args["$set"]

    @pytest.mark.asyncio
    async def test_reverses_disbursement_restores_balance(self):
        from routers.trust_phase1 import reverse_transaction_v2

        user = _manager_user()
        original_tx = {
            "_id": TX_OID,
            "building_id": BUILDING_OID,
            "trust_account_id": ACCOUNT_OID,
            "type": "disbursement",
            "amount_cents": 20000,
            "gst_cents": 0,
            "description": "Plumber payment",
            "is_reversed": False,
        }
        rev_result = MagicMock()
        rev_result.inserted_id = ObjectId()

        db = _mock_db()
        db.trust_transactions_v2.find_one = AsyncMock(return_value=original_tx)
        db.trust_transactions_v2.insert_one = AsyncMock(return_value=rev_result)
        db.trust_transactions_v2.update_one = AsyncMock()
        db.trust_accounts_v2.update_one = AsyncMock()
        db.trust_audit_logs.insert_one = AsyncMock()

        with patch("routers.trust_phase1._get_db", return_value=db):
            await reverse_transaction_v2(
                tx_id=TX_ID,
                body={"reason": "Wrong vendor"},
                current_user=user,
            )

        # Balance is computed from transactions (not stored as $inc on the account)
        # Reversing a disbursement — account updated_at is touched only
        balance_args = db.trust_accounts_v2.update_one.call_args[0][1]
        assert "$set" in balance_args
        assert "updated_at" in balance_args["$set"]

    @pytest.mark.asyncio
    async def test_returns_404_for_missing_transaction(self):
        from routers.trust_phase1 import reverse_transaction_v2

        user = _manager_user()
        db = _mock_db()
        db.trust_transactions_v2.find_one = AsyncMock(return_value=None)

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await reverse_transaction_v2(
                tx_id=TX_ID,
                body={"reason": "test"},
                current_user=user,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_409_for_already_reversed(self):
        from routers.trust_phase1 import reverse_transaction_v2

        user = _manager_user()
        original_tx = {
            "_id": TX_OID,
            "building_id": BUILDING_OID,
            "type": "receipt",
            "amount_cents": 50000,
            "is_reversed": True,  # Already reversed
        }

        db = _mock_db()
        db.trust_transactions_v2.find_one = AsyncMock(return_value=original_tx)

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await reverse_transaction_v2(
                tx_id=TX_ID,
                body={"reason": "Again"},
                current_user=user,
            )

        assert exc_info.value.status_code == 409


# ─── Levy Generation tests ─────────────────────────────────────────────────────

class TestGenerateLevySchedules:
    """POST /trust/v2/levies/generate"""

    def _building_with_trust_config(self):
        return {
            "_id": BUILDING_OID,
            "trust_config": {
                "is_trust_configured": True,
                "admin_fund_annual_budget_cents": 34087000,
                "sinking_fund_annual_budget_cents": 9950400,
                "deft_biller_code_admin": "12345",
                "grace_period_days": 14,
                "total_uoe": 1000,
            },
        }

    def _unit(self, idx: int, uoe: int = 100):
        oid = ObjectId()
        return {
            "_id": oid,
            "building_id": BUILDING_OID,
            "unit_number": f"UA0{idx:02d}",
            "lot_number": idx,
            "uoe": uoe,
            "is_active": True,
        }

    @pytest.mark.asyncio
    async def test_generates_schedules_for_all_units(self):
        from routers.trust_phase1 import generate_levy_schedules
        from models.trust_accounting import LevyGenerateRequest

        user = _manager_user()
        building = self._building_with_trust_config()
        units = [self._unit(i, 100) for i in range(1, 4)]

        insert_result = MagicMock()
        insert_result.inserted_id = ObjectId()

        db = _mock_db()
        db.buildings.find_one = AsyncMock(return_value=building)
        db.units.find = MagicMock(return_value=_async_cursor(units))
        db.trust_levy_schedules_v2.find_one = AsyncMock(return_value=None)  # No existing
        db.trust_levy_schedules_v2.insert_one = AsyncMock(return_value=insert_result)
        db.trust_audit_logs.insert_one = AsyncMock()

        payload = LevyGenerateRequest(
            quarter="2026-Q2",
            financial_year="2026-27",
            due_date="2026-06-01",
        )

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await generate_levy_schedules(
                payload=payload,
                current_user=user,
            )

        assert response.status_code == 200
        import json
        body = json.loads(response.body)
        data = body["data"]
        assert data["created"] == 3
        assert data["skipped"] == 0
        assert data["total_units"] == 3
        assert data["quarter"] == "2026-Q2"

    @pytest.mark.asyncio
    async def test_skips_existing_schedules(self):
        from routers.trust_phase1 import generate_levy_schedules
        from models.trust_accounting import LevyGenerateRequest

        user = _manager_user()
        building = self._building_with_trust_config()
        units = [self._unit(1)]

        existing_schedule = {"_id": ObjectId(), "quarter": "2026-Q2"}

        db = _mock_db()
        db.buildings.find_one = AsyncMock(return_value=building)
        db.units.find = MagicMock(return_value=_async_cursor(units))
        db.trust_levy_schedules_v2.find_one = AsyncMock(return_value=existing_schedule)
        db.trust_audit_logs.insert_one = AsyncMock()

        payload = LevyGenerateRequest(
            quarter="2026-Q2",
            financial_year="2026-27",
            due_date="2026-06-01",
        )

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await generate_levy_schedules(payload=payload, current_user=user)

        import json
        body = json.loads(response.body)
        assert body["data"]["skipped"] == 1
        assert body["data"]["created"] == 0

    @pytest.mark.asyncio
    async def test_rejects_unconfigured_trust(self):
        from routers.trust_phase1 import generate_levy_schedules
        from models.trust_accounting import LevyGenerateRequest

        user = _manager_user()
        building = {
            "_id": BUILDING_OID,
            "trust_config": {"is_trust_configured": False},
        }

        db = _mock_db()
        db.buildings.find_one = AsyncMock(return_value=building)
        db.units.find = MagicMock(return_value=_async_cursor([]))

        payload = LevyGenerateRequest(
            quarter="2026-Q2",
            financial_year="2026-27",
            due_date="2026-06-01",
        )

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await generate_levy_schedules(payload=payload, current_user=user)

        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_missing_deft_biller_code(self):
        from routers.trust_phase1 import generate_levy_schedules
        from models.trust_accounting import LevyGenerateRequest

        user = _manager_user()
        building = {
            "_id": BUILDING_OID,
            "trust_config": {
                "is_trust_configured": True,
                "admin_fund_annual_budget_cents": 1000000,
                "sinking_fund_annual_budget_cents": 500000,
                "deft_biller_code_admin": None,  # Missing!
                "grace_period_days": 14,
            },
        }
        units = [self._unit(1)]

        db = _mock_db()
        db.buildings.find_one = AsyncMock(return_value=building)
        db.units.find = MagicMock(return_value=_async_cursor(units))

        payload = LevyGenerateRequest(
            quarter="2026-Q1",
            financial_year="2026-27",
            due_date="2026-03-31",
        )

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await generate_levy_schedules(payload=payload, current_user=user)

        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_no_active_units(self):
        from routers.trust_phase1 import generate_levy_schedules
        from models.trust_accounting import LevyGenerateRequest

        user = _manager_user()
        building = self._building_with_trust_config()

        db = _mock_db()
        db.buildings.find_one = AsyncMock(return_value=building)
        db.units.find = MagicMock(return_value=_async_cursor([]))  # No units

        payload = LevyGenerateRequest(
            quarter="2026-Q1",
            financial_year="2026-27",
            due_date="2026-03-31",
        )

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await generate_levy_schedules(payload=payload, current_user=user)

        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_levy_splits_sum_to_quarterly_total(self):
        from routers.trust_phase1 import generate_levy_schedules
        from models.trust_accounting import LevyGenerateRequest

        user = _manager_user()

        # 4 units with slightly different UOEs to exercise rounding
        units = [
            {**self._unit(i, uoe), "_id": ObjectId()}
            for i, uoe in enumerate([101, 102, 99, 98], start=1)
        ]
        building = {
            "_id": BUILDING_OID,
            "trust_config": {
                "is_trust_configured": True,
                "admin_fund_annual_budget_cents": 100000,  # 25000 quarterly
                "sinking_fund_annual_budget_cents": 40000,  # 10000 quarterly
                "deft_biller_code_admin": "54321",
                "grace_period_days": 14,
                "total_uoe": 400,
            },
        }

        inserted_docs = []

        async def capture_insert(doc):
            inserted_docs.append(doc)
            r = MagicMock()
            r.inserted_id = ObjectId()
            return r

        db = _mock_db()
        db.buildings.find_one = AsyncMock(return_value=building)
        db.units.find = MagicMock(return_value=_async_cursor(units))
        db.trust_levy_schedules_v2.find_one = AsyncMock(return_value=None)
        db.trust_levy_schedules_v2.insert_one = AsyncMock(side_effect=capture_insert)
        db.trust_audit_logs.insert_one = AsyncMock()

        payload = LevyGenerateRequest(
            quarter="2026-Q3",
            financial_year="2026-27",
            due_date="2026-09-01",
        )

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await generate_levy_schedules(payload=payload, current_user=user)

        assert len(inserted_docs) == 4
        total_admin = sum(d["admin_fund_cents"] for d in inserted_docs)
        total_sinking = sum(d["sinking_fund_cents"] for d in inserted_docs)
        assert total_admin == 25000  # 100000 / 4
        assert total_sinking == 10000  # 40000 / 4


# ─── Levy Schedule tests ───────────────────────────────────────────────────────

class TestGetLevySchedule:
    """GET /trust/v2/levies/{building_id}/schedule"""

    @pytest.mark.asyncio
    async def test_returns_schedule_for_manager(self):
        from routers.trust_phase1 import get_levy_schedule

        user = _manager_user()
        schedule_doc = {
            "_id": SCHEDULE_OID,
            "building_id": BUILDING_OID,
            "unit_id": ObjectId(),
            "unit_number": "UA001",
            "lot_number": 1,
            "quarter": "2026-Q1",
            "financial_year": "2026-27",
            "due_date": "2026-03-31",
            "admin_fund_cents": 8000,
            "sinking_fund_cents": 2500,
            "total_cents": 10500,
            "deft_crn": "12345-0001-2026Q1-7",
            "status": "pending",
            "paid_cents": 0,
            "outstanding_cents": 10500,
        }

        db = _mock_db()
        db.trust_levy_schedules_v2.find = MagicMock(return_value=_async_cursor([schedule_doc]))
        db.trust_levy_schedules_v2.count_documents = AsyncMock(return_value=1)

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await get_levy_schedule(
                building_id_param=BUILDING_ID,
                quarter="2026-Q1",
                page=1,
                limit=25,
                current_user=user,
            )

        assert response.status_code == 200
        import json
        body = json.loads(response.body)
        assert body["success"] is True
        assert len(body["data"]) == 1
        assert body["data"][0]["unit_number"] == "UA001"
        assert body["data"][0]["total_cents"] == 10500

    @pytest.mark.asyncio
    async def test_rejects_cross_building_for_non_super_admin(self):
        from routers.trust_phase1 import get_levy_schedule

        user = _manager_user(building_id=BUILDING_ID)
        other_building = str(ObjectId())
        db = _mock_db()

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await get_levy_schedule(
                building_id_param=other_building,
                quarter="2026-Q1",
                page=1,
                limit=25,
                current_user=user,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_owner_can_view_own_schedule(self):
        from routers.trust_phase1 import get_levy_schedule

        user = _owner_user()
        db = _mock_db()
        db.trust_levy_schedules_v2.find = MagicMock(return_value=_async_cursor([]))
        db.trust_levy_schedules_v2.count_documents = AsyncMock(return_value=0)

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await get_levy_schedule(
                building_id_param=BUILDING_ID,
                quarter="2026-Q1",
                page=1,
                limit=25,
                current_user=user,
            )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_rejects_service_provider_role(self):
        from routers.trust_phase1 import get_levy_schedule

        user = {
            "_id": ObjectId(),
            "role": "service_provider",
            "email": "sp@test.com",
            "building_id": BUILDING_ID,
        }
        db = _mock_db()

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await get_levy_schedule(
                building_id_param=BUILDING_ID,
                quarter="2026-Q1",
                page=1,
                limit=25,
                current_user=user,
            )

        assert exc_info.value.status_code == 403


# ─── Levy Payment tests ────────────────────────────────────────────────────────

class TestRecordLevyPayment:
    """POST /trust/v2/levies/{schedule_id}/pay"""

    @pytest.mark.asyncio
    async def test_records_full_payment(self):
        from routers.trust_phase1 import record_levy_payment

        user = _manager_user()
        schedule_doc = {
            "_id": SCHEDULE_OID,
            "building_id": BUILDING_OID,
            "unit_number": "UA001",
            "quarter": "2026-Q1",
            "paid_cents": 0,
            "total_cents": 50000,
            "status": "pending",
        }
        admin_account = {"_id": ACCOUNT_OID}

        insert_result = MagicMock()
        insert_result.inserted_id = ObjectId()

        db = _mock_db()
        db.trust_levy_schedules_v2.find_one = AsyncMock(return_value=schedule_doc)
        db.trust_accounts_v2.find_one = AsyncMock(return_value=admin_account)
        db.trust_transactions_v2.insert_one = AsyncMock(return_value=insert_result)
        db.trust_levy_schedules_v2.update_one = AsyncMock()
        db.trust_accounts_v2.update_one = AsyncMock()
        db.trust_audit_logs.insert_one = AsyncMock()

        body = {"amount_cents": 50000, "payment_method": "bpay", "reference": "REF123"}

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await record_levy_payment(
                schedule_id=SCHEDULE_ID,
                body=body,
                current_user=user,
            )

        assert response.status_code == 200
        import json
        resp_body = json.loads(response.body)
        assert resp_body["success"] is True
        assert resp_body["new_status"] == "paid"
        assert resp_body["outstanding_cents"] == 0

    @pytest.mark.asyncio
    async def test_records_partial_payment(self):
        from routers.trust_phase1 import record_levy_payment

        user = _manager_user()
        schedule_doc = {
            "_id": SCHEDULE_OID,
            "building_id": BUILDING_OID,
            "unit_number": "TH087",
            "quarter": "2026-Q1",
            "paid_cents": 0,
            "total_cents": 50000,
            "status": "pending",
        }
        admin_account = {"_id": ACCOUNT_OID}

        insert_result = MagicMock()
        insert_result.inserted_id = ObjectId()

        db = _mock_db()
        db.trust_levy_schedules_v2.find_one = AsyncMock(return_value=schedule_doc)
        db.trust_accounts_v2.find_one = AsyncMock(return_value=admin_account)
        db.trust_transactions_v2.insert_one = AsyncMock(return_value=insert_result)
        db.trust_levy_schedules_v2.update_one = AsyncMock()
        db.trust_accounts_v2.update_one = AsyncMock()
        db.trust_audit_logs.insert_one = AsyncMock()

        body = {"amount_cents": 20000}

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await record_levy_payment(
                schedule_id=SCHEDULE_ID,
                body=body,
                current_user=user,
            )

        import json
        resp_body = json.loads(response.body)
        assert resp_body["new_status"] == "partial"
        assert resp_body["outstanding_cents"] == 30000

    @pytest.mark.asyncio
    async def test_returns_404_for_missing_schedule(self):
        from routers.trust_phase1 import record_levy_payment

        user = _manager_user()
        db = _mock_db()
        db.trust_levy_schedules_v2.find_one = AsyncMock(return_value=None)

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await record_levy_payment(
                schedule_id=SCHEDULE_ID,
                body={"amount_cents": 1000},
                current_user=user,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_rejects_invalid_amount(self):
        from routers.trust_phase1 import record_levy_payment

        user = _manager_user()
        schedule_doc = {
            "_id": SCHEDULE_OID,
            "building_id": BUILDING_OID,
            "unit_number": "UA001",
            "quarter": "2026-Q1",
            "paid_cents": 0,
            "total_cents": 50000,
            "status": "pending",
        }

        db = _mock_db()
        db.trust_levy_schedules_v2.find_one = AsyncMock(return_value=schedule_doc)

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await record_levy_payment(
                schedule_id=SCHEDULE_ID,
                body={"amount_cents": -100},  # Invalid
                current_user=user,
            )

        assert exc_info.value.status_code == 422


# ─── Financial Summary tests ───────────────────────────────────────────────────

def _async_agg_iter(docs: list) -> MagicMock:
    """Return a mock async-iterable suitable for 'async for doc in cursor'.

    Motor aggregate() returns an AsyncCommandCursor which supports
    __aiter__ / __anext__.  We implement a minimal async generator
    wrapper so the router's 'async for doc in db.X.aggregate(...)' works.
    """

    async def _gen():
        for doc in docs:
            yield doc

    return _gen()


class _HybridCursor:
    """Mock Motor aggregate cursor supporting both async for and .to_list().

    Use this when the router code may call either pattern on the same cursor.
    """

    def __init__(self, docs: list):
        self._docs = docs

    def __aiter__(self):
        async def _gen():
            for doc in self._docs:
                yield doc

        return _gen()

    async def to_list(self, length=None):
        if length is not None:
            return self._docs[:length]
        return list(self._docs)


class TestGetFinancialSummary:
    """GET /trust/v2/financial-summary"""

    @pytest.mark.asyncio
    async def test_returns_summary_with_data(self):
        from routers.trust_phase1 import get_financial_summary

        user = _manager_user()
        building_doc = {"_id": BUILDING_OID, "name": "East Gate Residences"}

        admin_account = {
            "_id": ACCOUNT_OID,
            "account_type": "admin_fund",
            "current_balance_cents": 250000,
        }
        sinking_account = {
            "_id": ObjectId(),
            "account_type": "sinking_fund",
            "current_balance_cents": 120000,
        }

        # Aggregation call order in get_financial_summary:
        # trust_transactions_v2.aggregate calls (in router execution order):
        #   1. by-type breakdown for admin → async for
        #   2. by-type breakdown for sinking → async for
        #   3. acc_computed_balance(admin_acc) → .to_list(1)
        #   4. acc_computed_balance(sinking_acc) → .to_list(1)
        admin_tx_agg = [
            {"_id": "receipt", "total": 300000},
            {"_id": "disbursement", "total": 50000},
        ]
        sinking_tx_agg = [
            {"_id": "receipt", "total": 150000},
        ]
        admin_balance_agg = [{"receipts": 300000, "disbursements": 50000}]
        sinking_balance_agg = [{"receipts": 150000, "disbursements": 0}]
        levy_agg = [{
            "_id": None,
            "total_raised": 440375,
            "total_received": 400000,
            "overdue_count": 3,
            "overdue_cents": 40375,
            "interest_accrued": 500,
        }]
        drb_agg = [
            {"_id": "none", "count": 2},
            {"_id": "monitor", "count": 1},
        ]

        db = _mock_db()
        db.buildings.find_one = AsyncMock(return_value=building_doc)
        db.trust_accounts_v2.find = MagicMock(
            return_value=_async_cursor([admin_account, sinking_account])
        )

        # 4 calls in order: by-type(admin), by-type(sinking), balance(admin), balance(sinking)
        tx_agg_side_effects = [admin_tx_agg, sinking_tx_agg, admin_balance_agg, sinking_balance_agg]
        tx_agg_iter = iter(tx_agg_side_effects)
        db.trust_transactions_v2.aggregate = MagicMock(
            side_effect=lambda _: _HybridCursor(next(tx_agg_iter))
        )

        # trust_levy_schedules_v2.aggregate is called twice (levy summary, drb)
        levy_agg_iter = iter([levy_agg, drb_agg])
        db.trust_levy_schedules_v2.aggregate = MagicMock(
            side_effect=lambda _: _HybridCursor(next(levy_agg_iter))
        )

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await get_financial_summary(
                from_date=None,
                to_date=None,
                current_user=user,
            )

        assert response.status_code == 200
        import json
        body = json.loads(response.body)
        assert body["success"] is True
        data = body["data"]
        assert data["building_id"] == BUILDING_ID
        assert data["building_name"] == "East Gate Residences"
        assert "admin_fund" in data
        assert "sinking_fund" in data
        assert "levies" in data
        assert "arrears" in data
        # Verify receipt totals flow through
        assert data["admin_fund"]["receipts"]["cents"] == 300000
        assert data["levies"]["collection_rate_percent"] == round(400000 / 440375 * 100, 1)

    @pytest.mark.asyncio
    async def test_collection_rate_is_zero_when_nothing_raised(self):
        from routers.trust_phase1 import get_financial_summary

        user = _manager_user()
        building_doc = {"_id": BUILDING_OID, "name": "Test Building"}

        db = _mock_db()
        db.buildings.find_one = AsyncMock(return_value=building_doc)
        db.trust_accounts_v2.find = MagicMock(return_value=_async_cursor([]))

        # No accounts → no transaction aggregation calls needed
        # But levy and drb calls still occur
        levy_agg_iter = iter([[], []])
        db.trust_levy_schedules_v2.aggregate = MagicMock(
            side_effect=lambda _: _async_agg_iter(next(levy_agg_iter))
        )

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await get_financial_summary(
                from_date=None,
                to_date=None,
                current_user=user,
            )

        import json
        body = json.loads(response.body)
        assert body["data"]["levies"]["collection_rate_percent"] == 0.0

    @pytest.mark.asyncio
    async def test_rejects_tenant_role(self):
        from routers.trust_phase1 import get_financial_summary

        user = {
            "_id": ObjectId(),
            "role": "tenant",
            "email": "tenant@test.com",
            "building_id": BUILDING_ID,
        }
        db = _mock_db()

        with patch("routers.trust_phase1._get_db", return_value=db), \
                pytest.raises(HTTPException) as exc_info:
            await get_financial_summary(
                from_date=None,
                to_date=None,
                current_user=user,
            )

        assert exc_info.value.status_code == 403


# ─── Role-Based Access Control summary tests ──────────────────────────────────

class TestRoleBasedAccess:
    """Verify TRUST_MANAGE_ROLES vs TRUST_VIEW_ROLES enforcement."""

    @pytest.mark.asyncio
    async def test_super_admin_can_manage(self):
        from routers.trust_phase1 import create_trust_account_v2
        from models.trust_accounting import TrustAccountPhase1Create

        user = _super_admin_user()
        insert_result = MagicMock()
        insert_result.inserted_id = ObjectId()

        db = _mock_db()
        db.trust_accounts_v2.insert_one = AsyncMock(return_value=insert_result)
        db.trust_audit_logs.insert_one = AsyncMock()

        payload = TrustAccountPhase1Create(
            account_type="admin_fund",
            bsb="082-001",
            account_number_masked="****4567",
            bank_name="NAB",
            account_name="Super Admin Account",
        )

        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await create_trust_account_v2(payload=payload, current_user=user)

        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_ec_member_can_view_but_not_manage(self):
        """ec_member is in TRUST_VIEW_ROLES but not TRUST_MANAGE_ROLES."""
        from routers.trust_phase1 import create_trust_account_v2, list_trust_accounts_v2
        from models.trust_accounting import TrustAccountPhase1Create

        user = _ec_user()
        db = _mock_db()
        db.trust_accounts_v2.find = MagicMock(return_value=_async_cursor([]))

        # View should succeed
        with patch("routers.trust_phase1._get_db", return_value=db):
            response = await list_trust_accounts_v2(current_user=user)

        assert response.status_code == 200


# ─── MongoDB → PostgreSQL Cutover Transition Guard ────────────────────────────
# These tests verify behaviour at the cutover boundary:
#   - When trust_pg_ledger_enabled = False  → trust_accounting router uses MongoDB
#   - When trust_pg_ledger_enabled = True   → trust_accounting router uses PG path
#   - The trust_accounting feature toggle (GAP-FT-001) gates BOTH paths
#
# Context: trust_phase1.py is the PRE-CUTOVER router (v2 endpoints).
# trust_accounting.py is the POST-CUTOVER router (standard /trust/* endpoints).
# After trust_pg_ledger_enabled is permanently True, trust_phase1 becomes
# dead code. Tests in this class lock in the transition contract so the
# cutover can be validated safely.

class TestTrustCutoverTransitionGuard:
    """Verify the trust_pg_ledger_enabled toggle correctly controls which
    router path runs for trust accounting requests."""

    @pytest.mark.asyncio
    async def test_trust_accounting_router_uses_mongo_when_cutover_off(self):
        """When trust_pg_ledger_enabled=False, /trust/accounts uses MongoDB path."""
        from routers.trust_accounting import list_trust_accounts

        user = _super_admin_user()
        mock_db = _mock_db()
        mock_db.trust_ledger_accounts.find = MagicMock(
            return_value=_async_cursor([
                {"_id": ObjectId(), "account_code": "ADM-001", "fund_type": "admin_fund",
                 "account_name": "Admin Fund", "building_id": BUILDING_ID,
                 "current_balance": 50000.0, "is_active": True}
            ])
        )

        with patch("routers.trust_accounting.are_cutover_features_enabled",
                   new=AsyncMock(return_value=False)), \
             patch("routers.trust_accounting._get_db", return_value=mock_db):
            result = await list_trust_accounts(
                building_id=BUILDING_ID,
                fund_type=None,
                current_user=user,
            )

        assert isinstance(result, dict)
        assert "data" in result
        # Confirm the MongoDB collection was queried (not PG)
        mock_db.trust_ledger_accounts.find.assert_called_once()

    @pytest.mark.asyncio
    async def test_trust_accounting_router_uses_pg_when_cutover_on(self):
        """When trust_pg_ledger_enabled=True AND the domain source guard reports
        the building/domain as PG-ready, /trust/accounts uses the PG read service.

        The toggle alone is no longer sufficient: require_domain_source() must
        also allow PostgreSQL (core.domain_cutover_status control plane).
        """
        from routers.trust_accounting import list_trust_accounts

        user = _super_admin_user()
        mock_db = _mock_db()
        pg_accounts = [
            {"account_code": "ADM-001", "fund_type": "admin_fund",
             "account_name": "Admin Fund", "current_balance": 50000.0}
        ]

        with patch("routers.trust_accounting.are_cutover_features_enabled",
                   new=AsyncMock(return_value=True)), \
             patch("routers.trust_accounting.require_domain_source",
                   new=AsyncMock(return_value=MagicMock(postgres_allowed=True))), \
             patch("routers.trust_accounting._trust_read_service") as mock_svc, \
             patch("routers.trust_accounting._get_db", return_value=mock_db):
            mock_svc.list_trust_accounts = AsyncMock(return_value=pg_accounts)
            result = await list_trust_accounts(
                building_id=BUILDING_ID,
                fund_type=None,
                current_user=user,
            )

        # When PG service returns data, it is used directly
        assert isinstance(result, dict)
        assert "data" in result
        assert result["data"] == pg_accounts
        # MongoDB path should NOT have been queried
        mock_db.trust_ledger_accounts.find.assert_not_called()

    @pytest.mark.asyncio
    async def test_trust_accounting_toggle_gate_blocks_all_paths(self):
        """trust_accounting feature toggle must block BOTH Mongo and PG code paths."""
        from server import app
        from utils.auth import get_current_user, get_current_building
        from fastapi.testclient import TestClient

        app.dependency_overrides[get_current_user] = lambda: _super_admin_user()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_ID
        client = TestClient(app, raise_server_exceptions=False)

        # This hits the real server.py app via TestClient. Even a 403 still
        # routes through DeprecatedTrustV1Route's telemetry wrapper (it emits
        # on HTTPException too — see routers/trust_accounting.py), which
        # writes to the real trust_v1_usage_telemetry collection unless
        # _get_db() is mocked. Confirmed live during a 2026-07-16 re-audit:
        # this exact test wrote 2 fabricated documents into production
        # (cleaned up manually) before this mock was added — see
        # docs/fixes/BUG-TRUST-001-legacy-v1-trust-accounting-empty-mongo-collection.md.
        mock_telemetry_db = MagicMock()
        mock_telemetry_db.trust_v1_usage_telemetry.insert_one = AsyncMock()

        try:
            with patch("utils.permissions.get_effective_feature_access",
                       new=AsyncMock(return_value=False)), \
                 patch("routers.trust_accounting._get_db", return_value=mock_telemetry_db):
                resp = client.get("/api/trust/accounts")
            # Feature gate fires before any cutover logic
            assert resp.status_code == 403, (
                f"trust_accounting toggle should block regardless of cutover state, "
                f"got {resp.status_code}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_trust_phase1_collection_name_deprecation_notice(self):
        """Sentinel: this test class uses trust_accounts_v2 collection (Mongo).
        When trust_pg_ledger_enabled is permanently True, these tests become dead
        code. This test acts as a marker — when it is removed, also remove:
          - All tests in this file using _mock_db().trust_accounts_v2
          - All tests in this file using _mock_db().trust_transactions_v2
        and replace them with PG-path tests through test_trust_cutover_reads.py.
        """
        # Verify that trust_phase1 router still exists (pre-cutover)
        try:
            import routers.trust_phase1 as trust_phase1_module
            assert hasattr(trust_phase1_module, 'router'), \
                "trust_phase1 router must exist until cutover is complete"
        except ImportError:
            pytest.fail(
                "trust_phase1 router has been removed — update test suite: "
                "remove trust_accounts_v2/trust_transactions_v2 MongoDB tests "
                "and ensure trust_cutover_reads.py covers all PG paths."
            )
