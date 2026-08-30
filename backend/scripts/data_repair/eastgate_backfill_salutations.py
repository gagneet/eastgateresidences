#!/usr/bin/env python3
# @featuretrace:user-management — Split embedded honorifics out of names into salutation.
# Layer: script
# Data flow: core.users.full_name / core.parties.legal_name -> salutation + cleaned name (building-scoped).
# Related: backend/alembic/versions/0097_party_user_salutation.py
#          backend/utils/name_utils.py
"""Move an embedded honorific out of the name and into `salutation`.

    python3 scripts/data_repair/eastgate_backfill_salutations.py --dry-run
    python3 scripts/data_repair/eastgate_backfill_salutations.py --apply

`legal_name = 'Ms Rachel Clarke'` becomes `salutation='Ms'`, `legal_name='Rachel Clarke'`.
Mongo `users.full_name` is updated alongside Postgres so the stores keep agreeing.

Deliberately conservative, because the failure mode is mangling somebody's actual name:

* Only a title at the START of the string is taken, and only from a closed list. A name
  containing "Miss" mid-string, or a surname that happens to resemble a title, is left
  alone.
* A title must be followed by at least two more characters, so a lone "Dr" — which may
  BE the whole stored name for a record with nothing else — is never stripped to empty.
* Records that already have a salutation are skipped, so a re-run cannot double-strip
  ("Ms Ms Clarke" is not a state this can reach).
* Nothing is invented. A person with no honorific in their name keeps salutation NULL
  rather than being assigned one from their title-less name or inferred from gender.
* JOINT parties are skipped entirely. `Dr Gunjan Pandey & Dr Rinku Pandey` is two people
  in one row: taking the leading title would assign Gunjan's honorific to the pair and
  strand Rinku's mid-string as `Gunjan Pandey & Dr Rinku Pandey`. A joint holding needs
  the party split into two, which is a different repair — this reports them instead.

The point of the split is that anything DERIVING a value from a name — an email local
part, a sort key, a greeting — otherwise has to strip the title itself, and every place
that does so is a separate chance to forget. The mailbox derivation added the same day
needed exactly that strip to avoid producing `ua001.mr.han@…`.
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
logger = logging.getLogger("backfill_salutations")

BUILDING = "13195"
TENANT = "9e9d75c2-bd92-4695-8487-1592018c3af9"

# Closed list, anchored at the start, requiring a real name after it. Canonical spelling
# is what gets stored, so "MS"/"ms."/"Ms" all persist as "Ms".
_CANON = {"mr": "Mr", "mrs": "Mrs", "ms": "Ms", "miss": "Miss",
          "dr": "Dr", "prof": "Prof", "sir": "Sir", "madam": "Madam"}
_TITLE = re.compile(r"^(mr|mrs|ms|miss|dr|prof|sir|madam)\.?\s+(?=\S{2,})", re.I)


# Two names in one row. A single salutation column cannot describe them.
_JOINT = re.compile(r"\s(&|and)\s", re.I)


def split_title(name: str) -> tuple[str | None, str]:
    """('Ms Rachel Clarke') -> ('Ms', 'Rachel Clarke'); leaves anything else untouched."""
    raw = (name or "").strip()
    if _JOINT.search(raw):
        return None, raw
    m = _TITLE.match(raw)
    if not m:
        return None, raw
    return _CANON[m.group(1).lower()], raw[m.end():].strip()


async def main(args) -> int:
    set_ctx_building_id(BUILDING)
    pg = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    try:
        await pg.execute(f"SET app.tenant_id = '{TENANT}'")

        plans = {"core.parties": [], "core.users": []}
        joint: list[tuple[str, str]] = []
        for table, col, key in (("core.parties", "legal_name", "party_id"),
                                ("core.users", "full_name", "user_id")):
            for r in await pg.fetch(
                    f"SELECT {key} AS id, {col} AS name FROM {table} "
                    f"WHERE {col} IS NOT NULL AND salutation IS NULL"):
                title, cleaned = split_title(r["name"])
                if title:
                    plans[table].append((r["id"], r["name"], title, cleaned))
                elif _JOINT.search(r["name"] or "") and _TITLE.match((r["name"] or "").strip()):
                    joint.append((table, r["name"]))

        for table, rows in plans.items():
            logger.info("%s — %s record(s) with an embedded honorific", table, len(rows))
            for _, original, title, cleaned in rows[:5]:
                logger.info("    %-30s -> salutation=%-5s name=%s",
                            original[:30], title, cleaned)
            if len(rows) > 5:
                logger.info("    ... and %s more", len(rows) - 5)

        if joint:
            logger.info("%s joint-party record(s) SKIPPED — two people in one row, which "
                        "one salutation cannot describe. These need the party split, not "
                        "a salutation:", len(joint))
            for table, name in joint[:6]:
                logger.info("    %-14s %s", table.split(".")[-1], name[:58])
            if len(joint) > 6:
                logger.info("    ... and %s more", len(joint) - 6)

        if not args.apply:
            total = sum(len(v) for v in plans.values())
            logger.info("DRY-RUN — would update %s record(s).", total)
            return 0

        for pid, _, title, cleaned in plans["core.parties"]:
            await pg.execute(
                "UPDATE core.parties SET salutation=$1, legal_name=$2 WHERE party_id=$3",
                title, cleaned, pid)
        for uid, original, title, cleaned in plans["core.users"]:
            await pg.execute(
                "UPDATE core.users SET salutation=$1, full_name=$2 WHERE user_id=$3",
                title, cleaned, uid)
            # Keep Mongo in step — matched on the ORIGINAL name, which is still what it
            # holds at this point.
            await db.users.update_one({"full_name": original},
                                      {"$set": {"full_name": cleaned, "salutation": title}})

        logger.info("APPLIED: %s part%s, %s user(s)",
                    len(plans["core.parties"]),
                    "y" if len(plans["core.parties"]) == 1 else "ies",
                    len(plans["core.users"]))
        return 0
    finally:
        await pg.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args())))
