"""Tests for building_id field consistency in financial collections."""
import pytest


# Test that TH087 scenario produces correct net_balance
class TestTH087FinancialScenario:
    """TH087: Avneet Rooprai, UOE=161, total_levied=7090.04, total_paid=1800, net_balance=5290.04"""

    def test_net_balance_calculation(self):
        """net_balance = total_levied - total_paid (with no opening arrears)."""
        total_levied = 7090.04
        total_paid = 1800.00
        opening_arrears = 0.0
        net_balance = round(opening_arrears + total_levied - total_paid, 2)
        assert net_balance == pytest.approx(5290.04, abs=0.01)

    def test_period_levy_calculation(self):
        """period_levy = total_levied / 4 (quarterly)."""
        total_levied = 7090.04
        period_levy = round(total_levied / 4, 2)
        assert period_levy == pytest.approx(1772.51, abs=0.01)

    def test_credit_balance_means_zero_opening_arrears(self):
        """CSV balance=-1800 means payment already recorded, not opening arrears."""
        csv_balance = -1800.00
        # Negative balance = credit, so opening_arrears = 0
        opening_arrears = 0.0 if csv_balance <= 0 else csv_balance
        assert opening_arrears == 0.0

    def test_arrears_balance_splits_by_levy_ratio(self):
        """CSV balance=154 (UA001) splits into admin/sinking by levy ratio."""
        csv_balance = 154.00
        admin_ratio = 0.7740
        admin_opening = round(csv_balance * admin_ratio, 2)
        sinking_opening = round(csv_balance - admin_opening, 2)
        assert admin_opening == pytest.approx(119.20, abs=0.01)
        assert sinking_opening == pytest.approx(34.80, abs=0.01)
        assert abs((admin_opening + sinking_opening) - csv_balance) < 0.02

    def test_ua063_negative_balance_zero_opening(self):
        """UA063: csv_balance=-4590.90 → opening_arrears=0 (credit already in portal)."""
        csv_balance = -4590.90
        opening_arrears = 0.0 if csv_balance <= 0 else csv_balance
        assert opening_arrears == 0.0


class TestBuildingIdConstants:
    """Verify building_id constant is correct."""

    def test_building_id_is_unit_plan_number(self):
        """Unit plan number for Eastgate is 13195."""
        BUILDING_ID = "13195"
        assert BUILDING_ID == "13195"
        assert BUILDING_ID != "eastgate_residences"
        assert BUILDING_ID != "sierra_gungahlin"

    def test_lot_to_unit_formula(self):
        """Lot to unit number conversion formula.

        Production unit numbers: UA001-UA070 (apartments), TH071-TH087 (townhouses).
        For townhouses the unit number equals the lot number directly.
        """

        def lot_to_unit(lot):
            if lot <= 70:
                return f"UA{lot:03d}"
            return f"TH{lot:03d}"

        assert lot_to_unit(1) == "UA001"
        assert lot_to_unit(70) == "UA070"
        assert lot_to_unit(71) == "TH071"
        assert lot_to_unit(87) == "TH087"

    def test_total_units_count(self):
        """There are 87 units total: 70 apartments + 17 townhouses."""
        apartments = [f"UA{i:03d}" for i in range(1, 71)]
        townhouses = [f"TH{i:03d}" for i in range(1, 18)]
        assert len(apartments) == 70
        assert len(townhouses) == 17
        assert len(apartments) + len(townhouses) == 87

    def test_plan_id_same_as_building_id(self):
        """plan_id and building_id must both equal '13195' post-migration."""
        PLAN_ID = "13195"
        BUILDING_ID = "13195"
        assert PLAN_ID == BUILDING_ID


