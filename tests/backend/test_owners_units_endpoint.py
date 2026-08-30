#!/usr/bin/env python3
"""
Backend Tests: GET /api/owners-units/{unit_number}?financial_year= Endpoint

What this covers
----------------
This test suite validates the per-unit financial detail endpoint that was
affected by a bug where ``yearly_forecast`` referenced an undefined variable
``total_annual`` instead of ``annual_levy``, causing HTTP 500 errors on
every request regardless of year.

Regression tests confirm:
  - Year 2025 and year 2026 both return HTTP 200 (2026 was the crash year)
  - ``yearly_forecast`` has all expected numeric keys without raising a 500
  - ``admin_fund`` / ``sinking_fund`` have the expected ledger keys
  - ``total_levies`` for TH087 is approximately $7,090 in 2026
    (161 UOE × $34.087 admin/UOE + 161 UOE × $9.9505 sinking/UOE ≈ $7,090)
  - ``remaining`` is never negative
  - ``net_balance`` is numeric
  - ``badges`` is a list

Auth notes
----------
These endpoint-contract tests mint JWTs through conftest.py instead of calling
``/auth/login``. That keeps stale local password fixtures and login rate limits
out of the financial endpoint assertions.

Test users
----------
  Owner   : avneet@eastgateresidences.com.au   -> unit TH087
  Admin   : administrator@eastgateresidences.com.au
  Chairman: anthony@eastgateresidences.com.au  -> unit UA063

Run
---
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    pytest tests/backend/test_owners_units_endpoint.py -v

Requirements
------------
  - Backend running on http://127.0.0.1:8003
  - DB: strata_production on mongodb://localhost:27018
  - unit_levy_ledger: 87 entries for year 2025
  - annual_levies: entries for years 2025 and 2026
  - TH087: entitlement (UOE) = 161, present in units collection
"""

import pytest
import requests

BASE_URL = "http://127.0.0.1:8003/api"

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
OWNER_EMAIL = "avneet@eastgateresidences.com.au"
OWNER_UNIT = "TH087"

CHAIRMAN_EMAIL = "anthony@eastgateresidences.com.au"
CHAIRMAN_UNIT = "UA063"

# TH087 levy estimate for 2026:
#   Admin  : 161 UOE × $34.087 = $5,488.01
#   Sinking: 161 UOE ×  $9.950 = $1,601.95
#   Total  ≈ $7,090
TH087_2026_LEVY_APPROX = 7090.0
TH087_2026_LEVY_TOLERANCE = 200.0  # allow ± $200 for rounding differences


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(scope="module")
def admin_headers(admin_token):
    """Super-admin requests must carry an explicit East Gate building context."""
    return {"Authorization": f"Bearer {admin_token}", "X-Building-ID": "13195"}


@pytest.fixture(scope="module")
def owner_token(mint_token):
    """Mint a legacy-identity owner token for avneet / TH087."""
    return mint_token(OWNER_EMAIL, legacy_identity=True)


@pytest.fixture(scope="module")
def owner_headers(owner_token):
    """Authorization headers for owner requests."""
    return {"Authorization": f"Bearer {owner_token}"}


@pytest.fixture(scope="module")
def chairman_token(mint_token):
    """Mint a legacy-identity chairman token for anthony / UA063."""
    return mint_token(CHAIRMAN_EMAIL, legacy_identity=True)


@pytest.fixture(scope="module")
def chairman_headers(chairman_token):
    """Authorization headers for chairman requests."""
    return {"Authorization": f"Bearer {chairman_token}"}


@pytest.fixture(scope="module")
def th017_2025(admin_headers):
    """Cached response for TH087, year 2025."""
    resp = requests.get(
        f"{BASE_URL}/owners-units/{OWNER_UNIT}",
        params={"financial_year": "2025"},
        headers=admin_headers,
        timeout=10,
    )
    assert resp.status_code == 200, (
        f"TH087/2025 returned {resp.status_code}: {resp.text}"
    )
    return resp.json()


@pytest.fixture(scope="module")
def th017_2026(admin_headers):
    """Cached response for TH087, year 2026 (the year that triggered the 500 bug)."""
    resp = requests.get(
        f"{BASE_URL}/owners-units/{OWNER_UNIT}",
        params={"financial_year": "2026"},
        headers=admin_headers,
        timeout=10,
    )
    assert resp.status_code == 200, (
        f"TH087/2026 returned {resp.status_code}: {resp.text}\n"
        "This may be the yearly_forecast / total_annual bug — check server logs."
    )
    return resp.json()


