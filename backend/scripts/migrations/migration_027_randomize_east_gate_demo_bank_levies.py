"""
Create randomized Demo Bank levy-payment staging rows for historical onboarding.

# @featuretrace:demo_bank — proposed levy + GST reconstruction into Demo Bank staging.
# Layer: migration
# Data flow: annual_levies + units + settings → demo_bank_transactions
#            (Mongo staging, source_type=synthetic_from_budget, building-scoped)
# Related: backend/scripts/migrations/migration_023_demo_bank_indexes.py
#          backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py
#          backend/scripts/migrations/migration_026_synthesize_historical_levy_payments.py
#          backend/services/gst_service.py
# Collection: annual_levies, units, settings, demo_bank_transactions, demo_bank_accounts

This script intentionally supersedes the simpler historical reconstruction in
`migration_026_synthesize_historical_levy_payments.py` for buildings where the
onboarding source is a historical levy/budget pack rather than observed bank
transactions. It models owner-payable bank movements, not ledger postings.

Amount contract
---------------
`annual_levies.proposed_admin_expenses` and
`annual_levies.proposed_sinking_expenses` are ex-GST AGM/budget totals. Demo
Bank transactions represent money received from owners, so this script adds the
building's levy GST settings and stores:

    amount_cents         = owner-payable amount, GST inclusive
    amount_ex_gst_cents  = ex-GST component
    gst_cents            = GST component

Rows are allocated by UOE across all active units and all four quarters.
Residual cents are distributed deterministically by largest remainder so each
fund/year sums exactly to the intended annual GST-inclusive total.

Timing contract
---------------
Payment dates are deterministic pseudo-random dates derived from
building/year/unit/quarter. This gives realistic variation without making each
rerun rewrite history. Payment patterns include regular quarterly payments,
annual and half-yearly lump sums, late payments, and arrears catch-up payments.

Known source-data caveats
-------------------------
* 2021 has no sinking levy in `annual_levies`; the script does not create
  zero-dollar bank rows.
* Some historical source payment_schedule records contain fewer than four
  quarters. The requested Demo Bank model needs all four owner-payment quarters,
  so missing quarter due dates fall back to quarter-end dates for that levy year.
* Special levy is not folded into ordinary Admin/Sinking rows. Special levy
  backfill should be a separate, explicit import when source amounts exist.
* If a Demo Bank row has a stale `finance_bank_transaction_ref` that does not
  exist in the connected Postgres database, `--repair-stale-sync` resets only
  the UI-facing sync cache fields to pending. It does not delete or rewrite any
  Postgres ledger/canonical bank transaction.
* If a Demo Bank row is already linked to an existing Postgres bank transaction,
  the script compares amount/date/description parity. It updates the Postgres
  bank transaction only when no downstream `finance.receipts` row references
  that bank transaction. Once receipts exist, the row is treated as ledger-locked
  and is left unchanged.

Run:
  python3 backend/scripts/migrations/migration_027_randomize_east_gate_demo_bank_levies.py --dry-run
  python3 backend/scripts/migrations/migration_027_randomize_east_gate_demo_bank_levies.py --repair-stale-sync
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import random
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import asyncpg
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

_HERE = Path(__file__).resolve()
_BACKEND = _HERE.parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from services.gst_service import parse_levy_gst_settings

load_dotenv(_BACKEND / ".env")

PROVIDER = "demo_bank_feed"
SOURCE_TYPE = "synthetic_from_budget"
DEFAULT_BUILDING_ID = "13195"
QUARTERS = ("Q1", "Q2", "Q3", "Q4")
FALLBACK_DUE_DATES = {
    "Q1": (3, 31),
    "Q2": (6, 30),
    "Q3": (9, 30),
    "Q4": (12, 31),
}

# Building-agnostic levy-frequency support (GAP: buildings can change billing frequency
# mid-history — confirmed real via docs/finances/UP13195_historical_levy_history_2021_2026.csv,
# which shows East Gate itself billed half-yearly (H1/H2) in 2021-2022 before switching to
# quarterly (Q1-Q4) from 2023). Deliberately ADDITIVE, not a modification of QUARTERS/
# _due_dates()/_payment_pattern()/_payment_date() above — those are relied on unchanged by
# levy_generation_service.py and budget_levy_generator.py's existing (quarterly) behaviour, and
# _payment_date()'s pattern simulation hardcodes quarter names ("Q1"/"Q3") in a way that doesn't
# generalise cleanly. Non-quarterly frequencies use the simpler functions below instead; the
# quarterly (default) path is untouched and byte-identical to before this was added.
LEVY_FREQUENCIES: dict[str, int] = {"monthly": 12, "quarterly": 4, "half_yearly": 2, "annual": 1}


def _periods_for_frequency(frequency: str) -> tuple[str, ...]:
    """Returns this frequency's period labels for one levy year. Unrecognised frequency values
    fall back to quarterly — the pre-existing, only-ever-supported behaviour."""
    n = LEVY_FREQUENCIES.get((frequency or "").strip().lower(), 4)
    if n == 12:
        return tuple(f"M{i}" for i in range(1, 13))
    if n == 2:
        return ("H1", "H2")
    if n == 1:
        return ("Y1",)
    return QUARTERS


def _due_dates_for_periods(levy_year: int, periods: tuple[str, ...]) -> dict[str, date]:
    """Evenly-spaced period-end due dates across the levy year — the generic equivalent of
    FALLBACK_DUE_DATES for a non-quarterly frequency. Does not support a payment_schedule
    override (an East-Gate-specific quarterly concept in _due_dates() above); every non-quarterly
    building uses these computed dates directly."""
    n = len(periods)
    due: dict[str, date] = {}
    for i, period in enumerate(periods, start=1):
        month = (i * 12) // n
        # Last day of `month` in `levy_year`, via first-day-of-next-month minus one day.
        if month == 12:
            next_month_first = date(levy_year + 1, 1, 1)
        else:
            next_month_first = date(levy_year, month + 1, 1)
        due[period] = next_month_first - timedelta(days=1)
    return due


def _payment_date_for_period(
    *, building_id: str, unit_number: str, levy_year: int, period: str,
    due_dates: dict[str, date], max_date: date,
) -> tuple[date, int]:
    """Simpler deterministic-pseudo-random payment date for a non-quarterly period — a small
    +/- lag around the period's own due date, no multi-pattern simulation (unlike
    _payment_date()'s quarterly-specific annual/half-yearly-lump-sum/late/arrears patterns,
    which assume quarter period names). Building/unit/year/period-derived, so reruns are
    stable, matching the existing determinism contract."""
    rng = _rng("date-v1-period-generic", building_id, unit_number, levy_year, period)
    due = due_dates[period]
    lag_days = rng.randint(-10, 15)
    paid = due + timedelta(days=lag_days)
    if paid > max_date:
        paid = max_date
    return paid, lag_days


@dataclass(frozen=True)
class UnitShare:
    unit_number: str
    uoe: float


@dataclass(frozen=True)
class FundModel:
    fund: str
    account_ref: str
    label: str
    annual_ex_gst_cents: int
    annual_inc_gst_cents: int
    basis: str


@dataclass
class PgParityStats:
    checked: int = 0
    invalid_refs: int = 0
    missing_pg_rows: int = 0
    parity_ok: int = 0
    pg_updated: int = 0
    reset_to_pending: int = 0
    locked_with_receipts: int = 0
    pg_errors: int = 0


def _uuid_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        return None


def _split_uuid_refs(docs: list[dict[str, Any]]) -> tuple[list[str], list[Any]]:
    valid_refs: list[str] = []
    invalid_ids: list[Any] = []
    for doc in docs:
        ref = _uuid_or_none(doc.get("finance_bank_transaction_ref"))
        if ref:
            valid_refs.append(ref)
        else:
            invalid_ids.append(doc["_id"])
    return valid_refs, invalid_ids


def _mongo_db_name(uri: str, fallback: str) -> str:
    parsed = urlparse(uri)
    if parsed.path and parsed.path != "/":
        return parsed.path.lstrip("/").split("?")[0]
    return fallback


def _mongo_url_and_db() -> tuple[str, str]:
    uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URL") or "mongodb://127.0.0.1:27018/strata_management"
    db_name = os.getenv("MONGODB_DB") or os.getenv("DB_NAME") or _mongo_db_name(uri, "strata_management")
    return uri, db_name


def _pg_dsn() -> str | None:
    value = os.getenv("DATABASE_URL")
    if not value:
        return None
    return value.replace("postgresql+asyncpg://", "postgresql://")


def _hash(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()


def _rng(*parts: Any) -> random.Random:
    return random.Random(int(_hash(*parts)[:16], 16))


def _parse_year(raw: Any) -> int:
    return int(str(raw).split("-")[0])


def _unit_sort_key(unit: dict[str, Any]) -> tuple[int, str]:
    text = str(unit.get("unit_number") or unit.get("lot_number") or "")
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits or "999999"), text


def _due_dates(levy_doc: dict[str, Any], levy_year: int) -> dict[str, date]:
    due: dict[str, date] = {}
    for entry in levy_doc.get("payment_schedule") or []:
        quarter = str(entry.get("quarter") or "").strip().upper()
        if quarter not in QUARTERS:
            continue
        try:
            due[quarter] = date.fromisoformat(str(entry.get("due_date"))[:10])
        except ValueError:
            continue

    # Required for 2023 in current East Gate data: only Q3/Q4 are present, but
    # the requested Demo Bank model needs all four owner-payment quarters.
    for quarter, (month, day) in FALLBACK_DUE_DATES.items():
        due.setdefault(quarter, date(levy_year, month, day))
    return due


def _proposed_ex_gst_total(levy_doc: dict[str, Any], fund: str) -> tuple[float, str]:
    key = "proposed_admin_expenses" if fund == "admin" else "proposed_sinking_expenses"
    value = levy_doc.get(key)
    if value not in (None, "") and float(value or 0) > 0:
        return float(value), key

    fund_doc_key = "admin_fund" if fund == "admin" else "sinking_fund"
    fund_doc = levy_doc.get(fund_doc_key) or {}

    # CONFIRMED BUG, fixed 2026-07-31 (found by a user cross-checking generated charge totals
    # against the source AGM/audited-financials CSV before approving a real posting batch):
    # {fund}.proposed_amount_cents is checked BEFORE {fund}.levy_income now, not after. Verified
    # against docs/finances/up13195_consolidated_financials-2021-2026.csv's own
    # `proposed_levy_ex_gst` column for every East Gate year 2021-2026, both funds:
    # proposed_amount_cents matches that authoritative column EXACTLY (to the cent) in all 12
    # cases checked. {fund}.levy_income is a DIFFERENT concept — real, ACTUAL collected income
    # (confirmed: it matches the same CSV's `actual_income` column, not `proposed_levy_ex_gst`;
    # its own levy_income_basis field is literally labelled "actual_income_verified_this_session"
    # on the live document) — using it as if it were "the proposed ex-GST levy total" and then
    # applying the GST multiplier on top double-counts against a figure that was never a
    # pre-GST budget figure to begin with. This was wrong for EVERY East Gate year where
    # levy_income happens to be populated (2021-2025 admin, 2022/2023 sinking at minimum) — not
    # just the one 2026 shape the branch below was originally added for. Real impact caught before
    # posting: this alone overstated the 2021-2026 admin+sinking levy charge total by $147,633.01
    # (postable lines only, as_of_date=2026-07-31) — confirmed by re-running the CLI script's
    # --preview before and after this fix and diffing the totals.
    if fund_doc.get("proposed_amount_cents") not in (None, ""):
        return float(fund_doc["proposed_amount_cents"]) / 100.0, f"{fund_doc_key}.proposed_amount_cents"

    if fund_doc.get("levy_income") not in (None, ""):
        return float(fund_doc.get("levy_income") or 0), f"{fund_doc_key}.levy_income"

    rate_key = "admin_levy_per_uoe_annual" if fund == "admin" else "sinking_levy_per_uoe_annual"
    total_uoe = float(levy_doc.get("total_uoe") or 0)
    return float(levy_doc.get(rate_key) or 0) * total_uoe, f"{rate_key}*total_uoe"


def _payment_pattern(building_id: str, unit_number: str, levy_year: int) -> str:
    sample = _rng("pattern-v3-gst", building_id, unit_number, levy_year).random()
    if sample < 0.15:
        return "annual_lump_sum"
    if sample < 0.27:
        return "half_yearly_lump_sum"
    if sample < 0.45:
        return "late"
    if sample < 0.58:
        return "arrears_catch_up"
    return "quarterly_regular"


def _payment_date(
    *,
    building_id: str,
    unit_number: str,
    levy_year: int,
    quarter: str,
    due_dates: dict[str, date],
    pattern: str,
    max_date: date,
) -> tuple[date, int]:
    rng = _rng("date-v3-gst", building_id, unit_number, levy_year, quarter, pattern)
    due = due_dates[quarter]
    if pattern == "annual_lump_sum":
        base = due_dates["Q1"]
        lag_days = rng.randint(-20, 8)
    elif pattern == "half_yearly_lump_sum":
        base = due_dates["Q1"] if quarter in {"Q1", "Q2"} else due_dates["Q3"]
        lag_days = rng.randint(-14, 12)
    elif pattern == "late":
        base = due
        lag_days = rng.randint(8, 45)
    elif pattern == "arrears_catch_up":
        base = due
        lag_days = rng.randint(46, 165)
    else:
        base = due
        lag_days = rng.randint(-7, 5)

    paid = min(base + timedelta(days=lag_days), max_date)
    return paid, (paid - due).days


def _allocate_cents(total_cents: int, weighted_keys: list[tuple[tuple[str, str], float]]) -> dict[tuple[str, str], int]:
    total_weight = sum(weight for _, weight in weighted_keys)
    rows: list[tuple[tuple[str, str], int, float]] = []
    floor_sum = 0
    for key, weight in weighted_keys:
        exact = (total_cents * weight) / total_weight if total_weight else 0
        floor = int(exact)
        rows.append((key, floor, exact - floor))
        floor_sum += floor

    residual = total_cents - floor_sum
    rows.sort(key=lambda row: (-row[2], row[0]))
    allocation = {key: floor for key, floor, _ in rows}
    for key, _, _ in rows[:residual]:
        allocation[key] += 1
    return allocation


def _description(
    *,
    pattern: str,
    levy_year: int,
    quarter: str,
    label: str,
    unit_number: str,
    amount_inc_cents: int,
    amount_ex_cents: int,
    gst_cents: int,
    paid_date: date,
    due_date: date,
    lag_days: int,
    basis: str,
    annual_ex_gst_cents: int,
    annual_inc_gst_cents: int,
    gst_rate: float,
    unit_count: int,
) -> str:
    pattern_labels = {
        "annual_lump_sum": "annual lump sum allocation",
        "half_yearly_lump_sum": "half-yearly lump sum allocation",
        "late": "late levy payment",
        "arrears_catch_up": "arrears catch-up payment",
        "quarterly_regular": "quarterly levy payment",
    }
    return (
        f"{pattern_labels[pattern]}: Levy {quarter} {levy_year} {label} - Unit {unit_number}; "
        f"paid {paid_date.isoformat()} for due date {due_date.isoformat()} ({lag_days:+d} days); "
        f"annual proposed {label} ${annual_ex_gst_cents / 100:.2f} ex-GST from {basis} "
        f"+ {gst_rate:.0%} GST = ${annual_inc_gst_cents / 100:.2f}; "
        f"allocated by UOE across {unit_count} units and 4 quarters = ${amount_inc_cents / 100:.2f} "
        f"(ex-GST ${amount_ex_cents / 100:.2f}, GST ${gst_cents / 100:.2f})"
    )


async def _connect_pg_for_guard(building_id: str) -> asyncpg.Connection | None:
    dsn = _pg_dsn()
    if not dsn:
        return None
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SELECT set_config('app.tenant_id', '00000000-0000-0000-0000-000000000000', true)")
        row = await conn.fetchrow(
            """
            SELECT tenant_id::text
            FROM core.schemes
            WHERE scheme_number = $1 OR scheme_id::text = $1
            ORDER BY CASE WHEN scheme_number = $1 THEN 0 ELSE 1 END
            LIMIT 1
            """,
            building_id,
        )
        if row and row["tenant_id"]:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(row["tenant_id"]))
    except Exception:
        await conn.close()
        raise
    return conn


def _pg_fields_match(row: asyncpg.Record, payload: dict[str, Any]) -> bool:
    return (
        int(row["amount_cents"] or 0) == int(payload["amount_cents"])
        and str(row["transaction_date"])[:10] == str(payload["posted_date"])[:10]
        and str(row["description"] or "") == str(payload["description"] or "")
        and str(row["reference"] or "") == str(payload.get("reference") or "")
        and str(row["external_transaction_id"] or "") == str(payload["external_transaction_id"] or "")
        and str(row["provider_name"] or "") == str(payload["provider"] or "")
    )


async def _guard_or_repair_pg_parity(
    pg_conn: asyncpg.Connection | None,
    existing: dict[str, Any],
    payload: dict[str, Any],
    *,
    repair_stale_sync: bool,
    dry_run: bool,
    parity_stats: PgParityStats,
) -> bool:
    ref = existing.get("finance_bank_transaction_ref")
    sync_status = existing.get("sync_status")
    if not ref and sync_status != "synced":
        return True

    parity_stats.checked += 1
    ref_uuid = _uuid_or_none(ref)
    if not ref_uuid:
        parity_stats.invalid_refs += 1
        if not repair_stale_sync:
            raise RuntimeError(
                "Synthetic Demo Bank row is marked synced but has an invalid Postgres bank transaction reference. "
                "Rerun with --repair-stale-sync to reset it to pending."
            )
        payload["sync_status"] = "pending"
        payload["sync_error"] = "Reset by migration_027: invalid finance_bank_transaction_ref."
        payload.pop("finance_bank_transaction_ref", None)
        parity_stats.reset_to_pending += 1
        return True

    if pg_conn is None:
        raise RuntimeError(
            "Synthetic Demo Bank row is already linked to Postgres, but DATABASE_URL is unavailable. "
            "Set DATABASE_URL and rerun with --repair-stale-sync so the script can compare PG parity."
        )

    row = await pg_conn.fetchrow(
        """
        SELECT
            bt.bank_transaction_id::text AS bank_transaction_id,
            bt.amount_cents,
            bt.transaction_date,
            bt.description,
            bt.reference,
            bt.external_transaction_id,
            bt.provider_name,
            COALESCE(receipts.receipt_count, 0) AS receipt_count
        FROM finance.bank_transactions bt
        LEFT JOIN LATERAL (
            SELECT COUNT(*)::int AS receipt_count
            FROM finance.receipts r
            WHERE r.bank_transaction_id = bt.bank_transaction_id
               OR r.external_reference = bt.bank_transaction_id::text
        ) receipts ON true
        WHERE bt.bank_transaction_id = $1::uuid
        """,
        ref_uuid,
    )
    if row is None:
        parity_stats.missing_pg_rows += 1
        if not repair_stale_sync:
            raise RuntimeError(
                f"Synthetic Demo Bank row references missing Postgres bank_transaction_id={ref_uuid}. "
                "Rerun with --repair-stale-sync to reset it to pending."
            )
        payload["sync_status"] = "pending"
        payload["sync_error"] = (
            "Reset by migration_027: stored Postgres bank_transaction_id was not present in connected DATABASE_URL."
        )
        payload.pop("finance_bank_transaction_ref", None)
        parity_stats.reset_to_pending += 1
        return True

    if _pg_fields_match(row, payload):
        parity_stats.parity_ok += 1
        return True

    if int(row["receipt_count"] or 0) > 0:
        parity_stats.locked_with_receipts += 1
        return False

    if not repair_stale_sync:
        raise RuntimeError(
            f"Synthetic Demo Bank row differs from unallocated Postgres bank_transaction_id={ref_uuid}. "
            "Rerun with --repair-stale-sync to update the unmatched PG bank transaction."
        )

    if not dry_run:
        try:
            await pg_conn.execute(
                """
                UPDATE finance.bank_transactions
                SET transaction_date = $2::date,
                    description = $3,
                    reference = $4,
                    amount_cents = $5,
                    external_transaction_id = $6,
                    provider_name = $7,
                    source_type = $8,
                    confidence = $9,
                    provenance_class = $10,
                    evidence_type = $11,
                    formula_version = $12,
                    requires_review = $13,
                    date_basis = $14,
                    unit_number = $15
                WHERE bank_transaction_id = $1::uuid
                """,
                ref_uuid,
                payload["posted_date"],
                payload["description"],
                payload.get("reference"),
                int(payload["amount_cents"]),
                payload["external_transaction_id"],
                payload["provider"],
                payload["source_type"],
                payload.get("confidence"),
                payload.get("provenance_class"),
                payload.get("evidence_type"),
                payload.get("formula_version"),
                bool(payload.get("requires_review")),
                payload.get("date_basis"),
                payload.get("unit_number"),
            )
        except Exception:
            parity_stats.pg_errors += 1
            raise
    parity_stats.pg_updated += 1
    return True


async def _load_units(
    db, building_id: str, *, warnings: list[str] | None = None,
) -> list[UnitShare]:
    """`warnings`, if given, collects one entry per unit dropped for missing/invalid UOE —
    callers that want gap-tolerant visibility (e.g. the generic budget generator) pass a list;
    the standalone migration script's own `main()` doesn't, preserving its exact prior output."""
    docs = await db.units.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}},
        {"unit_number": 1, "lot_number": 1, "entitlement_units": 1, "uoe": 1, "entitlement": 1},
    ).to_list(None)
    units: list[UnitShare] = []
    for doc in sorted(docs, key=_unit_sort_key):
        unit_number = str(doc.get("unit_number") or doc.get("lot_number") or "").strip()
        try:
            uoe = float(doc.get("entitlement_units") or doc.get("uoe") or doc.get("entitlement"))
        except (TypeError, ValueError):
            uoe = 0.0
        if unit_number and uoe > 0:
            units.append(UnitShare(unit_number=unit_number, uoe=uoe))
        elif warnings is not None:
            warnings.append(f"Skipped unit {unit_number or doc.get('_id')!r}: missing or non-positive UOE")
    return units


async def _load_levies(
    db, building_id: str, from_year: int, to_year: int, *, strict: bool = True,
) -> dict[int, dict[str, Any]]:
    """`strict=True` (default, used by the standalone migration script) raises on any missing
    year in the requested range — unchanged prior behaviour. `strict=False` (used by the
    gap-tolerant generic budget generator) returns whatever years ARE present; the caller is
    responsible for checking which of `range(from_year, to_year + 1)` didn't come back and
    surfacing that as a warning rather than a hard failure — real buildings onboarded with
    partial history are the expected case there, not an error condition."""
    docs = await db.annual_levies.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}}
    ).to_list(None)
    levies = {_parse_year(doc.get("year")): doc for doc in docs if str(doc.get("year") or "").strip()}
    missing = [year for year in range(from_year, to_year + 1) if year not in levies]
    if missing and strict:
        raise RuntimeError(f"Missing annual_levies for building_id={building_id}: {missing}")
    return levies


async def _load_gst_settings(db, building_id: str) -> dict[str, Any]:
    settings = await db.settings.find_one(
        {
            "building_id": building_id,
            "$or": [{"type": {"$exists": False}}, {"type": None}, {"type": "general"}],
        },
        {"_id": 0, "gst_registered": 1, "levy_gst_rate": 1},
        sort=[("updated_at", -1), ("created_at", -1), ("_id", -1)],
    )
    return parse_levy_gst_settings(settings)


# ATO GST registration threshold for non-profit bodies (ATO ID 2016/1). An Owners Corporation /
# strata scheme is treated as a non-profit body for GST purposes provided it does not distribute
# profits to individual lot owners — true for virtually every residential strata scheme — and the
# non-profit GST registration threshold is $150,000 annual turnover, not the standard $75,000.
# Below this threshold (and absent voluntary registration), a scheme is not required to register
# for GST and does not charge GST on levies: proposed_levy_ex_gst == proposed_levy_incl_gst that
# year. This is federal Australian tax law, not building-specific — the same rule applies to every
# building this generator runs for, not just East Gate. Real-world trigger: East Gate's 2021 annual
# turnover ($138,460, admin-only — no separate sinking fund existed that year) fell below $150,000;
# docs/finances/up13195_consolidated_financials-2021-2026.csv confirms proposed_levy_ex_gst ==
# proposed_levy_incl_gst == 138460 for 2021, i.e. zero GST charged that year, found and confirmed
# by a user cross-checking a real posting batch against that source CSV before approving it.
AUSTRALIAN_GST_NONPROFIT_TURNOVER_THRESHOLD_CENTS = 15_000_000  # $150,000


def _year_gst_registered(levy_doc: dict[str, Any]) -> bool:
    """Whether GST should be applied to this year's levy figures.

    Prefers an explicit, source-verified per-year fact if the import ever populates one
    (`gst_registered_this_year` — no current importer sets this; a forward extension point for
    when real ATO-registration-event evidence, rather than an inferred threshold check, becomes
    available). Falls back to the $150,000 non-profit turnover threshold (see
    AUSTRALIAN_GST_NONPROFIT_TURNOVER_THRESHOLD_CENTS above) applied to THIS year's own
    admin+sinking proposed ex-GST turnover.

    Deliberately does NOT model the ATO's stateful registration-history rules — a scheme that
    crosses the threshold must register within 21 days and, once registered, cannot deregister
    until at least 12 months have passed even if turnover later dips back below $150,000 (requires
    an AGM deregistration motion). Modelling that needs real registration/deregistration event
    dates this system does not currently capture; a per-year explicit fact, once available, should
    always be preferred over this stateless per-year inference for exactly that reason.
    """
    explicit = levy_doc.get("gst_registered_this_year")
    if explicit is not None:
        return bool(explicit)

    admin_ex_gst, _ = _proposed_ex_gst_total(levy_doc, "admin")
    sinking_ex_gst, _ = _proposed_ex_gst_total(levy_doc, "sinking")
    total_turnover_cents = int(round((admin_ex_gst + sinking_ex_gst) * 100))
    return total_turnover_cents >= AUSTRALIAN_GST_NONPROFIT_TURNOVER_THRESHOLD_CENTS


def _fund_models(building_id: str, levy_doc: dict[str, Any], gst_multiplier: float) -> list[FundModel]:
    effective_multiplier = gst_multiplier if _year_gst_registered(levy_doc) else 1.0
    models: list[FundModel] = []
    for fund, account_ref, label in (
        ("admin", f"ADMIN-{building_id}", "Admin Fund"),
        ("sinking", f"SINKING-{building_id}", "Sinking Fund"),
    ):
        annual_ex_gst, basis = _proposed_ex_gst_total(levy_doc, fund)
        annual_ex_gst_cents = int(round(annual_ex_gst * 100))
        annual_inc_gst_cents = int(round(annual_ex_gst * effective_multiplier * 100))
        if annual_inc_gst_cents <= 0:
            continue
        models.append(
            FundModel(
                fund=fund,
                account_ref=account_ref,
                label=label,
                annual_ex_gst_cents=annual_ex_gst_cents,
                annual_inc_gst_cents=annual_inc_gst_cents,
                basis=basis,
            )
        )
    return models


async def _upsert_transaction(
    db,
    *,
    pg_conn: asyncpg.Connection | None,
    building_id: str,
    levy_year: int,
    quarter: str,
    unit: UnitShare,
    fund_model: FundModel,
    unit_count: int,
    amount_ex_cents: int,
    amount_inc_cents: int,
    paid_date: date,
    due_date: date,
    lag_days: int,
    pattern: str,
    gst_rate: float,
    repair_stale_sync: bool,
    parity_stats: PgParityStats,
    dry_run: bool,
) -> tuple[bool, bool]:
    existing = await db.demo_bank_transactions.find_one(
        {
            "building_id": building_id,
            "source_type": SOURCE_TYPE,
            "account_ref": fund_model.account_ref,
            "levy_year": str(levy_year),
            "levy_quarter": quarter,
            "unit_number": unit.unit_number,
        }
    )
    if existing:
        external_id = existing["external_transaction_id"]
        idempotency_key = existing["idempotency_key"]
        sync_status = existing.get("sync_status") or "pending"
        set_on_insert: dict[str, Any] = {}
    else:
        external_id = _hash("randomized-levy-gst-v1", building_id, fund_model.account_ref, levy_year, quarter, unit.unit_number)
        idempotency_key = _hash("synthetic-gst", building_id, fund_model.account_ref, levy_year, quarter, unit.unit_number)
        sync_status = "pending"
        set_on_insert = {"created_at": datetime.now(timezone.utc).isoformat()}

    gst_cents = amount_inc_cents - amount_ex_cents
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "building_id": building_id,
        "provider": PROVIDER,
        "account_ref": fund_model.account_ref,
        "external_transaction_id": external_id,
        "idempotency_key": idempotency_key,
        "posted_date": paid_date.isoformat(),
        "effective_date": paid_date.isoformat(),
        "amount_cents": amount_inc_cents,
        "amount_ex_gst_cents": amount_ex_cents,
        "gst_cents": gst_cents,
        "direction": "credit",
        "description": _description(
            pattern=pattern,
            levy_year=levy_year,
            quarter=quarter,
            label=fund_model.label,
            unit_number=unit.unit_number,
            amount_inc_cents=amount_inc_cents,
            amount_ex_cents=amount_ex_cents,
            gst_cents=gst_cents,
            paid_date=paid_date,
            due_date=due_date,
            lag_days=lag_days,
            basis=fund_model.basis,
            annual_ex_gst_cents=fund_model.annual_ex_gst_cents,
            annual_inc_gst_cents=fund_model.annual_inc_gst_cents,
            gst_rate=gst_rate,
            unit_count=unit_count,
        ),
        "reference": f"{building_id}-{levy_year}-{quarter}-{unit.unit_number}-{fund_model.fund.upper()}-GST",
        "source_type": SOURCE_TYPE,
        "source_id": f"annual_levies:{levy_year}",
        "unit_number": unit.unit_number,
        "strata_year": str(levy_year),
        "levy_quarter": quarter,
        "levy_year": str(levy_year),
        "sync_status": sync_status,
        "is_synthetic": True,
        "is_test_data": False,
        # financial_matching.promote_high_confidence_reconstruction() checks
        # this exact string before allowing reconstructed rows to post through
        # the single authorised ledger-write path.
        "confidence": "high",
        "confidence_score": 0.95,
        "provenance_class": "reconstruction",
        "evidence_type": "proposed_budget_uoe_gst_reconstruction_with_modelled_payment_timing",
        "formula_version": "historical-levy-reconstruction-v5-proposed-gst-residual-allocation",
        "requires_review": False,
        "date_basis": "deterministic_randomized_payment_date_from_due_date",
        "due_date": due_date.isoformat(),
        "payment_lag_days": lag_days,
        "payment_pattern": pattern,
        "payment_group_id": f"{building_id}-{levy_year}-{unit.unit_number}-{pattern}",
        "randomization_seed": _hash("seed-v5-gst", building_id, unit.unit_number, levy_year)[:16],
        "annual_proposed_fund_total_cents": fund_model.annual_inc_gst_cents,
        "annual_proposed_fund_ex_gst_cents": fund_model.annual_ex_gst_cents,
        "annual_proposed_fund_gst_cents": fund_model.annual_inc_gst_cents - fund_model.annual_ex_gst_cents,
        "gst_rate": gst_rate,
        "gst_registered": gst_rate > 0,
        "amount_basis": f"{fund_model.basis}+GST allocated residual cents by UOE",
        "raw_payload": {
            "source_collection": "annual_levies",
            "source_id": f"annual_levies:{levy_year}",
            "due_date": due_date.isoformat(),
            "payment_pattern": pattern,
            "payment_lag_days": lag_days,
            "annual_proposed_fund_total_ex_gst_cents": fund_model.annual_ex_gst_cents,
            "annual_proposed_fund_total_inc_gst_cents": fund_model.annual_inc_gst_cents,
            "gst_rate": gst_rate,
            "amount_basis": f"{fund_model.basis}+GST",
            "uoe": unit.uoe,
            "allocation": "largest remainder by unit-quarter weight, deterministic tie-break",
        },
        "updated_at": now,
    }
    if existing:
        current_ref = existing.get("finance_bank_transaction_ref")
        if current_ref:
            payload["finance_bank_transaction_ref"] = current_ref
        if not await _guard_or_repair_pg_parity(
            pg_conn,
            existing,
            payload,
            repair_stale_sync=repair_stale_sync,
            dry_run=dry_run,
            parity_stats=parity_stats,
        ):
            return False, True

    if dry_run:
        return existing is None, existing is not None

    update_doc: dict[str, Any] = {"$set": payload}
    if set_on_insert:
        update_doc["$setOnInsert"] = set_on_insert
    if existing and existing.get("finance_bank_transaction_ref") and not payload.get("finance_bank_transaction_ref"):
        update_doc["$unset"] = {"finance_bank_transaction_ref": ""}

    result = await db.demo_bank_transactions.update_one(
        {"building_id": building_id, "idempotency_key": idempotency_key},
        update_doc,
        upsert=True,
    )
    return result.upserted_id is not None, result.upserted_id is None


async def _refresh_account_balances(db, building_id: str, dry_run: bool) -> dict[str, int]:
    pipeline = [
        {
            "$match": {
                "building_id": building_id,
                "$or": [{"status": {"$in": ["posted", "pending"]}}, {"status": {"$exists": False}}],
            }
        },
        {"$group": {"_id": {"account_ref": "$account_ref", "direction": "$direction"}, "total": {"$sum": "$amount_cents"}}},
    ]
    grouped: dict[str, dict[str, int]] = {}
    async for row in db.demo_bank_transactions.aggregate(pipeline):
        account_ref = row["_id"]["account_ref"]
        direction = row["_id"].get("direction") or "credit"
        grouped.setdefault(account_ref, {"credit": 0, "debit": 0})[direction] = int(row["total"] or 0)

    balances = {account_ref: values.get("credit", 0) - values.get("debit", 0) for account_ref, values in grouped.items()}
    if not dry_run:
        for account_ref, current_balance in balances.items():
            await db.demo_bank_accounts.update_one(
                {"building_id": building_id, "account_ref": account_ref},
                {"$set": {"current_balance_cents": current_balance, "updated_at": datetime.now(timezone.utc)}},
            )
    return balances


async def _repair_stale_sync_refs(db, building_id: str, dry_run: bool) -> dict[str, int]:
    dsn = _pg_dsn()
    if not dsn:
        return {"checked": 0, "invalid_refs": 0, "missing_pg_rows": 0, "reset_to_pending": 0}

    docs = await db.demo_bank_transactions.find(
        {
            "building_id": building_id,
            "source_type": SOURCE_TYPE,
            "finance_bank_transaction_ref": {"$nin": [None, ""]},
        },
        {"finance_bank_transaction_ref": 1},
    ).to_list(None)
    if not docs:
        return {"checked": 0, "invalid_refs": 0, "missing_pg_rows": 0, "reset_to_pending": 0}

    refs, invalid_ids = _split_uuid_refs(docs)
    rows = []
    conn = await _connect_pg_for_guard(building_id)
    if conn is None:
        return {"checked": len(docs), "invalid_refs": len(invalid_ids), "missing_pg_rows": 0, "reset_to_pending": 0}
    try:
        if refs:
            rows = await conn.fetch(
                "SELECT bank_transaction_id::text AS id FROM finance.bank_transactions WHERE bank_transaction_id = ANY($1::uuid[])",
                refs,
            )
    finally:
        await conn.close()

    existing = {row["id"] for row in rows}
    stale_ids = invalid_ids + [
        doc["_id"] for doc in docs if (ref := _uuid_or_none(doc.get("finance_bank_transaction_ref"))) and ref not in existing
    ]
    if dry_run or not stale_ids:
        return {
            "checked": len(docs),
            "invalid_refs": len(invalid_ids),
            "missing_pg_rows": len(stale_ids) - len(invalid_ids),
            "reset_to_pending": 0,
        }

    result = await db.demo_bank_transactions.update_many(
        {"_id": {"$in": stale_ids}},
        {
            "$set": {
                "sync_status": "pending",
                "sync_error": "Reset by migration_027: stored Postgres bank_transaction_id was not present in connected DATABASE_URL.",
                "last_sync_attempt_at": datetime.now(timezone.utc),
            },
            "$unset": {"finance_bank_transaction_ref": ""},
        },
    )
    return {
        "checked": len(docs),
        "invalid_refs": len(invalid_ids),
        "missing_pg_rows": len(stale_ids) - len(invalid_ids),
        "reset_to_pending": int(result.modified_count),
    }


async def run(
    db,
    *,
    building_id: str,
    from_year: int,
    to_year: int,
    max_payment_date: date,
    expected_unit_count: int | None,
    repair_stale_sync: bool,
    dry_run: bool,
) -> dict[str, Any]:
    units = await _load_units(db, building_id)
    if expected_unit_count is not None and len(units) != expected_unit_count:
        raise RuntimeError(f"Expected {expected_unit_count} units for building_id={building_id}, found {len(units)}")

    levies = await _load_levies(db, building_id, from_year, to_year)
    gst = await _load_gst_settings(db, building_id)
    gst_rate = float(gst["effective_gst_rate"])
    gst_multiplier = float(gst["gst_multiplier"])
    weighted_keys = [((unit.unit_number, quarter), unit.uoe) for unit in units for quarter in QUARTERS]

    inserted = already_present = 0
    by_year: dict[str, dict[str, dict[str, int]]] = {}
    pattern_counts: dict[str, int] = {}
    parity_stats = PgParityStats()
    pg_conn = await _connect_pg_for_guard(building_id) if _pg_dsn() else None

    try:
        for year in range(from_year, to_year + 1):
            levy = levies[year]
            due_dates = _due_dates(levy, year)
            unit_patterns = {
                unit.unit_number: _payment_pattern(building_id, unit.unit_number, year)
                for unit in units
            }
            for unit in units:
                pattern = unit_patterns[unit.unit_number]
                pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
            for fund_model in _fund_models(building_id, levy, gst_multiplier):
                ex_allocation = _allocate_cents(fund_model.annual_ex_gst_cents, weighted_keys)
                inc_allocation = _allocate_cents(fund_model.annual_inc_gst_cents, weighted_keys)
                for unit in units:
                    pattern = unit_patterns[unit.unit_number]
                    for quarter in QUARTERS:
                        paid_date, lag_days = _payment_date(
                            building_id=building_id,
                            unit_number=unit.unit_number,
                            levy_year=year,
                            quarter=quarter,
                            due_dates=due_dates,
                            pattern=pattern,
                            max_date=max_payment_date,
                        )
                        amount_ex_cents = ex_allocation[(unit.unit_number, quarter)]
                        amount_inc_cents = inc_allocation[(unit.unit_number, quarter)]
                        was_inserted, was_present = await _upsert_transaction(
                            db,
                            pg_conn=pg_conn,
                            building_id=building_id,
                            levy_year=year,
                            quarter=quarter,
                            unit=unit,
                            fund_model=fund_model,
                            unit_count=len(units),
                            amount_ex_cents=amount_ex_cents,
                            amount_inc_cents=amount_inc_cents,
                            paid_date=paid_date,
                            due_date=due_dates[quarter],
                            lag_days=lag_days,
                            pattern=pattern,
                            gst_rate=gst_rate,
                            repair_stale_sync=repair_stale_sync,
                            parity_stats=parity_stats,
                            dry_run=dry_run,
                        )
                        inserted += int(was_inserted)
                        already_present += int(was_present)
                        bucket = by_year.setdefault(str(year), {}).setdefault(
                            fund_model.account_ref, {"rows": 0, "inc_gst_cents": 0, "ex_gst_cents": 0, "gst_cents": 0}
                        )
                        bucket["rows"] += 1
                        bucket["inc_gst_cents"] += amount_inc_cents
                        bucket["ex_gst_cents"] += amount_ex_cents
                        bucket["gst_cents"] += amount_inc_cents - amount_ex_cents
    finally:
        if pg_conn is not None:
            await pg_conn.close()

    balances = await _refresh_account_balances(db, building_id, dry_run)
    stale_sync = await _repair_stale_sync_refs(db, building_id, dry_run) if repair_stale_sync else {
        "checked": 0,
        "invalid_refs": 0,
        "missing_pg_rows": 0,
        "reset_to_pending": 0,
    }
    return {
        "building_id": building_id,
        "dry_run": dry_run,
        "years_range": f"{from_year}-{to_year}",
        "units_processed": len(units),
        "gst_settings": gst,
        "inserted_or_would_insert": inserted,
        "updated_or_already_present": already_present,
        "by_year": by_year,
        "unit_year_payment_patterns": dict(sorted(pattern_counts.items())),
        "account_balances_cents": balances,
        "stale_sync_repair": stale_sync,
        "pg_parity_guard": parity_stats.__dict__,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Randomize historical Demo Bank levy payments from proposed levy + GST for an onboarding building."
    )
    parser.add_argument("--building-id", default=DEFAULT_BUILDING_ID)
    parser.add_argument("--from-year", type=int, default=2021)
    parser.add_argument("--to-year", type=int, default=2026)
    parser.add_argument("--max-payment-date", default=date.today().isoformat())
    parser.add_argument("--expected-unit-count", type=int, default=None)
    parser.add_argument(
        "--repair-stale-sync",
        action="store_true",
        help="Allow stale/invalid PG refs to reset to pending and unmatched PG rows to be updated to regenerated parity.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    mongo_url, db_name = _mongo_url_and_db()
    client = AsyncIOMotorClient(mongo_url)
    try:
        result = await run(
            client[db_name],
            building_id=args.building_id,
            from_year=args.from_year,
            to_year=args.to_year,
            max_payment_date=date.fromisoformat(args.max_payment_date),
            expected_unit_count=args.expected_unit_count,
            repair_stale_sync=args.repair_stale_sync,
            dry_run=args.dry_run,
        )
    finally:
        client.close()

    print("Randomized Demo Bank levy reconstruction summary")
    print("------------------------------------------------")
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    asyncio.run(main())
