"""
Layer 3 — CRN prefix substring present in transaction description. Score: 0.90.

Uses all digits except the check digit (the prefix), to tolerate descriptions
that omit or garble the final check-digit character.
"""
from __future__ import annotations

from integrations.envelopes import BankTxObserved
from integrations.matching.layers.base import LotCandidate, MatchScore


class L3PartialCRN:
    name = "L3_partial_crn"

    def score(self, tx: BankTxObserved, candidates: list[LotCandidate]) -> MatchScore:
        """Generated function header.

        Function: L3PartialCRN.score
        Path: backend/integrations/matching/layers/l3_partial_crn.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        desc = tx.description or ""
        if not desc:
            return MatchScore(layer_name=self.name, lot=None, score=0.0)

        for lot in candidates:
            if not lot.crn or len(lot.crn) < 2:
                continue
            crn_prefix = lot.crn[:-1]  # strip check digit
            if crn_prefix in desc:
                return MatchScore(
                    layer_name=self.name, lot=lot, score=0.90,
                    evidence={"crn_prefix": crn_prefix, "description": desc[:80]},
                )

        return MatchScore(layer_name=self.name, lot=None, score=0.0)
