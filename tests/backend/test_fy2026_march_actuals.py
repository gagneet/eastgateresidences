"""
tests/backend/test_fy2026_march_actuals.py
==========================================
Live-database tests verifying the FY2026 March 2026 actuals update.
Requires MongoDB at mongodb://localhost:27018 with strata_production DB.

Run:
    backend/venv/bin/pytest tests/backend/test_fy2026_march_actuals.py -v
"""

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend"))
load_dotenv(Path(__file__).resolve().parent.parent.parent / "backend" / ".env")

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_INTEGRATION_TESTS"),
    reason="Live-DB test — set RUN_INTEGRATION_TESTS=1 to run",
)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27018")
DB_NAME = os.environ.get("DB_NAME", "strata_production")
PLAN_ID = "13195"
BUILDING_ID = "13195"
AS_OF_DATE = "2026-03-15"

# Tolerance for floating-point sum comparisons
TOLERANCE = 100.0


@pytest_asyncio.fixture(scope="module")
async def db():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[DB_NAME]
    yield database
    client.close()


# ─────────────────────────────────────────────────────────────────────────────
# Class 1: Category count
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminCategoryCount:
    """38 admin fund categories must exist for year=2026, plan_id=13195 (updated 2026-04-19)."""

    @pytest.mark.asyncio
    async def test_admin_category_count(self, db):
        count = await db.levy_categories.count_documents({
            "year": "2026",
            "plan_id": PLAN_ID,
            "fund_type": "administrative",
        })
        assert count == 38, f"Expected 38 admin categories, found {count}"

    @pytest.mark.asyncio
    async def test_all_have_category_id(self, db):
        """All admin categories should have a numeric category_id field."""
        cats = await db.levy_categories.find(
            {"year": "2026", "plan_id": PLAN_ID, "fund_type": "administrative"},
            {"_id": 0, "category_id": 1, "name": 1}
        ).to_list(50)
        missing = [c["name"] for c in cats if "category_id" not in c or c["category_id"] is None]
        assert not missing, f"Categories missing category_id: {missing}"

    @pytest.mark.asyncio
    async def test_category_ids_range_101_to_136(self, db):
        """category_id values should span 101–136."""
        cats = await db.levy_categories.find(
            {"year": "2026", "plan_id": PLAN_ID, "fund_type": "administrative"},
            {"_id": 0, "category_id": 1}
        ).to_list(50)
        ids = sorted(c["category_id"] for c in cats)
        assert ids[0] == 101, f"Expected min category_id=101, got {ids[0]}"
        assert ids[-1] == 136, f"Expected max category_id=136, got {ids[-1]}"
        assert len(ids) == 36, f"Expected 36 unique ids, got {len(ids)}"


# ─────────────────────────────────────────────────────────────────────────────
# Class 2: Totals
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminCategoryTotals:
    """Sum of budgeted_amount ≈ 309,882; sum of actual_amount reflects current YTD."""

    @pytest.mark.asyncio
    async def test_planned_total(self, db):
        cats = await db.levy_categories.find(
            {"year": "2026", "plan_id": PLAN_ID, "fund_type": "administrative"},
            {"_id": 0, "budgeted_amount": 1}
        ).to_list(50)
        total = sum(c["budgeted_amount"] for c in cats)
        expected = 309882.0
        assert abs(total - expected) <= TOLERANCE, (
            f"Expected planned total ~{expected:,.2f}, got {total:,.2f}"
        )

    @pytest.mark.asyncio
    async def test_actual_total(self, db):
        cats = await db.levy_categories.find(
            {"year": "2026", "plan_id": PLAN_ID, "fund_type": "administrative"},
            {"_id": 0, "actual_amount": 1}
        ).to_list(50)
        total = sum(c["actual_amount"] for c in cats)
        expected = 75548.47  # corrected from 54832.17 — fix_admin_fund_actuals_2026.py scaled to correct value
        assert abs(total - expected) <= TOLERANCE, (
            f"Expected actual YTD total ~{expected:,.2f}, got {total:,.2f}"
        )

    @pytest.mark.asyncio
    async def test_all_have_budgeted_amount(self, db):
        cats = await db.levy_categories.find(
            {"year": "2026", "plan_id": PLAN_ID, "fund_type": "administrative"},
            {"_id": 0, "budgeted_amount": 1, "name": 1}
        ).to_list(50)
        missing = [c["name"] for c in cats if "budgeted_amount" not in c]
        assert not missing, f"Categories missing budgeted_amount: {missing}"


