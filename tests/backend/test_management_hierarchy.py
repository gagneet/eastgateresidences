# @featuretrace:management-hierarchy — Tests for management hierarchy service, router, access control, and BI toggle resolution.
# Layer: test
# Data flow: test → management_hierarchy_service / management_hierarchy router (building-scoped)
# Related: backend/services/management_hierarchy_service.py
#          backend/routers/management_hierarchy.py
#          backend/services/bi_toggle_service.py
#          backend/alembic/versions/0056_management_entity_model.py
#          backend/alembic/versions/0057_scheme_manager_appointments.py
# Tests: this file
"""Tests for management hierarchy service, router, access control, and BI toggle resolution.

Coverage:
  A. Management hierarchy ensure() — all four modes
  B. Idempotency — no duplicate active primary assignments
  C. EC-appointed manager appointments
  D. BI toggle resolution for management entity types
  E. Access control — per-role entity visibility
  F. ECPosition model extensions
  G. Access hardening — IDOR, role isolation, UUID validation, field filtering, audit
  H. Router smoke tests
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

SCHEME_ID = "aaaaaaaa-0000-0000-0000-000000000001"
ENTITY_ID = "bbbbbbbb-0000-0000-0000-000000000002"
ASSIGNMENT_ID = "cccccccc-0000-0000-0000-000000000003"
AGENCY_ID = "dddddddd-0000-0000-0000-000000000004"
USER_ID = "eeeeeeee-0000-0000-0000-000000000005"
ACTOR_ID = "ffffffff-0000-0000-0000-000000000006"
BUILDING_ID = "13195"


def _make_entity_row(entity_type: str = "self_managed_oc", is_self_managed: bool = True):
    return (
        ENTITY_ID, entity_type, f"Test {entity_type}", "active",
        True, is_self_managed, entity_type == "independent_manager",
        None, None,
    )


def _make_assignment_row():
    return (
        ASSIGNMENT_ID, SCHEME_ID, ENTITY_ID,
        "self_managed", "active", True, date.today(), None,
    )


def _make_mock_session(
    *,
    existing_assignment=None,
    entity_row=None,
    agency_row=None,
    insert_entity_row=None,
    insert_assignment_row=None,
    outbox_ok=True,
):
    """Build a mock async_session_context session with controlled query results."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.commit = AsyncMock()

    call_count = [0]

    async def mock_execute(sql, params=None):
        result = MagicMock()
        sql_str = str(sql)
        # existing assignment check
        if "scheme_management_assignments" in sql_str and "is_primary" in sql_str and insert_entity_row is None:
            result.fetchone = MagicMock(return_value=existing_assignment)
        # entity insert / fetch
        elif "INSERT INTO core.management_entities" in sql_str:
            result.fetchone = MagicMock(
                return_value=insert_entity_row or _make_entity_row()
            )
        # assignment insert
        elif "INSERT INTO core.scheme_management_assignments" in sql_str:
            result.fetchone = MagicMock(
                return_value=insert_assignment_row or _make_assignment_row()
            )
        # agency name lookup
        elif "core.agencies" in sql_str and "SELECT name" in sql_str:
            result.fetchone = MagicMock(return_value=agency_row)
        # entity type lookup
        elif "SELECT entity_type" in sql_str:
            result.fetchone = MagicMock(return_value=(entity_row[1],) if entity_row else ("unknown",))
        # outbox insert
        elif "core.outbox" in sql_str:
            result.fetchone = MagicMock(return_value=None)
        else:
            result.fetchone = MagicMock(return_value=entity_row)
            result.fetchall = MagicMock(return_value=[])
        return result

    session.execute = AsyncMock(side_effect=mock_execute)
    return session


# ─────────────────────────────────────────────────────────────────────────────
# Part A — ensure_scheme_management_hierarchy: all four modes
# ─────────────────────────────────────────────────────────────────────────────

