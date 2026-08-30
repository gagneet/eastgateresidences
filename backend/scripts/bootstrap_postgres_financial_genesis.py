#!/usr/bin/env python3
"""Bootstrap approved opening evidence, fund setup, and genesis journals."""

# @featuretrace:financial-onboarding — CLI bootstrap for approved opening evidence and clean-slate genesis.
# Layer: seed
# Data flow: operator CLI → financial_onboarding service → finance.evidence_documents / finance.financial_cutover_config / finance.financial_onboarding_audit / finance.journal_entries (building-scoped).
# Related: backend/services/onboarding/financial_onboarding.py
#          backend/scripts/postgres_cutover_p0_readiness.py
#          docs/migration/east-gate-postgres-financial-genesis.md

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from datetime import datetime
from typing import Any

import asyncpg

from services.onboarding.financial_onboarding import (
    EvidenceDocumentCreate,
    FundBalance,
    create_evidence_document,
    onboard_building_financials,
)


def _load_database_url() -> str:
    """Generated function header.

    Function: _load_database_url
    Path: backend/scripts/bootstrap_postgres_financial_genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    for env_path in (".env", "backend/.env"):
        if not os.path.exists(env_path):
            continue
        with open(env_path, "r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped.startswith("DATABASE_URL="):
                    return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _asyncpg_url(raw_url: str) -> str:
    """Generated function header.

    Function: _asyncpg_url
    Path: backend/scripts/bootstrap_postgres_financial_genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return raw_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _first_text(value: str | None) -> str | None:
    """Generated function header.

    Function: _first_text
    Path: backend/scripts/bootstrap_postgres_financial_genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    text = str(value).strip() if value is not None else ""
    return text or None


def _coerce_int(name: str, value: str | None) -> int | None:
    """Generated function header.

    Function: _coerce_int
    Path: backend/scripts/bootstrap_postgres_financial_genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer number of cents") from exc


def _normalize_building_identifier(value: str | None) -> str | None:
    """Generated function header.

    Function: _normalize_building_identifier
    Path: backend/scripts/bootstrap_postgres_financial_genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    upper = text.upper()
    if upper.startswith("UP"):
        return upper.removeprefix("UP") or None
    return text


async def _resolve_building_identifier(
    conn: asyncpg.Connection,
    *,
    building_id: str | None,
    scheme_id: str | None,
    plan_number: str | None,
    building_slug: str | None,
) -> str:
    """Generated function header.

    Function: _resolve_building_identifier
    Path: backend/scripts/bootstrap_postgres_financial_genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    candidates = [item for item in (building_id, plan_number) if item]
    if scheme_id:
        row = await conn.fetchrow(
            """
            SELECT scheme_number, COALESCE(b.asset_profile->>'building_slug', '') AS building_slug
            FROM core.schemes s
            LEFT JOIN core.buildings b ON b.scheme_id = s.scheme_id
            WHERE s.scheme_id = $1::uuid
            """,
            scheme_id,
        )
        if row:
            return _normalize_building_identifier(row["scheme_number"]) or _normalize_building_identifier(building_id) or _normalize_building_identifier(plan_number) or str(row["scheme_number"] or building_id or plan_number)
    if building_slug:
        row = await conn.fetchrow(
            """
            SELECT s.scheme_number
            FROM core.schemes s
            LEFT JOIN core.buildings b ON b.scheme_id = s.scheme_id
            WHERE lower(COALESCE(b.asset_profile->>'building_slug', '')) = lower($1)
            ORDER BY s.scheme_number
            LIMIT 1
            """,
            building_slug,
        )
        if row:
            return _normalize_building_identifier(row["scheme_number"]) or str(row["scheme_number"])
    if candidates:
        return str(candidates[0])
    raise ValueError("One of --building-id, --plan-number, --scheme-id, or --building-slug is required")


def _build_fund_balances(args: argparse.Namespace) -> list[FundBalance]:
    """Generated function header.

    Function: _build_fund_balances
    Path: backend/scripts/bootstrap_postgres_financial_genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    balances = [
        FundBalance("admin", int(args.admin_fund_opening_cents)),
        FundBalance("sinking", int(args.sinking_fund_opening_cents)),
    ]
    if args.special_fund_opening_cents is not None:
        balances.append(FundBalance("special", int(args.special_fund_opening_cents)))
    return balances


def _opening_total(balances: list[FundBalance]) -> int:
    """Generated function header.

    Function: _opening_total
    Path: backend/scripts/bootstrap_postgres_financial_genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return sum(item.amount_cents for item in balances)


