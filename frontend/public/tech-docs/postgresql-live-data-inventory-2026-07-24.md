Status: current_reference
Owner: Platform engineering
Last validated: 2026-07-24
Scope: Metadata-only PostgreSQL table and populated-field inventory

# Live PostgreSQL Data Inventory - 2026-07-24

This inventory was generated from PostgreSQL metadata plus exact table counts. It lists table names and column names only; it does not include row values, credentials, owner PII, or sampled payloads.

## Snapshot
- PostgreSQL version: `PostgreSQL 16.14 (Ubuntu 16.14-0ubuntu0.24.04.1) on x86_64-pc-linux-gnu`
- Alembic version: `0071_powerhouse_cmd_foundation`
- Application base tables inspected: `202`
- Non-empty application tables: `44`
- Table counts were inspected with `app.tenant_id=9e9d75c2-bd92-4695-8487-1592018c3af9` for East Gate `13195` where applicable.
- Application schemas inspected: `access` (8), `ai_assist` (7), `analytics` (26), `communications` (23), `compliance` (23), `core` (43), `documents` (6), `finance` (30), `governance` (8), `modules` (4), `ops` (18), `sustainability` (6)

## Cutover Control Status

| Building | Domain | Mode | Read | Write | Readiness | Updated |
|---|---|---|---|---|---|---|
| `13195` | `finance_ledger` | `postgres_shadow` | `mongo` | `mongo` | `shadow_active` | `2026-07-24 06:55:39.765973+00:00` |
| `13195` | `governance` | `postgres_write` | `postgres` | `postgres` | `promoted` | `2026-07-24 06:57:18.076183+00:00` |
| `13195` | `identity_core` | `postgres_write` | `postgres` | `postgres` | `promoted` | `2026-07-23 04:02:38.534562+00:00` |
| `13195` | `occupancy` | `postgres_write` | `postgres` | `postgres` | `promoted` | `2026-07-24 06:57:20.137560+00:00` |
| `13195` | `settings` | `postgres_write` | `postgres` | `postgres` | `promoted` | `2026-07-24 06:57:21.380351+00:00` |
| `13195` | `trust_ledger` | `postgres_write` | `postgres` | `postgres` | `promoted` | `2026-07-24 06:57:22.514185+00:00` |
| `13195` | `trust_reconciliation` | `postgres_write` | `postgres` | `postgres` | `promoted` | `2026-07-24 06:57:25.279803+00:00` |
| `99999` | `identity_core` | `mongo_primary` | `mongo` | `mongo` | `blocked` | `2026-07-13 07:46:39.466718+00:00` |

Post-deploy audit note: non-empty PostgreSQL tables do not by themselves mean a domain is
PostgreSQL write-primary. As of the 2026-07-24 07:06 UTC check, every listed East Gate cutover
domain except `finance_ledger` is `postgres_write`. Finance remains blocked in `postgres_shadow` by
live shadow diffs, incomplete expense/reconciliation work, and `financial_pg_writes_enabled=False`.
The 06:57 UTC non-finance write promotions also need follow-up smoke/audit work: trust evidence
still needs retargeting to the live V2 trust system, occupancy needs proof of the PG snapshot-refresh
write contract, and governance remains partial with empty `governance.decisions`.

## Finance Cutover Toggles

| Toggle | Global | Building overrides |
|---|---:|---|
| `bank_integration_abstraction_enabled` | `False` | `13195=True` |
| `external_api_finance_pg_enabled` | `False` | `[]` |
| `financial_integration_layer_v2` | `False` | `13195=True` |
| `financial_pg_reads_enabled` | `False` | `13195=True` |
| `financial_pg_writes_enabled` | `False` | `13195=False` |
| `financial_shadow_reads_enabled` | `False` | `13195=True` |
| `onboarding_current_balance_adapters_enabled` | `False` | `[]` |
| `trust_pg_ledger_enabled` | `False` | `13195=True` |
| `trust_reconciliation_pg_enabled` | `False` | `13195=True` |

