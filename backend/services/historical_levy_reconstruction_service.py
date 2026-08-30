# @featuretrace:demo_bank — Historical levy-payment reconstruction: synthesize pre-migration years.
# Layer: service
# Data flow: annual_levies + units (Mongo) → synthesize_historical_levy_payments()
#            → demo_bank_transactions (Mongo staging, source_type=synthetic_from_budget) (building-scoped).
# Related: backend/scripts/migrations/migration_026_synthesize_historical_levy_payments.py (thin CLI wrapper)
#          backend/routers/financial_matching.py (POST /financial/matching/historical-reconstruction/run,
#          _PROMOTABLE_SOURCE_TYPES/promote_high_confidence_reconstruction — synthetic_from_budget is
#          deliberately excluded from deterministic promotion)
#          backend/services/strata_web_balance_inference_service.py (sibling inference service)
# Collection: annual_levies, units, demo_bank_transactions
# Tests: tests/backend/test_historical_levy_reconstruction_service.py
"""
Historical levy-payment reconstruction — building-agnostic, idempotent service.

GAP-FIN-015 criterion 6: onboarding a building whose payment history predates its
StrataOS migration leaves a gap where `annual_levies` budget documents exist but no
real payment records do. This module reconstructs ONE synthetic levy payment per
unit per scheduled instalment for the requested range, from:

    amount = unit_uoe × instalment_rate_per_uoe   (per fund: admin, sinking)

The schedule can be quarterly, half-yearly, annual, or mixed inside a year. A
payment_schedule entry may carry explicit admin/sinking per-UOE rates; otherwise
the service derives the rate from annual per-UOE values and the entry's
period_fraction.

Every transaction is tagged `source_type="synthetic_from_budget"`, `is_synthetic=True`
— honest about being a reconstruction, not a real observed payment. These rows are
not eligible for deterministic high-confidence promotion; they must stay reviewable
because they are budget-derived assumptions rather than observed bank movements.

This logic previously lived only in
`scripts/migrations/migration_026_synthesize_historical_levy_payments.py` as a
one-off script with its own private Motor connection. The underlying logic was
already building-agnostic (`building_id` was always a plain parameter) and already
idempotent (SHA-256 idempotency key + `$setOnInsert` upsert) — this module makes it
importable/triggerable from application code (a router endpoint, an admin script,
or another service) via the app's own shared `db` handle, instead of requiring shell
access to a one-off script.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROVIDER = "demo_bank_feed"

# Fallback quarter due dates only used when payment_schedule is absent.
_QUARTER_DATES_FALLBACK = [
    ("Q1", 3, 31, 0.25),
    ("Q2", 6, 30, 0.25),
    ("Q3", 9, 30, 0.25),
    ("Q4", 12, 31, 0.25),
]


def _account_refs(building_id: str) -> Tuple[str, str]:
    """Generated function header.

    Function: _account_refs
    Path: backend/services/historical_levy_reconstruction_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return f"ADMIN-{building_id}", f"SINKING-{building_id}"


def _strata_year_from_levy(levy_year: int) -> str:
    """Strata year is the levy document's year, regardless of when payment falls."""
    return str(levy_year)


def _idempotency_key(building_id: str, account_ref: str, payment_date: str,
                      unit_number: str, quarter: str, year: int) -> str:
    """Generated function header.

    Function: _idempotency_key
    Path: backend/services/historical_levy_reconstruction_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw = f"synthetic|{building_id}|{account_ref}|{payment_date}|{unit_number}|{quarter}|{year}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _safe_float(value: Any) -> Optional[float]:
    """Parse optional numeric fields without converting blanks to zero."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "")
        if value == "":
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_numeric(*values: Any) -> Optional[float]:
    """Return the first parseable value, preserving explicit 0.0 rates."""
    for value in values:
        parsed = _safe_float(value)
        if parsed is not None:
            return parsed
    return None


def _period_fraction_from_label(label: str) -> Optional[float]:
    lower = label.lower()
    if lower.startswith(("h1", "h2", "half")) or "half" in lower:
        return 0.5
    if lower.startswith("annual") or lower in {"year", "yearly"}:
        return 1.0
    if lower.startswith("q"):
        return 0.25
    return None


