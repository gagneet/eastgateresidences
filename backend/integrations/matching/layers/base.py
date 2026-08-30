"""
backend/integrations/matching/layers/base.py — Shared types for all matching layers.

# @featuretrace:financial_matching — matching pipeline base types.
# Layer: service
# Data flow: MatchingEngine → MatchLayer.score() → MatchScore → engine decision (building-scoped).
# Related: backend/integrations/matching/engine.py
#           backend/routers/financial_matching.py
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from integrations.envelopes import BankTxObserved


@dataclass(frozen=True)
class LotCandidate:
    """A lot that could be the payer of an incoming bank transaction.

    Assembled by the matching router from units + trust_levy_schedules_v2 +
    mock_biller_allocations before calling the engine. Keeps the engine pure.
    """

    lot_id: str
    unit_number: str
    building_id: str
    owner_name: str  # Primary owner display name
    open_levy_cents: int  # Amount currently due on this lot
    due_date: str  # ISO YYYY-MM-DD of the most recent unpaid levy
    crn: str | None = None  # BPAY CRN if biller integration active
    osko_e2e_id: str | None = None  # NPP E2E ID on outstanding invoice (if any)


@dataclass(frozen=True)
class MatchScore:
    """Output of a single matching layer for the best candidate it found."""

    layer_name: str
    lot: LotCandidate | None  # None when the layer found no match (or L8)
    score: float  # 0.0–1.0; only 0.0 when lot is None
    evidence: dict[str, Any] = field(default_factory=dict)


class MatchLayer(Protocol):
    """Structural protocol every scoring layer must satisfy."""

    name: str  # class-level identifier used in evidence and logs

    def score(
            self,
            tx: BankTxObserved,
            candidates: list[LotCandidate],
    ) -> MatchScore:
        """Return the best MatchScore for tx across all candidates.

        Layers that find no candidate meeting their criteria return
        MatchScore(layer_name=self.name, lot=None, score=0.0).
        L8 always returns score=0.0 and lot=None.
        """
        ...
