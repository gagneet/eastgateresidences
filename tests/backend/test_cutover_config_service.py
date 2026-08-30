from unittest.mock import AsyncMock, patch

import pytest

from services import cutover_config_service as cutover


@pytest.mark.asyncio
async def test_child_cutover_toggle_requires_umbrella():
    with patch(
            "services.cutover_config_service.config_repo.resolve_feature_toggle",
            new=AsyncMock(side_effect=[False]),
    ):
        enabled = await cutover.is_cutover_feature_enabled(
            "13195",
            cutover.FINANCIAL_PG_READS_ENABLED,
        )

    assert enabled is False


@pytest.mark.asyncio
async def test_legacy_toggle_alias_maps_to_canonical_write_toggle():
    with patch(
            "services.cutover_config_service.config_repo.resolve_feature_toggle",
            new=AsyncMock(side_effect=[True, True]),
    ) as mock_resolve:
        enabled = await cutover.is_cutover_feature_enabled(
            "13195",
            "financial_core.read_from_postgres",
        )

    assert enabled is True
    requested_keys = [call.args[1] for call in mock_resolve.await_args_list]
    assert requested_keys == [
        cutover.UMBRELLA_FEATURE_KEY,
        cutover.FINANCIAL_PG_WRITES_ENABLED,
    ]


@pytest.mark.asyncio
async def test_get_bank_provider_preferences_maps_csv_replay_to_mock_protocols():
    with patch(
            "services.cutover_config_service.config_repo.get_building_setting",
            new=AsyncMock(side_effect=["csv_replay", "sandbox"]),
    ):
        prefs = await cutover.get_bank_provider_preferences("13195")
        mode = await cutover.get_banking_mode("13195")

    assert prefs == {
        "bank_feed": "csv_upload_bank_feed",
        "payment_initiation": "mock_aba_writer",
    }
    assert mode == "sandbox"
