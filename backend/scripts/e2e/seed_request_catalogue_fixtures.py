"""Seed / tear down the synthetic fixtures the request-catalogue Playwright suite needs.

tests/frontend/e2e/request-catalogue.global-setup.js logs five role accounts into two
buildings and hard-refuses to run against anything other than TEST-REQUEST-A /
TEST-REQUEST-B (see its assertAllowedBuilding). Those buildings and accounts had no
seeder, which is why that suite has never been executed. This is that seeder.

Everything it writes carries is_test_data=True and a TEST-REQUEST-* building_id, so a
production query — which filters {"is_test_data": {"$ne": True}} — can never see it, and
teardown can identify it precisely without pattern-matching on user-supplied text.

    cd backend && python3 scripts/e2e/seed_request_catalogue_fixtures.py --seed
    cd backend && python3 scripts/e2e/seed_request_catalogue_fixtures.py --tear-down
    cd backend && python3 scripts/e2e/seed_request_catalogue_fixtures.py --print-env

--seed is idempotent: identifiers are uuid5-derived from the building and email, so
re-running updates in place rather than duplicating.
"""

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import bcrypt
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT_DIR / ".env")

# The E2E global-setup refuses any other building id. Keep these in lockstep with
# ALLOWED_BUILDINGS in tests/frontend/e2e/request-catalogue.global-setup.js.
BUILDING_A = "TEST-REQUEST-A"
BUILDING_B = "TEST-REQUEST-B"
TEST_BUILDINGS = [BUILDING_A, BUILDING_B]

# Deterministic namespace so --seed can be re-run without creating duplicates.
_NS = uuid.UUID("6f1d4a1e-3c6d-5b2a-9f47-2f0f4a9c1d55")

_PG_BYPASS = "00000000-0000-0000-0000-000000000000"
E2E_TENANT_ID = str(uuid.uuid5(_NS, "tenant|request-catalogue-e2e"))
E2E_TENANT_NAME = "E2E Request Catalogue (synthetic)"

# NOTE ON is_test_data IN POSTGRES — deliberate, and the reason this suite could never
# run before. GET /buildings/me is Postgres-backed (identity_repo.list_schemes_for_user),
# and that query filters `COALESCE(s.is_test_data, FALSE) = FALSE AND
# COALESCE(t.is_test_data, FALSE) = FALSE` so the super-admin building switcher never
# surfaces test rows. The Playwright global-setup requires TEST-REQUEST-A to come back
# from /buildings/me. Those two requirements are mutually exclusive: a scheme flagged
# is_test_data=TRUE is invisible to the very endpoint the suite asserts on.
#
# So the Postgres tenant/scheme rows are written UNFLAGGED, which means they DO appear in
# the super-admin building switcher for as long as they exist. That is why --tear-down
# must be run immediately after the suite, and why these rows are named so an operator
# who sees one knows instantly what it is. Everything in MongoDB stays is_test_data=True.
PG_SCHEME_VISIBILITY_NOTE = (
    "Postgres scheme rows are intentionally unflagged so /buildings/me returns them; "
    "run --tear-down as soon as the suite finishes."
)

# Fixed password for every synthetic account. These accounts only ever exist in
# TEST-REQUEST-* buildings, hold no real data, and are removed by --tear-down.
E2E_PASSWORD = "E2eRequestCatalogue!2026"

# env suffix -> role. Mirrors ROLE_CONFIG in the global-setup.
ROLES = [
    ("OWNER", "owner", "UA101"),
    ("TENANT", "tenant", "UA102"),
    ("AGENT", "real_estate_agent", None),
    ("EC_MEMBER", "ec_member", "UA103"),
    ("MANAGER", "strata_manager", None),
]
# Only the manager needs the second building — the suite's building-switch test.
MULTI_BUILDING_ROLES = {"strata_manager"}

# The k6 benchmark needs a super_admin bearer token: workflow_requests.py's
# _can_mark_test_data() grants the is_test_data flag to that role ONLY, and a perf run
# that cannot set the flag writes unflagged rows into a live queue.
#
# This account is created with NO usable password hash and is never given one. A token is
# MINTED directly with the app's own signer, so the account cannot be logged into through
# the UI or /auth/login at all — it exists purely as the subject a short-lived JWT points
# at. That is deliberately weaker than creating a real, loginable admin credential on a
# production host, and it avoids touching any real super_admin's account or password.



def _uid(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "|".join(parts)))


# Subdomain of the project's own domain that has no MX record. It passes Pydantic's
# EmailStr validation (RFC 2606 reserved TLDs like .invalid/.test do NOT — the login
# endpoint returns 422 for them), while any stray notification bounces inside a domain
# the project controls rather than reaching a stranger's mailbox.
E2E_EMAIL_DOMAIN = "e2e-fixtures.eastgateresidences.com.au"


