# Regression tests for the 2026-08-20 /admin/owner-transfers bug: a unit legitimately held
# by two owners produced a pending "transfer" request from one of its own joint owners to
# the other, because the drift detector treated any imported name missing from the canonical
# user_units set as an incoming transferee — even when NO current owner had gone away.
#
# Two defects, both covered here:
#   1. detect_and_create_portal_owner_transfer must not raise a transfer for a pure
#      owner-set ADDITION (nothing removed).
#   2. link_missing_co_owners must be able to complete a PARTIALLY-linked unit, which
#      create_initial_ownership_link refuses to touch ("owner_already_canonical") — the
#      gap that left the co-owner unlinked and made the detector mis-fire in the first place.
from unittest.mock import AsyncMock, MagicMock
import importlib.util
from pathlib import Path

import pytest

try:
    from backend.services.ownership_transfer_detection_service import (
        CO_OWNER_ADDITION_REASON,
        CO_OWNER_LINK_BACKFILL_SOURCE,
        detect_and_create_portal_owner_transfer,
        link_missing_co_owners,
    )
except ImportError:
    from services.ownership_transfer_detection_service import (
        CO_OWNER_ADDITION_REASON,
        CO_OWNER_LINK_BACKFILL_SOURCE,
        detect_and_create_portal_owner_transfer,
        link_missing_co_owners,
    )


_FIX_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "scripts"
    / "data_repair"
    / "fix_co_owner_addition_transfer_requests_20260820.py"
)
_FIX_SPEC = importlib.util.spec_from_file_location(
    "fix_co_owner_addition_transfer_requests_20260820", _FIX_SCRIPT_PATH
)
fix_script = importlib.util.module_from_spec(_FIX_SPEC)
assert _FIX_SPEC and _FIX_SPEC.loader
_FIX_SPEC.loader.exec_module(fix_script)


def _cursor(rows):
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


def _mock_db():
    db = MagicMock()
    for name in [
        "user_units",
        "users",
        "units",
        "owner_transfer_requests",
        "memberships",
        "user_notifications",
        "strata_owners",
    ]:
        setattr(db, name, MagicMock())
    db.users.find_one = AsyncMock(return_value=None)
    db.users.insert_one = AsyncMock()
    db.user_units.find_one = AsyncMock(return_value=None)
    db.user_units.insert_one = AsyncMock()
    db.owner_transfer_requests.find_one = AsyncMock(return_value=None)
    db.owner_transfer_requests.insert_one = AsyncMock()
    db.memberships.find_one = AsyncMock(return_value=None)
    db.memberships.insert_one = AsyncMock()
    db.memberships.update_one = AsyncMock()
    db.user_notifications.insert_many = AsyncMock()
    db.strata_owners.update_one = AsyncMock()
    return db


def _with_canonical_owners(db, owners):
    """Wire user_units/users so _active_owner_info returns ``owners`` [(id, name)]."""
    db.user_units.find.return_value = _cursor(
        [{"user_id": uid, "is_primary": index == 0} for index, (uid, _) in enumerate(owners)]
    )
    db.users.find.side_effect = [
        _cursor([{"id": uid, "full_name": name, "email": f"{uid}@example.com"} for uid, name in owners]),
        _cursor([]),
    ]


# ---------------------------------------------------------------------------
# 1. The detector must not turn a joint-owner addition into a transfer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_imported_co_owner_addition_never_creates_a_transfer_request():
    """UA046: canonical owner is the primary only; the import lists both joint owners."""
    db = _mock_db()
    _with_canonical_owners(db, [("marcelo", "Marcelo Ramos da Silva")])

    result = await detect_and_create_portal_owner_transfer(
        db,
        "13195",
        "UA046",
        "Marcelo Ramos da Silva & Graciela Pezaroylo Topal",
        detected_at="2026-08-20T00:00:00+00:00",
    )

    assert result["created"] is False
    assert result["reason"] == CO_OWNER_ADDITION_REASON
    assert result["suggested_add_owner_names"] == ["Graciela Pezaroylo Topal"]
    db.owner_transfer_requests.insert_one.assert_not_awaited()
    db.users.insert_one.assert_not_awaited()
    db.user_notifications.insert_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_addition_to_an_already_two_owner_unit_is_not_a_transfer():
    """A third joint owner joining two existing ones is still not a change of ownership."""
    db = _mock_db()
    _with_canonical_owners(db, [("ann", "Ann Brooks"), ("ben", "Ben Brooks")])

    result = await detect_and_create_portal_owner_transfer(
        db, "13195", "TH010", "Ann Brooks & Ben Brooks & Cara Brooks"
    )

    assert result["reason"] == CO_OWNER_ADDITION_REASON
    assert result["suggested_add_owner_names"] == ["Cara Brooks"]
    db.owner_transfer_requests.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_co_owner_addition_dry_run_reports_both_owner_sets_without_writes():
    db = _mock_db()
    _with_canonical_owners(db, [("kaushal", "Kaushal Shah")])

    result = await detect_and_create_portal_owner_transfer(
        db, "13195", "TH073", "Kaushal Shah & Radhika Shah", dry_run=True
    )

    assert result == {
        "created": False,
        "reason": CO_OWNER_ADDITION_REASON,
        "unit_number": "TH073",
        "current_owner_names": ["Kaushal Shah"],
        "imported_raw_owner_names": ["Kaushal Shah", "Radhika Shah"],
        "projected_owner_names": ["Kaushal Shah", "Radhika Shah"],
        "suggested_add_owner_names": ["Radhika Shah"],
    }
    db.owner_transfer_requests.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_genuine_replacement_of_one_joint_owner_still_creates_a_transfer():
    """One of two owners leaves and a new name arrives — that IS a transfer."""
    db = _mock_db()
    db.user_units.find.return_value = _cursor(
        [{"user_id": "olivia", "is_primary": True}, {"user_id": "mark", "is_primary": False}]
    )
    db.users.find.side_effect = [
        _cursor([
            {"id": "olivia", "full_name": "Olivia Rollings", "email": "olivia@example.com"},
            {"id": "mark", "full_name": "Mark Raets", "email": "mark@example.com"},
        ]),
        _cursor([]),
    ]
    db.memberships.find.return_value = _cursor([])

    result = await detect_and_create_portal_owner_transfer(
        db, "13195", "TH078", "Tavis Christian Hamer & Mark Raets"
    )

    assert result["created"] is True
    transfer = db.owner_transfer_requests.insert_one.call_args[0][0]
    assert transfer["new_owner"]["full_name"] == "Tavis Christian Hamer"
    assert transfer["suggested_remove_owner_ids"] == ["olivia"]


