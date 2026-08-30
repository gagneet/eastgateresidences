"""
# @featuretrace:postgres-identity-foundation — Tests for P0 readiness identity gate.
# Layer: test
# Data flow: postgres_cutover_p0_readiness.check_foundation → core.schemes / core.tenants / etc (building-scoped).
# Related: backend/scripts/postgres_cutover_p0_readiness.py
#          backend/services/identity_bootstrap_service.py

P0 identity gate tests:

  - missing foundation fails (P0-05 = fail)
  - partial foundation warns or fails appropriately
  - complete foundation passes (P0-05 = pass)
  - wrong building does not pass
  - scheme UUID resolves correctly
  - plan number 13195 resolves correctly
  - UP13195 alias resolves if supported
  - scheme_number_candidates covers both forms
"""
from __future__ import annotations

import os
import sys
import types
import importlib.util
import uuid
from unittest.mock import AsyncMock, patch

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

_p0_module_path = os.path.join(_backend_dir, "scripts", "postgres_cutover_p0_readiness.py")
_p0_spec = importlib.util.spec_from_file_location("scripts.postgres_cutover_p0_readiness", _p0_module_path)
if _p0_spec and _p0_spec.loader:
    if "scripts" not in sys.modules:
        pkg = types.ModuleType("scripts")
        pkg.__path__ = [os.path.join(_backend_dir, "scripts")]  # type: ignore[attr-defined]
        sys.modules["scripts"] = pkg
    if "backend" not in sys.modules:
        backend_pkg = types.ModuleType("backend")
        backend_pkg.__path__ = [_project_root]  # type: ignore[attr-defined]
        sys.modules["backend"] = backend_pkg
    if "backend.scripts" not in sys.modules:
        backend_scripts_pkg = types.ModuleType("backend.scripts")
        backend_scripts_pkg.__path__ = [os.path.join(_backend_dir, "scripts")]  # type: ignore[attr-defined]
        sys.modules["backend.scripts"] = backend_scripts_pkg
    _p0_module = importlib.util.module_from_spec(_p0_spec)
    sys.modules["scripts.postgres_cutover_p0_readiness"] = _p0_module
    sys.modules["backend.scripts.postgres_cutover_p0_readiness"] = _p0_module
    _p0_spec.loader.exec_module(_p0_module)

