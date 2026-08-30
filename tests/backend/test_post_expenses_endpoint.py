"""Tests for routers/financial_onboarding.py::post_reconstruction_batch_expenses — the in-app,
dual-control endpoint that posts an approved+synced batch's expense (debit) lines to the GL via the
shared expense_posting_service core. The endpoint function is invoked directly with mocked
dependencies (the repo's standard pattern for router unit tests).
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from routers.financial_onboarding import post_reconstruction_batch_expenses
from services.reconstruction_generators.expense_posting_service import ExpensePostingResult

BUILDING_ID = "16244"
BATCH_ID = str(uuid.uuid4())
APPROVED_BY = uuid.uuid4()
EXECUTOR_ID = uuid.uuid4()  # distinct from APPROVED_BY -> dual control satisfied

_SUPER_ADMIN = {
    "id": str(EXECUTOR_ID), "role": "super_admin", "effective_role": "super_admin",
    "full_name": "Test Operator",
}

_R = "routers.financial_onboarding"
_RBS = "services.reconstruction_batch_service"


def _batch_doc(**overrides) -> dict:
    doc = {
        "batch_id": BATCH_ID, "building_id": BUILDING_ID, "status": "matching",
        "approved_by": str(APPROVED_BY), "reviewed_by": str(uuid.uuid4()),
        "manifest_hash": "h", "generated_debit_count": 2, "generated_credit_count": 1,
        "is_test_data": False,
    }
    doc.update(overrides)
    return doc


def _patched_env(monkeypatch, *, batch_docs, feature_on=True):
    """Wire up the shared happy-path mocks; `batch_docs` is the side_effect list for
    rbs._get_batch_doc (call 1 = initial, call 2 = post-posting current_now check)."""
    monkeypatch.setattr(f"{_R}._assert_building_access", AsyncMock(return_value=BUILDING_ID))
    monkeypatch.setattr(f"{_R}.get_effective_feature_access", AsyncMock(return_value=feature_on))
    mongo_db = MagicMock()
    mongo_db.demo_bank_reconstruction_batches.update_one = AsyncMock()
    monkeypatch.setattr(f"{_R}._mongo", lambda _bid: mongo_db)
    monkeypatch.setattr(f"{_R}.create_audit_log", AsyncMock())
    monkeypatch.setattr(f"{_RBS}._get_batch_doc", AsyncMock(side_effect=batch_docs))
    monkeypatch.setattr(f"{_RBS}._now", lambda: "2026-08-03T00:00:00Z")
    return mongo_db


@pytest.mark.asyncio
async def test_rejects_batch_not_yet_synced(monkeypatch):
    _patched_env(monkeypatch, batch_docs=[_batch_doc(status="approved")])
    with pytest.raises(HTTPException) as exc:
        await post_reconstruction_batch_expenses(
            building_id=BUILDING_ID, batch_id=BATCH_ID,
            current_user=_SUPER_ADMIN, current_building=BUILDING_ID, _feature={},
        )
    assert exc.value.status_code == 409
    assert "synced" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_rejects_batch_with_no_debit_lines(monkeypatch):
    _patched_env(monkeypatch, batch_docs=[_batch_doc(generated_debit_count=0)])
    with pytest.raises(HTTPException) as exc:
        await post_reconstruction_batch_expenses(
            building_id=BUILDING_ID, batch_id=BATCH_ID,
            current_user=_SUPER_ADMIN, current_building=BUILDING_ID, _feature={},
        )
    assert exc.value.status_code == 409
    assert "no expense (debit) lines" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_rejects_dual_control_violation(monkeypatch):
    # Executor is the same person who approved the batch.
    _patched_env(monkeypatch, batch_docs=[_batch_doc(approved_by=str(EXECUTOR_ID))])
    with pytest.raises(HTTPException) as exc:
        await post_reconstruction_batch_expenses(
            building_id=BUILDING_ID, batch_id=BATCH_ID,
            current_user=_SUPER_ADMIN, current_building=BUILDING_ID, _feature={},
        )
    assert exc.value.status_code == 409
    assert "dual control" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_rejects_when_feature_disabled(monkeypatch):
    _patched_env(monkeypatch, batch_docs=[_batch_doc()], feature_on=False)
    with pytest.raises(HTTPException) as exc:
        await post_reconstruction_batch_expenses(
            building_id=BUILDING_ID, batch_id=BATCH_ID,
            current_user=_SUPER_ADMIN, current_building=BUILDING_ID, _feature={},
        )
    assert exc.value.status_code == 403
    assert "financial_integration_layer_v2" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_non_super_admin_forbidden(monkeypatch):
    _patched_env(monkeypatch, batch_docs=[_batch_doc()])
    manager = {"id": str(EXECUTOR_ID), "role": "strata_manager", "effective_role": "strata_manager"}
    with pytest.raises(HTTPException) as exc:
        await post_reconstruction_batch_expenses(
            building_id=BUILDING_ID, batch_id=BATCH_ID,
            current_user=manager, current_building=BUILDING_ID, _feature={},
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_happy_path_posts_and_marks_partially_posted(monkeypatch):
    """A synced batch that still has income (credit) rows lands 'partially_posted' (the income
    match queue is still outstanding), records reconciliation evidence, and audit-logs."""
    mongo_db = _patched_env(
        monkeypatch,
        batch_docs=[_batch_doc(status="matching"), {"status": "matching"}],
    )
    posted_batch = MagicMock()
    posted_batch.model_dump.return_value = {"status": "partially_posted"}
    transition = AsyncMock(return_value=posted_batch)
    monkeypatch.setattr(f"{_RBS}._transition", transition)

    result = ExpensePostingResult(
        line_count=2, posted=2, replayed=0, posted_total_cents=33000,
        expected_total_cents=33000, per_fund_cents={"admin": 11000, "sinking": 22000},
        approved_by=str(APPROVED_BY), executed_by=str(EXECUTOR_ID),
    )
    monkeypatch.setattr(f"{_R}.run_expense_posting", AsyncMock(return_value=result))

    resp = await post_reconstruction_batch_expenses(
        building_id=BUILDING_ID, batch_id=BATCH_ID,
        current_user=_SUPER_ADMIN, current_building=BUILDING_ID, _feature={},
    )

    # Transitioned matching -> partially_posted, carrying reconciliation evidence.
    assert transition.await_count == 1
    _, kwargs = transition.await_args
    assert kwargs["to_status"] == "partially_posted"
    assert kwargs["extra"]["expenses_posted_total_cents"] == 33000
    assert kwargs["extra"]["expenses_per_fund_cents"] == {"admin": 11000, "sinking": 22000}
    assert resp["result"]["posted"] == 2
    assert resp["batch"] == {"status": "partially_posted"}


@pytest.mark.asyncio
async def test_synced_batch_first_transitions_to_matching(monkeypatch):
    """A freshly-synced batch is advanced synced -> matching before posting, then -> posted when it
    has no income rows to match."""
    mongo_db = _patched_env(
        monkeypatch,
        batch_docs=[
            _batch_doc(status="synced", generated_credit_count=0),  # initial
            {"status": "matching"},  # current_now after posting
        ],
    )
    posted_batch = MagicMock()
    posted_batch.model_dump.return_value = {"status": "posted"}
    transition = AsyncMock(return_value=posted_batch)
    monkeypatch.setattr(f"{_RBS}._transition", transition)
    monkeypatch.setattr(
        f"{_R}.run_expense_posting",
        AsyncMock(return_value=ExpensePostingResult(
            line_count=2, posted=2, posted_total_cents=33000, expected_total_cents=33000,
        )),
    )

    await post_reconstruction_batch_expenses(
        building_id=BUILDING_ID, batch_id=BATCH_ID,
        current_user=_SUPER_ADMIN, current_building=BUILDING_ID, _feature={},
    )

    # Two transitions: synced->matching, then matching->posted (no income rows).
    statuses = [c.kwargs["to_status"] for c in transition.await_args_list]
    assert statuses == ["matching", "posted"]
