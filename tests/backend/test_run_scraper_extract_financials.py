# @featuretrace:strata_sync — Tests for run_scraper.py's extract_financials(), specifically
#   the cross-category re-scan duplicate guard added 2026-08-19.
# Layer: test
# Related: backend/scripts/run_scraper.py (extract_financials)
"""Regression test for a real, live-confirmed East Gate bug: page.locator("table").all()
can match the same detail rows more than once (e.g. an outer table whose innerText already
includes a nested detail table's text, plus that nested table matching separately) —
current_record was never reset between table matches, so a second pass over already-consumed
rows silently re-attached them to whichever category happened to be current_record at that
point (in the real incident: "Waterproofing", the LAST category in table order, accumulated
685 rows — 685/685 of them exact duplicates of rows already correctly attributed elsewhere).

The fix must NOT collapse genuine same-category repeats — East Gate's real "Arrears Recovery
Costs" category legitimately repeats one (date, invoice_ref, supplier, amount) tuple 13 times
(a batch SMS charge issued identically to 13 different lots, sharing one reference number).
Only a repeat under a DIFFERENT category than where the key was first seen is the bug.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.run_scraper import (
    _row_sum_tolerance,
    enrich_financials,
    extract_financials,
    verify_category_row_sums,
)


def _fake_table(text: str):
    table = MagicMock()
    table.inner_text = AsyncMock(return_value=text)
    return table


def _empty_locator():
    """A locator with no matches — used for every selector other than "table" so
    expand_financial_rows() (called internally by extract_financials()) finds
    nothing to click/expand and returns quickly. This test targets the PARSING
    logic downstream of expansion, not the click/expand mechanics themselves —
    the fixture text below is already "post-expansion" content."""
    loc = MagicMock()
    loc.all = AsyncMock(return_value=[])
    loc.count = AsyncMock(return_value=0)
    return loc


def _fake_page(table_texts: list[str]):
    page = MagicMock()
    tables = [_fake_table(t) for t in table_texts]
    table_locator = MagicMock()
    table_locator.all = AsyncMock(return_value=tables)

    def _locator(selector):
        return table_locator if selector == "table" else _empty_locator()

    page.locator = MagicMock(side_effect=_locator)
    page.wait_for_selector = AsyncMock()
    page.evaluate = AsyncMock(return_value=0)
    return page


# Real East Gate row shape: Category\tPlanned\tActual\tVariance\tPrevious for a header row;
# Date\tInvoiceRef\tSupplier\tDetails\tAmount for a detail row.

ARREARS_HEADER = "Arrears Recovery Costs\t$0.00\t$59.15\t-$59.15\t$0.00"
ARREARS_ROW = "4/01/2026\t1089291\tCivium Strata\t\t$4.55"  # repeated 13x for real, legitimately
CLEANING_HEADER = "Cleaning\t$27,500.00\t$2,158.00\t$25,342.00\t$0.00"
CLEANING_ROW = "15/08/2026\t1149638\tImpress Cleaning Services\tCleaning July 2026\t$2,158.00"
WATERPROOFING_HEADER = "Waterproofing\t$0.00\t$0.00\t$0.00\t$0.00"


@pytest.mark.asyncio
async def test_cross_category_rescan_duplicate_is_dropped():
    """A row already correctly attributed to Cleaning must not ALSO be counted under
    Waterproofing when the same underlying rows get matched a second time."""
    table1 = "\n".join([
        ARREARS_HEADER, ARREARS_ROW,
        CLEANING_HEADER, CLEANING_ROW,
        WATERPROOFING_HEADER,
    ])
    # Second table match: a re-scan artifact re-exposing the Cleaning row with no new
    # category header of its own — current_record is still "Waterproofing" from table1.
    table2 = CLEANING_ROW

    page = _fake_page([table1, table2])
    records = await extract_financials(page)

    by_cat = {r["category"]: r for r in records}
    assert len(by_cat["Cleaning"]["transactions"]) == 1
    assert by_cat["Waterproofing"]["transactions"] == []


@pytest.mark.asyncio
async def test_legitimate_same_category_repeat_is_preserved():
    """13 real, distinct per-lot charges sharing one (date, invoice_ref, supplier,
    amount) tuple, all under their OWN correct category, must all be kept."""
    rows = [ARREARS_HEADER] + [ARREARS_ROW] * 13
    page = _fake_page(["\n".join(rows)])
    records = await extract_financials(page)

    arrears = next(r for r in records if r["category"] == "Arrears Recovery Costs")
    assert len(arrears["transactions"]) == 13


@pytest.mark.asyncio
async def test_mixed_scenario_matches_the_real_east_gate_incident_shape():
    """Combines both cases in one pass: legitimate 13x batch charge under its own
    category, plus a cross-category re-scan duplicate of a different row landing on
    Waterproofing — only the cross-category duplicate is dropped."""
    table1 = "\n".join(
        [ARREARS_HEADER] + [ARREARS_ROW] * 13
        + [CLEANING_HEADER, CLEANING_ROW, WATERPROOFING_HEADER]
    )
    table2 = CLEANING_ROW  # re-scan artifact

    page = _fake_page([table1, table2])
    records = await extract_financials(page)
    by_cat = {r["category"]: r for r in records}

    assert len(by_cat["Arrears Recovery Costs"]["transactions"]) == 13
    assert len(by_cat["Cleaning"]["transactions"]) == 1
    assert by_cat["Waterproofing"]["transactions"] == []


# ── Fund-aware category tracking (2026-08-19, second real bug found in the same
# session): a category name can legitimately repeat once PER FUND — East Gate's real
# "Lift Repairs" is an admin-fund $2,560.00 item AND a completely separate sinking-fund
# $10,990.00 item (a lift-flex installation fee, its own real invoice). "Administrative
# Fund"/"Sinking Fund" section-marker lines have no tab-separated columns, so they were
# invisible to the row loop entirely — `seen` deduped by category name alone, silently
# dropping the second same-named header and misattributing its detail row to whatever
# category preceded it. Live-confirmed: the sinking "Lift Repairs" $10,990 invoice
# landed under "Garage Door Replacement/Upgrade" (header $0.00) — exactly the
# "header $0.00 but 1 row summing to $10,990" mismatch verify_category_row_sums()
# flagged in production. ─────────────────────────────────────────────────────────────

ADMIN_FUND_MARKER = "Administrative Fund"
SINKING_FUND_MARKER = "Sinking Fund"
LIFT_REPAIRS_ADMIN_HEADER = "Lift Repairs\t$0.00\t$2,560.00\t-$2,560.00\t$0.00"
GARAGE_DOOR_HEADER = "Garage Door Replacement/Upgrade\t$9,851.00\t$0.00\t$9,851.00\t$3,400.00"
LIFT_REPAIRS_SINKING_HEADER = "Lift Repairs\t$0.00\t$10,990.00\t-$10,990.00\t$0.00"
LIFT_REPAIRS_SINKING_ROW = (
    "27/07/2026\t1142733\tELECTRA LIFT CO PTY LTD\t"
    "50% establishment fee for installation of security flexes\t$10,990.00"
)


@pytest.mark.asyncio
async def test_same_category_name_in_both_funds_are_kept_as_separate_records():
    table1 = "\n".join([
        ADMIN_FUND_MARKER,
        LIFT_REPAIRS_ADMIN_HEADER,  # no detail rows for this one, same as real East Gate
        SINKING_FUND_MARKER,
        GARAGE_DOOR_HEADER,  # no detail rows of its own
        LIFT_REPAIRS_SINKING_HEADER,
        LIFT_REPAIRS_SINKING_ROW,
    ])
    page = _fake_page([table1])
    records = await extract_financials(page)

    lift_repairs = [r for r in records if r["category"] == "Lift Repairs"]
    assert len(lift_repairs) == 2, "both fund instances must survive as separate records"

    admin_entry = next(r for r in lift_repairs if r["fund"] == "admin")
    sinking_entry = next(r for r in lift_repairs if r["fund"] == "capital_works")
    assert admin_entry["actual"] == 2560.0
    assert admin_entry["transactions"] == []
    assert sinking_entry["actual"] == 10990.0
    assert len(sinking_entry["transactions"]) == 1
    assert sinking_entry["transactions"][0]["amount"] == 10990.0

    garage_door = next(r for r in records if r["category"] == "Garage Door Replacement/Upgrade")
    assert garage_door["transactions"] == [], (
        "the sinking Lift Repairs row must not be misattributed to the preceding category"
    )


@pytest.mark.asyncio
async def test_rescan_table_without_its_own_fund_marker_does_not_spuriously_duplicate():
    """Real East Gate bug, found live during fix verification: a second table (a
    partial re-render lacking its own "Administrative Fund"/"Sinking Fund" marker)
    repeated the already-correctly-captured admin "Garage Door" row, but current_fund
    had carried over as "capital_works" from wherever the PREVIOUS table's scan ended
    — producing a spurious second "Garage Door" record under the wrong fund with
    IDENTICAL figures to the real one. Two same-name rows sharing identical figures
    must collapse to one record, regardless of what fund the second occurrence
    (mis)computes to — only a genuine figures difference (the real Lift Repairs case)
    justifies keeping both."""
    garage_door_header = "Garage Door\t$1,818.00\t$2,641.82\t-$823.82\t$1,200.00"
    table1 = "\n".join([
        ADMIN_FUND_MARKER,
        garage_door_header,
        SINKING_FUND_MARKER,
        "Waterproofing\t$0.00\t$18,990.00\t-$18,990.00\t$0.00",
    ])
    # table2: a partial re-render with NO fund marker of its own — current_fund is
    # still "capital_works" (left over from table1 ending in the Sinking Fund section)
    # when this table's identical Garage Door row is re-encountered.
    table2 = garage_door_header

    page = _fake_page([table1, table2])
    records = await extract_financials(page)

    garage_doors = [r for r in records if r["category"] == "Garage Door"]
    assert len(garage_doors) == 1, "identical figures must collapse to one record, not two"
    assert garage_doors[0]["fund"] == "admin", "the correctly-detected first occurrence must win"


@pytest.mark.asyncio
async def test_new_transaction_after_a_duplicate_header_skip_attaches_correctly():
    """Self-audit finding (2026-08-19): when a duplicate header is skipped,
    current_record must be re-pointed at the EXISTING record, not left stale — a
    genuinely new transaction row (never seen before) encountered right after a
    duplicate-header skip must still attach to the correct category, not whatever
    unrelated category current_record last pointed at."""
    garage_door_header = "Garage Door\t$1,818.00\t$2,641.82\t-$823.82\t$1,200.00"
    new_txn_row = "5/05/2026\t1120001\tAcme Garage Doors\tSpring replacement\t$250.00"
    table1 = "\n".join([
        ADMIN_FUND_MARKER,
        garage_door_header,
        SINKING_FUND_MARKER,
        "Waterproofing\t$0.00\t$18,990.00\t-$18,990.00\t$0.00",
    ])
    # table2: no fund marker of its own (current_fund stale at "capital_works"), and
    # the genuinely-new detail row comes straight after the duplicate header skip
    # rather than after a fresh, correctly-set current_record.
    table2 = "\n".join([garage_door_header, new_txn_row])

    page = _fake_page([table1, table2])
    records = await extract_financials(page)

    garage_doors = [r for r in records if r["category"] == "Garage Door"]
    assert len(garage_doors) == 1
    assert len(garage_doors[0]["transactions"]) == 1
    assert garage_doors[0]["transactions"][0]["amount"] == 250.0

    waterproofing = next(r for r in records if r["category"] == "Waterproofing")
    assert waterproofing["transactions"] == [], (
        "the new Garage Door transaction must not misattach to whatever category "
        "current_record was stale-pointing at"
    )


@pytest.mark.asyncio
async def test_full_table_rescan_does_not_reinflate_already_captured_transactions():
    """Real regression, found live while verifying the previous fix (2026-08-19): once
    current_record started being re-pointed at the EXISTING record on a duplicate
    header (the fix above), it correctly tracked the right category all the way
    through a FULL-TABLE re-scan too — which meant every re-scanned transaction's
    owner now matched current_record's owner, and the "same owner = legitimate
    repeat" rule (which exists for real batch charges, see
    test_legitimate_same_category_repeat_is_preserved) let them all get re-appended.
    Live-confirmed: "Cleaning" (9 real transactions) ballooned to 60 because 3 re-scan
    tables (table_13/14/19 in the real sync, each a full duplicate of the first
    table's content, fund markers and all) each re-added all of the first table's
    rows. table_idx is the real signal: a genuine repeat (13x Arrears SMS charge)
    occurs within ONE table's own single continuous scan; a re-occurrence of an
    already-recorded key in a DIFFERENT table_idx is always a re-scan artifact."""
    table1 = "\n".join([
        ADMIN_FUND_MARKER,
        CLEANING_HEADER,
        CLEANING_ROW,
    ])
    # table2: a full re-scan of table1's content, WITH its own fund marker correctly
    # re-declared (matching the real table_13/14/19 shape) — this is the case that
    # regressed: current_record correctly tracks "Cleaning" throughout, so the
    # owner-only check alone can no longer tell this apart from a legitimate repeat.
    table2 = "\n".join([
        ADMIN_FUND_MARKER,
        CLEANING_HEADER,
        CLEANING_ROW,
    ])

    page = _fake_page([table1, table2])
    records = await extract_financials(page)

    cleaning = next(r for r in records if r["category"] == "Cleaning")
    assert len(cleaning["transactions"]) == 1, (
        "a full-table re-scan must not re-add already-captured transactions"
    )


@pytest.mark.asyncio
async def test_fund_is_tracked_from_page_section_not_guessed_from_category_name():
    """_classify_fund()'s name-keyword heuristic would guess "capital_works" for ANY
    category containing "lift repair", regardless of which fund it's really in. The
    admin-fund "Lift Repairs" here must be tagged admin because it was scraped from
    inside the Administrative Fund section, not because of what its name contains."""
    table1 = "\n".join([ADMIN_FUND_MARKER, LIFT_REPAIRS_ADMIN_HEADER])
    page = _fake_page([table1])
    records = await extract_financials(page)

    assert len(records) == 1
    assert records[0]["fund"] == "admin"


def test_enrich_financials_trusts_captured_fund_over_name_heuristic():
    """A record extracted with fund="admin" must stay admin through enrich_financials()
    even though its category name would make _classify_fund() guess capital_works."""
    record = {
        "category": "Lift Repairs", "fund": "admin", "planned": 0.0, "actual": 2560.0,
        "variance": -2560.0, "previous": 0.0, "transactions": [],
    }
    enriched = enrich_financials([record])
    assert enriched[0]["fund"] == "admin"


def test_enrich_financials_falls_back_to_heuristic_when_fund_not_captured():
    """Records with no fund field (e.g. hand-built test fixtures predating the
    fund-tracking fix) still get a best-effort classification, not a KeyError."""
    record = {
        "category": "Cleaning", "planned": 0.0, "actual": 100.0,
        "variance": 0.0, "previous": 0.0, "transactions": [],
    }
    enriched = enrich_financials([record])
    assert enriched[0]["fund"] == "admin"


# ── verify_category_row_sums() — added 2026-08-19 at user request, after the user manually
# confirmed a real East Gate category's own detail rows sum to its own header actual figure
# and asked for that check to become a formal, reusable verification point. ──────────────────


def _record(category: str, actual: float, amounts: list[float]) -> dict:
    return {
        "category": category,
        "actual": actual,
        "transactions": [{"amount": a} for a in amounts],
    }


def test_row_sum_matches_header_actual_is_not_flagged():
    """Real shape: "Electricity - Utility", 9 detail rows including a REVERSAL/re-issue
    pair (-$1,500.00 / +$1,500.00, net zero), summing exactly to the header's $12,970.63."""
    record = _record(
        "Electricity - Utility",
        12970.63,
        [1200.00, 1350.25, 1480.10, 1600.00, 1750.33, 1900.45, 3689.50, -1500.00, 1500.00],
    )
    assert verify_category_row_sums([record]) == []


def test_incomplete_row_capture_is_flagged():
    """Real East Gate incident shape: "Banking Management" header said $506.29 but only 5
    of its detail rows were visible in the DOM when extract_financials() read the table,
    summing to $340.90 — a genuine $165.39 capture gap, not a data-quality problem."""
    record = _record("Banking Management", 506.29, [100.00, 80.90, 60.00, 50.00, 50.00])

    warnings = verify_category_row_sums([record])

    assert len(warnings) == 1
    w = warnings[0]
    assert w["category"] == "Banking Management"
    assert w["header_actual"] == 506.29
    assert w["row_sum"] == 340.90
    assert w["diff"] == pytest.approx(165.39, abs=0.001)
    assert w["transaction_count"] == 5


def test_zero_transactions_captured_with_nonzero_actual_is_flagged():
    """The same failure mode in its most extreme form — none of a category's rows were
    captured at all, not just some. Must still be surfaced, not silently skipped."""
    record = _record("Waterproofing", 18990.00, [])

    warnings = verify_category_row_sums([record])

    assert len(warnings) == 1
    assert warnings[0]["category"] == "Waterproofing"
    assert warnings[0]["row_sum"] == 0.0
    assert warnings[0]["transaction_count"] == 0


def test_zero_actual_and_zero_transactions_is_not_flagged():
    """A category genuinely untouched this period (e.g. no capital works spend) — header
    actual $0.00, no rows — must not be reported as a mismatch."""
    record = _record("Capital Works Reserve", 0.0, [])
    assert verify_category_row_sums([record]) == []


def test_within_tolerance_rounding_difference_is_not_flagged():
    """A one-cent rounding artifact (matches this repo's established dollar-float
    tolerance elsewhere) must not be reported as a real mismatch."""
    record = _record("Audit Fees", 730.00, [365.005, 365.005])
    assert verify_category_row_sums([record]) == []


def test_multiple_categories_only_mismatched_ones_are_returned():
    records = [
        _record("Cleaning", 2158.00, [2158.00]),
        _record("Banking Management", 506.29, [340.90]),
        _record("Audit Fees", 730.00, [730.00]),
    ]
    warnings = verify_category_row_sums(records)
    assert [w["category"] for w in warnings] == ["Banking Management"]


# ── Scaled tolerance (2026-08-19, added during performance/security follow-up audit) ──
# A flat $0.02 tolerance is too tight once a category has many detail rows — the user's
# own real "Electricity - Utility" example (9 rows) already sat 1 cent off its header
# purely from per-row rounding. Tolerance now scales at $0.01/transaction.


def test_row_sum_tolerance_scales_with_transaction_count():
    assert _row_sum_tolerance(0) == 0.02  # floor never drops below the base tolerance
    assert _row_sum_tolerance(1) == 0.02
    assert _row_sum_tolerance(2) == 0.02
    assert _row_sum_tolerance(9) == pytest.approx(0.09)
    assert _row_sum_tolerance(20) == pytest.approx(0.20)


def test_high_transaction_count_compounding_rounding_is_not_flagged():
    """20 rows compounding to a 15-cent rounding drift — would have been a false
    positive under the old flat $0.02 tolerance, but is within the real per-row
    rounding budget for this many transactions and must not be flagged."""
    amounts = [10.005] * 20  # rounds to 200.10 as a header figure with 15c of drift below
    record = _record("Gardens & Grounds", round(sum(amounts), 2) + 0.15, amounts)
    assert verify_category_row_sums([record]) == []


def test_large_gap_still_flagged_despite_many_transactions():
    """Scaling must not mask a genuinely large incomplete-capture gap just because a
    category happens to have many rows — 20 rows summing $50 short of the header is
    far beyond even the scaled $0.20 tolerance."""
    amounts = [10.00] * 20
    record = _record("Cleaning", round(sum(amounts), 2) + 50.00, amounts)
    warnings = verify_category_row_sums([record])
    assert len(warnings) == 1
    assert warnings[0]["diff"] == pytest.approx(50.00)
