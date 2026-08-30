# @featuretrace:financial_integration_v2 — Integration tests for migration 0073's
#   DB-level constraints against a real Postgres instance.
# Layer: test
# Data flow: finance.accounting_periods + finance.reconstruction_execution_batches(_items)
#            (all schemes, integration-only, RUN_INTEGRATION_TESTS=1).
# Related: backend/alembic/versions/0073_accounting_period_integrity.py
#          backend/alembic/versions/0074_harden_recon_batches.py
"""Integration tests for finance.accounting_periods constraints (migration 0073)
and finance.reconstruction_execution_batches(_items) integrity.

Requires a live Postgres instance and RUN_INTEGRATION_TESTS=1 (same gating
convention as the Mongo integration tests in test_schema_migration.py).

Each test runs inside ONE outer transaction (never committed, always rolled
back at teardown — no test data ever persists, no manual cleanup needed) with
`SET LOCAL app.tenant_id` set once for the whole transaction. A constraint
violation is provoked inside a SAVEPOINT (`session.begin_nested()`) so only
that statement rolls back — the outer transaction, and critically its
`SET LOCAL` tenant context, survives for the rest of the test. Committing or
rolling back the *outer* transaction mid-test (as an earlier draft of this
file did) silently drops `SET LOCAL` and, under `FORCE ROW LEVEL SECURITY`,
makes every subsequent statement match zero rows instead of raising — a
`DELETE`/`UPDATE` on zero visible rows never violates a constraint, so the
test would report "DID NOT RAISE" for a constraint that actually works fine.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.integration

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"


def _skip_unless_integration():
    if not os.getenv("RUN_INTEGRATION_TESTS"):
        pytest.skip("Integration tests disabled. Set RUN_INTEGRATION_TESTS=1")


@pytest.fixture(scope="module", autouse=True)
def check_integration_enabled():
    _skip_unless_integration()


@pytest.fixture
async def east_gate_ctx():
    """One outer transaction for the whole test, tenant context set once,
    rolled back (never committed) at teardown."""
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    async with async_session_context() as session:
        await session.execute(text(f"SELECT set_config('app.tenant_id', '{_TENANT_BYPASS_ID}', true)"))
        row = (
            await session.execute(
                text("SELECT tenant_id::text, scheme_id::text FROM core.schemes WHERE lower(scheme_number) = lower('13195')")
            )
        ).fetchone()
        assert row is not None, "East Gate (13195) scheme row not found — cannot run integration test"
        tenant_id, scheme_id = row
        await set_tenant(session, tenant_id)
        yield session, tenant_id, scheme_id
        await session.rollback()


class TestAccountingPeriodsConstraints:
    @pytest.mark.asyncio
    async def test_invalid_status_rejected(self, east_gate_ctx):
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        session, tenant_id, scheme_id = east_gate_ctx
        period_id = str(uuid.uuid4())
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    text(
                        """
                        INSERT INTO finance.accounting_periods
                            (period_id, tenant_id, scheme_id, period_label, starts_on, ends_on, status, created_at)
                        VALUES (:pid, :tid, :sid, 'test-bogus-status', '2099-01-01', '2099-12-31', 'bogus', now())
                        """
                    ),
                    {"pid": period_id, "tid": tenant_id, "sid": scheme_id},
                )

    @pytest.mark.asyncio
    async def test_overlapping_daterange_rejected(self, east_gate_ctx):
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        session, tenant_id, scheme_id = east_gate_ctx
        p1, p2 = str(uuid.uuid4()), str(uuid.uuid4())
        async with session.begin_nested():
            await session.execute(
                text(
                    """
                    INSERT INTO finance.accounting_periods
                        (period_id, tenant_id, scheme_id, period_label, starts_on, ends_on, status, created_at)
                    VALUES (:pid, :tid, :sid, 'test-overlap-a', '2098-01-01', '2098-12-31', 'open', now())
                    """
                ),
                {"pid": p1, "tid": tenant_id, "sid": scheme_id},
            )

        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    text(
                        """
                        INSERT INTO finance.accounting_periods
                            (period_id, tenant_id, scheme_id, period_label, starts_on, ends_on, status, created_at)
                        VALUES (:pid, :tid, :sid, 'test-overlap-b', '2098-06-01', '2099-06-30', 'closed', now())
                        """
                    ),
                    {"pid": p2, "tid": tenant_id, "sid": scheme_id},
                )

    @pytest.mark.asyncio
    async def test_non_overlapping_open_and_closed_periods_coexist(self, east_gate_ctx):
        """Proves the EXCLUDE constraint does NOT enforce 'only one open
        period at a time' — two non-overlapping periods, one open one
        closed, must both be insertable."""
        from sqlalchemy import text

        session, tenant_id, scheme_id = east_gate_ctx
        p1, p2 = str(uuid.uuid4()), str(uuid.uuid4())
        async with session.begin_nested():
            await session.execute(
                text(
                    """
                    INSERT INTO finance.accounting_periods
                        (period_id, tenant_id, scheme_id, period_label, starts_on, ends_on, status, created_at)
                    VALUES (:pid, :tid, :sid, 'test-coexist-open', '2097-01-01', '2097-12-31', 'open', now())
                    """
                ),
                {"pid": p1, "tid": tenant_id, "sid": scheme_id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO finance.accounting_periods
                        (period_id, tenant_id, scheme_id, period_label, starts_on, ends_on, status, created_at)
                    VALUES (:pid, :tid, :sid, 'test-coexist-closed', '2096-01-01', '2096-12-31', 'closed', now())
                    """
                ),
                {"pid": p2, "tid": tenant_id, "sid": scheme_id},
            )
        # No exception — both rows inserted successfully within the savepoint.

    @pytest.mark.asyncio
    async def test_duplicate_period_label_rejected(self, east_gate_ctx):
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        session, tenant_id, scheme_id = east_gate_ctx
        p1 = str(uuid.uuid4())
        async with session.begin_nested():
            await session.execute(
                text(
                    """
                    INSERT INTO finance.accounting_periods
                        (period_id, tenant_id, scheme_id, period_label, starts_on, ends_on, status, created_at)
                    VALUES (:pid, :tid, :sid, 'test-dup-label', '2095-01-01', '2095-12-31', 'open', now())
                    """
                ),
                {"pid": p1, "tid": tenant_id, "sid": scheme_id},
            )

        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    text(
                        """
                        INSERT INTO finance.accounting_periods
                            (period_id, tenant_id, scheme_id, period_label, starts_on, ends_on, status, created_at)
                        VALUES (:pid, :tid, :sid, 'test-dup-label', '2094-01-01', '2094-12-31', 'open', now())
                        """
                    ),
                    {"pid": str(uuid.uuid4()), "tid": tenant_id, "sid": scheme_id},
                )