## Non-Empty Tables And Populated Fields

| Table | Rows | Columns with at least one non-null value |
|---|---:|---|
| `analytics.bi_alert_rules` | 10 | `rule_id`, `rule_code`, `rule_name`, `rule_config`, `is_active`, `created_at` |
| `analytics.bi_etl_runs` | 3 | `run_id`, `target_table`, `scheme_id`, `started_at`, `completed_at`, `status`, `rows_inserted`, `rows_updated`, `rows_skipped` |
| `analytics.dim_date` | 20454 | `date_id`, `full_date`, `year`, `quarter`, `month`, `month_name`, `week_of_year`, `day_of_week`, `day_of_month`, `is_weekend`, `financial_year`, `financial_quarter`, `financial_month` |
| `analytics.fact_financial_balance` | 36 | `fact_id`, `tenant_id`, `scheme_id`, `period_date`, `financial_year`, `fund_type`, `opening_balance_cents`, `levy_income_cents`, `other_income_cents`, `expenses_cents`, `closing_balance_cents`, `is_test_data`, `created_at`, `source_system`, `source_collection`, `source_id`, `ingested_at`, `confidence`, `is_current` |
| `analytics.fact_occupancy_snapshot` | 87 | `fact_id`, `tenant_id`, `scheme_id`, `snapshot_date`, `lot_number`, `occupancy_type`, `has_active_tenancy`, `is_test_data`, `created_at`, `source_system`, `source_collection`, `source_table`, `source_id`, `source_updated_at`, `ingested_at`, `confidence`, `is_current` |
| `core.alembic_version` | 1 | `version_num` |
| `core.audit_events` | 5 | `audit_event_id`, `tenant_id`, `scheme_id`, `entity_type`, `entity_id`, `action`, `actor_user_id`, `event_payload`, `created_at` |
| `core.building_settings` | 6 | `setting_id`, `tenant_id`, `scheme_id`, `setting_key`, `setting_value`, `set_by`, `set_at` |
| `core.buildings` | 2 | `building_id`, `tenant_id`, `scheme_id`, `building_name`, `street_address`, `suburb`, `state`, `postcode`, `building_type`, `lot_count`, `asset_profile`, `status`, `created_at`, `updated_at` |
| `core.cutover_audit_log` | 18017 | `id`, `tenant_id`, `building_id`, `domain`, `action`, `from_mode`, `to_mode`, `actor_user_id`, `actor_role`, `reason`, `p0_snapshot`, `metadata`, `is_test_data`, `created_at` |
| `core.domain_cutover_status` | 7 | `id`, `tenant_id`, `building_id`, `scheme_id`, `domain`, `read_source`, `write_source`, `mode`, `readiness_status`, `last_readiness_check_at`, `last_shadow_diff_at`, `last_promoted_at`, `promoted_by`, `previous_mode`, `rollback_available`, `notes`, `p0_snapshot`, `is_test_data`, `created_at`, `updated_at` |
| `core.feature_toggle_overrides` | 13 | `override_id`, `tenant_id`, `scheme_id`, `feature_key`, `is_enabled`, `set_by`, `set_at`, `reason` |
| `core.feature_toggles` | 185 | `toggle_id`, `feature_key`, `feature_name`, `description`, `category`, `is_enabled`, `routes`, `allowed_roles`, `depends_on`, `seeded_version`, `last_modified_by`, `last_modified_at`, `created_at`, `icon` |
| `core.lots` | 87 | `lot_id`, `tenant_id`, `scheme_id`, `building_id`, `lot_number`, `unit_number`, `lot_use`, `entitlement_units`, `status`, `created_at`, `updated_at`, `is_test_data`, `metadata` |
| `core.onboarding_sessions` | 5 | `session_id`, `tenant_id`, `scheme_id`, `initiated_by`, `status`, `current_step`, `step_data`, `is_test_data`, `created_at`, `updated_at` |
| `core.outbox` | 4354 | `id`, `tenant_id`, `scheme_id`, `event_type`, `payload`, `created_at`, `attempts`, `event_version` |
| `core.ownership_periods` | 166 | `ownership_period_id`, `tenant_id`, `scheme_id`, `lot_id`, `owner_party_id`, `valid_from`, `valid_to`, `recorded_from`, `source_document_id`, `notes`, `created_at`, `is_primary_owner`, `ownership_share`, `is_test_data` |
| `core.parties` | 250 | `party_id`, `tenant_id`, `party_type`, `legal_name`, `preferred_name`, `primary_email`, `metadata`, `status`, `created_at`, `updated_at`, `is_test_data`, `source_system`, `source_collection`, `source_record_id`, `import_batch_id`, `imported_at`, `secondary_email` |
| `core.role_permissions` | 170 | `role`, `permission_key`, `is_granted` |
| `core.schemes` | 1 | `scheme_id`, `tenant_id`, `jurisdiction`, `scheme_number`, `scheme_name`, `legal_name`, `abn`, `gst_registered`, `gst_rate_basis_points`, `status`, `created_at`, `updated_at`, `is_demo`, `is_test_data` |
| `core.shadow_diffs` | 4934 | `id`, `tenant_id`, `building_id`, `domain`, `route`, `diff_type`, `mongo_value`, `divergence_score`, `resolved`, `resolved_at`, `notes`, `is_test_data`, `created_at` |
| `core.shadow_read_divergences` | 211 | `divergence_id`, `tenant_id`, `scheme_id`, `query_type`, `query_params`, `postgres_result`, `mongodb_result`, `difference_summary`, `detected_at`, `created_at`, `method_name`, `diverging_fields`, `is_test_data` |
| `core.tenants` | 256 | `tenant_id`, `tenant_name`, `abn`, `tenant_type`, `status`, `created_at`, `updated_at`, `is_demo`, `is_self_managed`, `legal_name`, `created_from_trial_id`, `is_test_data` |
| `core.trial_requests` | 2 | `request_id`, `submitted_at`, `status`, `org_name`, `jurisdiction`, `contact_first_name`, `contact_last_name`, `contact_email`, `contact_phone`, `message`, `source_ip`, `notes`, `expires_at` |
| `core.user_role_assignments` | 104 | `assignment_id`, `tenant_id`, `user_id`, `scheme_id`, `role`, `ec_position`, `granted_at`, `is_active` |
| `core.user_units` | 97 | `user_unit_id`, `tenant_id`, `scheme_id`, `user_id`, `lot_id`, `party_id`, `relationship`, `valid_from`, `is_test_data`, `created_at` |
| `core.users` | 105 | `user_id`, `tenant_id`, `party_id`, `email`, `full_name`, `mfa_required`, `status`, `created_at`, `updated_at`, `password_hash`, `first_name`, `last_name`, `role`, `effective_role`, `default_scheme_id`, `is_active`, `is_approved`, `approved_at`, `is_name_flagged`, `totp_enabled`, `is_test_data`, `last_login_at`, `permission_overrides`, `last_login_ip` |
| `finance.accounting_periods` | 7 | `period_id`, `tenant_id`, `scheme_id`, `period_label`, `starts_on`, `ends_on`, `status`, `created_at` |
| `finance.bank_transactions` | 2855 | `bank_transaction_id`, `tenant_id`, `scheme_id`, `trust_account_id`, `transaction_date`, `description`, `reference`, `amount_cents`, `external_transaction_id`, `reconciliation_status`, `created_at`, `provider_name`, `source_type`, `confidence`, `provenance_class`, `evidence_type`, `formula_version`, `source_snapshot_ids`, `requires_review`, `date_basis`, `unit_number`, `transaction_origin`, `reconstruction_batch_id`, `reconstruction_version`, `assumption_code`, `reconstruction_metadata` |
| `finance.evidence_documents` | 10 | `document_id`, `tenant_id`, `scheme_id`, `building_id`, `document_type`, `file_url`, `sha256_hash`, `uploaded_by`, `uploaded_at`, `declared_total_cents`, `metadata`, `is_test_data`, `approved_by`, `approved_at`, `notes`, `source_system`, `declared_totals_by_fund` |
| `finance.financial_cutover_config` | 1 | `config_id`, `tenant_id`, `scheme_id`, `building_id`, `cutover_date`, `onboarded`, `onboarded_at`, `approved_by`, `evidence_document_id`, `journal_entry_ids`, `created_at`, `updated_at`, `metadata`, `is_test_data` |
| `finance.financial_onboarding_audit` | 1 | `audit_id`, `tenant_id`, `scheme_id`, `building_id`, `approved_by`, `approved_at`, `cutover_date`, `evidence_document_id`, `evidence_sha256_hash`, `opening_balances`, `journal_entry_ids`, `is_test_data` |
| `finance.funds` | 3 | `fund_id`, `tenant_id`, `scheme_id`, `fund_code`, `fund_name`, `fund_type`, `status`, `created_at`, `opening_balance_cents` |
| `finance.gl_accounts` | 16 | `gl_account_id`, `tenant_id`, `scheme_id`, `account_code`, `account_name`, `account_type`, `is_control_account`, `status`, `created_at` |
| `finance.journal_entries` | 3198 | `journal_entry_id`, `tenant_id`, `scheme_id`, `fund_id`, `period_id`, `entry_number`, `source_type`, `source_reference`, `narration`, `status`, `effective_on`, `posted_at`, `posted_by`, `approved_by`, `idempotency_key`, `prev_entry_hash`, `entry_hash`, `is_test_data`, `metadata`, `created_at`, `evidence_document_id` |
| `finance.journal_lines` | 6396 | `journal_line_id`, `tenant_id`, `scheme_id`, `journal_entry_id`, `gl_account_id`, `direction`, `amount_cents`, `gst_cents`, `lot_id`, `party_id`, `narration`, `created_at` |
| `finance.levy_items` | 957 | `levy_item_id`, `tenant_id`, `scheme_id`, `levy_run_id`, `lot_id`, `owner_party_id`, `fund_id`, `principal_cents`, `gst_cents`, `interest_cents`, `recovery_costs_cents`, `paid_cents`, `status`, `created_at` |
| `finance.levy_runs` | 6 | `levy_run_id`, `tenant_id`, `scheme_id`, `financial_year`, `quarter_no`, `issue_date`, `due_date`, `status`, `created_at`, `levy_run_type` |
| `finance.receipt_allocations` | 334 | `allocation_id`, `receipt_id`, `levy_item_id`, `allocation_type`, `allocated_cents`, `created_at`, `tenant_id` |
| `finance.receipts` | 2221 | `receipt_id`, `tenant_id`, `scheme_id`, `payer_party_id`, `lot_id`, `channel`, `received_on`, `amount_cents`, `external_reference`, `journal_entry_id`, `created_at`, `reconstruction_batch_id`, `metadata` |
| `finance.trust_accounts` | 2 | `trust_account_id`, `tenant_id`, `scheme_id`, `fund_id`, `bank_name`, `account_name`, `masked_bsb`, `masked_account_number`, `external_uid`, `status`, `created_at` |
| `governance.agm_records` | 2 | `agm_id`, `tenant_id`, `scheme_id`, `meeting_type`, `scheduled_date`, `held_date`, `location`, `status`, `is_test_data`, `created_at`, `updated_at` |
| `governance.by_laws` | 1 | `by_law_id`, `tenant_id`, `scheme_id`, `by_law_number`, `title`, `body`, `effective_from`, `registered_with_actreg`, `is_test_data`, `created_at` |
| `governance.ec_members` | 5 | `ec_member_id`, `tenant_id`, `scheme_id`, `party_id`, `lot_id`, `ec_position`, `elected_at_agm_id`, `term_start`, `is_test_data`, `created_at` |

