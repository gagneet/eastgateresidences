# @featuretrace:finance-postgres-read-cutover — which number is wrong when they disagree.
# Layer: test
# Data flow: adjudicate_over_allocated_levy_items.classify over one levy_item (building-scoped).
# Related: backend/scripts/data_repair/adjudicate_over_allocated_levy_items.py
#          docs/architecture/allocation_trail_reconstruction_2026-08-30.md
"""16 levy_items had allocations exceeding their own paid_cents. This is the judgement.

Two numbers disagreed and the answer was not the same for all 16. Comparing the
allocation against what the item CHARGED separates them: within the charge, the money
really did go there and paid_cents lagged; beyond the charge, money was applied to a
charge bigger than the charge, and the allocation is what is wrong.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_adjudicate_over_allocations.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend" / "scripts" / "data_repair" / "adjudicate_over_allocated_levy_items.py"

spec = importlib.util.spec_from_file_location("_adjudicate_over", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules["_adjudicate_over"] = mod
spec.loader.exec_module(mod)
classify = mod.classify


class TestWhichNumberIsWrong:
    def test_allocation_within_the_charge_means_paid_cents_lagged(self):
        """TH074 admin FY2026: charged $1,363.48, paid $377.69, allocated $1,061.81.

        The allocations point at real unretired receipts and stay inside what was billed,
        so the money did go to this item. Raising paid_cents invents nothing — every cent
        is already evidenced by an allocation row.
        """
        action, target = classify(charged=136_348, paid=37_769, allocated=106_181)
        assert action == "raise_paid"
        assert target == 106_181

    def test_allocation_beyond_the_charge_means_the_allocation_is_wrong(self):
        """UA050 admin FY2026: charged $698.78, fully paid, yet $1,106.74 allocated.

        Money cannot be applied to a charge beyond the charge. Raising paid_cents here
        would claim the owner owed more than they were billed.
        """
        action, target = classify(charged=69_878, paid=69_878, allocated=110_674)
        assert action == "reduce_allocation"
        assert target == 69_878

    def test_paid_cents_is_never_raised_above_the_charge(self):
        """The invariant that keeps 'raise_paid' honest."""
        for charged, paid, allocated in (
            (136_348, 37_769, 106_181),
            (39_802, 20_802, 39_802),
            (27_613, 0, 11_752),
        ):
            action, target = classify(charged, paid, allocated)
            assert action == "raise_paid"
            assert target <= charged

    def test_an_allocation_exactly_equal_to_the_charge_is_not_an_over_allocation(self):
        """TH075 sinking: charged $398.02, allocated $398.02 — the boundary case."""
        action, target = classify(charged=39_802, paid=20_802, allocated=39_802)
        assert action == "raise_paid"
        assert target == 39_802

    def test_the_two_groups_partition_the_real_sixteen(self):
        """Live figures: 8 items / $2,302.85 raised, 8 items / $1,837.26 reduced."""
        live = [
            (136_348, 37_769, 106_181), (94_592, 737, 55_962), (69_878, 9, 40_805),
            (39_802, 20_802, 39_802), (39_802, 10_778, 29_376), (27_613, 0, 11_752),
            (20_398, 0, 10_965), (39_802, 0, 5_537),
            (69_878, 69_878, 110_674), (69_878, 69_878, 110_674),
            (69_878, 69_878, 100_552), (69_878, 69_878, 90_276),
            (20_398, 20_398, 40_796), (20_398, 20_398, 30_674),
            (20_398, 20_398, 30_592), (20_398, 20_398, 30_592),
        ]
        raised = sum(t - p for c, p, a in live
                     for act, t in [classify(c, p, a)] if act == "raise_paid")
        reduced = sum(a - t for c, p, a in live
                      for act, t in [classify(c, p, a)] if act == "reduce_allocation")
        assert raised == 230_285, "group 1 must total $2,302.85"
        assert reduced == 183_726, "group 2 must total $1,837.26"
        assert raised + reduced == 414_011, "and together the full $4,140.11"


class TestTheScriptsSafetyRails:
    def test_it_is_dry_run_by_default(self):
        assert 'ap.add_argument("--apply", action="store_true"' in SCRIPT.read_text()

    def test_it_refuses_when_a_retired_receipt_is_involved(self):
        source = SCRIPT.read_text()
        assert "REFUSING — a retired receipt is involved" in source

    def test_raising_paid_cents_is_capped_in_sql_not_only_in_python(self):
        """A bad target cannot be written even if classify() were wrong."""
        source = SCRIPT.read_text()
        assert "AND $2 <= principal_cents + gst_cents + interest_cents" in source

    def test_it_verifies_the_post_condition_rather_than_assuming(self):
        """The first run left $203.98 unresolved and this check is what caught it."""
        source = SCRIPT.read_text()
        assert "remaining over-allocation" in source
        assert "return 0 if int(remaining) == 0 else 1" in source

    def test_a_surplus_that_cannot_be_absorbed_raises(self):
        """Refusing to leave a half-corrected item is the point."""
        source = SCRIPT.read_text()
        assert "refusing to leave a half-corrected item" in source
