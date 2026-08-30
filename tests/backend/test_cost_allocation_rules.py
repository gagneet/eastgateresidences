"""Measured drivers, and the remainder that belongs to nobody.

# @featuretrace:levy-fairness — driver-based apportionment (building-scoped)
# Layer: test
# Data flow: core.cost_allocation_rules -> allocate_by_rule -> levy_fairness_service
# Related: backend/services/cost_allocation_rules.py,
#          backend/alembic/versions/0108_cost_alloc_rules.py
# Tests: this file

The failure this suite is built around: a wrong split still sums to 1.0. Folding the
unassigned remainder into a group, or dropping it, both produce an allocation that
reconciles perfectly and is wrong — so nothing downstream can detect it and the assertions
have to be here.
"""

import pytest

from services.cost_allocation_rules import (
    RuleNotApplicable,
    allocate_by_rule,
    rule_reasoning,
)

# Two groups, equal entitlement per lot, so a share maps directly onto a dollar figure.
LOT_GROUPS = {"A1": "Group A", "A2": "Group A", "B1": "Group B", "B2": "Group B"}
UE = {"A1": 1.0, "A2": 1.0, "B1": 1.0, "B2": 1.0}


def _rule(**kw):
    base = {
        "cost_line": "fac-garage", "basis": "shared_measured", "driver": "bays_held",
        "driver_values": {"Group A": 30.0, "Group B": 70.0},
        "unassigned_units": None, "unassigned_treatment": "entitlement",
    }
    base.update(kw)
    return base


def _by_group(alloc):
    out = {}
    for un, v in alloc.items():
        out[LOT_GROUPS[un]] = out.get(LOT_GROUPS[un], 0.0) + v
    return out


class TestMeasuredSplit:
    def test_cost_splits_on_the_driver_not_on_headcount(self):
        # Each group holds two lots. An equal-per-lot or entitlement split would give
        # 50/50; the driver says 30/70, and the driver is what the rule exists to apply.
        got = _by_group(allocate_by_rule(_rule(), 1000.0, LOT_GROUPS, UE))
        assert got["Group A"] == pytest.approx(300.0)
        assert got["Group B"] == pytest.approx(700.0)

    def test_the_whole_cost_is_allocated(self):
        alloc = allocate_by_rule(_rule(), 1000.0, LOT_GROUPS, UE)
        assert sum(alloc.values()) == pytest.approx(1000.0)

    def test_within_a_group_the_split_follows_entitlement(self):
        ue = {**UE, "A1": 3.0, "A2": 1.0}
        alloc = allocate_by_rule(_rule(), 1000.0, LOT_GROUPS, ue)
        assert alloc["A1"] == pytest.approx(225.0)   # 300 x 3/4
        assert alloc["A2"] == pytest.approx(75.0)    # 300 x 1/4

    def test_a_group_with_no_entitlement_data_splits_equally_rather_than_vanishing(self):
        ue = {**UE, "A1": 0.0, "A2": 0.0}
        alloc = allocate_by_rule(_rule(), 1000.0, LOT_GROUPS, ue)
        # Losing the amount would make the allocation fail to sum to the cost, which is
        # worse than a defensible equal split.
        assert alloc["A1"] == pytest.approx(150.0)
        assert alloc["A2"] == pytest.approx(150.0)
        assert sum(alloc.values()) == pytest.approx(1000.0)


class TestUnassignedRemainder:
    def test_entitlement_treatment_spreads_the_remainder_over_every_lot(self):
        # 30 / 70 / 20 unassigned. The remainder is 20/120 of the cost and goes to all
        # four lots equally (equal entitlement), NOT to either group's driver share.
        got = _by_group(allocate_by_rule(
            _rule(unassigned_units=20.0, unassigned_treatment="entitlement"),
            1200.0, LOT_GROUPS, UE,
        ))
        assert got["Group A"] == pytest.approx(300.0 + 100.0)   # 1200x30/120 + half of 200
        assert got["Group B"] == pytest.approx(700.0 + 100.0)

    def test_pro_rata_treatment_follows_the_measured_shares_instead(self):
        got = _by_group(allocate_by_rule(
            _rule(unassigned_units=20.0, unassigned_treatment="pro_rata"),
            1200.0, LOT_GROUPS, UE,
        ))
        assert got["Group A"] == pytest.approx(360.0)   # 1200 x 30/100
        assert got["Group B"] == pytest.approx(840.0)

    def test_excluded_treatment_removes_the_remainder_from_the_base(self):
        got = _by_group(allocate_by_rule(
            _rule(unassigned_units=20.0, unassigned_treatment="excluded"),
            1200.0, LOT_GROUPS, UE,
        ))
        # Same arithmetic as pro_rata by construction; asserted separately because they
        # are different DECISIONS and a future change must not silently merge them.
        assert got["Group A"] == pytest.approx(360.0)
        assert got["Group B"] == pytest.approx(840.0)

    def test_the_remainder_is_never_folded_into_a_group(self):
        with_remainder = _by_group(allocate_by_rule(
            _rule(unassigned_units=20.0), 1200.0, LOT_GROUPS, UE))
        folded = _by_group(allocate_by_rule(
            _rule(driver_values={"Group A": 50.0, "Group B": 70.0}), 1200.0, LOT_GROUPS, UE))
        # Folding 20 unassigned bays into Group A would give it 500.0. The remainder is a
        # separate fact about capacity nobody holds, and both totals still reconcile --
        # which is exactly why this needs asserting rather than reconciling.
        assert folded["Group A"] == pytest.approx(500.0)
        assert with_remainder["Group A"] == pytest.approx(400.0)

    def test_a_zero_remainder_behaves_as_no_remainder(self):
        assert _by_group(allocate_by_rule(_rule(unassigned_units=0.0), 1000.0, LOT_GROUPS, UE)) \
            == pytest.approx(_by_group(allocate_by_rule(_rule(), 1000.0, LOT_GROUPS, UE)))


