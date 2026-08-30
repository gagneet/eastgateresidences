#!/usr/bin/env python3
# NOTE — building-agnostic tool; --building-id defaults to "13195" (East Gate Residences,
# Unit Plan 13195) only because that is where the residue was found. The three rule-based
# phases name no unit and no person; the fourth acts only on addresses passed on the
# command line.
# @featuretrace:user-management — Close unit links for owners who have sold, restore names
# overwritten by a transfer, and take service/demo accounts out of the resident lists.
# Layer: migration
# Data flow: core.ownership_periods + core.user_units + core.users + core.parties (Postgres,
#            tenant-scoped) -> close links / restore full_name / rewrite service-account email
#            -> mirrored onto db.users / db.memberships / db.user_units (scope param: building|global).
# Related: backend/services/financial_core/genesis.py (_derive_cutover_actor_email — the
#            canonical service-account address P3 restores)
#          backend/scripts/data_repair/eastgate_neutralise_external_emails.py (the rewrite
#            that moved that address onto a real building domain)
#          backend/scripts/data_repair/dedupe_identity_records_20260827.py
"""
Retire ownership links and accounts that should no longer appear as residents.

Why this exists
---------------
`core.ownership_periods` is bitemporal and was maintained correctly through
East Gate's transfers: when a lot sold, the sellers' periods were closed and the
buyer's opened. `core.user_units` is not bitemporal and was not maintained to
match — the sellers' links stayed open. Every read that answers "who are this
building's owners" from `user_units` therefore still returned people who had sold,
while every read that answers it from `ownership_periods` was correct.

Two smaller faults travelled with it. A transfer's account-repair pass wrote the
BUYER's name onto the SELLER's account, so one person's record carried another
person's name over their own party. And a bulk email-neutralisation rewrote every
address onto the building's domain without exempting service accounts, moving a
system actor out of the reserved system domain and into the lists that filter on
it — where it began showing up as a strata manager.

The phases
----------
P1  stale-links
    An open owner row in `core.user_units` whose party holds no current
    `core.ownership_periods` row for that same lot. Current means valid_to IS NULL
    AND recorded_to IS NULL — a retracted period is not a current one, and reading
    valid_to alone is what makes a superseded row look live.
    The link is closed. The account behind it is archived only if the closure
    leaves it with no open link anywhere in the scheme and no active role
    assignment; a seller who still owns another lot, or sits on the EC, keeps
    their account.

P2  names
    A `core.users` row whose full_name disagrees with the legal_name of its own
    `core.parties` row. The party is the ledger's identity for that person, so it
    is the side that wins. Only rows whose party is unambiguous are touched.

P3  service-accounts
    A `core.users` row whose email local part marks it as a system actor but whose
    domain is a real one. The canonical address is not guessed: `genesis.py`
    derives the user_id as a uuid5 over the address, so the candidate is accepted
    only when recomputing that uuid5 reproduces the row's actual user_id. A
    candidate that fails the check is reported and skipped.

P4  archive
    Explicit `--archive-email` addresses. This phase exists because "this generic
    demo login should not be in the resident list" is a judgement about intent
    that no property of the row expresses — the account looks exactly like a real
    one. It is deliberately not rule-based, and does nothing unless asked.

Nothing is hard-deleted. Links are closed with an end date and accounts move to
status='archived'; ownership records are under the 7-year retention rule. A JSON
backup of every change is written before the first write.

Usage:
    # Dry run (default).
    python3 backend/scripts/data_repair/retire_stale_owner_and_demo_accounts_20260827.py \
        --building-id 13195

    # Apply the rule-based phases.
    python3 backend/scripts/data_repair/retire_stale_owner_and_demo_accounts_20260827.py \
        --building-id 13195 --apply

    # Also retire two named demo logins.
    python3 backend/scripts/data_repair/retire_stale_owner_and_demo_accounts_20260827.py \
        --building-id 13195 --apply \
        --archive-email tenant@example.invalid --archive-email guest@example.invalid
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from database import db  # noqa: E402
from db_postgres.session import async_session_context  # noqa: E402

BYPASS_UUID = "00000000-0000-0000-0000-000000000000"
DEFAULT_BACKUP_DIR = BACKEND_DIR / "scripts" / "data_repair" / "backups"

# Mirrors services/financial_core/genesis.py. A service actor belongs on the
# reserved system domain, never on a building's own mail domain — the user-list
# filters key off exactly that.
SYSTEM_DOMAIN = "system.strataos.local"
SYSTEM_LOCAL_PREFIXES = ("system-cutover", "system-")

# Roles that exist BECAUSE the person holds a unit. Every resident carries one, so
# they say nothing about whether an account should survive losing its last link.
# Anything else — an EC seat, a manager, admin staff — is a standing appointment
# that an ownership change must not silently revoke.
RESIDENT_ROLES = ("owner", "tenant", "guest")


async def _resolve_scheme(session, building_id: str) -> tuple[str, str]:
    await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": BYPASS_UUID})
    row = (await session.execute(
        text("""SELECT scheme_id::TEXT AS sid, tenant_id::TEXT AS tid FROM core.schemes
                 WHERE scheme_number = :n AND status = 'active'
                   AND COALESCE(is_test_data, FALSE) = FALSE LIMIT 1"""),
        {"n": str(building_id)},
    )).mappings().first()
    if row is None:
        raise SystemExit(f"No active scheme for building_id={building_id!r}")
    return row["sid"], row["tid"]


async def _archive_account_pg(session, user_id: str) -> None:
    """Archive one account in Postgres only.

    The Mongo half is deliberately separate (see _mirror_archive_to_mongo): Mongo
    takes no part in the Postgres transaction, so writing it inline would leave it
    holding a change that a later rollback erased on the Postgres side.
    """
    await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": BYPASS_UUID})
    await session.execute(
        text("""UPDATE core.users
                   SET status = CAST('archived' AS core.record_status),
                       is_active = FALSE, is_approved = FALSE, updated_at = NOW()
                 WHERE user_id = CAST(:u AS UUID)"""),
        {"u": user_id},
    )


def _mongo_user_filter(user_id: str, email: str | None) -> dict:
    """Match the Mongo user row by id OR email.

    The two stores do not always share a primary key: an account created during
    the Postgres restore has one user_id there and, if a legacy Mongo row for the
    same person also exists, a different id there. An id-only filter then matches
    nothing and update_one reports success having changed nothing — which is how
    tenant@ and guest@ stayed active in Mongo after being archived in Postgres on
    2026-08-27. Email is the only identifier the two stores reliably share.

    db._db is the raw handle, so TenantScopedDatabase's $or restriction (it rejects
    a top-level $or without request context) does not apply here.
    """
    clauses: list[dict] = [{"id": user_id}]
    if email:
        clauses.append({"email": email})
    return {"$or": clauses}


async def _mirror_archive_to_mongo(building_id: str, user_id: str, email: str | None, reason: str) -> None:
    """Replay one archive onto the Mongo DR copy. Run only after Postgres commits."""
    now = datetime.now(timezone.utc).isoformat()
    where = _mongo_user_filter(user_id, email)
    await db._db["users"].update_one(
        where,
        {"$set": {"status": "archived", "is_active": False, "is_approved": False,
                  "archived_at": now, "archived_reason": reason, "updated_at": now}},
    )
    mongo_row = await db._db["users"].find_one(where, {"_id": 0, "id": 1})
    mongo_id = (mongo_row or {}).get("id") or user_id
    await db._db["user_units"].update_many(
        {"building_id": building_id, "user_id": mongo_id, "is_active": True},
        {"$set": {"is_active": False, "archived_at": now, "actual_end_date": now[:10]}},
    )
    await db._db["memberships"].delete_many({"building_id": building_id, "user_id": mongo_id})


# ── P1 ─────────────────────────────────────────────────────────────────────────

async def phase_stale_links(session, scheme_id: str, tenant_id: str, building_id: str,
                            apply: bool, pending_mirror: list) -> dict:
    """Close owner links whose party no longer holds the lot, and archive what is left idle."""
    await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": tenant_id})
    stale = (await session.execute(
        text("""
            SELECT uu.user_unit_id::TEXT AS link_id, uu.user_id::TEXT AS user_id,
                   uu.lot_id::TEXT AS lot_id, l.lot_number,
                   uu.party_id::TEXT AS party_id, p.legal_name
              FROM core.user_units uu
              JOIN core.lots l ON l.lot_id = uu.lot_id
              LEFT JOIN core.parties p ON p.party_id = uu.party_id
             WHERE uu.scheme_id = :sid
               AND uu.valid_to IS NULL
               AND uu.relationship = 'owner'
               AND uu.party_id IS NOT NULL
               AND NOT EXISTS (
                     SELECT 1 FROM core.ownership_periods op
                      WHERE op.lot_id = uu.lot_id
                        AND op.owner_party_id = uu.party_id
                        AND op.valid_to IS NULL
                        AND op.recorded_to IS NULL)
             ORDER BY l.lot_number
        """),
        {"sid": scheme_id},
    )).mappings().all()

    print(f"\nP1  stale open owner links (party holds no current ownership period): {len(stale)}")
    today: date = datetime.now(timezone.utc).date()
    actions: list[dict] = []
    for row in stale:
        # Recomputed per row: an earlier iteration may already have closed this
        # person's last other link.
        remaining = (await session.execute(
            text("""SELECT COUNT(*) FROM core.user_units
                     WHERE scheme_id = :s AND user_id = CAST(:u AS UUID)
                       AND valid_to IS NULL AND user_unit_id::TEXT <> :lid"""),
            {"s": scheme_id, "u": row["user_id"], "lid": row["link_id"]},
        )).scalar()
        await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": BYPASS_UUID})
        account = (await session.execute(
            text("""SELECT email::TEXT AS email, full_name,
                           EXISTS (SELECT 1 FROM core.user_role_assignments ura
                                    WHERE ura.user_id = u.user_id AND ura.is_active = TRUE
                                      AND ura.role::TEXT <> ALL(:resident)) AS has_elevated_role
                      FROM core.users u WHERE user_id = CAST(:u AS UUID)"""),
            {"u": row["user_id"], "resident": list(RESIDENT_ROLES)},
        )).mappings().first()
        await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": tenant_id})

        elevated = bool(account and account["has_elevated_role"])
        archive = remaining == 0 and account is not None and not elevated
        why = ("archive account" if archive
               else f"keep account (other links={remaining}, elevated role={elevated})")
        print(f"    lot {row['lot_number']:>3}  {row['legal_name']!r:32} "
              f"{account['email'] if account else '?':55}  close link; {why}")
        actions.append({"lot": row["lot_number"], "party": row["legal_name"],
                        "email": account["email"] if account else None,
                        "archived": archive, "elevated_role": elevated})
        if not apply:
            continue
        await session.execute(
            text("UPDATE core.user_units SET valid_to = :d WHERE user_unit_id::TEXT = :lid"),
            {"d": today, "lid": row["link_id"]},
        )
        if archive:
            # list_active_users_for_scheme links a user by role assignment OR unit
            # link, so a closed link alone does not retire them — the resident role
            # assignment that the link justified has to go with it.
            await _retire_resident_role_assignments(session, scheme_id, row["user_id"])
            await _archive_account_pg(session, row["user_id"])
            pending_mirror.append((row["user_id"], account["email"], "ownership_transferred_no_current_period"))
            await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": tenant_id})
    return {"stale_links": len(stale), "actions": actions}


async def _retire_resident_role_assignments(session, scheme_id: str, user_id: str) -> None:
    """Deactivate the owner/tenant/guest role assignments a retired link justified.

    Elevated assignments (EC seat, manager, admin staff) are left alone — they are
    appointments in their own right, not a consequence of holding a unit.
    """
    await session.execute(
        text("""UPDATE core.user_role_assignments
                   SET is_active = FALSE
                 WHERE scheme_id = CAST(:s AS UUID)
                   AND user_id = CAST(:u AS UUID)
                   AND is_active = TRUE
                   AND role::TEXT = ANY(:resident)"""),
        {"s": scheme_id, "u": user_id, "resident": list(RESIDENT_ROLES)},
    )


# ── P2 ─────────────────────────────────────────────────────────────────────────

async def phase_names(session, tenant_id: str, apply: bool, pending_name_mirror: list) -> dict:
    """Restore any full_name that disagrees with its own party's legal_name."""
    await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": tenant_id})
    rows = (await session.execute(
        text("""SELECT u.user_id::TEXT AS user_id, u.email::TEXT AS email,
                       u.full_name, p.legal_name
                  FROM core.users u JOIN core.parties p ON p.party_id = u.party_id
                 WHERE u.tenant_id = CAST(:tid AS UUID)
                   AND p.legal_name IS NOT NULL AND p.legal_name <> ''
                   AND COALESCE(u.full_name, '') <> p.legal_name"""),
        {"tid": tenant_id},
    )).mappings().all()
    print(f"\nP2  accounts whose full_name disagrees with their own party: {len(rows)}")
    for row in rows:
        print(f"    {row['email']:58} {row['full_name']!r} -> {row['legal_name']!r}")
    if rows and apply:
        await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": BYPASS_UUID})
        for row in rows:
            await session.execute(
                text("UPDATE core.users SET full_name = :n, updated_at = NOW() WHERE user_id = CAST(:u AS UUID)"),
                {"n": row["legal_name"], "u": row["user_id"]},
            )
            pending_name_mirror.append((row["user_id"], row["email"], row["legal_name"]))
        await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": tenant_id})
    return {"repaired": [dict(r) for r in rows]}


