#!/usr/bin/env python3
# @featuretrace:financial-onboarding — CLI entry point for Workstream C's bounded portal
#   reconciliation posting orchestrator (GAP-ONBOARD-004 A2).
# Layer: script
# Data flow: see services/reconstruction_generators/bounded_portal_reconciliation_posting_service.py
# Related: scripts/preview_bounded_portal_reconciliation.py (the read-only classification this
#              script's dry-run path shares via assemble_and_classify())
#          services/reconstruction_generators/bounded_portal_reconciliation_posting_service.py
"""CLI: post the BOUNDED gap for units the classifier approves.

Building-agnostic (no East-Gate-specific logic — the building/year are CLI arguments).
DRY-RUN BY DEFAULT: without --apply, this only classifies and prints — no writes. --apply
requires --batch-id (an ALREADY-APPROVED demo_bank_reconstruction_batches row, the dual-control
authorisation vehicle) and --executed-by (a real user UUID, distinct from the batch's
approved_by).

Usage (from backend/):
    venv/bin/python3 scripts/post_bounded_portal_reconciliation.py \\
        --building-id 13195 --year 2026

    venv/bin/python3 scripts/post_bounded_portal_reconciliation.py \\
        --building-id 13195 --year 2026 --batch-id <uuid> --executed-by <uuid> --apply
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--building-id", required=True)
    p.add_argument("--year", required=True)
    p.add_argument("--bound-periods", type=float, default=1.0)
    p.add_argument("--batch-id", default=None)
    p.add_argument("--executed-by", type=UUID, default=None)
    p.add_argument("--is-test-data", action="store_true")
    p.add_argument("--apply", action="store_true", help="Write. Without this flag, dry-run only.")
    return p.parse_args()


async def _dry_run(args: argparse.Namespace) -> int:
    from db_postgres.repos.config_repo import resolve_scheme_context
    from db_postgres.session import async_session_context, set_tenant
    from database import db as mongo_db
    from services.reconstruction_generators.bounded_portal_reconciliation_posting_service import (
        BoundedReconciliationPostingError, assemble_and_classify,
    )

    scheme = await resolve_scheme_context(args.building_id)
    if not scheme:
        print(f"No PostgreSQL scheme found for building_id={args.building_id!r}.")
        return 1
    scheme_id = str(scheme["scheme_id"])
    tenant_id = str(scheme["tenant_id"])

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        try:
            snapshot_date, classified = await assemble_and_classify(
                mongo_db=mongo_db, session=session, building_id=args.building_id,
                financial_year=args.year, scheme_id=scheme_id, tenant_id=tenant_id,
                bound_periods=args.bound_periods,
            )
        except BoundedReconciliationPostingError as exc:
            print(f"Cannot classify: {exc}")
            return 1

    print(f"Building        : {args.building_id} (scheme_id={scheme_id})")
    print(f"Year            : {args.year}")
    print(f"Portal snapshot : {snapshot_date}")
    print(f"Bound           : {args.bound_periods:g}x one-period levy")
    print()

    counts: dict[str, int] = {}
    for u in sorted(classified, key=lambda c: c.unit_input.unit_number):
        counts[u.decision.decision] = counts.get(u.decision.decision, 0) + 1
        if u.decision.decision not in ("reconciled", "no_portal_data"):
            print(
                f"  {u.unit_input.unit_number:8} {u.decision.decision:<24} "
                f"gap=${(u.decision.gap_cents or 0) / 100:>9,.2f}  {u.decision.reason}"
            )

    print()
    print("Summary:", counts)
    print()
    if counts.get("halt_flag"):
        print("halt_flag unit(s) present -- --apply would ABORT the whole run without posting anything.")
    print("Dry-run only -- nothing posted. Re-run with --apply (+ --batch-id/--executed-by) to post.")
    return 0


async def _apply(args: argparse.Namespace) -> int:
    from database import db as mongo_db
    from services.reconstruction_generators.bounded_portal_reconciliation_posting_service import (
        BoundedReconciliationPostingError, run_reconciliation_posting,
    )

    try:
        result = await run_reconciliation_posting(
            mongo_db=mongo_db, building_id=args.building_id, financial_year=args.year,
            batch_id=args.batch_id, executed_by=args.executed_by,
            bound_periods=args.bound_periods, is_test_data=args.is_test_data,
        )
    except BoundedReconciliationPostingError as exc:
        print(f"\nABORTED: {exc}")
        return 1

    print(
        f"\nPosted {result.posted}, replayed {result.replayed}, reconciled {result.reconciled}, "
        f"no_portal_data {result.no_portal_data}. Total posted: ${result.posted_total_cents / 100:,.2f}"
    )
    print(
        f"Run-total integrity: covered=${result.covered_total_cents / 100:,.2f} "
        f"expected=${result.expected_total_cents / 100:,.2f} "
        f"({'OK' if result.run_total_integrity_ok else 'MISMATCH'})"
    )
    for o in result.outcomes:
        if o.status in ("posted", "replayed", "error"):
            print(
                f"  {o.unit_number:8} {o.decision:<24} {o.status:<10} "
                f"gap=${(o.gap_cents or 0) / 100:>9,.2f}  {o.error or ''}"
            )
    if result.has_errors:
        print("\nSome units failed to post -- see 'error' rows above. Units that posted "
              "successfully were committed regardless (partial-success discipline).")
    return 1 if result.has_errors else 0


def main() -> int:
    args = parse_args()
    if not args.apply:
        return asyncio.run(_dry_run(args))
    if not args.batch_id or not args.executed_by:
        print("--apply requires --batch-id and --executed-by (an APPROVED reconstruction "
              "batch + a distinct executor identity for dual control).")
        return 2
    return asyncio.run(_apply(args))


if __name__ == "__main__":
    raise SystemExit(main())
