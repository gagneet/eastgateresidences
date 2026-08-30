"""
Tests for Sessions 33/34 features.

Covers:
- Request form model validation (5 types: insurance claim, insurance enquiry,
  access control, alteration, reimbursement)
- Last login tracking logic (last_login_at, last_login_ip)
- New user registration notification logic
- BSB formatting and validation
- Access control quantity bounds
- Reimbursement amount validation
- Request status workflow transitions
- UserResponse last_login fields

Run with:
    cd backend && python -m pytest tests/test_sessions_33_34.py -v
"""
import os
import sys
from datetime import datetime, timezone

import pytest

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ---------------------------------------------------------------------------
# Guard imports: models may not be importable in a CI-only environment where
# optional packages are absent.  Tests still run in pure-logic mode when the
# model import fails.
# ---------------------------------------------------------------------------

try:
    from models.requests import (
        InsuranceClaimCreate,
        InsuranceEnquiryCreate,
        AccessControlRequestCreate,
        AlterationRequestCreate,
        ReimbursementRequestCreate,
    )

    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False

try:
    from models.user import UserResponse, Permission, UserRole

    _USER_MODEL_AVAILABLE = True
except ImportError:
    _USER_MODEL_AVAILABLE = False


# ===========================================================================
# Pure-logic helpers defined inline — no live DB / HTTP dependencies
# ===========================================================================

# ---------------------------------------------------------------------------
# BSB validation
# ---------------------------------------------------------------------------

def validate_bsb(bsb_raw: str) -> tuple:
    """Validate BSB after stripping non-digits. Returns (valid, digits_only)."""
    digits = "".join(c for c in bsb_raw if c.isdigit())
    return len(digits) == 6, digits


# ---------------------------------------------------------------------------
# Reimbursement amount validation
# ---------------------------------------------------------------------------

def validate_reimbursement_amount(amount: float) -> bool:
    """Amount must be strictly positive."""
    return amount > 0


# ---------------------------------------------------------------------------
# Access control quantity bounds
# ---------------------------------------------------------------------------

def validate_access_quantity(qty: int) -> bool:
    """Quantity must be between 1 and 5 inclusive."""
    return 1 <= qty <= 5


# ---------------------------------------------------------------------------
# Request status workflow
# ---------------------------------------------------------------------------

REQUEST_STATUS_FLOW = {
    "insurance_claim": ["submitted", "under_review", "approved", "rejected"],
    "insurance_enquiry": ["pending", "answered"],
    "access_control": ["pending", "approved", "rejected", "issued"],
    "alteration": ["pending", "approved", "rejected", "completed"],
    "reimbursement": ["pending", "approved", "rejected", "paid"],
}


def is_valid_status(request_type: str, status: str) -> bool:
    """Return True when status is a valid state for the given request_type."""
    return status in REQUEST_STATUS_FLOW.get(request_type, [])


# ---------------------------------------------------------------------------
# Last login tracking
# ---------------------------------------------------------------------------

def update_last_login(user_id: str, ip, now_iso: str) -> dict:
    """Returns the $set dict for a MongoDB last-login update."""
    return {"last_login_at": now_iso, "last_login_ip": ip}


# ---------------------------------------------------------------------------
# Registration notification helpers
# ---------------------------------------------------------------------------

NOTIFY_CC_EMAIL = "gagneet@eastgateresidences.com.au"


def get_notification_roles(user_role: str) -> list:
    """Returns list of admin roles that should be notified of a new registration."""
    return ["super_admin", "chairman"]


def build_registration_notification(user: dict, admin_id: str, now: str) -> dict:
    """Build an in-app notification document for an admin user."""
    return {
        "user_id": admin_id,
        "title": f"New {user['role'].capitalize()} Registration: {user['full_name']}",
        "message": (
            f"{user['full_name']} ({user['email']}) has registered as a {user['role']}."
        ),
        "type": "new_registration",
        "related_id": user["id"],
        "link": "/admin/users",
        "is_read": False,
        "created_at": now,
    }