## Empty Tables

- `access`: `access_device_audit_events`, `access_device_deactivation`, `access_device_issuance`, `access_device_procurement_batches`, `access_device_requests`, `access_device_returns`, `access_device_types`, `access_devices`
- `ai_assist`: `ai_assessments`, `ai_prompt_audit`, `ai_recommendation_evidence`, `ai_recommendations`, `ai_redaction_events`, `ai_review_decisions`, `ai_risk_scores`
- `analytics`: `bi_alert_events`, `bridge_lot_owner`, `dim_building`, `dim_lot`, `dim_owner`, `dim_supplier`, `fact_arrears_snapshot`, `fact_asset_condition_snapshot`, `fact_building_health_snapshot`, `fact_capex_actual`, `fact_capex_plan`, `fact_compliance_event`, `fact_investor_yield_snapshot`, `fact_levy_charge`, `fact_levy_payment`, `fact_ownership_transfer`, `fact_smart_request`, `fact_true_cost_ownership`, `fact_utility_bill`, `fact_work_order`, `login_audit`
- `communications`: `announcements`, `campaign_audience_segments`, `communication_acknowledgements`, `communication_approval_links`, `communication_campaigns`, `communication_delivery_events`, `communication_drafts`, `conversation_links`, `conversation_messages`, `conversation_participants`, `conversation_threads`, `conversation_watchers`, `inboxes`, `letters_log`, `message_ai_suggestions`, `message_ai_summaries`, `message_attachments`, `message_delivery_events`, `newsletter_recipients`, `newsletter_sections`, `newsletters`, `notices`, `thread_entity_links`
- `compliance`: `acat_dispute_events`, `compliance_events`, `compliance_items`, `compliance_registers`, `conflict_disclosures`, `data_breach_log`, `disclosure_events`, `ec_training_records`, `entitlement_schedules`, `generated_artifacts`, `insurance_policies`, `jurisdiction_rule_packs`, `lot_entitlements`, `manager_contracts`, `manager_licences`, `obligation_templates`, `privacy_consents`, `rental_certificates`, `rule_packs`, `tenant_meeting_rights`, `whs_incidents`, `whs_inductions`, `whs_swms`
- `core`: `agencies`, `agency_memberships`, `approval_policies`, `approval_requests`, `approval_steps`, `building_agency_assignments`, `building_manager_assignments`, `command_idempotency_records`, `joint_owner_review`, `legacy_entity_mappings`, `management_entities`, `party_roles`, `saga_runs`, `saga_steps`, `scheme_management_assignments`, `scheme_manager_appointments`, `shadow_read_coverage_daily`, `strata_manager_profiles`, `tenancy_periods`, `user_invitations`, `user_sessions`
- `documents`: `document_access_grants`, `document_audit_events`, `document_folders`, `document_retention_policies`, `document_versions`, `documents`
- `finance`: `bank_statement_imports`, `council_rates`, `expense_evidence_links`, `expense_transactions`, `levy_rules`, `owner_credit_balances`, `payment_batch_items`, `payment_batches`, `payment_plan_installments`, `payment_plans`, `reconciliation_runs`, `trust_interest_postings`, `utility_anomalies`, `utility_bills`, `utility_usage_readings`, `water_bills`
- `governance`: `agm_attendance`, `agm_motions`, `agm_votes`, `by_laws_acks`, `decisions`
- `modules`: `module_hooks`, `module_versions`, `modules`, `tenant_modules`
- `ops`: `case_events`, `case_links`, `cases`, `quote_requests`, `recurring_task_templates`, `service_request_intake_sources`, `service_requests`, `task_assignments`, `task_checklists`, `task_comments`, `task_sla_events`, `task_status_history`, `vendor_assignments`, `vendor_invoices`, `vendor_quotes`, `vendors`, `work_orders`, `work_requests`
- `sustainability`: `building_sustainability_profiles`, `common_area_lighting_assets`, `energy_efficiency_projects`, `lighting_control_recommendations`, `solar_readiness_assessments`, `sustainability_project_benefit_cases`
