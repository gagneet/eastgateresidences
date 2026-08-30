"""
Disable drifted global feature toggles left is_enabled=True in live stores (2026-08-06)
========================================================================================
PR #583 aligned the SEED defaults (`backend/seeds/feature_toggles.py`) with the
feature-registry governance gate by flipping eight global toggles to
`is_enabled: False`:

  - 6 Powerhouse shell toggles (violated the non-exceptable
    `check_shell_not_globally_enabled` gate + feature-toggle-governance.md §1):
        powerhouse_conversations, powerhouse_shared_inbox, powerhouse_email_intake,
        powerhouse_ai_summary, powerhouse_workflow_engine, powerhouse_automation_rules
  - outbound_payment_initiation_enabled (generates real ABA payment files; its own
    note says keep disabled until the payment-initiation workflow is validated)
  - pm_engagement_letter (no backend endpoint exists yet — frontend template only)

But `seed_feature_toggles()` deliberately never overwrites `is_enabled` on an
existing row, so the LIVE global rows (Postgres `core.feature_toggles` and Mongo
`feature_toggles` where `building_id IS NULL`) are still `True`. Until they are
flipped, AUDIT-13 (`scripts/audits/toggle_drift.py`) reports seed/live drift for
these eight keys, and the Powerhouse shells remain globally enabled in production.

This script reconciles both live stores to the seed. Disabling is the fail-safe
direction — `assert_global_enable_allowed()` only ever blocks `is_enabled=True`,
so no protected-enable escape hatch is needed here. Postgres uses the sanctioned
`config_repo.update_global_feature_toggle()` (never raw SQL, RLS handled inside
the repo/session layer); Mongo mirrors the seed's own direct global-row access
(`building_id=None`).

Idempotent: only rows currently `is_enabled=True` are touched; a second run
reports "already False" for every key and mutates nothing.

Per-building overrides in `core.feature_toggle_overrides` are NOT touched — a
building that has legitimately promoted a Powerhouse sub-feature keeps its
override; this only corrects the GLOBAL default rows.

Usage (from backend/):
    venv/bin/python3 scripts/data_repair/disable_drifted_global_toggles_20260806.py               # dry-run (default)
    venv/bin/python3 scripts/data_repair/disable_drifted_global_toggles_20260806.py --apply        # apply to both stores
    venv/bin/python3 scripts/data_repair/disable_drifted_global_toggles_20260806.py --apply --skip-mongo   # Postgres only
    venv/bin/python3 scripts/data_repair/disable_drifted_global_toggles_20260806.py --apply --skip-postgres # Mongo only
    venv/bin/python3 scripts/data_repair/disable_drifted_global_toggles_20260806.py --apply --actor you@example.com
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

# The eight global toggles flipped to is_enabled=False in the seed by PR #583.
# Kept as a literal list (not imported from the seed) so this repair script is a
# self-contained, auditable record of exactly what it changed on 2026-08-06.
KEYS: tuple[str, ...] = (
    "powerhouse_conversations",
    "powerhouse_shared_inbox",
    "powerhouse_email_intake",
    "powerhouse_ai_summary",
    "powerhouse_workflow_engine",
    "powerhouse_automation_rules",
    "outbound_payment_initiation_enabled",
    "pm_engagement_letter",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--apply", action="store_true", default=False,
                   help="Actually write the change. Default is a dry-run.")
    p.add_argument("--skip-postgres", action="store_true", default=False,
                   help="Do not touch Postgres core.feature_toggles.")
    p.add_argument("--skip-mongo", action="store_true", default=False,
                   help="Do not touch Mongo feature_toggles (building_id=None).")
    p.add_argument("--actor", default=None,
                   help="Actor email recorded as updated_by on the Postgres rows (optional).")
    return p.parse_args()


async def reconcile_postgres(apply: bool, actor: str | None) -> tuple[int, int]:
    """Return (changed, already_false). Missing keys are reported and skipped."""
    from db_postgres.repos.config_repo import (
        get_global_feature_toggle,
        update_global_feature_toggle,
    )

    changed = already = 0
    print("\n── Postgres (core.feature_toggles) ─────────────────────────────")
    for key in KEYS:
        try:
            current = await get_global_feature_toggle(key)
            if current is None:
                print(f"  {key}: NOT FOUND — skipping")
                continue
            if not current["is_enabled"]:
                print(f"  {key}: already is_enabled=False — nothing to do")
                already += 1
                continue
            if not apply:
                print(f"  {key}: is_enabled=True  →  DRY-RUN would set False")
                changed += 1
                continue
            result = await update_global_feature_toggle(
                key, {"is_enabled": False}, actor_email=actor,
            )
            print(f"  {key}: is_enabled=True  →  set {result['is_enabled']}")
            changed += 1
        except Exception as e:  # noqa: BLE001 — isolate per key so one bad key never blocks the rest
            print(f"  {key}: ERROR — {e}")
    return changed, already


async def reconcile_mongo(apply: bool) -> tuple[int, int]:
    """Return (changed, already_false) for global (building_id=None) Mongo rows."""
    from pymongo import AsyncMongoClient

    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        raise SystemExit(
            "MONGO_URL and DB_NAME must be set (backend/.env) to reconcile Mongo. "
            "Pass --skip-mongo to reconcile Postgres only."
        )

    client = AsyncMongoClient(mongo_url)
    db = client[db_name]
    changed = already = 0
    print("\n── Mongo (feature_toggles, building_id=None) ───────────────────")
    try:
        for key in KEYS:
            try:
                doc = await db.feature_toggles.find_one(
                    {"feature_key": key, "building_id": None},
                    {"is_enabled": 1, "_id": 0},
                )
                if doc is None:
                    print(f"  {key}: NOT FOUND — skipping")
                    continue
                if not doc.get("is_enabled", False):
                    print(f"  {key}: already is_enabled=False — nothing to do")
                    already += 1
                    continue
                if not apply:
                    print(f"  {key}: is_enabled=True  →  DRY-RUN would set False")
                    changed += 1
                    continue
                res = await db.feature_toggles.update_one(
                    {"feature_key": key, "building_id": None, "is_enabled": True},
                    {"$set": {"is_enabled": False, "updated_at": datetime.now(timezone.utc)}},
                )
                if res.modified_count == 0:
                    # Raced: another writer flipped it between the read and this update.
                    # Don't count it as our change — keeps the summary honest.
                    print(f"  {key}: was True at read but modified=0 (concurrent update) — not counted")
                    continue
                print(f"  {key}: is_enabled=True  →  set False (modified={res.modified_count})")
                changed += 1
            except Exception as e:  # noqa: BLE001 — isolate per key so one bad key never blocks the rest
                print(f"  {key}: ERROR — {e}")
    finally:
        await client.close()
    return changed, already


async def main() -> int:
    args = parse_args()
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Reconcile drifted global toggles — mode={mode} — {len(KEYS)} keys")

    pg_changed = pg_already = mo_changed = mo_already = 0
    if not args.skip_postgres:
        pg_changed, pg_already = await reconcile_postgres(args.apply, args.actor)
    if not args.skip_mongo:
        mo_changed, mo_already = await reconcile_mongo(args.apply)

    verb = "changed" if args.apply else "would change"
    print("\n── Summary ─────────────────────────────────────────────────────")
    print(f"  Postgres: {pg_changed} {verb}, {pg_already} already False")
    print(f"  Mongo:    {mo_changed} {verb}, {mo_already} already False")
    if not args.apply and (pg_changed or mo_changed):
        print("\n  Re-run with --apply to write the change.")
    print("\n  Verify after apply:")
    print("    python3 scripts/validation/audit_feature_registry.py   # expect PASS")
    print("    python3 scripts/audits/toggle_drift.py                  # expect no drift for these keys")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