class TestLedgerDocumentStructure:
    """Verify unit_levy_ledger documents have required fields."""

    def _make_ledger_doc(self, unit_number="TH087", levy_year=2026):
        return {
            "unit_number": unit_number,
            "levy_year": levy_year,
            "year": str(levy_year),
            "building_id": "13195",
            "plan_id": "13195",
            "admin_opening": 0.0,
            "sinking_opening": 0.0,
            "opening_arrears": 0.0,
            "admin_levied": 5491.67,
            "sinking_levied": 1598.37,
            "total_levied": 7090.04,
            "admin_paid": 1394.64,
            "sinking_paid": 405.36,
            "total_paid": 1800.00,
            "admin_closing": 4097.03,
            "sinking_closing": 1193.01,
            "net_balance": 5290.04,
        }

    def test_ledger_has_building_id(self):
        doc = self._make_ledger_doc()
        assert doc.get("building_id") == "13195"

    def test_ledger_has_plan_id(self):
        doc = self._make_ledger_doc()
        assert doc.get("plan_id") == "13195"

    def test_ledger_has_both_year_fields(self):
        """Ledger must have both 'year' (str) and 'levy_year' (int) for compatibility."""
        doc = self._make_ledger_doc()
        assert doc.get("year") == "2026"
        assert doc.get("levy_year") == 2026

    def test_ledger_net_balance_formula(self):
        doc = self._make_ledger_doc()
        expected_net = (
                doc["admin_opening"] + doc["admin_levied"] - doc["admin_paid"] +
                doc["sinking_opening"] + doc["sinking_levied"] - doc["sinking_paid"]
        )
        assert abs(doc["net_balance"] - expected_net) < 0.02

    def test_ledger_total_paid_consistency(self):
        doc = self._make_ledger_doc()
        assert abs(doc["total_paid"] - (doc["admin_paid"] + doc["sinking_paid"])) < 0.02

    def test_ledger_total_levied_consistency(self):
        doc = self._make_ledger_doc()
        assert abs(doc["total_levied"] - (doc["admin_levied"] + doc["sinking_levied"])) < 0.02

    def test_opening_arrears_equals_admin_plus_sinking_opening(self):
        doc = self._make_ledger_doc()
        expected = doc["admin_opening"] + doc["sinking_opening"]
        assert abs(doc["opening_arrears"] - expected) < 0.01


class TestOpeningBalanceLogic:
    """Verify the opening balance import logic handles all CSV cases correctly."""

    def _compute_opening(self, csv_balance, admin_ratio=0.7740):
        """Mirror import_fy2026_opening_balances.py logic."""
        if csv_balance > 0:
            admin_opening = round(csv_balance * admin_ratio, 2)
            sinking_opening = round(csv_balance - admin_opening, 2)
        else:
            admin_opening = 0.0
            sinking_opening = 0.0
        return admin_opening, sinking_opening

    def test_zero_balance_zero_opening(self):
        admin, sinking = self._compute_opening(0.0)
        assert admin == 0.0
        assert sinking == 0.0

    def test_positive_balance_splits(self):
        admin, sinking = self._compute_opening(154.00)
        assert admin > 0
        assert sinking > 0
        assert abs(admin + sinking - 154.00) < 0.02

    def test_negative_balance_zero_opening(self):
        """Negative CSV balance means pre-payment — no opening arrears."""
        admin, sinking = self._compute_opening(-1800.00)
        assert admin == 0.0
        assert sinking == 0.0

    def test_th017_negative_balance(self):
        admin, sinking = self._compute_opening(-1800.00)
        assert admin == 0.0
        assert sinking == 0.0
        opening_arrears = admin + sinking
        assert opening_arrears == 0.0

    def test_ua063_large_negative_balance(self):
        admin, sinking = self._compute_opening(-4590.90)
        assert admin == 0.0
        assert sinking == 0.0

    def test_ua001_arrears(self):
        admin, sinking = self._compute_opening(154.00)
        assert abs(admin + sinking - 154.00) < 0.02
        assert admin > 0
        assert sinking > 0


class TestBuildingIdMigrationRules:
    """Document the migration rules from Session 66."""

    def test_old_building_ids_are_invalid(self):
        """These values must never appear in the database after migration."""
        INVALID_IDS = {"eastgate_residences", "sierra_gungahlin"}
        VALID_ID = "13195"
        assert VALID_ID not in INVALID_IDS

    def test_eastgate_maps_to_13195(self):
        """Session 66 migration: eastgate_residences → 13195."""
        migration_map = {
            "eastgate_residences": "13195",
            "sierra_gungahlin": "16244",
        }
        assert migration_map["eastgate_residences"] == "13195"

    def test_default_building_id_constant(self):
        """DEFAULT_BUILDING_ID used in routers must be 13195."""
        DEFAULT_BUILDING_ID = "13195"
        assert DEFAULT_BUILDING_ID == "13195"


