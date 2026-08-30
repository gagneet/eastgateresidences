"""
Regression tests: get_levy_proposed_amounts()/get_levy_rate_breakdown() must read
admin_fund.proposed_amount_cents / sinking_fund.proposed_amount_cents.

gap_tolerant_financial_import.py writes both `levy_income` (legacy dollar,
"backward compatibility for existing readers") and `proposed_amount_cents`
(canonical, mandatory-precision) from the same source row — but only when the
source CSV/JSON row actually supplies a `proposed_amount` value. East Gate's
live 2026 annual_levies document (imported 2026-07-27 from a PDF financial
report) has proposed_amount_cents set but levy_income left null, which meant
get_levy_proposed_amounts() fell through every tier to 0.0 before this fix —
the owner dashboard's "Annual share" then silently substituted a partial
YTD-levied figure instead of the true annual budget.
"""
from utils.finance_helpers import get_levy_proposed_amounts, get_levy_rate_breakdown

# East Gate's live FY2026 shape: proposed_amount_cents set, levy_income null.
EAST_GATE_2026_DOC = {
    "building_id": "13195",
    "year": "2026",
    "admin_fund": {"proposed_amount_cents": 30988200, "levy_income": None},
    "sinking_fund": {"proposed_amount_cents": 9045900, "levy_income": None},
}

GST_SETTINGS = {"gst_registered": True, "gst_rate": 0.1}


def test_proposed_amounts_reads_cents_field_when_levy_income_is_null():
    admin, sinking = get_levy_proposed_amounts(EAST_GATE_2026_DOC, total_uoe=10000)
    assert admin == 309882.00
    assert sinking == 90459.00


def test_proposed_amounts_admin_expenses_still_outranks_cents_field():
    """Tier 1 (AGM-resolved proposed_admin_expenses) must still win over the
    fund-level proposed_amount_cents when both are present."""
    doc = {
        **EAST_GATE_2026_DOC,
        "proposed_admin_expenses": 340870.02,
        "proposed_sinking_expenses": 99504.90,
    }
    admin, sinking = get_levy_proposed_amounts(doc, total_uoe=10000)
    assert admin == 340870.02
    assert sinking == 99504.90


def test_proposed_amounts_cents_field_outranks_levy_income_when_both_present():
    """proposed_amount_cents is this repo's mandatory-precision canonical
    field; prefer it over the legacy dollar-float levy_income mirror."""
    doc = {
        "admin_fund": {"proposed_amount_cents": 30988200, "levy_income": 15000.0},
        "sinking_fund": {"proposed_amount_cents": 9045900, "levy_income": 5000.0},
    }
    admin, sinking = get_levy_proposed_amounts(doc, total_uoe=10000)
    assert admin == 309882.00
    assert sinking == 90459.00


def test_proposed_amounts_falls_back_to_levy_income_when_cents_field_absent():
    """Older documents that never got the cents-precision backfill must keep
    working exactly as before."""
    doc = {
        "admin_fund": {"levy_income": 50000.0},
        "sinking_fund": {"levy_income": 20000.0},
    }
    admin, sinking = get_levy_proposed_amounts(doc, total_uoe=1000)
    assert admin == 50000.0
    assert sinking == 20000.0


def test_rate_breakdown_applies_gst_to_cents_sourced_total():
    """End-to-end reproduction of the live East Gate FY2026 case: the fix
    must flow through get_levy_rate_breakdown's has_canonical_totals gate
    (not just get_levy_proposed_amounts in isolation) and apply the 10% GST
    multiplier, landing on the known-correct budget figures from the
    building's proposed Admin/Sinking budget."""
    breakdown = get_levy_rate_breakdown(EAST_GATE_2026_DOC, settings_doc=GST_SETTINGS, total_uoe=10000)
    assert breakdown["gst_registered"] is True
    assert round(breakdown["admin_payable_annual"] * 10000, 2) == 340870.20
    assert round(breakdown["sinking_payable_annual"] * 10000, 2) == 99504.90


def test_rate_breakdown_has_canonical_totals_detects_cents_field_alone():
    """Without this, has_canonical_totals stays False for a doc that only has
    proposed_amount_cents (levy_income null, no proposed_*_expenses) and the
    whole fix in get_levy_proposed_amounts is unreachable dead code."""
    breakdown = get_levy_rate_breakdown(EAST_GATE_2026_DOC, settings_doc=GST_SETTINGS, total_uoe=10000)
    assert breakdown["admin_payable_annual"] > 0
    assert breakdown["sinking_payable_annual"] > 0
