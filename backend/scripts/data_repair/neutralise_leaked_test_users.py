#!/usr/bin/env python3
"""
Neutralise (and optionally purge) leaked ``is_test_data`` accounts in core.users.

WHY THIS EXISTS
---------------
``pytest_sessionfinish`` in ``tests/backend/conftest.py`` sweeps eight ``core.*``
tables at the end of every run but has never included ``core.users``. Every test
that creates a user therefore leaks it permanently. On the production database
this accumulated to 2,155 of 2,160 rows (only 5 real users), of which 1,772 were
``role='super_admin'``, ``is_active=TRUE``, holding a password hash for the
constant ``"Test1234!"`` that is committed in
``tests/backend/test_invitation_rls_bypass.py`` — with a predictable email
pattern also public in the repo.

Neither ``core.find_user_for_auth`` nor the login route filters ``is_test_data``:
the flag is a cleanup marker, not an auth gate. So those rows were live,
authenticatable super-admin credentials.

WHAT THIS DOES
--------------
Two stages, deliberately separate, because they carry very different risk:

  --deactivate  Sets ``is_active = FALSE`` on every ``is_test_data`` row. The login
                handler rejects inactive accounts ("Account is deactivated"), so
                this closes the authentication path immediately. It touches no
                foreign keys and is fully reversible from the backup, which makes
                it the correct FIRST action.

  --purge       Deletes the rows outright. Riskier: ~30 tables reference
                ``core.users`` with ``ON DELETE NO ACTION``, so any row still
                referenced (an ops case actor, a task assignment, an audit actor)
                will refuse to delete. This stage reports those rows and leaves
                them deactivated rather than forcing anything.

Always writes a JSON backup before mutating, and refuses to run if any
``is_test_data`` row does not look like a test address — a real account wrongly
carrying the flag must be investigated, not silently deactivated.

USAGE
-----
    cd backend
    venv/bin/python3 scripts/data_repair/neutralise_leaked_test_users.py --dry-run
    venv/bin/python3 scripts/data_repair/neutralise_leaked_test_users.py --deactivate
    venv/bin/python3 scripts/data_repair/neutralise_leaked_test_users.py --purge

Run --dry-run first. It mutates nothing and prints exactly what each stage would do.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
from pathlib import Path

from dotenv import load_dotenv

# Anchored to backend/.env, not the CWD — matches seeds/navigation_configs.py.
# parents[2] is the backend dir itself (scripts/data_repair/<file>), so joining
# "backend" again pointed at a path that does not exist and silently loaded nothing.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import asyncpg  # noqa: E402

BYPASS = "00000000-0000-0000-0000-000000000000"

# RFC 2606 / RFC 6761 reserve these for documentation and testing; no real
# mailbox can exist on any of them. Widened from a bare "@test." substring on
# 2026-08-27, when three live production accounts on example.com / test.com were
# found that the old marker did not match.
TEST_ADDRESS_SQL = r"""(
        email::TEXT ~* '@(example|test|invalid|localhost)$'
     OR email::TEXT ~* '@([a-z0-9-]+\.)*(example|test|invalid|localhost)$'
     OR email::TEXT ~* '@example\.(com|net|org)$'
     OR email::TEXT ILIKE '%@test.%'
)"""

# A row flagged is_test_data whose address is not a reserved test domain is a red
# flag: either a real user was mis-flagged, or the flag is being set by something
# we do not understand. Either way, stop rather than deactivate a real account.


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        raise SystemExit("DATABASE_URL is not set (expected in backend/.env)")
    # SQLAlchemy's driver-qualified scheme is not valid for asyncpg.
    return raw.replace("postgresql+asyncpg://", "postgresql://")


async def _scope(conn) -> dict:
    q = {
        "total": "SELECT count(*) FROM core.users",
        "test": "SELECT count(*) FROM core.users WHERE is_test_data",
        "test_active": "SELECT count(*) FROM core.users WHERE is_test_data AND is_active",
        "test_super_admin_active":
            "SELECT count(*) FROM core.users WHERE is_test_data AND is_active AND role='super_admin'",
        "real": "SELECT count(*) FROM core.users WHERE NOT is_test_data",
        "real_active": "SELECT count(*) FROM core.users WHERE NOT is_test_data AND is_active",
        "stray": f"SELECT count(*) FROM core.users WHERE is_test_data AND NOT {TEST_ADDRESS_SQL}",
        # The inverse leak, and the more dangerous one: a test wrote a real row and
        # never set the flag. The conftest sweep keys off is_test_data and so does
        # the production login gate, so an unflagged test account is invisible to
        # both — it is a live credential, not clutter.
        "unflagged": f"SELECT count(*) FROM core.users WHERE NOT is_test_data AND {TEST_ADDRESS_SQL}",
        "unflagged_active": (
            f"SELECT count(*) FROM core.users WHERE NOT is_test_data AND is_active AND {TEST_ADDRESS_SQL}"
        ),
    }
    return {k: await conn.fetchval(v) for k, v in q.items()}


async def _backup(conn, where: str = "is_test_data = TRUE") -> Path:
    """Snapshot the rows a stage is about to mutate.

    The predicate is a parameter because --flag-unflagged runs BEFORE the flag is
    set: backing up "is_test_data = TRUE" there captured zero rows on 2026-08-27,
    which looked exactly like a successful backup of nothing.
    """
    rows = await conn.fetch(
        f"""SELECT user_id, tenant_id, email, role, status, is_active, is_approved,
                   password_hash, full_name, created_at
            FROM core.users WHERE {where} ORDER BY email"""
    )
    payload = [
        {k: (v if isinstance(v, (str, bool, int, type(None))) else str(v)) for k, v in dict(r).items()}
        for r in rows
    ]
    path = Path.home() / f"strataos_test_user_backup_{datetime.datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(payload, indent=1))
    path.chmod(0o600)  # contains password hashes
    print(f"  backup: {len(payload)} rows -> {path} (mode 600)")
    return path


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="report only; mutate nothing")
    g.add_argument("--deactivate", action="store_true", help="set is_active=FALSE on test rows (reversible)")
    g.add_argument("--purge", action="store_true", help="delete test rows that nothing references")
    g.add_argument("--flag-unflagged", action="store_true",
                   help="set is_test_data=TRUE on reserved-test-domain rows that lack it, "
                        "then deactivate them (they are live credentials until flagged)")
    args = ap.parse_args()

    conn = await asyncpg.connect(_dsn())
    try:
        # core.users carries an RLS bypass clause (migration 0014) under the sentinel.
        await conn.execute(f"SET app.tenant_id = '{BYPASS}'")

        s = await _scope(conn)
        print("=== scope ===")
        print(f"  core.users total .............. {s['total']}")
        print(f"  is_test_data .................. {s['test']}")
        print(f"    ...active ................... {s['test_active']}")
        print(f"    ...active super_admin ....... {s['test_super_admin_active']}")
        print(f"  real users .................... {s['real']} ({s['real_active']} active)")
        print(f"  UNFLAGGED test-domain rows .... {s['unflagged']} ({s['unflagged_active']} active)")

        if s["unflagged"] and not (args.dry_run or args.flag_unflagged):
            print("\n  NOTE: unflagged test-domain accounts exist and this stage will not touch them.\n"
                  "        Run --flag-unflagged first, or they stay live.")

        if s["stray"]:
            raise SystemExit(
                f"\nABORT: {s['stray']} is_test_data row(s) are not '{TEST_ADDRESS_MARKER}' addresses.\n"
                "A real account may be mis-flagged. Investigate before running this script."
            )

        if args.dry_run:
            print(f"\n[dry-run] --deactivate would set is_active=FALSE on {s['test_active']} row(s)")
            print(f"[dry-run] --purge would attempt to delete {s['test']} row(s)")
            print(f"[dry-run] --flag-unflagged would flag+deactivate {s['unflagged']} row(s):")
            for r in await conn.fetch(
                f"""SELECT email, full_name, role::TEXT AS role, is_active, created_at
                      FROM core.users WHERE NOT is_test_data AND {TEST_ADDRESS_SQL}
                     ORDER BY created_at DESC"""
            ):
                print(f"            {r['email']:32} {r['full_name']!r:20} role={r['role']:12} "
                      f"active={r['is_active']} created={str(r['created_at'])[:19]}")
            print("[dry-run] nothing was modified")
            return

        # Back up what THIS stage will touch, not what a different stage would.
        await _backup(
            conn,
            f"NOT is_test_data AND {TEST_ADDRESS_SQL}" if args.flag_unflagged else "is_test_data = TRUE",
        )

        if args.flag_unflagged:
            # Flag first, deactivate second, in one transaction: a flagged-but-live
            # row is still a usable credential, so the two must not be separable.
            async with conn.transaction():
                flagged = await conn.execute(
                    f"UPDATE core.users SET is_test_data = TRUE "
                    f"WHERE NOT is_test_data AND {TEST_ADDRESS_SQL}"
                )
                killed = await conn.execute(
                    "UPDATE core.users SET is_active = FALSE WHERE is_test_data = TRUE AND is_active"
                )
            print(f"  flagged: {flagged}")
            print(f"  deactivated: {killed}")

        if args.deactivate:
            async with conn.transaction():
                res = await conn.execute(
                    "UPDATE core.users SET is_active = FALSE WHERE is_test_data = TRUE AND is_active"
                )
            print(f"  deactivated: {res}")

        if args.purge:
            # ~30 tables reference core.users with ON DELETE NO ACTION. Delete row
            # by row so one referenced account cannot roll back the whole purge.
            ids = [r["user_id"] for r in await conn.fetch(
                "SELECT user_id FROM core.users WHERE is_test_data = TRUE")]
            deleted = blocked = 0
            for uid in ids:
                try:
                    async with conn.transaction():
                        await conn.execute("DELETE FROM core.users WHERE user_id = $1", uid)
                    deleted += 1
                except asyncpg.ForeignKeyViolationError:
                    # Still referenced by ops/sustainability/etc. Leave it in place —
                    # --deactivate has already removed its ability to authenticate.
                    blocked += 1
            print(f"  purged: {deleted}   still-referenced (left deactivated): {blocked}")

        print("\n=== post-state ===")
        s2 = await _scope(conn)
        print(f"  is_test_data remaining ........ {s2['test']} ({s2['test_active']} active)")
        print(f"  real users .................... {s2['real']} ({s2['real_active']} active)")
        if s2["test_active"]:
            print("  WARNING: test accounts are still active — re-run --deactivate")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
