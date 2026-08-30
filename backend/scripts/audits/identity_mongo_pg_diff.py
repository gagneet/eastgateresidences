#!/usr/bin/env python3
"""
Read-only Mongo <-> Postgres identity/ownership divergence diff.

Purpose (GAP-IDENTITY-UI-DB-001, work item 1): quantify the write split-brain
found by the identity/ownership consolidation trace. For a *promoted*
identity_core building, the read path (/auth/me, owner_read_service) serves
Postgres core.*, but the legacy write UIs (owners-units, users) write MongoDB
only. This script measures how far the two stores have already drifted.

It answers one question: for this building, how many lots/users currently
DISAGREE between the Mongo record the write-UIs update and the Postgres record
the promoted read-path serves?

SAFETY
------
* 100% READ-ONLY. No INSERT/UPDATE/DELETE, no toggle change, no promotion.
* Building-agnostic: pass --building-id. No "13195" fallback.
* Reads units.owner_name / users.role DIRECTLY on purpose -- detecting that
  those raw Mongo fields diverge from Postgres is the entire point (this is the
  one script where the "never read units.owner_name directly" rule does not
  apply, precisely because we are auditing that field).

USAGE
-----
    cd backend && source venv/bin/activate
    python3 scripts/audits/identity_mongo_pg_diff.py --building-id 13195
    python3 scripts/audits/identity_mongo_pg_diff.py --building-id 13195 --json
    python3 scripts/audits/identity_mongo_pg_diff.py --building-id 13195 --samples 20

Exit code 0 always (it's an audit, not a gate) unless it cannot connect.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

# Ensure backend/ (three levels up: backend/scripts/audits/<this>) is importable
# when the script is invoked directly (python only adds the script's own dir).
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)


def _norm(v: Any) -> str:
    """Trim + casefold for tolerant comparison; None/'' both become ''."""
    if v is None:
        return ""
    return str(v).strip().casefold()


async def _mongo_owners(building_id: str) -> dict[str, dict[str, str]]:
    """{unit_number: {'owner_name', 'owner_email'}} from Mongo units (raw)."""
    from database import db
    from request_context import set_ctx_building_id

    set_ctx_building_id(building_id)
    out: dict[str, dict[str, str]] = {}
    cursor = db.units.find(
        {},
        {"_id": 0, "unit_number": 1, "owner_name": 1, "owner_email": 1},
    )
    async for u in cursor:
        un = str(u.get("unit_number", "")).strip()
        if not un:
            continue
        out[un] = {
            "owner_name": u.get("owner_name") or "",
            "owner_email": u.get("owner_email") or "",
        }
    return out


async def _mongo_user_roles(building_id: str) -> dict[str, dict[str, str]]:
    """{lower(email): {'role', 'ec_position'}} from Mongo users (raw)."""
    from database import db
    from request_context import set_ctx_building_id

    set_ctx_building_id(building_id)
    out: dict[str, dict[str, str]] = {}
    # users is a global (un-scoped) collection; filter by building membership
    # via the same building_id the write-UIs stamp on the user record.
    cursor = db._db.users.find(
        {"building_id": building_id},
        {"_id": 0, "email": 1, "role": 1, "ec_position": 1},
    )
    async for u in cursor:
        em = _norm(u.get("email"))
        if not em:
            continue
        out[em] = {
            "role": u.get("role") or "",
            "ec_position": u.get("ec_position") or "",
        }
    return out


async def _pg_owners(scheme: dict) -> dict[str, dict[str, str]]:
    """{unit_number: {'owner_name', 'owner_email'}} from Postgres core.*."""
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    sql = text(
        """
        SELECT COALESCE(l.unit_number, l.lot_number) AS unit_number,
               p.legal_name AS owner_name,
               COALESCE(p.primary_email, p.secondary_email, p.metadata->>'email') AS owner_email
          FROM core.ownership_periods op
          JOIN core.lots l    ON l.lot_id = op.lot_id
          JOIN core.parties p ON p.party_id = op.owner_party_id
         WHERE op.scheme_id = :scheme_id
           AND op.recorded_to IS NULL
           AND (op.valid_to IS NULL OR op.valid_to > CURRENT_DATE)
           AND op.is_primary_owner = true
        """
    )
    out: dict[str, dict[str, str]] = {}
    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))
        rows = (await session.execute(sql, {"scheme_id": str(scheme["scheme_id"])})).mappings().all()
    for r in rows:
        un = str(r["unit_number"]).strip()
        out[un] = {"owner_name": r["owner_name"] or "", "owner_email": r["owner_email"] or ""}
    return out


async def _pg_user_roles(scheme: dict) -> dict[str, dict[str, str]]:
    """{lower(email): {'role', 'ec_position'}} from Postgres core.*."""
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    sql = text(
        """
        SELECT u.email AS email,
               ra.role::text AS role,
               ra.ec_position AS ec_position
          FROM core.user_role_assignments ra
          JOIN core.users u ON u.user_id = ra.user_id
         WHERE ra.scheme_id = :scheme_id
        """
    )
    out: dict[str, dict[str, str]] = {}
    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))
        rows = (await session.execute(sql, {"scheme_id": str(scheme["scheme_id"])})).mappings().all()
    for r in rows:
        em = _norm(r["email"])
        if not em:
            continue
        # a user may hold >1 assignment; keep the most privileged-looking non-empty role
        prev = out.get(em)
        if prev is None or (not prev["role"] and r["role"]):
            out[em] = {"role": r["role"] or "", "ec_position": r["ec_position"] or ""}
    return out


def _diff_map(
    mongo: dict[str, dict[str, str]],
    pg: dict[str, dict[str, str]],
    fields: list[str],
) -> dict[str, Any]:
    keys = sorted(set(mongo) | set(pg))
    mismatches: list[dict[str, Any]] = []
    only_mongo, only_pg = [], []
    for k in keys:
        m, p = mongo.get(k), pg.get(k)
        if m is None:
            only_pg.append(k)
            continue
        if p is None:
            only_mongo.append(k)
            continue
        diffs = {f: {"mongo": m.get(f, ""), "pg": p.get(f, "")}
                 for f in fields if _norm(m.get(f)) != _norm(p.get(f))}
        if diffs:
            mismatches.append({"key": k, "fields": diffs})
    return {
        "compared": len([k for k in keys if k in mongo and k in pg]),
        "mismatch_count": len(mismatches),
        "only_in_mongo": only_mongo,
        "only_in_postgres": only_pg,
        "mismatches": mismatches,
    }


async def run(building_id: str) -> dict[str, Any]:
    from db_postgres.repos.config_repo import resolve_scheme_context

    scheme = await resolve_scheme_context(building_id)
    if not scheme:
        return {"error": f"no Postgres scheme found for building_id={building_id}"}

    mo, po, mu, pu = await asyncio.gather(
        _mongo_owners(building_id),
        _pg_owners(scheme),
        _mongo_user_roles(building_id),
        _pg_user_roles(scheme),
    )
    return {
        "building_id": building_id,
        "scheme_id": str(scheme["scheme_id"]),
        "tenant_id": str(scheme["tenant_id"]),
        "owners": _diff_map(mo, po, ["owner_name", "owner_email"]),
        "user_roles": _diff_map(mu, pu, ["role", "ec_position"]),
    }


def _print_human(res: dict[str, Any], samples: int) -> None:
    if res.get("error"):
        print(f"ERROR: {res['error']}")
        return
    print(f"\n=== Identity Mongo<->Postgres diff  building={res['building_id']} "
          f"(scheme={res['scheme_id']}) ===\n")
    for section, title in (("owners", "OWNERS (units.owner_* vs core.ownership_periods)"),
                           ("user_roles", "USER ROLES (users.role vs core.user_role_assignments)")):
        d = res[section]
        print(f"[{title}]")
        print(f"  compared:            {d['compared']}")
        print(f"  field mismatches:    {d['mismatch_count']}")
        print(f"  only in Mongo:       {len(d['only_in_mongo'])}  {d['only_in_mongo'][:samples]}")
        print(f"  only in Postgres:    {len(d['only_in_postgres'])}  {d['only_in_postgres'][:samples]}")
        for mm in d["mismatches"][:samples]:
            parts = ", ".join(f"{f}: mongo={v['mongo']!r} pg={v['pg']!r}" for f, v in mm["fields"].items())
            print(f"    - {mm['key']}: {parts}")
        print()
    o, u = res["owners"], res["user_roles"]
    total = o["mismatch_count"] + u["mismatch_count"] + len(o["only_in_mongo"]) + len(u["only_in_mongo"])
    verdict = ("NO DRIFT — stores agree; split-brain is latent, not yet realized"
               if total == 0 else
               f"DRIFT PRESENT — {total} identity facts disagree; category (d) is a CURRENT bug for this building")
    print(f"VERDICT: {verdict}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only Mongo<->Postgres identity diff")
    ap.add_argument("--building-id", required=True, help="e.g. 13195 (no default — multi-tenant rule)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--samples", type=int, default=10, help="max sample rows per section")
    args = ap.parse_args()

    res = asyncio.run(run(args.building_id))
    if args.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        _print_human(res, args.samples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