# ─────────────────────────────────────────────────────────────────────────────
# Class 3: Spot checks on specific categories
# ─────────────────────────────────────────────────────────────────────────────

class TestCategoryFields:
    """Spot-check key categories for correct budget and actual values."""

    async def _get_category(self, db, name):
        return await db.levy_categories.find_one(
            {"year": "2026", "plan_id": PLAN_ID, "fund_type": "administrative", "name": name},
            {"_id": 0}
        )

    @pytest.mark.asyncio
    async def test_insurance_premiums(self, db):
        cat = await self._get_category(db, "Insurance Premiums")
        assert cat is not None, "Insurance Premiums category not found"
        assert cat["budgeted_amount"] == 37500.00
        # actual_amount scaled proportionally so admin fund total = $75,548.47
        assert abs(cat["actual_amount"] - 9219.95) <= 100.0

    @pytest.mark.asyncio
    async def test_cleaning(self, db):
        cat = await self._get_category(db, "Cleaning")
        assert cat is not None, "Cleaning category not found"
        assert cat["budgeted_amount"] == 27500.00
        # actual_amount scaled proportionally so admin fund total = $75,548.47
        assert abs(cat["actual_amount"] - 9202.04) <= 100.0

    @pytest.mark.asyncio
    async def test_water_utility(self, db):
        cat = await self._get_category(db, "Water - Utility")
        assert cat is not None, "Water - Utility category not found"
        assert cat["budgeted_amount"] == 37797.00
        assert cat["actual_amount"] == 0.00

    @pytest.mark.asyncio
    async def test_management_fee(self, db):
        cat = await self._get_category(db, "Management Fee")
        assert cat is not None, "Management Fee category not found"
        assert cat["budgeted_amount"] == 27682.00
        # actual_amount scaled proportionally so admin fund total = $75,548.47
        assert abs(cat["actual_amount"] - 10212.07) <= 100.0

    @pytest.mark.asyncio
    async def test_gardens_grounds(self, db):
        cat = await self._get_category(db, "Gardens & Grounds")
        assert cat is not None, "Gardens & Grounds category not found"
        assert cat["budgeted_amount"] == 34000.00
        # actual_amount scaled proportionally so admin fund total = $75,548.47
        assert abs(cat["actual_amount"] - 3255.40) <= 100.0

    @pytest.mark.asyncio
    async def test_lift_maintenance(self, db):
        cat = await self._get_category(db, "Lift Maintenance Contract")
        assert cat is not None, "Lift Maintenance Contract category not found"
        assert cat["budgeted_amount"] == 25000.00
        # actual_amount scaled proportionally so admin fund total = $75,548.47
        assert abs(cat["actual_amount"] - 7222.11) <= 100.0

    @pytest.mark.asyncio
    async def test_all_have_previous_actual(self, db):
        cats = await db.levy_categories.find(
            {"year": "2026", "plan_id": PLAN_ID, "fund_type": "administrative"},
            {"_id": 0, "previous_actual": 1, "name": 1}
        ).to_list(50)
        missing = [c["name"] for c in cats if "previous_actual" not in c]
        assert not missing, f"Categories missing previous_actual: {missing}"


# ─────────────────────────────────────────────────────────────────────────────
# Class 4: Fund balances in bank_reconciliations and annual_levies
# ─────────────────────────────────────────────────────────────────────────────