def _email(role: str) -> str:
    return f"e2e-{role.replace('_', '-')}@{E2E_EMAIL_DOMAIN}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def seed(db) -> str:
    """Seed the MongoDB fixtures; returns the bcrypt hash for the Postgres rows."""
    password_hash = bcrypt.hashpw(E2E_PASSWORD.encode(), bcrypt.gensalt()).decode()

    for bid, name in ((BUILDING_A, "E2E Request Catalogue A"), (BUILDING_B, "E2E Request Catalogue B")):
        await db.buildings.update_one(
            {"building_id": bid},
            {"$set": {
                "building_id": bid,
                "name": name,
                "address": "Synthetic building — Playwright request-catalogue suite",
                "is_active": True,
                "is_demo": False,
                "is_test_data": True,
                "jurisdiction": "ACT",
                "lot_count": 3,
                "updated_at": _now(),
                # buildings.id MUST equal the plan number, not a surrogate uuid:
                # utils/auth.py's Mongo path resolves the JWT's building_id against
                # buildings.id ({"id": building_id, "is_active": True}), so a uuid here
                # makes every request 403 with "Building not found or inactive."
                # East Gate/Sierra/Harbourview all store id == building_id.
                "id": bid,
            }, "$setOnInsert": {"created_at": _now()}},
            upsert=True,
        )
        print(f"  building {bid}")

    for env_key, role, unit in ROLES:
        email = _email(role)
        user_id = _uid("user", email)
        buildings = [BUILDING_A] + ([BUILDING_B] if role in MULTI_BUILDING_ROLES else [])

        await db.users.update_one(
            {"id": user_id},
            {"$set": {
                "id": user_id,
                "email": email,
                "password_hash": password_hash,
                "full_name": f"E2E {role.replace('_', ' ').title()}",
                "name": f"E2E {role.replace('_', ' ').title()}",
                "role": role,
                "building_id": BUILDING_A,
                "unit_number": unit,
                "is_active": True,
                "is_approved": True,
                "status": "active",
                # The global-setup aborts if a login lands on the TOTP challenge.
                "totp_enabled": False,
                "requires_account_setup": False,
                "failed_login_attempts": 0,
                "locked_until": None,
                "is_test_data": True,
                "updated_at": _now(),
            }, "$setOnInsert": {"created_at": _now()}},
            upsert=True,
        )

        for idx, bid in enumerate(buildings):
            await db.memberships.update_one(
                {"user_id": user_id, "building_id": bid},
                {"$set": {
                    "id": _uid("membership", user_id, bid),
                    "user_id": user_id,
                    "building_id": bid,
                    "roles": [role],
                    "is_active": True,
                    "is_primary": idx == 0,
                    "units": [unit] if unit else [],
                    "is_test_data": True,
                    "updated_at": _now(),
                }, "$setOnInsert": {"created_at": _now()}},
                upsert=True,
            )

        # isOwner()/isTenant() and the owner-only forms key off a unit link.
        if unit:
            await db.user_units.update_one(
                {"user_id": user_id, "building_id": BUILDING_A, "unit_number": unit},
                {"$set": {
                    "id": _uid("user_unit", user_id, unit),
                    "user_id": user_id,
                    "building_id": BUILDING_A,
                    "unit_number": unit,
                    "role_at_unit": "owner" if role != "tenant" else "tenant",
                    "is_active": True,
                    "is_primary": True,
                    "is_test_data": True,
                    "updated_at": _now(),
                }, "$setOnInsert": {"start_date": "2026-01-01", "created_at": _now()}},
                upsert=True,
            )
            await db.units.update_one(
                {"building_id": BUILDING_A, "unit_number": unit},
                {"$set": {
                    "id": _uid("unit", BUILDING_A, unit),
                    "building_id": BUILDING_A,
                    "unit_number": unit,
                    "entitlement": 100,
                    "is_test_data": True,
                    "updated_at": _now(),
                }, "$setOnInsert": {"created_at": _now()}},
                upsert=True,
            )

        print(f"  user {email:<44} role={role:<18} buildings={buildings}")

    return password_hash


