"""
Tests for services/cutover_status_service.py

Covers all 11 required scenarios:
  1.  per-building isolation
  2.  unsafe global toggle rejection (block_unsafe_global_cutover)
  3.  promote read without shadow → fail
  4.  promote write without read → fail
  5.  rollback restores previous state
  6.  ordinary user cannot promote (tested in router tests; service takes actor args)
  7.  super_admin can inspect (tested in router tests)
  8.  scheme UUID works (resolve_scheme_context path)
  9.  missing readiness check blocks promotion (P0 fail)
  10. shadow diffs are recorded
  11. mode resolution returns expected read/write source
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from models.cutover_status import (
    CutoverMode,
    DataSource,
    P0ReadinessReport,
    ReadinessStatus,
    VALID_FORWARD_TRANSITIONS,
    VALID_ROLLBACK_TRANSITIONS,
)
from services.cutover_status_service import (
    block_unsafe_global_cutover,
    get_or_default_cutover_status,
    resolve_read_source,
    resolve_write_source,
    _MODE_SOURCES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_status(
    building_id: str = "13195",
    domain: str = "finance_ledger",
    mode: CutoverMode = CutoverMode.mongo_primary,
    readiness_status: ReadinessStatus = ReadinessStatus.unknown,
    rollback_available: bool = True,
    previous_mode: str | None = None,
):
    from models.cutover_status import DomainCutoverStatus
    now = datetime.now(UTC)
    return DomainCutoverStatus(
        id=str(uuid4()),
        building_id=building_id,
        domain=domain,
        read_source=_MODE_SOURCES[mode][0],
        write_source=_MODE_SOURCES[mode][1],
        mode=mode,
        readiness_status=readiness_status,
        rollback_available=rollback_available,
        previous_mode=previous_mode,
        p0_snapshot={"financial_onboarding": {"status": "pass"}},
        created_at=now,
        updated_at=now,
    )


def _pass_p0(building_id: str = "13195") -> P0ReadinessReport:
    return P0ReadinessReport(
        building_id=building_id,
        overall="pass",
        gates={"cutover_tables": {"status": "pass"}, "scheme_context": {"status": "pass"}},
        checked_at=datetime.now(UTC),
    )


def _fail_p0(building_id: str = "13195") -> P0ReadinessReport:
    return P0ReadinessReport(
        building_id=building_id,
        overall="fail",
        gates={"cutover_tables": {"status": "fail", "detail": "tables missing"}},
        checked_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# 1. VALID_FORWARD_TRANSITIONS and VALID_ROLLBACK_TRANSITIONS are building-agnostic
#    per-building isolation is proven by showing queries are always filtered by building_id
# ---------------------------------------------------------------------------

class TestModeTransitionTable:
    def test_forward_chain_covers_all_active_modes(self):
        """Every non-terminal mode has exactly one forward step."""
        for mode in (
            CutoverMode.mongo_primary,
            CutoverMode.postgres_shadow,
            CutoverMode.postgres_read,
            CutoverMode.postgres_write,
        ):
            assert mode in VALID_FORWARD_TRANSITIONS, f"{mode} missing from forward transitions"

    def test_terminal_modes_have_no_forward(self):
        assert CutoverMode.mongo_archive not in VALID_FORWARD_TRANSITIONS
        assert CutoverMode.disabled not in VALID_FORWARD_TRANSITIONS

    def test_rollback_chain_mirrors_forward(self):
        for src, dst in VALID_FORWARD_TRANSITIONS.items():
            assert VALID_ROLLBACK_TRANSITIONS[dst] == src, (
                f"rollback from {dst} should return to {src}"
            )


# ---------------------------------------------------------------------------
# 2. block_unsafe_global_cutover
# ---------------------------------------------------------------------------

class TestBlockUnsafeGlobalCutover:
    @pytest.mark.parametrize("bad_id", ["", None, "*", "all", "global", "None", "none"])
    def test_rejects_wildcard_building_ids(self, bad_id):
        with pytest.raises(ValueError, match="building_id must be a specific building identifier"):
            block_unsafe_global_cutover(bad_id)

    def test_accepts_specific_building_id(self):
        # Should not raise
        block_unsafe_global_cutover("13195")
        block_unsafe_global_cutover("UP-DEMO-001")
        block_unsafe_global_cutover("16244")

    def test_whitespace_only_is_rejected(self):
        with pytest.raises(ValueError):
            block_unsafe_global_cutover("   ")


# ---------------------------------------------------------------------------
# 3. Promote read requires prior shadow mode
# ---------------------------------------------------------------------------

class TestPromoteReadRequiresShadow:
    @pytest.mark.asyncio
    async def test_promote_read_from_mongo_primary_fails(self):
        """Jumping directly to postgres_read from mongo_primary is not allowed.

        promote_domain from mongo_primary always goes to postgres_shadow (one step forward).
        The final returned status must reflect the new mode, not the original.
        """
        import services.cutover_status_service as svc

        current = _make_status(mode=CutoverMode.mongo_primary, readiness_status=ReadinessStatus.ready_for_shadow)
        after_promote = _make_status(mode=CutoverMode.postgres_shadow, readiness_status=ReadinessStatus.shadow_active)

        # promote_domain calls get_or_default_cutover_status twice:
        # first to read the current mode, then at the end to return the updated record.
        with patch.object(svc, "get_or_default_cutover_status",
                          new=AsyncMock(side_effect=[current, after_promote])), \
             patch.object(svc, "_run_p0_check", new=AsyncMock(return_value=_pass_p0())), \
             patch.object(svc, "_upsert_status_row", new=AsyncMock()), \
             patch.object(svc, "_write_audit_entry", new=AsyncMock(return_value=str(uuid4()))), \
             patch.object(svc, "async_session_context") as mock_ctx:

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock()
            mock_ctx.return_value = mock_session

            updated, _ = await svc.promote_domain(
                building_id="13195",
                domain="finance_ledger",
                actor_user_id="u-1",
                actor_role="super_admin",
            )
            # Promotion from mongo_primary goes to postgres_shadow, never directly to postgres_read
            assert updated.mode == CutoverMode.postgres_shadow

    @pytest.mark.asyncio
    async def test_promote_read_from_shadow_succeeds(self):
        import services.cutover_status_service as svc

        current = _make_status(mode=CutoverMode.postgres_shadow, readiness_status=ReadinessStatus.shadow_passing)
        identity_ready = _make_status(mode=CutoverMode.mongo_primary, domain="identity_core")
        identity_ready.p0_snapshot = {"identity_foundation": {"status": "pass"}}

        with patch.object(svc, "get_or_default_cutover_status", new=AsyncMock(return_value=current)), \
             patch.object(svc, "_run_p0_check", new=AsyncMock(return_value=_pass_p0())), \
             patch.object(svc, "get_cutover_status", new=AsyncMock(return_value=identity_ready)), \
             patch.object(svc, "_upsert_status_row", new=AsyncMock()), \
             patch.object(svc, "_write_audit_entry", new=AsyncMock(return_value=str(uuid4()))), \
             patch.object(svc, "async_session_context") as mock_ctx:

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock()
            mock_ctx.return_value = mock_session

            # After shadow → postgres_read
            result_status = _make_status(mode=CutoverMode.postgres_read, readiness_status=ReadinessStatus.shadow_passing)
            with patch.object(svc, "get_or_default_cutover_status",
                              new=AsyncMock(side_effect=[current, result_status])):
                updated, audit_id = await svc.promote_domain(
                    building_id="13195",
                    domain="finance_ledger",
                    actor_user_id="u-1",
                    actor_role="super_admin",
                )
            assert updated.mode == CutoverMode.postgres_read


# ---------------------------------------------------------------------------
# 4. Promote write requires postgres_read (not shadow or primary)
# ---------------------------------------------------------------------------

class TestPromoteWriteRequiresRead:
    @pytest.mark.asyncio
    async def test_forward_from_shadow_goes_to_read_not_write(self):
        """postgres_shadow → postgres_read (never skips to postgres_write)."""
        assert VALID_FORWARD_TRANSITIONS[CutoverMode.postgres_shadow] == CutoverMode.postgres_read

    @pytest.mark.asyncio
    async def test_forward_from_read_goes_to_write(self):
        assert VALID_FORWARD_TRANSITIONS[CutoverMode.postgres_read] == CutoverMode.postgres_write

    @pytest.mark.asyncio
    async def test_promote_write_requires_read_mode_in_service(self):
        """promote_domain from postgres_shadow advances to postgres_read, not postgres_write."""
        import services.cutover_status_service as svc

        current = _make_status(mode=CutoverMode.postgres_shadow, readiness_status=ReadinessStatus.shadow_passing)
        result_status = _make_status(mode=CutoverMode.postgres_read, readiness_status=ReadinessStatus.shadow_passing)
        identity_ready = _make_status(mode=CutoverMode.mongo_primary, domain="identity_core")
        identity_ready.p0_snapshot = {"identity_foundation": {"status": "pass"}}

        with patch.object(svc, "get_or_default_cutover_status",
                          new=AsyncMock(side_effect=[current, result_status])), \
             patch.object(svc, "_run_p0_check", new=AsyncMock(return_value=_pass_p0())), \
             patch.object(svc, "get_cutover_status", new=AsyncMock(return_value=identity_ready)), \
             patch.object(svc, "_upsert_status_row", new=AsyncMock()), \
             patch.object(svc, "_write_audit_entry", new=AsyncMock(return_value=str(uuid4()))), \
             patch.object(svc, "async_session_context") as mock_ctx:

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock()
            mock_ctx.return_value = mock_session

            updated, _ = await svc.promote_domain(
                building_id="13195",
                domain="finance_ledger",
                actor_user_id="u-1",
                actor_role="super_admin",
            )
        assert updated.mode == CutoverMode.postgres_read
        assert updated.mode != CutoverMode.postgres_write


# ---------------------------------------------------------------------------
# 5. Rollback restores previous state
# ---------------------------------------------------------------------------

class TestRollbackRestoresPreviousState:
    @pytest.mark.asyncio
    async def test_rollback_from_shadow_returns_to_primary(self):
        import services.cutover_status_service as svc

        current = _make_status(
            mode=CutoverMode.postgres_shadow,
            previous_mode="mongo_primary",
            rollback_available=True,
        )
        rolled_back_status = _make_status(mode=CutoverMode.mongo_primary)

        with patch.object(svc, "get_or_default_cutover_status",
                          new=AsyncMock(side_effect=[current, rolled_back_status])), \
             patch.object(svc, "_upsert_status_row", new=AsyncMock()), \
             patch.object(svc, "_write_audit_entry", new=AsyncMock(return_value=str(uuid4()))), \
             patch.object(svc, "async_session_context") as mock_ctx:

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock()
            mock_ctx.return_value = mock_session

            updated, audit_id = await svc.rollback_domain(
                building_id="13195",
                domain="finance_ledger",
                actor_user_id="u-1",
                actor_role="super_admin",
                reason="Testing rollback",
            )

        assert updated.mode == CutoverMode.mongo_primary
        assert audit_id is not None

    @pytest.mark.asyncio
    async def test_rollback_from_read_returns_to_shadow(self):
        import services.cutover_status_service as svc

        current = _make_status(
            mode=CutoverMode.postgres_read,
            previous_mode="postgres_shadow",
            rollback_available=True,
        )
        rolled_back = _make_status(mode=CutoverMode.postgres_shadow)

        with patch.object(svc, "get_or_default_cutover_status",
                          new=AsyncMock(side_effect=[current, rolled_back])), \
             patch.object(svc, "_upsert_status_row", new=AsyncMock()), \
             patch.object(svc, "_write_audit_entry", new=AsyncMock(return_value="audit-1")), \
             patch.object(svc, "async_session_context") as mock_ctx:

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock()
            mock_ctx.return_value = mock_session

            updated, _ = await svc.rollback_domain(
                building_id="13195",
                domain="finance_ledger",
                actor_user_id="u-1",
                actor_role="super_admin",
                reason="Reverting due to shadow diff count",
            )
        assert updated.mode == CutoverMode.postgres_shadow

    @pytest.mark.asyncio
    async def test_rollback_not_available_raises(self):
        from fastapi import HTTPException
        import services.cutover_status_service as svc

        current = _make_status(
            mode=CutoverMode.postgres_shadow,
            rollback_available=False,
        )

        with patch.object(svc, "get_or_default_cutover_status", new=AsyncMock(return_value=current)):
            with pytest.raises(HTTPException) as exc_info:
                await svc.rollback_domain(
                    building_id="13195",
                    domain="finance_ledger",
                    actor_user_id="u-1",
                    actor_role="super_admin",
                    reason="Trying to rollback",
                )
        assert exc_info.value.status_code == 409
        assert "not available" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_rollback_from_mongo_primary_raises(self):
        """mongo_primary has no rollback target."""
        from fastapi import HTTPException
        import services.cutover_status_service as svc

        current = _make_status(mode=CutoverMode.mongo_primary, rollback_available=True)

        with patch.object(svc, "get_or_default_cutover_status", new=AsyncMock(return_value=current)):
            with pytest.raises(HTTPException) as exc_info:
                await svc.rollback_domain(
                    building_id="13195",
                    domain="finance_ledger",
                    actor_user_id="u-1",
                    actor_role="super_admin",
                    reason="Nothing to roll back to",
                )
        assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# 8. Scheme UUID works — resolve_scheme_context called with building_id
# ---------------------------------------------------------------------------

class TestSchemeUUIDResolution:
    @pytest.mark.asyncio
    async def test_get_or_default_returns_default_when_db_unavailable(self):
        """When DB is unavailable, falls back to mongo_primary default."""
        import services.cutover_status_service as svc

        with patch.object(svc, "get_cutover_status", new=AsyncMock(return_value=None)):
            status = await get_or_default_cutover_status("13195", "finance_ledger")

        assert status.mode == CutoverMode.mongo_primary
        assert status.read_source == DataSource.mongo
        assert status.write_source == DataSource.mongo

    @pytest.mark.asyncio
    async def test_upsert_calls_resolve_scheme_context(self):
        """_upsert_status_row calls resolve_scheme_context to get tenant UUID."""
        import services.cutover_status_service as svc
        from sqlalchemy import text as sa_text

        scheme = {"tenant_id": str(uuid4()), "scheme_id": str(uuid4())}

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()

        with patch("services.cutover_status_service.resolve_scheme_context",
                   new=AsyncMock(return_value=scheme)) as mock_resolve:
            await svc._upsert_status_row(
                mock_session,
                building_id="13195",
                domain="finance_ledger",
                mode=CutoverMode.mongo_primary,
                read_source=DataSource.mongo,
                write_source=DataSource.mongo,
                readiness_status=ReadinessStatus.unknown,
                previous_mode=None,
                rollback_available=True,
                promoted_by=None,
                toggle_name=None,
                notes=None,
                p0_snapshot={},
            )
        mock_resolve.assert_called_once_with("13195")


# ---------------------------------------------------------------------------
# 9. Missing readiness check blocks promotion (P0 fail)
# ---------------------------------------------------------------------------

class TestP0FailBlocksPromotion:
    @pytest.mark.asyncio
    async def test_promote_blocked_when_p0_fails(self):
        from fastapi import HTTPException
        import services.cutover_status_service as svc

        current = _make_status(mode=CutoverMode.mongo_primary, readiness_status=ReadinessStatus.ready_for_shadow)

        with patch.object(svc, "get_or_default_cutover_status", new=AsyncMock(return_value=current)), \
             patch.object(svc, "_run_p0_check", new=AsyncMock(return_value=_fail_p0())):
            with pytest.raises(HTTPException) as exc_info:
                await svc.promote_domain(
                    building_id="13195",
                    domain="finance_ledger",
                    actor_user_id="u-1",
                    actor_role="super_admin",
                    skip_p0_check=False,
                )
        assert exc_info.value.status_code == 409
        assert "P0 readiness check failed" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_promote_succeeds_with_skip_p0_flag(self):
        import services.cutover_status_service as svc

        current = _make_status(mode=CutoverMode.mongo_primary, readiness_status=ReadinessStatus.ready_for_shadow)
        next_status = _make_status(mode=CutoverMode.postgres_shadow, readiness_status=ReadinessStatus.shadow_active)

        with patch.object(svc, "get_or_default_cutover_status",
                          new=AsyncMock(side_effect=[current, next_status])), \
             patch.object(svc, "_run_p0_check", new=AsyncMock(return_value=_fail_p0())), \
             patch.object(svc, "_upsert_status_row", new=AsyncMock()), \
             patch.object(svc, "_write_audit_entry", new=AsyncMock(return_value="audit-1")), \
             patch.object(svc, "async_session_context") as mock_ctx:

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock()
            mock_ctx.return_value = mock_session

            updated, _ = await svc.promote_domain(
                building_id="13195",
                domain="finance_ledger",
                actor_user_id="u-1",
                actor_role="super_admin",
                skip_p0_check=True,
            )
        assert updated.mode == CutoverMode.postgres_shadow


# ---------------------------------------------------------------------------
# 10. Shadow diffs are recorded
# ---------------------------------------------------------------------------

class TestShadowDiffRecording:
    @pytest.mark.asyncio
    async def test_record_shadow_diff_calls_db(self):
        import services.cutover_status_service as svc

        scheme = {"tenant_id": str(uuid4()), "scheme_id": str(uuid4())}
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock()

        with patch("services.cutover_status_service.resolve_scheme_context",
                   new=AsyncMock(return_value=scheme)), \
             patch("services.cutover_status_service.async_session_context",
                   return_value=mock_session):
            diff_id = await svc.record_shadow_diff(
                building_id="13195",
                domain="finance_ledger",
                route="/api/finance/summary",
                diff_type="count_mismatch",
                mongo_value={"count": 42},
                pg_value={"count": 41},
                divergence_score=0.5,
            )

        assert diff_id is not None
        assert len(diff_id) == 36  # UUID format
        # Three execute calls: SET LOCAL bypass tenant, INSERT shadow_diffs, UPDATE domain_cutover_status
        assert mock_session.execute.call_count == 3

    @pytest.mark.asyncio
    async def test_record_diff_updates_last_shadow_diff_at_on_status(self):
        """record_shadow_diff issues an UPDATE to domain_cutover_status."""
        import services.cutover_status_service as svc

        executed_sqls: list[str] = []

        class CapturingSession:
            async def execute(self, stmt, params=None):
                executed_sqls.append(str(stmt))
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass

        scheme = {"tenant_id": str(uuid4()), "scheme_id": str(uuid4())}
        session_obj = CapturingSession()

        with patch("services.cutover_status_service.resolve_scheme_context",
                   new=AsyncMock(return_value=scheme)), \
             patch("services.cutover_status_service.async_session_context",
                   return_value=session_obj):
            await svc.record_shadow_diff(
                building_id="13195",
                domain="finance_ledger",
                diff_type="field_mismatch",
            )

        update_calls = [s for s in executed_sqls if "UPDATE" in s.upper()]
        assert len(update_calls) >= 1, "Expected UPDATE to domain_cutover_status"


# ---------------------------------------------------------------------------
# 11. Mode resolution returns expected read/write source
# ---------------------------------------------------------------------------

class TestModeResolution:
    @pytest.mark.parametrize("mode,expected_read,expected_write", [
        (CutoverMode.mongo_primary,   DataSource.mongo,    DataSource.mongo),
        (CutoverMode.postgres_shadow, DataSource.mongo,    DataSource.mongo),
        (CutoverMode.postgres_read,   DataSource.postgres, DataSource.mongo),
        (CutoverMode.postgres_write,  DataSource.postgres, DataSource.postgres),
        (CutoverMode.mongo_archive,   DataSource.postgres, DataSource.postgres),
        (CutoverMode.disabled,        DataSource.none,     DataSource.none),
    ])
    def test_mode_sources_table(self, mode, expected_read, expected_write):
        read, write = _MODE_SOURCES[mode]
        assert read == expected_read, f"{mode}: expected read={expected_read}, got {read}"
        assert write == expected_write, f"{mode}: expected write={expected_write}, got {write}"

    @pytest.mark.asyncio
    async def test_resolve_read_source_returns_mongo_for_primary(self):
        status = _make_status(mode=CutoverMode.mongo_primary)
        with patch("services.cutover_status_service.get_or_default_cutover_status",
                   new=AsyncMock(return_value=status)):
            src = await resolve_read_source("13195", "finance_ledger")
        assert src == DataSource.mongo

    @pytest.mark.asyncio
    async def test_resolve_read_source_returns_postgres_for_pg_read(self):
        status = _make_status(mode=CutoverMode.postgres_read)
        with patch("services.cutover_status_service.get_or_default_cutover_status",
                   new=AsyncMock(return_value=status)):
            src = await resolve_read_source("13195", "finance_ledger")
        assert src == DataSource.postgres

    @pytest.mark.asyncio
    async def test_resolve_write_source_returns_mongo_until_pg_write(self):
        for mode in (
            CutoverMode.mongo_primary,
            CutoverMode.postgres_shadow,
            CutoverMode.postgres_read,
        ):
            status = _make_status(mode=mode)
            with patch("services.cutover_status_service.get_or_default_cutover_status",
                       new=AsyncMock(return_value=status)):
                src = await resolve_write_source("13195", "finance_ledger")
            assert src == DataSource.mongo, f"Expected mongo writes in {mode}, got {src}"

    @pytest.mark.asyncio
    async def test_resolve_write_source_returns_postgres_after_pg_write(self):
        for mode in (CutoverMode.postgres_write, CutoverMode.mongo_archive):
            status = _make_status(mode=mode)
            with patch("services.cutover_status_service.get_or_default_cutover_status",
                       new=AsyncMock(return_value=status)):
                src = await resolve_write_source("13195", "finance_ledger")
            assert src == DataSource.postgres, f"Expected postgres writes in {mode}, got {src}"


# ---------------------------------------------------------------------------
# 1. Per-building isolation
# ---------------------------------------------------------------------------

class TestPerBuildingIsolation:
    @pytest.mark.asyncio
    async def test_different_buildings_get_different_statuses(self):
        """Status for building A must not bleed into building B."""
        import services.cutover_status_service as svc

        status_a = _make_status(building_id="13195", mode=CutoverMode.postgres_shadow)
        status_b = _make_status(building_id="16244", mode=CutoverMode.mongo_primary)

        call_count = {"n": 0}

        async def _building_aware_get(building_id, domain):
            call_count["n"] += 1
            return status_a if building_id == "13195" else status_b

        with patch.object(svc, "get_cutover_status", side_effect=_building_aware_get):
            result_a = await get_or_default_cutover_status("13195", "finance_ledger")
            result_b = await get_or_default_cutover_status("16244", "finance_ledger")

        assert result_a.mode == CutoverMode.postgres_shadow
        assert result_b.mode == CutoverMode.mongo_primary
        assert result_a.building_id == "13195"
        assert result_b.building_id == "16244"

    def test_block_unsafe_global_prevents_cross_building_cutover(self):
        """No single call can promote all buildings simultaneously."""
        for bad in ("", None, "*", "all", "global"):
            with pytest.raises(ValueError):
                block_unsafe_global_cutover(bad)

        # Specific building IDs are always required
        block_unsafe_global_cutover("13195")  # must not raise
        block_unsafe_global_cutover("16244")  # must not raise


class TestFinancialFoundationReadiness:
    @pytest.mark.asyncio
    async def test_records_readiness_without_promoting_finance(self):
        import services.cutover_status_service as svc

        updated = _make_status(
            building_id="13195",
            domain="finance_ledger",
            mode=CutoverMode.mongo_primary,
            readiness_status=ReadinessStatus.ready_for_shadow,
        )

        fake_session = AsyncMock()
        fake_session.__aenter__ = AsyncMock(return_value=fake_session)
        fake_session.__aexit__ = AsyncMock(return_value=False)
        fake_session.execute = AsyncMock()

        with patch.object(svc, "async_session_context", return_value=fake_session), \
             patch.object(svc, "set_tenant", new=AsyncMock()), \
             patch.object(svc, "_upsert_status_row", new=AsyncMock(return_value="row-1")) as upsert, \
             patch.object(svc, "_write_audit_entry", new=AsyncMock(return_value="audit-1")), \
             patch.object(svc, "get_or_default_cutover_status", new=AsyncMock(return_value=updated)):

            result = await svc.record_financial_foundation_readiness(
                building_id="13195",
                validation_passed=True,
                summary={"evidence_document_id": "doc-1"},
                reason="ready",
            )

        assert result.mode == CutoverMode.mongo_primary
        assert result.readiness_status == ReadinessStatus.ready_for_shadow
        assert upsert.await_args.kwargs["domain"] == "finance_ledger"
        assert upsert.await_args.kwargs["mode"] == CutoverMode.mongo_primary


class TestFinancePrompt6SafetyGuards:
    @pytest.mark.asyncio
    async def test_finance_domain_can_promote_write_after_prompt7_controls(self):
        import services.cutover_status_service as svc

        current = _make_status(mode=CutoverMode.postgres_read, domain="finance_ledger", readiness_status=ReadinessStatus.shadow_clean)
        result_status = _make_status(mode=CutoverMode.postgres_write, domain="finance_ledger", readiness_status=ReadinessStatus.promoted)
        with patch.object(svc, "get_or_default_cutover_status", new=AsyncMock(side_effect=[current, result_status])), \
             patch.object(svc, "_run_p0_check", new=AsyncMock(return_value=_pass_p0())), \
             patch.object(svc, "_upsert_status_row", new=AsyncMock()), \
             patch.object(svc, "_write_audit_entry", new=AsyncMock(return_value=str(uuid4()))), \
             patch.object(svc, "async_session_context") as mock_ctx:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock()
            mock_ctx.return_value = mock_session

            updated, _ = await svc.promote_domain(
                building_id="13195",
                domain="finance_ledger",
                actor_user_id="sa-1",
                actor_role="super_admin",
            )

        assert updated.mode == CutoverMode.postgres_write

    @pytest.mark.asyncio
    async def test_finance_read_promotion_requires_financial_onboarding_pass(self):
        import services.cutover_status_service as svc

        current = _make_status(mode=CutoverMode.postgres_shadow, domain="finance_ledger", readiness_status=ReadinessStatus.shadow_passing)
        current.p0_snapshot = {"financial_onboarding": {"status": "fail"}}
        with patch.object(
            svc,
            "get_or_default_cutover_status",
            new=AsyncMock(return_value=current),
        ), patch.object(
            svc,
            "_run_p0_check",
            new=AsyncMock(return_value=_pass_p0()),
        ):
            with pytest.raises(Exception) as exc_info:
                await svc.promote_domain(
                    building_id="13195",
                    domain="finance_ledger",
                    actor_user_id="sa-1",
                    actor_role="super_admin",
                )
        assert "financial onboarding readiness snapshot is not pass" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_finance_read_promotion_requires_identity_foundation_pass(self):
        from fastapi import HTTPException
        import services.cutover_status_service as svc

        current = _make_status(mode=CutoverMode.postgres_shadow, domain="finance_ledger", readiness_status=ReadinessStatus.shadow_passing)
        current.p0_snapshot = {"financial_onboarding": {"status": "pass"}}
        identity_not_ready = _make_status(mode=CutoverMode.mongo_primary, domain="identity_core")
        identity_not_ready.p0_snapshot = {"identity_foundation": {"status": "fail"}}

        with patch.object(
            svc,
            "get_or_default_cutover_status",
            new=AsyncMock(return_value=current),
        ), patch.object(
            svc,
            "_run_p0_check",
            new=AsyncMock(return_value=_pass_p0()),
        ), patch.object(
            svc,
            "get_cutover_status",
            new=AsyncMock(return_value=identity_not_ready),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await svc.promote_domain(
                    building_id="13195",
                    domain="finance_ledger",
                    actor_user_id="sa-1",
                    actor_role="super_admin",
                )
        assert exc_info.value.status_code == 409
        assert "identity foundation readiness snapshot is not pass" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_skip_finance_identity_gate_bypasses_check_for_super_admin(self):
        """GAP-FIN-030 (2026-08-02): explicit, narrow override for the identity_core
        cross-check ONLY -- financial_onboarding must still independently pass, and
        the override only applies for super_admin actors."""
        import services.cutover_status_service as svc

        current = _make_status(mode=CutoverMode.postgres_shadow, domain="finance_ledger", readiness_status=ReadinessStatus.shadow_passing)
        current.p0_snapshot = {"financial_onboarding": {"status": "pass"}}
        result_status = _make_status(mode=CutoverMode.postgres_read, domain="finance_ledger", readiness_status=ReadinessStatus.shadow_passing)

        with patch.object(
            svc, "get_or_default_cutover_status", new=AsyncMock(side_effect=[current, result_status]),
        ), patch.object(
            svc, "_run_p0_check", new=AsyncMock(return_value=_pass_p0()),
        ), patch.object(
            svc, "get_cutover_status", new=AsyncMock(side_effect=AssertionError(
                "identity_core status must not be queried when the gate is skipped")),
        ), patch.object(
            svc, "_upsert_status_row", new=AsyncMock(),
        ), patch.object(
            svc, "_write_audit_entry", new=AsyncMock(return_value=str(uuid4())),
        ), patch.object(
            svc, "async_session_context",
        ) as mock_ctx:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock()
            mock_ctx.return_value = mock_session

            updated, _ = await svc.promote_domain(
                building_id="13195",
                domain="finance_ledger",
                actor_user_id="sa-1",
                actor_role="super_admin",
                skip_finance_identity_gate=True,
                reason="GAP-FIN-030: identity_core ownership data verified by row-count match; live sync not yet wired",
            )

        assert updated.mode == CutoverMode.postgres_read

    @pytest.mark.asyncio
    async def test_skip_finance_identity_gate_ignored_for_non_super_admin(self):
        """The override must not apply for non-super_admin actors, even if the
        caller passes skip_finance_identity_gate=True."""
        from fastapi import HTTPException
        import services.cutover_status_service as svc

        current = _make_status(mode=CutoverMode.postgres_shadow, domain="finance_ledger", readiness_status=ReadinessStatus.shadow_passing)
        current.p0_snapshot = {"financial_onboarding": {"status": "pass"}}
        identity_not_ready = _make_status(mode=CutoverMode.mongo_primary, domain="identity_core")
        identity_not_ready.p0_snapshot = {"identity_foundation": {"status": "fail"}}

        with patch.object(
            svc, "get_or_default_cutover_status", new=AsyncMock(return_value=current),
        ), patch.object(
            svc, "_run_p0_check", new=AsyncMock(return_value=_pass_p0()),
        ), patch.object(
            svc, "get_cutover_status", new=AsyncMock(return_value=identity_not_ready),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await svc.promote_domain(
                    building_id="13195",
                    domain="finance_ledger",
                    actor_user_id="strata-manager-1",
                    actor_role="strata_manager",
                    skip_finance_identity_gate=True,
                )
        assert exc_info.value.status_code == 409
        assert "identity foundation readiness snapshot is not pass" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Migration 0060 — new ReadinessStatus enum values
# These values were added by migration 0060_extend_readiness_constraint.py.
# Verify the Python enum accepts them so any DB round-trip will not raise.
# ---------------------------------------------------------------------------

class TestMigration0060ReadinessStatusValues:
    """Regression: migration 0060 extended ck_cutover_status_readiness with 4 values.
    Python-level test ensures the ReadinessStatus enum mirrors the DB constraint.
    """

    def test_identity_ready_is_valid_enum_member(self):
        assert ReadinessStatus("identity_ready") == ReadinessStatus.identity_ready

    def test_evidence_ready_is_valid_enum_member(self):
        assert ReadinessStatus("evidence_ready") == ReadinessStatus.evidence_ready

    def test_genesis_ready_is_valid_enum_member(self):
        # trust_ledger was set to genesis_ready after Phase C (balanced opening balances)
        assert ReadinessStatus("genesis_ready") == ReadinessStatus.genesis_ready

    def test_ready_for_shadow_is_valid_enum_member(self):
        # finance_ledger was set to ready_for_shadow at end of Phase C before Phase D activation
        assert ReadinessStatus("ready_for_shadow") == ReadinessStatus.ready_for_shadow

    def test_new_statuses_accepted_in_make_status_helper(self):
        # Confirm _make_status with genesis_ready does not raise (covers DB→Python round-trip)
        s = _make_status(
            domain="trust_ledger",
            readiness_status=ReadinessStatus.genesis_ready,
        )
        assert s.readiness_status == ReadinessStatus.genesis_ready

    def test_all_four_new_values_are_distinct(self):
        new_values = {
            ReadinessStatus.identity_ready,
            ReadinessStatus.evidence_ready,
            ReadinessStatus.genesis_ready,
            ReadinessStatus.ready_for_shadow,
        }
        assert len(new_values) == 4


# ---------------------------------------------------------------------------
# Phase A hardening (2026-07-14): promote_domain() readiness + consistency
# enforcement, domain canonicalization at the persistence boundary.
# See docs/migration/shadow-correction-updates-plan-p1.md / -p2.md / -p3.md.
# ---------------------------------------------------------------------------

class TestPromoteRequiresPreShadowReadiness:
    """mongo_primary -> postgres_shadow must reject domains without pre-shadow readiness."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_readiness", [
        ReadinessStatus.unknown,
        ReadinessStatus.not_started,
        ReadinessStatus.blocked,
    ])
    async def test_blocked_or_unknown_readiness_cannot_enter_shadow(self, bad_readiness):
        from fastapi import HTTPException
        import services.cutover_status_service as svc

        current = _make_status(mode=CutoverMode.mongo_primary, readiness_status=bad_readiness)

        with patch.object(svc, "get_or_default_cutover_status", new=AsyncMock(return_value=current)), \
             patch.object(svc, "_run_p0_check", new=AsyncMock(return_value=_pass_p0())):
            with pytest.raises(HTTPException) as exc_info:
                await svc.promote_domain(
                    building_id="13195",
                    domain="governance",
                    actor_user_id="u-1",
                    actor_role="super_admin",
                )
        assert exc_info.value.status_code == 409
        assert "not ready to enter shadow mode" in exc_info.value.detail

    @pytest.mark.asyncio
    @pytest.mark.parametrize("good_readiness", [
        ReadinessStatus.ready_for_shadow,
        ReadinessStatus.identity_ready,
        ReadinessStatus.evidence_ready,
        ReadinessStatus.genesis_ready,
    ])
    async def test_ready_states_can_enter_shadow(self, good_readiness):
        import services.cutover_status_service as svc

        current = _make_status(mode=CutoverMode.mongo_primary, readiness_status=good_readiness)
        after = _make_status(mode=CutoverMode.postgres_shadow, readiness_status=ReadinessStatus.shadow_active)

        with patch.object(svc, "get_or_default_cutover_status",
                          new=AsyncMock(side_effect=[current, after])), \
             patch.object(svc, "_run_p0_check", new=AsyncMock(return_value=_pass_p0())), \
             patch.object(svc, "_upsert_status_row", new=AsyncMock()), \
             patch.object(svc, "_write_audit_entry", new=AsyncMock(return_value=str(uuid4()))), \
             patch.object(svc, "async_session_context") as mock_ctx:

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock()
            mock_ctx.return_value = mock_session

            updated, _ = await svc.promote_domain(
                building_id="13195",
                domain="trust_ledger",
                actor_user_id="u-1",
                actor_role="super_admin",
            )
        assert updated.mode == CutoverMode.postgres_shadow


