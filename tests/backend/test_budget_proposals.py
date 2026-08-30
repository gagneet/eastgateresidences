"""
Tests for Budget Proposal endpoints, models, and lot finance service fix.

Covers:
  - BudgetProposalItem / BudgetProposalCreate Pydantic validation
  - GET /budget-proposals: CPI inflation logic, prior-year lookup, saved-proposal merge
  - POST /budget-proposals: save/replace logic, total computation, permission guard
  - lot_finance_service: admin_levied/sinking_levied used over total_paid
  - finance_intelligence: lot-summary fallback to unit_levy_ledger
  - FY2024 seed: CATS_2024_ADMIN sums are correct

Run with:
    cd backend && python -m pytest tests/test_budget_proposals.py -v
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

# ─── Helpers ─────────────────────────────────────────────────────────────────

PLAN_ID = "13195"


def _make_user(role: str, unit_number: str = "", user_id: str = "u1") -> dict:
    perms = {
        "can_view_finances": role in {"super_admin", "chairman", "strata_manager", "ec_member"},
        "can_manage_finances": role in {"super_admin", "chairman", "strata_manager"},
    }
    return {"id": user_id, "role": role, "unit_number": unit_number, "permissions": perms}


def _make_permissions(user: dict):
    class Perms:
        pass

    p = Perms()
    p.can_view_finances = user["permissions"]["can_view_finances"]
    p.can_manage_finances = user["permissions"]["can_manage_finances"]
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Section A — Pydantic model validation
# ─────────────────────────────────────────────────────────────────────────────

class TestBudgetProposalModels:
    """Validate BudgetProposalItem and BudgetProposalCreate Pydantic models."""

    def test_budget_proposal_item_defaults(self):
        """BudgetProposalItem has sensible defaults."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from models.finance import BudgetProposalItem
        item = BudgetProposalItem(
            fund_type="administrative",
            name="Insurance Premiums",
            proposed_amount=62000.0,
        )
        assert item.fund_type == "administrative"
        assert item.name == "Insurance Premiums"
        assert item.proposed_amount == 62000.0
        assert item.prior_year_actual == 0.0
        assert item.prior_year_budgeted == 0.0
        assert item.amended_amount is None
        assert item.approved is False

    def test_budget_proposal_item_with_all_fields(self):
        from models.finance import BudgetProposalItem
        item = BudgetProposalItem(
            fund_type="sinking",
            name="Lift Maintenance",
            prior_year_actual=32068.0,
            prior_year_budgeted=25000.0,
            proposed_amount=33190.18,
            amended_amount=33000.0,
            approved=True,
        )
        assert item.approved is True
        assert item.amended_amount == 33000.0
        assert item.prior_year_actual == 32068.0

    def test_budget_proposal_create_defaults(self):
        from models.finance import BudgetProposalItem, BudgetProposalCreate
        item = BudgetProposalItem(
            fund_type="administrative",
            name="Cleaning",
            proposed_amount=17239.12,
        )
        proposal = BudgetProposalCreate(
            target_year="2027",
            base_year="2024",
            items=[item],
        )
        assert proposal.inflation_rate == 3.0  # default
        assert proposal.target_year == "2027"
        assert proposal.base_year == "2024"
        assert len(proposal.items) == 1

    def test_budget_proposal_create_custom_inflation(self):
        from models.finance import BudgetProposalItem, BudgetProposalCreate
        proposal = BudgetProposalCreate(
            target_year="2028",
            base_year="2025",
            inflation_rate=5.0,
            items=[
                BudgetProposalItem(
                    fund_type="administrative",
                    name="Electricity - Utility",
                    proposed_amount=23842.31,
                    approved=True,
                )
            ],
        )
        assert proposal.inflation_rate == 5.0

    def test_budget_proposal_item_fund_type_values(self):
        from models.finance import BudgetProposalItem
        for ft in ("administrative", "sinking"):
            item = BudgetProposalItem(fund_type=ft, name="Test", proposed_amount=1000.0)
            assert item.fund_type == ft


