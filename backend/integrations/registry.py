"""
backend/integrations/registry.py — Provider dependency-injection helper.

# @featuretrace:financial_integration_v2 — provider selection and DI.
# Layer: service
# Data flow: building settings → ProviderRegistry → correct Protocol impl (scope param: building|global).
# Related: protocols.py (contracts), mocks/ (default impls), server.py (startup).

The ProviderRegistry is a module-level singleton. At startup, every available
provider registers itself. At request time, get_*_provider(building_id) reads
the per-building preference from Postgres-backed cutover settings first and
falls back to the legacy MongoDB settings document only when no Postgres
preference has been configured yet.

Per-building preference is stored in db.settings as:
  {
    "building_id": "<bid>",
    "integration_provider_preference": {
      "bank_feed": "csv_upload_bank_feed",
      "biller": "mock_biller",
      "payment_initiation": "mock_aba_writer",
      "accounting": "mock_accounting_source",
      "ocr": "mock_ocr"
    }
  }

If no preference document exists, all five protocols use their mock provider.
This is the correct behaviour for new buildings and CI.
"""
from __future__ import annotations

import logging
from typing import Optional

from integrations.portal_adapters import CIVIUM_PORTAL, PortalAdapter
from integrations.protocols import (
    AccountingProvider,
    BankFeedProvider,
    BillerProvider,
    OCRProvider,
    PaymentInitiationProvider,
)

logger = logging.getLogger(__name__)

# ── Provider names (canonical keys used in db.settings) ──────────────────────

MOCK_BANK_FEED = "csv_upload_bank_feed"
MOCK_BILLER = "mock_biller"
MOCK_ABA_WRITER = "mock_aba_writer"
MOCK_ACCOUNTING = "mock_accounting_source"
MOCK_OCR = "mock_ocr"
UNLIMITED_OCR = "unlimited_ocr"
# Portal/current-balance intake default. Civium is the back-compat default so an
# unconfigured building (e.g. East Gate) keeps its existing staging behaviour; a building
# selects another provider via db.settings.integration_provider_preference.portal.
DEFAULT_PORTAL = CIVIUM_PORTAL


