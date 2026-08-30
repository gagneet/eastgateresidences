# @featuretrace:community-hub — Building Health Score service: computes grade and score from aggregated building data.
# Layer: service
# Data flow: community_dashboard.py / analytics_worker.py → compute_building_health_score(data)
#            → {score, grade, status, components, unavailable_components, coverage}.
# Related: backend/routers/community_dashboard.py
#           backend/workers/analytics_worker.py
#           backend/services/morning_card_service.py
#           frontend/src/pages/dashboard/BuildingHealthPage.jsx
# Scope: (building-scoped)
# Tests: tests/backend/test_building_health_missing_data.py
#         tests/backend/test_community_os_unit.py

"""Score a building's health — and refuse to score one we cannot measure.

## The bug this module was rewritten to fix (2026-08-24)

A building with **no data at all** scored **75/100, Grade B**. Not from a stale
snapshot and not from a hardcoded constant: 75 is genuinely what the old formula
returned when every input was empty, because absence was scored as excellence.

- No compliance items tracked → ``max(0, 1 - 0 * 0.2)`` → compliance **100**
- No disputes recorded → ``max(0, 1 - 0)`` → dispute **100**
- No work orders → ``overdue_ratio`` 0 → half of maintenance scored **perfect**

Weighted together those three alone contribute 0.6 of the composite at full
marks. The building did not need to be healthy; it needed to be *empty*.

This is the same rule the finance code already carries in CLAUDE.md — "missing
data is never displayed as $0.00; zero and missing are distinct states" — and it
had simply never been applied to the health score.

## The contract

Every input may be ``None``, meaning **not measurable**, which is different from
``0``, meaning **measured and none**. A component whose inputs are unavailable is
**excluded** from the composite rather than scored full marks, and the remaining
weights are renormalised so the score still reads out of 100.

If too little is measurable — ``MIN_COVERAGE`` of the total weight — there is no
score at all. ``score`` and ``grade`` come back ``None`` with
``status="insufficient_data"``. Callers must render that as "not enough data",
never as a number.

A denominator of zero makes a component unavailable even when the numerator is a
real ``0``: "0 of 0 compliance items overdue" measures nothing. That is why
``compliance_items_total`` and ``work_orders_total`` exist as separate inputs.

## Backwards compatibility

A caller that supplies every input as a number gets the previous arithmetic
unchanged, so existing scores do not move. What changed is that omitting an input
now yields "unavailable" instead of silently defaulting to the flattering value.
"""

from __future__ import annotations

from math import isinf, isnan
from typing import Any, Mapping

#: Component weights. Must sum to 1.0.
COMPONENT_WEIGHTS: Mapping[str, float] = {
    "financial": 0.30,
    "maintenance": 0.25,
    "compliance": 0.25,
    "engagement": 0.10,
    "dispute": 0.10,
}

#: Minimum share of total weight that must be measurable before a score is
#: published. Below this the number would be dominated by whichever components
#: happened to have data, so no number is better than a misleading one.
MIN_COVERAGE = 0.5

#: Returned in place of a score when there is not enough data to publish one.
STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"