class TestEnsureHierarchyModes:
    """Part A: all four management modes create correct entities."""

    @pytest.mark.asyncio
    async def test_self_managed_creates_entity(self):
        from services.management_hierarchy_service import (
            ManagementEntityRecord, ManagementAssignmentRecord,
        )
        entity = ManagementEntityRecord(
            management_entity_id=ENTITY_ID, entity_type="self_managed_oc",
            name="Test Self-Managed OC", status="active",
            is_system_generated=True, is_self_managed=True, is_independent=False,
        )
        assignment = ManagementAssignmentRecord(
            assignment_id=ASSIGNMENT_ID, scheme_id=SCHEME_ID,
            management_entity_id=ENTITY_ID, assignment_type="self_managed",
            status="active", is_primary=True, start_date=date.today(),
        )
        with patch(
            "services.management_hierarchy_service._fetch_active_primary_assignment",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.management_hierarchy_service._resolve_or_create_entity",
            new=AsyncMock(return_value=(entity, True)),
        ), patch(
            "services.management_hierarchy_service._create_assignment",
            new=AsyncMock(return_value=assignment),
        ), patch(
            "services.management_hierarchy_service._emit_hierarchy_audit_event",
            new=AsyncMock(),
        ), patch(
            "db_postgres.session.async_session_context",
        ) as mock_ctx:
            _s = MagicMock(); _s.commit = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=_s)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            from services.management_hierarchy_service import ensure_scheme_management_hierarchy
            result = await ensure_scheme_management_hierarchy(
                scheme_id=SCHEME_ID,
                building_id=BUILDING_ID,
                mode="self_managed",
                actor_user_id=ACTOR_ID,
            )
        assert result.created is True
        assert result.entity.entity_type == "self_managed_oc"
        assert result.entity.is_self_managed is True
        assert result.entity.is_independent is False
        assert result.assignment.assignment_type == "self_managed"
        assert len(result.warnings) >= 1  # auto-created warning

    @pytest.mark.asyncio
    async def test_independent_manager_creates_entity(self):
        from services.management_hierarchy_service import (
            ManagementEntityRecord, ManagementAssignmentRecord,
        )
        entity = ManagementEntityRecord(
            management_entity_id=ENTITY_ID, entity_type="independent_manager",
            name="Test Independent Manager", status="active",
            is_system_generated=True, is_self_managed=False, is_independent=True,
        )
        assignment = ManagementAssignmentRecord(
            assignment_id=ASSIGNMENT_ID, scheme_id=SCHEME_ID,
            management_entity_id=ENTITY_ID, assignment_type="independent_manager",
            status="active", is_primary=True, start_date=date.today(),
        )
        with patch(
            "services.management_hierarchy_service._fetch_active_primary_assignment",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.management_hierarchy_service._resolve_or_create_entity",
            new=AsyncMock(return_value=(entity, True)),
        ), patch(
            "services.management_hierarchy_service._create_assignment",
            new=AsyncMock(return_value=assignment),
        ), patch(
            "services.management_hierarchy_service._emit_hierarchy_audit_event",
            new=AsyncMock(),
        ), patch("db_postgres.session.async_session_context") as mock_ctx:
            _s = MagicMock(); _s.commit = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=_s)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            from services.management_hierarchy_service import ensure_scheme_management_hierarchy
            result = await ensure_scheme_management_hierarchy(
                scheme_id=SCHEME_ID,
                building_id=BUILDING_ID,
                mode="independent_manager",
                actor_user_id=ACTOR_ID,
            )
        assert result.created is True
        assert result.entity.entity_type == "independent_manager"
        assert result.entity.is_independent is True

    @pytest.mark.asyncio
    async def test_unknown_creates_pending_placeholder(self):
        from services.management_hierarchy_service import (
            ManagementEntityRecord, ManagementAssignmentRecord,
        )
        entity = ManagementEntityRecord(
            management_entity_id=ENTITY_ID, entity_type="unknown",
            name="Test Management Pending", status="pending",
            is_system_generated=True, is_self_managed=False, is_independent=False,
        )
        assignment = ManagementAssignmentRecord(
            assignment_id=ASSIGNMENT_ID, scheme_id=SCHEME_ID,
            management_entity_id=ENTITY_ID, assignment_type="unknown",
            status="pending", is_primary=True, start_date=date.today(),
        )
        with patch(
            "services.management_hierarchy_service._fetch_active_primary_assignment",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.management_hierarchy_service._resolve_or_create_entity",
            new=AsyncMock(return_value=(entity, True)),
        ), patch(
            "services.management_hierarchy_service._create_assignment",
            new=AsyncMock(return_value=assignment),
        ), patch(
            "services.management_hierarchy_service._emit_hierarchy_audit_event",
            new=AsyncMock(),
        ), patch("db_postgres.session.async_session_context") as mock_ctx:
            _s = MagicMock(); _s.commit = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=_s)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            from services.management_hierarchy_service import ensure_scheme_management_hierarchy
            result = await ensure_scheme_management_hierarchy(
                scheme_id=SCHEME_ID,
                building_id=BUILDING_ID,
                mode="unknown",
                actor_user_id=ACTOR_ID,
            )
        assert result.created is True
        assert result.entity.entity_type == "unknown"
        assert result.assignment.status == "pending"

    @pytest.mark.asyncio
    async def test_agency_managed_requires_source_agency_id(self):
        with patch(
            "services.management_hierarchy_service._fetch_active_primary_assignment",
            new=AsyncMock(return_value=None),
        ), patch("db_postgres.session.async_session_context") as mock_ctx:
            _s = MagicMock(); _s.commit = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=_s)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            from services.management_hierarchy_service import ensure_scheme_management_hierarchy
            with pytest.raises(ValueError, match="source_agency_id is required"):
                await ensure_scheme_management_hierarchy(
                    scheme_id=SCHEME_ID,
                    building_id=BUILDING_ID,
                    mode="agency_managed",
                    actor_user_id=ACTOR_ID,
                    source_agency_id=None,
                )

    @pytest.mark.asyncio
    async def test_agency_managed_creates_entity_from_agency(self):
        from services.management_hierarchy_service import (
            ManagementEntityRecord, ManagementAssignmentRecord,
        )
        entity = ManagementEntityRecord(
            management_entity_id=ENTITY_ID, entity_type="agency",
            name="Test Agency Pty Ltd", status="active",
            is_system_generated=True, is_self_managed=False, is_independent=False,
            source_agency_id=AGENCY_ID,
        )
        assignment = ManagementAssignmentRecord(
            assignment_id=ASSIGNMENT_ID, scheme_id=SCHEME_ID,
            management_entity_id=ENTITY_ID, assignment_type="agency_managed",
            status="active", is_primary=True, start_date=date.today(),
        )
        with patch(
            "services.management_hierarchy_service._fetch_active_primary_assignment",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.management_hierarchy_service._resolve_or_create_entity",
            new=AsyncMock(return_value=(entity, True)),
        ), patch(
            "services.management_hierarchy_service._create_assignment",
            new=AsyncMock(return_value=assignment),
        ), patch(
            "services.management_hierarchy_service._emit_hierarchy_audit_event",
            new=AsyncMock(),
        ), patch("db_postgres.session.async_session_context") as mock_ctx:
            _s = MagicMock(); _s.commit = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=_s)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            from services.management_hierarchy_service import ensure_scheme_management_hierarchy
            result = await ensure_scheme_management_hierarchy(
                scheme_id=SCHEME_ID,
                building_id=BUILDING_ID,
                mode="agency_managed",
                actor_user_id=ACTOR_ID,
                source_agency_id=AGENCY_ID,
            )
        assert result.entity.entity_type == "agency"
        assert result.assignment.assignment_type == "agency_managed"


# ─────────────────────────────────────────────────────────────────────────────
# Part B — Idempotency
# ─────────────────────────────────────────────────────────────────────────────

class TestEnsureHierarchyIdempotency:
    """Part B: existing assignment returns without creating new rows."""

    @pytest.mark.asyncio
    async def test_returns_existing_without_creating(self):
        from services.management_hierarchy_service import ManagementEntityRecord, ManagementAssignmentRecord

        existing_assignment = {
            "assignment_id": ASSIGNMENT_ID,
            "scheme_id": SCHEME_ID,
            "management_entity_id": ENTITY_ID,
            "assignment_type": "self_managed",
            "status": "active",
            "is_primary": True,
            "start_date": date.today(),
            "end_date": None,
        }
        mock_entity = ManagementEntityRecord(
            management_entity_id=ENTITY_ID, entity_type="self_managed_oc",
            name="Test", status="active", is_system_generated=True,
            is_self_managed=True, is_independent=False,
        )

        with patch(
            "services.management_hierarchy_service._fetch_active_primary_assignment",
            new=AsyncMock(return_value=existing_assignment),
        ), patch(
            "services.management_hierarchy_service._fetch_entity",
            new=AsyncMock(return_value=mock_entity),
        ), patch("db_postgres.session.async_session_context") as mock_ctx:
            _s = MagicMock(); _s.commit = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=_s)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            from services.management_hierarchy_service import ensure_scheme_management_hierarchy
            result = await ensure_scheme_management_hierarchy(
                scheme_id=SCHEME_ID,
                building_id=BUILDING_ID,
                mode="self_managed",
                actor_user_id=ACTOR_ID,
            )
        assert result.created is False
        assert result.warnings == []


