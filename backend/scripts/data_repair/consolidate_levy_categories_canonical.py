# @featuretrace:levy — consolidate duplicate levy_categories + backfill proposed budgets (canonical taxonomy).
# Layer: script
"""Consolidate duplicate ``levy_categories`` rows onto a canonical category, and
backfill the per-category proposed budget that was never imported.

WHY
═══
`levy_categories` is keyed on the free-text ``name``. A building's history was
loaded from several sources that spell the same economic category differently
(AGM minutes, audited statements, Civium portal, the rebuild CSV), so one
category becomes several documents — e.g. "Caretaker" (proposed budget) and
"Caretaker (Building Manager)" (actual), or "Management Fees" (one budget line)
against "Management fees - LJ Hooker/CSMS/meetings" (itemised actuals). The
visible symptoms on the Spending Categories page:

  * duplicate rows for one economic category, and
  * an all-"—" Budgeted column, because the AGM's per-category *proposed* budgets
    (94 "Proposed Category" + 49 "Adopted Budget Category" rows in
    docs/finances/up13195_agm_proposed_spent_financials.csv) were never written
    to any ``levy_categories.budgeted_amount``.

WHAT THIS DOES
══════════════
For each ``(year, fund_type, canonical_key)`` group (canonical_key from
``utils.category_taxonomy``):

  1. **Backfill budget** — if a ``--csv`` of AGM proposed/adopted rows is given,
     the group's proposed budget = the sum of the *highest-authority* tier
     present for that canonical category (Adopted > Proposed > pre-AGM draft),
     tagged with its source tier. Never fabricated — only real AGM figures.
  2. **Consolidate actuals** — exact equal-amount duplicates within a group are
     de-duplicated (counted once — the "same $27,000 recorded twice" case);
     genuinely distinct sub-lines are summed (the itemised-management-fees case).
     Every de-dup and every sum is printed for review.
  3. **Keep one doc, archive the rest** — the keeper gets the merged actual +
     backfilled budget + ``canonical_key``/``canonical_name``; superseded docs
     are marked ``is_archived=True`` with a ``merged_into`` pointer. NEVER
     hard-deleted (7-year retention).

SAFETY (mirrors scripts/data_repair/reverse_duplicate_levy_payments_20260803.py)
═══════════════════════════════════════════════════════════════════════════════
  * Defaults to a **dry-run** — its output IS the review/diagnostic. ``--apply``
    is required for any write, and is the only thing that mutates the DB.
  * Building-scoped (``--building-id``, default East Gate 13195). The merge logic
    is building-agnostic; only the CSV input is building-specific.
  * HARD-ASSERTS the aggregate invariant before writing: the building's total
    actual after consolidation equals the de-duplicated total before it (no
    actual dollar is invented, and only exact-equal duplicates are removed).
    Aborts the whole run on any mismatch rather than writing a wrong figure.
  * Idempotent: a doc already archived, or a keeper already carrying its
    canonical_key with no group siblings, is a no-op on re-run.

USAGE (from backend/)
═════════════════════
    # Dry-run diagnostic (safe, read-only) — see clusters + budgets that WOULD attach:
    venv/bin/python3 scripts/data_repair/consolidate_levy_categories_canonical.py \
        --building-id 13195 --csv ../docs/finances/up13195_agm_proposed_spent_financials.csv

    # Apply (writes to DB — required flag):
    venv/bin/python3 scripts/data_repair/consolidate_levy_categories_canonical.py \
        --building-id 13195 --csv ../docs/finances/up13195_agm_proposed_spent_financials.csv --apply
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from utils.category_taxonomy import canonical_category  # noqa: E402

# AGM proposed-budget record types, most-authoritative first.
_PROPOSED_TIERS = [
    ("Adopted Budget Category", 3, "adopted"),
    ("Proposed Category (pre-AGM draft)", 1, "pre_agm_draft"),  # check before the looser "Proposed Category"
    ("Proposed Category", 2, "proposed"),
]
_FUND_MAP = {"administrative": "administrative", "admin": "administrative", "sinking": "sinking", "capital works": "sinking"}


def _is_float(v) -> bool:
    try:
        float(v)
        return True
    except (ValueError, TypeError):
        return False


def _tier_for(record_type: str):
    """Return (rank, label) for a proposed/adopted record_type, or None."""
    rt = (record_type or "").strip()
    for prefix, rank, label in _PROPOSED_TIERS:
        if rt.startswith(prefix):
            return rank, label
    return None


def _load_proposed_budgets(csv_path: Path) -> dict[tuple[str, str, str], dict]:
    """Map (year, fund_type, canonical_key) -> {amount, tier, tier_rank, lines}.

    For each canonical category we keep only rows of the single highest-authority
    tier present, and sum the amounts within that tier (so an itemised adopted
    budget rolls up to one figure). Never blends tiers.
    """
    by_key: dict[tuple[str, str, str], dict] = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tier = _tier_for(row.get("record_type", ""))
            if not tier:
                continue
            rank, label = tier
            ly = (row.get("levy_year_covered") or "").strip()
            if not ly.upper().startswith("LY") or not ly[2:].isdigit():
                continue
            year = ly[2:]
            fund_type = _FUND_MAP.get((row.get("fund") or "").strip().lower())
            if not fund_type:
                continue
            amount = row.get("amount_aud")
            if not _is_float(amount):
                continue
            canon = canonical_category(row.get("category", ""), fund_type)
            key = (year, fund_type, canon.key)
            cur = by_key.get(key)
            if cur is None or rank > cur["tier_rank"]:
                by_key[key] = {"amount": 0.0, "tier": label, "tier_rank": rank, "lines": []}
                cur = by_key[key]
            if rank == cur["tier_rank"]:
                cur["amount"] = round(cur["amount"] + float(amount), 2)
                cur["lines"].append({"name": row.get("category"), "amount": float(amount)})
    return by_key


def _dedup_sum(amounts: list[float]) -> tuple[float, list[float]]:
    """Sum a list of amounts, counting exact-equal non-zero values only once.

    Returns (total, removed_duplicates). Handles the "same $27,000 posted twice"
    double-count while still summing genuinely distinct sub-lines.
    """
    seen: set = set()
    total = 0.0
    removed: list[float] = []
    for a in amounts:
        r = round(float(a or 0), 2)
        if r == 0:
            continue
        if r in seen:
            removed.append(r)   # exact duplicate of an amount already counted
            continue
        seen.add(r)
        total = round(total + r, 2)
    return total, removed


def _pick_keeper(docs: list[dict]) -> dict:
    """Keeper = largest actual, then largest budget, then earliest created."""
    return sorted(
        docs,
        key=lambda d: (
            -round(float(d.get("actual_amount") or 0), 2),
            -round(float(d.get("budgeted_amount") or 0), 2),
            str(d.get("created_at") or ""),
        ),
    )[0]


async def run(building_id: str, csv_path: Path | None, year_filter: str | None, apply: bool) -> dict:
    import motor.motor_asyncio

    client = motor.motor_asyncio.AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "strata_production")]

    proposed = _load_proposed_budgets(csv_path) if csv_path else {}

    query: dict = {
        "$or": [{"building_id": building_id}, {"plan_id": building_id}],
        "is_archived": {"$ne": True},
    }
    if year_filter:
        query["year"] = year_filter
    docs = await db.levy_categories.find(query, {"_id": 0}).to_list(None)

    # Group by (year, fund_type, canonical_key)
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for d in docs:
        fund_type = "administrative" if str(d.get("fund_type", "")).lower().startswith("admin") else (
            "sinking" if "sink" in str(d.get("fund_type", "")).lower() else str(d.get("fund_type") or "administrative")
        )
        canon = canonical_category(d.get("name", ""), fund_type)
        groups[(str(d.get("year")), fund_type, canon.key)].append({**d, "_canon_name": canon.display_name})

    plan: list[dict] = []
    total_actual_before_dedup = 0.0
    total_actual_after = 0.0

    for (yr, fund_type, key), gdocs in sorted(groups.items()):
        actuals = [round(float(d.get("actual_amount") or 0), 2) for d in gdocs]
        total_actual_before_dedup = round(total_actual_before_dedup + sum(actuals), 2)
        merged_actual, removed_dups = _dedup_sum(actuals)
        total_actual_after = round(total_actual_after + merged_actual, 2)

        existing_budget, _ = _dedup_sum([float(d.get("budgeted_amount") or 0) for d in gdocs])
        prop = proposed.get((yr, fund_type, key))
        target_budget = round(prop["amount"], 2) if prop else existing_budget
        budget_source = prop["tier"] if prop else ("existing" if existing_budget > 0 else "none")

        keeper = _pick_keeper(gdocs)
        others = [d for d in gdocs if d.get("id") != keeper.get("id")]
        is_dup_cluster = len(gdocs) > 1
        budget_changes = round(target_budget, 2) != round(float(keeper.get("budgeted_amount") or 0), 2)
        needs_write = is_dup_cluster or budget_changes or not keeper.get("canonical_key")

        if not needs_write:
            continue

        status = "actual" if merged_actual > 0 else ("proposed" if target_budget > 0 else keeper.get("status", "proposed"))
        plan.append({
            "year": yr, "fund_type": fund_type, "canonical_key": key,
            "canonical_name": keeper["_canon_name"],
            "keeper_id": keeper.get("id"),
            "member_names": [d.get("name") for d in gdocs],
            "actual_before": round(sum(actuals), 2),
            "actual_after_dedup": merged_actual,
            "removed_equal_duplicates": removed_dups,
            "budget_before": round(float(keeper.get("budgeted_amount") or 0), 2),
            "budget_after": target_budget,
            "budget_source": budget_source,
            "budget_lines": prop["lines"] if prop else [],
            "archive_ids": [d.get("id") for d in others],
            "status": status,
        })

    # Budgets that would attach but have NO matching category doc yet (visibility).
    covered_keys = set(groups.keys())
    orphan_budgets = [
        {"year": y, "fund_type": ft, "canonical_key": k, "amount": v["amount"], "tier": v["tier"]}
        for (y, ft, k), v in proposed.items() if (y, ft, k) not in covered_keys
    ]

    # ── Invariant: consolidation must not invent or lose actual dollars ────────
    # (after == before minus exactly the equal-amount duplicates removed)
    removed_total = round(sum(sum(p["removed_equal_duplicates"]) for p in plan), 2)
    expected_after = round(total_actual_before_dedup - removed_total, 2)
    invariant_ok = abs(expected_after - total_actual_after) < 0.01

    result = {
        "building_id": building_id,
        "dry_run": not apply,
        "groups_total": len(groups),
        "duplicate_clusters": sum(1 for p in plan if len(p["member_names"]) > 1),
        "budgets_attached": sum(1 for p in plan if p["budget_source"] not in ("existing", "none")),
        "actual_total_before_dedup": total_actual_before_dedup,
        "actual_total_after": total_actual_after,
        "equal_amount_duplicates_removed_total": removed_total,
        "invariant_no_actual_invented_or_lost": invariant_ok,
        "orphan_budgets_no_matching_category": orphan_budgets,
        "plan": plan,
    }

    if not invariant_ok:
        result["ABORTED"] = "actual-total invariant failed — refusing to write"
        return result

    if not apply:
        result["note"] = "DRY-RUN — no writes. Re-run with --apply after reviewing the plan."
        return result

    now = datetime.now(timezone.utc)
    writes = 0
    for p in plan:
        await db.levy_categories.update_one(
            {"id": p["keeper_id"]},
            {"$set": {
                "budgeted_amount": p["budget_after"],
                "actual_amount": p["actual_after_dedup"],
                "canonical_key": p["canonical_key"],
                "canonical_name": p["canonical_name"],
                "budget_source": p["budget_source"],
                "status": p["status"],
                "updated_at": now,
            }},
        )
        writes += 1
        for aid in p["archive_ids"]:
            await db.levy_categories.update_one(
                {"id": aid},
                {"$set": {
                    "is_archived": True, "archived_at": now,
                    "merged_into": p["keeper_id"],
                    "archived_reason": (
                        f"Consolidated into canonical category '{p['canonical_name']}' "
                        f"({p['canonical_key']}) by consolidate_levy_categories_canonical.py — "
                        "duplicate spelling of the same economic category."
                    ),
                }},
            )
    result["keeper_docs_updated"] = writes
    result["docs_archived"] = sum(len(p["archive_ids"]) for p in plan)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Consolidate duplicate levy_categories + backfill proposed budgets.")
    ap.add_argument("--building-id", default="13195")
    ap.add_argument("--csv", type=Path, default=None, help="AGM proposed/spent CSV for budget backfill (optional).")
    ap.add_argument("--year", default=None, help="Restrict to a single levy year (optional).")
    ap.add_argument("--apply", action="store_true", help="Write changes. Default is dry-run.")
    args = ap.parse_args()
    out = asyncio.run(run(args.building_id, args.csv, args.year, args.apply))
    print(json.dumps(out, indent=2, default=str, sort_keys=True))
    return 0 if out.get("invariant_no_actual_invented_or_lost", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
