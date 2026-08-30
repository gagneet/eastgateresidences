# @featuretrace:coexistence — Shared test infrastructure for tenant isolation, dual-source parity, and phase 2 foundations.
# Layer: test
# Data flow: pytest fixtures → test modules → backend services/routers → Mongo/Postgres (building-scoped).
# Related: backend/utils/auth.py
#           backend/database.py (tenant scoping)
#           tests/backend/test_tenant_isolation_*.py
# Toggle: multiple (feature-gated by fixture)
# Tests: tests/backend/test_tenant_isolation_p0t01.py, tests/backend/test_dual_source_consistency.py

"""
conftest.py — adds the backend directory to sys.path so tests can import
backend modules (services, routers, models, etc.) regardless of where
pytest is invoked from (project root or tests/backend/ directory).

File location: tests/backend/conftest.py
Project root:  ../../  (two levels up)
Backend dir:   ../../backend/

Phase 2 Enhancement: Multi-tenant isolation and dual-source parity test fixtures.
"""
import functools
import os
from pathlib import Path
import re
import sys
import time
import uuid

import asyncio
import pytest

# tests/backend/conftest.py → up 2 levels = project root → into backend/
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, 'backend')
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

# ── Test database selection (GAP-TEST-001 step 2) ─────────────────────────────
#
# THIS BLOCK MUST STAY ABOVE `from database import db`.
#
# backend/database.py resolves DB_NAME and builds its client at IMPORT time
# (`_db = _client[os.environ['DB_NAME']]`), so the only moment this can be
# redirected is before that import — which the next line performs. Anything that
# sets DB_NAME later is writing to a client that already exists; that is why the
# three `os.environ.setdefault("DB_NAME", "strataos_test")` lines that used to sit
# at the top of individual test modules never did anything.
#
# It works because load_dotenv() defaults to override=False, so backend/.env cannot
# put strataos_production back over a value already in the environment.
#
# WHY: with no override, a test that reaches a real write path — rather than
# patching the writer — writes to the live database. A MONGO_WRITE_AUDIT=1 run on
# 2026-08-26 measured 168 tests across 49 files doing exactly that, into 12
# collections. Several (activities, audit_logs, user_notifications, workflow_runs)
# are append-only, so the production audit trail was accumulating rows that no user
# action produced. See tasks/GAP-TEST-001.
#
# The escape hatch is deliberate and narrow: RUN_INTEGRATION_TESTS drives the live
# HTTP backend on :8003, which has its own environment and is unaffected by this,
# so those suites do NOT need the hatch. USE_PRODUCTION_DB=1 exists for the rare
# case of reproducing a production-data-shaped bug in-process, and it is expected to
# be typed on the command line, never set in a file.
_TEST_DB_NAME = os.environ.get("TEST_DB_NAME", "strataos_test")
_USING_PRODUCTION_DB = os.environ.get("USE_PRODUCTION_DB") == "1"
if not _USING_PRODUCTION_DB:
    os.environ["DB_NAME"] = _TEST_DB_NAME

from database import db

# Pre-import server so its module-level `db` is bound once, before any test file can
# mutate os.environ and trigger a second client. It binds to whichever database the
# block above selected — the test database by default. (This comment used to say it
# "locks in server.db = real strata_production DB", which was accurate and was the
# problem: it locked the whole session onto production.)
import server  # noqa: F401 (imported for side-effect: caches in sys.modules)

# ── Shared live-backend fixtures (session-scoped to avoid rate-limit) ──────────
# Test files that need admin access should prefer these over their own
# module-scoped fixtures to avoid hitting the 10/minute login rate limit.

_ADMIN_EMAIL = os.environ.get("E2E_ADMIN_EMAIL", "administrator@strataos.live")

# From the environment, with NO DEFAULT.
#
# This was a literal — the live password of an active super_admin, committed here and
# in 35 other files. On 2026-08-26 the stored hash for that account was still
# byte-identical to the one seeds/super_admins.py committed, meaning it had never been
# changed in the life of the system. It has now been rotated, so the literal that used
# to sit here is worthless; the point of removing it is that the next one must not
# accumulate the same way.
#
# Empty rather than absent: only the RUN_INTEGRATION_TESTS suites need it, and they
# already skip without that flag. A missing password must not break the ~10,000 tests
# that never authenticate.
_ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "")
_BASE_URL = "http://127.0.0.1:8003/api"
_RUN_INTEGRATION = os.getenv("RUN_INTEGRATION_TESTS") == "1"

# IETF TEST-NET-1 (RFC 5737) — never assigned to a real host, safe for test fixtures.
# Used in X-Forwarded-For so test login records are distinguishable from real 127.0.0.1
# loopback entries in login_audit_logs.
_TEST_IP = "192.0.2.1"
_TEST_IP_HEADERS = {"X-Forwarded-For": _TEST_IP}

_TEST_RUN_ID = f"test_run_{uuid.uuid4().hex[:10]}"

# ── Test-session rate-limit constants ─────────────────────────────────────────
# slowapi 0.1.9 uses request.client.host (always 127.0.0.1 in tests), so all
# logins share one rate-limit bucket. We raise the limit via MongoDB BEFORE any
# test login, restoring it on session teardown.
_RATE_LIMIT_SETTING_ID = "rate_limits"
_TEST_LOGIN_RATE_LIMIT = 200  # raised during test session
_DEFAULT_LOGIN_RATE_LIMIT = 10  # restored after test session
_RATE_LIMIT_REFRESH_SECONDS = 60  # matches utils/rate_limit._REFRESH_INTERVAL_SECONDS

# Synchronous pymongo client for conftest DB operations (avoids async event-loop
# teardown issues that Motor/asyncio faces during pytest session finalisation).
import pymongo as _pymongo

_sync_mongo = _pymongo.MongoClient("mongodb://localhost:27018", serverSelectionTimeoutMS=2000)
_sync_db = _sync_mongo["strata_production"]
_SYNC_MONGO_URI = "mongodb://localhost:27018"  # canonical URI used by this test session


