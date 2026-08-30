"""
tests/backend/integrations/test_registry.py — Unit tests for ProviderRegistry.

Tests:
  - get_provider_registry() returns the singleton ProviderRegistry
  - list_registered() returns the correct dict shape
  - register_mock_providers() populates the DI protocol slots
  - get_bank_feed / get_biller resolve fallback to mock when no DB preference exists
  - get_bank_feed respects an explicit preference from db.settings

All DB calls are mocked with AsyncMock. No running backend needed.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

_TEST_BUILDING = "16244"  # Sierra demo — never "13195"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_db_no_preference():
    """Return a mock db where settings.find_one returns None (no preference stored)."""
    mock_db = MagicMock()
    mock_db.settings.find_one = AsyncMock(return_value=None)
    return mock_db


def _mock_db_with_preference(preference: dict):
    """Return a mock db where settings.find_one returns a preference document."""
    mock_db = MagicMock()
    mock_db.settings.find_one = AsyncMock(
        return_value={"integration_provider_preference": preference}
    )
    return mock_db


# ── Singleton and structure tests ─────────────────────────────────────────────

class TestProviderRegistrySingleton:
    def test_get_provider_registry_returns_registry_instance(self):
        """get_provider_registry() returns a ProviderRegistry instance."""
        from integrations.registry import get_provider_registry, ProviderRegistry
        reg = get_provider_registry()
        assert isinstance(reg, ProviderRegistry)

    def test_get_provider_registry_is_same_object_each_call(self):
        """get_provider_registry() always returns the same singleton object."""
        from integrations.registry import get_provider_registry
        reg_a = get_provider_registry()
        reg_b = get_provider_registry()
        assert reg_a is reg_b

    def test_list_registered_returns_dict_with_six_keys(self):
        """list_registered() returns a dict with exactly six protocol keys.

        "portal" (PortalAdapter / register_portal_adapter()) was added
        alongside the original five DI protocols — see integrations/registry.py.
        """
        from integrations.registry import get_provider_registry
        reg = get_provider_registry()
        result = reg.list_registered()
        assert isinstance(result, dict)
        assert set(result.keys()) == {
            "bank_feed",
            "biller",
            "payment_initiation",
            "accounting",
            "ocr",
            "portal",
        }

    def test_list_registered_values_are_lists(self):
        """Each value in list_registered() is a list of provider name strings."""
        from integrations.registry import get_provider_registry
        reg = get_provider_registry()
        result = reg.list_registered()
        for key, value in result.items():
            assert isinstance(value, list), (
                f"Expected list for key {key!r}, got {type(value).__name__}"
            )


# ── Mock provider registration tests ─────────────────────────────────────────

class TestRegisterMockProviders:
    def test_mock_providers_registered_after_call(self):
        """After register_mock_providers(), all five protocols have at least one provider."""
        from integrations.registry import get_provider_registry, register_mock_providers
        register_mock_providers()
        reg = get_provider_registry()
        listed = reg.list_registered()
        assert len(listed["bank_feed"]) >= 1
        assert len(listed["biller"]) >= 1
        assert len(listed["payment_initiation"]) >= 1
        assert len(listed["accounting"]) >= 1
        assert len(listed["ocr"]) >= 1

    def test_mock_bank_feed_registered_by_name(self):
        """After register_mock_providers(), 'csv_upload_bank_feed' is in bank_feed list."""
        from integrations.registry import get_provider_registry, register_mock_providers, MOCK_BANK_FEED
        register_mock_providers()
        listed = get_provider_registry().list_registered()
        assert MOCK_BANK_FEED in listed["bank_feed"]

    def test_mock_biller_registered_by_name(self):
        """After register_mock_providers(), 'mock_biller' is in biller list."""
        from integrations.registry import get_provider_registry, register_mock_providers, MOCK_BILLER
        register_mock_providers()
        listed = get_provider_registry().list_registered()
        assert MOCK_BILLER in listed["biller"]

    def test_mock_aba_writer_registered_by_name(self):
        """After register_mock_providers(), 'mock_aba_writer' is in payment_initiation list."""
        from integrations.registry import get_provider_registry, register_mock_providers, MOCK_ABA_WRITER
        register_mock_providers()
        listed = get_provider_registry().list_registered()
        assert MOCK_ABA_WRITER in listed["payment_initiation"]

    def test_mock_accounting_registered_by_name(self):
        """After register_mock_providers(), 'mock_accounting_source' is in accounting list."""
        from integrations.registry import get_provider_registry, register_mock_providers, MOCK_ACCOUNTING
        register_mock_providers()
        listed = get_provider_registry().list_registered()
        assert MOCK_ACCOUNTING in listed["accounting"]

    def test_mock_ocr_registered_by_name(self):
        """After register_mock_providers(), 'mock_ocr' is in ocr list."""
        from integrations.registry import get_provider_registry, register_mock_providers, MOCK_OCR
        register_mock_providers()
        listed = get_provider_registry().list_registered()
        assert MOCK_OCR in listed["ocr"]

    def test_register_mock_providers_is_idempotent(self):
        """Calling register_mock_providers() twice does not raise or duplicate entries."""
        from integrations.registry import get_provider_registry, register_mock_providers
        register_mock_providers()
        register_mock_providers()  # second call — should be a no-op (dict overwrites same key)
        listed = get_provider_registry().list_registered()
        # Should still have exactly one 'csv_upload_bank_feed' entry (dict key uniqueness)
        assert listed["bank_feed"].count("csv_upload_bank_feed") == 1


# ── Provider resolution — fallback to mock ────────────────────────────────────

class TestProviderResolutionFallback:
    @pytest.mark.asyncio
    async def test_get_bank_feed_falls_back_to_mock_when_no_preference(self):
        """When db.settings has no preference, get_bank_feed returns the mock provider."""
        from integrations.registry import get_provider_registry, register_mock_providers, MOCK_BANK_FEED
        register_mock_providers()
        reg = get_provider_registry()

        with patch("database.db", _mock_db_no_preference()):
            provider = await reg.get_bank_feed(_TEST_BUILDING)

        assert provider.name == MOCK_BANK_FEED

    @pytest.mark.asyncio
    async def test_get_biller_falls_back_to_mock_when_no_preference(self):
        """When db.settings has no preference, get_biller returns the mock provider."""
        from integrations.registry import get_provider_registry, register_mock_providers, MOCK_BILLER
        register_mock_providers()
        reg = get_provider_registry()

        with patch("database.db", _mock_db_no_preference()):
            provider = await reg.get_biller(_TEST_BUILDING)

        assert provider.name == MOCK_BILLER

    @pytest.mark.asyncio
    async def test_get_payment_initiator_falls_back_to_mock(self):
        """When db.settings has no preference, get_payment_initiator returns the mock."""
        from integrations.registry import get_provider_registry, register_mock_providers, MOCK_ABA_WRITER
        register_mock_providers()
        reg = get_provider_registry()

        with patch("database.db", _mock_db_no_preference()):
            provider = await reg.get_payment_initiator(_TEST_BUILDING)

        assert provider.name == MOCK_ABA_WRITER

    @pytest.mark.asyncio
    async def test_get_accounting_falls_back_to_mock(self):
        """When db.settings has no preference, get_accounting returns the mock."""
        from integrations.registry import get_provider_registry, register_mock_providers, MOCK_ACCOUNTING
        register_mock_providers()
        reg = get_provider_registry()

        with patch("database.db", _mock_db_no_preference()):
            provider = await reg.get_accounting(_TEST_BUILDING)

        assert provider.name == MOCK_ACCOUNTING

    @pytest.mark.asyncio
    async def test_get_ocr_falls_back_to_mock(self):
        """When db.settings has no preference, get_ocr returns the mock."""
        from integrations.registry import get_provider_registry, register_mock_providers, MOCK_OCR
        register_mock_providers()
        reg = get_provider_registry()

        with patch("database.db", _mock_db_no_preference()):
            provider = await reg.get_ocr(_TEST_BUILDING)

        assert provider.name == MOCK_OCR


# ── Provider resolution — explicit preference ─────────────────────────────────

class TestProviderResolutionWithPreference:
    @pytest.mark.asyncio
    async def test_get_bank_feed_uses_preference_if_set(self):
        """get_bank_feed uses the stored preference when it points to a registered provider."""
        from integrations.registry import get_provider_registry, register_mock_providers, MOCK_BANK_FEED
        register_mock_providers()
        reg = get_provider_registry()

        mock_db = _mock_db_with_preference({"bank_feed": MOCK_BANK_FEED})
        with patch("database.db", mock_db):
            provider = await reg.get_bank_feed(_TEST_BUILDING)

        assert provider.name == MOCK_BANK_FEED

    @pytest.mark.asyncio
    async def test_get_biller_uses_preference_if_set(self):
        """get_biller uses the stored preference when it points to a registered provider."""
        from integrations.registry import get_provider_registry, register_mock_providers, MOCK_BILLER
        register_mock_providers()
        reg = get_provider_registry()

        mock_db = _mock_db_with_preference({"biller": MOCK_BILLER})
        with patch("database.db", mock_db):
            provider = await reg.get_biller(_TEST_BUILDING)

        assert provider.name == MOCK_BILLER

    @pytest.mark.asyncio
    async def test_get_bank_feed_falls_back_to_mock_for_unknown_preference(self):
        """When preference names an unregistered provider, fallback to mock."""
        from integrations.registry import get_provider_registry, register_mock_providers, MOCK_BANK_FEED
        register_mock_providers()
        reg = get_provider_registry()

        mock_db = _mock_db_with_preference({"bank_feed": "basiq_real_provider"})
        with patch("database.db", mock_db):
            provider = await reg.get_bank_feed(_TEST_BUILDING)

        # Falls back to mock since "basiq_real_provider" is not registered
        assert provider.name == MOCK_BANK_FEED

    @pytest.mark.asyncio
    async def test_get_bank_feed_db_exception_falls_back_to_mock(self):
        """When db.settings.find_one raises, _get_preference returns {} and mock is used."""
        from integrations.registry import get_provider_registry, register_mock_providers, MOCK_BANK_FEED
        register_mock_providers()
        reg = get_provider_registry()

        mock_db = MagicMock()
        mock_db.settings.find_one = AsyncMock(side_effect=Exception("DB connection error"))

        with patch("database.db", mock_db):
            provider = await reg.get_bank_feed(_TEST_BUILDING)

        assert provider.name == MOCK_BANK_FEED

    @pytest.mark.asyncio
    async def test_get_biller_never_returns_none(self):
        """get_biller must always return a provider — never None."""
        from integrations.registry import get_provider_registry, register_mock_providers
        register_mock_providers()
        reg = get_provider_registry()

        with patch("database.db", _mock_db_no_preference()):
            provider = await reg.get_biller(_TEST_BUILDING)

        assert provider is not None

    @pytest.mark.asyncio
    async def test_get_bank_feed_never_returns_none(self):
        """get_bank_feed must always return a provider — never None."""
        from integrations.registry import get_provider_registry, register_mock_providers
        register_mock_providers()
        reg = get_provider_registry()

        with patch("database.db", _mock_db_no_preference()):
            provider = await reg.get_bank_feed(_TEST_BUILDING)

        assert provider is not None


# ── Preference isolation across buildings ─────────────────────────────────────

class TestProviderResolutionMultiTenant:
    @pytest.mark.asyncio
    async def test_get_bank_feed_queries_correct_building(self):
        """get_bank_feed queries db.settings for the specific building_id supplied."""
        from integrations.registry import get_provider_registry, register_mock_providers
        register_mock_providers()
        reg = get_provider_registry()

        mock_db = MagicMock()
        mock_db.settings.find_one = AsyncMock(return_value=None)

        with patch("database.db", mock_db), \
             patch("services.cutover_config_service.is_cutover_feature_enabled", AsyncMock(return_value=False)):
            await reg.get_bank_feed(_TEST_BUILDING)

        call_args = mock_db.settings.find_one.call_args
        query_filter = call_args[0][0]
        assert query_filter.get("building_id") == _TEST_BUILDING, (
            f"Expected query on building_id='{_TEST_BUILDING}', got {query_filter}"
        )

    @pytest.mark.asyncio
    async def test_get_biller_queries_correct_building(self):
        """get_biller queries db.settings for the specific building_id supplied."""
        from integrations.registry import get_provider_registry, register_mock_providers
        register_mock_providers()
        reg = get_provider_registry()

        mock_db = MagicMock()
        mock_db.settings.find_one = AsyncMock(return_value=None)

        with patch("database.db", mock_db), \
             patch("services.cutover_config_service.is_cutover_feature_enabled", AsyncMock(return_value=False)):
            await reg.get_biller(_TEST_BUILDING)

        call_args = mock_db.settings.find_one.call_args
        query_filter = call_args[0][0]
        assert query_filter.get("building_id") == _TEST_BUILDING


# ── Canonical constant values ─────────────────────────────────────────────────

class TestRegistryConstants:
    def test_mock_bank_feed_constant(self):
        from integrations.registry import MOCK_BANK_FEED
        assert MOCK_BANK_FEED == "csv_upload_bank_feed"

    def test_mock_biller_constant(self):
        from integrations.registry import MOCK_BILLER
        assert MOCK_BILLER == "mock_biller"

    def test_mock_aba_writer_constant(self):
        from integrations.registry import MOCK_ABA_WRITER
        assert MOCK_ABA_WRITER == "mock_aba_writer"

    def test_mock_accounting_constant(self):
        from integrations.registry import MOCK_ACCOUNTING
        assert MOCK_ACCOUNTING == "mock_accounting_source"

    def test_mock_ocr_constant(self):
        from integrations.registry import MOCK_OCR
        assert MOCK_OCR == "mock_ocr"
