# @featuretrace:demo_bank — Portal ⇄ constructed-ledger reconciliation engine (building-agnostic):
#   per-unit variance, chain-integrity, reverse-engineered implied-paid, suspected-year ranking.
# Layer: service
# Data flow: unit_levy_ledger (Mongo) + staging_strata_web_snapshots (Mongo) + annual_levies
#            controls -> per-unit UnitReconciliation -> reconciliation_unit_exceptions (Mongo,
#            persisted variances) and GET /finance/reconciliation/analysis (building-scoped).
# Related: backend/routers/finance_reconciliation.py (the live portal-vs-ledger delta view this
#          engine deepens), backend/services/reconstruction_generators/annual_fund_reconciliation.py
#          (year/fund control signal reused for suspected-year ranking),
#          backend/services/strata_web_balance_inference_service.py (consecutive-snapshot deltas),
#          backend/services/unit_levy_ledger_service.py (the chaining/closing formula this mirrors),
#          docs/architecture/strata_web_portal_reconciliation_logic.md (the worked-out logic)
# Toggle: bank_feeds_sync_enabled
# Collection: unit_levy_ledger, staging_strata_web_snapshots, annual_levies, reconciliation_unit_exceptions
# Tests: tests/backend/test_portal_ledger_reconciliation.py
"""Portal ⇄ constructed-ledger reconciliation — building-agnostic.

Implements the worked-out logic in
``docs/architecture/strata_web_portal_reconciliation_logic.md``:

* The scraped Strata Web portal balance ``P(u)`` is the REAL "today" anchor (per-unit total
  outstanding across all years; >0 owing, <0 credit) but tells us only the net, not levied vs paid.
* The constructed ledger (budget-derived charges, chained year-over-year, minus itemized payments)
  gives an independent "today" balance ``Bc(u)``.
* ``variance = Bc − P``; the two sides are independent so a disagreement is a real exception.
* Reverse-engineering paid (trusting the charges): ``implied_paid = opening_first + Σlevied +
  Σinterest − P``; ``paid_gap = implied_paid − recorded_paid`` — which is algebraically the
  *re-chained* variance. ``paid_gap>0`` ⇒ a missing recorded payment; ``paid_gap<0`` ⇒ an
  over-recorded/duplicate payment (the two failure modes this codebase has actually hit).

This module is PURE-COMPUTE + read orchestration. It NEVER writes to unit_levy_ledger and NEVER
posts a portal-derived value as a journal source (the documented "old wrong path" that caused a
$1.77M duplicate credit). Corrections live in the separate, dual-control, building-agnostic
``backend/scripts/reconcile_portal_ledger.py`` (Phase 2), which re-chains stored figures forward —
never the portal.
"""
from __future__ import annotations

import uuid as _uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from utils.unit_number import extract_lot_int

# $1 rounding/date-basis slack — same tolerance as annual_fund_reconciliation, not a hard gate.
DEFAULT_TOLERANCE_CENTS = 100


