"""
Statutory interest computation on arrears — B2-02.

@featuretrace:act_statutory_interest — Jurisdiction-aware statutory interest on levy arrears.
# Layer: service
Data flow: finance.py arrears endpoint → compute_accrued_interest() + get_effective_interest_rate() → committee_resolutions (special resolution override) + buildings.jurisdiction + domain.jurisdictional_rules (building-scoped).
Related: backend/routers/finance.py (GET /arrears/detail, GET /arrears/interest-rates,
                                     POST /arrears/special-resolution-rate)
         backend/routers/work_orders.py (POST committee_resolutions)
         backend/domain/jurisdictional_rules.py (per-jurisdiction rate ceilings)
# Scope: (building-scoped)

UTMA 2011 (ACT) § 94 — default 10 % p.a. on outstanding levy debts; owners corporations
may pass a special resolution to raise the rate up to 20 % p.a.

Jurisdiction-aware: NSW (SSMA 2015 s.85) and VIC (OCA 2006 s.29) both cap at 10% p.a. with
NO special-resolution increase permitted; QLD (BCCM reg 157) caps at an annual-equivalent
30% (2.5%/month). Rate floors/ceilings are resolved per building via
domain.jurisdictional_rules.rule_engine — see get_effective_interest_rate() and
record_special_resolution_rate(). ACT remains the fallback default when jurisdiction is
unresolvable.

All monetary amounts are in the same unit as the rest of the codebase (float AUD).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from domain.jurisdictional_rules import rule_engine

logger = logging.getLogger(__name__)

DEFAULT_INTEREST_RATE_PCT: float = 10.0  # 10 % p.a. — ACT/UTMA 2011 default, used as fallback
MAX_INTEREST_RATE_PCT: float = 20.0  # 20 % p.a. — ACT ceiling, used as fallback only


def _resolve_jurisdiction(building_config: Optional[dict]) -> str:
    """Return the jurisdiction code for a building, defaulting to ACT.

    Mirrors the fallback chain used by services/jurisdiction_service.py
    (jurisdiction field, then legacy state field, then ACT default).

    Validates the resolved code against rule_engine.valid_jurisdictions() and
    falls back to ACT if it isn't recognised — buildings.jurisdiction is not
    universally validated at write time across every onboarding path (legacy
    migrations, portfolio imports), so a malformed or unexpected value here
    must never raise; rule_engine.max_interest_rate_pa() etc. raise ValueError
    for anything outside {ACT, NSW, VIC, QLD}, which would otherwise surface
    as an unhandled 500 on the arrears endpoints for that building.
    """
    cfg = building_config or {}
    resolved = (cfg.get("jurisdiction") or cfg.get("state") or "ACT").upper()
    if resolved not in rule_engine.valid_jurisdictions():
        logger.warning(
            "Unrecognised building jurisdiction %r — falling back to ACT for arrears interest",
            resolved,
        )
        return "ACT"
    return resolved


def _statute_citation(jurisdiction: str) -> str:
    """Return the primary-legislation citation for a jurisdiction's arrears-interest
    rule, sourced from the same rule file rather than a separately maintained map."""
    try:
        rules = rule_engine.get_rule_dict(jurisdiction)
        legislation = (rules.get("max_interest_rate_pa") or {}).get("legislation")
        if legislation:
            return legislation
    except ValueError:
        pass
    return "UTMA 2011 s.94"


def compute_accrued_interest(
        principal: float,
        overdue_since: datetime,
        as_at: datetime,
        annual_rate_pct: float = DEFAULT_INTEREST_RATE_PCT,
        max_rate_pct: float = MAX_INTEREST_RATE_PCT,
) -> float:
    """
    Return the accrued statutory interest on *principal* (float AUD, 2 d.p.).

    Interest accrues from the day *after* overdue_since.
    Returns 0.0 if as_at <= overdue_since or principal <= 0.

    Raises ValueError if annual_rate_pct exceeds max_rate_pct. Callers with a
    jurisdiction-resolved rate should pass the matching jurisdiction ceiling
    (e.g. get_effective_interest_rate()'s max_rate_pct return value) rather
    than relying on the ACT-only default — QLD's correct ceiling is 30%, not 20%.

    Formula:
        interest = principal × (rate / 100 / 365) × days_overdue

    Example (10 %, 30 days, $100):
        100 × 0.10 / 365 × 30 = $0.82
    """
    if principal <= 0:
        return 0.0
    if annual_rate_pct > max_rate_pct:
        raise ValueError(
            f"Interest rate {annual_rate_pct}% exceeds legal maximum {max_rate_pct}%"
        )
    if annual_rate_pct <= 0:
        return 0.0

    # Strip timezone so callers can pass either naive or aware datetimes without
    # hitting "can't subtract offset-naive and offset-aware datetimes" TypeError.
    as_at_naive = as_at.replace(tzinfo=None)
    overdue_since_naive = overdue_since.replace(tzinfo=None)

    if as_at_naive <= overdue_since_naive:
        return 0.0

    days_overdue = (as_at_naive - overdue_since_naive).days
    if days_overdue <= 0:
        return 0.0

    daily_rate = Decimal(str(annual_rate_pct)) / Decimal("100") / Decimal("365")
    interest = Decimal(str(principal)) * daily_rate * Decimal(str(days_overdue))
    return float(interest.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def get_building_interest_rate(building_config: Optional[dict]) -> float:
    """
    Return the applicable interest rate for a building.

    Reads ``arrears_interest_rate_pct`` from *building_config* (the record from
    the ``buildings`` collection).  Falls back to the ACT default of 10 % if the
    field is absent or the config is None.  Silently caps at MAX_INTEREST_RATE_PCT.
    """
    if not building_config:
        return DEFAULT_INTEREST_RATE_PCT
    rate = building_config.get("arrears_interest_rate_pct", DEFAULT_INTEREST_RATE_PCT)
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return DEFAULT_INTEREST_RATE_PCT
    return min(rate, MAX_INTEREST_RATE_PCT)


async def get_effective_interest_rate(building_id: str, db: Any) -> dict:
    """Return the effective levy-arrears interest rate and its source.

    Resolution order (first match wins):
    1. Active ``levy_interest_rate_override`` special resolution in
       ``committee_resolutions`` — capped at the jurisdiction's special-resolution
       ceiling (e.g. 20% p.a. for ACT under UTMA 2011 s.94; 10% for NSW/VIC, where
       no increase is legally permitted; 30% annual-equivalent for QLD).
    2. ``buildings.arrears_interest_rate_pct`` config field, capped the same way.
    3. Jurisdiction's statutory default rate (10% for ACT/NSW/VIC, 30% for QLD).

    Building jurisdiction is resolved from ``buildings.jurisdiction`` (or legacy
    ``buildings.state``), defaulting to ACT — mirrors services/jurisdiction_service.py.

    Returns a dict with keys:
        rate_pct        float  — effective annual rate
        source          str    — "special_resolution" | "building_config" | "act_default" (ACT only) | "jurisdiction_default" (NSW/VIC/QLD)
        resolution_id   str|None
        resolution_date str|None  — ISO-8601 date the resolution was passed
        expires_at      str|None  — ISO-8601 date the override lapses (None = indefinite)
        max_rate_pct    float  — jurisdiction's special-resolution ceiling
        jurisdiction    str    — resolved jurisdiction code
        statute         str    — primary legislation citation for that jurisdiction's rate
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    # Building config fetched once up-front — jurisdiction affects both the
    # special-resolution branch and the building-config/default branch below.
    building_config = await db.buildings.find_one(
        {"id": building_id},
        {"arrears_interest_rate_pct": 1, "jurisdiction": 1, "state": 1, "_id": 0},
    )
    jurisdiction = _resolve_jurisdiction(building_config)
    statutory_default = float(rule_engine.max_interest_rate_pa(jurisdiction))
    special_resolution_ceiling = float(rule_engine.max_interest_rate_pa_by_special_resolution(jurisdiction))
    statute = _statute_citation(jurisdiction)

    # 1. Check for an active special resolution in committee_resolutions.
    resolution = await db.committee_resolutions.find_one(
        {
            "building_id": building_id,
            "resolution_type": "levy_interest_rate_override",
            "status": "active",
            "$or": [
                {"expires_at": None},
                {"expires_at": {"$gt": now_iso}},
            ],
        },
        sort=[("passed_date", -1)],
    )
    if resolution:
        rate = min(float(resolution.get("interest_rate_pct", statutory_default)), special_resolution_ceiling)
        return {
            "rate_pct": rate,
            "source": "special_resolution",
            "resolution_id": str(rid) if (rid := resolution.get("id") or resolution.get("_id")) else None,
            "resolution_date": resolution.get("passed_date"),
            "expires_at": resolution.get("expires_at"),
            "max_rate_pct": special_resolution_ceiling,
            "jurisdiction": jurisdiction,
            "statute": statute,
        }

    # 2. Building config field (set by strata manager via trust settings), capped
    #    at the same jurisdiction ceiling as a special resolution (matches prior
    #    behaviour, which capped both sources at the same MAX_INTEREST_RATE_PCT).
    if (building_config or {}).get("arrears_interest_rate_pct") is not None:
        try:
            rate = min(float(building_config["arrears_interest_rate_pct"]), special_resolution_ceiling)
        except (TypeError, ValueError):
            rate = statutory_default
        source = "building_config"
    else:
        rate = statutory_default
        # "act_default" is a historical string value kept stable for ACT so existing
        # consumers matching on it are unaffected. For any other jurisdiction it would
        # be actively misleading (e.g. a QLD building would show rate_pct=30 with a
        # source string claiming "act_default") — those get the honestly-named value.
        source = "act_default" if jurisdiction == "ACT" else "jurisdiction_default"
    return {
        "rate_pct": rate,
        "source": source,
        "resolution_id": None,
        "resolution_date": None,
        "expires_at": None,
        "max_rate_pct": special_resolution_ceiling,
        "jurisdiction": jurisdiction,
        "statute": statute,
    }


async def get_effective_credit_interest_rate(building_id: str, db: Any) -> float:
    """Return the effective OWNER-EARNED credit-interest rate for a building — the rate paid
    (display-only estimate, never posted to the ledger) to a unit whose net_balance is in
    credit, symmetric in shape to get_effective_interest_rate()'s arrears-side rate but with
    NO statutory basis: this is an optional building policy, not a legal requirement.

    GAP-FIN-047 §2: no ACT/UTMA (or NSW/VIC/QLD equivalent) provision was found entitling an
    owner to interest on an overpaid trust-account balance — offering one is an OC's own
    fairness/goodwill choice, not a compliance obligation. Defaults to 0.0 (no credit interest)
    for every building, including East Gate (whose 2022 AGM policy has no such provision), until
    an operator explicitly sets buildings.credit_interest_rate_pct. Softly bounded at
    MAX_INTEREST_RATE_PCT purely as a data-entry sanity check, not a jurisdictional ceiling.
    """
    building_config = await db.buildings.find_one(
        {"id": building_id}, {"credit_interest_rate_pct": 1, "_id": 0},
    )
    rate = (building_config or {}).get("credit_interest_rate_pct")
    if rate is None:
        return 0.0
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(rate, MAX_INTEREST_RATE_PCT))


