"""Tests for the owner-transfer paid split (GAP-FIN-047 follow-up).

Covers the two shared helpers in routers/finance.py that surface, for a unit after a
mid-year ownership transfer, how much of THIS financial year's levy receipts were paid
by the current owner vs a previous owner:

  * _compute_owner_paid_split        — pure query on an already-open PG session
  * _get_owner_paid_split_standalone — session-opening, NON-FATAL wrapper used by the
                                       Mongo-served /levy-status route

Key invariants under test:
  - a lot with no current ownership_periods row => None (missing != zero paid)
  - the wrapper never raises: any PG failure => None, so the Mongo response still renders
  - the wrapper returns None (not a hard error) when there is no scheme / no FY
"""

import os
import sys
from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


class _FakeRows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Returns queued results in order, recording every (sql, params) executed."""

    def __init__(self, results):
        self._results = list(results)
        self.executed = []

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params or {}))
        if not self._results:
            return _FakeRows([])
        return self._results.pop(0)


def _session_context(session):
    @asynccontextmanager
    async def _ctx():
        yield session

    return _ctx


def _scheme():
    return {
        "scheme_id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
    }


# ── _compute_owner_paid_split ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compute_split_returns_current_and_previous_cents():
    from routers.finance import _compute_owner_paid_split

    owner_id = "33333333-3333-3333-3333-333333333333"
    session = _FakeSession([
        _FakeRows([{"owner_party_id": owner_id, "valid_from": date(2026, 3, 15)}]),
        _FakeRows([{"current_owner_cents": 380000, "previous_owners_cents": 250000}]),
    ])

    result = await _compute_owner_paid_split(
        scheme_id="11111111-1111-1111-1111-111111111111",
        unit_number="TH087",
        financial_year="2026",
        session=session,
    )

    assert result == {
        "current_owner_cents": 380000,
        "previous_owners_cents": 250000,
        "current_owner_since": date(2026, 3, 15),
    }
    # The FY-scoped split query must be parameterised by the resolved current owner and unit.
    assert session.executed[1][1]["owner_id"] == owner_id
    assert session.executed[1][1]["unit_number"] == "TH087"
    assert session.executed[1][1]["fy"] == "2026"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_year", ["2025-2026", "FY2026", "", "20260", "abcd"])
async def test_compute_split_none_for_non_4digit_year(bad_year):
    """The split query casts the FY to int, so a financial-year *label* (e.g.
    "2025-2026") must be rejected up front -> None, WITHOUT running any query. This
    keeps a bad year from aborting the unit-dashboard-overview PG branch to its Mongo
    fallback."""
    from routers.finance import _compute_owner_paid_split

    # A session that raises if touched — proves we short-circuit before any query.
    class _ExplodingSession:
        async def execute(self, *a, **k):
            raise AssertionError("must not query for a non-4-digit year")

    result = await _compute_owner_paid_split(
        scheme_id="11111111-1111-1111-1111-111111111111",
        unit_number="TH087",
        financial_year=bad_year,
        session=_ExplodingSession(),
    )
    assert result is None


@pytest.mark.asyncio
async def test_compute_split_binds_cleaned_year_to_query():
    """A year with surrounding whitespace is stripped before it reaches the SQL bind,
    so Postgres never sees ' 2026' in the CAST."""
    from routers.finance import _compute_owner_paid_split

    session = _FakeSession([
        _FakeRows([{"owner_party_id": "33333333-3333-3333-3333-333333333333",
                    "valid_from": date(2026, 1, 1)}]),
        _FakeRows([{"current_owner_cents": 1, "previous_owners_cents": 2}]),
    ])

    await _compute_owner_paid_split(
        scheme_id="11111111-1111-1111-1111-111111111111",
        unit_number="5",
        financial_year="  2026  ",
        session=session,
    )

    assert session.executed[1][1]["fy"] == "2026"


@pytest.mark.asyncio
async def test_compute_split_none_when_no_ownership_period():
    """A lot not yet onboarded into the identity/ownership domain has no
    ownership_periods row -> None (unknown), NOT zero. The split query must not run."""
    from routers.finance import _compute_owner_paid_split

    session = _FakeSession([_FakeRows([])])  # owner query returns nothing

    result = await _compute_owner_paid_split(
        scheme_id="11111111-1111-1111-1111-111111111111",
        unit_number="TH087",
        financial_year="2026",
        session=session,
    )

    assert result is None
    assert len(session.executed) == 1  # short-circuits before the split query


@pytest.mark.asyncio
async def test_compute_split_query_never_drops_a_null_payer_receipt():
    """Audit finding (2026-08-05): `finance.receipts.payer_party_id` is nullable at the
    schema level (migration 0004 has no NOT NULL constraint), even though every receipt
    written through the canonical RecordPaymentCommand path currently populates it. A
    plain SQL `!=` comparison against NULL evaluates to NULL (neither branch matches),
    which would silently drop that receipt's amount from BOTH current_owner_cents and
    previous_owners_cents -- violating the documented invariant "current + previous =
    this FY's receipts total" with no error, no null, just a quietly wrong (understated)
    number. The fix uses "IS [NOT] DISTINCT FROM" instead of "=" / "!=", which treats
    NULL as "not the current owner" (previous_owners_cents) rather than "matches
    neither". This test can't exercise real Postgres NULL semantics through the fake
    session, so it guards the SQL text itself -- it must fail loudly if a future edit
    reverts to a plain "!=" that would reintroduce the silent-drop bug.
    """
    from routers.finance import _compute_owner_paid_split

    session = _FakeSession([
        _FakeRows([{"owner_party_id": "33333333-3333-3333-3333-333333333333",
                    "valid_from": date(2026, 1, 1)}]),
        _FakeRows([{"current_owner_cents": 0, "previous_owners_cents": 0}]),
    ])

    await _compute_owner_paid_split(
        scheme_id="11111111-1111-1111-1111-111111111111",
        unit_number="TH087", financial_year="2026", session=session,
    )

    split_sql = session.executed[1][0]
    assert "IS DISTINCT FROM" in split_sql, (
        "owner-split SQL must use IS DISTINCT FROM (NULL-safe), not a plain != "
        "which silently excludes NULL-payer receipts from both sums"
    )
    assert "payer_party_id !=" not in split_sql
    assert "payer_party_id =" not in split_sql or "IS NOT DISTINCT FROM" in split_sql


@pytest.mark.asyncio
async def test_compute_split_coalesces_null_sums_to_zero_cents():
    """COALESCE in SQL returns 0, but guard the Python side too: a None sum
    (e.g. no receipts) becomes 0 cents, never a crash."""
    from routers.finance import _compute_owner_paid_split

    session = _FakeSession([
        _FakeRows([{"owner_party_id": "33333333-3333-3333-3333-333333333333",
                    "valid_from": date(2026, 1, 1)}]),
        _FakeRows([{"current_owner_cents": None, "previous_owners_cents": None}]),
    ])

    result = await _compute_owner_paid_split(
        scheme_id="11111111-1111-1111-1111-111111111111",
        unit_number="5",
        financial_year="2026",
        session=session,
    )

    assert result["current_owner_cents"] == 0
    assert result["previous_owners_cents"] == 0


# ── _get_owner_paid_split_standalone (non-fatal wrapper) ─────────────────────


@pytest.mark.asyncio
async def test_standalone_happy_path_delegates_to_compute():
    from routers.finance import _get_owner_paid_split_standalone

    session = _FakeSession([
        _FakeRows([{"owner_party_id": "33333333-3333-3333-3333-333333333333",
                    "valid_from": date(2026, 3, 15)}]),
        _FakeRows([{"current_owner_cents": 100000, "previous_owners_cents": 50000}]),
    ])

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context",
              AsyncMock(return_value=_scheme())),
        patch("db_postgres.session.async_session_context", _session_context(session)),
        patch("db_postgres.session.set_tenant", AsyncMock()),
    ):
        result = await _get_owner_paid_split_standalone("13195", "TH087", "2026")

    assert result == {
        "current_owner_cents": 100000,
        "previous_owners_cents": 50000,
        "current_owner_since": date(2026, 3, 15),
    }


@pytest.mark.asyncio
async def test_standalone_none_when_no_scheme():
    from routers.finance import _get_owner_paid_split_standalone

    with patch("db_postgres.repos.config_repo.resolve_scheme_context",
               AsyncMock(return_value=None)):
        result = await _get_owner_paid_split_standalone("13195", "TH087", "2026")

    assert result is None


@pytest.mark.asyncio
async def test_standalone_none_when_no_financial_year():
    from routers.finance import _get_owner_paid_split_standalone

    # Short-circuits before touching Postgres at all.
    with patch("db_postgres.repos.config_repo.resolve_scheme_context",
               AsyncMock(side_effect=AssertionError("must not resolve scheme when fy is None"))):
        result = await _get_owner_paid_split_standalone("13195", "TH087", None)

    assert result is None


@pytest.mark.asyncio
async def test_standalone_is_non_fatal_on_pg_failure():
    """The whole point of the wrapper: a Postgres outage must NOT propagate into the
    otherwise-Mongo-served /levy-status response. Any exception -> None."""
    from routers.finance import _get_owner_paid_split_standalone

    with patch("db_postgres.repos.config_repo.resolve_scheme_context",
               AsyncMock(side_effect=Exception("PG connection refused"))):
        result = await _get_owner_paid_split_standalone("13195", "TH087", "2026")

    assert result is None  # swallowed, not raised