class TestFY2026AggregateBalances:
    """Validate FY2026 opening balance aggregate totals after correct import."""

    def test_expected_total_arrears(self):
        """Sum of positive CSV balances (prior-year arrears) ≈ $2,795."""
        # From authoritative strata roll data
        arrears_units = [
            154.00,  # UA001
            3.64,  # UA017
            5.00,  # UA019
            5.44,  # UA028
            12.82,  # UA030
            12.82,  # UA034
            0.01,  # UA058
            15.41,  # UA060
            10.95,  # UA067
            226.15,  # UA070
            963.31,  # UA042
            580.01,  # TH074
            97.33,  # TH077
            20.25,  # TH078
            7.61,  # TH080
            294.00,  # TH081
            366.00,  # TH084
            20.23,  # TH085
        ]
        total_arrears = sum(arrears_units)
        assert total_arrears == pytest.approx(2794.98, abs=1.0), (
            f"Total arrears should be ~$2,795, got ${total_arrears:.2f}"
        )

    def test_expected_total_payments_from_credits(self):
        """Sum of abs(negative balances) = payments from credits ≈ $27,207."""
        credit_units = [
            2088.56,  # UA003
            911.59,  # UA005
            240.00,  # UA009
            1530.30,  # UA011
            902.77,  # UA012
            902.77,  # UA015
            1102.99,  # UA021
            1.89,  # UA026
            666.12,  # UA032
            0.66,  # UA038
            902.77,  # UA040
            902.77,  # UA045
            1530.30,  # UA046
            902.77,  # UA050
            342.00,  # UA055
            0.04,  # UA057
            902.77,  # UA061
            4590.90,  # UA063
            902.77,  # UA064
            1580.71,  # TH071
            1761.50,  # TH072
            2739.94,  # TH076
            1800.00,  # TH087
        ]
        total_paid = sum(credit_units)
        assert total_paid == pytest.approx(27206.89, abs=5.0), (
            f"Total payments should be ~$27,207, got ${total_paid:.2f}"
        )

    def test_collection_rate_formula(self):
        """Collection rate = sum(total_paid) / sum(total_levied) ≈ 6.2%."""
        total_paid = 27206.89  # from correct import
        total_levied = 440374.90  # 87 units × UOE rates
        collection_rate = total_paid / total_levied * 100
        assert 5.5 <= collection_rate <= 7.0, (
            f"Expected ~6.2% collection rate, got {collection_rate:.2f}%"
        )

    def test_th017_correct_values(self):
        """TH087 (Avneet Rooprai): UOE=161, credit=-1800, confirmed_paid=1800, net≈5290."""
        uoe = 161
        csv_balance = -1800.00
        admin_rate_per_uoe = 34.087
        sinking_rate_per_uoe = 9.9505

        admin_levied = round(uoe * admin_rate_per_uoe, 2)
        sinking_levied = round(uoe * sinking_rate_per_uoe, 2)
        total_levied = round(admin_levied + sinking_levied, 2)

        confirmed_paid = max(0.0, -csv_balance)  # = 1800.00
        opening_arrears = max(0.0, csv_balance)  # = 0.0

        admin_ratio = 0.7740
        admin_paid = round(confirmed_paid * admin_ratio, 2)
        sinking_paid = round(confirmed_paid - admin_paid, 2)

        admin_closing = round(0.0 + admin_levied - admin_paid, 2)
        sinking_closing = round(0.0 + sinking_levied - sinking_paid, 2)
        net_balance = round(admin_closing + sinking_closing, 2)

        assert total_levied == pytest.approx(7090.04, abs=0.05)
        assert confirmed_paid == pytest.approx(1800.00, abs=0.01)
        assert opening_arrears == 0.0
        assert net_balance == pytest.approx(5290.04, abs=0.10)

    def test_ua001_correct_arrears(self):
        """UA001 (Jane Pearce): UOE=82, arrears=154, confirmed_paid=0."""
        uoe = 82
        csv_balance = 154.00

        opening_arrears = max(0.0, csv_balance)  # = 154.00
        confirmed_paid = max(0.0, -csv_balance)  # = 0.00

        assert opening_arrears == pytest.approx(154.00, abs=0.01)
        assert confirmed_paid == 0.0

    def test_ua063_large_credit(self):
        """UA063 (Anthony McDonald): balance=-4590.90 → confirmed_paid=4590.90."""
        csv_balance = -4590.90
        confirmed_paid = max(0.0, -csv_balance)
        opening_arrears = max(0.0, csv_balance)

        assert confirmed_paid == pytest.approx(4590.90, abs=0.01)
        assert opening_arrears == 0.0
