"""
# @featuretrace:multi-unit-ownership — guards the ?unit_number= parameter and its authorisation gate.
# Layer: test
# Data flow: mocked db + session dict → authorise_owner_unit / owner_finance routes (building-scoped).
# Related: backend/utils/unit_number.py
#          backend/routers/owner_finance.py
#          frontend/src/hooks/useActiveUnit.ts

Test Suite: Owner-finance per-unit scoping (GAP-IDENTITY-UNIT-SWITCH-001)
=========================================================================
Unit tests (mocked DB — no live backend required) for the ``unit_number``
request parameter added to ``/owner-finance/levy-breakdown`` and
``/owner-finance/savings-summary``, and for the authorisation helper that
gates it.

The bug being guarded: an owner with two units in the same building switched
units in the sidebar and every figure on My Finances kept showing the account's
default unit, because the endpoint derived the unit purely from the session and
the page never asked for one. Letting the page name the unit only works if the
caller is verified to hold it — hence ``authorise_owner_unit``.

What must stay true:
  1. No parameter → unchanged behaviour (the account's own unit).
  2. A parameter for a unit the caller holds → that unit's figures.
  3. A parameter for a unit the caller does NOT hold → 403, never a silent
     fallback to the default unit. Quietly answering about a different unit
     than the one asked for is how a wrong figure gets trusted.
  4. Display variants resolve (``87`` and ``TH087`` are the same lot).
  5. Cross-building isolation: a link in another building is not a link here.

Run with:
    backend/venv/bin/python3 -m pytest tests/backend/test_owner_finance_unit_scope.py -q
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastapi import HTTPException

BUILDING_ID = "13195"


def _owner(units=None, unit_number=None, user_id="user-multi-1"):
    """An owner session dict as ``get_current_user`` would produce it."""
    return {
        "id": user_id,
        "email": "multi.owner@example.invalid",
        "full_name": "Multi Owner",
        "role": "owner",
        "effective_role": "owner",
        "building_id": BUILDING_ID,
        "unit_number": unit_number,
        "owned_units": units or [],
        "is_test_data": True,
    }


def _mock_db(units_row=None, link_row=None, all_links=None):
    """Minimal db double: ``units`` for canonicalisation, ``user_units`` for links.

    ``units_row`` may be a single row (returned for every lookup) or a callable
    taking the query filter, for tests that must resolve different inputs to
    different canonical keys.
    """
    db = MagicMock()
    if callable(units_row):
        db.units.find_one = AsyncMock(side_effect=lambda q, *a, **k: units_row(q))
    else:
        db.units.find_one = AsyncMock(return_value=units_row)
    db.user_units.find_one = AsyncMock(return_value=link_row)
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=list(all_links or []))
    db.user_units.find = MagicMock(return_value=cursor)
    return db


# ── authorise_owner_unit ──────────────────────────────────────────────────────

class TestAuthoriseOwnerUnit:
    @pytest.mark.asyncio
    async def test_session_owned_units_authorise_the_request(self):
        from utils.unit_number import authorise_owner_unit

        db = _mock_db(units_row={"unit_number": "TH087"})
        resolved = await authorise_owner_unit(
            db, _owner(units=["UA013", "TH087"], unit_number="UA013"), BUILDING_ID, "TH087"
        )
        assert resolved == "TH087"
        # Session fields answered it — no link lookup was needed.
        db.user_units.find_one.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_display_variant_resolves_to_the_canonical_unit(self):
        """`87` typed/stored anywhere is the same lot as the ledger key `TH087`."""
        from utils.unit_number import authorise_owner_unit

        db = _mock_db(units_row={"unit_number": "TH087"})
        resolved = await authorise_owner_unit(
            db, _owner(units=["TH087"]), BUILDING_ID, "87"
        )
        assert resolved == "TH087"

    @pytest.mark.asyncio
    async def test_active_user_units_link_authorises_when_session_is_bare(self):
        """Postgres-authenticated sessions can arrive with owned_units unpopulated."""
        from utils.unit_number import authorise_owner_unit

        db = _mock_db(
            units_row={"unit_number": "UA045"},
            link_row={"unit_number": "UA045"},
        )
        resolved = await authorise_owner_unit(db, _owner(), BUILDING_ID, "UA045")
        assert resolved == "UA045"
        db.user_units.find_one.assert_awaited_once()
        query = db.user_units.find_one.await_args.args[0]
        assert query["is_active"] is True, "an inactive (sold/pending) link must not authorise"
        assert query["unit_number"] == "UA045", (
            "the link must be matched on the exact canonical key, never on a "
            "candidate-variant $in — see test_a_link_to_a_different_lot_never_authorises"
        )

    @pytest.mark.asyncio
    async def test_unowned_unit_is_refused_not_silently_substituted(self):
        from utils.unit_number import UnitNotOwnedError, authorise_owner_unit

        db = _mock_db(units_row={"unit_number": "TH071"}, link_row=None)
        with pytest.raises(UnitNotOwnedError):
            await authorise_owner_unit(
                db, _owner(units=["UA013"], unit_number="UA013"), BUILDING_ID, "TH071"
            )

    def test_user_units_is_tenant_scoped_so_the_link_lookup_is_building_scoped(self):
        """The cross-building guarantee comes from the collection's classification.

        This gate never filters on ``building_id`` itself — it relies on
        ``TenantScopedDatabase`` injecting one, which only happens for
        collections outside ``GLOBAL_COLLECTIONS``. Asserting that classification
        is a real check; a mocked ``find_one`` returning ``None`` would only
        assert the mock, which is what an earlier version of this test did.

        If ``user_units`` were ever moved to the global set, a user linked to
        ``UA002`` in one building would authorise against a same-numbered unit in
        another — so this assertion is the thing standing between the helper and
        a cross-tenant read.
        """
        from database import GLOBAL_COLLECTIONS

        assert "user_units" not in GLOBAL_COLLECTIONS, (
            "user_units must stay tenant-scoped: authorise_owner_unit depends on "
            "TenantScopedDatabase injecting building_id into its link lookup."
        )

    @pytest.mark.asyncio
    async def test_link_lookup_goes_through_the_scoped_wrapper(self):
        """…and the query must actually be issued via the wrapper attribute.

        Reaching past it (``db._db.user_units``, a raw Motor handle) would skip
        the injection the test above relies on.
        """
        from utils.unit_number import authorise_owner_unit

        db = _mock_db(units_row={"unit_number": "UA045"}, link_row={"unit_number": "UA045"})
        await authorise_owner_unit(db, _owner(), BUILDING_ID, "UA045")

        db.user_units.find_one.assert_awaited_once()
        # No building_id in the filter: it is injected by the wrapper, not by us.
        assert "building_id" not in db.user_units.find_one.await_args.args[0]

    @pytest.mark.asyncio
    async def test_blank_request_is_a_400_not_a_silent_default(self):
        """A blank parameter is malformed input, not "no parameter supplied".

        Falling back to the account's default unit here would be the silent
        substitution the whole gate exists to prevent.
        """
        from utils.unit_number import BlankUnitRequestError, authorise_owner_unit

        db = _mock_db()
        for blank in ("", "   ", "Unit "):
            with pytest.raises(BlankUnitRequestError):
                await authorise_owner_unit(db, _owner(units=["UA013"]), BUILDING_ID, blank)

    @pytest.mark.asyncio
    async def test_a_link_to_a_different_lot_never_authorises(self):
        """UA087 and TH087 are different lots. A link to one must not open the other.

        This is the hole flagged on PR #731. `unit_number_candidates` fans out in
        both directions by design so a lookup can FIND a row from a display value:

            unit_number_candidates("UA087") -> ['UA087', 'TH087', '87', 'U87', 'U087']

        Matching links with `unit_number IN (candidates)` therefore authorised a
        UA087 holder for TH087. The prefix rules explicitly allow overlapping
        numeric ranges across prefixes, so this is reachable for any building not
        shaped like East Gate (UA 1-70 / TH 71-87, which is the only reason it did
        not fire here).

        Expansion may be used to REACH a canonical key, never to decide two keys
        match.
        """
        from utils.unit_number import UnitNotOwnedError, authorise_owner_unit

        rules = [
            {"prefix": "UA", "min": 1, "max": 99, "pad": 3},
            {"prefix": "TH", "min": 1, "max": 99, "pad": 3},
        ]

        real_units = {"TH087", "UA087"}

        def units_lookup(query):
            """Both lots exist in this building; each resolves only to itself.

            Models a real units collection: an exact `unit_number` probe matches
            only that row, and an `$in` returns the first candidate that exists —
            deliberately ordered here to return the WRONG lot if the resolver ever
            reaches the expansion path for a value that is already a real unit.
            """
            wanted = query.get("unit_number")
            if isinstance(wanted, str):
                return {"unit_number": wanted} if wanted in real_units else None
            for candidate in (wanted or {}).get("$in", []):
                if candidate in real_units:
                    return {"unit_number": candidate}
            return None

        # The caller holds UA087 and asks for TH087.
        db = _mock_db(units_row=units_lookup, all_links=[{"unit_number": "UA087"}])
        with pytest.raises(UnitNotOwnedError):
            await authorise_owner_unit(
                db,
                _owner(units=["UA087"], unit_number="UA087"),
                BUILDING_ID,
                "TH087",
                rules=rules,
            )

    @pytest.mark.asyncio
    async def test_a_legacy_display_form_link_still_authorises_its_own_lot(self):
        """The strict comparison must not lock out links stored as e.g. "87".

        Those resolve to TH087 through the units collection, so they are the same
        lot and must still pass — the fallback branch canonicalises each link
        individually rather than expanding the request.
        """
        from utils.unit_number import authorise_owner_unit

        db = _mock_db(units_row={"unit_number": "TH087"}, all_links=[{"unit_number": "87"}])
        resolved = await authorise_owner_unit(db, _owner(), BUILDING_ID, "TH087")
        assert resolved == "TH087"

    def test_errors_do_not_derive_from_oserror(self):
        """An authorisation failure must not be catchable as an I/O error.

        ``PermissionError`` — the obvious-looking base — is an ``OSError``
        subclass, and this codebase has several ``except OSError`` handlers around
        file and subprocess work. Any of them wrapping a future adopter of this
        helper would swallow the refusal and fall through to the default unit.
        """
        from utils.unit_number import (
            BlankUnitRequestError,
            UnitNotOwnedError,
            UnitRequestError,
        )

        assert not issubclass(UnitRequestError, OSError)
        assert issubclass(UnitNotOwnedError, UnitRequestError)
        assert issubclass(BlankUnitRequestError, UnitRequestError)

    @pytest.mark.asyncio
    async def test_building_display_rules_are_passed_through_to_resolution(self):
        """Without rules, candidate expansion falls back to two hardcoded prefixes.

        `87 → TH087` passes with or without rules, because `TH` is one of those
        two fallbacks — so East Gate's own data cannot demonstrate this. A
        building using any other prefix is the case that breaks, and it breaks by
        403-ing a legitimate owner. Asserted here with a deliberately non-fallback
        prefix.
        """
        from utils.unit_number import authorise_owner_unit

        rules = [{"prefix": "APT", "min": 1, "max": 99, "pad": 3}]

        def units_lookup(query):
            """Only APT005 exists, so the exact probe on "5" must miss."""
            wanted = query.get("unit_number")
            if isinstance(wanted, str):
                return {"unit_number": wanted} if wanted == "APT005" else None
            return (
                {"unit_number": "APT005"}
                if "APT005" in (wanted or {}).get("$in", []) else None
            )

        db = _mock_db(units_row=units_lookup)
        resolved = await authorise_owner_unit(
            db, _owner(units=["APT005"]), BUILDING_ID, "5", rules=rules
        )
        assert resolved == "APT005"

        # The rules must reach the units lookup, not merely be accepted and dropped.
        # Search every call for the expansion query: the resolver now probes the
        # exact token first (a plain string), so the `$in` is no longer call zero.
        expansions = [
            call.args[0]["unit_number"]["$in"]
            for call in db.units.find_one.await_args_list
            if isinstance(call.args[0].get("unit_number"), dict)
        ]
        assert any("APT005" in candidates for candidates in expansions), (
            f"the rule-formatted candidate never reached the units query: {expansions}"
        )


# ── /owner-finance/levy-breakdown + /savings-summary ─────────────────────────

class TestOwnerFinanceUnitParameter:
    @pytest.mark.asyncio
    async def test_no_parameter_keeps_the_accounts_own_unit(self):
        """Single-unit owners must see exactly what they saw before this change."""
        from routers import owner_finance

        user = _owner(units=["UA013"], unit_number="UA013")
        with patch.object(owner_finance, "get_levy_breakdown", new=AsyncMock(return_value={"ok": True})) as gb:
            await owner_finance.levy_breakdown(
                unit_number=None, current_user=user, building_id=BUILDING_ID
            )
        gb.assert_awaited_once_with("UA013", BUILDING_ID)

    @pytest.mark.asyncio
    async def test_parameter_for_an_owned_unit_reports_on_that_unit(self):
        from routers import owner_finance

        user = _owner(units=["UA013", "TH087"], unit_number="UA013")
        db = _mock_db(units_row={"unit_number": "TH087"})
        with patch.object(owner_finance, "db", db), \
             patch.object(owner_finance, "_unit_display_rules_safe", new=AsyncMock(return_value=[])), \
             patch.object(owner_finance, "get_levy_breakdown", new=AsyncMock(return_value={"ok": True})) as gb:
            await owner_finance.levy_breakdown(
                unit_number="TH087", current_user=user, building_id=BUILDING_ID
            )
        gb.assert_awaited_once_with("TH087", BUILDING_ID)

    @pytest.mark.asyncio
    async def test_parameter_for_an_unowned_unit_is_403(self):
        from routers import owner_finance

        user = _owner(units=["UA013"], unit_number="UA013")
        db = _mock_db(units_row={"unit_number": "TH071"}, link_row=None)
        with patch.object(owner_finance, "db", db), \
             patch.object(owner_finance, "_unit_display_rules_safe", new=AsyncMock(return_value=[])), \
             patch.object(owner_finance, "get_levy_breakdown", new=AsyncMock()) as gb:
            with pytest.raises(HTTPException) as exc:
                await owner_finance.levy_breakdown(
                    unit_number="TH071", current_user=user, building_id=BUILDING_ID
                )
        assert exc.value.status_code == 403
        gb.assert_not_awaited(), "a refused request must not read another unit's ledger"

    @pytest.mark.asyncio
    async def test_blank_parameter_maps_to_400_at_the_route(self):
        """400 (you sent nonsense) and 403 (not your unit) are different answers.

        Collapsing them would tell an owner they are not linked to a unit they
        do own, for what is really a client bug.
        """
        from routers import owner_finance

        user = _owner(units=["UA013"], unit_number="UA013")
        db = _mock_db()
        with patch.object(owner_finance, "db", db), \
             patch.object(owner_finance, "_unit_display_rules_safe", new=AsyncMock(return_value=[])), \
             patch.object(owner_finance, "get_levy_breakdown", new=AsyncMock()) as gb:
            with pytest.raises(HTTPException) as exc:
                await owner_finance.levy_breakdown(
                    unit_number="   ", current_user=user, building_id=BUILDING_ID
                )
        assert exc.value.status_code == 400
        gb.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_savings_summary_honours_the_same_parameter_and_gate(self):
        from routers import owner_finance

        user = _owner(units=["UA013", "TH087"], unit_number="UA013")
        db = _mock_db(units_row={"unit_number": "TH087"})
        with patch.object(owner_finance, "db", db), \
             patch.object(owner_finance, "_unit_display_rules_safe", new=AsyncMock(return_value=[])), \
             patch.object(owner_finance, "get_savings_per_lot", new=AsyncMock(return_value={"ok": True})) as gs:
            await owner_finance.savings_summary(
                unit_number="TH087", current_user=user, building_id=BUILDING_ID
            )
        gs.assert_awaited_once_with("TH087", BUILDING_ID)

        db_unowned = _mock_db(units_row={"unit_number": "TH071"}, link_row=None)
        with patch.object(owner_finance, "db", db_unowned), \
             patch.object(owner_finance, "_unit_display_rules_safe", new=AsyncMock(return_value=[])), \
             patch.object(owner_finance, "get_savings_per_lot", new=AsyncMock()) as gs2:
            with pytest.raises(HTTPException) as exc:
                await owner_finance.savings_summary(
                    unit_number="TH071", current_user=user, building_id=BUILDING_ID
                )
        assert exc.value.status_code == 403
        gs2.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_owner_role_is_still_refused_before_any_unit_resolution(self):
        from routers import owner_finance

        tenant = _owner(units=["UA013"], unit_number="UA013")
        tenant["role"] = tenant["effective_role"] = "tenant"
        with pytest.raises(HTTPException) as exc:
            await owner_finance.levy_breakdown(
                unit_number="UA013", current_user=tenant, building_id=BUILDING_ID
            )
        assert exc.value.status_code == 403
