#!/usr/bin/env python3
"""Backfill East Gate owner email identity links into PostgreSQL.

Source of truth for this repair is the existing MongoDB strata-roll import:
``units.owner_email`` and ``units.owner_email_b`` for building 13195. The script
updates matching active PostgreSQL owner parties by legal name, creates/updates
``core.users`` for those emails, and creates active ``core.user_units`` links
where the schema permits them.

It deliberately does not change lots, levies, financial records, or ownership
period dates.
"""
from __future__ import annotations

import argparse
import asyncio
import bcrypt
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import UUID, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import db  # noqa: E402
from db_postgres.repos.config_repo import resolve_scheme_context  # noqa: E402
from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from request_context import set_ctx_building_id  # noqa: E402
from sqlalchemy import text  # noqa: E402

BUILDING_ID = "13195"
_SCHEME_NAMESPACE = UUID("4f2c9010-36e5-4b35-b5aa-2e80d36f9f37")


def _stable_uuid(kind: str, *parts: str) -> str:
    """Generated function header.

    Function: _stable_uuid
    Path: backend/scripts/repair_east_gate_owner_emails_pg.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    clean = "::".join((kind, *(str(p or "").strip().lower() for p in parts)))
    return str(uuid5(_SCHEME_NAMESPACE, clean))


def _clean_email(value: object) -> str | None:
    """Generated function header.

    Function: _clean_email
    Path: backend/scripts/repair_east_gate_owner_emails_pg.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    email = str(value or "").strip().lower()
    return email if "@" in email and "." in email else None


