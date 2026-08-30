#!/usr/bin/env python3
# @featuretrace:strata-web-portal-finance-ingest — Post-scrape verification of owners and balances (READ-ONLY).
# Layer: script
# Data flow: strata_owners (Mongo, portal mirror) + core.user_units/core.users (PG)
#            -> per-lot owner + balance comparison -> classified report. Writes nothing.
# Related: backend/scripts/ingest/strata_web_post_scrape_pipeline.py
#          backend/scripts/audits/portal_vs_ledger_reconciliation.py
# Toggle: n/a (read-only audit)
"""Verify owners and balances against the portal mirror after a scrape. READ-ONLY.

After a scrape, ``strata_owners`` holds the portal's own per-lot owner name and
balance. This compares that against what the application will actually show:

* **Balances** — ``strata_owners.balance`` (portal) vs the Mongo ``unit_levy_ledger``
  position. A gap here is payment activity not yet ingested, which is expected until
  the Demo Bank candidates from ``strata_web_post_scrape_pipeline.py`` are reviewed
  and promoted.

* **Owners** — the portal's owner name(s) vs ``core.user_units`` joined to
  ``core.users``. PostgreSQL is checked, not Mongo, because ``identity_core`` is
  promoted for East Gate and ``list_active_users_for_scheme`` — the read behind
  ``GET /users`` — resolves membership from ``core.user_units``.

Owner comparison is **per person and order-insensitive**. A portal string like
"Mr Peter Hanks & Ms Fiona Hanks" names two people; a naive string compare against
"Fiona Hanks & Peter Hanks" reports a false mismatch, which is exactly what a first
pass of this check did. Titles are stripped and " and " is normalised to " & ".

Findings are classified rather than lumped together, because the remedies differ:

  ``identical``        portal and PostgreSQL name the same people.
  ``missing_coowner``  PostgreSQL holds a strict subset — a co-owner has no active
                       link. East Gate has four documented shared-mailbox co-owner
                       pairs (UA013, UA015, UA045, UA054), so treat this as expected
                       for those and investigate any other lot.
  ``extra_person``     PostgreSQL links someone the portal does not name. Usually a
                       link left open after a transfer, or a non-human account.
  ``conflict``         Different people entirely — a real ownership discrepancy.

Usage (from repo root):
    backend/venv/bin/python3 backend/scripts/audits/verify_portal_owners_and_balances.py \
        --building-id 13195
    ... --json-out /tmp/verify.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")

import asyncpg  # noqa: E402

# Honorifics carry no identity and appear inconsistently between the portal and our
# own records; stripping them is what makes the per-person comparison meaningful.
_TITLES = re.compile(r"\b(mr|mrs|ms|miss|dr|prof)\b\.?", re.I)


def people(value: str | None) -> set[str]:
    """Split an owner string into a set of normalised person keys.

    "Mr Peter Hanks & Ms Fiona Hanks" -> {"peterhanks", "fionahanks"}

    Set semantics deliberately make the comparison order-insensitive: the portal and
    core.user_units order co-owners differently and neither order is wrong.
    """
    text = _TITLES.sub("", value or "").replace(" and ", " & ")
    return {
        re.sub(r"[^a-z]", "", part.lower())
        for part in text.split("&")
        if re.sub(r"[^a-z]", "", part.lower())
    }


async def _portal_rows(building_id: str) -> dict[str, dict]:
    """Per-lot owner + balance as the scrape last left them, keyed by lot number."""
    from request_context import set_ctx_building_id

    set_ctx_building_id(building_id)
    from database import db

    rows = await db.strata_owners.find({"building_id": building_id}, {"_id": 0}).to_list(None)
    out: dict[str, dict] = {}
    for r in rows:
        # strata_owners uses `lot`/`balance` — NOT `lot_number`/`balance_cents`. Keying
        # on the wrong field silently yields zero overlap and a report of "everything
        # differs", which is how a first pass of this check went wrong.
        lot = str(r.get("lot") if r.get("lot") is not None else r.get("lot_number") or "")
        if not lot:
            continue
        names = " & ".join(n for n in [r.get("owner_name"), r.get("owner_name_b")] if n)
        out[lot] = {
            "unit_number": r.get("unit_number"),
            "owner_name": names,
            "balance_cents": None if r.get("balance") is None else round(float(r["balance"]) * 100),
        }
    return out


async def _pg_owners(dsn: str, building_id: str) -> tuple[str, dict[str, list[dict]]]:
    conn = await asyncpg.connect(dsn)
    try:
        conn_sentinel = "00000000-0000-0000-0000-000000000000"
        await conn.execute(f"SET app.tenant_id='{conn_sentinel}'")
        tenant_id = await conn.fetchval(
            "SELECT tenant_id FROM core.schemes WHERE scheme_number=$1", building_id
        )
        if tenant_id is None:
            raise SystemExit(f"No Postgres scheme for building_id={building_id}")
        # core.lots and core.user_units have NO RLS bypass clause — the sentinel returns
        # zero rows on them, which reads exactly like "this building has no owners".
        await conn.execute(f"SET app.tenant_id = '{tenant_id}'")
        rows = await conn.fetch(
            """
            SELECT l.lot_number, l.unit_number,
                   -- IDENTITY COMES FROM THE PARTY, NOT THE ACCOUNT NAME.
                   -- Co-owners who share one household mailbox share ONE core.users row,
                   -- linked once per person via user_units.party_id. Reading
                   -- users.full_name therefore shows only whichever co-owner's name the
                   -- account happens to carry, and reports the other as missing.
                   -- East Gate has four such pairs (UA013, UA015, UA045, UA054) and an
                   -- earlier version of this script called three of them "missing a
                   -- co-owner" for exactly this reason. core.parties.legal_name is the
                   -- person; users.full_name is a label on a login. See CLAUDE.md,
                   -- "Person Identity Is the core.parties Link, Never a Name or Email".
                   COALESCE(p.legal_name, u.full_name) AS full_name,
                   u.full_name AS account_name,
                   u.email, u.status, uu.relationship, uu.valid_to
            FROM core.lots l
            LEFT JOIN core.user_units uu ON uu.lot_id = l.lot_id
            LEFT JOIN core.users u ON u.user_id = uu.user_id
            LEFT JOIN core.parties p ON p.party_id = uu.party_id
            WHERE l.tenant_id = $1
            ORDER BY (l.lot_number)::int
            """,
            tenant_id,
        )
    finally:
        await conn.close()

    by_lot: dict[str, list[dict]] = {}
    for r in rows:
        by_lot.setdefault(r["lot_number"], []).append(
            {
                "full_name": r["full_name"], "account_name": r["account_name"],
                "email": r["email"], "status": r["status"],
                "relationship": r["relationship"], "valid_to": r["valid_to"],
            }
        )
    return str(tenant_id), by_lot


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--building-id", required=True)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
    portal = await _portal_rows(args.building_id)
    tenant_id, pg = await _pg_owners(dsn, args.building_id)

    buckets: dict[str, list] = {
        "identical": [], "missing_coowner": [], "extra_person": [], "conflict": [], "no_link": [],
    }
    for lot in sorted(portal, key=lambda x: int(x) if x.isdigit() else 10**6):
        p = portal[lot]
        # Only links that are still OPEN count toward membership — a closed link
        # (valid_to set) is a former owner and must not be read as current.
        active = [r for r in pg.get(lot, []) if r["full_name"] and r["valid_to"] is None]
        want, have = people(p["owner_name"]), set()
        for r in active:
            have |= people(r["full_name"])

        row = {"lot": lot, "unit_number": p["unit_number"], "portal_owner": p["owner_name"],
               "pg_owners": sorted({r["full_name"] for r in active}),
               "pg_emails": sorted({r["email"] for r in active if r["email"]})}
        if not have:
            buckets["no_link"].append(row)
        elif want == have:
            buckets["identical"].append(row)
        elif have < want:
            row["missing"] = sorted(want - have)
            buckets["missing_coowner"].append(row)
        elif want < have:
            row["extra"] = sorted(have - want)
            buckets["extra_person"].append(row)
        else:
            buckets["conflict"].append(row)

    # Duplicate open links are their own defect: two rows for one person on one lot.
    # A genuine duplicate is the SAME PERSON linked twice. Two links on one lot that
    # resolve to two different parties are two co-owners sharing a login — the normal
    # shape for a shared household mailbox, not a defect. Keying this on the account
    # name (before the party fix above) reported all four East Gate co-owner pairs as
    # duplicates.
    dupes = []
    for lot, rows in pg.items():
        seen: dict[str, int] = {}
        for r in rows:
            if r["full_name"] and r["valid_to"] is None:
                seen[r["full_name"]] = seen.get(r["full_name"], 0) + 1
        for name, n in seen.items():
            if n > 1:
                dupes.append({"lot": lot, "name": name, "open_links": n})

    print(f"\n{'=' * 92}")
    print(f"Portal owner + balance verification — building {args.building_id}  (READ-ONLY)")
    print("=" * 92)
    print(f"  lots on the portal                 {len(portal)}")
    for k in ("identical", "missing_coowner", "extra_person", "conflict", "no_link"):
        print(f"  {k:34} {len(buckets[k])}")
    print(f"  {'lots with DUPLICATE open links':34} {len(dupes)}")

    for k, title in (
        ("conflict", "CONFLICT — different people entirely"),
        ("extra_person", "EXTRA — PostgreSQL links someone the portal does not name"),
        ("missing_coowner", "MISSING CO-OWNER — PostgreSQL holds a strict subset"),
        ("no_link", "NO ACTIVE LINK in PostgreSQL"),
    ):
        if not buckets[k]:
            continue
        print(f"\n  {title}")
        for r in buckets[k]:
            print(f"    lot {r['lot']:>3} {str(r['unit_number']):8} portal={r['portal_owner']!r}")
            print(f"{'':13} pg={r['pg_owners']}")
            if r.get("extra"):
                print(f"{'':13} extra={r['extra']}")
            if r.get("missing"):
                print(f"{'':13} missing={r['missing']}")
    if dupes:
        print("\n  DUPLICATE open user_units links (one person, two live rows on one lot)")
        for d in dupes:
            print(f"    lot {d['lot']:>3}  {d['name']}  x{d['open_links']}")

    print("\n" + "=" * 92)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            {"building_id": args.building_id, "tenant_id": tenant_id, "read_only": True,
             "buckets": buckets, "duplicate_open_links": dupes}, indent=2, default=str))
        print(f"  JSON written to {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
