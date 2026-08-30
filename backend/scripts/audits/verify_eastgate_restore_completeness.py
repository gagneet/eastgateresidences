# @featuretrace:east-gate-data-restore — Read-only completeness audit of the 2026-08-21 restore.
# Layer: script
# Data flow: backup MANIFEST.json + *.json.gz -> per-record presence check -> MongoDB + PostgreSQL (read-only).
# Related: backend/scripts/data_repair/eastgate_export_restore.py
#          docs/guides/eastgate_data_purge_and_restore_2026-08-21.md
#          docs/fixes/eastgate_restore_completeness_audit_2026-08-28.md
"""Read-only restore audit: for every record in the 2026-08-21 East Gate export,
check whether it is present in the live store. Makes NO writes.

    backend/venv/bin/python3 scripts/audits/verify_eastgate_restore_completeness.py

Why per-record and not per-count
--------------------------------
Comparing manifest counts against live counts cannot distinguish "restored" from
"deleted and regenerated with fresh ids" — several tables here are the latter. This
checks each exported primary key / _id for actual presence.

Two traps this script exists to document, both hit while writing it:

* The Postgres export encodes typed scalars as {"__t__": "uuid", "v": "..."} — comparing
  the raw wrapper stringifies the dict and matches NOTHING, reporting every table as
  100% missing. Use unwrap().
* Postgres rows are read under the tenant's OWN uuid, never the bypass sentinel: most
  tables here (core.lots, core.parties, every finance.*) have no RLS bypass clause and
  return a silent count of 0 under the sentinel (CLAUDE.md footgun #8). When a count
  looks like zero, check pg_stat_user_tables.n_live_tup before believing it.

Reading the output
------------------
A row reported "missing" means THIS EXPORTED RECORD is absent by primary key. It does
NOT mean the data is gone. Three different situations produce the same line, and they
have to be separated by hand before drawing any conclusion:

  1. Regenerated with fresh ids  — same content, different keys. Confirm by comparing a
     natural key instead (core.feature_toggle_overrides on (feature_key, is_enabled);
     capital_replacement_schedule on (asset_name, replacement_year)). Both were verified
     equivalent on 2026-08-28 despite reporting 100% missing here.
  2. Deliberately not restored   — see the "Deliberately NOT restored" section of
     docs/guides/eastgate_data_purge_and_restore_2026-08-21.md BEFORE calling anything a
     gap. core.outbox, core.domain_cutover_status and the high-volume logs are all listed
     there as intentional.
  3. Actually lost.

Only case 3 is a finding. Conflating 1 with 3 overstates loss; conflating 2 with 3
reports a documented decision as a bug. Both mistakes were made on the first run of this
script — see docs/fixes/eastgate_restore_completeness_audit_2026-08-28.md.
"""
import asyncio, gzip, json, os
from pathlib import Path
import asyncpg
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from bson import json_util

def unwrap(v):
    """The exporter encodes typed scalars as {"__t__": <type>, "v": <value>}."""
    if isinstance(v, dict) and "__t__" in v and "v" in v:
        return v["v"]
    return v

ROOT = Path("/home/gagneet/strata-management/backend")
load_dotenv(ROOT / ".env")
BACKUP = Path("/home/gagneet/_archive/strataos-backups/eastgate-13195-20260821T115020Z")

async def mongo_audit():
    man = json.loads((BACKUP / "mongo" / "MANIFEST.json").read_text())
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    rows = []
    try:
        for name, meta in sorted(man["collections"].items()):
            docs = json_util.loads(gzip.open(BACKUP / "mongo" / f"{name}.json.gz", "rb").read())
            ids = [d["_id"] for d in docs if d.get("_id") is not None]
            present = 0
            CH = 2000
            for i in range(0, len(ids), CH):
                present += await db[name].count_documents({"_id": {"$in": ids[i:i+CH]}})
            rows.append((name, len(docs), present))
    finally:
        cli.close()
    return rows

async def pg_audit():
    man = json.loads((BACKUP / "postgres" / "MANIFEST.json").read_text())
    tid = man["tenant_id"]
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    con = await asyncpg.connect(dsn)
    rows = []
    try:
        await con.execute(f"SET app.tenant_id = '{tid}'")
        for fq, meta in sorted(man["tables"].items()):
            schema, table = fq.split(".", 1)
            data = json.loads(gzip.open(BACKUP / "postgres" / f"{fq}.json.gz", "rb").read())
            if not data:
                rows.append((fq, 0, 0, "empty")); continue
            pk = await con.fetch("""
                SELECT a.attname FROM pg_index i
                JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey)
                WHERE i.indrelid=$1::regclass AND i.indisprimary""", fq)
            pkcols = [r["attname"] for r in pk]
            if not pkcols:
                live = await con.fetchval(f"SELECT count(*) FROM {fq}")
                rows.append((fq, len(data), live, "nopk-count")); continue
            present = 0
            CH = 1000
            for i in range(0, len(data), CH):
                chunk = data[i:i+CH]
                vals = [tuple(unwrap(r.get(c)) for c in pkcols) for r in chunk]
                cols = ", ".join(pkcols)
                # Compare as text to sidestep per-type binding of heterogeneous PKs.
                cast = ", ".join(f"{c}::text" for c in pkcols)
                params = [[str(v[j]) if v[j] is not None else None for v in vals] for j in range(len(pkcols))]
                if len(pkcols) == 1:
                    present += await con.fetchval(
                        f"SELECT count(*) FROM {fq} WHERE {pkcols[0]}::text = ANY($1::text[])", params[0])
                else:
                    q = (f"SELECT count(*) FROM {fq} t JOIN unnest("
                         + ", ".join(f"${k+1}::text[]" for k in range(len(pkcols)))
                         + ") AS u(" + ", ".join(f"k{k}" for k in range(len(pkcols))) + ") ON "
                         + " AND ".join(f"t.{c}::text = u.k{k}" for k, c in enumerate(pkcols)))
                    present += await con.fetchval(q, *params)
            rows.append((fq, len(data), present, ",".join(pkcols)))
    finally:
        await con.close()
    return rows

async def main():
    m = await mongo_audit()
    p = await pg_audit()
    print("="*76); print("MONGO — exported vs present live"); print("="*76)
    miss_m = [r for r in m if r[2] < r[1]]
    print(f"collections: {len(m)}   exported: {sum(r[1] for r in m)}   present: {sum(r[2] for r in m)}")
    print(f"collections with missing docs: {len(miss_m)}")
    for n, exp, pres in miss_m:
        print(f"  MISSING  {n:<42} exported={exp:<7} present={pres:<7} gap={exp-pres}")
    print()
    print("="*76); print("POSTGRES — exported vs present live"); print("="*76)
    miss_p = [r for r in p if r[2] < r[1]]
    print(f"tables: {len(p)}   exported: {sum(r[1] for r in p)}   present: {sum(r[2] for r in p)}")
    print(f"tables with missing rows: {len(miss_p)}")
    for fq, exp, pres, pk in miss_p:
        print(f"  MISSING  {fq:<42} exported={exp:<7} present={pres:<7} gap={exp-pres}  pk={pk}")

asyncio.run(main())
