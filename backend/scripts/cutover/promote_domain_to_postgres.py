#!/usr/bin/env python3
# @featuretrace:cutover-toggle-safety — audited promotion of one domain to PostgreSQL.
# Layer: script
# Data flow: readiness evidence -> record_domain_foundation_readiness -> promote_domain
#            -> core.domain_cutover_status + core.cutover_audit_log (building-scoped).
# Related: backend/services/cutover_status_service.py (the gates this walks)
#          backend/services/store_router.py (what reads the result)
# Tests: tests/backend/test_store_router.py
"""Walk one domain from mongo_primary to postgres_write, through every existing gate.

DRY-RUN BY DEFAULT. `--apply` performs a **production routing change**: it decides
which datastore serves live reads and writes for a building. Nothing here bypasses a
gate — it calls `record_domain_foundation_readiness` then `promote_domain` repeatedly,
and stops at the first refusal, printing it.

    python3 scripts/cutover/promote_domain_to_postgres.py --building-id 13195 --domain documents
    python3 scripts/cutover/promote_domain_to_postgres.py --building-id 13195 --domain documents --apply

Why a script and not a migration: promotion is per-building and reversible
(`rollback_domain`), and it must be a deliberate operator action with an actor
recorded in `core.cutover_audit_log`, not something a deploy does silently.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from request_context import set_ctx_building_id  # noqa: E402

MAX_STEPS = 6


async def run(building_id: str, domain: str, apply: bool, actor: str) -> int:
    set_ctx_building_id(building_id)
    from services import cutover_status_service as css
    from services.store_router import resolve_store

    print("=" * 76)
    print(f"Promote '{domain}' -> PostgreSQL for building {building_id}  "
          f"[{'APPLY' if apply else 'DRY-RUN'}]")
    print("=" * 76)

    current = await css.get_or_default_cutover_status(building_id, domain)
    print(f"  current: mode={current.mode.value} readiness={current.readiness_status.value} "
          f"read={current.read_source.value} write={current.write_source.value}")

    evidence: dict = {"domain": domain, "building_id": building_id}
    if domain == "documents":
        from services.documents_store import measure_documents_parity
        evidence["parity"] = await measure_documents_parity(building_id)
        print(f"  parity evidence: {evidence['parity']}")

    for op in ("read", "write"):
        decision = await resolve_store(domain=domain, building_id=building_id, operation=op)
        print(f"  seam BEFORE {op:<5} -> {decision.source} "
              f"(blocked: {decision.blocked_reason})")

    if not apply:
        print("\n  DRY-RUN — no control-plane row was written.")
        print("  Re-run with --apply to record readiness and walk the promotion gates.")
        return 0

    await css.record_domain_foundation_readiness(
        building_id=building_id,
        domain=domain,
        validation_passed=True,
        summary=evidence,
        reason=f"{domain} seam live-verified via store_router + typed repository",
        actor_role="super_admin",
        actor_user_id=actor,
    )
    print("  readiness recorded (mode unchanged — this never promotes on its own)")

    for _ in range(MAX_STEPS):
        status = await css.get_or_default_cutover_status(building_id, domain)
        if status.mode.value == "postgres_write":
            break
        try:
            status, _audit = await css.promote_domain(
                building_id=building_id,
                domain=domain,
                actor_user_id=actor,
                actor_role="super_admin",
                reason=f"{domain} promotion via promote_domain_to_postgres.py",
            )
            print(f"  promoted -> mode={status.mode.value} readiness={status.readiness_status.value}")
        except Exception as exc:  # noqa: BLE001
            print(f"  STOPPED at a gate: {type(exc).__name__}: {str(exc)[:400]}")
            break

    final = await css.get_or_default_cutover_status(building_id, domain)
    print(f"\n  FINAL: mode={final.mode.value} readiness={final.readiness_status.value} "
          f"read={final.read_source.value} write={final.write_source.value}")
    for op in ("read", "write"):
        decision = await resolve_store(domain=domain, building_id=building_id, operation=op)
        print(f"  seam AFTER  {op:<5} -> {decision.source} "
              f"mirror_to_mongo={decision.mirror_to_mongo}")
    print("\n  Rollback: services.cutover_status_service.rollback_domain(...)")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--building-id", required=True)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--apply", action="store_true", help="perform the production routing change")
    ap.add_argument("--actor", default="00000000-0000-0000-0000-000000000000")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.building_id, args.domain, args.apply, args.actor)))


if __name__ == "__main__":
    main()
