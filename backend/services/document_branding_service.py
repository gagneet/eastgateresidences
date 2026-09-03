# @featuretrace:document-branding
# Layer: service
# Data flow: building settings -> resolve_document_branding() -> notices, letters, meetings, financial exports
# Scope: building-scoped
"""Canonical document-branding profile.

Documents may carry two distinct identities:
* the owners corporation / building; and
* the appointed strata management agency.

Keeping those values separate prevents a building logo from being presented as the
issuer's logo (or vice versa) and gives every PDF generator one source of truth.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping

DEFAULT_ACCENT_COLOR = "#B8823D"
DEFAULT_COMPANY_NAME = "StrataOS"
VALID_BRANDING_MODES = {"dual", "agency", "building"}
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _first_nonempty(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def normalise_accent_color(value: Any) -> str:
    """Return a safe six-digit CSS/ReportLab colour."""
    text = str(value or "").strip()
    return text.upper() if _HEX_COLOR_RE.fullmatch(text) else DEFAULT_ACCENT_COLOR


def normalise_branding_mode(value: Any) -> str:
    mode = str(value or "dual").strip().lower()
    return mode if mode in VALID_BRANDING_MODES else "dual"


def _asset_url(value: Any) -> str | None:
    """Allow only application-relative or HTTP(S) image references."""
    url = _first_nonempty(value)
    if not url:
        return None
    if url.startswith(("/uploads/", "/images/", "https://", "http://")):
        return url
    return None


def resolve_document_branding(
    settings: Mapping[str, Any] | None,
    building_id: str = "",
) -> dict[str, Any]:
    """Resolve the complete, building-scoped document identity."""
    s = dict(settings or {})
    building_name = _first_nonempty(s.get("building_name"), s.get("name")) or "Owners Corporation"
    company_name = _first_nonempty(
        s.get("strata_management_company"),
        s.get("strata_manager_name"),
        s.get("manager_name"),
    ) or DEFAULT_COMPANY_NAME

    return {
        "building_id": building_id or str(s.get("building_id") or ""),
        "building_name": building_name,
        "building_address": _first_nonempty(s.get("strata_address"), s.get("building_address")) or "",
        "plan_number": _first_nonempty(s.get("plan_number"), s.get("strata_plan_number")) or "",
        "building_abn": _first_nonempty(s.get("building_abn")) or "",
        "building_logo_url": _asset_url(s.get("building_logo_url") or s.get("logo_url")),
        "strata_management_company": company_name,
        "strata_management_logo_url": _asset_url(
            s.get("strata_management_logo_url") or s.get("strata_manager_logo_url")
        ),
        "strata_management_abn": _first_nonempty(s.get("strata_management_abn")) or "",
        "strata_management_licence": _first_nonempty(s.get("strata_management_licence")) or "",
        "strata_management_website": _first_nonempty(s.get("strata_management_website")) or "",
        "strata_manager_phone": _first_nonempty(s.get("strata_manager_phone"), s.get("manager_phone")) or "",
        "strata_manager_email": _first_nonempty(s.get("strata_manager_email"), s.get("manager_email")) or "",
        "strata_manager_address": _first_nonempty(s.get("strata_manager_address"), s.get("manager_address")) or "",
        "document_branding_mode": normalise_branding_mode(s.get("document_branding_mode")),
        "document_accent_color": normalise_accent_color(s.get("document_accent_color")),
        "document_footer_text": _first_nonempty(s.get("document_footer_text")) or "",
        "document_show_page_numbers": bool(s.get("document_show_page_numbers", True)),
        "agm_recording_disclosure": _first_nonempty(s.get("agm_recording_disclosure")) or "",
        "agm_insurance_disclosure": _first_nonempty(s.get("agm_insurance_disclosure")) or "",
    }


def local_brand_asset_path(url: Any) -> str | None:
    """Resolve a configured local logo without allowing arbitrary filesystem access.

    Remote logos are intentionally not downloaded during report generation. Uploading
    through the settings endpoint creates an /uploads/branding URL which resolves here.
    Existing /images assets under frontend/public remain supported.
    """
    value = _asset_url(url)
    if not value or value.startswith(("http://", "https://")):
        return None

    candidates: list[tuple[Path, Path]] = []
    if value.startswith("/uploads/"):
        upload_root = Path(os.getenv("FILE_STORAGE_PATH", "/uploads")).resolve()
        relative = value.removeprefix("/uploads/")
        candidates.append((upload_root, (upload_root / relative).resolve()))
    elif value.startswith("/images/"):
        relative = value.lstrip("/")
        for public_root in (
            Path.cwd() / "frontend" / "public",
            Path.cwd().parent / "frontend" / "public",
        ):
            root = public_root.resolve()
            candidates.append((root, (root / relative).resolve()))

    for root, candidate in candidates:
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            return str(candidate)
    return None


def agency_contact_lines(profile: Mapping[str, Any]) -> list[str]:
    """Return compact, non-empty agency identity lines for headers/footers."""
    lines = []
    if profile.get("strata_management_abn"):
        lines.append(f"ABN: {profile['strata_management_abn']}")
    if profile.get("strata_management_licence"):
        lines.append(f"Licence: {profile['strata_management_licence']}")
    if profile.get("strata_manager_address"):
        lines.append(str(profile["strata_manager_address"]))
    if profile.get("strata_manager_phone"):
        lines.append(f"Ph: {profile['strata_manager_phone']}")
    if profile.get("strata_manager_email"):
        lines.append(str(profile["strata_manager_email"]))
    if profile.get("strata_management_website"):
        lines.append(str(profile["strata_management_website"]))
    return lines


__all__ = [
    "DEFAULT_ACCENT_COLOR",
    "VALID_BRANDING_MODES",
    "agency_contact_lines",
    "local_brand_asset_path",
    "normalise_accent_color",
    "normalise_branding_mode",
    "resolve_document_branding",
]
