# @featuretrace:outbound-message-queue — The queue collection must be tenant-scoped.
# Layer: test
# Data flow: TenantCollection.find/update -> TENANT_SCOPED_COLLECTIONS -> building_id injection (building-scoped).
# Related: backend/database.py
#          backend/services/outbound_queue_service.py
"""A collection listed in neither scoping set is silently unscoped.

`TenantCollection.find/find_one/update_one` inject `building_id` only when the
collection name appears in `TENANT_SCOPED_COLLECTIONS`. A name in neither that set nor
`GLOBAL_COLLECTIONS` raises nothing and filters nothing — it simply reads every
building's rows.

`outbound_messages` shipped in neither. The console lists messages, aggregates counts,
and cancels or releases by id, so an unscoped read exposed every building's recipients
and subjects to any manager, and cancel would have reached across buildings. The unit
tests missed it because they mock `db`, which is exactly the layer that does the
injecting — so these assertions are made against the registry itself.
"""

import pytest

from database import GLOBAL_COLLECTIONS, TENANT_SCOPED_COLLECTIONS
from services.outbound_queue_service import COLLECTION


def test_the_queue_collection_is_tenant_scoped():
    assert COLLECTION in TENANT_SCOPED_COLLECTIONS, (
        f"{COLLECTION!r} is not tenant-scoped: TenantCollection injects building_id only "
        f"for names in TENANT_SCOPED_COLLECTIONS, so every read would span all buildings"
    )


def test_the_queue_collection_is_not_also_global():
    """Membership of both sets is contradictory; scoped must win and be unambiguous."""
    assert COLLECTION not in GLOBAL_COLLECTIONS


def test_the_service_constant_matches_the_registered_name():
    """Guards a rename in one place but not the other, which re-opens the same hole."""
    assert COLLECTION == "outbound_messages"


@pytest.mark.parametrize("name", [
    "outbound_messages",
    # Collections this feature reads alongside the queue, asserted so a future edit
    # cannot quietly move one out of scope.
    "settings",
    "users",
])
def test_related_collections_are_classified_somewhere(name):
    """Neither-set is the dangerous state — it fails open, without an error."""
    assert name in TENANT_SCOPED_COLLECTIONS or name in GLOBAL_COLLECTIONS, (
        f"{name!r} is in neither scoping set, so it is silently unscoped"
    )


class TestDemoBankCollectionsAreScoped:
    """GAP-SEC-013 — the financial staging store must not fail open.

    demo_bank_transactions, demo_bank_accounts and demo_bank_import_batches shipped in
    NEITHER set while their two sibling reconstruction collections were registered. The
    asymmetry was the tell.

    No production call site was leaking — all 11 pass an explicit building_id — but an
    unscoped read returned every building's rows with no error, demonstrated live on
    2026-08-27 when a query for East Gate came back with 7,688 rows spanning two
    buildings. Registration is a no-op for correct code, because _inject_bid() does not
    re-inject when the filter already carries a building_id, and a guard for the next
    caller who forgets.
    """

    @pytest.mark.parametrize("name", [
        "demo_bank_transactions",
        "demo_bank_accounts",
        "demo_bank_import_batches",
        "demo_bank_reconstruction_batches",
        "demo_bank_reconstruction_manifests",
    ])
    def test_every_demo_bank_collection_is_tenant_scoped(self, name):
        assert name in TENANT_SCOPED_COLLECTIONS, (
            f"{name!r} holds building-scoped financial data but is not registered; the "
            f"wrapper would inject nothing and raise nothing"
        )

    @pytest.mark.parametrize("name", [
        "demo_bank_transactions", "demo_bank_accounts", "demo_bank_import_batches",
    ])
    def test_they_are_not_also_global(self, name):
        assert name not in GLOBAL_COLLECTIONS


def test_no_collection_is_registered_in_both_sets():
    """Membership of both is contradictory and the resolution order is not obvious."""
    overlap = TENANT_SCOPED_COLLECTIONS & GLOBAL_COLLECTIONS
    assert not overlap, f"registered in both sets: {sorted(overlap)}"
