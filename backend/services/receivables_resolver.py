# @featuretrace:demo_bank — Period-aware AR/receivable resolver for bank-feed matching.
# Layer: service
# Data flow: bank_feeds._load_lot_candidates() → get_open_receivable_for_unit()
#            → annual_levies.payment_schedule + unit_levy_ledger (building-scoped).
# Related: backend/routers/bank_feeds.py
#          backend/utils/finance_helpers.py
# Collection: annual_levies, unit_levy_ledger
# Tests: tests/backend/test_receivables_resolver.py
"""
Period-aware AR/receivable resolver.

Replaces the historical bank-feed matching-candidate logic that used
``open_levy_cents = abs(latest unit_levy_ledger.net_balance)`` — a single
annual figure that can never match a historical or reconstructed transaction
once the current-year balance has been paid down to zero (GAP-FIN-015).

This module does NOT make bank/input data a source of truth. It gives the
matching layer the *ledger's own* period-specific receivable so a historical
payment can be compared against what was actually owed at that time, instead
of what is owed today.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from database import db
from utils.finance_helpers import (
    compute_mongo_quarter_statuses,
    compute_period_installment_amounts,
)


def _parse_iso_date(value: Any) -> Optional[date]:
    """Generated function header.

    Function: _parse_iso_date
    Path: backend/services/receivables_resolver.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _result(
        *,
        unit_number: str,
        financial_year: Optional[str],
        period: Optional[str],
        open_receivable_cents: int,
        source: str,
        confidence: str,
        fallback_used: bool,
        warnings: List[str],
        due_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Generated function header.

    Function: _result
    Path: backend/services/receivables_resolver.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "unit_number": unit_number,
        "financial_year": financial_year,
        "period": period,
        "due_date": due_date,
        "open_receivable_cents": max(0, int(open_receivable_cents)),
        "source": source,
        "confidence": confidence,
        "fallback_used": fallback_used,
        "warnings": warnings,
    }


