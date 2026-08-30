#!/usr/bin/env python3
# @featuretrace:finance-shadow-reads — Evidence-based triage of unresolved finance_ledger shadow diffs.
# Layer: script
# Data flow: CLI -> core.shadow_diffs (read) + live PG builders (re-verify) -> core.shadow_diffs.resolved (write, --apply only).
# Related: backend/services/finance_shadow_read_service.py
#          backend/services/finance_route_cutover_service.py
#          backend/scripts/east_gate_phase_d_shadow_status.py
# Toggle: financial_shadow_reads_enabled
"""Triage unresolved finance_ledger shadow diffs against LIVE state.

Why this exists
---------------
``get_route_shadow_readiness`` counts every non-``shadow_ok`` row in
``core.shadow_diffs`` toward a route's ``diff_count``, and the promote scripts
refuse while ``critical_count > 0``. A diff recorded against data that has since
been restored, or against a comparison the harness never actually attempted, is
not evidence about PostgreSQL's fitness — but it blocks promotion exactly as if
it were.

This script does NOT blanket-resolve. It re-runs the CURRENT PostgreSQL builder
for each affected route and classifies each row on what it can prove:

  stale_pg_side_empty
      The recorded PG value was 0/absent while the same builder returns real
      data for that scheme today. The row measured a mid-restore snapshot.
      -> resolvable, with the live figures recorded in the note.

  harness_unscoped_comparison
      A ``pg_unavailable`` row on a route whose builder returns a complete
      payload today. PostgreSQL was never queried; the payload could not be
      scoped. Fixed at source by the NOT_APPLICABLE sentinel in
      finance_shadow_read_service.py — these rows are its backlog.
      -> resolvable.

  stale_mongo_side
      The recorded MongoDB value differs materially from what the same route
      handler returns today. The row captured a mid-restore snapshot of the
      Mongo side. Symmetric to stale_pg_side_empty — staleness is not a
      PostgreSQL-only failure mode, and assuming it is leaves a pile of rows
      wrongly labelled "live divergence".
      -> resolvable, with both live figures recorded in the note.

  live_divergence
      Anything else. Both sides had data, both still produce it, and they
      disagree.
      -> NEVER auto-resolved. Reported for adjudication against source
         documents, per CLAUDE.md's financial-formula verification rule.

Dry-run by default. ``--apply`` only ever sets ``resolved``/``resolved_at``/
``notes`` on rows in the first two classes; it never touches a ledger, a
receipt, a toggle, or the cutover control plane.

Usage (from repo root):
    backend/venv/bin/python3 backend/scripts/audits/finance_shadow_diff_triage_20260827.py
    backend/venv/bin/python3 backend/scripts/audits/finance_shadow_diff_triage_20260827.py --building-id 13195
    backend/venv/bin/python3 backend/scripts/audits/finance_shadow_diff_triage_20260827.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")

from sqlalchemy import text  # noqa: E402

from services.cutover_status_service import _get_bypass_session_context  # noqa: E402
from services.financial_read_service import FinancialReadService  # noqa: E402

_DOMAIN = "finance_ledger"
_RESOLVABLE = {"stale_pg_side_empty", "harness_unscoped_comparison", "stale_mongo_side"}

_frs = FinancialReadService()


async def _live_pg_probe(building_id: str) -> dict:
    """What PostgreSQL reports for this scheme RIGHT NOW.

    Deliberately uses the same read service the shadow comparator uses, so a
    "PG has data today" claim is made with the comparator's own eyes, not a
    hand-written query that could disagree with it.
    """
    probe: dict = {"building_id": building_id}
    try:
        oc = await _frs.get_oc_levy_summary(building_id=building_id, financial_year=None)
        probe["oc_levy_summary"] = None if oc is None else {
            "financial_year": oc.get("financial_year"),
            "total_budgeted": oc.get("total_budgeted"),
            "total_collected": oc.get("total_collected"),
        }
    except Exception as exc:
        probe["oc_levy_summary_error"] = repr(exc)
    try:
        ar = await _frs.get_arrears_summary(
            building_id=building_id, financial_year=None, grace_aware=True,
        )
        probe["arrears"] = None if ar is None else {
            "financial_year": ar.get("financial_year"),
            "total_arrears_cents": ar.get("total_arrears_cents"),
            "units_in_arrears": ar.get("units_in_arrears"),
            "basis": ar.get("basis"),
        }
    except Exception as exc:
        probe["arrears_error"] = repr(exc)
    return probe


async def _live_mongo_probe(building_id: str) -> dict:
    """What MongoDB reports for this scheme RIGHT NOW.

    Calls the real route handler — the same code path that produced the recorded
    value — rather than reimplementing the arrears computation here. CLAUDE.md's
    mandatory arrears rule exists precisely because that logic keeps getting
    re-derived in a second place; a triage script is not an excuse to do it again.

    The shadow hook is stubbed for the duration so this read-only probe cannot
    write new rows into the very table it is triaging.
    """
    probe: dict = {"building_id": building_id}
    try:
        from request_context import set_ctx_building_id

        set_ctx_building_id(building_id)
        import routers.finance as fin
        from database import db

        actor = await db.users.find_one(
            {"role": {"$in": ["super_admin", "strata_admin", "strata_manager"]}}, {"_id": 0},
        )
        if not actor:
            probe["error"] = "no manager/admin actor available"
            return probe

        original_hook = fin.maybe_run_finance_shadow

        async def _stubbed(**_kwargs):
            return None

        fin.maybe_run_finance_shadow = _stubbed
        try:
            results = await fin.get_arrears_board(
                year=None, current_user=actor, building_id=building_id,
            )
        finally:
            fin.maybe_run_finance_shadow = original_hook

        probe["arrears"] = {
            "total_arrears_cents": round(
                sum(float(r.get("total_arrears") or 0) for r in results) * 100
            ),
            "units_in_arrears": len(results),
        }
    except Exception as exc:
        probe["error"] = repr(exc)
    return probe


def _recorded_mongo_side(row) -> dict:
    """Pull the Mongo side out of a recorded diff (same envelope as the PG side)."""
    raw = row.mongo_value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    if not isinstance(raw, dict):
        return {}
    fields = raw.get("fields") or {}
    return {k: (v or {}).get("mongo") for k, v in fields.items() if isinstance(v, dict)}


def _pg_has_data(probe: dict) -> bool:
    oc = probe.get("oc_levy_summary") or {}
    ar = probe.get("arrears") or {}
    return bool(oc.get("total_budgeted")) or bool(ar.get("units_in_arrears"))


def _recorded_pg_side(row) -> dict:
    """Pull the PG side out of a recorded diff.

    ``mongo_value`` holds the whole ``{"fields": {name: {pg, mongo, diff_cents}}}``
    envelope — the column name is a historical misnomer, not a bug to 'fix' here.
    """
    raw = row.mongo_value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    if not isinstance(raw, dict):
        return {}
    fields = raw.get("fields") or {}
    return {k: (v or {}).get("pg") for k, v in fields.items() if isinstance(v, dict)}


def _classify(row, probe: dict, mongo_probe: dict) -> tuple[str, str]:
    """Return (class, human-readable reason)."""
    live = _pg_has_data(probe)
    if row.diff_type == "pg_unavailable":
        if live:
            ar = probe.get("arrears") or {}
            oc = probe.get("oc_levy_summary") or {}
            return (
                "harness_unscoped_comparison",
                f"PostgreSQL was never queried for this row. The same builder returns a "
                f"complete payload today (FY{oc.get('financial_year')}: budgeted "
                f"{oc.get('total_budgeted')}, collected {oc.get('total_collected')}, "
                f"arrears {ar.get('total_arrears_cents')}c / {ar.get('units_in_arrears')} units). "
                f"Cause: the payload carried no financial year to scope by; the builder "
                f"returned None and run_shadow_compare recorded that as pg_unavailable. "
                f"Fixed at source by the NOT_APPLICABLE sentinel.",
            )
        return ("live_divergence", "pg_unavailable and PostgreSQL still returns no data — investigate.")

    pg_side = _recorded_pg_side(row)
    if pg_side and all((v in (0, "0", None)) for v in pg_side.values()) and live:
        ar = probe.get("arrears") or {}
        return (
            "stale_pg_side_empty",
            f"Recorded PG side was empty on every compared field ({pg_side}) while the "
            f"same builder now returns {ar.get('total_arrears_cents')}c across "
            f"{ar.get('units_in_arrears')} units for FY{ar.get('financial_year')}. "
            f"The row captured a mid-restore snapshot, not a divergence.",
        )
    mongo_side = _recorded_mongo_side(row)
    live_mongo = (mongo_probe or {}).get("arrears") or {}
    if mongo_side and live_mongo and row.route in ("finance.arrears", "finance.arrears_detail"):
        rec_units = mongo_side.get("units_in_arrears")
        live_units = live_mongo.get("units_in_arrears")
        rec_cents = mongo_side.get("total_arrears")
        live_cents = live_mongo.get("total_arrears_cents")
        if rec_units is not None and live_units is not None and int(rec_units) != int(live_units):
            return (
                "stale_mongo_side",
                f"Recorded Mongo side ({rec_cents}c / {rec_units} units) does not match what "
                f"the same route handler returns today ({live_cents}c / {live_units} units). "
                f"The row captured a mid-restore snapshot of the Mongo ledger. Live PG side "
                f"today: {(probe.get('arrears') or {}).get('total_arrears_cents')}c / "
                f"{(probe.get('arrears') or {}).get('units_in_arrears')} units.",
            )

    return (
        "live_divergence",
        f"Both sides carried data and both still produce it (recorded PG: {pg_side}, "
        f"recorded Mongo: {mongo_side}; live PG: {probe.get('arrears')}, live Mongo: "
        f"{live_mongo}). Adjudicate against source documents — never auto-resolve.",
    )


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--building-id", default=None, help="Limit to one building. Default: every building with unresolved diffs.")
    ap.add_argument("--apply", action="store_true", help="Mark the resolvable classes resolved. Default: dry-run.")
    args = ap.parse_args()

    async with _get_bypass_session_context() as session:
        params: dict = {"domain": _DOMAIN}
        where = "domain = :domain AND resolved = FALSE"
        if args.building_id:
            where += " AND building_id = :building_id"
            params["building_id"] = args.building_id
        rows = (await session.execute(
            text(f"""SELECT id, building_id, route, diff_type, mongo_value, created_at
                     FROM core.shadow_diffs WHERE {where} ORDER BY created_at"""),
            params,
        )).fetchall()

    if not rows:
        print("No unresolved finance_ledger shadow diffs. Nothing to triage.")
        return 0

    probes: dict[str, dict] = {}
    mongo_probes: dict[str, dict] = {}
    for bid in {r.building_id for r in rows}:
        probes[bid] = await _live_pg_probe(bid)
        mongo_probes[bid] = await _live_mongo_probe(bid)

    buckets: dict[tuple, list] = defaultdict(list)
    reasons: dict[tuple, str] = {}
    for r in rows:
        cls, reason = _classify(r, probes[r.building_id], mongo_probes.get(r.building_id, {}))
        key = (r.building_id, r.route, r.diff_type, cls)
        buckets[key].append(r)
        reasons.setdefault(key, reason)

    print(f"\n{'=' * 100}")
    print(f"finance_ledger shadow-diff triage — {len(rows)} unresolved row(s)")
    print(f"mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    print("=" * 100)
    for bid, probe in sorted(probes.items()):
        print(f"\nLive PostgreSQL probe for {bid}: {json.dumps(probe, default=str)}")
        print(f"Live MongoDB    probe for {bid}: {json.dumps(mongo_probes.get(bid), default=str)}")

    resolvable_ids: list = []
    for (bid, route, dt, cls), rs in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        mark = "RESOLVE" if cls in _RESOLVABLE else "KEEP   "
        print(f"\n[{mark}] {bid} {route} {dt}")
        print(f"          class : {cls}  ({len(rs)} rows, {rs[0].created_at.date()} .. {rs[-1].created_at.date()})")
        print(f"          reason: {reasons[(bid, route, dt, cls)]}")
        if cls in _RESOLVABLE:
            resolvable_ids.extend([(r.id, reasons[(bid, route, dt, cls)]) for r in rs])

    kept = len(rows) - len(resolvable_ids)
    print(f"\n{'-' * 100}")
    print(f"resolvable: {len(resolvable_ids)}    left for adjudication: {kept}")

    if not args.apply:
        print("\nDry-run. Re-run with --apply to mark the resolvable rows resolved.")
        return 0
    if not resolvable_ids:
        print("\nNothing resolvable. No writes performed.")
        return 0

    stamp = datetime.now(UTC).isoformat()
    async with _get_bypass_session_context() as session:
        for diff_id, reason in resolvable_ids:
            await session.execute(
                text("""UPDATE core.shadow_diffs
                        SET resolved = TRUE, resolved_at = NOW(),
                            notes = :note
                        WHERE id = :id AND resolved = FALSE"""),
                {"id": diff_id, "note": f"[triage {stamp}] {reason}"},
            )
        await session.commit()
    print(f"\nApplied: {len(resolvable_ids)} row(s) marked resolved.")
    print(f"Left unresolved for adjudication: {kept}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
