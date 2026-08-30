"""
Maintenance router module.

This module handles all maintenance-related routes including maintenance requests,
contractors, purchase orders, and invoices.
"""

import html as html_lib
import io
import uuid
from datetime import datetime

import asyncio
import logging
import nh3
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from pymongo import ReturnDocument
from typing import List, Optional

from database import db
from models.maintenance import (
    MaintenanceStatus,
    MaintenanceRequestCreate,
    MaintenanceRequestResponse,
    ContractorCreate,
    ContractorResponse,
    PurchaseOrderCreate,
    PurchaseOrderResponse,
    InvoiceCreate,
    InvoiceResponse,
)
from models.user import UserRole
from services.gst_service import get_building_levy_gst_settings
from services.request_catalogue_service import enforce_request_policy
from services.settings_service import get_general_settings_or_default
from utils.activity_helper import log_activity
from utils.auth import get_current_user, get_current_building, effective_role
from utils.route_guards import require_manager_surface
from utils.email import send_email_async, get_email_template
from utils.helpers import create_user_notification, broadcast_user_notification, create_audit_log, get_current_utc_iso
from utils.pdf_generator import generate_purchase_order_pdf, generate_invoice_pdf
from utils.permissions import get_user_permissions, require_feature

# Create router
# Function scoping (2026-08-28) is PER-ROUTE in this file, not on the router.
#
# This module carries four different surfaces behind one empty prefix, and the
# split that matters is /purchase-orders vs /invoices. Raising a PO and engaging a
# contractor is the maintenance manager's job; APPROVING and PAYING a supplier
# invoice is the licensed agent's act under Agents Act 2003 (ACT) pt 7. A blanket
# router-level "maintenance" dependency would have handed invoice payment to every
# maintenance manager — not a widening (they can reach it today) but a narrowing
# this feature exists to make and would have silently skipped.
#
# Narrows nobody until an agency sets function_scoping_enabled. See
# docs/architecture/strata_management_staff_access_model.md.
router = APIRouter(prefix="")

# Logger
logger = logging.getLogger(__name__)


def _mask_maintenance_request(req: dict, current_user: dict, is_admin: bool = False) -> dict:
    """Mask sensitive fields in maintenance request based on user permissions"""
    is_submitter = req.get("submitted_by") == current_user["id"]
    is_assigned = req.get("assigned_contractor") == current_user["id"]
    is_impersonated = "impersonator_id" in current_user

    # Performance Optimization⚡: Using shallow copy for safety and performance.
    # We use dict(req) to prevent mutating the database driver's internal objects
    # while avoiding the high overhead of copy.deepcopy().
    masked_req = dict(req)

    # Mask financial details and IDs if not admin, submitter, or assigned contractor
    if not is_admin and not is_submitter and not is_assigned:
        masked_req["estimated_cost"] = None
        masked_req["actual_cost"] = None
        masked_req["purchase_order_id"] = None
        masked_req["invoice_id"] = None

        # Mask submitter name for non-common area requests not belonging to user
        if req.get("category") != "common_area":
            masked_req["submitted_by_name"] = "Resident"

    # Additional masking for impersonation sessions
    if is_impersonated:
        # Hide sensitive IDs and exact costs completely during impersonation
        masked_req["purchase_order_id"] = None
        masked_req["invoice_id"] = None
        if not is_submitter:
            masked_req["submitted_by"] = "REDACTED"
            masked_req["submitted_by_name"] = "Resident"

    return masked_req


# ==================== MAINTENANCE REQUEST ROUTES ====================

_TEST_DATA_ALLOWED_ROLES = {UserRole.SUPER_ADMIN, UserRole.STRATA_MANAGER}


def _can_mark_maintenance_test_data(current_user: dict) -> bool:
    """Only operational test runners should be able to hide synthetic maintenance rows."""
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    return _role in _TEST_DATA_ALLOWED_ROLES