class TestModeReadinessConsistency:
    """A row whose (mode, readiness_status) pair is impossible must fail closed, not promote."""

    def test_validator_rejects_mongo_primary_plus_shadow_clean(self):
        import services.cutover_status_service as svc

        violation = svc.validate_mode_readiness_pair(
            CutoverMode.mongo_primary, ReadinessStatus.shadow_clean
        )
        assert violation is not None
        assert "Inconsistent cutover mode/readiness state" in violation

    def test_validator_accepts_consistent_pairs(self):
        import services.cutover_status_service as svc

        assert svc.validate_mode_readiness_pair(
            CutoverMode.mongo_primary, ReadinessStatus.identity_ready
        ) is None
        assert svc.validate_mode_readiness_pair(
            CutoverMode.postgres_shadow, ReadinessStatus.shadow_active
        ) is None
        # rolled_back is valid alongside any mode reachable by rollback (all but mongo_archive)
        assert svc.validate_mode_readiness_pair(
            CutoverMode.postgres_shadow, ReadinessStatus.rolled_back
        ) is None

    @pytest.mark.asyncio
    async def test_promote_domain_rejects_inconsistent_current_row(self):
        """This is exactly the live state identity_core was found in on 2026-07-14:
        mode=mongo_primary with readiness_status=shadow_clean (impossible without ever
        having left mongo_primary). promote_domain must refuse to build on top of it.
        """
        from fastapi import HTTPException
        import services.cutover_status_service as svc

        current = _make_status(mode=CutoverMode.mongo_primary, readiness_status=ReadinessStatus.shadow_clean)

        with patch.object(svc, "get_or_default_cutover_status", new=AsyncMock(return_value=current)):
            with pytest.raises(HTTPException) as exc_info:
                await svc.promote_domain(
                    building_id="13195",
                    domain="identity_core",
                    actor_user_id="u-1",
                    actor_role="super_admin",
                )
        assert exc_info.value.status_code == 409
        assert "Inconsistent cutover mode/readiness state" in exc_info.value.detail


