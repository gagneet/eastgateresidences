#!/usr/bin/env python3
# @featuretrace:scoped-capability-access — Classifies server.py's inline routes into GAP-SEC-005 migration groups.
# Layer: docs
# Data flow: backend/server.py (read-only parse) -> group/tier assignment -> Markdown/JSON report (global).
# Related: backend/services/capability_registry.py
#          docs/security/acl_information_access_implementation_plan.md
# Tests: tests/backend/test_server_inline_route_classification.py
#         tests/backend/test_group2_tier_a_migration.py
"""Classify ``server.py``'s inline routes into the GAP-SEC-005 migration groups.

Why this exists
---------------
``GAP-SEC-005`` measured 189 route decorators living directly on ``server.py``'s
``api_router`` rather than in a domain router. They were catalogued as "group 12"
purely because of where they live, and the task is explicit that **group 12 cannot
be a group**: these routes span every domain in the product, so migrating them as
a block would mean applying one capability and one masking posture to blog posts,
compliance items, levy ledgers and emergency contacts alike.

This script produces the per-route classification that GAP-SEC-005 names as its
first deliverable: every inline route assigned to one of groups 1-11, so the
migration can proceed domain by domain in the agreed risk order.

It is read-only. It parses ``backend/server.py`` and writes a Markdown table.

Usage
-----
    backend/venv/bin/python3 backend/scripts/audits/classify_server_inline_routes.py
    backend/venv/bin/python3 backend/scripts/audits/classify_server_inline_routes.py --format json
    backend/venv/bin/python3 backend/scripts/audits/classify_server_inline_routes.py --check

``--check`` exits non-zero if any route is unclassified, so a newly added inline
route cannot silently escape the migration plan.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_PY = REPO_ROOT / "backend" / "server.py"

# ── Migration groups ─────────────────────────────────────────────────────────
# Names and numbering come from GAP-SEC-005's corrected order. Groups 1-8 are the
# original runbook's risk order; 9-11 were added by the coverage audit.

GROUPS: dict[int, str] = {
    1: "Organisation users, roles and building assignments",
    2: "Supplier/bank/payment/invoice/financial writes and exports",
    3: "Records, documents and the s 120A inspection workflow",
    4: "Meetings, agenda, minutes, motions, voting",
    5: "Communications and notifications",
    6: "Maintenance and work orders",
    7: "Tenancy, owner and agent data",
    8: "Physical access devices and keys",
    9: "Analytics / BI / intelligence read surface",
    10: "Platform control plane",
    11: "API-key subjects",
    0: "Public / unauthenticated by design — triage individually, do not migrate",
}

# ── Classification rules ─────────────────────────────────────────────────────
# Ordered most-specific first; the first matching pattern wins. Each pattern is
# matched against the route path, then (only if no path rule matched) against the
# handler function name.
#
# The ordering matters more than it looks. "/levy-payments" must be group 2
# (financial write) even though it contains "payment" which also appears in
# unrelated paths, and "/compliance/documents" must be group 3 (records) rather
# than group 6, because a compliance certificate is a statutory record before it
# is a maintenance artefact.

PATH_RULES: tuple[tuple[str, int], ...] = (
    # 10 — platform control plane. First, because a platform route may mention any domain.
    (r"^/admin/(diagnostics|system|migration|cutover|feature)", 10),
    (r"^/(feature-toggles|cutover|outbox|powerhouse-status)", 10),
    (r"^/site-settings", 10),
    # Scraper configuration is operator tooling over an ingestion pipeline, not a
    # building setting. Building settings below are group 10 too, but pair with
    # GAP-SEC-009 (the can_manage_settings split) rather than with the toggles screen.
    (r"^/settings/scrapers", 10),
    (r"^/settings(/|$)", 10),
    # 11 — API-key subjects.
    (r"^/external/", 11),
    # 0 — public by design.
    (r"^/$", 0),
    (r"^/(register|login|trial-request|health|status)$", 0),
    (r"^/auth/(login|register|forgot|reset|verify)", 0),
    # Token-validated self-service registration update: deliberately unauthenticated
    # (the token IS the credential) and rate-limited. Triage, do not capability-gate.
    (r"^/registration/", 0),
    (r"^/legal-pages/", 0),
    # 8 — physical access devices, ahead of group 1's /building rule which would
    # otherwise swallow /building/keys-fobs. A fob is an access device that
    # happens to be filed under the building prefix, not a role assignment.
    (r"^/building/(keys?-fobs?|access|devices?)", 8),
    # /chat/users is the chat participant picker, reachable by any approved
    # resident and already filtered by the directory visibility settings. It is a
    # communications surface, not user administration — giving it
    # building.people.view would deny chat to every owner and tenant.
    (r"^/chat/", 5),
    # 1 — organisation users, roles, building assignments.
    (r"^/admin/(create-staff-user|users|invitations|impersonat)", 1),
    (r"^/(users|user-management|roles|permissions|memberships)", 1),
    (r"^/buildings?(/|$)", 1),
    (r"^/portfolio/", 1),
    (r"^/organisations?(/|$)", 1),
    (r"reactivate|elevat|invite", 1),
    # 2 — money.
    (r"^/(finance|financial|levy|levies|trust|gl|journal|budget|invoice|payment)", 2),
    (r"^/(arrears|expenses?|banking|reconcil|receipts?)", 2),
    (r"levy-payments|annual-levies|unit-levy", 2),
    # A reimbursement request ends in money leaving the trust account, so it is a
    # financial write regardless of which form raised it.
    (r"^/requests/reimbursements", 2),
    # 3 — records and documents.
    (r"^/(documents?|records?|compliance|register|certificates?)", 3),
    (r"^/(insurance|contracts?)", 3),
    (r"s120a|inspection-request", 3),
    # By-laws and their acknowledgements are statutory records (s 107 register of
    # rules), and the document folder tree is the records filing structure itself.
    (r"^/by-laws", 3),
    (r"^/folders", 3),
    (r"^/requests/insurance-", 3),
    # 4 — meetings and governance decisions.
    (r"^/(meetings?|agm|agenda|minutes|motions?|votes?|voting|proposals?|ec-members?)", 4),
    (r"^/committee", 4),
    # /todos and /schedule read like generic productivity endpoints but both gate on
    # can_manage_meetings today: they are the committee's action register and meeting
    # calendar. Classified where they actually belong, not where the path suggests.
    (r"^/(todos|schedule)", 4),
    # 5 — communications.
    (r"^/(announcements?|notifications?|communications?|messages?|blog|newsletter)", 5),
    (r"^/(email|sms|digest|broadcast)", 5),
    # 6 — maintenance and work orders.
    (r"^/(maintenance|work-?orders?|defects?|repairs?|inspections?|assets?)", 6),
    (r"^/(quotes?|contractors?|service-providers?|vendors?)", 6),
    (r"^/outstanding-issues", 6),
    (r"^/requests/alterations", 6),
    # 7 — tenancy, owner and agent data.
    (r"^/(owners?|tenants?|residents?|tenanc|leases?|agents?|units?|lots?)", 7),
    (r"^/(directory|household|occupanc)", 7),
    (r"^/change-requests", 7),
    (r"^/requests/pets", 7),
    # ABN lookup resolves a business identity for an owner/vendor record.
    (r"^/abn/", 7),
    # 8 — physical access.
    (r"^/requests/access-control", 8),
    (r"^/(access-(devices?|keys?|cards?)|keys?|fobs?|remotes?|intercom|parcels?)", 8),
    (r"^/(amenit|bookings?|move-bookings?|facilit)", 8),
    # 9 — analytics last: an analytics path usually also names its domain, so it
    # must not shadow the domain rules above.
    (r"^/(stats|analytics|bi|intelligence|insights?|metrics|reports?|dashboards?)", 9),
    (r"^/(risk|forecast|benchmark|trends?)", 9),
    # 5 — community/social surfaces read as communications.
    (r"^/(events?|emergency-services|marketplace|community|listings?)", 5),
)

FUNC_RULES: tuple[tuple[str, int], ...] = (
    (r"compliance", 3),
    (r"levy|payment|invoice|budget|financ|arrears|trust", 2),
    (r"meeting|agm|motion|vote|minute|agenda|committee", 4),
    (r"announce|notif|blog|message|email", 5),
    (r"maintenance|work_?order|defect|quote|contractor", 6),
    (r"owner|tenant|unit|lot|resident|lease|agent", 7),
    (r"parcel|amenity|booking|key|fob", 8),
    (r"stat|analytic|report|dashboard|metric|forecast", 9),
    (r"user|role|permission|building|staff|invit", 1),
    (r"toggle|cutover|diagnostic|system", 10),
)

DECORATOR = re.compile(r"^@api_router\.(get|post|put|patch|delete)\(")
PATH_LITERAL = re.compile(r"""["'](/[^"']*)["']""")
DEF_LINE = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)")
DEPENDS = re.compile(r"Depends\(\s*([\w.]+)")


def _classify(path: str, func: str) -> tuple[int, str]:
    """Return ``(group, matched_rule)`` for one route."""
    for pattern, group in PATH_RULES:
        if re.search(pattern, path, re.IGNORECASE):
            return group, f"path:{pattern}"
    for pattern, group in FUNC_RULES:
        if re.search(pattern, func, re.IGNORECASE):
            return group, f"func:{pattern}"
    return -1, "unclassified"


def scan(source: str) -> list[dict]:
    """Parse every ``@api_router`` decorator in ``source`` into a route record."""
    lines = source.splitlines()
    routes: list[dict] = []
    for index, line in enumerate(lines):
        if not DECORATOR.match(line.strip()):
            continue
        method = DECORATOR.match(line.strip()).group(1).upper()

        # The path literal is on the decorator line for single-line decorators and
        # on one of the next few lines when the decorator is wrapped.
        path = ""
        for probe in range(index, min(index + 5, len(lines))):
            found = PATH_LITERAL.search(lines[probe])
            if found:
                path = found.group(1)
                break

        cursor = index
        while cursor < len(lines) and not DEF_LINE.match(lines[cursor]):
            cursor += 1
        func = DEF_LINE.match(lines[cursor]).group(1) if cursor < len(lines) else ""

        # Collect the signature by tracking bracket depth from the def line.
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

        group, rule = _classify(path, func)
        routes.append(
            {
                "line": index + 1,
                "method": method,
                "path": path,
                "func": func,
                "guards": guards,
                "group": group,
                "rule": rule,
            }
        )
    return routes


def _guard_style(guards: list[str]) -> str:
    """Summarise how a route is currently protected, in the inspection's vocabulary."""
    if any(g.endswith("require_capability") or "require_capability" in g for g in guards):
        return "capability"
    if any("require_permission" in g for g in guards):
        return "permission"
    if any("require_role" in g for g in guards):
        return "role"
    if any("require_feature" in g for g in guards):
        return "feature"
    if any("get_optional_user" in g for g in guards):
        return "optional-user"
    if any(("get_current_user" in g) or ("get_current_active_user" in g) or ("get_approved_user" in g) for g in guards):
        return "authenticated"
    if any(("building" in g) for g in guards):
        return "building-only"
    return "none"


