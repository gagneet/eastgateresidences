"""Reconcile East Gate's (13195) real AGM financial records against Mongo and
Postgres.

BACKGROUND
══════════
`docs/finances/up13195_agm_proposed_spent_financials.csv` is a hand-verified,
source-cited transcription of East Gate's real AGM minutes/papers (Dec 2020 –
Mar 2026): proposed Admin/Sinking fund budgets, adopted budgets, actual spend
by category, arrears at meeting dates, and special levies. This script is a
read-only reconciliation report — it makes NO writes anywhere. It exists to
surface deltas for human review, the same way the 2026-07-25 FY2021/FY2022
correction (`scripts/finances/update_2021_2022_levy_data.py`) was found: by
comparing a verified source document against what's actually stored, then
deciding with the user which deltas are real bugs vs. expected structural
differences (the DB's chart of accounts is coarser than an AGM paper's
category list by design) vs. narrative CSV notes with no DB-side equivalent
at all.

Per this repo's MANDATORY financial-formula-verification policy: neither
Mongo nor Postgres is treated as ground truth here — the CSV is (the user
has explicitly verified it against the real AGM source documents), and both
stores are checked independently against it, not against each other.

USAGE
═════
    cd backend
    venv/bin/python3 scripts/validate_east_gate_agm_csv.py

Requires MONGO_URL, DB_NAME, DATABASE_URL in backend/.env.
"""
from __future__ import annotations

import asyncio
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from db_postgres.engine import get_engine, dispose_engine  # noqa: E402

BUILDING_ID = "13195"
_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"
CSV_PATH = Path(__file__).resolve().parents[2] / "docs" / "finances" / "up13195_agm_proposed_spent_financials.csv"

FUND_ROW_TYPES = {
    "Proposed Fund Total",
    "Actual Fund Total",
    "Adopted Budget Fund Total",
}


def _load_csv_fund_rows() -> dict[tuple[str, str], list[dict]]:
    """Group every *_Fund Total_ row by (levy_year_covered, fund)."""
    by_key: dict[tuple[str, str], list[dict]] = {}
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["record_type"] not in FUND_ROW_TYPES:
                continue
            year = row["levy_year_covered"]
            fund = row["fund"]
            if year.startswith("LY") and fund in ("Administrative", "Sinking"):
                by_key.setdefault((year[2:], fund), []).append(row)
    return by_key


def _load_csv_arrears_rows() -> list[dict]:
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if row["record_type"] == "Arrears/Receivables"]


def _load_csv_category_totals() -> dict[tuple[str, str, str], float]:
    """Sum CSV category-level rows by (year, fund, record_type) for a coarse
    cross-check against DB category aggregates — the DB's chart of accounts
    is known to be coarser than the AGM papers' category lists, so this is a
    sanity total, not a line-by-line match target."""
    totals: dict[tuple[str, str, str], float] = {}
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if "Category" not in row["record_type"]:
                continue
            year, fund = row["levy_year_covered"], row["fund"]
            if not year.startswith("LY") or fund not in ("Administrative", "Sinking"):
                continue
            try:
                amt = float(row["amount_aud"])
            except (ValueError, TypeError):
                continue
            key = (year[2:], fund, row["record_type"])
            totals[key] = totals.get(key, 0.0) + amt
    return totals


async def _mongo_annual_levies() -> dict[str, dict]:
    import motor.motor_asyncio

    client = motor.motor_asyncio.AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "strata_production")]
    docs = await db.annual_levies.find({"plan_id": BUILDING_ID}).to_list(None)
    client.close()
    return {d["year"]: d for d in docs}


async def _mongo_category_totals() -> dict[tuple[str, str], tuple[float, float]]:
    """(year, fund_type) -> (sum_budgeted, sum_actual) from levy_categories."""
    import motor.motor_asyncio

    client = motor.motor_asyncio.AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "strata_production")]
    cats = await db.levy_categories.find({"plan_id": BUILDING_ID}).to_list(None)
    client.close()
    totals: dict[tuple[str, str], tuple[float, float]] = {}
    for c in cats:
        key = (c["year"], c.get("fund_type", "?"))
        b, a = totals.get(key, (0.0, 0.0))
        totals[key] = (b + float(c.get("budgeted_amount") or 0), a + float(c.get("actual_amount") or 0))
    return totals


async def _postgres_levy_and_expense_totals(conn, tenant_id, scheme_id) -> dict:
    await conn.execute(text(f"SET app.tenant_id = '{tenant_id}'"))

    levy_rows = (
        await conn.execute(
            text(
                """
                SELECT lr.financial_year AS yr, f.fund_type::text AS fund_type,
                       sum(li.principal_cents + li.gst_cents) AS levied_cents,
                       sum(li.paid_cents) AS paid_cents
                FROM finance.levy_runs lr
                JOIN finance.levy_items li ON li.levy_run_id = lr.levy_run_id
                JOIN finance.funds f ON f.fund_id = li.fund_id
                WHERE lr.scheme_id = :sid
                GROUP BY lr.financial_year, f.fund_type
                """
            ),
            {"sid": str(scheme_id)},
        )
    ).all()

    expense_rows = (
        await conn.execute(
            text(
                """
                SELECT EXTRACT(YEAR FROM et.transaction_date)::int::text AS yr,
                       f.fund_type::text AS fund_type,
                       sum(et.amount_cents + et.gst_cents) AS spent_cents
                FROM finance.expense_transactions et
                JOIN finance.funds f ON f.fund_id = et.fund_id
                WHERE et.scheme_id = :sid
                GROUP BY 1, 2
                """
            ),
            {"sid": str(scheme_id)},
        )
    ).all()

    arrears_rows = (
        await conn.execute(
            text(
                """
                SELECT sum(li.principal_cents + li.gst_cents + li.interest_cents
                           + li.recovery_costs_cents - li.paid_cents) AS arrears_cents,
                       count(DISTINCT li.lot_id) AS lots
                FROM finance.levy_items li
                WHERE li.scheme_id = :sid
                  AND (li.principal_cents + li.gst_cents + li.interest_cents
                       + li.recovery_costs_cents - li.paid_cents) > 0
                """
            ),
            {"sid": str(scheme_id)},
        )
    ).first()

    return {
        "levy": {(r.yr, r.fund_type): (int(r.levied_cents or 0), int(r.paid_cents or 0)) for r in levy_rows},
        "expense": {(r.yr, r.fund_type): int(r.spent_cents or 0) for r in expense_rows},
        "arrears_current_cents": int(arrears_rows.arrears_cents or 0) if arrears_rows else 0,
        "arrears_current_lots": int(arrears_rows.lots or 0) if arrears_rows else 0,
    }


