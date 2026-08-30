from fastapi import APIRouter, Depends
from typing import Optional

from utils.auth import get_optional_user, get_optional_building

router = APIRouter(prefix="/test-analytics")


@router.get("/market-snapshot")
async def test_get_market_snapshot(
        current_user: Optional[dict] = Depends(get_optional_user),
        building_id: str = Depends(get_optional_building)
):
    return {"user": bool(current_user), "building_id": building_id}
