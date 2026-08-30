"""Workstream B — derived payment (receipt) generator (spec §4b).

Pure/synchronous generator: builds a payment plan from a charge plan with paid == charged by
construction. These tests need no DB.
"""
from datetime import date
from uuid import uuid4

from services.reconstruction_generators.historical_levy_charge_generator import (
    LevyChargeLine,
    LevyChargePlan,
)
from services.reconstruction_generators.historical_levy_payment_generator import (
    RECONSTRUCTED_SOURCE_TAG,
    build_payment_plan_from_charge_plan,
    receipt_dedup_key,
    summarize_by_year_fund_period,
)


def _charge_line(*, unit="UA042", lot_id=None, year=2024, fund="admin", period="Q1",
                 quarter_no=1, total_cents=40250, issue=date(2024, 1, 1), due=date(2024, 1, 15)):
    return LevyChargeLine(
        unit_number=unit,
        lot_id=lot_id or uuid4(),
        owner_party_id=uuid4(),
        owner_resolution_status="resolved",
        year=year,
        fund_type=fund,
        levy_type="ordinary",
        period=period,
        quarter_no=quarter_no,
        issue_date=issue,
        due_date=due,
        uoe=161.0,
        total_uoe=10000.0,
        principal_cents=int(round(total_cents / 1.1)),
        gst_cents=total_cents - int(round(total_cents / 1.1)),
        total_cents=total_cents,
        source_annual_control="annual_levies.admin (year=2024)",
    )


def _charge_plan(lines, *, planned_not_posted=None):
    return LevyChargePlan(
        building_id="13195",
        from_year=2024,
        to_year=2024,
        as_of_date=date(2024, 6, 30),
        lines=lines,
        planned_not_posted=planned_not_posted or [],
    )


def test_paid_equals_charged_by_construction():
    """Every derived receipt amount == its source charge total_cents (the #4 GST-mismatch fix)."""
    lines = [
        _charge_line(period="Q1", quarter_no=1, total_cents=40250),
        _charge_line(period="Q2", quarter_no=2, total_cents=40251),  # odd cent — must be copied exactly
    ]
    plan = build_payment_plan_from_charge_plan(_charge_plan(lines))

    assert len(plan.lines) == 2
    for charge, payment in zip(lines, plan.lines):
        assert payment.amount_cents == charge.total_cents
    assert plan.total_amount_cents == 40250 + 40251
    assert all(p.source_tag == RECONSTRUCTED_SOURCE_TAG for p in plan.lines)


def test_payment_dated_before_due_never_before_issue():
    plan = build_payment_plan_from_charge_plan(
        _charge_plan([_charge_line(issue=date(2024, 1, 1), due=date(2024, 1, 15))]),
        payment_lead_days=3,
    )
    p = plan.lines[0]
    assert p.payment_date == date(2024, 1, 12)  # due - 3 days
    assert p.payment_date < date(2024, 1, 15)

    # When lead would precede issue_date, clamp to issue_date (issue == due in this repo's data).
    plan2 = build_payment_plan_from_charge_plan(
        _charge_plan([_charge_line(issue=date(2024, 1, 15), due=date(2024, 1, 15))]),
        payment_lead_days=3,
    )
    assert plan2.lines[0].payment_date == date(2024, 1, 15)


def test_existing_receipt_is_skipped_not_double_posted():
    """Spec §4a #10 — applying to a building that already has a receipt for (lot, year, fund, period)
    must skip it, never double-post the paid side."""
    lot = uuid4()
    line = _charge_line(lot_id=lot, year=2024, fund="admin", quarter_no=1)
    existing = frozenset({receipt_dedup_key(lot, 2024, "admin", 1)})

    plan = build_payment_plan_from_charge_plan(_charge_plan([line]), existing_receipt_keys=existing)

    assert plan.lines == []
    assert plan.skipped_existing_receipt == 1
    assert plan.total_amount_cents == 0


def test_planned_not_posted_charges_get_no_payment():
    """Future/not-yet-due periods (planned_not_posted) must NOT get a fabricated payment."""
    posted = _charge_line(period="Q1", quarter_no=1)
    future = _charge_line(period="Q4", quarter_no=4, due=date(2024, 10, 15))
    plan = build_payment_plan_from_charge_plan(_charge_plan([posted], planned_not_posted=[future]))
    assert len(plan.lines) == 1
    assert plan.lines[0].period == "Q1"


def test_zero_or_negative_charge_produces_no_receipt():
    plan = build_payment_plan_from_charge_plan(_charge_plan([_charge_line(total_cents=0)]))
    assert plan.lines == []
    assert plan.total_amount_cents == 0


def test_summary_groups_by_year_fund_period():
    lines = [
        _charge_line(period="Q1", quarter_no=1, fund="admin", total_cents=40250),
        _charge_line(period="Q1", quarter_no=1, fund="sinking", total_cents=10000, unit="UA043"),
    ]
    plan = build_payment_plan_from_charge_plan(_charge_plan(lines))
    summary = summarize_by_year_fund_period(plan)
    assert len(summary) == 2
    admin = next(g for g in summary if g["fund_type"] == "admin")
    assert admin["receipt_count"] == 1
    assert admin["total_cents"] == 40250
