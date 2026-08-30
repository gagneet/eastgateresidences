"""Correct stale `finance.trust_accounts` display fields for building 13195.

BACKGROUND
══════════
`finance.trust_accounts` for scheme 13195 (East Gate Residences) has exactly 2
rows (admin + capital-works fund), both created 2026-05-04 during the
Phase F-Zero Reset with placeholder values (`bank_name='strata_web_statement_manual'`,
identical generic `account_name` on both rows). Neither of the two Postgres
cutover scripts (`migrate_building_13195.py` step 06, `trust_migration.py`
`_migrate_trust_accounts`) produced these rows — their deterministic-ID schemes
don't match the stored `trust_account_id`s. Mongo's `trust_accounts_v2` was
later corrected with real bank details (Macquire Bank, a distinguishing
"— Capital Works Fund" suffix on the sinking account), but nothing re-synced
Postgres, because:
  - `migrate_building_13195.py` step 06 short-circuits entirely once any row
    exists for the scheme — it never re-checks field values on reruns.
  - `trust_migration.py`'s account ID is content-derived from
    (account_name, bank_name) — since those very fields changed in Mongo,
    rerunning it would compute a *different* ID and INSERT two new rows
    rather than fix the existing two, making things worse.

This script performs a targeted, idempotent UPDATE instead: it matches the 2
Mongo `trust_accounts_v2` docs to their Postgres rows by `fund_id` (the one
field that's already reliably correct — admin vs. sinking are properly
distinguished), and updates only `bank_name` and `account_name` — the two
fields with a confirmed, substantive mismatch. `masked_bsb`/
`masked_account_number` are deliberately left alone: Mongo's `bsb` field
holds the *unmasked* BSB, and blindly copying it into the `masked_*` column
would overwrite a correctly-masked value with an unmasked one (a PII
regression), while Mongo has no `account_number` value at all for either
account. No inserts, no deletes, no schema change. This script does not touch
`journal_entries`/`receipts`/`levy_items` (already verified consistent with
Mongo as of this writing).

USAGE
═════
    cd backend
    venv/bin/python3 scripts/fix_13195_trust_account_metadata.py            # dry-run (default)
    venv/bin/python3 scripts/fix_13195_trust_account_metadata.py --apply    # writes + audit log

Requires MONGO_URL, DB_NAME, DATABASE_URL in backend/.env (loaded automatically
via config.py / database.py, same as the rest of the app).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from db_postgres.engine import get_engine, dispose_engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("fix_13195_trust_accounts")

BUILDING_ID = "13195"  # This script is a one-off correction scoped to this scheme only.

FUND_KEY_MAP = {
    "admin_fund": "admin",
    "sinking_fund": "sinking",
    "capital_works_fund": "sinking",
}


def _mongo_db():
    """Generated function header.

    Function: _mongo_db
    Path: backend/scripts/fix_13195_trust_account_metadata.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    import motor.motor_asyncio

    mongo_url = os.environ["MONGO_URL"]
    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    return client, client[os.environ["DB_NAME"]]


