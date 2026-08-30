# @featuretrace:bookings — Move-in/out bookings, amenity bookings, parcel log (dead router — server.py canonical).
# Layer: router
# Data flow: frontend → /move-bookings + /amenity-bookings + /parcels → db.move_bookings + db.amenity_bookings + db.parcels (building-scoped; server.py is canonical)
# Related: backend/server.py (lines ~11696-12340)
#           backend/routers/defects_register.py (defects are here now)
# Collection: move_bookings, amenity_bookings, amenities, parcels
# Scope: (building-scoped)

"""DEAD ROUTER — AUDIT-9 / F-011 Phase B  (2026-05-24)
====================================================
All routes in this file are duplicated in server.py OR superseded by
purpose-specific routers that ARE registered. This file is NOT registered in
server.py and is retained for historical reference only. Do NOT add new
routes here.

Route disposition:
  POST/GET /defects           → routers/defects_register.py (GAP-MNT-001, registered)
  PUT  /defects/{id}/status   → server.py line ~11696 (still inline, pending migration)
  POST/GET/PUT/DELETE /move-bookings      → server.py lines ~11728-11905
  POST/GET/DELETE     /amenity-bookings   → server.py lines ~11807-11876
  GET/POST/DELETE     /amenities          → server.py lines ~12238-12335
  POST/GET/PUT/GET    /parcels            → server.py lines ~12081-12215
"""

# Bookings router module.
#
# This module handles all booking-related routes including defects,
# move in/out bookings, amenity bookings, and parcel management.

import logging
import uuid
from datetime import datetime, timezone

import asyncio
from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional

from database import db
from models.booking import (
    DefectCreate,
    DefectResponse,
    MoveBookingCreate,
    MoveBookingResponse,
    AmenityBookingCreate,
    AmenityBookingResponse,
    AmenityCreate,
    ParcelCreate,
    ParcelResponse,
)
from utils.auth import (
    get_approved_user,
    get_current_building,
)
from utils.email import send_email_async
from utils.helpers import create_notifications_batch
from utils.permissions import get_user_permissions, require_feature

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="")

# ==================== PARCEL NOTIFICATION HELPER ====================

_CARRIER_DISPLAY = {
    "auspost": "Australia Post",
    "startrack": "StarTrack",
    "dhl": "DHL",
    "fedex": "FedEx",
    "tnt": "TNT",
    "amazon": "Amazon",
    "toll": "Toll",
    "other": "Courier",
}


