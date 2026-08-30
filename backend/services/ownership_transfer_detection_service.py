# @featuretrace:owner-transfers — Detect owner-name drift from imported owner snapshots and create
# reviewable transfer requests; also bootstraps a canonical user_units link (and, separately, a
# pending invite record) for units with no active owner link at all — a distinct precondition the
# drift detector can't handle (GAP-IDENTITY-OWNER-BOOTSTRAP-001) — and backfills the missing
# co-owner link on a PARTIALLY-linked unit (link_missing_co_owners), which neither of the other
# two paths can reach. A pure owner-set addition is never a transfer: joint ownership is lawful.
# Layer: service
# Data flow: external owner snapshot rows -> owner_transfer_requests + user_notifications
#            (building-scoped); units.owner_name/owner_email -> users + user_units + memberships +
#            strata_owners + owner_transfer_requests (bootstrap path) -> owner_invites (invite path,
#            does not itself send email — see backend/scripts/send_owner_bootstrap_invites.py).
# Related: backend/routers/strata_sync.py
#           backend/seeds/migrate_strata_sync_to_financial.py
#           backend/scripts/data_repair/bootstrap_initial_owner_links_20260819.py
#           backend/scripts/data_repair/create_owner_bootstrap_invites_20260819.py
#           backend/scripts/send_owner_bootstrap_invites.py
from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from utils.auth import hash_password

logger = logging.getLogger(__name__)

# 'chairman' is NOT a top-level user.role value (see rules/post-compact-critical.md) — a
# chairman is a user with role 'ec_member' and ec_position 'CHAIRMAN', so 'ec_member' alone
# already covers them for reviewer notification.
OWNER_TRANSFER_REVIEWER_ROLES = [
    "super_admin",
    "strata_manager",
    "ec_member",
    "strata_admin",
    "real_estate_agent",
]
PENDING_OWNER_TRANSFER_STATUSES = ["pending", "pending_second_approval"]
PORTAL_DETECTED_TRANSFER_SOURCE = "external_ledger_owner_name_drift"
# Returned instead of creating a transfer when the imported owner set only ADDS
# names to the current canonical set. Joint ownership is legal and routine; it is
# not a change of ownership. See detect_and_create_portal_owner_transfer().
CO_OWNER_ADDITION_REASON = "co_owner_addition_not_a_transfer"
INTERNAL_OWNER_EMAIL_DOMAIN = "strataos.local"


def _collection(db_like: Any, name: str) -> Any:
    """Generated function header.

    Function: _collection
    Path: backend/services/ownership_transfer_detection_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    collection = getattr(db_like, name, None)
    if collection is not None:
        return collection
    return db_like[name]


def split_owner_names(combined: str | None) -> list[str]:
    """Generated function header.

    Function: split_owner_names
    Path: backend/services/ownership_transfer_detection_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    text = (combined or "").strip()
    if not text:
        return []
    parts = re.split(r"\s+(?:&|and)\s+|,\s*", text, flags=re.IGNORECASE)
    return [part.strip() for part in parts if part and part.strip()]


def _name_key(name: str | None) -> str:
    """Generated function header.

    Function: _name_key
    Path: backend/services/ownership_transfer_detection_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    text = re.sub(r"\s+", " ", (name or "").strip().lower())
    text = re.sub(r"[^a-z0-9 ]+", "", text)
    # Strip honorifics (2026-08-28). The portal renders the same owner with and without
    # a title between scrapes -- "rachel clarke" one run, "ms rachel clarke" the next --
    # and without this the key changes, the signature changes, and a cosmetic difference
    # is raised as an OWNERSHIP TRANSFER for EC review.
    #
    # Measured on the 2026-08-28 East Gate scrape: 29 transfer requests were created and
    # 28 of them were title-only drift ("jason carter" => "mr jason carter"). Exactly one
    # (UA029, "emma watt" => "sonja zink") was a real change. That noise re-pollutes the
    # review queue the operator had deliberately purged of 88 system-generated rows.
    #
    # Applied per WORD, not just at the start, because a multi-owner string carries a
    # title on each person ("mr tin leung ms jennifer leung"). Order is already handled
    # upstream by sorting the key sets, so only titles needed addressing.
    #
    # NOTE the residual risk this accepts: two people distinguished ONLY by title
    # (a hypothetical "mr smith" vs "ms smith" with no given name) now collapse to one
    # key. The portal supplies full names, so this is not reachable with real data --
    # but it is the reason this strips titles rather than, say, ignoring case entirely.
    stripped = re.sub(r"\b(mr|mrs|ms|miss|mx|dr|prof|sir|madam)\b", "", text)
    return re.sub(r"\s+", " ", stripped).strip()


def _display_names(names: list[str]) -> str:
    """Generated function header.

    Function: _display_names
    Path: backend/services/ownership_transfer_detection_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return " & ".join([name for name in names if name]) or "Unknown"


def _owner_info_from_names(unit_number: str, names: list[str], *, source: str) -> list[dict]:
    """Build non-user-backed owner entries for legacy snapshots or operator overrides."""
    return [
        {
            "user_id": f"{source}:{unit_number}:{_name_key(name)}",
            "full_name": name,
            "email": None,
            "is_legacy_snapshot": True,
        }
        for name in names
        if _name_key(name)
    ]


def _project_imported_owner_names(scraped_names: list[str], _current_owners: list[dict]) -> list[str]:
    """Return the owner-name set implied by one imported owner row.

    Imported snapshots are authoritative for owner cardinality. If the import
    contains one owner, the projected owner set must contain one owner only; if
    the import contains multiple owners, use that full set.
    """
    # Important: never append baseline secondary owners here. Doing so can retain
    # stale co-owners after a true one-owner transfer (e.g. TH078: new single
    # owner imported while a legacy secondary still exists in baseline records).
    return scraped_names


