#!/usr/bin/env python3
"""GAP-IDENTITY-UI-DB-001 — targeted Mongo→Postgres identity sync for building 13195.

Scope is deliberately narrow and was worked out interactively against live data,
not inferred from field names:

Part A — owner email primary/secondary swap (6 units: TH073, TH087, UA039,
UA046, UA047, UA063). `identity_mongo_pg_diff.py` reported these as mismatches,
but investigation showed the real (Mongo-sourced) email is *already* present in
`core.parties.secondary_email` — a 2026-08-02 run of
`repair_east_gate_owner_emails_pg.py` refused to overwrite an existing
`primary_email`, so it demoted the real address to secondary instead of
promoting it. `primary_email` is still holding the original onboarding-seed
placeholder from `seeds/units_east_gate.py`
(`firstname.lastname@eastgateresidences.com.au`). This part only ever SWAPS
two already-known values on a party row that already carries both — it never
invents or overwrites with unverified data. If a mismatch does not fit this
exact pattern (secondary_email already equals the Mongo value), it is skipped
and reported rather than blindly resolved.

Part B — create Postgres identity (core.users + core.user_role_assignments,
plus core.user_units for occupancy roles) for exactly two of the four
Mongo-only user accounts:
  - guest@eastgate.com (role=guest, unit UA011)
  - tenant@eastgateresidences.com.au (role=tenant, unit TH077)

Explicitly EXCLUDED, by design, not oversight:
  - jessica@civium.com.au — Mongo status=merged_alias, merged into
    manager@eastgate.com per explicit user direction on 2026-07-25 ("same real
    person... becomes a login alias... rather than a separate identity").
    Creating a Postgres row would recreate the exact duplicate identity that
    merge eliminated.
  - gagneet@silverfoxtechnologies.com.au — Mongo status=archived,
    archived_reason=deleted_by_admin. A deliberately deleted admin account;
    Postgres is the promoted system of record for identity_core and should not
    resurrect it.

SAFETY
------
* Idempotent — uuid5-derived IDs, existence checks before every INSERT.
* Dry-run by default; --apply required to write.
* Building-scoped to 13195 only (this is a one-off targeted repair, not a
  general building-agnostic migration tool).
* Does not touch levies, financial records, or ownership period dates.

USAGE
-----
    cd backend && source venv/bin/activate
    python3 scripts/data_repair/sync_identity_mongo_to_pg_20260805.py          # dry-run
    python3 scripts/data_repair/sync_identity_mongo_to_pg_20260805.py --apply  # write
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from uuid import UUID, uuid5

import bcrypt
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from database import db  # noqa: E402
from db_postgres.repos.config_repo import resolve_scheme_context  # noqa: E402
from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from request_context import set_ctx_building_id  # noqa: E402

BUILDING_ID = "13195"
_SCHEME_NAMESPACE = UUID("4f2c9010-36e5-4b35-b5aa-2e80d36f9f37")

# Part B allowlist — see module docstring for why jessica/gagneet are excluded.
_ALLOWED_MONGO_ONLY_USERS = {"guest@eastgate.com", "tenant@eastgateresidences.com.au"}
_EXCLUDED_MONGO_ONLY_USERS = {
    "jessica@civium.com.au": "merged_alias into manager@eastgate.com, 2026-07-25 user direction",
    "gagneet@silverfoxtechnologies.com.au": "archived, archived_reason=deleted_by_admin",
}


def _stable_uuid(kind: str, *parts: str) -> str:
    clean = "::".join((kind, *(str(p or "").strip().lower() for p in parts)))
    return str(uuid5(_SCHEME_NAMESPACE, clean))


def _hash_initial_password(full_name: str, email: str) -> str:
    """Deterministic bcrypt hash, same convention as repair_east_gate_owner_emails_pg.py."""
    source = (full_name or "").strip() or (email.split("@", 1)[0] if email else "User")
    first = source.split()[0] if source.split() else "User"
    first = "".join(ch for ch in first if ch.isalpha()) or "User"
    password = f"{first[:1].upper()}{first[1:]}123!"
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


@dataclass
class SyncReport:
    dry_run: bool
    owner_emails_swapped: list[str] = field(default_factory=list)
    owner_emails_skipped_unexpected_pattern: list[dict] = field(default_factory=list)
    users_created: list[str] = field(default_factory=list)
    users_already_present: list[str] = field(default_factory=list)
    role_assignments_created: list[str] = field(default_factory=list)
    user_units_created: list[str] = field(default_factory=list)
    excluded_by_design: dict = field(default_factory=lambda: dict(_EXCLUDED_MONGO_ONLY_USERS))

    def to_dict(self) -> dict:
        return asdict(self)


async def _swap_owner_emails(session, scheme: dict, report: SyncReport, apply: bool) -> None:
    from scripts.audits.identity_mongo_pg_diff import _mongo_owners, _pg_owners  # noqa: E402

    mongo = await _mongo_owners(BUILDING_ID)
    pg = await _pg_owners(scheme)

    party_rows = (await session.execute(
        text("""
             SELECT COALESCE(l.unit_number, l.lot_number) AS unit_number,
                    p.party_id::text AS party_id, p.primary_email, p.secondary_email
               FROM core.ownership_periods op
               JOIN core.lots l ON l.lot_id = op.lot_id
               JOIN core.parties p ON p.party_id = op.owner_party_id
              WHERE op.scheme_id = :sid
                AND op.recorded_to IS NULL
                AND (op.valid_to IS NULL OR op.valid_to > CURRENT_DATE)
                AND op.is_primary_owner = true
             """),
        {"sid": str(scheme["scheme_id"])},
    )).mappings().all()
    party_by_unit = {r["unit_number"]: r for r in party_rows}

    for unit_number in sorted(set(mongo) & set(pg)):
        m_email = (mongo[unit_number].get("owner_email") or "").strip().lower()
        p_email = (pg[unit_number].get("owner_email") or "").strip().lower()
        if not m_email or m_email == p_email:
            continue  # no mismatch, or nothing to compare

        row = party_by_unit.get(unit_number)
        if not row:
            report.owner_emails_skipped_unexpected_pattern.append(
                {"unit_number": unit_number, "reason": "no primary-owner party row found"}
            )
            continue

        current_secondary = (row["secondary_email"] or "").strip().lower()
        if current_secondary != m_email:
            # Doesn't match the known-safe "already in secondary" pattern —
            # do not guess; report for manual review instead.
            report.owner_emails_skipped_unexpected_pattern.append(
                {
                    "unit_number": unit_number,
                    "mongo_email": m_email,
                    "pg_primary_email": row["primary_email"],
                    "pg_secondary_email": row["secondary_email"],
                    "reason": "mongo email not found in pg secondary_email",
                }
            )
            continue

        if apply:
            await session.execute(
                text("""
                     UPDATE core.parties
                        SET primary_email = :new_primary,
                            secondary_email = :new_secondary,
                            updated_at = now()
                      WHERE party_id = CAST(:party_id AS UUID)
                        AND tenant_id = CAST(:tenant_id AS UUID)
                     """),
                {
                    "new_primary": row["secondary_email"],
                    "new_secondary": row["primary_email"],
                    "party_id": row["party_id"],
                    "tenant_id": str(scheme["tenant_id"]),
                },
            )
        report.owner_emails_swapped.append(unit_number)


async def _create_allowed_mongo_only_users(session, scheme: dict, report: SyncReport, apply: bool) -> None:
    set_ctx_building_id(BUILDING_ID)
    for email in sorted(_ALLOWED_MONGO_ONLY_USERS):
        mongo_user = await db._db.users.find_one({"email": email, "building_id": BUILDING_ID})
        if not mongo_user:
            report.owner_emails_skipped_unexpected_pattern.append(
                {"email": email, "reason": "expected Mongo user not found"}
            )
            continue

        existing = (await session.execute(
            text("SELECT user_id::text FROM core.users WHERE tenant_id = :tid AND email = :email"),
            {"tid": str(scheme["tenant_id"]), "email": email},
        )).fetchone()
        if existing:
            report.users_already_present.append(email)
            continue

        full_name = mongo_user.get("full_name") or email.split("@", 1)[0]
        role = mongo_user.get("role") or "guest"
        unit_number = str(mongo_user.get("unit_number") or "").strip()
        created_at = mongo_user.get("created_at")
        valid_from = created_at.date() if isinstance(created_at, datetime) else date.today()

        user_id = _stable_uuid("user", str(scheme["tenant_id"]), email)

        if apply:
            await session.execute(
                text("""
                     INSERT INTO core.users (
                         user_id, tenant_id, email, full_name, password_hash, role,
                         effective_role, default_scheme_id, is_active, is_approved,
                         is_test_data, status
                     ) VALUES (
                         CAST(:user_id AS UUID), CAST(:tenant_id AS UUID), :email, :full_name,
                         :password_hash, CAST(:role AS core.user_role), CAST(:role AS core.user_role),
                         CAST(:scheme_id AS UUID), TRUE, TRUE, FALSE, 'active'
                     )
                     """),
                {
                    "user_id": user_id,
                    "tenant_id": str(scheme["tenant_id"]),
                    "email": email,
                    "full_name": full_name,
                    "password_hash": _hash_initial_password(full_name, email),
                    "role": role,
                    "scheme_id": str(scheme["scheme_id"]),
                },
            )
        report.users_created.append(email)

        assignment_id = _stable_uuid("role-assignment", user_id, str(scheme["scheme_id"]), role)
        if apply:
            await session.execute(
                text("""
                     INSERT INTO core.user_role_assignments (
                         assignment_id, tenant_id, user_id, scheme_id, role, is_active
                     ) VALUES (
                         CAST(:assignment_id AS UUID), CAST(:tenant_id AS UUID), CAST(:user_id AS UUID),
                         CAST(:scheme_id AS UUID), CAST(:role AS core.user_role), TRUE
                     )
                     ON CONFLICT DO NOTHING
                     """),
                {
                    "assignment_id": assignment_id,
                    "tenant_id": str(scheme["tenant_id"]),
                    "user_id": user_id,
                    "scheme_id": str(scheme["scheme_id"]),
                    "role": role,
                },
            )
        report.role_assignments_created.append(email)

        # core.user_units.relationship check constraint only allows
        # owner/tenant/family/agent — 'guest' has no occupancy relationship.
        if role == "tenant" and unit_number:
            lot_id = (await session.execute(
                text("""
                     SELECT lot_id::text FROM core.lots
                      WHERE scheme_id = :sid AND (unit_number = :num OR lot_number = :num)
                      LIMIT 1
                     """),
                {"sid": str(scheme["scheme_id"]), "num": unit_number},
            )).scalar()
            if lot_id:
                user_unit_id = _stable_uuid("user-unit", user_id, lot_id, str(valid_from))
                if apply:
                    await session.execute(
                        text("""
                             INSERT INTO core.user_units (
                                 user_unit_id, tenant_id, scheme_id, user_id, lot_id,
                                 relationship, valid_from, valid_to, is_test_data
                             ) VALUES (
                                 CAST(:user_unit_id AS UUID), CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID),
                                 CAST(:user_id AS UUID), CAST(:lot_id AS UUID), 'tenant', :valid_from, NULL, FALSE
                             )
                             ON CONFLICT DO NOTHING
                             """),
                        {
                            "user_unit_id": user_unit_id,
                            "tenant_id": str(scheme["tenant_id"]),
                            "scheme_id": str(scheme["scheme_id"]),
                            "user_id": user_id,
                            "lot_id": lot_id,
                            "valid_from": valid_from,
                        },
                    )
                report.user_units_created.append(email)


async def sync(*, apply: bool) -> SyncReport:
    scheme = await resolve_scheme_context(BUILDING_ID)
    if not scheme:
        raise RuntimeError(f"No PostgreSQL scheme found for building_id={BUILDING_ID}")

    report = SyncReport(dry_run=not apply)
    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))
        await _swap_owner_emails(session, scheme, report, apply)
        await _create_allowed_mongo_only_users(session, scheme, report, apply)
        if apply:
            await session.commit()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply the sync. Defaults to dry-run.")
    args = parser.parse_args()
    report = asyncio.run(sync(apply=args.apply))
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
