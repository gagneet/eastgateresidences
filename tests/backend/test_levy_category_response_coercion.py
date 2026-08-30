"""Regression test for the spending-categories blank-page bug (issue 5 of the arrears/finance
batch): levy_categories rows written by the onboarding import store created_at/updated_at as
datetime OBJECTS, which the strict `created_at: str` model rejected — 500'ing /levy-categories and
(via the page's Promise.allSettled) blanking the spending-categories page. LevyCategoryResponse now
coerces datetime→ISO and tolerates missing timestamps.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from models.finance import LevyCategoryResponse


def _doc(**over):
    d = dict(
        id="c1", year="2026", fund_type="administrative", name="Cleaning",
        budgeted_amount=100.0, actual_amount=90.0, description="x",
        building_id="16244", status="active",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    d.update(over)
    return d


def test_datetime_timestamps_are_coerced_to_iso_strings():
    m = LevyCategoryResponse(**_doc())
    assert isinstance(m.created_at, str) and m.created_at.startswith("2026-01-01")
    assert isinstance(m.updated_at, str) and m.updated_at.startswith("2026-01-02")


def test_missing_timestamps_default_to_none_not_error():
    d = _doc()
    d.pop("created_at")
    d.pop("updated_at")
    m = LevyCategoryResponse(**d)  # must not raise
    assert m.created_at is None
    assert m.updated_at is None


def test_iso_string_timestamps_pass_through_unchanged():
    m = LevyCategoryResponse(
        **_doc(created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-02T00:00:00+00:00")
    )
    assert m.created_at == "2026-01-01T00:00:00+00:00"
    assert m.updated_at == "2026-01-02T00:00:00+00:00"


def test_still_rejects_a_genuinely_missing_required_field():
    d = _doc()
    d.pop("name")
    with pytest.raises(ValidationError):
        LevyCategoryResponse(**d)
