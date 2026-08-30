"""Per-building Strata Web portal connection config models.

Replaces the single global PORTAL_EMAIL / PORTAL_PASSWORD env vars with per-building connection
details so the scraper can select a building and use ITS configuration to connect to the external
Strata Web portal (my.civium.com.au). The password is stored encrypted at rest (Fernet, via
utils.encryption) and is NEVER returned by any API — reads expose only ``password_set``.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class StrataWebPortalConfigUpdate(BaseModel):
    """Request body for PUT /settings/strata-web-portal. All fields optional — only provided
    fields are updated. ``password`` is write-only; omit it to keep the stored one unchanged."""
    model_config = ConfigDict(extra="ignore")

    portal_url: Optional[str] = Field(default=None, description="Portal login URL, e.g. https://my.civium.com.au")
    scheme_number: Optional[str] = Field(default=None, description="The portal's scheme/plan identifier for this building")
    username: Optional[str] = Field(default=None, description="Portal login username/email")
    password: Optional[str] = Field(default=None, description="Portal login password (write-only; encrypted at rest, never returned)")
    enabled: Optional[bool] = Field(default=None, description="Whether the scraper may use this config")


class StrataWebPortalConfigResponse(BaseModel):
    """Masked config for GET responses — never carries the password itself."""
    model_config = ConfigDict(extra="ignore")

    building_id: str
    portal_url: Optional[str] = None
    scheme_number: Optional[str] = None
    username: Optional[str] = None
    password_set: bool = False
    enabled: bool = False
    last_synced_at: Optional[str] = None
    last_sync_status: Optional[str] = None
    updated_at: Optional[str] = None
    updated_by: Optional[str] = None