def _set_login_rate_limit_sync(value: int) -> None:
    """Directly update MongoDB rate-limit settings using sync pymongo.

    This bypasses the server's in-process cache; the server picks up the new
    value on its next 60-second refresh cycle OR immediately when the admin_token
    fixture also calls PUT /api/settings (which triggers refresh_rate_limit_config
    in the server process).

    When MongoDB is unavailable (e.g. pure unit-test environment), the call is
    silently skipped so that unit tests can run without a live database.
    """
    try:
        _sync_db.site_settings.update_one(
            {"id": _RATE_LIMIT_SETTING_ID},
            {"$set": {"id": _RATE_LIMIT_SETTING_ID, "rate_limit_login": value}},
            upsert=True,
        )
    except Exception:
        pass  # MongoDB unavailable in unit-test environment — skip gracefully


@pytest.fixture(scope="session", autouse=True)
def _pre_seed_test_rate_limits():
    """Raise the login rate limit in MongoDB before any test logins occur.

    slowapi 0.1.9 uses request.client.host (= 127.0.0.1 for localhost tests),
    NOT X-Forwarded-For. All logins from the test machine share one bucket.
    Running tests multiple times in a minute exhausts the 10/minute default.

    Strategy (uses the app's rate-limit controller design):
    1. Write rate_limit_login = 200 directly to MongoDB via pymongo (sync).
    2. The server's in-process rate_limit wrapper refreshes from DB every 60 s;
       if the cache is stale the new limit is picked up on the next request.
    3. The admin_token fixture ALSO calls PUT /api/settings after its first login
       to immediately update the server's in-memory state (so the second login
       — chairman — uses the 200/min limit).
    4. If 429 is still returned (cache is fresh with old limit), _login_with_retry
       waits up to 65 s for the moving-window to clear, then retries.
    5. On teardown, restore rate_limit_login = 10 via sync pymongo.
    """
    _set_login_rate_limit_sync(_TEST_LOGIN_RATE_LIMIT)
    yield  # --- test session runs ---
    _set_login_rate_limit_sync(_DEFAULT_LOGIN_RATE_LIMIT)


def _login_with_retry(email: str, password: str, *, max_wait: int = 65) -> str:
    """Attempt login, retrying with backoff until the rate-limit window resets.

    If the server's in-memory rate limit is still at 10/min (cache not yet
    refreshed from the DB update we did above), this waits up to max_wait
    seconds for the 60-second moving window to clear, then retries.
    """
    import requests as _requests
    deadline = time.time() + max_wait
    while True:
        resp = _requests.post(
            f"{_BASE_URL}/auth/login",
            json={"email": email, "password": password},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()["token"]
        if resp.status_code == 429 and time.time() < deadline:
            time.sleep(5)
            continue
        resp.raise_for_status()
    raise RuntimeError("_login_with_retry: exhausted retries")


def _slug(value: str) -> str:
    """Sanitise an arbitrary string into a safe ID component."""
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()


class CleanupRegistry:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self._filters: list[tuple[str, dict]] = []
        self._restores: list[tuple[str, str, str, dict]] = []
        # Track user IDs registered for deletion so we can cascade to user_notifications.
        # Bell notifications created by registration/approval flows reference the test
        # user as `related_id` and are sent to real admin accounts — without this cascade
        # they linger in the admin's notification feed after the test user is deleted.
        self._user_ids: set[str] = set()

    def make_id(self, suffix: str) -> str:
        return f"{self.run_id}-{_slug(suffix)}"

    def register_filter(self, collection: str, query: dict) -> None:
        if query:
            self._filters.append((collection, query))

    def register_id(self, collection: str, doc_id: str, id_field: str = "id") -> None:
        if doc_id:
            self.register_filter(collection, {id_field: doc_id})
            if collection == "users" and id_field == "id":
                self._user_ids.add(doc_id)

    def register_restore(self, collection: str, doc_id: str, original: dict, id_field: str = "id") -> None:
        if doc_id and original:
            self._restores.append((collection, id_field, doc_id, original))

    async def cleanup(self) -> None:
        for collection, id_field, doc_id, original in reversed(self._restores):
            try:
                doc = {k: v for k, v in original.items() if k != "_id"}
                await db[collection].replace_one({id_field: doc_id}, doc, upsert=True)
            except Exception:
                pass

        for collection, query in reversed(self._filters):
            try:
                await db[collection].delete_many(query)
            except Exception:
                pass

        # Cascade: remove bell notifications created as side-effects of test user activity.
        # Covers two cases:
        #   1. related_id = test user ID  (e.g. "New Registration" bell sent to admins)
        #   2. user_id    = test user ID  (e.g. notifications sent TO the test user)
        if self._user_ids:
            uid_list = list(self._user_ids)
            try:
                await db.user_notifications.delete_many({
                    "$or": [
                        {"related_id": {"$in": uid_list}},
                        {"user_id": {"$in": uid_list}},
                    ]
                })
            except Exception:
                pass


@pytest.fixture(scope="session")
def test_run_id():
    return _TEST_RUN_ID


@pytest.fixture(scope="session")
def cleanup_registry(test_run_id):
    return CleanupRegistry(test_run_id)


# ── Token minting (avoids /auth/login rate-limit during full-suite runs) ───────

import datetime as _dt
import uuid as _uuid

# Known test-user registry — used by mint_token fixture.
# Add users here as integration tests need them.
_TEST_USERS = {
    "administrator@strataos.live": {
        # UUID matches core.users (Postgres). Mongo identity collections were
        # dropped in 2026-05-02; minted JWTs now resolve through the Postgres
        # auth path only.
        "id": "d6b5c97a-bda3-4b3e-9a43-7c96add85bae",
        "role": "super_admin",
        "building_id": "13195",
    },
    "administrator@eastgateresidences.com.au": {
        # Legacy integration tests still refer to the former production admin
        # address. Keep it as a JWT alias for the current seeded super admin so
        # those tests exercise the endpoint contract instead of failing during
        # fixture setup.
        "id": "d6b5c97a-bda3-4b3e-9a43-7c96add85bae",
        "role": "super_admin",
        "building_id": "13195",
    },
    "anthony@eastgateresidences.com.au": {
        "id": "23c283c3-39c8-401c-93da-722e12f9c180",
        # 'chairman' is not a top-level role — this user is the real East Gate chairman,
        # who holds role='ec_member' with ec_position='CHAIRMAN' (see rules/post-compact-critical.md).
        "role": "ec_member",
        "building_id": "13195",
    },
    "avneet@eastgateresidences.com.au": {
        "id": "6e52ad0e-ce5a-44e0-bdb8-f75c5b234223",
        "role": "owner",
        "building_id": "13195",
    },
    "tenant@eastgateresidences.com.au": {
        "id": "6d4c9161-3572-4fb6-bab8-95621929552e",
        "role": "tenant",
        "building_id": "13195",
    },
}


def _mint_jwt(
    user_id: str,
    email: str,
    role: str,
    building_id: str = "13195",
    *,
    legacy_identity: bool = False,
) -> str:
    """
    Mint a signed JWT directly using the backend's secret key.

    Use this in integration-test fixtures instead of POST /auth/login to
    avoid exhausting the login rate-limit (10 req/min) when the full test
    suite runs.
    """
    try:
        from config import JWT_SECRET, JWT_ALGORITHM
        import jwt as _jwt
    except ImportError:
        raise RuntimeError("Cannot import backend config — is backend/ on sys.path?")
    payload = {
        "user_id": user_id,
        "email": email,
        "role": role,
        "building_id": building_id,
        "iat": _dt.datetime.now(_dt.timezone.utc),
        "jti": str(_uuid.uuid4()),
        "organisation_id": "org-silverfox-001",
        "exp": _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=2),
    }
    if not legacy_identity:
        payload["tenant_id"] = "608e19e6-6207-5c8f-b769-7b16e0b9278c"
    return _jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


