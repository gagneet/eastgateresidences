from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from models.cutover_status import CutoverMode, DataSource, ReadinessStatus
from services import owner_service
from services.domain_source_guard import DomainSourceDecision
from services.owner_read_service import OwnerDTO


def _decision(*, operation: str, postgres_allowed: bool = False, shadow_enabled: bool = False):
    return DomainSourceDecision(
        building_id="13195",
        requested_domain="identity_core",
        domain="identity_core",
        operation=operation,
        source=DataSource.postgres if postgres_allowed else (DataSource.dual if shadow_enabled else DataSource.mongo),
        postgres_allowed=postgres_allowed,
        shadow_enabled=shadow_enabled,
        blocked_reason=None if (postgres_allowed or shadow_enabled) else "readiness is rolled_back",
        mode=CutoverMode.postgres_shadow,
        readiness_status=ReadinessStatus.rolled_back,
    )


@pytest.mark.asyncio
async def test_owner_read_runtime_state_requires_identity_control_plane(monkeypatch):
    async def fake_toggle(building_id: str, feature_key: str) -> bool:
        return True

    async def fake_guard(*, operation: str, **kwargs):
        return _decision(operation=operation, postgres_allowed=False, shadow_enabled=False)

    monkeypatch.setattr(owner_service, "is_cutover_feature_enabled", fake_toggle)
    monkeypatch.setattr(owner_service, "require_domain_source", fake_guard)

    assert await owner_service._owner_read_runtime_state("13195") == (False, False)


@pytest.mark.asyncio
async def test_get_owner_info_prefers_mongo_until_pg_toggle_enabled(monkeypatch):
    mongo_result = {"owner_name": "Mongo Owner", "owner_email": "mongo@example.com"}
    pg_result = {"owner_name": "PG Owner", "owner_email": "pg@example.com"}
    shadow_mock = AsyncMock()

    monkeypatch.setattr(owner_service, "_owner_read_runtime_state", AsyncMock(return_value=(False, True)))
    monkeypatch.setattr(owner_service, "_mongo_get_owner_info", AsyncMock(return_value=mongo_result))
    monkeypatch.setattr(owner_service, "_pg_get_owner_info", AsyncMock(return_value=pg_result))
    monkeypatch.setattr(owner_service, "_run_owner_shadow", shadow_mock)

    result = await owner_service.get_owner_info("7", "13195")

    assert result == mongo_result
    shadow_mock.assert_awaited_once_with(
        building_id="13195",
        method_name="get_owner_info",
        query_params={"unit_number": "7"},
        mongodb_value=mongo_result,
        postgres_value=pg_result,
    )


@pytest.mark.asyncio
async def test_get_owner_info_prefers_postgres_when_pg_toggle_enabled(monkeypatch):
    mongo_result = {"owner_name": "Mongo Owner", "owner_email": "mongo@example.com"}
    pg_result = {"owner_name": "PG Owner", "owner_email": "pg@example.com"}
    shadow_mock = AsyncMock()

    monkeypatch.setattr(owner_service, "_owner_read_runtime_state", AsyncMock(return_value=(True, True)))
    monkeypatch.setattr(owner_service, "_mongo_get_owner_info", AsyncMock(return_value=mongo_result))
    monkeypatch.setattr(owner_service, "_pg_get_owner_info", AsyncMock(return_value=pg_result))
    monkeypatch.setattr(owner_service, "_run_owner_shadow", shadow_mock)

    result = await owner_service.get_owner_info("7", "13195")

    assert result == pg_result
    shadow_mock.assert_awaited_once_with(
        building_id="13195",
        method_name="get_owner_info",
        query_params={"unit_number": "7"},
        mongodb_value=mongo_result,
        postgres_value=pg_result,
    )


