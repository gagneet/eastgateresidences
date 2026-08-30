"""
Layer 1 — Exact BPAY CRN match with MOD10V05 checksum validation.
Score: 1.0 on exact match with valid checksum; 0.0 otherwise.
"""
from __future__ import annotations

import re

from integrations.envelopes import BankTxObserved
from integrations.matching.layers.base import LotCandidate, MatchScore


def _mod10v05_check_digit(digits_without_check: str) -> str:
    """Compute the MOD10V05 (BPAY) check digit for a numeric string.

    Each digit is weighted by its position (right to left, 1-indexed):
    odd positions × 1, even positions × 2; products > 9 have 9 subtracted.
    Check digit = (10 - (sum % 10)) % 10.
    """
    total = 0
    for i, ch in enumerate(reversed(digits_without_check)):
        d = int(ch)
        if i % 2 == 1:  # even position (1-indexed from right)
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - (total % 10)) % 10)


def validate_crn(crn: str) -> bool:
    """Return True if crn is a valid MOD10V05 CRN (9–20 numeric digits)."""
    if not re.fullmatch(r"\d{9,20}", crn):
        return False
    return crn[-1] == _mod10v05_check_digit(crn[:-1])


class L1ExactCRN:
    name = "L1_exact_crn"

    def score(self, tx: BankTxObserved, candidates: list[LotCandidate]) -> MatchScore:
        """Generated function header.

        Function: L1ExactCRN.score
        Path: backend/integrations/matching/layers/l1_exact_crn.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        raw = tx.bpay_crn or ""
        if not raw:
            return MatchScore(layer_name=self.name, lot=None, score=0.0)

        checksum_ok = validate_crn(raw)
        if not checksum_ok:
            return MatchScore(
                layer_name=self.name, lot=None, score=0.0,
                evidence={"crn": raw, "checksum_valid": False},
            )

        for lot in candidates:
            if lot.crn and lot.crn == raw:
                return MatchScore(
                    layer_name=self.name, lot=lot, score=1.0,
                    evidence={"crn": raw, "checksum_valid": True},
                )

        return MatchScore(
            layer_name=self.name, lot=None, score=0.0,
            evidence={"crn": raw, "checksum_valid": True, "no_lot_found": True},
        )
