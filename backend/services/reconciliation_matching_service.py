"""
Reconciliation Matching Service — smart matching engine for bank reconciliation.

Implements weighted signal scoring, anomaly detection, and exception reporting
for Phase 2 Trust Accounting reconciliation.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from pydantic import BaseModel
from typing import Any, Dict, List, Optional


# ─── Models ──────────────────────────────────────────────────────────────────

class ReconciliationMatch(BaseModel):
    bank_line_id: str
    internal_transaction_id: str
    confidence_score: float  # 0.0 – 1.0
    match_type: str  # exact | likely | weak | ambiguous | none
    match_reasons: List[str]  # ["exact_amount", "date_within_2_days", ...]
    suggested_action: str  # auto_match | review | flag


class MatchResult(BaseModel):
    status: str  # confirmed | idempotent_replay
    match_id: str
    bank_line_id: str
    internal_transaction_id: str
    matched_by: Optional[str] = None
    matched_at: Optional[str] = None


class UnmatchResult(BaseModel):
    status: str  # unmatched
    match_id: str
    unmatched_by: str
    reason: str
    unmatched_at: str


# ─── Inline Levenshtein & helpers ────────────────────────────────────────────

def _levenshtein_ratio(s1: str, s2: str) -> float:
    """Normalized Levenshtein similarity ratio (0.0–1.0). Implemented inline."""
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    s1, s2 = s1.lower(), s2.lower()
    if s1 == s2:
        return 1.0
    # Cap at 200 chars to keep O(n²) bounded
    s1 = s1[:200]
    s2 = s2[:200]
    len1, len2 = len(s1), len(s2)
    prev = list(range(len2 + 1))
    for i in range(1, len1 + 1):
        curr = [i] + [0] * len2
        for j in range(1, len2 + 1):
            if s1[i - 1] == s2[j - 1]:
                curr[j] = prev[j - 1]
            else:
                curr[j] = 1 + min(prev[j], curr[j - 1], prev[j - 1])
        prev = curr
    distance = prev[len2]
    return 1.0 - (distance / max(len1, len2))


def _token_overlap(s1: str, s2: str) -> float:
    """Jaccard token overlap ratio between two strings."""
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    tokens1 = set(s1.lower().split())
    tokens2 = set(s2.lower().split())
    if not tokens1 or not tokens2:
        return 0.0
    return len(tokens1 & tokens2) / len(tokens1 | tokens2)


def _date_proximity(date1: str, date2: str) -> float:
    """
    Date proximity score:
      1.0  → same day
      0.5  → within ±2 days
      0.2  → within 3–7 days
      0.0  → more than 7 days apart
    """
    try:
        d1 = datetime.fromisoformat(date1[:10])
        d2 = datetime.fromisoformat(date2[:10])
        delta = abs((d1 - d2).days)
        if delta == 0:
            return 1.0
        if delta <= 2:
            return 0.5
        if delta <= 7:
            return 0.2
        return 0.0
    except (ValueError, TypeError):
        return 0.0


# ─── Matching Engine ─────────────────────────────────────────────────────────

class MatchingEngine:
    """
    Score candidate matches using weighted signal combination.

    Signals and weights:
    - exact_amount_match:        0.40  (amount_cents equal)
    - amount_tolerance_match:    0.20  (within ±200 cents tolerance for bank fees)
    - date_proximity:            0.20  (1.0 same day, 0.5 ±2 days, 0 >7 days)
    - reference_similarity:      0.15  (Levenshtein ratio on reference strings)
    - description_similarity:    0.05  (token overlap on description)

    Thresholds:
    - score >= 0.90: exact        → auto_match
    - score >= 0.70: likely       → review
    - score >= 0.50: weak         → flag
    - score < 0.50:  ambiguous    → flag (exception queue)
    """

    WEIGHT_EXACT_AMOUNT = 0.40
    WEIGHT_AMOUNT_TOLERANCE = 0.20
    WEIGHT_DATE_PROXIMITY = 0.20
    WEIGHT_REFERENCE_SIMILARITY = 0.15
    WEIGHT_DESCRIPTION_SIMILARITY = 0.05
    AMOUNT_TOLERANCE_CENTS = 200

    # Maximum achievable score = exact_amount(0.40) + same_date(0.20)
    # + reference_match(0.15) + description_overlap(0.05) = 0.80.
    # Setting THRESHOLD_EXACT=0.80 ensures a perfect 4-signal match is typed "exact".
    THRESHOLD_EXACT = 0.80
    THRESHOLD_LIKELY = 0.70
    THRESHOLD_WEAK = 0.50

    def score_pair(
            self,
            bank_line: Dict[str, Any],
            internal_tx: Dict[str, Any],
    ) -> ReconciliationMatch:
        """Score a single (bank_line, internal_tx) pair and return a match result.

        Cross-building pairs are immediately rejected with score 0.0 / type "none"
        to enforce building-level data isolation.
        """
        reasons: List[str] = []
        score = 0.0

        # Building-isolation guard: never match across buildings
        bl_building = bank_line.get("building_id")
        tx_building = internal_tx.get("building_id")
        if bl_building and tx_building and bl_building != tx_building:
            return ReconciliationMatch(
                bank_line_id=str(bank_line.get("_id", bank_line.get("id", ""))),
                internal_transaction_id=str(internal_tx.get("_id", internal_tx.get("id", ""))),
                confidence_score=0.0,
                match_type="none",
                match_reasons=["cross_building_mismatch"],
                suggested_action="reject",
            )

        bank_amount = bank_line.get("amount_cents", 0)
        internal_amount = internal_tx.get("amount_cents", 0)

        # Signal 1: exact amount (0.40)
        if bank_amount == internal_amount:
            score += self.WEIGHT_EXACT_AMOUNT
            reasons.append("exact_amount")
        # Signal 2: tolerance match (0.20) — mutually exclusive with exact via elif
        elif abs(bank_amount - internal_amount) <= self.AMOUNT_TOLERANCE_CENTS:
            score += self.WEIGHT_AMOUNT_TOLERANCE
            reasons.append("amount_within_tolerance")

        # Signal 3: date proximity (0.20)
        bank_date = (bank_line.get("statement_date") or bank_line.get("date") or "")
        internal_date = (internal_tx.get("transaction_date") or internal_tx.get("date") or "")
        date_score = _date_proximity(bank_date, internal_date)
        score += self.WEIGHT_DATE_PROXIMITY * date_score
        if date_score == 1.0:
            reasons.append("same_date")
        elif date_score >= 0.5:
            reasons.append("date_within_2_days")
        elif date_score > 0:
            reasons.append("date_within_7_days")

        # Signal 4: reference similarity (0.15)
        bank_ref = (bank_line.get("external_reference") or bank_line.get("reference") or "").strip()
        internal_ref = (internal_tx.get("reference") or "").strip()
        if bank_ref or internal_ref:
            ref_sim = _levenshtein_ratio(bank_ref, internal_ref)
            score += self.WEIGHT_REFERENCE_SIMILARITY * ref_sim
            if ref_sim >= 0.9:
                reasons.append("reference_match")
            elif ref_sim >= 0.6:
                reasons.append("reference_similar")

        # Signal 5: description similarity (0.05)
        bank_desc = (bank_line.get("description") or "").strip()
        internal_desc = (internal_tx.get("description") or internal_tx.get("narration") or "").strip()
        if bank_desc or internal_desc:
            desc_sim = _token_overlap(bank_desc, internal_desc)
            score += self.WEIGHT_DESCRIPTION_SIMILARITY * desc_sim
            if desc_sim >= 0.5:
                reasons.append("description_overlap")

        score = min(1.0, score)

        if score >= self.THRESHOLD_EXACT:
            match_type = "exact"
            suggested_action = "auto_match"
        elif score >= self.THRESHOLD_LIKELY:
            match_type = "likely"
            suggested_action = "review"
        elif score >= self.THRESHOLD_WEAK:
            match_type = "weak"
            suggested_action = "flag"
        else:
            match_type = "ambiguous"
            suggested_action = "flag"

        return ReconciliationMatch(
            bank_line_id=str(bank_line.get("_id", bank_line.get("id", ""))),
            internal_transaction_id=str(internal_tx.get("_id", internal_tx.get("id", ""))),
            confidence_score=round(score, 4),
            match_type=match_type,
            match_reasons=reasons,
            suggested_action=suggested_action,
        )

    def find_best_matches(
            self,
            bank_lines: List[Dict[str, Any]],
            internal_transactions: List[Dict[str, Any]],
            min_score: float = 0.50,
    ) -> List[ReconciliationMatch]:
        """
        Compute best-match suggestions for all unmatched bank lines.
        Returns suggestions sorted by confidence_score descending.
        """
        results: List[ReconciliationMatch] = []
        for bank_line in bank_lines:
            best: Optional[ReconciliationMatch] = None
            for internal_tx in internal_transactions:
                candidate = self.score_pair(bank_line, internal_tx)
                if candidate.confidence_score >= min_score:
                    if best is None or candidate.confidence_score > best.confidence_score:
                        best = candidate
            if best is not None:
                results.append(best)
        results.sort(key=lambda m: m.confidence_score, reverse=True)
        return results


# ─── Anomaly / Exception Detectors ───────────────────────────────────────────

def detect_duplicate_bank_lines(
        bank_lines: List[Dict[str, Any]],
        window_days: int = 3,
) -> List[Dict[str, Any]]:
    """
    Detect duplicate bank lines: same (amount_cents, reference) within window_days.
    Returns list of exception dicts for each suspected duplicate.
    """
    exceptions: List[Dict[str, Any]] = []
    # Group by (amount, reference); then check date proximity within window
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for line in bank_lines:
        amount = line.get("amount_cents", 0)
        ref = (line.get("external_reference") or line.get("reference") or "").strip()
        groups[f"{amount}:{ref}"].append(line)

    for _, group in groups.items():
        if len(group) < 2:
            continue
        for i, line_a in enumerate(group):
            for line_b in group[i + 1:]:
                date_a = line_a.get("statement_date") or line_a.get("date") or ""
                date_b = line_b.get("statement_date") or line_b.get("date") or ""
                proximity = _date_proximity(date_a, date_b)
                # _date_proximity returns >0 for dates within 7 days; use window_days
                try:
                    delta = abs(
                        (datetime.fromisoformat(date_a[:10]) - datetime.fromisoformat(date_b[:10])).days
                    ) if date_a and date_b else 999
                except (ValueError, TypeError):
                    delta = 999
                if delta <= window_days:
                    exceptions.append({
                        "exception_type": "duplicate_bank_line",
                        "entity_id": str(line_b.get("_id", line_b.get("id", ""))),
                        "duplicate_of_id": str(line_a.get("_id", line_a.get("id", ""))),
                        "detail": (
                            f"Possible duplicate: amount {line_a.get('amount_cents')} cents, "
                            f"reference '{line_a.get('external_reference', '')}', "
                            f"dates {date_a} and {date_b} ({delta} days apart)."
                        ),
                    })
    return exceptions


def detect_duplicate_internal_transactions(
        internal_transactions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Detect internal transactions sharing the same idempotency_key.
    """
    exceptions: List[Dict[str, Any]] = []
    seen: Dict[str, str] = {}  # idempotency_key → first id

    for tx in internal_transactions:
        key = tx.get("idempotency_key")
        if not key:
            continue
        tx_id = str(tx.get("_id", tx.get("id", "")))
        if key in seen:
            exceptions.append({
                "exception_type": "duplicate_internal_transaction",
                "entity_id": tx_id,
                "duplicate_of_id": seen[key],
                "detail": f"Duplicate idempotency_key '{key}' found.",
            })
        else:
            seen[key] = tx_id

    return exceptions


