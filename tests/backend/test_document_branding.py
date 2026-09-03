# @featuretrace:document-branding
# Layer: test
"""Pure tests for the shared document-branding and letterhead contract."""

import pytest
from pydantic import ValidationError

from models.settings import SiteSettingsUpdate
from services.document_branding_service import (
    DEFAULT_ACCENT_COLOR,
    local_brand_asset_path,
    normalise_accent_color,
    resolve_document_branding,
)
from utils.letter_generator import render_letter_html


def _settings(**overrides):
    values = {
        "building_id": "test-building",
        "building_name": "Sierra",
        "building_address": "70 Example Street, Canberra ACT 2600",
        "plan_number": "16244",
        "building_abn": "88 640 163 643",
        "building_logo_url": "/uploads/branding/test-building/building.png",
        "strata_management_company": "Vantage Strata Pty Ltd",
        "strata_management_logo_url": "/uploads/branding/test-building/agency.png",
        "strata_management_abn": "79 602 359 482",
        "strata_management_licence": "18401928",
        "strata_manager_phone": "02 6171 9700",
        "strata_manager_email": "info@example.com",
        "strata_manager_address": "23 Example Street, Dickson ACT 2602",
        "document_branding_mode": "dual",
        "document_accent_color": "#B8823D",
    }
    values.update(overrides)
    return values


def test_document_profile_keeps_building_and_agency_identity_separate():
    profile = resolve_document_branding(_settings(), "test-building")
    assert profile["building_name"] == "Sierra"
    assert profile["building_logo_url"].endswith("building.png")
    assert profile["strata_management_company"] == "Vantage Strata Pty Ltd"
    assert profile["strata_management_logo_url"].endswith("agency.png")
    assert profile["building_abn"] == "88 640 163 643"
    assert profile["strata_management_abn"] == "79 602 359 482"


def test_document_profile_rejects_unsafe_assets_and_bad_colours():
    profile = resolve_document_branding(
        _settings(
            building_logo_url="file:///etc/passwd",
            strata_management_logo_url="javascript:alert(1)",
            document_accent_color="not-a-colour",
        ),
        "test-building",
    )
    assert profile["building_logo_url"] is None
    assert profile["strata_management_logo_url"] is None
    assert profile["document_accent_color"] == DEFAULT_ACCENT_COLOR
    assert normalise_accent_color("#123abc") == "#123ABC"


def test_local_brand_asset_is_confined_to_upload_root(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_STORAGE_PATH", str(tmp_path))
    logo = tmp_path / "branding" / "test-building" / "building.png"
    logo.parent.mkdir(parents=True)
    logo.write_bytes(b"not-rendered-in-this-unit-test")
    assert local_brand_asset_path("/uploads/branding/test-building/building.png") == str(logo)
    assert local_brand_asset_path("/uploads/../outside.png") is None


def test_settings_model_persists_document_branding_fields():
    payload = SiteSettingsUpdate(
        plan_number="16244",
        building_abn="88 640 163 643",
        building_logo_url="/uploads/branding/test-building/building.png",
        strata_management_logo_url="/uploads/branding/test-building/agency.png",
        strata_management_abn="79 602 359 482",
        strata_management_licence="18401928",
        document_branding_mode="dual",
        document_accent_color="#b8823d",
        document_show_page_numbers=True,
        agm_recording_disclosure="Meeting may be recorded.",
    )
    dumped = payload.model_dump(exclude_unset=True)
    assert dumped["document_accent_color"] == "#B8823D"
    assert dumped["document_branding_mode"] == "dual"
    assert dumped["strata_management_logo_url"].endswith("agency.png")


@pytest.mark.parametrize("mode", ["unknown", "both", ""])
def test_settings_model_rejects_unknown_branding_modes(mode):
    with pytest.raises(ValidationError):
        SiteSettingsUpdate(document_branding_mode=mode)


def test_agm_template_includes_formal_notice_fields_and_escapes_input():
    building = _settings(
        agm_recording_disclosure="The meeting may be recorded to prepare minutes.",
        agm_insurance_disclosure="Insurance remuneration is disclosed in the meeting papers.",
    )
    html = render_letter_html(
        "agm_invitation",
        {
            "owner_name": "Owner <script>alert(1)</script>",
            "agm_date": "21/07/2026",
            "agm_time": "5:30 PM",
            "agm_location": "Microsoft Teams",
            "meeting_link": "https://teams.example/join",
            "meeting_id": "448 250",
            "meeting_passcode": "xQ34",
            "agenda_items": "Adoption of minutes\nAdministrative Fund budget\nSinking Fund contribution",
        },
        building,
    )
    assert "NOTICE OF ANNUAL GENERAL MEETING" in html
    assert "Units Plan NO. 16244" in html or "UNITS PLAN NO. 16244" in html
    assert "Meeting ID" in html
    assert "Recording and transcription" in html
    assert "Insurance disclosure" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
