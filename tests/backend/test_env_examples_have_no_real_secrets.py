# @featuretrace:security — Guards committed *.env.example templates against real credentials.
# Layer: test
# Data flow: git-tracked *.env.example files → this test → CI failure on a live-looking value.
# Related: CLAUDE.md "Secrets Handling (MANDATORY)"
#           rules/post-compact-critical.md "## Secrets rules"
#           deploy/env/backend.env.example (reference placeholder style)
# Toggle: none
# Tests: this file

"""
Every committed ``*.env.example`` is a TEMPLATE, never a copy of a live ``.env``.

Real incident (2026-08-26): ``backend/.env.example`` was byte-identical to production
``backend/.env`` for 51 of 58 keys — JWT_SECRET, ENCRYPTION_KEY, CRON_SECRET, Mongo and
Postgres credentials, Stripe/Migadu/Mindee/Serper/reCAPTCHA and an Anthropic API key — and
had been committed and pushed. This test is the regression guard.

It checks two things per template file:
  1. no value matches a known live-credential signature (provider key prefixes);
  2. no secret-ish key (SECRET/KEY/PASSWORD/TOKEN/URL-with-userinfo) holds a value that
     lacks a placeholder marker.
"""
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# A value is a placeholder if it carries any of these markers.
PLACEHOLDER_MARKERS = ("<", "[", "xxx", "XXX", "your", "YOUR", "changeme", "CHANGEME",
                       "example", "EXAMPLE", "placeholder", "PLACEHOLDER", "replace",
                       "REPLACE", "dummy", "generate", "GENERATE")

# Provider signatures that only ever appear on a real credential.
LIVE_SIGNATURES = [
    re.compile(r"sk-ant-api\d{2}-[A-Za-z0-9_\-]{20,}"),   # Anthropic
    re.compile(r"\bsk_live_[A-Za-z0-9]{20,}"),            # Stripe live
    re.compile(r"\bsk_test_[A-Za-z0-9]{20,}"),            # Stripe test (still a live key)
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),       # Slack
    re.compile(r"\bghp_[A-Za-z0-9]{30,}"),                # GitHub PAT
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                  # AWS access key id
    re.compile(r"\bmd_[A-Za-z0-9_\-]{30,}"),              # Mindee
]

# Anchored at the end so MAX_ARTICLES_PER_KEYWORD / TOKEN_VALIDITY_HOURS are not secrets.
SECRETISH_KEY = re.compile(r"(SECRET|KEY|KEYS|APIKEY|PASSWORD|PASSWD|TOKEN|CREDENTIALS?)$", re.I)
NON_SECRET_VALUE = re.compile(r"^(\d+(\.\d+)?|true|false|yes|no|on|off)$", re.I)
URL_WITH_USERINFO = re.compile(r"://[^/\s:@]+:([^@\s]+)@")


def _tracked_env_examples():
    out = subprocess.check_output(
        ["git", "ls-files", "*.env.example", "*env.example", "*.env.sample", "*.env.template"],
        cwd=REPO_ROOT, text=True,
    )
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


def _is_placeholder(value: str) -> bool:
    return any(marker in value for marker in PLACEHOLDER_MARKERS)


def _entries(path: Path):
    for lineno, raw in enumerate(path.read_text(errors="replace").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        yield lineno, key.strip(), value.strip().strip("'\"")


def test_env_example_files_are_discovered():
    """Sanity: the glob must actually find the templates, or the test proves nothing."""
    found = _tracked_env_examples()
    assert found, "no *.env.example files found — the guard would silently pass"
    assert "backend/.env.example" in found


@pytest.mark.parametrize("rel_path", _tracked_env_examples())
def test_no_live_credential_signature(rel_path):
    """No committed template may contain a provider-issued credential."""
    text = (REPO_ROOT / rel_path).read_text(errors="replace")
    hits = [sig.pattern for sig in LIVE_SIGNATURES if sig.search(text)]
    assert not hits, (
        f"{rel_path} contains what looks like a REAL credential "
        f"(matched {hits}). Rotate it, then replace the value with a placeholder."
    )


@pytest.mark.parametrize("rel_path", _tracked_env_examples())
def test_secret_valued_keys_are_placeholders(rel_path):
    """Every SECRET/KEY/PASSWORD/TOKEN value, and every URL userinfo, must be a placeholder."""
    offenders = []
    for lineno, key, value in _entries(REPO_ROOT / rel_path):
        if not value:
            continue
        if NON_SECRET_VALUE.match(value):
            continue
        if SECRETISH_KEY.search(key) and not _is_placeholder(value):
            offenders.append(f"{rel_path}:{lineno} {key}")
        userinfo = URL_WITH_USERINFO.search(value)
        if userinfo and not _is_placeholder(userinfo.group(1)):
            offenders.append(f"{rel_path}:{lineno} {key} (credential embedded in URL)")

    assert not offenders, (
        "committed template holds non-placeholder secret values:\n  "
        + "\n  ".join(offenders)
        + "\nSee CLAUDE.md → 'Secrets Handling (MANDATORY)'."
    )
