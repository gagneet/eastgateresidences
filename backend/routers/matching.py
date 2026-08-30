"""
3-Way Matching Engine: PO → Invoice → Bank Transaction

Implements the classic accounts payable control:
  1. Purchase Order (PO): commitment to spend
  2. Invoice: supplier's claim for payment
  3. Bank Transaction: actual payment made

Matching logic:
  - Auto-match by: amount (±tolerance), reference, date proximity
  - Confidence scoring (0-100)
  - Partial matching support
  - Exception flagging for discrepancies

Collections used:
  purchase_orders    : PO commitments
  invoices           : supplier invoices (may reference existing maintenance/work_order)
  matching_results   : match records linking PO→Invoice→BankTx

Endpoints:
  POST  /matching/purchase-orders             — create PO
  GET   /matching/purchase-orders             — list POs
  POST  /matching/invoices                    — create invoice
  GET   /matching/invoices                    — list invoices
  POST  /matching/auto-match                  — run auto-matching for a building
  GET   /matching/results                     — list match results
  GET   /matching/exceptions                  — list unmatched/discrepant items
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, date
from bson.errors import InvalidId

import asyncio
import logging
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from pymongo import UpdateOne, InsertOne
from typing import Optional

from utils.auth import get_current_user, get_current_building
from utils.helpers import create_audit_log
from models.user import UserRole
from utils.route_guards import assert_roles

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/matching", tags=["3-Way Matching"])


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/matching.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/matching.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_finance(user: dict) -> dict:
    """Allow finance operations for management and the committee.

    Delegates to utils.route_guards so the role set is validated against UserRole
    at import time and the role is resolved through effective_role().

    This guard read `user.get("role")` directly until 2026-08-28. A temporarily
    elevated user keeps their underlying role ("owner") and carries the elevated
    one in `effective_role`, so this 403'd exactly the EC members elevation exists
    to admit — the last raw-role guard admitting ec_member in the router tree. It
    also tested for "treasurer" and "admin", neither of which is a UserRole, so
    those two conditions could never match.
    """
    return assert_roles(
        user,
        {UserRole.SUPER_ADMIN, UserRole.STRATA_MANAGER, UserRole.STRATA_ADMIN, UserRole.EC_MEMBER},
        detail="Finance access required.",
    )


# ─── Models ───────────────────────────────────────────────────────────────────

class PurchaseOrderCreate(BaseModel):
    building_id: str
    fund_type: str = "admin_fund"
    po_number: Optional[str] = None
    vendor_name: str
    vendor_abn: Optional[str] = None
    description: str
    amount: float = Field(..., gt=0)
    gst_amount: float = 0.0
    issue_date: str
    expected_delivery_date: Optional[str] = None
    budget_category: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    notes: Optional[str] = None


class InvoiceCreate(BaseModel):
    building_id: str
    fund_type: str = "admin_fund"
    invoice_number: str
    vendor_name: str
    vendor_abn: Optional[str] = None
    description: str
    amount: float = Field(..., gt=0)
    gst_amount: float = 0.0
    invoice_date: str
    due_date: Optional[str] = None
    po_id: Optional[str] = None  # link to PO if known
    maintenance_id: Optional[str] = None
    work_order_id: Optional[str] = None
    notes: Optional[str] = None


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/purchase-orders", status_code=201)
async def create_purchase_order(
        payload: PurchaseOrderCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: create_purchase_order
    Path: backend/routers/matching.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    user = _require_finance(current_user)
    db = _get_db()

    # Validate user ID exists to prevent KeyError
    if "id" not in user:
        raise HTTPException(500, "User ID is missing from authentication context")

    doc = payload.model_dump()
    # Sentinel 🛡️: Force verified building context to prevent BOLA/IDOR spoofing.
    doc["building_id"] = building_id
    doc["status"] = "open"  # open | invoiced | matched | cancelled
    doc["invoiced_amount"] = 0.0
    doc["matched_amount"] = 0.0
    doc["created_at"] = _now_iso()
    doc["created_by"] = user["id"]

    result = await db.purchase_orders.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    asyncio.create_task(create_audit_log(
        action="po_created",
        user_id=user["id"],
        user_name=user.get("full_name", "Unknown"),
        resource_type="purchase_order",
        resource_id=doc["id"],
        details={"vendor": payload.vendor_name, "amount": payload.amount},
        building_id=building_id,
    ))
    return doc


@router.get("/purchase-orders")
async def list_purchase_orders(
        building_id: str = Depends(get_current_building),
        po_status: Optional[str] = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: list_purchase_orders
    Path: backend/routers/matching.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_finance(current_user)
    db = _get_db()

    query: dict = {"building_id": building_id}
    if po_status:
        query["status"] = po_status

    skip = (page - 1) * page_size
    # Performance Optimization⚡: Parallelize count and data fetch, use to_list() for faster retrieval
    count_task = db.purchase_orders.count_documents(query)
    data_task = db.purchase_orders.find(query).sort("issue_date", -1).skip(skip).limit(page_size).to_list(page_size)

    total, items = await asyncio.gather(count_task, data_task)

    for doc in items:
        doc["id"] = str(doc.pop("_id"))
    return {"data": items, "total": total, "page": page}


@router.post("/invoices", status_code=201)
async def create_invoice(
        payload: InvoiceCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: create_invoice
    Path: backend/routers/matching.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    user = _require_finance(current_user)
    db = _get_db()

    # Validate user ID exists to prevent KeyError
    if "id" not in user:
        raise HTTPException(500, "User ID is missing from authentication context")

    doc = payload.model_dump()
    # Sentinel 🛡️: Force verified building context to prevent BOLA/IDOR spoofing.
    doc["building_id"] = building_id
    doc["status"] = "pending"  # pending | approved | matched | paid | disputed
    doc["matched_tx_id"] = None
    doc["match_confidence"] = 0
    doc["created_at"] = _now_iso()
    doc["created_by"] = user["id"]

    result = await db.invoices.insert_one(doc)
    doc["id"] = str(result.inserted_id)

    # If PO provided, update PO invoiced_amount
    if payload.po_id:
        # Validate ObjectId format to prevent crashes
        try:
            po_object_id = ObjectId(payload.po_id)
        except (InvalidId, TypeError, ValueError) as e:
            raise HTTPException(400, f"Invalid purchase order ID format: {str(e)}")

        # Validate that PO belongs to current building before updating (prevent BOLA)
        existing_po = await db.purchase_orders.find_one({
            "_id": po_object_id,
            "building_id": building_id
        })

        if not existing_po:
            raise HTTPException(
                404,
                "Purchase order not found or does not belong to the current building"
            )

        await db.purchase_orders.update_one(
            {"_id": po_object_id, "building_id": building_id},
            {"$inc": {"invoiced_amount": payload.amount},
             "$set": {"status": "invoiced"}},
        )

    asyncio.create_task(create_audit_log(
        action="invoice_created",
        user_id=user["id"],
        user_name=user.get("full_name", "Unknown"),
        resource_type="invoice",
        resource_id=doc["id"],
        details={"invoice_number": payload.invoice_number, "amount": payload.amount},
        building_id=building_id,
    ))
    return doc


@router.get("/invoices")
async def list_invoices(
        building_id: str = Depends(get_current_building),
        inv_status: Optional[str] = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: list_invoices
    Path: backend/routers/matching.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_finance(current_user)
    db = _get_db()

    query: dict = {"building_id": building_id}
    if inv_status:
        query["status"] = inv_status

    skip = (page - 1) * page_size
    # Performance Optimization⚡: Parallelize count and data fetch, use to_list() for faster retrieval
    count_task = db.invoices.count_documents(query)
    data_task = db.invoices.find(query).sort("invoice_date", -1).skip(skip).limit(page_size).to_list(page_size)

    total, items = await asyncio.gather(count_task, data_task)

    for doc in items:
        doc["id"] = str(doc.pop("_id"))
    return {"data": items, "total": total, "page": page}


@router.post("/auto-match")
async def run_auto_match(
        building_id: str = Depends(get_current_building),
        tolerance_aud: float = Query(default=0.01, ge=0),
        date_window_days: int = Query(default=7, ge=1, le=30),
        current_user: dict = Depends(get_current_user),
):
    """Run automatic 3-way matching for a building.

    Attempts to link:
      1. Unmatched invoices → unmatched bank transactions (by amount + date)
      2. Matched Invoice+BankTx → open POs (by vendor + amount)

    Returns a summary of matches found.
    Performance Optimization⚡: Using batch fetching and bulk writes to eliminate N+1 DB round-trips.
    Improved for reliability: memory validation, concurrency locking, and atomic transactions.
    """
    user = _require_finance(current_user)
    db = _get_db()

    # Validate user ID exists to prevent KeyError
    if "id" not in user:
        raise HTTPException(500, "User ID is missing from authentication context")

    # ⚡ Data Volume Validation: Prevent memory exhaustion
    MAX_INVOICES = 1000
    MAX_TRANSACTIONS = 5000

    invoice_count, tx_count = await asyncio.gather(
        db.invoices.count_documents({
            "building_id": building_id,
            "matched_tx_id": None,
            "status": {"$in": ["pending", "approved"]},
        }),
        db.bank_transactions.count_documents({
            "building_id": building_id,
            "matched": False,
            "type": "debit",
        })
    )

    if invoice_count > MAX_INVOICES or tx_count > MAX_TRANSACTIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Dataset too large for batch processing: {invoice_count} invoices, "
                f"{tx_count} transactions. Maximum: {MAX_INVOICES} invoices, {MAX_TRANSACTIONS} transactions."
            )
        )

    # ⚡ Concurrency Lock: Prevent multiple runs for same building
    lock_id = f"auto_match_lock_{building_id}"
    try:
        await db.locks.insert_one({"id": lock_id, "locked_at": _now_iso(), "user_id": user["id"]})
    except Exception:
        # Check if lock is stale (older than 10 mins)
        stale_lock = await db.locks.find_one({"id": lock_id})
        if stale_lock:
            locked_at = datetime.fromisoformat(stale_lock["locked_at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - locked_at < timedelta(minutes=10):
                raise HTTPException(409,
                                    "Auto-match is already running for this building. Please try again in 10 minutes.")
            # Remove stale lock
            await db.locks.delete_one({"id": lock_id})
            await db.locks.insert_one({"id": lock_id, "locked_at": _now_iso(), "user_id": user["id"]})
        else:
            raise HTTPException(409, "Conflict: auto-match lock acquired by another process.")

    try:
        # ⚡ Batch Fetch candidate records in parallel
        invoices_task = db.invoices.find({
            "building_id": building_id,
            "matched_tx_id": None,
            "status": {"$in": ["pending", "approved"]},
        }).to_list(MAX_INVOICES)

        tx_task = db.bank_transactions.find({
            "building_id": building_id,
            "matched": False,
            "type": "debit",
        }).to_list(MAX_TRANSACTIONS)

        unmatched_invoices, raw_txs = await asyncio.gather(invoices_task, tx_task)

        # ⚡ Pre-parse transaction dates
        unmatched_txs = []
        for tx in raw_txs:
            tx_date_str = tx.get("date", "")
            try:
                tx_date_val = date.fromisoformat(tx_date_str[:10])
                unmatched_txs.append({**tx, "_parsed_date": tx_date_val})
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Skipping bank transaction {tx.get('_id')} due to invalid date format: '{tx_date_str}'. Error: {e}")
                continue

        invoice_updates = []
        tx_updates = []
        matching_results_docs = []
        matched_count = 0
        now = _now_iso()

        # Track matched transactions in this run to avoid double-matching
        matched_tx_ids_local = set()

        for inv in unmatched_invoices:
            inv_amount = inv.get("amount", 0)
            inv_date_str = inv.get("invoice_date", "")

            try:
                inv_date_val = date.fromisoformat(inv_date_str[:10])
                from_date = inv_date_val - timedelta(days=date_window_days)
                to_date = inv_date_val + timedelta(days=date_window_days)
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Skipping invoice {inv.get('_id')} due to invalid date format: '{inv_date_str}'. Error: {e}")
                continue

            # ⚡ Efficient In-Memory Matching
            best_tx = None
            for tx in unmatched_txs:
                tx_id = str(tx["_id"])
                if tx_id in matched_tx_ids_local:
                    continue

                tx_amount = tx.get("amount", 0)
                if not (inv_amount - tolerance_aud <= tx_amount <= inv_amount + tolerance_aud):
                    continue

                if not (from_date <= tx["_parsed_date"] <= to_date):
                    continue

                # Found a match
                best_tx = tx
                break

            if best_tx:
                tx_id = str(best_tx["_id"])
                inv_id = inv["_id"]
                matched_tx_ids_local.add(tx_id)
                confidence = 100 if abs(best_tx["amount"] - inv_amount) < 0.001 else 85

                # ⚡ Accumulate Updates for Bulk Execution
                # Note: filters include matched: False/None to ensure atomicity even without transactions
                invoice_updates.append(UpdateOne(
                    {"_id": inv_id, "building_id": building_id, "matched_tx_id": None},
                    {"$set": {
                        "matched_tx_id": tx_id,
                        "match_confidence": confidence,
                        "status": "matched",
                        "matched_at": now,
                    }}
                ))

                tx_updates.append(UpdateOne(
                    {"_id": best_tx["_id"], "building_id": building_id, "matched": False},
                    {"$set": {
                        "matched": True,
                        "matched_to_id": str(inv_id),
                        "match_confidence": confidence,
                        "match_type": "auto",
                        "matched_at": now,
                    }}
                ))

                matching_results_docs.append(InsertOne({
                    "building_id": building_id,
                    "invoice_id": str(inv_id),
                    "bank_tx_id": tx_id,
                    "po_id": inv.get("po_id"),
                    "match_type": "auto",
                    "confidence": confidence,
                    "invoice_amount": inv_amount,
                    "tx_amount": best_tx["amount"],
                    "matched_at": now,
                    "matched_by": user["id"],
                }))
                matched_count += 1

        # ⚡ Atomic Persistence: Wrap bulk writes in a session transaction where possible
        if invoice_updates:
            try:
                async with db._db.client.start_session() as session:
                    async with await session.start_transaction():
                        await asyncio.gather(
                            db.invoices.bulk_write(invoice_updates, session=session),
                            db.bank_transactions.bulk_write(tx_updates, session=session),
                            db.matching_results.bulk_write(matching_results_docs, session=session)
                        )
            except Exception as e:
                # Fallback for environments where transactions are not supported (e.g. standalone Mongo)
                if "Storage engine does not support transactions" in str(e) or "transaction" not in str(e).lower():
                    logger.info("Transactions not supported, falling back to non-transactional bulk write.")
                    await asyncio.gather(
                        db.invoices.bulk_write(invoice_updates),
                        db.bank_transactions.bulk_write(tx_updates),
                        db.matching_results.bulk_write(matching_results_docs)
                    )
                else:
                    logger.error(f"Failed to commit auto-match results building={building_id}: {e}")
                    raise HTTPException(500, f"Match persistence failed: {e}")

        return {
            "building_id": building_id,
            "invoices_processed": len(unmatched_invoices),
            "matches_found": matched_count,
            "tolerance_aud": tolerance_aud,
            "date_window_days": date_window_days,
            "run_at": now,
        }
    finally:
        # ⚡ Release Lock
        await db.locks.delete_one({"id": lock_id})


@router.get("/results")
async def list_match_results(
        building_id: str = Depends(get_current_building),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: list_match_results
    Path: backend/routers/matching.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_finance(current_user)
    db = _get_db()

    skip = (page - 1) * page_size
    # Performance Optimization⚡: Parallelize count and data fetch, use to_list() for faster retrieval
    count_task = db.matching_results.count_documents({"building_id": building_id})
    data_task = db.matching_results.find({"building_id": building_id}).sort("matched_at", -1).skip(skip).limit(
        page_size).to_list(page_size)

    total, items = await asyncio.gather(count_task, data_task)

    for doc in items:
        doc["id"] = str(doc.pop("_id"))
    return {"data": items, "total": total, "page": page}


@router.get("/exceptions")
async def get_matching_exceptions(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Return unmatched invoices, unmatched bank transactions, and over-tolerance POs."""
    _require_finance(current_user)
    db = _get_db()

    # Performance Optimization⚡: Parallelize database queries to reduce latency.
    # Using to_list() instead of async for loops for faster retrieval.
    inv_task = db.invoices.find({
        "building_id": building_id,
        "matched_tx_id": None,
        "status": {"$in": ["pending", "approved"]},
    }).to_list(500)

    tx_task = db.bank_transactions.find({
        "building_id": building_id,
        "matched": False,
        "type": "debit",
    }).limit(100).to_list(100)

    inv_docs, tx_docs = await asyncio.gather(inv_task, tx_task)

    for doc in inv_docs:
        doc["id"] = str(doc.pop("_id"))

    for doc in tx_docs:
        doc["id"] = str(doc.pop("_id"))

    return {
        "building_id": building_id,
        "unmatched_invoices": inv_docs,
        "unmatched_bank_transactions": tx_docs,
        "total_exceptions": len(inv_docs) + len(tx_docs),
    }
