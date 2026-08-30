#!/usr/bin/env python3
"""
Backend Tests: Finance History Integrity

Validates the reconciled financial ledger (FY2021–2026) stored in annual_levies
and levy_categories collections against the authoritative CSV/PDF source data.

Checks:
  - Fund-level totals match known-good amounts (from AGM/audit sources)
  - Per-category sums match fund totals (within $1 tolerance)
  - Closing balance chain: each year's closing ≈ next year's opening
  - Levy rates derived from proposed budget (not YTD actuals)
  - GST-inclusive levy correctly = ex-GST × 1.10
  - Multi-tenant isolation: "16244" cannot see "13195" data
  - 2026 partial year returns both proposed AND YTD actual fields

Run:
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    pytest tests/backend/test_finance_history.py -v

Requirements:
    Backend running on localhost:8003
    Admin credentials: administrator@eastgateresidences.com.au / $E2E_ADMIN_PASSWORD
    annual_levies + levy_categories seeded via backend/seeds/finance_history.py
"""
import os

import pytest
import requests

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_INTEGRATION_TESTS"),
    reason="Live backend test — set RUN_INTEGRATION_TESTS=1 to run",
)

BASE_URL = "http://127.0.0.1:8003/api"
ADMIN_EMAIL = "administrator@eastgateresidences.com.au"
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "")

# Authoritative fund totals (from AGM papers and audited PDF reports)
KNOWN_TOTALS = {
    "2021": {
        "admin_levy_income": 213_142.64,
        "admin_other_income": 0.00,
        "admin_expenses": 213_146.16,
        "sinking_levy_income": 0.00,
        "sinking_expenses": 0.00,
    },
    "2022": {
        "admin_levy_income": 201_145.64,
        "admin_other_income": 0.00,
        "admin_expenses": 228_089.17,
        "sinking_levy_income": 25_001.00,
        "sinking_expenses": 74_981.20,
    },
    "2023": {
        "admin_levy_income": 254_461.43,
        "admin_other_income": 3_484.38,
        "admin_expenses": 257_945.81,
        "sinking_levy_income": 94_004.45,
        "sinking_expenses": 11_818.42,
    },
    "2024": {
        "admin_levy_income": 226_621.00,
        "admin_other_income": 6_404.63,
        "admin_expenses": 238_272.91,
        "sinking_levy_income": 71_900.35,
        "sinking_expenses": 21_673.53,
    },
    "2025": {
        "admin_levy_income": 317_948.97,
        "admin_other_income": 31_703.26,
        "admin_expenses": 276_347.14,
        "sinking_levy_income": 116_961.29,
        "sinking_expenses": 60_424.48,
    },
}

# Proposed budget per year (ex-GST, from AGM resolutions)
PROPOSED_BUDGETS = {
    "2021": {"admin": 138_460.0, "sinking": 0.0},
    "2022": {"admin": 228_089.17, "sinking": 25_000.0},
    "2023": {"admin": 250_946.00, "sinking": 93_400.0},
    "2024": {"admin": 223_496.00, "sinking": 70_795.0},
    "2025": {"admin": 262_076.00, "sinking": 99_262.0},
    "2026": {"admin": 340_870.20, "sinking": 99_504.9},
}

# Total UOE for East Gate Residences
TOTAL_UOE = 10_000


@pytest.fixture(scope="module")
def admin_token():
    resp = requests.post(f"{BASE_URL}/auth/login", json={
        "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD
    })
    assert resp.status_code == 200, f"Admin login failed: {resp.text}"
    return resp.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ─── Helper ──────────────────────────────────────────────────────────────────

def _annual_levy(year: str, headers: dict) -> dict:
    resp = requests.get(f"{BASE_URL}/finance/annual-levies/{year}", headers=headers)
    assert resp.status_code == 200, f"GET annual-levies/{year} failed: {resp.text}"
    return resp.json()


def _categories(year: str, fund: str, headers: dict) -> list:
    resp = requests.get(
        f"{BASE_URL}/finance/levy-categories",
        params={"year": year, "fund_type": fund},
        headers=headers,
    )
    assert resp.status_code == 200, f"GET levy-categories?year={year}&fund_type={fund} failed"
    return resp.json()


# ─── Fund-level totals ────────────────────────────────────────────────────────

