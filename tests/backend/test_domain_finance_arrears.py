"""
Tests for backend/domain/finance/formulas/arrears.py.

Pure formula tests — no DB mocking needed. 2026-08-03: quarter_true_arrears()
and recoverable_arrears() (extracted 2026-07-21) were retired and replaced by
a single canonical unit_arrears_and_credit() — both older formulas
independently reconstructed a unit's obligation from an opening/prior-period
figure plus a separately-derived period levy, rather than trusting
unit_levy_ledger.net_balance directly. That defect produced a real UA042
inflation ($963.31 -> $2,768, get_arrears_board) and, independently, East
Gate's (13195) live "31 units in arrears / $1,469.49 True Arrears" bug (true
figures: 14 units / $11,359.73). See the module docstring for the full
account and CLAUDE.md's "Arrears Are a Per-Unit Obligation" rule.
"""
from domain.finance.formulas.arrears import unit_arrears_and_credit


class TestUnitArrearsAndCredit:
    def test_positive_balance_is_arrears(self):
        arrears, credit = unit_arrears_and_credit(net_balance_cents=50000)
        assert arrears == 50000
        assert credit == 0

    def test_negative_balance_is_credit_never_arrears(self):
        """An overpaying unit is in credit, not arrears — and the credit is
        returned as its own positive magnitude for display, never coerced to
        zero or subtracted from any other unit's arrears."""
        arrears, credit = unit_arrears_and_credit(net_balance_cents=-64496)
        assert arrears == 0
        assert credit == 64496

    def test_zero_balance_is_neither_arrears_nor_credit(self):
        arrears, credit = unit_arrears_and_credit(net_balance_cents=0)
        assert arrears == 0
        assert credit == 0

    def test_in_grace_portion_excluded_from_arrears(self):
        """A unit owing $500 where $300 of that is the still-not-yet-overdue
        current period only has $200 in genuine arrears."""
        arrears, credit = unit_arrears_and_credit(
            net_balance_cents=50000, in_grace_portion_cents=30000,
        )
        assert arrears == 20000
        assert credit == 0

    def test_in_grace_portion_capped_at_net_balance(self):
        """clamp_non_negative must never let arrears go negative even if the
        in-grace portion is (incorrectly) computed larger than the balance."""
        arrears, credit = unit_arrears_and_credit(
            net_balance_cents=20000, in_grace_portion_cents=30000,
        )
        assert arrears == 0
        assert credit == 0

    def test_no_reconstruction_from_opening_balance(self):
        """Regression guard for the UA042/East-Gate-31-units incident class:
        this function's signature only accepts a net_balance and an
        in-grace-portion — there is no opening_debt or period_levy parameter
        to reconstruct an obligation from, which is the actual fix."""
        import inspect
        params = list(inspect.signature(unit_arrears_and_credit).parameters)
        assert params == ["net_balance_cents", "in_grace_portion_cents"]

    def test_reproduces_live_east_gate_th074(self):
        """Live-verified 2026-08-03 against East Gate (13195) unit TH074 post
        data-repair: net_balance $1,647.45, no in-grace period active
        (today's overdue periods = Q1+Q2, none in grace) -> full balance is
        arrears, matching the unit's own stored reconciliation_target_balance
        and the live Civium Strata portal figure ($1,648.30, small timing
        variance)."""
        arrears, credit = unit_arrears_and_credit(net_balance_cents=164745)
        assert arrears == 164745
        assert credit == 0