@pytest.mark.asyncio
async def test_complete_owner_name_change_still_creates_a_transfer():
    """UA029: the sole owner name changes outright — a real sale."""
    db = _mock_db()
    _with_canonical_owners(db, [("emma", "Ms Emma Watt")])
    db.memberships.find.return_value = _cursor([])

    result = await detect_and_create_portal_owner_transfer(db, "13195", "UA029", "Sonja Zink")

    assert result["created"] is True
    transfer = db.owner_transfer_requests.insert_one.call_args[0][0]
    assert transfer["new_owner"]["full_name"] == "Sonja Zink"
    assert transfer["suggested_remove_owner_ids"] == ["emma"]


# ---------------------------------------------------------------------------
# 2. link_missing_co_owners completes a partially-linked unit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_links_only_the_missing_co_owner_and_never_reassigns_primary():
    db = _mock_db()
    db.user_units.find.return_value = _cursor([{"user_id": "marcelo"}])
    db.users.find.return_value = _cursor(
        [{"id": "marcelo", "full_name": "Marcelo Ramos da Silva"}]
    )

    result = await link_missing_co_owners(
        db,
        "13195",
        "UA046",
        ["Marcelo Ramos da Silva", "Graciela Pezaroylo Topal"],
        ["mrsilvaz@example.com", "topalgp@example.com"],
        detected_at="2026-08-20T00:00:00+00:00",
    )

    assert result["linked"] is True
    assert result["linked_owner_names"] == ["Graciela Pezaroylo Topal"]

    links = [call.args[0] for call in db.user_units.insert_one.call_args_list]
    assert len(links) == 1
    assert links[0]["is_primary"] is False
    assert links[0]["is_active"] is True
    assert links[0]["unit_number"] == "UA046"

    created_user = db.users.insert_one.call_args[0][0]
    assert created_user["full_name"] == "Graciela Pezaroylo Topal"
    assert created_user["email"] == "topalgp@example.com"
    assert created_user["is_active"] is False

    audit = db.owner_transfer_requests.insert_one.call_args[0][0]
    assert audit["source"] == CO_OWNER_LINK_BACKFILL_SOURCE
    assert audit["status"] == "approved"
    assert audit["old_owners"] == []
    assert audit["action_taken"] == "co_owner_linked"


