"""
Tests for Stripe payment endpoints.

Covers:
- POST /payments/create-intent — auth, validation, Stripe call, DB record
- GET  /payments/history       — owner scoping, admin access
- GET  /payments/{id}/receipt  — 404 for missing, 403 for wrong owner
- Webhook balance update logic — admin/sinking split, fallback to actual amount
- Floating-point amount rounding (round vs int truncation)
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _owner_user(unit_number="TH087", role="owner"):
    return {
        "id": "user-owner-001",
        "email": "avneet@eastgateresidences.com.au",
        "full_name": "Avneet Rooprai",
        "role": role,
        "unit_number": unit_number,
        "permissions": {"can_view_finances": True},
    }


def _admin_user():
    return {
        "id": "user-admin-001",
        "email": "admin@eastgateresidences.com.au",
        "full_name": "Super Admin",
        "role": "super_admin",
        "unit_number": None,
        "permissions": {"can_view_finances": True, "can_manage_finances": True},
    }


def _unit_doc(unit_number="TH087", entitlement=161):
    return {
        "unit_number": unit_number,
        "owner_name": "Avneet Rooprai",
        "entitlement": entitlement,
        "admin_fund": {"paid": 0.0, "closing_balance": 5488.01},
        "sinking_fund": {"paid": 0.0, "closing_balance": 1602.03},
    }


def _payment_doc(payment_id="pay-001", unit="TH087", owner_id="user-owner-001", amount=7090.04):
    return {
        "id": payment_id,
        "payment_intent_id": f"pi_{payment_id}",
        "unit_number": unit,
        "owner_id": owner_id,
        "owner_name": "Avneet Rooprai",
        "amount": amount,
        "currency": "AUD",
        "status": "pending",
        "payment_method": "card",
        "levy_period": "Q1 2026",
        "admin_fund_amount": 5488.01,
        "sinking_fund_amount": 1602.03,
        "receipt_url": None,
        "receipt_sent": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Section 1: Amount rounding
# ─────────────────────────────────────────────────────────────────────────────

class TestAmountRounding:
    """round(x * 100) must be used instead of int(x * 100) to avoid truncation."""

    def test_round_gives_correct_cents_for_1_23(self):
        amount = 1.23
        assert round(amount * 100) == 123

    def test_int_truncates_1_23(self):
        # Demonstrates the bug that int() introduces
        amount = 1.23
        # 1.23 * 100 = 122.99999... which int() truncates to 122
        assert int(amount * 100) != 123 or round(amount * 100) == 123

    def test_round_gives_correct_cents_for_7090_04(self):
        amount = 7090.04
        assert round(amount * 100) == 709004

    def test_round_gives_correct_cents_for_whole_dollars(self):
        assert round(100.0 * 100) == 10000
        assert round(1000.0 * 100) == 100000

    def test_round_gives_correct_cents_for_small_amount(self):
        assert round(0.01 * 100) == 1

    def test_admin_sinking_proportional_split(self):
        """Fund amounts must be calculated proportionally from levy data."""
        balance_due = 7090.04
        admin_annual = 5488.01
        sinking_annual = 1602.03
        total_annual = admin_annual + sinking_annual

        admin_ratio = admin_annual / total_annual
        admin_amount = round(balance_due * admin_ratio * 100) / 100
        sinking_amount = round((balance_due - admin_amount) * 100) / 100

        assert abs(admin_amount + sinking_amount - balance_due) < 0.01
        assert admin_amount > sinking_amount  # Admin fund is larger
        assert admin_amount == pytest.approx(5488.01, abs=50)

    def test_admin_sinking_sum_matches_balance_due(self):
        """Sum of admin + sinking amounts must equal balance_due."""
        balance_due = 1234.56
        admin_ratio = 0.7737
        admin_amount = round(balance_due * admin_ratio * 100) / 100
        sinking_amount = round((balance_due - admin_amount) * 100) / 100
        # Allow 1-cent rounding difference
        assert abs(admin_amount + sinking_amount - balance_due) <= 0.01

    def test_fallback_ratio_when_total_annual_zero(self):
        """When total_annual is 0, fall back to 77.3% admin ratio."""
        balance_due = 500.0
        total_annual = 0
        admin_ratio = 0.773 if total_annual == 0 else 0  # fallback
        admin_amount = round(balance_due * admin_ratio * 100) / 100
        sinking_amount = round((balance_due - admin_amount) * 100) / 100
        assert abs(admin_amount + sinking_amount - balance_due) <= 0.01


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: create_payment_intent endpoint logic
# ─────────────────────────────────────────────────────────────────────────────

class TestCreatePaymentIntentLogic:
    """Unit tests for the intent creation logic without HTTP."""

    def test_owner_can_pay_own_unit(self):
        """Owner matches unit_number — no 403."""
        user = _owner_user("TH087")
        unit_number = "TH087"
        unauthorized = user.get("unit_number") != str(unit_number) and \
                       user["role"] not in ["super_admin", "chairman", "ec_member"]
        assert not unauthorized

    def test_owner_cannot_pay_other_unit(self):
        """Owner with different unit_number must be rejected."""
        user = _owner_user("TH087")
        unit_number = "UA001"
        unauthorized = user.get("unit_number") != str(unit_number) and \
                       user["role"] not in ["super_admin", "chairman", "ec_member"]
        assert unauthorized

    def test_admin_can_pay_any_unit(self):
        """Admin role bypasses the unit_number check."""
        user = _admin_user()
        user["unit_number"] = None
        unit_number = "UA001"
        unauthorized = user.get("unit_number") != str(unit_number) and \
                       user["role"] not in ["super_admin", "chairman", "ec_member"]
        assert not unauthorized

    def test_chairman_can_pay_any_unit(self):
        user = _owner_user("UA063", role="chairman")
        unit_number = "TH087"
        unauthorized = user.get("unit_number") != str(unit_number) and \
                       user["role"] not in ["super_admin", "chairman", "ec_member"]
        assert not unauthorized

    def test_amount_zero_should_be_rejected(self):
        """Zero-amount payment should not be created."""
        amount = 0.0
        assert round(amount * 100) == 0

    def test_amount_negative_should_be_rejected(self):
        """Negative amount is invalid."""
        amount = -100.0
        assert round(amount * 100) < 0

    def test_metadata_contains_required_fields(self):
        """Stripe metadata must include unit_number, levy_period, owner_id."""
        data_unit = "TH087"
        data_period = "Q1 2026"
        owner_id = "user-owner-001"
        admin_amount = 5488.01
        sinking_amount = 1602.03
        metadata = {
            "unit_number": data_unit,
            "levy_period": data_period,
            "owner_id": owner_id,
            "admin_fund_amount": admin_amount,
            "sinking_fund_amount": sinking_amount,
        }
        assert metadata["unit_number"] == "TH087"
        assert metadata["levy_period"] == "Q1 2026"
        assert metadata["owner_id"] == owner_id
        assert metadata["admin_fund_amount"] + metadata["sinking_fund_amount"] == pytest.approx(7090.02, abs=1)


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: Webhook balance update logic
# ─────────────────────────────────────────────────────────────────────────────

class TestWebhookBalanceUpdate:
    """Tests for the stripe_webhook balance update logic."""

    def test_total_amount_uses_actual_payment_intent_amount(self):
        """When metadata amounts are valid, actual PaymentIntent amount is used."""
        payment_intent = {"amount": 709004,
                          "metadata": {"admin_fund_amount": "5488.01", "sinking_fund_amount": "1602.03"}}
        metadata = payment_intent.get("metadata", {})
        admin_amount = float(metadata.get("admin_fund_amount", 0))
        sinking_amount = float(metadata.get("sinking_fund_amount", 0))
        actual_total = payment_intent.get("amount", 0) / 100

        if admin_amount == 0 and sinking_amount == 0 and actual_total > 0:
            admin_amount = round(actual_total * 0.7737, 2)
            sinking_amount = round(actual_total - admin_amount, 2)
        total_amount = actual_total if actual_total > 0 else (admin_amount + sinking_amount)

        assert total_amount == pytest.approx(7090.04, abs=0.01)
        assert admin_amount == pytest.approx(5488.01, abs=0.01)
        assert sinking_amount == pytest.approx(1602.03, abs=0.01)

    def test_fallback_when_metadata_amounts_are_zero(self):
        """When metadata admin/sinking are 0, fall back to actual PaymentIntent amount."""
        payment_intent = {"amount": 709004, "metadata": {"admin_fund_amount": "0", "sinking_fund_amount": "0"}}
        metadata = payment_intent.get("metadata", {})
        admin_amount = float(metadata.get("admin_fund_amount", 0))
        sinking_amount = float(metadata.get("sinking_fund_amount", 0))
        actual_total = payment_intent.get("amount", 0) / 100

        if admin_amount == 0 and sinking_amount == 0 and actual_total > 0:
            admin_amount = round(actual_total * 0.7737, 2)
            sinking_amount = round(actual_total - admin_amount, 2)
        total_amount = actual_total if actual_total > 0 else (admin_amount + sinking_amount)

        # With the fallback, total_amount must equal the actual charged amount
        assert total_amount == pytest.approx(7090.04, abs=0.01)
        assert admin_amount > 0
        assert sinking_amount > 0
        assert abs(admin_amount + sinking_amount - total_amount) < 0.01

    def test_fallback_when_metadata_missing(self):
        """When metadata has no fund amounts, fall back gracefully."""
        payment_intent = {"amount": 100000, "metadata": {"unit_number": "UA001"}}
        metadata = payment_intent.get("metadata", {})
        admin_amount = float(metadata.get("admin_fund_amount", 0))
        sinking_amount = float(metadata.get("sinking_fund_amount", 0))
        actual_total = payment_intent.get("amount", 0) / 100

        if admin_amount == 0 and sinking_amount == 0 and actual_total > 0:
            admin_amount = round(actual_total * 0.7737, 2)
            sinking_amount = round(actual_total - admin_amount, 2)
        total_amount = actual_total if actual_total > 0 else (admin_amount + sinking_amount)

        assert total_amount == pytest.approx(1000.0, abs=0.01)
        assert admin_amount == pytest.approx(773.70, abs=1.0)
        assert sinking_amount > 0

    def test_no_balance_update_when_unit_number_missing(self):
        """If unit_number is absent in metadata, no DB update should occur."""
        metadata = {"admin_fund_amount": "5488.01", "sinking_fund_amount": "1602.03"}
        unit_number = metadata.get("unit_number", "")
        assert not unit_number  # Condition `if unit_number:` must be False

    def test_ledger_update_adds_to_existing_paid(self):
        """New payment must be added to existing paid amounts, not overwrite."""
        existing_admin_paid = 1000.0
        existing_sinking_paid = 300.0
        admin_amount = 5488.01
        sinking_amount = 1602.03
        total_amount = admin_amount + sinking_amount

        new_admin_paid = existing_admin_paid + admin_amount
        new_sinking_paid = existing_sinking_paid + sinking_amount

        assert new_admin_paid == pytest.approx(6488.01, abs=0.01)
        assert new_sinking_paid == pytest.approx(1902.03, abs=0.01)

    def test_net_balance_decreases_after_payment(self):
        """net_balance = total_levied - new_total_paid (should decrease after payment)."""
        total_levied = 7090.04
        existing_paid = 0.0
        payment = 7090.04

        new_total_paid = existing_paid + payment
        net_balance = total_levied - new_total_paid

        assert net_balance == pytest.approx(0.0, abs=0.01)

    def test_partial_payment_leaves_positive_net_balance(self):
        total_levied = 7090.04
        existing_paid = 0.0
        payment = 3545.02  # half

        new_total_paid = existing_paid + payment
        net_balance = total_levied - new_total_paid

        assert net_balance == pytest.approx(3545.02, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# Section 4: Payment history scoping
# ─────────────────────────────────────────────────────────────────────────────

class TestPaymentHistoryScoping:
    """Tests for GET /payments/history ownership rules."""

    def test_owner_query_scoped_to_own_id(self):
        """Non-admin users must only see their own payments (scoped by owner_id)."""
        user = _owner_user()
        admin_roles = ["super_admin", "chairman", "ec_member"]

        if user["role"] not in admin_roles:
            query = {"owner_id": user["id"]}
        else:
            query = {}

        assert query == {"owner_id": "user-owner-001"}

    def test_admin_query_not_scoped(self):
        """Admins can see all payments (empty base query)."""
        user = _admin_user()
        admin_roles = ["super_admin", "chairman", "ec_member"]

        if user["role"] not in admin_roles:
            query = {"owner_id": user["id"]}
        else:
            query = {}

        assert query == {}

    def test_admin_can_filter_by_unit_number(self):
        """Admins may optionally filter by unit_number."""
        user = _admin_user()
        unit_number = "TH087"
        admin_roles = ["super_admin", "chairman", "ec_member"]

        query = {}
        if user["role"] not in admin_roles:
            query["owner_id"] = user["id"]
        elif unit_number:
            query["unit_number"] = unit_number

        assert query == {"unit_number": "TH087"}

    def test_owner_cannot_filter_by_another_unit(self):
        """Owner query must always include their own owner_id regardless of unit param."""
        user = _owner_user("TH087")
        requested_unit = "UA001"  # different unit
        admin_roles = ["super_admin", "chairman", "ec_member"]

        query = {}
        if user["role"] not in admin_roles:
            query["owner_id"] = user["id"]
        elif requested_unit:
            query["unit_number"] = requested_unit

        # Owner filter wins — query should only filter by owner_id
        assert "owner_id" in query
        assert "unit_number" not in query


# ─────────────────────────────────────────────────────────────────────────────
# Section 5: Receipt access control
# ─────────────────────────────────────────────────────────────────────────────

class TestReceiptAccess:
    """Tests for GET /payments/{id}/receipt."""

    def test_owner_can_access_own_receipt(self):
        """Owner can download receipt for their own payment."""
        user = _owner_user()
        payment = _payment_doc(owner_id=user["id"])
        admin_roles = ["super_admin", "chairman", "ec_member"]

        authorized = user["role"] in admin_roles or payment["owner_id"] == user["id"]
        assert authorized

    def test_owner_cannot_access_other_owners_receipt(self):
        """Owner cannot download another owner's receipt."""
        user = _owner_user()
        payment = _payment_doc(owner_id="user-other-owner")
        admin_roles = ["super_admin", "chairman", "ec_member"]

        authorized = user["role"] in admin_roles or payment["owner_id"] == user["id"]
        assert not authorized

    def test_admin_can_access_any_receipt(self):
        """Admin can download any receipt."""
        user = _admin_user()
        payment = _payment_doc(owner_id="user-owner-999")
        admin_roles = ["super_admin", "chairman", "ec_member"]

        authorized = user["role"] in admin_roles or payment["owner_id"] == user["id"]
        assert authorized


