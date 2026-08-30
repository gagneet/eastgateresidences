import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

# Browser-visible feature routes should no longer live under /dashboard/*.
# Keep this focused on quoted URL literals so implementation paths such as
# "@/pages/dashboard/FinancePage" remain valid legacy component imports.
DASHBOARD_CHILD_URL = re.compile(r"""(?P<quote>["'`])/dashboard/[^"'`\s)>,}]+""")
STALE_APP_GROUP_HEADER = "Path: frontend/src/app/(dashboard)/"

SCAN_ROOTS = (
    REPO_ROOT / "frontend" / "src",
    REPO_ROOT / "backend",
)

# Whole-directory exclusions: venv/build artifacts, and paths that legitimately
# reference old URLs (alembic migrations are historical snapshots of what was
# seeded at the time; tests intentionally exercise old strings for regression
# coverage). Do NOT re-narrow SCAN_ROOTS itself to an allowlist of subdirectories
# — that's exactly what let 22 live /dashboard/* links survive undetected in
# backend/server.py, backend/cron/, backend/workers/, and backend/utils/helpers.py
# through the 2026-08-07 product-namespace migration, including one embedded in
# an outbound committee-meeting email.
SCAN_DIR_EXCLUDES = {"venv", "__pycache__", "alembic", "tests", ".next"}

SCAN_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".py", ".json"}


def test_no_live_dashboard_child_url_literals():
    stale_urls: list[str] = []

    for root in SCAN_ROOTS:
        for path in root.rglob("*"):
            if path.suffix not in SCAN_SUFFIXES:
                continue
            if SCAN_DIR_EXCLUDES.intersection(path.parts):
                continue

            text = path.read_text(encoding="utf-8")
            for match in DASHBOARD_CHILD_URL.finditer(text):
                stale_urls.append(f"{path.relative_to(REPO_ROOT)}: {match.group(0)}")

    assert stale_urls == []


def test_moved_app_route_headers_do_not_reference_dashboard_group():
    stale_headers: list[str] = []
    app_route_root = REPO_ROOT / "frontend" / "src" / "app" / "(app)"

    for path in app_route_root.rglob("*.tsx"):
        text = path.read_text(encoding="utf-8")
        if STALE_APP_GROUP_HEADER in text:
            # These generated headers are used as navigation evidence in audits;
            # stale physical paths make a moved route look only partly migrated.
            stale_headers.append(str(path.relative_to(REPO_ROOT)))

    assert stale_headers == []
