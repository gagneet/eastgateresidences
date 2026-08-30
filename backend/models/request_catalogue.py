"""Public API models for the request catalogue."""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class RequestCatalogueDefinitionResponse(BaseModel):
    """Public, eligible request definition returned to the current user."""

    model_config = ConfigDict(extra="forbid")

    key: str
    version: int
    title: str
    description: str
    category: str
    search_terms: list[str] = Field(default_factory=list)
    expected_response: str
    documents: str
    fee: Optional[str] = None
    legacy_tab_aliases: list[str] = Field(default_factory=list)
    recommended: bool = False


class RequestCatalogueErrorResponse(BaseModel):
    """Documented error shape used by FastAPI for catalogue failures."""

    detail: str
