"""
Financial Repository — MongoDB query layer for the refactored financial schema.

Responsibilities
----------------
* Create MongoDB indexes for the new financial collections.
* Provide typed aggregation pipelines that the service layer can reuse.
* Centralise all direct db.collection calls for the new collections so services
  stay clean and testable.

Backward-compatibility strategy
---------------------------------
This module only touches the NEW collections introduced by the refactor:
  financial_years, financial_categories, financial_transactions, levy_plans

Legacy collections (annual_levies, levy_categories, unit_levy_ledger, finance,
budgets) are never written or deleted here.  They continue to be used by the
existing finance.py and finance_helpers.py code paths.

Index creation
--------------
Call ``ensure_indexes()`` once at application startup (e.g. from server.py).
All index creation is idempotent — Motor/MongoDB will silently skip indexes
that already exist.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from typing import Any, Dict, List, Optional

from database import db

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Generated function header.

    Function: _new_id
    Path: backend/repositories/financial_repository.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(uuid.uuid4())


# ─────────────────────────────────────────────────────────────────────────────
# Index management
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_key_spec(key_spec: list) -> List[tuple]:
    """Generated function header.

    Function: _normalise_key_spec
    Path: backend/repositories/financial_repository.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return [tuple(k) for k in key_spec]


def _relevant_index_options(options: Dict[str, Any]) -> Dict[str, Any]:
    """Extract only the options that affect index semantics for equality comparison."""
    relevant_keys = ("unique", "sparse", "partialFilterExpression", "expireAfterSeconds", "collation")
    return {k: options[k] for k in relevant_keys if k in options}


async def _safe_create_index(collection, key_spec: list, **kwargs) -> None:
    """Create a named index only if an exact match (key spec + options) does not already exist.

    Prevents IndexOptionsConflict (code 85) when the same key spec was previously
    created with an auto-generated name (e.g. building_id_1_expiry_date_1) before
    the bid_* naming convention was introduced.  Idempotent for both old and new DBs.
    If an index with matching key spec but different options is found, logs a warning
    and skips creation to avoid a transient drop of a live index.
    """
    desired_key = _normalise_key_spec(key_spec)
    desired_opts = _relevant_index_options(kwargs)
    existing = await collection.index_information()
    for info in existing.values():
        if _normalise_key_spec(info.get("key", [])) == desired_key:
            existing_opts = _relevant_index_options(info)
            if existing_opts == desired_opts:
                return  # exact match — skip
            logger.warning(
                "Index on %s has matching key spec but different options: "
                "existing=%s desired=%s — skipping creation to avoid live index drop.",
                collection.name, existing_opts, desired_opts,
            )
            return
    await collection.create_index(key_spec, **kwargs)


