"""Base classes for the StrataOS plugin system (ADR-008).

All plugins must inherit from StrataPlugin and implement the 6-hook contract.
Hooks that are not relevant to a plugin should return the default (pass-through) value.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ValidationResult:
    """Result of a validate_command hook."""
    is_valid: bool
    errors: list[str] = field(default_factory=list)

    @classmethod
    def ok(cls) -> "ValidationResult":
        """Generated function header.

        Function: ValidationResult.ok
        Path: backend/plugins/base.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return cls(is_valid=True)

    @classmethod
    def fail(cls, *errors: str) -> "ValidationResult":
        """Generated function header.

        Function: ValidationResult.fail
        Path: backend/plugins/base.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return cls(is_valid=False, errors=list(errors))


@dataclass
class CapabilitySpec:
    """Metadata describing what a plugin provides."""
    plugin_id: str
    name: str
    jurisdiction: Optional[str] = None
    hooks: list[str] = field(default_factory=list)
    version: str = "0.0.0"


class StrataPlugin(abc.ABC):
    """Abstract base class for all StrataOS plugins.

    Subclasses must implement expose_capability().
    All other hooks have safe default implementations (pass-through / no-op).

    SECURITY CONTRACT:
    - Hooks receive immutable value objects (commands, events)
    - Hooks must NOT import or reference SQLAlchemy models
    - Hooks must NOT call motor/asyncpg directly
    - Side effects are limited to: return enriched command, return artefact, call log_event
    """

    # ----------------------------------------------------------------- hooks

    def validate_command(self, cmd: Any) -> ValidationResult:
        """Hook 1 — validate a command before the core processes it.

        Return ValidationResult.fail(...) to reject the command.
        Default: accept all commands.
        """
        return ValidationResult.ok()

    def enrich_command(self, cmd: Any) -> Any:
        """Hook 2 — enrich or transform a command before processing.

        Return a modified copy of the command, or the original unchanged.
        The enriched value replaces the command for all subsequent hooks.
        """
        return cmd

    def enrich_template(self, tmpl: Any) -> Any:
        """Hook 3 — enrich a document/notice template.

        Used for levy notices, arrears letters, AGM agendas, etc.
        Return a modified copy of the template.
        """
        return tmpl

    def log_event(self, event: dict) -> None:
        """Hook 4 — observe a financial event (read-only).

        Called AFTER the core has completed its writes. Plugins may:
        - Write to compliance.disclosure_events (via an injected port)
        - Emit external notifications
        Must NOT raise; exceptions are caught and logged by the registry.
        """

    def generate_artefact(self, ctx: dict) -> Optional[Any]:
        """Hook 5 — generate a compliance artefact (e.g. s119 disclosure PDF).

        Return an Artefact object or None if not applicable.
        """
        return None

    @abc.abstractmethod
    def expose_capability(self) -> CapabilitySpec:
        """Hook 6 — declare this plugin's identity and capabilities.

        Must be implemented by all plugins.
        """
        ...
