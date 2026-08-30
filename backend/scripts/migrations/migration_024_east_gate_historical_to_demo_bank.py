"""
Load East Gate historical Mongo finance data into Demo Bank staging.

# @featuretrace:demo_bank — East Gate Mongo history → demo_bank_transactions.
# Layer: migration
# Data flow: existing Mongo finance collections → demo_bank_accounts / demo_bank_transactions.

This script does NOT write to PostgreSQL and does NOT write to levy_payments or
unit_levy_ledger. It only stages bank-like movements into Demo Bank so the real
bank-feed sync can later promote them into finance.bank_transactions.

Strata-year boundaries for East Gate use calendar years, with 2021 including
December 2020 as the 13-month opening year.

Run:
  python3 backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py \
    --building-id 13195 --from-date 2020-12-01 --to-date 2026-06-30 --dry-run

IMPORTANT: Run migration_023_demo_bank_indexes.py first to ensure unique indexes exist.
This script is idempotent: running twice produces the same state.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import logging
import os
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

PROVIDER = "demo_bank_feed"
SOURCE_COLLECTIONS = (
    "levy_payments",
    "expense_transactions",
    "income_transactions",
    "special_levy_payments",
    "water_bill_payments",
    "trust_transactions_v2",
    "bank_transactions",
)


def _account_refs(building_id: str) -> tuple[str, str]:
    """Generated function header.

    Function: _account_refs
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return f"ADMIN-{building_id}", f"SINKING-{building_id}"


def _db_name(uri: str, fallback: str) -> str:
    """Generated function header.

    Function: _db_name
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parsed = urlparse(uri)
    if parsed.path and parsed.path != "/":
        return parsed.path.lstrip("/").split("?")[0]
    return fallback


def _parse_cli_date(parser: argparse.ArgumentParser, value: str, arg_name: str) -> date:
    """Generated function header.

    Function: _parse_cli_date
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        parser.error(f"{arg_name} must use YYYY-MM-DD format: {exc}")


async def _close_client(client: AsyncIOMotorClient) -> None:
    """Generated function header.

    Function: _close_client
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = client.close()
    if inspect.isawaitable(result):
        await result


def _parse_date(value: Any) -> date | None:
    """Generated function header.

    Function: _parse_date
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _to_cents(value: Any) -> int | None:
    """Generated function header.

    Function: _to_cents
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value * 100))
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text or text == "-":
        return None
    try:
        if "." in text:
            return int(round(float(text) * 100))
        return int(text)
    except ValueError:
        return None


def _strata_year(value: date) -> str:
    """Generated function header.

    Function: _strata_year
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value < date(2022, 1, 1):
        return "2021"
    return str(value.year)


def _account_ref_for_doc(doc: dict[str, Any], building_id: str) -> str:
    """Generated function header.

    Function: _account_ref_for_doc
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    admin_ref, sinking_ref = _account_refs(building_id)
    fund = str(
        doc.get("payment_type")
        or doc.get("fund_type")
        or doc.get("fund")
        or doc.get("account_type")
        or doc.get("levy_fund")
        or doc.get("category")
        or ""
    ).lower()
    if any(token in fund for token in ("sinking", "capital", "cw", "sinking_fund")):
        return sinking_ref
    return admin_ref


def _direction_for_source(source: str, doc: dict[str, Any], amount_cents: int) -> tuple[str, int]:
    """Generated function header.

    Function: _direction_for_source
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    source_l = source.lower()
    type_l = str(doc.get("type") or doc.get("transaction_type") or doc.get("direction") or "").lower()
    if "expense" in source_l or type_l in {"debit", "expense", "payment_out"}:
        return "debit", abs(amount_cents)
    if amount_cents < 0:
        return "debit", abs(amount_cents)
    return "credit", abs(amount_cents)


