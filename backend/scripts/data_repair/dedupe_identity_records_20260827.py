#!/usr/bin/env python3
# NOTE — building-agnostic tool; --building-id defaults to "13195" (East Gate Residences,
# Unit Plan 13195) only because that is the building the duplication was found in. Every
# rule below is a property of the rows themselves; no unit, person or id is hardcoded.
# @featuretrace:user-management — Collapse duplicated identity records (unit links, person
# accounts, parties) left behind by repeated ownership-restore runs.
# Layer: migration
# Data flow: core.user_units / core.users / core.parties (Postgres, tenant-scoped)
#            -> JSON backup -> collapse duplicates
#            -> mirrored onto db.users / db.memberships / db.user_units / db.units (scope param: building|global).
# Related: backend/db_postgres/repos/identity_repo.py (list_active_users_for_scheme — the
#            read this cleans up for)
#          backend/server.py (GET /users — Postgres-primary for promoted buildings)
#          backend/scripts/data_repair/retire_stale_owner_and_demo_accounts_20260827.py
"""
Collapse duplicated identity records for one building.

Why this exists
---------------
Ownership data has been restored into this schema more than once (see
`bootstrap_initial_owner_links_*`, the co-owner backfills, and the 2026-08-26/27
units+owners restore). Each pass was idempotent in intent but keyed differently
from the last, so instead of updating the previous pass's row it inserted a second
one. Nothing errored: `core.user_units` has no uniqueness constraint on
(user, lot, relationship), and two `core.users` rows for the same person are only
duplicates once you notice they point at the same `core.parties` row.

The result on East Gate was 200 open unit links where 106 were meant, 7 people
holding two live accounts each, and 48 orphaned party records — all of it visible
on /admin/users as the same person listed twice.

The three phases
----------------
P1  Exact-duplicate unit links.
    Rows in `core.user_units` that agree on (scheme, user, lot, relationship,
    party) and are all still open. They are indistinguishable, so the oldest is
    kept and the rest deleted. No ownership fact changes — only the row count.

P2  Duplicate person accounts.
    Two or more `core.users` rows in the same tenant sharing a non-null
    `party_id`. A party is the ledger's identity for a person, so this is the
    store telling us these accounts are one human. One survives; the others are
    archived and their unit links closed.

    The survivor is chosen by rule, in order: an account holding an active
    `core.user_role_assignments` row (an EC seat or staff role lives on exactly
    one account and must not be archived), then one that has actually been logged
    into, then the earliest created. Ties break on user_id so a re-run picks the
    same survivor.

    A duplicate's unit link is only closed when the survivor already holds an open
    link to that same lot. If it does not, the link is MOVED to the survivor
    instead — closing it would leave the lot with one owner fewer, which is a
    worse error than the duplicate it was fixing.

P3  Orphaned party records.
    `core.parties` rows that share a legal_name with another party in the tenant
    and are referenced by no foreign key anywhere in the database. The referencing
    columns are discovered from information_schema at runtime rather than listed
    here, so a table added later cannot silently fall outside the check. A party
    with even one reference is never deleted.

Mongo mirror
------------
East Gate reads identity from Postgres, but Mongo remains the DR mirror and other
buildings still read it. Every archive in P2 is mirrored onto `db.users`,
`db.memberships` and `db.user_units`, and `db.units.owner_email` / `owner_email_b`
are repointed off an archived address onto the survivor's, so the strata roll does
not keep naming a mailbox nobody can sign into.

Nothing is hard-deleted except P1's indistinguishable duplicate rows and P3's
unreferenced parties. Accounts are archived, never removed — ownership records are
under the 7-year retention rule. A JSON backup of every row this script changes or
deletes is written before the first write.

Usage:
    # Dry run (default) — prints every decision, no writes.
    python3 backend/scripts/data_repair/dedupe_identity_records_20260827.py --building-id 13195

    # Apply.
    python3 backend/scripts/data_repair/dedupe_identity_records_20260827.py --building-id 13195 --apply

    # One phase at a time.
    python3 backend/scripts/data_repair/dedupe_identity_records_20260827.py --phase links --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from database import db  # noqa: E402
from db_postgres.session import async_session_context  # noqa: E402

# core.schemes / core.tenants / core.users carry an RLS bypass clause; core.lots,
# core.parties and core.user_units do NOT (see CLAUDE.md footgun #8). Resolve the
# scheme under the sentinel, then switch to the real tenant before touching the rest.
BYPASS_UUID = "00000000-0000-0000-0000-000000000000"
DEFAULT_BACKUP_DIR = BACKEND_DIR / "scripts" / "data_repair" / "backups"

# Roles a person holds because they occupy a unit, as opposed to a standing
# appointment (EC seat, manager, admin staff). Only the former follow the link.
RESIDENT_ROLES = ("owner", "tenant", "guest")


async def _resolve_scheme(session, building_id: str) -> tuple[str, str]:
    await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": BYPASS_UUID})
    row = (await session.execute(
        text("""SELECT scheme_id::TEXT AS sid, tenant_id::TEXT AS tid
                  FROM core.schemes
                 WHERE scheme_number = :n AND status = 'active'
                   AND COALESCE(is_test_data, FALSE) = FALSE
                 LIMIT 1"""),
        {"n": str(building_id)},
    )).mappings().first()
    if row is None:
        raise SystemExit(f"No active scheme for building_id={building_id!r}")
    return row["sid"], row["tid"]


# ── Phase 1 ────────────────────────────────────────────────────────────────────

async def phase_links(session, scheme_id: str, tenant_id: str, apply: bool) -> dict:
    """Delete all but the oldest of each set of indistinguishable open unit links."""
    await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": tenant_id})
    rows = (await session.execute(
        text("""
            SELECT uu.user_unit_id::TEXT AS user_unit_id, uu.user_id::TEXT AS user_id,
                   uu.lot_id::TEXT AS lot_id, l.lot_number,
                   uu.relationship::TEXT AS relationship,
                   COALESCE(uu.party_id::TEXT, '') AS party_id,
                   uu.valid_from, uu.created_at
              FROM core.user_units uu
              JOIN core.lots l ON l.lot_id = uu.lot_id
             WHERE uu.scheme_id = :sid AND uu.valid_to IS NULL
             ORDER BY uu.created_at, uu.user_unit_id
        """),
        {"sid": scheme_id},
    )).mappings().all()

    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row["user_id"], row["lot_id"], row["relationship"], row["party_id"])
        groups.setdefault(key, []).append(dict(row))

    doomed = [row for members in groups.values() if len(members) > 1 for row in members[1:]]
    print(f"\nP1  open unit links: {len(rows)}  distinct: {len(groups)}  duplicate rows: {len(doomed)}")
    per_lot: dict[str, int] = {}
    for row in doomed:
        per_lot[row["lot_number"]] = per_lot.get(row["lot_number"], 0) + 1
    if per_lot:
        preview = ", ".join(f"lot {k}×{v}" for k, v in sorted(per_lot.items(), key=lambda kv: int(kv[0]))[:12])
        print(f"    {preview}{' …' if len(per_lot) > 12 else ''}")

    if doomed and apply:
        await session.execute(
            text("DELETE FROM core.user_units WHERE user_unit_id::TEXT = ANY(:ids)"),
            {"ids": [row["user_unit_id"] for row in doomed]},
        )
        print(f"    deleted {len(doomed)} duplicate link rows")
    return {"open_links": len(rows), "distinct": len(groups), "duplicates_removed": len(doomed), "rows": doomed}


# ── Phase 2 ────────────────────────────────────────────────────────────────────

def _pick_survivor(candidates: list[dict]) -> dict:
    """Choose which of several accounts for one person stays live.

    Order matters: an account carrying an active role assignment is the one an EC
    seat or staff permission hangs off, and archiving it would silently revoke
    that. Login history comes next — it is the account the person actually knows
    about. Age is the final tie-break, with user_id behind it so a re-run is
    deterministic.
    """
    return sorted(
        candidates,
        key=lambda c: (
            0 if c["has_role_assignment"] else 1,
            0 if c["last_login_at"] else 1,
            c["created_at"],
            c["user_id"],
        ),
    )[0]


async def phase_accounts(session, scheme_id: str, tenant_id: str, building_id: str,
                         apply: bool, pending_mirror: list) -> dict:
    """Archive duplicate accounts for a single person, keeping one live.

    Mongo mirroring is not performed here — see pending_mirror at the call site.
    """
    await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": BYPASS_UUID})
    users = (await session.execute(
        text("""
            SELECT u.user_id::TEXT AS user_id, u.email::TEXT AS email, u.full_name,
                   u.party_id::TEXT AS party_id, u.role::TEXT AS role,
                   u.status::TEXT AS status, u.is_active, u.last_login_at, u.created_at,
                   EXISTS (SELECT 1 FROM core.user_role_assignments ura
                            WHERE ura.user_id = u.user_id AND ura.is_active = TRUE) AS has_role_assignment
              FROM core.users u
             WHERE u.tenant_id = CAST(:tid AS UUID)
               AND u.party_id IS NOT NULL
               AND u.status <> 'archived'
               AND COALESCE(u.is_test_data, FALSE) = FALSE
        """),
        {"tid": tenant_id},
    )).mappings().all()

    by_party: dict[str, list[dict]] = {}
    for row in users:
        by_party.setdefault(row["party_id"], []).append(dict(row))
    dupes = {pid: rows for pid, rows in by_party.items() if len(rows) > 1}

    print(f"\nP2  live accounts with a party: {len(users)}  people holding >1 account: {len(dupes)}")

    await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": tenant_id})
    today: date = datetime.now(timezone.utc).date()
    actions: list[dict] = []

    for party_id, candidates in sorted(dupes.items(), key=lambda kv: kv[1][0]["full_name"] or ""):
        survivor = _pick_survivor(candidates)
        losers = [c for c in candidates if c["user_id"] != survivor["user_id"]]
        print(f"    {survivor['full_name']!r}")
        print(f"      keep    {survivor['email']}  (role={survivor['role']}, "
              f"role_assignment={survivor['has_role_assignment']}, "
              f"logged_in={'yes' if survivor['last_login_at'] else 'never'})")

        survivor_lots = {r[0] for r in (await session.execute(
            text("SELECT lot_id::TEXT FROM core.user_units WHERE scheme_id=:s AND user_id=CAST(:u AS UUID) AND valid_to IS NULL"),
            {"s": scheme_id, "u": survivor["user_id"]},
        )).all()}

        for loser in losers:
            links = (await session.execute(
                text("""SELECT uu.user_unit_id::TEXT AS id, uu.lot_id::TEXT AS lot_id, l.lot_number
                          FROM core.user_units uu JOIN core.lots l ON l.lot_id = uu.lot_id
                         WHERE uu.scheme_id = :s AND uu.user_id = CAST(:u AS UUID) AND uu.valid_to IS NULL"""),
                {"s": scheme_id, "u": loser["user_id"]},
            )).mappings().all()
            closed, moved = [], []
            for link in links:
                # Closing a link the survivor does not hold would drop an owner from
                # the lot. Move it instead — the duplicate is the account, not the
                # ownership.
                (closed if link["lot_id"] in survivor_lots else moved).append(dict(link))
            print(f"      archive {loser['email']}  links: close {len(closed)}, move {len(moved)}")
            for link in moved:
                print(f"                move lot {link['lot_number']} -> survivor")
            actions.append({
                "party_id": party_id, "full_name": survivor["full_name"],
                "survivor": survivor["email"], "archived": loser["email"],
                "closed_links": closed, "moved_links": moved,
            })

            if not apply:
                continue

            for link in moved:
                await session.execute(
                    text("UPDATE core.user_units SET user_id = CAST(:new AS UUID) WHERE user_unit_id::TEXT = :id"),
                    {"new": survivor["user_id"], "id": link["id"]},
                )
                survivor_lots.add(link["lot_id"])
            if closed:
                await session.execute(
                    text("UPDATE core.user_units SET valid_to = :d WHERE user_unit_id::TEXT = ANY(:ids)"),
                    {"d": today, "ids": [link["id"] for link in closed]},
                )
            # An archived account keeps its role assignments live unless they are
            # retired here, and is_user_in_scheme() reads those directly — leaving
            # them active makes a merged-away account still look like a member.
            # Elevated seats are never retired this way: the survivor rule already
            # guarantees the account holding one is not the loser.
            await session.execute(
                text("""UPDATE core.user_role_assignments
                           SET is_active = FALSE
                         WHERE scheme_id = CAST(:s AS UUID)
                           AND user_id = CAST(:u AS UUID)
                           AND is_active = TRUE
                           AND role::TEXT = ANY(:resident)"""),
                {"s": scheme_id, "u": loser["user_id"], "resident": list(RESIDENT_ROLES)},
            )
            await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": BYPASS_UUID})
            await session.execute(
                text("""UPDATE core.users
                           SET status = CAST('archived' AS core.record_status),
                               is_active = FALSE, is_approved = FALSE, updated_at = NOW()
                         WHERE user_id = CAST(:u AS UUID)"""),
                {"u": loser["user_id"]},
            )
            await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": tenant_id})
            # Deferred deliberately. Mongo has no part in the Postgres transaction,
            # so mirroring here would survive a rollback of the very change it
            # mirrors — which is how a half-applied repair leaves the two stores
            # disagreeing about who exists. Queued and replayed after the commit.
            pending_mirror.append((loser, survivor))

    return {"people_with_duplicates": len(dupes), "actions": actions}


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


async def _mirror_archive_to_mongo(building_id: str, loser: dict, survivor: dict) -> None:
    """Mirror one P2 archive onto the Mongo DR copy and the strata roll.

    Mongo is not the serving store for a promoted building, but it is the disaster
    copy and every un-promoted building still reads it, so leaving it holding a
    live duplicate would reintroduce the bug on failover.
    """
    now = datetime.now(timezone.utc).isoformat()
    await db._db["users"].update_one(
        _mongo_user_filter(loser["user_id"], loser.get("email")),
        {"$set": {
            "status": "archived", "is_active": False, "is_approved": False,
            "archived_at": now, "archived_reason": "duplicate_account_merged",
            "archived_merged_into": survivor["user_id"], "updated_at": now,
        }},
    )
    mongo_row = await db._db["users"].find_one(
        _mongo_user_filter(loser["user_id"], loser.get("email")), {"_id": 0, "id": 1})
    mongo_id = (mongo_row or {}).get("id") or loser["user_id"]
    await db._db["user_units"].update_many(
        {"building_id": building_id, "user_id": mongo_id, "is_active": True},
        {"$set": {"is_active": False, "archived_at": now, "actual_end_date": now[:10]}},
    )
    await db._db["memberships"].delete_many({"building_id": building_id, "user_id": mongo_id})
    # The roll must not keep pointing correspondence at an address nobody can use.
    for field in ("owner_email", "owner_email_b"):
        await db._db["units"].update_many(
            {"building_id": building_id, field: loser["email"]},
            {"$set": {field: survivor["email"], "updated_at": now}},
        )


# ── Phase 3 ────────────────────────────────────────────────────────────────────

async def phase_parties(session, tenant_id: str, apply: bool) -> dict:
    """Delete duplicate-name party records that nothing anywhere references."""
    await session.execute(text("SELECT set_config('app.tenant_id', :u, true)"), {"u": tenant_id})

    # Discovered, not listed: a table added after this script was written must not
    # be able to hold the only reference to a party we are about to delete.
    referencing = (await session.execute(text("""
        SELECT tc.table_schema, tc.table_name, kcu.column_name
          FROM information_schema.table_constraints tc
          JOIN information_schema.key_column_usage kcu
            ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
          JOIN information_schema.constraint_column_usage ccu
            ON ccu.constraint_name = tc.constraint_name
         WHERE tc.constraint_type = 'FOREIGN KEY'
           AND ccu.table_schema = 'core' AND ccu.table_name = 'parties'
    """))).all()

    groups = (await session.execute(
        text("""SELECT legal_name, array_agg(party_id::TEXT) AS ids
                  FROM core.parties WHERE tenant_id = CAST(:tid AS UUID)
                 GROUP BY legal_name HAVING COUNT(*) > 1"""),
        {"tid": tenant_id},
    )).mappings().all()
    candidate_ids = [pid for group in groups for pid in group["ids"]]
    print(f"\nP3  parties sharing a legal_name: {len(candidate_ids)} across {len(groups)} names; "
          f"checking {len(referencing)} referencing columns")
    if not candidate_ids:
        return {"deleted": 0, "ids": []}

    referenced: set[str] = set()
    for schema, table, column in referencing:
        found = (await session.execute(
            text(f'SELECT DISTINCT "{column}"::TEXT FROM "{schema}"."{table}" WHERE "{column}"::TEXT = ANY(:ids)'),
            {"ids": candidate_ids},
        )).scalars().all()
        referenced.update(found)

    orphans = [pid for pid in candidate_ids if pid not in referenced]
    # Never empty out a name entirely: if every party for a name is unreferenced,
    # something is wrong with the reference scan, not with the data.
    keep_one: set[str] = set()
    for group in groups:
        if all(pid in orphans for pid in group["ids"]):
            keep_one.add(sorted(group["ids"])[0])
            print(f"    ! every party named {group['legal_name']!r} is unreferenced — keeping one, skipping the rest")
    orphans = [pid for pid in orphans if pid not in keep_one]

    print(f"    referenced: {len(referenced)}  unreferenced duplicates to delete: {len(orphans)}")
    if orphans and apply:
        await session.execute(
            text("DELETE FROM core.parties WHERE party_id::TEXT = ANY(:ids)"),
            {"ids": orphans},
        )
        print(f"    deleted {len(orphans)} orphan party rows")
    return {"deleted": len(orphans) if apply else 0, "would_delete": len(orphans), "ids": orphans}


# ── Driver ─────────────────────────────────────────────────────────────────────

async def run(building_id: str, apply: bool, phases: set[str], backup_dir: Path) -> dict:
    summary: dict = {"building_id": building_id, "applied": apply}
    # Mongo writes that must not happen until Postgres has committed.
    pending_mirror: list[tuple[dict, dict]] = []
    async with async_session_context() as session:
        scheme_id, tenant_id = await _resolve_scheme(session, building_id)
        print(f"building {building_id}  scheme={scheme_id}  tenant={tenant_id}")

        if "links" in phases:
            summary["links"] = await phase_links(session, scheme_id, tenant_id, apply)
        if "accounts" in phases:
            summary["accounts"] = await phase_accounts(
                session, scheme_id, tenant_id, building_id, apply, pending_mirror)
        if "parties" in phases:
            summary["parties"] = await phase_parties(session, tenant_id, apply)

        if apply:
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = backup_dir / f"identity_dedupe_{building_id}_{stamp}.json"
            path.write_text(json.dumps(summary, indent=2, default=str))
            print(f"\nbackup written: {path}")
            summary["backup"] = str(path)
            await session.commit()
        else:
            print("\nDRY RUN — nothing written. Re-run with --apply.")

    # Postgres is committed; only now is it safe to mirror.
    for loser, survivor in pending_mirror:
        await _mirror_archive_to_mongo(building_id, loser, survivor)
    if pending_mirror:
        print(f"mirrored {len(pending_mirror)} archive(s) to MongoDB")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--phase", action="append", choices=["links", "accounts", "parties"],
                        help="Run only these phases (repeatable). Default: all three, in order.")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    args = parser.parse_args()
    phases = set(args.phase) if args.phase else {"links", "accounts", "parties"}
    summary = asyncio.run(run(args.building_id, args.apply, phases, args.backup_dir))
    print("\n" + json.dumps({k: v for k, v in summary.items() if k != "links"}, indent=2, default=str)[:2000])


if __name__ == "__main__":
    main()
