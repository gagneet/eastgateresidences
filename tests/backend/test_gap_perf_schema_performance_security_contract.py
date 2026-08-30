"""Static guards for the GAP-PERF schema/read-model programme.

These tests do not prove the future state is implemented. They keep the planning
artifacts and benchmark/security harnesses honest while the implementation is
split across multiple future tasks.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "tasks"
K6 = ROOT / "tests" / "performance" / "gap_perf_read_model_benchmark.ts"

GAP_PERF_SCHEMA_TASKS = [
    "GAP-PERF-005-schema-index-read-model-master-plan.md",
    "GAP-PERF-006-finance-bi-report-pack-read-models.md",
    "GAP-PERF-007-operations-cases-maintenance-read-models.md",
    "GAP-PERF-008-documents-correspondence-certificates-search.md",
    "GAP-PERF-009-communications-mobile-delivery-read-models.md",
    "GAP-PERF-010-assets-suppliers-access-facilities-read-models.md",
    "GAP-PERF-011-enterprise-identity-acl-audit-api-contracts.md",
    "GAP-PERF-012-portfolio-cross-building-reporting-exports.md",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_gap_perf_schema_tasks_have_performance_and_security_sections():
    missing: list[str] = []
    for filename in GAP_PERF_SCHEMA_TASKS:
        text = _read(TASKS / filename)
        for heading in ("## K6", "## UI/UX Tests", "## Security Tests", "## Acceptance Criteria"):
            if heading not in text:
                missing.append(f"{filename}: missing {heading}")
    assert not missing, "\n".join(missing)


def test_gap_perf_tasks_require_tenant_scope_and_drill_through():
    combined = "\n".join(_read(TASKS / filename) for filename in GAP_PERF_SCHEMA_TASKS)
    required_terms = [
        "building_id",
        "scheme_id",
        "RLS",
        "canonical",
        "drill",
        "source IDs",
        "as-at",
        "field masking",
    ]
    missing = [term for term in required_terms if term not in combined]
    assert not missing, f"GAP-PERF schema plan lost required security/performance terms: {missing}"


def test_gap_perf_k6_benchmark_is_read_only_and_auth_scoped():
    text = _read(K6)
    forbidden = ["http.post(", "http.put(", "http.patch(", "http.del("]
    present = [token for token in forbidden if token in text]
    assert not present, f"gap_perf_read_model_benchmark.ts must stay read-only: {present}"
    assert "AUTH_TOKEN" in text
    assert "X-Building-ID" in text
    assert "export function teardown()" in text
    assert "Read-only by design" in text


def test_gap_perf_playwright_ui_smoke_exists_and_checks_overflow_and_api_prefixes():
    text = _read(ROOT / "tests" / "frontend" / "e2e" / "gap-perf-read-model-ui.spec.ts")
    assert "page.setViewportSize" in text
    assert "scrollWidth" in text
    assert "/api/api/" in text
    assert "**/api/auth/session" in text
    assert "route.fulfill" in text


def test_future_read_model_plan_mentions_view_security_and_not_dashboard_only_totals():
    text = _read(TASKS / "GAP-PERF-005-schema-index-read-model-master-plan.md")
    assert "Materialized views must not become an RLS bypass" in text
    assert "Every report-pack number must store source table/collection" in _read(
        TASKS / "GAP-PERF-006-finance-bi-report-pack-read-models.md"
    )
    assert "no aggregate leaks inaccessible building counts" in _read(
        TASKS / "GAP-PERF-012-portfolio-cross-building-reporting-exports.md"
    ).lower()
