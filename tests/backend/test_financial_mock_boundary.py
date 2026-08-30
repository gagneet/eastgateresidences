"""Per-building mock/live boundary for the external financial integrations.

Two toggles, `financial_services_mock` and `bank_direct_debit_mock`, replace the
single process-wide `MOCK_EXTERNAL_SERVICES` env var that was read in exactly one
place. They are the INVERSE of every other protected financial toggle: enabled is
the safe state, and disabling one for a building is what points it at a real
financial institution.

That inversion is what these tests mostly guard. The existing safety machinery
only ever asks permission to ENABLE, on the reasoning that disabling is fail-safe
— true for every other class and false for these.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.toggle_classification import (  # noqa: E402
    DISABLE_PROTECTED_TOGGLE_KEYS,
    MOCK_BOUNDARY_SAFETY_METADATA,
    PROTECTED_TOGGLE_KEYS,
    MockBoundaryToggleError,
    ToggleClass,
    assert_global_disable_allowed,
    assert_global_enable_allowed,
    get_toggle_class,
    is_disable_protected_toggle,
)
from services.financial_mock_mode import (  # noqa: E402
    BANK_DIRECT_DEBIT_MOCK_KEY,
    FINANCIAL_SERVICES_MOCK_KEY,
    bank_direct_debit_mocked,
    financial_services_mocked,
)

BOTH_KEYS = [FINANCIAL_SERVICES_MOCK_KEY, BANK_DIRECT_DEBIT_MOCK_KEY]
BUILDING_ID = "13195"


# ── Classification: the inverted guard ──────────────────────────────────────

class TestMockBoundaryClassification:
    @pytest.mark.parametrize("key", BOTH_KEYS)
    def test_key_is_mock_boundary_and_disable_protected(self, key):
        assert get_toggle_class(key) is ToggleClass.MOCK_BOUNDARY
        assert is_disable_protected_toggle(key)

    @pytest.mark.parametrize("key", BOTH_KEYS)
    def test_global_disable_is_blocked(self, key):
        """Disabling globally would point EVERY building at a live institution."""
        with pytest.raises(MockBoundaryToggleError):
            assert_global_disable_allowed(key)

    @pytest.mark.parametrize("key", BOTH_KEYS)
    def test_global_enable_stays_allowed(self, key):
        """Enabling is the return-to-mock direction and needs no permission.

        This is why the keys must NOT be folded into PROTECTED_TOGGLE_KEYS: that set
        means "cannot be globally enabled", which here would forbid the safe direction
        while leaving the dangerous one open.
        """
        assert_global_enable_allowed(key)  # must not raise

    @pytest.mark.parametrize("key", BOTH_KEYS)
    def test_key_is_not_in_the_enable_protected_set(self, key):
        assert key not in PROTECTED_TOGGLE_KEYS

    def test_the_two_protected_sets_do_not_overlap(self):
        """A key cannot be guarded in both directions — that would pin it forever."""
        assert not (DISABLE_PROTECTED_TOGGLE_KEYS & PROTECTED_TOGGLE_KEYS)

    def test_safety_metadata_covers_exactly_the_disable_protected_keys(self):
        assert set(MOCK_BOUNDARY_SAFETY_METADATA) == set(DISABLE_PROTECTED_TOGGLE_KEYS)

    def test_metadata_names_the_three_roles_that_may_hold_the_switch(self):
        for key, meta in MOCK_BOUNDARY_SAFETY_METADATA.items():
            assert set(meta["allowed_roles"]) == {"super_admin", "strata_admin", "strata_manager"}, key

    def test_escape_hatch_exists_for_deliberate_internal_callers(self):
        assert_global_disable_allowed(FINANCIAL_SERVICES_MOCK_KEY, _allow_global_mock_disable=True)


class TestBlockedWritesReachTheHttpLayerAs403:
    """A blocked write must be a 403, not a 500.

    MockBoundaryToggleError was originally a bare RuntimeError, so the
    `except ProtectedToggleError` handlers in routers/feature_toggles.py did not catch
    it: a super admin globally disabling one of these keys got an unhandled exception.
    The write was correctly refused either way, which is why the guard's own tests
    still passed — the defect was only in the HTTP contract.
    """

    def test_both_guards_share_a_base_the_routers_can_catch(self):
        from core.toggle_classification import (
            ProtectedToggleError,
            ToggleWriteBlockedError,
        )

        assert issubclass(MockBoundaryToggleError, ToggleWriteBlockedError)
        assert issubclass(ProtectedToggleError, ToggleWriteBlockedError)

    def test_the_two_errors_stay_distinguishable(self):
        """Sibling, not subclass: they report opposite directions.

        Code catching ProtectedToggleError by name must not be told an *enable* was
        blocked when a *disable* was.
        """
        from core.toggle_classification import ProtectedToggleError

        assert not issubclass(MockBoundaryToggleError, ProtectedToggleError)
        assert not issubclass(ProtectedToggleError, MockBoundaryToggleError)

    def test_the_error_still_carries_its_key_and_class(self):
        error = MockBoundaryToggleError(FINANCIAL_SERVICES_MOCK_KEY, ToggleClass.MOCK_BOUNDARY)
        assert error.feature_key == FINANCIAL_SERVICES_MOCK_KEY
        assert error.toggle_class is ToggleClass.MOCK_BOUNDARY
        assert "must not be disabled globally" in str(error)

    def test_the_toggle_router_catches_the_base(self):
        source = (BACKEND / "routers" / "feature_toggles.py").read_text()
        assert "except ProtectedToggleError as exc:" not in source, (
            "catching only ProtectedToggleError turns a blocked disable into a 500"
        )
        assert source.count("except ToggleWriteBlockedError as exc:") == 2


class TestGoLiveIsAttributedToTheRealActor:
    """core.feature_toggle_overrides.set_by is the record of who went live.

    upsert_feature_toggle_override passes require_existing=True, whose fallback returns
    the oldest active super_admin when the caller cannot be matched. For an ordinary
    toggle that is an acceptable trade; here it would name an uninvolved person as
    having connected a building to real money.
    """

    def test_router_resolves_the_actor_before_writing(self):
        from routers import building_integrations

        source = inspect.getsource(building_integrations.set_building_integration_mock_mode)
        assert "resolve_actor_user_id(" in source
        assert source.index("resolve_actor_user_id(") < source.index("upsert_feature_toggle_override(")
        assert "actor_uuid is None" in source
        assert "409" in source

    def test_router_passes_the_resolved_uuid_not_the_raw_id(self):
        from routers import building_integrations

        source = inspect.getsource(building_integrations.set_building_integration_mock_mode)
        assert "actor_user_id=actor_uuid," in source


# ── Resolution: fails safe in every direction ───────────────────────────────

class TestResolutionFailsSafe:
    @pytest.mark.asyncio
    async def test_missing_catalogue_row_resolves_to_mocked(self, monkeypatch):
        """The bug this caught during implementation, pinned.

        `resolve_feature_toggle(default=True)` does NOT cover a key with no row: it
        delegates to core.feature_toggle_resolved, which returns FALSE — not NULL —
        for a key it has never heard of, so the default is unreachable. For every
        other toggle FALSE is the safe answer; for these it means "live", so a
        database missing migration 0095 would have run live providers.
        """
        monkeypatch.delenv("MOCK_EXTERNAL_SERVICES", raising=False)
        with patch("db_postgres.repos.config_repo.get_global_feature_toggle",
                   new=AsyncMock(return_value=None)) as catalogue, \
             patch("db_postgres.repos.config_repo.resolve_feature_toggle",
                   new=AsyncMock(return_value=False)):
            assert await financial_services_mocked(BUILDING_ID) is True
        catalogue.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_safe_answer_costs_one_round_trip(self, monkeypatch):
        """Only a "live" answer is corroborated against the catalogue.

        This resolver sits in ProviderRegistry._get_preference and runs on every
        provider lookup, so the common path must not pay two queries. "Mocked" needs
        no corroboration — it is the safe answer, and the only way it can be produced
        by accident is a failure, which already returns it.
        """
        monkeypatch.delenv("MOCK_EXTERNAL_SERVICES", raising=False)
        with patch("db_postgres.repos.config_repo.get_global_feature_toggle",
                   new=AsyncMock(return_value={"is_enabled": True})) as catalogue, \
             patch("db_postgres.repos.config_repo.resolve_feature_toggle",
                   new=AsyncMock(return_value=True)):
            assert await financial_services_mocked(BUILDING_ID) is True
        catalogue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resolution_failure_resolves_to_mocked(self, monkeypatch):
        monkeypatch.delenv("MOCK_EXTERNAL_SERVICES", raising=False)
        with patch("db_postgres.repos.config_repo.get_global_feature_toggle",
                   new=AsyncMock(side_effect=RuntimeError("config store down"))):
            assert await financial_services_mocked(BUILDING_ID) is True

    @pytest.mark.asyncio
    async def test_missing_building_context_resolves_to_mocked(self, monkeypatch):
        monkeypatch.delenv("MOCK_EXTERNAL_SERVICES", raising=False)
        assert await financial_services_mocked(None) is True
        assert await bank_direct_debit_mocked("") is True

    @pytest.mark.asyncio
    async def test_env_override_forces_mock_for_every_building(self, monkeypatch):
        """The env var can only ever force mock ON, never off."""
        monkeypatch.setenv("MOCK_EXTERNAL_SERVICES", "true")
        with patch("db_postgres.repos.config_repo.get_global_feature_toggle",
                   new=AsyncMock(return_value={"is_enabled": False})), \
             patch("db_postgres.repos.config_repo.resolve_feature_toggle",
                   new=AsyncMock(return_value=False)):
            assert await financial_services_mocked(BUILDING_ID) is True

    @pytest.mark.asyncio
    async def test_a_building_switched_live_resolves_to_not_mocked(self, monkeypatch):
        """The switch has to actually work, or all of the above is vacuous."""
        monkeypatch.delenv("MOCK_EXTERNAL_SERVICES", raising=False)
        with patch("db_postgres.repos.config_repo.get_global_feature_toggle",
                   new=AsyncMock(return_value={"is_enabled": True})), \
             patch("db_postgres.repos.config_repo.resolve_feature_toggle",
                   new=AsyncMock(return_value=False)):
            assert await financial_services_mocked(BUILDING_ID) is False

    @pytest.mark.asyncio
    async def test_the_two_switches_resolve_independently(self, monkeypatch):
        monkeypatch.delenv("MOCK_EXTERNAL_SERVICES", raising=False)
        resolved = {FINANCIAL_SERVICES_MOCK_KEY: False, BANK_DIRECT_DEBIT_MOCK_KEY: True}

        async def _resolve(building_id, feature_key, default=False):
            return resolved[feature_key]

        with patch("db_postgres.repos.config_repo.get_global_feature_toggle",
                   new=AsyncMock(return_value={"is_enabled": True})), \
             patch("db_postgres.repos.config_repo.resolve_feature_toggle",
                   new=AsyncMock(side_effect=_resolve)):
            assert await financial_services_mocked(BUILDING_ID) is False
            assert await bank_direct_debit_mocked(BUILDING_ID) is True


# ── Demo Bank is out of scope ───────────────────────────────────────────────

class TestDemoBankIsExcluded:
    def test_bank_feed_protocol_is_not_pinned_by_the_toggle(self):
        """bank_feed is Demo Bank's slot and must survive the mock boundary.

        Demo Bank is a first-party emulator with its own gates and is mock by
        construction. Pinning its slot here would mean a building that flipped this
        switch silently lost its Demo Bank selection — and its reconstruction staging.
        """
        from integrations.registry import ProviderRegistry

        assert "bank_feed" not in ProviderRegistry._MOCKABLE_PROTOCOLS
        assert set(ProviderRegistry._MOCKABLE_PROTOCOLS) == {
            "biller", "payment_initiation", "accounting", "ocr",
        }

    @pytest.mark.asyncio
    async def test_mock_boundary_pins_the_four_protocols_and_leaves_bank_feed(self):
        from integrations.registry import ProviderRegistry

        registry = ProviderRegistry()
        with patch("services.financial_mock_mode.financial_services_mocked",
                   new=AsyncMock(return_value=True)):
            pinned = await registry._apply_mock_boundary(
                BUILDING_ID, {"bank_feed": "demo_bank", "biller": "real_biller"}
            )
        assert pinned["bank_feed"] == "demo_bank", "Demo Bank selection was overwritten"
        assert pinned["biller"] == "mock_biller"
        assert pinned["payment_initiation"] == "mock_aba_writer"

    @pytest.mark.asyncio
    async def test_a_live_building_keeps_its_configured_providers(self):
        from integrations.registry import ProviderRegistry

        registry = ProviderRegistry()
        stored = {"bank_feed": "demo_bank", "biller": "real_biller"}
        with patch("services.financial_mock_mode.financial_services_mocked",
                   new=AsyncMock(return_value=False)):
            assert await registry._apply_mock_boundary(BUILDING_ID, stored) == stored

    def test_demo_bank_toggles_are_untouched_by_this_classification(self):
        """Demo Bank's own keys keep their existing (enable-protected) class."""
        assert get_toggle_class("demo_bank_feed_enabled") is ToggleClass.CUTOVER_SENSITIVE
        assert "demo_bank_feed_enabled" in PROTECTED_TOGGLE_KEYS
        assert "demo_bank_feed_enabled" not in DISABLE_PROTECTED_TOGGLE_KEYS


