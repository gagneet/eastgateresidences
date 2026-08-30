#!/usr/bin/env python3
"""
Backend Tests: Council Rates Endpoint Suite

Tests for the ACT Revenue Office rates endpoints:
  GET  /api/council-rates/{unit_number}
  POST /api/council-rates/{unit_number}/payments
  GET  /api/council-rates/{unit_number}/payments

The GET /council-rates endpoint calls an external ACT Revenue API and may
return source="live", source="cache", or source="unavailable" depending on
availability.  Tests are written to handle all three gracefully.

Run:
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    pytest tests/backend/test_council_rates.py -v

Requirements:
    Backend running on localhost:8003
    Admin auth: conftest.py admin_token fixture plus X-Building-ID header
    Owner auth: conftest.py mint_token(..., legacy_identity=True), unit TH087
"""

import pytest
import requests

BASE_URL = "http://127.0.0.1:8003/api"
OWNER_EMAIL = "avneet@eastgateresidences.com.au"
OWNER_UNIT = "TH087"
OTHER_UNIT = "UA001"

VALID_SOURCES = {"live", "cache", "unavailable"}


def _record_payment(unit_number, payload, headers, cleanup_registry, test_run_id):
    payload = dict(payload)
    if payload.get("notes"):
        payload["notes"] = f"{payload['notes']} [{test_run_id}]"
    else:
        payload["notes"] = f"Automated test payment [{test_run_id}]"
    resp = requests.post(
        f"{BASE_URL}/council-rates/{unit_number}/payments",
        json=payload,
        headers=headers,
    )
    assert resp.status_code == 200, f"Payment creation failed: {resp.text}"
    data = resp.json()
    cleanup_registry.register_id("council_rate_payments", data.get("id"))
    return data


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    """Super-admin requests must carry an explicit East Gate building context."""
    return {"Authorization": f"Bearer {admin_token}", "X-Building-ID": "13195"}


@pytest.fixture(scope="module")
def owner_token(mint_token):
    # This suite validates council-rate authorisation, not password login.
    # Mint a legacy-identity token so local password drift and the Postgres
    # identity token path do not mask endpoint regressions during cutover.
    return mint_token(OWNER_EMAIL, legacy_identity=True)


@pytest.fixture(scope="module")
def owner_headers(owner_token):
    return {"Authorization": f"Bearer {owner_token}"}


