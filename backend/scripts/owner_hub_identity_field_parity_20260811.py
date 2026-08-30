#!/usr/bin/env python3
# @featuretrace:owner-hub — Read-only Mongo-vs-Postgres identity field parity check for owner-hub.
# Layer: script
# Data flow: CLI -> live Mongo db.units + owner_service.get_owner_name() vs. live PG
#            core.lots + core.ownership_periods + core.parties -> stdout JSON report.
# Related: backend/services/ownerhub_service.py (the Mongo-only consumer this checks against)
#          backend/services/owner_service.py (canonical Mongo owner resolution)
#          backend/db_postgres/repos/ownership_repo.py (the PG-side query shapes reused here)
#          tasks/GAP-FIN-051-owner-hub-adjacent-pages-consolidation.md
"""Read-only field-level parity check: Mongo db.units vs. PostgreSQL core.lots +
core.ownership_periods + core.parties, for every lot of a given building.

Per GAP-FIN-051's corrected trace (and the independent review that caught the earlier draft's
"core.lots alone is enough" oversimplification): owner-hub's Mongo path reads unit_number,
entitlement, property_type, and a canonically-resolved owner_name from db.units + owner_service.
The Postgres equivalent needs THREE tables, not one -- core.lots for entitlement/lot_use,
core.ownership_periods for the CURRENT (valid_to IS NULL, recorded_to IS NULL) ownership link,
and core.parties for the owner's legal_name. This script produces the field-level evidence needed
before any owner-hub route is safely re-sourced from Postgres -- it does NOT change what any
route serves. Production mutation authorised: NO.

Usage:
    cd backend && venv/bin/python3 scripts/owner_hub_identity_field_parity_20260811.py \\
        --building 13195
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")

from sqlalchemy import text  # noqa: E402

from database import db as mongo_db  # noqa: E402
from db_postgres.session import async_session_context  # noqa: E402
from request_context import set_ctx_building_id  # noqa: E402
from services import owner_service  # noqa: E402

_BYPASS_UUID = "00000000-0000-0000-0000-000000000000"


def _num_close(a, b, tol=0.01) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


async def _fetch_pg_lots(building_id: str) -> dict[str, dict]:
    """Return {unit_number: {entitlement, lot_use, owners: [...]}} for every lot in the scheme.

    "owners" is a list, not a single value, deliberately -- a lot can have more than one
    current owner (core.ownership_periods.is_primary_owner / ownership_share), and collapsing
    that to one name before comparing would hide a real parity gap, not just report one cleanly.
    """
    async with async_session_context() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": _BYPASS_UUID}
        )
        scheme_row = (
            await session.execute(
                text("SELECT scheme_id::TEXT, tenant_id::TEXT FROM core.schemes WHERE scheme_number = :num"),
                {"num": building_id},
            )
        ).fetchone()
        if not scheme_row:
            raise SystemExit(f"No core.schemes row for scheme_number={building_id!r}")
        scheme_id, tenant_id = scheme_row

        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
        )
        lot_rows = (
            await session.execute(
                text(
                    "SELECT lot_id::TEXT, lot_number, unit_number, entitlement_units, lot_use "
                    "FROM core.lots WHERE scheme_id = :sid"
                ),
                {"sid": scheme_id},
            )
        ).fetchall()

        owner_rows = (
            await session.execute(
                text(
                    "SELECT op.lot_id::TEXT, op.is_primary_owner, op.ownership_share, "
                    "       p.legal_name "
                    "FROM core.ownership_periods op "
                    "JOIN core.parties p ON p.party_id = op.owner_party_id "
                    "WHERE op.scheme_id = :sid AND op.valid_to IS NULL AND op.recorded_to IS NULL"
                ),
                {"sid": scheme_id},
            )
        ).fetchall()

    owners_by_lot: dict[str, list[dict]] = {}
    for lot_id, is_primary, share, legal_name in owner_rows:
        owners_by_lot.setdefault(lot_id, []).append(
            {"legal_name": legal_name, "is_primary_owner": is_primary, "ownership_share": float(share) if share is not None else None}
        )

    result: dict[str, dict] = {}
    for lot_id, lot_number, unit_number, entitlement_units, lot_use in lot_rows:
        key = unit_number or lot_number
        result[key] = {
            "lot_id": lot_id,
            "lot_number": lot_number,
            "entitlement": float(entitlement_units) if entitlement_units is not None else None,
            "lot_use": lot_use,
            "owners": owners_by_lot.get(lot_id, []),
        }
    return result


async def _fetch_mongo_units(building_id: str) -> dict[str, dict]:
    set_ctx_building_id(building_id)
    units = await mongo_db.units.find(
        {"building_id": building_id}, {"_id": 0, "unit_number": 1, "entitlement": 1, "property_type": 1}
    ).to_list(500)

    result: dict[str, dict] = {}
    for unit in units:
        unit_number = unit.get("unit_number")
        if not unit_number:
            continue
        owner_name = await owner_service.get_owner_name(unit_number, building_id)
        result[unit_number] = {
            "entitlement": float(unit["entitlement"]) if unit.get("entitlement") is not None else None,
            "property_type": unit.get("property_type"),
            "owner_name": owner_name,
        }
    return result


async def run(building_id: str) -> dict:
    pg_lots, mongo_units = await asyncio.gather(
        _fetch_pg_lots(building_id), _fetch_mongo_units(building_id)
    )

    mongo_keys = set(mongo_units.keys())
    pg_keys = set(pg_lots.keys())

    findings = {
        "building_id": building_id,
        "mongo_unit_count": len(mongo_keys),
        "pg_lot_count": len(pg_keys),
        "missing_in_pg": sorted(mongo_keys - pg_keys),
        "missing_in_mongo": sorted(pg_keys - mongo_keys),
        "entitlement_mismatches": [],
        "no_pg_ownership_period": [],
        "multiple_pg_owners": [],
        "owner_name_needs_manual_review": [],
        "property_type_values_seen": {"mongo": sorted({m.get("property_type") for m in mongo_units.values() if m.get("property_type")}),
                                        "pg_lot_use": sorted({p.get("lot_use") for p in pg_lots.values() if p.get("lot_use")})},
    }

    for unit_number in sorted(mongo_keys & pg_keys):
        m = mongo_units[unit_number]
        p = pg_lots[unit_number]

        if not _num_close(m.get("entitlement"), p.get("entitlement")):
            findings["entitlement_mismatches"].append(
                {"unit_number": unit_number, "mongo": m.get("entitlement"), "pg": p.get("entitlement")}
            )

        owners = p.get("owners", [])
        if not owners:
            findings["no_pg_ownership_period"].append(unit_number)
        elif len(owners) > 1:
            findings["multiple_pg_owners"].append(
                {"unit_number": unit_number, "mongo_owner_name": m.get("owner_name"), "pg_owners": owners}
            )
        else:
            pg_name = (owners[0]["legal_name"] or "").strip().lower()
            mongo_name = (m.get("owner_name") or "").strip().lower()
            if pg_name != mongo_name:
                findings["owner_name_needs_manual_review"].append(
                    {"unit_number": unit_number, "mongo": m.get("owner_name"), "pg": owners[0]["legal_name"]}
                )

    findings["clean"] = not any(
        findings[k] for k in (
            "missing_in_pg", "missing_in_mongo", "entitlement_mismatches",
            "no_pg_ownership_period", "multiple_pg_owners", "owner_name_needs_manual_review",
        )
    )
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--building", required=True, dest="building_id")
    args = parser.parse_args()
    findings = asyncio.run(run(args.building_id))
    print(json.dumps(findings, indent=2, default=str))


if __name__ == "__main__":
    main()
