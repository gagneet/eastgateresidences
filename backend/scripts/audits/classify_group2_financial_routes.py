#!/usr/bin/env python3
# @featuretrace:scoped-capability-access — Tiers the group 2 financial route surface by what a wrong guard costs.
# Layer: docs
# Data flow: backend/routers/*.py (read-only parse) -> tier A/B/C assignment -> Markdown/JSON report (global).
# Related: backend/services/capability_registry.py
#          docs/security/acl_information_access_implementation_plan.md
# Tests: tests/backend/test_server_inline_route_classification.py
#         tests/backend/test_group2_tier_a_migration.py
"""Inventory GAP-SEC-005 group 2 — the financial surface — by risk tier.

Why this exists
---------------
GAP-SEC-005 sizes group 2 at 330 routes, the largest of the twelve groups. That
count is a planning figure, not a work queue: it lumps "read the levy summary"
together with "release a payment" and "change a supplier's bank details", which
carry entirely different consequences if the guard is wrong.

Migrating 330 routes in one pass would also break the task's own rule — keep the
existing check, add the capability, and observe it allowing the right people
before moving on. That is not something a single change can honour at this size.

So this script splits group 2 into three tiers by what a wrong answer costs:

    A  money leaves, or bank/payment credentials are read or written, or
       financial data is exported in bulk
    B  financial state is written — levies, budgets, journals, invoices,
       reconciliation
    C  financial state is read

Tier A is migrated first and separately. B and C follow, so that a mistake in the
bulk of the work cannot be the mistake that moves money.

It is read-only. Usage:

    backend/venv/bin/python3 backend/scripts/audits/classify_group2_financial_routes.py
    backend/venv/bin/python3 backend/scripts/audits/classify_group2_financial_routes.py --tier A
    backend/venv/bin/python3 backend/scripts/audits/classify_group2_financial_routes.py --format json
    backend/venv/bin/python3 backend/scripts/audits/classify_group2_financial_routes.py --check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ROUTERS = REPO_ROOT / "backend" / "routers"

#: Router files that make up group 2. Derived from GAP-SEC-005's own grouping
#: plus a sweep for finance-domain prefixes; listed explicitly so a new finance
#: router has to be added deliberately rather than drifting in unnoticed.
GROUP2_FILES = (
    "finance.py",
    "finance_intelligence.py",
    "finance_reconciliation.py",
    "finance_reports.py",
    "financial_import.py",
    "financial_matching.py",
    "invoices.py",
    "invoice_ocr.py",
    "trust_accounting.py",
    "trust_phase1.py",
    "trust_reconciliation.py",
    "ap_approval.py",
    "ap_supplier_upload.py",
    "bank_feeds.py",
    "mock_bank_feed.py",
    "payment_plans.py",
    "special_payments.py",
    "arrears_recovery.py",
    "arrears_risk.py",
    "levy_reminders.py",
    "levy_scenarios.py",
    "settlement_adjustment.py",
    "three_way_matching.py",
    "water_bills.py",
    "demo_bank.py",
    "owner_finance.py",
)

# ── Tier rules, most dangerous first ─────────────────────────────────────────
#
# Order matters within each set: a route that touches bank details is tier A even
# if its path also says "view", because reading a BSB is the disclosure the access
# matrix reserves for the dual-control payment flow.

#: Matched against "METHOD /path handler_name" ONLY — never the signature.
#:
#: Matching these against the signature picked up prose: the description string
#: "Bank CSV export file" made an IMPORT route look like an export, and a query
#: parameter named ``bank_account_id`` made a reconciliation LIST look like a
#: bank-detail read. A tier is a claim about what a route does, so it must come
#: from what the route IS, not from words that happen to sit near it.
TIER_A_PATH_PATTERNS = (
    r"bank[-_]?(account|detail|token)",
    r"\bbsb\b",
    r"payment[-_]?(release|execute|initiat|disburs)",
    r"\brelease\b",
    r"\bdisburse",
    r"\baba\b",              # ABA payment file generation
    r"remittance",
    r"withdraw",
    r"\bexport\b",
    r"\bdownload\b",
    r"supplier[-_]?(bank|payment)",
    r"vendor[-_]?(bank|payment)",
    r"approve[-_]?(invoice|payment)",
    r"trust.*(withdraw|transfer|payment)",
)

#: Matched against TYPE ANNOTATIONS in the signature, after string literals are
#: stripped. The strongest tier-A signal is often the request model rather than
#: the URL: ``PATCH /accounts/{account_id}`` reads as ordinary CRUD until you
#: notice its payload is ``BankAccountUpdate``. Case-sensitive — these are class
#: names, and lowercasing them is what let prose match in the first place.
TIER_A_MODEL_PATTERNS = (
    r"\bBankAccount(Update|Create)\b",
    r"\bTrustAccount\w*(Create|Update)\b",
    r"\bPaymentBatch\w*\b",
    r"\bAbaFile\w*\b",
    r"\bDemoBankAccount\w*\b",
)

#: Union, for callers and tests that want the whole tier-A rule set.
TIER_A_PATTERNS = TIER_A_PATH_PATTERNS + TIER_A_MODEL_PATTERNS

TIER_B_PATTERNS = (
    r"^(POST|PUT|PATCH|DELETE)\b",   # any financial write not caught above
)

#: Tier-A routes deliberately NOT given a capability guard, with the reason.
#: ``--check`` treats these as satisfied. An entry here is a recorded decision;
#: anything else failing the check is an unmigrated route.
TIER_A_EXCLUSIONS: dict[str, str] = {
    "lookup_bsb": (
        "Looks up an Australian BSB against the public findbsb.com.au directory and "
        "returns a bank/branch name. It reads no building data, discloses nothing "
        "about any account, and carries no building scope at all — there is no "
        "get_current_building on the route. Attaching a building-scoped capability "
        "would deny every user without a resolvable building context in exchange for "
        "no confidentiality gain. Flagged tier A by the \\bbsb\\b pattern on the path, "
        "which is right to be broad; this is the false positive it costs."
    ),
}

#: Docstrings and quoted descriptions are stripped before model matching, so a
#: parameter description can never decide a route's risk tier.
_STRING_LITERAL = re.compile(
    r'"""[\s\S]*?"""' r"|'''[\s\S]*?'''" r'|"[^"\n]*"' r"|'[^'\n]*'"
)


def _annotations_only(signature: str) -> str:
    """Return the signature with every string literal blanked out."""
    return _STRING_LITERAL.sub(" ", signature)


DECORATOR = re.compile(r"^@router\.(get|post|put|patch|delete)\(")
PATH_LITERAL = re.compile(r"""["']([^"']*)["']""")
DEF_LINE = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)")
DEPENDS = re.compile(r"Depends\(\s*([\w.]+)")


def _tier(method: str, path: str, func: str, signature: str = "") -> str:
    """Return 'A', 'B' or 'C' for one route.

    The signature is included because the strongest tier-A signal is often the
    request MODEL, not the URL. ``PATCH /accounts/{account_id}`` reads as ordinary
    CRUD; its payload type ``BankAccountUpdate`` is what makes it a bank-detail
    change, which the access matrix puts under dual control and the separation-of-
    duty rule (plan §4.7) treats as a distinct trust boundary from approving the
    next payment to that supplier.
    """
    identity = f"{method} {path} {func}"
    for pattern in TIER_A_PATH_PATTERNS:
        if re.search(pattern, identity, re.IGNORECASE):
            return "A"

    annotations = _annotations_only(signature)
    for pattern in TIER_A_MODEL_PATTERNS:
        if re.search(pattern, annotations):  # case-sensitive: class names
            return "A"

    for pattern in TIER_B_PATTERNS:
        if re.search(pattern, identity, re.IGNORECASE):
            return "B"
    return "C"


def _guard_style(guards: list[str]) -> str:
    """Summarise how a route is currently protected."""
    joined = " ".join(guards)
    if "require_capability" in joined:
        return "capability"
    if "require_permission" in joined:
        return "permission"
    if "require_role" in joined:
        return "role"
    if "require_feature" in joined:
        return "feature"
    if "get_optional_user" in joined:
        return "optional-user"
    if any(k in joined for k in ("get_current_user", "get_current_active_user", "get_approved_user")):
        return "authenticated"
    if "building" in joined:
        return "building-only"
    return "none"


def scan_file(path: Path) -> list[dict]:
    """Parse every ``@router`` decorator in one router file."""
    lines = path.read_text().splitlines()
    out: list[dict] = []
    for index, line in enumerate(lines):
        match = DECORATOR.match(line.strip())
        if not match:
            continue
        method = match.group(1).upper()

        route_path = ""
        for probe in range(index, min(index + 5, len(lines))):
            found = PATH_LITERAL.search(lines[probe])
            if found:
                route_path = found.group(1)
                break

        cursor = index
        while cursor < len(lines) and not DEF_LINE.match(lines[cursor]):
            cursor += 1
        func = DEF_LINE.match(lines[cursor]).group(1) if cursor < len(lines) else ""

        signature: list[str] = []
        depth = 0
        probe = cursor
        while probe < len(lines):
            signature.append(lines[probe])
            depth += lines[probe].count("(") - lines[probe].count(")")
            if probe > cursor and depth <= 0:
                break
            probe += 1
        guards = sorted(set(DEPENDS.findall("\n".join(signature))))

        signature_text = "\n".join(signature)
        out.append({
            "file": path.name,
            "line": index + 1,
            "method": method,
            "path": route_path,
            "func": func,
            "guards": guards,
            "guard_style": _guard_style(guards),
            "tier": _tier(method, route_path, func, signature_text),
        })
    return out


def scan() -> list[dict]:
    """Scan every group-2 router file that exists."""
    routes: list[dict] = []
    for name in GROUP2_FILES:
        path = ROUTERS / name
        if path.exists():
            routes.extend(scan_file(path))
    return routes


def render(routes: list[dict], only_tier: str | None = None) -> str:
    """Generated function header.

    Function: render
    Path: backend/scripts/audits/classify_group2_financial_routes.py
    """
    out: list[str] = []
    tiers = Counter(r["tier"] for r in routes)
    styles = Counter(r["guard_style"] for r in routes)

    out.append(f"Group 2 — financial surface: {len(routes)} routes in "
               f"{len({r['file'] for r in routes})} router files\n")
    out.append("| Tier | Meaning | Routes |")
    out.append("|---|---|---|")
    out.append(f"| A | money moves, bank/payment credentials, bulk export | {tiers['A']} |")
    out.append(f"| B | financial state written | {tiers['B']} |")
    out.append(f"| C | financial state read | {tiers['C']} |")
    out.append("")
    out.append("| Current guard | Routes |")
    out.append("|---|---|")
    for style, count in styles.most_common():
        out.append(f"| {style} | {count} |")
    out.append("")

    by_tier: dict[str, list[dict]] = defaultdict(list)
    for route in routes:
        by_tier[route["tier"]].append(route)

    for tier in ("A", "B", "C"):
        if only_tier and tier != only_tier:
            continue
        members = sorted(by_tier[tier], key=lambda r: (r["file"], r["path"]))
        out.append(f"### Tier {tier} — {len(members)} routes\n")
        out.append("| File | Line | Method | Path | Handler | Current guard |")
        out.append("|---|---|---|---|---|---|")
        for r in members:
            out.append(f"| `{r['file']}` | {r['line']} | {r['method']} | `{r['path']}` "
                       f"| `{r['func']}` | {r['guard_style']} |")
        out.append("")
    return "\n".join(out)


def main() -> int:
    """Generated function header.

    Function: main
    Path: backend/scripts/audits/classify_group2_financial_routes.py
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--tier", choices=("A", "B", "C"))
    parser.add_argument(
        "--check", action="store_true",
        help="Exit non-zero if any tier-A route lacks a capability guard.",
    )
    args = parser.parse_args()

    routes = scan()

    if args.check:
        unguarded = [
            r for r in routes
            if r["tier"] == "A"
            and r["guard_style"] != "capability"
            and r["func"] not in TIER_A_EXCLUSIONS
        ]
        if unguarded:
            print(f"{len(unguarded)} tier-A financial route(s) without a capability guard:",
                  file=sys.stderr)
            for r in unguarded:
                print(f"  {r['file']}:{r['line']} {r['method']} {r['path']}", file=sys.stderr)
            return 1
        tier_a = [r for r in routes if r["tier"] == "A"]
        guarded = sum(1 for r in tier_a if r["guard_style"] == "capability")
        excluded = sum(1 for r in tier_a if r["func"] in TIER_A_EXCLUSIONS)
        print(
            f"Tier A: {len(tier_a)} routes — {guarded} carry a capability guard, "
            f"{excluded} documented exclusion(s)."
        )
        for r in tier_a:
            if r["func"] in TIER_A_EXCLUSIONS:
                print(f"  excluded: {r['file']}:{r['line']} {r['method']} {r['path']}")
        return 0

    if args.format == "json":
        print(json.dumps(routes, indent=2))
        return 0

    print(render(routes, args.tier))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
