# @featuretrace:levy-notice-delivery
# Layer: service
# Data flow: cron_payment_reminders.send_levy_reminders
#            → prepare_levy_notice_financial_context(building_id, year, settings) [once per building]
#            → resolve_unit_levy_position(unit_number, context) [per unit]
#            → levy_data{arrears, credit, interest_amount, penalty_amount} → generate_levy_notice_pdf
# Related: backend/utils/finance_helpers.py (get_unit_arrears_map, compute_mongo_quarter_statuses,
#                                            compute_period_due_dates)
#          backend/services/interest_penalty_service.py (compute_unit_interest_and_penalty, get_late_fee_policy)
#          backend/services/arrears_interest_service.py (get_effective_interest_rate)
#          backend/utils/pdf_generator.py (generate_levy_notice_pdf line items)
# Collection: unit_levy_ledger (read-only), settings (late_fee_policy)
"""Per-unit financial position for a Levy Notice — arrears/credit + computed interest & late fees.

The reference levy notices itemise, per unit: the new period's Admin/Sinking levy, the
account's brought-forward position (arrears owed OR credit paid in advance), and any
interest/late-fee estimate on overdue amounts. This module resolves that position by
reusing the *canonical* finance helpers rather than reconstructing any obligation:

  * arrears / credit  → utils.finance_helpers.get_unit_arrears_map, which is built on
    domain.finance.formulas.arrears.unit_arrears_and_credit (net_balance-based, per-unit,
    never netted across units). One unit's credit is displayed as its own real figure and
    never reduces another unit's arrears.
  * interest / penalty → services.interest_penalty_service.compute_unit_interest_and_penalty
    over per-period statuses from utils.finance_helpers.compute_mongo_quarter_statuses — the
    same computed-charges path the /levy-status endpoint uses. Nothing is persisted; these
    are display estimates that correctly yield $0 for a building with a nil rate / no late-fee
    policy (e.g. East Gate from 2022).

Building-level inputs (arrears map, ledger totals, effective rate, late-fee policy, the levy
due-date schedule) are the same for every unit, so ``prepare_levy_notice_financial_context``
resolves them once; ``resolve_unit_levy_position`` is then a cheap per-unit lookup + a pure
computation. All money is handled in the helpers' existing conventions (cents at the
canonical boundaries); this module only reads their rounded-dollar outputs for display.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Frequency → number of levy periods per year, mirroring routers/finance.get_levy_status.
_FREQUENCY_PERIODS = {"monthly": 12, "quarterly": 4, "half_yearly": 2, "yearly": 1}


def _year_int(year: Any) -> int:
    try:
        return int(str(year).split("-")[0])
    except (TypeError, ValueError):
        return date.today().year


def _build_payment_schedule(settings_doc: Dict[str, Any], levy_year_int: int) -> list[Dict[str, Any]]:
    """Per-period due-date schedule for the building's levy year.

    Uses the same settings-driven due-date computation as the /levy-status endpoint so the
    overdue classification underpinning interest/penalty matches what owners see there.
    Returns ``[]`` when the building has no ``levy_due_months`` configured — the caller then
    reports $0 computed charges rather than guessing dates.
    """
    from utils.finance_helpers import compute_period_due_dates

    levy_due_months = settings_doc.get("levy_due_months") or []
    if not levy_due_months:
        return []
    frequency = settings_doc.get("levy_collection_frequency", "quarterly")
    num_periods = _FREQUENCY_PERIODS.get(frequency, 4)
    try:
        due_dates = compute_period_due_dates(
            levy_year_int,
            levy_due_months,
            settings_doc.get("levy_due_day_type") or "first",
            settings_doc.get("levy_due_day"),
            num_periods,
            settings_doc.get("levy_due_custom_dates"),
            fy_start_month=int(settings_doc.get("financial_year_start_month", 1) or 1),
        )
    except (ValueError, TypeError) as exc:
        logger.warning("levy-notice due-date schedule unavailable: %s", exc)
        return []
    return [
        {"quarter": f"Q{i + 1}", "due_date": d}
        for i, d in enumerate(due_dates)
    ]


async def prepare_levy_notice_financial_context(
    db: Any,
    building_id: str,
    year: str,
    settings_doc: Dict[str, Any],
) -> Dict[str, Any]:
    """Resolve the building-level inputs shared by every unit's notice position (once).

    Reads are all building-scoped. Returns a context dict consumed by
    ``resolve_unit_levy_position``. Any sub-resolution that fails degrades to an empty/
    disabled default so a notice can still be issued with correct arrears/credit even when
    the interest/penalty inputs are unavailable.
    """
    from utils.finance_helpers import get_unit_arrears_map

    levy_year_int = _year_int(year)

    # Per-unit arrears/credit (canonical) — one map keyed by unit_number.
    try:
        arrears_map = await get_unit_arrears_map(building_id, year, settings_doc=settings_doc)
    except Exception as exc:  # noqa: BLE001 — notice must still issue with $0 arrears fallback
        logger.warning("levy-notice arrears map unavailable for %s/%s: %s", building_id, year, exc)
        arrears_map = {}

    # Per-unit ledger totals (total_levied / total_paid) for the interest period-status split.
    ledger_map: Dict[str, Dict[str, Any]] = {}
    try:
        ledger_docs = await db.unit_levy_ledger.find(
            {"building_id": building_id, "year": year},
            {"_id": 0, "unit_number": 1, "total_levied": 1, "total_paid": 1},
        ).to_list(500)
        ledger_map = {
            d["unit_number"]: d for d in ledger_docs if d.get("unit_number") is not None
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("levy-notice ledger totals unavailable for %s/%s: %s", building_id, year, exc)

    # Effective interest rate + late-fee policy (both building-scoped, resolved in parallel).
    rate_info: Dict[str, Any] = {"rate_pct": 0.0, "max_rate_pct": 20.0}
    late_fee_policy: Optional[Dict[str, Any]] = None
    try:
        from services.arrears_interest_service import get_effective_interest_rate
        from services.interest_penalty_service import get_late_fee_policy

        rate_info, late_fee_policy = await asyncio.gather(
            get_effective_interest_rate(building_id, db),
            get_late_fee_policy(db, building_id),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("levy-notice interest/penalty inputs unavailable for %s: %s", building_id, exc)

    payment_schedule = _build_payment_schedule(settings_doc, levy_year_int)
    grace_period_days = int(settings_doc.get("grace_period_days", 14) or 0)

    return {
        "arrears_map": arrears_map,
        "ledger_map": ledger_map,
        "rate_info": rate_info or {"rate_pct": 0.0, "max_rate_pct": 20.0},
        "late_fee_policy": late_fee_policy,
        "payment_schedule": payment_schedule,
        "grace_period_days": grace_period_days,
        "levy_year_int": levy_year_int,
    }


def _empty_position() -> Dict[str, Any]:
    return {
        "net_balance": 0.0,
        "arrears": 0.0,
        "credit": 0.0,
        "in_arrears": False,
        "in_credit": False,
        "interest": 0.0,
        "penalty": 0.0,
        "charges_total": 0.0,
        "late_fee_applied": False,
    }


def resolve_unit_levy_position(
    unit_number: str,
    context: Dict[str, Any],
    *,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """Per-unit arrears/credit + computed interest & late-fee estimate for the notice.

    ``context`` is the dict from ``prepare_levy_notice_financial_context``. Pure aside from
    the canonical helper calls it delegates to. Returns rounded-dollar display figures:

        net_balance    signed life-to-date levied-minus-paid (negative = credit)
        arrears        >0 amount overdue (0 when in credit)
        credit         >0 amount paid in advance (0 when in arrears)
        in_arrears / in_credit   booleans (mutually exclusive)
        interest       estimated percentage interest on overdue periods
        penalty        estimated flat late-fee penalty
        charges_total  interest + penalty
        late_fee_applied  whether the building's late-fee policy contributed
    """
    today = today or date.today()
    position = _empty_position()

    arrears_row = (context.get("arrears_map") or {}).get(unit_number)
    if arrears_row:
        position["net_balance"] = round(float(arrears_row.get("net_balance", 0.0) or 0.0), 2)
        position["arrears"] = round(float(arrears_row.get("arrears", 0.0) or 0.0), 2)
        position["credit"] = round(float(arrears_row.get("credit", 0.0) or 0.0), 2)
        position["in_arrears"] = bool(arrears_row.get("in_arrears"))
        position["in_credit"] = bool(arrears_row.get("in_credit"))

    # Interest / late-fee estimate — only when the building has a due-date schedule and a
    # unit ledger row to split into per-period statuses. A unit in credit has no overdue
    # principal, so the computation naturally yields $0.
    payment_schedule = context.get("payment_schedule") or []
    ledger_row = (context.get("ledger_map") or {}).get(unit_number)
    if payment_schedule and ledger_row:
        try:
            from services.interest_penalty_service import compute_unit_interest_and_penalty
            from utils.finance_helpers import compute_mongo_quarter_statuses

            levy_year_int = context.get("levy_year_int", today.year)
            as_of = min(today, date(levy_year_int, 12, 31))
            period_status = compute_mongo_quarter_statuses(
                payment_schedule,
                float(ledger_row.get("total_levied", 0.0) or 0.0),
                float(ledger_row.get("total_paid", 0.0) or 0.0),
                today=as_of,
            )
            rate_info = context.get("rate_info") or {}
            charges = compute_unit_interest_and_penalty(
                period_status=period_status,
                grace_period_days=context.get("grace_period_days", 14),
                as_of=as_of,
                annual_rate_pct=float(rate_info.get("rate_pct", 0.0) or 0.0),
                max_rate_pct=float(rate_info.get("max_rate_pct", 20.0) or 20.0),
                late_fee_policy=context.get("late_fee_policy"),
                levy_year=levy_year_int,
            )
            position["interest"] = round(float(charges.get("interest", 0.0) or 0.0), 2)
            position["penalty"] = round(float(charges.get("penalty", 0.0) or 0.0), 2)
            position["charges_total"] = round(position["interest"] + position["penalty"], 2)
            position["late_fee_applied"] = bool(charges.get("late_fee_applied"))
        except Exception as exc:  # noqa: BLE001 — display estimate; never block the notice
            logger.warning("levy-notice computed charges failed for unit %s: %s", unit_number, exc)

    return position


__all__ = [
    "prepare_levy_notice_financial_context",
    "resolve_unit_levy_position",
]