def detect_stale_unmatched(
        bank_lines: List[Dict[str, Any]],
        stale_days: int = 45,
) -> List[Dict[str, Any]]:
    """Detect bank lines that have been unmatched for more than stale_days days."""
    exceptions: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    for line in bank_lines:
        if line.get("matched_status") == "matched":
            continue
        created_str = line.get("created_at") or ""
        if not created_str:
            continue
        try:
            created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            delta_days = (now - created).days
            if delta_days > stale_days:
                exceptions.append({
                    "exception_type": "stale_unmatched",
                    "entity_id": str(line.get("_id", line.get("id", ""))),
                    "detail": (
                        f"Bank line unmatched for {delta_days} days "
                        f"(threshold: {stale_days} days). "
                        f"Amount: {line.get('amount_cents')} cents, "
                        f"date: {line.get('statement_date', '')}."
                    ),
                })
        except (ValueError, TypeError):
            continue

    return exceptions


def detect_suspicious_variances(
        bank_lines: List[Dict[str, Any]],
        internal_transactions: List[Dict[str, Any]],
        variance_pct: float = 0.05,
) -> List[Dict[str, Any]]:
    """
    Detect pairs with close dates/references but amount variance > variance_pct (5%).
    Only evaluates date-proximate pairs to avoid false positives.
    """
    exceptions: List[Dict[str, Any]] = []

    for bank_line in bank_lines:
        bank_amount = bank_line.get("amount_cents", 0)
        if bank_amount == 0:
            continue
        bank_date = (bank_line.get("statement_date") or bank_line.get("date") or "")
        for internal_tx in internal_transactions:
            internal_amount = internal_tx.get("amount_cents", 0)
            if internal_amount == 0 or bank_amount == internal_amount:
                continue
            internal_date = (internal_tx.get("transaction_date") or internal_tx.get("date") or "")
            if _date_proximity(bank_date, internal_date) < 0.5:
                continue
            variance = abs(bank_amount - internal_amount) / abs(internal_amount)
            if variance > variance_pct:
                exceptions.append({
                    "exception_type": "suspicious_variance",
                    "entity_id": str(bank_line.get("_id", bank_line.get("id", ""))),
                    "related_id": str(internal_tx.get("_id", internal_tx.get("id", ""))),
                    "detail": (
                        f"Amount variance {variance:.1%} exceeds {variance_pct:.0%} threshold. "
                        f"Bank: {bank_amount} cents, Internal: {internal_amount} cents."
                    ),
                })

    return exceptions


def detect_many_to_one(
        matches: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Detect many-to-one: multiple bank lines confirmed to the same internal transaction.
    Operates on confirmed match records from trust_reconciliation_matches collection.
    """
    exceptions: List[Dict[str, Any]] = []
    internal_to_bank: Dict[str, List[str]] = defaultdict(list)

    for match in matches:
        bank_id = str(match.get("bank_statement_line_id") or match.get("bank_line_id") or "")
        for ledger_id in match.get("ledger_entry_ids", []):
            internal_to_bank[str(ledger_id)].append(bank_id)
        # Also handle the new schema: single internal_transaction_id
        itx_id = match.get("internal_transaction_id")
        if itx_id:
            internal_to_bank[str(itx_id)].append(bank_id)

    for internal_id, bank_ids in internal_to_bank.items():
        unique_bank_ids = list(dict.fromkeys(bank_ids))
        if len(unique_bank_ids) > 1:
            exceptions.append({
                "exception_type": "many_to_one",
                "entity_id": internal_id,
                "detail": (
                    f"Internal transaction {internal_id} is matched by "
                    f"{len(unique_bank_ids)} bank lines: {unique_bank_ids}."
                ),
            })

    return exceptions
