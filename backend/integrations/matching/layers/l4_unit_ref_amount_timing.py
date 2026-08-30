"""
Layer 4 — Lot reference parse + exact amount + timing window (when known). Score: 0.85.
"""
from __future__ import annotations

import re
from datetime import date

from integrations.envelopes import BankTxObserved
from integrations.matching.layers.base import LotCandidate, MatchScore

# Patterns that indicate a unit/lot number in a free-text description. Fallback only —
# prefer tx.lot_ref_raw (populated by integrations.demo_bank.ingestion._extract_signals(),
# which also recognises alphanumeric unit codes like "TH078"/"UA064" that these
# digit-only patterns cannot).
_UNIT_PATTERNS = [
    re.compile(r"\bunit\s*(\d+)", re.IGNORECASE),
    re.compile(r"\blot\s*(\d+)", re.IGNORECASE),
    re.compile(r"\bu\.?(\d+)\b", re.IGNORECASE),
    re.compile(r"\b(\d+)\s*/\s*\d+\b"),  # "5/10" strata-style
]

# _extract_signals()'s _LOT_RE captures re.group(0) — the FULL match including the keyword
# — so it can preserve alphanumeric codes like "TH078"/"UA064" as one token (the keyword is
# part of the unit id there, with no separating space). But for a plain-numeric building
# (unit_number="12"), the same regex on "STRATA LOT 12 PAYMENT" yields lot_ref_raw="LOT 12"
# (keyword + space + digits), which will never string-equal a candidate's bare "12". This
# prefix list mirrors _LOT_RE's keyword alternation so a "KEYWORD <digits>" raw value can be
# reduced to bare digits before comparison, without needing to change _extract_signals()
# itself (which is also used, unmodified, by the direct-ingestion path).
_KEYWORD_PREFIX_RE = re.compile(
    r"^(?:UNIT|LOT|APT|APARTMENT|TOWNHOUSE|VILLA|PENTHOUSE)\s+(.+)$",
    re.IGNORECASE,
)

_TIMING_DAYS = 30


def _extract_unit_number(text: str) -> str | None:
    """Generated function header.

    Function: _extract_unit_number
    Path: backend/integrations/matching/layers/l4_unit_ref_amount_timing.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for pat in _UNIT_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1)
    return None


def _candidate_unit_forms(raw: str) -> set[str]:
    """All plausible bare-unit-id forms for a raw lot reference, upper-cased.

    Includes the raw value as-is (matches alphanumeric codes like "TH078" where the
    keyword has no separating space) and, if `raw` starts with a known keyword followed
    by whitespace, the remainder after stripping that prefix (matches "LOT 12" -> "12").
    """
    raw_norm = raw.strip().upper()
    forms = {raw_norm}
    stripped = _KEYWORD_PREFIX_RE.match(raw_norm)
    if stripped:
        forms.add(stripped.group(1).strip())
    return forms


class L4UnitRefAmountTiming:
    name = "L4_unit_ref_amount_timing"

    def score(self, tx: BankTxObserved, candidates: list[LotCandidate]) -> MatchScore:
        """Generated function header.

        Function: L4UnitRefAmountTiming.score
        Path: backend/integrations/matching/layers/l4_unit_ref_amount_timing.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        unit_num = (tx.lot_ref_raw or "").strip() or _extract_unit_number(tx.description or "")
        if not unit_num:
            return MatchScore(layer_name=self.name, lot=None, score=0.0)
        unit_num_forms = _candidate_unit_forms(unit_num)

        tx_date = tx.occurred_at.date()

        for lot in candidates:
            if str(lot.unit_number).strip().upper() not in unit_num_forms:
                continue
            if tx.amount_cents != lot.open_levy_cents:
                continue
            # GAP-FIN-015: candidate due_date is frequently unavailable (bank_feeds._load_lot_candidates()
            # has no reliable per-transaction levy-schedule source yet). Treat a missing/invalid due_date
            # as "timing unverifiable" rather than a hard rejection — unit-ref + exact-amount is still a
            # meaningful signal on its own; days_from_due is simply omitted from the evidence in that case.
            evidence = {"unit": unit_num, "amount_cents": tx.amount_cents}
            try:
                due = date.fromisoformat(lot.due_date)
            except (ValueError, TypeError, AttributeError):
                return MatchScore(layer_name=self.name, lot=lot, score=0.85, evidence=evidence)

            days_delta = abs((tx_date - due).days)
            if days_delta <= _TIMING_DAYS:
                evidence["days_from_due"] = (tx_date - due).days
                return MatchScore(layer_name=self.name, lot=lot, score=0.85, evidence=evidence)

        return MatchScore(layer_name=self.name, lot=None, score=0.0,
                          evidence={"unit_extracted": unit_num})
