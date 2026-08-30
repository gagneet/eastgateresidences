"""
backend/domain/finance/money.py — Cents-only money and percentage helpers.

# @featuretrace:financial-metrics-canonical — money/percentage primitives shared by all formulas.
# Layer: domain
# Data flow: domain/finance/formulas/*.py → cents_to_percentage()/clamp_non_negative() → Decimal/Cents (global).
# Related: backend/domain/__init__.py (Cents type)
#          backend/domain/finance/formulas/collection.py
#          backend/domain/finance/formulas/arrears.py

Every monetary value entering this module is already Cents (integer). Converting
a raw Mongo dollar-value (stored as a float) into Cents happens ONLY in
backend/services/finance_metrics/mongo_adapter.py — never here. See PR #500 for
two real bugs caused by skipping that conversion (true_cost_service.py,
capital_shock_service.py both read dollar fields straight into a *_cents field
with no conversion, under-counting by ~100x).

Percentages are returned as Decimal, quantized to 2 decimal places with
ROUND_HALF_UP — this matches the rounding behaviour of the existing
`round(x, 2)` float calls in server.py/finance.py exactly for every value
checked against live East Gate (13195) data (see
tests/backend/fixtures/east_gate_2026_07_12_snapshot.json).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from domain import Cents

_TWO_PLACES = Decimal(1).scaleb(-2)  # 0.01, expressed without a decimal-point literal


def cents_to_percentage(numerator_cents: Cents, denominator_cents: Cents, *, digits: int = 2) -> Decimal:
    """numerator/denominator * 100, clamped to [0, 100], rounded to `digits` decimal places (default 2dp).

    Returns Decimal(0) when denominator_cents <= 0 (matches the existing
    `if total_obligations > 0 else 0` guard in server.py verbatim). `digits`
    defaults to 2dp to preserve every existing caller's behaviour exactly —
    pass an explicit value only when a specific site's display precision
    differs (e.g. finance.py's fund_health, historically 1dp).
    """
    if denominator_cents <= 0:
        return Decimal(0)
    raw = Decimal(numerator_cents) / Decimal(denominator_cents) * 100
    clamped = max(Decimal(0), min(Decimal(100), raw))
    quant = Decimal(1).scaleb(-digits)
    return clamped.quantize(quant, rounding=ROUND_HALF_UP)


def clamp_non_negative(value_cents: Cents) -> Cents:
    """max(0, value) for a Cents value — arrears/outstanding amounts are never negative."""
    return max(0, value_cents)


def raw_percentage(
    numerator_cents: Cents,
    denominator_cents: Cents,
    *,
    digits: int = 2,
    zero_denominator_value: Decimal | None = Decimal(0),
) -> Decimal | None:
    """numerator/denominator * 100, rounded to `digits` decimal places. NOT clamped.

    Unlike cents_to_percentage(), this does not clamp to [0, 100] — some existing
    call sites (quarterly collection-rate displays) intentionally show >100% when a
    building has overpaid/prepaid, and clamping would silently hide that. Use
    cents_to_percentage() instead when the [0, 100] clamp is the desired behaviour
    (see domain/finance/formulas/collection.py's annual collection-rate formulas).

    Returns `zero_denominator_value` when denominator_cents <= 0 — pass Decimal(0)
    or None to match whichever a given caller previously returned.
    """
    if denominator_cents is None or denominator_cents <= 0:
        return zero_denominator_value
    raw = Decimal(numerator_cents) / Decimal(denominator_cents) * 100
    quant = Decimal(1).scaleb(-digits)
    return raw.quantize(quant, rounding=ROUND_HALF_UP)
