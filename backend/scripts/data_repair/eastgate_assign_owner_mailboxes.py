#!/usr/bin/env python3
# @featuretrace:owner-activation — Give every owner a real, own building-domain mailbox.
# Layer: script
# Data flow: core.parties + users -> derived address -> users.email + core.users + core.user_units (building-scoped).
# Related: backend/scripts/data_repair/eastgate_neutralise_external_emails.py
#          backend/db_postgres/repos/ownership_repo.py
#          tasks/GAP-COMMS-003-outbound-message-queue-and-activation.md
"""Give every East Gate owner their own name-derived @eastgateresidences.com.au mailbox.

    python3 scripts/data_repair/eastgate_assign_owner_mailboxes.py --dry-run
    python3 scripts/data_repair/eastgate_assign_owner_mailboxes.py --apply

Three problems that look different in a report but are one repair:

1. **Placeholder identities.** Owners materialised by the ownership-transfer pipeline
   carry `owner-transfer.<uuid>@eastgateresidences.com.au`. On the right domain, so the
   earlier neutralisation pass left them alone, but not an address anyone can be told,
   and `send_owner_activation_invites.py` deliberately skips them as non-inboxes.

2. **Shared mailboxes.** Four second-owners were given the PRIMARY owner's address
   (Kinjalben Vekariya holds `ua013.sanket_9377@…`, Sanket's). That is the duplicate-email
   finding from the restore: two people, one mailbox, so an invitation to one is an
   invitation to the other and neither can hold a distinct login.

3. **Postgres left behind.** `core.parties.primary_email` is NULL for these owners, and
   the six lots reported as "unlinked" have Mongo `user_units` rows but no matching
   `core.user_units`. Postgres is what login and the admin lists read, so the Mongo-side
   link alone is invisible where it counts.

Addresses are derived from the person's own name and unit — `ua019.niran.karaeni@…` —
so they are speakable, obviously theirs, and stable across re-runs. Titles (Mr/Ms/Mrs/Dr)
are stripped; collisions get a numeric suffix.

NOTE on lot identifiers: `core.lots.lot_number` is the plan lot ("79") and `unit_number`
is the addressable unit ("TH079"). This script joins on unit_number. Filtering the wrong
one returns zero rows silently — see ownership_repo.py's header and footgun #16.
"""

import argparse
import asyncio
import logging
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from database import db  # noqa: E402
from request_context import set_ctx_building_id  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("assign_mailboxes")

BUILDING = "13195"
TENANT = "9e9d75c2-bd92-4695-8487-1592018c3af9"
DOMAIN = "eastgateresidences.com.au"

# Honorifics carry no identity and make an address longer without making it clearer.
_TITLES = re.compile(r"^(mr|mrs|ms|miss|dr|prof|sir|madam)\.?\s+", re.I)
# A generated stand-in rather than a person's address.
_PLACEHOLDER = re.compile(r"^(owner-transfer|historical|developer)[.+]", re.I)


def derive_local(full_name: str, unit: str) -> str:
    """`Mr Niran Poglobe Karaeni` + `UA019` -> `ua019.niran.karaeni`.

    Unit-prefixed because two owners can share a name across a building, and the unit is
    the thing a manager reading the address will recognise. Middle names are dropped:
    they lengthen the address without disambiguating anyone.
    """
    name = _TITLES.sub("", (full_name or "").strip())
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", name) if p]
    if len(parts) >= 2:
        person = f"{parts[0]}.{parts[-1]}"
    elif parts:
        person = parts[0]
    else:
        person = "owner"
    local = f"{unit}.{person}" if unit else person
    return re.sub(r"\.{2,}", ".", local.lower()).strip(".")[:60]


