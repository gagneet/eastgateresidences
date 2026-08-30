"""
backend/scripts/migrations/migration_025_load_lots_from_mongo.py

# @featuretrace:demo_bank — Populate core.lots from the live Mongo units collection.
# Layer: migration
# Data flow: db.units (Mongo, building-scoped) → core.lots (Postgres)
# Related: backend/services/identity_bootstrap_service.py (LotRecord pattern)
#          backend/alembic/versions/0002_core_tenancy_tables.py (core.lots schema)
#          backend/scripts/migrations/migration_024_east_gate_historical_to_demo_bank.py
# Table: core.lots, core.schemes, core.tenants

Copies all units for a building from the Mongo `units` collection into `core.lots`,
linking to the matching `core.schemes` row via scheme_number = building_id.

Run (dry-run first):
  python3 backend/scripts/migrations/migration_025_load_lots_from_mongo.py --dry-run
  python3 backend/scripts/migrations/migration_025_load_lots_from_mongo.py

Optional flags:
  --building-id   building_id to load (default: read from DEFAULT_BUILDING_ID env, else prompt)
  --dry-run       Print what would be inserted without writing to Postgres

Prerequisites:
  - migration_023 (Mongo indexes) already run
  - core.schemes must have a row for this building_id (scheme_number = building_id)
  - Postgres RLS: script uses app.tenant_id bypass UUID for DML after setting correct context

Idempotent: uses ON CONFLICT (scheme_id, lot_number) DO UPDATE so running twice is safe.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

_here = os.path.dirname(os.path.abspath(__file__))
_backend = os.path.normpath(os.path.join(_here, "..", ".."))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import asyncpg
import motor.motor_asyncio

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# RLS bypass sentinel (grants access to all tenants for admin scripts)
_RLS_BYPASS = "00000000-0000-0000-0000-000000000000"


def _canonical_lot_number(raw: str | None) -> str:
    """Strip non-numeric prefixes like 'LOT' — store as plain '1', '2', …"""
    if not raw:
        return ""
    s = str(raw).strip()
    if s.upper().startswith("LOT"):
        s = s[3:].strip()
    return s


def _map_lot_use(mongo_val: str | None) -> str:
    """Normalise Mongo unit_type / lot_use values to Postgres lot_use vocabulary."""
    v = (mongo_val or "").lower().strip()
    mapping = {
        "apartment": "residential",
        "townhouse": "residential",
        "unit": "residential",
        "residential": "residential",
        "commercial": "commercial",
        "garage": "parking",
        "parking": "parking",
        "car_space": "parking",
        "storage": "storage",
        "common": "common",
    }
    return mapping.get(v, "residential")


async def _fetch_mongo_units(mongo_db, building_id: str) -> list[dict]:
    """Return all active units for the building from Mongo."""
    cursor = mongo_db.units.find(
        {
            "building_id": building_id,
            "is_test_data": {"$ne": True},
        },
        {
            "_id": 1,
            "unit_number": 1,
            "lot_number": 1,
            "unit_type": 1,
            "lot_use": 1,
            "entitlement_units": 1,
            "entitlement": 1,   # legacy alias
            "uoe": 1,           # unit of entitlement alias used in some buildings
            "floor_area_sqm": 1,
            "floor_area": 1,    # legacy alias
        },
    )
    return await cursor.to_list(None)


async def _resolve_scheme(pg: asyncpg.Connection, building_id: str) -> dict | None:
    """Look up core.schemes for this building_id (scheme_number = building_id)."""
    row = await pg.fetchrow(
        """
        SELECT scheme_id, tenant_id, scheme_number, scheme_name
        FROM core.schemes
        WHERE scheme_number = $1
          AND status = 'active'
        LIMIT 1
        """,
        building_id,
    )
    return dict(row) if row else None


async def _resolve_building_uuid(pg: asyncpg.Connection, scheme_id: str) -> str | None:
    """Return the core.buildings UUID linked to this scheme, if any."""
    row = await pg.fetchrow(
        "SELECT building_id FROM core.buildings WHERE scheme_id = $1 LIMIT 1",
        uuid.UUID(scheme_id),
    )
    return str(row["building_id"]) if row else None


async def run(
    mongo_db,
    pg: asyncpg.Connection,
    *,
    building_id: str,
    dry_run: bool,
) -> dict:
    # ── 1. Resolve the Postgres scheme for this building ──────────────────────
    """Generated function header.

    Function: run
    Path: backend/scripts/migrations/migration_025_load_lots_from_mongo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await pg.execute(f"SET app.tenant_id = '{_RLS_BYPASS}'")
    scheme = await _resolve_scheme(pg, building_id)
    if not scheme:
        raise RuntimeError(
            f"No active core.schemes row found for scheme_number='{building_id}'. "
            "Run the scheme bootstrap or onboarding pipeline first."
        )
    scheme_id = str(scheme["scheme_id"])
    tenant_id = str(scheme["tenant_id"])
    logger.info("Found scheme: %s (%s), tenant: %s", scheme["scheme_name"], scheme_id, tenant_id)

    building_uuid = await _resolve_building_uuid(pg, scheme_id)
    if building_uuid:
        logger.info("Linked core.buildings UUID: %s", building_uuid)
    else:
        logger.warning("No core.buildings row found for this scheme — lots will have NULL building_id")

    # ── 2. Pull units from Mongo ───────────────────────────────────────────────
    mongo_units = await _fetch_mongo_units(mongo_db, building_id)
    if not mongo_units:
        raise RuntimeError(
            f"No units found in Mongo for building_id='{building_id}'. "
            "Verify the building_id is correct and the units collection is populated."
        )
    logger.info("Found %d units in Mongo for building %s", len(mongo_units), building_id)

    # ── 3. Switch RLS to the actual tenant for core.lots DML ─────────────────
    # core.lots has NO bypass clause — the bypass UUID gives 0 rows.
    # Must use the real tenant_id to INSERT/UPDATE lots.
    if not dry_run:
        await pg.execute(f"SET app.tenant_id = '{tenant_id}'")

    # ── 4. Upsert each lot ────────────────────────────────────────────────────
    inserted = 0
    updated = 0
    skipped = 0
    now = datetime.now(timezone.utc)

    for unit in mongo_units:
        lot_number_raw = unit.get("lot_number") or unit.get("unit_number") or ""
        lot_number = _canonical_lot_number(str(lot_number_raw))
        unit_number = str(unit.get("unit_number") or lot_number_raw or "").strip()

        if not lot_number:
            logger.warning("Skipping unit with no lot_number: %s", unit.get("_id"))
            skipped += 1
            continue

        # Resolve entitlement — try multiple field aliases
        ent_raw = (
            unit.get("entitlement_units")
            or unit.get("entitlement")
            or unit.get("uoe")
        )
        entitlement = float(ent_raw) if ent_raw is not None else None

        lot_use = _map_lot_use(unit.get("lot_use") or unit.get("unit_type"))
        floor_area = unit.get("floor_area_sqm") or unit.get("floor_area")
        floor_area_sqm = float(floor_area) if floor_area is not None else None
        source_id = str(unit.get("_id", ""))

        if dry_run:
            logger.info(
                "  [DRY RUN] lot_number=%s unit_number=%s use=%s entitlement=%s",
                lot_number, unit_number, lot_use, entitlement,
            )
            inserted += 1
            continue

        result = await pg.fetchrow(
            """
            INSERT INTO core.lots
                (lot_id, tenant_id, scheme_id, building_id, lot_number, unit_number,
                 lot_use, entitlement_units, floor_area_sqm, status, created_at, updated_at)
            VALUES
                (gen_random_uuid(), $1::uuid, $2::uuid,
                 $3::uuid,
                 $4, $5, $6,
                 $7, $8,
                 'active', $9, $9)
            ON CONFLICT (scheme_id, lot_number) DO UPDATE
                SET unit_number       = EXCLUDED.unit_number,
                    lot_use           = EXCLUDED.lot_use,
                    entitlement_units = EXCLUDED.entitlement_units,
                    floor_area_sqm    = EXCLUDED.floor_area_sqm,
                    updated_at        = EXCLUDED.updated_at
            RETURNING (xmax = 0) AS was_inserted
            """,
            tenant_id,
            scheme_id,
            building_uuid,        # NULL-safe: asyncpg sends None as SQL NULL
            lot_number,
            unit_number,
            lot_use,
            entitlement,
            floor_area_sqm,
            now,
        )
        if result["was_inserted"]:
            inserted += 1
            logger.info("  INSERT lot_number=%s unit_number=%s", lot_number, unit_number)
        else:
            updated += 1
            logger.info("  UPDATE lot_number=%s unit_number=%s", lot_number, unit_number)

    # ── 5. Verify ─────────────────────────────────────────────────────────────
    if not dry_run:
        await pg.execute(f"SET app.tenant_id = '{_RLS_BYPASS}'")
        count = await pg.fetchval(
            "SELECT COUNT(*) FROM core.lots WHERE scheme_id = $1::uuid",
            uuid.UUID(scheme_id),
        )
        logger.info("Verification: core.lots now has %d rows for scheme %s", count, building_id)

    return {
        "building_id": building_id,
        "scheme_id": scheme_id,
        "dry_run": dry_run,
        "mongo_units_found": len(mongo_units),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
    }


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/migrations/migration_025_load_lots_from_mongo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(
        description="Load Mongo units → core.lots for a building"
    )
    parser.add_argument(
        "--building-id",
        default=os.environ.get("DEFAULT_BUILDING_ID") or "",
        help="building_id to load (scheme_number in Postgres). Required.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned inserts without writing to Postgres",
    )
    args = parser.parse_args()

    building_id = args.building_id.strip()
    if not building_id:
        building_id = input("building_id (e.g. 13195): ").strip()
    if not building_id:
        print("ERROR: --building-id is required")
        sys.exit(1)

    mongo_url = os.environ.get("MONGODB_URI") or os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("MONGODB_DB") or os.environ.get("DB_NAME", "strataos_production")
    pg_dsn = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL", "")
    if not pg_dsn:
        # Fall back to reading backend/.env directly
        env_path = os.path.join(_backend, ".env")
        if os.path.exists(env_path):
            with open(env_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("DATABASE_URL="):
                        pg_dsn = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
    if not pg_dsn:
        print("ERROR: DATABASE_URL env var is required (or set in backend/.env)")
        print("  Format: postgresql+asyncpg://user:pass@host:5432/dbname")
        sys.exit(1)
    # asyncpg requires plain postgresql:// — strip SQLAlchemy driver prefix
    pg_dsn = pg_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

    mongo_client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    mongo_db = mongo_client[db_name]
    pg = await asyncpg.connect(pg_dsn)

    try:
        result = await run(mongo_db, pg, building_id=building_id, dry_run=args.dry_run)
        print("\n── Lots Loader Result ────────────────────────")
        for k, v in result.items():
            print(f"  {k}: {v}")
        if args.dry_run:
            print("\n  [DRY RUN] No rows written. Re-run without --dry-run to apply.")
    finally:
        await pg.close()
        mongo_client.close()


if __name__ == "__main__":
    asyncio.run(main())
