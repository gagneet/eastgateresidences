# @featuretrace:financial_integration_v2 — Permanent regression coverage for the
#   three real bugs found during the 2026-07-24 deep-dive re-audit, all of which
#   were invisible to the mocked unit-test suite.
# Layer: test
# Data flow: finance.accounting_periods, finance.reconstruction_execution_batches(_items),
#            finance.bank_transactions (East Gate, integration-only, RUN_INTEGRATION_TESTS=1).
# Related: backend/services/financial_core/adapters/db_postgres/models.py
#          backend/scripts/reconstruction_batch_lib.py
#          backend/services/financial_core/adapters/db_postgres/ledger_repo.py
#          tasks/GAP-FIN-018-east-gate-demo-bank-to-ledger-pipeline.md
"""Regression tests for the three failure modes found live-auditing this
session's work against real Postgres (none were caught by mocks):

1. An ORM model mapped a column (`PgAccountingPeriod.locked_by`) that doesn't
   exist on the live table — `test_all_financial_core_models_match_live_schema`
   generically checks every model in this module against `information_schema`,
   not just the one that broke, so a similar drift anywhere else is caught too.
2. `compute_manifest_hash()` required a `sequence_number` key no real caller
   ever sets before calling `create_manifest_batch()` —
   `test_create_manifest_batch_accepts_unsequenced_caller_items` calls it with
   item dicts shaped exactly like the real scripts build them.
3. `begin_apply()` commits internally, dropping `SET LOCAL app.tenant_id` for
   the rest of the session — `test_load_pending_items_after_begin_apply_same_session`
   reproduces the exact call sequence the real `cmd_apply()` uses and asserts
   items are still visible afterward.

Also covers `assert_pending_load_not_silently_empty()`, the defense-in-depth
guard added on top of the fix for #3 — an independent check that raises
rather than lets `--apply` report a clean `posted=0 failed=0` success if
`load_pending_items()` ever returns empty again while the batch's items
haven't all reached a terminal state.

Several functions under test (`create_manifest_batch`, `approve_batch`,
`begin_apply`) commit internally as their own atomic unit, so these tests
cannot rely on a SAVEPOINT/rollback for isolation (unlike
test_accounting_period_migration_0073.py, which never calls a
self-committing function) — teardown here is an explicit DELETE / claim
release in a `finally` block, run even if an assertion fails mid-test.
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
async def east_gate():
    """Resolve East Gate's real tenant_id/scheme_id once per test."""
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
    return tenant_id, scheme_id


class TestModelsMatchLiveSchema:
    """Failure mode #1: a phantom ORM column mapping broke every real caller
    with UndefinedColumnError, invisible to any mocked test. Generically
    checks every financial_core model, not just the one that broke."""

    @pytest.mark.asyncio
    async def test_all_financial_core_models_match_live_schema(self):
        from sqlalchemy import text
        from db_postgres.session import async_session_context
        from services.financial_core.adapters.db_postgres import models as pg_models

        model_classes = [
            getattr(pg_models, name)
            for name in dir(pg_models)
            if name.startswith("Pg") and hasattr(getattr(pg_models, name), "__table__")
        ]
        assert len(model_classes) >= 10, "Sanity check: expected at least 10 Pg* models in models.py"

        mismatches = []
        async with async_session_context() as session:
            await session.execute(text(f"SELECT set_config('app.tenant_id', '{_TENANT_BYPASS_ID}', true)"))
            for model in model_classes:
                table = model.__table__
                live_columns = {
                    r[0]
                    for r in (
                        await session.execute(
                            text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_schema = :schema AND table_name = :table"
                            ),
                            {"schema": table.schema, "table": table.name},
                        )
                    ).fetchall()
                }
                if not live_columns:
                    mismatches.append(f"{table.schema}.{table.name}: table not found in live schema at all")
                    continue
                mapped_columns = {c.name for c in table.columns}
                missing = mapped_columns - live_columns
                if missing:
                    mismatches.append(f"{table.schema}.{table.name}: ORM-mapped columns not in live schema: {sorted(missing)}")

        assert not mismatches, "ORM/live schema drift found:\n" + "\n".join(mismatches)


