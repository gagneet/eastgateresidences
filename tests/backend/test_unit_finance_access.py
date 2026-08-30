# @featuretrace:finance-owner-dashboard — regression tests for PG-token unit context and finance unit access.
# Layer: test
# Data flow: get_current_user backfill / finance guards → user_unit_matches + resolve_canonical_unit_number (building-scoped).
# Related: backend/utils/auth.py
#           backend/utils/unit_number.py
#           backend/routers/finance.py
# Tests: this file
"""Regression tests for the co-owner finance visibility bug (East Gate TH087).

Root cause verified 2026-07-01: Postgres-path JWT sessions fetch the user from
``core.users`` which has no unit columns, so ``current_user["unit_number"]``
was ``None`` and owner-scoped finance guards 403'd owners on their own unit
(ec_member co-owners bypassed the guard via can_manage_finances — producing
the "one owner sees payments, the other sees none" asymmetry).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from utils.auth import _backfill_legacy_unit_context
from utils.unit_number import (
    resolve_canonical_unit_number,
    unit_number_candidates,
    user_unit_matches,
)


# ── user_unit_matches ────────────────────────────────────────────────────────

def test_owner_with_canonical_unit_matches_canonical_request():
    assert user_unit_matches({"unit_number": "TH087"}, "TH087") is True


def test_owner_with_display_unit_matches_canonical_request():
    # Stale profile/JWT value "87" must still grant access to TH087.
    assert user_unit_matches({"unit_number": "87"}, "TH087") is True
    assert user_unit_matches({"unit_number": "U87"}, "TH087") is True


def test_owner_with_canonical_unit_matches_display_request():
    assert user_unit_matches({"unit_number": "TH087"}, "87") is True
    assert user_unit_matches({"unit_number": "TH087"}, "Unit 87") is True


def test_owned_units_list_grants_access():
    user = {"unit_number": None, "owned_units": ["UA001", "TH087"]}
    assert user_unit_matches(user, "TH087") is True
    assert user_unit_matches(user, "UA001") is True


def test_other_unit_is_denied():
    assert user_unit_matches({"unit_number": "TH087"}, "TH086") is False
    assert user_unit_matches({"unit_number": "UA001", "owned_units": ["UA001"]}, "TH087") is False


def test_missing_unit_context_is_denied():
    # The pre-fix PG-session shape: no unit_number, no owned_units → deny,
    # never a false positive.
    assert user_unit_matches({}, "TH087") is False
    assert user_unit_matches({"unit_number": None, "owned_units": []}, "TH087") is False


# ── resolve_canonical_unit_number ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_canonical_unit_number_finds_unit_row():
    db = MagicMock()

    def _units(query, *_a, **_k):
        """Model a real units collection: only TH087 exists.

        The resolver probes the exact normalised token first (an exact match must
        win over variant expansion, or a real unit can resolve to a *different*
        real unit — see resolve_canonical_unit_number's docstring). "87" is not a
        row key here, so that probe misses and the candidate `$in` is what
        resolves it.
        """
        wanted = query.get("unit_number")
        if isinstance(wanted, str):
            return {"unit_number": wanted} if wanted == "TH087" else None
        return {"unit_number": "TH087"} if "TH087" in (wanted or {}).get("$in", []) else None

    db.units.find_one = AsyncMock(side_effect=_units)
    result = await resolve_canonical_unit_number(db, "13195", "87")
    assert result == "TH087"
    expansions = [
        call.args[0] for call in db.units.find_one.await_args_list
        if isinstance(call.args[0].get("unit_number"), dict)
    ]
    assert expansions, "the candidate expansion query was never issued"
    assert expansions[0]["building_id"] == "13195"
    assert "TH087" in expansions[0]["unit_number"]["$in"]


@pytest.mark.asyncio
async def test_resolve_canonical_unit_number_falls_back_to_lot_number():
    db = MagicMock()
    db.units.find_one = AsyncMock(side_effect=[None, {"unit_number": "TH087"}])
    result = await resolve_canonical_unit_number(db, "13195", "LOT87")
    assert result == "TH087"
    assert db.units.find_one.call_count == 2


@pytest.mark.asyncio
async def test_resolve_canonical_unit_number_returns_input_when_unknown():
    db = MagicMock()
    db.units.find_one = AsyncMock(return_value=None)
    assert await resolve_canonical_unit_number(db, "13195", "Unit 999") == "999"


@pytest.mark.asyncio
async def test_resolve_canonical_unit_number_is_building_scoped():
    # Multi-tenant rule: the lookup must carry the caller's building_id.
    db = MagicMock()
    db.units.find_one = AsyncMock(return_value=None)
    await resolve_canonical_unit_number(db, "16244", "87")
    for call in db.units.find_one.call_args_list:
        assert call.args[0]["building_id"] == "16244"


# ── PG-session unit-context backfill ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_backfill_merges_unit_context_from_mongo():
    legacy = {
        "id": "mongo-user-id",
        "unit_number": "TH087",
        "owned_units": ["TH087"],
        "custom_permissions": {"can_view_finances": True},
    }
    user = {"email": "avneet@eastgateresidences.com.au", "role": "owner"}
    with patch("utils.auth.db") as mock_db:
        mock_db.users.find_one = AsyncMock(return_value=legacy)
        await _backfill_legacy_unit_context(user)
    assert user["unit_number"] == "TH087"
    assert user["owned_units"] == ["TH087"]
    assert user["custom_permissions"] == {"can_view_finances": True}
    assert user["legacy_user_id"] == "mongo-user-id"


@pytest.mark.asyncio
async def test_backfill_does_not_override_existing_values():
    user = {
        "email": "avneet@eastgateresidences.com.au",
        "unit_number": "UA001",
        "owned_units": ["UA001"],
    }
    with patch("utils.auth.db") as mock_db:
        mock_db.users.find_one = AsyncMock(return_value={"unit_number": "TH087"})
        await _backfill_legacy_unit_context(user)
    # Both fields already present → helper returns before querying.
    mock_db.users.find_one.assert_not_called()
    assert user["unit_number"] == "UA001"


@pytest.mark.asyncio
async def test_backfill_survives_missing_legacy_user_and_db_errors():
    user = {"email": "nobody@example.com"}
    with patch("utils.auth.db") as mock_db:
        mock_db.users.find_one = AsyncMock(return_value=None)
        await _backfill_legacy_unit_context(user)
    assert "unit_number" not in user

    user2 = {"email": "nobody@example.com"}
    with patch("utils.auth.db") as mock_db:
        mock_db.users.find_one = AsyncMock(side_effect=RuntimeError("mongo down"))
        await _backfill_legacy_unit_context(user2)
    assert "unit_number" not in user2


# ── Candidate expansion sanity (guards both directions of the bug) ──────────

def test_candidates_bidirectional_th087():
    assert "TH087" in unit_number_candidates("87")
    assert "87" in unit_number_candidates("TH087")
