# @featuretrace:letters — WeasyPrint letter generation router
# Layer: router
# Data flow: LettersPage → POST /letters/generate → letter_generator.py (WeasyPrint) → StreamingResponse(PDF)
#            LettersPage → POST /letters/send → generate + email to unit owners → letters_log
#            LettersPage → GET  /letters/history → letters_log (building-scoped)
# Related: frontend/src/pages/dashboard/admin/LettersPage.tsx
#           backend/utils/letter_generator.py
#           backend/models/letter.py

import uuid
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse

from database import db
from models.letter import (
    TEMPLATE_SCHEMAS,
    LetterGenerateRequest,
    LetterLogEntry,
    LetterSendRequest,
    LetterTemplateInfo,
)
from services.owner_service import get_all_unit_owners
from services.settings_service import get_general_settings_or_default
from utils.auth import effective_role, get_current_building, get_current_user
from utils.email import send_email_async
from utils.helpers import get_current_utc_iso
from utils.letter_generator import generate_letter_pdf, render_letter_html

router = APIRouter(prefix="")

# "chairman" is not a top-level role (removed migration 0025); effective_role() returns
# "ec_member" for a chairman — normalize_user_role() does NOT map it to "strata_admin"
# (see models/user.py). "ec_member" must be listed directly or EC executives (including
# the chairman) cannot generate letters despite the docstring below saying they can.
_LETTER_ROLES = {"super_admin", "strata_admin", "strata_manager", "ec_member"}
_RATE_LIMIT_PER_MINUTE = 10


def _require_letter_role(user: dict) -> None:
    """Generated function header.

    Function: _require_letter_role
    Path: backend/routers/letters.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(user) not in _LETTER_ROLES:
        raise HTTPException(status_code=403, detail="Only strata managers and executives may generate letters")


async def _get_building_info(building_id: str) -> dict:
    """Fetch scheme identity and the canonical document-branding profile source."""
    building = await db.buildings.find_one(
        {"id": building_id},
        {"_id": 0, "name": 1, "address": 1, "id": 1},
    ) or {}
    settings = await get_general_settings_or_default(building_id, {"_id": 0})
    # General settings are authoritative for document identity. The building record
    # remains a compatibility fallback for installations not yet configured.
    return {**building, **(settings or {}), "building_id": building_id}


# ── List available templates ──────────────────────────────────────────────────

@router.get(
    "/letters/templates",
    response_model=List[LetterTemplateInfo],
    summary="List available letter templates with field schema",
)
async def list_templates(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: list_templates
    Path: backend/routers/letters.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_letter_role(current_user)
    return [
        LetterTemplateInfo(name=name, **{k: v for k, v in schema.items()})
        for name, schema in TEMPLATE_SCHEMAS.items()
    ]


# ── Generate PDF (preview / download) ────────────────────────────────────────

@router.post(
    "/letters/generate",
    summary="Generate a letter PDF — returns application/pdf stream",
)
async def generate_letter(
        body: LetterGenerateRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: generate_letter
    Path: backend/routers/letters.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_letter_role(current_user)
    building = await _get_building_info(building_id)

    try:
        pdf_bytes = await generate_letter_pdf(body.template, body.data, building)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    filename = f"letter_{body.template}_{building_id}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Send letter(s) to unit owners ─────────────────────────────────────────────

@router.post(
    "/letters/send",
    summary="Render letter as HTML and email to specified unit owners; log in letters_log",
)
async def send_letters(
        body: LetterSendRequest,
        background_tasks: BackgroundTasks,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: send_letters
    Path: backend/routers/letters.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_letter_role(current_user)

    # Rate guard — count letters sent in the last 60 s for this building
    one_min_ago = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    recent_count = await db.letters_log.count_documents(
        {"building_id": building_id, "sent_at": {"$gte": one_min_ago}, "is_test_data": {"$ne": True}}
    )
    if recent_count >= _RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429,
                            detail=f"Rate limit: max {_RATE_LIMIT_PER_MINUTE} letters/minute per building")

    building = await _get_building_info(building_id)

    # Resolve canonical owner emails (user_units → users, with legacy fallback)
    all_owners = await get_all_unit_owners(building_id)

    subject = body.data.get("subject") or TEMPLATE_SCHEMAS[body.template]["label"]
    sent_count = 0
    for unit_number in body.unit_numbers:
        owner = all_owners.get(unit_number)
        if not owner:
            continue
        email = owner.get("owner_email")
        if not email:
            continue
        owner_name = owner.get("owner_name", "Owner")
        background_tasks.add_task(
            send_email_async,
            email,
            subject,
            render_letter_html(body.template, {**body.data, "owner_name": owner_name}, building),
        )
        sent_count += 1

    # Log the send
    now = get_current_utc_iso()
    log_entry = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "template": body.template,
        "unit_numbers": body.unit_numbers,
        "sent_by": current_user["id"],
        "sent_by_name": current_user.get("full_name", ""),
        "sent_at": now,
        "recipient_count": sent_count,
        "subject": subject,
    }
    await db.letters_log.insert_one(log_entry)

    return {"sent": sent_count, "queued": sent_count, "logged_at": now}


# ── Letter history ─────────────────────────────────────────────────────────────

@router.get(
    "/letters/history",
    response_model=List[LetterLogEntry],
    summary="List previously sent letters for this building",
)
async def letter_history(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: letter_history
    Path: backend/routers/letters.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_letter_role(current_user)
    entries = await db.letters_log.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}},
        {"_id": 0},
    ).to_list(200)
    return [LetterLogEntry(**e) for e in entries]
