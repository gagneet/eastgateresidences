# @featuretrace:owner-transfers — Permanent regression coverage for two
#   real bugs found live while finalising TH078's ownership (2026-07-24), neither
#   caught by the existing mock-based test_write_postgres_ownership_period.py.
# Layer: test
# Data flow: core.parties, core.ownership_periods (East Gate, integration-only,
#            RUN_INTEGRATION_TESTS=1).
# Related: backend/db_postgres/repos/ownership_repo.py
#          tests/backend/test_write_postgres_ownership_period.py
#          tasks/GAP-FIN-018-east-gate-demo-bank-to-ledger-pipeline.md
"""Regression tests for two failure modes found live against real Postgres
while finalising TH078's ownership to its sole approved owner:

1. `upsert_owner_party()`'s INSERT used `:meta::jsonb`. SQLAlchemy 2.0.49's
   `text()` bind-param scanner truncates the last character of any parameter
   name immediately followed by `::` (`:meta::jsonb` parses as bindparam
   `met`, not `meta`), raising a raw `PostgresSyntaxError` for any brand-new
   party with no prior email/name match — i.e. every first-time owner
   onboarding, not just TH078. Fixed with `CAST(:meta AS jsonb)`.
2. `close_ownership_period()` unconditionally set `valid_to = :vt` on every
   open row for the lot. A row whose own `valid_from` equals the requested
   `valid_to` (a period recorded in error on the exact date it needs
   correcting — TH078's exact situation) violates
   `ownership_periods_check` (`valid_to > valid_from` strict). Fixed to
   distinguish a genuine business-time closure (`valid_from < valid_to` →
   set `valid_to`) from a bitemporal retraction of an erroneous row
   (`valid_from >= valid_to` → set only `recorded_to`, leave `valid_to`
   NULL — the row never represented a real elapsed ownership interval).

Neither `upsert_owner_party` nor `close_ownership_period` commits
internally — the caller's session controls the transaction — so these tests
use a single session, never commit, and roll back at the end. No explicit
DELETE teardown is required; no row from these tests is ever visible outside
the test's own uncommitted transaction.
"""
from __future__ import annotations

import os
import uuid
from datetime import date

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
async def east_gate_session():
    """A single, never-committed session scoped to East Gate's real
    tenant/scheme, with a dedicated synthetic test lot — rolled back
    automatically on fixture teardown.

    close_ownership_period() closes every open period for a given lot_id,
    not just rows belonging to one party — reusing a real East Gate lot
    would close/retract that lot's genuine, currently-open owner periods
    for the duration of the test (safe since the transaction is rolled
    back, never committed, but still the wrong thing to touch). A private
    is_test_data=True lot, inserted and rolled back in the same
    transaction, keeps this test fully isolated from real ownership data.
    """
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
        lot_row = (
            await session.execute(
                text("""
                     INSERT INTO core.lots (tenant_id, scheme_id, lot_number, is_test_data)
                     VALUES (:tid, :sid, :num, TRUE)
                     RETURNING lot_id::TEXT
                     """),
                {"tid": tenant_id, "sid": scheme_id, "num": f"TEST-{uuid.uuid4().hex[:8]}"},
            )
        ).fetchone()
        assert lot_row is not None
        try:
            yield session, tenant_id, scheme_id, lot_row[0]
        finally:
            await session.rollback()


class TestUpsertOwnerPartyNewInsert:
    """Failure mode #1: the INSERT path (no existing email/name match) is
    only exercised for a genuinely new party — the email-match and
    name-match SELECT branches never touch the buggy VALUES clause."""

    @pytest.mark.asyncio
    async def test_creates_new_party_with_email_metadata(self, east_gate_session):
        from db_postgres.repos import ownership_repo as own_repo

        session, tenant_id, _scheme_id, _lot_id = east_gate_session
        unique_name = f"Integration Test Owner {uuid.uuid4().hex[:8]}"
        unique_email = f"test-{uuid.uuid4().hex[:8]}@example.invalid"

        party_id = await own_repo.upsert_owner_party(session, tenant_id, unique_name, unique_email)

        assert party_id is not None
        from sqlalchemy import text
        row = (
            await session.execute(
                text("SELECT legal_name, metadata->>'email' FROM core.parties WHERE party_id = :pid"),
                {"pid": party_id},
            )
        ).fetchone()
        assert row == (unique_name, unique_email)

    @pytest.mark.asyncio
    async def test_creates_new_party_with_no_email(self, east_gate_session):
        """metadata = {} (no email key at all) must still cast cleanly —
        the bug reproduced regardless of whether the dict was empty or not,
        since the parser truncation happens at parse time, not runtime."""
        from db_postgres.repos import ownership_repo as own_repo

        session, tenant_id, _scheme_id, _lot_id = east_gate_session
        unique_name = f"Integration Test Owner {uuid.uuid4().hex[:8]}"

        party_id = await own_repo.upsert_owner_party(session, tenant_id, unique_name, None)

        assert party_id is not None
        from sqlalchemy import text
        row = (
            await session.execute(
                text("SELECT legal_name, metadata FROM core.parties WHERE party_id = :pid"),
                {"pid": party_id},
            )
        ).fetchone()
        assert row[0] == unique_name
        assert row[1] == {}