def render_markdown(routes: list[dict]) -> str:
    """Render the classification as the Markdown table the plan references."""
    by_group: dict[int, list[dict]] = defaultdict(list)
    for route in routes:
        by_group[route["group"]].append(route)

    out: list[str] = []
    out.append("| Group | Routes | Currently authenticated-only or unguarded |")
    out.append("|---|---|---|")
    for group in sorted(by_group, key=lambda g: (g == 0, g == -1, g)):
        members = by_group[group]
        weak = sum(
            1
            for r in members
            if _guard_style(r["guards"])
            in {"none", "optional-user", "authenticated", "building-only"}
        )
        label = GROUPS.get(group, "UNCLASSIFIED")
        out.append(f"| {group if group >= 0 else '—'}. {label} | {len(members)} | {weak} |")
    out.append("")

    for group in sorted(by_group, key=lambda g: (g == 0, g == -1, g)):
        label = GROUPS.get(group, "UNCLASSIFIED — needs a rule")
        out.append(f"### Group {group if group >= 0 else '—'} — {label}")
        out.append("")
        out.append("| Line | Method | Path | Handler | Current guard |")
        out.append("|---|---|---|---|---|")
        for route in sorted(by_group[group], key=lambda r: r["path"]):
            out.append(
                f"| {route['line']} | {route['method']} | `{route['path']}` "
                f"| `{route['func']}` | {_guard_style(route['guards'])} |"
            )
        out.append("")
    return "\n".join(out)


def main() -> int:
    """Generated function header.

    Function: main
    Path: backend/scripts/audits/classify_server_inline_routes.py
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if any inline route is unclassified.",
    )
    args = parser.parse_args()

    routes = scan(SERVER_PY.read_text())

    if args.check:
        stragglers = [r for r in routes if r["group"] < 0]
        if stragglers:
            print(f"{len(stragglers)} unclassified inline route(s):", file=sys.stderr)
            for route in stragglers:
                print(f"  server.py:{route['line']} {route['method']} {route['path']}", file=sys.stderr)
            return 1
        print(f"All {len(routes)} inline routes classified.")
        return 0

    if args.format == "json":
        print(json.dumps(routes, indent=2))
        return 0

    counts = Counter(r["group"] for r in routes)
    print(f"<!-- {len(routes)} inline routes in backend/server.py; "
          f"{len(counts)} groups. Regenerate with this script. -->\n")
    print(render_markdown(routes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
