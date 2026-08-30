"""
Layer 7 — Exact amount matches exactly one open levy. Score: 0.50.

If multiple lots share the same open levy amount the signal is ambiguous and
the layer returns 0.0.

An explicit unit reference on the transaction OVERRIDES this layer (2026-08-28).
Amount alone is weak evidence — an amount can coincide with a completely different
lot's open receivable. Observed live on East Gate: payments belonging to TH078, TH080
and TH082 (each $1,761.50) were all matched to TH072, whose open receivable happened to
be $1,761.50, because this layer never looked at `tx.lot_ref_raw`. Accepting those would
have credited three owners' payments to a fourth.

So when the transaction names a unit and that unit is a known candidate, this layer may
only ever return THAT lot. A contradiction is reported as no match, not as a lower-
confidence guess — the reference is the stronger signal and a human should see the
conflict rather than have it silently resolved the wrong way.
"""
from __future__ import annotations

from integrations.envelopes import BankTxObserved
from integrations.matching.layers.base import LotCandidate, MatchScore
from integrations.matching.layers.l4_unit_ref_amount_timing import _candidate_unit_forms


class L7ExactAmountUnique:
    name = "L7_exact_amount_unique"

    def score(self, tx: BankTxObserved, candidates: list[LotCandidate]) -> MatchScore:
        """Generated function header.

        Function: L7ExactAmountUnique.score
        Path: backend/integrations/matching/layers/l7_exact_amount_unique.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        matches = [lot for lot in candidates if lot.open_levy_cents == tx.amount_cents]

        # An explicit unit reference outranks an amount coincidence. Reuse L4's form
        # reducer rather than reimplementing it, so the two layers cannot disagree about
        # what counts as the same unit ("TH082", "UNIT 82", "82").
        referenced = (tx.lot_ref_raw or "").strip()
        if referenced:
            forms = _candidate_unit_forms(referenced)
            named = [
                lot for lot in candidates
                if str(lot.unit_number).strip().upper() in forms
            ]
            if named:
                # The transaction names a lot we know. Only that lot is admissible here.
                matches = [lot for lot in matches if lot in named]
                if not matches:
                    return MatchScore(
                        layer_name=self.name, lot=None, score=0.0,
                        evidence={
                            "amount_cents": tx.amount_cents,
                            "unit_referenced": referenced,
                            "rejected": "amount matches a different lot than the one "
                                        "the transaction names",
                        },
                    )

        if len(matches) == 1:
            return MatchScore(
                layer_name=self.name, lot=matches[0], score=0.50,
                evidence={"amount_cents": tx.amount_cents, "unique": True},
            )

        if len(matches) > 1:
            return MatchScore(
                layer_name=self.name, lot=None, score=0.0,
                evidence={"amount_cents": tx.amount_cents, "ambiguous_count": len(matches)},
            )

        return MatchScore(layer_name=self.name, lot=None, score=0.0,
                          evidence={"amount_cents": tx.amount_cents, "no_lot_found": True})
