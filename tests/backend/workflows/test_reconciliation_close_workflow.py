"""
Tests for workflows/reconciliation_close_workflow.py — ReconciliationCloseWorkflow.

Uses temporalio.testing.WorkflowEnvironment (time-skipping). Signal tests
send the `reviewer_approved` signal externally to unblock the workflow.
"""
from __future__ import annotations

import asyncio
import pytest
from typing import Any, Dict, Optional

import temporalio.activity
import temporalio.testing
import temporalio.worker

from workflows.reconciliation_close_workflow import (
    ReconciliationCloseInput,
    ReconciliationCloseWorkflow,
    close_period_activity,
    notify_reviewer_activity,
    run_reconciliation_check,
)


# ── Mock activity implementations ─────────────────────────────────────────────

@temporalio.activity.defn(name="run_reconciliation_check")
async def mock_run_reconciliation_check_balanced(inp: ReconciliationCloseInput) -> Dict[str, Any]:
    return {
        "balanced": True,
        "journal_total_cents": inp.bank_statement_total_cents,
        "bank_total_cents": inp.bank_statement_total_cents,
        "discrepancy_cents": 0,
    }


@temporalio.activity.defn(name="run_reconciliation_check")
async def mock_run_reconciliation_check_discrepancy(inp: ReconciliationCloseInput) -> Dict[str, Any]:
    return {
        "balanced": False,
        "journal_total_cents": inp.bank_statement_total_cents - 500,
        "bank_total_cents": inp.bank_statement_total_cents,
        "discrepancy_cents": 500,
    }


@temporalio.activity.defn(name="notify_reviewer_activity")
async def mock_notify_reviewer_activity(inp: ReconciliationCloseInput, discrepancy_cents: int) -> None:
    pass  # Email is a side effect — tested separately


