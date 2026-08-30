"""
Utilities router — Electricity, Gas, and NBN connection details per unit.

Data sourced from East Gate Residences strata plan 13195 meter schedules:
  - Electricity: ActewAGL/Evoenergy LOC IDs (embedded network metering)
  - Gas: Origin Energy meter serial numbers (townhouses only)
  - NBN: Network Metering Identifiers (NMI) and Property Numbers

All data is static (meter IDs do not change) and is embedded directly
in this module rather than in MongoDB to avoid unnecessary DB overhead.

Auth rules:
  - Owners / Tenants : view utilities for their own unit only
  - EC / Chairman / Strata Manager / Super Admin : view any unit
"""

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer
from pydantic import BaseModel
from typing import Optional

from database import db
from models.user import UserRole
from utils.auth import get_current_user, get_current_building, effective_role
from utils.permissions import get_user_permissions

# ─────────────────────────────────────────────────────────────────────────────
# Static Meter Data (East Gate Residences, 14 Hoolihan St, Denman Prospect ACT)
# ─────────────────────────────────────────────────────────────────────────────

# Lot number (1–87) → Electricity LOC ID
# Units 1–70  = Apartments (UA001–UA070)
# Units 71–87 = Townhouses (TH001–TH017)
_ELECTRICITY_LOC: dict[int, str] = {
    1: "LOC000186230612", 2: "LOC000186230620", 3: "LOC000186230631",
    4: "LOC000186230649", 5: "LOC000186230654", 6: "LOC000186230665",
    7: "LOC000186230677", 8: "LOC000186230683", 9: "LOC000186230696",
    10: "LOC000186230704", 11: "LOC000186230715", 12: "LOC000186230727",
    13: "LOC000186230736", 14: "LOC000186230743", 15: "LOC000186230758",
    16: "LOC000186230762", 17: "LOC000186230770", 18: "LOC000186230789",
    19: "LOC000186230791", 20: "LOC000186230801", 21: "LOC000186230817",
    22: "LOC000186230829", 23: "LOC000186230838", 24: "LOC000186230840",
    25: "LOC000186230855", 26: "LOC000186230864", 27: "LOC000186230872",
    28: "LOC000186230886", 29: "LOC000186230893", 30: "LOC000186230903",
    31: "LOC000186230919", 32: "LOC000186230926", 33: "LOC000186230935",
    34: "LOC000186230942", 35: "LOC000186230957", 36: "LOC000186230961",
    37: "LOC000186230974", 38: "LOC000186230988", 39: "LOC000186230990",
    40: "LOC000186231006", 41: "LOC000186231010", 42: "LOC000186231023",
    43: "LOC000186231034", 44: "LOC000186231047", 45: "LOC000186231052",
    46: "LOC000186231068", 47: "LOC000186231075", 48: "LOC000186231081",
    49: "LOC000186231099", 50: "LOC000186231109", 51: "LOC000186231113",
    52: "LOC000186231121", 53: "LOC000186231132", 54: "LOC000186231145",
    55: "LOC000186231150", 56: "LOC000186231166", 57: "LOC000186231178",
    58: "LOC000186231184", 59: "LOC000186231197", 60: "LOC000186231204",
    61: "LOC000186231215", 62: "LOC000186231227", 63: "LOC000186231236",
    64: "LOC000186231243", 65: "LOC000186231258", 66: "LOC000186231262",
    67: "LOC000186231270", 68: "LOC000186231289", 69: "LOC000186231291",
    70: "LOC000186231301",
    # Townhouses
    71: "LOC000186231317", 72: "LOC000186231329", 73: "LOC000186231338",
    74: "LOC000186231340", 75: "LOC000186231355", 76: "LOC000186231364",
    77: "LOC000186231372", 78: "LOC000186231386", 79: "LOC000186231393",
    80: "LOC000186231408", 81: "LOC000186231412", 82: "LOC000186231420",
    83: "LOC000186231431", 84: "LOC000186231449", 85: "LOC000186231454",
    86: "LOC000186231465", 87: "LOC000186231477",
}

