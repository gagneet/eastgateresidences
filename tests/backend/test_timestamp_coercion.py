#!/usr/bin/env python3
"""
Response models must accept Mongo's BSON datetimes, not 500 on them.

WHY THIS EXISTS
Mongo stores dates natively, so whether `created_at` comes back a `str` or a
`datetime` depends on which writer produced the document. This tree has both
conventions — most writers call `.isoformat()`, `seeds/demo_customer.py` did not.

Response models declare `created_at: str` in 139 places. Served a document written the
other way, pydantic raises ResponseValidationError and FastAPI returns **500** — a
server fault with nothing in the response pointing at the cause.

Two real occurrences:
  - GET /levy-categories 500'd on onboarding-import rows. Fixed once, locally, with a
    field_validator on LevyCategoryResponse (see the comment in models/finance.py).
  - GET /annual-levies and GET /workflow-requests 500'd on EVERY request for the demo
    building — the sales demo — found by the owner-dashboard k6 benchmark 2026-08-26.
    Ten of that building's collections carry BSON datetimes.

models/timestamps.py generalises the local fix. These tests pin the behaviour so the
third occurrence does not need finding by benchmark.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_timestamp_coercion.py -q
"""

import datetime

import pytest

from models.community_os import WorkflowRequestResponse
from models.finance import AnnualLevyResponse
from models.timestamps import _to_iso_string


class TestCoercion:
    def test_naive_datetime_is_labelled_utc(self):
        """Mongo returns naive datetimes for values it stored as UTC.

        Emitting them without a zone makes the client guess, and the rest of the API
        emits `Z` — a response should not be internally inconsistent about it.
        """
        assert _to_iso_string(datetime.datetime(2026, 8, 20, 22, 16, 45)) == "2026-08-20T22:16:45Z"

    def test_aware_datetime_uses_z_not_offset(self):
        aware = datetime.datetime(2026, 8, 20, 22, 16, 45, tzinfo=datetime.timezone.utc)
        assert _to_iso_string(aware) == "2026-08-20T22:16:45Z"

    def test_date_becomes_a_plain_iso_date(self):
        assert _to_iso_string(datetime.date(2026, 8, 20)) == "2026-08-20"

    def test_strings_pass_through_untouched(self):
        """Already-correct documents must not be rewritten."""
        assert _to_iso_string("2026-08-20T22:16:45Z") == "2026-08-20T22:16:45Z"

    def test_unrelated_types_pass_through_for_pydantic_to_reject(self):
        """This normalises a representational split; it does not silence validation.

        An int in a timestamp field is a real error and must still surface as one.
        """
        assert _to_iso_string(12345) == 12345
        with pytest.raises(Exception):
            WorkflowRequestResponse(
                id="x", building_id="b", request_type="t", status="open",
                created_at=12345, updated_at="2026-01-01T00:00:00Z",
            )


class TestAffectedResponseModels:
    """The two models whose endpoints were returning 500 for the demo building."""

    def test_workflow_request_accepts_bson_datetime(self):
        m = WorkflowRequestResponse(
            id="x", building_id="UPDEMO5", request_type="maintenance", status="open",
            created_at=datetime.datetime(2026, 8, 20, 22, 16, 46),
            updated_at=datetime.datetime(2026, 8, 20, 22, 16, 46),
        )
        assert m.created_at == "2026-08-20T22:16:46Z"
        assert m.updated_at == "2026-08-20T22:16:46Z"

    def test_annual_levy_accepts_bson_datetime(self):
        m = AnnualLevyResponse(
            id="x", year="2026", status="proposed", building_id="UPDEMO5",
            total_uoe=100, admin_fund={}, sinking_fund={}, payment_schedule=[],
            admin_levy_per_uoe_annual=1.0, admin_levy_per_uoe_quarterly=0.25,
            sinking_levy_per_uoe_annual=1.0, sinking_levy_per_uoe_quarterly=0.25,
            created_at=datetime.datetime(2026, 8, 20, 22, 16, 45),
            updated_at=datetime.datetime(2026, 8, 20, 22, 16, 45),
        )
        assert m.created_at == "2026-08-20T22:16:45Z"