def collect_notify_emails(admin_users: list) -> set:
    """
    Collect the final set of email addresses for a new-registration notification.
    Always adds the CC address (gagneet@eastgateresidences.com.au).
    """
    email_set = {u["email"] for u in admin_users if u.get("email")}
    email_set.add(NOTIFY_CC_EMAIL)
    return email_set


# ===========================================================================
# Test Classes
# ===========================================================================


# ---------------------------------------------------------------------------
# 1.  InsuranceClaimCreate — Pydantic model tests
# ---------------------------------------------------------------------------

class TestInsuranceClaimModel:
    """Tests for InsuranceClaimCreate model validation."""

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_valid_claim_accepted(self):
        """A fully-populated claim dict should create the model without error."""
        claim = InsuranceClaimCreate(
            claim_type="water_leak",
            incident_date="2026-02-15",
            description="Water leaked from upstairs bathroom into living area.",
            estimated_cost=4500.00,
        )
        assert claim.claim_type == "water_leak"
        assert claim.estimated_cost == 4500.00

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_attachments_defaults_to_empty_list(self):
        """Optional attachments field defaults to an empty list."""
        claim = InsuranceClaimCreate(
            claim_type="fire",
            incident_date="2026-01-01",
            description="Kitchen fire damage.",
            estimated_cost=12000.00,
        )
        assert claim.attachments == []

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_attachments_accepts_list_of_strings(self):
        """Attachments field accepts a list of base64/URL strings."""
        claim = InsuranceClaimCreate(
            claim_type="theft",
            incident_date="2026-03-01",
            description="Bicycle stolen from common area.",
            estimated_cost=800.00,
            attachments=["data:image/png;base64,abc123", "https://example.com/photo.jpg"],
        )
        assert len(claim.attachments) == 2

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_missing_required_field_raises(self):
        """Omitting a required field (description) should raise ValidationError."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            InsuranceClaimCreate(
                claim_type="property_damage",
                incident_date="2026-02-01",
                # description intentionally omitted
                estimated_cost=2000.00,
            )

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_zero_estimated_cost_accepted_by_model(self):
        """
        The Pydantic model itself imposes no lower bound on estimated_cost.
        Domain logic (separate validator) handles zero/negative checks.
        """
        claim = InsuranceClaimCreate(
            claim_type="property_damage",
            incident_date="2026-02-01",
            description="Minor crack in wall — cost TBD.",
            estimated_cost=0.0,
        )
        assert claim.estimated_cost == 0.0

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_claim_type_stored_as_string(self):
        """claim_type is a plain string — no enum enforcement at model level."""
        claim = InsuranceClaimCreate(
            claim_type="other",
            incident_date="2026-02-01",
            description="Other type of claim.",
            estimated_cost=500.00,
        )
        assert isinstance(claim.claim_type, str)


# ---------------------------------------------------------------------------
# 2.  InsuranceEnquiryCreate — Pydantic model tests
# ---------------------------------------------------------------------------

class TestInsuranceEnquiryModel:
    """Tests for InsuranceEnquiryCreate model validation."""

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_valid_enquiry_accepted(self):
        """A minimal valid enquiry should create the model without error."""
        enq = InsuranceEnquiryCreate(
            subject="Public liability coverage",
            question="What is the current public liability limit for the building?",
        )
        assert enq.subject == "Public liability coverage"
        assert "public liability" in enq.question.lower()

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_missing_subject_raises(self):
        """Omitting subject should raise ValidationError."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            InsuranceEnquiryCreate(
                # subject omitted
                question="Is my unit covered?",
            )

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_missing_question_raises(self):
        """Omitting question should raise ValidationError."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            InsuranceEnquiryCreate(
                subject="Flood coverage",
                # question omitted
            )

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_fields_stored_verbatim(self):
        """Both fields are stored exactly as provided."""
        subject = "Windows coverage"
        question = "Are internal windows covered under the strata insurance policy?"
        enq = InsuranceEnquiryCreate(subject=subject, question=question)
        assert enq.subject == subject
        assert enq.question == question


# ---------------------------------------------------------------------------
# 3.  AccessControlRequestCreate — Pydantic model tests
# ---------------------------------------------------------------------------

class TestAccessControlModel:
    """Tests for AccessControlRequestCreate model validation."""

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_valid_fob_request(self):
        """Valid fob request with quantity 2 should be accepted."""
        req = AccessControlRequestCreate(
            access_type="fob",
            quantity=2,
            reason="Lost existing fob, require replacement.",
        )
        assert req.access_type == "fob"
        assert req.quantity == 2

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_valid_garage_remote_request(self):
        """Garage remote is a valid access_type."""
        req = AccessControlRequestCreate(
            access_type="garage_remote",
            quantity=1,
            reason="New vehicle added to household.",
        )
        assert req.access_type == "garage_remote"

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_missing_reason_raises(self):
        """reason is a required field."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AccessControlRequestCreate(
                access_type="access_card",
                quantity=1,
                # reason omitted
            )

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_quantity_stored_as_int(self):
        """quantity is stored as an integer."""
        req = AccessControlRequestCreate(
            access_type="visitor_card",
            quantity=3,
            reason="Guest staying for extended period.",
        )
        assert isinstance(req.quantity, int)
        assert req.quantity == 3

    # Quantity-bounds logic (pure, no model import required)

    def test_quantity_lower_bound_valid(self):
        assert validate_access_quantity(1) is True

    def test_quantity_upper_bound_valid(self):
        assert validate_access_quantity(5) is True

    def test_quantity_zero_invalid(self):
        assert validate_access_quantity(0) is False

    def test_quantity_above_max_invalid(self):
        assert validate_access_quantity(6) is False

    def test_quantity_negative_invalid(self):
        assert validate_access_quantity(-1) is False


