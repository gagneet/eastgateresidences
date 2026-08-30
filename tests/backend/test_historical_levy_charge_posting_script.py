"""Unit tests for scripts/historical_levy_charge_posting.py.

run_apply()'s dual-control/status/integrity guard clauses all execute BEFORE any Postgres
session is opened, so they're testable by patching only the Mongo client + the two batch/
manifest lookup helpers — no real database connection required. _group_charge_lines() is a
pure function, tested directly.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.historical_levy_charge_posting import _group_charge_lines, run_apply

_MODULE = "scripts.historical_levy_charge_posting"

BUILDING_ID = "16244"
BATCH_ID = str(uuid.uuid4())
APPROVED_BY = uuid.uuid4()
EXECUTED_BY = uuid.uuid4()


def _line(**overrides) -> dict:
    defaults = dict(
        unit_number="TH001", lot_id=str(uuid.uuid4()), owner_party_id=str(uuid.uuid4()),
        owner_resolution_status="resolved", financial_year="2024", fund_type="admin",
        levy_type="ordinary", period="Q1", quarter_no=1, issue_date="2024-03-31",
        due_date="2024-03-31", uoe=100.0, total_uoe=100.0, principal_cents=10000,
        gst_cents=1000, total_cents=11000, source_annual_control="annual_levies.proposed_admin_expenses (year=2024)",
        derivation_type="document_derived_levy_charge", idempotency_key="k1",
    )
    defaults.update(overrides)
    return defaults


class TestGroupChargeLines:
    def test_groups_by_year_fund_levy_type_period(self):
        lines = [
            _line(unit_number="TH001", period="Q1"),
            _line(unit_number="TH002", period="Q1"),
            _line(unit_number="TH001", period="Q2"),
            _line(unit_number="TH001", fund_type="sinking", period="Q1"),
        ]
        groups = _group_charge_lines(lines)
        assert len(groups) == 3  # (admin,Q1)x2-lots, (admin,Q2), (sinking,Q1)
        assert len(groups[("2024", "admin", "ordinary", "Q1")]) == 2
        assert len(groups[("2024", "admin", "ordinary", "Q2")]) == 1
        assert len(groups[("2024", "sinking", "ordinary", "Q1")]) == 1


def _mock_mongo(monkeypatch, batch_doc, manifest_doc=None):
    mongo_db = MagicMock()
    mock_client = MagicMock()
    mock_client.__getitem__.return_value = mongo_db
    monkeypatch.setattr(f"{_MODULE}.AsyncIOMotorClient", lambda *a, **k: mock_client)
    monkeypatch.setattr(f"{_MODULE}._mongo_url_and_db", lambda: ("mongodb://fake", "fake_db"))
    monkeypatch.setattr(f"{_MODULE}._get_batch_doc", AsyncMock(return_value=batch_doc))
    if manifest_doc is not None:
        monkeypatch.setattr(f"{_MODULE}._get_latest_manifest_doc", AsyncMock(return_value=manifest_doc))
    return mongo_db


class TestRunApplyGuardClauses:
    @pytest.mark.asyncio
    async def test_refuses_unapproved_batch(self, monkeypatch):
        _mock_mongo(monkeypatch, batch_doc={"status": "extracted", "batch_id": BATCH_ID})
        with pytest.raises(SystemExit, match="requires 'approved'"):
            await run_apply(building_id=BUILDING_ID, batch_id=BATCH_ID, executed_by=EXECUTED_BY, is_test_data=False)

    @pytest.mark.asyncio
    async def test_refuses_missing_approved_by(self, monkeypatch):
        _mock_mongo(monkeypatch, batch_doc={"status": "approved", "batch_id": BATCH_ID, "approved_by": None})
        with pytest.raises(SystemExit, match="no approved_by"):
            await run_apply(building_id=BUILDING_ID, batch_id=BATCH_ID, executed_by=EXECUTED_BY, is_test_data=False)

    @pytest.mark.asyncio
    async def test_refuses_same_approver_and_executor(self, monkeypatch):
        _mock_mongo(monkeypatch, batch_doc={
            "status": "approved", "batch_id": BATCH_ID, "approved_by": str(EXECUTED_BY),
        })
        with pytest.raises(SystemExit, match="dual control"):
            await run_apply(building_id=BUILDING_ID, batch_id=BATCH_ID, executed_by=EXECUTED_BY, is_test_data=False)

    @pytest.mark.asyncio
    async def test_refuses_stale_manifest_hash(self, monkeypatch):
        _mock_mongo(
            monkeypatch,
            batch_doc={
                "status": "approved", "batch_id": BATCH_ID, "approved_by": str(APPROVED_BY),
                "manifest_hash": "current-hash",
            },
            manifest_doc={"manifest_hash": "stale-hash", "levy_charge_lines": [_line()]},
        )
        with pytest.raises(SystemExit, match="Manifest has changed"):
            await run_apply(building_id=BUILDING_ID, batch_id=BATCH_ID, executed_by=EXECUTED_BY, is_test_data=False)

    @pytest.mark.asyncio
    async def test_refuses_when_manifest_disagrees_with_its_own_recorded_total(self, monkeypatch):
        _mock_mongo(
            monkeypatch,
            batch_doc={
                "status": "approved", "batch_id": BATCH_ID, "approved_by": str(APPROVED_BY),
                "manifest_hash": "h",
            },
            manifest_doc={
                "manifest_hash": "h",
                "levy_charge_lines": [_line(total_cents=11000)],
                "calculation_configuration": {"levy_charge_total_cents": 999999},
            },
        )
        with pytest.raises(SystemExit, match="integrity check failed"):
            await run_apply(building_id=BUILDING_ID, batch_id=BATCH_ID, executed_by=EXECUTED_BY, is_test_data=False)

    @pytest.mark.asyncio
    async def test_no_charge_lines_is_a_clean_noop(self, monkeypatch, capsys):
        _mock_mongo(
            monkeypatch,
            batch_doc={
                "status": "approved", "batch_id": BATCH_ID, "approved_by": str(APPROVED_BY),
                "manifest_hash": "h",
            },
            manifest_doc={"manifest_hash": "h", "levy_charge_lines": []},
        )
        await run_apply(building_id=BUILDING_ID, batch_id=BATCH_ID, executed_by=EXECUTED_BY, is_test_data=False)
        assert "nothing to post" in capsys.readouterr().out.lower()