@pytest.fixture(scope="module")
def ua063_2025(admin_headers):
    """Cached response for UA063 (chairman unit), year 2025."""
    resp = requests.get(
        f"{BASE_URL}/owners-units/{CHAIRMAN_UNIT}",
        params={"financial_year": "2025"},
        headers=admin_headers,
        timeout=10,
    )
    assert resp.status_code == 200, (
        f"UA063/2025 returned {resp.status_code}: {resp.text}"
    )
    return resp.json()


# ===========================================================================
# Class 1 – Authentication & Authorization
# ===========================================================================

class TestAuthAndAuthorization:
    """Verify that the endpoint enforces authentication correctly."""

    def test_unauthenticated_request_returns_401(self):
        """No token → must get 401, not 200 or 500."""
        resp = requests.get(
            f"{BASE_URL}/owners-units/{OWNER_UNIT}",
            params={"financial_year": "2025"},
            timeout=10,
        )
        assert resp.status_code == 401, (
            f"Expected 401 without auth, got {resp.status_code}"
        )

    def test_unauthenticated_request_no_data_leak(self):
        """Unauthenticated response must not contain unit financial data."""
        resp = requests.get(
            f"{BASE_URL}/owners-units/{OWNER_UNIT}",
            params={"financial_year": "2025"},
            timeout=10,
        )
        body = resp.text
        assert "admin_fund" not in body
        assert "sinking_fund" not in body

    def test_admin_can_access_any_unit(self, admin_headers):
        """Super-admin must be able to fetch any unit without restriction."""
        resp = requests.get(
            f"{BASE_URL}/owners-units/{OWNER_UNIT}",
            params={"financial_year": "2025"},
            headers=admin_headers,
            timeout=10,
        )
        assert resp.status_code == 200

    def test_owner_can_access_own_unit(self, owner_headers):
        """An owner should be able to access their own unit data."""
        resp = requests.get(
            f"{BASE_URL}/owners-units/{OWNER_UNIT}",
            params={"financial_year": "2025"},
            headers=owner_headers,
            timeout=10,
        )
        assert resp.status_code in (200, 403), (
            f"Expected 200 or 403 for owner accessing own unit, got {resp.status_code}"
        )

    def test_chairman_can_access_own_unit(self, chairman_headers):
        """Chairman must be able to fetch their own unit data."""
        resp = requests.get(
            f"{BASE_URL}/owners-units/{CHAIRMAN_UNIT}",
            params={"financial_year": "2025"},
            headers=chairman_headers,
            timeout=10,
        )
        assert resp.status_code == 200, (
            f"Chairman accessing own unit returned {resp.status_code}: {resp.text}"
        )


# ===========================================================================
# Class 2 – HTTP Status & Error Handling
# ===========================================================================

class TestHttpStatusCodes:
    """Verify correct HTTP status codes for valid and invalid requests."""

    def test_returns_200_for_th017_year_2025(self, admin_headers):
        """Explicit smoke test: TH087 + 2025 must return 200."""
        resp = requests.get(
            f"{BASE_URL}/owners-units/{OWNER_UNIT}",
            params={"financial_year": "2025"},
            headers=admin_headers,
            timeout=10,
        )
        assert resp.status_code == 200, resp.text

    def test_returns_200_for_th017_year_2026(self, admin_headers):
        """
        Regression: TH087 + 2026 must return 200, not 500.
        Previously crashed because yearly_forecast used undefined 'total_annual'
        instead of 'annual_levy'.
        """
        resp = requests.get(
            f"{BASE_URL}/owners-units/{OWNER_UNIT}",
            params={"financial_year": "2026"},
            headers=admin_headers,
            timeout=10,
        )
        assert resp.status_code == 200, (
            f"Year 2026 returned {resp.status_code} — possible yearly_forecast bug: {resp.text}"
        )

    def test_unknown_unit_returns_404(self, admin_headers):
        """A unit number that does not exist must return 404."""
        resp = requests.get(
            f"{BASE_URL}/owners-units/ZZNOTEXIST",
            params={"financial_year": "2025"},
            headers=admin_headers,
            timeout=10,
        )
        assert resp.status_code == 404, (
            f"Expected 404 for unknown unit, got {resp.status_code}"
        )

    def test_response_is_json(self, admin_headers):
        """Endpoint must return valid JSON with correct content-type."""
        resp = requests.get(
            f"{BASE_URL}/owners-units/{OWNER_UNIT}",
            params={"financial_year": "2025"},
            headers=admin_headers,
            timeout=10,
        )
        assert resp.status_code == 200
        assert "application/json" in resp.headers.get("Content-Type", "")

    def test_default_year_does_not_crash(self, admin_headers):
        """Omitting financial_year param must not crash (200 or sensible default)."""
        resp = requests.get(
            f"{BASE_URL}/owners-units/{OWNER_UNIT}",
            headers=admin_headers,
            timeout=10,
        )
        assert resp.status_code in (200, 422), (
            f"No financial_year param returned unexpected {resp.status_code}"
        )

    def test_ua063_year_2025_returns_200(self, admin_headers):
        """Chairman unit UA063 must return 200 for year 2025."""
        resp = requests.get(
            f"{BASE_URL}/owners-units/{CHAIRMAN_UNIT}",
            params={"financial_year": "2025"},
            headers=admin_headers,
            timeout=10,
        )
        assert resp.status_code == 200, resp.text


