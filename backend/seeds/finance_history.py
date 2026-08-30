"""
backend/seeds/finance_history.py
=================================
Authoritative seed data for East Gate Residences (SP 13195) — FY 2021 to FY 2026.

Run this script to rebuild all annual_levies and levy_categories from scratch.
Existing records for years 2021–2026 are REMOVED and re-inserted.

Sources:
  2021 — User-provided budget vs actuals (strata records)
  2022 — User-provided actuals; proposed budget from AGM papers
  2023 — Civium Strata management PDF (02/05/2023–31/12/2023); full-year actuals
           from east_gate_proposed_budget_2024.pdf "2023 Actual" column
  2024 — Actual fund totals from user's 5-year summary; opening = 2023 Civium close;
           closing = 2025 audited open; per-category = 2024 proposed budget only
  2025 — Audited financial report (east_gate_audited_financial_report_2025.pdf)
  2026 — Proposed budget (east_gate_proposed_budget_2026.pdf)

Balance chain anchors (real/audited):
  2024 open  (= 2023 Civium Dec-31 close): Admin  $49,263.23   Sinking  $76,240.32
  2025 open  (= audited 2025 opening):     Admin −$37,321.16   Sinking $143,360.62
  2025 close (= audited):                  Admin  $34,332.27   Sinking $212,644.97

Usage:
    cd /path/to/strata-management/backend
    source venv/bin/activate
    python3 seeds/finance_history.py

    # Dry-run (no DB writes, just verifies checksums):
    python3 seeds/finance_history.py --dry-run
"""

import os
import sys
import uuid
from datetime import datetime, timezone

import asyncio
from pymongo import AsyncMongoClient
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ.get("DB_NAME", "strata_production")
PLAN_ID = "13195"
TOTAL_UOE = 10_000
LEVY_GST_MULTIPLIER = 1.10
NOW = datetime.now(timezone.utc).isoformat()
DRY_RUN = "--dry-run" in sys.argv


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def uid():
    """Generated function header.

    Function: uid
    Path: backend/seeds/finance_history.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(uuid.uuid4())


def levy_doc(year, data_origin, status, period_note,
             admin_levy_income, admin_other_income,
             admin_total_expenses, admin_open, admin_close,
             sink_levy_income, sink_other_income,
             sink_total_expenses, sink_open, sink_close,
             payment_schedule, notes="",
             proposed_admin=None, proposed_sinking=None):
    """Build an annual_levies document.

    Levy rates (admin_levy_per_uoe_*) are derived from the proposed levy-income
    budget resolved at the AGM — NOT from actual income collected. These
    compatibility fields represent what owners are billed on quarterly notices.

    Canonical fund totals remain ex-GST in admin_fund/sinking_fund. The stored
    compatibility per-UOE fields are owner-payable amounts (GST-inclusive).

    proposed_admin  — Admin Fund proposed levy income (ex-GST); = AGM budget.
    proposed_sinking — Sinking Fund proposed levy income (ex-GST); = AGM budget.
    """
    admin_total_income = round(admin_levy_income + admin_other_income, 2)
    sink_total_income = round(sink_levy_income + sink_other_income, 2)

    # Use proposed ex-GST budget as the base for levy rate; fall back to actual levy
    # income only when no proposed amount is provided (e.g., 2021 first year).
    _admin_rate_base = proposed_admin if proposed_admin is not None else admin_levy_income
    _sink_rate_base = proposed_sinking if proposed_sinking is not None else sink_levy_income

    doc = {
        "id": uid(),
        "plan_id": PLAN_ID,
        "building_id": PLAN_ID,  # required by all API queries
        "year": year,
        "status": status,
        "data_origin": data_origin,
        "is_seed_data": False,
        "total_uoe": TOTAL_UOE,
        "period_note": period_note,
        "admin_fund": {
            "levy_income": round(admin_levy_income, 2),
            "other_income": round(admin_other_income, 2),
            "total_income": admin_total_income,
            "total_expenses": round(admin_total_expenses, 2),
            "opening_balance": round(admin_open, 2),
            "closing_balance": round(admin_close, 2),
            "surplus_deficit": round(admin_total_income - admin_total_expenses, 2),
        },
        # Compatibility only: per-UOE rates are stored as owner-payable amounts,
        # derived from the canonical ex-GST fund totals plus the building GST rule.
        "admin_levy_per_uoe_annual": round((_admin_rate_base * LEVY_GST_MULTIPLIER) / TOTAL_UOE, 4),
        "admin_levy_per_uoe_quarterly": round((_admin_rate_base * LEVY_GST_MULTIPLIER) / TOTAL_UOE / 4, 4),
        "sinking_fund": {
            "levy_income": round(sink_levy_income, 2),
            "other_income": round(sink_other_income, 2),
            "total_income": sink_total_income,
            "total_expenses": round(sink_total_expenses, 2),
            "opening_balance": round(sink_open, 2),
            "closing_balance": round(sink_close, 2),
            "surplus_deficit": round(sink_total_income - sink_total_expenses, 2),
        },
        "sinking_levy_per_uoe_annual": round((_sink_rate_base * LEVY_GST_MULTIPLIER) / TOTAL_UOE, 4)
        if _sink_rate_base > 0 else 0,
        "sinking_levy_per_uoe_quarterly": round((_sink_rate_base * LEVY_GST_MULTIPLIER) / TOTAL_UOE / 4, 4)
        if _sink_rate_base > 0 else 0,
        "payment_schedule": payment_schedule,
        "notes": notes,
        "created_at": NOW,
        "updated_at": NOW,
    }
    if proposed_admin is not None:
        doc["proposed_admin_expenses"] = round(proposed_admin, 2)
    if proposed_sinking is not None:
        doc["proposed_sinking_expenses"] = round(proposed_sinking, 2)
    return doc


def cat_doc(year, fund_type, status, name, budgeted, actual):
    """Generated function header.

    Function: cat_doc
    Path: backend/seeds/finance_history.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "id": uid(),
        "plan_id": PLAN_ID,
        "building_id": PLAN_ID,  # required by all API queries
        "year": year,
        "status": status,
        "fund_type": fund_type,
        "name": name,
        "budgeted_amount": round(budgeted, 2),
        "actual_amount": round(actual, 2),
        "description": "",
        "created_at": NOW,
        "updated_at": NOW,
    }


