# @featuretrace:financial_core — GAP-FIN-057 orphaned-allocation reversal driver (dry-run first).
# Layer: script (data-repair)
# Data flow: reads finance.receipt_allocations / receipts / journal_entries (reversal join)
#            + GL journal_lines (assert target); on --apply drives
#            FinancialCoreService.reverse_allocations per unit inside a SAVEPOINT.
# Related: backend/services/financial_core/service.py (reverse_allocations)
#          backend/scripts/audits/reconcile_receipt_reversal_netting.py (GL-net evidence gate)
#          tasks/GAP-FIN-057-reversal-command-spec.md
"""
GAP-FIN-057 — Reverse orphaned receipt-allocations left by journal-only reversals
==================================================================================
Dry-run-first, idempotent one-time repair for the allocations that
``FinancialCoreService.reverse_entry()`` left behind when it reversed ONLY the GL
journal (see tasks/GAP-FIN-057-reversal-command-spec.md and
tasks/GAP-FIN-057-reversal-does-not-unwind-receipt-allocations.md).

A ``receipt_allocation`` is STALE when its parent receipt's journal entry has a POSTED
reversal — the GL is already net-correct, but ``finance.levy_items.paid_cents`` and
``finance.receipt_allocations`` remain gross, over-reporting "paid". This driver removes
exactly those allocations and recomputes ``paid_cents`` from the SURVIVING allocations,
through the canonical ``FinancialCoreService.reverse_allocations`` command (never bespoke
raw SQL — the GAP-FIN-035 / category-expenses rule).

WHAT MAKES THIS DIFFERENT FROM THE GAP-FIN-046 REPAIR (and why 046 didn't stick)
--------------------------------------------------------------------------------
046's per-unit hard-assert target was Mongo ``unit_levy_ledger.total_paid``, which is
STALE for 2026 — so the assert failed and each unit was rolled back, leaving the phantom
allocations in place (a silent no-op). Per spec §4.3 / §5, THIS script's hard-assert
target is the reversal-aware **GL-net** collected on Accounts Receivable (the same figure
``scripts/audits/reconcile_receipt_reversal_netting.py`` reports) — NOT Mongo, NOT a
re-derived obligation. After the unwind, per lot:

    Σ surviving receipt_allocations.allocated_cents  ==  GL-net AR collected (reversal-aware)

within a small tolerance. A unit that does not reconcile to its own GL-net is rolled back
and reported for manual review (e.g. it also has GAP-FIN-046 Bug-B unallocated receipts,
a different repair) — never partially written.

Invariants checked (spec §4, all integer cents):
  1. Per-fund   — admin & sinking removed/paid split reported independently (levy_items.fund_id).
  2. Per-unit   — each unit's paid_cents recomputed only from ITS OWN surviving allocations.
  3. GL-net     — the hard-assert above (the gate). Uses GL journal_lines, not Mongo.
  4. Directional— paid_cents may only go DOWN or stay equal (arrears up only); never up.
  5. Replay     — recompute-from-survivors is idempotent by construction; a second --apply
                  deletes 0 rows / changes 0 paid_cents.
  6. Missing≠0  — the affected set is exactly the units carrying a reversed receipt; a unit
                  with none is untouched, not zeroed. Receipts with a NULL journal_entry_id
                  (unmatchable by the reversal join — spec §2 caveat) are counted + surfaced.
  7. Ratio      — lifetime Σreceipts / Σlevied per unit, before vs projected-after
                  (~2.0× → ~1.0×) — the headline evidence.

Usage (from backend/):
    venv/bin/python3 scripts/data_repair/gap_fin_057_reverse_orphaned_allocations_20260807.py --building-id 13195
    venv/bin/python3 scripts/data_repair/gap_fin_057_reverse_orphaned_allocations_20260807.py --building-id 13195 --unit UA030 --apply
    venv/bin/python3 scripts/data_repair/gap_fin_057_reverse_orphaned_allocations_20260807.py --building-id 13195 --apply

Safety guarantees:
  - Defaults to DRY-RUN; --apply is required for any DB write.
  - Building-agnostic: --building-id resolves the scheme/tenant; no hardcoded 13195 logic.
  - Per-unit SAVEPOINT + GL-net hard-assert before keeping any unit's change; a unit that
    fails is rolled back and reported, never partially written.
  - --unit <UNIT> runs a single-unit canary first (do UA030 first — the line-traced one).
  - Idempotent: re-running after a clean apply finds nothing and no-ops.

Production mutation authorised: NO by default. --apply requires the dry-run evidence to
have been reviewed and sign-off given (spec §6.3).
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

import os

import asyncpg

# GL account codes — match reconcile_receipt_reversal_netting.py / genesis.py.
_AR_ACCOUNT_CODE = "1100"          # Levy Receivable / Accounts Receivable
# Divergence under this many cents is treated as reconciled (rounding / 4c allocation noise).
TOLERANCE_CENTS = 100


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--building-id", required=True, help="building_id / scheme number, e.g. 13195")
    p.add_argument("--apply", action="store_true", default=False,
                   help="Write. Without this flag, dry-run only (no DB changes).")
    p.add_argument("--unit", default="", help="Single-unit canary: repair ONLY this unit number (e.g. UA030).")
    return p.parse_args()


def _pg_dsn() -> str:
    """asyncpg DSN from DATABASE_URL (strip the SQLAlchemy +asyncpg driver marker)."""
    return (
        os.environ["DATABASE_URL"]
        .replace("postgresql+asyncpg://", "postgresql://")
        .replace("+asyncpg", "")
    )


async def _resolve_scheme(building_id: str) -> dict:
    from db_postgres.repos.config_repo import resolve_scheme_context
    scheme = await resolve_scheme_context(building_id)
    if not scheme:
        raise SystemExit(f"ERROR: no scheme row for building {building_id}")
    return scheme


async def _fetch_scope(scheme: dict, unit: str) -> dict:
    """Read-only pass (plain asyncpg with the real tenant_id — finance.* has no RLS
    bypass clause). Collects, per unit: the stale allocations (with fund split), the
    projected paid_cents per item, the per-lot GL-net AR collected (the assert target),
    and the lifetime receipts/levied ratio inputs."""
    tenant_id = str(scheme["tenant_id"])
    scheme_id = str(scheme["scheme_id"])
    conn = await asyncpg.connect(_pg_dsn())
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant_id)
    try:
        unit_filter = "AND l.unit_number = $2" if unit else ""
        args = [scheme_id] + ([unit] if unit else [])

        # Stale allocations: parent receipt's journal has a POSTED reversal.
        stale = await conn.fetch(
            f"""
            SELECT l.unit_number, li.lot_id, li.fund_id, f.fund_type,
                   ra.allocation_id, ra.receipt_id, ra.levy_item_id, ra.allocated_cents
            FROM finance.receipt_allocations ra
            JOIN finance.receipts        r    ON r.receipt_id       = ra.receipt_id
            JOIN finance.levy_items      li   ON li.levy_item_id    = ra.levy_item_id
            JOIN finance.funds           f    ON f.fund_id          = li.fund_id
            JOIN finance.journal_entries orig ON orig.journal_entry_id = r.journal_entry_id
            JOIN core.lots               l    ON l.lot_id           = li.lot_id
            WHERE r.scheme_id = $1
              {unit_filter}
              AND ra.levy_item_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM finance.journal_entries rev
                          WHERE rev.reversal_of_id = orig.journal_entry_id
                            AND rev.status = 'posted')
            ORDER BY l.unit_number, ra.receipt_id, ra.allocation_id
            """,
            *args,
        )

        # §2 caveat: reversed receipts with a NULL journal_entry_id are NOT caught by the
        # join above — count them so "missing" is never silently read as "zero".
        null_je = await conn.fetch(
            f"""
            SELECT l.unit_number, count(*) AS n, COALESCE(SUM(r.amount_cents), 0) AS cents
            FROM finance.receipts r
            JOIN core.lots l ON l.lot_id = r.lot_id
            WHERE r.scheme_id = $1 {unit_filter}
              AND r.journal_entry_id IS NULL
            GROUP BY l.unit_number
            """,
            *args,
        )

        # Per-lot reversal-aware GL-net AR collected (the hard-assert target, spec §4.3):
        # credit reduces AR (payment), a reversal debits AR back and nets out. Isolate to
        # receipt/payment/reversal source entries so the original levy CHARGE (which also
        # debits AR) is not netted in.
        gl_net = await conn.fetch(
            f"""
            SELECT l.unit_number, COALESCE(SUM(
                CASE WHEN jl.direction = 'credit' THEN jl.amount_cents ELSE -jl.amount_cents END
            ), 0) AS gl_net_cents
            FROM finance.journal_lines jl
            JOIN finance.journal_entries je ON je.journal_entry_id = jl.journal_entry_id
            JOIN finance.gl_accounts ga ON ga.gl_account_id = jl.gl_account_id
            JOIN core.lots l ON l.lot_id = jl.lot_id
            WHERE je.scheme_id = $1 {unit_filter}
              AND ga.account_code = '{_AR_ACCOUNT_CODE}'
              AND je.status = 'posted'
              AND je.source_type IN ('payment_receipt', 'reversal')
            GROUP BY l.unit_number
            """,
            *args,
        )

        # Lifetime ratio inputs: gross receipts and gross levied per unit (before-state).
        ratios = await conn.fetch(
            f"""
            SELECT l.unit_number,
                   COALESCE((SELECT SUM(r.amount_cents) FROM finance.receipts r
                             WHERE r.lot_id = l.lot_id AND r.scheme_id = $1), 0) AS receipts_cents,
                   COALESCE((SELECT SUM(li.principal_cents + li.gst_cents + li.interest_cents
                                        + li.recovery_costs_cents)
                             FROM finance.levy_items li WHERE li.lot_id = l.lot_id
                               AND li.scheme_id = $1), 0) AS levied_cents,
                   COALESCE((SELECT SUM(li.paid_cents) FROM finance.levy_items li
                             WHERE li.lot_id = l.lot_id AND li.scheme_id = $1), 0) AS paid_cents
            FROM core.lots l
            WHERE l.scheme_id = $1 {unit_filter}
            """,
            *args,
        )
    finally:
        await conn.close()

    gl_net_by_unit = {r["unit_number"]: int(r["gl_net_cents"]) for r in gl_net}
    ratio_by_unit = {r["unit_number"]: dict(r) for r in ratios}
    null_je_by_unit = {r["unit_number"]: dict(r) for r in null_je}

    by_unit: dict[str, dict] = {}
    for r in stale:
        u = by_unit.setdefault(r["unit_number"], {"stale": [], "gl_net_cents": 0})
        u["stale"].append(dict(r))
    for unit_number, u in by_unit.items():
        u["gl_net_cents"] = gl_net_by_unit.get(unit_number, 0)
        u["ratio"] = ratio_by_unit.get(unit_number, {})
        u["null_je"] = null_je_by_unit.get(unit_number, {})
    return by_unit


def _fund_split(rows: list[dict]) -> dict[str, int]:
    """Sum allocated_cents by fund_type (admin/sinking/...) — spec §4.1 per-fund view."""
    split: dict[str, int] = {}
    for r in rows:
        split[r["fund_type"]] = split.get(r["fund_type"], 0) + r["allocated_cents"]
    return split


def _aud(c: int) -> str:
    return f"${c / 100:,.2f}"


def _print_dry_run(by_unit: dict) -> None:
    total_stale = sum(s["allocated_cents"] for u in by_unit.values() for s in u["stale"])
    print(f"GAP-FIN-057 dry-run — {len(by_unit)} unit(s) carry stale (reversed-receipt) "
          f"allocations totalling {_aud(total_stale)}.\n")
    print(f"{'unit':8} {'stale#':>6} {'remove':>13} {'paid→':>13} {'GL-net':>13} "
          f"{'ratio b→a':>14}  fund split / status")
    print("-" * 100)
    for unit_number in sorted(by_unit):
        u = by_unit[unit_number]
        stale = u["stale"]
        remove = sum(s["allocated_cents"] for s in stale)
        ratio = u.get("ratio", {})
        paid_before = int(ratio.get("paid_cents", 0) or 0)
        # Projected paid_cents after removal: the surviving allocations only. Since every
        # stale row here is being removed, the projected drop is exactly `remove` for the
        # affected items (recompute-from-survivors); paid_after = paid_before - remove.
        paid_after = paid_before - remove
        gl_net = u["gl_net_cents"]
        receipts = int(ratio.get("receipts_cents", 0) or 0)
        levied = int(ratio.get("levied_cents", 0) or 0)
        ratio_b = (receipts / levied) if levied else 0.0
        # After the unwind, the lot's collected should track GL-net; ratio uses paid_after.
        ratio_a = (paid_after / levied) if levied else 0.0
        split = _fund_split(stale)
        split_str = ", ".join(f"{k}:{_aud(v)}" for k, v in sorted(split.items()))

        # GL-net hard-assert projection (spec §4.3): does paid_after reconcile to GL-net?
        ok = abs(paid_after - gl_net) <= TOLERANCE_CENTS
        directional = paid_after <= paid_before  # §4.4 arrears up only
        status = "OK" if (ok and directional) else "REVIEW"
        null_je = u.get("null_je", {})
        if null_je.get("n"):
            status += f" (+{null_je['n']} NULL-je receipts {_aud(int(null_je.get('cents', 0)))})"

        print(f"{unit_number:8} {len(stale):>6} {_aud(remove):>13} "
              f"{_aud(paid_after):>13} {_aud(gl_net):>13} "
              f"{ratio_b:>6.2f}→{ratio_a:<6.2f}  {split_str}  [{status}]")

    print("\nInvariant legend (spec §4): paid→ = projected paid_cents after unwind; "
          "GL-net = reversal-aware AR collected (the assert target, NOT Mongo); "
          "ratio b→a = lifetime receipts/levied before → paid/levied after (expect ~2.0→~1.0).")
    print("A unit marked [REVIEW] does not reconcile to its own GL-net within "
          f"{_aud(TOLERANCE_CENTS)} (likely also carries GAP-FIN-046 Bug-B unallocated "
          "receipts — a different repair) and would be ROLLED BACK under --apply, not written.")
    print("\nDry-run complete. Re-run with --apply (after sign-off) to write; add "
          "--unit UA030 to canary a single unit first.")


async def _apply(scheme: dict, by_unit: dict) -> int:
    from db_postgres.session import async_session_context, set_tenant
    from services.financial_core import get_financial_core_service
    from services.financial_core.domain.entities import ReverseAllocationsCommand, SchemeRef
    from sqlalchemy import text as sa_text

    scheme_ref = SchemeRef(
        tenant_id=UUID(str(scheme["tenant_id"])), scheme_id=UUID(str(scheme["scheme_id"])),
    )
    kept: list[str] = []
    rolled_back: list[dict] = []

    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))
        svc = get_financial_core_service(session)

        for unit_number in sorted(by_unit):
            u = by_unit[unit_number]
            receipt_ids = sorted({UUID(str(s["receipt_id"])) for s in u["stale"]})
            lot_id = u["stale"][0]["lot_id"]
            try:
                async with session.begin_nested():
                    # Canonical unwind, scoped to THIS unit's reversed receipts only.
                    result = await svc.reverse_allocations(ReverseAllocationsCommand(
                        scheme_ref=scheme_ref,
                        reason=f"GAP-FIN-057 orphaned-allocation cleanup ({unit_number})",
                        receipt_ids=list(receipt_ids),
                        idempotency_key=f"gap-fin-057:{scheme['scheme_id']}:{unit_number}",
                    ))

                    # Hard-assert against GL-net (spec §4.3) — NOT Mongo. After the unwind,
                    # the lot's Σ paid_cents must reconcile to its reversal-aware GL-net AR
                    # collected. A shortfall means unallocated real receipts remain
                    # (GAP-FIN-046 Bug B) — roll this unit back for a separate repair.
                    paid_after = (await session.execute(
                        sa_text("""
                            SELECT COALESCE(SUM(li.paid_cents), 0)
                            FROM finance.levy_items li
                            WHERE li.scheme_id = :sid AND li.lot_id = :lot
                        """),
                        {"sid": str(scheme["scheme_id"]), "lot": str(lot_id)},
                    )).scalar_one()
                    gl_net = u["gl_net_cents"]
                    if abs(int(paid_after) - int(gl_net)) > TOLERANCE_CENTS:
                        raise ValueError(
                            f"{unit_number}: post-unwind paid_cents={_aud(int(paid_after))} "
                            f"!= GL-net AR collected {_aud(int(gl_net))} "
                            f"(Δ {_aud(int(paid_after) - int(gl_net))}) — not reconciled to the GL; "
                            "rolled back (likely GAP-FIN-046 Bug-B unallocated receipts)."
                        )
                kept.append(unit_number)
                print(f"  {unit_number}: unwound {len(result.deleted_allocation_ids)} allocation(s), "
                      f"{_aud(result.reversed_allocated_cents)} → paid now reconciles to GL-net "
                      f"{_aud(int(gl_net))}.")
            except Exception as exc:  # noqa: BLE001
                rolled_back.append({"unit_number": unit_number, "error": str(exc)})

        if kept:
            await session.commit()
            print(f"\nCommitted GAP-FIN-057 unwind for {len(kept)} unit(s): {kept}")
        else:
            print("\nNo units passed the GL-net hard-assert — nothing committed.")

    if rolled_back:
        print(f"\nROLLED BACK (GL-net assert failed, nothing written) for {len(rolled_back)} unit(s):")
        for r in rolled_back:
            print(f"  {r['unit_number']}: {r['error']}")
    return 1 if rolled_back and not kept else 0


async def main() -> int:
    args = parse_args()
    scheme = await _resolve_scheme(args.building_id)
    by_unit = await _fetch_scope(scheme, args.unit.strip())

    if not by_unit:
        print(f"No stale (reversed-receipt) allocations found for building {args.building_id}"
              + (f" unit {args.unit}" if args.unit else "") + " — nothing to do.")
        return 0

    _print_dry_run(by_unit)

    if not args.apply:
        return 0

    print("\n--apply requested — running the canonical reverse_allocations per unit "
          "inside a per-unit SAVEPOINT with a GL-net hard-assert:\n")
    return await _apply(scheme, by_unit)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