def _compute_account_diff(
    fund_key: str,
    trust_account_id,
    mongo_acct: dict,
    pg_current: dict,
) -> tuple[dict, dict]:
    """Compute the SQL SET payload and full diff for one trust account row.

    Only bank_name/account_name are ever candidates for correction — see the module
    docstring for why masked_bsb/masked_account_number are deliberately excluded (Mongo's
    bsb is unmasked; copying it in would overwrite a correctly-masked Postgres value).
    Falsy Mongo values (missing/empty bank_name or account_name) are never candidates,
    so this can only ever narrow toward the current Postgres value, never null it out.

    Returns (changed_fields, diff):
      - changed_fields: the SQL SET payload — empty dict if nothing differs from pg_current.
      - diff: the full old/new pair for every *candidate* field (whether or not it
        changed), used for the dry-run log line and the audit_events payload.
    """
    candidate_fields: dict[str, str] = {}
    mongo_bank_name = mongo_acct.get("bank_name")
    if mongo_bank_name:
        candidate_fields["bank_name"] = mongo_bank_name
    mongo_account_name = mongo_acct.get("account_name")
    if mongo_account_name:
        candidate_fields["account_name"] = mongo_account_name

    changed_fields = {k: v for k, v in candidate_fields.items() if v != pg_current[k]}

    diff = {
        "trust_account_id": str(trust_account_id),
        "fund_type": fund_key,
        **{k: (pg_current[k], v) for k, v in candidate_fields.items()},
    }
    return changed_fields, diff


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/fix_13195_trust_account_metadata.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the correction (default: dry-run, prints diff only).")
    args = parser.parse_args()

    mongo_client, db = _mongo_db()
    engine = get_engine()

    try:
        mongo_accounts = await db.trust_accounts_v2.find(
            {"building_id": BUILDING_ID, "is_test_data": {"$ne": True}}
        ).to_list(None)
        if len(mongo_accounts) != 2:
            logger.error(
                "Expected exactly 2 trust_accounts_v2 docs for building %s, found %d. "
                "Aborting — this script assumes the known admin/sinking pair.",
                BUILDING_ID, len(mongo_accounts),
            )
            return

        by_fund_key: dict[str, dict] = {}
        for acct in mongo_accounts:
            raw_type = (acct.get("account_type") or acct.get("fund_type") or "").lower()
            fund_key = FUND_KEY_MAP.get(raw_type)
            if fund_key is None:
                logger.error("Unrecognised account_type/fund_type %r on Mongo doc %s — aborting.", raw_type, acct.get("_id"))
                return
            by_fund_key[fund_key] = acct

        async with engine.connect() as conn:
            await conn.execute(text("SET app.tenant_id = '00000000-0000-0000-0000-000000000000'"))
            scheme_row = (
                await conn.execute(
                    text("SELECT tenant_id, scheme_id FROM core.schemes WHERE scheme_number = :bid"),
                    {"bid": BUILDING_ID},
                )
            ).first()
            if scheme_row is None:
                logger.error("No core.schemes row found for scheme_number=%s — aborting.", BUILDING_ID)
                return
            tenant_id, scheme_id = scheme_row

        async with engine.connect() as conn:
            # finance.* tables have no RLS bypass clause (see CLAUDE.md) — the real
            # tenant_id is required, not the 00000000-... bypass UUID. Postgres SET
            # does not accept bind parameters, so this is a literal string — safe here
            # because tenant_id is a UUID object from a prior trusted query, not
            # user input.
            await conn.execute(text(f"SET app.tenant_id = '{tenant_id}'"))

            fund_rows = (
                await conn.execute(
                    text("SELECT fund_id, fund_type FROM finance.funds WHERE scheme_id = :sid"),
                    {"sid": str(scheme_id)},
                )
            ).all()
            fund_id_by_key = {row.fund_type: row.fund_id for row in fund_rows}

            pg_rows = (
                await conn.execute(
                    text(
                        "SELECT trust_account_id, fund_id, bank_name, account_name "
                        "FROM finance.trust_accounts WHERE scheme_id = :sid"
                    ),
                    {"sid": str(scheme_id)},
                )
            ).all()

            updates = []
            for fund_key, mongo_acct in by_fund_key.items():
                fund_id = fund_id_by_key.get(fund_key)
                if fund_id is None:
                    logger.error("No finance.funds row for fund_type=%s — aborting.", fund_key)
                    return
                pg_row = next((r for r in pg_rows if r.fund_id == fund_id), None)
                if pg_row is None:
                    logger.error(
                        "No finance.trust_accounts row for fund_id=%s (fund_type=%s) — "
                        "this script only corrects existing rows, it does not insert. Aborting.",
                        fund_id, fund_key,
                    )
                    return

                pg_current = {
                    "bank_name": pg_row.bank_name,
                    "account_name": pg_row.account_name,
                }
                changed_fields, diff = _compute_account_diff(
                    fund_key, pg_row.trust_account_id, mongo_acct, pg_current
                )
                updates.append((pg_row.trust_account_id, changed_fields, diff))

            logger.info("Planned corrections (%d rows):", len(updates))
            for _trust_account_id, changed_fields, diff in updates:
                for field_name, value in diff.items():
                    if field_name in ("trust_account_id", "fund_type"):
                        continue
                    old, new = value
                    marker = "→ CHANGE" if field_name in changed_fields else "  (unchanged)"
                    logger.info("  [%s / %s] %s: %r %s %r", diff["trust_account_id"], diff["fund_type"], field_name, old, marker, new)

            rows_with_changes = [(taid, cf, diff) for taid, cf, diff in updates if cf]
            if not rows_with_changes:
                logger.info("No changes needed — Postgres already matches Mongo.")
                return

            if not args.apply:
                logger.info("Dry-run only — no writes made. Re-run with --apply to write these changes.")
                return

            # NOTE: conn already has an implicit transaction open (SQLAlchemy AsyncConnection
            # auto-begins on the first execute() — triggered above by the SET/SELECT calls),
            # so this must NOT call conn.begin() again (raises InvalidRequestError: "This
            # connection has already initialized a SQLAlchemy Transaction()"). Commit
            # explicitly instead; an exception before commit leaves the connection's implicit
            # transaction uncommitted, and closing the connection without committing rolls
            # back automatically.
            for trust_account_id, changed_fields, diff in rows_with_changes:
                set_clause = ", ".join(f"{k} = :{k}" for k in changed_fields)
                await conn.execute(
                    text(f"UPDATE finance.trust_accounts SET {set_clause} WHERE trust_account_id = :taid"),
                    {**changed_fields, "taid": str(trust_account_id)},
                )
                await conn.execute(
                    text(
                        "INSERT INTO core.audit_events "
                        "(tenant_id, scheme_id, entity_type, entity_id, action, event_payload) "
                        "VALUES (:tenant_id, :scheme_id, 'finance_trust_account', :entity_id, "
                        "'finance.trust_account.metadata_corrected', CAST(:payload AS jsonb))"
                    ),
                    {
                        "tenant_id": str(tenant_id),
                        "scheme_id": str(scheme_id),
                        "entity_id": str(trust_account_id),
                        "payload": json.dumps({
                            "reason": "Stale placeholder values from 2026-05-04 Phase F-Zero Reset "
                                      "never synced against corrected trust_accounts_v2 Mongo source.",
                            "diff": diff,
                            "source": "scripts/fix_13195_trust_account_metadata.py",
                        }),
                    },
                )
            await conn.commit()
            logger.info("Applied %d corrections and logged audit_events rows.", len(rows_with_changes))
    finally:
        mongo_client.close()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