# ─────────────────────────────────────────────────────────────────────────────
# FY 2021  (Nov 2020 – Dec 2021, 14-month first strata year)
# Source: user-provided budget vs actuals
# ─────────────────────────────────────────────────────────────────────────────

LEVY_2021 = levy_doc(
    year="2021", data_origin="user_provided", status="actual",
    period_note=(
        "First strata year (Nov 2020–Dec 2021, ~14 months). "
        "Sinking Fund not yet established — left for first EC to provision. "
        "$23,761.63 admin deficit carried into 2022 opening balance."
    ),
    admin_levy_income=138_460.00, admin_other_income=0.00,
    admin_total_expenses=162_221.63,
    admin_open=0.00, admin_close=-23_761.63,
    sink_levy_income=0.00, sink_other_income=0.00,
    sink_total_expenses=0.00,
    sink_open=0.00, sink_close=0.00,
    payment_schedule=[
        {"quarter": "Q1", "due_date": "2021-03-31"},
        {"quarter": "Q2", "due_date": "2021-06-30"},
        {"quarter": "Q3", "due_date": "2021-10-31"},
        {"quarter": "Q4", "due_date": "2022-01-31"},
    ],
    proposed_admin=138_460.00, proposed_sinking=0.00,
)

CATS_2021 = [
    # (name, budgeted, actual)  — admin fund only (no sinking in 2021)
    ("Basement Cleaning", 0.00, 357.07),
    ("Keys / Lock / Fobs", 0.00, 4_531.28),
    ("Lift Maintenance", 2_000.00, 5_400.00),
    ("Lift Telephony", 0.00, 3_630.00),
    ("Lift Registration Fee", 0.00, 650.00),
    ("Fire Protection – Repairs & Servicing", 0.00, 1_716.55),
    ("Fire Protection – Monitoring", 0.00, 2_200.00),
    ("Banking Costs", 0.00, 443.60),
    ("Cleaning / Caretaking", 27_000.00, 27_000.00),
    ("Common Seal", 0.00, 60.00),
    ("Electricity – Utility", 9_000.00, 12_022.84),
    ("Gardens & Grounds", 15_000.00, 14_375.00),
    ("Management Fees", 34_800.00, 38_653.42),
    ("Management Fees – Work Out of Scope", 0.00, 1_111.00),
    ("Insurance Premiums", 13_000.00, 31_272.63),
    ("Water – Utility", 26_000.00, 14_284.69),
    ("Repairs & Maintenance – Building", 1_000.00, 638.00),
    ("Repairs & Maintenance – Electrical", 800.00, 418.00),
    ("Repairs & Maintenance – Garage Doors", 1_800.00, 440.00),
    ("Repairs & Maintenance – Plumbing", 2_000.00, 574.20),
    ("Rules Registration", 0.00, 193.75),
    ("Roof Access System Certification", 2_000.00, 906.40),
    ("Rubbish Removal", 2_000.00, 222.00),
    ("Sinking Fund Forecast Report", 1_500.00, 1_120.00),
    ("Sundry Items", 560.00, 0.00),
]

# ─────────────────────────────────────────────────────────────────────────────
# FY 2022  (Jan 2022 – Dec 2022)
# Source: user-provided actuals; proposed from AGM budget papers
# ─────────────────────────────────────────────────────────────────────────────

LEVY_2022 = levy_doc(
    year="2022", data_origin="user_provided", status="actual",
    period_note=(
        "First full calendar year under Capital Strata (CSMS). Sinking Fund established by EC. "
        "Admin opening = true economic deficit from 2021 (-$23,761.63, LJ Hooker handover). "
        "Sinking income: $10,000 lift provision + $15,000.79 prior-year back-levy. "
        "Admin expenses based on Capital Strata YTD actuals plus projected full-year — NOT an audited close. "
        "Source: UP13195 2022 YTD financials against Budget and 2022 projections.pdf"
    ),
    admin_levy_income=221_900.00,
    admin_other_income=3_004.27,  # $2,004.27 prior-year recovery + $1,000 debt recovery
    admin_total_expenses=228_089.17,  # corrected: 2022 projected full-year actual (was 216,901.65)
    admin_open=-23_761.63, admin_close=-26_943.53,  # corrected economic closing (was -15,759.01)
    sink_levy_income=10_000.00,  # lift replacement provision contributions
    sink_other_income=15_000.79,  # prior-year back-levy + interest (was 15,000.00)
    sink_total_expenses=74_981.20,  # corrected: per-category CSV sum consistent with closing (was 24,219.61)
    sink_open=0.00, sink_close=-49_980.41,  # corrected (was 780.39)
    payment_schedule=[
        {"quarter": "Q1", "due_date": "2022-03-31"},
        {"quarter": "Q2", "due_date": "2022-06-30"},
        {"quarter": "Q3", "due_date": "2022-09-30"},
        {"quarter": "Q4", "due_date": "2022-12-31"},
    ],
    proposed_admin=228_089.17, proposed_sinking=25_000.00,
)

