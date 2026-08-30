# @featuretrace:legacy-cleanup — Tests for the governance genesis bootstrap service.
# Layer: test
# Data flow: fake Mongo + fake Postgres session -> GovernanceBootstrapService -> report.
# Related: backend/services/governance_bootstrap_service.py
#          backend/scripts/bootstrap_postgres_governance_genesis.py
"""TDD suite for backend/services/governance_bootstrap_service.py.

Never exercises the real DATABASE_URL/MONGO_URL — everything runs against
in-memory fakes. See tests/backend/test_identity_bootstrap_service.py's
top-of-file comment for why that matters: a service with
pg_session_factory=None falls back to the real Postgres connection, and
this repo has no test-DB override anywhere.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from services.governance_bootstrap_service import (  # noqa: E402
    BootstrapMode,
    GovernanceBootstrapService,
    ValidationError,
)

SCHEME_ID = "11111111-1111-1111-1111-111111111111"
TENANT_ID = "22222222-2222-2222-2222-222222222222"
LOT_87_ID = "33333333-3333-3333-3333-333333333333"
LOT_63_ID = "44444444-4444-4444-4444-444444444444"
PARTY_GAGNEET_ID = "55555555-5555-5555-5555-555555555555"
PARTY_ANTHONY_ID = "66666666-6666-6666-6666-666666666666"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, *, all_rows=None, fetchone_row=None, scalar_value=None):
        self._all_rows = all_rows or []
        self._fetchone_row = fetchone_row
        self._scalar_value = scalar_value

    def all(self):
        return self._all_rows

    def fetchone(self):
        return self._fetchone_row

    def scalar(self):
        return self._scalar_value


class _FakeSession:
    """Answers the fixed set of queries governance_bootstrap_service.py issues.

    Tracks INSERTed primary keys per table so a second run against the same
    fake reports updated=True / created=False, exercising idempotency
    without a real database.
    """

    def __init__(self, *, scheme_row=(SCHEME_ID, TENANT_ID), lots=None, parties=None):
        self.scheme_row = scheme_row
        self.lots = lots if lots is not None else [(LOT_87_ID, "87"), (LOT_63_ID, "63")]
        self.parties = parties if parties is not None else [
            ("gagneet@eastgateresidences.com.au", PARTY_GAGNEET_ID),
            ("anthony@eastgateresidences.com.au", PARTY_ANTHONY_ID),
        ]
        self.inserted_keys: dict[str, set[str]] = {}
        self.written_rows: dict[str, list[dict]] = {}

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        params = params or {}

        if sql.strip().upper().startswith("SET LOCAL") or "set_config" in sql:
            return _FakeResult()
        if "FROM core.schemes" in sql:
            return _FakeResult(fetchone_row=self.scheme_row)
        if "FROM core.lots" in sql:
            return _FakeResult(all_rows=[(lid, num) for lid, num in self.lots])
        if "FROM core.parties" in sql:
            return _FakeResult(all_rows=[(email, pid) for email, pid in self.parties])

        if sql.strip().upper().startswith("INSERT INTO GOVERNANCE."):
            table = sql.split("governance.", 1)[1].split(" ", 1)[0].split("(", 1)[0].strip()
            key = params.get("id", params.get("agm_id"))
            bucket = self.inserted_keys.setdefault(table, set())
            first_time = key not in bucket
            bucket.add(key)
            self.written_rows.setdefault(table, []).append(params)

            if "RETURNING (xmax = 0) AS inserted" in sql:
                return _FakeResult(scalar_value=first_time)
            if "RETURNING ack_id" in sql:
                return _FakeResult(fetchone_row=(key,) if first_time else None)
            return _FakeResult()

        raise AssertionError(f"Unexpected SQL in test fake: {sql[:120]}")


def _session_factory(session: _FakeSession):
    class _Ctx:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *exc):
            return False

    return lambda: _Ctx()


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        for doc in self._docs:
            yield doc

    async def to_list(self, _n):
        return list(self._docs)


class _FakeCollection:
    def __init__(self, docs):
        self._docs = docs

    def find(self, query, _projection=None):
        building_id = query.get("building_id")
        matched = [d for d in self._docs if d.get("building_id") == building_id]
        status_filter = query.get("status")
        if isinstance(status_filter, dict) and "$in" in status_filter:
            matched = [d for d in matched if d.get("status") in status_filter["$in"]]
        return _FakeCursor(matched)

    async def find_one(self, query, *_args, **_kwargs):
        for doc in self._docs:
            ok = True
            for key, value in query.items():
                if key == "$regex":
                    continue
                if isinstance(value, dict) and "$regex" in value:
                    import re
                    if not re.match(value["$regex"], str(doc.get(key, "")), re.IGNORECASE if value.get("$options") == "i" else 0):
                        ok = False
                        break
                elif doc.get(key) != value:
                    ok = False
                    break
            if ok:
                return doc
        return None

    async def count_documents(self, query):
        building_id = query.get("building_id")
        return len([d for d in self._docs if d.get("building_id") == building_id])


class _FakeMongoDB:
    def __init__(self, collections: dict[str, list[dict]]):
        self._collections = {name: _FakeCollection(docs) for name, docs in collections.items()}

    def __getattr__(self, name):
        return self._collections.setdefault(name, _FakeCollection([]))


def _base_collections(**overrides) -> dict[str, list[dict]]:
    data = {
        "units": [
            {"unit_number": "TH087", "lot_number": "LOT87", "is_test_data": False, "building_id": "13195"},
            {"unit_number": "UA063", "lot_number": "LOT63", "is_test_data": False, "building_id": "13195"},
        ],
        "users": [
            {"id": "user-gagneet", "email": "gagneet@eastgateresidences.com.au", "unit_number": "TH087", "building_id": "13195"},
            {"id": "user-anthony", "email": "anthony@eastgateresidences.com.au", "unit_number": "UA063", "building_id": "13195"},
        ],
        "agm": [],
        "agm_motions": [],
        "agm_votes": [],
        "agm_attendance": [],
        "ec_members": [],
        "by_laws": [],
        "by_laws_acknowledgments": [],
        "decisions": [],
    }
    data.update(overrides)
    return data


def _make_service(session: _FakeSession, mongo_collections: dict[str, list[dict]]):
    return GovernanceBootstrapService(
        mongo_db=_FakeMongoDB(mongo_collections),
        pg_session_factory=_session_factory(session),
    )


# ---------------------------------------------------------------------------
# Missing scheme
# ---------------------------------------------------------------------------

class TestMissingScheme:
    @pytest.mark.asyncio
    async def test_raises_when_scheme_not_found(self):
        session = _FakeSession(scheme_row=None)
        service = _make_service(session, _base_collections())
        with pytest.raises(ValidationError):
            await service.run(building_id="99999", mode=BootstrapMode.DRY_RUN)


# ---------------------------------------------------------------------------
# Dry-run writes nothing
# ---------------------------------------------------------------------------

class TestDryRunWritesNothing:
    @pytest.mark.asyncio
    async def test_dry_run_reports_but_does_not_insert(self):
        collections = _base_collections(
            agm=[{"id": "agm-1", "building_id": "13195", "title": "AGM 2026", "date": "2026-02-23T18:00", "status": "upcoming", "location": "Online"}],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.DRY_RUN)

        assert report.agm_records_found == 1
        assert report.agm_records_created == 0
        assert session.written_rows == {}


# ---------------------------------------------------------------------------
# agm_records
# ---------------------------------------------------------------------------

class TestAgmRecords:
    @pytest.mark.asyncio
    async def test_apply_creates_agm_record(self):
        collections = _base_collections(
            agm=[{"id": "agm-1", "building_id": "13195", "title": "Annual General Meeting - 2026", "date": "2026-02-23T18:00", "status": "upcoming", "location": "Online"}],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.agm_records_created == 1
        row = session.written_rows["agm_records"][0]
        assert row["meeting_type"] == "agm"
        assert row["scheduled_date"] == date(2026, 2, 23)
        assert row["held_date"] is None  # status is "upcoming", not held

    @pytest.mark.asyncio
    async def test_second_apply_is_idempotent(self):
        collections = _base_collections(
            agm=[{"id": "agm-1", "building_id": "13195", "title": "AGM 2026", "date": "2026-02-23T18:00", "status": "upcoming", "location": "Online"}],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        await service.run(building_id="13195", mode=BootstrapMode.APPLY)
        report2 = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report2.agm_records_created == 0
        assert report2.agm_records_updated == 1


# ---------------------------------------------------------------------------
# ec_members — the Jessica-the-strata-manager case
# ---------------------------------------------------------------------------

class TestEcMembers:
    @pytest.mark.asyncio
    async def test_resolves_ec_member_by_email_and_links_lot(self):
        collections = _base_collections(
            agm=[{"id": "agm-founding", "building_id": "13195", "title": "First AGM", "date": "2026-02-10T18:00",
                  "status": "completed", "location": "Online"}],
            ec_members=[
                {"id": "ec-1", "building_id": "13195", "name": "Gagneet Singh", "position": "Secretary",
                 "email": "gagneet@eastgateresidences.com.au", "created_at": "2026-02-14T13:22:26"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.ec_members_created == 1
        row = session.written_rows["ec_members"][0]
        assert row["party_id"] == PARTY_GAGNEET_ID
        assert row["ec_position"] == "SECRETARY"
        assert row["lot_id"] == LOT_87_ID
        assert row["term_start"] == date(2026, 2, 10)
        assert row["elected_at_agm_id"] is not None

    @pytest.mark.asyncio
    async def test_skipped_when_no_held_agm_and_no_override(self):
        collections = _base_collections(
            ec_members=[
                {"id": "ec-1", "building_id": "13195", "name": "Gagneet Singh", "position": "Secretary",
                 "email": "gagneet@eastgateresidences.com.au", "created_at": "2026-02-14T13:22:26"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.ec_members_created == 0
        assert any(
            s.table == "ec_members" and "cannot determine term_start" in s.reason
            for s in report.skipped_records
        )

    @pytest.mark.asyncio
    async def test_rerun_upgrades_override_term_start_once_real_agm_exists(self):
        collections = _base_collections(
            ec_members=[
                {"id": "ec-1", "building_id": "13195", "name": "Gagneet Singh", "position": "Secretary",
                 "email": "gagneet@eastgateresidences.com.au", "created_at": "2026-02-14T13:22:26"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        await service.run(
            building_id="13195", mode=BootstrapMode.APPLY,
            ec_term_start_override=date(2026, 2, 10),
        )
        first_row = session.written_rows["ec_members"][0]
        assert first_row["elected_at_agm_id"] is None

        collections["agm"].append(
            {"id": "agm-founding", "building_id": "13195", "title": "First AGM",
             "date": "2026-02-10T18:00", "status": "completed", "location": "Online"}
        )
        report2 = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report2.ec_members_updated == 1
        last_row = session.written_rows["ec_members"][-1]
        assert last_row["term_start"] == date(2026, 2, 10)
        assert last_row["elected_at_agm_id"] is not None

    @pytest.mark.asyncio
    async def test_term_start_override_used_when_no_held_agm(self):
        collections = _base_collections(
            ec_members=[
                {"id": "ec-1", "building_id": "13195", "name": "Gagneet Singh", "position": "Secretary",
                 "email": "gagneet@eastgateresidences.com.au", "created_at": "2026-02-14T13:22:26"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(
            building_id="13195", mode=BootstrapMode.APPLY,
            ec_term_start_override=date(2026, 2, 10),
        )

        assert report.ec_members_created == 1
        row = session.written_rows["ec_members"][0]
        assert row["term_start"] == date(2026, 2, 10)
        assert row["elected_at_agm_id"] is None
        assert any("--ec-term-start-override" in note for note in report.anomalies)

    @pytest.mark.asyncio
    async def test_strata_manager_position_is_flagged_not_migrated(self):
        collections = _base_collections(
            ec_members=[
                {"id": "ec-6", "building_id": "13195", "name": "Jessica Minichiello",
                 "position": "Strata Manager - Civium", "email": "jessica@civium.com.au",
                 "created_at": "2026-02-14T13:22:26"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.ec_members_created == 0
        assert session.written_rows.get("ec_members") is None
        assert any("Jessica" in note for note in report.anomalies)
        assert any(s.table == "ec_members" for s in report.skipped_records)

    @pytest.mark.asyncio
    async def test_ec_member_without_matching_party_is_skipped_not_fabricated(self):
        collections = _base_collections(
            ec_members=[
                {"id": "ec-7", "building_id": "13195", "name": "No Body", "position": "Treasurer",
                 "email": "nobody@example.com", "created_at": "2026-02-14T13:22:26"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(
            building_id="13195", mode=BootstrapMode.APPLY,
            ec_term_start_override=date(2026, 2, 10),
        )

        assert report.ec_members_created == 0
        assert any(s.table == "ec_members" and "no core.parties row" in s.reason for s in report.skipped_records)

    @pytest.mark.asyncio
    async def test_committee_member_typo_variant_maps_to_member(self):
        collections = _base_collections(
            ec_members=[
                {"id": "ec-2", "building_id": "13195", "name": "Brenda Thompson", "position": "Commitee Member",
                 "email": "gagneet@eastgateresidences.com.au", "created_at": "2026-02-14T13:22:26"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(
            building_id="13195", mode=BootstrapMode.APPLY,
            ec_term_start_override=date(2026, 2, 10),
        )

        assert report.ec_members_created == 1
        assert session.written_rows["ec_members"][0]["ec_position"] == "MEMBER"


# ---------------------------------------------------------------------------
# by_laws / by_laws_acks
# ---------------------------------------------------------------------------

class TestByLaws:
    @pytest.mark.asyncio
    async def test_by_laws_version_becomes_one_row(self):
        collections = _base_collections(
            by_laws=[{"id": "bl-1", "building_id": "13195", "version": "2026-v1",
                      "effective_date": "2026-01-01", "content": "# By-Laws\n..."}],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.by_laws_created == 1
        row = session.written_rows["by_laws"][0]
        assert row["by_law_number"] == "2026-v1"
        assert row["effective_from"] == date(2026, 1, 1)
        assert any("ONE governance.by_laws row" in note for note in report.anomalies)

    @pytest.mark.asyncio
    async def test_ack_resolves_lot_and_by_law(self):
        collections = _base_collections(
            by_laws=[{"id": "bl-1", "building_id": "13195", "version": "2026-v1",
                      "effective_date": "2026-01-01", "content": "# By-Laws"}],
            by_laws_acknowledgments=[
                {"_id": "ack-1", "building_id": "13195", "user_id": "user-gagneet",
                 "by_laws_version": "2026-v1", "acknowledged_date": "2026-02-17T06:44:27"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.by_laws_acks_created == 1
        row = session.written_rows["by_laws_acks"][0]
        assert row["lot_id"] == LOT_87_ID
        assert row["party_id"] == PARTY_GAGNEET_ID

    @pytest.mark.asyncio
    async def test_ack_for_unknown_version_is_skipped(self):
        collections = _base_collections(
            by_laws=[{"id": "bl-1", "building_id": "13195", "version": "2026-v1",
                      "effective_date": "2026-01-01", "content": "# By-Laws"}],
            by_laws_acknowledgments=[
                {"_id": "ack-legacy", "building_id": "13195", "user_id": "user-gagneet",
                 "version": "2024-2025", "acknowledged_at": "2026-02-14T13:22:26"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.by_laws_acks_created == 0
        assert any(s.table == "by_laws_acks" and "no matching governance.by_laws row" in s.reason for s in report.skipped_records)


# ---------------------------------------------------------------------------
# agm_attendance — must not backdate RSVPs on an unheld meeting
# ---------------------------------------------------------------------------

class TestAgmAttendance:
    @pytest.mark.asyncio
    async def test_attendance_skipped_when_agm_not_yet_held(self):
        collections = _base_collections(
            agm=[{"id": "agm-1", "building_id": "13195", "title": "AGM 2026", "date": "2026-02-23T18:00",
                  "status": "upcoming", "location": "Online"}],
            agm_attendance=[
                {"_id": "att-1", "building_id": "13195", "agm_id": "agm-1", "user_id": "user-gagneet",
                 "status": "attending", "proxy_id": None, "confirmed_at": "2026-03-05T10:18:40"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.agm_attendance_created == 0
        assert any(
            s.table == "agm_attendance" and "has not been held yet" in s.reason
            for s in report.skipped_records
        )

    @pytest.mark.asyncio
    async def test_attendance_migrated_when_agm_held(self):
        collections = _base_collections(
            agm=[{"id": "agm-1", "building_id": "13195", "title": "AGM 2026", "date": "2026-02-23T18:00",
                  "status": "completed", "location": "Online"}],
            agm_attendance=[
                {"_id": "att-1", "building_id": "13195", "agm_id": "agm-1", "user_id": "user-gagneet",
                 "status": "attending", "proxy_id": None, "confirmed_at": "2026-03-05T10:18:40"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.agm_attendance_created == 1
        row = session.written_rows["agm_attendance"][0]
        assert row["lot_id"] == LOT_87_ID
        assert row["attendance_type"] == "in_person"


# ---------------------------------------------------------------------------
# agm_motions / agm_votes
# ---------------------------------------------------------------------------

class TestMotionsAndVotes:
    @pytest.mark.asyncio
    async def test_motion_numbering_and_vote_value_mapping(self):
        collections = _base_collections(
            agm=[{"id": "agm-1", "building_id": "13195", "title": "AGM 2026", "date": "2026-02-23T18:00",
                  "status": "completed", "location": "Online"}],
            agm_motions=[
                {"id": "mo-1", "building_id": "13195", "agm_id": "agm-1", "title": "Approve budget",
                 "description": "", "status": "proposed", "created_at": "2026-02-20T00:00:00"},
            ],
            agm_votes=[
                {"id": "v-1", "building_id": "13195", "motion_id": "mo-1", "unit_number": "TH087",
                 "vote": "for", "created_at": "2026-02-23T18:05:00"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.agm_motions_created == 1
        assert session.written_rows["agm_motions"][0]["motion_number"] == 1
        assert report.agm_votes_created == 1
        assert session.written_rows["agm_votes"][0]["vote"] == "yes"

    @pytest.mark.asyncio
    async def test_unrecognised_vote_value_is_skipped(self):
        collections = _base_collections(
            agm=[{"id": "agm-1", "building_id": "13195", "title": "AGM 2026", "date": "2026-02-23T18:00",
                  "status": "completed", "location": "Online"}],
            agm_motions=[
                {"id": "mo-1", "building_id": "13195", "agm_id": "agm-1", "title": "Approve budget",
                 "description": "", "status": "proposed", "created_at": "2026-02-20T00:00:00"},
            ],
            agm_votes=[
                {"id": "v-1", "building_id": "13195", "motion_id": "mo-1", "unit_number": "TH087",
                 "vote": "maybe", "created_at": "2026-02-23T18:05:00"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.agm_votes_created == 0
        assert any(s.table == "agm_votes" and "unrecognised vote value" in s.reason for s in report.skipped_records)


# ---------------------------------------------------------------------------
# Multi-tenant isolation
# ---------------------------------------------------------------------------

class TestMultiTenantIsolation:
    @pytest.mark.asyncio
    async def test_building_16244_records_are_not_counted_for_13195(self):
        collections = _base_collections(
            agm=[
                {"id": "agm-a", "building_id": "13195", "title": "AGM A", "date": "2026-02-23T18:00", "status": "upcoming", "location": "Online"},
                {"id": "agm-b", "building_id": "16244", "title": "AGM B", "date": "2026-03-01T18:00", "status": "upcoming", "location": "Online"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.DRY_RUN)

        assert report.agm_records_found == 1


# ---------------------------------------------------------------------------
# decisions — no source rows yet, no fabricated mapping
# ---------------------------------------------------------------------------

class TestDecisions:
    @pytest.mark.asyncio
    async def test_zero_decisions_is_not_an_error(self):
        session = _FakeSession()
        service = _make_service(session, _base_collections())
        report = await service.run(building_id="13195", mode=BootstrapMode.DRY_RUN)
        assert report.decisions_found == 0
        assert report.decisions_created == 0


# ---------------------------------------------------------------------------
# Regression tests for the 2026-07-20 audit fixes
# ---------------------------------------------------------------------------

class TestAgmIdMapOnlyContainsMigratedAgms:
    """A skipped (unparseable-date) AGM must never appear in agm_id_map — every
    downstream method (agm_motions/agm_votes/agm_attendance/ec_members) trusts
    the map as "this agm_id row was actually written to governance.agm_records".
    """

    @pytest.mark.asyncio
    async def test_motion_referencing_a_skipped_agm_is_skipped_not_inserted(self):
        collections = _base_collections(
            agm=[{"id": "agm-bad-date", "building_id": "13195", "title": "Broken AGM",
                  "date": "not-a-date", "status": "upcoming", "location": "Online"}],
            agm_motions=[
                {"id": "mo-1", "building_id": "13195", "agm_id": "agm-bad-date",
                 "title": "Approve budget", "description": "", "status": "proposed",
                 "created_at": "2026-02-20T00:00:00"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.agm_records_created == 0
        assert any(s.table == "agm_records" and "no parseable scheduled date" in s.reason for s in report.skipped_records)
        assert report.agm_motions_created == 0
        assert any(
            s.table == "agm_motions" and "not migrated" in s.reason
            for s in report.skipped_records
        )
        assert session.written_rows.get("agm_motions") is None


class TestByLawsAcksDoNotCollapseDistinctAcknowledgments:
    """governance.by_laws_acks has no UNIQUE(by_law_id, lot_id) constraint (unlike
    agm_votes/agm_attendance, which do) — two different co-owners of the same lot
    each acknowledging the by-laws are two distinct real events and must both be
    recorded, not silently collapsed into one row.
    """

    @pytest.mark.asyncio
    async def test_two_co_owners_of_the_same_lot_both_get_a_row(self):
        collections = _base_collections(
            users=[
                {"id": "user-gagneet", "email": "gagneet@eastgateresidences.com.au", "unit_number": "TH087", "building_id": "13195"},
                {"id": "user-co-owner", "email": "co-owner@eastgateresidences.com.au", "unit_number": "TH087", "building_id": "13195"},
                {"id": "user-anthony", "email": "anthony@eastgateresidences.com.au", "unit_number": "UA063", "building_id": "13195"},
            ],
            by_laws=[{"id": "bl-1", "building_id": "13195", "version": "2026-v1",
                      "effective_date": "2026-01-01", "content": "# By-Laws"}],
            by_laws_acknowledgments=[
                {"_id": "ack-1", "building_id": "13195", "user_id": "user-gagneet",
                 "by_laws_version": "2026-v1", "acknowledged_date": "2026-02-17T06:44:27"},
                {"_id": "ack-2", "building_id": "13195", "user_id": "user-co-owner",
                 "by_laws_version": "2026-v1", "acknowledged_date": "2026-02-18T09:00:00"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.by_laws_acks_created == 2
        ack_ids = {row["id"] for row in session.written_rows["by_laws_acks"]}
        assert len(ack_ids) == 2, "both acknowledgments must get distinct ack_ids, not collapse to one"


class TestEcMemberEmailLookupEscapesRegex:
    """The Mongo email lookup builds a $regex from the ec_member's own email —
    an unescaped '.' matches any character, so a decoy user whose email differs
    from the real one only by a single non-dot character in that position must
    NOT match.
    """

    @pytest.mark.asyncio
    async def test_decoy_user_differing_by_one_character_does_not_match(self):
        collections = _base_collections(
            users=[
                # No exact match for "jo.smith@example.com" exists — only this
                # decoy, which an unescaped "." in the regex would incorrectly match.
                {"id": "user-decoy", "email": "joXsmith@example.com", "unit_number": "TH087", "building_id": "13195"},
            ],
            ec_members=[
                {"id": "ec-1", "building_id": "13195", "name": "Jo Smith", "position": "Treasurer",
                 "email": "jo.smith@example.com", "created_at": "2026-02-14T13:22:26"},
            ],
        )
        session = _FakeSession(
            parties=[("jo.smith@example.com", PARTY_GAGNEET_ID)],
        )
        service = _make_service(session, collections)
        report = await service.run(
            building_id="13195", mode=BootstrapMode.APPLY,
            ec_term_start_override=date(2026, 2, 10),
        )

        assert report.ec_members_created == 1
        row = session.written_rows["ec_members"][0]
        assert row["lot_id"] is None, "must not resolve to the decoy user's unit (TH087) via an unescaped regex match"


class TestByLawsEffectiveDateNeverFabricated:
    @pytest.mark.asyncio
    async def test_missing_effective_date_is_skipped_not_defaulted_to_today(self):
        collections = _base_collections(
            by_laws=[{"id": "bl-1", "building_id": "13195", "version": "2026-v1",
                      "content": "# By-Laws"}],  # no effective_date field at all
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.by_laws_created == 0
        assert any(
            s.table == "by_laws" and "no parseable effective_date" in s.reason
            for s in report.skipped_records
        )
        assert session.written_rows.get("by_laws") is None


class TestAgmAttendanceRespectsOwnRsvpStatus:
    """Second audit pass (2026-07-20) finding: the original code gated only on whether the
    PARENT AGM was held, never on the attendance record's own status. The app's real status
    vocabulary (backend/models/community.py's AGMAttendanceUpdate) is "attending" or "apology"
    -- an apology is an explicit statement of NOT attending. Writing it as an attendance row
    would falsely show someone attended a legal meeting they told the app they'd miss.
    """

    @pytest.mark.asyncio
    async def test_apology_status_is_not_recorded_as_attendance(self):
        collections = _base_collections(
            agm=[{"id": "agm-1", "building_id": "13195", "title": "AGM 2026", "date": "2026-02-23T18:00",
                  "status": "completed", "location": "Online"}],
            agm_attendance=[
                {"_id": "att-1", "building_id": "13195", "agm_id": "agm-1", "user_id": "user-gagneet",
                 "status": "apology", "proxy_id": None, "confirmed_at": "2026-03-05T10:18:40"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.agm_attendance_created == 0
        assert session.written_rows.get("agm_attendance") is None
        assert any(
            s.table == "agm_attendance" and "not an attendance record" in s.reason
            for s in report.skipped_records
        )

    @pytest.mark.asyncio
    async def test_missing_status_is_not_recorded_as_attendance(self):
        collections = _base_collections(
            agm=[{"id": "agm-1", "building_id": "13195", "title": "AGM 2026", "date": "2026-02-23T18:00",
                  "status": "completed", "location": "Online"}],
            agm_attendance=[
                {"_id": "att-1", "building_id": "13195", "agm_id": "agm-1", "user_id": "user-gagneet",
                 "proxy_id": None, "confirmed_at": "2026-03-05T10:18:40"},  # no status field at all
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.agm_attendance_created == 0
        assert session.written_rows.get("agm_attendance") is None


class TestByLawsAcksAcknowledgedViaReflectsSourceEvidence:
    @pytest.mark.asyncio
    async def test_seed_script_origin_is_recorded_not_assumed_portal(self):
        collections = _base_collections(
            by_laws=[{"id": "bl-1", "building_id": "13195", "version": "2026-v1",
                      "effective_date": "2026-01-01", "content": "# By-Laws"}],
            by_laws_acknowledgments=[
                {"_id": "ack-1", "building_id": "13195", "user_id": "user-gagneet",
                 "by_laws_version": "2026-v1", "acknowledged_at": "2026-02-14T13:22:26",
                 "user_agent": "seed_script"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.by_laws_acks_created == 1
        assert session.written_rows["by_laws_acks"][0]["acknowledged_via"] == "seed"

    @pytest.mark.asyncio
    async def test_no_user_agent_defaults_to_portal(self):
        collections = _base_collections(
            by_laws=[{"id": "bl-1", "building_id": "13195", "version": "2026-v1",
                      "effective_date": "2026-01-01", "content": "# By-Laws"}],
            by_laws_acknowledgments=[
                {"_id": "ack-1", "building_id": "13195", "user_id": "user-gagneet",
                 "by_laws_version": "2026-v1", "acknowledged_date": "2026-02-17T06:44:27"},
            ],
        )
        session = _FakeSession()
        service = _make_service(session, collections)
        report = await service.run(building_id="13195", mode=BootstrapMode.APPLY)

        assert report.by_laws_acks_created == 1
        assert session.written_rows["by_laws_acks"][0]["acknowledged_via"] == "portal"
