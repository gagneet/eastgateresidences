# @featuretrace:coexistence — Tenant-scoped MongoDB wrapper; auto-injects building_id into all queries.
# Layer: domain
# Data flow: routers/services → db.collection.find/insert/update → Mongo (with automatic building_id injection).
# Related: backend/utils/auth.py
#           backend/request_context.py
#           backend/models/
# Collection: All 186 collections are scoped through this wrapper (building-scoped except global collections)
# Tests: tests/backend/test_tenant_isolation_p0t01.py

"""
Database Configuration

MongoDB client and database instance for the application.

GAP-INF-001 (2026-04-28): Migrated from Motor to PyMongo 4.10+ native async client.
  - `AsyncIOMotorClient`    → `pymongo.AsyncMongoClient`
  - `AsyncIOMotorDatabase`  → `pymongo.asynchronous.database.AsyncDatabase`
  - `AsyncIOMotorCollection`→ `pymongo.asynchronous.collection.AsyncCollection`

  Seeds, cron scripts, and standalone migration scripts still use Motor 3.7.1 for now;
  they will be migrated in a follow-up sprint (GAP-INF-001 phase 2).
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from typing import Any, Mapping, Optional, Sequence, Union

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
_client = AsyncMongoClient(mongo_url)
_db = _client[os.environ['DB_NAME']]

# ─── Tenant Isolation Wrapper ───

# Collections that are strictly isolated by building_id
TENANT_SCOPED_COLLECTIONS = {
    "access_cards",
    "activities",
    "agm",
    "agm_attendance",
    "agm_motions",
    "agm_votes",
    "alterations",
    "ballot_entries",
    "ballot_seals",
    "amenity_bookings",
    "building_amenities",
    "announcements",
    "annual_levies",
    "asset_health_scores",
    "asset_templates",
    "audit_logs",
    "auto_reminders_log",
    "bank_reconciliations",
    "benefit_groups",
    "blog_posts",
    "budgets",
    "building_assets",
    "by_laws",
    "by_laws_acknowledgments",
    "capital_replacement_schedule",
    "capital_shock_risks",
    "chat_groups",
    "committee_resolutions",
    "compliance_items",
    # By-law breach / dispute register. Every router handler already filters on
    # building_id explicitly, so this is not closing a live leak — it is closing the
    # same gap GAP-SEC-013 demonstrated on demo_bank_transactions, where a collection
    # in neither set silently fails OPEN: the wrapper injects nothing and one forgotten
    # filter returns every building's rows. These are legal records naming residents.
    "by_law_breach_reports",
    "contractors",
    "conversations",
    "council_rate_payments",
    "council_rates",
    "council_rate_settings",  # routers/council_rates.py — per-building AUV config (one per financial year)
    "defects",
    "document_annotations",  # routers/document_annotations.py — per-doc highlights + comments
    "document_folders",
    "documents",
    "ec_members",
    "emergency_services",
    "events",
    "expense_transactions",
    "facilities",
    "facility_cost_centres",
    "finance",
    "financial_anomalies",
    "financial_categories",
    "financial_documents",
    "funds",
    "financial_forecasts",
    "financial_transactions",
    "financial_years",
    "folders",
    "group_messages",
    "income_transactions",
    "insurance_claims",
    "insurance_policies",
    "intelligence_summary",
    "invoices",
    "lease_documents",
    "letters_log",  # routers/letters.py — generated owner letters history
    "levy_categories",
    "levy_fairness_audit",
    "levy_fairness_results",
    "levy_fairness_results_v2",
    "levy_fairness_snapshots",
    "levy_payments",
    "levy_plans",
    "levy_simulations",
    "levy_stability_snapshots",
    "listings",
    "lot_financial_summary",
    "maintenance_anomalies",
    "maintenance_forecasts",
    "maintenance_requests",
    "maintenance_schedules",
    "market_snapshots",
    "market_stats",
    "marketplace_listings",
    "meetings",
    "messages",
    "move_bookings",
    "notice_acknowledgments",
    "notice_comments",
    "notices",
    "notifications",
    "nsw_reports",
    "nsw_commission_disclosures",  # routers/nsw_compliance.py — GAP-JUR-NSW-007 s.264 commission disclosures
    "nsw_strata_hub_returns",  # routers/nsw_compliance.py — GAP-JUR-NSW-008 annual return records
    "building_manager_duties",  # routers/building_manager_duties.py — GAP-JUR-NSW-013 s.46B duties register
    "nsw_initial_maintenance_schedules",  # routers/nsw_initial_maintenance_schedule.py — GAP-JUR-NSW-005 Schedule 3
    # GAP-SEC-013 — Demo Bank's staging store. Registered 2026-08-27 after auditing all
    # 11 production call sites: every one already passes an explicit building_id, and
    # _inject_bid() deliberately does NOT re-inject when the filter carries one, so
    # registration is a no-op for correct code and a guard for the next caller who
    # forgets. Before this, an unscoped read returned every building's rows with no
    # error — demonstrated live, where a query for East Gate returned 7,688 rows across
    # two buildings. Its two sibling reconstruction collections were already registered;
    # these three were the gap.
    "demo_bank_transactions",
    "demo_bank_accounts",
    "demo_bank_import_batches",
    "outbound_messages",  # GAP-COMMS-003 — held outgoing mail; MUST stay scoped, the
                          # admin console lists and cancels by id and an unscoped read
                          # would expose every building's recipients and subjects
    "owner_transfer_requests",
    "owner_invites",  # GAP-IDENTITY-OWNER-BOOTSTRAP-001 — pending/sent invite records, decoupled from sending
    "outstanding_issues",
    "payment_plans",
    "payments",
    "pet_requests",
    "parcels",
    "private_messages",
    "projections",
    "purchase_orders",
    "quotes",
    "recurring_work_orders",
    "reimbursements",
    "rental_certificates",
    "schedules",
    "scraper_settings",
    "settings",
    "sinking_fund_capital_events",
    "sinking_fund_plan",
    "special_levies",
    "special_levy_forecasts",
    "special_levy_payments",
    "statistics",
    "tenant_renewal_requests",
    "todos",
    "unit_attributes",
    "unit_change_requests",
    "unit_levy_ledger",
    "units",
    "user_notifications",
    "user_units",
    "lot_ownerships",
    "utility_usage",
    "water_bills",
    "whs_incidents",
    "whs_inductions",
    "whs_swms",
    "work_order_approvals",
    "work_order_attachments",
    "work_order_communications",
    "work_order_invoices",
    "work_order_quotes",
    "work_orders",
    "zones",
    "workflow_requests",
    "workflow_runs",
    "quick_polls",
    "group_buying_campaigns",
    "proposals",
    "proposal_votes",
    "savings_events",
    "volunteer_events",
    "volunteer_registrations",
    "building_summaries",
    "lot_accounts",
    "journal_entries",
    "trust_ledger_entries",
    "trust_ledger_accounts",
    "feature_usage_events",
    "adaptive_nav_scores",
    # ── Trust accounting (all scoped per building) ───────────────────────────
    "trust_accounts",
    "trust_accounts_v2",
    "trust_ledger_batches",
    "trust_levy_schedules_v2",
    "trust_reconciliation_matches",
    "trust_reconciliation_runs",
    "trust_transactions_v2",
    # ── Bank feeds & reconciliation ──────────────────────────────────────────
    "bank_feed_runs",
    "bank_statement_lines",
    "bank_transactions",
    "reconciliation_exceptions",
    "matching_results",
    "staging_bank_balances",
    "staging_ledgers",
    "staging_lots",
    "staging_owners",
    "staging_transactions",
    "staging_validation_issues",
    # ── Compliance & decisions ───────────────────────────────────────────────
    "compliance_events",
    "compliance_register_items",
    "compliance_registers",
    "compliance_scores",
    "decisions",
    "decisions_counter",
    "manager_contracts",
    # ── Finance extras ───────────────────────────────────────────────────────
    "levies",
    "sinking_fund_accounts",
    "financial_import_logs",
    "migration_batches",
    "building_financial_health",
    "subsidy_map_cache",
    # ── Community & tenancy extras ───────────────────────────────────────────
    "tenant_maintenance_requests",
    "deft_notifications",
    "unit_utilities",
    "workflow_sla_overrides",
    # ── Market intelligence ──────────────────────────────────────────────────
    "market_intelligence",
    # ── Privacy & security (building-scoped) ─────────────────────────────────
    "privacy_access_requests",
    "privacy_consents",
    "data_breach_log",
    "api_webhooks",
    "audit_records",
    # ── Webhooks / notifications ─────────────────────────────────────────────
    "email_notification_preferences",
    "email_preferences",
    # ── Class A/B Scheme Split ────────────────────────────────────────────────
    "scheme_classes",
    "class_category_allocations",
    "scheme_class_history",
    # ── Levy Scenario Modeller ────────────────────────────────────────────────
    "levy_scenarios",
    # ── Trust bank interest postings & audit ─────────────────────────────────
    "trust_interest_postings",
    "trust_audit_logs",
    # ── Strata roll & financials (building-scoped) ────────────────────────────
    "strata_owners",
    "strata_financials",
    "bank_accounts",
    # ── Previously unregistered active collections (D-001 / D-002 audit fix) ──
    # These collections were confirmed in use by routers/services but were absent
    # from this registry, meaning the TenantScopedDatabase wrapper provided no
    # defence-in-depth auto-injection.  All have explicit building_id filters in
    # their query code; adding them here adds the enforcement layer.
    "conflict_of_interest",  # routers/conflict_of_interest.py
    "tenancies",  # services/tenancy_service.py
    "occupancy_status",  # routers/occupancy.py; services/occupancy_recompute.py
    "occupancy_snapshots",  # routers/occupancy.py; cron/cron_occupancy_recompute.py
    "levy_reminder_settings",  # routers/levy_reminders.py
    "essential_services_log",  # routers/essential_services.py
    "pool_safety_inspections",  # routers/pool_safety.py
    "strata_sync_jobs",  # routers/strata_sync.py
    "property_health_snapshots",  # services/ownerhub_service.py
    "building_financial_health_p2",  # services/building_stress_service.py
    "document_conversion_logs",  # routers/document_converter.py
    "ppm_items",  # routers/portfolio.py; routers/ppm.py
    "key_fob_register",  # server.py
    "financial_stress_scores",  # seeds/phase2_seed.py
    # ── Financial Integration Layer v2 (Phase 1) ─────────────────────────────
    "integration_inbox",  # integrations/mocks/routers/bank_feed_router.py
    "event_log",  # integrations/ domain event store (append-only)
    "mock_biller_allocations",  # integrations/mocks/mock_biller.py
    "mock_accounting_bills",  # integrations/mocks/mock_accounting_source.py
    "payment_runs",  # integrations/mocks/mock_aba_writer.py
    # ── Ledger hardening (Phase 2, migration_012) ────────────────────────────
    "financial_periods",  # domain/period_lifecycle.py — period state machine
    "trust_ledger_seals",  # workers/merkle_seal.py — daily Merkle seals
    "reconciliation_items",  # domain/reconciliation.py — in-flight rec items
    # ── Matching engine (Phase 3, migration_014) ─────────────────────────────
    "match_review_queue",  # integrations/matching/engine.py — review queue
    # ── AP automation (Phase 4, migration_015) ───────────────────────────────
    "invoice_documents",  # domain/invoice_lifecycle.py — AP invoice state machine
    "recurring_bill_templates",  # domain/recurring_bills.py — scheduled invoice generation
    # ── Jurisdictional rules (Phase 5, migration_020) ────────────────────────
    "ownership_periods",  # services/ownership_service.py — bitemporal lot ownership
    "jurisdiction_config",  # services/jurisdiction_service.py — per-building jurisdiction overrides
    # ── Audit-derived registrations (AUDIT-6, 2026-04-30) ─────────────────────
    "payment_approvals",  # routers/special_payments.py — multi-sig trust payment approvals (>$5k ABA / >$10k)
    "ownership_transfer_log",  # server.py — settlement history per building (referenced 9647, 9874)
    # ── Phase F-Zero historical import (C4, 2026-05) ─────────────────────────
    # Deliberately separate from live annual_levies / financial collections.
    # These are import-staging collections; Phase F-prime reconciles them into
    # Postgres and drops them. See ADR-022 §D6.
    "historical_annual_levies",       # import-historical-financials; fund totals per (year, fund_type) — isolated from live annual_levies
    "historical_levy_issuances",      # import-historical-financials; per-lot quarterly levy amounts
    "historical_financial_snapshots", # import-historical-financials; arrears + outstanding snapshots
    # ── Historical reconstruction pipeline (2026-07-17) ──────────────────────
    # See docs/migration/historical_ledger_reconciliation_plan01.md/plan02.md.
    "historical_expense_transactions",  # import-historical-expenses; vendor/invoice/category rows, Phase F-prime input only
    "demo_bank_reconstruction_batches",  # integrations.demo_bank.reconstruction_batch_schemas — workflow state
    "demo_bank_reconstruction_manifests",  # integrations.demo_bank.reconstruction_batch_schemas — immutable approved payload
}

# Collections that are GLOBAL (cross-building)
GLOBAL_COLLECTIONS = {
    "api_keys",
    "buildings",
    "organisations",
    "organisation_buildings",
    "organisation_members",
    "building_onboarding_checklists",
    "notice_templates",
    "property_finance_profiles",
    "owner_properties",
    "cron_runs",
    "email_sent_log",
    "email_settings",
    "trial_requests",
    "feature_toggles",
    "flagged_ips",
    "legal_pages",
    "locks",
    "login_audit_logs",
    "navigation_configs",
    "trust_v1_usage_telemetry",  # BUG-TRUST-001 Stage 1/2: cross-building V1 deprecation audit trail
    "password_change_audit",
    "password_resets",
    "registration_approval_tokens",
    "site_settings",
    "user_feature_access",
    "user_nav_preferences",
    "building_invitations",
    "resident_registration_invites",
    "memberships",
    "users",
    # ── RBAC / access control (global role definitions) ──────────────────────
    "permissions",
    "roles",
    "relationship_tuples",
    "user_roles",
    # ── ACT suburb reference data (global lookup) ────────────────────────────
    "act_suburbs",
    # ── Cross-building scraper job history (D-002 audit fix) ─────────────────
    "scraper_run_logs",  # server.py (news + property scrapers, global)
    # ── Matching engine (Phase 3, migration_014) ─────────────────────────────
    "payer_entities",  # integrations/matching/engine.py — agency BSB registry
    # ── AP automation (Phase 4) ───────────────────────────────────────────────
    "abn_validation_cache",  # integrations/abn_validator.py — 90-day ABN cache (global: ABNs are Australia-wide)
    # ── Jurisdictional rules (Phase 5, migration_020) ────────────────────────
    "jurisdictional_rule_overrides",
    # domain/jurisdictional_rules.py — global statutory rule overrides per jurisdiction
    # ── Audit-derived registrations (AUDIT-6, 2026-04-30) ─────────────────────
    "role_permissions",  # services/permission_service.py — global role→permission slug mapping
}


class _AsyncAggregateCursorProxy:
    """
    Motor-API-compatible proxy for PyMongo's async aggregate coroutine.

    Motor's ``aggregate()`` is synchronous and returns a cursor; callers then
    chain ``.to_list(n)`` or async-iterate.  PyMongo 4.10 ``aggregate()`` is a
    coroutine.  This proxy wraps the coroutine so all Motor-style call sites
    continue to work without modification:

      - ``await db.col.aggregate([...]).to_list(n)``   → still works
      - ``async for doc in db.col.aggregate([...]):``  → still works
    """
    __slots__ = ("_coro",)

    def __init__(self, coro) -> None:
        """Generated function header.

        Function: _AsyncAggregateCursorProxy.__init__
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._coro = coro

    def to_list(self, length: Optional[int]):
        """Generated function header.

        Function: _AsyncAggregateCursorProxy.to_list
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        coro = self._coro

        async def _run() -> list:
            """Generated function header.

            Function: _AsyncAggregateCursorProxy._run
            Path: backend/database.py

            Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
            """
            cursor = await coro
            return await cursor.to_list(length)

        return _run()

    def __aiter__(self):
        """Generated function header.

        Function: _AsyncAggregateCursorProxy.__aiter__
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return self._aiter()

    async def _aiter(self):
        """Generated function header.

        Function: _AsyncAggregateCursorProxy._aiter
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        cursor = await self._coro
        async for doc in cursor:
            yield doc


class TenantCollection:
    """Wrapper for Motor Collection that automatically injects building_id."""

    def __init__(self, collection: AsyncCollection):
        """Generated function header.

        Function: TenantCollection.__init__
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._coll = collection

    def _filter_has_building_id(self, value: Any) -> bool:
        """Generated function header.

        Function: TenantCollection._filter_has_building_id
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if isinstance(value, dict):
            if "building_id" in value:
                return True
            return any(self._filter_has_building_id(v) for v in value.values())
        if isinstance(value, list):
            return any(self._filter_has_building_id(v) for v in value)
        return False

    def _has_explicit_building_id(self, filter: Any) -> bool:
        """Generated function header.

        Function: TenantCollection._has_explicit_building_id
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if not isinstance(filter, dict):
            return False
        if "building_id" in filter or "plan_id" in filter:
            return True
        if "$and" in filter and isinstance(filter["$and"], list):
            return any(
                isinstance(clause, dict) and ("building_id" in clause or "plan_id" in clause)
                for clause in filter["$and"]
            )
        return False

    def _pipeline_has_building_id(self, pipeline: Sequence[Mapping[str, Any]]) -> bool:
        """Generated function header.

        Function: TenantCollection._pipeline_has_building_id
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return any(self._filter_has_building_id(stage) for stage in pipeline)

    def _extract_building_id(self, pipeline: Sequence[Mapping[str, Any]]) -> Optional[str]:
        """Generated function header.

        Function: TenantCollection._extract_building_id
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        for stage in pipeline:
            match = stage.get("$match") if isinstance(stage, dict) else None
            if isinstance(match, dict):
                bid = match.get("building_id") or match.get("plan_id")
                if isinstance(bid, str):
                    return bid
                if isinstance(bid, dict):
                    eq_val = bid.get("$eq")
                    if isinstance(eq_val, str):
                        return eq_val
        return None

    def _inject_bid(self, filter: Any) -> Any:
        """Generated function header.

        Function: TenantCollection._inject_bid
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        from request_context import get_ctx_building_id
        bid = get_ctx_building_id()
        if not bid:
            if filter is None:
                raise RuntimeError(f"Missing building context for tenant-scoped collection '{self._coll.name}'.")
            if isinstance(filter, dict):
                if "$or" in filter or "$nor" in filter:
                    raise RuntimeError(f"Missing building context for tenant-scoped collection '{self._coll.name}'.")
                if self._has_explicit_building_id(filter):
                    return filter
            raise RuntimeError(f"Missing building context for tenant-scoped collection '{self._coll.name}'.")

        # For reading/updating, we allow either building_id or plan_id to match the context bid.
        # This handles the inconsistent schema across collections.
        bid_match = {"$or": [{"building_id": bid}, {"plan_id": bid}]}

        if filter is None:
            return bid_match

        if isinstance(filter, dict):
            # If filter already has building_id or plan_id, do NOT inject again.
            if self._has_explicit_building_id(filter):
                return filter
            if "$and" in filter and isinstance(filter["$and"], list):
                return {**filter, "$and": [*filter["$and"], bid_match]}
            return {"$and": [filter, bid_match]}

        return filter

    def find(self, filter: Optional[Mapping[str, Any]] = None, *args: Any, **kwargs: Any):
        """Generated function header.

        Function: TenantCollection.find
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self._coll.name in TENANT_SCOPED_COLLECTIONS:
            filter = self._inject_bid(filter)
        return self._coll.find(filter, *args, **kwargs)

    async def find_one(self, filter: Optional[Mapping[str, Any]] = None, *args: Any, **kwargs: Any):
        """Generated function header.

        Function: TenantCollection.find_one
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self._coll.name in TENANT_SCOPED_COLLECTIONS:
            filter = self._inject_bid(filter)
        return await self._coll.find_one(filter, *args, **kwargs)

    async def insert_one(self, document: Mapping[str, Any], *args: Any, **kwargs: Any):
        """Generated function header.

        Function: TenantCollection.insert_one
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self._coll.name in TENANT_SCOPED_COLLECTIONS:
            from request_context import get_ctx_building_id
            bid = get_ctx_building_id()
            if bid:
                if "building_id" not in document and "plan_id" not in document:
                    document = {**document, "building_id": bid}
            elif "building_id" not in document and "plan_id" not in document:
                raise RuntimeError(f"Missing building_id/plan_id for tenant-scoped insert into '{self._coll.name}'.")
        return await self._coll.insert_one(document, *args, **kwargs)

    async def insert_many(self, documents: Sequence[Mapping[str, Any]], *args: Any, **kwargs: Any):
        """Generated function header.

        Function: TenantCollection.insert_many
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self._coll.name in TENANT_SCOPED_COLLECTIONS:
            from request_context import get_ctx_building_id
            bid = get_ctx_building_id()
            if bid:
                documents = [{**d, "building_id": bid} if ("building_id" not in d and "plan_id" not in d) else d for d
                             in documents]
            else:
                missing = [d for d in documents if ("building_id" not in d and "plan_id" not in d)]
                if missing:
                    raise RuntimeError(
                        f"Missing building_id/plan_id for tenant-scoped insert into '{self._coll.name}'.")
        return await self._coll.insert_many(documents, *args, **kwargs)

    async def update_one(self, filter: Mapping[str, Any], update: Union[Mapping[str, Any], Sequence[Mapping[str, Any]]],
                         *args: Any, **kwargs: Any):
        """Generated function header.

        Function: TenantCollection.update_one
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self._coll.name in TENANT_SCOPED_COLLECTIONS:
            filter = self._inject_bid(filter)
        return await self._coll.update_one(filter, update, *args, **kwargs)

    async def update_many(self, filter: Mapping[str, Any],
                          update: Union[Mapping[str, Any], Sequence[Mapping[str, Any]]], *args: Any, **kwargs: Any):
        """Generated function header.

        Function: TenantCollection.update_many
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self._coll.name in TENANT_SCOPED_COLLECTIONS:
            filter = self._inject_bid(filter)
        return await self._coll.update_many(filter, update, *args, **kwargs)

    async def replace_one(self, filter: Mapping[str, Any], replacement: Mapping[str, Any], *args: Any, **kwargs: Any):
        """Generated function header.

        Function: TenantCollection.replace_one
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self._coll.name in TENANT_SCOPED_COLLECTIONS:
            filter = self._inject_bid(filter)
            from request_context import get_ctx_building_id
            bid = get_ctx_building_id()
            if bid:
                if "building_id" not in replacement and "plan_id" not in replacement:
                    replacement = {**replacement, "building_id": bid}
            elif "building_id" not in replacement and "plan_id" not in replacement:
                raise RuntimeError(f"Missing building_id/plan_id for tenant-scoped replace in '{self._coll.name}'.")
        return await self._coll.replace_one(filter, replacement, *args, **kwargs)

    async def delete_one(self, filter: Mapping[str, Any], *args: Any, **kwargs: Any):
        """Generated function header.

        Function: TenantCollection.delete_one
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self._coll.name in TENANT_SCOPED_COLLECTIONS:
            filter = self._inject_bid(filter)
        return await self._coll.delete_one(filter, *args, **kwargs)

    async def delete_many(self, filter: Mapping[str, Any], *args: Any, **kwargs: Any):
        """Generated function header.

        Function: TenantCollection.delete_many
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self._coll.name in TENANT_SCOPED_COLLECTIONS:
            filter = self._inject_bid(filter)
        return await self._coll.delete_many(filter, *args, **kwargs)

    def _secure_lookup(self, lookup: Mapping[str, Any], bid: str) -> Mapping[str, Any]:
        """Generated function header.

        Function: TenantCollection._secure_lookup
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        from_collection = lookup.get("from")
        if not from_collection or from_collection in GLOBAL_COLLECTIONS:
            return lookup
        if from_collection not in TENANT_SCOPED_COLLECTIONS:
            return lookup

        if "pipeline" in lookup:
            nested = self._secure_pipeline(list(lookup["pipeline"]), bid)
            pipeline = [{"$match": {"building_id": bid}}, *nested]
            return {**lookup, "pipeline": pipeline}

        local_field = lookup.get("localField")
        foreign_field = lookup.get("foreignField")
        if local_field and foreign_field:
            let_vars = dict(lookup.get("let", {}))
            local_var = "local_id"
            while local_var in let_vars:
                local_var = f"{local_var}_x"
            let_vars[local_var] = f"${local_field}"
            pipeline = [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                {"$eq": [f"${foreign_field}", f"$${local_var}"]},
                                {"$eq": ["$building_id", bid]},
                            ]
                        }
                    }
                }
            ]
            return {
                "from": from_collection,
                "let": let_vars,
                "pipeline": pipeline,
                "as": lookup.get("as"),
            }
        return lookup

    def _secure_union_with(self, stage: Mapping[str, Any], bid: str) -> Mapping[str, Any]:
        """Generated function header.

        Function: TenantCollection._secure_union_with
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        union_with = stage.get("$unionWith")
        if isinstance(union_with, str):
            coll = union_with
            pipeline = []
            rest = {}
        elif isinstance(union_with, dict):
            coll = union_with.get("coll") or union_with.get("from")
            pipeline = list(union_with.get("pipeline", []))
            rest = {k: v for k, v in union_with.items() if k not in {"coll", "from", "pipeline"}}
        else:
            return stage

        if coll in TENANT_SCOPED_COLLECTIONS:
            secured_pipeline = [{"$match": {"building_id": bid}}, *self._secure_pipeline(pipeline, bid)]
            union_with = {"coll": coll, "pipeline": secured_pipeline, **rest}
        elif pipeline:
            union_with = {"coll": coll, "pipeline": self._secure_pipeline(pipeline, bid), **rest}
        return {**stage, "$unionWith": union_with}

    def _secure_graph_lookup(self, stage: Mapping[str, Any], bid: str) -> Mapping[str, Any]:
        """Generated function header.

        Function: TenantCollection._secure_graph_lookup
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        graph_lookup = stage.get("$graphLookup")
        if not isinstance(graph_lookup, dict):
            return stage
        from_collection = graph_lookup.get("from")
        if not from_collection or from_collection in GLOBAL_COLLECTIONS:
            return stage
        if from_collection not in TENANT_SCOPED_COLLECTIONS:
            return stage
        restrict = dict(graph_lookup.get("restrictSearchWithMatch", {}))
        restrict["building_id"] = bid
        return {**stage, "$graphLookup": {**graph_lookup, "restrictSearchWithMatch": restrict}}

    def _secure_stage(self, stage: Mapping[str, Any], bid: str) -> Mapping[str, Any]:
        """Generated function header.

        Function: TenantCollection._secure_stage
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if "$lookup" in stage:
            secured_lookup = self._secure_lookup(stage["$lookup"], bid)
            return {**stage, "$lookup": secured_lookup}
        if "$facet" in stage and isinstance(stage["$facet"], dict):
            secured_facets = {}
            for key, pipeline in stage["$facet"].items():
                if isinstance(pipeline, list):
                    secured_facets[key] = self._secure_pipeline(pipeline, bid)
                else:
                    secured_facets[key] = pipeline
            return {**stage, "$facet": secured_facets}
        if "$unionWith" in stage:
            return self._secure_union_with(stage, bid)
        if "$graphLookup" in stage:
            return self._secure_graph_lookup(stage, bid)
        return stage

    def _secure_pipeline(self, pipeline: Sequence[Mapping[str, Any]], bid: str) -> list[Mapping[str, Any]]:
        """Generated function header.

        Function: TenantCollection._secure_pipeline
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        secured = []
        for stage in pipeline:
            if isinstance(stage, dict):
                secured.append(self._secure_stage(stage, bid))
            else:
                secured.append(stage)
        return secured

    def aggregate(self, pipeline: Sequence[Mapping[str, Any]], *args: Any, **kwargs: Any):
        """Generated function header.

        Function: TenantCollection.aggregate
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self._coll.name in TENANT_SCOPED_COLLECTIONS:
            from request_context import get_ctx_building_id
            bid = get_ctx_building_id()
            if not bid:
                bid = self._extract_building_id(pipeline)
                if not bid:
                    raise RuntimeError(
                        f"Missing building context for tenant-scoped aggregation on '{self._coll.name}'.")

            secured_pipeline = [{"$match": {"building_id": bid}}]
            for stage in pipeline:
                if isinstance(stage, dict):
                    secured_pipeline.append(self._secure_stage(stage, bid))
                else:
                    secured_pipeline.append(stage)
            pipeline = secured_pipeline
        return _AsyncAggregateCursorProxy(self._coll.aggregate(pipeline, *args, **kwargs))

    async def count_documents(self, filter: Mapping[str, Any], *args: Any, **kwargs: Any):
        """Generated function header.

        Function: TenantCollection.count_documents
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self._coll.name in TENANT_SCOPED_COLLECTIONS:
            filter = self._inject_bid(filter)
        return await self._coll.count_documents(filter, *args, **kwargs)

    async def distinct(self, key: str, filter: Optional[Mapping[str, Any]] = None, *args: Any, **kwargs: Any):
        """Generated function header.

        Function: TenantCollection.distinct
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self._coll.name in TENANT_SCOPED_COLLECTIONS:
            filter = self._inject_bid(filter)
        return await self._coll.distinct(key, filter, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        """Generated function header.

        Function: TenantCollection.__getattr__
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return getattr(self._coll, name)


class TenantScopedDatabase:
    """Wrapper for Motor Database that returns TenantCollection wrappers."""

    def __init__(self, database: AsyncDatabase):
        """Generated function header.

        Function: TenantScopedDatabase.__init__
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._db = database
        self._cache = {}

    def __getattr__(self, name: str) -> TenantCollection:
        """Generated function header.

        Function: TenantScopedDatabase.__getattr__
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if name.startswith("_"):
            return getattr(self._db, name)
        if name not in self._cache:
            self._cache[name] = TenantCollection(self._db[name])
        return self._cache[name]

    def __getitem__(self, name: str) -> TenantCollection:
        """Generated function header.

        Function: TenantScopedDatabase.__getitem__
        Path: backend/database.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return self.__getattr__(name)


db = TenantScopedDatabase(_db)
client = _client

# Export database instance
__all__ = ['db', 'client']
