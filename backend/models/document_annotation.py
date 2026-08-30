# @featuretrace:documents — Pydantic models for document annotation data layer
# Layer: model
# Data flow: PDFAnnotatorDialog → POST /documents/{id}/annotations → document_annotations collection
# Related: backend/routers/document_annotations.py
#           frontend/src/components/documents/PDFAnnotatorDialog.tsx
# Scope: (building-scoped)

"""
Pydantic models for document annotations (highlight + comment layer).
Data structure mirrors react-pdf-highlighter-extended's IHighlight for
lossless round-trip serialisation through the API.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class BoundingRect(BaseModel):
    """Pixel-independent bounding box on a PDF page."""
    x1: float
    y1: float
    x2: float
    y2: float
    width: float
    height: float
    pageNumber: int


class AnnotationPosition(BaseModel):
    """Location of the highlight — viewport-independent (react-pdf-highlighter-extended format)."""
    boundingRect: BoundingRect
    rects: List[BoundingRect]
    pageNumber: int


class AnnotationContent(BaseModel):
    """Selected text or area screenshot captured by the highlight."""
    text: Optional[str] = None
    # base64 PNG screenshot for area highlights; capped at 512 KB server-side
    image: Optional[str] = None


class AnnotationComment(BaseModel):
    """User comment attached to a highlight."""
    text: str = ""
    emoji: str = ""


class AnnotationCreate(BaseModel):
    """Request body for creating a new annotation."""
    highlight_id: str = Field(..., description="Client-generated UUID — used for optimistic UI updates")
    position: AnnotationPosition
    content: AnnotationContent
    comment: AnnotationComment


class AnnotationUpdate(BaseModel):
    """Request body for updating an annotation's comment only."""
    comment: AnnotationComment


class AnnotationResponse(BaseModel):
    """Full annotation as stored in MongoDB, returned to callers."""
    model_config = ConfigDict(extra="ignore")

    id: str
    highlight_id: str
    document_id: str
    building_id: str
    position: AnnotationPosition
    content: AnnotationContent
    comment: AnnotationComment
    created_by: str
    created_by_name: str
    created_at: str
    updated_at: str
