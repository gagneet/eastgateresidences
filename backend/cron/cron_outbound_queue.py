#!/usr/bin/env python3
# @featuretrace:outbound-message-queue — Worker tick: expire, gate, claim, transmit.
# Layer: cron
# Data flow: outbound_messages (held) -> sendable_reason -> claim_for_send -> send_email_async(_from_worker=True) (scope param: building|global).
# Related: backend/services/outbound_queue_service.py
#          backend/utils/email.py
#          tasks/GAP-COMMS-003-outbound-message-queue-and-activation.md
"""Move the outbound queue forward by one tick.

    cd backend && python3 cron/cron_outbound_queue.py            # one pass, all buildings
    cd backend && python3 cron/cron_outbound_queue.py --building 13195
    cd backend && python3 cron/cron_outbound_queue.py --dry-run  # decide, never transmit

Run it from a systemd timer or PM2 cron like the rest of `backend/cron/` — there is no
in-process scheduler in this codebase (CLAUDE.md).

Why the gates are re-read every tick rather than captured at enqueue: that is the whole
mechanism behind "enable email within 48 hours and the held mail goes out". A message
blocked by a disabled queue is not failed and not dropped; it is simply not due yet, and
the next tick after an operator enables the queue releases it.

Ordering inside a tick matters. Expiry runs FIRST, so a message whose window closed can
never be transmitted by the same pass that was about to release it.
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from database import db  # noqa: E402
from models.outbound_message import MessageStatus  # noqa: E402
from request_context import set_ctx_building_id  # noqa: E402
from services.outbound_queue_service import (  # noqa: E402
    COLLECTION,
    MAX_ATTEMPTS,
    claim_for_send,
    expire_stale,
    get_queue_settings,
    mark_attempt_failed,
    mark_sent,
    return_to_held,
    sendable_reason,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cron_outbound_queue")


async def _buildings_with_pending() -> list[str]:
    """Buildings holding at least one non-terminal message.

    Uses the raw collection rather than the tenant-scoped wrapper: this is a
    cross-building sweep, so it must deliberately step outside the per-building
    scoping the wrapper enforces, then set the context per building below.
    """
    raw = db._db[COLLECTION] if hasattr(db, "_db") else db[COLLECTION]
    return await raw.distinct("building_id", {
        "status": {"$in": [MessageStatus.HELD.value, MessageStatus.SENDING.value]}
    })


async def process_building(building_id: str, *, dry_run: bool = False) -> dict:
    """One tick for one building. Returns a counter dict for the caller to report."""
    set_ctx_building_id(building_id)
    stats = {"building_id": building_id, "expired": 0, "sent": 0, "failed": 0,
             "held": 0, "suppressed": 0}

    # Expire first: a message past its window must not be released by this same pass.
    stats["expired"] = await expire_stale(building_id)

    settings = await get_queue_settings(building_id)
    now = datetime.now(timezone.utc)

    pending = await db[COLLECTION].find(
        {"status": MessageStatus.HELD.value}
    ).to_list(500)

    for msg in pending:
        ok, why = sendable_reason(msg, settings, now=now)
        if not ok:
            stats["held"] += 1
            logger.debug("holding %s: %s", msg.get("id"), why)
            continue

        if dry_run:
            stats["sent"] += 1
            logger.info("[dry-run] would send %s to %s (%s)",
                        msg.get("id"), msg.get("to_email"), msg.get("context"))
            continue

        # Atomic HELD -> SENDING. If this returns False another worker took it, or an
        # operator cancelled it between the read above and now — either way, leave it.
        if not await claim_for_send(msg["id"]):
            stats["held"] += 1
            continue

        from utils.email import send_email_async

        try:
            # _from_worker=True is what makes this a transmit rather than a re-enqueue.
            result = await send_email_async(
                msg.get("to_email", ""),
                msg.get("subject", ""),
                msg.get("html_body", ""),
                msg.get("text_body"),
                context=msg.get("context", ""),
                building_id=building_id,
                _from_worker=True,
            )
            if result.get("success"):
                await mark_sent(msg["id"], provider=str(result.get("provider", "")))
                stats["sent"] += 1
            elif result.get("suppressed"):
                # SUPPRESSED IS NOT FAILED. The kill switch or the domain allowlist
                # refused this message, and both are policies that can change — lifting
                # EMAIL_SEND_DISABLED_ALL, or adding a domain, should release everything
                # still inside its window.
                #
                # Counting it as an attempt would burn the 3-attempt budget in 90 seconds
                # on a 30s tick and mark the message FAILED, which is both untrue and
                # unrecoverable: an operator reviewing the console would see a wall of
                # failures for messages that were correctly held. Returned to HELD with
                # attempts UNCHANGED, so it waits for the policy or for its own expiry.
                await return_to_held(
                    msg["id"], reason=str(result.get("provider") or "suppressed"))
                stats["suppressed"] += 1
            else:
                await mark_attempt_failed(
                    msg["id"], str(result.get("provider") or "send returned no success"),
                    int(msg.get("attempts", 0)) + 1)
                stats["failed"] += 1
        except Exception as exc:
            await mark_attempt_failed(msg["id"], str(exc), int(msg.get("attempts", 0)) + 1)
            stats["failed"] += 1
            logger.warning("send failed for %s: %s", msg.get("id"), exc)

    return stats


async def main(args) -> int:
    buildings = [args.building] if args.building else await _buildings_with_pending()
    if not buildings:
        logger.info("outbound queue: nothing pending")
        return 0

    totals = {"expired": 0, "sent": 0, "failed": 0, "held": 0, "suppressed": 0}
    for bid in buildings:
        s = await process_building(bid, dry_run=args.dry_run)
        for k in totals:
            totals[k] += s[k]
        logger.info("building %s — sent=%s held=%s suppressed=%s expired=%s failed=%s",
                    bid, s["sent"], s["held"], s["suppressed"], s["expired"], s["failed"])

    logger.info("%s: sent=%s held=%s suppressed=%s expired=%s failed=%s (retry budget %s)",
                "DRY-RUN" if args.dry_run else "TICK",
                totals["sent"], totals["held"], totals["suppressed"], totals["expired"],
                totals["failed"], MAX_ATTEMPTS)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--building", help="Process a single building_id")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be sent without transmitting")
    sys.exit(asyncio.run(main(ap.parse_args())))
