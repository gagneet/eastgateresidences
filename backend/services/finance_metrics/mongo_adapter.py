"""
backend/services/finance_metrics/mongo_adapter.py — dollars<->cents boundary conversion.

# @featuretrace:financial-metrics-canonical — dollars->cents boundary conversion for canonical KPIs.
# Layer: service
# Data flow: facade.py → dollars_to_cents(mongo dollar float) → Cents (global).
# Related: backend/domain/finance/money.py
#          backend/services/finance_metrics/facade.py

Deliberately outside backend/domain/ (which bans float — see
backend/domain/__init__.py) because unit_levy_ledger/annual_levies store
monetary amounts as dollar floats (confirmed against live East Gate seed
data — see services/true_cost_service.py's _get_unit_levies docstring for the
same finding, fixed in PR #500). This is the ONE place that conversion should
happen for canonical metrics — domain/finance/formulas never see a float.
"""
from __future__ import annotations

from domain import Cents


def dollars_to_cents(value: float | int | None) -> Cents:
    """Convert a Mongo-stored dollar amount to integer cents.

    Matches the rounding convention already established in PR #500's fix to
    services/true_cost_service.py::_get_unit_levies (round(x * 100), not int()
    truncation — truncation silently drops the last cent on many real values).
    """
    if value is None:
        return 0
    return round(float(value) * 100)