def _num(value: Any) -> float | None:
    """Coerce to float, or None when the value is not a usable measurement.

    NaN and infinity are rejected explicitly rather than left to propagate. They
    were already neutralised, but only by accident: ``max(0.0, nan)`` happens to
    return 0.0 because the ``nan > 0.0`` comparison is False. That is incidental
    Python semantics, not a decision, and an expression rearranged later could
    just as easily carry the NaN through to ``round()``, which raises. A
    corrupted stored value should make a component unavailable — the same
    treatment as a missing one.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool is an int subclass; never a measurement here
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if isnan(result) or isinf(result):
        return None
    return result


def _financial(data: Mapping[str, Any]) -> float | None:
    """Sinking-fund adequacy and arrears rate.

    Unavailable when there are no lots (nothing to be in arrears) or when the
    capital-works forecast is missing — without the forecast, "adequacy" has no
    denominator and the old code substituted 1, which read as "no reserve".
    """
    total_lots = _num(data.get("total_lots"))
    if total_lots is None or total_lots <= 0:
        return None

    sinking_balance = _num(data.get("sinking_fund_balance"))
    forecast = _num(data.get("capital_works_10yr_forecast"))
    arrears_lots = _num(data.get("arrears_lots"))

    parts: list[tuple[float, float]] = []  # (value, weight)
    if sinking_balance is not None and forecast is not None and forecast > 0:
        parts.append((min(1.0, max(0.0, sinking_balance / forecast)), 0.6))
    if arrears_lots is not None:
        arrears_rate = arrears_lots / total_lots
        parts.append((max(0.0, 1 - arrears_rate * 5), 0.4))

    if not parts:
        return None
    weight = sum(w for _, w in parts)
    return sum(v * w for v, w in parts) / weight


def _maintenance(data: Mapping[str, Any]) -> float | None:
    """Overdue ratio and average age of open work orders.

    Unavailable when the building tracks no work orders at all. Previously a
    building with zero work orders scored a perfect overdue ratio, which is the
    difference between "nothing is overdue" and "nothing is recorded".
    """
    work_orders_total = _num(data.get("work_orders_total"))
    open_wo = _num(data.get("open_work_orders"))
    overdue_wo = _num(data.get("overdue_work_orders"))
    avg_age_days = _num(data.get("avg_work_order_age_days"))

    # If the caller did not say how many work orders exist, fall back to the open
    # count — a caller that knows of open work orders demonstrably tracks them.
    known_total = work_orders_total if work_orders_total is not None else open_wo
    if known_total is None or known_total <= 0:
        return None

    parts: list[tuple[float, float]] = []
    if open_wo is not None and open_wo > 0 and overdue_wo is not None:
        parts.append((max(0.0, 1 - overdue_wo / open_wo), 0.5))
    elif open_wo is not None and open_wo == 0:
        # Genuinely measured: work orders exist historically, none are open.
        parts.append((1.0, 0.5))
    if avg_age_days is not None:
        parts.append((max(0.0, 1 - avg_age_days / 90), 0.5))

    if not parts:
        return None
    weight = sum(w for _, w in parts)
    return sum(v * w for v, w in parts) / weight


def _compliance(data: Mapping[str, Any]) -> float | None:
    """Overdue compliance items.

    Unavailable when no compliance items are tracked. "0 of 0 overdue" is not a
    clean bill of health — it is an empty register.
    """
    overdue = _num(data.get("compliance_items_overdue"))
    if overdue is None:
        return None

    total = _num(data.get("compliance_items_total"))
    if total is not None and total <= 0:
        return None
    if total is None and overdue == 0:
        # No register size given and nothing overdue: indistinguishable from an
        # empty register, so it must not score full marks.
        return None

    return max(0.0, 1 - overdue * 0.2)


def _engagement(data: Mapping[str, Any]) -> float | None:
    """Voting participation and volunteer activity.

    ``volunteer_events_ytd`` alone cannot carry this axis. A building that has
    never used volunteer events reports 0 completed, which scored 0/100 — a full
    red "Engagement" bar asserting a disengaged community purely from an empty
    collection. That is the same absence-is-a-measurement bug that made an empty
    building score 75 overall, just pointing the other way.

    ``volunteer_events_total`` distinguishes the two: zero events recorded means
    the signal is unavailable, while events recorded but none completed is a
    genuine zero and still scores as one.
    """
    vote_rate = _num(data.get("vote_participation_rate"))
    volunteer_events = _num(data.get("volunteer_events_ytd"))
    volunteer_total = _num(data.get("volunteer_events_total"))

    # Absent key -> preserve the historical behaviour of trusting the YTD count;
    # an explicit 0 total -> nothing is tracked, so contribute nothing.
    if volunteer_total is not None and volunteer_total <= 0:
        volunteer_events = None

    parts: list[tuple[float, float]] = []
    if vote_rate is not None:
        parts.append((min(1.0, max(0.0, vote_rate)), 0.7))
    if volunteer_events is not None:
        parts.append((min(1.0, max(0.0, volunteer_events / 10)), 0.3))

    if not parts:
        return None
    weight = sum(w for _, w in parts)
    return sum(v * w for v, w in parts) / weight


# @featuretrace:by-law-breach-register — Dispute axis of the building health score.
# Layer: service
# Data flow: community_dashboard._build_health_data (counting by_law_breach_reports)
#            -> data["open_disputes"] -> _dispute() -> health score (building-scoped).
# Related: backend/routers/community_dashboard.py
#          backend/models/by_law_breach.py  (BreachStatus.UNRESOLVED)
#
# LESSON (2026-08-27): `open_disputes is None` means "this building has never recorded a
# breach", which is NOT the same as "this building has no disputes". Returning a score for
# an empty register would award 10% of the health score on no evidence at all. Only a
# register with history can report a meaningful zero -- so None here is deliberate, and the
# axis drops out and its weight redistributes rather than being scored as perfect.
def _dispute(data: Mapping[str, Any]) -> float | None:
    """Open disputes per lot.

    Unavailable when disputes are not tracked, or when there are no lots to
    normalise against.
    """
    open_disputes = _num(data.get("open_disputes"))
    total_lots = _num(data.get("total_lots"))
    if open_disputes is None or total_lots is None or total_lots <= 0:
        return None
    return max(0.0, 1 - (open_disputes / total_lots) * 10)


_COMPONENT_FUNCS = {
    "financial": _financial,
    "maintenance": _maintenance,
    "compliance": _compliance,
    "engagement": _engagement,
    "dispute": _dispute,
}


def grade_for(score: float) -> str:
    """Map a 0-100 score to its letter grade. Single source for the thresholds."""
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def compute_building_health_score(data: dict | None) -> dict:
    """Return the building health score, or an explicit "not enough data" result.

    Never invents a number. A caller receiving ``score is None`` must render the
    ``status`` rather than substituting a default — a fabricated 75 out of an
    empty building is exactly the bug this function was rewritten to stop.
    """
    data = data or {}

    values: dict[str, float | None] = {
        name: func(data) for name, func in _COMPONENT_FUNCS.items()
    }
    available = {name: value for name, value in values.items() if value is not None}
    unavailable = sorted(name for name, value in values.items() if value is None)

    coverage = sum(COMPONENT_WEIGHTS[name] for name in available)
    components = {
        name: (round(value * 100) if value is not None else None)
        for name, value in values.items()
    }

    if coverage < MIN_COVERAGE:
        return {
            "score": None,
            "grade": None,
            "status": STATUS_INSUFFICIENT,
            "components": components,
            "unavailable_components": unavailable,
            "coverage": round(coverage, 3),
        }

    # Renormalise over what we could actually measure, so the score still reads
    # out of 100 rather than being silently capped by the missing weight.
    composite = sum(COMPONENT_WEIGHTS[name] * value for name, value in available.items()) / coverage
    composite = max(0.0, min(1.0, composite))
    score = round(composite * 100)

    return {
        "score": score,
        "grade": grade_for(score),
        "status": STATUS_OK,
        "components": components,
        "unavailable_components": unavailable,
        "coverage": round(coverage, 3),
    }
