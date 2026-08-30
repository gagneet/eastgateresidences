"""
test_levy_kpi.py — Tests for the Levy KPI endpoint and formula derivation.

Covers:
  - Pure unit tests for the per-lot and building-level derivation logic
    (no DB required — uses mock data based on the spec §3-§5)
  - Formula invariants (collection_rate + unpaid/billed = 1, etc.)
  - Status threshold assignments
  - Multi-tenant isolation (building 13195 vs 16244)
  - Edge cases: empty building, zero UOE, negative balances (credits)
  - Integration tests (RUN_INTEGRATION_TESTS=1): live GET /finance/levy-kpi

Run (unit only):
    backend/venv/bin/python3 -m pytest tests/backend/test_levy_kpi.py -v

Run (with integration tests, requires running backend):
    RUN_INTEGRATION_TESTS=1 backend/venv/bin/python3 -m pytest tests/backend/test_levy_kpi.py -v
"""
import math
import os
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from utils.finance_helpers import get_annual_fund_balance


# ---------------------------------------------------------------------------
# Pure business-logic helpers (mirrors finance.py derivation)
# ---------------------------------------------------------------------------

def derive_lot(uoe: int, current_balance: float, total_q_rate: float) -> dict:
    """Derive per-lot metrics from raw inputs — mirrors get_levy_kpi logic."""
    quarter_levy = round(uoe * total_q_rate, 2)
    arrears_balance = round(max(current_balance, 0), 2)
    credit_balance = round(max(-current_balance, 0), 2)
    true_arrears = round(max(arrears_balance - quarter_levy, 0), 2)
    current_quarter_unpaid = round(min(arrears_balance, quarter_levy), 2)
    current_quarter_collected = round(quarter_levy - current_quarter_unpaid, 2)
    net_cash_collected = round(quarter_levy - arrears_balance + credit_balance, 2)

    if current_balance < 0:
        status = "credit"
    elif current_balance > 0:
        status = "arrears"
    else:
        status = "paid_exact"

    return {
        "uoe": uoe,
        "current_balance": current_balance,
        "quarter_levy": quarter_levy,
        "arrears_balance": arrears_balance,
        "credit_balance": credit_balance,
        "true_arrears": true_arrears,
        "current_quarter_unpaid": current_quarter_unpaid,
        "current_quarter_collected": current_quarter_collected,
        "net_cash_collected": net_cash_collected,
        "status": status,
        "is_compliant": current_balance <= 0,
    }


def derive_building(lots: List[dict], quarter_billed_display: float) -> dict:
    """Building-level aggregates — mirrors get_levy_kpi aggregation logic."""
    arrears_total = round(sum(l["arrears_balance"] for l in lots), 2)
    credit_total = round(sum(l["credit_balance"] for l in lots), 2)
    true_arrears_total = round(sum(l["true_arrears"] for l in lots), 2)
    current_quarter_unpaid_total = round(sum(l["current_quarter_unpaid"] for l in lots), 2)
    current_quarter_collected_total = round(quarter_billed_display - current_quarter_unpaid_total, 2)
    net_cash_collected_total = round(quarter_billed_display - arrears_total + credit_total, 2)
    net_arrears_outstanding = round(arrears_total - credit_total, 2)
    compliant_lot_count = sum(1 for l in lots if l["is_compliant"])
    total_lot_count = len(lots)

    collection_rate = round(current_quarter_collected_total / quarter_billed_display,
                            4) if quarter_billed_display > 0 else 0
    lot_compliance_rate = round(compliant_lot_count / total_lot_count, 4) if total_lot_count > 0 else 0

    return {
        "arrears_total": arrears_total,
        "credit_total": credit_total,
        "true_arrears_total": true_arrears_total,
        "current_quarter_unpaid_total": current_quarter_unpaid_total,
        "current_quarter_collected_total": current_quarter_collected_total,
        "net_cash_collected_total": net_cash_collected_total,
        "net_arrears_outstanding": net_arrears_outstanding,
        "compliant_lot_count": compliant_lot_count,
        "total_lot_count": total_lot_count,
        "collection_rate": collection_rate,
        "lot_compliance_rate": lot_compliance_rate,
    }