def _normalise_payment_schedule(
    doc: dict,
    year: int,
    admin_annual: Optional[float],
    sinking_annual: Optional[float],
) -> List[dict]:
    """Return dated instalments with per-fund rates per UOE.

    Accepted entry fields:
    - period, quarter, label, or name: identity/display label.
    - due_date: ISO date.
    - period_fraction: 0.5 half-yearly, 0.25 quarterly, 1.0 annual.
    - admin_levy_per_uoe / sinking_levy_per_uoe: explicit period rates.
    - *_levy_per_uoe_quarterly: legacy alias for existing quarterly schedules.

    Missing explicit rates are derived from annual per-UOE rates and the
    period_fraction. Missing fractions are inferred from the label, then from an
    even split across the supplied schedule entries.
    """
    raw_schedule = doc.get("payment_schedule")
    if isinstance(raw_schedule, str):
        lower = raw_schedule.lower()
        if "half" in lower or "biannual" in lower or "semi" in lower:
            raw_schedule = [
                {"period": "H1", "due_date": f"{year}-06-30", "period_fraction": 0.5},
                {"period": "H2", "due_date": f"{year}-12-31", "period_fraction": 0.5},
            ]
        elif "annual" in lower:
            raw_schedule = [
                {"period": "Annual", "due_date": f"{year}-12-31", "period_fraction": 1.0},
            ]
        else:
            raw_schedule = None

    if not raw_schedule:
        raw_schedule = [
            {"period": label, "due_date": date(year, month, day).isoformat(), "period_fraction": fraction}
            for label, month, day, fraction in _QUARTER_DATES_FALLBACK
        ]

    dated_entries: List[tuple[dict, str, date]] = []
    for idx, entry in enumerate(raw_schedule, start=1):
        if not isinstance(entry, dict):
            continue
        label = str(
            entry.get("period")
            or entry.get("quarter")
            or entry.get("label")
            or entry.get("name")
            or f"P{idx}"
        )
        try:
            due_date = date.fromisoformat(str(entry.get("due_date"))[:10])
        except (ValueError, TypeError):
            continue
        dated_entries.append((entry, label, due_date))

    if not dated_entries:
        return []

    even_fraction = 1.0 / len(dated_entries)
    schedule: List[dict] = []
    for entry, label, due_date in dated_entries:
        fraction = _safe_float(entry.get("period_fraction"))
        if fraction is None:
            fraction = _period_fraction_from_label(label)
        if fraction is None:
            fraction = even_fraction

        admin_rate = _first_numeric(
            entry.get("admin_levy_per_uoe"),
            entry.get("admin_levy_per_uoe_period"),
            entry.get("admin_levy_per_uoe_quarterly"),
        )
        sinking_rate = _first_numeric(
            entry.get("sinking_levy_per_uoe"),
            entry.get("sinking_levy_per_uoe_period"),
            entry.get("sinking_levy_per_uoe_quarterly"),
        )
        if admin_rate is None and admin_annual is not None:
            admin_rate = admin_annual * fraction
        if sinking_rate is None and sinking_annual is not None:
            sinking_rate = sinking_annual * fraction

        schedule.append({
            "label": label,
            "due_date": due_date,
            "period_fraction": fraction,
            "admin_rate": admin_rate,
            "sinking_rate": sinking_rate,
        })

    return schedule


async def _fetch_annual_levies(db, building_id: str) -> Dict[int, dict]:
    """Return {year_int: levy_doc} for all annual_levies for this building."""
    cursor = db.annual_levies.find(
        {"building_id": building_id},
        {
            "year": 1,
            "admin_levy_per_uoe_quarterly": 1,
            "sinking_levy_per_uoe_quarterly": 1,
            "admin_levy_per_uoe_annual": 1,
            "sinking_levy_per_uoe_annual": 1,
            "total_uoe": 1,
            "payment_schedule": 1,
        },
    )
    docs = await cursor.to_list(None)
    result: Dict[int, dict] = {}
    for doc in docs:
        yr_raw = doc.get("year")
        try:
            yr = int(str(yr_raw).split("-")[0])
        except (ValueError, TypeError):
            continue

        admin_annual = _safe_float(doc.get("admin_levy_per_uoe_annual"))
        sinking_annual = _safe_float(doc.get("sinking_levy_per_uoe_annual"))
        admin_quarterly = _safe_float(doc.get("admin_levy_per_uoe_quarterly"))
        sinking_quarterly = _safe_float(doc.get("sinking_levy_per_uoe_quarterly"))
        if admin_annual is None and admin_quarterly is not None:
            admin_annual = admin_quarterly * 4
        if sinking_annual is None and sinking_quarterly is not None:
            sinking_annual = sinking_quarterly * 4
        schedule = _normalise_payment_schedule(doc, yr, admin_annual, sinking_annual)

        result[yr] = {
            "year": yr,
            "admin_annual": admin_annual,
            "sinking_annual": sinking_annual,
            "total_uoe": doc.get("total_uoe"),
            "schedule": schedule,
        }
    return result


