#!/usr/bin/env python3
# @featuretrace:cutover-control-plane — Governed, idempotent Powerhouse domain promotion script.
# Layer: script
# Data flow: CLI -> cutover_status_service.record_powerhouse_foundation_readiness()/promote_domain()
#            -> core.domain_cutover_status + core.cutover_audit_log (building-scoped).
# Related: backend/services/cutover_status_service.py
#          backend/scripts/powerhouse_postgres_readiness.py
"""
Powerhouse domain: governed, idempotent promotion via cutover_status_service.promote_domain.

Advances a Powerhouse domain (powerhouse_conversations / powerhouse_inbox /
powerhouse_workflows / powerhouse_automation) step-by-step through the governed
cutover lifecycle (mongo_primary -> postgres_shadow -> postgres_read ->
postgres_write) up to an explicit --target-mode, one call to promote_domain()
per step. This is the governed alternative to
services.powerhouse_command_foundation.seed_powerhouse_control_plane_statuses(),
which jumps straight to postgres_write in one raw INSERT with no P0 gate, no
forward-transition check, and no audit log entry.

Powerhouse domains have never been through promote_domain()'s
mongo_primary -> postgres_shadow gate before, which hard-requires the domain's
readiness_status to already be one of ready_for_shadow / identity_ready /
evidence_ready / genesis_ready (see cutover_status_service._PRE_SHADOW_READY).
This script calls the new record_powerhouse_foundation_readiness() first
(schema presence + RLS + scheme-context checks, mirroring
powerhouse_postgres_readiness.py) whenever a domain is still sitting at
mongo_primary/unknown, then drives promote_domain() forward one step at a time.

SAFETY
------
* Building-agnostic: --building-id required, no "13195" default.
* --dry-run (default False) prints the planned step sequence without calling
  promote_domain or record_powerhouse_foundation_readiness.
* Idempotent: if a domain is already at or past --target-mode, reports a
  no-op rather than erroring.
* --is-test-data threads through to every readiness/promotion row written.

USAGE
-----
    cd backend && source venv/bin/activate

    # 1. See the planned steps first
    python3 scripts/powerhouse_promote_domain.py --building-id 13195 \
        --domain powerhouse_conversations --target-mode postgres_shadow \
        --actor-email manager@eastgate.com --reason "P2B-2 validation" --dry-run

    # 2. Apply
    python3 scripts/powerhouse_promote_domain.py --building-id 13195 \
        --domain powerhouse_conversations --target-mode postgres_shadow \
        --actor-email manager@eastgate.com --reason "P2B-2 validation"

    # 3. Re-run with a further --target-mode to advance again (idempotent up to
    #    the new target; already-passed steps are skipped, not repeated)
    python3 scripts/powerhouse_promote_domain.py --building-id 13195 \
        --domain powerhouse_conversations --target-mode postgres_write \
        --actor-email manager@eastgate.com --reason "P2B-2 validation"

    # --all promotes every Powerhouse domain to the same --target-mode
    python3 scripts/powerhouse_promote_domain.py --building-id 13195 --all \
        --target-mode postgres_read --actor-email manager@eastgate.com --reason "..."

    # --rollback steps a domain back exactly one mode via cutover_status_service.rollback_domain
    # (e.g. postgres_write -> postgres_read, when a command's write surface isn't
    # complete enough yet to keep write_source=postgres). No --target-mode needed.
    python3 scripts/powerhouse_promote_domain.py --building-id 13195 \
        --domain powerhouse_conversations --rollback \
        --actor-email manager@eastgate.com --reason "only 2 of ~13 commands exist"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

POWERHOUSE_DOMAINS = (
    "powerhouse_conversations",
    "powerhouse_inbox",
    "powerhouse_workflows",
    "powerhouse_automation",
)

# Ordering used to detect "already at or past target" — matches
# models.cutover_status.VALID_FORWARD_TRANSITIONS's forward sequence for the
# three modes this script ever targets.
_MODE_ORDER = ["mongo_primary", "postgres_shadow", "postgres_read", "postgres_write"]

_REQUIRED_TABLES = [
    ("communications", "inboxes"),
    ("communications", "conversation_threads"),
    ("communications", "conversation_messages"),
    ("communications", "conversation_participants"),
    ("communications", "conversation_watchers"),
    ("communications", "thread_entity_links"),
    ("workflow", "workflow_templates"),
    ("workflow", "workflow_instances"),
    ("workflow", "workflow_steps"),
    ("workflow", "workflow_events"),
    ("core", "command_idempotency_records"),
    ("core", "outbox"),
    ("core", "audit_events"),
]


async def _check_powerhouse_foundation_ready(building_id: str) -> dict[str, Any]:
    """Schema/RLS/scheme-context checks — same checks as powerhouse_postgres_readiness.py."""
    from sqlalchemy import text
    from db_postgres.session import async_session_context
    from db_postgres.repos.config_repo import resolve_scheme_context

    missing: list[str] = []
    rls_disabled: list[str] = []
    async with async_session_context() as session:
        for schema, table in _REQUIRED_TABLES:
            res = await session.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = :schema AND table_name = :table
                    ) AS present
                    """
                ),
                {"schema": schema, "table": table},
            )
            if not res.scalar():
                missing.append(f"{schema}.{table}")
                continue
            rls_res = await session.execute(
                text("SELECT rowsecurity FROM pg_tables WHERE schemaname = :schema AND tablename = :table"),
                {"schema": schema, "table": table},
            )
            if not rls_res.scalar():
                rls_disabled.append(f"{schema}.{table}")

    scheme = await resolve_scheme_context(building_id)
    summary = {
        "schema_present": not missing,
        "missing_tables": missing,
        "rls_enabled": not rls_disabled,
        "rls_disabled_tables": rls_disabled,
        "scheme_resolvable": scheme is not None,
    }
    summary["validation_passed"] = bool(summary["schema_present"] and summary["rls_enabled"] and summary["scheme_resolvable"])
    return summary


