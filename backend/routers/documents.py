# @featuretrace:documents — Document upload, listing, retrieval and access control.
# Layer: router
# Data flow: GET/POST /documents -> build_document_visibility_filter() -> documents
#            (MongoDB, building-scoped) -> DocumentsPage + the dashboard activity feed.
# Related: backend/utils/document_visibility.py
#          backend/routers/analytics.py
#          backend/cron/cron_payment_reminders.py
# Tests: tests/backend/test_document_visibility.py
#        tests/backend/test_document_security.py

"""
Document router module.

This module handles all document-related routes including document upload,
retrieval, deletion, and permission management.
"""

import base64
import json
import os
import re
import uuid

import asyncio
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Query
from fastapi.security import HTTPBearer
from typing import List, Optional

from database import db
from models.document import (
    DocumentResponse,
    DocumentPermissionUpdate,
)
from models.user import UserRole
from utils.activity_helper import log_activity
from utils.auth import get_current_user, get_optional_user, is_approved_user, get_current_building
from utils.helpers import create_audit_log, get_current_utc_iso
from utils.file_scan import scan_upload
from utils.permissions import get_user_permissions, require_feature
from utils.document_visibility import (
    owned_unit_numbers,
    build_document_visibility_filter,
    is_privileged_document_reader,
)

# Create router
router = APIRouter(prefix="")

# Security
security = HTTPBearer(auto_error=False)


def _safe_filename(filename: str) -> str:
    """Strip directory components and replace unsafe characters from a user-supplied filename."""
    name = os.path.basename(filename or "upload")
    # Replace path separators and any non-alphanumeric/dot/dash/underscore chars
    name = re.sub(r"[^\w.\-]", "_", name)
    return name or "upload"


def _get_storage_path(building_id: str, doc_id: str, file_name: str) -> str:
    """Derive a partitioned, server-controlled storage path for multi-tenancy.

    Uses the server-generated doc_id as the filename to avoid path traversal
    or collision from untrusted user input. The original filename extension is
    preserved for content-type hints.
    """
    ext = os.path.splitext(_safe_filename(file_name))[1]
    safe_doc_name = f"{doc_id}{ext}"
    return f"uploads/{building_id}/{safe_doc_name}"