# ── The call sites actually consult the boundary ────────────────────────────

class TestCallSitesAreWired:
    """A toggle nothing reads is decoration, so each scope is pinned to its site."""

    def test_stripe_payment_intent_is_gated(self):
        import server

        source = inspect.getsource(server.create_payment_intent)
        assert "assert_live_financial_call_allowed" in source
        assert source.index("assert_live_financial_call_allowed") < source.index("stripe.PaymentIntent.create")

    def test_deft_simulator_resolves_per_building(self):
        from routers import trust_phase1

        assert inspect.iscoroutinefunction(trust_phase1.deft_mock_mode_enabled)
        source = inspect.getsource(trust_phase1.simulate_deft_payment)
        assert "await deft_mock_mode_enabled(building_id)" in source

    def test_aba_file_generation_is_deliberately_not_gated(self):
        """Generating an ABA file is not a live financial call.

        `generate_aba_file` builds a string and stores it base64 on the batch; it
        contacts nothing. Gating it broke the dual-approval workflow outright (four
        tests in test_trust_dual_approval.py) on the first attempt at this feature.
        The live boundary for payment initiation is the PaymentInitiationProvider,
        which ProviderRegistry pins to mock_aba_writer while the toggle is on — so
        the coverage is real, it just belongs one layer down. Gate the TRANSMISSION
        of the file if that is ever implemented, never its generation.
        """
        source = (BACKEND / "routers" / "trust_accounting.py").read_text()
        assert "_assert_aba_generation_allowed" not in source
        assert "Deliberately NOT gated on financial_services_mock" in source
        assert "never its generation" in source
        from integrations.registry import ProviderRegistry

        assert ProviderRegistry._MOCKABLE_PROTOCOLS["payment_initiation"] == "mock_aba_writer"

    def test_provider_registry_applies_the_boundary_to_every_resolution(self):
        """One choke point, so a new get_*() cannot miss it."""
        from integrations.registry import ProviderRegistry

        source = inspect.getsource(ProviderRegistry._get_preference)
        assert "_apply_mock_boundary" in source


