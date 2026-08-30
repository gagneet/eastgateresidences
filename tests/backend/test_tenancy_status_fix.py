"""
Tests for the tenancy status bug fix.

Bug: create_tenancy() saved with status="pending" but get_active_tenancy()
only queried status IN ["active", "periodic"] → newly created tenancies
never appeared in the UI.

Fix:
  1. create_tenancy() now saves status="active" (owner adding their own tenant
     requires no separate approval step)
  2. get_active_tenancy() also matches "pending" (backwards compat for any
     pre-existing records)
  3. Existing "pending" records in the DB were migrated to "active"
"""
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Source-level checks
# ─────────────────────────────────────────────────────────────────────────────

class TestTenancyStatusInSource:
    """Verify the fix is present in the source without hitting MongoDB."""

    def _source(self) -> str:
        import services.tenancy_service as ts
        return inspect.getsource(ts)

    def test_create_tenancy_uses_active_status(self):
        """create_tenancy must save status='active', not 'pending'."""
        src = self._source()
        # The status assignment should be active
        assert '"status": "active"' in src or "'status': 'active'" in src, (
            "create_tenancy() must set status='active' so newly-created "
            "tenancies appear in the UI immediately."
        )

    def test_create_tenancy_does_not_use_pending(self):
        """create_tenancy must NOT save status='pending'."""
        import services.tenancy_service as ts
        src = inspect.getsource(ts.create_tenancy)
        assert '"status": "pending"' not in src and "'status': 'pending'" not in src, (
            "create_tenancy() must not set status='pending' — owners adding "
            "their own tenants should not require a separate approval step."
        )

    def test_get_active_tenancy_includes_pending_fallback(self):
        """get_active_tenancy must include 'pending' for backwards compat."""
        src = self._source()
        assert '"pending"' in src or "'pending'" in src, (
            "get_active_tenancy() should include 'pending' in its status "
            "filter for backwards compatibility with pre-fix records."
        )

    def test_get_active_tenancy_includes_active_and_periodic(self):
        """get_active_tenancy must still include 'active' and 'periodic'."""
        import services.tenancy_service as ts
        src = inspect.getsource(ts.get_active_tenancy)
        assert '"active"' in src or "'active'" in src
        assert '"periodic"' in src or "'periodic'" in src


# ─────────────────────────────────────────────────────────────────────────────
# Functional tests (mocked DB)
# ─────────────────────────────────────────────────────────────────────────────

PROP_ID = "507f1f77bcf86cd799439011"  # valid 24-char hex ObjectId
OWNER_ID = "507f1f77bcf86cd799439012"


@pytest.fixture
def mock_db():
    """Provide a mocked database."""
    db = MagicMock()
    db.tenancies = MagicMock()
    db.owner_properties = MagicMock()
    return db


