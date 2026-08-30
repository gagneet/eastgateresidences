# @featuretrace:demo_bank — Converter artifact tests for UP13195 Demo Bank upload CSVs.
# Layer: test
# Data flow: pytest -> convert_up13195_financials_to_demo_bank.py -> generated CSV/manifest files
#            -> Demo Bank parser contract (building-scoped, no database writes).
# Related: scripts/finance/convert_up13195_financials_to_demo_bank.py,
#          frontend/src/pages/dashboard/admin/DemoBankTransactionsPage.tsx
"""Tests for the UP13195 Demo Bank upload converter.

These tests read committed fixture CSVs and generated artifacts only. They do
not create database records, so no database cleanup is required.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/finance/convert_up13195_financials_to_demo_bank.py"


def _load_converter():
    spec = importlib.util.spec_from_file_location("up13195_converter", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_source_owner_rows_are_complete_and_uoe_balanced():
    converter = _load_converter()
    rows = converter.read_source()

    owners = converter.load_owners(rows)

    assert len(owners) == 87
    assert sum((owner.uoe for owner in owners), Decimal("0")) == Decimal("10000")
    assert {owner.lot_number for owner in owners} == {str(lot) for lot in range(1, 88)}


def test_generated_owner_levies_reconcile_to_proposed_totals_by_year_and_fund():
    converter = _load_converter()
    rows = converter.read_source()
    owners = converter.load_owners(rows)
    annual_totals = converter.load_annual_totals(rows)

    levy_rows = converter.build_levy_rows(owners, annual_totals)
    actual: defaultdict[tuple[int, str], int] = defaultdict(int)
    for row in levy_rows:
        actual[(row.source_year, row.fund)] += row.amount_cents

    expected = {
        key: converter.cents(total)
        for key, total in annual_totals.items()
        if total > 0
    }
    assert actual == expected


def test_payment_schedule_covers_mixed_frequency_and_full_2026_july_range():
    converter = _load_converter()

    assert sum(fraction for _period, _date, fraction in converter.PAYMENT_SCHEDULE[2022]) == Decimal("1.0")
    assert [period for period, _date, _fraction in converter.PAYMENT_SCHEDULE[2022]] == ["H1", "Q3", "Q4"]
    assert sum(fraction for _period, _date, fraction in converter.PAYMENT_SCHEDULE[2026]) == Decimal("1.00")
    assert converter.PAYMENT_SCHEDULE[2026][-1][1].isoformat() == "2026-07-01"


def test_generated_bank_csvs_parse_with_demo_bank_schema():
    converter = _load_converter()

    admin = converter.validate_with_demo_bank_parser(
        converter.OUT_DIR / "up13195_demo_bank_admin_upload_2020-2026.csv",
        converter.ADMIN_ACCOUNT_REF,
    )
    sinking = converter.validate_with_demo_bank_parser(
        converter.OUT_DIR / "up13195_demo_bank_sinking_upload_2020-2026.csv",
        converter.SINKING_ACCOUNT_REF,
    )

    assert admin["parsed_rows"] == 2010
    assert admin["max_date"] == "2026-07-01"
    assert sinking["parsed_rows"] == 1680
    assert sinking["max_date"] == "2026-07-01"


def test_combined_manifest_contains_no_future_or_zero_amount_rows():
    manifest = ROOT / "docs/finances/generated/up13195_demo_bank_combined_manifest_2020-2026.csv"
    with manifest.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 3690
    assert all(row["building_id"] == "13195" for row in rows)
    assert all(row["date"] <= "2026-07-26" for row in rows)
    assert all(Decimal(row["amount"]) != Decimal("0") for row in rows)
