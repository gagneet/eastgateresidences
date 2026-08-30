#!/usr/bin/env python3
# NOTE — building-agnostic tool; --building-id defaults to "13195" (East Gate Residences,
# Unit Plan 13195) only because that is the building the residue was found in. Nothing
# here special-cases East Gate.
# @featuretrace:owner-transfers — Purge owner_transfer_requests rows that never described
# a change of ownership, so the review register shows real transfers only.
# Layer: migration
# Data flow: owner_transfer_requests (building-scoped) + ownership_transfer_log (building-scoped,
#            READ-ONLY) -> JSON backup -> delete_many on owner_transfer_requests.
# Related: backend/server.py (#owner-transfers-section, GET /owner-transfers)
#          backend/services/ownership_transfer_detection_service.py
#          backend/scripts/data_repair/bootstrap_initial_owner_links_20260819.py
#          backend/scripts/data_repair/fix_co_owner_addition_transfer_requests_20260820.py
"""
Purge owner-transfer requests that are not ownership transfers.

Why this exists
---------------
`owner_transfer_requests` is the review queue behind /admin/owner-transfers. It is
meant to hold requests to move a unit from one owner to another. Three data-repair
campaigns wrote rows into it that describe something else entirely, and those rows
never leave the queue because they are created already-approved:

  - `bootstrap_initial_owner_links_*` writes an auto-approved row for every unit it
    gives a first canonical owner link. There is no outgoing owner — the unit had no
    link at all. That is a link being created, not ownership changing hands.
  - the co-owner link backfill writes a row per joint owner it links. Adding a second
    lawful owner to a unit is not a transfer (see
    `fix_co_owner_addition_transfer_requests_20260820.py`, which fixed the detector
    that used to raise these; the rows it already produced remained).
  - the orphaned-owner-link repair writes a row that says, in its own request_notes,
    "Data-integrity repair, NOT an ownership change".

On East Gate this left 90 rows in a register that had 2 real requests in it.

What counts as "not a transfer"
-------------------------------
Three rules, each independently sufficient. All of them are properties of the row
itself — no unit is named in this file and no id is hardcoded.

  R1  `old_owners` is empty.
      Nobody was removed, so nothing changed hands. This is what every bootstrap /
      co-owner-backfill / orphan-repair row looks like.

  R2  The row was raised by the detector (`submitted_by_role == "system"`) and its
      status is `withdrawn` or `rejected`.
      A detection that a reviewer discarded, or that a later script withdrew because
      the drift it described had already been corrected. It has no future.

  R4  The row was raised by the detector, and its outgoing and incoming owner names
      are the SAME PEOPLE once honorifics are stripped.
      "rachel clarke" => "ms rachel clarke" is the portal rendering one owner two
      ways between scrapes, not a change of ownership. Added 2026-08-28 after a live
      scrape raised 29 requests of which 28 were title-only drift; the underlying
      cause is fixed in `ownership_transfer_detection_service._name_key`, and this
      rule clears the rows that key already produced.

      R4 is the ONE rule permitted to delete a `pending` row, and the exception is
      deliberate. The pending guard exists because a live request may be awaiting a
      reviewer's judgement — but a row whose outgoing and incoming owner sets are
      identical presents nothing to judge. It is not a transfer a human might approve
      or reject; it describes no change at all. The comparison uses the detector's own
      (now corrected) `_name_key`, so a row survives R4 the moment the names differ by
      anything other than a title.

  R3  The row was raised by the detector, is `approved`, and the same transfer
      (unit + outgoing owner set + incoming owner) is already recorded in
      `ownership_transfer_log`.
      The log is the authoritative register and the History tab reads from it. The
      request row is a duplicate of a record that is kept elsewhere.

What it refuses to touch
------------------------
- `ownership_transfer_log` — never read for anything but rule R3's comparison, never
  written. The History tab is unchanged by this script.
- Anything a human lodged (`submitted_by_role != "system"`). A real owner's request
  stays on file with its full approval trail whatever its status.
- Anything pending (`OWNER_TRANSFER_PENDING_STATUSES`). A live request is never
  purged, even if it would otherwise match R1.

Retention
---------
These rows are script residue, not ownership records: the ownership facts they
duplicate live in `ownership_transfer_log`, `core.ownership_periods` and
`user_units`, none of which this script touches. Every row it deletes is written to
a timestamped JSON backup first, so the deletion is reversible from the file.

Usage:
    # Dry run (default) — prints the classification, no writes.
    python3 backend/scripts/data_repair/purge_non_transfer_owner_transfer_requests_20260827.py \
        --building-id 13195

    # Apply.
    python3 backend/scripts/data_repair/purge_non_transfer_owner_transfer_requests_20260827.py \
        --building-id 13195 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from database import db  # noqa: E402

DEFAULT_BACKUP_DIR = BACKEND_DIR / "scripts" / "data_repair" / "backups"

# Mirrors server.py's OWNER_TRANSFER_PENDING_STATUSES. Imported lazily below so this
# script stays runnable if server.py cannot be imported standalone.
_FALLBACK_PENDING = {"pending", "pending_second_approval"}


def _pending_statuses() -> set[str]:
    """Return the live pending-status set, falling back to a local copy.

    A row in any pending status is a live request and is never purged, so this set
    is a safety boundary rather than a cosmetic detail — prefer server.py's own
    definition so the two cannot drift.
    """
    try:
        from server import OWNER_TRANSFER_PENDING_STATUSES  # noqa: PLC0415
        return set(OWNER_TRANSFER_PENDING_STATUSES)
    except Exception:  # noqa: BLE001 — standalone run without the full app import
        return set(_FALLBACK_PENDING)


def _names(value) -> set[str]:
    """Normalise an old_owners / new_owner payload to a set of comparable names."""
    out: set[str] = set()
    items = value if isinstance(value, list) else [value]
    for item in items:
        if isinstance(item, dict):
            name = item.get("full_name") or item.get("name") or item.get("email") or ""
        else:
            name = str(item or "")
        name = " ".join(name.split()).strip().lower()
        if name:
            out.add(name)
    return out


def _log_names(value) -> set[str]:
    """Split an ownership_transfer_log name field ("A & B", "A, B") into a name set."""
    text = str(value or "")
    for sep in ("&", ",", " and "):
        text = text.replace(sep, "\n")
    return {" ".join(part.split()).strip().lower() for part in text.split("\n") if part.strip()}


def _person_keys(value) -> set[str]:
    """Normalise an owner payload to a set of keys using the DETECTOR'S own key.

    Importing `_name_key` rather than reimplementing it is the point: R4's claim is
    "re-evaluate this row under the corrected key". If the detector's normalisation
    changes again, this rule must change with it or the two will disagree about what
    counts as the same person.
    """
    from services.ownership_transfer_detection_service import _name_key  # noqa: PLC0415

    out: set[str] = set()
    items = value if isinstance(value, list) else [value]
    for item in items:
        if isinstance(item, dict):
            raw = item.get("full_name") or item.get("name") or ""
        else:
            raw = str(item or "")
        # Split joint-owner strings the same way the portal renders them.
        for part in str(raw).replace(" and ", "&").replace(",", "&").split("&"):
            key = _name_key(part)
            if key:
                out.add(key)
    return out


def _title_only_drift(request: dict) -> bool:
    """True when the row's outgoing and incoming owners are the same people.

    Prefers the portal-derived name lists the detector actually compared; falls back
    to the structured old_owners/new_owner payload when those are absent (rows written
    before the detector recorded them).
    """
    previous = _person_keys(request.get("portal_previous_owner_names")) or _person_keys(
        request.get("old_owners")
    )
    detected = _person_keys(request.get("portal_detected_owner_names")) or _person_keys(
        request.get("new_owner")
    )
    # Both sides must be non-empty: an empty side is R1's case (a link created), not
    # R4's, and treating "empty == empty" as drift would delete rows R1 should explain.
    return bool(previous) and bool(detected) and previous == detected


def classify(request: dict, log_by_unit: dict[str, list[dict]], pending: set[str]) -> tuple[bool, str]:
    """Decide whether one request row describes a real ownership transfer.

    Returns (delete, reason). `delete=False` reasons are worth printing too — a
    dry run should explain why every surviving row survived, not only why the
    others go.
    """
    status = (request.get("status") or "").strip().lower()
    submitted_by_role = (request.get("submitted_by_role") or "").strip().lower()
    is_system = submitted_by_role == "system"

    if not is_system:
        return False, f"keep: lodged by a person (submitted_by_role={submitted_by_role!r})"

    # R4 runs BEFORE the pending guard — see the module docstring for why this one rule
    # is allowed to remove a pending row. It is scoped to detector rows only (the
    # `is_system` check above already returned for anything a person lodged).
    if _title_only_drift(request):
        return True, (
            "R4: outgoing and incoming owners are the same people once honorifics are "
            "stripped — the portal rendered one owner two ways, ownership did not change"
        )

    if status in pending:
        return False, "keep: live request (pending)"

    old_owners = _names(request.get("old_owners"))
    if not old_owners:
        return True, "R1: no outgoing owner — a link was created, ownership did not change"

    if status in {"withdrawn", "rejected"}:
        return True, f"R2: detector row discarded (status={status})"

    if status == "approved":
        new_owner = _names(request.get("new_owner"))
        for entry in log_by_unit.get(request.get("unit_number") or "", []):
            logged_old = _log_names(entry.get("previous_owner_name") or entry.get("previous_owner"))
            logged_new = _log_names(entry.get("new_owner_name") or entry.get("new_owner"))
            if old_owners & logged_old and (not new_owner or new_owner & logged_new):
                return True, "R3: already recorded in ownership_transfer_log (History tab)"
        return False, "keep: approved transfer with no ownership_transfer_log counterpart"

    return False, f"keep: unrecognised status={status!r} — left for a human"


async def run(building_id: str, apply: bool, backup_dir: Path) -> dict:
    """Classify and (with --apply) delete the non-transfer rows for one building."""
    coll = db._db["owner_transfer_requests"]
    requests = await coll.find({"building_id": building_id}, {"_id": 0}).sort("requested_date", 1).to_list(5000)

    # READ-ONLY. The History tab reads this collection and it is never written here.
    log_rows = await db._db["ownership_transfer_log"].find(
        {"building_id": building_id}, {"_id": 0}
    ).to_list(5000)
    log_by_unit: dict[str, list[dict]] = {}
    for entry in log_rows:
        log_by_unit.setdefault(entry.get("unit_number") or "", []).append(entry)

    pending = _pending_statuses()
    to_delete: list[dict] = []
    to_keep: list[tuple[dict, str]] = []
    for request in requests:
        delete, reason = classify(request, log_by_unit, pending)
        (to_delete if delete else to_keep).append((request, reason) if not delete else request)
        if delete:
            request["_purge_reason"] = reason

    print(f"\nbuilding {building_id}: {len(requests)} owner_transfer_requests")
    print(f"  keep   {len(to_keep)}")
    for request, reason in to_keep:
        print(f"    {request.get('unit_number', '?'):>7} {str(request.get('status')):>10}  {reason}")
    print(f"  delete {len(to_delete)}")
    by_reason: dict[str, int] = {}
    for request in to_delete:
        by_reason[request["_purge_reason"]] = by_reason.get(request["_purge_reason"], 0) + 1
    for reason, count in sorted(by_reason.items()):
        print(f"    {count:>4}  {reason}")

    backup_path = None
    if to_delete and apply:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_dir / f"owner_transfer_requests_{building_id}_{stamp}.json"
        backup_path.write_text(json.dumps(to_delete, indent=2, default=str))
        print(f"\n  backup written: {backup_path}")

        ids = [r["id"] for r in to_delete if r.get("id")]
        result = await coll.delete_many({"building_id": building_id, "id": {"$in": ids}})
        print(f"  deleted {result.deleted_count} rows")
    elif to_delete:
        print("\n  DRY RUN — nothing deleted. Re-run with --apply.")

    return {
        "building_id": building_id,
        "total": len(requests),
        "kept": len(to_keep),
        "deleted": len(to_delete) if apply else 0,
        "would_delete": len(to_delete),
        "backup": str(backup_path) if backup_path else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--apply", action="store_true", help="Perform the deletion (default is a dry run).")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    args = parser.parse_args()
    summary = asyncio.run(run(args.building_id, args.apply, args.backup_dir))
    print("\n" + json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
