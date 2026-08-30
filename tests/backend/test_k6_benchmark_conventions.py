"""Conventions every k6 benchmark in `tests/performance/` must follow.

These are static checks over the scripts themselves — no k6, no backend, no network.
They exist because the failure modes they catch are silent: a benchmark with a
misconfigured threshold still runs, still prints numbers, and still reports pass/fail.
It just measures or gates the wrong thing, and nobody notices until a decision is made
on the output.

Codified 2026-08-24 after two such defects were found in real use:

  * `owner_dashboard_benchmark.ts` gated on `http_req_failed`, which counts EVERY
    non-2xx. Unit-scoped finance routes legitimately return 404 when the benchmarked
    unit has no ledger row for the year, so a live run reported 14.28% "failures" —
    every one a data condition, not a regression. A gate that fires on data hides the
    regressions it exists to catch.

  * The `tests/performance/ui/` Lighthouse runner reported complete, plausible metrics
    for four dashboards that had actually redirected to the login page.

The teardown rule is the project CLAUDE.md's, restated here so it is enforced rather
than remembered.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PERF_DIR = Path(__file__).resolve().parents[2] / "tests" / "performance"


def _scripts() -> list[Path]:
    return sorted(
        p for p in list(PERF_DIR.glob("*.ts")) + list(PERF_DIR.glob("*.js"))
        if p.is_file()
    )


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _scenario_names(src: str) -> list[str]:
    block = re.search(r"scenarios:\s*\{(.*?)\n    \}", src, re.S)
    if not block:
        return []
    return re.findall(r"\n\s{8}(\w+):\s*\{", block.group(1))


def _latency_threshold_keys(src: str) -> list[str]:
    block = re.search(r"thresholds:\s*\{(.*?)\n    \}", src, re.S)
    if not block:
        return []
    body = block.group(1)
    keys = re.findall(r"['\"]([^'\"]*http_req_duration[^'\"]*)['\"]\s*:", body)
    keys += re.findall(r"\n\s+(http_req_duration)\s*:", body)
    return keys


# Multi-scenario scripts whose latency thresholds span every scenario at once.
#
# A threshold evaluated across a 1-VU smoke scenario AND a ramping load scenario
# cannot distinguish "this endpoint got slower" from "we saturated the test box" —
# the p95 is dominated by whichever phase queues. `dashboards_benchmark.ts` shows the
# fix: scope the latency budget to `{scenario:smoke,...}` and gate saturation on a
# server-error counter instead.
#
# These are GRANDFATHERED, not endorsed. Re-scoping them means deciding what each
# one's load-phase budget should be, and inventing 32 budgets nobody measured would be
# worse than leaving them honestly listed. The list must only ever shrink: new scripts
# scope their thresholds, and removing an entry here is the last step of fixing one.
# Tracked in tasks/GAP-PERF-004-frontend-load-cost-and-benchmark-conventions.md.
UNSCOPED_LATENCY_THRESHOLD_GRANDFATHERED = {
    "access_lifecycle_benchmark.ts", "analytics_benchmark.ts", "auth_benchmark.ts",
    "bi_benchmark.ts", "communication_benchmark.ts", "communications_campaigns_benchmark.ts",
    "decision_register_benchmark.ts", "digital_twin_benchmark.ts", "documents_benchmark.ts",
    "external_api_benchmark.ts", "finance_benchmark.ts", "finance_calculation_registry_benchmark.ts",
    "finance_intelligence_benchmark.ts", "finance_singularity_benchmark.ts",
    "insurance_benchmark.ts", "intelligence_benchmark.ts", "invoice_benchmark.ts",
    "letters_benchmark.ts", "maintenance_benchmark.ts", "management_hierarchy_benchmark.ts",
    "meetings_benchmark.ts", "notifications_benchmark.ts", "nsw_compliance_benchmark.ts",
    "owner_dashboard_benchmark.ts", "portfolio_benchmark.ts", "public_api_benchmark.ts",
    "settings_benchmark.ts", "shadow_read_pool_capacity_benchmark.ts",
    "trust_accounting_benchmark.ts", "trust_dual_approval_benchmark.ts",
    "ui_public_pages_benchmark.js", "unit_true_balance_benchmark.ts",
}


def test_performance_directory_is_discoverable():
    """Guards the glob itself — an empty sweep would make every test below vacuous."""
    assert len(_scripts()) >= 40, f"expected the k6 suite, found {len(_scripts())} scripts"


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: p.name)
def test_every_benchmark_declares_teardown(script: Path):
    """CLAUDE.md: `teardown()` is mandatory, even when it only verifies/logs residue.

    A benchmark with no teardown leaves test records in collections that production
    dashboards and bell notifications read.
    """
    assert "export function teardown" in _source(script), (
        f"{script.name} has no teardown(). It is mandatory even when nothing is "
        f"created — say so explicitly, and say why it is a no-op."
    )


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: p.name)
def test_every_benchmark_gates_on_something(script: Path):
    """A benchmark with no thresholds cannot fail, so it cannot protect anything."""
    assert "thresholds" in _source(script), (
        f"{script.name} defines no thresholds — it reports numbers but gates nothing."
    )


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: p.name)
def test_new_multi_scenario_benchmarks_scope_latency_thresholds(script: Path):
    """A latency threshold spanning a smoke AND a load scenario measures queuing.

    New multi-scenario scripts must scope latency budgets to a named scenario, as
    `dashboards_benchmark.ts` does. Existing offenders are grandfathered above.
    """
    src = _source(script)
    if len(_scenario_names(src)) <= 1:
        return
    unscoped = [k for k in _latency_threshold_keys(src) if "scenario:" not in k]
    if not unscoped:
        return
    assert script.name in UNSCOPED_LATENCY_THRESHOLD_GRANDFATHERED, (
        f"{script.name} has multiple scenarios and unscoped latency thresholds "
        f"{unscoped[:3]}. Scope them to a scenario, e.g. "
        f"'http_req_duration{{scenario:smoke,endpoint:x}}' — otherwise a breach cannot "
        f"be told apart from test-box saturation."
    )


def test_grandfathered_list_has_no_stale_entries():
    """Every grandfathered name must still exist and still have the problem.

    Without this the list rots: a fixed or deleted script would sit in it forever,
    quietly exempting a name that no longer means anything.
    """
    by_name = {p.name: p for p in _scripts()}
    stale = []
    for name in sorted(UNSCOPED_LATENCY_THRESHOLD_GRANDFATHERED):
        script = by_name.get(name)
        if script is None:
            stale.append(f"{name}: no longer exists")
            continue
        src = _source(script)
        unscoped = [k for k in _latency_threshold_keys(src) if "scenario:" not in k]
        if len(_scenario_names(src)) <= 1 or not unscoped:
            stale.append(f"{name}: now scoped — remove it from the list")
    assert not stale, "Stale grandfather entries:\n  " + "\n  ".join(stale)
