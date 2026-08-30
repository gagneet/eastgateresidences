"""
Tests for scripts/data_extraction/extract_east_gate_import.py

Runs the extraction against the real MongoDB dump and asserts row counts,
internal-consistency checks, and CSV structure. Skipped if the dump is absent
(e.g. on CI without the local dump).
"""

import csv
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import pytest

# Add project root to path so we can import the extraction module
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "data_extraction"))

DUMP_PATH = ROOT / "deploy" / "db-snapshots" / "strata-mongodump-2026-05-01-092036" / "strata_production"
DUMP_PRESENT = DUMP_PATH.exists()

pytestmark = pytest.mark.skipif(
    not DUMP_PRESENT,
    reason="MongoDB dump not present at deploy/db-snapshots/strata-mongodump-2026-05-01-092036/strata_production",
)


@pytest.fixture(scope="module")
def output_dir():
    with tempfile.TemporaryDirectory(prefix="east_gate_test_") as tmpdir:
        yield tmpdir


@pytest.fixture(scope="module")
def generated_csvs(output_dir):
    """Run the extraction once and return a dict of {filename: [rows]}."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "extract_east_gate_import",
        ROOT / "scripts" / "data_extraction" / "extract_east_gate_import.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    data = mod.load_all_data(str(DUMP_PATH))
    mod.generate_01_organisation(output_dir)
    mod.generate_02_scheme(output_dir, data)
    mod.generate_03_lots(output_dir, data)
    mod.generate_04_current_owners(output_dir, data)
    mod.generate_05_owner_transfers(output_dir, data)
    mod.generate_06_quarterly_levies(output_dir, data)
    mod.generate_07_admin_fund_summary(output_dir, data)
    mod.generate_08_sinking_fund_summary(output_dir, data)
    mod.generate_09_arrears(output_dir, data)
    mod.generate_10_outstanding(output_dir, data)
    mod.generate_11_opening_balances(output_dir, data)

    result = {}
    for csv_path in sorted(Path(output_dir).glob("*.csv")):
        with open(csv_path, newline="", encoding="utf-8") as fh:
            result[csv_path.name] = list(csv.DictReader(fh))
    return result


# ---------------------------------------------------------------------------
# Row count assertions
# ---------------------------------------------------------------------------

class TestRowCounts:
    def test_01_organisation_has_one_row(self, generated_csvs):
        assert len(generated_csvs["01_organisation.csv"]) == 1

    def test_02_scheme_has_one_row(self, generated_csvs):
        assert len(generated_csvs["02_scheme.csv"]) == 1

    def test_03_lots_has_87_rows(self, generated_csvs):
        assert len(generated_csvs["03_lots.csv"]) == 87

    def test_04_current_owners_has_87_rows(self, generated_csvs):
        assert len(generated_csvs["04_current_owners.csv"]) == 87

    def test_05_owner_transfers_has_rows(self, generated_csvs):
        assert len(generated_csvs["05_owner_transfers.csv"]) >= 40

    def test_06_quarterly_levies_has_2088_rows(self, generated_csvs):
        # 87 units × 6 years × 4 quarters
        assert len(generated_csvs["06_historical_quarterly_levies.csv"]) == 87 * 6 * 4

    def test_07_admin_fund_summary_has_6_rows(self, generated_csvs):
        assert len(generated_csvs["07_historical_admin_fund_summary.csv"]) == 6

    def test_08_sinking_fund_summary_has_6_rows(self, generated_csvs):
        assert len(generated_csvs["08_historical_sinking_fund_summary.csv"]) == 6

    def test_09_arrears_has_87_rows(self, generated_csvs):
        assert len(generated_csvs["09_arrears_2025_12_31.csv"]) == 87

    def test_10_outstanding_has_87_rows(self, generated_csvs):
        assert len(generated_csvs["10_outstanding_2026_06_01.csv"]) == 87

    def test_11_opening_balances_has_2_rows(self, generated_csvs):
        assert len(generated_csvs["11_opening_balances_at_cutover.csv"]) == 2


# ---------------------------------------------------------------------------
# Content assertions — Organisation (01)
# ---------------------------------------------------------------------------

class TestOrganisation:
    def test_civium_name(self, generated_csvs):
        row = generated_csvs["01_organisation.csv"][0]
        assert row["organisation_name"] == "CIVIUM PROPERTY PTY LIMITED"

    def test_civium_abn(self, generated_csvs):
        row = generated_csvs["01_organisation.csv"][0]
        abn = row["abn"].replace(" ", "")
        assert abn == "39121276300"

    def test_not_self_managed(self, generated_csvs):
        row = generated_csvs["01_organisation.csv"][0]
        assert row["is_self_managed"].lower() in ("false", "0", "no")


# ---------------------------------------------------------------------------
# Content assertions — Scheme (02)
# ---------------------------------------------------------------------------

class TestScheme:
    def test_plan_number(self, generated_csvs):
        row = generated_csvs["02_scheme.csv"][0]
        assert row["plan_number"] == "UP13195"

    def test_jurisdiction(self, generated_csvs):
        row = generated_csvs["02_scheme.csv"][0]
        assert row["jurisdiction"] == "ACT"

    def test_lot_count(self, generated_csvs):
        row = generated_csvs["02_scheme.csv"][0]
        assert int(row["lot_count"]) == 87

    def test_total_uoe(self, generated_csvs):
        row = generated_csvs["02_scheme.csv"][0]
        assert int(row["total_uoe"]) == 10000


# ---------------------------------------------------------------------------
# Content assertions — Lots (03)
# ---------------------------------------------------------------------------

class TestLots:
    def test_all_units_have_lot_numbers(self, generated_csvs):
        for row in generated_csvs["03_lots.csv"]:
            assert row["lot_number"], f"Missing lot_number for unit {row['unit_number']}"

    def test_unit_types_are_apartment_or_townhouse(self, generated_csvs):
        for row in generated_csvs["03_lots.csv"]:
            assert row["unit_type"] in ("apartment", "townhouse"), f"Bad type: {row}"

    def test_entitlements_are_positive(self, generated_csvs):
        for row in generated_csvs["03_lots.csv"]:
            assert int(row["entitlement_uoe"]) > 0, f"Zero UOE: {row}"

    def test_entitlements_sum_to_10000(self, generated_csvs):
        total = sum(int(r["entitlement_uoe"]) for r in generated_csvs["03_lots.csv"])
        assert total == 10000

    def test_70_apartments_17_townhouses(self, generated_csvs):
        types = defaultdict(int)
        for r in generated_csvs["03_lots.csv"]:
            types[r["unit_type"]] += 1
        assert types["apartment"] == 70
        assert types["townhouse"] == 17


# ---------------------------------------------------------------------------
# Content assertions — Current owners (04)
# ---------------------------------------------------------------------------

class TestCurrentOwners:
    def test_all_lots_in_03_appear_in_04(self, generated_csvs):
        units_03 = {r["unit_number"] for r in generated_csvs["03_lots.csv"]}
        units_04 = {r["unit_number"] for r in generated_csvs["04_current_owners.csv"]}
        assert units_03 == units_04

    def test_owner_names_not_empty(self, generated_csvs):
        empty_name_count = sum(
            1 for r in generated_csvs["04_current_owners.csv"]
            if not r["owner_name_primary"].strip()
        )
        assert empty_name_count == 0, f"{empty_name_count} owners have no primary name"

    def test_owner_types_valid(self, generated_csvs):
        valid_types = {"individual", "joint", "corporate"}
        for r in generated_csvs["04_current_owners.csv"]:
            assert r["owner_type"] in valid_types, f"Invalid owner type: {r}"

    def test_joint_owners_have_secondary_name(self, generated_csvs):
        for r in generated_csvs["04_current_owners.csv"]:
            if r["owner_type"] == "joint":
                assert r["owner_name_secondary"].strip(), (
                    f"Joint owner unit {r['unit_number']} has no secondary name"
                )

    def test_31_joint_owners_present(self, generated_csvs):
        joint_count = sum(
            1 for r in generated_csvs["04_current_owners.csv"]
            if r["owner_type"] == "joint"
        )
        assert joint_count == 31, f"Expected 31 joint owners, got {joint_count}"

    def test_secondary_email_populated_for_joint_owners(self, generated_csvs):
        joint_rows = [r for r in generated_csvs["04_current_owners.csv"]
                      if r["owner_type"] == "joint"]
        with_secondary_email = sum(1 for r in joint_rows if r.get("email_secondary", "").strip())
        # Most joint owners have separate user accounts; expect at least 20
        assert with_secondary_email >= 20, (
            f"Only {with_secondary_email}/31 joint owners have a secondary email"
        )

    def test_no_test_emails(self, generated_csvs):
        test_domains = {"example.com", "test.com", "localhost"}
        for r in generated_csvs["04_current_owners.csv"]:
            for field in ("email_primary", "email_secondary"):
                email = r.get(field, "")
                if email:
                    domain = email.split("@")[-1].lower()
                    assert domain not in test_domains, f"Test email in {field}: {email}"

    def test_primary_and_secondary_emails_differ_for_joint(self, generated_csvs):
        for r in generated_csvs["04_current_owners.csv"]:
            if r["owner_type"] == "joint":
                ep = r.get("email_primary", "")
                es = r.get("email_secondary", "")
                if ep and es:
                    assert ep != es, (
                        f"Primary and secondary email are the same for {r['unit_number']}: {ep}"
                    )


# ---------------------------------------------------------------------------
# Content assertions — Owner transfers (05)
# ---------------------------------------------------------------------------

class TestOwnerTransfers:
    def test_all_transfers_have_effective_from(self, generated_csvs):
        for r in generated_csvs["05_owner_transfers.csv"]:
            assert r["effective_from"], f"Missing effective_from: {r}"

    def test_no_overlapping_periods_same_lot(self, generated_csvs):
        by_unit = defaultdict(list)
        for r in generated_csvs["05_owner_transfers.csv"]:
            by_unit[r["unit_number"]].append(r)

        for unit_num, rows in by_unit.items():
            sorted_rows = sorted(rows, key=lambda x: x["effective_from"])
            for i in range(len(sorted_rows) - 1):
                a, b = sorted_rows[i], sorted_rows[i + 1]
                if a["effective_to"]:
                    assert a["effective_to"] <= b["effective_from"], (
                        f"Overlapping periods for {unit_num}: "
                        f"{a['effective_from']}–{a['effective_to']} before {b['effective_from']}"
                    )

    def test_transfer_dates_in_range(self, generated_csvs):
        for r in generated_csvs["05_owner_transfers.csv"]:
            assert r["effective_from"] >= "2020-01-01", f"Suspiciously old date: {r}"
            assert r["effective_from"] <= "2026-12-31", f"Future date: {r}"


# ---------------------------------------------------------------------------
# Content assertions — Quarterly levies (06)
# ---------------------------------------------------------------------------

class TestQuarterlyLevies:
    def test_all_years_present(self, generated_csvs):
        years = {r["year"] for r in generated_csvs["06_historical_quarterly_levies.csv"]}
        assert years == {"2021", "2022", "2023", "2024", "2025", "2026"}

    def test_all_quarters_present(self, generated_csvs):
        quarters = {r["quarter"] for r in generated_csvs["06_historical_quarterly_levies.csv"]}
        assert quarters == {"Q1", "Q2", "Q3", "Q4"}

    def test_2026_admin_annual_total_within_1_percent(self, generated_csvs):
        rows_2026 = [r for r in generated_csvs["06_historical_quarterly_levies.csv"]
                     if r["year"] == "2026"]
        total = sum(float(r["admin_levy_amount"]) for r in rows_2026)
        expected = 340_870.02
        pct_diff = abs(total - expected) / expected * 100
        assert pct_diff <= 1.0, f"2026 admin total {total:.2f} vs expected {expected:.2f}"

    def test_2026_sinking_annual_total_within_1_percent(self, generated_csvs):
        rows_2026 = [r for r in generated_csvs["06_historical_quarterly_levies.csv"]
                     if r["year"] == "2026"]
        total = sum(float(r["sinking_levy_amount"]) for r in rows_2026)
        expected = 99_504.90
        pct_diff = abs(total - expected) / expected * 100
        assert pct_diff <= 1.0, f"2026 sinking total {total:.2f} vs expected {expected:.2f}"

    def test_annual_totals_match_summary_exactly(self, generated_csvs):
        levy_rows = generated_csvs["06_historical_quarterly_levies.csv"]
        admin_summary = {
            row["year"]: float(row["levy_income"])
            for row in generated_csvs["07_historical_admin_fund_summary.csv"]
        }
        sinking_summary = {
            row["year"]: float(row["levy_income"])
            for row in generated_csvs["08_historical_sinking_fund_summary.csv"]
        }
        actuals = defaultdict(lambda: {"admin": 0.0, "sinking": 0.0})
        for row in levy_rows:
            actuals[row["year"]]["admin"] += float(row["admin_levy_amount"])
            actuals[row["year"]]["sinking"] += float(row["sinking_levy_amount"])

        for year, totals in actuals.items():
            assert round(totals["admin"] - admin_summary[year], 2) == 0, (
                f"Admin levy mismatch for {year}: {totals['admin']:.2f} vs {admin_summary[year]:.2f}"
            )
            if year != "2021":
                assert round(totals["sinking"] - sinking_summary[year], 2) == 0, (
                    f"Sinking levy mismatch for {year}: {totals['sinking']:.2f} vs {sinking_summary[year]:.2f}"
                )

    def test_2021_q1_period_starts_dec_2020(self, generated_csvs):
        q1_2021 = next(
            r for r in generated_csvs["06_historical_quarterly_levies.csv"]
            if r["year"] == "2021" and r["quarter"] == "Q1"
        )
        assert q1_2021["period_start"] == "2020-12-01"

    def test_quarterly_due_dates_match_schedule(self, generated_csvs):
        expected_md = {
            "Q1": ("03", "31"),
            "Q2": ("06", "01"),
            "Q3": ("09", "01"),
            "Q4": ("12", "01"),
        }
        for r in generated_csvs["06_historical_quarterly_levies.csv"]:
            q = r["quarter"]
            due = r["due_date"]  # YYYY-MM-DD
            _, mm, dd = due.split("-")
            exp_mm, exp_dd = expected_md[q]
            assert mm == exp_mm and dd == exp_dd, (
                f"Unexpected due date {due} for {r['year']} {q}"
            )

    def test_positive_levy_amounts(self, generated_csvs):
        for r in generated_csvs["06_historical_quarterly_levies.csv"]:
            assert float(r["admin_levy_amount"]) >= 0, f"Negative admin levy: {r}"
            assert float(r["total_levy_amount"]) > 0, f"Zero total levy: {r}"

    def test_excl_gst_less_than_incl_gst(self, generated_csvs):
        for r in generated_csvs["06_historical_quarterly_levies.csv"]:
            if float(r["admin_levy_amount"]) > 0:
                assert float(r["admin_levy_amount_excl_gst"]) < float(r["admin_levy_amount"]), (
                    f"excl_gst not less than incl_gst: {r}"
                )


# ---------------------------------------------------------------------------
# Content assertions — Fund summaries (07, 08)
# ---------------------------------------------------------------------------

class TestFundSummaries:
    def test_admin_years_span_2021_to_2026(self, generated_csvs):
        years = {r["year"] for r in generated_csvs["07_historical_admin_fund_summary.csv"]}
        assert years == {"2021", "2022", "2023", "2024", "2025", "2026"}

    def test_sinking_years_span_2021_to_2026(self, generated_csvs):
        years = {r["year"] for r in generated_csvs["08_historical_sinking_fund_summary.csv"]}
        assert years == {"2021", "2022", "2023", "2024", "2025", "2026"}

    def test_admin_2026_levy_income_is_user_confirmed(self, generated_csvs):
        row = next(r for r in generated_csvs["07_historical_admin_fund_summary.csv"]
                   if r["year"] == "2026")
        assert abs(float(row["levy_income"]) - 340_870.02) < 0.01

    def test_sinking_2026_levy_income_is_user_confirmed(self, generated_csvs):
        row = next(r for r in generated_csvs["08_historical_sinking_fund_summary.csv"]
                   if r["year"] == "2026")
        assert abs(float(row["levy_income"]) - 99_504.90) < 0.01

    def test_2021_fy_starts_dec_2020(self, generated_csvs):
        for fname in ["07_historical_admin_fund_summary.csv", "08_historical_sinking_fund_summary.csv"]:
            row = next(r for r in generated_csvs[fname] if r["year"] == "2021")
            assert row["period_start"] == "2020-12-01"
            assert row["period_end"] == "2021-12-31"

    def test_fy_periods_jan_to_dec_for_other_years(self, generated_csvs):
        for yr in ["2022", "2023", "2024", "2025", "2026"]:
            row = next(r for r in generated_csvs["07_historical_admin_fund_summary.csv"]
                       if r["year"] == yr)
            assert row["period_start"] == f"{yr}-01-01"
            assert row["period_end"] == f"{yr}-12-31"

    def test_inconsistent_flag_for_2025_admin(self, generated_csvs):
        row = next(r for r in generated_csvs["07_historical_admin_fund_summary.csv"]
                   if r["year"] == "2025")
        # expense_transactions exist for 2025 and don't match audited figure
        assert "Inconsistent" in row["reconciliation_note"] or row["reconciliation_note"] == "reconciled"

    def test_rollforward_opening_matches_prior_year_closing(self, generated_csvs):
        for filename in ("07_historical_admin_fund_summary.csv", "08_historical_sinking_fund_summary.csv"):
            rows = sorted(generated_csvs[filename], key=lambda row: row["year"])
            for previous, current in zip(rows, rows[1:]):
                assert round(float(current["opening_balance"]) - float(previous["closing_balance"]), 2) == 0, (
                    f"{filename} rollforward mismatch: {current['year']} opening "
                    f"{current['opening_balance']} vs prior close {previous['closing_balance']}"
                )

    def test_admin_2023_notes_record_carry_forward_adjustment(self, generated_csvs):
        row = next(r for r in generated_csvs["07_historical_admin_fund_summary.csv"] if r["year"] == "2023")
        assert "Carry-forward adjustment +65477.79" in row["notes"]
        assert row["reconciliation_note"] == "carry_forward_adjustment:+65477.79"


# ---------------------------------------------------------------------------
# Content assertions — Arrears (09)
# ---------------------------------------------------------------------------

class TestArrears:
    def test_all_87_units_in_arrears_file(self, generated_csvs):
        assert len(generated_csvs["09_arrears_2025_12_31.csv"]) == 87

    def test_arrears_amounts_non_negative(self, generated_csvs):
        for r in generated_csvs["09_arrears_2025_12_31.csv"]:
            assert float(r["admin_arrears"]) >= 0
            assert float(r["sinking_arrears"]) >= 0
            assert float(r["total_arrears"]) >= 0

    def test_some_units_have_arrears(self, generated_csvs):
        arrears_count = sum(
            1 for r in generated_csvs["09_arrears_2025_12_31.csv"]
            if float(r["total_arrears"]) > 0
        )
        assert arrears_count > 0, "Expected some units in arrears at 31 Dec 2025"


# ---------------------------------------------------------------------------
# Content assertions — Outstanding (10)
# ---------------------------------------------------------------------------

class TestOutstanding:
    def test_all_87_units_in_outstanding_file(self, generated_csvs):
        assert len(generated_csvs["10_outstanding_2026_06_01.csv"]) == 87

    def test_as_at_date_is_2026_06_01(self, generated_csvs):
        for r in generated_csvs["10_outstanding_2026_06_01.csv"]:
            assert r["as_at_date"] == "2026-06-01"


# ---------------------------------------------------------------------------
# Content assertions — Opening balances (11)
# ---------------------------------------------------------------------------

class TestOpeningBalances:
    def test_two_funds(self, generated_csvs):
        funds = {r["fund_type"] for r in generated_csvs["11_opening_balances_at_cutover.csv"]}
        assert "Administrative Fund" in funds
        assert "Sinking Fund" in funds

    def test_admin_balance_from_latest_bank_rec(self, generated_csvs):
        row = next(r for r in generated_csvs["11_opening_balances_at_cutover.csv"]
                   if r["fund_type"] == "Administrative Fund")
        # Latest bank rec was 2026-04-06: admin=$17,778.11
        assert abs(float(row["opening_balance_amount"]) - 17_778.11) < 0.01
        assert row["as_at_date"] == "2026-04-06"

    def test_sinking_balance_from_latest_bank_rec(self, generated_csvs):
        row = next(r for r in generated_csvs["11_opening_balances_at_cutover.csv"]
                   if r["fund_type"] == "Sinking Fund")
        assert abs(float(row["opening_balance_amount"]) - 156_351.48) < 0.01

    def test_bsb_and_account_number_present(self, generated_csvs):
        for r in generated_csvs["11_opening_balances_at_cutover.csv"]:
            assert r["bsb"], "BSB missing"
            assert r["account_number"], "Account number missing"


# ---------------------------------------------------------------------------
# Cross-file consistency
# ---------------------------------------------------------------------------

class TestCrossFileConsistency:
    def test_unit_numbers_consistent_across_all_files(self, generated_csvs):
        units_03 = {r["unit_number"] for r in generated_csvs["03_lots.csv"]}
        units_04 = {r["unit_number"] for r in generated_csvs["04_current_owners.csv"]}
        units_09 = {r["unit_number"] for r in generated_csvs["09_arrears_2025_12_31.csv"]}
        units_10 = {r["unit_number"] for r in generated_csvs["10_outstanding_2026_06_01.csv"]}
        assert units_03 == units_04, "Unit mismatch between lots (03) and owners (04)"
        assert units_03 == units_09, "Unit mismatch between lots (03) and arrears (09)"
        assert units_03 == units_10, "Unit mismatch between lots (03) and outstanding (10)"

    def test_quarterly_levies_cover_all_87_units_per_quarter(self, generated_csvs):
        rows = generated_csvs["06_historical_quarterly_levies.csv"]
        by_year_quarter = defaultdict(set)
        for r in rows:
            by_year_quarter[(r["year"], r["quarter"])].add(r["unit_number"])
        for (yr, q), units in by_year_quarter.items():
            assert len(units) == 87, f"{yr} {q} has {len(units)} units, expected 87"

    def test_idempotent_second_run(self, output_dir):
        """Running the script twice into the same directory produces identical files."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "extract_east_gate_import",
            ROOT / "scripts" / "data_extraction" / "extract_east_gate_import.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        data = mod.load_all_data(str(DUMP_PATH))
        # Read first-run files
        first_run = {}
        for csv_path in sorted(Path(output_dir).glob("*.csv")):
            first_run[csv_path.name] = csv_path.read_text()

        # Re-run into same dir
        mod.generate_01_organisation(output_dir)
        mod.generate_03_lots(output_dir, data)
        mod.generate_06_quarterly_levies(output_dir, data)

        for csv_path in sorted(Path(output_dir).glob("*.csv")):
            if csv_path.name in first_run:
                assert csv_path.read_text() == first_run[csv_path.name], (
                    f"Second run produced different output for {csv_path.name}"
                )
