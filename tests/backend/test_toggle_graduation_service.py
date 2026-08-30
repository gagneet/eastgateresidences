# @featuretrace:cutover-toggle-safety — State-derived graduation of protected feature toggles.
# Layer: test
# Data flow: pytest → core.toggle_classification.evaluate_graduation /
#            services.toggle_graduation_service → assert_global_enable_allowed(graduated=…)
# Related: backend/core/toggle_classification.py
#          backend/services/toggle_graduation_service.py
#          backend/db_postgres/repos/config_repo.py
#          scripts/audits/toggle_drift.py
# Tests: this file
"""Graduation — the protection has to be able to end, and only on live evidence.

Before 2026-08-27 PROTECTED_TOGGLE_KEYS was a permanent veto: a cutover toggle
stayed forbidden globally no matter how far the migration it guards had actually
progressed, and toggle_drift_autoheal.py re-asserted that veto on every deploy
without reading a single row of migration state.

These tests pin the replacement, and specifically pin it AGAINST the failure mode
that matters — graduating on anything other than proof:

  1. Every protected key maps to the domain(s) that would graduate it.
  2. Graduation needs every production building promoted, not just one.
  3. postgres_read is not enough — writes must be PostgreSQL-authoritative too.
  4. No production buildings, or an unreadable control plane, means no graduation.
  5. A graduated key passes assert_global_enable_allowed; a blocked one still raises.
"""
from unittest.mock import AsyncMock, patch

import pytest

from core.toggle_classification import (
    GRADUATING_CUTOVER_MODES,
    PROTECTED_TOGGLE_CUTOVER_DOMAINS,
    PROTECTED_TOGGLE_KEYS,
    ProtectedToggleError,
    assert_global_enable_allowed,
    cutover_domains_for,
    evaluate_graduation,
)


class TestGraduationMap:
    def test_every_protected_key_declares_its_graduating_domains(self):
        """An unmapped protected key can never graduate — so none may be unmapped.

        cutover_domains_for() returns () for an unknown key and evaluate_graduation
        treats that as "never", which fails safe. This test makes the omission loud
        instead, so adding a protected toggle without saying what finishes it is a
        test failure rather than a permanent, silent veto.
        """
        unmapped = sorted(PROTECTED_TOGGLE_KEYS - set(PROTECTED_TOGGLE_CUTOVER_DOMAINS))
        assert not unmapped, f"protected keys with no graduating domain: {unmapped}"

    def test_map_does_not_cover_unprotected_keys(self):
        extra = sorted(set(PROTECTED_TOGGLE_CUTOVER_DOMAINS) - PROTECTED_TOGGLE_KEYS)
        assert not extra, f"graduating domains declared for unprotected keys: {extra}"

    def test_read_only_promotion_is_not_a_graduating_mode(self):
        """postgres_read leaves writes on Mongo, so the global default still steers."""
        assert "postgres_read" not in GRADUATING_CUTOVER_MODES
        assert "postgres_shadow" not in GRADUATING_CUTOVER_MODES
        assert "mongo_primary" not in GRADUATING_CUTOVER_MODES
        assert GRADUATING_CUTOVER_MODES == {"postgres_write", "mongo_archive"}


class TestEvaluateGraduation:
    def test_unprotected_key_is_always_permitted(self):
        assert evaluate_graduation("documents", {}) is True

    def test_graduates_when_every_building_has_every_required_domain(self):
        key = "owner_read_pg_enabled"
        required = set(cutover_domains_for(key))
        assert required, "fixture assumes this key is mapped"
        promoted = {"13195": set(required), "18932": set(required)}
        assert evaluate_graduation(key, promoted) is True

    def test_one_lagging_building_blocks_graduation(self):
        """The rule is every production building, not the most advanced one."""
        key = "owner_read_pg_enabled"
        required = set(cutover_domains_for(key))
        promoted = {"13195": set(required), "18932": set()}
        assert evaluate_graduation(key, promoted) is False

    def test_partial_domain_coverage_blocks_a_multi_domain_key(self):
        key = "owner_read_pg_enabled"
        required = list(cutover_domains_for(key))
        assert len(required) > 1, "fixture assumes a multi-domain key"
        promoted = {"13195": {required[0]}}
        assert evaluate_graduation(key, promoted) is False

    def test_empty_control_plane_never_graduates(self):
        """Absence of evidence is not evidence of promotion."""
        assert evaluate_graduation("financial_pg_reads_enabled", {}) is False

    def test_unmapped_protected_key_never_graduates(self):
        with patch.dict(PROTECTED_TOGGLE_CUTOVER_DOMAINS, clear=False):
            PROTECTED_TOGGLE_CUTOVER_DOMAINS.pop("financial_pg_reads_enabled", None)
            assert evaluate_graduation(
                "financial_pg_reads_enabled", {"13195": {"finance_ledger"}}
            ) is False


class TestServiceFailsClosed:
    @pytest.mark.asyncio
    async def test_control_plane_error_graduates_nothing(self):
        from services import toggle_graduation_service as svc

        with patch.object(
            svc, "_promoted_domains_by_production_building",
            AsyncMock(side_effect=RuntimeError("connection refused")),
        ):
            assert await svc.graduated_protected_keys() == frozenset()

    @pytest.mark.asyncio
    async def test_demo_schemes_do_not_vote(self):
        """A demo building is excluded from the vote, not from protection.

        require_domain_source fails closed on a missing cutover row, so a demo or
        newly-onboarded building keeps reading Mongo whatever the global default is.
        """
        from services import toggle_graduation_service as svc

        schemes = [
            {"scheme_number": "13195", "is_demo": False},
            {"scheme_number": "UPDEMO5", "is_demo": True},
        ]
        rows = [("13195", "governance", "postgres_write")]

        class _Result:
            def fetchall(self):
                return rows

        class _Session:
            async def execute(self, *_a, **_kw):
                return _Result()

        class _Ctx:
            async def __aenter__(self):
                return _Session()

            async def __aexit__(self, *_a):
                return False

        with patch(
            "db_postgres.repos.identity_repo.list_all_active_schemes",
            AsyncMock(return_value=schemes),
        ), patch(
            "services.cutover_status_service._get_bypass_session_context",
            lambda: _Ctx(),
        ):
            promoted = await svc._promoted_domains_by_production_building()

        assert "UPDEMO5" not in promoted
        assert promoted == {"13195": {"governance"}}


class TestEnableGuardHonoursGraduation:
    def test_protected_key_still_raises_without_graduation(self):
        with pytest.raises(ProtectedToggleError):
            assert_global_enable_allowed("financial_pg_reads_enabled")

    def test_graduated_key_is_permitted(self):
        assert_global_enable_allowed("financial_pg_reads_enabled", graduated=True)

    def test_escape_hatch_is_still_independent_of_graduation(self):
        """The hatch overrides the gate; graduation means the gate was satisfied."""
        assert_global_enable_allowed(
            "financial_pg_reads_enabled", _allow_protected_global_enable=True
        )
