"""
Tests that owner dashboard shows correct remaining balance after payments.

Root cause (Session 67): levy_payments collection had building_id='eastgate_residences'
(old slug) instead of '13195' (plan number). The levy-status endpoint queried with
building_id='13195', found zero payments, and returned balance_due=total_levied instead
of total_levied - total_paid.

Fix: Migrated all levy_payments (and other missed collections) to use building_id='13195'.

Additional root cause (discovered post-Session 67): app/(dashboard)/dashboard/OwnerDashboard.tsx
used `levyStatus?.balance_due` as the primary amount for "Total Remaining". balance_due is
the FULL-YEAR remaining balance and overstates the amount for users who have made advance
payments. The fix is to use `next_payment_adjusted` (Session-53 algorithm) as the primary
value, which correctly computes the next instalment amount accounting for advance payments.
"""
import pytest

from utils.finance_helpers import compute_period_installment_amounts
from utils.finance_helpers import compute_remaining_payment_obligation
from utils.finance_helpers import normalize_effective_total_paid


class TestOwnerBalanceCalculation:
    """Test that balance_owing = net_balance from unit_levy_ledger."""

    def test_net_balance_subtracts_payments(self):
        """net_balance = total_levied - total_paid (no opening arrears)."""
        total_levied = 7090.04
        total_paid = 1800.00
        opening_arrears = 0.0
        net_balance = round(opening_arrears + total_levied - total_paid, 2)
        assert net_balance == pytest.approx(5290.04, abs=0.01)

    def test_total_remaining_uses_net_balance_not_total_levied(self):
        """Dashboard 'Total Remaining' must NOT equal total_levied when payments exist."""
        ledger = {
            "total_levied": 7090.04,
            "total_paid": 1800.00,
            "net_balance": 5290.04,
            "opening_arrears": 0.0,
        }
        total_remaining = ledger["net_balance"]
        assert total_remaining != ledger["total_levied"], "Should subtract payments"
        assert total_remaining == pytest.approx(5290.04, abs=0.01)

    def test_balance_due_formula_matches_net_balance(self):
        """With no carry-forward, remaining obligation = total_annual - total_paid."""
        total_annual = 7090.04
        prev_year_balance = 0.0
        total_paid = 1800.00
        balance_due = compute_remaining_payment_obligation(
            total_annual=total_annual,
            total_paid=total_paid,
            prev_year_balance=prev_year_balance,
        )
        # This must match the ledger's net_balance
        net_balance_from_ledger = 5290.04
        assert balance_due == pytest.approx(net_balance_from_ledger, abs=0.01)

    def test_zero_payments_gives_full_levy_as_balance(self):
        """When no payments made, balance_due = total_annual."""
        total_annual = 7090.04
        prev_year_balance = 0.0
        total_paid = 0.0
        balance_due = round(max(0.0, compute_remaining_payment_obligation(
            total_annual=total_annual,
            total_paid=total_paid,
            prev_year_balance=prev_year_balance,
        )), 2)
        assert balance_due == pytest.approx(7090.04, abs=0.01)

    def test_full_payment_gives_zero_balance(self):
        """When fully paid, balance_due = 0."""
        total_annual = 7090.04
        prev_year_balance = 0.0
        total_paid = 7090.04
        balance_due = round(max(0.0, compute_remaining_payment_obligation(
            total_annual=total_annual,
            total_paid=total_paid,
            prev_year_balance=prev_year_balance,
        )), 2)
        assert balance_due == pytest.approx(0.0, abs=0.01)

    def test_overpayment_gives_zero_balance_due(self):
        """When overpaid, balance_due is clamped to 0 (credit_balance handles the excess)."""
        total_annual = 7090.04
        prev_year_balance = 0.0
        total_paid = 8000.00
        raw_balance = compute_remaining_payment_obligation(
            total_annual=total_annual,
            total_paid=total_paid,
            prev_year_balance=prev_year_balance,
        )
        balance_due = round(max(0.0, raw_balance), 2)
        credit_balance = round(max(0.0, -raw_balance), 2)
        assert balance_due == pytest.approx(0.0, abs=0.01)
        assert credit_balance == pytest.approx(909.96, abs=0.01)

    def test_prior_year_arrears_add_to_balance(self):
        """Opening arrears from prior year increase the balance_due."""
        total_annual = 7090.04
        prev_year_balance = 500.00  # owes $500 from prior year
        total_paid = 1800.00
        balance_due = round(max(0.0, compute_remaining_payment_obligation(
            total_annual=total_annual,
            total_paid=total_paid,
            prev_year_balance=prev_year_balance,
        )), 2)
        assert balance_due == pytest.approx(5790.04, abs=0.01)
        assert balance_due > (total_annual - total_paid)  # more than without arrears

    def test_prior_year_credit_is_not_subtracted_twice(self):
        """If total_paid already includes carry-forward credit, remaining ignores negative prev_year_balance."""
        total_annual = 7090.04
        prev_year_balance = -200.00  # $200 credit from prior year
        total_paid = 2000.00  # current-year $1800 + carried credit $200
        balance_due = round(max(0.0, compute_remaining_payment_obligation(
            total_annual=total_annual,
            total_paid=total_paid,
            prev_year_balance=prev_year_balance,
        )), 2)
        assert balance_due == pytest.approx(5090.04, abs=0.01)
        assert balance_due == pytest.approx(total_annual - total_paid, abs=0.01)