def _clean_name(value: object, fallback: str) -> str:
    """Generated function header.

    Function: _clean_name
    Path: backend/scripts/repair_east_gate_owner_emails_pg.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(value or "").strip() or fallback


@dataclass
class RepairReport:
    dry_run: bool
    mongo_owner_email_records: int = 0
    pg_parties_updated: int = 0
    pg_users_created: int = 0
    pg_users_updated: int = 0
    password_hashes_updated: int = 0
    secondary_emails_updated: int = 0
    role_assignments_created: int = 0
    user_units_created: int = 0
    skipped_no_pg_lot: list[str] = field(default_factory=list)
    skipped_no_matching_party: list[str] = field(default_factory=list)
    skipped_conflicting_party_email: list[str] = field(default_factory=list)
    skipped_user_unit_constraint: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Generated function header.

        Function: RepairReport.to_dict
        Path: backend/scripts/repair_east_gate_owner_emails_pg.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return asdict(self)


def _initial_password(full_name: str, email: str) -> str:
    """Return the local default password: the owner's first name, capitalised,
    followed by three digits and an exclamation mark.

    NOTE (GAP-SEC-013): this scheme is predictable from the strata roll, which is
    public. It is recorded here rather than silently changed because altering it
    changes what every repaired owner's password becomes, which is an operational
    decision, not a clean-up.

    The value is immediately bcrypt-hashed before storage and is not returned
    in reports. If a name is missing, fall back to the email local-part so the
    repair remains deterministic without inventing a generic shared password.
    """
    source = (full_name or "").strip() or (email.split("@", 1)[0] if email else "Owner")
    first = source.split()[0] if source.split() else "Owner"
    first = "".join(ch for ch in first if ch.isalpha()) or "Owner"
    return f"{first[:1].upper()}{first[1:]}123!"


def _hash_initial_password(full_name: str, email: str) -> str:
    """Generated function header.

    Function: _hash_initial_password
    Path: backend/scripts/repair_east_gate_owner_emails_pg.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return bcrypt.hashpw(_initial_password(full_name, email).encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


async def _load_mongo_owner_records() -> list[dict]:
    """Generated function header.

    Function: _load_mongo_owner_records
    Path: backend/scripts/repair_east_gate_owner_emails_pg.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    set_ctx_building_id(BUILDING_ID)
    units = await db.units.find(
        {"building_id": BUILDING_ID},
        {
            "_id": 0,
            "unit_number": 1,
            "lot_number": 1,
            "owner_name": 1,
            "owner_email": 1,
            "owner_name_b": 1,
            "owner_email_b": 1,
        },
    ).to_list(None)

    records: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for unit in units:
        unit_number = str(unit.get("unit_number") or unit.get("lot_number") or "").strip()
        lot_number = str(unit.get("lot_number") or unit_number).strip()
        for name_key, email_key in (("owner_name", "owner_email"), ("owner_name_b", "owner_email_b")):
            email = _clean_email(unit.get(email_key))
            if not email:
                continue
            key = (unit_number, email)
            if key in seen:
                continue
            seen.add(key)
            records.append(
                {
                    "unit_number": unit_number,
                    "lot_number": lot_number,
                    "email": email,
                    "full_name": _clean_name(unit.get(name_key), email),
                }
            )
    return records


async def repair(*, apply: bool) -> RepairReport:
    """Generated function header.

    Function: repair
    Path: backend/scripts/repair_east_gate_owner_emails_pg.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scheme = await resolve_scheme_context(BUILDING_ID)
    if not scheme:
        raise RuntimeError("No PostgreSQL scheme found for East Gate building_id=13195")

    records = await _load_mongo_owner_records()
    report = RepairReport(dry_run=not apply, mongo_owner_email_records=len(records))

    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))
        column_result = await session.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'core'
                  AND table_name = 'parties'
                  AND column_name = 'secondary_email'
                LIMIT 1
                """
            )
        )
        has_secondary_email_column = column_result.scalar() is not None
        if apply:
            await session.execute(text("ALTER TABLE core.parties ADD COLUMN IF NOT EXISTS secondary_email CITEXT"))
            await session.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS parties_tenant_secondary_email_idx
                        ON core.parties (tenant_id, secondary_email)
                        WHERE secondary_email IS NOT NULL
                    """
                )
            )
            has_secondary_email_column = True
        secondary_email_select = "p.secondary_email" if has_secondary_email_column else "NULL::CITEXT"
        for record in records:
            lot_result = await session.execute(
                text(
                    """
                    SELECT lot_id::text
                    FROM core.lots
                    WHERE scheme_id = CAST(:scheme_id AS UUID)
                      AND (lot_number = :lot_number OR unit_number = :unit_number)
                    ORDER BY CASE WHEN unit_number = :unit_number THEN 0 ELSE 1 END
                    LIMIT 1
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "lot_number": record["lot_number"],
                    "unit_number": record["unit_number"],
                },
            )
            lot_id = lot_result.scalar()
            if not lot_id:
                report.skipped_no_pg_lot.append(record["unit_number"])
                continue

            owner_result = await session.execute(
                text(
                    f"""
                    SELECT p.party_id::text, p.primary_email, {secondary_email_select} AS secondary_email, op.valid_from
                    FROM core.ownership_periods op
                    JOIN core.parties p ON p.party_id = op.owner_party_id
                    WHERE op.scheme_id = CAST(:scheme_id AS UUID)
                      AND op.lot_id = CAST(:lot_id AS UUID)
                      AND op.recorded_to IS NULL
                      AND op.valid_to IS NULL
                      AND lower(p.legal_name) = lower(:full_name)
                    ORDER BY op.is_primary_owner DESC, op.valid_from DESC
                    LIMIT 1
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "lot_id": lot_id,
                    "full_name": record["full_name"],
                },
            )
            owner_row = owner_result.fetchone()
            if not owner_row:
                owner_result = await session.execute(
                    text(
                        f"""
                        SELECT p.party_id::text, p.primary_email, {secondary_email_select} AS secondary_email, op.valid_from
                        FROM core.ownership_periods op
                        JOIN core.parties p ON p.party_id = op.owner_party_id
                        WHERE op.scheme_id = CAST(:scheme_id AS UUID)
                          AND op.lot_id = CAST(:lot_id AS UUID)
                          AND op.recorded_to IS NULL
                          AND op.valid_to IS NULL
                        ORDER BY op.is_primary_owner DESC, op.valid_from DESC
                        LIMIT 1
                        """
                    ),
                    {
                        "scheme_id": str(scheme["scheme_id"]),
                        "lot_id": lot_id,
                    },
                )
                owner_row = owner_result.fetchone()
                if not owner_row:
                    report.skipped_no_matching_party.append(record["unit_number"])
                    continue

            party_id = str(owner_row.party_id)
            existing_party_email = (owner_row.primary_email or "").strip().lower()
            existing_secondary_email = (owner_row.secondary_email or "").strip().lower()
            if existing_party_email and existing_party_email != record["email"]:
                if existing_secondary_email and existing_secondary_email != record["email"]:
                    report.skipped_conflicting_party_email.append(record["unit_number"])
                    continue
                if apply and not existing_secondary_email:
                    await session.execute(
                        text(
                            """
                            UPDATE core.parties
                            SET secondary_email = :email,
                                updated_at = now()
                            WHERE party_id = CAST(:party_id AS UUID)
                              AND tenant_id = CAST(:tenant_id AS UUID)
                            """
                        ),
                        {
                            "email": record["email"],
                            "party_id": party_id,
                            "tenant_id": str(scheme["tenant_id"]),
                        },
                    )
                if not existing_secondary_email:
                    report.secondary_emails_updated += 1

            user_id = _stable_uuid("user", str(scheme["tenant_id"]), record["email"])
            if apply:
                if not existing_party_email:
                    await session.execute(
                        text(
                            """
                            UPDATE core.parties
                            SET primary_email = :email,
                                updated_at = now()
                            WHERE party_id = CAST(:party_id AS UUID)
                              AND tenant_id = CAST(:tenant_id AS UUID)
                            """
                        ),
                        {
                            "email": record["email"],
                            "party_id": party_id,
                            "tenant_id": str(scheme["tenant_id"]),
                        },
                    )
                existing_user = await session.execute(
                    text(
                        """
                        SELECT user_id::text, party_id::text AS party_id
                        FROM core.users
                        WHERE tenant_id = CAST(:tenant_id AS UUID)
                          AND email = :email
                        LIMIT 1
                        """
                    ),
                    {"tenant_id": str(scheme["tenant_id"]), "email": record["email"]},
                )
                user_row = existing_user.fetchone()
                if user_row:
                    user_id = str(user_row.user_id)
                    await session.execute(
                        text(
                            """
                            UPDATE core.users
                            SET full_name = COALESCE(NULLIF(full_name, ''), :full_name),
                                party_id = COALESCE(party_id, CAST(:party_id AS UUID)),
                                default_scheme_id = COALESCE(default_scheme_id, CAST(:scheme_id AS UUID)),
                                is_active = TRUE,
                                is_approved = TRUE,
                                updated_at = now()
                            WHERE user_id = CAST(:user_id AS UUID)
                            """
                        ),
                        {
                            "full_name": record["full_name"],
                            "party_id": party_id,
                            "scheme_id": str(scheme["scheme_id"]),
                            "user_id": user_id,
                        },
                    )
                    report.pg_users_updated += 1
                else:
                    await session.execute(
                        text(
                            """
                            INSERT INTO core.users (
                                user_id, tenant_id, party_id, email, full_name, password_hash, role,
                                effective_role, default_scheme_id, is_active, is_approved, is_test_data, status
                            ) VALUES (
                                CAST(:user_id AS UUID), CAST(:tenant_id AS UUID), CAST(:party_id AS UUID),
                                :email, :full_name, :password_hash, CAST('owner' AS core.user_role), CAST('owner' AS core.user_role),
                                CAST(:scheme_id AS UUID), TRUE, TRUE, FALSE, 'active'
                            )
                            """
                        ),
                        {
                            "user_id": user_id,
                            "tenant_id": str(scheme["tenant_id"]),
                            "party_id": party_id,
                            "email": record["email"],
                            "full_name": record["full_name"],
                            "password_hash": _hash_initial_password(record["full_name"], record["email"]),
                            "scheme_id": str(scheme["scheme_id"]),
                        },
                    )
                    report.pg_users_created += 1

                role_exists = await session.execute(
                    text(
                        """
                        SELECT 1
                        FROM core.user_role_assignments
                        WHERE tenant_id = CAST(:tenant_id AS UUID)
                          AND user_id = CAST(:user_id AS UUID)
                          AND scheme_id = CAST(:scheme_id AS UUID)
                          AND role = CAST('owner' AS core.user_role)
                          AND is_active = TRUE
                        LIMIT 1
                        """
                    ),
                    {
                        "tenant_id": str(scheme["tenant_id"]),
                        "user_id": user_id,
                        "scheme_id": str(scheme["scheme_id"]),
                    },
                )
                if not role_exists.fetchone():
                    await session.execute(
                        text(
                            """
                            INSERT INTO core.user_role_assignments (
                                assignment_id, tenant_id, user_id, scheme_id, role, is_active
                            ) VALUES (
                                CAST(:assignment_id AS UUID), CAST(:tenant_id AS UUID), CAST(:user_id AS UUID),
                                CAST(:scheme_id AS UUID), CAST('owner' AS core.user_role), TRUE
                            )
                            """
                        ),
                        {
                            "assignment_id": _stable_uuid("role-assignment", user_id, str(scheme["scheme_id"]), "owner"),
                            "tenant_id": str(scheme["tenant_id"]),
                            "user_id": user_id,
                            "scheme_id": str(scheme["scheme_id"]),
                        },
                    )
                    report.role_assignments_created += 1

                user_unit_exists = await session.execute(
                    text(
                        """
                        SELECT lot_id::text AS lot_id
                        FROM core.user_units
                        WHERE tenant_id = CAST(:tenant_id AS UUID)
                          AND scheme_id = CAST(:scheme_id AS UUID)
                          AND user_id = CAST(:user_id AS UUID)
                          AND relationship = 'owner'
                          AND valid_to IS NULL
                        LIMIT 1
                        """
                    ),
                    {
                        "tenant_id": str(scheme["tenant_id"]),
                        "scheme_id": str(scheme["scheme_id"]),
                        "user_id": user_id,
                        "lot_id": lot_id,
                    },
                )
                existing_user_unit = user_unit_exists.fetchone()
                if existing_user_unit and str(existing_user_unit.lot_id) != str(lot_id):
                    report.skipped_user_unit_constraint.append(record["unit_number"])
                elif not existing_user_unit:
                    await session.execute(
                        text(
                            """
                            INSERT INTO core.user_units (
                                user_unit_id, tenant_id, scheme_id, user_id, lot_id, party_id,
                                relationship, valid_from, valid_to, is_test_data
                            ) VALUES (
                                CAST(:user_unit_id AS UUID), CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID),
                                CAST(:user_id AS UUID), CAST(:lot_id AS UUID), CAST(:party_id AS UUID),
                                'owner', :valid_from, NULL, FALSE
                            )
                            """
                        ),
                        {
                            "user_unit_id": _stable_uuid("user-unit", user_id, lot_id, str(owner_row.valid_from)),
                            "tenant_id": str(scheme["tenant_id"]),
                            "scheme_id": str(scheme["scheme_id"]),
                            "user_id": user_id,
                            "lot_id": lot_id,
                            "party_id": party_id,
                            "valid_from": owner_row.valid_from,
                        },
                    )
                    report.user_units_created += 1

            elif not existing_party_email:
                report.pg_parties_updated += 1

            if apply and not existing_party_email:
                report.pg_parties_updated += 1

        if apply:
            empty_passwords = await session.execute(
                text(
                    """
                    SELECT user_id::text AS user_id, email, full_name
                    FROM core.users
                    WHERE COALESCE(password_hash, '') = ''
                      AND COALESCE(email, '') <> ''
                    """
                )
            )
            for row in empty_passwords.fetchall():
                await session.execute(
                    text(
                        """
                        UPDATE core.users
                        SET password_hash = :password_hash,
                            updated_at = now()
                        WHERE user_id = CAST(:user_id AS UUID)
                          AND COALESCE(password_hash, '') = ''
                        """
                    ),
                    {
                        "password_hash": _hash_initial_password(row.full_name or "", row.email or ""),
                        "user_id": str(row.user_id),
                    },
                )
                report.password_hashes_updated += 1

    return report


def main() -> int:
    """Generated function header.

    Function: main
    Path: backend/scripts/repair_east_gate_owner_emails_pg.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Backfill East Gate owner emails into PostgreSQL identity tables.")
    parser.add_argument("--apply", action="store_true", help="Apply the repair. Defaults to dry-run.")
    args = parser.parse_args()
    report = asyncio.run(repair(apply=args.apply))
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
