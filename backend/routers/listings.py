"""
Listings router module.

This module handles all marketplace listing routes including creating,
retrieving, updating, and deleting listings for properties, services, and items.
"""

import html as html_lib
import uuid

import asyncio
import nh3
from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional

from database import db
from models.listing import ListingCreate, ListingResponse
from utils.activity_helper import log_activity
from utils.auth import (
    get_current_user,
    get_optional_user,
    get_approved_user,
    get_current_building,
    get_optional_building,
    is_approved_user,
)
from utils.helpers import get_current_utc_iso
from utils.permissions import get_user_permissions

# Create router
router = APIRouter(prefix="")


@router.post("/listings", response_model=ListingResponse)
async def create_listing(
        listing: ListingCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create a new marketplace listing.

    Requires authentication and appropriate permissions to create listings.
    Creates a listing for properties, services, or items for sale/rent.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_create_listings:
        raise HTTPException(status_code=403, detail="Not authorized to create listings")

    listing_id = str(uuid.uuid4())
    now = get_current_utc_iso()

    # SECURITY: Sanitize user input to prevent Stored XSS
    listing_data = listing.model_dump()
    if listing_data.get("title"):
        sanitized_title = html_lib.escape(listing_data["title"])
        if len(sanitized_title) > 200:
            raise HTTPException(
                status_code=400, detail="Title too long after sanitization"
            )
        listing_data["title"] = sanitized_title
    if listing_data.get("description"):
        sanitized_desc = nh3.clean(listing_data["description"])
        if len(sanitized_desc) > 2000:
            raise HTTPException(
                status_code=400, detail="Description too long after sanitization"
            )
        listing_data["description"] = sanitized_desc

    listing_doc = {
        "id": listing_id,
        "building_id": building_id,
        **listing_data,
        "created_by": current_user["id"],
        "created_by_name": current_user["full_name"],
        "status": "active",
        "created_at": now,
        "updated_at": now
    }

    await db.listings.insert_one(listing_doc)

    asyncio.create_task(log_activity(
        activity_type="marketplace",
        title=f"New Marketplace Listing: {listing.title}",
        entity_id=listing_id,
        priority=4,
        metadata={"listing_type": listing.listing_type}
    ))

    return ListingResponse(**listing_doc)


@router.get("/listings", response_model=List[ListingResponse])
async def get_listings(
        listing_type: Optional[str] = None,
        status: str = "active",
        scope: Optional[str] = None,
        current_user: dict = Depends(get_optional_user),
        building_id: str = Depends(get_optional_building)
):
    """
    Get all listings matching the specified criteria.

    Public listings are visible to everyone. Private listings require authentication.
    Can be filtered by listing type and status.
    Users can select the Global or their own specific building,
    but only if they are logged in and approved, else they see only public listings.
    Global shows all public listings and building-specific listings for the current building.

    F-010 note: get_optional_building falls back to DEFAULT_BUILDING_ID when no JWT /
    X-Building-ID header is present. This is safe here because unauthenticated callers
    are routed to the is_public=True branch which carries no building_id filter.
    Authenticated callers always have building_id resolved from their JWT claim.
    """
    base_query = {"status": status}

    # SECURITY: Only approved residents or staff can see private building content
    is_approved = is_approved_user(current_user)

    if not is_approved:
        # Unapproved/Anonymous see only public listings
        query = {**base_query, "is_public": True}
    elif scope == "global":
        # Global view for approved users
        query = {
            **base_query,
            "$or": [
                {"scope": "global"},
                {"building_id": building_id},
                {"is_public": True}
            ]
        }
    else:
        # Building specific view for approved users
        query = {**base_query, "building_id": building_id}

    if listing_type:
        query["listing_type"] = listing_type

    listings = await db.listings.find(query, {"_id": 0}).to_list(1000)
    return [ListingResponse(**listing_item) for listing_item in listings]


@router.get("/listings/{listing_id}", response_model=ListingResponse)
async def get_listing(
        listing_id: str,
        current_user: dict = Depends(get_optional_user),
        building_id: str = Depends(get_optional_building)
):
    """
    Get a specific listing by ID. Scoped to building context.

    Public listings are visible to everyone. Private listings require approved status.
    """
    listing_query = {
        "id": listing_id,
        "building_id": {"$exists": True},  # Signals bypass of auto-injection
        "$or": [
            {"building_id": building_id},
            {"scope": "global"},
            {"is_public": True}
        ]
    }

    listing = await db.listings.find_one(listing_query, {"_id": 0})

    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    # SECURITY: Verify access to private listings
    if not listing.get("is_public", True):
        if not is_approved_user(current_user):
            raise HTTPException(status_code=403, detail="Not authorized to view this listing")

        # Check building context if building scoped
        if listing.get("scope") == "building" and listing.get("building_id") != building_id:
            # Check if user has membership in that building
            if current_user.get("role") != "super_admin":
                membership = await db.memberships.find_one({
                    "user_id": current_user["id"],
                    "building_id": listing["building_id"],
                    "is_active": True
                })
                if not membership:
                    raise HTTPException(status_code=403, detail="Not authorized to view this building's content")

    return ListingResponse(**listing)


@router.put("/listings/{listing_id}", response_model=ListingResponse)
async def update_listing(
        listing_id: str,
        update_data: ListingCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Update an existing listing.
    
    Users can update their own listings. Administrators can update any listing.
    """
    listing = await db.listings.find_one(
        {"id": listing_id, "building_id": building_id}, {"_id": 0}
    )
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if listing["created_by"] != current_user["id"]:
        permissions = get_user_permissions(current_user)
        if not permissions.can_manage_users:
            raise HTTPException(status_code=403, detail="Not authorized")

    # SECURITY: Sanitize user input to prevent Stored XSS
    update_dict = update_data.model_dump()
    if update_dict.get("title"):
        sanitized_title = html_lib.escape(update_dict["title"])
        if len(sanitized_title) > 200:
            raise HTTPException(
                status_code=400, detail="Title too long after sanitization"
            )
        update_dict["title"] = sanitized_title
    if update_dict.get("description"):
        sanitized_desc = nh3.clean(update_dict["description"])
        if len(sanitized_desc) > 2000:
            raise HTTPException(
                status_code=400, detail="Description too long after sanitization"
            )
        update_dict["description"] = sanitized_desc

    update_dict["updated_at"] = get_current_utc_iso()

    await db.listings.update_one(
        {"id": listing_id, "building_id": building_id},
        {"$set": update_dict}
    )

    updated = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    return ListingResponse(**updated)


@router.delete("/listings/{listing_id}")
async def delete_listing(
        listing_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Delete a listing.
    
    Users can delete their own listings. Administrators can delete any listing.
    """
    listing = await db.listings.find_one(
        {"id": listing_id, "building_id": building_id}, {"_id": 0}
    )
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if listing["created_by"] != current_user["id"]:
        permissions = get_user_permissions(current_user)
        if not permissions.can_manage_users:
            raise HTTPException(status_code=403, detail="Not authorized")

    await db.listings.delete_one(
        {"id": listing_id, "building_id": building_id}
    )
    return {"message": "Listing deleted successfully"}


__all__ = ["router"]
