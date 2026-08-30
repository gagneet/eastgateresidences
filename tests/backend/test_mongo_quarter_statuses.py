"""
Tests for compute_mongo_quarter_statuses() (utils/finance_helpers.py).

Regression coverage for the MongoDB path of GET /finance/unit-dashboard-overview/{unit},
which previously hardcoded every quarter's status to the literal string "unpaid"
regardless of due date or payment state — silently disabling overdue-levy detection
for any building still on the Mongo read path (Postgres already computed real
per-quarter status). This approximates real status from the annual totals Mongo does
track (total_levied, total_paid) via an even-instalment split + earliest-first waterfall,
mirroring the classification rules already used on the PostgreSQL path.
"""

from datetime import date

from utils.finance_helpers import compute_mongo_quarter_statuses

TODAY = date(2026, 7, 3)

SCHEDULE_4Q = [
    {"quarter": "Q1", "due_date": "2026-03-31"},
    {"quarter": "Q2", "due_date": "2026-06-30"},
    {"quarter": "Q3", "due_date": "2026-09-30"},
    {"quarter": "Q4", "due_date": "2026-12-31"},
]


def test_empty_schedule_returns_empty_list():
    assert compute_mongo_quarter_statuses([], 4000.0, 0.0, today=TODAY) == []


def test_no_payments_flags_past_due_quarters_as_overdue_not_unpaid():
    quarters = compute_mongo_quarter_statuses(SCHEDULE_4Q, 4000.0, 0.0, today=TODAY)

    # Q1 (Mar 31) and Q2 (Jun 30) are in the past relative to TODAY (Jul 3) — must be
    # "overdue", not the previous hardcoded "unpaid".
    assert quarters[0]["status"] == "overdue"
    assert quarters[1]["status"] == "overdue"
    # Q3/Q4 are still in the future — not yet due, not "unpaid" (which implies a
    # missed obligation; these haven't been invoiced yet).
    assert quarters[2]["status"] == "not_yet_due"
    assert quarters[3]["status"] == "not_yet_due"
    # Instalments sum exactly to total_levied.
    assert round(sum(q["amount_due"] for q in quarters), 2) == 4000.0


def test_full_payment_of_first_quarter_only():
    quarters = compute_mongo_quarter_statuses(SCHEDULE_4Q, 4000.0, 1000.0, today=TODAY)

    assert quarters[0]["status"] == "paid"
    assert quarters[0]["amount_paid"] == 1000.0
    assert quarters[0]["outstanding"] == 0.0
    # Q2 is past-due and unpaid → overdue.
    assert quarters[1]["status"] == "overdue"
    assert quarters[1]["amount_paid"] == 0.0


def test_partial_payment_applied_to_earliest_quarter_first():
    quarters = compute_mongo_quarter_statuses(SCHEDULE_4Q, 4000.0, 400.0, today=TODAY)

    assert quarters[0]["status"] == "partial"
    assert quarters[0]["amount_paid"] == 400.0
    assert quarters[0]["outstanding"] == 600.0
    # Nothing left over for Q2, which is also past due → overdue, zero paid.
    assert quarters[1]["status"] == "overdue"
    assert quarters[1]["amount_paid"] == 0.0


def test_overpayment_covers_only_due_quarters_as_paid():
    # GAP-FIN-030 Root Cause D: an annual total_paid large enough to cover every
    # quarter must NOT retroactively mark quarters that aren't due yet (Q3/Q4,
    # relative to TODAY=2026-07-03) as "paid" — only Q1/Q2 (already due) actually
    # get payment applied; Q3/Q4 must show not_yet_due with zero amount_paid,
    # regardless of how large the remaining balance is.
    quarters = compute_mongo_quarter_statuses(SCHEDULE_4Q, 4000.0, 5000.0, today=TODAY)

    assert quarters[0]["status"] == "paid"
    assert quarters[1]["status"] == "paid"
    assert quarters[2]["status"] == "not_yet_due"
    assert quarters[3]["status"] == "not_yet_due"
    assert quarters[2]["amount_paid"] == 0.0
    assert quarters[3]["amount_paid"] == 0.0
    assert quarters[2]["outstanding"] == quarters[2]["amount_due"]
    assert quarters[3]["outstanding"] == quarters[3]["amount_due"]
    assert quarters[0]["outstanding"] == 0.0
    assert quarters[1]["outstanding"] == 0.0


def test_future_quarter_never_marked_overdue():
    # A single quarter due well in the future, unpaid — must be "not_yet_due",
    # never "overdue" and never silently marked "paid" from a large annual total.
    schedule = [{"quarter": "Q1", "due_date": "2027-03-31"}]
    quarters = compute_mongo_quarter_statuses(schedule, 1000.0, 0.0, today=TODAY)
    assert quarters[0]["status"] == "not_yet_due"

    quarters_overpaid = compute_mongo_quarter_statuses(schedule, 1000.0, 5000.0, today=TODAY)
    assert quarters_overpaid[0]["status"] == "not_yet_due"
    assert quarters_overpaid[0]["amount_paid"] == 0.0


def test_near_fully_paid_annual_total_does_not_collect_future_quarters():
    # Regression for the exact reported symptom: East Gate FY2026, annual total_paid
    # close to (but not exceeding) total_levied — every quarter the waterfall reaches
    # showed identical Levied == Collected, including Q3 (Sep) and Q4 (Dec), which
    # have not occurred yet as of TODAY (2026-07-03, and equally as of the live report
    # date 2026-08-02). Only quarters actually due may show amount_paid > 0.
    quarters = compute_mongo_quarter_statuses(SCHEDULE_4Q, 220187.56, 219187.56, today=TODAY)

    q3, q4 = quarters[2], quarters[3]
    assert q3["status"] == "not_yet_due"
    assert q4["status"] == "not_yet_due"
    assert q3["amount_paid"] != q3["amount_due"]
    assert q4["amount_paid"] != q4["amount_due"]
    assert q3["amount_paid"] == 0.0
    assert q4["amount_paid"] == 0.0


def test_entries_without_due_date_are_dropped_not_crashed_on():
    schedule = [{"quarter": "Q1"}, *SCHEDULE_4Q]
    quarters = compute_mongo_quarter_statuses(schedule, 4000.0, 0.0, today=TODAY)
    assert len(quarters) == 4
    assert [q["quarter"] for q in quarters] == ["Q1", "Q2", "Q3", "Q4"]
    assert quarters[0]["due_date"] == "2026-03-31"


def test_schedule_out_of_order_is_sorted_by_due_date():
    shuffled = [SCHEDULE_4Q[2], SCHEDULE_4Q[0], SCHEDULE_4Q[3], SCHEDULE_4Q[1]]
    quarters = compute_mongo_quarter_statuses(shuffled, 4000.0, 1000.0, today=TODAY)
    assert [q["due_date"] for q in quarters] == [
        "2026-03-31", "2026-06-30", "2026-09-30", "2026-12-31",
    ]
    assert quarters[0]["status"] == "paid"
