"""The capital lens: a ten-year plan reconciled against ten years of levy, not one.

# @featuretrace:levy-fairness — capital-horizon lens and remedy catalogue (building-scoped)
# Layer: test
# Data flow: capital_replacement_schedule + building_assets -> levy_fairness_service
#            -> capital_outlook / remedies -> LevyFairnessPage
# Related: backend/services/levy_fairness_service.py,
#          backend/routers/benefit_groups.py, tests/backend/test_levy_fairness.py

These guard three things the annual lens structurally cannot:

  * a capital item is attributed to the lots it SERVES, resolved through the asset it
    names, rather than spread on entitlement across lots the work never touches;
  * a dangling asset reference is reported as a data fault and NOT collapsed into
    "nobody has decided yet", because only the second is a question for a committee;
  * the ten-year deltas are reconciled against ten years of sinking levy, and the
    residual is named `funding_gap` rather than read as a broken zero-sum.
"""

import pytest

from services.levy_fairness_service import (
    _REMEDY_CATALOGUE,
    _build_capital_outlook,
    _build_remedies,
)


def _item(name, year, cost, shares, attribution="attributed", asset_ref=None):
    return {
        "item_id": name, "name": name, "year": year, "cost": cost,
        "shares": shares, "attribution": attribution, "asset_ref": asset_ref,
    }


LOT_GROUPS = {"A1": "Group A", "A2": "Group A", "B1": "Group B"}
# Group A holds 2 of 3 lots and two-thirds of entitlement.
PAYMENT_SHARES = {"A1": 1 / 3, "A2": 1 / 3, "B1": 1 / 3}


class TestCapitalAttribution:
    def test_item_serving_one_group_is_not_spread_across_the_others(self):
        outlook = _build_capital_outlook(
            [_item("Lift", 2030, 300.0, {"A1": 0.5, "A2": 0.5})],
            LOT_GROUPS, PAYMENT_SHARES, sinking_annual=1.0, total_ue=30.0,
        )
        spend = {g["group"]: g["planned_spend"] for g in outlook["groups"]}
        assert spend["Group A"] == 300.0
        # The lot that cannot reach the lift carries none of replacing it.
        assert spend.get("Group B", 0.0) == 0.0

    def test_horizon_spans_first_to_last_planned_year_inclusive(self):
        outlook = _build_capital_outlook(
            [_item("Early", 2027, 10.0, {"A1": 1.0}),
             _item("Late", 2036, 10.0, {"A1": 1.0})],
            LOT_GROUPS, PAYMENT_SHARES, sinking_annual=1.0, total_ue=1.0,
        )
        assert (outlook["first_year"], outlook["last_year"]) == (2027, 2036)
        assert outlook["horizon_years"] == 10

    def test_a_single_year_plan_does_not_collapse_the_horizon_to_zero(self):
        outlook = _build_capital_outlook(
            [_item("One", 2030, 10.0, {"A1": 1.0})],
            LOT_GROUPS, PAYMENT_SHARES, sinking_annual=1.0, total_ue=1.0,
        )
        # A zero horizon would zero every contribution and report the whole plan as
        # unfunded, which is a division artefact rather than a finding.
        assert outlook["horizon_years"] == 1

    def test_contributions_are_scaled_by_the_horizon_not_by_one_year(self):
        outlook = _build_capital_outlook(
            [_item("X", 2027, 100.0, {"A1": 1.0}),
             _item("Y", 2036, 100.0, {"A1": 1.0})],
            LOT_GROUPS, PAYMENT_SHARES, sinking_annual=10.0, total_ue=3.0,
        )
        # 10 years x $10 x 3 UE = $300 raised over the plan.
        assert outlook["sinking_total"] == 300.0

    def test_no_capital_plan_returns_none_rather_than_an_empty_verdict(self):
        assert _build_capital_outlook([], LOT_GROUPS, PAYMENT_SHARES, 1.0, 1.0) is None

    def test_items_without_a_year_cannot_form_a_horizon(self):
        assert _build_capital_outlook(
            [_item("Undated", None, 10.0, {"A1": 1.0})],
            LOT_GROUPS, PAYMENT_SHARES, 1.0, 1.0,
        ) is None


class TestUnresolvedReferences:
    def test_a_dangling_asset_reference_is_distinguished_from_an_undecided_one(self):
        outlook = _build_capital_outlook(
            [_item("Ghost", 2030, 100.0, {"A1": 0.5, "B1": 0.5},
                   attribution="unresolved_reference", asset_ref="asset-missing"),
             _item("Open", 2031, 100.0, {"A1": 0.5, "B1": 0.5},
                   attribution="entitlement_default")],
            LOT_GROUPS, PAYMENT_SHARES, 1.0, 1.0,
        )
        assert [i["asset_ref"] for i in outlook["unresolved_items"]] == ["asset-missing"]
        # The undecided item is counted separately: it is a committee decision, not a
        # broken record, and telling an operator to "fix" it would be wrong.
        assert outlook["unattributed_items"] == 1
        assert outlook["attributed_items"] == 0

    def test_a_fully_attributed_plan_reports_nothing_unresolved(self):
        outlook = _build_capital_outlook(
            [_item("Lift", 2030, 100.0, {"A1": 1.0})],
            LOT_GROUPS, PAYMENT_SHARES, 1.0, 1.0,
        )
        assert outlook["unresolved_items"] == []
        assert outlook["attributed_items"] == 1


