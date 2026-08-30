"""
Layer 6 — Surname fuzzy match only (no amount gate). Score: 0.60.

Uses rapidfuzz token_sort_ratio on the last token of the payer description vs
the last token of the owner name. Threshold: 80 (out of 100).
"""
from __future__ import annotations

from rapidfuzz import fuzz

from integrations.envelopes import BankTxObserved
from integrations.matching.layers.base import LotCandidate, MatchScore

_SURNAME_RATIO_THRESHOLD = 80


def _last_token(name: str) -> str:
    """Generated function header.

    Function: _last_token
    Path: backend/integrations/matching/layers/l6_surname_fuzzy.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parts = name.strip().split()
    return parts[-1].lower() if parts else name.lower()


class L6SurnameFuzzy:
    name = "L6_surname_fuzzy"

    def score(self, tx: BankTxObserved, candidates: list[LotCandidate]) -> MatchScore:
        """Generated function header.

        Function: L6SurnameFuzzy.score
        Path: backend/integrations/matching/layers/l6_surname_fuzzy.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        payer_surname = _last_token(tx.description or "")
        if not payer_surname:
            return MatchScore(layer_name=self.name, lot=None, score=0.0)

        best_ratio = 0
        best_lot: LotCandidate | None = None

        for lot in candidates:
            owner_surname = _last_token(lot.owner_name)
            ratio = fuzz.token_sort_ratio(payer_surname, owner_surname)
            if ratio >= _SURNAME_RATIO_THRESHOLD and ratio > best_ratio:
                best_ratio = ratio
                best_lot = lot

        if best_lot is not None:
            return MatchScore(
                layer_name=self.name, lot=best_lot, score=0.60,
                evidence={
                    "payer_surname": payer_surname,
                    "owner_surname": _last_token(best_lot.owner_name),
                    "ratio": best_ratio,
                },
            )

        return MatchScore(layer_name=self.name, lot=None, score=0.0)