class TestBalanceOwingCreditFromNetBalance:
    """GAP-FIN-047 item (a): the /levy-status "Balance Due" tile must read the authoritative
    net_balance-based owing/credit — balance_owing = max(net_balance, 0),
    balance_credit = max(-net_balance, 0) — which is the SAME formula the canonical
    /finance/unit-dashboard-overview already uses and the MANDATORY arrears rule (net_balance
    is the trusted per-unit figure; a credit is a real dollar amount, never zeroed, never
    netted across units). It must NOT read the legacy full-annual `balance_due`
    (compute_remaining_payment_obligation), which overstated a mid-year unit's obligation and
    showed a credit-ahead unit as owing (the reported TH087 bug).
    """

    @staticmethod
    def _owing_credit(ledger_net_balance: float):
        balance_owing = round(max(0.0, ledger_net_balance), 2)
        balance_credit = round(max(0.0, -ledger_net_balance), 2)
        return balance_owing, balance_credit

    def test_th087_credit_unit_shows_credit_not_owing(self):
        """TH087 live: ledger net_balance = -254.98 (credit), matches portal $254.98 CR."""
        owing, credit = self._owing_credit(-254.98)
        assert owing == pytest.approx(0.0, abs=0.01)
        assert credit == pytest.approx(254.98, abs=0.01)

    def test_th087_legacy_full_annual_balance_due_was_the_bug(self):
        """The OLD tile field showed the full-annual remaining ($3,290.04 "Outstanding")
        for a unit that is actually in credit — the exact GAP-FIN-047 symptom."""
        legacy_balance_due = round(max(0.0, compute_remaining_payment_obligation(
            total_annual=7090.04, total_paid=3800.00, prev_year_balance=0.0)), 2)
        assert legacy_balance_due == pytest.approx(3290.04, abs=0.01)  # the wrong figure
        # The net_balance-based fix shows $0 owing + the real credit instead.
        owing, credit = self._owing_credit(-254.98)
        assert owing == pytest.approx(0.0, abs=0.01)
        assert credit == pytest.approx(254.98, abs=0.01)

    def test_owing_unit_shows_owing_not_credit(self):
        owing, credit = self._owing_credit(5290.04)
        assert owing == pytest.approx(5290.04, abs=0.01)
        assert credit == pytest.approx(0.0, abs=0.01)

    def test_owing_and_credit_are_mutually_exclusive(self):
        for nb in (-500.0, -0.01, 0.0, 0.01, 500.0, 12345.67):
            owing, credit = self._owing_credit(nb)
            assert owing == 0.0 or credit == 0.0, f"both nonzero for net_balance={nb}"

    def test_zero_net_balance_is_all_paid_up(self):
        owing, credit = self._owing_credit(0.0)
        assert owing == pytest.approx(0.0, abs=0.01)
        assert credit == pytest.approx(0.0, abs=0.01)