# ──────────────────────────────────────────────────────────────────────────────
# Production-write recorder (GAP-TEST-001 step 1)
# ──────────────────────────────────────────────────────────────────────────────
#
# This suite has no database of its own. `backend/database.py` builds its client
# from MONGO_URL / DB_NAME at IMPORT time and conftest has already loaded
# backend/.env by then, so any test that reaches a real write path — rather than
# patching the writer — writes to strataos_production.
#
# Three instances of that have now been found, each by a different accident:
#
#   - test_report_service.py upserted maintenance_forecasts and
#     intelligence_summary for East Gate, nine times per run, because
#     generate_full_report LATE-IMPORTS its maintenance helpers so patching
#     services.report_service.db never reached them. Found only because a newly
#     added field turned up in production stamped with a test run's timestamp.
#   - Test logins wrote core.users.last_login_* once identity_core moved to
#     Postgres (GAP-SEC-012).
#   - The is_test_data sweep below failed on an FK for every run, silently.
#
# None of those was findable by reading. This makes the next one fall out of a
# normal run instead: set MONGO_WRITE_AUDIT=1 and every write is recorded with the
# collection and the test that caused it, then summarised at session end.
#
# OFF by default and deliberately so — it wraps eleven hot methods, and a permanently
# instrumented client is a cost every developer pays for a report almost nobody
# reads. On, it changes no behaviour: it records and delegates.
_WRITE_AUDIT_ENV = "MONGO_WRITE_AUDIT"

# insert/update/delete/replace, plus the two that are easy to forget because they
# read like reads. bulk_write covers the batched paths.
_AUDITED_WRITE_METHODS = (
    "insert_one", "insert_many",
    "update_one", "update_many", "replace_one",
    "delete_one", "delete_many",
    "bulk_write", "find_one_and_update", "find_one_and_replace", "find_one_and_delete",
)

# collection -> {(nodeid, method), ...}. Keyed by test so triage can start from the
# offender rather than from the collection.
_recorded_writes: dict = {}
_current_nodeid = {"id": "<session scope / fixture>"}


def pytest_configure(config):
    """Install the write audit before any test module imports backend.database.

    pytest_configure runs before collection, which is the only window that works:
    backend/database.py builds its client at import time and collection imports test
    modules, so anything later has already missed the first writes.
    """
    if _install_write_audit():
        sys.stderr.write(
            f"\n[conftest] {_WRITE_AUDIT_ENV}=1 — recording every Mongo write "
            f"against DB_NAME={os.environ.get('DB_NAME', '<unset>')}.\n"
        )


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    """Track which test is running so a recorded write can name its cause."""
    _current_nodeid["id"] = item.nodeid
    return None  # do not take over the protocol, only observe


def audit_wrap(cls, method_names=_AUDITED_WRITE_METHODS) -> int:
    """Wrap `cls`'s write methods so each call is recorded, then delegated.

    Separated from _install_write_audit and taking the class as a parameter so the
    mechanism can be tested against a stub — see tests/backend/test_write_audit.py.
    A detector nobody has watched fire is indistinguishable from a detector that
    does not work, and this one's whole value is being trusted when it says nothing.

    Idempotent: an already-wrapped method is skipped, so a re-install cannot stack
    wrappers and double-count. Returns the number of methods newly wrapped.
    """
    wrapped = 0
    for name in method_names:
        original = getattr(cls, name, None)
        if original is None or getattr(original, "_write_audited", False):
            continue

        def make(method_name, fn):
            @functools.wraps(fn)
            async def wrapper(self, *args, **kwargs):
                # Resolve the label defensively: a stub or a partially-built
                # collection may not carry both attributes, and an audit hook is
                # never allowed to be the thing that fails a test run.
                try:
                    label = f"{self.database.name}.{self.name}"
                except Exception:
                    label = f"<unknown>.{getattr(self, 'name', '?')}"
                _recorded_writes.setdefault(label, set()).add(
                    (_current_nodeid["id"], method_name)
                )
                return await fn(self, *args, **kwargs)

            wrapper._write_audited = True
            return wrapper

        setattr(cls, name, make(name, original))
        wrapped += 1
    return wrapped


