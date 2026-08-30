"""
Cleanup: Delete test rental certificate created during April 2026 audit (Step 3.5c).

RC-2026-0001 (id: 34ff86d9-0301-41ae-8792-372acad23d32) was created on 2026-04-24
as the 'before' baseline for the East Gate preservation diff in Step 3.5c. It has
no is_test_data flag and was visible in the admin dashboard throughout the audit
period (2026-04-24 → 2026-04-25) as a pending request for unit UA057 in two places:
  - Summary card amber "Pending Requests" count (1)
  - "Manage Requests" tab listing

Deleted during April 2026 audit closeout — RC-2026-0001 was the preservation-diff
baseline for Step 3.5c. It is not a real owner request and was never issued.

Run:
  cd backend && python3 scripts/cleanup_audit_2026_04_test_rental_cert.py

Dry-run:
  DRY_RUN=1 python3 scripts/cleanup_audit_2026_04_test_rental_cert.py
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))

from pymongo import AsyncMongoClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DRY_RUN = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")

CERT_ID = "34ff86d9-0301-41ae-8792-372acad23d32"
CERT_NUMBER = "RC-2026-0001"
REASON = "audit-2026-04-test-cert: preservation-diff baseline for Step 3.5c, not a real owner request"


async def run():
    """Generated function header.

    Function: run
    Path: backend/scripts/cleanup_audit_2026_04_test_rental_cert.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncMongoClient(os.getenv("MONGO_URL"))
    db = client[os.getenv("DB_NAME", "strata")]

    doc = await db.rental_certificates.find_one(
        {"id": CERT_ID},
        {"_id": 0, "id": 1, "cert_number": 1, "status": 1, "unit_number": 1,
         "building_id": 1, "created_at": 1, "is_test_data": 1},
    )

    if not doc:
        logger.info(f"cert {CERT_ID} not found — already deleted or never existed. Nothing to do.")
        client.close()
        return

    logger.info(
        f"Found: id={doc['id']} cert_number={doc.get('cert_number')} "
        f"unit={doc.get('unit_number')} status={doc.get('status')} "
        f"building_id={doc.get('building_id')} created_at={doc.get('created_at')} "
        f"is_test_data={doc.get('is_test_data')}"
    )
    logger.info(f"  {'[DRY] Would delete' if DRY_RUN else '[DELETE]'}: {CERT_NUMBER} — {REASON}")

    if not DRY_RUN:
        result = await db.rental_certificates.delete_one({"id": CERT_ID})
        if result.deleted_count == 1:
            logger.info(f"Deleted {CERT_NUMBER} (id={CERT_ID})")

            # Audit log entry so the deletion is traceable
            await db.audit_logs.insert_one({
                "action": "delete_test_record",
                "collection": "rental_certificates",
                "record_id": CERT_ID,
                "cert_number": CERT_NUMBER,
                "reason": REASON,
                "deleted_at": datetime.now(timezone.utc).isoformat(),
                "deleted_by": "cleanup_audit_2026_04_test_rental_cert.py",
            })
            logger.info("Audit log entry written.")
        else:
            logger.warning(f"delete_one matched 0 documents — cert may already be gone")

    client.close()


if __name__ == "__main__":
    asyncio.run(run())
