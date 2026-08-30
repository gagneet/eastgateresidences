"""
tests/backend/integrations/matching/test_layers.py — Unit tests for L1–L8.

Each layer is tested in isolation. No DB, no async — pure function calls.
Multi-tenant: candidate lots from building "16244" are never matched against
a transaction for "13195".
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from integrations.envelopes import BankTxObserved
from integrations.matching.layers.base import LotCandidate
from integrations.matching.layers.l1_exact_crn import L1ExactCRN, validate_crn
from integrations.matching.layers.l2_npp_e2e import L2NppE2E
from integrations.matching.layers.l3_partial_crn import L3PartialCRN
from integrations.matching.layers.l4_unit_ref_amount_timing import L4UnitRefAmountTiming
from integrations.matching.layers.l5_jw_name_exact_amount import L5JWNameExactAmount
from integrations.matching.layers.l6_surname_fuzzy import L6SurnameFuzzy
from integrations.matching.layers.l7_exact_amount_unique import L7ExactAmountUnique
from integrations.matching.layers.l8_unidentified import L8Unidentified

BUILDING_A = "16244"
BUILDING_B = "13195"

# Valid MOD10V05 CRNs (precomputed from mock_biller.build_crn — all verified)
CRN_A1 = "0162440010019"  # BUILDING_A lot 001 inst 001
CRN_A2 = "0162440020017"  # BUILDING_A lot 002 inst 001
CRN_B1 = "0131950010015"  # BUILDING_B lot 001 inst 001

_DT = datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc)


def _tx(**overrides) -> BankTxObserved:
    defaults = dict(
        provider_txn_id="txn-test",
        tenant_id=BUILDING_A,
        account_ref="trust-16244",
        occurred_at=_DT,
        amount_cents=153000,
        description="LEVY PAYMENT",
        bpay_crn=None,
        osko_e2e_id=None,
        lot_ref_raw=None,
    )
    defaults.update(overrides)
    return BankTxObserved(**defaults)


def _lot(**overrides) -> LotCandidate:
    defaults = dict(
        lot_id="lot-001",
        unit_number="1",
        building_id=BUILDING_A,
        owner_name="Margaret Thompson",
        open_levy_cents=153000,
        due_date="2026-03-31",
    )
    defaults.update(overrides)
    return LotCandidate(**defaults)


# ══════════════════════════════════════════════════════════════════════════════
# L1 — Exact CRN
# ══════════════════════════════════════════════════════════════════════════════

class TestL1ExactCRN:
    layer = L1ExactCRN()

    def test_validate_crn_valid(self):
        assert validate_crn(CRN_A1) is True

    def test_validate_crn_invalid_checksum(self):
        bad = CRN_A1[:-1] + str((int(CRN_A1[-1]) + 1) % 10)
        assert validate_crn(bad) is False

    def test_validate_crn_non_numeric(self):
        assert validate_crn("01624400100AB") is False

    def test_validate_crn_too_short(self):
        assert validate_crn("12345678") is False

    def test_exact_crn_match_returns_score_1(self):
        lot = _lot(crn=CRN_A1)
        tx = _tx(bpay_crn=CRN_A1, amount_cents=lot.open_levy_cents)
        result = self.layer.score(tx, [lot])
        assert result.score == 1.0
        assert result.lot == lot
        assert result.evidence["checksum_valid"] is True

    def test_no_crn_in_tx_returns_0(self):
        lot = _lot(crn=CRN_A1)
        result = self.layer.score(_tx(bpay_crn=None), [lot])
        assert result.score == 0.0
        assert result.lot is None

    def test_invalid_checksum_returns_0(self):
        bad_crn = CRN_A1[:-1] + str((int(CRN_A1[-1]) + 1) % 10)
        lot = _lot(crn=CRN_A1)
        result = self.layer.score(_tx(bpay_crn=bad_crn), [lot])
        assert result.score == 0.0
        assert result.evidence["checksum_valid"] is False

    def test_valid_crn_no_matching_lot_returns_0(self):
        lot = _lot(crn=CRN_A2)  # different CRN
        result = self.layer.score(_tx(bpay_crn=CRN_A1), [lot])
        assert result.score == 0.0
        assert result.evidence.get("no_lot_found") is True

    def test_cross_building_crn_does_not_match(self):
        """CRN from BUILDING_B must not match lots from BUILDING_A."""
        lot_a = _lot(crn=CRN_A1, building_id=BUILDING_A)
        result = self.layer.score(_tx(bpay_crn=CRN_B1), [lot_a])
        assert result.score == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# L2 — NPP E2E ID
# ══════════════════════════════════════════════════════════════════════════════

class TestL2NppE2E:
    layer = L2NppE2E()

    def test_matching_e2e_returns_095(self):
        e2e = "NPP20260423SIERRA001"
        lot = _lot(osko_e2e_id=e2e)
        result = self.layer.score(_tx(osko_e2e_id=e2e), [lot])
        assert result.score == 0.95
        assert result.lot == lot
        assert result.evidence["e2e_id"] == e2e

    def test_no_e2e_in_tx_returns_0(self):
        result = self.layer.score(_tx(osko_e2e_id=None), [_lot(osko_e2e_id="x")])
        assert result.score == 0.0

    def test_mismatching_e2e_returns_0(self):
        lot = _lot(osko_e2e_id="CORRECT_ID")
        result = self.layer.score(_tx(osko_e2e_id="WRONG_ID"), [lot])
        assert result.score == 0.0
        assert result.evidence.get("no_lot_found") is True

    def test_lot_without_e2e_skipped(self):
        lot = _lot(osko_e2e_id=None)
        result = self.layer.score(_tx(osko_e2e_id="SOME_E2E"), [lot])
        assert result.score == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# L3 — Partial CRN
# ══════════════════════════════════════════════════════════════════════════════

class TestL3PartialCRN:
    layer = L3PartialCRN()

    def test_crn_prefix_in_description_returns_090(self):
        crn_prefix = CRN_A1[:-1]
        lot = _lot(crn=CRN_A1)
        tx = _tx(description=f"INTERNET BANKING {crn_prefix} LEVY")
        result = self.layer.score(tx, [lot])
        assert result.score == 0.90
        assert result.lot == lot

    def test_no_description_returns_0(self):
        lot = _lot(crn=CRN_A1)
        result = self.layer.score(_tx(description=""), [lot])
        assert result.score == 0.0

    def test_crn_prefix_not_in_description_returns_0(self):
        lot = _lot(crn=CRN_A1)
        result = self.layer.score(_tx(description="LEVY PAYMENT NO REF"), [lot])
        assert result.score == 0.0

    def test_lot_without_crn_skipped(self):
        lot = _lot(crn=None)
        result = self.layer.score(_tx(description=f"PAYMENT {CRN_A1[:-1]}"), [lot])
        assert result.score == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# L4 — Unit ref + amount + timing
# ══════════════════════════════════════════════════════════════════════════════

class TestL4UnitRefAmountTiming:
    layer = L4UnitRefAmountTiming()

    def test_unit_ref_amount_within_30_days_returns_085(self):
        lot = _lot(unit_number="5", open_levy_cents=153000, due_date="2026-04-01")
        # occurred_at=2026-04-23 → 22 days after due_date
        tx = _tx(description="LEVY UNIT 5 STRATA", amount_cents=153000)
        result = self.layer.score(tx, [lot])
        assert result.score == 0.85
        assert result.lot == lot
        assert result.evidence["unit"] == "5"

    def test_unit_ref_outside_30_days_returns_0(self):
        lot = _lot(unit_number="5", open_levy_cents=153000, due_date="2025-12-01")
        # occurred_at=2026-04-23 → 143 days after due_date
        tx = _tx(description="LEVY UNIT 5 STRATA", amount_cents=153000)
        result = self.layer.score(tx, [lot])
        assert result.score == 0.0

    def test_unit_ref_wrong_amount_returns_0(self):
        lot = _lot(unit_number="5", open_levy_cents=153000, due_date="2026-04-01")
        tx = _tx(description="LEVY UNIT 5 STRATA", amount_cents=100000)
        result = self.layer.score(tx, [lot])
        assert result.score == 0.0

    def test_no_unit_ref_in_description_returns_0(self):
        lot = _lot(unit_number="5", open_levy_cents=153000, due_date="2026-04-01")
        tx = _tx(description="LEVY PAYMENT NO REF", amount_cents=153000)
        result = self.layer.score(tx, [lot])
        assert result.score == 0.0

    def test_lot_style_description_parsed(self):
        lot = _lot(unit_number="12", open_levy_cents=153000, due_date="2026-04-01")
        tx = _tx(description="STRATA LOT 12 PAYMENT", amount_cents=153000)
        result = self.layer.score(tx, [lot])
        assert result.score == 0.85

    def test_prefers_lot_ref_raw_over_description_regex(self):
        """GAP-FIN-015: lot_ref_raw (populated by _extract_signals(), which recognises
        alphanumeric codes like TH078/UA064) must be used ahead of L4's own digit-only
        description regex, which cannot parse those codes at all."""
        lot = _lot(unit_number="TH078", open_levy_cents=157356, due_date="2026-04-01")
        tx = _tx(
            description="DEFT/BPAY payment received — reconciled from FY2025 closing balance - LOT TH078",
            amount_cents=157356,
            lot_ref_raw="TH078",
        )
        result = self.layer.score(tx, [lot])
        assert result.score == 0.85
        assert result.lot == lot

    def test_missing_due_date_does_not_block_match(self):
        """GAP-FIN-015: bank_feeds._load_lot_candidates() frequently can't supply a real
        due_date for historical/reconstructed transactions. A missing/invalid due_date
        must not hard-reject an otherwise-exact unit+amount match."""
        lot = _lot(unit_number="TH078", open_levy_cents=157356, due_date="")
        tx = _tx(description="payment - LOT TH078", amount_cents=157356, lot_ref_raw="TH078")
        result = self.layer.score(tx, [lot])
        assert result.score == 0.85
        assert result.lot == lot
        assert "days_from_due" not in result.evidence

    def test_lot_ref_raw_with_keyword_prefix_matches_bare_unit_number(self):
        """Regression: integrations.demo_bank.ingestion._extract_signals()'s _LOT_RE captures
        re.group(0) (the FULL match including the keyword), so for a plain-numeric building
        it populates lot_ref_raw="LOT 12" / "UNIT 5" — not the bare "12"/"5" that
        lot.unit_number actually stores. An earlier version of this fix compared lot_ref_raw
        to lot.unit_number directly and silently scored 0.0 for every plain-numeric building
        (found only by re-deriving lot_ref_raw via the real _extract_signals() rather than
        hand-constructing it in a test, which is what let this slip through the first time)."""
        lot = _lot(unit_number="12", open_levy_cents=153000, due_date="2026-04-01")
        tx = _tx(description="STRATA LOT 12 PAYMENT", amount_cents=153000, lot_ref_raw="LOT 12")
        result = self.layer.score(tx, [lot])
        assert result.score == 0.85
        assert result.lot == lot

    def test_lot_ref_raw_alphanumeric_code_still_matches_as_is(self):
        """Companion to the above: a keyword embedded IN the unit code with no separating
        space (lot_ref_raw="TH078") must still match lot.unit_number="TH078" as-is — the
        keyword-prefix-stripping fallback must not corrupt this, already-working, case."""
        lot = _lot(unit_number="TH078", open_levy_cents=157356, due_date="2026-04-01")
        tx = _tx(description="payment - LOT TH078", amount_cents=157356, lot_ref_raw="TH078")
        result = self.layer.score(tx, [lot])
        assert result.score == 0.85
        assert result.lot == lot


# ══════════════════════════════════════════════════════════════════════════════
# L5 — Jaro-Winkler name + exact amount
# ══════════════════════════════════════════════════════════════════════════════

class TestL5JWNameExactAmount:
    layer = L5JWNameExactAmount()

    def test_high_similarity_exact_amount_returns_080(self):
        lot = _lot(owner_name="Margaret Thompson", open_levy_cents=153000)
        # Single-character transposition "Margraret" → JW ≈ 0.977 (≥ 0.88)
        tx = _tx(description="MARGRARET THOMPSON", amount_cents=153000)
        result = self.layer.score(tx, [lot])
        assert result.score == 0.80
        assert result.lot == lot
        assert result.evidence["jaro_winkler"] >= 0.88

    def test_low_similarity_returns_0(self):
        lot = _lot(owner_name="Margaret Thompson", open_levy_cents=153000)
        tx = _tx(description="JOHN SMITH PAYMENT", amount_cents=153000)
        result = self.layer.score(tx, [lot])
        assert result.score == 0.0

    def test_high_similarity_wrong_amount_returns_0(self):
        lot = _lot(owner_name="Margaret Thompson", open_levy_cents=153000)
        tx = _tx(description="MARGARET THOMPSON", amount_cents=999)
        result = self.layer.score(tx, [lot])
        assert result.score == 0.0

    def test_empty_description_returns_0(self):
        lot = _lot(owner_name="Margaret Thompson", open_levy_cents=153000)
        result = self.layer.score(_tx(description="", amount_cents=153000), [lot])
        assert result.score == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# L6 — Surname fuzzy
# ══════════════════════════════════════════════════════════════════════════════

class TestL6SurnameFuzzy:
    layer = L6SurnameFuzzy()

    def test_matching_surname_returns_060(self):
        lot = _lot(owner_name="James O'Brien")
        tx = _tx(description="LEVY OBRIEN", amount_cents=999999)  # amount differs
        result = self.layer.score(tx, [lot])
        assert result.score == 0.60
        assert result.lot == lot

    def test_no_surname_match_returns_0(self):
        lot = _lot(owner_name="James O'Brien")
        tx = _tx(description="LEVY CHEN")
        result = self.layer.score(tx, [lot])
        assert result.score == 0.0

    def test_empty_description_returns_0(self):
        lot = _lot(owner_name="James O'Brien")
        result = self.layer.score(_tx(description=""), [lot])
        assert result.score == 0.0

    def test_picks_best_when_multiple_near_matches(self):
        lot1 = _lot(lot_id="lot-1", owner_name="James O'Brien")
        lot2 = _lot(lot_id="lot-2", owner_name="James Obrian")  # closer to "OBRIEN"
        tx = _tx(description="LEVY OBRIEN")
        result = self.layer.score(tx, [lot1, lot2])
        assert result.score == 0.60
        assert result.lot is not None


# ══════════════════════════════════════════════════════════════════════════════
# L7 — Exact amount unique
# ══════════════════════════════════════════════════════════════════════════════

class TestL7ExactAmountUnique:
    layer = L7ExactAmountUnique()

    def test_unique_amount_returns_050(self):
        lot = _lot(open_levy_cents=182500)
        tx = _tx(amount_cents=182500)
        result = self.layer.score(tx, [lot])
        assert result.score == 0.50
        assert result.lot == lot
        assert result.evidence["unique"] is True

    def test_ambiguous_amount_returns_0(self):
        lot1 = _lot(lot_id="lot-1", open_levy_cents=153000)
        lot2 = _lot(lot_id="lot-2", open_levy_cents=153000)
        tx = _tx(amount_cents=153000)
        result = self.layer.score(tx, [lot1, lot2])
        assert result.score == 0.0
        assert result.evidence["ambiguous_count"] == 2

    def test_no_lot_with_exact_amount_returns_0(self):
        lot = _lot(open_levy_cents=153000)
        result = self.layer.score(_tx(amount_cents=999999), [lot])
        assert result.score == 0.0
        assert result.evidence.get("no_lot_found") is True


# ══════════════════════════════════════════════════════════════════════════════
# L8 — Unidentified
# ══════════════════════════════════════════════════════════════════════════════

class TestL8Unidentified:
    layer = L8Unidentified()

    def test_always_returns_0(self):
        lot = _lot()
        result = self.layer.score(_tx(), [lot])
        assert result.score == 0.0
        assert result.lot is None
        assert result.evidence["reason"] == "no_layer_matched"

    def test_always_returns_0_empty_candidates(self):
        result = self.layer.score(_tx(), [])
        assert result.score == 0.0


# ---------------------------------------------------------------------------
# L7 must not override an explicit unit reference (2026-08-28)
# ---------------------------------------------------------------------------

class TestL7RespectsAnExplicitUnitReference:
    """An amount coincidence must never outrank the unit the payment names.

    Observed live on East Gate: three payments of $1,761.50 belonging to TH078,
    TH080 and TH082 were each matched to TH072 — whose open receivable happened to be
    $1,761.50 — because this layer scored on amount alone and never read
    `tx.lot_ref_raw`. All three were surfaced to the reviewer as confident matches.
    Accepting them would have credited three owners' payments to a fourth.
    """

    def test_amount_match_on_a_different_lot_is_rejected(self):
        tx = _tx(amount_cents=176150, lot_ref_raw="TH082")
        candidates = [
            _lot(lot_id="TH072", unit_number="TH072", open_levy_cents=176150),
            _lot(lot_id="TH082", unit_number="TH082", open_levy_cents=0),
        ]
        result = L7ExactAmountUnique().score(tx, candidates)
        assert result.score == 0.0
        assert result.lot is None
        assert result.evidence.get("unit_referenced") == "TH082"

    def test_amount_match_on_the_named_lot_still_scores(self):
        """The veto must not blind the layer — when the amount matches the lot the
        transaction actually names, that is the strongest possible agreement."""
        tx = _tx(amount_cents=176150, lot_ref_raw="TH072")
        candidates = [
            _lot(lot_id="TH072", unit_number="TH072", open_levy_cents=176150),
            _lot(lot_id="TH082", unit_number="TH082", open_levy_cents=0),
        ]
        result = L7ExactAmountUnique().score(tx, candidates)
        assert result.score == 0.50
        assert result.lot.unit_number == "TH072"

    def test_unreferenced_transactions_are_unaffected(self):
        """No unit reference means amount is all we have — original behaviour stands."""
        tx = _tx(amount_cents=176150, lot_ref_raw=None)
        candidates = [
            _lot(lot_id="TH072", unit_number="TH072", open_levy_cents=176150),
            _lot(lot_id="TH082", unit_number="TH082", open_levy_cents=0),
        ]
        result = L7ExactAmountUnique().score(tx, candidates)
        assert result.score == 0.50
        assert result.lot.unit_number == "TH072"

    def test_a_reference_to_an_unknown_unit_does_not_veto(self):
        """If the named unit is not a candidate at all the reference tells us nothing
        about THIS building's lots, so it must not suppress an otherwise-valid match."""
        tx = _tx(amount_cents=176150, lot_ref_raw="ZZ999")
        candidates = [
            _lot(lot_id="TH072", unit_number="TH072", open_levy_cents=176150),
        ]
        result = L7ExactAmountUnique().score(tx, candidates)
        assert result.score == 0.50

    def test_keyword_prefixed_reference_is_understood(self):
        """"UNIT 82" and "TH082" must behave identically — the form reducer is shared
        with L4 precisely so the two layers cannot disagree about unit identity."""
        tx = _tx(amount_cents=176150, lot_ref_raw="UNIT TH082")
        candidates = [
            _lot(lot_id="TH072", unit_number="TH072", open_levy_cents=176150),
            _lot(lot_id="TH082", unit_number="TH082", open_levy_cents=0),
        ]
        assert L7ExactAmountUnique().score(tx, candidates).score == 0.0
