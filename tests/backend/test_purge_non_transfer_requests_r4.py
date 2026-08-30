# @featuretrace:owner-transfers — R4: title-only drift is not an ownership transfer.
# Layer: test
# Data flow: owner_transfer_requests row -> classify() -> (delete, reason).
# Scope: building-scoped (classify() is pure; the caller scopes the query)
# Related: backend/scripts/data_repair/purge_non_transfer_owner_transfer_requests_20260827.py
#          backend/services/ownership_transfer_detection_service.py (_name_key)
"""R4 — the rule that clears title-only owner-transfer requests.

A live scrape on 2026-08-28 raised 29 requests for East Gate. **28 were title-only
drift** — the portal rendering the same owner as "jason carter" one run and "mr jason
carter" the next. Exactly one (UA029, "emma watt" => "sonja zink") was a real change.

The underlying cause is fixed in ``_name_key``; R4 clears the rows that key already
produced. It is the ONE rule allowed to delete a ``pending`` row, so its boundaries
matter more than most:

* it must fire when the two sides are the same people under different titles,
* it must NOT fire when the names genuinely differ,
* it must NOT fire on a row a person lodged,
* it must NOT fire when one side is empty — that is R1's case (a link created), and
  treating "empty == empty" as drift would delete rows R1 exists to explain.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_SCRIPT = _BACKEND / "scripts" / "data_repair" / "purge_non_transfer_owner_transfer_requests_20260827.py"


def _load():
    import importlib.util

    spec = importlib.util.spec_from_file_location("purge_non_transfer", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PENDING = {"pending", "pending_review", "awaiting_review"}


def _row(**over) -> dict:
    base = {
        "unit_number": "UA006",
        "status": "pending",
        "submitted_by_role": "system",
        "old_owners": [{"full_name": "Rachel Clarke"}],
        "new_owner": {"full_name": "Ms Rachel Clarke"},
        "portal_previous_owner_names": ["Rachel Clarke"],
        "portal_detected_owner_names": ["Ms Rachel Clarke"],
    }
    base.update(over)
    return base


class TestR4TitleOnlyDrift:
    def test_fires_on_a_title_only_difference(self):
        mod = _load()
        delete, reason = mod.classify(_row(), {}, PENDING)
        assert delete is True
        assert reason.startswith("R4")

    def test_fires_even_though_the_row_is_pending(self):
        """R4 is the deliberate exception to the pending guard: a row whose two sides
        name the same people presents nothing for a reviewer to judge."""
        mod = _load()
        delete, _ = mod.classify(_row(status="pending"), {}, PENDING)
        assert delete is True

    def test_does_not_fire_on_a_real_ownership_change(self):
        """UA029's live case — this row must survive and reach a reviewer."""
        mod = _load()
        delete, reason = mod.classify(
            _row(
                unit_number="UA029",
                old_owners=[{"full_name": "Emma Watt"}],
                new_owner={"full_name": "Sonja Zink"},
                portal_previous_owner_names=["Emma Watt"],
                portal_detected_owner_names=["Sonja Zink"],
            ),
            {}, PENDING,
        )
        assert delete is False
        assert "pending" in reason

    def test_does_not_fire_on_a_row_a_person_lodged(self):
        mod = _load()
        delete, reason = mod.classify(_row(submitted_by_role="owner"), {}, PENDING)
        assert delete is False
        assert "lodged by a person" in reason

    def test_does_not_fire_when_one_side_is_empty(self):
        """An empty outgoing side is R1 (a link was created), not R4. If R4 treated
        empty == empty as drift it would swallow the rows R1 exists to explain."""
        mod = _load()
        delete, reason = mod.classify(
            _row(status="approved", old_owners=[], portal_previous_owner_names=[]),
            {}, PENDING,
        )
        assert delete is True
        assert reason.startswith("R1"), f"expected R1 to own this case, got: {reason}"

    def test_handles_joint_owners_with_a_title_each(self):
        """A multi-owner string carries one title per person."""
        mod = _load()
        delete, _ = mod.classify(
            _row(
                unit_number="UA054",
                portal_previous_owner_names=["Jennifer Leung", "Tin Leung"],
                portal_detected_owner_names=["Mr Tin Leung", "Ms Jennifer Leung"],
            ),
            {}, PENDING,
        )
        assert delete is True

    def test_a_co_owner_genuinely_added_is_not_drift(self):
        """Adding a second lawful owner is a real change to review, not a title."""
        mod = _load()
        delete, _ = mod.classify(
            _row(
                portal_previous_owner_names=["Tin Leung"],
                portal_detected_owner_names=["Mr Tin Leung", "Ms Jennifer Leung"],
            ),
            {}, PENDING,
        )
        assert delete is False

    def test_falls_back_to_structured_payload_when_portal_names_absent(self):
        """Rows written before the detector recorded portal_* lists must still classify."""
        mod = _load()
        delete, _ = mod.classify(
            _row(portal_previous_owner_names=None, portal_detected_owner_names=None),
            {}, PENDING,
        )
        assert delete is True


class TestR4UsesTheDetectorsOwnKey:
    def test_person_keys_matches_name_key(self):
        """R4's claim is 're-evaluate under the corrected key'. If it reimplemented the
        normalisation the two could drift apart and disagree about who is the same
        person — so it must import _name_key, not copy it."""
        mod = _load()
        from services.ownership_transfer_detection_service import _name_key

        assert mod._person_keys([{"full_name": "Ms Rachel Clarke"}]) == {_name_key("Rachel Clarke")}
        assert _SCRIPT.read_text().count("from services.ownership_transfer_detection_service import _name_key") >= 1
