#!/bin/bash
#
# setup_cron_jobs.sh — Register all scheduled cron jobs for the strata management platform.
#
# All times are UTC. The server runs at UTC+10 (AEST) / UTC+11 (AEDT in summer).
# Comments show the local AEST equivalent.
#
# Usage:
#   bash backend/cron/setup_cron_jobs.sh
#
# Notes:
#   - cron_maintenance_intelligence.py is a continuous background loop started at
#     FastAPI startup (@app.on_event("startup")) — NOT registered here.
#   - cron_levy_reminders.py is registered three times with different arguments
#     for quarterly notices, weekly arrears tier-1, and bi-monthly arrears tier-2.
#   - Idempotent: existing East Gate cron lines are removed before re-adding.

SCRIPT_DIR="/home/gagneet/strata-management/backend"
PYTHON_BIN="$SCRIPT_DIR/venv/bin/python3"
LOG_DIR="$SCRIPT_DIR/logs"

mkdir -p "$LOG_DIR"

echo "====================================="
echo " Strata Platform — Cron Job Setup"
echo "====================================="
echo ""

echo "Backing up existing crontab..."
crontab -l > /tmp/crontab_backup_$(date +%Y%m%d_%H%M%S).txt 2>/dev/null || true

# Strip all previous strata platform cron lines (idempotent reinstall).
#
# Two passes are needed, not one:
#
#  1. Lines between the sentinel markers — the block this script manages.
#  2. Legacy strata lines OUTSIDE the markers, installed by hand or by a version
#     of this script that predates the sentinels (East Gate's crontab carried
#     entries dated 2026-02-17 and 2026-02-27).
#
# Pass 1 alone preserved those legacy lines and then appended the managed block
# on top, so five jobs ended up scheduled TWICE — including
# cron_payment_reminders, which generates levy-notice PDFs and emails owners.
# Two duplicate runs fired 33ms apart and produced a duplicate notice per unit
# (and, since that job has no send-log, a duplicate email to the owner).
#
# The legacy sweep is deliberately narrow: it matches only lines invoking this
# repo's own cron directory, so unrelated jobs sharing the crontab (Firefly III,
# backups, anything the operator added) are untouched. Comment lines are left
# alone — a stale comment is harmless, and deleting the operator's notes is not
# this script's business.
FULL_CRON=$(crontab -l 2>/dev/null || echo "")
CURRENT_CRON=$(echo "$FULL_CRON" \
  | awk '/# BEGIN_STRATA_CRON/{found=1; next} /# END_STRATA_CRON/{found=0; next} !found{print}' \
  | grep -v -E '^[^#].*strata-management/backend/cron/')

NEW_CRON="$CURRENT_CRON

# BEGIN_STRATA_CRON — managed by setup_cron_jobs.sh — do not edit manually
# ─── Strata Platform — Scheduled Jobs (last updated $(date +%Y-%m-%d)) ───────────

# ── High-frequency ────────────────────────────────────────────────────────────────

# Auto-approve guest/tenant registrations if admin takes no action within threshold
*/5 * * * * $PYTHON_BIN $SCRIPT_DIR/cron/cron_admin_auto_approve.py >> $LOG_DIR/admin_auto_approve.log 2>&1

# Escalate pending owner-approval requests to super admins (hourly)
0 * * * * $PYTHON_BIN $SCRIPT_DIR/cron/cron_approval_escalation.py >> $LOG_DIR/approval_escalation.log 2>&1

# Finance shadow-parity traffic — drives the finance_ledger shadow-diff comparisons that
# feed the 7-consecutive-clean-day Phase D soak metric (3x/day so a single-day outage
# doesn't stall the soak; 08:00/13:00/18:00 UTC = 18:00/23:00/04:00 AEST)
7 8,13,18 * * * $PYTHON_BIN $SCRIPT_DIR/cron/cron_finance_shadow_traffic.py >> $LOG_DIR/finance_shadow_traffic.log 2>&1

# ── Daily (UTC midnight–09:00, = AEST 10:00–19:00) ────────────────────────────────

# Notification cleanup — purge old read notifications (02:00 UTC = 12:00 AEST)
0 2 * * * $PYTHON_BIN $SCRIPT_DIR/cron/cron_notification_cleanup.py >> $LOG_DIR/notification_cleanup.log 2>&1

# Finance recompute — rebuild fund totals and forecasts (02:00 UTC = 12:00 AEST)
0 2 * * * $PYTHON_BIN $SCRIPT_DIR/cron/cron_finance_recompute.py >> $LOG_DIR/finance_recompute.log 2>&1

# Occupancy recompute — refresh vacancy stats and unit occupancy (02:00 UTC = 12:00 AEST)
0 2 * * * $PYTHON_BIN $SCRIPT_DIR/cron/cron_occupancy_recompute.py >> $LOG_DIR/occupancy_recompute.log 2>&1

# Account expiration check — disable expired trial/temp accounts (03:00 UTC = 13:00 AEST)
0 3 * * * $PYTHON_BIN $SCRIPT_DIR/cron/cron_expiration_check.py >> $LOG_DIR/expiration_check.log 2>&1

# Payment reminders — send levy due/overdue reminder emails (04:00 UTC = 14:00 AEST)
0 4 * * * $PYTHON_BIN $SCRIPT_DIR/cron/cron_payment_reminders.py >> $LOG_DIR/payment_reminders.log 2>&1

