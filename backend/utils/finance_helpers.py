# @featuretrace:levy — Finance helper utilities: levy computation, payment projection, fund health.
# Layer: service
# Data flow: unit_levy_ledger + annual_levies + levy_payments → compute_next_estimated_payment()
#            → GET /levy-status/{unit_number} (finance.py) → OwnerDashboard / FinancePage (building-scoped).
# IMPORTANT: net_balance in unit_levy_ledger is always the authoritative balance.
#            Do NOT read strata_web_balance or total_closing as the balance — always use net_balance.
# Related: backend/routers/finance.py
#           frontend/src/pages/dashboard/OwnerDashboard.tsx
#           frontend/src/pages/dashboard/FinancePage.tsx
#           backend/services/health_service.py (get_levy_fund_data, compute_combined_fund_totals)
#           backend/services/report_service.py (get_levy_fund_data, compute_combined_fund_totals)
#           backend/services/anomaly_service.py (get_levy_fund_data)
#           backend/services/forecast_service.py (get_levy_fund_data)
#           backend/domain/finance/metric_registry.py (levy.opening_debt.v1)
"""
Finance Helper Utilities — Clean Architecture (2026-02-19)

Helpers for new collections: annual_levies, levy_categories, unit_levy_ledger.
"""
import calendar
import inspect
from datetime import date, datetime, timedelta

import asyncio
from typing import Dict, List, Any, Optional

from database import db
from domain.finance.formulas.arrears import unit_arrears_and_credit
from services.gst_service import parse_levy_gst_settings
from utils.unit_number import normalise_unit_token

TOTAL_UOE = 10000


def _year_int(value: Any) -> Optional[int]:
    """Parse the leading calendar/levy year from values like ``"2026-2027"``.

    Returns ``None`` for non-year labels instead of guessing. Callers that use
    this to choose a current levy year should exclude unknown labels rather than
    letting them sort above real year rows.
    """
    try:
        return int(str(value)[:4])
    except (TypeError, ValueError):
        return None


def _current_levy_year_for_start_month(today: date, start_month: int) -> int:
    """Return the levy-year label that has started for a building on ``today``.

    Levy years are building-specific. For East Gate the configured start month
    is January, so July 2026 is still levy year 2026. A July-start scheme would
    also be levy year 2026 on/after 1 July 2026, but levy year 2025 before then.
    """
    if start_month <= 1:
        return today.year
    return today.year if today.month >= start_month else today.year - 1


async def _get_levy_year_start_month(building_id: str) -> int:
    """Read the building levy-year start month, defaulting to calendar year.

    The settings collection is tenant-scoped and can be unavailable in small
    unit-test doubles. In that case we deliberately use the same safe default as
    the `/years` route: January, which prevents future-year rows from becoming
    current by accident.
    """
    try:
        settings = await db.settings.find_one(
            {
                "$and": [
                    {"building_id": building_id},
                    {
                        "$or": [
                            {"type": {"$exists": False}},
                            {"type": None},
                            {"type": "general"},
                        ]
                    },
                ]
            },
            {"_id": 0, "financial_year_start_month": 1},
            sort=[("updated_at", -1), ("created_at", -1), ("_id", -1)],
        )
    except Exception:
        settings = None
    return int((settings or {}).get("financial_year_start_month") or 1)


async def resolve_levy_year_for_date(
        building_id: str,
        target_date: date,
        fallback: Optional[str] = None,
) -> Optional[str]:
    """Resolve which annual_levies year a real-world event date belongs to.

    Generalizes _current_levy_year_for_start_month (already date-parameterized,
    just conventionally called with "today") to any date -- the same
    fy_start_month-aware calendar math, applied to when something actually
    happened rather than to right now. This is the shared, correct primitive
    for filing a dated event (a bank transaction, an uploaded receipt) against
    its OWN levy year.

    Before this existed, backend/routers/financial_matching.py's payment-mirror
    code resolved the Mongo unit_levy_ledger year via
    routers.finance._resolve_default_levy_year(building_id) -- which always
    returns the building's CURRENT levy year, regardless of the transaction's
    real date. Every historical-dated "allocate" decision (any transaction
    older than the current levy year) was mirrored into the CURRENT year's
    ledger doc instead of its own -- confirmed live 2026-08-01 against East
    Gate: 2,483 real 2021-2025 transactions were misfiled into 2026's
    unit_levy_ledger, which is why those years show $0 paid and 2026 shows an
    ~8x inflated total. This is a generic bug (the function was building-agnostic
    and dated-transaction-agnostic), not an East-Gate-data quirk -- it recurs for
    any building with a historical-dated allocate decision.

    Returns the newest annual_levies year at or before target_date's own levy
    year (mirrors _resolve_default_levy_year's "never select a not-yet-started
    year" rule, applied relative to target_date instead of today), or
    `fallback` if this building has no annual_levies row at or before that year.
    """
    start_month = await _get_levy_year_start_month(building_id)
    target_levy_year = _current_levy_year_for_start_month(target_date, start_month)
    years = await db.annual_levies.distinct("year", {"building_id": building_id})
    eligible = sorted(
        (y for y in years if (_year_int(y) is not None and _year_int(y) <= target_levy_year)),
        key=lambda y: _year_int(y),
        reverse=True,
    )
    return eligible[0] if eligible else fallback


def get_levy_proposed_amounts(levy_doc: dict, total_uoe: int = TOTAL_UOE) -> tuple:
    """
    Returns (admin_annual_proposed, sinking_annual_proposed) from an annual_levies document.

    Used for budget/forecast charts where the FULL ANNUAL proposed levy is needed,
    not the YTD-actual levy_income (which is only a partial amount for the current year).

    Priority:
      1. proposed_admin_expenses / proposed_sinking_expenses
          — AGM-resolved annual levy targets (ex-GST). For partial/current years
          these remain the only full-year budget source because levy_income is YTD actual.
      2. admin_fund.proposed_amount_cents / sinking_fund.proposed_amount_cents
          (cents, converted to dollars) OR admin_fund.levy_income / sinking_fund.levy_income
          (dollars) — both represent the same ex-GST fund total from a financial-report
          import (gap_tolerant_financial_import.py writes both from the same source row),
          preferring the cents field when present since it's this repo's mandatory
          precision format and the dollar field is documented as a backward-compat
          mirror that isn't always populated (confirmed empirically: East Gate's live
          2026 PDF-sourced import has proposed_amount_cents set but levy_income null).
      3. admin_levy_per_uoe_annual * total_uoe
          — compatibility fallback only for legacy documents that do not carry
          explicit fund totals. These stored rates have mixed historical
          semantics across imports, so they should never outrank fund totals.
    """
    _uoe = int(levy_doc.get("total_uoe") or total_uoe or TOTAL_UOE)
    af = levy_doc.get("admin_fund", {})
    sf = levy_doc.get("sinking_fund", {})

    # Prefer proposed annual levy targets whenever they are stored. Partial/current-year
    # annual_levies docs record YTD actuals in admin_fund/sinking_fund.levy_income.
    proposed_admin = levy_doc.get("proposed_admin_expenses")
    if proposed_admin not in (None, "") and float(proposed_admin or 0) > 0:
        admin = float(proposed_admin)
    elif af.get("proposed_amount_cents") not in (None, ""):
        admin = float(af.get("proposed_amount_cents") or 0) / 100
    elif af.get("levy_income") not in (None, ""):
        admin = float(af.get("levy_income") or 0)
    elif levy_doc.get("admin_levy_per_uoe_annual"):
        admin = float(levy_doc["admin_levy_per_uoe_annual"]) * _uoe
    else:
        admin = 0.0

    # Sinking
    proposed_sinking = levy_doc.get("proposed_sinking_expenses")
    if proposed_sinking not in (None, "") and float(proposed_sinking or 0) > 0:
        sinking = float(proposed_sinking)
    elif sf.get("proposed_amount_cents") not in (None, ""):
        sinking = float(sf.get("proposed_amount_cents") or 0) / 100
    elif sf.get("levy_income") not in (None, ""):
        sinking = float(sf.get("levy_income") or 0)
    elif levy_doc.get("sinking_levy_per_uoe_annual"):
        sinking = float(levy_doc["sinking_levy_per_uoe_annual"]) * _uoe
    else:
        sinking = 0.0

    return round(admin, 2), round(sinking, 2)