class TestManifestHashingWithRealPayloadShape:
    """Failure mode #2: compute_manifest_hash() required a key
    (sequence_number) that create_manifest_batch()'s own caller-supplied
    item dicts never carry — this reproduces the exact unsequenced shape
    both real backfill scripts build."""

    @pytest.mark.asyncio
    async def test_create_manifest_batch_accepts_unsequenced_caller_items(self, east_gate):
        from sqlalchemy import text
        from db_postgres.session import async_session_context, set_tenant
        import scripts.reconstruction_batch_lib as lib

        tenant_id, scheme_id = east_gate
        batch_id = None
        try:
            async with async_session_context() as session:
                await set_tenant(session, tenant_id)
                user_row = (await session.execute(text("SELECT user_id::text FROM core.users LIMIT 1"))).fetchone()
                assert user_row is not None
                creator = user_row[0]
                bt_row = (
                    await session.execute(
                        text("SELECT bank_transaction_id::text, transaction_date, description, amount_cents "
                             "FROM finance.bank_transactions LIMIT 1")
                    )
                ).fetchone()
                assert bt_row is not None, "No finance.bank_transactions row for East Gate — cannot run this test"

                # Shaped exactly like east_gate_2025_expense_reconstruction.py's
                # `items` list comprehension — NO sequence_number key, matching
                # what a real caller actually passes.
                items = [{
                    "bank_transaction_id": bt_row.bank_transaction_id, "transaction_date": bt_row.transaction_date,
                    "description": bt_row.description, "amount_cents": bt_row.amount_cents,
                    "category_name": "administration expenses", "lot_number": None, "item_type": "general_expense",
                }]
                assert "sequence_number" not in items[0]

                batch_id = await lib.create_manifest_batch(
                    session, tenant_id=tenant_id, scheme_id=scheme_id, batch_type="historical_expense",
                    target_period_label="2025", description="integration test — unsequenced payload shape",
                    created_by=creator, items=items,
                )
                assert batch_id is not None

                # create_manifest_batch() commits internally, same as
                # begin_apply() — re-establish tenant context before the next
                # query in this same session rather than assume it survived.
                await set_tenant(session, tenant_id)
                row = (
                    await session.execute(
                        text("SELECT sequence_number FROM finance.reconstruction_execution_batch_items WHERE batch_id = :bid"),
                        {"bid": batch_id},
                    )
                ).fetchone()
                assert row is not None, "batch item not visible after create_manifest_batch()'s internal commit"
                assert row.sequence_number == 1
        finally:
            if batch_id:
                async with async_session_context() as session:
                    await set_tenant(session, tenant_id)
                    await session.execute(
                        text("UPDATE finance.bank_transactions SET reconstruction_batch_id = NULL WHERE reconstruction_batch_id = :bid"),
                        {"bid": batch_id},
                    )
                    await session.execute(
                        text("DELETE FROM finance.reconstruction_execution_batch_items WHERE batch_id = :bid"), {"bid": batch_id}
                    )
                    await session.execute(
                        text("DELETE FROM finance.reconstruction_execution_batches WHERE batch_id = :bid"), {"bid": batch_id}
                    )
                    await session.commit()


