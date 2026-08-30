#!/usr/bin/env python3
# @featuretrace:levy-fairness — read-only readiness audit for the benefit-group /
#   facility / asset / capital-schedule data the LBFI engine consumes.
# Layer: script
# Related: backend/services/levy_fairness_service.py,
#          backend/services/facility_allocation_engine.py,
#          scripts/db/seed_sinking_fund_plan.py
# Collections: benefit_groups, facilities, building_assets, unit_attributes,
#              capital_replacement_schedule, units, unit_levy_ledger, annual_levies
"""Read-only readiness audit for the Levy Fairness (LBFI) engine.

Answers one question: *is this building's benefit-group / facility / asset /
capital-works data complete and unambiguous enough for the fairness output to be
trusted in front of a committee?*

The script writes NOTHING. It reads the collections
``simulate_levy_fairness_v2`` actually consumes and reports, per benefit group:
how much annualised asset cost rolls into it, how many units it resolves to, and
whether that resolution came from explicit configuration or from the engine's
legacy prefix-guessing fallback.

Exit codes: 0 = ready, 1 = blocking gaps found (see the FINDINGS section).

Usage:
    cd backend
    set -a && source .env && set +a
    venv/bin/python3 scripts/audits/levy_fairness_data_readiness.py --building-id 13195

    # machine-readable, for diffing between runs
    venv/bin/python3 scripts/audits/levy_fairness_data_readiness.py \
        --building-id 13195 --json > /tmp/lbfi_readiness.json
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import db  # noqa: E402
from request_context import set_ctx_building_id  # noqa: E402
from services.facility_allocation_engine import calculate_facility_allocation  # noqa: E402

SEP = "─" * 78

# The endpoint that serves the capital plan to the UI caps its cursor here.
# See routers/intelligence.py::get_capital_works_schedule.
CAPITAL_WORKS_ENDPOINT_CAP = 100

findings: List[Dict[str, str]] = []


def _finding(severity: str, code: str, message: str) -> None:
    findings.append({"severity": severity, "code": code, "message": message})


def _h(title: str) -> None:
    print(f"\n{SEP}\n  {title}\n{SEP}")


def _money(dollars: float) -> str:
    return f"-${abs(dollars):,.2f}" if dollars < 0 else f"${dollars:,.2f}"


async def _load(building_id: str) -> Dict[str, Any]:
    groups = await db.benefit_groups.find({"building_id": building_id}, {"_id": 0}).to_list(500)
    facilities = await db.facilities.find({"building_id": building_id}, {"_id": 0}).to_list(1000)
    assets = await db.building_assets.find({"building_id": building_id}, {"_id": 0}).to_list(2000)
    schedule = await db.capital_replacement_schedule.find(
        {"building_id": building_id}, {"_id": 0}
    ).to_list(5000)
    units = await db.units.find({"building_id": building_id}, {"_id": 0}).to_list(2000)
    attributes = await db.unit_attributes.find({"building_id": building_id}, {"_id": 0}).to_list(2000)
    result = await db.levy_fairness_results_v2.find_one(
        {"building_id": building_id}, {"_id": 0, "computed_at": 1, "cross_subsidy_report": 1}
    )
    return {
        "groups": groups,
        "facilities": facilities,
        "assets": assets,
        "schedule": schedule,
        "units": units,
        "attributes": attributes,
        "result": result,
    }


def _resolution_mode(group: Dict[str, Any]) -> str:
    """Which arm of calculate_facility_allocation's 4-level chain this group hits."""
    if group.get("lot_numbers"):
        return "explicit lot_numbers"
    if group.get("unit_prefixes"):
        return "unit_prefixes"
    name = (group.get("name") or "").upper()
    if any(k in name for k in ("APARTMENT", "TOWNHOUSE", "BASEMENT", "GARAGE")):
        return "LEGACY name guess"
    return "ALL_LOTS default"


