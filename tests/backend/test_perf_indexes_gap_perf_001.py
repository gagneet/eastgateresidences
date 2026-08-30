"""GAP-PERF-001 — assert the new building_id-leading indexes stay in server.py.

The startup index block in backend/server.py creates the compound indexes that
keep tenant-scoped list reads off collection scans (purchase_orders/contractors
were measured at p95 ~52s/~34s with no index). Because TenantScopedDatabase
prepends {building_id: ...} to every scoped query, each of these indexes MUST
lead with building_id. This is a static source assertion — no DB required — so a
future edit that drops or reorders a key fails CI.
"""
import os
import re

_SERVER_PY = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "server.py")

# collection -> the leading key list its create_index call must contain (verbatim)
_EXPECTED = {
    "purchase_orders": '[("building_id", 1), ("created_at", -1)]',
    "contractors": '[("building_id", 1)]',
    "meetings": '[("building_id", 1), ("meeting_date", -1)]',
    "agm": '[("building_id", 1), ("date", -1)]',
    "agm_motions": '[("building_id", 1), ("agm_id", 1)]',
    "agm_votes": '[("building_id", 1), ("agm_id", 1)]',
    "agm_attendance": '[("building_id", 1), ("agm_id", 1)]',
    "todos": '[("building_id", 1), ("due_date", 1)]',
    "insurance_policies": '[("building_id", 1), ("expiry_date", 1)]',
    "insurance_brokers": '[("building_id", 1), ("broker_name", 1)]',
}


def _server_source() -> str:
    with open(_SERVER_PY, encoding="utf-8") as fh:
        return fh.read()


def test_new_perf_indexes_present_and_building_id_leading():
    src = _server_source()
    missing = []
    for collection, key_spec in _EXPECTED.items():
        # the create_index call for this collection must appear with the exact key spec
        pattern = re.compile(
            r"db\.%s\.create_index\(\s*%s" % (re.escape(collection), re.escape(key_spec))
        )
        if not pattern.search(src):
            missing.append(f"{collection} -> {key_spec}")
    assert not missing, "GAP-PERF-001 indexes missing or altered in server.py:\n  " + "\n  ".join(missing)


def test_perf_index_status_variant_for_purchase_orders():
    # POs are also listed filtered by status, so the status-leading-after-building compound must exist too.
    src = _server_source()
    assert re.search(
        r'db\.purchase_orders\.create_index\(\s*\[\("building_id", 1\), \("status", 1\), \("created_at", -1\)\]',
        src,
    ), "purchase_orders (building_id, status, created_at) index missing"
