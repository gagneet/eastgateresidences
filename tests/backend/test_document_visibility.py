"""Document visibility: one rule, honoured by the list, the fetch and the feed.

Regression cover for the East Gate defect where 242 generated levy notices were
invisible on /documents (the reader filtered on is_public/uploaded_by/allowed_roles
while the writer stored is_private/owner_id) yet were still advertised in the
Community Feed, so every feed link dead-ended on an empty page.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from utils.document_visibility import (  # noqa: E402
    build_document_visibility_filter,
    is_privileged_document_reader,
    owned_unit_numbers,
)

# Shapes the two writers actually produce.
GENERATED_NOTICE = {  # cron_payment_reminders
    "id": "d1", "title": "Levy Notice - TH083 - 2026-09-01",
    "category": "finance", "unit_number": "TH083",
    "owner_id": "owner-1", "is_private": True,
}
PLAIN_UPLOAD = {"id": "d2", "title": "Minutes", "uploaded_by": "mgr-1"}
EXPLICIT_PUBLIC = {"id": "d3", "title": "Notice", "is_public": True}
EXPLICIT_PRIVATE = {"id": "d4", "title": "Legal advice", "is_public": False,
                    "uploaded_by": "mgr-1"}
ROLE_SCOPED = {"id": "d5", "title": "EC pack", "is_private": True,
               "allowed_roles": ["ec_member"]}

ALL_DOCS = [GENERATED_NOTICE, PLAIN_UPLOAD, EXPLICIT_PUBLIC, EXPLICIT_PRIVATE, ROLE_SCOPED]


def _matches(doc: dict, clause: dict) -> bool:
    """Minimal evaluator for the operators this filter emits."""
    if not clause:
        return True
    for key, cond in clause.items():
        if key == "$and":
            if not all(_matches(doc, c) for c in cond):
                return False
        elif key == "$or":
            if not any(_matches(doc, c) for c in cond):
                return False
        elif isinstance(cond, dict) and "$ne" in cond:
            if doc.get(key) == cond["$ne"]:
                return False
        elif isinstance(cond, dict) and "$in" in cond:
            value = doc.get(key)
            values = value if isinstance(value, list) else [value]
            if not any(v in cond["$in"] for v in values):
                return False
        else:
            if doc.get(key) != cond:
                return False
    return True


def _visible(user, docs=ALL_DOCS):
    f = build_document_visibility_filter(user)
    return {d["id"] for d in docs if _matches(d, f)}


class TestPrivilegedReaders:
    @pytest.mark.parametrize("role", ["super_admin", "strata_admin", "strata_manager", "ec_member"])
    def test_privileged_roles_see_everything(self, role):
        user = {"id": "u", "role": role}
        assert is_privileged_document_reader(user)
        assert build_document_visibility_filter(user) == {}
        assert _visible(user) == {"d1", "d2", "d3", "d4", "d5"}

    def test_elevated_owner_is_privileged_via_effective_role(self):
        """Raw role is 'owner'; effective role is what governs (CLAUDE.md)."""
        user = {"id": "u", "role": "owner", "effective_role": "ec_member"}
        assert is_privileged_document_reader(user)
        assert _visible(user) == {"d1", "d2", "d3", "d4", "d5"}


class TestResidents:
    def test_owner_sees_own_generated_notice(self):
        """The regression: a levy notice must reach the owner it is addressed to."""
        assert "d1" in _visible({"id": "owner-1", "role": "owner"})

    def test_owner_sees_notice_addressed_to_their_unit(self):
        assert "d1" in _visible({"id": "someone", "role": "owner", "unit_number": "TH083"})

    def test_unrelated_owner_cannot_see_another_units_notice(self):
        """No cross-owner leak — the whole reason the filter is not simply dropped."""
        visible = _visible({"id": "other", "role": "owner", "unit_number": "UA001"})
        assert "d1" not in visible
        assert "d4" not in visible
        assert visible == {"d2", "d3"}

    def test_unflagged_upload_is_shared(self):
        """A document carrying neither flag is shared, as the upload UI presents it."""
        assert "d2" in _visible({"id": "anyone", "role": "owner"})

    def test_allowed_roles_still_honoured(self):
        assert "d5" in _visible({"id": "x", "role": "owner", "effective_role": "ec_member"})

    def test_multi_unit_owner_sees_each_unit(self):
        user = {"id": "x", "role": "owner", "owned_units": ["TH083", {"unit_number": "UA001"}]}
        assert owned_unit_numbers(user) == ["TH083", "UA001"]
        assert "d1" in _visible(user)


class TestAnonymous:
    def test_anonymous_sees_only_non_private(self):
        assert _visible(None) == {"d2", "d3"}

    def test_anonymous_never_sees_a_generated_notice(self):
        assert "d1" not in _visible(None)


class TestFilterShape:
    def test_no_top_level_or_for_residents(self):
        """TenantScopedDatabase._inject_bid() rejects a top-level $or; callers nest
        this under $and, so the fragment must stay a single mergeable clause."""
        f = build_document_visibility_filter({"id": "u", "role": "owner"})
        assert set(f.keys()) == {"$or"}

    def test_empty_for_privileged_so_callers_can_merge_unconditionally(self):
        assert build_document_visibility_filter({"id": "u", "role": "super_admin"}) == {}
