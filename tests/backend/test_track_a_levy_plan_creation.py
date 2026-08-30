"""Unit tests for the Track A (clean-state) onboarding levy-plan creation
(GAP-ONBOARD-002 Item 1).

``services.financial_service.create_levy_plans_from_onboarding`` consumes the
``total_admin_levy`` / ``total_cw_levy`` totals captured by the onboarding
wizard's "Current Levy Schedule" step and creates the matching admin +
capital-works (sinking) ``levy_plans`` rows so a freshly onboarded building has
a levy plan to bill against.

These tests patch the two collaborators the function calls —
``create_or_update_levy_plan`` (the levy_plans writer) and
``get_current_levy_year`` (the default financial-year resolver) — so the mapping,
skip, parsing, and multi-tenant threading logic is exercised with no database.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, call, patch

import pytest

from services.financial_service import (
    _parse_levy_amount,
    create_levy_plans_from_onboarding,
)

# Sierra demo — never "13195" (real East Gate production data) in tests.
BUILDING_ID = "16244"
OTHER_BUILDING_ID = "17001"


def _patched_writer(return_value=None):
    """Patch the levy_plans writer with an AsyncMock echoing a stored doc."""
    async def _echo(*, year, fund_type, building_id, total_income_required):
        return {
            "id": f"plan-{fund_type}",
            "financial_year": year,
            "fund_type": fund_type,
            "building_id": building_id,
            "total_income_required": total_income_required,
        }

    mock = AsyncMock(side_effect=return_value or _echo)
    return patch("services.financial_service.create_or_update_levy_plan", new=mock), mock


def _patched_year(year="2026"):
    return patch(
        "services.financial_service.get_current_levy_year",
        new=AsyncMock(return_value=year),
    )


# ─────────────────────────────────────────────────────────────────────────────
# _parse_levy_amount
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1234.56", 1234.56),   # string from the HTML number input
        ("0", 0.0),
        (1500, 1500.0),          # already numeric
        (2500.25, 2500.25),
        ("  980.00  ", 980.0),   # surrounding whitespace
    ],
)
def test_parse_levy_amount_valid(raw, expected):
    assert _parse_levy_amount(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", "abc", "12.3.4", "-50", -0.01],
)
def test_parse_levy_amount_invalid_returns_none(raw):
    assert _parse_levy_amount(raw) is None


# ─────────────────────────────────────────────────────────────────────────────
# create_levy_plans_from_onboarding — mapping
# ─────────────────────────────────────────────────────────────────────────────

async def test_both_totals_create_admin_and_sinking_plans():
    step_data = {"total_admin_levy": "40000.00", "total_cw_levy": "15000.50"}
    writer_patch, writer = _patched_writer()
    with writer_patch, _patched_year("2026"):
        result = await create_levy_plans_from_onboarding(BUILDING_ID, step_data)

    assert result["financial_year"] == "2026"
    assert [p["fund_type"] for p in result["created"]] == ["admin", "sinking"]
    assert result["skipped"] == []

    # Correct fund mapping + dollar amounts threaded through to the writer.
    writer.assert_has_calls([
        call(year="2026", fund_type="admin", building_id=BUILDING_ID,
             total_income_required=40000.00),
        call(year="2026", fund_type="sinking", building_id=BUILDING_ID,
             total_income_required=15000.50),
    ])
    assert writer.await_count == 2


async def test_cw_maps_to_sinking_fund():
    """Capital works ("cw") must map to the sinking fund, not a "cw" fund_type."""
    step_data = {"total_admin_levy": "1000", "total_cw_levy": "2000"}
    writer_patch, writer = _patched_writer()
    with writer_patch, _patched_year():
        await create_levy_plans_from_onboarding(BUILDING_ID, step_data)

    fund_types = {c.kwargs["fund_type"] for c in writer.await_args_list}
    assert fund_types == {"admin", "sinking"}
    assert "cw" not in fund_types


# ─────────────────────────────────────────────────────────────────────────────
# create_levy_plans_from_onboarding — skip logic
# ─────────────────────────────────────────────────────────────────────────────

async def test_missing_cw_only_creates_admin():
    """Capital works is optional in the wizard — absent -> sinking is skipped."""
    step_data = {"total_admin_levy": "40000"}
    writer_patch, writer = _patched_writer()
    with writer_patch, _patched_year():
        result = await create_levy_plans_from_onboarding(BUILDING_ID, step_data)

    assert [p["fund_type"] for p in result["created"]] == ["admin"]
    assert [s["fund_type"] for s in result["skipped"]] == ["sinking"]
    assert writer.await_count == 1
    assert writer.await_args.kwargs["fund_type"] == "admin"


async def test_zero_and_blank_totals_are_skipped():
    step_data = {"total_admin_levy": "0", "total_cw_levy": ""}
    writer_patch, writer = _patched_writer()
    with writer_patch, _patched_year():
        result = await create_levy_plans_from_onboarding(BUILDING_ID, step_data)

    assert result["created"] == []
    assert [s["fund_type"] for s in result["skipped"]] == ["admin", "sinking"]
    writer.assert_not_awaited()


async def test_invalid_amount_is_skipped_not_written():
    step_data = {"total_admin_levy": "not-a-number", "total_cw_levy": "-100"}
    writer_patch, writer = _patched_writer()
    with writer_patch, _patched_year():
        result = await create_levy_plans_from_onboarding(BUILDING_ID, step_data)

    assert result["created"] == []
    writer.assert_not_awaited()


async def test_empty_step_data_creates_nothing():
    writer_patch, writer = _patched_writer()
    with writer_patch, _patched_year():
        result = await create_levy_plans_from_onboarding(BUILDING_ID, {})

    assert result["created"] == []
    writer.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────
# financial_year handling
# ─────────────────────────────────────────────────────────────────────────────

async def test_financial_year_override_is_respected_and_default_not_called():
    step_data = {"total_admin_levy": "1000"}
    writer_patch, writer = _patched_writer()
    year_mock = AsyncMock(return_value="2099")
    with writer_patch, patch("services.financial_service.get_current_levy_year", new=year_mock):
        result = await create_levy_plans_from_onboarding(
            BUILDING_ID, step_data, financial_year="2031"
        )

    assert result["financial_year"] == "2031"
    assert writer.await_args.kwargs["year"] == "2031"
    year_mock.assert_not_awaited()  # explicit year short-circuits the resolver


async def test_default_year_uses_get_current_levy_year():
    step_data = {"total_admin_levy": "1000"}
    writer_patch, writer = _patched_writer()
    year_mock = AsyncMock(return_value="2026")
    with writer_patch, patch("services.financial_service.get_current_levy_year", new=year_mock):
        result = await create_levy_plans_from_onboarding(BUILDING_ID, step_data)

    assert result["financial_year"] == "2026"
    year_mock.assert_awaited_once_with(BUILDING_ID)


# ─────────────────────────────────────────────────────────────────────────────
# multi-tenant + idempotency
# ─────────────────────────────────────────────────────────────────────────────

async def test_building_id_is_threaded_per_tenant():
    step_data = {"total_admin_levy": "1000", "total_cw_levy": "500"}
    writer_patch, writer = _patched_writer()
    with writer_patch, _patched_year():
        await create_levy_plans_from_onboarding(OTHER_BUILDING_ID, step_data)

    assert {c.kwargs["building_id"] for c in writer.await_args_list} == {OTHER_BUILDING_ID}
    # Never leaks the East Gate production building id.
    assert all(c.kwargs["building_id"] != "13195" for c in writer.await_args_list)


async def test_repeated_calls_issue_identical_writes():
    """The function is deterministic; idempotency is guaranteed by the writer's
    upsert on (financial_year, fund_type, building_id). Two identical calls must
    issue byte-for-byte identical writes (no duplicate/divergent rows)."""
    step_data = {"total_admin_levy": "40000", "total_cw_levy": "15000"}
    writer_patch, writer = _patched_writer()
    with writer_patch, _patched_year():
        await create_levy_plans_from_onboarding(BUILDING_ID, step_data)
        first_round = list(writer.await_args_list)
        await create_levy_plans_from_onboarding(BUILDING_ID, step_data)
        second_round = writer.await_args_list[len(first_round):]

    assert first_round == second_round
