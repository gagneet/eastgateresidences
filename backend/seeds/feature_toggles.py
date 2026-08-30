# @featuretrace:cutover-toggle-safety — Master toggle seed: globally-safe defaults for all cutover/migration toggles.
# Layer: seed
# Data flow: seed script → core.feature_toggles (global) / core.feature_toggle_overrides (building) → require_feature() guards.
# Related: docs/architecture/feature-toggle-governance.md
#          scripts/validation/audit_feature_registry.py
#          backend/services/cutover_status_service.py
# Toggle: n/a (this IS the toggle seed)
# Table: core.feature_toggles, core.feature_toggle_overrides
# Tests: tests/backend/test_feature_registry_audit.py

"""
Seed Feature Toggles

Initializes default feature toggles for all major features in the application.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

import asyncio
from dotenv import load_dotenv
from pymongo import AsyncMongoClient

ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncMongoClient(mongo_url)
db = client[os.environ['DB_NAME']]

DEFAULT_FEATURES = [
    # ── Core ─────────────────────────────────────────────────────────────────
    {
        "feature_key": "documents",
        "feature_name": "Documents Management",
        "description": "Upload, view, and manage community documents",
        "is_enabled": True,
        "category": "core",
        "icon": "FileText",
        "routes": ["/documents"],
        "depends_on": []
    },
    {
        "feature_key": "sidebar",
        "feature_name": "Sidebar",
        "description": "Redesigned grouped and collapsible sidebar navigation",
        "is_enabled": True,
        "category": "core",
        "icon": "Layout",
        "routes": [],
        "depends_on": []
    },
    {
        "feature_key": "user_guide",
        "feature_name": "User Guide",
        "description": "Access to user guide documentation",
        "is_enabled": True,
        "category": "core",
        "icon": "BookOpen",
        "routes": ["/user-guides/index.html"],
        "depends_on": []
    },
    # ── Financial ─────────────────────────────────────────────────────────────
    {
        "feature_key": "finance",
        "feature_name": "Finance Management",
        "description": "View financial reports, budgets, and levy payments",
        "is_enabled": True,
        "category": "financial",
        "icon": "DollarSign",
        "routes": [
            "/financials/overview",
            "/financials/levy-payments",
            "/intelligence/projections",
            "/reports",
        ],
        "depends_on": []
    },
    {
        "feature_key": "approvals",
        "feature_name": "My Approvals",
        "description": "Invoice and payment approval queue",
        "is_enabled": True,
        "category": "financial",
        "icon": "ClipboardCheck",
        "routes": ["/requests/my-approvals"],
        "depends_on": ["finance"]
    },
    # ── Mock boundary (see core/toggle_classification.py: mock_boundary) ──────
    # These two are the INVERSE of the cutover toggles around them: enabled is the
    # safe state, and disabling one for a building is what points it at a real
    # financial institution. assert_global_disable_allowed blocks a global disable.
    {
        "feature_key": "financial_services_mock",
        "feature_name": "Mock Financial Services",
        "description": (
            "While enabled, this building's DEFT/BPAY, Stripe, provider-protocol and "
            "outbound payment (ABA) integrations run against their mock implementations "
            "instead of a live financial institution. Disable per-building only, once that "
            "building is ready to transact for real. Demo Bank is NOT affected — it is a "
            "first-party emulator with its own toggles."
        ),
        "is_enabled": True,  # Mock boundary: safe state is ON; disable per-building only
        "category": "financial",
        "icon": "FlaskConical",
        "routes": [],
        "roles": ["super_admin", "strata_admin", "strata_manager"],
        "depends_on": []
    },
    {
        "feature_key": "bank_direct_debit_mock",
        "feature_name": "Mock Bank Direct Debit & Transaction History",
        "description": (
            "While enabled, bank direct debit and real transaction-history retrieval are "
            "mocked for this building. Held separately from Mock Financial Services because "
            "these pull customer bank data and can debit an owner directly. No live code "
            "path consumes this yet — the switch exists ahead of the implementation."
        ),
        "is_enabled": True,  # Mock boundary: safe state is ON; disable per-building only
        "category": "financial",
        "icon": "Landmark",
        "routes": [],
        "roles": ["super_admin", "strata_admin", "strata_manager"],
        "depends_on": []
    },
    {
        "feature_key": "financial_integration_layer_v2",
        "feature_name": "Financial Integration Layer v2",
        "description": "Provider-agnostic financial integration layer with Protocol boundaries, event envelope inbox, and matching engine. Enable per building when ready to migrate off legacy scrapers.",
        "is_enabled": False,  # Cutover toggle: globally false; enable per-building via core.feature_toggle_overrides
        "category": "financial",
        "icon": "Zap",
        "routes": ["/financials/matching"],
        "roles": ["super_admin"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "demo_bank_feed_enabled",
        "feature_name": "Demo Bank Feed",
        "description": "Stateful bank-feed emulator: routes CSV uploads and Strata Web scrape data through demo_bank_transactions staging layer instead of writing directly to the financial ledger. Enable for demo buildings (UPDEMO5) and East Gate (13195). Disable for production buildings until a real CDR/Basiq provider is wired.",
        "is_enabled": False,  # Data-path toggle: globally false; enable per-building after readiness review.
        "category": "financial",
        "icon": "Database",
        "routes": ["/admin/demo-bank-transactions"],
        "roles": ["super_admin", "strata_admin", "strata_manager"],
        "depends_on": ["financial_integration_layer_v2"]
    },
    {
        "feature_key": "bank_feeds_sync_enabled",
        "feature_name": "Bank Feeds Sync to Postgres",
        "description": "Enables POST /bank-feeds/sync to promote Demo Bank transactions into finance.bank_transactions (Postgres canonical store). Cutover-sensitive: keep disabled until Demo Bank ingestion is verified to have full coverage of the building's transaction history.",
        "is_enabled": False,
        "category": "financial",
        "icon": "RefreshCw",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["demo_bank_feed_enabled"]
    },
    {
        "feature_key": "disable_strata_sync_direct_write",
        "feature_name": "Disable strata_sync Direct Ledger Write",
        "is_enabled": False,  # is_enabled=False; cutover-sensitive default
        "description": "When enabled, legacy direct-write import paths skip their writes to unit_levy_ledger/levy_payments and route through the single-intake staging layer instead (enforced in services/single_intake_service.py and routers/reconciliation.py). Only enable after Demo Bank ingestion has been verified to have full coverage of the building's transaction history and bank_feeds_sync_enabled is active.",
        "category": "financial",
        "icon": "ShieldOff",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["bank_feeds_sync_enabled"]
    },
    {
        "feature_key": "matching_auto_allocate_enabled",
        "feature_name": "Auto-Allocate Matched Payments",
        "description": "Automatically post payments to ledger when matching engine confidence exceeds the auto-match threshold. Disable to send all matches to the review queue instead.",
        "is_enabled": True,
        "category": "financial",
        "icon": "CheckCircle",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["financial_integration_layer_v2"]
    },
    {
        "feature_key": "outbound_payment_initiation_enabled",
        "feature_name": "Outbound Payment Initiation",
        "description": "Allow the platform to generate ABA payment files for outbound disbursements. Keep disabled until payment initiation workflow has been validated end-to-end.",
        "is_enabled": False,  # Default OFF per its own governance note: generates real ABA payment files; enable per-building only after the payment-initiation workflow is validated end-to-end.
        "category": "financial",
        "icon": "ArrowUpCircle",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["financial_integration_layer_v2"]
    },
    {
        "feature_key": "ap_automation",
        "feature_name": "AP Automation",
        "description": "End-to-end accounts payable pipeline: OCR invoice ingestion, ABN validation, duplicate detection, invoice lifecycle state machine, and supplier approval queue. Enable per building after validating OCR provider credentials.",
        "is_enabled": True,
        "category": "financial",
        "icon": "FileCheck",
        "routes": ["/financials/ap-approval", "/supplier/invoice-upload"],
        "roles": ["super_admin", "strata_admin", "strata_manager", "ec_member", "service_provider"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "invoice_ocr",
        "feature_name": "Invoice & Quote OCR",
        "description": "Upload invoices and quotes; Claude Vision auto-extracts vendor, amounts, GST and posts to the financial ledger.",
        "is_enabled": True,
        "category": "financial",
        "icon": "Receipt",
        "routes": ["/financials/invoices"],
        "roles": ["super_admin", "strata_admin", "strata_manager", "ec_member"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "document_ocr",
        "feature_name": "Document OCR Workspace",
        "description": "Building-scoped OCR workspace for invoices, bank statements, AGM minutes, rates notices, contracts, and other uploaded documents. Results are stored for review before redistribution or finance posting.",
        "is_enabled": True,
        "category": "core",
        "icon": "ScanText",
        "routes": ["/documents/ocr"],
        "roles": ["super_admin", "strata_admin", "strata_manager", "ec_member", "owner", "tenant", "real_estate_agent", "admin_staff"],
        "depends_on": ["documents"]
    },
    {
        "feature_key": "council_rates",
        "feature_name": "Utilities & Property Services",
        "description": "Council rates and property service billing information",
        "is_enabled": True,
        "category": "financial",
        "icon": "Landmark",
        "routes": ["/financials/council-rates"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "water_bills",
        "feature_name": "Water Bills",
        "description": "Icon Water billing and usage tracking",
        "is_enabled": True,
        "category": "financial",
        "icon": "Droplets",
        "routes": ["/financials/water-bills"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "gst_bas_ledger",
        "feature_name": "GST & BAS Ledger",
        "description": "GST/BAS reporting ledger for OC tax obligations — output tax on levies, input tax credits on expenses, GST-free supplies (water, council rates), and quarterly ATO BAS worksheet",
        "is_enabled": True,
        "category": "financial",
        "icon": "Receipt",
        "roles": ["super_admin", "strata_manager", "strata_admin"],
        "routes": ["/admin/gst-bas-ledger"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "levy_payments",
        "feature_name": "Levy Payment Portal",
        "description": "Owner levy payment submission, history, and management portal with verification workflow",
        "is_enabled": True,
        "category": "financial",
        "icon": "CreditCard",
        "routes": ["/financials/levy-payments"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "financial_projections",
        "feature_name": "Budget Projections",
        "description": "Forward-looking financial projections, levy simulations and budget planning scenarios",
        "is_enabled": True,
        "category": "financial",
        "icon": "TrendingUp",
        "routes": ["/intelligence/projections"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "collection_rate",
        "feature_name": "Collection Rate Analysis",
        "description": "Detailed levy collection rate analytics with per-unit breakdown and trend charts",
        "is_enabled": True,
        "category": "financial",
        "icon": "BarChart2",
        "routes": ["/financials/collection-rate"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "levy_kpi_dashboard",
        "feature_name": "Levy KPI Dashboard",
        "description": "Quarter-level KPI dashboard: Collection Rate, Current Quarter Collected/Unpaid, True Arrears, Admin Fund Health, Sinking Cash Coverage, Lot Compliance. Accessible from Collection Rate and Fund Balance cards.",
        "is_enabled": True,
        "category": "financial",
        "icon": "LayoutDashboard",
        "routes": ["/financials/levy-kpi"],
        "depends_on": ["finance", "collection_rate"]
    },
    {
        "feature_key": "arrears_recovery",
        "feature_name": "Debt Recovery Board",
        "description": "Overdue levy tracking, arrears recovery board, payment notices and per-unit debt analysis",
        "is_enabled": True,
        "category": "financial",
        "icon": "TrendingDown",
        "routes": ["/intelligence/debt-recovery"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "spending_categories",
        "feature_name": "Spending Categories",
        "description": "Manage and configure budget spending categories for financial tracking and reporting",
        "is_enabled": True,
        "category": "financial",
        "icon": "Tag",
        "routes": ["/financials/spending-categories"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "financial_data_import",
        "feature_name": "Financial Data Import",
        "description": "Import financial data from Excel, CSV and third-party sources into the system",
        "is_enabled": True,
        "category": "financial",
        "icon": "Upload",
        "routes": ["/admin/financial-data"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "financial_year_import",
        "feature_name": "Financial Year Import",
        "description": "Upload and import comprehensive financial year data via CSV (owner details, levy amounts, budget categories, per-unit levy status)",
        "is_enabled": True,
        "category": "financial",
        "icon": "Upload",
        "roles": ["super_admin", "strata_manager", "strata_admin"],
        "is_premium": False,
        "sort_order": 40,
        "routes": ["/admin/financial-year-import"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "trust_accounting",
        "feature_name": "Trust Accounting",
        "description": "Double-entry trust ledger: accounts, journal entries, payment batches, ABA file generation, and hash-chain audit export. Restricted to strata managers and EC roles.",
        "is_enabled": True,
        "category": "financial",
        "icon": "BookOpen",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member"],
        "routes": ["/financials/trust"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "trust_bank_accounts",
        "feature_name": "Trust Account & Bank Accounts",
        "description": "Manage per-building trust and bank account configuration including interest rates, account categories (transaction/savings/term deposit), advance levy payment interest tracking, and monthly interest income posting.",
        "is_enabled": True,
        "category": "financial",
        "icon": "Landmark",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member"],
        "is_premium": False,
        "sort_order": 45,
        "routes": ["/financials/trust-bank-accounts"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "savings",
        "feature_name": "Savings Ledger",
        "description": "Track and record community cost savings events",
        "is_enabled": True,
        "category": "financial",
        "icon": "PiggyBank",
        "routes": ["/financials/savings"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "insurance_claims",
        "feature_name": "Insurance Claims",
        "description": "Track and manage building insurance claims and settlements",
        "is_enabled": True,
        "category": "financial",
        "icon": "ShieldCheck",
        "routes": ["/insurance", "/insurance/claims"],
        "depends_on": ["finance", "maintenance"]
    },
    {
        "feature_key": "finance_intelligence",
        "feature_name": "Financial Intelligence Dashboard",
        "description": "AI-powered financial forecasting, anomaly detection, health scoring and levy simulation",
        "is_enabled": True,
        "category": "financial",
        "icon": "Brain",
        "routes": ["/intelligence/financial"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "building_financial_stress",
        "feature_name": "Building Financial Stress Early Warning",
        "description": "Predictive special levy forecasting using Reserve Adequacy Ratio, capital works gap analysis, maintenance backlog and expense trends. Shows 10-year reserve timeline and unit-level cost impact.",
        "is_enabled": True,
        "category": "financial",
        "icon": "AlertTriangle",
        "routes": ["/intelligence/building-stress"],
        "depends_on": ["finance_intelligence"]
    },
    {
        "feature_key": "stress_score",
        "feature_name": "Building Financial Stress Score",
        "description": "Phase 2 seven-component stress score (0–100) with categories: healthy, watch, elevated, critical. Components: reserve adequacy, arrears, unreconciled exposure, maintenance pressure, liquidity, capital shock.",
        "is_enabled": True,
        "category": "financial",
        "icon": "Activity",
        "roles": ["super_admin", "strata_admin", "strata_manager", "ec_member"],
        "routes": ["/intelligence/building-stress"],
        "depends_on": ["finance_intelligence"]
    },
    {
        "feature_key": "investor_dashboard",
        "feature_name": "Investor Intelligence Dashboard",
        "description": "Phase 2 investor-grade building and unit analysis: stress scores, TCO, levy trajectory, risk-adjusted yield, and investor grading (A–D).",
        "is_enabled": True,
        "category": "financial",
        "icon": "TrendingUp",
        "roles": ["super_admin", "strata_admin", "strata_manager", "ec_member", "owner"],
        "routes": ["/intelligence/investor"],
        "depends_on": ["finance_intelligence"]
    },
    {
        "feature_key": "insurance_lending_hooks",
        "feature_name": "Insurance & Lending Risk Signals",
        "description": "Phase 2 insurance risk signals and lending valuation signals for underwriters and lenders. Includes risk tier classification and lender advisory notes.",
        "is_enabled": True,
        "category": "financial",
        "icon": "Shield",
        "roles": ["super_admin", "strata_admin", "strata_manager", "ec_member"],
        "routes": ["/intelligence/insurance-lending"],
        "depends_on": ["finance_intelligence"]
    },
    {
        "feature_key": "levy_fairness",
        "feature_name": "Levy Fairness Engine",
        "description": "Show the Levy Fairness analysis page to EC members (including the Chairman) and Strata Manager (Super Admin always has access)",
        "is_enabled": True,
        "category": "financial",
        "icon": "Scale",
        "routes": ["/intelligence/levy-fairness"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "levy_fairness_owner",
        "feature_name": "Levy Fairness — Owner View",
        "description": "Allow owners to view the read-only Levy Fairness page (they cannot edit groups or recompute)",
        "is_enabled": True,
        "category": "financial",
        "icon": "Scale",
        "routes": ["/intelligence/levy-fairness"],
        "depends_on": ["levy_fairness"]
    },
    {
        "feature_key": "levy_fairness_explain",
        "feature_name": "Levy Fairness — Explain This Result",
        "description": "Show per-unit explanation drawer with facility cost breakdown drivers",
        "is_enabled": True,
        "category": "financial",
        "icon": "Scale",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member"],
        "routes": ["/intelligence/levy-fairness"],
        "depends_on": ["levy_fairness"]
    },
    {
        "feature_key": "levy_fairness_confidence",
        "feature_name": "Levy Fairness — Model Confidence Badge",
        "description": "Show data quality confidence score (High/Medium/Low) on the Overview tab",
        "is_enabled": True,
        "category": "financial",
        "icon": "Scale",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member"],
        "routes": ["/intelligence/levy-fairness"],
        "depends_on": ["levy_fairness"]
    },
    {
        "feature_key": "levy_fairness_distribution",
        "feature_name": "Levy Fairness — Distribution Histogram",
        "description": "Before/after levy distribution histogram tab",
        "is_enabled": True,
        "category": "financial",
        "icon": "Scale",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member"],
        "routes": ["/intelligence/levy-fairness"],
        "depends_on": ["levy_fairness"]
    },
    {
        "feature_key": "levy_fairness_snapshots",
        "feature_name": "Levy Fairness — Lock Reference Scenario",
        "description": "Save and restore named model snapshots",
        "is_enabled": True,
        "category": "financial",
        "icon": "Scale",
        "roles": ["super_admin"],
        "routes": ["/intelligence/levy-fairness"],
        "depends_on": ["levy_fairness"]
    },
    {
        "feature_key": "levy_fairness_audit",
        "feature_name": "Levy Fairness — Audit Trail",
        "description": "View full audit log of model changes and group edits",
        "is_enabled": True,
        "category": "financial",
        "icon": "Scale",
        "roles": ["super_admin", "strata_manager"],
        "routes": ["/intelligence/levy-fairness"],
        "depends_on": ["levy_fairness"]
    },
    {
        "feature_key": "levy_fairness_cross_subsidy",
        "feature_name": "Levy Fairness — Cross-Subsidy Report",
        "description": "Per-facility cross-subsidy table showing TH vs APT contributions",
        "is_enabled": True,
        "category": "financial",
        "icon": "Scale",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member"],
        "routes": ["/intelligence/levy-fairness"],
        "depends_on": ["levy_fairness"]
    },
    {
        "feature_key": "subsidy_map",
        "feature_name": "Levy Subsidy Map",
        "description": "Visualise cross-subsidy flows between lot groups (apartments, townhouses, etc.) based on levy payments vs benefit allocation.",
        "is_enabled": True,
        "category": "financial",
        "icon": "Map",
        "roles": ["super_admin", "strata_admin", "strata_manager", "ec_member"],
        "routes": ["/intelligence/levy-fairness"],
        "depends_on": ["levy_fairness"]
    },
    {
        "feature_key": "levy_scenario_modeller",
        "feature_name": "Levy Scenario Modeller",
        "description": "Model what-if levy scenarios: divide units into custom groups (not just A/B), assign percentage budget shares, adjust UOE values, and compute per-unit levy projections. Compare scenarios side-by-side. Owners, EC members (including the Chairman), Strata Managers and Super Admins can view and run scenarios.",
        "is_enabled": True,
        "category": "financial",
        "icon": "SlidersHorizontal",
        "roles": ["owner", "ec_member", "strata_admin", "strata_manager", "super_admin"],
        "is_premium": False,
        "sort_order": 47,
        "routes": ["/intelligence/levy-scenarios"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "payment_plans",
        "feature_name": "Payment Plans",
        "description": "NSW Form 1 payment plan portal: owners in arrears can request a formal instalment arrangement (ss.83A–83C SSMA 2015). Managers approve, decline, and track repayment progress.",
        "is_enabled": True,
        "category": "financial",
        "icon": "CreditCard",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff", "owner"],
        "routes": ["/financials/payment-plans"],
        "depends_on": ["finance", "arrears_recovery"]
    },
    {
        "feature_key": "capital_funding_workspace",
        "feature_name": "Capital Funding & Special Levy Workspace",
        "description": (
            "Preview-only staff workspace for major works funding: project requirement, "
            "special levy, strata-loan and mixed funding scenario modelling. Does not "
            "issue notices or post ledger entries."
        ),
        "is_enabled": True,
        "category": "financial",
        "icon": "Landmark",
        "roles": ["super_admin", "strata_admin", "strata_manager", "admin_staff"],
        "routes": ["/financials/capital-funding"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "capital_funding_owner_elections",
        "feature_name": "Capital Funding Owner Elections",
        "description": (
            "Owner-facing vote/election and acknowledgement portal for legally approved "
            "capital funding projects. Elections are inputs to an approved funding run, "
            "not ledger postings."
        ),
        "is_enabled": True,
        "category": "financial",
        "icon": "Vote",
        "roles": ["owner", "ec_member", "super_admin", "strata_admin", "strata_manager", "admin_staff"],
        "routes": ["/financials/capital-funding/elections"],
        "depends_on": ["capital_funding_workspace"]
    },
    {
        "feature_key": "capital_funding_notice_preview",
        "feature_name": "Capital Funding Notice Preview",
        "description": (
            "Generate levy-notice PDF preview batches from a frozen capital-funding "
            "calculation manifest. Preview only: no email, portal delivery, postal queue "
            "or ledger mutation."
        ),
        "is_enabled": True,
        "category": "financial",
        "icon": "FileText",
        "roles": ["super_admin", "strata_admin", "strata_manager", "admin_staff"],
        "routes": ["/financials/capital-funding"],
        "depends_on": ["capital_funding_workspace"]
    },
    {
        "feature_key": "capital_funding_notice_issuance",
        "feature_name": "Capital Funding Notice Issuance",
        "description": (
            "Protected write-side gate for actual capital-funding levy notice delivery. "
            "Keep disabled until legal review, demo issuance and dual approval pass."
        ),
        "is_enabled": False,
        "category": "financial",
        "icon": "Send",
        "roles": ["super_admin", "strata_admin"],
        "routes": [],
        "depends_on": ["capital_funding_notice_preview"]
    },
    {
        "feature_key": "capital_funding_ledger_posting",
        "feature_name": "Capital Funding Ledger Posting",
        "description": (
            "Protected write-side gate for posting approved capital-funding levy runs "
            "into the canonical ledger. Default disabled during finance cutover."
        ),
        "is_enabled": False,
        "category": "financial",
        "icon": "BookOpenCheck",
        "roles": ["super_admin", "strata_admin"],
        "routes": [],
        "depends_on": ["capital_funding_notice_issuance"]
    },
    {
        "feature_key": "hybrid_funding_structures",
        "feature_name": "Hybrid Funding Structures",
        "description": (
            "Allows modelling of hybrid/self-funding structures only when legal-review "
            "evidence is attached. Does not assert levy-credit validity in any jurisdiction."
        ),
        "is_enabled": True,
        "category": "financial",
        "icon": "Scale",
        "roles": ["super_admin"],
        "routes": [],
        "depends_on": ["capital_funding_workspace"]
    },
    {
        "feature_key": "fund_collections_by_unit_type_report",
        "feature_name": "Fund Collections by Unit Type",
        "description": (
            "Life-to-date Admin Fund and Sinking Fund collected totals, broken down by "
            "unit type (Apartment/Townhouse/etc). Read-only report — no ledger writes."
        ),
        "is_enabled": True,
        "category": "financial",
        "icon": "PieChart",
        "roles": ["super_admin", "strata_admin", "strata_manager", "ec_member", "admin_staff"],
        "routes": ["/financials/fund-collections-by-type"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "levy_reminders",
        "feature_name": "Levy Reminder Cadence",
        "description": "Configure pre-due and overdue levy reminder schedules per building. Set escalation tiers, delivery channels (email/in-app/SMS) and overdue threshold. Managers can trigger a dry-run to preview impacted units.",
        "is_enabled": True,
        "category": "financial",
        "icon": "Bell",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"],
        "routes": ["/settings/levy-reminders"],
        "depends_on": ["finance", "arrears_recovery"]
    },
    {
        "feature_key": "arrears_risk",
        "feature_name": "Predictive Arrears Risk",
        "description": "AI-assisted arrears risk scoring per unit — CRITICAL/HIGH/MODERATE/LOW bands based on payment history, outstanding balance, and trend signals. Finance roles only.",
        "is_enabled": True,
        "category": "financial",
        "icon": "TrendingDown",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"],
        "routes": ["/intelligence/debt-recovery/risk"],
        "depends_on": ["finance", "arrears_recovery"]
    },
    # ── Governance ────────────────────────────────────────────────────────────
    {
        "feature_key": "meetings",
        "feature_name": "Meetings",
        "description": "Schedule and manage committee meetings",
        "is_enabled": True,
        "category": "governance",
        "icon": "Calendar",
        "routes": ["/governance/meetings", "/governance/agm-voting", "/governance/todos"],
        "depends_on": []
    },
    {
        "feature_key": "decision_register",
        "feature_name": "Decision Register",
        "description": "Formal searchable record of all owners corporation and EC resolutions with DEC-YYYY-NNNN numbering, voting history, and resolution type tracking (ACT UTMA 2011 / NSW SSMA 2015).",
        "is_enabled": True,
        "category": "governance",
        "icon": "FileCheck",
        "routes": ["/governance/decisions"],
        "depends_on": ["meetings"]
    },
    {
        "feature_key": "proposals",
        "feature_name": "Proposals & Voting",
        "description": "Create and vote on community proposals",
        "is_enabled": True,
        "category": "governance",
        "icon": "Vote",
        "routes": ["/governance/proposals"],
        "depends_on": ["meetings"]
    },
    {
        "feature_key": "compliance",
        "feature_name": "Compliance Checklist",
        "description": "Building compliance tracking and regulatory checklists",
        "is_enabled": True,
        "category": "governance",
        "icon": "ClipboardCheck",
        "routes": ["/compliance"],
        "depends_on": []
    },
    {
        "feature_key": "nsw_commission_disclosures",
        "feature_name": "NSW Insurance Commission Disclosures",
        "description": "SSMA 2015 s.264 — mandatory disclosure of insurance commissions/referral fees received by strata managers. Penalty up to $110,000. Records commission events with amount, insurer, policy reference, and acknowledgement trail.",
        "is_enabled": True,
        "category": "governance",
        "icon": "FileCheck",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member"],
        "routes": ["/compliance/nsw-commissions"],
        "depends_on": ["compliance"]
    },
    {
        "feature_key": "nsw_strata_hub_returns",
        "feature_name": "NSW Strata Hub Annual Returns",
        "description": "SSMA 2015 s.182 — annual return submission to NSW Strata Hub. Penalty $3/lot/day plus up to $5,500. Tracks financial year, lot count, levy details, managing agent, and submission confirmation.",
        "is_enabled": True,
        "category": "governance",
        "icon": "Send",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"],
        "routes": ["/compliance/nsw-strata-hub"],
        "depends_on": ["compliance"]
    },
    {
        "feature_key": "building_manager_duties",
        "feature_name": "Building Manager Duties Register",
        "description": "GAP-JUR-NSW-013: SSMA 2015 s.46B — statutory register of functions delegated to a building manager. Strata managing agents must maintain this register and make it available for inspection. Penalty up to $5,500 for non-compliance.",
        "is_enabled": True,
        "category": "governance",
        "icon": "ClipboardList",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"],
        "routes": ["/compliance/building-manager-duties"],
        "depends_on": ["compliance"]
    },
    {
        "feature_key": "nsw_maintenance_schedule",
        "feature_name": "NSW Initial Maintenance Schedule",
        "description": "GAP-JUR-NSW-005: SSMA 2015 s.80 + Schedule 3 — owners corporation must prepare and maintain an initial maintenance schedule for common property. Must be reviewed at each AGM. Penalty up to $5,500.",
        "is_enabled": True,
        "category": "governance",
        "icon": "CalendarCheck",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"],
        "routes": ["/compliance/nsw-maintenance-schedule"],
        "depends_on": ["compliance", "ppm_schedule"]
    },
    {
        "feature_key": "agm_evoting",
        "feature_name": "AGM Electronic Voting",
        "description": "GAP-GOV-001: SSMA 2015 s.253B (2022 amendment) — electronic voting for general meetings including proxy caps enforcement. NSW: 5% scheme-wide proxy cap (or 1 proxy in schemes under 20 lots). ACT: role-based proxy restrictions.",
        "is_enabled": True,
        "category": "governance",
        "icon": "Vote",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member", "owner"],
        "routes": ["/governance/meetings/voting"],
        "depends_on": ["compliance"]
    },
    {
        "feature_key": "jurisdiction_rules",
        "feature_name": "Jurisdiction Rule Engine",
        "description": "GAP-INF-003: Multi-jurisdiction statutory rule engine for ACT, NSW, VIC, QLD. Encodes strata legislation constraints as data — interest rates, proxy caps, notice periods, fund transfer rules, AGM requirements. Foundational for all compliance features.",
        "is_enabled": True,
        "category": "governance",
        "icon": "Scale",
        "roles": ["super_admin", "strata_manager", "strata_admin"],
        "routes": ["/admin/jurisdictional-rules"],
        "depends_on": []
    },
    {
        "feature_key": "privacy_dsar",
        "feature_name": "Privacy & Data Subject Access Requests",
        "description": "GAP-SEC-004: Privacy Act 1988 (Cth) APP compliance — DSAR workflow, consent capture, data breach notification, and PII retention schedule. Required before national launch.",
        "is_enabled": True,
        "category": "governance",
        "icon": "ShieldCheck",
        "roles": ["super_admin", "strata_manager", "strata_admin", "admin_staff", "owner", "tenant"],
        "routes": ["/settings/privacy"],
        "depends_on": []
    },
    {
        "feature_key": "rental_certificates",
        "feature_name": "Rental Certificates",
        "description": "Request and issue ACT Section 119A Unit Title Rental Certificates (mandatory since Jan 2025)",
        "is_enabled": True,
        "category": "governance",
        "icon": "FileText",
        "routes": ["/requests/rental-certificates"],
        "depends_on": ["compliance", "owner_units_management"]
    },
    {
        "feature_key": "scheme_class_split",
        "feature_name": "Class A / Class B Scheme Split",
        "description": "Define separate Class A (apartments) and Class B (townhouses) levy schedules with independent UOE pools and per-category cost allocation under the ACT Unit Titles Act 2001",
        "is_enabled": True,
        "category": "financial",
        "icon": "Layers",
        "routes": ["/admin/scheme-classes"],
        "depends_on": ["levy_fairness"]
    },
    {
        "feature_key": "owner_units_management",
        "feature_name": "Owners & Units Management",
        "description": "Manage owner and unit data",
        "is_enabled": True,
        "category": "governance",
        "icon": "Building2",
        "routes": ["/admin/owners-units"],
        "depends_on": []
    },
    {
        "feature_key": "strata_roll",
        "feature_name": "Strata Roll",
        "description": "Official strata roll / owner register with lot entitlements, unit interests and unit details",
        "is_enabled": True,
        "category": "governance",
        "icon": "Scroll",
        "routes": ["/admin/strata-roll"],
        "depends_on": ["owner_units_management"]
    },
    {
        "feature_key": "user_management",
        "feature_name": "User Management",
        "description": "Create, edit, and manage user accounts",
        "is_enabled": True,
        "category": "governance",
        "icon": "Users",
        "routes": ["/admin/users"],
        "depends_on": []
    },
    {
        "feature_key": "tenant_approvals",
        "feature_name": "Tenant & Guest Approvals",
        "description": "Review and approve tenant and guest registration requests submitted by property owners",
        "is_enabled": True,
        "category": "governance",
        "icon": "UserCheck",
        "routes": ["/requests/tenant-approvals"],
        "depends_on": ["user_management"]
    },
    {
        "feature_key": "owner_name_verification",
        "feature_name": "Owner Name Verification",
        "description": "Flag owner users whose registered name does not closely match the strata roll (primary or secondary owner). An amber warning triangle appears in User Management next to mismatched names, helping administrators detect potential fraud, data-entry errors or name changes.",
        "is_enabled": True,
        "category": "governance",
        "icon": "AlertTriangle",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member"],
        "routes": ["/admin/users"],
        "depends_on": ["user_management"]
    },
    {
        "feature_key": "multi_unit_ownership",
        "feature_name": "Multi-Unit Ownership",
        "description": "Allow owners to register and manage multiple units in the same building under one account. Enables the Unit Switcher dropdown in the sidebar and the Add Unit flow on the Profile page. Owners can switch their active unit context without logging out.",
        "is_enabled": True,
        "category": "governance",
        "icon": "Home",
        "roles": ["owner", "super_admin", "strata_manager", "strata_admin", "ec_member"],
        "routes": ["/profile", "/dashboard"],
        "depends_on": ["user_management"]
    },
    {
        "feature_key": "change_requests",
        "feature_name": "Change Requests",
        "description": "Review and approve user change requests",
        "is_enabled": True,
        "category": "governance",
        "icon": "RefreshCw",
        "routes": ["/admin/change-requests"],
        "depends_on": ["user_management"]
    },
    {
        "feature_key": "owner_transfers",
        "feature_name": "Owner Transfers",
        "description": "Manage unit ownership transfers",
        "is_enabled": True,
        "category": "governance",
        "icon": "UserPlus",
        "routes": ["/admin/owner-transfers"],
        "depends_on": ["user_management"]
    },
    {
        "feature_key": "outstanding_issues_register",
        "feature_name": "Outstanding Issues Register",
        "description": "Shared building-wide register for open issues, EC updates, and chair notes",
        "is_enabled": True,
        "category": "governance",
        "icon": "ClipboardList",
        "routes": ["/requests/outstanding-issues"],
        "depends_on": ["meetings"]
    },
    {
        "feature_key": "tenant_renewals",
        "feature_name": "Tenant Renewals",
        "description": "Manage tenant lease renewals",
        "is_enabled": True,
        "category": "governance",
        "icon": "RefreshCw",
        "routes": ["/admin/tenant-renewals"],
        "depends_on": ["user_management"]
    },
    {
        "feature_key": "expired_accounts",
        "feature_name": "Expired Accounts",
        "description": "Manage expired user accounts",
        "is_enabled": True,
        "category": "governance",
        "icon": "UserX",
        "routes": ["/admin/expired-accounts"],
        "depends_on": ["user_management"]
    },
    {
        "feature_key": "conflict_of_interest",
        "feature_name": "Conflict of Interest Register",
        "description": "Committee conflict-of-interest register (GAP-GOV-007). EC members declare financial, personal, or professional conflicts tied to motions. Managers update resolutions and retract declarations. Supports abstained, recused, disclosed_and_voted, and chair_ruled_no_conflict outcomes.",
        "is_enabled": True,
        "category": "governance",
        "icon": "AlertCircle",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member"],
        "routes": ["/governance/conflict-of-interest"],
        "depends_on": ["meetings"]
    },
    # ── Communication ─────────────────────────────────────────────────────────
    {
        "feature_key": "chat",
        "feature_name": "Community Chat",
        "description": "Group chat and community discussions",
        "is_enabled": True,
        "category": "communication",
        "icon": "MessageCircle",
        "routes": ["/community/chat"],
        "depends_on": []
    },
    {
        "feature_key": "private_messages",
        "feature_name": "Private Messages",
        "description": "Direct messaging between residents",
        "is_enabled": True,
        "category": "communication",
        "icon": "Mail",
        "routes": ["/community/messages"],
        "depends_on": ["chat"]
    },
    {
        "feature_key": "announcements",
        "feature_name": "Legacy Announcements API",
        "description": "Compatibility flag for legacy announcement API reads while notices are the canonical UI",
        "is_enabled": True,
        "category": "communication",
        "icon": "Megaphone",
        "routes": [],
        "depends_on": []
    },
    {
        "feature_key": "notices",
        "feature_name": "Notice Board",
        "description": "Official notices with acknowledgment tracking and community discussions",
        "is_enabled": True,
        "category": "communication",
        "icon": "Bell",
        "routes": ["/community/notices"],
        "depends_on": []
    },
    {
        "feature_key": "email",
        "feature_name": "Custom Email Domain",
        "description": "Email addresses with @eastgateresidences.com.au domain",
        "is_enabled": True,
        "category": "communication",
        "icon": "AtSign",
        "routes": [],
        "depends_on": []
    },
    {
        "feature_key": "webmail",
        "feature_name": "Email Access",
        "description": "Access East Gate Mail webmail client",
        "is_enabled": True,
        "category": "communication",
        "icon": "AtSign",
        "routes": ["/my-communications"],
        "depends_on": ["email"]
    },
    {
        "feature_key": "email_preferences",
        "feature_name": "Email Preferences",
        "description": "Manage email notification preferences",
        "is_enabled": True,
        "category": "communication",
        "icon": "Bell",
        "routes": ["/my-communications"],
        "depends_on": ["email"]
    },
    {
        "feature_key": "manual_email",
        "feature_name": "Manual Email Communication",
        "description": "Send manual email communications to individual residents, owners or community groups",
        "is_enabled": True,
        "category": "communication",
        "icon": "MailOpen",
        "routes": ["/admin/communications"],
        "depends_on": ["email"]
    },
    {
        "feature_key": "notifications_management",
        "feature_name": "Notification Management",
        "description": "Admin notification system management",
        "is_enabled": True,
        "category": "communication",
        "icon": "Send",
        "routes": ["/notifications"],
        "depends_on": []
    },
    {
        "feature_key": "news",
        "feature_name": "News & Blog",
        "description": "Community news and updates",
        "is_enabled": True,
        "category": "communication",
        "icon": "Newspaper",
        "routes": ["/blog"],
        "depends_on": []
    },
    {
        "feature_key": "blog_management",
        "feature_name": "Blog Management",
        "description": "Create and manage community news and blog posts",
        "is_enabled": True,
        "category": "communication",
        "icon": "Newspaper",
        "routes": ["/community/blog-management"],
        "depends_on": ["news"]
    },
    # ── Community ─────────────────────────────────────────────────────────────
    {
        "feature_key": "marketplace",
        "feature_name": "Marketplace",
        "description": "Buy, sell, and trade within the community",
        "is_enabled": True,
        "category": "community",
        "icon": "ShoppingCart",
        "routes": ["/community/marketplace", "/community/my-listings"],
        "depends_on": []
    },
    {
        "feature_key": "events",
        "feature_name": "Events Calendar",
        "description": "View and manage community events",
        "is_enabled": True,
        "category": "community",
        "icon": "CalendarDays",
        "routes": ["/community/events"],
        "depends_on": []
    },
    {
        "feature_key": "directory",
        "feature_name": "Resident Directory",
        "description": "Browse resident profiles and contact information",
        "is_enabled": True,
        "category": "community",
        "icon": "Users",
        "routes": ["/community/directory"],
        "depends_on": []
    },
    {
        "feature_key": "pet_register",
        "feature_name": "Pet Register",
        "description": "Register and manage pets living in the complex with owner unit tracking and approval status",
        "is_enabled": True,
        "category": "community",
        "icon": "PawPrint",
        "routes": ["/community/pet-register"],
        "depends_on": []
    },
    {
        "feature_key": "volunteer",
        "feature_name": "Volunteer Events",
        "description": "Coordinate volunteer events and apply levy credits",
        "is_enabled": True,
        "category": "community",
        "icon": "HandHelping",
        "routes": ["/community/volunteer"],
        "depends_on": []
    },
    {
        "feature_key": "building_health",
        "feature_name": "Building Health Score",
        "description": "Computed building health dashboard and score",
        "is_enabled": True,
        "category": "community",
        "icon": "HeartPulse",
        "routes": ["/community-dashboard"],
        "depends_on": []
    },
    {
        "feature_key": "email_notifications_enabled",
        "feature_name": "Outgoing Email",
        "description": (
            "Master switch for outgoing email for this building. Turn OFF to suppress "
            "every outgoing message — levy notices, invitations, reminders, digests and "
            "the direct-sending cron jobs. Nothing is queued for later: suppressed sends "
            "are dropped and recorded in email_sent_log with provider='suppressed'."
        ),
        "is_enabled": True,   # global default: email flows unless a building turns it off
        "category": "operations",
        "icon": "Mail",
        "routes": [],
        "depends_on": []
    },
    {
        "feature_key": "smart_requests",
        "feature_name": "Smart Request Intake",
        "description": "Deterministic and AI-assisted workflow request triage and routing",
        "is_enabled": True,
        "category": "operations",
        "icon": "Zap",
        "routes": ["/requests/new"],
        "depends_on": []
    },
    {
        "feature_key": "request_status_tracking",
        "feature_name": "Request Status Tracking",
        "description": "Real-time request status timeline for residents — eliminates 'any update?' emails",
        "is_enabled": True,
        "category": "operations",
        "icon": "ClipboardList",
        "routes": ["/requests"],
        "depends_on": ["smart_requests"]
    },
    {
        "feature_key": "workflow_governance",
        "feature_name": "Workflow Governance Dashboard",
        "description": "Named, observable automations dashboard — shows all workflow runs, health status, and coverage",
        "is_enabled": True,
        "category": "system",
        "icon": "GitBranch",
        "routes": ["/admin/workflows"],
        "depends_on": [],
        "roles": ["super_admin", "strata_manager"]
    },
    {
        "feature_key": "portfolio_dashboard",
        "feature_name": "Portfolio Dashboard",
        "description": "Multi-building command centre for strata managers",
        "is_enabled": True,
        "category": "system",
        "icon": "Building2",
        "routes": ["/admin"],
        "depends_on": [],
        "roles": ["super_admin", "strata_manager"]
    },
    {
        "feature_key": "building_onboarding",
        "feature_name": "Building Onboarding Workflow",
        "description": "Structured onboarding checklist for new buildings",
        "is_enabled": True,
        "category": "system",
        "icon": "ClipboardCheck",
        "routes": ["/admin/portfolio/onboard", "/admin/buildings/onboard"],
        "depends_on": []
    },
    {
        "feature_key": "my_finances",
        "feature_name": "My Finances Dashboard",
        "description": "Owner financial transparency dashboard — levy breakdown, savings, health",
        "is_enabled": True,
        "category": "financial",
        "icon": "DollarSign",
        "routes": ["/financials/my-finances"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "owner_finance",
        "feature_name": "Owner Finance Dashboard",
        "description": "Personalised financial transparency dashboard for owners and tenants",
        "is_enabled": True,
        "category": "financial",
        "icon": "DollarSign",
        "routes": ["/financials/my-finances"],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "volunteer_credits",
        "feature_name": "Volunteer Levy Credits",
        "description": "Volunteer credit system — earn levy credits through community contributions",
        "is_enabled": True,
        "category": "community",
        "icon": "Heart",
        "routes": [],
        "depends_on": []
    },
    {
        "feature_key": "quick_polls",
        "feature_name": "Quick Polls",
        "description": "Non-binding micro-governance polls for community voice",
        "is_enabled": True,
        "category": "governance",
        "icon": "Vote",
        "routes": ["/governance/polls"],
        "depends_on": []
    },
    {
        "feature_key": "group_buying",
        "feature_name": "Community Group Buying",
        "description": "Bulk purchase campaigns for community savings",
        "is_enabled": True,
        "category": "community",
        "icon": "ShoppingBag",
        "routes": ["/community/group-buy"],
        "depends_on": []
    },
    {
        "feature_key": "morning_card",
        "feature_name": "Morning Card",
        "description": "Single most relevant action card shown on dashboard load",
        "is_enabled": True,
        "category": "engagement",
        "icon": "Zap",
        "routes": [],
        "depends_on": []
    },
    {
        "feature_key": "building_pulse",
        "feature_name": "Building Pulse",
        "description": "Live activity feed showing recent building events",
        "is_enabled": True,
        "category": "engagement",
        "icon": "Activity",
        "routes": [],
        "depends_on": []
    },
    {
        "feature_key": "tenancy_passport",
        "feature_name": "Tenancy Passport",
        "description": "Portable residency score and PDF passport for tenants",
        "is_enabled": True,
        "category": "community",
        "icon": "FileCheck",
        "routes": ["/profile/passport"],
        "depends_on": []
    },
    {
        "feature_key": "suburb_radar",
        "feature_name": "Suburb Radar",
        "description": "Weekly local intelligence digest — property market, ESA incidents, planning",
        "is_enabled": True,
        "category": "intelligence",
        "icon": "Map",
        "routes": ["/intelligence/suburb-radar"],
        "depends_on": []
    },
    {
        "feature_key": "safety_feed",
        "feature_name": "Building Safety Feed",
        "description": "Building safety events log for residents and managers",
        "is_enabled": True,
        "category": "safety",
        "icon": "Shield",
        "routes": ["/intelligence/safety-feed"],
        "depends_on": []
    },
    # ── Safety ────────────────────────────────────────────────────────────────
    {
        "feature_key": "emergency",
        "feature_name": "Emergency Services",
        "description": "Quick access to emergency contacts and services",
        "is_enabled": True,
        "category": "safety",
        "icon": "AlertTriangle",
        "routes": ["/emergency"],
        "depends_on": []
    },
    {
        "feature_key": "essential_services",
        "feature_name": "Essential Services Compliance Log",
        "description": "Compliance log for statutory essential services (GAP-COM-008): cooling towers, HVAC, backflow prevention, lifts, fire pumps, emergency lighting, and exit lighting. Auto-calculates next due date from service interval. Overdue endpoint for dashboard alerts. Managers write; owners and tenants can view.",
        "is_enabled": True,
        "category": "safety",
        "icon": "ShieldCheck",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff", "owner",
                  "tenant"],
        "routes": ["/compliance/essential-services"],
        "depends_on": []
    },
    {
        "feature_key": "pool_safety",
        "feature_name": "Pool Safety Register",
        "description": "Statutory pool safety inspection register (GAP-COM-005). Tracks inspector credentials, pass/fail results, defect rectification deadlines and certificate storage. Overdue endpoint surfaces inspections past their next-due date. Managers log and update; owners and tenants can view.",
        "is_enabled": True,
        "category": "safety",
        "icon": "Waves",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff", "owner",
                  "tenant"],
        "routes": ["/compliance/pool-safety"],
        "depends_on": []
    },
    # ── Facilities ────────────────────────────────────────────────────────────
    {
        "feature_key": "maintenance",
        "feature_name": "Maintenance Requests",
        "description": "Submit and track maintenance requests",
        "is_enabled": True,
        "category": "facilities",
        "icon": "Wrench",
        "routes": ["/maintenance"],
        "depends_on": []
    },
    {
        "feature_key": "work_orders",
        "feature_name": "Work Orders",
        "description": "Full lifecycle management of work orders, quotes, vendor assignments, and invoice approvals",
        "is_enabled": True,
        "category": "facilities",
        "icon": "Wrench",
        "routes": ["/maintenance"],
        "depends_on": ["maintenance"]
    },
    {
        "feature_key": "ppm_schedule",
        "feature_name": "Preventive Maintenance Plan (PPM)",
        "description": "79-item compliance calendar sourced from the building's PPM schedule — section/status/compliance filters, completion logging with auto-recomputed next due dates, and daily manager/resident email notifications at 30/14/7/0-day thresholds",
        "is_enabled": True,
        "category": "facilities",
        "icon": "Calendar",
        "routes": ["/maintenance"],
        "depends_on": ["maintenance"]
    },
    {
        "feature_key": "defects",
        "feature_name": "Defect Reports",
        "description": "Report and track building defects",
        "is_enabled": True,
        "category": "facilities",
        "icon": "AlertCircle",
        "routes": ["/maintenance/defects"],
        "depends_on": ["maintenance"]
    },
    {
        # GAP-OPS-005. The router and its ACAT/NCAT evidence trail have existed and been
        # reachable since it shipped, but with no toggle and no page nothing could reach
        # it — the register stayed empty, and the building health score's dispute axis
        # therefore reported "unavailable" permanently.
        "feature_key": "by_law_breach",
        "feature_name": "By-law Breaches & Disputes",
        "description": "Record by-law breaches, issue notices, and keep a tribunal-ready evidence trail",
        "is_enabled": True,
        "category": "community",
        "icon": "Gavel",
        "routes": ["/community/by-law-breach"],
        "depends_on": []
    },
    {
        "feature_key": "bookings",
        "feature_name": "Facility Bookings",
        "description": "Book common areas and amenities",
        "is_enabled": True,
        "category": "facilities",
        "icon": "CalendarCheck",
        "routes": ["/community/bookings"],
        "depends_on": []
    },
    {
        "feature_key": "parcels",
        "feature_name": "Parcel Management",
        "description": "Track parcel deliveries and collections",
        "is_enabled": True,
        "category": "facilities",
        "icon": "Package",
        "routes": ["/community/parcels"],
        "depends_on": []
    },
    {
        "feature_key": "schedule",
        "feature_name": "Schedule Management",
        "description": "Service provider schedules and calendar",
        "is_enabled": True,
        "category": "facilities",
        "icon": "Clock",
        "routes": ["/maintenance/schedule"],
        "depends_on": []
    },
    {
        "feature_key": "requests",
        "feature_name": "Request System",
        "description": "Submit general requests to management",
        "is_enabled": True,
        "category": "facilities",
        "icon": "FileQuestion",
        "routes": ["/requests"],
        "depends_on": []
    },
    # ── Maintenance Intelligence ───────────────────────────────────────────────
    {
        "feature_key": "asset_register",
        "feature_name": "Asset Register",
        "description": "Manage building assets, equipment and infrastructure inventory with lifecycle tracking",
        "is_enabled": True,
        "category": "maintenance",
        "icon": "Database",
        "routes": ["/admin/assets"],
        "depends_on": ["maintenance"]
    },
    {
        "feature_key": "maintenance_intelligence",
        "feature_name": "Maintenance Intelligence Engine",
        "description": "Asset health scoring, predictive maintenance forecasts, 10-year capital works planning and levy stabilization",
        "is_enabled": True,
        "category": "maintenance",
        "icon": "Brain",
        "routes": ["/intelligence/building"],
        "depends_on": ["asset_register"]
    },
    {
        "feature_key": "digital_twin",
        "feature_name": "Digital Twin Architecture",
        "description": "Building asset graph — zones, facilities, components and benefit groups for cost allocation",
        "is_enabled": True,
        "category": "system",
        "icon": "Building2",
        "routes": ["/settings/digital-twin"],
        "depends_on": ["asset_register"]
    },
    # ── OwnerHub / Landlord Platform ─────────────────────────────────────────
    {
        "feature_key": "owner_hub",
        "feature_name": "OwnerHub — Landlord Platform",
        "description": "Self-managed landlord platform: manage investment properties (Eastgate + external), tenancies, rent ledger, inspections and property intelligence in one place.",
        "is_enabled": True,
        "category": "landlord",
        "icon": "Home",
        "routes": [
            "/owner-hub",
            "/owner-hub/properties",
            "/owner-hub/ledger",
            "/owner-hub/inspections",
        ],
        "depends_on": []
    },
    {
        "feature_key": "property_health_score",
        "feature_name": "Property Health Score",
        "description": "Weekly property health score (0–100) combining income stability, maintenance backlog, tenant tenure, capital risk and compliance. Gives landlords a single investment quality indicator.",
        "is_enabled": True,
        "category": "landlord",
        "icon": "Activity",
        "routes": ["/owner-hub/health-score"],
        "depends_on": ["owner_hub"]
    },
    {
        "feature_key": "true_cost_ownership",
        "feature_name": "True Cost of Ownership",
        "description": "Accurate annual ownership cost calculator including strata levies, maintenance, capital works share, vacancy, financing and 10-year cashflow projection.",
        "is_enabled": True,
        "category": "landlord",
        "icon": "Calculator",
        "routes": ["/owner-hub/tco"],
        "depends_on": ["owner_hub", "finance"]
    },
    {
        "feature_key": "tenant_maintenance_portal",
        "feature_name": "Tenant Maintenance Portal",
        "description": "RentBetter-inspired tenant maintenance portal: submit requests with photos, track status (Submitted → Assigned → In Progress → Completed), message thread with owner/agent/manager.",
        "is_enabled": True,
        "category": "landlord",
        "icon": "Wrench",
        "routes": ["/maintenance/tenant"],
        "depends_on": ["maintenance", "owner_hub"]
    },
    {
        "feature_key": "real_estate_agent_portal",
        "feature_name": "Real Estate Agent Portal",
        "description": "Dedicated portal for real estate agents to manage assigned properties on behalf of owners: tenant communications, maintenance oversight, lease status, inspection scheduling.",
        "is_enabled": True,
        "category": "landlord",
        "icon": "Briefcase",
        "routes": ["/real-estate"],
        "depends_on": ["owner_hub"]
    },
    {
        "feature_key": "pm_engagement_letter",
        "feature_name": "Property Manager Engagement Letter",
        "description": "Generate, preview, and email a property management engagement summary (fees, owner responsibilities, ACT compliance requirements) from OwnerHub for any tenanted external property. Template: /user-guides/property_manager_engagement_letter.html. Backend wiring not yet implemented — enable this toggle when endpoints are live.",
        "is_enabled": False,  # Default OFF: no backend endpoint exists yet (frontend template only); enable when the engagement-letter endpoints are live.
        "category": "landlord",
        "icon": "FileText",
        "routes": ["/owner-hub/properties"],
        "depends_on": ["owner_hub", "real_estate_agent_portal"]
    },
    # ── Intelligence ──────────────────────────────────────────────────────────
    {
        "feature_key": "strata_market_intelligence",
        "feature_name": "ACT Strata Market Intelligence",
        "description": "ACT-wide suburb strata density heatmap, cluster analysis, TAM calculator, true cost of ownership and financial stress predictor",
        "is_enabled": True,
        "category": "intelligence",
        "icon": "Map",
        "roles": ["super_admin", "strata_manager"],
        "routes": ["/intelligence/market"],
        "depends_on": []
    },
    {
        "feature_key": "building_risk_index",
        "feature_name": "Global Building Risk Index",
        "description": "Composite 0–100 risk score combining asset health, financial stability, reserve adequacy, capital shock risk, and maintenance forecasts. Includes public building report page.",
        "is_enabled": True,
        "category": "intelligence",
        "icon": "ShieldAlert",
        "roles": ["super_admin", "strata_manager", "strata_admin", "ec_member", "owner"],
        "routes": ["/intelligence/global-risk", "/building/[slug]"],
        "depends_on": ["strata_market_intelligence"]
    },
    # ── System ────────────────────────────────────────────────────────────────
    {
        "feature_key": "notification_cleanup",
        "feature_name": "Notification Cleanup",
        "description": "Automatically delete old notifications after a set period (configurable in days)",
        "is_enabled": True,
        "category": "system",
        "icon": "Trash2",
        "routes": [],
        "depends_on": []
    },
    {
        "feature_key": "rate_limiting",
        "feature_name": "Rate Limiting",
        "description": "Enable IP-based rate limiting for sensitive authentication endpoints",
        "is_enabled": True,
        "category": "system",
        "icon": "Gauge",
        "routes": [],
        "depends_on": []
    },
    {
        "feature_key": "external_api",
        "feature_name": "External API (v1)",
        "description": "Versioned external API for banking connectors, strata management platforms and service providers. API key authentication with scope-based access control.",
        "is_enabled": True,
        "category": "system",
        "icon": "Plug",
        "routes": ["/admin/api-keys"],
        "depends_on": []
    },
    {
        "feature_key": "audit_logs",
        "feature_name": "Audit Logs",
        "description": "View system activity and audit logs",
        "is_enabled": True,
        "category": "system",
        "icon": "History",
        "routes": ["/admin/audit-logs"],
        "depends_on": []
    },
    {
        "feature_key": "site_settings",
        "feature_name": "Site Configuration Settings",
        "description": "System-wide settings including registration, branding, levy periods and general configuration",
        "is_enabled": True,
        "category": "system",
        "icon": "Settings",
        "routes": ["/settings"],
        "depends_on": []
    },
    {
        "feature_key": "admin_console",
        "feature_name": "Legal & Privacy Console",
        "description": "Manage legal documents, terms of use, privacy policies and compliance records",
        "is_enabled": True,
        "category": "system",
        "icon": "ScrollText",
        "routes": ["/admin/console"],
        "depends_on": []
    },
    {
        "feature_key": "email_settings",
        "feature_name": "Email System Configuration",
        "description": "Configure SMTP settings, email templates, sender details and delivery preferences",
        "is_enabled": True,
        "category": "system",
        "icon": "MailCheck",
        "routes": ["/admin/communications"],
        "depends_on": []
    },
    {
        "feature_key": "manage_mail_passwords",
        "feature_name": "Mail Credential Manager",
        "description": "Manage @eastgateresidences.com.au email credentials and portal access for residents",
        "is_enabled": True,
        "category": "system",
        "icon": "KeySquare",
        "routes": ["/admin/communications"],
        "depends_on": ["email"]
    },
    {
        "feature_key": "security_ip_logs",
        "feature_name": "Security & IP Protection Logs",
        "description": "Monitor IP-based access logs, blocked addresses, rate-limit events and security alerts",
        "is_enabled": True,
        "category": "system",
        "icon": "ShieldAlert",
        "routes": ["/admin/security-ip-logs"],
        "depends_on": []
    },
    {
        "feature_key": "scraper_settings",
        "feature_name": "Scraper Settings",
        "description": "Configure and manage data scrapers, cron schedules, API usage and thresholds (Super Admin only)",
        "is_enabled": True,
        "category": "system",
        "icon": "Bot",
        "routes": ["/admin/scraper-settings"],
        "depends_on": []
    },
    {
        "feature_key": "impersonation",
        "feature_name": "User Impersonation",
        "description": "Allow Super Admins to view the portal as another user for troubleshooting",
        "is_enabled": True,
        "category": "system",
        "icon": "UserSearch",
        "routes": [],
        "depends_on": []
    },
    {
        "feature_key": "user_permissions",
        "feature_name": "Access Control Management",
        "description": "Manage granular user permissions and role-based access control settings per role",
        "is_enabled": True,
        "category": "system",
        "icon": "Lock",
        "routes": ["/admin/user-permissions"],
        "depends_on": ["user_management"]
    },
    {
        "feature_key": "user_feature_permissions",
        "feature_name": "Feature Access Controls",
        "description": "Grant or restrict individual user access to specific features independently of their role",
        "is_enabled": True,
        "category": "system",
        "icon": "KeyRound",
        "routes": ["/admin/user-feature-permissions"],
        "depends_on": ["user_management"]
    },
    {
        "feature_key": "progressive_nav",
        "feature_name": "Progressive Navigation System",
        "description": "Three-layer progressive navigation with simple/advanced modes",
        "is_enabled": True,
        "category": "system",
        "icon": "Layout",
        "routes": [],
        "depends_on": []
    },
    {
        "feature_key": "adaptive_nav",
        "feature_name": "Adaptive Navigation AI",
        "description": "Reorders advanced menu items based on user behaviour",
        "is_enabled": True,
        "category": "system",
        "icon": "Brain",
        "routes": [],
        "depends_on": ["progressive_nav"]
    },
    {
        "feature_key": "nav_customisation",
        "feature_name": "Navigation Customisation",
        "description": "Allow users to pin, hide, and reorder nav items",
        "is_enabled": True,
        "category": "system",
        "icon": "Settings",
        "routes": [],
        "depends_on": ["progressive_nav"]
    },
    {
        "feature_key": "nav_discovery_nudges",
        "feature_name": "Feature Discovery Nudges",
        "description": "Periodic prompts to explore unused features",
        "is_enabled": True,
        "category": "system",
        "icon": "Lightbulb",
        "routes": [],
        "depends_on": ["progressive_nav"]
    },
    # ── Intelligence ──────────────────────────────────────────────────────────
    {
        "feature_key": "occupancy_intelligence",
        "feature_name": "Building Occupancy Intelligence",
        "description": "Classify each lot as tenant, owner-occupied, or investor-unknown using rental certificates and address matching. Includes pie chart, trend chart, and heatmap grid.",
        "is_enabled": True,
        "category": "intelligence",
        "icon": "Users",
        "routes": ["/intelligence/occupancy"],
        "depends_on": []
    },
    {
        "feature_key": "ops_case_management_pg_enabled",
        "feature_name": "Ops Case Management PG",
        "description": "Enable the PostgreSQL-backed unified case/task foundation for the strata-manager operating system.",
        "is_enabled": True,
        "category": "operations",
        "icon": "Briefcase",
        "routes": ["/requests/ops/cases"],
        "roles": ["super_admin"],
        "depends_on": []
    },
    {
        "feature_key": "communications_campaigns_pg_enabled",
        "feature_name": "Communications Campaigns PG",
        "description": "Enable PostgreSQL-backed newsletters, campaigns, acknowledgements, and campaign drafting workflows.",
        "is_enabled": True,
        "category": "communications",
        "icon": "Megaphone",
        "routes": ["/admin/communications"],
        "roles": ["super_admin"],
        "depends_on": []
    },
    {
        "feature_key": "access_device_lifecycle_pg_enabled",
        "feature_name": "Access Device Lifecycle PG",
        "description": "Enable PostgreSQL-backed access device request, issuance, return, and deactivation workflows.",
        "is_enabled": True,
        "category": "operations",
        "icon": "KeyRound",
        "routes": ["/settings?tab=access-devices", "/requests?tab=access-control"],
        "roles": ["super_admin", "strata_admin", "strata_manager", "admin_staff", "owner", "tenant", "real_estate_agent"],
        # Access requests can be escalated to ops cases; require the case layer first.
        "depends_on": ["ops_case_management_pg_enabled"]
    },
    {
        "feature_key": "ai_review_panel_enabled",
        "feature_name": "AI Review Panel",
        "description": "Expose tenant-scoped AI assessment, recommendation, and human review panel workflows. No automated high-risk actions are enabled by this toggle alone.",
        "is_enabled": True,
        "category": "ai",
        "icon": "Bot",
        "routes": ["/admin/ops/review-panel"],
        "roles": ["super_admin"],
        # convert_recommendation_to_case writes into ops.cases; the case layer must be active.
        "depends_on": ["ops_case_management_pg_enabled"]
    },
    {
        "feature_key": "sustainability_assessments_pg_enabled",
        "feature_name": "Sustainability Assessments PG",
        "description": "Enable PostgreSQL-backed sustainability profile, lighting, solar-readiness, and project-benefit foundations.",
        "is_enabled": True,
        "category": "operations",
        "icon": "Leaf",
        "routes": ["/intelligence/sustainability"],
        "roles": ["super_admin"],
        "depends_on": []
    },

    # ── PostgreSQL Cutover Infrastructure Flags ───────────────────────────────
    # These toggles control the data-cutover from MongoDB to PostgreSQL.
    # They live in core.feature_toggles (PG) and are the source of truth for
    # the feature-toggles admin page. Added to DEFAULT_FEATURES so the MongoDB
    # seed remains a complete canonical list and toggle_drift.py reports clean.
    #
    # POLICY: All cutover/migration toggles MUST default globally false.
    # Enable per-building via core.feature_toggle_overrides only after the
    # building has passed its cutover readiness gates (see
    # docs/architecture/feature-toggle-governance.md §5).
    {
        "feature_key": "financial_core.read_from_postgres",
        "feature_name": "Read Financial Data from Postgres",
        "description": "Legacy alias — resolves to financial_pg_writes_enabled (routes financial WRITES through the Postgres core), NOT reads. For read cutover use financial_pg_reads_enabled instead. Retained only for back-compat with pre-consolidation building configs; prefer the canonical keys.",
        "is_enabled": False,  # Cutover toggle: globally false; enable per-building via core.feature_toggle_overrides
        "category": "finance",
        "icon": "Database",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["financial_integration_layer_v2"]
    },
    {
        "feature_key": "financial_core.shadow_read_postgres",
        "feature_name": "Shadow Read Mode (Postgres)",
        "description": "Enables parallel MongoDB + Postgres reads with divergence detection. Used during pre-cutover validation. Set by observability team when ready to monitor Postgres parity.",
        "is_enabled": False,  # Cutover toggle: globally false; enable per-building via core.feature_toggle_overrides
        "category": "finance",
        "icon": "Database",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["financial_integration_layer_v2"]
    },
    {
        "feature_key": "bank_integration_abstraction_enabled",
        "feature_name": "Bank Integration Abstraction",
        "description": "Resolve bank providers from Postgres-backed building settings and the provider registry instead of hard-coded mocks.",
        "is_enabled": False,  # Cutover toggle: globally false; enable per-building via core.feature_toggle_overrides
        "category": "financial",
        "icon": "Database",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["financial_integration_layer_v2"]
    },
    {
        "feature_key": "external_api_finance_pg_enabled",
        "feature_name": "External API PostgreSQL Finance",
        "description": "Serve migrated external finance API responses from PostgreSQL read models.",
        "is_enabled": False,  # Cutover toggle: globally false; enable per-building via core.feature_toggle_overrides
        "category": "financial",
        "icon": "Database",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["financial_integration_layer_v2"]
    },
    {
        "feature_key": "financial_pg_reads_enabled",
        "feature_name": "Financial PostgreSQL Reads",
        "description": "Enable PostgreSQL read models for migrated financial APIs and dashboards.",
        "is_enabled": False,  # Cutover toggle: globally false; enable per-building via core.feature_toggle_overrides
        "category": "financial",
        "icon": "Database",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["financial_integration_layer_v2"]
    },
    {
        "feature_key": "financial_pg_reads_bypass_shadow",
        "feature_name": "Financial PG Reads — Shadow-Soak Waiver",
        "description": (
            "Serve finance PostgreSQL reads once PG↔source parity is verified out-of-band, "
            "WITHOUT waiting out the 7-day shadow soak. Skips only the soak-duration gate — a "
            "route with any critical shadow diff is still refused. Enable per-building only after "
            "reconciliation confirms the building's data is clean."
        ),
        "is_enabled": False,  # Cutover toggle: globally false; enable per-building via core.feature_toggle_overrides
        "category": "financial",
        "icon": "Database",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["financial_pg_reads_enabled"]
    },
    {
        "feature_key": "financial_pg_writes_enabled",
        "feature_name": "Financial PostgreSQL Writes",
        "description": "Route financial write operations through the PostgreSQL financial core instead of legacy MongoDB paths.",
        "is_enabled": False,  # Cutover toggle: globally false; enable per-building via core.feature_toggle_overrides
        "category": "financial",
        "icon": "Database",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["financial_integration_layer_v2"]
    },
    {
        "feature_key": "financial_shadow_reads_enabled",
        "feature_name": "Financial Shadow Reads",
        "description": "Read MongoDB and PostgreSQL in parallel and log divergences during the finance cutover soak period.",
        "is_enabled": False,  # Cutover toggle: globally false; enable per-building via core.feature_toggle_overrides
        "category": "financial",
        "icon": "Database",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["financial_integration_layer_v2"]
    },
    {
        "feature_key": "onboarding_current_balance_adapters_enabled",
        "feature_name": "Onboarding Current-Balance Adapters",
        "description": "Enable the current-balance onboarding adapters that feed genesis and financial cutover validation.",
        "is_enabled": False,  # Cutover toggle: globally false; enable per-building via core.feature_toggle_overrides
        "category": "financial",
        "icon": "Database",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["financial_integration_layer_v2"]
    },
    {
        "feature_key": "owner_read_pg_enabled",
        "feature_name": "Owner PostgreSQL Reads",
        "description": "Enable PostgreSQL-backed owner read models and cut over owner-facing building-scoped reads from legacy MongoDB paths.",
        "is_enabled": False,  # Cutover toggle: globally false; enable per-building via core.feature_toggle_overrides
        "category": "financial",
        "icon": "Database",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["financial_integration_layer_v2"]
    },
    {
        "feature_key": "trust_pg_ledger_enabled",
        "feature_name": "Trust PostgreSQL Ledger",
        "description": "Enable PostgreSQL trust ledger reads and writes for trust-accounting workflows.",
        "is_enabled": False,  # Cutover toggle: globally false; enable per-building via core.feature_toggle_overrides
        "category": "financial",
        "icon": "Database",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["financial_integration_layer_v2"]
    },
    {
        "feature_key": "trust_reconciliation_pg_enabled",
        "feature_name": "Trust PostgreSQL Reconciliation",
        "description": "Enable PostgreSQL-backed trust reconciliation summaries and statement workflows.",
        "is_enabled": False,  # Cutover toggle: globally false; enable per-building via core.feature_toggle_overrides
        "category": "financial",
        "icon": "Database",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["financial_integration_layer_v2"]
    },
    {
        "feature_key": "governance_read_pg_enabled",
        "feature_name": "Governance PostgreSQL Reads",
        "description": "Enable PostgreSQL read models for AGM records, EC members, decisions, and by-laws. Flip per-scheme after 7 days clean shadow-read parity.",
        "is_enabled": False,  # Cutover toggle: globally false; enable per-building via core.feature_toggle_overrides
        "category": "governance",
        "icon": "Database",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["financial_integration_layer_v2"]
    },
    {
        "feature_key": "settings_pg_reads_enabled",
        "feature_name": "Settings PostgreSQL Reads",
        "description": "Enable PostgreSQL-first reads for the settings router (Phase D per-router PG read gate). Resolves to safe default False when absent.",
        "is_enabled": False,  # Cutover toggle: globally false; enable per-building via core.feature_toggle_overrides
        "category": "system",
        "icon": "Database",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["financial_integration_layer_v2"]
    },
    {
        "feature_key": "users_pg_reads_enabled",
        "feature_name": "Users PostgreSQL Reads",
        "description": "Enable PostgreSQL-first reads for the users router (Phase D per-router PG read gate). Resolves to safe default False when absent.",
        "is_enabled": False,  # Cutover toggle: globally false; enable per-building via core.feature_toggle_overrides
        "category": "system",
        "icon": "Database",
        "routes": [],
        "roles": ["super_admin"],
        "depends_on": ["financial_integration_layer_v2"]
    },
    # ── System-level flags seeded by PG migration (2026-05-12) ───────────────
    {
        "feature_key": "budget_management",
        "feature_name": "Budget Management",
        "description": "System-level toggle enabling the budget management module.",
        "is_enabled": True,
        "category": "system",
        "icon": "BarChart2",
        "routes": ["/financials/budget"],
        "depends_on": []
    },
    {
        "feature_key": "community",
        "feature_name": "Community",
        "description": "System-level toggle enabling the community hub module.",
        "is_enabled": True,
        "category": "system",
        "icon": "Users",
        "routes": ["/community"],
        "depends_on": []
    },
    {
        "feature_key": "e_voting",
        "feature_name": "E-Voting",
        "description": "System-level toggle enabling electronic voting. Use agm_evoting for AGM-specific proxy/quorum logic.",
        "is_enabled": True,
        "category": "system",
        "icon": "Vote",
        "routes": ["/governance/meetings/voting"],
        "depends_on": []
    },
    {
        "feature_key": "financial_reporting",
        "feature_name": "Financial Reporting",
        "description": "System-level toggle enabling the financial reporting module.",
        "is_enabled": True,
        "category": "finance",
        "icon": "FileText",
        "routes": [
            "/reports",
        ],
        "depends_on": ["finance"]
    },
    {
        "feature_key": "levy_management",
        "feature_name": "Levy Management",
        "description": "System-level toggle enabling the levy management and scheduling module.",
        "is_enabled": True,
        "category": "system",
        "icon": "DollarSign",
        "routes": ["/financials/levy-payments"],
        "depends_on": []
    },
    {
        "feature_key": "owner_dashboard",
        "feature_name": "Owner Dashboard",
        "description": "System-level toggle enabling the owner-facing dashboard.",
        "is_enabled": True,
        "category": "system",
        "icon": "Home",
        "routes": ["/dashboard"],
        "depends_on": []
    },
    {
        "feature_key": "tenant_portal",
        "feature_name": "Tenant Portal",
        "description": "System-level toggle enabling the tenant-facing portal.",
        "is_enabled": True,
        "category": "system",
        "icon": "Key",
        "routes": ["/dashboard"],
        "depends_on": []
    },
    {
        "feature_key": "ft_dashboard_v2",
        "feature_name": "Dashboard V2 — Personal Stake Layout",
        "description": "Activates the dashboard-v2 redesign for both Manager (Building Pulse + Triage Queue + Since Last Visit) and Owner (Your Standing hero + Community Pulse + Capital For Me). Shipped behind this toggle so rollback is a single flag flip.",
        "is_enabled": True,
        "category": "system",
        "icon": "LayoutDashboard",
        "roles": ["super_admin", "strata_admin", "strata_manager", "ec_member", "owner", "tenant"],
        "routes": ["/dashboard", "/owner-view"],
        "depends_on": []
    },
    # ── Powerhouse foundation shell (all disabled by default — shell/preview only) ─
    # These must remain is_enabled=False globally until each sub-feature passes its
    # readiness gate. Enable per-scheme (building) via core.feature_toggle_overrides.
    # No Powerhouse feature may be promoted to is_enabled=True globally without:
    #   1. Full PG write path validated (zero Mongo-only writes for that sub-feature)
    #   2. Cross-building isolation tests passing
    #   3. Toggle-off tests passing (API returns 403, UI shows disabled state)
    #   4. Production readiness sign-off in docs/architecture/feature-toggle-governance.md
    {
        "feature_key": "powerhouse_conversations",
        "feature_name": "Powerhouse — Conversation Center",
        "description": "Unified conversation center: shared inbox, thread timelines, internal notes, participants, watchers, entity links. Shell preview — disabled globally until PG write path is complete.",
        "is_enabled": False,
        "category": "powerhouse",
        "icon": "MessageSquare",
        "roles": ["super_admin", "strata_admin", "strata_manager", "ec_member", "admin_staff", "owner", "tenant"],
        "routes": ["/powerhouse/conversations", "/powerhouse/thread"],
        "depends_on": []
    },
    {
        "feature_key": "powerhouse_shared_inbox",
        "feature_name": "Powerhouse — Shared Inbox",
        "description": "Building shared email inboxes (building@, committee@, strata@). Config, inbound intake, outbound drafts. Shell preview — provider is mock-only until real provider is wired.",
        "is_enabled": False,
        "category": "powerhouse",
        "icon": "Inbox",
        "roles": ["super_admin", "strata_admin", "strata_manager", "ec_member", "admin_staff"],
        "routes": ["/powerhouse/inbox-settings"],
        "depends_on": ["powerhouse_conversations"]
    },
    {
        "feature_key": "powerhouse_email_intake",
        "feature_name": "Powerhouse — Email Intake Webhooks",
        "description": "Inbound email webhook processing from external providers (Sendgrid/Resend/Mailgun inbound parse). Shell preview — idempotency key dedup in Mongo, auto-thread mapping not yet wired.",
        "is_enabled": False,
        "category": "powerhouse",
        "icon": "Mail",
        "roles": ["super_admin", "strata_admin", "strata_manager"],
        "routes": [],
        "depends_on": ["powerhouse_shared_inbox"]
    },
    {
        "feature_key": "powerhouse_ai_summary",
        "feature_name": "Powerhouse — AI Summary and Response Draft",
        "description": "AI-assisted thread summary and response draft generation. Always returns placeholder with confidence=0.0 and requires_human_approval=True. Real AI model not connected.",
        "is_enabled": False,
        "category": "powerhouse",
        "icon": "Sparkles",
        "roles": ["super_admin", "strata_admin", "strata_manager", "ec_member", "admin_staff"],
        "routes": [],
        "depends_on": ["powerhouse_conversations"]
    },
    {
        "feature_key": "powerhouse_workflow_engine",
        "feature_name": "Powerhouse — Workflow Engine Shell",
        "description": "Workflow template listing, instance creation, event appending, step completion, task assignment. Convert-to-workflow from conversation/message. Shell preview — no real orchestrator connected.",
        "is_enabled": False,
        "category": "powerhouse",
        "icon": "GitBranch",
        "roles": ["super_admin", "strata_admin", "strata_manager", "ec_member", "admin_staff"],
        "routes": ["/powerhouse/workflows"],
        "depends_on": ["powerhouse_conversations"]
    },
    {
        "feature_key": "powerhouse_automation_rules",
        "feature_name": "Powerhouse — Automation Rules Shell",
        "description": "Automation rule run simulation (dry-run only). All runs are status=simulated and require_human_approval=True. No real rule engine connected.",
        "is_enabled": False,
        "category": "powerhouse",
        "icon": "Zap",
        "roles": ["super_admin", "strata_admin", "strata_manager"],
        "routes": ["/powerhouse/automation"],
        "depends_on": ["powerhouse_workflow_engine"]
    },
    {
        "feature_key": "bi_analytics_enabled",
        "feature_name": "BI Analytics — Legacy Alias",
        "description": (
            "Backward-compatible alias. Prefer bi_analytics_building_enabled for new configs. "
            "Gates /bi/building/* endpoint reads. Falls back to MongoDB when off. "
            "Resolved by bi_service._toggle_on() and bi_toggle_service."
        ),
        "is_enabled": True,
        "category": "analytics",
        "icon": "BarChart3",
        "roles": ["super_admin", "strata_admin", "strata_manager", "ec_member"],
        "routes": ["/intelligence/bi"],
        "depends_on": []
    },
    # ── Phase 2 BI hierarchy toggles ──────────────────────────────────────────
    {
        "feature_key": "bi_analytics_platform_enabled",
        "feature_name": "BI Analytics — Platform Scope (Super Admin)",
        "description": (
            "Enables platform-wide BI analytics for super admins. "
            "Resolution order: building override → agency override → this platform default. "
            "Enables /api/bi/platform/* endpoints and the Platform Analytics page."
        ),
        "is_enabled": True,
        "category": "analytics",
        "icon": "Globe",
        "roles": ["super_admin"],
        "routes": ["/intelligence/bi/platform"],
        "depends_on": []
    },
    {
        "feature_key": "bi_analytics_agency_enabled",
        "feature_name": "BI Analytics — Agency Portfolio Scope",
        "description": (
            "Enables agency-level portfolio BI for strata_admin (agency principals). "
            "Grants access to /api/bi/agency/{id}/* endpoints across all assigned buildings. "
            "Resolution: checked at agency level before platform default."
        ),
        "is_enabled": True,
        "category": "analytics",
        "icon": "Building2",
        "roles": ["super_admin", "strata_admin"],
        "routes": ["/intelligence/bi/agency"],
        "depends_on": ["bi_analytics_platform_enabled"]
    },
    {
        "feature_key": "bi_analytics_building_enabled",
        "feature_name": "BI Analytics — Building Scope",
        "description": (
            "Enables building-level BI analytics for EC members, strata managers, and admins. "
            "Building-specific override — takes precedence over agency and platform defaults. "
            "Enable per-building after ETL parity confirmed via /api/bi/building/{id}/cutover-status."
        ),
        "is_enabled": True,
        "category": "analytics",
        "icon": "BarChart3",
        "roles": ["super_admin", "strata_admin", "strata_manager", "ec_member"],
        "routes": ["/intelligence/bi"],
        "depends_on": []
    },
    {
        "feature_key": "bi_portfolio_analytics_enabled",
        "feature_name": "BI Analytics — Portfolio Scope (Strata Manager)",
        "description": (
            "Enables cross-building portfolio BI for strata managers. "
            "Strata managers see only their assigned buildings, never the full platform. "
            "Enables /api/bi/manager/{id}/* endpoints."
        ),
        "is_enabled": True,
        "category": "analytics",
        "icon": "Layers",
        "roles": ["super_admin", "strata_admin", "strata_manager"],
        "routes": ["/intelligence/bi/manager/[manager_id]"],
        "depends_on": []
    },
    {
        "feature_key": "bi_pg_primary_enabled",
        "feature_name": "BI Analytics — PostgreSQL Primary Mode",
        "description": (
            "Switches BI reads from MongoDB fallback to PostgreSQL analytics schema as primary. "
            "Independent from BI visibility — only enable after /cutover-status passes all checks. "
            "Safe default = False. Per-building override. Does not affect BI page visibility."
        ),
        "is_enabled": False,
        "category": "analytics",
        "icon": "Database",
        "roles": ["super_admin"],
        "routes": [],
        "depends_on": ["bi_analytics_enabled"]
    },

    # ── Historical Ledger Reconstruction (2026-07-17) ─────────────────────────
    # Originally seeded directly against live PostgreSQL by
    # seeds/seed_historical_reconstruction_toggles.py (create_global_feature_toggle),
    # bypassing this canonical list — that left scripts/audits/toggle_drift.py's
    # PG-vs-seed comparison permanently drifted and blocked
    # scripts/deployment/deploy.sh's protected-toggle gate. Added here,
    # unchanged from what is already live (same feature_key/is_enabled/category),
    # so the seed becomes the source of truth again. None of these three keys
    # appear in core/toggle_classification.py's TOGGLE_CLASSIFICATION map —
    # absent keys default to the safe `visibility` class there, but see that
    # module's own docstring instruction to classify explicitly when a toggle
    # affects a data path; historical_reconstruction_posting now has an
    # explicit ADMIN_ONLY entry there for that reason.
    {
        "feature_key": "historical_financial_reconstruction",
        "feature_name": "Historical Financial Reconstruction",
        "description": (
            "Enables the reconstruction-batch workflow that turns AGM/audited "
            "budget facts into a reviewable Demo Bank transaction manifest "
            "(create/extract/preview/submit-review + GST-aware levy regeneration "
            "dry-run planning). Read/plan-only capability — does not itself post "
            "anything to the ledger."
        ),
        "is_enabled": True,
        "category": "finance",
        "icon": "History",
        "roles": ["super_admin", "strata_admin", "strata_manager"],
        "routes": ["/financials/onboarding"],
        "depends_on": []
    },
    {
        "feature_key": "historical_reconstruction_posting",
        "feature_name": "Historical Reconstruction Posting",
        "description": (
            "Enables the write-side of the reconstruction pipeline: approve, "
            "generate, sync, reverse a reconstruction batch, and apply a levy-item "
            "GST regeneration. Gated separately from "
            "historical_financial_reconstruction because this class of action "
            "mutates finance.levy_items / demo_bank_transactions. Intentionally "
            "enabled globally (documented 2026-07-26/27 onboarding decision): it is "
            "classified ADMIN_ONLY in core/toggle_classification.py — not a protected "
            "data-path toggle — so the actual guard is router RBAC (super_admin / "
            "strata_admin only) plus the per-building expectation that a batch's "
            "dry-run report is reviewed before it is approved and posted. It is not "
            "held OFF globally."
        ),
        "is_enabled": True,
        "category": "finance",
        "icon": "History",
        "roles": ["super_admin", "strata_admin"],
        "routes": ["/financials/onboarding"],
        "depends_on": ["historical_financial_reconstruction"]
    },
    {
        "feature_key": "historical_expense_reconstruction",
        "feature_name": "Historical Expense Reconstruction",
        "description": (
            "Enables import-historical-expenses (CSV) and the expense-side "
            "ledger posting path (FinancialCoreService.record_expense with "
            "derivation_level tiers exact/source_derived/aggregate_balancing)."
        ),
        # Enabled globally 2026-07-27 to match its sibling reconstruction toggles
        # (historical_financial_reconstruction, historical_reconstruction_posting — both
        # flipped True 2026-07-26) — live Postgres already showed is_enabled=True with no
        # blocking precondition in this toggle's own description; the seed had simply never
        # been updated to match, which the AUDIT-13 drift gate correctly caught.
        "is_enabled": True,
        "category": "finance",
        "icon": "Receipt",
        "roles": ["super_admin", "strata_admin", "strata_manager"],
        "routes": ["/financials/onboarding"],
        "depends_on": []
    },
]


async def seed_feature_toggles():
    """Seed default feature toggles as global defaults (building_id=None)."""
    print("Seeding feature toggles...")

    now = datetime.now(timezone.utc)

    for feature in DEFAULT_FEATURES:
        # Global defaults are identified by building_id=None
        existing = await db.feature_toggles.find_one({
            "feature_key": feature["feature_key"],
            "building_id": None,
        })

        if not existing:
            feature_doc = {
                **feature,
                "building_id": None,  # Global default — applies to all buildings unless overridden
                "created_at": now,
                "updated_at": now,
                "updated_by": None  # System-created
            }
            await db.feature_toggles.insert_one(feature_doc)
            print(f"  ✓ Created feature toggle: {feature['feature_name']}")
        else:
            # Patch metadata without overwriting is_enabled.
            # Both 'roles' and 'allowed_roles' are updated so stale values
            # (chairman, treasurer) are removed from whichever field exists.
            patch = {
                "depends_on": feature.get("depends_on", []),
                "updated_at": now,
            }
            if "roles" in feature:
                patch["roles"] = feature["roles"]
                patch["allowed_roles"] = feature["roles"]
            await db.feature_toggles.update_one(
                {"feature_key": feature["feature_key"], "building_id": None},
                {"$set": patch},
            )
            print(f"  ~ Patched metadata for: {feature['feature_name']}")

    print(f"Feature toggles seeded successfully! Total: {len(DEFAULT_FEATURES)}")


async def main():
    """Main entry point."""
    try:
        await seed_feature_toggles()
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