# PM scheduler — process preventive maintenance job triggers (06:00 UTC = 16:00 AEST)
0 6 * * * $PYTHON_BIN $SCRIPT_DIR/cron/cron_pm_scheduler.py >> $LOG_DIR/pm_scheduler.log 2>&1

# Property listings scraper — fetch new listings from external sources (06:00 UTC = 16:00 AEST)
0 6 * * * $PYTHON_BIN $SCRIPT_DIR/cron/cron_property_scraper.py >> $LOG_DIR/property_scraper.log 2>&1

# Insurance renewal alerts — check policy expiry dates (08:00 UTC = 18:00 AEST)
0 8 * * * $PYTHON_BIN $SCRIPT_DIR/cron/cron_insurance.py >> $LOG_DIR/insurance.log 2>&1

# Adaptive nav recompute — update personalised navigation weights (17:00 UTC = 03:00 AEST next day)
0 17 * * * $PYTHON_BIN $SCRIPT_DIR/cron/cron_adaptive_nav.py >> $LOG_DIR/adaptive_nav.log 2>&1

# PPM notifications — preventive maintenance due/overdue alerts (21:00 UTC = 07:00 AEST next day)
0 21 * * * $PYTHON_BIN $SCRIPT_DIR/cron/cron_ppm_notifications.py >> $LOG_DIR/ppm_notifications.log 2>&1

# ── Weekly ─────────────────────────────────────────────────────────────────────────

# Levy reminders — arrears tier-1 notices (every Monday 09:00 UTC = 19:00 AEST)
0 9 * * 1 $PYTHON_BIN $SCRIPT_DIR/cron/cron_levy_reminders.py --action=reminders --tier=1 >> $LOG_DIR/levy_reminders_arrears_t1.log 2>&1

# Suburb market radar — scrape suburb price/auction data (Sunday 07:00 UTC = 17:00 AEST)
0 7 * * 0 $PYTHON_BIN $SCRIPT_DIR/cron/cron_suburb_radar.py >> $LOG_DIR/suburb_radar.log 2>&1

# Weekly notification digest — send Sunday summary to owners/tenants (Sunday 08:00 UTC = 18:00 AEST)
0 8 * * 0 $PYTHON_BIN $SCRIPT_DIR/cron/cron_notification_digest.py >> $LOG_DIR/notification_digest.log 2>&1

# ── Monthly ────────────────────────────────────────────────────────────────────────

# Trust account interest accrual — 1st of each month (20:00 UTC = 06:00 AEST next day)
0 20 1 * * $PYTHON_BIN $SCRIPT_DIR/cron/cron_trust_interest.py >> $LOG_DIR/trust_interest.log 2>&1

# NSW strata compliance checks — deadlines, AGM notices, by-law reviews (1st of month 09:00 UTC)
0 9 1 * * $PYTHON_BIN $SCRIPT_DIR/cron/cron_nsw_compliance.py >> $LOG_DIR/nsw_compliance.log 2>&1

# Levy reminders — arrears tier-2 bi-monthly notices (1st and 15th of month 10:00 UTC)
0 10 1,15 * * $PYTHON_BIN $SCRIPT_DIR/cron/cron_levy_reminders.py --action=reminders --tier=2 >> $LOG_DIR/levy_reminders_arrears_t2.log 2>&1

# ── Quarterly ──────────────────────────────────────────────────────────────────────

# Levy reminders — quarterly levy notice dispatch (1st of Jan/Apr/Jul/Oct 08:00 UTC)
0 8 1 1,4,7,10 * $PYTHON_BIN $SCRIPT_DIR/cron/cron_levy_reminders.py --action=levy_run >> $LOG_DIR/levy_reminders_quarterly.log 2>&1

# ── Weekly (specific day) ──────────────────────────────────────────────────────────

# News scraper — fetch building/community news articles (Saturday 01:00 UTC)
0 1 * * 6 $PYTHON_BIN $SCRIPT_DIR/cron/cron_news_scraper.py >> $LOG_DIR/news_scraper.log 2>&1

# END_STRATA_CRON
"

echo "$NEW_CRON" | crontab -

echo ""
echo "✓ All cron jobs installed successfully!"
echo ""
echo "Schedule summary (UTC / AEST):"
echo "  */5 min     admin_auto_approve"
echo "  hourly      approval_escalation"
echo "  02:00 UTC   notification_cleanup, finance_recompute, occupancy_recompute"
echo "  03:00 UTC   expiration_check"
echo "  04:00 UTC   payment_reminders"
echo "  06:00 UTC   pm_scheduler, property_scraper"
echo "  08:00 UTC   insurance"
echo "  17:00 UTC   adaptive_nav  (03:00 AEST)"
echo "  21:00 UTC   ppm_notifications  (07:00 AEST)"
echo "  Mon 09:00   levy_reminders (arrears tier-1)"
echo "  Sun 07:00   suburb_radar  (17:00 AEST)"
echo "  Sun 08:00   notification_digest  (18:00 AEST)"
echo "  1st 20:00   trust_interest  (06:00 AEST)"
echo "  1st 09:00   nsw_compliance"
echo "  1st+15th    levy_reminders (arrears tier-2)"
echo "  Quarterly   levy_reminders (quarterly notices)"
echo "  Sat 01:00   news_scraper"
echo ""
echo "Logs: $LOG_DIR"
echo ""
echo "To verify:  crontab -l"
echo "To edit:    crontab -e"
echo ""
echo "Note: cron_maintenance_intelligence.py is a continuous loop started"
echo "      by FastAPI startup — do NOT add it here."
echo "====================================="
