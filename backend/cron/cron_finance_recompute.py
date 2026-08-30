"""
Nightly Financial Intelligence Recompute Job

Runs nightly (default: 2:00 AM AEDT) and recomputes:
  1. Category expense forecasts for the current + next financial year
  2. Levy projection for the current year
  3. Anomaly detection scan for the current year
  4. Lot financial summaries (true cost of ownership) for all units
  5. Cashflow projections (cached in financial_forecasts)
  6. DCA (debt collection agency) eligibility flags
  7. Special-levy-forecast + levy-stability risk model caches (force-refresh,
     otherwise these have no cron-driven invalidation at all)

Designed to run without blocking API requests.  All operations are async.

Usage (standalone):
    python cron_finance_recompute.py [--year 2026] [--dry-run]

Cron schedule (via system crontab or setup_cron_jobs.sh):
    0 2 * * * /usr/bin/python /path/to/cron_finance_recompute.py >> /var/log/finance_recompute.log 2>&1
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import argparse
import asyncio
import logging

# ── Path setup ────────────────────────────────────────────────────────────────
# Allow direct execution from the cron directory
_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from database import db
from utils.finance_helpers import get_latest_levy_year

log = logging.getLogger("finance_recompute")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def _recompute_forecasts(year: str, dry_run: bool, building_id: str) -> int:
    """Generate category expense forecasts for the given year."""
    from services.forecast_service import generate_category_forecast
    if dry_run:
        log.info("[DRY RUN] Would generate category forecasts for %s (building=%s)", year, building_id)
        return 0
    forecasts = await generate_category_forecast(year, building_id=building_id)
    log.info("  ✅ Generated %d category forecast(s) for %s (building=%s)", len(forecasts), year, building_id)
    return len(forecasts)


async def _recompute_levy_projection(year: str, dry_run: bool, building_id: str) -> None:
    """Compute 3-year levy rate projections."""
    from services.forecast_service import generate_levy_projection
    if dry_run:
        log.info("[DRY RUN] Would generate levy projection for %s (building=%s)", year, building_id)
        return
    result = await generate_levy_projection(year, building_id)
    years = list(result.get("combined", {}).keys())
    log.info("  ✅ Levy projection computed for %s → %s (building=%s)", year, years, building_id)


async def _recompute_anomalies(year: str, dry_run: bool, building_id: str) -> int:
    """Re-scan for financial anomalies."""
    from services.anomaly_service import detect_financial_anomalies
    if dry_run:
        log.info("[DRY RUN] Would scan anomalies for %s (building=%s)", year, building_id)
        return 0
    anomalies = await detect_financial_anomalies(year, building_id)
    log.info("  ✅ Anomaly scan: found %d anomalie(s) for %s (building=%s)", len(anomalies), year, building_id)
    return len(anomalies)


async def _recompute_lot_summaries(year: str, dry_run: bool, building_id: str) -> int:
    """Recompute true cost of ownership for all units."""
    from services.lot_finance_service import compute_building_cost_distribution
    if dry_run:
        unit_count = await db.units.count_documents({"building_id": building_id})
        log.info(
            "[DRY RUN] Would compute lot summaries for %d units in %s (building=%s)",
            unit_count,
            year,
            building_id,
        )
        return 0
    summaries = await compute_building_cost_distribution(year, building_id)
    log.info("  ✅ Lot summaries computed for %d units in %s (building=%s)", len(summaries), year, building_id)
    return len(summaries)


async def _cache_cashflow(year: str, dry_run: bool, building_id: str) -> None:
    """Cache cashflow projection for the year."""
    from services.forecast_service import generate_cashflow_projection
    if dry_run:
        log.info("[DRY RUN] Would cache cashflow projection for %s (building=%s)", year, building_id)
        return
    cashflow = await generate_cashflow_projection(year, building_id)
    risk_count = len(cashflow.get("risk_months", []))
    cashflow_category = "__cashflow__"
    cashflow_fund_type = "cashflow"
    # Persist to financial_forecasts for fast retrieval
    await db.financial_forecasts.update_one(
        {
            "financial_year": year,
            "forecast_type": "cashflow",
            "building_id": building_id,
            "category": cashflow_category,
            "fund_type": cashflow_fund_type,
        },
        {"$set": {
            "financial_year": year,
            "forecast_type": "cashflow",
            "building_id": building_id,
            "category": cashflow_category,
            "fund_type": cashflow_fund_type,
            "data": cashflow,
            "risk_month_count": risk_count,
            "recomputed_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
    log.info(
        "  ✅ Cashflow cached for %s — %d risk month(s), "
        "income=$%.2f, expenses=$%.2f",
        year,
        risk_count,
        cashflow.get("annual_income", 0),
        cashflow.get("annual_expenses", 0),
    )


async def _check_dca_eligibility(year: str, dry_run: bool, building_id: str) -> int:
    """
    Check for units eligible for Debt Collection Agency (DCA) referral.
    Rule: Arrears > 2 quarters (approx $1,500+) AND first notice sent > 14 days ago.
    """
    if dry_run:
        log.info("[DRY RUN] Would check DCA eligibility for %s (building=%s)", year, building_id)
        return 0

    # 1. Fetch ledger for current year to find arrears
    ledger_entries = await db.unit_levy_ledger.find(
        {"year": year, "building_id": building_id, "net_balance": {"$gt": 1500}},
        {"unit_number": 1, "net_balance": 1}
    ).to_list(100)

    if not ledger_entries:
        return 0

    eligible_count = 0
    now = datetime.now(timezone.utc)

    for entry in ledger_entries:
        unit_num = entry["unit_number"]
        unit = await db.units.find_one({"building_id": building_id, "unit_number": unit_num}, {"arrears_metadata": 1})
        if not unit:
            continue

        meta = unit.get("arrears_metadata", {})
        # Skip units already flagged for DCA or beyond "none".
        # NOTE: the condition was previously `!= "none"` which skips units where dca_status
        # is missing (None != "none" is True) — i.e. all fresh arrears units. Corrected to
        # only skip units whose status is already set to something actionable.
        dca_status = meta.get("dca_status")
        if dca_status and dca_status != "none":
            continue

        first_notice_str = meta.get("first_notice_sent_at")
        if not first_notice_str:
            continue

        try:
            first_notice_date = datetime.fromisoformat(first_notice_str)
            days_diff = (now - first_notice_date).days

            if days_diff >= 14:
                # Mark as eligible
                await db.units.update_one(
                    {"building_id": building_id, "unit_number": unit_num},
                    {"$set": {"arrears_metadata.dca_status": "eligible", "updated_at": now.isoformat()}}
                )
                eligible_count += 1
                log.info("  👉 Unit %s marked as DCA ELIGIBLE (Arrears: $%.2f, Notice sent: %d days ago)",
                         unit_num, entry["net_balance"], days_diff)
        except (ValueError, TypeError):
            continue

    return eligible_count


async def _recompute_risk_models(year: str, dry_run: bool, building_id: str) -> dict:
    """Force-refresh the special-levy-forecast and levy-stability caches.

    Both `special_levy_forecasts` and `levy_stability_snapshots` are
    request-triggered, cache-forever collections with no other invalidation
    hook — without this step they can silently keep serving a stale forecast
    for months after sinking_fund_plan/capital_replacement_schedule/units
    change underneath them. force=True bypasses the cache-hit check and
    recomputes+overwrites.

    Both calls are individually isolated: unlike steps 1-6, this is new,
    less-exercised code being run unattended across every building overnight
    (the two underlying compute functions were originally written for a
    single logged-in user's GET request against one known-good building, not
    a batch sweep that may hit an edge-case building — e.g. mid-onboarding,
    zero units, no sinking_fund_plan yet). A failure here must not be allowed
    to mark steps 1-6's already-successful work as "error" for this building.
    """
    if dry_run:
        log.info("[DRY RUN] Would recompute special-levy-forecast + levy-stability for building=%s", building_id)
        return {"special_levy": "dry_run", "levy_stability": "dry_run"}

    result = {"special_levy": "ok", "levy_stability": "ok"}
    try:
        from services.special_levy_service import compute_special_levy_forecast
        await compute_special_levy_forecast(building_id, force=True)
    except Exception as exc:
        result["special_levy"] = "error"
        log.error("  ⚠️  special-levy-forecast refresh failed for building=%s: %s", building_id, exc)

    try:
        from services.levy_stability_service import compute_levy_stability_snapshot
        await compute_levy_stability_snapshot(building_id, force=True)
    except Exception as exc:
        result["levy_stability"] = "error"
        log.error("  ⚠️  levy-stability refresh failed for building=%s: %s", building_id, exc)

    if result["special_levy"] == "ok" and result["levy_stability"] == "ok":
        log.info("  ✅ Special levy forecast + levy stability snapshot refreshed (building=%s)", building_id)
    return result


async def run_recompute(year: str, dry_run: bool = False, building_id: str = None) -> dict:
    """
    Execute the full nightly recompute pipeline.

    Args:
        year: Financial year string (e.g. "2026")
        dry_run: When True, logs actions without executing them.

    Returns:
        Summary dict with counts of items recomputed.
    """
    started_at = datetime.now(timezone.utc)
    log.info("=== Finance Recompute START (year=%s, dry_run=%s, building=%s) ===", year, dry_run, building_id)

    summary = {
        "year": year,
        "building_id": building_id,
        "dry_run": dry_run,
        "started_at": started_at.isoformat(),
        "forecasts": 0,
        "anomalies": 0,
        "lot_summaries": 0,
    }

    try:
        log.info("Step 1/5: Category forecasts …")
        summary["forecasts"] = await _recompute_forecasts(year, dry_run, building_id)

        log.info("Step 2/5: Levy projection …")
        await _recompute_levy_projection(year, dry_run, building_id)

        log.info("Step 3/5: Anomaly scan …")
        summary["anomalies"] = await _recompute_anomalies(year, dry_run, building_id)

        log.info("Step 4/5: Lot financial summaries …")
        summary["lot_summaries"] = await _recompute_lot_summaries(year, dry_run, building_id)

        log.info("Step 5/6: Cashflow cache …")
        await _cache_cashflow(year, dry_run, building_id)

        log.info("Step 6/7: DCA eligibility check …")
        summary["dca_eligible"] = await _check_dca_eligibility(year, dry_run, building_id)

        log.info("Step 7/7: Risk model refresh (special levy forecast + levy stability) …")
        summary["risk_models"] = await _recompute_risk_models(year, dry_run, building_id)

        elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
        summary["elapsed_seconds"] = round(elapsed, 2)
        summary["status"] = "success"
        log.info(
            "=== Finance Recompute COMPLETE (%.2fs) — forecasts=%d anomalies=%d lots=%d (building=%s) ===",
            elapsed, summary["forecasts"], summary["anomalies"], summary["lot_summaries"], building_id,
        )

        if not dry_run:
            # Record run in a cron_runs collection for observability
            await db.cron_runs.insert_one({
                "job": "finance_recompute",
                "year": year,
                "building_id": building_id,
                "status": "success",
                "forecasts": summary["forecasts"],
                "anomalies": summary["anomalies"],
                "lot_summaries": summary["lot_summaries"],
                "risk_models": summary.get("risk_models"),
                "elapsed_seconds": summary["elapsed_seconds"],
                "ran_at": started_at.isoformat(),
            })

    except Exception as exc:
        log.exception("Finance recompute FAILED: %s", exc)
        summary["status"] = "error"
        summary["error"] = str(exc)
        if not dry_run:
            await db.cron_runs.insert_one({
                "job": "finance_recompute",
                "year": year,
                "building_id": building_id,
                "status": "error",
                "error": str(exc),
                "ran_at": started_at.isoformat(),
            })

    return summary


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/cron/cron_finance_recompute.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Nightly financial intelligence recompute")
    parser.add_argument("--year", help="Financial year (default: latest from DB)")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without executing")
    args = parser.parse_args()

    building_override = os.environ.get("BUILDING_ID")
    if building_override:
        building_ids = [building_override]
    else:
        buildings = await db.buildings.find({"is_active": True}, {"_id": 0, "id": 1}).to_list(200)
        building_ids = [b["id"] for b in buildings if b.get("id")]
        if not building_ids:
            log.error("No active buildings found in DB — aborting finance recompute (set BUILDING_ID to target one)")
            return

    for building_id in building_ids:
        year = args.year
        if not year:
            year = await get_latest_levy_year(building_id)
        if not year:
            year = str(datetime.now(timezone.utc).year)
            log.warning("No levy year found in DB — defaulting to %s (building=%s)", year, building_id)

        await run_recompute(year=year, dry_run=args.dry_run, building_id=building_id)


if __name__ == "__main__":
    asyncio.run(main())