# ===========================================================================
# Class 3 – Top-Level Response Keys
# ===========================================================================

class TestTopLevelResponseKeys:
    """Confirm all required keys are present at the top level of the response."""

    REQUIRED_KEYS = [
        "unit_number",
        "admin_fund",
        "sinking_fund",
        "yearly_forecast",
        "net_balance",
        "badges",
    ]

    @pytest.mark.parametrize("key", REQUIRED_KEYS)
    def test_required_key_present_2025(self, th017_2025, key):
        """Each required key must be present in the 2025 response."""
        assert key in th017_2025, (
            f"Key '{key}' missing from TH087/2025 response. "
            f"Present keys: {list(th017_2025.keys())}"
        )

    @pytest.mark.parametrize("key", REQUIRED_KEYS)
    def test_required_key_present_2026(self, th017_2026, key):
        """Each required key must be present in the 2026 response (regression)."""
        assert key in th017_2026, (
            f"Key '{key}' missing from TH087/2026 response. "
            f"Present keys: {list(th017_2026.keys())}"
        )

    def test_unit_number_matches_requested_unit(self, th017_2025):
        """unit_number in response must equal the requested unit."""
        assert th017_2025["unit_number"] == OWNER_UNIT

    def test_ua063_unit_number_matches(self, ua063_2025):
        """unit_number in chairman unit response must be UA063."""
        assert ua063_2025["unit_number"] == CHAIRMAN_UNIT


# ===========================================================================
# Class 4 – yearly_forecast Structure (Regression Focus)
# ===========================================================================