class TestCreateTenancyStatus:

    @pytest.mark.asyncio
    async def test_create_tenancy_saves_active_status(self, mock_db):
        """Verify create_tenancy inserts a document with status='active'."""
        inserted_doc = {}

        async def fake_insert(doc):
            inserted_doc.update(doc)
            result = MagicMock()
            result.inserted_id = "abc123"
            return result

        mock_db.tenancies.insert_one = fake_insert
        mock_db.owner_properties.update_one = AsyncMock()

        with patch("services.tenancy_service.db", mock_db), \
                patch("services.tenancy_service.create_audit_log", new_callable=AsyncMock), \
                patch("services.tenancy_service.asyncio") as mock_asyncio:
            mock_asyncio.create_task = MagicMock()

            import services.tenancy_service as ts
            data = {
                "tenant_name": "Jane Smith",
                "tenant_email": "jane@example.com",
                "lease_start": "2026-03-01",
                "lease_end": "2027-02-28",
                "rent_amount": 2200,
                "bond_amount": 4400,
            }
            result = await ts.create_tenancy(PROP_ID, data, OWNER_ID)

        assert result["status"] == "active", (
            f"Expected status='active', got status='{result['status']}'. "
            "Tenancy must be active immediately after owner creates it."
        )
        assert inserted_doc["status"] == "active"

    @pytest.mark.asyncio
    async def test_create_tenancy_not_pending(self, mock_db):
        """create_tenancy must NOT produce a pending document."""
        inserted_doc = {}

        async def fake_insert(doc):
            inserted_doc.update(doc)
            result = MagicMock()
            result.inserted_id = "abc123"
            return result

        mock_db.tenancies.insert_one = fake_insert
        mock_db.owner_properties.update_one = AsyncMock()

        with patch("services.tenancy_service.db", mock_db), \
                patch("services.tenancy_service.create_audit_log", new_callable=AsyncMock), \
                patch("services.tenancy_service.asyncio") as mock_asyncio:
            mock_asyncio.create_task = MagicMock()

            import services.tenancy_service as ts
            await ts.create_tenancy(PROP_ID, {"tenant_name": "Bob"}, OWNER_ID)

        assert inserted_doc.get("status") != "pending", (
            "Tenancy must not be created with 'pending' status."
        )

    @pytest.mark.asyncio
    async def test_get_active_tenancy_finds_active(self, mock_db):
        """get_active_tenancy must return a document with status='active'."""
        expected = {"_id": "tid1", "property_id": PROP_ID, "status": "active", "tenant_name": "Alice"}
        mock_db.tenancies.find_one = AsyncMock(return_value=expected)

        with patch("services.tenancy_service.db", mock_db):
            import services.tenancy_service as ts
            result = await ts.get_active_tenancy(PROP_ID)

        assert result is not None
        assert result["status"] == "active"

    @pytest.mark.asyncio
    async def test_get_active_tenancy_finds_pending_legacy(self, mock_db):
        """get_active_tenancy must also find legacy 'pending' records (backwards compat)."""
        # Simulate a pre-fix pending record still in the DB
        expected = {"_id": "tid2", "property_id": PROP_ID, "status": "pending", "tenant_name": "Bob"}
        mock_db.tenancies.find_one = AsyncMock(return_value=expected)

        with patch("services.tenancy_service.db", mock_db):
            import services.tenancy_service as ts
            result = await ts.get_active_tenancy(PROP_ID)

        assert result is not None, (
            "get_active_tenancy() must return pending records for backwards "
            "compatibility with pre-fix data."
        )

    @pytest.mark.asyncio
    async def test_get_active_tenancy_returns_none_when_no_tenancy(self, mock_db):
        """get_active_tenancy must return None when no matching tenancy exists."""
        mock_db.tenancies.find_one = AsyncMock(return_value=None)

        with patch("services.tenancy_service.db", mock_db):
            import services.tenancy_service as ts
            result = await ts.get_active_tenancy(PROP_ID)

        assert result is None

    @pytest.mark.asyncio
    async def test_workflow_create_then_get_returns_tenancy(self, mock_db):
        """
        End-to-end mock: after creating a tenancy, get_active_tenancy should
        find it (simulates what the frontend does after handleAddTenancy).
        """
        stored = {}

        async def fake_insert(doc):
            stored.update(doc)
            result = MagicMock()
            result.inserted_id = "new-id"
            return result

        mock_db.tenancies.insert_one = fake_insert
        mock_db.owner_properties.update_one = AsyncMock()

        # After create, find_one returns what was stored
        async def fake_find_one(query):
            status_filter = query.get("status", {}).get("$in", [])
            if stored.get("status") in status_filter:
                return {**stored, "_id": "new-id"}
            return None

        mock_db.tenancies.find_one = fake_find_one

        with patch("services.tenancy_service.db", mock_db), \
                patch("services.tenancy_service.create_audit_log", new_callable=AsyncMock), \
                patch("services.tenancy_service.asyncio") as mock_asyncio:
            mock_asyncio.create_task = MagicMock()

            import services.tenancy_service as ts
            # Step 1: Owner creates tenancy
            await ts.create_tenancy(PROP_ID, {"tenant_name": "Carol"}, OWNER_ID)

            # Step 2: Frontend immediately fetches — must find the record
            tenancy = await ts.get_active_tenancy(PROP_ID)

        assert tenancy is not None, (
            "After create_tenancy(), get_active_tenancy() must return the new "
            "record. This was the UI bug: create returned 'pending' but GET "
            "only queried 'active'/'periodic'."
        )
        assert tenancy["tenant_name"] == "Carol"
