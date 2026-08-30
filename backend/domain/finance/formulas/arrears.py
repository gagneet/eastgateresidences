"""
backend/domain/finance/formulas/arrears.py — canonical arrears/credit formula.

# @featuretrace:financial-metrics-canonical — levy.unit_arrears_and_credit.v1
# Layer: domain
# Data flow: backend/utils/finance_helpers.py get_arrears_metrics() /
#            backend/routers/finance.py get_levy_kpi()/get_arrears_board() → this module (pure) →
#            response dict (building-scoped).
# Related: backend/domain/finance/formulas/collection.py
#          docs/architecture/arrears_calculation_spec_2026_08_03.md (superseded worked scenarios)

A unit's arrears is a per-unit obligation, never netted against any other
unit's credit or arrears. ``unit_levy_ledger.net_balance`` already represents
"life-to-date levied minus life-to-date paid" for a unit — because levy
periods are charged incrementally as they become due, this already rolls
prior-period debt into the current period automatically (there is no
separate "prior year" bucket to track once past its own grace deadline). The
only adjustment needed on top of ``net_balance`` is excluding the portion of
the *currently-charged* period that has not yet passed its own grace
deadline — that is ``in_grace_portion_cents`` below.

2026-08-03 incident this formula is designed to avoid repeating: an earlier
version of get_arrears_board() computed arrears as
``opening_debt + periods_past_grace * period_levy`` — reconstructing the
obligation from an independently-derived period levy on top of an opening
balance — and inflated UA042 from a true $963.31 to $2,768. The same defect
independently existed in get_arrears_metrics()'s "periods overdue" branch,
producing 31 "units in arrears" for East Gate against a true count of 14.
This formula does not reconstruct anything: it trusts the ledger's own
net_balance and only ever subtracts (never adds) the still-in-grace portion.

Amounts are integer cents per the backend/domain/ no-float rule.
"""
from __future__ import annotations

from domain import Cents
from domain.finance.money import clamp_non_negative


def unit_arrears_and_credit(
    *,
    net_balance_cents: Cents,
    in_grace_portion_cents: Cents = 0,
) -> tuple[Cents, Cents]:
    """Split a unit's net_balance into (arrears_cents, credit_cents).

    net_balance_cents <= 0 means the unit has paid at least as much as it has
    been levied to date — an advance/credit position, never arrears. The
    credit is returned as a positive magnitude for display; it must not be
    subtracted from any other unit's arrears.

    net_balance_cents > 0 means the unit owes money. in_grace_portion_cents
    (the still-not-yet-overdue slice of the currently-charged period, capped
    at net_balance_cents by the caller) is subtracted before the result is
    treated as arrears — a unit owing money for a period that isn't overdue
    yet is "outstanding", not "in arrears".
    """
    if net_balance_cents <= 0:
        return 0, -net_balance_cents
    return clamp_non_negative(net_balance_cents - in_grace_portion_cents), 0
