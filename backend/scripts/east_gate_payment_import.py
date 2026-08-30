#!/usr/bin/env python3
# @featuretrace:financial-onboarding — Imports East Gate portal levy payments into PostgreSQL journal receipts.

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sys
from uuid import uuid4

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from database import db

BYPASS = "00000000-0000-0000-0000-000000000000"


def _pg_url() -> str:
    """Generated function header.

    Function: _pg_url
    Path: backend/scripts/east_gate_payment_import.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")


def _cents(value) -> int:
    """Generated function header.

    Function: _cents
    Path: backend/scripts/east_gate_payment_import.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None or value == "":
        return 0
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _hash(entry_id: str, narration: str, prev_hash: str | None) -> str:
    """Generated function header.

    Function: _hash
    Path: backend/scripts/east_gate_payment_import.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return hashlib.sha256(json.dumps({"id": entry_id, "narration": narration, "prev": prev_hash or ""}, sort_keys=True).encode()).hexdigest()


async def _scheme(conn, building_id: str) -> dict:
    """Generated function header.

    Function: _scheme
    Path: backend/scripts/east_gate_payment_import.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)
    row = await conn.fetchrow("""
        SELECT tenant_id::text, scheme_id::text
        FROM core.schemes
        WHERE scheme_number = ANY($1::text[])
        LIMIT 1
    """, [building_id, f"UP{building_id}" if not str(building_id).upper().startswith("UP") else str(building_id)[2:]])
    if not row:
        raise RuntimeError(f"No scheme for building_id={building_id}")
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", row["tenant_id"])
    return dict(row)


async def _ensure_period(conn, scheme: dict, label: str, starts: date, ends: date) -> str:
    """Generated function header.

    Function: _ensure_period
    Path: backend/scripts/east_gate_payment_import.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    row = await conn.fetchrow("""
        INSERT INTO finance.accounting_periods
            (period_id, tenant_id, scheme_id, period_label, starts_on, ends_on, status, created_at)
        VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, 'open', NOW())
        ON CONFLICT DO NOTHING
        RETURNING period_id::text
    """, str(uuid4()), scheme["tenant_id"], scheme["scheme_id"], label, starts, ends)
    if row:
        return row["period_id"]
    row = await conn.fetchrow("""
        SELECT period_id::text FROM finance.accounting_periods
        WHERE scheme_id=$1::uuid AND period_label=$2 LIMIT 1
    """, scheme["scheme_id"], label)
    if not row:
        raise RuntimeError(f"No period {label}")
    return row["period_id"]


async def _accounts(conn, scheme: dict) -> dict[str, str]:
    """Generated function header.

    Function: _accounts
    Path: backend/scripts/east_gate_payment_import.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    rows = await conn.fetch("""
        SELECT account_code, gl_account_id::text FROM finance.gl_accounts
        WHERE scheme_id=$1::uuid AND account_code = ANY($2::text[])
    """, scheme["scheme_id"], ["1000", "1100", "3100"])
    return {r["account_code"]: r["gl_account_id"] for r in rows}


async def _funds(conn, scheme: dict) -> dict[str, str]:
    """Generated function header.

    Function: _funds
    Path: backend/scripts/east_gate_payment_import.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    rows = await conn.fetch("""
        SELECT fund_code, fund_type::text, fund_id::text FROM finance.funds
        WHERE scheme_id=$1::uuid
    """, scheme["scheme_id"])
    out = {}
    for r in rows:
        raw = f"{r['fund_code']} {r['fund_type']}".lower()
        if "admin" in raw:
            out["ADMIN"] = r["fund_id"]
        if "sink" in raw or "capital" in raw:
            out["SINK"] = r["fund_id"]
    return out


async def _insert_journal(conn, scheme: dict, *, fund_id: str, period_id: str, effective_on: date,
                          source_type: str, source_reference: str, narration: str, idempotency_key: str,
                          debit_account: str, credit_account: str, amount_cents: int, metadata: dict,
                          evidence_document_id: str | None = None) -> tuple[str, bool]:
    """Generated function header.

    Function: _insert_journal
    Path: backend/scripts/east_gate_payment_import.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    existing = await conn.fetchval("""
        SELECT journal_entry_id::text FROM finance.journal_entries
        WHERE scheme_id=$1::uuid AND idempotency_key=$2 LIMIT 1
    """, scheme["scheme_id"], idempotency_key)
    if existing:
        return existing, False
    prev_hash = await conn.fetchval("""
        SELECT entry_hash FROM finance.journal_entries
        WHERE scheme_id=$1::uuid AND fund_id=$2::uuid
        ORDER BY entry_number DESC LIMIT 1
    """, scheme["scheme_id"], fund_id)
    jid = str(uuid4())
    eh = _hash(jid, narration, prev_hash)
    await conn.execute("""
        INSERT INTO finance.journal_entries
            (journal_entry_id, tenant_id, scheme_id, fund_id, period_id, source_type, source_reference,
             narration, status, effective_on, posted_at, idempotency_key, prev_entry_hash, entry_hash,
             evidence_document_id, is_test_data, metadata, created_at)
        VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$6,$7,$8,'posted',$9,NOW(),$10,$11,$12,$13::uuid,FALSE,$14::jsonb,NOW())
    """, jid, scheme["tenant_id"], scheme["scheme_id"], fund_id, period_id, source_type, source_reference,
        narration, effective_on, idempotency_key, prev_hash, eh, evidence_document_id, json.dumps(metadata, default=str))
    for acct, direction in ((debit_account, "debit"), (credit_account, "credit")):
        await conn.execute("""
            INSERT INTO finance.journal_lines
                (journal_line_id, tenant_id, scheme_id, journal_entry_id, gl_account_id, direction, amount_cents, gst_cents, narration, created_at)
            VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$6,$7,0,$8,NOW())
        """, str(uuid4()), scheme["tenant_id"], scheme["scheme_id"], jid, acct, direction, amount_cents, narration)
    return jid, True


