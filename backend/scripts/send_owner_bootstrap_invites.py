#!/usr/bin/env python3
# NOTE — building-agnostic tool; --building-id defaults to "13195" only as a convenience
# for this repo's one production building. The 83 pending owner_invites records currently
# waiting to be sent ARE East Gate Residences (Unit Plan 13195)-specific data (this
# building's real owners) — running --send today only emails those 83 people, regardless
# of building-id default. Safe to reuse for another building once it has its own pending
# owner_invites rows.
# @featuretrace:owner-transfers — Manual trigger: send the actual invite emails for owner_invites
# records created by create_owner_bootstrap_invites_20260819.py. Run this whenever the platform and
# the target building are ready — NOT part of the bootstrap itself.
# Layer: script
# Related: backend/services/ownership_transfer_detection_service.py
#          backend/scripts/data_repair/create_owner_bootstrap_invites_20260819.py
#          tasks/archive/GAP-IDENTITY-OWNER-BOOTSTRAP-001-canonical-owner-bootstrap.md
"""
Send owner invite emails for pending owner_invites records.

This is the manual trigger referred to in GAP-IDENTITY-OWNER-BOOTSTRAP-001:
ownership bootstrapping (creating the canonical user_units link + a
provisional, inactive account) and inviting an owner to actually log in are
two separate actions. This script performs the second one, on demand,
whenever the platform/building is ready for real owners to receive email.

For each pending invite it:
  1. Generates a fresh 72-hour password-reset token (generated NOW, at send
     time — not when the invite was created, so it can't expire sitting in
     "pending" for weeks/months).
  2. Sends the same "owner_transfer_invite" email template already used by
     the manual owner-transfer approval flow (server.py
     _finalize_owner_transfer_approval), so recipients see a consistent
     message regardless of which path added them.
  3. Marks the user account active (status="active", requires_account_setup
     stays True — they still set their own password) and the invite
     "sent", so a re-run never double-sends.

Default is dry-run — prints exactly who would be emailed, with no email
provider call and no writes. Requires --send to actually fire.

Usage:
    # See who is queued.
    python3 backend/scripts/send_owner_bootstrap_invites.py --building-id 13195

    # Send everyone queued.
    python3 backend/scripts/send_owner_bootstrap_invites.py --building-id 13195 --send

    # Send a bounded batch (staged rollout), or just one unit.
    python3 backend/scripts/send_owner_bootstrap_invites.py --building-id 13195 --send --limit 10
    python3 backend/scripts/send_owner_bootstrap_invites.py --building-id 13195 --send --unit-number TH072
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from database import db  # noqa: E402
from services.ownership_transfer_detection_service import OWNER_INVITE_STATUS_SENT  # noqa: E402
from utils.email import get_email_template, send_email_async  # noqa: E402


async def run(building_id: str, send: bool, unit_number: str | None, limit: int | None) -> dict:
    query = {"building_id": building_id, "status": "pending"}
    if unit_number:
        query["unit_number"] = unit_number

    invites = await db.owner_invites.find(query, {"_id": 0}).sort("unit_number", 1).to_list(1000)
    if limit:
        invites = invites[:limit]

    now = datetime.now(timezone.utc).isoformat()
    frontend_url = os.getenv("FRONTEND_URL", "https://www.eastgateresidences.com.au").rstrip("/")
    sent, would_send, failed = [], [], []

    for invite in invites:
        email = invite.get("email")
        unit = invite.get("unit_number")
        user_id = invite.get("user_id")
        if not email or not user_id:
            continue

        if not send:
            would_send.append({"unit_number": unit, "email": email, "full_name": invite.get("full_name")})
            continue

        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat()
        await db.password_resets.insert_one(
            {
                "token": token,
                "user_id": user_id,
                "email": email,
                "expires_at": expires_at,
                "used": False,
                "building_id": building_id,
            }
        )
        invite_link = f"{frontend_url}/reset-password?token={token}"
        html_body, text_body = get_email_template(
            "owner_transfer_invite", invite_link=invite_link, unit_number=unit,
        )
        result = await send_email_async(
            email,
            f"You've been added as an owner — Unit {unit}",
            html_body,
            text_body,
            context="owner_bootstrap_invite",
        )

        if not result or not result.get("success"):
            # Email genuinely failed to send (provider error, no provider configured, etc.)
            # — send_email_async returns {"success": False, ...} rather than raising, so this
            # must be checked explicitly. Do NOT flip is_active or mark the invite "sent": an
            # active-but-unreachable account is worse than a still-pending one. Leaving the
            # invite's status untouched ("pending") means the next --send run retries it
            # automatically — no separate "failed" state or manual requeue needed.
            failed.append({"unit_number": unit, "email": email, "email_result": result})
            continue

        # is_active must flip True here — routers/auth.py login (~line 1698) rejects
        # `is_active=False` with 401 "Account is deactivated" regardless of whether the
        # password reset succeeds. Accounts are created with is_active=False by
        # _ensure_bootstrap_owner_user() precisely so they can't be used before an invite
        # is actually sent; this is the one place that must undo it.
        await db.users.update_one(
            {"id": user_id, "building_id": building_id},
            {"$set": {"status": "active", "is_active": True, "updated_at": now, "bootstrap_invite_sent_at": now}},
        )
        await db.owner_invites.update_one(
            {"id": invite["id"], "building_id": building_id},
            {"$set": {"status": OWNER_INVITE_STATUS_SENT, "sent_at": now, "updated_at": now}},
        )
        sent.append({"unit_number": unit, "email": email, "email_result": result})

    return {
        "building_id": building_id,
        "send": send,
        "pending_found": len(invites),
        "sent": len(sent),
        "would_send": len(would_send),
        "failed": len(failed),
        "results": sent if send else would_send,
        "failed_detail": failed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send pending owner-bootstrap invite emails. Default is dry-run; --send actually emails."
    )
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--unit-number", help="Optional single unit, e.g. TH072")
    parser.add_argument("--limit", type=int, help="Send at most N invites (staged rollout)")
    parser.add_argument("--send", action="store_true", help="Actually send emails. Default is dry-run.")
    args = parser.parse_args()
    result = asyncio.run(run(args.building_id, args.send, args.unit_number, args.limit))
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