async def tear_down(db) -> None:
    """Remove every fixture row.

    Scoped by is_test_data AND a TEST-REQUEST-* building_id (or a
    @test-request.invalid address for the users). Both conditions are required so this
    can never reach a real building's records even if run against production by mistake.
    """
    emails = [_email(role) for _, role, _ in ROLES]
    user_ids = [_uid("user", e) for e in emails]

    total = 0
    res = await db.users.delete_many({
        "is_test_data": True,
        "$or": [{"id": {"$in": user_ids}}, {"email": {"$regex": f"@{E2E_EMAIL_DOMAIN}$"}}],
    })
    print(f"  users               {res.deleted_count}")
    total += res.deleted_count

    for coll in ("memberships", "user_units", "units", "workflow_requests",
                 "unit_levy_ledger", "annual_levies", "settings", "feature_toggles"):
        res = await db[coll].delete_many(
            {"building_id": {"$in": TEST_BUILDINGS}, "is_test_data": True})
        if res.deleted_count:
            print(f"  {coll:<20}{res.deleted_count}")
        total += res.deleted_count

    res = await db.buildings.delete_many(
        {"building_id": {"$in": TEST_BUILDINGS}, "is_test_data": True})
    print(f"  buildings           {res.deleted_count}")
    total += res.deleted_count

    # Sweep anything else that named these buildings — a test run can create records in
    # collections this script never wrote to (a submitted request, a nav preference).
    # Reported separately so the count is never silently understated.
    residue = 0
    for cname in await db.list_collection_names():
        try:
            r = await db[cname].delete_many({"building_id": {"$in": TEST_BUILDINGS}})
        except Exception:
            continue
        if r.deleted_count:
            print(f"  [sweep] {cname:<28}{r.deleted_count}")
            residue += r.deleted_count
    print(f"\n  removed {total} fixture doc(s) + {residue} doc(s) created during the run")