async def ensure_indexes() -> None:
    """
    Create all required indexes for the new financial collections.
    Safe to call multiple times — indexes are idempotent.
    """
    # financial_years
    await db.financial_years.create_index([("year", 1), ("building_id", 1)], unique=True)
    await db.financial_years.create_index([("status", 1)])

    # financial_categories
    await db.financial_categories.create_index(
        [("financial_year", 1), ("fund_type", 1), ("name", 1), ("building_id", 1)],
        unique=True
    )
    await db.financial_categories.create_index([("financial_year", 1)])
    await db.financial_categories.create_index([("fund_type", 1)])

    # financial_transactions
    await db.financial_transactions.create_index([("financial_year", 1), ("building_id", 1)])
    await db.financial_transactions.create_index([("fund_type", 1)])
    await db.financial_transactions.create_index([("transaction_type", 1)])
    await db.financial_transactions.create_index([("transaction_date", 1)])
    await db.financial_transactions.create_index([("lot_number", 1)])
    await db.financial_transactions.create_index([("category_id", 1)])
    # Compound index for the most common dashboard aggregation
    await db.financial_transactions.create_index(
        [("financial_year", 1), ("fund_type", 1), ("transaction_type", 1)]
    )

    # levy_payments (legacy but high-traffic)
    await db.levy_payments.create_index([("building_id", 1), ("unit_number", 1), ("year", 1)])
    await db.levy_payments.create_index([("building_id", 1), ("status", 1)])
    await db.levy_payments.create_index([("building_id", 1), ("created_at", -1)])

    # expense_transactions (Phase P1)
    await db.expense_transactions.create_index([("building_id", 1), ("financial_year", 1), ("fund_type_short", 1)])
    await db.expense_transactions.create_index([("building_id", 1), ("date", -1)])

    # unit_levy_ledger (Core Accounting)
    await db.unit_levy_ledger.create_index([("building_id", 1), ("year", 1), ("unit_number", 1)], unique=True)
    await db.unit_levy_ledger.create_index([("building_id", 1), ("year", 1), ("net_balance", 1)])

    # GAP-PERF-002: annual_levies + units are read on every finance-aggregation
    # request (/stats/building-kpis, /finance/summary, /finance/kpi-contract,
    # /arrears/detail, /bi/building/{id}/financial-summary) but had NO
    # building_id-leading index — so annual_levies.find_one({building_id, year})
    # and units.count_documents({building_id}) each did a full collection scan
    # across ALL buildings on every call, compounding under concurrency (measured
    # p95 ~8.9s on building_kpis / bi_financial_summary in the k6 baseline). These
    # indexes change those scans into index lookups; the computed values are
    # unchanged (index-only optimisation).
    await db.annual_levies.create_index([("building_id", 1), ("year", 1)])
    # units: count_documents({building_id}) uses the building_id prefix;
    # find_one({building_id, unit_number}) and find({building_id}).sort(unit_number)
    # use the full compound. Non-unique to tolerate any pre-existing duplicate rows.
    await db.units.create_index([("building_id", 1), ("unit_number", 1)])

    # levy_plans
    await db.levy_plans.create_index(
        [("financial_year", 1), ("fund_type", 1), ("building_id", 1)],
        unique=True
    )
    await db.levy_plans.create_index([("financial_year", 1)])

    # invoice_documents
    await db.invoice_documents.create_index([("building_id", 1), ("status", 1)])
    await db.invoice_documents.create_index([("building_id", 1), ("created_at", -1)])
    await db.invoice_documents.create_index([("id", 1), ("building_id", 1)], unique=True)

    # DB-021: financial_transactions — Statement of Accounts + per-fund period reports
    await _safe_create_index(
        db.financial_transactions,
        [("building_id", 1), ("lot_number", 1), ("transaction_date", -1)],
        name="bid_lot_txdate_desc",
    )
    await _safe_create_index(
        db.financial_transactions,
        [("building_id", 1), ("fund_type", 1), ("transaction_date", -1)],
        name="bid_fund_txdate_desc",
    )

    # DB-021: levy_payments — compound index for arrears-by-unit query
    # $ne is not supported in MongoDB partial filter expressions (any version).
    # Compound (building_id, unit_number, status) covers the same query pattern.
    await _safe_create_index(
        db.levy_payments,
        [("building_id", 1), ("unit_number", 1), ("status", 1)],
        name="bid_unit_status",
    )

    # DB-021: bank_transactions — reconciliation amount-matching + unmatched queue
    await _safe_create_index(
        db.bank_transactions,
        [("building_id", 1), ("transaction_date", 1), ("amount_cents", 1)],
        name="bid_date_amount",
    )
    await _safe_create_index(
        db.bank_transactions,
        [("building_id", 1), ("status", 1)],
        name="bid_status_unmatched_partial",
        partialFilterExpression={"status": "unmatched"},
    )

    # DB-021: trust_ledger_entries — trust balance aggregation per account
    await _safe_create_index(
        db.trust_ledger_entries,
        [("building_id", 1), ("trust_account_id", 1), ("created_at", -1)],
        name="bid_account_created_desc",
    )

    # DB-021: insurance_policies — renewal alert cron + compliance type filter
    # Live DB has these as building_id_1_expiry_date_1 / building_id_1_policy_type_1
    # (auto-named before bid_* convention). _safe_create_index skips if key spec
    # already exists under any name, preventing IndexOptionsConflict code 85.
    await _safe_create_index(
        db.insurance_policies,
        [("building_id", 1), ("expiry_date", 1)],
        name="bid_expiry",
    )
    await _safe_create_index(
        db.insurance_policies,
        [("building_id", 1), ("policy_type", 1)],
        name="bid_policy_type",
    )


