# @featuretrace:owner-transfers — guards that approving a transfer maintains core.user_units,
# not only core.ownership_periods.
# Layer: test
# Data flow: server._finalize_owner_transfer_approval -> _sync_postgres_user_units_for_transfer
#            -> core.user_units / core.user_role_assignments (building-scoped).
# Related: backend/server.py (_sync_postgres_user_units_for_transfer,
#            _write_postgres_ownership_period)
#          backend/db_postgres/repos/identity_repo.py (list_active_users_for_scheme)
# Toggle: none
"""Approving an owner transfer must retire the seller's core.user_units link.

WHY THIS GUARD EXISTS
---------------------
Until 2026-08-27 the approval path wrote ``core.ownership_periods`` and thirteen
MongoDB collections but never touched ``core.user_units`` — no code anywhere
closed a link in that table. ``list_active_users_for_scheme`` (the read behind
GET /users for a building whose ``identity_core`` is promoted) resolves membership
from ``core.user_units`` OR ``core.user_role_assignments``, NOT from
``core.ownership_periods``. So a sold-and-approved unit went on listing its former
owners as current owners forever, while the bitemporal ownership table said
correctly that they had gone.

Found live on East Gate TH078: ownership_periods had closed Olivia Rollings and
Mark Raets at 2026-07-01 and opened Tavis Christian Hamer at 2026-07-02, yet all
three still held open ``core.user_units`` rows and all three appeared on
/admin/users as owners of the same lot.

WHAT THESE TESTS PIN
--------------------
Beyond the happy path, they pin the transaction boundary that a follow-up audit
added. ``core.user_units`` has a FOREIGN KEY on ``user_id`` and a UNIQUE index on
``(user_id, lot_id, valid_from)``, and the buyer id reaching this function
satisfies neither in two real cases: the caller mints a fresh uuid4 for an
internal-contact owner that exists in MongoDB only, and a legacy MongoDB id may
not be a UUID at all. Done in one transaction, either aborts the seller closure
too — producing precisely the stale state the function exists to prevent. The
closure therefore commits before the buyer insert is attempted, and
``test_buyer_without_a_postgres_row_still_retires_the_seller`` is what stops that
being collapsed back into one transaction by a later refactor.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import server  # noqa: E402

SCHEME_ID = "11111111-1111-1111-1111-111111111111"
TENANT_ID = "22222222-2222-2222-2222-222222222222"
LOT_ID = "33333333-3333-3333-3333-333333333333"
PARTY_ID = "44444444-4444-4444-4444-444444444444"
SELLER_ID = "55555555-5555-5555-5555-555555555555"
BUYER_ID = "66666666-6666-6666-6666-666666666666"

BUILDING_ID = "13195"


class _Session:
    """Async session double that records SQL and can answer the buyer-exists probe.

    `buyer_exists` drives the `SELECT 1 FROM core.users` guard so a test can
    choose whether the buyer has a Postgres identity without patching the query.
    """

    def __init__(self, buyer_exists: bool = True):
        self.statements: list[tuple[str, dict]] = []
        self._buyer_exists = buyer_exists
        self.committed = False

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, params or {}))
        result = MagicMock()
        result.rowcount = 1
        result.scalar = MagicMock(return_value=1 if self._buyer_exists else None)
        return result

    async def commit(self):
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _SessionFactory:
    """Hands out a fresh session per `async with`, recording each one.

    The function under test opens TWO independent transactions; a single shared
    session double would hide a regression that merged them back into one.
    """

    def __init__(self, buyer_exists: bool = True):
        self.sessions: list[_Session] = []
        self._buyer_exists = buyer_exists

    def __call__(self):
        session = _Session(buyer_exists=self._buyer_exists)
        self.sessions.append(session)
        return session

    @property
    def all_statements(self) -> list[tuple[str, dict]]:
        return [item for session in self.sessions for item in session.statements]


def _matching(statements, *needles: str) -> list[tuple[str, dict]]:
    return [(sql, params) for sql, params in statements
            if all(needle in sql for needle in needles)]


def _patches(factory: _SessionFactory, *, lot_id=LOT_ID):
    return (
        patch("db_postgres.repos.identity_repo.get_scheme_by_number",
              AsyncMock(return_value={"scheme_id": SCHEME_ID, "tenant_id": TENANT_ID})),
        patch("db_postgres.session.async_session_context", factory),
        patch("db_postgres.session.set_tenant", AsyncMock()),
        patch("db_postgres.repos.ownership_repo.get_lot_id_by_number",
              AsyncMock(return_value=lot_id)),
        patch("db_postgres.repos.ownership_repo.upsert_owner_party",
              AsyncMock(return_value=PARTY_ID)),
    )


async def _run(factory, **overrides):
    kwargs = dict(
        building_id=BUILDING_ID,
        unit_number="TH078",
        outgoing_user_ids=[SELLER_ID],
        new_owner_id=BUYER_ID,
        new_owner_name="Tavis Christian Hamer",
        new_owner_email="buyer@example.invalid",
        settlement_date="2026-07-02",
    )
    kwargs.update(overrides)
    ctx = _patches(factory)
    for c in ctx:
        c.start()
    try:
        await server._sync_postgres_user_units_for_transfer(**kwargs)
    finally:
        for c in ctx:
            c.stop()


@pytest.mark.asyncio
async def test_seller_link_closed_and_buyer_link_opened():
    """The happy path: seller end-dated on the settlement date, buyer linked."""
    factory = _SessionFactory()
    await _run(factory)

    closes = _matching(factory.all_statements, "UPDATE core.user_units", "valid_to = :end_date")
    assert closes, "the seller's core.user_units link was never closed"
    assert SELLER_ID in closes[0][1]["user_ids"]
    assert closes[0][1]["end_date"] == date(2026, 7, 2), "link must end on the settlement date"

    # A role assignment outlives the link unless retired with it, and
    # list_active_users_for_scheme accepts either as proof of membership.
    retires = _matching(factory.all_statements, "core.user_role_assignments", "is_active = FALSE")
    assert retires, "the seller's owner role assignment was left active"
    assert "NOT EXISTS" in retires[0][0], (
        "the role retirement must be scoped so a seller who still holds another "
        "lot in this scheme keeps their owner role"
    )

    opens = _matching(factory.all_statements, "INSERT INTO core.user_units")
    assert opens, "the buyer never got a core.user_units link"
    assert opens[0][1]["user_id"] == BUYER_ID
    assert opens[0][1]["valid_from"] == date(2026, 7, 2)


@pytest.mark.asyncio
async def test_seller_closure_and_buyer_insert_are_separate_transactions():
    """Two sessions, so a buyer-side failure cannot roll back the seller closure."""
    factory = _SessionFactory()
    await _run(factory)
    assert len(factory.sessions) == 2, (
        f"expected the closure and the insert in separate transactions, got "
        f"{len(factory.sessions)} session(s) — a buyer-side error would now undo "
        f"the seller retirement, recreating the exact bug this function fixes"
    )
    assert _matching(factory.sessions[0].statements, "UPDATE core.user_units", "valid_to")
    assert _matching(factory.sessions[1].statements, "INSERT INTO core.user_units")


@pytest.mark.asyncio
async def test_buyer_insert_tolerates_the_unique_index():
    """A buyer reacquiring a lot on a date they already have a row for must not raise.

    WHERE NOT EXISTS only covers an OPEN link; a CLOSED prior period with the same
    valid_from still violates user_units_user_id_lot_id_valid_from_key.
    """
    factory = _SessionFactory()
    await _run(factory)
    opens = _matching(factory.all_statements, "INSERT INTO core.user_units")
    assert "ON CONFLICT (user_id, lot_id, valid_from) DO NOTHING" in opens[0][0]


@pytest.mark.asyncio
async def test_buyer_without_a_postgres_row_still_retires_the_seller():
    """Internal-contact owners exist only in MongoDB; the seller must still be retired."""
    factory = _SessionFactory(buyer_exists=False)
    await _run(factory)

    assert _matching(factory.all_statements, "UPDATE core.user_units", "valid_to = :end_date"), (
        "seller retirement must not depend on the buyer having a Postgres identity"
    )
    assert not _matching(factory.all_statements, "INSERT INTO core.user_units"), (
        "must not attempt an insert that the user_id foreign key would reject"
    )


@pytest.mark.asyncio
async def test_non_uuid_buyer_id_is_skipped_not_cast():
    """A legacy MongoDB id must never reach CAST(... AS UUID)."""
    factory = _SessionFactory()
    await _run(factory, new_owner_id="legacy-mongo-id")
    assert _matching(factory.all_statements, "UPDATE core.user_units", "valid_to = :end_date"), (
        "seller retirement must still happen for a legacy buyer id"
    )
    assert not _matching(factory.all_statements, "INSERT INTO core.user_units")


@pytest.mark.asyncio
async def test_non_uuid_seller_ids_are_filtered_out():
    """Legacy seller ids are dropped rather than aborting the statement."""
    factory = _SessionFactory()
    await _run(factory, outgoing_user_ids=["legacy-mongo-id", SELLER_ID])
    closes = _matching(factory.all_statements, "UPDATE core.user_units", "valid_to = :end_date")
    assert closes[0][1]["user_ids"] == [SELLER_ID]


@pytest.mark.asyncio
async def test_all_seller_ids_legacy_means_no_update_attempted():
    """With nothing castable there is no statement to run — and no crash."""
    factory = _SessionFactory()
    await _run(factory, outgoing_user_ids=["a", "b"])
    assert not _matching(factory.all_statements, "UPDATE core.user_units", "valid_to = :end_date")


@pytest.mark.asyncio
async def test_sync_never_raises_when_postgres_is_unreachable():
    """MongoDB has already committed by this point, so a PG failure must not raise."""
    with patch("db_postgres.repos.identity_repo.get_scheme_by_number",
               AsyncMock(side_effect=RuntimeError("postgres down"))):
        await server._sync_postgres_user_units_for_transfer(
            building_id=BUILDING_ID, unit_number="TH078",
            outgoing_user_ids=[SELLER_ID], new_owner_id=BUYER_ID,
            new_owner_name="Buyer", new_owner_email="buyer@example.invalid",
            settlement_date="2026-07-02",
        )


@pytest.mark.asyncio
async def test_sync_skips_cleanly_when_lot_absent_from_postgres():
    """A building not yet onboarded to core.lots is skipped, not half-written."""
    factory = _SessionFactory()
    ctx = _patches(factory, lot_id=None)
    for c in ctx:
        c.start()
    try:
        await server._sync_postgres_user_units_for_transfer(
            building_id=BUILDING_ID, unit_number="ZZ999",
            outgoing_user_ids=[SELLER_ID], new_owner_id=BUYER_ID,
            new_owner_name="Buyer", new_owner_email="buyer@example.invalid",
            settlement_date="2026-07-02",
        )
    finally:
        for c in ctx:
            c.stop()
    assert not _matching(factory.all_statements, "core.user_units")


@pytest.mark.asyncio
async def test_unparseable_settlement_date_falls_back_to_today():
    """A malformed settlement date must not abort the retirement."""
    factory = _SessionFactory()
    await _run(factory, settlement_date="not-a-date")
    closes = _matching(factory.all_statements, "UPDATE core.user_units", "valid_to = :end_date")
    assert closes[0][1]["end_date"] == date.today()


def test_approval_path_still_calls_the_user_units_sync():
    """Static guard: the call site must not be dropped from the approval path.

    A behavioural test of _finalize_owner_transfer_approval would need the whole
    thirteen-collection MongoDB surface mocked; this instead pins the one line
    whose absence caused the incident, next to its ownership_periods sibling.
    """
    source = (BACKEND / "server.py").read_text(encoding="utf-8")
    assert "_fire_and_forget(_sync_postgres_user_units_for_transfer(" in source, (
        "the approval path no longer syncs core.user_units — /admin/users will "
        "keep listing sellers as current owners on promoted buildings"
    )
    assert source.index("_fire_and_forget(_write_postgres_ownership_period(") < source.index(
        "_fire_and_forget(_sync_postgres_user_units_for_transfer("
    ), "both Postgres writes must stay together in the approval path"