# Columns: (name, budget_2022, actual_2022)
# Source: UP13195 - 2022 YTD financials against Budget and 2022 projections.pdf
# actual_amount = projected full-year actual (not audited); basis = 2022_projected_full_year_actual_from_YTD_plus_projection
CATS_2022_ADMIN = [
    ("Accounting costs", 1_045.00, 715.00),
    ("Banking costs", 142.40, 142.40),
    ("Cleaning - basement", 5_000.00, 1_300.00),
    ("Cleaning - carpets", 2_000.00, 0.00),
    ("Cleaning/caretaking", 12_000.00, 8_599.41),
    ("Common seal", 33.75, 33.75),
    ("Consultant fees", 5_000.00, 5_032.00),
    ("Debt recovery costs incurred", 1_000.00, 0.00),
    ("Electricity", 18_000.00, 17_683.51),
    ("Fire protection - monitoring", 2_200.00, 0.00),
    ("Fire protection equipment – repairs", 0.00, 616.00),
    ("Fire protection equipment – servicing", 5_500.00, 7_730.80),
    ("Fire protection – false alarm call out charges", 0.00, 0.00),
    ("Garage vehicle basement door maintenance", 800.00, 800.00),
    ("Gardening", 17_000.00, 13_798.25),
    ("Gardening - landscaping", 0.00, 580.00),
    ("Gardening - mulching", 0.00, 2_612.00),
    ("Insurance excesses", 3_000.00, 500.00),
    ("Insurance premium", 38_000.00, 37_983.52),
    ("Key, lock & fob expenses", 4_500.00, 6_266.28),
    ("Legal expenses", 5_000.00, 5_000.00),
    ("Lift consultant", 3_740.00, 3_750.00),
    ("Lift maintenance", 26_400.00, 26_400.00),
    ("Lift registration fee", 0.00, 0.00),
    ("Lift telephony", 3_960.00, 3_960.00),
    ("Management fees - CSMS", 32_594.60, 32_594.60),
    ("Management fees - LJ Hooker", 953.42, 953.42),
    ("Management fees - work out of scope", 3_300.00, 6_063.13),
    ("Management fees – meetings", 1_320.00, 2_443.75),
    ("Office of Regulatory", 0.00, 137.00),
    ("Pest control", 1_000.00, 3_685.00),
    ("Postage", 1_500.00, 2_458.08),
    ("Repairs & Maintenance - Building", 0.00, 462.00),
    ("Repairs & Maintenance - plumbing", 0.00, 287.10),
    ("Repairs & Maintenance – garage Doors", 0.00, 730.00),
    ("Roof access system Certification", 1_000.00, 1_000.00),
    ("Rubbish removal", 500.00, 231.00),
    ("Taxation payments - income tax & GST", 3_600.00, 0.00),
    ("Unit title certificate costs", 0.00, 721.00),
    ("Venue hire", 0.00, 0.00),
    ("Water consumption", 28_000.00, 26_247.04),
    # $6,573.13 in projected expenditure not captured in named categories (Capital Strata 2022 projected year)
    ("Reconciliation Adjustment (Capital Strata 2022 — unallocated)", 0.00, 6_573.13),
]
# Verify: actuals sum to $228,089.17
assert abs(sum(r[2] for r in CATS_2022_ADMIN) - 228_089.17) < 0.02, \
    f"CATS_2022_ADMIN actual sum = {sum(r[2] for r in CATS_2022_ADMIN):.2f}, expected 228089.17"

# Columns: (name, budget_2022, actual_2022)
# Source: UP13195 - 2022 YTD financials against Budget and 2022 projections.pdf
# Per-category CSV sums to $74,981.20, consistent with closing balance of -$49,980.41
CATS_2022_SINKING = [
    ("Door handle replacement", 0.00, 2_000.00),
    ("Electrical repairs", 2_000.00, 9_549.47),
    ("Fire protection Equipment", 0.00, 2_762.23),
    ("Garage door repair", 0.00, 269.50),
    ("Planter box removal and waterproofing make good", 10_000.00, 45_990.00),
    ("Plumbing repairs", 1_000.00, 0.00),
    ("Roofing repairs", 1_000.00, 2_745.00),
    ("Signage", 500.00, 2_000.00),
    ("Waterproofing works", 0.00, 9_665.00),
]
# Verify: actuals sum to $74,981.20
assert abs(sum(r[2] for r in CATS_2022_SINKING) - 74_981.20) < 0.02, \
    f"CATS_2022_SINKING actual sum = {sum(r[2] for r in CATS_2022_SINKING):.2f}, expected 74981.20"

# ─────────────────────────────────────────────────────────────────────────────
# FY 2023 — Civium Strata management period: 02/05/2023 – 31/12/2023
# Source: east_gate_proposed_budget_2024.pdf "2023 Actual" column
# (Full Civium-period actuals; replaces partial Jul-Nov audited sub-period)
# ─────────────────────────────────────────────────────────────────────────────

LEVY_2023 = levy_doc(
    year="2023", data_origin="strata_portal", status="actual",
    period_note=(
        "Civium Strata management period 02/05/2023–31/12/2023 (8-month partial year). "
        "Opening balances represent the fund position when Civium took over (May 2023). "
        "Jan–Apr 2023 (under previous manager) is not separately recorded. "
        "Closing = Dec 31, 2023 from Civium management report (real anchor for 2024 opening). "
        "Source: east_gate_proposed_budget_2024.pdf '2023 Actual' column."
    ),
    admin_levy_income=165_986.79,
    admin_other_income=2_823.74,  # interest $395.72 + overdues $100.75 + keys $327.27 + recovery $2,000
    admin_total_expenses=169_266.08,
    admin_open=49_718.78, admin_close=49_263.23,
    sink_levy_income=45_718.08,
    sink_other_income=517.89,  # interest $206.89 + overdues $143.50 + recovery $167.50
    sink_total_expenses=31_340.78,
    sink_open=61_345.13, sink_close=76_240.32,
    payment_schedule=[
        {"quarter": "Q3", "due_date": "2023-08-01"},
        {"quarter": "Q4", "due_date": "2023-11-01"},
    ],
    proposed_admin=250_945.87, proposed_sinking=93_400.00,
)

