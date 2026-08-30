"""
Building assets router module.

Handles management of building assets, keys, and lock codes.
"""

import io
import json
import uuid
from datetime import datetime, timezone

import asyncio
from cryptography.fernet import Fernet
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from typing import List, Optional, Dict, Any

from config import ENCRYPTION_KEY, SYSTEM_HIDDEN_EMAIL
from database import db
from models.building import BuildingAssetCreate, BuildingAssetResponse, BuildingAssetUpdate
from models.user import UserRole
from services.owner_service import OWNER_EQUIVALENT_USER_ROLES
from services.settings_service import get_general_settings_or_default
from utils.auth import get_current_user, get_current_building, effective_role
from utils.name_utils import format_owner_names

router = APIRouter(prefix="/building/assets")

# Initialize encryption suite
cipher_suite = Fernet(ENCRYPTION_KEY.encode())


def encrypt_sensitive_data(data: Dict[str, Any]) -> Optional[str]:
    """Encrypt dictionary data to string."""
    if not data:
        return None
    try:
        json_data = json.dumps(data)
        return cipher_suite.encrypt(json_data.encode()).decode()
    except Exception as e:
        print(f"Encryption error: {e}")
        return None


def decrypt_sensitive_data(encrypted_str: Optional[str]) -> Dict[str, Any]:
    """Decrypt string data back to dictionary."""
    if not encrypted_str:
        return {}
    try:
        decrypted_data = cipher_suite.decrypt(encrypted_str.encode()).decode()
        return json.loads(decrypted_data)
    except Exception as e:
        print(f"Decryption error: {e}")
        return {"error": "Decryption failed"}


def mask_asset_response(asset: Dict[str, Any], current_user: dict) -> Dict[str, Any]:
    """Decrypt sensitive data if Super Admin, otherwise mask it."""
    # Only Super Admin can view decrypted sensitive data as per security requirement
    if effective_role(current_user) == UserRole.SUPER_ADMIN:
        asset["sensitive_data"] = decrypt_sensitive_data(asset.get("sensitive_data_encrypted"))
    else:
        asset["sensitive_data"] = {}  # Masked for others

    # Remove the encrypted field from response
    asset.pop("sensitive_data_encrypted", None)
    return asset


