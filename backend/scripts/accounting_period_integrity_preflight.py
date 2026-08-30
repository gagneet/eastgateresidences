#!/usr/bin/env python3
# @featuretrace:financial_integration_v2 — Read-only cross-scheme integrity audit for
#   finance.accounting_periods, run before/after the date-aware period-resolution fix
#   (migration 0073) to prove no new effective_on/period mismatches were introduced.
# Layer: cron
# Data flow: finance.accounting_periods + finance.journal_entries (all schemes, read-only).
# Related: backend/alembic/versions/0073_accounting_period_integrity.py
#          backend/services/financial_core/adapters/db_postgres/ledger_repo.py
#          tasks/GAP-FIN-018-east-gate-demo-bank-to-ledger-pipeline.md
"""Global, read-only accounting-period integrity report across every scheme.

Checks (all schemes, not just East Gate):
  1. Invalid `status` values (outside open/reconciling/closed/audited/locked).
  2. Overlapping [starts_on, ends_on] date ranges per (tenant_id, scheme_id).
  3. Duplicate (tenant_id, scheme_id, period_label) pairs.
  4. Journal entries whose effective_on falls outside their own period's range.
  5. Journal entries whose period_id belongs to a different tenant/scheme than
     the journal itself (orphan / cross-tenant reference).
  6. Periods with NULL or invalid (ends_on < starts_on) boundaries.

Exit code 0 = clean, 1 = violations found. Never writes anything.

Usage:
    python3 scripts/accounting_period_integrity_preflight.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from db_postgres.session import async_session_context  # noqa: E402

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"
_VALID_STATUSES = {"open", "reconciling", "closed", "audited", "locked"}


_CHECKS = [
    (
        "Invalid status values",
        """
        SELECT period_id::text, tenant_id::text, scheme_id::text, period_label, status
        FROM finance.accounting_periods
        WHERE status NOT IN ('open','reconciling','closed','audited','locked')
        """,
    ),
    (
        "Overlapping date ranges (same tenant+scheme)",
        """
        SELECT a.tenant_id::text, a.scheme_id::text,
               a.period_id::text AS period_a, a.period_label AS label_a,
               b.period_id::text AS period_b, b.period_label AS label_b
        FROM finance.accounting_periods a
        JOIN finance.accounting_periods b
          ON a.tenant_id = b.tenant_id AND a.scheme_id = b.scheme_id
         AND a.period_id < b.period_id
         AND daterange(a.starts_on, a.ends_on, '[]') && daterange(b.starts_on, b.ends_on, '[]')
        """,
    ),
    (
        "Duplicate (tenant_id, scheme_id, period_label)",
        """
        SELECT tenant_id::text, scheme_id::text, period_label, COUNT(*) AS n
        FROM finance.accounting_periods
        GROUP BY tenant_id, scheme_id, period_label
        HAVING COUNT(*) > 1
        """,
    ),
    (
        "Journal effective_on outside its period's range",
        """
        SELECT je.journal_entry_id::text, je.scheme_id::text, je.period_id::text,
               je.effective_on, ap.period_label, ap.starts_on, ap.ends_on
        FROM finance.journal_entries je
        JOIN finance.accounting_periods ap ON ap.period_id = je.period_id
        WHERE je.effective_on < ap.starts_on OR je.effective_on > ap.ends_on
        """,
    ),
    (
        "Journal/period tenant-scheme cross-reference mismatches",
        """
        SELECT je.journal_entry_id::text, je.tenant_id::text, je.scheme_id::text,
               ap.tenant_id::text AS period_tenant_id, ap.scheme_id::text AS period_scheme_id
        FROM finance.journal_entries je
        JOIN finance.accounting_periods ap ON ap.period_id = je.period_id
        WHERE je.tenant_id != ap.tenant_id OR je.scheme_id != ap.scheme_id
        """,
    ),
    (
        "NULL / invalid period boundaries",
        """
        SELECT period_id::text, tenant_id::text, scheme_id::text, period_label, starts_on, ends_on
        FROM finance.accounting_periods
        WHERE starts_on IS NULL OR ends_on IS NULL OR ends_on < starts_on
        """,
    ),
]


async def run() -> int:
    # finance.accounting_periods and finance.journal_entries have NO RLS
    # bypass clause (confirmed live: SET app.tenant_id to the bypass id
    # returns 0 rows even though real data exists) — unlike core.schemes/
    # core.tenants. Must iterate the real tenant_id of every scheme and run
    # each check under that tenant's own RLS context, aggregating results.
    # See CLAUDE.md: "an ad-hoc connection with no app.tenant_id set silently
    # returns 0 rows, not an error."
    totals = {label: 0 for label, _ in _CHECKS}
    samples: dict[str, list] = {label: [] for label, _ in _CHECKS}

    async with async_session_context() as session:
        await session.execute(text(f"SELECT set_config('app.tenant_id', '{_TENANT_BYPASS_ID}', true)"))
        tenant_rows = (
            await session.execute(text("SELECT DISTINCT tenant_id::text FROM core.schemes"))
        ).fetchall()

    tenant_ids = [r[0] for r in tenant_rows]
    print(f"Auditing {len(tenant_ids)} tenant(s)...")

    for tenant_id in tenant_ids:
        async with async_session_context() as session:
            await session.execute(text(f"SELECT set_config('app.tenant_id', '{tenant_id}', true)"))
            for label, sql in _CHECKS:
                rows = (await session.execute(text(sql))).fetchall()
                totals[label] += len(rows)
                if len(samples[label]) < 20:
                    samples[label].extend(dict(r._mapping) for r in rows[: 20 - len(samples[label])])

    violations = 0
    for i, (label, _) in enumerate(_CHECKS, start=1):
        print(f"[{i}] {label}: {totals[label]}")
        for s in samples[label]:
            print("    ", s)
        violations += totals[label]

    print()
    if violations:
        print(f"FAIL — {violations} total violation(s) found across {len(tenant_ids)} tenant(s).")
        return 1
    print(f"PASS — no violations found across {len(tenant_ids)} tenant(s).")
    return 0


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
