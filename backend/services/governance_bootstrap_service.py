# @featuretrace:legacy-cleanup — Governance genesis: idempotent Mongo -> Postgres population for governance.* (Phase G1).
# Layer: service
# Data flow: bootstrap CLI -> GovernanceBootstrapService -> governance.agm_records/agm_motions/agm_votes/agm_attendance/ec_members/by_laws/by_laws_acks (building-scoped).
# Related: backend/scripts/bootstrap_postgres_governance_genesis.py
#          backend/services/identity_bootstrap_service.py (pattern this mirrors)
#          backend/alembic/versions/0033_governance_schema.py
#          docs/migration/governance_occupancy_settings_migration_plan.md
#          tests/backend/test_governance_bootstrap_service.py
"""Governance genesis population: Mongo -> Postgres, dry-run first.

Deliberately NOT a blind copy. `governance.*` is a 7-year compliance
retention register — see 0033_governance_schema.py's docstring (2026-07-20
correction: its original "ACT ss.115-116"/"s.109" citations were verified
wrong against the actual Act text and replaced with an accurate summary of
what those sections cover). Mongo's `ec_members`/`agm_attendance` collections
mix concepts
that don't map 1:1 onto that register (e.g. a strata manager listed
alongside elected EC office bearers; RSVP "attending" status on a meeting
that hasn't been held yet, not a post-meeting quorum record). Anything
ambiguous is reported as `skipped` with a reason, never guessed at and
written through. Always run in `dry_run` (default) first and read the
report before `apply`.
"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any, AsyncIterator, Callable
from uuid import UUID, uuid5

from sqlalchemy import text

from db_postgres.session import async_session_context, set_tenant

_GOVERNANCE_NAMESPACE = UUID("7a2f1c3e-9b6d-4e1a-8f2c-5d3a9e7b1c40")

_VOTE_VALUE_MAP = {"for": "yes", "against": "no", "abstain": "abstain"}

_EC_POSITION_MAP = {
    "chairman": "CHAIRMAN",
    "treasurer": "TREASURER",
    "secretary": "SECRETARY",
    "committee member": "MEMBER",
    "commitee member": "MEMBER",  # live Mongo data carries this exact typo for East Gate
    "member": "MEMBER",
}


class BootstrapMode(str, Enum):
    DRY_RUN = "dry_run"
    APPLY = "apply"
    VALIDATE_ONLY = "validate_only"


def _stable_uuid(kind: str, *parts: str) -> str:
    clean = "::".join((kind, *(str(p or "").strip().lower() for p in parts)))
    return str(uuid5(_GOVERNANCE_NAMESPACE, clean))


def _plain_lot_number(raw: str) -> str:
    raw = (raw or "").strip()
    return raw[3:] if raw.upper().startswith("LOT") else raw


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        return datetime.fromisoformat(text_value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        return datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(slots=True)
class SkippedRecord:
    table: str
    source_id: str
    reason: str


@dataclass(slots=True)
class GovernanceBootstrapReport:
    mode: BootstrapMode
    plan_number: str
    import_batch_id: str | None = None

    agm_records_found: int = 0
    agm_records_created: int = 0
    agm_records_updated: int = 0

    agm_motions_found: int = 0
    agm_motions_created: int = 0

    agm_votes_found: int = 0
    agm_votes_created: int = 0

    agm_attendance_found: int = 0
    agm_attendance_created: int = 0

    ec_members_found: int = 0
    ec_members_created: int = 0
    ec_members_updated: int = 0

    by_laws_found: int = 0
    by_laws_created: int = 0
    by_laws_updated: int = 0

    by_laws_acks_found: int = 0
    by_laws_acks_created: int = 0

    decisions_found: int = 0
    decisions_created: int = 0

    skipped_records: list[SkippedRecord] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = {
            k: getattr(self, k)
            for k in self.__slots__
            if k not in ("skipped_records",)
        }
        out["mode"] = self.mode.value
        out["skipped_records"] = [
            {"table": s.table, "source_id": s.source_id, "reason": s.reason}
            for s in self.skipped_records
        ]
        return out


class ValidationError(ValueError):
    """Raised when the target scheme/tenant is not already onboarded."""


class GovernanceBootstrapService:
    """Populate governance.* for a scheme already present in core.schemes.

    Unlike IdentityBootstrapService, this does NOT create tenants/schemes/
    lots/parties — governance genesis only runs after identity foundation
    (Phase A/B/C) already exists for the building. If the scheme isn't
    found, this raises rather than fabricating one.
    """

    def __init__(
        self,
        *,
        mongo_db: Any,
        pg_session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._mongo_db = mongo_db
        self._pg_session_factory = pg_session_factory

    @asynccontextmanager
    async def _session_context(self) -> AsyncIterator[Any]:
        if self._pg_session_factory is None:
            async with async_session_context() as session:
                yield session
            return
        async with self._pg_session_factory() as session:
            yield session

    async def run(
        self,
        *,
        building_id: str,
        mode: BootstrapMode,
        import_batch_id: str | None = None,
        ec_term_start_override: date | None = None,
    ) -> GovernanceBootstrapReport:
        report = GovernanceBootstrapReport(
            mode=mode,
            plan_number=building_id,
            import_batch_id=import_batch_id,
        )
        async with self._session_context() as session:
            # core.schemes RLS has a bypass clause for app.tenant_id = the zero UUID,
            # but with no tenant context set at all the GUC is NULL and the bypass
            # comparison is also NULL (falsy) — the row is invisible even though the
            # policy nominally allows it. Set the bypass explicitly before the one
            # query that has to run before we know the real tenant_id.
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": "00000000-0000-0000-0000-000000000000"},
            )
            scheme_row = (
                await session.execute(
                    text(
                        "SELECT scheme_id::text, tenant_id::text FROM core.schemes "
                        "WHERE lower(scheme_number) = lower(:num) LIMIT 1"
                    ),
                    {"num": str(building_id)},
                )
            ).fetchone()
            if scheme_row is None:
                raise ValidationError(
                    f"No core.schemes row for building_id={building_id!r} — "
                    "run identity foundation bootstrap first "
                    "(bootstrap_postgres_identity_foundation.py)."
                )
            scheme_id, tenant_id = scheme_row
            await set_tenant(session, tenant_id)

            lot_id_by_unit_number = await self._resolve_lot_ids(session, scheme_id, building_id)
            party_id_by_email = await self._resolve_parties_by_email(session, tenant_id)

            apply = mode == BootstrapMode.APPLY

            agm_id_map = await self._populate_agm_records(
                session, scheme_id, tenant_id, building_id, report, apply=apply
            )
            await self._populate_ec_members(
                session, scheme_id, tenant_id, building_id, report,
                lot_id_by_unit_number, party_id_by_email, agm_id_map,
                ec_term_start_override, apply=apply,
            )
            by_law_id_by_version = await self._populate_by_laws(
                session, scheme_id, tenant_id, building_id, report, apply=apply
            )
            await self._populate_by_laws_acks(
                session, scheme_id, tenant_id, building_id, report,
                by_law_id_by_version, lot_id_by_unit_number, party_id_by_email, apply=apply,
            )
            motion_id_map = await self._populate_agm_motions(
                session, scheme_id, tenant_id, building_id, report, agm_id_map, apply=apply
            )
            await self._populate_agm_votes(
                session, scheme_id, tenant_id, building_id, report,
                motion_id_map, agm_id_map, lot_id_by_unit_number, apply=apply,
            )
            await self._populate_agm_attendance(
                session, scheme_id, tenant_id, building_id, report,
                agm_id_map, lot_id_by_unit_number, party_id_by_email, apply=apply,
            )
            await self._count_decisions(building_id, report)

        return report

    # ── Postgres lookups shared across tables ───────────────────────────────

    async def _resolve_lot_ids(self, session: Any, scheme_id: str, building_id: str) -> dict[str, str]:
        """Mongo units.unit_number ("TH071") -> core.lots.lot_id.

        Mirrors services/levy_generation_service.py's _resolve_lot_ids — same
        three-format mismatch (core.lots plain digits vs Mongo's LOT/TH/UA
        prefixed unit_number), duplicated here rather than imported since
        that function is private to its own module and this service has no
        finance dependency otherwise.
        """
        pg_rows = (
            await session.execute(
                text("SELECT lot_id::text, lot_number FROM core.lots WHERE scheme_id = :sid"),
                {"sid": str(scheme_id)},
            )
        ).all()
        lot_id_by_plain_number = {str(num): lot_id for lot_id, num in pg_rows}

        mongo_units = await self._mongo_db.units.find(
            {"building_id": building_id, "is_test_data": {"$ne": True}},
            {"unit_number": 1, "lot_number": 1, "_id": 0},
        ).to_list(None)

        result: dict[str, str] = {}
        for doc in mongo_units:
            unit_number = str(doc.get("unit_number") or "").strip()
            plain = _plain_lot_number(str(doc.get("lot_number") or ""))
            lot_id = lot_id_by_plain_number.get(plain)
            if unit_number and lot_id is not None:
                result[unit_number] = lot_id
        return result

    async def _resolve_parties_by_email(self, session: Any, tenant_id: str) -> dict[str, str]:
        rows = (
            await session.execute(
                text(
                    "SELECT lower(primary_email::text), party_id::text FROM core.parties "
                    "WHERE tenant_id = :tid AND primary_email IS NOT NULL"
                ),
                {"tid": str(tenant_id)},
            )
        ).all()
        return {email: party_id for email, party_id in rows}

    async def _mongo_user(self, user_id: str) -> dict | None:
        if not user_id:
            return None
        return await self._mongo_db.users.find_one(
            {"id": user_id}, {"email": 1, "unit_number": 1, "id": 1, "_id": 0}
        )

    # ── agm_records ──────────────────────────────────────────────────────

    async def _populate_agm_records(
        self, session, scheme_id, tenant_id, building_id, report, *, apply: bool
    ) -> dict[str, str]:
        docs = await self._mongo_db.agm.find({"building_id": building_id}).to_list(None)
        report.agm_records_found = len(docs)
        agm_id_map: dict[str, str] = {}

        for doc in docs:
            source_id = str(doc.get("id") or doc.get("_id"))
            agm_id = _stable_uuid("agm_record", str(scheme_id), source_id)

            scheduled_date = _parse_date(doc.get("date"))
            if scheduled_date is None:
                report.skipped_records.append(
                    SkippedRecord("agm_records", source_id, "no parseable scheduled date")
                )
                continue

            # Only entered once validation passes — an entry here is a promise to every
            # downstream method (ec_members' founding-AGM resolution, agm_motions,
            # agm_votes, agm_attendance) that this agm_id exists as a governance.agm_records
            # row. A skipped/never-inserted AGM must never appear here, or those FK
            # inserts fail on a row that was never written (found and fixed 2026-07-20 —
            # this line used to run unconditionally before the date check above).
            agm_id_map[source_id] = agm_id

            title = str(doc.get("title") or "")
            meeting_type = "egm" if "egm" in title.lower() or "extraordinary" in title.lower() else "agm"
            status = str(doc.get("status") or "scheduled")
            held_date = scheduled_date if status in ("completed", "held", "closed") else None

            if not apply:
                continue

            result = await session.execute(
                text(
                    """
                    INSERT INTO governance.agm_records (
                        agm_id, tenant_id, scheme_id, meeting_type, scheduled_date,
                        held_date, location, status, is_test_data
                    ) VALUES (
                        CAST(:agm_id AS UUID), CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID),
                        :meeting_type, :scheduled_date, :held_date, :location, :status, FALSE
                    )
                    ON CONFLICT (agm_id) DO UPDATE SET
                        meeting_type = EXCLUDED.meeting_type,
                        scheduled_date = EXCLUDED.scheduled_date,
                        held_date = EXCLUDED.held_date,
                        location = EXCLUDED.location,
                        status = EXCLUDED.status,
                        updated_at = now()
                    RETURNING (xmax = 0) AS inserted
                    """
                ),
                {
                    "agm_id": agm_id, "tenant_id": tenant_id, "scheme_id": scheme_id,
                    "meeting_type": meeting_type, "scheduled_date": scheduled_date,
                    "held_date": held_date, "location": doc.get("location"), "status": status,
                },
            )
            inserted = result.scalar()
            if inserted:
                report.agm_records_created += 1
            else:
                report.agm_records_updated += 1

        return agm_id_map

    # ── ec_members ───────────────────────────────────────────────────────

    async def _resolve_founding_agm(
        self, building_id: str, agm_id_map: dict[str, str]
    ) -> tuple[date, str] | None:
        """Earliest HELD agm_records row for the scheme -> (scheduled_date, migrated agm_id).

        s.106 gives no explicit "EC term starts at the first AGM" date field in
        Mongo — this is a domain inference (ACT EC composition is set at the
        AGM that elects it), not something read directly off a record. Only
        AGMs already migrated (i.e. present in agm_id_map) are eligible, so
        elected_at_agm_id always points at a row that actually exists.
        """
        docs = await self._mongo_db.agm.find(
            {"building_id": building_id, "status": {"$in": ["completed", "held", "closed"]}}
        ).to_list(None)
        candidates: list[tuple[date, str]] = []
        for doc in docs:
            source_id = str(doc.get("id") or doc.get("_id"))
            migrated_id = agm_id_map.get(source_id)
            scheduled = _parse_date(doc.get("date"))
            if migrated_id is not None and scheduled is not None:
                candidates.append((scheduled, migrated_id))
        if not candidates:
            return None
        candidates.sort(key=lambda pair: pair[0])
        return candidates[0]

    async def _populate_ec_members(
        self, session, scheme_id, tenant_id, building_id, report,
        lot_id_by_unit_number: dict[str, str], party_id_by_email: dict[str, str],
        agm_id_map: dict[str, str], ec_term_start_override: date | None, *, apply: bool,
    ) -> None:
        docs = await self._mongo_db.ec_members.find({"building_id": building_id}).to_list(None)
        report.ec_members_found = len(docs)
        founding_agm = await self._resolve_founding_agm(building_id, agm_id_map)

        for doc in docs:
            source_id = str(doc.get("id") or doc.get("_id"))
            email = str(doc.get("email") or "").strip().lower()
            position_raw = str(doc.get("position") or "").strip().lower()
            ec_position = _EC_POSITION_MAP.get(position_raw)
            party_id = party_id_by_email.get(email) if email else None

            if ec_position is None:
                report.anomalies.append(
                    f"ec_members: {doc.get('name')!r} (source {source_id}) has position "
                    f"{doc.get('position')!r}, which does not map to a governance EC office-bearer "
                    "position (CHAIRMAN/TREASURER/SECRETARY/MEMBER). This looks like a non-EC "
                    "contact (e.g. an appointed strata/building manager) stored in the same "
                    "Mongo collection as elected EC members. Not written to the compliance "
                    "register — if this person needs recording, it belongs in "
                    "core.scheme_manager_appointments (ECPosition.STRATA_MANAGER/BUILDING_MANAGER), "
                    "not governance.ec_members."
                )
                report.skipped_records.append(
                    SkippedRecord("ec_members", source_id, f"unmapped position {doc.get('position')!r}")
                )
                continue
            if party_id is None:
                report.skipped_records.append(
                    SkippedRecord(
                        "ec_members", source_id,
                        f"no core.parties row with primary_email={email!r}; cannot link to a party",
                    )
                )
                continue

            unit_number = None
            # ec_members has no unit_number field directly; best-effort via a
            # matching Mongo user record with the same email, if one exists.
            # re.escape is required: every real email contains unescaped regex
            # metacharacters ("." matches any char) — found 2026-07-20, never
            # triggered a wrong match against East Gate's real data (no two
            # emails differ only by a single character in place of a ".") but
            # is a real correctness gap for any other building's data.
            mongo_user = await self._mongo_db.users.find_one(
                {"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}, "building_id": building_id},
                {"unit_number": 1, "_id": 0},
            ) if email else None
            if mongo_user:
                unit_number = mongo_user.get("unit_number")
            lot_id = lot_id_by_unit_number.get(unit_number) if unit_number else None

            elected_at_agm_id: str | None = None
            if founding_agm is not None:
                term_start, elected_at_agm_id = founding_agm
            elif ec_term_start_override is not None:
                term_start = ec_term_start_override
                report.anomalies.append(
                    f"ec_members: {doc.get('name')!r} term_start set from --ec-term-start-override "
                    f"({ec_term_start_override.isoformat()}) — no HELD AGM record exists in Mongo to "
                    "derive it from (the ACT rule is that EC composition is set at the AGM that "
                    "elects it; Mongo's `agm` collection has no record of that meeting for this "
                    "scheme). elected_at_agm_id left NULL since there is no migrated AGM row to link."
                )
            else:
                report.skipped_records.append(
                    SkippedRecord(
                        "ec_members", source_id,
                        "cannot determine term_start: no HELD agm_records row exists in Mongo for "
                        "this scheme, and no --ec-term-start-override was supplied. Pass the real "
                        "election/founding-AGM date explicitly rather than guessing one.",
                    )
                )
                continue

            if not apply:
                continue

            ec_member_id = _stable_uuid("ec_member", str(scheme_id), party_id)
            result = await session.execute(
                text(
                    """
                    INSERT INTO governance.ec_members (
                        ec_member_id, tenant_id, scheme_id, party_id, lot_id,
                        ec_position, elected_at_agm_id, term_start, is_test_data
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID),
                        CAST(:party_id AS UUID), CAST(:lot_id AS UUID), :ec_position,
                        CAST(:elected_at_agm_id AS UUID), :term_start, FALSE
                    )
                    ON CONFLICT (ec_member_id) DO UPDATE SET
                        ec_position = EXCLUDED.ec_position,
                        lot_id = EXCLUDED.lot_id,
                        elected_at_agm_id = EXCLUDED.elected_at_agm_id,
                        term_start = EXCLUDED.term_start
                    RETURNING (xmax = 0) AS inserted
                    """
                ),
                {
                    "id": ec_member_id, "tenant_id": tenant_id, "scheme_id": scheme_id,
                    "party_id": party_id, "lot_id": lot_id, "ec_position": ec_position,
                    "elected_at_agm_id": elected_at_agm_id, "term_start": term_start,
                },
            )
            # elected_at_agm_id/term_start are always overwritten with the freshly
            # resolved value on re-run (not just at insert time) — the founding-AGM
            # resolution order (real held AGM > --override > skip) always picks the
            # best evidence available *right now*, so a later re-run with a newly
            # added Mongo AGM record should replace an earlier override-derived
            # value rather than leave it stale.
            if result.scalar():
                report.ec_members_created += 1
            else:
                report.ec_members_updated += 1

    # ── by_laws (one row per Mongo version document, not split into clauses) ─

    async def _populate_by_laws(
        self, session, scheme_id, tenant_id, building_id, report, *, apply: bool
    ) -> dict[str, str]:
        docs = await self._mongo_db.by_laws.find({"building_id": building_id}).to_list(None)
        report.by_laws_found = len(docs)
        by_law_id_by_version: dict[str, str] = {}

        for doc in docs:
            version = str(doc.get("version") or "").strip()
            if not version:
                source_id = str(doc.get("id") or doc.get("_id"))
                report.skipped_records.append(SkippedRecord("by_laws", source_id, "no version field"))
                continue
            source_id = str(doc.get("id") or doc.get("_id"))
            effective_from = _parse_date(doc.get("effective_date"))
            if effective_from is None:
                # Was `or date.today()` — a silent fabricated date that contradicts this
                # module's own documented rule (module docstring: "never guessed at and
                # written through"). Found 2026-07-20; never triggered for East Gate's
                # real by_laws doc (effective_date parsed fine), so no live row carries
                # a fabricated date, but the fallback was a real, live latent bug.
                report.skipped_records.append(
                    SkippedRecord("by_laws", source_id, "no parseable effective_date")
                )
                continue
            by_law_id = _stable_uuid("by_law", str(scheme_id), version)
            by_law_id_by_version[version] = by_law_id

            if not apply:
                continue

            result = await session.execute(
                text(
                    """
                    INSERT INTO governance.by_laws (
                        by_law_id, tenant_id, scheme_id, by_law_number, title, body,
                        effective_from, registered_with_actreg, is_test_data
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID),
                        :by_law_number, :title, :body, :effective_from, FALSE, FALSE
                    )
                    ON CONFLICT (scheme_id, by_law_number) DO UPDATE SET
                        body = EXCLUDED.body,
                        effective_from = EXCLUDED.effective_from
                    RETURNING (xmax = 0) AS inserted
                    """
                ),
                {
                    "id": by_law_id, "tenant_id": tenant_id, "scheme_id": scheme_id,
                    "by_law_number": version, "title": f"Strata By-Laws — {version}",
                    "body": doc.get("content") or "", "effective_from": effective_from,
                },
            )
            if result.scalar():
                report.by_laws_created += 1
            else:
                report.by_laws_updated += 1

        if docs:
            report.anomalies.append(
                "by_laws: each Mongo version document is migrated as ONE governance.by_laws row "
                "(the whole rules text as `body`), not split into individually numbered clauses. "
                "Researched (2026-07-20) whether the numbered headings in this content could be "
                "split into per-clause rows: verified against the actual ACT Unit Titles "
                "(Management) Act 2011 text that this is not safely possible. s.106 defines an "
                "owners corporation's rules as the *default rules* (prescribed by regulation) *as "
                "modified by alternative rules registered under the Land Titles (Unit Titles) Act "
                "1970, s.27/27A* — there is no single statutory numbered list to split against, and "
                "Mongo's markdown headings don't indicate which clauses (if any) are separately "
                "registered alternative rules vs restatements of the regulation defaults. Splitting "
                "on markdown headings alone would fabricate a per-clause legal register with "
                "registration-status data that doesn't exist anywhere in this system. Keeping it as "
                "one row is the more legally honest representation of what's actually known."
            )
        return by_law_id_by_version

    # ── by_laws_acks ─────────────────────────────────────────────────────

    async def _populate_by_laws_acks(
        self, session, scheme_id, tenant_id, building_id, report,
        by_law_id_by_version: dict[str, str], lot_id_by_unit_number: dict[str, str],
        party_id_by_email: dict[str, str], *, apply: bool,
    ) -> None:
        docs = await self._mongo_db.by_laws_acknowledgments.find({"building_id": building_id}).to_list(None)
        report.by_laws_acks_found = len(docs)

        for doc in docs:
            source_id = str(doc.get("id") or doc.get("_id"))
            version = str(doc.get("by_laws_version") or doc.get("version") or "").strip()
            by_law_id = by_law_id_by_version.get(version)
            if by_law_id is None:
                report.skipped_records.append(
                    SkippedRecord(
                        "by_laws_acks", source_id,
                        f"acknowledged version {version!r} has no matching governance.by_laws row "
                        "(no current Mongo by_laws document for that version)",
                    )
                )
                continue

            user_id = str(doc.get("user_id") or "")
            mongo_user = await self._mongo_user(user_id)
            if mongo_user is None:
                report.skipped_records.append(
                    SkippedRecord("by_laws_acks", source_id, f"user_id {user_id!r} not found in Mongo users")
                )
                continue

            unit_number = mongo_user.get("unit_number")
            lot_id = lot_id_by_unit_number.get(unit_number) if unit_number else None
            if lot_id is None:
                report.skipped_records.append(
                    SkippedRecord(
                        "by_laws_acks", source_id,
                        f"could not resolve lot_id for unit_number={unit_number!r}",
                    )
                )
                continue

            email = str(mongo_user.get("email") or "").strip().lower()
            party_id = party_id_by_email.get(email)

            acknowledged_at = (
                _parse_datetime(doc.get("acknowledged_date"))
                or _parse_datetime(doc.get("acknowledged_at"))
                or _parse_datetime(doc.get("created_at"))
            )
            if acknowledged_at is None:
                report.skipped_records.append(
                    SkippedRecord("by_laws_acks", source_id, "no parseable acknowledgment timestamp")
                )
                continue

            # Was a hardcoded 'portal' literal for every row — an invented detail (found in a
            # second audit pass, 2026-07-20) that ignored evidence already present in the source
            # doc. Live East Gate data has one record with user_agent="seed_script" (a
            # database-seeded record, not a real owner clicking "acknowledge" in the app); the
            # rest have user_agent=None (consistent with a real portal acknowledgment, but not
            # proof of it — 'portal' remains a default, not a verified fact, for those).
            acknowledged_via = "seed" if doc.get("user_agent") == "seed_script" else "portal"

            if not apply:
                continue

            # Keyed on the Mongo source document, not (by_law_id, lot_id) — governance.by_laws_acks
            # has no unique constraint on (by_law_id, lot_id) (confirmed against migration 0033: only
            # a plain, non-unique index), unlike agm_votes/agm_attendance which DO have a real
            # UNIQUE(motion_id, lot_id) / UNIQUE(agm_id, lot_id) constraint backing that same keying
            # choice. Keying by (by_law_id, lot_id) here would silently collapse two different
            # co-owners' distinct acknowledgment events on the same unit into one row — the second
            # INSERT's ON CONFLICT DO NOTHING would drop it with no skip/anomaly recorded anywhere
            # (found 2026-07-20; no live row was ever written this way — by_laws_acks_created was 0
            # for East Gate the entire time this bug existed, so no data was actually lost).
            ack_id = _stable_uuid("by_law_ack", str(scheme_id), source_id)
            result = await session.execute(
                text(
                    """
                    INSERT INTO governance.by_laws_acks (
                        ack_id, tenant_id, scheme_id, by_law_id, lot_id, party_id,
                        acknowledged_at, acknowledged_via, is_test_data
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID),
                        CAST(:by_law_id AS UUID), CAST(:lot_id AS UUID), CAST(:party_id AS UUID),
                        :acknowledged_at, :acknowledged_via, FALSE
                    )
                    ON CONFLICT DO NOTHING
                    RETURNING ack_id
                    """
                ),
                {
                    "id": ack_id, "tenant_id": tenant_id, "scheme_id": scheme_id,
                    "by_law_id": by_law_id, "lot_id": lot_id, "party_id": party_id,
                    "acknowledged_at": acknowledged_at, "acknowledged_via": acknowledged_via,
                },
            )
            if result.fetchone() is not None:
                report.by_laws_acks_created += 1

    # ── agm_motions / agm_votes (no source rows for East Gate today; wired for future/other buildings) ─

    async def _populate_agm_motions(
        self, session, scheme_id, tenant_id, building_id, report, agm_id_map: dict[str, str], *, apply: bool
    ) -> dict[str, str]:
        docs = await self._mongo_db.agm_motions.find({"building_id": building_id}).to_list(None)
        report.agm_motions_found = len(docs)
        motion_id_map: dict[str, str] = {}
        counters: dict[str, int] = {}

        for doc in sorted(docs, key=lambda d: str(d.get("created_at") or "")):
            source_id = str(doc.get("id") or doc.get("_id"))
            source_agm_id = str(doc.get("agm_id") or "")
            agm_id = agm_id_map.get(source_agm_id)
            if agm_id is None:
                report.skipped_records.append(
                    SkippedRecord("agm_motions", source_id, f"parent agm_id {source_agm_id!r} not migrated")
                )
                continue

            counters[agm_id] = counters.get(agm_id, 0) + 1
            motion_number = counters[agm_id]
            motion_id = _stable_uuid("agm_motion", agm_id, str(motion_number))
            motion_id_map[source_id] = motion_id

            title = str(doc.get("title") or "")
            description = str(doc.get("description") or "")
            motion_text = f"{title}\n\n{description}".strip() if description else title
            status = str(doc.get("status") or "proposed")

            if not apply:
                continue

            result = await session.execute(
                text(
                    """
                    INSERT INTO governance.agm_motions (
                        motion_id, tenant_id, scheme_id, agm_id, motion_number, motion_text, status, is_test_data
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID),
                        CAST(:agm_id AS UUID), :motion_number, :motion_text, :status, FALSE
                    )
                    ON CONFLICT (agm_id, motion_number) DO UPDATE SET
                        motion_text = EXCLUDED.motion_text,
                        status = EXCLUDED.status
                    RETURNING (xmax = 0) AS inserted
                    """
                ),
                {
                    "id": motion_id, "tenant_id": tenant_id, "scheme_id": scheme_id,
                    "agm_id": agm_id, "motion_number": motion_number,
                    "motion_text": motion_text, "status": status,
                },
            )
            if result.scalar():
                report.agm_motions_created += 1

        return motion_id_map

    async def _populate_agm_votes(
        self, session, scheme_id, tenant_id, building_id, report,
        motion_id_map: dict[str, str], agm_id_map: dict[str, str],
        lot_id_by_unit_number: dict[str, str], *, apply: bool,
    ) -> None:
        docs = await self._mongo_db.agm_votes.find({"building_id": building_id}).to_list(None)
        report.agm_votes_found = len(docs)

        for doc in docs:
            source_id = str(doc.get("id") or doc.get("_id"))
            source_motion_id = str(doc.get("motion_id") or "")
            motion_id = motion_id_map.get(source_motion_id)
            if motion_id is None:
                report.skipped_records.append(
                    SkippedRecord("agm_votes", source_id, f"motion_id {source_motion_id!r} not migrated")
                )
                continue

            unit_number = doc.get("unit_number")
            lot_id = lot_id_by_unit_number.get(unit_number) if unit_number else None
            if lot_id is None:
                report.skipped_records.append(
                    SkippedRecord("agm_votes", source_id, f"could not resolve lot_id for unit_number={unit_number!r}")
                )
                continue

            vote = _VOTE_VALUE_MAP.get(str(doc.get("vote") or "").strip().lower())
            if vote is None:
                report.skipped_records.append(
                    SkippedRecord("agm_votes", source_id, f"unrecognised vote value {doc.get('vote')!r}")
                )
                continue

            voted_at = _parse_datetime(doc.get("created_at")) or datetime.now(UTC)

            if not apply:
                continue

            vote_id = _stable_uuid("agm_vote", motion_id, lot_id)
            result = await session.execute(
                text(
                    """
                    INSERT INTO governance.agm_votes (
                        vote_id, tenant_id, scheme_id, motion_id, lot_id, vote, voted_at, is_test_data
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID),
                        CAST(:motion_id AS UUID), CAST(:lot_id AS UUID), :vote, :voted_at, FALSE
                    )
                    ON CONFLICT (motion_id, lot_id) DO UPDATE SET
                        vote = EXCLUDED.vote,
                        voted_at = EXCLUDED.voted_at
                    RETURNING (xmax = 0) AS inserted
                    """
                ),
                {
                    "id": vote_id, "tenant_id": tenant_id, "scheme_id": scheme_id,
                    "motion_id": motion_id, "lot_id": lot_id, "vote": vote, "voted_at": voted_at,
                },
            )
            if result.scalar():
                report.agm_votes_created += 1

    # ── agm_attendance (only for meetings that have actually been held) ────

    async def _populate_agm_attendance(
        self, session, scheme_id, tenant_id, building_id, report,
        agm_id_map: dict[str, str], lot_id_by_unit_number: dict[str, str],
        party_id_by_email: dict[str, str], *, apply: bool,
    ) -> None:
        docs = await self._mongo_db.agm_attendance.find({"building_id": building_id}).to_list(None)
        report.agm_attendance_found = len(docs)

        held_agm_source_ids: set[str] = set()
        async for agm_doc in self._mongo_db.agm.find(
            {"building_id": building_id, "status": {"$in": ["completed", "held", "closed"]}},
            {"id": 1, "_id": 1},
        ):
            held_agm_source_ids.add(str(agm_doc.get("id") or agm_doc.get("_id")))

        for doc in docs:
            source_id = str(doc.get("_id"))
            source_agm_id = str(doc.get("agm_id") or "")
            if source_agm_id not in held_agm_source_ids:
                report.skipped_records.append(
                    SkippedRecord(
                        "agm_attendance", source_id,
                        "parent AGM has not been held yet — this is an RSVP/intent record "
                        "(status='attending'), not a post-meeting quorum-attendance record. "
                        "governance.agm_attendance is a compliance record of who actually attended.",
                    )
                )
                continue

            agm_id = agm_id_map.get(source_agm_id)
            if agm_id is None:
                report.skipped_records.append(
                    SkippedRecord("agm_attendance", source_id, f"parent agm_id {source_agm_id!r} not migrated")
                )
                continue

            # Real bug, found in a second audit pass (2026-07-20): this method previously
            # never read the record's own RSVP status at all — it wrote a "they attended"
            # compliance row for EVERY agm_attendance doc whose parent AGM was held,
            # regardless of what the individual actually said. The app's own status
            # vocabulary (backend/models/community.py's AGMAttendanceUpdate) is
            # "attending" or "apology" — an apology is explicitly a statement of NOT
            # attending. Only "attending" may become a governance.agm_attendance row;
            # anything else (including missing/unrecognised status) is skipped and
            # reported, never silently treated as attendance.
            status = str(doc.get("status") or "").strip().lower()
            if status != "attending":
                report.skipped_records.append(
                    SkippedRecord(
                        "agm_attendance", source_id,
                        f"status={doc.get('status')!r} is not an attendance record — "
                        "governance.agm_attendance must not record someone as having "
                        "attended when their own RSVP says otherwise (or is unrecognised).",
                    )
                )
                continue

            user_id = str(doc.get("user_id") or "")
            mongo_user = await self._mongo_user(user_id)
            unit_number = mongo_user.get("unit_number") if mongo_user else None
            lot_id = lot_id_by_unit_number.get(unit_number) if unit_number else None
            if lot_id is None:
                report.skipped_records.append(
                    SkippedRecord("agm_attendance", source_id, f"could not resolve lot_id for user_id={user_id!r}")
                )
                continue

            email = str((mongo_user or {}).get("email") or "").strip().lower()
            party_id = party_id_by_email.get(email)
            proxy_id = doc.get("proxy_id")
            proxy_held_by_id = None
            if proxy_id:
                proxy_user = await self._mongo_user(str(proxy_id))
                proxy_email = str((proxy_user or {}).get("email") or "").strip().lower()
                proxy_held_by_id = party_id_by_email.get(proxy_email)
            attendance_type = "proxy" if proxy_id else "in_person"
            recorded_at = (
                _parse_datetime(doc.get("confirmed_at"))
                or _parse_datetime(doc.get("updated_at"))
                or datetime.now(UTC)
            )

            if not apply:
                continue

            attendance_id = _stable_uuid("agm_attendance", agm_id, lot_id)
            result = await session.execute(
                text(
                    """
                    INSERT INTO governance.agm_attendance (
                        attendance_id, tenant_id, scheme_id, agm_id, lot_id, party_id,
                        attendance_type, proxy_held_by_id, recorded_at, is_test_data
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID),
                        CAST(:agm_id AS UUID), CAST(:lot_id AS UUID), CAST(:party_id AS UUID),
                        :attendance_type, CAST(:proxy_held_by_id AS UUID), :recorded_at, FALSE
                    )
                    ON CONFLICT (agm_id, lot_id) DO UPDATE SET
                        attendance_type = EXCLUDED.attendance_type,
                        proxy_held_by_id = EXCLUDED.proxy_held_by_id
                    RETURNING (xmax = 0) AS inserted
                    """
                ),
                {
                    "id": attendance_id, "tenant_id": tenant_id, "scheme_id": scheme_id,
                    "agm_id": agm_id, "lot_id": lot_id, "party_id": party_id,
                    "attendance_type": attendance_type, "proxy_held_by_id": proxy_held_by_id,
                    "recorded_at": recorded_at,
                },
            )
            if result.scalar():
                report.agm_attendance_created += 1

    # ── decisions (no Mongo source rows for East Gate as at this pass) ─────

    async def _count_decisions(self, building_id: str, report: GovernanceBootstrapReport) -> None:
        report.decisions_found = await self._mongo_db.decisions.count_documents({"building_id": building_id})
        if report.decisions_found:
            report.anomalies.append(
                "decisions: source rows exist but this script does not populate governance.decisions "
                "yet — no live Mongo document was available to confirm the field shape when this "
                "script was written. Extend _count_decisions into a real populate step once a sample "
                "record exists to verify against, rather than guessing the mapping."
            )
