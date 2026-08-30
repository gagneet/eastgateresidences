# @featuretrace:canonical-owner-registry — One generated test per registry entry.
# Layer: test
# Data flow: docs/architecture/canonical_owners.yaml -> pytest parametrisation
#            -> static scan of backend/**.py -> fail on a second implementation.
# Scope: repo-wide (building-agnostic, no database)
# Related: docs/architecture/canonical_owners.yaml (the registry itself)
#          scripts/validation/generate_canonical_owner_registry.py (the scanner)
"""The capability index, enforced.

What this turns into a build failure
------------------------------------
"This concept already has an owner" was, until now, something a document said. A
document cannot fail. These tests can, and they fail on the three ways an owner
stops being the only owner:

1. **A second implementation appears.** The point. A re-implementation creates no
   call edge to the original, so FeatureTrace, the mindmap and the router map all
   render it as healthy new code — which is exactly how lot->unit resolution got
   rebuilt five times while every map looked fine.

2. **The owner stops exporting what callers need.** Rename or delete
   ``format_unit_display`` and every caller quietly rolls their own again. The
   symbol list is the contract.

3. **Recorded debt is fixed but left in the registry.** The ratchet only holds if
   it can go down. A stale entry is a slot a NEW violation can hide in.

Adding an entry is cheap and is how you stop a concept being rebuilt — see the
header of ``docs/architecture/canonical_owners.yaml``.

If a test here fails
--------------------
Call the owner. Do **not** add yourself to ``known_violations`` — that list is
existing debt being paid down, not an escape hatch. If you genuinely believe the
concept needs a second owner, that is a design change: say so in the PR and change
the registry deliberately, with the reason written down.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts" / "validation"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

pytest.importorskip("yaml", reason="pyyaml is required to read the registry")

from generate_canonical_owner_registry import (  # noqa: E402
    evaluate,
    load_registry,
    owner_symbols,
    scan,
    validate_schema,
)

_REGISTRY = load_registry()
_ENTRIES = _REGISTRY["concepts"]
_IDS = [e["concept"] for e in _ENTRIES]


def _entry(concept: str) -> dict:
    return next(e for e in _ENTRIES if e["concept"] == concept)


class TestRegistryIsWellFormed:
    """The registry is only as good as its own shape."""

    def test_every_concept_is_named_once(self):
        assert len(_IDS) == len(set(_IDS)), f"duplicate concept names: {_IDS}"

    def test_every_entry_carries_a_rule_and_a_reason(self):
        """A rule a reviewer cannot apply, or a rule with no incident behind it, is
        the kind of entry that gets deleted the first time it is inconvenient."""
        for e in _ENTRIES:
            assert (e.get("rule") or "").strip(), f"{e['concept']}: no rule"
            assert (e.get("why") or "").strip(), f"{e['concept']}: no reason"
            assert e.get("tests"), f"{e['concept']}: nothing enforces it"

    def test_every_entry_has_a_supported_language_and_schema(self):
        problems = validate_schema(_REGISTRY)
        assert not problems, "\n".join(problems)


@pytest.mark.parametrize("concept", _IDS)
class TestConceptHasExactlyOneOwner:
    """One test per registry entry, parametrised by concept so a failure names the
    concept that broke rather than 'the registry test'."""

    def test_the_owner_module_exists(self, concept):
        entry = _entry(concept)
        exists, _ = owner_symbols(entry)
        assert exists, (
            f"{concept}: owner module {entry['owner']} does not exist. An entry with no "
            f"destination cannot be consolidated toward."
        )

    def test_the_owner_still_exports_what_callers_need(self, concept):
        entry = _entry(concept)
        _, defined = owner_symbols(entry)
        missing = [s for s in entry.get("symbols") or [] if s not in defined]
        assert not missing, (
            f"{concept}: {entry['owner']} no longer defines {missing}. Callers will roll "
            f"their own again — which is precisely how this concept came back last time."
        )

    def test_no_second_implementation(self, concept):
        entry = _entry(concept)
        if not entry.get("detect"):
            pytest.skip(
                f"{concept} is deliberately not pattern-detectable — see the entry's own "
                f"comment in canonical_owners.yaml. Enforced by {entry['tests']}."
            )
        _, defined = owner_symbols(entry)
        found = sorted({h["key"] for h in scan(entry, defined)})
        known = set(entry.get("known_violations") or [])
        new = [k for k in found if k not in known]
        assert not new, (
            f"{concept}: a second implementation appeared.\n"
            f"  Rule: {' '.join(entry['rule'].split())}\n"
            f"  Use {entry['owner']} instead.\n"
            f"  New: {new}\n"
            f"  Do NOT add these to known_violations — that list is debt being paid "
            f"down, not an escape hatch."
        )

    def test_recorded_debt_is_still_real(self, concept):
        """A fixed violation left in the registry is a slot a new one can hide in."""
        entry = _entry(concept)
        if not entry.get("detect"):
            pytest.skip("not pattern-detectable")
        _, defined = owner_symbols(entry)
        found = {h["key"] for h in scan(entry, defined)}
        stale = [k for k in entry.get("known_violations") or [] if k not in found]
        assert not stale, (
            f"{concept}: {stale} are fixed but still listed in known_violations. "
            f"Remove them from docs/architecture/canonical_owners.yaml — the ratchet "
            f"only holds if it can go down."
        )

    def test_the_named_tests_exist(self, concept):
        entry = _entry(concept)
        missing = [t for t in entry.get("tests") or [] if not (_ROOT / t).exists()]
        assert not missing, (
            f"{concept} names tests that do not exist: {missing}. An entry whose "
            f"enforcement has been deleted is worse than no entry — it reads as covered."
        )


class TestTheDebtOnlyGoesDown:
    def test_recorded_violations_do_not_exceed_language_ceilings(self):
        """Ceilings, not assertions of the current number — so paying debt down
        never fails the build, and taking more on always does.

        Lower a language ceiling when you fix something. Never raise one.
        """
        ceilings = _REGISTRY.get("debt_ceiling") or {}
        report = evaluate(_REGISTRY)
        by_language = report["known_violations_by_language"]
        missing = sorted(set(by_language) - set(ceilings))
        assert not missing, f"missing debt ceilings for languages: {missing}"
        over = {
            language: (count, ceilings[language])
            for language, count in by_language.items()
            if count > ceilings[language]
        }
        assert not over, (
            f"recorded violations exceed language ceilings: {over}. New debt was "
            f"added to the registry instead of being fixed."
        )