def _external_txn_id(
        account_ref: str,
        source_collection: str,
        source_id: Any,
        payment_date: date,
        amount_cents: int,
        direction: str,
        description: str,
) -> str:
    """Generated function header.

    Function: _external_txn_id
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw = "|".join([
        account_ref,
        source_collection,
        str(source_id),
        payment_date.isoformat(),
        str(amount_cents),
        direction,
        description.strip().upper(),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def _idempotency_key(building_id: str, account_ref: str, external_txn_id: str) -> str:
    """Generated function header.

    Function: _idempotency_key
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw = f"{building_id}|{PROVIDER}|{account_ref}|{external_txn_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def _existing_source_collections(db) -> set[str]:
    """Generated function header.

    Function: _existing_source_collections
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return set(await db.list_collection_names())


async def source_docs(db, building_id: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Generated function header.

    Function: source_docs
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    existing = await _existing_source_collections(db)
    for source in SOURCE_COLLECTIONS:
        if source not in existing:
            continue
        cursor = db[source].find({"building_id": building_id, "is_test_data": {"$ne": True}})
        async for doc in cursor:
            yield source, doc


async def _ensure_accounts(db, building_id: str, dry_run: bool) -> None:
    """Generated function header.

    Function: _ensure_accounts
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if dry_run:
        return
    now = datetime.now(timezone.utc)
    admin_ref, sinking_ref = _account_refs(building_id)
    accounts = [
        (admin_ref, "admin", "East Gate Admin Fund"),
        (sinking_ref, "sinking", "East Gate Sinking Fund"),
    ]
    for account_ref, fund_type, account_name in accounts:
        await db.demo_bank_accounts.update_one(
            {"building_id": building_id, "account_ref": account_ref},
            {"$setOnInsert": {
                "building_id": building_id,
                "provider": PROVIDER,
                "account_ref": account_ref,
                "fund_type": fund_type,
                "account_type": f"trust_{fund_type}",
                "account_name": account_name,
                "institution_name": "Demo Bank (historical import)",
                "bsb": "000-000",
                "account_number_masked": "****1319" if fund_type == "admin" else "****1320",
                "currency": "AUD",
                "opening_balance_cents": 0,
                "current_balance_cents": 0,
                "status": "active",
                "is_test_data": False,
                "created_at": now,
                "updated_at": now,
            }},
            upsert=True,
        )


def _normalise_doc(
        *,
        building_id: str,
        source: str,
        doc: dict[str, Any],
        from_date: date,
        to_date: date,
        is_test_data: bool,
) -> dict[str, Any] | None:
    """Generated function header.

    Function: _normalise_doc
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    payment_date = None
    for key in ("payment_date", "paid_at", "transaction_date", "date", "posted_date", "created_at"):
        payment_date = _parse_date(doc.get(key))
        if payment_date:
            break
    if not payment_date or not (from_date <= payment_date <= to_date):
        return None

    amount_cents = None
    for key in ("amount_cents", "paid_cents", "amount", "amount_dollars", "actual_amount", "payment_amount", "total_cents", "total", "debit_amount"):
        amount_cents = _to_cents(doc.get(key))
        if amount_cents is not None:
            break
    if amount_cents is None or amount_cents == 0:
        return None

    direction, stored_amount_cents = _direction_for_source(source, doc, amount_cents)
    unit_number = str(doc.get("unit_number") or doc.get("lot_number") or doc.get("lot") or "")
    description = str(
        doc.get("notes")
        or doc.get("description")
        or doc.get("narration")
        or doc.get("reference")
        or doc.get("payment_reference")
        or doc.get("category")
        or f"{source} {unit_number}".strip()
    )
    if unit_number and "LOT" not in description.upper() and "UNIT" not in description.upper():
        description = f"{description} - LOT {unit_number}"

    account_ref = _account_ref_for_doc(doc, building_id)
    source_id = str(doc.get("_id", ""))
    external_id = _external_txn_id(
        account_ref,
        source,
        source_id,
        payment_date,
        stored_amount_cents,
        direction,
        description,
    )
    now = datetime.now(timezone.utc)

    return {
        "building_id": building_id,
        "provider": PROVIDER,
        "account_ref": account_ref,
        "external_transaction_id": external_id,
        "idempotency_key": _idempotency_key(building_id, account_ref, external_id),
        "posted_date": datetime.combine(payment_date, time.min, tzinfo=timezone.utc),
        "effective_date": datetime.combine(payment_date, time.min, tzinfo=timezone.utc),
        "amount_cents": stored_amount_cents,
        "direction": direction,
        "description": description,
        "reference": str(doc.get("payment_reference") or doc.get("receipt_number") or doc.get("reference") or source_id),
        "payer_name": doc.get("owner_name") or doc.get("payer_name"),
        "payment_channel": doc.get("payment_channel") or doc.get("channel") or "EFT",
        "raw_payload": {"source_collection": source, "source_id": source_id},
        "source_type": "historical_mongo",
        "source_id": source_id,
        "source_batch_id": None,
        "unit_number": unit_number,
        "strata_year": _strata_year(payment_date),
        "running_balance_cents": doc.get("running_balance_cents") or doc.get("balance_after_cents"),
        "status": "posted",
        "sync_status": "pending",
        "last_sync_attempt_at": None,
        "finance_bank_transaction_ref": None,
        "sync_error": None,
        "evidence_document_id": None,
        "original_file_storage_ref": None,
        "source_sha256": None,
        "is_test_data": is_test_data,
        "created_at": now,
        "updated_at": now,
    }


async def _upsert_transaction(db, tx: dict[str, Any], dry_run: bool) -> tuple[bool, bool]:
    """Generated function header.

    Function: _upsert_transaction
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if dry_run:
        return True, False
    result = await db.demo_bank_transactions.update_one(
        {"building_id": tx["building_id"], "idempotency_key": tx["idempotency_key"]},
        {"$setOnInsert": tx},
        upsert=True,
    )
    inserted = result.upserted_id is not None
    return inserted, not inserted


