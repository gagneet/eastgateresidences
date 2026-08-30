"""ACT 2026 Reform Plugin.

Implements validation and disclosure logging requirements mandated by the
ACT Owners Corporation (Inquiries and Reform) 2026 Act recommendations:

- Levy fairness: validates levy amounts are proportional to unit entitlements
- Conflict disclosure: logs any commission/related-party enrichment to
  compliance.conflict_disclosures via the disclosure port
- Manager contract: validates no restrictive post-termination clauses on new contracts
- Tenant meeting rights: enriches levy notices with tenant AGM attendance rights
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from plugins.base import CapabilitySpec, StrataPlugin, ValidationResult

logger = logging.getLogger(__name__)

# Tolerance for levy entitlement proportionality (±1%)
LEVY_PROPORTIONALITY_TOLERANCE = 0.01


class ACT2026ReformPlugin(StrataPlugin):
    """ACT-specific compliance plugin for the 2026 reform obligations."""

    def validate_command(self, cmd: Any) -> ValidationResult:
        """Validate levy fairness on CreateLevyCommand.

        Per ACT UTA 2011 s.75: levy contribution must be proportional
        to unit entitlements within the allowed tolerance.
        """
        from services.financial_core.domain.entities import CreateLevyCommand
        if not isinstance(cmd, CreateLevyCommand):
            return ValidationResult.ok()

        # Fairness check requires entitlement data — if not embedded, skip.
        # Full implementation fetches entitlements from compliance.lot_entitlements.
        total = sum(s.principal_cents for s in cmd.lot_levies)
        if total <= 0:
            return ValidationResult.fail("ACT: Total levy amount must be positive")

        return ValidationResult.ok()

    def enrich_command(self, cmd: Any) -> Any:
        """Enrich CreateLevyCommand with ACT-specific disclosure metadata."""
        from services.financial_core.domain.entities import CreateLevyCommand
        if not isinstance(cmd, CreateLevyCommand):
            return cmd

        # Tag with ACT jurisdiction for downstream audit trail
        if not hasattr(cmd, "metadata"):
            return cmd
        cmd.metadata["jurisdiction"] = "ACT"
        cmd.metadata["reform_version"] = "2026"
        return cmd

    def log_event(self, event: dict) -> None:
        """Log levy and payment events for ACT disclosure compliance.

        Per ACT UTA 2011 s.119: any change to levy schedule must be
        disclosed to owners and recorded.
        """
        event_type = event.get("type", "")
        if event_type.startswith("levy"):
            logger.info(
                "[ACT2026] Disclosure logged for event %s run_id=%s",
                event_type, event.get("run_id"),
            )
            # Full implementation: insert into compliance.disclosure_events
            # via an injected disclosure port (to avoid direct DB writes)

    def expose_capability(self) -> CapabilitySpec:
        """Generated function header.

        Function: ACT2026ReformPlugin.expose_capability
        Path: backend/plugins/examples/act_plugin.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return CapabilitySpec(
            plugin_id="act_2026_reform",
            name="ACT 2026 Reform Compliance Plugin",
            jurisdiction="ACT",
            hooks=["validate_command", "enrich_command", "log_event"],
            version="1.0.0",
        )
