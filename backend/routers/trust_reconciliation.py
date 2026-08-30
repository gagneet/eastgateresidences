# @featuretrace:trust-reconciliation — Trust bank reconciliation and period lock management router.
# Layer: router
# Data flow: TrustReconciliationPage → GET/POST /trust/reconciliation/runs, GET/POST/DELETE /trust/period-locks
#            → trust_period_locks + trust_reconciliation_runs (building-scoped).
# Related: frontend/src/pages/dashboard/TrustReconciliationPage.jsx
#          frontend/src/__tests__/pages/dashboard/TrustReconciliationPage.test.jsx
# Toggle: trust_reconciliation
# Collection: trust_period_locks, trust_reconciliation_runs, bank_statement_lines, trust_reconciliation_matches, reconciliation_exceptions
"""
Trust Reconciliation Router — bank reconciliation and period lock management.

Endpoints:
  POST   /trust/reconciliation/runs              — create reconciliation run
  GET    /trust/reconciliation/runs              — list runs (building-scoped)
  GET    /trust/reconciliation/runs/{run_id}     — get run details
  POST   /trust/reconciliation/runs/{run_id}/import-statement  — import bank CSV
  GET    /trust/reconciliation/runs/{run_id}/statement-lines   — list statement lines
  POST   /trust/reconciliation/runs/{run_id}/match             — match statement line to ledger (manual or smart)
  POST   /trust/reconciliation/runs/{run_id}/unmatch           — remove a confirmed match
  GET    /trust/reconciliation/runs/{run_id}/suggestions       — smart match suggestions
  GET    /trust/reconciliation/runs/{run_id}/summary           — extended summary with confidence + exceptions
  POST   /trust/reconciliation/runs/{run_id}/close             — close reconciliation run
  POST   /trust/period-locks                     — lock a financial period
  GET    /trust/period-locks                     — list period locks
  DELETE /trust/period-locks/{lock_id}           — unlock a period (super_admin only)
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
from datetime import datetime, timezone

import logging
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

from models.trust_accounting import (
    PeriodLockCreate,
    ReconciliationStatus,
    TrustReconciliationRunCreate,
    format_aud,
)
from services.cutover_config_service import (
    TRUST_RECONCILIATION_PG_ENABLED,
    are_cutover_features_enabled,
)
from services.reconciliation_matching_service import (
    MatchingEngine,
    detect_duplicate_bank_lines,
    detect_duplicate_internal_transactions,
    detect_many_to_one,
    detect_stale_unmatched,
    detect_suspicious_variances,
)
from services.trust_read_service import TrustReadService
from services.domain_source_guard import DomainSourceAuditContext, require_domain_source
from utils.auth import get_current_user, effective_role
from utils.file_scan import scan_upload
from utils.helpers import create_audit_log

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/trust", tags=["Trust Reconciliation"])
_trust_read_service = TrustReadService()

# GAP-FIN-049 (2026-08-07): the shadow route_keys the trust comparators write to
# core.shadow_diffs, keyed by canonical domain (see trust_shadow_read_service.py).
# Consumed by the critical-diff HARD gate in _trust_pg_read_enabled to fail PG trust
# reads over to Mongo whenever the trust shadow comparators have recorded a critical
# divergence for the domain.
_TRUST_SHADOW_ROUTE_KEYS: dict[str, list[str]] = {
    "trust_ledger": ["trust.summary", "trust_v2.accounts.summary"],
    "trust_reconciliation": ["trust_reconciliation.summary"],
}


async def _trust_pg_read_enabled(
    building_id: str,
    toggle_key: str,
    domain: str,
    *,
    route: str | None = None,
    current_user: dict | None = None,
) -> bool:
    """Generated function header.

    Function: _trust_pg_read_enabled
    Path: backend/routers/trust_reconciliation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not await are_cutover_features_enabled(building_id, toggle_key):
        return False
    decision = await require_domain_source(
        domain=domain,
        building_id=building_id,
        operation="read",
        requested_source="postgres",
        audit_context=DomainSourceAuditContext(
            actor_user_id=str(current_user.get("id")) if current_user and current_user.get("id") else None,
            actor_role=(current_user.get("effective_role") or current_user.get("role")) if current_user else None,
            route=route,
            source_service="trust_reconciliation_router",
            feature_toggle_key=toggle_key,
        ),
    )
    if not decision.postgres_allowed:
        return False
    # GAP-FIN-049 critical-diff HARD gate — mirrors finance_route_cutover_service's
    # never-waivable critical gate, previously absent from the trust read path (the
    # gap this closes: trust reads could flip to PG on domain mode alone while the trust
    # shadow comparators were actively recording critical divergence). If ANY critical
    # trust shadow diff exists for this domain in the recent window, refuse PG and fall
    # back to Mongo (the trusted store). Deliberately non-blocking on no-data / query
    # error: the domain-source decision above stays the primary control and Mongo is
    # always the fallback, so a failed readiness check can never wrongly serve stale PG.
    try:
        from services.cutover_status_service import canonical_domain
        from services.shadow_read_service import get_building_domain_shadow_readiness
        canon = canonical_domain(domain)
        route_keys = _TRUST_SHADOW_ROUTE_KEYS.get(canon, [])
        if route_keys:
            readiness = await get_building_domain_shadow_readiness(
                building_id=building_id, domain=canon, route_keys=route_keys,
            )
            if int(readiness.get("total_critical_count") or 0) > 0:
                logger.warning(
                    "trust PG read refused for %s/%s: %d critical shadow diff(s) — "
                    "falling back to Mongo",
                    building_id, canon, readiness["total_critical_count"],
                )
                return False
    except Exception as exc:
        logger.debug("trust critical-diff gate check failed for %s: %s", building_id, exc)
    return True


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/trust_reconciliation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/trust_reconciliation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


