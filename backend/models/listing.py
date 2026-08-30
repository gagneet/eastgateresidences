"""
Listing-related Pydantic models.

Contains models for marketplace listings (properties for sale/rent, services, items).
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional


class ListingType:
    FOR_SALE = "for_sale"
    FOR_RENT = "for_rent"
    SERVICE = "service"
    ITEM_SALE = "item_sale"


class ListingCreate(BaseModel):
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=2000)
    listing_type: str = Field(...,
                              pattern=f"^({'|'.join([ListingType.FOR_SALE, ListingType.FOR_RENT, ListingType.SERVICE, ListingType.ITEM_SALE])})$")
    price: Optional[float] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    is_public: bool = True
    images: List[str] = []
    scope: str = Field("building", pattern="^(building|global)$")


class ListingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: str
    listing_type: str
    price: Optional[float] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    is_public: bool
    images: List[str]
    created_by: str
    created_by_name: str
    status: str
    created_at: str
    updated_at: str
    scope: str = "building"
    building_id: Optional[str] = None


__all__ = [
    "ListingType",
    "ListingCreate",
    "ListingResponse",
]
