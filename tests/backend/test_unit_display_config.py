# @featuretrace:finance-owner-dashboard — tests for per-building unit display prefix rules.
# Layer: test
# Data flow: settings endpoints/service → db.settings {type: unit_display} → unit_number candidates (building-scoped).
# Related: backend/routers/settings.py
#           backend/services/settings_service.py
#           backend/utils/unit_number.py
"""Unit display rules: numeric lots + per-building display prefix configuration."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
from pydantic import ValidationError

from models.settings import UnitDisplayRule, UnitDisplayRulesUpdate
from utils.unit_number import format_unit_display, unit_number_candidates

EAST_GATE_RULES = [
    {"prefix": "UA", "min": 1, "max": 70, "pad": 3},
    {"prefix": "TH", "min": 71, "max": 87, "pad": 3},
]
DEMO_RULES = [{"prefix": "A", "min": 1, "max": 14, "pad": 1}]


# ── format_unit_display ──────────────────────────────────────────────────────

def test_east_gate_lots_format_with_configured_prefixes():
    assert format_unit_display(1, EAST_GATE_RULES) == "UA001"
    assert format_unit_display(70, EAST_GATE_RULES) == "UA070"
    assert format_unit_display(71, EAST_GATE_RULES) == "TH071"
    assert format_unit_display(87, EAST_GATE_RULES) == "TH087"


def test_demo_building_uses_single_letter_unpadded_prefix():
    assert format_unit_display(1, DEMO_RULES) == "A1"
    assert format_unit_display(14, DEMO_RULES) == "A14"


def test_lot_outside_all_rules_falls_back_to_bare_number():
    assert format_unit_display(99, EAST_GATE_RULES) == "99"
    assert format_unit_display(5, None) == "5"


# ── rules-driven candidate expansion ─────────────────────────────────────────

def test_demo_display_candidate_generated_from_rules():
    # Without rules "1" never expands to "A1"; with the demo building's rules it must.
    assert "A1" not in unit_number_candidates("1")
    assert "A1" in unit_number_candidates("1", DEMO_RULES)


def test_rules_do_not_break_generic_candidates():
    candidates = unit_number_candidates("87", EAST_GATE_RULES)
    assert "TH087" in candidates
    assert "87" in candidates
    assert "U87" in candidates


# ── Pydantic validation ──────────────────────────────────────────────────────

def test_rule_rejects_numeric_prefix_and_bad_bounds():
    with pytest.raises(ValidationError):
        UnitDisplayRule(prefix="T2", min=1, max=10, pad=3)
    with pytest.raises(ValidationError):
        UnitDisplayRule(prefix="TH", min=0, max=10, pad=3)
    with pytest.raises(ValidationError):
        UnitDisplayRule(prefix="TH", min=1, max=10, pad=9)


def test_rules_update_rejects_overlapping_ranges():
    with pytest.raises(ValidationError):
        UnitDisplayRulesUpdate(rules=[
            UnitDisplayRule(prefix="UA", min=1, max=70, pad=3),
            UnitDisplayRule(prefix="TH", min=70, max=87, pad=3),
        ])


def test_rules_update_accepts_east_gate_shape():
    update = UnitDisplayRulesUpdate(rules=[
        UnitDisplayRule(prefix="UA", min=1, max=70, pad=3),
        UnitDisplayRule(prefix="TH", min=71, max=87, pad=3),
    ])
    assert len(update.rules) == 2


# ── service round-trip (mocked Mongo) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_service_get_returns_empty_for_unconfigured_building():
    from services.settings_service import get_unit_display_rules
    mock_db = MagicMock()
    mock_db.settings.find_one = AsyncMock(return_value=None)
    assert await get_unit_display_rules("16244", settings_db=mock_db) == []


@pytest.mark.asyncio
async def test_service_upsert_is_building_scoped():
    from services.settings_service import upsert_unit_display_rules
    mock_db = MagicMock()
    mock_db.settings.update_one = AsyncMock()
    mock_db.settings.find_one = AsyncMock(return_value={"rules": EAST_GATE_RULES})
    result = await upsert_unit_display_rules("13195", EAST_GATE_RULES, settings_db=mock_db)
    assert result == EAST_GATE_RULES
    filter_arg = mock_db.settings.update_one.call_args.args[0]
    assert filter_arg == {"building_id": "13195", "type": "unit_display"}
    # Cross-building isolation: another building's read must not see these rules.
    mock_db.settings.find_one = AsyncMock(return_value=None)
    from services.settings_service import get_unit_display_rules
    assert await get_unit_display_rules("16244", settings_db=mock_db) == []


# ── endpoint guard ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_put_unit_display_requires_manage_users_permission():
    from routers.settings import update_unit_display_config
    payload = UnitDisplayRulesUpdate(rules=[UnitDisplayRule(prefix="TH", min=71, max=87, pad=3)])
    owner = {"id": "u1", "role": "owner", "is_approved": True, "building_id": "13195"}
    with pytest.raises(HTTPException) as exc:
        await update_unit_display_config(payload=payload, current_user=owner, building_id="13195")
    assert exc.value.status_code == 403
