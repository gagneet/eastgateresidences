import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.shadow_read_validator import ShadowReadValidator


@pytest.mark.asyncio
async def test_validate_owners_normalizes_legacy_co_owner_aliases(monkeypatch):
    validator = ShadowReadValidator()
    log_mock = AsyncMock()

    monkeypatch.setattr(
        "services.shadow_read_validator.config_repo.resolve_scheme_context",
        AsyncMock(return_value=MagicMock(tenant_id=uuid.uuid4(), scheme_id=uuid.uuid4())),
    )
    monkeypatch.setattr(validator, "_log_divergence", log_mock)

    result = await validator.validate_owners(
        building_id="13195",
        method_name="get_owner_info",
        query_params={"unit_number": "7"},
        mongodb_value={
            "owner_name": "Jane Smith",
            "owner_email": "jane@example.com",
            "co_owner_name": "John Smith",
            "co_owner_email": "john@example.com",
        },
        postgres_value={
            "owner_name": "jane smith",
            "owner_email": "JANE@example.com",
            "owner_name_b": "john smith",
            "owner_email_b": "john@example.com",
        },
    )

    assert result.diverged is False
    assert result.diverging_fields == []
    log_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_validate_owners_normalizes_joint_owner_order_for_agm(monkeypatch):
    validator = ShadowReadValidator()
    log_mock = AsyncMock()

    monkeypatch.setattr(
        "services.shadow_read_validator.config_repo.resolve_scheme_context",
        AsyncMock(return_value=MagicMock(tenant_id=uuid.uuid4(), scheme_id=uuid.uuid4())),
    )
    monkeypatch.setattr(validator, "_log_divergence", log_mock)

    mongo_value = [
        {"unit_number": "7", "lot_number": "7", "email": "b@example.com", "full_name": "Bravo Owner", "is_primary_owner": False, "ownership_share": "0.5", "entitlement_units": "10"},
        {"unit_number": "7", "lot_number": "7", "email": "a@example.com", "full_name": "Alpha Owner", "is_primary_owner": True, "ownership_share": "0.5", "entitlement_units": "10"},
    ]
    postgres_value = list(reversed(mongo_value))

    result = await validator.validate_owners(
        building_id="13195",
        method_name="resolve_agm_voters",
        query_params={"as_at_date": "2026-05-03"},
        mongodb_value=mongo_value,
        postgres_value=postgres_value,
    )

    assert result.diverged is False
    assert result.diverging_fields == []
    log_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_validate_owners_returns_structured_divergence_fields(monkeypatch):
    validator = ShadowReadValidator()
    log_mock = AsyncMock()

    monkeypatch.setattr(
        "services.shadow_read_validator.config_repo.resolve_scheme_context",
        AsyncMock(return_value=MagicMock(tenant_id=uuid.uuid4(), scheme_id=uuid.uuid4())),
    )
    monkeypatch.setattr(validator, "_log_divergence", log_mock)

    result = await validator.validate_owners(
        building_id="13195",
        method_name="get_all_unit_owners",
        query_params={},
        mongodb_value={"7": {"owner_name": "Mongo Owner", "owner_email": "mongo@example.com"}},
        postgres_value={"7": {"owner_name": "Postgres Owner", "owner_email": "mongo@example.com"}},
        is_test_data=True,
    )

    assert result.diverged is True
    assert result.method_name == "get_all_unit_owners"
    assert "7.owner_name" in result.diverging_fields
    assert result.is_test_data is True
    log_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_log_divergence_writes_structured_columns():
    session = AsyncMock()
    validator = ShadowReadValidator(session=session)
    scheme_ref = MagicMock(tenant_id=uuid.uuid4(), scheme_id=uuid.uuid4())
    result = MagicMock(
        query_type="get_owner_info",
        method_name="get_owner_info",
        query_params={"unit_number": "7"},
        postgres_value={"owner_name": "Postgres Owner"},
        mongodb_value={"owner_name": "Mongo Owner"},
        divergence_summary="Owner divergence",
        diverging_fields=["owner_name"],
        is_test_data=True,
    )

    await validator._log_divergence(scheme_ref, result)

    assert session.execute.await_count == 1
    _, params = session.execute.await_args.args
    assert params["method_name"] == "get_owner_info"
    assert params["diverging_fields"] == ["owner_name"]
    assert params["is_test_data"] is True