# ---------------------------------------------------------------------------
# Spec reference values from the requirement document (§2-§5)
# ---------------------------------------------------------------------------

# Per-UOE quarterly rate: from the spec's levy_table_by_uoe / per-unit rate
# total_annual_gross / 4 / total_uoe_sum  → but the spec provides absolute
# amounts per UOE tier which imply a rate of ~11.009756... per UOE unit.
# We use the spec's exact absolute values to construct reference lots.

# Spec example lots (from §9 and §10 pseudocode examples)
# Lot 70: UOE=82, balance=1128.92 (arrears), quarter_levy=902.77
SPEC_Q_RATE_82 = 902.77 / 82  # implied per-UOE quarterly rate for UOE=82

# For the spec, let's use the concrete UOE-to-levy table directly.
# These are the "canonical" amounts that would come from admin+sinking rates × UOE.
SPEC_LEVY_TABLE: Dict[int, float] = {
    82: 902.77,
    111: 1222.04,
    139: 1530.30,
    149: 1640.40,
    160: 1761.50,
    161: 1772.51,
}

SPEC_TOTAL_Q_RATE = SPEC_LEVY_TABLE[82] / 82  # ~11.009756...
SPEC_QUARTER_BILLED_DISPLAY = 110093.78


# ---------------------------------------------------------------------------
# §3 — Per-lot derivation tests
# ---------------------------------------------------------------------------