async def _current_mode(building_id: str, domain: str) -> dict[str, Any]:
    """Return a JSON-safe snapshot of a domain's current cutover status."""
    from services.cutover_status_service import get_or_default_cutover_status

    status = await get_or_default_cutover_status(building_id, domain)
    return {
        "mode": getattr(status.mode, "value", str(status.mode)),
        "read_source": getattr(status.read_source, "value", str(status.read_source)),
        "write_source": getattr(status.write_source, "value", str(status.write_source)),
        "readiness_status": getattr(status.readiness_status, "value", str(status.readiness_status)),
    }


def _mode_index(mode: str) -> int:
    """Return a mode's position in the forward sequence, or -1 if unrecognised."""
    try:
        return _MODE_ORDER.index(mode)
    except ValueError:
        return -1


async def _promote_one_domain(*, building_id: str, domain: str, target_mode: str, actor_user_id: str,
                               actor_role: str, reason: str, is_test_data: bool, dry_run: bool) -> dict[str, Any]:
    """Advance one domain toward target_mode, recording readiness first if needed."""
    from services.cutover_status_service import promote_domain, record_powerhouse_foundation_readiness

    out: dict[str, Any] = {"domain": domain, "steps": [], "promoted": False}
    before = await _current_mode(building_id, domain)
    out["mode_before"] = before

    target_idx = _mode_index(target_mode)
    current_idx = _mode_index(before["mode"])
    if current_idx >= target_idx:
        out["note"] = f"Already at or past '{target_mode}' (current='{before['mode']}') — nothing to do."
        return out

    if dry_run:
        planned = _MODE_ORDER[current_idx + 1: target_idx + 1]
        out["planned_steps"] = planned
        out["note"] = "--dry-run: no changes made."
        return out

    if before["mode"] == "mongo_primary" and before["readiness_status"] not in {
        "ready_for_shadow", "identity_ready", "evidence_ready", "genesis_ready",
    }:
        readiness_check = await _check_powerhouse_foundation_ready(building_id)
        status = await record_powerhouse_foundation_readiness(
            building_id=building_id,
            domain=domain,
            validation_passed=readiness_check["validation_passed"],
            summary=readiness_check,
            reason=reason,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            is_test_data=is_test_data,
        )
        out["steps"].append({
            "action": "record_powerhouse_foundation_readiness",
            "readiness_check": readiness_check,
            "readiness_status_after": getattr(status.readiness_status, "value", str(status.readiness_status)),
        })
        if not readiness_check["validation_passed"]:
            out["error"] = "Foundation readiness check failed — refusing to enter shadow mode."
            return out

    while _mode_index((await _current_mode(building_id, domain))["mode"]) < target_idx:
        try:
            status, audit_id = await promote_domain(
                building_id=building_id,
                domain=domain,
                actor_user_id=actor_user_id,
                actor_role=actor_role,
                reason=reason,
                is_test_data=is_test_data,
            )
        except Exception as exc:  # promote_domain raises HTTPException(409) on a blocked gate
            out["error"] = f"{type(exc).__name__}: {getattr(exc, 'detail', exc)}"
            break
        new_mode = getattr(status.mode, "value", str(status.mode))
        out["steps"].append({"action": "promote_domain", "new_mode": new_mode, "audit_id": str(audit_id)})
        out["promoted"] = True

    out["mode_after"] = await _current_mode(building_id, domain)
    return out


