# @featuretrace:east-gate-data-restore — Rewrite non-building-domain user emails after a restore.
# Layer: script
# Data flow: MongoDB users/units/invites + core.users/parties -> one value map -> both stores (building-scoped).
# Related: backend/scripts/data_repair/eastgate_export_restore.py
#          backend/utils/email_suppression.py
#          docs/guides/eastgate_data_purge_and_restore_2026-08-21.md
"""Rewrite every non-@eastgateresidences.com.au user email for East Gate (13195).

Requested 2026-08-26 alongside the owner/unit restore: after restoring real owner
records from the 2026-08-21 export, no real personal address (gmail, hotmail, ...) may
remain in either store. Addresses are REWRITTEN onto the building domain rather than
blanked, because `core.users` carries UNIQUE (tenant_id, email) and email is the login
identifier resolved by `core.find_user_for_auth(CITEXT)` — nulling it would break login
for 95 of 128 accounts and collide on the unique index.

    python3 scripts/data_repair/eastgate_neutralise_external_emails.py --dry-run
    python3 scripts/data_repair/eastgate_neutralise_external_emails.py --apply

What is NOT rewritten, and why
------------------------------
* role=super_admin. The platform's only super_admin lives on another domain; rewriting
  it changes the address the operator signs in with and there is no fallback account.
  Excluded BY ROLE, not by a hardcoded address, so it holds if the account changes.
* Infrastructure config in `email_settings` (smtp_user / sender_email /
  migadu_admin_email). These are transport credentials, not user identities — rewriting
  smtp_user would break SMTP auth. Reported so the operator can see them.

The mapping is built ONCE from every address found across both stores and then applied
by value everywhere, so the same person keeps one identity across `users`, `units`,
`owner_invites`, `core.parties` and the rest. Rewriting per-collection would let the
same human end up with different addresses in different tables.
"""

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT / ".env")

BUILDING = "13195"
TENANT = "9e9d75c2-bd92-4695-8487-1592018c3af9"
DOMAIN = "eastgateresidences.com.au"
EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)

# Scoped to what the restore actually brought back, plus the config docs that survived it.
MONGO_COLLECTIONS = [
    "users", "units", "user_units", "strata_owners", "occupancy_status", "unit_utilities",
    "memberships", "user_roles", "lot_ownerships", "ownership_transfer_log",
    "owner_transfer_requests", "owner_invites", "relationship_tuples",
    "unit_change_requests", "zones", "scheme_classes", "scheme_class_history",
    "ec_members", "benefit_groups", "settings", "document_folders",
    "levy_fairness_audit",
    "by_laws", "by_laws_acknowledgments", "organisation_buildings", "legal_pages",
]
# email_settings is deliberately absent: it holds SMTP transport credentials.

PG_COLUMNS = [
    ("core.users", "email"),
    ("core.parties", "primary_email"),
    ("core.parties", "secondary_email"),
    ("core.user_email_aliases", "alias_email"),
]

BID_FILTER = {"$or": [{"building_id": BUILDING}, {"plan_id": BUILDING}]}

# Real owners who self-registered and hold a working password on their own address.
# Exempted on the operator's instruction 2026-08-26: rewriting these changes the address
# three actual people sign in with, and with email hard-stopped platform-wide there is no
# way to tell them the new one. Their addresses therefore remain in the database
# deliberately — they are still unmailable while EMAIL_SEND_DISABLED_ALL is set.
EXEMPT_EMAILS = {
    "riyuroy@gmail.com",        # Riyu Kurian Abraham, TH086
    "adityashouvik@gmail.com",  # Shouvik Aditya, TH084 (logged in 2026-06-13)
    "avneetrooprai@gmail.com",  # Avneet Rooprai, TH087
}


def slug(local: str) -> str:
    s = re.sub(r"[^a-z0-9._-]+", ".", local.lower()).strip(".")
    s = re.sub(r"\.{2,}", ".", s)
    return s[:48] or "owner"


def walk_emails(o, out):
    """Collect every email-shaped string in a document."""
    if isinstance(o, dict):
        for v in o.values():
            walk_emails(v, out)
    elif isinstance(o, list):
        for v in o:
            walk_emails(v, out)
    elif isinstance(o, str) and EMAIL_RX.match(o.strip()):
        out.add(o.strip())


def rewrite(o, mapping):
    """Return a copy with every mapped address replaced. Reports how many it changed."""
    if isinstance(o, dict):
        return {k: rewrite(v, mapping) for k, v in o.items()}
    if isinstance(o, list):
        return [rewrite(v, mapping) for v in o]
    if isinstance(o, str):
        return mapping.get(o.strip().lower(), o)
    return o