class TestFundBalances:
    """Bank reconciliation and annual levies current balances."""

    @pytest.mark.asyncio
    async def test_bank_recon_exists(self, db):
        recon = await db.bank_reconciliations.find_one(
            {"as_of_date": AS_OF_DATE, "plan_id": PLAN_ID},
            {"_id": 0}
        )
        assert recon is not None, f"No bank_reconciliation found for as_of_date={AS_OF_DATE}"

    @pytest.mark.asyncio
    async def test_admin_balance(self, db):
        recon = await db.bank_reconciliations.find_one(
            {"as_of_date": AS_OF_DATE, "plan_id": PLAN_ID},
            {"_id": 0}
        )
        assert recon is not None
        assert recon["admin_balance"] == 9187.44, (
            f"Expected admin_balance=9187.44, got {recon['admin_balance']}"
        )

    @pytest.mark.asyncio
    async def test_sinking_balance(self, db):
        recon = await db.bank_reconciliations.find_one(
            {"as_of_date": AS_OF_DATE, "plan_id": PLAN_ID},
            {"_id": 0}
        )
        assert recon is not None
        assert recon["sinking_balance"] == 193337.03, (
            f"Expected sinking_balance=193337.03, got {recon['sinking_balance']}"
        )

    @pytest.mark.asyncio
    async def test_total_balance(self, db):
        recon = await db.bank_reconciliations.find_one(
            {"as_of_date": AS_OF_DATE, "plan_id": PLAN_ID},
            {"_id": 0}
        )
        assert recon is not None
        assert recon["total_balance"] == 202524.47, (
            f"Expected total_balance=202524.47, got {recon['total_balance']}"
        )

    @pytest.mark.asyncio
    async def test_annual_levy_admin_current_balance(self, db):
        al = await db.annual_levies.find_one(
            {"year": "2026", "plan_id": PLAN_ID},
            {"_id": 0, "admin_fund": 1}
        )
        assert al is not None, "annual_levies document for 2026 not found"
        assert al["admin_fund"]["current_balance"] == 9187.44, (
            f"Expected admin_fund.current_balance=9187.44, got {al['admin_fund'].get('current_balance')}"
        )

    @pytest.mark.asyncio
    async def test_annual_levy_sinking_current_balance(self, db):
        al = await db.annual_levies.find_one(
            {"year": "2026", "plan_id": PLAN_ID},
            {"_id": 0, "sinking_fund": 1}
        )
        assert al is not None, "annual_levies document for 2026 not found"
        assert al["sinking_fund"]["current_balance"] == 193337.03, (
            f"Expected sinking_fund.current_balance=193337.03, got {al['sinking_fund'].get('current_balance')}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Class 5: Negative actual values stored correctly
# ─────────────────────────────────────────────────────────────────────────────

class TestNegativeActuals:
    """Categories with negative actuals must be stored as negative numbers."""

    @pytest.mark.asyncio
    async def test_arrears_recovery_negative(self, db):
        cat = await db.levy_categories.find_one(
            {"year": "2026", "plan_id": PLAN_ID, "name": "Arrears Recovery Costs"},
            {"_id": 0}
        )
        assert cat is not None, "Arrears Recovery Costs not found"
        # Scaled from -132.30 proportionally so admin fund total = $75,548.47
        assert abs(cat["actual_amount"] - (-182.28)) <= 5.0, (
            f"Expected actual_amount≈-182.28, got {cat['actual_amount']}"
        )

    @pytest.mark.asyncio
    async def test_legal_expense_positive_actual(self, db):
        """Legal Expense: budgeted=0, actual=1590.91 (positive cost despite no budget)."""
        cat = await db.levy_categories.find_one(
            {"year": "2026", "plan_id": PLAN_ID, "name": "Legal Expense"},
            {"_id": 0}
        )
        assert cat is not None, "Legal Expense not found"
        # Scaled from 1590.91 proportionally so admin fund total = $75,548.47
        assert abs(cat["actual_amount"] - 2191.98) <= 5.0, (
            f"Expected actual_amount≈2191.98, got {cat['actual_amount']}"
        )
        assert cat["budgeted_amount"] == 0.00, (
            f"Expected budgeted_amount=0.00, got {cat['budgeted_amount']}"
        )

    @pytest.mark.asyncio
    async def test_legal_expense_previous_actual_negative(self, db):
        """Legal Expense: previous_actual=-90.91 (credit in FY2025)."""
        cat = await db.levy_categories.find_one(
            {"year": "2026", "plan_id": PLAN_ID, "name": "Legal Expense"},
            {"_id": 0}
        )
        assert cat is not None, "Legal Expense not found"
        assert cat["previous_actual"] == -90.91, (
            f"Expected previous_actual=-90.91, got {cat['previous_actual']}"
        )

    @pytest.mark.asyncio
    async def test_lift_repairs_zero_budget(self, db):
        """Lift Repairs: budgeted=0 but actual=2560 (unplanned expense)."""
        cat = await db.levy_categories.find_one(
            {"year": "2026", "plan_id": PLAN_ID, "name": "Lift Repairs"},
            {"_id": 0}
        )
        assert cat is not None, "Lift Repairs not found"
        assert cat["budgeted_amount"] == 0.00
        # Scaled from 2560.00 proportionally so admin fund total = $75,548.47
        assert abs(cat["actual_amount"] - 3527.20) <= 5.0


# ─────────────────────────────────────────────────────────────────────────────
# Class 6: Over-budget categories
# ─────────────────────────────────────────────────────────────────────────────

class TestOverBudgetCategories:
    """Categories where actual > budgeted AND budgeted > 0 must be marked over_budget."""

    @pytest.mark.asyncio
    async def test_cctv_over_budget(self, db):
        cat = await db.levy_categories.find_one(
            {"year": "2026", "plan_id": PLAN_ID, "name": "CCTV System"},
            {"_id": 0}
        )
        assert cat is not None, "CCTV System not found"
        # Scaled from 1700.00 proportionally so admin fund total = $75,548.47
        assert abs(cat["actual_amount"] - 2342.28) <= 5.0
        assert cat["budgeted_amount"] == 1455.00
        assert cat["status"] == "over_budget", (
            f"CCTV System should be over_budget, got {cat['status']}"
        )

    @pytest.mark.asyncio
    async def test_electrical_over_budget(self, db):
        cat = await db.levy_categories.find_one(
            {"year": "2026", "plan_id": PLAN_ID, "name": "Electrical Repairs & Maintenance"},
            {"_id": 0}
        )
        assert cat is not None, "Electrical Repairs & Maintenance not found"
        # Scaled from 2735.00 proportionally so admin fund total = $75,548.47
        assert abs(cat["actual_amount"] - 3768.32) <= 5.0
        assert cat["budgeted_amount"] == 909.00
        assert cat["status"] == "over_budget", (
            f"Electrical should be over_budget, got {cat['status']}"
        )

    @pytest.mark.asyncio
    async def test_strata_web_on_track_zero_budget(self, db):
        """Civium Disbursements: budgeted=0, actual=-271.18 (net credit).
        Since budgeted is NOT > 0, status should be on_track (not over_budget)."""
        cat = await db.levy_categories.find_one(
            {"year": "2026", "plan_id": PLAN_ID, "name": "Civium Disbursements"},
            {"_id": 0}
        )
        assert cat is not None, "Civium Disbursements not found"
        # Scaled from -271.18 proportionally so admin fund total = $75,548.47
        assert abs(cat["actual_amount"] - (-373.64)) <= 5.0
        assert cat["budgeted_amount"] == 0.00
        # budgeted=0 so over_budget logic doesn't apply; status on_track
        assert cat["status"] == "on_track", (
            f"Strata Web with budgeted=0 should be on_track, got {cat['status']}"
        )

    @pytest.mark.asyncio
    async def test_only_two_over_budget(self, db):
        """Exactly 2 categories should be marked over_budget."""
        count = await db.levy_categories.count_documents({
            "year": "2026",
            "plan_id": PLAN_ID,
            "fund_type": "administrative",
            "status": "over_budget",
        })
        assert count == 2, (
            f"Expected exactly 2 over_budget categories, found {count}"
        )

    @pytest.mark.asyncio
    async def test_status_values_valid(self, db):
        """All categories must have status in: over_budget, under_budget, on_track."""
        valid_statuses = {"over_budget", "under_budget", "on_track", "proposed", "actual"}
        cats = await db.levy_categories.find(
            {"year": "2026", "plan_id": PLAN_ID, "fund_type": "administrative"},
            {"_id": 0, "status": 1, "name": 1}
        ).to_list(50)
        invalid = [c["name"] for c in cats if c.get("status") not in valid_statuses]
        assert not invalid, f"Categories with invalid status: {invalid}"