class ProviderRegistry:
    """Holds all registered provider instances and resolves per-building selection.

    Thread-safe for reads. Registrations happen once at startup.
    """

    def __init__(self) -> None:
        """Generated function header.

        Function: ProviderRegistry.__init__
        Path: backend/integrations/registry.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._bank_feeds: dict[str, BankFeedProvider] = {}
        self._billers: dict[str, BillerProvider] = {}
        self._payment_initiators: dict[str, PaymentInitiationProvider] = {}
        self._accounting: dict[str, AccountingProvider] = {}
        self._ocr: dict[str, OCRProvider] = {}
        self._portal_adapters: dict[str, PortalAdapter] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register_bank_feed(self, provider: BankFeedProvider) -> None:
        """Generated function header.

        Function: ProviderRegistry.register_bank_feed
        Path: backend/integrations/registry.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._bank_feeds[provider.name] = provider
        logger.info("Registered BankFeedProvider: %s", provider.name)

    def register_biller(self, provider: BillerProvider) -> None:
        """Generated function header.

        Function: ProviderRegistry.register_biller
        Path: backend/integrations/registry.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._billers[provider.name] = provider
        logger.info("Registered BillerProvider: %s", provider.name)

    def register_payment_initiator(self, provider: PaymentInitiationProvider) -> None:
        """Generated function header.

        Function: ProviderRegistry.register_payment_initiator
        Path: backend/integrations/registry.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._payment_initiators[provider.name] = provider
        logger.info("Registered PaymentInitiationProvider: %s", provider.name)

    def register_accounting(self, provider: AccountingProvider) -> None:
        """Generated function header.

        Function: ProviderRegistry.register_accounting
        Path: backend/integrations/registry.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._accounting[provider.name] = provider
        logger.info("Registered AccountingProvider: %s", provider.name)

    def register_ocr(self, provider: OCRProvider) -> None:
        """Generated function header.

        Function: ProviderRegistry.register_ocr
        Path: backend/integrations/registry.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._ocr[provider.name] = provider
        logger.info("Registered OCRProvider: %s", provider.name)

    def register_portal_adapter(self, provider: PortalAdapter) -> None:
        """Register a PortalAdapter (portal/current-balance intake) by its .name."""
        self._portal_adapters[provider.name] = provider
        logger.info("Registered PortalAdapter: %s", provider.name)

    # ── Preference lookup (async — reads db.settings) ─────────────────────────

    #: Protocols the ``financial_services_mock`` toggle forces back to their mock
    #: implementation. ``bank_feed`` is deliberately ABSENT: that slot is Demo Bank's,
    #: and Demo Bank is a first-party emulator excluded from this toggle by design (it
    #: has its own gates). Overriding it here would let a building lose its Demo Bank
    #: selection — and its reconstruction staging — as a side effect of a switch that
    #: is supposed to be about live providers.
    _MOCKABLE_PROTOCOLS: dict[str, str] = {
        "biller": MOCK_BILLER,
        "payment_initiation": MOCK_ABA_WRITER,
        "accounting": MOCK_ACCOUNTING,
        "ocr": MOCK_OCR,
    }

    async def _get_preference(self, building_id: str) -> dict[str, str]:
        """Return the integration_provider_preference dict for a building.

        Preference resolution order:
          1. ``financial_services_mock`` — while on, every protocol in
             ``_MOCKABLE_PROTOCOLS`` is pinned to its mock regardless of what the
             building selected, so a stored preference for a live provider cannot
             take effect before the building is deliberately switched live.
          2. Postgres building settings (`banking.provider`)
          3. Legacy Mongo db.settings.integration_provider_preference

        Returns {} if no preference document exists (mocks will be used).
        Never raises — a missing or malformed preference is treated as default.
        """
        pref = await self._resolve_stored_preference(building_id)
        return await self._apply_mock_boundary(building_id, pref)

    async def _apply_mock_boundary(
            self, building_id: str, pref: dict[str, str]
    ) -> dict[str, str]:
        """Pin the mockable protocols to their mocks while the toggle is on."""
        try:
            from services.financial_mock_mode import financial_services_mocked

            if not await financial_services_mocked(building_id):
                return pref
        except Exception as exc:
            # financial_services_mocked already fails safe; this is the belt for the
            # import itself, and it fails the same way — toward mock.
            logger.warning(
                "Could not resolve the financial mock boundary for building %s (%s); "
                "pinning mock providers.", building_id, exc,
            )

        pinned = dict(pref)
        for protocol, mock_name in self._MOCKABLE_PROTOCOLS.items():
            if pinned.get(protocol) not in (None, mock_name):
                logger.info(
                    "financial_services_mock is on for building %s: using %s for %s "
                    "instead of the configured %s",
                    building_id, mock_name, protocol, pinned[protocol],
                )
            pinned[protocol] = mock_name
        return pinned

    async def _resolve_stored_preference(self, building_id: str) -> dict[str, str]:
        """The building's stored preference, before the mock boundary is applied."""
        try:
            from services.cutover_config_service import (
                BANK_INTEGRATION_ABSTRACTION_ENABLED,
                get_bank_provider_preferences,
                is_cutover_feature_enabled,
            )

            if await is_cutover_feature_enabled(building_id, BANK_INTEGRATION_ABSTRACTION_ENABLED):
                pref = await get_bank_provider_preferences(building_id)
                if pref:
                    return pref

            from database import db  # imported lazily to avoid circular import
            doc = await db.settings.find_one(
                {"building_id": building_id},
                {"integration_provider_preference": 1},
            )
            if doc and "integration_provider_preference" in doc:
                pref = doc["integration_provider_preference"]
                if isinstance(pref, dict):
                    return pref
        except Exception as exc:
            logger.warning(
                "Could not load provider preference for building %s: %s",
                building_id,
                exc,
            )
        return {}

    # ── Provider resolution ───────────────────────────────────────────────────

    async def get_bank_feed(self, building_id: str) -> BankFeedProvider:
        """Generated function header.

        Function: ProviderRegistry.get_bank_feed
        Path: backend/integrations/registry.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        pref = await self._get_preference(building_id)
        name = pref.get("bank_feed", MOCK_BANK_FEED)
        provider = self._bank_feeds.get(name) or self._bank_feeds.get(MOCK_BANK_FEED)
        if provider is None:
            raise RuntimeError(
                f"No BankFeedProvider registered (requested={name!r}, "
                f"available={list(self._bank_feeds)})"
            )
        return provider

    async def get_biller(self, building_id: str) -> BillerProvider:
        """Generated function header.

        Function: ProviderRegistry.get_biller
        Path: backend/integrations/registry.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        pref = await self._get_preference(building_id)
        name = pref.get("biller", MOCK_BILLER)
        provider = self._billers.get(name) or self._billers.get(MOCK_BILLER)
        if provider is None:
            raise RuntimeError(
                f"No BillerProvider registered (requested={name!r}, "
                f"available={list(self._billers)})"
            )
        return provider

    async def get_payment_initiator(self, building_id: str) -> PaymentInitiationProvider:
        """Generated function header.

        Function: ProviderRegistry.get_payment_initiator
        Path: backend/integrations/registry.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        pref = await self._get_preference(building_id)
        name = pref.get("payment_initiation", MOCK_ABA_WRITER)
        provider = (
                self._payment_initiators.get(name)
                or self._payment_initiators.get(MOCK_ABA_WRITER)
        )
        if provider is None:
            raise RuntimeError(
                f"No PaymentInitiationProvider registered (requested={name!r}, "
                f"available={list(self._payment_initiators)})"
            )
        return provider

    async def get_accounting(self, building_id: str) -> AccountingProvider:
        """Generated function header.

        Function: ProviderRegistry.get_accounting
        Path: backend/integrations/registry.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        pref = await self._get_preference(building_id)
        name = pref.get("accounting", MOCK_ACCOUNTING)
        provider = self._accounting.get(name) or self._accounting.get(MOCK_ACCOUNTING)
        if provider is None:
            raise RuntimeError(
                f"No AccountingProvider registered (requested={name!r}, "
                f"available={list(self._accounting)})"
            )
        return provider

    async def get_ocr(self, building_id: str) -> OCRProvider:
        """Generated function header.

        Function: ProviderRegistry.get_ocr
        Path: backend/integrations/registry.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        pref = await self._get_preference(building_id)
        name = pref.get("ocr", MOCK_OCR)
        provider = self._ocr.get(name) or self._ocr.get(MOCK_OCR)
        if provider is not None and getattr(provider, "is_available", True) is False:
            logger.warning(
                "OCRProvider %s selected for building %s but unavailable (%s); falling back to %s",
                name,
                building_id,
                getattr(provider, "unavailable_reason", "unavailable"),
                MOCK_OCR,
            )
            provider = self._ocr.get(MOCK_OCR)
        if provider is None:
            raise RuntimeError(
                f"No OCRProvider registered (requested={name!r}, "
                f"available={list(self._ocr)})"
            )
        return provider

    def _ensure_portal_default(self) -> None:
        """Lazily register the in-repo Civium PortalAdapter if none is registered yet.

        `register_mock_providers()` registers it at server startup, but standalone contexts (the
        `strata_web_portal_ingest` CLI, tests) resolve adapters without that startup call — the
        Civium adapter is in-repo and always available, so ensure the default exists on demand.
        """
        if DEFAULT_PORTAL not in self._portal_adapters:
            from integrations.portal_adapters import CiviumPortalAdapter

            self.register_portal_adapter(CiviumPortalAdapter())

    async def get_portal_adapter(self, building_id: str) -> PortalAdapter:
        """Resolve the PortalAdapter for a building (portal/current-balance intake).

        Selection: db.settings.integration_provider_preference.portal, defaulting to Civium
        (DEFAULT_PORTAL) so an unconfigured building keeps its existing behaviour. Falls back to
        the Civium default if the requested adapter isn't registered.
        """
        self._ensure_portal_default()
        pref = await self._get_preference(building_id)
        name = pref.get("portal", DEFAULT_PORTAL)
        provider = self._portal_adapters.get(name) or self._portal_adapters.get(DEFAULT_PORTAL)
        if provider is None:
            raise RuntimeError(
                f"No PortalAdapter registered (requested={name!r}, "
                f"available={list(self._portal_adapters)})"
            )
        return provider

    # ── Introspection ─────────────────────────────────────────────────────────

    def list_registered(self) -> dict[str, list[str]]:
        """Return all registered provider names per protocol — for health checks."""
        return {
            "bank_feed": list(self._bank_feeds),
            "biller": list(self._billers),
            "payment_initiation": list(self._payment_initiators),
            "accounting": list(self._accounting),
            "ocr": list(self._ocr),
            "portal": list(self._portal_adapters),
        }


# Module-level singleton — registered once at startup, read many times per request.
_registry = ProviderRegistry()


def get_provider_registry() -> ProviderRegistry:
    """FastAPI dependency: return the global provider registry.

    Usage in a route:
        registry: ProviderRegistry = Depends(get_provider_registry)
        biller = await registry.get_biller(building_id)
    """
    return _registry


def register_mock_providers() -> None:
    """Register all mock providers into the global registry.

    Called once at server startup (server.py @app.on_event("startup")).
    Mock providers are ALWAYS registered so any building can fall back to them.
    Real providers are registered additionally when their credentials are present.
    """
    try:
        from integrations.mocks.csv_upload_bank_feed import CsvUploadBankFeed

        _registry.register_bank_feed(CsvUploadBankFeed())
    except ImportError as e:
        logger.warning(f"csv_upload_bank_feed provider not available: {e}")

    try:
        from integrations.demo_bank.provider import DemoBankFeed
        from integrations.mocks.mock_biller import MockBiller
        from integrations.mocks.mock_aba_writer import MockAbaWriter
        from integrations.mocks.mock_accounting_source import MockAccountingSource
        from integrations.mocks.mock_ocr import MockOCR

        _registry.register_bank_feed(DemoBankFeed())
        _registry.register_biller(MockBiller())
        _registry.register_payment_initiator(MockAbaWriter())
        _registry.register_accounting(MockAccountingSource())
        _registry.register_ocr(MockOCR())
    except ImportError as e:
        logger.warning(f"Demo Bank mock providers not available: {e}")

    try:
        from services.ocr.unlimited_ocr_client import UnlimitedOCRProvider, get_unlimited_ocr_config

        config = get_unlimited_ocr_config()
        if config.is_available:
            _registry.register_ocr(UnlimitedOCRProvider(config))
        else:
            logger.info("Unlimited OCR provider not registered: %s", config.unavailable_reason)
    except Exception as e:
        logger.warning(f"Unlimited OCR provider not available: {e}")

    # Portal/current-balance intake (GAP-ONBOARD-003). The Civium adapter is in-repo and always
    # registered as the back-compat default; a real per-provider CSV/API adapter (data-backed
    # CsvPortalAdapter or similar) is registered here too once its integration lands (follow-on).
    try:
        from integrations.portal_adapters import CiviumPortalAdapter

        _registry.register_portal_adapter(CiviumPortalAdapter())
    except Exception as e:
        logger.warning(f"Civium portal adapter not available: {e}")

    logger.info("Mock providers registered for all five Protocols (including demo_bank_feed).")