def _to_cents(value: Any) -> int:
    """Convert a stored dollar-float (unit_levy_ledger's known-dollars fields) to integer cents at
    the read boundary. Missing/blank → 0. Cents are the only honest ledger truth (CLAUDE.md)."""
    if value in (None, ""):
        return 0
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class UnitYearLedger:
    """One unit_levy_ledger row, normalised to integer cents. Built by the orchestration layer;
    the pure compute below takes a list of these sorted ascending by year."""
    year: int
    admin_opening_cents: int
    sinking_opening_cents: int
    admin_levied_cents: int
    sinking_levied_cents: int
    admin_interest_cents: int
    sinking_interest_cents: int
    admin_paid_cents: int
    sinking_paid_cents: int
    admin_closing_cents: int
    sinking_closing_cents: int

    @property
    def opening_cents(self) -> int:
        return self.admin_opening_cents + self.sinking_opening_cents

    @property
    def levied_cents(self) -> int:
        return self.admin_levied_cents + self.sinking_levied_cents

    @property
    def interest_cents(self) -> int:
        return self.admin_interest_cents + self.sinking_interest_cents

    @property
    def paid_cents(self) -> int:
        return self.admin_paid_cents + self.sinking_paid_cents

    @property
    def closing_cents(self) -> int:
        return self.admin_closing_cents + self.sinking_closing_cents

    @classmethod
    def from_doc(cls, doc: dict) -> Optional["UnitYearLedger"]:
        raw_year = doc.get("year")
        try:
            year = int(str(raw_year).split("-")[0])
        except (TypeError, ValueError):
            return None
        return cls(
            year=year,
            admin_opening_cents=_to_cents(doc.get("admin_opening")),
            sinking_opening_cents=_to_cents(doc.get("sinking_opening")),
            admin_levied_cents=_to_cents(doc.get("admin_levied")),
            sinking_levied_cents=_to_cents(doc.get("sinking_levied")),
            admin_interest_cents=_to_cents(doc.get("admin_interest")),
            sinking_interest_cents=_to_cents(doc.get("sinking_interest")),
            admin_paid_cents=_to_cents(doc.get("admin_paid")),
            sinking_paid_cents=_to_cents(doc.get("sinking_paid")),
            admin_closing_cents=_to_cents(doc.get("admin_closing")),
            sinking_closing_cents=_to_cents(doc.get("sinking_closing")),
        )


@dataclass(frozen=True)
class ChainBreak:
    """A year whose STORED opening does not equal the prior year's STORED closing (per fund)."""
    year: int
    fund: str  # "admin" | "sinking"
    opening_cents: int
    prior_closing_cents: int
    gap_cents: int


@dataclass(frozen=True)
class UnitReconciliation:
    unit_number: str
    as_of_date: Optional[str]
    portal_balance_cents: Optional[int]
    # Constructed balance today as STORED (latest year's admin_closing + sinking_closing).
    constructed_balance_cents: int
    # Constructed balance today if openings were RE-CHAINED from the first year forward.
    rechained_balance_cents: int
    # Bc(stored) − P. What the current UI disagreement is measured against.
    variance_cents: Optional[int]
    # Bc(re-chained) − P == paid_gap. The "true" reconciliation once chain breaks are fixed.
    rechained_variance_cents: Optional[int]
    implied_paid_cents: Optional[int]
    recorded_paid_cents: int
    paid_gap_cents: Optional[int]
    paid_gap_class: str  # "reconciled" | "missing_payment" | "over_recorded" | "no_portal_data"
    chain_breaks: list[ChainBreak]
    row_nonreconciling_years: list[int]
    # Re-chaining alone would bring the unit within tolerance of the portal.
    rechain_would_resolve: bool
    suspected_years: list[dict]
    status: str  # "reconciled" | "variance" | "no_portal_data" | "no_ledger_data"

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["chain_breaks"] = [asdict(cb) for cb in self.chain_breaks]
        return d