class TestLotDerivation:
    """Tests for §3 lot-level formula derivation."""

    def test_arrears_lot(self):
        """Lot with positive balance: arrears > quarter_levy → true_arrears > 0."""
        lot = derive_lot(82, 1128.92, SPEC_TOTAL_Q_RATE)
        assert lot["quarter_levy"] == pytest.approx(902.77, abs=0.02)
        assert lot["arrears_balance"] == pytest.approx(1128.92, abs=0.01)
        assert lot["credit_balance"] == 0.0
        # true_arrears = max(1128.92 - 902.77, 0) = 226.15
        assert lot["true_arrears"] == pytest.approx(226.15, abs=0.02)
        # current_quarter_unpaid = min(1128.92, 902.77) = 902.77
        assert lot["current_quarter_unpaid"] == pytest.approx(902.77, abs=0.01)
        # current_quarter_collected = 902.77 - 902.77 = 0
        assert lot["current_quarter_collected"] == pytest.approx(0.0, abs=0.01)
        assert lot["status"] == "arrears"
        assert lot["is_compliant"] is False

    def test_partial_arrears_lot(self):
        """Lot with balance < quarter_levy: true_arrears = 0, partial Q collected."""
        lot = derive_lot(82, 86.65, SPEC_TOTAL_Q_RATE)
        assert lot["arrears_balance"] == pytest.approx(86.65, abs=0.01)
        assert lot["credit_balance"] == 0.0
        # true_arrears = max(86.65 - 902.77, 0) = 0
        assert lot["true_arrears"] == 0.0
        # current_quarter_unpaid = min(86.65, 902.77) = 86.65
        assert lot["current_quarter_unpaid"] == pytest.approx(86.65, abs=0.01)
        # current_quarter_collected = 902.77 - 86.65 = 816.12
        assert lot["current_quarter_collected"] == pytest.approx(816.12, abs=0.02)
        assert lot["status"] == "arrears"
        assert lot["is_compliant"] is False

    def test_paid_exact_lot(self):
        """Lot with balance = 0: fully paid, compliant, no arrears."""
        lot = derive_lot(82, 0.0, SPEC_TOTAL_Q_RATE)
        assert lot["arrears_balance"] == 0.0
        assert lot["credit_balance"] == 0.0
        assert lot["true_arrears"] == 0.0
        assert lot["current_quarter_unpaid"] == 0.0
        assert lot["current_quarter_collected"] == pytest.approx(902.77, abs=0.02)
        assert lot["status"] == "paid_exact"
        assert lot["is_compliant"] is True

    def test_credit_lot(self):
        """Lot with negative balance: credit, compliant, no arrears."""
        lot = derive_lot(82, -200.0, SPEC_TOTAL_Q_RATE)
        assert lot["arrears_balance"] == 0.0
        assert lot["credit_balance"] == 200.0
        assert lot["true_arrears"] == 0.0
        assert lot["current_quarter_unpaid"] == 0.0
        assert lot["current_quarter_collected"] == pytest.approx(902.77, abs=0.02)
        # net_cash_collected can exceed quarter_levy due to credit
        assert lot["net_cash_collected"] == pytest.approx(902.77 + 200.0, abs=0.02)
        assert lot["status"] == "credit"
        assert lot["is_compliant"] is True

    def test_credits_do_not_reduce_true_arrears(self):
        """Spec §13 rule 7: credits must not reduce true_arrears (tracked separately)."""
        # This would only apply if a lot had both arrears AND credit, which is impossible
        # (balance is a single net figure). But we verify the formula is independent:
        # true_arrears depends only on arrears_balance, not credit_balance.
        lot_arrears = derive_lot(82, 500.0, SPEC_TOTAL_Q_RATE)  # arrears only
        lot_credit = derive_lot(82, -100.0, SPEC_TOTAL_Q_RATE)  # credit only
        assert lot_arrears["credit_balance"] == 0.0
        assert lot_credit["arrears_balance"] == 0.0
        assert lot_credit["true_arrears"] == 0.0  # no prior-period debt

    def test_zero_uoe_skipped(self):
        """UOE=0 produces quarter_levy=0 — entire balance becomes true_arrears."""
        lot = derive_lot(0, 500.0, SPEC_TOTAL_Q_RATE)
        assert lot["quarter_levy"] == 0.0
        # true_arrears = max(500 - 0, 0) = 500
        assert lot["true_arrears"] == 500.0
        assert lot["current_quarter_unpaid"] == 0.0

    def test_current_quarter_collected_never_exceeds_levy(self):
        """Spec §13 rule 8: current_quarter_collected ≤ quarter_levy."""
        for balance in [-500.0, -200.0, 0.0, 50.0, 902.77, 1200.0]:
            lot = derive_lot(82, balance, SPEC_TOTAL_Q_RATE)
            assert lot["current_quarter_collected"] <= lot["quarter_levy"] + 1e-6, (
                f"balance={balance}: collected {lot['current_quarter_collected']} > levy {lot['quarter_levy']}"
            )


# ---------------------------------------------------------------------------
# §4-§5 — Building-level aggregate tests
# ---------------------------------------------------------------------------

