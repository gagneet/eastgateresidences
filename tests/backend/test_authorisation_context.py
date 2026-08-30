"""
Tests for services/authorisation_context.py (ACL plan Phase 2).

The load-bearing test here is `test_office_at_one_building_does_not_carry_to_another`:
before this module existed, `identity_repo.get_user_by_id` resolved `ec_position`
from the user's DEFAULT scheme and `capability_registry.can()` then tested that
office against the REQUESTED building. A treasurer at building A passed the
treasurer office gate at building B.

The rest pin the fail-closed contract: every failure path must yield FEWER
claims, never more.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.authorisation_context import (
    POLICY_VERSION,
    _canonical_office,
    hydrate_authorisation_claims,
)
from services.capability_registry import can


def _user(**over) -> dict:
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "party_id": "22222222-2222-2222-2222-222222222222",
        "tenant_id": "33333333-3333-3333-3333-333333333333",
        "role": "ec_member",
        "effective_role": "ec_member",
        "building_id": "BLD-A",
    }
    base.update(over)
    return base


# ── canonical office vocabulary ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "stored,expected",
    [
        ("CHAIRMAN", "chairperson"),   # legacy stored form
        ("chairman", "chairperson"),
        ("Chair", "chairperson"),
        ("TREASURER", "treasurer"),
        ("SECRETARY", "secretary"),
        ("MEMBER", "ordinary_member"),
        ("ordinary_member", "ordinary_member"),
        ("", None),
        (None, None),
        ("not_an_office", None),       # unknown offices are dropped, not passed through
    ],
)
def test_canonical_office_mapping(stored, expected):
    assert _canonical_office(stored) == expected


# ── fail-closed paths ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_none_user_returns_empty_mapping():
    assert await hydrate_authorisation_claims(None, {"building_id": "BLD-A"}) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_over,scope",
    [
        ({"tenant_id": None}, {"building_id": "BLD-A"}),   # no Postgres identity
        ({"id": None}, {"building_id": "BLD-A"}),          # no user id
        ({"building_id": None}, {}),                       # no resolvable scope
    ],
)
async def test_unresolvable_subject_yields_deny_shaped_claims(user_over, scope):
    claims = await hydrate_authorisation_claims(_user(**user_over), scope)

    assert claims["governance_offices"] == []
    assert claims["ec_position"] is None
    assert claims["assigned_building_ids"] == []
    assert claims["active_resolution_ids"] == []
    assert claims["active_delegation_ids"] == []
    assert claims["policy_version"] == POLICY_VERSION


@pytest.mark.asyncio
async def test_database_failure_denies_rather_than_inheriting_claims():
    """A hydration error must not leave the caller's own claims standing."""
    user = _user(governance_offices=["treasurer"], ec_position="TREASURER")

    with patch(
        "services.authorisation_context._resolve_target_scheme",
        new=AsyncMock(side_effect=RuntimeError("postgres down")),
    ):
        claims = await hydrate_authorisation_claims(user, {"building_id": "BLD-A"})

    assert claims["governance_offices"] == []
    assert claims["ec_position"] is None


@pytest.mark.asyncio
async def test_unknown_scheme_denies():
    with patch(
        "services.authorisation_context._resolve_target_scheme",
        new=AsyncMock(return_value=None),
    ):
        claims = await hydrate_authorisation_claims(_user(), {"building_id": "nope"})

    assert claims["governance_offices"] == []


@pytest.mark.asyncio
async def test_input_mapping_is_never_mutated():
    user = _user()
    snapshot = dict(user)

    with patch(
        "services.authorisation_context._resolve_target_scheme",
        new=AsyncMock(return_value=None),
    ):
        await hydrate_authorisation_claims(user, {"building_id": "BLD-A"})

    assert user == snapshot


@pytest.mark.asyncio
async def test_authority_claims_are_empty_when_no_authority_is_held():
    """A treasurer with no recorded EC authority still cannot execute a payment."""
    with patch(
        "services.authorisation_context._resolve_target_scheme",
        new=AsyncMock(return_value={"scheme_id": "44444444-4444-4444-4444-444444444444"}),
    ), patch(
        "services.authorisation_context._offices_for_scheme",
        new=AsyncMock(return_value=["treasurer"]),
    ), patch(
        "services.authorisation_context._assigned_buildings",
        new=AsyncMock(return_value=["BLD-A"]),
    ), patch(
        "services.authorisation_context._active_authority_ids",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.authorisation_context._active_delegation_ids",
        new=AsyncMock(return_value=[]),
    ), patch("db_postgres.session.set_tenant", new=AsyncMock()):
        claims = await hydrate_authorisation_claims(_user(), {"building_id": "BLD-A"})

    assert claims["active_resolution_ids"] == []
    assert claims["active_delegation_ids"] == []
    # A treasurer with no recorded EC authority cannot execute a payment.
    assert not can(
        claims,
        "building.finance.payment.execute",
        {"building_id": "BLD-A", "resolution_id": "any"},
    )