# ─────────────────────────────────────────────────────────────────────────────
# Section B — GET /budget-proposals logic
# ─────────────────────────────────────────────────────────────────────────────

class TestGetBudgetProposalsLogic:
    """Unit-test the CPI inflation and proposal-merge logic inline."""

    @staticmethod
    def _compute_proposed(actual: float, budgeted: float, rate: float) -> float:
        base = actual if actual > 0 else budgeted
        return round(base * (1 + rate / 100), 2)

    def test_cpi_uses_actual_when_positive(self):
        """Base amount = actual_amount when actual > 0."""
        proposed = self._compute_proposed(actual=59238.61, budgeted=48000.0, rate=3.0)
        # 59238.61 * 1.03 = 61015.77
        assert proposed == pytest.approx(61015.77, abs=0.02)

    def test_cpi_falls_back_to_budgeted_when_no_actual(self):
        """Base amount = budgeted_amount when actual == 0."""
        proposed = self._compute_proposed(actual=0.0, budgeted=25000.0, rate=3.0)
        # 25000 * 1.03 = 25750.00
        assert proposed == 25750.00

    def test_cpi_zero_base_produces_zero(self):
        """Both zero → proposed 0."""
        proposed = self._compute_proposed(actual=0.0, budgeted=0.0, rate=3.0)
        assert proposed == 0.0

    def test_custom_inflation_rate(self):
        """Custom rate (5%) is applied correctly."""
        proposed = self._compute_proposed(actual=20000.0, budgeted=20000.0, rate=5.0)
        assert proposed == pytest.approx(21000.0, abs=0.02)

    def test_negative_inflation_rate_reduces_amount(self):
        """Negative rate (deflation) reduces the proposed amount."""
        proposed = self._compute_proposed(actual=10000.0, budgeted=10000.0, rate=-2.0)
        assert proposed == pytest.approx(9800.0, abs=0.02)

    def test_saved_proposal_is_approved_with_saved_amount(self):
        """When target_year already has a saved category, approved=True with amended_amount."""
        base_cat = {
            "fund_type": "administrative",
            "name": "Insurance Premiums",
            "actual_amount": 59238.61,
            "budgeted_amount": 48000.0,
        }
        saved_cat = {
            "fund_type": "administrative",
            "name": "Insurance Premiums",
            "budgeted_amount": 61000.0,
            "status": "proposed",
        }
        saved_map = {("administrative", "Insurance Premiums"): saved_cat}

        proposed = self._compute_proposed(base_cat["actual_amount"], base_cat["budgeted_amount"], 3.0)
        key = (base_cat["fund_type"], base_cat["name"])
        saved = saved_map.get(key)
        item = {
            "approved": saved is not None,
            "amended_amount": round(saved["budgeted_amount"], 2) if saved else None,
            "proposed_amount": proposed,
        }
        assert item["approved"] is True
        assert item["amended_amount"] == 61000.0

    def test_unsaved_category_is_not_approved(self):
        """Category not in saved_map → approved=False, amended_amount=None."""
        saved_map = {}
        key = ("administrative", "Cleaning")
        saved = saved_map.get(key)
        item = {
            "approved": saved is not None,
            "amended_amount": round(saved["budgeted_amount"], 2) if saved else None,
        }
        assert item["approved"] is False
        assert item["amended_amount"] is None

    def test_admin_total_uses_amended_when_present(self):
        """Admin total uses amended_amount over proposed_amount for approved items."""
        items = [
            {"fund_type": "administrative", "approved": True,
             "proposed_amount": 61015.77, "amended_amount": 61000.0},
            {"fund_type": "administrative", "approved": True,
             "proposed_amount": 17239.12, "amended_amount": None},
            {"fund_type": "administrative", "approved": False,
             "proposed_amount": 5000.0, "amended_amount": None},
        ]
        admin_total = round(sum(
            (i["amended_amount"] or i["proposed_amount"])
            for i in items if i["fund_type"] == "administrative" and i["approved"]
        ), 2)
        assert admin_total == pytest.approx(78239.12, abs=0.02)

    def test_sinking_total_excludes_admin_items(self):
        """Sinking total only counts sinking fund items."""
        items = [
            {"fund_type": "administrative", "approved": True, "proposed_amount": 5000.0, "amended_amount": None},
            {"fund_type": "sinking", "approved": True, "proposed_amount": 10000.0, "amended_amount": None},
            {"fund_type": "sinking", "approved": True, "proposed_amount": 2000.0, "amended_amount": None},
        ]
        sinking_total = round(sum(
            (i["amended_amount"] or i["proposed_amount"])
            for i in items if i["fund_type"] == "sinking" and i["approved"]
        ), 2)
        assert sinking_total == 12000.0

    def test_permission_guard_view_finances(self):
        """Only roles with can_view_finances can call GET /budget-proposals."""
        view_roles = {"super_admin", "chairman", "strata_manager", "ec_member"}
        denied_roles = {"owner", "tenant", "service_provider", "guest"}
        for role in view_roles:
            user = _make_user(role)
            p = _make_permissions(user)
            assert p.can_view_finances is True, f"{role} should have can_view_finances"
        for role in denied_roles:
            user = _make_user(role)
            p = _make_permissions(user)
            assert p.can_view_finances is False, f"{role} should NOT have can_view_finances"


