"""Canonicalise East Gate owner/unit relationship records.

# @featuretrace:finance-owner-dashboard — repair stored unit references so co-owners resolve to the same ledger row.
# Layer: seed
# Data flow: users / user_units / memberships → canonical units.unit_number values (building-scoped).
# Related: backend/utils/unit_number.py
#           backend/scripts/data_repair/audit_unit_finance_resolution.py
# Collection: user_units

This is the production-safe companion to the frontend selectedUnit fix.  It does
not alter payments, levies, ledger balances, or Strata Web scraper snapshots.

Why this is required
--------------------
East Gate has accumulated multiple textual references for the same lot.  For
Unit 87, owner profiles may contain ``87`` or ``U87`` while the finance ledger
is keyed by the canonical ``units.unit_number`` value ``TH087``.  Co-owners can
therefore call finance APIs with different unit strings and see different owner
finance cards even though the accounting truth is per-lot.

Usage
-----

Connection defaults come from ``backend/.env`` (``MONGO_URL`` + ``DB_NAME``),
so from the repo root no explicit URI is needed:

    python3 backend/scripts/data_repair/repair_eastgate_unit_number_canonicalisation.py --dry-run
    python3 backend/scripts/data_repair/repair_eastgate_unit_number_canonicalisation.py

Override with ``--mongo-uri``/``--mongo-db`` or ``MONGODB_URI``/``MONGODB_DB``
env vars only when targeting a non-default database. Note the production
database name is ``strataos_production`` and the server requires auth — a bare
``mongodb://127.0.0.1:27018/strata_management`` URI points at an empty
database and the script will abort with "No canonical unit map found".

Optional targeted run:

    ... --email gagneet@eastgateresidences.com.au
    ... --email avneet@eastgateresidences.com.au

The script is idempotent. Re-running it should report zero changes after the
first successful repair.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from motor.motor_asyncio import AsyncIOMotorClient

try:
    from utils.unit_number import canonicalise_unit_from_existing, unit_number_candidates
except ImportError:  # Allow running from repository root without PYTHONPATH tweaks.
    import sys
    from pathlib import Path

    backend_dir = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(backend_dir))
    from utils.unit_number import canonicalise_unit_from_existing, unit_number_candidates

BUILDING_ID = "13195"


def _db_name(uri: str, fallback: str) -> str:
    """Generated function header.

    Function: _db_name
    Path: backend/scripts/data_repair/repair_eastgate_unit_number_canonicalisation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parsed = urlparse(uri)
    if parsed.path and parsed.path != "/":
        return parsed.path.lstrip("/").split("?")[0]
    return fallback


def _backend_env() -> dict[str, str]:
    """Read backend/.env so the script's defaults match the running backend."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    values: dict[str, str] = {}
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/scripts/data_repair/repair_eastgate_unit_number_canonicalisation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _unique(values: list[str]) -> list[str]:
    """Generated function header.

    Function: _unique
    Path: backend/scripts/data_repair/repair_eastgate_unit_number_canonicalisation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return list(dict.fromkeys([v for v in values if v]))


async def _canonical_map(db, building_id: str) -> dict[str, str]:
    # No result cap: a truncated map would silently skip repairs for units
    # beyond the cutoff. Projection keeps the full scan cheap even for large
    # schemes (PR #468 review, comment 3509328786).
    """Generated function header.

    Function: _canonical_map
    Path: backend/scripts/data_repair/repair_eastgate_unit_number_canonicalisation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    units = [
        unit
        async for unit in db.units.find(
            {"building_id": building_id},
            {"_id": 0, "unit_number": 1, "lot_number": 1},
        )
    ]
    canonical_values = [u.get("unit_number") for u in units if u.get("unit_number")]
    mapping: dict[str, str] = {}
    for unit in units:
        canonical = unit.get("unit_number")
        if not canonical:
            continue
        variants = []
        variants.extend(unit_number_candidates(canonical))
        variants.extend(unit_number_candidates(unit.get("lot_number")))
        for candidate in variants:
            mapping[candidate] = canonical
    # Add generated numeric/display aliases for every canonical row.
    for canonical in canonical_values:
        resolved = canonicalise_unit_from_existing(canonical, canonical_values)
        for candidate in unit_number_candidates(canonical):
            mapping[candidate] = resolved
    return mapping


def _canonicalise(value: Any, mapping: dict[str, str]) -> str | None:
    """Generated function header.

    Function: _canonicalise
    Path: backend/scripts/data_repair/repair_eastgate_unit_number_canonicalisation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for candidate in unit_number_candidates(value):
        if candidate in mapping:
            return mapping[candidate]
    return None


