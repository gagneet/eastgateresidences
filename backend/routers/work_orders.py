"""
Work Order router module.

This module handles the full lifecycle management of Work Orders, including
creation from Maintenance Requests, quote collection, EC approvals,
vendor assignment, and invoice processing.
"""

import html as html_lib
import hmac
import os
import uuid
from datetime import datetime, timezone

import asyncio
import logging
import nh3
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Header
from typing import List, Optional, Dict, Any

from database import db
from models.user import UserRole, ECPosition
from models.work_order import (
    WorkOrderStatus, WorkOrderCreate, WorkOrderResponse,
    QuoteCreate, QuoteResponse, QuoteStatus, WorkOrderAttachmentCreate, WorkOrderAttachmentResponse,
    WorkOrderCommunicationResponse,
    WorkOrderApprovalCreate, WorkOrderApprovalResponse, WorkOrderApprovalDecision,
    WorkOrderInvoiceCreate, WorkOrderInvoiceResponse,
    RecurringWorkOrderCreate, RecurringWorkOrderResponse, CommitteeResolution,
    WorkOrderSWMSUpdate, WorkOrderPhotoAdd,
)
from utils.activity_helper import log_activity
from utils.auth import get_approved_user, get_current_building, get_optional_building
from utils.helpers import create_audit_log, create_notifications_batch, create_user_notification
from utils.permissions import get_user_permissions, require_feature
from services.request_catalogue_service import enforce_request_policy

# GAP-FT-004: Gate the work orders router behind the work_orders feature toggle.
router = APIRouter(
    prefix="/work-orders",
    tags=["Work Orders"],
    dependencies=[Depends(require_feature("work_orders"))],
)
logger = logging.getLogger(__name__)


# ==================== WORK ORDER ROUTES ====================

