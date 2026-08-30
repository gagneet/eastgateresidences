# @featuretrace:demo_bank — Reconciliation contract: portal/input/bank/match/ledger states, side by side.
# Layer: router
# Data flow: staging_strata_web_snapshots + demo_bank_transactions + match_review_queue
#            + unit_levy_ledger → GET /finance/reconciliation
#            → frontend/src/pages/dashboard/financial/ReconciliationPage.tsx (building-scoped).
# Related: backend/routers/finance.py (ledger KPI contract — this router does NOT feed it)
#          backend/routers/financial_matching.py
#          backend/services/strata_web_balance_inference_service.py
#          backend/services/receivables_resolver.py
# Toggle: bank_feeds_sync_enabled
# Collection: staging_strata_web_snapshots, demo_bank_transactions, match_review_queue, unit_levy_ledger, units
# Tests: tests/backend/test_reconciliation_ui_contract.py
"""
Reconciliation contract — deliberately separate from routers/finance.py.

GAP-FIN-015 blocker 5: there was no operational view showing WHY a portal balance
and the ledger balance disagree, what candidate transactions exist to explain the
gap, and what their match/review/promotion status is. finance.py must stay
Demo-Bank-free (its own guardrail, enforced by
tests/backend/test_finance_input_source_guardrails.py) — this router is the
explicitly-labelled exception: a reconciliation/input-surface reader, not an
operational finance route. Nothing here feeds /finance/kpi-contract or any other
operational balance display.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from database import db
from utils.auth import get_current_building
from utils.permissions import require_feature
from utils.unit_number import extract_lot_int

router = APIRouter(
    prefix="/finance/reconciliation",
    tags=["Finance Reconciliation"],
)

# Reuse the single source of truth for "who may decide/promote queue items" rather
# than duplicating the role list — this page's actions call those same endpoints.
from routers.financial_matching import _DECIDE_ROLES  # noqa: E402

_CANDIDATE_SOURCE_TYPES = {"strata_web_inferred_payment", "synthetic_from_budget", "historical_mongo"}


def _require_decide_role(current_user: dict) -> None:
    """Generated function header.

    Function: _require_decide_role
    Path: backend/routers/finance_reconciliation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = current_user.get("effective_role") or current_user.get("role", "guest")
    if role not in _DECIDE_ROLES:
        raise HTTPException(status_code=403, detail="Strata manager role or above required.")