def _install_write_audit() -> bool:
    """Wrap AsyncCollection's write methods. Returns False if not enabled."""
    if not os.environ.get(_WRITE_AUDIT_ENV):
        return False
    try:
        from pymongo.asynchronous.collection import AsyncCollection
    except Exception:  # pragma: no cover - pymongo absent in a pure-unit environment
        return False

    # Patch the RAW pymongo class, not TenantCollection. TenantCollection forwards
    # anything it does not define through __getattr__ straight to the raw
    # collection, so wrapping the wrapper would miss exactly the passthrough calls
    # (bulk_write, find_one_and_*) that are least likely to be mocked.
    audit_wrap(AsyncCollection)
    return True


def _report_write_audit() -> None:
    """Print what the run wrote, grouped by collection then by test."""
    if not os.environ.get(_WRITE_AUDIT_ENV):
        return
    if not _recorded_writes:
        sys.stderr.write("\n[conftest] MONGO_WRITE_AUDIT: no Mongo writes recorded.\n")
        return
    lines = [
        "",
        "[conftest] MONGO_WRITE_AUDIT — Mongo writes made by this run.",
        "  Every line below is a write against the configured database, which for a",
        "  default checkout is strataos_production. A test that is not deliberately an",
        "  integration test should appear here zero times; if it does, its writer is",
        "  reaching a real code path instead of a patched one. See GAP-TEST-001.",
        "",
    ]
    for coll in sorted(_recorded_writes):
        callers = sorted(_recorded_writes[coll])
        lines.append(f"  {coll}  ({len(callers)} call site(s))")
        for nodeid, method in callers:
            lines.append(f"      {method:22s} {nodeid}")
    lines.append("")
    sys.stderr.write("\n".join(lines))


@pytest.fixture(scope="session")
def mint_token():
    """
    Session-scoped factory fixture.  Call ``mint_token(email)`` inside a test
    or fixture to obtain a signed JWT for the given email without hitting the
    login endpoint.

    Example::
        @pytest.fixture(scope="module")
        def admin_token(mint_token):
            return mint_token("administrator@strataos.live")
    """

    def _factory(email: str, *, building_id: str | None = None, legacy_identity: bool = False) -> str:
        user = _TEST_USERS.get(email)
        if not user:
            raise KeyError(
                f"Unknown test user: {email!r}. "
                "Add to _TEST_USERS in tests/backend/conftest.py."
            )
        bid = building_id or user["building_id"]
        return _mint_jwt(user["id"], email, user["role"], bid, legacy_identity=legacy_identity)

    return _factory


@pytest.fixture(scope="session")
def require_live_identity():
    """Skip a live-data suite when the identity records it depends on are absent.

    mint_token() signs a JWT locally from _TEST_USERS without hitting /auth/login, so the
    token is well-formed even when the user no longer exists — get_current_user then looks
    the id up in the database and returns 401. That is indistinguishable from a broken
    test unless something says otherwise.

    East Gate's owner, financial and transactional records were deliberately removed on
    2026-08-21 (see docs/guides/eastgate_data_purge_and_restore_2026-08-21.md). The suites
    that log in as an East Gate owner or chairman therefore cannot pass until the backup
    is restored. Skipping with that reason keeps the coverage available for when it is,
    rather than deleting the tests or leaving a permanent red suite.

    Usage::

        @pytest.fixture(scope="module")
        def owner_token(mint_token, require_live_identity):
            return require_live_identity(mint_token("owner@example.com"))
    """
    import requests as _requests

    def _check(token: str) -> str:
        url = "http://127.0.0.1:8003/api/auth/me"
        try:
            r = _requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        except Exception as exc:  # backend not running for this local run
            pytest.skip(f"backend unreachable at {url}: {exc}")
        if r.status_code == 401:
            pytest.skip(
                "identity records for this token are absent — East Gate data was purged "
                "2026-08-21; restore via scripts/data_repair/eastgate_export_restore.py "
                "to run this live-data suite"
            )
        return token

    return _check