class TestDomainCanonicalization:
    """record_shadow_diff and status reads must not let an alias slip into storage/queries."""

    def test_canonical_domain_maps_known_aliases(self):
        import services.cutover_status_service as svc

        assert svc.canonical_domain("finance") == "finance_ledger"
        assert svc.canonical_domain("financial") == "finance_ledger"
        assert svc.canonical_domain("ledger") == "finance_ledger"
        assert svc.canonical_domain("trust") == "trust_ledger"
        assert svc.canonical_domain("trust_accounting") == "trust_ledger"
        assert svc.canonical_domain("identity") == "identity_core"
        assert svc.canonical_domain("ownership") == "identity_core"

    def test_canonical_domain_passes_through_unknown_and_already_canonical(self):
        import services.cutover_status_service as svc

        assert svc.canonical_domain("finance_ledger") == "finance_ledger"
        assert svc.canonical_domain("governance") == "governance"

    def test_canonical_domain_is_case_and_whitespace_insensitive(self):
        import services.cutover_status_service as svc

        assert svc.canonical_domain(" Finance ") == "finance_ledger"
        assert svc.canonical_domain("FINANCE") == "finance_ledger"

    @pytest.mark.asyncio
    async def test_record_shadow_diff_persists_canonical_domain_not_alias(self):
        import services.cutover_status_service as svc

        scheme = {"tenant_id": str(uuid4()), "scheme_id": str(uuid4())}
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock()

        with patch("services.cutover_status_service.resolve_scheme_context",
                   new=AsyncMock(return_value=scheme)), \
             patch("services.cutover_status_service.async_session_context",
                   return_value=mock_session):
            await svc.record_shadow_diff(
                building_id="13195",
                domain="finance",  # alias, not canonical
                route="/api/finance/summary",
                diff_type="count_mismatch",
                mongo_value={"count": 1},
                pg_value={"count": 2},
            )

        # Find the INSERT INTO core.shadow_diffs call among all execute() calls
        # (session.execute is also called by set_tenant()) and assert its bound
        # "domain" param is the canonical name, never the alias.
        insert_calls = [
            call for call in mock_session.execute.await_args_list
            if len(call.args) > 1 and "INSERT INTO core.shadow_diffs" in str(call.args[0])
        ]
        assert len(insert_calls) == 1
        assert insert_calls[0].args[1]["domain"] == "finance_ledger"

    @pytest.mark.asyncio
    async def test_get_cutover_status_canonicalizes_domain_before_fetch(self):
        import services.cutover_status_service as svc

        with patch.object(svc, "_get_bypass_session_context") as mock_ctx, \
             patch.object(svc, "_fetch_status_row", new=AsyncMock(return_value=None)) as mock_fetch:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.return_value = mock_session

            await svc.get_cutover_status("13195", "finance")  # alias, not canonical

        # _fetch_status_row must be called with the canonical domain, not the alias.
        assert mock_fetch.await_args.args[2] == "finance_ledger"


