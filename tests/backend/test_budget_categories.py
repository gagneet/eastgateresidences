# @featuretrace:finance-postgres-read-cutover — the PostgreSQL home for levy_categories.
# Layer: test
# Data flow: levy_categories → backfill → finance.budget_categories → get_budget_categories
#            (building-scoped).
# Related: backend/alembic/versions/0106_budget_categories.py
#          backend/scripts/data_migration/backfill_budget_categories.py
#          backend/services/financial_read_service.py
"""Contract tests for the budget-category migration and its derived actuals.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_budget_categories.py -q
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

MIGRATION = ROOT / "backend" / "alembic" / "versions" / "0106_budget_categories.py"
BACKFILL = ROOT / "backend" / "scripts" / "data_migration" / "backfill_budget_categories.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module          # before exec: @dataclass needs it
    spec.loader.exec_module(module)
    return module


class TestMigration:
    def test_revision_string_fits_the_32_char_column(self):
        """core.alembic_version.version_num is VARCHAR(32). A longer id applies its DDL
        and then fails the version bump, rolling the whole migration back."""
        source = MIGRATION.read_text()
        revision = source.split('revision = "')[1].split('"')[0]
        assert len(revision) <= 32, f"{revision!r} is {len(revision)} chars"

    def test_there_is_no_actual_cents_column(self):
        """The codebase already decided this: financial_service.py says actual_amount is
        NEVER stored, always derived. A stored copy drifts from expense_transactions the
        moment an expense is posted, reversed or re-categorised — which is how East Gate
        ended up with two expense totals that differed 3.6x."""
        source = MIGRATION.read_text()
        assert "actual_cents" not in source.split('def upgrade')[1].split('def downgrade')[0]

    def test_budgeted_cents_is_nullable(self):
        """109 of 322 rows have no budget. NULL and 0 are different: 0 states a budget
        of zero that nobody set."""
        source = MIGRATION.read_text()
        block = source.split('"budgeted_cents"')[1][:200]
        assert "nullable=True" in block

    def test_rls_is_enabled_and_forced(self):
        source = MIGRATION.read_text()
        assert "ENABLE ROW LEVEL SECURITY" in source
        assert "FORCE ROW LEVEL SECURITY" in source
        assert "tenant_id = core.current_tenant_id()" in source

    def test_downgrade_removes_the_policy_before_the_table(self):
        down = MIGRATION.read_text().split("def downgrade")[1]
        assert down.index("DROP POLICY") < down.index("drop_table")


class TestBackfill:
    def setup_method(self):
        self.mod = _load(BACKFILL, "_backfill_budget_categories")

    def test_dollars_become_cents_exactly_once(self):
        assert self.mod._to_cents(1798.00) == 179800
        assert self.mod._to_cents("909") == 90900
        assert self.mod._to_cents(0.1 + 0.2) == 30      # float noise rounded, not truncated

    def test_a_missing_budget_is_none_not_zero(self):
        """A category with no budgeted_amount is one nobody budgeted."""
        assert self.mod._to_cents(None) is None
        assert self.mod._to_cents("") is None
        assert self.mod._to_cents(0) == 0               # an explicit zero survives as zero

    def test_fund_types_map_explicitly_rather_than_passing_through(self):
        """An unrecognised fund_type must be a reported skip, not an FK failure — and
        never a best guess: a sinking expense filed to the admin fund is silently wrong
        in exactly the way nobody notices until a levy is set from it."""
        assert self.mod.FUND_TYPE_MAP["admin"] == "admin"
        assert self.mod.FUND_TYPE_MAP["capital_works"] == "sinking"
        assert "nonsense_fund" not in self.mod.FUND_TYPE_MAP

    def test_it_is_idempotent_on_the_legacy_id(self):
        """Re-running must update, not duplicate. There is no natural key that survives
        the archived duplicates — an archived row and its replacement share
        scheme + year + fund + name."""
        source = BACKFILL.read_text()
        assert "ON CONFLICT (tenant_id, legacy_mongo_id) DO UPDATE" in source

    def test_it_is_dry_run_by_default(self):
        source = BACKFILL.read_text()
        assert 'ap.add_argument("--apply", action="store_true")' in source


class TestDerivedActuals:
    def setup_method(self):
        from services.financial_read_service import FinancialReadService

        self.src = inspect.getsource(FinancialReadService.get_budget_categories)

    def test_the_actual_is_derived_from_expense_transactions(self):
        assert "finance.expense_transactions" in self.src
        assert "SUM(et.amount_cents)" in self.src

    def test_the_derivation_joins_on_the_full_grain(self):
        """scheme + fund + year + name. Dropping any one silently sums another fund's or
        another year's expenses into this budget line."""
        for predicate in ("et.scheme_id = bc.scheme_id", "et.fund_id = bc.fund_id",
                          "et.financial_year = bc.financial_year",
                          "et.category_name = bc.name"):
            assert predicate in self.src, f"missing join predicate: {predicate}"

    def test_a_category_with_no_expenses_derives_zero_not_null(self):
        assert "COALESCE(" in self.src

    def test_archived_categories_are_excluded_by_default(self):
        assert "bc.is_archived = FALSE" in self.src
        assert "include_archived" in self.src

    def test_the_decimal_from_sum_is_cast_to_float(self):
        """SUM() over a bigint returns Decimal. Pydantic coerces it on the way out, which
        hides the problem, but a caller that does arithmetic first — /finance/budget-vs-actual
        sums these — raises TypeError on Decimal + float."""
        assert "float(_to_aud(row.actual_cents))" in self.src

    def test_the_string_join_limitation_is_documented_not_papered_over(self):
        """A fuzzy match here would silently move money between budget lines."""
        assert "category_name" in self.src
        assert "STRING match" in self.src or "string match" in self.src.lower()
