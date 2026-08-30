# @featuretrace:east-gate-data-restore — Scoped restore of the 2026-08-21 East Gate export.
# Layer: script
# Data flow: verified backup dir (mongo/*.json.gz + postgres/*.json.gz) -> --only scope -> MongoDB + PostgreSQL (building-scoped).
# Related: backend/scripts/data_repair/eastgate_purge_owner_and_financial_data.py
#          backend/scripts/data_repair/eastgate_neutralise_external_emails.py
#          docs/guides/eastgate_data_purge_and_restore_2026-08-21.md
"""Restore an East Gate (13195) export produced by the 2026-08-21 dump.

The export is the reversibility half of a deliberate data removal. It contains, per
store, one gzipped JSON file per collection/table plus a MANIFEST.json carrying row
counts and a sha256 of every file.

    # verify the archive is intact and matches its manifest (no writes)
    python3 scripts/data_repair/eastgate_export_restore.py --verify  --dir <BACKUP_DIR>

    # show what a restore would write, without writing (no writes)
    python3 scripts/data_repair/eastgate_export_restore.py --dry-run --dir <BACKUP_DIR>

    # actually restore
    python3 scripts/data_repair/eastgate_export_restore.py --apply   --dir <BACKUP_DIR>

Postgres notes
--------------
* Rows are inserted under the tenant's OWN uuid, not the bypass sentinel. Most tables
  (core.lots, core.parties, every finance.*) have no RLS bypass clause, so inserting
  under the sentinel would be rejected or silently scoped wrong (CLAUDE.md footgun #7).
* Insert order is resolved by retrying: a table whose foreign keys are not yet satisfied
  is deferred to the next pass. A pass that inserts nothing while rows remain reports the
  blocking constraint instead of continuing quietly.
* ON CONFLICT DO NOTHING makes the restore idempotent, so a partial run can be repeated.

Why the export is JSON and not pg_dump
--------------------------------------
pg_dump cannot run as `strataos_user`: it aborts on the first RLS-protected table
("query would be affected by row-level security policy"), and the partial file it leaves
behind contains ZERO rows for finance.receipts, core.lots and finance.bank_transactions —
a backup that looks plausible and restores nothing. A per-tenant logical export read
through the tenant's own RLS context is the faithful alternative available without
superuser.
"""

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import asyncpg
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from bson import json_util

ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT / ".env")


