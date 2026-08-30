"""ORM models for core config tables (migration 0011).

Covers:
    core.feature_toggles           — global feature registry (no RLS)
    core.feature_toggle_overrides  — per-scheme overrides     (tenant-scoped)
    core.building_settings         — per-scheme key/value     (tenant-scoped)
    core.role_permissions          — global role→permission   (no RLS)

Global tables (feature_toggles, role_permissions) have no tenant_id column;
they are visible to all tenants and have no RLS policy.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from . import stubs as _stubs  # noqa: F401 — registers FK-target table stubs in metadata


# ---------------------------------------------------------------------------
# core.feature_toggles  (global — no RLS, no tenant_id)
# ---------------------------------------------------------------------------
class FeatureToggle(Base):
    """Global feature-flag registry.

    Seeded by ``backend/seeds/feature_toggles.py``.  A row here defines the
    global default (tier 3).  Per-scheme overrides live in FeatureToggleOverride.
    """

    __tablename__ = "feature_toggles"
    __table_args__ = {"schema": "core"}

    toggle_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    feature_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    feature_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    routes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    allowed_roles: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    depends_on: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    seeded_version: Mapped[int] = mapped_column(nullable=False, default=1)
    last_modified_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    last_modified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    overrides: Mapped[list["FeatureToggleOverride"]] = relationship(
        "FeatureToggleOverride", back_populates="toggle", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# core.feature_toggle_overrides  (tenant-scoped)
# ---------------------------------------------------------------------------
class FeatureToggleOverride(Base):
    """Per-scheme override of a global feature toggle (tier 2).

    Uniqueness: ``(scheme_id, feature_key)`` — one override per feature per scheme.
    """

    __tablename__ = "feature_toggle_overrides"
    __table_args__ = (
        UniqueConstraint("scheme_id", "feature_key", name="uq_fto_scheme_feature"),
        {"schema": "core"},
    )

    override_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
    )
    scheme_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.schemes.scheme_id", ondelete="CASCADE"),
        nullable=False,
    )
    feature_key: Mapped[str] = mapped_column(
        Text,
        ForeignKey("core.feature_toggles.feature_key", onupdate="CASCADE"),
        nullable=False,
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    set_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    toggle: Mapped["FeatureToggle"] = relationship(
        "FeatureToggle", back_populates="overrides"
    )


# ---------------------------------------------------------------------------
# core.building_settings  (tenant-scoped)
# ---------------------------------------------------------------------------
class BuildingSetting(Base):
    """Per-scheme key/value configuration store.

    Keys use dot-notation (e.g. ``finance.fy_start_month``, ``building.abn``).
    Values are JSONB to support scalars, arrays, and nested objects.
    """

    __tablename__ = "building_settings"
    __table_args__ = (
        UniqueConstraint("scheme_id", "setting_key", name="uq_bs_scheme_key"),
        {"schema": "core"},
    )

    setting_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
    )
    scheme_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.schemes.scheme_id", ondelete="CASCADE"),
        nullable=False,
    )
    setting_key: Mapped[str] = mapped_column(Text, nullable=False)
    setting_value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    set_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# core.role_permissions  (global — no RLS, no tenant_id)
# ---------------------------------------------------------------------------
class RolePermission(Base):
    """Global role → permission grant map.

    PK is ``(role, permission_key)``.  Seeded by ``backend/seeds/role_permissions.py``,
    which mirrors the ``DEFAULT_PERMISSIONS`` dict in ``backend/models/user.py``.
    """

    __tablename__ = "role_permissions"
    __table_args__ = {"schema": "core"}

    role: Mapped[str] = mapped_column(Text, primary_key=True)  # core.user_role value
    permission_key: Mapped[str] = mapped_column(Text, primary_key=True)  # e.g. 'can_view_finances'
    is_granted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