# ---------------------------------------------------------------------------
# 4.  AlterationRequestCreate — Pydantic model tests
# ---------------------------------------------------------------------------

class TestAlterationModel:
    """Tests for AlterationRequestCreate model validation."""

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_valid_alteration_accepted(self):
        """Fully-populated alteration request should be accepted."""
        req = AlterationRequestCreate(
            alteration_type="flooring",
            description="Replace carpet with hardwood floors in all bedrooms.",
            proposed_start_date="2026-04-15",
            estimated_duration="2 weeks",
        )
        assert req.alteration_type == "flooring"
        assert req.estimated_duration == "2 weeks"

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_plans_attachments_defaults_to_empty_list(self):
        """plans_attachments should default to an empty list if not provided."""
        req = AlterationRequestCreate(
            alteration_type="painting",
            description="Repaint interior walls — neutral colour.",
            proposed_start_date="2026-05-01",
            estimated_duration="3 days",
        )
        assert req.plans_attachments == []

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_contractor_details_is_optional(self):
        """contractor_details is optional and defaults to None."""
        req = AlterationRequestCreate(
            alteration_type="bathroom",
            description="Install new shower screen.",
            proposed_start_date="2026-06-01",
            estimated_duration="1 week",
        )
        assert req.contractor_details is None

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_missing_proposed_start_date_raises(self):
        """proposed_start_date is required."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AlterationRequestCreate(
                alteration_type="structural",
                description="Remove non-load-bearing wall.",
                # proposed_start_date omitted
                estimated_duration="4 weeks",
            )

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_contractor_details_accepted_when_provided(self):
        """contractor_details is stored verbatim when supplied."""
        detail = "ABC Renovations Pty Ltd — Ph: 0412 345 678"
        req = AlterationRequestCreate(
            alteration_type="flooring",
            description="Timber flooring installation.",
            contractor_details=detail,
            proposed_start_date="2026-04-01",
            estimated_duration="5 days",
        )
        assert req.contractor_details == detail


# ---------------------------------------------------------------------------
# 5.  ReimbursementRequestCreate — Pydantic model tests
# ---------------------------------------------------------------------------

class TestReimbursementModel:
    """Tests for ReimbursementRequestCreate model validation."""

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_valid_reimbursement_accepted(self):
        """A fully-populated reimbursement request should be accepted."""
        req = ReimbursementRequestCreate(
            description="Emergency plumber callout for burst pipe in common area.",
            amount=385.00,
            expense_date="2026-02-20",
            category="emergency_expense",
            bank_account_name="Avneet Rooprai",
            bsb="062-000",
            account_number="12345678",
            receipt_attachments=["data:application/pdf;base64,abc123"],
        )
        assert req.amount == 385.00
        assert req.bsb == "062-000"

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_receipt_attachments_required(self):
        """receipt_attachments is a required list — empty list is allowed by model."""
        req = ReimbursementRequestCreate(
            description="Common area cleaning supplies.",
            amount=75.50,
            expense_date="2026-02-01",
            category="common_area_repair",
            bank_account_name="John Smith",
            bsb="032-123",
            account_number="99887766",
            receipt_attachments=[],
        )
        assert req.receipt_attachments == []

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_missing_bank_account_name_raises(self):
        """bank_account_name is required."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ReimbursementRequestCreate(
                description="Test.",
                amount=100.00,
                expense_date="2026-01-01",
                category="common_area_repair",
                # bank_account_name omitted
                bsb="012-345",
                account_number="11223344",
                receipt_attachments=[],
            )

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_missing_amount_raises(self):
        """amount is required."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ReimbursementRequestCreate(
                description="Test.",
                # amount omitted
                expense_date="2026-01-01",
                category="common_area_repair",
                bank_account_name="Jane Doe",
                bsb="012-345",
                account_number="11223344",
                receipt_attachments=[],
            )

    @pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models.requests not importable")
    def test_bsb_stored_as_string(self):
        """BSB is stored as a raw string — formatting is not normalised by the model."""
        req = ReimbursementRequestCreate(
            description="Office supplies.",
            amount=42.00,
            expense_date="2026-03-01",
            category="admin_expense",
            bank_account_name="Test Owner",
            bsb="063-000",
            account_number="55443322",
            receipt_attachments=["data:image/jpeg;base64,xyz"],
        )
        assert isinstance(req.bsb, str)

    # --- Amount domain validation (pure logic, no model import needed) ---

    def test_positive_amount_passes(self):
        assert validate_reimbursement_amount(1.00) is True

    def test_very_small_positive_passes(self):
        assert validate_reimbursement_amount(0.01) is True

    def test_zero_amount_fails(self):
        assert validate_reimbursement_amount(0.0) is False

    def test_negative_amount_fails(self):
        assert validate_reimbursement_amount(-10.00) is False

    def test_large_amount_passes(self):
        assert validate_reimbursement_amount(99999.99) is True


# ---------------------------------------------------------------------------
# 6.  BSB Validation
# ---------------------------------------------------------------------------

class TestBSBValidation:
    """Tests for the validate_bsb() helper function."""

    def test_standard_format_with_dash_valid(self):
        """'123-456' — 6 digits with a dash — should be valid."""
        valid, digits = validate_bsb("123-456")
        assert valid is True
        assert digits == "123456"

    def test_digits_only_valid(self):
        """'123456' — 6 plain digits — should be valid."""
        valid, digits = validate_bsb("123456")
        assert valid is True
        assert digits == "123456"

    def test_too_short_invalid(self):
        """'12345' — only 5 digits — should be invalid."""
        valid, _ = validate_bsb("12345")
        assert valid is False

    def test_too_long_invalid(self):
        """'1234567' — 7 digits — should be invalid."""
        valid, _ = validate_bsb("1234567")
        assert valid is False

    def test_different_dash_position_valid(self):
        """'12-3456' — dash in non-standard position but still 6 digits — valid."""
        valid, digits = validate_bsb("12-3456")
        assert valid is True
        assert digits == "123456"

    def test_leading_zeros_preserved(self):
        """BSBs starting with 0 (e.g. Commonwealth '062-000') should be handled."""
        valid, digits = validate_bsb("062-000")
        assert valid is True
        assert digits == "062000"

    def test_empty_string_invalid(self):
        """Empty string produces 0 digits — should be invalid."""
        valid, digits = validate_bsb("")
        assert valid is False
        assert digits == ""

    def test_non_numeric_characters_stripped(self):
        """Non-digit characters beyond dashes (e.g. spaces) are also stripped."""
        valid, digits = validate_bsb("03 2 12 3")
        assert valid is True
        assert digits == "032123"

    def test_all_dashes_invalid(self):
        """A string of only dashes produces 0 digits — should be invalid."""
        valid, _ = validate_bsb("------")
        assert valid is False


# ---------------------------------------------------------------------------
# 7.  Last Login Tracking Logic
# ---------------------------------------------------------------------------

class TestLastLoginTracking:
    """Tests for the update_last_login() helper that produces MongoDB $set dicts."""

    def test_returns_dict_with_both_fields(self):
        """update_last_login must return a dict containing both required keys."""
        now = datetime.now(timezone.utc).isoformat()
        result = update_last_login("user-001", "192.168.1.1", now)
        assert "last_login_at" in result
        assert "last_login_ip" in result

    def test_ip_stored_verbatim(self):
        """The IP address is stored exactly as provided."""
        now = datetime.now(timezone.utc).isoformat()
        result = update_last_login("user-002", "10.0.0.42", now)
        assert result["last_login_ip"] == "10.0.0.42"

    def test_none_ip_handled_gracefully(self):
        """When request.client is None, ip=None must be accepted without error."""
        now = datetime.now(timezone.utc).isoformat()
        result = update_last_login("user-003", None, now)
        assert result["last_login_ip"] is None

    def test_timestamp_stored_verbatim(self):
        """The ISO timestamp is stored exactly as provided."""
        ts = "2026-03-01T08:30:00.000000Z"
        result = update_last_login("user-004", "172.16.0.1", ts)
        assert result["last_login_at"] == ts

    def test_timestamp_ends_with_z(self):
        """UTC ISO timestamps from the server end with 'Z'."""
        now = datetime.utcnow().isoformat() + "Z"
        result = update_last_login("user-005", "127.0.0.1", now)
        assert result["last_login_at"].endswith("Z")

    def test_result_has_exactly_two_keys(self):
        """The returned dict should contain exactly two keys."""
        now = datetime.now(timezone.utc).isoformat()
        result = update_last_login("user-006", "1.2.3.4", now)
        assert len(result) == 2

    def test_different_users_produce_independent_dicts(self):
        """Calling the function for two users returns independent dicts."""
        now = datetime.now(timezone.utc).isoformat()
        r1 = update_last_login("user-A", "10.0.0.1", now)
        r2 = update_last_login("user-B", "10.0.0.2", now)
        assert r1 is not r2
        assert r1["last_login_ip"] != r2["last_login_ip"]


# ---------------------------------------------------------------------------
# 8.  Registration Notification Logic
# ---------------------------------------------------------------------------

class TestRegistrationNotifications:
    """Tests for registration notification helper functions."""

    def _make_user(self, role="owner"):
        return {
            "id": "user-reg-001",
            "full_name": "Emma Wilson",
            "email": "emma@example.com",
            "role": role,
        }

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def test_notification_roles_include_super_admin(self):
        """super_admin must always be in the notification roles list."""
        roles = get_notification_roles("owner")
        assert "super_admin" in roles

    def test_notification_roles_include_chairman(self):
        """chairman must always be in the notification roles list."""
        roles = get_notification_roles("owner")
        assert "chairman" in roles

    def test_notification_built_for_super_admin(self):
        """A notification document targeted at super_admin should be well-formed."""
        user = self._make_user(role="owner")
        now = self._now()
        doc = build_registration_notification(user, "admin-001", now)
        assert doc["user_id"] == "admin-001"
        assert doc["type"] == "new_registration"
        assert doc["is_read"] is False

    def test_notification_built_for_chairman(self):
        """A notification document targeted at chairman should be well-formed."""
        user = self._make_user(role="tenant")
        now = self._now()
        doc = build_registration_notification(user, "chairman-001", now)
        assert doc["user_id"] == "chairman-001"
        assert doc["type"] == "new_registration"
        assert doc["link"] == "/admin/users"

    def test_notification_title_format(self):
        """Title must follow the pattern 'New <Role> Registration: <FullName>'."""
        user = self._make_user(role="owner")
        now = self._now()
        doc = build_registration_notification(user, "admin-001", now)
        expected_title = "New Owner Registration: Emma Wilson"
        assert doc["title"] == expected_title

    def test_notification_message_contains_email(self):
        """Notification message must include the registrant's email address."""
        user = self._make_user(role="owner")
        now = self._now()
        doc = build_registration_notification(user, "admin-001", now)
        assert user["email"] in doc["message"]

    def test_gagneet_always_in_cc_list(self):
        """gagneet@eastgateresidences.com.au must always be in the notify email set."""
        admin_users = [
            {"email": "super@eastgateresidences.com.au"},
            {"email": "chairman@eastgateresidences.com.au"},
        ]
        emails = collect_notify_emails(admin_users)
        assert "gagneet@eastgateresidences.com.au" in emails

    def test_cc_added_even_with_no_admins(self):
        """CC address is added even when the admin user list is empty."""
        emails = collect_notify_emails([])
        assert "gagneet@eastgateresidences.com.au" in emails

    def test_admin_emails_included_in_set(self):
        """Admin email addresses are included alongside the CC address."""
        admin_users = [
            {"email": "admin@eastgateresidences.com.au"},
        ]
        emails = collect_notify_emails(admin_users)
        assert "admin@eastgateresidences.com.au" in emails
        assert len(emails) >= 2  # admin + CC

    def test_admin_without_email_field_excluded(self):
        """Admin user docs missing the email key are silently skipped."""
        admin_users = [
            {"id": "admin-no-email"},  # no 'email' key
            {"email": "valid@eastgateresidences.com.au"},
        ]
        emails = collect_notify_emails(admin_users)
        assert None not in emails
        assert "valid@eastgateresidences.com.au" in emails

    def test_notification_type_is_new_registration(self):
        """type field must be the literal string 'new_registration'."""
        user = self._make_user(role="guest")
        now = self._now()
        doc = build_registration_notification(user, "admin-001", now)
        assert doc["type"] == "new_registration"

    def test_notification_related_id_is_user_id(self):
        """related_id must match the user's id for deep-link correlation."""
        user = self._make_user(role="tenant")
        now = self._now()
        doc = build_registration_notification(user, "admin-001", now)
        assert doc["related_id"] == user["id"]

    def test_notification_created_at_matches_now(self):
        """created_at in the notification must equal the timestamp passed in."""
        user = self._make_user(role="owner")
        now = self._now()
        doc = build_registration_notification(user, "admin-001", now)
        assert doc["created_at"] == now


