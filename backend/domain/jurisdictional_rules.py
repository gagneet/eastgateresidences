"""
# @featuretrace:jurisdictional-rules — Pure domain rule engine for Australian strata legislation.
# Layer: domain
# Data flow: backend/domain/rules/{jurisdiction}.json → JurisdictionalRuleEngine → callers.
# Related: backend/services/jurisdiction_service.py (DB-aware wrapper)
#           backend/routers/jurisdictional_rules_router.py
#           frontend/src/pages/dashboard/admin/JurisdictionalRulesPage.tsx
# Scope: (global)

Single source of truth for all statutory constraints that vary by jurisdiction.
All financial write paths must consult this engine — never inline `if jurisdiction ==` branches.

Design principles:
  - Synchronous and pure: no DB access, no network calls.
  - Rules loaded from JSON files in backend/domain/rules/ at first call (cached in module-level dict).
  - Methods are typed and named after the statutory concept they encode.
  - Callers receive typed values (int, bool, Decimal, list[str], str | None).
  - Unknown jurisdiction raises ValueError — callers must handle or default.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_RULES_DIR = Path(__file__).parent / "rules"
_CACHE: dict[str, dict[str, Any]] = {}

_VALID_JURISDICTIONS = frozenset({"ACT", "NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT"})


def _load(jurisdiction: str) -> dict[str, Any]:
    """Generated function header.

    Function: _load
    Path: backend/domain/jurisdictional_rules.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    jur = jurisdiction.upper()
    if jur not in _VALID_JURISDICTIONS:
        raise ValueError(f"Unknown jurisdiction: {jurisdiction!r}. Expected one of {sorted(_VALID_JURISDICTIONS)}.")
    if jur not in _CACHE:
        path = _RULES_DIR / f"{jur.lower()}.json"
        try:
            _CACHE[jur] = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ValueError(f"Rule file missing for jurisdiction {jur!r}: {path}")
    return _CACHE[jur]


def _val(rules: dict[str, Any], key: str) -> Any:
    """Extract the .value field from a rule entry."""
    entry = rules.get(key)
    if entry is None:
        return None
    return entry.get("value")