async def _cutover_owner_names(building_id: str, unit_number: str) -> list[str] | None:
    """Owner names from whichever store actually SERVES this building, or None.

    MongoDB `user_units` is not universally the canonical owner baseline. For a
    building whose `identity_core` / `occupancy` domains are promoted (East Gate
    since 2026-08-02) with `owner_read_pg_enabled`, owner reads are served from
    Postgres — and PG can hold joint owners that Mongo's links are missing. A
    detector that compares an import against Mongo alone therefore measures drift
    against the wrong baseline: on 2026-08-20 all four East Gate "joint owner
    transfer" false positives had BOTH owners present in `core.ownership_periods`
    the whole time.

    Returns None whenever the serving source is Mongo, the read fails, or the unit
    is not found there — the caller then keeps its Mongo baseline. Fallback is
    directional (PG attempt -> Mongo fallback), never the reverse.
    """
    from request_context import get_ctx_building_id, set_ctx_building_id
    from services.owner_service import get_owner_info

    # owner_service reads through the tenant-scoped db wrapper, which needs building
    # context set. A live HTTP request already has it; a script or worker does not,
    # and without this the lookup raises "Missing building context" and every caller
    # silently falls back to the Mongo baseline — which is exactly the blindness this
    # function exists to remove. Save and restore so context never leaks between
    # buildings in a loop over a portfolio.
    previous_building_id = get_ctx_building_id()
    try:
        set_ctx_building_id(building_id)
        record = await get_owner_info(unit_number, building_id)
    except Exception:  # noqa: BLE001 - never let a baseline lookup break detection
        logger.warning(
            "owner-drift baseline: serving-store lookup failed for %s/%s; "
            "falling back to the MongoDB baseline",
            building_id,
            unit_number,
            exc_info=True,
        )
        return None
    finally:
        set_ctx_building_id(previous_building_id)
    if (record or {}).get("source") != "postgres_owner_read":
        return None
    names = [
        name
        for name in [record.get("owner_name"), record.get("owner_name_b")]
        if name and name != "Unknown"
    ]
    return names or None


async def _active_owner_info(
    db_like: Any,
    building_id: str,
    unit_number: str,
    *,
    use_cutover_baseline: bool = False,
) -> list[dict]:
    """Generated function header.

    Function: _active_owner_info
    Path: backend/services/ownership_transfer_detection_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    user_units = await _collection(db_like, "user_units").find(
        {
            "building_id": building_id,
            "unit_number": unit_number,
            "role_at_unit": "owner",
            "is_active": True,
        },
        {"_id": 0, "user_id": 1, "is_primary": 1},
    ).to_list(20)

    owner_ids = [rel.get("user_id") for rel in user_units if rel.get("user_id")]
    users_by_id: dict[str, dict] = {}
    if owner_ids:
        users = await _collection(db_like, "users").find(
            {"id": {"$in": owner_ids}},
            {"_id": 0, "id": 1, "full_name": 1, "first_name": 1, "last_name": 1, "email": 1},
        ).to_list(len(owner_ids))
        users_by_id = {user["id"]: user for user in users if user.get("id")}

    owner_info = []
    for rel in user_units:
        user = users_by_id.get(rel.get("user_id"))
        if not user:
            continue
        full_name = (
            user.get("full_name")
            or f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
        )
        if not full_name:
            continue
        owner_info.append(
            {
                "user_id": user.get("id"),
                "full_name": full_name,
                "email": user.get("email"),
                "is_primary": rel.get("is_primary", False),
            }
        )

    if use_cutover_baseline:
        served_names = await _cutover_owner_names(building_id, unit_number)
        if served_names:
            by_key = {
                _name_key(owner.get("full_name")): owner
                for owner in owner_info
                if _name_key(owner.get("full_name"))
            }
            merged = []
            for name in served_names:
                key = _name_key(name)
                if not key:
                    continue
                # Keep the Mongo entry where one exists: it carries the real
                # user_id that suggested_remove_owner_ids needs. Owners the
                # serving store knows about but Mongo has not linked yet get a
                # user_id-less entry, so they count toward the baseline without
                # ever being proposed for removal.
                merged.append(
                    by_key.get(key)
                    or {
                        "user_id": None,
                        "full_name": name,
                        "email": None,
                        "is_primary": not merged,
                        "is_served_source_only": True,
                    }
                )
            if merged:
                return merged

    if owner_info:
        owner_info.sort(key=lambda item: (not item.get("is_primary"), item.get("full_name") or ""))
        return owner_info

    unit = await _collection(db_like, "units").find_one(
        {"building_id": building_id, "unit_number": unit_number},
        {"_id": 0, "owner_name": 1, "owner_name_b": 1},
    )
    if not unit:
        return []

    fallback = []
    for name in [unit.get("owner_name"), unit.get("owner_name_b")]:
        if name:
            fallback.append(
                {
                    "user_id": f"legacy-owner:{unit_number}:{_name_key(name)}",
                    "full_name": name,
                    "email": None,
                    "is_legacy_snapshot": True,
                }
            )
    return fallback


async def _ensure_portal_detected_owner_user(
    db_like: Any,
    building_id: str,
    unit_number: str,
    full_name: str,
    now: str,
) -> dict:
    """Generated function header.

    Function: _ensure_portal_detected_owner_user
    Path: backend/services/ownership_transfer_detection_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    name_key = _name_key(full_name)
    existing = await _collection(db_like, "users").find_one(
        {
            "building_id": building_id,
            "unit_number": unit_number,
            "portal_detected_owner_name_key": name_key,
            "portal_detected_owner": True,
        },
        {"_id": 0},
    )
    if existing:
        return existing

    user_id = str(uuid.uuid4())
    email = f"owner-transfer+{user_id}@{INTERNAL_OWNER_EMAIL_DOMAIN}"
    user_doc = {
        "id": user_id,
        "email": email,
        "full_name": full_name,
        "first_name": "",
        "last_name": "",
        "role": "owner",
        "building_id": building_id,
        "unit_number": unit_number,
        "is_approved": False,
        "is_active": False,
        "status": "pending_owner_transfer",
        "requires_account_setup": True,
        "portal_detected_owner": True,
        "portal_detected_owner_name_key": name_key,
        "is_internal_contact_email": True,
        "created_at": now,
        "updated_at": now,
    }
    await _collection(db_like, "users").insert_one(user_doc)
    return user_doc


