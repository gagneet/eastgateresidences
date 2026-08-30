# Test Guide

`./tests` is the operational entry point for running and understanding the automated suites in this repository.

## Test layout

| Location                  | Suite                 | Notes                                                         |
|---------------------------|-----------------------|---------------------------------------------------------------|
| `tests/backend/`          | Backend pytest suites | Unit, service, router, schema, and integration-style coverage |
| `tests/seed_tests/`       | Seed pytest suites    | Seed invariants and idempotency coverage                      |
| `tests/scripts/`          | Script pytest suites  | Guarded cleanup, extraction, and validation script coverage   |
| `tests/frontend/unit/`    | Frontend Jest/RTL     | Component, hook, page, and utility tests                      |
| `tests/frontend/`         | Playwright            | Browser and integration smoke coverage                        |
| `tests/performance/`      | k6                    | Read/write performance benchmarks for API and page-load paths |

## Data Upload / Strata Sync / Demo Bank tests

Financial CSV bulk import, Strata Sync (portal scraping), and Demo Bank (provider + mock
Biller/ABA/Accounting/OCR) were briefly split into a separate `strataos-demo-integrations` repo and
re-imported as a pip/yarn git dependency; that split was reversed and the code — and its tests —
merged back into this repo (`backend/integrations/demo_bank/`, `backend/routers/{demo_bank,
financial_import,strata_sync}.py`, `backend/services/financial_import_service.py`,
`backend/scripts/{run_scraper,committee_report_scraper,scrape_civium_committee_report,
scrape_committee_report_cli}.py`). Their tests live in `tests/backend/`
like every other suite (`test_finance_upload_endpoints.py`, `test_finance_2026_import.py`,
`test_financial_year_import.py`, `test_strata_sync_owner_transfer_detection.py`,
`test_levy_calendar_sync.py`, `test_owner_unit_balance_sync.py`, `test_bank_feed_router.py`,
`test_csv_bank_feed.py`, `test_demo_bank_provider.py`, `test_demo_scheme_seed.py`,
`test_demo_customer_seed_roles.py`, `test_bank_feed_service_cutover.py`,
`test_committee_report_scraper.py`, `test_scrape_civium_committee_report.py`, and
`test_run_scraper_invoice_reconciliation.py`/`test_run_scraper_levy_sync.py`) — no separate repo,
no separate invocation, just `backend/venv/bin/python3 -m pytest tests/backend -q` from repo root.

## Canonical commands

From the repository root:

```bash
# Python pytest suites
backend/venv/bin/python3 -m pytest -q

# Frontend lint
cd frontend && yarn lint

# Frontend Jest/RTL (discovers tests/frontend/unit)
cd frontend && yarn test --watchAll=false

# Frontend production build
cd frontend && yarn build

# Playwright
npx playwright test tests/frontend/

# k6 performance
k6 run tests/performance/public_api_benchmark.ts
k6 run tests/performance/ui_public_pages_benchmark.js
k6 run tests/performance/owner_dashboard_benchmark.ts -e AUTH_TOKEN=<owner_jwt> -e UNIT_NUMBER=<owner_unit>
```

Useful shortcuts:

```bash
make test-backend
make test-frontend
make test
```

## Targeted runs

### Backend

```bash
# One file
backend/venv/bin/python3 -m pytest tests/backend/test_schema_sync.py -q

# One test
backend/venv/bin/python3 -m pytest tests/backend/test_navigation.py::TestNavigationConfig::test_navigation_config_mode_is_valid -q
```

### Frontend Jest

```bash
cd frontend
yarn test --testPathPatterns=BIAnalyticsPage --watchAll=false
```

### Playwright

```bash
npx playwright test tests/frontend/e2e/rental-certificates.spec.js --reporter=list
```

Playwright only discovers `*.spec.*` files. Auth-required specs should skip cleanly when their required env vars are unset instead of failing during test discovery.

Request catalogue Phase 1B coverage:

```bash
backend/venv/bin/python3 -m pytest tests/backend/test_request_catalogue.py tests/backend/test_request_catalogue_api_contract.py -q
cd frontend && yarn test --testPathPatterns=tests/frontend/unit/pages/dashboard/requests/requestCatalogue.test.js --watchAll=false
cd frontend && yarn test --testPathPatterns=tests/frontend/unit/pages/dashboard/requests/RequestsPage.test.jsx --watchAll=false
npx playwright test tests/frontend/e2e/request-catalogue-journeys.spec.ts --reporter=list
```

The Playwright journey spec must use synthetic TEST-REQUEST tenants via role-specific storage-state files:
`REQUEST_CATALOGUE_E2E_OWNER_STATE`, `REQUEST_CATALOGUE_E2E_TENANT_STATE`,
`REQUEST_CATALOGUE_E2E_AGENT_STATE`, `REQUEST_CATALOGUE_E2E_EC_MEMBER_STATE` and
`REQUEST_CATALOGUE_E2E_MANAGER_STATE`. Do not run it with East Gate production credentials.

### k6

```bash
# Public API read paths
k6 run tests/performance/public_api_benchmark.ts -e BASE_URL=http://localhost:8003/api -e BUILDING_ID=13195

# Public UI page loads
k6 run tests/performance/ui_public_pages_benchmark.js -e UI_BASE_URL=http://localhost:3000

# Authenticated owner dashboard API fan-out plus optional page shell
k6 run tests/performance/owner_dashboard_benchmark.ts \
  -e BASE_URL=http://localhost:8003/api \
  -e AUTH_TOKEN=<owner_jwt> \
  -e UNIT_NUMBER=<owner_unit> \
  -e FINANCIAL_YEAR=2026

# Add UI_BASE_URL and SESSION_COOKIE to include the authenticated Next.js page shell.
k6 run tests/performance/owner_dashboard_benchmark.ts \
  -e BASE_URL=http://localhost:8003/api \
  -e UI_BASE_URL=http://localhost:3000 \
  -e AUTH_TOKEN=<owner_jwt> \
  -e SESSION_COOKIE='<nextauth_session_cookie>' \
  -e UNIT_NUMBER=<owner_unit> \
  -e FINANCIAL_YEAR=2026
```

Legacy multi-building backend tests are now opt-in and require both `RUN_INTEGRATION_TESTS=1` and `RUN_LEGACY_BUILDING_TESTS=1`.

## Notable backend test files

| File                                                      | Coverage                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
|-----------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `tests/backend/test_strata_portal_bridge.py`              | Portal-to-finance bridge: `parse_money`, `portal_summary` in finance summary, `/finance/portal-bank-balances`, `/finance/portal-actuals`, `strata_owners` JOIN in levy-status and unit-levy-ledger, single source of truth (no strata_balance writes to units), multi-tenant isolation. 4 opt-in integration smoke tests via `RUN_INTEGRATION_TESTS=1`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `tests/backend/test_unit_number_canonicalisation.py`      | Unit token normalisation, candidate generation (`87`/`U87`/`Unit 87` → `TH087`), canonical selection from existing `units` rows                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `tests/backend/test_unit_finance_access.py`               | Co-owner finance visibility regression (East Gate TH087): `user_unit_matches` bidirectional ownership match, `resolve_canonical_unit_number` building-scoped lookup, PG-session `_backfill_legacy_unit_context` merge from Mongo users. No DB required (AsyncMock).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `tests/backend/test_unit_display_config.py`               | Per-building unit display prefix rules: `format_unit_display`, rules-driven candidates, `UnitDisplayRule(sUpdate)` validation (overlap/prefix/pad), settings service round-trip, PUT `/settings/unit-display` 403 for owners. No DB required (AsyncMock).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `tests/backend/test_gst.py`                               | GST input tax credit calculations, `get_building_levy_gst_settings`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `tests/backend/test_advance_payments.py`                  | Advance payment detection, `get_levy_status` with `strata_owners` JOIN via `asyncio.gather`, multiple building contexts                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `tests/backend/test_arrears_board.py`                     | Arrears board endpoint with `building_id` explicit injection, multi-tier severity levels (CRITICAL/URGENT/MODERATE/LOW)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `tests/backend/test_rbac_enhancements.py`                 | Full RBAC: `admin_staff` role (canonical name; `reception` is a backward-compat alias), permission map coverage, role elevation, seed user coverage                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `tests/backend/test_staff_management.py`                  | Staff role canonicalization (`admin_staff` not `reception`), ELEVATABLE_ROLES, STAFF_ROLES                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `tests/backend/test_building_switcher.py`                 | Postgres identity-backed `/buildings/me` and `/auth/switch-building` flows, including cross-building membership scoping and JWT building-context refresh                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `tests/backend/test_fy2026_march_actuals.py`              | **Live-DB test**: FY2026 March actuals in `levy_categories` and `annual_levies`. Requires MongoDB auth via `.env` (MONGO_URL, DB_NAME). Skip when DB unavailable.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `tests/backend/test_portal_owner_balances.py`             | Portal balance snapshot validation: `parse_portal_outstanding`, `build_owner_balance_records`, 87 lots, credit/arrears totals                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `tests/backend/test_levy_reminder_settings_pg_cutover.py` | PostgreSQL-first levy reminder settings cutover: `core.building_settings` key `finance.levy_reminders`, Mongo fallback/mirror behavior, and legacy finance endpoint compatibility                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `tests/backend/test_feature_toggle_pg_runtime_regressions.py` | Regression coverage for remaining runtime toggle helpers moved from Mongo reads to the Postgres config repo (`external_api`, `occupancy`, ARQ/scheduler helpers, invoice lifecycle)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `tests/backend/test_toggle_classification_safety.py` | P0.3 toggle safety hardening: canonical classification (`backend/core/toggle_classification.py`) covers every cutover-runtime key; `config_repo` refuses global enable of protected toggles (keyword-only escape hatch, unreachable via HTTP); safety-metadata floor merge; router 403 on protected global enable while per-building override promotion stays open; seed keeps protected toggles globally disabled; migration 0058 contract. No DB required. |
| `tests/backend/test_owner_paid_split.py`             | GAP-FIN-047 — owner-transfer paid split: `_compute_owner_paid_split` (pure PG query), `_get_owner_paid_split_standalone` (non-fatal Mongo-route wrapper), `None` (not `0`) when no `ownership_periods` row, non-fatal on PG outage, and a SQL-text regression guard that the current/previous-owner split uses `IS DISTINCT FROM` (NULL-safe) rather than a plain `!=` that would silently drop a NULL-`payer_party_id` receipt from both sums. No DB required (fake session). |
| `tests/backend/test_interest_penalty.py`             | Per-unit computed arrears interest/late-fee engine (`compute_unit_interest_and_penalty`, East Gate nil-rate + $55/14-day model) plus GAP-FIN-047 §1's `apply_net_credit_override`/`zero_charges`: the TH087 residual-interest-on-a-credit-unit scenario, the `net_balance == 0.0` boundary, genuine-arrears pass-through, and the `0.01` float-rounding tolerance edge. No DB required (pure functions). |

## Recent audit notes

- Building-switching coverage now targets the Postgres identity layer (`db_postgres.repos.identity_repo`) instead of legacy Mongo `memberships`/`buildings` mocks.
- Tests that patch `asyncio.create_task(...)` around audit logging should close the coroutine they intercept, otherwise pytest can emit false-positive `coroutine was never awaited` warnings.
- Playwright teardown removes login-audit rows for the fixed test accounts and registered workflow requests from smart-request tests so repeated browser runs stay clean.
- 2026-08-05 audit of GAP-FIN-047 (`803c6451`, `1026f1b9`): both commits' core logic was correct
  and live-data-verified, but shipped with a coverage gap (the net-credit interest override had
  no unit test) and a latent SQL gap (owner-split `!=` against a nullable column). Both fixed and
  regression-tested; see `docs/fixes/gap_fin_047_audit_20260805.md`.
