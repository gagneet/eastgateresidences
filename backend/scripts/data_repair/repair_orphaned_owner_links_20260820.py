#!/usr/bin/env python3
# NOTE — building-agnostic tool; --building-id defaults to "13195" (East Gate Residences,
# Unit Plan 13195) only because that is where the orphan was found. The identity it restores
# is read from that building's own records; nothing here is hardcoded to East Gate.
# @featuretrace:owner-transfers — Restore the deleted user account behind an active but
# orphaned user_units owner link, so the unit has a resolvable canonical owner again.
# Layer: migration
# Data flow: user_units (active, orphaned) + owner_transfer_requests + units
#            -> users (recreated with the ORIGINAL id) + memberships
#            -> owner_transfer_requests (audit row), all building-scoped.
# Related: backend/services/ownership_transfer_detection_service.py
#          backend/scripts/data_repair/fix_co_owner_addition_transfer_requests_20260820.py
#          backend/scripts/data_repair/bootstrap_initial_owner_links_20260819.py
"""
Repair active `user_units` owner links whose `users` row no longer exists.

Why this matters
----------------
`owner_service.py` resolves a unit's canonical owner by joining an active
`user_units` row to `users`. When the user row is gone the join yields nothing,
so the unit reads as having no canonical owner — while still counting as
"linked" to every tool that only checks for a `user_units` row. That is the
worst of both states: `create_initial_ownership_link` skips the unit
("owner_already_canonical") and `link_missing_co_owners` refuses it
("unresolvable_existing_owner_link", because it cannot tell which owners are
already attached), so nothing can complete the unit until the orphan is fixed.

East Gate case (2026-08-20): UA038 had one active owner link to
`13f13588-4dde-4090-93e9-2b256c5c9ee6`, created by a data_repair run on
2026-05-05 with `start_date=2020-12-01`, following an approved 2026-04-23
transfer from Kikham Sikoulabot. Both users' rows were later hard-deleted by
something outside this feature, leaving the link dangling and three
`user_notifications` addressed to a user id that no longer resolved.

How identity is established (evidence, not guesswork)
-----------------------------------------------------
The orphan's email comes from the approved `owner_transfer_requests` row that
created it (`new_owner.user_id == <orphan id>`). That email is then matched
against the unit's own `owner_email` / `owner_email_b` to pick the paired
`owner_name` / `owner_name_b`. Both sources must agree.

Where no transfer record exists, a single fallback applies: if the unit has
exactly ONE imported owner name and exactly ONE active owner link, the mapping
is unambiguous. Anything else is reported and skipped — the script never
guesses which of several names an orphan was, and never invents an email.

What it writes
--------------
Writes MongoDB only. `users` / `user_units` / `memberships` live in Mongo; the
Postgres ownership record (`core.parties` / `core.ownership_periods`) is a
separate store that this repair does not touch. For a promoted building that
store is the one that SERVES owner reads, so check it before assuming a unit is
broken there too — East Gate's UA038 already held both owners in Postgres while
its Mongo link was orphaned.

The `users` row is recreated with the ORIGINAL id, which also re-resolves any
notifications and audit references that still point at it. The account is
provisional and INACTIVE (`is_active=False`, `requires_account_setup=True`) —
the same shape the ownership bootstrap uses. This script never re-activates an
account, never sets a password the owner could use, and never emails anyone.
The existing `user_units` link is left exactly as it is: its `start_date`,
`is_primary` flag and approval provenance are real history.

Usage:
    # Dry run (default) — prints what would be restored, no writes.
    python3 backend/scripts/data_repair/repair_orphaned_owner_links_20260820.py \
        --building-id 13195

    # Apply.
    python3 backend/scripts/data_repair/repair_orphaned_owner_links_20260820.py \
        --building-id 13195 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from database import db  # noqa: E402
from utils.auth import hash_password  # noqa: E402
from models.timestamps import timestamp_sort_key  # noqa: E402
from services.ownership_transfer_detection_service import (  # noqa: E402
    INTERNAL_OWNER_EMAIL_DOMAIN,
    ensure_owner_membership,
)

ORPHAN_LINK_REPAIR_SOURCE = "orphaned_owner_link_repair"


def _normalise_email(value: str | None) -> str:
    return (value or "").strip().lower()


async def _find_orphaned_owner_links(building_id: str) -> list[dict]:
    """Active owner links in this building whose users row is missing."""
    links = await db._db["user_units"].find(
        {
            "building_id": building_id,
            "role_at_unit": "owner",
            "is_active": True,
        },
        {"_id": 0, "id": 1, "user_id": 1, "unit_number": 1, "is_primary": 1, "start_date": 1},
    ).to_list(5000)

    user_ids = sorted({link["user_id"] for link in links if link.get("user_id")})
    if not user_ids:
        return []
    known = await db._db["users"].find(
        {"id": {"$in": user_ids}}, {"_id": 0, "id": 1}
    ).to_list(len(user_ids))
    known_ids = {user["id"] for user in known}
    return [link for link in links if link.get("user_id") not in known_ids]


async def _evidence_for(building_id: str, link: dict) -> dict:
    """Resolve the orphan's real name + email from the building's own records.

    Returns a dict with ``resolved`` True/False and, when False, the reason —
    never a guessed identity.
    """
    unit_number = link.get("unit_number")
    orphan_id = link.get("user_id")

    unit = await db._db["units"].find_one(
        {"building_id": building_id, "unit_number": unit_number},
        {"_id": 0, "owner_name": 1, "owner_name_b": 1, "owner_email": 1, "owner_email_b": 1},
    )
    if not unit:
        return {"resolved": False, "reason": "unit_not_found"}

    pairs = [
        (unit.get("owner_name"), unit.get("owner_email")),
        (unit.get("owner_name_b"), unit.get("owner_email_b")),
    ]
    named_pairs = [(name, email) for name, email in pairs if (name or "").strip()]
    if not named_pairs:
        return {"resolved": False, "reason": "unit_has_no_imported_owner_name"}

    # Primary evidence: the approved transfer that created this user id.
    transfers = await db._db["owner_transfer_requests"].find(
        {"building_id": building_id, "new_owner.user_id": orphan_id},
        {"_id": 0, "id": 1, "status": 1, "new_owner": 1, "created_at": 1},
    ).to_list(50)
    approved = [
        transfer for transfer in transfers if transfer.get("status") == "approved"
    ]
    # Mixed datetime/str would raise TypeError — see models/timestamps.
    approved.sort(key=lambda transfer: timestamp_sort_key(transfer.get("created_at")))
    transfer_email = _normalise_email(
        (approved[-1].get("new_owner") or {}).get("email") if approved else None
    )

    if transfer_email:
        matches = [
            (name, email)
            for name, email in named_pairs
            if _normalise_email(email) == transfer_email
        ]
        if len(matches) == 1:
            return {
                "resolved": True,
                "full_name": matches[0][0].strip(),
                "email": transfer_email,
                "basis": "approved_transfer_email_matches_unit_owner_email",
                "transfer_id": approved[-1].get("id"),
            }
        return {
            "resolved": False,
            "reason": (
                "transfer_email_matches_no_unit_owner_email"
                if not matches
                else "transfer_email_matches_multiple_unit_owners"
            ),
            "transfer_email": transfer_email,
        }

    # Fallback: unambiguous only when the unit has one name and one owner link.
    active_link_count = await db._db["user_units"].count_documents(
        {
            "building_id": building_id,
            "unit_number": unit_number,
            "role_at_unit": "owner",
            "is_active": True,
        }
    )
    if len(named_pairs) == 1 and active_link_count == 1:
        name, email = named_pairs[0]
        return {
            "resolved": True,
            "full_name": name.strip(),
            "email": _normalise_email(email) or None,
            "basis": "sole_imported_owner_name_and_sole_active_link",
        }

    return {
        "resolved": False,
        "reason": "ambiguous_no_transfer_evidence",
        "imported_owner_names": [name for name, _ in named_pairs],
        "active_owner_links": active_link_count,
    }


async def run(building_id: str, apply: bool, unit_number: str | None = None) -> dict:
    """Restore the deleted account behind each orphaned active owner link."""
    orphans = await _find_orphaned_owner_links(building_id)
    if unit_number:
        orphans = [link for link in orphans if link.get("unit_number") == unit_number]
    orphans.sort(key=lambda link: link.get("unit_number") or "")

    now = datetime.now(timezone.utc).isoformat()
    repaired, skipped = [], []

    for link in orphans:
        evidence = await _evidence_for(building_id, link)
        entry = {
            "unit_number": link.get("unit_number"),
            "orphan_user_id": link.get("user_id"),
            "link_id": link.get("id"),
            "is_primary": link.get("is_primary"),
            **evidence,
        }
        if not evidence.get("resolved"):
            skipped.append(entry)
            continue
        if not apply:
            entry["would_repair"] = True
            repaired.append(entry)
            continue

        user_id = link["user_id"]
        email = evidence.get("email") or (
            f"owner-transfer+{user_id}@{INTERNAL_OWNER_EMAIL_DOMAIN}"
        )
        # Recreated with the ORIGINAL id so existing notifications and audit
        # references resolve again. Inactive and provisional: this restores an
        # identity record, it does not restore account access.
        await db._db["users"].insert_one(
            {
                "id": user_id,
                "email": email,
                "full_name": evidence["full_name"],
                "first_name": "",
                "last_name": "",
                "role": "owner",
                "building_id": building_id,
                "unit_number": link.get("unit_number"),
                "is_approved": False,
                "is_active": False,
                "status": "pending_owner_transfer",
                "requires_account_setup": True,
                "password_hash": hash_password(secrets.token_urlsafe(32)),
                "portal_detected_owner": not bool(evidence.get("email")),
                "is_internal_contact_email": not bool(evidence.get("email")),
                "restored_from_orphaned_link": True,
                "bootstrap_source": ORPHAN_LINK_REPAIR_SOURCE,
                "created_at": now,
                "updated_at": now,
            }
        )
        await ensure_owner_membership(
            db,
            building_id,
            link.get("unit_number"),
            user_id,
            now,
            is_primary=bool(link.get("is_primary")),
        )
        # Deterministic id so a second restoration of the same user (were the row
        # ever deleted again) records one audit entry, not a duplicate pair.
        audit_id = f"{ORPHAN_LINK_REPAIR_SOURCE}:{user_id}"
        existing_audit = await db._db["owner_transfer_requests"].find_one(
            {"id": audit_id}, {"_id": 0, "id": 1}
        )
        if existing_audit:
            entry["repaired"] = True
            entry["audit_id"] = audit_id
            entry["audit_already_recorded"] = True
            repaired.append(entry)
            continue
        await db._db["owner_transfer_requests"].insert_one(
            {
                "id": audit_id,
                "building_id": building_id,
                "unit_number": link.get("unit_number"),
                "old_owners": [],
                "new_owner": {
                    "user_id": user_id,
                    "full_name": evidence["full_name"],
                    "email": email,
                    "is_provisional": True,
                    "is_internal_contact_email": not bool(evidence.get("email")),
                },
                "settlement_date": link.get("start_date") or now[:10],
                "request_notes": (
                    "Data-integrity repair, NOT an ownership change: an active user_units "
                    f"owner link for this unit referenced deleted user {user_id}. The "
                    f"account was recreated with its original id as {evidence['full_name']} "
                    f"({email}) on the basis '{evidence['basis']}'. No ownership was "
                    "transferred and no existing link was modified."
                ),
                "ownership_documents": [],
                "ownership_verified": False,
                "status": "approved",
                "required_approvals": 1,
                "current_approvals": 1,
                "approval_mode": "auto_bootstrap",
                "approval_history": [
                    {
                        "action": "orphaned_owner_link_repaired",
                        "by": f"system:{ORPHAN_LINK_REPAIR_SOURCE}",
                        "at": now,
                        "notes": "Auto-approved — restores a deleted identity record only.",
                    }
                ],
                "pending_approval_action": None,
                "requested_date": now,
                "submitted_by_id": f"system:{ORPHAN_LINK_REPAIR_SOURCE}",
                "submitted_by_name": "Orphaned Owner Link Repair",
                "submitted_by_role": "system",
                "reviewed_by": None,
                "reviewed_by_name": f"system:{ORPHAN_LINK_REPAIR_SOURCE}",
                "reviewed_date": now,
                "review_notes": "Auto-approved — restores a deleted identity record only.",
                "action_taken": "orphaned_owner_link_repaired",
                "old_owners_notified": False,
                "new_owner_notified": False,
                "source": ORPHAN_LINK_REPAIR_SOURCE,
                "created_at": now,
                "updated_at": now,
            }
        )
        entry["repaired"] = True
        entry["audit_id"] = audit_id
        repaired.append(entry)

    return {
        "building_id": building_id,
        "unit_number": unit_number,
        "apply": apply,
        "orphaned_links_found": len(orphans),
        "repaired": repaired,
        "skipped_needs_manual_review": skipped,
    }


def main() -> int:
    """CLI entry point. Dry-run by default; --apply writes."""
    parser = argparse.ArgumentParser(
        description="Restore deleted user accounts behind orphaned active owner links."
    )
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--unit-number", help="Optional single unit, e.g. UA038")
    parser.add_argument("--apply", action="store_true", help="Write changes. Default is dry-run.")
    args = parser.parse_args()

    result = asyncio.run(run(args.building_id, args.apply, args.unit_number))
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
