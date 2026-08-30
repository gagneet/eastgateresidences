"""
Tests for workflows/generate_levies_workflow.py — GenerateLeviesWorkflow.

Uses temporalio.testing.WorkflowEnvironment (time-skipping embedded server,
no external Temporal process required). Activities are replaced with fast
mock implementations to keep tests self-contained.
"""
from __future__ import annotations

import pytest
from datetime import timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import temporalio.activity
import temporalio.testing
import temporalio.worker

from workflows.generate_levies_workflow import (
    GenerateLeviesWorkflow,
    LevyGenerationInput,
    allocate_crns,
    compute_lot_levies,
    create_levy_documents,
    send_levy_notices,
)

# ── Mock activity implementations ─────────────────────────────────────────────

_MOCK_LOTS = [
    {"lot_id": "lot1", "lot_number": "1", "owner_name": "Alice", "levy_cents": 38250},
    {"lot_id": "lot2", "lot_number": "2", "owner_name": "Bob", "levy_cents": 38250},
    {"lot_id": "lot3", "lot_number": "3", "owner_name": "Carol", "levy_cents": 38250},
]


@temporalio.activity.defn(name="compute_lot_levies")
async def mock_compute_lot_levies(inp: LevyGenerationInput) -> List[Dict[str, Any]]:
    return [dict(l) for l in _MOCK_LOTS]


@temporalio.activity.defn(name="allocate_crns")
async def mock_allocate_crns(
        building_id: str,
        lot_levies: List[Dict[str, Any]],
        quarter: str,
) -> List[Dict[str, Any]]:
    return [{**l, "crn": f"CRN-{l['lot_number']}-{quarter}"} for l in lot_levies]


@temporalio.activity.defn(name="create_levy_documents")
async def mock_create_levy_documents(
        inp: LevyGenerationInput,
        lot_levies: List[Dict[str, Any]],
) -> List[str]:
    return [f"doc_id_{l['lot_id']}" for l in lot_levies]


