# @featuretrace:demo_bank — Strata Web balance-delta inference: candidate generation only.
# Layer: service
# Data flow: staging_strata_web_snapshots (two consecutive) → derive_strata_web_balance_delta_transactions()
#            → demo_bank_transactions (Mongo staging, source_type=strata_web_inferred_payment) (building-scoped).
# Related: backend/routers/demo_bank.py
#          backend/integrations/demo_bank/ingestion.py
#          backend/services/receivables_resolver.py
# Collection: staging_strata_web_snapshots, demo_bank_transactions, annual_levies, unit_levy_ledger
# Tests: tests/backend/test_strata_web_balance_inference.py
"""
Strata Web balance-delta inference — candidate generation only.

GAP-FIN-015 blocker 4: comparing consecutive Strata Web portal balance snapshots
can infer that a payment probably happened between them, but this is an INFERENCE,
not an observed bank event. This module writes candidate Demo Bank transactions
(tagged source_type="strata_web_inferred_payment", requires_review=True) for a
human/promotion-path decision — it NEVER writes to unit_levy_ledger directly. The
old (wrong) path derived `total_paid = total_levied - net_balance` and wrote it
straight into the ledger; this module replaces that with candidates that must
still pass through matching/promotion/review like any other input transaction.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from services.settings_service import get_unit_display_rules
from utils.finance_helpers import compute_period_installment_amounts, normalise_financial_year
from utils.unit_number import normalise_unit_token, resolve_canonical_unit_number


def _account_ref_for_building(building_id: str) -> str:
    # Strata Web portal balances are a combined admin+sinking total per unit, not
    # fund-split, so the inferred candidate is booked against the admin account —
    # the same simplification migration_026_synthesize_historical_levy_payments.py
    # avoids only because it has fund-split budget rates to work from; a portal
    # balance snapshot does not.
    """Generated function header.

    Function: _account_ref_for_building
    Path: backend/services/strata_web_balance_inference_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return f"ADMIN-{building_id}"