def _source_label(source: str) -> str:
    """Generated function header.

    Function: _source_label
    Path: backend/services/ownership_transfer_detection_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if source == "strata_web_portal_owner_name_drift":
        return "Strata Web portal data"
    if source == PORTAL_DETECTED_TRANSFER_SOURCE:
        return "Imported ledger data"
    return source.replace("_", " ")


async def _notify_reviewers(
    db_like: Any,
    building_id: str,
    unit_number: str,
    new_owner_name: str,
    now: str,
    source: str,
) -> int:
    """Generated function header.

    Function: _notify_reviewers
    Path: backend/services/ownership_transfer_detection_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    memberships = await _collection(db_like, "memberships").find(
        {"building_id": building_id, "is_active": True},
        {"_id": 0, "user_id": 1},
    ).to_list(2000)
    user_ids = [membership.get("user_id") for membership in memberships if membership.get("user_id")]
    if not user_ids:
        return 0

    users = await _collection(db_like, "users").find(
        {
            "id": {"$in": user_ids},
            "role": {"$in": OWNER_TRANSFER_REVIEWER_ROLES},
            "is_active": True,
        },
        {"_id": 0, "id": 1},
    ).to_list(1000)

    notifications = [
        {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "building_id": building_id,
            "title": "Imported Owner Change Detected",
            "message": (
                f"{_source_label(source)} shows Unit {unit_number} now includes {new_owner_name}. "
                "Review the ownership transfer request."
            ),
            "type": "owner_transfer",
            "link": "/admin/owner-transfers",
            "is_read": False,
            "created_at": now,
        }
        for user in users
        if user.get("id")
    ]
    if not notifications:
        return 0

    await _collection(db_like, "user_notifications").insert_many(notifications)
    return len(notifications)


INITIAL_OWNERSHIP_BOOTSTRAP_SOURCE = "initial_ownership_bootstrap"


async def _ensure_bootstrap_owner_user(
    db_like: Any,
    building_id: str,
    unit_number: str,
    full_name: str,
    now: str,
    *,
    email: str | None = None,
) -> dict:
    """Find-or-create a provisional owner account for the initial bootstrap.

    A real email (when known from the legacy import) is stored on the account
    so a later, separate, explicitly-triggered invite can use it — but the
    account itself stays ``is_active=False`` here. Bootstrapping canonical
    `user_units` attribution must never itself send an unsolicited email to a
    real address; that is a distinct follow-up action.

    Dedup is keyed on (email, name) together, never email alone — real
    co-owners (e.g. spouses) commonly share one household contact email in
    the legacy import (``owner_email == owner_email_b``), and matching on
    email alone would wrongly collapse two distinct people into one account.
    """
    normalized_email = (email or "").strip().lower() or None
    name_key = _name_key(full_name)
    if normalized_email:
        existing = await _collection(db_like, "users").find_one(
            {
                "email": normalized_email,
                "portal_detected_owner_name_key": name_key,
                # Archived accounts were retired deliberately; never silently revive one.
                "status": {"$ne": "archived"},
            },
            {"_id": 0},
        )
        if existing:
            return existing

        # Adopt, don't duplicate. The drift detector mints a provisional
        # portal-detected account (internal @strataos.local email) for a name it
        # sees in an import. Matching on email alone misses it, so bootstrapping
        # the SAME person from a later import that does carry their real email
        # used to create a second account for one human — three of them on East
        # Gate (Radhika Shah, Graciela Pezaroylo Topal, Rose Marimon, 2026-08-20).
        # Take over the provisional record and upgrade it instead, but only while
        # it is genuinely unclaimed: same building/unit/name, still inactive, and
        # not already attached to the unit.
        candidate = await _collection(db_like, "users").find_one(
            {
                "building_id": building_id,
                "unit_number": unit_number,
                "portal_detected_owner_name_key": name_key,
                "portal_detected_owner": True,
                "is_active": False,
                # An archived account was retired deliberately (typically as a
                # duplicate artefact of a withdrawn request). Reviving it would
                # produce a live owner link on a row still flagged is_archived.
                "status": {"$ne": "archived"},
            },
            {"_id": 0},
        )
        if candidate:
            claimed = await _collection(db_like, "user_units").find_one(
                {
                    "building_id": building_id,
                    "user_id": candidate.get("id"),
                    "role_at_unit": "owner",
                    "is_active": True,
                },
                {"_id": 0, "id": 1},
            )
            if not claimed:
                await _collection(db_like, "users").update_one(
                    {"id": candidate["id"]},
                    {
                        "$set": {
                            "email": normalized_email,
                            "is_internal_contact_email": False,
                            "portal_detected_owner": False,
                            "adopted_from_portal_detected_account": True,
                            "updated_at": now,
                        }
                    },
                )
                return {
                    **candidate,
                    "email": normalized_email,
                    "is_internal_contact_email": False,
                    "portal_detected_owner": False,
                    "adopted_from_portal_detected_account": True,
                }

    if not normalized_email:
        existing = await _collection(db_like, "users").find_one(
            {
                "building_id": building_id,
                "unit_number": unit_number,
                "portal_detected_owner_name_key": name_key,
                "portal_detected_owner": True,
                "status": {"$ne": "archived"},
            },
            {"_id": 0},
        )
        if existing:
            return existing

    user_id = str(uuid.uuid4())
    resolved_email = normalized_email or f"owner-transfer+{user_id}@{INTERNAL_OWNER_EMAIL_DOMAIN}"
    user_doc = {
        "id": user_id,
        "email": resolved_email,
        "full_name": full_name,
        "first_name": "",
        "last_name": "",
        "role": "owner",
        "building_id": building_id,
        "unit_number": unit_number,
        "is_approved": False,
        "is_active": False,
        "status": "pending_owner_transfer",
        "requires_account_setup": True,
        "password_hash": hash_password(secrets.token_urlsafe(32)),
        "portal_detected_owner": not bool(normalized_email),
        "portal_detected_owner_name_key": name_key,
        "is_internal_contact_email": not bool(normalized_email),
        "bootstrap_source": INITIAL_OWNERSHIP_BOOTSTRAP_SOURCE,
        "created_at": now,
        "updated_at": now,
    }
    await _collection(db_like, "users").insert_one(user_doc)
    return user_doc


