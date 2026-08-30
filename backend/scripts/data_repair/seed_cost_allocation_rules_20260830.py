#!/usr/bin/env python3
# @featuretrace:levy-fairness — operator-supplied drivers recorded as data, not code.
# Layer: script
# Data flow: operator answers -> core.cost_allocation_rules -> levy_fairness_service (building-scoped).
# Related: backend/services/cost_allocation_rules.py
#          backend/routers/cost_allocation_rules.py
# Tests: tests/backend/test_cost_allocation_rules.py
"""Record apportionment rules supplied by an operator for one building.

DRY-RUN BY DEFAULT. `--apply` writes to core.cost_allocation_rules.

Rules are DATA. They belong in a table an operator can correct, not in a service where
they would be one scheme's facts compiled into every scheme's logic. This script is the
bootstrap for a building whose committee has already answered the questions, before the
benefit-assignment UI exists; afterwards the UI is the way in and this is only useful for
a bulk first load.

The values below are read from a JSON file, so this script names no building and no
driver. East Gate's answers of 2026-08-30 ship as
`backend/scripts/data/east_gate_allocation_rules_20260830.json`.

    python3 backend/scripts/data_repair/seed_cost_allocation_rules_20260830.py \
        --building-id 13195 --rules backend/scripts/data/east_gate_allocation_rules_20260830.json
    ... --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--building-id", required=True)
    ap.add_argument("--rules", required=True, help="path to the rules JSON file")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    with open(args.rules) as fh:
        rules = json.load(fh)
    if not isinstance(rules, list):
        print("rules file must be a JSON array of rule objects")
        return 2

    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant
    from db_postgres.repos.identity_repo import get_scheme_by_number
    from services.cost_allocation_rules import VALID_BASES, VALID_TREATMENTS

    async with async_session_context() as session:
        scheme = await get_scheme_by_number(args.building_id)
        if not scheme:
            print(f"No Postgres scheme for building {args.building_id}")
            return 1
        await set_tenant(session, str(scheme["tenant_id"]))

        # Group NAMES are what an operator writes; the engine keys on them too
        # (`_configured_lot_groups` returns names), so resolve and validate rather than
        # trusting the file. A driver value naming a group that does not exist is silently
        # dropped at allocation time, which reads as that group benefiting from nothing.
        known = {r[0] for r in (await session.execute(
            text("SELECT name FROM core.benefit_groups WHERE scheme_id = :sid"),
            {"sid": str(scheme["scheme_id"])},
        )).all()}

        problems = []
        for r in rules:
            if r.get("basis") not in VALID_BASES:
                problems.append(f"{r.get('cost_line')}: bad basis {r.get('basis')!r}")
            if r.get("unassigned_treatment", "entitlement") not in VALID_TREATMENTS:
                problems.append(f"{r.get('cost_line')}: bad treatment")
            for g in (r.get("driver_values") or {}):
                if g not in known:
                    problems.append(f"{r.get('cost_line')}: unknown group {g!r} "
                                    f"(configured: {sorted(known)})")
        if problems:
            print("REFUSING — the rules file does not match this building:")
            for p in problems:
                print("  " + p)
            return 1

        print(f"{'APPLY' if args.apply else 'DRY-RUN'} — {len(rules)} rule(s) "
              f"for building {args.building_id}\n")
        for r in rules:
            vals = r.get("driver_values") or {}
            print(f"  {r['cost_line']:<24} {r['basis']:<16} {r.get('driver') or '-':<26} "
                  f"{vals} unassigned={r.get('unassigned_units')} "
                  f"({r.get('unassigned_treatment', 'entitlement')})")
            print(f"      evidence: {r.get('evidence_source') or 'NONE RECORDED'}")

        if not args.apply:
            print("\nDry run. Re-run with --apply to write.")
            return 0

        for r in rules:
            await session.execute(
                text("""
                    INSERT INTO core.cost_allocation_rules
                        (tenant_id, scheme_id, cost_line, cost_line_label, basis, driver,
                         driver_unit, driver_period, driver_values, unassigned_units,
                         unassigned_treatment, evidence_ref, evidence_source, notes,
                         decided_at)
                    VALUES (:tid, :sid, :line, :label, :basis, :driver, :unit, :period,
                            CAST(:values AS JSONB), :unassigned, :treatment, :eref,
                            :esrc, :notes, now())
                    ON CONFLICT (scheme_id, cost_line) DO UPDATE SET
                        cost_line_label = EXCLUDED.cost_line_label,
                        basis = EXCLUDED.basis,
                        driver = EXCLUDED.driver,
                        driver_unit = EXCLUDED.driver_unit,
                        driver_period = EXCLUDED.driver_period,
                        driver_values = EXCLUDED.driver_values,
                        unassigned_units = EXCLUDED.unassigned_units,
                        unassigned_treatment = EXCLUDED.unassigned_treatment,
                        evidence_ref = EXCLUDED.evidence_ref,
                        evidence_source = EXCLUDED.evidence_source,
                        notes = EXCLUDED.notes,
                        updated_at = now()
                """),
                {
                    "tid": str(scheme["tenant_id"]), "sid": str(scheme["scheme_id"]),
                    "line": r["cost_line"], "label": r.get("cost_line_label"),
                    "basis": r["basis"], "driver": r.get("driver"),
                    "unit": r.get("driver_unit"), "period": r.get("driver_period"),
                    "values": json.dumps(r.get("driver_values") or {}),
                    "unassigned": r.get("unassigned_units"),
                    "treatment": r.get("unassigned_treatment", "entitlement"),
                    "eref": r.get("evidence_ref"), "esrc": r.get("evidence_source"),
                    "notes": r.get("notes"),
                },
            )
        await session.commit()
        print(f"\nWrote {len(rules)} rule(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