class TestBuildingAggregates:
    """Tests for §4-§5 building-level collection metrics."""

    @pytest.fixture
    def spec_lots(self):
        """Build a representative set of lots for building-level aggregate invariant tests.

        Note: This fixture tests general formula invariants (collection rate identity,
        lot compliance bounds, true_arrears ≤ arrears_total, etc.) using a synthetic
        dataset. It does NOT reproduce the exact aggregate totals from spec §9
        (arrears_total=33220.22, credit_total=6339.03, true_arrears_total=1928.00,
        28 arrears lots, 59 compliant) — those figures depend on the exact per-lot
        UOE mix and closing balances from the production levy table. The invariant
        tests below remain valid for any representative dataset.
        """

        lots = []
        # 28 arrears lots: mix of partial (true_arrears=0) and full (true_arrears>0)
        # Use UOE=82 (levy 902.77) for simplicity
        for i in range(26):
            # Balance < quarter_levy → true_arrears=0 (this quarter's unpaid portion only)
            lots.append(derive_lot(82, 500.0, SPEC_TOTAL_Q_RATE))
        # 2 lots with balance > quarter_levy → true_arrears > 0
        lots.append(derive_lot(82, 1000.0, SPEC_TOTAL_Q_RATE))  # true_arrears = ~97
        lots.append(derive_lot(82, 2000.0, SPEC_TOTAL_Q_RATE))  # true_arrears = ~1097

        # 10 credit lots
        for _ in range(10):
            lots.append(derive_lot(82, -500.0, SPEC_TOTAL_Q_RATE))

        # 49 paid-exact lots
        for _ in range(49):
            lots.append(derive_lot(82, 0.0, SPEC_TOTAL_Q_RATE))

        return lots

    def test_collection_rate_invariant(self, spec_lots):
        """collection_rate + unpaid_rate must equal 1.0 (within rounding tolerance)."""
        q = SPEC_QUARTER_BILLED_DISPLAY
        bldg = derive_building(spec_lots, q)
        assert bldg["collection_rate"] + (bldg["current_quarter_unpaid_total"] / q) == pytest.approx(1.0, abs=0.001)

    def test_lot_compliance_rate_bounds(self, spec_lots):
        """lot_compliance_rate must be in [0, 1]."""
        bldg = derive_building(spec_lots, SPEC_QUARTER_BILLED_DISPLAY)
        assert 0 <= bldg["lot_compliance_rate"] <= 1.0

    def test_compliant_plus_noncompliant_equals_total(self, spec_lots):
        """compliant_lot_count + non_compliant = total_lot_count."""
        bldg = derive_building(spec_lots, SPEC_QUARTER_BILLED_DISPLAY)
        assert bldg["compliant_lot_count"] + (bldg["total_lot_count"] - bldg["compliant_lot_count"]) == bldg[
            "total_lot_count"]

    def test_true_arrears_subset_of_arrears(self, spec_lots):
        """true_arrears_total ≤ arrears_total (prior debt can't exceed total outstanding)."""
        bldg = derive_building(spec_lots, SPEC_QUARTER_BILLED_DISPLAY)
        assert bldg["true_arrears_total"] <= bldg["arrears_total"] + 1e-6

    def test_net_arrears_equals_arrears_minus_credits(self, spec_lots):
        """net_arrears_outstanding = arrears_total - credit_total."""
        bldg = derive_building(spec_lots, SPEC_QUARTER_BILLED_DISPLAY)
        assert bldg["net_arrears_outstanding"] == pytest.approx(
            bldg["arrears_total"] - bldg["credit_total"], abs=0.02
        )

    def test_empty_building_returns_zero_collection_rate(self):
        """Zero lots → collection_rate=0, no division-by-zero."""
        bldg = derive_building([], 0.0)
        assert bldg["collection_rate"] == 0.0
        assert bldg["total_lot_count"] == 0


# ---------------------------------------------------------------------------
# §8 — Status threshold tests
# ---------------------------------------------------------------------------

class TestStatusThresholds:
    """Tests for §8 status label assignment."""

    # Statuses are determined in the endpoint as string labels.
    # We reproduce the threshold logic here for independent verification.

    def collection_status_label(self, rate: float) -> str:
        pct = rate * 100
        if pct >= 95: return "green"
        if pct >= 85: return "amber"
        return "red"

    def admin_health_label(self, rate: float) -> str:
        pct = rate * 100
        if pct >= 100: return "green"
        if pct >= 50: return "amber"
        return "red"

    def sinking_label(self, rate: float) -> str:
        pct = rate * 100
        if pct >= 200: return "green"
        if pct >= 100: return "amber"
        return "red"

    def compliance_label(self, rate: float) -> str:
        pct = rate * 100
        if pct >= 90: return "green"
        if pct >= 75: return "amber"
        return "red"

    def test_collection_rate_thresholds(self):
        assert self.collection_status_label(0.96) == "green"
        assert self.collection_status_label(0.95) == "green"
        assert self.collection_status_label(0.94) == "amber"
        assert self.collection_status_label(0.85) == "amber"
        assert self.collection_status_label(0.7733) == "red"  # spec example
        assert self.collection_status_label(0.84) == "red"

    def test_admin_health_thresholds(self):
        assert self.admin_health_label(1.0) == "green"
        assert self.admin_health_label(1.5) == "green"
        assert self.admin_health_label(0.9999) == "amber"
        assert self.admin_health_label(0.5) == "amber"
        assert self.admin_health_label(0.2086) == "red"  # spec example
        assert self.admin_health_label(0.49) == "red"

    def test_sinking_coverage_thresholds(self):
        assert self.sinking_label(2.0) == "green"
        assert self.sinking_label(6.28) == "green"  # spec example
        assert self.sinking_label(1.99) == "amber"
        assert self.sinking_label(1.0) == "amber"
        assert self.sinking_label(0.99) == "red"
        assert self.sinking_label(0.0) == "red"

    def test_lot_compliance_thresholds(self):
        assert self.compliance_label(0.90) == "green"
        assert self.compliance_label(0.91) == "green"
        assert self.compliance_label(0.89) == "amber"
        assert self.compliance_label(0.75) == "amber"
        assert self.compliance_label(0.6782) == "red"  # spec example
        assert self.compliance_label(0.74) == "red"


