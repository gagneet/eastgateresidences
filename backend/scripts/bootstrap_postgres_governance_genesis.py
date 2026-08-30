#!/usr/bin/env python3
# @featuretrace:legacy-cleanup — CLI for dry-run/apply governance genesis (Phase G1).
# Layer: seed
# Data flow: operator CLI -> GovernanceBootstrapService -> governance.* (building-scoped).
# Related: backend/services/governance_bootstrap_service.py
#          docs/migration/governance_occupancy_settings_migration_plan.md
"""Bootstrap governance.* for a building already onboarded into core.schemes.

Always run without --apply first and read the report — skipped_records and
anomalies describe exactly what would NOT be migrated and why. Only pass
--apply once that report has been reviewed by a person who knows the real
governance history for the building (see anomalies like the EC-member /
strata-manager conflation this script deliberately refuses to resolve on
its own).

Usage:
    python3 scripts/bootstrap_postgres_governance_genesis.py --building-id 13195
    python3 scripts/bootstrap_postgres_governance_genesis.py --building-id 13195 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import db as mongo_db  # noqa: E402
from services.governance_bootstrap_service import (  # noqa: E402
    BootstrapMode,
    GovernanceBootstrapService,
    ValidationError,
)


async def _run(args: argparse.Namespace) -> int:
    mode = BootstrapMode.APPLY if args.apply else BootstrapMode.DRY_RUN
    service = GovernanceBootstrapService(mongo_db=mongo_db._db)
    if args.ec_term_start_override:
        try:
            override = date.fromisoformat(args.ec_term_start_override)
        except ValueError as exc:
            print(json.dumps({"status": "fail", "error": f"--ec-term-start-override: {exc}"}, indent=2))
            return 2
    else:
        override = None
    report = await service.run(
        building_id=args.building_id,
        mode=mode,
        import_batch_id=args.import_batch_id,
        ec_term_start_override=override,
    )

    if args.output_json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True, default=str))
        return 0

    print(f"[{report.mode.value}] building_id={report.plan_number}")
    for table in ("agm_records", "agm_motions", "agm_votes", "agm_attendance", "ec_members", "by_laws", "by_laws_acks", "decisions"):
        found = getattr(report, f"{table}_found")
        created = getattr(report, f"{table}_created", 0)
        updated = getattr(report, f"{table}_updated", 0)
        print(f"  {table}: found={found} created={created} updated={updated}")

    if report.anomalies:
        print("\nANOMALIES (not written, need a human decision):")
        for note in report.anomalies:
            print(f"  - {note}")

    if report.skipped_records:
        print(f"\nSKIPPED RECORDS ({len(report.skipped_records)}):")
        for s in report.skipped_records:
            print(f"  - [{s.table}] {s.source_id}: {s.reason}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap governance.* for a building from Mongo.")
    parser.add_argument("--building-id", required=True, help="Plan number / building_id (e.g. 13195).")
    parser.add_argument("--apply", action="store_true", help="Write to Postgres. Default is dry-run (report only).")
    parser.add_argument("--import-batch-id", default=None, help="Optional import batch identifier.")
    parser.add_argument(
        "--ec-term-start-override", default=None,
        help="ISO date (YYYY-MM-DD) to use as ec_members.term_start when Mongo has no HELD "
             "agm_records row for this scheme to derive it from (ACT: EC composition is set at "
             "the AGM that elects it). Only used as a fallback; a real held AGM record always "
             "wins. elected_at_agm_id is left NULL when this override is used.",
    )
    parser.add_argument("--output-json", action="store_true", help="Emit the report as JSON.")
    args = parser.parse_args()

    try:
        return asyncio.run(_run(args))
    except ValidationError as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