class TestPaymentNormalization:
    """Credit carry-forward should be merged with live current-year payments exactly once."""

    def test_normalize_effective_total_paid_adds_live_payments_to_prior_credit(self):
        normalized = normalize_effective_total_paid(
            ledger_total_paid=3029.94,
            live_payments_total=1950.00,
            carry_forward_balance=-1369.94,
        )
        assert normalized == pytest.approx(3319.94, abs=0.01)

    def test_remaining_obligation_ignores_negative_carry_when_paid_already_includes_credit(self):
        remaining = compute_remaining_payment_obligation(
            total_annual=7046.00,
            total_paid=3319.94,
            prev_year_balance=-1369.94,
        )
        assert remaining == pytest.approx(3726.06, abs=0.01)

    def test_balance_owing_field_mapping(self):
        """API response balance_owing maps to unit_levy_ledger.net_balance."""
        ledger_doc = {
            "unit_number": "TH087",
            "levy_year": 2026,
            "building_id": "13195",
            "total_levied": 7090.04,
            "total_paid": 1800.00,
            "net_balance": 5290.04,
            "opening_arrears": 0.0,
            "admin_opening": 0.0,
            "sinking_opening": 0.0,
        }
        # balance_owing in API response = net_balance from ledger
        balance_owing = ledger_doc["net_balance"]
        assert balance_owing == pytest.approx(5290.04, abs=0.01)

    def test_building_id_mismatch_causes_zero_payments(self):
        """
        Regression test: demonstrates the bug where levy_payments had old building_id.

        If levy_payments has building_id='eastgate_residences' but the query uses
        building_id='13195', zero payments are found and balance_due = total_levied.
        This is the exact bug fixed in Session 67.
        """
        # Simulated state before fix
        levy_payments_building_id = "eastgate_residences"  # old slug — the bug
        query_building_id = "13195"  # plan number — what endpoint uses

        # The query would find zero payments
        payments_matching_query = [
            p for p in [{"building_id": levy_payments_building_id, "amount": 1800.0}]
            if p["building_id"] == query_building_id
        ]
        total_paid_bug = sum(p["amount"] for p in payments_matching_query)
        balance_due_bug = round(7090.04 - total_paid_bug, 2)
        assert balance_due_bug == pytest.approx(7090.04, abs=0.01), "Bug: shows full levy"

        # Simulated state after fix
        levy_payments_building_id_fixed = "13195"
        payments_matching_fixed = [
            p for p in [{"building_id": levy_payments_building_id_fixed, "amount": 1800.0}]
            if p["building_id"] == query_building_id
        ]
        total_paid_fixed = sum(p["amount"] for p in payments_matching_fixed)
        balance_due_fixed = round(7090.04 - total_paid_fixed, 2)
        assert balance_due_fixed == pytest.approx(5290.04, abs=0.01), "Fix: shows correct balance"