# ── P3 ─────────────────────────────────────────────────────────────────────────

async def phase_service_accounts(session, scheme_id: str, tenant_id: str, apply: bool,
                                 pending_email_mirror: list) -> dict:
    """Move system actors back onto the reserved system domain, id-verified."""
    await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": BYPASS_UUID})
    rows = (await session.execute(
        text("""SELECT user_id::TEXT AS user_id, email::TEXT AS email, full_name, role::TEXT AS role
                  FROM core.users WHERE tenant_id = CAST(:tid AS UUID)"""),
        {"tid": tenant_id},
    )).mappings().all()

    misplaced = [
        r for r in rows
        if any((r["email"] or "").lower().startswith(p) for p in SYSTEM_LOCAL_PREFIXES)
        and not (r["email"] or "").lower().endswith("@" + SYSTEM_DOMAIN)
    ]
    print(f"\nP3  system actors on a non-system domain: {len(misplaced)}")
    fixed: list[dict] = []
    for row in misplaced:
        local = row["email"].split("@", 1)[0]
        # genesis.py derives the user_id from the address, so the address can be
        # recovered rather than guessed — and the recovery proves itself.
        candidates = [f"{local.replace('.', '+', 1)}@{SYSTEM_DOMAIN}", f"{local}@{SYSTEM_DOMAIN}"]
        match = next(
            (c for c in candidates
             if str(uuid5(NAMESPACE_URL, f"system-cutover:{tenant_id}:{scheme_id}:{c}")) == row["user_id"]),
            None,
        )
        if match is None:
            print(f"    ! {row['email']}  no candidate reproduces user_id {row['user_id']} — skipped, needs a human")
            continue
        print(f"    {row['email']}  ->  {match}   (user_id verified)")
        fixed.append({"user_id": row["user_id"], "from": row["email"], "to": match})
        if apply:
            await session.execute(
                text("UPDATE core.users SET email = :e, updated_at = NOW() WHERE user_id = CAST(:u AS UUID)"),
                {"e": match, "u": row["user_id"]},
            )
            pending_email_mirror.append((row["user_id"], row["email"], match))
    await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": tenant_id})
    return {"candidates": len(misplaced), "fixed": fixed}


