"""
Digital Twin Management Router
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends
from typing import List

from database import db
from models.digital_twin import (
    BenefitGroup,
    BenefitGroupUpdate,
    BuildingAsset,
    BuildingAssetUpdate,
    Facility,
    FacilityUpdate,
    Zone,
    ZoneUpdate,
)
from models.user import UserRole
from utils.auth import get_approved_user, get_current_user, get_current_building, effective_role

router = APIRouter(prefix="/digital-twin")

MANAGER_ROLES = [UserRole.SUPER_ADMIN, UserRole.STRATA_MANAGER, UserRole.EC_MEMBER]

_IMMUTABLE = {"id", "building_id", "created_at"}


def _update_fields(data, immutable=_IMMUTABLE) -> dict:
    """Fields a PUT may write, taken from a validated partial-update model.

    Only keys the caller actually sent are returned (`exclude_unset`), so a partial
    update never clobbers a field with the model default. Unknown keys are dropped by
    Pydantic before reaching Mongo — these handlers used to `$set` a raw dict, which
    both bypassed the models' length bounds and let a caller write arbitrary new keys
    into the document.

    Raises 400 rather than returning {} — an empty `$set` is a pymongo error, and now
    that unknown keys are dropped a body of nothing but unknown keys would reach it.
    """
    fields = {k: v for k, v in data.model_dump(exclude_unset=True).items() if k not in immutable}
    if not fields:
        raise HTTPException(status_code=400, detail="No updatable fields provided")
    return fields


# ==================== ZONES ====================

@router.get("/zones", response_model=List[Zone])
async def get_zones(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_zones
    Path: backend/routers/digital_twin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await db.zones.find({"building_id": building_id}, {"_id": 0}).to_list(100)


@router.post("/zones", response_model=Zone)
async def create_zone(
        zone: dict,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: create_zone
    Path: backend/routers/digital_twin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")
    doc = Zone(id=str(uuid.uuid4()), building_id=building_id, **zone)
    await db.zones.insert_one(doc.model_dump())
    return doc


@router.put("/zones/{zone_id}", response_model=Zone)
async def update_zone(
        zone_id: str,
        data: ZoneUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: update_zone
    Path: backend/routers/digital_twin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")
    update = _update_fields(data)
    result = await db.zones.update_one({"id": zone_id, "building_id": building_id}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Zone not found")
    return await db.zones.find_one({"id": zone_id, "building_id": building_id}, {"_id": 0})


@router.delete("/zones/{zone_id}")
async def delete_zone(
        zone_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: delete_zone
    Path: backend/routers/digital_twin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")
    result = await db.zones.delete_one({"id": zone_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Zone not found")
    return {"status": "deleted"}


# ==================== FACILITIES ====================

@router.get("/facilities", response_model=List[Facility])
async def get_facilities(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_facilities
    Path: backend/routers/digital_twin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await db.facilities.find({"building_id": building_id}, {"_id": 0}).to_list(100)


@router.post("/facilities", response_model=Facility)
async def create_facility(
        facility: dict,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: create_facility
    Path: backend/routers/digital_twin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")
    doc = Facility(id=str(uuid.uuid4()), building_id=building_id, **facility)
    await db.facilities.insert_one(doc.model_dump())
    return doc


@router.put("/facilities/{facility_id}", response_model=Facility)
async def update_facility(
        facility_id: str,
        data: FacilityUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: update_facility
    Path: backend/routers/digital_twin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")
    update = _update_fields(data)
    result = await db.facilities.update_one({"id": facility_id, "building_id": building_id}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Facility not found")
    return await db.facilities.find_one({"id": facility_id, "building_id": building_id}, {"_id": 0})


@router.delete("/facilities/{facility_id}")
async def delete_facility(
        facility_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: delete_facility
    Path: backend/routers/digital_twin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")
    result = await db.facilities.delete_one({"id": facility_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Facility not found")
    return {"status": "deleted"}


# ==================== ASSETS ====================

@router.get("/assets", response_model=List[BuildingAsset])
async def get_assets(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_assets
    Path: backend/routers/digital_twin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await db.building_assets.find({"building_id": building_id}, {"_id": 0}).to_list(1000)


@router.post("/assets", response_model=BuildingAsset)
async def create_asset(
        asset: dict,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: create_asset
    Path: backend/routers/digital_twin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")
    doc = BuildingAsset(id=str(uuid.uuid4()), building_id=building_id, **asset)
    await db.building_assets.insert_one(doc.model_dump())
    return doc


@router.put("/assets/{asset_id}", response_model=BuildingAsset)
async def update_asset(
        asset_id: str,
        data: BuildingAssetUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: update_asset
    Path: backend/routers/digital_twin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")
    update = _update_fields(data)
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.building_assets.update_one({"id": asset_id, "building_id": building_id}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Asset not found")
    return await db.building_assets.find_one({"id": asset_id, "building_id": building_id}, {"_id": 0})


@router.delete("/assets/{asset_id}")
async def delete_asset(
        asset_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: delete_asset
    Path: backend/routers/digital_twin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")
    result = await db.building_assets.delete_one({"id": asset_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Asset not found")
    return {"status": "deleted"}


# ==================== BENEFIT GROUPS ====================

@router.get("/benefit-groups", response_model=List[BenefitGroup])
async def get_benefit_groups(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_benefit_groups
    Path: backend/routers/digital_twin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await db.benefit_groups.find({"building_id": building_id}, {"_id": 0}).to_list(100)


@router.post("/benefit-groups", response_model=BenefitGroup)
async def create_benefit_group(
        bg: dict,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: create_benefit_group
    Path: backend/routers/digital_twin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")
    doc = BenefitGroup(id=str(uuid.uuid4()), building_id=building_id, **bg)
    await db.benefit_groups.insert_one(doc.model_dump())
    return doc


@router.put("/benefit-groups/{bg_id}", response_model=BenefitGroup)
async def update_benefit_group(
        bg_id: str,
        data: BenefitGroupUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: update_benefit_group
    Path: backend/routers/digital_twin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")
    update = _update_fields(data)
    result = await db.benefit_groups.update_one({"id": bg_id, "building_id": building_id}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Benefit group not found")
    return await db.benefit_groups.find_one({"id": bg_id, "building_id": building_id}, {"_id": 0})


@router.delete("/benefit-groups/{bg_id}")
async def delete_benefit_group(
        bg_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: delete_benefit_group
    Path: backend/routers/digital_twin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")
    result = await db.benefit_groups.delete_one({"id": bg_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Benefit group not found")
    return {"status": "deleted"}