# ─────────────────────────────────────────────────────────────────────────────
# Part C — EC-appointed manager appointments
# ─────────────────────────────────────────────────────────────────────────────

class TestManagerAppointments:
    """Part C: EC-appointed manager appointment lifecycle."""

    def _appt_session(self, dup_exists: bool, return_row: tuple):
        """Build a mock session for appointment tests."""
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        session.commit = AsyncMock()

        async def mock_execute(sql, params=None):
            result = MagicMock()
            if "SELECT 1 FROM core.scheme_manager_appointments" in str(sql):
                result.fetchone = MagicMock(return_value=(1,) if dup_exists else None)
            else:
                result.fetchone = MagicMock(return_value=return_row)
            return result

        session.execute = AsyncMock(side_effect=mock_execute)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=None)
        return ctx

    @pytest.mark.asyncio
    async def test_create_ec_internal_strata_manager(self):
        appointment_row = (
            "appt-001", SCHEME_ID, USER_ID, ENTITY_ID,
            "ec_internal_strata_manager", "Internal Strata Manager",
            "active", True, date.today(), None, "AGM 2026 motion #3",
        )
        ctx = self._appt_session(dup_exists=False, return_row=appointment_row)
        with patch("db_postgres.session.async_session_context", return_value=ctx):
            from services.management_hierarchy_service import create_manager_appointment
            record = await create_manager_appointment(
                scheme_id=SCHEME_ID,
                user_id=USER_ID,
                management_entity_id=ENTITY_ID,
                appointment_type="ec_internal_strata_manager",
                role_title="Internal Strata Manager",
                appointed_by=ACTOR_ID,
                approved_by_ec=True,
                approval_reference="AGM 2026 motion #3",
            )
        assert record.appointment_type == "ec_internal_strata_manager"
        assert record.approved_by_ec is True

    @pytest.mark.asyncio
    async def test_create_building_manager_appointment(self):
        appointment_row = (
            "appt-002", SCHEME_ID, USER_ID, ENTITY_ID,
            "building_manager", "Building Manager",
            "active", True, date.today(), None, None,
        )
        ctx = self._appt_session(dup_exists=False, return_row=appointment_row)
        with patch("db_postgres.session.async_session_context", return_value=ctx):
            from services.management_hierarchy_service import create_manager_appointment
            record = await create_manager_appointment(
                scheme_id=SCHEME_ID,
                user_id=USER_ID,
                management_entity_id=ENTITY_ID,
                appointment_type="building_manager",
                role_title="Building Manager",
                appointed_by=ACTOR_ID,
            )
        assert record.appointment_type == "building_manager"

    @pytest.mark.asyncio
    async def test_invalid_appointment_type_raises(self):
        from services.management_hierarchy_service import create_manager_appointment
        with pytest.raises(ValueError, match="Invalid appointment_type"):
            await create_manager_appointment(
                scheme_id=SCHEME_ID,
                user_id=USER_ID,
                management_entity_id=ENTITY_ID,
                appointment_type="random_invalid_type",
                role_title="Test",
                appointed_by=ACTOR_ID,
            )

    @pytest.mark.asyncio
    async def test_duplicate_appointment_raises(self):
        ctx = self._appt_session(dup_exists=True, return_row=())
        with patch("db_postgres.session.async_session_context", return_value=ctx):
            from services.management_hierarchy_service import create_manager_appointment
            with pytest.raises(RuntimeError, match="already exists"):
                await create_manager_appointment(
                    scheme_id=SCHEME_ID,
                    user_id=USER_ID,
                    management_entity_id=ENTITY_ID,
                    appointment_type="ec_internal_strata_manager",
                    role_title="Manager",
                    appointed_by=ACTOR_ID,
                )

    @pytest.mark.asyncio
    async def test_tenant_appointment_requires_valid_type(self):
        """Tenant cannot become a manager; invalid type raises ValueError."""
        from services.management_hierarchy_service import create_manager_appointment
        with pytest.raises(ValueError):
            await create_manager_appointment(
                scheme_id=SCHEME_ID,
                user_id=USER_ID,
                management_entity_id=ENTITY_ID,
                appointment_type="tenant_manager",  # not a valid type
                role_title="Tenant Manager",
                appointed_by=ACTOR_ID,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Part D — BI toggle resolution
# ─────────────────────────────────────────────────────────────────────────────

class TestBIToggleResolutionManagementEntity:
    """Part D: BI toggle resolver for management entity types."""

    @pytest.mark.asyncio
    async def test_building_override_wins(self):
        with patch(
            "services.bi_toggle_service._resolve_pg_primary", new=AsyncMock(return_value=False)
        ), patch(
            "services.bi_toggle_service._resolve_management_entity_id",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.bi_toggle_service._resolve_building_toggle_with_source",
            new=AsyncMock(return_value=(True, False)),
        ):
            from services.bi_toggle_service import resolve_bi_feature_access
            result = await resolve_bi_feature_access(
                user_id=USER_ID,
                role="ec_member",
                effective_role="ec_member",
                agency_id=None,
                building_id=BUILDING_ID,
            )
        assert result.enabled is True
        assert result.source == "building_override"

    @pytest.mark.asyncio
    async def test_management_entity_override_works(self):
        with patch(
            "services.bi_toggle_service._resolve_pg_primary", new=AsyncMock(return_value=False)
        ), patch(
            "services.bi_toggle_service._resolve_management_entity_id",
            new=AsyncMock(return_value=ENTITY_ID),
        ), patch(
            "services.bi_toggle_service._resolve_building_toggle_with_source",
            new=AsyncMock(return_value=(False, False)),
        ), patch(
            "services.bi_toggle_service._resolve_management_entity_toggle",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.bi_toggle_service._get_management_entity_type",
            new=AsyncMock(return_value="agency"),
        ):
            from services.bi_toggle_service import resolve_bi_feature_access
            result = await resolve_bi_feature_access(
                user_id=USER_ID,
                role="ec_member",
                effective_role="ec_member",
                agency_id=None,
                building_id=BUILDING_ID,
            )
        assert result.enabled is True
        assert result.source == "management_entity_override"

    @pytest.mark.asyncio
    async def test_self_managed_oc_requires_appointment(self):
        with patch(
            "services.bi_toggle_service._resolve_pg_primary", new=AsyncMock(return_value=False)
        ), patch(
            "services.bi_toggle_service._resolve_management_entity_id",
            new=AsyncMock(return_value=ENTITY_ID),
        ), patch(
            "services.bi_toggle_service._resolve_building_toggle_with_source",
            new=AsyncMock(return_value=(False, False)),
        ), patch(
            "services.bi_toggle_service._resolve_management_entity_toggle",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.bi_toggle_service._get_management_entity_type",
            new=AsyncMock(return_value="self_managed_oc"),
        ), patch(
            "services.bi_toggle_service._user_has_active_appointment",
            new=AsyncMock(return_value=False),  # no appointment
        ), patch(
            "services.bi_toggle_service._resolve_platform_toggle",
            new=AsyncMock(return_value=False),
        ), patch(
            "services.bi_toggle_service._resolve_global_toggle",
            new=AsyncMock(return_value=False),
        ):
            from services.bi_toggle_service import resolve_bi_feature_access
            result = await resolve_bi_feature_access(
                user_id=USER_ID,
                role="ec_member",
                effective_role="ec_member",
                agency_id=None,
                building_id=BUILDING_ID,
            )
        assert result.enabled is False
        assert "appointment" in result.warnings[0]

    @pytest.mark.asyncio
    async def test_self_managed_oc_bi_works_with_appointment(self):
        with patch(
            "services.bi_toggle_service._resolve_pg_primary", new=AsyncMock(return_value=False)
        ), patch(
            "services.bi_toggle_service._resolve_management_entity_id",
            new=AsyncMock(return_value=ENTITY_ID),
        ), patch(
            "services.bi_toggle_service._resolve_building_toggle_with_source",
            new=AsyncMock(return_value=(False, False)),
        ), patch(
            "services.bi_toggle_service._resolve_management_entity_toggle",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.bi_toggle_service._get_management_entity_type",
            new=AsyncMock(return_value="self_managed_oc"),
        ), patch(
            "services.bi_toggle_service._user_has_active_appointment",
            new=AsyncMock(return_value=True),
        ):
            from services.bi_toggle_service import resolve_bi_feature_access
            result = await resolve_bi_feature_access(
                user_id=USER_ID,
                role="ec_member",
                effective_role="ec_member",
                agency_id=None,
                building_id=BUILDING_ID,
            )
        assert result.enabled is True
        assert result.scope == "management_entity"

    @pytest.mark.asyncio
    async def test_independent_manager_portfolio_bi(self):
        with patch(
            "services.bi_toggle_service._resolve_pg_primary", new=AsyncMock(return_value=False)
        ), patch(
            "services.bi_toggle_service._resolve_platform_toggle",
            new=AsyncMock(return_value=True),
        ):
            from services.bi_toggle_service import resolve_bi_feature_access
            result = await resolve_bi_feature_access(
                user_id=USER_ID,
                role="strata_manager",
                effective_role="strata_manager",
                agency_id=None,
                building_id=BUILDING_ID,
            )
        assert result.enabled is True
        assert result.scope == "portfolio"

    @pytest.mark.asyncio
    async def test_safe_default_false_for_unknown_role(self):
        with patch(
            "services.bi_toggle_service._resolve_pg_primary", new=AsyncMock(return_value=False)
        ), patch(
            "services.bi_toggle_service._resolve_management_entity_id",
            new=AsyncMock(return_value=None),
        ):
            from services.bi_toggle_service import resolve_bi_feature_access
            result = await resolve_bi_feature_access(
                user_id=USER_ID,
                role="guest",
                effective_role="guest",
                agency_id=None,
                building_id=None,
            )
        assert result.enabled is False
        assert result.source == "safe_default"


# ─────────────────────────────────────────────────────────────────────────────
# Part E — Access control
# ─────────────────────────────────────────────────────────────────────────────

class TestManagementEntityAccess:
    """Part E: access control per role."""

    @pytest.mark.asyncio
    async def test_super_admin_can_access_any_entity(self):
        with patch(
            "services.bi_toggle_service._resolve_management_entity_toggle",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.bi_toggle_service._resolve_global_toggle",
            new=AsyncMock(return_value=False),
        ):
            from services.bi_toggle_service import resolve_management_entity_access
            ok = await resolve_management_entity_access(
                user_id=USER_ID,
                effective_role="super_admin",
                management_entity_id=ENTITY_ID,
            )
        assert ok is True

    @pytest.mark.asyncio
    async def test_strata_admin_sees_own_agency_entity(self):
        with patch(
            "services.bi_toggle_service._resolve_management_entity_toggle",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.bi_toggle_service._resolve_global_toggle",
            new=AsyncMock(return_value=False),
        ), patch(
            "services.bi_toggle_service._get_management_entity_type",
            new=AsyncMock(return_value="agency"),
        ), patch(
            "services.bi_toggle_service._get_agency_id_for_entity",
            new=AsyncMock(return_value=AGENCY_ID),
        ), patch(
            "services.bi_toggle_service._user_belongs_to_agency",
            new=AsyncMock(return_value=True),
        ):
            from services.bi_toggle_service import resolve_management_entity_access
            ok = await resolve_management_entity_access(
                user_id=USER_ID,
                effective_role="strata_admin",
                management_entity_id=ENTITY_ID,
            )
        assert ok is True

    @pytest.mark.asyncio
    async def test_ec_member_without_appointment_denied_entity(self):
        with patch(
            "services.bi_toggle_service._resolve_management_entity_toggle",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.bi_toggle_service._resolve_global_toggle",
            new=AsyncMock(return_value=False),
        ), patch(
            "services.bi_toggle_service._user_has_appointment_for_entity",
            new=AsyncMock(return_value=False),
        ):
            from services.bi_toggle_service import resolve_management_entity_access
            ok = await resolve_management_entity_access(
                user_id=USER_ID,
                effective_role="ec_member",
                management_entity_id=ENTITY_ID,
            )
        assert ok is False

    @pytest.mark.asyncio
    async def test_owner_cannot_access_management_entity(self):
        with patch(
            "services.bi_toggle_service._resolve_management_entity_toggle",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.bi_toggle_service._resolve_global_toggle",
            new=AsyncMock(return_value=False),
        ):
            from services.bi_toggle_service import resolve_management_entity_access
            ok = await resolve_management_entity_access(
                user_id=USER_ID,
                effective_role="owner",
                management_entity_id=ENTITY_ID,
            )
        assert ok is False

    @pytest.mark.asyncio
    async def test_toggle_off_denies_all(self):
        with patch(
            "services.bi_toggle_service._resolve_management_entity_toggle",
            new=AsyncMock(return_value=False),
        ), patch(
            "services.bi_toggle_service._resolve_global_toggle",
            new=AsyncMock(return_value=False),
        ):
            from services.bi_toggle_service import resolve_management_entity_access
            ok = await resolve_management_entity_access(
                user_id=USER_ID,
                effective_role="super_admin",
                management_entity_id=ENTITY_ID,
            )
        assert ok is False


# ─────────────────────────────────────────────────────────────────────────────
# Part F — ECPosition model extensions
# ─────────────────────────────────────────────────────────────────────────────

class TestECPositionExtensions:
    """Part F: ECPosition constants and helpers."""

    def test_new_positions_exist(self):
        from models.user import ECPosition
        assert ECPosition.STRATA_MANAGER == "STRATA_MANAGER"
        assert ECPosition.BUILDING_MANAGER == "BUILDING_MANAGER"

    def test_legacy_positions_unchanged(self):
        from models.user import ECPosition
        assert ECPosition.CHAIRMAN == "CHAIRMAN"
        assert ECPosition.TREASURER == "TREASURER"
        assert ECPosition.SECRETARY == "SECRETARY"
        assert ECPosition.MEMBER == "MEMBER"

    def test_all_contains_all_positions(self):
        from models.user import ECPosition
        assert "CHAIRMAN" in ECPosition.ALL
        assert "STRATA_MANAGER" in ECPosition.ALL
        assert "BUILDING_MANAGER" in ECPosition.ALL
        assert len(ECPosition.ALL) == 6

    def test_is_valid(self):
        from models.user import ECPosition
        assert ECPosition.is_valid("CHAIRMAN")
        assert ECPosition.is_valid("STRATA_MANAGER")
        assert not ECPosition.is_valid("DIRECTOR")
        assert not ECPosition.is_valid("chairman")  # case-sensitive

    def test_requires_appointment(self):
        from models.user import ECPosition
        assert ECPosition.requires_appointment("STRATA_MANAGER")
        assert ECPosition.requires_appointment("BUILDING_MANAGER")
        assert not ECPosition.requires_appointment("CHAIRMAN")
        assert not ECPosition.requires_appointment("MEMBER")


# ─────────────────────────────────────────────────────────────────────────────
# Part G — Access hardening: IDOR, role isolation, UUID validation, field filtering
# ─────────────────────────────────────────────────────────────────────────────

FOREIGN_SCHEME_ID = "11111111-aaaa-0000-0000-000000000001"
FOREIGN_ENTITY_ID = "22222222-bbbb-0000-0000-000000000002"
FOREIGN_BUILDING_ID = "99999"


def _make_user(role: str, building_id: str = BUILDING_ID) -> dict:
    return {
        "_id": USER_ID,
        "role": role,
        "effective_role": role,
        "is_approved": True,
        "building_id": building_id,
    }


class TestUUIDValidation:
    """Part G.1: Malformed UUIDs must return 400, never 500."""

    def test_parse_uuid_valid(self):
        from services.management_hierarchy_service import parse_uuid_or_400
        result = parse_uuid_or_400(SCHEME_ID, "scheme_id")
        assert result == SCHEME_ID

    def test_parse_uuid_malformed_raises_400(self):
        from services.management_hierarchy_service import parse_uuid_or_400
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            parse_uuid_or_400("not-a-uuid", "scheme_id")
        assert exc.value.status_code == 400
        assert "scheme_id" in exc.value.detail

    def test_parse_uuid_empty_raises_400(self):
        from services.management_hierarchy_service import parse_uuid_or_400
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            parse_uuid_or_400("", "entity_id")
        assert exc.value.status_code == 400

    def test_parse_uuid_sql_injection_raises_400(self):
        from services.management_hierarchy_service import parse_uuid_or_400
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            parse_uuid_or_400("'; DROP TABLE core.management_entities; --", "scheme_id")
        assert exc.value.status_code == 400

    def test_parse_uuid_partial_raises_400(self):
        from services.management_hierarchy_service import parse_uuid_or_400
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            parse_uuid_or_400("aaaaaaaa-0000-0000", "management_entity_id")
        assert exc.value.status_code == 400


class TestFieldFiltering:
    """Part G.2: Sensitive fields must be stripped for restricted callers."""

    def test_full_access_returns_all_fields(self):
        from services.management_hierarchy_service import filter_entity_fields
        entity = {
            "management_entity_id": ENTITY_ID,
            "entity_type": "agency",
            "name": "Test Agency",
            "status": "active",
            "abn": "12 345 678 901",
            "email": "admin@agency.com",
            "phone": "0400000000",
            "legal_name": "Test Agency Pty Ltd",
        }
        result = filter_entity_fields(entity, full_access=True)
        assert result["abn"] == "12 345 678 901"
        assert result["email"] == "admin@agency.com"
        assert result["phone"] == "0400000000"
        assert result["legal_name"] == "Test Agency Pty Ltd"

    def test_restricted_access_strips_sensitive_fields(self):
        from services.management_hierarchy_service import filter_entity_fields
        entity = {
            "management_entity_id": ENTITY_ID,
            "entity_type": "agency",
            "name": "Test Agency",
            "status": "active",
            "abn": "12 345 678 901",
            "email": "admin@agency.com",
            "phone": "0400000000",
            "legal_name": "Test Agency Pty Ltd",
        }
        result = filter_entity_fields(entity, full_access=False)
        assert "abn" not in result
        assert "email" not in result
        assert "phone" not in result
        assert "legal_name" not in result
        # Public fields remain
        assert result["management_entity_id"] == ENTITY_ID
        assert result["name"] == "Test Agency"
        assert result["status"] == "active"

    def test_restricted_access_preserves_public_fields(self):
        from services.management_hierarchy_service import filter_entity_fields
        entity = {
            "management_entity_id": ENTITY_ID,
            "entity_type": "self_managed_oc",
            "name": "My OC",
            "status": "active",
            "is_system_generated": True,
            "is_self_managed": True,
        }
        result = filter_entity_fields(entity, full_access=False)
        assert result["entity_type"] == "self_managed_oc"
        assert result["is_self_managed"] is True


class TestAccessHelperSuperAdmin:
    """Part G.3: super_admin always has platform access."""

    @pytest.mark.asyncio
    async def test_super_admin_scheme_access(self):
        user = _make_user("super_admin")
        with patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            ctx = await require_management_hierarchy_access(
                current_user=user,
                scheme_id=SCHEME_ID,
                action="read",
            )
        assert ctx["allowed"] is True
        assert ctx["scope"] == "platform"

    @pytest.mark.asyncio
    async def test_super_admin_entity_access(self):
        user = _make_user("super_admin")
        with patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            ctx = await require_management_hierarchy_access(
                current_user=user,
                management_entity_id=ENTITY_ID,
                action="read",
            )
        assert ctx["allowed"] is True
        assert ctx["scope"] == "platform"

    @pytest.mark.asyncio
    async def test_super_admin_write_access(self):
        user = _make_user("super_admin")
        with patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            ctx = await require_management_hierarchy_access(
                current_user=user,
                scheme_id=SCHEME_ID,
                action="write",
            )
        assert ctx["allowed"] is True


class TestAccessHelperECMemberOwnScheme:
    """Part G.4: EC member can read own scheme and its entity; denied for foreign."""

    @pytest.mark.asyncio
    async def test_ec_member_own_scheme_allowed(self):
        user = _make_user("ec_member", building_id=BUILDING_ID)
        with patch(
            "services.management_hierarchy_service._get_scheme_number",
            new=AsyncMock(return_value=BUILDING_ID),
        ), patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            ctx = await require_management_hierarchy_access(
                current_user=user,
                scheme_id=SCHEME_ID,
                action="read",
            )
        assert ctx["allowed"] is True
        assert ctx["scope"] == "scheme"

    @pytest.mark.asyncio
    async def test_ec_member_foreign_scheme_denied(self):
        """IDOR: EC member of scheme A cannot read scheme B by UUID."""
        user = _make_user("ec_member", building_id=BUILDING_ID)
        with patch(
            "services.management_hierarchy_service._get_scheme_number",
            new=AsyncMock(return_value=FOREIGN_BUILDING_ID),  # different building
        ), patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await require_management_hierarchy_access(
                    current_user=user,
                    scheme_id=FOREIGN_SCHEME_ID,
                    action="read",
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_ec_member_write_denied(self):
        """EC member cannot write management hierarchy data."""
        user = _make_user("ec_member", building_id=BUILDING_ID)
        with patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await require_management_hierarchy_access(
                    current_user=user,
                    scheme_id=SCHEME_ID,
                    action="write",
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_ec_member_own_entity_allowed(self):
        """EC member can read an entity that is assigned to their own scheme."""
        user = _make_user("ec_member", building_id=BUILDING_ID)
        with patch(
            "services.management_hierarchy_service._is_entity_linked_to_building",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            ctx = await require_management_hierarchy_access(
                current_user=user,
                management_entity_id=ENTITY_ID,
                action="read",
            )
        assert ctx["allowed"] is True
        assert ctx["scope"] == "building"

    @pytest.mark.asyncio
    async def test_ec_member_foreign_entity_denied(self):
        """IDOR: EC member cannot read arbitrary entity UUIDs."""
        user = _make_user("ec_member", building_id=BUILDING_ID)
        with patch(
            "services.management_hierarchy_service._is_entity_linked_to_building",
            new=AsyncMock(return_value=False),  # not their entity
        ), patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await require_management_hierarchy_access(
                    current_user=user,
                    management_entity_id=FOREIGN_ENTITY_ID,
                    action="read",
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_ec_member_entity_write_denied(self):
        """EC member cannot write entity data even for own scheme's entity."""
        user = _make_user("ec_member", building_id=BUILDING_ID)
        with patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await require_management_hierarchy_access(
                    current_user=user,
                    management_entity_id=ENTITY_ID,
                    action="write",
                )
        assert exc.value.status_code == 403


class TestAccessHelperAgencyAdmin:
    """Part G.5: Agency admin (strata_admin) access isolation."""

    @pytest.mark.asyncio
    async def test_agency_admin_own_entity_allowed(self):
        user = _make_user("strata_admin")
        with patch(
            "services.management_hierarchy_service._get_scheme_number",
            new=AsyncMock(return_value=BUILDING_ID),
        ), patch(
            "services.management_hierarchy_service._user_has_scheme_appointment",
            new=AsyncMock(return_value=False),
        ), patch(
            "services.management_hierarchy_service._get_scheme_entity_id",
            new=AsyncMock(return_value=ENTITY_ID),
        ), patch(
            "services.management_hierarchy_service._get_agency_id_for_entity_pg",
            new=AsyncMock(return_value=AGENCY_ID),
        ), patch(
            "services.management_hierarchy_service._user_belongs_to_agency_pg",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            ctx = await require_management_hierarchy_access(
                current_user=user,
                scheme_id=SCHEME_ID,
                action="read",
            )
        assert ctx["allowed"] is True
        assert ctx["scope"] == "agency"

    @pytest.mark.asyncio
    async def test_agency_admin_foreign_entity_denied(self):
        """IDOR: strata_admin cannot access another agency's entity."""
        user = _make_user("strata_admin")
        with patch(
            "services.management_hierarchy_service._get_scheme_number",
            new=AsyncMock(return_value=FOREIGN_BUILDING_ID),
        ), patch(
            "services.management_hierarchy_service._user_has_scheme_appointment",
            new=AsyncMock(return_value=False),
        ), patch(
            "services.management_hierarchy_service._get_scheme_entity_id",
            new=AsyncMock(return_value=FOREIGN_ENTITY_ID),
        ), patch(
            "services.management_hierarchy_service._get_agency_id_for_entity_pg",
            new=AsyncMock(return_value="00000000-dead-dead-dead-000000000000"),
        ), patch(
            "services.management_hierarchy_service._user_belongs_to_agency_pg",
            new=AsyncMock(return_value=False),  # different agency
        ), patch(
            "services.management_hierarchy_service._user_has_appointment_for_entity_pg",
            new=AsyncMock(return_value=False),
        ), patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await require_management_hierarchy_access(
                    current_user=user,
                    scheme_id=FOREIGN_SCHEME_ID,
                    action="read",
                )
        assert exc.value.status_code == 403


class TestAccessHelperIndependentManager:
    """Part G.6: Independent strata manager access isolation."""

    @pytest.mark.asyncio
    async def test_independent_manager_assigned_scheme_allowed(self):
        user = _make_user("strata_manager")
        with patch(
            "services.management_hierarchy_service._get_scheme_number",
            new=AsyncMock(return_value=BUILDING_ID),
        ), patch(
            "services.management_hierarchy_service._user_has_scheme_appointment",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            ctx = await require_management_hierarchy_access(
                current_user=user,
                scheme_id=SCHEME_ID,
                action="read",
            )
        assert ctx["allowed"] is True
        assert ctx["scope"] == "scheme"

    @pytest.mark.asyncio
    async def test_independent_manager_unassigned_scheme_denied(self):
        """IDOR: strata_manager cannot read schemes they're not appointed to."""
        user = _make_user("strata_manager")
        with patch(
            "services.management_hierarchy_service._get_scheme_number",
            new=AsyncMock(return_value=FOREIGN_BUILDING_ID),
        ), patch(
            "services.management_hierarchy_service._user_has_scheme_appointment",
            new=AsyncMock(return_value=False),
        ), patch(
            "services.management_hierarchy_service._get_scheme_entity_id",
            new=AsyncMock(return_value=FOREIGN_ENTITY_ID),
        ), patch(
            "services.management_hierarchy_service._get_agency_id_for_entity_pg",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.management_hierarchy_service._user_has_appointment_for_entity_pg",
            new=AsyncMock(return_value=False),
        ), patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await require_management_hierarchy_access(
                    current_user=user,
                    scheme_id=FOREIGN_SCHEME_ID,
                    action="read",
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_independent_manager_own_entity_allowed(self):
        """Manager with appointment under entity can access that entity."""
        user = _make_user("strata_manager")
        with patch(
            "services.management_hierarchy_service._get_management_entity_type_pg",
            new=AsyncMock(return_value="independent_manager"),
        ), patch(
            "services.management_hierarchy_service._get_agency_id_for_entity_pg",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.management_hierarchy_service._user_has_appointment_for_entity_pg",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            ctx = await require_management_hierarchy_access(
                current_user=user,
                management_entity_id=ENTITY_ID,
                action="read",
            )
        assert ctx["allowed"] is True
        assert ctx["scope"] == "management_entity"

    @pytest.mark.asyncio
    async def test_independent_manager_foreign_entity_denied(self):
        """IDOR: strata_manager cannot read unassigned management entities."""
        user = _make_user("strata_manager")
        with patch(
            "services.management_hierarchy_service._get_management_entity_type_pg",
            new=AsyncMock(return_value="independent_manager"),
        ), patch(
            "services.management_hierarchy_service._get_agency_id_for_entity_pg",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.management_hierarchy_service._user_has_appointment_for_entity_pg",
            new=AsyncMock(return_value=False),
        ), patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await require_management_hierarchy_access(
                    current_user=user,
                    management_entity_id=FOREIGN_ENTITY_ID,
                    action="read",
                )
        assert exc.value.status_code == 403


class TestAccessHelperOwnerTenant:
    """Part G.7: Owner and tenant are always denied."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("role", ["owner", "tenant", "guest", "real_estate_agent"])
    async def test_non_manager_roles_denied(self, role: str):
        user = _make_user(role)
        with patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await require_management_hierarchy_access(
                    current_user=user,
                    scheme_id=SCHEME_ID,
                    action="read",
                )
        assert exc.value.status_code == 403


class TestAccessHelperAuditLogging:
    """Part G.8: Allowed and denied accesses are audit-logged."""

    @pytest.mark.asyncio
    async def test_allowed_read_emits_audit_event(self):
        user = _make_user("super_admin")
        mock_emit = AsyncMock()
        with patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=mock_emit,
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            await require_management_hierarchy_access(
                current_user=user,
                scheme_id=SCHEME_ID,
                action="read",
            )
        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args.kwargs
        assert call_kwargs["allowed"] is True
        assert call_kwargs["action"] == "read"
        assert call_kwargs["scheme_id"] == SCHEME_ID

    @pytest.mark.asyncio
    async def test_denied_read_emits_audit_event(self):
        user = _make_user("owner")
        mock_emit = AsyncMock()
        with patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=mock_emit,
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            from fastapi import HTTPException
            with pytest.raises(HTTPException):
                await require_management_hierarchy_access(
                    current_user=user,
                    scheme_id=SCHEME_ID,
                    action="read",
                )
        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args.kwargs
        assert call_kwargs["allowed"] is False
        assert "owner" in call_kwargs["reason"] or "role" in call_kwargs["reason"]

    @pytest.mark.asyncio
    async def test_foreign_scheme_denial_emits_audit_event(self):
        user = _make_user("ec_member", building_id=BUILDING_ID)
        mock_emit = AsyncMock()
        with patch(
            "services.management_hierarchy_service._get_scheme_number",
            new=AsyncMock(return_value=FOREIGN_BUILDING_ID),
        ), patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=mock_emit,
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            from fastapi import HTTPException
            with pytest.raises(HTTPException):
                await require_management_hierarchy_access(
                    current_user=user,
                    scheme_id=FOREIGN_SCHEME_ID,
                    action="read",
                )
        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args.kwargs
        assert call_kwargs["allowed"] is False
        assert call_kwargs["scheme_id"] == FOREIGN_SCHEME_ID


class TestAccessHelperSelfManagedOC:
    """Part G.9: Self-managed OC — EC member can read own scheme; cannot access agency portfolio."""

    @pytest.mark.asyncio
    async def test_self_managed_oc_ec_reads_own_scheme(self):
        user = _make_user("ec_member", building_id=BUILDING_ID)
        with patch(
            "services.management_hierarchy_service._get_scheme_number",
            new=AsyncMock(return_value=BUILDING_ID),
        ), patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            ctx = await require_management_hierarchy_access(
                current_user=user,
                scheme_id=SCHEME_ID,
                action="read",
            )
        assert ctx["allowed"] is True
        assert ctx["scope"] == "scheme"

    @pytest.mark.asyncio
    async def test_self_managed_oc_ec_cannot_access_agency_portfolio(self):
        """EC member of a self-managed OC cannot access agency entity."""
        user = _make_user("ec_member", building_id=BUILDING_ID)
        with patch(
            "services.management_hierarchy_service._is_entity_linked_to_building",
            new=AsyncMock(return_value=False),  # agency entity not linked to their building
        ), patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await require_management_hierarchy_access(
                    current_user=user,
                    management_entity_id=ENTITY_ID,
                    action="read",
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_self_managed_oc_ec_cannot_access_foreign_oc(self):
        """EC member of OC-A cannot read OC-B entity by UUID."""
        user = _make_user("ec_member", building_id=BUILDING_ID)
        with patch(
            "services.management_hierarchy_service._is_entity_linked_to_building",
            new=AsyncMock(return_value=False),
        ), patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await require_management_hierarchy_access(
                    current_user=user,
                    management_entity_id=FOREIGN_ENTITY_ID,
                    action="read",
                )
        assert exc.value.status_code == 403


class TestAccessHelperManagerSelfQuery:
    """Part G.10: GET /managers/{user_id}/buildings access rules."""

    @pytest.mark.asyncio
    async def test_self_access_allowed(self):
        user = _make_user("strata_manager")
        user["_id"] = USER_ID
        with patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            ctx = await require_management_hierarchy_access(
                current_user=user,
                target_user_id=USER_ID,
                action="read",
            )
        assert ctx["allowed"] is True
        assert ctx["scope"] == "building"

    @pytest.mark.asyncio
    async def test_strata_admin_can_query_other_manager(self):
        user = _make_user("strata_admin")
        other_manager_id = "77777777-0000-0000-0000-000000000007"
        with patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            ctx = await require_management_hierarchy_access(
                current_user=user,
                target_user_id=other_manager_id,
                action="read",
            )
        assert ctx["allowed"] is True
        assert ctx["scope"] == "agency"

    @pytest.mark.asyncio
    async def test_strata_manager_cannot_query_other_manager(self):
        """strata_manager cannot enumerate another manager's buildings."""
        user = _make_user("strata_manager")
        user["_id"] = USER_ID
        other_manager_id = "77777777-0000-0000-0000-000000000007"
        with patch(
            "services.management_hierarchy_service._emit_access_audit_event",
            new=AsyncMock(),
        ):
            from services.management_hierarchy_service import require_management_hierarchy_access
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await require_management_hierarchy_access(
                    current_user=user,
                    target_user_id=other_manager_id,
                    action="read",
                )
        assert exc.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# Part H — Router smoke tests (updated for hardened router)
# ─────────────────────────────────────────────────────────────────────────────

class TestManagementHierarchyRouter:
    """Smoke tests for hardened management_hierarchy router."""

    @pytest.mark.asyncio
    async def test_ensure_owner_raises_403(self):
        """POST /ensure rejects owner role before access helper is called."""
        from routers.management_hierarchy import ensure_hierarchy, EnsureHierarchyRequest
        from fastapi import HTTPException

        caller = {"_id": ACTOR_ID, "role": "owner", "effective_role": "owner",
                  "is_approved": True, "building_id": BUILDING_ID}
        body = EnsureHierarchyRequest(mode="self_managed")
        with pytest.raises(HTTPException) as exc:
            await ensure_hierarchy(
                scheme_id=SCHEME_ID,
                body=body,
                current_user=caller,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_ensure_super_admin_with_valid_uuid(self):
        """POST /ensure with valid UUID and super_admin passes through to service."""
        from routers.management_hierarchy import ensure_hierarchy, EnsureHierarchyRequest
        from fastapi import HTTPException
        from services.management_hierarchy_service import (
            ManagementEntityRecord, ManagementAssignmentRecord, ManagementHierarchyResult,
        )

        caller = {"_id": ACTOR_ID, "role": "super_admin", "effective_role": "super_admin",
                  "is_approved": True, "building_id": BUILDING_ID}
        body = EnsureHierarchyRequest(mode="self_managed")
        mock_entity = ManagementEntityRecord(
            management_entity_id=ENTITY_ID, entity_type="self_managed_oc",
            name="Test OC", status="active", is_system_generated=True,
            is_self_managed=True, is_independent=False,
        )
        mock_assignment = ManagementAssignmentRecord(
            assignment_id=ASSIGNMENT_ID, scheme_id=SCHEME_ID,
            management_entity_id=ENTITY_ID, assignment_type="self_managed",
            status="active", is_primary=True,
        )
        mock_result = ManagementHierarchyResult(
            entity=mock_entity, assignment=mock_assignment, created=True, warnings=[],
        )
        with patch(
            "services.management_hierarchy_service.require_management_hierarchy_access",
            new=AsyncMock(return_value={"allowed": True, "scope": "platform",
                                        "reason": "super_admin"}),
        ), patch(
            "services.management_hierarchy_service.ensure_scheme_management_hierarchy",
            new=AsyncMock(return_value=mock_result),
        ):
            result = await ensure_hierarchy(
                scheme_id=SCHEME_ID,
                body=body,
                current_user=caller,
            )
        assert result["created"] is True
        assert result["entity"]["entity_type"] == "self_managed_oc"

    @pytest.mark.asyncio
    async def test_ensure_malformed_uuid_raises_400(self):
        """POST /ensure with malformed scheme_id returns 400."""
        from routers.management_hierarchy import ensure_hierarchy, EnsureHierarchyRequest
        from fastapi import HTTPException

        caller = {"_id": ACTOR_ID, "role": "super_admin", "effective_role": "super_admin",
                  "is_approved": True, "building_id": BUILDING_ID}
        body = EnsureHierarchyRequest(mode="self_managed")
        with pytest.raises(HTTPException) as exc:
            await ensure_hierarchy(
                scheme_id="not-a-valid-uuid",
                body=body,
                current_user=caller,
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_get_manager_buildings_self_access(self):
        """GET /managers/{user_id}/buildings allows self-access."""
        from routers.management_hierarchy import get_manager_buildings

        caller = {"_id": USER_ID, "role": "strata_manager", "effective_role": "strata_manager",
                  "is_approved": True, "building_id": BUILDING_ID}
        with patch(
            "services.management_hierarchy_service.require_management_hierarchy_access",
            new=AsyncMock(return_value={"allowed": True, "scope": "building", "reason": "self"}),
        ), patch("db_postgres.session.async_session_context") as mock_ctx:
            session = MagicMock()
            session.__aenter__ = AsyncMock(return_value=session)
            session.__aexit__ = AsyncMock(return_value=None)
            mock_result = MagicMock()
            mock_result.fetchall = MagicMock(return_value=[("13195",)])
            session.execute = AsyncMock(return_value=mock_result)
            mock_ctx.return_value = session
            result = await get_manager_buildings(user_id=USER_ID, current_user=caller)
        assert result["user_id"] == USER_ID
        assert "13195" in result["building_ids"]

    @pytest.mark.asyncio
    async def test_get_manager_buildings_malformed_uuid_raises_400(self):
        """GET /managers/{user_id}/buildings with bad UUID returns 400."""
        from routers.management_hierarchy import get_manager_buildings
        from fastapi import HTTPException

        caller = {"_id": USER_ID, "role": "strata_manager", "effective_role": "strata_manager",
                  "is_approved": True, "building_id": BUILDING_ID}
        with pytest.raises(HTTPException) as exc:
            await get_manager_buildings(user_id="bad-uuid", current_user=caller)
        assert exc.value.status_code == 400

    def test_create_appointment_request_validates_type(self):
        from routers.management_hierarchy import CreateAppointmentRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CreateAppointmentRequest(
                user_id=USER_ID,
                management_entity_id=ENTITY_ID,
                appointment_type="invalid_type",
                role_title="Manager",
            )

    def test_ensure_hierarchy_request_validates_mode(self):
        from routers.management_hierarchy import EnsureHierarchyRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            EnsureHierarchyRequest(mode="bad_mode")