async def _recompute_balances(db, building_id: str, dry_run: bool) -> None:
    """Generated function header.

    Function: _recompute_balances
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if dry_run:
        return
    for account_ref in _account_refs(building_id):
        pipeline = [
            {"$match": {"building_id": building_id, "account_ref": account_ref, "status": {"$in": ["posted", "pending"]}}},
            {"$group": {"_id": "$direction", "total": {"$sum": "$amount_cents"}}},
        ]
        totals = {row["_id"]: row["total"] async for row in db.demo_bank_transactions.aggregate(pipeline)}
        current = int(totals.get("credit", 0)) - int(totals.get("debit", 0))
        await db.demo_bank_accounts.update_one(
            {"building_id": building_id, "account_ref": account_ref},
            {"$set": {"current_balance_cents": current, "updated_at": datetime.now(timezone.utc)}},
        )


async def run(
        db,
        *,
        building_id: str,
        from_date: date,
        to_date: date,
        is_test_data: bool,
        dry_run: bool,
) -> dict[str, Any]:
    """Generated function header.

    Function: run
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    logger.info(
        "Starting historical Demo Bank import: building=%s from=%s to=%s dry_run=%s is_test_data=%s",
        building_id, from_date, to_date, dry_run, is_test_data,
    )

    await _ensure_accounts(db, building_id, dry_run)

    seen = imported = skipped = duplicates = 0
    by_source: dict[str, int] = {}
    by_year: dict[str, int] = {}

    async for source, doc in source_docs(db, building_id):
        seen += 1
        tx = _normalise_doc(
            building_id=building_id,
            source=source,
            doc=doc,
            from_date=from_date,
            to_date=to_date,
            is_test_data=is_test_data,
        )
        if tx is None:
            skipped += 1
            continue

        by_source[source] = by_source.get(source, 0) + 1
        by_year[tx["strata_year"]] = by_year.get(tx["strata_year"], 0) + 1
        was_inserted, was_duplicate = await _upsert_transaction(db, tx, dry_run)
        if was_inserted:
            imported += 1
        elif was_duplicate:
            duplicates += 1

    await _recompute_balances(db, building_id, dry_run)

    result = {
        "building_id": building_id,
        "dry_run": dry_run,
        "source_rows_seen": seen,
        "normalised_importable_rows": imported,
        "duplicates_noop_rows": duplicates,
        "skipped_rows": skipped,
        "by_source": dict(sorted(by_source.items())),
        "by_strata_year": dict(sorted(by_year.items())),
    }
    logger.info("Historical Demo Bank import complete: %s", result)
    return result


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Import East Gate Mongo finance history into Demo Bank staging")
    parser.add_argument("--building-id", default=os.environ.get("DEFAULT_BUILDING_ID", "13195"))
    parser.add_argument("--from-date", default="2020-12-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--to-date", default=date.today().isoformat(), help="End date YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Count only, write nothing")
    parser.add_argument("--is-test-data", action="store_true", help="Tag imported records as test data")
    args = parser.parse_args()

    from_date = _parse_cli_date(parser, args.from_date, "--from-date")
    to_date = _parse_cli_date(parser, args.to_date, "--to-date")

    uri = os.environ.get("MONGODB_URI") or os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27018/strata_management"
    db_name = os.environ.get("MONGODB_DB") or os.environ.get("DB_NAME") or _db_name(uri, "strata_management")
    client = AsyncIOMotorClient(uri)
    db = client[db_name]

    try:
        print(f"Mongo database: {db_name}")
        result = await run(
            db,
            building_id=args.building_id,
            from_date=from_date,
            to_date=to_date,
            is_test_data=args.is_test_data,
            dry_run=args.dry_run,
        )
    finally:
        await _close_client(client)

    print("\nHistorical Demo Bank import summary")
    print("-----------------------------------")
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    asyncio.run(main())
