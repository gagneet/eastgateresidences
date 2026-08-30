# @featuretrace:conversations — P1-T01 migration behavior tests.
# Layer: test
# Data flow: migration upgrade/downgrade → Alembic op calls → powerhouse schema objects (building-scoped).
# Related: backend/alembic/versions/0047_conversation_primitives_p1t01.py
#          tests/backend/test_dual_source_consistency.py

"""Behavioral tests for Alembic 0047 conversation primitives migration."""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import MagicMock, call


MIGRATION_PATH = Path(__file__).resolve().parents[2] / "backend" / "alembic" / "versions" / "0047_conversation_primitives_p1t01.py"


def _load_migration_module():
    """
    Load migration source in an isolated module namespace.

    The repository has a local `backend/alembic/` package that shadows the pip Alembic
    package during test imports, so we execute source directly and inject `op` mocks.
    """
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    source = source.replace("from alembic import op\n", "")
    module = types.ModuleType("migration_0047_conversation_primitives_p1t01")
    exec(compile(source, str(MIGRATION_PATH), "exec"), module.__dict__)
    return module


def _table_call_named(create_table_mock: MagicMock, table_name: str):
    """Return the create_table call matching the requested table name."""
    for c in create_table_mock.call_args_list:
        if c.args and c.args[0] == table_name:
            return c
    raise AssertionError(f"Missing create_table call for {table_name}")


def _column_names_from_table_call(table_call) -> set[str]:
    """Extract column names from an Alembic op.create_table call."""
    columns = [arg for arg in table_call.args[1:] if getattr(arg, "name", None)]
    return {c.name for c in columns}


def test_upgrade_creates_expected_tables_and_schema(monkeypatch):
    module = _load_migration_module()

    mock_op = MagicMock()
    module.op = mock_op
    module._create_rls_policy = MagicMock()

    module.upgrade()

    mock_op.execute.assert_any_call("CREATE SCHEMA IF NOT EXISTS powerhouse")
    created_tables = [c.args[0] for c in mock_op.create_table.call_args_list]
    assert created_tables == ["conversations", "messages", "participants", "assignments", "events"]


def test_upgrade_tables_include_tenant_scheme_and_test_data(monkeypatch):
    module = _load_migration_module()

    mock_op = MagicMock()
    module.op = mock_op
    module._create_rls_policy = MagicMock()

    module.upgrade()

    for table in ["conversations", "messages", "participants", "assignments", "events"]:
        table_call = _table_call_named(mock_op.create_table, table)
        column_names = _column_names_from_table_call(table_call)
        assert "tenant_id" in column_names
        assert "scheme_id" in column_names
        assert "is_test_data" in column_names


def test_upgrade_applies_rls_policy_to_every_table(monkeypatch):
    module = _load_migration_module()

    mock_op = MagicMock()
    mock_rls = MagicMock()
    module.op = mock_op
    module._create_rls_policy = mock_rls

    module.upgrade()

    assert mock_rls.call_args_list == [
        call("powerhouse", "conversations"),
        call("powerhouse", "messages"),
        call("powerhouse", "participants"),
        call("powerhouse", "assignments"),
        call("powerhouse", "events"),
    ]


def test_upgrade_creates_expected_foreign_keys(monkeypatch):
    module = _load_migration_module()

    mock_op = MagicMock()
    module.op = mock_op
    module._create_rls_policy = MagicMock()

    module.upgrade()

    fk_names = [c.args[0] for c in mock_op.create_foreign_key.call_args_list]
    assert fk_names == [
        "fk_messages_conversation",
        "fk_participants_conversation",
        "fk_assignments_conversation",
        "fk_events_conversation",
    ]


def test_upgrade_creates_participants_uniqueness_constraint(monkeypatch):
    module = _load_migration_module()

    mock_op = MagicMock()
    module.op = mock_op
    module._create_rls_policy = MagicMock()

    module.upgrade()

    mock_op.create_unique_constraint.assert_called_once_with(
        "uq_participants_conversation_user",
        "participants",
        ["conversation_id", "user_id"],
        schema="powerhouse",
    )


def test_upgrade_creates_core_indexes(monkeypatch):
    module = _load_migration_module()

    mock_op = MagicMock()
    module.op = mock_op
    module._create_rls_policy = MagicMock()

    module.upgrade()

    created_index_names = [c.args[0] for c in mock_op.create_index.call_args_list]
    for required in [
        "idx_conversations_tenant_scheme",
        "idx_messages_tenant_conversation",
        "idx_participants_tenant_conversation",
        "idx_assignments_tenant_conversation",
        "idx_events_tenant_conversation",
    ]:
        assert required in created_index_names


def test_downgrade_drops_schema(monkeypatch):
    module = _load_migration_module()

    mock_op = MagicMock()
    module.op = mock_op

    module.downgrade()

    mock_op.execute.assert_called_once_with("DROP SCHEMA IF EXISTS powerhouse CASCADE")