@pytest.fixture(scope="module")
def rates_response_admin(admin_headers):
    """Fetch council rates for TH087 as admin, return parsed JSON."""
    resp = requests.get(
        f"{BASE_URL}/council-rates/{OWNER_UNIT}",
        params={"financial_year": "2025-26"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, f"Rates fetch failed: {resp.text}"
    return resp.json()


@pytest.fixture(scope="module")
def recorded_payment(admin_headers, cleanup_registry, test_run_id):
    """Record a test payment for TH087, returning the created payment."""
    payload = {
        "payment_date": "2026-02-01",
        "amount": 450.75,
        "payment_type": "full",
        "notes": f"Test payment from automated test suite [{test_run_id}]",
        "financial_year": "2025-26",
    }
    resp = requests.post(
        f"{BASE_URL}/council-rates/{OWNER_UNIT}/payments",
        json=payload,
        headers=admin_headers,
    )
    assert resp.status_code == 200, f"Payment creation failed: {resp.text}"
    payment = resp.json()
    cleanup_registry.register_id("council_rate_payments", payment.get("id"))
    return payment


# ── Auth / unauthenticated tests ───────────────────────────────────────────────

class TestCouncilRatesAuth:
    def test_get_rates_requires_auth(self):
        resp = requests.get(f"{BASE_URL}/council-rates/{OWNER_UNIT}")
        assert resp.status_code == 401

    def test_post_payment_requires_auth(self):
        resp = requests.post(
            f"{BASE_URL}/council-rates/{OWNER_UNIT}/payments",
            json={"payment_date": "2026-02-01", "amount": 450.75,
                  "payment_type": "full", "financial_year": "2025-26"},
        )
        assert resp.status_code == 401

    def test_list_payments_requires_auth(self):
        resp = requests.get(f"{BASE_URL}/council-rates/{OWNER_UNIT}/payments")
        assert resp.status_code == 401


# ── GET /council-rates/{unit_number} ──────────────────────────────────────────

class TestGetCouncilRates:
    def test_admin_gets_rates_200(self, admin_headers, rates_response_admin):
        assert rates_response_admin is not None

    def test_response_has_required_fields(self, rates_response_admin):
        data = rates_response_admin
        assert "unit_number" in data
        assert "financial_year" in data
        assert "source" in data
        assert "land_tax_applicable" in data

    def test_source_is_valid_enum(self, rates_response_admin):
        assert rates_response_admin["source"] in VALID_SOURCES

    def test_unit_number_uppercased_in_response(self, admin_headers):
        resp = requests.get(
            f"{BASE_URL}/council-rates/th087",
            params={"financial_year": "2025-26"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["unit_number"] == "TH087"

    def test_financial_year_echoed_in_response(self, admin_headers):
        resp = requests.get(
            f"{BASE_URL}/council-rates/{OWNER_UNIT}",
            params={"financial_year": "2025-26"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["financial_year"] == "2025-26"

    def test_default_financial_year_is_2025_26(self, admin_headers):
        """Default year param should be 2025-26 if omitted."""
        resp = requests.get(
            f"{BASE_URL}/council-rates/{OWNER_UNIT}",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["financial_year"] == "2025-26"

    def test_land_tax_applicable_is_bool(self, rates_response_admin):
        assert isinstance(rates_response_admin["land_tax_applicable"], bool)

    def test_optional_rate_components_present_when_data_available(self, rates_response_admin):
        """
        When source is live or cache, numeric rate fields should be present.
        When source is unavailable, they may be None.
        """
        source = rates_response_admin.get("source")
        if source in ("live", "cache"):
            # At least total_rates should be set when data is available
            # (some ACT API fields may legitimately be None)
            data = rates_response_admin
            optional_fields = ["auv", "fixed_charge", "valuation_charge",
                               "pfesl", "safer_families_levy", "total_rates"]
            # At least one should be non-None
            non_null = [f for f in optional_fields if data.get(f) is not None]
            assert len(non_null) >= 1, "Expected at least one rate field to be populated"
        # When unavailable, all None is acceptable — no assertion needed

    def test_unavailable_still_returns_200(self, admin_headers):
        """
        Even when the ACT API is unavailable and no cache exists, the endpoint
        should return 200 with source="unavailable" (not 503 / 500).
        """
        # We can't force unavailability in a live environment, but we can
        # assert any response we get is 200 and has a valid source.
        resp = requests.get(
            f"{BASE_URL}/council-rates/{OTHER_UNIT}",
            params={"financial_year": "2025-26"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["source"] in VALID_SOURCES

    def test_owner_can_view_own_unit(self, owner_headers):
        resp = requests.get(
            f"{BASE_URL}/council-rates/{OWNER_UNIT}",
            params={"financial_year": "2025-26"},
            headers=owner_headers,
        )
        assert resp.status_code == 200

    def test_owner_cannot_view_other_unit(self, owner_headers):
        resp = requests.get(
            f"{BASE_URL}/council-rates/{OTHER_UNIT}",
            params={"financial_year": "2025-26"},
            headers=owner_headers,
        )
        assert resp.status_code == 403

    def test_owner_unit_case_insensitive(self, owner_headers):
        """Owner querying their own unit in lowercase should still work."""
        resp = requests.get(
            f"{BASE_URL}/council-rates/th087",
            params={"financial_year": "2025-26"},
            headers=owner_headers,
        )
        assert resp.status_code == 200

    def test_multiple_units_admin(self, admin_headers):
        """Admin can query several different units."""
        for unit in ("UA001", "UA035", "UA070", "TH071", "TH087"):
            resp = requests.get(
                f"{BASE_URL}/council-rates/{unit}",
                params={"financial_year": "2025-26"},
                headers=admin_headers,
            )
            assert resp.status_code == 200, f"Failed for {unit}: {resp.text}"
            assert resp.json()["unit_number"] == unit


# ── POST /council-rates/{unit_number}/payments ────────────────────────────────

class TestRecordCouncilRatePayment:
    def test_admin_records_payment(self, recorded_payment):
        """Fixture creates the payment and verifies 200 response."""
        assert recorded_payment["id"]
        assert recorded_payment["unit_number"] == OWNER_UNIT
        assert recorded_payment["financial_year"] == "2025-26"
        assert abs(recorded_payment["amount"] - 450.75) < 0.01
        assert recorded_payment["payment_type"] == "full"
        assert recorded_payment["payment_date"] == "2026-02-01"
        assert recorded_payment["created_by"]

    def test_payment_has_notes(self, recorded_payment):
        assert "Test payment" in (recorded_payment.get("notes") or "")

    def test_payment_has_created_at(self, recorded_payment):
        assert recorded_payment["created_at"]

    def test_owner_can_record_own_payment(self, owner_headers, cleanup_registry, test_run_id):
        payload = {
            "payment_date": "2026-03-01",
            "amount": 350.00,
            "payment_type": "partial",
            "financial_year": "2025-26",
        }
        data = _record_payment(OWNER_UNIT, payload, owner_headers, cleanup_registry, test_run_id)
        assert data["unit_number"] == OWNER_UNIT
        assert abs(data["amount"] - 350.00) < 0.01

    def test_owner_cannot_record_payment_for_other_unit(self, owner_headers):
        payload = {
            "payment_date": "2026-03-01",
            "amount": 350.00,
            "payment_type": "full",
            "financial_year": "2025-26",
        }
        resp = requests.post(
            f"{BASE_URL}/council-rates/{OTHER_UNIT}/payments",
            json=payload,
            headers=owner_headers,
        )
        assert resp.status_code == 403

    def test_payment_validates_positive_amount(self, admin_headers):
        payload = {
            "payment_date": "2026-03-01",
            "amount": -100.0,
            "payment_type": "full",
            "financial_year": "2025-26",
        }
        resp = requests.post(
            f"{BASE_URL}/council-rates/{OWNER_UNIT}/payments",
            json=payload,
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_payment_validates_payment_type_enum(self, admin_headers):
        """payment_type must be 'full' or 'partial'."""
        payload = {
            "payment_date": "2026-03-01",
            "amount": 100.0,
            "payment_type": "invalid_type",
            "financial_year": "2025-26",
        }
        resp = requests.post(
            f"{BASE_URL}/council-rates/{OWNER_UNIT}/payments",
            json=payload,
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_payment_missing_required_fields(self, admin_headers):
        resp = requests.post(
            f"{BASE_URL}/council-rates/{OWNER_UNIT}/payments",
            json={"payment_date": "2026-03-01"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_partial_payment_type_accepted(self, admin_headers, cleanup_registry, test_run_id):
        payload = {
            "payment_date": "2026-04-01",
            "amount": 150.00,
            "payment_type": "partial",
            "financial_year": "2025-26",
            "notes": "Instalment 1",
        }
        data = _record_payment(OWNER_UNIT, payload, admin_headers, cleanup_registry, test_run_id)
        assert data["payment_type"] == "partial"


# ── GET /council-rates/{unit_number}/payments ─────────────────────────────────

class TestListCouncilRatePayments:
    def test_admin_lists_payments(self, admin_headers, recorded_payment):
        resp = requests.get(
            f"{BASE_URL}/council-rates/{OWNER_UNIT}/payments",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        payments = resp.json()
        assert isinstance(payments, list)
        assert len(payments) >= 1

    def test_payment_fields_present(self, admin_headers, recorded_payment):
        resp = requests.get(
            f"{BASE_URL}/council-rates/{OWNER_UNIT}/payments",
            headers=admin_headers,
        )
        payment = resp.json()[0]
        for field in ("id", "unit_number", "financial_year", "payment_date",
                      "amount", "payment_type", "created_at", "created_by"):
            assert field in payment, f"Missing field: {field}"

    def test_owner_lists_own_payments(self, owner_headers):
        resp = requests.get(
            f"{BASE_URL}/council-rates/{OWNER_UNIT}/payments",
            headers=owner_headers,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_owner_cannot_list_other_unit_payments(self, owner_headers):
        resp = requests.get(
            f"{BASE_URL}/council-rates/{OTHER_UNIT}/payments",
            headers=owner_headers,
        )
        assert resp.status_code == 403

    def test_filter_by_financial_year(self, admin_headers, recorded_payment):
        resp = requests.get(
            f"{BASE_URL}/council-rates/{OWNER_UNIT}/payments",
            params={"financial_year": "2025-26"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        payments = resp.json()
        # All returned payments should match the requested financial year
        for p in payments:
            assert p["financial_year"] == "2025-26"

    def test_filter_by_nonexistent_year_returns_empty(self, admin_headers):
        resp = requests.get(
            f"{BASE_URL}/council-rates/{OWNER_UNIT}/payments",
            params={"financial_year": "1999-00"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_no_year_filter_returns_all(self, admin_headers, recorded_payment):
        resp_all = requests.get(
            f"{BASE_URL}/council-rates/{OWNER_UNIT}/payments",
            headers=admin_headers,
        )
        resp_filtered = requests.get(
            f"{BASE_URL}/council-rates/{OWNER_UNIT}/payments",
            params={"financial_year": "2025-26"},
            headers=admin_headers,
        )
        assert resp_all.status_code == 200
        assert resp_filtered.status_code == 200
        # Without filter should return >= records than with a specific year filter
        assert len(resp_all.json()) >= len(resp_filtered.json())

    def test_empty_unit_returns_empty_list(self, admin_headers):
        """Units with no recorded payments return empty list, not 404."""
        resp = requests.get(
            f"{BASE_URL}/council-rates/UA050/payments",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_unit_number_case_insensitive_payments(self, admin_headers, recorded_payment):
        resp_upper = requests.get(
            f"{BASE_URL}/council-rates/TH087/payments",
            headers=admin_headers,
        )
        resp_lower = requests.get(
            f"{BASE_URL}/council-rates/th087/payments",
            headers=admin_headers,
        )
        assert resp_upper.status_code == 200
        assert resp_lower.status_code == 200
        assert len(resp_upper.json()) == len(resp_lower.json())

    def test_recorded_payment_appears_in_list(self, admin_headers, recorded_payment):
        resp = requests.get(
            f"{BASE_URL}/council-rates/{OWNER_UNIT}/payments",
            headers=admin_headers,
        )
        payment_ids = [p["id"] for p in resp.json()]
        assert recorded_payment["id"] in payment_ids