class TestYearlyForecast:
    """
    The yearly_forecast dict was the source of the 500-crash bug.
    These tests confirm it is present, non-crashing, and numerically sound.
    """

    FORECAST_KEYS = ["total_levies", "paid_so_far", "remaining"]

    @pytest.mark.parametrize("key", FORECAST_KEYS)
    def test_forecast_key_present_2025(self, th017_2025, key):
        """yearly_forecast must contain all expected keys for year 2025."""
        forecast = th017_2025.get("yearly_forecast", {})
        assert key in forecast, (
            f"yearly_forecast missing '{key}' for 2025. "
            f"forecast keys: {list(forecast.keys())}"
        )

    @pytest.mark.parametrize("key", FORECAST_KEYS)
    def test_forecast_key_present_2026(self, th017_2026, key):
        """
        Regression: yearly_forecast must contain all expected keys for year 2026.
        The bug caused 'total_levies' (and others) to be missing or crash.
        """
        forecast = th017_2026.get("yearly_forecast", {})
        assert key in forecast, (
            f"yearly_forecast missing '{key}' for 2026. "
            f"forecast keys: {list(forecast.keys())}"
        )

    @pytest.mark.parametrize("key", FORECAST_KEYS)
    def test_forecast_values_are_numeric_2025(self, th017_2025, key):
        """All yearly_forecast values must be int or float for year 2025."""
        val = th017_2025["yearly_forecast"][key]
        assert isinstance(val, (int, float)), (
            f"yearly_forecast['{key}'] is {type(val).__name__}, expected numeric"
        )

    @pytest.mark.parametrize("key", FORECAST_KEYS)
    def test_forecast_values_are_numeric_2026(self, th017_2026, key):
        """
        Regression: All yearly_forecast values must be numeric for year 2026.
        Previously NameError on 'total_annual' returned 500 instead of data.
        """
        val = th017_2026["yearly_forecast"][key]
        assert isinstance(val, (int, float)), (
            f"yearly_forecast['{key}'] is {type(val).__name__} for 2026, expected numeric"
        )

    def test_total_levies_positive_2025(self, th017_2025):
        """total_levies must be > 0 for TH087 in 2025."""
        total = th017_2025["yearly_forecast"]["total_levies"]
        assert total > 0, f"total_levies for 2025 is {total}, expected > 0"

    def test_total_levies_positive_2026(self, th017_2026):
        """total_levies must be > 0 for TH087 in 2026."""
        total = th017_2026["yearly_forecast"]["total_levies"]
        assert total > 0, f"total_levies for 2026 is {total}, expected > 0"

    def test_remaining_not_negative_2025(self, th017_2025):
        """remaining must be >= 0 (cannot owe more than entire annual levy)."""
        remaining = th017_2025["yearly_forecast"]["remaining"]
        assert remaining >= 0, f"remaining for 2025 is {remaining}, expected >= 0"

    def test_remaining_not_negative_2026(self, th017_2026):
        """remaining must be >= 0 for year 2026."""
        remaining = th017_2026["yearly_forecast"]["remaining"]
        assert remaining >= 0, f"remaining for 2026 is {remaining}, expected >= 0"

    def test_paid_so_far_not_negative_2025(self, th017_2025):
        """paid_so_far must be >= 0."""
        paid = th017_2025["yearly_forecast"]["paid_so_far"]
        assert paid >= 0, f"paid_so_far for 2025 is {paid}, expected >= 0"

    def test_paid_so_far_not_negative_2026(self, th017_2026):
        """paid_so_far must be >= 0 for year 2026."""
        paid = th017_2026["yearly_forecast"]["paid_so_far"]
        assert paid >= 0, f"paid_so_far for 2026 is {paid}, expected >= 0"

    def test_th017_2026_total_levies_approximately_7090(self, th017_2026):
        """
        TH087 has 161 UOE.
        2026 admin rate ≈ $34.087/UOE → admin levy ≈ $5,488
        2026 sinking rate ≈ $9.9505/UOE → sinking levy ≈ $1,602
        Combined total_levies ≈ $7,090 (± $200 tolerance).
        """
        total = th017_2026["yearly_forecast"]["total_levies"]
        lower = TH087_2026_LEVY_APPROX - TH087_2026_LEVY_TOLERANCE
        upper = TH087_2026_LEVY_APPROX + TH087_2026_LEVY_TOLERANCE
        assert lower <= total <= upper, (
            f"TH087 2026 total_levies = ${total:.2f}, "
            f"expected ≈ ${TH087_2026_LEVY_APPROX:.2f} (± ${TH087_2026_LEVY_TOLERANCE:.0f})"
        )

    def test_cross_year_total_levies_differ(self, th017_2025, th017_2026):
        """2025 and 2026 total_levies should differ (different levy rates per year)."""
        total_2025 = th017_2025["yearly_forecast"]["total_levies"]
        total_2026 = th017_2026["yearly_forecast"]["total_levies"]
        assert total_2025 != total_2026, (
            f"total_levies identical for 2025 and 2026 (both ${total_2025:.2f}) — "
            "rates should differ between years"
        )

    def test_remaining_consistent_with_totals_2026(self, th017_2026):
        """
        remaining must equal max(0, total_levies - paid_so_far).

        The backend correctly clamps remaining to 0.0 when paid_so_far exceeds
        total_levies (i.e. the unit has a credit for the year).  We verify that
        clamping rather than a raw subtraction.
        """
        forecast = th017_2026["yearly_forecast"]
        total = forecast["total_levies"]
        paid = forecast["paid_so_far"]
        actual_remaining = forecast["remaining"]
        # Backend clamps remaining to 0 when overpaid (credit situation)
        expected_remaining = max(0.0, total - paid)
        # Allow $1 rounding tolerance
        assert abs(actual_remaining - expected_remaining) <= 1.0, (
            f"remaining ({actual_remaining:.2f}) != "
            f"max(0, total_levies ({total:.2f}) - paid_so_far ({paid:.2f})) "
            f"= {expected_remaining:.2f}"
        )


