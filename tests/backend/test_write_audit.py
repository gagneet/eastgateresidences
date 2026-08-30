#!/usr/bin/env python3
"""
Tests for the production-write recorder in conftest (GAP-TEST-001 step 1).

WHY THIS FILE EXISTS
The recorder's entire value is that its silence can be trusted. A detector nobody
has watched fire is indistinguishable from one that does not work — and this one
reports on writes to the PRODUCTION database, so a false "no writes recorded" is
worse than no detector at all: it converts an unknown risk into a believed-safe one.

These exercise the wrapping mechanism against a stub class, so nothing here touches
Mongo. The wiring to pymongo's AsyncCollection is one line in _install_write_audit.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_write_audit.py -q
"""

import pytest


def _ct(pytestconfig):
    """The already-loaded tests/backend/conftest module.

    Not `import conftest`: conftest puts BACKEND on sys.path, not tests/backend, so
    that import fails. And re-importing it by file path would create a SECOND module
    object with its own recording dict — the tests would then pass against a copy
    while the real recorder went unexercised, which is precisely the failure mode
    this file exists to rule out.

    pytest has already loaded and registered the real one; ask it.
    """
    for plugin in pytestconfig.pluginmanager.get_plugins():
        path = getattr(plugin, "__file__", "") or ""
        if path.replace("\\", "/").endswith("tests/backend/conftest.py"):
            return plugin
    raise AssertionError("tests/backend/conftest.py is not a registered plugin")


class _StubCollection:
    """Stands in for pymongo's AsyncCollection: same shape, no network."""

    def __init__(self, db_name="somedb", name="somecoll"):
        self.database = type("DB", (), {"name": db_name})()
        self.name = name
        self.calls = []

    async def insert_one(self, doc):
        self.calls.append(("insert_one", doc))
        return "inserted"

    async def update_one(self, flt, upd, **kw):
        self.calls.append(("update_one", flt))
        return "updated"

    async def find_one(self, flt=None):
        # A READ. Must never be recorded — a recorder that flags reads produces a
        # list too long to triage, which is the same as producing nothing.
        self.calls.append(("find_one", flt))
        return None


@pytest.fixture
def ct(pytestconfig):
    """The conftest module, with its recording state isolated per test."""
    mod = _ct(pytestconfig)
    saved = dict(mod._recorded_writes)
    saved_node = mod._current_nodeid["id"]
    mod._recorded_writes.clear()
    yield mod
    mod._recorded_writes.clear()
    mod._recorded_writes.update(saved)
    mod._current_nodeid["id"] = saved_node


@pytest.mark.asyncio
async def test_records_a_write_with_collection_and_caller(ct):
    cls = type("C", (_StubCollection,), {})
    ct.audit_wrap(cls, ("insert_one", "update_one", "find_one_and_update"))
    ct._current_nodeid["id"] = "tests/backend/test_x.py::test_y"

    coll = cls(db_name="strataos_production", name="maintenance_forecasts")
    assert await coll.insert_one({"a": 1}) == "inserted"   # delegation preserved

    assert ct._recorded_writes == {
        "strataos_production.maintenance_forecasts": {
            ("tests/backend/test_x.py::test_y", "insert_one"),
        }
    }


@pytest.mark.asyncio
async def test_does_not_record_reads(ct):
    cls = type("C", (_StubCollection,), {})
    ct.audit_wrap(cls, ("insert_one", "update_one"))

    coll = cls()
    await coll.find_one({"a": 1})

    assert ct._recorded_writes == {}


@pytest.mark.asyncio
async def test_wrapping_is_idempotent(ct):
    """A second install must not stack wrappers and double-count the same call."""
    cls = type("C", (_StubCollection,), {})
    first = ct.audit_wrap(cls, ("insert_one",))
    second = ct.audit_wrap(cls, ("insert_one",))

    assert (first, second) == (1, 0)

    coll = cls()
    await coll.insert_one({"a": 1})
    # One call site recorded, and the stub saw the call exactly once.
    assert len(next(iter(ct._recorded_writes.values()))) == 1
    assert coll.calls.count(("insert_one", {"a": 1})) == 1


@pytest.mark.asyncio
async def test_audit_never_breaks_the_call_it_observes(ct):
    """A stub with no `database` must still record, and must still delegate.

    The hook runs inside every write in the suite. If it can raise, it can fail a
    test for a reason that has nothing to do with the test.
    """
    class _Bare:
        name = "orphan"

        async def insert_one(self, doc):
            return "ok"

    ct.audit_wrap(_Bare, ("insert_one",))
    assert await _Bare().insert_one({}) == "ok"
    assert "<unknown>.orphan" in ct._recorded_writes


def test_every_mutating_method_name_is_audited(pytestconfig):
    ct = _ct(pytestconfig)
    """The list is the contract. bulk_write and find_one_and_* are the easy misses:
    they do not read as writes at the call site, and TenantCollection forwards them
    straight through to the raw collection via __getattr__."""
    for name in ("insert_one", "insert_many", "update_one", "update_many",
                 "replace_one", "delete_one", "delete_many", "bulk_write",
                 "find_one_and_update", "find_one_and_replace", "find_one_and_delete"):
        assert name in ct._AUDITED_WRITE_METHODS