@pytest.mark.asyncio
async def test_link_missing_co_owners_is_idempotent_when_all_owners_linked():
    db = _mock_db()
    db.user_units.find.return_value = _cursor([{"user_id": "ann"}, {"user_id": "ben"}])
    db.users.find.return_value = _cursor(
        [{"id": "ann", "full_name": "Ann Brooks"}, {"id": "ben", "full_name": "Ben Brooks"}]
    )

    result = await link_missing_co_owners(db, "13195", "TH010", ["Ann Brooks", "Ben Brooks"])

    assert result == {
        "linked": False,
        "reason": "co_owners_already_linked",
        "unit_number": "TH010",
    }
    db.user_units.insert_one.assert_not_awaited()
    db.owner_transfer_requests.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_link_missing_co_owners_defers_to_the_initial_bootstrap_when_nothing_is_linked():
    """No active link at all is the bootstrap case; routing it here would make every
    owner non-primary, so it must refuse rather than guess."""
    db = _mock_db()
    db.user_units.find.return_value = _cursor([])

    result = await link_missing_co_owners(db, "13195", "UA070", ["Mr Lloyd Taylor"])

    assert result == {"linked": False, "reason": "no_existing_owner_link"}
    db.user_units.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_link_missing_co_owners_refuses_when_an_existing_link_is_orphaned():
    """UA038: an active user_units link points at a user row that no longer exists, so
    the already-linked owner set is unknowable — linking anyway would duplicate an owner."""
    db = _mock_db()
    db.user_units.find.return_value = _cursor([{"user_id": "ghost"}])
    db.users.find.return_value = _cursor([])

    result = await link_missing_co_owners(
        db, "13195", "UA038", ["Alyx Ashley Ford", "Isabella Celeste Lomax"]
    )

    assert result["linked"] is False
    assert result["reason"] == "unresolvable_existing_owner_link"
    assert result["unresolvable_user_ids"] == ["ghost"]
    db.user_units.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_link_missing_co_owners_dry_run_writes_nothing():
    db = _mock_db()
    db.user_units.find.return_value = _cursor([{"user_id": "anthony"}])
    db.users.find.return_value = _cursor([{"id": "anthony", "full_name": "Anthony McDonald"}])

    result = await link_missing_co_owners(
        db, "13195", "UA063", ["Anthony McDonald", "Rose Marimon"], dry_run=True
    )

    assert result["would_link"] is True
    assert result["missing_owner_names"] == ["Rose Marimon"]
    db.user_units.insert_one.assert_not_awaited()
    db.users.insert_one.assert_not_awaited()
    db.owner_transfer_requests.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_link_missing_co_owners_is_scoped_to_building_id():
    db = _mock_db()
    db.user_units.find.return_value = _cursor([{"user_id": "anthony"}])
    db.users.find.return_value = _cursor([{"id": "anthony", "full_name": "Anthony McDonald"}])

    await link_missing_co_owners(
        db, "UP-DEMO-001", "UA063", ["Anthony McDonald", "Rose Marimon"],
        detected_at="2026-08-20T00:00:00+00:00",
    )

    assert db.user_units.find.call_args[0][0]["building_id"] == "UP-DEMO-001"
    assert db.user_units.insert_one.call_args[0][0]["building_id"] == "UP-DEMO-001"
    assert db.owner_transfer_requests.insert_one.call_args[0][0]["building_id"] == "UP-DEMO-001"


@pytest.mark.asyncio
async def test_link_missing_co_owners_matches_a_name_form_drift_and_links_only_the_gap():
    """Linked "Kaushal Shah" vs imported "Mr Kaushal Shah" is the SAME person.

    UPDATED 2026-08-28. This test previously asserted the run must REFUSE
    (``linked is False``, reason ``existing_owner_not_in_imported_names``). That
    refusal was a safety net for a limitation, not a requirement: ``_name_key`` did
    not strip honorifics, so it could not tell that "Kaushal Shah" and "Mr Kaushal
    Shah" were one person, and refusing was the only way to avoid attaching a second
    account for someone already linked. The test's own docstring said as much — "is
    the SAME person under a different name form".

    ``_name_key`` now strips honorifics, so the two forms match and the correct
    outcome is available: link the ONE genuinely missing co-owner and leave the
    already-linked owner alone.

    The refusal was not harmless. It left the real co-owner unlinked, which is exactly
    the state found live on UA015, UA045 and UA054 — the portal names two owners and
    PostgreSQL holds only the first.

    The duplication guard the old test protected is still asserted below, now by
    checking WHAT was written rather than that nothing was.
    """
    db = _mock_db()
    db.user_units.find.return_value = _cursor([{"user_id": "kaushal"}])
    db.users.find.return_value = _cursor([{"id": "kaushal", "full_name": "Kaushal Shah"}])

    result = await link_missing_co_owners(
        db, "13195", "TH073", ["Mr Kaushal Shah", "Radhika Shah"]
    )

    assert result["linked"] is True

    # Exactly one link and one account — for the missing co-owner only.
    assert db.user_units.insert_one.await_count == 1
    assert db.users.insert_one.await_count == 1
    created = db.users.insert_one.await_args_list[0][0][0]
    assert created["full_name"] == "Radhika Shah"

    # The already-linked owner must NOT be re-created under the titled form. This is
    # the duplication the previous refusal existed to prevent.
    created_names = [c[0][0]["full_name"] for c in db.users.insert_one.await_args_list]
    assert not any("Kaushal" in n for n in created_names), (
        f"Kaushal Shah is already linked and must not be duplicated; created: {created_names}"
    )


