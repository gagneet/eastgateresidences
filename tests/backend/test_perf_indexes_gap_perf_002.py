"""GAP-PERF-002 — assert the finance-aggregation indexes stay in financial_repository.

The finance-aggregation endpoints (/stats/building-kpis, /finance/summary,
/finance/kpi-contract, /arrears/detail, /bi/building/{id}/financial-summary) read
`annual_levies` and `units` on every request. Both lacked a building_id-leading
index, so each request did a full collection scan across ALL buildings — a major
contributor to the ~8.9s p95 measured on building_kpis / bi_financial_summary.

`financial_repository.ensure_indexes()` now creates:
  - annual_levies (building_id, year)
  - units        (building_id, unit_number)

This is a static source assertion (no DB) so a future edit that drops or reorders
a key fails CI. It is an index-only optimisation — the computed finance values
are unchanged.
"""
import os
import re

_REPO = os.path.join(
    os.path.dirname(__file__), "..", "..", "backend", "repositories", "financial_repository.py"
)

_EXPECTED = {
    "annual_levies": '[("building_id", 1), ("year", 1)]',
    "units": '[("building_id", 1), ("unit_number", 1)]',
}


def _source() -> str:
    with open(_REPO, encoding="utf-8") as fh:
        return fh.read()


def test_finance_aggregation_indexes_present_and_building_id_leading():
    src = _source()
    missing = []
    for collection, key_spec in _EXPECTED.items():
        pattern = re.compile(
            r"db\.%s\.create_index\(\s*%s" % (re.escape(collection), re.escape(key_spec))
        )
        if not pattern.search(src):
            missing.append(f"{collection} -> {key_spec}")
    assert not missing, (
        "GAP-PERF-002 indexes missing or altered in financial_repository.ensure_indexes():\n  "
        + "\n  ".join(missing)
    )


def test_indexes_live_inside_ensure_indexes():
    # Guard against the calls being added outside the startup-invoked function.
    src = _source()
    body = src.split("async def ensure_indexes()", 1)
    assert len(body) == 2, "ensure_indexes() not found"
    after = body[1]
    assert "db.annual_levies.create_index" in after
    assert "db.units.create_index" in after