@temporalio.activity.defn(name="close_period_activity")
async def mock_close_period_activity(inp: ReconciliationCloseInput) -> Dict[str, Any]:
    return {
        "period_id": inp.period_id,
        "building_id": inp.building_id,
        "closed_at": "2026-04-26T00:00:00+00:00",
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestReconciliationCloseWorkflowBalanced:
    @pytest.mark.asyncio
    async def test_balanced_period_closes_without_signal(self):
        """When journal == bank statement, workflow closes the period directly."""
        async with await temporalio.testing.WorkflowEnvironment.start_time_skipping() as env:
            async with temporalio.worker.Worker(
                    env.client,
                    task_queue="test-recon-1",
                    workflows=[ReconciliationCloseWorkflow],
                    activities=[
                        mock_run_reconciliation_check_balanced,
                        mock_notify_reviewer_activity,
                        mock_close_period_activity,
                    ],
            ):
                inp = ReconciliationCloseInput(
                    building_id="16244",
                    period_id="period001",
                    bank_statement_total_cents=1_500_000,
                    is_test_data=True,
                )
                result = await env.client.execute_workflow(
                    ReconciliationCloseWorkflow.run,
                    inp,
                    id="recon-close:16244:period001",
                    task_queue="test-recon-1",
                )

        assert result["balanced"] is True
        assert result["discrepancy_cents"] == 0
        assert result["reviewer_approved"] is False  # Signal not needed
        assert result["closed_at"] is not None

    @pytest.mark.asyncio
    async def test_result_contains_building_and_period(self):
        async with await temporalio.testing.WorkflowEnvironment.start_time_skipping() as env:
            async with temporalio.worker.Worker(
                    env.client,
                    task_queue="test-recon-2",
                    workflows=[ReconciliationCloseWorkflow],
                    activities=[
                        mock_run_reconciliation_check_balanced,
                        mock_notify_reviewer_activity,
                        mock_close_period_activity,
                    ],
            ):
                inp = ReconciliationCloseInput(
                    building_id="13195",
                    period_id="period_abc",
                    bank_statement_total_cents=500_000,
                    is_test_data=True,
                )
                result = await env.client.execute_workflow(
                    ReconciliationCloseWorkflow.run,
                    inp,
                    id="recon-close:13195:period_abc",
                    task_queue="test-recon-2",
                )

        assert result["building_id"] == "13195"
        assert result["period_id"] == "period_abc"


class TestReconciliationCloseWorkflowDiscrepancy:
    @pytest.mark.asyncio
    async def test_discrepancy_blocks_until_signal(self):
        """When discrepancy detected, workflow waits for reviewer_approved signal."""
        async with await temporalio.testing.WorkflowEnvironment.start_time_skipping() as env:
            async with temporalio.worker.Worker(
                    env.client,
                    task_queue="test-recon-3",
                    workflows=[ReconciliationCloseWorkflow],
                    activities=[
                        mock_run_reconciliation_check_discrepancy,
                        mock_notify_reviewer_activity,
                        mock_close_period_activity,
                    ],
            ):
                inp = ReconciliationCloseInput(
                    building_id="16244",
                    period_id="period002",
                    bank_statement_total_cents=1_500_000,
                    reviewer_email="manager@test.com",
                    is_test_data=True,
                )
                handle = await env.client.start_workflow(
                    ReconciliationCloseWorkflow.run,
                    inp,
                    id="recon-close:16244:period002",
                    task_queue="test-recon-3",
                )

                # Give workflow time to block on wait_condition
                await asyncio.sleep(0.1)

                # Send the reviewer_approved signal
                await handle.signal(ReconciliationCloseWorkflow.reviewer_approved, "Looks OK after review")

                result = await handle.result()

        assert result["balanced"] is False
        assert result["discrepancy_cents"] == 500
        assert result["reviewer_approved"] is True
        assert result["reviewer_notes"] == "Looks OK after review"
        assert result["closed_at"] is not None

    @pytest.mark.asyncio
    async def test_notify_reviewer_called_on_discrepancy(self):
        """notify_reviewer_activity must be called when there is a discrepancy."""
        notify_calls: list = []

        @temporalio.activity.defn(name="notify_reviewer_activity")
        async def tracking_notify(inp: ReconciliationCloseInput, discrepancy_cents: int) -> None:
            notify_calls.append({"building_id": inp.building_id, "discrepancy": discrepancy_cents})

        async with await temporalio.testing.WorkflowEnvironment.start_time_skipping() as env:
            async with temporalio.worker.Worker(
                    env.client,
                    task_queue="test-recon-4",
                    workflows=[ReconciliationCloseWorkflow],
                    activities=[
                        mock_run_reconciliation_check_discrepancy,
                        tracking_notify,
                        mock_close_period_activity,
                    ],
            ):
                inp = ReconciliationCloseInput(
                    building_id="16244",
                    period_id="period003",
                    bank_statement_total_cents=2_000_000,
                    is_test_data=True,
                )
                handle = await env.client.start_workflow(
                    ReconciliationCloseWorkflow.run,
                    inp,
                    id="recon-close:16244:period003",
                    task_queue="test-recon-4",
                )
                await asyncio.sleep(0.1)
                await handle.signal(ReconciliationCloseWorkflow.reviewer_approved)
                await handle.result()

        assert len(notify_calls) == 1
        assert notify_calls[0]["building_id"] == "16244"
        assert notify_calls[0]["discrepancy"] == 500

    @pytest.mark.asyncio
    async def test_notify_reviewer_not_called_when_balanced(self):
        """notify_reviewer_activity must NOT be called when balanced."""
        notify_calls: list = []

        @temporalio.activity.defn(name="notify_reviewer_activity")
        async def tracking_notify(inp: ReconciliationCloseInput, discrepancy_cents: int) -> None:
            notify_calls.append(discrepancy_cents)

        async with await temporalio.testing.WorkflowEnvironment.start_time_skipping() as env:
            async with temporalio.worker.Worker(
                    env.client,
                    task_queue="test-recon-5",
                    workflows=[ReconciliationCloseWorkflow],
                    activities=[
                        mock_run_reconciliation_check_balanced,
                        tracking_notify,
                        mock_close_period_activity,
                    ],
            ):
                inp = ReconciliationCloseInput(
                    building_id="13195",
                    period_id="period004",
                    bank_statement_total_cents=750_000,
                    is_test_data=True,
                )
                await env.client.execute_workflow(
                    ReconciliationCloseWorkflow.run,
                    inp,
                    id="recon-close:13195:period004",
                    task_queue="test-recon-5",
                )

        assert notify_calls == []