@pytest.mark.asyncio
async def test_link_missing_co_owners_refuses_when_a_linked_owner_has_departed():
    """A linked owner absent from the import is a departure, not an addition — that is
    the drift/transfer path's job, not this one's."""
    db = _mock_db()
    db.user_units.find.return_value = _cursor([{"user_id": "ann"}, {"user_id": "ben"}])
    db.users.find.return_value = _cursor(
        [{"id": "ann", "full_name": "Ann Brooks"}, {"id": "ben", "full_name": "Ben Brooks"}]
    )

    result = await link_missing_co_owners(db, "13195", "TH010", ["Ann Brooks", "Cara Brooks"])

    assert result["reason"] == "existing_owner_not_in_imported_names"
    assert result["unmatched_linked_owner_names"] == ["ben brooks"]
    db.user_units.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_link_missing_co_owners_refuses_an_active_link_with_no_user_id():
    """A link row with no user_id occupies an owner slot whose identity cannot be read.
    Filtering it out silently would let a duplicate through."""
    db = _mock_db()
    db.user_units.find.return_value = _cursor([{"user_id": "ann"}, {}])
    db.users.find.return_value = _cursor([{"id": "ann", "full_name": "Ann Brooks"}])

    result = await link_missing_co_owners(db, "13195", "TH010", ["Ann Brooks", "Ben Brooks"])

    assert result["linked"] is False
    assert result["reason"] == "active_owner_link_without_user_id"
    assert result["active_link_count"] == 2
    db.user_units.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_audit_records_the_accounts_real_email_when_the_import_had_none():
    """TH086's co-owner has owner_email_b="" — the audit must show the placeholder the
    account actually got, not a bare None that reads as "unknown"."""
    db = _mock_db()
    db.user_units.find.return_value = _cursor([{"user_id": "riyu"}])
    db.users.find.return_value = _cursor([{"id": "riyu", "full_name": "Riyu Kurian Abraham"}])

    result = await link_missing_co_owners(
        db, "13195", "TH086",
        ["Riyu Kurian Abraham", "Reshma Shaji"], ["riyuroy@example.com", ""],
        detected_at="2026-08-20T00:00:00+00:00",
    )

    assert result["linked"] is True
    created_user = db.users.insert_one.call_args[0][0]
    audit = db.owner_transfer_requests.insert_one.call_args[0][0]
    assert audit["new_owner"]["email"] == created_user["email"]
    assert audit["new_owner"]["email"].endswith("@strataos.local")
    assert audit["new_owner"]["is_internal_contact_email"] is True


@pytest.mark.asyncio
async def test_added_owner_names_are_reported_in_import_order():
    """Two additions at once must read in the order the import listed them, not in
    normalised-key alphabetical order."""
    db = _mock_db()
    _with_canonical_owners(db, [("zoe", "Zoe Adams")])

    result = await detect_and_create_portal_owner_transfer(
        db, "13195", "TH011", "Zoe Adams & Yannick Bell & Xavier Cole", dry_run=True
    )

    assert result["reason"] == CO_OWNER_ADDITION_REASON
    assert result["suggested_add_owner_names"] == ["Yannick Bell", "Xavier Cole"]


# ---------------------------------------------------------------------------
# 3. The repair script classifies existing rows correctly
# ---------------------------------------------------------------------------


def _pending(unit, old_names, detected_names, new_owner):
    return {
        "id": f"req-{unit}",
        "building_id": "13195",
        "unit_number": unit,
        "status": "pending",
        "source": "external_ledger_owner_name_drift",
        "old_owners": [{"full_name": name} for name in old_names],
        "new_owner": {"full_name": new_owner},
        "portal_detected_owner_names": detected_names,
    }


def test_pure_addition_is_classified_as_a_co_owner_addition():
    assert fix_script._is_pure_co_owner_addition(
        _pending("UA046", ["Marcelo Ramos da Silva"],
                 ["Marcelo Ramos da Silva", "Graciela Pezaroylo Topal"],
                 "Graciela Pezaroylo Topal")
    )


def test_replacement_is_not_classified_as_a_co_owner_addition():
    assert not fix_script._is_pure_co_owner_addition(
        _pending("UA029", ["Ms Emma Watt"], ["Sonja Zink"], "Sonja Zink")
    )


def test_partial_replacement_is_not_classified_as_a_co_owner_addition():
    assert not fix_script._is_pure_co_owner_addition(
        _pending("TH078", ["Olivia Rollings", "Mark Raets"],
                 ["Tavis Christian Hamer", "Mark Raets"], "Tavis Christian Hamer")
    )


def test_request_without_recorded_old_owners_is_left_alone():
    """A bootstrap/manual row is not owner-name drift — never auto-withdraw it."""
    assert not fix_script._is_pure_co_owner_addition(
        _pending("UA070", [], ["Mr Lloyd Taylor"], "Mr Lloyd Taylor")
    )


def test_classification_falls_back_to_raw_imported_names():
    transfer = _pending("TH073", ["Kaushal Shah"], None, "Radhika Shah")
    transfer.pop("portal_detected_owner_names")
    transfer["portal_detected_raw_owner_names"] = ["Kaushal Shah", "Radhika Shah"]
    assert fix_script._is_pure_co_owner_addition(transfer)