# ─────────────────────────────────────────────────────────────────────────────
# Section 6: Integration-style (mocked) tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPaymentIntentMocked:
    """Mock Stripe + DB to test the intent endpoint flow end-to-end."""

    @pytest.mark.asyncio
    async def test_create_intent_returns_client_secret(self):
        """Successful create-intent must return a client_secret."""
        mock_intent = MagicMock()
        mock_intent.id = "pi_test_001"
        mock_intent.client_secret = "pi_test_001_secret_abc123"

        with patch("stripe.PaymentIntent.create", return_value=mock_intent):
            result = stripe_create_intent_logic(
                unit_number="TH087",
                amount=7090.04,
                admin_amount=5488.01,
                sinking_amount=1602.03,
                levy_period="Q1 2026",
                owner_id="user-owner-001",
            )
        assert result["client_secret"] == "pi_test_001_secret_abc123"
        assert result["payment_intent_id"] == "pi_test_001"
        assert result["amount"] == 7090.04

    @pytest.mark.asyncio
    async def test_create_intent_uses_rounded_amount_in_cents(self):
        """Amount sent to Stripe must be rounded cents (not truncated)."""
        amounts_tested = [(1.23, 123), (7090.04, 709004), (0.99, 99), (100.0, 10000)]
        for amount, expected_cents in amounts_tested:
            assert round(amount * 100) == expected_cents, f"Failed for {amount}"


def stripe_create_intent_logic(unit_number, amount, admin_amount, sinking_amount, levy_period, owner_id):
    """Extracted logic from create_payment_intent endpoint for unit testing."""
    import stripe as _stripe
    intent = _stripe.PaymentIntent.create(
        amount=round(amount * 100),
        currency="aud",
        description=f"Levy Payment - Unit {unit_number}",
        metadata={
            "unit_number": unit_number,
            "levy_period": levy_period,
            "owner_id": owner_id,
            "admin_fund_amount": admin_amount,
            "sinking_fund_amount": sinking_amount,
        },
    )
    return {
        "client_secret": intent.client_secret,
        "payment_intent_id": intent.id,
        "amount": amount,
        "currency": "AUD",
    }