async def _fetch_units(db, building_id: str) -> List[dict]:
    """Return all active units with their UOE value."""
    cursor = db.units.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}},
        {"unit_number": 1, "lot_number": 1, "entitlement_units": 1, "uoe": 1, "entitlement": 1},
    )
    docs = await cursor.to_list(None)
    units = []
    for doc in docs:
        unit_number = str(doc.get("unit_number") or doc.get("lot_number") or "").strip()
        if not unit_number:
            continue
        # East Gate stores UOE in 'entitlement'; support all known aliases.
        uoe_raw = (
            doc.get("entitlement_units")
            or doc.get("uoe")
            or doc.get("entitlement")
        )
        try:
            uoe = float(uoe_raw)
        except (ValueError, TypeError):
            logger.warning("Unit %s has no UOE — skipping", unit_number)
            continue
        if uoe <= 0:
            logger.warning("Unit %s has zero UOE — skipping", unit_number)
            continue
        units.append({"unit_number": unit_number, "uoe": uoe})
    return units


async def _upsert_transaction(
        db,
        *,
        building_id: str,
        account_ref: str,
        payment_date: date,
        amount_cents: int,
        description: str,
        unit_number: str,
        strata_year: str,
        quarter: str,
        levy_year: int,
        dry_run: bool,
) -> Tuple[bool, bool]:
    """Upsert one synthetic transaction. Returns (was_inserted, was_duplicate)."""
    date_str = payment_date.isoformat()
    idem_key = _idempotency_key(
        building_id, account_ref, date_str, unit_number, quarter, levy_year
    )
    now = datetime.now(timezone.utc).isoformat()

    doc = {
        "building_id": building_id,
        "provider": _PROVIDER,
        "account_ref": account_ref,
        "external_transaction_id": idem_key,
        "idempotency_key": idem_key,
        "posted_date": date_str,
        "amount_cents": amount_cents,
        "direction": "credit",
        "description": description,
        "reference": f"SYNTHETIC-{levy_year}-{quarter}-{unit_number}",
        "source_type": "synthetic_from_budget",
        "source_id": f"annual_levies:{levy_year}",
        "unit_number": unit_number,
        "strata_year": strata_year,
        "levy_quarter": quarter,
        "levy_year": str(levy_year),
        "sync_status": "pending",
        "is_synthetic": True,
        "is_test_data": False,
        "created_at": now,
        "updated_at": now,
    }

    if dry_run:
        return True, False

    result = await db.demo_bank_transactions.update_one(
        {"building_id": building_id, "idempotency_key": idem_key},
        {"$setOnInsert": doc},
        upsert=True,
    )
    inserted = result.upserted_id is not None
    return inserted, not inserted


