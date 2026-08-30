"""
tests/backend/test_east_gate_levy_income_reconstruction.py

East Gate 13195 historical reconstruction, Step 2 (financial-db-issues_plan04.md).
Covers the pure allocation/GST-split math, event derivation from a
financial_source_fact_versions-shaped document, and full manifest generation +
shadow reconciliation — including the exact bug class the first version of
generate_levy_income_manifest had (bucketing generated totals by
(financial_year, fund_type) alone conflates events that apply in a different
calendar year than their confirmed target, and conflates "additional" events
with ordinary ones — see reconciliation_targets()).
"""
from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from services.east_gate_levy_income_reconstruction import (
    allocate_event_to_units,
    build_levy_events,
    generate_levy_income_manifest,
    largest_remainder_allocation,
    reconciliation_targets,
    split_gst_inclusive,
)

BUILDING_A = "13195"


# ── 1. largest_remainder_allocation ────────────────────────────────────────────

class TestLargestRemainderAllocation:
    def test_sums_exactly_to_total_even_with_awkward_weights(self):
        # $69,230.00 across 87 units with real East Gate-shaped UOE values would
        # never divide evenly — this uses a smaller but equally awkward case.
        weights = {"A": 161, "B": 160, "C": 113, "D": 87}  # sums to 521, doesn't divide 6923000 evenly
        result = largest_remainder_allocation(692300, weights)
        assert sum(result.values()) == 692300

    def test_th087_worked_example_lands_within_one_cent_of_naive_share(self):
        """UOE 161/10000 of $69,230.00 — the user's own worked example."""
        weights = {"TH087": 161}
        weights.update({f"OTHER{i}": 100 for i in range(86)})  # 86 * 100 + 161 = 8761... pad to 10000
        weights["PAD"] = 10000 - sum(weights.values())
        result = largest_remainder_allocation(6923000, weights)
        naive = 6923000 * 161 / 10000  # 111460.3
        assert abs(result["TH087"] - naive) < 1  # within 1 cent, i.e. either 111460 or 111461
        assert sum(result.values()) == 6923000

    def test_zero_total_returns_all_zero(self):
        result = largest_remainder_allocation(0, {"A": 100, "B": 200})
        assert result == {"A": 0, "B": 0}

    def test_rejects_zero_or_negative_total_weight(self):
        with pytest.raises(ValueError):
            largest_remainder_allocation(1000, {"A": 0, "B": 0})

    def test_deterministic_across_repeated_calls(self):
        weights = {"A": 33, "B": 33, "C": 34}
        r1 = largest_remainder_allocation(100, weights)
        r2 = largest_remainder_allocation(100, weights)
        assert r1 == r2


# ── 2. split_gst_inclusive ──────────────────────────────────────────────────────

class TestSplitGstInclusive:
    def test_net_plus_gst_equals_gross(self):
        for gross in (11000, 138460 * 100, 69230014, 1, 999999):
            net, gst = split_gst_inclusive(gross)
            assert net + gst == gross

    def test_standard_ten_percent_split(self):
        net, gst = split_gst_inclusive(11000)  # $110.00 gross
        assert net == 10000  # $100.00
        assert gst == 1000  # $10.00

    def test_zero_gross_is_zero_zero(self):
        assert split_gst_inclusive(0) == (0, 0)


# ── 3. build_levy_events + reconciliation_targets (minimal fixture) ────────────

def _minimal_source_facts() -> dict:
    """Small, hand-computable fixture — not the real 6-year East Gate document,
    just enough shape to exercise every branch of build_levy_events()."""
    return {
        "years": {
            "2021": {
                "admin_fund": {"amount_cents": 13846000},
                "sinking_fund": {"amount_cents": 0},
                "levy_instalments": {
                    "instalments": [
                        {"date": "2020-12-23", "amount_cents": 6923000, "type": "ordinary_half_year_1"},
                        {"date": "2021-06-01", "amount_cents": 6923000, "type": "ordinary_half_year_2"},
                        {"date": "2022-01-01", "amount_cents": 6923014, "type": "additional_urgent_levy"},
                    ],
                },
            },
            "2022": {
                "admin_fund": {"amount_cents": 22190000},
                "sinking_fund": {"amount_cents": 2500000},
                "levy_instalments": {
                    "instalments": [
                        {"date": "2022-01-28", "quarter": "Q1"},
                        {"date": "2022-03-03", "quarter": "Q2"},
                        {"date": "2022-08-01", "quarter": "Q3"},
                        {"date": "2022-12-01", "quarter": "Q4"},
                    ],
                },
            },
            "2023": {"admin_fund": {"amount_cents": 24344845}, "sinking_fund": {"amount_cents": 6705342}},
        },
        "additional_levy_instalments": {
            "dec_2021_urgent_levy": {"amount_cents": 6923014, "ledger_posting_date": "2022-01-01"},
            "dec_2022_sinking_fund_contribution": {
                "amount_cents": 3000000,
                "transfer_date": "2022-12-02",
                "distinct_from_ordinary_levy_evidence": {
                    "ordinary_2022_sinking_instalments": [
                        {"date": "2022-03-21", "amount_cents": 833334},
                        {"date": "2022-08-05", "amount_cents": 833334},
                        {"date": "2022-08-26", "amount_cents": 833333},
                    ],
                    "ordinary_2022_sinking_instalments_total_cents": 2500001,
                },
            },
        },
        "levy_schedule": {"due_dates": {"Q1": "03-30", "Q2": "06-01", "Q3": "09-01", "Q4": "12-01"}},
    }