def test_classification_is_case_and_punctuation_insensitive():
    assert fix_script._is_pure_co_owner_addition(
        _pending("TH086", ["Riyu Kurian Abraham"],
                 ["riyu  kurian abraham", "Reshma Shaji"], "Reshma Shaji")
    )


@pytest.mark.asyncio
async def test_fix_script_refuses_to_link_names_the_withdrawn_request_never_assessed(monkeypatch):
    """units.* is read only for the owner EMAILS. If its name set has since diverged from
    the set that justified the withdrawal, linking from it would attach a name this
    withdrawal never assessed."""
    raw = {}
    raw["owner_transfer_requests"] = MagicMock()
    raw["owner_transfer_requests"].find = MagicMock(
        return_value=_cursor([
            _pending("UA046", ["Marcelo Ramos da Silva"],
                     ["Marcelo Ramos da Silva", "Graciela Pezaroylo Topal"],
                     "Graciela Pezaroylo Topal")
        ])
    )
    raw["owner_transfer_requests"].update_one = AsyncMock()
    raw["units"] = MagicMock()
    raw["units"].find_one = AsyncMock(
        return_value={
            "owner_name": "Someone Entirely Different",
            "owner_email": "other@example.com",
            "owner_name_b": None,
            "owner_email_b": None,
        }
    )

    mock_db = MagicMock()
    mock_db._db = raw
    monkeypatch.setattr(fix_script, "db", mock_db)

    linker = AsyncMock()
    monkeypatch.setattr(fix_script, "link_missing_co_owners", linker)

    result = await fix_script.run("13195", apply=True)

    assert result["withdrawn_count"] == 1
    entry = result["co_owner_links"][0]
    assert entry["reason"] == "unit_owner_names_disagree_with_withdrawn_request"
    assert entry["unit_owner_names"] == ["Someone Entirely Different"]
    linker.assert_not_awaited()


@pytest.mark.asyncio
async def test_fix_script_links_when_unit_names_match_the_withdrawn_request(monkeypatch):
    raw = {}
    raw["owner_transfer_requests"] = MagicMock()
    raw["owner_transfer_requests"].find = MagicMock(
        return_value=_cursor([
            _pending("UA046", ["Marcelo Ramos da Silva"],
                     ["Marcelo Ramos da Silva", "Graciela Pezaroylo Topal"],
                     "Graciela Pezaroylo Topal")
        ])
    )
    raw["owner_transfer_requests"].update_one = AsyncMock()
    raw["units"] = MagicMock()
    raw["units"].find_one = AsyncMock(
        return_value={
            "owner_name": "Marcelo Ramos da Silva",
            "owner_email": "mrsilvaz@example.com",
            "owner_name_b": "Graciela Pezaroylo Topal",
            "owner_email_b": "topalgp@example.com",
        }
    )

    mock_db = MagicMock()
    mock_db._db = raw
    monkeypatch.setattr(fix_script, "db", mock_db)

    linker = AsyncMock(return_value={"linked": True, "linked_owner_names": ["Graciela Pezaroylo Topal"]})
    monkeypatch.setattr(fix_script, "link_missing_co_owners", linker)

    result = await fix_script.run("13195", apply=True)

    assert result["co_owner_links"][0]["linked"] is True
    linker.assert_awaited_once()
    # Withdrawal is a status change only — the row is retained, never deleted.
    update = raw["owner_transfer_requests"].update_one.call_args[0][1]
    assert update["$set"]["status"] == "withdrawn"
    assert "$unset" not in update


@pytest.mark.asyncio
async def test_fix_script_skip_linking_only_withdraws(monkeypatch):
    raw = {}
    raw["owner_transfer_requests"] = MagicMock()
    raw["owner_transfer_requests"].find = MagicMock(
        return_value=_cursor([
            _pending("UA046", ["Marcelo Ramos da Silva"],
                     ["Marcelo Ramos da Silva", "Graciela Pezaroylo Topal"],
                     "Graciela Pezaroylo Topal")
        ])
    )
    raw["owner_transfer_requests"].update_one = AsyncMock()
    raw["units"] = MagicMock()
    raw["units"].find_one = AsyncMock(return_value=None)

    mock_db = MagicMock()
    mock_db._db = raw
    monkeypatch.setattr(fix_script, "db", mock_db)
    linker = AsyncMock()
    monkeypatch.setattr(fix_script, "link_missing_co_owners", linker)

    result = await fix_script.run("13195", apply=True, skip_linking=True)

    assert result["withdrawn_count"] == 1
    assert result["co_owner_links"] == []
    linker.assert_not_awaited()


