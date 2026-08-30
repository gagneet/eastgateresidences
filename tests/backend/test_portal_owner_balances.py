import pytest

from utils.portal_owner_balances import PORTAL_ARREARS_TOTAL_2026
from utils.portal_owner_balances import PORTAL_CREDIT_TOTAL_2026
from utils.portal_owner_balances import build_owner_balance_records
from utils.portal_owner_balances import parse_portal_outstanding
from utils.portal_owner_balances import summarize_owner_balances


def test_parse_portal_outstanding_supports_credit_marker():
    assert parse_portal_outstanding("$1,558.44 (CR)") == pytest.approx(-1558.44, abs=0.01)
    assert parse_portal_outstanding("$27.49 (CR)") == pytest.approx(-27.49, abs=0.01)


def test_parse_portal_outstanding_supports_standard_arrears():
    assert parse_portal_outstanding("$1,056.77") == pytest.approx(1056.77, abs=0.01)
    assert parse_portal_outstanding("$0.00") == pytest.approx(0.0, abs=0.01)


def test_current_portal_snapshot_matches_user_totals():
    summary = summarize_owner_balances(build_owner_balance_records())
    assert summary["arrears_total"] == pytest.approx(PORTAL_ARREARS_TOTAL_2026, abs=0.01)
    assert summary["credit_total"] == pytest.approx(PORTAL_CREDIT_TOTAL_2026, abs=0.01)
    assert summary["net_total"] == pytest.approx(PORTAL_ARREARS_TOTAL_2026 - PORTAL_CREDIT_TOTAL_2026, abs=0.01)


def test_current_portal_snapshot_matches_key_units():
    records = {record["lot"]: record for record in build_owner_balance_records()}

    assert len(records) == 87

    assert records[32]["balance"] == pytest.approx(-63.35, abs=0.01)
    assert records[32]["status"] == "CREDIT"

    assert records[42]["balance"] == pytest.approx(949.37, abs=0.01)
    assert records[42]["status"] == "ARREARS"

    assert records[73]["balance"] == pytest.approx(0.0, abs=0.01)
    assert records[73]["status"] == "CLEAR"

    assert records[76]["balance"] == pytest.approx(-1558.44, abs=0.01)
    assert records[76]["status"] == "CREDIT"
