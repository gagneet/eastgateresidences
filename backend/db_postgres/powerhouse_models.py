"""SQLAlchemy models for Powerhouse PostgreSQL command foundation."""

from __future__ import annotations

# @featuretrace:powerhouse-foundation — ORM mappings for Powerhouse command idempotency/control-plane metadata.
# Layer: model
# Data flow: Alembic 0071 → SQLAlchemy models → Powerhouse command services/tests.
# Related: backend/services/powerhouse_command_foundation.py

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db_postgres.base import Base


class PgPowerhouseCommandIdempotencyRecord(Base):
    """Maps `core.command_idempotency_records` for repeat-safe commands."""

    __tablename__ = "command_idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "scheme_id",
            "command_scope",
            "command_type",
            "idempotency_key",
            name="uq_command_idempotency_scope_key",
        ),
        {"schema": "core"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scheme_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    building_id: Mapped[str] = mapped_column(Text, nullable=False)
    command_scope: Mapped[str] = mapped_column(Text, nullable=False)
    command_type: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_type: Mapped[Optional[str]] = mapped_column(Text)
    aggregate_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    result_status: Mapped[str] = mapped_column(Text, nullable=False)
    result_reference: Mapped[dict] = mapped_column(JSONB, nullable=False, default={})
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    replay_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_test_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class PgPowerhouseDomainCutoverStatusContinuity(Base):
    """Minimal mapping for Powerhouse continuity fields on `core.domain_cutover_status`."""

    __tablename__ = "domain_cutover_status"
    __table_args__ = {"schema": "core"}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    building_id: Mapped[str] = mapped_column(Text, nullable=False)
    scheme_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    read_source: Mapped[str] = mapped_column(Text, nullable=False)
    write_source: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    readiness_status: Mapped[str] = mapped_column(Text, nullable=False)
    continuity_source: Mapped[Optional[str]] = mapped_column(Text)
    continuity_policy: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