def compute_unit_reconciliation(
    *,
    unit_number: str,
    years: list[UnitYearLedger],
    portal_balance_cents: Optional[int],
    as_of_date: Optional[str] = None,
    annual_control_variance_years: Optional[set[int]] = None,
    tolerance_cents: int = DEFAULT_TOLERANCE_CENTS,
) -> UnitReconciliation:
    """PURE — no I/O. Reconcile one unit's constructed ledger against its portal balance.

    ``years`` need not be pre-sorted. ``annual_control_variance_years`` is the set of years whose
    building-level annual fund-control reconciliation is out of tolerance (from
    annual_fund_reconciliation) — a suspected-year signal only; pass an empty set if unavailable.
    """
    annual_control_variance_years = annual_control_variance_years or set()
    ys = sorted(years, key=lambda y: y.year)

    if not ys:
        return UnitReconciliation(
            unit_number=unit_number, as_of_date=as_of_date,
            portal_balance_cents=portal_balance_cents,
            constructed_balance_cents=0, rechained_balance_cents=0,
            variance_cents=None, rechained_variance_cents=None,
            implied_paid_cents=None, recorded_paid_cents=0, paid_gap_cents=None,
            paid_gap_class="no_portal_data", chain_breaks=[], row_nonreconciling_years=[],
            rechain_would_resolve=False, suspected_years=[], status="no_ledger_data",
        )

    # --- Chain-integrity: stored opening(y) vs prior stored closing(y-1), per fund ---
    chain_breaks: list[ChainBreak] = []
    for prev, cur in zip(ys, ys[1:]):
        admin_gap = cur.admin_opening_cents - prev.admin_closing_cents
        if abs(admin_gap) > tolerance_cents:
            chain_breaks.append(ChainBreak(
                year=cur.year, fund="admin",
                opening_cents=cur.admin_opening_cents,
                prior_closing_cents=prev.admin_closing_cents, gap_cents=admin_gap,
            ))
        sinking_gap = cur.sinking_opening_cents - prev.sinking_closing_cents
        if abs(sinking_gap) > tolerance_cents:
            chain_breaks.append(ChainBreak(
                year=cur.year, fund="sinking",
                opening_cents=cur.sinking_opening_cents,
                prior_closing_cents=prev.sinking_closing_cents, gap_cents=sinking_gap,
            ))

    # --- Row reconciliation: stored closing == opening + levied + interest − paid, per fund ---
    row_nonreconciling_years: list[int] = []
    for y in ys:
        admin_expected = y.admin_opening_cents + y.admin_levied_cents + y.admin_interest_cents - y.admin_paid_cents
        sinking_expected = y.sinking_opening_cents + y.sinking_levied_cents + y.sinking_interest_cents - y.sinking_paid_cents
        if (abs(admin_expected - y.admin_closing_cents) > tolerance_cents
                or abs(sinking_expected - y.sinking_closing_cents) > tolerance_cents):
            row_nonreconciling_years.append(y.year)

    # --- Constructed balances: stored vs re-chained-from-first-year ---
    constructed_balance_cents = ys[-1].closing_cents

    prev_admin_close: Optional[int] = None
    prev_sink_close: Optional[int] = None
    for y in ys:
        admin_open = y.admin_opening_cents if prev_admin_close is None else prev_admin_close
        sink_open = y.sinking_opening_cents if prev_sink_close is None else prev_sink_close
        prev_admin_close = admin_open + y.admin_levied_cents + y.admin_interest_cents - y.admin_paid_cents
        prev_sink_close = sink_open + y.sinking_levied_cents + y.sinking_interest_cents - y.sinking_paid_cents
    rechained_balance_cents = (prev_admin_close or 0) + (prev_sink_close or 0)

    # --- Reverse-engineered paid (trusting charges + first-year opening) ---
    opening_first_cents = ys[0].opening_cents
    sum_levied = sum(y.levied_cents for y in ys)
    sum_interest = sum(y.interest_cents for y in ys)
    recorded_paid_cents = sum(y.paid_cents for y in ys)

    variance_cents: Optional[int] = None
    rechained_variance_cents: Optional[int] = None
    implied_paid_cents: Optional[int] = None
    paid_gap_cents: Optional[int] = None
    paid_gap_class = "no_portal_data"

    if portal_balance_cents is not None:
        variance_cents = constructed_balance_cents - portal_balance_cents
        rechained_variance_cents = rechained_balance_cents - portal_balance_cents
        implied_paid_cents = opening_first_cents + sum_levied + sum_interest - portal_balance_cents
        paid_gap_cents = implied_paid_cents - recorded_paid_cents
        # paid_gap_cents == rechained_variance_cents (algebraic identity — see module docstring).
        if abs(rechained_variance_cents) <= tolerance_cents:
            paid_gap_class = "reconciled"
        elif rechained_variance_cents > 0:
            # Constructed owes MORE than the real portal balance → we recorded too little paid.
            paid_gap_class = "missing_payment"
        else:
            # Constructed owes LESS than reality → over-recorded / duplicate payment.
            paid_gap_class = "over_recorded"

    chain_ok = not chain_breaks
    rows_ok = not row_nonreconciling_years
    portal_ok = portal_balance_cents is not None and abs(rechained_variance_cents or 0) <= tolerance_cents
    rechain_would_resolve = bool(chain_breaks) and rows_ok and (
        portal_balance_cents is None or portal_ok
    )

    if portal_balance_cents is None:
        status = "no_portal_data"
    elif chain_ok and rows_ok and portal_ok:
        status = "reconciled"
    else:
        status = "variance"

    # --- Suspected-year ranking: fuse chain-break, row-nonreconciling, annual-control signals ---
    suspected: dict[int, list[str]] = {}
    for cb in chain_breaks:
        suspected.setdefault(cb.year, []).append(f"chain_break_{cb.fund}")
    for yr in row_nonreconciling_years:
        suspected.setdefault(yr, []).append("row_does_not_reconcile")
    for yr in sorted(annual_control_variance_years):
        suspected.setdefault(yr, []).append("annual_control_variance")
    suspected_years = [
        {"year": yr, "signal_count": len(reasons), "reasons": reasons}
        for yr, reasons in sorted(suspected.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]

    return UnitReconciliation(
        unit_number=unit_number, as_of_date=as_of_date,
        portal_balance_cents=portal_balance_cents,
        constructed_balance_cents=constructed_balance_cents,
        rechained_balance_cents=rechained_balance_cents,
        variance_cents=variance_cents, rechained_variance_cents=rechained_variance_cents,
        implied_paid_cents=implied_paid_cents, recorded_paid_cents=recorded_paid_cents,
        paid_gap_cents=paid_gap_cents, paid_gap_class=paid_gap_class,
        chain_breaks=chain_breaks, row_nonreconciling_years=row_nonreconciling_years,
        rechain_would_resolve=rechain_would_resolve, suspected_years=suspected_years,
        status=status,
    )


def compute_rechain_plan(
    rows: list[UnitYearLedger], tolerance_cents: int = DEFAULT_TOLERANCE_CENTS,
) -> list[dict[str, Any]]:
    """PURE — the Phase 2 correction core. Return per-year corrected balances (in cents) for the
    years that change when openings are re-chained FORWARD from the first chain-break year.

    Only years from the first break onward are recomputed (earlier years are already correctly
    chained). Stored levied/paid/interest are TRUSTED and untouched; the portal is never consulted.
    No-op years (already equal to their re-chained value within tolerance) are dropped.
    """
    ys = sorted(rows, key=lambda r: r.year)
    first_break_idx: Optional[int] = None
    for i in range(1, len(ys)):
        prev, cur = ys[i - 1], ys[i]
        if (abs(cur.admin_opening_cents - prev.admin_closing_cents) > tolerance_cents
                or abs(cur.sinking_opening_cents - prev.sinking_closing_cents) > tolerance_cents):
            first_break_idx = i
            break
    if first_break_idx is None:
        return []

    changes: list[dict[str, Any]] = []
    prev = ys[first_break_idx - 1]
    prev_admin_close = prev.admin_closing_cents
    prev_sink_close = prev.sinking_closing_cents
    for y in ys[first_break_idx:]:
        new_admin_open, new_sink_open = prev_admin_close, prev_sink_close
        new_admin_close = new_admin_open + y.admin_levied_cents + y.admin_interest_cents - y.admin_paid_cents
        new_sink_close = new_sink_open + y.sinking_levied_cents + y.sinking_interest_cents - y.sinking_paid_cents
        changes.append({
            "year": y.year,
            "before": {
                "admin_opening_cents": y.admin_opening_cents,
                "sinking_opening_cents": y.sinking_opening_cents,
                "admin_closing_cents": y.admin_closing_cents,
                "sinking_closing_cents": y.sinking_closing_cents,
                "net_balance_cents": y.closing_cents,
            },
            "after": {
                "admin_opening_cents": new_admin_open,
                "sinking_opening_cents": new_sink_open,
                "admin_closing_cents": new_admin_close,
                "sinking_closing_cents": new_sink_close,
                "net_balance_cents": new_admin_close + new_sink_close,
            },
        })
        prev_admin_close, prev_sink_close = new_admin_close, new_sink_close
    return [
        c for c in changes
        if abs(c["after"]["net_balance_cents"] - c["before"]["net_balance_cents"]) > tolerance_cents
        or abs(c["after"]["admin_opening_cents"] - c["before"]["admin_opening_cents"]) > tolerance_cents
        or abs(c["after"]["sinking_opening_cents"] - c["before"]["sinking_opening_cents"]) > tolerance_cents
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Read orchestration (building-agnostic) — never writes to unit_levy_ledger.
# ─────────────────────────────────────────────────────────────────────────────

async def _latest_portal_by_unit(db, building_id: str, year: str) -> tuple[Optional[str], dict[int, int]]:
    """Return (snapshot_date, {raw_lot_int: balance_cents}) from the latest snapshot.

    Keyed by the raw lot integer, not a display-prefixed token: the snapshot stores the
    scraper's bare lot number ("1"), while callers hold a display-formatted unit_number
    ("UA001") — the prefix is a config-driven display value (see core.lots: lot_number is
    canonical/raw, unit_number is a derived, cached display column), never itself a stable
    join key. normalise_unit_token("1") != normalise_unit_token("UA001"), so matching on the
    lot int (extract_lot_int, which strips whatever prefix either side happens to carry) is
    the only comparison that's config-independent and won't silently break if the display
    rules ever change. Found 2026-08-06: every unit was silently reporting "not in portal
    snapshot" despite the snapshot being present and correct, because this used to match on
    normalise_unit_token(lot_number) directly against normalise_unit_token(unit_number).
    """
    snapshot = await db.staging_strata_web_snapshots.find_one(
        {"building_id": building_id, "financial_year": str(year)},
        sort=[("snapshot_date", -1)],
    )
    if not snapshot:
        return None, {}
    by_unit: dict[int, int] = {}
    for entry in snapshot.get("per_unit_balances") or []:
        lot_int = extract_lot_int(entry.get("lot_number"))
        if lot_int is None:
            continue
        by_unit[lot_int] = int(entry.get("balance_cents") or 0)
    return snapshot.get("snapshot_date"), by_unit


async def _annual_control_variance_years(db, building_id: str) -> set[int]:
    """Years whose building-level annual fund-control reconciliation is out of tolerance — reused
    as a suspected-year signal. Best-effort: any failure yields an empty set (signal absent), never
    an error, since it is purely advisory."""
    try:
        from services.reconstruction_generators.annual_fund_reconciliation import (
            compute_annual_fund_reconciliation,
        )
        years = await db.annual_levies.distinct("year", {"building_id": building_id})
        parsed = [int(str(y).split("-")[0]) for y in years if str(y).split("-")[0].isdigit()]
        if not parsed:
            return set()
        warnings: list[str] = []
        lines = await compute_annual_fund_reconciliation(
            db, building_id=building_id,
            financial_year_start=min(parsed), financial_year_end=max(parsed),
            warnings=warnings,
        )
        return {ln.year for ln in lines if ln.within_tolerance is False}
    except Exception:
        return set()


async def run_building_reconciliation(
    db, building_id: str, *, as_of_year: str,
    tolerance_cents: int = DEFAULT_TOLERANCE_CENTS,
    persist: bool = False,
    persist_run_id: Optional[str] = None,
) -> dict[str, Any]:
    """Reconcile every unit in a building. Read-only unless ``persist=True`` (which writes ONLY to
    reconciliation_unit_exceptions — never to unit_levy_ledger)."""
    snapshot_date, portal_by_unit = await _latest_portal_by_unit(db, building_id, as_of_year)
    control_variance_years = await _annual_control_variance_years(db, building_id)

    ledger_docs = await db.unit_levy_ledger.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}}, {"_id": 0},
    ).to_list(None)

    ledger_by_unit: dict[str, list[UnitYearLedger]] = {}
    for doc in ledger_docs:
        unit = str(doc.get("unit_number") or "")
        if not unit:
            continue
        row = UnitYearLedger.from_doc(doc)
        if row is not None:
            ledger_by_unit.setdefault(unit, []).append(row)

    results: list[UnitReconciliation] = []
    for unit, rows in ledger_by_unit.items():
        portal_cents = portal_by_unit.get(extract_lot_int(unit))
        results.append(compute_unit_reconciliation(
            unit_number=unit, years=rows, portal_balance_cents=portal_cents,
            as_of_date=snapshot_date, annual_control_variance_years=control_variance_years,
            tolerance_cents=tolerance_cents,
        ))

    results.sort(key=lambda r: r.unit_number)
    variances = [r for r in results if r.status == "variance"]

    run_id = persist_run_id or str(_uuid.uuid4())
    if persist and variances:
        await _persist_exceptions(db, building_id, variances, run_id, snapshot_date)

    return {
        "building_id": building_id,
        "financial_year": str(as_of_year),
        "portal_snapshot_date": snapshot_date,
        "tolerance_cents": tolerance_cents,
        "run_id": run_id,
        "summary": {
            "units": len(results),
            "reconciled": sum(1 for r in results if r.status == "reconciled"),
            "variance": len(variances),
            "no_portal_data": sum(1 for r in results if r.status == "no_portal_data"),
            "no_ledger_data": sum(1 for r in results if r.status == "no_ledger_data"),
            "chain_break_units": sum(1 for r in results if r.chain_breaks),
            "rechain_would_resolve": sum(1 for r in results if r.rechain_would_resolve),
            "missing_payment": sum(1 for r in results if r.paid_gap_class == "missing_payment"),
            "over_recorded": sum(1 for r in results if r.paid_gap_class == "over_recorded"),
        },
        "units": [r.as_dict() for r in results],
    }


