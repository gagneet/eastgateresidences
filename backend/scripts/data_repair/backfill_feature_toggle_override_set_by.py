"""
Backfill NULL set_by attribution on core.feature_toggle_overrides (2026-08-11)
==============================================================================
CORRECTED 2026-08-12 — the premise below is false; kept as a permanent, harmless
safety net. See "Correction" section before trusting the original narrative.

Original (2026-08-11) premise: several per-building feature-toggle override rows in
`core.feature_toggle_overrides` supposedly had `set_by = NULL` even though the cutover
audit-log narrative names the session/actor that flipped them — the go-live readiness
review reported 8 of 13 East Gate override rows with `set_by = NULL`.

## Correction (2026-08-12)

`set_by` is `UUID NOT NULL REFERENCES core.users (user_id)` — it has been NOT NULL
since the table was *created* (migration `0011_config_tables.py`, unchanged since
commit `cb86f08c`). A row with `set_by = NULL` has never been possible in this table;
there was nothing to repair and the original 8-row claim was itself wrong.

Verified root cause via direct query (2026-08-12): all 8 "NULL" rows have real,
non-null `set_by` UUIDs, both resolving to genuine `super_admin` users
(`administrator@strataos.live`, `gagneet@silverfoxtechnologies.com.au`) whose OWN
`core.users` row lives in the **platform tenant**, not East Gate's tenant. The
2026-08-11 investigation query correctly switched `app.tenant_id` to East Gate's real
tenant to read `feature_toggle_overrides` (per the RLS gotcha noted below) — but then
joined `set_by` to `core.users` under that *same* East-Gate tenant context. Because
`core.users` has strict `tenant_id = current_tenant_id()` RLS with no query-context
override for legitimately cross-tenant actors (super_admins act across every
building's tenant by design), that join silently drops any super_admin actor and
renders as "(set_by NULL)" — a display/resolution artifact, not the column's real
value. Resolving an actor UUID that may be cross-tenant requires a SEPARATE lookup
against `core.users` under the bypass sentinel (see `_resolve_actor_uuid` below, which
already does this correctly — this script itself never had the bug).

The "authoritative actor-per-change record is `core.cutover_audit_log`" claim in the
original note (further below) is also wrong: that table has no `feature_toggle`
domain at all — it exclusively logs domain cutover-state transitions (`finance_ledger`,
`identity_core`, `trust_ledger`, etc.), never per-row `feature_toggle_overrides`
attribution. There is currently no audit table that logs `set_by` history; `set_by`
itself (backed by the NOT NULL FK to `core.users`) is the only attribution record for
who last touched a given override row.

This script is kept, unmodified in behaviour, as a permanent no-op safety net: it is
correctly idempotent and will keep reporting "nothing to do" for as long as the NOT
NULL constraint holds. It is not currently doing (and never did) any repair work.

Original narrative, for provenance — they were believed written by early raw-SQL
batch scripts (the 2026-08-01 "restoration" batch and the 2026-08-02 parity-audit
batch) that supposedly inserted rows directly without an actor UUID, rather than
through the sanctioned `config_repo.upsert_feature_toggle_override()` — which resolves
a real actor (`resolve_actor_user_id(require_existing=True)` → a super_admin fallback)
or raises, and so can never itself persist a NULL. This part of the story remains true
as a description of how `upsert_feature_toggle_override()` behaves; only the "8 rows
had NULL" observation was wrong.

`is_enabled` / `reason` / `set_at` are never touched by this script.

RLS gotcha this script is careful about (see CLAUDE.md "Postgres RLS — an ad-hoc
asyncpg.connect() … returns 0 rows"): `core.feature_toggle_overrides` has a strict
`tenant_id = current_tenant_id()` policy from migration 0012 and NO bypass clause —
the `00000000-…` sentinel does NOT unlock it. Querying it under the sentinel returns
0 rows even when populated. So this script enumerates real tenants from core.schemes
(which the sentinel CAN read), then switches `app.tenant_id` to each real tenant
before reading/updating that tenant's override rows.

Idempotent: only rows with `set_by IS NULL` are touched; every run (including the
very first) finds none and mutates nothing, because the column cannot hold NULL.

Usage (from backend/):
    venv/bin/python3 scripts/data_repair/backfill_feature_toggle_override_set_by.py                       # dry-run (default)
    venv/bin/python3 scripts/data_repair/backfill_feature_toggle_override_set_by.py --apply               # write
    venv/bin/python3 scripts/data_repair/backfill_feature_toggle_override_set_by.py --apply --actor you@example.com
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

BYPASS_UUID = "00000000-0000-0000-0000-000000000000"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--apply", action="store_true", default=False,
                   help="Actually write set_by. Default is a dry-run.")
    p.add_argument("--actor", default=None,
                   help="Email of the actor to attribute the historical rows to. "
                        "Defaults to the first active super_admin (the same fallback "
                        "config_repo._fallback_actor_user_id uses).")
    return p.parse_args()


async def _resolve_actor_uuid(conn, actor_email: str | None) -> tuple[str, str]:
    """Return (user_id, describe). core.users has a bypass clause (migration 0014),
    so this read works under the sentinel context set by the caller."""
    if actor_email:
        row = await conn.fetchrow(
            "SELECT user_id::text, email::text FROM core.users "
            "WHERE email = $1 AND is_active = TRUE LIMIT 1",
            actor_email,
        )
        if row is None:
            raise SystemExit(
                f"--actor {actor_email!r} did not match an active core.users row."
            )
        return row["user_id"], f"{actor_email} (explicit --actor)"

    row = await conn.fetchrow(
        "SELECT user_id::text, email::text FROM core.users "
        "WHERE role = CAST('super_admin' AS core.user_role) AND is_active = TRUE "
        "ORDER BY created_at LIMIT 1"
    )
    if row is None:
        raise SystemExit(
            "No active super_admin found in core.users to attribute the rows to. "
            "Pass --actor <email> explicitly."
        )
    return row["user_id"], f"{row['email']} (super_admin fallback)"


async def main() -> int:
    args = parse_args()
    mode = "APPLY" if args.apply else "DRY-RUN"

    try:
        import asyncpg
    except ImportError:
        raise SystemExit("asyncpg not installed: pip install asyncpg")

    db_url = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    if not db_url:
        raise SystemExit("DATABASE_URL must be set (backend/.env).")

    conn = await asyncpg.connect(db_url)
    print(f"Backfill core.feature_toggle_overrides.set_by — mode={mode}")
    try:
        # Sentinel context: reads core.schemes / core.users (both have a bypass clause).
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS_UUID)

        actor_uuid, actor_desc = await _resolve_actor_uuid(conn, args.actor)
        print(f"  actor → {actor_desc}\n         user_id={actor_uuid}")

        tenant_ids = [
            r["tenant_id"]
            for r in await conn.fetch(
                "SELECT DISTINCT tenant_id::text AS tenant_id FROM core.schemes "
                "WHERE tenant_id IS NOT NULL"
            )
        ]
        print(f"  scanning {len(tenant_ids)} tenant(s) "
              f"(feature_toggle_overrides has NO bypass clause — per-tenant context required)\n")

        total_null = total_written = 0
        for tid in tenant_ids:
            # Switch to the real tenant so RLS exposes THIS tenant's override rows.
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tid)
            null_rows = await conn.fetch(
                "SELECT scheme_id::text AS scheme_id, feature_key "
                "FROM core.feature_toggle_overrides WHERE set_by IS NULL "
                "ORDER BY feature_key"
            )
            if not null_rows:
                continue
            total_null += len(null_rows)
            print(f"  tenant {tid}: {len(null_rows)} row(s) with set_by IS NULL")
            for r in null_rows:
                print(f"      - {r['feature_key']} (scheme {r['scheme_id']})")
            if args.apply:
                result = await conn.execute(
                    "UPDATE core.feature_toggle_overrides "
                    "SET set_by = $1 WHERE set_by IS NULL",
                    actor_uuid,
                )
                # asyncpg returns e.g. "UPDATE 5"
                written = int(result.split()[-1]) if result else 0
                total_written += written
                print(f"      → wrote set_by on {written} row(s)")

        # Reset to sentinel before leaving.
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS_UUID)

        print("\n── Summary ─────────────────────────────────────────────────────")
        if total_null == 0:
            print("  No rows with set_by IS NULL — nothing to do (already attributed).")
        elif args.apply:
            print(f"  Attributed {total_written} row(s) to {actor_desc}.")
        else:
            print(f"  {total_null} row(s) would be attributed to {actor_desc}.")
            print("  Re-run with --apply to write.")
        print("\n  Note: set_by is NOT NULL at the schema level (migration 0011) — a row with")
        print("  set_by IS NULL has never been possible. core.cutover_audit_log does NOT track")
        print("  per-row feature_toggle_overrides attribution (see module docstring, corrected")
        print("  2026-08-12); set_by itself is the only attribution record.")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