@router.post("", response_model=BuildingAssetResponse)
async def create_asset(
        asset: BuildingAssetCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create a new building asset.
    Only Strata Manager, Chairman, and Super Admin can manage assets.
    """
    if effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized to manage assets")

    asset_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    asset_dict = asset.model_dump()
    sensitive_data = asset_dict.pop("sensitive_data", {})

    asset_doc = {
        "id": asset_id,
        "building_id": building_id,
        **asset_dict,
        "sensitive_data_encrypted": encrypt_sensitive_data(sensitive_data),
        "created_at": now,
        "updated_at": now,
        "created_by": current_user["id"]
    }

    await db.building_assets.insert_one(asset_doc)
    return BuildingAssetResponse(**mask_asset_response(asset_doc, current_user))


@router.get("", response_model=List[BuildingAssetResponse])
async def get_assets(
        category: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get all building assets.
    Restricted to admin roles as per requirement.
    """
    if effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized to view asset register")

    query = {"building_id": building_id}
    if category:
        query["category"] = category

    assets = await db.building_assets.find(query, {"_id": 0}).sort("name", 1).to_list(500)
    return [BuildingAssetResponse(**mask_asset_response(a, current_user)) for a in assets]


@router.get("/pet-register")
async def get_pet_register(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Get building-wide pet register.
    - Admins (super_admin, chairman, strata_manager, ec_member) see ALL requests
      (pending, approved, rejected) so they can process the workflow.
    - Residents see only approved pets.
    """
    role = effective_role(current_user)
    admin_roles = {UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER}

    if role not in admin_roles:
        if not current_user.get("is_approved"):
            raise HTTPException(status_code=403, detail="Not authorized")
        # Regular residents: approved pets only
        # Performance Optimization⚡: Explicit limit and sort
        pets = await db.pet_requests.find(
            {"building_id": building_id, "status": "approved"}, {"_id": 0}
        ).sort("created_at", -1).to_list(200)
        return {"view": "register", "pets": pets}

    # Admins: all statuses for full workflow management
    query = {"building_id": building_id}
    pets = await db.pet_requests.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    return {"view": "admin", "pets": pets}


@router.get("/strata-roll")
async def get_strata_roll(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Get the formal Strata Roll / Corporate Register."""
    if effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized to view Strata Roll")

    # Performance Optimization⚡: Parallelize unit and relationship fetching to reduce I/O wait time.
    tasks = [
        db.units.find({"building_id": building_id}, {"_id": 0}).sort("unit_number", 1).to_list(None),
        db.user_units.find(
            {
                "building_id": building_id,
                "is_active": True,
                "$or": [
                    {"role_at_unit": "owner"},
                    {"role_at_unit": {"$exists": False}},
                    {"role_at_unit": None},
                ],
            }
        ).to_list(None)
    ]
    units, user_units = await asyncio.gather(*tasks)
    units.sort(key=lambda u: int(u["unit_number"]) if str(u.get("unit_number", "")).isdigit() else float("inf"))

    # Performance Optimization⚡: Deduplicate user IDs and use targeted field projection to reduce data transfer.
    user_ids = list({uu["user_id"] for uu in user_units if uu.get("user_id")})
    users = await db.users.find(
        {"id": {"$in": user_ids}, "email": {"$ne": SYSTEM_HIDDEN_EMAIL}},
        {
            "id": 1, "full_name": 1, "email": 1, "phone": 1, "postal_address": 1,
            "postal_suburb": 1, "postal_state": 1, "postal_postcode": 1,
            "strata_roll_consent": 1, "role": 1, "_id": 0
        }
    ).to_list(len(user_ids) if user_ids else None)
    user_map = {u["id"]: u for u in users}

    # Performance Optimization⚡: Create a hash map for $O(1)$ relationship lookups,
    # replacing the $O(N \times M)$ nested loop pattern.
    rel_map = {}
    for uu in user_units:
        unit_num = uu["unit_number"]
        if unit_num not in rel_map:
            rel_map[unit_num] = []
        rel_map[unit_num].append(uu)

    roll = []
    for unit in units:
        unit_num = unit["unit_number"]
        related_user_rels = rel_map.get(unit_num, [])

        registered_owners = []
        for rel in related_user_rels:
            user_id = rel.get("user_id")
            if not user_id:
                continue
            usr = user_map.get(user_id)
            if usr and (
                    rel.get("role_at_unit") == "owner"
                    or (rel.get("role_at_unit") in (None, "") and usr.get("role") in OWNER_EQUIVALENT_USER_ROLES)
            ):
                has_consent = usr.get("strata_roll_consent", False)

                email = usr["email"]
                phone = usr.get("phone", "N/A")
                address = f"{usr.get('postal_address', '')}, {usr.get('postal_suburb', '')} {usr.get('postal_state', '')} {usr.get('postal_postcode', '')}".strip(
                    ", ")

                if not has_consent:
                    # Mask PII if no consent
                    parts = email.split("@")
                    if len(parts) == 2:
                        name_part, domain = parts
                        masked_name = name_part[0] + "***" if name_part else "***"
                        email = masked_name + "@" + domain
                    else:
                        email = "****"

                    phone = "****"
                    address = "**** (Consent Required)"

                registered_owners.append({
                    "full_name": usr["full_name"],
                    "email": email,
                    "phone": phone,
                    "postal_address": address,
                    "is_on_platform": True,
                    "has_consent": has_consent,
                    "is_primary": rel.get("is_primary", False)
                })

        # Resolve display owner_name/email from user_units (canonical) with fallback
        # to legacy units fields for units not yet on the portal.
        # Prefer the record explicitly marked is_primary=True; fall back to the
        # alphabetically-first entry so the result is stable regardless of DB order.
        primary_registered = next(
            (owner for owner in registered_owners if owner.get("is_primary")),
            None
        )
        if primary_registered is None and registered_owners:
            primary_registered = sorted(
                registered_owners,
                key=lambda owner: (
                    owner.get("full_name") or "",
                    owner.get("email") or ""
                )
            )[0]
        canonical_owner_name = (
            primary_registered["full_name"] if primary_registered
            else unit.get("owner_name")
        )
        canonical_owner_email = (
            primary_registered["email"] if primary_registered
            else unit.get("owner_email")
        )

        # Build the full legal ownership string (may include co-owner from unit record)
        co_owner_name = unit.get("co_owner_name")
        all_owner_names = [n for n in [canonical_owner_name, co_owner_name] if n]

        roll_entry = {
            "unit_number": unit_num,
            "lot_number": unit.get("lot_number"),
            "uoe": unit.get("entitlement"),
            "owner_name": canonical_owner_name,
            "co_owner_name": co_owner_name,
            # Convenience field: all legal owners joined for display
            "all_owner_names": " & ".join(all_owner_names) if all_owner_names else None,
            "owner_email": canonical_owner_email,
            "is_owner_occupied": unit.get("is_owner_occupied"),
            "registered_owners": registered_owners,
            "purchase_date": unit.get("purchase_date"),
            "notes": unit.get("notes")
        }
        roll.append(roll_entry)

    return roll


@router.get("/strata-roll/pdf")
async def get_strata_roll_pdf(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generate the formal Strata Roll as a landscape PDF (ACT UTMA 2011, s.91)."""
    # Use effective_role to honour temporary elevation (e.g. owner → ec_member).
    # _effective_role() lives in server.py and cannot be imported here; inline the same logic.
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized to export Strata Roll")

    # Fetch all data in parallel
    tasks = [
        db.units.find({"building_id": building_id}, {"_id": 0}).sort("lot_number", 1).to_list(None),
        db.user_units.find(
            {
                "building_id": building_id,
                "is_active": True,
                "$or": [
                    {"role_at_unit": "owner"},
                    {"role_at_unit": {"$exists": False}},
                    {"role_at_unit": None},
                ],
            }
        ).to_list(None),
        get_general_settings_or_default(building_id, {"_id": 0}),
    ]
    units, user_units, settings = await asyncio.gather(*tasks)
    units.sort(key=lambda u: int(u["lot_number"]) if str(u.get("lot_number", "")).isdigit() else float("inf"))

    # Build user map
    user_ids = list({uu["user_id"] for uu in user_units if uu.get("user_id")})
    users = []
    if user_ids:
        users = await db.users.find(
            {"id": {"$in": user_ids}, "email": {"$ne": SYSTEM_HIDDEN_EMAIL}},
            {"id": 1, "full_name": 1, "email": 1, "postal_address": 1,
             "postal_suburb": 1, "postal_state": 1, "postal_postcode": 1,
             "strata_roll_consent": 1, "role": 1, "_id": 0}
        ).to_list(len(user_ids))
    user_map = {u["id"]: u for u in users}

    rel_map: Dict[str, list] = {}
    for uu in user_units:
        user = user_map.get(uu.get("user_id"))
        if user and (
                uu.get("role_at_unit") == "owner"
                or (uu.get("role_at_unit") in (None, "") and user.get("role") in OWNER_EQUIVALENT_USER_ROLES)
        ):
            rel_map.setdefault(uu["unit_number"], []).append(uu)

    plan_number = building_id
    building_name = (settings or {}).get("building_name", "East Gate Residences")
    building_address = (settings or {}).get("building_address", "14 Hoolihan Street, Denman Prospect ACT 2611")
    printed_by = current_user.get("full_name", current_user.get("email", "Admin"))
    printed_at = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M")

    # Build PDF in memory
    buf = io.BytesIO()
    page_w, page_h = landscape(A4)
    margin = 15 * mm
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=margin, rightMargin=margin,
        topMargin=20 * mm, bottomMargin=15 * mm,
    )

    styles = getSampleStyleSheet()
    header_style = ParagraphStyle(
        "StrataHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=14,
        textColor=colors.HexColor("#1a3c5e"),
        spaceAfter=2,
    )
    sub_style = ParagraphStyle(
        "StrataSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        textColor=colors.HexColor("#444444"),
        spaceAfter=1,
    )
    cell_style = ParagraphStyle(
        "Cell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=10,
    )
    cell_bold = ParagraphStyle(
        "CellBold",
        parent=cell_style,
        fontName="Helvetica-Bold",
    )
    crn_style = ParagraphStyle(
        "CRN",
        parent=cell_style,
        fontSize=6.5,
        textColor=colors.HexColor("#666666"),
    )

    story = []

    # ── Header block ──
    story.append(Paragraph("OWNER LIST", header_style))
    story.append(Paragraph(f"Unit Plan {plan_number} — {building_name}", sub_style))
    story.append(Paragraph(f"{building_address}", sub_style))
    story.append(Paragraph(
        f"Printed: {printed_at} &nbsp;&nbsp; By: {printed_by}",
        ParagraphStyle("meta", parent=sub_style, fontSize=7.5, textColor=colors.HexColor("#888888"))
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1a3c5e"), spaceAfter=6))

    # ── Table ──
    col_widths = [18 * mm, 22 * mm, 55 * mm, 95 * mm, 40 * mm, 22 * mm]  # Lot, Unit, Owner, Address, Occ, UOE

    header_row = [
        Paragraph("<b>Lot</b>", cell_bold),
        Paragraph("<b>Unit</b>", cell_bold),
        Paragraph("<b>Owner(s)</b>", cell_bold),
        Paragraph("<b>Correspondence Address</b>", cell_bold),
        Paragraph("<b>Occupancy</b>", cell_bold),
        Paragraph("<b>UOE</b>", cell_bold),
    ]
    table_data = [header_row]

    for unit in units:
        unit_num = unit["unit_number"]
        # Lot number: strip "LOT" prefix if present
        raw_lot = str(unit.get("lot_number", "")).upper().replace("LOT", "").strip()

        # Owner name(s): resolve from user_units (canonical), fallback to units fields
        rels = rel_map.get(unit_num, [])
        resolved_owners = [
            user_map[rel["user_id"]]["full_name"]
            for rel in rels
            if rel.get("user_id") and rel.get("user_id") in user_map
        ]
        if resolved_owners:
            owner_display = "\n& ".join(resolved_owners)
        else:
            owner_display = format_owner_names(
                unit.get("owner_name") or "",
                unit.get("owner_name_b") or "",
                separator="\n& ",
            )
        # strata_roll_consent is True; otherwise fall back to the unit-derived address.
        address_lines = []
        rels = rel_map.get(unit_num, [])
        for rel in rels:
            usr = user_map.get(rel["user_id"])
            if usr and usr.get("strata_roll_consent", False):
                parts = [
                    usr.get("postal_address", ""),
                    f"{usr.get('postal_suburb', '')} {usr.get('postal_state', '')} {usr.get('postal_postcode', '')}".strip()
                ]
                addr = ", ".join(p for p in parts if p)
                if addr:
                    address_lines.append(addr)
                    break

        if not address_lines:
            address_lines.append(unit.get("full_property_address") or unit.get("address") or "")

        address_text = "\n".join(address_lines) if address_lines else "—"

        occ_type = "Owner Occupied" if unit.get("is_owner_occupied", True) else "Rented"
        uoe = str(unit.get("entitlement") or unit.get("unit_entitlement") or "")

        # Build cell paragraphs
        owner_para = Paragraph(owner_display.replace("\n", "<br/>"), cell_style)
        addr_para = Paragraph(address_text.replace("\n", "<br/>"), cell_style)
        occ_para = Paragraph(occ_type, cell_style)

        table_data.append([
            Paragraph(raw_lot, cell_style),
            Paragraph(unit_num, cell_style),
            owner_para,
            addr_para,
            occ_para,
            Paragraph(uoe, cell_style),
        ])

    tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a3c5e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        # Body rows
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 7.5),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        # Alternating rows
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f4f8")]),
        # Grid
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#1a3c5e")),
    ]))
    story.append(tbl)

    def _add_page_number(canvas, doc):
        """Generated function header.

        Function: _add_page_number
        Path: backend/routers/building.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#888888"))
        page_str = f"Page {doc.page}   |   Unit Plan {plan_number}   |   {building_name}   |   CONFIDENTIAL"
        canvas.drawString(margin, 8 * mm, page_str)
        canvas.restoreState()

    doc.build(story, onFirstPage=_add_page_number, onLaterPages=_add_page_number)
    buf.seek(0)

    filename = f"strata_roll_{plan_number}_{datetime.now().strftime('%Y%m%d')}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{asset_id}", response_model=BuildingAssetResponse)
async def get_asset(
        asset_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Get a specific building asset."""
    if effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    asset = await db.building_assets.find_one({"id": asset_id, "building_id": building_id}, {"_id": 0})
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    return BuildingAssetResponse(**mask_asset_response(asset, current_user))


@router.put("/{asset_id}", response_model=BuildingAssetResponse)
async def update_asset(
        asset_id: str,
        updates: BuildingAssetUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Update a building asset."""
    if effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    update_data = updates.model_dump(exclude_unset=True)

    if "sensitive_data" in update_data:
        update_data["sensitive_data_encrypted"] = encrypt_sensitive_data(update_data.pop("sensitive_data"))

    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    result = await db.building_assets.update_one({"id": asset_id, "building_id": building_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Asset not found")

    asset = await db.building_assets.find_one({"id": asset_id, "building_id": building_id}, {"_id": 0})
    return BuildingAssetResponse(**mask_asset_response(asset, current_user))


@router.delete("/{asset_id}")
async def delete_asset(
        asset_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Delete a building asset."""
    if effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.building_assets.delete_one({"id": asset_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Asset not found")

    return {"message": "Asset deleted successfully"}