def get_levy_rate_breakdown(
        levy_doc: Optional[dict],
        settings_doc: Optional[dict] = None,
        trust_config: Optional[dict] = None,
        total_uoe: int = TOTAL_UOE,
) -> Dict[str, float]:
    """
    Derive both ex-GST and owner-payable levy rates from canonical fund totals.

    The storage contract is stable for fund totals (`proposed_*_expenses` and
    `*_fund.levy_income` are ex-GST), but the historical semantics of the stored
    per-UOE fields vary across import paths. User-facing calculations therefore
    derive payable rates from the ex-GST totals plus the building GST settings,
    with trust_config override budgets taking precedence for the current FY.
    """
    levy_doc = levy_doc or {}
    total_uoe_value = int(levy_doc.get("total_uoe") or total_uoe or TOTAL_UOE or 0)
    gst_config = parse_levy_gst_settings(settings_doc)

    if total_uoe_value <= 0:
        return {
            "total_uoe": 0,
            "gst_registered": gst_config["gst_registered"],
            "gst_rate": gst_config["effective_gst_rate"],
            "gst_label": gst_config["gst_label"],
            "admin_ex_gst_annual": 0.0,
            "admin_ex_gst_quarterly": 0.0,
            "sinking_ex_gst_annual": 0.0,
            "sinking_ex_gst_quarterly": 0.0,
            "admin_payable_annual": 0.0,
            "admin_payable_quarterly": 0.0,
            "sinking_payable_annual": 0.0,
            "sinking_payable_quarterly": 0.0,
            "total_payable_annual": 0.0,
            "total_payable_quarterly": 0.0,
        }

    gst_multiplier = gst_config["gst_multiplier"]
    has_canonical_totals = (
            (levy_doc.get("proposed_admin_expenses") not in (None, "", 0))
            or (levy_doc.get("proposed_sinking_expenses") not in (None, "", 0))
            or ((levy_doc.get("admin_fund") or {}).get("levy_income") not in (None, ""))
            or ((levy_doc.get("sinking_fund") or {}).get("levy_income") not in (None, ""))
            or ((levy_doc.get("admin_fund") or {}).get("proposed_amount_cents") not in (None, ""))
            or ((levy_doc.get("sinking_fund") or {}).get("proposed_amount_cents") not in (None, ""))
    )

    if has_canonical_totals:
        admin_ex_gst_total, sinking_ex_gst_total = get_levy_proposed_amounts(levy_doc, total_uoe_value)
        admin_payable_total = round(admin_ex_gst_total * gst_multiplier, 2)
        sinking_payable_total = round(sinking_ex_gst_total * gst_multiplier, 2)
    else:
        # Raw legacy per-UOE fields are compatibility outputs with mixed history.
        # When they are the only source present, treat them as already-payable owner
        # amounts and derive ex-GST rates backwards from the building GST settings.
        admin_payable_total = round(
            float(levy_doc.get("admin_levy_per_uoe_annual") or 0) * total_uoe_value,
            2,
        )
        sinking_payable_total = round(
            float(levy_doc.get("sinking_levy_per_uoe_annual") or 0) * total_uoe_value,
            2,
        )
        if gst_multiplier > 0:
            admin_ex_gst_total = round(admin_payable_total / gst_multiplier, 2)
            sinking_ex_gst_total = round(sinking_payable_total / gst_multiplier, 2)
        else:
            admin_ex_gst_total = admin_payable_total
            sinking_ex_gst_total = sinking_payable_total

    admin_ex_gst_annual = round(admin_ex_gst_total / total_uoe_value, 6)
    sinking_ex_gst_annual = round(sinking_ex_gst_total / total_uoe_value, 6)

    # Trust config budgets are the final owner-payable levy targets for the active FY.
    trust_config = trust_config or {}
    trust_year = str(trust_config.get("current_financial_year") or "")[:4]
    levy_year = str(levy_doc.get("year") or "")[:4]
    use_trust_override = trust_year and levy_year and trust_year == levy_year

    if use_trust_override and trust_config.get("admin_fund_annual_budget_cents") is not None:
        admin_payable_total = round(float(trust_config["admin_fund_annual_budget_cents"]) / 100, 2)
    if use_trust_override and trust_config.get("sinking_fund_annual_budget_cents") is not None:
        sinking_payable_total = round(float(trust_config["sinking_fund_annual_budget_cents"]) / 100, 2)

    admin_payable_annual = round(admin_payable_total / total_uoe_value, 6)
    sinking_payable_annual = round(sinking_payable_total / total_uoe_value, 6)

    return {
        "total_uoe": total_uoe_value,
        "gst_registered": gst_config["gst_registered"],
        "gst_rate": gst_config["effective_gst_rate"],
        "gst_label": gst_config["gst_label"],
        "admin_ex_gst_annual": admin_ex_gst_annual,
        "admin_ex_gst_quarterly": round(admin_ex_gst_annual / 4, 6),
        "sinking_ex_gst_annual": sinking_ex_gst_annual,
        "sinking_ex_gst_quarterly": round(sinking_ex_gst_annual / 4, 6),
        "admin_payable_annual": admin_payable_annual,
        "admin_payable_quarterly": round(admin_payable_annual / 4, 6),
        "sinking_payable_annual": sinking_payable_annual,
        "sinking_payable_quarterly": round(sinking_payable_annual / 4, 6),
        "total_payable_annual": round(admin_payable_annual + sinking_payable_annual, 6),
        "total_payable_quarterly": round((admin_payable_annual + sinking_payable_annual) / 4, 6),
    }


def _compute_prorated_monthly_interest(
        principal: float,
        overdue_since: datetime,
        as_at: datetime,
        monthly_rate: float,
) -> float:
    """
    Prorate the Site Settings monthly interest rate to the current overdue days.

    `interest_rate_per_month` is stored as a decimal (e.g. 0.02 = 2% / month).
    For "interest till that day" we apply simple daily proration using a 30-day
    month so the next estimated payment can include the current accrued amount.
    """
    if principal <= 0 or monthly_rate <= 0 or as_at <= overdue_since:
        return 0.0

    days_overdue = (as_at - overdue_since).days
    if days_overdue <= 0:
        return 0.0

    return round(principal * monthly_rate * (days_overdue / 30.0), 2)


def compute_period_installment_amounts(total_amount: float, total_periods: int) -> List[float]:
    """
    Split an annual levy into exact cent instalments.

    We floor the first N-1 instalments to whole cents and carry the residual cents
    into the final instalment so the schedule always sums exactly to the annual total.
    Example: 6121.22 / 4 -> [1530.30, 1530.30, 1530.30, 1530.32]
    """
    total_periods = max(0, int(total_periods or 0))
    if total_periods <= 0:
        return []

    total_cents = max(0, round(float(total_amount or 0.0) * 100))
    base_cents = total_cents // total_periods
    amounts_cents = [base_cents] * total_periods
    amounts_cents[-1] = total_cents - (base_cents * (total_periods - 1))
    return [round(amount / 100.0, 2) for amount in amounts_cents]


def sum_ledger_collected_outstanding(ledger_entries: List[Dict[str, Any]]) -> Dict[str, float]:
    """Building-wide levied / collected / outstanding rolled up from per-unit ledger entries,
    WITHOUT netting credits against arrears.

    Outstanding is a per-unit obligation: a unit in credit (``net_balance < 0``) contributes 0
    to outstanding and its own levy counts as fully paid, but its *excess* never offsets another
    unit's shortfall (CLAUDE.md "Arrears Are a Per-Unit Obligation — Never Netted Across Units").
    So::

        total_outstanding = Σ max(net_balance_i, 0)
        total_collected   = total_levied − total_outstanding   (= Σ (levied_i − max(net_balance_i, 0)))

    NEVER ``total_levied − Σ signed(net_balance)`` — that collapses to ``Σ paid_i`` the instant one
    owner pays ahead, netting overpayers against underpayers and understating outstanding (the
    2026 Q2 "$2,275.50 Outstanding" bug).

    NOTE: this is the due-date waterfall's building rollup and is intentionally grace-UNAWARE — it
    is NOT the grace-aware canonical arrears from ``get_arrears_metrics()``.
    """
    total_levied = round(sum(float(e.get("total_levied", 0) or 0) for e in ledger_entries), 2)
    total_outstanding = round(
        sum(max(float(e.get("net_balance", 0) or 0), 0.0) for e in ledger_entries), 2
    )
    total_collected = round(total_levied - total_outstanding, 2)
    return {
        "total_levied": total_levied,
        "total_collected": total_collected,
        "total_outstanding": total_outstanding,
    }


