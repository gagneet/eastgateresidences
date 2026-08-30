"""The Function Lens generator: correct, honest, and not stale.

# @featuretrace:function-lens — generator contract tests.
# Layer: test
# Data flow: generate_function_lens.build() -> assertions on the emitted index (global).
#            The lens indexes the whole repository; it has no tenant dimension.
# Related: scripts/validation/generate_function_lens.py
#          .claude/skills/function-lens/SKILL.md

The lens is a navigation tool people will trust, so the tests that matter are the
ones pinning what it must NOT overclaim: that name-based edges are marked ambiguous,
that it defers to the canonical-owner registry instead of guessing at duplication,
and that the committed artefact matches the tree.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GEN = ROOT / "scripts" / "validation" / "generate_function_lens.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("generate_function_lens", GEN)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def lens():
    return _load_module()


@pytest.fixture(scope="module")
def artefact(lens):
    """The lens built from the CURRENT tree.

    Built rather than read from disk: the full index is a 13.5 MB gitignored cache,
    so a test that read it would pass or fail on whether someone had run the
    generator locally. Freshness is a separate assertion against the committed
    digest, below.
    """
    return lens.build()


@pytest.fixture(scope="module")
def committed_digest():
    """The small artefact that IS committed and gates the PR."""
    path = ROOT / "docs" / "architecture" / "function_lens_digest.json"
    assert path.exists(), (
        "docs/architecture/function_lens_digest.json is missing. Run:\n"
        "  python3 scripts/validation/generate_function_lens.py"
    )
    return json.loads(path.read_text())


# ─── Shape ────────────────────────────────────────────────────────────────────

def test_keys_are_path_plus_qualified_name(artefact) -> None:
    """The spec asked for a stable key of `path::qualified_function_name`.

    Stability is the whole point: a tool, a doc or a review comment can reference a
    key and still resolve months later.
    """
    for key, rec in list(artefact["functions"].items())[:400]:
        assert key == f"{rec['path']}::{rec['qualname']}", key
        assert "::" in key


def test_the_index_is_not_trivially_small(artefact) -> None:
    """A silent extraction failure would leave a plausible-looking empty-ish index."""
    counts = artefact["counts"]
    assert counts["total"] > 5000, counts
    assert counts["python"] > 3000, counts
    assert counts["javascript"] > 500, counts


# ─── Honesty ──────────────────────────────────────────────────────────────────

def test_shared_names_are_flagged_ambiguous(artefact) -> None:
    """Name-based edges must never be presented as precise.

    Three functions are named `dollars_to_cents` and two of them are the documented
    duplicates. Their caller lists are unions across all three, and a reader who is
    not told that will treat a union as one function's blast radius.
    """
    shared = [r for r in artefact["functions"].values() if r["name"] == "dollars_to_cents"]
    assert len(shared) >= 2, "expected the known dollars_to_cents duplicates"
    for rec in shared:
        assert rec["edges_ambiguous"] is True
        assert rec["homonyms"] == len(shared)


def test_a_uniquely_named_function_is_not_flagged(artefact) -> None:
    """The flag must discriminate, or it is noise everyone learns to ignore."""
    rec = artefact["functions"][
        "backend/services/manager_function_service.py::resolve_manager_scope"
    ]
    assert rec["homonyms"] == 1
    assert rec["edges_ambiguous"] is False


def test_limits_are_published_with_the_data(artefact) -> None:
    """The caveats travel with the artefact, not only in a doc someone may not open."""
    joined = " ".join(artefact["limits"]).lower()
    assert "name-based" in joined
    assert "canonical_owners.yaml" in joined
    assert "alembic" in joined
    assert "tests[]" in joined


def test_test_references_are_flagged_when_the_name_is_ambiguous(artefact) -> None:
    """`main` has 196 definitions and every one listed the same 12 test files.

    The refs are name matches, so they inherit the ambiguity of the call edges. Kept
    (they are genuinely useful for a uniquely named function) but flagged, so nobody
    reads another function's coverage as this one's.
    """
    fns = artefact["functions"]
    mains = [r for r in fns.values() if r["name"] == "main"]
    assert len(mains) > 20, "expected many main() definitions"
    assert all(r["tests_ambiguous"] for r in mains)

    unique = fns["backend/services/manager_function_service.py::resolve_manager_scope"]
    assert unique["tests_ambiguous"] is False


def test_untested_count_is_named_as_an_upper_bound(artefact) -> None:
    """It counts functions no test NAMES — not uncovered code, which it cannot see."""
    assert "untested_upper_bound" in artefact["counts"]
    assert "untested" not in artefact["counts"]


def test_churn_is_excluded_from_the_digest_hash(lens, artefact) -> None:
    """Otherwise the gate fails on which flag the last person happened to pass.

    churn is populated only under --with-churn. If it fed the hash, a developer
    without the flag and a CI run with it would disagree for a reason having nothing
    to do with the code.
    """
    with_churn = {
        key: {**rec, "churn": {"commits": 99, "last_changed": "2026-01-01T00:00:00+00:00"}}
        for key, rec in artefact["functions"].items()
    }
    assert lens.digest({**artefact, "functions": with_churn})["content_sha256"] == \
        lens.digest(artefact)["content_sha256"]


def test_migrations_are_excluded(artefact) -> None:
    """93 identical upgrade/downgrade pairs would dominate the index as false dupes."""
    assert not [k for k in artefact["functions"] if "alembic/versions" in k]


# ─── Joins ────────────────────────────────────────────────────────────────────

def test_canonical_ownership_is_joined_not_guessed(artefact) -> None:
    """The lens must DEFER to the registry on duplication, never derive its own view.

    A re-implementation creates no edge to the original, so no reachability structure
    can answer this. Deriving it here would reproduce the exact failure the registry
    exists to prevent.
    """
    owner = artefact["functions"]["backend/utils/money.py::dollars_to_cents"]
    assert owner["canonical"]["owns"] == "money-dollars-to-cents"

    dupe = artefact["functions"][
        "backend/services/finance_metrics/mongo_adapter.py::dollars_to_cents"
    ]
    assert dupe["canonical"]["violates"] == "money-dollars-to-cents"


def test_featuretrace_tags_and_layer_are_joined(artefact) -> None:
    rec = artefact["functions"][
        "backend/services/manager_function_service.py::resolve_manager_scope"
    ]
    assert "manager-function-scoping" in rec["feature_tags"]
    assert rec["layer"] == "service"


def test_route_contracts_are_extracted(artefact) -> None:
    routed = [r for r in artefact["functions"].values() if r["routes"]]
    assert len(routed) > 500
    sample = routed[0]["routes"][0]
    assert sample["method"] in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
    assert sample["path"].startswith("/")


def test_guards_are_recorded_for_a_known_guarded_function(artefact) -> None:
    rec = artefact["functions"]["backend/utils/route_guards.py::assert_roles"]
    assert "effective_role" in rec["guards"]


# ─── Lookup ───────────────────────────────────────────────────────────────────

def test_lookup_prefers_exact_key_then_name_then_substring(lens, artefact) -> None:
    key = "backend/services/manager_function_service.py::resolve_manager_scope"
    assert lens.lookup(artefact, key)[0]["key"] == key

    by_name = lens.lookup(artefact, "resolve_manager_scope")
    assert [r["key"] for r in by_name] == [key]

    fuzzy = lens.lookup(artefact, "manager_scope")
    assert any(r["key"] == key for r in fuzzy)


def test_lookup_of_an_unknown_name_returns_nothing(lens, artefact) -> None:
    assert lens.lookup(artefact, "no_such_function_anywhere_xyzzy") == []


def test_rendered_lens_warns_on_ambiguous_edges(lens, artefact) -> None:
    dupe = artefact["functions"][
        "backend/services/finance_metrics/mongo_adapter.py::dollars_to_cents"
    ]
    rendered = lens.render_lens(dupe)
    assert "AMBIGUOUS" in rendered
    assert "DUPLICATE" in rendered


# ─── Freshness ────────────────────────────────────────────────────────────────

def test_committed_digest_matches_the_tree(lens, artefact, committed_digest) -> None:
    """A stale lens is worse than none: it is a wrong answer a reader will believe.

    The hash covers every function record, so any drift trips it. `generated_at` is
    excluded by design — a timestamp in the hash would make every run report dirty
    and the gate would be switched off within a week.
    """
    fresh = lens.digest(artefact)
    assert fresh["content_sha256"] == committed_digest["content_sha256"], (
        "docs/architecture/function_lens_digest.json is stale. Regenerate and commit:\n"
        "  python3 scripts/validation/generate_function_lens.py"
    )


def test_the_full_index_is_not_committed() -> None:
    """16 MB of generated JSON has no place in git history.

    Committing it once produced a +427,459-line diff and rewrote megabytes into
    history on every code change. A generated artefact belongs in the repository
    only if a human can read its diff; this one cannot, so the digest is committed
    and the index is a gitignored cache.
    """
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "docs/architecture/function_lens.json"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    ).stdout.strip()
    assert not tracked, (
        "docs/architecture/function_lens.json is tracked again. It is a 13.5 MB "
        "cache; commit function_lens_digest.json instead."
    )


def test_digest_carries_the_lists_people_act_on(committed_digest) -> None:
    """The digest must stay useful, not shrink to a bare hash."""
    assert committed_digest["canonical_duplicates"], "expected known duplicates"
    assert committed_digest["canonical_owners"], "expected registered owners"
    assert "dollars_to_cents" in committed_digest["ambiguous_names"]
