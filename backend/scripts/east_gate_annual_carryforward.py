#!/usr/bin/env python3
# @featuretrace:financial-onboarding — Posts East Gate annual carry-forward journals.

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
    Path: backend/scripts/east_gate_annual_carryforward.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")


def _cents(value) -> int:
    """Generated function header.

    Function: _cents
    Path: backend/scripts/east_gate_annual_carryforward.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None or value == "":
        return 0
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _hash(entry_id: str, narration: str, prev_hash: str | None) -> str:
    """Generated function header.

    Function: _hash
    Path: backend/scripts/east_gate_annual_carryforward.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return hashlib.sha256(json.dumps({"id": entry_id, "narration": narration, "prev": prev_hash or ""}, sort_keys=True).encode()).hexdigest()


async def _scheme(conn, building_id: str) -> dict:
    """Generated function header.

    Function: _scheme
    Path: backend/scripts/east_gate_annual_carryforward.py

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
    Path: backend/scripts/east_gate_annual_carryforward.py

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
    Path: backend/scripts/east_gate_annual_carryforward.py

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
    Path: backend/scripts/east_gate_annual_carryforward.py

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
    Path: backend/scripts/east_gate_annual_carryforward.py

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

YEARS = ["2021", "2022", "2023", "2024", "2025"]


async def _actor_user(conn, scheme: dict) -> str:
    """Generated function header.

    Function: _actor_user
    Path: backend/scripts/east_gate_annual_carryforward.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    row = await conn.fetchrow("""
        SELECT user_id::text FROM core.users
        WHERE tenant_id=$1::uuid AND is_active=TRUE AND is_approved=TRUE
        ORDER BY CASE WHEN role::text='super_admin' THEN 0 WHEN role::text='strata_manager' THEN 1 ELSE 2 END, created_at
        LIMIT 1
    """, scheme["tenant_id"])
    if not row:
        raise RuntimeError("No active PostgreSQL user available for evidence attribution")
    return row["user_id"]


async def _evidence_doc(conn, scheme: dict, *, building_id: str, year: str, fund_code: str, amount_cents: int) -> str:
    """Generated function header.

    Function: _evidence_doc
    Path: backend/scripts/east_gate_annual_carryforward.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    actor = await _actor_user(conn, scheme)
    sha = hashlib.sha256(json.dumps({"building_id": building_id, "year": year, "fund": fund_code, "amount_cents": amount_cents, "source": "annual_levies"}, sort_keys=True).encode()).hexdigest()
    existing = await conn.fetchval("""
        SELECT document_id::text FROM finance.evidence_documents
        WHERE scheme_id=$1::uuid AND building_id=$2 AND sha256_hash=$3 LIMIT 1
    """, scheme["scheme_id"], building_id, sha)
    if existing:
        return existing
    doc_id = str(uuid4())
    await conn.execute("""
        INSERT INTO finance.evidence_documents
            (document_id, tenant_id, scheme_id, building_id, document_type, file_url, sha256_hash,
             uploaded_by, approved_by, approved_at, declared_total_cents, declared_totals_by_fund,
             source_system, notes, metadata, is_test_data)
        VALUES ($1::uuid,$2::uuid,$3::uuid,$4,'reconciliation',$5,$6,$7::uuid,$7::uuid,NOW(),$8,$9::jsonb,
                'annual_levies','Annual carry-forward evidence from Mongo annual_levies source',$10::jsonb,FALSE)
    """, doc_id, scheme["tenant_id"], scheme["scheme_id"], building_id,
        f"mongo://annual_levies/{building_id}/{year}/{fund_code}", sha, actor, amount_cents,
        json.dumps({fund_code: amount_cents}), json.dumps({"source_collection": "annual_levies", "year": year, "fund": fund_code}))
    return doc_id

async def run(building_id: str, apply: bool) -> dict:
    """Generated function header.

    Function: run
    Path: backend/scripts/east_gate_annual_carryforward.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    conn = await asyncpg.connect(_pg_url())
    try:
        scheme = await _scheme(conn, building_id)
        accounts = await _accounts(conn, scheme)
        funds = await _funds(conn, scheme)
        actions = []
        for year in YEARS:
            levy = await db._db.annual_levies.find_one({"building_id": building_id, "year": year})
            if not levy:
                actions.append({"year": year, "status": "missing_annual_levy"})
                continue
            period_id = await _ensure_period(conn, scheme, year, date(int(year), 1, 1), date(int(year), 12, 31))
            for fund_code, mongo_key in (("ADMIN", "admin_fund"), ("SINK", "sinking_fund")):
                amount = _cents((levy.get(mongo_key) or {}).get("closing_balance"))
                if amount == 0:
                    actions.append({"year": year, "fund": fund_code, "amount_cents": 0, "status": "skipped_zero"})
                    continue
                idem = f"east-gate-carryforward:{scheme['scheme_id']}:{fund_code}:{year}"
                item = {"year": year, "fund": fund_code, "amount_cents": amount, "idempotency_key": idem}
                if apply:
                    debit, credit = (accounts["3100"], accounts["1000"]) if amount > 0 else (accounts["1000"], accounts["3100"])
                    evidence_id = await _evidence_doc(conn, scheme, building_id=building_id, year=year, fund_code=fund_code, amount_cents=amount)
                    jid, inserted = await _insert_journal(
                        conn, scheme, fund_id=funds[fund_code], period_id=period_id, effective_on=date(int(year), 12, 31),
                        source_type="annual_carry_forward", source_reference=f"annual_levies:{year}:{fund_code}",
                        narration=f"Annual carry-forward {fund_code} fund - FY{year} closing", idempotency_key=idem,
                        debit_account=debit, credit_account=credit, amount_cents=abs(amount),
                        metadata={"building_id": building_id, "source": "annual_levies", "year": year, "fund": fund_code, "is_test_data": False},
                        evidence_document_id=evidence_id,
                    )
                    item.update({"journal_entry_id": jid, "inserted": inserted, "evidence_document_id": evidence_id})
                actions.append(item)
        return {"dry_run": not apply, "actions": actions}
    finally:
        await conn.close()

def main() -> int:
    """Generated function header.

    Function: main
    Path: backend/scripts/east_gate_annual_carryforward.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.building_id, args.apply)), default=str, indent=2, sort_keys=True))
    return 0
if __name__ == "__main__": raise SystemExit(main())