async def _rollback_one_domain(*, building_id: str, domain: str, actor_user_id: str,
                                actor_role: str, reason: str, is_test_data: bool, dry_run: bool) -> dict[str, Any]:
    """Step one domain back exactly one mode via cutover_status_service.rollback_domain."""
    from services.cutover_status_service import rollback_domain

    out: dict[str, Any] = {"domain": domain, "steps": [], "promoted": False}
    before = await _current_mode(building_id, domain)
    out["mode_before"] = before

    if dry_run:
        out["note"] = f"--dry-run: would roll back one step from '{before['mode']}'."
        return out

    try:
        status, audit_id = await rollback_domain(
            building_id=building_id,
            domain=domain,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            reason=reason,
            is_test_data=is_test_data,
        )
    except Exception as exc:  # rollback_domain raises HTTPException(409) if already at the floor
        out["error"] = f"{type(exc).__name__}: {getattr(exc, 'detail', exc)}"
        out["mode_after"] = await _current_mode(building_id, domain)
        return out

    new_mode = getattr(status.mode, "value", str(status.mode))
    out["steps"].append({"action": "rollback_domain", "new_mode": new_mode, "audit_id": str(audit_id)})
    out["mode_after"] = await _current_mode(building_id, domain)
    return out


async def run(args: argparse.Namespace) -> dict[str, Any]:
    """Resolve the actor and promote/rollback each requested domain."""
    domains = list(POWERHOUSE_DOMAINS) if args.all else [args.domain]
    actor_user_id = None
    if not args.dry_run:
        from db_postgres.repos.config_repo import resolve_actor_user_id
        actor_user_id = await resolve_actor_user_id(None, args.actor_email, require_existing=True)
        if not actor_user_id:
            return {"error": f"Could not resolve --actor-email '{args.actor_email}' to an existing user."}

    results = []
    for domain in domains:
        if args.rollback:
            results.append(
                await _rollback_one_domain(
                    building_id=args.building_id,
                    domain=domain,
                    actor_user_id=actor_user_id,
                    actor_role=args.actor_role,
                    reason=args.reason,
                    is_test_data=args.is_test_data,
                    dry_run=args.dry_run,
                )
            )
        else:
            results.append(
                await _promote_one_domain(
                    building_id=args.building_id,
                    domain=domain,
                    target_mode=args.target_mode,
                    actor_user_id=actor_user_id,
                    actor_role=args.actor_role,
                    reason=args.reason,
                    is_test_data=args.is_test_data,
                    dry_run=args.dry_run,
                )
            )
    return {
        "building_id": args.building_id,
        "target_mode": None if args.rollback else args.target_mode,
        "rollback": args.rollback,
        "dry_run": args.dry_run,
        "results": results,
    }


def _print_human(res: dict[str, Any]) -> None:
    """Render the run() result as readable console output (vs --json)."""
    if "error" in res:
        print(f"\nERROR: {res['error']}\n")
        return
    action_label = "ROLLBACK" if res.get("rollback") else f"target_mode={res['target_mode']}"
    print(f"\n=== Powerhouse domain promotion  building={res['building_id']}  {action_label} "
          f"{'(DRY RUN)' if res['dry_run'] else ''} ===\n")
    for r in res["results"]:
        print(f"  Domain: {r['domain']}")
        print(f"    Before: {r['mode_before']}")
        for step in r.get("steps", []):
            print(f"    Step: {step}")
        if r.get("planned_steps"):
            print(f"    Planned steps: {r['planned_steps']}")
        if r.get("mode_after"):
            print(f"    After:  {r['mode_after']}")
        if r.get("note"):
            print(f"    NOTE: {r['note']}")
        if r.get("error"):
            print(f"    ERROR: {r['error']}")
        print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Powerhouse domain governed promotion (idempotent, is_test_data-aware)")
    ap.add_argument("--building-id", required=True, help="e.g. 13195 (no default — multi-tenant rule)")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--domain", choices=POWERHOUSE_DOMAINS, help="a single Powerhouse domain")
    group.add_argument("--all", action="store_true", help="promote all four Powerhouse domains")
    ap.add_argument("--target-mode", choices=["postgres_shadow", "postgres_read", "postgres_write"],
                     help="required unless --rollback")
    ap.add_argument("--rollback", action="store_true",
                     help="step back exactly one mode via rollback_domain instead of promoting to --target-mode")
    ap.add_argument("--actor-email", help="existing user's email, resolved to a UUID for the audit trail (required unless --dry-run)")
    ap.add_argument("--actor-role", default="super_admin")
    ap.add_argument("--reason", required=True, help="audit reason for the promotion/rollback")
    ap.add_argument("--is-test-data", action="store_true", help="tag every written row is_test_data=True")
    ap.add_argument("--dry-run", action="store_true", help="print planned steps only, no writes")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args()

    if not args.dry_run and not args.actor_email:
        ap.error("--actor-email is required unless --dry-run")
    if not args.rollback and not args.target_mode:
        ap.error("--target-mode is required unless --rollback")
    if args.rollback and args.target_mode:
        ap.error("--target-mode and --rollback are mutually exclusive")

    res = asyncio.run(run(args))
    if args.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        _print_human(res)
    return 0 if "error" not in res and not any(r.get("error") for r in res.get("results", [])) else 1


if __name__ == "__main__":
    sys.exit(main())