class TestTenantContextSurvivesInternalCommit:
    """Failure mode #3: begin_apply() commits its own status='applying'
    update, which drops SET LOCAL app.tenant_id — load_pending_items(),
    called right after in the same session (the real cmd_apply() sequence),
    would then silently return zero rows under FORCE ROW LEVEL SECURITY, not
    an error. This reproduces that exact sequence and asserts pending items
    are still visible."""

    @pytest.mark.asyncio
    async def test_load_pending_items_after_begin_apply_same_session(self, east_gate):
        from sqlalchemy import text
        from db_postgres.session import async_session_context, set_tenant
        import scripts.reconstruction_batch_lib as lib

        tenant_id, scheme_id = east_gate
        batch_id = None
        try:
            async with async_session_context() as session:
                await set_tenant(session, tenant_id)
                user_rows = (await session.execute(text("SELECT user_id::text FROM core.users LIMIT 2"))).fetchall()
                assert len(user_rows) >= 2, "Need at least 2 users for this tenant to test dual control"
                creator, approver = user_rows[0][0], user_rows[1][0]
                bt_row = (
                    await session.execute(
                        text("SELECT bank_transaction_id::text, transaction_date, description, amount_cents "
                             "FROM finance.bank_transactions LIMIT 1")
                    )
                ).fetchone()
                assert bt_row is not None

                items = [{
                    "bank_transaction_id": bt_row.bank_transaction_id, "transaction_date": bt_row.transaction_date,
                    "description": bt_row.description, "amount_cents": bt_row.amount_cents,
                    "category_name": "administration expenses", "lot_number": None, "item_type": "general_expense",
                }]
                batch_id = await lib.create_manifest_batch(
                    session, tenant_id=tenant_id, scheme_id=scheme_id, batch_type="historical_expense",
                    target_period_label="2025", description="integration test — tenant context after commit",
                    created_by=creator, items=items,
                )

            async with async_session_context() as session:
                await set_tenant(session, tenant_id)
                await lib.approve_batch(
                    session, tenant_id=tenant_id, batch_id=batch_id, approved_by=approver, approval_reference="test"
                )

            # This is the exact sequence cmd_apply() uses: begin_apply() (which
            # commits internally) followed immediately by load_pending_items()
            # in the SAME session, with no intervening set_tenant() call from
            # the test itself — proving reconstruction_batch_lib's own
            # defensive set_tenant() calls (not caller discipline) are what
            # keeps this correct.
            async with async_session_context() as session:
                await set_tenant(session, tenant_id)
                batch = await lib.begin_apply(session, tenant_id, batch_id)
                assert batch["status"] == "approved"

                pending = await lib.load_pending_items(session, tenant_id, batch_id)
                assert len(pending) == 1, (
                    "load_pending_items() returned 0 items after begin_apply()'s internal commit — "
                    "this is exactly the silent-zero-items regression this test exists to catch"
                )
        finally:
            if batch_id:
                async with async_session_context() as session:
                    await set_tenant(session, tenant_id)
                    await session.execute(
                        text("UPDATE finance.bank_transactions SET reconstruction_batch_id = NULL WHERE reconstruction_batch_id = :bid"),
                        {"bid": batch_id},
                    )
                    await session.execute(
                        text("DELETE FROM finance.reconstruction_execution_batch_items WHERE batch_id = :bid"), {"bid": batch_id}
                    )
                    await session.execute(
                        text("DELETE FROM finance.reconstruction_execution_batches WHERE batch_id = :bid"), {"bid": batch_id}
                    )
                    await session.commit()