# ── the cross-building office leak ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_office_at_one_building_does_not_carry_to_another():
    """Treasurer at BLD-A must not pass the treasurer gate at BLD-B.

    The hydrator resolves the office for the REQUESTED scheme and overwrites any
    inherited value, so the stale default-scheme office cannot survive.
    """
    # The user arrives carrying a treasurer office inherited from their default
    # scheme — exactly what identity_repo.get_user_by_id puts there today.
    user = _user(building_id="BLD-A", ec_position="TREASURER",
                 governance_offices=["treasurer"])

    async def offices_by_scheme(session, u, scheme_id):
        # Treasurer at scheme A only; an ordinary member at scheme B.
        return ["treasurer"] if scheme_id == "SCHEME-A" else ["ordinary_member"]

    async def resolve(building_value):
        return {"scheme_id": "SCHEME-A" if building_value == "BLD-A" else "SCHEME-B"}

    with patch("services.authorisation_context._resolve_target_scheme",
               new=AsyncMock(side_effect=resolve)), \
         patch("services.authorisation_context._offices_for_scheme",
               new=AsyncMock(side_effect=offices_by_scheme)), \
         patch("services.authorisation_context._assigned_buildings",
               new=AsyncMock(return_value=["BLD-A", "BLD-B"])), \
         patch("services.authorisation_context._active_authority_ids",
               new=AsyncMock(return_value=[])), \
         patch("services.authorisation_context._active_delegation_ids",
               new=AsyncMock(return_value=[])), \
         patch("db_postgres.session.set_tenant", new=AsyncMock()):

        at_a = await hydrate_authorisation_claims(user, {"building_id": "BLD-A"})
        at_b = await hydrate_authorisation_claims(user, {"building_id": "BLD-B"})

    assert at_a["governance_offices"] == ["treasurer"]
    assert at_b["governance_offices"] == ["ordinary_member"]
    # The inherited claim is overwritten, not merged.
    assert "treasurer" not in at_b["governance_offices"]
    assert at_b["ec_position"] == "ordinary_member"

    # And the capability decision follows.
    assert can(at_a, "building.finance.records.prepare", {"building_id": "BLD-A"})
    assert not can(at_b, "building.finance.records.prepare", {"building_id": "BLD-B"})


@pytest.mark.asyncio
async def test_hydrated_manager_building_claims_scope_the_decision():
    """assigned_building_ids was read by can() and written by nothing before Phase 2."""
    manager = _user(role="strata_manager", effective_role="strata_manager",
                    ec_position=None, party_id=None)

    with patch("services.authorisation_context._resolve_target_scheme",
               new=AsyncMock(return_value={"scheme_id": "SCHEME-A"})), \
         patch("services.authorisation_context._offices_for_scheme",
               new=AsyncMock(return_value=[])), \
         patch("services.authorisation_context._assigned_buildings",
               new=AsyncMock(return_value=["BLD-A"])), \
         patch("services.authorisation_context._active_authority_ids",
               new=AsyncMock(return_value=[])), \
         patch("services.authorisation_context._active_delegation_ids",
               new=AsyncMock(return_value=[])), \
         patch("db_postgres.session.set_tenant", new=AsyncMock()):
        claims = await hydrate_authorisation_claims(manager, {"building_id": "BLD-A"})

    assert claims["assigned_building_ids"] == ["BLD-A"]
    assert can(claims, "building.finance.manage", {"building_id": "BLD-A"})
    assert not can(claims, "building.finance.manage", {"building_id": "BLD-B"})


# ── authority and delegation hydration (Phase 4 / GAP-SEC-004) ───────────────

def _pg_row(value):
    """A minimal stand-in for a SQLAlchemy result row."""
    return (value,)


class _FakeResult:
    def __init__(self, rows): self._rows = rows
    def __iter__(self): return iter(self._rows)


