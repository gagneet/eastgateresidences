#!/usr/bin/env python3
# @featuretrace:demo_bank — Stage 0 pre-flight verification for a reconstruction batch,
#   before Generate/Sync is ever called (docs/migration/implementation_plan_strataos_pending_tasks-2026-07-20.md).
# Layer: seed
# Data flow: operator CLI -> read-only Mongo + Postgres queries -> JSON pre-flight report.
# Related: backend/routers/financial_onboarding.py
#          backend/services/reconstruction_batch_service.py
#          backend/services/levy_generation_service.py
#          docs/migration/implementation_plan_strataos_pending_tasks-2026-07-20.md
"""Read-only Stage 0 pre-flight check for a reconstruction batch.

Never writes anything -- this is purely a snapshot-and-verify tool, run
before a human decides whether to call Generate/Sync on an approved batch.
Implements the exact checklist from the implementation plan's "Stage 0 —
Pre-flight verification" section:

  - Alembic at the expected head revision
  - the batch is still `approved` (hasn't moved/been reversed since review)
  - the approved manifest hash, captured for later comparison
  - current counts across every table Generate/Sync/matching touches
  - the manual-review-overpaid exceptions, exported in full (not just counted)
  - the reconstruction posting + bank-feed sync toggles are enabled for this
    building specifically, not globally
  - no other batch is currently mid-flight for the same building+period

Usage:
    python3 scripts/reconstruction_batch_preflight_check.py --building-id 13195 --batch-id <id>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path

# Import the REAL alembic library before backend/ ever goes on sys.path below.
# backend/alembic/ is this project's own migrations package (has its own
# __init__.py) — once _BACKEND_DIR is inserted at sys.path[0], `import
# alembic.config` resolves to that directory instead of the installed library
# and fails with "No module named 'alembic.config'". Importing here first
# caches the correct modules in sys.modules so every later import (including
# inside the dynamically-loaded check_alembic_head module below) reuses them
# instead of re-resolving against the now-shadowed name. Confirmed live
# 2026-07-20: an earlier version of this script without this ordering failed
# with exactly that error the first time the Alembic-head check ran.
import alembic.config  # noqa: F401,E402
import alembic.script  # noqa: F401,E402

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
sys.path.insert(0, str(_BACKEND_DIR))

_REQUIRED_TOGGLES = (
    "historical_financial_reconstruction",
    "historical_reconstruction_posting",
    "financial_integration_layer_v2",
    "bank_feeds_sync_enabled",
)

# The reconstruction workflow's real status machine (reconstruction_batch_service.py's
# _VALID_TRANSITIONS / ReconstructionBatchStatus) has NO "generating" status — a batch sits in "generation_ready" for the
# duration of a Generate call (transitioned to it first, then materialised, then
# moved to "generated" only on success). Verified directly against both sources
# 2026-07-20, after an earlier draft of this script guessed "generating" and would
# have silently never detected a concurrent Generate in progress. "matching" is
# included too since a concurrent Match/Post run against the same building's
# finance.bank_transactions is exactly the kind of overlap Stage 0 exists to catch.
_IN_FLIGHT_BATCH_STATUSES = {"generation_ready", "syncing", "matching"}



def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


async def _run(building_id: str, batch_id: str) -> dict:
    from database import db as mongo_db
    from db_postgres.repos.config_repo import resolve_scheme_context
    from db_postgres.session import async_session_context, set_tenant
    from services import reconstruction_batch_service as rbs
    from sqlalchemy import text

    report: dict = {
        "building_id": building_id, "batch_id": batch_id,
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "blockers": [], "warnings": [],
    }

    raw_mongo = mongo_db._db

    # ── 1. Batch status + manifest hash ─────────────────────────────────────
    try:
        batch_doc = await rbs._get_batch_doc(raw_mongo, building_id, batch_id)
    except rbs.ReconstructionWorkflowError as exc:
        report["blockers"].append(f"batch_not_found: {exc}")
        return report
    batch = rbs._doc_to_batch(batch_doc)
    report["batch_status"] = batch.status
    if batch.status != "approved":
        report["blockers"].append(
            f"batch_not_approved: status={batch.status!r}, expected 'approved'"
        )

    manifest_doc = await raw_mongo.demo_bank_reconstruction_manifests.find_one(
        {"building_id": building_id, "batch_id": batch_id}
    )
    report["manifest"] = {
        "manifest_hash": manifest_doc.get("manifest_hash") if manifest_doc else None,
        "version": manifest_doc.get("version") if manifest_doc else None,
        "expected_transaction_count": manifest_doc.get("expected_transaction_count") if manifest_doc else None,
        "expected_credit_cents": manifest_doc.get("expected_credit_cents") if manifest_doc else None,
    }
    if manifest_doc is None:
        report["blockers"].append("no_manifest: batch has no persisted manifest to Generate from")

    # ── 2. No other batch mid-flight for this building ──────────────────────
    other_batches = await raw_mongo.demo_bank_reconstruction_batches.find(
        {"building_id": building_id, "batch_id": {"$ne": batch_id}}
    ).to_list(None)
    concurrent = [b for b in other_batches if b.get("status") in _IN_FLIGHT_BATCH_STATUSES]
    if concurrent:
        report["blockers"].append(
            f"concurrent_batch_in_flight: {[b['batch_id'] for b in concurrent]}"
        )
    report["other_batches_for_building"] = [
        {"batch_id": b["batch_id"], "status": b.get("status")} for b in other_batches
    ]

    # ── 3. Toggle state — must be per-building, not global ──────────────────
    from db_postgres.repos import config_repo

    toggle_state = {}
    for key in _REQUIRED_TOGGLES:
        try:
            enabled = await config_repo.resolve_feature_toggle(building_id, key, default=False)
        except Exception as exc:  # pragma: no cover — defensive, report don't crash
            enabled = None
            report["warnings"].append(f"toggle_check_failed for {key!r}: {exc}")
        toggle_state[key] = enabled
        if enabled is not True:
            report["blockers"].append(f"toggle_not_enabled: {key!r} is not enabled for building {building_id!r}")
    report["toggles"] = toggle_state

    # ── 4. Alembic head — compared against the REPO's actual current head, not a
    # hardcoded constant (a hardcoded expected-head value goes stale the moment the
    # next migration lands after whichever revision this script was last edited
    # against). Reuses tools/check_alembic_head.py's own get_repository_head(), the
    # same function the deploy preflight and systemd ExecStartPre gate already use,
    # so this check can never disagree with what a real deploy would enforce.
    # CLAUDE.md: "Treat any head mismatch as a deploy stop, not a warning to work
    # around" — classified as a blocker here to match, not a warning.
    try:
        import importlib.util as _ilu

        _check_head_path = _REPO_ROOT / "tools" / "check_alembic_head.py"
        _spec = _ilu.spec_from_file_location("check_alembic_head", _check_head_path)
        _check_head_mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_check_head_mod)
        repository_head = _check_head_mod.get_repository_head(
            str(_BACKEND_DIR / "alembic.ini")
        )
    except Exception as exc:  # pragma: no cover — defensive, report don't crash
        repository_head = None
        report["warnings"].append(f"alembic_repo_head_check_failed: {exc}")

    async with async_session_context() as session:
        row = (await session.execute(
            text("SELECT version_num FROM core.alembic_version")
        )).fetchone()
        current_head = row[0] if row else None
    report["alembic_head"] = current_head
    report["alembic_repository_head"] = repository_head
    if repository_head is not None:
        if current_head != repository_head:
            report["blockers"].append(
                f"alembic_head_mismatch: database is at {current_head!r}, "
                f"repository head is {repository_head!r} — deploy stop, not a warning "
                "(CLAUDE.md Database Migrations policy)."
            )


    # ── 5. Current counts across everything Generate/Sync/matching touches ──
    demo_bank_count = await raw_mongo.demo_bank_transactions.count_documents(
        {"building_id": building_id, "reconstruction_batch_id": batch_id}
    )
    report["counts_before"] = {"demo_bank_transactions_for_batch": demo_bank_count}

    scheme = await resolve_scheme_context(building_id)
    if scheme:
        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            for label, sql in (
                ("finance_bank_transactions", "SELECT count(*) FROM finance.bank_transactions"),
                ("finance_bank_transactions_for_batch",
                 "SELECT count(*) FROM finance.bank_transactions WHERE reconstruction_batch_id = :bid"),
                ("finance_receipts", "SELECT count(*) FROM finance.receipts"),
                ("finance_receipt_allocations", "SELECT count(*) FROM finance.receipt_allocations"),
                ("finance_journal_entries", "SELECT count(*) FROM finance.journal_entries"),
                ("finance_journal_lines", "SELECT count(*) FROM finance.journal_lines"),
                ("finance_levy_items", "SELECT count(*) FROM finance.levy_items"),
            ):
                params = {"bid": batch_id} if ":bid" in sql else {}
                value = (await session.execute(text(sql), params)).scalar()
                report["counts_before"][label] = value
    else:
        report["warnings"].append("no_pg_scheme: could not resolve Postgres scheme context for this building")

    mongo_match_queue = await raw_mongo.match_review_queue.count_documents({"building_id": building_id})
    report["counts_before"]["match_review_queue"] = mongo_match_queue

    # ── 6. Manual-review-overpaid exceptions, exported in full ──────────────
    try:
        from services.financial_core.domain.entities import SchemeRef
        from services.levy_generation_service import build_levy_regeneration_plan
        from uuid import UUID

        if scheme:
            scheme_ref = SchemeRef(tenant_id=UUID(str(scheme["tenant_id"])), scheme_id=UUID(str(scheme["scheme_id"])))
            async with async_session_context() as session:
                await set_tenant(session, scheme_ref.tenant_id)
                plan = await build_levy_regeneration_plan(
                    building_id=building_id, mongo_db=raw_mongo, pg_session=session,
                    scheme_ref=scheme_ref,
                    from_year=batch.financial_year_start, to_year=batch.financial_year_end,
                )
            manual_review = [l for l in plan.lines if l.action == "manual_review_overpaid"]
            report["manual_review_exceptions"] = {
                "count": len(manual_review),
                "total_variance_cents": sum(
                    l.existing_paid_cents - (l.principal_cents + l.gst_cents) for l in manual_review
                ),
                "rows": [
                    {
                        "year": l.year, "fund_type": l.fund_type, "unit_number": l.unit_number,
                        "existing_paid_cents": l.existing_paid_cents,
                        "regenerated_total_cents": l.principal_cents + l.gst_cents,
                        "variance_cents": l.existing_paid_cents - (l.principal_cents + l.gst_cents),
                    }
                    for l in manual_review
                ],
            }
    except RuntimeError as exc:
        report["warnings"].append(f"manual_review_export_unavailable: {exc}")

    report["ready_to_generate"] = len(report["blockers"]) == 0
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 0 pre-flight check for a reconstruction batch. Read-only.")
    parser.add_argument("--building-id", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--output-file", default=None, help="Write the full JSON report to this path too.")
    args = parser.parse_args()

    report = asyncio.run(_run(args.building_id, args.batch_id))
    text_out = json.dumps(report, indent=2, sort_keys=True, default=_json_default)
    print(text_out)
    if args.output_file:
        Path(args.output_file).write_text(text_out)
    return 0 if report.get("ready_to_generate") else 1


if __name__ == "__main__":
    raise SystemExit(main())
