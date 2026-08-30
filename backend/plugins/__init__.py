"""Plugin system for StrataOS financial core.

Plugins extend jurisdictional behaviour without modifying the core.
They implement the 6-hook contract defined in ADR-008.

RULES (enforced by PluginRegistry):
- Plugins CANNOT write to the ledger or any finance.* table
- Plugins CANNOT bypass financial_core service
- Plugins CAN: validate, enrich, log, generate artefacts
"""
from plugins.base import StrataPlugin, ValidationResult, CapabilitySpec
from plugins.registry import PluginRegistry

__all__ = ["StrataPlugin", "ValidationResult", "CapabilitySpec", "PluginRegistry"]
