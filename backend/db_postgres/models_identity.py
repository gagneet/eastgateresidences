"""ORM models for core identity tables (migration 0010).

Covers:
    core.users
    core.user_units
    core.user_role_assignments
    core.user_sessions
    core.user_invitations

All tenant-scoped models carry ``tenant_id`` (the RLS partition key).
The ``user_role`` enum type already exists in the DB — use
``create_type=False`` to prevent SQLAlchemy from attempting to CREATE it.

Column types sourced from ``sqlalchemy.dialects.postgresql`` to match the
exact Postgres column types created in migration 0010.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import BYTEA, CITEXT, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import relationship as sa_relationship

import sqlalchemy.dialects.postgresql as pg

from .base import Base
from . import stubs as _stubs  # noqa: F401 — registers FK-target table stubs in metadata


# ---------------------------------------------------------------------------
# Python-side mirror of core.user_role DB enum.
# create_type=False because the type already exists in the DB (migration 0010).
# ---------------------------------------------------------------------------

class UserRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    STRATA_ADMIN = "strata_admin"  # admin of a Strata Management Company (the tenant)
    STRATA_MANAGER = "strata_manager"
    EC_MEMBER = "ec_member"  # sub-positions (chairman/treasurer/…) on user_role_assignments
    ADMIN_STAFF = "admin_staff"
    REAL_ESTATE_AGENT = "real_estate_agent"
    SERVICE_PROVIDER = "service_provider"
    OWNER = "owner"
    TENANT = "tenant"
    GUEST = "guest"


_ROLE_ENUM = pg.ENUM(*[r.value for r in UserRole], name="user_role", schema="core", create_type=False)

# core.record_status enum — used by users.status, schemes.status, etc.
_RECORD_STATUS_ENUM = pg.ENUM(
    "draft", "active", "inactive", "archived",
    name="record_status", schema="core", create_type=False,
)


# ---------------------------------------------------------------------------
# core.users
# ---------------------------------------------------------------------------
class User(Base):
    """Identity record for every platform user.

    Scoped by ``tenant_id`` (RLS).  The unique natural key is
    ``(tenant_id, email)`` — enforced by the DB unique constraint.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="users_tenant_id_email_key"),
        {"schema": "core"},
    )

    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
    )
    party_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.parties.party_id", ondelete="SET NULL"),
        nullable=True,
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False, default="")
    auth_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    totp_secret_encrypted: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(_RECORD_STATUS_ENUM, nullable=False, default="active")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    owner_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_name_flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    flag_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(_ROLE_ENUM, nullable=False, default="owner")
    effective_role: Mapped[str | None] = mapped_column(_ROLE_ENUM, nullable=True)
    default_scheme_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.schemes.scheme_id", ondelete="SET NULL"),
        nullable=True,
    )
    permission_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    is_test_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationships
    unit_assignments: Mapped[list["UserUnit"]] = sa_relationship(
        "UserUnit", back_populates="user", cascade="all, delete-orphan"
    )
    role_assignments: Mapped[list["UserRoleAssignment"]] = sa_relationship(
        "UserRoleAssignment",
        foreign_keys="UserRoleAssignment.user_id",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    sessions: Mapped[list["UserSession"]] = sa_relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# core.user_units
# ---------------------------------------------------------------------------
class UserUnit(Base):
    """Temporal binding of a user to a lot (unit) within a scheme.

    A user may own/occupy multiple lots; valid_to=NULL means currently active.
    """

    __tablename__ = "user_units"
    __table_args__ = {"schema": "core"}

    user_unit_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
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
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    lot_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.lots.lot_id", ondelete="CASCADE"),
        nullable=False,
    )
    party_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.parties.party_id", ondelete="SET NULL"),
        nullable=True,
    )
    # owner | tenant | family | agent — named lot_relationship to avoid shadowing
    # the sqlalchemy.orm.relationship import; maps to "relationship" column in DB.
    lot_relationship: Mapped[str] = mapped_column("relationship", Text, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(Date, nullable=False)  # type: ignore[assignment]
    valid_to: Mapped[datetime | None] = mapped_column(Date, nullable=True)  # type: ignore[assignment]
    is_test_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped["User"] = sa_relationship("User", back_populates="unit_assignments")


# ---------------------------------------------------------------------------
# core.user_role_assignments
# ---------------------------------------------------------------------------
class UserRoleAssignment(Base):
    """Maps a user to a role, either globally or within a specific scheme.

    Uniqueness invariant (from migration 0010 partial indexes):
    - If ``scheme_id IS NOT NULL``: unique on (user_id, scheme_id, role)
    - If ``scheme_id IS NULL``:    unique on (user_id, role) — global role

    ``expires_at IS NOT NULL`` indicates a temporary elevation that will be
    cleaned up by the background worker once the timestamp passes.
    """

    __tablename__ = "user_role_assignments"
    __table_args__ = {"schema": "core"}

    assignment_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    scheme_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.schemes.scheme_id", ondelete="CASCADE"),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(_ROLE_ENUM, nullable=False)
    # EC sub-position: CHAIRMAN | TREASURER | SECRETARY | MEMBER
    ec_position: Mapped[str | None] = mapped_column(Text, nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    granted_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    # Non-NULL = temp elevation; NULL = permanent assignment
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user: Mapped["User"] = sa_relationship(
        "User", foreign_keys=[user_id], back_populates="role_assignments"
    )


# ---------------------------------------------------------------------------
# core.user_sessions
# ---------------------------------------------------------------------------
class UserSession(Base):
    """JWT session ledger for revocation and audit.

    ``jwt_jti`` is the JWT ID claim (unique identifier per issued token).
    Revocation: set ``revoked_at``.  Background worker prunes expired rows.
    """

    __tablename__ = "user_sessions"
    __table_args__ = {"schema": "core"}

    session_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    jwt_jti: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = sa_relationship("User", back_populates="sessions")


# ---------------------------------------------------------------------------
# core.user_invitations
# ---------------------------------------------------------------------------
class UserInvitation(Base):
    """Single-use expiring claim-token invitation (Phase C onboarding flow).

    ``claim_token_sha256`` stores only the SHA-256 digest of the raw secret;
    the raw secret is delivered via email and never stored.
    """

    __tablename__ = "user_invitations"
    __table_args__ = {"schema": "core"}

    invitation_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
    )
    # NULL scheme_id = org-level invite (no specific building yet)
    scheme_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.schemes.scheme_id", ondelete="CASCADE"),
        nullable=True,
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    invited_role: Mapped[str] = mapped_column(_ROLE_ENUM, nullable=False)
    invited_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    invited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claim_token_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    prefill_first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    prefill_last_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    prefill_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    prefill_unit_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.lots.lot_id", ondelete="SET NULL"),
        nullable=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("core.users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
