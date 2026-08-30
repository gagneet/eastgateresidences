#!/usr/bin/env python3
# NOTE — building-agnostic tool; --building-id defaults to "13195" (East Gate Residences,
# Unit Plan 13195) only because that is the building the bug was found in. Nothing here
# special-cases East Gate.
# @featuretrace:owner-transfers — Retract transfer requests that were really joint-owner
# additions, and link the co-owner the request was standing in for.
# Layer: migration
# Data flow: owner_transfer_requests (pending, additive) -> status="withdrawn";
#            units.owner_name_b -> ownership_transfer_detection_service.link_missing_co_owners
#            -> users / user_units / memberships / owner_transfer_requests (building-scoped).
# Related: backend/services/ownership_transfer_detection_service.py
#          backend/scripts/data_repair/bootstrap_initial_owner_links_20260819.py
#          backend/scripts/data_repair/create_owner_transfer_requests_from_imported_owner_drift.py
"""
Fix owner-transfer requests that are actually joint-owner additions.

The bug
-------
`detect_and_create_portal_owner_transfer` compared the imported owner-name set
against the canonical `user_units` set and raised a transfer request whenever
the import contained a name the canonical set lacked — even when NO current
owner had gone away. For a unit legitimately held by two people whose second
owner was never linked canonically, that produced a review row on
/admin/owner-transfers asking staff to transfer the unit from one of its own
joint owners to the other (East Gate: UA046, UA063, TH073, TH086).

Two things went wrong and both are fixed in the service:
  1. A pure owner-set ADDITION is not a change of ownership. The detector now
     returns `co_owner_addition_not_a_transfer` instead of creating a request.
  2. `create_initial_ownership_link` skips any unit that already has an active
     owner link, so a unit linked to its primary owner only could never get its
     genuine co-owner linked. `link_missing_co_owners` closes that gap.

What this script does
---------------------
Phase 1 — retract: every PENDING request whose old_owners set is fully
contained in its detected/projected owner set (nothing removed) is marked
`status="withdrawn"`. Nothing is hard-deleted: ownership records are under the
7-year retention rule, so the row and its full detection payload stay on file.

Phase 2 — link: for each affected unit, add the missing canonical co-owner link
from the unit's own imported owner names. Additive only — no existing link is
removed, retired, or repointed, and the primary flag is never reassigned.

Phase 3 — archive the residue: each bogus request minted a provisional
"new owner" account for the phantom transferee. Once the request is withdrawn
and the real co-owner is linked under their own account, that provisional record
is a duplicate identity for a living owner. It is soft-archived (never deleted),
and only when it is demonstrably unclaimed: portal-detected, inactive, with zero
active unit links and zero memberships. An account the co-owner linking adopted
therefore keeps working untouched.

Usage:
    # Dry run (default) — prints what would change, no writes.
    python3 backend/scripts/data_repair/fix_co_owner_addition_transfer_requests_20260820.py \
        --building-id 13195

    # Apply.
    python3 backend/scripts/data_repair/fix_co_owner_addition_transfer_requests_20260820.py \
        --building-id 13195 --apply

    # Retract the bogus requests but leave the co-owner linking for later.
    python3 .../fix_co_owner_addition_transfer_requests_20260820.py --apply --skip-linking
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from database import db  # noqa: E402
from services.ownership_transfer_detection_service import (  # noqa: E402
    OWNER_TRANSFER_WITHDRAWN_STATUS,
    PENDING_OWNER_TRANSFER_STATUSES,
    archive_stray_provisional_owner_account,
    link_missing_co_owners,
    split_owner_names,
    withdraw_owner_transfer_request,
)

WITHDRAWN_STATUS = OWNER_TRANSFER_WITHDRAWN_STATUS
WITHDRAWN_ACTION = "withdrawn_co_owner_addition"
WITHDRAWN_NOTE = (
    "Withdrawn automatically: the imported owner snapshot only ADDED a joint owner — "
    "no existing owner was removed or replaced — so this was never an ownership "
    "transfer. Joint ownership is lawful and routine. The missing co-owner is linked "
    "canonically instead (see link_missing_co_owners)."
)


def _name_key(name: str | None) -> str:
    """Normalise an owner name for set comparison (mirrors the detection service)."""
    text = re.sub(r"\s+", " ", (name or "").strip().lower())
    return re.sub(r"[^a-z0-9 ]+", "", text)


def _detected_owner_keys(transfer: dict) -> set[str]:
    """Owner-name keys the detector projected as the unit's post-import owner set.

    Falls back to the raw imported names, then to the new_owner name, so a
    request written before the projected-names field existed is still classifiable.
    """
    for field in ("portal_detected_owner_names", "portal_detected_raw_owner_names"):
        names = transfer.get(field) or []
        keys = {_name_key(name) for name in names if _name_key(name)}
        if keys:
            return keys
    new_owner_name = (transfer.get("new_owner") or {}).get("full_name")
    return {_name_key(name) for name in split_owner_names(new_owner_name) if _name_key(name)}


def _is_pure_co_owner_addition(transfer: dict) -> bool:
    """True when no current owner is absent from the imported owner set.

    That is exactly the "nothing was removed" condition the detector now refuses
    to raise a transfer for. A request with no recorded old_owners is NOT
    classified here — it is a bootstrap/manual row, not owner-name drift.
    """
    old_keys = {
        _name_key(owner.get("full_name"))
        for owner in transfer.get("old_owners") or []
        if _name_key(owner.get("full_name"))
    }
    if not old_keys:
        return False
    detected_keys = _detected_owner_keys(transfer)
    if not detected_keys:
        return False
    return not (old_keys - detected_keys)


REPAIR_ACTOR = "system:co_owner_addition_repair"
STRAY_ACCOUNT_ARCHIVE_REASON = (
    "Provisional account minted for a phantom transferee by an owner transfer request "
    "that was withdrawn as a joint-owner addition. The real owner holds their own "
    "canonical account."
)


async def run(
    building_id: str,
    apply: bool,
    unit_number: str | None = None,
    skip_linking: bool = False,
) -> dict:
    """Retract additive transfer requests and link the co-owners they stood in for."""
    raw_transfers = db._db["owner_transfer_requests"]
    raw_units = db._db["units"]

    query = {
        "building_id": building_id,
        "status": {"$in": list(PENDING_OWNER_TRANSFER_STATUSES)},
    }
    if unit_number:
        query["unit_number"] = unit_number

    pending = await raw_transfers.find(query, {"_id": 0}).sort("unit_number", 1).to_list(1000)
    now = datetime.now(timezone.utc).isoformat()

    withdrawn, kept = [], []
    # Keeps each withdrawn summary paired with the row it came from, so phase 2 can
    # re-read the request's own detected owner set without re-scanning.
    withdrawn_transfers: dict[str, dict] = {}
    for transfer in pending:
        summary = {
            "id": transfer.get("id"),
            "unit_number": transfer.get("unit_number"),
            "source": transfer.get("source"),
            "old_owners": [
                owner.get("full_name") for owner in transfer.get("old_owners") or []
            ],
            "new_owner": (transfer.get("new_owner") or {}).get("full_name"),
            "detected_owner_names": transfer.get("portal_detected_owner_names"),
        }
        if not _is_pure_co_owner_addition(transfer):
            kept.append(summary)
            continue
        withdrawn.append(summary)
        withdrawn_transfers[transfer["id"]] = transfer
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

    linked = []
    if not skip_linking:
        for summary in withdrawn:
            un = summary["unit_number"]
            unit = await raw_units.find_one(
                {"building_id": building_id, "unit_number": un},
                {
                    "_id": 0,
                    "owner_name": 1,
                    "owner_name_b": 1,
                    "owner_email": 1,
                    "owner_email_b": 1,
                },
            )
            if not unit:
                linked.append({"unit_number": un, "linked": False, "reason": "unit_not_found"})
                continue
            names = [name for name in [unit.get("owner_name"), unit.get("owner_name_b")] if name]
            emails = [unit.get("owner_email"), unit.get("owner_email_b")]
            if not names:
                linked.append({"unit_number": un, "linked": False, "reason": "no_owner_name"})
                continue

            # units.* is read for the owner EMAILS, which the transfer request does not
            # carry — but the request's own detected owner set is what justified the
            # withdrawal. If the two disagree, linking from units.* would attach a name
            # this withdrawal never assessed. Stop and report rather than proceed.
            detected_keys = _detected_owner_keys(withdrawn_transfers[summary["id"]])
            unit_keys = {_name_key(name) for name in names if _name_key(name)}
            if detected_keys and unit_keys != detected_keys:
                linked.append(
                    {
                        "unit_number": un,
                        "linked": False,
                        "reason": "unit_owner_names_disagree_with_withdrawn_request",
                        "unit_owner_names": names,
                        "request_detected_owner_names": summary.get("detected_owner_names"),
                    }
                )
                continue

            result = await link_missing_co_owners(
                db, building_id, un, names, emails, detected_at=now, dry_run=not apply
            )
            result.setdefault("unit_number", un)
            linked.append(result)

    # Phase 3 covers every request this repair has EVER withdrawn, not only the ones
    # withdrawn in this run — a first pass that predates this phase still left its
    # provisional accounts behind, and the phase must be idempotent either way.
    archive_query = {
        "building_id": building_id,
        "status": WITHDRAWN_STATUS,
        "action_taken": WITHDRAWN_ACTION,
    }
    if unit_number:
        archive_query["unit_number"] = unit_number
    already_withdrawn = await raw_transfers.find(
        archive_query, {"_id": 0, "id": 1, "unit_number": 1, "new_owner": 1}
    ).to_list(1000)

    archive_candidates = {
        transfer["id"]: transfer
        for transfer in [*already_withdrawn, *withdrawn_transfers.values()]
        if transfer.get("id")
    }
    archived = []
    for transfer in sorted(
        archive_candidates.values(), key=lambda row: row.get("unit_number") or ""
    ):
        minted_id = (transfer.get("new_owner") or {}).get("user_id")
        if not minted_id:
            continue
        outcome = await archive_stray_provisional_owner_account(
            db._db,
            building_id,
            minted_id,
            transfer.get("unit_number"),
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
        "skip_linking": skip_linking,
        "pending_scanned": len(pending),
        "withdrawn_count": len(withdrawn),
        "withdrawn": withdrawn,
        "kept_as_real_transfers": kept,
        "co_owner_links": linked,
        "stray_provisional_accounts": archived,
    }


def main() -> int:
    """CLI entry point. Dry-run by default; --apply writes."""
    parser = argparse.ArgumentParser(
        description=(
            "Withdraw pending owner-transfer requests that are really joint-owner "
            "additions, and link the missing co-owner canonically."
        )
    )
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--unit-number", help="Optional single unit, e.g. UA046")
    parser.add_argument(
        "--skip-linking",
        action="store_true",
        help="Only withdraw the bogus requests; do not create the missing co-owner links.",
    )
    parser.add_argument("--apply", action="store_true", help="Write changes. Default is dry-run.")
    args = parser.parse_args()

    result = asyncio.run(
        run(args.building_id, args.apply, args.unit_number, args.skip_linking)
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