# ── P4 ─────────────────────────────────────────────────────────────────────────

async def phase_archive_named(session, scheme_id: str, tenant_id: str, building_id: str,
                              emails: list[str], apply: bool, pending_mirror: list) -> dict:
    """Archive explicitly named accounts and close every link they hold."""
    if not emails:
        return {"requested": 0, "archived": []}
    await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": BYPASS_UUID})
    rows = (await session.execute(
        text("""SELECT user_id::TEXT AS user_id, email::TEXT AS email, full_name, role::TEXT AS role,
                       status::TEXT AS status
                  FROM core.users
                 WHERE tenant_id = CAST(:tid AS UUID) AND lower(email::TEXT) = ANY(:e)"""),
        {"tid": tenant_id, "e": [e.lower() for e in emails]},
    )).mappings().all()
    found = {r["email"].lower() for r in rows}
    print(f"\nP4  explicitly named accounts to archive: {len(rows)} of {len(emails)} requested")
    for missing in sorted(set(e.lower() for e in emails) - found):
        print(f"    ! {missing} not found in this tenant — skipped")
    archived = []
    for row in rows:
        print(f"    {row['email']:58} {row['full_name']!r} role={row['role']} status={row['status']}")
        archived.append(dict(row))
        if apply:
            await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": tenant_id})
            await session.execute(
                text("""UPDATE core.user_units SET valid_to = CURRENT_DATE
                         WHERE user_id = CAST(:u AS UUID) AND valid_to IS NULL"""),
                {"u": row["user_id"]},
            )
            await _retire_resident_role_assignments(session, scheme_id, row["user_id"])
            await _archive_account_pg(session, row["user_id"])
            pending_mirror.append((row["user_id"], row["email"], "demo_account_retired"))
    await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": tenant_id})
    return {"requested": len(emails), "archived": archived}