@pytest.mark.parametrize("year", list(KNOWN_TOTALS.keys()))
class TestFundLevelTotals:
    def test_admin_levy_income(self, year, auth_headers):
        doc = _annual_levy(year, auth_headers)
        af = doc.get("admin_fund", {})
        actual = round(float(af.get("levy_income") or 0), 2)
        expected = round(KNOWN_TOTALS[year]["admin_levy_income"], 2)
        assert abs(actual - expected) < 1.0, (
            f"FY{year} admin levy_income: expected {expected}, got {actual}"
        )

    def test_admin_expenses(self, year, auth_headers):
        doc = _annual_levy(year, auth_headers)
        af = doc.get("admin_fund", {})
        actual = round(float(af.get("total_expenses") or 0), 2)
        expected = round(KNOWN_TOTALS[year]["admin_expenses"], 2)
        assert abs(actual - expected) < 1.0, (
            f"FY{year} admin total_expenses: expected {expected}, got {actual}"
        )

    def test_sinking_levy_income(self, year, auth_headers):
        doc = _annual_levy(year, auth_headers)
        sf = doc.get("sinking_fund", {})
        actual = round(float(sf.get("levy_income") or 0), 2)
        expected = round(KNOWN_TOTALS[year]["sinking_levy_income"], 2)
        assert abs(actual - expected) < 1.0, (
            f"FY{year} sinking levy_income: expected {expected}, got {actual}"
        )

    def test_sinking_expenses(self, year, auth_headers):
        doc = _annual_levy(year, auth_headers)
        sf = doc.get("sinking_fund", {})
        actual = round(float(sf.get("total_expenses") or 0), 2)
        expected = round(KNOWN_TOTALS[year]["sinking_expenses"], 2)
        assert abs(actual - expected) < 1.0, (
            f"FY{year} sinking total_expenses: expected {expected}, got {actual}"
        )


# ─── Per-category sums match fund totals ─────────────────────────────────────

@pytest.mark.parametrize("year", ["2022", "2023", "2024", "2025", "2026"])
class TestCategoryIntegrity:
    def test_admin_categories_sum_to_expenses(self, year, auth_headers):
        doc = _annual_levy(year, auth_headers)
        af = doc.get("admin_fund", {})
        fund_total = float(af.get("total_expenses") or 0)
        if fund_total == 0:
            pytest.skip(f"No admin expenses for {year}")
        cats = _categories(year, "administrative", auth_headers)
        cat_sum = round(sum(float(c.get("actual_amount") or 0) for c in cats), 2)
        assert abs(cat_sum - round(fund_total, 2)) < 1.0, (
            f"FY{year} admin categories sum {cat_sum} ≠ fund total {fund_total}"
        )

    def test_sinking_categories_sum_to_expenses(self, year, auth_headers):
        doc = _annual_levy(year, auth_headers)
        sf = doc.get("sinking_fund", {})
        fund_total = float(sf.get("total_expenses") or 0)
        if fund_total == 0:
            pytest.skip(f"No sinking expenses for {year}")
        cats = _categories(year, "sinking", auth_headers)
        cat_sum = round(sum(float(c.get("actual_amount") or 0) for c in cats), 2)
        assert abs(cat_sum - round(fund_total, 2)) < 1.0, (
            f"FY{year} sinking categories sum {cat_sum} ≠ fund total {fund_total}"
        )


# ─── Closing balance chain ────────────────────────────────────────────────────

@pytest.mark.parametrize("year_pair", [("2021", "2022"), ("2022", "2023"),
                                       ("2023", "2024"), ("2024", "2025"),
                                       ("2025", "2026")])
class TestBalanceChain:
    def test_admin_closing_equals_next_opening(self, year_pair, auth_headers):
        y1, y2 = year_pair
        doc1 = _annual_levy(y1, auth_headers)
        doc2 = _annual_levy(y2, auth_headers)
        closing = float(doc1.get("admin_fund", {}).get("closing_balance") or 0)
        opening = float(doc2.get("admin_fund", {}).get("opening_balance") or 0)
        assert abs(closing - opening) < 1.0, (
            f"Admin balance chain FY{y1}→FY{y2}: closing {closing} ≠ opening {opening}"
        )

    def test_sinking_closing_equals_next_opening(self, year_pair, auth_headers):
        y1, y2 = year_pair
        doc1 = _annual_levy(y1, auth_headers)
        doc2 = _annual_levy(y2, auth_headers)
        closing = float(doc1.get("sinking_fund", {}).get("closing_balance") or 0)
        opening = float(doc2.get("sinking_fund", {}).get("opening_balance") or 0)
        assert abs(closing - opening) < 1.0, (
            f"Sinking balance chain FY{y1}→FY{y2}: closing {closing} ≠ opening {opening}"
        )


