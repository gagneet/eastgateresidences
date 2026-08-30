#!/usr/bin/env python3
"""Read-only GAP-FIN-031 probe for Demo Bank, matching queue, and PG receipts.

This script does not write to MongoDB or PostgreSQL. It prints aggregate counts
only, scoped to one building, so operators can prove which levy-year records
exist at each handoff before replaying any posting path.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import db  # noqa: E402
from db_postgres.repos import config_repo  # noqa: E402
from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from request_context import set_ctx_building_id  # noqa: E402


def _json(label: str, value) -> None:
    print(f"{label} {json.dumps(value, default=str, sort_keys=True)}")


async def _mongo_settings(building_id: str) -> None:
    settings = await db.settings.find_one(
        {
            "building_id": building_id,
            "$or": [
                {"type": {"$exists": False}},
                {"type": None},
                {"type": "general"},
            ],
        },
        {
            "_id": 0,
            "financial_year_start_month": 1,
            "levy_collection_frequency": 1,
            "levy_due_months": 1,
            "levy_due_day_type": 1,
            "levy_due_day": 1,
            "levy_due_custom_dates": 1,
        },
        sort=[("updated_at", -1), ("created_at", -1), ("_id", -1)],
    )
    _json("MONGO_SETTINGS", settings or {})


async def _mongo_demo_bank(building_id: str, years: list[str]) -> None:
    cursor = await db._db.demo_bank_transactions.aggregate(
        [
            {
                "$match": {
                    "building_id": building_id,
                    "is_test_data": {"$ne": True},
                    "is_archived": {"$ne": True},
                }
            },
            {"$addFields": {"year_key": {"$substr": [{"$toString": "$posted_date"}, 0, 4]}}},
            {"$match": {"year_key": {"$in": years}}},
            {
                "$group": {
                    "_id": {
                        "year": "$year_key",
                        "source_type": "$source_type",
                        "sync_status": "$sync_status",
                        "confidence": "$confidence",
                        "requires_review": "$requires_review",
                    },
                    "count": {"$sum": 1},
                    "amount_cents": {"$sum": "$amount_cents"},
                    "synced_refs": {
                        "$sum": {"$cond": [{"$ifNull": ["$finance_bank_transaction_ref", False]}, 1, 0]}
                    },
                }
            },
            {"$sort": {"_id.year": 1, "_id.source_type": 1, "_id.sync_status": 1}},
        ]
    )
    rows = await cursor.to_list(None)
    _json("MONGO_DEMO_BANK_BY_YEAR", rows)


async def _mongo_match_queue(building_id: str, years: list[str]) -> None:
    cursor = await db._db.match_review_queue.aggregate(
        [
            {
                "$match": {
                    "building_id": building_id,
                    "is_test_data": {"$ne": True},
                }
            },
            {"$addFields": {"year_key": {"$substr": [{"$toString": "$tx.occurred_at"}, 0, 4]}}},
            {"$match": {"year_key": {"$in": years}}},
            {
                "$group": {
                    "_id": {
                        "year": "$year_key",
                        "status": "$status",
                        "match_type": "$match_type",
                        "source_type": "$tx.metadata.source_type",
                    },
                    "count": {"$sum": 1},
                    "amount_cents": {"$sum": "$tx.amount_cents"},
                    "with_receipt": {"$sum": {"$cond": [{"$ifNull": ["$receipt_id", False]}, 1, 0]}},
                }
            },
            {"$sort": {"_id.year": 1, "_id.status": 1, "_id.match_type": 1}},
        ]
    )
    rows = await cursor.to_list(None)
    _json("MONGO_MATCH_QUEUE_BY_YEAR", rows)


async def _postgres(building_id: str, years: list[str]) -> None:
    scheme = await config_repo.resolve_scheme_context(building_id)
    if not scheme:
        _json("PG_SCHEME", None)
        return

    _json("PG_SCHEME", {k: str(v) for k, v in scheme.items()})
    first_year = min(int(y) for y in years)
    last_year = max(int(y) for y in years)
    params = {
        "scheme_id": str(scheme["scheme_id"]),
        "from_date": date(first_year, 1, 1),
        "to_date": date(last_year + 1, 1, 1),
        "years": years,
    }

    queries = {
        "PG_BANK_TRANSACTIONS_BY_YEAR": """
            SELECT EXTRACT(YEAR FROM transaction_date)::int AS year,
                   source_type, provenance_class, confidence, requires_review,
                   count(*) AS count,
                   coalesce(sum(amount_cents),0) AS amount_cents,
                   count(*) FILTER (WHERE amount_cents > 0) AS positive_count,
                   coalesce(sum(amount_cents) FILTER (WHERE amount_cents > 0),0) AS positive_amount_cents,
                   count(*) FILTER (WHERE reconstruction_batch_id IS NOT NULL) AS with_reconstruction_batch
              FROM finance.bank_transactions
             WHERE scheme_id = CAST(:scheme_id AS uuid)
               AND transaction_date >= CAST(:from_date AS date)
               AND transaction_date < CAST(:to_date AS date)
             GROUP BY 1,2,3,4,5
             ORDER BY 1,2,3,4,5
        """,
        "PG_RECEIPTS_BY_YEAR": """
            SELECT EXTRACT(YEAR FROM received_on)::int AS year,
                   channel,
                   count(*) AS count,
                   coalesce(sum(amount_cents),0) AS amount_cents,
                   count(*) FILTER (WHERE bank_transaction_id IS NOT NULL) AS with_bank_transaction_id,
                   count(*) FILTER (WHERE external_reference IS NOT NULL) AS with_external_reference,
                   count(*) FILTER (WHERE reconstruction_batch_id IS NOT NULL) AS with_reconstruction_batch
              FROM finance.receipts
             WHERE scheme_id = CAST(:scheme_id AS uuid)
               AND received_on >= CAST(:from_date AS date)
               AND received_on < CAST(:to_date AS date)
             GROUP BY 1,2
             ORDER BY 1,2
        """,
        "PG_RECEIPT_BANK_JOIN_BY_YEAR": """
            SELECT EXTRACT(YEAR FROM bt.transaction_date)::int AS year,
                   count(*) FILTER (WHERE bt.amount_cents > 0) AS positive_bank_tx,
                   coalesce(sum(bt.amount_cents) FILTER (WHERE bt.amount_cents > 0),0) AS positive_bank_cents,
                   count(*) FILTER (WHERE bt.amount_cents > 0 AND r.receipt_id IS NOT NULL) AS matched_receipt_count,
                   coalesce(sum(bt.amount_cents) FILTER (WHERE bt.amount_cents > 0 AND r.receipt_id IS NOT NULL),0) AS matched_receipt_cents,
                   count(*) FILTER (WHERE bt.amount_cents > 0 AND r.receipt_id IS NULL) AS missing_receipt_count,
                   coalesce(sum(bt.amount_cents) FILTER (WHERE bt.amount_cents > 0 AND r.receipt_id IS NULL),0) AS missing_receipt_cents
              FROM finance.bank_transactions bt
              LEFT JOIN finance.receipts r
                ON r.scheme_id = bt.scheme_id
               AND (
                    r.bank_transaction_id = bt.bank_transaction_id
                    OR r.external_reference = bt.bank_transaction_id::text
               )
             WHERE bt.scheme_id = CAST(:scheme_id AS uuid)
               AND bt.transaction_date >= CAST(:from_date AS date)
               AND bt.transaction_date < CAST(:to_date AS date)
             GROUP BY 1
             ORDER BY 1
        """,
        "PG_LEVY_ITEMS_BY_YEAR": """
            SELECT lr.financial_year AS levy_year,
                   count(*) AS item_count,
                   coalesce(sum(li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents),0) AS charged_cents,
                   coalesce(sum(li.paid_cents),0) AS paid_cents,
                   count(DISTINCT li.lot_id) AS lots
              FROM finance.levy_items li
              JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
             WHERE li.scheme_id = CAST(:scheme_id AS uuid)
               AND lr.financial_year = ANY(:years)
             GROUP BY 1
             ORDER BY 1
        """,
        "PG_RECONSTRUCTION_BATCHES": """
            SELECT status,
                   count(*) AS count,
                   coalesce(sum(expected_amount_cents),0) AS expected_cents,
                   coalesce(sum(actual_amount_cents),0) AS actual_cents
              FROM finance.reconstruction_execution_batches
             WHERE scheme_id = CAST(:scheme_id AS uuid)
             GROUP BY status
             ORDER BY status
        """,
    }

    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))
        for label, sql in queries.items():
            rows = (await session.execute(text(sql), params)).mappings().all()
            _json(label, [dict(row) for row in rows])


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--from-year", type=int, default=2021)
    parser.add_argument("--to-year", type=int, default=2026)
    args = parser.parse_args()

    years = [str(year) for year in range(args.from_year, args.to_year + 1)]
    set_ctx_building_id(args.building_id)
    await _mongo_settings(args.building_id)
    await _mongo_demo_bank(args.building_id, years)
    await _mongo_match_queue(args.building_id, years)
    await _postgres(args.building_id, years)


if __name__ == "__main__":
    asyncio.run(main())