async def record_special_resolution_rate(
    building_id: str,
    interest_rate_pct: float,
    passed_date: str,
    passed_by: str,
    expires_at: Optional[str],
    notes: Optional[str],
    db: Any,
) -> dict:
    """Persist a special-resolution interest-rate override (UTMA 2011 s.94 for ACT).

    Deactivates any prior active override for the building before inserting
    the new record.  Returns the newly created resolution document.

    Jurisdiction-aware: resolves the building's jurisdiction and validates
    interest_rate_pct against that jurisdiction's real statutory band via
    domain.jurisdictional_rules.rule_engine. For NSW/VIC, whose statutes permit
    no special-resolution increase (floor == ceiling == 10%), any value other
    than 10% is rejected — that is the correct legal outcome, not a bug.
    """
    building_config = await db.buildings.find_one(
        {"id": building_id},
        {"jurisdiction": 1, "state": 1, "_id": 0},
    )
    jurisdiction = _resolve_jurisdiction(building_config)
    floor = float(rule_engine.max_interest_rate_pa(jurisdiction))
    ceiling = float(rule_engine.max_interest_rate_pa_by_special_resolution(jurisdiction))

    if interest_rate_pct > ceiling:
        raise ValueError(
            f"Special resolution rate {interest_rate_pct}% exceeds legal maximum {ceiling}% "
            f"for jurisdiction {jurisdiction}"
        )
    if interest_rate_pct < floor:
        raise ValueError(
            f"Special resolution rate {interest_rate_pct}% is below the statutory default "
            f"{floor}% for jurisdiction {jurisdiction}"
        )

    now_iso = datetime.now(timezone.utc).isoformat()

    # Deactivate prior override.
    await db.committee_resolutions.update_many(
        {
            "building_id": building_id,
            "resolution_type": "levy_interest_rate_override",
            "status": "active",
        },
        {"$set": {"status": "superseded", "superseded_at": now_iso}},
    )

    doc = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "resolution_type": "levy_interest_rate_override",
        "title": f"Special resolution: levy arrears interest rate {interest_rate_pct:.1f}% p.a.",
        "interest_rate_pct": interest_rate_pct,
        "passed_date": passed_date,
        "passed_by": passed_by,
        "expires_at": expires_at,
        "status": "active",
        "notes": notes,
        "created_at": now_iso,
    }
    await db.committee_resolutions.insert_one(doc)
    logger.info(
        "levy_interest_rate_override recorded building=%s rate=%.1f passed_by=%s",
        building_id, interest_rate_pct, passed_by,
    )
    return doc


__all__ = [
    "DEFAULT_INTEREST_RATE_PCT",
    "MAX_INTEREST_RATE_PCT",
    "compute_accrued_interest",
    "get_building_interest_rate",
    "get_effective_interest_rate",
    "get_effective_credit_interest_rate",
    "record_special_resolution_rate",
]
