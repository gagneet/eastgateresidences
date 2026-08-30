"""Unit-number canonicalisation helpers.

# @featuretrace:finance-owner-dashboard — canonical unit resolution for owner finance lookups.
# Layer: service
# Data flow: user/account unit display values → canonical units.unit_number candidates (building-scoped).
# @featuretrace:multi-unit-ownership — authorise_owner_unit() gates the unit_number request parameter.
# Layer: service
# Data flow: owner-facing endpoint ?unit_number= → authorise_owner_unit → session owned_units OR active
#            user_units link → canonical units.unit_number, else 403 (building-scoped).
# Related: backend/routers/finance.py
#           backend/routers/owner_finance.py
#           backend/services/settings_service.py
#           backend/scripts/data_repair/repair_eastgate_unit_number_canonicalisation.py
# Collection: units
# Tests: tests/backend/test_unit_number_canonicalisation.py
#         tests/backend/test_owner_finance_unit_scope.py

Historical data has used multiple display/reference formats for the same lot
(East Gate example: user-entered ``87`` / ``U87`` / ``Unit 87`` vs the
canonical ledger key ``TH087``). Finance lookups must resolve to the canonical
``units.unit_number`` before reading ``unit_levy_ledger``, ``levy_payments``,
``strata_owners``, or derived summaries.

Display-prefix rules are per-building configuration, not code. A building's
``db.settings`` document with ``type="unit_display"`` holds::

    {
        "type": "unit_display",
        "building_id": "<bid>",
        "rules": [
            {"prefix": "UA", "min": 1, "max": 70, "pad": 3},
            {"prefix": "TH", "min": 71, "max": 87, "pad": 3}
        ]
    }

The long-term direction is numeric lot storage with display formatting applied
from these rules at the UI layer; re-keying every existing collection is
deferred until after the Mongo→Postgres cutover (see
docs/api/owner_finance_unit_resolution.md).
"""
from __future__ import annotations

import re
from typing import Any, Iterable

DEFAULT_UNIT_PAD = 3

# Fallback prefixes tried when a building has no unit_display rules configured.
# These cover the formats observed in imported data; rule-driven candidates
# are preferred and take priority when rules exist.
_GENERIC_PREFIXES = ("UA", "TH")


def normalise_unit_token(value: object) -> str:
    """Normalise a user-entered unit token without guessing the canonical prefix."""
    text = str(value or "").strip().upper()
    text = re.sub(r"^UNIT\s*", "", text)
    text = re.sub(r"\s+", "", text)
    return text


def extract_lot_int(value: object) -> int | None:
    """Return the trailing numeric lot component of a unit token, if any."""
    raw = normalise_unit_token(value)
    match = re.search(r"(\d{1,4})$", raw)
    return int(match.group(1)) if match else None


def format_unit_display(lot: int, rules: list[dict[str, Any]] | None) -> str:
    """Format a numeric lot using the building's display rules.

    Falls back to the bare number when no rule covers the lot — callers must
    treat that as "no prefix configured", not an error.
    """
    for rule in rules or []:
        try:
            lo = int(rule.get("min", 1))
            hi = int(rule.get("max", 10**9))
            if lo <= lot <= hi:
                pad = int(rule.get("pad", DEFAULT_UNIT_PAD))
                return f"{str(rule.get('prefix') or '').upper()}{lot:0{pad}d}"
        except (TypeError, ValueError):
            continue
    return str(lot)