@router.post("", response_model=WorkOrderResponse)
async def create_work_order(
        data: WorkOrderCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Create a new work order from a maintenance request."""
    await enforce_request_policy(current_user, building_id, "work-order", version=1, stage="submission", db=db)
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings and not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized to create work orders")

    wo_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # SECURITY: Sanitize user input to prevent Stored XSS
    sanitized_title = html_lib.escape(data.title)
    sanitized_description = nh3.clean(data.description)
    sanitized_lot = html_lib.escape(data.lot_number) if data.lot_number else None

    # Check if maintenance request exists - BOLA: filter by building_id
    request = await db.maintenance_requests.find_one({"id": data.maintenance_request_id, "building_id": building_id})
    if not request:
        raise HTTPException(status_code=404, detail="Maintenance request not found")

    wo_doc = {
        "id": wo_id,
        **data.model_dump(),
        "title": sanitized_title,
        "description": sanitized_description,
        "lot_number": sanitized_lot,
        "building_id": building_id,  # SECURITY: Explicitly override building_id AFTER expansion
        "status": WorkOrderStatus.NEW if not data.is_emergency else WorkOrderStatus.APPROVED,
        "created_by": current_user["id"],
        "assigned_vendor": None,
        "vendor_name": None,
        "approval_status": "none",
        "completion_date": None,
        "created_at": now,
        "updated_at": now
    }

    await db.work_orders.insert_one(wo_doc)

    # Link to maintenance request and advance its status to in_progress
    await db.maintenance_requests.update_one(
        {"id": data.maintenance_request_id, "building_id": building_id},
        {
            "$push": {"work_order_ids": wo_id},
            "$set": {"status": "in_progress", "updated_at": now}
        }
    )

    # Audit log
    await create_audit_log(
        "created",
        "work_order",
        wo_id,
        current_user["id"],
        current_user["full_name"],
        {"title": sanitized_title, "request_id": data.maintenance_request_id},
        building_id
    )

    return WorkOrderResponse(**wo_doc)


@router.get("", response_model=List[WorkOrderResponse])
async def get_work_orders(
        status: Optional[str] = None,
        maintenance_request_id: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """List work orders with filtering."""
    query = {"building_id": building_id}
    if status:
        query["status"] = status
    if maintenance_request_id:
        query["maintenance_request_id"] = maintenance_request_id

    # RBAC: Non-admin users might only see WOs related to their requests or lot
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings and not permissions.can_manage_finances:
        # If not admin, only see WOs for their own lot (if lot_number matches unit_number)
        if current_user.get("unit_number"):
            query["lot_number"] = current_user["unit_number"]
        else:
            return []

    work_orders = await db.work_orders.find(query, {"_id": 0}).sort("created_at", -1).to_list(100)
    return [WorkOrderResponse(**wo) for wo in work_orders]


@router.put("/{wo_id}", response_model=WorkOrderResponse)
async def update_work_order(
        wo_id: str,
        data: Dict[str, Any],
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Update work order details. restricted to managers/EC."""
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    update_dict = {k: v for k, v in data.items() if
                   k in ["title", "description", "priority", "emergency_override", "status"]}

    # SECURITY: Sanitize user input to prevent Stored XSS
    if "title" in update_dict:
        update_dict["title"] = html_lib.escape(update_dict["title"])
    if "description" in update_dict:
        update_dict["description"] = nh3.clean(update_dict["description"])

    update_dict["updated_at"] = datetime.now(timezone.utc).isoformat()

    result = await db.work_orders.update_one({"id": wo_id, "building_id": building_id}, {"$set": update_dict})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Work order not found")

    # Audit log
    await create_audit_log(
        "updated",
        "work_order",
        wo_id,
        current_user["id"],
        current_user["full_name"],
        update_dict,
        building_id
    )

    wo = await db.work_orders.find_one({"id": wo_id, "building_id": building_id}, {"_id": 0})
    return WorkOrderResponse(**wo)


@router.get("/{wo_id}", response_model=WorkOrderResponse)
async def get_work_order(
        wo_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Get details of a specific work order."""
    wo = await db.work_orders.find_one({"id": wo_id, "building_id": building_id}, {"_id": 0})
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")

    # Authorization check
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings and not permissions.can_manage_finances:
        if wo.get("lot_number") != current_user.get("unit_number"):
            raise HTTPException(status_code=403, detail="Not authorized to view this work order")

    return WorkOrderResponse(**wo)


# ==================== QUOTE ROUTES ====================

@router.post("/{wo_id}/quotes", response_model=QuoteResponse)
async def add_quote(
        wo_id: str,
        data: QuoteCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Add a vendor quote to a work order."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized to add quotes")

    quote_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # BOLA: filter by building_id
    vendor = await db.contractors.find_one({"id": data.vendor_id, "building_id": building_id})
    vendor_name = vendor["name"] if vendor else "Unknown Vendor"
    vendor_rating = vendor.get("rating") if vendor else None

    # SECURITY: Sanitize user input to prevent Stored XSS
    sanitized_description = nh3.clean(data.description)

    quote_doc = {
        "id": quote_id,
        "work_order_id": wo_id,
        "vendor_id": data.vendor_id,
        "vendor_name": vendor_name,
        "vendor_rating": vendor_rating,
        "amount": data.amount,
        "description": sanitized_description,
        "attachments": data.attachments,
        "submitted_date": now,
        "selected": False,
        "status": QuoteStatus.SUBMITTED,
        "building_id": building_id,  # SECURITY: Explicitly scope to building
        "created_at": now
    }

    await db.work_order_quotes.insert_one(quote_doc)

    # Update work order status if needed - BOLA: filter by building_id
    await db.work_orders.update_one(
        {"id": wo_id, "status": WorkOrderStatus.NEW, "building_id": building_id},
        {"$set": {"status": WorkOrderStatus.AWAITING_QUOTES, "updated_at": now}}
    )

    return QuoteResponse(**quote_doc)


@router.get("/{wo_id}/quotes", response_model=List[QuoteResponse])
async def get_work_order_quotes(
        wo_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Get all quotes for a specific work order."""
    quotes = await db.work_order_quotes.find({"work_order_id": wo_id, "building_id": building_id}, {"_id": 0}).to_list(
        50)
    return [QuoteResponse(**q) for q in quotes]


@router.put("/quotes/{quote_id}/select")
async def select_quote(
        quote_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Select a quote for a work order."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    quote = await db.work_order_quotes.find_one({"id": quote_id, "building_id": building_id})
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")

    wo_id = quote["work_order_id"]
    now = datetime.now(timezone.utc).isoformat()

    # Performance Optimization⚡: Parallelize independent DB operations to reduce
    # latency. Disjoint filters allow concurrent updates.
    tasks = [
        db.work_order_quotes.update_many(
            {"work_order_id": wo_id, "id": {"$ne": quote_id}, "building_id": building_id},
            {"$set": {"selected": False, "status": QuoteStatus.REVIEWED}}
        ),
        db.work_order_quotes.update_one(
            {"id": quote_id, "building_id": building_id},
            {"$set": {"selected": True, "status": QuoteStatus.ACCEPTED}}
        ),
        db.work_orders.find_one({"id": wo_id, "building_id": building_id}),
        db.site_settings.find_one({"id": "main"}),
        db.contractors.find_one({"id": quote["vendor_id"], "building_id": building_id})
    ]

    _, _, wo, settings, vendor = await asyncio.gather(*tasks)

    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")

    # Determine if approval is required based on amount and settings
    thresholds = settings.get("work_order_thresholds", []) if settings else []

    requires_approval = wo.get("requires_approval", False)
    if not requires_approval:
        for t in sorted(thresholds, key=lambda x: x["max_amount"]):
            if quote["amount"] <= t["max_amount"]:
                requires_approval = t.get("approval_required", True)
                break
        else:
            if thresholds:
                requires_approval = thresholds[-1].get("approval_required", True)

    new_status = WorkOrderStatus.PENDING_APPROVAL if requires_approval else WorkOrderStatus.APPROVED

    # Check contractor insurance
    if vendor:
        today = datetime.now(timezone.utc).date().isoformat()
        if vendor.get("insurance_expiry") and vendor["insurance_expiry"] < today:
            logger.warning(f"Contractor {vendor['name']} has expired insurance")
            # We don't block yet, but we could add a warning to the WO

    # Update work order - BOLA: filter by building_id
    await db.work_orders.update_one(
        {"id": wo_id, "building_id": building_id},
        {"$set": {
            "assigned_vendor": quote["vendor_id"],
            "vendor_name": quote["vendor_name"],
            "estimated_cost": quote["amount"],
            "status": new_status,
            "updated_at": now
        }}
    )

    if requires_approval:
        # Notify EC members
        asyncio.create_task(_notify_ec_pending_approval(wo_id, quote["amount"], building_id))

    return {"message": "Quote selected successfully"}


# ==================== APPROVAL ROUTES ====================

@router.post("/{wo_id}/approvals", response_model=WorkOrderApprovalResponse)
async def submit_approval(
        wo_id: str,
        data: WorkOrderApprovalCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Submit an EC approval decision for a work order."""
    # Governance trust boundary: super_admin / chairman / ec_member only.
    # strata_manager is intentionally excluded (operational role) per
    # CLAUDE.md "Governance vs operational boundary".
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER]:
        raise HTTPException(status_code=403, detail="Only EC members or Admins can approve work orders")

    approval_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # SECURITY: Sanitize user input to prevent Stored XSS
    sanitized_comment = nh3.clean(data.comment) if data.comment else None

    approval_doc = {
        "id": approval_id,
        "work_order_id": wo_id,
        "approver_id": current_user["id"],
        "approver_name": current_user["full_name"],
        "role": current_user["role"],
        "ec_position": current_user.get("ec_position"),
        "decision": data.decision,
        "comment": sanitized_comment,
        "building_id": building_id,  # SECURITY: Explicitly scope to building
        "timestamp": now
    }

    await db.work_order_approvals.insert_one(approval_doc)

    # Audit log — governance accountability requires every EC vote to be recorded.
    asyncio.create_task(create_audit_log(
        action="approval_submitted",
        resource_type="work_order",
        resource_id=wo_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={
            "approval_id": approval_id,
            "decision": data.decision,
            "comment": sanitized_comment,
            "ec_position": current_user.get("ec_position"),
        },
        building_id=building_id,
    ))

    # Check if approval process is complete based on rules
    await _check_approval_completion(wo_id, building_id)

    return WorkOrderApprovalResponse(**approval_doc)


async def _notify_ec_pending_approval(wo_id: str, amount: float, building_id: str):
    """Notify relevant EC members and super admins about a pending work order approval."""
    wo = await db.work_orders.find_one({"id": wo_id, "building_id": building_id})
    if not wo: return

    memberships = await db.memberships.find({"building_id": building_id}).to_list(None)
    building_user_ids = {m["user_id"] for m in memberships}

    # Super admins are global catch-all + building-scoped EC officeholders
    # (CHAIRMAN/TREASURER/SECRETARY only — plain MEMBER is NOT notified).
    # Pushing the ec_position filter into the Mongo query avoids reading
    # every EC member then discarding most of them in Python.
    NOTIFIABLE_EC_POSITIONS = [ECPosition.CHAIRMAN, ECPosition.TREASURER, ECPosition.SECRETARY]
    recipients = await db.users.find({
        "$or": [
            {"role": UserRole.SUPER_ADMIN, "is_active": True},
            {
                "id": {"$in": list(building_user_ids)},
                "role": UserRole.EC_MEMBER,
                "ec_position": {"$in": NOTIFIABLE_EC_POSITIONS},
                "is_active": True,
            },
        ]
    }).to_list(100)

    notif_data = [
        {
            "user_id": member["id"],
            "title": "Work Order Pending Approval",
            "message": f"Work Order '{wo['title']}' for ${amount:,.2f} is awaiting your approval.",
            "type": "maintenance",
            "link": f"/maintenance/work-order/{wo_id}"
        }
        for member in recipients
    ]
    if notif_data:
        await create_notifications_batch(notif_data)


async def _check_approval_completion(wo_id: str, building_id: str):
    """
    Internal helper to evaluate if a work order has met approval requirements.
    """
    # Performance Optimization⚡: Parallelize independent DB fetches to reduce
    # decision phase round-trips from 3 to 1.
    tasks = [
        db.work_orders.find_one({"id": wo_id, "building_id": building_id}),
        db.site_settings.find_one({"id": "main"}),
        db.work_order_approvals.find({"work_order_id": wo_id, "building_id": building_id}).to_list(100)
    ]
    wo, settings, approvals = await asyncio.gather(*tasks)

    if not wo: return

    # Get SiteSettings for approval rules
    thresholds = settings.get("work_order_thresholds", []) if settings else []

    amount = wo.get("estimated_cost", 0)

    # Find matching threshold
    matching_rule = None
    for t in sorted(thresholds, key=lambda x: x["max_amount"]):
        if amount <= t["max_amount"]:
            matching_rule = t
            break

    if not matching_rule and thresholds:
        matching_rule = sorted(thresholds, key=lambda x: x["max_amount"])[-1]

    approved_list = [a for a in approvals if a["decision"] == WorkOrderApprovalDecision.APPROVE]
    rejected_list = [a for a in approvals if a["decision"] == WorkOrderApprovalDecision.REJECT]

    if rejected_list:
        await db.work_orders.update_one(
            {"id": wo_id, "building_id": building_id},
            {"$set": {"status": WorkOrderStatus.CANCELLED, "approval_status": "rejected",
                      "updated_at": datetime.now(timezone.utc).isoformat()}}
        )
        rejector = rejected_list[0]
        asyncio.create_task(create_audit_log(
            action="work_order_rejected",
            resource_type="work_order",
            resource_id=wo_id,
            user_id=rejector["approver_id"],
            user_name=rejector["approver_name"],
            details={"rejection_count": len(rejected_list)},
            building_id=building_id,
        ))
        requester_id = wo.get("created_by")
        if requester_id:
            asyncio.create_task(create_user_notification(
                user_id=requester_id,
                title="Work Order Rejected",
                message=f"Work Order '{wo.get('title', wo_id)}' has been rejected by the committee.",
                notification_type="maintenance",
                link=f"/maintenance/work-order/{wo_id}",
                building_id=building_id,
            ))
        return

    is_approved = False

    if not matching_rule:
        # Fallback logic if no rules defined
        is_approved = any(
            a["role"] == UserRole.EC_MEMBER or a.get("ec_position") in [ECPosition.CHAIRMAN, ECPosition.TREASURER]
            for a in approved_list
        )
    else:
        mode = matching_rule.get("approval_mode", "SINGLE_APPROVAL")
        required_roles = matching_rule.get("approval_roles", [])

        if mode == "SINGLE_APPROVAL":
            # Any of the required roles (or admin)
            is_approved = any(
                a["role"] == UserRole.SUPER_ADMIN or a.get("ec_position") in required_roles
                for a in approved_list
            )
        elif mode == "DUAL_APPROVAL":
            # Need at least two different people from required roles
            approver_ids = {a["approver_id"] for a in approved_list if
                            a["role"] == UserRole.SUPER_ADMIN or a.get("ec_position") in required_roles}
            is_approved = len(approver_ids) >= 2
        elif mode == "MAJORITY":
            # Need majority of EC members
            ec_count = await db.users.count_documents({"role": UserRole.EC_MEMBER, "is_active": True})
            required_votes = (ec_count // 2) + 1
            approver_ids = {a["approver_id"] for a in approved_list if
                            a["role"] in [UserRole.EC_MEMBER, UserRole.SUPER_ADMIN]}
            is_approved = len(approver_ids) >= required_votes

    if is_approved:
        now = datetime.now(timezone.utc).isoformat()
        await db.work_orders.update_one(
            {"id": wo_id, "building_id": building_id},
            {"$set": {"status": WorkOrderStatus.APPROVED, "approval_status": "approved", "updated_at": now}}
        )
        last_approver = approved_list[-1] if approved_list else {}
        asyncio.create_task(create_audit_log(
            action="work_order_approved",
            resource_type="work_order",
            resource_id=wo_id,
            user_id=last_approver.get("approver_id", "system"),
            user_name=last_approver.get("approver_name", "system"),
            details={
                "approval_count": len(approved_list),
                "amount": amount,
                "committee_resolution_created": amount > 5000,
            },
            building_id=building_id,
        ))
        requester_id = wo.get("created_by")
        if requester_id:
            asyncio.create_task(create_user_notification(
                user_id=requester_id,
                title="Work Order Approved",
                message=f"Work Order '{wo.get('title', wo_id)}' has been approved by the committee.",
                notification_type="maintenance",
                link=f"/maintenance/work-order/{wo_id}",
                building_id=building_id,
            ))

        # Generate Committee Resolution Record for large WOs (> $5000)
        if amount > 5000:
            resolution_id = str(uuid.uuid4())
            resolution = CommitteeResolution(
                id=resolution_id,
                work_order_id=wo_id,
                title=f"Resolution: {wo['title']}",
                description=f"Approved work order for {wo['title']} with selected vendor {wo.get('vendor_name')}.",
                approved_amount=amount,
                approvals=[{
                    "approver_id": a["approver_id"],
                    "approver_name": a["approver_name"],
                    "role": a["role"],
                    "comment": a.get("comment"),
                    "timestamp": a["timestamp"]
                } for a in approved_list],
                generated_at=now
            )
            res_doc = resolution.model_dump()
            res_doc["building_id"] = building_id
            await db.committee_resolutions.insert_one(res_doc)


# ==================== INVOICE ROUTES ====================

@router.get("/invoices/pending-approvals", response_model=List[WorkOrderInvoiceResponse])
async def get_pending_work_order_invoices(
        current_user: dict = Depends(get_approved_user),
        building_id: str | None = Depends(get_optional_building),
):
    """Get all work order invoices awaiting approval with integrity warnings.

    Returns an empty list when no building is in scope (e.g. a fresh
    super-admin login with no scheme selected) so the dashboard nav-badge
    fetch doesn't break the page.
    """
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    if not building_id:
        return []

    invoices = await db.work_order_invoices.find(
        {"approval_status": "submitted", "building_id": building_id},
        {"_id": 0}
    ).sort("created_at", -1).to_list(100)

    if not invoices:
        return []

    # Performance Optimization⚡: Eliminate N+1 query pattern by batch fetching selected quotes.
    # This reduces potential database round-trips from O(N) to O(1).
    wo_ids = list(set(inv["work_order_id"] for inv in invoices))
    quotes = await db.work_order_quotes.find(
        {"work_order_id": {"$in": wo_ids}, "selected": True, "building_id": building_id},
        {"_id": 0, "work_order_id": 1, "amount": 1}
    ).to_list(len(wo_ids))
    quote_map = {q["work_order_id"]: q for q in quotes}

    enriched_invoices = []
    for inv in invoices:
        warnings = []
        quote = quote_map.get(inv["work_order_id"])

        if not quote:
            warnings.append("No approved quote exists for this work order")
        elif inv.get("amount", 0) > quote.get("amount", 0):
            diff = inv["amount"] - quote["amount"]
            warnings.append(f"Invoice exceeds approved quote by ${diff:,.2f}")

        inv["warnings"] = warnings
        enriched_invoices.append(inv)

    return [WorkOrderInvoiceResponse(**inv) for inv in enriched_invoices]


@router.post("/{wo_id}/invoices", response_model=WorkOrderInvoiceResponse)
async def submit_invoice(
        wo_id: str,
        data: WorkOrderInvoiceCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Submit an invoice for a completed work order. Detects duplicates."""
    now = datetime.now(timezone.utc).isoformat()

    # SECURITY: Sanitize user input to prevent Stored XSS
    sanitized_invoice_number = html_lib.escape(data.invoice_number)

    # Duplicate Invoice Detection - BOLA: filter by building_id
    existing = await db.work_order_invoices.find_one({
        "building_id": building_id,
        "vendor_id": data.vendor_id,
        "invoice_number": sanitized_invoice_number,
        "amount": data.amount
    })

    inv_id = str(uuid.uuid4())
    inv_doc = {
        "id": inv_id,
        **data.model_dump(),
        "invoice_number": sanitized_invoice_number,
        "building_id": building_id,  # SECURITY: Explicitly override building_id AFTER expansion
        "approval_status": "submitted",
        "payment_status": "pending",
        "approved_by": None,
        "approved_at": None,
        "paid_at": None,
        "created_at": now,
        "updated_at": now
    }

    if existing:
        # Add warning if duplicate detected
        inv_doc["warnings"] = ["Possible duplicate invoice detected (same vendor, number and amount)"]

    await db.work_order_invoices.insert_one(inv_doc)

    # Update WO status - BOLA: filter by building_id
    await db.work_orders.update_one(
        {"id": wo_id, "building_id": building_id},
        {"$set": {"status": WorkOrderStatus.INVOICE_PENDING, "updated_at": now}}
    )

    # Audit log
    await create_audit_log(
        "invoice_submitted",
        "work_order",
        wo_id,
        current_user["id"],
        current_user["full_name"],
        {"invoice_number": sanitized_invoice_number, "amount": data.amount},
        building_id
    )

    return WorkOrderInvoiceResponse(**inv_doc)


@router.put("/invoices/{inv_id}/approve")
async def approve_work_order_invoice(
        inv_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Approve a work order invoice. Implements financial integrity checks and emergency bypass."""
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    invoice = await db.work_order_invoices.find_one({"id": inv_id, "building_id": building_id})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    wo = await db.work_orders.find_one({"id": invoice["work_order_id"], "building_id": building_id})
    if not wo:
        raise HTTPException(status_code=404, detail="Associated work order not found")

    # Emergency Override Bypass
    is_emergency = wo.get("is_emergency", False) or wo.get("emergency_override", False)

    # Integrity Check: No quote exists - BOLA: filter by building_id
    quote = await db.work_order_quotes.find_one(
        {"work_order_id": invoice["work_order_id"], "selected": True, "building_id": building_id})

    if not quote and not is_emergency:
        raise HTTPException(status_code=400,
                            detail="Cannot approve invoice: No approved quote exists for this work order.")

    # Integrity Check: Invoice > Quote * 2
    if quote and invoice["amount"] > (quote["amount"] * 2) and not is_emergency:
        raise HTTPException(status_code=400,
                            detail=f"Invoice amount (${invoice['amount']}) is more than double the quote (${quote['amount']}). Hard block triggered.")

    now = datetime.now(timezone.utc).isoformat()

    await db.work_order_invoices.update_one(
        {"id": inv_id, "building_id": building_id},
        {"$set": {
            "approval_status": "approved",
            "approved_by": current_user["id"],
            "approved_at": now,
            "updated_at": now
        }}
    )

    # Create Financial Transaction Entry
    await db.financial_transactions.insert_one({
        "id": str(uuid.uuid4()),
        "building_id": building_id,  # SECURITY: Explicitly scope to building
        "category": "Maintenance",
        "amount": invoice["amount"],
        "linked_resource_type": "work_order",
        "linked_resource_id": invoice["work_order_id"],
        "invoice_id": inv_id,
        "vendor_id": invoice["vendor_id"],
        "status": "pending_payment",
        "created_at": now
    })

    # Audit log
    await create_audit_log(
        "invoice_approved",
        "work_order",
        invoice["work_order_id"],
        current_user["id"],
        current_user["full_name"],
        {"invoice_id": inv_id, "amount": invoice["amount"], "is_emergency": is_emergency},
        building_id
    )

    return {"message": "Invoice approved successfully"}


@router.put("/invoices/{inv_id}/reject")
async def reject_work_order_invoice(
        inv_id: str,
        reason: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Reject a work order invoice with a mandatory reason."""
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    if not reason or len(reason.strip()) < 5:
        raise HTTPException(status_code=422, detail="Rejection reason must be at least 5 characters")

    invoice = await db.work_order_invoices.find_one({"id": inv_id, "building_id": building_id})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # SECURITY: Sanitize user input to prevent Stored XSS
    sanitized_reason = nh3.clean(reason.strip())

    now = datetime.now(timezone.utc).isoformat()

    await db.work_order_invoices.update_one(
        {"id": inv_id, "building_id": building_id},
        {"$set": {
            "approval_status": "rejected",
            "rejected_by": current_user["id"],
            "rejected_at": now,
            "rejection_reason": sanitized_reason,
            "updated_at": now
        }}
    )

    await create_audit_log(
        "invoice_rejected",
        "work_order",
        invoice["work_order_id"],
        current_user["id"],
        current_user["full_name"],
        {"invoice_id": inv_id, "reason": sanitized_reason},
        building_id
    )

    return {"message": "Invoice rejected successfully"}


# ==================== EMAIL INGESTION ====================

@router.post("/email/ingest")
async def ingest_email(
        payload: Dict[str, Any],
        background_tasks: BackgroundTasks,
        x_api_key: Optional[str] = Header(None)
):
    """
    Endpoint for mail ingestion service.
    Matches emails to work orders by WO#, Lot, or Request ID.
    """
    # API key authentication, fail-closed. `if expected_key and ...` skipped the
    # check entirely whenever EMAIL_INGEST_API_KEY was unset — which is the state
    # backend/.env is in — leaving the endpoint open to every authenticated user
    # (the router-level require_feature dependency authenticates but does not
    # authorise, and this route is meant to be machine-to-machine). Matches the
    # 503-when-unconfigured contract of stripe_webhook and
    # services/email_intake_service.verify_inbound_email_signature.
    expected_key = os.environ.get("EMAIL_INGEST_API_KEY", "")
    if not expected_key:
        logger.critical("[SECURITY] EMAIL_INGEST_API_KEY is not configured; rejecting ingestion.")
        raise HTTPException(status_code=503, detail="Email ingestion is not configured.")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected_key):
        raise HTTPException(status_code=403, detail="Invalid API Key")

    # SECURITY: Sanitize user input to prevent Stored XSS
    subject = html_lib.escape(payload.get("subject", ""))
    body = nh3.clean(payload.get("body", ""))
    sender = html_lib.escape(payload.get("from", ""))

    wo_id = None
    target_building_id = None

    # Match WO #12345 (UUID or numerical ID if implemented)
    import re
    wo_match = re.search(r"WO\s*#?\s*([a-f0-9\-]{36})", subject, re.I)
    if wo_match:
        wo_id = wo_match.group(1)
        # Use raw DB instance to bypass tenant scoping for initial building lookup
        wo = await db._db.work_orders.find_one({"id": wo_id})
        if wo:
            target_building_id = wo.get("building_id")
        else:
            wo_id = None

    if not wo_id:
        # Match by Lot number if WO# not present - requires building_id in payload for context
        lot_match = re.search(r"Lot\s*(\d+)", subject, re.I)
        bid_from_payload = payload.get("building_id")
        if lot_match and bid_from_payload:
            lot_num = lot_match.group(1)
            # Find latest work order for this lot within the specified building
            latest_wo = await db.work_orders.find_one(
                {"lot_number": lot_num, "building_id": bid_from_payload},
                sort=[("created_at", -1)]
            )
            if latest_wo:
                wo_id = latest_wo["id"]
                target_building_id = bid_from_payload

    if wo_id and target_building_id:
        comm_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        comm_doc = {
            "id": comm_id,
            "building_id": target_building_id,  # SECURITY: Explicitly scope to building
            "work_order_id": wo_id,
            "sender": sender,
            "sender_name": sender,
            "recipient": "System",
            "subject": subject,
            "message": body,
            "attachments": payload.get("attachments", []),
            "source_type": "email",
            "timestamp": now
        }

        # Use raw DB instance for ingestion if context not set globally
        await db._db.work_order_communications.insert_one(comm_doc)
        logger.info(f"Ingested email for WO {wo_id} in building {target_building_id}")
        return {"status": "success", "work_order_id": wo_id}

    return {"status": "ignored", "reason": "No work order ID found in subject or building missing"}


# ==================== TIMELINE ====================

@router.get("/{wo_id}/timeline")
async def get_work_order_timeline(
        wo_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Get a combined timeline of all events related to a work order."""
    # Performance Optimization⚡: Parallelize independent database fetches using asyncio.gather.
    # This reduces cumulative I/O wait time from O(4) to O(1) concurrent requests.
    tasks = [
        db.work_orders.find_one({"id": wo_id, "building_id": building_id}, {"_id": 0}),
        db.work_order_quotes.find({"work_order_id": wo_id, "building_id": building_id}).to_list(100),
        db.work_order_approvals.find({"work_order_id": wo_id, "building_id": building_id}).to_list(100),
        db.work_order_invoices.find({"work_order_id": wo_id, "building_id": building_id}).to_list(100)
    ]

    wo, quotes, approvals, invoices = await asyncio.gather(*tasks)

    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")

    # This would be a consolidated list of events sorted by timestamp
    timeline = []

    timeline.append({
        "type": "WORK_ORDER_CREATED",
        "timestamp": wo["created_at"],
        "title": "Work Order Created",
        "description": f"Work order created by {wo.get('created_by_name', 'System')}"
    })

    # Quotes
    for q in quotes:
        timeline.append({
            "type": "QUOTE_SUBMITTED",
            "timestamp": q["submitted_date"],
            "title": f"Quote Submitted: {q['vendor_name']}",
            "description": f"Amount: ${q['amount']:.2f}"
        })
        if q.get("selected"):
            timeline.append({
                "type": "QUOTE_SELECTED",
                "timestamp": q.get("updated_at", q["submitted_date"]),
                "title": "Quote Selected",
                "description": f"Vendor {q['vendor_name']} selected for the job."
            })

    # Approvals
    for a in approvals:
        timeline.append({
            "type": "APPROVAL_DECISION",
            "timestamp": a["timestamp"],
            "title": f"Approval Decision: {a['decision'].title()}",
            "description": f"By {a['approver_name']} ({a['role']}). Comment: {a.get('comment', 'No comment')}"
        })

    # Invoices
    for i in invoices:
        timeline.append({
            "type": "INVOICE_SUBMITTED",
            "timestamp": i["created_at"],
            "title": f"Invoice Submitted: {i['invoice_number']}",
            "description": f"Amount: ${i['total_amount'] if 'total_amount' in i else i['amount']:.2f}"
        })
        if i.get("status") == "paid":
            timeline.append({
                "type": "PAYMENT_PROCESSED",
                "timestamp": i.get("paid_at", i["created_at"]),
                "title": "Payment Processed",
                "description": f"Invoice {i['invoice_number']} has been paid."
            })

    timeline.sort(key=lambda x: x["timestamp"])
    return timeline


# ==================== ATTACHMENT ROUTES ====================

@router.post("/{wo_id}/attachments", response_model=WorkOrderAttachmentResponse)
async def add_attachment(
        wo_id: str,
        data: WorkOrderAttachmentCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Add an attachment to a work order."""
    attachment_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    attachment_doc = {
        "id": attachment_id,
        **data.model_dump(),
        "building_id": building_id,  # SECURITY: Explicitly scope to building AFTER expansion
        "uploaded_by": current_user["id"],
        "uploaded_by_name": current_user["full_name"],
        "created_at": now
    }

    await db.work_order_attachments.insert_one(attachment_doc)
    return WorkOrderAttachmentResponse(**attachment_doc)


@router.get("/{wo_id}/attachments", response_model=List[WorkOrderAttachmentResponse])
async def get_work_order_attachments(
        wo_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Get all attachments for a specific work order."""
    attachments = await db.work_order_attachments.find(
        {"work_order_id": wo_id, "building_id": building_id},
        {"_id": 0}
    ).to_list(100)
    return [WorkOrderAttachmentResponse(**a) for a in attachments]


@router.get("/{wo_id}/communications", response_model=List[WorkOrderCommunicationResponse])
async def get_work_order_communications(
        wo_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Get all communications for a specific work order."""
    comms = await db.work_order_communications.find(
        {"work_order_id": wo_id, "building_id": building_id},
        {"_id": 0}
    ).to_list(100)
    return [WorkOrderCommunicationResponse(**c) for c in comms]


@router.get("/{wo_id}/invoices", response_model=List[WorkOrderInvoiceResponse])
async def get_wo_specific_invoices(
        wo_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Get all invoices for a specific work order."""
    invoices = await db.work_order_invoices.find(
        {"work_order_id": wo_id, "building_id": building_id},
        {"_id": 0}
    ).to_list(100)
    return [WorkOrderInvoiceResponse(**i) for i in invoices]


# ==================== RECURRING WORK ORDERS ====================

@router.post("/recurring", response_model=RecurringWorkOrderResponse)
async def create_recurring_work_order(
        data: RecurringWorkOrderCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Create a recurring work order (e.g. monthly maintenance)."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    rwo_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # SECURITY: Sanitize user input to prevent Stored XSS
    sanitized_data = data.model_dump()
    sanitized_data["title"] = html_lib.escape(data.title)
    sanitized_data["description"] = nh3.clean(data.description)

    rwo_doc = {
        "id": rwo_id,
        **sanitized_data,
        "building_id": building_id,  # SECURITY: Explicitly override building_id AFTER expansion
        "last_generated_at": None,
        "created_at": now
    }

    await db.recurring_work_orders.insert_one(rwo_doc)
    return RecurringWorkOrderResponse(**rwo_doc)


@router.get("/recurring", response_model=List[RecurringWorkOrderResponse])
async def get_recurring_work_orders(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """List recurring work orders."""
    rwos = await db.recurring_work_orders.find({"building_id": building_id}, {"_id": 0}).to_list(100)
    return [RecurringWorkOrderResponse(**r) for r in rwos]


@router.get("/resolutions", response_model=List[CommitteeResolution])
async def get_committee_resolutions(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """List all committee resolutions."""
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    resolutions = await db.committee_resolutions.find({"building_id": building_id}, {"_id": 0}).sort("generated_at",
                                                                                                     -1).to_list(100)
    return [CommitteeResolution(**r) for r in resolutions]


# ── GAP-MNT-004: SWMS (Safe Work Method Statement) ────────────────────────────

@router.put("/{wo_id}/swms")
async def attach_swms(
        wo_id: str,
        payload: WorkOrderSWMSUpdate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Attach or replace the SWMS document for a work order (WHS Act compliance gate).

    High-risk construction work (swms_required=True) cannot transition to IN_PROGRESS
    without a valid swms_document_id.  Manager/EC roles only.
    """
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in {"super_admin", "strata_admin", "strata_manager", "ec_member", "admin_staff"}:
        raise HTTPException(status_code=403, detail="Manager access required to attach SWMS.")

    wo = await db.work_orders.find_one({"id": wo_id, "building_id": building_id}, {"_id": 0})
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found.")
    if wo.get("status") == WorkOrderStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Cannot modify a completed work order.")

    now = datetime.now(timezone.utc).isoformat()
    await db.work_orders.update_one(
        {"id": wo_id, "building_id": building_id},
        {"$set": {"swms_document_id": payload.swms_document_id, "updated_at": now}},
    )

    asyncio.create_task(create_audit_log(
        "swms_attached",
        "work_order",
        wo_id,
        current_user.get("id", "unknown"),
        current_user.get("full_name", ""),
        {"swms_document_id": payload.swms_document_id},
        building_id,
    ))

    return {"message": "SWMS document attached.", "wo_id": wo_id,
            "swms_document_id": payload.swms_document_id}


# ── GAP-MNT-005: Before / after inspection photos ─────────────────────────────

@router.post("/{wo_id}/before-photos")
async def add_before_photo(
        wo_id: str,
        payload: WorkOrderPhotoAdd,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Add a before-photo document ID to the work order (append, not replace)."""
    wo = await db.work_orders.find_one({"id": wo_id, "building_id": building_id}, {"_id": 0})
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found.")

    now = datetime.now(timezone.utc).isoformat()
    await db.work_orders.update_one(
        {"id": wo_id, "building_id": building_id},
        {"$addToSet": {"before_photos": payload.document_id}, "$set": {"updated_at": now}},
    )
    return {"message": "Before-photo added.", "wo_id": wo_id, "document_id": payload.document_id}


@router.post("/{wo_id}/after-photos")
async def add_after_photo(
        wo_id: str,
        payload: WorkOrderPhotoAdd,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Add an after-photo document ID to the work order (append, not replace)."""
    wo = await db.work_orders.find_one({"id": wo_id, "building_id": building_id}, {"_id": 0})
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found.")

    now = datetime.now(timezone.utc).isoformat()
    await db.work_orders.update_one(
        {"id": wo_id, "building_id": building_id},
        {"$addToSet": {"after_photos": payload.document_id}, "$set": {"updated_at": now}},
    )
    return {"message": "After-photo added.", "wo_id": wo_id, "document_id": payload.document_id}