# ── The building-scoped surface ─────────────────────────────────────────────

class TestBuildingIntegrationsRouter:
    def test_only_the_two_managed_keys_are_writable(self):
        """An allow-list, not a parameter: otherwise this is a second, less-guarded
        way to write arbitrary per-building toggle overrides."""
        from routers.building_integrations import _MANAGED_KEYS

        assert set(_MANAGED_KEYS) == set(BOTH_KEYS)

    def test_routes_are_scoped_to_the_path_building(self):
        source = (BACKEND / "routers" / "building_integrations.py").read_text()
        assert source.count('scope_params={"building_id": "building_id"}') == 2
        assert '"building.integrations.view"' in source
        assert '"building.integrations.manage"' in source

    def test_capabilities_allow_exactly_the_three_manager_roles(self):
        """ec_member is excluded: going live is a management act, not a committee one."""
        from models.user import UserRole
        from services.capability_registry import CAPABILITY_REGISTRY

        expected = {UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER}
        for name in ("building.integrations.view", "building.integrations.manage"):
            definition = CAPABILITY_REGISTRY[name]
            assert definition.scope_type == "building"
            assert set(definition.roles) == expected, name

    @pytest.mark.asyncio
    async def test_manager_of_one_building_cannot_reach_another(self):
        from fastapi import HTTPException
        from starlette.requests import Request as StarletteRequest

        from services.capability_registry import require_capability

        def _request(building_id):
            request = StarletteRequest({
                "type": "http", "method": "GET",
                "path": f"/api/buildings/{building_id}/integrations/mock-mode",
                "query_string": b"", "headers": [], "client": ("127.0.0.1", 1),
            })
            request.scope["path_params"] = {"building_id": building_id}
            return request

        async def _verified(subject, scope, **_hints):
            return {**subject, "assigned_building_ids": [BUILDING_ID], "governance_offices": []}

        manager = {"id": "u-1", "role": "strata_manager", "effective_role": "strata_manager",
                   "building_id": BUILDING_ID}
        dependency = require_capability(
            "building.integrations.manage", scope_params={"building_id": "building_id"}
        )
        with patch("services.authorisation_context.hydrate_authorisation_claims",
                   new=AsyncMock(side_effect=_verified)):
            assert await dependency(request=_request(BUILDING_ID), current_user=manager) is manager
            with pytest.raises(HTTPException) as exc:
                await dependency(request=_request("16244"), current_user=manager)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_ec_member_and_owner_are_denied(self):
        from fastapi import HTTPException
        from starlette.requests import Request as StarletteRequest

        from services.capability_registry import require_capability

        request = StarletteRequest({
            "type": "http", "method": "GET",
            "path": f"/api/buildings/{BUILDING_ID}/integrations/mock-mode",
            "query_string": b"", "headers": [], "client": ("127.0.0.1", 1),
        })
        request.scope["path_params"] = {"building_id": BUILDING_ID}
        dependency = require_capability(
            "building.integrations.view", scope_params={"building_id": "building_id"}
        )
        for role in ("ec_member", "owner", "tenant"):
            user = {"id": "u-2", "role": role, "effective_role": role, "building_id": BUILDING_ID}
            with pytest.raises(HTTPException) as exc:
                await dependency(request=request, current_user=user)
            assert exc.value.status_code == 403, role

    def test_going_live_requires_a_reason_but_returning_to_mock_does_not(self):
        from routers import building_integrations

        source = inspect.getsource(building_integrations.set_building_integration_mock_mode)
        assert "if not payload.is_mocked and not (payload.reason or \"\").strip():" in source
        assert "422" in source

    def test_env_override_blocks_going_live_through_the_api(self):
        from routers import building_integrations

        source = inspect.getsource(building_integrations.set_building_integration_mock_mode)
        assert "global_mock_override_active() and not payload.is_mocked" in source

    def test_router_writes_only_per_building_overrides(self):
        """No global write path: one call must not be able to take everyone live."""
        source = (BACKEND / "routers" / "building_integrations.py").read_text()
        assert "upsert_feature_toggle_override" in source
        assert "update_global_feature_toggle" not in source
        assert "create_global_feature_toggle" not in source