@router.post("/maintenance", response_model=MaintenanceRequestResponse, dependencies=[Depends(require_manager_surface("maintenance"))])
async def create_maintenance_request(
        data: MaintenanceRequestCreate,
        current_user: dict = Depends(require_feature("maintenance")),
        building_id: str = Depends(get_current_building)
):
    """
    Create a new maintenance request.
    
    Allows users to submit maintenance requests for repairs or issues
    that need attention in the property. Sends a confirmation email to the submitter.
    """
    await enforce_request_policy(current_user, building_id, "maintenance", version=1, stage="submission", db=db)
    req_id = str(uuid.uuid4())
    now = get_current_utc_iso()

    # SECURITY: Sanitize user input to prevent Stored XSS
    sanitized_data = data.model_dump()
    sanitized_data["title"] = html_lib.escape(data.title)
    sanitized_data["description"] = nh3.clean(data.description)
    sanitized_data["category"] = html_lib.escape(data.category)
    sanitized_data["location"] = html_lib.escape(data.location)
    # The k6 benchmark historically creates records with this prefix. Treat it
    # as test data only for trusted operational roles, so residents cannot hide
    # real requests by spoofing either the explicit flag or the title prefix.
    requested_test_data = data.is_test_data or data.title.startswith("Perf test request perf-")
    sanitized_data["is_test_data"] = bool(requested_test_data and _can_mark_maintenance_test_data(current_user))

    req_doc = {
        "id": req_id,
        "building_id": building_id,
        **sanitized_data,
        "status": MaintenanceStatus.SUBMITTED,
        "submitted_by": current_user["id"],
        "submitted_by_name": current_user["full_name"],
        "assigned_contractor": None,
        "contractor_name": None,
        "estimated_cost": None,
        "actual_cost": None,
        "purchase_order_id": None,
        "invoice_id": None,
        "work_order_ids": [],
        "approval_history": [{
            "status": MaintenanceStatus.SUBMITTED,
            "by": current_user["full_name"],
            "at": now,
            "note": "Request submitted"
        }],
        "notes": [],
        "created_at": now,
        "updated_at": now,
        "completed_at": None
    }

    await db.maintenance_requests.insert_one(req_doc)

    if not req_doc.get("is_test_data"):
        # Synthetic performance rows must not fan out into user-facing surfaces
        # or durable audit clutter; they are removed by k6 teardown/safety sweep.
        asyncio.create_task(create_audit_log(
            action="created",
            resource_type="maintenance_request",
            resource_id=req_id,
            user_id=current_user["id"],
            user_name=current_user["full_name"],
            details={"title": data.title},
            building_id=building_id,
        ))

        # Notify admins about new request
        asyncio.create_task(_notify_admins_new_maintenance(req_doc))

        # Send confirmation email to submitter
        asyncio.create_task(_send_maintenance_confirmation_email(current_user, req_doc))

        # Log to community activity feed
        asyncio.create_task(log_activity(
            activity_type="maintenance",
            title=f"New Maintenance Request: {data.title}",
            entity_id=req_id,
            priority=3,
            metadata={"category": data.category, "unit": current_user.get("unit_number")}
        ))

    return MaintenanceRequestResponse(**req_doc)


@router.get("/maintenance", response_model=List[MaintenanceRequestResponse], dependencies=[Depends(require_manager_surface("maintenance"))])
async def get_maintenance_requests(
        status: Optional[str] = None,
        category: Optional[str] = None,
        mine: Optional[bool] = False,
        assigned_to_me: Optional[bool] = False,
        include_test_data: Optional[bool] = False,
        current_user: dict = Depends(require_feature("maintenance")),
        building_id: str = Depends(get_current_building)
):
    """
    Get maintenance requests with optional filtering. Scoped to building context.

    Returns a list of maintenance requests. By default, returns all requests
    to prevent duplicate filings.
    If mine=true, returns only requests submitted by the current user.
    If assigned_to_me=true, returns requests assigned to the current user (for service providers).
    """
    query = {"building_id": building_id}

    # Pre-calculate permissions once - Bolt ⚡
    permissions = get_user_permissions(current_user)
    is_admin = permissions.can_manage_requests or permissions.can_manage_finances
    if not (include_test_data and is_admin):
        query["is_test_data"] = {"$ne": True}

    if mine:
        query["submitted_by"] = current_user["id"]
    elif assigned_to_me:
        # Explicitly filtering for assigned requests
        query["assigned_contractor"] = current_user["id"]
    else:
        # Default visibility filter for non-admin users
        if not is_admin:
            query["$or"] = [
                {"submitted_by": current_user["id"]},
                {"assigned_contractor": current_user["id"]},
                {"category": "common_area"}
            ]

    if status:
        query["status"] = status
    if category:
        query["category"] = category

    # Performance Optimization⚡: Batch retrieval of maintenance requests for better performance.
    requests = await db.maintenance_requests.find(query, {"_id": 0}).sort("created_at", -1).to_list(100)
    return [MaintenanceRequestResponse(**_mask_maintenance_request(request_item, current_user, is_admin)) for
            request_item in requests]


@router.get("/maintenance/{req_id}", response_model=MaintenanceRequestResponse, dependencies=[Depends(require_manager_surface("maintenance"))])
async def get_maintenance_request(
        req_id: str,
        current_user: dict = Depends(require_feature("maintenance")),
        building_id: str = Depends(get_current_building)
):
    """
    Get a specific maintenance request by ID. Scoped to building context.

    Returns details of a single maintenance request. All authenticated users
    can view any maintenance request to prevent duplicate filings.
    """
    req = await db.maintenance_requests.find_one({"id": req_id, "building_id": building_id}, {"_id": 0})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    # Authorization check
    permissions = get_user_permissions(current_user)
    is_admin = permissions.can_manage_requests or permissions.can_manage_finances
    is_submitter = req.get("submitted_by") == current_user["id"]
    is_common_area = req.get("category") == "common_area"
    is_assigned = req.get("assigned_contractor") == current_user["id"]

    if not is_admin and not is_submitter and not is_common_area and not is_assigned:
        raise HTTPException(
            status_code=403,
            detail="You are not authorized to view this maintenance request"
        )

    return MaintenanceRequestResponse(**_mask_maintenance_request(req, current_user, is_admin))


