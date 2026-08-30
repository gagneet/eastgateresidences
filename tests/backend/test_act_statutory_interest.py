"""
ACT Statutory Interest Tests — GAP-JUR-ACT-002 (UTMA 2011 s.94)

Covers:
  - compute_accrued_interest(): pure arithmetic, edge cases
  - get_effective_interest_rate(): 3-tier resolution (special resolution → building config → ACT default)
  - record_special_resolution_rate(): persist + supersede prior
  - GET /arrears/interest-rates: role guards, response shape, statute cite
  - POST /arrears/special-resolution-rate: role guards, validation, 201 response
  - Jurisdiction-aware rate ceiling/floor (ACT/NSW/VIC/QLD) via domain.jurisdictional_rules

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_act_statutory_interest.py -v
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.arrears_interest_service import (
    DEFAULT_INTEREST_RATE_PCT,
    MAX_INTEREST_RATE_PCT,
    compute_accrued_interest,
    get_building_interest_rate,
    get_effective_interest_rate,
    record_special_resolution_rate,
)


# ─────────────────────────────────────────────────────────────────────────────
# compute_accrued_interest — pure function, no DB
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeAccruedInterest:
    """Unit tests for the core interest arithmetic (UTMA 2011 s.96)."""

    def _dt(self, iso: str) -> datetime:
        return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)

    def test_example_from_docstring(self):
        """$100 @ 10% for 30 days = $0.82."""
        result = compute_accrued_interest(100.0, self._dt("2026-01-01"), self._dt("2026-01-31"), 10.0)
        assert result == pytest.approx(0.82, abs=0.01)

    def test_zero_principal_returns_zero(self):
        result = compute_accrued_interest(0.0, self._dt("2026-01-01"), self._dt("2026-02-01"))
        assert result == 0.0

    def test_negative_principal_returns_zero(self):
        result = compute_accrued_interest(-100.0, self._dt("2026-01-01"), self._dt("2026-02-01"))
        assert result == 0.0

    def test_as_at_equals_overdue_since_returns_zero(self):
        dt = self._dt("2026-01-01")
        assert compute_accrued_interest(500.0, dt, dt) == 0.0

    def test_as_at_before_overdue_since_returns_zero(self):
        result = compute_accrued_interest(500.0, self._dt("2026-02-01"), self._dt("2026-01-01"))
        assert result == 0.0

    def test_zero_rate_returns_zero(self):
        result = compute_accrued_interest(500.0, self._dt("2026-01-01"), self._dt("2026-02-01"), 0.0)
        assert result == 0.0

    def test_rate_above_max_raises(self):
        with pytest.raises(ValueError, match="exceeds legal maximum"):
            compute_accrued_interest(500.0, self._dt("2026-01-01"), self._dt("2026-02-01"), 21.0)

    def test_max_rate_20_pct_accepted(self):
        result = compute_accrued_interest(1000.0, self._dt("2026-01-01"), self._dt("2026-02-01"), 20.0)
        assert result > 0.0

    def test_default_rate_applied_when_not_specified(self):
        explicit = compute_accrued_interest(1000.0, self._dt("2026-01-01"), self._dt("2026-02-01"), DEFAULT_INTEREST_RATE_PCT)
        default = compute_accrued_interest(1000.0, self._dt("2026-01-01"), self._dt("2026-02-01"))
        assert explicit == default

    def test_result_rounded_to_two_dp(self):
        result = compute_accrued_interest(333.33, self._dt("2026-01-01"), self._dt("2026-04-01"))
        assert round(result, 2) == result

    def test_higher_rate_produces_higher_interest(self):
        low = compute_accrued_interest(1000.0, self._dt("2026-01-01"), self._dt("2026-04-01"), 10.0)
        high = compute_accrued_interest(1000.0, self._dt("2026-01-01"), self._dt("2026-04-01"), 20.0)
        assert high == pytest.approx(low * 2, rel=0.01)

    def test_multi_tenant_same_principal_different_building(self):
        """Rate computation must not be contaminated by building context — pure function."""
        r1 = compute_accrued_interest(500.0, self._dt("2026-01-01"), self._dt("2026-03-01"), 10.0)
        r2 = compute_accrued_interest(500.0, self._dt("2026-01-01"), self._dt("2026-03-01"), 10.0)
        assert r1 == r2

    def test_mixed_timezone_aware_naive_does_not_raise(self):
        """Aware overdue_since + naive as_at must not raise TypeError (timezone normalization)."""
        aware_overdue = datetime(2026, 1, 1, tzinfo=timezone.utc)
        naive_as_at = datetime(2026, 1, 31)  # no tzinfo
        result = compute_accrued_interest(100.0, aware_overdue, naive_as_at, 10.0)
        assert result == pytest.approx(0.82, abs=0.01)

    def test_mixed_timezone_naive_aware_does_not_raise(self):
        """Naive overdue_since + aware as_at must not raise TypeError (timezone normalization)."""
        naive_overdue = datetime(2026, 1, 1)
        aware_as_at = datetime(2026, 1, 31, tzinfo=timezone.utc)
        result = compute_accrued_interest(100.0, naive_overdue, aware_as_at, 10.0)
        assert result == pytest.approx(0.82, abs=0.01)

    def test_rate_above_max_raises_even_when_dates_reversed(self):
        """Rate validation must fire before date comparison — invalid rate always raises."""
        with pytest.raises(ValueError, match="exceeds legal maximum"):
            # as_at before overdue_since would previously short-circuit and hide the bad rate
            compute_accrued_interest(500.0, self._dt("2026-02-01"), self._dt("2026-01-01"), 21.0)


# ─────────────────────────────────────────────────────────────────────────────
# get_building_interest_rate — sync helper
# ─────────────────────────────────────────────────────────────────────────────

class TestGetBuildingInterestRate:
    def test_none_config_returns_default(self):
        assert get_building_interest_rate(None) == DEFAULT_INTEREST_RATE_PCT

    def test_empty_config_returns_default(self):
        assert get_building_interest_rate({}) == DEFAULT_INTEREST_RATE_PCT

    def test_valid_rate_returned(self):
        assert get_building_interest_rate({"arrears_interest_rate_pct": 15.0}) == 15.0

    def test_rate_capped_at_max(self):
        assert get_building_interest_rate({"arrears_interest_rate_pct": 25.0}) == MAX_INTEREST_RATE_PCT

    def test_invalid_string_returns_default(self):
        assert get_building_interest_rate({"arrears_interest_rate_pct": "not_a_number"}) == DEFAULT_INTEREST_RATE_PCT


# ─────────────────────────────────────────────────────────────────────────────
# get_effective_interest_rate — async, 3-tier resolution
# ─────────────────────────────────────────────────────────────────────────────

def _mock_db(resolution=None, building_config=None):
    db = MagicMock()
    db.committee_resolutions.find_one = AsyncMock(return_value=resolution)
    db.buildings.find_one = AsyncMock(return_value=building_config)
    return db


class TestGetEffectiveInterestRate:
    @pytest.mark.asyncio
    async def test_act_default_when_no_override(self):
        db = _mock_db(resolution=None, building_config=None)
        result = await get_effective_interest_rate("13195", db)
        assert result["rate_pct"] == DEFAULT_INTEREST_RATE_PCT
        assert result["source"] == "act_default"
        assert result["resolution_id"] is None
        assert result["max_rate_pct"] == MAX_INTEREST_RATE_PCT

    @pytest.mark.asyncio
    async def test_building_config_overrides_default(self):
        db = _mock_db(resolution=None, building_config={"arrears_interest_rate_pct": 12.5})
        result = await get_effective_interest_rate("13195", db)
        assert result["rate_pct"] == 12.5
        assert result["source"] == "building_config"
        assert result["resolution_id"] is None

    @pytest.mark.asyncio
    async def test_special_resolution_wins_over_building_config(self):
        resolution = {
            "id": "res-001",
            "interest_rate_pct": 18.0,
            "passed_date": "2026-03-15",
            "expires_at": None,
            "status": "active",
        }
        db = _mock_db(resolution=resolution, building_config={"arrears_interest_rate_pct": 12.5})
        result = await get_effective_interest_rate("13195", db)
        assert result["rate_pct"] == 18.0
        assert result["source"] == "special_resolution"
        assert result["resolution_id"] == "res-001"
        assert result["resolution_date"] == "2026-03-15"

    @pytest.mark.asyncio
    async def test_special_resolution_capped_at_max(self):
        """Even a stored resolution rate above 20% must be capped."""
        resolution = {
            "id": "res-002",
            "interest_rate_pct": 25.0,  # invalid but defensive cap applies
            "passed_date": "2026-03-15",
            "expires_at": None,
        }
        db = _mock_db(resolution=resolution, building_config=None)
        result = await get_effective_interest_rate("13195", db)
        assert result["rate_pct"] == MAX_INTEREST_RATE_PCT

    @pytest.mark.asyncio
    async def test_resolution_rate_wins_even_though_buildings_is_queried_for_jurisdiction(self):
        """buildings.find_one IS called even when a resolution is active (needed to
        resolve jurisdiction for the rate ceiling) — but the resolution's rate still
        wins over the building_config value, since jurisdiction resolution doesn't
        touch rate-source precedence."""
        resolution = {"id": "r", "interest_rate_pct": 15.0, "passed_date": "2026-01-01", "expires_at": None}
        db = _mock_db(resolution=resolution, building_config={"arrears_interest_rate_pct": 12.0})
        result = await get_effective_interest_rate("13195", db)
        db.buildings.find_one.assert_called_once()
        assert result["source"] == "special_resolution"
        assert result["rate_pct"] == 15.0

    @pytest.mark.asyncio
    async def test_multi_tenant_isolation(self):
        """Different building_ids query separate committee_resolutions — no cross-contamination."""
        db_a = _mock_db(resolution=None, building_config={"arrears_interest_rate_pct": 12.0})
        db_b = _mock_db(resolution=None, building_config=None)
        res_a = await get_effective_interest_rate("13195", db_a)
        res_b = await get_effective_interest_rate("16244", db_b)
        assert res_a["rate_pct"] == 12.0
        assert res_b["rate_pct"] == DEFAULT_INTEREST_RATE_PCT
        # Verify each db received the correct building_id
        call_kwargs_a = db_a.committee_resolutions.find_one.call_args[0][0]
        assert call_kwargs_a["building_id"] == "13195"
        call_kwargs_b = db_b.committee_resolutions.find_one.call_args[0][0]
        assert call_kwargs_b["building_id"] == "16244"


# ─────────────────────────────────────────────────────────────────────────────
# record_special_resolution_rate — async, writes to committee_resolutions
# ─────────────────────────────────────────────────────────────────────────────

def _mock_write_db(jurisdiction=None):
    db = MagicMock()
    db.buildings.find_one = AsyncMock(
        return_value={"jurisdiction": jurisdiction} if jurisdiction else None
    )
    db.committee_resolutions.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
    db.committee_resolutions.insert_one = AsyncMock(return_value=MagicMock(inserted_id="x"))
    return db


class TestRecordSpecialResolutionRate:
    @pytest.mark.asyncio
    async def test_persists_new_resolution(self):
        db = _mock_write_db()
        doc = await record_special_resolution_rate(
            building_id="13195",
            interest_rate_pct=15.0,
            passed_date="2026-03-15",
            passed_by="admin-1",
            expires_at=None,
            notes="Annual general meeting resolution",
            db=db,
        )
        db.committee_resolutions.insert_one.assert_called_once()
        assert doc["interest_rate_pct"] == 15.0
        assert doc["building_id"] == "13195"
        assert doc["status"] == "active"
        assert doc["resolution_type"] == "levy_interest_rate_override"
        assert "id" in doc

    @pytest.mark.asyncio
    async def test_supersedes_prior_active_resolution(self):
        db = _mock_write_db()
        await record_special_resolution_rate(
            "13195", 15.0, "2026-03-15", "admin-1", None, None, db
        )
        db.committee_resolutions.update_many.assert_called_once()
        filter_used = db.committee_resolutions.update_many.call_args[0][0]
        assert filter_used["status"] == "active"
        assert filter_used["resolution_type"] == "levy_interest_rate_override"

    @pytest.mark.asyncio
    async def test_rate_above_max_raises(self):
        db = _mock_write_db()
        with pytest.raises(ValueError, match="exceeds legal maximum"):
            await record_special_resolution_rate("13195", 21.0, "2026-03-15", "admin-1", None, None, db)
        db.committee_resolutions.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_rate_below_default_raises(self):
        db = _mock_write_db()
        with pytest.raises(ValueError, match="below the statutory default"):
            await record_special_resolution_rate("13195", 8.0, "2026-03-15", "admin-1", None, None, db)
        db.committee_resolutions.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_expires_at_stored(self):
        db = _mock_write_db()
        doc = await record_special_resolution_rate(
            "13195", 18.0, "2026-03-15", "admin-1", "2027-03-15", None, db
        )
        assert doc["expires_at"] == "2027-03-15"

    @pytest.mark.asyncio
    async def test_building_id_isolation(self):
        """Supersede query must filter by building_id to avoid cross-tenant contamination."""
        db = _mock_write_db()
        await record_special_resolution_rate("16244", 15.0, "2026-03-15", "admin-2", None, None, db)
        filter_used = db.committee_resolutions.update_many.call_args[0][0]
        assert filter_used["building_id"] == "16244"


# ─────────────────────────────────────────────────────────────────────────────
# Router endpoint tests
# ─────────────────────────────────────────────────────────────────────────────

def _router_mock_db(resolution=None, building_config=None):
    db = MagicMock()
    db.committee_resolutions.find_one = AsyncMock(return_value=resolution)
    db.buildings.find_one = AsyncMock(return_value=building_config)
    db.committee_resolutions.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
    db.committee_resolutions.insert_one = AsyncMock(return_value=MagicMock(inserted_id="x"))
    return db


class TestGetArrearsInterestRateEndpoint:
    @pytest.mark.asyncio
    async def test_returns_act_default(self):
        import routers.finance as fin
        mock_db = _router_mock_db(resolution=None, building_config=None)
        with patch("routers.finance.db", mock_db):
            result = await fin.get_arrears_interest_rate(
                building_id="13195",
                current_user={"id": "u1", "role": "strata_manager"},
            )
        assert result.rate_pct == DEFAULT_INTEREST_RATE_PCT
        assert result.source == "act_default"
        # domain/rules/act.json is this repo's single source of truth for statutory
        # citations; it cites s.94 for the interest provisions (checked 3 independent
        # keys agree) — the historical "s.96" in older comments/docstrings predates
        # the jurisdictional rule engine and was never reconciled against it.
        assert result.statute == "UTMA 2011 s.94"
        assert result.max_rate_pct == MAX_INTEREST_RATE_PCT
        assert result.building_id == "13195"

    @pytest.mark.asyncio
    async def test_returns_special_resolution_rate(self):
        import routers.finance as fin
        resolution = {"id": "r1", "interest_rate_pct": 18.0, "passed_date": "2026-01-10", "expires_at": None}
        mock_db = _router_mock_db(resolution=resolution)
        with patch("routers.finance.db", mock_db):
            result = await fin.get_arrears_interest_rate(
                building_id="13195",
                current_user={"id": "u1", "role": "strata_manager"},
            )
        assert result.rate_pct == 18.0
        assert result.source == "special_resolution"
        assert result.resolution_id == "r1"

    @pytest.mark.asyncio
    async def test_owner_forbidden(self):
        from fastapi import HTTPException
        import routers.finance as fin
        mock_db = _router_mock_db()
        with patch("routers.finance.db", mock_db), pytest.raises(HTTPException) as exc_info:
            await fin.get_arrears_interest_rate(
                building_id="13195",
                current_user={"id": "u1", "role": "owner"},
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_tenant_forbidden(self):
        from fastapi import HTTPException
        import routers.finance as fin
        mock_db = _router_mock_db()
        with patch("routers.finance.db", mock_db), pytest.raises(HTTPException) as exc_info:
            await fin.get_arrears_interest_rate(
                building_id="13195",
                current_user={"id": "u1", "role": "tenant"},
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_ec_member_allowed(self):
        import routers.finance as fin
        mock_db = _router_mock_db()
        with patch("routers.finance.db", mock_db):
            result = await fin.get_arrears_interest_rate(
                building_id="13195",
                current_user={"id": "u1", "role": "ec_member"},
            )
        assert result.rate_pct == DEFAULT_INTEREST_RATE_PCT

    @pytest.mark.asyncio
    async def test_effective_role_used_for_elevated_user(self):
        """Elevated owner (effective_role=ec_member) must be allowed — raw role alone would 403."""
        import routers.finance as fin
        mock_db = _router_mock_db()
        with patch("routers.finance.db", mock_db):
            result = await fin.get_arrears_interest_rate(
                building_id="13195",
                current_user={"id": "u1", "role": "owner", "effective_role": "ec_member"},
            )
        assert result.rate_pct == DEFAULT_INTEREST_RATE_PCT

    @pytest.mark.asyncio
    async def test_multi_tenant_response_has_correct_building_id(self):
        import routers.finance as fin
        mock_db = _router_mock_db()
        with patch("routers.finance.db", mock_db):
            result = await fin.get_arrears_interest_rate(
                building_id="16244",
                current_user={"id": "u1", "role": "strata_manager"},
            )
        assert result.building_id == "16244"


class TestSetSpecialResolutionInterestRateEndpoint:
    def _payload(self, rate=15.0, passed_date="2026-03-15", expires_at=None, notes=None):
        from models.finance import SpecialResolutionRateCreate
        return SpecialResolutionRateCreate(
            interest_rate_pct=rate,
            passed_date=passed_date,
            expires_at=expires_at,
            notes=notes,
        )

    @pytest.mark.asyncio
    async def test_strata_manager_can_create(self):
        import routers.finance as fin
        mock_db = _router_mock_db()
        with patch("routers.finance.db", mock_db):
            result = await fin.set_special_resolution_interest_rate(
                payload=self._payload(15.0),
                building_id="13195",
                current_user={"id": "mgr-1", "role": "strata_manager"},
            )
        assert result.interest_rate_pct == 15.0
        assert result.status == "active"
        assert result.building_id == "13195"
        assert result.resolution_type == "levy_interest_rate_override"

    @pytest.mark.asyncio
    async def test_super_admin_can_create(self):
        import routers.finance as fin
        mock_db = _router_mock_db()
        with patch("routers.finance.db", mock_db):
            result = await fin.set_special_resolution_interest_rate(
                payload=self._payload(20.0),
                building_id="13195",
                current_user={"id": "sa-1", "role": "super_admin"},
            )
        assert result.interest_rate_pct == 20.0

    @pytest.mark.asyncio
    async def test_ec_member_forbidden(self):
        from fastapi import HTTPException
        import routers.finance as fin
        mock_db = _router_mock_db()
        with patch("routers.finance.db", mock_db), pytest.raises(HTTPException) as exc_info:
            await fin.set_special_resolution_interest_rate(
                payload=self._payload(),
                building_id="13195",
                current_user={"id": "ec-1", "role": "ec_member"},
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_owner_forbidden(self):
        from fastapi import HTTPException
        import routers.finance as fin
        mock_db = _router_mock_db()
        with patch("routers.finance.db", mock_db), pytest.raises(HTTPException) as exc_info:
            await fin.set_special_resolution_interest_rate(
                payload=self._payload(),
                building_id="13195",
                current_user={"id": "o-1", "role": "owner"},
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_passed_by_is_current_user_id(self):
        import routers.finance as fin
        mock_db = _router_mock_db()
        with patch("routers.finance.db", mock_db):
            result = await fin.set_special_resolution_interest_rate(
                payload=self._payload(),
                building_id="13195",
                current_user={"id": "mgr-42", "role": "strata_manager"},
            )
        assert result.passed_by == "mgr-42"

    @pytest.mark.asyncio
    async def test_service_valueerror_becomes_422(self):
        """A ValueError from the service (rate out of range) must surface as HTTP 422."""
        from fastapi import HTTPException
        import routers.finance as fin
        mock_db = _router_mock_db()
        with patch("routers.finance.db", mock_db), \
             patch("routers.finance.record_special_resolution_rate",
                   AsyncMock(side_effect=ValueError("rate exceeds legal maximum"))), \
             pytest.raises(HTTPException) as exc_info:
            await fin.set_special_resolution_interest_rate(
                payload=self._payload(15.0),
                building_id="13195",
                current_user={"id": "mgr-1", "role": "strata_manager"},
            )
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_building_id_isolation(self):
        """Resolution must be scoped to the requested building, not a different tenant."""
        import routers.finance as fin
        mock_db = _router_mock_db()
        with patch("routers.finance.db", mock_db):
            result = await fin.set_special_resolution_interest_rate(
                payload=self._payload(),
                building_id="16244",
                current_user={"id": "mgr-1", "role": "strata_manager"},
            )
        assert result.building_id == "16244"
        # Supersede query must have targeted 16244, not some other building
        filter_used = mock_db.committee_resolutions.update_many.call_args[0][0]
        assert filter_used["building_id"] == "16244"


# ─────────────────────────────────────────────────────────────────────────────
# Jurisdiction-aware rate ceiling/floor — ACT/NSW/VIC/QLD via rule_engine
# ─────────────────────────────────────────────────────────────────────────────

def _mock_db_with_jurisdiction(jurisdiction, resolution=None, arrears_interest_rate_pct=None):
    db = MagicMock()
    db.committee_resolutions.find_one = AsyncMock(return_value=resolution)
    building_config = {"jurisdiction": jurisdiction}
    if arrears_interest_rate_pct is not None:
        building_config["arrears_interest_rate_pct"] = arrears_interest_rate_pct
    db.buildings.find_one = AsyncMock(return_value=building_config)
    return db


def _mock_write_db_with_jurisdiction(jurisdiction):
    db = MagicMock()
    db.buildings.find_one = AsyncMock(return_value={"jurisdiction": jurisdiction})
    db.committee_resolutions.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
    db.committee_resolutions.insert_one = AsyncMock(return_value=MagicMock(inserted_id="x"))
    return db


class TestJurisdictionAwareInterestRate:
    """domain/rules/{act,nsw,vic,qld}.json ceilings, reused via rule_engine —
    GAP: record_special_resolution_rate() previously applied ACT's 10-20% band
    to every building regardless of jurisdiction."""

    @pytest.mark.asyncio
    async def test_act_default_and_ceiling(self):
        db = _mock_db_with_jurisdiction("ACT")
        result = await get_effective_interest_rate("13195", db)
        assert result["rate_pct"] == 10.0
        assert result["max_rate_pct"] == 20.0
        assert result["jurisdiction"] == "ACT"
        # "act_default" is the historical, backward-compatible string for ACT specifically.
        assert result["source"] == "act_default"

    @pytest.mark.asyncio
    async def test_nsw_default_and_ceiling_no_increase_permitted(self):
        db = _mock_db_with_jurisdiction("NSW")
        result = await get_effective_interest_rate("13195", db)
        assert result["rate_pct"] == 10.0
        assert result["max_rate_pct"] == 10.0  # NSW: no special-resolution increase
        assert result["jurisdiction"] == "NSW"
        # Regression guard: a non-ACT building must NOT report "act_default" as its
        # source — that string is reserved for ACT so it stays self-consistent.
        assert result["source"] == "jurisdiction_default"

    @pytest.mark.asyncio
    async def test_vic_default_and_ceiling_no_increase_permitted(self):
        db = _mock_db_with_jurisdiction("VIC")
        result = await get_effective_interest_rate("13195", db)
        assert result["rate_pct"] == 10.0
        assert result["max_rate_pct"] == 10.0  # VIC: no special-resolution increase
        assert result["jurisdiction"] == "VIC"
        assert result["source"] == "jurisdiction_default"

    @pytest.mark.asyncio
    async def test_qld_default_and_ceiling_annualized(self):
        db = _mock_db_with_jurisdiction("QLD")
        result = await get_effective_interest_rate("13195", db)
        assert result["rate_pct"] == 30.0
        assert result["max_rate_pct"] == 30.0
        assert result["source"] == "jurisdiction_default"
        assert result["jurisdiction"] == "QLD"

    @pytest.mark.asyncio
    async def test_nsw_special_resolution_rejects_any_increase(self):
        """NSW/VIC statutes permit no special-resolution increase — floor == ceiling."""
        db = _mock_write_db_with_jurisdiction("NSW")
        with pytest.raises(ValueError, match="exceeds legal maximum"):
            await record_special_resolution_rate("13195", 15.0, "2026-03-15", "admin-1", None, None, db)

    @pytest.mark.asyncio
    async def test_vic_special_resolution_rejects_any_increase(self):
        db = _mock_write_db_with_jurisdiction("VIC")
        with pytest.raises(ValueError, match="exceeds legal maximum"):
            await record_special_resolution_rate("13195", 12.0, "2026-03-15", "admin-1", None, None, db)

    @pytest.mark.asyncio
    async def test_nsw_special_resolution_at_exactly_10_percent_accepted(self):
        """The only legal NSW rate (10%, i.e. no real increase) must still be accepted."""
        db = _mock_write_db_with_jurisdiction("NSW")
        doc = await record_special_resolution_rate("13195", 10.0, "2026-03-15", "admin-1", None, None, db)
        assert doc["interest_rate_pct"] == 10.0

    @pytest.mark.asyncio
    async def test_qld_special_resolution_at_30_percent_accepted(self):
        """QLD's floor and ceiling are both 30% (annual-equivalent of the fixed
        2.5%/month statutory rate, BCCM reg 157) — no discretion, like NSW/VIC's
        fixed 10%, just at a different value. A rate that would have raised
        under the old hardcoded ACT-only 20% cap must now pass at the correct
        QLD value."""
        db = _mock_write_db_with_jurisdiction("QLD")
        doc = await record_special_resolution_rate("13195", 30.0, "2026-03-15", "admin-1", None, None, db)
        assert doc["interest_rate_pct"] == 30.0

    @pytest.mark.asyncio
    async def test_qld_special_resolution_above_30_percent_rejected(self):
        db = _mock_write_db_with_jurisdiction("QLD")
        with pytest.raises(ValueError, match="exceeds legal maximum"):
            await record_special_resolution_rate("13195", 31.0, "2026-03-15", "admin-1", None, None, db)

    @pytest.mark.asyncio
    async def test_qld_special_resolution_below_30_percent_rejected(self):
        db = _mock_write_db_with_jurisdiction("QLD")
        with pytest.raises(ValueError, match="below the statutory default"):
            await record_special_resolution_rate("13195", 25.0, "2026-03-15", "admin-1", None, None, db)

    @pytest.mark.asyncio
    async def test_compute_accrued_interest_honours_custom_max_rate_pct(self):
        """QLD's 25% rate must not trip the ACT-only default 20% ceiling when the
        caller passes the correct jurisdiction ceiling explicitly."""
        interest = compute_accrued_interest(
            principal=1000.0,
            overdue_since=datetime(2026, 1, 1),
            as_at=datetime(2026, 2, 1),
            annual_rate_pct=25.0,
            max_rate_pct=30.0,
        )
        assert interest > 0

    @pytest.mark.asyncio
    async def test_compute_accrued_interest_still_rejects_above_default_max(self):
        """Backward compatibility: omitting max_rate_pct keeps the old ACT-only 20% guard."""
        with pytest.raises(ValueError, match="exceeds legal maximum"):
            compute_accrued_interest(
                principal=1000.0,
                overdue_since=datetime(2026, 1, 1),
                as_at=datetime(2026, 2, 1),
                annual_rate_pct=25.0,
            )

    @pytest.mark.asyncio
    async def test_unrecognised_jurisdiction_falls_back_to_act_instead_of_raising(self):
        """buildings.jurisdiction is not validated at write time on every onboarding
        path (legacy migrations, portfolio imports) — a malformed value must not
        turn into an unhandled 500 on the arrears endpoints for that building."""
        db = _mock_db_with_jurisdiction("NZ")  # not one of ACT/NSW/VIC/QLD
        result = await get_effective_interest_rate("13195", db)
        assert result["jurisdiction"] == "ACT"
        assert result["rate_pct"] == 10.0
        assert result["max_rate_pct"] == 20.0
        assert result["source"] == "act_default"

    @pytest.mark.asyncio
    async def test_record_special_resolution_unrecognised_jurisdiction_falls_back_to_act(self):
        db = _mock_write_db_with_jurisdiction("NZ")
        with pytest.raises(ValueError, match="exceeds legal maximum"):
            await record_special_resolution_rate("13195", 21.0, "2026-03-15", "admin-1", None, None, db)
