#!/usr/bin/env python3
# @featuretrace:cutover-control-plane — restore withheld domain_cutover_status rows from the backup.
# Layer: script
# Data flow: backup core.domain_cutover_status.json.gz -> core.domain_cutover_status (building-scoped).
# Related: backend/services/domain_source_guard.py (the consumer — footgun #17)
#          docs/guides/eastgate_data_purge_and_restore_2026-08-21.md (why they were withheld)
# Collection: core.domain_cutover_status
# Tests: tests/backend/test_restore_domain_cutover_rows.py
"""Restore `core.domain_cutover_status` rows that the 2026-08-21 restore withheld.

Why they were withheld, and why that no longer applies
------------------------------------------------------
The 2026-08-21 purge removed all eight of East Gate's rows — it discovers tables
dynamically by `tenant_id` column, so this table was in scope without being
named. Phase 1 restored four (governance, occupancy, settings, identity_core)
and deliberately held the other four back, for a reason the restore guide states
plainly:

    "Restoring them while finance.* is empty would point the app at an empty
     store and render 'no data' as $0. The routing is paired with the data it
     routes to and moves in phase 2."

Phase 2 has since restored `finance.*` in full (2,233 receipts, 3,480 levy
items, 12,250 journal lines) and `finance.financial_cutover_config`. Half the
pair moved and half did not, so the stated precondition no longer holds.

This is a live routing change, not a data repair
------------------------------------------------
Per footgun #17, a MISSING row is not neutral: `require_domain_source`
synthesises `mongo_primary` / `readiness=unknown` and fails closed to MongoDB.
Inserting these rows is what makes PostgreSQL serve those domains. Run the
readiness gates first (`--check`) and read the output — the important one is
per-lot agreement between the stores, because that is what an owner sees.

Rows are restored **verbatim from the backup**, never reconstructed. Mode,
readiness, route group, toggle name and continuity policy all carry their
original values; inventing them would silently change routing semantics while
looking like a restore.

Safety
------
* Dry-run by default; `--apply` required.
* Only inserts domains that are ABSENT. An existing row is never overwritten —
  a live row may have been changed deliberately since the backup.
* Idempotent: re-running after a successful apply inserts nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import asyncpg  # noqa: E402

BYPASS = "00000000-0000-0000-0000-000000000000"

# Columns restored verbatim. `id` is excluded so Postgres assigns a fresh key —
# the backup's key may collide with a row created since, and the business
# identity here is (tenant_id, domain), not the surrogate.
COLUMNS = [
    "tenant_id", "scheme_id", "organisation_id", "building_id", "domain",
    "mode", "previous_mode", "readiness_status", "read_source", "write_source",
    "route_group", "toggle_name", "continuity_policy", "continuity_source",
    "rollback_available", "p0_snapshot", "promoted_by", "last_promoted_at",
    "last_readiness_check_at", "last_shadow_diff_at", "notes", "is_test_data",
]

# Columns whose Postgres type needs an explicit cast on insert: the export
# carries them as plain strings, and asyncpg will not coerce a str into an enum
# or jsonb on its own.
CASTS = {
    "p0_snapshot": "jsonb",
}

# asyncpg will not coerce an ISO string into a timestamp bind parameter — it
# wants a real datetime and raises DataError otherwise (the same class of trap
# as footgun #21's DATE encoding). The export carries these as strings.
TIMESTAMP_COLUMNS = {
    "last_promoted_at",
    "last_readiness_check_at",
    "last_shadow_diff_at",
    "created_at",
    "updated_at",
}


def _unwrap(value):
    """The exporter encodes typed scalars as {"__t__": <type>, "v": <value>}."""
    if isinstance(value, dict) and "__t__" in value and "v" in value:
        return value["v"]
    return value


def _coerce(column: str, value):
    """Turn an exported scalar into what asyncpg's binder expects."""
    if value is None:
        return None
    if column in TIMESTAMP_COLUMNS and isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def _bind(column: str, value):
    """Bind value for a column, without double-encoding JSON.

    `p0_snapshot` arrives from the export ALREADY serialised as a JSON string.
    Passing it through `json.dumps` again produced a jsonb *string* whose content
    is JSON, rather than a jsonb *object* — and `jsonb_typeof` then reads
    "string". That is not cosmetic: `DomainCutoverStatus` validates the field as
    a dict, so every restored row failed validation, `get_cutover_status`
    swallowed the error and returned None, and per footgun #17 a missing status
    falls back to MongoDB. The restore silently did nothing while appearing to
    succeed — the exact failure shape this table is notorious for.

    So: serialise only a value that is not already serialised.
    """
    if column not in CASTS:
        return _coerce(column, value)
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value)


