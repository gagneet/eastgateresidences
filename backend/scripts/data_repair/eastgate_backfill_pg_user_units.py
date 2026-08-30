#!/usr/bin/env python3
# @featuretrace:owner-activation — Create the missing core.user_units links for current owners.
# Layer: script
# Data flow: core.ownership_periods + core.users -> core.user_units (building-scoped).
# Related: backend/db_postgres/repos/ownership_repo.py
#          backend/scripts/data_repair/eastgate_assign_owner_mailboxes.py
"""Link current owners to their lots in core.user_units.

    python3 scripts/data_repair/eastgate_backfill_pg_user_units.py --dry-run
    python3 scripts/data_repair/eastgate_backfill_pg_user_units.py --apply

Nine owners across six lots (UA019, UA023, UA031, UA050, UA059, UA065) have a current
ownership period and a MongoDB user_units row, but no `core.user_units`. Postgres is what
login and the admin lists read, so the Mongo-side link is invisible where it counts —
those owners simply do not appear in /admin/users.

`party_id` IS set on these rows. That is the distinction the column carries:

    party_id SET   -> this person is an owner of record, tied to an ownership period
    party_id NULL  -> partner-of-owner: lives there, has portal access, owns nothing
                      (Jaime Aviles on TH071 is the reference example)

Both use `relationship='owner'`, so party_id is the ONLY thing separating a titleholder
from a resident partner. Setting it wrongly would either hide a real owner from ownership
queries or grant a partner an ownership claim they do not have.

The blocker turned out to be upstream: these owners have **no `core.users` row at all**.
MongoDB holds 128 East Gate users, Postgres 110, and the missing 18 include every one of
them. So this creates the Postgres identity first, from the MongoDB record, then links it.

Users are created with `requires_activation = TRUE` and NO password hash. That is
deliberate: an owner who has never signed in must not gain a usable login as a side
effect of a data-consistency repair. They pass through the same activation flow as
everyone else.

Matching is party -> user via the building-domain address assigned by
eastgate_assign_owner_mailboxes.py, which must run FIRST: before it, these owners carry
owner-transfer.<uuid>@ placeholders that match nothing by name.

NOTE: joins use core.lots.unit_number ("TH079"), never lot_number ("79") — see
ownership_repo.py's header and footgun #16.
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
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_user_units")

TENANT = "9e9d75c2-bd92-4695-8487-1592018c3af9"
_TITLES = re.compile(r"^(mr|mrs|ms|miss|dr|prof)\.?\s+", re.I)


def _key(name: str) -> str:
    """first+last, lowercased — stable across 'Mr Joshua Solano' vs 'Joshua Solano'."""
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", _TITLES.sub("", name or "")) if p]
    return f"{parts[0]}.{parts[-1]}".lower() if len(parts) >= 2 else (parts[0].lower() if parts else "")


async def main(args) -> int:
    pg = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    try:
        await pg.execute(f"SET app.tenant_id = '{TENANT}'")

        gaps = await pg.fetch("""
            SELECT l.lot_id, l.unit_number, l.scheme_id,
                   p.party_id, p.legal_name
            FROM core.lots l
            JOIN core.ownership_periods op
                  ON op.lot_id = l.lot_id AND op.valid_to IS NULL
            JOIN core.parties p ON p.party_id = op.owner_party_id
            WHERE NOT EXISTS (
                SELECT 1 FROM core.user_units uu
                 WHERE uu.lot_id = l.lot_id
                   AND uu.party_id = p.party_id
                   AND uu.valid_to IS NULL)
            ORDER BY l.unit_number
        """)
        users = await pg.fetch("SELECT user_id, full_name, email FROM core.users")
        by_key = {_key(u["full_name"]): u for u in users if u["full_name"]}

        # MongoDB identities for owners Postgres does not know about yet.
        cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
        mdb = cli[os.environ["DB_NAME"]]
        mongo_by_key = {}
        async for mu in mdb.users.find({"$or": [{"building_id": "13195"},
                                                {"plan_id": "13195"}]}):
            k = _key(mu.get("full_name") or "")
            if k:
                mongo_by_key[k] = mu
        cli.close()

        planned, to_create, unmatched = [], [], []
        for g in gaps:
            k = _key(g["legal_name"])
            if k in by_key:
                planned.append((g, by_key[k]))
            elif k in mongo_by_key:
                to_create.append((g, mongo_by_key[k]))
            else:
                unmatched.append((g, None))

        logger.info("%s owner(s) with no core.user_units link", len(gaps))
        for g, u in planned:
            logger.info("  LINK   %-7s %-26s -> %s", g["unit_number"],
                        str(g["legal_name"])[:26], u["email"])
        for g, mu in to_create:
            logger.info("  CREATE %-7s %-26s -> %s (no Postgres identity yet)",
                        g["unit_number"], str(g["legal_name"])[:26], mu.get("email"))
        for g, _ in unmatched:
            logger.info("  SKIP   %-7s %-26s (no user record in EITHER store)",
                        g["unit_number"], str(g["legal_name"])[:26])

        if not args.apply:
            logger.info("DRY-RUN — would create %s Postgres identit%s and %s link(s).",
                        len(to_create), "y" if len(to_create) == 1 else "ies",
                        len(planned) + len(to_create))
            return 0

        for g, mu in to_create:
            row = await pg.fetchrow("""
                INSERT INTO core.users
                    (tenant_id, email, full_name, role, is_active, is_approved,
                     requires_activation, is_test_data)
                VALUES ($1, $2, $3, CAST('owner' AS core.user_role), TRUE, TRUE, TRUE, FALSE)
                ON CONFLICT (tenant_id, email) DO UPDATE SET full_name = EXCLUDED.full_name
                RETURNING user_id
            """, TENANT, (mu.get("email") or "").strip().lower(), mu.get("full_name"))
            planned.append((g, {"user_id": row["user_id"], "email": mu.get("email")}))
        logger.info("created %s Postgres identit%s", len(to_create),
                    "y" if len(to_create) == 1 else "ies")

        created = 0
        for g, u in planned:
            # ON CONFLICT DO NOTHING keeps a repeat run idempotent; valid_from is the
            # ownership period's own start rather than today, so the link does not claim
            # the person only became an owner when this script ran.
            await pg.execute("""
                INSERT INTO core.user_units
                    (tenant_id, scheme_id, user_id, lot_id, party_id, relationship,
                     valid_from, is_test_data)
                SELECT $1, $2, $3, $4, $5, 'owner', op.valid_from, FALSE
                  FROM core.ownership_periods op
                 WHERE op.lot_id = $4 AND op.owner_party_id = $5 AND op.valid_to IS NULL
                 LIMIT 1
                ON CONFLICT DO NOTHING
            """, TENANT, g["scheme_id"], u["user_id"], g["lot_id"], g["party_id"])
            created += 1

        logger.info("APPLIED: %s link(s) created, %s skipped", created, len(unmatched))
        return 0
    finally:
        await pg.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args())))
