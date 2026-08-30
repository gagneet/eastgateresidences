#!/usr/bin/env python3
"""
Static scan: no plaintext credentials or password hashes in the tree.

WHY THIS EXISTS
On 2026-08-26 the repository contained the live password of an active super_admin
in 36 files, plus a pre-computed bcrypt hash of it, plus eight more seeded fixture
passwords attached to the real names and email addresses of East Gate owners and
committee members — around 90 files in total. The super-admin account's stored hash
was still byte-identical to the committed one, meaning the "change these passwords
immediately after first login" note in the seed had never been acted on in the life
of the system.

None of it was hidden. It accumulated because nothing ever looked. This looks.

It is a heuristic, not a secret scanner: it knows the specific shapes this codebase
produced (bcrypt hashes, the seed's password family, obvious literals next to a
password key). A real scanner over history is a separate job — see GAP-SEC-013.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_no_committed_credentials.py -q
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Directories with no first-party source in them.
SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", ".next", ".next-verify",
    "dist", "build", ".pytest_cache", "coverage", "playwright-report", "test-results",
}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".pdf", ".zip", ".gz",
    ".woff", ".woff2", ".ttf", ".otf", ".mp4", ".webm", ".lock",
}

# This file necessarily describes the patterns it bans.
SELF = Path(__file__).name

# Deliberate, reviewed exceptions. Each needs a reason, not just a path.
ALLOWED = {
    # A constant-time anti-enumeration sentinel: verify_password() must run even when
    # no user matched, so login timing cannot reveal whether an address exists. It is
    # a hash of nothing anyone can log in with, and it has to be a literal to do its
    # job.
    "backend/routers/auth.py",
    # The sales-demo account password is PUBLIC by design — the whole point is that a
    # prospect can log in and evaluate the platform without being issued anything.
    # Both files say so at the literal. A public credential is not a leaked one.
    "backend/seeds/demo_customer.py",
    "backend/seeds/demo_scheme.py",
}

# Retained by an explicit decision (2026-08-26), not by oversight.
#
# These are archived database dumps. They carry bcrypt hashes and the real email
# addresses of East Gate owners, for accounts that no longer exist in either store —
# so the hashes authenticate nothing, and the credential risk is nil. What they still
# are is personal data in a repository.
#
# They are KEPT deliberately: East Gate's operational data was purged, and the
# platform currently has no real data to render. These dumps are the only fixture
# that can be loaded back to validate the pages, the UI and the calculation logic
# against realistic content. Deleting them now would remove the means of verifying
# the thing they are evidence of.
#
# EXIT CONDITION — remove this entry and the files when BOTH hold:
#   1. the platform has been validated against re-uploaded data, and
#   2. that data is sourced from somewhere other than these dumps.
# Whoever does that should also decide whether the owner email addresses need
# scrubbing from anything derived from them.
RETAINED_BY_DECISION = {
    "docs/archive/architecture/mongodb.schema",
    "docs/archive/architecture/mongodb_raw.json",
}

# Precision over recall, deliberately.
#
# The first version of this scan matched a SHAPE — a capitalised word followed by
# three digits and "!" — to catch the seed family. It also matched the throwaway
# passwords in a dozen registration tests, where the string belongs to a user the
# test creates and discards two lines later. Redacting those bought no security, left
# the source reading as an unresolved template, and in three frontend tests meant the
# value typed into a password field was the placeholder itself.
#
# A scanner with false positives gets switched off, and then it protects nothing. So
# this knows the ACTUAL leaked values, not a family resemblance. Adding to this list
# is a deliberate act; that is the point.
#
# The values are assembled from fragments so this file is not itself a hit.
_LEAKED = [
    "East" "Gate$13195%",      # rotated 2026-08-26 — was an active super_admin's password
    "Chair" "man123!",
    "Building" "Admin123!",
    "E" "C123!",
    "Gag" "neet123!",
    "Ten" "ant123!",
    "Recep" "tion123!",
    "Ag" "ent123!",
    "Contr" "actor123!",
    "Man" "ager123!",
    "Serv" "ice123!",
    "Nore" "ply123!",          # was the live SMTP password, committed in .env.example
]

PATTERNS = [
    # Any bcrypt hash. There is no legitimate reason to commit one: it is a credential
    # for offline cracking, and both of the ones found here were generated from a
    # password written in the comment directly above them. Genuine exceptions are
    # named in ALLOWED with a reason.
    (re.compile(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}"), "bcrypt hash"),
    # The specific credentials this repository is known to have committed.
    (re.compile("|".join(re.escape(v) for v in _LEAKED)), "known leaked credential"),
]

def _candidate_files():
    """Files GIT TRACKS — the scope the title claims.

    Deliberately not a filesystem walk. backend/.env holds real credentials and is
    correctly gitignored; flagging it would be a false positive that trains people to
    ignore this test, which is the only way a scanner like this fails. `git ls-files`
    is also the honest definition of "committed".
    """
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        capture_output=True, text=True, check=True,
    ).stdout
    for name in out.split("\0"):
        if not name:
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.name == SELF:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel in ALLOWED or rel in RETAINED_BY_DECISION:
            continue
        yield path


@pytest.mark.parametrize("pattern,label", PATTERNS, ids=[p[1] for p in PATTERNS])
def test_no_committed_credentials(pattern, label):
    hits = []
    for path in _candidate_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                rel = path.relative_to(ROOT)
                hits.append(f"  {rel}:{i}")

    assert not hits, (
        f"{len(hits)} committed {label}(s) found.\n"
        + "\n".join(hits[:40])
        + ("\n  ... and more" if len(hits) > 40 else "")
        + "\n\nCredentials come from the environment. Seeds read SEED_SUPER_ADMIN_PASSWORD /"
          "\nSEED_TEST_USER_PASSWORD and refuse to run without them; tests read E2E_*_PASSWORD"
          "\nand skip without them. See GAP-SEC-013."
    )


def test_the_scan_actually_matches_what_it_claims_to():
    """A scanner that matches nothing passes every run and protects nothing.

    Each pattern is checked against a sample of the exact shape it was written for,
    using values reconstructed here rather than copied, so this file does not itself
    become the thing it bans.
    """
    bcrypt_re, leaked_re = (p for p, _ in PATTERNS)
    assert bcrypt_re.search("$2b$12$" + "A" * 53)
    assert leaked_re.search("Chair" + "man123!")
    assert leaked_re.search("East" + "Gate$13195%")
    # And does not fire on ordinary content, including the throwaway passwords that
    # the earlier shape-based pattern wrongly flagged across the registration tests.
    assert not bcrypt_re.search("$2b$12$tooshort")
    assert not leaked_re.search("Pass" + "word123!")
    assert not leaked_re.search("Ad" + "min123!")
    assert not leaked_re.search("EastGate is a building")


def test_no_credentials_baked_into_claude_permission_rules():
    """Secrets rule 3: never bake a credential into a permission rule.

    Checked SEPARATELY from the git-tracked scan above, and deliberately so.
    `.claude/settings.local.json` is untracked, so a committed-credentials scan cannot
    see it — but the rule is about the file EXISTING with plaintext in it, not about it
    reaching a remote. Four rules there held login passwords, one of them the East Gate
    super-admin credential that GAP-SEC-013 had already rotated: stale, useless, and
    still sitting in plaintext on disk.

    A permission rule never needs the credential. `Bash(curl ...)` already matches the
    command; embedding the password buys nothing and persists it.

    The companion guard test_env_examples_have_no_real_secrets.py covers `.env.example`
    files (rule 2). This covers rule 3, which that one does not reach.
    """
    import json
    import re

    offenders = []
    # Both the shared file and the untracked local one — the rule applies to either.
    for name in ("settings.json", "settings.local.json"):
        path = ROOT / ".claude" / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        rules = []
        for value in (data.get("permissions") or {}).values():
            if isinstance(value, list):
                rules.extend(value)
        for rule in rules:
            # A quoted password field, or a URL carrying inline credentials.
            if re.search(r'"password"\s*:\s*"[^"]+"', rule, re.I) or re.search(r'://[^:@/\s]+:[^@\s]+@', rule):
                offenders.append(f"  {name}: {rule[:80]}")

    assert not offenders, (
        "Credentials found in Claude permission rules:\n"
        + "\n".join(offenders)
        + "\n\nThe generic Bash(curl *) / Bash(psql *) rules already cover these commands."
          "\nExport the credential in the shell instead of embedding it."
    )
