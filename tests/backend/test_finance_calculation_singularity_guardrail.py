"""
tests/backend/test_finance_calculation_singularity_guardrail.py

GAP-FIN-040 — Financial Calculation Singularity guardrail (the enforcement keystone).

The canonical arrears / collection-rate implementations already exist
(utils/finance_helpers.py::get_arrears_metrics / get_collection_rate_metrics /
get_building_arrears_summary, over domain/finance/formulas/*). The reason the
"same number computed three different ways" bug keeps recurring is that NOTHING
enforces their use — a new finance surface just writes the obvious inline
`net_balance > 0` count or reads the banned `balance_owing` field, and no test
stops it (GAP-FIN-040 §5).

This static (source-text) guard fails the build the moment a NEW router/service
derives arrears from a raw ledger balance instead of the canonical helpers. It is
modelled on tests/backend/test_finance_input_source_guardrails.py.

Two bypass shapes are detected:
  Rule A — reading a BANNED balance field (`balance_owing` / `total_closing`) as a
           source. `net_balance` is the only authoritative balance
           (finance_helpers.py header); these legacy/portal fields must never feed
           an operational number.
  Rule B — deriving a building-AGGREGATE arrears count/total from a raw
           `net_balance > 0` shape (Mongo `$cond`/`$gt` aggregate, a
           `count_documents({"net_balance": {"$gt": 0}})`, or an `arrears_flag`
           set from `net_balance`) instead of get_arrears_metrics() /
           get_building_arrears_summary().

Deliberately NOT flagged (to avoid false positives): a bare per-unit read such as
`net_balance = ledger.get("net_balance", 0)` or `credit = abs(min(0, net_balance))`
for a SINGLE unit — that is the legitimate way to read one unit's balance. Only
aggregate/flag shapes and banned-field reads are caught.

`_PENDING_MIGRATION` is the tracked-debt allowlist: files that still contain a
bypass and are scheduled for migration onto the canonical helpers. It MUST shrink
to empty as GAP-FIN-040 completes; the test also asserts every allowlisted file
still actually violates (a strict ratchet — once a file is migrated it must be
removed from the allowlist or the test fails, so the list can never silently
rot).
"""
from __future__ import annotations

import os
import re

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BACKEND_ROOT = os.path.join(_REPO_ROOT, "backend")

# Directories scanned in full, plus individually-scanned large modules.
_SCAN_DIRS = ["routers", "services"]
_SCAN_EXTRA_FILES = ["server.py"]

# Canonical modules that ARE the single source of truth — they legitimately
# implement the raw ledger math that everything else must delegate to.
_CANONICAL_EXEMPT = {
    "utils/finance_helpers.py",
    "domain/finance/formulas/arrears.py",
    "domain/finance/formulas/collection.py",
    "services/finance_metrics/facade.py",
    "services/finance_metrics/mongo_adapter.py",
    "services/finance_metrics/postgres_adapter.py",
}

# Files with a documented, permanent justification for containing a matching
# pattern (canonical orchestrator whose raw aggregate is immediately corrected via
# the canonical helper, or a non-display operational use). Each MUST route its
# user-facing arrears/collection figures through the canonical helpers — verified
# by test_finance_kpi_contract.py / test_metric_consistency.py.
_JUSTIFIED_EXEMPT = {
    # get_finance_summary / get_finance_kpi_contract compute a raw units_owing /
    # total_outstanding aggregate ONLY as the `raw_*` input to
    # _compute_grace_aware_arrears(), which overrides it with get_arrears_metrics().
    "routers/finance.py": (
        "canonical orchestrator: raw aggregate feeds _compute_grace_aware_arrears() "
        "then is overridden by get_arrears_metrics() (see finance.py:1817-1830)"
    ),
    # get_building_kpis() routes arrears through get_arrears_metrics() and collection
    # through current_year_collection_rate(); the remaining net_balance matches are
    # the levy-reminder cron (operational notification, not a displayed metric) and
    # get_top_arrears() which uses opening_arrears (a deliberate distinct variant).
    "server.py": (
        "get_building_kpis routes through get_arrears_metrics; remaining net_balance "
        "uses are the operational levy-reminder cron and the opening_arrears variant"
    ),
}

# Tracked-debt allowlist — files still on a bypass, scheduled for migration onto
# get_building_arrears_summary() / get_collection_rate_metrics(). GAP-FIN-040:
# drive this to {}. Every entry must currently violate (asserted below).
_PENDING_MIGRATION: dict[str, str] = {
    # B13 — cross-BUILDING rollup: it aggregates every building in one raw-client
    # query, so routing per-building through get_building_arrears_summary() needs
    # sequential per-building tenant-context switching + live portfolio
    # verification. Deferred as its own careful migration (tracked, GAP-FIN-040).
    "routers/portfolio.py": "B13 — cross-building portfolio rollup → per-building get_building_arrears_summary() (needs context loop + live verify)",
}