async def _notify_parcel_residents(
        unit_number: str,
        building_id: str,
        parcel_id: str,
        carrier: str,
        tracking_number: Optional[str] = None,
        description: Optional[str] = None,
) -> None:
    """
    Notify all active residents of a unit when a parcel arrives.

    Steps:
    0. Wrap in workflow_run for observability.
    1. Query user_units (preferred - finds all active owner/tenant occupants).
    2. Batch-fetch their user records for email + name.
    3. Fall back to units.owner_email when no user_units records exist.
    4. Send in-app bell notifications via create_notifications_batch().
    5. Send email notifications via send_email_async() in background tasks.
    6. Push SSE parcel_logged event to connected clients.
    """
    import logging
    from utils.workflow_runner import workflow_run
    _log = logging.getLogger(__name__)

    try:
        async with workflow_run("wf-001", building_id, trigger_detail="parcel_created") as run:
            carrier_label = _CARRIER_DISPLAY.get(carrier, carrier.replace("_", " ").title())
            desc_suffix = f" — {description}" if description else ""

            # ── Step 1: find active occupants via user_units collection ──────────
            # TenantScopedDatabase auto-injects building_id; do NOT add it manually.
            occupants = await db.user_units.find(
                {
                    "unit_number": unit_number,
                    "is_active": True,
                    "role_at_unit": {"$in": ["owner", "tenant"]},
                },
                {"_id": 0, "user_id": 1},
            ).to_list(length=20)

            recipient_user_ids = [o["user_id"] for o in occupants] if occupants else []

            # ── Step 2: batch-fetch user records (users is GLOBAL — no building_id) ──
            users_to_notify: list[dict] = []
            if recipient_user_ids:
                users_to_notify = await db.users.find(
                    {"id": {"$in": recipient_user_ids}, "is_active": True},
                    {"_id": 0, "id": 1, "email": 1, "full_name": 1, "mail_username": 1},
                ).to_list(length=20)

            # If user_units lookup returned no active users, fall back to direct DB
            # lookup in the users collection by unit_number (legacy pattern).
            if not users_to_notify:
                users_to_notify = await db.users.find(
                    {
                        "unit_number": unit_number,
                        "role": {"$in": ["owner", "tenant"]},
                        "is_active": True,
                    },
                    {"_id": 0, "id": 1, "email": 1, "full_name": 1, "mail_username": 1},
                ).to_list(length=20)
                recipient_user_ids = [u["id"] for u in users_to_notify]

            # ── Step 3: fallback — units.owner_email ─────────────────────────────
            fallback_emails: list[dict] = []
            if not users_to_notify:
                unit_doc = await db.units.find_one(
                    {"unit_number": unit_number},
                    {"_id": 0, "owner_email": 1, "owner_name": 1},
                )
                if unit_doc and unit_doc.get("owner_email"):
                    fallback_emails.append(
                        {"email": unit_doc["owner_email"], "name": unit_doc.get("owner_name", "Owner")}
                    )

            # ── Step 4: in-app bell notifications (batch) ────────────────────────
            if users_to_notify:
                notifications = [
                    {
                        "user_id": user["id"],
                        "title": "📦 Parcel Arrived",
                        "message": (
                            f"A {carrier_label} delivery has arrived for Unit {unit_number}"
                            f"{desc_suffix}. Please collect from reception."
                        ),
                        "type": "parcel",
                        "link": "/community/parcels",
                    }
                    for user in users_to_notify
                ]
                run.items_processed = await create_notifications_batch(notifications, building_id=building_id)

            # ── Step 5: email notifications ──────────────────────────────────────
            def _make_html(to_name: str) -> str:
                """Generated function header.

                Function: _make_html
                Path: backend/routers/bookings.py

                Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
                """
                desc_row = (
                    f'<tr><td style="padding:8px;border:1px solid #ddd;"><strong>Description</strong></td>'
                    f'<td style="padding:8px;border:1px solid #ddd;">{description}</td></tr>'
                    if description else ""
                )
                return f"""
                <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
                  <div style="background:#2F4F4F;color:white;padding:24px;border-radius:8px 8px 0 0;">
                    <h2 style="margin:0;">📦 Parcel Arrived</h2>
                  </div>
                  <div style="background:#f9f9f9;padding:24px;border-radius:0 0 8px 8px;">
                    <p>Hi {to_name},</p>
                    <p>A parcel has arrived for <strong>Unit {unit_number}</strong> at East Gate Residences.</p>
                    <table style="border-collapse:collapse;width:100%;margin:16px 0;">
                      <tr><td style="padding:8px;border:1px solid #ddd;"><strong>Carrier</strong></td>
                          <td style="padding:8px;border:1px solid #ddd;">{carrier_label}</td></tr>
                      {desc_row}
                    </table>
                    <p>Please collect your parcel from reception during staffed hours.</p>
                    <p><a href="https://eastgateresidences.com.au/community/parcels">View My Parcels</a></p>
                  </div>
                  <div style="text-align:center;color:#888;font-size:12px;margin-top:16px;">
                    East Gate Residences — Strata Plan 13195, Denman Prospect ACT
                  </div>
                </div>"""

            subject = f"📦 Parcel Arrived — Unit {unit_number}"
            emails_sent: set[str] = set()  # deduplicate

            for user in users_to_notify:
                login_email = user.get("email", "")
                mail_username = user.get("mail_username", "")
                full_name = user.get("full_name", "Resident")

                if login_email and login_email not in emails_sent:
                    emails_sent.add(login_email)
                    asyncio.create_task(
                        send_email_async(
                            to_email=login_email,
                            subject=subject,
                            html_content=_make_html(full_name),
                            context="parcel_notification",
                        )
                    )

                # Add @eastgateresidences.com.au alias if different from login email
                if mail_username:
                    mail_email = (
                        mail_username if "@" in mail_username
                        else f"{mail_username}@eastgateresidences.com.au"
                    )
                    if mail_email != login_email and mail_email not in emails_sent:
                        emails_sent.add(mail_email)
                        asyncio.create_task(
                            send_email_async(
                                to_email=mail_email,
                                subject=subject,
                                html_content=_make_html(full_name),
                                context="parcel_notification",
                            )
                        )

            for fb in fallback_emails:
                if fb["email"] and fb["email"] not in emails_sent:
                    emails_sent.add(fb["email"])
                    asyncio.create_task(
                        send_email_async(
                            to_email=fb["email"],
                            subject=subject,
                            html_content=_make_html(fb["name"]),
                            context="parcel_notification",
                        )
                    )

            # ── Step 6: SSE push ─────────────────────────────────────────────────
            try:
                from routers.sse import push_to_user as _push
                for uid in recipient_user_ids:
                    asyncio.create_task(
                        _push(uid, "parcel_logged", {"unit_number": unit_number, "carrier": carrier_label})
                    )
            except Exception as exc:
                logger.warning("bookings: SSE push failed for parcel notification (unit %s): %s", unit_number, exc)

    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "Failed to send parcel notifications for unit %s", unit_number
        )


