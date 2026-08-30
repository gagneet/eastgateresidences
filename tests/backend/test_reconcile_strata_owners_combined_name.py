# Tests for reconcile_strata_owners_combined_name_20260820.py.
#
# strata_owners stores the imported owner snapshot twice — the combined `owner` string
# and the split owner_name / owner_name_b. The drift detector reads the COMBINED field
# in preference, so a junk value there is what owner-change detection gets measured
# against. East Gate UA042 held the literal string "Test Owner" in `owner` while every
# other field and both datastores said "Ms Sarah Marrapodi".
#
# The repair only trusts the split fields once units.* independently corroborates them;
# these tests focus on that corroboration and on the cases it must refuse.
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "scripts"
    / "data_repair"
    / "reconcile_strata_owners_combined_name_20260820.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "reconcile_strata_owners_combined_name_20260820", _SCRIPT_PATH
)
script = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(script)


def _cursor(rows):
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


def _install_db(monkeypatch, *, strata_owners, unit):
    raw = {}
    raw["strata_owners"] = MagicMock()
    raw["strata_owners"].find = MagicMock(return_value=_cursor(strata_owners))
    raw["strata_owners"].update_one = AsyncMock()
    raw["units"] = MagicMock()
    raw["units"].find_one = AsyncMock(return_value=unit)

    mock_db = MagicMock()
    mock_db._db = raw
    monkeypatch.setattr(script, "db", mock_db)
    return raw


UA042 = {
    "unit_number": "UA042",
    "owner": "Test Owner",
    "owner_name": "Ms Sarah Marrapodi",
    "owner_name_b": None,
}


@pytest.mark.asyncio
async def test_corrects_the_combined_name_when_units_corroborates(monkeypatch):
    raw = _install_db(
        monkeypatch,
        strata_owners=[UA042],
        unit={"owner_name": "Ms Sarah Marrapodi", "owner_name_b": None},
    )

    result = await script.run("13195", apply=True)

    entry = result["corrected"][0]
    assert entry["corrected"] is True
    assert entry["new_combined_owner"] == "Ms Sarah Marrapodi"
    assert entry["corroborated_by"] == "units.owner_name/owner_name_b"

    update = raw["strata_owners"].update_one.call_args[0][1]["$set"]
    assert update["owner"] == "Ms Sarah Marrapodi"
    # A correction with a trail, never a silent overwrite.
    assert update["owner_corrected_from"] == "Test Owner"
    assert raw["strata_owners"].update_one.call_args[0][0]["building_id"] == "13195"
    assert raw["strata_owners"].update_one.call_args[0][0]["unit_number"] == "UA042"


@pytest.mark.asyncio
async def test_rejoins_two_owners_with_the_standard_separator(monkeypatch):
    raw = _install_db(
        monkeypatch,
        strata_owners=[{
            "unit_number": "UA046",
            "owner": "Marcelo Ramos da Silva",
            "owner_name": "Marcelo Ramos da Silva",
            "owner_name_b": "Graciela Pezaroylo Topal",
        }],
        unit={
            "owner_name": "Marcelo Ramos da Silva",
            "owner_name_b": "Graciela Pezaroylo Topal",
        },
    )

    await script.run("13195", apply=True)

    assert raw["strata_owners"].update_one.call_args[0][1]["$set"]["owner"] == (
        "Marcelo Ramos da Silva & Graciela Pezaroylo Topal"
    )


@pytest.mark.asyncio
async def test_consistent_rows_are_left_alone(monkeypatch):
    """"A and B" vs owner_name/owner_name_b is the same set — not a disagreement."""
    raw = _install_db(
        monkeypatch,
        strata_owners=[{
            "unit_number": "UA038",
            "owner": "Alyx Ashley Ford and Isabella Celeste Lomax",
            "owner_name": "Alyx Ashley Ford",
            "owner_name_b": "Isabella Celeste Lomax",
        }],
        unit={"owner_name": "Alyx Ashley Ford", "owner_name_b": "Isabella Celeste Lomax"},
    )

    result = await script.run("13195", apply=True)

    assert result["corrected"] == []
    raw["strata_owners"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_refuses_when_units_disagrees_with_the_split_fields(monkeypatch):
    """Two sources conflict — picking one would be a guess dressed up as a repair."""
    raw = _install_db(
        monkeypatch,
        strata_owners=[UA042],
        unit={"owner_name": "Someone Else Entirely", "owner_name_b": None},
    )

    result = await script.run("13195", apply=True)

    assert result["corrected"] == []
    entry = result["skipped_needs_manual_review"][0]
    assert entry["reason"] == "unit_owner_names_disagree_with_split_fields"
    assert entry["unit_owner_names"] == ["Someone Else Entirely"]
    raw["strata_owners"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_refuses_when_there_is_nothing_to_corroborate_against(monkeypatch):
    raw = _install_db(monkeypatch, strata_owners=[UA042], unit=None)

    result = await script.run("13195", apply=True)

    assert result["corrected"] == []
    assert (
        result["skipped_needs_manual_review"][0]["reason"]
        == "no_corroborating_unit_owner_name"
    )
    raw["strata_owners"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_rows_without_split_fields_are_ignored(monkeypatch):
    """Nothing to reconcile against — this repair has no opinion on those rows."""
    raw = _install_db(
        monkeypatch,
        strata_owners=[{"unit_number": "UA099", "owner": "Test Owner"}],
        unit={"owner_name": "Real Person"},
    )

    result = await script.run("13195", apply=True)

    assert result["corrected"] == []
    assert result["skipped_needs_manual_review"] == []
    raw["strata_owners"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(monkeypatch):
    raw = _install_db(
        monkeypatch,
        strata_owners=[UA042],
        unit={"owner_name": "Ms Sarah Marrapodi", "owner_name_b": None},
    )

    result = await script.run("13195", apply=False)

    assert result["corrected"][0]["would_correct"] is True
    raw["strata_owners"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_is_idempotent(monkeypatch):
    """After correction the combined and split forms agree, so a re-run is a no-op."""
    raw = _install_db(
        monkeypatch,
        strata_owners=[{**UA042, "owner": "Ms Sarah Marrapodi"}],
        unit={"owner_name": "Ms Sarah Marrapodi", "owner_name_b": None},
    )

    result = await script.run("13195", apply=True)

    assert result["corrected"] == []
    raw["strata_owners"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_is_scoped_to_building_id(monkeypatch):
    raw = _install_db(
        monkeypatch,
        strata_owners=[UA042],
        unit={"owner_name": "Ms Sarah Marrapodi", "owner_name_b": None},
    )

    await script.run("UP-DEMO-001", apply=True)

    assert raw["strata_owners"].find.call_args[0][0]["building_id"] == "UP-DEMO-001"
    assert raw["units"].find_one.call_args[0][0]["building_id"] == "UP-DEMO-001"
    assert raw["strata_owners"].update_one.call_args[0][0]["building_id"] == "UP-DEMO-001"


@pytest.mark.asyncio
async def test_unit_filter_limits_scope(monkeypatch):
    raw = _install_db(
        monkeypatch,
        strata_owners=[UA042],
        unit={"owner_name": "Ms Sarah Marrapodi", "owner_name_b": None},
    )

    await script.run("13195", apply=False, unit_number="UA042")

    assert raw["strata_owners"].find.call_args[0][0]["unit_number"] == "UA042"