@pytest.fixture(scope="session", autouse=True)
def _inject_test_ip_on_login():
    """Patch requests.post + httpx.post to inject X-Forwarded-For: 192.0.2.1 on
    all /auth/login calls for the entire test session.  Without this, every test
    that logs in directly (bypassing nginx/Cloudflare) writes ip_address=127.0.0.1
    to login_audit_logs, making the Security IP Logs page look broken.
    192.0.2.1 is IETF TEST-NET-1 (RFC 5737) — never a real user IP."""
    import unittest.mock
    try:
        import requests as _requests

        _orig_requests_post = _requests.post

        def _patched_requests_post(url, *args, **kwargs):
            if "/auth/login" in str(url):
                hdrs = dict(kwargs.get("headers") or {})
                hdrs.setdefault("X-Forwarded-For", _TEST_IP)
                kwargs["headers"] = hdrs
            return _orig_requests_post(url, *args, **kwargs)

        _requests_patch = unittest.mock.patch.object(_requests, "post", side_effect=_patched_requests_post)
        _requests_patch.start()
    except ImportError:
        _requests_patch = None

    try:
        import httpx as _httpx

        _orig_httpx_post = _httpx.post

        def _patched_httpx_post(url, *args, **kwargs):
            if "/auth/login" in str(url):
                hdrs = dict(kwargs.get("headers") or {})
                hdrs.setdefault("X-Forwarded-For", _TEST_IP)
                kwargs["headers"] = hdrs
            return _orig_httpx_post(url, *args, **kwargs)

        _httpx_patch = unittest.mock.patch.object(_httpx, "post", side_effect=_patched_httpx_post)
        _httpx_patch.start()
    except ImportError:
        _httpx_patch = None

    yield

    if _requests_patch:
        _requests_patch.stop()
    if _httpx_patch:
        _httpx_patch.stop()

    # Teardown: remove test login records written to the production DB.
    # 192.0.2.1 is IETF TEST-NET-1 (RFC 5737) — never a real user IP, so
    # it is safe to delete every record bearing it only in an explicitly
    # approved integration-test/local MongoDB environment.

    def _extract_mongo_hosts(uri: str) -> list[str]:
        """Extract all hostname/IP candidates from a MongoDB URI string.

        Handles: plain hostnames, IPv4 addresses, and IPv6 addresses in bracket
        notation (e.g. [::1]:27018).
        """
        hosts = []
        # IPv6 bracket notation: [::1] or [::1]:port
        hosts.extend(re.findall(r'\[([^\]]+)\]', uri))
        # Standard hostnames and IPv4 addresses (alphanumeric, dots, hyphens)
        hosts.extend(re.findall(r'(?:@|//|,)([A-Za-z0-9._-]+)(?::\d+)?', uri))
        # Fallback: bare IPv4/hostname without a preceding @, // or ,
        hosts.extend(re.findall(r'(?<![:/])([A-Za-z0-9._-]+)(?::\d+)', uri))
        return hosts

    def _is_local_mongo_host() -> bool:
        host_candidates: list[str] = []

        # Always include the known test-session URI — it's hardcoded to localhost.
        host_candidates.extend(_extract_mongo_hosts(_SYNC_MONGO_URI))

        address = getattr(_sync_mongo, "address", None)
        if isinstance(address, tuple) and address:
            host_candidates.append(str(address[0]))

        nodes = getattr(_sync_mongo, "nodes", None)
        if nodes:
            for node in nodes:
                if isinstance(node, tuple) and node:
                    host_candidates.append(str(node[0]))

        for env_var in ("MONGO_URL", "MONGODB_URL", "MONGO_URI", "MONGODB_URI"):
            value = os.environ.get(env_var)
            if value:
                host_candidates.extend(_extract_mongo_hosts(value))

        normalized_hosts = {h.strip("[]").lower() for h in host_candidates if h}
        return any(host in {"localhost", "127.0.0.1", "::1"} for host in normalized_hosts)

    def _allow_login_audit_log_cleanup() -> bool:
        return _RUN_INTEGRATION or _is_local_mongo_host()

    if not _allow_login_audit_log_cleanup():
        raise RuntimeError(
            "Refusing to delete login_audit_logs test records because the test "
            "environment is not explicitly approved. Set RUN_INTEGRATION_TESTS=1 "
            "or run against a localhost MongoDB instance."
        )

    try:
        _sync_db.login_audit_logs.delete_many({"ip_address": _TEST_IP})
    except Exception as exc:
        # Log but do not silently swallow — surface for diagnosis.
        print(f"WARNING: login_audit_logs teardown failed: {exc}")

    _clear_test_login_residue_postgres()


def _clear_test_login_residue_postgres() -> None:
    """Clear last_login_* rows this test session stamped into Postgres.

    This teardown cleaned MongoDB only. Once identity_core was promoted to
    Postgres, test logins began writing core.users.last_login_at /
    last_login_ip / last_login_local_ip in the REAL database and nothing ever
    removed them — the dashboard's "Last login" line then showed 192.0.2.1
    (TEST-NET-1) to whoever logged in next, dated to the test run.

    Only rows whose recorded address IS the test IP are touched, so a genuine
    login is never disturbed. Scoped by _TEST_IP rather than by timestamp
    precisely so a concurrent real login cannot be caught in the sweep.
    """
    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        return

    pg_url = re.sub(r"^postgresql\+asyncpg://", "postgresql://", raw_url)

    async def _clear() -> int:
        try:
            import asyncpg  # type: ignore
        except ImportError:
            return 0
        conn = await asyncpg.connect(pg_url)
        try:
            # core.users carries an RLS bypass clause for the sentinel tenant.
            await conn.execute(
                "SET app.tenant_id = '00000000-0000-0000-0000-000000000000'"
            )
            result = await conn.execute(
                """
                UPDATE core.users
                   SET last_login_ip = NULL,
                       last_login_public_ip = NULL,
                       last_login_local_ip = NULL,
                       last_login_at = NULL
                 WHERE last_login_ip = $1
                    OR last_login_local_ip = $1::inet
                    OR last_login_public_ip = $1::inet
                """,
                _TEST_IP,
            )
            return int(result.rsplit(" ", 1)[-1]) if result else 0
        finally:
            await conn.close()

    try:
        cleared = asyncio.run(_clear())
        if cleared:
            print(f"conftest: cleared {cleared} test login row(s) from core.users")
    except Exception as exc:
        print(f"WARNING: Postgres login teardown failed: {exc}")


@pytest.fixture(scope="session", autouse=True)
def _cleanup_registry_finalizer(cleanup_registry):
    yield
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(cleanup_registry.cleanup())
        else:
            loop.run_until_complete(cleanup_registry.cleanup())
    except Exception:
        asyncio.run(cleanup_registry.cleanup())


@pytest.fixture(autouse=True, scope="function")
async def _reset_pg_engine_after_module():
    """Reset the SQLAlchemy async engine singleton after each test function.

    Starlette's TestClient (used by several test modules) runs the ASGI app in
    an anyio worker thread with its own event loop.  Any asyncpg pool connections
    acquired by route handlers during those requests are bound to that
    thread-local loop.  Once the thread terminates the loop is closed, but the
    SQLAlchemy pool still holds references to those connections.  Subsequent
    pytest-asyncio tests on the session loop then fail with:
        RuntimeError: Task ... got Future ... attached to a different loop

    Originally module-scoped, on the assumption that every TestClient()
    instantiated within a module shared one worker loop, so only cross-module
    boundaries needed a reset. That assumption broke once the FastAPI/Starlette
    dependency bump (backend-minor-patch group, 2026-08-10) changed TestClient
    to allocate a fresh worker loop per instantiation — modules that create
    more than one TestClient(app) (e.g. one per test method) now hit the same
    cross-loop error *within* a single module. Function scope resets after
    every test regardless of how many TestClient()s it created, at the cost of
    a few extra (cheap) dispose() calls per module.

    Fix: after every test's teardown, attempt a clean dispose of the pool
    (closes connections gracefully) and reset the singleton so the next test
    creates a fresh engine on the correct (session) loop.

    The dispose is wrapped in try/except because if pool connections are on a
    CLOSED loop, the dispose coroutine itself raises RuntimeError — in that case
    we still reset the singleton so the leak is bounded to abandoned connections
    (which the Postgres server will reclaim via its idle-connection timeout).
    """
    yield
    try:
        import db_postgres.engine as _pg_engine
        if _pg_engine._engine is not None:
            try:
                await _pg_engine._engine.dispose()
            except Exception:
                # Dispose fails when pool connections are on a different (closed)
                # loop — e.g., after a TestClient module.  Still reset the
                # singleton so the next module creates a clean engine.
                pass
            _pg_engine._engine = None
    except Exception:
        pass


