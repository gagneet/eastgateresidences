#!/usr/bin/env python3
# @featuretrace:financial_core — Per-building finance PG-cutover toggle flip helper.
# Layer: script (cutover operator action)
# Data flow: config_repo.upsert_feature_toggle_override -> core.feature_toggle_overrides
#            -> finance_route_cutover_service / finance_write_cutover_service runtime gates.
# Related: backend/db_postgres/repos/config_repo.py::upsert_feature_toggle_override
#          backend/services/finance_write_cutover_service.py::get_finance_write_readiness_table
#          docs/architecture/go_live_readiness_2026-09-01.md §3d / §5.4 / §9
"""Flip finance PostgreSQL read+write runtime toggles for ONE building, together.

WHY THIS EXISTS
---------------
The go-live readiness doc (§5.4) says the sanctioned finance cutover action is to
enable `financial_pg_reads_enabled` AND `financial_pg_writes_enabled` as a
**per-building override**, TOGETHER. Flipping reads alone splits the stores (PG
reads would not see new Mongo writes); the write-cutover code gate additionally
refuses to serve PG writes while critical shadow diffs remain (§3d).

These are `data_source_primary` / `finance_write` PROTECTED toggles — they must
NEVER be enabled globally (`config_repo.assert_global_enable_allowed` raises).
A per-building override is the ONLY sanctioned path, and
`upsert_feature_toggle_override` deliberately allows it (with a warning log).

WHAT THIS DOES
--------------
1. Prints the current resolved value of each toggle for the building.
2. Prints the live finance WRITE readiness table (so you see whether the runtime
   gate will actually engage PG writes, or is still blocked — e.g. by critical
   shadow diffs). Run `resolve_shadow_diffs.py --apply` first if it is blocked.
3. On `--apply`, flips BOTH toggles together via the repo API (never one alone,
   unless you pass `--force-single`, which is intentionally awkward).

SAFETY
------
- DRY-RUN BY DEFAULT — only writes with `--apply`.
- Refuses to flip a single toggle (reads without writes, or vice-versa) unless
  `--force-single` is given, to prevent a read/write store split.
- `--enable` requires a real `--actor-email` (an existing super_admin), because
  the override is attributed to a `set_by` user and the repo requires it to exist.

USAGE (on the live server, from the repo root)
----------------------------------------------
    cd backend
    # preview current state + write-gate readiness (no writes):
    venv/bin/python3 scripts/set_pg_cutover_toggles.py --building 13195
    # flip finance reads+writes ON for East Gate (after diffs resolved):
    venv/bin/python3 scripts/set_pg_cutover_toggles.py --building 13195 --enable \
        --actor-email sa@example.com \
        --reason "Finance cutover per go-live readiness §5.4; diffs resolved, DR synced" --apply
    # roll back:
    venv/bin/python3 scripts/set_pg_cutover_toggles.py --building 13195 --disable \
        --actor-email sa@example.com --reason "Rollback finance cutover" --apply

Reads DATABASE_URL from backend/.env.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

# The two finance runtime toggles, flipped as a pair.
READ_KEY = "financial_pg_reads_enabled"
WRITE_KEY = "financial_pg_writes_enabled"
FINANCE_KEYS = (READ_KEY, WRITE_KEY)


async def _print_current(building: str) -> None:
    from db_postgres.repos.config_repo import get_resolved_feature_toggle
    from core.toggle_classification import get_toggle_class

    print(f"Current finance PG-cutover toggles for building={building}")
    print("=" * 74)
    for key in FINANCE_KEYS:
        resolved = await get_resolved_feature_toggle(key, building)
        try:
            cls = get_toggle_class(key).value
        except Exception:
            cls = "?"
        if resolved is None:
            print(f"  {key:<32} (no catalogue row!) class={cls}")
        else:
            print(f"  {key:<32} enabled={str(resolved.get('is_enabled')):<6} class={cls}")


async def _print_write_readiness(building: str) -> None:
    print("\nFinance WRITE-cutover runtime readiness (does the code gate allow PG writes?)")
    print("-" * 74)
    try:
        from services.finance_write_cutover_service import get_finance_write_readiness_table
        table = await get_finance_write_readiness_table(building_id=building)
    except Exception as e:
        print(f"  ! could not evaluate write readiness: {type(e).__name__}: {str(e)[:160]}")
        return
    if not table:
        print("  (no write routes reported)")
        return
    any_blocked = False
    for row in table:
        rk = row.get("route_key", "?")
        src = row.get("source", "?")
        blocked = row.get("blocked_reason")
        if blocked:
            any_blocked = True
            print(f"  {rk:<40} source={src:<9} BLOCKED: {blocked}")
        else:
            print(f"  {rk:<40} source={src:<9} eligible ✓")
    if any_blocked:
        print("\n  NOTE: routes above are BLOCKED by the runtime gate. Enabling the toggles is")
        print("  safe but PG writes will NOT engage until the blockers clear (commonly:")
        print("  critical shadow diffs -> run resolve_shadow_diffs.py --apply first).")


async def _flip(building: str, keys: tuple[str, ...], enable: bool,
                actor_email: str, reason: str) -> None:
    from db_postgres.repos.config_repo import upsert_feature_toggle_override

    for key in keys:
        result = await upsert_feature_toggle_override(
            building_id=building,
            feature_key=key,
            is_enabled=enable,
            actor_email=actor_email,
            reason=reason,
        )
        print(f"  ✓ {key:<32} -> enabled={result.get('is_enabled')} "
              f"(set_by={actor_email})")


async def run(building: str, enable: bool | None, actor_email: str | None,
              reason: str, apply: bool, force_single: bool, keys: tuple[str, ...]) -> None:
    await _print_current(building)
    await _print_write_readiness(building)

    if enable is None:
        print("\nDRY-RUN (no action selected). Pass --enable or --disable with --apply to flip.")
        return

    # Guard the read/write split unless explicitly overridden.
    flipping_pair = set(keys) == set(FINANCE_KEYS)
    if not flipping_pair and not force_single:
        print(f"\n! Refusing to flip only {list(keys)} — reads and writes must move together "
              "to avoid a store split.\n  Pass --force-single ONLY if you understand the "
              "consequences (§3d of the readiness doc).")
        sys.exit(2)

    if not apply:
        verb = "ENABLE" if enable else "DISABLE"
        print(f"\nDRY-RUN — would {verb} {list(keys)} for building={building}.")
        print("  Re-run with --apply (and --actor-email / --reason) to write.")
        return

    if not reason:
        print("\n! --reason is required with --apply (recorded on the override for audit).")
        sys.exit(2)
    if enable and not actor_email:
        print("\n! --actor-email (an existing super_admin) is required to ENABLE a protected "
              "toggle — the override is attributed to that user.")
        sys.exit(2)

    verb = "Enabling" if enable else "Disabling"
    print(f"\n{verb} {list(keys)} for building={building} ...")
    await _flip(building, keys, enable, actor_email or "", reason)

    print("\nDone. Re-checking resolved state:")
    await _print_current(building)
    print("\nNext: verify per-route current_source flips to PostgreSQL "
          "(scripts/audits/cutover_readiness_snapshot.py or the Control-Plane page), "
          "then monitor.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Flip finance PG read+write toggles per-building, together (dry-run by default).")
    ap.add_argument("--building", default="13195",
                    help="building_id / scheme_number (default: 13195 East Gate)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--enable", action="store_true", help="Enable the toggles (finance -> PG).")
    mode.add_argument("--disable", action="store_true", help="Disable the toggles (rollback -> Mongo).")
    ap.add_argument("--actor-email", default=None,
                    help="Existing super_admin email recorded as set_by (required to --enable).")
    ap.add_argument("--reason", default="", help="Audit reason (required with --apply).")
    ap.add_argument("--only", choices=list(FINANCE_KEYS), default=None,
                    help="Restrict to a single key (requires --force-single).")
    ap.add_argument("--force-single", action="store_true",
                    help="Permit flipping only ONE toggle (dangerous: risks a read/write split).")
    ap.add_argument("--apply", action="store_true", help="Actually write (default: dry-run).")
    args = ap.parse_args()

    enable: bool | None = None
    if args.enable:
        enable = True
    elif args.disable:
        enable = False

    keys = (args.only,) if args.only else FINANCE_KEYS
    asyncio.run(run(args.building, enable, args.actor_email, args.reason,
                    args.apply, args.force_single, keys))


if __name__ == "__main__":
    main()
