"""
Booking-related Pydantic models.

Contains models for defects, move bookings, amenity bookings, and parcel management.
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional


class DefectCreate(BaseModel):
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=2000)
    location: str = Field(..., max_length=200)
    defect_type: str = Field(..., max_length=100)  # structural, waterproofing, electrical, plumbing, finishing, other
    severity: str = Field("medium", max_length=20)  # low, medium, high, critical
    discovered_date: str = Field(..., max_length=100)
    warranty_claim: bool = False
    images: List[str] = Field(default_factory=list, max_items=10)


class DefectResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: str
    location: str
    defect_type: str
    severity: str
    discovered_date: str
    warranty_claim: bool
    status: str = "reported"  # reported, acknowledged, in_progress, resolved, closed
    # Pydantic v2: Optional[X] without a default is still *required*. Add = None so
    # existing DB docs missing these fields don't raise ValidationError on serialization.
    images: List[str] = Field(default_factory=list)
    contractor_id: Optional[str] = None
    resolution_notes: Optional[str] = None
    resolved_date: Optional[str] = None
    reported_by: str
    created_at: str
    updated_at: str


class MoveBookingCreate(BaseModel):
    unit_number: str = Field(..., max_length=20)
    move_type: str = Field(..., max_length=50)  # move_in, move_out
    scheduled_date: str = Field(..., max_length=100)
    time_slot: str = Field(..., max_length=50)  # morning, afternoon
    requires_lift: bool = True
    moving_company: Optional[str] = Field(None, max_length=200)
    bulk_waste: bool = False
    notes: Optional[str] = Field(None, max_length=1000)
    is_test_data: bool = False


class MoveBookingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    building_id: str
    unit_number: str
    move_type: str
    scheduled_date: str
    time_slot: str
    requires_lift: bool
    moving_company: Optional[str] = None
    bulk_waste: bool = False
    notes: Optional[str] = None
    status: str = "pending"  # pending, approved, completed, cancelled
    created_by: str
    created_at: str


class AmenityBookingCreate(BaseModel):
    amenity_type: str = Field(..., max_length=100)  # bbq_area, meeting_room, visitor_parking, gym
    date: str = Field(..., max_length=100)
    start_time: str = Field(..., max_length=50)
    end_time: str = Field(..., max_length=50)
    notes: Optional[str] = Field(None, max_length=1000)
    is_test_data: bool = False


_VALID_AMENITY_ICONS = {
    "Building", "UtensilsCrossed", "Users", "Car", "Dumbbell",
    "Waves", "Trees", "ShieldCheck", "Bike", "Film",
}


class AmenityCreate(BaseModel):
    key: str = Field(..., max_length=100)
    label: str = Field(..., max_length=100)
    description: Optional[str] = Field("", max_length=500)
    icon: str = Field("Building", max_length=50)

    def model_post_init(self, __context):
        """Generated function header.

        Function: AmenityCreate.model_post_init
        Path: backend/models/booking.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.key = self.key.strip().lower().replace(" ", "_")
        self.label = self.label.strip()
        if not self.key:
            raise ValueError("Amenity key is required")
        if not self.label:
            raise ValueError("Amenity label is required")
        if self.icon not in _VALID_AMENITY_ICONS:
            raise ValueError(
                f"Invalid icon '{self.icon}'. Valid options: {sorted(_VALID_AMENITY_ICONS)}"
            )


class AmenityBookingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    building_id: str
    amenity_type: str
    date: str
    start_time: str
    end_time: str
    notes: Optional[str] = None
    status: str = "confirmed"
    booked_by: str
    booked_by_name: Optional[str] = None
    unit_number: str
    created_at: str


class ParcelCreate(BaseModel):
    unit_number: str = Field(..., max_length=20)
    carrier: str = Field(..., max_length=100)  # auspost, startrack, dhl, fedex, amazon, other
    tracking_number: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = Field(None, max_length=500)
    storage_location: Optional[str] = Field(None, max_length=200)


class ParcelResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    unit_number: str
    carrier: str
    tracking_number: Optional[str]
    description: Optional[str]
    storage_location: Optional[str]
    status: str = "received"  # received, notified, collected
    received_date: str
    collected_date: Optional[str]
    logged_by: str
    created_at: str


__all__ = [
    "DefectCreate",
    "DefectResponse",
    "MoveBookingCreate",
    "MoveBookingResponse",
    "AmenityBookingCreate",
    "AmenityBookingResponse",
    "ParcelCreate",
    "ParcelResponse",
]
