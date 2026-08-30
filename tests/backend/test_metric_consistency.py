#!/usr/bin/env python3
"""
Cross-Endpoint Metric Consistency Tests
========================================

These tests call MULTIPLE backend endpoints and assert that shared financial
metrics agree across them. A failure here means two endpoints have drifted apart
in their formulas and the user will see inconsistent numbers in the UI.

This file is the enforcement layer for the METRIC[...] comment markers placed
throughout the codebase. If you change a formula in:
  - backend/routers/finance.py (fund_health, levies_paid_pct, total_obligations)
  - backend/server.py (collection_rate, total_obligations)
  - backend/utils/finance_helpers.py (total_opening_arrears aggregation)

...a test here will fail and warn you BEFORE deployment.

Requires: live backend on port 8003 (integration tests — NOT unit tests).
Run with: cd tests && pytest backend/test_metric_consistency.py -v

Multi-tenant isolation: tests run for both 13195 (East Gate) and 16244 (Sierra).
"""

import sys
import os
import datetime

import pytest
import requests

# Allow importing backend config directly so we can mint tokens without
# hitting the login endpoint (which is rate-limited to 10/minute).
_BACKEND = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

BASE_URL = "http://127.0.0.1:8003/api"
RUN = os.getenv("RUN_INTEGRATION_TESTS") == "1"
LEGACY_MULTI_BUILDING = os.getenv("RUN_LEGACY_BUILDING_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not RUN or not LEGACY_MULTI_BUILDING,
    reason="legacy multi-building integration test; set RUN_INTEGRATION_TESTS=1 and RUN_LEGACY_BUILDING_TESTS=1 to enable",
)

ADMIN_USER_ID = "f26f3caf-710f-4a61-84b1-26d8bcac7042"
ADMIN_EMAIL = "administrator@eastgateresidences.com.au"


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def admin_token():
    """Mint a JWT directly — avoids the /auth/login rate-limit (10 req/min)."""
    try:
        from config import JWT_SECRET, JWT_ALGORITHM
    except ImportError:
        pytest.skip("Cannot import backend config — skipping metric consistency tests")
    import jwt as _jwt
    payload = {
        "user_id": ADMIN_USER_ID,
        "email": ADMIN_EMAIL,
        "role": "super_admin",
        "building_id": "13195",
        "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
    }
    return _jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


@pytest.fixture(scope="module")
def headers_13195(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "X-Building-ID": "13195"}


@pytest.fixture(scope="module")
def headers_16244(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "X-Building-ID": "16244"}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_building_overview(headers, year=None):
    """Call /finance/building-overview and return parsed JSON."""
    params = f"?year={year}" if year else ""
    r = requests.get(f"{BASE_URL}/finance/building-overview{params}", headers=headers)
    assert r.status_code == 200, f"building-overview failed {r.status_code}: {r.text}"
    return r.json()


def _get_building_kpis(headers, year=None):
    """Call /stats/building-kpis and return parsed JSON."""
    params = f"?financial_year={year}" if year else ""
    r = requests.get(f"{BASE_URL}/stats/building-kpis{params}", headers=headers)
    assert r.status_code == 200, f"building-kpis failed {r.status_code}: {r.text}"
    return r.json()


# ─── METRIC[fund_health] vs METRIC[collection_rate] ──────────────────────────