def _report_groups(data: Dict[str, Any]) -> Dict[str, Any]:
    _h("1. Benefit groups — how each resolves to units")
    groups = data["groups"]
    if not groups:
        _finding("BLOCKER", "NO_GROUPS",
                 "No benefit_groups exist. Every facility falls back to ALL_LOTS and the "
                 "fairness output is a flat UOE restatement with no apartment/townhouse split.")
        print("  (none)")
        return {}

    print(f"  {'name':<22} {'id':<16} {'driver':<20} resolution")
    by_id = {}
    for g in sorted(groups, key=lambda x: x.get("name") or ""):
        mode = _resolution_mode(g)
        driver = (g.get("allocation_rule") or {}).get("allocation_type") or g.get("allocation_driver") or "—"
        print(f"  {(g.get('name') or '?'):<22} {(g.get('id') or '?'):<16} {driver:<20} {mode}")
        by_id[g.get("id")] = g
        if mode == "LEGACY name guess":
            _finding("WARN", "LEGACY_RESOLUTION",
                     f"Group '{g.get('name')}' has neither lot_numbers nor unit_prefixes, so membership "
                     f"is inferred from hardcoded 'UA'/'TH'/car-space heuristics in "
                     f"facility_allocation_engine.py. Set explicit membership before quoting these "
                     f"numbers to owners.")
    return by_id


def _report_units(data: Dict[str, Any]) -> None:
    _h("2. Units and attributes")
    units = data["units"]
    print(f"  units                     : {len(units)}")
    if not units:
        _finding("BLOCKER", "NO_UNITS", "No units found — the simulation returns {'error': 'No units found'}.")
        return

    prefixes: Dict[str, int] = defaultdict(int)
    missing_ent = 0
    for u in units:
        un = str(u.get("unit_number") or "")
        prefixes["".join(c for c in un[:2] if c.isalpha()) or "(numeric)"] += 1
        if not float(u.get("entitlement") or 0):
            missing_ent += 1
    print(f"  unit_number prefixes      : {dict(prefixes)}")
    print(f"  missing/zero entitlement  : {missing_ent}")
    if missing_ent:
        _finding("BLOCKER", "MISSING_ENTITLEMENT",
                 f"{missing_ent} unit(s) have no entitlement. unit_entitlement allocation silently "
                 f"gives them a $0 share, understating their fair cost and inflating everyone else's.")

    # calculate_facility_allocation looks attributes up by unit_number, but
    # get_unit_attributes keys the dict on the raw `unit_id` field. If unit_id
    # holds a UUID rather than a unit number, every car-space/area lookup is a
    # silent miss.
    attrs = data["attributes"]
    unit_numbers = {str(u.get("unit_number")) for u in units}
    keyed_by_number = sum(1 for a in attrs if str(a.get("unit_id")) in unit_numbers)
    print(f"  unit_attributes rows      : {len(attrs)} ({keyed_by_number} keyed by unit_number)")
    if attrs and keyed_by_number == 0:
        _finding("BLOCKER", "ATTR_KEY_MISMATCH",
                 f"All {len(attrs)} unit_attributes rows key on a unit_id that is not a unit_number. "
                 f"calculate_facility_allocation() looks them up BY unit_number, so car_spaces / "
                 f"internal_area resolve to 0 and every car_space_weighted or area_weighted facility "
                 f"allocates nothing.")
    elif attrs and keyed_by_number < len(attrs):
        _finding("WARN", "ATTR_KEY_PARTIAL",
                 f"{len(attrs) - keyed_by_number} of {len(attrs)} unit_attributes rows are not keyed "
                 f"by unit_number and will be ignored by the allocation engine.")
    elif not attrs:
        _finding("WARN", "NO_ATTRIBUTES",
                 "No unit_attributes rows. car_space_weighted and area_weighted drivers will "
                 "allocate $0; GARAGE/BASEMENT groups fall back to the 'UA' prefix heuristic.")


