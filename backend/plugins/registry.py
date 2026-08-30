"""PluginRegistry — manages plugin registration and hook dispatching.

Usage:
    registry = PluginRegistry()
    registry.register(ACTPlugin())
    registry.register(NSWPlugin())

    # In FinancialCoreService:
    await registry.run_hook("validate_command", cmd)
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Optional

from plugins.base import StrataPlugin, ValidationResult

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Registry and dispatcher for StrataPlugin hooks.

    Dispatches hooks in registration order. Blocking hooks (validate_command)
    fail-fast on the first ValidationResult.is_valid=False.
    """

    def __init__(self) -> None:
        """Generated function header.

        Function: PluginRegistry.__init__
        Path: backend/plugins/registry.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._plugins: list[StrataPlugin] = []

    def register(self, plugin: StrataPlugin) -> None:
        """Generated function header.

        Function: PluginRegistry.register
        Path: backend/plugins/registry.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        cap = plugin.expose_capability()
        logger.info(
            "Registered plugin: %s v%s (jurisdiction=%s hooks=%s)",
            cap.name, cap.version, cap.jurisdiction, cap.hooks,
        )
        self._plugins.append(plugin)

    def unregister(self, plugin_id: str) -> None:
        """Generated function header.

        Function: PluginRegistry.unregister
        Path: backend/plugins/registry.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._plugins = [
            p for p in self._plugins
            if p.expose_capability().plugin_id != plugin_id
        ]

    async def run_hook(self, hook_name: str, payload: Any) -> Any:
        """Run all plugins for the given hook name.

        For validate_command: fail-fast on first error.
        For enrich_command / enrich_template: chain (each plugin gets the previous output).
        For log_event / generate_artefact: run all, exceptions are swallowed.
        Returns the (possibly enriched) payload.
        """
        current = payload

        for plugin in self._plugins:
            method = getattr(plugin, hook_name, None)
            if method is None:
                continue
            try:
                result = method(current) if not inspect.iscoroutinefunction(method) else await method(current)

                if hook_name == "validate_command":
                    if isinstance(result, ValidationResult) and not result.is_valid:
                        raise ValueError(
                            f"Plugin {plugin.expose_capability().name} rejected command: "
                            + "; ".join(result.errors)
                        )

                elif hook_name in ("enrich_command", "enrich_template"):
                    if result is not None:
                        current = result

            except ValueError:
                raise
            except Exception as exc:
                logger.warning(
                    "Plugin %s hook %s raised unexpected error: %s",
                    plugin.expose_capability().plugin_id, hook_name, exc,
                )

        return current

    @property
    def registered_plugins(self) -> list[StrataPlugin]:
        """Generated function header.

        Function: PluginRegistry.registered_plugins
        Path: backend/plugins/registry.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return list(self._plugins)


# Global registry singleton — populated at app startup
_global_registry: Optional[PluginRegistry] = None


def get_registry() -> PluginRegistry:
    """Return the global plugin registry (created on first call)."""
    global _global_registry
    if _global_registry is None:
        _global_registry = PluginRegistry()
    return _global_registry
