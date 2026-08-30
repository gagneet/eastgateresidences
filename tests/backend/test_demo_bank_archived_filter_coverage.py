"""Static guard: every demo_bank_transactions READ (find/find_one/aggregate) in backend/routers
and backend/services must exclude archived rows (is_archived != True), or be explicitly
allowlisted here with a reason.

This is a proximity-based text check, not a real AST analysis — deliberately simple, matching
the style of this repo's other doubled-role/hardcoded-id grep audits (see CLAUDE.md). It exists
because a single is_archived-aware writer (the archive script) does not, by itself, guarantee any
downstream read actually honours the flag — this repo's CLAUDE.md documents this exact class of
bug repeatedly (archived buildings resurfacing in listings, is_test_data leaking into dashboards).

If you add a new demo_bank_transactions read: add "is_archived" to its filter (inline, or via
repositories.demo_bank_repository.active_demo_bank_filter), or add it to _ALLOWLISTED_WRITE_SITES
below with a one-line reason if it's a write keyed on an already-resolved _id (which does not
need this guard).
"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
SEARCH_DIRS = ["routers", "services", "repositories"]
READ_CALL_PATTERN = re.compile(r"demo_bank_transactions\.(find_one|find|aggregate)\(")

# (file relative to backend/, 1-indexed line number of the call) -> reason.
# Only WRITE operations keyed on an already-resolved _id/unique field belong here — anything
# that decides what to list/use as a candidate must carry the is_archived guard instead.
_ALLOWLISTED_WRITE_SITES: dict[tuple[str, int], str] = {
    ("routers/bank_feeds.py", 578): "update_one by _id -- mutates an already-identified row, not a listing read.",
    ("routers/financial_onboarding.py", 1474): "update_many labeling operation on already-identified rows, not a listing read.",
    ("services/historical_levy_reconstruction_service.py", 341): "update_one on an already-identified row, not a listing read.",
}


def _iter_python_files():
    for sub in SEARCH_DIRS:
        base = BACKEND_ROOT / sub
        if base.is_dir():
            yield from base.rglob("*.py")


def test_every_demo_bank_transactions_read_excludes_archived_rows():
    violations = []
    for path in _iter_python_files():
        rel = str(path.relative_to(BACKEND_ROOT))
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if not READ_CALL_PATTERN.search(line):
                continue
            call_type = READ_CALL_PATTERN.search(line).group(1)
            if call_type == "find_one" and ".update_one(" in line:
                continue  # not actually the read pattern (defensive, shouldn't match anyway)
            line_no = i + 1
            if (rel, line_no) in _ALLOWLISTED_WRITE_SITES:
                continue
            # Look for is_archived within a window around the call (covers multi-line filter
            # dicts/pipelines built well above the call site that references the variable, and
            # the admin_diagnostics.py conditional-guard style alike).
            window = "\n".join(lines[max(0, i - 40): i + 20])
            if "is_archived" not in window:
                violations.append(f"{rel}:{line_no} ({call_type}) -- no is_archived guard found nearby")

    assert not violations, (
        "New/changed demo_bank_transactions read(s) without an is_archived guard:\n"
        + "\n".join(violations)
        + "\n\nAdd `\"is_archived\": {\"$ne\": True}` to the filter, or add to "
          "_ALLOWLISTED_WRITE_SITES in this test with a one-line reason if it's a write on an "
          "already-resolved row."
    )
