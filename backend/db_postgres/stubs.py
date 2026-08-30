"""Minimal SQLAlchemy Table stubs for DB tables that have ORM FK references
but no full ORM model in this module.

These stubs register the table + primary-key column in Base.metadata so that
FK resolution succeeds when SQLAlchemy builds its dependency-sorted table map
for DML operations.  They do NOT define all columns — only the PK columns
that are the FK targets.

Tables defined here:
    core.tenants    – tenant root; FK target for tenant_id columns
    core.schemes    – per-tenant strata scheme; FK target for scheme_id columns
    core.lots       – individual lots/units; FK target for lot_id columns
    core.parties    – legal entities (companies, trusts); FK target for party_id
"""
from __future__ import annotations

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import UUID

from .base import Base

# We use Table() objects (not declarative classes) to keep these stubs minimal.
# The schema is "core" for all these tables.

_metadata = Base.metadata


def _stub(table_name: str, pk_col: str) -> None:
    """Register a single-column stub in Base.metadata (idempotent)."""
    if f"core.{table_name}" not in _metadata.tables:
        from sqlalchemy import Table
        Table(
            table_name,
            _metadata,
            Column(pk_col, UUID(as_uuid=False), primary_key=True),
            schema="core",
            extend_existing=True,
        )


_stub("tenants", "tenant_id")
_stub("schemes", "scheme_id")
_stub("lots", "lot_id")
_stub("parties", "party_id")