@pytest.fixture(autouse=True, scope="function")
async def _reset_mongo_client_after_test():
    """Same cross-event-loop problem as _reset_pg_engine_after_module above, for
    database.py's module-level AsyncMongoClient/db singleton.

    routers/* do `from database import db` — a direct reference to the shared
    TenantScopedDatabase instance, not a lazy getter — so reassigning
    database.db here would not reach already-imported callers. Instead mutate
    that same object's internals (_db, _cache) in place; every module holds a
    reference to the identical object, so the mutation is visible everywhere.
    """
    yield
    try:
        import database as _database_module
        old_client = _database_module._client
        try:
            await old_client.close()
        except Exception:
            # Same rationale as the Postgres dispose() above: closing a client
            # bound to an already-dead worker-thread loop can itself raise.
            pass
        new_client = _pymongo.AsyncMongoClient(_database_module.mongo_url)
        new_db = new_client[os.environ['DB_NAME']]
        _database_module._client = new_client
        _database_module._db = new_db
        _database_module.client = new_client
        _database_module.db._db = new_db
        _database_module.db._cache = {}
    except Exception:
        pass


def pytest_collection_modifyitems(config, items):
    if _RUN_INTEGRATION:
        return

    integration_files = {
        "test_analytics_endpoints.py",
        "test_api_fixes.py",
        "test_auth_repoint.py",
        "test_blog_scraper.py",
        "test_calendar_integration.py",
        "test_capital_works_planner.py",
        "test_council_rates.py",
        "test_emergency_services.py",
        "test_external_api.py",
        "test_finance_endpoints.py",
        "test_intelligence_extensions.py",
        "test_ip_protection.py",
        "test_levy_status_carry_forward.py",
        "test_navigation.py",
        "test_owners_units_endpoint.py",
        "test_recent_fixes.py",
        "test_registration_approval_flows.py",
        "test_security.py",
        "test_sentinel_rate_limit_auth.py",
        "test_spending_categories.py",
        "test_user_registration_workflows.py",
        "test_utilities.py",
    }
    skip_marker = pytest.mark.skip(
        reason="Integration tests disabled. Set RUN_INTEGRATION_TESTS=1 to run."
    )
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(skip_marker)
            continue
        if any(name in item.nodeid for name in integration_files):
            item.add_marker(skip_marker)


