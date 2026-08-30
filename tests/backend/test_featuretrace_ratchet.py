"""The FeatureTrace ratchet must fire, in both directions.

# @featuretrace:featuretrace-ratchet — baseline gate for marker quality (global).
# Layer: test
# Data flow: audit_featuretrace.run_ratchet() <- live markers + committed baseline.
# Related: scripts/validation/audit_featuretrace.py
#          scripts/validation/featuretrace_baseline.json
#          docs/architecture/featuretrace_visibility_standard.md

The standard has outlived its enforcement: 478 marker issues across 293 files predate
this gate, 299 of them a missing scope qualifier. `--strict` would fail every PR on
debt its author did not create, so the gate ratchets instead — it asks only "did THIS
change make it worse?".

A gate nobody has watched fail is a gate nobody should trust, so the tests below drive
it into both failure modes rather than only its happy path.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts" / "validation" / "audit_featuretrace.py"
BASELINE = ROOT / "scripts" / "validation" / "featuretrace_baseline.json"


def _load():
    spec = importlib.util.spec_from_file_location("audit_featuretrace", AUDIT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register BEFORE exec: the module defines a @dataclass under
    # `from __future__ import annotations`, and dataclasses resolve their string
    # annotations via sys.modules[cls.__module__].__dict__. Without this the lookup
    # returns None and every test using the module errors with
    # "'NoneType' object has no attribute '__dict__'".
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def audit():
    return _load()


class _FakeMarker:
    """Minimal stand-in: run_ratchet only reads `.path` and `.issues`."""

    def __init__(self, path: str, issues: list[str]):
        self.path = ROOT / path
        self.issues = issues


@pytest.fixture
def isolated_baseline(audit, tmp_path, monkeypatch):
    """Point the module at a scratch baseline so tests never touch the real one."""
    monkeypatch.setattr(audit, "BASELINE_PATH", tmp_path / "baseline.json")
    return audit


# ─── The committed baseline ───────────────────────────────────────────────────

def test_committed_baseline_exists_and_is_shaped_right() -> None:
    assert BASELINE.exists(), (
        "scripts/validation/featuretrace_baseline.json is missing. Create it with:\n"
        "  python3 scripts/validation/audit_featuretrace.py --update-baseline"
    )
    data = json.loads(BASELINE.read_text())
    assert data["files"], "an empty baseline would licence every finding"
    assert data["total_issues"] == sum(data["files"].values())
    assert all(isinstance(v, int) and v > 0 for v in data["files"].values()), (
        "a zero entry is debt already paid — drop the file from the baseline"
    )


def test_baseline_matches_the_tree(audit) -> None:
    """A baseline above the real number silently re-licenses findings.

    That is exactly how the design-token baseline went stale on main and had to be
    repaired, which is why an un-baselined improvement fails too.
    """
    assert audit.run_ratchet(_live_markers(audit), update=False) == 0


def _live_markers(audit):
    markers = []
    for path in audit.iter_files():
        markers.extend(audit.markers_for(path, audit.read_text(path)))
    return markers


# ─── Failure mode 1: it got worse ─────────────────────────────────────────────

def test_a_new_file_with_an_incomplete_marker_fails(isolated_baseline) -> None:
    """New markers must be complete — the backlog is not a licence to add to it."""
    audit = isolated_baseline
    assert audit.run_ratchet([_FakeMarker("a.py", ["missing Layer"])], update=True) == 0
    assert audit.run_ratchet(
        [_FakeMarker("a.py", ["missing Layer"]), _FakeMarker("brand_new.py", ["missing Related"])],
        update=False,
    ) == 1


def test_an_existing_file_getting_worse_fails(isolated_baseline) -> None:
    audit = isolated_baseline
    assert audit.run_ratchet([_FakeMarker("a.py", ["missing Layer"])], update=True) == 0
    assert audit.run_ratchet(
        [_FakeMarker("a.py", ["missing Layer", "missing Related"])], update=False,
    ) == 1


def test_swapping_one_issue_for_another_is_not_a_regression(isolated_baseline) -> None:
    """Counted per ISSUE, so a like-for-like swap is neutral — as it should be.

    (Counting per MARKER would also call this neutral, but would then miss a marker
    that gained a second issue, which is why issues are the unit.)
    """
    audit = isolated_baseline
    assert audit.run_ratchet([_FakeMarker("a.py", ["missing Layer"])], update=True) == 0
    assert audit.run_ratchet([_FakeMarker("a.py", ["missing Related"])], update=False) == 0


# ─── Failure mode 2: it got better and nobody locked it in ────────────────────

def test_an_unbaselined_improvement_also_fails(isolated_baseline) -> None:
    """Deliberately the same behaviour as the design-token ratchet.

    A baseline that drifts above the real number quietly re-licenses every finding it
    was meant to hold. Making the win fail loudly is what keeps it.
    """
    audit = isolated_baseline
    assert audit.run_ratchet([_FakeMarker("a.py", ["x", "y"])], update=True) == 0
    assert audit.run_ratchet([_FakeMarker("a.py", ["x"])], update=False) == 1
    # ...and updating clears it.
    assert audit.run_ratchet([_FakeMarker("a.py", ["x"])], update=True) == 0
    assert audit.run_ratchet([_FakeMarker("a.py", ["x"])], update=False) == 0


def test_a_fully_fixed_file_leaves_the_baseline(isolated_baseline) -> None:
    audit = isolated_baseline
    assert audit.run_ratchet([_FakeMarker("a.py", ["x"])], update=True) == 0
    assert audit.run_ratchet([], update=False) == 1          # improvement, not yet locked
    assert audit.run_ratchet([], update=True) == 0
    assert json.loads((audit.BASELINE_PATH).read_text())["files"] == {}


# ─── Guard rails ──────────────────────────────────────────────────────────────

def test_ratchet_refuses_to_run_against_a_single_tag(audit, capsys) -> None:
    """With `tag` set the audit holds one feature's markers.

    Every other file would read as zero and `--update-baseline` would rewrite the
    whole baseline as one enormous improvement — erasing the debt record instead of
    paying it down.
    """
    assert audit.main(["function-lens", "--check"]) == 2
    assert "whole repository" in capsys.readouterr().out
