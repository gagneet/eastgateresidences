"""
migration_011_encrypt_email_credentials.py

GAP-SEC-001: Encrypt plaintext email credentials in the email_settings collection.

Affected fields: smtp_password, mail_password, resend_api_key, sendgrid_api_key, migadu_api_key

Safe to re-run — already-encrypted values (prefixed 'enc:') are skipped.

Run:
    cd backend && python3 scripts/migrations/migration_011_encrypt_email_credentials.py
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database import db
from utils.crypto import encrypt_sensitive, is_encrypted

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_CRED_FIELDS = ("smtp_password", "mail_password", "resend_api_key", "sendgrid_api_key", "migadu_api_key")


async def run():
    """Generated function header.

    Function: run
    Path: backend/scripts/migrations/migration_011_encrypt_email_credentials.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    docs = await db._db.email_settings.find({}, {"_id": 0}).to_list(None)
    updated = 0
    skipped = 0

    for doc in docs:
        patch = {}
        for field in _CRED_FIELDS:
            val = doc.get(field)
            if val and not is_encrypted(val):
                patch[field] = encrypt_sensitive(val)

        if not patch:
            skipped += 1
            continue

        filter_key = {"id": doc["id"]} if doc.get("id") else {"building_id": doc.get("building_id")}
        await db._db.email_settings.update_one(filter_key, {"$set": patch})
        logger.info("Encrypted %d field(s) in doc %s", len(patch), filter_key)
        updated += 1

    logger.info("Done. %d docs updated, %d skipped (already encrypted or no credentials).", updated, skipped)


if __name__ == "__main__":
    asyncio.run(run())
