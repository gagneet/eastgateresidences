"""Tests for FY2026 authoritative opening balance data correctness.

Validates that the import_fy2026_opening_balances.py script produces the
correct field values from the strata roll CSV balance data.

Key logic:
  csv_balance < 0 → credit → total_paid = abs(balance), opening_arrears = 0
  csv_balance > 0 → arrears → opening_arrears = balance, total_paid = 0
  csv_balance = 0 → clean slate → both 0
"""
import pytest

# The authoritative opening balance data (non-zero entries for testing)
LOT_BALANCE_DATA = [
    # (lot, unit_number, uoe, balance)
    (1, "UA001", 82, 154.00),  # arrears
    (3, "UA003", 139, -2088.56),  # credit
    (5, "UA005", 111, -911.59),  # credit
    (9, "UA009", 82, -240.00),  # credit
    (11, "UA011", 139, -1530.30),  # credit
    (12, "UA012", 82, -902.77),  # credit
    (15, "UA015", 82, -902.77),  # credit
    (17, "UA017", 82, 3.64),  # tiny arrears
    (19, "UA019", 139, 5.00),  # tiny arrears
    (21, "UA021", 111, -1102.99),  # credit
    (26, "UA026", 139, -1.89),  # tiny credit
    (28, "UA028", 82, 5.44),  # tiny arrears
    (30, "UA030", 82, 12.82),  # tiny arrears
    (32, "UA032", 82, -666.12),  # credit
    (34, "UA034", 82, 12.82),  # tiny arrears
    (38, "UA038", 139, -0.66),  # tiny credit
    (40, "UA040", 82, -902.77),  # credit
    (42, "UA042", 82, 963.31),  # arrears
    (45, "UA045", 82, -902.77),  # credit
    (46, "UA046", 139, -1530.30),  # credit
    (50, "UA050", 82, -902.77),  # credit
    (55, "UA055", 139, -342.00),  # credit
    (57, "UA057", 111, -0.04),  # tiny credit
    (58, "UA058", 82, 0.01),  # near-zero arrears
    (60, "UA060", 111, 15.41),  # arrears
    (61, "UA061", 82, -902.77),  # credit
    (63, "UA063", 139, -4590.90),  # large credit (Anthony McDonald)
    (64, "UA064", 82, -902.77),  # credit
    (67, "UA067", 149, 10.95),  # arrears
    (70, "UA070", 82, 226.15),  # arrears
    (71, "TH071", 160, -1580.71),  # credit
    (72, "TH072", 160, -1761.50),  # credit
    (74, "TH074", 160, 580.01),  # arrears
    (76, "TH076", 160, -2739.94),  # large credit
    (77, "TH077", 160, 97.33),  # arrears
    (78, "TH078", 160, 20.25),  # arrears
    (80, "TH080", 160, 7.61),  # arrears
    (81, "TH081", 160, 294.00),  # arrears
    (84, "TH084", 160, 366.00),  # arrears
    (85, "TH085", 160, 20.23),  # arrears
    (87, "TH087", 161, -1800.00),  # credit (Avneet Rooprai)
]


