# @featuretrace:act_statutory_interest — Per-unit arrears interest + late-fee PENALTY computation:
#   used when no per-unit interest transactions exist (compute from overdue periods + policy).
# Layer: service
# Data flow: /levy-status period_status + get_effective_interest_rate + late-fee policy
#            -> compute_unit_interest_and_penalty() -> computed per-unit interest/penalty (building-scoped).
# Related: backend/services/arrears_interest_service.py (percentage-interest formula + rate resolver),
#          backend/routers/finance.py (levy-status), docs/finances/ (East Gate AGM: nil interest +
#          $55/14-day late fee, 2022 Motion 5).
# Collection: settings (late_fee_policy typed doc)
"""Compute per-unit arrears interest and late-payment PENALTY from overdue periods + building policy.

Two distinct owner charges, per the real AGM model (East Gate 2022 Motion 5 — but building-agnostic):

1. **Percentage interest** on overdue balances — the statutory/config rate resolved by
   ``arrears_interest_service.get_effective_interest_rate`` and applied via its
   ``compute_accrued_interest`` formula (principal × rate/365 × days_overdue). A building that
   adopted a NIL rate (East Gate from 2022) correctly yields ~$0 here.

2. **Flat late-fee penalty** — a fixed amount per fixed period a levy stays overdue (East Gate:
   $55.00 GST-inclusive per 14 days). Configured per building via ``get_late_fee_policy``; a
   building with no policy yields $0.

Both are PURE computations over a list of overdue periods; the caller supplies the periods (from
the levy-status timeline) and the resolved rate/policy. This service never persists anything — it
produces a computed estimate for display when no actual interest/penalty transactions exist.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Optional

# NOTE: compute_accrued_interest is imported lazily inside compute_percentage_interest (it pulls in
# domain.jurisdictional_rules → pydantic). This keeps compute_late_fee_penalty /
# build_overdue_periods_from_status importable and unit-testable without the heavier dependency
# graph, mirroring strata_web_balance_inference_service._interest_since. We deliberately reuse
# arrears_interest_service's formula rather than inventing a second interest calculation.

# East Gate-specific INPUT is permitted (a real AGM fact), building-agnostic CODE. Buildings with
# no configured policy get this all-disabled default (→ $0 penalty), never East Gate's numbers.
_DEFAULT_LATE_FEE_POLICY = {
    "enabled": False,
    "amount": 0.0,
    "period_days": 14,
    "gst_inclusive": True,
    "start_year": None,  # int | None — policy applies from this levy year onward when set
}


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    try:
        return datetime.fromisoformat(str(value)[:19])
    except ValueError:
        try:
            return datetime.combine(date.fromisoformat(str(value)[:10]), datetime.min.time())
        except ValueError:
            return None


def compute_late_fee_penalty(overdue_periods: list[dict], amount: float, period_days: int) -> float:
    """PURE. Sum the flat late fee across overdue periods: for each period, one ``amount`` per
    complete ``period_days`` window it stayed overdue. ``overdue_periods`` items need a
    ``days_overdue`` int. Returns $0 when amount/period_days are non-positive."""
    if amount <= 0 or period_days <= 0:
        return 0.0
    total = 0.0
    for p in overdue_periods:
        days = int(p.get("days_overdue") or 0)
        if days >= period_days:
            total += math.floor(days / period_days) * amount
    return round(total, 2)


def compute_percentage_interest(
    overdue_periods: list[dict], annual_rate_pct: float, max_rate_pct: float,
) -> float:
    """PURE. Sum statutory percentage interest across overdue periods via
    arrears_interest_service.compute_accrued_interest. Each item needs ``principal`` (float),
    ``overdue_since`` and ``as_at`` (datetime/date/ISO). A nil rate yields $0."""
    if annual_rate_pct <= 0:
        return 0.0
    from services.arrears_interest_service import compute_accrued_interest  # lazy — see module note
    total = 0.0
    for p in overdue_periods:
        principal = float(p.get("principal") or 0.0)
        overdue_since = _as_datetime(p.get("overdue_since"))
        as_at = _as_datetime(p.get("as_at"))
        if principal <= 0 or overdue_since is None or as_at is None:
            continue
        total += compute_accrued_interest(
            principal=principal, overdue_since=overdue_since, as_at=as_at,
            annual_rate_pct=annual_rate_pct, max_rate_pct=max_rate_pct,
        )
    return round(total, 2)


def build_overdue_periods_from_status(
    period_status: list[dict], *, grace_period_days: int, as_of: date,
) -> list[dict]:
    """PURE. Turn levy-status ``period_status`` rows into overdue-period inputs for the two
    computations above. A period contributes only if it is past its grace deadline AND still
    carries an unpaid balance as of ``as_of``.

    Uses ``standard_amount`` (the period's OWN levy) as the principal so interest/penalty are
    per-period, never on a cumulative gross. ``overdue_since`` = due_date + grace_period_days;
    days_overdue is measured to ``as_of`` (a closed year should pass its year-end as ``as_of``).
    """
    out: list[dict] = []
    for p in period_status:
        due_raw = p.get("due_date")
        try:
            due = date.fromisoformat(str(due_raw)[:10]) if due_raw else None
        except (ValueError, TypeError):
            due = None
        if due is None:
            continue
        grace_deadline = date.fromordinal(due.toordinal() + max(0, int(grace_period_days)))
        if as_of <= grace_deadline:
            continue  # not yet overdue as of the measurement date
        standard = float(p.get("standard_amount") if p.get("standard_amount") is not None else p.get("amount_due") or 0.0)
        actual_paid = float(p.get("actual_paid") if p.get("actual_paid") is not None else p.get("amount_paid") or 0.0)
        unpaid = round(max(0.0, standard - actual_paid), 2)
        if unpaid <= 0.0:
            continue
        days_overdue = (as_of - grace_deadline).days
        if days_overdue <= 0:
            continue
        out.append({
            "quarter": p.get("quarter"),
            "principal": unpaid,
            "overdue_since": grace_deadline,
            "as_at": as_of,
            "days_overdue": days_overdue,
        })
    return out


def compute_unit_interest_and_penalty(
    *,
    period_status: list[dict],
    grace_period_days: int,
    as_of: date,
    annual_rate_pct: float,
    max_rate_pct: float,
    late_fee_policy: Optional[dict],
    levy_year: Optional[int] = None,
) -> dict[str, Any]:
    """PURE orchestration. Returns computed interest + late-fee penalty for one unit-year.

    The late-fee policy only applies when enabled and (if ``start_year`` is set) the levy year is
    at/after it. Returns a dict with ``interest``, ``penalty``, ``total``, and the inputs used, so
    a caller can display and audit the estimate.
    """
    overdue = build_overdue_periods_from_status(
        period_status, grace_period_days=grace_period_days, as_of=as_of,
    )
    interest = compute_percentage_interest(overdue, annual_rate_pct, max_rate_pct)

    policy = {**_DEFAULT_LATE_FEE_POLICY, **(late_fee_policy or {})}
    policy_applies = bool(policy.get("enabled")) and (
        policy.get("start_year") is None
        or (levy_year is not None and int(levy_year) >= int(policy["start_year"]))
    )
    penalty = (
        compute_late_fee_penalty(overdue, float(policy.get("amount") or 0.0), int(policy.get("period_days") or 14))
        if policy_applies else 0.0
    )

    return {
        "interest": round(interest, 2),
        "penalty": round(penalty, 2),
        "total": round(interest + penalty, 2),
        "source": "computed",
        "rate_pct": annual_rate_pct,
        "late_fee_applied": policy_applies and penalty > 0,
        "overdue_period_count": len(overdue),
    }


# Zeroed-charges literal shared by every "no computed interest/penalty" outcome below
# (no policy, computation failed, or a net-credit unit) -- one definition, not three
# independently-maintained copies (routers/finance.py previously repeated this dict
# inline at each of its three call sites, GAP-FIN-047).
_ZERO_CHARGES: dict[str, Any] = {
    "interest": 0.0,
    "penalty": 0.0,
    "total": 0.0,
    "source": "computed",
    "late_fee_applied": False,
    "overdue_period_count": 0,
}


def zero_charges() -> dict[str, Any]:
    """A fresh copy of the zeroed computed_charges shape (default / no-data case)."""
    return dict(_ZERO_CHARGES)


def compute_credit_interest_estimate(credit_balance: float, annual_rate_pct: float, days: int) -> float:
    """PURE. Owner-earned interest estimate on money the OC currently holds on behalf of a unit
    in credit (``credit_balance`` = -ledger_net_balance, always >= 0) -- symmetric in shape to
    ``compute_percentage_interest`` above, but for the opposite direction of money. Returns $0
    unless the building has explicitly opted in via a positive ``annual_rate_pct`` (GAP-FIN-047
    §2 -- no jurisdiction was found to make this a legal entitlement, so every building defaults
    to 0% until an operator sets one).

    ``days`` is the caller-supplied accrual window -- this function does not know or assume how
    it was derived. The one caller in this codebase (``routers/finance.py::get_levy_status``)
    uses a simple calendar-year-to-date span (Jan 1 of the levy year to ``as_of``), not a
    per-contributing-payment accrual from the exact date each payment creating the credit was
    received -- an honest simplification, consistent with the arrears-side estimate's own
    "for guidance only, not a posted charge" framing, not a claim of transaction-level precision.
    """
    if credit_balance <= 0 or annual_rate_pct <= 0 or days <= 0:
        return 0.0
    daily_rate = annual_rate_pct / 100.0 / 365.0
    return round(credit_balance * daily_rate * days, 2)


def apply_net_credit_override(
    computed_charges: dict[str, Any],
    ledger_net_balance: float,
    *,
    credit_interest_rate_pct: float = 0.0,
    credit_days: int = 0,
) -> dict[str, Any]:
    """A unit in net credit has, by definition, NO arrears -- so it can accrue NO arrears
    interest or late fee (MANDATORY arrears rule: ``net_balance <= 0`` => $0 arrears; GAP-FIN-047).

    ``compute_unit_interest_and_penalty`` above is correct in isolation -- it charges only
    periods that are *currently* unpaid past grace. But the unit-dashboard's own credit-carry
    pass (``routers/finance.py``) rolls a unit's NET credit onto its unpaid periods; when a
    unit's payments land on a later period while an earlier past-grace one is left short, the
    net credit only partially covers that earlier shortfall and a residual survives, accruing
    interest even though the unit owes nothing overall (real incident: TH087, $79.64 estimated
    interest against a $254.98 CR portal balance, 2026-08-05).

    The authoritative, year-scoped ``ledger_net_balance`` (``unit_levy_ledger.net_balance``)
    overrides the per-period estimate: never show estimated arrears interest on a unit that
    owes nothing. The ``0.01`` tolerance matches the float-cents rounding tolerance used
    elsewhere against these same dollar-float ledger fields (see CLAUDE.md footgun re:
    ``unit_levy_ledger`` storing dollars, not cents).

    GAP-FIN-047 §2 (owner-earned credit interest, approved 2026-08-19): when the building has
    opted in via a configured ``credit_interest_rate_pct`` (default 0.0 -- off for every
    building until an operator sets one) and the unit is in REAL credit (``ledger_net_balance``
    strictly negative -- a unit at exactly $0.00 has no money sitting there to earn anything),
    the returned dict gains an additional ``credit_interest_estimate``/``credit_interest_rate_pct``
    field pair, entirely separate from ``interest``/``penalty``/``total`` (which stay $0 and mean
    "owed BY the owner" -- ``credit_interest_estimate`` means "owed TO the owner", a distinct
    concept that must never be summed into or confused with the arrears fields). Additive only:
    existing consumers of this dict that don't know the new keys are unaffected.
    """
    if ledger_net_balance <= 0.01:
        result = zero_charges()
        if ledger_net_balance < 0 and credit_interest_rate_pct > 0 and credit_days > 0:
            result["credit_interest_estimate"] = compute_credit_interest_estimate(
                -ledger_net_balance, credit_interest_rate_pct, credit_days,
            )
            result["credit_interest_rate_pct"] = credit_interest_rate_pct
        return result
    return computed_charges


# ─────────────────────────────────────────────────────────────────────────────
# Per-building late-fee policy config (settings-backed, building-agnostic).
# ─────────────────────────────────────────────────────────────────────────────

LATE_FEE_POLICY_TYPE = "late_fee_policy"


async def get_late_fee_policy(db, building_id: str) -> dict[str, Any]:
    """Return the building's late-fee policy (amount, period_days, gst_inclusive, start_year,
    enabled), or the all-disabled default when none is configured. Building-scoped read."""
    doc = await db.settings.find_one(
        {"building_id": building_id, "type": LATE_FEE_POLICY_TYPE}, {"_id": 0},
    )
    if not doc:
        return dict(_DEFAULT_LATE_FEE_POLICY)
    return {
        "enabled": bool(doc.get("enabled", False)),
        "amount": float(doc.get("amount") or 0.0),
        "period_days": int(doc.get("period_days") or 14),
        "gst_inclusive": bool(doc.get("gst_inclusive", True)),
        "start_year": doc.get("start_year"),
    }


async def upsert_late_fee_policy(db, building_id: str, policy: dict, *, updated_by: Optional[str]) -> dict[str, Any]:
    """Create/update the building's late-fee policy. Only provided fields change."""
    update: dict[str, Any] = {"building_id": building_id, "type": LATE_FEE_POLICY_TYPE}
    for f in ("enabled", "amount", "period_days", "gst_inclusive", "start_year"):
        if policy.get(f) is not None:
            update[f] = policy[f]
    if updated_by:
        update["updated_by"] = updated_by
    await db.settings.update_one(
        {"building_id": building_id, "type": LATE_FEE_POLICY_TYPE}, {"$set": update}, upsert=True,
    )
    return await get_late_fee_policy(db, building_id)
