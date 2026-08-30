# @featuretrace:levy-fairness — configured cohorts replace inferred ones.
# Layer: test
# Data flow: /benefit-groups CRUD + levy_fairness_service group resolution (building-scoped).
# Related: backend/routers/benefit_groups.py
#          backend/services/levy_fairness_service.py
"""Groups are configured, not inferred — and membership is exclusive.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_benefit_groups.py -q
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pathlib

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

MIGRATION = ROOT / "backend" / "alembic" / "versions" / "0107_benefit_groups.py"
ROUTER = ROOT / "backend" / "routers" / "benefit_groups.py"


class TestMembershipIsExclusive:
    def test_the_lot_is_the_primary_key_not_the_pair(self):
        """A lot in two groups is counted on BOTH sides of a zero-sum redistribution, so
        the arithmetic still balances and nothing downstream can detect it. The constraint
        is the only place it can be caught."""
        source = MIGRATION.read_text()
        block = source.split('"lot_benefit_groups"')[1].split("op.create_index")[0]
        assert 'sa.Column("lot_id"' in block and "primary_key=True" in block
        assert block.count("primary_key=True") == 1, "only the lot may be the key"

    def test_assignment_upserts_on_the_lot(self):
        assert "ON CONFLICT (lot_id) DO UPDATE" in ROUTER.read_text()


class TestTenantAndBuildingSafety:
    def test_lots_from_another_building_are_rejected(self):
        """RLS would NOT stop this — the row written carries this tenant's id."""
        source = ROUTER.read_text()
        assert "do not belong to this building" in source

    def test_missing_scheme_context_refuses_rather_than_returning_empty(self):
        """core.benefit_groups has a strict RLS policy: no tenant context means zero rows
        and no error, which reads exactly like 'no groups configured'."""
        source = ROUTER.read_text()
        assert "No PostgreSQL scheme context" in source
        assert "status_code=409" in source

    def test_rls_is_enabled_and_forced_on_both_tables(self):
        """Applied via an f-string loop over both tables, so match that rather than a
        per-table literal — asserting the literal made this test fail on correct code."""
        source = MIGRATION.read_text()
        assert 'for table in ("benefit_groups", "lot_benefit_groups")' in source
        assert "ENABLE ROW LEVEL SECURITY" in source
        assert "FORCE ROW LEVEL SECURITY" in source
        assert "tenant_id = core.current_tenant_id()" in source

    def test_manage_guard_uses_effective_role(self):
        """An elevated owner reads as 'owner' on the raw field and would be refused.

        Checked by AST, not by text search: the first version searched the source for
        `current_user["role"]` and matched the COMMENT warning against using it, failing
        on code that was already correct.
        """
        import ast as _ast

        tree = _ast.parse(ROUTER.read_text())
        raw_role_reads = [
            n for n in _ast.walk(tree)
            if isinstance(n, _ast.Subscript)
            and isinstance(n.value, _ast.Name) and n.value.id == "current_user"
            and isinstance(n.slice, _ast.Constant) and n.slice.value == "role"
        ]
        assert not raw_role_reads, "guard must use effective_role(), not current_user['role']"
        assert "effective_role(current_user)" in ROUTER.read_text()


class TestTheEngineUsesConfiguration:
    def test_configured_groups_take_precedence_over_inference(self):
        from services import levy_fairness_service as svc

        source = inspect.getsource(svc.simulate_levy_fairness)
        assert "_configured_lot_groups" in source
        assert "configured.get(u[\"unit_number\"]) or _group_key(u)" in source

    @pytest.mark.asyncio
    async def test_no_configuration_falls_back_rather_than_failing(self):
        """A building that has configured nothing keeps its previous behaviour."""
        from services import levy_fairness_service as svc

        with patch("db_postgres.repos.config_repo.resolve_scheme_context",
                   new=AsyncMock(return_value=None)):
            assert await svc._configured_lot_groups("13195") == {}

    @pytest.mark.asyncio
    async def test_an_unreachable_database_falls_back_rather_than_500ing(self):
        """A settings table must not be able to take the fairness page down."""
        from services import levy_fairness_service as svc

        with patch("db_postgres.repos.config_repo.resolve_scheme_context",
                   new=AsyncMock(side_effect=RuntimeError("pg down"))):
            assert await svc._configured_lot_groups("13195") == {}

    def test_unassigned_lots_are_reported_not_defaulted(self):
        """A default silently changes who subsidises whom."""
        from services import levy_fairness_service as svc

        source = inspect.getsource(svc.simulate_levy_fairness)
        assert "_unassigned" in source
        assert "not assigned to a benefit group" in source


class TestNeutralNaming:
    def test_the_default_name_is_a_letter_not_a_building_form(self):
        page = (ROOT / "frontend" / "src" / "pages" / "dashboard"
                / "BenefitGroupsSettingsPage.jsx").read_text()
        assert "String.fromCharCode(65 + groups.length)" in page
        assert "Townhouse" not in page and "Apartment" not in page


class TestZeroSum:
    def test_benefit_is_a_share_of_the_levy_pool_not_the_capital_base(self):
        """benefit_totals mixes annual facility costs with the whole multi-year capital
        schedule. Comparing that to one year's levy produced a 2.85x ratio in which every
        group owed more — impossible for a redistribution."""
        from services import levy_fairness_service as svc

        source = inspect.getsource(svc.simulate_levy_fairness)
        assert "_levy_pool = sum(payment_totals.values())" in source
        assert 'benefit_shares.get(u["unit_number"], 0.0) * _levy_pool' in source

    def test_the_zero_sum_violation_is_reported_not_scaled_away(self):
        from services import levy_fairness_service as svc

        source = inspect.getsource(svc.simulate_levy_fairness)
        assert "zero_sum_violation" in source
        assert "reconciliation" in source


class TestGroupListingQuery:
    """The lot_count query must scan the membership table once, and keep empty groups."""

    def _sql(self):
        src = pathlib.Path("backend/routers/benefit_groups.py").read_text()
        start = src.index("SELECT g.benefit_group_id::text, g.name, g.description")
        return src[start:src.index('"""', start)]

    def test_it_does_not_re_scan_membership_once_per_group(self):
        # A correlated subquery is O(groups x lots). An index on benefit_group_id exists
        # and the planner declines it at this size, so the cost is the REPEAT rather than
        # a missing index -- which is why this asserts the shape, not an index.
        sql = self._sql()
        assert "SELECT count(*) FROM core.lot_benefit_groups" not in sql
        assert "LEFT JOIN core.lot_benefit_groups" in sql
        assert "GROUP BY" in sql

    def test_the_join_is_outer_so_an_empty_group_still_lists(self):
        # An inner join silently drops a group with no lots. An empty group is a
        # legitimate mid-configuration state, and the settings UI has to show it for
        # anyone to be able to put lots into it.
        sql = self._sql()
        joins = [ln for ln in sql.splitlines() if "JOIN core.lot_benefit_groups" in ln]
        assert joins and all("LEFT JOIN" in ln for ln in joins), joins

    def test_it_counts_the_joined_lot_not_the_rows(self):
        # count(*) over a LEFT JOIN returns 1 for an empty group, because the outer row
        # survives with NULLs. count(m.lot_id) returns 0. This is the classic outer-join
        # counting bug and it reports an empty group as holding one lot.
        sql = self._sql()
        assert "count(m.lot_id)" in sql
        assert "count(*)" not in sql