TRUST_MANAGE_ROLES = {"super_admin", "strata_manager"}
TRUST_VIEW_ROLES = {"super_admin", "strata_manager", "strata_admin", "ec_member", "treasurer"}


def _require_manage(user: dict) -> dict:
    """Generated function header.

    Function: _require_manage
    Path: backend/routers/trust_reconciliation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(user) not in TRUST_MANAGE_ROLES:
        raise HTTPException(status_code=403, detail="Requires trust.manage permission.")
    return user


def _require_view(user: dict) -> dict:
    """Generated function header.

    Function: _require_view
    Path: backend/routers/trust_reconciliation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(user) not in TRUST_VIEW_ROLES:
        raise HTTPException(status_code=403, detail="Requires trust.view permission.")
    return user


def _get_building_id(user: dict) -> str:
    """Generated function header.

    Function: _get_building_id
    Path: backend/routers/trust_reconciliation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    bid = user.get("building_id")
    if not bid:
        raise HTTPException(status_code=400, detail="No building_id in token.")
    return str(bid)


def _is_period_locked(period_locks: List[Dict], date_str: str, fund_type: str) -> bool:
    """Check if a given date falls within an active period lock for the fund."""
    for lock in period_locks:
        if not lock.get("is_active"):
            continue
        if lock.get("fund_type") != fund_type:
            continue
        if lock.get("period_start", "") <= date_str <= lock.get("period_end", ""):
            return True
    return False


def _line_amount_cents(line: Dict[str, Any]) -> int:
    """Generated function header.

    Function: _line_amount_cents
    Path: backend/routers/trust_reconciliation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if line.get("amount_cents") is not None:
        return int(line.get("amount_cents") or 0)
    return int(round(float(line.get("amount") or 0) * 100))


