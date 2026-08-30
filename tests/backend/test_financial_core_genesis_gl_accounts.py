import sys
from pathlib import Path


BACKEND_PATH = str(Path(__file__).resolve().parents[2] / "backend")
if BACKEND_PATH not in sys.path:
    sys.path.insert(0, BACKEND_PATH)


def test_genesis_seeds_gl_accounts_required_by_financial_core_writers():
    from services.financial_core.genesis import DEFAULT_FUND_GL_ACCOUNTS, DEFAULT_GL_ACCOUNTS

    general_codes = {account[0] for account in DEFAULT_GL_ACCOUNTS}
    fund_accounts = {
        (fund_type, account_code): (account_name, account_type)
        for fund_type, account_code, account_name, account_type, _is_control in DEFAULT_FUND_GL_ACCOUNTS
    }

    assert "4000" in general_codes
    assert "4003" in general_codes
    assert fund_accounts[("sinking", "4001")] == ("Sinking Levy Income", "income")
    assert fund_accounts[("sinking", "5100")] == ("Sinking Fund Expenses", "expense")
    assert fund_accounts[("admin", "5000")] == ("Administration Expenses", "expense")
    assert fund_accounts[("admin", "5001")] == ("Insurance", "expense")
    assert fund_accounts[("admin", "5002")] == ("Repairs and Maintenance", "expense")
    assert fund_accounts[("admin", "5003")] == ("Management Fees", "expense")
    assert fund_accounts[("admin", "5004")] == ("Legal and Compliance", "expense")