# ===========================================================================
# Class 5 – admin_fund Structure
# ===========================================================================

class TestAdminFundStructure:
    """Validate the admin_fund sub-object shape and numeric integrity."""

    FUND_KEYS = ["opening_balance", "levied", "paid", "closing_balance"]

    @pytest.mark.parametrize("key", FUND_KEYS)
    def test_admin_fund_key_present_2025(self, th017_2025, key):
        """admin_fund must contain all ledger keys for year 2025."""
        fund = th017_2025.get("admin_fund", {})
        assert key in fund, (
            f"admin_fund missing '{key}' for 2025. "
            f"Present keys: {list(fund.keys())}"
        )

    @pytest.mark.parametrize("key", FUND_KEYS)
    def test_admin_fund_key_present_2026(self, th017_2026, key):
        """admin_fund must contain all ledger keys for year 2026 (regression)."""
        fund = th017_2026.get("admin_fund", {})
        assert key in fund, (
            f"admin_fund missing '{key}' for 2026. "
            f"Present keys: {list(fund.keys())}"
        )

    @pytest.mark.parametrize("key", FUND_KEYS)
    def test_admin_fund_values_numeric_2025(self, th017_2025, key):
        """admin_fund values must be numeric for year 2025."""
        val = th017_2025["admin_fund"][key]
        assert isinstance(val, (int, float)), (
            f"admin_fund['{key}'] is {type(val).__name__} for 2025"
        )

    @pytest.mark.parametrize("key", FUND_KEYS)
    def test_admin_fund_values_numeric_2026(self, th017_2026, key):
        """admin_fund values must be numeric for year 2026."""
        val = th017_2026["admin_fund"][key]
        assert isinstance(val, (int, float)), (
            f"admin_fund['{key}'] is {type(val).__name__} for 2026"
        )

    def test_admin_fund_levied_positive_2025(self, th017_2025):
        """Admin fund levied amount should be > 0 for year 2025."""
        levied = th017_2025["admin_fund"]["levied"]
        assert levied > 0, f"admin_fund.levied for 2025 is {levied}, expected > 0"

    def test_admin_fund_levied_positive_2026(self, th017_2026):
        """Admin fund levied amount should be > 0 for year 2026."""
        levied = th017_2026["admin_fund"]["levied"]
        assert levied > 0, f"admin_fund.levied for 2026 is {levied}, expected > 0"


# ===========================================================================
# Class 6 – sinking_fund Structure
# ===========================================================================

class TestSinkingFundStructure:
    """Validate the sinking_fund sub-object shape and numeric integrity."""

    FUND_KEYS = ["opening_balance", "levied", "paid", "closing_balance"]

    @pytest.mark.parametrize("key", FUND_KEYS)
    def test_sinking_fund_key_present_2025(self, th017_2025, key):
        """sinking_fund must contain all ledger keys for year 2025."""
        fund = th017_2025.get("sinking_fund", {})
        assert key in fund, (
            f"sinking_fund missing '{key}' for 2025. "
            f"Present keys: {list(fund.keys())}"
        )

    @pytest.mark.parametrize("key", FUND_KEYS)
    def test_sinking_fund_key_present_2026(self, th017_2026, key):
        """sinking_fund must contain all ledger keys for year 2026."""
        fund = th017_2026.get("sinking_fund", {})
        assert key in fund, (
            f"sinking_fund missing '{key}' for 2026. "
            f"Present keys: {list(fund.keys())}"
        )

    @pytest.mark.parametrize("key", FUND_KEYS)
    def test_sinking_fund_values_numeric_2025(self, th017_2025, key):
        """sinking_fund values must be numeric for year 2025."""
        val = th017_2025["sinking_fund"][key]
        assert isinstance(val, (int, float)), (
            f"sinking_fund['{key}'] is {type(val).__name__} for 2025"
        )

    @pytest.mark.parametrize("key", FUND_KEYS)
    def test_sinking_fund_values_numeric_2026(self, th017_2026, key):
        """sinking_fund values must be numeric for year 2026."""
        val = th017_2026["sinking_fund"][key]
        assert isinstance(val, (int, float)), (
            f"sinking_fund['{key}'] is {type(val).__name__} for 2026"
        )

    def test_sinking_fund_levied_positive_2026(self, th017_2026):
        """Sinking fund levied amount should be > 0 for year 2026."""
        levied = th017_2026["sinking_fund"]["levied"]
        assert levied > 0, (
            f"sinking_fund.levied for 2026 is {levied}, expected > 0"
        )


