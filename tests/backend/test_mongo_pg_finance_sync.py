"""
# @featuretrace:financial_core — guards the Mongo->Postgres finance sync's matching rule.
# Layer: test
# Data flow: fixture payment/receipt lists -> build_sync_plan / measure_drift (building-scoped).
# Related: backend/services/mongo_pg_finance_sync.py
#          backend/scripts/data_repair/sync_mongo_payments_to_postgres.py

Test Suite: Mongo -> Postgres finance sync
==========================================

The gap being closed: `outbox_relay` runs Postgres -> Mongo (an audit log), and
nothing has ever run the other way — while Mongo is the store that actually
SERVES finance for East Gate. So every live write landed in Mongo and Postgres
drifted silently, by $26,042.77 across 32 of 87 lots by 2026-08-28.

The rule these tests protect is that the sync closes that gap **through Demo
Bank**, never by mirroring rows into `finance.*`. A direct mirror would
manufacture financial facts that never passed intake — the failure already on
record twice here ($415,031.21 staged vs $1,502,451.24 GL-posted, diverging
3.6x because neither pipeline checked the other).

Run with:
    backend/venv/bin/python3 -m pytest tests/backend/test_mongo_pg_finance_sync.py -q
"""

from services.mongo_pg_finance_sync import (
    build_sync_plan,
    measure_drift,
    to_demo_bank_candidates,
)

BUILDING_ID = "13195"


def _payment(unit="UA013", amount=902.77, date="2026-08-19", status="confirmed", **kw):
    return {
        "id": kw.pop("id", "pay-1"),
        "building_id": BUILDING_ID,
        "unit_number": unit,
        "amount": amount,
        "payment_date": date,
        "status": status,
        "transaction_origin": kw.pop("origin", "reconstructed_historical"),
        "payment_reference": kw.pop("ref", "REF-1"),
        **kw,
    }


def _receipt(unit="UA013", cents=90277, date="2026-08-19"):
    return {"unit_number": unit, "amount_cents": cents, "received_on": date}


class TestMatching:
    def test_a_payment_already_in_postgres_is_not_re_emitted(self):
        plan = build_sync_plan([_payment()], [_receipt()])
        assert plan.already_present == 1
        assert plan.missing_in_pg == []

    def test_a_payment_absent_from_postgres_is_reported(self):
        plan = build_sync_plan([_payment()], [])
        assert len(plan.missing_in_pg) == 1
        assert plan.total_cents == 90277

    def test_dollar_floats_convert_to_cents_once_at_the_boundary(self):
        """`levy_payments.amount` is a dollar FLOAT — a documented, still-live
        violation of the cents-only rule. The conversion must happen here and
        match the ledger key exactly, or every row looks 'missing'."""
        plan = build_sync_plan([_payment(amount=1222.03)], [_receipt(cents=122203)])
        assert plan.already_present == 1, "float dollars did not match integer cents"

    def test_matching_is_a_multiset_not_a_set(self):
        """Two genuine payments of the same amount on the same day are two facts.

        If PG holds one, one is still missing. Treating the key as a set would
        under-report exactly the duplicate-shaped drift this exists to find.
        """
        plan = build_sync_plan([_payment(id="a"), _payment(id="b")], [_receipt()])
        assert plan.already_present == 1
        assert len(plan.missing_in_pg) == 1

    def test_unit_case_and_whitespace_do_not_create_false_gaps(self):
        plan = build_sync_plan([_payment(unit=" ua013 ")], [_receipt(unit="UA013")])
        assert plan.already_present == 1

    def test_unconfirmed_payments_are_never_emitted(self):
        """A pending or rejected Mongo row is not a financial fact yet.

        Emitting it as intake would CREATE one — money invented from a draft.
        """
        plan = build_sync_plan(
            [_payment(status="pending"), _payment(status="rejected")], []
        )
        assert plan.missing_in_pg == []
        assert plan.skipped_unconfirmed == 2

    def test_a_payment_with_no_unit_is_skipped_not_guessed(self):
        plan = build_sync_plan([_payment(unit=None)], [])
        assert plan.missing_in_pg == []
        assert plan.skipped_no_unit == 1


class TestCandidateShape:
    def test_candidates_require_review_and_never_post(self):
        plan = build_sync_plan([_payment()], [])
        cand = to_demo_bank_candidates(plan, BUILDING_ID)[0]
        assert cand["requires_review"] is True
        assert cand["sync_status"] == "pending"
        assert cand["building_id"] == BUILDING_ID

    def test_source_type_is_distinguishable_forever_after(self):
        """Four disjoint transaction_origin vocabularies already exist here.

        An ambiguous fifth would make provenance unanswerable, so this path
        names itself.
        """
        plan = build_sync_plan([_payment()], [])
        assert to_demo_bank_candidates(plan, BUILDING_ID)[0]["source_type"] == "mongo_pg_backfill"

    def test_idempotency_key_is_stable_for_the_same_fact(self):
        """A re-run must not create a second candidate for the same payment,
        even while the first is still awaiting review."""
        a = to_demo_bank_candidates(build_sync_plan([_payment()], []), BUILDING_ID)[0]
        b = to_demo_bank_candidates(build_sync_plan([_payment(id="other")], []), BUILDING_ID)[0]
        assert a["idempotency_key"] == b["idempotency_key"]

    def test_amount_is_stored_absolute_with_direction_carrying_the_sign(self):
        """Demo Bank's signed-amount contract applies at the PROVIDER boundary;
        ingestion stores the absolute value."""
        cand = to_demo_bank_candidates(build_sync_plan([_payment()], []), BUILDING_ID)[0]
        assert cand["amount_cents"] > 0
        assert cand["direction"] == "credit"


class TestDrift:
    def test_agreement_is_reported_clean(self):
        report = measure_drift(BUILDING_ID, {"UA013": -100}, {"UA013": -100})
        assert report.is_clean
        assert report.net_gap_cents == 0

    def test_divergence_is_counted_and_netted(self):
        report = measure_drift(BUILDING_ID, {"UA013": 500, "UA014": 0}, {"UA013": 200, "UA014": 0})
        assert report.lots_diverged == 1
        assert report.net_gap_cents == 300

    def test_a_unit_missing_from_one_side_counts_as_diverged(self):
        """Missing and zero are different states. Collapsing them is the specific
        error that has produced wrong finance figures here more than once."""
        report = measure_drift(BUILDING_ID, {"UA013": 250}, {})
        assert report.lots_diverged == 1
        assert report.lots_compared == 1

    def test_a_unit_missing_from_one_side_holding_zero_is_not_divergence(self):
        report = measure_drift(BUILDING_ID, {"UA013": 0}, {})
        assert report.is_clean