def _report_assets(data: Dict[str, Any], groups: Dict[str, Any]) -> Dict[str, float]:
    _h("3. Facilities and assets — the annualised cost basis")
    facilities = {f.get("id"): f for f in data["facilities"]}
    assets = data["assets"]
    print(f"  facilities                : {len(facilities)}")
    print(f"  building_assets           : {len(assets)}")

    untagged_fac = [f for f in facilities.values() if not f.get("benefit_group_id")]
    if untagged_fac:
        _finding("WARN", "UNTAGGED_FACILITY",
                 f"{len(untagged_fac)} facility/ies have no benefit_group_id and default to ALL_LOTS: "
                 + ", ".join(str(f.get('name')) for f in untagged_fac[:6]))

    per_group: Dict[str, float] = defaultdict(float)
    orphan_assets, unusable = [], []
    for a in assets:
        fac = facilities.get(a.get("facility_id"))
        if a.get("facility_id") and not fac:
            orphan_assets.append(a)
        cost = float(a.get("replacement_cost_estimate") or 0)
        life = float(a.get("expected_lifespan_years") or 0)
        if cost <= 0 or life <= 0:
            unusable.append(a)
            continue
        # Asset's own tag wins, then its facility's (see _derive_virtual_cost_centres).
        bg_id = a.get("benefit_group_id") or (fac or {}).get("benefit_group_id")
        per_group[bg_id or "(untagged → ALL_LOTS)"] += cost / life

    print(f"  assets with no cost/life  : {len(unusable)}")
    print(f"  assets w/ dangling facility: {len(orphan_assets)}")
    if unusable:
        _finding("WARN", "ASSET_NOT_COSTED",
                 f"{len(unusable)} asset(s) have no replacement_cost_estimate or no "
                 f"expected_lifespan_years, so they contribute $0 to the cost basis: "
                 + ", ".join(str(a.get('name')) for a in unusable[:6]))
    if orphan_assets:
        _finding("WARN", "ORPHAN_ASSET",
                 f"{len(orphan_assets)} asset(s) point at a facility_id that does not exist; they "
                 f"inherit no benefit group and land in ALL_LOTS.")

    total = sum(per_group.values())
    print(f"\n  Annualised asset cost by benefit group (replacement_cost / lifespan):")
    if not total:
        _finding("BLOCKER", "NO_COST_BASIS",
                 "Total annualised asset cost is $0. simulate_levy_fairness_v2 falls back to a single "
                 "'All Common Costs (UOE Allocation)' cost centre — i.e. the fairness report will show "
                 "zero cross-subsidy no matter what the real split is.")
        print("    (nothing costed)")
        return per_group
    for bg_id, amount in sorted(per_group.items(), key=lambda kv: -kv[1]):
        name = (groups.get(bg_id) or {}).get("name", bg_id)
        print(f"    {str(name):<28} {_money(amount):>14}   {amount / total * 100:5.1f}%")
    print(f"    {'TOTAL':<28} {_money(total):>14}")

    empty = [g.get("name") for gid, g in groups.items() if gid not in per_group]
    if empty:
        _finding("WARN", "GROUP_NO_ASSETS",
                 f"Benefit group(s) with no costed assets at all: {', '.join(map(str, empty))}. "
                 f"They contribute nothing to the benefit model and will read as pure subsidisers.")
    return per_group