async def synthesize_historical_levy_payments(
        db,
        *,
        building_id: str,
        from_year: int,
        to_year: int,
        dry_run: bool = False,
) -> Dict[str, Any]:
    """Reconstruct synthetic levy payments for `building_id` across [from_year, to_year].

    Building-agnostic and idempotent: safe to re-run for the same or a different
    building — re-running for years already synthesized produces `already_existed`
    counts, never duplicate `demo_bank_transactions` rows.
    """
    logger.info(
        "Synthesising levy payments: building=%s years=%s-%s dry_run=%s",
        building_id, from_year, to_year, dry_run,
    )

    levies = await _fetch_annual_levies(db, building_id)
    if not levies:
        raise ValueError(
            f"No annual_levies documents found for building_id={building_id!r}. "
            "Cannot synthesise without budget data."
        )
    logger.info("Found annual_levies for years: %s", sorted(levies.keys()))

    units = await _fetch_units(db, building_id)
    if not units:
        raise ValueError(f"No units found for building_id={building_id!r}.")
    logger.info("Found %d units", len(units))

    admin_ref, sinking_ref = _account_refs(building_id)

    inserted = 0
    updated = 0
    skipped = 0
    by_year: Dict[str, int] = {}

    for year in range(from_year, to_year + 1):
        levy = levies.get(year)
        if levy is None:
            # Forward-fill from the nearest prior year as an estimate.
            for fallback_yr in range(year - 1, year - 4, -1):
                levy = levies.get(fallback_yr)
                if levy:
                    logger.warning(
                        "Year %d has no annual_levies — using %d rates as estimate",
                        year, fallback_yr,
                    )
                    break
        if levy is None:
            logger.warning("Year %d: no levy rates found anywhere — skipping", year)
            skipped += 1
            continue

        if not levy["schedule"]:
            logger.warning("Year %d: payment schedule is empty — skipping", year)
            skipped += 1
            continue

        if levy["admin_annual"] is None and levy["sinking_annual"] is None:
            logger.warning("Year %d: levy rates are null — skipping", year)
            skipped += 1
            continue

        # Use actual due dates from payment_schedule; drop future dates (partial current year).
        today = date.today()
        instalments = [
            item
            for item in levy["schedule"]
            if item["due_date"] <= today
        ]

        if not instalments:
            logger.warning("Year %d: all instalments are in the future — skipping", year)
            skipped += 1
            continue

        # strata_year is the levy doc's year, not derived from payment date — a
        # 2025-Q1 payment due 2024-10-01 still belongs to strata year 2025.
        strata_yr = _strata_year_from_levy(year)

        logger.info(
            "Year %d: admin_annual=%.4f sinking_annual=%s instalments=%d strata_year=%s",
            year, levy["admin_annual"] or 0, levy["sinking_annual"] or 0, len(instalments), strata_yr,
        )

        for unit in units:
            unit_number = unit["unit_number"]
            uoe = unit["uoe"]

            for item in instalments:
                period_label = item["label"]
                due_date = item["due_date"]
                admin_rate = item["admin_rate"]
                sinking_rate = item["sinking_rate"]

                if admin_rate:
                    admin_cents = int(round(uoe * admin_rate * 100))
                    desc = (
                        f"Levy {period_label} {year} Admin Fund - "
                        f"Unit {unit_number} ({uoe} UOE x ${admin_rate:.4f}/UOE)"
                    )
                    was_in, was_dup = await _upsert_transaction(
                        db,
                        building_id=building_id,
                        account_ref=admin_ref,
                        payment_date=due_date,
                        amount_cents=admin_cents,
                        description=desc,
                        unit_number=unit_number,
                        strata_year=strata_yr,
                        quarter=period_label,
                        levy_year=year,
                        dry_run=dry_run,
                    )
                    if was_in:
                        inserted += 1
                        by_year[strata_yr] = by_year.get(strata_yr, 0) + 1
                    elif was_dup:
                        updated += 1

                if sinking_rate:
                    sinking_cents = int(round(uoe * sinking_rate * 100))
                    desc = (
                        f"Levy {period_label} {year} Sinking Fund - "
                        f"Unit {unit_number} ({uoe} UOE x ${sinking_rate:.4f}/UOE)"
                    )
                    was_in, was_dup = await _upsert_transaction(
                        db,
                        building_id=building_id,
                        account_ref=sinking_ref,
                        payment_date=due_date,
                        amount_cents=sinking_cents,
                        description=desc,
                        unit_number=unit_number,
                        strata_year=strata_yr,
                        quarter=period_label,
                        levy_year=year,
                        dry_run=dry_run,
                    )
                    if was_in:
                        inserted += 1
                        by_year[strata_yr] = by_year.get(strata_yr, 0) + 1
                    elif was_dup:
                        updated += 1

    result = {
        "building_id": building_id,
        "dry_run": dry_run,
        "years_range": f"{from_year}-{to_year}",
        "units_processed": len(units),
        "inserted": inserted,
        "already_existed": updated,
        "years_skipped": skipped,
        "by_strata_year": dict(sorted(by_year.items())),
    }
    logger.info("Synthesis complete: %s", result)
    return result