class TestFundHealthVsCollectionRate:
    """
    fund_health (/finance/building-overview) and collection_rate (/stats/building-kpis)
    must agree within 1.0 percentage point for the current year.

    Both now use the net_balance-derived formula (updated 2026-04-17):
      numerator  = total_obligations - total_outstanding   (sum net_balance > 0)
      denominator = total_levied + total_opening_arrears   (full annual + carry-forward)

    Previously both used total_paid / total_obligations, but total_paid is stale for
    bridge-synced units (scraper bridge updates net_balance, not total_paid).

    The 1.0 tolerance accounts for:
    - Rounding: fund_health rounds to 1dp, collection_rate to 2dp → max 0.05 diff
    - Data source: total_opening_arrears computed via different aggregation paths
      (finance.py: $opening_arrears field; finance_helpers.py: $admin_opening + $sinking_opening)

    Historical years are excluded: collection_rate uses a different formula
    (next year's opening arrears as closing arrears) while fund_health always
    uses the same formula — so they legitimately diverge for past years.
    """

    TOLERANCE = 1.0  # percentage points — catches formula drift, allows rounding noise

    def test_east_gate_current_year(self, headers_13195):
        """East Gate (13195): fund_health and collection_rate agree within 1 pp."""
        overview = _get_building_overview(headers_13195)
        kpis = _get_building_kpis(headers_13195)

        fund_health = overview["fund_health"]
        collection_rate = kpis["collection_rate"]
        diff = abs(fund_health - collection_rate)

        assert diff <= self.TOLERANCE, (
            f"METRIC DRIFT DETECTED — East Gate 13195:\n"
            f"  fund_health     = {fund_health}%  (from /finance/building-overview)\n"
            f"  collection_rate = {collection_rate}%  (from /stats/building-kpis)\n"
            f"  difference      = {diff:.2f} pp  (tolerance: {self.TOLERANCE} pp)\n"
            f"\n"
            f"Likely cause: formula or denominator changed in one endpoint but not the other.\n"
            f"Check: routers/finance.py (fund_health), server.py (collection_rate),\n"
            f"       utils/finance_helpers.py (total_opening_arrears aggregation)."
        )

    def test_sierra_current_year(self, headers_16244):
        """Sierra (16244): fund_health and collection_rate agree within 1 pp."""
        overview = _get_building_overview(headers_16244)
        kpis = _get_building_kpis(headers_16244)

        fund_health = overview["fund_health"]
        collection_rate = kpis["collection_rate"]
        diff = abs(fund_health - collection_rate)

        assert diff <= self.TOLERANCE, (
            f"METRIC DRIFT DETECTED — Sierra 16244:\n"
            f"  fund_health     = {fund_health}%\n"
            f"  collection_rate = {collection_rate}%\n"
            f"  difference      = {diff:.2f} pp  (tolerance: {self.TOLERANCE} pp)"
        )

    def test_explicit_year_2026(self, headers_13195):
        """Explicit year=2026: both endpoints agree for the same year."""
        overview = _get_building_overview(headers_13195, year="2026")
        kpis = _get_building_kpis(headers_13195, year="2026")

        fund_health = overview["fund_health"]
        collection_rate = kpis["collection_rate"]
        diff = abs(fund_health - collection_rate)

        assert diff <= self.TOLERANCE, (
            f"year=2026: fund_health={fund_health}% vs collection_rate={collection_rate}% "
            f"(diff={diff:.2f} pp, tolerance={self.TOLERANCE} pp)"
        )


# ─── METRIC[total_opening_arrears] consistency ───────────────────────────────

class TestOpeningArrearsConsistency:
    """
    total_opening_arrears is computed via two different MongoDB aggregations:
    - /finance/building-overview: {"$sum": "$opening_arrears"}
    - /stats/building-kpis (via finance_helpers): $admin_opening + $sinking_opening where > 0.01

    For well-formed data these must agree. A large discrepancy indicates the
    opening_arrears field is not being kept in sync with admin_opening/sinking_opening.
    """

    TOLERANCE_DOLLARS = 10.0  # $10 tolerance for floating-point and field-sync differences

    def test_east_gate_opening_arrears_consistent(self, headers_13195):
        """Both endpoints return similar total_opening_arrears for East Gate."""
        overview = _get_building_overview(headers_13195)
        kpis = _get_building_kpis(headers_13195)

        arrears_overview = overview.get("total_opening_arrears", 0)
        arrears_kpis = kpis.get("total_opening_arrears", 0)
        diff = abs(arrears_overview - arrears_kpis)

        assert diff <= self.TOLERANCE_DOLLARS, (
            f"OPENING ARREARS MISMATCH — East Gate 13195:\n"
            f"  /finance/building-overview: ${arrears_overview:,.2f}\n"
            f"  /stats/building-kpis:       ${arrears_kpis:,.2f}\n"
            f"  difference: ${diff:,.2f}  (tolerance: ${self.TOLERANCE_DOLLARS:.2f})\n"
            f"\n"
            f"Cause: unit_levy_ledger.$opening_arrears field out of sync with "
            f"$admin_opening + $sinking_opening. Re-run the levy ledger seed script."
        )

    def test_total_obligations_derivable(self, headers_13195):
        """
        fund_health must equal (total_obligations - total_outstanding) / total_obligations.
        Both total_obligations and total_outstanding are returned in the response.
        total_outstanding = sum(net_balance > 0) — always current (net_balance updated by bridge).
        """
        overview = _get_building_overview(headers_13195)
        fund_health = overview["fund_health"]
        total_obligations = overview.get("total_obligations")
        total_outstanding = overview.get("total_outstanding")

        assert total_obligations is not None, (
            "total_obligations must be present in /finance/building-overview response"
        )
        assert total_outstanding is not None, (
            "total_outstanding must be present in /finance/building-overview response"
        )
        # Recompute fund_health from net_balance-derived numerator — must match returned fund_health
        if total_obligations > 0:
            net_collected = max(0.0, total_obligations - total_outstanding)
            expected_fh = round((net_collected / total_obligations) * 100, 1)
        else:
            expected_fh = 0.0

        assert fund_health == expected_fh, (
            f"fund_health formula invariant broken:\n"
            f"  total_obligations={total_obligations}, total_outstanding={total_outstanding}\n"
            f"  net_collected={max(0.0, total_obligations - total_outstanding):.2f}\n"
            f"  expected_fund_health={expected_fh}%  actual={fund_health}%\n"
            f"  Formula: (total_obligations - total_outstanding) / total_obligations"
        )