async def _latest_portal_snapshot(building_id: str, year: str) -> Optional[dict]:
    """Generated function header.

    Function: _latest_portal_snapshot
    Path: backend/routers/finance_reconciliation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await db.staging_strata_web_snapshots.find_one(
        {"building_id": building_id, "financial_year": str(year)},
        sort=[("snapshot_date", -1)],
    )


def _build_candidates(
        tx_docs: List[dict], queue_by_inbox_event_id: Dict[str, dict],
) -> List[Dict[str, Any]]:
    """Pure (no I/O) candidate-row construction from pre-fetched documents."""
    candidates = []
    for tx_doc in tx_docs:
        queue_item_id = None
        match_status = None
        receipt_id = None
        inbox_event_id = tx_doc.get("finance_bank_transaction_ref")
        queue_doc = queue_by_inbox_event_id.get(inbox_event_id) if inbox_event_id else None
        if queue_doc:
            queue_item_id = str(queue_doc.get("_id")) if queue_doc.get("_id") else None
            match_status = queue_doc.get("status")
            receipt_id = queue_doc.get("receipt_id")

        candidates.append({
            "queue_item_id": queue_item_id,
            "source_type": tx_doc.get("source_type"),
            "confidence": tx_doc.get("confidence"),
            "provenance_class": tx_doc.get("provenance_class"),
            "evidence_type": tx_doc.get("evidence_type"),
            "amount_cents": tx_doc.get("amount_cents"),
            "requires_review": bool(tx_doc.get("requires_review") or False),
            "match_status": match_status,
            "receipt_id": receipt_id,
        })
    return candidates


async def _candidates_for_unit(building_id: str, unit_number: str) -> List[Dict[str, Any]]:
    """Single-unit candidate lookup — used only by the single-unit endpoint, where
    the candidate count is bounded by one unit's own transactions, not the whole
    building. The building-wide endpoint batches this instead (see
    get_building_reconciliation) to avoid an N (units) x M (candidates) query
    explosion."""
    tx_docs = await db.demo_bank_transactions.find(
        {
            "building_id": building_id,
            "unit_number": unit_number,
            "source_type": {"$in": list(_CANDIDATE_SOURCE_TYPES)},
            "is_test_data": {"$ne": True},
            # Defense-in-depth, not currently load-bearing: _CANDIDATE_SOURCE_TYPES doesn't
            # today include "historical_reconstruction" (this pipeline's own source_type),
            # so its rows are already excluded above. Added so a future addition to
            # _CANDIDATE_SOURCE_TYPES can never accidentally surface a row explicitly marked
            # as excluded from cash reconciliation.
            "excluded_from_cash_reconciliation": {"$ne": True},
            # A superseded reconstruction batch's rows are archived (is_archived=True), not
            # deleted — must not resurface as live matching candidates.
            "is_archived": {"$ne": True},
        },
        {"_id": 0},
    ).to_list(length=None)

    inbox_event_ids = [t["finance_bank_transaction_ref"] for t in tx_docs if t.get("finance_bank_transaction_ref")]
    queue_by_inbox_event_id: Dict[str, dict] = {}
    if inbox_event_ids:
        queue_docs = await db.match_review_queue.find(
            {"building_id": building_id, "inbox_event_id": {"$in": inbox_event_ids}},
            {"status": 1, "match_type": 1, "receipt_id": 1, "inbox_event_id": 1},
        ).to_list(length=None)
        queue_by_inbox_event_id = {q["inbox_event_id"]: q for q in queue_docs}

    return _build_candidates(tx_docs, queue_by_inbox_event_id)


def _compute_unit_row(
        unit_number: str, ledger_doc: Optional[dict], portal_snapshot: Optional[dict],
        candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Pure (no I/O) row construction from pre-fetched ledger/portal/candidate data."""
    ledger_balance_cents = int(round(float((ledger_doc or {}).get("net_balance") or 0) * 100))

    portal_balance_cents = None
    portal_snapshot_date = None
    if portal_snapshot:
        # Match on the raw lot int, not a display-prefixed token: the snapshot stores the
        # scraper's bare lot number while unit_number carries a config-driven display prefix
        # (core.lots: lot_number is canonical/raw, unit_number is a derived, cached display
        # column) — see portal_ledger_reconciliation_service._latest_portal_by_unit for the
        # same fix and full rationale (found 2026-08-06).
        lot_int = extract_lot_int(unit_number)
        # Guard against None == None: an unparseable unit_number must never match an
        # equally-unparseable/blank snapshot row (found live 2026-08-06 via direct test —
        # without this guard, unit_number="" false-matched a malformed lot_number="" row).
        for entry in (portal_snapshot.get("per_unit_balances") or []) if lot_int is not None else []:
            if extract_lot_int(entry.get("lot_number")) == lot_int:
                portal_balance_cents = int(entry.get("balance_cents") or 0)
                portal_snapshot_date = portal_snapshot.get("snapshot_date")
                break

    delta_cents = None
    requires_reconciliation = False
    if portal_balance_cents is not None:
        delta_cents = portal_balance_cents - ledger_balance_cents
        requires_reconciliation = abs(delta_cents) > 1

    ledger_result = next(
        (
            {"receipt_id": c["receipt_id"], "status": c["match_status"]}
            for c in candidates
            if c["match_status"] == "allocated" and c["receipt_id"]
        ),
        None,
    )

    return {
        "unit_number": unit_number,
        "portal_balance_cents": portal_balance_cents,
        "portal_snapshot_date": portal_snapshot_date,
        "ledger_balance_cents": ledger_balance_cents,
        "delta_cents": delta_cents,
        "requires_reconciliation": requires_reconciliation,
        "candidates": candidates,
        "ledger_result": ledger_result,
    }


async def _build_unit_row(building_id: str, unit_number: str, year: str, portal_snapshot: Optional[dict]) -> Dict[str, Any]:
    """Single-unit fetch + row construction — used only by the single-unit endpoint."""
    ledger = await db.unit_levy_ledger.find_one(
        {"building_id": building_id, "unit_number": unit_number, "year": str(year)},
        {"_id": 0, "net_balance": 1},
    )
    candidates = await _candidates_for_unit(building_id, unit_number)
    return _compute_unit_row(unit_number, ledger, portal_snapshot, candidates)


