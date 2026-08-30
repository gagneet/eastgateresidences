#!/usr/bin/env python3
# @featuretrace:pg-migration-shadow — Generalized Phase D cutover activation/rollback for any domain.
# Layer: script
# Data flow: CLI → services.cutover_status_service.promote_domain()/rollback_domain() →
#            core.domain_cutover_status + core.shadow_diffs + core.cutover_audit_log (building-scoped).
# Related: backend/services/cutover_status_service.py
#          backend/scripts/east_gate_phase_d_activate.py
#          backend/scripts/phase_d_shadow_status.py
#          docs/migration/shadow-correction-updates-plan-p1.md
"""Generalized Phase D activation/rollback — any domain, any building, via the service layer only.

Unlike east_gate_phase_d_activate.py (finance-only, hand-rolled SQL for the mode transition),
this script calls cutover_status_service.promote_domain()/rollback_domain() exclusively —
it is not itself a second enforcement point. It inherits, for free:
  - block_unsafe_global_cutover (wildcard building_id rejected)
  - state-machine transition validation
  - the mode/readiness consistency check (rejects corrupted rows)
  - the pre-shadow readiness gate (rejects blocked/unknown/not_started domains)
  - the generic P0 platform check
  - finance_ledger's own extra postgres_read gate (financial_onboarding + identity_foundation)
  - audit logging

Defaults to --dry-run when neither --apply nor --rollback is given. --apply/--rollback both
require --reason.

Usage:
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    python backend/scripts/phase_d_activate.py --domain identity_core --building-id 13195 --dry-run
    python backend/scripts/phase_d_activate.py --domain identity_core --building-id 13195 --apply --reason "..."
    python backend/scripts/phase_d_activate.py --domain identity_core --building-id 13195 --rollback --reason "..."
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import text  # noqa: E402

from db_postgres.repos.config_repo import resolve_scheme_context  # noqa: E402
from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from models.cutover_status import VALID_FORWARD_TRANSITIONS, VALID_ROLLBACK_TRANSITIONS  # noqa: E402
from services.cutover_status_service import (  # noqa: E402
    block_unsafe_global_cutover,
    canonical_domain,
    get_or_default_cutover_status,
    promote_domain,
    rollback_domain,
)


async def _resolve_actor_user_id(building_id: str) -> str:
    """Best-effort: attribute the audit entry to a real active operator in this building's
    tenant (super_admin preferred, then strata_manager), falling back to a synthetic UUID
    if none can be resolved — the audit trail is still written either way.
    """
    scheme = await resolve_scheme_context(building_id)
    if not scheme or not scheme.get("tenant_id"):
        return str(uuid4())
    tenant_id = str(scheme["tenant_id"])
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        result = await session.execute(
            text(
                """
                SELECT user_id::text AS user_id FROM core.users
                WHERE tenant_id = CAST(:tenant_id AS UUID) AND is_active = TRUE AND is_approved = TRUE
                ORDER BY CASE WHEN role::text = 'super_admin' THEN 0
                              WHEN role::text = 'strata_manager' THEN 1
                              ELSE 2 END,
                         created_at
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id},
        )
        row = result.fetchone()
    return row.user_id if row else str(uuid4())


async def run(
        *,
        domain: str,
        building_id: str,
        dry_run: bool,
        do_apply: bool,
        do_rollback: bool,
        reason: str | None,
) -> dict:
    """Generated function header.

    Function: run
    Path: backend/scripts/phase_d_activate.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    domain = canonical_domain(domain)
    # Fail fast on a wildcard building_id before any DB round-trip — promote_domain()/
    # rollback_domain() re-check this too, but a clear early error is friendlier at the CLI.
    try:
        block_unsafe_global_cutover(building_id)
    except ValueError as exc:
        return {"error": str(exc), "domain": domain, "building_id": building_id}

    current = await get_or_default_cutover_status(building_id, domain)
    action = "rollback" if do_rollback else "promote"
    proposed_mode = (
        VALID_ROLLBACK_TRANSITIONS.get(current.mode) if do_rollback
        else VALID_FORWARD_TRANSITIONS.get(current.mode)
    )

    if dry_run or not (do_apply or do_rollback):
        return {
            "domain": domain,
            "building_id": building_id,
            "action": action,
            "current_mode": current.mode.value,
            "current_readiness": current.readiness_status.value,
            "proposed_mode": proposed_mode.value if proposed_mode else None,
            "readiness_evidence": current.p0_snapshot,
            "dry_run": True,
        }

    actor_id = await _resolve_actor_user_id(building_id)

    try:
        if do_rollback:
            updated, audit_id = await rollback_domain(
                building_id=building_id, domain=domain,
                actor_user_id=actor_id, actor_role="super_admin", reason=reason,
            )
        else:
            updated, audit_id = await promote_domain(
                building_id=building_id, domain=domain,
                actor_user_id=actor_id, actor_role="super_admin", reason=reason,
            )
    except HTTPException as exc:
        return {
            "error": exc.detail, "status_code": exc.status_code,
            "domain": domain, "building_id": building_id, "action": action,
        }
    except ValueError as exc:
        return {"error": str(exc), "domain": domain, "building_id": building_id, "action": action}

    return {
        "domain": domain,
        "building_id": building_id,
        "action": action,
        "audit_id": audit_id,
        "previous_mode": current.mode.value,
        "new_mode": updated.mode.value,
        "new_readiness": updated.readiness_status.value,
        "dry_run": False,
    }


def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/phase_d_activate.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(
        description="Generalized Phase D cutover activation/rollback for any domain/building."
    )
    parser.add_argument("--domain", required=True, help="e.g. identity_core, trust_ledger, finance_ledger")
    parser.add_argument("--building-id", default="13195")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    parser.add_argument("--reason", default=None, help="Required with --apply or --rollback")
    args = parser.parse_args()

    if (args.apply or args.rollback) and not args.reason:
        parser.error("--reason is required with --apply or --rollback")

    dry_run = args.dry_run or not (args.apply or args.rollback)  # default to dry-run
    result = asyncio.run(run(
        domain=args.domain,
        building_id=args.building_id,
        dry_run=dry_run,
        do_apply=args.apply,
        do_rollback=args.rollback,
        reason=args.reason,
    ))
    print(json.dumps(result, indent=2, default=str))
    if "error" in result:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