def unit_number_candidates(
    value: object,
    rules: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Return likely unit-number variants, preserving order and uniqueness.

    The helper is intentionally generous. It should be used to find the actual
    canonical row in ``units`` first, not to blindly write new values.
    When ``rules`` are provided (per-building unit_display config) the
    rule-formatted candidate is generated first so configured buildings do not
    depend on the generic fallback prefixes.
    """
    raw = normalise_unit_token(value)
    if not raw:
        return []

    variants: list[str] = []

    def add(candidate: object) -> None:
        """Generated function header.

        Function: add
        Path: backend/utils/unit_number.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        candidate_text = normalise_unit_token(candidate)
        if candidate_text and candidate_text not in variants:
            variants.append(candidate_text)

    add(raw)
    lot = extract_lot_int(raw)
    if lot is None:
        return variants

    if rules:
        add(format_unit_display(lot, rules))
    add(str(lot))
    add(f"U{lot}")
    add(f"U{lot:02d}")
    add(f"U{lot:03d}")
    for prefix in _GENERIC_PREFIXES:
        add(f"{prefix}{lot:0{DEFAULT_UNIT_PAD}d}")

    return variants


def canonicalise_unit_from_existing(value: object, existing_unit_numbers: Iterable[str]) -> str:
    """Return the first matching canonical unit from existing rows, else normalised input.

    Callers should pass actual ``units.unit_number`` values for the building.
    This avoids hardcoding building-specific logic while still allowing
    display values to resolve to the stored canonical unit key.
    """
    existing_map = {normalise_unit_token(unit): unit for unit in existing_unit_numbers if unit}
    for candidate in unit_number_candidates(value):
        if candidate in existing_map:
            return existing_map[candidate]
    return normalise_unit_token(value)


async def resolve_canonical_unit_number(
    db: Any,
    building_id: str,
    value: object,
    rules: list[dict[str, Any]] | None = None,
) -> str:
    """Resolve a raw unit reference to the canonical ``units.unit_number`` row key.

    Looks the candidate variants up against the building's ``units`` collection
    (unit_number first, then legacy lot_number). Returns the normalised input
    unchanged when nothing matches so callers keep their existing 404 handling.

    **An exact match always wins over variant expansion.** Candidate expansion is
    generous by design and fans out across every configured prefix, so for a
    building whose rules cover overlapping numeric ranges the candidate list for
    ``UA087`` contains ``TH087`` — two genuinely distinct lots. Feeding both to a
    single ``$in`` made the answer depend on which row Mongo happened to return
    first, so a real unit could resolve to a *different* real unit. Probing the
    exact normalised token first removes that ambiguity at source: an input that
    is already a unit key in this building can only ever resolve to itself.
    Narrowing only — an exact hit was always the correct answer, so no caller that
    was previously right becomes wrong.
    """
    exact = normalise_unit_token(value)
    if exact:
        row = await db.units.find_one(
            {"building_id": building_id, "unit_number": exact},
            {"_id": 0, "unit_number": 1},
        )
        if row and row.get("unit_number"):
            return row["unit_number"]

    candidates = unit_number_candidates(value, rules)
    if not candidates:
        return normalise_unit_token(value)
    row = await db.units.find_one(
        {"building_id": building_id, "unit_number": {"$in": candidates}},
        {"_id": 0, "unit_number": 1},
    )
    if not row:
        row = await db.units.find_one(
            {"building_id": building_id, "lot_number": {"$in": candidates}},
            {"_id": 0, "unit_number": 1},
        )
    return row["unit_number"] if row and row.get("unit_number") else normalise_unit_token(value)


def user_unit_matches(current_user: dict, unit_number: str) -> bool:
    """True when the requested unit matches the user's unit context in any stored variant.

    Compares the requested unit against the session's ``unit_number`` and
    ``owned_units`` using candidate expansion in both directions, so a stored
    display value (``87``) still matches the canonical ledger key (``TH087``)
    and vice versa. Pure function — authorisation callers that need ownership
    beyond the session fields must resolve owned units separately.
    """
    requested = normalise_unit_token(unit_number)
    if not requested:
        return False
    requested_candidates = set(unit_number_candidates(requested))
    held = [current_user.get("unit_number"), *(current_user.get("owned_units") or [])]
    for value in held:
        token = normalise_unit_token(value)
        if not token:
            continue
        if token == requested or token in requested_candidates:
            return True
        if requested in set(unit_number_candidates(token)):
            return True
    return False


class UnitRequestError(Exception):
    """Base for a rejected owner-supplied ``unit_number`` request parameter.

    Deliberately derives from ``Exception``, NOT ``PermissionError`` — that is an
    ``OSError`` subclass, and this codebase has five ``except OSError`` handlers
    around file and subprocess work. An authorisation failure that can be
    swallowed by an unrelated I/O handler becomes a silent fall-through to the
    caller's default unit, which is exactly the failure this gate exists to
    prevent (cf. footgun #16: a try/except that continues turns a hard failure
    into a permanent silent one).
    """


class BlankUnitRequestError(UnitRequestError):
    """The parameter was present but held no usable unit token → HTTP 400."""


class UnitNotOwnedError(UnitRequestError):
    """The caller holds no active link to the requested unit → HTTP 403."""


async def authorise_owner_unit(
    db: Any,
    current_user: dict,
    building_id: str,
    requested: object,
    rules: list[dict[str, Any]] | None = None,
) -> str:
    """Resolve and authorise an owner-supplied ``unit_number`` request parameter.

    Owner-facing endpoints historically derived the unit purely from the session
    (``current_user["unit_number"]``), which pins a multi-unit owner to whichever
    unit their account defaults to. Letting the page pass the unit it is showing
    fixes that — but only if the caller is actually linked to it, so this is the
    one gate every such endpoint must go through.

    Authorisation is deliberately narrow: the caller's own active units, never
    "any unit in the building". An elevated role is not a bypass here — manager
    views of another owner's ledger have their own endpoints and their own
    guards.

    ``rules`` are the building's ``unit_display`` prefix rules. **Pass them.**
    Without them ``unit_number_candidates`` falls back to two hardcoded generic
    prefixes (``UA``/``TH``), so a display variant in a building using any other
    prefix would fail to resolve and 403 a legitimate owner. Callers should use
    the same ``_unit_display_rules_safe``-style lookup as the finance routes, so
    a settings outage degrades to generic expansion rather than failing the read.

    Checks, in order:
      1. ``user_unit_matches`` against the session's ``unit_number`` /
         ``owned_units`` (covers display-format variants once ``rules`` are
         supplied, e.g. ``87`` vs ``TH087``). In practice this usually only
         short-circuits the caller's *own* ``unit_number``: ``owned_units`` is a
         ``UserResponse`` field computed by ``/auth/me``, not a stored column, so
         a session built by ``get_current_user`` normally arrives without it.
      2. Therefore the DB lookup below is the primary authority for any unit
         other than the session's default, not a rare fallback — an active link
         in the MongoDB ``user_units`` collection. Postgres-
         authenticated sessions can reach here with ``owned_units`` unpopulated
         (see ``utils.auth._backfill_legacy_unit_context``). ``user_units`` is a
         tenant-scoped collection in ``TenantScopedDatabase``, so this lookup is
         building-scoped by injection; it does NOT consult Postgres
         ``core.user_units``, which is maintained separately.

    Returns the canonical ``units.unit_number``.

    Raises:
        BlankUnitRequestError: the parameter held no usable token (→ HTTP 400).
            Not treated as "no parameter supplied": silently answering about the
            account's default unit when the caller asked for something else is
            the exact substitution this gate forbids.
        UnitNotOwnedError: the caller holds no active link (→ HTTP 403).
    """
    if not normalise_unit_token(requested):
        raise BlankUnitRequestError("unit_number was supplied but blank")

    canonical = await resolve_canonical_unit_number(db, building_id, requested, rules)

    # ── Why this compares CANONICAL KEYS and never candidate variants ──────────
    # `unit_number_candidates` deliberately fans out in both directions so a lookup
    # can FIND a row from a display value. That breadth is correct for finding and
    # catastrophic for authorising: with East Gate's own rules,
    #
    #     unit_number_candidates("UA087") -> ['UA087', 'TH087', '87', 'U87', 'U087']
    #
    # so an `IN (candidates)` match would let a link to lot UA087 authorise lot
    # TH087 — two genuinely distinct lots. The prefix rules explicitly permit
    # overlapping numeric ranges across prefixes, so this is not hypothetical for a
    # building shaped differently from East Gate (where UA is 1-70 and TH is 71-87,
    # which is the only reason it does not fire here today).
    #
    # Both branches below therefore resolve the HELD unit to its canonical key and
    # require exact equality with the canonical key of the REQUESTED unit. Expansion
    # is used only to reach a canonical key, never to decide that two keys match.

    # Branch 1 — session fields, no DB. Exact canonical-key equality only.
    held_values = [current_user.get("unit_number"), *(current_user.get("owned_units") or [])]
    if any(normalise_unit_token(v) == canonical for v in held_values if v):
        return canonical

    # Branch 2 — the authoritative check, and in practice the primary one (see the
    # note above on `owned_units` rarely being populated on a get_current_user
    # session). `user_units` is tenant-scoped in TenantScopedDatabase, so
    # building_id is injected here rather than filtered explicitly.
    #
    # A Postgres-path session may carry a different id than the Mongo user row
    # (footgun #24), so both identifiers the session can hold are tried.
    candidate_ids = [
        uid for uid in (current_user.get("id"), current_user.get("legacy_user_id")) if uid
    ]
    if candidate_ids:
        # Exact key first: covers every correctly-stored link with one query and no
        # expansion at all.
        link = await db.user_units.find_one(
            {"user_id": {"$in": candidate_ids}, "unit_number": canonical, "is_active": True},
            {"_id": 0, "unit_number": 1},
        )
        if link:
            return canonical

        # Fallback for links stored in a legacy display form (e.g. "87" for TH087).
        # Each link is resolved to ITS OWN canonical key and compared exactly — the
        # expansion happens per stored value, so a UA087 link resolves to UA087 and
        # can never satisfy a request for TH087. Bounded by the caller's own link
        # count (typically one to three), not by the building's unit count.
        cursor = db.user_units.find(
            {"user_id": {"$in": candidate_ids}, "is_active": True},
            {"_id": 0, "unit_number": 1},
        )
        for row in await cursor.to_list(50):
            held = row.get("unit_number")
            if not held or normalise_unit_token(held) == canonical:
                continue  # already covered by the exact query above
            if await resolve_canonical_unit_number(db, building_id, held, rules) == canonical:
                return canonical

    raise UnitNotOwnedError(f"You are not linked to unit {canonical}")