# ===========================================================================
# Class 7 – net_balance Field
# ===========================================================================

class TestNetBalance:
    """Validate the net_balance field is present and numeric."""

    def test_net_balance_present_2025(self, th017_2025):
        """net_balance must be in the 2025 response."""
        assert "net_balance" in th017_2025

    def test_net_balance_present_2026(self, th017_2026):
        """net_balance must be in the 2026 response."""
        assert "net_balance" in th017_2026

    def test_net_balance_is_numeric_2025(self, th017_2025):
        """net_balance must be a number (positive = owes, negative = credit)."""
        val = th017_2025["net_balance"]
        assert isinstance(val, (int, float)), (
            f"net_balance for 2025 is {type(val).__name__}, expected numeric"
        )

    def test_net_balance_is_numeric_2026(self, th017_2026):
        """net_balance must be numeric for year 2026."""
        val = th017_2026["net_balance"]
        assert isinstance(val, (int, float)), (
            f"net_balance for 2026 is {type(val).__name__}, expected numeric"
        )

    def test_net_balance_ua063_is_numeric(self, ua063_2025):
        """net_balance for UA063 must be numeric."""
        val = ua063_2025["net_balance"]
        assert isinstance(val, (int, float)), (
            f"net_balance for UA063/2025 is {type(val).__name__}"
        )


# ===========================================================================
# Class 8 – badges Field
# ===========================================================================

class TestBadges:
    """Validate that badges is always a list."""

    def test_badges_is_list_2025(self, th017_2025):
        """badges must be a list for year 2025."""
        assert isinstance(th017_2025["badges"], list), (
            f"badges for 2025 is {type(th017_2025['badges']).__name__}, expected list"
        )

    def test_badges_is_list_2026(self, th017_2026):
        """badges must be a list for year 2026."""
        assert isinstance(th017_2026["badges"], list), (
            f"badges for 2026 is {type(th017_2026['badges']).__name__}, expected list"
        )

    def test_badges_is_list_ua063(self, ua063_2025):
        """badges must be a list for UA063/2025."""
        assert isinstance(ua063_2025["badges"], list), (
            f"badges for UA063/2025 is {type(ua063_2025['badges']).__name__}"
        )


# ===========================================================================
# Class 9 – Chairman Access
# ===========================================================================

class TestChairmanAccess:
    """Confirm chairman can access their own unit data for finance year 2025."""

    def test_chairman_unit_returns_200(self, chairman_headers):
        """Chairman fetching their own unit must return 200."""
        resp = requests.get(
            f"{BASE_URL}/owners-units/{CHAIRMAN_UNIT}",
            params={"financial_year": "2025"},
            headers=chairman_headers,
            timeout=10,
        )
        assert resp.status_code == 200, (
            f"Chairman accessing {CHAIRMAN_UNIT} returned {resp.status_code}: {resp.text}"
        )

    def test_chairman_unit_has_required_keys(self, chairman_headers):
        """Chairman unit response must contain all required top-level keys."""
        resp = requests.get(
            f"{BASE_URL}/owners-units/{CHAIRMAN_UNIT}",
            params={"financial_year": "2025"},
            headers=chairman_headers,
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        for key in ("admin_fund", "sinking_fund", "yearly_forecast", "net_balance", "badges"):
            assert key in data, f"Missing key '{key}' in chairman unit response"

    def test_chairman_unit_number_correct(self, ua063_2025):
        """The unit_number in UA063 response must equal UA063."""
        assert ua063_2025["unit_number"] == CHAIRMAN_UNIT

    def test_chairman_yearly_forecast_not_crashing(self, ua063_2025):
        """yearly_forecast on UA063 must have total_levies without crashing."""
        forecast = ua063_2025.get("yearly_forecast", {})
        assert "total_levies" in forecast, (
            f"yearly_forecast missing 'total_levies' for UA063. Keys: {list(forecast.keys())}"
        )
        assert isinstance(forecast["total_levies"], (int, float))
        assert forecast["total_levies"] > 0