class TestSeedProducesTheSameShapeAsEveryOtherWriter:
    """Coercing on read is the safety net; the seed should not need it.

    Sierra's annual_levies rows carry an `id` and ISO strings. The demo seed carried
    neither, which is what made it the only building whose /annual-levies 500'd.
    """

    def test_demo_seed_writes_iso_strings_and_an_id(self):
        import inspect
        from seeds import demo_customer

        src = inspect.getsource(demo_customer)
        block = src[src.index("# MongoDB: annual_levies"):src.index("MongoDB: annual_levies (2025-2026)")]
        assert '"created_at": _now_iso' in block, "seed regressed to writing a datetime object"
        assert '"updated_at": _now_iso' in block
        assert '"id": _u5(' in block, "AnnualLevyResponse.id is required; the seed must supply it"


class TestMixedTypeSorting:
    """The OTHER half of the same split — and the coercion above does not reach it.

    `activities.sort(key=lambda x: x.get("created_at", ""))` raised
    `TypeError: '<' not supported between instances of 'datetime.datetime' and 'str'`,
    a 500 on GET /analytics/activities for every request. That list is assembled from
    several collections plus synthesised entries, so it holds datetimes from one
    writer, ISO strings from another, and "" for rows missing the field.

    Four call sites had the same shape: analytics, engagement, a server.py max(), and
    a data-repair script.
    """

    def test_sorts_datetimes_and_strings_together(self):
        from models.timestamps import timestamp_sort_key
        rows = [
            {"created_at": datetime.datetime(2026, 8, 20, 22, 16, 45)},   # BSON
            {"created_at": "2026-08-21T09:00:00Z"},                        # ISO Z
            {"created_at": "2026-08-19T00:00:00+00:00"},                   # ISO offset
        ]
        rows.sort(key=lambda r: timestamp_sort_key(r.get("created_at")), reverse=True)
        assert [r["created_at"] for r in rows] == [
            "2026-08-21T09:00:00Z",
            datetime.datetime(2026, 8, 20, 22, 16, 45),
            "2026-08-19T00:00:00+00:00",
        ]

    def test_z_and_offset_spellings_of_one_instant_are_equal(self):
        """A plain str() key would order these by ASCII, where 'Z' > '+'."""
        from models.timestamps import timestamp_sort_key
        assert timestamp_sort_key("2026-08-20T22:16:45Z") == timestamp_sort_key("2026-08-20T22:16:45+00:00")

    def test_missing_and_unparseable_sort_last_in_a_newest_first_feed(self):
        """An absent timestamp must not push a row to the top of the feed.

        Every caller uses reverse=True, so the floor value lands at the end.
        """
        from models.timestamps import timestamp_sort_key
        rows = [{"created_at": ""}, {"created_at": "2026-08-21T09:00:00Z"},
                {}, {"created_at": "not a date"}]
        rows.sort(key=lambda r: timestamp_sort_key(r.get("created_at")), reverse=True)
        assert rows[0]["created_at"] == "2026-08-21T09:00:00Z"

    def test_naive_datetimes_are_treated_as_utc(self):
        """Mongo stores naive datetimes as UTC; comparing naive against aware raises."""
        from models.timestamps import timestamp_sort_key
        naive = timestamp_sort_key(datetime.datetime(2026, 8, 20, 22, 16, 45))
        aware = timestamp_sort_key("2026-08-20T22:16:45Z")
        assert naive == aware

    def test_every_known_call_site_uses_the_helper(self):
        """These four all compared raw values and would raise on mixed input."""
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[2]
        for rel in ("backend/routers/analytics.py",
                    "backend/routers/engagement.py",
                    "backend/server.py",
                    "backend/scripts/data_repair/repair_orphaned_owner_links_20260820.py"):
            assert "timestamp_sort_key" in (root / rel).read_text(), rel