def _decode(o):
    """Reverse the type tagging the exporter applied, at any depth.

    Recursion is load-bearing. A JSON/JSONB column can hold a LIST of tagged values, and
    decoding only the top level leaves `{"__t__": "uuid", ...}` dicts inside it. asyncpg
    then fails with "'dict' object has no attribute 'bytes'" — an error that names the
    argument position but not the column, and reads like a driver problem rather than a
    decoding one. finance.financial_cutover_config and financial_onboarding_audit both
    carry such a column.
    """
    if isinstance(o, dict):
        if "__t__" in o:
            t, v = o["__t__"], o["v"]
            if t == "ts":
                return datetime.fromisoformat(v)
            if t == "dec":
                return Decimal(v)
            if t == "uuid":
                return UUID(v)
            if t == "b64":
                return bytes.fromhex(v)
        return {k: _decode(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_decode(v) for v in o]
    return o


def verify(dirpath: Path) -> int:
    """Checksum every file against the manifest. A backup nobody verified is a rumour."""
    bad = 0
    for store, key in (("mongo", "collections"), ("postgres", "tables")):
        mpath = dirpath / store / "MANIFEST.json"
        if not mpath.exists():
            print(f"  {store}: MANIFEST.json MISSING")
            bad += 1
            continue
        man = json.loads(mpath.read_text())
        entries = man[key]
        total = 0
        for name, meta in sorted(entries.items()):
            f = dirpath / store / f"{name}.json.gz"
            if not f.exists():
                print(f"  {store}/{name}: FILE MISSING")
                bad += 1
                continue
            digest = hashlib.sha256(gzip.open(f, "rb").read() if False else f.read_bytes()).hexdigest()
            # The manifest hashes the UNCOMPRESSED payload.
            digest = hashlib.sha256(gzip.open(f, "rb").read()).hexdigest()
            if digest != meta["sha256"]:
                print(f"  {store}/{name}: CHECKSUM MISMATCH")
                bad += 1
            total += meta.get("documents", meta.get("rows", 0))
        print(f"  {store}: {len(entries)} files, {total} records, "
              f"{'OK' if not bad else 'PROBLEMS FOUND'}")
    return bad


def select(entries: dict, only: list[str] | None) -> dict:
    """Narrow a manifest to `only`, refusing silently-wrong selections.

    A name that is not in the manifest is an operator error (a typo, or a table that
    moved between exports). Skipping it quietly would restore less than the operator
    asked for while still reporting success, so it is fatal instead.
    """
    if not only:
        return entries
    return {k: v for k, v in entries.items() if k in set(only)}


def report_unknown(only: list[str] | None, mongo: dict, pg: dict) -> list[str]:
    """Names matching neither store. Checked across BOTH manifests before failing."""
    if not only:
        return []
    known = set(mongo) | set(pg)
    return [n for n in only if n not in known]


async def restore_mongo(dirpath: Path, apply: bool, only=None) -> int:
    man = json.loads((dirpath / "mongo" / "MANIFEST.json").read_text())
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    written = 0
    try:
        for name, meta in sorted(select(man["collections"], only).items()):
            docs = json_util.loads(gzip.open(dirpath / "mongo" / f"{name}.json.gz", "rb").read())
            if not docs:
                continue
            if not apply:
                print(f"  [dry-run] mongo {name:<38} would insert {len(docs)}")
                written += len(docs)
                continue
            # Re-inserting by _id makes a repeat run idempotent rather than duplicating.
            ops = 0
            for d in docs:
                _id = d.get("_id")
                if _id is None:
                    await db[name].insert_one(d)
                else:
                    await db[name].replace_one({"_id": _id}, d, upsert=True)
                ops += 1
            print(f"  mongo {name:<38} restored {ops}")
            written += ops
    finally:
        cli.close()
    return written



async def _identity_always_columns(con, fq: str) -> set[str]:
    """Columns declared GENERATED ALWAYS AS IDENTITY on this table."""
    schema, table = fq.split(".", 1)
    rows = await con.fetch(
        """SELECT column_name FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
              AND identity_generation = 'ALWAYS'""",
        schema, table,
    )
    return {r["column_name"] for r in rows}


async def _break_cycle(con, fq: str, rows: list[dict], cols: list[str]) -> list[tuple]:
    """Insert rows with their cycle-forming FK nulled, returning what to restore after.

    `finance.levy_runs.earliest_grace_item_id` points at `finance.levy_items`, and
    `finance.levy_items.levy_run_id` points back at `finance.levy_runs`. Neither table can
    go first, and NONE of these constraints is DEFERRABLE, so `SET CONSTRAINTS ALL
    DEFERRED` cannot help — the retry-pass loop simply reports both as blocked forever.

    The column is nullable, so the cycle is broken by inserting the run without its
    pointer, letting the items land, then setting the pointer afterwards. Returns
    (primary_key_value, column, value) triples for that second pass.
    """
    pending: list[tuple] = []
    for r in rows:
        for col in _CYCLE_COLUMNS.get(fq, ()):
            if r.get(col) is not None:
                pending.append((fq, r[_PRIMARY_KEY[fq]], col, r[col]))
                r[col] = None
    return pending


# Columns whose foreign key no insert ORDER can satisfy, so the value is set on a second
# pass. Two shapes, both real in this schema:
#
#   self-referencing — journal_entries.reversal_of_id and
#     evidence_documents.supersedes_document_id point INTO THEIR OWN TABLE. A reversal
#     row batched before the entry it reverses fails, and since executemany has no
#     ordering guarantee that is a coin flip, not a fixable sort.
#
#   mutual — levy_runs.earliest_grace_item_id -> levy_items, while
#     levy_items.levy_run_id -> levy_runs. Neither table can go first.
#
# None of these constraints is DEFERRABLE, so SET CONSTRAINTS ALL DEFERRED cannot help.
# Every listed column is nullable, which is what makes the two-pass approach available.
# Kept explicit rather than discovered from the catalogue: the set is small, and a wrong
# guess would null a column that carried meaning.
# MUTUAL cycles only. A self-reference is handled by ORDERING instead (see
# _topological_order): a posted journal entry is immutable — prevent_posted_* triggers
# reject any UPDATE — so nulling the pointer and setting it afterwards fails with
# "journal entry ... is posted and cannot be modified. Use a reversal entry." Ordering
# needs no second write at all, which is the only approach an immutable ledger allows.
# NOTE the column is earliest_PAST_grace_item_id. The constraint is named
# fk_levy_runs_earliest_grace_item, without "past", so inferring the column from the
# error message gives a name that does not exist — and a non-existent key in this dict
# fails SILENTLY, because the null-out loop simply never matches. Verified against
# pg_get_constraintdef, not the constraint's name.
_CYCLE_COLUMNS = {
    "finance.levy_runs": ("earliest_past_grace_item_id",),
}
_PRIMARY_KEY = {
    "finance.levy_runs": "levy_run_id",
    "finance.journal_entries": "journal_entry_id",
    "finance.evidence_documents": "document_id",
}

# table -> (self-referencing column, primary key). Rows are sorted so a referenced row is
# always inserted before the row pointing at it.
_SELF_REF = {
    "finance.journal_entries": ("reversal_of_id", "journal_entry_id"),
    "finance.evidence_documents": ("supersedes_document_id", "document_id"),
}


def _topological_order(fq: str, rows: list[dict]) -> list[dict]:
    """Order rows so a self-referenced target precedes whatever points at it.

    executemany gives no ordering guarantee relative to the file, so a reversal entry
    batched ahead of the entry it reverses fails its own foreign key — intermittently,
    depending on export order. Sorting removes the problem without a second write, which
    matters because these rows are immutable once posted.

    A pointer to a row NOT in this batch is left where it is: the target either already
    exists in the table or genuinely does not, and both are the insert's business to
    report rather than this function's to hide.
    """
    col, pk = _SELF_REF[fq]
    by_id = {r[pk]: r for r in rows}
    ordered: list[dict] = []
    placed: set = set()

    def visit(row, seen):
        rid = row[pk]
        if rid in placed:
            return
        parent_id = row.get(col)
        # `seen` guards a data cycle (A reverses B reverses A), which should not exist
        # but must not hang the restore if it does.
        if parent_id and parent_id in by_id and parent_id not in placed and parent_id not in seen:
            visit(by_id[parent_id], seen | {rid})
        placed.add(rid)
        ordered.append(row)

    for r in rows:
        visit(r, {r[pk]})
    return ordered


async def restore_postgres(dirpath: Path, apply: bool, only=None) -> int:
    man = json.loads((dirpath / "postgres" / "MANIFEST.json").read_text())
    tid = man["tenant_id"]
    con = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    written = 0
    try:
        await con.execute(f"SET app.tenant_id = '{tid}'")
        remaining = dict(select(man["tables"], only))
        last_error: dict[str, str] = {}
        deferred_fk: list[tuple] = []
        while remaining:
            progressed = False
            for fq in sorted(remaining):
                rows = [{k: _decode(v) for k, v in r.items()}
                        for r in json.loads(gzip.open(dirpath / "postgres" / f"{fq}.json.gz", "rb").read())]
                if not rows:
                    remaining.pop(fq); progressed = True; continue
                if not apply:
                    print(f"  [dry-run] pg {fq:<40} would insert {len(rows)}")
                    written += len(rows); remaining.pop(fq); progressed = True; continue
                cols = list(rows[0].keys())
                ph = ",".join(f"${i+1}" for i in range(len(cols)))

                # A GENERATED ALWAYS identity column rejects an explicit value unless the
                # statement says OVERRIDING SYSTEM VALUE. finance.journal_entries.
                # entry_number is one, and a restore MUST preserve it: it is the human
                # reference on a posted journal entry, so letting the sequence reassign
                # numbers would silently renumber the general ledger.
                if fq in _SELF_REF:
                    rows = _topological_order(fq, rows)
                deferred_fk.extend(await _break_cycle(con, fq, rows, cols))
                identity = await _identity_always_columns(con, fq)
                override = " OVERRIDING SYSTEM VALUE" if identity & set(cols) else ""

                sql = (f"INSERT INTO {fq} ({','.join(cols)}){override} VALUES ({ph}) "
                       f"ON CONFLICT DO NOTHING")
                try:
                    async with con.transaction():
                        await con.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
                except Exception as exc:
                    last_error[fq] = str(exc)[:200]
                    continue  # foreign keys not satisfied yet — retry next pass
                print(f"  pg {fq:<40} restored {len(rows)}")
                written += len(rows)
                remaining.pop(fq)
                progressed = True
            if not progressed:
                for fq in sorted(remaining):
                    print(f"  pg {fq:<40} BLOCKED: {last_error.get(fq, 'unknown')}")
                break

        # Restore the pointers nulled to break the levy_runs <-> levy_items cycle. Runs
        # after every table has landed, so the targets now exist. Reported rather than
        # silent: a restore that quietly left these NULL would look complete while the
        # grace-period pointer it carries had been dropped.
        if apply and deferred_fk:
            by_table: dict[str, int] = {}
            for table, pk, col, value in deferred_fk:
                res = await con.execute(
                    f"UPDATE {table} SET {col} = $1 WHERE {_PRIMARY_KEY[table]} = $2",
                    value, pk)
                by_table[table] = by_table.get(table, 0) + int(res.split()[-1])
            for table, n in sorted(by_table.items()):
                print(f"  pg {table:<40} restored {n} cycle pointer(s)")
    finally:
        await con.close()
    return written


async def main(args) -> int:
    d = Path(args.dir)
    if not d.exists():
        print(f"Backup directory not found: {d}")
        return 2

    print(f"Backup: {d}")
    problems = verify(d)
    if args.verify:
        return 1 if problems else 0
    if problems:
        print("\nREFUSING to restore from an archive that failed verification.")
        return 2

    only = None
    if args.only:
        only = [x.strip() for x in args.only.split(",") if x.strip()]
        mman = json.loads((d / "mongo" / "MANIFEST.json").read_text())["collections"]
        pman = json.loads((d / "postgres" / "MANIFEST.json").read_text())["tables"]
        unknown = report_unknown(only, mman, pman)
        if unknown:
            print("\nUnknown collection/table name(s) — refusing to restore a partial "
                  "selection that would look successful:")
            for u in unknown:
                print(f"  {u}")
            return 2
        print(f"\nSCOPED restore: {len(only)} selected name(s); everything else is "
              f"deliberately NOT restored.")

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== MongoDB restore ({mode}) ===")
    m = await restore_mongo(d, args.apply, only)
    print(f"\n=== PostgreSQL restore ({mode}) ===")
    p = await restore_postgres(d, args.apply, only)
    print(f"\n{mode}: {m} MongoDB documents, {p} PostgreSQL rows")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, help="Backup directory produced by the exporter")
    ap.add_argument("--only", default="",
                    help="Comma-separated collection/table names to restore (Postgres "
                         "names are schema-qualified, e.g. core.lots). Omit to restore "
                         "everything. Unknown names abort the run.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--verify", action="store_true")
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args())))