class _FakeSession:
    """Returns a canned result per query, matched on a substring of the SQL."""

    def __init__(self, by_fragment: dict[str, list]):
        self._by_fragment = by_fragment
        self.executed: list[str] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append(sql)
        for fragment, rows in self._by_fragment.items():
            if fragment in sql:
                return _FakeResult(rows)
        return _FakeResult([])


@pytest.mark.asyncio
async def test_active_authorities_are_resolved_for_the_requested_scheme():
    from services.authorisation_context import _active_authority_ids

    session = _FakeSession({
        "core.user_role_assignments": [_pg_row("TREASURER")],
        "governance.ec_members": [],
        "governance.authorities": [_pg_row("auth-1"), _pg_row("auth-2")],
    })
    ids = await _active_authority_ids(session, _user(), "SCHEME-A")

    assert ids == ["auth-1", "auth-2"]
    authority_sql = next(s for s in session.executed if "governance.authorities" in s)
    # The query must constrain on all four liveness dimensions, not just scheme.
    for clause in ("scheme_id", "status", "revoked_at", "effective_from", "effective_to"):
        assert clause in authority_sql, f"authority query must filter on {clause}"


@pytest.mark.asyncio
async def test_authorities_are_matched_by_office_as_well_as_by_user():
    """An EC resolution is usually granted to the treasurer, not to a named person."""
    from services.authorisation_context import _active_authority_ids

    session = _FakeSession({
        "core.user_role_assignments": [_pg_row("TREASURER")],
        "governance.ec_members": [],
        "governance.authorities": [_pg_row("auth-1")],
    })
    await _active_authority_ids(session, _user(), "SCHEME-A")

    sql = next(s for s in session.executed if "governance.authorities" in s)
    assert "granted_to_office" in sql
    assert "granted_to_user_id" in sql


@pytest.mark.asyncio
async def test_delegations_are_personal_and_window_bounded():
    from services.authorisation_context import _active_delegation_ids

    session = _FakeSession({"governance.delegations": [_pg_row("del-1")]})
    ids = await _active_delegation_ids(session, _user(), "SCHEME-A")

    assert ids == ["del-1"]
    sql = session.executed[0]
    assert "grantee_user_id" in sql, "a delegation names a person, never an office"
    assert "revoked_at" in sql and "starts_at" in sql and "ends_at" in sql


@pytest.mark.asyncio
async def test_a_live_resolution_lets_the_treasurer_execute_a_payment():
    """The end-to-end point of GAP-SEC-004: this was unreachable before."""
    with patch("services.authorisation_context._resolve_target_scheme",
               new=AsyncMock(return_value={"scheme_id": "SCHEME-A"})), \
         patch("services.authorisation_context._offices_for_scheme",
               new=AsyncMock(return_value=["treasurer"])), \
         patch("services.authorisation_context._assigned_buildings",
               new=AsyncMock(return_value=["BLD-A"])), \
         patch("services.authorisation_context._active_authority_ids",
               new=AsyncMock(return_value=["auth-1"])), \
         patch("services.authorisation_context._active_delegation_ids",
               new=AsyncMock(return_value=[])), \
         patch("db_postgres.session.set_tenant", new=AsyncMock()):
        claims = await hydrate_authorisation_claims(_user(), {"building_id": "BLD-A"})

    assert claims["active_resolution_ids"] == ["auth-1"]
    # Granted, but only against the authority actually held.
    assert can(claims, "building.finance.payment.execute",
               {"building_id": "BLD-A", "resolution_id": "auth-1"})
    assert not can(claims, "building.finance.payment.execute",
                   {"building_id": "BLD-A", "resolution_id": "some-other-authority"})


@pytest.mark.asyncio
async def test_no_live_resolution_still_denies_the_treasurer():
    with patch("services.authorisation_context._resolve_target_scheme",
               new=AsyncMock(return_value={"scheme_id": "SCHEME-A"})), \
         patch("services.authorisation_context._offices_for_scheme",
               new=AsyncMock(return_value=["treasurer"])), \
         patch("services.authorisation_context._assigned_buildings",
               new=AsyncMock(return_value=["BLD-A"])), \
         patch("services.authorisation_context._active_authority_ids",
               new=AsyncMock(return_value=[])), \
         patch("services.authorisation_context._active_delegation_ids",
               new=AsyncMock(return_value=[])), \
         patch("db_postgres.session.set_tenant", new=AsyncMock()):
        claims = await hydrate_authorisation_claims(_user(), {"building_id": "BLD-A"})

    assert not can(claims, "building.finance.payment.execute",
                   {"building_id": "BLD-A", "resolution_id": "auth-1"})