async def _insert_open_period_recorded_in_the_past(session, tenant_id, scheme_id, lot_id, party_id, valid_from):
    """Insert an open ownership period with recorded_from an hour in the
    past, bypassing open_ownership_period()'s recorded_from=now() default.

    Real close_ownership_period() calls always close a row recorded in an
    earlier, separate transaction — never the same one that opened it. Since
    Postgres's NOW() returns the same value for every call within a single
    transaction, testing open-then-close back to back in one (deliberately
    uncommitted, for isolation) session would make recorded_from == NOW() at
    close time and spuriously trip ownership_periods_check1 (recorded_to >
    recorded_from) — a test-harness artifact, not the bug under test."""
    from sqlalchemy import text

    row = (
        await session.execute(
            text("""
                 INSERT INTO core.ownership_periods
                     (tenant_id, scheme_id, lot_id, owner_party_id, valid_from, valid_to, recorded_from)
                 VALUES (:tid, :sid, :lid, :pid, :vf, NULL, NOW() - INTERVAL '1 hour')
                 RETURNING ownership_period_id::TEXT
                 """),
            {"tid": str(tenant_id), "sid": str(scheme_id), "lid": str(lot_id), "pid": str(party_id), "vf": valid_from},
        )
    ).fetchone()
    return row[0]


class TestCloseOwnershipPeriodSameDayRetraction:
    """Failure mode #2: closing a period whose own valid_from equals the
    requested valid_to must retract (recorded_to only), not raise."""

    @pytest.mark.asyncio
    async def test_retracts_when_valid_from_equals_requested_valid_to(self, east_gate_session):
        from sqlalchemy import text
        from db_postgres.repos import ownership_repo as own_repo

        session, tenant_id, scheme_id, lot_id = east_gate_session
        party_id = await own_repo.upsert_owner_party(
            session, tenant_id, f"Retraction Test Owner {uuid.uuid4().hex[:8]}", None
        )
        same_day = date(2030, 1, 15)
        period_id = await _insert_open_period_recorded_in_the_past(session, tenant_id, scheme_id, lot_id, party_id, same_day)
        assert period_id is not None

        # Closing on the exact date the row itself opened must not raise
        # ownership_periods_check (valid_to > valid_from).
        closed_count = await own_repo.close_ownership_period(session, lot_id, same_day, tenant_id)
        assert closed_count == 1

        row = (
            await session.execute(
                text(
                    "SELECT valid_from, valid_to, recorded_to IS NOT NULL "
                    "FROM core.ownership_periods WHERE ownership_period_id = :pid"
                ),
                {"pid": period_id},
            )
        ).fetchone()
        assert row[0] == same_day
        assert row[1] is None, "retraction must not fabricate a business-time end date"
        assert row[2] is True, "retraction must still remove the row from the current/open scope"

    @pytest.mark.asyncio
    async def test_normal_closure_still_sets_valid_to(self, east_gate_session):
        """Regression guard: the CASE expression must not break the ordinary
        case (valid_from strictly before the requested valid_to)."""
        from sqlalchemy import text
        from db_postgres.repos import ownership_repo as own_repo

        session, tenant_id, scheme_id, lot_id = east_gate_session
        party_id = await own_repo.upsert_owner_party(
            session, tenant_id, f"Normal Closure Test Owner {uuid.uuid4().hex[:8]}", None
        )
        opened_on = date(2030, 1, 1)
        closed_on = date(2030, 6, 30)
        period_id = await _insert_open_period_recorded_in_the_past(session, tenant_id, scheme_id, lot_id, party_id, opened_on)
        assert period_id is not None

        closed_count = await own_repo.close_ownership_period(session, lot_id, closed_on, tenant_id)
        assert closed_count == 1

        row = (
            await session.execute(
                text("SELECT valid_to FROM core.ownership_periods WHERE ownership_period_id = :pid"),
                {"pid": period_id},
            )
        ).fetchone()
        assert row[0] == closed_on