# ─────────────────────────────────────────────────────────────────────────────
# financial_years CRUD
# ─────────────────────────────────────────────────────────────────────────────

async def upsert_financial_year(data: Dict[str, Any], building_id: str) -> Dict[str, Any]:
    """
    Insert or update a financial year document.
    Matches on (year, building_id).  Returns the full stored document.
    """
    year = data["year"]
    now = _now()

    doc = {**data, "building_id": building_id, "updated_at": now}
    # For backward compatibility if some code still uses plan_id
    doc.pop("plan_id", None)

    existing = await db.financial_years.find_one(
        {"year": year, "building_id": building_id}, {"_id": 0}
    )
    if not existing:
        doc.setdefault("id", _new_id())
        doc.setdefault("created_at", now)
        await db.financial_years.insert_one({k: v for k, v in doc.items() if k != "_id"})
    else:
        await db.financial_years.update_one(
            {"year": year, "building_id": building_id},
            {"$set": doc}
        )
    return await db.financial_years.find_one(
        {"year": year, "building_id": building_id}, {"_id": 0}
    )


async def get_financial_year(year: str, building_id: str) -> Optional[Dict[str, Any]]:
    """Generated function header.

    Function: get_financial_year
    Path: backend/repositories/financial_repository.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await db.financial_years.find_one(
        {"year": year, "building_id": building_id}, {"_id": 0}
    )


async def list_financial_years(building_id: str) -> List[Dict[str, Any]]:
    """Generated function header.

    Function: list_financial_years
    Path: backend/repositories/financial_repository.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await db.financial_years.find(
        {"building_id": building_id}, {"_id": 0}
    ).sort("year", -1).to_list(20)


# ─────────────────────────────────────────────────────────────────────────────
# financial_categories CRUD
# ─────────────────────────────────────────────────────────────────────────────

async def upsert_financial_category(data: Dict[str, Any], building_id: str) -> Dict[str, Any]:
    """
    Insert or update a financial category.
    Matches on (financial_year, fund_type, name, building_id).
    """
    key = {
        "financial_year": data["financial_year"],
        "fund_type": data["fund_type"],
        "name": data["name"],
        "building_id": building_id,
    }
    now = _now()
    doc = {**key, **data, "updated_at": now}
    doc.pop("plan_id", None)
    existing = await db.financial_categories.find_one(key, {"_id": 0})
    if not existing:
        doc.setdefault("id", _new_id())
        doc.setdefault("created_at", now)
        await db.financial_categories.insert_one({k: v for k, v in doc.items() if k != "_id"})
    else:
        await db.financial_categories.update_one(key, {"$set": doc})
    return await db.financial_categories.find_one(key, {"_id": 0})


