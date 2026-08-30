"""
GAP-FIN-006: Predictive Arrears + Hardship Flagging Service.

Rule-based risk scoring for levy payment default. Each unit receives a
0–100 risk score based on observable signals:

  Signal                          Weight
  ─────────────────────────────── ──────
  Current outstanding balance     30 pts  (scaled vs average levy)
  Days since last payment         25 pts  (>90 days = max)
  Payment miss rate (12-month)    25 pts  (% of quarters missed)
  Consecutive missed quarters     20 pts  (up to 4+)

Score bands:
  0–24   LOW      — standard dunning
  25–49  MODERATE — first-touch outreach
  50–74  HIGH     — escalated notice + Form 1 offer (GAP-JUR-NSW-002)
  75–100 CRITICAL — immediate escalation + legal referral trigger

This is a rule-based v1. An ML model can replace `score_unit()` later
without changing the router surface.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, date
from typing import Optional

logger = logging.getLogger(__name__)


# ── Risk scoring ──────────────────────────────────────────────────────────────

def _days_since(iso_date: Optional[str]) -> int:
    """Return days between iso_date and today. Returns 999 if no date."""
    if not iso_date:
        return 999
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except (ValueError, TypeError):
        return 999


def score_unit(
        outstanding_cents: int,
        average_quarterly_levy_cents: int,
        days_since_last_payment: int,
        missed_quarters_last_12m: int,
        consecutive_missed: int,
) -> dict:
    """Compute a 0–100 risk score for a single unit.

    Returns:
        {score, band, signals}  — signals is a list of human-readable factors
    """
    score = 0
    signals: list[str] = []

    # Signal 1 — outstanding balance relative to average quarterly levy
    if average_quarterly_levy_cents > 0:
        ratio = outstanding_cents / average_quarterly_levy_cents
        bal_pts = min(30, int(ratio * 15))  # 2× levy = 30 pts
        score += bal_pts
        if bal_pts >= 15:
            signals.append(f"Outstanding balance is {ratio:.1f}× the quarterly levy")

    # Signal 2 — days since last payment
    if days_since_last_payment >= 90:
        score += 25
        signals.append(f"No payment in {days_since_last_payment} days")
    elif days_since_last_payment >= 60:
        score += 15
        signals.append(f"No payment in {days_since_last_payment} days")
    elif days_since_last_payment >= 30:
        score += 7

    # Signal 3 — missed quarters in last 12 months (0–4 quarters)
    miss_pts = min(25, missed_quarters_last_12m * 8)
    score += miss_pts
    if missed_quarters_last_12m >= 2:
        signals.append(f"Missed {missed_quarters_last_12m} of last 4 quarters")

    # Signal 4 — consecutive missed quarters
    consec_pts = min(20, consecutive_missed * 6)
    score += consec_pts
    if consecutive_missed >= 2:
        signals.append(f"{consecutive_missed} consecutive missed quarters")

    score = min(100, score)

    if score >= 75:
        band = "CRITICAL"
    elif score >= 50:
        band = "HIGH"
    elif score >= 25:
        band = "MODERATE"
    else:
        band = "LOW"

    return {"score": score, "band": band, "signals": signals}


# ── Data aggregation helpers ──────────────────────────────────────────────────

async def compute_arrears_risk(db, building_id: str) -> list[dict]:
    """Compute risk scores for all units with outstanding levies.

    Returns list of {unit_number, outstanding_aud, score, band, signals, hardship_flag}
    sorted descending by score.
    """
    year = str(date.today().year)

    # Fetch current-year ledger (outstanding balances)
    ledger_docs = await db.unit_levy_ledger.find(
        {"building_id": building_id, "year": {"$regex": f"^{year}"}},
        {"unit_number": 1, "admin_balance": 1, "sinking_balance": 1, "total_levied": 1},
    ).to_list(500)

    if not ledger_docs:
        return []

    # Average quarterly levy across all units (proxy for "normal" levy)
    total_levied_sum = sum(
        (d.get("total_levied") or 0) for d in ledger_docs if d.get("total_levied")
    )
    avg_quarterly_cents = int((total_levied_sum / max(len(ledger_docs), 1)) * 100 / 4)

    # Fetch payment history per unit (last 12 months)
    twelve_months_ago = datetime.now(timezone.utc).replace(
        year=datetime.now(timezone.utc).year - 1
    ).isoformat()

    payment_docs = await db.levy_payments.find(
        {"building_id": building_id, "payment_date": {"$gte": twelve_months_ago}},
        {"unit_number": 1, "payment_date": 1, "amount": 1},
    ).to_list(5000)

    # Group payments by unit
    payments_by_unit: dict[str, list] = {}
    for p in payment_docs:
        unit = p.get("unit_number", "")
        payments_by_unit.setdefault(unit, []).append(p)

    results = []
    for doc in ledger_docs:
        unit = doc.get("unit_number", "")
        outstanding = round(
            (doc.get("admin_balance") or 0) + (doc.get("sinking_balance") or 0), 2
        )
        if outstanding <= 0:
            continue

        unit_payments = payments_by_unit.get(unit, [])

        # Days since last payment
        last_payment_date = None
        if unit_payments:
            try:
                last_payment_date = max(
                    p["payment_date"] for p in unit_payments if p.get("payment_date")
                )
            except (ValueError, KeyError):
                pass
        days_since = _days_since(last_payment_date)

        # Missed quarters — crude: count 90-day windows with no payment
        missed = 4 - len({
            (datetime.fromisoformat(p["payment_date"].replace("Z", "+00:00")).month - 1) // 3
            for p in unit_payments
            if p.get("payment_date")
        })
        missed = max(0, min(4, missed))

        # Consecutive — if no payment at all in 12 months, flag as 4.
        # Otherwise cap at missed (max 4) so the full 20-pt signal 4 is reachable.
        consecutive = 4 if not unit_payments else min(missed, 4)

        risk = score_unit(
            outstanding_cents=int(outstanding * 100),
            average_quarterly_levy_cents=avg_quarterly_cents,
            days_since_last_payment=days_since,
            missed_quarters_last_12m=missed,
            consecutive_missed=consecutive,
        )

        results.append({
            "unit_number": unit,
            "outstanding_aud": outstanding,
            "days_since_last_payment": days_since if days_since < 999 else None,
            "missed_quarters_last_12m": missed,
            **risk,
            # Auto-flag for hardship outreach (HIGH+ and >60 days overdue)
            "hardship_flag": risk["band"] in ("HIGH", "CRITICAL") and days_since >= 60,
            "form1_eligible": risk["band"] in ("HIGH", "CRITICAL"),  # Offer NSW Form 1
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results