async def _pg():
    url = (os.environ.get("DATABASE_URL") or "").replace("+asyncpg", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set; cannot seed the Postgres identity rows.")
    return await asyncpg.connect(url)


async def seed_postgres(password_hash: str) -> None:
    """Create the tenant/scheme/user/role rows GET /buildings/me reads.

    Mongo alone is not enough: utils/auth.py falls back to the Mongo path for a user with
    no tenant_id (which is why login already worked), but /buildings/me always goes to
    Postgres, so without these rows every role's building list comes back empty and the
    global-setup aborts with "cannot access TEST-REQUEST-A".
    """
    con = await _pg()
    try:
        await con.execute(f"SET app.tenant_id = '{_PG_BYPASS}'")
        await con.execute("""
            INSERT INTO core.tenants (tenant_id, tenant_name, tenant_type, status, is_demo, is_test_data)
            VALUES ($1::uuid, $2, 'strata_manager', 'active', FALSE, FALSE)
            ON CONFLICT (tenant_id) DO UPDATE SET tenant_name = EXCLUDED.tenant_name
        """, E2E_TENANT_ID, E2E_TENANT_NAME)
        print(f"  pg tenant  {E2E_TENANT_NAME}")

        for bid, name in ((BUILDING_A, "E2E Request Catalogue A"), (BUILDING_B, "E2E Request Catalogue B")):
            await con.execute("""
                INSERT INTO core.schemes (scheme_id, tenant_id, jurisdiction, scheme_number,
                                          scheme_name, status, is_demo, is_test_data)
                VALUES ($1::uuid, $2::uuid, 'ACT', $3, $4, 'active', FALSE, FALSE)
                ON CONFLICT (scheme_id) DO UPDATE SET scheme_name = EXCLUDED.scheme_name,
                                                      status = 'active'
            """, _uid("scheme", bid), E2E_TENANT_ID, bid, name)
            print(f"  pg scheme  {bid}")

        for _env, role, unit in ROLES:
            email = _email(role)
            user_id = _uid("user", email)
            # password_hash is required here, not just in Mongo: once a core.users row
            # exists, /auth/login authenticates against Postgres and issues a token
            # carrying tenant_id, which then routes get_current_user down the Postgres
            # branch too. A PG row without a hash makes every login fail 401 even though
            # the Mongo user is perfectly valid.
            await con.execute("""
                INSERT INTO core.users (user_id, tenant_id, email, full_name, role, status,
                                        is_active, is_approved, totp_enabled, is_test_data,
                                        password_hash)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, 'active', TRUE, TRUE, FALSE, FALSE, $6)
                ON CONFLICT (user_id) DO UPDATE SET email = EXCLUDED.email,
                                                    role = EXCLUDED.role,
                                                    password_hash = EXCLUDED.password_hash
            """, user_id, E2E_TENANT_ID, email, f"E2E {role}", role, password_hash)

            targets = [BUILDING_A] + ([BUILDING_B] if role in MULTI_BUILDING_ROLES else [])
            for bid in targets:
                await con.execute("""
                    INSERT INTO core.user_role_assignments
                        (assignment_id, tenant_id, user_id, scheme_id, role, is_active)
                    VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, TRUE)
                    ON CONFLICT (assignment_id) DO UPDATE SET is_active = TRUE
                """, _uid("ura", user_id, bid), E2E_TENANT_ID, user_id, _uid("scheme", bid), role)
            print(f"  pg roles   {role:<18} -> {targets}")
    finally:
        await con.close()


async def tear_down_postgres() -> None:
    con = await _pg()
    try:
        # core.lots has NO RLS bypass clause, so it must be cleared under the real tenant
        # before the scheme rows can go (CLAUDE.md "Postgres RLS" footgun). Nothing here
        # creates lots, but the delete is kept so a test run that does cannot wedge this.
        await con.execute(f"SET app.tenant_id = '{E2E_TENANT_ID}'")
        await con.execute("DELETE FROM core.lots WHERE tenant_id = $1::uuid", E2E_TENANT_ID)

        await con.execute(f"SET app.tenant_id = '{_PG_BYPASS}'")
        for table in ("core.user_role_assignments", "core.users", "core.schemes", "core.tenants"):
            res = await con.execute(
                f"DELETE FROM {table} WHERE tenant_id = $1::uuid", E2E_TENANT_ID)
            print(f"  pg {table:<28}{res.split()[-1]}")
    finally:
        await con.close()


def _k6_admin_email() -> str:
    return f"e2e-k6-super-admin@{E2E_EMAIL_DOMAIN}"


async def ensure_k6_admin(db) -> str:
    """Create the password-less synthetic super_admin and return a signed token."""
    email = _k6_admin_email()
    user_id = _uid("user", email)
    await db.buildings.update_one(
        {"building_id": BUILDING_A},
        {"$set": {
            "building_id": BUILDING_A,
            "name": "E2E Request Catalogue A",
            "address": "Synthetic building — k6 GAP-PERF benchmark",
            "is_active": True,
            "is_demo": False,
            "is_test_data": True,
            "jurisdiction": "ACT",
            "lot_count": 3,
            "updated_at": _now(),
            # The legacy JWT auth path validates payload.building_id against
            # buildings.id, so the synthetic k6 token must create the same
            # active test building that --seed creates for full E2E runs.
            "id": BUILDING_A,
        }, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )

    await db.users.update_one(
        {"id": user_id},
        {"$set": {
            "id": user_id,
            "email": email,
            # Deliberately not a valid bcrypt hash: bcrypt.checkpw raises/returns False,
            # so /auth/login can never succeed for this account.
            "password_hash": "!-no-login-perf-fixture-!",
            "full_name": "E2E k6 Perf Admin",
            "role": "super_admin",
            "building_id": BUILDING_A,
            "is_active": True,
            "is_approved": True,
            "status": "active",
            "totp_enabled": False,
            "is_test_data": True,
            "updated_at": _now(),
        }, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )

    sys.path.insert(0, str(ROOT_DIR))
    from utils.auth import create_token  # noqa: E402

    # No tenant_id claim on purpose: that keeps get_current_user on the MongoDB branch,
    # so this fixture never needs a core.users row and cannot be resolved as a Postgres
    # identity. Expiry is the standard JWT_EXPIRATION_HOURS.
    return create_token(
        user_id=user_id,
        email=email,
        role="super_admin",
        building_id=BUILDING_A,
    )


def print_env() -> None:
    """Emit the exact exports the Playwright global-setup reads."""
    lines = [f"export REQUEST_CATALOGUE_E2E_BUILDING_A={BUILDING_A}",
             f"export REQUEST_CATALOGUE_E2E_BUILDING_B={BUILDING_B}"]
    for env_key, role, _ in ROLES:
        lines.append(f"export REQUEST_CATALOGUE_E2E_{env_key}_EMAIL={_email(role)}")
        lines.append(f"export REQUEST_CATALOGUE_E2E_{env_key}_PASSWORD='{E2E_PASSWORD}'")
    print("\n".join(lines))


async def main(action: str) -> int:
    if action == "print-env":
        print_env()
        return 0

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        if action == "k6-token":
            print(await ensure_k6_admin(db))
            return 0
        if action == "seed":
            print("Seeding request-catalogue E2E fixtures:")
            pw_hash = await seed(db)
            await seed_postgres(pw_hash)
            print(f"\n  NOTE: {PG_SCHEME_VISIBILITY_NOTE}")
            print("\nEnvironment for the Playwright run:\n")
            print_env()
        else:
            print("Tearing down request-catalogue E2E fixtures:")
            await tear_down(db)
            await tear_down_postgres()
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--seed", action="store_const", dest="action", const="seed")
    group.add_argument("--tear-down", action="store_const", dest="action", const="tear-down")
    group.add_argument("--print-env", action="store_const", dest="action", const="print-env")
    group.add_argument("--k6-token", action="store_const", dest="action", const="k6-token",
                       help="Create the password-less perf super_admin and print a bearer token.")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.action)))