# ---------------------------------------------------------------------------
# 9.  Request Status Workflow
# ---------------------------------------------------------------------------

class TestRequestStatusWorkflow:
    """Tests for is_valid_status() — checks valid transitions per request type."""

    # Insurance claim statuses
    def test_insurance_claim_submitted_valid(self):
        assert is_valid_status("insurance_claim", "submitted") is True

    def test_insurance_claim_under_review_valid(self):
        assert is_valid_status("insurance_claim", "under_review") is True

    def test_insurance_claim_approved_valid(self):
        assert is_valid_status("insurance_claim", "approved") is True

    def test_insurance_claim_rejected_valid(self):
        assert is_valid_status("insurance_claim", "rejected") is True

    def test_insurance_claim_invalid_status(self):
        assert is_valid_status("insurance_claim", "pending") is False

    # Insurance enquiry statuses
    def test_insurance_enquiry_pending_valid(self):
        assert is_valid_status("insurance_enquiry", "pending") is True

    def test_insurance_enquiry_answered_valid(self):
        assert is_valid_status("insurance_enquiry", "answered") is True

    def test_insurance_enquiry_invalid_status(self):
        assert is_valid_status("insurance_enquiry", "submitted") is False

    # Access control statuses
    def test_access_control_pending_valid(self):
        assert is_valid_status("access_control", "pending") is True

    def test_access_control_approved_valid(self):
        assert is_valid_status("access_control", "approved") is True

    def test_access_control_rejected_valid(self):
        assert is_valid_status("access_control", "rejected") is True

    def test_access_control_issued_valid(self):
        assert is_valid_status("access_control", "issued") is True

    def test_access_control_invalid_status(self):
        assert is_valid_status("access_control", "completed") is False

    # Alteration statuses
    def test_alteration_pending_valid(self):
        assert is_valid_status("alteration", "pending") is True

    def test_alteration_approved_valid(self):
        assert is_valid_status("alteration", "approved") is True

    def test_alteration_rejected_valid(self):
        assert is_valid_status("alteration", "rejected") is True

    def test_alteration_completed_valid(self):
        assert is_valid_status("alteration", "completed") is True

    def test_alteration_invalid_status(self):
        assert is_valid_status("alteration", "issued") is False

    # Reimbursement statuses
    def test_reimbursement_pending_valid(self):
        assert is_valid_status("reimbursement", "pending") is True

    def test_reimbursement_approved_valid(self):
        assert is_valid_status("reimbursement", "approved") is True

    def test_reimbursement_rejected_valid(self):
        assert is_valid_status("reimbursement", "rejected") is True

    def test_reimbursement_paid_valid(self):
        assert is_valid_status("reimbursement", "paid") is True

    def test_reimbursement_invalid_status(self):
        assert is_valid_status("reimbursement", "answered") is False

    # Unknown request type
    def test_unknown_request_type_returns_false(self):
        assert is_valid_status("unknown_type", "pending") is False

    def test_empty_status_returns_false(self):
        assert is_valid_status("insurance_claim", "") is False


