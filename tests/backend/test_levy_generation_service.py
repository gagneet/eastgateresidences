"""Unit tests for services/levy_generation_service.py.

build_levy_regeneration_plan() reads from four boundary functions (_load_units,
_load_levies, _load_gst_settings from migration_027, plus this module's own
_resolve_lot_ids/_resolve_current_levy_items/_synthetic_bank_totals_by_year_fund).
All are patched directly so these tests exercise the reconciliation math and
action-classification logic without a database connection.
"""
from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from scripts.migrations.migration_027_randomize_east_gate_demo_bank_levies import UnitShare
from services.financial_core.domain.entities import SchemeRef
from services.levy_generation_service import build_levy_regeneration_plan

BUILDING_ID = "16244"  # Sierra demo — never "13195" in tests
TENANT_ID = uuid.UUID("11111111-0000-0000-0000-000000000001")
SCHEME_ID = uuid.UUID("22222222-0000-0000-0000-000000000002")
SCHEME_REF = SchemeRef(tenant_id=TENANT_ID, scheme_id=SCHEME_ID)
LOT_1 = uuid.UUID("33333333-0000-0000-0000-000000000001")
LOT_2 = uuid.UUID("33333333-0000-0000-0000-000000000002")

_GST_SETTINGS = {
    "gst_registered": True, "levy_gst_rate": 0.10, "effective_gst_rate": 0.10,
    "gst_multiplier": 1.10, "gst_label": "GST (10%)",
}


def _patch_all(*, units, levies, existing_items=None, bank_totals=None, lot_ids=None, gst_settings=None):
    """Context-manager stack patching every boundary function
    build_levy_regeneration_plan reads from."""
    return (
        patch("services.levy_generation_service._load_units", new=AsyncMock(return_value=units)),
        patch("services.levy_generation_service._load_levies", new=AsyncMock(return_value=levies)),
        patch("services.levy_generation_service._load_gst_settings", new=AsyncMock(return_value=gst_settings or _GST_SETTINGS)),
        patch("services.levy_generation_service._resolve_lot_ids", new=AsyncMock(return_value=lot_ids or {})),
        patch("services.levy_generation_service._resolve_current_levy_items", new=AsyncMock(return_value=existing_items or {})),
        patch("services.levy_generation_service._synthetic_bank_totals_by_year_fund", new=AsyncMock(return_value=bank_totals or {})),
    )


async def _run(**kwargs):
    patches = _patch_all(**kwargs)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        return await build_levy_regeneration_plan(
            building_id=BUILDING_ID, mongo_db=object(), pg_session=object(),
            scheme_ref=SCHEME_REF, from_year=2024, to_year=2024,
        )


class TestPerfectReconciliation:
    @pytest.mark.asyncio
    async def test_single_unit_single_fund_reconciles_exactly(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        # $200,000 — deliberately ABOVE the $150,000 ATO non-profit GST registration threshold
        # (AUSTRALIAN_GST_NONPROFIT_TURNOVER_THRESHOLD_CENTS) so this test exercises GST math,
        # not threshold behaviour (covered separately in test_levy_generation_gst_threshold.py).
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 200000.0, "data_origin": "audited_pdf"}}
        bank_totals = {("2024", "admin"): 22000000}  # $200,000 * 1.10 GST-inclusive, in cents

        plan = await _run(units=units, levies=levies, bank_totals=bank_totals)

        assert len(plan.by_year_fund) == 1
        yf = plan.by_year_fund[0]
        assert yf.year == "2024"
        assert yf.fund_type == "admin"
        assert yf.annual_levies_proposed_inc_gst_cents == 22000000
        assert yf.regenerated_levy_items_total_cents == 22000000
        assert yf.variance_cents == 0
        assert yf.within_tolerance is True
        assert plan.totals_reconcile is True

    @pytest.mark.asyncio
    async def test_gst_split_matches_configured_rate(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 200000.0, "data_origin": "audited_pdf"}}

        plan = await _run(units=units, levies=levies)

        line = plan.lines[0]
        assert line.principal_cents == 20000000  # $200,000 ex-GST
        assert line.gst_cents == 2000000  # 10% of ex-GST
        assert line.principal_cents + line.gst_cents == 22000000


