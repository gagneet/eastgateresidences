"""
GAP-FIN-046 — PostgreSQL Receipt-Allocation Integrity Repair (2026-08-05)
============================================================================
Fixes the two-part allocation-layer bug documented in
tasks/GAP-FIN-046-pg-receipt-allocation-integrity.md (found independently, twice,
2026-08-04: once via this session's building_overview shadow-diff trace, once via
a parallel session's east_gate_finance_pg_mongo_diff_report_20260804.py). Root
cause is in `finance.receipt_allocations` / `finance.levy_items.paid_cents`, NOT
`finance.receipts` (which GAP-FIN-045 / the 73-unit sync already reconciled).

Bug A — receipt reversal never cascades to allocations. `FinancialCoreService
.reverse_entry()` (services/financial_core/service.py:809) creates a mirrored
reversal journal entry but never touches `finance.receipt_allocations` or
decrements `finance.levy_items.paid_cents` (which is an ADD-only running counter,
incremented in ledger_repo.py:605's `save_allocations`). Building-wide: 87 units,
3,498 stale allocation rows, +$1,765,516.47 phantom "paid".

Bug B — real receipts posted to finance.receipts were never allocated at all —
zero finance.receipt_allocations rows exist for them. The original GAP-FIN-046
finding (both this session's and the parallel session's) scoped this to "the 342
2026 receipts" because that was what explained building_overview's 2026-specific
$2,275.50 divergence. AUDIT CORRECTION (2026-08-05): that scope was never
independently verified against other years, and it was wrong -- of 1,740 active
pre-2026 receipts (the 2021-2025 history already confirmed itemized-correct
against Mongo earlier this session), 1,726 (99.2%, $1,546,796.82) are ALSO
completely unallocated. An earlier draft of this script fixed Part 1 (Bug A)
across all 6 years but scoped Part 2 (Bug B) to 2026 only -- that asymmetry
would have made the per-year hard-assert fail for nearly every unit, since
removing 2021-2025's phantom credit (Part 1) with nothing to replace it (Part 2
not scoped there) leaves those years genuinely under-paid relative to Mongo.
Part 2 is now scoped to every unallocated active receipt, any year.

ORDER MATTERS: Bug A must be fixed before Bug B, per unit. allocate_payment()
allocates against get_open_levy_items(), whose outstanding_cents = principal -
paid_cents at call time -- if Bug A's phantom paid_cents is still inflating a
unit's items when Bug B runs, real new allocations would either get skipped
(item looks falsely closed) or land on the wrong item.

DEVIATION FROM "NEVER HARD-DELETE", FLAGGED EXPLICITLY: finance.receipt_allocations
has a DB-level CHECK (allocated_cents > 0), so an offsetting negative "reversal"
row (the pattern used everywhere else this session for receipts/journal entries)
is not possible for this table. This script DELETES the stale allocation rows
tied to reversed receipts (Bug A) rather than leaving them in place, since -- unlike
a receipt or journal entry -- an allocation row is a derived bookkeeping linkage,
not itself financial evidence; the reversal journal entry is the actual audit
trail. Flagging this explicitly rather than deciding it silently: if you want a
non-destructive alternative, the schema needs a migration (status column) first.

Per-unit transactional discipline: both parts run per-unit inside a SAVEPOINT
(session.begin_nested()). The exact set of levy_item_ids actually touched (Part
1's deletions + Part 2's real AllocatePaymentCommand return value -- never a
date-derived guess) determines the exact set of financial_years to verify. A
unit's changes are only kept if EVERY touched year's sum(levy_items.paid_cents)
matches that year's own unit_levy_ledger.total_paid (Mongo, already
GAP-FIN-035-verified-correct for 2026 and confirmed present for other years at
apply time) within 2 cents -- otherwise that unit's savepoint is rolled back and
it's reported for manual review, exactly like every other repair script this
session. A touched year with no Mongo ledger doc to check against is a hard
failure for that unit, never a silent pass. Nothing is committed until every
kept unit has independently passed its own hard-assert.

Usage (from backend/):
    venv/bin/python3 scripts/data_repair/gap_fin_046_allocation_integrity_repair_20260805.py             # dry-run
    venv/bin/python3 scripts/data_repair/gap_fin_046_allocation_integrity_repair_20260805.py --apply

Safety guarantees:
    - Defaults to --dry-run; --apply required for any DB writes.
    - Hard-coded to building_id="13195" (East Gate).
    - Per-unit SAVEPOINT + hard-assert before keeping any change for that unit;
      a unit that doesn't reconcile cleanly is skipped, reported, never partially
      written.
    - Uses only the canonical `FinancialCoreService.allocate_payment()` for new
      allocations (Bug B) -- never raw INSERT. Bug A's deletion + paid_cents
      decrement is raw SQL (no canonical "unallocate" command exists yet); every
      row deleted is logged with its receipt_id/levy_item_id/amount before removal.
    - Idempotent: re-running after a partial failure is safe -- a levy_item with
      no more stale allocations, or a receipt already allocated, is a no-op.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

import asyncpg

BUILDING_ID = "13195"
TENANT_ID = "9e9d75c2-bd92-4695-8487-1592018c3af9"

# Discovered live 2026-08-05: the phantom lump-sum reversed receipt (Bug A)
# allocated across a unit's ENTIRE open levy history, not just FY2026 -- stale
# allocations exist for 2021 through 2026. The hard-assert below therefore
# checks every affected year per unit, not just 2026 (an earlier draft of this
# script only checked 2026 and would have applied 4 years of unverified change).
LEDGER_YEARS = ["2021", "2022", "2023", "2024", "2025", "2026"]

# GAP-FIN-046 (2026-08-05): three units carry a NAMED data-authenticity suspicion
# that no Postgres/Mongo state can adjudicate — their underlying active receipts are
# themselves under suspicion, so allocating them would pass the now-honest hard-assert
# while committing WRONG figures:
#   TH074, TH077 — suspected phantom "assume paid on time" generator receipts.
#   UA050        — an unreconcilable exact-timestamp duplicate (distinct UUIDs, and
#                  Mongo's own total_paid is ~$15 off, so neither store is a clean anchor).
# Resolving them needs real portal per-payment history, which does not exist anywhere
# in this system (staging_strata_web_snapshots holds balance snapshots only). They are
# therefore EXCLUDED BY DEFAULT from --apply; --include-flagged is the explicit opt-in a
# human must use only AFTER an independent per-payment evidence source resolves them.
FLAGGED_UNRESOLVED_UNITS = {"TH074", "TH077", "UA050"}

# GAP-FIN-057/046 collision (2026-08-09): these 10 units were resolved via a SEPARATE,
# narrower repair (gap_fin_046_phantom_removal_portal_anchored_20260809.py) that deletes
# ONLY their stale/phantom receipt_allocations rows and deliberately does NOT touch
# finance.levy_items.paid_cents -- paid_cents stays anchored to the 2026-08-06
# portal-evidenced adjustment (a human, evidenced decision: for these units, real
# reconstructed receipts sum to exactly the levied amount every year, so allocating them
# would silently re-introduce the same fabricated "paid in full" overstatement the 08-06
# adjustment exists to correct -- explicitly rejected, 2026-08-09: "we don't go
# backwards"). Their receipt_allocations no longer sum to paid_cents by design; that
# divergence is the intended, documented, permanent state.
#
# 2026-08-09's `unallocated` query fix (broadening detection from "zero allocations" to
# "any remaining balance", to catch UA030's partially-allocated stray receipt) has a
# dangerous side effect on these 10: since Option-1 deleted their allocation rows without
# touching paid_cents, and paid_cents is an ADD-only counter (ledger_repo.py's
# save_allocations), running Part 2 against them now would DOUBLE-COUNT -- adding their
# full active-receipt total on top of an already-correct paid_cents. Hard-excluded here,
# unconditionally (no --include-flagged-style override; there is no future evidence that
# would make backfilling them correct -- the 08-06 finding is the evidence).
PORTAL_ANCHORED_EXCLUDED_UNITS = {
    "UA001", "UA009", "UA013", "UA016", "UA028", "UA040", "UA042", "UA058", "UA067", "UA070",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", default=False, help="Write. Without this flag, dry-run only.")
    p.add_argument("--only-units", default="", help="Comma-separated allowlist: repair ONLY these unit numbers.")
    p.add_argument("--exclude-units", default="", help="Comma-separated unit numbers to skip (added to the default flagged set).")
    p.add_argument("--include-flagged", action="store_true", default=False,
                   help=f"Opt in to also repair the flagged-unresolved units ({', '.join(sorted(FLAGGED_UNRESOLVED_UNITS))}). "
                        "Only use after their receipt authenticity is independently resolved — see module docstring.")
    return p.parse_args()


def _select_units(
        all_units: set[str],
        *,
        only_units: set[str] | None = None,
        exclude_units: set[str] | None = None,
        include_flagged: bool = False,
) -> tuple[set[str], dict[str, str]]:
    """Decide which units to repair. Pure/testable — no DB.

    Returns (kept, excluded) where `excluded` maps unit_number -> reason. The
    filter is purely RESTRICTIVE: it can only shrink the working set, never add a
    unit, so it can never make --apply touch more than the raw scope.
    """
    only_units = only_units or set()
    exclude_units = exclude_units or set()
    flagged = set() if include_flagged else set(FLAGGED_UNRESOLVED_UNITS)

    kept: set[str] = set()
    excluded: dict[str, str] = {}
    for u in all_units:
        if u in PORTAL_ANCHORED_EXCLUDED_UNITS:
            excluded[u] = ("portal-anchored (GAP-FIN-057/046 collision, 2026-08-09) — "
                            "paid_cents intentionally != SUM(receipt_allocations); no override flag")
        elif only_units and u not in only_units:
            excluded[u] = "not in --only-units allowlist"
        elif u in flagged:
            excluded[u] = "flagged unresolved (needs per-payment evidence; pass --include-flagged to override)"
        elif u in exclude_units:
            excluded[u] = "in --exclude-units"
        else:
            kept.add(u)
    return kept, excluded


async def _get_mongo():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(__import__("os").getenv("MONGO_URL"))
    return client[__import__("os").getenv("DB_NAME", "strataos_production")]


async def _fetch_scope() -> dict:
    """Read-only pass (plain asyncpg, real tenant_id -- finance.* has no RLS
    bypass clause): everything needed to build the per-unit plan."""
    database_url = __import__("os").environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
    conn = await asyncpg.connect(database_url)
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", TENANT_ID)

    stale = await conn.fetch("""
        SELECT l.unit_number, ra.allocation_id, ra.receipt_id, ra.levy_item_id, ra.allocated_cents,
               lr.financial_year
        FROM finance.receipt_allocations ra
        JOIN finance.receipts r ON r.receipt_id = ra.receipt_id
        JOIN finance.journal_entries rev ON rev.reversal_of_id = r.journal_entry_id
        JOIN finance.levy_items li ON li.levy_item_id = ra.levy_item_id
        JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
        JOIN core.lots l ON l.lot_id = li.lot_id
        WHERE r.tenant_id = $1
    """, TENANT_ID)

    # AUDIT CORRECTION (2026-08-05): originally scoped to r.received_on >= '2026-01-01'
    # (the 342 receipts that explained building_overview's 2026-specific divergence).
    # That scope was never checked against other years and was wrong -- 1,726 of 1,740
    # pre-2026 active receipts are ALSO unallocated. Every unallocated active receipt,
    # any year, must be included or Part 1's cross-year fix (which does span all years)
    # has nothing to replace what it removes in 2021-2025.
    # AUDIT CORRECTION (2026-08-09, GAP-FIN-057/046 collision follow-up, UA030): the
    # original `ra.allocation_id IS NULL` filter only caught receipts with ZERO
    # allocation rows -- a receipt already carrying a PARTIAL allocation (e.g. a stray
    # cross-period allocation from historical reconstruction) was silently invisible
    # here, permanently stranding its remaining balance. Root-caused live on East Gate
    # UA030: a 2021 receipt with $101.94 already allocated to an unrelated 2026 item
    # left its remaining $181.90 unaccounted for by either "already allocated" or
    # "unallocated" bookkeeping -- exactly the observed FY2026 shortfall. Now selects
    # every active receipt's REMAINING balance (amount_cents minus its own existing
    # allocations, which may be zero), including receipts that are only partially
    # allocated. allocate_payment() (fixed same day) tops up the remainder rather than
    # re-allocating the full amount, so this is safe to run even on a receipt that
    # already has some allocation.
    unallocated = await conn.fetch("""
        SELECT l.unit_number, r.receipt_id,
               r.amount_cents - COALESCE(alloc.total, 0) AS remaining_cents,
               r.received_on
        FROM finance.receipts r
        JOIN core.lots l ON l.lot_id = r.lot_id
        LEFT JOIN finance.journal_entries rev ON rev.reversal_of_id = r.journal_entry_id
        LEFT JOIN LATERAL (
            SELECT SUM(ra.allocated_cents) AS total
            FROM finance.receipt_allocations ra
            WHERE ra.receipt_id = r.receipt_id
        ) alloc ON true
        WHERE r.tenant_id = $1 AND rev.journal_entry_id IS NULL
          AND r.amount_cents - COALESCE(alloc.total, 0) > 0
        ORDER BY r.received_on, r.receipt_id
    """, TENANT_ID)
    # CRITICAL ORDERING (2026-08-05): receipts MUST be allocated oldest-first. Part 1
    # reopens EVERY year's levy_items (historical was never allocated — active_alloc=$0
    # for all 87 units per the state diagnostic), and allocate_payment() fills the
    # OLDEST open item first (get_open_levy_items ORDER BY created_at ASC). Without this
    # ORDER BY, a 2026 receipt processed before the 2021 ones lands on a 2021 item,
    # shifting money across years so the per-year hard-assert fails and the unit rolls
    # back — turning a correct repair into a mass no-op. Chronological order makes each
    # receipt settle its own period.

    await conn.close()

    by_unit: dict[str, dict] = {}
    for r in stale:
        u = by_unit.setdefault(r["unit_number"], {"stale": [], "unallocated": []})
        u["stale"].append(dict(r))
    for r in unallocated:
        u = by_unit.setdefault(r["unit_number"], {"stale": [], "unallocated": []})
        u["unallocated"].append(dict(r))
    return by_unit


async def main() -> int:
    args = parse_args()
    by_unit = await _fetch_scope()
    db = await _get_mongo()

    # Restrictive unit filter (GAP-FIN-046): exclude the flagged-unresolved units by
    # default (+ any --exclude-units), or narrow to --only-units. Applies to BOTH the
    # dry-run preview and --apply, so a scoped run touches exactly the intended set.
    _only = {u.strip() for u in args.only_units.split(",") if u.strip()}
    _excl = {u.strip() for u in args.exclude_units.split(",") if u.strip()}
    kept, excluded = _select_units(
        set(by_unit), only_units=_only, exclude_units=_excl, include_flagged=args.include_flagged,
    )
    if excluded:
        print(f"Excluding {len(excluded)} of {len(by_unit)} in-scope units from this run:")
        for u in sorted(excluded):
            print(f"  - {u}: {excluded[u]}")
        print()
    by_unit = {u: v for u, v in by_unit.items() if u in kept}
    if not by_unit:
        print("No units remain after filtering — nothing to do.")
        return 0

    total_stale_cents = sum(s["allocated_cents"] for u in by_unit.values() for s in u["stale"])
    total_unallocated_cents = sum(r["remaining_cents"] for u in by_unit.values() for r in u["unallocated"])
    print(f"Scope: {len(by_unit)} units affected. "
          f"Stale allocations to remove: ${total_stale_cents/100:,.2f}. "
          f"Missing allocations to create: ${total_unallocated_cents/100:,.2f}.")
    print()

    # Planning-phase "years touched" is a HEURISTIC (received_on's calendar year for
    # unallocated receipts, financial_year join for stale allocations) — used only for
    # the dry-run preview below. It is NOT what the --apply hard-assert relies on: that
    # computes the EXACT touched years from the EXACT levy_item_ids Part 1 deleted from
    # and Part 2's real AllocatePaymentCommand return value, after both have actually run
    # (see the apply loop). A date-derived heuristic here could be wrong if a receipt's
    # received_on doesn't fall in the same calendar year as the levy period it settles;
    # the real hard-assert never depends on this guess.
    plan = []
    skipped = []
    for unit_number, u in sorted(by_unit.items()):
        approx_years = sorted({str(s.get("financial_year") or "") for s in u["stale"]} |
                               {str(r["received_on"].year) for r in u["unallocated"]})
        approx_years = [y for y in approx_years if y]

        plan.append({
            "unit_number": unit_number,
            "stale_count": len(u["stale"]),
            "stale_cents": sum(s["allocated_cents"] for s in u["stale"]),
            "unallocated_count": len(u["unallocated"]),
            "unallocated_cents": sum(r["remaining_cents"] for r in u["unallocated"]),
            "unallocated_receipt_ids": [r["receipt_id"] for r in u["unallocated"]],
            "approx_years": approx_years,
        })

    print(f"{len(plan)} units in scope for repair. Sample:")
    for entry in plan[:10]:
        years_str = ",".join(entry["approx_years"])
        print(f"  {entry['unit_number']:6}  remove {entry['stale_count']} stale (${entry['stale_cents']/100:>9,.2f})  "
              f"add {entry['unallocated_count']} allocation(s) (${entry['unallocated_cents']/100:>9,.2f})  "
              f"approx years=[{years_str}]")
    if len(plan) > 10:
        print(f"  ... {len(plan) - 10} more")
    print()

    if not args.apply:
        print("Dry-run complete. Re-run with --apply to write these changes.")
        print("NOTE: Bug A's fix DELETES stale finance.receipt_allocations rows (see module docstring for why —")
        print("the table's CHECK(allocated_cents > 0) constraint rules out a non-destructive offsetting row).")
        return 0

    from db_postgres.repos import config_repo
    from db_postgres.session import async_session_context, set_tenant
    from services.financial_core import get_financial_core_service
    from services.financial_core.domain.entities import AllocatePaymentCommand, SchemeRef
    from sqlalchemy import text as sa_text

    scheme = await config_repo.resolve_scheme_context(BUILDING_ID)
    scheme_ref = SchemeRef(tenant_id=UUID(str(scheme["tenant_id"])), scheme_id=UUID(str(scheme["scheme_id"])))

    kept, rolled_back = [], []
    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))
        svc = get_financial_core_service(session)

        for entry in plan:
            unit_number = entry["unit_number"]
            try:
                async with session.begin_nested():
                    # Part 1 (Bug A): delete stale allocations tied to reversed receipts,
                    # decrement paid_cents to match (mirrors ledger_repo.py:605's own
                    # additive pattern, in reverse). Track exactly which levy_item_ids were
                    # touched -- needed below to compute exact touched years, not a guess.
                    touched_levy_item_ids = set()
                    for s in by_unit[unit_number]["stale"]:
                        await session.execute(
                            sa_text("DELETE FROM finance.receipt_allocations WHERE allocation_id = :aid"),
                            {"aid": s["allocation_id"]},
                        )
                        await session.execute(
                            sa_text(
                                "UPDATE finance.levy_items SET paid_cents = paid_cents - :amt "
                                "WHERE levy_item_id = :lid"
                            ),
                            {"amt": s["allocated_cents"], "lid": s["levy_item_id"]},
                        )
                        touched_levy_item_ids.add(s["levy_item_id"])

                    # Part 2 (Bug B): allocate the previously-unallocated receipts through
                    # the canonical path. Must run AFTER Part 1 within the same savepoint —
                    # get_open_levy_items() reads live (uncommitted-to-outer, but visible
                    # within this transaction) state. allocate_payment()'s return value is
                    # the exact set of new allocations created — used below, not guessed.
                    for receipt_id in entry["unallocated_receipt_ids"]:
                        new_allocs = await svc.allocate_payment(AllocatePaymentCommand(scheme_ref=scheme_ref, receipt_id=receipt_id))
                        for alloc in new_allocs:
                            if alloc.levy_item_id is not None:
                                touched_levy_item_ids.add(alloc.levy_item_id)

                    if not touched_levy_item_ids:
                        # Nothing actually touched for this unit (e.g. a stale allocation
                        # already cleaned up by a prior partial run, and no unallocated
                        # receipts remain) -- idempotent no-op, not a failure.
                        kept.append(unit_number)
                        continue

                    # Exact touched years, from the exact levy_items touched -- not the
                    # planning-phase heuristic.
                    years_result = await session.execute(
                        sa_text("""
                            SELECT DISTINCT lr.financial_year
                            FROM finance.levy_items li
                            JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                            WHERE li.levy_item_id = ANY(:ids)
                        """),
                        {"ids": list(touched_levy_item_ids)},
                    )
                    touched_years = [row[0] for row in years_result.fetchall()]

                    # Hard-assert: EVERY actually-touched year's paid_cents now matches
                    # Mongo's already-verified total_paid for that year -- not just an
                    # aggregate across years, which could hide one year overshooting while
                    # another undershoots (the exact "netting masks the real error" pattern
                    # this whole GAP-FIN-046 investigation started from). A touched year
                    # with no Mongo ledger doc to verify against is a hard failure, not a
                    # silent pass -- never commit a change this script can't verify.
                    #
                    # AUDIT CORRECTION (2026-08-05, after the first --apply run rolled back
                    # 28 units): 14 of those rollbacks were units in CREDIT (net_balance < 0,
                    # total_paid > total_levied) -- not a data bug at all.
                    # allocate_payment()/get_open_levy_items() can only allocate against OPEN
                    # (outstanding) levy items, so once a unit's real receipts fully cover its
                    # total_levied for the year, the excess (the credit) has no levy_item left
                    # to attach to -- confirmed live: every one of the 14 landed EXACTLY on
                    # total_levied, not some arbitrary partial figure. levy_items.paid_cents
                    # structurally cannot exceed total_levied under the current allocation
                    # model; the credit balance is correctly tracked separately by Mongo's own
                    # net_balance field, not by this table. Target is min(total_paid,
                    # total_levied) -- a no-op change for the other 14 (genuine arrears) units,
                    # where total_paid is already <= total_levied.
                    for year in touched_years:
                        ledger = await db.unit_levy_ledger.find_one(
                            {"building_id": BUILDING_ID, "unit_number": unit_number, "year": year}
                        )
                        if not ledger:
                            raise ValueError(f"{unit_number}/{year}: touched by this repair but no "
                                              f"unit_levy_ledger doc exists to verify against")
                        total_paid_cents = round(ledger.get("total_paid", 0.0) * 100)
                        total_levied_cents = round(ledger.get("total_levied", 0.0) * 100)

                        # ASSERT-TARGET FIX (2026-08-05, GAP-FIN-046 follow-up): Mongo
                        # unit_levy_ledger.total_paid is CONFIRMED STALE for 2026 -- it is
                        # missing real 2026 payments (see
                        # docs/finances/GAP-FIN-046-reconciliation-analysis.md's correction:
                        # "at the receipts layer Mongo is STALE... PG's own active
                        # finance.receipts" is the authoritative 2026 figure, validated
                        # against the portal snapshot, NOT Mongo). This was the actual
                        # reason all 14 units held back by the first --apply run failed
                        # their hard-assert here -- not necessarily a receipt-authenticity
                        # problem for every one of them (confirmed via
                        # gap_fin_046_allocation_state_diagnostic_20260805.py, 2026-08-05).
                        # For 2026 only, use PG's own active (non-reversed) receipts total
                        # as the target instead of Mongo's total_paid. 2021-2025 are NOT
                        # touched by this fix -- those years are itemized/chained and not
                        # flagged stale anywhere in this investigation.
                        #
                        # IMPORTANT: this fixes the COMPARISON only. It does NOT establish
                        # that every "active" PG receipt is itself genuine -- TH074/TH077
                        # (suspected phantom generator-posted receipts) and UA050 (a
                        # suspected duplicate pair that does not cleanly reconcile even
                        # after removing the flagged duplicate -- see
                        # gap_fin_046_duplicate_receipt_finder_20260805.py) remain
                        # explicitly open, unresolved data-integrity questions, deferred
                        # pending real portal per-payment history (none exists in this
                        # system as of 2026-08-05 -- staging_strata_web_snapshots only
                        # stores balance snapshots, never itemized transactions). This
                        # script does not decide that question either way -- it only
                        # makes the comparison honest for whichever units a future,
                        # separately-authorised --apply run is scoped to.
                        if year == "2026":
                            active_2026 = await session.execute(
                                sa_text("""
                                    SELECT COALESCE(SUM(r.amount_cents), 0) total
                                    FROM finance.receipts r
                                    JOIN core.lots l ON l.lot_id = r.lot_id
                                    LEFT JOIN finance.journal_entries rev
                                        ON rev.reversal_of_id = r.journal_entry_id
                                    WHERE r.tenant_id = :tid AND l.unit_number = :u
                                      AND rev.journal_entry_id IS NULL
                                      AND EXTRACT(YEAR FROM r.received_on)::int = 2026
                                """),
                                {"tid": str(scheme["tenant_id"]), "u": unit_number},
                            )
                            pg_active_2026_cents = active_2026.scalar_one()
                            total_paid_cents = max(total_paid_cents, pg_active_2026_cents)

                        target_cents = min(total_paid_cents, total_levied_cents)

                        check = await session.execute(
                            sa_text("""
                                SELECT COALESCE(SUM(li.paid_cents), 0) total
                                FROM finance.levy_items li
                                JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                                JOIN core.lots l ON l.lot_id = li.lot_id
                                WHERE li.scheme_id = :sid AND l.unit_number = :u AND lr.financial_year = :fy
                            """),
                            {"sid": str(scheme["scheme_id"]), "u": unit_number, "fy": year},
                        )
                        actual_cents = check.scalar_one()
                        if abs(actual_cents - target_cents) >= 2:
                            raise ValueError(
                                f"{unit_number}/{year}: post-repair paid_cents=${actual_cents/100:,.2f} != "
                                f"target=${target_cents/100:,.2f}"
                            )
                kept.append(unit_number)
            except Exception as exc:
                rolled_back.append({"unit_number": unit_number, "error": str(exc)})

        if kept:
            await session.commit()
            print(f"Committed repairs for {len(kept)} unit(s): {kept}")
        else:
            print("No units passed their hard-assert — nothing committed.")

    if rolled_back:
        print(f"\nROLLED BACK (hard-assert failed, nothing written) for {len(rolled_back)} unit(s):")
        for r in rolled_back:
            print(f"  {r}")

    return 1 if rolled_back and not kept else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
