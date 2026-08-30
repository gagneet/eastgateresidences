# @featuretrace:owner-transfers — Tests for the strata_sync._upsert_owners wiring into
# detect_and_create_portal_owner_transfer (the call site added alongside the owner
# transfer drift detection feature).
# Layer: test
# Related: backend/routers/strata_sync.py
#          backend/services/ownership_transfer_detection_service.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from routers.strata_sync import _upsert_owners

    STRATA_SYNC_MODULE = "routers.strata_sync"
except ImportError:
    from routers.strata_sync import _upsert_owners

    STRATA_SYNC_MODULE = "routers.strata_sync"


def _async_iter(items):
    async def gen(*_args, **_kwargs):
        for item in items:
            yield item

    return gen()


def _mock_db(valid_units):
    db = MagicMock()
    db._db = {"units": MagicMock()}
    db._db["units"].find = MagicMock(return_value=_async_iter(
        [{"unit_number": u} for u in valid_units]
    ))
    db._db["units"].update_one = AsyncMock()
    db.strata_owners.find_one = AsyncMock(return_value=None)
    db.strata_owners.update_one = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_upsert_owners_calls_detection_with_building_scoped_args():
    db = _mock_db(["TH078"])
    owners = [{"unit_number": "TH078", "owner": "Tavis Christian Hamer", "lot": 78}]

    with patch(f"{STRATA_SYNC_MODULE}.db", db), \
         patch(
             f"{STRATA_SYNC_MODULE}.detect_and_create_portal_owner_transfer",
             new=AsyncMock(return_value={"created": False, "reason": "owner_names_match"}),
         ) as mock_detect:
        await _upsert_owners("13195", owners, "2026-07-02T00:00:00+00:00")

    mock_detect.assert_awaited_once_with(
        db,
        "13195",
        "TH078",
        "Tavis Christian Hamer",
        detected_at="2026-07-02T00:00:00+00:00",
        # East Gate's owner reads are served by Postgres; the drift baseline must
        # come from the serving store, not MongoDB alone.
        use_cutover_baseline=True,
    )
    # The strata_owners upsert must still run regardless of the detection outcome.
    db.strata_owners.update_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_owners_survives_detection_exception_and_still_upserts():
    """The call site wraps detect_and_create_portal_owner_transfer in try/except and
    only logs a warning on failure (per routers/strata_sync.py:244-250) — a bug in the
    detector must never break the strata_sync ingest pipeline that owns it."""
    db = _mock_db(["TH078"])
    owners = [{"unit_number": "TH078", "owner": "Tavis Christian Hamer", "lot": 78}]

    with patch(f"{STRATA_SYNC_MODULE}.db", db), \
         patch(
             f"{STRATA_SYNC_MODULE}.detect_and_create_portal_owner_transfer",
             new=AsyncMock(side_effect=RuntimeError("boom")),
         ):
        # Must not raise.
        await _upsert_owners("13195", owners, "2026-07-02T00:00:00+00:00")

    db.strata_owners.update_one.assert_awaited_once()
    upsert_filter, upsert_update = db.strata_owners.update_one.call_args[0]
    assert upsert_filter == {"building_id": "13195", "unit_number": "TH078"}
    assert upsert_update["$set"]["owner_name"] == "Tavis Christian Hamer"


@pytest.mark.asyncio
async def test_upsert_owners_processes_remaining_rows_after_one_detection_failure():
    """One owner row's detection failure must not stop the loop — later rows in the
    same sync batch still get their upsert and their own detection attempt."""
    db = _mock_db(["TH078", "TH079"])
    owners = [
        {"unit_number": "TH078", "owner": "Owner Fails", "lot": 78},
        {"unit_number": "TH079", "owner": "Owner Succeeds", "lot": 79},
    ]

    async def flaky_detect(_db, _building_id, unit_number, *_args, **_kwargs):
        if unit_number == "TH078":
            raise RuntimeError("boom")
        return {"created": False, "reason": "owner_names_match"}

    with patch(f"{STRATA_SYNC_MODULE}.db", db), \
         patch(f"{STRATA_SYNC_MODULE}.detect_and_create_portal_owner_transfer", new=flaky_detect):
        await _upsert_owners("13195", owners, "2026-07-02T00:00:00+00:00")

    assert db.strata_owners.update_one.await_count == 2


@pytest.mark.asyncio
async def test_upsert_owners_passes_actual_building_id_not_hardcoded():
    """Multi-tenant regression guard: the detection call and the strata_owners upsert
    must both use the caller's building_id, never a hardcoded '13195' fallback."""
    db = _mock_db(["1"])
    owners = [{"unit_number": "1", "owner": "Someone Else", "lot": 1}]

    with patch(f"{STRATA_SYNC_MODULE}.db", db), \
         patch(
             f"{STRATA_SYNC_MODULE}.detect_and_create_portal_owner_transfer",
             new=AsyncMock(return_value={"created": False, "reason": "owner_names_match"}),
         ) as mock_detect:
        await _upsert_owners("16244", owners, "2026-07-02T00:00:00+00:00")

    assert mock_detect.call_args[0][1] == "16244"
    upsert_filter = db.strata_owners.update_one.call_args[0][0]
    assert upsert_filter["building_id"] == "16244"


# ---------------------------------------------------------------------------
# Honorific drift is not an ownership transfer (2026-08-28)
# ---------------------------------------------------------------------------

class TestHonorificsAreNotOwnershipChanges:
    """The portal renders the same owner with and without a title between scrapes.

    Without stripping honorifics, "rachel clarke" one run and "ms rachel clarke" the
    next produce different name keys, a different portal_detected_signature, and a
    transfer request raised for EC review over a cosmetic difference.

    Measured on the live 2026-08-28 East Gate scrape: **29 requests created, 28 of them
    title-only drift.** Exactly one — UA029, "emma watt" => "sonja zink" — was a real
    ownership change. That noise re-pollutes the review queue the operator had
    deliberately purged of 88 system-generated rows.
    """

    @pytest.mark.parametrize("plain,titled", [
        ("Rachel Clarke", "Ms Rachel Clarke"),
        ("Jason Carter", "Mr Jason Carter"),
        ("Gunjan Pandey", "Dr Gunjan Pandey"),
        ("Isabelle Baldwin", "Miss Isabelle Baldwin"),
        ("Tin Leung & Jennifer Leung", "Mr Tin Leung & Ms Jennifer Leung"),
    ])
    def test_title_does_not_change_the_key(self, plain, titled):
        from services.ownership_transfer_detection_service import _name_key
        assert _name_key(plain) == _name_key(titled)

    def test_a_real_name_change_still_differs(self):
        """The fix must not blind the detector to genuine transfers."""
        from services.ownership_transfer_detection_service import _name_key
        assert _name_key("Emma Watt") != _name_key("Sonja Zink")

    def test_surnames_are_not_swallowed(self):
        """Titles are stripped as whole WORDS, so a name that merely contains those
        letters survives intact."""
        from services.ownership_transfer_detection_service import _name_key
        assert "mrozek" in _name_key("Anna Mrozek")
        assert "drummond" in _name_key("Paul Drummond")
        assert "missen" in _name_key("Kate Missen")

    def test_key_is_whitespace_normalised_after_stripping(self):
        """Removing a leading title must not leave a doubled or leading space, or two
        spellings of the same owner would still produce different keys."""
        from services.ownership_transfer_detection_service import _name_key
        key = _name_key("Mr  Tin   Leung")
        assert key == "tin leung"
        assert not key.startswith(" ") and "  " not in key
