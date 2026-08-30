"""
backend/integrations — Provider-agnostic financial integration layer.

# @featuretrace:financial_integration_v2 — integration package root.
# Layer: domain
# Data flow: provider adapters → registry/protocols/envelopes → integration inbox + finance workflows (building-scoped).
# Related: backend/integrations/registry.py
#          backend/integrations/protocols.py
#          backend/integrations/envelopes.py

Public API:
    get_provider_registry() — FastAPI Depends helper for the global registry.
    register_mock_providers() — called once at startup to wire the defaults.
"""
from integrations.registry import get_provider_registry, register_mock_providers

__all__ = ["get_provider_registry", "register_mock_providers"]