async def build_mapping(db, pg):
    """One address -> one replacement, across both stores."""
    found: set[str] = set()
    for c in MONGO_COLLECTIONS:
        async for d in db[c].find(BID_FILTER):
            walk_emails(d, found)
    for table, col in PG_COLUMNS:
        for r in await pg.fetch(f"SELECT {col} AS e FROM {table} WHERE {col} IS NOT NULL"):
            if r["e"] and EMAIL_RX.match(str(r["e"]).strip()):
                found.add(str(r["e"]).strip())

    # Protected: the operator's own administrator login, identified by role in both stores.
    protected: set[str] = set()
    async for u in db["users"].find({**BID_FILTER, "role": "super_admin"}):
        if u.get("email"):
            protected.add(u["email"].strip().lower())
    for r in await pg.fetch("SELECT email FROM core.users WHERE role = 'super_admin'"):
        if r["email"]:
            protected.add(str(r["email"]).strip().lower())

    # Unit numbers make the replacement addresses legible rather than opaque.
    unit_of: dict[str, str] = {}
    async for u in db["units"].find(BID_FILTER):
        for f in ("owner_email", "owner_email_b"):
            if u.get(f) and u.get("unit_number"):
                unit_of.setdefault(str(u[f]).strip().lower(), str(u["unit_number"]))

    mapping: dict[str, str] = {}
    taken: set[str] = set()
    for r in await pg.fetch("SELECT lower(email) AS e FROM core.users WHERE email IS NOT NULL"):
        taken.add(r["e"])

    skipped_ondomain, skipped_protected = 0, 0
    for addr in sorted(found, key=str.lower):
        low = addr.lower()
        if low in mapping:
            continue
        if low.endswith("@" + DOMAIN):
            skipped_ondomain += 1
            continue
        if low in protected or low in EXEMPT_EMAILS:
            skipped_protected += 1
            continue
        base = slug(low.split("@")[0])
        unit = unit_of.get(low)
        local = f"{unit.lower()}.{base}" if unit else base
        cand, n = f"{local}@{DOMAIN}", 1
        while cand in taken:
            n += 1
            cand = f"{local}.{n}@{DOMAIN}"
        taken.add(cand)
        mapping[low] = cand
    return mapping, skipped_ondomain, skipped_protected


async def main(args) -> int:
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    pg = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    try:
        await pg.execute(f"SET app.tenant_id = '{TENANT}'")
        mapping, on_domain, protected = await build_mapping(db, pg)

        print(f"Addresses already on @{DOMAIN}: {on_domain} (left as-is)")
        print(f"Protected super_admin logins:   {protected} (left as-is)")
        print(f"To rewrite:                     {len(mapping)}\n")
        for k, v in sorted(mapping.items())[: args.show]:
            print(f"  {k:44s} -> {v}")
        if len(mapping) > args.show:
            print(f"  ... and {len(mapping) - args.show} more")

        if not mapping:
            return 0

        print(f"\n=== MongoDB ({'APPLY' if args.apply else 'DRY-RUN'}) ===")
        m_docs = 0
        for c in MONGO_COLLECTIONS:
            changed = 0
            async for d in db[c].find(BID_FILTER):
                new = rewrite(d, mapping)
                if new != d:
                    changed += 1
                    if args.apply:
                        await db[c].replace_one({"_id": d["_id"]}, new)
            if changed:
                print(f"  {c:34s} {changed} document(s)")
                m_docs += changed

        print(f"\n=== PostgreSQL ({'APPLY' if args.apply else 'DRY-RUN'}) ===")
        p_rows = 0
        for table, col in PG_COLUMNS:
            n = 0
            for old, new in mapping.items():
                if args.apply:
                    res = await pg.execute(
                        f"UPDATE {table} SET {col} = $1 WHERE lower({col}) = $2", new, old)
                    n += int(res.split()[-1])
                else:
                    n += await pg.fetchval(
                        f"SELECT count(*) FROM {table} WHERE lower({col}) = $1", old)
            if n:
                print(f"  {table}.{col:24s} {n} row(s)")
                p_rows += n

        print(f"\n{'APPLIED' if args.apply else 'DRY-RUN'}: "
              f"{m_docs} MongoDB documents, {p_rows} PostgreSQL rows")
        return 0
    finally:
        cli.close()
        await pg.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--show", type=int, default=15, help="Mapping rows to print")
    sys.exit(asyncio.run(main(ap.parse_args())))