@temporalio.activity.defn(name="send_levy_notices")
async def mock_send_levy_notices(
        building_id: str,
        lot_levies: List[Dict[str, Any]],
        quarter: str,
        is_test_data: bool = False,
) -> Dict[str, Any]:
    return {"sent": len(lot_levies), "failed": 0}


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestGenerateLeviesWorkflow:
    @pytest.mark.asyncio
    async def test_workflow_generates_correct_lot_count(self):
        async with await temporalio.testing.WorkflowEnvironment.start_time_skipping() as env:
            async with temporalio.worker.Worker(
                    env.client,
                    task_queue="test-levy-gen",
                    workflows=[GenerateLeviesWorkflow],
                    activities=[
                        mock_compute_lot_levies,
                        mock_allocate_crns,
                        mock_create_levy_documents,
                        mock_send_levy_notices,
                    ],
            ) as worker:
                inp = LevyGenerationInput(
                    building_id="16244",
                    quarter="2026-Q2",
                    budget_year="2026",
                    is_test_data=True,
                )
                result = await env.client.execute_workflow(
                    GenerateLeviesWorkflow.run,
                    inp,
                    id="levygen:16244:2026-Q2",
                    task_queue="test-levy-gen",
                )
        assert result["lots_processed"] == 3
        assert result["documents_created"] == 3
        assert result["emails_sent"] == 3
        assert result["emails_failed"] == 0

    @pytest.mark.asyncio
    async def test_workflow_result_contains_building_and_quarter(self):
        async with await temporalio.testing.WorkflowEnvironment.start_time_skipping() as env:
            async with temporalio.worker.Worker(
                    env.client,
                    task_queue="test-levy-gen-2",
                    workflows=[GenerateLeviesWorkflow],
                    activities=[
                        mock_compute_lot_levies,
                        mock_allocate_crns,
                        mock_create_levy_documents,
                        mock_send_levy_notices,
                    ],
            ):
                inp = LevyGenerationInput(
                    building_id="13195",
                    quarter="2026-Q3",
                    budget_year="2026",
                    is_test_data=True,
                )
                result = await env.client.execute_workflow(
                    GenerateLeviesWorkflow.run,
                    inp,
                    id="levygen:13195:2026-Q3",
                    task_queue="test-levy-gen-2",
                )
        assert result["building_id"] == "13195"
        assert result["quarter"] == "2026-Q3"

    @pytest.mark.asyncio
    async def test_crns_allocated_per_lot(self):
        """Each lot must have a CRN after allocate_crns activity completes."""
        captured: list = []

        @temporalio.activity.defn(name="create_levy_documents")
        async def capturing_create_levy_documents(inp, lot_levies):
            captured.extend(lot_levies)
            return [f"doc_{l['lot_id']}" for l in lot_levies]

        async with await temporalio.testing.WorkflowEnvironment.start_time_skipping() as env:
            async with temporalio.worker.Worker(
                    env.client,
                    task_queue="test-levy-gen-3",
                    workflows=[GenerateLeviesWorkflow],
                    activities=[
                        mock_compute_lot_levies,
                        mock_allocate_crns,
                        capturing_create_levy_documents,
                        mock_send_levy_notices,
                    ],
            ):
                inp = LevyGenerationInput(
                    building_id="16244",
                    quarter="2026-Q4",
                    budget_year="2026",
                    is_test_data=True,
                )
                await env.client.execute_workflow(
                    GenerateLeviesWorkflow.run,
                    inp,
                    id="levygen:16244:2026-Q4",
                    task_queue="test-levy-gen-3",
                )

        assert len(captured) == 3
        for lot in captured:
            assert lot.get("crn"), f"Lot {lot['lot_number']} missing CRN"
            assert lot["crn"].startswith("CRN-")

    @pytest.mark.asyncio
    async def test_is_test_data_flag_propagated(self):
        """is_test_data=True must reach the send_levy_notices activity."""
        test_data_flags: list = []

        @temporalio.activity.defn(name="send_levy_notices")
        async def capturing_send_notices(building_id, lot_levies, quarter, is_test_data=False):
            test_data_flags.append(is_test_data)
            return {"sent": 0, "failed": 0}

        async with await temporalio.testing.WorkflowEnvironment.start_time_skipping() as env:
            async with temporalio.worker.Worker(
                    env.client,
                    task_queue="test-levy-gen-4",
                    workflows=[GenerateLeviesWorkflow],
                    activities=[
                        mock_compute_lot_levies,
                        mock_allocate_crns,
                        mock_create_levy_documents,
                        capturing_send_notices,
                    ],
            ):
                inp = LevyGenerationInput(
                    building_id="16244",
                    quarter="2026-Q1",
                    budget_year="2026",
                    is_test_data=True,
                )
                await env.client.execute_workflow(
                    GenerateLeviesWorkflow.run,
                    inp,
                    id="levygen:16244:2026-Q1",
                    task_queue="test-levy-gen-4",
                )

        assert test_data_flags == [True]

    @pytest.mark.asyncio
    async def test_multi_tenant_isolation_building_id_passed_to_activities(self):
        """Workflow for 16244 must not invoke activities with 13195."""
        seen_building_ids: list = []

        @temporalio.activity.defn(name="compute_lot_levies")
        async def tracking_compute(inp: LevyGenerationInput):
            seen_building_ids.append(inp.building_id)
            return _MOCK_LOTS

        async with await temporalio.testing.WorkflowEnvironment.start_time_skipping() as env:
            async with temporalio.worker.Worker(
                    env.client,
                    task_queue="test-levy-gen-5",
                    workflows=[GenerateLeviesWorkflow],
                    activities=[
                        tracking_compute,
                        mock_allocate_crns,
                        mock_create_levy_documents,
                        mock_send_levy_notices,
                    ],
            ):
                inp = LevyGenerationInput(
                    building_id="16244",
                    quarter="2025-Q4",
                    budget_year="2025",
                    is_test_data=True,
                )
                await env.client.execute_workflow(
                    GenerateLeviesWorkflow.run,
                    inp,
                    id="levygen:16244:2025-Q4",
                    task_queue="test-levy-gen-5",
                )

        assert seen_building_ids == ["16244"]
        assert "13195" not in seen_building_ids