def _fmt(v) -> str:
    if v is None:
        return "—"
    return f"${v:,.2f}"


async def main() -> None:
    csv_fund_rows = _load_csv_fund_rows()
    csv_category_totals = _load_csv_category_totals()
    csv_arrears = _load_csv_arrears_rows()

    mongo_levies = await _mongo_annual_levies()
    mongo_cats = await _mongo_category_totals()

    engine = get_engine()
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f"SET app.tenant_id = '{_TENANT_BYPASS_ID}'"))
            scheme_row = (
                await conn.execute(
                    text("SELECT tenant_id, scheme_id FROM core.schemes WHERE scheme_number = :bid"),
                    {"bid": BUILDING_ID},
                )
            ).first()
            tenant_id, scheme_id = scheme_row

        async with engine.connect() as conn:
            pg = await _postgres_levy_and_expense_totals(conn, tenant_id, scheme_id)

        print("=" * 100)
        print("EAST GATE (13195) — AGM CSV RECONCILIATION REPORT")
        print(f"Source: {CSV_PATH}")
        print("=" * 100)

        for year in ("2021", "2022", "2023", "2024", "2025", "2026"):
            print(f"\n{'─' * 100}\nFY{year}\n{'─' * 100}")

            mdoc = mongo_levies.get(year)
            for fund_label, fund_key, fund_type in (
                ("Administrative", "admin_fund", "admin"),
                ("Sinking", "sinking_fund", "sinking"),
            ):
                print(f"\n  [{fund_label} Fund]")
                rows = csv_fund_rows.get((year, fund_label), [])
                if not rows:
                    print("    CSV: (no Fund Total rows for this year/fund)")
                else:
                    print("    CSV rows (all labeled variants — pick the one matching the basis you need):")
                    for r in rows:
                        print(f"      [{r['record_type']}] {r['category']}: {_fmt(_safe_float(r['amount_aud']))}")

                mfund = (mdoc or {}).get(fund_key, {})
                print(
                    f"    Mongo annual_levies[{year}].{fund_key}: "
                    f"levy_income={_fmt(mfund.get('levy_income'))} "
                    f"total_expenses={_fmt(mfund.get('total_expenses'))} "
                    f"opening={_fmt(mfund.get('opening_balance'))} "
                    f"closing={_fmt(mfund.get('closing_balance'))} "
                    f"(data_origin={mdoc.get('data_origin') if mdoc else '—'})"
                )

                levied, paid = pg["levy"].get((year, fund_type), (None, None))
                spent = pg["expense"].get((year, fund_type))
                print(
                    f"    Postgres: levied={_fmt(levied / 100 if levied is not None else None)} "
                    f"paid={_fmt(paid / 100 if paid is not None else None)} "
                    f"expense_transactions_spent={_fmt(spent / 100 if spent is not None else None)}"
                    + ("" if spent is not None else "  [no Postgres expense data posted for this year yet]")
                )

                mb, ma = mongo_cats.get((year, fund_type if fund_type != "admin" else "administrative"), (None, None))
                csv_actual_cat_total = csv_category_totals.get((year, fund_label, "Actual Category"))
                csv_proposed_cat_total = csv_category_totals.get((year, fund_label, "Proposed Category"))
                print(
                    f"    Category sums — CSV proposed={_fmt(csv_proposed_cat_total)} "
                    f"CSV actual={_fmt(csv_actual_cat_total)} | Mongo levy_categories budgeted={_fmt(mb)} "
                    f"actual={_fmt(ma)}  [structural: DB category granularity may differ from AGM papers]"
                )

        print(f"\n{'─' * 100}\nARREARS AT MEETING DATE (CSV) vs CURRENT Postgres arrears\n{'─' * 100}")
        print(
            "  NOTE: get_arrears_summary()-style Postgres arrears below is CURRENT (today), not "
            "as-of the historical AGM meeting date — no as_of_date parameter exists for a true "
            "historical replay. Only useful as a sanity magnitude check, not a row-by-row match."
        )
        print(
            f"  Postgres arrears (current, all years, unpaid levy_items): "
            f"{_fmt(pg['arrears_current_cents'] / 100)} across {pg['arrears_current_lots']} lot(s)"
        )
        for r in csv_arrears:
            amt = _safe_float(r["amount_aud"])
            print(f"    [{r['meeting_date']}] {r['category']}: {_fmt(amt)}  — {r['notes'][:90]}")

        print(f"\n{'=' * 100}\nEnd of report — no writes made.\n{'=' * 100}")
    finally:
        await dispose_engine()


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    asyncio.run(main())