# Columns: (name, 2024_proposed [used as budget], 2023_actual, 2023_budget)
# Negative actuals = credits
_ADMIN_2023_2024 = [
    ("Accountant - Professional Fees", 132.00, 1_787.50, 132.00),
    ("Accounting Service Provision", 660.00, 200.00, 660.00),
    ("Admin Fund Deficit Recovery", 0.00, 0.00, 25_000.00),
    ("Arrears Recovery Costs", 0.00, 4.55, 0.00),
    ("BMC - Keys/Swipe Cards/Fobs", 0.00, 31.82, 0.00),
    ("Banking Charges", 0.00, 110.00, 0.00),
    ("Banking Management", 660.00, 200.00, 660.00),
    ("Building Repairs & Maintenance", 10_000.00, 8_026.02, 10_000.00),
    ("Bundled Disbursements", 4_355.00, 1_318.20, 4_355.00),
    ("Civium Disbursements", 0.00, -777.28, 0.00),  # credit
    ("Cleaning", 20_000.00, 18_075.60, 25_000.00),
    ("Cleaning - Carpets", 3_500.00, 2_600.00, 3_500.00),
    ("Electrical Repairs & Maintenance", 0.00, 790.00, 0.00),
    ("Electricity - Utility", 25_000.00, 19_974.83, 16_000.00),
    ("Fire Protection - Contracted", 10_260.00, 10_260.20, 6_500.00),
    ("GST Administration", 0.00, 340.92, 0.00),
    ("GST Payment/Refund", 0.00, -4_715.00, 0.00),  # credit
    ("Garage Door", 3_000.00, 435.00, 3_000.00),
    ("Gardening", 0.00, 9_979.55, 0.00),
    ("Gardens & Grounds", 20_000.00, 7_757.08, 0.00),
    ("Income Tax Expense", 0.00, 6_883.20, 0.00),
    ("Insurance Claims", 0.00, 2_334.09, 0.00),
    ("Insurance Premiums", 48_000.00, 1_225.00, 48_000.00),
    ("Keys Fobs & Access Swipes", 0.00, 145.45, 0.00),
    ("Keys and Locks", 0.00, 81.82, 0.00),
    ("Legal Expense", 0.00, 1_210.18, 0.00),
    ("Lift Maintenance Contract", 25_000.00, 30_250.00, 25_000.00),
    ("Management Fee", 25_000.00, 23_052.68, 29_580.00),
    ("Management Fees - Additional", 0.00, 3_000.00, 0.00),
    ("Online Portal Fees", 0.00, 131.80, 0.00),
    ("Plumbing & Drainage", 5_000.00, 215.00, 5_000.00),
    ("Postage", 0.00, 190.40, 0.00),
    ("Tax Agent Fees - BAS/GST", 0.00, 160.00, 0.00),
    ("Tax Agent Fees - Income Tax", 0.00, 105.00, 0.00),
    ("Taxation Reporting (Civium)", 132.00, 120.00, 132.00),
    ("Telephone - Intercom/Lift Line", 0.00, 3_600.00, 0.00),
    ("Trades Compliance", 297.00, 89.72, 297.00),
    ("Venue Hire", 500.00, 136.36, 500.00),
    ("Waste Collection", 2_000.00, 400.00, 2_000.00),
    ("Water - Utility", 20_000.00, 19_536.39, 16_000.00),
]

_SINKING_2023_2024 = [
    # (name, 2024_proposed, 2023_actual, 2023_budget)
    ("Driveway Maintenance", 0.00, 2_500.00, 0.00),
    ("Electrical Replacement/Upgrade", 0.00, 5_897.17, 0.00),
    ("Essential Services", 0.00, 1_940.01, 0.00),
    ("Fire Protection Replacement/Upgrade", 0.00, 3_450.42, 0.00),
    ("Lift Repairs", 0.00, 585.00, 0.00),
    ("Plumbing & Drainage Works", 0.00, 1_400.00, 0.00),
    ("Roof Repairs/Waterproofing", 0.00, 2_700.00, 0.00),
    ("Sinking Fund Capital Provision", 70_795.00, 2_960.00, 60_958.00),
    ("Waterproofing", 0.00, 9_908.18, 0.00),
]

# Verify 2023 category totals
assert abs(sum(r[2] for r in _ADMIN_2023_2024) - 169_266.08) < 0.02
assert abs(sum(r[2] for r in _SINKING_2023_2024) - 31_340.78) < 0.02
assert abs(sum(r[1] for r in _ADMIN_2023_2024) - 223_496.00) < 0.02
assert abs(sum(r[1] for r in _SINKING_2023_2024) - 70_795.00) < 0.02

# ─────────────────────────────────────────────────────────────────────────────
# FY 2024 — Actual fund totals; opening = 2023 Civium Dec-31 close
# Actual admin expenses: $311,666.80; sinking: $1,618.18
# Income derived via: income = closing − opening + expenses
# Per-category actuals not available (proposed budget only)
# ─────────────────────────────────────────────────────────────────────────────

_A24_OPEN = 49_263.23  # = 2023 Civium Dec-31 closing (real anchor)
_A24_CLOSE = -37_321.16  # = 2025 audited opening (real anchor)
_A24_EXP = 311_666.80  # real actual (user 5-year summary)
_A24_INC = round(_A24_CLOSE - _A24_OPEN + _A24_EXP, 2)  # 225,082.41