@pytest.mark.asyncio
async def test_fix_script_dry_run_writes_nothing(monkeypatch):
    raw = {}
    raw["owner_transfer_requests"] = MagicMock()
    raw["owner_transfer_requests"].find = MagicMock(
        return_value=_cursor([
            _pending("UA046", ["Marcelo Ramos da Silva"],
                     ["Marcelo Ramos da Silva", "Graciela Pezaroylo Topal"],
                     "Graciela Pezaroylo Topal")
        ])
    )
    raw["owner_transfer_requests"].update_one = AsyncMock()
    raw["units"] = MagicMock()
    raw["units"].find_one = AsyncMock(
        return_value={
            "owner_name": "Marcelo Ramos da Silva",
            "owner_email": "mrsilvaz@example.com",
            "owner_name_b": "Graciela Pezaroylo Topal",
            "owner_email_b": "topalgp@example.com",
        }
    )

    mock_db = MagicMock()
    mock_db._db = raw
    monkeypatch.setattr(fix_script, "db", mock_db)
    linker = AsyncMock(return_value={"linked": False, "would_link": True})
    monkeypatch.setattr(fix_script, "link_missing_co_owners", linker)

    result = await fix_script.run("13195", apply=False)

    assert result["withdrawn_count"] == 1
    raw["owner_transfer_requests"].update_one.assert_not_awaited()
    assert linker.await_args.kwargs["dry_run"] is True


# ---------------------------------------------------------------------------
# 4. Adoption instead of duplication, and archival of the residue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_adopts_the_provisional_account_instead_of_duplicating_a_person():
    """The drift detector mints a portal-detected account with an internal email. A later
    import carrying that person's REAL email must take over that record, not mint a second
    account for one human (East Gate: Radhika Shah, Graciela Pezaroylo Topal, Rose Marimon)."""
    db = _mock_db()
    db.user_units.find.return_value = _cursor([{"user_id": "kaushal"}])
    db.users.find.return_value = _cursor([{"id": "kaushal", "full_name": "Kaushal Shah"}])
    db.users.update_one = AsyncMock()
    db.users.find_one = AsyncMock(
        side_effect=[
            None,  # no account on (real email, name key)
            {  # the unclaimed provisional account the detector minted
                "id": "525eaf5c",
                "email": "owner-transfer+525eaf5c@strataos.local",
                "full_name": "Radhika Shah",
                "building_id": "13195",
                "unit_number": "TH073",
                "portal_detected_owner": True,
                "is_active": False,
            },
        ]
    )
    db.user_units.find_one = AsyncMock(return_value=None)  # provisional acct is unclaimed

    result = await link_missing_co_owners(
        db, "13195", "TH073",
        ["Kaushal Shah", "Radhika Shah"], [None, "radhishah1110@example.com"],
        detected_at="2026-08-20T00:00:00+00:00",
    )

    assert result["linked"] is True
    assert result["user_ids"] == ["525eaf5c"]
    db.users.insert_one.assert_not_awaited()

    update = db.users.update_one.call_args[0][1]["$set"]
    assert update["email"] == "radhishah1110@example.com"
    assert update["is_internal_contact_email"] is False
    assert update["portal_detected_owner"] is False
    assert update["adopted_from_portal_detected_account"] is True


@pytest.mark.asyncio
async def test_bootstrap_does_not_adopt_a_provisional_account_already_linked_to_the_unit():
    """A provisional account that has become a real owner link belongs to someone —
    never repurpose it."""
    db = _mock_db()
    db.user_units.find.return_value = _cursor([{"user_id": "kaushal"}])
    db.users.find.return_value = _cursor([{"id": "kaushal", "full_name": "Kaushal Shah"}])
    db.users.update_one = AsyncMock()
    db.users.find_one = AsyncMock(
        side_effect=[
            None,
            {"id": "525eaf5c", "full_name": "Radhika Shah", "portal_detected_owner": True,
             "is_active": False},
        ]
    )
    db.user_units.find_one = AsyncMock(return_value={"id": "already-linked"})

    result = await link_missing_co_owners(
        db, "13195", "TH073",
        ["Kaushal Shah", "Radhika Shah"], [None, "radhishah1110@example.com"],
        detected_at="2026-08-20T00:00:00+00:00",
    )

    assert result["linked"] is True
    db.users.update_one.assert_not_awaited()
    created = db.users.insert_one.call_args[0][0]
    assert created["full_name"] == "Radhika Shah"
    assert created["id"] != "525eaf5c"


def _archive_db(monkeypatch, *, user, active_links=0, memberships=0, withdrawn_rows=None):
    raw = {}
    raw["owner_transfer_requests"] = MagicMock()
    raw["owner_transfer_requests"].find = MagicMock(
        side_effect=[_cursor([]), _cursor(withdrawn_rows or [])]
    )
    raw["owner_transfer_requests"].update_one = AsyncMock()
    raw["units"] = MagicMock()
    raw["units"].find_one = AsyncMock(return_value=None)
    raw["users"] = MagicMock()
    raw["users"].find_one = AsyncMock(return_value=user)
    raw["users"].update_one = AsyncMock()
    raw["user_units"] = MagicMock()
    raw["user_units"].count_documents = AsyncMock(return_value=active_links)
    raw["memberships"] = MagicMock()
    raw["memberships"].count_documents = AsyncMock(return_value=memberships)

    mock_db = MagicMock()
    mock_db._db = raw
    monkeypatch.setattr(fix_script, "db", mock_db)
    return raw


