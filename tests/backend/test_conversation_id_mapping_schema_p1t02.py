# @featuretrace:powerhouse-foundation — P1-T02 migration behavior tests.
# Layer: test
# Data flow: migration upgrade/downgrade → Alembic op calls → powerhouse.legacy_id_mappings (building-scoped).
# Related: backend/alembic/versions/0048_powerhouse_id_mapping.py
#          backend/services/powerhouse_conversation_service.py

"""Behavioral tests for Alembic 0048 legacy ID mapping migration."""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import MagicMock


MIGRATION_PATH = Path(__file__).resolve().parents[2] / "backend" / "alembic" / "versions" / "0048_powerhouse_id_mapping.py"


def _load_migration_module():
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    source = source.replace("from alembic import op\n", "")
    module = types.ModuleType("migration_0048_powerhouse_id_mapping")
    exec(compile(source, str(MIGRATION_PATH), "exec"), module.__dict__)
    return module


def _table_call_named(create_table_mock: MagicMock, table_name: str):
    for c in create_table_mock.call_args_list:
        if c.args and c.args[0] == table_name:
            return c
    raise AssertionError(f"Missing create_table call for {table_name}")


def _column_names_from_table_call(table_call) -> set[str]:
    columns = [arg for arg in table_call.args[1:] if getattr(arg, "name", None)]
    return {c.name for c in columns}


def test_upgrade_creates_mapping_table_and_schema():
    module = _load_migration_module()

    mock_op = MagicMock()
    module.op = mock_op
    module._create_rls_policy = MagicMock()
    module._grant_schema = MagicMock()
    module._ensure_current_tenant_id_function = MagicMock()

    module.upgrade()

    mock_op.execute.assert_any_call("CREATE SCHEMA IF NOT EXISTS powerhouse")
    created_tables = [c.args[0] for c in mock_op.create_table.call_args_list]
    assert created_tables == ["legacy_id_mappings"]


def test_upgrade_mapping_table_has_required_columns():
    module = _load_migration_module()

    mock_op = MagicMock()
    module.op = mock_op
    module._create_rls_policy = MagicMock()
    module._grant_schema = MagicMock()
    module._ensure_current_tenant_id_function = MagicMock()

    module.upgrade()

    table_call = _table_call_named(mock_op.create_table, "legacy_id_mappings")
    cols = _column_names_from_table_call(table_call)
    for required in [
        "tenant_id",
        "scheme_id",
        "building_id",
        "entity_type",
        "legacy_id",
        "pg_id",
        "is_active",
        "is_test_data",
    ]:
        assert required in cols


def test_upgrade_creates_expected_indexes():
    module = _load_migration_module()

    mock_op = MagicMock()
    module.op = mock_op
    module._create_rls_policy = MagicMock()
    module._grant_schema = MagicMock()
    module._ensure_current_tenant_id_function = MagicMock()

    module.upgrade()

    created_index_names = [c.args[0] for c in mock_op.create_index.call_args_list]
    assert "ux_legacy_id_mappings_legacy" in created_index_names
    assert "ux_legacy_id_mappings_pg" in created_index_names
    assert "idx_legacy_id_mappings_building_entity" in created_index_names


def test_upgrade_applies_rls_policy():
    module = _load_migration_module()

    mock_op = MagicMock()
    mock_rls = MagicMock()
    module.op = mock_op
    module._create_rls_policy = mock_rls
    module._grant_schema = MagicMock()
    module._ensure_current_tenant_id_function = MagicMock()

    module.upgrade()

    mock_rls.assert_called_once_with("powerhouse", "legacy_id_mappings")


def test_downgrade_drops_mapping_table():
    module = _load_migration_module()

    mock_op = MagicMock()
    module.op = mock_op

    module.downgrade()

    mock_op.execute.assert_called_once_with('DROP TABLE IF EXISTS "powerhouse"."legacy_id_mappings" CASCADE')


def test_identifier_validation_rejects_unsafe_names():
    module = _load_migration_module()

    assert module._safe_identifier("powerhouse") == "powerhouse"
    assert module._safe_identifier("legacy_id_mappings") == "legacy_id_mappings"

    for bad in ["powerhouse;DROP", 'bad"name', "space name", "camelCase", "../x"]:
        try:
            module._safe_identifier(bad)
            raise AssertionError(f"Expected ValueError for {bad!r}")
        except ValueError:
            pass
