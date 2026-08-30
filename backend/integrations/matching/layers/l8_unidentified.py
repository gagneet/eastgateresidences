"""
Layer 8 — Catch-all. Always scores 0.0. Routes to unidentified-money sub-ledger.

Every transaction that reaches L8 has failed all seven identification layers.
The engine writes it to the review queue with match_type="unidentified".
"""
from __future__ import annotations

from integrations.envelopes import BankTxObserved
from integrations.matching.layers.base import LotCandidate, MatchScore


class L8Unidentified:
    name = "L8_unidentified"

    def score(self, tx: BankTxObserved, candidates: list[LotCandidate]) -> MatchScore:
        """Generated function header.

        Function: L8Unidentified.score
        Path: backend/integrations/matching/layers/l8_unidentified.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return MatchScore(
            layer_name=self.name,
            lot=None,
            score=0.0,
            evidence={"reason": "no_layer_matched"},
        )