async def ensure_owner_membership(
    db_like: Any,
    building_id: str,
    unit_number: str,
    user_id: str,
    now: str,
    *,
    is_primary: bool,
) -> None:
    """Find-or-create the building membership that backs an owner's unit link.

    Idempotent: an existing membership is reactivated and gains the owner role
    and unit via ``$addToSet`` rather than being replaced.
    """
    existing_membership = await _collection(db_like, "memberships").find_one(
        {"user_id": user_id, "building_id": building_id}, {"_id": 0}
    )
    if existing_membership:
        await _collection(db_like, "memberships").update_one(
            {"user_id": user_id, "building_id": building_id},
            {
                "$set": {"is_active": True, "updated_at": now},
                "$addToSet": {"roles": "owner", "units": unit_number},
            },
        )
        return
    await _collection(db_like, "memberships").insert_one(
        {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "building_id": building_id,
            "roles": ["owner"],
            "is_active": True,
            "is_primary": is_primary,
            "units": [unit_number],
            "created_at": now,
            "updated_at": now,
        }
    )


async def _link_owner_to_unit(
    db_like: Any,
    building_id: str,
    unit_number: str,
    full_name: str,
    email: str | None,
    now: str,
    *,
    is_primary: bool,
) -> dict:
    """Create the provisional account + canonical `user_units` link + membership for one owner.

    Shared by the initial bootstrap and the co-owner backfill so both produce
    byte-identical link/membership records. Returns the linked user document —
    callers need its resolved ``email``, which is the internal
    ``owner-transfer+<id>@strataos.local`` address when the import had none.
    """
    user_doc = await _ensure_bootstrap_owner_user(
        db_like, building_id, unit_number, full_name, now, email=email
    )
    user_id = user_doc["id"]

    await _collection(db_like, "user_units").insert_one(
        {
            "id": str(uuid.uuid4()),
            "building_id": building_id,
            "user_id": user_id,
            "unit_number": unit_number,
            "role_at_unit": "owner",
            "is_primary": is_primary,
            "is_active": True,
            "start_date": now[:10],
            "end_date": None,
            "actual_end_date": None,
            "created_at": now,
            "updated_at": now,
        }
    )

    await ensure_owner_membership(
        db_like, building_id, unit_number, user_id, now, is_primary=is_primary
    )
    return user_doc