# ── Bypass pattern definitions ───────────────────────────────────────────────

# Rule A: banned balance fields read/projected/queried as a source.
# Matches `.get("balance_owing"...)` and `"balance_owing": 1` / `"balance_owing": {...}`
# (projection or query operator) — NOT `"balance_owing": some_var` (a legit OUTPUT
# field name derived from net_balance, e.g. owner_finance_service).
_RULE_A = re.compile(
    r'\.get\(\s*"(?:balance_owing|total_closing)"'
    r'|"(?:balance_owing|total_closing)"\s*:\s*(?:1\b|\{)'
)

# Rule B: building-aggregate arrears count/total derived from a raw net_balance>0
# shape, or an arrears_flag set from net_balance. Per-unit scalar reads do NOT match.
_RULE_B = re.compile(
    r'"\$net_balance",\s*0'                       # {"$gt": ["$net_balance", 0]}
    r'|"net_balance"\s*:\s*\{\s*"\$gt"'           # {"net_balance": {"$gt": 0}}
    r'|count_documents\([^)]*net_balance'         # count_documents({"net_balance": ...})
    r'|arrears_flag"?\s*[=:].*net_balance'        # arrears_flag = / "arrears_flag": ... net_balance
)


def _iter_scanned_files():
    for rel in _SCAN_EXTRA_FILES:
        yield rel
    for d in _SCAN_DIRS:
        root = os.path.join(_BACKEND_ROOT, d)
        for dirpath, _dirs, files in os.walk(root):
            if "__pycache__" in dirpath:
                continue
            for fn in files:
                if fn.endswith(".py"):
                    abs_path = os.path.join(dirpath, fn)
                    yield os.path.relpath(abs_path, _BACKEND_ROOT)


def _violations(rel_path: str) -> list[tuple[int, str, str]]:
    """Return (line_no, rule, snippet) bypass matches for a file."""
    abs_path = os.path.join(_BACKEND_ROOT, rel_path)
    out: list[tuple[int, str, str]] = []
    with open(abs_path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, start=1):
            if _RULE_A.search(line):
                out.append((i, "A(banned-field)", line.strip()))
            if _RULE_B.search(line):
                out.append((i, "B(raw-arrears)", line.strip()))
    return out


def test_no_new_finance_calculation_bypass():
    """No router/service outside the canonical helpers may derive arrears from a
    raw ledger balance — except the tracked _PENDING_MIGRATION debt allowlist."""
    allowed = _CANONICAL_EXEMPT | set(_JUSTIFIED_EXEMPT) | set(_PENDING_MIGRATION)
    offenders: dict[str, list[tuple[int, str, str]]] = {}
    for rel in _iter_scanned_files():
        if rel in allowed:
            continue
        v = _violations(rel)
        if v:
            offenders[rel] = v

    assert not offenders, (
        "New financial-calculation bypass detected — arrears/collection must route "
        "through utils/finance_helpers.py::get_building_arrears_summary() / "
        "get_arrears_metrics() / get_collection_rate_metrics(), never a raw "
        "net_balance>0 aggregate or the banned balance_owing/total_closing fields "
        "(GAP-FIN-040):\n"
        + "\n".join(
            f"  {f}:{ln} [{rule}] {snip}"
            for f, vs in offenders.items()
            for ln, rule, snip in vs
        )
    )


def test_pending_migration_allowlist_is_not_stale():
    """Strict ratchet: every file in _PENDING_MIGRATION must still contain a bypass.
    Once a file is migrated onto the canonical helpers it MUST be removed from the
    allowlist (otherwise this fails), so the debt list can only shrink."""
    stale = [rel for rel in _PENDING_MIGRATION if not _violations(rel)]
    assert not stale, (
        "These files no longer contain a bypass — remove them from "
        "_PENDING_MIGRATION (GAP-FIN-040 ratchet): " + ", ".join(stale)
    )


def test_guard_is_not_vacuous():
    """Sanity: the canonical helper module itself contains the raw math the guard
    looks for, proving the patterns match something real (else a typo'd regex would
    pass trivially)."""
    assert _violations("utils/finance_helpers.py"), (
        "Expected utils/finance_helpers.py to contain the raw net_balance math the "
        "guard detects — if the canonical implementation moved, update the patterns."
    )
