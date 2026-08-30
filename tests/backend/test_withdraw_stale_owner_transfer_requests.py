# Tests for withdraw_stale_owner_transfer_requests_20260820.py.
#
# A drift request is a snapshot of a disagreement taken at one moment. When the
# underlying data is later corrected the request outlives the disagreement it describes.
# East Gate UA042: strata_owners.owner held the junk string "Test Owner" (written into
# production by a leaking test — see test_no_production_writes_from_cascade.py), so the
# detector raised a transfer to the person who already owned the unit. Correcting the
# field removed the drift but not the request.
#
# Staleness is decided by re-running the detector's OWN comparison, never by a copy of
# the rule. These tests pin that, and pin what the script refuses to touch.
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "scripts"
    / "data_repair"
    / "withdraw_stale_owner_transfer_requests_20260820.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "withdraw_stale_owner_transfer_requests_20260820", _SCRIPT_PATH
)
script = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(script)


def _cursor(rows):
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


def _request(**overrides):
    """A detector-raised, untouched, pending request."""
    return {
        "id": "req-UA042",
        "building_id": "13195",
        "unit_number": "UA042",
        "status": "pending",
        "source": "external_ledger_owner_name_drift",
        "submitted_by_role": "system",
        "portal_detected_signature": "UA042|test owner|=>|ms sarah marrapodi",
        "current_approvals": 0,
        "approval_history": [],
        "old_owners": [{"full_name": "Test Owner"}],
        "new_owner": {"user_id": "a7d54f25", "full_name": "Ms Sarah Marrapodi"},
        **overrides,
    }


def _install(monkeypatch, *, requests, strata_owner, verdict):
    raw = {}
    raw["owner_transfer_requests"] = MagicMock()
    raw["owner_transfer_requests"].find = MagicMock(return_value=_cursor(requests))
    raw["owner_transfer_requests"].update_one = AsyncMock()
    raw["strata_owners"] = MagicMock()
    raw["strata_owners"].find_one = AsyncMock(return_value=strata_owner)
    raw["users"] = MagicMock()
    raw["users"].find_one = AsyncMock(return_value=None)
    raw["users"].update_one = AsyncMock()
    raw["user_units"] = MagicMock()
    raw["user_units"].count_documents = AsyncMock(return_value=0)
    raw["memberships"] = MagicMock()
    raw["memberships"].count_documents = AsyncMock(return_value=0)

    mock_db = MagicMock()
    mock_db._db = raw
    monkeypatch.setattr(script, "db", mock_db)

    detector = AsyncMock(return_value=verdict)
    monkeypatch.setattr(script, "detect_and_create_portal_owner_transfer", detector)
    return raw, detector


_MATCHES = {"created": False, "reason": "owner_names_match", "current_owner_names": ["Ms Sarah Marrapodi"]}
_STILL_DRIFTED = {"created": False, "would_create": True, "new_owner_name": "Sonja Zink"}
_OWNER_ROW = {"unit_number": "UA042", "owner": "Ms Sarah Marrapodi", "owner_name": "Ms Sarah Marrapodi"}


@pytest.mark.asyncio
async def test_withdraws_a_request_whose_drift_no_longer_exists(monkeypatch):
    raw, detector = _install(
        monkeypatch, requests=[_request()], strata_owner=_OWNER_ROW, verdict=_MATCHES
    )

    result = await script.run("13195", apply=True)

    assert result["withdrawn_count"] == 1
    assert result["withdrawn"][0]["recheck_verdict"] == "owner_names_match"

    update = raw["owner_transfer_requests"].update_one.call_args[0][1]
    # Withdrawn, not rejected, and not deleted: the row and its detection payload stay.
    assert update["$set"]["status"] == "withdrawn"
    assert update["$set"]["action_taken"] == script.WITHDRAWN_ACTION
    assert "$unset" not in update
    raw["owner_transfer_requests"].delete_one.assert_not_called()

    # Staleness came from the detector, re-run in dry-run against the serving store.
    assert detector.await_args.kwargs["dry_run"] is True
    assert detector.await_args.kwargs["use_cutover_baseline"] is True


