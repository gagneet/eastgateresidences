#!/usr/bin/env python3
# @featuretrace:owner-activation — Give empty-hash accounts a placeholder password behind the activation gate.
# Layer: script
# Data flow: core.users (password_hash='') -> bcrypt hash + requires_activation -> core.users + Mongo users (building-scoped).
# Related: backend/scripts/data_repair/eastgate_assign_owner_mailboxes.py
#          backend/routers/auth.py (the activation gate)
"""Set a placeholder password on accounts created with an empty hash.

    EASTGATE_PLACEHOLDER_PASSWORD='...' python3 scripts/data_repair/eastgate_set_placeholder_passwords.py --dry-run
    EASTGATE_PLACEHOLDER_PASSWORD='...' python3 scripts/data_repair/eastgate_set_placeholder_passwords.py --apply

The password is read from the environment and NEVER written into this repository. A
shared constant committed to source is precisely how 1,772 active super_admin accounts
came to share one password in this platform's own history (CLAUDE.md, `is_test_data`
section); the lesson of that incident is that the credential outlived the context which
made it look harmless.

SAFETY: every account touched here is left with `requires_activation = TRUE`, so the
password does not produce a usable login. `/auth/login` refuses an unactivated account
with 403 `activation_required` AFTER verifying the password, so a correct password on a
gated account still opens nothing. The placeholder exists so these rows hold a real
bcrypt hash instead of an empty string — it is not a way in.

That gate is the entire safety argument, so this ENFORCES it rather than assuming it: any
account it touches that is not already gated is gated in the same run. One such account
was found on 2026-08-27 — Tavis Christian Hamer (TH078), whose placeholder address began
`th078.owner-transfer.` rather than `owner-transfer.`, so the start-anchored regex in the
mailbox pass skipped him and he kept an ungated empty hash.
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from database import db  # noqa: E402
from request_context import set_ctx_building_id  # noqa: E402
from utils.auth import hash_password  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("placeholder_passwords")

BUILDING = "13195"
TENANT = "9e9d75c2-bd92-4695-8487-1592018c3af9"
ENV_VAR = "EASTGATE_PLACEHOLDER_PASSWORD"


async def main(args) -> int:
    password = os.getenv(ENV_VAR, "")
    if not password:
        logger.error("%s is not set. Export it for this command only — it must not be "
                     "committed, and does not belong in .env either.", ENV_VAR)
        return 2

    set_ctx_building_id(BUILDING)
    pg = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    try:
        await pg.execute(f"SET app.tenant_id = '{TENANT}'")
        rows = await pg.fetch("""
            SELECT email, full_name, requires_activation
              FROM core.users
             WHERE password_hash = ''
             ORDER BY email
        """)
        ungated = [r for r in rows if not r["requires_activation"]]

        logger.info("%s account(s) with an empty password hash", len(rows))
        if ungated:
            logger.info("%s NOT gated by activation — they will be gated here:", len(ungated))
            for r in ungated:
                logger.info("    GATE  %-28s %s", str(r["full_name"])[:28], r["email"])

        if not args.apply:
            logger.info("DRY-RUN — would set a placeholder hash on %s account(s) and "
                        "gate %s.", len(rows), len(ungated))
            return 0

        gated = 0
        for r in rows:
            # bcrypt salts per call, so identical passwords still produce distinct
            # hashes and the rows do not advertise that they share a secret.
            await pg.execute("""
                UPDATE core.users
                   SET password_hash = $1, requires_activation = TRUE, updated_at = NOW()
                 WHERE email = $2
            """, hash_password(password), r["email"])
            if not r["requires_activation"]:
                gated += 1
            # Mirror to Mongo so the stores agree about the account. Login reads Postgres
            # first but falls back, and a split identity is worse than either state.
            await db.users.update_one({"email": r["email"]},
                                      {"$set": {"password_hash": hash_password(password)}})

        logger.info("APPLIED: %s placeholder hash(es) set, %s newly gated. Every account "
                    "touched has requires_activation=TRUE, so none can be signed into "
                    "with this password.", len(rows), gated)
        return 0
    finally:
        await pg.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args())))