class TestBuildLevyEvents:
    def test_2021_emits_two_ordinary_plus_one_urgent_all_admin_only(self):
        events = build_levy_events(_minimal_source_facts(), date(2024, 1, 1))
        e2021 = [e for e in events if e.applied_date.year in (2020, 2021) or
                 (e.applied_date == date(2022, 1, 1) and e.assumption_code == "confirmed_additional_urgent_levy")]
        assert len(e2021) == 3
        assert all(set(e.fund_amounts_cents.keys()) == {"admin"} for e in e2021)
        assert sum(e.fund_amounts_cents["admin"] for e in e2021) == 6923000 + 6923000 + 6923014

    def test_urgent_levy_tagged_with_its_own_reconciliation_group(self):
        events = build_levy_events(_minimal_source_facts(), date(2024, 1, 1))
        urgent = [e for e in events if e.assumption_code == "confirmed_additional_urgent_levy"]
        assert len(urgent) == 1
        assert urgent[0].reconciliation_groups == {"admin": "urgent_levy_dec2021"}
        assert urgent[0].combine_funds is False

    def test_2022_admin_and_sinking_not_combined_real_evidence_of_separate_schedules(self):
        events = build_levy_events(_minimal_source_facts(), date(2024, 1, 1))
        # Admin instalments: only the DATE is confirmed per-instalment (the amount is an
        # equal split of the confirmed annual total) -> "confirmed_date_equal_split_amount".
        # Sinking instalments: both date AND amount are confirmed per-instalment ->
        # "confirmed_real_instalment". These must stay distinct codes — see
        # build_levy_events()'s 2022 admin loop docstring for why conflating them would
        # mislead a reviewer about what was actually confirmed vs inferred.
        e2022_admin = [e for e in events if e.applied_date.year == 2022 and "admin" in e.fund_amounts_cents
                       and e.assumption_code == "confirmed_date_equal_split_amount"]
        e2022_sinking = [e for e in events if e.applied_date.year == 2022 and "sinking" in e.fund_amounts_cents
                          and e.assumption_code == "confirmed_real_instalment"]
        assert len(e2022_admin) == 4
        assert len(e2022_sinking) == 3
        assert all(not e.combine_funds for e in e2022_admin + e2022_sinking)
        assert sum(e.fund_amounts_cents["admin"] for e in e2022_admin) == 22190000
        assert sum(e.fund_amounts_cents["sinking"] for e in e2022_sinking) == 833334 + 833334 + 833333

    def test_additional_sinking_contribution_lower_confidence_and_own_group(self):
        events = build_levy_events(_minimal_source_facts(), date(2024, 1, 1))
        extra = [e for e in events if e.assumption_code == "inferred_uoe_allocation_pending_levy_roll"]
        assert len(extra) == 1
        assert extra[0].confidence == "inferred"
        assert extra[0].reconciliation_groups == {"sinking": "additional_sinking_dec2022"}

    def test_2023_provisional_quarters_combine_admin_and_sinking(self):
        events = build_levy_events(_minimal_source_facts(), date(2024, 1, 1))
        e2023 = [e for e in events if e.applied_date.year == 2023]
        assert len(e2023) == 4  # Q1-Q4
        assert all(e.combine_funds for e in e2023)
        assert all(set(e.fund_amounts_cents.keys()) == {"admin", "sinking"} for e in e2023)
        assert sum(e.fund_amounts_cents["admin"] for e in e2023) == 24344845
        assert sum(e.fund_amounts_cents["sinking"] for e in e2023) == 6705342

    def test_future_quarters_are_never_generated_as_payment_events(self):
        """Live incident, 2026-07-22/23: the pre-fix generator produced 174 real
        Demo Bank transactions ($220,187.44) dated 2026-09-01 and 2026-12-01 — both
        after that generation run's real-world date — as if every unit had already
        paid Q3/Q4 2026 in full months before either due date arrived. A quarter
        must never be generated as a "payment received" event before its own due
        date has actually arrived, barring genuine early payment (not modelled)."""
        as_of = date(2023, 7, 1)  # after Q1 (03-30) and Q2 (06-01); before Q3 (09-01) and Q4 (12-01)
        events = build_levy_events(_minimal_source_facts(), as_of)
        e2023 = [e for e in events if e.applied_date.year == 2023]
        due_dates = {e.applied_date for e in e2023}
        assert due_dates == {date(2023, 3, 30), date(2023, 6, 1)}  # Q1, Q2 only
        assert all(d <= as_of for d in due_dates)

    def test_as_of_date_before_any_provisional_quarter_generates_none(self):
        as_of = date(2023, 1, 1)  # before Q1's 03-30 due date
        events = build_levy_events(_minimal_source_facts(), as_of)
        e2023 = [e for e in events if e.applied_date.year == 2023]
        assert e2023 == []