class TestAssertPendingLoadNotSilentlyEmpty:
    """The defense-in-depth guard layered on top of the tenant-context fix —
    tested independently of whatever might cause load_pending_items() to
    return empty, by calling the guard directly with controlled inputs."""

    @pytest.mark.asyncio
    async def test_nonempty_pending_is_always_a_no_op(self, east_gate):
        """loaded_pending_count > 0 must never raise, regardless of the
        batch's actual terminal-item state — the guard only activates on
        the empty-list case."""
        from db_postgres.session import async_session_context, set_tenant
        import scripts.reconstruction_batch_lib as lib

        tenant_id, scheme_id = east_gate
        async with async_session_context() as session:
            await set_tenant(session, tenant_id)
            # batch_id doesn't need to exist for this call — loaded_pending_count=1
            # short-circuits before any query runs.
            await lib.assert_pending_load_not_silently_empty(
                session, tenant_id, str(uuid.uuid4()), expected_row_count=5, loaded_pending_count=1,
            )

    @pytest.mark.asyncio
    async def test_empty_pending_with_all_items_terminal_is_legitimate(self, east_gate):
        """0 pending items is fine — not a bug — when every one of the
        batch's expected items has genuinely already reached a terminal
        ('applied' or 'skipped') state."""
        from sqlalchemy import text
        from db_postgres.session import async_session_context, set_tenant
        import scripts.reconstruction_batch_lib as lib

        tenant_id, scheme_id = east_gate
        batch_id = None
        try:
            async with async_session_context() as session:
                await set_tenant(session, tenant_id)
                user_row = (await session.execute(text("SELECT user_id::text FROM core.users LIMIT 1"))).fetchone()
                creator = user_row[0]
                bt_row = (
                    await session.execute(
                        text("SELECT bank_transaction_id::text, transaction_date, description, amount_cents "
                             "FROM finance.bank_transactions LIMIT 1")
                    )
                ).fetchone()
                items = [{
                    "bank_transaction_id": bt_row.bank_transaction_id, "transaction_date": bt_row.transaction_date,
                    "description": bt_row.description, "amount_cents": bt_row.amount_cents,
                    "category_name": "administration expenses", "lot_number": None, "item_type": "general_expense",
                }]
                batch_id = await lib.create_manifest_batch(
                    session, tenant_id=tenant_id, scheme_id=scheme_id, batch_type="historical_expense",
                    target_period_label="2025", description="integration test — legitimate zero-pending case",
                    created_by=creator, items=items,
                )
                await set_tenant(session, tenant_id)
                # Directly mark the one item 'skipped' (bypassing journal
                # posting — this test targets the guard's counting logic,
                # not the posting path — 'skipped' is used rather than
                # 'applied' specifically because it has no journal-reference
                # CHECK constraint to satisfy) so 0 pending items is the
                # correct, legitimate outcome.
                await session.execute(
                    text(
                        "UPDATE finance.reconstruction_execution_batch_items "
                        "SET status = 'skipped' "
                        "WHERE batch_id = :bid"
                    ),
                    {"bid": batch_id},
                )
                await session.commit()

            async with async_session_context() as session:
                await set_tenant(session, tenant_id)
                pending = await lib.load_pending_items(session, tenant_id, batch_id)
                assert pending == []
                # Must NOT raise — every item is genuinely terminal.
                await lib.assert_pending_load_not_silently_empty(
                    session, tenant_id, batch_id, expected_row_count=1, loaded_pending_count=len(pending),
                )
        finally:
            if batch_id:
                async with async_session_context() as session:
                    await set_tenant(session, tenant_id)
                    await session.execute(
                        text("UPDATE finance.bank_transactions SET reconstruction_batch_id = NULL WHERE reconstruction_batch_id = :bid"),
                        {"bid": batch_id},
                    )
                    await session.execute(
                        text("DELETE FROM finance.reconstruction_execution_batch_items WHERE batch_id = :bid"), {"bid": batch_id}
                    )
                    await session.execute(
                        text("DELETE FROM finance.reconstruction_execution_batches WHERE batch_id = :bid"), {"bid": batch_id}
                    )
                    await session.commit()

    @pytest.mark.asyncio
    async def test_empty_pending_with_items_not_terminal_raises(self, east_gate):
        """The actual regression this guard exists to catch: 0 pending items
        loaded, but the frozen manifest's expected_row_count says there
        should be items still outstanding (none marked applied/skipped) —
        must raise, not silently report success."""
        from db_postgres.session import async_session_context, set_tenant
        import scripts.reconstruction_batch_lib as lib

        tenant_id, scheme_id = east_gate
        async with async_session_context() as session:
            await set_tenant(session, tenant_id)
            with pytest.raises(lib.BatchOrchestrationError, match="silent-zero-items"):
                # batch_id need not exist — expected_row_count=1 with
                # loaded_pending_count=0 forces the terminal-count query,
                # which returns 0 for a nonexistent batch, correctly < 1.
                await lib.assert_pending_load_not_silently_empty(
                    session, tenant_id, str(uuid.uuid4()), expected_row_count=1, loaded_pending_count=0,
                )


