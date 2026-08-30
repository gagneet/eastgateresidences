# @featuretrace:request-catalogue — Authenticated catalogue discovery endpoint.
# Layer: router
# Data flow: RequestsPage -> GET /api/request-catalogue -> request_catalogue_service.
# Related: backend/services/request_catalogue_service.py

"""Request catalogue API."""

from fastapi import APIRouter, Depends

from models.request_catalogue import (
    RequestCatalogueDefinitionResponse,
    RequestCatalogueErrorResponse,
)
from services.request_catalogue_service import resolve_request_catalogue
from utils.auth import get_current_building
from utils.auth import get_current_user


router = APIRouter(prefix="/request-catalogue", tags=["Request Catalogue"])


@router.get(
    "",
    response_model=list[RequestCatalogueDefinitionResponse],
    responses={
        401: {"model": RequestCatalogueErrorResponse, "description": "Authentication is required."},
        403: {"model": RequestCatalogueErrorResponse, "description": "Building context, feature or role denied."},
        404: {"model": RequestCatalogueErrorResponse, "description": "Definition not found for direct lookups."},
        409: {"model": RequestCatalogueErrorResponse, "description": "Definition version conflict."},
        422: {"model": RequestCatalogueErrorResponse, "description": "Invalid request parameters."},
    },
)
async def get_request_catalogue(
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Return only definitions authorised for this user and building context."""
    return await resolve_request_catalogue(current_user, building_id)