async def plan(pg) -> list[dict]:
    """Everyone who needs an address, and what it should be."""
    set_ctx_building_id(BUILDING)

    taken = set()
    async for u in db.users.find({"$or": [{"building_id": BUILDING}, {"plan_id": BUILDING}]}):
        if u.get("email"):
            taken.add(u["email"].strip().lower())

    # Count how many users hold each address: >1 means a shared mailbox.
    holders: dict[str, list[dict]] = {}
    async for u in db.users.find({"$or": [{"building_id": BUILDING}, {"plan_id": BUILDING}]}):
        email = (u.get("email") or "").strip().lower()
        if email:
            holders.setdefault(email, []).append(u)

    # A person who ALREADY holds a proper address on another user record is a duplicate
    # identity, not someone missing a mailbox. Renaming their placeholder row would mint
    # a second near-identical address (ua063.rose.marimon vs ua063.rose.marimon.2) and
    # make the duplicate harder to see, not easier. Those are reported for deduplication
    # instead — a different repair, with a different correct answer.
    proper_by_name: dict[str, str] = {}
    for email, users in holders.items():
        if _PLACEHOLDER.match(email.split("@")[0]) or len(users) > 1:
            continue
        for u in users:
            name = (u.get("full_name") or "").strip().lower()
            if name:
                proper_by_name[name] = email

    duplicates: list[dict] = []
    actions: list[dict] = []
    for email, users in holders.items():
        placeholder = bool(_PLACEHOLDER.match(email.split("@")[0]))
        shared = len(users) > 1

        if not placeholder and not shared:
            continue

        # On a shared mailbox the FIRST holder keeps it — that owner has used the address
        # and may have signed in with it. Only the co-owners who were handed someone
        # else's address are moved.
        movers = users if placeholder else sorted(
            users, key=lambda u: str(u.get("created_at") or ""))[1:]

        for u in movers:
            name = (u.get("full_name") or "").strip().lower()
            if name and name in proper_by_name:
                duplicates.append({
                    "full_name": u.get("full_name"), "unit": u.get("unit_number"),
                    "placeholder": email, "existing": proper_by_name[name],
                })
                continue
            unit = (u.get("unit_number") or "").strip()
            base = derive_local(u.get("full_name") or "", unit)
            candidate, n = f"{base}@{DOMAIN}", 1
            while candidate in taken:
                n += 1
                candidate = f"{base}.{n}@{DOMAIN}"
            taken.add(candidate)
            actions.append({
                "user_id": u.get("id"), "full_name": u.get("full_name"),
                "unit": unit, "old": u.get("email"), "new": candidate,
                "why": "placeholder" if placeholder else "shared mailbox",
            })
    return sorted(actions, key=lambda a: (a["unit"] or "", a["new"])), duplicates


async def missing_pg_links(pg) -> list[dict]:
    """Mongo user_units rows with no core.user_units counterpart.

    Joined on unit_number, not lot_number — see the module docstring.
    """
    rows = await pg.fetch("""
        SELECT l.unit_number, p.legal_name, p.party_id, l.lot_id
        FROM core.lots l
        JOIN core.ownership_periods op ON op.lot_id = l.lot_id AND op.valid_to IS NULL
        JOIN core.parties p ON p.party_id = op.owner_party_id
        WHERE NOT EXISTS (
            SELECT 1 FROM core.user_units uu
             WHERE uu.lot_id = l.lot_id AND uu.valid_to IS NULL)
        ORDER BY l.unit_number
    """)
    return [dict(r) for r in rows]


async def main(args) -> int:
    pg = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    try:
        await pg.execute(f"SET app.tenant_id = '{TENANT}'")

        actions, duplicates = await plan(pg)
        logger.info("%s mailbox reassignment(s)", len(actions))
        for a in actions:
            logger.info("  %-7s %-26s %-12s %s -> %s", a["unit"],
                        str(a["full_name"])[:26], a["why"],
                        str(a["old"])[:34], a["new"])

        if duplicates:
            logger.info("%s duplicate identit%s skipped — these need DEDUPING, not a new "
                        "mailbox (the person already holds a proper address):",
                        len(duplicates), "y" if len(duplicates) == 1 else "ies")
            for d in duplicates:
                logger.info("  %-7s %-26s already has %s",
                            str(d["unit"]), str(d["full_name"])[:26], d["existing"])

        gaps = await missing_pg_links(pg)
        logger.info("%s lot(s) with a current owner but NO core.user_units link", len(gaps))
        for g in gaps:
            logger.info("  %-7s %s", g["unit_number"], str(g["legal_name"])[:34])

        if not args.apply:
            logger.info("DRY-RUN — re-run with --apply to write these changes.")
            return 0

        for a in actions:
            await db.users.update_one({"id": a["user_id"]},
                                      {"$set": {"email": a["new"]}})
            if a["old"]:
                await pg.execute(
                    "UPDATE core.users SET email = $1 WHERE lower(email) = $2",
                    a["new"], a["old"].strip().lower())
        logger.info("APPLIED: %s mailbox(es) reassigned", len(actions))
        return 0
    finally:
        await pg.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args())))