def test_crossing_the_boundary_is_logged_like_a_protected_promotion():
    """Going live must leave the same operational trace a promotion does."""
    source = (BACKEND / "db_postgres" / "repos" / "config_repo.py").read_text()
    assert "MOCK BOUNDARY CROSSED" in source
    assert "is_disable_protected_toggle(feature_key)" in source


def test_seed_and_migration_agree_on_both_keys():
    """A key in the seed with no migration row fails the protected drift gate."""
    seed_source = (BACKEND / "seeds" / "feature_toggles.py").read_text()
    migration = (BACKEND / "alembic" / "versions" / "0095_mock_boundary_toggles.py").read_text()
    for key in BOTH_KEYS:
        assert f'"feature_key": "{key}"' in seed_source, key
        assert f'"feature_key": "{key}"' in migration, key


def test_migration_inserts_the_rows_enabled():
    """The inverse of every cutover migration beside it, asserted on the real SQL.

    An earlier version of this test asserted `"is_enabled, TRUE" not in migration`,
    which was meaningless — that string appears in neither the correct nor the
    incorrect version, so it could never fail. This parses the actual VALUES clause.
    """
    migration = (BACKEND / "alembic" / "versions" / "0095_mock_boundary_toggles.py").read_text()
    values = migration[migration.index("VALUES"):migration.index("ON CONFLICT")]
    assert ":category, TRUE," in values, "rows must be inserted enabled (mocked)"
    assert ":category, FALSE," not in values, "copy-pasted the cutover-toggle FALSE pattern"
    # ...and must NOT force it back on re-run, which would undo a gated go-live.
    on_conflict = migration[migration.index("ON CONFLICT"):]
    assert "is_enabled = EXCLUDED.is_enabled" not in on_conflict
    assert "is_enabled = TRUE" not in on_conflict
    assert "is_enabled = FALSE" not in on_conflict


def test_seed_entries_are_enabled_and_carry_the_manager_roles():
    """The mirror of test_protected_keys_in_seed_are_globally_disabled.

    That existing invariant iterates PROTECTED_TOGGLE_KEYS and asserts the seed keeps
    each one globally DISABLED and super_admin-only. These keys are deliberately not in
    that set, so nothing covered them — and "make it match its neighbours" is exactly
    the plausible edit that would flip them to False and take every building live.
    """
    from seeds.feature_toggles import DEFAULT_FEATURES

    seeded = {f["feature_key"]: f for f in DEFAULT_FEATURES}
    for key in DISABLE_PROTECTED_TOGGLE_KEYS:
        assert key in seeded, f"{key} is classified but missing from the seed of record"
        entry = seeded[key]
        assert entry["is_enabled"] is True, (
            f"seed must keep mock-boundary toggle {key} globally ENABLED — disabled "
            f"means every building talks to a live financial institution"
        )
        assert set(entry.get("roles", [])) == {"super_admin", "strata_admin", "strata_manager"}, key