class TestCrossSessionInterruptedAndResumedApply:
    """Simulates an --apply run interrupted partway through (process crash,
    connection drop) and resumed later. Every step below uses its own
    `async with async_session_context()` block — a genuinely separate
    database session/connection each time (this is exactly what the
    SET LOCAL app.tenant_id mechanism the three bugs hinge on scopes to),
    never one retained session carried across the whole flow."""

    @pytest.mark.asyncio
    async def test_resume_only_touches_items_not_yet_terminal(self, east_gate):
        from sqlalchemy import text
        from db_postgres.session import async_session_context, set_tenant
        import scripts.reconstruction_batch_lib as lib

        tenant_id, scheme_id = east_gate
        batch_id = None
        try:
            # Session 1: build a 3-item manifest from real bank_transactions.
            async with async_session_context() as session:
                await set_tenant(session, tenant_id)
                user_row = (await session.execute(text("SELECT user_id::text FROM core.users LIMIT 1"))).fetchone()
                creator = user_row[0]
                bt_rows = (
                    await session.execute(
                        text("SELECT bank_transaction_id::text, transaction_date, description, amount_cents "
                             "FROM finance.bank_transactions LIMIT 3")
                    )
                ).fetchall()
                assert len(bt_rows) == 3, "Need 3 distinct bank_transactions rows for East Gate to run this test"
                items = [{
                    "bank_transaction_id": r.bank_transaction_id, "transaction_date": r.transaction_date,
                    "description": r.description, "amount_cents": r.amount_cents,
                    "category_name": "administration expenses", "lot_number": None, "item_type": "general_expense",
                } for r in bt_rows]
                batch_id = await lib.create_manifest_batch(
                    session, tenant_id=tenant_id, scheme_id=scheme_id, batch_type="historical_expense",
                    target_period_label="2025", description="integration test — cross-session resume",
                    created_by=creator, items=items,
                )

            # Session 2: approve (a distinct identity from the creator).
            async with async_session_context() as session:
                await set_tenant(session, tenant_id)
                approver = (
                    await session.execute(
                        text("SELECT user_id::text FROM core.users WHERE user_id::text != :cby LIMIT 1"), {"cby": creator}
                    )
                ).fetchone()[0]
                await lib.approve_batch(
                    session, tenant_id=tenant_id, batch_id=batch_id, approved_by=approver, approval_reference="test"
                )

            # Session 3: "first apply attempt" — begin_apply, then simulate
            # that ONE item (sequence 1) was successfully processed and
            # marked 'skipped' before the process crashed, leaving the other
            # two still 'pending'. Never calls finalize_batch_status — the
            # batch is left mid-flight at status='applying', exactly as a
            # real crash would leave it.
            async with async_session_context() as session:
                await set_tenant(session, tenant_id)
                batch = await lib.begin_apply(session, tenant_id, batch_id)
                assert batch["status"] == "approved"

                first_pending = await lib.load_pending_items(session, tenant_id, batch_id)
                assert len(first_pending) == 3
                first_item_id = first_pending[0]["batch_item_id"]

                await set_tenant(session, tenant_id)
                await session.execute(
                    text("UPDATE finance.reconstruction_execution_batch_items SET status = 'skipped' WHERE batch_item_id = :iid"),
                    {"iid": first_item_id},
                )
                await session.commit()
                # No finalize_batch_status() call — this session ends here,
                # simulating an interrupted process. Batch header status
                # stays 'applying'; 1 item 'skipped', 2 items still 'pending'.

            # Session 4: "resumed" apply — a completely fresh session/connection,
            # calling begin_apply() again on a batch already at status='applying'
            # (proving 'applying' itself is resumable, not just 'partially_applied'
            # or 'failed'), then load_pending_items().
            async with async_session_context() as session:
                await set_tenant(session, tenant_id)
                resumed_batch = await lib.begin_apply(session, tenant_id, batch_id)
                assert resumed_batch["status"] == "applying"

                resumed_pending = await lib.load_pending_items(session, tenant_id, batch_id)
                assert len(resumed_pending) == 2, (
                    "resume must load exactly the 2 still-pending items, excluding the 1 "
                    "already marked 'skipped' before the simulated crash"
                )
                resumed_ids = {item["batch_item_id"] for item in resumed_pending}
                assert first_item_id not in resumed_ids, "the already-terminal item must never be reprocessed"

                # The frozen item population itself is unchanged — still
                # exactly 3 rows total, same snapshot data, regardless of
                # the interruption.
                total_items = (
                    await session.execute(
                        text("SELECT count(*) FROM finance.reconstruction_execution_batch_items WHERE batch_id = :bid"),
                        {"bid": batch_id},
                    )
                ).scalar()
                assert total_items == 3

            # Session 5: finish the resumed run — mark the remaining 2 items
            # 'skipped' (again bypassing real journal posting, same rationale
            # as the other tests in this module) and finalize.
            async with async_session_context() as session:
                await set_tenant(session, tenant_id)
                await session.execute(
                    text(
                        "UPDATE finance.reconstruction_execution_batch_items SET status = 'skipped' "
                        "WHERE batch_id = :bid AND status = 'pending'"
                    ),
                    {"bid": batch_id},
                )
                await session.commit()

            async with async_session_context() as session:
                await set_tenant(session, tenant_id)
                final_pending = await lib.load_pending_items(session, tenant_id, batch_id)
                assert final_pending == []
                # This is the legitimate empty-pending case (all terminal) —
                # must not raise.
                await lib.assert_pending_load_not_silently_empty(
                    session, tenant_id, batch_id, expected_row_count=3, loaded_pending_count=0,
                )
        finally:
            if batch_id:
                async with async_session_context() as session:
                    await set_tenant(session, tenant_id)
                    await session.execute(
                        text("UPDATE finance.bank_transactions SET reconstruction_batch_id = NULL WHERE reconstruction_batch_id = :bid"),
                        {"bid": batch_id},
                    )
                    await session.execute(
                        text("DELETE FROM finance.reconstruction_execution_batch_items WHERE batch_id = :bid"), {"bid": batch_id}
                    )
                    await session.execute(
                        text("DELETE FROM finance.reconstruction_execution_batches WHERE batch_id = :bid"), {"bid": batch_id}
                    )
                    await session.commit()