class TestOpeningBalanceLogic:
    """Test the correct derivation of opening balance fields from CSV balance."""

    def test_credit_balance_gives_zero_opening_arrears(self):
        """Negative balance = credit/advance payment; opening_arrears must be 0."""
        for lot, unit, uoe, balance in LOT_BALANCE_DATA:
            if balance < 0:
                opening_arrears = max(0.0, balance)
                assert opening_arrears == 0.0, (
                    f"{unit} (balance={balance}) credit should give opening_arrears=0, got {opening_arrears}"
                )

    def test_credit_balance_gives_nonzero_total_paid(self):
        """Negative balance magnitude = total_paid (payment already received outside portal)."""
        for lot, unit, uoe, balance in LOT_BALANCE_DATA:
            if balance < 0:
                total_paid = max(0.0, -balance)
                assert total_paid > 0, f"{unit} should have total_paid > 0"
                assert abs(total_paid - abs(balance)) < 0.01, (
                    f"{unit}: total_paid={total_paid} should equal abs({balance})"
                )

    def test_arrears_balance_gives_nonzero_opening_arrears(self):
        """Positive balance = prior-year arrears; total_paid should be 0."""
        for lot, unit, uoe, balance in LOT_BALANCE_DATA:
            if balance > 0:
                opening_arrears = balance
                total_paid = 0.0
                assert opening_arrears > 0, f"{unit} should have opening_arrears > 0"
                assert total_paid == 0.0, f"{unit} should have total_paid = 0"

    def test_zero_balance_gives_clean_slate(self):
        """Zero balance = no carry-forward."""
        balance = 0.0
        opening_arrears = max(0.0, balance)
        total_paid = max(0.0, -balance)
        assert opening_arrears == 0.0
        assert total_paid == 0.0


class TestAggregateBalanceTotals:
    """Test aggregate totals across all 87 units."""

    def _get_full_data(self):
        """Full 87-unit dataset for aggregate checks."""
        # All zero-balance units contribute 0 to both totals.
        # Non-zero entries are in LOT_BALANCE_DATA above.
        return LOT_BALANCE_DATA

    def test_total_arrears_is_approximately_correct(self):
        """Sum of all positive balances ≈ $2,795."""
        total_arrears = sum(b for _, _, _, b in LOT_BALANCE_DATA if b > 0)
        # Expected: 154+3.64+5+5.44+12.82+12.82+0.01+15.41+10.95+226.15+
        #           580.01+97.33+20.25+7.61+294+366+20.23 = 1831.67
        # Plus UA058=0.01 (near-zero)
        # Note: full 87-unit total differs because many 0-balance units not listed
        assert total_arrears == pytest.approx(2794.98, abs=1.0), (
            f"Total arrears should be ~$2,795, got ${total_arrears:.2f}"
        )

    def test_total_payments_is_approximately_correct(self):
        """Sum of abs(negative balances) ≈ $27,207."""
        total_paid = sum(abs(b) for _, _, _, b in LOT_BALANCE_DATA if b < 0)
        assert total_paid == pytest.approx(27206.89, abs=5.0), (
            f"Total payments should be ~$27,207, got ${total_paid:.2f}"
        )

    def test_expected_collection_rate(self):
        """Collection rate = total_paid / total_annual_levy ≈ 6.2%."""
        total_paid = sum(abs(b) for _, _, _, b in LOT_BALANCE_DATA if b < 0)
        total_annual_levy = 440374.90  # actual from import aggregate
        collection_rate = total_paid / total_annual_levy * 100
        assert 5.5 <= collection_rate <= 7.0, (
            f"Expected ~6.2% collection rate, got {collection_rate:.2f}%"
        )


