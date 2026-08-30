#!/usr/bin/env python3
# @featuretrace:financial-onboarding — East Gate zero-balance genesis onboarding runner.
# Data flow: CLI -> financial_onboarding service -> finance.evidence_documents/journal_entries/financial_cutover_config/audit.
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from db_postgres.repos.config_repo import resolve_scheme_context  # noqa: E402
from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from services.financial_core.domain.entities import SchemeRef  # noqa: E402
from services.financial_core.genesis import (  # noqa: E402
    SYSTEM_CUTOVER_USER_ID,
    compute_evidence_doc_hash,
    ensure_cutover_actor_user,
)
from services.onboarding.financial_onboarding import (  # noqa: E402
    EvidenceDocumentCreate,
    FundBalance,
    create_evidence_document,
    onboard_building_financials,
)

OPENING_DATE = date(2020, 12, 1)
NOTES = (
    "East Gate Residences genesis - scheme commencement 1 Dec 2020. "
    "Opening balances zero (new scheme). Source: Strata Web portal scrape."
)


def _hash_payload(fund_type: str) -> dict[str, object]:
    """Generated function header.

    Function: _hash_payload
    Path: backend/scripts/east_gate_genesis_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "fund_type": fund_type,
        "opening_balance_cents": 0,
        "as_at_date": OPENING_DATE.isoformat(),
        "bsb": "",
        "account_number": "",
        "account_name": "East Gate Residences",
        "evidence_source": "strata_web_portal_scrape",
        "notes": NOTES,
    }


async def _scheme_ref(building_id: str) -> SchemeRef:
    """Generated function header.

    Function: _scheme_ref
    Path: backend/scripts/east_gate_genesis_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scheme = await resolve_scheme_context(building_id)
    if not scheme:
        raise RuntimeError(f"No PostgreSQL scheme context for building_id={building_id}")
    return SchemeRef(tenant_id=scheme["tenant_id"], scheme_id=scheme["scheme_id"])


async def _ensure_actor(building_id: str, scheme_ref: SchemeRef) -> str:
    """Generated function header.

    Function: _ensure_actor
    Path: backend/scripts/east_gate_genesis_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    async with async_session_context() as session:
        await set_tenant(session, str(scheme_ref.tenant_id))
        user_id = await ensure_cutover_actor_user(session, scheme_ref=scheme_ref, building_id=building_id)
    return str(user_id)


async def run(building_id: str, apply: bool) -> dict[str, object]:
    """Generated function header.

    Function: run
    Path: backend/scripts/east_gate_genesis_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scheme_ref = await _scheme_ref(building_id)
    actor_id = await _ensure_actor(building_id, scheme_ref)
    sha = compute_evidence_doc_hash(_hash_payload("admin"))
    payload = EvidenceDocumentCreate(
        building_id=building_id,
        document_type="reconciliation",
        file_url="strata_web://portal-scrape/east-gate/2021-opening",
        sha256_hash=sha,
        uploaded_by=actor_id,
        approved_by=actor_id,
        declared_total_cents=0,
        declared_totals_by_fund={"ADMIN": 0, "SINK": 0},
        source_system="strata_web_portal_scrape",
        notes=NOTES,
        metadata={"scheme_commencement": OPENING_DATE.isoformat(), "lots": 87, "location": "Denman Prospect ACT"},
        is_test_data=False,
    )
    preview = {
        "building_id": building_id,
        "scheme_id": str(scheme_ref.scheme_id),
        "actor_id": actor_id,
        "evidence_sha256_hash": sha,
        "opening_balances": [
            {"fund_code": "ADMIN", "amount_cents": 0, "as_at_date": OPENING_DATE.isoformat()},
            {"fund_code": "SINK", "amount_cents": 0, "as_at_date": OPENING_DATE.isoformat()},
        ],
        "idempotency_keys": [
            f"clean-slate-genesis:{scheme_ref.scheme_id}:admin:{OPENING_DATE.isoformat()}",
            f"clean-slate-genesis:{scheme_ref.scheme_id}:sinking:{OPENING_DATE.isoformat()}",
        ],
    }
    if not apply:
        return {"dry_run": True, "would_create_or_reuse": preview}
    evidence = await create_evidence_document(payload)
    try:
        result = await onboard_building_financials(
            building_id=building_id,
            cutover_date=OPENING_DATE,
            opening_balances=[FundBalance("ADMIN", 0), FundBalance("SINK", 0)],
            evidence_document_id=evidence["document_id"],
            approved_by_user_id=actor_id or str(SYSTEM_CUTOVER_USER_ID),
        )
        return {"dry_run": False, "evidence": evidence, "onboarding": result.__dict__, "preview": preview}
    except ValueError as exc:
        if "already completed" not in str(exc).lower():
            raise
        return {"dry_run": False, "evidence": evidence, "onboarding": "already completed", "preview": preview}


def main() -> int:
    """Generated function header.

    Function: main
    Path: backend/scripts/east_gate_genesis_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Run East Gate genesis financial onboarding.")
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--apply", action="store_true", help="Execute writes. Default is dry-run.")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.building_id, args.apply)), default=str, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
