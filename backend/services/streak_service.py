# @featuretrace:dashboard-v2 — Streak Service: computes consecutive on-time payment quarters per unit.
# Layer: service
# Data flow: OwnerDashboard → /analytics/my-streak → streak_service.on_time_quarters → unit_levy_ledger (building-scoped).
# Related: backend/routers/analytics.py
#           frontend/src/components/dashboard/PaymentStreakCard.tsx

"""
Streak Service — compute consecutive on-time quarterly levy payments.

Source priority (first with data wins):
  1. unit_levy_ledger quarterly records with status/paid_on_time  — demo seed path
  2. unit_levy_ledger annual records with q1_paid…q4_paid sub-fields — CSV import path
  3. unit_levy_ledger annual records with levy_status/net_balance  — payment API / Strata Web import
  4. levy_payments confirmed records                               — portal/Stripe path only
"""
import logging
from datetime import datetime, timezone

from request_context import get_ctx_building_id, set_ctx_building_id

logger = logging.getLogger(__name__)

_QUARTERS = ["Q1", "Q2", "Q3", "Q4"]


async def on_time_quarters(unit_number: str, building_id: str, db=None) -> dict:
    """
    Return streak data for a given unit:
      {
        "streak": <int>,            # consecutive on-time quarters (most recent first)
        "total_quarters": <int>,    # total quarters assessed
        "on_time_count": <int>,     # total on-time quarters ever
        "on_time_pct": <float>,     # % of all quarters paid on time
        "recent_quarters": [...],   # last 14 quarter records [{year, quarter, on_time, status}]
        "source": <str>,            # which data path produced the result
      }
    """
    if db is None:
        from database import db as _db
        db = _db

    previous_building_id = get_ctx_building_id()
    try:
        if building_id and previous_building_id != building_id:
            set_ctx_building_id(building_id)
        # Fetch all ledger records for this unit — request all fields needed for every
        # fallback path in a single query so we only hit the DB once.
        cursor = db.unit_levy_ledger.find(
            {"unit_number": unit_number},
            {
                "year": 1, "quarter": 1, "status": 1, "paid_on_time": 1, "due_date": 1,
                # per-quarter sub-fields written by CSV import path
                "q1_amount": 1, "q2_amount": 1, "q3_amount": 1, "q4_amount": 1,
                "q1_paid": 1,   "q2_paid": 1,   "q3_paid": 1,   "q4_paid": 1,
                "q1_balance": 1, "q2_balance": 1, "q3_balance": 1, "q4_balance": 1,
                # annual summary fields written by payment API and Strata Web import path
                "net_balance": 1, "total_paid": 1, "total_levied": 1, "levy_status": 1,
            }
        ).sort([("year", -1), ("quarter", -1)])
        # 60 = 15 years × 4 quarters — sufficient for streak + 14-dot track.
        ledger = await cursor.to_list(60)
    except Exception as e:
        logger.warning("streak_service: failed to fetch ledger for %s: %s", unit_number, e)
        return _empty()
    finally:
        if building_id and previous_building_id != building_id:
            set_ctx_building_id(previous_building_id)

    # ── Path 1: proper quarterly records (status / paid_on_time per quarter) ──
    # Written by the demo seed and any future quarterly-aware write path.
    if ledger:
        quarterly = [r for r in ledger if r.get("quarter") is not None]
        if quarterly:
            quarterly.sort(
                key=lambda r: (str(r.get("year", "0")), str(r.get("quarter", "Q0") or "Q0")),
                reverse=True,
            )
            return _compute_streak(quarterly, source="unit_levy_ledger_quarterly")

    # ── Path 2: annual records with per-quarter sub-fields (CSV import) ───────
    # financial_import_service.process_unit_levy_status_csv writes q1_amount…q4_balance.
    if ledger:
        synthetic = _expand_annual_quarterly(ledger)
        if synthetic:
            return _compute_streak(synthetic, source="unit_levy_ledger_csv_quarterly")

    # ── Path 3: annual records with levy_status / net_balance ─────────────────
    # Payment API (_upsert_ledger_for_payment) and Strata Web portal import write
    # annual summaries without per-quarter breakdown.
    if ledger:
        synthetic = _expand_annual_balance(ledger)
        if synthetic:
            return _compute_streak(synthetic, source="unit_levy_ledger_annual")

    # ── Path 4: levy_payments confirmed records (portal/Stripe only) ──────────
    # Last resort: DEFT, BPay, and manually entered payments are NOT in levy_payments.
    return await _streak_from_payments(unit_number, building_id, db)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _is_on_time(rec: dict) -> bool:
    """Generated function header.

    Function: _is_on_time
    Path: backend/services/streak_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if rec.get("paid_on_time") is True:
        return True
    status = str(rec.get("status", "") or "").lower()
    return status in ("paid", "on_time", "current")


def _compute_streak(records: list, source: str = "unknown") -> dict:
    """Compute streak stats from a list of {year, quarter, status?, paid_on_time?} records.

    Records must be sorted most-recent-first before calling.
    """
    streak = 0
    for rec in records:
        if _is_on_time(rec):
            streak += 1
        else:
            break

    total = len(records)
    on_time_count = sum(1 for r in records if _is_on_time(r))
    on_time_pct = round((on_time_count / total) * 100, 1) if total else 0.0

    recent = [
        {
            "year": rec.get("year"),
            "quarter": rec.get("quarter"),
            "on_time": _is_on_time(rec),
            "status": rec.get("status"),
        }
        for rec in records[:14]
    ]

    return {
        "streak": streak,
        "total_quarters": total,
        "on_time_count": on_time_count,
        "on_time_pct": on_time_pct,
        "recent_quarters": recent,
        "source": source,
    }


def _expand_annual_quarterly(annual_records: list) -> list:
    """Expand annual ledger records with q1_paid…q4_balance sub-fields into quarterly pseudo-records.

    The CSV import path (financial_import_service.process_unit_levy_status_csv) writes
    q1_amount, q1_paid, q1_balance … q4_amount, q4_paid, q4_balance on the annual document.
    Quarters with no levied amount AND no paid data are skipped (not yet due).
    """
    result = []
    for rec in annual_records:
        year = str(rec.get("year", ""))
        for n, q in enumerate(_QUARTERS, 1):
            qk = f"q{n}"
            q_amount = float(rec.get(f"{qk}_amount") or 0)
            q_paid_raw = rec.get(f"{qk}_paid")
            # Skip quarters with no data at all — not yet levied
            if q_amount == 0 and q_paid_raw is None:
                continue
            q_paid = float(q_paid_raw or 0)
            q_balance = rec.get(f"{qk}_balance")
            if q_balance is not None:
                on_time = float(q_balance) <= 0.01  # fully paid (allow rounding)
            elif q_amount > 0:
                on_time = q_paid >= q_amount * 0.99  # ≥99% of amount paid
            else:
                on_time = q_paid > 0
            result.append({
                "year": year,
                "quarter": q,
                "status": "paid" if on_time else "overdue",
                "paid_on_time": on_time,
            })

    if not result:
        return []

    result.sort(
        key=lambda r: (str(r.get("year", "0")), str(r.get("quarter", "Q0"))),
        reverse=True,
    )
    return result


def _expand_annual_balance(annual_records: list) -> list:
    """Expand annual ledger records (no per-quarter breakdown) into quarterly pseudo-records.

    On-time status is derived from levy_status (CSV import enrichment) when present,
    otherwise from net_balance. The current calendar year is skipped when only net_balance
    is available because a mid-year balance is always positive for up-to-date owners
    (full-year levy levied, only part-year paid so far).
    """
    current_year = str(datetime.now(timezone.utc).year)
    result = []
    for rec in annual_records:
        year = str(rec.get("year", ""))
        levy_status = str(rec.get("levy_status", "") or "").lower()

        if levy_status in ("current", "credit", "paid"):
            on_time = True
        elif levy_status == "arrears":
            on_time = False
        else:
            # No levy_status — derive from net_balance, but skip current year since
            # a mid-year balance is always positive even for fully up-to-date payers.
            if year >= current_year:
                continue
            net_balance = float(rec.get("net_balance") or 0)
            on_time = net_balance <= 0.01  # paid off or in credit

        for q in _QUARTERS:
            result.append({
                "year": year,
                "quarter": q,
                "status": "paid" if on_time else "overdue",
                "paid_on_time": on_time,
            })

    if not result:
        return []

    result.sort(
        key=lambda r: (str(r.get("year", "0")), str(r.get("quarter", "Q0"))),
        reverse=True,
    )
    return result


async def _streak_from_payments(unit_number: str, building_id: str, db) -> dict:
    """Compute streak from levy_payments confirmed records (portal/Stripe payment path only).

    Note: levy_payments does NOT capture DEFT, BPay, or manually entered payments.
    Those are recorded in unit_levy_ledger via the payment API write path instead.
    """
    previous = get_ctx_building_id()
    try:
        if building_id and previous != building_id:
            set_ctx_building_id(building_id)
        cursor = db.levy_payments.find(
            {"unit_number": unit_number, "status": "confirmed"},
            {"year": 1, "quarter": 1}
        ).sort([("year", -1), ("quarter", -1)])
        payments = await cursor.to_list(60)
    except Exception as e:
        logger.warning("streak_service: failed to fetch payments for %s: %s", unit_number, e)
        return _empty()
    finally:
        if building_id and previous != building_id:
            set_ctx_building_id(previous)

    if not payments:
        return _empty()

    # Dedup by year+quarter — a confirmed payment means that quarter was paid on time
    seen: set = set()
    synthetic = []
    for p in payments:
        key = (str(p.get("year", "")), str(p.get("quarter", "")))
        if key not in seen and key != ("", ""):
            seen.add(key)
            synthetic.append({
                "year": p.get("year", ""),
                "quarter": p.get("quarter", ""),
                "status": "paid",
                "paid_on_time": True,
            })

    return _compute_streak(synthetic, source="levy_payments") if synthetic else _empty()


def _empty() -> dict:
    """Generated function header.

    Function: _empty
    Path: backend/services/streak_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "streak": 0,
        "total_quarters": 0,
        "on_time_count": 0,
        "on_time_pct": 0.0,
        "recent_quarters": [],
        "source": "no_data",
    }