async def get_financial_categories(
        year: str,
        fund_type: Optional[str] = None,
        building_id: str = None,
) -> List[Dict[str, Any]]:
    """Generated function header.

    Function: get_financial_categories
    Path: backend/repositories/financial_repository.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query: Dict[str, Any] = {"financial_year": year, "building_id": building_id}
    if fund_type:
        query["fund_type"] = fund_type
    return await db.financial_categories.find(
        query, {"_id": 0}
    ).sort("name", 1).to_list(200)


# ─────────────────────────────────────────────────────────────────────────────
# financial_transactions CRUD + aggregation
# ─────────────────────────────────────────────────────────────────────────────

async def insert_transaction(data: Dict[str, Any]) -> Dict[str, Any]:
    """Record a new financial transaction. Returns stored document."""
    now = _now()
    doc = {
        "id": _new_id(),
        "created_at": now,
        **data,
    }
    await db.financial_transactions.insert_one({k: v for k, v in doc.items() if k != "_id"})
    return {k: v for k, v in doc.items() if k != "_id"}


async def list_transactions(
        year: Optional[str] = None,
        fund_type: Optional[str] = None,
        transaction_type: Optional[str] = None,
        lot_number: Optional[str] = None,
        category_name: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        building_id: str = None,
        limit: int = 500,
) -> List[Dict[str, Any]]:
    """Generated function header.

    Function: list_transactions
    Path: backend/repositories/financial_repository.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query: Dict[str, Any] = {"building_id": building_id}
    if year:
        query["financial_year"] = year
    if fund_type:
        query["fund_type"] = fund_type
    if transaction_type:
        query["transaction_type"] = transaction_type
    if lot_number:
        query["lot_number"] = lot_number
    if category_name:
        query["category_name"] = category_name
    if date_from or date_to:
        date_filter: Dict[str, str] = {}
        if date_from:
            date_filter["$gte"] = date_from
        if date_to:
            date_filter["$lte"] = date_to
        query["transaction_date"] = date_filter

    return await db.financial_transactions.find(
        query, {"_id": 0}
    ).sort("transaction_date", -1).to_list(limit)


async def aggregate_actual_by_category(
        year: str, fund_type: Optional[str] = None, building_id: str = None
) -> Dict[str, float]:
    """
    Aggregate total expense transactions grouped by category_name for a year.
    Returns {category_name: total_amount}.

    This is the authoritative source for actual_amount when using the new
    financial_transactions collection.
    """
    match: Dict[str, Any] = {
        "financial_year": year,
        "building_id": building_id,
        "transaction_type": "expense",
    }
    if fund_type:
        match["fund_type"] = fund_type

    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$category_name", "total": {"$sum": "$amount"}}},
    ]
    results = await db.financial_transactions.aggregate(pipeline).to_list(200)
    return {r["_id"]: round(r["total"], 2) for r in results if r["_id"]}


async def aggregate_fund_totals(year: str, building_id: str = None) -> Dict[str, Any]:
    """
    Aggregate income and expense totals per fund type for a year.
    Returns dict keyed by fund_type with income/expense sub-totals.
    """
    pipeline = [
        {"$match": {"financial_year": year, "building_id": building_id}},
        {
            "$group": {
                "_id": {"fund_type": "$fund_type", "tx_type": "$transaction_type"},
                "total": {"$sum": "$amount"},
            }
        },
    ]
    results = await db.financial_transactions.aggregate(pipeline).to_list(20)
    totals: Dict[str, Any] = {}
    for r in results:
        ft = r["_id"]["fund_type"]
        tt = r["_id"]["tx_type"]
        if ft not in totals:
            totals[ft] = {"income": 0.0, "expense": 0.0}
        if tt in ("income", "levy", "interest", "special_levy"):
            totals[ft]["income"] = round(totals[ft]["income"] + r["total"], 2)
        elif tt == "expense":
            totals[ft]["expense"] = round(totals[ft]["expense"] + r["total"], 2)
    return totals


# ─────────────────────────────────────────────────────────────────────────────
# levy_plans CRUD
# ─────────────────────────────────────────────────────────────────────────────