def _normalize_internal_tx_amount(doc: Dict[str, Any], bank_account_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Ensure a trust_ledger_entries doc has a top-level amount_cents for matching.

    trust_ledger_entries holds two shapes: immutable single-entry rows
    (POST /trust/ledger) already carry amount_cents; double-entry journal rows
    (POST /trust/journal) carry balanced debit/credit totals plus per-account
    lines. For journal rows, the bank movement is the signed line amount for
    this reconciliation run's bank account, not debit_total - credit_total
    because balanced journals normally have equal totals.

    Debit is treated as money into the trust bank account, matching
    bank_statement_lines' +credit/-debit convention. Journal rows that do not
    touch the reconciled bank account are excluded from the candidate pool.
    """
    if "amount_cents" in doc:
        return doc

    lines = doc.get("lines") or []
    if bank_account_id and lines:
        amount_cents = 0
        touched_bank_account = False
        for line in lines:
            if str(line.get("account_id") or "") != str(bank_account_id):
                continue
            touched_bank_account = True
            sign = 1 if line.get("entry_type") == "debit" else -1
            amount_cents += sign * _line_amount_cents(line)
        if not touched_bank_account:
            return None
        doc["amount_cents"] = amount_cents
        return doc

    if "debit_total_cents" in doc or "credit_total_cents" in doc:
        doc["amount_cents"] = int(doc.get("debit_total_cents") or 0) - int(doc.get("credit_total_cents") or 0)
        return doc

    doc["amount_cents"] = 0
    return doc


# ─── Reconciliation Runs ────────────────────────────────────────────────────

@router.post("/reconciliation/runs", status_code=status.HTTP_201_CREATED)
async def create_reconciliation_run(
        payload: TrustReconciliationRunCreate,
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Create a new bank reconciliation run."""
    user = _require_manage(current_user)
    building_id = _get_building_id(user)
    db = _get_db()

    doc = payload.model_dump()
    doc["building_id"] = building_id
    doc["status"] = ReconciliationStatus.OPEN.value
    doc["matched_count"] = 0
    doc["unmatched_count"] = 0
    doc["discrepancy_cents"] = 0
    doc["prepared_by"] = user.get("email")
    doc["created_at"] = _now_iso()
    doc["updated_at"] = _now_iso()

    result = await db.trust_reconciliation_runs.insert_one(doc)
    rid = str(result.inserted_id)

    import asyncio
    asyncio.create_task(create_audit_log(
        db=db,
        action="reconciliation_run_created",
        entity_type="trust_reconciliation_run",
        entity_id=rid,
        user_id=user.get("email"),
        building_id=building_id,
        details={
            "bank_account_id": payload.bank_account_id,
            "period": f"{payload.statement_start_date} to {payload.statement_end_date}",
        },
    ))
    return {"id": rid, "status": ReconciliationStatus.OPEN.value, "building_id": building_id}


@router.get("/reconciliation/runs")
async def list_reconciliation_runs(
        bank_account_id: Optional[str] = Query(None),
        status_filter: Optional[str] = Query(None, alias="status"),
        page: int = Query(1, ge=1),
        limit: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """List reconciliation runs for this building."""
    user = _require_view(current_user)
    building_id = _get_building_id(user)
    if await _trust_pg_read_enabled(
        building_id,
        TRUST_RECONCILIATION_PG_ENABLED,
        "trust_reconciliation",
        route="GET /trust/reconciliation/runs",
        current_user=current_user,
    ):
        pg_runs = await _trust_read_service.list_reconciliation_runs(
            building_id=building_id,
            bank_account_id=bank_account_id,
            status_filter=status_filter,
            page=page,
            limit=limit,
        )
        if pg_runs is not None:
            return pg_runs
    db = _get_db()

    query: Dict[str, Any] = {"building_id": building_id}
    if bank_account_id:
        query["bank_account_id"] = bank_account_id
    if status_filter:
        query["status"] = status_filter

    skip = (page - 1) * limit
    # Performance Optimization⚡: Parallelize count and data retrieval.
    # Using to_list() is faster than async iteration for paginated results.
    count_task = db.trust_reconciliation_runs.count_documents(query)
    runs_task = db.trust_reconciliation_runs.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)

    total, docs = await asyncio.gather(count_task, runs_task)

    runs = []
    for doc in docs:
        doc["id"] = str(doc.pop("_id"))
        doc.setdefault("discrepancy_display", format_aud(doc.get("discrepancy_cents", 0)))
        runs.append(doc)

    return {"runs": runs, "total": total, "page": page, "limit": limit}


@router.get("/reconciliation/runs/{run_id}")
async def get_reconciliation_run(
        run_id: str,
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get full details of a reconciliation run."""
    user = _require_view(current_user)
    building_id = _get_building_id(user)
    if await _trust_pg_read_enabled(
        building_id,
        TRUST_RECONCILIATION_PG_ENABLED,
        "trust_reconciliation",
        route="GET /trust/reconciliation/runs/{run_id}",
        current_user=current_user,
    ):
        pg_run = await _trust_read_service.get_reconciliation_run(
            building_id=building_id,
            run_id=run_id,
        )
        if pg_run is not None:
            return pg_run
    db = _get_db()

    doc = await db.trust_reconciliation_runs.find_one({"_id": ObjectId(run_id), "building_id": building_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Reconciliation run not found.")
    doc["id"] = str(doc.pop("_id"))
    return doc


@router.post("/reconciliation/runs/{run_id}/import-statement")
async def import_bank_statement(
        run_id: str,
        file: UploadFile = File(...),
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Import a bank statement CSV for this reconciliation run.

    Expected CSV columns (flexible header matching):
    Date, Amount, Description, Reference (optional), ValueDate (optional)
    Amount is positive for credits (deposits), negative for debits (withdrawals).
    """
    user = _require_manage(current_user)
    building_id = _get_building_id(user)
    db = _get_db()

    run = await db.trust_reconciliation_runs.find_one({"_id": ObjectId(run_id), "building_id": building_id})
    if not run:
        raise HTTPException(status_code=404, detail="Reconciliation run not found.")
    if run.get("status") not in (ReconciliationStatus.OPEN.value, ReconciliationStatus.IN_PROGRESS.value):
        raise HTTPException(status_code=400, detail="Cannot import to a closed or cancelled run.")

    content = await file.read()
    await scan_upload(content, context="csv", filename=file.filename or "")
    checksum = hashlib.sha256(content).hexdigest()

    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(decoded))
    rows = list(reader)

    if not rows:
        raise HTTPException(status_code=400, detail="CSV file is empty or has no data rows.")

    # Normalize headers
    def _find_col(headers, candidates):
        """Generated function header.

        Function: _find_col
        Path: backend/routers/trust_reconciliation.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        for h in headers:
            if h.strip().lower() in candidates:
                return h
        return None

    headers = list(rows[0].keys())
    date_col = _find_col(headers, {"date", "transaction date", "txn date", "posting date"})
    amount_col = _find_col(headers, {"amount", "net amount", "transaction amount", "debit/credit"})
    desc_col = _find_col(headers, {"description", "narrative", "details", "particulars"})
    ref_col = _find_col(headers, {"reference", "ref", "cheque no", "transaction reference"})

    if not date_col or not amount_col:
        raise HTTPException(
            status_code=400,
            detail=f"CSV must have Date and Amount columns. Found: {headers[:10]}"
        )

    imported = 0
    duplicates = 0
    errors: List[str] = []
    insert_docs = []

    for i, row in enumerate(rows):
        try:
            date_str = (row.get(date_col) or "").strip()
            amount_str = (row.get(amount_col) or "0").strip().replace("$", "").replace(",", "")
            description = (row.get(desc_col) or "") if desc_col else ""
            reference = (row.get(ref_col) or "") if ref_col else ""

            if not date_str:
                continue

            try:
                amount_float = float(amount_str)
            except ValueError:
                errors.append(f"Row {i + 2}: invalid amount '{amount_str}'")
                continue

            amount_cents = round(amount_float * 100)

            fingerprint = hashlib.md5(
                f"{building_id}:{run.get('bank_account_id')}:{date_str}:{amount_cents}:{description[:50]}".encode()
            ).hexdigest()

            existing = await db.bank_statement_lines.find_one({
                "building_id": building_id,
                "fingerprint_hash": fingerprint,
            })
            if existing:
                duplicates += 1
                continue

            doc = {
                "building_id": building_id,
                "bank_account_id": run.get("bank_account_id"),
                "reconciliation_run_id": run_id,
                "statement_date": date_str,
                "amount_cents": amount_cents,
                "amount_display": format_aud(amount_cents),
                "description": description.strip(),
                "external_reference": reference.strip(),
                "fingerprint_hash": fingerprint,
                "matched_status": "unmatched",
                "matched_ledger_entry_ids": [],
                "import_run_id": None,
                "created_at": _now_iso(),
            }
            insert_docs.append(doc)
            imported += 1

        except Exception as e:
            errors.append(f"Row {i + 2}: {str(e)}")

    if insert_docs:
        await db.bank_statement_lines.insert_many(insert_docs)

    # Update run status
    await db.trust_reconciliation_runs.update_one(
        {"_id": ObjectId(run_id)},
        {"$set": {
            "status": ReconciliationStatus.IN_PROGRESS.value,
            "unmatched_count": imported,
            "updated_at": _now_iso(),
        }}
    )

    return {
        "run_id": run_id,
        "imported": imported,
        "duplicates": duplicates,
        "errors_count": len(errors),
        "errors_sample": errors[:10],
        "checksum": checksum,
    }


@router.get("/reconciliation/runs/{run_id}/statement-lines")
async def list_statement_lines(
        run_id: str,
        matched_status: Optional[str] = Query(None),
        page: int = Query(1, ge=1),
        limit: int = Query(50, ge=1, le=200),
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """List bank statement lines for a reconciliation run."""
    user = _require_view(current_user)
    building_id = _get_building_id(user)
    if await _trust_pg_read_enabled(
        building_id,
        TRUST_RECONCILIATION_PG_ENABLED,
        "trust_reconciliation",
        route="GET /trust/reconciliation/runs/{run_id}/statement-lines",
        current_user=current_user,
    ):
        pg_lines = await _trust_read_service.list_statement_lines(
            building_id=building_id,
            run_id=run_id,
            matched_status=matched_status,
            page=page,
            limit=limit,
        )
        if pg_lines is not None:
            return pg_lines
    db = _get_db()

    query: Dict[str, Any] = {"building_id": building_id, "reconciliation_run_id": run_id}
    if matched_status:
        query["matched_status"] = matched_status

    skip = (page - 1) * limit
    # Performance Optimization⚡: Parallelize count and data retrieval.
    count_task = db.bank_statement_lines.count_documents(query)
    lines_task = db.bank_statement_lines.find(query).sort("statement_date", 1).skip(skip).limit(limit).to_list(limit)

    total, docs = await asyncio.gather(count_task, lines_task)

    lines = []
    for doc in docs:
        doc["id"] = str(doc.pop("_id"))
        lines.append(doc)

    return {"lines": lines, "total": total, "page": page, "limit": limit}


class MatchRequest(BaseModel):
    # Legacy format: manual match (statement_line_id + ledger_entry_ids)
    statement_line_id: Optional[str] = None
    ledger_entry_ids: Optional[List[str]] = None
    # Smart-match format: single bank_line → single internal transaction
    bank_line_id: Optional[str] = None
    internal_transaction_id: Optional[str] = None
    override_reason: Optional[str] = None
    # Shared fields
    match_type: str = "exact"  # exact | tolerance | manual | split
    variance_cents: int = 0
    notes: Optional[str] = None


@router.post("/reconciliation/runs/{run_id}/match")
async def match_statement_line(
        run_id: str,
        payload: MatchRequest,
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Match a bank statement line to one or more ledger entries.

    Accepts two formats:
    - Legacy:     { statement_line_id, ledger_entry_ids }
    - Smart-match: { bank_line_id, internal_transaction_id, override_reason? }
    """
    user = _require_manage(current_user)
    building_id = _get_building_id(user)
    db = _get_db()

    run = await db.trust_reconciliation_runs.find_one({"_id": ObjectId(run_id), "building_id": building_id})
    if not run or run.get("status") not in (ReconciliationStatus.OPEN.value, ReconciliationStatus.IN_PROGRESS.value):
        raise HTTPException(status_code=400, detail="Reconciliation run is not open for matching.")

    # Resolve bank_line_id (smart-match) vs statement_line_id (legacy)
    bank_line_id = payload.bank_line_id or payload.statement_line_id
    internal_transaction_id = payload.internal_transaction_id
    ledger_entry_ids = payload.ledger_entry_ids or (
        [internal_transaction_id] if internal_transaction_id else []
    )

    if not bank_line_id:
        raise HTTPException(status_code=400, detail="Provide bank_line_id (or statement_line_id).")
    if not ledger_entry_ids:
        raise HTTPException(status_code=400, detail="Provide internal_transaction_id (or ledger_entry_ids).")

    line = await db.bank_statement_lines.find_one({
        "_id": ObjectId(bank_line_id),
        "building_id": building_id,
        "reconciliation_run_id": run_id,
    })
    if not line:
        raise HTTPException(status_code=404, detail="Statement line not found in this run.")

    # Idempotency: return existing match if already matched to the same target
    existing_match = await db.trust_reconciliation_matches.find_one({
        "building_id": building_id,
        "reconciliation_run_id": run_id,
        "bank_statement_line_id": bank_line_id,
        "ledger_entry_ids": ledger_entry_ids,
    })
    if existing_match:
        return {
            "status": "idempotent_replay",
            "match_id": str(existing_match["_id"]),
            "bank_line_id": bank_line_id,
            "internal_transaction_id": internal_transaction_id or (ledger_entry_ids[0] if ledger_entry_ids else None),
            "matched_count": await db.bank_statement_lines.count_documents(
                {"reconciliation_run_id": run_id, "matched_status": "matched"}
            ),
        }

    match_doc = {
        "building_id": building_id,
        "reconciliation_run_id": run_id,
        "bank_statement_line_id": bank_line_id,
        "bank_line_id": bank_line_id,
        "internal_transaction_id": internal_transaction_id,
        "ledger_entry_ids": ledger_entry_ids,
        "match_type": payload.match_type,
        "variance_cents": payload.variance_cents,
        "notes": payload.notes,
        "override_reason": payload.override_reason,
        "matched_by": user.get("email"),
        "matched_at": _now_iso(),
    }
    result = await db.trust_reconciliation_matches.insert_one(match_doc)

    # Update statement line as matched
    await db.bank_statement_lines.update_one(
        {"_id": ObjectId(bank_line_id)},
        {"$set": {
            "matched_status": "matched",
            "matched_ledger_entry_ids": ledger_entry_ids,
        }}
    )

    # Update run counts
    matched_total = await db.bank_statement_lines.count_documents({
        "reconciliation_run_id": run_id, "matched_status": "matched"
    })
    unmatched_total = await db.bank_statement_lines.count_documents({
        "reconciliation_run_id": run_id, "matched_status": "unmatched"
    })
    await db.trust_reconciliation_runs.update_one(
        {"_id": ObjectId(run_id)},
        {"$set": {"matched_count": matched_total, "unmatched_count": unmatched_total, "updated_at": _now_iso()}}
    )

    return {
        "status": "confirmed",
        "match_id": str(result.inserted_id),
        "bank_line_id": bank_line_id,
        "internal_transaction_id": internal_transaction_id or (ledger_entry_ids[0] if ledger_entry_ids else None),
        "matched_count": matched_total,
        "unmatched_count": unmatched_total,
    }


# ─── Unmatch ──────────────────────────────────────────────────────────────────

class UnmatchRequest(BaseModel):
    match_id: str
    reason: str


@router.post("/reconciliation/runs/{run_id}/unmatch")
async def unmatch_statement_line(
        run_id: str,
        payload: UnmatchRequest,
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Remove a confirmed match, returning the bank line to unmatched status."""
    user = _require_manage(current_user)
    building_id = _get_building_id(user)
    db = _get_db()

    run = await db.trust_reconciliation_runs.find_one({"_id": ObjectId(run_id), "building_id": building_id})
    if not run:
        raise HTTPException(status_code=404, detail="Reconciliation run not found.")
    if run.get("status") == ReconciliationStatus.CLOSED.value:
        raise HTTPException(status_code=400, detail="Cannot unmatch lines in a closed run.")

    match_doc = await db.trust_reconciliation_matches.find_one({
        "_id": ObjectId(payload.match_id),
        "building_id": building_id,
        "reconciliation_run_id": run_id,
    })
    if not match_doc:
        raise HTTPException(status_code=404, detail="Match record not found in this run.")

    bank_line_id = str(match_doc.get("bank_statement_line_id") or match_doc.get("bank_line_id") or "")

    # Mark the match as unmatched (soft-delete via status flag)
    await db.trust_reconciliation_matches.update_one(
        {"_id": ObjectId(payload.match_id)},
        {"$set": {
            "unmatched": True,
            "unmatched_by": user.get("email"),
            "unmatched_at": _now_iso(),
            "unmatch_reason": payload.reason,
        }}
    )

    # Restore bank line to unmatched
    if bank_line_id:
        await db.bank_statement_lines.update_one(
            {"_id": ObjectId(bank_line_id), "building_id": building_id},
            {"$set": {"matched_status": "unmatched", "matched_ledger_entry_ids": []}}
        )

    # Update run counts
    matched_total = await db.bank_statement_lines.count_documents({
        "reconciliation_run_id": run_id, "matched_status": "matched"
    })
    unmatched_total = await db.bank_statement_lines.count_documents({
        "reconciliation_run_id": run_id, "matched_status": "unmatched"
    })
    await db.trust_reconciliation_runs.update_one(
        {"_id": ObjectId(run_id)},
        {"$set": {"matched_count": matched_total, "unmatched_count": unmatched_total, "updated_at": _now_iso()}}
    )

    return {
        "status": "unmatched",
        "match_id": payload.match_id,
        "unmatched_by": user.get("email"),
        "reason": payload.reason,
        "unmatched_at": _now_iso(),
        "matched_count": matched_total,
        "unmatched_count": unmatched_total,
    }


# ─── Match Suggestions ────────────────────────────────────────────────────────

@router.get("/reconciliation/runs/{run_id}/suggestions")
async def get_match_suggestions(
        run_id: str,
        min_score: float = Query(0.50, ge=0.0, le=1.0),
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Run the smart matching engine and return ranked match suggestions
    for all unmatched bank lines in this reconciliation run.
    Also writes anomaly exceptions (duplicates, stale, variances) to the DB.
    """
    user = _require_view(current_user)
    building_id = _get_building_id(user)
    db = _get_db()

    run = await db.trust_reconciliation_runs.find_one({"_id": ObjectId(run_id), "building_id": building_id})
    if not run:
        raise HTTPException(status_code=404, detail="Reconciliation run not found.")

    # Performance Optimization⚡: Parallelize data fetching for suggestions and anomalies.
    # Replaces 4 sequential fetches with 1 concurrent block.
    unmatched_bank_task = db.bank_statement_lines.find({
        "building_id": building_id,
        "reconciliation_run_id": run_id,
        "matched_status": "unmatched",
    }).to_list(1000)

    period_query: Dict[str, Any] = {"building_id": building_id}
    if run.get("statement_start_date") and run.get("statement_end_date"):
        period_query["$or"] = [
            {"transaction_date": {"$gte": run["statement_start_date"], "$lte": run["statement_end_date"]}},
            {"date": {"$gte": run["statement_start_date"], "$lte": run["statement_end_date"]}},
        ]
    internal_tx_task = db.trust_ledger_entries.find(period_query).limit(500).to_list(500)

    all_bank_task = db.bank_statement_lines.find({
        "building_id": building_id,
        "reconciliation_run_id": run_id
    }).to_list(2000)

    existing_matches_task = db.trust_reconciliation_matches.find({
        "building_id": building_id, "reconciliation_run_id": run_id, "unmatched": {"$ne": True}
    }).to_list(1000)

    unmatched_docs, internal_docs, all_bank_docs, matched_docs = await asyncio.gather(
        unmatched_bank_task, internal_tx_task, all_bank_task, existing_matches_task
    )

    bank_lines = []
    for doc in unmatched_docs:
        doc["id"] = str(doc["_id"])
        bank_lines.append(doc)

    internal_txs = []
    for doc in internal_docs:
        doc["id"] = str(doc["_id"])
        normalized_doc = _normalize_internal_tx_amount(doc, run.get("bank_account_id"))
        if normalized_doc is not None:
            internal_txs.append(normalized_doc)

    all_bank_lines = []
    for doc in all_bank_docs:
        doc["id"] = str(doc["_id"])
        all_bank_lines.append(doc)

    existing_matches = matched_docs

    # Run matching engine
    engine = MatchingEngine()
    suggestions = engine.find_best_matches(bank_lines, internal_txs, min_score=min_score)

    dup_bank = detect_duplicate_bank_lines(all_bank_lines)
    dup_internal = detect_duplicate_internal_transactions(internal_txs)
    stale = detect_stale_unmatched(all_bank_lines)
    variances = detect_suspicious_variances(bank_lines, internal_txs)
    many_one = detect_many_to_one(existing_matches)

    all_exceptions = dup_bank + dup_internal + stale + variances + many_one

    # Persist new exceptions (upsert by entity_id + exception_type + run_id)
    now = _now_iso()
    for exc in all_exceptions:
        await db.reconciliation_exceptions.update_one(
            {
                "building_id": building_id,
                "reconciliation_run_id": run_id,
                "exception_type": exc["exception_type"],
                "entity_id": exc.get("entity_id", ""),
            },
            {"$set": {
                **exc,
                "building_id": building_id,
                "reconciliation_run_id": run_id,
                "detected_at": now,
            }},
            upsert=True,
        )

    return {
        "run_id": run_id,
        "suggestions": [s.model_dump() for s in suggestions],
        "suggested_count": len(suggestions),
        "exceptions": all_exceptions,
        "exception_count": len(all_exceptions),
        "generated_at": now,
    }


# ─── Extended Summary ─────────────────────────────────────────────────────────

@router.get("/reconciliation/runs/{run_id}/summary")
async def get_reconciliation_summary(
        run_id: str,
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Extended reconciliation run summary including:
    - matched/unmatched counts, suggested count
    - duplicate warnings and other exceptions
    - total_unreconciled_amount_cents
    - reconciliation_confidence (fraction matched by amount)
    - current status and last_updated
    """
    user = _require_view(current_user)
    building_id = _get_building_id(user)
    if await _trust_pg_read_enabled(
        building_id,
        TRUST_RECONCILIATION_PG_ENABLED,
        "trust_reconciliation",
        route="GET /trust/reconciliation/runs/{run_id}/summary",
        current_user=current_user,
    ):
        pg_summary = await _trust_read_service.get_reconciliation_summary(
            building_id=building_id,
            run_id=run_id,
        )
        if pg_summary is not None:
            # Not wired for shadow-observation from this branch: trust_reconciliation's
            # mode can't reach postgres_read (where this branch would ever actually fire)
            # within this rollout's scope — see docs/migration/shadow-correction-updates-plan-p1.md
            # Phase G. Add a Mongo-independent recompute here if that ever changes.
            return pg_summary
    db = _get_db()

    # Performance Optimization⚡: Collapse multiple count and aggregation calls into a single $facet pipeline.
    # Reduces 6 sequential database round-trips into 2 (one for the run doc, one for stats).
    run = await db.trust_reconciliation_runs.find_one({"_id": ObjectId(run_id), "building_id": building_id})
    if not run:
        raise HTTPException(status_code=404, detail="Reconciliation run not found.")

    stats_pipeline = [
        {"$match": {"building_id": building_id, "reconciliation_run_id": run_id}},
        {"$facet": {
            "matched": [
                {"$match": {"matched_status": "matched"}},
                {"$group": {
                    "_id": None,
                    "count": {"$sum": 1},
                    "total_abs": {"$sum": {"$abs": "$amount_cents"}}
                }}
            ],
            "unmatched": [
                {"$match": {"matched_status": "unmatched"}},
                {"$group": {
                    "_id": None,
                    "count": {"$sum": 1},
                    "total": {"$sum": "$amount_cents"}
                }}
            ],
            "overall": [
                {"$group": {
                    "_id": None,
                    "total_abs": {"$sum": {"$abs": "$amount_cents"}}
                }}
            ]
        }}
    ]

    stats_task = db.bank_statement_lines.aggregate(stats_pipeline).to_list(1)
    exceptions_task = db.reconciliation_exceptions.find(
        {"building_id": building_id, "reconciliation_run_id": run_id}
    ).to_list(100)

    stats_res, exceptions_docs = await asyncio.gather(stats_task, exceptions_task)

    stats = stats_res[0] if stats_res else {}
    matched_data = stats.get("matched", [{}])[0] if stats.get("matched") else {}
    unmatched_data = stats.get("unmatched", [{}])[0] if stats.get("unmatched") else {}
    overall_data = stats.get("overall", [{}])[0] if stats.get("overall") else {}

    matched_count = matched_data.get("count", 0)
    unmatched_count = unmatched_data.get("count", 0)
    total_unreconciled = unmatched_data.get("total", 0)
    total_abs = overall_data.get("total_abs", 0)
    matched_abs = matched_data.get("total_abs", 0)

    # Count active suggestions (suggestions are ephemeral; set to 0 until a cache is implemented)
    suggested_count = 0
    reconciliation_confidence = round(matched_abs / total_abs, 4) if total_abs > 0 else 0.0

    # Fetch persisted exceptions for this run
    exceptions: List[Dict[str, Any]] = []
    for exc in exceptions_docs:
        exc.pop("_id", None)
        exceptions.append(exc)

    duplicate_warnings = [e for e in exceptions if "duplicate" in e.get("exception_type", "")]

    mongo_summary = {
        "run_id": run_id,
        "building_id": building_id,
        "status": run.get("status"),
        "matched_count": matched_count,
        "unmatched_bank_count": unmatched_count,
        "unmatched_internal_count": 0,  # requires internal tx tracking if needed
        "suggested_count": suggested_count,
        "duplicate_warnings": duplicate_warnings,
        "total_unreconciled_amount_cents": total_unreconciled,
        "total_unreconciled_display": format_aud(total_unreconciled),
        "reconciliation_confidence": reconciliation_confidence,
        "exceptions": exceptions,
        "exception_count": len(exceptions),
        "discrepancy_cents": run.get("discrepancy_cents", 0),
        "discrepancy_display": run.get("discrepancy_display", format_aud(0)),
        "last_updated": run.get("updated_at"),
        "statement_period": {
            "start": run.get("statement_start_date"),
            "end": run.get("statement_end_date"),
        },
    }
    _maybe_shadow_reconciliation_summary(building_id, run_id, mongo_payload=mongo_summary)
    return mongo_summary


def _maybe_shadow_reconciliation_summary(building_id: str, run_id: str, *, mongo_payload: dict) -> None:
    """Fire-and-forget shadow comparison for GET /trust/reconciliation/runs/{id}/summary.

    Only wired from the Mongo-primary branch (the only one reachable while
    trust_reconciliation's mode is mongo_primary — see the comment on the PG-primary
    branch above for why the other direction isn't built yet).
    """
    async def _run() -> None:
        """Generated function header.

        Function: _run
        Path: backend/routers/trust_reconciliation.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        try:
            from services.domain_source_guard import require_domain_source
            decision = await require_domain_source(
                domain="trust_reconciliation", building_id=building_id, operation="shadow_read",
            )
            if not decision.shadow_enabled:
                return
            from services.trust_shadow_read_service import compare_trust_reconciliation_summary
            pg_payload = await _trust_read_service.get_reconciliation_summary(
                building_id=building_id, run_id=run_id,
            )
            await compare_trust_reconciliation_summary(
                building_id=building_id, mongo_payload=mongo_payload, pg_payload=pg_payload,
            )
        except Exception as exc:
            logger.debug("trust reconciliation summary shadow compare failed: %s", exc)

    from services.shadow_read_service import schedule_shadow_compare
    schedule_shadow_compare(
        _run(), domain="trust_reconciliation",
        context={"building_id": building_id, "route": "trust_reconciliation.summary"},
    )


@router.post("/reconciliation/runs/{run_id}/close")
async def close_reconciliation_run(
        run_id: str,
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Close a reconciliation run. Requires no unmatched lines (or explicit override)."""
    user = _require_manage(current_user)
    building_id = _get_building_id(user)
    db = _get_db()

    run = await db.trust_reconciliation_runs.find_one({"_id": ObjectId(run_id), "building_id": building_id})
    if not run:
        raise HTTPException(status_code=404, detail="Reconciliation run not found.")
    if run.get("status") == ReconciliationStatus.CLOSED.value:
        raise HTTPException(status_code=400, detail="Run is already closed.")

    unmatched = await db.bank_statement_lines.count_documents({
        "reconciliation_run_id": run_id, "matched_status": "unmatched"
    })

    # Compute discrepancy: closing_balance - opening_balance - net_transactions
    closing = run.get("closing_balance_cents", 0)
    opening = run.get("opening_balance_cents", 0)
    net_pipeline = [
        {"$match": {"reconciliation_run_id": run_id, "matched_status": "matched"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount_cents"}}}
    ]
    net_result = await db.bank_statement_lines.aggregate(net_pipeline).to_list(1)
    net_transactions = net_result[0]["total"] if net_result else 0
    discrepancy = closing - opening - net_transactions

    await db.trust_reconciliation_runs.update_one(
        {"_id": ObjectId(run_id)},
        {"$set": {
            "status": ReconciliationStatus.CLOSED.value,
            "discrepancy_cents": discrepancy,
            "discrepancy_display": format_aud(discrepancy),
            "unmatched_count": unmatched,
            "reviewed_by": user.get("email"),
            "closed_at": _now_iso(),
            "updated_at": _now_iso(),
        }}
    )

    import asyncio
    asyncio.create_task(create_audit_log(
        db=db,
        action="reconciliation_run_closed",
        entity_type="trust_reconciliation_run",
        entity_id=run_id,
        user_id=user.get("email"),
        building_id=building_id,
        details={"discrepancy_cents": discrepancy, "unmatched_remaining": unmatched},
    ))

    return {
        "run_id": run_id,
        "status": "closed",
        "discrepancy_cents": discrepancy,
        "discrepancy_display": format_aud(discrepancy),
        "unmatched_remaining": unmatched,
    }


# ─── Period Locks ─────────────────────────────────────────────────────────────

@router.post("/period-locks", status_code=status.HTTP_201_CREATED)
async def create_period_lock(
        payload: PeriodLockCreate,
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Lock a financial period to prevent new postings."""
    user = _require_manage(current_user)
    building_id = _get_building_id(user)
    db = _get_db()

    # Check for overlapping active locks
    overlap = await db.trust_period_locks.find_one({
        "building_id": building_id,
        "fund_type": payload.fund_type.value,
        "is_active": True,
        "$or": [
            {"period_start": {"$lte": payload.period_end}, "period_end": {"$gte": payload.period_start}},
        ]
    })
    if overlap:
        raise HTTPException(
            status_code=400,
            detail=f"Period lock already exists for {payload.fund_type.value} overlapping {payload.period_start} to {payload.period_end}."
        )

    doc = payload.model_dump()
    doc["fund_type"] = payload.fund_type.value
    doc["building_id"] = building_id
    doc["locked_by"] = user.get("email")
    doc["locked_at"] = _now_iso()
    doc["is_active"] = True
    doc["created_at"] = _now_iso()

    result = await db.trust_period_locks.insert_one(doc)
    lock_id = str(result.inserted_id)

    import asyncio
    asyncio.create_task(create_audit_log(
        db=db,
        action="period_lock_created",
        entity_type="trust_period_lock",
        entity_id=lock_id,
        user_id=user.get("email"),
        building_id=building_id,
        details={
            "fund_type": payload.fund_type.value,
            "period": f"{payload.period_start} to {payload.period_end}",
        },
    ))
    return {"id": lock_id, "is_active": True, "fund_type": payload.fund_type.value}


@router.get("/period-locks")
async def list_period_locks(
        fund_type: Optional[str] = Query(None),
        active_only: bool = Query(True),
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """List period locks for this building."""
    user = _require_view(current_user)
    building_id = _get_building_id(user)
    db = _get_db()

    query: Dict[str, Any] = {"building_id": building_id}
    if fund_type:
        query["fund_type"] = fund_type
    if active_only:
        query["is_active"] = True

    docs = await db.trust_period_locks.find(query).sort("period_start", -1).to_list(10000)
    locks = []
    for doc in docs:
        doc["id"] = str(doc.pop("_id"))
        locks.append(doc)
    return {"locks": locks, "total": len(locks)}


@router.delete("/period-locks/{lock_id}")
async def unlock_period(
        lock_id: str,
        reason: Optional[str] = Query(None),
        current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Unlock a period lock. Requires super_admin role."""
    user = current_user
    if effective_role(user) != "super_admin":
        raise HTTPException(status_code=403, detail="Only super_admin can unlock periods.")

    building_id = _get_building_id(user)
    db = _get_db()

    lock = await db.trust_period_locks.find_one({"_id": ObjectId(lock_id), "building_id": building_id})
    if not lock:
        raise HTTPException(status_code=404, detail="Period lock not found.")

    await db.trust_period_locks.update_one(
        {"_id": ObjectId(lock_id)},
        {"$set": {
            "is_active": False,
            "unlocked_by": user.get("email"),
            "unlocked_at": _now_iso(),
            "unlock_reason": reason,
        }}
    )

    import asyncio
    asyncio.create_task(create_audit_log(
        db=db,
        action="period_lock_removed",
        entity_type="trust_period_lock",
        entity_id=lock_id,
        user_id=user.get("email"),
        building_id=building_id,
        details={"reason": reason},
    ))
    return {"id": lock_id, "unlocked": True}
