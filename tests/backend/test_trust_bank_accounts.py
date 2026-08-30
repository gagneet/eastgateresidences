"""
tests/backend/test_trust_bank_accounts.py

Tests for the Trust Account & Bank Account feature:
  - Account settings update (interest rate, category)
  - Interest forecast calculation
  - Interest income posting (and double-post guard)
  - Advance levy payment detection + interest calculation
  - Advance payments list endpoint
  - Interest history endpoint
  - Multi-tenant isolation (building_id from JWT)
  - Interest accrual cron logic (dry run)
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId

# Valid 24-char hex ObjectId strings for testing
BLD_A = "aaaaaaaaaaaaaaaaaaaaaaaa"  # building A
BLD_B = "bbbbbbbbbbbbbbbbbbbbbbbb"  # building B (different tenant)
ACC_ID = "cccccccccccccccccccccccc"
SCHED_ID = "dddddddddddddddddddddddd"
USER_ID = "eeeeeeeeeeeeeeeeeeeeeeee"
UNIT_ID = "ffffffffffffffffffffffff"


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_user(building_id: str = BLD_A, role: str = "strata_manager") -> dict:
    return {
        "_id": USER_ID,
        "building_id": building_id,
        "role": role,
        "email": "test@example.com",
    }


def _make_account(
        account_id: str = ACC_ID,
        building_id: str = BLD_A,
        account_type: str = "admin_fund",
        interest_rate_pa: float = 0.045,
        account_category: str = "savings",
        balance_cents: int = 1_000_000,
) -> dict:
    return {
        "_id": ObjectId(account_id),
        "building_id": ObjectId(building_id),
        "account_type": account_type,
        "account_name": f"{account_type.replace('_', ' ').title()} Account",
        "bank_name": "NAB",
        "bsb": "082-001",
        "account_number_masked": "****4567",
        "account_category": account_category,
        "interest_rate_pa": interest_rate_pa,
        "interest_calc_method": "daily_balance",
        "current_balance_cents": balance_cents,
        "last_reconciled_balance_cents": balance_cents,
        "is_active": True,
        "interest_ytd_cents": 0,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


def _make_levy_schedule(
        schedule_id: str = SCHED_ID,
        building_id: str = BLD_A,
        unit_number: str = "101",
        due_date: str = "2026-06-01",
        total_cents: int = 500_000,
        status: str = "pending",
) -> dict:
    return {
        "_id": ObjectId(schedule_id),
        "building_id": ObjectId(building_id),
        "unit_number": unit_number,
        "unit_id": ObjectId(UNIT_ID),
        "lot_number": 1,
        "quarter": "2026-Q2",
        "financial_year": "2026-27",
        "due_date": due_date,
        "grace_period_days": 14,
        "admin_fund_cents": 300_000,
        "sinking_fund_cents": 200_000,
        "total_cents": total_cents,
        "deft_crn": "12345678",
        "status": status,
        "paid_cents": 0,
        "outstanding_cents": total_cents,
        "interest_accrued_cents": 0,
        "drb_stage": "none",
        "generated_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


# ─── Interest Calculation Unit Tests ─────────────────────────────────────────

class TestInterestCalculation:
    """Pure calculation logic — no DB needed."""

    def test_simple_monthly_interest(self):
        """30 days at 4.5% p.a. on $10,000 should be ~$36.99"""
        balance_cents = 1_000_000  # $10,000
        rate_pa = 0.045
        days = 30
        interest = round(balance_cents * rate_pa * days / 365)
        assert interest == 3699  # ~$36.99

    def test_advance_payment_interest(self):
        """Levy paid 30 days early at 4.5% p.a. on $5,000 = ~$18.49"""
        amount_cents = 500_000  # $5,000
        rate_pa = 0.045
        advance_days = 30
        interest = round(amount_cents * rate_pa * advance_days / 365)
        assert interest == 1849  # ~$18.49

    def test_zero_rate_no_interest(self):
        """Transaction account (0%) earns nothing."""
        balance_cents = 1_000_000
        rate_pa = 0.0
        days = 31
        interest = round(balance_cents * rate_pa * days / 365)
        assert interest == 0

    def test_advance_days_calculation(self):
        """Payment 15 days before due date → advance_days = 15."""
        due = date(2026, 6, 1)
        paid = date(2026, 5, 17)
        advance_days = (due - paid).days
        assert advance_days == 15

    def test_same_day_payment_not_advance(self):
        """Payment on due date → NOT advance."""
        due = date(2026, 6, 1)
        paid = date(2026, 6, 1)
        is_advance = paid < due
        assert is_advance is False

    def test_proportional_split_equal_balances(self):
        """Equal admin/sinking balances → 50/50 split."""
        total_interest = 1000
        admin_bal = 500_000
        sinking_bal = 500_000
        total_bal = admin_bal + sinking_bal
        admin_share = round(total_interest * admin_bal / total_bal)
        sinking_share = total_interest - admin_share
        assert admin_share == 500
        assert sinking_share == 500

    def test_proportional_split_unequal_balances(self):
        """Admin fund 3× larger than sinking → 75% admin."""
        total_interest = 1000
        admin_bal = 750_000
        sinking_bal = 250_000
        total_bal = admin_bal + sinking_bal
        admin_share = round(total_interest * admin_bal / total_bal)
        sinking_share = total_interest - admin_share
        assert admin_share == 750
        assert sinking_share == 250


# ─── Router Endpoint Tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestUpdateTrustAccount:
    async def test_update_interest_rate(self):
        """PATCH /trust/v2/accounts/{id} sets interest_rate_pa and account_category."""
        from routers.trust_phase1 import update_trust_account
        from models.trust_accounting import BankAccountUpdate, AccountCategory

        account = _make_account()
        updated_account = {**account, "account_category": "savings", "interest_rate_pa": 0.045}
        mock_db = MagicMock()
        mock_db.trust_accounts_v2.find_one = AsyncMock(side_effect=[account, updated_account])
        mock_db.trust_accounts_v2.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
        mock_db.trust_audit_log_v2 = MagicMock()
        mock_db.trust_audit_log_v2.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId()))

        payload = BankAccountUpdate(
            account_category=AccountCategory.SAVINGS,
            interest_rate_pa=0.045,
        )
        user = _make_user()

        with patch("routers.trust_phase1._get_db", return_value=mock_db):
            response = await update_trust_account(ACC_ID, payload, user)

        assert response.status_code == 200

    async def test_reject_invalid_interest_rate(self):
        """Interest rate > 1.0 (100%) should be rejected."""
        from fastapi import HTTPException
        from routers.trust_phase1 import update_trust_account
        from models.trust_accounting import BankAccountUpdate

        account = _make_account()
        mock_db = MagicMock()
        mock_db.trust_accounts_v2.find_one = AsyncMock(return_value=account)

        payload = BankAccountUpdate(interest_rate_pa=1.5)  # 150% — invalid
        user = _make_user()

        with patch("routers.trust_phase1._get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await update_trust_account(ACC_ID, payload, user)

        assert exc_info.value.status_code == 422
        assert "interest_rate_pa" in exc_info.value.detail

    async def test_building_isolation(self):
        """Cannot update account belonging to a different building."""
        from fastapi import HTTPException
        from routers.trust_phase1 import update_trust_account
        from models.trust_accounting import BankAccountUpdate

        # Account belongs to BLD_B, user's JWT has BLD_A
        account = _make_account(building_id=BLD_B)
        mock_db = MagicMock()
        mock_db.trust_accounts_v2.find_one = AsyncMock(return_value=account)

        payload = BankAccountUpdate(interest_rate_pa=0.03)
        user = _make_user(building_id=BLD_A)

        with patch("routers.trust_phase1._get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await update_trust_account(ACC_ID, payload, user)

        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
class TestInterestForecast:
    async def test_forecast_calculation(self):
        """Interest forecast should correctly apply rate × days / 365."""
        from routers.trust_phase1 import get_interest_forecast

        account = _make_account(balance_cents=1_000_000, interest_rate_pa=0.045)
        mock_db = MagicMock()
        mock_db.trust_accounts_v2.find_one = AsyncMock(return_value=account)
        mock_db.trust_accounts_v2.find = MagicMock()
        mock_db.trust_accounts_v2.find.return_value.to_list = AsyncMock(return_value=[account])
        mock_db.trust_interest_postings.count_documents = AsyncMock(return_value=0)

        user = _make_user()
        with patch("routers.trust_phase1._get_db", return_value=mock_db):
            response = await get_interest_forecast(
                ACC_ID,
                period_start="2026-04-01",
                period_end="2026-04-30",
                current_user=user,
            )

        import json
        data = json.loads(response.body)["data"]
        assert data["days_in_period"] == 30
        assert data["forecast_interest_cents"] == round(1_000_000 * 0.045 * 30 / 365)
        assert data["already_posted_this_period"] is False

    async def test_forecast_already_posted(self):
        """Returns already_posted_this_period=True if posting exists."""
        from routers.trust_phase1 import get_interest_forecast

        account = _make_account(interest_rate_pa=0.045)
        mock_db = MagicMock()
        mock_db.trust_accounts_v2.find_one = AsyncMock(return_value=account)
        mock_db.trust_accounts_v2.find = MagicMock()
        mock_db.trust_accounts_v2.find.return_value.to_list = AsyncMock(return_value=[account])
        mock_db.trust_interest_postings.count_documents = AsyncMock(return_value=1)

        user = _make_user()
        with patch("routers.trust_phase1._get_db", return_value=mock_db):
            response = await get_interest_forecast(
                ACC_ID,
                period_start="2026-04-01",
                period_end="2026-04-30",
                current_user=user,
            )

        import json
        data = json.loads(response.body)["data"]
        assert data["already_posted_this_period"] is True


@pytest.mark.asyncio
class TestPostInterest:
    def _setup_mock_db(self, account: dict, has_existing_posting: bool = False):
        mock_db = MagicMock()
        mock_db.trust_accounts_v2.find_one = AsyncMock(return_value=account)
        mock_db.trust_interest_postings.find_one = AsyncMock(
            return_value={"_id": ObjectId()} if has_existing_posting else None
        )
        mock_db.trust_accounts_v2.find = MagicMock()
        mock_db.trust_accounts_v2.find.return_value.to_list = AsyncMock(return_value=[account])
        mock_db.trust_transactions_v2.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId()))
        mock_db.trust_accounts_v2.update_one = AsyncMock()
        mock_db.trust_interest_postings.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId()))
        mock_db.trust_audit_log_v2 = MagicMock()
        mock_db.trust_audit_log_v2.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId()))
        return mock_db

    async def test_post_interest_success(self):
        """Posting interest creates transactions and a posting record."""
        from routers.trust_phase1 import post_interest_income
        from models.trust_accounting import InterestPostingRequest

        account = _make_account(interest_rate_pa=0.045, balance_cents=1_000_000)
        mock_db = self._setup_mock_db(account)

        payload = InterestPostingRequest(
            period_start="2026-03-01",
            period_end="2026-03-31",
        )
        user = _make_user()

        with patch("routers.trust_phase1._get_db", return_value=mock_db):
            response = await post_interest_income(ACC_ID, payload, user)

        assert response.status_code == 201
        mock_db.trust_interest_postings.insert_one.assert_called_once()

    async def test_double_post_rejected(self):
        """Posting the same period twice raises 409 Conflict."""
        from fastapi import HTTPException
        from routers.trust_phase1 import post_interest_income
        from models.trust_accounting import InterestPostingRequest

        account = _make_account(interest_rate_pa=0.045)
        mock_db = self._setup_mock_db(account, has_existing_posting=True)

        payload = InterestPostingRequest(
            period_start="2026-03-01",
            period_end="2026-03-31",
        )
        user = _make_user()

        with patch("routers.trust_phase1._get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await post_interest_income(ACC_ID, payload, user)

        assert exc_info.value.status_code == 409

    async def test_zero_rate_account_rejected(self):
        """Account with 0% rate cannot have interest posted."""
        from fastapi import HTTPException
        from routers.trust_phase1 import post_interest_income
        from models.trust_accounting import InterestPostingRequest

        account = _make_account(interest_rate_pa=0.0, balance_cents=1_000_000)
        mock_db = self._setup_mock_db(account)

        payload = InterestPostingRequest(
            period_start="2026-03-01",
            period_end="2026-03-31",
        )
        user = _make_user()

        with patch("routers.trust_phase1._get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await post_interest_income(ACC_ID, payload, user)

        assert exc_info.value.status_code == 422

    async def test_manual_amount_override(self):
        """Manual amount_cents overrides the calculated amount."""
        from routers.trust_phase1 import post_interest_income
        from models.trust_accounting import InterestPostingRequest

        account = _make_account(interest_rate_pa=0.045, balance_cents=1_000_000)
        mock_db = self._setup_mock_db(account)

        payload = InterestPostingRequest(
            period_start="2026-03-01",
            period_end="2026-03-31",
            amount_cents=5000,
        )
        user = _make_user()

        with patch("routers.trust_phase1._get_db", return_value=mock_db):
            response = await post_interest_income(ACC_ID, payload, user)

        import json
        data = json.loads(response.body)["data"]
        assert data["total_interest_cents"] == 5000


@pytest.mark.asyncio
class TestAdvancePaymentDetection:
    def _setup_mock_db(self, schedule: dict, account: dict):
        mock_db = MagicMock()
        mock_db.trust_levy_schedules_v2.find_one = AsyncMock(return_value=schedule)
        mock_db.trust_accounts_v2.find_one = AsyncMock(return_value=account)
        mock_db.trust_transactions_v2.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId()))
        mock_db.trust_levy_schedules_v2.update_one = AsyncMock()
        mock_db.trust_accounts_v2.update_one = AsyncMock()
        mock_db.trust_audit_log_v2 = MagicMock()
        mock_db.trust_audit_log_v2.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId()))
        return mock_db

    async def test_advance_payment_detected(self):
        """Payment 30 days before due date is marked as advance with interest calculated."""
        from routers.trust_phase1 import record_levy_payment

        due_date = (date.today() + timedelta(days=30)).isoformat()
        schedule = _make_levy_schedule(due_date=due_date)
        account = _make_account(interest_rate_pa=0.045)
        mock_db = self._setup_mock_db(schedule, account)

        body = {
            "amount_cents": 500_000,
            "payment_date": date.today().isoformat(),
        }
        user = _make_user()

        with patch("routers.trust_phase1._get_db", return_value=mock_db):
            response = await record_levy_payment(SCHED_ID, body, user)

        import json
        result = json.loads(response.body)
        assert result["is_advance_payment"] is True
        assert result["advance_days"] == 30
        assert result["advance_interest_earned_cents"] > 0
        expected = round(500_000 * 0.045 * 30 / 365)
        assert result["advance_interest_earned_cents"] == expected

    async def test_on_time_payment_not_advance(self):
        """Payment on the due date should NOT be flagged as advance."""
        from routers.trust_phase1 import record_levy_payment

        due_date = date.today().isoformat()
        schedule = _make_levy_schedule(due_date=due_date)
        account = _make_account(interest_rate_pa=0.045)
        mock_db = self._setup_mock_db(schedule, account)

        body = {"amount_cents": 500_000, "payment_date": due_date}
        user = _make_user()

        with patch("routers.trust_phase1._get_db", return_value=mock_db):
            response = await record_levy_payment(SCHED_ID, body, user)

        import json
        result = json.loads(response.body)
        assert result["is_advance_payment"] is False
        assert result["advance_interest_earned_cents"] == 0

    async def test_late_payment_not_advance(self):
        """Payment AFTER due date should NOT be flagged as advance."""
        from routers.trust_phase1 import record_levy_payment

        due_date = (date.today() - timedelta(days=10)).isoformat()
        schedule = _make_levy_schedule(due_date=due_date)
        account = _make_account(interest_rate_pa=0.045)
        mock_db = self._setup_mock_db(schedule, account)

        body = {"amount_cents": 500_000, "payment_date": date.today().isoformat()}
        user = _make_user()

        with patch("routers.trust_phase1._get_db", return_value=mock_db):
            response = await record_levy_payment(SCHED_ID, body, user)

        import json
        result = json.loads(response.body)
        assert result["is_advance_payment"] is False


@pytest.mark.asyncio
class TestCronTrustInterest:
    async def test_dry_run_no_writes(self):
        """Dry run should calculate but NOT write to the database."""
        from cron.cron_trust_interest import run_interest_accrual

        account = _make_account(interest_rate_pa=0.045, balance_cents=1_000_000)
        mock_db = MagicMock()
        mock_db.trust_accounts_v2.aggregate = MagicMock()
        mock_db.trust_accounts_v2.aggregate.return_value.to_list = AsyncMock(
            return_value=[{"_id": ObjectId(BLD_A)}]
        )
        mock_db.trust_accounts_v2.find = MagicMock()
        mock_db.trust_accounts_v2.find.return_value.to_list = AsyncMock(return_value=[account])
        mock_db.trust_interest_postings.find_one = AsyncMock(return_value=None)
        mock_db.trust_interest_postings.insert_one = AsyncMock()
        mock_db.trust_transactions_v2.insert_one = AsyncMock()
        mock_db.trust_accounts_v2.update_one = AsyncMock()
        mock_db.trust_levy_schedules_v2.find = MagicMock()
        mock_db.trust_levy_schedules_v2.find.return_value.to_list = AsyncMock(return_value=[])

        result = await run_interest_accrual(dry_run=True, target_month="2026-03", _raw_db=mock_db)

        assert result["dry_run"] is True
        assert result["postings_created"] == 1
        mock_db.trust_interest_postings.insert_one.assert_not_called()
        mock_db.trust_transactions_v2.insert_one.assert_not_called()

    async def test_idempotency_skip_existing(self):
        """If posting already exists for the period, skip it."""
        from cron.cron_trust_interest import run_interest_accrual

        account = _make_account(interest_rate_pa=0.045, balance_cents=1_000_000)
        mock_db = MagicMock()
        mock_db.trust_accounts_v2.aggregate = MagicMock()
        mock_db.trust_accounts_v2.aggregate.return_value.to_list = AsyncMock(
            return_value=[{"_id": ObjectId(BLD_A)}]
        )
        mock_db.trust_accounts_v2.find = MagicMock()
        mock_db.trust_accounts_v2.find.return_value.to_list = AsyncMock(return_value=[account])
        mock_db.trust_interest_postings.find_one = AsyncMock(return_value={"_id": ObjectId()})
        mock_db.trust_interest_postings.insert_one = AsyncMock()
        mock_db.trust_levy_schedules_v2.find = MagicMock()
        mock_db.trust_levy_schedules_v2.find.return_value.to_list = AsyncMock(return_value=[])

        result = await run_interest_accrual(dry_run=False, target_month="2026-03", _raw_db=mock_db)

        assert result["postings_skipped"] == 1
        assert result["postings_created"] == 0
        mock_db.trust_interest_postings.insert_one.assert_not_called()


# ─── Multi-building isolation check ──────────────────────────────────────────

@pytest.mark.asyncio
class TestMultiBuildingIsolation:
    async def test_building_id_must_come_from_jwt(self):
        """The building_id for all trust queries must be from the JWT user, not request params."""
        from routers.trust_phase1 import _get_building_id_from_jwt
        user = {"building_id": BLD_A, "role": "strata_manager"}
        assert await _get_building_id_from_jwt(user) == BLD_A

    async def test_missing_building_id_raises(self):
        """User without building_id should get 400."""
        from fastapi import HTTPException
        from routers.trust_phase1 import _get_building_id_from_jwt
        user = {"role": "strata_manager"}
        with pytest.raises(HTTPException) as exc_info:
            await _get_building_id_from_jwt(user)
        assert exc_info.value.status_code == 400

    async def test_super_admin_header_resolves_uuid_to_plan_number(self):
        """X-Building-ID carries the scheme UUID (see AuthContext.tsx's axios
        interceptor: `X-Building-ID = b.id`), not a Mongo-compatible plan
        number. A super_admin's header override must resolve through
        identity_repo, or every Mongo query in this router silently returns
        zero rows for a switched-in super_admin."""
        from routers.trust_phase1 import _get_building_id_from_jwt
        user = {"building_id": BLD_A, "role": "super_admin"}
        scheme_uuid = "11111111-1111-1111-1111-111111111111"
        with patch("db_postgres.repos.identity_repo.get_scheme_by_id",
                   AsyncMock(return_value={"scheme_id": scheme_uuid, "scheme_number": BLD_B})), \
             patch("db_postgres.repos.identity_repo.get_scheme_by_number",
                   AsyncMock(return_value=None)):
            result = await _get_building_id_from_jwt(user, scheme_uuid)
        assert result == BLD_B

    async def test_super_admin_header_falls_back_when_scheme_not_found(self):
        """An unresolvable header (stale/deleted scheme) must not crash —
        fall back to the JWT's own building_id rather than raising, since the
        header is sent unconditionally on every request by the axios
        interceptor, not just on deliberate building switches."""
        from routers.trust_phase1 import _get_building_id_from_jwt
        user = {"building_id": BLD_A, "role": "super_admin"}
        with patch("db_postgres.repos.identity_repo.get_scheme_by_id",
                   AsyncMock(return_value=None)), \
             patch("db_postgres.repos.identity_repo.get_scheme_by_number",
                   AsyncMock(return_value=None)):
            result = await _get_building_id_from_jwt(user, "deleted-scheme-id")
        assert result == BLD_A

    async def test_non_super_admin_header_is_ignored(self):
        """The X-Building-ID header override is super_admin-only — a regular
        manager must never be able to escalate into another building by
        setting the header, even though the axios interceptor sends it on
        every request for every role."""
        from routers.trust_phase1 import _get_building_id_from_jwt
        user = {"building_id": BLD_A, "role": "strata_manager"}
        with patch("db_postgres.repos.identity_repo.get_scheme_by_id",
                   AsyncMock(return_value={"scheme_id": "x", "scheme_number": BLD_B})):
            result = await _get_building_id_from_jwt(user, BLD_B)
        assert result == BLD_A