class TestReconstructionExecutionBatches:
    @pytest.mark.asyncio
    async def test_approver_must_differ_from_creator(self, east_gate_ctx):
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        session, tenant_id, scheme_id = east_gate_ctx
        user_row = (await session.execute(text("SELECT user_id::text FROM core.users LIMIT 1"))).fetchone()
        assert user_row is not None
        user_id = user_row[0]
        batch_id = str(uuid.uuid4())

        async with session.begin_nested():
            await session.execute(
                text(
                    """
                    INSERT INTO finance.reconstruction_execution_batches
                        (batch_id, tenant_id, scheme_id, batch_type, target_period_label, description,
                         manifest_hash, expected_row_count, expected_amount_cents, created_by)
                    VALUES (:bid, :tid, :sid, 'historical_expense', '2025', 'integration test batch',
                            'hash', 1, 100, :cby)
                    """
                ),
                {"bid": batch_id, "tid": tenant_id, "sid": scheme_id, "cby": user_id},
            )

        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "UPDATE finance.reconstruction_execution_batches "
                        "SET status='approved', approved_by=:cby, approved_at=now() WHERE batch_id=:bid"
                    ),
                    {"bid": batch_id, "cby": user_id},
                )

    @pytest.mark.asyncio
    async def test_manifest_defining_columns_are_immutable(self, east_gate_ctx):
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError

        session, tenant_id, scheme_id = east_gate_ctx
        user_row = (await session.execute(text("SELECT user_id::text FROM core.users LIMIT 1"))).fetchone()
        user_id = user_row[0]
        batch_id = str(uuid.uuid4())

        async with session.begin_nested():
            await session.execute(
                text(
                    """
                    INSERT INTO finance.reconstruction_execution_batches
                        (batch_id, tenant_id, scheme_id, batch_type, target_period_label, description,
                         manifest_hash, expected_row_count, expected_amount_cents, created_by)
                    VALUES (:bid, :tid, :sid, 'historical_expense', '2025', 'immutable test batch',
                            'hash', 1, 100, :cby)
                    """
                ),
                {"bid": batch_id, "tid": tenant_id, "sid": scheme_id, "cby": user_id},
            )

        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    text("UPDATE finance.reconstruction_execution_batches SET description='changed' WHERE batch_id=:bid"),
                    {"bid": batch_id},
                )

    @pytest.mark.asyncio
    async def test_batch_item_snapshot_columns_are_immutable(self, east_gate_ctx):
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError

        session, tenant_id, scheme_id = east_gate_ctx
        user_row = (await session.execute(text("SELECT user_id::text FROM core.users LIMIT 1"))).fetchone()
        user_id = user_row[0]
        bt_row = (await session.execute(text("SELECT bank_transaction_id::text FROM finance.bank_transactions LIMIT 1"))).fetchone()
        assert bt_row is not None, "No finance.bank_transactions row available for East Gate — cannot run this test"
        bank_transaction_id = bt_row[0]
        batch_id = str(uuid.uuid4())
        batch_item_id = str(uuid.uuid4())

        async with session.begin_nested():
            await session.execute(
                text(
                    """
                    INSERT INTO finance.reconstruction_execution_batches
                        (batch_id, tenant_id, scheme_id, batch_type, target_period_label, description,
                         manifest_hash, expected_row_count, expected_amount_cents, created_by)
                    VALUES (:bid, :tid, :sid, 'historical_expense', '2025', 'item immutability test batch',
                            'hash', 1, 100, :cby)
                    """
                ),
                {"bid": batch_id, "tid": tenant_id, "sid": scheme_id, "cby": user_id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO finance.reconstruction_execution_batch_items
                        (batch_item_id, batch_id, tenant_id, scheme_id, sequence_number, bank_transaction_id,
                         transaction_date, description, amount_cents, item_type, source_snapshot_hash)
                    VALUES (:iid, :bid, :tid, :sid, 1, :btid, '2025-01-01', 'test item', -1000,
                            'general_expense', 'snaphash')
                    """
                ),
                {"iid": batch_item_id, "bid": batch_id, "tid": tenant_id, "sid": scheme_id, "btid": bank_transaction_id},
            )

        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    text("UPDATE finance.reconstruction_execution_batch_items SET amount_cents=-9999 WHERE batch_item_id=:iid"),
                    {"iid": batch_item_id},
                )

    @pytest.mark.asyncio
    async def test_batch_item_status_mutation_is_allowed(self, east_gate_ctx):
        """Sanity check: the immutability trigger must NOT block legitimate
        status/journal-link updates — only snapshot columns are frozen."""
        from sqlalchemy import text

        session, tenant_id, scheme_id = east_gate_ctx
        user_row = (await session.execute(text("SELECT user_id::text FROM core.users LIMIT 1"))).fetchone()
        user_id = user_row[0]
        bt_row = (await session.execute(text("SELECT bank_transaction_id::text FROM finance.bank_transactions LIMIT 1"))).fetchone()
        bank_transaction_id = bt_row[0]
        batch_id = str(uuid.uuid4())
        batch_item_id = str(uuid.uuid4())

        async with session.begin_nested():
            await session.execute(
                text(
                    """
                    INSERT INTO finance.reconstruction_execution_batches
                        (batch_id, tenant_id, scheme_id, batch_type, target_period_label, description,
                         manifest_hash, expected_row_count, expected_amount_cents, created_by)
                    VALUES (:bid, :tid, :sid, 'historical_expense', '2025', 'status mutation test batch',
                            'hash', 1, 100, :cby)
                    """
                ),
                {"bid": batch_id, "tid": tenant_id, "sid": scheme_id, "cby": user_id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO finance.reconstruction_execution_batch_items
                        (batch_item_id, batch_id, tenant_id, scheme_id, sequence_number, bank_transaction_id,
                         transaction_date, description, amount_cents, item_type, source_snapshot_hash)
                    VALUES (:iid, :bid, :tid, :sid, 1, :btid, '2025-01-01', 'test item', -1000,
                            'general_expense', 'snaphash')
                    """
                ),
                {"iid": batch_item_id, "bid": batch_id, "tid": tenant_id, "sid": scheme_id, "btid": bank_transaction_id},
            )
            # status/error_message/applied_at are NOT snapshot columns — must succeed.
            await session.execute(
                text("UPDATE finance.reconstruction_execution_batch_items SET status='failed', error_message='test' WHERE batch_item_id=:iid"),
                {"iid": batch_item_id},
            )
        # No exception — legitimate status mutation succeeded within the savepoint.