@router.put("/maintenance/{req_id}/status", dependencies=[Depends(require_manager_surface("maintenance"))])
async def update_maintenance_status(
        req_id: str,
        status: str,
        note: Optional[str] = None,
        contractor_id: Optional[str] = None,
        estimated_cost: Optional[float] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Update the status of a maintenance request.
    
    Allows admin users to update the status of a maintenance request,
    assign contractors, and set estimated costs.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_requests:
        raise HTTPException(status_code=403, detail="Not authorized")

    req = await db.maintenance_requests.find_one({"id": req_id, "building_id": building_id})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    now = get_current_utc_iso()

    update_data = {
        "status": status,
        "updated_at": now
    }

    if contractor_id:
        contractor = await db.contractors.find_one({
            "id": contractor_id,
            "building_id": building_id
        }, {"_id": 0})
        if contractor:
            update_data["assigned_contractor"] = contractor_id
            update_data["contractor_name"] = contractor["name"]

    if estimated_cost is not None:
        update_data["estimated_cost"] = estimated_cost

    if status == MaintenanceStatus.COMPLETED:
        update_data["completed_at"] = now

    # SECURITY: Sanitize user input to prevent Stored XSS
    sanitized_note = nh3.clean(note) if note else None

    # Add to approval history
    history_entry = {
        "status": status,
        "by": current_user["full_name"],
        "at": now,
        "note": sanitized_note or f"Status updated to {status}"
    }

    await db.maintenance_requests.update_one(
        {"id": req_id, "building_id": building_id},
        {"$set": update_data, "$push": {"approval_history": history_entry}}
    )

    # Create audit log
    asyncio.create_task(create_audit_log(
        action="status_updated",
        resource_type="maintenance_request",
        resource_id=req_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"status": status, "note": sanitized_note}
    ))

    # Create in-app notification for submitter
    asyncio.create_task(create_user_notification(
        user_id=req["submitted_by"],
        title="Maintenance Request Updated",
        message=f"Your request '{req['title']}' is now: {status.replace('_', ' ').title()}",
        notification_type="maintenance",
        link=f"/maintenance/{req_id}"
    ))

    # Send email notification to request submitter
    asyncio.create_task(_send_maintenance_status_update_email(req_id, status, note))

    # Log to community activity feed if completed
    if status == MaintenanceStatus.COMPLETED:
        asyncio.create_task(log_activity(
            activity_type="maintenance",
            title=f"Maintenance Completed: {req['title']}",
            entity_id=req_id,
            priority=3,
            metadata={"category": req.get("category")}
        ))

    return {"message": "Status updated successfully"}