# Roles whose user_units rows may safely receive role_at_unit="owner".
# Mirrors OWNER_EQUIVALENT_USER_ROLES in services/owner_service.py.
_OWNER_EQUIVALENT_ROLES = {"owner", "ec_member", "strata_admin", "strata_manager", "super_admin"}


async def repair_users(db, mapping: dict[str, str], building_id: str, dry_run: bool, email: str | None) -> tuple[int, int]:
    # building_id filter is mandatory: the mapping is built from THIS building's
    # units, and users of other buildings (e.g. the Acme demo's A1..A14) must
    # never be rewritten against it. users is a global collection, so the
    # tenant filter has to be explicit here.
    """Generated function header.

    Function: repair_users
    Path: backend/scripts/data_repair/repair_eastgate_unit_number_canonicalisation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query: dict[str, Any] = {"building_id": building_id}
    if email:
        query["$or"] = [{"email": email}, {"portal_email": email}, {"owner_email": email}]
    users = await db.users.find(query).to_list(5000)
    matched = modified = 0
    for user in users:
        update: dict[str, Any] = {}
        current_unit = user.get("unit_number")
        canonical = _canonicalise(current_unit, mapping)
        if canonical and current_unit != canonical:
            update["unit_number"] = canonical
        owned_units = user.get("owned_units") or []
        if owned_units:
            canonical_owned = _unique([_canonicalise(v, mapping) or str(v) for v in owned_units])
            if canonical_owned != owned_units:
                update["owned_units"] = canonical_owned
        if update:
            matched += 1
            update["updated_at"] = _now()
            update["unit_canonicalised_by"] = "repair_eastgate_unit_number_canonicalisation.py"
            if not dry_run:
                await db.users.update_one({"_id": user["_id"]}, {"$set": update})
            modified += 1
    return matched, modified


async def repair_user_units(db, mapping: dict[str, str], building_id: str, dry_run: bool, email: str | None) -> tuple[int, int]:
    """Generated function header.

    Function: repair_user_units
    Path: backend/scripts/data_repair/repair_eastgate_unit_number_canonicalisation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    user_ids: list[str] | None = None
    if email:
        users = await db.users.find(
            {"$or": [{"email": email}, {"portal_email": email}, {"owner_email": email}]},
            {"_id": 0, "id": 1},
        ).to_list(20)
        user_ids = [u["id"] for u in users if u.get("id")]
    query: dict[str, Any] = {"building_id": building_id}
    if user_ids is not None:
        query["user_id"] = {"$in": user_ids}
    rels = await db.user_units.find(query).to_list(5000)
    # Resolve linked user roles once so role_at_unit is only backfilled for
    # owner-equivalent accounts — never for tenants/agents/guests.
    rel_user_ids = list({r.get("user_id") for r in rels if r.get("user_id")})
    role_by_user: dict[str, str] = {}
    if rel_user_ids:
        rel_users = await db.users.find(
            {"id": {"$in": rel_user_ids}}, {"_id": 0, "id": 1, "role": 1}
        ).to_list(len(rel_user_ids))
        role_by_user = {u["id"]: u.get("role", "") for u in rel_users}
    matched = modified = 0
    for rel in rels:
        update: dict[str, Any] = {}
        canonical = _canonicalise(rel.get("unit_number"), mapping)
        if canonical and rel.get("unit_number") != canonical:
            update["unit_number"] = canonical
        if (
            rel.get("is_active")
            and not rel.get("role_at_unit")
            and role_by_user.get(rel.get("user_id")) in _OWNER_EQUIVALENT_ROLES
        ):
            update["role_at_unit"] = "owner"
        if update:
            matched += 1
            update["updated_at"] = _now()
            update["unit_canonicalised_by"] = "repair_eastgate_unit_number_canonicalisation.py"
            if not dry_run:
                await db.user_units.update_one({"_id": rel["_id"]}, {"$set": update})
            modified += 1
    return matched, modified


