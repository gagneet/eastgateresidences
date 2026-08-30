"""Unit tests for services/reconstruction_batch_service.py.

Uses a minimal in-memory fake Mongo database (not AsyncMock — the status-
machine logic needs real read-your-writes semantics across multiple calls,
which a mocked find_one/insert_one/update_one would make verbose to express).
No real MongoDB connection required.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from services import reconstruction_batch_service as rbs
from integrations.demo_bank.reconstruction_batch_schemas import (
    ReconstructedTransactionRow,
    ReconstructionManifest,
)

BUILDING_ID = "16244"  # Sierra demo — never "13195" in tests


def _matches_clause(actual, clause) -> bool:
    """Minimal Mongo operator support ($ne, $in) -- just enough for the queries this fake
    is asked to match; anything else falls back to plain equality."""
    if isinstance(clause, dict) and ("$ne" in clause or "$in" in clause):
        if "$ne" in clause and actual == clause["$ne"]:
            return False
        if "$in" in clause and actual not in clause["$in"]:
            return False
        return True
    return actual == clause


def _matches(doc: dict, query: dict) -> bool:
    return all(_matches_clause(doc.get(k), v) for k, v in query.items())


class _FakeCollection:
    def __init__(self):
        self._docs: list[dict] = []

    async def insert_one(self, doc: dict):
        self._docs.append(dict(doc))

    async def find_one(self, query: dict):
        for doc in self._docs:
            if _matches(doc, query):
                return doc
        return None

    async def update_one(self, query: dict, update: dict):
        for doc in self._docs:
            if _matches(doc, query):
                doc.update(update.get("$set", {}))
                return

    def find(self, query: dict):
        matches = [doc for doc in self._docs if _matches(doc, query)]

        class _Cursor:
            def __init__(self, items):
                self._items = items

            def sort(self, *args, **kwargs):
                return self

            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                for item in self._items:
                    yield item

        return _Cursor(matches)

    async def count_documents(self, query: dict):
        return len([doc for doc in self._docs if _matches(doc, query)])


class _FakeDb:
    def __init__(self):
        self.demo_bank_reconstruction_batches = _FakeCollection()
        self.demo_bank_reconstruction_manifests = _FakeCollection()
        self.demo_bank_transactions = _FakeCollection()


@pytest.fixture
def db():
    return _FakeDb()


def _manifest(batch_id: str) -> ReconstructionManifest:
    return ReconstructionManifest(
        manifest_id="manifest-1",
        batch_id=batch_id,
        building_id=BUILDING_ID,
        input_fact_hash="abc",
        generator_version="test-v1",
        expected_transaction_count=1,
        expected_credit_cents=1000,
        transactions=[ReconstructedTransactionRow(
            account_ref="ADMIN-16244", unit_number="1", financial_year="2024", quarter=1,
            fund_type="admin", posted_date=date(2024, 3, 1), amount_cents=1000,
            amount_ex_gst_cents=909, gst_cents=91, assumption_code="quarterly_regular",
            description="test row", transaction_sequence=1,
        )],
        manifest_hash="deadbeef",
    )


class TestBatchCreation:
    @pytest.mark.asyncio
    async def test_create_batch_starts_in_draft(self, db):
        batch = await rbs.create_batch(
            db, building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="test-method", created_by="tester",
        )
        assert batch.status == "draft"
        assert batch.building_id == BUILDING_ID


class TestMarkExtracted:
    """mark_extracted() — the one status transition that previously had no named
    public wrapper (every other transition has one: mark_generation_ready,
    mark_generated, mark_syncing, mark_synced, mark_posted, supersede, reverse),
    forcing callers to reach into the private _transition() function directly
    (see TestStatusMachine.test_valid_transition_chain below, which still does
    that — added for East Gate 13195's Step 2 reconstruction orchestration,
    which needed draft->extracted before submit_for_review())."""

    @pytest.mark.asyncio
    async def test_moves_draft_to_extracted(self, db):
        batch = await rbs.create_batch(
            db, building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="test-method", created_by="tester",
        )
        updated = await rbs.mark_extracted(db, building_id=BUILDING_ID, batch_id=batch.batch_id)
        assert updated.status == "extracted"

    @pytest.mark.asyncio
    async def test_enables_submit_for_review(self, db):
        batch = await rbs.create_batch(
            db, building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="test-method", created_by="tester",
        )
        await rbs.mark_extracted(db, building_id=BUILDING_ID, batch_id=batch.batch_id)
        manifest = _manifest(batch.batch_id)
        updated = await rbs.submit_for_review(db, building_id=BUILDING_ID, batch_id=batch.batch_id, manifest=manifest)
        assert updated.status == "needs_review"


class TestStatusMachine:
    @pytest.mark.asyncio
    async def test_valid_transition_chain(self, db):
        batch = await rbs.create_batch(
            db, building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="test-method", created_by="tester",
        )
        await rbs._transition(db, BUILDING_ID, batch.batch_id, to_status="extracted")
        manifest = _manifest(batch.batch_id)
        updated = await rbs.submit_for_review(db, building_id=BUILDING_ID, batch_id=batch.batch_id, manifest=manifest)
        assert updated.status == "needs_review"

    @pytest.mark.asyncio
    async def test_invalid_transition_raises(self, db):
        batch = await rbs.create_batch(
            db, building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="test-method", created_by="tester",
        )
        # draft -> approved directly is not in the allowed transition set
        with pytest.raises(rbs.ReconstructionWorkflowError, match="Invalid transition"):
            await rbs._transition(db, BUILDING_ID, batch.batch_id, to_status="approved")

    @pytest.mark.asyncio
    async def test_terminal_status_has_no_outbound_transitions(self, db):
        assert rbs._VALID_TRANSITIONS["superseded"] == set()
        assert rbs._VALID_TRANSITIONS["reversed"] == set()

    @pytest.mark.asyncio
    async def test_unknown_batch_id_raises(self, db):
        with pytest.raises(rbs.ReconstructionWorkflowError, match="No reconstruction batch"):
            await rbs._transition(db, BUILDING_ID, "does-not-exist", to_status="extracted")


class TestApprovalGovernance:
    @pytest.mark.asyncio
    async def test_approve_requires_review_first(self, db):
        batch = await rbs.create_batch(
            db, building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="test-method", created_by="tester",
        )
        await rbs._transition(db, BUILDING_ID, batch.batch_id, to_status="extracted")
        await rbs.submit_for_review(db, building_id=BUILDING_ID, batch_id=batch.batch_id, manifest=_manifest(batch.batch_id))

        with pytest.raises(rbs.ReconstructionWorkflowError, match="reviewed_by"):
            await rbs.approve_batch(db, building_id=BUILDING_ID, batch_id=batch.batch_id, approved_by="admin-1")

    @pytest.mark.asyncio
    async def test_approve_succeeds_after_review(self, db):
        batch = await rbs.create_batch(
            db, building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="test-method", created_by="tester",
        )
        await rbs._transition(db, BUILDING_ID, batch.batch_id, to_status="extracted")
        await rbs.submit_for_review(db, building_id=BUILDING_ID, batch_id=batch.batch_id, manifest=_manifest(batch.batch_id))
        await rbs.record_review(db, building_id=BUILDING_ID, batch_id=batch.batch_id, reviewed_by="manager-1")

        approved = await rbs.approve_batch(db, building_id=BUILDING_ID, batch_id=batch.batch_id, approved_by="admin-1")
        assert approved.status == "approved"
        assert approved.approved_by == "admin-1"

    @pytest.mark.asyncio
    async def test_record_review_only_from_needs_review(self, db):
        batch = await rbs.create_batch(
            db, building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="test-method", created_by="tester",
        )
        with pytest.raises(rbs.ReconstructionWorkflowError, match="needs_review"):
            await rbs.record_review(db, building_id=BUILDING_ID, batch_id=batch.batch_id, reviewed_by="manager-1")


class TestManifestLocking:
    def test_is_manifest_locked_for_approved_statuses(self):
        from integrations.demo_bank.reconstruction_batch_schemas import ReconstructionBatch

        locked = ReconstructionBatch(
            batch_id="b1", building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="m", status="approved",
        )
        unlocked = ReconstructionBatch(
            batch_id="b2", building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="m", status="needs_review",
        )
        assert rbs.is_manifest_locked(locked) is True
        assert rbs.is_manifest_locked(unlocked) is False


class TestRetroactiveLinking:
    @pytest.mark.asyncio
    async def test_link_existing_rows_builds_manifest_without_mutating_them(self, db):
        # Seed 3 pre-existing demo_bank_transactions rows, as if migration_027 had already run.
        for i in range(3):
            await db.demo_bank_transactions.insert_one({
                "building_id": BUILDING_ID, "source_type": "synthetic_from_budget",
                "account_ref": "ADMIN-16244", "unit_number": str(i + 1),
                "levy_year": "2024", "levy_quarter": "Q1",
                "posted_date": datetime(2024, 3, 1, tzinfo=timezone.utc),
                "amount_cents": 1000, "amount_ex_gst_cents": 909, "gst_cents": 91,
                "direction": "credit", "payment_pattern": "quarterly_regular",
                "description": "test", "idempotency_key": f"idem-{i}",
                "external_transaction_id": f"ext-{i}",
            })

        batch = await rbs.create_batch(
            db, building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="retroactive-link-v1", created_by="tester",
        )
        manifest = await rbs.link_existing_rows_as_manifest(db, building_id=BUILDING_ID, batch=batch)

        assert manifest.expected_transaction_count == 3
        assert manifest.expected_credit_cents == 3000
        assert manifest.batch_id == batch.batch_id
        # Original rows must be byte-identical — nothing rewritten.
        raw_docs = db.demo_bank_transactions._docs
        assert len(raw_docs) == 3
        assert all(d["amount_cents"] == 1000 for d in raw_docs)

    @pytest.mark.asyncio
    async def test_link_existing_rows_raises_when_nothing_to_link(self, db):
        batch = await rbs.create_batch(
            db, building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="retroactive-link-v1", created_by="tester",
        )
        with pytest.raises(rbs.ReconstructionWorkflowError, match="nothing to link"):
            await rbs.link_existing_rows_as_manifest(db, building_id=BUILDING_ID, batch=batch)


class TestRejectAction:
    """reject_batch() — the previously-missing negative outcome of needs_review. Unlike
    approve, reject has no dual-control precondition (any single reviewer can reject) and
    requires no prior record_review() call — a batch can be rejected straight out of
    needs_review without ever being reviewed first."""

    @pytest.mark.asyncio
    async def test_reject_returns_batch_to_extracted(self, db):
        batch = await rbs.create_batch(
            db, building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="test-method", created_by="tester",
        )
        await rbs._transition(db, BUILDING_ID, batch.batch_id, to_status="extracted")
        await rbs.submit_for_review(db, building_id=BUILDING_ID, batch_id=batch.batch_id, manifest=_manifest(batch.batch_id))

        rejected = await rbs.reject_batch(
            db, building_id=BUILDING_ID, batch_id=batch.batch_id, rejected_by="manager-1",
            reason="Closing balance is implausible — source CSV looks wrong for FY2024.",
        )
        assert rejected.status == "extracted"

    @pytest.mark.asyncio
    async def test_reject_does_not_require_prior_review(self, db):
        batch = await rbs.create_batch(
            db, building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="test-method", created_by="tester",
        )
        await rbs._transition(db, BUILDING_ID, batch.batch_id, to_status="extracted")
        await rbs.submit_for_review(db, building_id=BUILDING_ID, batch_id=batch.batch_id, manifest=_manifest(batch.batch_id))

        # No record_review() call before this — must still succeed.
        rejected = await rbs.reject_batch(
            db, building_id=BUILDING_ID, batch_id=batch.batch_id, rejected_by="manager-1", reason="Bad data.",
        )
        assert rejected.status == "extracted"

    @pytest.mark.asyncio
    async def test_reject_clears_reviewed_by(self, db):
        """A stale reviewed_by from the rejected attempt must not silently carry over and be
        mistaken for having applied to whatever manifest gets submitted next."""
        batch = await rbs.create_batch(
            db, building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="test-method", created_by="tester",
        )
        await rbs._transition(db, BUILDING_ID, batch.batch_id, to_status="extracted")
        await rbs.submit_for_review(db, building_id=BUILDING_ID, batch_id=batch.batch_id, manifest=_manifest(batch.batch_id))
        await rbs.record_review(db, building_id=BUILDING_ID, batch_id=batch.batch_id, reviewed_by="manager-1")

        rejected = await rbs.reject_batch(
            db, building_id=BUILDING_ID, batch_id=batch.batch_id, rejected_by="manager-2", reason="Wrong figures.",
        )
        assert rejected.reviewed_by is None

    @pytest.mark.asyncio
    async def test_reject_invalid_status_raises(self, db):
        batch = await rbs.create_batch(
            db, building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="test-method", created_by="tester",
        )
        # Still draft — reject requires status=needs_review explicitly (see reject_batch's
        # own docstring: draft->extracted is technically valid in _VALID_TRANSITIONS, but
        # reject is specifically the needs_review outcome, not a generic re-extract).
        with pytest.raises(rbs.ReconstructionWorkflowError, match="requires status=needs_review"):
            await rbs.reject_batch(
                db, building_id=BUILDING_ID, batch_id=batch.batch_id, rejected_by="manager-1", reason="x",
            )


class TestSubmitForReviewCreditDebitSplit:
    """submit_for_review() previously attributed every transaction to generated_credit_count
    regardless of direction — harmless when only credit rows existed, wrong once the merged
    income+expense manifest can carry debit rows too."""

    @pytest.mark.asyncio
    async def test_credit_and_debit_counts_split_by_direction(self, db):
        batch = await rbs.create_batch(
            db, building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="test-method", created_by="tester",
        )
        await rbs._transition(db, BUILDING_ID, batch.batch_id, to_status="extracted")

        manifest = ReconstructionManifest(
            manifest_id="manifest-2", batch_id=batch.batch_id, building_id=BUILDING_ID,
            input_fact_hash="abc", generator_version="test-v1", expected_transaction_count=2,
            expected_credit_cents=1000, expected_debit_cents=500,
            transactions=[
                ReconstructedTransactionRow(
                    account_ref="ADMIN-16244", unit_number="1", financial_year="2024", quarter=1,
                    fund_type="admin", posted_date=date(2024, 3, 1), amount_cents=1000,
                    amount_ex_gst_cents=909, gst_cents=91, direction="credit",
                    assumption_code="quarterly_regular", description="income row", transaction_sequence=1,
                ),
                ReconstructedTransactionRow(
                    account_ref="ADMIN-16244", unit_number="", financial_year="2024",
                    fund_type="admin", posted_date=date(2024, 6, 1), amount_cents=500,
                    amount_ex_gst_cents=455, gst_cents=45, direction="debit",
                    assumption_code="actual_category_spend", description="expense row", transaction_sequence=1,
                ),
            ],
            manifest_hash="deadbeef2",
        )
        updated = await rbs.submit_for_review(db, building_id=BUILDING_ID, batch_id=batch.batch_id, manifest=manifest)
        assert updated.generated_credit_count == 1
        assert updated.generated_debit_count == 1
        assert updated.generated_credit_cents == 1000
        assert updated.generated_debit_cents == 500

    @pytest.mark.asyncio
    async def test_reconciliation_variance_cents_stored_from_fund_balance_reconciliation(self, db):
        from integrations.demo_bank.reconstruction_batch_schemas import FundBalanceReconciliationLine

        batch = await rbs.create_batch(
            db, building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="test-method", created_by="tester",
        )
        await rbs._transition(db, BUILDING_ID, batch.batch_id, to_status="extracted")

        manifest = _manifest(batch.batch_id)
        manifest = manifest.model_copy(update={
            "fund_balance_reconciliation": [
                FundBalanceReconciliationLine(
                    account_ref="ADMIN-16244", opening_balance_cents=0, reconstructed_credits_cents=1000,
                    reconstructed_debits_cents=0, closing_balance_cents=1500, matches_expected_closing=False,
                ),
            ],
        })
        updated = await rbs.submit_for_review(db, building_id=BUILDING_ID, batch_id=batch.batch_id, manifest=manifest)
        # declared closing 1500 vs expected (0 + 1000 - 0) = 1000 -> variance 500
        assert updated.reconciliation_variance_cents == 500
