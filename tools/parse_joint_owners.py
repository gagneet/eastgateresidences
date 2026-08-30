#!/usr/bin/env python3
"""Joint-owner name parser — operator CLI.

Scans core.ownership_periods for parties whose legal_name contains a
joint-name separator (" & ", " and "), parses them into individual owners,
and (optionally) seeds the review table or applies confirmed decisions.

Usage
-----
  # Dry-run: print CSV to stdout (no DB writes)
  python tools/parse_joint_owners.py --building-id 13195 --dry-run

  # Dry-run to file
  python tools/parse_joint_owners.py --building-id 13195 --dry-run --output-csv /tmp/review.csv

  # Seed core.joint_owner_review with parsed pending rows (idempotent)
  python tools/parse_joint_owners.py --building-id 13195 --seed

  # Preview what --apply would do (no writes)
  python tools/parse_joint_owners.py --building-id 13195 --apply --dry-run

  # Apply confirmed reviews (writes ownership_periods + outbox events)
  python tools/parse_joint_owners.py --building-id 13195 --apply

  # Parse a single name string (no DB needed)
  python tools/parse_joint_owners.py --parse-name "John Smith & Jane Smith"

Discipline
----------
  --apply NEVER runs without explicit flag.
  --apply in dry-run mode logs the plan but writes nothing.
  Every applied review emits an ADR-025 restatement event to core.outbox.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path

# Resolve backend on sys.path before any local imports
_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Pure-parser import — no DB deps; works without JWT_SECRET in env
from services.joint_owner_service import parse_joint_name  # noqa: E402


def _import_db_functions():
    """Lazy-import DB-dependent functions only when a DB operation is required.

    This lets --parse-name run without DATABASE_URL or JWT_SECRET in the env.
    All three variables must be set for any operation that touches Postgres.
    """
    import os as _os
    # Require real credentials for DB operations
    if not _os.environ.get("JWT_SECRET"):
        print(
            "ERROR: JWT_SECRET environment variable is required for DB operations.\n"
            "       Source backend/.env or export JWT_SECRET=<value> and retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    from services.joint_owner_service import (
        load_joint_owner_candidates,
        seed_review_records,
        apply_confirmed_reviews,
        list_reviews,
    )
    return load_joint_owner_candidates, seed_review_records, apply_confirmed_reviews, list_reviews


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

_CSV_FIELDS = [
    "lot_id",
    "lot_number",
    "unit_number",
    "original_legal_name",
    "parsed_owners",
    "parser_confidence",
    "split_reason",
]


def _write_csv(rows: list[dict], dest) -> None:
    writer = csv.DictWriter(dest, fieldnames=_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)


def _build_dry_run_rows(candidates: list[dict]) -> list[dict]:
    out = []
    for cand in candidates:
        result = parse_joint_name(cand["legal_name"])
        out.append(
            {
                "lot_id": cand["lot_id"],
                "lot_number": cand["lot_number"],
                "unit_number": cand.get("unit_number", ""),
                "original_legal_name": cand["legal_name"],
                "parsed_owners": json.dumps(
                    [{"name": o.name, "ownership_share": str(o.ownership_share)} for o in result.owners]
                ),
                "parser_confidence": result.confidence,
                "split_reason": result.split_reason,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Sub-command handlers
# ---------------------------------------------------------------------------

async def _cmd_dry_run(building_id: str, output_csv: str | None) -> None:
    load_joint_owner_candidates, _, _, _ = _import_db_functions()
    print(f"[dry-run] Loading joint-owner candidates for building {building_id!r} …")
    candidates = await load_joint_owner_candidates(building_id)
    print(f"[dry-run] Found {len(candidates)} candidate lot(s) with combined names.")

    rows = _build_dry_run_rows(candidates)

    if not rows:
        print("[dry-run] No joint-owner names detected. Nothing to do.")
        return

    if output_csv:
        path = Path(output_csv)
        with path.open("w", newline="", encoding="utf-8") as fh:
            _write_csv(rows, fh)
        print(f"[dry-run] CSV written to {path.resolve()}")
    else:
        _write_csv(rows, sys.stdout)

    # Summary
    high = sum(1 for r in rows if float(r["parser_confidence"]) >= 0.9)
    med = sum(1 for r in rows if 0.6 <= float(r["parser_confidence"]) < 0.9)
    low = sum(1 for r in rows if float(r["parser_confidence"]) < 0.6)
    print(f"\n[dry-run] Confidence summary: high={high}  medium={med}  low={low}")
    print("[dry-run] No data was written. Run --seed to populate core.joint_owner_review.")


async def _cmd_seed(building_id: str) -> None:
    _, seed_review_records, _, _ = _import_db_functions()
    print(f"[seed] Seeding core.joint_owner_review for building {building_id!r} …")
    inserted = await seed_review_records(building_id)
    if inserted:
        print(f"[seed] Inserted {len(inserted)} new pending review row(s):")
        for row in inserted:
            print(f"       lot {row.get('lot_number', '?'):>4}  {row.get('original_legal_name', '')}")
    else:
        print("[seed] No new rows inserted (all lots already have review records).")
    print("[seed] Done. Use GET /api/joint-owner-review or the admin UI to review.")


async def _cmd_apply(building_id: str, dry_run: bool) -> None:
    _, _, apply_confirmed_reviews, list_reviews = _import_db_functions()
    mode = "dry-run" if dry_run else "LIVE"
    print(f"[apply/{mode}] Applying confirmed reviews for building {building_id!r} …")

    # Show pending count before applying
    pending = await list_reviews(building_id, status="pending")
    confirmed = await list_reviews(building_id, status="confirmed")
    edited = await list_reviews(building_id, status="edited")
    print(
        f"[apply/{mode}] Review status: pending={len(pending)}  "
        f"confirmed={len(confirmed)}  edited={len(edited)}"
    )

    eligible = len(confirmed) + len(edited)
    if eligible == 0:
        print(f"[apply/{mode}] No confirmed/edited reviews found. Nothing to apply.")
        print("              Use PATCH /api/joint-owner-review/<id> to confirm reviews first.")
        return

    results = await apply_confirmed_reviews(
        building_id=building_id,
        actor_user_id="cli-operator",
        dry_run=dry_run,
    )

    applied_ok = [r for r in results if not r.get("error") and not r.get("dry_run")]
    errors = [r for r in results if r.get("error")]

    print(f"\n[apply/{mode}] Results:")
    for r in results:
        status_str = "DRY-RUN" if r.get("dry_run") else ("ERROR" if r.get("error") else "OK")
        names = ", ".join(r.get("owners_applied", []))
        print(f"  [{status_str}] lot {r.get('lot_number', '?'):>4}  "
              f"{r.get('original_legal_name', '')!r}  →  {names}")
        if r.get("error"):
            print(f"           ERROR: {r['error']}")

    print(f"\n[apply/{mode}] Summary: {len(results)} processed, "
          f"{len(applied_ok)} applied, {len(errors)} errors.")
    if dry_run:
        print("[apply/dry-run] No data was written. Re-run without --dry-run to commit.")


def _cmd_parse_name(name: str) -> None:
    result = parse_joint_name(name)
    print(f"Input:       {name!r}")
    print(f"Confidence:  {result.confidence:.3f}  ({result.split_reason})")
    print(f"Parsed into {len(result.owners)} owner(s):")
    for i, owner in enumerate(result.owners, 1):
        print(f"  {i}. {owner.name!r}  share={owner.ownership_share}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="StrataOS joint-owner name parser",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--building-id", metavar="ID", help="Building/scheme number (e.g. 13195)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print CSV of parsed candidates without writing to DB.",
    )
    p.add_argument(
        "--seed",
        action="store_true",
        help="Seed core.joint_owner_review with parsed pending rows (idempotent).",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Apply confirmed/edited reviews: open new ownership_periods + emit restatement events.",
    )
    p.add_argument(
        "--output-csv",
        metavar="PATH",
        help="Write dry-run CSV to this file instead of stdout.",
    )
    p.add_argument(
        "--parse-name",
        metavar="NAME",
        help="Parse a single name string and exit (no DB required).",
    )
    return p


def main() -> None:
    p = _build_parser()
    args = p.parse_args()

    # Single-name parse — no DB needed
    if args.parse_name:
        _cmd_parse_name(args.parse_name)
        return

    if not args.building_id:
        p.error("--building-id is required unless using --parse-name")

    if not (args.dry_run or args.seed or args.apply):
        p.error("Specify at least one of --dry-run, --seed, or --apply")

    if args.seed and args.apply:
        p.error("--seed and --apply are mutually exclusive. Seed first, review, then apply.")

    building_id = args.building_id

    if args.dry_run and not args.apply:
        asyncio.run(_cmd_dry_run(building_id, args.output_csv))
    elif args.seed:
        asyncio.run(_cmd_seed(building_id))
    elif args.apply:
        asyncio.run(_cmd_apply(building_id, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
