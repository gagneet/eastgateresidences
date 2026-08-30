"""
backend/routers/financial_matching.py — Match review queue API.

# @featuretrace:financial_matching — REST API for the payment-to-lot match review queue.
# Layer: router
# Data flow: bank feed → MatchingEngine → match_review_queue ← this router ← frontend (building-scoped).
# Related: backend/integrations/matching/engine.py
#           frontend/src/pages/dashboard/financial/MatchingReviewPage.tsx

Endpoints:
  GET  /financial/matching/queue                  — paginated pending/decided items
  GET  /financial/matching/queue/{item_id}        — single item with all candidate scores
  POST /financial/matching/queue/{item_id}/decide — allocate | reject | unidentified
  POST /financial/matching/queue/bulk-decide      — bounded bulk reject|unidentified (no allocate)
  GET  /financial/matching/stats                  — depth, SLA breaches, auto-match rate
  POST /financial/matching/queue/{item_id}/promote-high-confidence — deterministic ledger promotion
  POST /financial/matching/historical-reconstruction/run — GAP-FIN-015 criterion 6: synthesize
       historical levy-payment demo_bank_transactions for an onboarding gap-year range
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from database import db
from services.historical_levy_reconstruction_service import synthesize_historical_levy_payments
from utils.auth import get_current_building
from utils.permissions import require_feature

logger = logging.getLogger(__name__)
router = APIRouter()

# Roles permitted to access or decide on review queue items (includes ec_member per arch docs).
_DECIDE_ROLES = {"super_admin", "strata_admin", "strata_manager", "ec_member"}

# Map decision actions to stable stored-status values (frontend contract uses past-tense).
_ACTION_TO_STATUS: dict[str, str] = {
    "allocate": "allocated",
    "reject": "rejected",
    "unidentified": "unidentified",
}


# ── Request / Response models ─────────────────────────────────────────────────

class DecideRequest(BaseModel):
    action: str = Field(..., pattern=r"^(allocate|reject|unidentified)$")
    lot_id: Optional[str] = None  # required when action == "allocate"
    amount_cents: Optional[int] = None  # required when action == "allocate"
    notes: Optional[str] = None


class QueueItem(BaseModel):
    item_id: str
    tenant_id: str
    inbox_event_id: str
    status: str
    match_type: str
    best_score: float
    best_layer: str
    best_lot_id: Optional[str]
    sla_due_at: str
    created_at: str
    decided_at: Optional[str]
    decided_by: Optional[str]
    decision: Optional[dict]
    tx: dict
    candidates: list
    all_scores: list


class BulkDecideRequest(BaseModel):
    """Bounded bulk close-out of pending queue items.

    Deliberately supports only reject|unidentified — never allocate. Allocation posts
    to the Postgres ledger and mirrors into Mongo levy_payments against the CURRENT
    default levy year, so bulk-allocating historical backfill rows would double-count
    payments already reflected in ledger closing balances (verified 2026-07-03 on
    East Gate: queue item TH078 $1,573.56 already exists as a confirmed levy_payments
    row). See docs/runbooks/east_gate_match_queue_backlog_clearance_2026_07_03.md.
    """
    action: str = Field(..., pattern=r"^(reject|unidentified)$")
    notes: str = Field(..., min_length=10, max_length=2000)
    direction: Optional[str] = Field(None, pattern=r"^(inflow|outflow)$")
    match_type: Optional[str] = Field(None, pattern=r"^(unidentified|review|agency_sweep|auto)$")
    max_items: int = Field(500, ge=1, le=1000)
    dry_run: bool = True


class BulkDecideResponse(BaseModel):
    dry_run: bool
    action: str
    matched_total: int          # pending rows matching the filter (before max_items cap)
    selected: int               # rows examined in this call (≤ max_items)
    decided: int                # rows actually updated (0 when dry_run)
    identified_lot_count: int   # selected rows whose description names a known unit
    bulk_operation_id: Optional[str] = None
    sample: list[dict] = Field(default_factory=list)  # first 10 selected items


class QueueStats(BaseModel):
    queue_depth: int
    sla_breach_count: int
    auto_match_rate: float  # 0.0–1.0 over last 7 days
    total_last_7_days: int
    auto_allocated_last_7_days: int


class HistoricalReconstructionRequest(BaseModel):
    """GAP-FIN-015 criterion 6: synthesize historical levy-payment demo_bank_transactions
    for a building's onboarding gap years (years with an annual_levies budget but no real
    payment history). Building-agnostic and idempotent — safe to re-run."""
    from_year: int = Field(..., ge=2000, le=2100)
    to_year: int = Field(..., ge=2000, le=2100)
    dry_run: bool = True


class HistoricalReconstructionResponse(BaseModel):
    building_id: str
    dry_run: bool
    years_range: str
    units_processed: int
    inserted: int
    already_existed: int
    years_skipped: int
    by_strata_year: dict[str, int]


# ── GET /financial/matching/queue ─────────────────────────────────────────────

@router.get("/financial/matching/queue", response_model=list[QueueItem])
async def list_queue(
        item_status: str = Query("pending", alias="status"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(require_feature("financial_integration_layer_v2")),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: list_queue
    Path: backend/routers/financial_matching.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in _DECIDE_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only managers may access the matching queue.")
    items = await db.match_review_queue.find(
        {"status": item_status, "is_test_data": {"$ne": True}},
        sort=[("sla_due_at", 1)],
    ).skip((page - 1) * page_size).limit(page_size).to_list(page_size)

    return [_doc_to_item(i) for i in items]


# ── GET /financial/matching/queue/{item_id} ───────────────────────────────────

@router.get("/financial/matching/queue/{item_id}", response_model=QueueItem)
async def get_queue_item(
        item_id: str,
        current_user: dict = Depends(require_feature("financial_integration_layer_v2")),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_queue_item
    Path: backend/routers/financial_matching.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in _DECIDE_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only managers may access the matching queue.")
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        oid = ObjectId(item_id)
    except (InvalidId, Exception):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    doc = await db.match_review_queue.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    if doc.get("building_id") != building_id and doc.get("tenant_id") != building_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    return _doc_to_item(doc)


# ── POST /financial/matching/queue/{item_id}/decide ───────────────────────────

@router.post("/financial/matching/queue/{item_id}/decide")
async def decide_queue_item(
        item_id: str,
        payload: DecideRequest,
        current_user: dict = Depends(require_feature("financial_integration_layer_v2")),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: decide_queue_item
    Path: backend/routers/financial_matching.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from bson import ObjectId
    from bson.errors import InvalidId

    # Only managers may submit decisions.
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in _DECIDE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only strata managers and above may decide queue items.",
        )

    try:
        oid = ObjectId(item_id)
    except (InvalidId, Exception):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    doc = await db.match_review_queue.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    if doc.get("building_id") != building_id and doc.get("tenant_id") != building_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    is_allocate_posting_retry = (
        payload.action == "allocate"
        and doc.get("status") == "posting"
        and (doc.get("decision") or {}).get("action") == "allocate"
        and (doc.get("decision") or {}).get("lot_id") == payload.lot_id
        and (doc.get("decision") or {}).get("amount_cents") == payload.amount_cents
    )
    if doc.get("status") not in ("pending",) and not is_allocate_posting_retry:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Item already decided (status={doc.get('status')!r}). "
                   "Each review item can only be decided once.",
        )

    # Validate action-specific fields.
    if payload.action == "allocate":
        if not payload.lot_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="lot_id is required for action='allocate'",
            )
        if payload.amount_cents is None or payload.amount_cents <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="amount_cents must be a positive integer for action='allocate'",
            )

    now = datetime.now(timezone.utc).isoformat()
    decided_by = current_user.get("email") or str(current_user.get("_id", "unknown"))

    # Freeze the candidates snapshot at decision time.
    candidates_snapshot = doc.get("candidates", [])

    decision = {
        "action": payload.action,
        "lot_id": payload.lot_id,
        "amount_cents": payload.amount_cents,
        "notes": payload.notes,
    }

    receipt_id = None
    if payload.action == "allocate":
        # Claim before posting so concurrent reviewers cannot both create
        # receipts. A pre-existing status="posting" with the same allocation
        # payload is treated as a retry after a crash between the idempotent
        # Postgres receipt write and Mongo queue finalization.
        if not is_allocate_posting_retry:
            claim = await db.match_review_queue.update_one(
                {"_id": oid, "status": "pending"},
                {"$set": {
                    "status": "posting",
                    "posting_started_at": now,
                    "posting_by": decided_by,
                    "decision": decision,
                    "candidates_snapshot": candidates_snapshot,
                }},
            )
            if claim.modified_count != 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Item is already being decided or was decided by another request.",
                )

        # This is the ONLY authorised path for writing to the accounting ledger.
        # The service writes finance.receipts + finance.journal_entries (Postgres).
        receipt_id = await _post_payment_to_ledger(
            doc=doc,
            lot_id=payload.lot_id,
            amount_cents=payload.amount_cents,
            decided_by=decided_by,
            current_user=current_user,
            building_id=building_id,
            item_id=item_id,
        )
        if not receipt_id:
            # Keep the item retryable. The failed attempt metadata is deliberately
            # retained for audit/debugging, but status returns to pending so a
            # human can approve again after the underlying Postgres/mapping issue
            # is fixed.
            await db.match_review_queue.update_one(
                {"_id": oid, "status": "posting"},
                {"$set": {
                    "status": "pending",
                    "last_post_failed_at": datetime.now(timezone.utc).isoformat(),
                    "last_post_failed_by": decided_by,
                }},
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Allocation was not recorded in the ledger; the queue item remains pending.",
            )

        await db.match_review_queue.update_one(
            {"_id": oid, "status": "posting"},
            {"$set": {
                "status": _ACTION_TO_STATUS[payload.action],
                "decided_at": now,
                "decided_by": decided_by,
                "receipt_id": str(receipt_id),
                "decision": decision,
                "candidates_snapshot": candidates_snapshot,
            }},
        )
    else:
        await db.match_review_queue.update_one(
            {"_id": oid, "status": "pending"},
            {"$set": {
                "status": _ACTION_TO_STATUS[payload.action],
                "decided_at": now,
                "decided_by": decided_by,
                "decision": decision,
                "candidates_snapshot": candidates_snapshot,
            }},
        )

    # Emit immutable MatchDecisionRecorded event only after the queue item is in
    # its final decision state. For allocate, this means the ledger post has
    # already succeeded; failed posts leave no terminal decision event.
    event_doc = {
        "event_id": str(uuid.uuid4()),
        "event_type": "MatchDecisionRecorded",
        "tenant_id": doc.get("tenant_id"),
        "building_id": doc.get("building_id"),
        "queue_item_id": item_id,
        "inbox_event_id": doc.get("inbox_event_id"),
        "action": payload.action,
        "lot_id": payload.lot_id,
        "amount_cents": payload.amount_cents,
        "notes": payload.notes,
        "candidates_snapshot": candidates_snapshot,
        "decided_at": now,
        "decided_by": decided_by,
        **({"receipt_id": str(receipt_id)} if receipt_id else {}),
    }
    await db.event_log.insert_one(event_doc)
    logger.info(
        "MatchDecisionRecorded: tenant=%s item=%s action=%s lot=%s by=%s",
        doc.get("tenant_id"), item_id, payload.action, payload.lot_id, decided_by,
    )

    return {
        "status": "ok",
        "action": payload.action,
        "item_id": item_id,
        **({"receipt_id": str(receipt_id)} if receipt_id else {}),
    }


# ── POST /financial/matching/queue/bulk-decide ────────────────────────────────

# Explicit "LOT TH078" / "Unit UA070" tokens as written by the Demo Bank backfill and
# DEFT narratives. Requires the LOT/UNIT keyword so date fragments ("12/02/2025") and
# invoice numbers are never mistaken for a unit code. The letter prefix is also
# mandatory: purely numeric unit numbers ("Unit 12") are deliberately NOT extracted,
# because bare digits after LOT/UNIT are too often invoice or reference numbers. This
# annotation is audit metadata only — a None extraction never blocks a decision.
_LOT_CODE_RE = re.compile(r"\b(?:LOT|UNIT)\s+([A-Z]{1,3}\d{1,4})\b", re.IGNORECASE)


def _extract_known_lot_code(description: str, known_units: set[str]) -> Optional[str]:
    """Return the first LOT/UNIT code in description that names a real unit, else None."""
    for m in _LOT_CODE_RE.finditer(description or ""):
        code = m.group(1).upper()
        if code in known_units:
            return code
    return None


@router.post("/financial/matching/queue/bulk-decide", response_model=BulkDecideResponse)
async def bulk_decide_queue_items(
        payload: BulkDecideRequest,
        current_user: dict = Depends(require_feature("financial_integration_layer_v2")),
        building_id: str = Depends(get_current_building),
):
    """Close out many pending queue items in one call (reject | unidentified only).

    Built for backlog clearance where the disposition is known in bulk — e.g. outflow
    (expense) rows that can never match a lot levy, or historical backfill inflows whose
    payments are already reflected in ledger closing balances. Runs as dry_run=True by
    default; the caller must explicitly pass dry_run=false to mutate. Every real decision
    is race-guarded on status="pending" and emits a MatchDecisionRecorded event carrying
    a shared bulk_operation_id.
    """
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in _DECIDE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only strata managers and above may decide queue items.",
        )

    # building_id scoping is injected by TenantScopedDatabase on every call below.
    query: dict = {"status": "pending", "is_test_data": {"$ne": True}}
    if payload.match_type:
        query["match_type"] = payload.match_type
    if payload.direction == "inflow":
        query["tx.amount_cents"] = {"$gt": 0}
    elif payload.direction == "outflow":
        query["tx.amount_cents"] = {"$lt": 0}

    matched_total = await db.match_review_queue.count_documents(query)
    docs = await db.match_review_queue.find(
        query,
        {"tx.amount_cents": 1, "tx.description": 1, "tx.account_ref": 1,
         "inbox_event_id": 1, "match_type": 1, "tenant_id": 1, "building_id": 1},
        sort=[("created_at", 1)],
    ).limit(payload.max_items).to_list(payload.max_items)

    # Known unit numbers for this building, for audit-trail lot identification.
    # Capped at 2000: buildings beyond that would get incomplete annotations, never
    # wrong decisions (extraction is metadata only).
    unit_rows = await db.units.find({}, {"unit_number": 1}).to_list(2000)
    known_units = {str(u.get("unit_number", "")).upper() for u in unit_rows if u.get("unit_number")}

    annotated = []
    for doc in docs:
        tx = doc.get("tx", {})
        annotated.append({
            "item_id": str(doc["_id"]),
            "amount_cents": tx.get("amount_cents"),
            "account_ref": tx.get("account_ref"),
            "description": (tx.get("description") or "")[:120],
            "extracted_lot_code": _extract_known_lot_code(tx.get("description") or "", known_units),
            "_doc": doc,
        })
    identified_lot_count = sum(1 for a in annotated if a["extracted_lot_code"])
    sample = [{k: v for k, v in a.items() if k != "_doc"} for a in annotated[:10]]

    if payload.dry_run:
        return BulkDecideResponse(
            dry_run=True,
            action=payload.action,
            matched_total=matched_total,
            selected=len(annotated),
            decided=0,
            identified_lot_count=identified_lot_count,
            sample=sample,
        )

    bulk_operation_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    decided_by = current_user.get("email") or str(current_user.get("_id", "unknown"))

    events: list[dict] = []
    decided = 0
    # Unlike the single-item decide path, bulk decisions do NOT freeze
    # candidates_snapshot: the snapshot exists to preserve what a human reviewer saw,
    # and copying up to 87 candidates onto 1,000 queue docs + 1,000 events per call
    # would bloat both collections for decisions that never weighed candidates. The
    # original candidates array stays untouched on each queue doc.
    for a in annotated:
        doc = a["_doc"]
        decision = {
            "action": payload.action,
            "lot_id": None,
            "amount_cents": None,
            "notes": payload.notes,
            "bulk_operation_id": bulk_operation_id,
            "extracted_lot_code": a["extracted_lot_code"],
        }
        # Race guard: only flip items still pending; concurrently decided items are skipped.
        result = await db.match_review_queue.update_one(
            {"_id": doc["_id"], "status": "pending"},
            {"$set": {
                "status": _ACTION_TO_STATUS[payload.action],
                "decided_at": now,
                "decided_by": decided_by,
                "decision": decision,
            }},
        )
        if result.modified_count != 1:
            continue
        decided += 1
        events.append({
            "event_id": str(uuid.uuid4()),
            "event_type": "MatchDecisionRecorded",
            "tenant_id": doc.get("tenant_id"),
            "building_id": doc.get("building_id"),
            "queue_item_id": a["item_id"],
            "inbox_event_id": doc.get("inbox_event_id"),
            "action": payload.action,
            "lot_id": None,
            "amount_cents": None,
            "notes": payload.notes,
            "bulk_operation_id": bulk_operation_id,
            "extracted_lot_code": a["extracted_lot_code"],
            "decided_at": now,
            "decided_by": decided_by,
        })

    if events:
        await db.event_log.insert_many(events)

    logger.info(
        "Bulk match decision: building=%s action=%s decided=%d/%d matched_total=%d op=%s by=%s",
        building_id, payload.action, decided, len(annotated), matched_total,
        bulk_operation_id, decided_by,
    )

    return BulkDecideResponse(
        dry_run=False,
        action=payload.action,
        matched_total=matched_total,
        selected=len(annotated),
        decided=decided,
        identified_lot_count=identified_lot_count,
        bulk_operation_id=bulk_operation_id,
        sample=sample,
    )


# ── GET /financial/matching/stats ─────────────────────────────────────────────

@router.get("/financial/matching/stats", response_model=QueueStats)
async def get_stats(
        current_user: dict = Depends(require_feature("financial_integration_layer_v2")),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_stats
    Path: backend/routers/financial_matching.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in _DECIDE_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only managers may access the matching queue.")
    now = datetime.now(timezone.utc)
    cutoff_7d = (now - timedelta(days=7)).isoformat()
    now_iso = now.isoformat()

    # Performance Optimization⚡: Parallelize independent counts and aggregation.
    # This reduces sequential database round-trips from 3 to 1.
    pipeline = [
        {"$match": {
            "created_at": {"$gte": cutoff_7d},
            "is_test_data": {"$ne": True},
        }},
        {"$group": {
            "_id": "$match_type",
            "count": {"$sum": 1},
        }},
    ]

    tasks = [
        db.match_review_queue.count_documents(
            {"status": "pending", "is_test_data": {"$ne": True}}
        ),
        db.match_review_queue.count_documents(
            {
                "status": "pending",
                "sla_due_at": {"$lt": now_iso},
                "is_test_data": {"$ne": True},
            }
        ),
        db.match_review_queue.aggregate(pipeline).to_list(100)
    ]

    results = await asyncio.gather(*tasks)

    queue_depth = results[0]
    sla_breach_count = results[1]
    agg_rows = results[2]

    # Auto-match rate over last 7 days.
    type_counts: dict[str, int] = {}
    for row in agg_rows:
        type_counts[row["_id"]] = row["count"]

    total = sum(type_counts.values())
    auto_count = type_counts.get("auto", 0)
    auto_rate = (auto_count / total) if total > 0 else 0.0

    return QueueStats(
        queue_depth=queue_depth,
        sla_breach_count=sla_breach_count,
        auto_match_rate=round(auto_rate, 4),
        total_last_7_days=total,
        auto_allocated_last_7_days=auto_count,
    )


# ── Ledger posting ────────────────────────────────────────────────────────────


async def _resolve_bank_transaction_id(
        *,
        session,
        scheme_id,
        inbox_event_id,
        provider_txn_id,
        building_id: str,
        item_id: str,
):
    """Resolve the finance.bank_transactions UUID a receipt may legally point at.

    Returns a UUID that is known to exist in finance.bank_transactions for this
    scheme, or None. Never returns an id it has not verified — the caller writes
    it into finance.receipts.bank_transaction_id, which is a foreign key, and an
    unverified id turns a routine allocation into a 502.

    Two sources are tried, in order of directness:

    1. ``inbox_event_id`` — correct for producers whose inbox event IS the bank
       transaction (the real bank-feed path).
    2. ``demo_bank_transactions.finance_bank_transaction_ref`` looked up by the
       provider's own transaction id — correct for everything that reaches the
       ledger through Demo Bank, which is every non-bank-feed input (CLAUDE.md:
       "Demo Bank is the only door into finance"). The inferred-payment and
       reconstruction paths mint their own inbox event ids, so source 1 cannot
       work for them and this is the only link that exists.

    Both are verified against finance.bank_transactions before being returned.
    """
    from uuid import UUID

    from sqlalchemy import text

    async def _exists(candidate) -> bool:
        row = await session.execute(
            text(
                "SELECT 1 FROM finance.bank_transactions "
                "WHERE bank_transaction_id = :btid AND scheme_id = :sid"
            ),
            {"btid": str(candidate), "sid": str(scheme_id)},
        )
        return row.first() is not None

    if inbox_event_id:
        try:
            candidate = UUID(str(inbox_event_id))
        except ValueError:
            candidate = None
        if candidate is not None and await _exists(candidate):
            return candidate

    if provider_txn_id:
        # `is_archived` matters here, it is not boilerplate: an archived Demo Bank
        # row has been SUPERSEDED (East Gate's 2021-2026 rebuild archived a whole
        # generation of synthetic rows). Linking a new receipt to a superseded
        # transaction's finance_bank_transaction_ref would attach today's payment to
        # evidence that was explicitly retired.
        demo_row = await db.demo_bank_transactions.find_one(
            {
                "building_id": building_id,
                "external_transaction_id": provider_txn_id,
                "is_archived": {"$ne": True},
            },
            {"_id": 0, "finance_bank_transaction_ref": 1},
        )
        ref = (demo_row or {}).get("finance_bank_transaction_ref")
        if ref:
            try:
                candidate = UUID(str(ref))
            except ValueError:
                candidate = None
            if candidate is not None and await _exists(candidate):
                return candidate

    logger.warning(
        "_post_payment_to_ledger: no finance.bank_transactions row for "
        "inbox_event_id=%r provider_txn_id=%r (item=%s building=%s) — posting the "
        "receipt with bank_transaction_id=NULL; the provider id is kept as "
        "external_reference",
        inbox_event_id, provider_txn_id, item_id, building_id,
    )
    return None



# @featuretrace:levy — Demo Bank/DEFT payments post to Postgres AND mirror into Mongo levy_payments.
# Layer: router
# Data flow: match_review_queue -> decide(allocate) -> _resolve_bank_transaction_id()
#            -> demo_bank_transactions.finance_bank_transaction_ref + finance.bank_transactions
#            -> _post_payment_to_ledger() -> finance.receipts (Postgres)
#            + db.levy_payments / _upsert_ledger_for_payment (Mongo mirror) (building-scoped).
# Related: backend/routers/finance.py (_upsert_ledger_for_payment — shared ledger-rollup helper)
#           backend/routers/reconciliation.py (import_deft_csv — the precedent this mirror follows)
#           backend/integrations/demo_bank/provider.py (provider_txn_id == external_transaction_id)
# Table: finance.receipts, finance.journal_entries, finance.bank_transactions (FK target, read-only)
# Collection: levy_payments, unit_levy_ledger, demo_bank_transactions (read-only, FK resolution)

# IMPORTANT: the Mongo mirror is best-effort/non-fatal (own try/except) so a Mongo write failure
#            does not erase the fact that the Postgres receipt already committed — same convention
#            as the allocate_payment block above. If get_latest_levy_year(building_id) finds no
#            annual_levies row, the mirror is skipped (logged) and only the Postgres receipt exists
#            for that payment until an annual_levies doc is created for the building's current year.
# ADDITIONALLY: this endpoint requires require_feature("financial_integration_layer_v2"). Verified
#            2026-07-03 via `SELECT core.feature_toggle_resolved(scheme_id, key)` (the resolver
#            require_feature ultimately calls) that this toggle IS enabled for East Gate (13195) via
#            a per-scheme override in core.feature_toggle_overrides — the endpoint is reachable for
#            strata_manager/strata_admin/ec_member users today, which is exactly why this mirror fix
#            was necessary rather than theoretical. See merge_candidates.md A6b.
#            Also related: backend/routers/demo_bank.py,
#            frontend/src/pages/dashboard/financial/MatchingReviewPage.tsx.
async def _post_payment_to_ledger(
        *,
        doc: dict,
        lot_id: str,
        amount_cents: int,
        decided_by: str,
        current_user: dict,
        building_id: str,
        item_id: str,
) -> Optional[str]:
    """Call FinancialCoreService.record_payment() for an approved match decision.

    Returns the receipt_id string on success, None on failure. Callers must keep
    the queue item in a retryable state until this returns a receipt_id; otherwise
    a terminal match decision can exist without a matching ledger receipt.
    """
    try:
        from uuid import UUID
        from db_postgres.repos import config_repo
        from db_postgres.session import async_session_context, set_tenant
        from services.financial_core import get_financial_core_service
        from services.financial_core.domain.entities import RecordPaymentCommand, SchemeRef, PaymentChannel

        scheme = await config_repo.resolve_scheme_context(building_id)
        if scheme is None:
            logger.warning(
                "FinancialCoreService skip: no Postgres scheme for building=%s item=%s",
                building_id, item_id,
            )
            return None

        # BankTxObserved tx dict is stored on the queue doc.
        tx = doc.get("tx", {})
        # Reconstruction provenance (2026-07-17 audit fix): tx is
        # BankTxObserved.model_dump(), so tx["metadata"] carries whatever
        # DemoBankFeed._doc_to_envelope() populated (Phase 0B) for
        # reconstructed rows — None/absent for every real observed payment.
        tx_metadata = tx.get("metadata") or {}
        reconstruction_batch_id_str = tx_metadata.get("reconstruction_batch_id")
        received_on_str = tx.get("occurred_at", "")[:10] if tx.get("occurred_at") else None
        if not received_on_str:
            logger.warning("_post_payment_to_ledger: no occurred_at on tx for item=%s", item_id)
            return None

        from datetime import date as date_type
        received_on = date_type.fromisoformat(received_on_str)

        # Resolve lot_id to a Postgres UUID and post to the ledger in the same
        # session — a prior version of this function opened a session for the
        # lot_id lookup, closed it, then constructed FinancialCoreService()
        # with no arguments (missing the required ledger_repo/outbox_port),
        # which raised TypeError on every call and was silently swallowed by
        # this function's outer try/except, so no "allocate" decision ever
        # actually posted to the ledger. Fixed 2026-07-17 audit: single
        # session, built via the canonical get_financial_core_service()
        # factory (services/financial_core/__init__.py) so this can't drift
        # from the adapter's own construction pattern again.
        async with async_session_context() as session:
            await set_tenant(session, str(scheme["tenant_id"]))
            # get_lot_id_by_number(), not a raw lot_number-only query: core.lots has
            # BOTH a lot_number column (the legal lot number, e.g. "87") and a
            # separate unit_number column (the Mongo-style display label, e.g.
            # "TH087"/"UA067") -- they are NOT always the same string. The raw query
            # this replaced only checked lot_number, so any unit whose Mongo
            # unit_number doesn't equal its Postgres lot_number 1:1 (confirmed live
            # 2026-08-01: every East Gate lot_number is a bare digit string like "87",
            # never "TH087") silently failed this lookup on every single payment,
            # logging a misleading "not found in core.lots" as if the lot itself were
            # missing rather than just being looked up by the wrong column. This is a
            # generic bug -- any building using a prefixed unit_number display
            # convention (this repo function's own docstring already documents "the
            # Acme demo building" as exactly this case) would hit the same failure.
            from db_postgres.repos.ownership_repo import get_lot_id_by_number
            pg_lot_id_str = await get_lot_id_by_number(session, str(scheme["scheme_id"]), lot_id)

            if not pg_lot_id_str:
                logger.warning(
                    "_post_payment_to_ledger: lot/unit_number=%s not found in core.lots for building=%s",
                    lot_id, building_id,
                )
                return None

            pg_lot_id = UUID(pg_lot_id_str)

            # Resolve the actual owning party as of the payment date — core.lots and
            # core.parties are distinct tables (a lot is the unit/title, a party is the
            # owner), so the lot's own UUID is never a valid payer_party_id. Found live
            # 2026-07-24 (East Gate 13195 individual queue review): this previously
            # passed pg_lot_id straight through as payer_party_id, which violates
            # journal_lines_party_id_fkey on every single allocate decision — the same
            # class of "never actually exercised end-to-end" bug this function's own
            # docstring already documents once (the 2026-07-17 FinancialCoreService()
            # zero-arg fix). Date-scoped (not "current owner") because reconstructed
            # historical payments must resolve to whoever owned the lot AT THE TIME, and
            # ownership demonstrably changes over a lot's history (verified: East Gate
            # lot 72 alone has 3 ownership_periods rows spanning 1900-2025 and 2026+).
            owner_result = await session.execute(
                __import__("sqlalchemy").text(
                    "SELECT owner_party_id::text FROM core.ownership_periods "
                    "WHERE lot_id = :lot_id AND is_primary_owner = TRUE "
                    # recorded_to IS NULL excludes bitemporally-retracted rows (system-time
                    # corrections — see ownership_repo.py::close_ownership_period()'s
                    # retraction branch, added 2026-07-24). Without this, a retracted row
                    # can still satisfy valid_from/valid_to and race a LIMIT 1 with no
                    # ORDER BY against the row that actually superseded it — found 2026-07-25
                    # auditing the prior day's retraction fix, before any real payment ever
                    # happened to exercise it (verified: no finance.receipts row for lot 78
                    # has received_on >= the retraction date).
                    "AND recorded_to IS NULL "
                    "AND valid_from <= :received_on "
                    "AND (valid_to IS NULL OR valid_to >= :received_on) "
                    "LIMIT 1"
                ),
                {"lot_id": str(pg_lot_id), "received_on": received_on},
            )
            owner_row = owner_result.first()
            if not owner_row:
                logger.warning(
                    "_post_payment_to_ledger: no primary owner found for lot_number=%s "
                    "as of %s (building=%s) — cannot resolve payer_party_id",
                    lot_id, received_on, building_id,
                )
                return None
            payer_party_id = UUID(owner_row.owner_party_id)

            user_id = current_user.get("id") or current_user.get("_id") or "system"
            try:
                approver_id = UUID(str(user_id))
            except ValueError:
                approver_id = UUID(int=0)  # fallback for non-UUID user IDs during Mongo era

            idempotency_key = f"match-decision:{item_id}:allocate"

            svc = get_financial_core_service(session)
            scheme_ref = SchemeRef(
                tenant_id=UUID(str(scheme["tenant_id"])),
                scheme_id=UUID(str(scheme["scheme_id"])),
            )
            try:
                reconstruction_batch_id = (
                    UUID(reconstruction_batch_id_str) if reconstruction_batch_id_str else None
                )
            except ValueError:
                logger.warning(
                    "_post_payment_to_ledger: reconstruction_batch_id %r on tx metadata "
                    "is not a valid UUID (item=%s) — dropping, not blocking the payment",
                    reconstruction_batch_id_str, item_id,
                )
                reconstruction_batch_id = None
            inbox_event_id = doc.get("inbox_event_id")
            # finance.receipts.bank_transaction_id is a REAL foreign key into
            # finance.bank_transactions. A UUID that merely PARSES is not a valid
            # value for it, and this code used to assume the two were the same
            # thing: inbox_event_id is the id of the ingestion inbox event, which
            # for some producers happens to equal the bank transaction id and for
            # others does not. Every queue item created by the Strata Web
            # balance-delta inference path carries an inbox_event_id with no
            # bank_transactions row behind it (verified live 2026-08-28: 39 of 39
            # pending East Gate items), so record_payment() raised
            # ForeignKeyViolationError, _post_payment_to_ledger() returned None,
            # and the endpoint answered 502 on every single allocate — the
            # matching queue could not be cleared at all.
            #
            # Resolve the real id instead of guessing: the inbox event id if it
            # actually names a bank transaction, else the Demo Bank row's own
            # finance_bank_transaction_ref (Demo Bank records it when it syncs a
            # transaction into finance.bank_transactions), else NULL. The column
            # is nullable and external_reference below preserves the provider's
            # own id either way, so an unresolvable link degrades to a receipt
            # with no bank link rather than to a failed allocation.
            bank_transaction_id = await _resolve_bank_transaction_id(
                session=session,
                scheme_id=UUID(str(scheme["scheme_id"])),
                inbox_event_id=inbox_event_id,
                provider_txn_id=tx.get("provider_txn_id"),
                building_id=building_id,
                item_id=item_id,
            )
            external_reference = (
                tx.get("provider_txn_id")
                or tx.get("description")
                or (str(inbox_event_id) if inbox_event_id else None)
            )
            cmd = RecordPaymentCommand(
                scheme_ref=scheme_ref,
                payer_party_id=payer_party_id,
                lot_id=pg_lot_id,
                channel=PaymentChannel.BANK_TRANSFER,
                received_on=received_on,
                amount_cents=amount_cents,
                bank_transaction_id=bank_transaction_id,
                external_reference=external_reference,
                idempotency_key=idempotency_key,
                is_test_data=doc.get("is_test_data", False),
                posted_by=approver_id,
                approved_by=approver_id,
                metadata={"queue_item_id": item_id, "decided_by": decided_by, **tx_metadata},
                reconstruction_batch_id=reconstruction_batch_id,
            )
            receipt = await svc.record_payment(cmd)
            logger.info(
                "FinancialCoreService.record_payment: receipt=%s lot=%s amount=%d building=%s",
                receipt.receipt_id, lot_id, amount_cents, building_id,
            )

            # Allocate the receipt across open levy items immediately.
            # This closes the AR balance for the lot — partial payments are absorbed
            # in levy→interest→costs order. Failures are non-fatal (logged only).
            try:
                from services.financial_core.domain.entities import AllocatePaymentCommand
                alloc_cmd = AllocatePaymentCommand(
                    scheme_ref=scheme_ref,
                    receipt_id=receipt.receipt_id,
                )
                allocations = await svc.allocate_payment(alloc_cmd)
                logger.info(
                    "FinancialCoreService.allocate_payment: %d allocations for receipt=%s",
                    len(allocations), receipt.receipt_id,
                )
            except Exception as alloc_exc:
                logger.error(
                    "allocate_payment failed for receipt=%s building=%s: %s",
                    receipt.receipt_id, building_id, alloc_exc,
                )

        # Mirror into Mongo levy_payments + unit_levy_ledger so the payment is visible on
        # every current dashboard (FinancePage, CollectionRatePage, OwnerDashboard,
        # MyFinancesPage, LevyPaymentPage, UnitFinanceDetailPage, ArrearsRecoveryPage,
        # BIAnalyticsPage all read Mongo, not Postgres, for buildings pending PG-read cutover).
        # Mirrors the same insert+ledger-update shape reconciliation.py::import_deft_csv() uses
        # for its own non-finance.py write path. Non-fatal by the same convention as the
        # allocate_payment block above — a Mongo mirror failure must not erase the fact that
        # the Postgres receipt (the source of truth once PG reads are enabled) already committed.
        try:
            from uuid import uuid4
            from routers.finance import _upsert_ledger_for_payment
            from utils.finance_helpers import resolve_levy_year_for_date

            unit_number = lot_id
            mongo_amount = round(amount_cents / 100, 2)
            # resolve_levy_year_for_date(building_id, received_on) -- NOT
            # _resolve_default_levy_year(building_id) (which always returns the
            # building's CURRENT levy year, no matter when the transaction actually
            # happened). Fixed 2026-08-01: every historical-dated allocate decision
            # was mirroring into the current year's unit_levy_ledger doc regardless of
            # the transaction's own date, because the old call ignored received_on
            # entirely -- confirmed live against East Gate: 2,483 real 2021-2025
            # transactions were misfiled into 2026 this way (matching the ~8x
            # inflation found in 2026's total_paid, and the $0 paid shown for every
            # prior year). received_on is already parsed above for the Postgres
            # posting a few lines up -- reuse it here so the Mongo mirror and the
            # Postgres receipt agree on which year this payment belongs to.
            mongo_year = await resolve_levy_year_for_date(building_id, received_on)
            if not mongo_year:
                logger.warning(
                    "levy_payments mirror skipped: no annual_levies year found for building=%s "
                    "receipt=%s", building_id, receipt.receipt_id,
                )
            else:
                mongo_payment_id = str(uuid4())
                now = datetime.now(timezone.utc).isoformat()
                await db.levy_payments.insert_one({
                    "id": mongo_payment_id,
                    "building_id": building_id,
                    "unit_number": unit_number,
                    "amount": mongo_amount,
                    "payment_method": "bank_transfer",
                    "payment_reference": doc.get("inbox_event_id"),
                    "payment_date": received_on_str,
                    "quarter": "AUTO",
                    "year": mongo_year,
                    # Disclosure surfacing (2026-07-17): the owner-facing payment
                    # history (routers/finance.py's payment_history builder) reads
                    # this Mongo collection, not finance.receipts directly — so
                    # provenance must be mirrored here too, not just onto the
                    # Postgres receipt, or a reconstructed payment would silently
                    # look identical to an observed one on every owner-facing screen.
                    "transaction_origin": tx_metadata.get("transaction_origin"),
                    "reconstruction_batch_id": reconstruction_batch_id_str,
                    "assumption_code": tx_metadata.get("assumption_code"),
                    "status": "confirmed",
                    "receipt_number": f"PG-{receipt.receipt_id}",
                    "notes": f"Auto-posted from bank-feed match decision (queue_item={item_id})",
                    "confirmed_by": decided_by,
                    "confirmed_at": now,
                    "created_at": now,
                })
                await _upsert_ledger_for_payment(
                    unit_number, mongo_year, mongo_amount, mongo_payment_id, building_id
                )
                logger.info(
                    "levy_payments mirror: payment=%s unit=%s year=%s receipt=%s building=%s",
                    mongo_payment_id, unit_number, mongo_year, receipt.receipt_id, building_id,
                )
        except Exception as mirror_exc:
            logger.error(
                "levy_payments mirror failed for receipt=%s building=%s: %s",
                receipt.receipt_id, building_id, mirror_exc,
            )

        return str(receipt.receipt_id)

    except Exception as exc:
        logger.error(
            "_post_payment_to_ledger failed for item=%s building=%s: %s",
            item_id, building_id, exc,
        )
        return None


_AUTO_ALLOCATE_DECIDED_BY = "system:matching_engine_auto_allocate"


async def auto_allocate_queue_item(queue_id: str, building_id: str) -> Optional[str]:
    """Post a high-confidence MatchingEngine result through to the ledger.

    engine.match() sets status="auto_allocated" when the best score exceeds
    the auto-match threshold, but that write alone never reaches the ledger:
    decide_queue_item() is the only caller of _post_payment_to_ledger(), and
    it only accepts status="pending" (409 otherwise), so "auto_allocated"
    items were previously a dead end. This closes that gap by claiming the item,
    posting the ledger receipt, and committing terminal status only after the
    receipt exists. Failed posts restore status="auto_allocated" so the next
    idempotent replay can retry without creating a duplicate receipt.
    """
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        oid = ObjectId(queue_id)
    except (InvalidId, Exception):
        return None

    # Filter by _id only, matching decide_queue_item()'s convention — the
    # TenantScopedDatabase wrapper already scopes find_one() to the current
    # building; the explicit re-check below (as decide_queue_item also does)
    # guards against a queue_id from a different building ever being acted on.
    doc = await db.match_review_queue.find_one({"_id": oid})
    if (
        not doc
        or doc.get("status") not in {"auto_allocated", "posting"}
        or (doc.get("building_id") != building_id and doc.get("tenant_id") != building_id)
    ):
        return None

    is_posting_retry = doc.get("status") == "posting"
    if is_posting_retry and doc.get("posting_by") != _AUTO_ALLOCATE_DECIDED_BY:
        return None

    lot_id = doc.get("best_lot_id")
    tx = doc.get("tx") or {}
    amount_cents = tx.get("amount_cents")
    # Same validation decide_queue_item() applies to a human "allocate" action —
    # a bare truthiness check would let a negative amount_cents through (e.g. if
    # BankTxObserved ever carried a debit here), posting a negative receipt.
    if not lot_id or amount_cents is None or amount_cents <= 0:
        logger.warning(
            "auto_allocate_queue_item: missing best_lot_id or non-positive amount_cents=%r "
            "for queue_item=%s building=%s",
            amount_cents, queue_id, building_id,
        )
        return None

    now = datetime.now(timezone.utc).isoformat()
    candidates_snapshot = doc.get("candidates", [])
    decision = {
        "action": "allocate",
        "lot_id": lot_id,
        "amount_cents": amount_cents,
        "notes": "Auto-allocated: best match score exceeded auto_match_threshold.",
    }

    if not is_posting_retry:
        result = await db.match_review_queue.update_one(
            {"_id": oid, "status": "auto_allocated"},
            {"$set": {
                "status": "posting",
                "posting_started_at": now,
                "posting_by": _AUTO_ALLOCATE_DECIDED_BY,
                "decision": decision,
                "candidates_snapshot": candidates_snapshot,
            }},
        )
        if result.modified_count == 0:
            # Lost the race to another dispatch of the same event, or already handled.
            return None

    receipt_id = await _post_payment_to_ledger(
        doc=doc,
        lot_id=lot_id,
        amount_cents=amount_cents,
        decided_by=_AUTO_ALLOCATE_DECIDED_BY,
        current_user={"id": None, "email": _AUTO_ALLOCATE_DECIDED_BY},
        building_id=building_id,
        item_id=queue_id,
    )
    if not receipt_id:
        await db.match_review_queue.update_one(
            {"_id": oid, "status": "posting"},
            {"$set": {
                "status": "auto_allocated",
                "last_post_failed_at": datetime.now(timezone.utc).isoformat(),
                "last_post_failed_by": _AUTO_ALLOCATE_DECIDED_BY,
            }},
        )
        return None

    await db.match_review_queue.update_one(
        {"_id": oid, "status": "posting"},
        {"$set": {
            "status": _ACTION_TO_STATUS["allocate"],
            "decided_at": now,
            "decided_by": _AUTO_ALLOCATE_DECIDED_BY,
            "receipt_id": str(receipt_id),
            "decision": decision,
            "candidates_snapshot": candidates_snapshot,
        }},
    )

    event_doc = {
        "event_id": str(uuid.uuid4()),
        "event_type": "MatchDecisionRecorded",
        "tenant_id": doc.get("tenant_id"),
        "building_id": doc.get("building_id"),
        "queue_item_id": queue_id,
        "inbox_event_id": doc.get("inbox_event_id"),
        "action": "allocate",
        "lot_id": lot_id,
        "amount_cents": amount_cents,
        "notes": decision["notes"],
        "candidates_snapshot": candidates_snapshot,
        "decided_at": now,
        "decided_by": _AUTO_ALLOCATE_DECIDED_BY,
        "receipt_id": str(receipt_id),
    }
    await db.event_log.insert_one(event_doc)
    logger.info(
        "MatchDecisionRecorded (auto): tenant=%s item=%s lot=%s amount=%d",
        doc.get("tenant_id"), queue_id, lot_id, amount_cents,
    )

    return receipt_id


# ── Deterministic high-confidence promotion ───────────────────────────────────
#
# GAP-FIN-015 blocker 3: historical reconstruction and confirmed Strata Web payment
# rows already know their exact unit, amount, and period BY CONSTRUCTION — unlike a
# real bank transaction, there is no reference ambiguity for the generic L1-L8
# MatchingEngine to resolve. Worse, L4 (the strongest layer that can score a
# unit-ref+amount match) tops out at 0.85, always below the 0.90 auto-allocate
# threshold, so these transactions would otherwise sit in match_review_queue
# forever despite being unambiguous. This path bypasses generic scoring entirely
# for an explicit allowlist of source_types, and only for confidence="high" rows
# that do not require human review — everything else stays in the normal queue.
#
# "synthetic_from_budget" is deliberately absent from this allowlist: unlike
# historical_mongo rows, budget-derived synthetic rows are not confirmed bank
# movements. They are computed from unit entitlement and levy assumptions, with
# an inferred payment date, so the deterministic promotion rationale above does
# not apply. They must remain reviewable like any other unconfirmed transaction.
#
# "historical_reconstruction" added 2026-08-01: this is the CURRENT reconstruction
# generator's source_type (services/east_gate_levy_income_reconstruction.py, which
# explicitly supersedes the older synthetic_from_budget approach and, unlike it,
# uses real per-instalment dates/amounts from EC-confirmed financial_source_fact_versions
# wherever they exist). It was simply never added here when that generator replaced
# the older one -- confirmed live: every non-archived historical_reconstruction Demo
# Bank transaction for the one production building on this platform is
# confidence="high" and not requires_review (2,388/2,388), yet 100% of them sat
# permanently pending in match_review_queue because historical_mongo (the only
# allowed source_type) matches zero real transactions for that building. This is a
# generic gap -- any building using the current reconstruction generator hits the
# same dead end, not something specific to one building's data.
_PROMOTABLE_SOURCE_TYPES = {"historical_mongo", "historical_reconstruction"}
_PROMOTION_DECIDED_BY = "system:high_confidence_promotion"


async def promote_high_confidence_reconstruction(
        queue_item_id: str,
        building_id: str,
        actor_user_id: Optional[str] = None,
) -> dict:
    """Deterministically promote a high-confidence reconstructed/synthetic transaction.

    Reuses _post_payment_to_ledger() — the single authorised ledger-write path —
    exactly as auto_allocate_queue_item() does, so there is no second ledger writer.
    Raises HTTPException(404) if the queue item or its underlying Demo Bank
    transaction cannot be found, (409) if it is not eligible for deterministic
    promotion or not in a promotable state, (502) if ledger posting fails (the
    queue item is reverted to its prior status so a retry can be attempted).
    """
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        oid = ObjectId(queue_item_id)
    except (InvalidId, Exception):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Queue item not found")

    doc = await db.match_review_queue.find_one({"_id": oid})
    if not doc or (doc.get("building_id") != building_id and doc.get("tenant_id") != building_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Queue item not found")

    prior_status = doc.get("status")
    if prior_status not in {"pending", "unidentified", "review"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Queue item status={prior_status!r} is not eligible for promotion",
        )

    inbox_event_id = doc.get("inbox_event_id")
    tx_doc = await db.demo_bank_transactions.find_one({
        "building_id": building_id,
        "finance_bank_transaction_ref": inbox_event_id,
        "is_archived": {"$ne": True},
    })
    if not tx_doc:
        # Genuinely missing resource (the referenced Demo Bank transaction doesn't
        # exist) — 404, not 409. 409 is reserved below for real eligibility
        # conflicts (wrong source_type/confidence/status) on a transaction that
        # DOES exist, matching this function's own docstring contract.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No underlying Demo Bank transaction found for this queue item",
        )

    source_type = tx_doc.get("source_type")
    if source_type not in _PROMOTABLE_SOURCE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"source_type={source_type!r} is not eligible for deterministic promotion",
        )
    if tx_doc.get("confidence") != "high" or tx_doc.get("requires_review"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transaction is not high-confidence or still requires_review; "
                   "use the normal review queue instead.",
        )

    unit_number = tx_doc.get("unit_number")
    tx = doc.get("tx") or {}
    amount_cents = tx.get("amount_cents")
    if not unit_number or amount_cents is None or amount_cents <= 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transaction is missing a resolvable unit_number or a positive amount_cents",
        )

    now = datetime.now(timezone.utc).isoformat()
    decision = {
        "action": "allocate",
        "lot_id": unit_number,
        "amount_cents": amount_cents,
        "notes": f"Deterministic high-confidence promotion: source_type={source_type}",
    }

    claim = await db.match_review_queue.update_one(
        {"_id": oid, "status": prior_status},
        {"$set": {
            "status": "posting",
            "posting_started_at": now,
            "posting_by": _PROMOTION_DECIDED_BY,
            "decision": decision,
        }},
    )
    if claim.modified_count == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Queue item was claimed by another operation",
        )

    receipt_id = await _post_payment_to_ledger(
        doc=doc,
        lot_id=unit_number,
        amount_cents=amount_cents,
        decided_by=_PROMOTION_DECIDED_BY,
        current_user={"id": actor_user_id, "email": _PROMOTION_DECIDED_BY},
        building_id=building_id,
        item_id=queue_item_id,
    )
    if not receipt_id:
        await db.match_review_queue.update_one(
            {"_id": oid, "status": "posting"},
            {"$set": {
                "status": prior_status,
                "last_post_failed_at": datetime.now(timezone.utc).isoformat(),
                "last_post_failed_by": _PROMOTION_DECIDED_BY,
            }},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Ledger posting failed — queue item reverted for retry",
        )

    await db.match_review_queue.update_one(
        {"_id": oid, "status": "posting"},
        {"$set": {
            "status": "allocated",
            "match_type": "high_confidence_reconstruction",
            "decided_at": now,
            "decided_by": _PROMOTION_DECIDED_BY,
            "receipt_id": str(receipt_id),
            "decision": decision,
        }},
    )

    event_doc = {
        "event_id": str(uuid.uuid4()),
        "event_type": "MatchDecisionRecorded",
        "tenant_id": doc.get("tenant_id"),
        "building_id": doc.get("building_id"),
        "queue_item_id": queue_item_id,
        "inbox_event_id": inbox_event_id,
        "action": "allocate",
        "match_type": "high_confidence_reconstruction",
        "lot_id": unit_number,
        "amount_cents": amount_cents,
        "notes": decision["notes"],
        "decided_at": now,
        "decided_by": _PROMOTION_DECIDED_BY,
        "receipt_id": str(receipt_id),
    }
    await db.event_log.insert_one(event_doc)
    logger.info(
        "MatchDecisionRecorded (high_confidence_reconstruction): tenant=%s item=%s lot=%s amount=%d",
        doc.get("tenant_id"), queue_item_id, unit_number, amount_cents,
    )

    return {
        "queue_item_id": queue_item_id,
        "status": "allocated",
        "match_type": "high_confidence_reconstruction",
        "lot_id": unit_number,
        "amount_cents": amount_cents,
        "receipt_id": str(receipt_id),
        "source_type": source_type,
    }


@router.post("/financial/matching/queue/{item_id}/promote-high-confidence")
async def promote_high_confidence_queue_item(
        item_id: str,
        current_user: dict = Depends(require_feature("financial_integration_layer_v2")),
        building_id: str = Depends(get_current_building),
):
    """Promote a high-confidence reconstructed/synthetic transaction directly to the ledger.

    Distinct from POST /decide: this path is for transactions whose source_type is
    an explicitly-approved deterministic reconstruction type (see
    _PROMOTABLE_SOURCE_TYPES), not for human review of an ambiguous match.
    """
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in _DECIDE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only strata managers and above may promote queue items.",
        )

    actor_user_id = current_user.get("id") or current_user.get("_id")
    return await promote_high_confidence_reconstruction(
        queue_item_id=item_id,
        building_id=building_id,
        actor_user_id=str(actor_user_id) if actor_user_id else None,
    )


# ── Historical levy-payment reconstruction (GAP-FIN-015 criterion 6) ─────────

@router.post(
    "/financial/matching/historical-reconstruction/run",
    response_model=HistoricalReconstructionResponse,
)
async def run_historical_reconstruction(
        body: HistoricalReconstructionRequest,
        current_user: dict = Depends(require_feature("financial_integration_layer_v2")),
        building_id: str = Depends(get_current_building),
):
    """Synthesize historical levy-payment demo_bank_transactions for an onboarding gap-year range.

    Manager-triggered only — not wired into any scheduler. Building-agnostic and
    idempotent (SHA-256 idempotency key + $setOnInsert): safe to re-run for the same
    or a different building without creating duplicate transactions. Resulting
    transactions carry source_type="synthetic_from_budget" and must go through the
    normal review path; they are budget-derived assumptions, not observed bank
    movements, so they are deliberately excluded from deterministic promotion.
    """
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in _DECIDE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only strata managers and above may run historical reconstruction.",
        )
    if body.from_year > body.to_year:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from_year must be <= to_year",
        )

    try:
        result = await synthesize_historical_levy_payments(
            db,
            building_id=building_id,
            from_year=body.from_year,
            to_year=body.to_year,
            dry_run=body.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    return HistoricalReconstructionResponse(**result)


# ── Serialisation helper ──────────────────────────────────────────────────────

def _doc_to_item(doc: dict) -> QueueItem:
    """Generated function header.

    Function: _doc_to_item
    Path: backend/routers/financial_matching.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return QueueItem(
        item_id=str(doc.get("_id", "")),
        tenant_id=doc.get("tenant_id", ""),
        inbox_event_id=doc.get("inbox_event_id", ""),
        status=doc.get("status", ""),
        match_type=doc.get("match_type", ""),
        best_score=doc.get("best_score", 0.0),
        best_layer=doc.get("best_layer", ""),
        best_lot_id=doc.get("best_lot_id"),
        sla_due_at=doc.get("sla_due_at", ""),
        created_at=doc.get("created_at", ""),
        decided_at=doc.get("decided_at"),
        decided_by=doc.get("decided_by"),
        decision=doc.get("decision"),
        tx=doc.get("tx", {}),
        candidates=doc.get("candidates", []),
        all_scores=doc.get("all_scores", []),
    )