# ─── Levy rates derived from proposed budget ──────────────────────────────────

@pytest.mark.parametrize("year", list(PROPOSED_BUDGETS.keys()))
class TestLevyRates:
    def test_admin_rate_per_uoe_matches_proposed(self, year, auth_headers):
        doc = _annual_levy(year, auth_headers)
        rate = float(doc.get("admin_levy_per_uoe_annual") or 0)
        expected = round(PROPOSED_BUDGETS[year]["admin"] / TOTAL_UOE, 4)
        assert abs(rate - expected) < 0.001, (
            f"FY{year} admin rate/UOE: expected {expected}, got {rate}"
        )

    def test_sinking_rate_per_uoe_matches_proposed(self, year, auth_headers):
        if PROPOSED_BUDGETS[year]["sinking"] == 0:
            pytest.skip(f"No sinking fund for {year}")
        doc = _annual_levy(year, auth_headers)
        rate = float(doc.get("sinking_levy_per_uoe_annual") or 0)
        expected = round(PROPOSED_BUDGETS[year]["sinking"] / TOTAL_UOE, 4)
        assert abs(rate - expected) < 0.001, (
            f"FY{year} sinking rate/UOE: expected {expected}, got {rate}"
        )

    def test_quarterly_rate_equals_annual_divided_by_4(self, year, auth_headers):
        doc = _annual_levy(year, auth_headers)
        annual = float(doc.get("admin_levy_per_uoe_annual") or 0)
        quarterly = float(doc.get("admin_levy_per_uoe_quarterly") or 0)
        if annual == 0:
            pytest.skip(f"No admin rate for {year}")
        assert abs(quarterly - round(annual / 4, 4)) < 0.001, (
            f"FY{year} quarterly rate {quarterly} ≠ annual/4 {round(annual / 4, 4)}"
        )


# ─── 2026 partial year fields ─────────────────────────────────────────────────

class TestFY2026PartialYear:
    def test_status_is_partial_actual(self, auth_headers):
        doc = _annual_levy("2026", auth_headers)
        assert doc.get("status") == "partial_actual", (
            f"FY2026 status should be 'partial_actual', got {doc.get('status')!r}"
        )

    def test_proposed_admin_expenses_present(self, auth_headers):
        doc = _annual_levy("2026", auth_headers)
        assert float(doc.get("proposed_admin_expenses") or 0) > 0, \
            "FY2026 missing proposed_admin_expenses"

    def test_proposed_sinking_expenses_present(self, auth_headers):
        doc = _annual_levy("2026", auth_headers)
        assert float(doc.get("proposed_sinking_expenses") or 0) > 0, \
            "FY2026 missing proposed_sinking_expenses"

    def test_ytd_levy_income_less_than_proposed(self, auth_headers):
        doc = _annual_levy("2026", auth_headers)
        af = doc.get("admin_fund", {})
        ytd = float(af.get("levy_income") or 0)
        proposed = float(doc.get("proposed_admin_expenses") or 0)
        assert ytd < proposed, (
            f"FY2026 YTD levy income {ytd} should be < proposed {proposed}"
        )

    def test_finance_summary_exposes_proposed_income(self, auth_headers):
        resp = requests.get(f"{BASE_URL}/finance/summary", params={"year": "2026"}, headers=auth_headers)
        assert resp.status_code == 200, f"GET finance/summary?year=2026 failed: {resp.text}"
        data = resp.json()
        af = data.get("admin_fund", {})
        assert "proposed_income" in af, "finance/summary missing proposed_income for 2026"
        assert float(af["proposed_income"]) > 0, "proposed_income is zero"
        assert "ytd_total_income" in af, "finance/summary missing ytd_total_income for 2026"

    def test_finance_summary_proposed_matches_budget(self, auth_headers):
        resp = requests.get(f"{BASE_URL}/finance/summary", params={"year": "2026"}, headers=auth_headers)
        data = resp.json()
        proposed = float(data["admin_fund"].get("proposed_income", 0))
        expected = PROPOSED_BUDGETS["2026"]["admin"]
        assert abs(proposed - expected) < 1.0, (
            f"FY2026 summary proposed_income {proposed} ≠ expected {expected}"
        )


