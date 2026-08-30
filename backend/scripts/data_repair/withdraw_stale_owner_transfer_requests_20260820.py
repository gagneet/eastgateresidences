#!/usr/bin/env python3
# NOTE — building-agnostic tool; --building-id defaults to "13195" (East Gate Residences,
# Unit Plan 13195) only because that is where the stale request was found.
# @featuretrace:owner-transfers — Withdraw system-detected transfer requests describing
# owner drift that no longer exists, and archive the accounts they minted.
# Layer: migration
# Data flow: owner_transfer_requests (pending, system-detected) + strata_owners
#            -> ownership_transfer_detection_service (re-run comparison, dry-run)
#            -> owner_transfer_requests.status="withdrawn" + users (soft-archive).
# Related: backend/services/ownership_transfer_detection_service.py
#          backend/scripts/data_repair/fix_co_owner_addition_transfer_requests_20260820.py
#          backend/scripts/data_repair/reconcile_strata_owners_combined_name_20260820.py
"""
Withdraw pending drift requests whose drift no longer exists.

Why this exists
---------------
A request raised by the drift detector is a snapshot of a disagreement between an
imported owner snapshot and the canonical owner baseline, taken at one moment. If
the underlying data is later corrected, the request outlives the disagreement it
describes and sits in the review queue asking for a transfer with no basis.

East Gate UA042 (2026-08-20): `strata_owners.owner` held the junk string
"Test Owner" while every other field and both datastores said "Ms Sarah
Marrapodi". The detector duly raised a transfer FROM "Test Owner" TO Ms Sarah
Marrapodi — who already owned the unit. Correcting the junk field (see
`reconcile_strata_owners_combined_name_20260820.py`) removed the drift but not
the request it had produced.

How staleness is decided
------------------------
By re-running the detector's OWN comparison against current data, in dry-run,
with the cutover-aware baseline — never by reimplementing the rule here. Only a
verdict of `owner_names_match` counts as stale: the import and the canonical
baseline now agree, so there is nothing to transfer. Every other verdict
(`would_create`, a co-owner addition, no incoming owner, an unreadable baseline)
leaves the request untouched.

What it refuses to touch
------------------------
- Anything a human lodged. Only requests submitted by the detector itself
  (`submitted_by_role == "system"`, carrying a `portal_detected_signature`) are
  eligible; a real owner's lodged sale is never auto-withdrawn because an import
  has not caught up yet.
- Anything an approver has already acted on (`current_approvals > 0`, or a
  non-empty approval history).
- Anything not currently pending.

Nothing is hard-deleted: the row keeps its full detection payload and moves to
`status="withdrawn"`, out of the review queue but still on file for the 7-year
retention rule. The provisional account the request minted for its phantom
transferee is soft-archived only when demonstrably unclaimed.

Usage:
    # Dry run (default) — prints what would change, no writes.
    python3 backend/scripts/data_repair/withdraw_stale_owner_transfer_requests_20260820.py \
        --building-id 13195

    # Apply.
    python3 backend/scripts/data_repair/withdraw_stale_owner_transfer_requests_20260820.py \
        --building-id 13195 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from database import db  # noqa: E402
from services.ownership_transfer_detection_service import (  # noqa: E402
    PENDING_OWNER_TRANSFER_STATUSES,
    archive_stray_provisional_owner_account,
    detect_and_create_portal_owner_transfer,
    withdraw_owner_transfer_request,
)

WITHDRAWN_ACTION = "withdrawn_drift_no_longer_present"
WITHDRAWN_NOTE = (
    "Withdrawn automatically: re-running the owner-drift comparison against current "
    "data returns 'owner_names_match' — the imported owner snapshot and the canonical "
    "owner baseline now agree, so the disagreement this request described no longer "
    "exists and there is nothing to transfer."
)
REPAIR_ACTOR = "system:stale_drift_request_repair"
STRAY_ACCOUNT_ARCHIVE_REASON = (
    "Provisional account minted for a phantom transferee by an owner transfer request "
    "that was withdrawn because the owner drift it described no longer exists."
)
STALE_VERDICT = "owner_names_match"


def _imported_owner_names(owner: dict) -> str:
    """The imported owner string, exactly as the drift detector reads it.

    Mirrors `create_owner_transfer_requests_from_imported_owner_drift._owner_names`:
    the combined `owner` field wins, with the split fields as the fallback.
    """
    if owner.get("owner"):
        return owner["owner"]
    names = [owner.get("owner_name"), owner.get("owner_name_b")]
    return " & ".join([name for name in names if name])


def _is_detector_raised(transfer: dict) -> bool:
    """Only the detector's own requests are eligible — never a human's."""
    return bool(
        transfer.get("submitted_by_role") == "system"
        and transfer.get("portal_detected_signature")
    )


def _is_untouched(transfer: dict) -> bool:
    """No approver has acted on it yet."""
    return not transfer.get("current_approvals") and not transfer.get("approval_history")


async def run(building_id: str, apply: bool, unit_number: str | None = None) -> dict:
    """Withdraw pending detector requests whose drift no longer exists."""
    query = {
        "building_id": building_id,
        "status": {"$in": list(PENDING_OWNER_TRANSFER_STATUSES)},
    }
    if unit_number:
        query["unit_number"] = unit_number

    pending = await db._db["owner_transfer_requests"].find(
        query, {"_id": 0}
    ).sort("unit_number", 1).to_list(1000)

    now = datetime.now(timezone.utc).isoformat()
    withdrawn, kept, archived = [], [], []

    for transfer in pending:
        un = transfer.get("unit_number")
        summary = {
            "id": transfer.get("id"),
            "unit_number": un,
            "source": transfer.get("source"),
            "old_owners": [
                owner.get("full_name") for owner in transfer.get("old_owners") or []
            ],
            "new_owner": (transfer.get("new_owner") or {}).get("full_name"),
        }

        if not _is_detector_raised(transfer):
            kept.append({**summary, "reason": "not_a_detector_raised_request"})
            continue
        if not _is_untouched(transfer):
            kept.append({**summary, "reason": "already_under_review"})
            continue

        owner_row = await db._db["strata_owners"].find_one(
            {"building_id": building_id, "unit_number": un}, {"_id": 0}
        )
        imported = _imported_owner_names(owner_row or {})
        if not imported:
            kept.append({**summary, "reason": "no_current_imported_owner_snapshot"})
            continue

        # The detector's own comparison decides staleness — never a copy of it here.
        verdict = await detect_and_create_portal_owner_transfer(
            db,
            building_id,
            un,
            imported,
            detected_at=now,
            source=transfer.get("source") or "external_ledger_owner_name_drift",
            dry_run=True,
            use_cutover_baseline=True,
        )
        summary["recheck_verdict"] = verdict.get("reason")
        summary["current_imported_owner_names"] = imported
        if verdict.get("reason") != STALE_VERDICT:
            kept.append({**summary, "reason": "drift_still_present"})
            continue

        summary["current_owner_names"] = verdict.get("current_owner_names")
        withdrawn.append(summary)
        if apply:
            await withdraw_owner_transfer_request(
                db._db,
                building_id,
                transfer["id"],
                action=WITHDRAWN_ACTION,
                note=WITHDRAWN_NOTE,
                actor=REPAIR_ACTOR,
                now=now,
            )

        minted_id = (transfer.get("new_owner") or {}).get("user_id")
        if minted_id:
            outcome = await archive_stray_provisional_owner_account(
                db._db,
                building_id,
                minted_id,
                un,
                now=now,
                apply=apply,
                reason=STRAY_ACCOUNT_ARCHIVE_REASON,
                actor=REPAIR_ACTOR,
            )
            if outcome:
                archived.append(outcome)

    return {
        "building_id": building_id,
        "unit_number": unit_number,
        "apply": apply,
        "pending_scanned": len(pending),
        "withdrawn_count": len(withdrawn),
        "withdrawn": withdrawn,
        "kept": kept,
        "stray_provisional_accounts": archived,
    }


def main() -> int:
    """CLI entry point. Dry-run by default; --apply writes."""
    parser = argparse.ArgumentParser(
        description=(
            "Withdraw pending detector-raised owner transfer requests whose drift no "
            "longer exists, and archive the provisional accounts they minted."
        )
    )
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--unit-number", help="Optional single unit, e.g. UA042")
    parser.add_argument("--apply", action="store_true", help="Write changes. Default is dry-run.")
    args = parser.parse_args()

    result = asyncio.run(run(args.building_id, args.apply, args.unit_number))
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