class JurisdictionalRuleEngine:
    """Pure, synchronous rule engine for Australian strata legislation.

    Instantiate once and reuse — all state is in the module-level cache.
    """

    # ── Interest ──────────────────────────────────────────────────────────────

    def max_interest_rate_pa(self, jurisdiction: str) -> Decimal:
        """Annual simple interest ceiling on overdue levies (percent, e.g. Decimal('10'))."""
        rules = _load(jurisdiction)
        v = _val(rules, "max_interest_rate_pa")
        return Decimal(str(v)) if v is not None else Decimal("10")

    def max_interest_rate_pa_by_special_resolution(self, jurisdiction: str) -> Decimal:
        """Annual ceiling when elevated by special resolution."""
        rules = _load(jurisdiction)
        v = _val(rules, "max_interest_rate_pa_by_special_resolution")
        return Decimal(str(v)) if v is not None else self.max_interest_rate_pa(jurisdiction)

    def interest_grace_period_days(self, jurisdiction: str) -> int:
        """Days before interest starts accruing after a levy falls due (0 = no grace)."""
        rules = _load(jurisdiction)
        v = _val(rules, "interest_grace_period_days")
        return int(v) if v is not None else 0

    # ── Fund transfers ────────────────────────────────────────────────────────

    def can_transfer_between_funds(self, jurisdiction: str) -> bool:
        """True if any inter-fund transfer (e.g. admin→sinking) is legally possible."""
        rules = _load(jurisdiction)
        v = _val(rules, "can_transfer_between_funds")
        return bool(v) if v is not None else True

    def inter_fund_transfer_resolution_required(self, jurisdiction: str) -> str:
        """Resolution type needed for an inter-fund transfer.

        Returns one of: "none", "ordinary", "general_meeting", "special", "prohibited".
        """
        rules = _load(jurisdiction)
        v = _val(rules, "inter_fund_transfer_resolution_required")
        return str(v) if v is not None else "ordinary"

    # ── Over-budget controls ──────────────────────────────────────────────────

    def overbudget_threshold_pct(self, jurisdiction: str) -> Decimal | None:
        """Percentage over approved budget line that triggers approval requirement.

        Returns None for jurisdictions with no statutory cap.
        """
        rules = _load(jurisdiction)
        v = _val(rules, "overbudget_threshold_pct")
        return Decimal(str(v)) if v is not None else None

    def overbudget_requires_resolution_above_pct(self, jurisdiction: str) -> Decimal | None:
        """Pct above which a general-meeting resolution is required before spending."""
        rules = _load(jurisdiction)
        v = _val(rules, "overbudget_requires_resolution_above_pct")
        return Decimal(str(v)) if v is not None else None

    # ── Trust and withdrawal authority ────────────────────────────────────────

    def trust_withdrawal_authority_roles(self, jurisdiction: str) -> list[str]:
        """Roles that may authorise a trust withdrawal in this jurisdiction."""
        rules = _load(jurisdiction)
        entry = rules.get("trust_withdrawal_authority_roles", {})
        v = entry.get("value") if isinstance(entry, dict) else None
        return list(v) if v else []

    def pooled_trust_permitted(self, jurisdiction: str) -> bool:
        """True if a pooled trust account arrangement is permitted."""
        rules = _load(jurisdiction)
        v = _val(rules, "pooled_trust_permitted")
        return bool(v) if v is not None else True

    def interest_belongs_to(self, jurisdiction: str) -> str:
        """Who receives interest on pooled trust funds.

        Returns one of: "scheme", "statutory_account", "body_corporate".
        """
        rules = _load(jurisdiction)
        v = _val(rules, "interest_belongs_to")
        return str(v) if v is not None else "scheme"

    # ── Deadlines and reporting ───────────────────────────────────────────────

    def trial_balance_deadline_days(self, jurisdiction: str) -> int:
        """Days after period-end by which trial balance must be prepared."""
        rules = _load(jurisdiction)
        v = _val(rules, "trial_balance_deadline_days")
        return int(v) if v is not None else 30

    def audit_deadline(self, jurisdiction: str) -> str:
        """Human-readable audit deadline, e.g. "30 September", "pre-AGM", "at-AGM"."""
        rules = _load(jurisdiction)
        v = _val(rules, "audit_deadline")
        return str(v) if v else "30 September"

    # ── Committee spending cap ────────────────────────────────────────────────

    def committee_spending_cap_formula(self, jurisdiction: str) -> str | None:
        """Formula string for committee spending cap, or None if no cap.

        Example: "$200 * lot_count" for QLD.
        Callers should evaluate this formula with the scheme's lot count.
        """
        rules = _load(jurisdiction)
        v = _val(rules, "committee_spending_cap_formula")
        return str(v) if v else None

    def committee_spending_cap_per_lot_cents(self, jurisdiction: str) -> int | None:
        """Per-lot cap in integer cents, or None if jurisdiction has no cap."""
        rules = _load(jurisdiction)
        v = _val(rules, "committee_spending_cap_per_lot_cents")
        return int(v) if v is not None else None

    # ── Withholding ───────────────────────────────────────────────────────────

    def min_withholding_pct_no_abn(self, jurisdiction: str) -> Decimal:
        """PAYG withholding rate (%) when supplier has no ABN — ATO rule, same all states."""
        rules = _load(jurisdiction)
        v = _val(rules, "min_withholding_pct_no_abn")
        return Decimal(str(v)) if v is not None else Decimal("47")

    # ── AGM and meetings ──────────────────────────────────────────────────────

    def agm_notice_period_days(self, jurisdiction: str) -> int:
        """Minimum clear days notice required for a general meeting (AGM or EGM)."""
        rules = _load(jurisdiction)
        v = _val(rules, "agm_notice_period_days") or _val(rules, "agm_notice_days")
        return int(v) if v is not None else 7

    def evoting_permitted(self, jurisdiction: str) -> bool:
        """True if electronic voting is permitted for general meetings."""
        rules = _load(jurisdiction)
        v = _val(rules, "evoting_permitted")
        return bool(v) if v is not None else False

    # ── Capital funding / special levy notices ───────────────────────────────

    def levy_notice_period_days(self, jurisdiction: str, *, emergency: bool = False) -> int | None:
        """Minimum days between levy notice issue and due date.

        Capital-funding workflows use this for notice-preview validation. The
        emergency branch applies only when the jurisdiction has a specific urgent
        repairs levy rule; otherwise it falls back to the standard levy notice.
        Returns None where a payment-period rule has not been verified. Callers
        must then require legal-review evidence instead of applying a national
        default.
        """
        rules = _load(jurisdiction)
        key = "capital_funding_emergency_notice_days" if emergency else "capital_funding_standard_notice_days"
        v = _val(rules, key)
        if v is None and emergency:
            v = _val(rules, "capital_funding_standard_notice_days")
        return int(v) if v is not None else None

    def capital_funding_resolution_required(self, jurisdiction: str) -> str:
        """Default resolution gate for capital funding or special levy approval."""
        rules = _load(jurisdiction)
        v = _val(rules, "capital_funding_resolution_required")
        return str(v) if v is not None else "ordinary"

    def capital_funding_allocation_basis(self, jurisdiction: str) -> str:
        """Default allocation basis for capital funding notices."""
        rules = _load(jurisdiction)
        v = _val(rules, "capital_funding_allocation_basis")
        return str(v) if v is not None else "unit_entitlement"

    def capital_funding_benefit_principle_available(self, jurisdiction: str) -> bool:
        """True when jurisdiction guidance/law expressly allows benefit-based special fees."""
        rules = _load(jurisdiction)
        v = _val(rules, "capital_funding_benefit_principle_available")
        return bool(v) if v is not None else False

    def capital_funding_tenant_consultation_required(self, jurisdiction: str) -> bool:
        """True when tenant/occupier notice or consultation may be needed for works impacts."""
        rules = _load(jurisdiction)
        v = _val(rules, "capital_funding_tenant_consultation_required")
        return bool(v) if v is not None else False

    def capital_funding_rules_summary(self, jurisdiction: str) -> dict[str, Any]:
        """Return capital-funding rule entries with citations for UI/docs display."""
        rules = _load(jurisdiction)
        keys = (
            "capital_funding_standard_notice_days",
            "capital_funding_emergency_notice_days",
            "capital_funding_notice_period_verified",
            "capital_funding_resolution_required",
            "capital_funding_special_resolution_trigger",
            "capital_funding_allocation_basis",
            "capital_funding_benefit_principle_available",
            "capital_funding_tenant_consultation_required",
            "capital_funding_hardship_notice_required",
            "capital_funding_hybrid_legal_review_required",
        )
        return {key: rules[key] for key in keys if key in rules}

    # ── Proxy caps ────────────────────────────────────────────────────────────

    def proxy_cap_scheme_pct(self, jurisdiction: str) -> Decimal | None:
        """Max % of lots one person may hold proxies for (None = no pct cap)."""
        rules = _load(jurisdiction)
        v = _val(rules, "proxy_cap_scheme_pct")
        return Decimal(str(v)) if v is not None else None

    def proxy_cap_small_scheme_lots(self, jurisdiction: str) -> int | None:
        """Max proxies per holder in small schemes (None = no cap)."""
        rules = _load(jurisdiction)
        v = _val(rules, "proxy_cap_small_scheme_lots")
        return int(v) if v is not None else None

    def proxy_cap_scheme_size_threshold(self, jurisdiction: str) -> int:
        """Lot count at-or-above which the % proxy cap applies (below = per-lot cap)."""
        rules = _load(jurisdiction)
        v = _val(rules, "proxy_cap_scheme_size_threshold")
        return int(v) if v is not None else 20

    # ── Compliance registers ──────────────────────────────────────────────────

    def building_manager_duties_register_required(self, jurisdiction: str) -> bool:
        """True if a building manager duties register is a statutory obligation."""
        rules = _load(jurisdiction)
        v = _val(rules, "building_manager_duties_register_required")
        return bool(v) if v is not None else False

    def initial_maintenance_schedule_required(self, jurisdiction: str) -> bool:
        """True if an initial maintenance schedule is statutorily required."""
        rules = _load(jurisdiction)
        v = _val(rules, "initial_maintenance_schedule_required")
        return bool(v) if v is not None else False

    def initial_maintenance_schedule_deadline_months(self, jurisdiction: str) -> int | None:
        """Months from plan registration within which the IMS must be prepared. None = no deadline."""
        rules = _load(jurisdiction)
        v = _val(rules, "initial_maintenance_schedule_deadline_months")
        return int(v) if v is not None else None

    # ── Manager reporting ─────────────────────────────────────────────────────

    def manager_report_period_months(self, jurisdiction: str) -> int:
        """How often the managing agent must report to the owners corporation (months)."""
        rules = _load(jurisdiction)
        v = _val(rules, "manager_report_period_months") or _val(rules, "agent_report_frequency_months")
        return int(v) if v is not None else 6

    def manager_report_retention_years(self, jurisdiction: str) -> int:
        """How many years manager reports must be retained."""
        rules = _load(jurisdiction)
        v = _val(rules, "manager_report_retention_years")
        return int(v) if v is not None else 7

    # ── Privacy compliance ────────────────────────────────────────────────────

    def privacy_dsar_response_days(self, jurisdiction: str) -> int:
        """Days within which a Data Subject Access Request must be responded to."""
        rules = _load(jurisdiction)
        v = _val(rules, "privacy_dsar_response_days")
        return int(v) if v is not None else 30

    # ── Full rules dict ───────────────────────────────────────────────────────

    def get_effective_rules(self, jurisdiction: str) -> dict[str, Any]:
        """Return the full rules dict for a jurisdiction (excludes _meta key).

        Used by the admin API endpoint to display all rules with statutory citations.
        """
        rules = _load(jurisdiction)
        return {k: v for k, v in rules.items() if k != "_meta"}

    def get_rule_dict(self, jurisdiction: str) -> dict[str, Any]:
        """Return the full rules dict, same as get_effective_rules.

        Alias kept for JurisdictionService backward compatibility.
        """
        return self.get_effective_rules(jurisdiction)

    def get_meta(self, jurisdiction: str) -> dict[str, Any]:
        """Return the _meta section (primary legislation, trust law, last reviewed)."""
        rules = _load(jurisdiction)
        return rules.get("_meta", {})

    def valid_jurisdictions(self) -> list[str]:
        """Return sorted list of supported jurisdiction codes."""
        return sorted(_VALID_JURISDICTIONS)


# Module-level singleton — import and reuse rather than instantiating per-request.
rule_engine = JurisdictionalRuleEngine()