@router.post("/maintenance/{req_id}/notes", dependencies=[Depends(require_manager_surface("maintenance"))])
async def add_maintenance_note(
        req_id: str,
        note: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Add a note to a maintenance request. Scoped to building context.
    Fixes IDOR: Ensures only authorized users can add notes.
    """
    req = await db.maintenance_requests.find_one({"id": req_id, "building_id": building_id})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    # SECURITY FIX: IDOR Authorization check
    permissions = get_user_permissions(current_user)
    is_admin = permissions.can_manage_requests or permissions.can_manage_finances
    is_submitter = req.get("submitted_by") == current_user["id"]
    is_assigned = req.get("assigned_contractor") == current_user["id"]

    if not (is_admin or is_submitter or is_assigned):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to add notes to this request"
        )

    now = get_current_utc_iso()
    # SECURITY: Sanitize user input to prevent Stored XSS
    sanitized_note = nh3.clean(note)

    note_entry = {
        "by": current_user["full_name"],
        "at": now,
        "content": sanitized_note
    }

    await db.maintenance_requests.update_one(
        {"id": req_id, "building_id": building_id},
        {"$push": {"notes": note_entry}, "$set": {"updated_at": now}}
    )

    # Audit log
    asyncio.create_task(create_audit_log(
        action="note_added",
        resource_type="maintenance_request",
        resource_id=req_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"note_preview": sanitized_note[:50]}
    ))
    if req:
        # If admin added a note, notify the submitter. If submitter added a note, notify admins?
        # For now, let's notify the other party.
        target_user_id = None
        if current_user["id"] != req["submitted_by"]:
            target_user_id = req["submitted_by"]
            title = "New Note on Maintenance Request"
        else:
            # Notify admins? (super_admin, chairman)
            # This could be noisy, maybe skip for now or notify assigned contractor if any.
            pass

        if target_user_id:
            asyncio.create_task(create_user_notification(
                user_id=target_user_id,
                title="New Note on Maintenance Request",
                message=f"A new note was added to your request '{req['title']}' by {current_user['full_name']}",
                notification_type="maintenance",
                link=f"/maintenance/{req_id}"
            ))

    return {"message": "Note added successfully"}


_DELETE_ALLOWED_ROLES = {UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER}


@router.delete("/maintenance/{req_id}", dependencies=[Depends(require_manager_surface("maintenance"))])
async def delete_maintenance_request(
        req_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Delete a maintenance request. Restricted to super_admin, chairman, strata_manager.
    Intended for removing test/erroneous entries.
    """
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in _DELETE_ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Only senior managers can delete maintenance requests")

    req = await db.maintenance_requests.find_one({"id": req_id, "building_id": building_id})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    await db.maintenance_requests.delete_one({"id": req_id, "building_id": building_id})

    if not req.get("is_test_data"):
        asyncio.create_task(create_audit_log(
            action="deleted",
            resource_type="maintenance_request",
            resource_id=req_id,
            user_id=current_user["id"],
            user_name=current_user["full_name"],
            details={"title": req.get("title")},
            building_id=building_id,
        ))

    return {"message": "Maintenance request deleted"}


# ==================== CONTRACTOR ROUTES ====================

@router.post("/contractors", response_model=ContractorResponse, dependencies=[Depends(require_manager_surface("contractors"))])
async def create_contractor(
        data: ContractorCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create a new contractor.
    
    Allows admin users to add new contractors to the system for
    maintenance work assignments.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_requests:
        raise HTTPException(status_code=403, detail="Not authorized")

    contractor_id = str(uuid.uuid4())
    now = get_current_utc_iso()

    contractor_doc = {
        "id": contractor_id,
        "building_id": building_id,
        **data.model_dump(),
        "total_jobs": 0,
        "total_spent": 0,
        "rating": None,
        "ratings": [],
        "created_at": now
    }

    await db.contractors.insert_one(contractor_doc)
    return ContractorResponse(**contractor_doc)


@router.get("/contractors", response_model=List[ContractorResponse], dependencies=[Depends(require_manager_surface("contractors"))])
async def get_contractors(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get all contractors.
    
    Returns a list of all contractors in the system. Only available
    to users with admin permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_requests:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Performance Optimization⚡: Batch fetch of contractors.
    contractors = await db.contractors.find({"building_id": building_id}, {"_id": 0}).to_list(100)
    return [ContractorResponse(**c) for c in contractors]


@router.post("/contractors/{contractor_id}/rate", dependencies=[Depends(require_manager_surface("contractors"))])
async def rate_contractor(
        contractor_id: str,
        quality: int,
        timeliness: int,
        communication: int,
        value: int,
        comment: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Rate a contractor. Only Strata Managers and EC members can rate."""
    if effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Only Managers and EC members can rate contractors")

    avg_rating = (quality + timeliness + communication + value) / 4.0
    now = get_current_utc_iso()

    rating_entry = {
        "id": str(uuid.uuid4()),
        "rated_by": current_user["id"],
        "rated_by_name": current_user["full_name"],
        "quality": quality,
        "timeliness": timeliness,
        "communication": communication,
        "value": value,
        "average": avg_rating,
        "comment": comment,
        "timestamp": now
    }

    # Performance Optimization⚡: Atomically push rating and fetch updated list in 1 round-trip instead of 2.
    contractor = await db.contractors.find_one_and_update(
        {"id": contractor_id, "building_id": building_id},
        {"$push": {"ratings": rating_entry}},
        return_document=ReturnDocument.AFTER
    )

    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    # Recalculate overall rating
    ratings = contractor.get("ratings", [])
    if ratings:
        new_overall = sum(r["average"] for r in ratings) / len(ratings)
        await db.contractors.update_one(
            {"id": contractor_id, "building_id": building_id},
            {"$set": {"rating": new_overall}}
        )

    return {"message": "Rating submitted successfully", "overall_rating": avg_rating}


@router.put("/contractors/{contractor_id}", response_model=ContractorResponse, dependencies=[Depends(require_manager_surface("contractors"))])
async def update_contractor(
        contractor_id: str,
        data: ContractorCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Update contractor information.
    
    Allows admin users to update the details of an existing contractor.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_requests:
        raise HTTPException(status_code=403, detail="Not authorized")

    await db.contractors.update_one(
        {"id": contractor_id, "building_id": building_id},
        {"$set": data.model_dump()}
    )

    updated = await db.contractors.find_one({"id": contractor_id, "building_id": building_id}, {"_id": 0})
    if not updated:
        raise HTTPException(status_code=404, detail="Contractor not found")

    return ContractorResponse(**updated)


# ==================== PURCHASE ORDER ROUTES ====================

@router.post("/purchase-orders", response_model=PurchaseOrderResponse, dependencies=[Depends(require_manager_surface("contractors"))])
async def create_purchase_order(
        data: PurchaseOrderCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create a new purchase order.
    
    Creates a purchase order for maintenance work, linked to a maintenance
    request and contractor. Only available to users with finance permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Performance Optimization⚡: Parallelize independent DB reads to reduce latency
    contractor_task = db.contractors.find_one({"id": data.contractor_id, "building_id": building_id})
    count_task = db.purchase_orders.count_documents({"building_id": building_id})

    contractor, count = await asyncio.gather(contractor_task, count_task)

    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    po_id = str(uuid.uuid4())
    now = get_current_utc_iso()

    # Generate PO number (scoped to building)
    po_number = f"PO-{datetime.now().year}-{count + 1:04d}"

    po_doc = {
        "id": po_id,
        "building_id": building_id,
        "po_number": po_number,
        **data.model_dump(),
        "contractor_name": contractor["name"],
        "status": "draft",
        "created_by": current_user["id"],
        "created_at": now
    }

    await db.purchase_orders.insert_one(po_doc)

    # Update maintenance request
    await db.maintenance_requests.update_one(
        {"id": data.maintenance_request_id, "building_id": building_id},
        {"$set": {"purchase_order_id": po_id, "estimated_cost": data.amount}}
    )

    return PurchaseOrderResponse(**po_doc)


@router.get("/purchase-orders", response_model=List[PurchaseOrderResponse], dependencies=[Depends(require_manager_surface("contractors"))])
async def get_purchase_orders(
        status: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get all purchase orders with optional status filtering.
    
    Returns a list of purchase orders. Only available to users with
    view finance permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    query = {"building_id": building_id}
    if status:
        query["status"] = status

    pos = await db.purchase_orders.find(query, {"_id": 0}).sort("created_at", -1).to_list(100)
    return [PurchaseOrderResponse(**p) for p in pos]


@router.get("/purchase-orders/{po_id}/pdf", dependencies=[Depends(require_manager_surface("contractors"))])
async def get_purchase_order_pdf(
        po_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Generate and download a purchase order as PDF.
    
    Returns a PDF file of the purchase order for printing or record keeping.
    Only available to users with view finance permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    po = await db.purchase_orders.find_one({"id": po_id, "building_id": building_id}, {"_id": 0})
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    contractor = await db.contractors.find_one({"id": po["contractor_id"], "building_id": building_id}, {"_id": 0})

    gst_config = await get_building_levy_gst_settings(building_id)
    settings = await get_general_settings_or_default(building_id)
    pdf_bytes = generate_purchase_order_pdf(
        po,
        contractor or {},
        building_settings=settings,
        gst_rate=gst_config["levy_gst_rate"],
        gst_registered=gst_config["gst_registered"],
    )
    if not pdf_bytes:
        raise HTTPException(status_code=500, detail="PDF generation not available")

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={po['po_number']}.pdf"}
    )


# ==================== INVOICE ROUTES ====================

@router.post("/invoices", response_model=InvoiceResponse, dependencies=[Depends(require_manager_surface("invoices"))])
async def create_invoice(
        data: InvoiceCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create a new invoice.
    
    Creates an invoice linked to a purchase order for completed work.
    Only available to users with finance management permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    po = await db.purchase_orders.find_one({"id": data.purchase_order_id, "building_id": building_id})
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    inv_id = str(uuid.uuid4())
    now = get_current_utc_iso()

    # Generate invoice number
    count = await db.invoices.count_documents({"building_id": building_id})
    invoice_number = data.invoice_number or f"INV-{datetime.now().year}-{count + 1:04d}"

    inv_doc = {
        "id": inv_id,
        "building_id": building_id,
        "invoice_number": invoice_number,
        "purchase_order_id": data.purchase_order_id,
        "po_number": po["po_number"],
        "contractor_id": po["contractor_id"],
        "contractor_name": po["contractor_name"],
        "amount": data.amount,
        "gst_amount": data.gst_amount,
        "total_amount": data.amount + data.gst_amount,
        "status": "pending",
        "notes": data.notes,
        "approved_by": None,
        "approved_at": None,
        "paid_at": None,
        "created_at": now
    }

    await db.invoices.insert_one(inv_doc)

    # Update maintenance request
    await db.maintenance_requests.update_one(
        {"id": po["maintenance_request_id"], "building_id": building_id},
        {"$set": {"invoice_id": inv_id, "actual_cost": data.amount + data.gst_amount}}
    )

    return InvoiceResponse(**inv_doc)


@router.get("/invoices", response_model=List[InvoiceResponse], dependencies=[Depends(require_manager_surface("invoices"))])
async def get_invoices(
        status: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get all invoices with optional status filtering.
    
    Returns a list of invoices. Only available to users with
    view finance permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    query = {"building_id": building_id}
    if status:
        query["status"] = status

    invoices = await db.invoices.find(query, {"_id": 0}).sort("created_at", -1).to_list(100)
    return [InvoiceResponse(**invoice_item) for invoice_item in invoices]


@router.get("/invoices/pending-approvals", response_model=List[InvoiceResponse], dependencies=[Depends(require_manager_surface("invoices"))])
async def get_pending_invoice_approvals(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Get all invoices awaiting approval (for EC members and admins)"""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized to view pending approvals")

    # Get invoices with pending approval status
    invoices = await db.invoices.find(
        {
            "building_id": building_id,
            "$or": [
                {"approval_status": "pending"},
                {"status": "pending"}
            ]
        },
        {"_id": 0}
    ).sort("created_at", -1).to_list(100)

    return [InvoiceResponse(**inv) for inv in invoices]


@router.put("/invoices/{inv_id}/approve", dependencies=[Depends(require_manager_surface("invoices"))])
async def approve_invoice(
        inv_id: str,
        background_tasks: BackgroundTasks,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Approve an invoice for payment.
    
    Marks an invoice as approved. Only available to users with
    finance management permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    invoice_data, po, maintenance_req, creator = await _fetch_invoice_with_context(inv_id, building_id)

    now = get_current_utc_iso()

    # Perform atomic update
    result = await db.invoices.update_one(
        {"id": inv_id, "building_id": building_id},
        {
            "$set": {
                "status": "approved",
                "approval_status": "approved",
                "approved_by": current_user["id"],
                "approved_by_name": current_user["full_name"],
                "approved_at": now
            },
            "$push": {
                "approval_history": {
                    "action": "approved",
                    "performed_by_id": current_user["id"],
                    "performed_by_name": current_user["full_name"],
                    "performed_at": now,
                    "reason": "Approved by EC/Admin"
                }
            }
        }
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Invoice not found or already approved")

    # Update local invoice_data for notification consistency
    invoice_data["status"] = "approved"
    invoice_data["approval_status"] = "approved"

    asyncio.create_task(create_audit_log(
        action="approved", resource_type="invoice", resource_id=inv_id,
        user_id=current_user["id"], user_name=current_user["full_name"],
        details={"invoice_number": invoice_data.get("invoice_number"),
                 "amount": invoice_data.get("total_amount")},
        building_id=building_id
    ))

    # Notify relevant users via background task with pre-fetched context
    background_tasks.add_task(
        _notify_invoice_update,
        invoice_data, "approved", current_user["full_name"],
        po, maintenance_req, creator
    )

    return {"message": "Invoice approved successfully"}


@router.put("/invoices/{inv_id}/reject", dependencies=[Depends(require_manager_surface("invoices"))])
async def reject_invoice(
        inv_id: str,
        reason: str,
        background_tasks: BackgroundTasks,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Reject an invoice with a reason"""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    if not reason or len(reason.strip()) < 5:
        raise HTTPException(status_code=400, detail="Rejection reason must be at least 5 characters")

    invoice_data, po, maintenance_req, creator = await _fetch_invoice_with_context(inv_id, building_id)

    now = get_current_utc_iso()

    # Perform atomic update
    result = await db.invoices.update_one(
        {"id": inv_id, "building_id": building_id},
        {
            "$set": {
                "status": "rejected",
                "approval_status": "rejected",
                "rejected_by": current_user["id"],
                "rejected_by_name": current_user["full_name"],
                "rejected_at": now,
                "rejected_reason": reason
            },
            "$push": {
                "approval_history": {
                    "action": "rejected",
                    "performed_by_id": current_user["id"],
                    "performed_by_name": current_user["full_name"],
                    "performed_at": now,
                    "reason": reason
                }
            }
        }
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Invoice not found or already processed")

    # Update local invoice_data for notification consistency
    invoice_data["status"] = "rejected"
    invoice_data["approval_status"] = "rejected"
    invoice_data["rejected_reason"] = reason

    asyncio.create_task(create_audit_log(
        action="rejected", resource_type="invoice", resource_id=inv_id,
        user_id=current_user["id"], user_name=current_user["full_name"],
        details={"invoice_number": invoice_data.get("invoice_number"),
                 "amount": invoice_data.get("total_amount"), "reason": reason},
        building_id=building_id
    ))

    # Notify relevant users via background task with pre-fetched context
    background_tasks.add_task(
        _notify_invoice_update,
        invoice_data, "rejected", current_user["full_name"],
        po, maintenance_req, creator, reason
    )

    return {"message": "Invoice rejected successfully"}


@router.put("/invoices/{inv_id}/pay", dependencies=[Depends(require_manager_surface("invoices"))])
async def mark_invoice_paid(
        inv_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Mark an invoice as paid.
    
    Records an invoice as paid, updates contractor statistics, and
    creates a finance entry. Only available to users with finance
    management permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    now = get_current_utc_iso()

    invoice = await db.invoices.find_one({"id": inv_id, "building_id": building_id}, {"_id": 0})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    await db.invoices.update_one(
        {"id": inv_id, "building_id": building_id},
        {"$set": {"status": "paid", "paid_at": now}}
    )

    # Update contractor stats
    await db.contractors.update_one(
        {"id": invoice["contractor_id"], "building_id": building_id},
        {"$inc": {"total_jobs": 1, "total_spent": invoice["total_amount"]}}
    )

    # Record in expense_transactions (canonical SSoT for all expenses)
    await db.expense_transactions.insert_one({
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "financial_year": now[:4],
        "fund_type": "administrative",
        "category": "Building Repairs & Maintenance",
        "description": f"Invoice {invoice['invoice_number']} - {invoice['contractor_name']}",
        "amount": invoice["total_amount"],
        "date": now[:10],
        "supplier": invoice.get("contractor_name"),
        "reference": invoice["invoice_number"],
        "unit_number": None,
        "data_source": "maintenance_invoice",
        "created_by": current_user["id"],
        "created_at": now,
        "updated_at": now,
    })

    asyncio.create_task(create_audit_log(
        action="paid", resource_type="invoice", resource_id=inv_id,
        user_id=current_user["id"], user_name=current_user["full_name"],
        details={"invoice_number": invoice.get("invoice_number"),
                 "amount": invoice.get("total_amount"),
                 "contractor": invoice.get("contractor_name")},
        building_id=building_id
    ))

    return {"message": "Invoice marked as paid"}


@router.get("/invoices/{inv_id}/pdf", dependencies=[Depends(require_manager_surface("invoices"))])
async def get_invoice_pdf(
        inv_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Generate and download an invoice as PDF.
    
    Returns a PDF file of the invoice for printing or record keeping.
    Only available to users with view finance permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    invoice = await db.invoices.find_one({"id": inv_id, "building_id": building_id}, {"_id": 0})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    po = await db.purchase_orders.find_one({"id": invoice["purchase_order_id"], "building_id": building_id}, {"_id": 0})
    contractor = await db.contractors.find_one({"id": invoice["contractor_id"], "building_id": building_id}, {"_id": 0})

    settings = await get_general_settings_or_default(building_id)
    pdf_bytes = generate_invoice_pdf(invoice, po or {}, contractor or {}, building_settings=settings)
    if not pdf_bytes:
        raise HTTPException(status_code=500, detail="PDF generation not available")

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={invoice['invoice_number']}.pdf"}
    )


# ==================== HELPER FUNCTIONS FOR EMAIL NOTIFICATIONS ====================

async def _fetch_invoice_with_context(inv_id: str, building_id: str) -> tuple:
    """
    Fetch an invoice along with its related purchase order, maintenance request,
    and submitter in a single aggregation query.

    Uses a $lookup pipeline to avoid multiple round-trips and race conditions
    in background notification tasks.

    Returns:
        tuple: (invoice_data, po, maintenance_req, creator) dicts,
               or raises HTTPException 404 if invoice not found.
    """
    pipeline = [
        {"$match": {"id": inv_id, "building_id": building_id}},
        # Join Purchase Order
        {"$lookup": {
            "from": "purchase_orders",
            "localField": "purchase_order_id",
            "foreignField": "id",
            "as": "po_list"
        }},
        {"$addFields": {"po": {"$arrayElemAt": ["$po_list", 0]}}},
        # Join Maintenance Request (linked from PO)
        {"$lookup": {
            "from": "maintenance_requests",
            "localField": "po.maintenance_request_id",
            "foreignField": "id",
            "as": "req_list"
        }},
        {"$addFields": {"maintenance_req": {"$arrayElemAt": ["$req_list", 0]}}},
        # Join Submitter/Creator (linked from Request)
        {"$lookup": {
            "from": "users",
            "localField": "maintenance_req.submitted_by",
            "foreignField": "id",
            "as": "creator_list"
        }},
        {"$addFields": {"creator": {"$arrayElemAt": ["$creator_list", 0]}}},
        {"$project": {"_id": 0, "po_list": 0, "req_list": 0, "creator_list": 0}}
    ]

    agg_results = await db.invoices.aggregate(pipeline).to_list(1)
    if not agg_results:
        raise HTTPException(status_code=404, detail="Invoice not found")

    invoice_data = agg_results[0]
    po = invoice_data.pop("po", {})
    maintenance_req = invoice_data.pop("maintenance_req", {})
    creator = invoice_data.pop("creator", {})
    return invoice_data, po, maintenance_req, creator


async def _send_maintenance_confirmation_email(user: dict, request_doc: dict):
    """Send confirmation email when maintenance request is created"""
    try:
        # Check user's notification preferences
        prefs = await db.email_notification_preferences.find_one({"user_id": user["id"]})
        if prefs and not prefs.get("maintenance_updates_enabled", True):
            return

        # Generate email content
        html, text = get_email_template(
            "maintenance_update",
            request_title=request_doc["title"],
            status="Submitted",
            message=f"Your maintenance request has been received and is being reviewed by the management team.\n\nCategory: {request_doc['category']}\nPriority: {request_doc['priority']}\nLocation: {request_doc['location']}\n\nYou will be notified of any updates."
        )

        subject = f"Maintenance Request Received: {request_doc['title']}"

        await send_email_async(user["email"], subject, html, text)

    except Exception as e:
        logger.error(f"Failed to send maintenance confirmation email: {str(e)}")


async def _notify_admins_new_maintenance(request_doc: dict):
    """Notify all users about a new maintenance request"""
    try:
        # User wants all users to see these notifications now
        await broadcast_user_notification(
            recipient_roles=["all"],
            title="New Maintenance Request",
            message=f"New request '{request_doc['title']}' from {request_doc['submitted_by_name']}",
            notification_type="maintenance",
            link=f"/maintenance/{request_doc['id']}",
            building_id=request_doc.get("building_id")  # Assuming broadcast_user_notification will support this
        )
    except Exception as e:
        logger.error(f"Failed to notify users about new maintenance: {str(e)}")


async def _notify_invoice_update(
        invoice: dict,
        status: str,
        by_name: str,
        po: dict,
        maintenance_req: dict,
        creator: dict,
        reason: str = None
):
    """
    Notify users of an invoice update (in-app and email).
    Consolidated into a single background task to reduce main endpoint latency. - Bolt ⚡

    Context (PO, Request, Creator) is pre-fetched in the main endpoint to avoid race conditions.
    """
    try:
        # 1. In-app notification
        target_user_id = maintenance_req.get("submitted_by")
        if target_user_id:
            await create_user_notification(
                user_id=target_user_id,
                title=f"Invoice {status.title()}",
                message=f"Invoice {invoice['invoice_number']} for '{maintenance_req['title']}' was {status} by {by_name}.{f' Reason: {reason}' if reason else ''}",
                notification_type="maintenance",
                link=f"/maintenance/{maintenance_req['id']}"
            )

            # 2. Email notification (Hoisted from main endpoints)
            if creator and creator.get("email"):
                safe_full_name = html_lib.escape(creator.get('full_name', 'User'))
                safe_invoice_num = html_lib.escape(invoice['invoice_number'])
                safe_by_name = html_lib.escape(by_name)
                safe_contractor = html_lib.escape(invoice.get('contractor_name', 'N/A'))
                safe_now = html_lib.escape(get_current_utc_iso())

                if status == "approved":
                    subject = f"Invoice {invoice['invoice_number']} Approved"
                    html_content = f"""
                    <h2>Invoice Approved</h2>
                    <p>Hello {safe_full_name},</p>
                    <p>Invoice <strong>{safe_invoice_num}</strong> has been approved by {safe_by_name}.</p>
                    <p><strong>Details:</strong></p>
                    <ul>
                        <li>Amount: ${invoice.get('total_amount', 0):,.2f}</li>
                        <li>Contractor: {safe_contractor}</li>
                        <li>Approved At: {safe_now}</li>
                    </ul>
                    <p>The invoice will be processed for payment.</p>
                    """
                else:  # rejected
                    safe_reason = html_lib.escape(reason or "No reason provided")
                    subject = f"Invoice {invoice['invoice_number']} Rejected"
                    html_content = f"""
                    <h2>Invoice Rejected</h2>
                    <p>Hello {safe_full_name},</p>
                    <p>Invoice <strong>{safe_invoice_num}</strong> has been rejected by {safe_by_name}.</p>
                    <p><strong>Details:</strong></p>
                    <ul>
                        <li>Amount: ${invoice.get('total_amount', 0):,.2f}</li>
                        <li>Contractor: {safe_contractor}</li>
                        <li>Rejected At: {safe_now}</li>
                        <li>Reason: {safe_reason}</li>
                    </ul>
                    <p>Please review the reason and take appropriate action.</p>
                    """

                await send_email_async(
                    to_email=creator["email"],
                    subject=subject,
                    html_content=html_content
                )
    except Exception as e:
        logger.error(f"Failed to notify invoice update: {str(e)}")


async def _send_maintenance_status_update_email(request_id: str, new_status: str, note: Optional[str] = None):
    """Send email notification when maintenance request status changes"""
    try:
        # Get the request and submitter details
        request_doc = await db.maintenance_requests.find_one({"id": request_id}, {"_id": 0})
        if not request_doc:
            return

        submitter = await db.users.find_one(
            {"id": request_doc["submitted_by"]},
            {"_id": 0, "id": 1, "email": 1, "full_name": 1}
        )

        if not submitter:
            return

        # Check user's notification preferences
        prefs = await db.email_notification_preferences.find_one({"user_id": submitter["id"]})
        if prefs and not prefs.get("maintenance_updates_enabled", True):
            return

        # Build message based on status
        status_messages = {
            MaintenanceStatus.UNDER_REVIEW: "Your maintenance request is now being reviewed by the management team.",
            MaintenanceStatus.APPROVED: "Your maintenance request has been approved and will be scheduled soon.",
            MaintenanceStatus.REJECTED: "Your maintenance request has been reviewed. Please contact management for more information.",
            MaintenanceStatus.IN_PROGRESS: f"Work has commenced on your maintenance request.{' Contractor: ' + request_doc.get('contractor_name', '') if request_doc.get('contractor_name') else ''}",
            MaintenanceStatus.COMPLETED: "Your maintenance request has been completed. Thank you for your patience."
        }

        message = status_messages.get(new_status, f"Status updated to: {new_status}")
        if note:
            message += f"\n\nNote: {note}"

        # Generate email content
        html, text = get_email_template(
            "maintenance_update",
            request_title=request_doc["title"],
            status=new_status.replace("_", " ").title(),
            message=message
        )

        subject = f"Maintenance Request Update: {request_doc['title']}"

        await send_email_async(submitter["email"], subject, html, text)

    except Exception as e:
        logger.error(f"Failed to send maintenance status update email: {str(e)}")


__all__ = ["router"]
