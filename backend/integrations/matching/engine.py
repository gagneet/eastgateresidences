"""
backend/integrations/matching/engine.py — MatchingEngine: eight-layer payment-to-lot orchestrator.

# @featuretrace:financial_matching — core matching pipeline orchestrator.
# Layer: service
# Data flow: bank feed → BankTxObserved → MatchingEngine.match() → match_review_queue (building-scoped).
# Related: backend/integrations/matching/layers/ (scoring layers)
#           backend/routers/financial_matching.py (router, review queue API)
#           backend/integrations/envelopes.py (BankTxObserved)

Design:
  - Layers are run L1→L8 in order; the first layer with score ≥ threshold
    wins and triggers auto-allocation.
  - Below threshold: the best score found across all layers is recorded and
    the transaction is queued for manual review.
  - Agency sweep detection precedes layer scoring: a deposit larger than any
    single open levy that originates from a known real-estate agency
    (matched by entity_name substring in description against payer_entities)
    is routed immediately to the review queue as match_type="agency_sweep".
  - Idempotency: inbox_event_id + tenant_id unique index absorbs replays.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from integrations.envelopes import BankTxObserved
from integrations.matching.layers.base import LotCandidate, MatchScore
from integrations.matching.layers.l1_exact_crn import L1ExactCRN
from integrations.matching.layers.l2_npp_e2e import L2NppE2E
from integrations.matching.layers.l3_partial_crn import L3PartialCRN
from integrations.matching.layers.l4_unit_ref_amount_timing import L4UnitRefAmountTiming
from integrations.matching.layers.l5_jw_name_exact_amount import L5JWNameExactAmount
from integrations.matching.layers.l6_surname_fuzzy import L6SurnameFuzzy
from integrations.matching.layers.l7_exact_amount_unique import L7ExactAmountUnique
from integrations.matching.layers.l8_unidentified import L8Unidentified

logger = logging.getLogger(__name__)

_DEFAULT_AUTO_MATCH_THRESHOLD = 0.90
_REVIEW_SLA_HOURS = 24
_AGENCY_SWEEP_SLA_HOURS = 48


@dataclass
class EngineResult:
    """Outcome of a single MatchingEngine.match() call."""

    queue_id: str  # _id of the match_review_queue document
    match_type: str  # "auto" | "agency_sweep" | "review" | "unidentified"
    best_score: float
    best_layer: str
    best_lot: LotCandidate | None
    all_scores: list[MatchScore] = field(default_factory=list)
    is_auto_allocated: bool = False
    is_idempotent_replay: bool = False


_LAYERS = [
    L1ExactCRN(),
    L2NppE2E(),
    L3PartialCRN(),
    L4UnitRefAmountTiming(),
    L5JWNameExactAmount(),
    L6SurnameFuzzy(),
    L7ExactAmountUnique(),
    L8Unidentified(),
]


async def _is_agency_sweep(
        tx: BankTxObserved,
        candidates: list[LotCandidate],
        db: Any,
) -> bool:
    """Return True when the transaction looks like a real-estate agency bulk sweep.

    Criteria (both must be true):
      1. amount_cents exceeds every single lot's open levy — can't be a simple levy.
      2. The transaction description contains an entity_name from payer_entities.
    """
    if not candidates:
        return False

    max_single_levy = max(c.open_levy_cents for c in candidates)
    if tx.amount_cents <= max_single_levy:
        return False

    desc = (tx.description or "").lower()
    if not desc:
        return False

    # payer_entities is a global collection — no building_id scoping.
    entities = await db.payer_entities.find({}, {"entity_name": 1}).to_list(500)
    for ent in entities:
        name = (ent.get("entity_name") or "").lower()
        if name:
            pattern = r'\b' + re.escape(name) + r'\b'
            if re.search(pattern, desc):
                return True

    return False


async def match(
        tx: BankTxObserved,
        candidates: list[LotCandidate],
        db: Any,
        *,
        inbox_event_id: str,
        auto_match_threshold: float = _DEFAULT_AUTO_MATCH_THRESHOLD,
        is_test_data: bool = False,
) -> EngineResult:
    """Run the eight-layer matching pipeline for one bank transaction.

    Args:
        tx: The incoming bank transaction envelope.
        candidates: Pre-loaded lot candidates for the building.
        db: Raw Motor database handle (db._db from TenantScopedDatabase).
        inbox_event_id: Unique ID from integration_inbox; used for idempotency.
        auto_match_threshold: Score >= this triggers auto-allocation.
        is_test_data: Written to match_review_queue document.

    Returns:
        EngineResult describing the outcome and queue document ID.
    """
    tenant_id = tx.tenant_id
    now = datetime.now(timezone.utc)

    # Idempotency guard: if this event was already processed, return existing result.
    existing = await db.match_review_queue.find_one(
        {"tenant_id": tenant_id, "inbox_event_id": inbox_event_id}
    )
    if existing:
        return EngineResult(
            queue_id=str(existing.get("_id", "")),
            match_type=existing.get("match_type", "review"),
            best_score=existing.get("best_score", 0.0),
            best_layer=existing.get("best_layer", ""),
            best_lot=None,
            all_scores=[],
            is_auto_allocated=existing.get("status") == "auto_allocated",
            is_idempotent_replay=True,
        )

    # Agency sweep detection runs before layer scoring.
    if await _is_agency_sweep(tx, candidates, db):
        sla_due = now + timedelta(hours=_AGENCY_SWEEP_SLA_HOURS)
        doc = {
            "tenant_id": tenant_id,
            "building_id": tenant_id,  # required for TenantScopedDatabase auto-injection
            "inbox_event_id": inbox_event_id,
            "status": "pending",
            "match_type": "agency_sweep",
            "tx": tx.model_dump(mode="json"),
            "candidates": [_lot_to_dict(c) for c in candidates],
            "all_scores": [],
            "best_score": 0.0,
            "best_layer": "",
            "best_lot_id": None,
            "sla_due_at": sla_due.isoformat(),
            "created_at": now.isoformat(),
            "decided_at": None,
            "decided_by": None,
            "decision": None,
            "candidates_snapshot": None,
            "is_test_data": is_test_data,
        }
        result = await db.match_review_queue.insert_one(doc)
        logger.info(
            "Agency sweep detected: tenant=%s inbox_event_id=%s amount=%d",
            tenant_id, inbox_event_id, tx.amount_cents,
        )
        return EngineResult(
            queue_id=str(result.inserted_id),
            match_type="agency_sweep",
            best_score=0.0,
            best_layer="",
            best_lot=None,
            all_scores=[],
        )

    # Run L1–L8 in order.
    all_scores: list[MatchScore] = []
    best: MatchScore | None = None

    for layer in _LAYERS:
        ms = layer.score(tx, candidates)
        all_scores.append(ms)

        if best is None or ms.score > best.score:
            best = ms

        if ms.score > auto_match_threshold:
            break  # auto-match threshold exceeded — stop early

    assert best is not None  # L8 always returns a score, so best is always set

    # Determine match_type and status.
    if best.score > auto_match_threshold and best.lot is not None:
        match_type = "auto"
        status = "auto_allocated"
        is_auto = True
        sla_due = now  # already resolved — no SLA countdown needed
    elif best.score == 0.0 or best.lot is None:
        match_type = "unidentified"
        status = "pending"
        is_auto = False
        sla_due = now + timedelta(hours=_REVIEW_SLA_HOURS)
    else:
        match_type = "review"
        status = "pending"
        is_auto = False
        sla_due = now + timedelta(hours=_REVIEW_SLA_HOURS)

    doc = {
        "tenant_id": tenant_id,
        "building_id": tenant_id,  # required for TenantScopedDatabase auto-injection
        "inbox_event_id": inbox_event_id,
        "status": status,
        "match_type": match_type,
        "tx": tx.model_dump(mode="json"),
        "candidates": [_lot_to_dict(c) for c in candidates],
        "all_scores": [_score_to_dict(s) for s in all_scores],
        "best_score": best.score,
        "best_layer": best.layer_name,
        "best_lot_id": best.lot.lot_id if best.lot else None,
        "sla_due_at": sla_due.isoformat(),
        "created_at": now.isoformat(),
        "decided_at": None,
        "decided_by": None,
        "decision": None,
        "candidates_snapshot": None,
        "is_test_data": is_test_data,
    }

    result = await db.match_review_queue.insert_one(doc)
    logger.info(
        "Match result: tenant=%s type=%s score=%.2f layer=%s lot=%s",
        tenant_id, match_type, best.score, best.layer_name,
        best.lot.lot_id if best.lot else "none",
    )

    return EngineResult(
        queue_id=str(result.inserted_id),
        match_type=match_type,
        best_score=best.score,
        best_layer=best.layer_name,
        best_lot=best.lot,
        all_scores=all_scores,
        is_auto_allocated=is_auto,
    )


# ── Serialisation helpers ─────────────────────────────────────────────────────

def _lot_to_dict(lot: LotCandidate) -> dict[str, Any]:
    """Generated function header.

    Function: _lot_to_dict
    Path: backend/integrations/matching/engine.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "lot_id": lot.lot_id,
        "unit_number": lot.unit_number,
        "building_id": lot.building_id,
        "owner_name": lot.owner_name,
        "open_levy_cents": lot.open_levy_cents,
        "due_date": lot.due_date,
        "crn": lot.crn,
        "osko_e2e_id": lot.osko_e2e_id,
    }


def _score_to_dict(ms: MatchScore) -> dict[str, Any]:
    """Generated function header.

    Function: _score_to_dict
    Path: backend/integrations/matching/engine.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "layer": ms.layer_name,
        "lot_id": ms.lot.lot_id if ms.lot else None,
        "score": ms.score,
        "evidence": ms.evidence,
    }
