# @featuretrace:security-ip-logging — Field-scoped search with exclusions for the audit log.
# Layer: service
# Data flow: search string -> parse_audit_query() -> Mongo filter -> login_audit_logs
#            -> Security & IP Logs page (global).
# Related: backend/routers/security.py
#          frontend/src/pages/dashboard/admin/SecurityIPLogsPage.jsx
# Tests: tests/backend/test_audit_search.py

"""A small, explicit query language for the security log.

## Why not just a free-text box

The Security & IP Logs page had one filter: a substring match on email. The
first thing anyone actually wants to do with a security log is the opposite —
*remove* the noise. "Show me everything that is not the monitoring probe" is the
question that makes the rest of the page readable, and there was no way to ask
it.

## The grammar

Whitespace-separated terms, combined with AND:

    anthony                      bare text: email OR IP contains it
    ip:118.210.60.180            field equals (case-insensitive)
    ip!=192.0.2.1                field does NOT equal
    -ip:192.0.2.1                same thing, shorthand
    status:failed                only failed attempts
    device:api                   only script/API clients
    country:AU city:Canberra     combine freely
    email~=eastgate              field CONTAINS (substring)

``:`` and ``=`` are interchangeable. Quoting works for values with spaces:
``city:"New South Wales"``.

## Design choices worth stating

- **Unknown field names are not silently ignored.** A typo like ``adress:x``
  would otherwise return everything and look like "no results match", which is
  the worst possible failure for a security tool. Unknown fields are reported
  back to the caller and rendered as a warning.
- **Values are matched exactly by default**, with ``~=`` for substring. The
  opposite default would make ``ip:10.0.0.1`` also match ``10.0.0.10``, which is
  wrong in a security context.
- **Everything is escaped before it reaches Mongo.** Search text is user input
  arriving at a database query; ``re.escape`` on every value means a term
  containing ``.*`` filters rather than matching everything.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

#: Field alias -> the document path it filters. Aliases exist because operators
#: type what they see on screen ("ip", "name", "browser"), not the storage path.
FIELD_MAP: dict[str, str] = {
    "email": "email",
    "user": "email",
    "ip": "ip_address",
    "publicip": "public_ip",
    "public_ip": "public_ip",
    "localip": "local_ip",
    "local_ip": "local_ip",
    "name": "user_full_name",
    "status": "status",
    "reason": "failure_reason",
    "country": "geo.country_code",
    "countryname": "geo.country_name",
    "city": "geo.city",
    "isp": "geo.isp",
    "device": "device_info.device_type",
    "browser": "device_info.browser",
    "os": "device_info.os",
    "risk": "risk_score",
    "hosting": "is_hosting_provider",
    "language": "signals.primary_language",
    "origin": "signals.origin",
}

#: Fields a bare word searches, in order.
FREE_TEXT_FIELDS = ("email", "ip_address", "public_ip", "local_ip", "user_full_name")

#: Rendered by the frontend as the help panel, so the UI and the parser can
#: never drift apart — there is one source for what the syntax supports.
SEARCH_HELP: dict[str, Any] = {
    "summary": "Terms are combined with AND. Prefix a term with - or use != to exclude.",
    "examples": [
        {"query": "anthony", "means": "email, IP or name contains \"anthony\""},
        {"query": "ip!=192.0.2.1", "means": "everything EXCEPT that IP"},
        {"query": "-device:api", "means": "hide script and monitoring logins"},
        {"query": "status:failed", "means": "failed attempts only"},
        {"query": "country:AU -city:Canberra", "means": "Australia, but not Canberra"},
        {"query": "email~=eastgate", "means": "email contains \"eastgate\""},
        {"query": "hosting:true", "means": "logins from datacentre/VPN networks"},
        {"query": "risk:>=50", "means": "risk score 50 or above"},
    ],
    "operators": [
        {"op": ":  or  =", "means": "equals (case-insensitive)"},
        {"op": "!=", "means": "does not equal"},
        {"op": "-field:value", "means": "does not equal (shorthand)"},
        {"op": "~=", "means": "contains"},
        {"op": ">=  >  <=  <", "means": "numeric comparison (risk only)"},
    ],
    "fields": sorted(set(FIELD_MAP)),
}

_TERM = re.compile(
    r"^(?P<neg>-)?(?P<field>[A-Za-z_]+)\s*(?P<op>!=|~=|>=|<=|>|<|:|=)\s*(?P<value>.*)$"
)
_NUMERIC_FIELDS = {"risk_score"}
_BOOLEAN_VALUES = {"true": True, "yes": True, "1": True, "false": False, "no": False, "0": False}

#: Fields stored as real booleans. A regex match against these never matches, so
#: they need the dedicated branch in parse_audit_query.
_BOOLEAN_FIELDS = {"is_hosting_provider"}


#: Fields whose stored values are normalised, mapped to the normaliser.
#:
#: These get a plain ``$eq`` instead of a case-insensitive ``$regex``, and the
#: difference is not cosmetic: ``$options: "i"`` makes a regex **unindexable**,
#: so a selective filter has to residual-filter the whole sort index. Measured
#: on 2,463 rows a non-matching filter examined all 2,463; at a million rows the
#: same query walks a million index entries.
#:
#: Only fields with a known, enforced value shape belong here — a normaliser
#: applied to free-form data would silently stop matching real rows.
_NORMALISED_FIELDS: dict[str, Any] = {
    "status": str.lower,                    # "success" / "failed"; also BreachStatus
    "device_info.device_type": str.lower,   # desktop/mobile/tablet/api/unknown
    "geo.country_code": str.upper,          # ISO-3166 alpha-2
    # by_law_breach_reports. Stored as a lowercase BreachSeverity enum, so equality is
    # safe and indexable. This map is shared across every caller of the grammar rather
    # than per-vocabulary; that is harmless here because a field name only appears in
    # one collection's field_map, and the entry is inert for callers that never map it.
    "severity": str.lower,
}


def _exact(value: str, field: str | None = None) -> dict:
    """Exact match — indexable where the stored value shape is known.

    For a normalised field this is a plain equality, which an index can serve.
    Everything else keeps the anchored case-insensitive regex: an IP, an email
    or a city name has no guaranteed casing, and matching only the exact case
    would quietly miss rows.

    Anchoring matters either way — unanchored, ``ip:10.0.0.1`` also matches
    ``10.0.0.10``, which is wrong in a security context.
    """
    normaliser = _NORMALISED_FIELDS.get(field or "")
    if normaliser is not None:
        return {"$eq": normaliser(value)}
    return {"$regex": f"^{re.escape(value)}$", "$options": "i"}


def _contains(value: str) -> dict:
    """Generated function header.

    Function: _contains
    Path: backend/utils/audit_search.py
    """
    return {"$regex": re.escape(value), "$options": "i"}


def parse_audit_query(
    search: str | None,
    *,
    field_map: dict[str, str] | None = None,
    free_text_fields: "list[str] | tuple[str, ...] | None" = None,
    numeric_fields: "set[str] | frozenset[str] | None" = None,
    boolean_fields: "set[str] | frozenset[str] | None" = None,
) -> tuple[dict, list[str]]:
    """Turn a search string into a Mongo filter.

    Returns ``(filter, unknown_fields)``. The caller merges the filter into its
    own base query and surfaces ``unknown_fields`` to the user — a mistyped field
    silently matching everything is the failure mode this avoids.

    The four schema arguments default to the audit-log vocabulary, so existing
    callers are unchanged. They exist so a second collection can reuse this GRAMMAR
    without copying it: ``docs/architecture/ui_table_and_search_conventions.md``
    requires the help text to come from the parser, and two parsers would let the
    documented syntax drift from what each one actually accepts. A caller supplying
    a field_map must supply its own SEARCH_HELP alongside it.
    """
    field_map = FIELD_MAP if field_map is None else field_map
    free_text = FREE_TEXT_FIELDS if free_text_fields is None else free_text_fields
    numeric = _NUMERIC_FIELDS if numeric_fields is None else numeric_fields
    boolean = _BOOLEAN_FIELDS if boolean_fields is None else boolean_fields
    if not search or not search.strip():
        return {}, []

    try:
        terms = shlex.split(search)
    except ValueError:
        # Unbalanced quote — fall back to whitespace splitting rather than
        # refusing to search at all.
        terms = search.split()

    and_clauses: list[dict] = []
    unknown: list[str] = []

    for term in terms:
        if not term:
            continue
        match = _TERM.match(term)

        if not match:
            # Bare word: match across the identity-ish fields.
            and_clauses.append({"$or": [{f: _contains(term)} for f in free_text]})
            continue

        alias = match.group("field").lower()
        field = field_map.get(alias)
        if field is None:
            unknown.append(match.group("field"))
            continue

        op = match.group("op")
        value = match.group("value").strip()
        negated = bool(match.group("neg")) or op == "!="
        if not value:
            continue

        if field in numeric:
            # Allow "risk:>=50" as well as "risk>=50".
            inner = re.match(r"^(>=|<=|>|<)?\s*(-?\d+)$", value)
            if inner:
                comparator, number = inner.group(1), int(inner.group(2))
                mongo_op = {">=": "$gte", "<=": "$lte", ">": "$gt", "<": "$lt"}.get(
                    comparator or ("$ne" if negated else "$eq")
                )
                if comparator:
                    and_clauses.append({field: {mongo_op: number}})
                else:
                    and_clauses.append({field: {"$ne" if negated else "$eq": number}})
                continue

        if field in boolean:
            # Written as an explicit field set rather than
            # `field == "..." or value in ... and alias == "..."`, which mixed
            # `or`/`and` without parentheses and read as though a non-boolean
            # value on a boolean field would do something sensible. It did not —
            # it fell through to a regex match against a stored boolean, which
            # silently matches nothing. An unusable value is now a rejected term
            # rather than a filter that quietly returns zero rows.
            flag = _BOOLEAN_VALUES.get(value.lower())
            if flag is None:
                unknown.append(f"{alias}={value} (expected true/false)")
                continue
            and_clauses.append({field: {"$ne": flag} if negated else flag})
            continue

        matcher = _contains(value) if op == "~=" else _exact(value, field)
        if negated:
            # $not cannot take a $regex with $options in older servers, so the
            # negation is expressed as "does not match" via $nor, which is
            # supported everywhere and reads the same.
            and_clauses.append({"$nor": [{field: matcher}]})
        else:
            and_clauses.append({field: matcher})

    if not and_clauses:
        return {}, unknown
    if len(and_clauses) == 1:
        return and_clauses[0], unknown
    return {"$and": and_clauses}, unknown

# ---------------------------------------------------------------------------
# @featuretrace:by-law-breach-register — Search vocabulary for the dispute register.
# Layer: service
# Data flow: ByLawBreachPage.tsx search box -> GET /by-law-breach/reports?search=
#            -> parse_audit_query(field_map=BREACH_FIELD_MAP) -> by_law_breach_reports
#            (building-scoped); GET /by-law-breach/search-help serves BREACH_SEARCH_HELP
#            to the `?` panel.
# Related: backend/routers/by_law_breach.py
#          frontend/src/pages/dashboard/ByLawBreachPage.tsx
#          docs/architecture/ui_table_and_search_conventions.md
#
# LESSON (2026-08-27): a field listed in FREE_TEXT_FIELDS must ALSO appear in the field_map.
# `description` was free-text but unmapped, so a bare word searched it while
# `description~=parking` -- an example the help panel advertised -- came back "unknown
# field". Caught only because a test asserts every documented example parses. That test is
# the mechanism that keeps help and parser from drifting; keep it passing.
#
# By-law breach / dispute register vocabulary
#
# A second caller of the SAME grammar, which is exactly what the field_map/
# free_text_fields parameters exist for. Defining a second parser here instead would
# let the documented syntax drift from what each one accepts — the failure the
# docstring on parse_audit_query warns about. Only the vocabulary differs.
# ---------------------------------------------------------------------------

#: Search alias -> stored field, for `by_law_breach_reports`.
BREACH_FIELD_MAP: dict[str, str] = {
    "unit": "alleged_unit",
    "alleged_unit": "alleged_unit",
    "reporter": "reporter_unit",
    "reporter_unit": "reporter_unit",
    "status": "status",
    "severity": "severity",
    "section": "by_law_section",
    "bylaw": "by_law_section",
    # Free-text fields must ALSO be mapped, or `description~=parking` reports
    # "unknown field" while a bare word searches that very field. Caught by
    # test_every_documented_example_actually_parses.
    "description": "description",
    "notes": "notes",
    "outcome": "resolution_outcome",
    "tribunal": "escalation_target",
    "repeat": "is_repeat_offence",
}

#: A bare word searches these, in order.
BREACH_FREE_TEXT_FIELDS = ("alleged_unit", "description", "by_law_section", "reporter_unit")

BREACH_NUMERIC_FIELDS: frozenset[str] = frozenset()
BREACH_BOOLEAN_FIELDS = frozenset({"is_repeat_offence"})

#: Rendered by the UI as the `?` panel, from the parser, for the same reason as above.
BREACH_SEARCH_HELP: dict[str, Any] = {
    "summary": "Terms are combined with AND. Prefix a term with - or use != to exclude.",
    "examples": [
        {"query": "TH074", "means": "unit, description or by-law contains \"TH074\""},
        {"query": "status:escalated", "means": "escalated matters only"},
        {"query": "-status:resolved", "means": "hide everything already resolved"},
        {"query": "severity:major unit:UA042", "means": "major breaches against UA042"},
        {"query": "tribunal:ACAT", "means": "matters referred to ACAT"},
        {"query": "repeat:true", "means": "repeat offences only"},
        {"query": "description~=parking", "means": "description mentions parking"},
    ],
    "operators": [
        {"op": ":  or  =", "means": "equals (case-insensitive)"},
        {"op": "!=", "means": "does not equal"},
        {"op": "-field:value", "means": "does not equal (shorthand)"},
        {"op": "~=", "means": "contains"},
    ],
    "fields": sorted(set(BREACH_FIELD_MAP)),
}