async def create_initial_ownership_link(
    db_like: Any,
    building_id: str,
    unit_number: str,
    owner_names: list[str] | str,
    owner_emails: list[str | None] | str | None = None,
    *,
    detected_at: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Bootstrap a canonical `user_units` owner link where none exists yet.

    This is deliberately NOT the drift detector (`detect_and_create_portal_owner_transfer`)
    and NOT the manual staff transfer endpoint — both require an existing
    canonical owner to compare against or transfer from, and 87/87 East Gate
    units have a legacy `units.owner_name` but only 10/87 had an active
    `user_units` link (GAP-IDENTITY-OWNER-BOOTSTRAP-001). This function's
    only precondition is "no active owner link exists yet."

    ``owner_names``/``owner_emails`` are index-aligned (primary owner first).
    A bare string for either is treated as the single primary owner.
    """
    names = (
        split_owner_names(owner_names)
        if isinstance(owner_names, str)
        else [name.strip() for name in (owner_names or []) if name and name.strip()]
    )
    if not names:
        return {"created": False, "reason": "no_owner_name"}

    if isinstance(owner_emails, str) or owner_emails is None:
        emails = [owner_emails] + [None] * (len(names) - 1)
    else:
        emails = list(owner_emails) + [None] * (len(names) - len(owner_emails))

    existing_active = await _collection(db_like, "user_units").find_one(
        {
            "building_id": building_id,
            "unit_number": unit_number,
            "role_at_unit": "owner",
            "is_active": True,
        },
        {"_id": 0, "id": 1},
    )
    if existing_active:
        return {"created": False, "reason": "owner_already_canonical"}

    pending_request = await _collection(db_like, "owner_transfer_requests").find_one(
        {
            "building_id": building_id,
            "unit_number": unit_number,
            "status": {"$in": PENDING_OWNER_TRANSFER_STATUSES},
        },
        {"_id": 0, "id": 1, "source": 1},
    )
    if pending_request:
        return {
            "created": False,
            "reason": "pending_transfer_request_exists",
            "id": pending_request.get("id"),
            "source": pending_request.get("source"),
        }

    now = detected_at or datetime.now(timezone.utc).isoformat()
    primary_email = emails[0] if emails else None

    if dry_run:
        return {
            "created": False,
            "would_create": True,
            "unit_number": unit_number,
            "owner_names": names,
            "owner_email": primary_email,
            "has_email": bool(primary_email),
        }

    created_user_ids: list[str] = []
    for index, name in enumerate(names):
        user_doc = await _link_owner_to_unit(
            db_like,
            building_id,
            unit_number,
            name,
            emails[index],
            now,
            is_primary=index == 0,
        )
        created_user_ids.append(user_doc["id"])

    await _collection(db_like, "strata_owners").update_one(
        {"building_id": building_id, "unit_number": unit_number},
        {"$set": {"user_id": created_user_ids[0], "updated_at": now}},
    )

    audit_id = str(uuid.uuid4())
    await _collection(db_like, "owner_transfer_requests").insert_one(
        {
            "id": audit_id,
            "building_id": building_id,
            "unit_number": unit_number,
            "old_owners": [],
            "new_owner": {
                "user_id": created_user_ids[0],
                "full_name": _display_names(names),
                "email": primary_email,
                "is_provisional": True,
                "is_internal_contact_email": not bool(primary_email),
            },
            "settlement_date": now[:10],
            "request_notes": (
                "Automated initial ownership bootstrap — no prior canonical user_units "
                "link existed for this unit. Source: legacy units.owner_name / "
                "strata_owners import (GAP-IDENTITY-OWNER-BOOTSTRAP-001)."
            ),
            "ownership_documents": [],
            "ownership_verified": False,
            "status": "approved",
            "required_approvals": 1,
            "current_approvals": 1,
            "approval_mode": "auto_bootstrap",
            "approval_history": [
                {
                    "action": "bootstrap_created",
                    "by": f"system:{INITIAL_OWNERSHIP_BOOTSTRAP_SOURCE}",
                    "at": now,
                    "notes": "Auto-approved batch bootstrap — no existing owner to conflict with.",
                }
            ],
            "pending_approval_action": None,
            "requested_date": now,
            "submitted_by_id": f"system:{INITIAL_OWNERSHIP_BOOTSTRAP_SOURCE}",
            "submitted_by_name": "Initial Ownership Bootstrap",
            "submitted_by_role": "system",
            "reviewed_by": None,
            "reviewed_by_name": f"system:{INITIAL_OWNERSHIP_BOOTSTRAP_SOURCE}",
            "reviewed_date": now,
            "review_notes": "Auto-approved — no existing owner to conflict with; batch-authorized.",
            "action_taken": "bootstrap_created",
            "old_owners_notified": False,
            "new_owner_notified": False,
            "source": INITIAL_OWNERSHIP_BOOTSTRAP_SOURCE,
            "created_at": now,
            "updated_at": now,
        }
    )

    return {
        "created": True,
        "unit_number": unit_number,
        "user_ids": created_user_ids,
        "audit_id": audit_id,
    }


CO_OWNER_LINK_BACKFILL_SOURCE = "co_owner_link_backfill"


async def link_missing_co_owners(
    db_like: Any,
    building_id: str,
    unit_number: str,
    owner_names: list[str] | str,
    owner_emails: list[str | None] | str | None = None,
    *,
    detected_at: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Add canonical owner links for imported co-owners a unit is missing.

    Fills the gap between the two existing mechanisms, neither of which can
    handle a PARTIALLY-linked unit:

      - ``create_initial_ownership_link`` refuses any unit that already has an
        active owner link ("owner_already_canonical"), so a unit whose primary
        owner was linked at some earlier point never gets its genuine second
        owner linked.
      - ``detect_and_create_portal_owner_transfer`` deliberately will NOT raise a
        transfer for a pure owner-set addition (``CO_OWNER_ADDITION_REASON``) —
        joint ownership is lawful, not a change of ownership.

    Only ADDS links for imported names with no active link. Never removes,
    retires, or repoints an existing link, and never touches the primary flag,
    so it cannot silently rewrite an ownership record. Idempotent: a second run
    finds nothing missing and writes nothing.

    Writes MongoDB only. The Postgres ownership record (`core.ownership_periods`)
    is a separate store and is not touched here — for a promoted building it is
    the one that SERVES owner reads, so verify it independently rather than
    assuming a missing Mongo link means the owner is missing everywhere.
    """
    names = (
        split_owner_names(owner_names)
        if isinstance(owner_names, str)
        else [name.strip() for name in (owner_names or []) if name and name.strip()]
    )
    if not names:
        return {"linked": False, "reason": "no_owner_name"}

    if isinstance(owner_emails, str) or owner_emails is None:
        emails = [owner_emails] + [None] * (len(names) - 1)
    else:
        emails = list(owner_emails) + [None] * (len(names) - len(owner_emails))

    active_links = await _collection(db_like, "user_units").find(
        {
            "building_id": building_id,
            "unit_number": unit_number,
            "role_at_unit": "owner",
            "is_active": True,
        },
        {"_id": 0, "user_id": 1},
    ).to_list(20)
    if not active_links:
        # No baseline at all — that is the initial-bootstrap case, and routing it
        # here would create every owner as non-primary. Leave it to
        # create_initial_ownership_link, which sets the primary correctly.
        return {"linked": False, "reason": "no_existing_owner_link"}

    linked_ids = [link.get("user_id") for link in active_links if link.get("user_id")]
    if len(linked_ids) != len(active_links):
        # An active owner link with no user_id at all is as unusable as an orphaned
        # one: it occupies an owner slot whose identity cannot be read, so "which
        # owners are already linked" is unknowable and adding names would duplicate.
        return {
            "linked": False,
            "reason": "active_owner_link_without_user_id",
            "unit_number": unit_number,
            "active_link_count": len(active_links),
        }

    linked_users = []
    if linked_ids:
        linked_users = await _collection(db_like, "users").find(
            {"id": {"$in": linked_ids}},
            {"_id": 0, "id": 1, "full_name": 1, "first_name": 1, "last_name": 1},
        ).to_list(len(linked_ids))
    linked_keys = set()
    resolved_ids = set()
    for user in linked_users:
        full_name = (
            user.get("full_name")
            or f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
        )
        if _name_key(full_name):
            linked_keys.add(_name_key(full_name))
            resolved_ids.add(user.get("id"))

    unresolvable_ids = [user_id for user_id in linked_ids if user_id not in resolved_ids]
    if unresolvable_ids:
        # An active owner link whose user row is missing (or has no usable name) makes
        # the "which owners are already linked" set unknowable, and guessing would add a
        # duplicate link for an owner who is in fact already attached. Refuse and report
        # instead — an orphaned link is its own data-integrity problem to fix first.
        return {
            "linked": False,
            "reason": "unresolvable_existing_owner_link",
            "unit_number": unit_number,
            "unresolvable_user_ids": unresolvable_ids,
        }

    imported_keys = {_name_key(name) for name in names if _name_key(name)}
    unmatched_linked = sorted(linked_keys - imported_keys)
    if unmatched_linked:
        # A currently-linked owner is not named in the import. That is a rename or a
        # departure, not a pure addition — and adding the "missing" names on top would
        # attach a SECOND account for a person who is already linked under a different
        # name form (e.g. linked "Kaushal Shah" vs imported "Mr Kaushal Shah"). This is
        # the exact mirror of the detector's removed-owner check; route it through the
        # drift/transfer path or fix the name form first.
        return {
            "linked": False,
            "reason": "existing_owner_not_in_imported_names",
            "unit_number": unit_number,
            "unmatched_linked_owner_names": unmatched_linked,
            "imported_owner_names": names,
        }

    missing = [
        (name, emails[index])
        for index, name in enumerate(names)
        if _name_key(name) and _name_key(name) not in linked_keys
    ]
    if not missing:
        return {"linked": False, "reason": "co_owners_already_linked", "unit_number": unit_number}

    if dry_run:
        return {
            "linked": False,
            "would_link": True,
            "unit_number": unit_number,
            "missing_owner_names": [name for name, _ in missing],
            "already_linked_owner_names": sorted(linked_keys),
        }

    now = detected_at or datetime.now(timezone.utc).isoformat()
    new_user_ids = []
    new_user_docs = []
    for name, email in missing:
        # is_primary=False unconditionally: a unit that already has an active
        # owner link already has its primary, and this function must never
        # reassign it.
        user_doc = await _link_owner_to_unit(
            db_like, building_id, unit_number, name, email, now, is_primary=False
        )
        new_user_docs.append(user_doc)
        new_user_ids.append(user_doc["id"])

    missing_names = [name for name, _ in missing]
    # The account's own address, which is the internal owner-transfer+… placeholder
    # when the import carried no email — never a bare None that reads as "unknown".
    primary_new_email = new_user_docs[0].get("email")
    has_real_email = bool(missing[0][1])
    audit_id = str(uuid.uuid4())
    await _collection(db_like, "owner_transfer_requests").insert_one(
        {
            "id": audit_id,
            "building_id": building_id,
            "unit_number": unit_number,
            "old_owners": [],
            "new_owner": {
                "user_id": new_user_ids[0],
                "full_name": _display_names(missing_names),
                "email": primary_new_email,
                "is_provisional": True,
                "is_internal_contact_email": not has_real_email,
            },
            "settlement_date": now[:10],
            "request_notes": (
                "Automated co-owner link backfill — the imported owner snapshot lists "
                f"{_display_names(missing_names)} as a joint owner of this unit, but no "
                "canonical user_units link existed. This is NOT an ownership transfer: no "
                "existing owner was removed or replaced."
            ),
            "ownership_documents": [],
            "ownership_verified": False,
            "status": "approved",
            "required_approvals": 1,
            "current_approvals": 1,
            "approval_mode": "auto_bootstrap",
            "approval_history": [
                {
                    "action": "co_owner_linked",
                    "by": f"system:{CO_OWNER_LINK_BACKFILL_SOURCE}",
                    "at": now,
                    "notes": "Auto-approved — additive co-owner link, no existing owner changed.",
                }
            ],
            "pending_approval_action": None,
            "requested_date": now,
            "submitted_by_id": f"system:{CO_OWNER_LINK_BACKFILL_SOURCE}",
            "submitted_by_name": "Co-owner Link Backfill",
            "submitted_by_role": "system",
            "reviewed_by": None,
            "reviewed_by_name": f"system:{CO_OWNER_LINK_BACKFILL_SOURCE}",
            "reviewed_date": now,
            "review_notes": "Auto-approved — additive co-owner link, no existing owner changed.",
            "action_taken": "co_owner_linked",
            "old_owners_notified": False,
            "new_owner_notified": False,
            "source": CO_OWNER_LINK_BACKFILL_SOURCE,
            "created_at": now,
            "updated_at": now,
        }
    )

    return {
        "linked": True,
        "unit_number": unit_number,
        "linked_owner_names": missing_names,
        "user_ids": new_user_ids,
        "audit_id": audit_id,
    }


OWNER_TRANSFER_WITHDRAWN_STATUS = "withdrawn"


async def withdraw_owner_transfer_request(
    db_like: Any,
    building_id: str,
    transfer_id: str,
    *,
    action: str,
    note: str,
    actor: str,
    now: str,
) -> None:
    """Retract a transfer request that should never have been raised.

    "Withdrawn" is deliberately distinct from "rejected": nobody reviewed the
    request and decided against it — it had no basis in the first place. The row
    and its full detection payload are retained (ownership records fall under the
    7-year retention rule); only the status moves, so it leaves the pending review
    queue without leaving the record.
    """
    await _collection(db_like, "owner_transfer_requests").update_one(
        {"id": transfer_id, "building_id": building_id},
        {
            "$set": {
                "status": OWNER_TRANSFER_WITHDRAWN_STATUS,
                "action_taken": action,
                "review_notes": note,
                "reviewed_by_name": actor,
                "reviewed_date": now,
                "updated_at": now,
            },
            "$push": {
                "approval_history": {
                    "action": action,
                    "by": actor,
                    "at": now,
                    "notes": note,
                }
            },
        },
    )


async def archive_stray_provisional_owner_account(
    db_like: Any,
    building_id: str,
    user_id: str,
    unit_number: str,
    *,
    now: str,
    apply: bool,
    reason: str,
    actor: str,
) -> dict | None:
    """Soft-archive the provisional account a withdrawn request minted, if unclaimed.

    A withdrawn drift request leaves behind the account it created for its phantom
    transferee. Once the real owner holds their own canonical account, that record
    is a duplicate identity for a living person. Returns None when there is nothing
    to report.

    Deliberately conservative: an account that has since been claimed (a live unit
    link or a membership), one that was never portal-detected, one that is active,
    or one already archived is left completely alone. Nothing is ever hard-deleted.
    """
    user = await _collection(db_like, "users").find_one(
        {"id": user_id},
        {
            "_id": 0,
            "id": 1,
            "full_name": 1,
            "email": 1,
            "is_active": 1,
            "status": 1,
            "portal_detected_owner": 1,
        },
    )
    if not user:
        return None
    if user.get("status") == "archived":
        return None
    if not user.get("portal_detected_owner") or user.get("is_active"):
        return {
            "unit_number": unit_number,
            "user_id": user_id,
            "archived": False,
            "reason": "not_an_unclaimed_provisional_account",
        }

    active_links = await _collection(db_like, "user_units").count_documents(
        {"user_id": user_id, "is_active": True}
    )
    memberships = await _collection(db_like, "memberships").count_documents(
        {"user_id": user_id}
    )
    if active_links or memberships:
        return {
            "unit_number": unit_number,
            "user_id": user_id,
            "archived": False,
            "reason": "account_is_in_use",
            "active_links": active_links,
            "memberships": memberships,
        }

    if apply:
        await _collection(db_like, "users").update_one(
            {"id": user_id},
            {
                "$set": {
                    "status": "archived",
                    "is_active": False,
                    "is_archived": True,
                    "archived_at": now,
                    "archived_by": actor,
                    "archived_reason": reason,
                    "updated_at": now,
                }
            },
        )

    return {
        "unit_number": unit_number,
        "user_id": user_id,
        "full_name": user.get("full_name"),
        "archived": bool(apply),
        "would_archive": not apply,
    }


OWNER_INVITE_STATUS_PENDING = "pending"
OWNER_INVITE_STATUS_SENT = "sent"


async def create_owner_bootstrap_invite(
    db_like: Any,
    building_id: str,
    user_id: str,
    unit_number: str,
    full_name: str,
    email: str,
    now: str,
) -> dict:
    """Register intent to invite a bootstrapped owner — does NOT send anything.

    This only creates a durable, reviewable ``owner_invites`` record
    (``status="pending"``). The actual email — with its own short-lived
    token, generated fresh at send time so it can't go stale sitting in
    "pending" for weeks — is sent later by a separate, explicitly-triggered
    script (``send_owner_bootstrap_invites.py``). Idempotent: a unit/user
    that already has an invite record is left untouched.
    """
    existing = await _collection(db_like, "owner_invites").find_one(
        {"building_id": building_id, "user_id": user_id}, {"_id": 0, "id": 1, "status": 1}
    )
    if existing:
        return {"created": False, "reason": "invite_already_exists", "id": existing.get("id"), "status": existing.get("status")}

    invite_id = str(uuid.uuid4())
    await _collection(db_like, "owner_invites").insert_one(
        {
            "id": invite_id,
            "building_id": building_id,
            "user_id": user_id,
            "unit_number": unit_number,
            "full_name": full_name,
            "email": email,
            "status": OWNER_INVITE_STATUS_PENDING,
            "source": INITIAL_OWNERSHIP_BOOTSTRAP_SOURCE,
            "created_at": now,
            "updated_at": now,
            "sent_at": None,
        }
    )
    return {"created": True, "id": invite_id}


async def detect_and_create_portal_owner_transfer(
    db_like: Any,
    building_id: str,
    unit_number: str,
    scraped_owner_names: list[str] | str | None,
    *,
    detected_at: str | None = None,
    source: str = PORTAL_DETECTED_TRANSFER_SOURCE,
    dry_run: bool = False,
    previous_owner_names: list[str] | str | None = None,
    use_cutover_baseline: bool = False,
) -> dict:
    """Generated function header.

    Function: detect_and_create_portal_owner_transfer
    Path: backend/services/ownership_transfer_detection_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scraped_names = (
        split_owner_names(scraped_owner_names)
        if isinstance(scraped_owner_names, str)
        else [name.strip() for name in (scraped_owner_names or []) if name and name.strip()]
    )
    if not {_name_key(name) for name in scraped_names if _name_key(name)}:
        return {"created": False, "reason": "no_scraped_owner_names"}

    override_names = (
        split_owner_names(previous_owner_names)
        if isinstance(previous_owner_names, str)
        else [name.strip() for name in (previous_owner_names or []) if name and name.strip()]
    )
    current_owners = (
        _owner_info_from_names(unit_number, override_names, source="previous-owner-override")
        if override_names
        else await _active_owner_info(
            db_like, building_id, unit_number, use_cutover_baseline=use_cutover_baseline
        )
    )
    current_by_key = {
        _name_key(owner.get("full_name")): owner
        for owner in current_owners
        if _name_key(owner.get("full_name"))
    }
    if not current_by_key:
        return {"created": False, "reason": "no_current_owner_baseline"}

    projected_names = _project_imported_owner_names(scraped_names, current_owners)
    projected_by_key = {_name_key(name): name for name in projected_names if _name_key(name)}
    projected_keys = set(projected_by_key)
    current_keys = set(current_by_key)
    if projected_keys == current_keys:
        if dry_run:
            return {
                "created": False,
                "reason": "owner_names_match",
                "unit_number": unit_number,
                "current_owner_names": [
                    owner.get("full_name") for owner in current_owners if owner.get("full_name")
                ],
                "imported_raw_owner_names": scraped_names,
                "projected_owner_names": projected_names,
            }
        return {"created": False, "reason": "owner_names_match"}

    incoming_keys = sorted(projected_keys - current_keys)
    removed_keys = sorted(current_keys - projected_keys)
    if not incoming_keys:
        if dry_run:
            return {
                "created": False,
                "reason": "no_incoming_owner_detected",
                "unit_number": unit_number,
                "current_owner_names": [
                    owner.get("full_name") for owner in current_owners if owner.get("full_name")
                ],
                "imported_raw_owner_names": scraped_names,
                "projected_owner_names": projected_names,
                "suggested_remove_owner_names": [
                    current_by_key[key].get("full_name")
                    for key in removed_keys
                    if current_by_key.get(key)
                ],
            }
        return {"created": False, "reason": "no_incoming_owner_detected"}

    if not removed_keys:
        # Every current canonical owner is STILL present in the imported snapshot —
        # the import only adds name(s). A unit legitimately held by two or more
        # people (spouses, joint investors, a trustee pair) is normal, lawful
        # ownership, not a change of ownership, so this must never become a
        # transfer request: doing so puts a review row on /admin/owner-transfers
        # asking staff to transfer the unit from one of its own joint owners to
        # the other. A transfer is only real when an existing owner actually
        # LEAVES (``removed_keys`` non-empty) or the owner name changes outright.
        # The correct remedy for a pure addition is to link the extra co-owner —
        # see ``link_missing_co_owners`` — not to raise a transfer.
        # Import order, not normalised-key order — this is read by an operator.
        added_names = [
            name for name in projected_names if _name_key(name) in set(incoming_keys)
        ]
        if dry_run:
            return {
                "created": False,
                "reason": CO_OWNER_ADDITION_REASON,
                "unit_number": unit_number,
                "current_owner_names": [
                    owner.get("full_name") for owner in current_owners if owner.get("full_name")
                ],
                "imported_raw_owner_names": scraped_names,
                "projected_owner_names": projected_names,
                "suggested_add_owner_names": added_names,
            }
        return {
            "created": False,
            "reason": CO_OWNER_ADDITION_REASON,
            "unit_number": unit_number,
            "suggested_add_owner_names": added_names,
        }

    signature = "|".join([unit_number, ",".join(sorted(current_keys)), "=>", ",".join(sorted(projected_keys))])
    existing = await _collection(db_like, "owner_transfer_requests").find_one(
        {
            "building_id": building_id,
            "unit_number": unit_number,
            "source": source,
            "portal_detected_signature": signature,
            "status": {"$in": PENDING_OWNER_TRANSFER_STATUSES},
        },
        {"_id": 0, "id": 1},
    )
    if existing:
        return {"created": False, "reason": "pending_request_exists", "id": existing.get("id")}

    now = detected_at or datetime.now(timezone.utc).isoformat()
    incoming_names = [projected_by_key[key] for key in incoming_keys]
    new_owner_name = _display_names(incoming_names)

    if dry_run:
        return {
            "created": False,
            "would_create": True,
            "unit_number": unit_number,
            "new_owner_name": new_owner_name,
            "current_owner_names": [owner.get("full_name") for owner in current_owners if owner.get("full_name")],
            "imported_raw_owner_names": scraped_names,
            "projected_owner_names": projected_names,
            "suggested_remove_owner_names": [
                current_by_key[key].get("full_name")
                for key in removed_keys
                if current_by_key.get(key)
            ],
        }

    new_owner = await _ensure_portal_detected_owner_user(db_like, building_id, unit_number, new_owner_name, now)

    suggested_remove_owner_ids = [
        current_by_key[key].get("user_id")
        for key in removed_keys
        if current_by_key.get(key)
        and current_by_key[key].get("user_id")
        and not current_by_key[key].get("is_legacy_snapshot")
    ]

    transfer_id = str(uuid.uuid4())
    transfer_request = {
        "id": transfer_id,
        "building_id": building_id,
        "unit_number": unit_number,
        "old_owners": [
            {
                "user_id": owner.get("user_id"),
                "full_name": owner.get("full_name"),
                "email": owner.get("email"),
                "is_legacy_snapshot": owner.get("is_legacy_snapshot", False),
            }
            for owner in current_owners
        ],
        "new_owner": {
            "user_id": new_owner.get("id"),
            "full_name": new_owner_name,
            "email": new_owner.get("email"),
            "is_provisional": True,
            "is_portal_detected": True,
            "is_internal_contact_email": True,
        },
        "settlement_date": now[:10],
        "request_notes": (
            f"Automatically created from {_source_label(source)} owner-name drift. "
            f"Current canonical owners: {_display_names([owner.get('full_name') for owner in current_owners])}. "
            f"Projected imported owner names: {_display_names(projected_names)}."
        ),
        "ownership_documents": [],
        "ownership_verified": False,
        "status": "pending",
        "required_approvals": 1,
        "current_approvals": 0,
        "approval_mode": None,
        "approval_history": [],
        "pending_approval_action": None,
        "requested_date": now,
        "submitted_by_id": f"system:{source}",
        "submitted_by_name": _source_label(source),
        "submitted_by_role": "system",
        "reviewed_by": None,
        "reviewed_by_name": None,
        "reviewed_date": None,
        "review_notes": None,
        "action_taken": None,
        "old_owners_notified": False,
        "new_owner_notified": False,
        "source": source,
        "portal_detected_signature": signature,
        "portal_detected_owner_names": projected_names,
        "portal_detected_raw_owner_names": scraped_names,
        "portal_previous_owner_names": [owner.get("full_name") for owner in current_owners if owner.get("full_name")],
        "suggested_remove_owner_ids": suggested_remove_owner_ids,
        "created_at": now,
        "updated_at": now,
    }

    await _collection(db_like, "owner_transfer_requests").insert_one(transfer_request)
    notified = await _notify_reviewers(db_like, building_id, unit_number, new_owner_name, now, source)
    return {"created": True, "id": transfer_id, "notified": notified}