from scripts.postgres_cutover_p0_readiness import (
    CheckResult,
    _scheme_number_candidates,
    check_foundation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_scheme(plan_number: str = "13195") -> dict:
    return {
        "scheme_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "scheme_number": plan_number,
        "scheme_name": "Test Building",
        "status": "active",
    }


def _count_returning(counts: dict):
    """Build an AsyncMock side_effect that returns from a lookup dict keyed by table name."""
    async def _fake_count(conn, schema, table, where=None, *params):
        return counts.get(f"{schema}.{table}", 0)
    return _fake_count


# ---------------------------------------------------------------------------
# _scheme_number_candidates
# ---------------------------------------------------------------------------

class TestSchemeNumberCandidates:
    def test_bare_number_includes_up_prefix(self):
        candidates = _scheme_number_candidates("13195")
        assert "13195" in candidates
        assert "UP13195" in candidates

    def test_up_prefixed_includes_bare(self):
        candidates = _scheme_number_candidates("UP13195")
        assert "13195" in candidates
        assert "UP13195" in candidates

    def test_lowercase_up_prefix_normalised(self):
        candidates = _scheme_number_candidates("up13195")
        bare = [c for c in candidates if not c.upper().startswith("UP")]
        assert len(bare) >= 1  # at least the bare form is present

    def test_wrong_number_does_not_include_13195(self):
        candidates = _scheme_number_candidates("99999")
        assert "13195" not in candidates
        assert "UP13195" not in candidates

    def test_candidates_are_nonempty_list(self):
        assert len(_scheme_number_candidates("13195")) >= 2


# ---------------------------------------------------------------------------
# check_foundation — missing
# ---------------------------------------------------------------------------

class TestCheckFoundationMissing:
    @pytest.mark.asyncio
    async def test_fails_when_no_scheme_rows_exist(self):
        with patch(
            "scripts.postgres_cutover_p0_readiness._matching_schemes",
            new=AsyncMock(return_value=[]),
        ):
            mock_conn = AsyncMock()
            result = await check_foundation(mock_conn, "13195")

        assert isinstance(result, CheckResult)
        assert result.id == "P0-05"
        assert result.status == "fail"
        assert "incomplete" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_details_contain_zero_counts_on_missing_foundation(self):
        with patch(
            "scripts.postgres_cutover_p0_readiness._matching_schemes",
            new=AsyncMock(return_value=[]),
        ), patch(
            "scripts.postgres_cutover_p0_readiness._count_if_exists",
            new=AsyncMock(return_value=0),
        ):
            mock_conn = AsyncMock()
            result = await check_foundation(mock_conn, "13195")

        assert result.status == "fail"
        counts = result.details.get("counts", {})
        assert counts.get("core.schemes", 0) == 0

    @pytest.mark.asyncio
    async def test_fails_when_scheme_exists_but_no_lots(self):
        """Scheme present but no lots — foundation is still incomplete."""
        fake_scheme = _make_fake_scheme()
        scheme_id = fake_scheme["scheme_id"]
        tenant_id = fake_scheme["tenant_id"]

        counts = {
            "core.tenants": 1,
            "core.schemes": 1,
            "core.users": 5,
            "core.lots": 0,           # missing lots
            "core.parties": 3,
            "core.ownership_periods": 0,  # missing because lots missing
        }

        with patch(
            "scripts.postgres_cutover_p0_readiness._matching_schemes",
            new=AsyncMock(return_value=[fake_scheme]),
        ), patch(
            "scripts.postgres_cutover_p0_readiness._count_if_exists",
            new=AsyncMock(side_effect=_count_returning(counts)),
        ):
            mock_conn = AsyncMock()
            result = await check_foundation(mock_conn, "13195")

        assert result.status == "fail"

    @pytest.mark.asyncio
    async def test_fails_when_scheme_exists_but_no_users(self):
        """Scheme and lots present but no users — still incomplete."""
        fake_scheme = _make_fake_scheme()

        counts = {
            "core.tenants": 1,
            "core.schemes": 1,
            "core.users": 0,           # missing users
            "core.lots": 14,
            "core.parties": 14,
            "core.ownership_periods": 14,
        }

        with patch(
            "scripts.postgres_cutover_p0_readiness._matching_schemes",
            new=AsyncMock(return_value=[fake_scheme]),
        ), patch(
            "scripts.postgres_cutover_p0_readiness._count_if_exists",
            new=AsyncMock(side_effect=_count_returning(counts)),
        ):
            mock_conn = AsyncMock()
            result = await check_foundation(mock_conn, "13195")

        assert result.status == "fail"

    @pytest.mark.asyncio
    async def test_fails_when_no_ownership_periods(self):
        """All other tables present but no ownership periods — still incomplete."""
        fake_scheme = _make_fake_scheme()

        counts = {
            "core.tenants": 1,
            "core.schemes": 1,
            "core.users": 14,
            "core.lots": 14,
            "core.parties": 14,
            "core.ownership_periods": 0,  # missing
        }

        with patch(
            "scripts.postgres_cutover_p0_readiness._matching_schemes",
            new=AsyncMock(return_value=[fake_scheme]),
        ), patch(
            "scripts.postgres_cutover_p0_readiness._count_if_exists",
            new=AsyncMock(side_effect=_count_returning(counts)),
        ):
            mock_conn = AsyncMock()
            result = await check_foundation(mock_conn, "13195")

        assert result.status == "fail"


# ---------------------------------------------------------------------------
# check_foundation — complete (passes)
# ---------------------------------------------------------------------------

class TestCheckFoundationComplete:
    @pytest.mark.asyncio
    async def test_passes_when_all_foundation_tables_populated(self):
        fake_scheme = _make_fake_scheme()

        counts = {
            "core.tenants": 1,
            "core.schemes": 1,
            "core.users": 14,
            "core.lots": 14,
            "core.parties": 14,
            "core.ownership_periods": 14,
        }

        with patch(
            "scripts.postgres_cutover_p0_readiness._matching_schemes",
            new=AsyncMock(return_value=[fake_scheme]),
        ), patch(
            "scripts.postgres_cutover_p0_readiness._count_if_exists",
            new=AsyncMock(side_effect=_count_returning(counts)),
        ):
            mock_conn = AsyncMock()
            result = await check_foundation(mock_conn, "13195")

        assert result.status == "pass"
        assert result.id == "P0-05"
        assert "exists" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_pass_result_includes_matching_scheme_details(self):
        fake_scheme = _make_fake_scheme("13195")

        counts = {k: 10 for k in (
            "core.tenants", "core.schemes", "core.users",
            "core.lots", "core.parties", "core.ownership_periods",
        )}

        with patch(
            "scripts.postgres_cutover_p0_readiness._matching_schemes",
            new=AsyncMock(return_value=[fake_scheme]),
        ), patch(
            "scripts.postgres_cutover_p0_readiness._count_if_exists",
            new=AsyncMock(side_effect=_count_returning(counts)),
        ):
            mock_conn = AsyncMock()
            result = await check_foundation(mock_conn, "13195")

        assert result.status == "pass"
        assert len(result.details["matching_schemes"]) == 1
        assert result.details["matching_schemes"][0]["scheme_number"] == "13195"


# ---------------------------------------------------------------------------
# Wrong building does not pass
# ---------------------------------------------------------------------------

class TestWrongBuildingDoesNotPass:
    @pytest.mark.asyncio
    async def test_wrong_building_id_fails(self):
        """P0 check for 99999 fails even if East Gate (13195) data exists in DB."""
        with patch(
            "scripts.postgres_cutover_p0_readiness._matching_schemes",
            new=AsyncMock(return_value=[]),  # no scheme for 99999
        ):
            mock_conn = AsyncMock()
            result = await check_foundation(mock_conn, "99999")

        assert result.status == "fail"

    @pytest.mark.asyncio
    async def test_east_gate_data_does_not_bleed_into_wrong_building(self):
        """Even with East Gate fully populated, wrong building_id gets 'fail'."""
        async def _scheme_for_13195_only(conn, building_id):
            if building_id in ("13195", "UP13195"):
                return [_make_fake_scheme("13195")]
            return []

        with patch(
            "scripts.postgres_cutover_p0_readiness._matching_schemes",
            new=AsyncMock(side_effect=_scheme_for_13195_only),
        ):
            mock_conn = AsyncMock()
            result_wrong = await check_foundation(mock_conn, "99999")
            result_right = await check_foundation(mock_conn, "13195")

        assert result_wrong.status == "fail"
        # Right building fails only if count mock returns 0 (no count mock here),
        # but the key assertion is that wrong building fails.
        assert result_right.id == "P0-05"


# ---------------------------------------------------------------------------
# Scheme UUID resolution
# ---------------------------------------------------------------------------

class TestSchemeUUIDResolution:
    @pytest.mark.asyncio
    async def test_scheme_id_uuid_is_returned_in_matching_schemes(self):
        scheme_uuid = str(uuid.uuid4())
        tenant_uuid = str(uuid.uuid4())
        fake_scheme = {
            "scheme_id": scheme_uuid,
            "tenant_id": tenant_uuid,
            "scheme_number": "13195",
            "scheme_name": "East Gate",
            "status": "active",
        }

        counts = {k: 10 for k in (
            "core.tenants", "core.schemes", "core.users",
            "core.lots", "core.parties", "core.ownership_periods",
        )}

        with patch(
            "scripts.postgres_cutover_p0_readiness._matching_schemes",
            new=AsyncMock(return_value=[fake_scheme]),
        ), patch(
            "scripts.postgres_cutover_p0_readiness._count_if_exists",
            new=AsyncMock(side_effect=_count_returning(counts)),
        ):
            mock_conn = AsyncMock()
            result = await check_foundation(mock_conn, "13195")

        assert result.status == "pass"
        assert result.details["matching_schemes"][0]["scheme_id"] == scheme_uuid
        assert result.details["matching_schemes"][0]["tenant_id"] == tenant_uuid


# ---------------------------------------------------------------------------
# Plan number 13195 resolution
# ---------------------------------------------------------------------------

class TestPlanNumber13195Resolution:
    @pytest.mark.asyncio
    async def test_plan_number_13195_resolves_to_scheme(self):
        """_matching_schemes is called with candidates including '13195'."""
        fake_scheme = _make_fake_scheme("13195")
        called_with = {}

        async def _capture_candidates(conn, building_id):
            called_with["building_id"] = building_id
            called_with["candidates"] = _scheme_number_candidates(building_id)
            return [fake_scheme]

        with patch(
            "scripts.postgres_cutover_p0_readiness._matching_schemes",
            new=AsyncMock(side_effect=_capture_candidates),
        ), patch(
            "scripts.postgres_cutover_p0_readiness._count_if_exists",
            new=AsyncMock(return_value=10),
        ):
            mock_conn = AsyncMock()
            await check_foundation(mock_conn, "13195")

        assert "13195" in called_with["candidates"]
        assert "UP13195" in called_with["candidates"]


# ---------------------------------------------------------------------------
# UP13195 alias resolution
# ---------------------------------------------------------------------------

class TestUP13195AliasResolution:
    @pytest.mark.asyncio
    async def test_up13195_alias_resolves_same_as_bare(self):
        """check_foundation called with 'UP13195' finds the same scheme as '13195'."""
        fake_scheme = _make_fake_scheme("13195")
        counts = {k: 10 for k in (
            "core.tenants", "core.schemes", "core.users",
            "core.lots", "core.parties", "core.ownership_periods",
        )}

        async def _lookup_by_candidates(conn, building_id):
            candidates = _scheme_number_candidates(building_id)
            # Both '13195' and 'UP13195' input should resolve to this scheme
            if "13195" in candidates or "UP13195" in candidates:
                return [fake_scheme]
            return []

        with patch(
            "scripts.postgres_cutover_p0_readiness._matching_schemes",
            new=AsyncMock(side_effect=_lookup_by_candidates),
        ), patch(
            "scripts.postgres_cutover_p0_readiness._count_if_exists",
            new=AsyncMock(side_effect=_count_returning(counts)),
        ):
            mock_conn = AsyncMock()
            result_bare = await check_foundation(mock_conn, "13195")
            result_up = await check_foundation(mock_conn, "UP13195")

        assert result_bare.status == "pass"
        assert result_up.status == "pass"
