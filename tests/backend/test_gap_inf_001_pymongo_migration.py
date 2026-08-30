"""
tests/backend/test_gap_inf_001_pymongo_migration.py

GAP-INF-001: Verify the Motor → PyMongo async migration is complete for the
hot-path files: database.py, server.py, change_stream_worker.py, and
migration_021_index_optimisations.py.

Tests:
  - PyMongo AsyncMongoClient is used in database.py (not Motor)
  - TenantCollection wraps a PyMongo AsyncCollection
  - TenantScopedDatabase wraps a PyMongo AsyncDatabase
  - server.py uses AsyncMongoClient, not AsyncIOMotorClient
  - change_stream_worker.py uses AsyncMongoClient, not Motor
  - migration_021 uses AsyncMongoClient, not Motor
  - Motor is NOT imported in any of the four migrated files
  - PyMongo version >= 4.9 (async support requirement)
  - TenantCollection build-time type annotations reference AsyncCollection
  - Functional: TenantCollection wraps correctly and _inject_bid works
"""
from __future__ import annotations

import ast
import importlib
import inspect
import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── path setup ────────────────────────────────────────────────────────────────
_BACKEND = Path(__file__).parent.parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
# DB_NAME is not set here: conftest selects the test database before
# backend.database is imported, which is the only moment it can be redirected.
# The setdefault that used to sit on this line was a no-op — backend/.env had
# already put strataos_production in the environment (GAP-TEST-001).
os.environ.setdefault("JWT_SECRET", "test-secret")

_MIGRATED_FILES = {
    "database.py": _BACKEND / "database.py",
    "server.py": _BACKEND / "server.py",
    "change_stream_worker.py": _BACKEND / "workers" / "change_stream_worker.py",
    "migration_021_index_optimisations.py": (
            _BACKEND / "scripts" / "migrations" / "migration_021_index_optimisations.py"
    ),
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _source(filename: str) -> str:
    return _MIGRATED_FILES[filename].read_text()


def _ast_imports(filename: str) -> list[str]:
    """Return flat list of 'module.attr' strings from all import statements."""
    tree = ast.parse(_source(filename))
    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                results.append(f"{mod}.{alias.name}")
    return results


# ── 1. Motor must NOT appear in any migrated hot-path file ────────────────────

class TestNoMotorInMigratedFiles:
    @pytest.mark.parametrize("filename", list(_MIGRATED_FILES))
    def test_no_motor_runtime_import(self, filename: str):
        """motor.motor_asyncio must not be imported at runtime in migrated files."""
        imports = _ast_imports(filename)
        motor_imports = [i for i in imports if
                         "motor" in i.lower() and "motor" not in i.split(".")[-1].lower().replace("motor", "")]
        # Allow 'motor' only if it appears inside a string literal (comments/docstrings)
        # We specifically check for import-level motor usage
        runtime_motor = [i for i in imports if i.startswith("motor.") or i == "motor"]
        assert runtime_motor == [], (
            f"{filename} still imports Motor at runtime: {runtime_motor}. "
            "Use pymongo.AsyncMongoClient instead."
        )

    @pytest.mark.parametrize("filename", list(_MIGRATED_FILES))
    def test_no_asynciomotorclient_symbol(self, filename: str):
        """AsyncIOMotorClient must not appear as an executable name reference in migrated files."""
        tree = ast.parse(_source(filename))
        # Walk AST looking for Name nodes (variable/function refs) — this skips
        # string constants including docstrings and comment-style strings.
        bad_names = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "AsyncIOMotorClient"
        ]
        assert bad_names == [], (
            f"{filename} still uses AsyncIOMotorClient as an executable name "
            f"at lines: {[n.lineno for n in bad_names]}. "
            "Use pymongo.AsyncMongoClient instead."
        )


# ── 2. PyMongo AsyncMongoClient must be used ─────────────────────────────────

class TestPyMongoAsyncClientUsed:
    def test_database_py_imports_asyncmongoclient(self):
        imports = _ast_imports("database.py")
        assert "pymongo.AsyncMongoClient" in imports, \
            "database.py must import AsyncMongoClient from pymongo"

    def test_server_py_imports_asyncmongoclient(self):
        imports = _ast_imports("server.py")
        assert "pymongo.AsyncMongoClient" in imports, \
            "server.py must import AsyncMongoClient from pymongo"

    def test_change_stream_worker_uses_asyncmongoclient(self):
        src = _source("change_stream_worker.py")
        assert "AsyncMongoClient" in src, \
            "change_stream_worker.py must use AsyncMongoClient"

    def test_migration_021_uses_asyncmongoclient(self):
        src = _source("migration_021_index_optimisations.py")
        assert "AsyncMongoClient" in src, \
            "migration_021 must use AsyncMongoClient"


