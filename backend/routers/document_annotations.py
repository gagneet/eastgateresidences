# @featuretrace:documents — Document annotation CRUD router
# Layer: router
# Data flow: PDFAnnotatorDialog → GET/POST/PUT/DELETE /documents/{id}/annotations
#            → document_annotations collection (building-scoped)
# Related: frontend/src/components/documents/PDFAnnotatorDialog.tsx
#           backend/routers/documents.py
#           backend/models/document_annotation.py

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query

from database import db
from models.document_annotation import (
    AnnotationCreate,
    AnnotationResponse,
    AnnotationUpdate,
)
from utils.auth import get_current_building, get_current_user
from utils.helpers import get_current_utc_iso

router = APIRouter(prefix="")

_IMAGE_B64_MAX_BYTES = 512 * 1024  # 512 KB limit on area-highlight screenshots

_MANAGER_ROLES = {"super_admin", "strata_manager", "ec_member"}


def _effective_role(user: dict) -> str:
    """Generated function header.

    Function: _effective_role
    Path: backend/routers/document_annotations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return user.get("effective_role") or user.get("role", "guest")


def _is_manager(user: dict) -> bool:
    """Generated function header.

    Function: _is_manager
    Path: backend/routers/document_annotations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return _effective_role(user) in _MANAGER_ROLES


async def _get_document_or_404(doc_id: str, building_id: str) -> dict:
    """Verify document exists in this building — BOLA guard."""
    doc = await db.documents.find_one(
        {"id": doc_id, "building_id": building_id}, {"_id": 0, "file_data": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


# ── List annotations ──────────────────────────────────────────────────────────

@router.get(
    "/documents/{doc_id}/annotations",
    response_model=List[AnnotationResponse],
    summary="List annotations for a document",
)
async def list_annotations(
        doc_id: str,
        include_test_data: bool = Query(False),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: list_annotations
    Path: backend/routers/document_annotations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await _get_document_or_404(doc_id, building_id)
    query = {"document_id": doc_id, "building_id": building_id}
    if not include_test_data:
        query["is_test_data"] = {"$ne": True}
    annotations = await db.document_annotations.find(query, {"_id": 0}).to_list(1000)
    return [AnnotationResponse(**a) for a in annotations]


# ── Create annotation ─────────────────────────────────────────────────────────

@router.post(
    "/documents/{doc_id}/annotations",
    response_model=AnnotationResponse,
    status_code=201,
    summary="Create an annotation on a document",
)
async def create_annotation(
        doc_id: str,
        body: AnnotationCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: create_annotation
    Path: backend/routers/document_annotations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await _get_document_or_404(doc_id, building_id)

    # Guard: area-highlight image must not exceed 512 KB
    if body.content.image and len(body.content.image) > _IMAGE_B64_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Area highlight image exceeds {_IMAGE_B64_MAX_BYTES // 1024} KB limit",
        )

    now = get_current_utc_iso()
    annotation_id = str(uuid.uuid4())
    annotation = {
        "id": annotation_id,
        "highlight_id": body.highlight_id,
        "document_id": doc_id,
        "building_id": building_id,
        "position": body.position.model_dump(),
        "content": body.content.model_dump(),
        "comment": body.comment.model_dump(),
        "created_by": current_user["id"],
        "created_by_name": current_user.get("full_name", ""),
        "created_at": now,
        "updated_at": now,
        "is_test_data": bool(doc.get("is_test_data")),
    }
    await db.document_annotations.insert_one(annotation)
    return AnnotationResponse(**annotation)


# ── Update annotation comment ─────────────────────────────────────────────────

@router.put(
    "/documents/{doc_id}/annotations/{ann_id}",
    response_model=AnnotationResponse,
    summary="Update annotation comment (owner only)",
)
async def update_annotation(
        doc_id: str,
        ann_id: str,
        body: AnnotationUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: update_annotation
    Path: backend/routers/document_annotations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await _get_document_or_404(doc_id, building_id)
    ann = await db.document_annotations.find_one(
        {"id": ann_id, "document_id": doc_id, "building_id": building_id}, {"_id": 0}
    )
    if not ann:
        raise HTTPException(status_code=404, detail="Annotation not found")

    if ann["created_by"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Only the annotation owner can edit it")

    now = get_current_utc_iso()
    await db.document_annotations.update_one(
        {"id": ann_id, "building_id": building_id},
        {"$set": {"comment": body.comment.model_dump(), "updated_at": now}},
    )
    ann["comment"] = body.comment.model_dump()
    ann["updated_at"] = now
    return AnnotationResponse(**ann)


# ── Delete annotation ─────────────────────────────────────────────────────────

@router.delete(
    "/documents/{doc_id}/annotations/{ann_id}",
    summary="Delete an annotation (owner or manager)",
)
async def delete_annotation(
        doc_id: str,
        ann_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: delete_annotation
    Path: backend/routers/document_annotations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await _get_document_or_404(doc_id, building_id)
    ann = await db.document_annotations.find_one(
        {"id": ann_id, "document_id": doc_id, "building_id": building_id}, {"_id": 0}
    )
    if not ann:
        raise HTTPException(status_code=404, detail="Annotation not found")

    # Only the creator or a manager may delete
    if ann["created_by"] != current_user["id"] and not _is_manager(current_user):
        raise HTTPException(status_code=403, detail="Not authorised to delete this annotation")

    await db.document_annotations.delete_one(
        {"id": ann_id, "building_id": building_id}
    )
    return {"message": "Annotation deleted"}