| `tests/backend/test_new_features.py`                      | Finance seed history (FY2021–FY2022), levy rate breakdowns, multi-building seed data                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `tests/backend/test_trust_dual_approval.py`               | GAP-FIN-011: ABA dual-approval threshold ($5k gate), first/second approve happy paths, same-person rejection, role guard with effective_role, multi-tenant isolation (18 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `tests/backend/test_decision_register.py`                 | GAP-GOV-005: Decision Register CRUD — DEC-YYYY-NNNN numbering, BOLA building_id override, enum validation, role guards (effective_role), search/list/filter scoping, multi-tenant isolation (20 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `tests/backend/test_session_82_fixes.py`                  | Session 82 bug fixes — numeric sort for unit_number and lot_number (lexicographic vs numeric), `QuoteCreate.work_order_id` Optional (no 422 when omitted), notification unread/history endpoints exclude `is_test_data` records, multi-tenant user_id isolation on notification queries (33 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `tests/backend/test_archived_user_restore_workflow.py`    | Archived user return & restore workflow — `_get_staff_registration_reviewers` building-scoped two-step query (memberships.distinct → users.find), super_admin always included, cross-building isolation (16244 IDs absent from 13195 queries), deduplication; `pending_return_details` written to DB on re-registration (not overwriting original archived fields), password_hash updated, 409 with `code=archived_user_return_request`; restore applies pending name/phone/end_date via `$set`, `$unset`s pending key after restore, enforces 400/403/404 guards; unit-change path deactivates old user_units and inserts new record, 409 when new unit has active primary occupant of same role, multi-tenant isolation throughout (31 tests)                                                                                                                                                                        |
| `tests/backend/test_morning_card.py`                      | Morning card urgency priority, SLA breach card `cta_link` points to `/requests` (not the missing `/dashboard/manager`), pluralisation of breach title, role gate (strata_manager + super_admin only), savings milestone shown-to tracking                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `tests/backend/test_comms_intake.py`                      | Message classification, SLA deadline computation, `process_inbound` full pipeline, `_format_timeline_note` — confidence as %, bool as Yes/No, empty list omission, no Python dict repr, SLA breach scheduler idempotency, unassigned request fallback to building managers, deflection rate excludes test data                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `tests/backend/test_multi_unit_ownership.py`              | Multi-unit ownership feature — `_get_user_owned_units` helper (single/empty), registration with `additional_unit_numbers` (invalid unit 400, duplicate dedup, primary-in-additional dedup), `owner_exists_add_unit` 409 vs already-linked 400 vs non-owner generic 400, `POST /auth/switch-unit` (valid/invalid link/not-owner), `GET /auth/my-units` (unit-type enrichment), `POST /auth/add-unit` (owner-only, duplicate guard, admin notification), multi-tenant isolation (building 16244 units not visible in 13195 context), JWT `unit_number` claim injection into `get_current_user`, `UserResponse.owned_units` population. Prerequisite: `_disable_rate_limit()` called per test; `is_test_data=True` on all fixtures (27 tests)                                                                                                                                                                             |
| `tests/backend/test_building_switch_sierra.py`            | Cross-building strata manager flow — Sierra (`16244`) annual levy seeding via `snapshot_annual_levies.py`; `seed_sierra.py` building_id preservation (existing cross-building users must not have their primary `building_id` overwritten by the Sierra seed — else-branch uses `pass`); `snapshot_annual_levies.py` upsert correctness (excludes `created_at` from `$set` to avoid MongoDB WriteError "conflict at 'created_at'"); `/years` endpoint returns levy year list after Sierra seed runs; management dashboard fallback year when `/years` returns empty array                                                                                                                                                                                                                                                                                                                                              |
| `tests/backend/test_invoice_ocr.py`                       | Invoice OCR pipeline — `extract_with_claude` JSON parsing + markdown fence stripping, `parse_invoice` fallback to Mindee on Claude error or low confidence (<0.5), empty scaffold when both keys absent, `RuntimeError` on missing API key. Router: upload-and-parse happy path, unsupported file type (400), oversized file (400), non-finance role (403). Confirm: creates transaction + marks confirmed + receipt flag, rejects already-confirmed (409), rejects zero total (400), rejects blank vendor (400). Delete: pending succeeds, confirmed blocked (409). Multi-tenant isolation: building A invoice not visible in building B (18 tests). Prerequisite: `set_ctx_building_id("13195")` called in async tests; multi-tenant isolation asserted via `building_id` scoping (router writes `is_test_data=False`; isolation verified by seeding under building A and confirming building B query returns empty) |
| `tests/backend/integrations/test_envelope.py`             | Financial Integration Layer v2 — EventEnvelope and envelope types: frozen model enforcement, tenant_id presence, ULID 26-char format and sort order, SHA-256 idempotency key derivation, BankTxObserved Cents=int enforcement, BillerReference/PaymentLine/OCRFieldResult shapes (25 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `tests/backend/integrations/test_csv_bank_feed.py`        | Financial Integration Layer v2 — CsvUploadBankFeed: CBA signed-amount parsing, ANZ split Debit/Credit columns, BPAY CRN MOD10V05 extraction, NPP E2E ID extraction, lot reference extraction, `skip_rows` semantics, provider_txn_id determinism, multi-tenant isolation (both building_id and tenant_id scoped), supported_banks list, `parse_csv_bytes` error on unknown bank (43 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `tests/backend/integrations/test_mock_biller.py`          | Financial Integration Layer v2 — MockBiller: MOD10V05 CRN validation, scheme_id derivation for numeric/non-numeric building_ids, deterministic CRN build from lot/instalment, allocate idempotency via duplicate-key handling, validate_mod10v05 positive/negative cases, multi-tenant isolation on allocation reads (34 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `tests/backend/integrations/test_mock_aba.py`             | Financial Integration Layer v2 — MockAbaWriter: all ABA lines are exactly 120 chars, field-position assertions (BSB at 1-7, amount at 21-30, lodgement at 63-80, item count at 75-80 in total record), debit/credit balance invariant, SHA-256 file hash stored, idempotency via run_id (30 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `tests/backend/integrations/test_registry.py`             | Financial Integration Layer v2 — ProviderRegistry singleton, `register_mock_providers()` populates all 5 protocol slots, `get_bank_feed()` returns mock for unregistered building, preference resolution reads `integration_provider_preference` from db.settings (27 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `tests/backend/integrations/test_bank_feed_router.py`     | Financial Integration Layer v2 — upload endpoint: bulk `insert_many(ordered=False)` called once (no N+1), `BulkWriteError` duplicate vs real-error counting, empty CSV skips insert, role guard (owner→403, chairman+super_admin→200), 10 MB file size limit, multi-tenant `tenant_id` on all inserted docs (11 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `tests/backend/test_alembic_0012.py`                      | Postgres RLS isolation: 7 tenant-scoped tables have RLS + FORCE enabled, global tables don't; `building_settings` row from tenant A is invisible to tenant B session; alembic revision is at least 0015 (Phase E minimum). Requires DATABASE_URL (skipped otherwise).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `tests/backend/test_invitation_rls_bypass.py`             | Migration 0015 (Phase E): `find_invitation_by_token()` uses bypass sentinel UUID to find invitations without knowing tenant_id; claimed/expired/cancelled invitations return None; wrong tenant cannot read invitation; bypass sentinel does not expose wrong-hash lookups (5 tests, require DATABASE_URL).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `tests/backend/test_building_switcher.py`                 | JWT building_id behavior: `DEFAULT_BUILDING_ID` must be `""` (clean-break, not "13195"); login with no active memberships omits building_id from JWT; documents the Phase E clean-break invariant.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `tests/backend/test_sentinel_feature_toggle_hardening.py` | Feature gate enforcement: unapproved owner→403, service_provider→403, approved owner→200, super_admin→200 on levy-kpi endpoint; all toggles respected with effective_role resolution.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `tests/backend/test_cross_collection_write_audit.py`      | Cross-collection write-path audit — 32 tests (Sprint H-1 extended suite). Covers: invoice approve/reject/pay, meeting create/update, AGM create/motion create/vote cast, proxy submit/revoke, ballot close, document upload/delete, essential service update, announcement delete, compliance register inspection result (audit_log + fail notification to responsible person), work-order EC approval submission (per-vote audit_log), work-order approval completion (completion audit_log + requester notification on approve and reject). Also asserts building_id isolation throughout. Prerequisites: none (all mocked). |

## Group 1 Gap Closure router tests

Five router test files introduced for the Group 1 compliance/financial/governance gap closures. All are unit tests (
mocked DB, no live database, no teardown required). Every mock document includes `building_id`; every suite asserts
cross-building isolation via `BUILDING_A = "16244"` vs `BUILDING_B = "13195"`.

| File                                                 | Gap ID          | Coverage                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
|------------------------------------------------------|-----------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `tests/backend/routers/test_payment_plans.py`        | GAP-JUR-NSW-002 | NSW Form 1 payment plan lifecycle (18 tests): UUID `id` returned on create (not ObjectId), `building_id` stamped on insert, invalid `instalment_frequency` → 400, manager list query has no `owner_id` filter, owner list query includes `owner_id`, status filter accepted / invalid status → 400, 404 on missing plan, owner cannot view another owner's plan (403), owner cannot approve or decline (403), approve/decline 409 when plan not in `requested` status, `update_plan_status` 409 for all terminal statuses (declined/completed/cancelled), cross-building isolation on get and list, approve/decline set the correct DB update fields |
| `tests/backend/routers/test_levy_reminders.py`       | GAP-FIN-012     | Levy reminder cadence settings (8 tests): defaults returned when unconfigured, DB doc returned when configured, upsert scoped to `building_id`, empty payload → 400, dry-run returns overdue units above threshold, ledger query scoped to building, owner role → 403, log pagination response shape                                                                                                                                                                                                                                                                                                                                                 |
| `tests/backend/routers/test_conflict_of_interest.py` | GAP-GOV-007     | Conflict-of-interest register (9 tests): create doc with `building_id` + `retracted=False`, invalid conflict type → 400, owner cannot declare (403), list scoped to building, `retracted={"$ne":True}` filter by default, 404 on missing declaration, update requires manager (403), retract sets `retracted=True` (soft delete), cross-building isolation                                                                                                                                                                                                                                                                                           |
| `tests/backend/routers/test_essential_services.py`   | GAP-COM-008     | Essential services compliance log (8 tests): create with auto-calculated `next_due`, `building_id` stamped, invalid service type → 400, owner can list (read access), manager required to create, 404 on missing record, overdue list scoped to building, cross-building isolation                                                                                                                                                                                                                                                                                                                                                                   |
| `tests/backend/routers/test_arrears_risk.py`         | GAP-FIN-006     | Predictive arrears risk (7 tests): summary scoped to building, unit list returns risk bands, per-unit detail, finance role required (owner → 403), empty result when no units, cross-building isolation, CRITICAL/HIGH/MODERATE/LOW band values                                                                                                                                                                                                                                                                                                                                                                                                      |

Prerequisites: none (all mocked). Run with:

```bash
backend/venv/bin/python3 -m pytest tests/backend/routers/ -v
```

Toggle coverage for all 5 features lives in `tests/backend/test_access_control_and_toggles.py` under
`TestGroup1GapToggleKeys` (8 tests added): seed presence, enabled-by-default, routes non-empty, correct categories, and
`payment_plans` toggle-on/toggle-off effective-access assertions via the 3-tier access logic.
| `tests/backend/integrations/contract_tests.py` | Financial Integration Layer v2 — Protocol contract suite:
BankFeedProvider, BillerProvider, AbaWriterProvider, AccountingSource, OCRProvider — verifies that each mock satisfies
the structural Protocol without `isinstance()` inheritance, method signatures match, return types correct (33 tests) |
| `tests/backend/test_no_floats_in_domain.py` | Linting rule — enforces that `backend/domain/` contains no float
literals and no float type annotations (`float`, `Float`); also verifies `Cents` is a strict `int` type at both
annotation and runtime (5 tests) |
| `tests/backend/domain/test_journals.py` | Phase 2 — Journal domain model invariants: ≥2 lines, every amount_cents
gt=0 (field-level), exact integer-cent balance (sum debits == sum credits, no float tolerance), control_total_cents,
building_id isolation, concurrent independent objects (15 tests) |
| `tests/backend/domain/test_period_lifecycle.py` | Phase 2 — Period lifecycle state machine: all 5 valid transitions
pass, 7 invalid transitions raise ValueError with both state names, LOCKED is terminal, can_post_to_period allows OPEN
and raises HTTP 409 for RECONCILING/CLOSED/AUDITED/LOCKED with reversal hint (22 tests) |
| `tests/backend/domain/test_merkle_seal.py` | Phase 2 — Merkle seal: empty/single/even/odd leaf counts, determinism,
leaf mutation breaks root, verify_seal detects tampered amount/hash/extra entry, seal_period writes correct fields +
filesystem export, hash chain links to prior seal, multi-tenant building isolation (19 tests) |
| `tests/backend/domain/test_reconciliation.py` | Phase 2 — Three-way reconciliation: perfect agreement, DIT +
unpresented adjustments, 1-cent zero-tolerance failure, each of 3 legs disagrees independently, assert_agrees raises
ValueError with full numeric breakdown, frozen immutable result (14 tests) |
| `tests/backend/test_trust_accounting_phase2.py` | Phase 2 router-level — Period guard (OPEN→201,
CLOSED/RECONCILING/LOCKED→409, not-found→404), fund_type in period lookup query, cross-building period isolation (
BUILDING_B cannot reach BUILDING_A period), backward compat (no period_id skips guard), unbalanced debit/credit→400,
debit_total_cents/credit_total_cents/period_id stored on entry, effective_role guards (elevated owner passes, downgraded
manager blocked) (16 tests) |

## Phase 3 — Matching and Auto-Allocation Engine

Eight-layer matching pipeline, MatchingEngine orchestrator, and review queue router. All tests are unit tests (no live
DB, no teardown required). Every mock document includes `building_id` / `tenant_id`; cross-building isolation is
asserted in each suite using `BUILDING_A = "16244"` vs `BUILDING_B = "13195"`. Test data is fully in-memory — no
`is_test_data` cleanup needed.

| File                                                 | Coverage                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
|------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `tests/backend/integrations/matching/test_layers.py` | Eight matching layers (L1–L8) in isolation — no DB, pure function calls. L1 ExactCRN: MOD10V05 checksum validation (valid/invalid/non-numeric/too-short), score=1.0 on match, score=0 when no CRN in tx, score=0 on invalid checksum, score=0 when no lot has that CRN, cross-building CRN isolation (CRN from BUILDING_B never matches BUILDING_A lots). L2 NppE2E: score=0.95 on e2e match, 0 when absent/mismatch/lot-has-no-e2e. L3 PartialCRN: CRN prefix in description → 0.90, empty desc → 0, no match → 0, lot-without-CRN skipped. L4 UnitRefAmountTiming: unit+amount+≤30day-window → 0.85, outside 30 days → 0, wrong amount → 0, no unit ref → 0, "LOT N" description format. L5 JWNameExactAmount: JW≥0.88+exact-amount → 0.80, low-similarity → 0, right-name-wrong-amount → 0, empty description → 0. L6 SurnameFuzzy: token_sort_ratio≥80 → 0.60, no match → 0, empty desc → 0, picks-best-when-multiple-near-matches. L7 ExactAmountUnique: single-lot-with-amount → 0.50, ambiguous (2 lots same amount) → 0, no-lot-with-exact-amount → 0. L8 Unidentified: always 0.0 with reason="no_layer_matched", empty candidates (35 tests) |
| `tests/backend/integrations/matching/test_engine.py` | MatchingEngine orchestration: auto-allocates when L1 score≥0.90, stops at first threshold layer (only L1 in all_scores), creates review queue entry when below threshold (L4→0.85), unidentified when no layer matches, agency sweep routes to review queue when known entity + amount>max_levy, agency sweep not triggered for unknown entity, agency sweep not triggered when amount equals largest levy, idempotent replay returns existing queue doc without re-insert, multi-tenant: queue doc uses tx.tenant_id not lot.building_id, custom high threshold routes L1 to review, custom low threshold auto-allocates L4 (11 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `tests/backend/routers/test_financial_matching.py`   | Financial matching router: GET /queue returns pending items, GET /queue excludes is_test_data (filter verified via call_args), cross-building isolation (empty result when scoped wrapper returns nothing), GET /queue/{id} returns item (item_id + best_score verified), invalid ObjectId → 404, not-found → 404, POST /decide allocate emits MatchDecisionRecorded to event_log with frozen candidates_snapshot, POST /decide reject records decision, POST /decide unidentified records decision, POST /decide already-decided → 409, POST /decide allocate missing lot_id → 422, POST /decide allocate missing amount_cents → 422, POST /decide not-found → 404, GET /stats queue_depth+sla_breach_count+total_last_7_days+auto_allocated+auto_match_rate, GET /stats zero rate when no activity (15 tests)                                                                                                                                                                                                                                                                                                                                        |
| `tests/backend/routers/test_financial_matching_bulk.py` | POST /queue/bulk-decide (backlog clearance, see `docs/runbooks/east_gate_match_queue_backlog_clearance_2026_07_03.md`): request model rejects action=allocate / short notes / max_items>1000, dry_run defaults true; lot-code extraction (LOT/Unit prefix required, date fragments like "12/02/2025" never misread, unknown codes → None); RBAC (owner/tenant → 403 before any DB call, elevated owner with effective_role=ec_member allowed, strata_manager allowed); dry run makes zero writes and reports matched_total/selected/identified_lot_count; filter shape (outflow → tx.amount_cents $lt 0, inflow → $gt 0, is_test_data excluded, no hardcoded building_id — TenantScopedDatabase injects it); real run race-guards each update on status="pending", skips concurrently-decided items, writes MatchDecisionRecorded events sharing one bulk_operation_id, no event insert when nothing decided; unidentified action maps to unidentified status with extracted_lot_code recorded; tenant isolation (22 tests) |
| `tests/backend/test_strata_web_balance_inference.py` | GAP-FIN-015 criterion 3: `derive_strata_web_balance_delta_transactions()` — positive/explainable balance delta creates a high-confidence candidate ($0 fully-reconciled unit), accrued jurisdiction-aware interest is included in the inferred amount, negative/unexplained delta creates no candidate, `annual_levies` fetched exactly once regardless of unit count (N+1 guard), no-previous-snapshot returns zero candidates with a warning. Criterion 8 (added 2026-07-13): high-confidence candidates still carry `requires_review=True` and a `source_type` excluded from `financial_matching._PROMOTABLE_SOURCE_TYPES` (never auto-allocate, by design — see GAP-FIN-015 criterion 8), idempotent re-run produces byte-identical `_upsert_transaction` idempotency-key inputs, multi-tenant isolation (building `16244` query never touches `13195` snapshot data) (8 tests) |
| `tests/backend/test_historical_levy_reconstruction_service.py` | GAP-FIN-015 criterion 6: `synthesize_historical_levy_payments()` — one admin+sinking row per unit per quarter, idempotent re-run creates zero duplicates, building-agnostic (a non-`13195` building_id behaves identically, no hardcoded fallback), multi-tenant isolation, zero-rate year skips only that fund's rows (2021-style combined levy), missing `annual_levies`/`units` raises `ValueError`, `dry_run` performs zero writes, future-dated quarters excluded (9 tests) |
| `tests/backend/test_sentinel_historical_reconstruction_rbac.py` | Security/RBAC for `POST /financial/matching/historical-reconstruction/run`: owner role → 403, strata_manager/ec_member → 200, request has no `building_id` field (cross-tenant triggering structurally impossible, not just policy-gated), `from_year > to_year` → 400 before any DB call, a service-layer `ValueError` surfaces as 409 (not a raw 500) (6 tests) |

Prerequisites: none (all mocked). Run with:

```bash
backend/venv/bin/python3 -m pytest tests/backend/integrations/matching/ tests/backend/routers/test_financial_matching.py tests/backend/routers/test_financial_matching_bulk.py tests/backend/test_strata_web_balance_inference.py tests/backend/test_historical_levy_reconstruction_service.py tests/backend/test_sentinel_historical_reconstruction_rbac.py -q
```

Performance: `tests/performance/historical_reconstruction_benchmark.ts` exercises
`POST /financial/matching/historical-reconstruction/run` in `dry_run: true` mode only — this
endpoint's real mode writes non-`is_test_data`-tagged financial rows by design (genuine
per-building reconstructions, not disposable fixtures), so a load test must never invoke it for
real. `teardown()` is a documented no-op since dry-run mode is guaranteed to write nothing.

Feature toggle coverage: `financial_integration_layer_v2` (routes matching review page) and
`matching_auto_allocate_enabled` (controls auto-post to ledger) are seeded in `backend/seeds/feature_toggles.py`. Both
default to `is_enabled=False` and require `super_admin` role. Toggle-on/off behaviour is verified at the engine level
via the `auto_match_threshold` parameter — below threshold → review queue (toggle-off equivalent), above threshold →
auto-allocate (toggle-on equivalent).

## Audit Gap Closure — Branch `fix/audit-gaps-issues`

New test files covering multi-tenant audit fixes. All are pure unit tests (no live DB or running backend required).
Every mock document includes `building_id`; multi-tenant isolation is asserted for all three buildings (East Gate
`13195`, Sierra `16244`, Harbourview `18932`).

| File                                               | Coverage                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
|----------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `tests/backend/test_pdf_generator_po_invoice.py`   | `generate_purchase_order_pdf` and `generate_invoice_pdf` — building_settings.strata_address used as footer (not hardcoded), building_address fallback, empty footer when settings absent, multi-tenant: Sierra and East Gate produce distinct PDFs and never contaminate each other, XSS-safe address escaping, neither function raises `BuildingSettingsIncompleteError` (35 tests)                                                                                                                                                                                                  |
| `tests/backend/test_maintenance_pdf_handlers.py`   | Maintenance router PDF handlers — `get_purchase_order_pdf` and `get_invoice_pdf` each call `get_general_settings_or_default` with the correct `building_id` and forward the result as `building_settings` to the PDF generators; 403/404/500 error paths; DB queries include `building_id`; Sierra settings not substituted by East Gate settings (17 tests)                                                                                                                                                                                                                          |
| `tests/backend/test_council_rates_per_building.py` | Council rates per-building resolution — `_get_block_auv()` pure function: configured vs fallback, estimated flag, Sierra AUV ($4,708,728) distinct from East Gate default, zero/None fallback; `_get_council_settings()`: query scoped by `building_id`, empty dict when no record, East Gate record not returned for Sierra query; `total_block_entitlement` priority and fallback; `report_service._generate_full_report_impl`: source code verified to use dynamic `building_id` in council_rate_settings query; AUV string formatting (configured vs "not configured") (19 tests) |
| `tests/backend/test_settings_pg_cutover.py`        | PostgreSQL-first settings cutover — `get_general_settings()` prefers `core.building_settings` (`general.settings`) and falls back to Mongo `settings`, `upsert_general_settings()` writes the merged payload to Postgres, and `PUT /settings` uses the shared PG-backed settings service path (4 tests)                                                                                                                                                                                                                                                                                     |
| `tests/backend/test_feature_toggles_pg_cutover.py` | PostgreSQL-first feature toggle cutover — router reads/writes use `core.feature_toggles` / `core.feature_toggle_overrides`, user overrides use `core.users.permission_overrides`, `get_effective_feature_access()` respects PG user overrides, and `refresh_rate_limit_config()` reads the PG global `rate_limiting` toggle (5 tests)                                                                                                                                                                                                                                                  |

Run all three with:

```bash
backend/venv/bin/python3 -m pytest tests/backend/test_pdf_generator_po_invoice.py tests/backend/test_maintenance_pdf_handlers.py tests/backend/test_council_rates_per_building.py -v
```

Prerequisites: `pdfminer.six` must be installed in backend venv (used for PDF text extraction in pdf generator tests).
No live DB or running backend required for any of these tests.

## Operational Gap Closure — Branch `fix/gaps-and-issues-updates`

Six new test files covering the top-6 operational gaps identified in the 2026-04-27 audit. All are pure unit tests (no
live DB). Every mock document includes `building_id`; cross-building isolation is asserted for buildings `13195` and
`16244`.

| File                                         | Gap         | Coverage                                                                                                                                                                                                                                                     |
|----------------------------------------------|-------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `tests/backend/test_insurance_management.py` | GAP-OPS-001 | Insurance policy/broker models, `_determine_status` / `_days_until` / `_compliance_check` helpers, PLI minimum enforcement, multi-tenant isolation, seed data coverage for all 3 buildings (20 tests)                                                        |
| `tests/backend/test_insurance_claims.py`     | GAP-OPS-001 | Insurance claim CRUD — create/list/get/update-status endpoints, `_get_db` patch pattern, positional `create_audit_log` assertion, permission guards (403), 404 on missing claim (15 tests)                                                                   |
| `tests/backend/test_arrears_recovery.py`     | GAP-OPS-004 | Arrears recovery state machine — threshold logic (30/60/90 day bands), manual action recording, suppress/resume, LOD fee posting, permission guards (manager-only actions), cross-building isolation (13 tests)                                              |
| `tests/backend/test_document_requests.py`    | GAP-OPS-002 | Document request workflow — auto-fulfil for eligible types (AGM minutes, by-laws, insurance cert), 14-day statutory deadline, owner-only-own-requests visibility, manager-sees-all, 6-state machine transitions, overdue list (15 tests)                     |
| `tests/backend/test_by_law_breach.py`        | GAP-OPS-005 | By-law breach workflow — `BreachStatus.TRANSITIONS` state machine, tribunal export shape (`breach_report`, `timeline`, `notices`, `jurisdiction_note`), notice validation, multi-tenant isolation (16 tests)                                                 |
| `tests/backend/test_building_handovers.py`   | GAP-OPS-006 | Manager handover workflow — `_completion_pct` helper, 22-item default checklist, one-active-per-building guard (409), auto-advance to `in_progress` on first tick, complete-with-warning when checklist incomplete, role guards (ec_member → 403) (18 tests) |

Run all six with:

```bash
backend/venv/bin/python3 -m pytest tests/backend/test_insurance_management.py tests/backend/test_insurance_claims.py tests/backend/test_arrears_recovery.py tests/backend/test_document_requests.py tests/backend/test_by_law_breach.py tests/backend/test_building_handovers.py -q
```

Prerequisites: none (all mocked). No `is_test_data` cleanup required.

## Jurisdiction Gap Closure — Branch `feat/gap-jurisdiction-act-002` (May 2026)

Three new test files covering the NSW jurisdiction and maintenance gap closures shipped in May 2026. All are pure
unit tests (no live DB). Every mock document includes `building_id`; cross-building isolation is asserted for
buildings `13195` and `16244`.

| File                                             | Gap IDs                                | Coverage                                                                                                                                                                                                                                                                                                                          |
|--------------------------------------------------|----------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `tests/backend/test_nsw_compliance_gaps.py`      | GAP-JUR-NSW-007, GAP-JUR-NSW-008       | 22 tests — NSW SSMA s.264 insurance commission disclosures (create/list/get/update/soft-cancel, role guards 403 for owner/guest, multi-tenant isolation, Pydantic validation, hard-delete never called); NSW SSMA s.182 Strata Hub annual returns (create/list/get/update, role guards, multi-tenant isolation, is_test_data stamp) |
| `tests/backend/test_defects_register.py`         | GAP-MNT-001                            | 35 tests — 8 pure unit tests for `_compute_warranty_deadline()` (ACT 6/2yr, NSW 6/2yr, VIC/QLD periods, Feb-29 edge case, exact calendar-year arithmetic); integration tests: create/list/warranty-summary aggregate/get/update/soft-cancel/notes/photos; role guards (owner 403, manager 200), multi-tenant isolation, `delete_one` never called |

Run with:

```bash
backend/venv/bin/python3 -m pytest tests/backend/test_nsw_compliance_gaps.py tests/backend/test_defects_register.py -v
```

Prerequisites: none (all mocked). No `is_test_data` cleanup required (all docs are in-memory).

Key invariants tested:
- `_compute_warranty_deadline()` uses `date.replace(year=+N)` (exact calendar year), not `timedelta(days=N*365.25)`.
  The Feb-29 edge case advances to March 1 of the target year.
- Soft-cancel is the only delete path — `db.defects.delete_one` must never be called (asserted via `assert_not_called()`).
- `create_audit_log` called with positional `action` first (no `db` arg — signature: `create_audit_log(action, resource_type, resource_id, user_id, user_name, ...)`).
- Warranty deadline is recomputed when `practical_completion_date` or `jurisdiction` changes on update.

## Notable frontend test files

| File                                                                           | Coverage                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
|--------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `tests/frontend/unit/pages/dashboard/admin/BuildingOnboardingForm.test.tsx` | 8-step building onboarding wizard — access gate (owner blocked, admin/manager allowed), step 1 identity validation (unit plan required, building name required, active organisation tenant required for SA callers, derived building_id preview), `deriveId()` helper unit tests (UP/SP prefix strip, lowercase, spaces), step 2 address validation, step 3 lot types, step 6 CHAIRMAN requirement, XP system, tenant neutrality (no hardcoded "East Gate" or "13195"), full 8-step navigation, `POST /admin/onboarding/scheme` payload shape (30 tests)                                                                                                                                                                                                                                                                                                                                 |
| `tests/frontend/unit/pages/dashboard/BugFixesSess82.test.tsx`               | Session 82 frontend fixes — RequestStatusPage role-aware card (resident sees "What happens next?", managers/admins see staff action card, SLA-breached variant, closed/auto-resolved show nothing), CreateStaffUserPage buildings fetch path `/buildings/me` (no double `/api/` prefix) (8 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `tests/frontend/unit/components/UnitSwitcher.test.tsx`                      | UnitSwitcher component — feature toggle gate (`multi_unit_ownership`), role gate (owner/manager only, not tenant), single-unit static display, multi-unit dropdown: all options rendered, aria-current on active, Active badge, `switchUnit()` called on option click, no-op on active unit click, fallback to `user.owned_units` when `availableUnits` empty (13 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `tests/frontend/unit/components/widgets/FinancialSummaryCard.test.tsx`      | FinancialSummaryCard "Next Estimated Payment" sub-label — all 5 priority branches: fully paid, prior-year credit (including regression: credit owner with `any_overdue=true` must NOT see overdue message), overdue+prior-arrears (dollar amount + fallback), missed current instalment (no prior-year component), prior-year arrears carried forward (not yet overdue), advance payment credited, no sub-label baseline (9 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `tests/frontend/unit/pages/auth/RegisterPage.multiunit.test.tsx`            | RegisterPage multi-unit additions — checkbox hidden before role/unit selected, hidden for tenant/guest, shown for owner with primary unit, additional slot add/remove/hide-on-uncheck, `additional_unit_numbers` in submit payload when checked, omitted when unchecked, `owner_exists_add_unit` 409 → redirect toast with 8s duration (11 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `tests/frontend/unit/pages/auth/RegisterPage.test.jsx`                      | RegisterPage archived-user return — 409 with `code=archived_user_return_request` shows blue informational banner (not toast.error), banner text displayed, no login/reset links, `already_registered` 409 shows amber banner, `pending_approval` 409 shows blue banner, `pending_now_approved` 409 shows green banner, unknown 409 code → toast.error, 400 → toast.error, successful registration redirects to /register/success with correct building_id payload, `confirmPassword` stripped from payload, client-side validation prevents submit (22 tests)                                                                                                                                                                                                                                                                                                                                                                                        |
| `tests/backend/test_register_duplicate_email.py`                               | Duplicate-email registration paths — Path A (has password_hash → 409 already_registered with unit in message, unit omitted when none on account, no DB writes); Path B (imported/MRI account → claim: update_one not insert_one, existing user id preserved, membership not duplicated when one exists, ObjectId-only record uses _id filter) (8 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `tests/frontend/unit/pages/dashboard/ManagementDashboard.test.tsx`          | ManagementDashboard null/zero data resilience — `?? 0` replaces `\|\| 124`, `\|\| 82`, `\|\| 100` hardcoded fallbacks so missing metrics render as zero rather than fake data; building name fallback uses `'your building'` instead of hardcoded `'East Gate'`; all metrics render without crashing when API returns null or partial data                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `tests/frontend/unit/pages/dashboard/InvoicesPage.test.jsx`                 | Invoice & Quote page — heading + upload buttons render, summary stats (total/pending/confirmed counts), both invoice rows in table, status badges (Pending Review/Confirmed), OCR source badges (Claude Vision/Mindee), upload modal opens/closes, empty state when no invoices, client-side search filters by vendor name, delete API called + row removed for pending invoice, non-manager redirected to /dashboard (11 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `tests/frontend/unit/pages/dashboard/financial/MatchingReviewPage.test.tsx` | Phase 3 — Payment matching review queue UI. Access control: non-manager redirected via `router.replace`, loading guard renders null, managers see queue. Queue rendering: item description, amount in AUD cents, match-type badge, best-score %, layer name, empty-state when no items. Stats bar: renders with queue depth, zero auto_match_rate. API errors: error-banner on fetch failure, retry button refetches. Allocate flow: modal opens on Allocate click, pre-fills best_lot_id, POST /decide called with allocate action on confirm, cancel closes modal without API call. Reject + unidentified: POST /decide called with correct action. Search: input renders, status filter group renders, filtering by description hides non-matching items. Expand/collapse: scores panel visible after expand click. Multi-tenant isolation: only current-building items rendered (BUILDING_A="16244"). Refresh: triggers new API fetch (23 tests) |
| `tests/frontend/unit/pages/dashboard/NotificationsPage.test.jsx`            | Notifications page levy-reminder compatibility flow — initial load fetches `/notifications`, `/levy-reminder-settings`, and `/levy-reminder-log`; reminder settings render from the legacy response shape; save sends the legacy `reminder_days` payload back to `/levy-reminder-settings`; manual send triggers `/notifications/levy-reminder`; permission gating hides levy-reminder controls when the user lacks notification-send access (4 tests) |

## Phase 4 — AP Automation Pipeline

End-to-end accounts payable: OCR invoice ingestion, ABN validation, duplicate detection, invoice lifecycle state
machine, recurring bill templates. All unit tests — no live DB required.

Run:

```bash
backend/venv/bin/python3 -m pytest tests/backend/integrations/test_abn_validator.py tests/backend/domain/test_invoice_lifecycle.py tests/backend/domain/test_duplicate_detection.py tests/backend/domain/test_recurring_bills.py tests/backend/routers/test_ap_supplier_upload.py tests/backend/routers/test_ap_approval.py -q
```

| Test file                                                                       | Coverage                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
|---------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `tests/backend/integrations/test_abn_validator.py`                              | ABN checksum (mod-89 weighted), normalise_abn, parse_abr_response, cache hit/miss, ABR offline fallback (no ABR_API_GUID), withholding threshold ($75 ex-GST boundary), valid/invalid/cancelled/NotFound ABN states (25 tests)                                                                                                                                                                                                                                                     |
| `tests/backend/domain/test_invoice_lifecycle.py`                                | _assert_transition_allowed (allowed/not-permitted/terminal), mandatory field check (missing vendor_name/description/multiple), transition_invoice happy paths (draft→submitted, submitted→validated, rejected, history push), not-found error, NSW SSMA s.102 budget cap (within/over/no-cap), QLD BCCM committee cap (within/exceeds/ACT-no-gate), payment scheduling toggle on/off (23 tests)                                                                                    |
| `tests/backend/domain/test_duplicate_detection.py`                              | normalise_ocr_text, content_sha256, levenshtein distance, Layer 1 exact match (detected/no-match/exclude_id/missing-abn skips-L1), Layer 2 near-dup (detected/edit-distance-3-not-flagged/bounded-.to_list(20)), Layer 3 content hash (match/no-hash skips L3), cross-building isolation (different building not flagged, tenant_id in L1+L2 queries) (22 tests)                                                                                                                   |
| `tests/backend/domain/test_recurring_bills.py`                                  | period_label, idempotency_key, trailing_median (3/last-3/two/one/empty), fixed_monthly (instantiates/idempotent-skips/beyond-horizon), variable_cadence (trailing-median/fallback-to-template), one_off (past-due/deactivates-template/future-skipped/missing-due-date), multi-tenant (correct building_id inserted, is_active=True filter, error-in-one-template-does-not-abort-others) (20 tests)                                                                                |
| `tests/backend/routers/test_ap_supplier_upload.py`                              | Permission gate (owner-403/service-provider-allowed), PDF/text accepted/unsupported-mime-422/file-too-large-413, duplicate 409, withholding flag on invalid ABN, draft inserted with correct fields (tenant_id/building_id/submitted_by/is_test_data/content_sha256), OCR confidence in response, low_confidence_fields flagged at &lt;0.70 threshold (10 tests)                                                                                                                   |
| `tests/backend/routers/test_ap_approval.py`                                     | Manager guard (manager/chairman/super_admin allowed; owner/ec_member 403; effective_role used), list invoices (returns list, filters by state with is_test_data filter, owner-403), get invoice (returns detail, not-found-404, invalid-OID-404), submit (success, invalid-transition-409), validate (no-dup-succeeds, dup-409, exclude_id passed), approve (success, decided_by-email), reject (success, NSW-gate-422), schedule (success, toggle-off-409) (23 tests)             |
| `tests/frontend/unit/pages/dashboard/financial/APApprovalQueuePage.test.tsx` | Access control (non-manager redirect, auth-loading null, manager renders). State tabs (all 6 rendered, tab-click reloads with new status param). Invoice list (vendor name/invoice#, AUD total, ABN-valid/invalid flag, withholding badge, empty state). Detail panel (opens on click, OCR %, withholding warning, Approve+Reject for validated, Schedule for approved). Actions (approve/reject POST call, action-error on failure). Refresh button. Load error banner (17 tests) |
| `tests/frontend/unit/pages/supplier/InvoiceUploadPage.test.tsx`              | Render (drop zone, submit disabled). File selection (filename shown, submit enabled, drag-and-drop). Submission (POST /ap/supplier-upload/ called, result card shown, vendor name, OCR%, ABN verified). Withholding (warning shown/not-shown). Low confidence fields (shown/not-shown). Errors (generic error, duplicate-409 with invoice ID). Reset flow (upload-another resets form) (21 tests)                                                                                  |

Prerequisites: No env vars required. All DB calls are mocked. ABN_API_GUID not needed (offline fallback tested).

## Phase 5 — Jurisdictional Rule Engine + Bitemporal Ownership

```bash
backend/venv/bin/python3 -m pytest tests/backend/domain/test_jurisdictional_rules.py tests/backend/services/test_ownership_service.py tests/backend/routers/test_jurisdictional_rules_router.py tests/backend/routers/test_settlement_adjustment.py -q
```

| Test file                                                                       | Coverage                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
|---------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `tests/backend/domain/test_jurisdictional_rules.py`                             | ACT (UTMA 2011): 14-day AGM, 3yr contract, $250K audit, 10% levy interest, $10M PLI, 5yr rental cert (2025-01-09 effective). NSW (SSMA 2015): 7-day AGM, 3 insurance quotes, hardship required. VIC (OCA 2006): trial_balance 21 days, pooled_trust False. QLD (BCCM 1997): $200/lot committee cap, no transfer ledger. Engine: invalid jurisdiction → ValueError, get_meta structure, valid_jurisdictions() list, determinism across repeated calls (49 tests)                                                                                                                                                                                                                                                           |
| `tests/backend/services/test_ownership_service.py`                              | resolve_owner: returns owner within period, None when no record, naive datetime UTC conversion, cross-building isolation. get_ownership_history: ordered history, empty list. record_ownership_transfer: records new transfer with building_id, idempotent on duplicate settlement date (no double-insert), naive settlement date UTC conversion, cross-building isolation (10 tests)                                                                                                                                                                                                                                                                                                                                     |
| `tests/backend/routers/test_jurisdictional_rules_router.py`                     | GET /jurisdictional-rules/: building rules returned with jurisdiction+metadata+typed_rules+full_rules (ACT+QLD building IDs, NSW trial_balance=21, meta contains legislation, QLD can_transfer_between_funds=False). Role guard: owner → 403, tenant → 403, chairman/strata_manager/super_admin allowed; elevated owner with effective_role=ec_member → 403 (ec_member is NOT in the allowed set — the test verifies effective_role is used, not raw role). GET /jurisdictional-rules/all: all 4 jurisdictions returned with typed_rules (QLD transfer=False, NSW interest=statutory_account, VIC pooled_trust=False). GET /jurisdictional-rules/supported: any authenticated user can list jurisdiction codes (17 tests) |
| `tests/backend/routers/test_settlement_adjustment.py`                           | GET /settlement-adjustment/{lot_id}: pro-rata split (outgoing+incoming sums to total levy), amounts are integers not floats, 404 when no ownership record, 404 for future settlement date, 422 for bad date format, 403 for owner role, no active levy period returns adjustment=None with message, cross-building isolation (building_id in DB filter), ec_member allowed. GET /settlement-adjustment/{lot_id}/history: _id stripped from history records, empty list returned when no records, ec_member → 403 (history endpoint has stricter guard than adjustment endpoint) (12 tests)                                                                                                                                |
| `tests/frontend/unit/pages/dashboard/admin/JurisdictionalRulesPage.test.tsx` | Page title renders, API fetch hits /jurisdictional-rules/ for current building, jurisdiction name displayed, typed-rules table with key/value/unit/legislation columns, statutory citation links, refresh button refetches, error alert on API failure, tab switching, admin-only guard redirects non-manager (10 tests)                                                                                                                                                                                                                                                                                                                                                                                                  |

Migration: `backend/scripts/migrations/migration_020_jurisdictional_collections.py`
Creates `ownership_periods` (3 indexes: unique compound, desc lookup, is_test_data) and
`jurisdictional_rule_overrides` (jurisdiction_unique). Patches `buildings.jurisdiction` default to "ACT".

Prerequisites: No env vars required. All DB calls mocked. Domain tests (`test_jurisdictional_rules.py`) have
no DB dependency — pure function calls on the `rule_engine` singleton.

## Phase 6 — ARQ Tasks, Temporal Workflows, Idempotency

Run the full Phase 6 suite (no external services required — all DB calls and Temporal activities mocked):

```bash
backend/venv/bin/python3 -m pytest tests/backend/workflows/ tests/backend/workers/ tests/backend/scripts/ -q
```

| File                                                            | What it covers                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
|-----------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `tests/backend/workers/test_arq_tasks.py`                       | Toggle guard: bank_feed_poll/interest_accrual/levy_reminders each return `{skipped:True}` when toggle OFF. BankFeedPollTask: new envelope upserted (upserted_id set), duplicate not re-inserted (upserted_id None), missing provider returns 0 envelopes with error key. InterestAccrualTask: calls run_interest_accrual with correct building_id, 16244 gets 16244 not 13195. LevyReminderDispatchTask: passes building_id and tier=1. DailyMerkleSealTask: seals existing period, no-period returns {sealed:False,reason:"no_period"}, idempotent on same date (13 tests) |
| `tests/backend/workers/test_idempotency_concurrent.py`          | RebuildProjection: repeated rebuild produces same count, concurrent rebuilds for 13195 and 16244 use separate DB handles (isolated), alias consistency (lot_balances_projection == unit_levy_ledger). ARQ idempotency: bank_feed_poll returns 1 then 0 on duplicate, merkle_seal returns same hash on second call. Concurrent isolation: interest_accrual and levy_reminder tasks called concurrently for two buildings each receive their own building_id (7 tests)                                                                                                        |
| `tests/backend/workflows/conftest.py`                           | Autouse `skip_without_temporal` fixture — skips all 10 Temporal tests unless `TEMPORAL_AVAILABLE=1` is set. Prevents offline CI failures caused by Temporal downloading the test-server binary. |
| `tests/backend/workflows/test_generate_levies_workflow.py`      | Temporal time-skipping embedded server (no external process — requires `TEMPORAL_AVAILABLE=1`). lot count == 3, building_id and quarter in result, each lot has CRN after allocate_crns, is_test_data=True reaches send_levy_notices, 16244 tasks never see 13195 (5 tests) |
| `tests/backend/workflows/test_reconciliation_close_workflow.py` | Temporal time-skipping (requires `TEMPORAL_AVAILABLE=1`). Balanced period closes without signal; result contains building_id + period_id; discrepancy blocks until reviewer_approved signal is sent; notify_reviewer_activity called on discrepancy (tracking_notify gets building_id + discrepancy_cents); notify_reviewer NOT called when balanced (5 tests) |
| `tests/backend/scripts/test_rebuild_projection.py`              | rebuild_projection: aggregates levy_payments and upserts unit_levy_ledger (count=2), lot_balances_projection alias produces count=1, unknown projection raises ValueError, empty levy_payments returns count=0, aggregate pipeline contains building_id in $match (multi-tenant isolation), two sequential rebuilds both return count=1 with upsert called twice (idempotent) (6 tests)                                                                                                                                                                                     |
| `tests/scripts/test_remove_legacy_mongo_collections.py`         | Legacy Mongo cleanup guardrails — production runs refused, dry-run leaves collections intact, typed confirmation enforced, 2026-06-04 stability gate blocks destructive mode unless override flag is supplied, confirm path drops `organisations` + `trial_requests` in the test database and logs affected documents (6 tests)                                                                                                                                                                                                                                                             |

## PostgreSQL schema tests (Phase B — D-prime migration)

These tests target the `strataos` PostgreSQL database (migrations 0010–0012).
They require `DATABASE_URL` in `backend/.env` pointing to the `strataos` DB.

| File                                               | What it covers                                                                                                                                                                                                                                                                                 |
|----------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `tests/backend/test_alembic_0010.py`               | Verifies migration 0010: `core.users`, `core.user_units`, `core.user_role_assignments`, `core.user_sessions`, `core.user_invitations` tables exist with expected columns; `core.user_role` enum exists; `core.user_effective_role()` function exists (7 tests)                                 |
| `tests/backend/test_alembic_0011.py`               | Verifies migration 0011: `core.feature_toggles`, `core.feature_toggle_overrides`, `core.building_settings`, `core.role_permissions` tables exist; `core.feature_toggle_resolved()` function exists and resolves toggles correctly (3-tier: scheme override → global default → false) (8 tests) |
| `tests/backend/test_alembic_0012.py`               | Verifies migrations through Phase E: RLS policies exist on all 7 tenant-scoped tables; `building_settings` isolation — tenant A's rows are invisible when GUC is set to tenant B's UUID; alembic revision is at least `0015` (Phase E minimum: rls_invitation_bypass) (4 tests)                |
| `tests/backend/test_user_effective_role_fn.py`     | 4-tier `user_effective_role()` SQL function: fallback to base role, permanent scheme assignment, global assignment, temp elevation overrides permanent, expired temp elevation falls through, inactive assignment ignored, unknown user returns `guest` (7 tests)                              |
| `tests/backend/test_feature_toggle_resolved_fn.py` | `feature_toggle_resolved()` SQL function: unknown key → false, global true with no override, global false with no override, scheme override true beats global false, scheme override false beats global true, other-scheme override does not affect this scheme (6 tests)                      |
| `tests/backend/test_orm_identity_models.py`        | ORM round-trip tests: `FeatureToggle` insert/fetch, `RolePermission` insert/fetch, `User` insert + RLS isolation (tenant A row invisible to tenant B), `BuildingSetting` insert/fetch + isolation (4 tests)                                                                                    |

Run prerequisites: `DATABASE_URL` in `backend/.env`; PostgreSQL `strataos` must be at revision `0012`.

```bash
backend/venv/bin/python3 -m pytest tests/backend/test_alembic_0010.py tests/backend/test_alembic_0011.py tests/backend/test_alembic_0012.py tests/backend/test_user_effective_role_fn.py tests/backend/test_feature_toggle_resolved_fn.py tests/backend/test_orm_identity_models.py -v
```

Key implementation notes:

- `SET LOCAL app.tenant_id = '<uuid>'` must be inside an explicit transaction; asyncpg does not accept bind parameters
  for GUC `SET LOCAL`.
- All test cleanup (DELETE) for RLS-scoped tables (`core.users`, `core.schemes`, `core.building_settings`) must also run
  inside `async with conn.transaction(): SET LOCAL; DELETE`.
- `core.tenants` has no RLS — can be deleted outside a transaction context.

Prerequisites: No env vars required. No Redis or Temporal server required.

- ARQ tests: all DB/cron calls patched with AsyncMock.
- Temporal tests: `WorkflowEnvironment.start_time_skipping()` downloads the Temporal CLI binary on first run (~5s), then
  runs an embedded server. Activities are replaced with mock implementations decorated `@activity.defn(name=...)`.
- rebuild_projection tests: Motor aggregate cursor mocked with `cursor.to_list = AsyncMock(return_value=rows)`.

## Onboarding Workflow Tests (Migration 0028 — May 2026)

These tests cover the SM-organisation + self-managed-scheme onboarding
spec (`docs/architecture/onboarding/`). They require a live Postgres DB
(`DATABASE_URL` env var) and the migration applied to head.

| File                                            | What it covers                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
|-------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `tests/backend/test_alembic_0028_onboarding.py` | Migration 0028 schema invariants: new `core.tenants` columns (`is_self_managed`, `legal_name`, `created_from_trial_id`), partial-active ABN uniqueness, self-managed 1:1 trigger, `core.trial_requests` table + enum, one-open-submission-per-email backstop (12 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `tests/backend/test_sm_organisations.py`        | SM-Organisations endpoints: happy paths for self-managed and SM-managed direct-create, error codes (`abn_already_active`, invalid jurisdiction, invalid invitation role, non-SA 403), self-managed 1:1 trigger via direct SQL (7 tests)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `tests/backend/test_onboarding_e2e.py`          | End-to-end self-managed onboarding flow + cross-tenant RLS isolation assertion (1 test)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `tests/backend/test_get_scheme_helpers_rls.py`  | Regression for the SA switch-building 404 loop — `get_scheme_by_id` / `get_scheme_by_number` must set the RLS bypass sentinel before SELECTing `core.schemes`, AND the bypass must be transaction-local (`is_local=true`) so it does not leak across pooled connections. Includes a leak-detection test that spawns 10 connections after a helper call and asserts none of them inherit the bypass GUC (5 tests)                                                                                                                                                                                                                                                                                                                      |
| `tests/backend/test_is_test_data_filter.py`     | Migration 0029: `is_test_data` filter on `core.tenants`/`core.schemes`. Asserts `list_all_active_schemes`, `list_schemes_for_user`, `get_scheme_by_id`, `get_scheme_by_number` all hide `is_test_data=TRUE` rows from the SA building switcher and switch-building lookup. Also asserts `services.bootstrap_demo.ensure_demo_chain` short-circuits when an active demo chain already exists (idempotent on the cheap path), and that the demo chain is never flagged `is_test_data=TRUE` (so the pytest session-end sweeper cannot delete it). Conftest `pytest_sessionfinish` hook is the safety net that TRUNCATEs every `is_test_data=TRUE` row at the end of every run regardless of whether per-test cleanup succeeded (6 tests) |

Run prerequisites: `DATABASE_URL` set; migration `0029` applied via `alembic upgrade head`.

## Phase 1 operations foundation tests (Migration 0042)

These tests cover the Phase 1 schema-only foundation for the new strata-manager
operating-system wave. They require a live Postgres DB (`DATABASE_URL`) and
the migration applied to head.

| File | What it covers |
| --- | --- |
| `tests/backend/test_alembic_0042_phase1_ops_foundation.py` | Migration 0042 smoke coverage: new schemas (`access`, `ai_assist`, `sustainability`), representative tables/indexes/RLS policies, default-off feature toggle seeds, and the `ops.cases` invalid-status-transition guard. |

Targeted run:

```bash
backend/venv/bin/python3 -m pytest tests/backend/test_alembic_0042_phase1_ops_foundation.py -q
```

## Phase 2 ops case API tests

| File | What it covers |
| --- | --- |
| `tests/backend/test_ops_cases_router.py` | Router coverage for the first Postgres-backed unified ops case slice: request-body guardrails (no `building_id` body input), dependency-derived building context, manager-only assignment/audit routes, and list-filter wiring. |
| `tests/backend/test_communications_intake_router.py` | Inbound-email intake route guardrails: webhook signature enforcement, no `building_id` in request bodies, manager-only queue access, and queue filter/building wiring for the Postgres-backed communications intake queue. |
| `tests/backend/test_communications_campaigns_router.py` | Campaign/newsletter route coverage for the PG-backed communications slice: newsletter create wiring, campaign update/preview/send flows, approval wiring via `submit-approval`, delivery-event and acknowledgement payload wiring, and body validation that blocks `building_id` request input. |
| `tests/backend/test_ops_repairs_router.py` | Repairs/vendor route coverage for the Postgres-backed ops slice: service-provider list wiring, service-request creation wiring, vendor assignment, recurring-template body guardrails, and recurring-task generation wiring. |
| `tests/backend/test_access_lifecycle_router.py` | Access lifecycle route coverage for the PG-backed access slice: request creation guardrails, device-list wiring, issue/disable action wiring, and body validation that blocks `building_id` request input. |
| `tests/backend/test_ai_review_router.py` | AI review-panel route coverage for the PG-backed assessment slice: building-assessment run wiring, recommendation list filters, recommendation approval/case-conversion wiring, evidence routing, and body validation that blocks `building_id` request input. |
| `tests/backend/test_ai_review_service.py` | AI review service guardrail coverage for the Phase 2 review slice: approval-policy validation, recommendation state-machine restrictions, and request-model validation that forbids subject overrides outside auth-scoped building context. |
| `tests/backend/test_utilities_workflows_router.py` | Utility and sustainability route coverage for the PG-backed workflow slice: utility-bill import wiring, anomaly-to-case conversion, sustainability assessment runs, recommendation-to-project conversion, project approval wiring, and body validation that blocks `building_id` request input. |

Targeted run:

```bash
backend/venv/bin/python3 -m pytest tests/backend/test_ops_cases_router.py -q
backend/venv/bin/python3 -m pytest tests/backend/test_communications_intake_router.py -q
backend/venv/bin/python3 -m pytest tests/backend/test_communications_campaigns_router.py -q
backend/venv/bin/python3 -m pytest tests/backend/test_ops_repairs_router.py -q
backend/venv/bin/python3 -m pytest tests/backend/test_access_lifecycle_router.py -q
backend/venv/bin/python3 -m pytest tests/backend/test_ai_review_router.py -q
backend/venv/bin/python3 -m pytest tests/backend/test_ai_review_service.py -q
backend/venv/bin/python3 -m pytest tests/backend/test_utilities_workflows_router.py -q
```

```bash
# All onboarding tests (and the is_test_data filter regressions)
DATABASE_URL=postgresql+asyncpg://… backend/venv/bin/python3 -m pytest \
    tests/backend/test_alembic_0028_onboarding.py \
    tests/backend/test_sm_organisations.py \
    tests/backend/test_onboarding_e2e.py \
    tests/backend/test_is_test_data_filter.py -v
```

> **Test-data hygiene rule.** Any test that inserts into `core.tenants`,
> `core.schemes`, `core.lots`, `core.onboarding_sessions`, or
> `core.user_invitations` MUST set `is_test_data=TRUE` (direct SQL) or
> pass `_is_test_data=True` (router calls). The `pytest_sessionfinish`
> hook in `tests/backend/conftest.py` sweeps every flagged row at the end
> of the run; un-flagged test rows leak into the SA building switcher
> on the next login.

Key invariants enforced by tests:

- Self-managed tenants (`core.tenants.is_self_managed=TRUE`) may only own one scheme. Second scheme insert returns
  `unique_violation` from the `schemes_self_managed_one_per_tenant` trigger; surfaced to API as HTTP 409 with code
  `self_managed_already_has_scheme`.
- Active ABNs are unique across `core.tenants`. The partial unique index `tenants_abn_unique_active` only fires for
  `status='active'`, so an archived org's ABN can be reused. Surfaced as HTTP 409 `abn_already_active`.
- One open trial-request per email at a time (status='submitted'). Once rejected/approved, a new submission for the same
  email is allowed.
- Cross-tenant RLS: a lot inserted under `target_tenant_id` is invisible when querying as the SA's own platform tenant (
  Postgres RLS, exercised live via the e2e test).

## Phase C Tests (Postgres Auth Repoint + Onboarding)

These tests target the `strataos` PostgreSQL database migrations 0013–0015,
which add auth support and onboarding endpoints to Postgres.
Requires `DATABASE_URL` pointing to the `strataos` DB.

| File                                      | What it covers                                                                                                                                                                                                                                                                                                                                                                                     |
|-------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `tests/backend/test_alembic_0013.py`      | Migration 0013: `last_login_ip` column on `core.users`; `core.find_user_for_auth(email TEXT)` function (SECURITY DEFINER); `core.onboarding_sessions` table with session_id/scheme_id/status columns (5 tests)                                                                                                                                                                                     |
| `tests/backend/test_auth_repoint.py`      | Auth endpoints repointed to Postgres: `login` returns JWT with `tenant_id` claim; `get_current_user` resolves Postgres user; onboarding flow (start/status/finalize); invitations (create/claim). Includes cleanup teardown for all test data (idempotent). Note: 4 tests marked skip due to FastAPI TestClient event loop conflict with asyncpg; run manually in dev (6 active tests, 4 skipped). |
| `tests/backend/test_pg_identity_repo.py`  | Postgres identity repository functions: `find_user_for_auth` (SECURITY DEFINER bypass); `get_user_by_id` with tenant scoping; user creation + role assignment; last_login tracking; invitation lifecycle (create/find_by_token/claim). All tests idempotent with MongoDB-level cleanup (9 tests)                                                                                                   |
| `tests/backend/test_register_endpoint.py` | Register endpoint baseline (Phase C — still writes to MongoDB). Tests: cleanup idempotency, user creation/teardown, multi-tenant isolation, batch cleanup. Core data layer tests avoiding TestClient event loop conflicts (5 tests). Phase D will repoint register to Postgres.                                                                                                                    |

Run prerequisites: `DATABASE_URL` in `backend/.env`; PostgreSQL `strataos` at revision `0015`.

```bash
# All Phase C tests
backend/venv/bin/python3 -m pytest tests/backend/test_alembic_0013.py tests/backend/test_auth_repoint.py tests/backend/test_pg_identity_repo.py tests/backend/test_register_endpoint.py -v

# Skip TestClient-conflict tests
backend/venv/bin/python3 -m pytest tests/backend/test_auth_repoint.py tests/backend/test_pg_identity_repo.py tests/backend/test_register_endpoint.py -v

# Migration schema tests only
backend/venv/bin/python3 -m pytest tests/backend/test_alembic_0013.py -v
```

Key implementation notes (Phase C):

- **RLS bypass pattern**: `find_user_for_auth()` is SECURITY DEFINER and uses RLS policy bypass with sentinel UUID
  `00000000-0000-0000-0000-000000000000` to enable pre-auth cross-tenant lookups.
- **TestClient + asyncpg conflict**: FastAPI TestClient cannot be used with asyncpg in pytest due to event loop
  conflicts. Affected tests marked `@pytest.mark.skip` for CI; manual testing in dev environment.
- **Async direct calls**: Instead of TestClient, tests use direct async calls to repo functions (`find_user_for_auth`,
  `get_user_by_id`, etc.) to avoid event loop conflicts.
- **Cleanup is critical**: All onboarding/invitation tests create data in Postgres and must delete it in teardown (
  transactions + RLS bypass). Register endpoint tests clean up both Postgres and MongoDB data.

## Multi-Tenant Test Strategies

All tests include explicit multi-tenant isolation verification:

1. **MongoDB-level isolation** (Phase C register tests):
    - Create user in building 13195, verify absent in 16244
    - Cleanup must check both buildings

2. **Postgres RLS isolation** (Phase B/C identity tests):
    - Query with `building_id = B1`, verify B2 rows invisible
    - Tests use `SET LOCAL app.tenant_id = '<uuid>'` inside transactions

3. **API-level isolation** (routers):
    - Seed test data for building B1
    - Query via API with building B2 context
    - Verify 404 or empty result

4. **Cleanup patterns**:
    - **MongoDB**: Use `db.collection.delete_many({"email": test_email})`
    - **Postgres in RLS table**: Must DELETE inside `async with conn.transaction(): SET LOCAL`
    - **Postgres global table**: Can DELETE without transaction
    - All tests are idempotent — cleanup safe to call multiple times (0 rows deleted is OK)

## Phase D Tests (Register ORM Helper + JWT Tenant ID)

| File                                            | What it covers                                                                                                                                                                                                                                                                                                                                                           |
|-------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `tests/backend/test_register_with_tenant_id.py` | ORM helper `create_user_for_registration()` from identity_repo. Tests: Postgres user creation, idempotency on duplicate email, password hashing, JWT includes tenant_id claim, multi-tenant isolation (user A ≠ visible in context B), cleanup idempotent (6 tests, all passing). Prerequisite: `DATABASE_URL` + Postgres at migration 0015 + `PLATFORM_TENANT_ID` seed. |

```bash
# Phase D ORM tests
backend/venv/bin/python3 -m pytest tests/backend/test_register_with_tenant_id.py -v
```

Key notes (Phase D):

- **Idempotency**: Duplicate email returns existing user_id (no error)
- **Tenant context**: Uses `SET config('app.tenant_id', :tid, false)` for RLS
- **Platform tenant ID**: Deterministic UUID `uuid5(NAMESPACE_DNS, "strataos-platform-tenant")`
- **JWT tenant_id claim**: All Phase D users get tenant_id in token

## Phase E Tests (Register Endpoint Integration — Postgres + MongoDB)

| File                                          | What it covers                                                                                                                                                                                                                                                                                                                                                      |
|-----------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `tests/backend/test_register_phase_e.py`      | Full register endpoint integration: Postgres user creation after MongoDB persistence. Tests: dual-write verification (MongoDB + Postgres), JWT includes tenant_id, idempotency on duplicate email, password hashing, multi-tenant isolation, cleanup idempotent (6 tests, all passing). Prerequisite: Same as Phase D + register endpoint accessible.               |
| `tests/backend/test_invitation_rls_bypass.py` | Migration 0015 (rls_invitation_bypass): invitation lookup by raw token works without knowing tenant_id (bypass sentinel UUID). Tests: bypass sentinel allows token lookup, wrong tenant cannot read invitation, claimed/expired invitations return None, isolation across tenants (5 tests). Requires DATABASE_URL + Postgres with 0015 applied; skipped otherwise. |

```bash
# Phase E register integration tests
backend/venv/bin/python3 -m pytest tests/backend/test_register_phase_e.py -v

# Phase E invitation RLS bypass tests (requires DATABASE_URL)
backend/venv/bin/python3 -m pytest tests/backend/test_invitation_rls_bypass.py -v

# All Phase D + Phase E tests together
backend/venv/bin/python3 -m pytest tests/backend/test_register_with_tenant_id.py tests/backend/test_register_phase_e.py tests/backend/test_invitation_rls_bypass.py -v
```

Key notes (Phase E):

- **Dual-write**: Register creates users in both MongoDB (legacy) and Postgres (primary)
- **Backward compatible**: If Postgres unavailable, registration still works (MongoDB-only)
- **Non-fatal failures**: Postgres creation errors logged but don't block registration
- **Archived users**: Unchanged; return request flow preserved entirely
- **Hybrid approach**: Preserves 7-year archive requirements while transitioning to Postgres-primary
- **Migration 0015**: Adds RLS bypass sentinel to `core.user_invitations` so pre-claim token lookups work without
  knowing tenant_id

## Combined Test Coverage (Phases C + D + E)

```bash
# All auth/register tests (C + D + E)
backend/venv/bin/python3 -m pytest \
  tests/backend/test_alembic_0013.py \
  tests/backend/test_auth_repoint.py \
  tests/backend/test_pg_identity_repo.py \
  tests/backend/test_register_endpoint.py \
  tests/backend/test_register_with_tenant_id.py \
  tests/backend/test_register_phase_e.py \
  tests/backend/test_invitation_rls_bypass.py \
  -v

# Phase C baseline (3 files, 13 tests active + 4 skipped)
# Phase D additions (1 file, 6 tests)
# Phase E additions (2 files, 11 tests; 5 require DATABASE_URL)
# Total: 30 tests active + skipped when DATABASE_URL not set
```

Test status summary:

- ✅ Phase C: 2/6 active tests passing (4 skipped TestClient conflict)
- ✅ Phase D: 6/6 tests passing
- ✅ Phase E: 6/6 register tests passing
- ✅ Phase E: 5/5 invitation RLS bypass tests passing (when DATABASE_URL set)
- ✅ Multi-tenant isolation: Verified across all 3 phases
- ✅ Cleanup idempotency: All tests safe to run repeatedly

## Integration-style backend tests

Some backend suites call a running backend and live database.

Use:

```bash
export RUN_INTEGRATION_TESTS=1
export TEST_ADMIN_EMAIL="<admin email>"
export TEST_ADMIN_PASSWORD="<admin password>"
export TEST_OWNER_EMAIL="<owner email>"
export TEST_OWNER_PASSWORD="<owner password>"
backend/venv/bin/python3 -m pytest tests/backend -q
```

Rules:

- do not hardcode new credentials in test files or docs
- prefer seeded data where possible
- if a test creates data, clean it up in teardown even on failure
- keep tests building-aware and idempotent

### Why `make test-backend` reports ~1,571 skipped

A plain `backend/venv/bin/python3 -m pytest tests/backend -q` run (verified 2026-07-06:
7306 passed, 1572 skipped, 1 xfailed before the fix below; 7307 passed, 1571 skipped, 1 xfailed
after) breaks down into eight gated/environment-dependent categories, all intentional:

| Category | Count | Gate | How to run |
|---|---|---|---|
| Opt-in integration tests (various reasons, all funnel through `pytest_collection_modifyitems` in `conftest.py` or a per-module `pytestmark`) | ~1,447 | `RUN_INTEGRATION_TESTS=1` | `RUN_INTEGRATION_TESTS=1 backend/venv/bin/python3 -m pytest tests/backend -q` against a running backend + live Mongo |
| Legacy multi-building tests (Sierra `16244` / Harbourview `18932` seed + regression coverage) requiring **both** flags | 26 | `RUN_INTEGRATION_TESTS=1` **and** `RUN_LEGACY_BUILDING_TESTS=1` | `RUN_INTEGRATION_TESTS=1 RUN_LEGACY_BUILDING_TESTS=1 backend/venv/bin/python3 -m pytest tests/backend -q` |
| Legacy multi-building tests requiring only the legacy flag (`test_multitenant_seed_buildings.py`, part of `test_building_switch_sierra.py`) | 58 | `RUN_LEGACY_BUILDING_TESTS=1` | `RUN_LEGACY_BUILDING_TESTS=1 backend/venv/bin/python3 -m pytest tests/backend -q` |
| Temporal workflow tests (`tests/backend/workflows/`) — needs the embedded time-skipping test server binary cached | 10 | `TEMPORAL_AVAILABLE=1` | `TEMPORAL_AVAILABLE=1 backend/venv/bin/python3 -m pytest tests/backend/workflows -q` (first run downloads the Temporal test-server binary; keep it cached for CI) |
| Live rate-limit tests (`test_sentinel_rate_limit_auth.py`) — exercises real rate-limit windows, needs wall-clock time to elapse | 3 | `RUN_LIVE_RATE_LIMIT_TESTS=1` | `RUN_LIVE_RATE_LIMIT_TESTS=1 backend/venv/bin/python3 -m pytest tests/backend/test_sentinel_rate_limit_auth.py -q` |
| Owner-facing live API fixtures (`test_owner_finance.py`, `test_owners_units_endpoint.py`, `test_council_rates.py`, `test_utilities.py`, `test_calendar_integration.py`) | mixed | `RUN_INTEGRATION_TESTS=1` for the integration-gated suites | start the dev backend (`uvicorn server:app --reload --port 8003`) and confirm the seeded users exist in the local identity store; these suites mint legacy-identity JWTs via `tests/backend/conftest.py::mint_token(..., legacy_identity=True)` for Mongo-primary owner/chair/tenant endpoints instead of depending on hardcoded local passwords or the Postgres-primary token path |
| `test_email_access.py::TestMailAccessResponseModel` — `from server import MailAccessResponse` fails when imported in isolation from this test module | 3 | none (soft-skip) | run the full file together with the rest of the suite (`pytest tests/backend -q`) rather than in isolation, or run `pytest tests/backend/test_email_access.py -q` on its own — if it still skips, `server.py` has a top-level import/side-effect that only resolves inside the full collection graph and is worth root-causing |
| Other one-off local-state skips (no demo scheme/chain seeded, no per-scheme toggle override present, `migration_output/` not generated yet) | 5 | varies — each checks its own precondition | seed the missing fixture, e.g. `python3 seeds/demo_customer.py` for the demo-scheme/chain cases |

**Fixed during this audit (2026-07-06):** `test_frontend_static_analysis.py::test_dashboard_layout_imports_all_icon_usages`
was checking for `frontend/src/components/layout/DashboardLayout.jsx`, but that file had been renamed to
`DashboardLayout.tsx` — the `.jsx` path never matched, so this regression guard silently skipped on every
run since the rename (not an environment precondition — a stale path). Updated the test to check `.tsx`;
it now runs and passes, which is why the current skip count is 1,571 rather than the 1,572 seen in the
original full run referenced above.

Run everything (all gates open) in one go when you need full coverage, e.g. before a release:

```bash
RUN_INTEGRATION_TESTS=1 RUN_LEGACY_BUILDING_TESTS=1 TEMPORAL_AVAILABLE=1 RUN_LIVE_RATE_LIMIT_TESTS=1 \
  backend/venv/bin/python3 -m pytest tests/backend -q
```

This still requires a running backend on port 8003, live MongoDB, live Postgres (`DATABASE_URL`), and the East Gate
test user seed — plain CI/local runs are expected to keep skipping these categories.

**A separate, non-env-var gate — `DATABASE_URL` (Postgres):** files like `test_alembic_0010/0011/0012/0013.py`,
`test_invitation_rls_bypass.py`, `test_register_with_tenant_id.py`, and `test_orm_identity_models.py` check for a
live Postgres connection at import/fixture time and skip (not fail) when `DATABASE_URL` is unset or the target
migration revision isn't applied. They are **not** part of the ~1,572 figure above in this checkout — `backend/.env`
already has `DATABASE_URL` pointing at a reachable `strataos` Postgres at head, so all of those pass here. In an
environment without Postgres configured, expect an additional ~40+ tests to skip under this gate; run
`cd backend && alembic upgrade head` and confirm `DATABASE_URL` is set before relying on that coverage.

## Live-database backend tests

These tests connect to MongoDB directly. They pass only if the database has the expected data.

| File                            | What it checks                                              | Skip condition                    |
|---------------------------------|-------------------------------------------------------------|-----------------------------------|
| `test_fy2026_march_actuals.py`  | FY2026 March actuals in `levy_categories` + `annual_levies` | DB missing expected category data |
| `test_schema_sync.py`           | Live DB schema matches expected collections/fields          | DB unavailable                    |
| `test_portal_owner_balances.py` | Portal balance snapshot (87 lots, credit/arrears totals)    | Stale snapshot data               |

These tests load connection settings from `backend/.env` (MONGO_URL, DB_NAME). Run from repo root:

```bash
backend/venv/bin/python3 -m pytest tests/backend/test_fy2026_march_actuals.py -v
```

## Multi-tenant test rules

This is a multi-tenant, multi-building platform. Every test file must respect the following rules:

- All test data must use `building_id` on every mock document — never create documents without it
- Tests must assert that building `"16244"` cannot see building `"13195"` data (isolation check)
- Role assertions must use `"admin_staff"` (not `"reception"`) — `reception` is a backward-compat alias only
- `AsyncMock` is required for any DB method called inside `asyncio.gather()` — regular `MagicMock` will not work
- Pass `building_id` explicitly when calling endpoint functions directly (not via HTTP client) —
  `Depends(get_current_building)` defaults will not resolve in unit test context

## Multi-tenant expectations

Every affected suite should respect the platform model:

- requests are scoped by `building_id`
- data from one building must not bleed into another
- tenant-scoped collections should be accessed through the wrapped database API
- feature toggles and settings should be tested per building when behavior changes

## Recommended validation order

1. run the smallest backend pytest suite touching the change
2. run `backend/venv/bin/python3 -m pytest tests/backend -q` for full unit suite
3. run the relevant frontend Jest suite if UI behavior changed
4. run `cd frontend && yarn build` for route/runtime validation
5. run broader Playwright coverage only where the change crosses browser boundaries

## P0-T02 Dual-Source Parity Contract

The dual-source parity test suite (`tests/backend/test_dual_source_consistency.py`) validates the real **`/analytics/activities` Postgres-first + Mongo-fallback** behavior. This is critical during the coexistence period where dashboard activity data can originate from either source.

### Parity guarantees

1. **Canonical field parity**: both source paths produce dashboard-safe activity items with `type`, `title`, `created_at`, `visibility`
2. **Fallback continuity**: Postgres failures cleanly fall back to Mongo without endpoint failure
3. **Visibility safety**: Mongo fallback enforces non-privileged `is_public` filters
4. **Observability**: Postgres-origin records keep `source="postgres"` for debugging
5. **No silent schema drift**: canonical output shape checks catch source divergence early

### Running parity tests

```bash
# All parity tests
backend/venv/bin/python3 -m pytest tests/backend/test_dual_source_consistency.py -v

# Single parity test
backend/venv/bin/python3 -m pytest tests/backend/test_dual_source_consistency.py::test_activities_pg_and_mongo_paths_are_canonically_equivalent -v
```

### Powerhouse PG-read fallback coverage

Powerhouse Phase 2 router readiness uses PG-first reads with Mongo fallback for empty/unavailable PG data while cutover is in progress. Targeted coverage lives in:

- `tests/backend/test_powerhouse_conversation_service_pg_fallback.py`
- `tests/backend/test_conversation_id_mapping_schema_p1t02.py`

Run it with:

```bash
backend/venv/bin/python3 -m pytest tests/backend/test_powerhouse_conversation_service_pg_fallback.py -v
backend/venv/bin/python3 -m pytest tests/backend/test_conversation_id_mapping_schema_p1t02.py -v
```

### Common failure modes

| Symptom | Root cause | Resolution |
|---------|-----------|------------|
| `AssertionError: canonical parity mismatch` | PG formatter and Mongo formatter diverged | Normalize shape in router or update parity contract intentionally |
| `AssertionError: source key missing` | PG branch stopped annotating origin metadata | Restore `source="postgres"` in PG branch response mapping |
| `AssertionError: Fallback should return ...` | PG error path short-circuited instead of fallback | Ensure exception handling continues to Mongo query |
| `AssertionError` on `is_public` filters | non-privileged fallback query leaked private documents/listings | Re-apply visibility guards in fallback query builder |

### Powerhouse Command Foundation Health (2026-07-21)

Read-only diagnostic surface for the P2A command foundation (outbox delivery,
idempotency, per-domain cutover readiness) added to the Powerhouse Control Centre.
No feature toggle — gated by the same `building.cutover.view` capability as the rest
of `cutover_admin.py`.

- `tests/backend/test_powerhouse_command_foundation_health.py` — service-level: scheme-not-found
  shell response, outbox/idempotency count mapping, domain list coverage.
- `tests/backend/test_cutover_admin_router.py::TestPowerhouseCommandFoundationHealthEndpoint` —
  router-level RBAC (super_admin can view; non-super_admin blocked from another building).
- `tests/frontend/unit/pages/powerhouse/PowerhouseControlCentrePage.test.tsx` — panel
  rendering (loading, populated, no-scheme-context states) and the category-grouping/Docs-link
  regression tests for the same page.

```bash
backend/venv/bin/python3 -m pytest tests/backend/test_powerhouse_command_foundation_health.py -v
backend/venv/bin/python3 -m pytest tests/backend/test_cutover_admin_router.py -k CommandFoundation -v
cd frontend && yarn jest --testPathPatterns=PowerhouseControlCentrePage --watchAll=false
```

### When to add a new parity test

Add a test when:
- A new route is implemented with PG-first + Mongo fallback paths
- A field is added to an existing dual-source model
- A new collection is migrated from Mongo to Postgres
- Bug fixes introduce data transformations that must remain consistent across both stores

**Do not** add a parity test for routes that only read from one store (e.g., Postgres-only analytics, Mongo-only maintenance).

---

## BI Analytics (Canonical Star Schema — Phase 2–6)

Tests for the canonical BI analytics platform: star schema migration (0052), ETL service, 27-endpoint `/bi/*` router, and rebuilt BIAnalyticsPage.

### Test files

| File | Coverage | Count |
|------|----------|-------|
| `tests/backend/test_bi_service.py` | `bi_service.py` helper functions, toggle routing, Mongo/PG path switching, alert evaluators, multi-tenant isolation | 28 |
| `tests/backend/test_bi_router.py` | Route handler signatures, role guards (`_require_manager`, `_require_admin`), owner lot access control, envelope shape, portfolio non-manager 403, `building_alerts` gather error swallowing | 32 |

### Run

```bash
backend/venv/bin/python3 -m pytest tests/backend/test_bi_service.py tests/backend/test_bi_router.py -v
```

### Prerequisites

No running server required. All PG sessions and Mongo collections are mocked. The tests use `patch.object(bi_service, ...)` targeting private helpers (`_toggle_on`, `_financial_summary_pg`, `_financial_summary_mongo`, etc.) and `patch("services.bi_service.<func>")` for lazy-imported service functions called from the router.

### Feature toggle behaviour

- `bi_analytics_enabled = FALSE` (default): all `/bi/*` endpoints fall back to MongoDB-backed analytics service. Tests assert `source == "mongo_fallback"`.
- `bi_analytics_enabled = TRUE`: endpoints read from `analytics.fact_*` tables in PostgreSQL. Tests assert `source == "postgres"`.

### ETL trigger

Admin-only: `POST /api/bi/admin/etl/run?building_id=<bid>` (super_admin only). Calls `bi_etl_service.run_nightly_etl(building_id)` synchronously. Normally scheduled nightly at 02:00 AEST by `bi_etl_service.run_nightly_etl_all_buildings()`.

### Common failure modes

| Symptom | Root cause | Resolution |
|---------|-----------|------------|
| `AttributeError: module 'routers.bi' has no attribute 'bi_service'` | Patching `routers.bi.bi_service.*` — router uses lazy imports | Patch at source: `services.bi_service.<func>` |
| `TypeError: unexpected keyword argument 'fy'` | Router param is `financial_year` not `fy` | Use `financial_year=` in test calls |
| `TypeError: unexpected keyword argument 'min_days'` | Router param is `min_days_overdue` | Use `min_days_overdue=` in test calls |
| Alert test fails `"rule_key" not in result` | Alert evaluators use `rule_code` not `rule_key` | Check `rule_code` field |
| `_pct(90, 100) != 0.9` | `_pct` returns 0–100 scale, not 0–1 ratio | Assert `== 90.0`, not `== 0.9` |

---

## Management Hierarchy Security Hardening

**Sprint:** Hierarchy Normalisation + PG Readiness (2026-06-09)

### Backend — `tests/backend/test_management_hierarchy.py` (70 tests)

| Class | What it tests | Count |
|-------|---------------|-------|
| `TestUUIDValidation` | Malformed, empty, SQL-injection, partial UUIDs all return 400 | 5 |
| `TestFieldFiltering` | Full-access returns ABN/email/phone/legal_name; restricted strips them | 3 |
| `TestAccessHelperSuperAdmin` | super_admin gets platform scope on scheme, entity, write | 3 |
| `TestAccessHelperECMemberOwnScheme` | Own scheme/entity allowed; foreign UUIDs → IDOR 403 | 6 |
| `TestAccessHelperAgencyAdmin` | Own-agency entity allowed; foreign agency entity → 403 | 2 |
| `TestAccessHelperIndependentManager` | Assigned scheme allowed; unassigned/foreign → 403 | 4 |
| `TestAccessHelperOwnerTenant` | owner/tenant/guest/real_estate_agent all → 403 | 4 |
| `TestAccessHelperAuditLogging` | Allow + deny events emitted to core.outbox | 3 |
| `TestAccessHelperSelfManagedOC` | EC reads own scheme; cannot access agency portfolio or foreign OC | 3 |
| `TestAccessHelperManagerSelfQuery` | Self-access allowed; strata_manager cannot query other manager | 3 |
| `TestManagementHierarchyRouter` | Router-level: owner reject, valid UUID, malformed UUID, appointment type | 7 |
| Other (legacy) | Ensure idempotency, model/assignment creation, entity types, mode switch | 27 |

### Backend — `tests/backend/test_bi_phase2.py` (32 tests)

Covers BI toggle Phase 2: 5-tier hierarchy resolution, legacy alias acceptance (tier 4 uses
`_resolve_global_toggle` not `_resolve_building_toggle_with_source` for ec_member path),
agency/manager/platform scopes, `BIResolvedAccess` model, UUID validation in BI access helpers.

### Frontend — `tests/frontend/unit/components/management/ManagementModelSelector.test.tsx` (24 tests)

RTL tests for the `ManagementModelSelector` and `ManagementModelCard` components:
all four mode options render, contextual fields per mode (Agency ID for agency_managed,
entity name for self_managed/independent_manager), save button state, POST API call verification
including `source_agency_id`, success/error/loading states, `onSaved` callback, and card wrapper.

### Prerequisites

No running server required. All PG sessions mocked via `AsyncMock`. `core.outbox` emit
calls mocked in `TestAccessHelperAuditLogging`.

### Key invariants

- `parse_uuid_or_400()` must be called before any DB access for all UUID parameters.
- EC members can only access the scheme matching their own `building_id` — cross-scheme
  UUID attempts always return 403 regardless of other conditions.
- `filter_entity_fields(entity, full_access=False)` strips `abn`, `email`, `phone`,
  `legal_name` — verify these fields are absent in scheme/building-scope responses.
- Audit events are non-fatal — test stubs should verify `core.outbox` insert was called
  with the correct `event_type` key.

### Performance test

`tests/performance/management_hierarchy_benchmark.ts` — smoke + load scenarios against
the 4 most-called management hierarchy endpoints. Asserts:
- `p(95) < 500ms` for reads, `p(95) < 1000ms` for ensure
- Malformed UUID path returns 400 (not 500) under load

### Backend — `tests/backend/test_ownerhub_tco_service.py` (11 tests)

Unit tests for `services/ownerhub_service.compute_unit_tco`/`compute_tco`. Covers quarterly levy
aggregation, council-rates financial-year matching, water-bill calendar-year matching, 5-year
amortised capital-replacement formula, 10-year projection structure, and (added 2026-07-02) the
committed-annual-rate regression fix: `strata_levies` must come from `get_levy_rates()` (matching
the main Dashboard) rather than a possibly-partial `unit_levy_ledger` YTD row, and
`capital_replacement` must be excluded from `total_costs` (informational only, not double-counted
against the sinking levy already inside `strata_levies`). `_mock_db_for_tco()` defaults
`annual_levies`/`settings`/`buildings` lookups to `None` so the pre-existing ledger-path tests keep
exercising the fallback branch unchanged.

### Backend — `tests/backend/test_owner_finance_service.py` (3 tests, added 2026-07-02)

Regression coverage for `services/owner_finance_service.get_levy_breakdown()` — found during the
2026-07-02 cross-page financial audit (see `docs/architecture/financial_data_flow_live_map.md` §0.5).
The `unit_levy_ledger` lookup behind `/financials/my-finances`'s "Where does my money go" card sorted
by year string descending with no upper bound, so a still-forming next-FY ledger row could be
selected over the current year's row, under-reporting `quarterly_levy` by using a partial future-year
total. Tests assert the query is constrained to `year <= current calendar year`, that the resulting
`quarterly_levy × 4` matches the canonical annual levy figure, and that the fallback to the most
recent past year (never a future one) works when no current-year row exists yet.

### Backend — GAP-FIN-016 deep-dive audit correction: Item C's levies_paid_pct test fixture strengthened (2026-07-21, later same session)

See `tasks/GAP-FIN-016-financial-calculation-consolidation-phase2.md` ("Item C — Audit correction")
for the full account of what was wrong and why. Summary for this file's purposes:
`test_dashboard_pg_first.py::test_building_overview_uses_postgres_ledger_contract_without_mongo`'s
original fixture had `total_outstanding` (250000 cents) coincidentally equal to
`total_levied - total_paid` (600000+400000 - 450000-300000 = 250000) — so `total_paid/total_levied`
and `(total_levied-total_outstanding)/total_levied` produced the identical number (75.0) and the
test could not have caught a formula regression either way. Changed the third arrears row from
50000 to 100000 cents (`total_outstanding` now 300000) so the two formulas diverge (75.0 vs 70.0) —
this is what actually caught, on re-audit, that the PG branch's `levies_paid_pct` should stay
`paid/levied` rather than match Mongo's formula. `test_building_fund_overview_parity.py`'s
Mongo-branch test was unaffected (that branch's formula was never changed).

### Backend — GAP-FIN-016 Phase 2b Items C, A, B1: fund_health parity fix, arrears split, per-fund rate consolidation (2026-07-21, continued same session)

See `tasks/GAP-FIN-016-financial-calculation-consolidation-phase2.md` ("Item C" / "Item A" / "Item
B") for the full account, including the same-day audit correction above — the description below is
the corrected, final state, not the first pass. No live DB required — all DB access is mocked;
live-verification against real East Gate data was a one-off manual check during development, not
part of the automated suite.

- `tests/backend/test_domain_finance_formulas.py` — extended: `TestCentsToPercentage` gains 3 tests
  for the new `digits` param; `TestCurrentYearCollectionRate` gains a live-reproducing test for
  `digits=1` (fund_health's precision).
- `tests/backend/test_domain_finance_arrears.py` (new, 8 tests) — pure formula tests for
  `quarter_true_arrears()`/`recoverable_arrears()`: excess-over-quarter-levy, within-quarter-levy
  (must be 0), zero-input cases, a live-East-Gate-shaped case, and a signature-lock test guarding
  against a 3rd parameter ever being added to `recoverable_arrears()` (the exact shape of the
  documented UA042 incident this split exists to prevent).
- `tests/backend/test_building_fund_overview_parity.py` (new, 3 tests) — `_get_building_overview_mongo_fallback`'s
  refactored formula: reproduces the live East Gate 2026 value exactly (fund_health, levies_paid_pct,
  admin/sinking collection_rate all asserted), a floor-clamp edge case (outstanding > obligations
  must not go negative), and the zero-levied case. This branch's formulas were never changed by
  Item C, only refactored onto shared primitives — no audit correction needed here.
- `tests/backend/test_dashboard_pg_first.py` — the existing PG-contract test extended with
  `total_opening_arrears`/`total_obligations`/`fund_health`/`levies_paid_pct`/`admin_fund.collection_rate`/
  `sinking_fund.collection_rate` assertions, computed from the same mocked inputs — this is the
  actual Item C regression guard, using a fixture deliberately chosen (see the correction entry
  above) so `fund_health` and `levies_paid_pct` cannot coincidentally agree. It also proves the PG
  branch still never reads Mongo (the pre-existing `unit_levy_ledger.aggregate.assert_not_called()`
  in the same test, which the first, reverted Item C attempt broke).

### Backend — GAP-FIN-016 Phase 2b part 1: quarterly-rate consolidation + external_api.py field-name bug fix (2026-07-21, later session)

See `tasks/GAP-FIN-016-financial-calculation-consolidation-phase2.md` ("Phase 2b, part 1") for the
full account, including the real live-schema bug this session found and fixed in
`backend/routers/external_api.py`. No live DB required to run these tests — all DB access is
mocked; the live-verification against real East Gate data described in the GAP doc was a one-off
manual check during development, not part of the automated suite.

- `tests/backend/test_domain_finance_formulas.py` — 8 new tests: `TestRawPercentage` (4 — zero/
  negative denominator, not-clamped-above-100, digits precision) and `TestQuarterlyCollectionRate`
  (4 — reproduces the live East Gate trust_phase1.py value byte-for-byte, the `bi_service.py` vs
  `trust_phase1.py`/`external_api.py` zero-denominator return-value difference `None` vs `0.0`
  preserved distinctly, not-clamped-above-100, 2dp precision).
- `tests/backend/test_external_api_building_scoping.py` — rewritten. Mocks now use the real
  `annual_levies`/`unit_levy_ledger` schema shape (`year` field, nested `admin_fund`/`sinking_fund`,
  `total_opening`/`total_levied`/`total_paid`/`total_closing`) instead of the wrong flat shape the
  buggy code (and the old mocks, matching it) used — the old mocks were themselves masking the bug
  by echoing its wrong assumptions. Added computed-value regression assertions (budgeted/collected/
  collection_rate_pct/units_in_arrears/levied_amount/paid_amount/closing_balance), not just
  building_id-scoping assertions, so a future field-name regression fails a value check even if the
  filter shape stays technically correct. `_get_annual_levy`'s test rewritten to assert it delegates
  to `get_latest_levy_year()`/`get_levy_fund_data()` rather than querying `db.annual_levies` itself.
- `tests/backend/test_external_api_finance_cutover.py` — the one Mongo-fallback test's mock `levy_doc`
  updated from the wrong flat shape to the real nested shape (this test patches `_get_annual_levy`
  directly, so it must return what the real, now-fixed function actually returns).

### Backend — GAP-FIN-016 Phase 2a calculation-duplication cleanup (2026-07-21)

See `tasks/GAP-FIN-016-financial-calculation-consolidation-phase2.md` for the full scoping (including
what was deliberately NOT merged and why). No live DB required — all DB access is mocked.

- `tests/backend/test_finance_helpers_consolidation.py` (8 tests) — unit tests for the 3 new/changed
  `backend/utils/finance_helpers.py` functions. `TestLedgerStatsExcludesTestData` (1 test) is a
  regression guard asserting `get_unit_ledger_stats()`'s aggregation `$match` stage excludes
  `is_test_data=True` rows (a real gap found this session — this was the only ledger-stats pipeline
  in the codebase without that mandatory filter). `TestGetLevyFundData` (3 tests) covers the
  new-schema-first/legacy-fallback/not-found paths of `get_levy_fund_data()`, the function that
  replaced 4 byte-identical private `_get_levy_data()` copies. `TestComputeCombinedFundTotals`
  (4 tests) covers `compute_combined_fund_totals()` — normal sum, `None` levy doc, a levy doc with
  only one fund populated, and explicit `None` field values (not just missing keys) all defaulting
  to zero rather than raising.
- Existing suites re-run clean after the refactor (no behaviour change intended, verified):
  `test_collection_rate.py`, `test_building_kpis_fix.py`, `test_finance_kpi_contract.py`,
  `test_financial_phase_p1_p2.py`, `test_report_service.py`, `test_finance_phase4.py`,
  `test_no_floats_in_domain.py` (the `metric_registry.py` formula-text update initially tripped this
  regex-based float-literal scanner on a `$0.01` substring inside a docstring string — reworded to
  avoid the false positive, not a real float).

### Backend — GAP-FIN-014 ledger source-of-truth reconciliation (2026-07-04)

See `docs/architecture/finance_ledger_source_of_truth_audit_2026-07-04.md` §9 for the full implementation
summary. No live DB required — all DB access is mocked (`AsyncMock`/`MagicMock`).

- `tests/backend/test_ledger_quality.py` (7 tests) — unit tests for `utils.finance_helpers.get_ledger_quality()`.
  Covers: a duplicate ledger row (case/whitespace-variant token) not inflating the canonical unit count; a
  missing ledger row for a canonical unit; an "extra" ledger row referencing a unit absent from `units`
  (excluded from the canonical count); a fully consistent 87-unit East Gate fixture; and
  `canonical_status_counts` classification by `net_balance`. Extended post-Copilot-review (PR #484) with:
  `duplicate_ledger_row_count` counting redundant rows (not duplicated units — a unit duplicated 3x reports
  `duplicate_ledger_units == 1` but `duplicate_ledger_row_count == 2`); and a malformed ledger row (null/missing
  `unit_number`) being skipped and counted under `malformed_ledger_row_count` rather than propagating `None`
  into `extra_ledger_units`, which would previously raise `TypeError` in `_build_ledger_quality_warnings()`'s
  `", ".join(...)`. The canonical `units` and `unit_levy_ledger` roster fetches now also use `to_list(None)`
  instead of a hardcoded `to_list(500)`/`to_list(1000)` cap, which silently undercounted for buildings
  exceeding those caps.
- `tests/backend/test_finance_kpi_contract.py` (6 tests) — unit tests for `GET /finance/kpi-contract`.
  Covers: `unit_counts.paid_up + owing + credit == canonical_unit_count`; a portal/ledger arrears mismatch
  setting `requires_reconciliation=True` without altering the ledger-derived arrears figure; no portal
  document present; and the East Gate 87-unit sanity check. Extended post-Copilot-review (PR #484) with:
  a `can_view_finances=False` RBAC test asserting `HTTPException(403)`; and a multi-tenant isolation test
  asserting `get_ledger_quality()`'s `units`/`unit_levy_ledger` queries are filtered by the caller's
  `building_id` (not a different building's, and not unscoped).
- `tests/backend/test_finance_summary_annual_levy_proposed.py` (extended, +2 tests) — `annual_levy_total`
  now asserted to use the proposed annual budget, not the YTD `admin_fund/sinking_fund.total_income` sum;
  and `/finance/summary` response now asserted to include `ledger_quality` with the correct
  `canonical_unit_count`. The shared `_make_mock_db`/`_call` helpers now also patch `utils.finance_helpers.db`
  (previously only `routers.finance.db` was patched — `get_arrears_metrics`/`get_ledger_quality` hold their
  own `db` reference from `utils.finance_helpers`, so leaving it unpatched silently hit the real configured
  MongoDB instance for the current-year branch).

### Frontend — GAP-FIN-014 ledger source-of-truth reconciliation (2026-07-04)

- `tests/frontend/unit/pages/dashboard/FinancePage.test.tsx` (extended, +2 tests) — unit denominator
  renders "of 87 units" (not "of 156") when `ledger_quality` reports duplicate ledger rows; ledger-quality
  warning banner renders on inconsistency.
- `tests/frontend/unit/pages/dashboard/CollectionRatePage.test.jsx` (4 tests, new — no prior dedicated
  test file existed) — building-wide collection rate/status distribution come from the mocked
  `/finance/kpi-contract` response, not a local recompute from `/finance/summary`; portal/scraper data
  renders as an explicit cross-check; a portal/ledger arrears mismatch renders a reconciliation warning.
- `tests/frontend/unit/pages/dashboard/UnitFinanceDetailPage.test.jsx` (2 tests, new — no prior test
  file existed) — operational balances render from `/levy-status` ledger fields only; a missing
  selected-year ledger row renders a data-quality warning instead of the stale `/owners-units` fallback.

### Frontend — `tests/frontend/unit/contexts/AuthContext.selectedYear.test.tsx` (2 tests, added 2026-07-02)

Regression coverage for `AuthContext`'s `selectedYear` default — the root cause of most of the
2026-07-02 cross-page levy mismatch (see `docs/architecture/financial_data_flow_live_map.md` §0.2).
`GET /years` returns years sorted newest-first by string, which can put a still-forming next-FY
`annual_levies` document ahead of the real current year. Tests render `AuthProvider` directly (same
pattern as `AuthContext.switchBuilding.test.tsx`) with a pinned `Date.prototype.getFullYear` and
assert `selectedYear` resolves to the current calendar year when present in the list, falling back to
`years[0]` only when it isn't.

### Prerequisites for the three suites above

No running server required — all DB access is mocked (`AsyncMock`/`MagicMock`) or exercised via
`AuthProvider` with a mocked axios instance. Regression figures in `test_ownerhub_tco_service.py` and
`test_owner_finance_service.py` mirror real production data (unit `TH087`, building `13195`, FY2026,
entitlement 161, $7,090.04 annual / $1,772.51 quarterly) captured during the live-DB audit, not
invented numbers.

## Trust Accounting Phase 2 — reconciliation fixes and Postgres correction (2026-07-02)

Fixes for two bugs found in the Phase 2 field-level trust-accounting audit
(`docs/architecture/financial_field_level_traceability.md` §6–§9): the reconciliation-run close
endpoint was unreachable (missing route decorator), and the matcher scored double-entry
`trust_ledger_entries` rows (from `POST /trust/journal`) as a permanent $0 amount. Also covers the
one-off Postgres data correction for building `13195`'s stale `finance.trust_accounts` metadata.

- `tests/backend/test_trust_reconciliation.py` — `TestCloseRunRouteRegistered` (route now resolves
  to `close_reconciliation_run`) and `TestNormalizeInternalTxAmount` (double-entry rows are
  normalized to a signed `amount_cents` derived from the line touching the reconciliation run's
  bank account; proven end-to-end against the real `MatchingEngine` — a previously-invisible match
  now scores ≥`THRESHOLD_LIKELY`). No live DB; all mocked.
- `tests/backend/test_fix_13195_trust_account_metadata.py` (11 tests, new) — unit tests for
  `scripts/fix_13195_trust_account_metadata.py`'s `_compute_account_diff()` (extracted as a pure
  function specifically so this could be tested without a live DB). Covers: both/one/no fields
  changed, falsy or missing Mongo values are never candidates, `masked_bsb`/`masked_account_number`
  are never touched even if present on the Mongo doc (PII-masking regression guard), and
  `FUND_KEY_MAP` normalizes both `sinking_fund` and the legacy `capital_works_fund` label to the
  same Postgres `fund_type`. Importing the module does not touch Mongo/Postgres — `main()` (the only
  function that does) is never called by this test file.
- Frontend: `tests/frontend/unit/pages/dashboard/TrustReconciliationPage.test.jsx` — `match
  suggestions` and `close run` describe blocks cover the new `/suggestions`-driven Match button and
  the Close Run button end to end (mocked axios).

No `is_test_data` cleanup required — all mocked, no live DB access.

## Owner Transfer Drift Detection (2026-07-02)

Tests for `services/ownership_transfer_detection_service.py` (detects owner-name drift between an
imported/scraped owner snapshot and the portal's current owner records, and creates a reviewable
`owner_transfer_requests` row), its `strata_sync._upsert_owners` call site, and the
`OwnerTransfersPage` review UI. Also documents a `chairman`-role regression found and fixed during
this pass (see below) — all mocked, no live DB.

- `tests/backend/test_owner_transfer_detection_service.py` (15 tests) — drift detection creates a
  pending transfer; single-primary-owner import replaces the full existing owner set (not just the
  primary); no-op when imported names already match; dedup against an existing pending request;
  dry-run makes zero writes; `previous_owner_names` override recovers a baseline that's already been
  overwritten; internal-contact-owner reuse is idempotent; repair-script (
  `scripts/data_repair/create_owner_transfer_requests_from_imported_owner_drift.py`) dry-run/apply
  paths. Added this pass: `test_active_owner_lookup_and_dedup_check_are_scoped_to_building_id`
  (multi-tenant isolation — both the owner lookup and the pending-request dedup filter must include
  `building_id`, not just `unit_number`), `test_previous_owner_override_still_dedups_against_existing_pending_request`
  (edge case: the override skips the owner-baseline lookup but must NOT skip the dedup check),
  `test_reviewer_roles_never_contains_literal_chairman_string` /
  `test_ec_member_chairman_is_notified_via_ec_member_role_alone` (regression pair — see below).
- `tests/backend/test_strata_sync_owner_transfer_detection.py` (4 tests, new) — covers the
  `_upsert_owners` → `detect_and_create_portal_owner_transfer` call site added to
  `routers/strata_sync.py`: correct building-scoped args are passed, a detector exception is caught
  and logged (never breaks the sync ingest loop — `strata_owners.update_one` still runs), the loop
  continues processing later rows after one row's detection fails, and `building_id` is never
  hardcoded.
- `tests/backend/test_bolt_owner_transfers.py` — added
  `test_ec_approver_roles_never_contains_literal_chairman_string` (regression — see below).
- `tests/frontend/unit/pages/dashboard/admin/OwnerTransfersPage.test.jsx` (5 tests) — imported
  owner drift renders without exposing generated internal-contact emails, pre-selects the suggested
  owner-to-remove, a real chairman (`role: 'ec_member'`, `ec_position: 'CHAIRMAN'`) can review, and a
  regression guard proving an impossible `role: 'chairman'` user gets no reviewer actions when the
  backend's own `can_review` flag is false (forcing the frontend's role-array fallback path to run).

### `chairman`-literal regression (found and fixed this pass)

Three call sites introduced during this feature's development held the literal string
`"chairman"` in a role-membership list, violating the standing invariant that `chairman` is never a
top-level `user.role` value (a chairman is `role='ec_member'` + `ec_position='CHAIRMAN'` — see
`rules/post-compact-critical.md`). Because `'ec_member'` was already present in every list, none of
these were user-visible bugs (real chairmen were still covered), but each was dead, misleading code
that could regress trust in the invariant. Fixed in the same commit as their regression tests:

- `backend/server.py::OWNER_TRANSFER_EC_APPROVER_ROLES`
- `backend/services/ownership_transfer_detection_service.py::OWNER_TRANSFER_REVIEWER_ROLES`
- `frontend/src/pages/dashboard/admin/OwnerTransfersPage.jsx::REVIEWER_ROLES` / `EC_ROLES`

## Trust Bank Accounts Page (2026-07-02)

`tests/frontend/unit/pages/dashboard/TrustBankAccountsPage.test.tsx` (9 tests, new) — first test
coverage for `TrustBankAccountsPage.tsx` (per-building bank account settings, interest posting,
advance-payment interest summary, interest history). Mocks `next-auth/react`'s `useSession` and
`axios` directly (this page calls `axios` directly rather than through `AuthContext`'s `api`
instance). Covers: loading state, computed summary totals, role-gated Edit/Post-Interest actions,
History tab lazy-fetch, and a regression test for the `unpostedAdvanceInterest` fix — the page must
display the backend's own `unposted_interest_cents` total (`trust_phase1.py::list_advance_payments`)
rather than a client-side `.filter().reduce()` over the current (paginated) page of advance-payment
items, which undercounts once results span more than one page.

No `is_test_data` cleanup required for any of the three sections above — all fully mocked
(`AsyncMock`/`MagicMock` on the backend, mocked `axios`/`next-auth` on the frontend), no live
database access.

## Codebase-wide 'chairman' role-literal sweep (2026-07-02)

Follow-up to the owner-transfer `chairman`-literal fixes above: a full sweep for the same
anti-pattern (the literal string `"chairman"` used as if it were a `user.role` value — it
never is; a chairman is `role='ec_member'` + `ec_position='CHAIRMAN'`, see
`rules/post-compact-critical.md`) across the rest of the codebase. Two classes of bug found:

- **Dead-weight entries** — `"chairman"` alongside `"ec_member"` in the same role list. Harmless
  (real chairmen already matched via `ec_member`) but misleading; removed for clarity in
  `server.py` (3 sites + 1 stale comment), `capability_registry.py`, `investor_intelligence.py`,
  `notifications.py`, `feature_toggles.py` seed (4 sites), `dashboard/page.tsx`, and
  `DashboardLayout.tsx`'s Conversation Centre nav entry.
- **Real functional bugs** — `"chairman"` present *without* `"ec_member"`, meaning real chairmen
  were silently excluded:
  - `cron/cron_approval_escalation.py` — owner-approval escalation emails never reached real
    chairmen (only `super_admin`/`strata_admin`). Covered by
    `tests/backend/test_cron_approval_escalation.py` (mocks `AsyncMongoClient`, asserts the
    admin-recipient query's `role.$in` list).
  - `routers/letters.py` — `_LETTER_ROLES` was missing `ec_member` entirely, based on a stale
    comment claiming `normalize_user_role()` maps `"chairman"` → `"strata_admin"` (it doesn't —
    see `models/user.py`). EC members/chairmen could not generate or send letters. Covered by
    `tests/backend/test_letters.py::test_ec_member_chairman_can_generate`.
  - `seeds/demo_customer.py` — the demo chairman portal user (`chair@stratademo.au`) was written
    to **MongoDB** (the live operational store) with the raw, un-normalized `role='chairman'`;
    only the Postgres write path normalized it. The Mongo-side chairman account was effectively
    broken for every `_effective_role()`-gated feature. Fixed by extracting
    `_normalize_pg_user_role()`/`_ec_position_for_raw_role()` as shared helpers used by both
    write paths. Covered by `tests/backend/test_demo_customer_seed_roles.py` (8 tests).
  - `frontend/src/lib/powerhouseFeatureCatalogue.ts` — the `powerhouse_control_centre` feature's
    `allowedRoles` (a live gate via `roleCanSeePowerhouseFeature()`, not just display data) and
    `INTERNAL_POWERHOUSE_ROLES` were missing `ec_member`. Combined with
    `PowerhouseControlCentrePage.tsx`'s page-level gate (`role === "chairman"` — dead literal,
    removed; real access already came from `isManager()`), chairmen previously could not see the
    Powerhouse Control Centre feature entry. Covered by
    `PowerhouseControlCentrePage.test.tsx`'s two new tests (realistic `ec_member`+`ec_position`
    access, and a regression guard that an impossible `role: 'chairman'` user is denied).
  - `frontend/src/components/layout/DashboardLayout.tsx` — the "Powerhouse" sidebar nav item's
    visibility list was missing `ec_member`, so real chairmen couldn't see the nav link to a page
    they could otherwise access directly by URL. Fixed; not independently unit tested (the nav
    item array isn't exposed through this file's existing, heavily-mocked test suite without a
    disproportionate mocking effort) — covered only by the full `DashboardLayout.test.tsx` suite
    passing with no regressions.
  - `tests/backend/conftest.py` — the shared `_TEST_USERS["anthony@eastgateresidences.com.au"]`
    integration-test fixture (a real East Gate chairman) minted JWTs with `role: 'chairman'`.
    Currently unused by any test (dormant, not actively causing failures), but fixed to
    `role: 'ec_member'` to prevent a future integration test from getting an unrealistic token.

**Deliberately left unfixed** (found, not touched):
- `services/authorization_engine.py`, `models/rbac_models.py` — `"chairman"` here is a **graph
  relation tag**, not a `UserRole` value (explicitly documented as legitimate in CLAUDE.md's
  Role Guard Rules — do not "fix" this).
- `routers/chat.py`'s `who_can_delete` check — `"chairman"` is a **policy-tier label** in seeded
  `chat_groups.settings.who_can_delete` config (`['admin', 'chairman', 'strata_admin']`, never
  paired with `'ec_member'`), cross-referenced against the real user's role in a separate clause.
  An earlier attempt in this same pass to "simplify" this to `ec_member`-only was caught and
  reverted before commit — it would have revoked real chairmen's message-delete permission.
  Documented inline in `chat.py` to prevent re-attempting the same mistake.
- `backend/routers/auth.py` and `backend/services/identity_bootstrap_service.py` — both contain
  the same dead-literal pattern but were excluded from this pass because they were being actively
  edited by a concurrent process at the time; revisit in a follow-up pass.
- `seeds/chat_groups.py`, `seeds/initialize_defaults.py`, `seeds/multi_user.py`,
  `seeds/invoice_feature_toggle.py` — confirmed orphaned (no import references anywhere in the
  active codebase; not wired into `seed_all.py` or any router). Same bug pattern present
  (including a missing-`ec_member` "Service Requests" auto-join group in
  `initialize_defaults.py`), but zero live impact — left as-is rather than risk editing dead code
  under time pressure.
- `seeds/snapshot_chat_groups.py`, `seeds/snapshot_feature_toggles.py` — auto-generated snapshots
  explicitly marked `DO NOT EDIT MANUALLY` (regenerated from live DB via
  `scripts/db/snapshot_all.py`); editing them by hand would be overwritten on next regeneration
  and violates their own documented contract.
- `frontend/src/lib/mindmap/interactiveMindmapData.ts` — `"chairman"` appears inside static
  documentation/mindmap content (mixed with DB collection names, not consumed by any role check).
- `backend/scripts/migrations/migration_016_chairman_to_building_admin.py`,
  `backend/scripts/pre_migration_audit.py`, `backend/scripts/fix_chairman_role_literals.py`,
  `backend/scripts/fix_chairman_role_live.py` — historical/retired one-off migration and audit
  tooling (per CLAUDE.md, the numbered `scripts/migrations/` tree is retired and not run).

Verification: full backend suite (`pytest tests/backend -q`) — 7108 passed, same 19 pre-existing
failures confirmed unrelated (verified via `git stash` against unmodified `main` before this pass
began). Full frontend suite (`yarn test --watchAll=false`) — 821/821 passed.

## Financial browser/GUI audit — Cluster A (2026-07-02)

`tests/frontend/financial_gui_audit.spec.js` (new) — a **real Playwright browser run** (headless
Chromium, not a mocked `page.route()` fixture) against the running local dev servers, closing a
gap `docs/architecture/financial_browser_verification.md` documents in full: every prior
"live verification" in this project's financial audit trail meant calling backend logic
directly, never rendering the actual page. 6 tests, one per Cluster A page (Dashboard, My
Finances, TCO, Finance, Arrears, Trust Bank Accounts) — each fetches ground truth via a raw API
request inside the same test (so it can't go stale as demo data changes) and asserts the
rendered DOM matches it exactly.

**Run:**
```bash
BASE_URL=http://localhost:3020 BACKEND_URL=http://localhost:8003 \
  npx playwright test tests/frontend/financial_gui_audit.spec.js --project=chromium
```
Requires the local dev backend (:8003) and frontend (:3020) running, and the Acme demo seed
(`backend/seeds/demo_customer.py`) already applied. **Refuses to run unless `BASE_URL` is exactly
`http://localhost:3020`, and unless `BACKEND_URL` is a localhost http URL** — hard-coded safety
checks in `test.beforeAll`. Uses
the demo tenant only (`manager@acmestrata.demo` / `james.mitchell@acmedemo.au`, password
`DemoUser$01` — the seed's own comment marks this "intentionally public"). Read-only; no writes,
no `is_test_data` cleanup needed. Login-audit cleanup is handled by Playwright's global teardown
(`tests/frontend/global-teardown.js`), not by per-spec `beforeAll`/`afterAll` hooks.

This run found and fixed two real bugs (both covered by dedicated regression tests, not just the
E2E spec — see below): the demo tenant's `db.buildings` document had no `id` field (every
consumer queries by `id`, not the `building_id` field the seed used to write), and
`db.memberships` was never written for any of the seed's 17 users at all — together these made
every owner/EC-member login to this tenant fail 403 on every request, previously undetected
because every prior audit pass used a manager/PG-token account, never an owner login.

- `tests/backend/test_demo_customer_seed_roles.py` — `TestBuildingsDocumentWriteShape` and
  `TestMembershipsWriteRegression` (2 new test classes, 4 tests) assert the exact `$set` payload
  `layer_1_structural()`/`layer_5_toggles_and_users()` write, fully mocked (mocked
  `async_session_context` + mocked Mongo `db`, no live DB access).
- `tests/frontend/unit/pages/dashboardhub/TrueOwnershipCostPage.test.jsx` (new, 3
  tests) — covers the second bug the same audit run surfaced: an auth-loading-guard race in
  `TrueOwnershipCostPage.jsx` (the exact footgun documented in
  `rules/post-compact-critical.md` — no `if (loading) return` before evaluating `canAccess`),
  found because the Playwright run was flaky (passed once, failed once with an unexpected
  redirect) before being root-caused and fixed.

See `docs/architecture/financial_browser_verification.md` §3 for the full 30-page inventory and
§4/§5 for the complete real run results — Clusters B–E remain `not started`, explicitly scoped
as backlog for future sessions.

## Date-aware accounting-period resolution + historical reconstruction (GAP-FIN-018, 2026-07-24)

Fixes `get_current_accounting_period()` always picking the latest `status='open'`
`finance.accounting_periods` row regardless of a transaction's own date (confirmed live:
2,610 pre-existing East Gate journal entries dated 2020–2025 were all filed into the "2026"
period as a result — reclassifying those is a separate, out-of-scope, evidence-backed
correction task). Adds a privileged, dual-control-authorised historical posting pathway for
closed-period reconstruction, never reachable from any HTTP route.

- `tests/backend/test_financial_core_service.py` — extended `MockLedgerRepository` with
  date-aware `get_accounting_period_for_date`/`claim_bank_transaction_for_batch`. New test
  classes: `TestGetAccountingPeriodForDate`, `TestHandlerDateAwareResolution` (one regression
  test per journal-producing handler — `create_levy`, `record_payment`, `post_genesis_journal`,
  `reverse_entry`, `record_expense`, `charge_lot_fee`), `TestHistoricalPostingAuthorisation`
  (dual-control invariant, audit-event semantics — `ledger.closed_period_override` fires only
  when the resolved period was actually closed, not merely when authorisation was supplied),
  `TestBankTransactionLinkage` (atomic claim/conflict semantics),
  `TestHistoricalPathwayIsolation` (AST/grep scan asserting no router or `adapter.py` file
  references the historical command types — the structural HTTP-unreachability guarantee).
  All in-memory, no DB required.
- `tests/backend/test_accounting_period_migration_0073.py` (new) — integration tests against a
  real Postgres instance for migration `0073`'s (`status` CHECK, `EXCLUDE` constraint, unique
  label) and `0074`'s (FKs, RLS, immutability triggers, dual-control CHECK) constraints. Opt-in
  via `RUN_INTEGRATION_TESTS=1` (same convention as the Mongo integration tests above). Uses one
  outer transaction + `SAVEPOINT`s per test (`session.begin_nested()`) so `SET LOCAL
  app.tenant_id` context survives constraint-violation assertions — an earlier draft that used
  `commit()`/`rollback()` between assertions silently lost tenant context under `FORCE ROW LEVEL
  SECURITY`, making every subsequent statement match zero rows instead of raising (indistinguishable
  from a real "DID NOT RAISE" failure without checking `pg_stat_user_tables`/re-querying with
  tenant context — the same class of footgun documented in `CLAUDE.md`'s
  "an ad-hoc connection with no `app.tenant_id` set silently returns 0 rows" rule, encountered here
  inside a test harness rather than a script).
- `backend/scripts/accounting_period_integrity_preflight.py` (new) — read-only, cross-tenant
  report script (not a pytest file): invalid status values, overlapping ranges, duplicate labels,
  journal/period date mismatches. Iterates each tenant's real `tenant_id` explicitly —
  `finance.accounting_periods`/`finance.journal_entries` have no RLS bypass clause, confirmed
  live (0 rows under the bypass id vs 7 real rows under East Gate's actual tenant_id).
- `backend/scripts/reconstruction_batch_lib.py` (new) — shared manifest/approve/apply
  orchestration used by both backfill scripts below; no permanent pytest coverage (a residual
  gap worth closing later — see below). `--preview` dry runs against live East Gate data confirm
  exact match to the task doc's figures (362 rows/$337,986.38 general-expense, 109 rows/$9,160.37
  lot-fee, zero overlap between the two populations), but `--preview` never calls this module at
  all. A same-day deep-dive re-audit ad-hoc-scripted a live call of most functions in this module
  (`get_accounting_period_for_date`, `claim_bank_transaction_for_batch`, `create_manifest_batch`,
  `approve_batch`, `begin_apply`, `load_pending_items`, `check_item_drift`) against real Postgres
  and found 3 real bugs invisible to the mocked suite: a phantom `PgAccountingPeriod.locked_by`
  ORM mapping for a column that doesn't exist (broke `get_accounting_period_for_date()` for every
  real caller), a `KeyError` in `compute_manifest_hash()` that would fire on
  `create_manifest_batch()`'s very first real call, and a `SET LOCAL`-vs-`commit()` tenant-context
  bug in `begin_apply()` that made `load_pending_items()` silently return zero items — the
  `--apply` command would have reported `posted=0 failed=0` and exited 0, a clean-looking no-op,
  while leaving the batch stuck at `applying` forever. All three fixed same-day.
  **Correction to an earlier draft of this entry**: several of these functions (`create_manifest_batch`,
  `approve_batch`, `begin_apply`) commit internally as their own atomic unit — they were NOT run
  inside a savepoint, and claiming "no data left committed" was wrong. Two batch header rows were
  found still durably committed afterward (`status='applying'`) and were removed by explicit
  `DELETE`, not by rollback. `mark_item_applied`/`finalize_batch_status` were exercised only with
  fake placeholder journal-entry UUIDs (never real ones, to avoid creating a real immutable East
  Gate journal entry) — whether that specific call passed or failed migration 0074's
  `expense_journal_entry_id` foreign key could not be reliably reconstructed afterward, so an
  earlier claim that a batch reached `status='applied'` is retracted as unverified.
  `FinancialCoreService.record_historical_expense()`/`charge_historical_lot_fee()` — the actual
  journal-posting code path — were not exercised against live Postgres by this pass at all. A
  direct fresh query confirms zero rows currently exist in either reconstruction table and zero
  unexpected `bank_transactions.reconstruction_batch_id` claims remain; see
  `tasks/GAP-FIN-018-...md`'s "Deep-dive
  re-audit" section for the full account. That ad-hoc verification is now permanent coverage:
  `tests/backend/test_reconstruction_batch_lib_integration.py` (see below) reproduces all three
  bugs as regression tests — this line previously said turning it into a pytest file was still an
  open follow-up, which was stale as of the commit that added that file.
- `tests/backend/test_reconstruction_batch_lib_integration.py` (new) — permanent
  `RUN_INTEGRATION_TESTS=1` regression coverage for the three bugs above:
  `TestModelsMatchLiveSchema` generically diffs every `financial_core` ORM model against
  `information_schema` (not just `PgAccountingPeriod`, so similar drift elsewhere is caught too);
  `TestManifestHashingWithRealPayloadShape` calls `create_manifest_batch()` with item dicts shaped
  exactly like the real scripts build them (no pre-set `sequence_number`); `TestTenantContextSurvivesInternalCommit`
  reproduces `begin_apply()` → `load_pending_items()` in the same session and asserts items are
  still visible; `TestAssertPendingLoadNotSilentlyEmpty` covers the independent
  `assert_pending_load_not_silently_empty()` guard (no-op when items remain, legitimate when all
  reached a terminal state, raises when genuinely stuck); `TestCrossSessionInterruptedAndResumedApply`
  simulates a crash between two separate DB sessions and asserts resume only touches items not yet
  terminal. Functions under test commit internally, so teardown is explicit `DELETE`/claim-release
  in `finally` blocks, not `SAVEPOINT` rollback.
- `tests/backend/test_ownership_repo_integration.py` (new, 2026-07-24) — permanent
  `RUN_INTEGRATION_TESTS=1` coverage for two more real bugs found the same day while finalising
  unit TH078's ownership to its sole approved owner (Tavis Christian Hamer), applying
  `backend/db_postgres/repos/ownership_repo.py`'s `upsert_owner_party()`/`close_ownership_period()`/
  `open_ownership_period()` directly against live Postgres for the first time end-to-end (the
  existing `test_write_postgres_ownership_period.py` mocks all three, so neither bug was visible
  there): (1) `upsert_owner_party()`'s INSERT used `:meta::jsonb` — SQLAlchemy 2.0.49's `text()`
  bind-param scanner truncates the last character of a parameter name immediately followed by `::`
  (`:meta::jsonb` parsed as bindparam `met`), raising a raw `PostgresSyntaxError` for any brand-new
  party with no prior email/name match, i.e. every first-time owner onboarding, not just TH078 —
  fixed with `CAST(:meta AS jsonb)`. (2) `close_ownership_period()` unconditionally set
  `valid_to = :vt`, which raises `ownership_periods_check` (`valid_to > valid_from` strict) when
  the row being closed has `valid_from` on or after the requested close date — TH078's exact
  situation, a period recorded in error on the same date it needed correcting — fixed to retract
  (`recorded_to` only, `valid_to` left `NULL`) rather than closing, whenever `valid_from >=
  valid_to`; the ordinary case (`valid_from < valid_to`) is unchanged and covered by its own
  regression test. Neither function commits internally, so these tests use one session, never
  commit, and roll back at fixture teardown — including a dedicated `is_test_data=TRUE` synthetic
  lot per test run, since `close_ownership_period()` closes every open period for a `lot_id`
  regardless of party and reusing a real East Gate lot would touch that lot's genuine open owner
  periods for the test's duration.
- `tests/backend/test_financial_matching_owner_resolution_integration.py` (new, 2026-07-25,
  deep-dive audit) — a next-day audit of the retraction fix above found it introduces a row shape
  (`valid_to IS NULL` with `recorded_to` set) that never previously existed in
  `core.ownership_periods`, and that `backend/routers/financial_matching.py`'s
  `_post_payment_to_ledger()` owner-resolution query (which resolves `payer_party_id` for a real
  payment) filtered only on `valid_to`/`valid_from`, never `recorded_to` — so a retracted row and
  the row that superseded it could both match its `WHERE ... LIMIT 1` with no `ORDER BY`,
  non-deterministic payer attribution. Confirmed live against TH078 (a synthetic reproduction only —
  no real query was run against real East Gate data for this proof; see the fix commit for the
  live-data confirmation). `TestOwnerResolutionIgnoresRetractedPeriods` builds a synthetic
  `is_test_data=TRUE` lot with an outgoing owner's period retracted and an incoming owner's period
  opened, same shape as TH078: one test proves the un-fixed query matches both rows (real bug,
  reproduced), the other proves the fixed query (`AND recorded_to IS NULL` added) deterministically
  returns only the live owner. A second, lower-stakes instance of the same gap was found and fixed
  in `backend/scripts/audits/east_gate_parity_audit.py` (a read-only Mongo/Postgres ownership-count
  comparison) but has no dedicated test — it is diagnostic tooling with no write path, and the fix
  is a one-line `AND recorded_to IS NULL` addition mirroring the tested case exactly.
- `tests/backend/test_trust_read_service_integration.py` (new, 2026-07-24; corrected 2026-07-25
  during a deep-dive audit — see below) — grepping the codebase for the same `:name::cast`
  truncation pattern after the ownership_repo.py fix above found two more live instances in
  `backend/services/trust_read_service.py`: `get_journal_entry()`'s `lines_query`
  (`:entry_id::uuid`) and `get_reconciliation_summary()`'s query (`:trust_account_id::uuid`), both
  fixed with `CAST(:name AS uuid)`. Fixing those surfaced a third, independent bug — not in either
  function above, but in **`get_reconciliation_summary()` and `list_statement_lines()`**, the two
  functions that call `get_reconciliation_run()` first: it returns `statement_start_date`/
  `statement_end_date` as `::text`-cast strings (correct for its own callers, which compare/
  serialise them as strings), but both downstream functions passed those strings directly as bind
  parameters against `bt.transaction_date`, a native `date` column — asyncpg raised `DataError`
  unconditionally for any real call. `get_journal_entry()` is unaffected by this third bug — it
  never touches period dates. Fixed with `date.fromisoformat(...)` at the two affected call sites,
  not by changing `get_reconciliation_run()`'s public string contract. `TestGetJournalEntryLinesQuery`
  uses a real East Gate journal entry with lines (found via query, not fabricated). No
  `finance.reconciliation_runs` row exists yet for East Gate, so `TestGetReconciliationSummaryQuery`
  and `TestListStatementLinesQuery` share a `synthetic_reconciliation_run` fixture that builds a
  trust account + run + bank transaction and **must commit** (unlike the ownership_repo tests above)
  so `TrustReadService`'s own separate `async_session_context()` connection can see it, then
  explicitly re-runs `set_tenant()` before its `finally`-block cleanup — the exact `SET LOCAL`-vs-
  `commit()` RLS footgun this repo's own CLAUDE.md documents was hit live while writing this
  fixture's first draft: the `finally` block's DELETEs silently matched zero rows and left two rows
  committed in `finance.trust_accounts`/`reconciliation_runs`/`bank_transactions` until caught and
  cleaned up manually. Confirmed clean (zero leftover rows) after the fix, across two consecutive
  real runs. **2026-07-25 correction**: the original write-up of this entry (and matching code
  comments in `trust_read_service.py`) called the third function `list_reconciliation_matches()` —
  it does not exist; the real name is `list_statement_lines()`. It also claimed the date bug hit
  "three" functions/call sites when it hits two. Both wrong on first write, caught during a
  deep-dive audit the next day, not caught by any test (naming/count errors in comments don't fail
  tests) — fixed in code comments, this file, and `list_statement_lines()` gained its own dedicated
  test (`TestListStatementLinesQuery`), which the original write-up didn't have despite the function
  being fixed.
- `backend/scripts/east_gate_2025_expense_reconstruction.py` (new) and
  `backend/scripts/east_gate_lot_fee_charge_backfill.py` (rewritten) — `--preview` is read-only
  and safe to run any time; `--create-manifest`/`--approve`/`--apply` write real,
  immutable-once-approved control-plane rows (and, for `--apply`, real ledger journals) and were
  deliberately NOT run for real this session — `--create-manifest`'s `created_by` is a permanent
  FK into `core.users` on an immutable record, not something to fabricate.

Full backend suite (`backend/venv/bin/python3 -m pytest tests/backend -q`, run from the repo
root — some pre-existing tests are cwd-sensitive and fail if run from inside `backend/`):
7,550 passed, 0 failed, 1,534 skipped, 1 xfailed.

## 2026-08-03 — Arrears calculation fix (per-unit obligation, never netted; grace-aware)

East Gate (13195) Financial Overview showed "31 units in arrears / $1,469.49 True Arrears";
live-verified correct figures are 14 units / $11,359.73. Two independent root causes: (1) 40
duplicate `levy_payments` records posted after a correct reconciliation, understating 10 units'
balances by $9,890.68 (data bug, reversed via
`backend/scripts/data_repair/reverse_duplicate_levy_payments_20260803.py`); (2)
`get_arrears_metrics()` reconstructed obligations from a life-to-date `opening_balance` plus an
independently-derived period levy instead of trusting `unit_levy_ledger.net_balance` (formula
bug, same defect class as the documented UA042 $963.31→$2,768 incident). See
`domain/finance/formulas/arrears.py` module docstring, `CLAUDE.md`'s "Arrears Are a Per-Unit
Obligation" rule, and `docs/architecture/financial_metrics_registry.md`'s
`levy.unit_arrears_and_credit.v1` entry for the full account.

- `tests/backend/test_domain_finance_arrears.py` (rewritten) — `quarter_true_arrears()`/
  `recoverable_arrears()` retired; tests now cover the single canonical
  `unit_arrears_and_credit()`: positive-balance-is-arrears, negative-balance-is-credit-never-
  arrears, zero-balance, in-grace-portion exclusion (and its clamp), a signature-lock test
  guarding against a future PR re-adding an opening-balance/period-levy reconstruction parameter,
  and a live-East-Gate-shaped (TH074) reproduction.
- `tests/backend/test_arrears_board.py` — 3 tests rewritten (`test_arrears_board_excludes_future_
  levy_periods` → `test_arrears_board_current_year_net_balance_counts_as_arrears`, etc.): these
  previously asserted the OLD, now-intentionally-changed behaviour (arrears scoped to prior-year
  opening only, current-year past-grace amounts excluded). Rewritten to assert the corrected
  behaviour instead of being deleted, since the underlying scenarios (zero-opening units, a
  zero-net-balance unit) are still worth covering.
- `tests/backend/test_collection_rate.py` — `TestGetArrearsUnitCount`/
  `TestGetArrearsUnitCountSubtractPayments` (7 tests) rewritten: the old mocks set up
  `annual_levies`/`units`/`levy_payments`/`get_levy_rates` dependencies the rewritten
  `get_arrears_metrics()` no longer has (it reads `unit_levy_ledger.net_balance` directly) — mocks
  simplified to just `unit_levy_ledger.find()` returning `net_balance`. Also fixed a latent bug in
  the original tests: `get_arrears_unit_count(year, num_overdue, 4)` was missing the required
  `building_id` positional arg (silently landing `4` there); calls now pass a building_id
  explicitly.

No k6 perf script added — this is a bug fix to existing endpoints' calculation logic, not new
functionality with different load characteristics; existing endpoint-level perf coverage applies
unchanged (the fix replaces one Mongo aggregate query with one `unit_levy_ledger.find()`, and adds
one additional non-parallelized `get_arrears_metrics()` call to `/finance/summary` and
`/finance/kpi-contract` — a deliberate, documented trade-off since in-grace exclusion now happens
inside that call, not a separate pipeline).

Full backend suite (`backend/venv/bin/python3 -m pytest tests/backend -q`, run from the repo
root): 7,940 passed, 4 failed (all 4 confirmed pre-existing via `git stash` — unrelated
jurisdictional-rules/expense-filter/GST-quarterly-budget tests), 1,552 skipped, 1 xfailed.

## GAP-FIN-063 — `finance.levy_kpi` Postgres branch (2026-08-18)

- `tests/backend/test_levy_kpi.py::TestLevyKpiPostgresBranch` (new, 4 tests) — router-level tests
  for `get_levy_kpi()`'s new per-lot Postgres net_balance override (mocked `db` + mocked
  `_financial_read_service`, following the `test_dashboard_pg_first.py` mock recipe):
  `test_uses_postgres_net_balance_when_source_postgres` (asserts a status *flip*, arrears→credit,
  to rule out a false pass; a unit absent from the PG map keeps its Mongo value),
  `test_falls_back_to_mongo_net_balance_when_pg_returns_none`,
  `test_falls_back_to_mongo_net_balance_on_pg_exception`, and
  `test_serves_mongo_when_source_not_postgres` (asserts `get_unit_levy_balance_list` is never
  called for a non-promoted building — no wasted PG round-trip).
- Run: `backend/venv/bin/python3 -m pytest tests/backend/test_levy_kpi.py -q` → 27 passed, 12
  skipped (pre-existing live-HTTP integration tests, gated behind `RUN_INTEGRATION_TESTS=1`).
- No k6 perf script added yet — the new PG fetch runs inside the route's existing
  `asyncio.gather()` (parallel with the pre-existing ledger/expense fetches, adding no serialized
  latency); tracked as a fast-follow in `tasks/GAP-FIN-063-levy-kpi-router-no-postgres-serving-path.md`
  rather than silently skipped, since CLAUDE.md's per-feature-benchmark policy is a default, not an
  absolute requirement for every low-traffic addition.
- Full evidence pack, including live East Gate reconciliation (0/87 diffs vs Mongo across
  2021-2025) and a newly-discovered, unrelated live production finding (GAP-FIN-068 — FY2026
  `unit_levy_ledger` missing `uoe`/fund-split fields due to a systemd-timer DR snapshot clobber):
  `docs/finances/GAP-FIN-063-levy-kpi-postgres-branch-reconciliation-evidence-pack-2026-08-18.md`.
- Same-day self-audit (requested explicitly, "deep-dive audit... hallucination" check):
  `docs/finances/GAP-FIN-063-self-audit-and-deploy-verification-2026-08-18.md` — confirms the fix
  is correct but NOT yet deployed (backend process predates the code; frontend currently down),
  fixes one docstring imprecision, caveats the FY2026 reconciliation row as circular (not
  independent), and files GAP-FIN-069 (unbounded per-lot Postgres connection fan-out, shared with
  `arrears_detail`, against a pool with a documented prior exhaustion incident). Per-section
  confidence ratings (1-10) included.

## GAP-FIN-068 / GAP-FIN-069 fixes (2026-08-18, same-day follow-up)

- `tests/backend/test_dr_mongo_snapshot.py` — rewrote `test_reversed_units_values_are_fully_
  replaced_not_patched` (which had asserted the BUGGY wholesale-replace behaviour was correct)
  into `test_reversed_units_values_are_freshly_set_every_run`, asserting the fixed `$set`
  behaviour instead. Added `test_does_not_wholesale_replace_the_document` — mocks `replace_one`
  to raise if called at all, and asserts the `$set` document contains exactly the 9 owned field
  names, nothing else. This is the regression test that would have caught GAP-FIN-068 before it
  shipped. All other tests in the file updated from `replace_one` to `update_one` mocks. 10/10
  passed.
- No new test file for GAP-FIN-069 (bounded concurrency) — the change is verified by re-running
  the existing full 6-year live reconciliation (Mongo vs Postgres per-unit balances, 87 units/
  year) and confirming 0 diffs, identical to the pre-fix run; a unit test asserting internal
  semaphore behaviour would be lower-value than that live correctness proof.
- Full regression run: `test_levy_kpi.py`, `test_dr_mongo_snapshot.py`,
  `test_financial_read_service_levy_kpi.py`, `test_dashboard_pg_first.py`, `test_arrears_board.py`,
  `test_finance_route_cutover_service.py` → 97 passed, 12 skipped, 0 regressions.
- Live data repair: `backend/scripts/data_repair/backfill_fy2026_static_ledger_fields_20260818.py`
  (new, dry-run by default, idempotent — verified via a second dry-run showing 0 writes needed)
  restored `uoe`/`lot_number` for East Gate's 87 FY2026 `unit_levy_ledger` documents.


## Request-metadata security baseline

The isolated security suite does not load the database-backed backend conftest:

```bash
python -m pytest tests/security -q
python scripts/validation/audit_request_metadata.py
```

The validator keeps direct forwarded-IP header reads inside
`backend/utils/client_ip.py`. Routers and services must consume
`utils.request_metadata` so trusted-proxy handling, request IDs, and bounded
user-agent values cannot drift between features.
