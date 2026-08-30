# @featuretrace:finance-postgres-read-cutover — rebuilding the receipt->levy_item trail.
# Layer: test
# Data flow: reconstruct_receipt_allocations.plan_allocations over one lot-year (building-scoped).
# Related: backend/scripts/data_repair/reconstruct_receipt_allocations.py
#          docs/architecture/unit_levy_ledger_derivation_design_2026-08-30.md
"""The matching rule, tested without a database.

$224,733.13 of levy_items.paid_cents has no receipt_allocations row, which blocks the
unit_levy_ledger derivation and any defensible per-lot position. This is the rule that
rebuilds the link — so it has to be provably incapable of inventing money, moving it
between owners, or double-counting a trail that already exists.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_reconstruct_receipt_allocations.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend" / "scripts" / "data_repair" / "reconstruct_receipt_allocations.py"

spec = importlib.util.spec_from_file_location("_reconstruct_allocs", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules["_reconstruct_allocs"] = mod
spec.loader.exec_module(mod)
plan = mod.plan_allocations


def _item(item_id, paid, already=0):
    return {"levy_item_id": item_id, "paid_cents": paid, "already_allocated": already}


def _receipt(receipt_id, cents):
    return {"receipt_id": receipt_id, "amount_cents": cents}


class TestItNeverInventsMoney:
    def test_allocations_never_exceed_the_receipts_available(self):
        allocs, unalloc, unexplained = plan([_item("i1", 10_000)], [_receipt("r1", 4_000)])
        assert sum(a["allocated_cents"] for a in allocs) == 4_000
        assert unexplained == 6_000, "the shortfall must be reported, not conjured"
        assert unalloc == 0

    def test_a_shortfall_is_reported_rather_than_filled(self):
        """FY2021 lot-years genuinely have fewer receipts than paid_cents claims. The
        right answer may be that paid_cents is wrong — not that a receipt is missing."""
        _, _, unexplained = plan([_item("i1", 2_215_36)], [_receipt("r1", 1_661_52)])
        assert unexplained == 2_215_36 - 1_661_52

    def test_allocations_never_exceed_an_items_paid_cents(self):
        allocs, unalloc, _ = plan([_item("i1", 1_000)], [_receipt("r1", 9_999)])
        assert sum(a["allocated_cents"] for a in allocs) == 1_000
        assert unalloc == 8_999, "the surplus is unapplied CREDIT, not an over-payment of this item"


class TestItNeverDoubleCountsAnExistingTrail:
    def test_a_partially_allocated_item_is_only_topped_up(self):
        """11 items are already partly allocated. Re-allocating the full paid_cents
        would double-count the trail that exists."""
        allocs, _, unexplained = plan(
            [_item("i1", 1_000, already=400)], [_receipt("r1", 5_000)],
        )
        assert sum(a["allocated_cents"] for a in allocs) == 600
        assert unexplained == 0

    def test_a_fully_allocated_item_gets_nothing(self):
        allocs, unalloc, unexplained = plan(
            [_item("i1", 1_000, already=1_000)], [_receipt("r1", 5_000)],
        )
        assert allocs == []
        assert unalloc == 5_000
        assert unexplained == 0

    def test_an_over_allocated_item_is_left_alone(self):
        """Never negative: an item allocated beyond its paid_cents is a pre-existing
        defect and this rule must not try to correct it by allocating less than zero."""
        allocs, _, unexplained = plan(
            [_item("i1", 1_000, already=1_500)], [_receipt("r1", 5_000)],
        )
        assert allocs == []
        assert unexplained == 0


class TestTheWaterfallOrder:
    def test_the_oldest_item_is_paid_first(self):
        """A levy waterfall applies money to the earliest outstanding charge. Any other
        order produces a trail that disagrees with how the balance was actually reduced.
        Callers pass items ordered by grace deadline."""
        allocs, _, _ = plan(
            [_item("oldest", 500), _item("newest", 500)], [_receipt("r1", 500)],
        )
        assert [a["levy_item_id"] for a in allocs] == ["oldest"]

    def test_a_receipt_is_drained_before_the_next_is_touched(self):
        allocs, _, _ = plan(
            [_item("i1", 300), _item("i2", 300)],
            [_receipt("r1", 400), _receipt("r2", 400)],
        )
        by_receipt = {}
        for a in allocs:
            by_receipt.setdefault(a["receipt_id"], 0)
            by_receipt[a["receipt_id"]] += a["allocated_cents"]
        assert by_receipt["r1"] == 400
        assert by_receipt["r2"] == 200

    def test_one_receipt_can_span_two_items(self):
        allocs, _, _ = plan(
            [_item("i1", 300), _item("i2", 300)], [_receipt("r1", 600)],
        )
        assert len(allocs) == 2
        assert all(a["receipt_id"] == "r1" for a in allocs)


class TestTheRealShape:
    def test_a_matching_lot_year_reconciles_to_zero(self):
        """The FY2022-2025 shape: 8 levy items (admin+sinking per quarter) against 4
        quarterly receipts, totals equal. 52 lot-years look exactly like this."""
        items = [_item(f"i{n}", 76_318) for n in range(8)]
        receipts = [_receipt(f"r{n}", 152_636) for n in range(4)]
        allocs, unalloc, unexplained = plan(items, receipts)
        assert unalloc == 0 and unexplained == 0
        assert sum(a["allocated_cents"] for a in allocs) == 8 * 76_318

    def test_an_advance_paying_lot_year_leaves_credit(self):
        """The FY2026 shape: receipts far exceed what the year levied."""
        allocs, unalloc, unexplained = plan(
            [_item("i1", 176_150)], [_receipt("r1", 3_102_247)],
        )
        assert unexplained == 0
        assert unalloc == 3_102_247 - 176_150
        assert sum(a["allocated_cents"] for a in allocs) == 176_150

    def test_no_allocation_is_ever_zero_or_negative(self):
        """receipt_allocations has CHECK (allocated_cents > 0) — a zero row would be
        rejected by the database mid-transaction."""
        allocs, _, _ = plan(
            [_item("i1", 0), _item("i2", 500), _item("i3", 0)], [_receipt("r1", 500)],
        )
        assert all(a["allocated_cents"] > 0 for a in allocs)


class TestTheScriptRefusesUnsafeApplies:
    def test_it_is_dry_run_by_default(self):
        source = SCRIPT.read_text()
        assert 'ap.add_argument("--apply", action="store_true"' in source

    def test_over_allocation_blocks_apply(self):
        """A reconciliation proof that cannot balance is not a proof."""
        source = SCRIPT.read_text()
        assert "REFUSING TO APPLY" in source
        assert "return 1" in source

    def test_it_never_writes_paid_cents(self):
        """The allocation explains the ledger; it must not restate it."""
        source = SCRIPT.read_text()
        assert "UPDATE finance.levy_items" not in source
        assert "SET paid_cents" not in source

    def test_it_never_retires_a_receipt(self):
        """An unallocated receipt IS unapplied credit — acting otherwise retired 14 real
        credit receipts on 2026-08-28 and had to be rolled back the same day."""
        source = SCRIPT.read_text()
        assert "retired_at =" not in source
        assert "UPDATE finance.receipts" not in source