@router.get("")
async def get_building_reconciliation(
        year: Optional[str] = None,
        current_user: dict = Depends(require_feature("bank_feeds_sync_enabled")),
        building_id: str = Depends(get_current_building),
) -> Dict[str, Any]:
    """Building-wide, per-unit reconciliation view (portal vs ledger vs candidates)."""
    _require_decide_role(current_user)

    if not year:
        from routers.finance import _resolve_default_levy_year
        year = await _resolve_default_levy_year(building_id) or str(date.today().year)

    portal_snapshot = await _latest_portal_snapshot(building_id, year)
    units = await db.units.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}}, {"unit_number": 1, "_id": 0}
    ).to_list(500)
    unit_numbers = [str(u.get("unit_number") or "") for u in units if u.get("unit_number")]

    # Batched to avoid the N (units) x M (candidates) query explosion a naive
    # per-unit loop produces: one query per collection for the whole building,
    # not one per unit. See _build_unit_row()/_candidates_for_unit() for the
    # equivalent (acceptable) per-unit path used by the single-unit endpoint.
    ledger_docs = await db.unit_levy_ledger.find(
        {"building_id": building_id, "unit_number": {"$in": unit_numbers}, "year": str(year)},
        {"_id": 0, "unit_number": 1, "net_balance": 1},
    ).to_list(length=None)
    ledger_by_unit = {d.get("unit_number"): d for d in ledger_docs if d.get("unit_number")}

    # length=None (not a fixed cap like 5000): a building with several years of
    # historical/reconstructed candidates can realistically exceed a hardcoded
    # limit — migration_026 alone generates one row per unit x quarter x year x
    # fund, e.g. 87 units x 4 quarters x 5 years x 2 funds = 3,480 rows for a
    # single building. A hardcoded cap here would silently truncate results,
    # exactly the footgun documented for financial_forecasts.to_list(200).
    tx_docs = await db.demo_bank_transactions.find(
        {
            "building_id": building_id,
            "unit_number": {"$in": unit_numbers},
            "source_type": {"$in": list(_CANDIDATE_SOURCE_TYPES)},
            "is_test_data": {"$ne": True},
            # Defense-in-depth, not currently load-bearing: _CANDIDATE_SOURCE_TYPES doesn't
            # today include "historical_reconstruction" (this pipeline's own source_type),
            # so its rows are already excluded above. Added so a future addition to
            # _CANDIDATE_SOURCE_TYPES can never accidentally surface a row explicitly marked
            # as excluded from cash reconciliation.
            "excluded_from_cash_reconciliation": {"$ne": True},
            # A superseded reconstruction batch's rows are archived (is_archived=True), not
            # deleted — must not resurface as live matching candidates.
            "is_archived": {"$ne": True},
        },
        {"_id": 0},
    ).to_list(length=None)
    tx_docs_by_unit: Dict[str, List[dict]] = {}
    for t in tx_docs:
        tx_docs_by_unit.setdefault(t.get("unit_number"), []).append(t)

    inbox_event_ids = [t["finance_bank_transaction_ref"] for t in tx_docs if t.get("finance_bank_transaction_ref")]
    queue_by_inbox_event_id: Dict[str, dict] = {}
    if inbox_event_ids:
        queue_docs = await db.match_review_queue.find(
            {"building_id": building_id, "inbox_event_id": {"$in": inbox_event_ids}},
            {"status": 1, "match_type": 1, "receipt_id": 1, "inbox_event_id": 1},
        ).to_list(length=None)
        queue_by_inbox_event_id = {q["inbox_event_id"]: q for q in queue_docs}

    rows = [
        _compute_unit_row(
            unit_number,
            ledger_by_unit.get(unit_number),
            portal_snapshot,
            _build_candidates(tx_docs_by_unit.get(unit_number, []), queue_by_inbox_event_id),
        )
        for unit_number in unit_numbers
    ]

    return {
        "building_id": building_id,
        "financial_year": str(year),
        "portal_snapshot_date": (portal_snapshot or {}).get("snapshot_date"),
        "units": rows,
    }


@router.get("/analysis")
async def get_reconciliation_analysis(
        year: Optional[str] = None,
        current_user: dict = Depends(require_feature("bank_feeds_sync_enabled")),
        building_id: str = Depends(get_current_building),
) -> Dict[str, Any]:
    """Deep, building-agnostic portal ⇄ constructed-ledger reconciliation (read-only).

    Extends the plain per-unit portal−ledger delta above with: cross-year chain-integrity, the
    re-chained constructed balance, reverse-engineered implied-paid + paid-gap classification
    (missing_payment vs over_recorded), and a suspected-year ranking. Writes nothing. See
    docs/architecture/strata_web_portal_reconciliation_logic.md.

    Registered BEFORE the /{unit_number} catch-all so the literal path wins the route match.
    """
    _require_decide_role(current_user)

    if not year:
        from routers.finance import _resolve_default_levy_year
        year = await _resolve_default_levy_year(building_id) or str(date.today().year)

    from services.portal_ledger_reconciliation_service import run_building_reconciliation
    return await run_building_reconciliation(db, building_id, as_of_year=str(year))


@router.get("/{unit_number}")
async def get_unit_reconciliation(
        unit_number: str,
        year: Optional[str] = None,
        current_user: dict = Depends(require_feature("bank_feeds_sync_enabled")),
        building_id: str = Depends(get_current_building),
) -> Dict[str, Any]:
    """Single-unit reconciliation detail — same shape as one row of the building-wide view."""
    _require_decide_role(current_user)

    if not year:
        from routers.finance import _resolve_default_levy_year
        year = await _resolve_default_levy_year(building_id) or str(date.today().year)

    portal_snapshot = await _latest_portal_snapshot(building_id, year)
    return await _build_unit_row(building_id, unit_number, year, portal_snapshot)