async def repair_memberships(db, mapping: dict[str, str], building_id: str, dry_run: bool, email: str | None) -> tuple[int, int]:
    """Generated function header.

    Function: repair_memberships
    Path: backend/scripts/data_repair/repair_eastgate_unit_number_canonicalisation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    user_ids: list[str] | None = None
    if email:
        users = await db.users.find(
            {"$or": [{"email": email}, {"portal_email": email}, {"owner_email": email}]},
            {"_id": 0, "id": 1},
        ).to_list(20)
        user_ids = [u["id"] for u in users if u.get("id")]
    query: dict[str, Any] = {"building_id": building_id}
    if user_ids is not None:
        query["user_id"] = {"$in": user_ids}
    memberships = await db.memberships.find(query).to_list(5000)
    matched = modified = 0
    for membership in memberships:
        units = membership.get("units") or []
        if not units:
            continue
        canonical_units = _unique([_canonicalise(v, mapping) or str(v) for v in units])
        if canonical_units != units:
            matched += 1
            if not dry_run:
                await db.memberships.update_one(
                    {"_id": membership["_id"]},
                    {"$set": {
                        "units": canonical_units,
                        "updated_at": _now(),
                        "unit_canonicalised_by": "repair_eastgate_unit_number_canonicalisation.py",
                    }},
                )
            modified += 1
    return matched, modified


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/data_repair/repair_eastgate_unit_number_canonicalisation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Canonicalise East Gate unit references for users and memberships")
    parser.add_argument("--building-id", default=BUILDING_ID)
    parser.add_argument("--email", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--mongo-db", default=None)
    args = parser.parse_args()

    backend_env = _backend_env()
    uri = (args.mongo_uri or os.environ.get("MONGODB_URI") or os.environ.get("MONGO_URL")
           or backend_env.get("MONGO_URL") or "mongodb://127.0.0.1:27017")
    db_name = (args.mongo_db or os.environ.get("MONGODB_DB") or os.environ.get("DB_NAME")
               or backend_env.get("DB_NAME") or _db_name(uri, "strataos_production"))
    client = AsyncIOMotorClient(uri)
    db = client[db_name]
    try:
        mapping = await _canonical_map(db, args.building_id)
        if not mapping:
            raise RuntimeError(f"No canonical unit map found for building {args.building_id}")
        print(f"Mongo database: {db_name}")
        print(f"Building: {args.building_id}; email filter: {args.email or '<all>'}; dry_run={args.dry_run}")
        print("Sample Unit 87 candidates:", {c: mapping.get(c) for c in unit_number_candidates("87") if mapping.get(c)})

        user_counts = await repair_users(db, mapping, args.building_id, args.dry_run, args.email)
        rel_counts = await repair_user_units(db, mapping, args.building_id, args.dry_run, args.email)
        membership_counts = await repair_memberships(db, mapping, args.building_id, args.dry_run, args.email)

        print("\nRepair summary")
        print("--------------")
        print(f"users matched/modified: {user_counts[0]}/{user_counts[1]}")
        print(f"user_units matched/modified: {rel_counts[0]}/{rel_counts[1]}")
        print(f"memberships matched/modified: {membership_counts[0]}/{membership_counts[1]}")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
