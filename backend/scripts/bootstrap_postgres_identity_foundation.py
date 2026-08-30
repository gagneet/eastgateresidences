#!/usr/bin/env python3
# @featuretrace:postgres-identity-foundation — CLI for dry-run/apply/validate-only identity bootstrap.
# Layer: seed
# Data flow: operator CLI args → identity_bootstrap_service (fixture|mongo source) → core identity tables + readiness snapshot (building-scoped).
# Related: backend/services/identity_bootstrap_service.py
#          backend/scripts/postgres_cutover_p0_readiness.py
#          docs/migration/east-gate-postgres-identity-foundation.md
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.identity_bootstrap_service import (  # noqa: E402
    BootstrapMode,
    BootstrapSource,
    IdentityBootstrapService,
    IdentityFixture,
    LotRecord,
    OwnerRecord,
    UserRecord,
    ValidationError,
    _normalize_plan_number,
    _slugify,
)


def _build_fixture_from_args(args: argparse.Namespace) -> IdentityFixture:
    """Generated function header.

    Function: _build_fixture_from_args
    Path: backend/scripts/bootstrap_postgres_identity_foundation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    plan = _normalize_plan_number(args.plan_number or args.building_id or "")
    if not plan:
        raise ValidationError("plan_number or building_id is required for fixture source")
    slug = args.building_slug or _slugify(f"scheme-{plan}")
    return IdentityFixture(
        plan_number=plan,
        building_slug=slug,
        building_name=f"Scheme {plan}",
        jurisdiction="ACT",
        lots=[
            LotRecord(lot_number="1", unit_number="1"),
            LotRecord(lot_number="2", unit_number="2"),
        ],
        owners=[
            OwnerRecord(
                full_name="Fixture Owner 1",
                email="fixture.owner1@example.com",
                lot_numbers=["1"],
                ownership_start=date(2020, 1, 1),
            ),
            OwnerRecord(
                full_name="Fixture Owner 2",
                email="fixture.owner2@example.com",
                lot_numbers=["2"],
                ownership_start=date(2020, 1, 1),
            ),
        ],
        users=[
            UserRecord(email="fixture.owner1@example.com", full_name="Fixture Owner 1", role="owner"),
            UserRecord(email="fixture.owner2@example.com", full_name="Fixture Owner 2", role="owner"),
        ],
        source_system="fixture",
        source_collection="synthetic_fixture",
        source_external_id=f"fixture:{plan}",
    )


def _resolve_mode(args: argparse.Namespace) -> BootstrapMode:
    """Generated function header.

    Function: _resolve_mode
    Path: backend/scripts/bootstrap_postgres_identity_foundation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if args.apply:
        return BootstrapMode.APPLY
    if args.validate_only:
        return BootstrapMode.VALIDATE_ONLY
    return BootstrapMode.DRY_RUN


async def _run(args: argparse.Namespace) -> int:
    """Generated function header.

    Function: _run
    Path: backend/scripts/bootstrap_postgres_identity_foundation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    source = BootstrapSource(args.source)
    mode = _resolve_mode(args)
    service = IdentityBootstrapService()

    if source == BootstrapSource.MONGO:
        fixture = await service.load_fixture_from_mongo(
            building_id=args.building_id or args.plan_number or "",
            plan_number=args.plan_number,
            building_slug=args.building_slug,
        )
    else:
        print(
            "WARNING: --source fixture builds SYNTHETIC test data "
            "(fixture.owner1@example.com, 2 fake owners) — the readiness report "
            "reflects that synthetic fixture, not this building's real identity data. "
            "Use --source mongo to check/bootstrap a real building.",
            file=sys.stderr,
        )
        fixture = _build_fixture_from_args(args)

    report = await service.run(
        fixture=fixture,
        mode=mode,
        source=source,
        import_batch_id=args.import_batch_id,
    )

    if args.output_json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(
            f"[{report.mode.value}] plan={report.plan_number} source={report.source.value} "
            f"lots(created={report.lots_created}, found={report.lots_found}, updated={report.lots_updated}) "
            f"users(created={report.users_created}, found={report.users_found}, skipped={report.users_skipped}) "
            f"parties(created={report.parties_created}, found={report.parties_found}) "
            f"ownership={report.ownership_records_created} memberships={report.memberships_created}"
        )
        if report.invalid_emails:
            print(f"invalid_emails={','.join(report.invalid_emails)}")
        if report.duplicate_lots:
            print(f"duplicate_lots={','.join(report.duplicate_lots)}")
        if report.duplicate_users:
            print(f"duplicate_users={','.join(report.duplicate_users)}")
        if report.skipped_records:
            print(f"skipped_records={len(report.skipped_records)}")

    return 0 if (mode != BootstrapMode.APPLY or not report.cross_building_risks) else 1


def main() -> int:
    """Generated function header.

    Function: main
    Path: backend/scripts/bootstrap_postgres_identity_foundation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Bootstrap PostgreSQL identity foundation for a building/scheme.")
    parser.add_argument("--building-id", default=None, help="Legacy building_id / plan alias (e.g. 13195, UP13195).")
    parser.add_argument("--scheme-id", default=None, help="Optional scheme UUID hint for reporting.")
    parser.add_argument("--plan-number", default=None, help="Scheme plan number (e.g. 13195).")
    parser.add_argument("--building-slug", default=None, help="Building slug hint (e.g. east-gate-residences).")
    # No default: --source previously defaulted to "fixture", so running this script
    # against a real building without remembering the flag silently built a synthetic
    # 2-fake-owner test fixture (fixture.owner1@example.com etc.) and validated *that*
    # against the database instead of the real data — reporting a false "blocked"/"fail"
    # readiness status (lots_found=0, scheme_found=false) for a building whose identity
    # data was actually complete and correct. Forcing an explicit choice here prevents
    # that footgun; use --source fixture deliberately only for local dry-run testing.
    parser.add_argument(
        "--source", choices=[s.value for s in BootstrapSource], required=True,
        help="'mongo' loads the real building's data for bootstrap/readiness checks against "
             "a live scheme. 'fixture' builds a SYNTHETIC 2-owner test fixture — dry-run/testing "
             "only, never point --source fixture --apply at a real (non-test) building_id.",
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", action="store_true", help="Validate and report only (default).")
    mode_group.add_argument("--apply", action="store_true", help="Apply idempotent bootstrap changes.")
    mode_group.add_argument("--validate-only", action="store_true", help="Validate input and output counters only.")

    parser.add_argument("--output-json", action="store_true", help="Emit report as JSON.")
    parser.add_argument("--import-batch-id", default=None, help="Optional import batch identifier.")

    args = parser.parse_args()
    if not any([args.dry_run, args.apply, args.validate_only]):
        args.dry_run = True

    try:
        return asyncio.run(_run(args))
    except ValidationError as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
