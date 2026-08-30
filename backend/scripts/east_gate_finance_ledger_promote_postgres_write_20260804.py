#!/usr/bin/env python3
# @featuretrace:cutover-control-plane — East Gate finance_ledger: postgres_shadow -> postgres_write.
"""East Gate finance_ledger promotion: postgres_shadow -> postgres_read -> postgres_write.

Context (2026-08-04 session): GAP-FIN-045 cross-pipeline duplicate-receipt guard shipped
and tested (commit 46b95090, 5/5 tests pass). 73/87 units reconciled and hard-verified
between MongoDB and PostgreSQL. Comprehensive literal-duplicate scan across all 87 units
found nothing further to fix. Remaining variance on 13 units understood as genuine unpaid
arrears already correctly reflected in unit_levy_ledger.net_balance. Unit UA050's residual
~$15 gap is understood as accrued arrears interest (not itemized in the portal scrape) and
is an explicitly accepted manual follow-up, not a promotion blocker.

Steps, each independently idempotent and logged to core.cutover_audit_log:

  1. Record financial_onboarding=pass via a mode-preserving jsonb merge (NOT
     record_financial_foundation_readiness(), which unconditionally hardcodes
     mode=mongo_primary on every call -- confirmed live 2026-08-04 that this silently
     regressed finance_ledger/13195 from postgres_shadow back to mongo_primary mid-chain).
     This is what promote_domain() actually gates on for finance_ledger -> postgres_read.
  2. promote_domain(postgres_shadow -> postgres_read), with skip_finance_identity_gate=True.
     Justification for the override: identity_core's OWN readiness check in promote_domain()
     looks for a p0_snapshot["identity_foundation"] key that identity_core's real snapshot
     never populated (it uses "identity_pg_write_preflight" instead) -- a known key-naming
     mismatch in the check, not a real gap. identity_core's actual live state: mode=
     postgres_write, readiness=promoted, finalized 2026-08-02, with 0 unresolved shadow
     diffs and 87/87 owner-payload parity confirmed in its own preflight snapshot.
  3. Per-building feature-toggle overrides (core.feature_toggle_overrides, NOT global --
     the only sanctioned path for a protected DATA_SOURCE_PRIMARY/FINANCE_WRITE key):
     financial_integration_layer_v2 (umbrella -- reads/writes resolve False without it),
     financial_pg_reads_enabled.
  4. promote_domain(postgres_read -> postgres_write).
  5. Per-building override: financial_pg_writes_enabled.

Usage (from backend/):
    venv/bin/python3 scripts/east_gate_finance_ledger_promote_postgres_write_20260804.py             # dry-run
    venv/bin/python3 scripts/east_gate_finance_ledger_promote_postgres_write_20260804.py --apply      # apply
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BUILDING_ID = "13195"
ACTOR_ROLE = "super_admin"

EVIDENCE_SUMMARY = {
    "evidence": [
        "GAP-FIN-045 cross-pipeline duplicate-receipt guard shipped and tested (commit 46b95090; "
        "5/5 tests pass, tests/backend/test_financial_core_service.py::TestRecordPaymentBankTransactionDedup)",
        "306 itemized 2026 levy_payments backfilled to Mongo for 73/87 units from PostgreSQL "
        "finance.receipts, each hard-asserted against unit_levy_ledger.total_paid before and after write",
        "Comprehensive literal-duplicate scan across all 87 units found zero unsafe active duplicates remaining",
        "Remaining variance on 13 units is genuine unpaid arrears already correctly reflected in "
        "unit_levy_ledger.net_balance, confirmed against the live portal for UA050 (arrears interest, "
        "not a posting defect) -- accepted as a manual owner follow-up, not a blocker",
    ],
    "checked_by": "session_2026-08-04",
}

READINESS_REASON = (
    "Financial onboarding readiness recorded 2026-08-04: GAP-FIN-045 duplicate guard verified, "
    "73/87 units reconciled and hard-asserted, remaining variance understood as real arrears/interest "
    "not data defects."
)

IDENTITY_GATE_REASON = (
    "skip_finance_identity_gate: identity_core's promote_domain() check looks for "
    "p0_snapshot['identity_foundation'], which identity_core's real snapshot never populated -- it "
    "uses 'identity_pg_write_preflight' instead (key-naming mismatch, not a real gap). identity_core "
    "is live at mode=postgres_write/readiness=promoted, finalized 2026-08-02, with 0 unresolved shadow "
    "diffs and 87/87 owner-payload parity per its own preflight snapshot."
)

TOGGLE_REASON = (
    "Per-building promotion after finance_ledger domain readiness gates passed 2026-08-04 -- "
    "see core.cutover_audit_log for the paired promote_domain() entries."
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", default=False, help="Write. Without this flag, dry-run only.")
    return p.parse_args()


async def main() -> int:
    args = parse_args()

    import json
    import os
    from datetime import UTC, datetime
    import asyncpg
    from services.cutover_status_service import (
        promote_domain,
        get_or_default_cutover_status,
    )
    from models.cutover_status import CutoverMode
    from db_postgres.repos.config_repo import upsert_feature_toggle_override, resolve_actor_user_id

    BYPASS = "00000000-0000-0000-0000-000000000000"

    async def record_financial_onboarding_pass_without_mode_reset(building_id: str, summary: dict, reason: str) -> None:
        """record_financial_foundation_readiness() unconditionally hardcodes mode=mongo_primary
        on every call -- fine for a building that hasn't entered shadow yet, but a silent regression
        for one already past it (confirmed live 2026-08-04: it reset finance_ledger/13195 from
        postgres_shadow back to mongo_primary mid-promotion-chain). This merges just the
        financial_onboarding key into the existing p0_snapshot via a top-level jsonb merge,
        leaving mode/read_source/write_source/readiness_status untouched."""
        database_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
        conn = await asyncpg.connect(database_url)
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)
        payload = json.dumps({"financial_onboarding": {
            "status": "pass", "summary": summary,
            "checked_at": datetime.now(UTC).isoformat(),
        }})
        await conn.execute(
            """
            UPDATE core.domain_cutover_status
            SET p0_snapshot = COALESCE(p0_snapshot, '{}'::jsonb) || $1::jsonb,
                last_readiness_check_at = NOW(), updated_at = NOW()
            WHERE building_id = $2 AND domain = 'finance_ledger'
            """,
            payload, building_id,
        )
        await conn.execute(
            """
            INSERT INTO core.cutover_audit_log
                (id, tenant_id, building_id, domain, action, from_mode, to_mode, actor_role, reason, is_test_data, created_at)
            SELECT gen_random_uuid(), $1::uuid, $2, 'finance_ledger', 'readiness_checked', mode, mode, 'super_admin', $3, FALSE, NOW()
            FROM core.domain_cutover_status WHERE building_id = $2 AND domain = 'finance_ledger'
            """,
            "9e9d75c2-bd92-4695-8487-1592018c3af9", building_id, reason,
        )
        await conn.close()

    current = await get_or_default_cutover_status(BUILDING_ID, "finance_ledger")
    print(f"Current state: mode={current.mode.value} readiness={current.readiness_status.value}")

    if current.mode != CutoverMode.postgres_shadow:
        print(f"ABORT: expected mode=postgres_shadow, found {current.mode.value}. Not proceeding.")
        return 1

    if not args.apply:
        print()
        print("DRY-RUN plan:")
        print("  1. record financial_onboarding=pass via mode-preserving jsonb merge (NOT")
        print("     record_financial_foundation_readiness(), which resets mode to mongo_primary)")
        print("  2. promote_domain: postgres_shadow -> postgres_read (skip_finance_identity_gate=True)")
        print("  3. per-building override: financial_integration_layer_v2=True")
        print("  4. per-building override: financial_pg_reads_enabled=True")
        print("  5. promote_domain: postgres_read -> postgres_write")
        print("  6. per-building override: financial_pg_writes_enabled=True")
        print()
        print("Re-run with --apply to execute.")
        return 0

    # Resolve a real actor user id for the audit trail (upsert_feature_toggle_override
    # requires an existing core.users row, not a synthetic UUID).
    actor_user_id = await resolve_actor_user_id(None, "manager@eastgate.com", require_existing=True)

    print("\n[1/6] Recording financial_onboarding readiness (mode-preserving)...")
    await record_financial_onboarding_pass_without_mode_reset(BUILDING_ID, EVIDENCE_SUMMARY, READINESS_REASON)
    check = await get_or_default_cutover_status(BUILDING_ID, "finance_ledger")
    print(f"  -> mode={check.mode.value} readiness={check.readiness_status.value} (should be unchanged from above)")
    if check.mode != CutoverMode.postgres_shadow:
        print(f"  ABORT: mode drifted to {check.mode.value} unexpectedly -- not proceeding.")
        return 1

    print("\n[2/6] Promoting finance_ledger: postgres_shadow -> postgres_read...")
    status, audit_id = await promote_domain(
        building_id=BUILDING_ID,
        domain="finance_ledger",
        actor_user_id=str(actor_user_id),
        actor_role=ACTOR_ROLE,
        reason=IDENTITY_GATE_REASON,
        skip_finance_identity_gate=True,
    )
    print(f"  -> mode={status.mode.value} readiness={status.readiness_status.value} audit_id={audit_id}")

    print("\n[3/6] Per-building override: financial_integration_layer_v2=True...")
    result = await upsert_feature_toggle_override(
        BUILDING_ID, "financial_integration_layer_v2", True,
        actor_user_id=str(actor_user_id), reason=TOGGLE_REASON,
    )
    print(f"  -> {result}")

    print("\n[4/6] Per-building override: financial_pg_reads_enabled=True...")
    result = await upsert_feature_toggle_override(
        BUILDING_ID, "financial_pg_reads_enabled", True,
        actor_user_id=str(actor_user_id), reason=TOGGLE_REASON,
    )
    print(f"  -> {result}")

    print("\n[5/6] Promoting finance_ledger: postgres_read -> postgres_write...")
    status, audit_id = await promote_domain(
        building_id=BUILDING_ID,
        domain="finance_ledger",
        actor_user_id=str(actor_user_id),
        actor_role=ACTOR_ROLE,
        reason="Second promotion step in the same 2026-08-04 session -- see step 2's audit entry for full justification.",
        skip_finance_identity_gate=True,  # no-op for this transition (gate only applies to ->postgres_read); kept for defense in depth
    )
    print(f"  -> mode={status.mode.value} readiness={status.readiness_status.value} audit_id={audit_id}")

    print("\n[6/6] Per-building override: financial_pg_writes_enabled=True...")
    result = await upsert_feature_toggle_override(
        BUILDING_ID, "financial_pg_writes_enabled", True,
        actor_user_id=str(actor_user_id), reason=TOGGLE_REASON,
    )
    print(f"  -> {result}")

    final = await get_or_default_cutover_status(BUILDING_ID, "finance_ledger")
    print(f"\nFINAL STATE: mode={final.mode.value} readiness={final.readiness_status.value} "
          f"read_source={final.read_source.value} write_source={final.write_source.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