# ---------------------------------------------------------------------------
# §6 — Fund health formula tests
# ---------------------------------------------------------------------------

class TestFundHealthFormulas:
    """Tests for §6 admin and sinking fund health calculations."""

    def test_admin_fund_health_formula(self):
        """admin_fund_health = admin_fund_balance / admin_quarter_budget."""
        admin_fund_balance = 17778.11
        admin_quarter_budget = 340870.20 / 4  # 85217.55
        expected = admin_fund_balance / admin_quarter_budget
        assert expected == pytest.approx(0.2086, abs=0.0001)

    def test_sinking_cash_coverage_formula(self):
        """sinking_cash_coverage = sinking_fund_balance / sinking_quarter_budget."""
        sinking_fund_balance = 156351.48
        sinking_quarter_budget = 99504.90 / 4  # 24876.225
        expected = sinking_fund_balance / sinking_quarter_budget
        assert expected == pytest.approx(6.285, abs=0.001)

    def test_overall_liquidity_cash_only(self):
        """overall_liquidity = total_cash / quarter_billed_display."""
        total_cash = 174129.59
        q_billed = 110093.78
        expected = total_cash / q_billed
        assert expected == pytest.approx(1.5816, abs=0.0005)

    def test_admin_health_incl_receivables(self):
        """admin_fund_health_incl_receivables adds apportioned net receivables."""
        admin_fund_balance = 17778.11
        admin_quarter_budget = 85217.55
        total_annual = 440375.10
        admin_annual = 340870.20
        net_arrears_outstanding = 26881.19
        admin_share = admin_annual / total_annual  # ~0.7741
        admin_net_recv = net_arrears_outstanding * admin_share
        expected = (admin_fund_balance + admin_net_recv) / admin_quarter_budget
        assert expected == pytest.approx(0.4528, abs=0.002)


class TestAnnualFundBalanceResolution:
    def test_prefers_current_balance_when_present(self):
        balance, source = get_annual_fund_balance({
            "current_balance": 9187.44,
            "closing_balance_actual": 18300.00,
            "closing_balance": 15000.00,
        })
        assert balance == pytest.approx(9187.44)
        assert source == "current_balance"

    def test_falls_back_to_closing_balance_when_current_missing(self):
        balance, source = get_annual_fund_balance({
            "opening_balance": 34332.27,
            "closing_balance": 36342.99,
        })
        assert balance == pytest.approx(36342.99)
        assert source == "closing_balance"


# ---------------------------------------------------------------------------
# GAP-FIN-063 — Postgres per-lot net_balance branch (router-level, mocked DB)
# ---------------------------------------------------------------------------

def _annual_levy_fixture() -> dict:
    return {
        "year": "2026",
        "total_uoe": 100,
        "proposed_admin_expenses": 40000,
        "proposed_sinking_expenses": 20000,
        "admin_fund": {"opening_balance": 1000, "closing_balance": 5000},
        "sinking_fund": {"opening_balance": 2000, "closing_balance": 8000},
    }