async def _report_pays_vs_benefits(
        data: Dict[str, Any], groups: Dict[str, Any], cost_basis: Dict[str, float]
) -> None:
    """Diagnostic preview of what each unit type funds versus what it benefits from.

    Allocation comes from calculate_facility_allocation() itself — the same
    function the engine uses — so this cannot drift from real allocation
    behaviour. Diagnostic only: the authoritative cross-subsidy figures come
    from GET /intelligence/levy-fairness/subsidy-map after a recompute.
    """
    _h("3b. What each unit type funds vs benefits from (diagnostic preview)")
    units = data["units"]
    if not units or not groups:
        print("  (needs units and benefit groups)")
        return
    total_cost = sum(cost_basis.values())
    if total_cost <= 0:
        print("  (no costed assets to allocate)")
        return

    attrs = {a["unit_id"]: a for a in data["attributes"] if "unit_id" in a}

    # Group membership overlaps (ALL_LOTS contains everyone), so a per-group
    # table cannot be compared like-for-like. Roll the benefit model down to
    # individual units first, then aggregate by unit type.
    benefit: Dict[str, float] = {str(u.get("unit_number")): 0.0 for u in units}
    unreachable = 0.0
    for gid, amount in cost_basis.items():
        if amount <= 0:
            continue
        if gid not in groups:
            gid = None  # untagged → engine's ALL_LOTS default
        probe = {"annual_cost": amount, "benefit_group_id": gid,
                 "allocation_driver": None}
        allocation = await calculate_facility_allocation(probe, units, groups, attrs)
        if not allocation:
            unreachable += amount
            continue
        for un, val in allocation.items():
            benefit[str(un)] = benefit.get(str(un), 0.0) + val

    total_ue = sum(float(u.get("entitlement") or 0) for u in units) or 1.0

    def _utype(u: Dict[str, Any]) -> str:
        explicit = (u.get("unit_type") or u.get("property_type") or "").strip()
        if explicit:
            return explicit.title()
        un = str(u.get("unit_number") or "").upper()
        pfx = "".join(c for c in un[:2] if c.isalpha())
        return f"{pfx}-prefixed" if pfx else "Unprefixed"

    rows: Dict[str, Dict[str, float]] = defaultdict(lambda: {"n": 0, "uoe": 0.0, "ben": 0.0})
    for u in units:
        r = rows[_utype(u)]
        r["n"] += 1
        r["uoe"] += total_cost * (float(u.get("entitlement") or 0) / total_ue)
        r["ben"] += benefit.get(str(u.get("unit_number")), 0.0)

    print(f"  Allocating the {_money(total_cost)} annualised asset basis two ways:\n")
    print(f"  {'unit type':<18} {'units':>6} {'pays (UOE)':>14} {'benefits':>14} {'difference':>14}")
    for name, r in sorted(rows.items(), key=lambda kv: -kv[1]["uoe"]):
        diff = r["ben"] - r["uoe"]
        shown = f"+{_money(diff)}" if diff > 0 else _money(diff)
        print(f"  {name:<18} {int(r['n']):>6} {_money(r['uoe']):>14} {_money(r['ben']):>14} "
              f"{shown:>14}")

    print("\n  A negative difference means that unit type funds more common cost than the")
    print("  benefit model attributes to it — i.e. it is subsidising the others.")
    if unreachable:
        print(f"  NOTE: {_money(unreachable)} could not be allocated to any unit and is excluded.")
    print("\n  Diagnostic only — authoritative: GET /intelligence/levy-fairness/subsidy-map.")


def _report_capital(data: Dict[str, Any], groups: Dict[str, Any]) -> None:
    _h("4. Capital works schedule — the forward, itemised plan")
    schedule = data["schedule"]
    print(f"  capital_replacement_schedule rows : {len(schedule)}")
    if not schedule:
        _finding("WARN", "NO_CAPITAL_SCHEDULE",
                 "No capital_replacement_schedule rows. There is no per-year itemised capital plan to "
                 "split by benefit group — run scripts/db/seed_sinking_fund_plan.py (or import the real "
                 "plan) first.")
        return

    # replacement_year may be missing, null, or a non-numeric string. Parse
    # defensively: an undated row still carries a real cost and must stay in the
    # per-group totals below, it just cannot be placed on the year axis.
    years: set = set()
    undated = 0
    for r in schedule:
        try:
            years.add(int(r["replacement_year"]))
        except (KeyError, TypeError, ValueError):
            undated += 1
    year_list = sorted(years)
    untagged = [r for r in schedule if not r.get("benefit_group_id")]

    if year_list:
        print(f"  years covered                     : {year_list[0]}–{year_list[-1]} ({len(year_list)} years)")
    else:
        print(f"  years covered                     : (none — no usable replacement_year)")
    print(f"  rows with no usable year          : {undated}")
    print(f"  rows with no benefit_group_id     : {len(untagged)}")

    if undated:
        # Not a blocker: simulate_levy_fairness_v2 allocates schedule rows by
        # estimated_cost and benefit_group_id only, never by year, so the LBFI
        # maths is unaffected. It is the forward per-year capital view that breaks.
        _finding("WARN", "CAPITAL_UNDATED",
                 f"{undated} capital row(s) have a missing or non-numeric replacement_year. Their cost "
                 f"still counts toward the benefit-group totals, but they cannot appear on a per-year "
                 f"capital schedule — which is exactly the view Phase 3 builds.")

    if len(schedule) > CAPITAL_WORKS_ENDPOINT_CAP:
        _finding("BLOCKER", "CAPITAL_ENDPOINT_TRUNCATION",
                 f"{len(schedule)} rows exceed the .to_list({CAPITAL_WORKS_ENDPOINT_CAP}) cap in "
                 f"routers/intelligence.py::get_capital_works_schedule — the UI is silently missing "
                 f"{len(schedule) - CAPITAL_WORKS_ENDPOINT_CAP} line item(s).")
    else:
        headroom = CAPITAL_WORKS_ENDPOINT_CAP - len(schedule)
        print(f"  endpoint cap headroom             : {headroom} rows before truncation")
        if headroom <= 25:
            _finding("WARN", "CAPITAL_ENDPOINT_HEADROOM",
                     f"Only {headroom} rows of headroom under the .to_list({CAPITAL_WORKS_ENDPOINT_CAP}) "
                     f"cap in get_capital_works_schedule. Raise the cap before adding more line items.")
    if untagged:
        _finding("WARN", "CAPITAL_UNTAGGED",
                 f"{len(untagged)} capital line-item/year row(s) have no benefit_group_id and will be "
                 f"allocated across ALL lots by entitlement.")

    per_group: Dict[str, float] = defaultdict(float)
    for r in schedule:
        per_group[r.get("benefit_group_id") or "(untagged → ALL_LOTS)"] += float(r.get("estimated_cost") or 0)
    grand = sum(per_group.values())
    span = f" {year_list[0]}–{year_list[-1]}" if year_list else ""
    print(f"\n  Planned capital spend{span} by benefit group:")
    for bg_id, amount in sorted(per_group.items(), key=lambda kv: -kv[1]):
        name = (groups.get(bg_id) or {}).get("name", bg_id)
        pct = (amount / grand * 100) if grand else 0
        print(f"    {str(name):<28} {_money(amount):>14}   {pct:5.1f}%")
    print(f"    {'TOTAL':<28} {_money(grand):>14}")


