# @featuretrace:cutover-toggle-safety — the ratchet that keeps converted domains dispatched.
# Layer: test
# Data flow: audit_datastore_dispatch.scan() -> datastore_dispatch_baseline.json (global).
# Related: scripts/validation/audit_datastore_dispatch.py
#          backend/services/store_router.py
"""The gate must be honest in both directions, or it gets deleted.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_datastore_dispatch_ratchet.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validation" / "audit_datastore_dispatch.py"

spec = importlib.util.spec_from_file_location("audit_datastore_dispatch", SCRIPT)
audit = importlib.util.module_from_spec(spec)
sys.modules["audit_datastore_dispatch"] = audit
# No try/except: a partially-loaded module silently exposes its early definitions and
# omits the rest, which is how an earlier analysis in this repo produced a confident
# wrong answer. Let an import error fail the test loudly.
spec.loader.exec_module(audit)


class TestBaselineIsHonest:
    def test_the_baseline_file_exists_and_parses(self):
        baseline = audit.BASELINE
        assert baseline.exists(), "run --update-baseline and commit the result"
        json.loads(baseline.read_text())

    def test_the_tree_does_not_exceed_its_baseline(self):
        """The actual gate: no NEW undispatched read in a converted domain."""
        current = {f.key for f in audit.scan().findings}
        recorded = set(audit.load_baseline().get("undispatched", []))
        new = current - recorded
        assert not new, (
            "New undispatched MongoDB read(s) in a converted domain:\n  "
            + "\n  ".join(sorted(new))
            + "\nRoute them through services/store_router.py::read_through."
        )

    def test_improvements_are_baselined_so_they_cannot_be_given_back(self):
        current = {f.key for f in audit.scan().findings}
        recorded = set(audit.load_baseline().get("undispatched", []))
        fixed = recorded - current
        assert not fixed, (
            "These were fixed but the baseline still lists them — re-baseline so the "
            "win is locked in:\n  " + "\n  ".join(sorted(fixed))
            + "\n  python3 scripts/validation/audit_datastore_dispatch.py --update-baseline"
        )


class TestScopeIsDeliberate:
    def test_only_converted_domains_are_enforced(self):
        """The 103-file backlog must not fail PRs that never touched it."""
        modules = {f.module for f in audit.scan().findings}
        assert modules <= audit._CONVERTED_MODULES

    def test_documents_is_not_enforced(self):
        """routers/documents.py is an unwired duplicate; the live routes are in server.py."""
        assert "documents" not in audit._CONVERTED_MODULES

    def test_writes_are_reported_but_not_enforced(self):
        """No general write dispatch exists yet; demanding one would be demanding nothing."""
        result = audit.scan()
        enforced_ops = {f.op for f in result.findings}
        assert enforced_ops <= audit.MONGO_READ_OPS
        assert not (enforced_ops & audit.MONGO_WRITE_OPS)

    def test_server_py_is_counted_but_never_enforced(self):
        """189 inline routes need extracting first; gating them would be noise."""
        result = audit.scan()
        assert result.server_count > 0, "server.py should still be reported"
        assert not any(f.module == "server" for f in result.findings)


class TestFindingIdentity:
    def test_key_excludes_the_line_number(self):
        """Otherwise an unrelated edit above a finding looks like a new violation."""
        a = audit.Finding(module="finance", handler="h", line=10, collection="c", op="find")
        b = audit.Finding(module="finance", handler="h", line=999, collection="c", op="find")
        assert a.key == b.key

    def test_key_distinguishes_collection_and_operation(self):
        a = audit.Finding(module="finance", handler="h", line=1, collection="c1", op="find")
        b = audit.Finding(module="finance", handler="h", line=1, collection="c2", op="find")
        c = audit.Finding(module="finance", handler="h", line=1, collection="c1", op="aggregate")
        assert len({a.key, b.key, c.key}) == 3


class TestFallbackIsNotAViolation:
    def test_a_mongo_callable_passed_to_read_through_is_exempt(self, tmp_path):
        """That closure IS the dispatch. Flagging it would forbid the correct pattern."""
        module = tmp_path / "sample.py"
        module.write_text(
            "async def _mongo():\n"
            "    return await db.things.find({}).to_list(10)\n"
            "\n"
            "@router.get('/x')\n"
            "async def handler():\n"
            "    return await read_through(domain='d', building_id='b', route='r',\n"
            "                              postgres=lambda: None, mongo=_mongo)\n"
        )
        reads, writes = audit._scan_module(module)
        assert reads == [], f"the read_through fallback was wrongly flagged: {reads}"

    def test_an_undispatched_read_in_the_same_shape_is_still_caught(self, tmp_path):
        module = tmp_path / "sample2.py"
        module.write_text(
            "@router.get('/x')\n"
            "async def handler():\n"
            "    return await db.things.find({}).to_list(10)\n"
        )
        reads, _ = audit._scan_module(module)
        assert [f.collection for f in reads] == ["things"]