def test_record_from_owner_rows_preserves_joint_owner_shape():
    rows = [
        OwnerDTO(
            building_id="13195",
            party_id="secondary",
            user_id="user-b",
            email="joint@example.com",
            full_name="Joint Owner",
            is_primary_owner=False,
            ownership_share=Decimal("0.5"),
            lot_id="lot-7",
            lot_number="7",
            unit_number="7",
            valid_from=date(2026, 1, 1),
            valid_to=None,
        ),
        OwnerDTO(
            building_id="13195",
            party_id="primary",
            user_id="user-a",
            email="primary@example.com",
            full_name="Primary Owner",
            is_primary_owner=True,
            ownership_share=Decimal("0.5"),
            lot_id="lot-7",
            lot_number="7",
            unit_number="7",
            valid_from=date(2026, 1, 1),
            valid_to=None,
        ),
    ]

    result = owner_service._record_from_owner_rows(rows)

    assert result["owner_name"] == "Primary Owner"
    assert result["owner_email"] == "primary@example.com"
    assert result["owner_name_b"] == "Joint Owner"
    assert result["co_owner_email"] == "joint@example.com"


@pytest.mark.asyncio
async def test_resolve_agm_voters_prefers_postgres_when_enabled(monkeypatch):
    as_at_date = date(2026, 5, 3)
    mongo_result = [{"unit_number": "7", "user_id": "mongo-user"}]
    pg_result = [{"unit_number": "7", "user_id": "pg-user"}]
    shadow_mock = AsyncMock()

    monkeypatch.setattr(owner_service, "_owner_read_runtime_state", AsyncMock(return_value=(True, True)))
    monkeypatch.setattr(owner_service, "_mongo_resolve_agm_voters", AsyncMock(return_value=mongo_result))
    monkeypatch.setattr(owner_service, "_pg_resolve_agm_voters", AsyncMock(return_value=pg_result))
    monkeypatch.setattr(owner_service, "_run_owner_shadow", shadow_mock)

    result = await owner_service.resolve_agm_voters("13195", as_at_date)

    assert result == pg_result
    shadow_mock.assert_awaited_once_with(
        building_id="13195",
        method_name="resolve_agm_voters",
        query_params={"as_at_date": "2026-05-03"},
        mongodb_value=mongo_result,
        postgres_value=pg_result,
    )


@pytest.mark.asyncio
async def test_is_user_current_owner_of_unit_uses_mongo_when_pg_disabled(monkeypatch):
    as_at_date = date(2026, 5, 3)
    pg_check = AsyncMock(return_value=False)
    mongo_check = AsyncMock(return_value=True)
    shadow_mock = AsyncMock()

    monkeypatch.setattr(owner_service, "_owner_read_runtime_state", AsyncMock(return_value=(False, False)))
    monkeypatch.setattr(owner_service, "_pg_is_user_current_owner_of_unit", pg_check)
    monkeypatch.setattr(owner_service, "_mongo_is_user_current_owner_of_unit", mongo_check)
    monkeypatch.setattr(owner_service, "_run_owner_shadow", shadow_mock)

    result = await owner_service.is_user_current_owner_of_unit(
        "13195", "user-1", "101", as_at_date=as_at_date
    )

    assert result is True
    mongo_check.assert_awaited_once()
    pg_check.assert_not_awaited()
    shadow_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_is_user_current_owner_of_unit_prefers_pg_and_runs_shadow(monkeypatch):
    as_at_date = date(2026, 5, 3)
    pg_check = AsyncMock(return_value=True)
    mongo_check = AsyncMock(return_value=False)
    shadow_mock = AsyncMock()

    monkeypatch.setattr(owner_service, "_owner_read_runtime_state", AsyncMock(return_value=(True, True)))
    monkeypatch.setattr(owner_service, "_pg_is_user_current_owner_of_unit", pg_check)
    monkeypatch.setattr(owner_service, "_mongo_is_user_current_owner_of_unit", mongo_check)
    monkeypatch.setattr(owner_service, "_run_owner_shadow", shadow_mock)

    result = await owner_service.is_user_current_owner_of_unit(
        "13195", "user-1", "101", as_at_date=as_at_date
    )

    assert result is True
    pg_check.assert_awaited_once()
    shadow_mock.assert_awaited_once_with(
        building_id="13195",
        method_name="is_user_current_owner_of_unit",
        query_params={"user_id": "user-1", "unit_identifier": "101", "as_at_date": "2026-05-03"},
        mongodb_value=False,
        postgres_value=True,
    )