@pytest.fixture(scope="session")
def admin_token(_pre_seed_test_rate_limits):
    """Session-scoped admin token — logs in ONCE for the entire test session.

    Uses the app's rate-limit controller:
    - _pre_seed_test_rate_limits raises rate_limit_login to 200 in MongoDB
    - If the server's in-memory cache is still using the old limit and rejects
      the login with 429, _login_with_retry waits up to 65 s for the window to
      reset (matching the 60-s refresh interval in utils/rate_limit.py).
    - After login, calls PUT /api/settings to bump the in-memory limit to 200
      immediately (so subsequent logins in this session use the higher limit).
    """
    import requests
    if not _RUN_INTEGRATION:
        try:
            resp = requests.post(
                f"{_BASE_URL}/auth/login",
                json={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD},
                headers=_TEST_IP_HEADERS,
                timeout=10
            )
            if resp.status_code == 200:
                return resp.json()["token"]
        except Exception:
            pass
        return None

    token = _login_with_retry(_ADMIN_EMAIL, _ADMIN_PASSWORD)

    # Use the app's settings controller to raise the in-process rate limit
    # immediately (in case the 60-s cache refresh hasn't lapsed yet).
    requests.put(
        f"{_BASE_URL}/settings",
        json={"rate_limit_login": _TEST_LOGIN_RATE_LIMIT},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    return token


@pytest.fixture(scope="session")
def admin_headers(admin_token):
    """Session-scoped Authorization headers for admin requests."""
    if admin_token:
        return {"Authorization": f"Bearer {admin_token}"}
    return {}


@pytest.fixture(scope="session")
def auth_token(admin_token):
    """Alias for admin_token — for test files that use auth_token naming."""
    return admin_token


@pytest.fixture(scope="session")
def auth_headers(admin_headers):
    """Alias for admin_headers — for test files that use auth_headers naming."""
    return admin_headers


# ──────────────────────────────────────────────────────────────────────────────
# Session-end sweeper for Postgres test data
# ──────────────────────────────────────────────────────────────────────────────
#
# Tests that go through routers/sm_organisations.py or insert directly into
# core.tenants / core.schemes pass ``is_test_data=TRUE`` (or call the routers
# with the internal ``_is_test_data=True`` kwarg). Each per-test fixture is
# expected to clean up its own rows, but if a test crashes before
# ``cleanup.append()``, gets sigkilled, or hits an FK we didn't anticipate,
# the row leaks into the live Postgres and surfaces in the SA building
# switcher on the next login.
#
# The hook below is the backstop: at the end of every pytest session it
# unconditionally TRUNCATEs every is_test_data=TRUE row in
# user_invitations / onboarding_sessions / lots / schemes / tenants.
# Skips silently when DATABASE_URL is unset (unit-only runs) or the column
# does not exist yet (pre-migration window).
def pytest_sessionfinish(session, exitstatus):
    _report_write_audit()
    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        return
    pg_url = re.sub(r"^postgresql\+asyncpg://", "postgresql://", raw_url)

    async def _sweep():
        try:
            import asyncpg  # type: ignore
        except ImportError:
            return
        try:
            conn = await asyncpg.connect(pg_url)
        except Exception:
            return
        try:
            # RLS bypass for the cleanup transaction.
            await conn.execute("SET app.tenant_id = '00000000-0000-0000-0000-000000000000'")

            # Probe whether is_test_data column exists before issuing DELETEs.
            has_col = await conn.fetchval("""
                                          SELECT EXISTS (SELECT 1
                                                         FROM information_schema.columns
                                                         WHERE table_schema = 'core'
                                                           AND table_name = 'tenants'
                                                           AND column_name = 'is_test_data')
                                          """)
            if not has_col:
                return

            # documents.documents gained its first WRITER on 2026-08-29
            # (db_postgres/repos/documents_repo.create_document, reached through
            # services/documents_store.py). CLAUDE.md's rule is that a table joins
            # this sweep in the SAME PR as its is_test_data writer — core.users was
            # absent for months and leaked 2,155 rows, 1,772 of them active
            # super_admins.
            #
            # Its RLS policy is `tenant_id = core.current_tenant_id()` with NO bypass
            # clause, so a DELETE issued under the sentinel sees zero rows and removes
            # nothing, silently — the same trap core.lots carries below. And unlike
            # core.lots the rows are NOT confined to test tenants: a test that drives a
            # production upload handler writes into whichever REAL tenant it was scoped
            # to. So iterate every tenant, not just the is_test_data ones.
            doc_table_exists = await conn.fetchval("""
                                                   SELECT EXISTS (SELECT 1
                                                                  FROM information_schema.tables
                                                                  WHERE table_schema = 'documents'
                                                                    AND table_name = 'documents')
                                                   """)
            if doc_table_exists:
                all_tenant_ids = [
                    r["tenant_id"] for r in await conn.fetch("SELECT tenant_id FROM core.tenants")
                ]
                swept_docs = 0
                for tid in all_tenant_ids:
                    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tid))
                    result = await conn.execute(
                        "DELETE FROM documents.documents WHERE is_test_data = TRUE AND tenant_id = $1",
                        tid,
                    )
                    swept_docs += int(str(result).rsplit(" ", 1)[-1] or 0)
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, false)",
                    "00000000-0000-0000-0000-000000000000",
                )
                if swept_docs:
                    print(f"conftest: swept {swept_docs} test row(s) from documents.documents")

            # FK-safe order: child tables → schemes → tenants.
            await conn.execute("""
                               DELETE
                               FROM core.user_invitations
                               WHERE tenant_id IN (SELECT tenant_id FROM core.tenants WHERE is_test_data = TRUE)
                                  OR scheme_id IN (SELECT scheme_id FROM core.schemes WHERE is_test_data = TRUE)
                               """)
            await conn.execute("""
                               DELETE
                               FROM core.onboarding_sessions
                               WHERE tenant_id IN (SELECT tenant_id FROM core.tenants WHERE is_test_data = TRUE)
                               """)
            # core.lots RLS policy `tenant_id = current_tenant_id()` has no
            # bypass clause (unlike core.schemes), so a single DELETE under
            # the bypass UUID would see zero lots and the subsequent
            # `DELETE FROM core.schemes` would fail with lots_scheme_id_fkey.
            # Switch RLS to each test tenant in turn so the lots delete is
            # actually visible.
            test_tenant_ids = [
                r["tenant_id"]
                for r in await conn.fetch(
                    "SELECT tenant_id FROM core.tenants WHERE is_test_data = TRUE"
                )
            ]
            for tid in test_tenant_ids:
                await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tid))
                await conn.execute("DELETE FROM core.lots WHERE tenant_id = $1", tid)
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, false)",
                "00000000-0000-0000-0000-000000000000",
            )
            # user_role_assignments and any other tenant-scoped tables created
            # by future tests should be added here.
            await conn.execute("""
                               DELETE
                               FROM core.user_role_assignments
                               WHERE tenant_id IN (SELECT tenant_id FROM core.tenants WHERE is_test_data = TRUE)
                               """)
            await conn.execute("DELETE FROM core.scheme_manager_appointments WHERE is_test_data = TRUE")
            await conn.execute("DELETE FROM core.scheme_management_assignments WHERE is_test_data = TRUE")
            await conn.execute("DELETE FROM core.management_entities WHERE is_test_data = TRUE")
            # Delete every scheme whose tenant is flagged is_test_data=TRUE,
            # even if the scheme itself wasn't tagged (legacy leak from prior
            # test runs that pre-date the is_test_data column on schemes).
            # Without this, tenants delete would fail with schemes_tenant_id_fkey.
            await conn.execute("""
                               DELETE
                               FROM core.schemes
                               WHERE is_test_data = TRUE
                                  OR tenant_id IN (SELECT tenant_id FROM core.tenants WHERE is_test_data = TRUE)
                               """)
            await conn.execute("DELETE FROM core.tenants WHERE is_test_data = TRUE")
            # core.users LAST: ~30 tables across ops.*/sustainability.*/modules.*
            # reference it with ON DELETE NO ACTION, so it can only go once the
            # tenant/scheme-owned rows above are gone.
            #
            # This table was missing from the sweep entirely until 2026-08-25, and
            # it is the one that matters most: nothing filters is_test_data at
            # login (neither core.find_user_for_auth nor the login route), so a
            # leaked row is a working credential, not just clutter. On production
            # the leak had reached 2,155 of 2,160 rows — 1,772 of them active
            # super_admins sharing the constant password committed in
            # test_invitation_rls_bypass.py.
            #
            # Deactivate first, then delete: a row still referenced by an ops case
            # or task assignment will refuse to delete, and deactivating means such
            # a survivor still cannot authenticate.
            await conn.execute(
                "UPDATE core.users SET is_active = FALSE WHERE is_test_data = TRUE AND is_active"
            )

            # Identity rows OWNED BY a test user, wherever they landed.
            #
            # Every child delete above is scoped to test TENANTS. A flagged user can
            # live in a REAL tenant — that is exactly what identity_repo's
            # _under_pytest() backstop produces when a test exercises a production
            # handler (routers.auth.register writes to Postgres unmocked), and what
            # neutralise_leaked_test_users.py --flag-unflagged produces when it
            # retro-flags a leak. The tenant-scoped passes miss those children
            # completely, and they then block the users delete.
            #
            # Only rows a user OWNS are removed here. core.users is referenced by 122
            # foreign keys, nearly all of them actor/audit columns (created_by,
            # approved_by, actor_user_id) whose rows are real records that must not be
            # cascaded away because a test happened to be the actor. Those are handled
            # by the per-row pass below instead: they block the delete, the row stays
            # deactivated, and it gets reported.
            for _sql in (
                "DELETE FROM core.user_sessions WHERE user_id IN (SELECT user_id FROM core.users WHERE is_test_data = TRUE)",
                "DELETE FROM core.user_email_aliases WHERE user_id IN (SELECT user_id FROM core.users WHERE is_test_data = TRUE)",
                "DELETE FROM core.user_units WHERE user_id IN (SELECT user_id FROM core.users WHERE is_test_data = TRUE)",
                # user_role_assignments references core.users TWICE (user_id and
                # granted_by); either reference blocks the delete.
                "DELETE FROM core.user_role_assignments WHERE user_id IN (SELECT user_id FROM core.users WHERE is_test_data = TRUE)"
                " OR granted_by IN (SELECT user_id FROM core.users WHERE is_test_data = TRUE)",
                # Invitations issued by, claimed by, or cancelled by a test user,
                # wherever they landed. test_invitation_rls_bypass.py deliberately
                # invites into a non-test tenant, so the tenant-scoped pass misses these.
                "DELETE FROM core.user_invitations WHERE invited_by IN (SELECT user_id FROM core.users WHERE is_test_data = TRUE)"
                " OR claimed_user_id IN (SELECT user_id FROM core.users WHERE is_test_data = TRUE)"
                " OR cancelled_by IN (SELECT user_id FROM core.users WHERE is_test_data = TRUE)",
            ):
                await conn.execute(_sql)

            # Delete one row at a time, each in its own savepoint.
            #
            # A single `DELETE FROM core.users WHERE is_test_data = TRUE` is
            # all-or-nothing: one row still referenced by an audit column raises, the
            # exception unwinds the whole `try`, and EVERY other test user survives
            # too — including ones with nothing referencing them. Reproduced
            # 2026-08-27 by planting one flagged user with a role assignment in a real
            # tenant: the sweep aborted with
            # `user_role_assignments_user_id_fkey` and left the entire test-user set
            # behind. A savepoint per row bounds the damage to the row that caused it.
            doomed = [
                r["user_id"] for r in await conn.fetch(
                    "SELECT user_id FROM core.users WHERE is_test_data = TRUE"
                )
            ]
            blocked: list[str] = []
            for _uid in doomed:
                try:
                    async with conn.transaction():
                        await conn.execute(
                            "DELETE FROM core.users WHERE user_id = $1", _uid
                        )
                except Exception:  # noqa: BLE001 — referenced by a real record; keep it
                    blocked.append(str(_uid))

            # Say what survived, and name it. Best-effort cleanup that reports nothing
            # is how a broken sweep stays broken.
            if blocked:
                detail = await conn.fetch(
                    "SELECT email::TEXT AS email, tenant_id::TEXT AS tenant_id"
                    " FROM core.users WHERE user_id::TEXT = ANY($1::TEXT[])",
                    blocked,
                )
                sys.stderr.write(
                    f"[conftest] is_test_data sweep: {len(blocked)} core.users row(s) survived "
                    f"(deactivated, so they cannot authenticate, but still present). "
                    f"Something references them:\n"
                )
                for _row in detail:
                    sys.stderr.write(
                        f"[conftest]   {_row['email']} (tenant {_row['tenant_id']})\n"
                    )
        except Exception as exc:
            # Cleanup is a best-effort safety net — never fail the whole
            # session because the sweep didn't run. The next session will
            # try again and the SA selector filter (is_test_data = FALSE)
            # already hides the rows from production callers.
            sys.stderr.write(f"[conftest] is_test_data sweep failed: {exc}\n")
        finally:
            await conn.close()

    try:
        asyncio.run(_sweep())
    except RuntimeError:
        # An event loop may already be running in some CI containers; in that
        # case fall back to ensure_future on the existing loop.
        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(_sweep())
        except Exception:
            pass