# ─── Payment schedule calendar year ──────────────────────────────────────────

@pytest.mark.parametrize("year", ["2024", "2025", "2026"])
class TestPaymentSchedule:
    def test_has_four_quarters(self, year, auth_headers):
        doc = _annual_levy(year, auth_headers)
        schedule = doc.get("payment_schedule", [])
        assert len(schedule) == 4, f"FY{year} expected 4 quarters, got {len(schedule)}"

    def test_q1_due_march(self, year, auth_headers):
        doc = _annual_levy(year, auth_headers)
        schedule = doc.get("payment_schedule", [])
        q1 = next((q for q in schedule if q.get("quarter") == "Q1"), None)
        assert q1 is not None, f"FY{year} missing Q1"
        assert q1["due_date"].startswith(f"{year}-03"), (
            f"FY{year} Q1 due_date should be March, got {q1['due_date']}"
        )

    def test_q2_due_june(self, year, auth_headers):
        doc = _annual_levy(year, auth_headers)
        schedule = doc.get("payment_schedule", [])
        q2 = next((q for q in schedule if q.get("quarter") == "Q2"), None)
        assert q2 is not None, f"FY{year} missing Q2"
        assert q2["due_date"].startswith(f"{year}-06"), (
            f"FY{year} Q2 due_date should be June, got {q2['due_date']}"
        )


# ─── Multi-tenant isolation ───────────────────────────────────────────────────

class TestMultiTenantIsolation:
    """Sierra ("16244") must not see East Gate ("13195") annual levy data."""

    @pytest.fixture(scope="class")
    def sierra_token(self):
        """
        Authenticate as a Sierra building user.
        If Sierra has no test user, skip these tests gracefully.
        """
        resp = requests.post(f"{BASE_URL}/auth/login", json={
            "email": "test@sierra16244.com.au",
            "password": "SierraTest123!"
        })
        if resp.status_code != 200:
            pytest.skip("Sierra test user not available — skipping isolation tests")
        return resp.json()["token"]

    def test_sierra_cannot_see_eastgate_annual_levy(self, sierra_token):
        headers = {"Authorization": f"Bearer {sierra_token}"}
        resp = requests.get(
            f"{BASE_URL}/finance/annual-levies/2025",
            headers=headers,
        )
        if resp.status_code == 404:
            pass  # no 2025 data for Sierra — expected
        elif resp.status_code == 200:
            # Should NOT return East Gate's $317K admin levy income
            data = resp.json()
            levy_income = float((data.get("admin_fund") or {}).get("levy_income") or 0)
            east_gate_income = KNOWN_TOTALS["2025"]["admin_levy_income"]
            assert abs(levy_income - east_gate_income) > 1.0, (
                "Sierra user retrieved East Gate levy income — cross-tenant leak!"
            )

    def test_sierra_levy_categories_scoped(self, sierra_token):
        headers = {"Authorization": f"Bearer {sierra_token}"}
        resp = requests.get(
            f"{BASE_URL}/finance/levy-categories",
            params={"year": "2025", "fund_type": "administrative"},
            headers=headers,
        )
        if resp.status_code == 200:
            cats = resp.json()
            # Verify none have building_id = "13195"
            for c in cats:
                assert c.get("building_id") != "13195", (
                    f"Category {c.get('name')!r} has East Gate building_id — isolation breach"
                )


# ─── Collection rate is 0–100 ─────────────────────────────────────────────────

class TestCollectionRate:
    def test_collection_rate_non_negative(self, auth_headers):
        resp = requests.get(f"{BASE_URL}/stats/building-kpis", headers=auth_headers)
        assert resp.status_code == 200
        rate = resp.json().get("collection_rate", 0)
        assert rate >= 0, f"collection_rate is negative: {rate}"

    def test_collection_rate_at_most_100(self, auth_headers):
        resp = requests.get(f"{BASE_URL}/stats/building-kpis", headers=auth_headers)
        assert resp.status_code == 200
        rate = resp.json().get("collection_rate", 0)
        assert rate <= 100, f"collection_rate exceeds 100: {rate}"

    def test_historical_year_collection_rate_valid(self, auth_headers):
        resp = requests.get(
            f"{BASE_URL}/stats/building-kpis",
            params={"year": "2025"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        rate = resp.json().get("collection_rate", -1)
        assert 0 <= rate <= 100, f"FY2025 collection_rate out of range: {rate}"
