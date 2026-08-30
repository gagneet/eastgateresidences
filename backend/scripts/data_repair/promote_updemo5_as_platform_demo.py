"""Retire DEMO-0001 and make UPDEMO5 the platform's single demo building.

Background
----------
Two demo schemes existed for different reasons: DEMO-0001 ("StrataOS Demo Tower") was a
minimal Postgres-only platform shell that the startup bootstrap created so the
super-admin building switcher was never empty, and UPDEMO5 ("StrataOS Demo Residences",
formerly UP-DEMO-001) is the full sales/onboarding demo with 14 lots, owners, two years
of levies and GL accounts. UPDEMO5 was deliberately written with is_demo=FALSE so it did
not collide with the Tower's singleton invariant.

Only one demo is wanted now, and it should be the useful one.

The singleton invariant
-----------------------
Migration 0024 created two UNIQUE PARTIAL indexes:

    schemes_one_demo_idx  ON core.schemes (is_demo) WHERE is_demo = TRUE
    tenants_one_demo_idx  ON core.tenants (is_demo) WHERE is_demo = TRUE

At most one scheme and one tenant may carry is_demo = TRUE. The order below is therefore
not cosmetic: DEMO-0001 must be removed before UPDEMO5 can take the flag, or the UPDATE
violates the index.

    python3 scripts/data_repair/promote_updemo5_as_platform_demo.py --dry-run
    python3 scripts/data_repair/promote_updemo5_as_platform_demo.py --apply
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT / ".env")

BYPASS = "00000000-0000-0000-0000-000000000000"
RETIRE = "DEMO-0001"
PROMOTE = "UPDEMO5"

# Tenant-scoped tables are DISCOVERED, not hardcoded. A fixed list missed
# finance.gl_accounts, which references the scheme and blocked its deletion — the same
# mistake the demo tear-down made before it was rewritten. Rows are cleared in repeated
# passes so foreign keys dictate the order themselves.
#
# All of it runs under the tenant's OWN uuid: core.lots, core.parties,
# core.ownership_periods and every finance.* table have NO RLS bypass clause, so under
# the sentinel a DELETE matches zero rows and reports success (CLAUDE.md footgun #7).


async def _purge_tenant_rows(con, tenant_id: str, apply: bool) -> int:
    await con.execute(f"SET app.tenant_id = '{tenant_id}'")
    remaining = {
        f"{r[0]}.{r[1]}"
        for r in await con.fetch("""
            SELECT table_schema, table_name FROM information_schema.columns
            WHERE column_name='tenant_id'
              AND table_schema NOT IN ('pg_catalog','information_schema')
              AND NOT (table_schema='core' AND table_name IN ('tenants','schemes'))
        """)
    }
    total = 0
    last_error: dict[str, str] = {}
    while remaining:
        progressed = False
        for fq in sorted(remaining):
            n = await con.fetchval(
                f"SELECT count(*) FROM {fq} WHERE tenant_id=$1::uuid", tenant_id)  # noqa: S608
            if not n:
                remaining.discard(fq); progressed = True; continue
            if not apply:
                print(f"  would delete {fq:<38} {n}")
                total += n; remaining.discard(fq); progressed = True; continue
            try:
                async with con.transaction():
                    await con.execute(
                        f"DELETE FROM {fq} WHERE tenant_id=$1::uuid", tenant_id)  # noqa: S608
            except Exception as exc:
                last_error[fq] = str(exc)[:160]
                continue  # dependency not cleared yet — retry next pass
            print(f"  deleted {fq:<38} {n}")
            total += n
            remaining.discard(fq)
            progressed = True
        if not progressed:
            for fq in sorted(remaining):
                n = await con.fetchval(
                    f"SELECT count(*) FROM {fq} WHERE tenant_id=$1::uuid", tenant_id)  # noqa: S608
                if n:
                    print(f"  BLOCKED {fq} ({n} rows): {last_error.get(fq,'unknown')}")
            break
    return total


async def main(apply: bool) -> int:
    con = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    try:
        await con.execute(f"SET app.tenant_id = '{BYPASS}'")
        retire = await con.fetchrow(
            "SELECT scheme_id::text s, tenant_id::text t FROM core.schemes WHERE scheme_number=$1", RETIRE)
        promote = await con.fetchrow(
            "SELECT scheme_id::text s, tenant_id::text t FROM core.schemes WHERE scheme_number=$1", PROMOTE)

        if not promote:
            print(f"ABORT: {PROMOTE} does not exist — refusing to retire the only demo.")
            return 2
        print(f"{PROMOTE} present: scheme={promote['s']}")

        if retire:
            print(f"{RETIRE} present: scheme={retire['s']} tenant={retire['t']}")
            n = await _purge_tenant_rows(con, retire["t"], apply)
            if apply:
                await con.execute(f"SET app.tenant_id = '{BYPASS}'")
                await con.execute("DELETE FROM core.schemes WHERE scheme_id=$1::uuid", retire["s"])
                await con.execute("DELETE FROM core.tenants WHERE tenant_id=$1::uuid", retire["t"])
                print(f"  {RETIRE} removed ({n} child rows + scheme + tenant)")
        else:
            print(f"{RETIRE} already absent")

        if not apply:
            print(f"\n--dry-run: would then set is_demo=TRUE on {PROMOTE} and its tenant.")
            return 0

        # Only now is the singleton slot free.
        await con.execute(f"SET app.tenant_id = '{BYPASS}'")
        await con.execute("UPDATE core.tenants SET is_demo=TRUE WHERE tenant_id=$1::uuid", promote["t"])
        await con.execute("UPDATE core.schemes SET is_demo=TRUE WHERE scheme_id=$1::uuid", promote["s"])
        print(f"  {PROMOTE} and its tenant marked is_demo=TRUE")

        print("\n=== resulting demo chain ===")
        for r in await con.fetch("""
            SELECT s.scheme_number, s.scheme_name, s.is_demo AS s_demo, s.status,
                   t.tenant_name, t.is_demo AS t_demo
            FROM core.schemes s JOIN core.tenants t ON t.tenant_id=s.tenant_id
            ORDER BY s.scheme_number"""):
            mark = "  <-- platform demo" if r["s_demo"] else ""
            print(f"  {r['scheme_number']:<12} {r['scheme_name'][:30]:<32} "
                  f"scheme_demo={r['s_demo']} tenant_demo={r['t_demo']} status={r['status']}{mark}")
        return 0
    finally:
        await con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.apply)))