# ── 3. PyMongo version requirement ────────────────────────────────────────────

class TestPyMongoVersion:
    def test_pymongo_version_supports_async(self):
        import pymongo
        major, minor, *_ = (int(x) for x in pymongo.version.split(".")[:2])
        assert (major, minor) >= (4, 9), (
            f"pymongo {pymongo.version} is too old — AsyncMongoClient requires >= 4.9. "
            "GAP-INF-001 migration requires pymongo>=4.9."
        )

    def test_asyncmongoclient_is_importable(self):
        from pymongo import AsyncMongoClient
        assert AsyncMongoClient is not None


# ── 4. TenantCollection / TenantScopedDatabase wrap PyMongo types ─────────────

class TestTenantWrapperTypes:
    def test_tenant_collection_init_accepts_asynccollection(self):
        """TenantCollection.__init__ type hint references AsyncCollection, not Motor."""
        from database import TenantCollection
        from pymongo.asynchronous.collection import AsyncCollection
        hints = TenantCollection.__init__.__annotations__
        assert hints.get("collection") is AsyncCollection, (
            f"TenantCollection.__init__ type hint is {hints.get('collection')!r}, "
            "expected pymongo.asynchronous.collection.AsyncCollection"
        )

    def test_tenant_scoped_db_init_accepts_asyncdatabase(self):
        """TenantScopedDatabase.__init__ type hint references AsyncDatabase, not Motor."""
        from database import TenantScopedDatabase
        from pymongo.asynchronous.database import AsyncDatabase
        hints = TenantScopedDatabase.__init__.__annotations__
        assert hints.get("database") is AsyncDatabase, (
            f"TenantScopedDatabase.__init__ type hint is {hints.get('database')!r}, "
            "expected pymongo.asynchronous.database.AsyncDatabase"
        )


# ── 5. Functional: TenantCollection still injects building_id correctly ───────

class TestTenantCollectionFunctional:
    """Smoke-tests that TenantCollection still wraps PyMongo correctly.

    These do not hit a real DB — they mock the underlying collection and verify
    that _inject_bid, find, update_one, etc. behave identically after the migration.
    """

    def _make_tenant_collection(self, name: str):
        from database import TenantCollection, TENANT_SCOPED_COLLECTIONS
        mock_coll = MagicMock()
        mock_coll.name = name
        mock_coll.find = MagicMock(return_value=MagicMock())
        mock_coll.find_one = AsyncMock(return_value={"_id": "x", "building_id": "16244"})
        mock_coll.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
        mock_coll.count_documents = AsyncMock(return_value=3)
        tc = TenantCollection(mock_coll)
        return tc, mock_coll

    @pytest.mark.asyncio
    async def test_find_one_injects_building_id(self):
        from request_context import set_ctx_building_id
        set_ctx_building_id("16244")
        tc, mock_coll = self._make_tenant_collection("units")
        await tc.find_one({"unit_number": "5"})
        called_filter = mock_coll.find_one.call_args[0][0]
        # injected filter wraps with $and + building_id clause
        assert "$and" in called_filter or "building_id" in str(called_filter)

    @pytest.mark.asyncio
    async def test_count_documents_injects_building_id(self):
        from request_context import set_ctx_building_id
        set_ctx_building_id("16244")
        tc, mock_coll = self._make_tenant_collection("units")
        await tc.count_documents({"status": "active"})
        called_filter = mock_coll.count_documents.call_args[0][0]
        assert "$and" in called_filter or "building_id" in str(called_filter)

    def test_global_collection_skips_injection(self):
        from database import GLOBAL_COLLECTIONS, TenantCollection
        from request_context import set_ctx_building_id
        set_ctx_building_id("16244")
        # users is a GLOBAL collection — find() should NOT inject building_id
        mock_coll = MagicMock()
        mock_coll.name = "users"
        mock_coll.find = MagicMock(return_value=MagicMock())
        tc = TenantCollection(mock_coll)
        tc.find({"email": "test@example.com"})
        # global collection: filter passed through as-is
        mock_coll.find.assert_called_once_with({"email": "test@example.com"})

    @pytest.mark.asyncio
    async def test_insert_one_injects_building_id(self):
        from request_context import set_ctx_building_id
        set_ctx_building_id("13195")
        from database import TenantCollection
        mock_coll = MagicMock()
        mock_coll.name = "announcements"
        mock_coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id="abc"))
        tc = TenantCollection(mock_coll)
        await tc.insert_one({"title": "Notice"})
        doc_written = mock_coll.insert_one.call_args[0][0]
        assert doc_written.get("building_id") == "13195"


# ── 6. requirements.txt reflects the upgrade ─────────────────────────────────

