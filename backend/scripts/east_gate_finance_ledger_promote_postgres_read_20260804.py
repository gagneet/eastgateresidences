#!/usr/bin/env python3
# @featuretrace:cutover-control-plane — East Gate finance_ledger READ-FIRST promotion (postgres_shadow -> postgres_read).
# Layer: script
# Data flow: CLI -> cutover_status_service.promote_domain + config_repo.upsert_feature_toggle_override
#            -> core.domain_cutover_status + core.feature_toggle_overrides + core.cutover_audit_log (building-scoped).
# Related: backend/scripts/east_gate_phase_d_activate.py         (enter shadow)
#          backend/scripts/east_gate_phase_d_shadow_status.py    (monitor shadow parity)
#          backend/services/finance_route_cutover_service.py     (runtime read gate)
#          docs/deployment/east_gate_finance_ledger_read_first_cutover_runbook.md
"""East Gate (13195) finance_ledger READ-FIRST promotion — postgres_shadow -> postgres_read ONLY.

This script deliberately does the *reversible* half of the cutover and HARD-STOPS
before any write promotion. It performs exactly three mutations, all building-scoped:

  1. promote_domain(finance_ledger): postgres_shadow -> postgres_read
       with skip_finance_identity_gate=True.
       WHY skip and not "record identity readiness": identity_core is live at
       postgres_write/promoted. record_identity_foundation_readiness() FORCES its
       row back to mongo_primary (cutover_status_service.py:884-887) — calling it
       would REGRESS the live identity/auth domain. The promote-time identity gate
       is the only place that must be satisfied for a READ promotion (the runtime
       READ gate in finance_route_cutover_service.py has NO identity check), so the
       narrow super_admin skip is correct and sufficient here.
  2. per-building override: financial_integration_layer_v2 = True  (umbrella; every
       child toggle resolves False without it — cutover_config_service.py:131-140).
  3. per-building override: financial_pg_reads_enabled = True.

It NEVER:
  * promotes past postgres_read,
  * touches financial_pg_writes_enabled,
  * calls record_identity_foundation_readiness (would regress identity_core),
  * writes a global toggle (everything is core.feature_toggle_overrides, scheme-scoped).

HARD PRECONDITIONS (refuses --apply otherwise):
  * finance_ledger current mode == postgres_shadow.
      - If mongo_primary: run east_gate_phase_d_activate.py --apply first (and record
        financial_onboarding readiness — see the runbook; that call resets the row to
        mongo_primary by design, so it must precede the shadow hop, not follow it).
      - If already postgres_read / postgres_write: nothing to do here.
  * financial_onboarding readiness snapshot status == "pass".
  * finance.unit_dashboard_overview shadow readiness == "shadow_pass" AND critical_count == 0.
      This mirrors the runtime READ gate exactly (finance_route_cutover_service.py:247-253):
      promoting the mode before shadow_pass just leaves reads on Mongo. Override only with
      an explicit, audited --force-before-shadow-pass if you understand reads stay on Mongo
      until parity is proven. Phase D's own bar is 7 consecutive clean days
      (east_gate_phase_d_shadow_status.py) — prefer that before promoting.

Dry-run by default. Reversible: east_gate_phase_d_activate.py --rollback returns the
domain to mongo_primary; delete the two overrides to revert reads to Mongo.

Usage (from repo root):
    source backend/venv/bin/activate
    python backend/scripts/east_gate_finance_ledger_promote_postgres_read_20260804.py \
        --actor-user-id <super_admin_uuid> [--actor-email you@example.com]           # dry-run
    python backend/scripts/east_gate_finance_ledger_promote_postgres_read_20260804.py \
        --actor-user-id <super_admin_uuid> --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from db_postgres.repos.config_repo import upsert_feature_toggle_override  # noqa: E402
from models.cutover_status import CutoverMode  # noqa: E402
from services.cutover_config_service import (  # noqa: E402
    FINANCIAL_PG_READS_ENABLED,
    FINANCIAL_PG_WRITES_ENABLED,
    UMBRELLA_FEATURE_KEY,
)
from services.cutover_status_service import (  # noqa: E402
    get_or_default_cutover_status,
    promote_domain,
)
from services.finance_shadow_read_service import get_route_shadow_readiness  # noqa: E402

_DOMAIN = "finance_ledger"
_READ_ROUTE_KEY = "finance.unit_dashboard_overview"

_EVIDENCE = {
    "duplicate_receipt_guard": "FinancialCoreService._post_payment_journal cross-pipeline "
    "guard keyed on bank_transaction_id (PR #567); +5 tests.",
    "reconciliation": "Manual receipt-level reconciliation PG vs Mongo (owner workbook); "
    "gap decomposes to legitimate PG-ahead bank-matching + portal-scrape deltas.",
    "duplicate_scan": "Exhaustive hard-assert duplicate scan returned zero safe reversals; "
    "residuals (e.g. UA050 ~$16.07) explained as accrued arrears interest, not duplicates.",
    "note": "Interest is a computed/estimated read-side figure (item E finding: East Gate has "
    "no per-unit interest transactions). PG-vs-portal interest parity is a SEPARATE follow-up "
    "and is NOT a precondition satisfied by shadow_pass (which only proves PG==Mongo).",
}


async def _snapshot(building_id: str) -> dict:
    fin = await get_or_default_cutover_status(building_id, _DOMAIN)
    ident = await get_or_default_cutover_status(building_id, "identity_core")
    readiness = await get_route_shadow_readiness(
        building_id=building_id, route_key=_READ_ROUTE_KEY
    )
    fin_snap = (fin.p0_snapshot or {}).get("financial_onboarding", {}) if fin else {}
    return {
        "finance_ledger": {
            "mode": fin.mode.value if fin else None,
            "readiness_status": fin.readiness_status.value if fin else None,
            "financial_onboarding_status": fin_snap.get("status"),
        },
        "identity_core": {
            "mode": ident.mode.value if ident else None,
            "readiness_status": ident.readiness_status.value if ident else None,
        },
        "shadow_read": {
            "route": _READ_ROUTE_KEY,
            "status": readiness.get("status"),
            "critical_count": readiness.get("critical_count"),
            "diff_count": readiness.get("diff_count"),
        },
    }


def _blockers(state: dict, force_before_shadow_pass: bool) -> list[str]:
    blockers: list[str] = []
    fin = state["finance_ledger"]
    sr = state["shadow_read"]
    if fin["mode"] == CutoverMode.postgres_read.value:
        blockers.append("ALREADY_AT_READ: finance_ledger is already postgres_read; nothing to promote.")
    elif fin["mode"] in (CutoverMode.postgres_write.value, "mongo_archive"):
        blockers.append(
            f"PAST_READ: finance_ledger mode is {fin['mode']!r}; this read-first script refuses to act."
        )
    elif fin["mode"] != CutoverMode.postgres_shadow.value:
        blockers.append(
            f"NOT_IN_SHADOW: finance_ledger mode is {fin['mode']!r}; expected postgres_shadow. "
            "Run east_gate_phase_d_activate.py --apply (after recording financial_onboarding readiness) first."
        )
    if fin["financial_onboarding_status"] != "pass":
        blockers.append(
            "READINESS_NOT_PASS: financial_onboarding snapshot is "
            f"{fin['financial_onboarding_status']!r}; record it as pass first (see runbook)."
        )
    if not (sr["status"] == "shadow_pass" and int(sr["critical_count"] or 0) == 0):
        msg = (
            f"SHADOW_NOT_PASS: {_READ_ROUTE_KEY} readiness is {sr['status']!r} "
            f"(critical={sr['critical_count']}); runtime read gate requires shadow_pass/0-critical."
        )
        if force_before_shadow_pass:
            msg += " [OVERRIDDEN via --force-before-shadow-pass: reads WILL stay on Mongo until parity is proven]"
        else:
            blockers.append(msg)
    return blockers


async def run(building_id: str, actor_user_id: str, actor_email: str | None,
              apply: bool, force_before_shadow_pass: bool) -> dict:
    before = await _snapshot(building_id)
    blockers = _blockers(before, force_before_shadow_pass)

    plan = [
        f"promote_domain({_DOMAIN}): postgres_shadow -> postgres_read (skip_finance_identity_gate=True)",
        f"override {UMBRELLA_FEATURE_KEY} = True (per-building {building_id})",
        f"override {FINANCIAL_PG_READS_ENABLED} = True (per-building {building_id})",
        f"HARD STOP — {FINANCIAL_PG_WRITES_ENABLED} left untouched; no write promotion",
    ]

    if not apply or blockers:
        return {
            "dry_run": not apply,
            "building_id": building_id,
            "state_before": before,
            "planned_mutations": plan,
            "blockers": blockers,
            "will_execute": bool(apply and not blockers),
            "evidence": _EVIDENCE,
        }

    reason = "READ-FIRST promotion (PR #567 evidence); write deferred pending 7-clean-day shadow + PG-vs-portal interest parity."

    status, audit_id = await promote_domain(
        building_id=building_id,
        domain=_DOMAIN,
        actor_user_id=actor_user_id,
        actor_role="super_admin",
        reason=reason + " | " + json.dumps(_EVIDENCE, sort_keys=True),
        skip_finance_identity_gate=True,
    )
    if status.mode != CutoverMode.postgres_read:
        return {
            "dry_run": False,
            "error": f"promote_domain landed at {status.mode.value!r}, expected postgres_read; aborting before toggles.",
            "audit_id": audit_id,
            "state_after": await _snapshot(building_id),
        }

    umbrella = await upsert_feature_toggle_override(
        building_id, UMBRELLA_FEATURE_KEY, True,
        actor_user_id=actor_user_id, actor_email=actor_email, reason=reason,
    )
    reads = await upsert_feature_toggle_override(
        building_id, FINANCIAL_PG_READS_ENABLED, True,
        actor_user_id=actor_user_id, actor_email=actor_email, reason=reason,
    )

    return {
        "dry_run": False,
        "building_id": building_id,
        "promote_audit_id": audit_id,
        "overrides": {
            UMBRELLA_FEATURE_KEY: umbrella.get("is_enabled") if isinstance(umbrella, dict) else umbrella,
            FINANCIAL_PG_READS_ENABLED: reads.get("is_enabled") if isinstance(reads, dict) else reads,
        },
        "state_after": await _snapshot(building_id),
        "write_promotion": "DEFERRED — run the separate reviewed write script only after "
        "7-clean-day shadow parity AND PG-vs-portal interest reconciliation.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="East Gate finance_ledger READ-FIRST promotion (postgres_shadow -> postgres_read). Dry-run by default."
    )
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--actor-user-id", required=True,
                        help="Super-admin user_id (UUID) recorded as the promoting actor.")
    parser.add_argument("--actor-email", default=None,
                        help="Optional super-admin email for the toggle-override audit trail.")
    parser.add_argument("--apply", action="store_true", help="Execute (default is dry-run).")
    parser.add_argument("--force-before-shadow-pass", action="store_true",
                        help="Promote the mode even though shadow parity is not proven. Reads STAY on Mongo "
                             "until shadow_pass; use only with a clear operational reason.")
    args = parser.parse_args()
    result = asyncio.run(run(
        building_id=args.building_id,
        actor_user_id=args.actor_user_id,
        actor_email=args.actor_email,
        apply=args.apply,
        force_before_shadow_pass=args.force_before_shadow_pass,
    ))
    print(json.dumps(result, indent=2, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