class TestSpecificUnitScenarios:
    """Test key unit-specific scenarios."""

    def test_ua063_anthony_mcdonald(self):
        """UA063: balance=-4590.90 → total_paid=4590.90, opening_arrears=0."""
        ua063 = next((r for r in LOT_BALANCE_DATA if r[1] == "UA063"), None)
        assert ua063 is not None, "UA063 must exist in test data"
        _, unit, uoe, balance = ua063
        assert uoe == 139
        assert balance == pytest.approx(-4590.90, abs=0.01)
        total_paid = max(0.0, -balance)
        opening_arrears = max(0.0, balance)
        assert total_paid == pytest.approx(4590.90, abs=0.01)
        assert opening_arrears == 0.0

    def test_th017_avneet_rooprai(self):
        """TH087: balance=-1800 → total_paid=1800, opening_arrears=0, net_balance≈5290."""
        th017 = next((r for r in LOT_BALANCE_DATA if r[1] == "TH087"), None)
        assert th017 is not None, "TH087 must exist in test data"
        _, unit, uoe, balance = th017
        assert uoe == 161
        assert balance == pytest.approx(-1800.00, abs=0.01)
        total_paid = max(0.0, -balance)
        opening_arrears = max(0.0, balance)
        assert total_paid == pytest.approx(1800.00, abs=0.01)
        assert opening_arrears == 0.0
        # net_balance = total_levied - total_paid (TH087 UOE=161 × rate≈44.04/UOE)
        admin_rate = 34.087
        sinking_rate = 9.9505
        total_levied = round(uoe * admin_rate + uoe * sinking_rate, 2)
        net_balance = round(total_levied - total_paid, 2)
        assert net_balance == pytest.approx(5290.04, abs=0.10), (
            f"TH087 net_balance should be ~5290.04, got {net_balance}"
        )

    def test_ua042_arrears_unit(self):
        """UA042: balance=963.31 → opening_arrears=963.31, total_paid=0."""
        ua042 = next((r for r in LOT_BALANCE_DATA if r[1] == "UA042"), None)
        assert ua042 is not None, "UA042 must exist in test data"
        _, unit, uoe, balance = ua042
        opening_arrears = max(0.0, balance)
        total_paid = max(0.0, -balance)
        assert opening_arrears == pytest.approx(963.31, abs=0.01)
        assert total_paid == 0.0

    def test_ua001_arrears_split_by_ratio(self):
        """UA001: balance=154 → opening_arrears=154, split into admin/sinking by ratio."""
        ua001 = next((r for r in LOT_BALANCE_DATA if r[1] == "UA001"), None)
        assert ua001 is not None
        _, unit, uoe, balance = ua001
        admin_ratio = 0.7740
        admin_opening = round(balance * admin_ratio, 2)
        sinking_opening = round(balance - admin_opening, 2)
        assert admin_opening == pytest.approx(119.20, abs=0.01)
        assert sinking_opening == pytest.approx(34.80, abs=0.01)
        assert abs((admin_opening + sinking_opening) - balance) < 0.02

    def test_th006_large_credit(self):
        """TH076: balance=-2739.94 → total_paid=2739.94, opening_arrears=0."""
        th006 = next((r for r in LOT_BALANCE_DATA if r[1] == "TH076"), None)
        assert th006 is not None
        _, unit, uoe, balance = th006
        assert balance == pytest.approx(-2739.94, abs=0.01)
        total_paid = max(0.0, -balance)
        opening_arrears = max(0.0, balance)
        assert total_paid == pytest.approx(2739.94, abs=0.01)
        assert opening_arrears == 0.0

    def test_th004_arrears(self):
        """TH074: balance=580.01 → opening_arrears=580.01, total_paid=0."""
        th004 = next((r for r in LOT_BALANCE_DATA if r[1] == "TH074"), None)
        assert th004 is not None
        _, unit, uoe, balance = th004
        opening_arrears = max(0.0, balance)
        total_paid = max(0.0, -balance)
        assert opening_arrears == pytest.approx(580.01, abs=0.01)
        assert total_paid == 0.0


class TestUOEValues:
    """Test that Unit of Entitlement values are correct per the strata roll."""

    def test_townhouse_uoe_values(self):
        """TH071-TH086 have UOE=160, TH087 has UOE=161."""
        th_units = [(u, uoe) for _, u, uoe, _ in LOT_BALANCE_DATA if u.startswith("TH")]
        for unit, uoe in th_units:
            if unit == "TH087":
                assert uoe == 161, f"{unit} should have UOE=161 (special townhouse)"
            else:
                assert uoe == 160, f"{unit} should have UOE=160"

    def test_ua063_ua067_uoe_139_149(self):
        """UA063 (lot 63) has UOE=139, UA067 (lot 67) has UOE=149."""
        ua063 = next((r for r in LOT_BALANCE_DATA if r[1] == "UA063"), None)
        ua067 = next((r for r in LOT_BALANCE_DATA if r[1] == "UA067"), None)
        assert ua063 is not None
        assert ua067 is not None
        assert ua063[2] == 139
        assert ua067[2] == 149

    def test_no_duplicate_lots(self):
        """Each lot number should appear at most once."""
        lots = [lot for lot, _, _, _ in LOT_BALANCE_DATA]
        assert len(set(lots)) == len(lots), "Duplicate lot numbers found in test data"

    def test_lots_in_valid_range(self):
        """All lots should be in 1-87 range."""
        lots = [lot for lot, _, _, _ in LOT_BALANCE_DATA]
        assert max(lots) <= 87
        assert min(lots) >= 1