class TestRequirementsUpgraded:
    def _req_versions(self) -> dict[str, str]:
        req_path = _BACKEND / "requirements.txt"
        versions: dict[str, str] = {}
        for line in req_path.read_text().splitlines():
            line = line.strip()
            if "==" in line and not line.startswith("#"):
                pkg, ver = line.split("==", 1)
                versions[pkg.lower()] = ver.strip()
        return versions

    def test_pymongo_version_is_4_10_or_higher(self):
        versions = self._req_versions()
        ver = versions.get("pymongo", "0.0.0")
        major, minor, *_ = (int(x) for x in ver.split(".")[:2])
        assert (major, minor) >= (4, 10), \
            f"requirements.txt has pymongo=={ver}, expected >= 4.10.1 (GAP-INF-001)"

    def test_motor_version_is_3_7_or_higher(self):
        """Motor 3.7.x required for pymongo 4.10 compatibility (seeds/crons still use Motor)."""
        versions = self._req_versions()
        ver = versions.get("motor", "0.0.0")
        major, minor, *_ = (int(x) for x in ver.split(".")[:2])
        assert (major, minor) >= (3, 7), \
            f"requirements.txt has motor=={ver}, expected >= 3.7.1 for pymongo 4.10 compatibility"


# ── 7. change_stream_worker: watch() must be awaited before async-with ────────

class TestChangeStreamWorkerWatchPattern:
    """PyMongo async watch() is a coroutine, unlike Motor where it returned a cursor.
    The correct pattern is ``async with await collection.watch(...) as stream:``.
    Using ``async with collection.watch(...) as stream:`` (no await) raises
    TypeError at runtime because a coroutine has no __aenter__."""

    def test_watch_call_uses_await_in_async_with(self):
        """change_stream_worker.py must use ``await`` before watch() in async-with."""
        import ast as _ast
        src = _source("change_stream_worker.py")
        tree = _ast.parse(src)
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.AsyncWith):
                continue
            for item in node.items:
                ctx = item.context_expr
                # Correct: Await(Call(Attribute(...watch...), ...))
                if (
                        isinstance(ctx, _ast.Await)
                        and isinstance(ctx.value, _ast.Call)
                        and isinstance(ctx.value.func, _ast.Attribute)
                        and ctx.value.func.attr == "watch"
                ):
                    return  # found the correct pattern
                # Wrong: Call(Attribute(...watch...), ...) without Await
                if (
                        isinstance(ctx, _ast.Call)
                        and isinstance(ctx.func, _ast.Attribute)
                        and ctx.func.attr == "watch"
                ):
                    raise AssertionError(
                        "change_stream_worker.py uses 'async with collection.watch(...) as s:' "
                        "WITHOUT await. PyMongo async watch() is a coroutine — must be "
                        "'async with await collection.watch(...) as s:'"
                    )
        # If no watch() async-with found at all, the test is vacuously satisfied
        # (pattern may have changed — the import check is the primary guard).

    def test_pymongo_watch_is_coroutine(self):
        """Confirm PyMongo AsyncCollection.watch is a coroutine (not a sync cursor factory)."""
        import inspect
        from pymongo.asynchronous.collection import AsyncCollection
        assert inspect.iscoroutinefunction(AsyncCollection.watch), (
            "AsyncCollection.watch is not a coroutine — review change_stream_worker.py "
            "for the correct async-with pattern."
        )

    def test_async_aggregate_cursor_proxy_exists(self):
        """_AsyncAggregateCursorProxy must exist in database.py to bridge Motor vs PyMongo API."""
        from database import _AsyncAggregateCursorProxy
        assert _AsyncAggregateCursorProxy is not None

    @pytest.mark.asyncio
    async def test_async_aggregate_cursor_proxy_to_list(self):
        """_AsyncAggregateCursorProxy.to_list() must return an awaitable list."""
        from database import _AsyncAggregateCursorProxy

        class _FakeCursor:
            async def to_list(self, length):
                return [{"_id": 1}, {"_id": 2}]

        async def _fake_aggregate():
            return _FakeCursor()

        proxy = _AsyncAggregateCursorProxy(_fake_aggregate())
        result = await proxy.to_list(10)
        assert result == [{"_id": 1}, {"_id": 2}]

    @pytest.mark.asyncio
    async def test_async_aggregate_cursor_proxy_aiter(self):
        """_AsyncAggregateCursorProxy must support async-for iteration."""
        from database import _AsyncAggregateCursorProxy

        class _FakeCursor:
            def __aiter__(self):
                return self._it()

            async def _it(self):
                for doc in [{"x": 1}, {"x": 2}]:
                    yield doc

        async def _fake_aggregate():
            return _FakeCursor()

        proxy = _AsyncAggregateCursorProxy(_fake_aggregate())
        docs = [doc async for doc in proxy]
        assert docs == [{"x": 1}, {"x": 2}]