def _build_evidence_payload(args: argparse.Namespace, building_id: str) -> EvidenceDocumentCreate:
    """Generated function header.

    Function: _build_evidence_payload
    Path: backend/scripts/bootstrap_postgres_financial_genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    declared_totals = {"admin": int(args.admin_fund_opening_cents), "sinking": int(args.sinking_fund_opening_cents)}
    if args.special_fund_opening_cents is not None:
        declared_totals["special"] = int(args.special_fund_opening_cents)
    uploaded_by = _first_text(args.uploaded_by) or _first_text(args.approved_by)
    approved_by = _first_text(args.approved_by) or _first_text(args.uploaded_by)
    if not uploaded_by:
        raise ValueError("--uploaded-by or --approved-by is required")
    if not approved_by:
        raise ValueError("--approved-by or --uploaded-by is required")
    return EvidenceDocumentCreate(
        building_id=building_id,
        document_type=args.document_type,
        file_url=args.evidence_file_url,
        sha256_hash=args.evidence_sha256,
        uploaded_by=uploaded_by,
        approved_by=approved_by,
        approved_at=datetime.fromisoformat(args.approved_at) if args.approved_at else None,
        declared_total_cents=_opening_total(_build_fund_balances(args)),
        declared_totals_by_fund=declared_totals,
        source_system=args.source_system,
        import_batch_id=args.import_batch_id,
        notes=args.notes,
        metadata={"source": "bootstrap_postgres_financial_genesis"},
        is_test_data=args.is_test_data,
    )


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    """Generated function header.

    Function: _run
    Path: backend/scripts/bootstrap_postgres_financial_genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw_url = _load_database_url()
    if not raw_url:
        raise RuntimeError("DATABASE_URL is not set")

    conn = await asyncpg.connect(_asyncpg_url(raw_url))
    try:
        building_id = await _resolve_building_identifier(
            conn,
            building_id=args.building_id,
            scheme_id=args.scheme_id,
            plan_number=args.plan_number,
            building_slug=args.building_slug,
        )
        balances = _build_fund_balances(args)
        evidence_payload = _build_evidence_payload(args, building_id)
        plan = {
            "building_id": building_id,
            "scheme_id": args.scheme_id,
            "plan_number": args.plan_number,
            "building_slug": args.building_slug,
            "cutover_date": args.cutover_date,
            "opening_balances": [asdict(item) for item in balances],
            "declared_total_cents": _opening_total(balances),
            "evidence": {
                "document_type": evidence_payload.document_type,
                "file_url": evidence_payload.file_url,
                "sha256_hash": evidence_payload.sha256_hash,
                "approved_by": evidence_payload.approved_by,
                "uploaded_by": evidence_payload.uploaded_by,
                "declared_totals_by_fund": evidence_payload.declared_totals_by_fund,
                "source_system": evidence_payload.source_system,
                "import_batch_id": evidence_payload.import_batch_id,
                "notes": evidence_payload.notes,
            },
        }
        if args.validate_only or args.dry_run:
            return {"mode": "validate-only" if args.validate_only else "dry-run", "plan": plan}

        evidence = await create_evidence_document(evidence_payload)
        result = await onboard_building_financials(
            building_id=building_id,
            cutover_date=args.cutover_date,
            opening_balances=balances,
            evidence_document_id=str(evidence["document_id"]),
            approved_by_user_id=evidence_payload.approved_by,
        )
        return {
            "mode": "apply",
            "plan": plan,
            "evidence": evidence,
            "result": {
                "building_id": result.building_id,
                "cutover_date": result.cutover_date.isoformat(),
                "source_of_truth": result.source_of_truth,
                "readiness_status": result.readiness_status,
                "cutover_mode": result.cutover_mode,
                "read_source": result.read_source,
                "write_source": result.write_source,
                "onboarded": result.onboarded,
                "evidence_document_id": result.evidence_document_id,
                "journal_entry_ids": result.journal_entry_ids,
                "opening_balances": result.opening_balances,
            },
        }
    finally:
        await conn.close()


def parse_args() -> argparse.Namespace:
    """Generated function header.

    Function: parse_args
    Path: backend/scripts/bootstrap_postgres_financial_genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Bootstrap approved opening evidence and financial genesis journals.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Validate inputs and print the plan without writing.")
    mode.add_argument("--validate-only", action="store_true", help="Validate inputs and print the plan without writing.")
    mode.add_argument("--apply", action="store_true", help="Create evidence and genesis ledger rows.")
    parser.add_argument("--building-id", default=None, help="Building/plan identifier.")
    parser.add_argument("--scheme-id", default=None, help="Optional scheme UUID hint.")
    parser.add_argument("--plan-number", default=None, help="Optional scheme number hint.")
    parser.add_argument("--building-slug", default=None, help="Optional building slug hint.")
    parser.add_argument("--cutover-date", required=True, help="Cutover date in YYYY-MM-DD form.")
    parser.add_argument("--evidence-file-url", required=True, help="Source file URL or storage reference.")
    parser.add_argument("--evidence-sha256", required=True, help="SHA-256 hex digest for the evidence document.")
    parser.add_argument("--approved-by", required=True, help="UUID of the approver.")
    parser.add_argument("--uploaded-by", default=None, help="UUID of the uploader.")
    parser.add_argument("--document-type", default="bank_statement", choices=["bank_statement", "reconciliation", "xero_export"], help="Evidence document type.")
    parser.add_argument("--admin-fund-opening-cents", required=True, type=int, help="Admin fund opening balance in cents.")
    parser.add_argument("--sinking-fund-opening-cents", required=True, type=int, help="Sinking fund opening balance in cents.")
    parser.add_argument("--special-fund-opening-cents", default=None, help="Optional special fund opening balance in cents.")
    parser.add_argument("--source-system", default="manual_cutover", help="Originating source system for the evidence.")
    parser.add_argument("--import-batch-id", default=None, help="Import batch identifier.")
    parser.add_argument("--approved-at", default=None, help="Optional approval timestamp in ISO-8601 format.")
    parser.add_argument("--notes", default=None, help="Optional free-form notes.")
    parser.add_argument("--is-test-data", action="store_true", help="Mark the generated records as test data.")
    parser.add_argument("--output-json", action="store_true", help="Emit machine-readable JSON.")
    return parser.parse_args()


def main() -> int:
    """Generated function header.

    Function: main
    Path: backend/scripts/bootstrap_postgres_financial_genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    args = parse_args()
    args.cutover_date = datetime.fromisoformat(args.cutover_date).date()
    if args.special_fund_opening_cents is not None:
        args.special_fund_opening_cents = int(args.special_fund_opening_cents)
    if args.approved_at:
        datetime.fromisoformat(args.approved_at)
    report = asyncio.run(_run(args))
    if args.output_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