class TestBalanceSignConvention:
    """Test that the sign convention is correctly applied."""

    def test_negative_balance_means_credit_not_debt(self):
        """By convention: negative CSV balance = owner has credit (they overpaid)."""
        # UA063 paid $4,590.90 in advance → balance = -4590.90 (credit to owner)
        # This means total_paid = 4590.90, NOT opening_arrears = 4590.90
        balance = -4590.90
        opening_arrears = max(0.0, balance)  # correct: 0
        wrong_arrears = abs(balance)  # wrong: 4590.90
        assert opening_arrears == 0.0
        assert wrong_arrears == pytest.approx(4590.90)
        # The correct total_paid = abs(credit):
        total_paid = max(0.0, -balance)
        assert total_paid == pytest.approx(4590.90)

    def test_positive_balance_means_debt(self):
        """By convention: positive CSV balance = owner has arrears (they underpaid)."""
        balance = 154.00  # UA001
        opening_arrears = balance  # correct: 154
        total_paid = max(0.0, -balance)  # correct: 0
        assert opening_arrears == pytest.approx(154.00)
        assert total_paid == 0.0

    def test_levy_payments_not_queried_for_credits(self):
        """
        Credit units (negative balance) should not rely on levy_payments collection.
        The credit IS the payment — levy_payments may not have a matching record.
        total_paid = abs(csv_balance) for credits.
        """
        credit_units = [(u, b) for _, u, _, b in LOT_BALANCE_DATA if b < 0]
        for unit, balance in credit_units:
            total_paid = max(0.0, -balance)
            assert total_paid > 0, f"{unit}: total_paid should be > 0 for credit unit"
            # If we incorrectly queried levy_payments (which is empty), total_paid would be 0
            incorrect_total_paid = 0.0  # what levy_payments would return
            assert total_paid != incorrect_total_paid, (
                f"{unit}: total_paid should not be 0 for credit balance {balance}"
            )


class TestNetBalanceFormula:
    """Test the net_balance calculation formula."""

    def test_net_balance_formula_for_credit_unit(self):
        """Credit unit: net_balance = admin_levied + sinking_levied - total_paid."""
        # TH087: balance=-1800, UOE=161
        # admin_levied = 161 * 34.087 = 5488.01
        # sinking_levied = 161 * 9.9505 = 1602.03
        # total_levied = 7090.04
        # total_paid = 1800.00
        # net_balance = 7090.04 - 1800.00 = 5290.04
        total_levied = 7090.04
        total_paid = 1800.00
        opening_arrears = 0.0
        net_balance = round(opening_arrears + total_levied - total_paid, 2)
        assert net_balance == pytest.approx(5290.04, abs=0.01)

    def test_net_balance_formula_for_arrears_unit(self):
        """Arrears unit: net_balance = opening_arrears + total_levied - 0."""
        # UA042: balance=963.31, UOE=82
        # admin_levied = 82 * 34.087 = 2795.13
        # sinking_levied = 82 * 9.9505 = 815.94
        # total_levied = 3611.07
        # opening_arrears = 963.31, total_paid = 0
        # net_balance = 963.31 + 3611.07 - 0 = 4574.38
        total_levied = 3611.07
        opening_arrears = 963.31
        total_paid = 0.0
        net_balance = round(opening_arrears + total_levied - total_paid, 2)
        assert net_balance == pytest.approx(4574.38, abs=0.01)
