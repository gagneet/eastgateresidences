"""
tests/backend/test_building_fund_overview.py

Tests for GET /finance/building-overview — the building-wide fund health
endpoint introduced in the fix that changed the dashboard notice bar from
showing individual unit numbers to building-wide collection rates.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_building_fund_overview.py -v
"""

import os

import pytest
import requests

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_INTEGRATION_TESTS"),
    reason="Live backend test — set RUN_INTEGRATION_TESTS=1 to run",
)

_BASE_URL = "http://127.0.0.1:8003/api"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_overview(token: str, year: str = None) -> requests.Response:
    url = f"{_BASE_URL}/finance/building-overview"
    params = {}
    if year:
        params["year"] = year
    return requests.get(
        url,
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestBuildingFundOverview:
    """Integration tests for /finance/building-overview."""

    def test_admin_can_access_overview(self, admin_token):
        """Admin user can retrieve building-wide fund overview."""
        if not admin_token:
            pytest.skip("Backend not available")
        resp = _get_overview(admin_token)
        assert resp.status_code == 200, f"Unexpected status: {resp.status_code} — {resp.text}"

    def test_response_schema(self, admin_token):
        """Response contains all required top-level fields."""
        if not admin_token:
            pytest.skip("Backend not available")
        resp = _get_overview(admin_token)
        assert resp.status_code == 200
        data = resp.json()
        assert "year" in data, "Missing 'year' field"
        assert "admin_fund" in data, "Missing 'admin_fund' field"
        assert "sinking_fund" in data, "Missing 'sinking_fund' field"
        assert "total_levied" in data, "Missing 'total_levied' field"
        assert "total_paid" in data, "Missing 'total_paid' field"
        assert "levies_paid_pct" in data, "Missing 'levies_paid_pct' field"
        assert "fund_health" in data, "Missing 'fund_health' field"

    def test_fund_sub_schema(self, admin_token):
        """admin_fund and sinking_fund contain total_levied, total_paid, collection_rate."""
        if not admin_token:
            pytest.skip("Backend not available")
        data = _get_overview(admin_token).json()
        for fund_key in ("admin_fund", "sinking_fund"):
            fund = data[fund_key]
            assert "total_levied" in fund, f"{fund_key} missing total_levied"
            assert "total_paid" in fund, f"{fund_key} missing total_paid"
            assert "collection_rate" in fund, f"{fund_key} missing collection_rate"

    def test_percentages_within_valid_range(self, admin_token):
        """All percentage fields should be in the range [0, 100]."""
        if not admin_token:
            pytest.skip("Backend not available")
        data = _get_overview(admin_token).json()
        for pct_field in ("fund_health", "levies_paid_pct"):
            val = data[pct_field]
            assert 0 <= val <= 100, f"{pct_field}={val} is outside [0, 100]"
        for fund_key in ("admin_fund", "sinking_fund"):
            rate = data[fund_key]["collection_rate"]
            assert 0 <= rate <= 100, f"{fund_key}.collection_rate={rate} is outside [0, 100]"

    def test_year_parameter_accepted(self, admin_token):
        """Endpoint honours the ?year= query parameter."""
        if not admin_token:
            pytest.skip("Backend not available")
        resp = _get_overview(admin_token, year="2026")
        assert resp.status_code == 200
        data = resp.json()
        assert data["year"] == "2026", f"Expected year '2026', got '{data['year']}'"

    def test_unknown_year_returns_zeroes_not_error(self, admin_token):
        """Requesting a year with no data returns zeroes, not an HTTP error."""
        if not admin_token:
            pytest.skip("Backend not available")
        resp = _get_overview(admin_token, year="1900")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_levied"] == 0.0
        assert data["total_paid"] == 0.0
        assert data["fund_health"] == 0.0

    def test_unauthenticated_returns_401(self):
        """Unauthenticated request must be rejected with 401."""
        resp = requests.get(f"{_BASE_URL}/finance/building-overview", timeout=10)
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"

    def test_building_totals_exceed_single_unit(self, admin_token):
        """
        Building-wide total_levied should generally exceed any single unit's levy,
        confirming the endpoint is aggregating across units rather than returning
        one unit's data.  East Gate has 87 units so even a modest levy per unit
        would sum to a large building total.
        """
        if not admin_token:
            pytest.skip("Backend not available")
        data = _get_overview(admin_token).json()
        # With 87 units each levying at least a few hundred dollars per year,
        # total_levied should comfortably exceed $10,000.
        if data["total_levied"] > 0:
            assert data["total_levied"] > 10_000, (
                f"total_levied={data['total_levied']} looks like per-unit data, not building aggregate"
            )

    def test_fund_health_is_average_of_fund_rates(self, admin_token):
        """fund_health = total_paid / (total_levied + opening_arrears) × 100 (within rounding)."""
        if not admin_token:
            pytest.skip("Backend not available")
        data = _get_overview(admin_token).json()
        total_paid = data["total_paid"]
        total_obligations = data.get("total_obligations", 0)
        # fund_health is defined as total_paid / total_obligations * 100
        # When opening_arrears > 0 the denominator exceeds total_levied, so
        # fund_health < (admin_rate + sinking_rate) / 2.  Test the actual formula.
        if total_obligations > 0:
            expected = round(total_paid / total_obligations * 100, 1)
        else:
            expected = 0.0
        actual = data["fund_health"]
        assert abs(actual - expected) <= 0.2, (
            f"fund_health={actual} differs from total_paid/total_obligations×100={expected}"
        )
