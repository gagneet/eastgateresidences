#!/usr/bin/env python3
"""
Fix stale "chairman" role literals in backend production code.

chairman was removed as a top-level UserRole in migration 0025 / commit 67fbc4a5.
Rules applied per-line:
  - Comments and MongoDB $in queries: skipped (backward-compat needed).
  - Line has both "chairman" and "ec_member" (or UserRole.EC_MEMBER): remove "chairman".
  - Line has "chairman" only: replace with "ec_member".
  - Dict key names like "chairman_only" or "who_can_delete" lists: skipped
    (handled separately as stored-data strings, not runtime role guards).
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _has_ec_member(line: str) -> bool:
    """Generated function header.

    Function: _has_ec_member
    Path: backend/scripts/fix_chairman_role_literals.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return '"ec_member"' in line or "UserRole.EC_MEMBER" in line


def _should_skip(line: str) -> bool:
    """Generated function header.

    Function: _should_skip
    Path: backend/scripts/fix_chairman_role_literals.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    stripped = line.strip()
    # Pure comment
    if stripped.startswith("#"):
        return True
    # MongoDB $in query  — these look for users in DB who may still have role="chairman"
    if "$in" in line:
        return True
    # Dict key definitions that ARE the string "chairman_only" (not a role value)
    if '"chairman_only"' in line:
        return True
    # who_can_delete config lists in chat groups (stored in DB)
    if "who_can_delete" in line:
        return True
    # Lines where "chairman" is only in an inline comment after code
    # (rare but safe to skip — the guard part will be on another line)
    if re.search(r'^\s*\S.*#.*"chairman"', line) and '"chairman"' not in line.split("#")[0]:
        return True
    return False


def fix_line(line: str) -> str:
    """Generated function header.

    Function: fix_line
    Path: backend/scripts/fix_chairman_role_literals.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if '"chairman"' not in line:
        return line
    if _should_skip(line):
        return line

    original = line
    if _has_ec_member(line):
        # Remove ", "chairman"" pattern (chairman is not first in list/set)
        line = re.sub(r',\s*"chairman"', "", line)
        # Remove ""chairman", " pattern (chairman is first in list/set)
        line = re.sub(r'"chairman",\s*', "", line)
        # Fallback: bare "chairman" still remaining
        line = line.replace('"chairman"', "")
    else:
        # Chairman was a management-tier role; simply remove it rather than
        # replacing with "ec_member" to avoid granting EC governance access
        # into management-tier role sets on re-runs.
        line = re.sub(r',\s*"chairman"', "", line)
        line = re.sub(r'"chairman",\s*', "", line)
        line = line.replace('"chairman"', "")

    if line != original:
        return line
    return original


def process_file(path: Path, dry_run: bool = False) -> list[str]:
    """Return list of change descriptions; empty if nothing changed."""
    text = path.read_text()
    lines = text.splitlines(keepends=True)
    new_lines = []
    changes = []
    for i, line in enumerate(lines, start=1):
        fixed = fix_line(line)
        new_lines.append(fixed)
        if fixed != line:
            changes.append(f"  line {i}: {line.rstrip()!r} → {fixed.rstrip()!r}")
    if changes and not dry_run:
        path.write_text("".join(new_lines))
    return changes


def main(dry_run: bool = False) -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/fix_chairman_role_literals.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    targets = [
        *sorted((ROOT / "backend" / "routers").glob("*.py")),
        ROOT / "backend" / "server.py",
        *sorted((ROOT / "backend" / "services").glob("**/*.py")),
        *sorted((ROOT / "backend" / "cron").glob("*.py")),
        *sorted((ROOT / "backend" / "domain").glob("*.py")),
    ]

    total_files = 0
    total_lines = 0
    for path in targets:
        if "venv" in str(path) or "alembic" in str(path):
            continue
        changes = process_file(path, dry_run=dry_run)
        if changes:
            total_files += 1
            total_lines += len(changes)
            rel = path.relative_to(ROOT)
            print(f"\n{'[DRY-RUN] ' if dry_run else ''}FIXED {rel}:")
            for c in changes:
                print(c)

    print(f"\n{'[DRY-RUN] ' if dry_run else ''}Done: {total_lines} lines changed across {total_files} files.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    main(dry_run=dry_run)
