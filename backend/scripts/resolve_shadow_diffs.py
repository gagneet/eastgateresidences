#!/usr/bin/env python3
# @featuretrace:financial_core — Building-agnostic shadow-diff bulk-resolver.
# Layer: script (cutover housekeeping)
# Data flow: core.shadow_diffs (unresolved divergence backlog) -> resolved=TRUE.
# Related: backend/scripts/finalize_identity_core_pg_write.py::_resolve_shadow_diffs
#          backend/scripts/audits/cutover_readiness_snapshot.py (§4 reports the backlog)
#          docs/architecture/go_live_readiness_2026-09-01.md §5.2 / §9
"""Bulk-resolve unresolved shadow-read divergence rows for a building.

WHY THIS EXISTS
---------------
Per the go-live readiness doc's architecture rule #2: a PG-vs-Mongo shadow diff
means the *Mongo fallback* is stale, NOT that Postgres is wrong. The remedy is to
sync PG->Mongo (`dr_mongo_snapshot.py`) and then clear the diff log — never to
"fix PG to match Mongo." This script performs the second half: marking the
existing `core.shadow_diffs` backlog resolved so it stops gating the finance
write-cutover (`finance_write_cutover_service` requires `critical_count == 0`).

Only an identity-scoped resolver existed before this
(`finalize_identity_core_pg_write._resolve_shadow_diffs`); this generalises it to
any building and any/all domains, with a dry-run default.

SAFETY
------
- DRY-RUN BY DEFAULT. It only writes when you pass `--apply`.
- Touches ONLY `core.shadow_diffs` (sets resolved / resolved_at / resolved_by /
  notes). It never edits ledger data, toggles, or `domain_cutover_status`.
- RLS: `core.shadow_diffs` carries a bypass clause, so a session-local
  `app.tenant_id = <BYPASS>` can UPDATE across the building's rows. `building_id`
  is still filtered explicitly in the WHERE clause, so this is building-scoped.

USAGE (on the live server, from the repo root)
----------------------------------------------
    cd backend
    # preview what would be resolved (no writes):
    venv/bin/python3 scripts/resolve_shadow_diffs.py --building 13195
    # resolve finance + trust for East Gate:
    venv/bin/python3 scripts/resolve_shadow_diffs.py --building 13195 \
        --domain finance_ledger --domain trust_ledger \
        --reason "PG->Mongo synced 2026-08-11; Mongo-fallback staleness cleared" --apply

Reads DATABASE_URL from backend/.env.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import uuid
from pathlib import Path

# backend/scripts/ -> parents[1] is the backend dir.
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

BYPASS_TENANT = "00000000-0000-0000-0000-000000000000"


def _asyncpg_dsn() -> str:
    dsn = os.getenv("DATABASE_URL", "")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", dsn).replace("+asyncpg", "")


async def _set_bypass(conn) -> None:
    # Parameterised set_config (SET cannot take bind params). Bypass is a constant UUID.
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS_TENANT)


async def _resolve_actor(conn, actor_email: str | None) -> str | None:
    """Best-effort resolve an actor email to a core.users UUID for resolved_by.

    Returns None (leaving resolved_by NULL) if no email is given or no match is
    found — resolved_by is a nullable column, so this is non-fatal.
    """
    if not actor_email:
        return None
    row = await conn.fetchrow(
        "SELECT id::text AS id FROM core.users WHERE lower(email) = lower($1) LIMIT 1",
        actor_email,
    )
    if not row:
        print(f"  ! actor email {actor_email!r} not found in core.users — resolved_by left NULL")
        return None
    return row["id"]


async def _backlog_by_domain(conn, building: str, domains: list[str] | None, min_score: float):
    where = ["building_id = $1", "resolved = FALSE", "COALESCE(divergence_score, 0) >= $2"]
    params: list = [building, min_score]
    if domains:
        where.append(f"domain = ANY(${len(params) + 1})")
        params.append(domains)
    sql = (
        "SELECT domain, COUNT(*) AS unresolved, "
        "       COUNT(*) FILTER (WHERE COALESCE(divergence_score,0) >= 1) AS critical, "
        "       MAX(created_at) AS last_diff "
        f"FROM core.shadow_diffs WHERE {' AND '.join(where)} "
        "GROUP BY domain ORDER BY unresolved DESC"
    )
    return await conn.fetch(sql, *params)


async def run(building: str, domains: list[str] | None, reason: str,
              apply: bool, min_score: float, actor_email: str | None) -> None:
    import asyncpg

    dsn = _asyncpg_dsn()
    if not dsn:
        print("! DATABASE_URL not set — aborting")
        sys.exit(2)

    conn = await asyncpg.connect(dsn)
    try:
        await _set_bypass(conn)

        rows = await _backlog_by_domain(conn, building, domains, min_score)
        scope = ", ".join(domains) if domains else "ALL domains"
        print(f"Shadow-diff backlog for building={building}  ({scope}, min_score={min_score})")
        print("=" * 74)
        if not rows:
            print("  (no unresolved diffs match — nothing to do)")
            return
        total = 0
        for r in rows:
            d = dict(r)
            total += d["unresolved"]
            print(f"  {str(d['domain']):<26} unresolved={d['unresolved']:<6} "
                  f"critical={d['critical']:<6} last={d['last_diff']}")
        print(f"  --> TOTAL unresolved to resolve: {total}")

        if not apply:
            print("\nDRY-RUN — no rows changed. Re-run with --apply --reason \"...\" to resolve.")
            return

        if not reason:
            print("\n! --reason is required with --apply (it is written to notes for the audit trail)")
            sys.exit(2)

        resolved_by = await _resolve_actor(conn, actor_email)

        where = ["building_id = $1", "resolved = FALSE", "COALESCE(divergence_score, 0) >= $2"]
        params: list = [building, min_score]
        if domains:
            where.append(f"domain = ANY(${len(params) + 1})")
            params.append(domains)
        params.append(reason)
        reason_idx = len(params)
        # resolved_by cast: pass text, cast to uuid in SQL; NULL stays NULL.
        params.append(resolved_by)
        by_idx = len(params)

        async with conn.transaction():
            result = await conn.execute(
                f"""
                UPDATE core.shadow_diffs
                SET resolved = TRUE,
                    resolved_at = NOW(),
                    resolved_by = ${by_idx}::uuid,
                    notes = ${reason_idx}
                WHERE {' AND '.join(where)}
                """,
                *params,
            )
        n = int(result.split()[-1])
        print(f"\n✓ Resolved {n} shadow-diff row(s). "
              f"resolved_by={resolved_by or 'NULL'}  reason={reason!r}")
        print("  Verify with: scripts/audits/cutover_readiness_snapshot.py --building "
              f"{building}  (§4 should now show 0 unresolved for these domains)")
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Building-agnostic bulk-resolver for core.shadow_diffs (dry-run by default).")
    ap.add_argument("--building", default="13195",
                    help="building_id / scheme_number (default: 13195 East Gate)")
    ap.add_argument("--domain", action="append", dest="domains", default=None,
                    help="Domain to resolve (repeatable). Omit to resolve ALL domains. "
                         "e.g. --domain finance_ledger --domain trust_ledger")
    ap.add_argument("--min-score", type=float, default=0.0,
                    help="Only resolve diffs with divergence_score >= this (default 0.0 = all "
                         "unresolved; use 1.0 to target only 'critical' rows).")
    ap.add_argument("--reason", default="",
                    help="Written to shadow_diffs.notes (required with --apply).")
    ap.add_argument("--actor-email", default=None,
                    help="Optional super_admin email → recorded as resolved_by (else NULL).")
    ap.add_argument("--apply", action="store_true", help="Actually write (default: dry-run).")
    args = ap.parse_args()
    asyncio.run(run(args.building, args.domains, args.reason,
                    args.apply, args.min_score, args.actor_email))


if __name__ == "__main__":
    main()
