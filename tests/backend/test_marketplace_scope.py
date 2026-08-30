"""
test_marketplace_scope.py — Unit tests for marketplace cross-building scope feature.

Tests cover:
- Listing with scope="building" not visible from other buildings via ?scope=network query
- Listing with scope="network" visible across buildings via ?scope=network
- Only super_admin/strata_manager can create network-scope listings
- Building-scoped GET still only returns own building listings
- Scope field defaults to "building" if not provided
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.listing import ListingCreate, ListingResponse


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_user(role="owner", building_id="13195", **kwargs):
    return {
        "id": "user-001",
        "email": "user@example.com",
        "full_name": "Test User",
        "role": role,
        "building_id": building_id,
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "custom_permissions": {},
        **kwargs,
    }


def _mock_db():
    db = MagicMock()
    db.listings.find.return_value.to_list = AsyncMock(return_value=[])
    db.listings.find_one = AsyncMock(return_value=None)
    db.listings.insert_one = AsyncMock(return_value=None)
    return db


LISTING_BUILDING = {
    "id": "list-001",
    "building_id": "13195",
    "title": "Sofa for sale",
    "description": "IKEA sofa, good condition",
    "listing_type": "item_sale",
    "price": 150.0,
    "contact_email": "seller@example.com",
    "contact_phone": "",
    "is_public": True,
    "images": [],
    "scope": "building",
    "created_by": "user-001",
    "created_by_name": "Test User",
    "status": "active",
    "created_at": "2026-01-01T00:00:00",
    "updated_at": "2026-01-01T00:00:00",
}

LISTING_GLOBAL = {
    **LISTING_BUILDING,
    "id": "list-002",
    "building_id": "13195",
    "scope": "global",
    "title": "Global listing",
}


# ── Test: ListingCreate model ─────────────────────────────────────────────

class TestListingCreateModel:
    """Verify scope field in ListingCreate and ListingResponse models."""

    def test_scope_defaults_to_building(self):
        """ListingCreate.scope must default to 'building'."""
        listing = ListingCreate(
            title="Test",
            description="desc",
            listing_type="item_sale",
        )
        assert listing.scope == "building"

    def test_scope_can_be_set_to_global(self):
        """ListingCreate.scope can be explicitly set to 'global'."""
        listing = ListingCreate(
            title="Test",
            description="desc",
            listing_type="item_sale",
            scope="global",
        )
        assert listing.scope == "global"

    def test_listing_response_has_scope_field(self):
        """ListingResponse must include the scope field."""
        response = ListingResponse(**LISTING_BUILDING)
        assert response.scope == "building"

    def test_listing_response_scope_global(self):
        """ListingResponse correctly reflects scope=global."""
        response = ListingResponse(**LISTING_GLOBAL)
        assert response.scope == "global"


# ── Test: Create listing permissions ─────────────────────────────────────

class TestCreateListingGlobalScope:
    """Everyone can create global listings as per new requirements."""

    @pytest.mark.asyncio
    async def test_owner_can_create_global_listing(self):
        """Owner can create scope=global."""
        from routers.listings import create_listing

        mock_db = _mock_db()
        user = _make_user(role="owner")

        with patch("routers.listings.db", mock_db), \
                patch("routers.listings.asyncio.create_task"):
            result = await create_listing(
                listing=ListingCreate(
                    title="Test", description="desc",
                    listing_type="item_sale", scope="global",
                ),
                current_user=user,
                building_id="13195",
            )

        assert result.scope == "global"

    @pytest.mark.asyncio
    async def test_super_admin_can_create_global_listing(self):
        """Super admin can create a global-scoped listing."""
        from routers.listings import create_listing

        mock_db = _mock_db()
        inserted = {}

        async def capture_insert(doc):
            inserted.update(doc)

        mock_db.listings.insert_one = capture_insert
        user = _make_user(role="super_admin")

        with patch("routers.listings.db", mock_db), \
                patch("routers.listings.asyncio.create_task"):
            result = await create_listing(
                listing=ListingCreate(
                    title="Global Listing", description="Visible everywhere",
                    listing_type="item_sale", scope="global",
                ),
                current_user=user,
                building_id="13195",
            )

        assert result.scope == "global"
        assert inserted.get("scope") == "global"


# ── Test: GET /listings with scope=global ────────────────────────────────

class TestGetListingsGlobalScope:
    """Verify ?scope=global returns cross-building listings correctly."""

    @pytest.mark.asyncio
    async def test_no_scope_param_returns_building_only(self):
        """Without scope param, only building-scoped listings are returned."""
        from routers.listings import get_listings

        mock_db = _mock_db()
        mock_db.listings.find.return_value.to_list = AsyncMock(
            return_value=[LISTING_BUILDING]
        )

        with patch("routers.listings.db", mock_db):
            result = await get_listings(
                listing_type=None, status="active", scope=None,
                current_user=_make_user(), building_id="13195",
            )

        # Query should use building_id filter
        call_args = mock_db.listings.find.call_args
        query = call_args[0][0]
        assert "building_id" in query
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_scope_global_uses_or_query(self):
        """When scope=global, query uses $or to include global listings."""
        from routers.listings import get_listings

        mock_db = _mock_db()
        mock_db.listings.find.return_value.to_list = AsyncMock(
            return_value=[LISTING_BUILDING, LISTING_GLOBAL]
        )

        with patch("routers.listings.db", mock_db):
            result = await get_listings(
                listing_type=None, status="active", scope="global",
                current_user=_make_user(), building_id="13195",
            )

        call_args = mock_db.listings.find.call_args
        query = call_args[0][0]
        assert "$or" in query
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_scope_global_unauthenticated_only_public(self):
        """Unauthenticated requests only see public items in global view."""
        from routers.listings import get_listings

        mock_db = _mock_db()
        mock_db.listings.find.return_value.to_list = AsyncMock(return_value=[])

        with patch("routers.listings.db", mock_db):
            await get_listings(
                listing_type=None, status="active", scope="global",
                current_user=None, building_id="13195",
            )

        call_args = mock_db.listings.find.call_args
        query = call_args[0][0]
        assert query.get("is_public") is True
