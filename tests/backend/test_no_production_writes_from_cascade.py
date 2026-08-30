# @featuretrace:owner-transfers — Guard against tests leaking writes into the live database
# via the fire-and-forget owner-change cascade.
# Layer: test
"""
`update_owner_unit` and the owner-transfer approval both fire

    asyncio.create_task(_cascade_owner_change(...))

The task is scheduled, not awaited, so it runs AFTER the caller's
`with patch("server.db", ...)` block has exited. At that point it resolves the
module-level `db` — the REAL one — and writes to `units`, `strata_owners`,
`agm_attendance`, `ec_members`, `chat_groups` and `rental_certificates` for whatever
building_id the test used.

Real incident (2026-08-20): tests/backend/test_owner_unit_balance_sync.py exercised
`update_owner_unit("UA042", ...)` for building 13195 with a fixture whose owner_name was
the string "Test Owner". Every full test run therefore wrote
`strata_owners.owner = "Test Owner"` into East Gate PRODUCTION for unit UA042. That value
made the owner-drift detector raise a transfer request from a person who never existed,
and it silently reverted the data repair that corrected it — twice — before the cause was
found. The file's own docstring claimed "No live DB required; all DB calls are mocked."

Patching `server.db` is NOT sufficient for anything that reaches a create_task. Patch
`server._cascade_owner_change` as well; it has dedicated coverage in
test_owner_change_cascade.py.
"""
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_DIR = _REPO_ROOT / "tests"

# Entry points that schedule _cascade_owner_change as a background task.
_CASCADE_ENTRY_POINTS = ("update_owner_unit", "process_owner_transfer")


def _test_files():
    return sorted(path for path in _TESTS_DIR.rglob("test_*.py") if path.name != Path(__file__).name)


def test_server_still_fires_the_cascade_as_a_background_task():
    """If this ever stops being true the guard below is obsolete, not passing."""
    server = (_REPO_ROOT / "backend" / "server.py").read_text()
    assert server.count("asyncio.create_task(_cascade_owner_change(") >= 1, (
        "The owner-change cascade is no longer fired via create_task. Re-check whether "
        "the leak this guard protects against still exists before deleting it."
    )


@pytest.mark.parametrize("path", _test_files(), ids=lambda p: p.name)
def test_cascade_callers_patch_the_cascade(path):
    """Any test invoking a cascade entry point must neutralise the background task."""
    source = path.read_text()
    calls = [
        entry
        for entry in _CASCADE_ENTRY_POINTS
        if re.search(rf"await\s+{entry}\s*\(", source)
    ]
    if not calls:
        pytest.skip("does not invoke a cascade entry point")

    # A real patch() of the cascade — a passing mention in a comment or docstring
    # must not satisfy this guard.
    patched = re.search(r"""patch\(\s*f?["'][^"']*_cascade_owner_change""", source)
    try:
        label = path.relative_to(_REPO_ROOT)
    except ValueError:  # a path outside the repo, e.g. the guard's own decoy fixture
        label = path
    assert patched, (
        f"{label} awaits {', '.join(calls)}, which schedules "
        "_cascade_owner_change via asyncio.create_task. That task outlives "
        'patch("server.db", ...) and writes to the REAL database. Add '
        'patch("server._cascade_owner_change", new=AsyncMock()) to every such call site.'
    )


def test_the_guard_rejects_a_mere_mention_of_the_cascade(tmp_path):
    """The guard checks for a real patch() call, not the words appearing in a comment."""
    decoy = tmp_path / "test_decoy.py"
    decoy.write_text(
        '"""Mentions _cascade_owner_change but never patches it."""\n'
        "async def go():\n"
        "    await update_owner_unit('UA042', updates, user, '13195')\n"
    )
    with pytest.raises(AssertionError, match="_cascade_owner_change"):
        test_cascade_callers_patch_the_cascade(decoy)