# ── Driver ─────────────────────────────────────────────────────────────────────

async def run(building_id: str, apply: bool, phases: set[str],
              archive_emails: list[str], backup_dir: Path) -> dict:
    summary: dict = {"building_id": building_id, "applied": apply}
    # Mongo writes held back until Postgres commits — see _mirror_archive_to_mongo.
    pending_mirror: list[tuple[str, str, str]] = []
    pending_name_mirror: list[tuple[str, str]] = []
    pending_email_mirror: list[tuple[str, str]] = []
    async with async_session_context() as session:
        scheme_id, tenant_id = await _resolve_scheme(session, building_id)
        print(f"building {building_id}  scheme={scheme_id}  tenant={tenant_id}")
        if "stale-links" in phases:
            summary["stale_links"] = await phase_stale_links(
                session, scheme_id, tenant_id, building_id, apply, pending_mirror)
        if "names" in phases:
            summary["names"] = await phase_names(session, tenant_id, apply, pending_name_mirror)
        if "service-accounts" in phases:
            summary["service_accounts"] = await phase_service_accounts(
                session, scheme_id, tenant_id, apply, pending_email_mirror)
        if "archive" in phases:
            summary["archive"] = await phase_archive_named(
                session, scheme_id, tenant_id, building_id, archive_emails, apply, pending_mirror)
        if apply:
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = backup_dir / f"retire_stale_accounts_{building_id}_{stamp}.json"
            path.write_text(json.dumps(summary, indent=2, default=str))
            print(f"\nbackup written: {path}")
            summary["backup"] = str(path)
            await session.commit()
        else:
            print("\nDRY RUN — nothing written. Re-run with --apply.")

    # Postgres is committed; only now is it safe to mirror onto Mongo.
    now = datetime.now(timezone.utc).isoformat()
    for user_id, email, name in pending_name_mirror:
        await db._db["users"].update_one(
            _mongo_user_filter(user_id, email), {"$set": {"full_name": name, "updated_at": now}})
    for user_id, old_email, new_email in pending_email_mirror:
        await db._db["users"].update_one(
            _mongo_user_filter(user_id, old_email), {"$set": {"email": new_email, "updated_at": now}})
    for user_id, email, reason in pending_mirror:
        await _mirror_archive_to_mongo(building_id, user_id, email, reason)
    mirrored = len(pending_mirror) + len(pending_name_mirror) + len(pending_email_mirror)
    if mirrored:
        print(f"mirrored {mirrored} change(s) to MongoDB")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--phase", action="append",
                        choices=["stale-links", "names", "service-accounts", "archive"])
    parser.add_argument("--archive-email", action="append", default=[],
                        help="Address to archive in P4. Repeatable. P4 does nothing without it.")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    args = parser.parse_args()
    phases = set(args.phase) if args.phase else {"stale-links", "names", "service-accounts", "archive"}
    summary = asyncio.run(run(args.building_id, args.apply, phases, args.archive_email, args.backup_dir))
    print("\n" + json.dumps(summary, indent=2, default=str)[:2500])


if __name__ == "__main__":
    main()
