"""
Layer 2 — NPP End-to-End ID matches an open invoice. Score: 0.95.
"""
from __future__ import annotations

from integrations.envelopes import BankTxObserved
from integrations.matching.layers.base import LotCandidate, MatchScore


class L2NppE2E:
    name = "L2_npp_e2e"

    def score(self, tx: BankTxObserved, candidates: list[LotCandidate]) -> MatchScore:
        """Generated function header.

        Function: L2NppE2E.score
        Path: backend/integrations/matching/layers/l2_npp_e2e.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        e2e = tx.osko_e2e_id or ""
        if not e2e:
            return MatchScore(layer_name=self.name, lot=None, score=0.0)

        for lot in candidates:
            if lot.osko_e2e_id and lot.osko_e2e_id == e2e:
                return MatchScore(
                    layer_name=self.name, lot=lot, score=0.95,
                    evidence={"e2e_id": e2e},
                )

        return MatchScore(layer_name=self.name, lot=None, score=0.0,
                          evidence={"e2e_id": e2e, "no_lot_found": True})