def compute_mongo_quarter_statuses(
        payment_schedule: List[Dict[str, Any]],
        total_levied: float,
        total_paid: float,
        today: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """
    Approximate real per-quarter payment status for MongoDB-backed buildings.

    MongoDB's unit_levy_ledger only holds annual totals — there is no per-quarter
    paid amount to read. This splits total_levied evenly across the schedule (same
    method as compute_period_installment_amounts) and applies total_paid as a
    waterfall against quarters in due-date order, earliest first — consistent with
    how arrears are carried forward across periods elsewhere in this module.

    Status is classified the same way as the PostgreSQL read path
    (routers/finance.py get_unit_dashboard_overview): paid / partial / overdue / unpaid /
    not_yet_due. Previously every quarter was hardcoded "unpaid" regardless of due date,
    which silently disabled overdue-levy detection for any building still on the Mongo
    path.

    GAP-FIN-030 Root Cause D (2026-08-02): the waterfall previously applied `total_paid`
    against quarters strictly in due-date order with NO check on whether a quarter's due
    date had actually passed. For a building close to fully paid YTD (total_paid ≈
    total_levied), this marked quarters that hadn't occurred yet -- e.g. Q3/Q4 while only
    Q1/Q2 are due -- as fully "paid" too, showing "Collected" == "Levied" for periods that
    haven't been invoiced/collected yet. A quarter whose due date is still in the future
    now always gets amount_paid=0 / status="not_yet_due", regardless of remaining_paid --
    payment can only be waterfalled into quarters that are actually due.
    """
    today = today or datetime.now().date()

    schedule_entries = sorted(
        (q for q in payment_schedule if isinstance(q, dict) and q.get("due_date")),
        key=lambda q: q["due_date"],
    )
    period_amounts = compute_period_installment_amounts(total_levied, len(schedule_entries))

    remaining_paid = round(float(total_paid or 0.0), 2)
    quarters: List[Dict[str, Any]] = []
    for entry, q_due in zip(schedule_entries, period_amounts):
        due_date_str = entry.get("due_date")
        due_date_val = None
        if due_date_str:
            try:
                due_date_val = date.fromisoformat(str(due_date_str)[:10])
            except ValueError:
                due_date_val = None

        if due_date_val and due_date_val > today:
            # Not yet due -- no payment applied regardless of remaining_paid balance,
            # even if the annual total_paid would otherwise be large enough to cover it.
            quarters.append({
                "quarter": entry.get("quarter"),
                "due_date": due_date_str,
                "status": "not_yet_due",
                "amount_due": q_due,
                "amount_paid": 0.0,
                "outstanding": q_due,
            })
            continue

        q_paid = round(min(max(remaining_paid, 0.0), q_due), 2)
        remaining_paid = round(remaining_paid - q_paid, 2)
        outstanding = round(max(q_due - q_paid, 0.0), 2)

        if outstanding <= 0.01:
            status = "paid"
        elif q_paid > 0:
            status = "partial"
        elif due_date_val and due_date_val < today:
            status = "overdue"
        else:
            status = "unpaid"

        quarters.append({
            "quarter": entry.get("quarter"),
            "due_date": due_date_str,
            "status": status,
            "amount_due": q_due,
            "amount_paid": q_paid,
            "outstanding": outstanding,
        })
    return quarters


def compute_next_estimated_payment(
        effective_total_paid: float,
        opening_arrears: float,
        period_levy: float,
        due_dates: List[str],
        period_amounts: Optional[List[float]] = None,
        today: Optional[datetime] = None,
        grace_period_days: int = 14,
        interest_rate_per_month: float = 0.0,
        penalty_amount: float = 0.0,
) -> Dict[str, Any]:
    """
    Compute the owner-specific next estimated payment and due date.

    Rules:
      - Before / within grace: show the amount due for the next unfunded current-year
        period, plus any prior-year arrears carried forward.
      - After grace: roll the overdue shortfall into the next period and add the
        building's current interest/penalty settings.
      - Prior-year arrears/credits are represented by opening_arrears, but they do
        not change how many current-year instalments have been funded:
          * prior-year credit can reduce the next amount due
          * prior-year arrears are carried separately on top of the next period
    """
    today = today or datetime.now()
    grace_period_days = max(0, int(grace_period_days or 0))
    interest_rate_per_month = float(interest_rate_per_month or 0.0)
    penalty_amount = round(float(penalty_amount or 0.0), 2)
    scheduled_period_amounts = [
        round(float(amount or 0.0), 2)
        for amount in (period_amounts or [period_levy] * max(len(due_dates), 1))
    ]
    period_count = len(scheduled_period_amounts)

    if period_levy <= 0 or period_count <= 0:
        return {
            "next_payment_adjusted": 0.0,
            "next_due_date": due_dates[0] if due_dates else None,
            "periods_funded": 0,
            "outstanding_current": 0.0,
            "interest_amount": 0.0,
            "penalty_amount_applied": 0.0,
            "past_grace": False,
        }

    prior_year_credit = max(0.0, -float(opening_arrears or 0.0))
    carry_forward_arrears = max(0.0, float(opening_arrears or 0.0))

    # Current-year period funding is based on current-year payments plus any prior-year
    # credit carried in. Prior-year arrears remain additive on top of the next amount due.
    funding_base = effective_total_paid + prior_year_credit
    funding_base_cents = max(0, round(funding_base * 100))
    schedule_cents = [max(0, round(amount * 100)) for amount in scheduled_period_amounts]

    periods_funded = 0
    cumulative_due_cents = 0
    for scheduled_cents in schedule_cents:
        cumulative_due_cents += scheduled_cents
        if funding_base_cents >= cumulative_due_cents:
            periods_funded += 1
        else:
            break

    if periods_funded >= period_count:
        return {
            "next_payment_adjusted": round(carry_forward_arrears, 2),
            "next_due_date": None,
            "periods_funded": periods_funded,
            "outstanding_current": 0.0,
            "interest_amount": 0.0,
            "penalty_amount_applied": 0.0,
            "past_grace": False,
        }

    current_due_cumulative_cents = sum(schedule_cents[: periods_funded + 1])
    outstanding_current = round(
        max(0, current_due_cumulative_cents - funding_base_cents) / 100.0,
        2,
    )
    current_due_str = due_dates[periods_funded] if periods_funded < len(due_dates) else None
    current_due_dt = (
        datetime.strptime(current_due_str, "%Y-%m-%d")
        if current_due_str
        else None
    )
    grace_deadline = (
        current_due_dt + timedelta(days=grace_period_days)
        if current_due_dt
        else None
    )
    past_grace = bool(grace_deadline and grace_deadline < today)

    interest_amount = 0.0
    penalty_amount_applied = 0.0
    next_due_date = current_due_str
    next_payment_adjusted = round(outstanding_current + carry_forward_arrears, 2)

    if past_grace and (outstanding_current > 0 or carry_forward_arrears > 0):
        next_idx = periods_funded + 1
        next_due_date = due_dates[next_idx] if next_idx < len(due_dates) else None
        interest_amount = _compute_prorated_monthly_interest(
            outstanding_current,
            grace_deadline or today,
            today,
            interest_rate_per_month,
        )
        penalty_amount_applied = penalty_amount if penalty_amount > 0 else 0.0
        next_period_amount = (
            scheduled_period_amounts[next_idx]
            if next_idx < period_count
            else 0.0
        )
        next_payment_adjusted = round(
            outstanding_current
            + carry_forward_arrears
            + next_period_amount
            + interest_amount
            + penalty_amount_applied,
            2,
        )

    return {
        "next_payment_adjusted": next_payment_adjusted,
        "next_due_date": next_due_date,
        "periods_funded": periods_funded,
        "outstanding_current": outstanding_current,
        "interest_amount": interest_amount,
        "penalty_amount_applied": penalty_amount_applied,
        "past_grace": past_grace,
    }


def compute_remaining_payment_obligation(
        total_annual: float,
        total_paid: float,
        prev_year_balance: float = 0.0,
) -> float:
    """
    Compute the remaining cash obligation for the current year.

    `unit_levy_ledger.total_paid` already includes carry-forward credit funding for
    credit units imported from prior years, so negative previous-year balances must
    not reduce the obligation a second time. Positive carry-forward arrears still
    increase what the owner needs to pay.
    """
    total_annual = round(float(total_annual or 0.0), 2)
    total_paid = round(float(total_paid or 0.0), 2)
    prior_arrears = round(max(0.0, float(prev_year_balance or 0.0)), 2)
    return round(total_annual + prior_arrears - total_paid, 2)


def normalize_effective_total_paid(
        ledger_total_paid: float,
        live_payments_total: float,
        carry_forward_balance: float = 0.0,
) -> float:
    """
    Combine ledger and live payment signals without dropping carried-forward credit.

    Imported `unit_levy_ledger.total_paid` may already include prior-year credit
    carried into the current year, while live payment collections only hold the
    current-year cash receipts. For credit units we therefore compare the ledger
    total to `(live payments + prior-year credit)` and use whichever is higher.
    """
    ledger_total_paid = round(float(ledger_total_paid or 0.0), 2)
    live_payments_total = round(float(live_payments_total or 0.0), 2)
    prior_year_credit = round(max(0.0, -float(carry_forward_balance or 0.0)), 2)
    return round(max(ledger_total_paid, live_payments_total + prior_year_credit), 2)


def get_annual_fund_balance(fund_doc: Optional[dict]) -> tuple[float, str]:
    """
    Resolve the best available annual fund balance from an annual_levies subdocument.

    Priority:
      1. current_balance        — explicit current bank balance written by fund-balance updates
      2. closing_balance_actual — imported actual close when present
      3. closing_balance        — projected/imported close used by legacy annual levy imports
      4. opening_balance        — last-resort non-null balance so callers do not silently fall to zero
    """
    fund = fund_doc or {}
    for key in ("current_balance", "closing_balance_actual", "closing_balance", "opening_balance"):
        value = fund.get(key)
        if value in (None, ""):
            continue
        try:
            return round(float(value), 2), key
        except (TypeError, ValueError):
            continue
    return 0.0, "missing"


async def get_latest_levy_year(building_id: str) -> Optional[str]:
    """Return the newest annual_levies year that has started for the building.

    A future budget/import row can exist before the levy year starts. Do not let
    those rows become the implicit "current" year for dashboards, reminders, or
    analytics.
    """
    start_month = await _get_levy_year_start_month(building_id)
    current_levy_year = _current_levy_year_for_start_month(datetime.now().date(), start_month)
    years = await db.annual_levies.distinct("year", {"building_id": building_id})

    eligible = sorted(
        (year for year in years if (_year_int(year) is not None and _year_int(year) <= current_levy_year)),
        key=lambda year: _year_int(year),
        reverse=True,
    )
    return str(eligible[0]) if eligible else None


async def get_current_levy_year(building_id: str) -> str:
    """Return the building's current levy-year label as a string (e.g. ``"2026"``).

    Unlike :func:`get_latest_levy_year` — which returns the newest *existing*
    ``annual_levies`` row and is therefore ``None`` for a brand-new building with
    no levy data yet — this computes the levy year that has *started* as of today
    from the building's configured start month. A freshly onboarded scheme with
    no levy rows still resolves to a concrete current year, which is exactly what
    Track A onboarding needs when creating a building's first levy plan.

    Honours the per-building levy-year start month (defaults to January when the
    tenant-scoped settings row is unavailable, matching ``get_latest_levy_year``).
    Requires Mongo building context to be set for the settings lookup.
    """
    start_month = await _get_levy_year_start_month(building_id)
    return str(_current_levy_year_for_start_month(datetime.now().date(), start_month))


async def get_latest_ledger_year(building_id: str) -> Optional[str]:
    """Return the most recent year that has unit_levy_ledger entries for the building."""
    entry = await db.unit_levy_ledger.find_one(
        {"building_id": building_id}, {"_id": 0, "year": 1}, sort=[("year", -1)]
    )
    return entry["year"] if entry else None


async def get_levy_rates(year: str, building_id: str) -> Dict[str, float]:
    """
    Return owner-payable admin and sinking levy rates per UOE for a given year and building.

    Returns dict with:
      admin_annual, admin_quarterly, sinking_annual, sinking_quarterly
    """
    levy_task = db.annual_levies.find_one({"year": year, "building_id": building_id}, {"_id": 0})

    settings_query = {
        "$and": [
            {"building_id": building_id},
            {
                "$or": [
                    {"type": {"$exists": False}},
                    {"type": None},
                    {"type": "general"},
                ]
            },
        ]
    }
    settings_lookup = db.settings.find_one(
        settings_query,
        {"_id": 0},
        sort=[("updated_at", -1), ("created_at", -1), ("_id", -1)],
    )
    building_lookup = db.buildings.find_one({"id": building_id}, {"_id": 0, "trust_config": 1})

    async def _resolve_lookup(result):
        """Generated function header.

        Function: _resolve_lookup
        Path: backend/utils/finance_helpers.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if inspect.isawaitable(result):
            return await result
        return result if isinstance(result, dict) else {}

    levy, settings_doc, building_doc = await asyncio.gather(
        levy_task,
        _resolve_lookup(settings_lookup),
        _resolve_lookup(building_lookup),
    )

    if not levy:
        return {
            "admin_annual": 0,
            "admin_quarterly": 0,
            "sinking_annual": 0,
            "sinking_quarterly": 0,
            "admin_annual_ex_gst": 0,
            "admin_quarterly_ex_gst": 0,
            "sinking_annual_ex_gst": 0,
            "sinking_quarterly_ex_gst": 0,
            "gst_registered": False,
            "gst_rate": 0.0,
            "gst_label": "ex GST",
        }

    rate_breakdown = get_levy_rate_breakdown(
        levy,
        settings_doc=settings_doc,
        trust_config=(building_doc or {}).get("trust_config"),
    )
    return {
        "admin_annual": rate_breakdown["admin_payable_annual"],
        "admin_quarterly": rate_breakdown["admin_payable_quarterly"],
        "sinking_annual": rate_breakdown["sinking_payable_annual"],
        "sinking_quarterly": rate_breakdown["sinking_payable_quarterly"],
        "admin_annual_ex_gst": rate_breakdown["admin_ex_gst_annual"],
        "admin_quarterly_ex_gst": rate_breakdown["admin_ex_gst_quarterly"],
        "sinking_annual_ex_gst": rate_breakdown["sinking_ex_gst_annual"],
        "sinking_quarterly_ex_gst": rate_breakdown["sinking_ex_gst_quarterly"],
        "gst_registered": rate_breakdown["gst_registered"],
        "gst_rate": rate_breakdown["gst_rate"],
        "gst_label": rate_breakdown["gst_label"],
    }


def compute_unit_levy(uoe: float, rates: Dict[str, float]) -> Dict[str, float]:
    """Perform the math for unit levy calculation from pre-fetched rates."""
    admin_annual = round(rates.get("admin_annual", 0) * uoe, 2)
    sinking_annual = round(rates.get("sinking_annual", 0) * uoe, 2)
    total_annual = round(admin_annual + sinking_annual, 2)

    return {
        "uoe": uoe,
        "admin_annual": admin_annual,
        "admin_quarterly": round(admin_annual / 4, 2),
        "sinking_annual": sinking_annual,
        "sinking_quarterly": round(sinking_annual / 4, 2),
        "total_annual": total_annual,
        "total_quarterly": round(total_annual / 4, 2),
    }


async def calculate_unit_levy(unit_number: str, year: str, building_id: str) -> Dict[str, float]:
    """
    Calculate levy amounts for a specific unit in a given year.
    Derives amounts from unit's UOE × levy rates.
    """
    unit = await db.units.find_one({"building_id": building_id, "unit_number": unit_number},
                                   {"_id": 0, "entitlement": 1})
    if not unit:
        return {}

    uoe = unit.get("entitlement", 0)
    rates = await get_levy_rates(year, building_id)
    return compute_unit_levy(uoe, rates)


async def get_expense_breakdown_by_category(year: str, building_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Get expense categories for a year grouped by fund type.
    Uses actual_amount if available, otherwise budgeted_amount.
    """
    cats = await db.levy_categories.find(
        {"year": year, "building_id": building_id}, {"_id": 0}
    ).sort("name", 1).to_list(100)

    # levy_categories.fund_type is stored in the canonical short form ("admin"/"sinking")
    # by every current writer (see FUND_TYPE_NORM above) -- normalise to the legacy long
    # form before comparing, or this silently matches zero documents (found live 2026-08-01
    # against East Gate: every category actual/budget came back $0.00 for every year).
    admin_cats = [
        {"name": c["name"], "value": round(c.get("actual_amount") or c.get("budgeted_amount", 0), 2)}
        for c in cats if legacy_fund_type(normalise_fund_type(c.get("fund_type"))) == "administrative"
    ]
    sinking_cats = [
        {"name": c["name"], "value": round(c.get("actual_amount") or c.get("budgeted_amount", 0), 2)}
        for c in cats if c.get("fund_type") == "sinking"
    ]

    admin_cats.sort(key=lambda x: x["value"], reverse=True)
    sinking_cats.sort(key=lambda x: x["value"], reverse=True)

    return {"administrative": admin_cats, "sinking": sinking_cats}


def get_fy_label(levy_year: int, fy_start_month: int = 1) -> str:
    """
    Return the human-readable financial year label for a given levy_year and FY start month.

    Rules:
      - fy_start_month = 1 (January): all months fall in the same calendar year
        → label = "FY {levy_year}"  e.g. "FY 2026"
      - Any other start month: the FY spans two calendar years
        → label = "FY {levy_year}–{levy_year+1 mod 100:02d}"  e.g. "FY 2025-26"

    levy_year is always the calendar year of the FY's START month.

    Building vs ATO tax year:
      Buildings are free to choose any start month. The ATO tax year (Jul–Jun)
      uses the same two-year label convention (e.g. "FY 2025-26"). A building
      starting in March 2025 also produces "FY 2025-26" but runs Mar 2025–Feb 2026,
      which differs from the ATO year. Both produce the same label format — callers
      should always store levy_year + fy_start_month together for unambiguity.
    """
    if fy_start_month == 1:
        return f"FY {levy_year}"
    short_next = (levy_year + 1) % 100
    return f"FY {levy_year}-{short_next:02d}"


def get_fy_date_range(levy_year: int, fy_start_month: int = 1) -> tuple:
    """
    Return (start_date_str, end_date_str) for the financial year as ISO strings.

    Examples:
      levy_year=2026, fy_start_month=1  → ("2026-01-01", "2026-12-31")
      levy_year=2025, fy_start_month=7  → ("2025-07-01", "2026-06-30")
      levy_year=2025, fy_start_month=3  → ("2025-03-01", "2026-02-28")
      levy_year=2025, fy_start_month=11 → ("2025-11-01", "2026-10-31")
    """
    if fy_start_month == 1:
        end_year, end_month = levy_year, 12
    else:
        # End month = month before start month, in levy_year+1
        end_month = fy_start_month - 1
        end_year = levy_year + 1

    last_day = calendar.monthrange(end_year, end_month)[1]
    start_str = f"{levy_year:04d}-{fy_start_month:02d}-01"
    end_str = f"{end_year:04d}-{end_month:02d}-{last_day:02d}"
    return start_str, end_str


def compute_period_due_dates(
        levy_year: int,
        levy_due_months: List[int],
        levy_due_day_type: str,
        levy_due_day: Optional[int],
        num_periods: int,
        levy_due_custom_dates: Optional[dict] = None,
        fy_start_month: int = 1,
) -> List[str]:
    """
    Returns ISO date strings for each levy period due date, in FY chronological order.

    Parameters:
      levy_year         – Calendar year of the FY's START month (e.g. 2026 for Jan-start,
                          2025 for a Jul-start or Nov-start FY).
      levy_due_months   – List of calendar month numbers (1=Jan … 12=Dec) indicating
                          which months levies are due.
      levy_due_day_type – "first" | "middle" | "last" | "custom"
      levy_due_day      – Explicit day number (used when day_type is not "custom").
      num_periods       – How many due dates to return (typically 4 for quarterly).
      levy_due_custom_dates – Dict {str(month): int(day)} for per-month day overrides.
      fy_start_month    – The calendar month (1–12) where the FY begins. Default 1 (Jan).
                          READ from settings.financial_year_start_month.

    Year-rollover logic (handles any FY start month):
      Months >= fy_start_month fall in levy_year (same calendar year as FY start).
      Months <  fy_start_month fall in levy_year + 1 (next calendar year, later in FY).

      Examples with fy_start_month=1 (Jan, East Gate):
        levy_year=2026, months=[3,6,9,12], levy_due_day_type="last"
          → Q1=2026-03-31, Q2=2026-06-30, Q3=2026-09-30, Q4=2026-12-31

      Examples with fy_start_month=7 (Jul, ATO-aligned):
        levy_year=2025, months=[10,1,4,7]:
           7 >= 7 → 2025-07-01 (Q1 — first day of FY)
          10 >= 7 → 2025-10-01 (Q2)
           1 <  7 → 2026-01-01 (Q3)
           4 <  7 → 2026-04-01 (Q4)

      Examples with fy_start_month=11 (Nov):
        levy_year=2025, months=[11,2,5,8]:
          11 >= 11 → 2025-11-01 (Q1)
           2 <  11 → 2026-02-01 (Q2)
           5 <  11 → 2026-05-01 (Q3)
           8 <  11 → 2026-08-01 (Q4)

    Months are ordered by their position within the FY (not raw ascending).
    Default day is 1 when day_type is missing/unrecognised.
    """

    # Sort months in FY order: months starting from fy_start_month, then wrapping.
    # Sort key: m itself if m >= fy_start_month (earlier in FY), else m+12 (later, next cal year).
    def _fy_order_key(m: int) -> int:
        """Generated function header.

        Function: _fy_order_key
        Path: backend/utils/finance_helpers.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return m if m >= fy_start_month else m + 12

    months_in_fy_order = sorted((int(m) for m in levy_due_months), key=_fy_order_key)[:num_periods]

    dates = []
    custom = levy_due_custom_dates or {}
    for m in months_in_fy_order:
        # Assign calendar year based on position relative to FY start
        year = levy_year if m >= fy_start_month else levy_year + 1
        last_day = calendar.monthrange(year, m)[1]

        if levy_due_day_type == "first":
            day = 1
        elif levy_due_day_type == "middle":
            day = 15
        elif levy_due_day_type == "custom":
            m_str = str(m)
            if m_str in custom:
                day = min(int(custom[m_str]), last_day)
            else:
                day = min(levy_due_day or 1, last_day)
        elif levy_due_day_type == "last":
            day = last_day
        else:
            # Default: first of month
            day = min(levy_due_day or 1, last_day)

        dates.append(f"{year:04d}-{m:02d}-{day:02d}")

    # Dates are already in FY chronological order (sorted by _fy_order_key above).
    # No secondary sort needed — re-sorting by string would break cross-year ordering.
    return dates


async def get_arrears_metrics(
        year: str,
        num_overdue_periods: int,
        building_id: str,
        total_periods: int = 4,
        subtract_payments: bool = True,
        in_grace_periods: int = 0,
) -> Dict[str, Any]:
    """
    Canonical per-unit arrears amount/count for `year`, building-scoped.

    2026-08-03 rewrite: the previous "periods overdue" branch reconstructed a
    unit's obligation as `opening + num_overdue_periods * period_levy`, where
    `opening` (admin_opening + sinking_opening) is a life-to-date cumulative
    ledger balance, not prior-year-only carry-forward, and `period_levy` was
    independently re-derived from UOE entitlement rather than the ledger's
    own levied amount. That mismatch produced 31 "units in arrears" for East
    Gate (13195) against a true count of 14 (live-verified: net_balance > 0
    for exactly 14 of 87 units). See domain.finance.formulas.arrears module
    docstring for the same defect's earlier appearance in get_arrears_board().

    unit_levy_ledger.net_balance is the authoritative balance (see this
    file's header comment) and already rolls prior-period debt into the
    current period, since levy periods are charged incrementally as they
    become due. The only correction on top of net_balance is excluding the
    still-in-grace portion of the currently-charged period via
    domain.finance.formulas.arrears.unit_arrears_and_credit() — never an
    independently reconstructed obligation. Never nets one unit's credit
    against another unit's arrears; each unit is evaluated independently.
    """
    if num_overdue_periods == 0 and not subtract_payments:
        # Historical proxy: count units with opening arrears (ignore payments).
        # Distinct, narrower use case (fixed historical-year snapshot display,
        # not live current-year arrears) — untouched by the rewrite above.
        pipeline = [
            {"$match": {"year": year, "building_id": building_id}},
            {"$project": {"opening": {"$add": ["$admin_opening", "$sinking_opening"]}}},
            {"$match": {"opening": {"$gt": 0.01}}},
            {"$count": "count"},
        ]
        agg = await db.unit_levy_ledger.aggregate(pipeline).to_list(1)
        return {"total_amount": 0.0, "unit_count": agg[0]["count"] if agg else 0}

    rows = await _compute_unit_arrears_rows(building_id, year, in_grace_periods)

    total_amount = round(sum(r["arrears"] for r in rows if r["in_arrears"]), 2)
    unit_count = sum(1 for r in rows if r["in_arrears"])
    total_credit_amount = round(sum(r["credit"] for r in rows if r["in_credit"]), 2)
    credit_unit_count = sum(1 for r in rows if r["in_credit"])

    return {
        "total_amount": total_amount,
        "unit_count": unit_count,
        "total_credit_amount": total_credit_amount,
        "credit_unit_count": credit_unit_count,
    }


async def _compute_unit_arrears_rows(
        building_id: str,
        year: str,
        in_grace_periods: int,
) -> List[Dict[str, Any]]:
    """Per-unit grace-aware arrears/credit rows — the ONE per-unit implementation
    shared by get_arrears_metrics() (building aggregate) and get_unit_arrears_map()
    (per-unit map). Every arrears figure — aggregate count/total AND any per-unit
    ``arrears_flag`` — derives from this single loop over
    domain.finance.formulas.arrears.unit_arrears_and_credit(), so a per-unit flag
    can never diverge from the building total (GAP-FIN-040).
    """
    ledger_docs = await db.unit_levy_ledger.find(
        {"building_id": building_id, "year": year},
        {"_id": 0, "unit_number": 1, "net_balance": 1, "total_levied": 1, "quarters_charged": 1},
    ).to_list(200)

    rows: List[Dict[str, Any]] = []
    for d in ledger_docs:
        net_balance_cents = round((d.get("net_balance") or 0) * 100)
        quarters_charged = d.get("quarters_charged") or 0
        total_levied_cents = round((d.get("total_levied") or 0) * 100)
        per_period_cents = (
            round(total_levied_cents / quarters_charged) if quarters_charged > 0 else total_levied_cents
        )
        in_grace_portion_cents = min(max(net_balance_cents, 0), per_period_cents * in_grace_periods)

        arrears_cents, credit_cents = unit_arrears_and_credit(
            net_balance_cents=net_balance_cents,
            in_grace_portion_cents=in_grace_portion_cents,
        )
        rows.append({
            "unit_number": d.get("unit_number"),
            "net_balance": round(net_balance_cents / 100, 2),
            "arrears": round(arrears_cents / 100, 2),
            "credit": round(credit_cents / 100, 2),
            "in_arrears": arrears_cents > 1,
            "in_credit": credit_cents > 1,
        })
    return rows


async def get_unit_arrears_map(
        building_id: str,
        year: Optional[str] = None,
        *,
        settings_doc: Optional[dict] = None,
        today: Optional[date] = None,
) -> Dict[str, Dict[str, Any]]:
    """Canonical PER-UNIT grace-aware arrears map (GAP-FIN-040 companion to
    get_building_arrears_summary). Returns
    ``{unit_number: {net_balance, arrears, credit, in_arrears, in_credit}}``
    using the SAME unit_arrears_and_credit formula as get_arrears_metrics — so a
    per-unit ``arrears_flag`` (lot_financial_summary, finance-intelligence,
    building-stress, anomaly detection, BI hotspots) never diverges from the
    building aggregate or the canonical /arrears/detail figures.
    """
    today = today or date.today()
    if settings_doc is None:
        settings_doc = await _get_general_settings_doc(building_id)
    if not year:
        year = await get_latest_levy_year(building_id) or str(today.year)
    levy_year_int = _year_int(year) or today.year
    counts = compute_grace_period_counts(
        settings_doc=settings_doc, levy_year_int=levy_year_int, today=today,
    )
    rows = await _compute_unit_arrears_rows(building_id, year, counts["in_grace_count"])
    return {r["unit_number"]: r for r in rows if r.get("unit_number") is not None}


async def get_collection_rate_metrics(year: str, building_id: str) -> Dict[str, Any]:
    """
    Canonical per-unit, due-date-gated Collection Rate metrics for `year`,
    building-scoped. GAP-FIN-035 (2026-08-03) — see
    domain.finance.formulas.collection.due_date_collection_rate() and
    docs/architecture/financial-summary-analysis-of-issues.md Rule 53:
    "Collection performance is not fund health." This is a DIFFERENT metric
    from current_year_collection_rate()/fund_health, which uses the FULL
    annual levy as its denominator — that formula must never be labelled
    "Collection Rate".

    unit_levy_ledger.total_levied/admin_levied/sinking_levied for a
    Mongo-backed building are YTD-to-date charged amounts (confirmed live,
    GAP-FIN-033 Part B1) — i.e. already scoped to periods that have actually
    come due, not the full annual levy. That makes them the correct
    "due to date" denominator for this metric without any further per-period
    due-date computation.

    Per-unit (never cross-unit netted): due_to_date = total_levied;
    collected_to_date = due_to_date - max(net_balance, 0) — a unit's own
    credit can raise its own collected_to_date up to (never past) its own
    due_to_date; a unit's own arrears reduces only its own collected_to_date.
    collected_in_advance = max(-net_balance, 0) — identical in shape to
    get_arrears_metrics()'s total_credit_amount (same unit_arrears_and_credit
    credit branch), computed independently here to keep this function
    self-contained and avoid a second, possibly-diverging DB round trip.
    """
    ledger_docs = await db.unit_levy_ledger.find(
        {"building_id": building_id, "year": year, "is_test_data": {"$ne": True}},
        {
            "_id": 0, "unit_number": 1, "net_balance": 1, "total_levied": 1,
            "admin_levied": 1, "admin_closing": 1, "sinking_levied": 1, "sinking_closing": 1,
        },
    ).to_list(200)

    def _fund_metrics(levied_cents: int, closing_or_balance_cents: int) -> tuple[int, int, int]:
        due = max(0, levied_cents)
        # Floored at 0: a unit whose net_balance/closing carries a positive
        # (arrears) balance larger than its own measured due_to_date (e.g. a
        # data gap where total_levied/admin_levied wasn't populated for this
        # year) must contribute zero collected, never a negative amount that
        # would silently subtract from every OTHER unit's collected total.
        collected = max(0, due - max(closing_or_balance_cents, 0))
        advance = max(-closing_or_balance_cents, 0)
        return due, collected, advance

    total_due = total_collected = total_advance = 0
    admin_due = admin_collected = admin_advance = 0
    sinking_due = sinking_collected = sinking_advance = 0

    for d in ledger_docs:
        net_balance_cents = round((d.get("net_balance") or 0) * 100)
        total_levied_cents = round((d.get("total_levied") or 0) * 100)
        due, collected, advance = _fund_metrics(total_levied_cents, net_balance_cents)
        total_due += due
        total_collected += collected
        total_advance += advance

        admin_levied_cents = round((d.get("admin_levied") or 0) * 100)
        admin_closing_cents = round((d.get("admin_closing") or 0) * 100)
        due, collected, advance = _fund_metrics(admin_levied_cents, admin_closing_cents)
        admin_due += due
        admin_collected += collected
        admin_advance += advance

        sinking_levied_cents = round((d.get("sinking_levied") or 0) * 100)
        sinking_closing_cents = round((d.get("sinking_closing") or 0) * 100)
        due, collected, advance = _fund_metrics(sinking_levied_cents, sinking_closing_cents)
        sinking_due += due
        sinking_collected += collected
        sinking_advance += advance

    from domain.finance.formulas.collection import due_date_collection_rate

    total_result = due_date_collection_rate(due_to_date_cents=total_due, collected_to_date_cents=total_collected)
    admin_result = due_date_collection_rate(due_to_date_cents=admin_due, collected_to_date_cents=admin_collected)
    sinking_result = due_date_collection_rate(due_to_date_cents=sinking_due, collected_to_date_cents=sinking_collected)

    return {
        "collection_rate_pct": float(total_result.collection_rate_pct),
        "due_to_date": round(total_due / 100, 2),
        "collected_to_date": round(total_collected / 100, 2),
        "collected_in_advance": round(total_advance / 100, 2),
        "admin_fund": {
            "collection_rate_pct": float(admin_result.collection_rate_pct),
            "due_to_date": round(admin_due / 100, 2),
            "collected_to_date": round(admin_collected / 100, 2),
            "collected_in_advance": round(admin_advance / 100, 2),
        },
        "sinking_fund": {
            "collection_rate_pct": float(sinking_result.collection_rate_pct),
            "due_to_date": round(sinking_due / 100, 2),
            "collected_to_date": round(sinking_collected / 100, 2),
            "collected_in_advance": round(sinking_advance / 100, 2),
        },
    }


def _unit_type_group(unit_type_raw: Optional[str], unit_number: str) -> str:
    """Classify a unit into Apartment/Townhouse/Villa/Retail/Commercial/Other.

    Mirrors services.levy_fairness_service._group_key() exactly (already
    imported cross-module elsewhere in this codebase, e.g.
    routers/intelligence.py) — kept as a local copy here rather than a fresh
    cross-import so this module's only dependency on levy_fairness_service
    stays the existing unit_arrears_and_credit-style pattern. Any change to
    the classification rule must be made in both places.
    """
    ut = (unit_type_raw or "").lower()
    if "apartment" in ut:
        return "Apartment"
    if "townhouse" in ut:
        return "Townhouse"
    if "villa" in ut:
        return "Villa"
    if "retail" in ut:
        return "Retail"
    if "commercial" in ut:
        return "Commercial"
    un = (unit_number or "").upper()
    if un.startswith("UA"):
        return "Apartment"
    if un.startswith("TH"):
        return "Townhouse"
    return "Other"


async def get_fund_collections_by_unit_type(building_id: str, years: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Life-to-date Admin Fund / Sinking Fund collected totals, each broken down
    by unit-type group (Apartment/Townhouse/...). GAP-FIN-035 Item 3
    (2026-08-03).

    Reuses the same per-unit-clamped collected_to_date formula as
    get_collection_rate_metrics() (due_to_date=levied, collected=due -
    max(closing_balance, 0)), applied per (unit, year) and summed across
    every year found for this building — never a raw sum of a cumulative
    "total_paid"-style field, which for Mongo-backed buildings can include
    back-solved/cumulative amounts not reliably scoped to a single year.

    Data-source precedence (CLAUDE.md, mandatory): checks
    core.domain_cutover_status for the finance_ledger domain. East Gate and
    other Mongo-primary buildings are correctly served from Mongo per the
    documented phased-cutover state (GAP-FIN-031: Postgres bank-matching is
    still incomplete for this domain) — this is not routed around. If a
    building's finance_ledger domain is ever promoted to postgres_read/write,
    this function still serves Mongo and flags `postgres_promotion_pending`
    in the response so a future session builds the Postgres-native path
    rather than this silently going stale.
    """
    from models.cutover_status import CutoverMode
    from services.cutover_status_service import get_or_default_cutover_status

    cutover_status = await get_or_default_cutover_status(building_id, "finance_ledger")
    postgres_promotion_pending = cutover_status.mode in (
        CutoverMode.postgres_read, CutoverMode.postgres_write,
    )

    units = await db.units.find(
        {"building_id": building_id},
        {"_id": 0, "unit_number": 1, "unit_type": 1, "property_type": 1},
    ).to_list(200)
    unit_type_by_number = {
        u["unit_number"]: _unit_type_group(u.get("unit_type") or u.get("property_type"), u["unit_number"])
        for u in units
    }

    if years is None:
        distinct_years = await db.unit_levy_ledger.distinct(
            "year", {"building_id": building_id, "is_test_data": {"$ne": True}}
        )
        years = sorted(str(y) for y in distinct_years if y is not None)

    def _fund_metrics(levied_cents: int, closing_or_balance_cents: int) -> tuple[int, int]:
        due = max(0, levied_cents)
        collected = max(0, due - max(closing_or_balance_cents, 0))
        return due, collected

    by_type: Dict[str, Dict[str, int]] = {}
    admin_due_total = admin_collected_total = 0
    sinking_due_total = sinking_collected_total = 0

    for year in years:
        ledger_docs = await db.unit_levy_ledger.find(
            {"building_id": building_id, "year": year, "is_test_data": {"$ne": True}},
            {
                "_id": 0, "unit_number": 1,
                "admin_levied": 1, "admin_closing": 1,
                "sinking_levied": 1, "sinking_closing": 1,
            },
        ).to_list(200)

        for d in ledger_docs:
            unit_type = unit_type_by_number.get(d.get("unit_number"), "Other")
            bucket = by_type.setdefault(unit_type, {
                "admin_due": 0, "admin_collected": 0,
                "sinking_due": 0, "sinking_collected": 0,
            })

            admin_levied_cents = round((d.get("admin_levied") or 0) * 100)
            admin_closing_cents = round((d.get("admin_closing") or 0) * 100)
            due, collected = _fund_metrics(admin_levied_cents, admin_closing_cents)
            bucket["admin_due"] += due
            bucket["admin_collected"] += collected
            admin_due_total += due
            admin_collected_total += collected

            sinking_levied_cents = round((d.get("sinking_levied") or 0) * 100)
            sinking_closing_cents = round((d.get("sinking_closing") or 0) * 100)
            due, collected = _fund_metrics(sinking_levied_cents, sinking_closing_cents)
            bucket["sinking_due"] += due
            bucket["sinking_collected"] += collected
            sinking_due_total += due
            sinking_collected_total += collected

    return {
        "building_id": building_id,
        "years_included": years,
        "data_source": "mongo",
        "postgres_promotion_pending": postgres_promotion_pending,
        "admin_fund": {
            "collected_to_date": round(admin_collected_total / 100, 2),
            "due_to_date": round(admin_due_total / 100, 2),
        },
        "sinking_fund": {
            "collected_to_date": round(sinking_collected_total / 100, 2),
            "due_to_date": round(sinking_due_total / 100, 2),
        },
        "total_collected_to_date": round((admin_collected_total + sinking_collected_total) / 100, 2),
        "by_unit_type": {
            unit_type: {
                "admin_collected_to_date": round(v["admin_collected"] / 100, 2),
                "admin_due_to_date": round(v["admin_due"] / 100, 2),
                "sinking_collected_to_date": round(v["sinking_collected"] / 100, 2),
                "sinking_due_to_date": round(v["sinking_due"] / 100, 2),
                "total_collected_to_date": round((v["admin_collected"] + v["sinking_collected"]) / 100, 2),
            }
            for unit_type, v in sorted(by_type.items())
        },
    }


async def get_arrears_unit_count(
        year: str,
        num_overdue_periods: int,
        building_id: str,
        total_periods: int = 4,
        subtract_payments: bool = True,
        in_grace_periods: int = 0,
) -> int:
    """Count units with genuine (grace-aware) arrears for the given year."""
    res = await get_arrears_metrics(
        year, num_overdue_periods, building_id, total_periods, subtract_payments, in_grace_periods
    )
    return res["unit_count"]


async def get_arrears_total_amount(
        year: str,
        num_overdue_periods: int,
        building_id: str,
        total_periods: int = 4,
        in_grace_periods: int = 0,
) -> float:
    """Compute the true aggregate arrears amount for the plan/year."""
    res = await get_arrears_metrics(year, num_overdue_periods, building_id, total_periods, True, in_grace_periods)
    return res["total_amount"]


async def _get_general_settings_doc(building_id: str) -> Optional[dict]:
    """Read the building's general settings doc (levy schedule + grace policy).

    Mirrors the query used by routers/finance.py and _get_levy_year_start_month:
    the most recently updated non-typed / "general" settings row for the building.
    Returns None (never raises) so callers degrade to safe defaults.
    """
    try:
        return await db.settings.find_one(
            {
                "$and": [
                    {"building_id": building_id},
                    {
                        "$or": [
                            {"type": {"$exists": False}},
                            {"type": None},
                            {"type": "general"},
                        ]
                    },
                ]
            },
            {"_id": 0},
            sort=[("updated_at", -1), ("created_at", -1), ("_id", -1)],
        )
    except Exception:
        return None


def compute_grace_period_counts(
        *,
        settings_doc: Optional[dict],
        levy_year_int: int,
        today: Optional[date] = None,
) -> Dict[str, Any]:
    """Pure levy-schedule math: how many periods are past grace (overdue) vs still
    within grace as of ``today``, for a building's configured levy-due schedule.

    Extracted from routers/finance.py::_compute_grace_aware_arrears so every
    consumer derives ``num_overdue`` / ``in_grace_count`` from ONE implementation
    (GAP-FIN-040 calculation singularity). A past levy year naturally yields
    ``num_overdue == total_periods`` and ``in_grace_count == 0`` (all its due
    dates + grace have elapsed); a future year yields both 0.
    """
    today = today or date.today()
    grace_days = int(settings_doc.get("grace_period_days", 14)) if settings_doc else 14
    due_months = settings_doc.get("levy_due_months", [3, 6, 9, 12]) if settings_doc else [3, 6, 9, 12]
    due_day_type = settings_doc.get("levy_due_day_type", "first") if settings_doc else "first"
    due_day = settings_doc.get("levy_due_day") if settings_doc else None
    custom_dates = settings_doc.get("levy_due_custom_dates", {}) if settings_doc else {}
    fy_start_month = int(settings_doc.get("financial_year_start_month", 1)) if settings_doc else 1

    total_periods = len(due_months) if due_months else 4
    computed_dates = compute_period_due_dates(
        levy_year_int, due_months, due_day_type, due_day, total_periods, custom_dates,
        fy_start_month=fy_start_month,
    )
    overdue_periods: List[str] = []
    in_grace_periods: List[str] = []
    for i, d_str in enumerate(computed_dates):
        try:
            due = date.fromisoformat(d_str)
            grace_deadline = due + timedelta(days=grace_days)
            if today > grace_deadline:
                overdue_periods.append(f"Q{i + 1}")
            elif due < today <= grace_deadline:
                in_grace_periods.append(f"Q{i + 1}")
        except (ValueError, TypeError):
            pass
    return {
        "grace_days": grace_days,
        "total_periods": total_periods,
        "computed_dates": computed_dates,
        "overdue_periods": overdue_periods,
        "in_grace_periods": in_grace_periods,
        "num_overdue": len(overdue_periods),
        "in_grace_count": len(in_grace_periods),
    }


async def get_building_arrears_summary(
        building_id: str,
        year: Optional[str] = None,
        *,
        settings_doc: Optional[dict] = None,
        today: Optional[date] = None,
) -> Dict[str, Any]:
    """THE canonical building-level arrears entry point (GAP-FIN-040).

    Every page/endpoint/service that needs a building's grace-aware "units in
    arrears" count and true-arrears dollar total MUST call this (or
    get_arrears_metrics directly) — never re-derive arrears from a raw
    ``net_balance > 0`` aggregate or the banned ``balance_owing`` /
    ``total_closing`` fields. This reads the building levy schedule / grace
    policy from settings, computes which periods are overdue vs in-grace, and
    delegates the money math to get_arrears_metrics() ->
    domain.finance.formulas.arrears.unit_arrears_and_credit(). Credits are never
    netted against another unit's arrears; they are returned as their own total.

    Returns a dict with: year, units_in_arrears, true_arrears_amount,
    total_credit_amount, credit_unit_count, num_overdue, in_grace_count,
    total_periods.
    """
    today = today or date.today()
    if settings_doc is None:
        settings_doc = await _get_general_settings_doc(building_id)
    if not year:
        year = await get_latest_levy_year(building_id) or str(today.year)
    levy_year_int = _year_int(year) or today.year

    counts = compute_grace_period_counts(
        settings_doc=settings_doc, levy_year_int=levy_year_int, today=today,
    )
    metrics = await get_arrears_metrics(
        year,
        counts["num_overdue"],
        building_id,
        counts["total_periods"],
        subtract_payments=True,
        in_grace_periods=counts["in_grace_count"],
    )
    return {
        "year": year,
        "units_in_arrears": metrics["unit_count"],
        "true_arrears_amount": round(metrics["total_amount"], 2),
        "total_credit_amount": round(metrics.get("total_credit_amount", 0.0) or 0.0, 2),
        "credit_unit_count": metrics.get("credit_unit_count", 0),
        "num_overdue": counts["num_overdue"],
        "in_grace_count": counts["in_grace_count"],
        "total_periods": counts["total_periods"],
    }


async def get_unit_ledger_stats(year: str, building_id: str) -> Dict[str, Any]:
    """
    Get aggregate stats from unit_levy_ledger for a given year.
    Returns totals and unit payment status counts.

    Falls back to computing live from annual_levies + levy_payments when
    no ledger documents exist for the requested year (e.g. current/future years
    not yet seeded).

    Fields added 2026-03-02:
      total_opening_arrears — sum of opening balances > $0.01 (historical carry-forward)
      units_opening_arrears — count of units with opening_balance > $0.01
    """
    pipeline = [
        {"$match": {"year": year, "building_id": building_id, "is_test_data": {"$ne": True}}},
        {"$group": {
            "_id": None,
            "total_levied": {"$sum": "$total_levied"},
            "total_paid": {"$sum": "$total_paid"},
            "total_net_balance": {"$sum": "$net_balance"},
            "units_owing": {"$sum": {"$cond": [{"$gt": ["$net_balance", 0]}, 1, 0]}},
            "units_credit": {"$sum": {"$cond": [{"$lt": ["$net_balance", 0]}, 1, 0]}},
            "units_paid_up": {"$sum": {"$cond": [{"$eq": ["$net_balance", 0]}, 1, 0]}},
            "total_outstanding": {
                "$sum": {"$cond": [{"$gt": ["$net_balance", 0]}, "$net_balance", 0]}
            },
            # METRIC[total_opening_arrears]: aggregation source — sum of (admin_opening + sinking_opening)
            # where > $0.01 (filters credits/zeroes). Used by /stats/building-kpis via get_unit_ledger_stats().
            # NOTE: finance.py uses $opening_arrears field directly; both must agree.
            # If you change this filter or formula, update routers/finance.py and test_metric_consistency.py.
            "total_opening_arrears": {
                "$sum": {
                    "$cond": [
                        {"$gt": [{"$add": ["$admin_opening", "$sinking_opening"]}, 0.01]},
                        {"$add": ["$admin_opening", "$sinking_opening"]},
                        0
                    ]
                }
            },
            "units_opening_arrears": {
                "$sum": {
                    "$cond": [
                        {"$gt": [{"$add": ["$admin_opening", "$sinking_opening"]}, 0.01]},
                        1, 0
                    ]
                }
            },
            # Per-fund payment totals — used for live balance computation
            "admin_paid": {"$sum": "$admin_paid"},
            "sinking_paid": {"$sum": "$sinking_paid"},
        }}
    ]
    result = await db.unit_levy_ledger.aggregate(pipeline).to_list(1)
    ls = result[0] if result else {}

    if result:
        return {
            "total_levied": round(ls.get("total_levied", 0), 2),
            "total_paid": round(ls.get("total_paid", 0), 2),
            "net_balance": round(ls.get("total_net_balance", 0), 2),
            "units_owing": ls.get("units_owing", 0),
            "units_credit": ls.get("units_credit", 0),
            "units_paid_up": ls.get("units_paid_up", 0),
            "total_outstanding": round(ls.get("total_outstanding", 0), 2),
            "total_opening_arrears": round(ls.get("total_opening_arrears", 0), 2),
            "units_opening_arrears": ls.get("units_opening_arrears", 0),
            "admin_paid": round(ls.get("admin_paid", 0), 2),
            "sinking_paid": round(ls.get("sinking_paid", 0), 2),
        }

    # ── Computed fallback: derive from annual_levies + confirmed levy_payments ──
    # Used when unit_levy_ledger has no entries for this year (e.g. not yet seeded).
    levy = await db.annual_levies.find_one({"year": year, "building_id": building_id}, {"_id": 0})
    if not levy:
        return {
            "total_levied": 0,
            "total_paid": 0,
            "net_balance": 0,
            "units_owing": 0,
            "units_credit": 0,
            "units_paid_up": 0,
            "total_outstanding": 0,
            "total_opening_arrears": 0,
            "units_opening_arrears": 0,
            "admin_paid": 0,
            "sinking_paid": 0,
        }

    rates = await get_levy_rates(year, building_id)
    admin_rate = rates.get("admin_annual", 0)
    sinking_rate = rates.get("sinking_annual", 0)
    rate_per_uoe = admin_rate + sinking_rate

    units = await db.units.find(
        {"building_id": building_id}, {"_id": 0, "unit_number": 1, "entitlement": 1}
    ).to_list(200)

    levy_per_unit: Dict[str, float] = {
        u["unit_number"]: round(rate_per_uoe * u.get("entitlement", 0), 2)
        for u in units
    }
    total_levied = round(sum(levy_per_unit.values()), 2)

    # Sum confirmed payments per unit
    confirmed_pipeline = [
        {"$match": {"building_id": building_id, "year": year, "status": "confirmed"}},
        {"$group": {"_id": "$unit_number", "paid": {"$sum": "$amount"}}},
    ]
    confirmed = await db.levy_payments.aggregate(confirmed_pipeline).to_list(200)
    paid_by_unit: Dict[str, float] = {r["_id"]: round(r["paid"], 2) for r in confirmed}
    total_paid = round(sum(paid_by_unit.values()), 2)

    units_owing = sum(
        1 for u in units
        if levy_per_unit.get(u["unit_number"], 0) - paid_by_unit.get(u["unit_number"], 0) > 0.005
    )
    units_credit = sum(
        1 for u in units
        if paid_by_unit.get(u["unit_number"], 0) - levy_per_unit.get(u["unit_number"], 0) > 0.005
    )
    units_paid_up = len(units) - units_owing - units_credit
    total_outstanding = round(
        sum(
            max(0, levy_per_unit.get(u["unit_number"], 0) - paid_by_unit.get(u["unit_number"], 0))
            for u in units
        ),
        2,
    )

    # Estimate per-fund split using levy rate ratio (only fallback available here)
    _admin_ratio_fb = (admin_rate / rate_per_uoe) if rate_per_uoe > 0 else 0.77
    _admin_paid_fb = round(total_paid * _admin_ratio_fb, 2)
    _sinking_paid_fb = round(total_paid - _admin_paid_fb, 2)

    return {
        "total_levied": total_levied,
        "total_paid": total_paid,
        "net_balance": round(total_levied - total_paid, 2),
        "units_owing": units_owing,
        "units_credit": units_credit,
        "units_paid_up": units_paid_up,
        "total_outstanding": total_outstanding,
        # Fallback path has no ledger docs so no opening balances available
        "total_opening_arrears": 0,
        "units_opening_arrears": 0,
        # Per-fund split estimated from levy rate ratio
        "admin_paid": _admin_paid_fb,
        "sinking_paid": _sinking_paid_fb,
    }


async def get_levy_fund_data(year: str, building_id: str) -> Optional[Dict[str, Any]]:
    """Return the annual_levies-shaped levy doc for a year, preferring the new schema.

    Extracted from 4 byte-identical private copies of this same function
    (health_service.py, report_service.py, anomaly_service.py,
    forecast_service.py each defined their own `_get_levy_data`) — GAP-FIN-016
    Phase 2. Behaviour is unchanged: new-schema shape first via
    get_legacy_annual_levy_shape(), falling back to a raw annual_levies read.
    """
    from services.financial_service import get_legacy_annual_levy_shape
    data = await get_legacy_annual_levy_shape(year, building_id)
    if data:
        return data
    return await db.annual_levies.find_one({"year": year, "building_id": building_id}, {"_id": 0})


def compute_combined_fund_totals(levy: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """Sum admin_fund + sinking_fund income/expenses/closing_balance from an
    annual_levies-shaped doc (as returned by get_levy_fund_data()).

    Extracted from identical inline arithmetic duplicated in health_service.py
    and report_service.py (GAP-FIN-016 Phase 2) — same fields, same "or 0"
    guards, same rounding (none — callers round for display as before).
    """
    admin = (levy or {}).get("admin_fund", {}) or {}
    sinking = (levy or {}).get("sinking_fund", {}) or {}
    total_income = (admin.get("total_income", 0) or 0) + (sinking.get("total_income", 0) or 0)
    total_expenses = (admin.get("total_expenses", 0) or 0) + (sinking.get("total_expenses", 0) or 0)
    closing_balance = (admin.get("closing_balance", 0) or 0) + (sinking.get("closing_balance", 0) or 0)
    return {
        "total_income": total_income,
        "total_expenses": total_expenses,
        "closing_balance": closing_balance,
        "surplus": total_income - total_expenses,
    }


async def get_ledger_quality(year: str, building_id: str) -> Dict[str, Any]:
    """
    Reconcile unit_levy_ledger rows for `year` against the canonical `units`
    collection for `building_id`. This is the GAP-FIN-014 guard against
    treating ledger-row counts as the operational unit denominator — a
    duplicate or orphaned ledger row must never inflate/shrink the unit
    count shown on finance pages (e.g. East Gate's "59 of 156 units" bug,
    where the building actually has 87 lots).

    Unit numbers are normalised (utils.unit_number.normalise_unit_token)
    before comparison, which makes reconciliation resilient to case and
    whitespace variants of the same token (e.g. " th087 " vs "TH087").
    normalise_unit_token only uppercases, strips whitespace, and strips a
    leading "Unit " prefix — it does NOT reconcile cross-prefix formats
    (e.g. "87" vs "TH087"); a ledger row written under a different lot-
    prefix convention than the canonical `units` row will surface as a
    missing/extra unit here, not a silently-matched duplicate. Cross-prefix
    resolution is the job of the separate, per-row
    utils.unit_number.resolve_canonical_unit_number(), which this bulk
    reconciliation intentionally does not call for cost reasons.

    unit_levy_ledger has a unique index on (building_id, year,
    unit_number) so exact-string duplicates should not occur via normal
    writes — duplicates here are almost always a normalisation collision
    or a bulk-import bypass of that index.

    Ledger rows with a missing/null unit_number are skipped (and counted
    separately under malformed_ledger_row_count) rather than surfaced as
    an extra_ledger_units entry, since a None value would break
    string-join formatting in downstream warning messages.

    is_test_data filtering is applied to `units` (matching the existing
    convention in routers/portfolio.py) but NOT to unit_levy_ledger, which
    has no is_test_data field in any known seed/production data.
    """
    unit_docs = await db.units.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}},
        {"_id": 0, "unit_number": 1},
    ).to_list(None)
    canonical_units = [u["unit_number"] for u in unit_docs if u.get("unit_number")]
    norm_to_canonical: Dict[str, str] = {
        normalise_unit_token(u): u for u in canonical_units
    }

    ledger_docs = await db.unit_levy_ledger.find(
        {"building_id": building_id, "year": year},
        {"_id": 0, "unit_number": 1, "net_balance": 1},
    ).to_list(None)

    seen_tokens: Dict[str, int] = {}
    extra_ledger_units: List[str] = []
    matched_canonical_net_balance: Dict[str, float] = {}
    malformed_ledger_row_count = 0
    for row in ledger_docs:
        raw_unit_number = row.get("unit_number")
        if not raw_unit_number:
            malformed_ledger_row_count += 1
            continue
        token = normalise_unit_token(raw_unit_number)
        seen_tokens[token] = seen_tokens.get(token, 0) + 1
        canonical = norm_to_canonical.get(token)
        if canonical is None:
            if raw_unit_number not in extra_ledger_units:
                extra_ledger_units.append(raw_unit_number)
            continue
        # Last-write-wins for duplicate rows; duplicates are reported separately.
        matched_canonical_net_balance[canonical] = row.get("net_balance", 0) or 0

    duplicate_ledger_units = sorted(
        norm_to_canonical.get(token, token)
        for token, count in seen_tokens.items()
        if count > 1 and token in norm_to_canonical
    )
    # Redundant-row count (e.g. one unit duplicated 3x contributes 2 here),
    # distinct from duplicate_ledger_units' per-unit count — used for the
    # kpi-contract's row-level duplicate_ledger_rows field.
    duplicate_ledger_row_count = sum(
        count - 1 for count in seen_tokens.values() if count > 1
    )
    missing_ledger_units = sorted(
        canonical for token, canonical in norm_to_canonical.items()
        if token not in seen_tokens
    )

    canonical_unit_count = len(canonical_units)
    distinct_ledger_unit_count = len(matched_canonical_net_balance)
    ledger_row_count = len(ledger_docs)

    canonical_status_counts = {"paid_up": 0, "owing": 0, "credit": 0}
    for net_balance in matched_canonical_net_balance.values():
        if net_balance > 0:
            canonical_status_counts["owing"] += 1
        elif net_balance < 0:
            canonical_status_counts["credit"] += 1
        else:
            canonical_status_counts["paid_up"] += 1

    is_unit_count_consistent = (
        canonical_unit_count == distinct_ledger_unit_count
        and not duplicate_ledger_units
        and not missing_ledger_units
        and not extra_ledger_units
        and not malformed_ledger_row_count
    )

    return {
        "canonical_unit_count": canonical_unit_count,
        "ledger_row_count": ledger_row_count,
        "distinct_ledger_unit_count": distinct_ledger_unit_count,
        "duplicate_ledger_units": duplicate_ledger_units,
        "duplicate_ledger_row_count": duplicate_ledger_row_count,
        "missing_ledger_units": missing_ledger_units,
        "extra_ledger_units": extra_ledger_units,
        "malformed_ledger_row_count": malformed_ledger_row_count,
        "is_unit_count_consistent": is_unit_count_consistent,
        "source": "units + unit_levy_ledger",
        # Non-spec extra field: shared canonical-unit status classification so
        # /finance/summary and /finance/kpi-contract never diverge on
        # units_paid_up/units_owing/units_credit.
        "canonical_status_counts": canonical_status_counts,
    }


