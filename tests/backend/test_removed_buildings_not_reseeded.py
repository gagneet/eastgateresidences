"""A removed building must stay removed.

Harbourview (18932) was hard-deleted on 2026-08-20 (3,558 documents). Deleting the rows
is only half the job: `seed_all()` used to call `seed_harbourview()` and
`seed_demo_enrichment()` re-created its levies, ledger, announcements, meetings, events
and maintenance. Running the seed would have quietly resurrected it, and nothing would
have failed to say so.

Sierra (16244) was deliberately KEPT, so these tests must not push toward removing it.
"""

from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
SEEDS = BACKEND / "seeds"

REMOVED_BUILDING = "18932"
RETAINED_BUILDING = "16244"

# Modules seed_all() actually imports and runs. Files not reachable from it (the
# snapshot_* dumps, insurance.py, trust_accounting.py) are restore-only artifacts that
# nothing imports, so they cannot resurrect anything on their own.
WIRED_SEEDS = [
    "buildings.py",
    "seed_asset_templates.py",
    "seed_building_summaries.py",
    "seed_demo_building.py",
    "seed_demo_enrichment.py",
    "seed_demo_finance.py",
    "seed_demo_intelligence_dataset.py",
    "seed_demo_workorders.py",
    "seed_mega_complex.py",
    "seed_sierra.py",
]


def test_seed_all_does_not_import_the_harbourview_seed():
    src = (SEEDS / "seed_all.py").read_text()
    assert "seed_harbourview" not in src.replace("# ", "").split("Harbourview (18932) removed")[0], (
        "seed_all still wires seed_harbourview(); running the seed would recreate the building"
    )


def test_harbourview_seed_module_is_gone():
    assert not (SEEDS / "seed_harbourview.py").exists(), (
        "seeds/seed_harbourview.py still exists and can recreate the removed building"
    )


@pytest.mark.parametrize("module", WIRED_SEEDS)
def test_no_wired_seed_writes_the_removed_building(module):
    """Reachability, not mere mention.

    seed_demo_enrichment.py still defines HARBOUR = "18932" as inert data; what matters is
    that nothing CALLS the seeding routine with it.
    """
    path = SEEDS / module
    if not path.exists():
        pytest.skip(f"{module} not present")
    code = [
        ln for ln in path.read_text().splitlines()
        if not ln.lstrip().startswith("#")
    ]
    body = "\n".join(code)
    assert "_seed_building(\n        HARBOUR" not in body and "_seed_building(HARBOUR" not in body, (
        f"{module} still seeds the removed building"
    )
    # A bare building dict keyed to the removed id would recreate it via upsert.
    assert f'"id": "{REMOVED_BUILDING}"' not in body, (
        f"{module} still defines a buildings row for {REMOVED_BUILDING}"
    )
    assert f'"building_id": "{REMOVED_BUILDING}"' not in body or "HARBOUR" in body, (
        f"{module} still writes documents scoped to {REMOVED_BUILDING}"
    )


def test_sierra_is_still_seeded():
    """Guard against over-correction: Sierra was explicitly retained."""
    src = (SEEDS / "seed_all.py").read_text()
    assert "seed_sierra" in src, "Sierra seeding was removed — it was meant to be kept"
    assert (SEEDS / "seed_sierra.py").exists()


def test_sierra_tests_were_not_deleted_with_harbourview():
    tests_dir = Path(__file__).resolve().parent
    assert (tests_dir / "test_building_switch_sierra.py").exists(), (
        "Sierra's own test file was deleted; only Harbourview was meant to go"
    )
    seed_tests = (tests_dir / "test_multitenant_seed_buildings.py").read_text()
    assert "class TestSierraSeedConstants" in seed_tests
    assert "class TestHarbourviewSeedConstants" not in seed_tests
