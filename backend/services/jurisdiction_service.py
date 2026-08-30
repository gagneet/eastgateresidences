"""
Jurisdiction Service — DB-aware resolution of per-building jurisdictional rules.

All statutory rule lookups are delegated to JurisdictionalRuleEngine (pure domain layer).
This service adds only two things on top:
  1. Resolving which jurisdiction code applies to a building (via DB + fallback chain).
  2. Applying per-building rule overrides stored in the jurisdiction_config collection.

Callers that only need statutory values should use JurisdictionalRuleEngine directly.
Callers that need building-specific effective values (with per-building overrides applied)
should use this service.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from domain.jurisdictional_rules import rule_engine

logger = logging.getLogger(__name__)


class JurisdictionService:
    """Resolves effective jurisdiction rules for a building.

    Lookup order for base rules:
      1. Per-building override stored in jurisdiction_config collection
      2. State-level default from JurisdictionalRuleEngine (loaded from JSON)

    Lookup order for jurisdiction code:
      1. jurisdiction_config.state
      2. buildings.jurisdiction or buildings.state
      3. Default: "ACT"
    """

    def __init__(self, db):
        """Generated function header.

        Function: JurisdictionService.__init__
        Path: backend/services/jurisdiction_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.db = db

    # ── Jurisdiction resolution ───────────────────────────────────────────────

    async def get_building_state(self, building_id: str) -> str:
        """Return the state code for a building (e.g. 'ACT', 'NSW')."""
        cfg = await self.db.jurisdiction_config.find_one({"building_id": building_id})
        if cfg and cfg.get("state"):
            return cfg["state"].upper()
        try:
            import bson
            oid = bson.ObjectId(building_id)
            building = await self.db.buildings.find_one({"_id": oid})
        except Exception:
            building = await self.db.buildings.find_one({
                "$or": [{"_id": building_id}, {"building_id": building_id}, {"slug": building_id}]
            })
        if building:
            state = building.get("jurisdiction") or building.get("state")
            if state:
                return state.upper()
        return "ACT"

    # ── Rule access ───────────────────────────────────────────────────────────

    async def get_rule(
            self,
            building_id: str,
            rule_key: str,
            state: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return the effective rule dict (value, unit, legislation, notes) for a building.

        Checks per-building overrides first; falls back to engine's JSON data.
        """
        if not state:
            state = await self.get_building_state(building_id)
        state = (state or "ACT").upper()

        cfg = await self.db.jurisdiction_config.find_one({"building_id": building_id})
        if cfg:
            overrides = cfg.get("rule_overrides", {})
            if rule_key in overrides:
                return overrides[rule_key]

        base_rules = rule_engine.get_rule_dict(state)
        return base_rules.get(rule_key)

    async def get_rule_value(
            self,
            building_id: str,
            rule_key: str,
            default: Any = None,
            state: Optional[str] = None,
    ) -> Any:
        """Convenience: return just the rule's .value field."""
        rule = await self.get_rule(building_id, rule_key, state=state)
        if rule is None:
            return default
        return rule.get("value", default)

    async def get_all_rules(
            self,
            building_id: str,
            state: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return all effective rules for a building, with per-building overrides applied."""
        if not state:
            state = await self.get_building_state(building_id)
        state = (state or "ACT").upper()

        rules = dict(rule_engine.get_rule_dict(state))

        cfg = await self.db.jurisdiction_config.find_one({"building_id": building_id})
        if cfg:
            rules.update(cfg.get("rule_overrides", {}))

        return rules

    async def get_state_and_all_rules(
            self,
            building_id: str,
    ) -> tuple[str, Dict[str, Any]]:
        """Fetch jurisdiction_config once; return (state, effective_rules).

        Avoids the double find_one incurred when callers invoke get_building_state()
        then get_all_rules() separately. Use this in any code path that needs both.
        """
        cfg = await self.db.jurisdiction_config.find_one({"building_id": building_id})
        if cfg and cfg.get("state"):
            state = cfg["state"].upper()
        else:
            state = await self._state_from_buildings(building_id)

        rules = dict(rule_engine.get_rule_dict(state))
        if cfg:
            rules.update(cfg.get("rule_overrides", {}))
        return state, rules

    async def _state_from_buildings(self, building_id: str) -> str:
        """Resolve state from the buildings collection (fallback when no jurisdiction_config)."""
        try:
            import bson
            oid = bson.ObjectId(building_id)
            building = await self.db.buildings.find_one({"_id": oid})
        except Exception:
            building = await self.db.buildings.find_one(
                {"$or": [{"_id": building_id}, {"building_id": building_id}, {"slug": building_id}]}
            )
        if building:
            state = building.get("jurisdiction") or building.get("state")
            if state:
                return state.upper()
        return "ACT"

    # ── Domain-method convenience wrappers ────────────────────────────────────
    # These let callers use the typed engine methods without needing to resolve
    # the jurisdiction code themselves.

    async def max_interest_rate_pa(self, building_id: str) -> Any:
        """Generated function header.

        Function: JurisdictionService.max_interest_rate_pa
        Path: backend/services/jurisdiction_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        state = await self.get_building_state(building_id)
        return rule_engine.max_interest_rate_pa(state)

    async def interest_grace_period_days(self, building_id: str) -> int:
        """Generated function header.

        Function: JurisdictionService.interest_grace_period_days
        Path: backend/services/jurisdiction_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        state = await self.get_building_state(building_id)
        return rule_engine.interest_grace_period_days(state)

    async def can_transfer_between_funds(self, building_id: str) -> bool:
        """Generated function header.

        Function: JurisdictionService.can_transfer_between_funds
        Path: backend/services/jurisdiction_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        state = await self.get_building_state(building_id)
        return rule_engine.can_transfer_between_funds(state)

    async def overbudget_threshold_pct(self, building_id: str) -> Any:
        """Generated function header.

        Function: JurisdictionService.overbudget_threshold_pct
        Path: backend/services/jurisdiction_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        state = await self.get_building_state(building_id)
        return rule_engine.overbudget_threshold_pct(state)

    async def committee_spending_cap_per_lot_cents(self, building_id: str) -> Any:
        """Generated function header.

        Function: JurisdictionService.committee_spending_cap_per_lot_cents
        Path: backend/services/jurisdiction_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        state = await self.get_building_state(building_id)
        return rule_engine.committee_spending_cap_per_lot_cents(state)

    # ── Higher-level computed checks ──────────────────────────────────────────

    async def check_audit_required(
            self, building_id: str, annual_budget: float, lot_count: int
    ) -> Dict[str, Any]:
        """Determine if an annual audit is legally required for a building."""
        state = await self.get_building_state(building_id)
        budget_threshold = await self.get_rule_value(
            building_id, "audit_threshold_budget_aud", default=250_000, state=state,
        )
        lot_threshold = await self.get_rule_value(
            building_id, "audit_threshold_lots", default=100, state=state,
        )

        budget_trigger = annual_budget > budget_threshold
        lot_trigger = lot_count > lot_threshold
        required = budget_trigger or lot_trigger

        base_rules = rule_engine.get_rule_dict(state)
        legislation = (
            base_rules.get("audit_threshold_budget_aud", {}).get("legislation", "")
        )

        return {
            "required": required,
            "reason": (
                f"Budget ${annual_budget:,.0f} > ${budget_threshold:,.0f} threshold"
                if budget_trigger
                else f"Lot count {lot_count} > {lot_threshold} threshold"
                if lot_trigger
                else "Below threshold — audit not legally required"
            ),
            "legislation": legislation,
            "state": state,
        }

    async def calculate_levy_interest(
            self,
            building_id: str,
            principal: float,
            days_overdue: int,
            custom_rate_percent: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Calculate simple interest on an overdue levy.

        Uses the building's configured rate or jurisdiction default (via engine).
        Rate is per annum, simple interest.
        """
        state = await self.get_building_state(building_id)
        default_rate = float(rule_engine.max_interest_rate_pa(state))
        max_rate = float(rule_engine.max_interest_rate_pa_by_special_resolution(state))

        rate = custom_rate_percent if custom_rate_percent is not None else default_rate
        rate = max(0.0, min(rate, max_rate))

        interest = principal * (rate / 100) * (days_overdue / 365)
        interest = round(interest, 2)

        base_rules = rule_engine.get_rule_dict(state)
        legislation = (
            base_rules.get("max_interest_rate_pa", {}).get("legislation", "")
        )

        return {
            "principal": principal,
            "days_overdue": days_overdue,
            "rate_percent_pa": rate,
            "interest_amount": interest,
            "total_owing": round(principal + interest, 2),
            "state": state,
            "legislation": legislation,
        }