async def get_open_receivable_for_unit(
        building_id: str,
        unit_number: str,
        as_of_date: Optional[date] = None,
        financial_year: Optional[str] = None,
        amount_cents: Optional[int] = None,
        _levy_doc_cache: Optional[Dict[str, Optional[dict]]] = None,
) -> Dict[str, Any]:
    """Resolve the amount owed by a unit for a specific period.

    Resolution order (first tier that can be computed wins):
      1. Levy instalment schedule for the relevant period — matched by
         ``amount_cents`` (exact) or bracketed by ``as_of_date``.
         source="levy_installment_schedule".
      2. No amount/date hint: the current/overdue-and-unpaid period.
         source="current_period_due".
      3. No payment_schedule for the target year: that year's
         unit_levy_ledger.net_balance. source="ledger_year_balance".
      4. No ledger row for the target year either: latest-year
         unit_levy_ledger.net_balance (today's pre-refactor behaviour).
         source="ledger_net_balance_fallback".

    Note: ``amount_cents`` is accepted per the documented contract (and exercised
    directly by callers/tests that pass it), but the only production caller today
    — bank_feeds._load_lot_candidates() — passes only ``as_of_date``, so tier 1a
    is not yet reachable via the live matching pipeline. It remains available for
    a future caller that already knows the transaction amount up front (e.g. a
    deterministic-reconstruction candidate builder).

    ``_levy_doc_cache`` is an optional caller-owned dict (keyed by year string)
    used to memoize the ``annual_levies`` lookup across repeated calls for the
    SAME building — that document does not vary per unit, only per building+year,
    but this function is called once per unit by
    bank_feeds._load_lot_candidates(). Without the cache, building a candidate
    list for an N-unit building would re-fetch the identical annual_levies
    document N times. unit_levy_ledger is genuinely per-unit and is not cached.
    """
    warnings: List[str] = []
    guessed_year = str(as_of_date.year) if as_of_date and not financial_year else None
    year = financial_year or guessed_year

    if not year:
        from routers.finance import _resolve_default_levy_year
        year = await _resolve_default_levy_year(building_id)

    async def _get_levy_doc(y: str) -> Optional[dict]:
        """Generated function header.

        Function: _get_levy_doc
        Path: backend/services/receivables_resolver.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if _levy_doc_cache is not None and y in _levy_doc_cache:
            return _levy_doc_cache[y]
        doc = await db.annual_levies.find_one({"building_id": building_id, "year": y}, {"_id": 0})
        if _levy_doc_cache is not None:
            _levy_doc_cache[y] = doc
        return doc

    async def _load_for_year(y: str) -> tuple[Optional[dict], Optional[dict]]:
        """Generated function header.

        Function: _load_for_year
        Path: backend/services/receivables_resolver.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        ledger_doc = await db.unit_levy_ledger.find_one(
            {"building_id": building_id, "unit_number": unit_number, "year": y},
            {"_id": 0},
        )
        annual_doc = await _get_levy_doc(y)
        return ledger_doc, annual_doc

    ledger = None
    levy_doc = None
    if year:
        ledger, levy_doc = await _load_for_year(year)

        # guessed_year assumes financial_year == calendar year of as_of_date, which
        # does not hold for buildings whose FY does not start in January
        # (settings.financial_year_start_month). If neither collection has data
        # for that guess, retry the prior calendar year once before falling
        # through to the ledger-balance tiers — this recovers the common case of
        # an early-calendar-year transaction that actually belongs to the
        # previous FY's final quarter.
        if guessed_year and ledger is None and levy_doc is None:
            retry_year = str(int(guessed_year) - 1)
            retry_ledger, retry_levy_doc = await _load_for_year(retry_year)
            if retry_ledger is not None or retry_levy_doc is not None:
                year, ledger, levy_doc = retry_year, retry_ledger, retry_levy_doc
                warnings.append(
                    f"calendar-year guess {guessed_year!r} had no data; used {retry_year!r} "
                    "instead — verify this building's financial_year_start_month if this "
                    "recurs, as this heuristic assumes a January or later FY start."
                )

    raw_schedule = (levy_doc or {}).get("payment_schedule")
    raw_schedule = raw_schedule if isinstance(raw_schedule, list) else []
    schedule_entries = sorted(
        (q for q in raw_schedule if isinstance(q, dict) and q.get("due_date")),
        key=lambda q: q["due_date"],
    )

    if schedule_entries and ledger is not None:
        total_levied = float(ledger.get("total_levied") or 0)
        period_amounts = compute_period_installment_amounts(total_levied, len(schedule_entries))
        periods = [
            {
                "quarter": entry.get("quarter"),
                "due_date": entry.get("due_date"),
                "amount_cents": int(round(amt * 100)),
            }
            for entry, amt in zip(schedule_entries, period_amounts)
        ]

        # Tier 1a: exact amount match pins down which period the transaction paid.
        if amount_cents is not None:
            for period in periods:
                if abs(period["amount_cents"] - amount_cents) <= 1:
                    return _result(
                        unit_number=unit_number, financial_year=year,
                        period=period["quarter"], open_receivable_cents=period["amount_cents"],
                        source="levy_installment_schedule", confidence="high",
                        fallback_used=False, warnings=warnings, due_date=period["due_date"],
                    )
            warnings.append("amount_cents did not match any scheduled period amount")

        # Tier 1b: bracket by as_of_date — the period that was "current" then.
        if as_of_date is not None:
            on_or_before = [
                p for p in periods
                if (parsed := _parse_iso_date(p["due_date"])) is not None and parsed <= as_of_date
            ]
            chosen = on_or_before[-1] if on_or_before else periods[0]
            return _result(
                unit_number=unit_number, financial_year=year,
                period=chosen["quarter"], open_receivable_cents=chosen["amount_cents"],
                source="levy_installment_schedule", confidence="medium",
                fallback_used=False, warnings=warnings, due_date=chosen["due_date"],
            )

        # Tier 2: no amount/date hint — current/overdue-and-unpaid period.
        # ledger.total_paid is NOT reliably scoped to this year (confirmed live 2026-08-01: it
        # can be back-solved from a portal balance snapshot, i.e. cumulative payment history
        # through the scrape date, not payments received this year specifically -- see
        # routers/finance.py's _get_unit_dashboard_overview_mongo_fallback for the full live
        # evidence). Waterfalling that uncapped, potentially many-years-cumulative figure across
        # a single year's quarters would mark all of them "paid" regardless of what was actually
        # paid toward THIS year's charges, silently finding no overdue/unpaid/partial period to
        # match an incoming payment against even when one genuinely exists. total_levied -
        # net_balance is the correctly year-scoped equivalent (same derivation used throughout
        # routers/finance.py's 2026-08-01 fixes).
        net_balance = float(ledger.get("net_balance") or (total_levied - float(ledger.get("total_paid") or 0)))
        paid_this_year = total_levied - net_balance
        quarters = compute_mongo_quarter_statuses(raw_schedule, total_levied, paid_this_year)
        due_quarter = next(
            (q for q in quarters if q["status"] in {"overdue", "unpaid", "partial"}), None
        )
        if due_quarter:
            return _result(
                unit_number=unit_number, financial_year=year,
                period=due_quarter["quarter"],
                open_receivable_cents=int(round(due_quarter["outstanding"] * 100)),
                source="current_period_due", confidence="medium",
                fallback_used=False, warnings=warnings, due_date=due_quarter["due_date"],
            )

    # Tier 3: ledger row exists for the target year but no usable schedule —
    # use THAT year's net_balance, not the latest year's.
    if ledger is not None:
        net_balance = float(ledger.get("net_balance") or 0)
        warnings.append("no payment_schedule available for this year; using year net_balance")
        return _result(
            unit_number=unit_number, financial_year=year, period=None,
            open_receivable_cents=int(round(abs(net_balance) * 100)),
            source="ledger_year_balance", confidence="low",
            fallback_used=True, warnings=warnings,
        )

    # Tier 4: last resort — latest-year ledger row (today's pre-refactor behaviour).
    latest = await db.unit_levy_ledger.find_one(
        {"building_id": building_id, "unit_number": unit_number},
        sort=[("year", -1)],
    )
    net_balance = float((latest or {}).get("net_balance") or 0)
    warnings.append("no ledger row for target year; fell back to latest-year net_balance")
    return _result(
        unit_number=unit_number, financial_year=(latest or {}).get("year") or year, period=None,
        open_receivable_cents=int(round(abs(net_balance) * 100)),
        source="ledger_net_balance_fallback", confidence="low",
        fallback_used=True, warnings=warnings,
    )