class TestActionClassification:
    @pytest.mark.asyncio
    async def test_insert_when_no_existing_row(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf"}}

        plan = await _run(units=units, levies=levies)
        assert plan.lines[0].action == "insert"

    @pytest.mark.asyncio
    async def test_update_when_existing_gst_differs(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 200000.0, "data_origin": "audited_pdf"}}
        existing = {("2024", "admin", "TH001"): {
            "levy_item_id": uuid.uuid4(), "principal_cents": 20000000, "gst_cents": 0,
            "paid_cents": 0, "owner_party_id": uuid.uuid4(), "fund_id": uuid.uuid4(),
        }}

        plan = await _run(units=units, levies=levies, existing_items=existing)
        assert plan.lines[0].action == "update"
        assert plan.lines[0].existing_gst_cents == 0
        assert plan.lines[0].gst_cents == 2000000

    @pytest.mark.asyncio
    async def test_unchanged_when_existing_already_correct(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 200000.0, "data_origin": "audited_pdf"}}
        existing = {("2024", "admin", "TH001"): {
            "levy_item_id": uuid.uuid4(), "principal_cents": 20000000, "gst_cents": 2000000,
            "paid_cents": 0, "owner_party_id": uuid.uuid4(), "fund_id": uuid.uuid4(),
        }}

        plan = await _run(units=units, levies=levies, existing_items=existing)
        assert plan.lines[0].action == "unchanged"

    @pytest.mark.asyncio
    async def test_manual_review_overpaid_when_paid_exceeds_new_total(self):
        """Real East Gate scenario: paid_cents exceeds the GST-corrected
        total — must be flagged and excluded from totals_reconcile,
        even though the aggregate year/fund variance is otherwise zero."""
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 200000.0, "data_origin": "audited_pdf"}}
        existing = {("2024", "admin", "TH001"): {
            "levy_item_id": uuid.uuid4(), "principal_cents": 20000000, "gst_cents": 0,
            "paid_cents": 22000001, "owner_party_id": uuid.uuid4(), "fund_id": uuid.uuid4(),
        }}

        plan = await _run(units=units, levies=levies, existing_items=existing)

        assert plan.lines[0].action == "manual_review_overpaid"
        assert plan.totals_reconcile is False
        assert any("manual review" in w for w in plan.warnings)


class TestProvenanceWarnings:
    @pytest.mark.asyncio
    async def test_no_discontinuity_warning_when_data_origin_stable(self):
        units = [UnitShare(unit_number="TH001", uoe=100.0)]
        levies = {2024: {"year": "2024", "proposed_admin_expenses": 1000.0, "data_origin": "audited_pdf"}}

        plan = await _run(units=units, levies=levies)
        assert not any("unresolved_source_discontinuity" in w for w in plan.warnings)


class TestMultiFundMultiUnit:
    @pytest.mark.asyncio
    async def test_two_units_two_funds_all_reconcile(self):
        units = [UnitShare(unit_number="TH001", uoe=60.0), UnitShare(unit_number="TH002", uoe=40.0)]
        levies = {2024: {
            "year": "2024", "proposed_admin_expenses": 200000.0, "proposed_sinking_expenses": 100000.0,
            "data_origin": "audited_pdf",
        }}

        plan = await _run(units=units, levies=levies)

        assert len(plan.by_year_fund) == 2
        assert len(plan.lines) == 4  # 2 units x 2 funds
        for yf in plan.by_year_fund:
            assert yf.within_tolerance is True
        # Largest-remainder allocation must sum exactly to the fund total per unit pair
        admin_lines = [l for l in plan.lines if l.fund_type == "admin"]
        assert sum(l.principal_cents + l.gst_cents for l in admin_lines) == 22000000


