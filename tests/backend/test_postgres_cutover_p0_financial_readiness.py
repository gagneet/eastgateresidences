"""Tests for finance readiness gate evaluation."""

# @featuretrace:financial-onboarding — Readiness gate tests for approved evidence, genesis, and cutover config.
# Layer: test
# Data flow: readiness evaluator → finance.evidence_documents / finance.journal_entries / finance.financial_onboarding_audit / finance.financial_cutover_config (building-scoped).
# Related: backend/scripts/postgres_cutover_p0_readiness.py
#          backend/services/onboarding/financial_onboarding.py

from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "backend" / "scripts" / "postgres_cutover_p0_readiness.py"


def _load_module():
    module_name = f"test_postgres_cutover_p0_financial_readiness_{uuid.uuid4().hex}"
    previous = sys.modules.get(module_name)
    try:
        spec = importlib.util.spec_from_file_location(module_name, _SCRIPT_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous


def _base_snapshot() -> dict[str, object]:
    return {
        "matching_schemes": [{"scheme_id": str(uuid.uuid4()), "tenant_id": str(uuid.uuid4())}],
        "evidence_rows": [
            {
                "document_id": str(uuid.uuid4()),
                "building_id": "13195",
                "scheme_id": str(uuid.uuid4()),
                "sha256_hash": "a" * 64,
                "approved_by": str(uuid.uuid4()),
                "approved_at": "2026-06-04T00:00:00Z",
                "declared_total_cents": 125000,
                "declared_totals_by_fund": {"admin": 50000, "sinking": 75000},
                "uploaded_by": str(uuid.uuid4()),
                "document_type": "bank_statement",
            }
        ],
        "fund_rows": [
            {"fund_id": str(uuid.uuid4()), "building_id": "13195", "scheme_id": str(uuid.uuid4()), "fund_code": "ADMIN", "fund_type": "admin", "status": "active"},
            {"fund_id": str(uuid.uuid4()), "building_id": "13195", "scheme_id": str(uuid.uuid4()), "fund_code": "SINK", "fund_type": "sinking", "status": "active"},
        ],
        "gl_rows": [
            {"gl_account_id": str(uuid.uuid4()), "building_id": "13195", "scheme_id": str(uuid.uuid4()), "account_code": "1000", "account_type": "asset", "status": "active"},
            {"gl_account_id": str(uuid.uuid4()), "building_id": "13195", "scheme_id": str(uuid.uuid4()), "account_code": "3100", "account_type": "equity", "status": "active"},
        ],
        "journal_entries": [
            {
                "journal_entry_id": "entry-1",
                "building_id": "13195",
                "scheme_id": str(uuid.uuid4()),
                "fund_id": str(uuid.uuid4()),
                "entry_number": 1,
                "evidence_document_id": str(uuid.uuid4()),
                "approved_by": str(uuid.uuid4()),
                "posted_by": str(uuid.uuid4()),
                "prev_entry_hash": None,
                "entry_hash": "hash-1",
                "status": "posted",
                "source_type": "genesis",
            }
        ],
        "journal_lines": [
            {"journal_entry_id": "entry-1", "direction": "debit", "amount_cents": 50000},
            {"journal_entry_id": "entry-1", "direction": "credit", "amount_cents": 50000},
        ],
        "audit_rows": [
            {
                "audit_id": str(uuid.uuid4()),
                "building_id": "13195",
                "scheme_id": str(uuid.uuid4()),
                "approved_by": str(uuid.uuid4()),
                "evidence_document_id": str(uuid.uuid4()),
                "evidence_sha256_hash": "a" * 64,
                "cutover_date": "2026-07-01",
                "opening_balances": [],
            }
        ],
        "cutover_rows": [
            {
                "config_id": str(uuid.uuid4()),
                "building_id": "13195",
                "scheme_id": str(uuid.uuid4()),
                "cutover_date": "2026-07-01",
                "onboarded": True,
                "approved_by": str(uuid.uuid4()),
                "evidence_document_id": str(uuid.uuid4()),
                "journal_entry_ids": ["entry-1"],
                "metadata": {"strategy": "clean_slate", "readiness_only": True, "mode": "mongo_primary", "read_source": "mongo", "write_source": "mongo"},
            }
        ],
    }


@pytest.mark.parametrize(
    ("mutator", "needle"),
    [
        (lambda s: s["evidence_rows"].clear(), "missing evidence"),
        (lambda s: s["evidence_rows"][0].update({"approved_by": None}), "missing approved_by"),
        (lambda s: s["fund_rows"].pop(), "missing funds"),
        (lambda s: s["gl_rows"].pop(), "missing GL accounts"),
        (lambda s: s["journal_entries"].clear(), "missing genesis journal entries"),
        (lambda s: s["journal_lines"].clear(), "missing journal lines"),
        (lambda s: s["journal_entries"][0].update({"approved_by": None}), "missing approved_by"),
        (lambda s: s["journal_entries"][0].update({"posted_by": None}), "missing posted_by"),
        (lambda s: s["journal_entries"][0].update({"prev_entry_hash": "broken"}), "broken hash chain"),
        (lambda s: s["journal_lines"].__setitem__(0, {"journal_entry_id": "entry-1", "direction": "debit", "amount_cents": 10}), "unbalanced"),
        (lambda s: s["audit_rows"].clear(), "missing onboarding audit"),
        (lambda s: s["cutover_rows"][0]["metadata"].update({"readiness_only": False}), "readiness-only"),
    ],
)
def test_financial_readiness_failures(mutator, needle):
    module = _load_module()
    snapshot = _base_snapshot()
    mutator(snapshot)
    results = module.evaluate_financial_onboarding_snapshot("13195", snapshot)
    assert any(result.status == "fail" and needle in " ".join(result.details.get("failures", [])) for result in results)


def test_financial_readiness_complete_fixture_passes():
    module = _load_module()
    snapshot = _base_snapshot()
    results = module.evaluate_financial_onboarding_snapshot("13195", snapshot)
    assert all(result.status == "pass" for result in results)


def test_financial_readiness_wrong_building_fails():
    module = _load_module()
    snapshot = _base_snapshot()
    snapshot["matching_schemes"] = []
    results = module.evaluate_financial_onboarding_snapshot("16244", snapshot)
    assert any(result.status == "fail" for result in results)