_S24_OPEN = 76_240.32  # = 2023 Civium Dec-31 closing
_S24_CLOSE = 143_360.62  # = 2025 audited opening
_S24_EXP = 1_618.18  # real actual
_S24_INC = round(_S24_CLOSE - _S24_OPEN + _S24_EXP, 2)  # 68,738.48

LEVY_2024 = levy_doc(
    year="2024", data_origin="derived_actual", status="actual",
    period_note=(
        "Full calendar year 01/01/2024–31/12/2024. "
        "Actual admin expenses $311,666.80 and sinking $1,618.18 from 5-year summary. "
        "Opening balances from Civium Dec-31-2023 management report. "
        "Closing = 2025 audited opening (real anchor). "
        "Income derived via: income = closing − opening + expenses. "
        "Per-category actuals sourced from 2024 budget comparison column."
    ),
    admin_levy_income=223_496.00,  # proposed levy; total inc = $225,082.41 (derived)
    admin_other_income=round(_A24_INC - 223_496.00, 2),  # 1,586.41
    admin_total_expenses=_A24_EXP,
    admin_open=_A24_OPEN, admin_close=_A24_CLOSE,
    sink_levy_income=_S24_INC,  # derived ($68,738.48)
    sink_other_income=0.00,
    sink_total_expenses=_S24_EXP,
    sink_open=_S24_OPEN, sink_close=_S24_CLOSE,
    payment_schedule=[
        {"quarter": "Q1", "due_date": "2024-03-31"},
        {"quarter": "Q2", "due_date": "2024-06-01"},
        {"quarter": "Q3", "due_date": "2024-09-01"},
        {"quarter": "Q4", "due_date": "2024-12-01"},
    ],
    proposed_admin=223_496.00, proposed_sinking=70_795.00,
)

# ─────────────────────────────────────────────────────────────────────────────
# FY 2024 per-category admin actuals
# Source: 2024 budget comparison column (budget_2024 vs actual_2024)
# Names must match _ADMIN_2023_2024 exactly for cross-year consistency
# ─────────────────────────────────────────────────────────────────────────────