@router.post("/documents", response_model=DocumentResponse)
async def upload_document(
        title: str = Form(...),
        description: str = Form(None),
        category: str = Form(...),
        is_public: bool = Form(False),
        allowed_roles: str = Form("[]"),
        is_test_data: bool = Form(False),
        file: UploadFile = File(...),
        current_user: dict = Depends(require_feature("documents")),
        building_id: str = Depends(get_current_building)
):
    """
    Upload a new document. Scoped to building.
    
    Allows authorized users to upload documents with specified access controls.
    Requires can_upload_documents permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_upload_documents:
        raise HTTPException(status_code=403, detail="Not authorized to upload documents")

    roles_list = json.loads(allowed_roles)

    file_content = await file.read()
    await scan_upload(file_content, context="document", filename=file.filename or "")
    file_base64 = base64.b64encode(file_content).decode('utf-8')
    doc_id = str(uuid.uuid4())
    safe_display_name = _safe_filename(file.filename or "upload")
    storage_path = _get_storage_path(building_id, doc_id, file.filename)

    now = get_current_utc_iso()

    doc = {
        "id": doc_id,
        "building_id": building_id,
        "title": title,
        "description": description,
        "category": category,
        "file_name": safe_display_name,
        "file_type": file.content_type,
        "file_size": len(file_content),
        "file_data": file_base64,
        "storage_path": storage_path,  # Path-based isolation for future S3/Disk move
        "is_public": is_public,
        "allowed_roles": roles_list,
        "uploaded_by": current_user["id"],
        "uploaded_by_name": current_user["full_name"],
        "created_at": now,
        "updated_at": now,
        "is_test_data": bool(is_test_data),
    }

    await db.documents.insert_one(doc)

    asyncio.create_task(create_audit_log(
        action="uploaded", resource_type="document", resource_id=doc_id,
        user_id=current_user["id"], user_name=current_user["full_name"],
        details={"title": title, "category": category, "file_name": safe_display_name,
                 "file_size": len(file_content)},
        building_id=building_id
    ))

    asyncio.create_task(log_activity(
        activity_type="document",
        title=f"New Document: {title}",
        entity_id=doc_id,
        priority=3,
        metadata={"category": category, "file_name": safe_display_name}
    ))

    return DocumentResponse(**doc)


@router.get("/documents", response_model=List[DocumentResponse])
async def get_documents(
        category: Optional[str] = None,
        include_test_data: bool = Query(False),
        current_user: dict = Depends(get_optional_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_documents
    Path: backend/routers/documents.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if current_user:
        from utils.permissions import get_effective_feature_access
        if not await get_effective_feature_access(current_user, "documents"):
            raise HTTPException(status_code=403, detail="Documents feature is disabled")
    """
    Get list of documents.
    
    Returns documents based on user permissions and access levels.
    Public documents are visible to all, private documents only to authorized users.
    """
    query: dict = {"building_id": building_id}
    if not include_test_data:
        query["is_test_data"] = {"$ne": True}

    # Visibility comes from ONE shared definition (utils/document_visibility.py).
    # This block used to filter on is_public/uploaded_by/allowed_roles only, which
    # matched nothing against documents written by the levy-notice cron — those
    # carry is_private/owner_id instead. East Gate had 242 such documents and this
    # endpoint returned an empty list for every one of them.
    visibility = build_document_visibility_filter(current_user)
    if current_user and not is_privileged_document_reader(current_user):
        permissions = get_user_permissions(current_user)
        if not permissions.can_view_documents:
            # Explicitly barred from the documents feature: shared documents only,
            # never another owner's private notice.
            visibility = {"$and": [
                {"is_private": {"$ne": True}},
                {"is_public": {"$ne": False}},
            ]}

    # Merged under $and rather than assigned to query["$or"], so it composes with
    # the category filter and never collides with a top-level $or.
    if visibility:
        query["$and"] = [*query.get("$and", []), visibility]

    if category:
        query["category"] = category

    documents = await db.documents.find(query, {"_id": 0, "file_data": 0}).to_list(1000)
    return [DocumentResponse(**d, file_data=None) for d in documents]


@router.get("/documents/{doc_id}", response_model=DocumentResponse)
async def get_document(
        doc_id: str,
        include_test_data: bool = Query(False),
        current_user: dict = Depends(get_optional_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get a specific document by ID. Scoped to building context.
    
    Returns the complete document including file data if user has access.
    Checks access permissions before returning.
    """
    query = {"id": doc_id, "building_id": building_id}
    if not include_test_data:
        query["is_test_data"] = {"$ne": True}

    doc = await db.documents.find_one(query, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check access.
    #
    # This read `doc["is_public"]` directly. Documents written by the levy-notice
    # cron carry no is_public key at all, so fetching one raised KeyError -> HTTP
    # 500 rather than any deliberate answer. Private-ness is now asked of the same
    # helper the list endpoint uses, under both spellings.
    is_private_doc = doc.get("is_private") is True or doc.get("is_public") is False
    if is_private_doc:
        if not current_user:
            raise HTTPException(status_code=403, detail="Not authorized")

        # Administrators, uploader/owner, and approved users with allowed roles.
        permissions = get_user_permissions(current_user)
        is_admin = permissions.can_manage_users or is_privileged_document_reader(current_user)
        is_uploader = current_user["id"] in {doc.get("uploaded_by"), doc.get("owner_id")}
        # A generated notice is addressed to a unit; its holder must be able to open it.
        owns_unit = bool(doc.get("unit_number")) and str(doc["unit_number"]) in set(
            owned_unit_numbers(current_user)
        )

        # Role-based access is only granted to approved users (use effective_role
        # so users elevated to ec_member/chairman are matched against allowed_roles).
        _eff_role = current_user.get("effective_role") or current_user.get("role", "guest")
        role_allowed = is_approved_user(current_user) and _eff_role in (doc.get("allowed_roles") or [])

        if not (is_admin or is_uploader or owns_unit or role_allowed):
            raise HTTPException(status_code=403, detail="Not authorized")

    return DocumentResponse(**doc)


@router.delete("/documents/{doc_id}")
async def delete_document(
        doc_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Delete a document.
    
    Only the document uploader or users with manage_users permission can delete documents.
    """
    permissions = get_user_permissions(current_user)

    doc = await db.documents.find_one({"id": doc_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc["uploaded_by"] != current_user["id"] and not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.documents.delete_one({"id": doc_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Document not found")

    asyncio.create_task(create_audit_log(
        action="deleted", resource_type="document", resource_id=doc_id,
        user_id=current_user["id"], user_name=current_user["full_name"],
        details={"title": doc.get("title"), "category": doc.get("category")},
        building_id=building_id
    ))

    return {"message": "Document deleted successfully"}


@router.put("/documents/{doc_id}/permissions")
async def update_document_permissions(
        doc_id: str,
        data: DocumentPermissionUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Update document permissions.
    
    Only Chairman or Super Admin can manage document permissions.
    Allows setting access levels and specific user/role permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_document_permissions:
        raise HTTPException(status_code=403, detail="Only Chairman or Super Admin can manage document permissions")

    await db.documents.update_one(
        {"id": doc_id, "building_id": building_id},
        {"$set": {
            "access_level": data.access_level,
            "allowed_roles": data.allowed_roles,
            "allowed_users": data.allowed_users,
            "permissions_updated_by": current_user["id"],
            "permissions_updated_at": get_current_utc_iso()
        }}
    )

    return {"message": "Document permissions updated"}


@router.get("/documents/{doc_id}/can-access")
async def check_document_access(
        doc_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Check if current user can access a specific document.
    
    Returns access and edit permissions for the current user based on
    their role and the document's access level.
    """
    doc = await db.documents.find_one({"id": doc_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    user_role = current_user.get("role", "guest")
    user_id = current_user.get("id")
    access_level = doc.get("access_level", "all_members")

    # Chairman and Super Admin always have access
    if user_role in [UserRole.EC_MEMBER, UserRole.SUPER_ADMIN]:
        return {"can_access": True, "can_edit": True, "reason": "Admin access"}

    # Check specific user permissions
    if user_id in doc.get("allowed_users", []):
        return {"can_access": True, "can_edit": True, "reason": "Specific permission"}

    # Check role-based access
    access_rules = {
        "all_members": {"view": ["owner", "tenant", "ec_member", "strata_admin", "super_admin"],
                        "edit": []},
        "owners_view": {"view": ["owner", "ec_member", "strata_admin", "super_admin"], "edit": []},
        "owners_edit": {"view": ["owner", "ec_member", "strata_admin", "super_admin"],
                        "edit": ["owner", "ec_member", "strata_admin", "super_admin"]},
        "ec_view": {"view": ["ec_member", "strata_admin", "super_admin"], "edit": []},
        "ec_edit": {"view": ["ec_member", "strata_admin", "super_admin"],
                    "edit": ["ec_member", "strata_admin", "super_admin"]},
        "chairman_only": {"view": ["strata_admin", "super_admin"],
                          "edit": ["strata_admin", "super_admin"]}
    }

    rules = access_rules.get(access_level, access_rules["all_members"])
    can_view = user_role in rules["view"]
    can_edit = user_role in rules["edit"]

    return {"can_access": can_view, "can_edit": can_edit, "access_level": access_level}


__all__ = ["router"]