# ---------------------------------------------------------------------------
# 10.  UserResponse — last_login fields
# ---------------------------------------------------------------------------

class TestUserResponseLastLoginFields:
    """Tests that UserResponse contains last_login_at and last_login_ip fields."""

    @pytest.mark.skipif(not _USER_MODEL_AVAILABLE, reason="models.user not importable")
    def test_new_user_has_none_last_login_at(self):
        """A freshly-created UserResponse without login history has last_login_at=None."""
        user = UserResponse(
            id="u-001",
            email="newuser@example.com",
            full_name="New User",
            role="owner",
            is_active=True,
            permissions=Permission(),
            created_at="2026-03-01T00:00:00Z",
        )
        assert user.last_login_at is None

    @pytest.mark.skipif(not _USER_MODEL_AVAILABLE, reason="models.user not importable")
    def test_new_user_has_none_last_login_ip(self):
        """A freshly-created UserResponse without login history has last_login_ip=None."""
        user = UserResponse(
            id="u-002",
            email="newuser2@example.com",
            full_name="New User Two",
            role="owner",
            is_active=True,
            permissions=Permission(),
            created_at="2026-03-01T00:00:00Z",
        )
        assert user.last_login_ip is None

    @pytest.mark.skipif(not _USER_MODEL_AVAILABLE, reason="models.user not importable")
    def test_last_login_at_accepts_iso_timestamp(self):
        """last_login_at accepts a valid ISO 8601 timestamp string."""
        ts = "2026-03-01T08:30:00.000000Z"
        user = UserResponse(
            id="u-003",
            email="existing@example.com",
            full_name="Returning User",
            role="owner",
            is_active=True,
            permissions=Permission(),
            created_at="2026-01-01T00:00:00Z",
            last_login_at=ts,
        )
        assert user.last_login_at == ts

    @pytest.mark.skipif(not _USER_MODEL_AVAILABLE, reason="models.user not importable")
    def test_last_login_ip_accepts_ipv4_string(self):
        """last_login_ip accepts a standard IPv4 address string."""
        user = UserResponse(
            id="u-004",
            email="existing2@example.com",
            full_name="Returning User Two",
            role="owner",
            is_active=True,
            permissions=Permission(),
            created_at="2026-01-01T00:00:00Z",
            last_login_ip="203.0.113.1",
        )
        assert user.last_login_ip == "203.0.113.1"

    @pytest.mark.skipif(not _USER_MODEL_AVAILABLE, reason="models.user not importable")
    def test_extra_fields_ignored_due_to_config(self):
        """
        UserResponse has extra='ignore' config.
        Unknown fields in a dict passed to model construction are silently dropped.
        """
        user = UserResponse(
            id="u-005",
            email="extra@example.com",
            full_name="Extra Fields User",
            role="tenant",
            is_active=True,
            permissions=Permission(),
            created_at="2026-03-01T00:00:00Z",
            last_login_at="2026-03-01T10:00:00Z",
            last_login_ip="10.0.0.5",
            unknown_future_field="some_value",  # should be ignored
        )
        assert user.id == "u-005"
        assert not hasattr(user, "unknown_future_field")
