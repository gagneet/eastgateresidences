"""Tests for the generate_from_budget_v1 dispatch and the toggle-gate fix in
routers/financial_onboarding.py.

The existing tests/backend/test_financial_onboarding_router.py uses a hand-rolled
fake-module harness that can't exercise real Pydantic validation or real FastAPI
dependency resolution (see its own module docstring). This file uses the real
FastAPI app + TestClient + dependency_overrides pattern (matching
test_cutover_admin_router.py) so the toggle-gate Depends(require_feature(...))
additions and the generation_method dispatch are exercised for real, not against
stand-in stubs.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.financial_onboarding import router
from services.reconstruction_generators.budget_levy_generator import GENERATION_METHOD
from services.reconstruction_generators.historical_levy_charge_generator import (
    LevyChargeGenerationError,
)
from utils.auth import get_current_building, get_current_user

BUILDING_ID = "16244"
SCHEME_ID = uuid.UUID("22222222-0000-0000-0000-000000000002")
TENANT_ID = uuid.UUID("11111111-0000-0000-0000-000000000001")

_SCHEME = {"scheme_id": SCHEME_ID, "tenant_id": TENANT_ID, "scheme_number": BUILDING_ID}


def _user(role: str = "strata_manager") -> dict:
    return {"id": "user-1", "full_name": "Test User", "role": role, "effective_role": role}


def _build_app(user: dict, building_id: str = BUILDING_ID) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_building] = lambda: building_id
    return TestClient(app)


def _patched_scheme_resolution():
    return patch("routers.financial_onboarding.resolve_scheme_context", new=AsyncMock(return_value=_SCHEME))


class TestToggleGateOnEvidenceEndpoints:
    """The 4 endpoints (evidence, onboard/building, cutover, archive) had zero
    Depends(require_feature(...)) before this fix — confirm they now 403 with a
    FEATURE_DISABLED code when the toggle is off, and pass the gate when on."""

    @pytest.mark.asyncio
    async def test_create_evidence_document_403_when_toggle_off(self):
        client = _build_app(_user())
        with _patched_scheme_resolution(), \
             patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=False)):
            resp = client.post(
                "/api/financials/onboarding/evidence",
                json={
                    "document_type": "bank_statement", "file_url": "https://x/y", "sha256_hash": "a" * 64,
                    "declared_total_cents": 100, "declared_totals_by_fund": {"admin": 100},
                },
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "FEATURE_DISABLED"
        assert resp.json()["detail"]["feature_key"] == "historical_financial_reconstruction"

    @pytest.mark.asyncio
    async def test_create_evidence_document_passes_gate_when_toggle_on(self):
        client = _build_app(_user())
        with _patched_scheme_resolution(), \
             patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True)), \
             patch("routers.financial_onboarding.create_evidence_document", new=AsyncMock(return_value={"id": "evd-1"})):
            resp = client.post(
                "/api/financials/onboarding/evidence",
                json={
                    "document_type": "bank_statement", "file_url": "https://x/y", "sha256_hash": "a" * 64,
                    "declared_total_cents": 100, "declared_totals_by_fund": {"admin": 100},
                },
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_onboard_financials_gated_on_posting_toggle_not_prep_toggle(self):
        """Endpoint #2 posts real genesis journals — must be gated on
        historical_reconstruction_posting, not historical_financial_reconstruction."""
        client = _build_app(_user())
        with _patched_scheme_resolution(), \
             patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=False)):
            resp = client.post(
                f"/api/financials/onboarding/building/{BUILDING_ID}",
                json={
                    "cutover_date": "2024-01-01", "opening_balances": [{"fund_code": "admin", "amount_cents": 100}],
                    "evidence_document_id": "evd-1",
                },
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["feature_key"] == "historical_reconstruction_posting"

    @pytest.mark.asyncio
    async def test_get_cutover_config_403_when_toggle_off(self):
        client = _build_app(_user())
        with _patched_scheme_resolution(), \
             patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=False)):
            resp = client.get(f"/api/financials/cutover/{BUILDING_ID}")
        assert resp.status_code == 403
        assert resp.json()["detail"]["feature_key"] == "historical_financial_reconstruction"

    @pytest.mark.asyncio
    async def test_get_financial_archive_403_when_toggle_off(self):
        client = _build_app(_user())
        with _patched_scheme_resolution(), \
             patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=False)):
            resp = client.get(f"/api/financials/archive/{BUILDING_ID}")
        assert resp.status_code == 403
        assert resp.json()["detail"]["feature_key"] == "historical_financial_reconstruction"


class TestGenerationMethodDispatch:
    """generate_from_budget_v1 must route to the new generator; any other
    reconstruction_method value must keep routing to the unchanged East-Gate-only
    link_existing_rows_as_manifest — a regression guard so East Gate's existing
    stored batches keep working exactly as before."""

    @pytest.mark.asyncio
    async def test_generate_from_budget_calls_new_generator(self):
        """_build_manifest_for_batch merges the income generator's output with the expense
        generator's into ONE new ReconstructionManifest (GAP-FIN-024 income+expense merge) — it
        no longer passes the income generator's return value straight through, so this asserts
        the merged result carries the income mock's transaction through, plus the correct
        dispatch (income generator called, legacy linker not called), rather than identity
        equality with a bare sentinel."""
        from datetime import date

        from routers.financial_onboarding import _build_manifest_for_batch
        from integrations.demo_bank.reconstruction_batch_schemas import (
            ReconstructedTransactionRow,
            ReconstructionBatch,
            ReconstructionManifest,
        )

        batch = ReconstructionBatch(
            batch_id="b1", building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method=GENERATION_METHOD, status="draft",
        )
        income_row = ReconstructedTransactionRow(
            account_ref="OPERATING-16244", unit_number="TH001", financial_year="2024", fund_type="admin",
            posted_date=date(2024, 3, 31), amount_cents=1000, amount_ex_gst_cents=1000, gst_cents=0,
            assumption_code="quarterly_regular", description="test", transaction_sequence=1,
        )
        income_manifest = ReconstructionManifest(
            manifest_id="m1", batch_id="b1", building_id=BUILDING_ID, input_fact_hash="x",
            generator_version=GENERATION_METHOD, expected_transaction_count=1,
            expected_credit_cents=1000, transactions=[income_row], manifest_hash="h1",
        )

        class _FakeSessionContext:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *exc_info):
                return False

        # The GENERATION_METHOD branch opens a real Postgres session via
        # async_session_context()/set_tenant() before calling the generator — both must
        # be mocked here, or this test would open a real DB connection against whatever
        # DATABASE_URL happens to be configured (the exact footgun this repo's own
        # pattern_test_no_db_override_writes_to_real_control_plane note warns about).
        # mongo_db is a MagicMock (not object()) since _fund_balance_reconciliation now calls
        # annual_fund_reconciliation.compute_annual_fund_reconciliation() +
        # reconciliation_exceptions.list_exceptions(), both of which do real find(...).to_list()
        # calls against it even when there's nothing to reconcile — a bare AsyncMock()'s
        # auto-mocked find() returns a coroutine, not a cursor with .to_list(), so this must be
        # configured explicitly (per backend/CLAUDE.md's documented Motor-mocking convention).
        def _empty_cursor(*_args, **_kwargs):
            cursor = AsyncMock()
            cursor.to_list = AsyncMock(return_value=[])
            return cursor

        mongo_db = AsyncMock()
        mongo_db.annual_levies.find_one = AsyncMock(return_value=None)
        mongo_db.annual_levies.find = _empty_cursor
        mongo_db.levy_categories.find = _empty_cursor
        mongo_db.reconciliation_annual_exceptions.find = _empty_cursor
        with _patched_scheme_resolution(), \
             patch("routers.financial_onboarding.async_session_context", return_value=_FakeSessionContext()), \
             patch("routers.financial_onboarding.set_tenant", new=AsyncMock()), \
             patch("routers.financial_onboarding.generate_manifest_from_proposed_budget", new=AsyncMock(return_value=income_manifest)) as new_gen, \
             patch("routers.financial_onboarding.generate_expense_manifest_from_categories", new=AsyncMock(return_value=([], []))), \
             patch(
                 "routers.financial_onboarding.generate_historical_levy_charge_plan",
                 new=AsyncMock(side_effect=LevyChargeGenerationError("no levy charges in this test")),
             ), \
             patch("services.reconstruction_batch_service.link_existing_rows_as_manifest", new=AsyncMock()) as legacy_gen:
            result = await _build_manifest_for_batch(mongo_db, BUILDING_ID, batch)

        assert result.transactions == [income_row]
        assert result.expected_credit_cents == 1000
        assert result.expected_debit_cents == 0
        new_gen.assert_awaited_once()
        legacy_gen.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_levy_charge_lines_attached_to_the_same_manifest(self):
        """The manifest a GENERATION_METHOD batch builds must carry the levy-charge generator's
        output alongside the Demo Bank income/expense rows — one manifest, one dual-control
        approval, per the 2026-07-31 product decision (docs/finances/
        reconcilation-2021-2022-finances-plan02.md)."""
        from datetime import date

        from routers.financial_onboarding import _build_manifest_for_batch
        from services.reconstruction_generators.historical_levy_charge_generator import (
            LevyChargeLine,
            LevyChargePlan,
        )
        from integrations.demo_bank.reconstruction_batch_schemas import (
            ReconstructionBatch,
        )

        batch = ReconstructionBatch(
            batch_id="b3", building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
            reconstruction_method=GENERATION_METHOD, status="draft",
        )
        charge_line = LevyChargeLine(
            unit_number="TH001", lot_id=uuid.uuid4(), owner_party_id=uuid.uuid4(),
            owner_resolution_status="resolved", year=2024, fund_type="admin", levy_type="ordinary",
            period="Q1", quarter_no=1, issue_date=date(2024, 3, 31), due_date=date(2024, 3, 31),
            uoe=100.0, total_uoe=100.0, principal_cents=10000, gst_cents=1000, total_cents=11000,
            source_annual_control="annual_levies.proposed_admin_expenses (year=2024)",
            idempotency_key="k1",
        )
        charge_plan = LevyChargePlan(
            building_id=BUILDING_ID, from_year=2024, to_year=2024, as_of_date=date(2024, 12, 31),
            lines=[charge_line], total_principal_cents=10000, total_gst_cents=1000,
        )

        class _FakeSessionContext:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *exc_info):
                return False

        def _empty_cursor(*_args, **_kwargs):
            cursor = AsyncMock()
            cursor.to_list = AsyncMock(return_value=[])
            return cursor

        mongo_db = AsyncMock()
        mongo_db.annual_levies.find_one = AsyncMock(return_value=None)
        mongo_db.annual_levies.find = _empty_cursor
        mongo_db.levy_categories.find = _empty_cursor
        mongo_db.reconciliation_annual_exceptions.find = _empty_cursor

        from services.reconstruction_generators.budget_levy_generator import (
            ReconstructionGenerationError,
        )

        with _patched_scheme_resolution(), \
             patch("routers.financial_onboarding.async_session_context", return_value=_FakeSessionContext()), \
             patch("routers.financial_onboarding.set_tenant", new=AsyncMock()), \
             patch(
                 "routers.financial_onboarding.generate_manifest_from_proposed_budget",
                 new=AsyncMock(side_effect=ReconstructionGenerationError("no income in this test")),
             ), \
             patch("routers.financial_onboarding.generate_expense_manifest_from_categories", new=AsyncMock(return_value=([], []))), \
             patch("routers.financial_onboarding.generate_historical_levy_charge_plan", new=AsyncMock(return_value=charge_plan)):
            result = await _build_manifest_for_batch(mongo_db, BUILDING_ID, batch)

        assert len(result.levy_charge_lines) == 1
        line = result.levy_charge_lines[0]
        assert line.unit_number == "TH001"
        assert line.fund_type == "admin"
        assert line.total_cents == 11000
        assert result.calculation_configuration["levy_charge_total_cents"] == 11000
        assert result.calculation_configuration["levy_charge_line_count"] == 1

    @pytest.mark.asyncio
    async def test_other_method_calls_legacy_linker_unchanged(self):
        from routers.financial_onboarding import _build_manifest_for_batch
        from integrations.demo_bank.reconstruction_batch_schemas import ReconstructionBatch

        batch = ReconstructionBatch(
            batch_id="b2", building_id="13195", financial_year_start=2024, financial_year_end=2024,
            reconstruction_method="gst_uoe_largest_remainder_v5", status="draft",
        )
        sentinel = object()
        with patch("routers.financial_onboarding.generate_manifest_from_proposed_budget", new=AsyncMock()) as new_gen, \
             patch("services.reconstruction_batch_service.link_existing_rows_as_manifest", new=AsyncMock(return_value=sentinel)) as legacy_gen:
            result = await _build_manifest_for_batch(object(), "13195", batch)

        assert result is sentinel
        legacy_gen.assert_awaited_once()
        new_gen.assert_not_awaited()
