"""
Tests for the Zanzibar-Style Authorization Graph Engine.

Tests relationship-based permission derivation covering:
- Relation → permission slug mapping (owner_of, chairman, tenant_of, etc.)
- BFS graph traversal with part_of/belongs_to structural edges
- Chairman inheritance of committee_member permissions
- Expiry and is_active filtering
- Building-scope isolation across buildings

Run from project root:
    backend/venv/bin/pytest tests/backend/test_authorization_graph.py -v
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── Shared fixture tuples ────────────────────────────────────────────────────

# Core relationship tuples used across most tests.
# user:87  — owner + committee_member + chairman in building:east
# user:90  — tenant of unit:TH087 in building:east
SAMPLE_TUPLES = [
    {
        "subject": "user:87",
        "relation": "owner_of",
        "object": "unit:TH087",
        "building_id": "east",
        "is_active": True,
        "expires_at": None,
    },
    {
        "subject": "unit:TH087",
        "relation": "part_of",
        "object": "building:east",
        "building_id": "east",
        "is_active": True,
        "expires_at": None,
    },
    {
        "subject": "user:87",
        "relation": "committee_member",
        "object": "building:east",
        "building_id": "east",
        "is_active": True,
        "expires_at": None,
    },
    {
        "subject": "user:87",
        "relation": "chairman",
        "object": "building:east",
        "building_id": "east",
        "is_active": True,
        "expires_at": None,
    },
    {
        "subject": "user:90",
        "relation": "tenant_of",
        "object": "unit:TH087",
        "building_id": "east",
        "is_active": True,
        "expires_at": None,
    },
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_db_mock(tuples: list) -> MagicMock:
    """
    Return a MagicMock for `db` whose relationship_tuples.find(...) cursor
    always returns the provided *tuples* list.
    """
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=tuples)
    mock_db = MagicMock()
    mock_db.relationship_tuples.find.return_value = cursor
    return mock_db


def _make_subject_filter(tuples: list, subject: str) -> list:
    """Return tuples filtered by subject (simulates a MongoDB subject query)."""
    return [t for t in tuples if t["subject"] == subject]


def _make_graph_mock(tuples: list):
    """
    Patch get_subject_relations to filter the given tuple list by subject,
    correctly simulating per-subject MongoDB queries for BFS traversal.

    Mirrors the real get_subject_relations query which already filters on
    is_active=True in MongoDB, so inactive tuples are excluded here too.
    """

    async def _mock_get_relations(subject, building_id=None):
        filtered = [
            t for t in tuples
            if t["subject"] == subject
               and t.get("is_active", True)
               and (building_id is None or t.get("building_id") == building_id)
        ]
        return filtered

    return patch(
        "services.authorization_engine.get_subject_relations",
        side_effect=_mock_get_relations,
    )


# ─── 1  owner_of relation grants workorder.create ────────────────────────────

class TestOwnerRelation:
    async def test_owner_relations_grant_workorder_create(self):
        from services.authorization_engine import traverse_graph

        with _make_graph_mock(SAMPLE_TUPLES):
            perms = await traverse_graph("user:87", building_id="east")

        assert "workorder.create" in perms

    async def test_owner_relations_grant_financial_view_own(self):
        from services.authorization_engine import traverse_graph

        with _make_graph_mock(SAMPLE_TUPLES):
            perms = await traverse_graph("user:87", building_id="east")

        assert "financial.view_own" in perms

    async def test_owner_relations_grant_documents_view(self):
        from services.authorization_engine import traverse_graph

        with _make_graph_mock(SAMPLE_TUPLES):
            perms = await traverse_graph("user:87", building_id="east")

        assert "documents.view" in perms

    async def test_owner_relations_do_not_grant_full_financial_view(self):
        """
        owner_of grants financial.view_own only; financial.view (all units)
        requires committee_member or manager relation.
        This test uses an owner-only tuple set without committee membership.
        """
        from services.authorization_engine import traverse_graph

        owner_only_tuples = [
            {
                "subject": "user:55",
                "relation": "owner_of",
                "object": "unit:A1",
                "building_id": "east",
                "is_active": True,
                "expires_at": None,
            }
        ]
        with _make_graph_mock(owner_only_tuples):
            perms = await traverse_graph("user:55", building_id="east")

        assert "financial.view_own" in perms
        assert "financial.view" not in perms


# ─── 2  chairman relation adds function, never rank ──────────────────────────

class TestChairmanRelation:
    """The chair chairs. Chairing is not spending power.

    These three tests previously asserted the opposite — that the chairman
    relation granted ``financial.approve_invoice``, ``financial.export`` and
    ``users.invite``. They were the over-grant, written down as an expectation.
    Inverted 2026-08-24 under GAP-SEC-006, in the same spirit as the Phase 2 test
    that had encoded the cross-building office leak.

    UTMA 2011 (ACT) s 41 gives the chairperson the chair and the agenda, and
    sch 2 s 2.11 gives a casting vote only to break a tie. Neither is a financial
    or user-administration power.
    """

    async def test_chairman_relation_does_not_grant_approve_invoice(self):
        from services.authorization_engine import traverse_graph

        with _make_graph_mock(SAMPLE_TUPLES):
            perms = await traverse_graph("user:87", building_id="east")

        assert "financial.approve_invoice" not in perms, (
            "chairing a committee is not authority to approve an invoice"
        )

    async def test_chairman_relation_does_not_grant_financial_export(self):
        from services.authorization_engine import traverse_graph

        with _make_graph_mock(SAMPLE_TUPLES):
            perms = await traverse_graph("user:87", building_id="east")

        assert "financial.export" not in perms

    async def test_chairman_relation_does_not_grant_users_invite(self):
        from services.authorization_engine import traverse_graph

        with _make_graph_mock(SAMPLE_TUPLES):
            perms = await traverse_graph("user:87", building_id="east")

        assert "users.invite" not in perms, (
            "user administration is not an executive committee function"
        )

    async def test_chairman_relation_grants_agenda_control(self):
        """What the office DOES confer — UTMA 2011 (ACT) s 41."""
        from services.authorization_engine import traverse_graph

        with _make_graph_mock(SAMPLE_TUPLES):
            perms = await traverse_graph("user:87", building_id="east")

        assert "meetings.agenda.manage" in perms


# ─── 3  chairman inherits committee_member permissions ───────────────────────

class TestChairmanInheritance:
    async def test_chairman_inherits_committee_member_perms(self):
        """
        chairman relation must yield all committee_member permissions PLUS the
        chairman-exclusive ones.  User:87 has an explicit chairman tuple; the
        engine should inherit committee_member's set automatically.
        """
        from services.authorization_engine import traverse_graph, RELATION_PERMISSION_MAP

        with _make_graph_mock(SAMPLE_TUPLES):
            perms = await traverse_graph("user:87", building_id="east")

        # All committee_member perms should be present even though only
        # 'chairman' tuple exists for user:87 (plus explicit committee_member tuple).
        committee_member_perms = RELATION_PERMISSION_MAP["committee_member"]
        missing = committee_member_perms - perms
        assert not missing, (
            f"chairman should inherit committee_member perms; missing: {missing}"
        )

    async def test_chairman_only_user_still_gets_committee_member_perms(self):
        """
        A user with ONLY a chairman tuple (no explicit committee_member tuple)
        must still inherit committee_member permissions via the _CHAIRMAN_INHERITS logic.
        """
        from services.authorization_engine import traverse_graph, RELATION_PERMISSION_MAP

        chairman_only_tuples = [
            {
                "subject": "user:chair_only",
                "relation": "chairman",
                "object": "building:west",
                "building_id": "west",
                "is_active": True,
                "expires_at": None,
            }
        ]
        with _make_graph_mock(chairman_only_tuples):
            perms = await traverse_graph("user:chair_only", building_id="west")

        committee_member_perms = RELATION_PERMISSION_MAP["committee_member"]
        missing = committee_member_perms - perms
        assert not missing, (
            f"chairman should inherit committee_member perms; missing: {missing}"
        )

    async def test_chairman_also_has_chairman_specific_perms(self):
        from services.authorization_engine import traverse_graph, RELATION_PERMISSION_MAP

        chairman_only_tuples = [
            {
                "subject": "user:chair_only",
                "relation": "chairman",
                "object": "building:west",
                "building_id": "west",
                "is_active": True,
                "expires_at": None,
            }
        ]
        with _make_graph_mock(chairman_only_tuples):
            perms = await traverse_graph("user:chair_only", building_id="west")

        chairman_perms = RELATION_PERMISSION_MAP["chairman"]
        missing = chairman_perms - perms
        assert not missing, f"chairman-specific perms missing: {missing}"


# ─── 4  tenant_of grants tenant permissions ──────────────────────────────────

class TestTenantRelation:
    async def test_tenant_gets_tenant_permissions(self):
        from services.authorization_engine import traverse_graph, RELATION_PERMISSION_MAP

        with _make_graph_mock(SAMPLE_TUPLES):
            perms = await traverse_graph("user:90", building_id="east")

        tenant_perms = RELATION_PERMISSION_MAP["tenant_of"]
        missing = tenant_perms - perms
        assert not missing, f"Tenant missing expected perms: {missing}"

    async def test_tenant_can_access_chat(self):
        from services.authorization_engine import traverse_graph

        with _make_graph_mock(SAMPLE_TUPLES):
            perms = await traverse_graph("user:90", building_id="east")

        assert "chat.access" in perms

    async def test_tenant_can_request_maintenance(self):
        from services.authorization_engine import traverse_graph

        with _make_graph_mock(SAMPLE_TUPLES):
            perms = await traverse_graph("user:90", building_id="east")

        assert "maintenance.request" in perms


# ─── 5  tenant cannot view financial data ────────────────────────────────────

class TestTenantFinancialAccess:
    async def test_tenant_cannot_view_financial(self):
        from services.authorization_engine import traverse_graph

        with _make_graph_mock(SAMPLE_TUPLES):
            perms = await traverse_graph("user:90", building_id="east")

        assert "financial.view" not in perms
        assert "financial.view_own" not in perms
        assert "financial.manage" not in perms
        assert "financial.approve_invoice" not in perms

    async def test_tenant_cannot_manage_users(self):
        from services.authorization_engine import traverse_graph

        with _make_graph_mock(SAMPLE_TUPLES):
            perms = await traverse_graph("user:90", building_id="east")

        assert "users.manage" not in perms
        assert "users.invite" not in perms

    async def test_tenant_cannot_upload_documents(self):
        from services.authorization_engine import traverse_graph

        with _make_graph_mock(SAMPLE_TUPLES):
            perms = await traverse_graph("user:90", building_id="east")

        assert "documents.upload" not in perms


# ─── 6  Graph traversal through structural edges ─────────────────────────────

class TestGraphTraversal:
    """
    Structural edges (part_of, belongs_to) extend the BFS to child nodes.
    This test suite exercises multi-hop traversal where intermediate nodes
    grant permissions at depth > 0.
    """

    async def test_graph_traversal_depth(self):
        """
        BFS must traverse: user:99 --belongs_to--> node:A --committee_member--> building:east
        The committee_member relation on node:A should propagate its permissions back to user:99.
        """
        from services.authorization_engine import traverse_graph

        depth_tuples = [
            # hop 0: user:99 → belongs_to → node:A  (structural traversal)
            {
                "subject": "user:99",
                "relation": "belongs_to",
                "object": "node:A",
                "building_id": "east",
                "is_active": True,
                "expires_at": None,
            },
            # hop 1: node:A → committee_member → building:east  (permission-granting)
            {
                "subject": "node:A",
                "relation": "committee_member",
                "object": "building:east",
                "building_id": "east",
                "is_active": True,
                "expires_at": None,
            },
        ]

        with _make_graph_mock(depth_tuples):
            perms = await traverse_graph("user:99", building_id="east")

        assert "financial.view" in perms, (
            "committee_member permissions should propagate via belongs_to traversal"
        )
        assert "committee.vote" in perms

    async def test_part_of_chain_traverses_to_building(self):
        """
        unit:TH087 --part_of--> building:east; if the BFS reaches unit:TH087
        it should continue to building:east.  Validate that the BFS visits the
        intermediate node and continues to the target.
        """
        from services.authorization_engine import traverse_graph

        # Start at "unit:TH087" which has a part_of relation (simulates
        # a subject that IS a unit, not a user — useful for aggregate checks).
        chain_tuples = [
            {
                "subject": "unit:TH087",
                "relation": "part_of",
                "object": "building:east",
                "building_id": "east",
                "is_active": True,
                "expires_at": None,
            },
            {
                "subject": "building:east",
                "relation": "manager_of",
                "object": "strata:corp",
                "building_id": "east",
                "is_active": True,
                "expires_at": None,
            },
        ]

        with _make_graph_mock(chain_tuples):
            perms = await traverse_graph("unit:TH087", building_id="east")

        # manager_of grants broad permissions
        assert "financial.view" in perms

    async def test_max_depth_limit_prevents_runaway(self):
        """
        A circular or very deep graph must be safely capped by max_depth.
        No exception should be raised and the function must return a set.
        """
        from services.authorization_engine import traverse_graph

        # Create a chain of 10 nodes connected by belongs_to
        deep_tuples = []
        for i in range(10):
            deep_tuples.append({
                "subject": f"node:{i}",
                "relation": "belongs_to",
                "object": f"node:{i + 1}",
                "building_id": "east",
                "is_active": True,
                "expires_at": None,
            })

        with _make_graph_mock(deep_tuples):
            perms = await traverse_graph("node:0", building_id="east", max_depth=4)

        # Should not raise, should return a set (even if empty)
        assert isinstance(perms, set)


# ─── 7  Expired tuples are not used ──────────────────────────────────────────

class TestExpiredTuples:
    async def test_expired_relation_not_used(self):
        """A tuple whose expires_at is in the past must be filtered out."""
        from services.authorization_engine import traverse_graph

        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        expired_tuples = [
            {
                "subject": "user:expired",
                "relation": "chairman",
                "object": "building:east",
                "building_id": "east",
                "is_active": True,
                "expires_at": past,
            }
        ]

        with _make_graph_mock(expired_tuples):
            perms = await traverse_graph("user:expired", building_id="east")

        assert "financial.approve_invoice" not in perms
        assert len(perms) == 0

    async def test_future_expiry_tuple_is_active(self):
        """A tuple with expires_at in the future must still grant permissions."""
        from services.authorization_engine import traverse_graph

        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        future_tuples = [
            {
                "subject": "user:future",
                "relation": "chairman",
                "object": "building:east",
                "building_id": "east",
                "is_active": True,
                "expires_at": future,
            }
        ]

        with _make_graph_mock(future_tuples):
            perms = await traverse_graph("user:future", building_id="east")

        # Probe permission is incidental — this test is about expiry handling.
        # It must be one the chairman relation actually confers (GAP-SEC-006).
        assert "meetings.agenda.manage" in perms

    async def test_is_tuple_active_past_expiry_returns_false(self):
        """Unit-test the pure helper _is_tuple_active() with a past timestamp."""
        from services.authorization_engine import _is_tuple_active

        past = datetime.now(timezone.utc) - timedelta(hours=1)
        tup = {"is_active": True, "expires_at": past}
        assert _is_tuple_active(tup) is False

    async def test_is_tuple_active_future_expiry_returns_true(self):
        from services.authorization_engine import _is_tuple_active

        future = datetime.now(timezone.utc) + timedelta(hours=1)
        tup = {"is_active": True, "expires_at": future}
        assert _is_tuple_active(tup) is True

    async def test_is_tuple_active_none_expiry_returns_true(self):
        from services.authorization_engine import _is_tuple_active

        tup = {"is_active": True, "expires_at": None}
        assert _is_tuple_active(tup) is True

    async def test_is_tuple_active_iso_string_past_returns_false(self):
        """String ISO timestamps (from MongoDB) must also be handled."""
        from services.authorization_engine import _is_tuple_active

        past_str = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        tup = {"is_active": True, "expires_at": past_str}
        assert _is_tuple_active(tup) is False


# ─── 8  is_active=False tuples are not used ──────────────────────────────────

class TestInactiveTuples:
    async def test_inactive_relation_not_used(self):
        """Soft-deleted (is_active=False) tuples must grant no permissions."""
        from services.authorization_engine import traverse_graph

        inactive_tuples = [
            {
                "subject": "user:inactive",
                "relation": "owner_of",
                "object": "unit:TH080",
                "building_id": "east",
                "is_active": False,  # soft-deleted
                "expires_at": None,
            }
        ]

        with _make_graph_mock(inactive_tuples):
            perms = await traverse_graph("user:inactive", building_id="east")

        assert len(perms) == 0

    async def test_mix_of_active_and_inactive_tuples(self):
        """Only active tuples contribute permissions; inactive ones are ignored."""
        from services.authorization_engine import traverse_graph

        mixed_tuples = [
            {
                "subject": "user:mixed",
                "relation": "owner_of",
                "object": "unit:A",
                "building_id": "east",
                "is_active": True,
                "expires_at": None,
            },
            {
                "subject": "user:mixed",
                "relation": "chairman",
                "object": "building:east",
                "building_id": "east",
                "is_active": False,  # inactive
                "expires_at": None,
            },
        ]

        with _make_graph_mock(mixed_tuples):
            perms = await traverse_graph("user:mixed", building_id="east")

        # owner_of permissions should be present
        assert "financial.view_own" in perms
        # chairman permissions should NOT be present
        assert "financial.approve_invoice" not in perms


# ─── 9  Multi-building isolation ─────────────────────────────────────────────

class TestMultiBuildingIsolation:
    async def test_multi_building_isolation(self):
        """
        A user with a relation scoped to building:east must NOT obtain permissions
        when traversal is scoped to building:west.
        """
        from services.authorization_engine import traverse_graph

        east_tuples = [
            {
                "subject": "user:isolated",
                "relation": "chairman",
                "object": "building:east",
                "building_id": "east",
                "is_active": True,
                "expires_at": None,
            }
        ]

        # The mock filters by building_id via _make_graph_mock, so building:west
        # queries return empty — simulating MongoDB's building_id scoping.
        with _make_graph_mock(east_tuples):
            perms_west = await traverse_graph("user:isolated", building_id="west")

        assert len(perms_west) == 0, (
            "User with east-scoped chairman relation must have no perms in west"
        )

    async def test_user_gets_correct_perms_in_own_building(self):
        """Same user must receive full permissions when scoped to their own building."""
        from services.authorization_engine import traverse_graph

        east_tuples = [
            {
                "subject": "user:isolated",
                "relation": "chairman",
                "object": "building:east",
                "building_id": "east",
                "is_active": True,
                "expires_at": None,
            }
        ]

        with _make_graph_mock(east_tuples):
            perms_east = await traverse_graph("user:isolated", building_id="east")

        # Probe permission is incidental — this test is about building isolation.
        assert "meetings.agenda.manage" in perms_east

    async def test_strata_manager_with_no_building_scope(self):
        """
        A tuple with building_id=None (global scope) must be returned regardless
        of the building_id filter passed to traverse_graph.
        """
        from services.authorization_engine import traverse_graph

        global_tuples = [
            {
                "subject": "user:strata_mgr",
                "relation": "manager_of",
                "object": "strata:corp",
                "building_id": None,
                "is_active": True,
                "expires_at": None,
            }
        ]

        with _make_graph_mock(global_tuples):
            perms = await traverse_graph("user:strata_mgr")

        assert "financial.view" in perms
        assert "financial.approve_invoice" in perms


# ─── check_permission integration ────────────────────────────────────────────

class TestCheckPermission:
    """Tests for the higher-level check_permission function."""

    async def test_check_permission_allowed(self):
        from services.authorization_engine import check_permission

        with _make_graph_mock(SAMPLE_TUPLES):
            result = await check_permission("87", "meetings.agenda.manage", building_id="east")

        assert result.allowed is True
        assert result.reason.startswith("permission")

    async def test_check_permission_denied(self):
        from services.authorization_engine import check_permission

        with _make_graph_mock(SAMPLE_TUPLES):
            result = await check_permission("90", "financial.approve_invoice", building_id="east")

        assert result.allowed is False

    async def test_check_permission_result_has_matched_relations(self):
        """Matched relations must be populated for the audit trail."""
        from services.authorization_engine import check_permission

        with _make_graph_mock(SAMPLE_TUPLES):
            result = await check_permission("87", "meetings.agenda.manage", building_id="east")

        assert isinstance(result.matched_relations, list)
        assert len(result.matched_relations) > 0

    async def test_check_permission_denied_result_reason(self):
        from services.authorization_engine import check_permission

        with _make_graph_mock(SAMPLE_TUPLES):
            result = await check_permission("90", "financial.view", building_id="east")

        assert result.allowed is False
        assert "90" in result.reason or "no relationship" in result.reason.lower()

    async def test_check_permission_user_with_no_relations(self):
        """A user with no tuples must be denied every permission."""
        from services.authorization_engine import check_permission

        with _make_graph_mock([]):
            result = await check_permission("user:nobody", "documents.view")

        assert result.allowed is False


# ─── RELATION_PERMISSION_MAP integrity ───────────────────────────────────────

class TestRelationPermissionMap:
    """Structural tests on the RELATION_PERMISSION_MAP constant."""

    def test_owner_of_has_workorder_create(self):
        from services.authorization_engine import RELATION_PERMISSION_MAP

        assert "workorder.create" in RELATION_PERMISSION_MAP["owner_of"]

    def test_chairman_has_agenda_control_and_not_spending_power(self):
        """GAP-SEC-006: the chair's entry adds function, never rank."""
        from services.authorization_engine import RELATION_PERMISSION_MAP

        chairman = RELATION_PERMISSION_MAP["chairman"]
        assert "meetings.agenda.manage" in chairman
        assert "financial.approve_invoice" not in chairman
        assert "financial.export" not in chairman
        assert "users.invite" not in chairman

    def test_committee_member_has_financial_view(self):
        from services.authorization_engine import RELATION_PERMISSION_MAP

        assert "financial.view" in RELATION_PERMISSION_MAP["committee_member"]

    def test_tenant_of_lacks_financial_permissions(self):
        from services.authorization_engine import RELATION_PERMISSION_MAP

        tenant_perms = RELATION_PERMISSION_MAP["tenant_of"]
        assert "financial.view" not in tenant_perms
        assert "financial.view_own" not in tenant_perms
        assert "financial.approve_invoice" not in tenant_perms

    def test_all_relation_slugs_are_strings(self):
        from services.authorization_engine import RELATION_PERMISSION_MAP

        for relation, slugs in RELATION_PERMISSION_MAP.items():
            for slug in slugs:
                assert isinstance(slug, str), (
                    f"Slug '{slug}' in relation '{relation}' is not a string"
                )
            assert isinstance(slugs, set), (
                f"Permission set for '{relation}' must be a set"
            )

    def test_manager_of_is_superset_of_committee_member(self):
        """Strata/building manager must hold all committee_member permissions."""
        from services.authorization_engine import RELATION_PERMISSION_MAP

        manager_perms = RELATION_PERMISSION_MAP["manager_of"]
        committee_perms = RELATION_PERMISSION_MAP["committee_member"]

        missing = committee_perms - manager_perms
        assert not missing, (
            f"manager_of is missing committee_member perms: {missing}"
        )
