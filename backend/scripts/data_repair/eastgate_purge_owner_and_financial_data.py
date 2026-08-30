# @featuretrace:east-gate-data-restore — Reversible removal of East Gate owner/financial data.
# Layer: script
# Data flow: verified backup dir (refuses without one) -> scoped DELETE across MongoDB + PostgreSQL (building-scoped).
# Related: backend/scripts/data_repair/eastgate_export_restore.py
#          docs/guides/eastgate_data_purge_and_restore_2026-08-21.md
"""Remove East Gate (13195) owner, financial and transactional data from both stores.

Reversible by design: every row removed here is present in the verified export produced
by the 2026-08-21 dump, and restorable with
`scripts/data_repair/eastgate_export_restore.py --apply --dir <BACKUP_DIR>`. The script
REFUSES to run unless a verified backup directory is supplied.

Scope (owner + financial + transactional). The building itself SURVIVES as an empty
shell — buildings, settings, feature toggles and documents are deliberately kept so the
platform still boots and East Gate still exists to re-enter data into.

    python3 scripts/data_repair/eastgate_purge_owner_and_financial_data.py --dir <BACKUP> --dry-run
    python3 scripts/data_repair/eastgate_purge_owner_and_financial_data.py --dir <BACKUP> --apply

super_admin accounts are NEVER deleted
--------------------------------------
The platform's only real super_admin (gagneet@silverfoxtechnologies.com.au) carries
building_id="13195". Deleting every user scoped to the building would have removed the
sole account able to administer the platform and locked the operator out with no way back
in — the restore itself requires database access, not application access. That account is
excluded unconditionally, not by a list of emails but by role, so the protection holds if
the account changes.

The Postgres General Ledger
---------------------------
finance.journal_lines carries an immutability trigger (prevent_posted_line_mutation)
rejecting DELETE on anything belonging to a POSTED entry, and strataos_user is not a
superuser, so session_replication_role cannot switch it off. Those tables are listed in
GL_TABLES and are skipped unless --include-gl is passed, which is only usable once an
operator has disabled the triggers as the postgres superuser.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT / ".env")

BUILDING = "13195"

# Kept so the platform still functions and the building remains a re-entry point.
MONGO_KEEP = {
    "buildings", "settings", "site_settings", "feature_toggles", "documents",
    "levy_reminder_settings", "unit_display_config",
}

# Roles that must survive: without a super_admin nobody can administer the platform,
# and the restore path needs database access rather than application access.
PROTECTED_ROLES = {"super_admin"}

# Postgres tables holding the posted General Ledger. Blocked by immutability triggers
# unless an operator has disabled them as the postgres superuser.
GL_TABLES = {
    "finance.journal_lines",
    "finance.journal_entries",
    "finance.gl_accounts",
}


def _mongo_filter():
    return {"$or": [{"building_id": BUILDING}, {"plan_id": BUILDING}]}


async def purge_mongo(apply: bool) -> tuple[int, int]:
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    removed = kept = 0
    try:
        for name in sorted(await db.list_collection_names()):
            if name in MONGO_KEEP:
                n = await db[name].count_documents(_mongo_filter())
                if n:
                    print(f"  KEEP  {name:<40} {n}")
                    kept += n
                continue

            if name == "users":
                # Role-based exclusion, not an email allowlist.
                q = {**_mongo_filter(), "role": {"$nin": list(PROTECTED_ROLES)}}
                protected = await db.users.count_documents(
                    {**_mongo_filter(), "role": {"$in": list(PROTECTED_ROLES)}})
                if protected:
                    print(f"  KEEP  {'users (protected roles)':<40} {protected}")
                    kept += protected
            else:
                q = _mongo_filter()

            n = await db[name].count_documents(q)
            if not n:
                continue
            if apply:
                res = await db[name].delete_many(q)
                n = res.deleted_count
            print(f"  {'DEL ' if apply else 'would delete'} {name:<40} {n}")
            removed += n
    finally:
        cli.close()
    return removed, kept


async def purge_postgres(apply: bool, include_gl: bool) -> tuple[int, list[str]]:
    con = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    removed = 0
    blocked: list[str] = []
    try:
        bypass = "00000000-0000-0000-0000-000000000000"
        await con.execute(f"SET app.tenant_id = '{bypass}'")
        tid = await con.fetchval(
            "SELECT tenant_id::text FROM core.schemes WHERE scheme_number=$1", BUILDING)
        if not tid:
            print("  East Gate scheme not found in Postgres")
            return 0, []

        # Real tenant context: core.lots, core.parties and every finance.* table have no
        # RLS bypass clause, so a DELETE under the sentinel matches ZERO rows and reports
        # success (CLAUDE.md footgun #7).
        await con.execute(f"SET app.tenant_id = '{tid}'")

        candidates = {
            f"{r[0]}.{r[1]}" for r in await con.fetch("""
                SELECT table_schema, table_name FROM information_schema.columns
                WHERE column_name='tenant_id'
                  AND table_schema NOT IN ('pg_catalog','information_schema')
                  AND NOT (table_schema='core' AND table_name IN ('tenants','schemes'))
            """)
        }
        if not include_gl:
            candidates -= GL_TABLES

        last_error: dict[str, str] = {}
        while candidates:
            progressed = False
            for fq in sorted(candidates):
                n = await con.fetchval(f"SELECT count(*) FROM {fq} WHERE tenant_id=$1", tid)  # noqa: S608
                if not n:
                    candidates.discard(fq); progressed = True; continue
                # Same role protection as MongoDB. There is no super_admin in this
                # tenant today (they live in the platform tenant), but a future run
                # against a differently-shaped tenant must not be able to delete the
                # only account capable of administering the platform.
                role_guard = ""
                if fq == "core.users":
                    roles = "','".join(sorted(PROTECTED_ROLES))
                    role_guard = f" AND role NOT IN ('{roles}')"
                    n = await con.fetchval(
                        f"SELECT count(*) FROM {fq} WHERE tenant_id=$1{role_guard}", tid)  # noqa: S608
                    if not n:
                        candidates.discard(fq); progressed = True; continue

                if not apply:
                    print(f"  would delete pg {fq:<44} {n}")
                    removed += n; candidates.discard(fq); progressed = True; continue
                try:
                    async with con.transaction():
                        await con.execute(
                            f"DELETE FROM {fq} WHERE tenant_id=$1{role_guard}", tid)  # noqa: S608
                except Exception as exc:
                    last_error[fq] = str(exc)[:180]
                    continue  # dependency not cleared yet — retry next pass
                print(f"  DEL  pg {fq:<44} {n}")
                removed += n
                candidates.discard(fq)
                progressed = True
            if not progressed:
                for fq in sorted(candidates):
                    n = await con.fetchval(f"SELECT count(*) FROM {fq} WHERE tenant_id=$1", tid)  # noqa: S608
                    if n:
                        blocked.append(f"{fq} ({n} rows): {last_error.get(fq,'unknown')}")
                break
    finally:
        await con.close()
    return removed, blocked


async def main(args) -> int:
    backup = Path(args.dir)
    for store in ("mongo", "postgres"):
        if not (backup / store / "MANIFEST.json").exists():
            print(f"REFUSING: {backup}/{store}/MANIFEST.json missing — no verified backup.")
            return 2
    man_m = json.loads((backup / "mongo" / "MANIFEST.json").read_text())
    man_p = json.loads((backup / "postgres" / "MANIFEST.json").read_text())
    print(f"Backup: {backup}")
    print(f"  mongo    {man_m['total_documents']} documents")
    print(f"  postgres {man_p['total_rows']} rows")
    print(f"\nMode: {'APPLY' if args.apply else 'DRY-RUN'}   include_gl={args.include_gl}\n")

    print("=== MongoDB ===")
    m_removed, m_kept = await purge_mongo(args.apply)
    print("\n=== PostgreSQL ===")
    p_removed, blocked = await purge_postgres(args.apply, args.include_gl)

    print(f"\nMongoDB   removed={m_removed}  kept={m_kept}")
    print(f"PostgreSQL removed={p_removed}")
    if blocked:
        print("\nBLOCKED (needs the superuser trigger step):")
        for b in blocked:
            print(f"  {b}")
    print(f"\nRestore with:\n  python3 scripts/data_repair/eastgate_export_restore.py --apply --dir {backup}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, help="Verified backup directory")
    ap.add_argument("--include-gl", action="store_true",
                    help="Also delete the posted GL (requires triggers disabled by a superuser)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args())))