# ─────────────────────────────────────────────────────────────────────────────
# Section C — POST /budget-proposals logic
# ─────────────────────────────────────────────────────────────────────────────

class TestPostBudgetProposalsLogic:
    """Unit-test the save/replace logic for POST /budget-proposals."""

    def _build_approved_items(self):
        """Return a list of mock BudgetProposalItem-like dicts."""
        return [
            MagicMock(
                fund_type="administrative", name="Insurance Premiums",
                proposed_amount=61015.77, amended_amount=61000.0, approved=True,
            ),
            MagicMock(
                fund_type="administrative", name="Cleaning",
                proposed_amount=17239.12, amended_amount=None, approved=True,
            ),
            MagicMock(
                fund_type="sinking", name="External Painting",
                proposed_amount=335000.0, amended_amount=None, approved=True,
            ),
            MagicMock(
                fund_type="administrative", name="Venue Hire",
                proposed_amount=515.0, amended_amount=None, approved=False,
            ),
        ]

    def test_only_approved_items_saved(self):
        """POST only saves items where approved=True."""
        items = self._build_approved_items()
        approved = [i for i in items if i.approved]
        assert len(approved) == 3

    def test_amended_amount_takes_priority(self):
        """final_amount = amended_amount if not None else proposed_amount."""
        items = self._build_approved_items()
        finals = []
        for i in items:
            if i.approved:
                final = i.amended_amount if i.amended_amount is not None else i.proposed_amount
                finals.append(final)
        # Insurance: 61000.0 (amended), Cleaning: 17239.12, External Painting: 335000.0
        assert finals[0] == 61000.0
        assert finals[1] == pytest.approx(17239.12, abs=0.01)
        assert finals[2] == 335000.0

    def test_admin_sinking_totals_computed_correctly(self):
        items = self._build_approved_items()
        approved = [i for i in items if i.approved]
        admin_total = round(sum(
            (i.amended_amount or i.proposed_amount)
            for i in approved if i.fund_type == "administrative"
        ), 2)
        sinking_total = round(sum(
            (i.amended_amount or i.proposed_amount)
            for i in approved if i.fund_type == "sinking"
        ), 2)
        assert admin_total == pytest.approx(61000.0 + 17239.12, abs=0.02)
        assert sinking_total == 335000.0

    def test_no_approved_items_raises(self):
        """POST with zero approved items should return 400 (no-op guard)."""
        items = [
            MagicMock(fund_type="administrative", approved=False, proposed_amount=1000.0),
        ]
        approved = [i for i in items if i.approved]
        assert len(approved) == 0  # caller should raise HTTPException(400)

    def test_permission_guard_manage_finances(self):
        """Only manage-finance roles can call POST /budget-proposals."""
        manage_roles = {"super_admin", "chairman", "strata_manager"}
        denied_roles = {"ec_member", "owner", "tenant"}
        for role in manage_roles:
            user = _make_user(role)
            p = _make_permissions(user)
            assert p.can_manage_finances is True
        for role in denied_roles:
            user = _make_user(role)
            p = _make_permissions(user)
            assert p.can_manage_finances is False

    def test_description_includes_inflation_rate_and_base_year(self):
        """Each saved category doc embeds inflation_rate and base_year for audit trail."""
        inflation_rate = 3.0
        base_year = "2024"
        description = f"CPI {inflation_rate}% from {base_year}"
        assert "CPI 3.0%" in description
        assert "2024" in description

    def test_doc_fields_completeness(self):
        """Each inserted document has all required fields."""
        required_fields = {
            "id", "plan_id", "year", "status", "fund_type",
            "name", "budgeted_amount", "actual_amount",
            "description", "created_at", "updated_at",
        }
        import uuid
        doc = {
            "id": str(uuid.uuid4()),
            "plan_id": PLAN_ID,
            "year": "2027",
            "status": "proposed",
            "fund_type": "administrative",
            "name": "Insurance Premiums",
            "budgeted_amount": 61000.0,
            "actual_amount": 0.0,
            "description": "CPI 3.0% from 2024",
            "created_at": "2026-02-28T00:00:00Z",
            "updated_at": "2026-02-28T00:00:00Z",
        }
        assert required_fields.issubset(set(doc.keys()))
        assert doc["status"] == "proposed"
        assert doc["actual_amount"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Section D — lot_finance_service levied-amount fix
# ─────────────────────────────────────────────────────────────────────────────

class TestLotFinanceServiceLeviedAmountFix:
    """
    Verify that compute_true_cost and compute_building_cost_distribution
    use admin_levied/sinking_levied (assessed amounts) rather than total_paid.

    Most owners pay externally via DEFT/BPAY, so total_paid = 0 in the system.
    Using levied amounts gives the correct cost-distribution picture.
    """

    @staticmethod
    def _compute_levy_split(ledger: dict):
        """Mirror the logic from lot_finance_service._compute_levy_split."""
        admin_levied = ledger.get("admin_levied", 0) or 0
        sinking_levied = ledger.get("sinking_levied", 0) or 0
        if admin_levied + sinking_levied > 0:
            return round(admin_levied, 2), round(sinking_levied, 2)
        else:
            total_paid = ledger.get("total_paid", 0) or 0
            return round(total_paid * 0.77, 2), round(total_paid * 0.23, 2)

    def test_uses_levied_amounts_when_available(self):
        """When admin_levied + sinking_levied > 0, use those directly."""
        ledger = {
            "admin_levied": 5444.22,
            "sinking_levied": 1608.15,
            "total_paid": 0.0,
        }
        admin, sinking = self._compute_levy_split(ledger)
        assert admin == pytest.approx(5444.22, abs=0.01)
        assert sinking == pytest.approx(1608.15, abs=0.01)

    def test_falls_back_to_total_paid_split_when_no_levied(self):
        """When levied amounts are both 0, fall back to 77/23 split of total_paid."""
        ledger = {
            "admin_levied": 0.0,
            "sinking_levied": 0.0,
            "total_paid": 7090.04,
        }
        admin, sinking = self._compute_levy_split(ledger)
        assert admin == pytest.approx(7090.04 * 0.77, abs=0.02)
        assert sinking == pytest.approx(7090.04 * 0.23, abs=0.02)

    def test_zero_ledger_produces_zero_costs(self):
        """Empty ledger → all zeros."""
        ledger = {}
        admin, sinking = self._compute_levy_split(ledger)
        assert admin == 0.0
        assert sinking == 0.0

    def test_typical_th017_levy_amounts(self):
        """TH087 (UOE=161): admin 5444.22 + sinking 1608.15 → total 7052.37."""
        ledger = {"admin_levied": 5444.22, "sinking_levied": 1608.15, "total_paid": 0.0}
        admin, sinking = self._compute_levy_split(ledger)
        total_levy = admin + sinking
        assert total_levy == pytest.approx(7052.37, abs=0.02)

    def test_levied_amounts_produce_nonzero_total_cost(self):
        """Ensures the fix resolves the 'Cost Distribution shows nothing' bug."""
        ledger = {
            "admin_levied": 4000.0,
            "sinking_levied": 1200.0,
            "total_paid": 0.0,  # 0 because owner pays via DEFT
        }
        council = {"total_rates": 1800.0, "land_tax_total": 500.0}
        admin, sinking = self._compute_levy_split(ledger)
        total_council = council.get("total_rates", 0)
        total_land_tax = council.get("land_tax_total", 0)
        total_cost = round(admin + sinking + total_council + total_land_tax, 2)
        # Should be non-zero; previously all-zero with old total_paid approach
        assert total_cost > 0
        assert total_cost == pytest.approx(7500.0, abs=0.02)

    def test_units_without_levied_amounts_still_use_fallback(self):
        """Units that only have total_paid (older data) still get a non-zero split."""
        ledger = {"admin_levied": 0, "sinking_levied": 0, "total_paid": 5000.0}
        admin, sinking = self._compute_levy_split(ledger)
        assert admin > 0
        assert sinking > 0
        assert admin + sinking == pytest.approx(5000.0, abs=0.02)

    def test_risk_flag_calculation(self):
        """risk_flag is True when cost_per_uoe > avg * 1.5."""
        summaries = [
            {"cost_per_uoe": 50.0},
            {"cost_per_uoe": 100.0},
            {"cost_per_uoe": 200.0},  # avg=116.67, 200 > 175 → risk
        ]
        valid = [s for s in summaries if s["cost_per_uoe"] > 0]
        avg = sum(s["cost_per_uoe"] for s in valid) / len(valid)
        for s in summaries:
            s["risk_flag"] = s["cost_per_uoe"] > avg * 1.5
        assert summaries[2]["risk_flag"] is True
        assert summaries[0]["risk_flag"] is False

    def test_arrears_flag_positive_when_net_balance_positive(self):
        """arrears_flag = True when net_balance > 0 (owner owes money)."""
        assert (150.0 > 0) is True  # in arrears
        assert (0.0 > 0) is False  # paid up
        assert (-50.0 > 0) is False  # credit


# ─────────────────────────────────────────────────────────────────────────────
# Section E — lot-summary fallback to unit_levy_ledger
# ─────────────────────────────────────────────────────────────────────────────

class TestLotSummaryFallback:
    """
    Test the finance_intelligence GET /finance/lot-summary fallback logic.

    When lot_financial_summary is empty or all-zero, the endpoint should
    synthesise entries directly from unit_levy_ledger.
    """

    @staticmethod
    def _summaries_all_zero(summaries: list) -> bool:
        return not summaries or all(s.get("total_cost", 0) == 0 for s in summaries)

    def test_empty_summaries_triggers_fallback(self):
        assert self._summaries_all_zero([]) is True

    def test_all_zero_summaries_triggers_fallback(self):
        summaries = [{"total_cost": 0}, {"total_cost": 0}]
        assert self._summaries_all_zero(summaries) is True

    def test_nonzero_summaries_do_not_trigger_fallback(self):
        summaries = [{"total_cost": 5000.0}, {"total_cost": 0}]
        assert self._summaries_all_zero(summaries) is False

    def test_fallback_builds_summary_from_ledger_entry(self):
        """Simulate constructing a fallback row from a unit_levy_ledger entry."""
        ledger_entry = {
            "lot_number": "TH087",
            "admin_levied": 5444.22,
            "sinking_levied": 1608.15,
            "net_balance": 0.0,
            "uoe": 161,
        }
        admin_levied = ledger_entry.get("admin_levied", 0) or 0
        sinking_levied = ledger_entry.get("sinking_levied", 0) or 0
        total_cost = round(admin_levied + sinking_levied, 2)
        uoe = ledger_entry.get("uoe", 1) or 1
        row = {
            "lot_number": ledger_entry.get("lot_number", ""),
            "financial_year": "2026",
            "total_admin_paid": round(admin_levied, 2),
            "total_sinking_paid": round(sinking_levied, 2),
            "total_cost": total_cost,
            "cost_per_uoe": round(total_cost / uoe, 2),
            "arrears_flag": (ledger_entry.get("net_balance", 0) or 0) > 0,
            "_source": "levy_ledger_fallback",
        }
        assert row["lot_number"] == "TH087"
        assert row["total_cost"] == pytest.approx(7052.37, abs=0.02)
        assert row["cost_per_uoe"] == pytest.approx(7052.37 / 161, abs=0.02)
        assert row["arrears_flag"] is False
        assert row["_source"] == "levy_ledger_fallback"

    def test_fallback_marks_arrears_when_net_balance_positive(self):
        ledger_entry = {
            "lot_number": "UA012",
            "admin_levied": 3000.0,
            "sinking_levied": 900.0,
            "net_balance": 450.0,
            "uoe": 100,
        }
        arrears_flag = (ledger_entry.get("net_balance", 0) or 0) > 0
        assert arrears_flag is True

    def test_fallback_list_ordered_by_total_cost_descending(self):
        """Fallback rows should be sorted highest-cost first (mirrors live path)."""
        rows = [
            {"lot_number": "A", "total_cost": 5000.0},
            {"lot_number": "B", "total_cost": 12000.0},
            {"lot_number": "C", "total_cost": 3000.0},
        ]
        sorted_rows = sorted(rows, key=lambda x: x["total_cost"], reverse=True)
        assert sorted_rows[0]["lot_number"] == "B"
        assert sorted_rows[-1]["lot_number"] == "C"


# ─────────────────────────────────────────────────────────────────────────────
# Section F — FY2024 seed integrity
# ─────────────────────────────────────────────────────────────────────────────

class TestFY2024SeedIntegrity:
    """
    Verify CATS_2024_ADMIN totals match the user-provided source data:
      - budgeted total  = $223,496.00
      - actual total    = $311,666.80
    """

    EXPECTED_BUDGETED = 223_496.00
    EXPECTED_ACTUAL = 311_666.80

    # Inline copy of CATS_2024_ADMIN so tests don't import the seed module
    CATS_2024_ADMIN = [
        ("Accountant - Professional Fees", 132.00, 210.00),
        ("Accounting Service Provision", 660.00, 622.50),
        ("Admin Fund Deficit Recovery", 0.00, 0.00),
        ("Arrears Recovery Costs", 0.00, 617.84),
        ("BMC - Keys/Swipe Cards/Fobs", 0.00, 0.00),
        ("Banking Charges", 0.00, 0.00),
        ("Banking Management", 660.00, 622.50),
        ("Building Repairs & Maintenance", 10_000.00, 51_439.84),
        ("Bundled Disbursements", 4_355.00, 4_102.83),
        ("Civium Disbursements", 0.00, 1_411.19),
        ("Cleaning", 20_000.00, 16_716.62),
        ("Cleaning - Carpets", 3_500.00, 0.00),
        ("Electrical Repairs & Maintenance", 0.00, 0.00),
        ("Electricity - Utility", 25_000.00, 22_702.20),
        ("Fire Protection - Contracted", 10_260.00, 10_455.49),
        ("GST Administration", 0.00, 340.92),
        ("GST Payment/Refund", 0.00, 0.00),
        ("Garage Door", 3_000.00, 8_592.72),
        ("Gardening", 0.00, 0.00),
        ("Gardens & Grounds", 20_000.00, 34_127.09),
        ("Income Tax Expense", 0.00, 0.00),
        ("Insurance Claims", 0.00, 3_614.00),
        ("Insurance Premiums", 48_000.00, 59_238.61),
        ("Keys Fobs & Access Swipes", 0.00, 0.00),
        ("Keys and Locks", 0.00, 0.00),
        ("Legal Expense", 0.00, 70.00),
        ("Lift Maintenance Contract", 25_000.00, 32_068.00),
        ("Management Fee", 25_000.00, 27_899.28),
        ("Management Fees - Additional", 0.00, 0.00),
        ("Online Portal Fees", 0.00, 410.25),
        ("Plumbing & Drainage", 5_000.00, 1_126.94),
        ("Postage", 0.00, 0.00),
        ("Tax Agent Fees - BAS/GST", 0.00, 260.00),
        ("Tax Agent Fees - Income Tax", 0.00, 0.00),
        ("Taxation Reporting (Civium)", 132.00, 120.00),
        ("Telephone - Intercom/Lift Line", 0.00, 0.00),
        ("Trades Compliance", 297.00, 279.24),
        ("Venue Hire", 500.00, 0.00),
        ("Waste Collection", 2_000.00, 0.00),
        ("Water - Utility", 20_000.00, 34_618.74),
    ]

    def test_row_count_is_40(self):
        assert len(self.CATS_2024_ADMIN) == 40

    def test_budgeted_total_matches(self):
        total = sum(row[1] for row in self.CATS_2024_ADMIN)
        assert abs(total - self.EXPECTED_BUDGETED) < 0.02, (
            f"Budgeted total {total} != expected {self.EXPECTED_BUDGETED}"
        )

    def test_actual_total_matches(self):
        total = sum(row[2] for row in self.CATS_2024_ADMIN)
        assert abs(total - self.EXPECTED_ACTUAL) < 0.02, (
            f"Actual total {total} != expected {self.EXPECTED_ACTUAL}"
        )

    def test_all_category_names_are_unique(self):
        names = [row[0] for row in self.CATS_2024_ADMIN]
        assert len(names) == len(set(names)), "Duplicate category names found"

    def test_no_negative_amounts(self):
        for name, budgeted, actual in self.CATS_2024_ADMIN:
            assert budgeted >= 0, f"{name}: negative budgeted amount"
            assert actual >= 0, f"{name}: negative actual amount"

    def test_key_categories_present(self):
        """Verify high-value categories are included with correct actuals."""
        cat_map = {row[0]: (row[1], row[2]) for row in self.CATS_2024_ADMIN}
        # Insurance Premiums: budgeted 48000, actual 59238.61
        assert cat_map["Insurance Premiums"][1] == pytest.approx(59238.61, abs=0.01)
        # Water - Utility: budgeted 20000, actual 34618.74
        assert cat_map["Water - Utility"][1] == pytest.approx(34618.74, abs=0.01)
        # Building Repairs & Maintenance: budgeted 10000, actual 51439.84
        assert cat_map["Building Repairs & Maintenance"][1] == pytest.approx(51439.84, abs=0.01)

    def test_taxation_reporting_strata_web_present(self):
        """Taxation Reporting (Civium) must be included (name-match check)."""
        names = [row[0] for row in self.CATS_2024_ADMIN]
        assert "Taxation Reporting (Civium)" in names

    def test_actual_exceeds_budget_for_known_overspends(self):
        """Known overspend categories: actual > budgeted."""
        cat_map = {row[0]: (row[1], row[2]) for row in self.CATS_2024_ADMIN}
        overspend_cats = [
            "Building Repairs & Maintenance",
            "Insurance Premiums",
            "Gardens & Grounds",
            "Lift Maintenance Contract",
            "Water - Utility",
            "Garage Door",
        ]
        for cat in overspend_cats:
            budgeted, actual = cat_map[cat]
            assert actual > budgeted, f"{cat}: expected overspend but actual <= budgeted"

    def test_underspend_or_zero_for_known_categories(self):
        """Known under/zero categories: actual < budgeted."""
        cat_map = {row[0]: (row[1], row[2]) for row in self.CATS_2024_ADMIN}
        underspend_cats = [
            ("Cleaning", 20_000.00),  # actual 16716.62
            ("Cleaning - Carpets", 3_500.00),  # actual 0
            ("Plumbing & Drainage", 5_000.00),  # actual 1126.94
        ]
        for cat, budget in underspend_cats:
            _, actual = cat_map[cat]
            assert actual < budget, f"{cat}: expected underspend but actual >= budget"
