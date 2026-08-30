# @featuretrace:financial_integration_v2 — Permanent regression coverage for a
#   bitemporal-retraction bug found auditing the 2026-07-24 ownership_repo.py fix.
# Layer: test
# Data flow: core.ownership_periods (East Gate, integration-only, RUN_INTEGRATION_TESTS=1).
# Related: backend/routers/financial_matching.py::_post_payment_to_ledger
#          backend/db_postgres/repos/ownership_repo.py::close_ownership_period
#          tests/backend/test_ownership_repo_integration.py
"""Regression test for a real bug found auditing the 2026-07-24 TH078 fix.

`close_ownership_period()`'s retraction branch (added 2026-07-24) leaves a
row with `valid_to IS NULL` and `recorded_to` set — the first time this
row-shape could ever exist (previously `close_ownership_period()` always set
both columns together, so `valid_to IS NULL` implied `recorded_to IS NULL`
everywhere in the codebase). `_post_payment_to_ledger()`'s owner-resolution
query in `financial_matching.py` filtered only on `valid_to`/`valid_from`,
never `recorded_to` — so a retracted row and the row that superseded it can
both match the same `WHERE ... LIMIT 1` with no `ORDER BY`, making payment
payer-resolution non-deterministic (silently correct or silently wrong,
depending on Postgres's internal row order) for any lot with a retracted
period. Confirmed live against TH078 before the fix: the query matched both
the retracted Olivia Rollings row and the correct Tavis Christian Hamer row
for a payment dated after the correction. Fixed by adding
`AND recorded_to IS NULL`.

This test reproduces the exact shape with a synthetic, is_test_data=TRUE lot
(never touches real East Gate ownership data) and asserts the fixed query
(mirrored inline — the real query lives in an f-string inside a large
private function not worth invoking end-to-end here) returns exactly the
live owner, never the retracted one.
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


# Mirrors the fixed query in backend/routers/financial_matching.py::_post_payment_to_ledger.
_OWNER_RESOLUTION_QUERY = """
    SELECT owner_party_id::text FROM core.ownership_periods
    WHERE lot_id = :lot_id AND is_primary_owner = TRUE
    AND recorded_to IS NULL
    AND valid_from <= :received_on
    AND (valid_to IS NULL OR valid_to >= :received_on)
    LIMIT 1
"""


@pytest.fixture
async def east_gate_session():
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
        try:
            yield session, tenant_id, scheme_id, lot_row[0]
        finally:
            await session.rollback()


class TestOwnerResolutionIgnoresRetractedPeriods:
    @pytest.mark.asyncio
    async def test_retracted_row_and_live_row_both_match_without_recorded_to_filter(self, east_gate_session):
        """Proves the bug: without the recorded_to filter, both rows match —
        the exact non-determinism found live for TH078."""
        from sqlalchemy import text
        from db_postgres.repos import ownership_repo as own_repo

        session, tenant_id, scheme_id, lot_id = east_gate_session
        outgoing_party = await own_repo.upsert_owner_party(session, tenant_id, f"Outgoing Owner {uuid.uuid4().hex[:8]}", None)
        incoming_party = await own_repo.upsert_owner_party(session, tenant_id, f"Incoming Owner {uuid.uuid4().hex[:8]}", None)
        settlement = date(2030, 3, 1)

        # Simulate the exact TH078 shape: a stale "current" row for the
        # outgoing owner, later retracted, plus the correct incoming owner's
        # row — both starting the same date.
        await _insert_open_period(session, tenant_id, scheme_id, lot_id, outgoing_party, settlement)
        await own_repo.close_ownership_period(session, lot_id, settlement, tenant_id)
        incoming_period_id = await own_repo.open_ownership_period(session, tenant_id, scheme_id, lot_id, incoming_party, settlement)
        assert incoming_period_id is not None

        received_on = date(2030, 3, 15)
        buggy_query = _OWNER_RESOLUTION_QUERY.replace("AND recorded_to IS NULL\n    ", "")
        # LIMIT 1 means only one row surfaces from the buggy query, but a
        # plain COUNT without the LIMIT proves both rows are eligible —
        # exactly the non-determinism risk (Postgres gives no ordering
        # guarantee for which of the two the LIMIT 1 actually returns).
        count_rows = (
            await session.execute(
                text(buggy_query.replace("LIMIT 1", "")),
                {"lot_id": lot_id, "received_on": received_on},
            )
        ).fetchall()
        assert len(count_rows) == 2, "expected both the retracted and live rows to be eligible under the buggy query"

    @pytest.mark.asyncio
    async def test_fixed_query_returns_only_the_live_owner(self, east_gate_session):
        from sqlalchemy import text
        from db_postgres.repos import ownership_repo as own_repo

        session, tenant_id, scheme_id, lot_id = east_gate_session
        outgoing_party = await own_repo.upsert_owner_party(session, tenant_id, f"Outgoing Owner {uuid.uuid4().hex[:8]}", None)
        incoming_party = await own_repo.upsert_owner_party(session, tenant_id, f"Incoming Owner {uuid.uuid4().hex[:8]}", None)
        settlement = date(2030, 3, 1)

        await _insert_open_period(session, tenant_id, scheme_id, lot_id, outgoing_party, settlement)
        await own_repo.close_ownership_period(session, lot_id, settlement, tenant_id)
        await own_repo.open_ownership_period(session, tenant_id, scheme_id, lot_id, incoming_party, settlement)

        received_on = date(2030, 3, 15)
        rows = (
            await session.execute(text(_OWNER_RESOLUTION_QUERY), {"lot_id": lot_id, "received_on": received_on})
        ).fetchall()

        assert len(rows) == 1
        assert rows[0].owner_party_id == incoming_party


async def _insert_open_period(session, tenant_id, scheme_id, lot_id, party_id, valid_from):
    """core.ownership_periods.is_primary_owner defaults TRUE — matches the
    real TH078 shape (outgoing owner was primary) without needing the
    _with_share() variant.

    recorded_from is backdated an hour, same reason as
    test_ownership_repo_integration.py's helper of the same shape: Postgres's
    NOW() returns one fixed value for the whole transaction, so a later
    close_ownership_period() call in the same (deliberately uncommitted, for
    isolation) session would otherwise set recorded_to == recorded_from and
    spuriously trip ownership_periods_check1 (recorded_to > recorded_from)."""
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
