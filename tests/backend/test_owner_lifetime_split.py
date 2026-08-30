"""GAP-FIN-054 — lifetime per-owner split scaling contract.

The lifetime split shows how much of a unit's cumulative "Total Paid" (Mongo
`total_paid_all_years`, Admin+Sinking+GST across all years) was paid by the current owner
vs previous owners. PostgreSQL supplies only the tenure-based attribution RATIO (receipts
partitioned by whether `received_on` fell in the current owner's tenure); the displayed
magnitude is the authoritative Mongo total scaled by that ratio. Invariant:
`current + previous == total_paid_all_years` to the cent — one lifetime total, never a
second conflicting figure. `_scale_lifetime_owner_split` is the pure scaling core.
"""

import pytest

from routers.finance import _scale_lifetime_owner_split


def test_split_sums_to_tile_total_exactly():
    for pc, pp, tile in [
        (1_200_000, 1_800_000, 28783.04),   # 40/60 of a real total
        (2_229_000, 771_000, 100000.00),
        (1, 2, 100.00),                     # rounding residual case
        (999_999, 1, 54321.99),
    ]:
        current, previous = _scale_lifetime_owner_split(pc, pp, tile)
        assert current is not None and previous is not None
        assert round(current + previous, 2) == pytest.approx(round(tile, 2), abs=0.01)


def test_no_transfer_gives_full_total_to_current_owner():
    current, previous = _scale_lifetime_owner_split(3_000_000, 0, 28783.04)
    assert current == pytest.approx(28783.04, abs=0.01)
    assert previous == pytest.approx(0.0, abs=0.01)


def test_all_previous_gives_zero_to_current_owner():
    current, previous = _scale_lifetime_owner_split(0, 3_000_000, 28783.04)
    assert current == pytest.approx(0.0, abs=0.01)
    assert previous == pytest.approx(28783.04, abs=0.01)


def test_zero_pg_total_is_unknown_not_zero():
    # No PG receipts to form a ratio -> (None, None) so the caller renders null / hides the
    # card (missing != zero), rather than fabricating a $0 split.
    assert _scale_lifetime_owner_split(0, 0, 28783.04) == (None, None)


def test_ratio_uses_pg_but_magnitude_uses_tile_total():
    # PG cents (30_000.00 total) are a DIFFERENT magnitude than the Mongo tile
    # ($28,783.04) — the split must scale to the tile, not the PG total (stores diverge
    # during cutover). Current = 50% of the TILE, not 50% of the PG total.
    current, previous = _scale_lifetime_owner_split(1_500_000, 1_500_000, 28783.04)
    assert current == pytest.approx(14391.52, abs=0.01)
    assert previous == pytest.approx(14391.52, abs=0.01)
    assert round(current + previous, 2) == pytest.approx(28783.04, abs=0.01)


def test_zero_tile_total_yields_zero_split_when_pg_has_ratio():
    # A unit with a known PG ratio but $0 lifetime paid -> $0/$0 (still a defined split).
    current, previous = _scale_lifetime_owner_split(1_000_000, 1_000_000, 0.0)
    assert current == pytest.approx(0.0, abs=0.01)
    assert previous == pytest.approx(0.0, abs=0.01)
