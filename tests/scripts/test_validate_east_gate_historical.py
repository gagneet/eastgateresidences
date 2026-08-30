import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent.parent
MODULE_PATH = ROOT / "scripts" / "validation" / "validate_east_gate_historical.py"

spec = importlib.util.spec_from_file_location("validate_east_gate_historical", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def _annual_docs():
    return [
        {
            "year": "2024",
            "fund_type": "admin",
            "levy_income": "100.00",
            "opening_balance": "10.00",
            "closing_balance": "20.00",
        },
        {
            "year": "2025",
            "fund_type": "admin",
            "levy_income": "120.00",
            "opening_balance": "20.00",
            "closing_balance": "40.00",
        },
        {
            "year": "2024",
            "fund_type": "sinking",
            "levy_income": "50.00",
            "opening_balance": "0.00",
            "closing_balance": "15.00",
        },
        {
            "year": "2025",
            "fund_type": "sinking",
            "levy_income": "60.00",
            "opening_balance": "15.00",
            "closing_balance": "35.00",
        },
    ]


def _issuance_docs():
    return [
        {"year": "2024", "admin_levy_amount": "40.00", "sinking_levy_amount": "20.00"},
        {"year": "2024", "admin_levy_amount": "60.00", "sinking_levy_amount": "30.00"},
        {"year": "2025", "admin_levy_amount": "50.00", "sinking_levy_amount": "25.00"},
        {"year": "2025", "admin_levy_amount": "70.00", "sinking_levy_amount": "35.00"},
    ]


def _snapshot_docs():
    return [
        {
            "snapshot_type": "arrears_2025_12_31",
            "admin_arrears": "10.00",
            "sinking_arrears": "5.00",
            "total_arrears": "15.00",
        },
        {
            "snapshot_type": "outstanding_2026_06_01",
            "admin_outstanding": "12.00",
            "sinking_outstanding": "8.00",
            "total_outstanding": "20.00",
        },
    ]


def _opening_balances():
    return [
        {
            "fund_type": "admin",
            "as_at_date": "2026-04-06",
            "evidence_source": "statement",
            "opening_balance_amount": "17778.11",
        },
        {
            "fund_type": "sinking",
            "as_at_date": "2026-04-06",
            "evidence_source": "statement",
            "opening_balance_amount": "156351.48",
        },
    ]


def test_validation_passes_on_consistent_data():
    annual_rows, annual_failures = mod.build_annual_reconciliation(_annual_docs(), _issuance_docs())
    snapshot_rows, snapshot_failures = mod.build_snapshot_reconciliation(_snapshot_docs())
    genesis_rows, genesis_failures = mod.build_genesis_preview(_opening_balances())

    assert len(annual_rows) == 4
    assert len(snapshot_rows) == 2
    assert len(genesis_rows) == 2
    assert annual_failures == []
    assert snapshot_failures == []
    assert genesis_failures == []


def test_validation_fails_on_drift():
    drifted = _annual_docs()
    drifted[1] = {**drifted[1], "levy_income": "119.00"}

    _, annual_failures = mod.build_annual_reconciliation(drifted, _issuance_docs())

    assert annual_failures
    assert "admin 2025" in annual_failures[0]


def test_report_format(tmp_path):
    context = mod.ValidationContext(
        building_id="13195",
        tenant_id="tenant-1",
        scheme_id="scheme-1",
        scheme_name="East Gate Residences",
        session_id="session-1",
        report_path=tmp_path / "east_gate_validation_report.md",
    )
    annual_rows, _ = mod.build_annual_reconciliation(_annual_docs(), _issuance_docs())
    snapshot_rows, _ = mod.build_snapshot_reconciliation(_snapshot_docs())
    genesis_rows, _ = mod.build_genesis_preview(_opening_balances())

    report = mod.render_report(
        context,
        {
            "historical_annual_levies": 4,
            "historical_levy_issuances": 4,
            "historical_financial_snapshots": 2,
        },
        annual_rows,
        snapshot_rows,
        genesis_rows,
        [],
    )

    assert "# East Gate historical validation report" in report
    assert "## Per-fund per-year reconciliation" in report
    assert "## Snapshot reconciliation" in report
    assert "## Genesis journal preview" in report
    assert "| Year | Fund | Levy income |" in report
    assert "| Fund | As at | Evidence source |" in report
