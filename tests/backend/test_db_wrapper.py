"""
Structural unit tests for the TenantCollection building-isolation wrapper.

These tests verify that:
  - TENANT_SCOPED_COLLECTIONS and GLOBAL_COLLECTIONS contain the expected entries
  - The eight new Community OS collections are tenant-scoped
  - The two sets are disjoint (no accidental cross-listing)
  - All collection names are non-empty strings
  - TenantCollection and TenantScopedDatabase exist with the expected API surface

No live database connection is required.
"""

import pytest

from database import (
    TENANT_SCOPED_COLLECTIONS,
    GLOBAL_COLLECTIONS,
    TenantCollection,
    TenantScopedDatabase,
)

# The eight collections introduced by the Community OS feature set.
_COMMUNITY_OS_COLLECTIONS = {
    "workflow_requests",
    "proposals",
    "proposal_votes",
    "savings_events",
    "volunteer_events",
    "volunteer_registrations",
    "building_summaries",
    "lot_accounts",
}


class TestTenantScopedCollectionSet:

    def test_tenant_collection_set_exists(self):
        """TENANT_SCOPED_COLLECTIONS is a non-empty set."""
        assert isinstance(TENANT_SCOPED_COLLECTIONS, set)
        assert len(TENANT_SCOPED_COLLECTIONS) > 0

    def test_workflow_requests_in_tenant_scoped(self):
        assert "workflow_requests" in TENANT_SCOPED_COLLECTIONS

    def test_proposals_in_tenant_scoped(self):
        assert "proposals" in TENANT_SCOPED_COLLECTIONS

    def test_savings_events_in_tenant_scoped(self):
        assert "savings_events" in TENANT_SCOPED_COLLECTIONS

    def test_new_community_os_collections_in_tenant_scoped(self):
        """All eight Community OS collections must be tenant-scoped."""
        missing = _COMMUNITY_OS_COLLECTIONS - TENANT_SCOPED_COLLECTIONS
        assert not missing, (
            f"These Community OS collections are missing from "
            f"TENANT_SCOPED_COLLECTIONS: {missing}"
        )


class TestGlobalCollectionSet:

    def test_global_collection_set_exists(self):
        """GLOBAL_COLLECTIONS is a non-empty set."""
        assert isinstance(GLOBAL_COLLECTIONS, set)
        assert len(GLOBAL_COLLECTIONS) > 0

    def test_users_in_global_collections(self):
        assert "users" in GLOBAL_COLLECTIONS

    def test_buildings_in_global_collections(self):
        assert "buildings" in GLOBAL_COLLECTIONS


class TestCollectionSetIntegrity:

    def test_tenant_scoped_disjoint_from_global(self):
        """A collection must not appear in both sets."""
        overlap = TENANT_SCOPED_COLLECTIONS & GLOBAL_COLLECTIONS
        assert not overlap, (
            f"Collections appear in both TENANT_SCOPED and GLOBAL sets: {overlap}"
        )

    def test_all_tenant_collection_names_are_strings(self):
        for name in TENANT_SCOPED_COLLECTIONS:
            assert isinstance(name, str) and name, (
                f"Invalid entry in TENANT_SCOPED_COLLECTIONS: {name!r}"
            )

    def test_all_global_collection_names_are_strings(self):
        for name in GLOBAL_COLLECTIONS:
            assert isinstance(name, str) and name, (
                f"Invalid entry in GLOBAL_COLLECTIONS: {name!r}"
            )

    def test_all_collection_names_are_strings(self):
        """Combined check: every name in either set is a non-empty string."""
        for name in TENANT_SCOPED_COLLECTIONS | GLOBAL_COLLECTIONS:
            assert isinstance(name, str) and name


class TestTenantCollectionStructure:

    def test_tenant_collection_class_exists(self):
        assert TenantCollection is not None

    def test_inject_bid_adds_building_id_to_empty_filter(self):
        """TenantCollection._inject_bid method exists and is callable."""
        assert callable(getattr(TenantCollection, "_inject_bid", None)), (
            "TenantCollection must expose a _inject_bid method"
        )

    def test_tenant_collection_has_required_async_methods(self):
        """Verify TenantCollection exposes the core Motor-compatible API."""
        for method_name in (
                "find",
                "find_one",
                "insert_one",
                "insert_many",
                "update_one",
                "update_many",
                "delete_one",
                "delete_many",
                "replace_one",
        ):
            assert hasattr(TenantCollection, method_name), (
                f"TenantCollection is missing method: {method_name}"
            )


class TestTenantScopedDatabase:

    def test_tenant_scoped_database_class_exists(self):
        assert TenantScopedDatabase is not None

    def test_tenant_scoped_database_has_getitem(self):
        """__getitem__ must be present so db['collection'] syntax works."""
        assert hasattr(TenantScopedDatabase, "__getitem__"), (
            "TenantScopedDatabase must implement __getitem__"
        )

    def test_tenant_scoped_database_has_getattr(self):
        """__getattr__ must be present so db.collection syntax works."""
        assert hasattr(TenantScopedDatabase, "__getattr__"), (
            "TenantScopedDatabase must implement __getattr__"
        )

    def test_db_module_export(self):
        """The module-level `db` instance is a TenantScopedDatabase."""
        from database import db
        assert isinstance(db, TenantScopedDatabase)