_WITHDRAWN_ROW = {
    "id": "req-TH073",
    "unit_number": "TH073",
    "new_owner": {"user_id": "525eaf5c"},
}
_PROVISIONAL = {
    "id": "525eaf5c",
    "full_name": "Radhika Shah",
    "email": "owner-transfer+525eaf5c@strataos.local",
    "portal_detected_owner": True,
    "is_active": False,
    "status": "pending_owner_transfer",
}


@pytest.mark.asyncio
async def test_archives_the_unclaimed_provisional_account_of_a_withdrawn_request(monkeypatch):
    raw = _archive_db(monkeypatch, user=_PROVISIONAL, withdrawn_rows=[_WITHDRAWN_ROW])

    result = await fix_script.run("13195", apply=True)

    entry = result["stray_provisional_accounts"][0]
    assert entry["archived"] is True
    assert entry["full_name"] == "Radhika Shah"
    update = raw["users"].update_one.call_args[0][1]["$set"]
    # Soft-archive only — the row is retained under the 7-year retention rule.
    assert update["status"] == "archived"
    assert update["is_archived"] is True
    assert update["is_active"] is False
    raw["users"].delete_one.assert_not_called()
    raw["users"].delete_many.assert_not_called()


@pytest.mark.asyncio
async def test_never_archives_a_provisional_account_that_is_in_use(monkeypatch):
    """TH086's minted account was adopted as the real co-owner link — leave it alone."""
    raw = _archive_db(
        monkeypatch, user=_PROVISIONAL, active_links=1, memberships=1,
        withdrawn_rows=[_WITHDRAWN_ROW],
    )

    result = await fix_script.run("13195", apply=True)

    entry = result["stray_provisional_accounts"][0]
    assert entry["archived"] is False
    assert entry["reason"] == "account_is_in_use"
    raw["users"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_never_archives_an_active_account(monkeypatch):
    raw = _archive_db(
        monkeypatch,
        user={**_PROVISIONAL, "is_active": True},
        withdrawn_rows=[_WITHDRAWN_ROW],
    )

    result = await fix_script.run("13195", apply=True)

    assert result["stray_provisional_accounts"][0]["reason"] == (
        "not_an_unclaimed_provisional_account"
    )
    raw["users"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_archival_is_idempotent(monkeypatch):
    raw = _archive_db(
        monkeypatch,
        user={**_PROVISIONAL, "status": "archived"},
        withdrawn_rows=[_WITHDRAWN_ROW],
    )

    result = await fix_script.run("13195", apply=True)

    assert result["stray_provisional_accounts"] == []
    raw["users"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_archival_dry_run_writes_nothing(monkeypatch):
    raw = _archive_db(monkeypatch, user=_PROVISIONAL, withdrawn_rows=[_WITHDRAWN_ROW])

    result = await fix_script.run("13195", apply=False)

    assert result["stray_provisional_accounts"][0]["would_archive"] is True
    raw["users"].update_one.assert_not_awaited()


# ---------------------------------------------------------------------------
# 5. The drift baseline must come from the store that SERVES the building
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postgres_served_owners_are_used_as_the_baseline(monkeypatch):
    """East Gate's identity_core/occupancy are promoted, so core.ownership_periods —
    not Mongo user_units — is what owner reads return. All four 2026-08-20 false
    positives had BOTH joint owners in Postgres the whole time; measuring drift against
    Mongo alone measured it against the wrong baseline."""
    import services.ownership_transfer_detection_service as svc

    db = _mock_db()
    _with_canonical_owners(db, [("marcelo", "Marcelo Ramos da Silva")])

    async def fake_cutover_names(building_id, unit_number):
        assert (building_id, unit_number) == ("13195", "UA046")
        return ["Marcelo Ramos da Silva", "Graciela Pezaroylo Topal"]

    monkeypatch.setattr(svc, "_cutover_owner_names", fake_cutover_names)

    result = await detect_and_create_portal_owner_transfer(
        db, "13195", "UA046",
        "Marcelo Ramos da Silva & Graciela Pezaroylo Topal",
        dry_run=True, use_cutover_baseline=True,
    )

    # Postgres already lists both owners, so the import adds nothing at all.
    assert result["reason"] == "owner_names_match"
    assert sorted(result["current_owner_names"]) == [
        "Graciela Pezaroylo Topal",
        "Marcelo Ramos da Silva",
    ]


@pytest.mark.asyncio
async def test_served_only_owner_is_never_proposed_for_removal(monkeypatch):
    """An owner Postgres knows about but Mongo has not linked has no Mongo user_id.
    It must count toward the baseline yet never appear in suggested_remove_owner_ids."""
    import services.ownership_transfer_detection_service as svc

    db = _mock_db()
    _with_canonical_owners(db, [("marcelo", "Marcelo Ramos da Silva")])
    db.memberships.find.return_value = _cursor([])

    async def fake_cutover_names(_building_id, _unit_number):
        return ["Marcelo Ramos da Silva", "Graciela Pezaroylo Topal"]

    monkeypatch.setattr(svc, "_cutover_owner_names", fake_cutover_names)

    result = await detect_and_create_portal_owner_transfer(
        db, "13195", "UA046", "Brand New Buyer", use_cutover_baseline=True
    )

    assert result["created"] is True
    transfer = db.owner_transfer_requests.insert_one.call_args[0][0]
    assert [owner["full_name"] for owner in transfer["old_owners"]] == [
        "Marcelo Ramos da Silva",
        "Graciela Pezaroylo Topal",
    ]
    # Only the owner with a real Mongo link can be removed from one.
    assert transfer["suggested_remove_owner_ids"] == ["marcelo"]


@pytest.mark.asyncio
async def test_mongo_baseline_is_kept_when_the_serving_store_is_mongo(monkeypatch):
    """Directional fallback: a building that is NOT promoted keeps its Mongo baseline."""
    import services.ownership_transfer_detection_service as svc

    db = _mock_db()
    _with_canonical_owners(db, [("ann", "Ann Brooks")])

    async def no_pg(_building_id, _unit_number):
        return None

    monkeypatch.setattr(svc, "_cutover_owner_names", no_pg)

    result = await detect_and_create_portal_owner_transfer(
        db, "UP-DEMO-001", "TH010", "Ann Brooks & Ben Brooks",
        dry_run=True, use_cutover_baseline=True,
    )

    assert result["reason"] == CO_OWNER_ADDITION_REASON
    assert result["current_owner_names"] == ["Ann Brooks"]


@pytest.mark.asyncio
async def test_a_failing_serving_store_read_falls_back_to_mongo(monkeypatch):
    """A baseline lookup must never be able to break detection."""
    import services.ownership_transfer_detection_service as svc

    db = _mock_db()
    _with_canonical_owners(db, [("ann", "Ann Brooks")])

    async def boom(_building_id, _unit_number):
        raise RuntimeError("postgres unreachable")

    monkeypatch.setattr(svc, "get_owner_info", boom, raising=False)

    async def failing_owner_info(_unit_number, _building_id):
        raise RuntimeError("postgres unreachable")

    import sys
    import types

    stub = types.ModuleType("services.owner_service")
    stub.get_owner_info = failing_owner_info
    monkeypatch.setitem(sys.modules, "services.owner_service", stub)

    result = await detect_and_create_portal_owner_transfer(
        db, "13195", "TH010", "Ann Brooks & Ben Brooks",
        dry_run=True, use_cutover_baseline=True,
    )

    assert result["reason"] == CO_OWNER_ADDITION_REASON
    assert result["current_owner_names"] == ["Ann Brooks"]


def test_every_production_call_site_uses_the_serving_store_baseline():
    """Footgun #9b: a gate must be applied at EVERY path that can reach it, not just
    the obvious one. These are the call sites that run against a live building."""
    import re

    call_sites = [
        Path("backend/routers/strata_sync.py"),
        Path("backend/scripts/run_scraper.py"),
        Path("backend/seeds/migrate_strata_sync_to_financial.py"),
        Path(
            "backend/scripts/data_repair/"
            "create_owner_transfer_requests_from_imported_owner_drift.py"
        ),
    ]
    repo_root = Path(__file__).resolve().parents[2]
    for relative in call_sites:
        source = (repo_root / relative).read_text()
        calls = re.findall(
            r"await detect_and_create_portal_owner_transfer\((.*?)\n\s*\)",
            source,
            re.DOTALL,
        )
        assert calls, f"{relative}: expected a detector call site"
        for call in calls:
            assert "use_cutover_baseline=True" in call, (
                f"{relative} calls the drift detector without "
                "use_cutover_baseline=True, so it would measure drift against "
                "MongoDB even for a building whose owner reads are served by Postgres."
            )


@pytest.mark.asyncio
async def test_an_archived_provisional_account_is_never_adopted():
    """Archived accounts were retired deliberately — typically as duplicate artefacts of
    a withdrawn request. Reviving one would leave a live owner link on a row still
    flagged is_archived."""
    db = _mock_db()
    db.user_units.find.return_value = _cursor([{"user_id": "kaushal"}])
    db.users.find.return_value = _cursor([{"id": "kaushal", "full_name": "Kaushal Shah"}])
    db.users.update_one = AsyncMock()
    db.users.find_one = AsyncMock(return_value=None)
    db.user_units.find_one = AsyncMock(return_value=None)

    result = await link_missing_co_owners(
        db, "13195", "TH073",
        ["Kaushal Shah", "Radhika Shah"], [None, "radhishah1110@example.com"],
        detected_at="2026-08-20T00:00:00+00:00",
    )

    assert result["linked"] is True
    # Both lookups must exclude archived rows outright.
    for call in db.users.find_one.call_args_list:
        assert call[0][0].get("status") == {"$ne": "archived"}
    db.users.update_one.assert_not_awaited()
    assert db.users.insert_one.call_args[0][0]["full_name"] == "Radhika Shah"
