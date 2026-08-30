"""
Organisation Models — Multi-Building Architecture

An Organisation groups multiple strata buildings under a single strata
management firm or self-managed owner's corporation network.

Collections:
  organisations           : one per firm/network
  organisation_buildings  : many-to-many: org → buildings
  organisation_members    : user access to org (cross-building roles)

This enables:
  - Strata management companies managing multiple buildings
  - Consolidated reporting across buildings
  - Per-building isolation (existing building_id pattern preserved)
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional


class OrgType(str):
    STRATA_MANAGER = "strata_manager"
    SELF_MANAGED = "self_managed"
    DEVELOPER = "developer"


class OrgStatus(str):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TRIAL = "trial"


class OrganisationCreate(BaseModel):
    """Create a new organisation."""
    name: str = Field(..., max_length=200)
    org_type: str = "strata_manager"  # OrgType
    abn: Optional[str] = Field(None, max_length=14)
    licence_number: Optional[str] = None  # Real estate agent licence
    licence_expiry: Optional[str] = None  # ISO-8601 date
    contact_name: str = Field(..., max_length=200)
    contact_email: str
    contact_phone: Optional[str] = None
    address: Optional[str] = None
    website: Optional[str] = None
    logo_url: Optional[str] = None
    subscription_plan: str = "starter"  # starter | professional | enterprise
    notes: Optional[str] = None


class OrganisationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    org_type: str
    abn: Optional[str] = None
    licence_number: Optional[str] = None
    licence_expiry: Optional[str] = None
    contact_name: str
    contact_email: str
    contact_phone: Optional[str] = None
    address: Optional[str] = None
    website: Optional[str] = None
    logo_url: Optional[str] = None
    subscription_plan: str
    building_count: int = 0
    member_count: int = 0
    status: str = "active"
    created_at: str
    updated_at: Optional[str] = None


class OrgBuildingAssignment(BaseModel):
    """Assign a building to an organisation."""
    building_id: str
    role: str = "managed"  # managed | self_managed | consulting


class OrgMemberCreate(BaseModel):
    """Grant a user access to an organisation (cross-building)."""
    user_id: str
    role: str = "viewer"  # owner | admin | manager | viewer
    building_ids: List[str] = []  # empty = all buildings in org


class OrgMemberResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    organisation_id: str
    user_id: str
    role: str
    building_ids: List[str] = []
    joined_at: str