class TestRefusals:
    def test_undetermined_does_not_allocate(self):
        # The water line ships in exactly this state: structure known, numbers not.
        with pytest.raises(RuleNotApplicable):
            allocate_by_rule(_rule(basis="undetermined", driver_values={}), 100.0, LOT_GROUPS, UE)

    def test_a_non_driver_basis_falls_back(self):
        for basis in ("entitlement", "equal_per_lot", "group_exclusive"):
            with pytest.raises(RuleNotApplicable):
                allocate_by_rule(_rule(basis=basis), 100.0, LOT_GROUPS, UE)

    def test_an_unknown_basis_is_refused_not_guessed(self):
        with pytest.raises(RuleNotApplicable):
            allocate_by_rule(_rule(basis="by_vibes"), 100.0, LOT_GROUPS, UE)

    def test_no_driver_values_is_refused(self):
        with pytest.raises(RuleNotApplicable):
            allocate_by_rule(_rule(driver_values={}), 100.0, LOT_GROUPS, UE)

    def test_a_non_numeric_driver_value_is_refused_not_zeroed(self):
        # Coercing to 0.0 would drop that group's entire share and read as the group
        # benefiting from nothing.
        with pytest.raises(RuleNotApplicable):
            allocate_by_rule(_rule(driver_values={"Group A": "lots", "Group B": 70}),
                             100.0, LOT_GROUPS, UE)

    def test_a_null_driver_value_is_refused(self):
        with pytest.raises(RuleNotApplicable):
            allocate_by_rule(_rule(driver_values={"Group A": None, "Group B": 70}),
                             100.0, LOT_GROUPS, UE)

    def test_a_negative_driver_value_is_refused(self):
        with pytest.raises(RuleNotApplicable):
            allocate_by_rule(_rule(driver_values={"Group A": -5, "Group B": 70}),
                             100.0, LOT_GROUPS, UE)

    def test_a_negative_remainder_is_refused(self):
        with pytest.raises(RuleNotApplicable):
            allocate_by_rule(_rule(unassigned_units=-1), 100.0, LOT_GROUPS, UE)

    def test_an_all_zero_driver_is_refused_rather_than_dividing_by_zero(self):
        with pytest.raises(RuleNotApplicable):
            allocate_by_rule(_rule(driver_values={"Group A": 0, "Group B": 0}),
                             100.0, LOT_GROUPS, UE)

    def test_an_unknown_treatment_is_refused(self):
        with pytest.raises(RuleNotApplicable):
            allocate_by_rule(_rule(unassigned_units=5, unassigned_treatment="somehow"),
                             100.0, LOT_GROUPS, UE)

    def test_excluded_basis_allocates_zero_to_everyone_without_refusing(self):
        # A deliberate zero is not a failure to compute, and the caller must not fall back
        # to the tag-based split -- which would re-introduce the cost the rule excluded.
        alloc = allocate_by_rule(_rule(basis="excluded"), 1000.0, LOT_GROUPS, UE)
        assert alloc == {un: 0.0 for un in LOT_GROUPS}


class TestStaleGroupReferences:
    def test_a_driver_value_for_a_group_with_no_lots_is_dropped(self):
        # Its share would otherwise be handed to nobody, shrinking every real group's
        # cost with nothing to show where it went.
        got = _by_group(allocate_by_rule(
            _rule(driver_values={"Group A": 30.0, "Group B": 70.0, "Group Z": 400.0}),
            1000.0, LOT_GROUPS, UE,
        ))
        assert got["Group A"] == pytest.approx(300.0)
        assert got["Group B"] == pytest.approx(700.0)

    def test_a_rule_naming_only_absent_groups_is_refused(self):
        with pytest.raises(RuleNotApplicable):
            allocate_by_rule(_rule(driver_values={"Group Z": 10.0}), 100.0, LOT_GROUPS, UE)


class TestReasoning:
    def test_reasoning_names_the_evidence_and_whether_it_repeats(self):
        r = rule_reasoning(
            _rule(driver_period="monthly", evidence_source="Access-system report"), 1000.0)
        assert r["evidence_recorded"] is True
        assert r["repeatable_measurement"] is True

    def test_a_one_off_measurement_is_flagged_as_not_repeatable(self):
        # UTMA s.78(3): a single observation cannot support a standing contribution,
        # however precise it is.
        assert rule_reasoning(_rule(driver_period="one_off"), 1.0)["repeatable_measurement"] is False
        assert rule_reasoning(_rule(driver_period=None), 1.0)["repeatable_measurement"] is False

    def test_a_rule_with_no_evidence_is_flagged_rather_than_looking_complete(self):
        assert rule_reasoning(_rule(), 1.0)["evidence_recorded"] is False

    def test_arithmetic_mentions_the_remainder_when_there_is_one(self):
        assert "unassigned" in rule_reasoning(_rule(unassigned_units=11), 100.0)["arithmetic"]
        assert "unassigned" not in rule_reasoning(_rule(), 100.0)["arithmetic"]
