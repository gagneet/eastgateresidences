"""
Layer 5 — Jaro-Winkler similarity ≥ 0.88 on owner name + exact amount. Score: 0.80.

Uses RapidFuzz (rapidfuzz>=3.0.0) for the JW computation.
"""
from __future__ import annotations

from rapidfuzz.distance import JaroWinkler

from integrations.envelopes import BankTxObserved
from integrations.matching.layers.base import LotCandidate, MatchScore

_JW_THRESHOLD = 0.88


class L5JWNameExactAmount:
    name = "L5_jw_name_exact_amount"

    def score(self, tx: BankTxObserved, candidates: list[LotCandidate]) -> MatchScore:
        """Generated function header.

        Function: L5JWNameExactAmount.score
        Path: backend/integrations/matching/layers/l5_jw_name_exact_amount.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        payer = (tx.description or "").lower()
        if not payer:
            return MatchScore(layer_name=self.name, lot=None, score=0.0)

        best_sim = 0.0
        best_lot: LotCandidate | None = None

        for lot in candidates:
            if tx.amount_cents != lot.open_levy_cents:
                continue
            sim = JaroWinkler.normalized_similarity(payer, lot.owner_name.lower())
            if sim >= _JW_THRESHOLD and sim > best_sim:
                best_sim = sim
                best_lot = lot

        if best_lot is not None:
            return MatchScore(
                layer_name=self.name, lot=best_lot, score=0.80,
                evidence={
                    "jaro_winkler": round(best_sim, 4),
                    "payer": (tx.description or "")[:40],
                    "owner": best_lot.owner_name,
                    "amount_cents": tx.amount_cents,
                },
            )

        return MatchScore(layer_name=self.name, lot=None, score=0.0)