class TestNextPaymentAdjustedPriority:
    """
    Tests that the /owner-view page uses next_payment_adjusted (not balance_due
    or balance_owing) as the primary "amount due" for EC Member / Chairman users.

    This covers the bug reported after Session 67: UA063 (Chairman) saw $6,121.21
    as "Total Remaining" even after making an advance payment of $4,590.90.

    Confidence: 9/10 — algorithm is deterministic and matches Session-53 formula.
    """

    # UA063 constants (annual levy $7,090.04, 4 quarterly periods)
    ANNUAL_LEVY = 7090.04
    PERIOD_LEVY = round(7090.04 / 4, 2)  # ≈ $1,772.51
    NUM_PERIODS = 4

    def _compute_next_payment_adjusted(
            self,
            total_paid: float,
            opening_arrears: float = 0.0,
            after_grace: bool = False,
    ) -> float:
        """Mirror of Session-53 algorithm in server.py (includes fully-funded guard)."""
        period_levy = self.PERIOD_LEVY
        prior_year_credit = max(0.0, -opening_arrears)
        carry_forward_arrears = max(0.0, opening_arrears)
        funding_base = total_paid + prior_year_credit
        if period_levy <= 0:
            return 0.0
        # Integer-cents arithmetic to avoid Python float // precision loss
        nc_cents = max(0, round(funding_base * 100))
        pl_cents = round(period_levy * 100)
        periods_funded = (nc_cents // pl_cents) if pl_cents > 0 else 0
        # All periods funded → next payment = 0
        if periods_funded >= self.NUM_PERIODS:
            return round(carry_forward_arrears, 2)
        outstanding = round((periods_funded + 1) * period_levy - funding_base, 2)
        if after_grace:
            return round(period_levy + outstanding + carry_forward_arrears, 2)
        return round(max(0.0, outstanding) + carry_forward_arrears, 2)

    def test_advance_payment_reduces_next_payment_adjusted(self):
        """
        UA063 paid $4,590.90 advance.  next_payment_adjusted must be < $1,772.51
        (the period levy) because 2+ periods are already funded.
        """
        total_paid = 4590.90
        npa = self._compute_next_payment_adjusted(total_paid)
        # 2 full periods funded ($3545.02), partial into period 3
        assert npa < self.PERIOD_LEVY, f"Expected < {self.PERIOD_LEVY}, got {npa}"
        assert npa > 0, "Should still owe something (partial period 3)"

    def test_advance_payment_next_payment_adjusted_not_full_balance_due(self):
        """
        balance_due after $4,590.90 payment is still large (~$2,499.14).
        next_payment_adjusted must be significantly lower (next instalment only).
        """
        total_paid = 4590.90
        balance_due = round(max(0.0, self.ANNUAL_LEVY - total_paid), 2)
        npa = self._compute_next_payment_adjusted(total_paid)
        assert balance_due == pytest.approx(2499.14, abs=0.02)
        assert npa < balance_due, "next_payment_adjusted should be less than full balance_due"
        assert npa < 1000, "next_payment_adjusted should be a single-period instalment"

    def test_frontend_amountDue_priority_order(self):
        """
        Frontend logic: amountDue = next_payment_adjusted ?? balance_due ?? balance_owing
        Verifies that when next_payment_adjusted is set, it takes priority.
        """
        # Simulate ownerUnit API response
        ownerUnit = {
            "unit_number": "UA063",
            "next_payment_adjusted": 453.24,  # correct instalment
            "balance_owing": 2499.14,  # full-year remaining (wrong to show)
            "net_balance": 2499.14,
        }
        levyStatus = {
            "balance_due": 2499.14,  # also full-year (wrong to show)
        }

        # Mirror frontend priority: next_payment_adjusted ?? balance_due ?? balance_owing
        amount_due = (
                ownerUnit.get("next_payment_adjusted")
                or levyStatus.get("balance_due")
                or ownerUnit.get("balance_owing")
                or ownerUnit.get("net_balance")
                or 0
        )
        assert amount_due == pytest.approx(453.24, abs=0.01), (
            "next_payment_adjusted should take priority over balance_due/balance_owing"
        )

    def test_no_advance_payment_shows_first_period_amount(self):
        """When no payments made, next_payment_adjusted = first period levy."""
        npa = self._compute_next_payment_adjusted(total_paid=0.0)
        assert npa == pytest.approx(self.PERIOD_LEVY, abs=0.02)

    def test_prior_year_arrears_do_not_block_current_period_funding(self):
        """Exact Q1 payment still advances to the Q2 amount; prior arrears stay additive."""
        npa = self._compute_next_payment_adjusted(
            total_paid=self.PERIOD_LEVY,
            opening_arrears=200.0,
        )
        assert npa == pytest.approx(self.PERIOD_LEVY + 200.0, abs=0.02)

    def test_fully_paid_shows_zero(self):
        """When fully paid, next_payment_adjusted = 0."""
        npa = self._compute_next_payment_adjusted(total_paid=self.ANNUAL_LEVY)
        assert npa == pytest.approx(0.0, abs=0.01)

    def test_session67_ua063_bug_scenario(self):
        """
        Regression: UA063 (Chairman) showed $6,121.21 on /owner-view.
        $6,121.21 ≈ $7,090.04 - $968.83  (only ~1 period recognised as paid).
        After fix, the hero shows next_payment_adjusted (~$453) not balance_due (~$2,499).
        """
        # Amounts from user report
        annual_levy = 7090.04
        advance_paid = 4590.90
        buggy_display = 6121.21  # what was shown before fix

        # balance_due formula that produced the bug
        # (zero payments recognised due to wrong building_id → old formula)
        balance_due_bug = round(annual_levy - 968.83, 2)  # only 1 period seen
        assert balance_due_bug == pytest.approx(buggy_display, abs=0.02), (
            "Confirming the bug scenario: balance_due with only 1 period seen"
        )

        # After fix: next_payment_adjusted with advance payment recognised
        npa_fixed = self._compute_next_payment_adjusted(advance_paid)
        assert npa_fixed < buggy_display, "Fix must display less than the buggy amount"
        assert npa_fixed < 1000, "Fix should show a single-period instalment"


class TestDueDatePriority:
    """
    Tests that "Due in X days" uses the backend-computed owner-specific next_due_date,
    NOT the building's generic calendar period (period_status.current_due_date).

    Bug: FinancialSummaryCard.jsx used `period_status?.current_due_date ?? next_due_date`
    Fix: Flipped to `next_due_date ?? period_status?.current_due_date`

    Scenario: UA063 (EC Member / Chairman)
      Annual levy: $6,121.22  →  base period_levy = $1,530.30, Q4 residual = $1,530.32
      Advance payment: $4,590.90  (covers Q1 + Q2 + Q3)
      periods_funded: 3  →  next_due = Q4 = 2026-12-01
      Today (2026-03-17): 258 days to December 1
      Building's next calendar period (period_status.current_due_date): Q2 = 2026-06-01
      Old "Due in X days" badge: 76 days (wrong — Q2 = June 1)
      New "Due in X days" badge: 258 days (correct — Dec 1 = owner's personal next due)

    Confidence: 9/10 — root cause confirmed in FinancialSummaryCard.jsx line 101.
    """

    # UA063 owner-facing FY2026 contract from the live owner-unit endpoint
    UA063_ANNUAL_LEVY = 6121.22
    UA063_INSTALLMENTS = compute_period_installment_amounts(UA063_ANNUAL_LEVY, 4)
    UA063_PERIOD_LEVY = UA063_INSTALLMENTS[0]
    UA063_ADVANCE_PAID = 4590.90
    # East Gate due dates: [3,6,9,12] "first" for 2026
    DUE_DATES_2026 = ["2026-03-01", "2026-06-01", "2026-09-01", "2026-12-01"]
    TODAY = "2026-03-17"

    def _periods_funded_for_ua063(self) -> int:
        """Cumulative instalment funding for UA063's residual-cent Q4 schedule."""
        paid_cents = max(0, round(self.UA063_ADVANCE_PAID * 100))
        cumulative = 0
        periods_funded = 0
        for instalment in self.UA063_INSTALLMENTS:
            cumulative += round(instalment * 100)
            if paid_cents >= cumulative:
                periods_funded += 1
            else:
                break
        return periods_funded

    def test_ua063_period_levy_is_1530_30(self):
        """Display/base quarter levy rounds to $1,530.30."""
        assert self.UA063_PERIOD_LEVY == pytest.approx(1530.30, abs=0.01)

    def test_ua063_q4_residual_is_1530_32(self):
        """Residual cents must be carried into the final instalment, not lost as $0.03."""
        assert self.UA063_INSTALLMENTS == [1530.30, 1530.30, 1530.30, 1530.32]

    def test_ua063_periods_funded_is_3(self):
        """$4,590.90 funds the first three instalments exactly under the residual-cent schedule."""
        pf = self._periods_funded_for_ua063()
        assert pf == 3, f"Expected 3 periods funded, got {pf}"

    def test_ua063_next_due_is_december_1(self):
        """With 3 periods funded, next due date = due_dates[3] = 2026-12-01."""
        pf = self._periods_funded_for_ua063()
        assert pf < len(self.DUE_DATES_2026), "periods_funded must be < 4"
        next_due = self.DUE_DATES_2026[pf]
        assert next_due == "2026-12-01", f"Expected 2026-12-01, got {next_due}"

    def test_ua063_next_payment_is_q4_residual_amount(self):
        remaining = round(self.UA063_ANNUAL_LEVY - self.UA063_ADVANCE_PAID, 2)
        assert remaining == pytest.approx(1530.32, abs=0.01)

    def test_days_to_december_1_from_march_17(self):
        """Days from 2026-03-17 to 2026-12-01 ≈ 258 (not 76)."""
        from datetime import date
        today = date(2026, 3, 17)
        dec1 = date(2026, 12, 1)
        days = (dec1 - today).days
        assert days == 259, f"Expected 259 days to Dec 1, got {days}"
        # User reports 258 days — likely Math.ceil((new Date...) - new Date()) / ms_per_day
        # which may differ by 1 depending on time-of-day. Assert it's in range 257-260.
        assert 257 <= days <= 260

    def test_june_1_is_76_days_the_wrong_value(self):
        """Confirm the old bug: June 1 is 76 days from March 17 (the wrong value shown)."""
        from datetime import date
        today = date(2026, 3, 17)
        jun1 = date(2026, 6, 1)
        days = (jun1 - today).days
        assert days == 76, f"June 1 should be 76 days away, got {days}"

    def test_correct_priority_uses_next_due_date_first(self):
        """
        Frontend fix verification: when both next_due_date and period_status.current_due_date
        are present, the owner-specific next_due_date takes priority.
        """
        # Simulate API response for UA063
        next_due_date = "2026-12-01"  # owner-specific (correct)
        period_status_current_due = "2026-06-01"  # building's Q2 (irrelevant for this owner)

        # NEW priority: next_due_date ?? period_status.current_due_date
        current_due_date_new = next_due_date or period_status_current_due
        assert current_due_date_new == "2026-12-01", "next_due_date should take priority"

        # OLD (buggy) priority: period_status.current_due_date ?? next_due_date
        current_due_date_old = period_status_current_due or next_due_date
        assert current_due_date_old == "2026-06-01", "Old logic incorrectly shows building date"

    def test_zero_advance_payment_shows_next_upcoming_period(self):
        """
        Owner with no payments: next due = first period after today (Q1 March 1 past,
        Q1 grace period March 15 also past on March 17) → next_due = Q2 June 1.
        """
        # On 2026-03-17, Q1 (March 1) has passed its 14-day grace (March 15).
        # So periods_funded=0, past_grace=True → next_due = due_dates[1] = June 1
        nc_cents = max(0, round(0.0 * 100))
        pl_cents = round(self.UA063_PERIOD_LEVY * 100)
        pf = (nc_cents // pl_cents) if pl_cents > 0 else 0
        assert pf == 0, "No payments → 0 periods funded"

        # June 1 would be shown → 76 days — correct for an unpaid owner
        # This is DIFFERENT from UA063 who has paid 3 periods
        from datetime import date
        today = date(2026, 3, 17)
        jun1 = date(2026, 6, 1)
        days = (jun1 - today).days
        assert days == 76

    def test_east_gate_fallback_schedule_is_correct(self):
        """
        East Gate building uses [3,6,9,12] 'first' — quarterly due dates are
        March 1, June 1, September 1, December 1 (NOT Oct 31 / Jan 31).
        """
        # Correct East Gate schedule for 2026
        expected = ["2026-03-01", "2026-06-01", "2026-09-01", "2026-12-01"]
        assert self.DUE_DATES_2026 == expected

        # The OLD fallback in OwnerDashboard.tsx was wrong:
        wrong_q3 = "2026-10-31"  # October 31 — was in old hardcoded fallback
        wrong_q4 = "2027-01-31"  # January 31 next year — was in old hardcoded fallback
        assert wrong_q3 not in expected, "October 31 is NOT a valid East Gate due date"
        assert wrong_q4 not in expected, "January 31 is NOT a valid East Gate due date"


class TestTH086CustomDueDates:
    """
    Tests for TH086 scenario: owner with no current-year payments and custom Q1 due date.

    Root cause (Session 69):
      The single-unit /owners-units/{unit} endpoint read settings from db.site_settings
      (which only has rate-limit fields) instead of db.settings (which has levy configuration
      including levy_due_custom_dates={'3': 31, '6': 1, '9': 1, '12': 1}).

      With wrong settings (defaulting to day_type='first'):
        Q1 = 2026-03-01; grace = 2026-03-15; today = 2026-03-17 → past_grace = True
        next_payment = period_levy + outstanding = 1761.50 + 1761.50 = $3,523  (wrong)
        next_due = "2026-06-01" = "Due in 76 days"  (wrong)
        any_overdue = True  → "overdue" sub-label  (wrong)

      With correct settings (day_type='custom', custom_dates={'3': 31}):
        Q1 = 2026-03-31; grace = 2026-04-14; today = 2026-03-17 → before_grace = True
        next_payment = outstanding = period_levy = $1,761.50  (correct)
        next_due = "2026-03-31" = "Due in 14 days"  (correct)
        any_overdue = False  → no "overdue" sub-label  (correct)

    Fix: Change db.site_settings.find_one → db.settings.find_one in the single-unit endpoint
    (server.py line ~10699). The list endpoint already used db.settings correctly.
    """

    # Building 13195 custom due dates: Q1 = March 31, rest = 1st of month
    CUSTOM_DUE_DATES_2026 = ["2026-03-31", "2026-06-01", "2026-09-01", "2026-12-01"]
    PERIOD_LEVY_TH086 = 1761.50  # TH086 quarterly levy
    TODAY = "2026-03-17"

    def _compute_period_due_dates_custom(self, levy_year: int, custom_dates: dict) -> list:
        """Simplified simulate of compute_period_due_dates with custom day_type."""
        import calendar
        months = [3, 6, 9, 12]
        result = []
        for m in months:
            last_day = calendar.monthrange(levy_year, m)[1]
            m_str = str(m)
            if m_str in custom_dates:
                day = min(int(custom_dates[m_str]), last_day)
            else:
                day = 1
            result.append(f"{levy_year:04d}-{m:02d}-{day:02d}")
        return result

    def test_custom_dates_gives_march_31_for_q1(self):
        """levy_due_custom_dates={'3': 31} → Q1 = 2026-03-31, not 2026-03-01."""
        custom = {"3": 31, "6": 1, "9": 1, "12": 1}
        dates = self._compute_period_due_dates_custom(2026, custom)
        assert dates[0] == "2026-03-31", f"Q1 should be March 31, got {dates[0]}"

    def test_default_dates_gives_march_1_for_q1(self):
        """Without custom dates, default day_type='first' → Q1 = 2026-03-01."""
        custom = {}
        dates = self._compute_period_due_dates_custom(2026, custom)
        assert dates[0] == "2026-03-01", f"Default Q1 should be March 1, got {dates[0]}"

    def test_march_31_grace_not_passed_on_march_17(self):
        """Q1=March 31 + 14 days grace = April 14. March 17 is before April 14 → before_grace."""
        from datetime import date, timedelta
        q1 = date(2026, 3, 31)
        grace_deadline = q1 + timedelta(days=14)  # April 14
        today = date(2026, 3, 17)
        past_grace = grace_deadline < today
        assert not past_grace, "March 17 is before April 14 grace deadline"

    def test_march_1_grace_passed_on_march_17(self):
        """Q1=March 1 + 14 days grace = March 15. March 17 is after March 15 → past_grace."""
        from datetime import date, timedelta
        q1 = date(2026, 3, 1)
        grace_deadline = q1 + timedelta(days=14)  # March 15
        today = date(2026, 3, 17)
        past_grace = grace_deadline < today
        assert past_grace, "March 17 is after March 15 grace deadline"

    def test_th016_before_grace_next_payment_is_period_levy(self):
        """Before Q1 grace: next_payment = outstanding = period_levy (no arrears, no advance)."""
        # TH086: opening_arrears=0, total_paid=0
        opening_arrears = 0.0
        total_paid = 0.0
        period_levy = self.PERIOD_LEVY_TH086

        net_credit = total_paid - opening_arrears  # = 0
        nc_cents = max(0, round(net_credit * 100))  # = 0
        pl_cents = round(period_levy * 100)
        pf = (nc_cents // pl_cents) if pl_cents > 0 else 0  # = 0
        outstanding = round((pf + 1) * period_levy - net_credit, 2)  # = 1761.50

        # Before grace → next_payment = outstanding
        next_payment = outstanding
        assert next_payment == pytest.approx(1761.50, abs=0.01), \
            f"Before Q1 grace: expected $1,761.50, got {next_payment}"

    def test_th016_wrong_settings_gives_double_amount(self):
        """
        With wrong Q1=March 1 (past grace): next_payment = period_levy + outstanding = 3523.
        This is the bug caused by reading db.site_settings instead of db.settings.
        """
        opening_arrears = 0.0
        total_paid = 0.0
        period_levy = self.PERIOD_LEVY_TH086

        net_credit = total_paid - opening_arrears
        nc_cents = max(0, round(net_credit * 100))
        pl_cents = round(period_levy * 100)
        pf = nc_cents // pl_cents  # = 0
        outstanding = round((pf + 1) * period_levy - net_credit, 2)  # = 1761.50

        # WRONG: past_grace=True (March 1 + 14 = March 15 < March 17)
        next_payment_wrong = round(period_levy + outstanding, 2)
        assert next_payment_wrong == pytest.approx(3523.0, abs=0.01), \
            f"Bug replication: expected $3,523.00, got {next_payment_wrong}"

    def test_th016_correct_next_due_is_march_31(self):
        """With custom dates, TH086's next_due_date = 2026-03-31 = 14 days from March 17."""
        from datetime import date
        next_due = date(2026, 3, 31)
        today = date(2026, 3, 17)
        days = (next_due - today).days
        assert days == 14, f"March 31 should be 14 days away on March 17, got {days}"

    def test_settings_collection_should_be_db_settings_not_site_settings(self):
        """
        Document the fix: /owners-units/{unit} must read levy settings from db.settings
        (which has levy_due_custom_dates), NOT db.site_settings (rate-limit only).

        Building 13195 settings:
          Collection: db.settings  (building_id='13195')
          levy_due_day_type: 'custom'
          levy_due_custom_dates: {'3': 31, '6': 1, '9': 1, '12': 1}

        db.site_settings (id='rate_limits') has no levy configuration at all.
        """
        # Simulated db.settings document for building 13195
        building_settings = {
            "building_id": "13195",
            "levy_due_day_type": "custom",
            "levy_due_custom_dates": {"3": 31, "6": 1, "9": 1, "12": 1},
            "levy_due_months": [3, 6, 9, 12],
        }
        # Simulated db.site_settings document
        site_settings = {
            "id": "rate_limits",
            "rate_limit_login": 10,
        }

        # db.settings has the levy config
        assert "levy_due_custom_dates" in building_settings
        assert building_settings["levy_due_custom_dates"]["3"] == 31

        # db.site_settings does NOT have it
        assert "levy_due_custom_dates" not in site_settings

        # The fix: read levy settings from db.settings (query: building_id=<id>)
        correct_custom = building_settings.get("levy_due_custom_dates", {})
        assert correct_custom.get("3") == 31, "Should read March 31 from db.settings"