async def upsert_levy_plan(data: Dict[str, Any], building_id: str) -> Dict[str, Any]:
    """
    Insert or update a levy plan.
    Matches on (financial_year, fund_type, building_id).
    """
    key = {
        "financial_year": data["financial_year"],
        "fund_type": data["fund_type"],
        "building_id": building_id,
    }
    now = _now()
    doc = {**key, **data, "updated_at": now}
    doc.pop("plan_id", None)
    existing = await db.levy_plans.find_one(key, {"_id": 0})
    if not existing:
        doc.setdefault("id", _new_id())
        doc.setdefault("created_at", now)
        await db.levy_plans.insert_one({k: v for k, v in doc.items() if k != "_id"})
    else:
        await db.levy_plans.update_one(key, {"$set": doc})
    return await db.levy_plans.find_one(key, {"_id": 0})


async def get_levy_plans(
        year: str, building_id: str
) -> List[Dict[str, Any]]:
    """Generated function header.

    Function: get_levy_plans
    Path: backend/repositories/financial_repository.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await db.levy_plans.find(
        {"financial_year": year, "building_id": building_id}, {"_id": 0}
    ).to_list(5)


async def get_levy_plan(
        year: str, fund_type: str, building_id: str
) -> Optional[Dict[str, Any]]:
    """Generated function header.

    Function: get_levy_plan
    Path: backend/repositories/financial_repository.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await db.levy_plans.find_one(
        {"financial_year": year, "fund_type": fund_type, "building_id": building_id},
        {"_id": 0},
    )


# ─────────────────────────────────────────────────────────────────────────────
# invoice_documents CRUD
# ─────────────────────────────────────────────────────────────────────────────

async def insert_invoice_record(data: Dict[str, Any]) -> Dict[str, Any]:
    """Insert a new invoice_documents record. Returns stored document."""
    now = _now()
    doc = {"id": _new_id(), "created_at": now, **data}
    await db.invoice_documents.insert_one({k: v for k, v in doc.items() if k != "_id"})
    return {k: v for k, v in doc.items() if k != "_id"}


async def get_invoice(invoice_id: str, building_id: str) -> Optional[Dict[str, Any]]:
    """Generated function header.

    Function: get_invoice
    Path: backend/repositories/financial_repository.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await db.invoice_documents.find_one(
        {"id": invoice_id, "building_id": building_id},
        {"_id": 0, "file_data": 0, "receipt_data": 0},
    )


async def get_invoice_receipt_data(invoice_id: str, building_id: str) -> Optional[Dict[str, Any]]:
    """Return only the receipt_data and vendor info needed by the receipt download endpoint."""
    return await db.invoice_documents.find_one(
        {"id": invoice_id, "building_id": building_id},
        {"_id": 0, "receipt_data": 1, "confirmed_data": 1},
    )


async def list_invoices(
        building_id: str,
        status: Optional[str] = None,
        document_type: Optional[str] = None,
        limit: int = 100,
) -> List[Dict[str, Any]]:
    """Generated function header.

    Function: list_invoices
    Path: backend/repositories/financial_repository.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query: Dict[str, Any] = {"building_id": building_id}
    if status:
        query["status"] = status
    if document_type:
        query["document_type"] = document_type
    return await db.invoice_documents.find(query, {"_id": 0, "file_data": 0, "receipt_data": 0}).sort(
        "created_at", -1
    ).to_list(limit)