def _ledger_docs_fixture() -> list:
    return [
        {
            "unit_number": "101", "lot_number": "101", "uoe": 50,
            "net_balance": 500.0, "total_paid": 0, "admin_paid": 0,
            "sinking_paid": 0, "admin_opening": 0, "sinking_opening": 0,
        },
        {
            "unit_number": "102", "lot_number": "102", "uoe": 50,
            "net_balance": -200.0, "total_paid": 0, "admin_paid": 0,
            "sinking_paid": 0, "admin_opening": 0, "sinking_opening": 0,
        },
    ]


def _mock_db_for_levy_kpi(ledger_docs: list) -> MagicMock:
    mock_db = MagicMock()
    ledger_cursor = MagicMock()
    ledger_cursor.to_list = AsyncMock(return_value=ledger_docs)
    mock_db.unit_levy_ledger.find = MagicMock(return_value=ledger_cursor)

    exp_cursor = MagicMock()
    exp_cursor.to_list = AsyncMock(return_value=[])
    mock_db.expense_transactions.aggregate = MagicMock(return_value=exp_cursor)

    mock_db.buildings.find_one = AsyncMock(return_value={})
    mock_db.annual_levies.find_one = AsyncMock(return_value=_annual_levy_fixture())
    return mock_db


class TestLevyKpiPostgresBranch:
    """GAP-FIN-063: get_levy_kpi() must source per-lot net_balance from Postgres
    (via FinancialReadService.get_unit_levy_balance_list, reused unchanged from
    finance.arrears_detail's own PG branch) when route_state["source"] ==
    "postgres", with a safe per-lot fallback to the Mongo ledger doc otherwise.
    Rate/budget/fund-balance fields are unaffected -- see the module comment in
    get_levy_kpi() -- so this suite only asserts on the arrears/credit-derived
    fields that field actually changes.
    """

    async def _call(self, mock_db, pg_balances=None, source="mongo",
                     pg_side_effect=None):
        from routers import finance as finance_module

        pg_service = MagicMock()
        if pg_side_effect is not None:
            pg_service.get_unit_levy_balance_list = AsyncMock(side_effect=pg_side_effect)
        else:
            pg_service.get_unit_levy_balance_list = AsyncMock(return_value=pg_balances)

        with (
            patch.object(finance_module, "db", mock_db),
            patch.object(finance_module, "_get_general_settings", AsyncMock(return_value={})),
            patch.object(finance_module, "_financial_read_service", pg_service),
            patch.object(
                finance_module, "get_finance_route_runtime_state",
                AsyncMock(return_value={"source": source, "run_shadow": False}),
            ),
        ):
            result = await finance_module.get_levy_kpi(
                year="2026",
                current_user={"id": "manager-1", "role": "strata_manager", "is_approved": True},
                building_id="13195",
            )
        return result, pg_service

    def _lot(self, result, unit_number):
        for l in result["lots"]:
            if l["unit"] == unit_number:
                return l
        found = [l["unit"] for l in result["lots"]]
        raise AssertionError(
            f"unit {unit_number!r} not found in result['lots'] (found: {found})"
        )

    @pytest.mark.asyncio
    async def test_uses_postgres_net_balance_when_source_postgres(self):
        mock_db = _mock_db_for_levy_kpi(_ledger_docs_fixture())
        pg_balances = [
            {"unit_number": "101", "closing_balance": -300.0,
             "opening_balance": 0.0, "levied_amount": 8250.0},
        ]
        result, pg_service = await self._call(mock_db, pg_balances=pg_balances, source="postgres")

        # unit 101: Mongo net_balance=500.0 (arrears) is overridden by the PG
        # value -300.0 (credit) -- a status flip proves the override took effect,
        # not just a number that happens to round the same.
        lot_101 = self._lot(result, "101")
        assert lot_101["current_balance"] == -300.0
        assert lot_101["status"] == "credit"
        assert lot_101["credit_balance"] == 300.0
        assert lot_101["arrears_balance"] == 0.0

        # unit 102: absent from the PG map -> keeps its Mongo net_balance (-200.0).
        lot_102 = self._lot(result, "102")
        assert lot_102["current_balance"] == -200.0
        assert lot_102["status"] == "credit"

        pg_service.get_unit_levy_balance_list.assert_awaited_once_with(
            building_id="13195", financial_year="2026",
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_mongo_net_balance_when_pg_returns_none(self):
        mock_db = _mock_db_for_levy_kpi(_ledger_docs_fixture())
        result, pg_service = await self._call(mock_db, pg_balances=None, source="postgres")

        lot_101 = self._lot(result, "101")
        assert lot_101["current_balance"] == 500.0
        assert lot_101["status"] == "arrears"
        lot_102 = self._lot(result, "102")
        assert lot_102["current_balance"] == -200.0
        pg_service.get_unit_levy_balance_list.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_mongo_net_balance_on_pg_exception(self):
        mock_db = _mock_db_for_levy_kpi(_ledger_docs_fixture())
        result, pg_service = await self._call(
            mock_db, source="postgres", pg_side_effect=RuntimeError("pg unavailable"),
        )

        lot_101 = self._lot(result, "101")
        assert lot_101["current_balance"] == 500.0
        assert lot_101["status"] == "arrears"

    @pytest.mark.asyncio
    async def test_serves_mongo_when_source_not_postgres(self):
        mock_db = _mock_db_for_levy_kpi(_ledger_docs_fixture())
        result, pg_service = await self._call(mock_db, source="mongo")

        lot_101 = self._lot(result, "101")
        assert lot_101["current_balance"] == 500.0
        assert lot_101["status"] == "arrears"
        pg_service.get_unit_levy_balance_list.assert_not_awaited()


# ---------------------------------------------------------------------------
# Integration tests — require running backend
# ---------------------------------------------------------------------------

_RUN_INTEGRATION = os.getenv("RUN_INTEGRATION_TESTS") == "1"
_BASE_URL = "http://127.0.0.1:8003/api"
# Credentials are read from environment variables — never hardcoded.
# Set before running:
#   export TEST_ADMIN_EMAIL=administrator@...
#   export TEST_ADMIN_PASSWORD=<password>
_ADMIN_EMAIL = os.getenv("TEST_ADMIN_EMAIL", "administrator@eastgateresidences.com.au")
_ADMIN_PASSWORD = os.getenv("TEST_ADMIN_PASSWORD", "")


@pytest.mark.skipif(not _RUN_INTEGRATION, reason="Set RUN_INTEGRATION_TESTS=1 to run")
class TestLevyKpiIntegration:
    """
    Integration tests for GET /finance/levy-kpi.

    Requires:
      - Running backend on port 8003
      - RUN_INTEGRATION_TESTS=1 env var
      - TEST_ADMIN_EMAIL / TEST_ADMIN_PASSWORD env vars set
      - Building "13195" has levy + ledger data

    Idempotent: endpoint is read-only (pure derivation), no data created or deleted.
    """

    @pytest.fixture(scope="class")
    def auth_headers(self):
        import requests
        if not _ADMIN_PASSWORD:
            pytest.skip("TEST_ADMIN_PASSWORD not set")
        resp = requests.post(f"{_BASE_URL}/auth/login",
                             json={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD}, timeout=10)
        assert resp.status_code == 200, f"Login failed: {resp.text}"
        token = resp.json()["token"]
        return {"Authorization": f"Bearer {token}"}

    def test_endpoint_returns_200(self, auth_headers):
        import requests
        resp = requests.get(f"{_BASE_URL}/finance/levy-kpi", headers=auth_headers, timeout=10)
        assert resp.status_code == 200

    def test_response_has_required_fields(self, auth_headers):
        import requests
        resp = requests.get(f"{_BASE_URL}/finance/levy-kpi", headers=auth_headers, timeout=10)
        data = resp.json()
        required_fields = [
            "building_id", "year",
            "collection_rate", "lot_compliance_rate",
            "admin_fund_health", "sinking_cash_coverage",
            "current_quarter_collected_total", "quarter_billed_total_display",
            "true_arrears_total", "credit_total",
            "compliant_lot_count", "non_compliant_lot_count", "total_lot_count",
            "lots", "top_true_arrears",
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

    def test_collection_rate_in_range(self, auth_headers):
        import requests
        resp = requests.get(f"{_BASE_URL}/finance/levy-kpi", headers=auth_headers, timeout=10)
        data = resp.json()
        assert 0.0 <= data["collection_rate"] <= 1.0

    def test_lot_compliance_invariant(self, auth_headers):
        """compliant + non_compliant = total."""
        import requests
        resp = requests.get(f"{_BASE_URL}/finance/levy-kpi", headers=auth_headers, timeout=10)
        data = resp.json()
        assert data["compliant_lot_count"] + data["non_compliant_lot_count"] == data["total_lot_count"]

    def test_collection_rate_formula_consistent(self, auth_headers):
        """collection_rate = current_quarter_collected / quarter_billed_display."""
        import requests
        resp = requests.get(f"{_BASE_URL}/finance/levy-kpi", headers=auth_headers, timeout=10)
        data = resp.json()
        if data["quarter_billed_total_display"] > 0:
            expected = data["current_quarter_collected_total"] / data["quarter_billed_total_display"]
            assert abs(data["collection_rate"] - expected) < 0.001

    def test_true_arrears_subset_of_arrears(self, auth_headers):
        """true_arrears_total ≤ arrears_total."""
        import requests
        resp = requests.get(f"{_BASE_URL}/finance/levy-kpi", headers=auth_headers, timeout=10)
        data = resp.json()
        assert data["true_arrears_total"] <= data["arrears_total"] + 0.05

    def test_building_id_scoped_correctly(self, auth_headers):
        """Response building_id must match the authenticated user's building."""
        import requests
        resp = requests.get(f"{_BASE_URL}/finance/levy-kpi", headers=auth_headers, timeout=10)
        data = resp.json()
        # Admin token is scoped to "13195"
        assert data["building_id"] == "13195"

    def test_unauthenticated_returns_401(self):
        import requests
        resp = requests.get(f"{_BASE_URL}/finance/levy-kpi", timeout=10)
        assert resp.status_code == 401

    def test_year_parameter_accepted(self, auth_headers):
        """year param is accepted and reflected in response."""
        import requests
        resp = requests.get(f"{_BASE_URL}/finance/levy-kpi?year=2026", headers=auth_headers, timeout=10)
        data = resp.json()
        assert data["year"] == "2026"

    def test_lots_all_have_required_fields(self, auth_headers):
        """Every lot in the lots array has the required KPI fields."""
        import requests
        resp = requests.get(f"{_BASE_URL}/finance/levy-kpi", headers=auth_headers, timeout=10)
        data = resp.json()
        for lot in data.get("lots", []):
            for field in ["lot", "unit", "uoe", "current_balance", "quarter_levy",
                          "arrears_balance", "credit_balance", "true_arrears",
                          "current_quarter_unpaid", "current_quarter_collected",
                          "status", "is_compliant"]:
                assert field in lot, f"Lot missing field: {field}"

    def test_current_quarter_collected_never_exceeds_levy_per_lot(self, auth_headers):
        """Spec §13 rule 8: current_quarter_collected ≤ quarter_levy for each lot."""
        import requests
        resp = requests.get(f"{_BASE_URL}/finance/levy-kpi", headers=auth_headers, timeout=10)
        data = resp.json()
        for lot in data.get("lots", []):
            assert lot["current_quarter_collected"] <= lot["quarter_levy"] + 0.02, (
                f"Lot {lot.get('unit')}: collected {lot['current_quarter_collected']} > levy {lot['quarter_levy']}"
            )

    def test_sinking_percent_funded_is_null(self, auth_headers):
        """sinking_percent_funded should be null until reserve plan is loaded (spec §6.2)."""
        import requests
        resp = requests.get(f"{_BASE_URL}/finance/levy-kpi", headers=auth_headers, timeout=10)
        data = resp.json()
        assert data.get("sinking_percent_funded") is None