CATS_2024_ADMIN = [
    # (name, budgeted_2024, actual_2024)
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

# Guard against typos
assert abs(sum(r[1] for r in CATS_2024_ADMIN) - 223_496.00) < 0.02, \
    f"CATS_2024_ADMIN budgeted sum = {sum(r[1] for r in CATS_2024_ADMIN):.2f}, expected 223496.00"
assert abs(sum(r[2] for r in CATS_2024_ADMIN) - 311_666.80) < 0.02, \
    f"CATS_2024_ADMIN actual sum = {sum(r[2] for r in CATS_2024_ADMIN):.2f}, expected 311666.80"

# ─────────────────────────────────────────────────────────────────────────────
# FY 2025 — Audited (east_gate_audited_financial_report_2025.pdf)
# ─────────────────────────────────────────────────────────────────────────────

LEVY_2025 = levy_doc(
    year="2025", data_origin="audited_pdf", status="actual",
    period_note=(
        "Full calendar year 01/01/2025–31/12/2025 under Civium Strata. "
        "Source: east_gate_audited_financial_report_2025.pdf. "
        "Admin income: Levy $317,948.97 + Insurance Refund $30,981.60 + Interest $721.66 = $349,652.23. "
        "Sinking income: Levy $116,961.29 + Interest $362.75 = $117,324.04. "
        "Levy rates derived from AGM-resolved proposed budget: Admin $262,076 + Sinking $99,262."
    ),
    admin_levy_income=317_948.97,
    admin_other_income=31_703.26,  # $30,981.60 insurance refund + $721.66 interest on overdues
    admin_total_expenses=277_998.80,
    admin_open=-37_321.16, admin_close=34_332.27,
    sink_levy_income=116_961.29,  # audited levy income
    sink_other_income=362.75,  # interest on overdues only
    sink_total_expenses=48_039.69,
    sink_open=143_360.62, sink_close=212_644.97,
    payment_schedule=[
        {"quarter": "Q1", "due_date": "2025-03-31"},
        {"quarter": "Q2", "due_date": "2025-06-01"},
        {"quarter": "Q3", "due_date": "2025-09-01"},
        {"quarter": "Q4", "due_date": "2025-12-01"},
    ],
    proposed_admin=262_076.00, proposed_sinking=99_262.00,
)

# Columns: (name, budget_2025, actual_2025)
# Source: east_gate_audited_financial_report_2025.pdf
# comparison_amount = actual_2024 (prior year); budget_amount = proposed_2025
CATS_2025_ADMIN = [
    ("Accountant - Professional Fees", 210.00, 1_168.84),
    ("Accounting Service Provision", 652.00, 653.67),
    ("Arrears Recovery Costs", 0.00, 125.00),
    ("Audit Fees", 0.00, 720.00),
    ("Banking Management", 652.00, 653.67),
    ("Building Repairs & Maintenance", 20_000.00, 11_964.47),
    ("Bundled Disbursements", 4_291.00, 4_307.94),
    ("CCTV System", 15_000.00, 1_440.00),
    ("Civium Disbursements", 0.00, 670.73),
    ("Cleaning", 20_000.00, 27_738.00),
    ("Cleaning - Car Park", 0.00, 2_360.00),
    ("Cleaning - Carpets", 0.00, 2_544.00),
    ("Consultant Fees", 0.00, 9_615.45),
    ("Electrical Repairs & Maintenance", 0.00, 569.80),
    ("Electricity - Utility", 25_000.00, 23_631.47),
    ("Fire Protection - Contracted", 10_500.00, 10_660.66),
    ("Fire Protection - Repairs/Replacements", 0.00, 670.00),
    ("GST Administration", 500.00, 0.00),
    ("Garage Door", 5_000.00, 1_200.00),
    ("Gardens & Grounds", 30_000.00, 32_696.32),
    ("HVAC - Service", 0.00, 531.00),
    ("Insurance Claims", 0.00, 40_994.15),  # insurance claim payout
    ("Insurance Premiums", 35_000.00, 6_746.27),
    ("Keys and Locks", 0.00, 3_055.52),
    ("Legal expense", 0.00, -90.91),  # credit
    ("Lift Maintenance Contract", 23_000.00, 24_447.80),
    ("Management Fee", 29_178.00, 29_294.25),
    ("Online Portal Fees", 430.00, 430.77),
    ("Pest Control", 0.00, 200.00),
    ("Plumbing & Drainage", 5_000.00, 3_048.00),
    ("Roofing Repairs & Maintenance", 0.00, 900.00),
    ("Sundry Expenses", 0.00, 397.78),
    ("Tax Agent Fees - BAS/GST", 260.00, 0.00),
    ("Taxation Reporting (Civium)", 110.00, 0.00),
    ("Trades Compliance", 293.00, 293.22),
    ("Waste collection", 2_000.00, 0.00),
    ("Water - Utility", 35_000.00, 34_360.93),
]
# Verify: actuals sum to $277,998.80
assert abs(sum(r[2] for r in CATS_2025_ADMIN) - 277_998.80) < 0.02, \
    f"CATS_2025_ADMIN actual sum = {sum(r[2] for r in CATS_2025_ADMIN):.2f}, expected 277998.80"
assert abs(sum(r[1] for r in CATS_2025_ADMIN) - 262_076.00) < 0.02, \
    f"CATS_2025_ADMIN budget sum = {sum(r[1] for r in CATS_2025_ADMIN):.2f}, expected 262076.00"

# Columns: (name, budget_2025, actual_2025)
# Source: east_gate_audited_financial_report_2025.pdf
CATS_2025_SINKING = [
    ("Building Improvement/Upgrade", 9_815.00, 21_753.00),
    ("Building Repairs & Maintenance", 0.00, 1_479.50),
    ("Capital Works Budget", 0.00, 0.00),
    ("Consultant Fees", 0.00, 0.00),
    ("Electrical Replacement/Upgrade", 0.00, 6_064.00),
    ("Fire Protection Replacement/Upgrade", 0.00, 7_105.29),
    ("Garage Door Replacement/Upgrade", 0.00, 3_400.00),
    ("Rep & Maint - Roof", 0.00, 7_806.15),
    ("Repairs & Maintenance Roof", 0.00, 431.75),
]
# Verify: actuals sum to $48,039.69
assert abs(sum(r[2] for r in CATS_2025_SINKING) - 48_039.69) < 0.02, \
    f"CATS_2025_SINKING actual sum = {sum(r[2] for r in CATS_2025_SINKING):.2f}, expected 48039.69"

# ─────────────────────────────────────────────────────────────────────────────
# FY 2026 — YTD actuals (2026_Latest_Financial_Report.pdf) + proposed budget
# Sources: east_gate_proposed_budget_2026.pdf (budget), 2026_Latest_Financial_Report.pdf (actuals)
# Status: partial_actual — year is in progress; actuals are YTD only
# ─────────────────────────────────────────────────────────────────────────────

# YTD actual values from 2026 Latest Financial Report
_A26_LEVY_YTD = 77_470.20  # YTD levy income (partial year)
_A26_INT_YTD = 133.17  # YTD interest on overdues
_A26_EXP_YTD = 75_592.65  # YTD admin expenses
_A26_OPEN = 34_332.27  # opening = 2025 audited close
_A26_CLOSE = 36_342.99  # YTD closing balance

_S26_LEVY_YTD = 22_615.32  # YTD sinking levy income
_S26_INT_YTD = 59.05  # YTD interest on overdues
_S26_EXP_YTD = 70_060.00  # YTD sinking expenses (Roof Repairs $68,160 + minor works)
_S26_OPEN = 212_644.97  # opening = 2025 audited close
_S26_CLOSE = 165_259.34  # YTD closing balance

# Verify YTD balance chain
assert abs(_A26_OPEN + _A26_LEVY_YTD + _A26_INT_YTD - _A26_EXP_YTD - _A26_CLOSE) < 0.02, \
    "Admin 2026 YTD balance chain broken"
assert abs(_S26_OPEN + _S26_LEVY_YTD + _S26_INT_YTD - _S26_EXP_YTD - _S26_CLOSE) < 0.02, \
    "Sinking 2026 YTD balance chain broken"

LEVY_2026 = levy_doc(
    year="2026", data_origin="partial_actual", status="partial_actual",
    period_note=(
        "Calendar year 01/01/2026–31/12/2026 — partial year (YTD actuals as of latest report). "
        "Proposed budget from east_gate_proposed_budget_2026.pdf stored in proposed_admin/proposed_sinking. "
        "Income/expense/closing fields reflect YTD actuals only (year not yet complete). "
        "Levy rates are derived from proposed budget (Admin $340,870.20 + Sinking $99,504.90), not YTD actuals."
    ),
    # YTD actuals — used for fund position display
    admin_levy_income=_A26_LEVY_YTD,
    admin_other_income=_A26_INT_YTD,
    admin_total_expenses=_A26_EXP_YTD,
    admin_open=_A26_OPEN, admin_close=_A26_CLOSE,
    sink_levy_income=_S26_LEVY_YTD,
    sink_other_income=_S26_INT_YTD,
    sink_total_expenses=_S26_EXP_YTD,
    sink_open=_S26_OPEN, sink_close=_S26_CLOSE,
    payment_schedule=[
        {"quarter": "Q1", "due_date": "2026-03-31"},
        {"quarter": "Q2", "due_date": "2026-06-01"},
        {"quarter": "Q3", "due_date": "2026-09-01"},
        {"quarter": "Q4", "due_date": "2026-12-01"},
    ],
    # Full-year proposed budget amounts (stored in dedicated fields)
    proposed_admin=340_870.20, proposed_sinking=99_504.90,
)
# Columns: (name, budget_2026, actual_2026_ytd)
# budget from east_gate_proposed_budget_2026.pdf; actual = YTD from 2026_Latest_Financial_Report.pdf
CATS_2026_ADMIN = [
    ("Accountant - Professional Fees", 1_182.00, 0.00),
    ("Accounting Service Provision", 616.00, 229.03),
    ("Arrears Recovery Costs", 0.00, 11.80),
    ("Audit Fees", 909.00, 730.00),
    ("Banking Management", 695.00, 233.57),
    ("Building Repairs & Maintenance", 15_000.00, 5_149.17),
    ("Bundled Disbursements", 4_579.00, 1_959.96),
    ("CCTV System", 1_455.00, 1_700.00),
    ("Civium Disbursements", 0.00, 205.65),
    ("Cleaning", 27_500.00, 6_678.73),
    ("Cleaning - Car Park", 2_500.00, 0.00),
    ("Cleaning - Carpets", 2_650.00, 0.00),
    ("Consultant Fees", 20_000.00, 2_950.00),
    ("Electrical Repairs & Maintenance", 909.00, 2_735.00),
    ("Electricity - Utility", 23_955.00, 3_660.00),
    ("Fire Protection - Contracted", 15_000.00, 2_397.90),
    ("Fire Protection - Repairs/Replacements", 909.00, 0.00),
    ("GST Administration", 0.00, 125.28),
    ("Garage Door", 1_818.00, 0.00),
    ("Gardens & Grounds", 34_000.00, 4_725.46),
    ("HVAC - Service", 909.00, 796.50),
    ("Insurance Claims", 20_000.00, 1_925.00),
    ("Insurance Premiums", 37_500.00, 10_037.58),
    ("Keys and Locks", 1_500.00, 957.82),
    ("Legal expense", 0.00, 1_590.91),
    ("Lift Maintenance Contract", 25_000.00, 5_241.72),
    ("Lift Repairs", 0.00, 2_560.00),
    ("Management Fee", 27_682.00, 9_718.62),
    ("Online Portal Fees", 457.00, 282.99),
    ("Pest Control", 500.00, 200.00),
    ("Plumbing & Drainage", 2_500.00, 292.50),
    ("Roofing Repairs & Maintenance", 1_500.00, 1_121.50),
    ("Sundry Expenses", 500.00, 224.55),
    ("Tax Agent Fees - BAS/GST", 0.00, 100.00),
    ("Trades Compliance", 360.00, 104.19),
    ("Water - Utility", 37_797.00, 6_947.22),
]
# Verify: YTD actuals sum to $75,592.65
assert abs(sum(r[2] for r in CATS_2026_ADMIN) - 75_592.65) < 0.02, \
    f"CATS_2026_ADMIN ytd sum = {sum(r[2] for r in CATS_2026_ADMIN):.2f}, expected 75592.65"

# Columns: (name, budget_2026, actual_2026_ytd)
CATS_2026_SINKING = [
    ("Building Improvement/Upgrade", 0.00, 1_900.00),
    ("Building Repairs & Maintenance", 0.00, 0.00),
    ("Capital Works Budget", 4_896.00, 0.00),
    ("Electrical Replacement/Upgrade", 0.00, 0.00),
    ("Fire Protection Replacement/Upgrade", 1_707.00, 0.00),
    ("Garage Door Replacement/Upgrade", 9_851.00, 0.00),
    ("Plumbing & Drainage Works", 197.00, 0.00),
    ("Rep & Maint - Roof", 0.00, 0.00),
    ("Repairs & Maintenance Roof", 0.00, 0.00),
    ("Roof Repairs", 55_680.00, 68_160.00),
    ("Sprinkler System", 1_313.00, 0.00),
]
# Verify: YTD actuals sum to $70,060.00
assert abs(sum(r[2] for r in CATS_2026_SINKING) - 70_060.00) < 0.02, \
    f"CATS_2026_SINKING ytd sum = {sum(r[2] for r in CATS_2026_SINKING):.2f}, expected 70060.00"


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

async def seed(db):
    """Drop existing records for all years and re-insert from seed data."""

    if DRY_RUN:
        print("DRY RUN — no data written. Checksums passed ✅")
        return {}

    results = {}

    for year in ["2021", "2022", "2023", "2024", "2025", "2026"]:
        r1 = await db.annual_levies.delete_many({"year": year})
        r2 = await db.levy_categories.delete_many({"year": year})
        results[year] = {"levies_deleted": r1.deleted_count, "cats_deleted": r2.deleted_count}

    # ── 2021 ─────────────────────────────────────────────────────────────────
    await db.annual_levies.insert_one(LEVY_2021)
    cats = [cat_doc("2021", "administrative", "actual", n, b, a) for n, b, a in CATS_2021]
    await db.levy_categories.insert_many(cats)
    print(f"  2021: 1 levy, {len(cats)} admin cats")

    # ── 2022 ─────────────────────────────────────────────────────────────────
    await db.annual_levies.insert_one(LEVY_2022)
    cats_a = [cat_doc("2022", "administrative", "actual", n, b, a) for n, b, a in CATS_2022_ADMIN]
    cats_s = [cat_doc("2022", "sinking", "actual", n, b, a) for n, b, a in CATS_2022_SINKING]
    await db.levy_categories.insert_many(cats_a + cats_s)
    print(f"  2022: 1 levy, {len(cats_a)} admin + {len(cats_s)} sinking cats")

    # ── 2023 ─────────────────────────────────────────────────────────────────
    await db.annual_levies.insert_one(LEVY_2023)
    # Use 2023 actual column from _ADMIN_2023_2024, budget = 2023_budget column
    cats_a = [cat_doc("2023", "administrative", "actual", n, bud23, act23)
              for n, _, act23, bud23 in _ADMIN_2023_2024]
    cats_s = [cat_doc("2023", "sinking", "actual", n, bud23, act23)
              for n, _, act23, bud23 in _SINKING_2023_2024]
    await db.levy_categories.insert_many(cats_a + cats_s)
    print(f"  2023: 1 levy, {len(cats_a)} admin + {len(cats_s)} sinking cats")

    # ── 2024 ─────────────────────────────────────────────────────────────────
    await db.annual_levies.insert_one(LEVY_2024)
    # admin: real per-category actuals now available; sinking: proposed only
    cats_a = [cat_doc("2024", "administrative", "actual", n, b, a) for n, b, a in CATS_2024_ADMIN]
    cats_s = [cat_doc("2024", "sinking", "proposed", n, prop24, 0.0)
              for n, prop24, _, _ in _SINKING_2023_2024]
    await db.levy_categories.insert_many(cats_a + cats_s)
    print(f"  2024: 1 levy, {len(cats_a)} admin (actual) + {len(cats_s)} sinking cats")

    # ── 2025 ─────────────────────────────────────────────────────────────────
    await db.annual_levies.insert_one(LEVY_2025)
    cats_a = [cat_doc("2025", "administrative", "actual", n, b, a) for n, b, a in CATS_2025_ADMIN]
    cats_s = [cat_doc("2025", "sinking", "actual", n, b, a) for n, b, a in CATS_2025_SINKING]
    await db.levy_categories.insert_many(cats_a + cats_s)
    print(f"  2025: 1 levy, {len(cats_a)} admin + {len(cats_s)} sinking cats")

    # ── 2026 ─────────────────────────────────────────────────────────────────
    await db.annual_levies.insert_one(LEVY_2026)
    # status="partial_actual": budget_amount=proposed, actual_amount=YTD actual
    cats_a = [cat_doc("2026", "administrative", "partial_actual", n, b, a) for n, b, a in CATS_2026_ADMIN]
    cats_s = [cat_doc("2026", "sinking", "partial_actual", n, b, a) for n, b, a in CATS_2026_SINKING]
    await db.levy_categories.insert_many(cats_a + cats_s)
    print(f"  2026: 1 levy, {len(cats_a)} admin + {len(cats_s)} sinking cats (partial_actual)")

    return results


async def main():
    """Generated function header.

    Function: main
    Path: backend/seeds/finance_history.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncMongoClient(MONGO_URL)
    db = client[DB_NAME]

    print("=" * 65)
    print(f"East Gate — Finance History Seed  (DB: {DB_NAME})")
    print(f"DRY RUN: {DRY_RUN}")
    print("=" * 65)

    results = await seed(db)

    if not DRY_RUN:
        # Verification
        print("\n── Verification (balance chain) ───────────────────────────────")
        for yr in ["2021", "2022", "2023", "2024", "2025", "2026"]:
            doc = await db.annual_levies.find_one({"year": yr}, {"_id": 0})
            if not doc:
                print(f"  {yr}: NOT FOUND")
                continue
            af = doc["admin_fund"]
            sf = doc["sinking_fund"]
            n = await db.levy_categories.count_documents({"year": yr})
            print(f"  {yr} [{doc.get('data_origin', '?'):20s}]  "
                  f"Admin {af['opening_balance']:>12,.2f} → {af['closing_balance']:>12,.2f}  "
                  f"Sink {sf['opening_balance']:>12,.2f} → {sf['closing_balance']:>12,.2f}  "
                  f"cats={n}")

        print("\n── 5-year proposed vs actual ──────────────────────────────────")
        print(f"  {'Year':6s}  {'Admin Prop':>12s}  {'Admin Act':>12s}  "
              f"{'Sink Prop':>12s}  {'Sink Act':>12s}")
        for yr in ["2022", "2023", "2024", "2025"]:
            doc = await db.annual_levies.find_one({"year": yr}, {"_id": 0})
            if doc:
                ap = doc.get("proposed_admin_expenses", 0)
                aa = doc["admin_fund"]["total_expenses"]
                sp = doc.get("proposed_sinking_expenses", 0)
                sa = doc["sinking_fund"]["total_expenses"]
                print(f"  {yr:6s}  {ap:>12,.2f}  {aa:>12,.2f}  {sp:>12,.2f}  {sa:>12,.2f}")

        print("\n── Levy rates per UOE (owner-payable compatibility fields; fund totals remain ex-GST) ─")
        print(f"  {'Year':6s}  {'Admin/UOE/yr':>14s}  {'Sink/UOE/yr':>13s}  "
              f"{'Combined/UOE/yr':>16s}  {'Combined+GST/UOE/yr':>20s}  {'Quarterly incl GST':>20s}")
        for yr in ["2021", "2022", "2023", "2024", "2025", "2026"]:
            doc = await db.annual_levies.find_one({"year": yr}, {"_id": 0})
            if doc:
                ar = doc.get("admin_levy_per_uoe_annual", 0)
                sr = doc.get("sinking_levy_per_uoe_annual", 0)
                comb = ar + sr
                comb_gst = round(comb * 1.10, 4)
                qtr_gst = round(comb_gst / 4, 4)
                print(f"  {yr:6s}  {ar:>14.4f}  {sr:>13.4f}  "
                      f"{comb:>16.4f}  {comb_gst:>20.4f}  {qtr_gst:>20.4f}")

    print("\n✅  Done.")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
