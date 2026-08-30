from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "alembic"
    / "versions"
    / "0087_sinking_expense_gl_alignment.py"
)
MIGRATION_0088_PATH = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "alembic"
    / "versions"
    / "0088_backfill_fund_expense_gl_accounts.py"
)
MIGRATION_0089_PATH = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "alembic"
    / "versions"
    / "0089_capital_works_income_gl.py"
)
MIGRATION_0090_PATH = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "alembic"
    / "versions"
    / "0090_align_gl_account_fund_ids.py"
)


def test_0087_creates_sinking_expense_account_and_repairs_gl_classification_only():
    sql = MIGRATION_PATH.read_text()

    assert "Revision ID: 0087_sinking_expense_gl" in sql
    assert 'down_revision = "0086_outbox_per_dest_delivery"' in sql
    assert '_SINKING_EXPENSE_CODE = "5100"' in sql
    assert '_SINKING_EXPENSE_NAME = "Sinking Fund Expenses"' in sql
    assert "INSERT INTO finance.gl_accounts" in sql
    assert "CAST('expense' AS finance.account_type)" in sql
    assert "f.fund_type::text IN ('sinking', 'capital_works')" in sql

    assert "ALTER TABLE finance.journal_lines DISABLE TRIGGER trg_prevent_posted_line_update" in sql
    assert "ALTER TABLE finance.journal_lines ENABLE TRIGGER trg_prevent_posted_line_update" in sql
    assert "UPDATE finance.journal_lines jl" in sql
    assert "SET gl_account_id = candidates.target_gl_account_id" in sql
    assert "COALESCE(et.fund_id, je.fund_id)" in sql
    assert "current_ga.fund_id IS DISTINCT FROM ef.fund_id" in sql

    assert "UPDATE finance.expense_transactions et" in sql
    assert "SET gl_account_id = ga.gl_account_id" in sql

    assert "SET amount_cents" not in sql
    assert "SET gst_cents" not in sql
    assert "SET direction" not in sql
    assert "DELETE FROM finance.journal_lines" not in sql
    assert "DELETE FROM finance.journal_entries" not in sql


def test_0088_backfills_all_writer_required_fund_gl_accounts_without_destructive_downgrade():
    sql = MIGRATION_0088_PATH.read_text()

    assert "Revision ID: 0088_fund_gl_accounts" in sql
    assert 'down_revision = "0087_sinking_expense_gl"' in sql
    for account_code in ("4001", "5000", "5001", "5002", "5003", "5004", "5100"):
        assert account_code in sql
    assert "INSERT INTO finance.gl_accounts" in sql
    assert "f.fund_type::text = :fund_type" in sql
    assert "WHERE ga.scheme_id = CAST(:scheme_id AS UUID)" in sql
    assert "AND ga.account_code = :account_code" in sql
    assert "def downgrade() -> None:" in sql
    assert "pass" in sql
    assert "DELETE FROM finance.gl_accounts" not in sql


def test_0090_aligns_existing_writer_required_gl_account_fund_ids_non_destructively():
    sql = MIGRATION_0090_PATH.read_text()

    assert "Revision ID: 0090_align_gl_fund_ids" in sql
    assert 'down_revision = "0089_capital_works_income_gl"' in sql
    for account_code in ("4000", "4003", "5000", "5001", "5002", "5003", "5004", "4001", "5100"):
        assert account_code in sql
    assert "UPDATE finance.gl_accounts ga" in sql
    assert "f.fund_type::text = 'admin'" in sql
    assert "f.fund_type::text IN ('sinking', 'capital_works')" in sql
    assert "ga.fund_id IS DISTINCT FROM" in sql
    assert "def downgrade() -> None:" in sql
    assert "pass" in sql
    assert "DELETE FROM finance.gl_accounts" not in sql
    assert "DELETE FROM finance.journal_lines" not in sql


def test_0089_backfills_4001_for_capital_works_funds_without_destructive_downgrade():
    sql = MIGRATION_0089_PATH.read_text()

    assert "Revision ID: 0089_capital_works_income_gl" in sql
    assert 'down_revision = "0088_fund_gl_accounts"' in sql
    assert "account_code = '4001'" in sql
    assert "f.fund_type::text IN ('sinking', 'capital_works')" in sql
    assert "ORDER BY CASE WHEN f.fund_type::text = 'sinking'" in sql
    assert "def downgrade() -> None:" in sql
    assert "pass" in sql
    assert "DELETE FROM finance.gl_accounts" not in sql