# ==================== DEFECT ROUTES ====================


@router.post("/defects", response_model=DefectResponse)
async def create_defect(
        data: DefectCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """
    Report a new defect.

    Creates a defect report with the current user as the reporter.
    """
    defect_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    defect_doc = {
        "id": defect_id,
        "building_id": building_id,
        **data.model_dump(),
        "status": "reported",
        "contractor_id": None,
        "resolution_notes": None,
        "resolved_date": None,
        "reported_by": current_user["id"],
        "reported_by_name": current_user.get("full_name"),
        "created_at": now,
        "updated_at": now,
    }

    await db.defects.insert_one(defect_doc)
    return DefectResponse(**defect_doc)


@router.get("/defects", response_model=List[DefectResponse])
async def get_defects(
        status: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """
    Get a list of defects.

    Can optionally filter by status.
    """
    query = {"building_id": building_id}
    if status:
        query["status"] = status

    defects = (
        await db.defects.find(query, {"_id": 0})
        .sort("created_at", -1)
        .to_list(200)
    )
    return [DefectResponse(**d) for d in defects]


@router.put("/defects/{defect_id}/status")
async def update_defect_status(
        defect_id: str,
        status: str,
        resolution_notes: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """
    Update the status of a defect.

    Requires meeting management permissions. Automatically sets resolved_date
    when status is changed to 'resolved' or 'closed'.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    update_dict = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if resolution_notes:
        update_dict["resolution_notes"] = resolution_notes
    if status in ["resolved", "closed"]:
        update_dict["resolved_date"] = datetime.now(timezone.utc).isoformat()

    await db.defects.update_one(
        {"id": defect_id, "building_id": building_id}, {"$set": update_dict}
    )
    return {"message": "Defect status updated"}


# ==================== MOVE IN/OUT BOOKINGS ====================


@router.post("/move-bookings", response_model=MoveBookingResponse)
async def create_move_booking(
        data: MoveBookingCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
        _feature: dict = Depends(require_feature("bookings")),
):
    """
    Create a new move in/out booking.

    Books elevator and common area access for moving activities.
    """
    booking_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    booking_doc = {
        "id": booking_id,
        "building_id": building_id,
        **data.model_dump(),
        "status": "pending",
        "created_by": current_user["id"],
        "created_at": now,
    }

    await db.move_bookings.insert_one(booking_doc)
    return MoveBookingResponse(**booking_doc)


@router.get("/move-bookings", response_model=List[MoveBookingResponse])
async def get_move_bookings(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
        _feature: dict = Depends(require_feature("bookings")),
):
    """
    Get a list of move bookings.

    Admins can see all bookings, regular users only see their own.
    """
    permissions = get_user_permissions(current_user)

    query = {"building_id": building_id, "status": {"$ne": "cancelled"}, "is_test_data": {"$ne": True}}
    if not permissions.can_manage_users:
        query["created_by"] = current_user["id"]

    limit = 100 if permissions.can_manage_users else 20
    bookings = (
        await db.move_bookings.find(query, {"_id": 0})
        .sort("scheduled_date", -1)
        .to_list(limit)
    )

    return [MoveBookingResponse(**b) for b in bookings]


@router.put("/move-bookings/{booking_id}/status")
async def update_move_booking_status(
        booking_id: str,
        status: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """
    Update the status of a move booking.

    Requires user management permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    await db.move_bookings.update_one(
        {"id": booking_id, "building_id": building_id},
        {"$set": {"status": status}},
    )
    return {"message": "Booking status updated"}


@router.delete("/move-bookings/{booking_id}")
async def delete_move_booking(
        booking_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """
    Hard-delete a move booking. Admins only.
    Used for cleaning up test data and removing erroneous bookings.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.move_bookings.delete_one({"id": booking_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Booking not found")
    return {"message": "Booking deleted"}


# ==================== AMENITY BOOKINGS ====================


@router.post("/amenity-bookings", response_model=AmenityBookingResponse)
async def create_amenity_booking(
        data: AmenityBookingCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
        _feature: dict = Depends(require_feature("bookings")),
):
    """
    Create a new amenity booking.

    Books common amenities such as BBQ area, meeting room, or visitor parking.
    Checks for time slot conflicts before creating the booking.
    """
    # Check for conflicts within the same building, ignoring test data records
    existing = await db.amenity_bookings.find_one(
        {
            "building_id": building_id,
            "amenity_type": data.amenity_type,
            "date": data.date,
            "status": "confirmed",
            "is_test_data": {"$ne": True},
            "$or": [
                {
                    "start_time": {"$lt": data.end_time},
                    "end_time": {"$gt": data.start_time},
                }
            ],
        }
    )

    if existing:
        raise HTTPException(status_code=400, detail="Time slot already booked")

    booking_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    booking_doc = {
        "id": booking_id,
        "building_id": building_id,
        **data.model_dump(),
        "status": "confirmed",
        "booked_by": current_user["id"],
        "booked_by_name": current_user.get("full_name"),
        "unit_number": current_user.get("unit_number", ""),
        "created_at": now,
    }

    await db.amenity_bookings.insert_one(booking_doc)
    return AmenityBookingResponse(**booking_doc)


@router.get("/amenity-bookings", response_model=List[AmenityBookingResponse])
async def get_amenity_bookings(
        amenity_type: Optional[str] = None,
        date: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
        _feature: dict = Depends(require_feature("bookings")),
):
    """
    Get a list of amenity bookings.

    Can filter by amenity type and date.
    """
    query = {"building_id": building_id, "status": {"$ne": "cancelled"}, "is_test_data": {"$ne": True}}
    if amenity_type:
        query["amenity_type"] = amenity_type
    if date:
        query["date"] = date

    bookings = (
        await db.amenity_bookings.find(query, {"_id": 0})
        .sort("date", -1)
        .to_list(100)
    )
    return [AmenityBookingResponse(**b) for b in bookings]


@router.delete("/amenity-bookings/{booking_id}")
async def cancel_amenity_booking(
        booking_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """
    Cancel an amenity booking.

    Users can cancel their own bookings. Admins can cancel any booking.
    """
    booking = await db.amenity_bookings.find_one(
        {"id": booking_id, "building_id": building_id}, {"_id": 0}
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    permissions = get_user_permissions(current_user)
    if (
            booking["booked_by"] != current_user["id"]
            and not permissions.can_manage_users
    ):
        raise HTTPException(status_code=403, detail="Not authorized")

    await db.amenity_bookings.delete_one({"id": booking_id, "building_id": building_id})
    return {"message": "Booking cancelled"}


# ==================== AMENITY MANAGEMENT ====================

DEFAULT_AMENITIES = [
    {"key": "bbq_area", "label": "BBQ Area", "icon": "UtensilsCrossed",
     "description": "Outdoor BBQ and entertaining area"},
    {"key": "meeting_room", "label": "Meeting Room", "icon": "Users", "description": "Conference room for meetings"},
    {"key": "visitor_parking", "label": "Visitor Parking", "icon": "Car", "description": "Visitor parking bays"},
    {"key": "gym", "label": "Gym", "icon": "Dumbbell", "description": "Fitness centre"},
]


@router.get("/amenities")
async def list_amenities(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
        _feature: dict = Depends(require_feature("bookings")),
):
    """List all amenities for this building."""
    amenities = await db.building_amenities.find(
        {"building_id": building_id}, {"_id": 0}
    ).to_list(100)
    if not amenities:
        # Return defaults if not yet configured
        return {"amenities": DEFAULT_AMENITIES, "using_defaults": True}
    return {"amenities": amenities, "using_defaults": False}


@router.post("/amenities")
async def add_amenity(
        data: AmenityCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
        _feature: dict = Depends(require_feature("bookings")),
):
    """Add a new amenity. Requires manager role."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Manager role required")

    existing = await db.building_amenities.find_one({"building_id": building_id, "key": data.key})
    if existing:
        raise HTTPException(status_code=409, detail="Amenity with this key already exists")

    amenity = {
        "id": str(uuid.uuid4()),
        "key": data.key,
        "label": data.label,
        "icon": data.icon,
        "description": data.description or "",
        "building_id": building_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": current_user["id"],
    }
    await db.building_amenities.insert_one(amenity)
    amenity.pop("_id", None)
    return amenity


@router.delete("/amenities/{amenity_key}")
async def remove_amenity(
        amenity_key: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
        _feature: dict = Depends(require_feature("bookings")),
):
    """Remove an amenity. Requires manager role."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Manager role required")

    result = await db.building_amenities.delete_one({"building_id": building_id, "key": amenity_key})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Amenity not found")
    return {"message": "Amenity removed"}


# ==================== PARCEL NOTIFICATIONS ====================


@router.post("/parcels", response_model=ParcelResponse)
async def log_parcel(
        data: ParcelCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """
    Log a received parcel.

    Records parcel delivery for a resident. Requires user management
    permissions or service provider role.
    """
    permissions = get_user_permissions(current_user)
    if (
            not permissions.can_manage_users
            and current_user.get("role") != "service_provider"
    ):
        raise HTTPException(status_code=403, detail="Not authorized")

    parcel_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    parcel_doc = {
        "id": parcel_id,
        "building_id": building_id,
        **data.model_dump(),
        "status": "received",
        "received_date": now,
        "collected_date": None,
        "logged_by": current_user["id"],
        "created_at": now,
    }

    await db.parcels.insert_one(parcel_doc)

    # Emit PARCEL_LOGGED: notify all active residents of the unit
    asyncio.create_task(_notify_parcel_residents(
        unit_number=data.unit_number,
        building_id=building_id,
        parcel_id=parcel_id,
        carrier=data.carrier,
        tracking_number=data.tracking_number,
        description=data.description,
    ))

    return ParcelResponse(**parcel_doc)


@router.get("/parcels", response_model=List[ParcelResponse])
async def get_parcels(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """
    Get a list of parcels.

    Admins can see all parcels, regular users only see parcels for their unit.
    """
    permissions = get_user_permissions(current_user)

    query = {"building_id": building_id}
    if not permissions.can_manage_users:
        query["unit_number"] = current_user.get("unit_number")

    limit = 200 if permissions.can_manage_users else 50
    parcels = (
        await db.parcels.find(query, {"_id": 0})
        .sort("received_date", -1)
        .to_list(limit)
    )

    return [ParcelResponse(**p) for p in parcels]


@router.put("/parcels/{parcel_id}/collected")
async def mark_parcel_collected(
        parcel_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """
    Mark a parcel as collected.

    Updates the parcel status and records collection timestamp.
    """
    now = datetime.now(timezone.utc).isoformat()
    await db.parcels.update_one(
        {"id": parcel_id, "building_id": building_id},
        {"$set": {"status": "collected", "collected_date": now}},
    )
    return {"message": "Parcel marked as collected"}


@router.get("/parcels/{parcel_id}/track")
async def track_parcel_status(
        parcel_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """
    Get live or mock tracking information for a parcel.

    Attempts a live carrier API call if credentials are configured (via
    AUSPOST_API_KEY, DHL_API_KEY, etc.); otherwise returns a simulated
    response so the feature is always exercisable.

    Residents can only track their own unit's parcels; admins can track any.
    """
    parcel = await db.parcels.find_one(
        {"id": parcel_id, "building_id": building_id}, {"_id": 0}
    )
    if not parcel:
        raise HTTPException(status_code=404, detail="Parcel not found")

    permissions = get_user_permissions(current_user)
    if (
            not permissions.can_manage_users
            and current_user.get("role") != "service_provider"
            and parcel.get("unit_number") != current_user.get("unit_number")
    ):
        raise HTTPException(status_code=403, detail="Not authorized")

    tracking_number = parcel.get("tracking_number")
    carrier = parcel.get("carrier", "other")

    if not tracking_number:
        return {
            "parcel_id": parcel_id,
            "carrier": carrier,
            "tracking_number": None,
            "message": "No tracking number recorded for this parcel.",
            "tracking_url": None,
        }

    from services.courier_tracking_service import track_parcel

    result = await track_parcel(carrier, tracking_number)
    return result.to_dict()