# ─── METRIC[levies_paid_pct] vs METRIC[fund_health] relationship ─────────────

class TestLeviesPaidPctRelationship:
    """
    levies_paid_pct = (total_levied - total_outstanding) / total_levied  (trust-accounting-correct)
    fund_health     = (total_obligations - total_outstanding) / total_obligations
                      (net_balance-sourced; arrears in denominator; always current)

    Trust accounting policy (2026-04-24): credit overpayments are pre-payments for the next
    quarter and must not be counted as current-period income. levies_paid_pct therefore uses
    net_balance-derived outstanding so credits count as "period complete" and the rate never
    exceeds 100%.  fund_health uses the full obligations denominator (levied + opening arrears)
    so it can legitimately differ from levies_paid_pct in either direction.

    levies_paid_pct is an informational breakdown metric shown in detail panels only.
    fund_health is the primary health signal on the dashboard.
    """

    def test_fund_health_non_negative(self, headers_13195):
        """fund_health must be in [0, 100] range."""
        overview = _get_building_overview(headers_13195)
        fh = overview["fund_health"]
        assert 0 <= fh <= 100, f"fund_health out of range: {fh}%"

    def test_levies_paid_pct_non_negative(self, headers_13195):
        """levies_paid_pct must be in [0, 100] range."""
        overview = _get_building_overview(headers_13195)
        lpp = overview["levies_paid_pct"]
        assert 0 <= lpp <= 100, f"levies_paid_pct out of range: {lpp}%"

    def test_levies_paid_pct_formula_invariant(self, headers_13195):
        """levies_paid_pct = (total_levied - total_outstanding) / total_levied (trust-accounting)."""
        overview = _get_building_overview(headers_13195)
        total_levied = overview["total_levied"]
        total_outstanding = overview["total_outstanding"]
        lpp_returned = overview["levies_paid_pct"]

        if total_levied > 0:
            lpp_computed = round((total_levied - total_outstanding) / total_levied * 100, 1)
            assert lpp_returned == lpp_computed, (
                f"levies_paid_pct formula broken: "
                f"computed={lpp_computed}% returned={lpp_returned}% "
                f"(total_levied={total_levied}, total_outstanding={total_outstanding})"
            )


# ─── Regression guard ─────────────────────────────────────────────────────────

class TestRegressionGuard:
    """
    Explicit regression test for the 2026-04-02 bug:
    fund_health was 64.2% while collection_rate was 63.09% — a 1.11 pp drift.

    Root cause: fund_health used total_paid / total_levied (missing opening_arrears
    in denominator), while collection_rate used total_paid / (total_levied + opening_arrears).

    This test ensures the regression cannot silently reappear.
    """

    def test_no_large_drift(self, headers_13195):
        """
        The original regression produced a 1.11 pp drift.
        Assert drift is well below 1.0 pp to ensure the fix is in place.
        """
        overview = _get_building_overview(headers_13195, year="2026")
        kpis = _get_building_kpis(headers_13195, year="2026")

        fund_health = overview["fund_health"]
        collection_rate = kpis["collection_rate"]
        drift = abs(fund_health - collection_rate)

        # The regression was 1.11 pp. After the fix it should be < 0.5 pp.
        assert drift < 0.5, (
            f"REGRESSION: fund_health vs collection_rate drift is {drift:.2f} pp.\n"
            f"  fund_health={fund_health}%, collection_rate={collection_rate}%\n"
            f"The 2026-04-02 bug (1.11 pp drift) appears to have re-emerged.\n"
            f"Check routers/finance.py — fund_health must use (total_levied + opening_arrears) denominator."
        )

    def test_opening_arrears_in_response(self, headers_13195):
        """Both endpoints must expose total_opening_arrears so the UI can show it."""
        overview = _get_building_overview(headers_13195, year="2026")
        kpis = _get_building_kpis(headers_13195, year="2026")

        assert "total_opening_arrears" in overview, (
            "REGRESSION: /finance/building-overview no longer returns total_opening_arrears"
        )
        assert "total_opening_arrears" in kpis, (
            "REGRESSION: /stats/building-kpis no longer returns total_opening_arrears"
        )
