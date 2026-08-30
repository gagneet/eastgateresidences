#!/usr/bin/env python3
# @featuretrace:levy-fairness — bulk first load of operator-decided comparison groups.
# Layer: script
# Data flow: JSON spec -> core.benefit_groups + core.lot_benefit_groups (building-scoped).
# Related: backend/routers/benefit_groups.py
#          backend/scripts/data_repair/seed_cost_allocation_rules_20260830.py
# Tests: tests/backend/test_benefit_groups.py
"""Create benefit groups and assign lots for one building, from a JSON spec.

DRY-RUN BY DEFAULT. `--apply` writes.

The settings UI is the normal way in. This exists for the first load of a building whose
committee has already decided the split, where clicking 87 lots into two groups is not a
reasonable ask.

Names come from the spec. Prefer "Group A"/"Group B" over building-form words: the
analysis is about measured benefit, and naming a group "Townhouses" invites the reading
that the building form is what justifies a different contribution, which is not the
argument and is not what the engine measures.

    python3 backend/scripts/data_repair/seed_benefit_groups_20260830.py \
        --building-id 13195 --spec backend/scripts/data/east_gate_benefit_groups_20260830.json
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


def _expand(spec_units) -> list[str]:
    """Accept an explicit list or {prefix, start, end, width} ranges, or both."""
    out: list[str] = []
    for item in spec_units:
        if isinstance(item, str):
            out.append(item)
            continue
        pfx = item.get("prefix", "")
        width = int(item.get("width", 3))
        for n in range(int(item["start"]), int(item["end"]) + 1):
            out.append(f"{pfx}{n:0{width}d}")
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--building-id", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    with open(args.spec) as fh:
        spec = json.load(fh)

    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant
    from db_postgres.repos.identity_repo import get_scheme_by_number

    async with async_session_context() as session:
        scheme = await get_scheme_by_number(args.building_id)
        if not scheme:
            print(f"No Postgres scheme for building {args.building_id}")
            return 1
        tid, sid = str(scheme["tenant_id"]), str(scheme["scheme_id"])
        # FORCE-RLS on core.lots has no bypass clause: without this every lookup below
        # returns zero rows and no error, which reads as "this building has no lots".
        await set_tenant(session, tid)

        lots = {r[0]: r[1] for r in (await session.execute(
            text("SELECT unit_number, lot_id FROM core.lots WHERE scheme_id = :sid"),
            {"sid": sid},
        )).all()}
        print(f"building {args.building_id}: {len(lots)} lots in Postgres\n")

        plan, missing = [], []
        for grp in spec:
            wanted = _expand(grp.get("units", []))
            found = [u for u in wanted if u in lots]
            missing += [(grp["name"], u) for u in wanted if u not in lots]
            plan.append((grp, found))
            print(f"  {grp['name']:<12} {len(found):>3} lot(s)  {grp.get('description', '')}")

        if missing:
            # Refuse rather than assign a partial group: a group silently short a few lots
            # changes every share it drives, and the arithmetic still balances.
            print(f"\nREFUSING — {len(missing)} unit number(s) in the spec are not lots "
                  f"in this building:")
            for name, u in missing[:20]:
                print(f"  {name}: {u}")
            return 1

        assigned = sum(len(f) for _, f in plan)
        if assigned < len(lots):
            # Not an error. An unassigned lot is a legitimate mid-configuration state and
            # the engine reports it rather than defaulting it into a group.
            print(f"\n  note: {len(lots) - assigned} lot(s) will remain unassigned")

        if not args.apply:
            print("\nDry run. Re-run with --apply to write.")
            return 0

        for grp, found in plan:
            gid = (await session.execute(
                text("""
                    INSERT INTO core.benefit_groups
                        (tenant_id, scheme_id, name, description, display_order)
                    VALUES (:tid, :sid, :name, :desc, :ord)
                    ON CONFLICT (scheme_id, name) DO UPDATE
                        SET description = EXCLUDED.description, updated_at = now()
                    RETURNING benefit_group_id
                """),
                {"tid": tid, "sid": sid, "name": grp["name"],
                 "desc": grp.get("description"), "ord": grp.get("display_order", 0)},
            )).scalar_one()
            for un in found:
                await session.execute(
                    text("""
                        INSERT INTO core.lot_benefit_groups (lot_id, tenant_id, benefit_group_id)
                        VALUES (:lot, :tid, :gid)
                        ON CONFLICT (lot_id) DO UPDATE SET benefit_group_id = EXCLUDED.benefit_group_id
                    """),
                    {"lot": lots[un], "tid": tid, "gid": gid},
                )
        await session.commit()
        print(f"\nWrote {len(plan)} group(s), {assigned} lot assignment(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
