# @featuretrace:demo_bank — Super-admin-only cross-feature diagnostic endpoints.
# Layer: router
# Data flow: DemoBankTransactionsPage -> GET /admin/demo-bank/transactions -> demo_bank_transactions
#            (Mongo staging, building-scoped via the caller's currently-selected building context).
# Related: frontend/src/pages/dashboard/admin/DemoBankTransactionsPage.tsx
#          backend/routers/bank_feeds.py (the sibling router this endpoint moved out of)
#          backend/routers/trial_request.py (_require_super_admin pattern this mirrors)
#          tasks/GAP-ADMIN-001-building-agnostic-super-admin-pages.md
# Toggle: none — deliberately NOT gated behind financial_integration_layer_v2 or any other
#         building-scoped feature toggle. See module docstring below for why.
# Collection: demo_bank_transactions
"""
Super-admin-only diagnostic endpoints that must stay reachable regardless of which
building-scoped feature toggles are enabled for the currently-selected building.

Why this file exists (GAP-ADMIN-001): GET /admin/demo-bank/transactions originally
lived in routers/bank_feeds.py, whose whole router carries a
`dependencies=[Depends(require_feature("financial_integration_layer_v2"))]` gate —
appropriate for the live bank-feed ingestion/sync/matching endpoints that router
owns, but wrong for a read-only viewer over data that already exists in Mongo
regardless of that toggle's state. `financial_integration_layer_v2` is classified
CUTOVER_SENSITIVE in core/toggle_classification.py (12+ other features depend_on
it), so it is never bulk-enabled just to unblock a diagnostic viewer — the fix is
to not require it here at all.

Building scoping is NOT relaxed by this move — every endpoint here still resolves
building_id via Depends(get_current_building), which is the same mechanism (JWT
scope, super_admin's X-Building-ID header override, the existing header
BuildingSwitcher component) every other page in the app uses. A super_admin
switches which building's data they're viewing the normal way; this module never
queries or aggregates across multiple buildings in one request.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from utils.auth import effective_role, get_current_building, get_current_user

router = APIRouter(prefix="/admin/demo-bank", tags=["Admin Diagnostics"])


def _get_db():
    from database import db
    return db


def _require_super_admin_only(current_user: dict) -> dict:
    """Mirrors routers.trial_request._require_super_admin's pattern (inline
    effective_role check after Depends(get_current_user)) — the idiom this
    codebase's routers actually use for single-role gating, not the unused
    utils.permissions.require_role()."""
    if effective_role(current_user) != "super_admin":
        raise HTTPException(403, "Super admin only.")
    return current_user


@router.get("/transactions")
async def list_demo_bank_transactions_admin(
        source_type: Optional[str] = Query(None),
        reconstruction_batch_id: Optional[str] = Query(None),
        include_archived: bool = Query(False, description="Include rows archived by a supersede/rebuild — off by default."),
        page: int = Query(1, ge=1),
        limit: int = Query(50, ge=1, le=200),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """List raw demo_bank_transactions staging rows for super_admin review.

    Distinct from GET /bank-feeds/transactions (the Postgres-synced
    finance.bank_transactions mirror) and the Reconciliation page (which only
    surfaces a fixed candidate set of source_types) — this endpoint returns
    every demo_bank_transactions row for the currently-selected building,
    across all source_types, including historical_reconstruction batches
    before they've synced anywhere downstream. Archived (superseded-by-rebuild)
    rows are excluded by default — pass include_archived=true to see them too,
    since this is a diagnostic viewer where that's occasionally useful.
    """
    _require_super_admin_only(current_user)
    db = _get_db()

    query: dict[str, Any] = {"building_id": building_id}
    if not include_archived:
        query["is_archived"] = {"$ne": True}
    if source_type:
        query["source_type"] = source_type
    if reconstruction_batch_id:
        query["reconstruction_batch_id"] = reconstruction_batch_id

    skip = (page - 1) * limit
    count_task = db.demo_bank_transactions.count_documents(query)
    docs_task = (
        db.demo_bank_transactions.find(query)
        .sort("posted_date", -1)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    total, docs = await asyncio.gather(count_task, docs_task)

    data = [
        {
            "id": str(doc["_id"]),
            "posted_date": doc.get("posted_date"),
            "unit_number": doc.get("unit_number"),
            "account_ref": doc.get("account_ref"),
            "amount_cents": doc.get("amount_cents"),
            "direction": doc.get("direction"),
            "description": doc.get("description"),
            "source_type": doc.get("source_type"),
            "transaction_origin": doc.get("transaction_origin"),
            "reconstruction_batch_id": doc.get("reconstruction_batch_id"),
            "status": doc.get("status"),
            "confidence": doc.get("confidence"),
            "assumption_code": doc.get("assumption_code"),
            "allocations": doc.get("allocations") or [],
        }
        for doc in docs
    ]

    total_pages = max(1, -(-total // limit))  # ceil division
    return {
        "data": data,
        "pagination": {"page": page, "limit": limit, "total": total, "total_pages": total_pages},
    }