class TestPromoteDomainPerBuildingIsolationOnNewChecks:
    """The new readiness/consistency checks must not leak across buildings."""

    @pytest.mark.asyncio
    async def test_building_a_blocked_state_does_not_affect_building_b(self):
        import services.cutover_status_service as svc

        async def _building_aware_get(building_id, domain):
            if building_id == "13195":
                return _make_status(building_id="13195", mode=CutoverMode.mongo_primary,
                                     readiness_status=ReadinessStatus.blocked)
            return _make_status(building_id="16244", mode=CutoverMode.mongo_primary,
                                 readiness_status=ReadinessStatus.ready_for_shadow)

        after_b = _make_status(building_id="16244", mode=CutoverMode.postgres_shadow,
                                readiness_status=ReadinessStatus.shadow_active)

        with patch.object(svc, "get_or_default_cutover_status", side_effect=_building_aware_get):
            from fastapi import HTTPException
            with pytest.raises(HTTPException):
                await svc.promote_domain(
                    building_id="13195", domain="governance",
                    actor_user_id="u-1", actor_role="super_admin",
                )

        with patch.object(svc, "get_or_default_cutover_status",
                          new=AsyncMock(side_effect=[
                              await _building_aware_get("16244", "governance"), after_b,
                          ])), \
             patch.object(svc, "_run_p0_check", new=AsyncMock(return_value=_pass_p0())), \
             patch.object(svc, "_upsert_status_row", new=AsyncMock()), \
             patch.object(svc, "_write_audit_entry", new=AsyncMock(return_value=str(uuid4()))), \
             patch.object(svc, "async_session_context") as mock_ctx:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock()
            mock_ctx.return_value = mock_session

            updated, _ = await svc.promote_domain(
                building_id="16244", domain="governance",
                actor_user_id="u-1", actor_role="super_admin",
            )
        assert updated.mode == CutoverMode.postgres_shadow