# ---------------------------------------------------------------------------
# pin_store — required by any test of a route that dispatches through the seam
# ---------------------------------------------------------------------------
# Exposed as a FIXTURE rather than an import because `tests/backend` is a package whose
# parent is not, so `from fixtures.store_pinning import pin_store` does not resolve from
# a test module without sys.path surgery in every file. A fixture is the idiomatic way
# to hand shared machinery to tests and needs no import at all.
#
# See tests/backend/fixtures/store_pinning.py for WHY every converted route needs this:
# once a route goes through store_router.read_through(), a test that patches
# `routers.<module>.db` is only half-mocked, and the failure surfaces as a wrong number
# rather than an error.
@pytest.fixture
def pin_store():
    """Return the pin_store context manager. Usage: `with pin_store("mongo"): ...`"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_store_pinning", Path(__file__).parent / "fixtures" / "store_pinning.py"
    )
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec_module: the helper defines a @dataclass, and dataclasses
    # resolve their annotations through sys.modules[cls.__module__].__dict__. Without
    # this line the class body raises AttributeError on NoneType — the same trap that
    # silently half-loaded a mapping script earlier in this work.
    sys.modules[spec.name] = module
    # No try/except: a partially-loaded helper would hand back a broken context manager
    # and every pinned test would quietly stop pinning while still passing.
    spec.loader.exec_module(module)
    return module.pin_store