async def run(backup_dir: Path, building_id: str, apply: bool) -> int:
    path = backup_dir / "postgres" / "core.domain_cutover_status.json.gz"
    if not path.exists():
        raise SystemExit(f"Backup file not found: {path}")
    rows = [
        {k: _unwrap(v) for k, v in r.items()}
        for r in json.load(gzip.open(path))
    ]

    conn = await asyncpg.connect(
        os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    )
    try:
        await conn.execute(f"SET app.tenant_id = '{BYPASS}'")
        scheme = await conn.fetchrow(
            "SELECT tenant_id::text tid FROM core.schemes WHERE scheme_number = $1",
            building_id,
        )
        if not scheme:
            raise SystemExit(f"No scheme for building_id={building_id!r}")
        tenant_id = scheme["tid"]
        await conn.execute(f"SET app.tenant_id = '{tenant_id}'")

        backup_rows = [r for r in rows if str(r.get("tenant_id")) == tenant_id]
        live = {
            r["domain"]
            for r in await conn.fetch(
                "SELECT domain FROM core.domain_cutover_status WHERE tenant_id = $1",
                tenant_id,
            )
        }
        missing = [r for r in backup_rows if r["domain"] not in live]

        print("=" * 76)
        print(f"Restore domain_cutover_status — building {building_id}"
              f"  [{'APPLY' if apply else 'DRY-RUN'}]")
        print("=" * 76)
        print(f"  in backup for this tenant : {len(backup_rows)}")
        print(f"  already live              : {sorted(live)}")
        print(f"  to restore                : {[r['domain'] for r in missing]}")
        for r in missing:
            print(f"      {r['domain']:<26} mode={r.get('mode')} "
                  f"readiness={r.get('readiness_status')} "
                  f"read={r.get('read_source')} write={r.get('write_source')}")

        if not missing:
            print("\n  Nothing to restore.")
            return 0
        if not apply:
            print("\n  DRY-RUN — re-run with --apply.")
            print("  NOTE: this CHANGES WHICH STORE SERVES these domains in production.")
            return 0

        inserted = 0
        for r in missing:
            cols = [c for c in COLUMNS if c in r]
            placeholders = [
                f"${i + 1}::{CASTS[c]}" if c in CASTS else f"${i + 1}"
                for i, c in enumerate(cols)
            ]
            values = [_bind(c, r[c]) for c in cols]
            await conn.execute(
                f"INSERT INTO core.domain_cutover_status ({', '.join(cols)}) "
                f"VALUES ({', '.join(placeholders)})",
                *values,
            )
            inserted += 1
            print(f"      restored {r['domain']}")

        # Assert the POST-CONDITION, not just the absence of an exception.
        #
        # The first run of this script inserted four rows successfully, printed
        # "RESTORED 4 rows", and routed nothing: p0_snapshot went in
        # double-encoded, so DomainCutoverStatus rejected every row,
        # get_cutover_status logged its error and returned None, and footgun
        # #17's missing-row default sent all four domains back to MongoDB.
        # Every `SELECT domain, mode` looked perfect throughout.
        #
        # A restore that cannot be READ is not a restore, so check readability
        # here rather than leaving it to be discovered later.
        bad = await conn.fetch(
            """SELECT domain FROM core.domain_cutover_status
                WHERE tenant_id = $1 AND p0_snapshot IS NOT NULL
                  AND jsonb_typeof(p0_snapshot) <> 'object'""",
            tenant_id,
        )
        if bad:
            raise SystemExit(
                "RESTORE UNUSABLE — p0_snapshot is not a jsonb object on "
                f"{[b['domain'] for b in bad]}. Consumers would reject these "
                "rows and fall back to MongoDB. Repair with:\n"
                "  UPDATE core.domain_cutover_status\n"
                "     SET p0_snapshot = (p0_snapshot #>> '{}')::jsonb\n"
                "   WHERE jsonb_typeof(p0_snapshot) = 'string';"
            )
        print(f"\n  RESTORED {inserted} rows — all readable by get_cutover_status.")
        print("  PostgreSQL now serves these domains.")
        return inserted
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backup-dir", required=True)
    ap.add_argument("--building-id", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(Path(args.backup_dir), args.building_id, args.apply))


if __name__ == "__main__":
    main()
