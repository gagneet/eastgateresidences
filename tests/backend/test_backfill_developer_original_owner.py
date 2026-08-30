from unittest.mock import AsyncMock, MagicMock
import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "scripts"
    / "data_repair"
    / "backfill_developer_original_owner_20260819.py"
)
_SPEC = importlib.util.spec_from_file_location("backfill_developer_original_owner_20260819", _SCRIPT_PATH)
backfill_script = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(backfill_script)


def _cursor(rows):
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


class _RawCollection:
    """Minimal raw-collection stand-in matching db._db["name"] usage in the script."""

    def __init__(self, find_one_side_effect=None, find_rows=None):
        self.find_one = AsyncMock(side_effect=find_one_side_effect) if find_one_side_effect else AsyncMock(return_value=None)
        self.insert_one = AsyncMock()
        self.update_one = AsyncMock()
        self._find_rows = find_rows or []

    def find(self, *_args, **_kwargs):
        return _cursor(self._find_rows)


def _mock_db(units, developer_lookup_result=None, current_owner_rows=None, bootstrap_audit_result=None):
    raw = {
        "units": _RawCollection(find_rows=units),
        "users": _RawCollection(find_one_side_effect=[developer_lookup_result]),
        "user_units": _RawCollection(
            find_one_side_effect=lambda *a, **k: None,  # overridden per-test below when needed
            find_rows=current_owner_rows or [],
        ),
        "owner_transfer_requests": _RawCollection(
            find_one_side_effect=[bootstrap_audit_result] if bootstrap_audit_result is not None else None,
        ),
    }
    db = MagicMock()
    db._db = raw
    return db, raw


@pytest.mark.asyncio
async def test_dry_run_on_fresh_building_reports_would_create_for_all_units(monkeypatch):
    units = [{"unit_number": "UA070"}, {"unit_number": "TH087"}]
    db, raw = _mock_db(units, developer_lookup_result=None)
    # user_units.find_one (developer-row existence check) -> None since developer_id is None
    raw["user_units"].find_one = AsyncMock(return_value=None)
    monkeypatch.setattr(backfill_script, "db", db)

    result = await backfill_script.run("13195", False)

    assert result["apply"] is False
    assert result["developer_id"] is None
    assert result["developer_rows_created"] == 2
    assert result["developer_rows_skipped"] == 0
    raw["users"].insert_one.assert_not_called()
    raw["user_units"].insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_dry_run_after_apply_correctly_reports_zero_created(monkeypatch):
    """Regression test for the audit-found bug: dry-run used to always report
    'would create N' even when the developer rows already existed, because the
    existence check was gated behind `apply`. It must do a read-only lookup in
    dry-run too."""
    units = [{"unit_number": "UA070"}]
    db, raw = _mock_db(units, developer_lookup_result={"id": "dev-1"})
    raw["user_units"].find_one = AsyncMock(return_value={"id": "existing-dev-row"})
    monkeypatch.setattr(backfill_script, "db", db)

    result = await backfill_script.run("13195", False)

    assert result["developer_id"] == "dev-1"
    assert result["developer_rows_created"] == 0
    assert result["developer_rows_skipped"] == 1
    raw["user_units"].insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_apply_creates_developer_once_and_reuses_across_units(monkeypatch):
    units = [{"unit_number": "UA070"}, {"unit_number": "TH087"}]
    db, raw = _mock_db(units)
    raw["users"].find_one = AsyncMock(return_value=None)  # no existing developer -> create
    raw["user_units"].find_one = AsyncMock(return_value=None)  # no existing dev row for either unit
    monkeypatch.setattr(backfill_script, "db", db)

    result = await backfill_script.run("13195", True)

    assert result["developer_rows_created"] == 2
    raw["users"].insert_one.assert_called_once()  # ONE shared developer account, not one per unit
    assert raw["user_units"].insert_one.call_count == 2

    inserted = [c.args[0] for c in raw["user_units"].insert_one.call_args_list]
    th087 = next(d for d in inserted if d["unit_number"] == "TH087")
    ua070 = next(d for d in inserted if d["unit_number"] == "UA070")
    assert th087["actual_end_date"] == "2020-12-16"  # TH087 override
    assert ua070["actual_end_date"] == "2020-12-01"  # default
    assert th087["is_active"] is False
    assert th087["start_date"] is None


@pytest.mark.asyncio
async def test_current_owner_start_date_corrected_to_settlement_date(monkeypatch):
    units = [{"unit_number": "UA070"}]
    current_owner_rows = [{"id": "link-1", "user_id": "owner-1"}]
    db, raw = _mock_db(units, current_owner_rows=current_owner_rows)
    raw["users"].find_one = AsyncMock(return_value={"id": "dev-1"})
    raw["user_units"].find_one = AsyncMock(return_value={"id": "existing"})  # dev row already exists
    monkeypatch.setattr(backfill_script, "db", db)

    result = await backfill_script.run("13195", True)

    assert result["start_dates_fixed"] == 1
    update_call = raw["user_units"].update_one.call_args
    assert update_call.args[0] == {"id": "link-1", "building_id": "13195"}
    assert update_call.args[1]["$set"]["start_date"] == "2020-12-01"


@pytest.mark.asyncio
async def test_bootstrap_audit_settlement_date_corrected_when_stale(monkeypatch):
    units = [{"unit_number": "UA070"}]
    db, raw = _mock_db(
        units,
        bootstrap_audit_result={"id": "audit-1", "settlement_date": "2026-08-19"},
    )
    raw["users"].find_one = AsyncMock(return_value={"id": "dev-1"})
    raw["user_units"].find_one = AsyncMock(return_value={"id": "existing"})
    monkeypatch.setattr(backfill_script, "db", db)

    result = await backfill_script.run("13195", True)

    assert result["audit_settlement_dates_fixed"] == 1
    update_call = raw["owner_transfer_requests"].update_one.call_args
    assert update_call.args[0] == {"id": "audit-1", "building_id": "13195"}
    assert update_call.args[1]["$set"]["settlement_date"] == "2020-12-01"


@pytest.mark.asyncio
async def test_bootstrap_audit_left_alone_when_already_correct(monkeypatch):
    units = [{"unit_number": "UA070"}]
    db, raw = _mock_db(
        units,
        bootstrap_audit_result={"id": "audit-1", "settlement_date": "2020-12-01"},
    )
    raw["users"].find_one = AsyncMock(return_value={"id": "dev-1"})
    raw["user_units"].find_one = AsyncMock(return_value={"id": "existing"})
    monkeypatch.setattr(backfill_script, "db", db)

    result = await backfill_script.run("13195", True)

    assert result["audit_settlement_dates_fixed"] == 0
    raw["owner_transfer_requests"].update_one.assert_not_called()