# Lot number → gas meter serial number (townhouses with gas only)
# Townhouses 72, 74, 75, 76, 78 have induction cooktops — not in this dict
_GAS_METERS: dict[int, str] = {
    71: "EC 930 472",  # TH001
    73: "EC 930 380",  # TH003
    77: "EC 930 381",  # TH007
    79: "EC 930 470",  # TH009
    80: "EC 930 371",  # TH010
    81: "EC 930 376",  # TH011
    82: "EC 930 382",  # TH012
    83: "EC 930 379",  # TH013
    84: "EC 930 384",  # TH014
    85: "EC 930 374",  # TH015
    86: "EC 930 385",  # TH016
    87: "EC 930 372",  # TH017
}

# Lot number → (NMI, Property Number)
_NBN: dict[int, tuple[str, str]] = {
    1: ("ENNE019259", "ED041905391"), 2: ("ENNE019260", "ED041905392"),
    3: ("ENNE019261", "ED041905385"), 4: ("ENNE019262", "ED041905386"),
    5: ("ENNE019263", "ED041905383"), 6: ("ENNE019264", "ED041905384"),
    7: ("ENNE019265", "ED041905387"), 8: ("ENNE019266", "ED041905388"),
    9: ("ENNE019267", "ED041905389"), 10: ("ENNE019268", "ED041905390"),
    11: ("ENNE019269", "ED041905381"), 12: ("ENNE019270", "ED041905382"),
    13: ("ENNE019271", "ED041905379"), 14: ("ENNE019272", "ED041905380"),
    15: ("ENNE019273", "ED041905393"), 16: ("ENNE019274", "ED041905394"),
    17: ("ENNE019275", "ED041905395"), 18: ("ENNE019276", "ED041905396"),
    19: ("ENNE019277", "ED041905371"), 20: ("ENNE019278", "ED041905372"),
    21: ("ENNE019279", "ED041905373"), 22: ("ENNE019280", "ED041905374"),
    23: ("ENNE019281", "ED041905397"), 24: ("ENNE019282", "ED041905398"),
    25: ("ENNE019283", "ED041905399"), 26: ("ENNE019284", "ED041905400"),
    27: ("ENNE019285", "ED041905375"), 28: ("ENNE019286", "ED041905376"),
    29: ("ENNE019287", "ED041905377"), 30: ("ENNE019288", "ED041905378"),
    31: ("ENNE019289", "ED041905367"), 32: ("ENNE019290", "ED041905368"),
    33: ("ENNE019291", "ED041905369"), 34: ("ENNE019292", "ED041905370"),
    35: ("ENNE019293", "ED041904473"), 36: ("ENNE019294", "ED041904474"),
    37: ("ENNE019295", "ED041904475"), 38: ("ENNE019296", "ED041904476"),
    39: ("ENNE019297", "ED041605827"), 40: ("ENNE019298", "ED041905450"),
    41: ("ENNE019299", "ED041904468"), 42: ("ENNE019300", "ED041904492"),
    43: ("ENNE019301", "ED041904477"), 44: ("ENNE019302", "ED041904478"),
    45: ("ENNE019303", "ED041904479"), 46: ("ENNE019304", "ED041904480"),
    47: ("ENNE019305", "ED041904493"), 48: ("ENNE019306", "ED041904497"),
    49: ("ENNE019307", "ED041904498"), 50: ("ENNE019308", "ED041904499"),
    51: ("ENNE019309", "ED041905359"), 52: ("ENNE019310", "ED041905360"),
    53: ("ENNE019311", "ED041905361"), 54: ("ENNE019312", "ED041905362"),
    55: ("ENNE019313", "ED041905351"), 56: ("ENNE019314", "ED041905352"),
    57: ("ENNE019315", "ED041905353"), 58: ("ENNE019316", "ED041905354"),
    59: ("ENNE019317", "ED041905363"), 60: ("ENNE019318", "ED041905364"),
    61: ("ENNE019319", "ED041905365"), 62: ("ENNE019320", "ED041905366"),
    63: ("ENNE019321", "ED041905355"), 64: ("ENNE019322", "ED041905356"),
    65: ("ENNE019323", "ED041905357"), 66: ("ENNE019324", "ED041905358"),
    67: ("ENNE019325", "ED041904469"), 68: ("ENNE019326", "ED041904470"),
    69: ("ENNE019327", "ED041904471"), 70: ("ENNE019328", "ED041904472"),
    71: ("ENNE019329", "ED041904451"), 72: ("ENNE019330", "ED041904452"),
    73: ("ENNE019331", "ED041904453"), 74: ("ENNE019332", "ED041904454"),
    75: ("ENNE019333", "ED041904455"), 76: ("ENNE019334", "ED041904456"),
    77: ("ENNE019335", "ED041904457"), 78: ("ENNE019336", "ED041904458"),
    79: ("ENNE019337", "ED041904459"), 80: ("ENNE019338", "ED041904460"),
    81: ("ENNE019339", "ED041904461"), 82: ("ENNE019340", "ED041904462"),
    83: ("ENNE019341", "ED041904463"), 84: ("ENNE019342", "ED041904464"),
    85: ("ENNE019343", "ED041904465"), 86: ("ENNE019344", "ED041904466"),
    87: ("ENNE019345", "ED041904467"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Response models
# ─────────────────────────────────────────────────────────────────────────────

class ElectricityInfo(BaseModel):
    unit_number: str
    lot_number: int
    loc_id: Optional[str] = None
    supplier: str = "Origin Energy"
    distributor: str = "Evoenergy (ActewAGL)"
    supply_charge_per_day: float = 1.467840
    usage_charge_per_unit: float = 0.256740
    faults_emergency: str = "1800 002 438"
    assistance: str = "1300 137 427"
    my_account_url: str = "https://www.originenergy.com.au/myaccount"


class GasInfo(BaseModel):
    unit_number: str
    lot_number: int
    has_gas: bool
    cooktop_type: str  # "Gas" | "Induction"
    meter_number: Optional[str] = None
    supplier: Optional[str] = None
    distributor: str = "Evoenergy"
    supply_charge_per_day: Optional[float] = None
    usage_charge_first_3863: Optional[float] = None
    faults_evoenergy: str = "1300 137 078"
    emergencies_evoenergy: str = "13 19 09"
    assistance: Optional[str] = None
    my_account_url: Optional[str] = None


class NBNInfo(BaseModel):
    unit_number: str
    lot_number: int
    nmi: Optional[str] = None
    property_number: Optional[str] = None
    supplier: str = "National Broadband Network Pty Ltd"
    note: str = (
        "The Broadband connectivity is specific to the Person staying in the Unit. "
        "Contact your chosen retail service provider (RSP) to connect or transfer service."
    )


class UtilitiesInfo(BaseModel):
    unit_number: str
    lot_number: int
    electricity: ElectricityInfo
    gas: GasInfo
    nbn: NBNInfo


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _unit_to_lot(unit_number: str) -> Optional[int]:
    """
    Convert a strata unit number string to its lot number (1–87).

    UA001–UA070  →  1–70   (apartments, lot == unit sequence)
    TH071–TH087  →  71–87  (townhouses, lot == unit number, NOT offset)

    Note: Production unit numbers for townhouses are TH071–TH087 (matching
    their lot numbers directly).  Legacy TH001–TH017 style is no longer used.
    """
    unit_number = unit_number.upper().strip()
    m = re.match(r"^(UA|TH)(\d+)$", unit_number)
    if not m:
        return None
    prefix, digits = m.group(1), int(m.group(2))
    if prefix == "UA":
        if 1 <= digits <= 70:
            return digits
    elif prefix == "TH":
        # Production format: TH071–TH087 where the number equals the lot number
        if 71 <= digits <= 87:
            return digits
        # Legacy fallback: TH001–TH017 offset by 70 (kept for backwards compat)
        if 1 <= digits <= 17:
            return digits + 70
    return None


def _build_utilities(unit_number: str, lot: int) -> UtilitiesInfo:
    """Generated function header.

    Function: _build_utilities
    Path: backend/routers/utilities.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    gas_meter = _GAS_METERS.get(lot)
    has_gas = gas_meter is not None and lot >= 71  # only townhouses can have gas

    elec = ElectricityInfo(
        unit_number=unit_number,
        lot_number=lot,
        loc_id=_ELECTRICITY_LOC.get(lot),
    )

    gas = GasInfo(
        unit_number=unit_number,
        lot_number=lot,
        has_gas=has_gas,
        cooktop_type="Gas" if has_gas else "Induction",
        meter_number=gas_meter if has_gas else None,
        supplier="Origin Gas" if has_gas else "Origin Energy",
        supply_charge_per_day=0.912670 if has_gas else None,
        usage_charge_first_3863=0.054890 if has_gas else None,
        assistance="1300 137 427" if has_gas else None,
        my_account_url="https://www.originenergy.com.au/myaccount" if has_gas else None,
    )

    nbn_data = _NBN.get(lot)
    nbn = NBNInfo(
        unit_number=unit_number,
        lot_number=lot,
        nmi=nbn_data[0] if nbn_data else None,
        property_number=nbn_data[1] if nbn_data else None,
    )

    return UtilitiesInfo(
        unit_number=unit_number,
        lot_number=lot,
        electricity=elec,
        gas=gas,
        nbn=nbn,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Router
# ─────────────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="")
security = HTTPBearer(auto_error=False)


def _is_privileged(user: dict) -> bool:
    """Generated function header.

    Function: _is_privileged
    Path: backend/routers/utilities.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return effective_role(user) in (
        UserRole.SUPER_ADMIN,
        UserRole.EC_MEMBER,
        UserRole.STRATA_MANAGER,
    )


@router.get("/utilities/{unit_number}", response_model=UtilitiesInfo)
async def get_utilities(
        unit_number: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Return electricity, gas, and NBN utility details for a unit. Scoped to building.

    Owners and tenants may only view their own unit's details.
    Privileged roles may view any unit.
    Requires can_view_finances permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized to view finances")

    unit_upper = unit_number.upper().strip()

    if not _is_privileged(current_user):
        owner_unit = (current_user.get("unit_number") or "").upper()
        if owner_unit != unit_upper:
            raise HTTPException(
                status_code=403,
                detail="You may only view utility details for your own unit",
            )

    # Try to find unit in DB first to confirm existence in this building
    unit_doc = await db.units.find_one({"building_id": building_id, "unit_number": unit_upper})
    if not unit_doc:
        raise HTTPException(status_code=404, detail=f"Unit {unit_upper} not found in this building")

    util_data = await db.unit_utilities.find_one({"building_id": building_id, "unit_number": unit_upper})
    if util_data:
        return UtilitiesInfo(**util_data)

    # Generic fallback if no specific meter data is found
    lot_num_raw = unit_doc.get("lot_number", 0)
    # lot_number may be a string like "LOT87" — extract leading digits only
    try:
        lot_num = int(lot_num_raw) if lot_num_raw else 0
    except (ValueError, TypeError):
        import re as _re
        digits = _re.sub(r"\D", "", str(lot_num_raw))
        lot_num = int(digits) if digits else 0
    return UtilitiesInfo(
        unit_number=unit_upper,
        lot_number=lot_num,
        electricity=ElectricityInfo(unit_number=unit_upper, lot_number=lot_num),
        gas=GasInfo(unit_number=unit_upper, lot_number=lot_num, has_gas=False, cooktop_type="Induction"),
        nbn=NBNInfo(unit_number=unit_upper, lot_number=lot_num)
    )