def _parse_snapshot_date(value: Any) -> Optional[date]:
    """Generated function header.

    Function: _parse_snapshot_date
    Path: backend/services/strata_web_balance_inference_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


async def _load_schedule_entries(db, building_id: str, financial_year: str) -> List[dict]:
    """Fetch + parse the building+year payment_schedule ONCE per run.

    Building+year, not per-unit — callers must fetch this outside their per-unit
    loop and pass the result into _charges_since(), otherwise an N-unit building
    re-fetches the identical annual_levies document N times.
    """
    levy_doc = await db.annual_levies.find_one(
        {"building_id": building_id, "year": financial_year}, {"_id": 0},
    )
    schedule_raw = (levy_doc or {}).get("payment_schedule")
    schedule_raw = schedule_raw if isinstance(schedule_raw, list) else []
    return sorted(
        (q for q in schedule_raw if isinstance(q, dict) and q.get("due_date")),
        key=lambda q: q["due_date"],
    )


async def _charges_since(
        db, building_id: str, unit_number: str, financial_year: str,
        window_start: date, window_end: date,
        schedule_entries: List[dict],
) -> int:
    """Sum the per-unit instalment amounts (cents) due in (window_start, window_end].

    ``schedule_entries`` is pre-fetched by the caller (see _load_schedule_entries) —
    it is a building+year value, not per-unit, so it must not be re-fetched here.
    unit_levy_ledger IS genuinely per-unit (different total_levied per unit) and is
    fetched fresh on every call.
    """
    if not schedule_entries:
        return 0

    ledger = await db.unit_levy_ledger.find_one(
        {"building_id": building_id, "unit_number": unit_number, "year": financial_year},
        {"_id": 0},
    )
    total_levied = float((ledger or {}).get("total_levied") or 0)
    period_amounts = compute_period_installment_amounts(total_levied, len(schedule_entries))

    total_cents = 0
    for entry, amount in zip(schedule_entries, period_amounts):
        due = _parse_snapshot_date(entry.get("due_date"))
        if due is not None and window_start < due <= window_end:
            total_cents += int(round(amount * 100))
    return total_cents


async def _interest_since(
        db, building_id: str, opening_arrears_cents: int,
        window_start: date, window_end: date,
        _rate_cache: Optional[Dict[str, dict]] = None,
) -> int:
    """Accrued statutory interest (cents) on the OPENING arrears balance over the window.

    Reuses services/arrears_interest_service.py — the jurisdiction-aware rate
    resolver and compounding-free simple-interest formula already used by the
    real arrears-interest posting path — rather than inventing a second interest
    calculation. Interest only accrues on a positive (owing) opening balance;
    a credit balance (opening_arrears_cents <= 0) never accrues interest.
    ``_rate_cache`` lets callers avoid resolving the same building's rate once
    per unit — the rate does not vary per unit, only per building.
    """
    from services.arrears_interest_service import compute_accrued_interest, get_effective_interest_rate

    if opening_arrears_cents <= 0:
        return 0

    if _rate_cache is not None and building_id in _rate_cache:
        rate_info = _rate_cache[building_id]
    else:
        rate_info = await get_effective_interest_rate(building_id, db)
        if _rate_cache is not None:
            _rate_cache[building_id] = rate_info

    interest = compute_accrued_interest(
        principal=opening_arrears_cents / 100,
        overdue_since=datetime.combine(window_start, datetime.min.time()),
        as_at=datetime.combine(window_end, datetime.min.time()),
        annual_rate_pct=rate_info["rate_pct"],
        max_rate_pct=rate_info["max_rate_pct"],
    )
    return int(round(interest * 100))


async def derive_strata_web_balance_delta_transactions(
        db,
        building_id: str,
        financial_year: str,
        current_snapshot_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Infer candidate payment transactions from consecutive Strata Web snapshots.

    Never writes to unit_levy_ledger. Writes candidates via
    integrations.demo_bank.ingestion._upsert_transaction(), tagged
    source_type="strata_web_inferred_payment", requires_review=True, so they still
    have to pass through matching/promotion/review like any other input.
    """
    from bson import ObjectId
    from integrations.demo_bank.ingestion import _upsert_transaction

    warnings: List[str] = []

    # Snapshot pairing is matched on the NORMALISED financial year, not the raw label
    # (2026-08-28). `staging_strata_web_snapshots.financial_year` stores whatever string
    # its caller passed, and East Gate alone holds "2025", "2026" and "2026-2027" side by
    # side. An exact string match meant two snapshots of the same actual year under
    # different labels never paired, so this function returned "no earlier snapshot" and
    # produced ZERO candidates — an entire scraper run silently reduced to a no-op.
    target_fy = normalise_financial_year(financial_year)
    candidates = await db.staging_strata_web_snapshots.find(
        {"building_id": building_id}
    ).to_list(None)
    snapshots = [
        s for s in candidates
        if normalise_financial_year(s.get("financial_year")) == target_fy
    ]
    snapshots.sort(key=lambda s: str(s.get("snapshot_date") or ""))

    if current_snapshot_id:
        wanted = ObjectId(current_snapshot_id)
        current = next((s for s in snapshots if s.get("_id") == wanted), None)
    else:
        current = snapshots[-1] if snapshots else None

    if not current:
        # Name the labels actually present — "no current snapshot found" against a
        # collection that visibly HAS snapshots is the confusing version of this.
        present = sorted({str(s.get("financial_year")) for s in candidates})
        return {
            "building_id": building_id, "financial_year": str(financial_year),
            "normalised_financial_year": target_fy,
            "candidates_created": 0, "candidates_skipped": 0,
            "warnings": [
                f"no snapshot for financial year {target_fy!r}; "
                f"labels present for this building: {present or 'none'}"
            ],
        }

    current_date = _parse_snapshot_date(current.get("snapshot_date"))
    earlier = [
        s for s in snapshots
        if str(s.get("snapshot_date") or "") < str(current.get("snapshot_date") or "")
    ]
    previous = earlier[-1] if earlier else None
    if not previous or current_date is None:
        return {
            "building_id": building_id, "financial_year": str(financial_year),
            "normalised_financial_year": target_fy,
            "candidates_created": 0, "candidates_skipped": 0,
            "snapshots_for_year": len(snapshots),
            "warnings": [
                "no earlier snapshot to compare against — a balance delta needs TWO "
                f"snapshots of financial year {target_fy!r}; this building has "
                f"{len(snapshots)}. Stage another portal snapshot, then re-run."
            ],
        }

    previous_date = _parse_snapshot_date(previous.get("snapshot_date"))
    if previous_date is None:
        return {
            "building_id": building_id, "financial_year": str(financial_year),
            "candidates_created": 0, "candidates_skipped": 0,
            "warnings": ["previous snapshot has no parseable snapshot_date"],
        }

    previous_by_unit = {
        normalise_unit_token(str(u.get("lot_number") or "")): u
        for u in (previous.get("per_unit_balances") or [])
        if u.get("lot_number")
    }

    created = 0
    skipped = 0
    account_ref = _account_ref_for_building(building_id)
    # UNIT IDENTITY IS RESOLVED IN EXACTLY ONE PLACE — utils.unit_number.
    #
    # A portal snapshot carries the plan LOT number ("71"). Every ledger collection is
    # keyed by the addressable UNIT number ("TH071"). The mapping between them is
    # per-building CONFIGURATION, not code: db.settings type="unit_display" holds
    # [{"prefix":"UA","min":1,"max":70,"pad":3},{"prefix":"TH","min":71,"max":87,"pad":3}].
    #
    # Do NOT build a lot->unit map here, or anywhere else. An earlier version of this
    # function did exactly that, from units.lot_number, and it was wrong twice over:
    # units.lot_number is "LOT71" not "71" so the map missed every lookup, and even
    # once fixed it was a second implementation of a rule that already has an owner.
    # `resolve_canonical_unit_number` probes the exact token first and only then
    # expands rule-driven candidates, which is what stops "UA087" resolving to
    # "TH087" — a real bug this module must not reintroduce.
    display_rules = await get_unit_display_rules(building_id)
    effective_date = datetime.combine(current_date, datetime.min.time(), tzinfo=timezone.utc)
    # Shared across all units in this run — the arrears interest rate is a
    # building-level (jurisdiction/config) value, not per-unit.
    interest_rate_cache: Dict[str, dict] = {}
    # Shared across all units — annual_levies.payment_schedule is a building+year
    # document, not per-unit; fetching it once here avoids re-querying it per unit.
    schedule_entries = await _load_schedule_entries(db, building_id, str(financial_year))

    for unit in current.get("per_unit_balances") or []:
        lot_number = str(unit.get("lot_number") or "")
        if not lot_number:
            continue
        token = normalise_unit_token(lot_number)
        # The reference every downstream consumer expects is the ADDRESSABLE unit number.
        # Prefer the snapshot's own (carried since 2026-08-28); otherwise resolve the
        # lot number through the one canonical helper.
        unit_ref = str(unit.get("unit_number") or "").strip() or await resolve_canonical_unit_number(
            db, building_id, lot_number, display_rules,
        )
        prev_unit = previous_by_unit.get(token)
        if prev_unit is None:
            skipped += 1
            continue

        previous_balance_cents = int(prev_unit.get("balance_cents") or 0)
        current_balance_cents = int(unit.get("balance_cents") or 0)
        charges_since_cents = await _charges_since(
            db, building_id, lot_number, str(financial_year), previous_date, current_date,
            schedule_entries,
        )
        interest_since_cents = await _interest_since(
            db, building_id, previous_balance_cents, previous_date, current_date,
            _rate_cache=interest_rate_cache,
        )
        # Doc formula: previous_balance + charges_raised_since + interest_or_penalties_raised - current_balance.
        inferred_amount_cents = (
            previous_balance_cents + charges_since_cents + interest_since_cents - current_balance_cents
        )

        if inferred_amount_cents <= 0:
            # Balance did not decrease more than known charges explain — either no
            # payment happened, or the increase is an unexplained/negative delta.
            # Doc requirement: only positive, explainable deltas become candidates.
            skipped += 1
            continue

        # High confidence when either signal is unambiguous:
        #   - the unit reached a $0 balance (fully reconciled — the strongest
        #     signal that the delta really is a payment, not noise), or
        #   - the delta is (near-)exactly the newly-accrued charges + interest for
        #     the window, i.e. a clean on-time payment with no other movement.
        # Anything else is a plausible but inexact partial/irregular payment.
        is_fully_reconciled = current_balance_cents == 0
        matches_new_charges = abs(inferred_amount_cents - (charges_since_cents + interest_since_cents)) <= 1
        confidence = "high" if (is_fully_reconciled or matches_new_charges) else "medium"

        await _upsert_transaction(
            db=db,
            building_id=building_id,
            account_ref=account_ref,
            source_type="strata_web_inferred_payment",
            source_batch_id=None,
            is_test_data=bool(current.get("is_test_data") or False),
            posted_date=effective_date,
            effective_date=effective_date,
            amount_cents=inferred_amount_cents,
            direction="credit",
            description=(
                f"Inferred payment from Strata Web balance delta — unit {unit_ref}"
            ),
            reference=None,
            payer_name=unit.get("owner_name"),
            payment_channel="strata_web_inferred",
            running_balance_cents=current_balance_cents,
            confidence=confidence,
            provenance_class="inferred_from_balance_delta",
            evidence_type="consecutive_snapshot_delta",
            requires_review=True,
            source_snapshot_ids=[str(previous.get("_id")), str(current.get("_id"))],
            date_basis="snapshot_date",
            unit_number=unit_ref,
        )
        created += 1

    return {
        "building_id": building_id,
        "financial_year": str(financial_year),
        "candidates_created": created,
        "candidates_skipped": skipped,
        "warnings": warnings,
    }
