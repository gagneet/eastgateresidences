"""
Tenant Maintenance router — bidirectional request thread between tenants,
owners, agents and strata managers, with photo upload support.
"""
import uuid
from pathlib import Path

import logging
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File

from database import db
from models.tenancy import (
    MaintenanceRequestCreate,
    MaintenanceRequestUpdate,
    MaintenanceRequestMessage,
)
from services.tenancy_service import (
    create_maintenance_request,
    get_maintenance_requests,
    update_maintenance_request,
    add_maintenance_message,
    get_maintenance_messages,
    ownerhub_property_visibility_query,
)
from utils.auth import effective_role, get_current_user

router = APIRouter(prefix="/tenant/maintenance", tags=["Tenant Maintenance"])
logger = logging.getLogger(__name__)

UPLOAD_BASE = Path("/uploads/maintenance")

MANAGER_ROLES = {
    "owner", "real_estate_agent", "strata_manager",
    "strata_admin", "ec_member", "super_admin", "admin_staff"
}


def _require_auth(current_user: dict = Depends(get_current_user)) -> dict:
    """Generated function header.

    Function: _require_auth
    Path: backend/routers/tenant_maintenance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return current_user


def _require_manager(current_user: dict = Depends(get_current_user)) -> dict:
    """Generated function header.

    Function: _require_manager
    Path: backend/routers/tenant_maintenance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return current_user


async def _assert_request_access(request_id: str, current_user: dict) -> dict:
    """Return the request doc if the user is allowed to view it."""
    from bson import ObjectId
    try:
        oid = ObjectId(request_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request ID")

    doc = await db.tenant_maintenance_requests.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Maintenance request not found")

    role = effective_role(current_user)
    user_id = current_user.get("id", "")

    # Super admins retain platform support visibility. Other staff must be
    # explicitly shared onto an OwnerHub property before seeing linked requests.
    if role == "super_admin":
        doc["id"] = str(doc.pop("_id"))
        return doc

    # Tenant: own requests only
    if role == "tenant":
        if doc.get("submitted_by_user_id") != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        doc["id"] = str(doc.pop("_id"))
        return doc

    prop_query = ownerhub_property_visibility_query(current_user)
    prop_ids_cursor = db.owner_properties.find(prop_query, {"_id": 1})
    prop_docs = await prop_ids_cursor.to_list(500)
    prop_ids = [str(p["_id"]) for p in prop_docs]
    if doc.get("property_id") not in prop_ids:
        raise HTTPException(status_code=403, detail="Access denied")
    doc["id"] = str(doc.pop("_id"))
    return doc


# ── Submit Request ─────────────────────────────────────────────────────────

@router.post("/", status_code=201)
async def submit_request(
        data: MaintenanceRequestCreate,
        current_user: dict = Depends(_require_auth)
):
    """Generated function header.

    Function: submit_request
    Path: backend/routers/tenant_maintenance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await create_maintenance_request(
        data.model_dump(exclude_none=True), current_user
    )


# ── List Requests ──────────────────────────────────────────────────────────

@router.get("/")
async def list_requests(
        status: str = None,
        urgency: str = None,
        current_user: dict = Depends(_require_auth)
):
    """Generated function header.

    Function: list_requests
    Path: backend/routers/tenant_maintenance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    filters = {}
    if status:
        filters["status"] = status
    if urgency:
        filters["urgency"] = urgency
    return await get_maintenance_requests(filters, current_user)


# ── Get Single Request ─────────────────────────────────────────────────────

@router.get("/{request_id}")
async def get_request(
        request_id: str,
        current_user: dict = Depends(_require_auth)
):
    """Generated function header.

    Function: get_request
    Path: backend/routers/tenant_maintenance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await _assert_request_access(request_id, current_user)
    messages = await get_maintenance_messages(request_id)
    return {**doc, "messages": messages}


# ── Update Request ─────────────────────────────────────────────────────────

@router.put("/{request_id}")
async def update_request(
        request_id: str,
        data: MaintenanceRequestUpdate,
        current_user: dict = Depends(_require_manager)
):
    """Generated function header.

    Function: update_request
    Path: backend/routers/tenant_maintenance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await _assert_request_access(request_id, current_user)
    return await update_maintenance_request(
        request_id, data.model_dump(exclude_none=True), current_user
    )


# ── Message Thread ─────────────────────────────────────────────────────────

@router.get("/{request_id}/messages")
async def get_messages(
        request_id: str,
        current_user: dict = Depends(_require_auth)
):
    """Generated function header.

    Function: get_messages
    Path: backend/routers/tenant_maintenance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await _assert_request_access(request_id, current_user)
    return await get_maintenance_messages(request_id)


@router.post("/{request_id}/messages", status_code=201)
async def post_message(
        request_id: str,
        data: MaintenanceRequestMessage,
        current_user: dict = Depends(_require_auth)
):
    """Generated function header.

    Function: post_message
    Path: backend/routers/tenant_maintenance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await _assert_request_access(request_id, current_user)
    return await add_maintenance_message(
        request_id, data.model_dump(exclude_none=True), current_user
    )


# ── Photo Upload ───────────────────────────────────────────────────────────

@router.post("/upload-photo")
async def upload_photo(
        request_id: str,
        file: UploadFile = File(...),
        current_user: dict = Depends(_require_auth)
):
    """Upload a maintenance photo. Returns the relative URL path."""
    await _assert_request_access(request_id, current_user)

    # Validate file type — check both content-type and extension to prevent spoofing
    content_type = file.content_type or ""
    ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
    ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/heic"}
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Only image files are accepted")
    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Invalid file extension. Allowed: jpg, png, gif, webp, heic")

    # Sanitize request_id to prevent path traversal — accept only hex ObjectId chars (24 hex chars)
    import re
    safe_request_id = re.sub(r"[^a-f0-9]", "", request_id.lower())[:24]
    if len(safe_request_id) != 24:
        raise HTTPException(status_code=400, detail="Invalid request ID format")

    # Ensure upload directory exists under the resolved UPLOAD_BASE
    upload_dir = (UPLOAD_BASE / safe_request_id).resolve()
    if not str(upload_dir).startswith(str(UPLOAD_BASE.resolve())):
        raise HTTPException(status_code=400, detail="Invalid upload path")
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique filename with validated extension only
    filename = f"{uuid.uuid4().hex}{ext}"
    file_path = upload_dir / filename

    contents = await file.read()
    from utils.file_scan import scan_upload  # noqa: PLC0415
    await scan_upload(contents, context="image", filename=file.filename or "")
    with open(file_path, "wb") as f:
        f.write(contents)

    relative_url = f"/uploads/maintenance/{safe_request_id}/{filename}"
    return {"url": relative_url, "filename": filename}