async def update_invoice(invoice_id: str, building_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Generated function header.

    Function: update_invoice
    Path: backend/repositories/financial_repository.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    updates["updated_at"] = _now()
    await db.invoice_documents.update_one(
        {"id": invoice_id, "building_id": building_id},
        {"$set": updates},
    )
    return await db.invoice_documents.find_one(
        {"id": invoice_id, "building_id": building_id},
        {"_id": 0, "file_data": 0, "receipt_data": 0},
    )


def _extract_affected_keys(events: list, building_id: str) -> list:
    """Extract distinct (unit_id, year) pairs from event payloads for scoped rebuild.

    Events may carry keys at top level or inside a 'data'/'payload' sub-dict.
    Returns an empty list when events carry no usable keys — callers fall back
    to a full rebuild in that case.
    """
    seen: set = set()
    keys = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        payload = event.get("data") or event.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        unit_id = payload.get("unit_id") or event.get("unit_id")
        year = payload.get("year") or event.get("year")
        # Skip events for a different building
        ev_bid = payload.get("building_id") or event.get("building_id")
        if ev_bid and ev_bid != building_id:
            continue
        if unit_id and year:
            k = (str(unit_id), str(year))
            if k not in seen:
                seen.add(k)
                keys.append({"unit_id": str(unit_id), "year": str(year)})
    return keys


async def rebuild_projection(
        raw_db,
        projection_name: str,
        building_id: str,
        events: list,
) -> int:
    """Rebuild a named projection from source data, optionally scoped by events.

    Supported projections:
      - unit_levy_ledger: re-aggregates levy totals from levy_payments
      - lot_balances_projection / levy_payments_projection: aliases for unit_levy_ledger

    When `events` contains identifiable (unit_id, year) keys, only the affected
    rows are recomputed. When events are empty or carry no usable keys, the full
    projection for the building is rebuilt.

    Returns the count of records rebuilt/updated.
    """
    ALIASES = {"lot_balances_projection": "unit_levy_ledger", "levy_payments_projection": "unit_levy_ledger"}
    target = ALIASES.get(projection_name, projection_name)

    if target == "unit_levy_ledger":
        affected_keys = _extract_affected_keys(events, building_id) if events else []
        return await _rebuild_unit_levy_ledger(raw_db, building_id, affected_keys or None)

    raise ValueError(f"No rebuild handler for projection '{projection_name}'")


async def _rebuild_unit_levy_ledger(raw_db, building_id: str, affected_keys=None) -> int:
    """Re-aggregate unit_levy_ledger totals from levy_payments for a building.

    When `affected_keys` is provided (list of {unit_id, year} dicts), only those
    rows are recomputed — useful for incremental --since replay. Otherwise the full
    building projection is rebuilt. Idempotent in both modes.
    """
    match: dict = {"building_id": building_id, "status": "confirmed"}
    if affected_keys:
        match["$or"] = [{"unit_id": k["unit_id"], "year": k["year"]} for k in affected_keys]

    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": {
                    "building_id": "$building_id",
                    "unit_id": "$unit_id",
                    "year": "$year",
                },
                "total_paid_cents": {"$sum": "$amount_cents"},
                "payment_count": {"$sum": 1},
            }
        },
    ]
    rows = await raw_db.levy_payments.aggregate(pipeline).to_list(50000)
    if not rows:
        return 0

    from datetime import datetime, timezone
    from pymongo import UpdateOne
    now = datetime.now(timezone.utc)

    ops = [
        UpdateOne(
            {
                "building_id": row["_id"]["building_id"],
                "unit_id": row["_id"]["unit_id"],
                "year": row["_id"]["year"],
            },
            {
                "$set": {
                    "total_paid_cents": row["total_paid_cents"],
                    "payment_count": row["payment_count"],
                    "rebuilt_at": now,
                }
            },
            upsert=True,
        )
        for row in rows
    ]

    await raw_db.unit_levy_ledger.bulk_write(ops, ordered=False)
    return len(ops)


__all__ = [
    "ensure_indexes",
    "rebuild_projection",
    # financial_years
    "upsert_financial_year",
    "get_financial_year",
    "list_financial_years",
    # financial_categories
    "upsert_financial_category",
    "get_financial_categories",
    # financial_transactions
    "insert_transaction",
    "list_transactions",
    "aggregate_actual_by_category",
    "aggregate_fund_totals",
    # invoice_documents
    "insert_invoice_record",
    "get_invoice",
    "get_invoice_receipt_data",
    "list_invoices",
    "update_invoice",
    # levy_plans
    "upsert_levy_plan",
    "get_levy_plans",
    "get_levy_plan",
]
