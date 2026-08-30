"""
tests/backend/integrations/test_abn_validator.py

Tests for integrations/abn_validator.py — ABN checksum, cache, ABR lookup,
withholding threshold, and graceful offline degradation.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from integrations.abn_validator import (
    ABNValidationResult,
    _check_abn_format,
    _normalise_abn,
    _parse_abr_response,
    validate_abn,
)


# ── Checksum tests ────────────────────────────────────────────────────────────

class TestAbnChecksum:
    def test_valid_abn_passes(self):
        # 51 824 753 556 — ABN Tools reference ABN
        assert _check_abn_format("51824753556") is True

    def test_valid_abn_ato_example(self):
        # 53 004 085 616 — ATO published example
        assert _check_abn_format("53004085616") is True

    def test_invalid_check_digit(self):
        assert _check_abn_format("51824753557") is False  # last digit off by 1

    def test_too_short(self):
        assert _check_abn_format("5182475355") is False  # 10 digits

    def test_too_long(self):
        assert _check_abn_format("518247535561") is False  # 12 digits

    def test_all_zeros(self):
        assert _check_abn_format("00000000000") is False

    def test_non_digit_chars_rejected(self):
        assert _check_abn_format("5182475355A") is False


class TestNormaliseAbn:
    def test_strips_spaces(self):
        assert _normalise_abn("51 824 753 556") == "51824753556"

    def test_strips_hyphens(self):
        assert _normalise_abn("51-824-753-556") == "51824753556"

    def test_empty_string(self):
        assert _normalise_abn("") == ""

    def test_none_returns_empty(self):
        assert _normalise_abn(None) == ""  # type: ignore[arg-type]


# ── validate_abn() integration ────────────────────────────────────────────────

class TestValidateAbnInvalidFormat:
    @pytest.mark.asyncio
    async def test_invalid_format_no_db(self):
        result = await validate_abn("12345", supply_amount_ex_gst_cents=0)
        assert result.is_valid is False
        assert result.abn_status == "InvalidFormat"
        assert result.cached is False
        assert result.withholding_required is False

    @pytest.mark.asyncio
    async def test_invalid_format_triggers_withholding_above_threshold(self):
        result = await validate_abn("12345", supply_amount_ex_gst_cents=10_000)
        assert result.withholding_required is True

    @pytest.mark.asyncio
    async def test_invalid_format_no_withholding_below_threshold(self):
        result = await validate_abn("12345", supply_amount_ex_gst_cents=5_000)
        assert result.withholding_required is False


class TestValidateAbnCache:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_result(self):
        mock_db = MagicMock()
        mock_db.abn_validation_cache.find_one = AsyncMock(return_value={
            "abn": "51824753556",
            "is_valid": True,
            "entity_name": "Acme Pty Ltd",
            "gst_registered": True,
            "abn_status": "Active",
        })
        result = await validate_abn("51824753556", db=mock_db)
        assert result.is_valid is True
        assert result.cached is True
        assert result.entity_name == "Acme Pty Ltd"
        assert result.abn_status == "Active"

    @pytest.mark.asyncio
    async def test_cache_hit_invalid_triggers_withholding(self):
        mock_db = MagicMock()
        mock_db.abn_validation_cache.find_one = AsyncMock(return_value={
            "abn": "51824753556",
            "is_valid": False,
            "entity_name": None,
            "gst_registered": False,
            "abn_status": "Cancelled",
        })
        result = await validate_abn("51824753556", supply_amount_ex_gst_cents=10_000, db=mock_db)
        assert result.withholding_required is True

    @pytest.mark.asyncio
    async def test_cache_miss_falls_through_to_abr(self):
        mock_db = MagicMock()
        mock_db.abn_validation_cache.find_one = AsyncMock(return_value=None)
        mock_db.abn_validation_cache.update_one = AsyncMock()

        with patch("integrations.abn_validator._fetch_abr", new=AsyncMock(return_value={
            "is_valid": True,
            "entity_name": "Test Corp",
            "gst_registered": True,
            "abn_status": "Active",
        })):
            result = await validate_abn("51824753556", db=mock_db)

        assert result.is_valid is True
        assert result.cached is False
        # Verify result was cached
        mock_db.abn_validation_cache.update_one.assert_called_once()


class TestValidateAbnOffline:
    @pytest.mark.asyncio
    async def test_offline_no_guid_returns_graceful_result(self):
        mock_db = MagicMock()
        mock_db.abn_validation_cache.find_one = AsyncMock(return_value=None)
        mock_db.abn_validation_cache.update_one = AsyncMock()

        with patch.dict("os.environ", {}, clear=True):
            # Remove ABR_API_GUID if present
            import os
            os.environ.pop("ABR_API_GUID", None)
            result = await validate_abn("51824753556", db=mock_db)

        assert result.abn_status == "Offline"
        assert result.is_valid is True  # offline = pass-through


# ── ABR response parsing ──────────────────────────────────────────────────────

class TestParseAbrResponse:
    def test_active_gst_registered(self):
        data = {
            "AbnStatus": "Active",
            "EntityName": "Acme Pty Ltd",
            "Gst": "2012-01-01",
        }
        result = _parse_abr_response(data)
        assert result["is_valid"] is True
        assert result["entity_name"] == "Acme Pty Ltd"
        assert result["gst_registered"] is True
        assert result["abn_status"] == "Active"

    def test_cancelled_abn(self):
        data = {"AbnStatus": "Cancelled", "EntityName": "Old Corp", "Gst": ""}
        result = _parse_abr_response(data)
        assert result["is_valid"] is False
        assert result["gst_registered"] is False

    def test_empty_response(self):
        result = _parse_abr_response({})
        assert result["is_valid"] is False
        assert result["abn_status"] == "NotFound"
        assert result["entity_name"] is None

    def test_organisation_name_fallback(self):
        data = {
            "AbnStatus": "Active",
            "MainName": {"OrganisationName": "Widget Co"},
            "Gst": "",
        }
        result = _parse_abr_response(data)
        assert result["entity_name"] == "Widget Co"


# ── Withholding threshold ─────────────────────────────────────────────────────

class TestWithholdingThreshold:
    @pytest.mark.asyncio
    async def test_exactly_at_threshold_no_withholding(self):
        # Exactly $75 ex-GST = 7500 cents — NOT above threshold
        result = await validate_abn("INVALID", supply_amount_ex_gst_cents=7_500)
        assert result.withholding_required is False

    @pytest.mark.asyncio
    async def test_one_cent_above_threshold_triggers_withholding(self):
        result = await validate_abn("INVALID", supply_amount_ex_gst_cents=7_501)
        assert result.withholding_required is True

    @pytest.mark.asyncio
    async def test_zero_supply_no_withholding(self):
        result = await validate_abn("INVALID", supply_amount_ex_gst_cents=0)
        assert result.withholding_required is False
