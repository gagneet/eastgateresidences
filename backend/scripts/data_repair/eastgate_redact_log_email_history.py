#!/usr/bin/env python3
# @featuretrace:east-gate-data-restore — Remove real owner addresses from global log collections.
# Layer: script
# Data flow: backup users + live users -> original->current map -> email_sent_log / login_audit_logs (global).
# Related: backend/scripts/data_repair/eastgate_neutralise_external_emails.py
#          backend/utils/email_suppression.py
"""Rewrite real East Gate owner addresses out of the global log collections.

    python3 scripts/data_repair/eastgate_redact_log_email_history.py --dry-run
    python3 scripts/data_repair/eastgate_redact_log_email_history.py --apply

The 2026-08-27 user-record pass rewrote ~100 real addresses onto the building domain,
but it swept with a `building_id` filter. `email_sent_log` and `login_audit_logs` are
GLOBAL collections — they carry no `building_id` — so they were never examined, and 66
real owner addresses stayed behind in them.

Rewritten, not deleted, and to each person's CURRENT address rather than a placeholder.
These are audit records: a levy notice sent to an owner in March is a fact, and blanking
the recipient destroys the trail while keeping the row. Mapping the old address to the
one that owner now holds keeps every entry attributable to the same person, so the
history stays readable while the real address stops existing here.

Scope is deliberately narrow: ONLY addresses that were East Gate owner addresses in the
2026-08-21 export. Demo and fixture addresses (`manager@acmestrata.demo`,
`u1@example.com`, `chair@stratademo.au`) belong to other tenants or to test data, are not
real people, and rewriting them would corrupt those trails for no privacy gain.
"""

import argparse
import asyncio
import gzip
import logging
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from bson import json_util  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("redact_log_emails")

BACKUP = Path("/home/gagneet/_archive/strataos-backups/eastgate-13195-20260821T115020Z")
DOMAIN = "eastgateresidences.com.au"
BUILDING = "13195"

# Log collections and the field holding a recipient/actor address. Both are GLOBAL,
# which is exactly why the building-scoped sweep missed them.
LOG_TARGETS = [("email_sent_log", "to_email"), ("login_audit_logs", "email")]

# Kept for the same reasons as the user-record pass, so the two agree:
#   * the sole super_admin — the operator's own login, excluded by role there
#   * three owners who self-registered and still sign in with these addresses
# All four are unmailable regardless: EMAIL_ALLOWED_DOMAINS refuses anything off the
# building domain.
EXEMPT = {
    "gagneet@silverfoxtechnologies.com.au",
    "riyuroy@gmail.com",
    "adityashouvik@gmail.com",
    "avneetrooprai@gmail.com",
}


def _slug(local: str) -> str:
    s = re.sub(r"[^a-z0-9._-]+", ".", local.lower()).strip(".")
    return re.sub(r"\.{2,}", ".", s)[:48] or "owner"


async def build_map(db) -> dict[str, str]:
    """original address -> the address that person holds now.

    Preferring the live record keeps the logs consistent with the user table. Where a
    backup user has no live counterpart, the same slug rule the user-record pass used is
    applied, so an address rewritten in both places lands on the same value.
    """
    backup_users = json_util.loads(gzip.open(BACKUP / "mongo" / "users.json.gz", "rb").read())
    bid_filter = {"$or": [{"building_id": BUILDING}, {"plan_id": BUILDING}]}

    live_by_id = {}
    async for u in db.users.find(bid_filter):
        if u.get("id"):
            live_by_id[u["id"]] = u

    unit_of = {}
    async for unit in db.units.find(bid_filter):
        for f in ("owner_email", "owner_email_b"):
            if unit.get(f) and unit.get("unit_number"):
                unit_of.setdefault(str(unit[f]).strip().lower(), str(unit["unit_number"]))

    mapping: dict[str, str] = {}
    for bu in backup_users:
        original = (bu.get("email") or "").strip().lower()
        if not original or original.endswith("@" + DOMAIN) or original in EXEMPT:
            continue
        live = live_by_id.get(bu.get("id")) or {}
        current = (live.get("email") or "").strip()
        if not current or not current.lower().endswith("@" + DOMAIN):
            unit = unit_of.get(original) or (bu.get("unit_number") or "")
            base = _slug(original.split("@")[0])
            local = f"{str(unit).lower()}.{base}" if unit else base
            current = f"{local}@{DOMAIN}"
        mapping[original] = current
    return mapping


async def main(args) -> int:
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    try:
        mapping = await build_map(db)
        logger.info("%s original East Gate owner address(es) in scope", len(mapping))

        total = 0
        for coll, field in LOG_TARGETS:
            present = {}
            for original in mapping:
                n = await db[coll].count_documents({field: original})
                if n:
                    present[original] = n
            logger.info("%s.%s — %s distinct real address(es) across %s row(s)",
                        coll, field, len(present), sum(present.values()))
            for original, n in sorted(present.items(), key=lambda kv: -kv[1])[:6]:
                logger.info("    %-40s %4s row(s) -> %s", original, n, mapping[original])
            if len(present) > 6:
                logger.info("    ... and %s more address(es)", len(present) - 6)

            if args.apply:
                for original in present:
                    res = await db[coll].update_many(
                        {field: original},
                        {"$set": {field: mapping[original], "email_redacted": True}},
                    )
                    total += res.modified_count
            else:
                total += sum(present.values())

        logger.info("%s: %s row(s) %s", "APPLIED" if args.apply else "DRY-RUN", total,
                    "rewritten" if args.apply else "would be rewritten")
        if not args.apply:
            logger.info("Re-run with --apply to rewrite them.")
        return 0
    finally:
        cli.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args())))