async def _persist_exceptions(
    db, building_id: str, variances: list[UnitReconciliation], run_id: str,
    snapshot_date: Optional[str],
) -> None:
    """Upsert per-unit variance exceptions into reconciliation_unit_exceptions. Idempotent on
    (building_id, unit_number, run_id). Never touches unit_levy_ledger. This is the visible
    reconciliation-exception surface required by the 'surface, never force-match' rule."""
    now = datetime.now(timezone.utc)
    for r in variances:
        await db.reconciliation_unit_exceptions.update_one(
            {"building_id": building_id, "unit_number": r.unit_number, "run_id": run_id},
            {"$set": {
                "building_id": building_id,
                "unit_number": r.unit_number,
                "run_id": run_id,
                "as_of_date": snapshot_date,
                "portal_balance_cents": r.portal_balance_cents,
                "constructed_balance_cents": r.constructed_balance_cents,
                "rechained_balance_cents": r.rechained_balance_cents,
                "variance_cents": r.variance_cents,
                "rechained_variance_cents": r.rechained_variance_cents,
                "paid_gap_cents": r.paid_gap_cents,
                "paid_gap_class": r.paid_gap_class,
                "chain_breaks": [asdict(cb) for cb in r.chain_breaks],
                "row_nonreconciling_years": r.row_nonreconciling_years,
                "rechain_would_resolve": r.rechain_would_resolve,
                "suspected_years": r.suspected_years,
                "status": r.status,
                "is_test_data": False,
                "updated_at": now,
            }, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