class TestReconciliationTargets:
    def test_2022_sinking_ordinary_reconciles_against_real_instalment_sum_not_nominal(self):
        """v5's own documented 1-cent difference: nominal years.2022.sinking_fund
        ($25,000.00) vs the real 3-instalment sum ($25,000.01) — the target must
        use the more precise real figure, not the nominal one."""
        targets = reconciliation_targets(_minimal_source_facts(), date(2024, 1, 1))
        assert targets["2022_sinking_ordinary"] == 2500001
        assert targets["2022_sinking_ordinary"] != _minimal_source_facts()["years"]["2022"]["sinking_fund"]["amount_cents"]

    def test_partial_year_target_is_sum_of_included_quarters_not_full_annual_total(self):
        """Reconciling a still-in-progress year against its full-year confirmed total
        would report a permanent, misleading variance for quarters that legitimately
        haven't come due yet. The target for a partial year must be the sum of only
        the quarters build_levy_events() actually generated for that same as_of_date."""
        as_of = date(2023, 7, 1)  # Q1 + Q2 only
        targets = reconciliation_targets(_minimal_source_facts(), as_of)
        full_year_admin = _minimal_source_facts()["years"]["2023"]["admin_fund"]["amount_cents"]
        assert targets["2023_admin_ordinary"] < full_year_admin
        assert targets["2023_admin_ordinary"] > 0
        # Cross-check against build_levy_events()'s own generated total for the same
        # as_of_date — the two must never independently drift.
        events = build_levy_events(_minimal_source_facts(), as_of)
        generated_2023_admin = sum(
            e.fund_amounts_cents["admin"] for e in events if e.applied_date.year == 2023
        )
        assert targets["2023_admin_ordinary"] == generated_2023_admin


# ── 4. Full manifest generation — mocked db, small unit set ────────────────────

def _mock_db(units):
    db = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=units)
    db.units = MagicMock()
    db.units.find = MagicMock(return_value=cursor)
    return db