async def get_budget_summary(year: str, building_id: str) -> Dict[str, Any]:
    """
    Get budget summary totals for a year.

    Reads budgeted amounts from financial_categories and computes actual amounts
    from financial_transactions (never from levy_categories.actual_amount).
    Falls back to levy_categories for budgeted_amount only if new collection
    has no data.
    """
    # ── primary: new schema ───────────────────────────────────────────────
    cats = await db.financial_categories.find(
        {"financial_year": year, "building_id": building_id}, {"_id": 0, "fund_type": 1, "budgeted_amount": 1}
    ).to_list(200)

    if not cats:
        # Fallback: read budgeted_amount from levy_categories (actual_amount NOT read)
        cats_legacy = await db.levy_categories.find(
            {"year": year, "building_id": building_id},
            {"_id": 0, "fund_type": 1, "budgeted_amount": 1}
        ).to_list(100)
        cats = [{"fund_type": c.get("fund_type", ""), "budgeted_amount": c.get("budgeted_amount", 0)} for c in
                cats_legacy]

    # ── actual amounts: aggregate from financial_transactions ─────────────
    tx_pipeline = [
        {"$match": {"financial_year": year, "building_id": building_id, "transaction_type": "expense"}},
        {"$group": {"_id": "$fund_type", "actual": {"$sum": "$amount"}}},
    ]
    tx_results = await db.financial_transactions.aggregate(tx_pipeline).to_list(5)
    actuals_by_fund: Dict[str, float] = {r["_id"]: round(r["actual"], 2) for r in tx_results}

    summary: Dict[str, Any] = {}
    for c in cats:
        ft = c.get("fund_type", "")
        if ft not in summary:
            summary[ft] = {"budgeted": 0.0, "actual": 0.0}
        summary[ft]["budgeted"] += c.get("budgeted_amount") or 0

    for ft, actual in actuals_by_fund.items():
        if ft not in summary:
            summary[ft] = {"budgeted": 0.0, "actual": 0.0}
        summary[ft]["actual"] = actual

    return {k: {"budgeted": round(v["budgeted"], 2), "actual": round(v["actual"], 2)}
            for k, v in summary.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Fund-type normalisation helpers
# ─────────────────────────────────────────────────────────────────────────────

# Maps legacy "administrative" to the new short form "admin" used in the
# refactored financial_categories / financial_transactions collections.
FUND_TYPE_NORM: Dict[str, str] = {
    "administrative": "admin",
    "admin": "admin",
    "sinking": "sinking",
}

# Reverse map: new short form → legacy API name (for backward-compat responses)
FUND_TYPE_LEGACY: Dict[str, str] = {
    "admin": "administrative",
    "sinking": "sinking",
}


def normalise_fund_type(fund_type: str) -> str:
    """Return the canonical short fund type ('admin' or 'sinking')."""
    return FUND_TYPE_NORM.get(fund_type, fund_type)


def legacy_fund_type(fund_type: str) -> str:
    """Return the legacy API fund type name ('administrative' or 'sinking')."""
    return FUND_TYPE_LEGACY.get(fund_type, fund_type)




def normalise_financial_year(value) -> str:
    """Reduce any financial-year label to its canonical closing calendar year.

    ``"2025-2026"`` -> ``"2026"``; ``"2026"`` -> ``"2026"``; ``"FY2026"`` -> ``"2026"``.

    Why this exists (2026-08-28)
    ----------------------------
    Three call sites already inlined ``financial_year.split("-")[1] if "-" in
    financial_year else financial_year`` — ``routers/strata_sync.py``,
    ``scripts/run_scraper.py`` and ``seeds/migrate_strata_sync_to_financial.py`` —
    while ``staging_strata_web_snapshots`` stores whatever label its caller passed.
    East Gate consequently holds snapshots labelled ``"2025"``, ``"2026"`` AND
    ``"2026-2027"`` side by side.

    That matters because ``strata_web_balance_inference_service`` pairs consecutive
    snapshots with an **exact string match** on ``financial_year``. Two snapshots of
    the same actual year under different labels never pair, so the delta inference
    returns "no earlier snapshot to compare against" and silently produces ZERO
    payment candidates — a whole scraper run reduced to a no-op with only a warning
    string in a return dict to show for it.

    Returns the input stripped as a fallback when no 4-digit year can be found,
    rather than raising — the caller is matching, not validating, and a label this
    function cannot parse should still match itself.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    # A hyphenated range names its CLOSING year, matching the convention the three
    # inlined call sites above already use.
    if "-" in text:
        head, _, tail = text.rpartition("-")
        head, tail = head.strip(), tail.strip()
        if len(tail) == 4 and tail.isdigit():
            return tail
        # "2026-27" / "FY2025-26": a two-digit tail borrows the head's century.
        if len(tail) == 2 and tail.isdigit():
            head_digits = "".join(ch for ch in head if ch.isdigit())
            if len(head_digits) == 4:
                return head_digits[:2] + tail
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 4:
        return digits
    # "FY2025-26" style: take the last four consecutive digits if they form a year.
    if len(digits) >= 4 and digits[-4:].isdigit() and digits[-4:].startswith(("19", "20")):
        return digits[-4:]
    return text


__all__ = [
    "TOTAL_UOE",
    "FUND_TYPE_NORM",
    "FUND_TYPE_LEGACY",
    "normalise_fund_type",
    "normalise_financial_year",
    "legacy_fund_type",
    "resolve_levy_year_for_date",
    "compute_period_due_dates",
    "get_latest_levy_year",
    "get_latest_ledger_year",
    "get_levy_rates",
    "calculate_unit_levy",
    "get_expense_breakdown_by_category",
    "get_unit_ledger_stats",
    "get_ledger_quality",
    "get_arrears_unit_count",
    "get_arrears_total_amount",
    "get_budget_summary",
]
