# @featuretrace:canonical-owner-registry — THE dollars -> cents boundary conversion.
# Layer: util
# Data flow: external dollar amount (API body, Mongo doc, CSV cell, OCR total)
#            -> dollars_to_cents / dollars_to_cents_strict -> integer cents -> ledger.
# Scope: repo-wide (building-agnostic, no I/O, no database)
# Related: backend/domain/__init__.py            (Cents — floats are BANNED past this point)
#          docs/architecture/canonical_owners.yaml  (registry entry: money-dollars-to-cents)
# Tests: tests/backend/test_money.py
"""Dollars become cents exactly once, here.

Why this module exists
----------------------
Two functions with the IDENTICAL name ``dollars_to_cents`` shipped in two
different modules. They agreed on the happy path and disagreed on every edge
case. Verified live 2026-08-28::

    dollars_to_cents(  12.34 ) -> 1234            1234
    dollars_to_cents(      0 ) -> HTTPException   0
    dollars_to_cents(     -5 ) -> HTTPException   -500
    dollars_to_cents(   None ) -> HTTPException   0
    dollars_to_cents('10.005') -> HTTPException   1001

A caller who imported the wrong one got different money for the same input, and
nothing in the type signature, the call site or the name told them which one
they had. ``'10.005'`` is the one that matters: a half-cent is either a bad
request or silently 1001 cents, depending on an import line.

Both behaviours are legitimate — they answer different questions — so this
module keeps both and makes the choice explicit at the call site instead of
implicit in an import path.

Which one to use
----------------
``dollars_to_cents_strict``  — an amount a USER just supplied. A fractional
cent, a zero, a negative or a missing value is a bad request and must be
rejected loudly, at the edge, before anything is written. Raises ``ValueError``;
the router turns that into a 422.

``dollars_to_cents``  — an amount already STORED as dollars that must be read
as cents. ``unit_levy_ledger.admin_levied`` / ``sinking_levied`` and
``annual_levies``' fund totals are the known offenders (a documented violation
of the cents-only rule, not a naming accident). Nothing can be rejected here —
the row exists and has to be read — so this rounds and tolerates ``None`` as 0.

Do not add a third variant. If you need different behaviour, you almost
certainly want one of these two with a different call site.

Rounding
--------
``dollars_to_cents`` uses ``Decimal`` with ``ROUND_HALF_UP``, not ``round()``.
Python's ``round()`` is banker's rounding — ``round(0.005 * 100)`` is 0, not 1 —
and float multiplication reaches the rounding step already wrong: ``8.115 * 100``
is ``811.4999999999999``. Money rounds half away from zero, which is what every
invoice, levy notice and bank statement in the building does.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

__all__ = ["dollars_to_cents", "dollars_to_cents_strict", "cents_to_dollars"]

_HUNDRED = Decimal("100")


def _as_decimal(value: Any) -> Decimal:
    """str() first — Decimal(0.1) is 0.1000000000000000055511151231257827."""
    return Decimal(str(value))


def dollars_to_cents(value: Any) -> int:
    """Read a stored dollar amount as integer cents. Tolerant by design.

    ``None`` and ``""`` are 0 — a missing stored amount is zero, not an error,
    because the row already exists and has to be read. Fractions of a cent round
    half away from zero. Negatives pass through: a stored dollar figure may
    legitimately be a credit or a reversal.

    Raises ``ValueError`` only if the value is not a number at all, which means
    the field holds something other than an amount and silently returning 0
    would hide it.
    """
    if value is None or value == "":
        return 0
    try:
        amount = _as_decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"not a dollar amount: {value!r}") from exc
    return int((amount * _HUNDRED).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def dollars_to_cents_strict(value: Any, *, allow_zero: bool = False,
                            allow_negative: bool = False) -> int:
    """Validate a user-supplied dollar amount and convert it. Rejects loudly.

    A fractional cent is a bad request, not something to round — the user typed
    an amount that cannot exist, and rounding it writes a number they did not
    authorise. Zero and negative are rejected unless the caller opts in, because
    the overwhelmingly common case (a payment, a levy, an invoice) is a positive
    amount and a 0 usually means a parse failure upstream.

    Raises ``ValueError``. Callers at an HTTP boundary translate that to 422 —
    this module raises no HTTPException so it stays importable from scripts,
    workers and the domain layer without dragging FastAPI in.
    """
    if value is None or value == "":
        raise ValueError("amount is required")
    try:
        amount = _as_decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("amount must be a valid decimal dollar value") from exc

    cents = amount * _HUNDRED
    if cents != cents.to_integral_value():
        raise ValueError("amount must not contain fractions of a cent")
    result = int(cents)
    if result == 0 and not allow_zero:
        raise ValueError("amount must not be zero")
    if result < 0 and not allow_negative:
        raise ValueError("amount must be greater than zero")
    return result


def cents_to_dollars(cents: int | None) -> Decimal:
    """Integer cents back to dollars, for DISPLAY and serialisation only.

    Returns ``Decimal``, never ``float`` — a float here is how cents-precision
    gets lost on the way out, and the round trip through JSON is where it stops
    being recoverable. Note the direction: several private ``_cents()`` helpers
    in this codebase actually do THIS (cents -> dollars) under a name that
    everywhere else means the opposite. That ambiguity is why this one is
    spelled out.
    """
    if cents is None:
        return Decimal("0.00")
    return (Decimal(int(cents)) / _HUNDRED).quantize(Decimal("0.01"))