class TestGenerateLevyIncomeManifest:
    @pytest.mark.asyncio
    async def test_full_reconciliation_ties_out_exactly(self):
        """Regression test for the exact bug class found during development:
        bucketing generated totals by (financial_year, fund_type) alone produced
        three false mismatches (the urgent levy applies in Jan 2022 but has its
        own confirmed target; 2022's real sinking sum differs by 1c from the
        nominal figure). Every group must reconcile to zero variance."""
        units = [{"unit_number": "TH087", "entitlement": 161}] + [
            {"unit_number": f"U{i}", "entitlement": 100} for i in range(1, 100)
        ]
        # pad remaining UOE so total is a round number the allocator can still handle exactly
        total_so_far = 161 + 99 * 100
        units.append({"unit_number": "PAD", "entitlement": 10000 - total_so_far})
        db = _mock_db(units)

        manifest, recon = await generate_levy_income_manifest(
            db, building_id=BUILDING_A, source_facts=_minimal_source_facts(),
            batch_id="test-batch", generator_version="test-v1",
            as_of_date=date(2024, 1, 1),
        )

        assert len(recon) > 0
        for line in recon:
            assert line.within_tolerance, (
                f"{line.year}/{line.fund_type}: expected={line.expected_levy_total_cents} "
                f"generated={line.generated_credit_cents} variance={line.variance_cents}"
            )

    @pytest.mark.asyncio
    async def test_combined_rows_share_payment_group_id_and_sum_to_header(self):
        units = [{"unit_number": "TH087", "entitlement": 161},
                 {"unit_number": "PAD", "entitlement": 10000 - 161}]
        db = _mock_db(units)

        manifest, _ = await generate_levy_income_manifest(
            db, building_id=BUILDING_A, source_facts=_minimal_source_facts(),
            batch_id="test-batch", generator_version="test-v1",
            as_of_date=date(2024, 1, 1),
        )

        q1_2023_th087 = [
            r for r in manifest.transactions
            if r.unit_number == "TH087" and r.posted_date == date(2023, 3, 30)
        ]
        assert len(q1_2023_th087) == 2  # admin + sinking
        group_ids = {r.payment_group_id for r in q1_2023_th087}
        assert len(group_ids) == 1 and None not in group_ids
        assert sum(r.amount_cents for r in q1_2023_th087) == \
            sum(r.amount_ex_gst_cents + r.gst_cents for r in q1_2023_th087)
        # A single real bank transaction can only land in one account — combined
        # (admin+sinking) groups must all share the SAME account_ref (the admin
        # account, the building's primary levy-collection account), never each
        # row's own fund's account_ref. Regression test: found live 2026-07-22 —
        # ingestion.import_historical_reconstruction()'s consistency guard
        # correctly rejected every combined group until this was fixed.
        account_refs_in_group = {r.account_ref for r in q1_2023_th087}
        assert account_refs_in_group == {"ADMIN-13195"}

    @pytest.mark.asyncio
    async def test_2022_rows_not_combined_separate_payment_group_id_none(self):
        units = [{"unit_number": "TH087", "entitlement": 161},
                 {"unit_number": "PAD", "entitlement": 10000 - 161}]
        db = _mock_db(units)

        manifest, _ = await generate_levy_income_manifest(
            db, building_id=BUILDING_A, source_facts=_minimal_source_facts(),
            batch_id="test-batch", generator_version="test-v1",
            as_of_date=date(2024, 1, 1),
        )

        admin_row = [r for r in manifest.transactions
                     if r.unit_number == "TH087" and r.posted_date == date(2022, 1, 28)]
        assert len(admin_row) == 1
        assert admin_row[0].payment_group_id is None

    @pytest.mark.asyncio
    async def test_no_transactions_dated_after_as_of_date_and_partial_year_still_reconciles(self):
        """Live incident regression (2026-07-22/23): a manifest generated as of a date
        partway through the most recent provisional year must contain zero transactions
        dated after that as_of_date, and the partial year's reconciliation group must
        still tie out to exact zero variance (against the sum of only the quarters that
        were actually generated, not the full annual total)."""
        units = [{"unit_number": "TH087", "entitlement": 161},
                 {"unit_number": "PAD", "entitlement": 10000 - 161}]
        db = _mock_db(units)
        as_of = date(2023, 7, 1)  # Q1 + Q2 2023 only; Q3/Q4 2023 not yet due

        manifest, recon = await generate_levy_income_manifest(
            db, building_id=BUILDING_A, source_facts=_minimal_source_facts(),
            batch_id="test-batch", generator_version="test-v1",
            as_of_date=as_of,
        )

        assert all(r.posted_date <= as_of for r in manifest.transactions)
        q3_or_q4_2023 = [r for r in manifest.transactions
                          if r.posted_date in (date(2023, 9, 1), date(2023, 12, 1))]
        assert q3_or_q4_2023 == []

        line_2023_admin = next(l for l in recon if l.year == "2023" and l.fund_type == "admin")
        assert line_2023_admin.within_tolerance
        assert line_2023_admin.variance_cents == 0
        assert 0 < line_2023_admin.expected_levy_total_cents < 24344845  # less than the full annual figure

    @pytest.mark.asyncio
    async def test_rejects_zero_total_uoe(self):
        db = _mock_db([{"unit_number": "X", "entitlement": 0}])
        with pytest.raises(ValueError, match="total UOE"):
            await generate_levy_income_manifest(
                db, building_id=BUILDING_A, source_facts=_minimal_source_facts(),
                batch_id="test-batch", generator_version="test-v1",
                as_of_date=date(2024, 1, 1),
            )

    @pytest.mark.asyncio
    async def test_account_refs_use_building_id_never_hardcoded(self):
        """Multi-tenant rule: account_ref must derive from the actual building_id
        parameter, never a hardcoded '13195' — verified with a different building id."""
        units = [{"unit_number": "U1", "entitlement": 10000}]
        db = _mock_db(units)
        manifest, _ = await generate_levy_income_manifest(
            db, building_id="16244", source_facts=_minimal_source_facts(),
            batch_id="test-batch", generator_version="test-v1",
            as_of_date=date(2024, 1, 1),
        )
        account_refs_used = {r.account_ref for r in manifest.transactions}
        assert account_refs_used == {"ADMIN-16244", "SINKING-16244"}
        assert not any("13195" in ref for ref in account_refs_used)