def _payment_date(doc) -> date:
    """Generated function header.

    Function: _payment_date
    Path: backend/scripts/east_gate_payment_import.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw = doc.get("payment_date") or doc.get("confirmed_at") or doc.get("created_at")
    if isinstance(raw, datetime):
        return raw.date()
    return date.fromisoformat(str(raw)[:10])

async def run(building_id: str, apply: bool, batch_size: int = 50) -> dict:
    """Generated function header.

    Function: run
    Path: backend/scripts/east_gate_payment_import.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    conn = await asyncpg.connect(_pg_url())
    try:
        scheme = await _scheme(conn, building_id)
        accounts = await _accounts(conn, scheme)
        funds = await _funds(conn, scheme)
        payments = await db._db.levy_payments.find({"building_id": building_id}).sort("created_at", 1).to_list(None)
        batches = 0
        imported = 0
        skipped = 0
        samples = []
        for i in range(0, len(payments), batch_size):
            batch = payments[i:i + batch_size]
            batches += 1
            if not batch:
                continue
            for doc in batch:
                amount = int(doc.get("amount_cents") or _cents(doc.get("amount")))
                if amount <= 0:
                    skipped += 1
                    continue
                pdate = _payment_date(doc)
                lot = str(doc.get("lot_number") or doc.get("unit_number") or "unknown")
                ref = str(doc.get("payment_reference") or doc.get("receipt_number") or doc.get("id") or doc.get("_id"))
                idem = f"strata_web-payment:{scheme['scheme_id']}:{lot}:{pdate.isoformat()}:{amount}"
                period_id = await _ensure_period(conn, scheme, str(pdate.year), date(pdate.year, 1, 1), date(pdate.year, 12, 31))
                existing = await conn.fetchval(
                    """
                    SELECT je.journal_entry_id::text
                    FROM finance.journal_entries je
                    WHERE je.scheme_id=$1::uuid
                      AND (
                        je.idempotency_key=$2
                        OR (je.source_type IN ('receipt', 'payment_receipt') AND je.source_reference=$3)
                        OR (
                            je.source_type = 'receipt'
                            AND je.effective_on = $4
                            AND je.narration ILIKE '%' || $5 || '%'
                            AND EXISTS (
                                SELECT 1 FROM finance.journal_lines jl
                                WHERE jl.journal_entry_id = je.journal_entry_id
                                  AND jl.amount_cents = $6
                            )
                        )
                      )
                    LIMIT 1
                    """,
                    scheme["scheme_id"], idem, ref, pdate, lot, amount,
                )
                item = {"lot_number": lot, "payment_date": pdate.isoformat(), "amount_cents": amount, "idempotency_key": idem}
                if existing:
                    skipped += 1
                    item.update({"existing_journal_entry_id": existing, "status": "already_imported"})
                    if len(samples) < 5:
                        samples.append(item)
                    continue
                if apply:
                    jid, inserted = await _insert_journal(
                        conn, scheme, fund_id=funds.get("ADMIN") or next(iter(funds.values())), period_id=period_id, effective_on=pdate,
                        source_type="payment_receipt", source_reference=ref,
                        narration=f"Levy payment - Lot {lot} - {ref}", idempotency_key=idem,
                        debit_account=accounts["1000"], credit_account=accounts["1100"], amount_cents=amount,
                        metadata={"building_id": building_id, "source": "strata_web_portal_import", "mongo_id": str(doc.get("_id")), "is_test_data": False},
                    )
                    item.update({"journal_entry_id": jid, "inserted": inserted})
                    imported += 1 if inserted else 0
                    skipped += 0 if inserted else 1
                if len(samples) < 5:
                    samples.append(item)
        return {"dry_run": not apply, "total_payments": len(payments), "batch_size": batch_size, "batches_processed": batches, "imported": imported, "skipped": skipped, "samples": samples}
    finally:
        await conn.close()

def main() -> int:
    """Generated function header.

    Function: main
    Path: backend/scripts/east_gate_payment_import.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.building_id, args.apply, args.batch_size)), default=str, indent=2, sort_keys=True))
    return 0
if __name__ == "__main__": raise SystemExit(main())