@pytest.mark.asyncio
async def test_keeps_a_request_whose_drift_is_still_present(monkeypatch):
    """UA029: a real pending sale must survive untouched."""
    raw, _ = _install(
        monkeypatch,
        requests=[_request(id="req-UA029", unit_number="UA029")],
        strata_owner={"unit_number": "UA029", "owner": "Sonja Zink"},
        verdict=_STILL_DRIFTED,
    )

    result = await script.run("13195", apply=True)

    assert result["withdrawn_count"] == 0
    assert result["kept"][0]["reason"] == "drift_still_present"
    raw["owner_transfer_requests"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_pending_request_exists_verdict_is_not_treated_as_stale(monkeypatch):
    """The re-run dedups against the very request being examined; only an explicit
    owner_names_match means the drift is gone."""
    raw, _ = _install(
        monkeypatch,
        requests=[_request()],
        strata_owner=_OWNER_ROW,
        verdict={"created": False, "reason": "pending_request_exists", "id": "req-UA042"},
    )

    result = await script.run("13195", apply=True)

    assert result["withdrawn_count"] == 0
    assert result["kept"][0]["recheck_verdict"] == "pending_request_exists"
    raw["owner_transfer_requests"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_co_owner_addition_verdict_is_not_treated_as_stale(monkeypatch):
    raw, _ = _install(
        monkeypatch,
        requests=[_request()],
        strata_owner=_OWNER_ROW,
        verdict={"created": False, "reason": "co_owner_addition_not_a_transfer"},
    )

    result = await script.run("13195", apply=True)

    assert result["withdrawn_count"] == 0
    raw["owner_transfer_requests"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_never_withdraws_a_human_lodged_request(monkeypatch):
    """An owner's own lodged sale is never auto-withdrawn because an import lags."""
    raw, detector = _install(
        monkeypatch,
        requests=[_request(submitted_by_role="owner", portal_detected_signature=None)],
        strata_owner=_OWNER_ROW,
        verdict=_MATCHES,
    )

    result = await script.run("13195", apply=True)

    assert result["kept"][0]["reason"] == "not_a_detector_raised_request"
    detector.assert_not_awaited()
    raw["owner_transfer_requests"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_never_withdraws_a_request_an_approver_has_acted_on(monkeypatch):
    raw, detector = _install(
        monkeypatch,
        requests=[_request(
            current_approvals=1,
            approval_history=[{"action": "approve_remove_old", "user_id": "ec-1"}],
        )],
        strata_owner=_OWNER_ROW,
        verdict=_MATCHES,
    )

    result = await script.run("13195", apply=True)

    assert result["kept"][0]["reason"] == "already_under_review"
    detector.assert_not_awaited()
    raw["owner_transfer_requests"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_there_is_no_current_imported_snapshot(monkeypatch):
    """With nothing to compare against, staleness is unknowable."""
    raw, detector = _install(
        monkeypatch, requests=[_request()], strata_owner=None, verdict=_MATCHES
    )

    result = await script.run("13195", apply=True)

    assert result["kept"][0]["reason"] == "no_current_imported_owner_snapshot"
    detector.assert_not_awaited()
    raw["owner_transfer_requests"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_archives_the_provisional_account_the_request_minted(monkeypatch):
    raw, _ = _install(
        monkeypatch, requests=[_request()], strata_owner=_OWNER_ROW, verdict=_MATCHES
    )
    raw["users"].find_one = AsyncMock(return_value={
        "id": "a7d54f25",
        "full_name": "Ms Sarah Marrapodi",
        "portal_detected_owner": True,
        "is_active": False,
        "status": "pending_owner_transfer",
    })

    result = await script.run("13195", apply=True)

    assert result["stray_provisional_accounts"][0]["archived"] is True
    archived = raw["users"].update_one.call_args[0][1]["$set"]
    assert archived["status"] == "archived"
    assert archived["is_archived"] is True
    raw["users"].delete_one.assert_not_called()


@pytest.mark.asyncio
async def test_does_not_archive_a_minted_account_that_is_in_use(monkeypatch):
    raw, _ = _install(
        monkeypatch, requests=[_request()], strata_owner=_OWNER_ROW, verdict=_MATCHES
    )
    raw["users"].find_one = AsyncMock(return_value={
        "id": "a7d54f25", "full_name": "Ms Sarah Marrapodi",
        "portal_detected_owner": True, "is_active": False,
    })
    raw["user_units"].count_documents = AsyncMock(return_value=1)

    result = await script.run("13195", apply=True)

    assert result["stray_provisional_accounts"][0]["reason"] == "account_is_in_use"
    raw["users"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(monkeypatch):
    raw, _ = _install(
        monkeypatch, requests=[_request()], strata_owner=_OWNER_ROW, verdict=_MATCHES
    )
    raw["users"].find_one = AsyncMock(return_value={
        "id": "a7d54f25", "full_name": "Ms Sarah Marrapodi",
        "portal_detected_owner": True, "is_active": False,
    })

    result = await script.run("13195", apply=False)

    assert result["withdrawn_count"] == 1
    assert result["stray_provisional_accounts"][0]["would_archive"] is True
    raw["owner_transfer_requests"].update_one.assert_not_awaited()
    raw["users"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_reads_the_combined_owner_field_the_detector_reads(monkeypatch):
    """The detector prefers strata_owners.owner over the split fields; the recheck must
    feed it the same string or it would be comparing something else."""
    _, detector = _install(
        monkeypatch,
        requests=[_request()],
        strata_owner={"owner": "A & B", "owner_name": "A", "owner_name_b": "Different"},
        verdict=_MATCHES,
    )

    await script.run("13195", apply=False)

    assert detector.await_args.args[3] == "A & B"


def test_falls_back_to_the_split_fields_when_the_combined_field_is_absent():
    assert script._imported_owner_names({"owner_name": "A", "owner_name_b": "B"}) == "A & B"
    assert script._imported_owner_names({"owner": "A & B"}) == "A & B"
    assert script._imported_owner_names({}) == ""


@pytest.mark.asyncio
async def test_is_scoped_to_building_id(monkeypatch):
    raw, detector = _install(
        monkeypatch, requests=[_request()], strata_owner=_OWNER_ROW, verdict=_MATCHES
    )

    await script.run("UP-DEMO-001", apply=True)

    assert raw["owner_transfer_requests"].find.call_args[0][0]["building_id"] == "UP-DEMO-001"
    assert raw["strata_owners"].find_one.call_args[0][0]["building_id"] == "UP-DEMO-001"
    assert detector.await_args.args[1] == "UP-DEMO-001"