class TestFundingGap:
    def test_an_underfunded_plan_reports_a_positive_gap(self):
        outlook = _build_capital_outlook(
            [_item("Big", 2027, 1000.0, {"A1": 1.0}),
             _item("Also", 2036, 0.01, {"A1": 1.0})],
            LOT_GROUPS, PAYMENT_SHARES, sinking_annual=1.0, total_ue=10.0,
        )
        # $1000.01 planned against 10 years x $1 x 10 UE = $100 raised.
        assert outlook["funding_gap"] == pytest.approx(900.01)

    def test_group_deltas_are_not_required_to_net_to_zero(self):
        # Unlike the annual lens, planned spend and planned contributions are two
        # independent figures, so a non-zero sum here is a solvency finding rather than
        # an arithmetic fault. Asserting zero-sum on this lens would be the bug.
        outlook = _build_capital_outlook(
            [_item("Big", 2027, 900.0, {"A1": 1.0}),
             _item("End", 2036, 0.0 + 1.0, {"B1": 1.0})],
            LOT_GROUPS, PAYMENT_SHARES, sinking_annual=1.0, total_ue=3.0,
        )
        total_delta = sum(g["delta"] for g in outlook["groups"])
        assert total_delta == pytest.approx(outlook["funding_gap"])


class TestRemedies:
    def test_a_material_asymmetry_offers_the_class_contribution_only_as_last_resort(self):
        remedies = _build_remedies(
            [{"group": "Group A", "current_total": 1000.0, "delta": -300.0},
             {"group": "Group B", "current_total": 1000.0, "delta": 300.0}],
            None, [],
        )
        method = [r for r in remedies if r["shape"] == "class_contribution"]
        assert len(method) == 1
        assert method[0]["status"] == "last_resort"
        # It is the only remedy that needs owners to vote, and the only one an owner can
        # take to ACAT. Presenting it beside the structural options as an equal is how a
        # committee reaches for it first.
        assert method[0]["requires_resolution"] is True

    def test_an_immaterial_difference_recommends_taking_no_action(self):
        remedies = _build_remedies(
            [{"group": "Group A", "current_total": 1000.0, "delta": 1.0}], None, [],
        )
        shapes = {r["shape"]: r for r in remedies}
        assert "class_contribution" not in shapes
        assert shapes["immaterial"]["status"] == "recommended"
        assert shapes["immaterial"]["requires_resolution"] is False

    def test_structural_remedies_never_require_a_special_resolution(self):
        # The whole argument for surfacing them: they change the COST, so they need no
        # vote and cannot be reviewed by a tribunal.
        for shape, entry in _REMEDY_CATALOGUE.items():
            if shape == "class_contribution":
                continue
            assert entry["requires_resolution"] is False, shape

    def test_every_remedy_states_the_evidence_it_would_produce(self):
        # A remedy that produces no evidence cannot answer the s.78(3) factors, which is
        # what an owners corporation is actually required to address.
        for shape, entry in _REMEDY_CATALOGUE.items():
            assert entry["evidence_produced"], shape
            assert entry["detail"].strip(), shape

    def test_a_metered_cost_line_offers_sub_metering(self):
        remedies = _build_remedies(
            [{"group": "Group A", "current_total": 1000.0, "delta": 1.0}],
            None,
            [{"name": "Common Lighting", "shape": "metered_utility", "cost": 5000.0}],
        )
        metered = [r for r in remedies if r["shape"] == "metered_utility"]
        assert metered and metered[0]["cost_line"] == "Common Lighting"

    def test_an_unknown_cost_shape_invents_no_remedy(self):
        remedies = _build_remedies(
            [{"group": "Group A", "current_total": 1000.0, "delta": 1.0}],
            None,
            [{"name": "Mystery", "shape": "not_a_shape", "cost": 1.0}],
        )
        assert all(r["cost_line"] != "Mystery" for r in remedies)

    def test_a_skewed_capital_plan_offers_per_item_attribution(self):
        outlook = _build_capital_outlook(
            [_item("Lift", 2030, 1000.0, {"A1": 1.0})],
            LOT_GROUPS, PAYMENT_SHARES, sinking_annual=1.0, total_ue=3.0,
        )
        remedies = _build_remedies(
            [{"group": "Group A", "current_total": 1000.0, "delta": 1.0}], outlook, [],
        )
        assert any(r["shape"] == "capital_plan_asymmetry" for r in remedies)