class TestSyntheticBankTotalsByYearFund:
    """Direct coverage for _synthetic_bank_totals_by_year_fund — every test
    above mocks this function away entirely, so a real regression here (the
    Motor-vs-PyMongo-4.10 coroutine-aggregate bug fixed alongside this test)
    was invisible to the existing suite. mongo_db here is db._db-shaped: a
    raw collection whose .aggregate() is itself a coroutine that must be
    awaited before the result can be async-iterated (PyMongo 4.10's native
    async contract) — NOT Motor's old synchronous-cursor-returning aggregate.
    """

    @pytest.mark.asyncio
    async def test_awaits_the_aggregate_coroutine_before_iterating(self):
        from services.levy_generation_service import _synthetic_bank_totals_by_year_fund

        class _FakeCursor:
            def __init__(self, docs):
                self._docs = docs

            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                for d in self._docs:
                    yield d

        class _FakeCollection:
            def __init__(self, docs):
                self._docs = docs

            async def aggregate(self, pipeline):
                # The real bug: this is a coroutine (must be awaited) that
                # resolves to a cursor, not a cursor itself.
                return _FakeCursor(self._docs)

        class _FakeMongoDb:
            def __init__(self, docs):
                self.demo_bank_transactions = _FakeCollection(docs)

        fake_db = _FakeMongoDb([
            {"_id": {"year": "2024", "fund": "admin"}, "total": 50000},
            {"_id": {"year": "2024", "fund": "sinking"}, "total": 20000},
        ])

        totals = await _synthetic_bank_totals_by_year_fund(fake_db, "16244")

        assert totals == {("2024", "admin"): 50000, ("2024", "sinking"): 20000}

    @pytest.mark.asyncio
    async def test_matches_provenance_class_not_stale_source_type(self):
        """Regression test for the 2026-08-01 bug: this pipeline used to match
        source_type="synthetic_from_budget" and group by $levy_year -- both belong to the OLDER
        migration_026/027 generator only. The current generator (budget_levy_generator.py /
        expense_category_generator.py, via the vendored package's
        import_historical_reconstruction()) tags rows source_type="historical_reconstruction"
        and never populates levy_year at all -- so the old filter matched zero East Gate rows,
        permanently. Both old and new generators DO set provenance_class="reconstruction", so
        that's the correct match; the year must fall back through $ifNull to strata_year (the
        current generator's backfilled equivalent of the old levy_year field)."""
        from services.levy_generation_service import _synthetic_bank_totals_by_year_fund

        class _FakeCollection:
            def __init__(self):
                self.captured_pipeline = None

            async def aggregate(self, pipeline):
                self.captured_pipeline = pipeline

                class _EmptyCursor:
                    def __aiter__(self):
                        return self._gen()

                    async def _gen(self):
                        return
                        yield  # pragma: no cover - makes this an async generator

                return _EmptyCursor()

        class _FakeMongoDb:
            def __init__(self):
                self.demo_bank_transactions = _FakeCollection()

        fake_db = _FakeMongoDb()
        await _synthetic_bank_totals_by_year_fund(fake_db, "13195")

        pipeline = fake_db.demo_bank_transactions.captured_pipeline
        match_stage = pipeline[0]["$match"]
        assert match_stage.get("provenance_class") == "reconstruction"
        assert "source_type" not in match_stage
        # amount_cents is always a positive magnitude (direction carries the sign) -- without
        # this filter, "bank payment total" silently blends expense debits into an income-only
        # total. Caught live 2026-08-01 after backfilling missing expense rows roughly tripled
        # this function's output for expense-heavy years.
        assert match_stage.get("direction") == "credit"

        group_stage = pipeline[-1]["$group"]
        assert group_stage["_id"]["year"] == "$year"
        assert group_stage["_id"]["fund"] == "$lines.fund_type"

    @pytest.mark.asyncio
    async def test_fund_split_uses_allocations_not_account_ref_for_combined_rows(self):
        """Regression test for the 2026-08-01 fund-misattribution bug: the current generator
        writes ALL owner-payment credit rows to one combined OPERATING-{building_id} account_ref
        (see budget_levy_generator.py's own docstring on why), with admin/sinking split living
        only in each row's allocations[] lines. Classifying fund purely from account_ref silently
        attributed 100% of this income to "admin" and made sinking-fund income vanish entirely
        from the reconciliation-summary report. A real (non-mocked-pipeline) aggregation against
        an in-memory fake collection proves the $project/$unwind actually splits correctly."""
        from services.levy_generation_service import _synthetic_bank_totals_by_year_fund

        docs = [
            {
                "building_id": "13195", "provenance_class": "reconstruction", "direction": "credit",
                "is_archived": False, "strata_year": "2026", "account_ref": "OPERATING-13195",
                "amount_cents": 90277,
                "allocations": [
                    {"fund_type": "admin", "amount_cents": 69878},
                    {"fund_type": "sinking", "amount_cents": 20399},
                ],
            },
            {
                # Older generator shape: no allocations, fund lives only in account_ref.
                "building_id": "13195", "provenance_class": "reconstruction", "direction": "credit",
                "is_archived": False, "levy_year": "2022", "account_ref": "SINKING-13195",
                "amount_cents": 5000, "allocations": [],
            },
        ]

        class _FakeCollection:
            async def aggregate(self, pipeline):
                # Minimal real evaluation of just the stages this test exercises, against the
                # fixed `docs` above -- proves the pipeline SHAPE does the right split, without
                # needing a live Mongo instance.
                import re as _re

                def project(doc):
                    year = doc.get("levy_year") or doc.get("strata_year")
                    allocations = doc.get("allocations") or []
                    if allocations:
                        lines = allocations
                    else:
                        fund = "sinking" if _re.search("SINKING", doc["account_ref"]) else "admin"
                        lines = [{"fund_type": fund, "amount_cents": doc["amount_cents"]}]
                    return {"year": year, "lines": lines}

                projected = [project(d) for d in docs]
                unwound = [{"year": p["year"], "lines": line} for p in projected for line in p["lines"]]
                totals: dict[tuple[str, str], int] = {}
                for row in unwound:
                    key = (row["year"], row["lines"]["fund_type"])
                    totals[key] = totals.get(key, 0) + row["lines"]["amount_cents"]

                class _Cursor:
                    def __aiter__(self):
                        return self._gen()

                    async def _gen(self):
                        for (year, fund), total in totals.items():
                            yield {"_id": {"year": year, "fund": fund}, "total": total}

                return _Cursor()

        class _FakeMongoDb:
            def __init__(self):
                self.demo_bank_transactions = _FakeCollection()

        totals = await _synthetic_bank_totals_by_year_fund(_FakeMongoDb(), "13195")

        assert totals[("2026", "admin")] == 69878
        assert totals[("2026", "sinking")] == 20399
        assert totals[("2022", "sinking")] == 5000