def _report_result(data: Dict[str, Any]) -> None:
    _h("5. Last computed fairness result")
    result = data["result"]
    if not result:
        print("  none — POST /intelligence/levy-fairness/recompute has never been run for this building")
        _finding("WARN", "NO_RESULT",
                 "No stored levy_fairness_results_v2 document; the page computes on the fly each load.")
        return
    print(f"  computed_at : {result.get('computed_at')}")
    report = result.get("cross_subsidy_report") or []
    if isinstance(report, list) and report:
        print(f"  cross-subsidy rows: {len(report)}")


def _report_findings() -> int:
    _h("FINDINGS")
    if not findings:
        print("  ✅ No gaps found — the fairness output is safe to put in front of the committee.")
        return 0
    blockers = [f for f in findings if f["severity"] == "BLOCKER"]
    for f in findings:
        icon = "❌" if f["severity"] == "BLOCKER" else "⚠️ "
        print(f"  {icon} [{f['code']}] {f['message']}\n")
    print(f"  {len(blockers)} blocker(s), {len(findings) - len(blockers)} warning(s)")
    return 1 if blockers else 0


async def run(building_id: str, as_json: bool) -> int:
    # findings is module-level so the reporters can append without threading an
    # accumulator through every call. Reset it here so a second in-process call
    # (tests, or an importer looping over buildings) cannot inherit the first
    # run's findings and its exit code.
    findings.clear()
    set_ctx_building_id(building_id)
    data = await _load(building_id)

    # Run every reporter into a buffer so --json can discard the prose while
    # still collecting the findings the reporters raise as a side effect.
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        print(f"\nLevy Fairness data readiness — building {building_id}")
        by_id = _report_groups(data)
        _report_units(data)
        cost_basis = _report_assets(data, by_id)
        await _report_pays_vs_benefits(data, by_id, cost_basis)
        _report_capital(data, by_id)
        _report_result(data)
        exit_code = _report_findings()

    if as_json:
        print(json.dumps({
            "building_id": building_id,
            "counts": {
                "benefit_groups": len(data["groups"]),
                "facilities": len(data["facilities"]),
                "building_assets": len(data["assets"]),
                "capital_schedule_rows": len(data["schedule"]),
                "units": len(data["units"]),
                "unit_attributes": len(data["attributes"]),
            },
            "findings": findings,
        }, indent=2, default=str))
    else:
        print(buffer.getvalue(), end="")
    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--building-id", required=True,
                        help="Building / Unit Plan number to audit, e.g. 13195")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.building_id, args.json)))


if __name__ == "__main__":
    main()
