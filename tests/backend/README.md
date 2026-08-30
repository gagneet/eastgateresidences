# Backend Tests — East Gate Residences

Pytest test suite for all backend services, models, routers, and business logic.

**Last Updated:** 2026-04-08 (Reconciled financial ledger FY2021–2026: ~55 new tests for fund totals, balance chain,
levy rates, collection rate clamping, 2026 partial-year fields, multi-tenant isolation)
**Total Tests (canonical suite):** ~2791 passing pytest tests (2 pre-existing LBFI formula failures excluded)

---

## Quick Start

```bash
# From the project root (using venv)
cd /path/to/strata-management
backend/venv/bin/python3 -m pytest tests/backend/ -v

# Without venv (using system python3 — requires packages installed)
MONGO_URL=mongodb://localhost:27017 DB_NAME=test_db JWT_SECRET=test-secret JWT_ALGORITHM=HS256 \
  python3 -m pytest tests/backend/ -v

# Or using the pytest.ini defaults (testpaths = tests)
backend/venv/bin/python3 -m pytest
```

### Run a Specific File

```bash
backend/venv/bin/python3 -m pytest tests/backend/test_levy_payment_workflow.py -v
```

### Run a Specific Test

```bash
backend/venv/bin/python3 -m pytest tests/backend/test_levy_payment_workflow.py::TestAuditLogIntegration::test_verify_creates_audit_log -v
```

---

## Test Structure

```
tests/backend/
├── conftest.py                          # Adds backend/ to sys.path for all imports
├── README.md                            # This file
│
├── # ── FINANCIAL CORE ──────────────────────────────────────────────────────
├── test_finance_history.py              # ~55 tests — reconciled ledger FY2021–2026: fund totals, category sums, balance chain, levy rates from proposed budget, multi-tenant isolation, collection rate 0–100, 2026 partial-year fields
├── test_fy2026_march_actuals.py         # 28 tests  — FY2026 March actuals: category count/totals/spot-checks, fund balances, negative actuals, over-budget detection
├── test_collection_rate.py              # 103 tests — collection rate, arrears KPIs
├── test_finance_levy_status.py          # 36 tests  — levy status, period grace logic
├── test_finance_intelligence.py         # 30 tests  — health score, forecast, anomaly
├── test_finance_phase4.py               # 42 tests  — role restrictions, bounds, toggle
├── test_financial_phase_p1_p2.py        # 61+ tests — Phase 1 & 2 financial services
├── test_budget_proposals.py             # 45 tests  — budget proposal CRUD
├── test_report_service.py               # 12 tests  — PDF generation quality
│
├── # ── COMMUNITY OS TESTS (2026-03-27) ──────────────────────────────────────
├── test_community_os_unit.py            # 45 tests  — health score algorithm, proposal/savings/volunteer/
│                                        #             workflow-request Pydantic model validation, grade boundaries
├── test_db_wrapper.py                   # 16 tests  — TenantCollection/TenantScopedDatabase API surface,
│                                        #             TENANT_SCOPED_COLLECTIONS vs GLOBAL_COLLECTIONS sets,
│                                        #             8 Community OS collections confirmed tenant-scoped
│
├── # ── ENHANCED INTEGRATION TESTS (2026-03-27) ──────────────────────────────
├── test_integration_enhanced.py         # 28 tests  — multi-tenant isolation (7), financial txn rollback (5),
│                                        #             guest JWT 364-day cap (6), parcel notifications (4),
│                                        #             courier tracking service (6)
│
├── # ── IMPORTANT DOCS & MAINTENANCE FAB (2026-03-27) ────────────────────────
├── test_important_docs_and_maintenance_fab.py  # 28 tests — doc filtering, summary generation,
│                                        #             upload model, FAB role visibility, endpoint
│                                        #             mocks, dismissal persistence
│
├── # ── LEVY & PAYMENT WORKFLOW ─────────────────────────────────────────────
├── test_compute_period_due_dates.py     # 38 tests  — FY-split due date logic (months >=7 → levy_year-1)
├── test_levy_payment_workflow.py        # 28 tests  — verify/reject/delete, idempotency
├── test_advance_payments.py             # 34 tests  — advance payment logic
├── test_next_estimated_payment.py       # 34 tests  — next_due_date / next_payment algorithm; compute_next_payment helper uses integer-cents arithmetic (Session 68)
├── test_payments.py                     # 31 tests  — Stripe intent, webhook, history
├── test_payment_reminders.py            # 10 tests  — reminder scheduling
├── test_property_taxes.py               # 72 tests  — ACT FY, council rates, water bills
├── test_owner_dashboard_balance.py      # 18 tests  — balance_owing=net_balance, building_id mismatch regression; TestDueDatePriority (8 tests, Session 68)
│
├── # ── SECURITY & AUTHENTICATION ───────────────────────────────────────────
├── test_security.py                     # 62 tests  — geo, risk scoring, audit logs
├── test_ip_protection.py                # 4 tests   — IP protection endpoints
├── test_impersonation_security.py       # 2 tests   — PII masking on impersonation
├── test_analytics_security.py           # 3 tests   — analytics role restrictions
├── test_document_security.py            # 3 tests   — document access control
├── test_document_access_vulnerability.py # 1 test   — unapproved user blocked
├── test_communication_auth_vulnerability.py # 1 test — comm endpoint auth
│
├── # ── FEATURES ────────────────────────────────────────────────────────────
├── test_agm_voting.py                   # 67 tests  — AGM status, attendance, voting
├── test_new_features.py                 # 83 tests  — asset register, strata roll, pets, sinking fund
├── test_rental_certificates.py          # 27 tests  — RC-YYYY-NNNN, permissions, PDF
├── test_sessions_33_34.py               # 45 tests  — request forms, last login, notifications
├── test_arrears_board.py                # 18 tests  — grace period, obligation calc
├── test_work_orders.py                  # 43 tests  — WO create, approve, reject, workflow modes; ECPosition sub-roles
├── test_insurance_claims.py             # 16 tests  — CRUD, permissions, status transitions
├── test_maintenance_intelligence.py     # 11 tests  — risk score, capital schedule, levy stabilization (Session 50)
├── test_performance_optimizations.py    # 2 tests   — batch query perf, timeline optimization (migrated Session 50)
├── test_levy_calculator_perf.py         # 2 tests   — parallel levy calc logic (migrated Session 50)
├── test_sentinel_xss_fix.py             # 3 tests   — XSS sanitization, html.escape, title escaping (migrated Session 50)
├── test_capital_works_planner.py        # 49 tests  — sinking fund plan CRUD, milestones, WO edit access (Session 50b)
├── test_lbfi_sinking_fund.py            # varies    — LBFI scoring, capital shock + sinking fund integration
├── test_intelligence_extensions.py     # varies    — intelligence endpoint extensions
├── test_access_control_and_toggles.py  # varies    — feature toggles, role-based access control
├── test_levy_fairness.py               # varies    — levy fairness engine, LBFI computation
├── test_levy_allocation_engine.py      # varies    — levy allocation by unit entitlement/benefit group
├── test_simulation_engine.py           # varies    — levy stabilization simulation engine
├── test_levy_stability_score.py        # varies    — LSS formula, smoothed vs volatile projections
├── test_sentinel_rate_limit_auth.py    # 32 tests  — rate limit decorators, single source of truth (Session 56)
├── test_bolt_owner_transfers.py        # varies    — owner transfer workflow
├── test_bolt_maintenance_analytics.py  # varies    — maintenance analytics
├── test_bolt_maintenance_optimization.py # varies  — maintenance optimisation
├── test_bolt_notifications_optimized.py  # varies  — notification batch optimisation
├── test_whs_optimized_standalone.py    # 2 tests   — WHS optimized standalone (fixed)
├── test_bolt_matching_optimization.py  # 3 tests   — BOLT matching optimisation (fixed)
├── test_bolt_whs_optimized.py          # 1 test    — BOLT WHS optimized integration (fixed)
├── test_registration_approval_flows.py # varies    — registration approval, owner decision
├── test_owner_name_verification.py     # 53 tests  — name_utils algorithm, feature toggle, UserResponse fields, pre-flag at registration, approval-path flag, user_to_response passthrough, real-world name pairs (Session 70)
├── test_intelligence_fixes.py          # 43 tests  — database._inject_bid, capital-shock, levy-fairness, unit-TCO (Session 59)
├── test_owners_units_owner_role.py     # 8 tests   — owner/strata_manager access to /owners-units, year param format (Session 60)
├── test_utilities_lot_number.py        # 23 tests  — lot_number string parsing edge cases "LOT87"→87 (Session 60)
├── test_tenancy_service_audit.py       # 21 tests  — audit log user_name="" argument, signature compatibility (Session 60)
├── test_ownerhub_endpoints.py          # 56 tests  — ownerhub HTTP endpoints: role gating, properties CRUD, weekly-radar, units, unit-tco, tenancy (Session 60)
├── test_building_switcher.py           # 12 tests  — /buildings/me, /auth/switch-building permissions, JWT building_id fallback (Session 62)
├── test_marketplace_scope.py           # 12 tests  — scope=network listings, cross-building visibility, permission enforcement (Session 62)
├── test_announcement_broadcast.py      # 13 tests  — /announcements/broadcast, multi-building creation, role restrictions (Session 62)
├── test_building_kpis_fix.py           # 13 tests  — building-kpis year fallback, site settings new fields, JWT building_id defaults (Session 62)
│
├── # ── AUTHORIZATION & RBAC ────────────────────────────────────────────────
├── test_permissions.py                 # 43 tests  — RBAC permission service: user_can(), legacy mapping, ABAC rules, DEFAULT_PERMISSIONS (Session 63)
├── test_rbac_enhancements.py           # 43 tests  — RBAC audit fixes: effective_role serialisation, _check_owns_unit fallback, ROLE_PERMISSION_MAP completeness, isManager/isECMember logic, seed coverage (Session 65)
├── test_authorization_graph.py         # 41 tests  — Zanzibar graph engine: relation→permission derivation, BFS traversal, expiry, multi-building isolation (Session 63)
├── test_staff_management.py            # 35 tests  — Staff user management: list/detail/assign/revoke endpoints, cross-building isolation, EC position sync, access control (Session 64)
│
├── # ── SETTINGS & CONFIGURATION ────────────────────────────────────────────
├── test_settings_validation.py          # 5 tests   — levy_due_custom_dates Pydantic
├── test_custom_due_dates.py             # 3 tests   — period due date computation
├── test_email_access.py                 # 11 tests  — mail credential endpoint
│
├── # ── FINANCIAL DATA IMPORT ───────────────────────────────────────────────
├── test_building_id_field_consistency.py # 31 tests — building_id constants, net_balance formula, FY2026 aggregate totals (Sessions 66, 67)
├── test_fy2026_opening_balances.py      # 22 tests  — FY2026 CSV balance → total_paid/opening_arrears logic, UOE values, sign convention (Session 67)
├── test_financial_year_import.py        # 65 tests  — Financial Year CSV Import: unit owners, annual levy summary, budget categories, per-unit levy status
├── test_trust_accounting_phase1.py      # 63 tests  — Trust Accounting Phase 1: trust_accounts_v2 CRUD, trust_levy_schedules_v2, trust_transactions_v2 double-entry, trust_audit_logs immutability, multi-tenant isolation, schema validation
│
├── # ── PHASE 2 INTELLIGENCE & TRUST HARDENING ──────────────────────────────
├── test_stress_score.py                 # 20 tests  — 7-component BuildingFinancialStressScore: weight sum=1, category thresholds (healthy/watch/elevated/critical), full mock-DB integration, graceful degradation, building_id scoping
├── test_subsidy_map.py                  # 12 tests  — SubsidyMapEntry/SubsidyMapResult models, cross-subsidy computation, integer-cent enforcement, empty-assets→zero-subsidy
├── test_true_cost.py                    # 17 tests  — TrueCostOfOwnershipInput model, CPI geometric series, 1yr/5yr/10yr ordering, integer cents, assumptions list, unknown unit graceful handling
├── test_investor_intelligence.py        # 14 tests  — Grade A/B/C/D logic (boundary tests), red_flags_and_signals with correct 6-arg signature, permission 403 tests
├── test_insurance_lending.py            # 17 tests  — InsuranceRiskSignals/LendingValuationSignals models, _insurance_risk_tier (6-arg), _lending_risk_tier (4-arg), authorised roles
├── test_building_isolation.py           # 9 tests   — Cross-building isolation: idempotency key, posting building_id guard, cross-building reconciliation rejected, TCO building_id, stress score scoping
├── test_reconciliation_engine.py        # 9 tests   — MatchingEngine.score_pair (not score_candidate), detect_duplicate_bank_lines, building_id cross-building rejection, score thresholds
├── test_migration_pipeline.py           # 8 tests   — MigrationBatchStatus enum, ValidationFinding model, MigrationValidationReport, _assert_transition state machine, idempotent commit
│
├── # ── MISC / STATIC ANALYSIS ──────────────────────────────────────────────
├── test_notifications_performance.py    # 4 tests   — notification perf
├── test_frontend_static_analysis.py     # 15 tests  — frontend static analysis
├── test_schema_sync.py                  # 12 tests  — schema sync: multi-tenant feature toggle schema (building_id=None for global defaults, compound unique index)
│
└── # ── LEGACY INTEGRATION TESTS (HTTP, need live server) ───────────────────
    test_analytics_endpoints.py          # 32 tests  — HTTP analytics API
    test_announcements_enhanced.py       # 7 tests   — HTTP announcements
    test_api_fixes.py                    # 8 tests   — HTTP API fixes
    test_bolt_expired_users.py           # 6 tests   — expired user workflow
    test_bolt_feature_toggles.py         # 5 tests   — feature toggle API
    test_bolt_get_events.py              # 6 tests   — events API
    test_bolt_maintenance_stats.py       # 8 tests   — maintenance stats
    test_council_rates.py                # 28 tests  — council rates API
    test_dashboard_api.py                # 9 tests   — dashboard API
    test_directory_security.py           # 6 tests   — directory security
    test_email_configuration.py          # 18 tests  — email config API
    test_finance_2026_import.py          # 15 tests  — finance import
    test_finance_endpoints.py            # 43 tests  — finance API endpoints
    test_levy_endpoint.py                # 4 tests   — levy API endpoint
    test_levy_status_carry_forward.py    # 36 tests  — levy status carry-forward
    test_maintenance_security.py         # 10 tests  — maintenance security
    test_market_pulse.py                 # 3 tests   — market pulse
    test_notifications_feature.py        # 4 tests   — notifications feature
    test_notifications_v2.py             # 5 tests   — notifications v2
    test_owners_units_endpoint.py        # 50 tests  — owners-units endpoint
    test_recent_fixes.py                 # 14 tests  — regression tests
    test_schema_sync.py                  # 12 tests  — schema sync
    test_security_fixes.py               # 10 tests  — security fixes
    test_security_headers.py             # 4 tests   — security headers
    test_sentinel_guest_security.py      # 6 tests   — sentinel guest security
    test_spending_categories.py          # 20 tests  — spending categories
    test_user_registration_workflows.py  # 22 tests  — user registration
    test_utilities.py                    # 30 tests  — utility functions
    test_water_bills.py                  # 40 tests  — water bill API
```

Manual diagnostics that can touch live services are kept outside pytest
discovery under `manual/diagnostics/`.

---

## conftest.py — Path Setup

`tests/backend/conftest.py` ensures all tests can import from the backend:

```python
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, 'backend')
sys.path.insert(0, _backend_dir)
```

This allows tests to use `from routers.finance import ...`, `from services.anomaly_service import ...`, etc. regardless
of where `pytest` is invoked from.

> **IMPORTANT**: Always use `from routers.X import Y` (not `from backend.routers.X import Y`).
> Always use `patch("routers.X.Y")` (not `patch("backend.routers.X.Y")`).

---

## pytest.ini

`pytest.ini` at the project root configures:

```ini
[pytest]
testpaths = tests
asyncio_mode = auto
asyncio_default_fixture_loop_scope = session
asyncio_default_test_loop_scope = session
```

**Why session-scoped event loop?** Motor's `AsyncIOMotorClient` is created at module import time and must stay bound to
a live loop for the entire test session. Function-scoped loops cause Motor to fail on every test.

**Critical implication**: With session-scoped loops, `asyncio.create_task()` calls from one test may execute in a later
test's context, after any `@patch` decorators have already restored real objects. **Always patch every function called
via `asyncio.create_task()`** when also patching `db`. See Session 49b fix documentation.

---

## Canonical Test Run

The simplest way to run the full suite (pytest.ini sets `testpaths = tests`):

```bash
cd /home/gagneet/strata-management
backend/venv/bin/pytest -q
```

To run explicitly and exclude legacy HTTP tests, use this list:

```bash
backend/venv/bin/pytest \
  tests/backend/test_advance_payments.py \
  tests/backend/test_agm_voting.py \
  tests/backend/test_analytics_security.py \
  tests/backend/test_arrears_board.py \
  tests/backend/test_budget_proposals.py \
  tests/backend/test_collection_rate.py \
  tests/backend/test_communication_auth_vulnerability.py \
  tests/backend/test_custom_due_dates.py \
  tests/backend/test_document_access_vulnerability.py \
  tests/backend/test_document_security.py \
  tests/backend/test_email_access.py \
  tests/backend/test_finance_intelligence.py \
  tests/backend/test_finance_levy_status.py \
  tests/backend/test_finance_phase4.py \
  tests/backend/test_financial_phase_p1_p2.py \
  tests/backend/test_frontend_static_analysis.py \
  tests/backend/test_impersonation_security.py \
  tests/backend/test_ip_protection.py \
  tests/backend/test_levy_payment_workflow.py \
  tests/backend/test_new_features.py \
  tests/backend/test_notifications_performance.py \
  tests/backend/test_payment_reminders.py \
  tests/backend/test_payments.py \
  tests/backend/test_property_taxes.py \
  tests/backend/test_rental_certificates.py \
  tests/backend/test_report_service.py \
  tests/backend/test_security.py \
  tests/backend/test_sessions_33_34.py \
  tests/backend/test_settings_validation.py \
  tests/backend/test_maintenance_intelligence.py \
  tests/backend/test_performance_optimizations.py \
  tests/backend/test_levy_calculator_perf.py \
  tests/backend/test_sentinel_xss_fix.py \
  tests/backend/test_trust_accounting_phase1.py \
  tests/backend/test_whs_optimized_standalone.py \
  tests/backend/test_bolt_matching_optimization.py \
  tests/backend/test_bolt_whs_optimized.py \
  tests/backend/test_schema_sync.py
```

---

## Key Test Files — Coverage Summary

### `test_levy_payment_workflow.py` (28 tests) — Sessions 40–49b

| Class                     | Tests | Coverage                                                                                                                        |
|---------------------------|-------|---------------------------------------------------------------------------------------------------------------------------------|
| `TestVerifyPayment`       | 7     | Happy path, wrong status, 404, missing permission                                                                               |
| `TestRejectPayment`       | 3     | Rejection reason, audit trail                                                                                                   |
| `TestDeletePayment`       | 9     | Owner-own, owner-other, confirmed, manager, super_admin                                                                         |
| `TestLevyPaymentModels`   | 6     | Pydantic model validation                                                                                                       |
| `TestAuditLogIntegration` | 3     | Audit log creation; `_upsert_ledger_for_payment` and `_notify_payment_verified` patched to prevent asyncio.create_task DB leaks |

> **Session 49b fix** (2026-03-04): `test_verify_creates_audit_log` now patches `_upsert_ledger_for_payment` and
`_notify_payment_verified`. Without these patches, tasks fire after the `@patch` context exits (session event loop) and
> write to real MongoDB, corrupting the TH017 ledger. See `docs/fixes/session_49b_asyncio_task_leak_fix_2026-03-04.md`.

### `test_collection_rate.py` (103 tests) — Sessions 42–44b

| Class         | Tests | Coverage                                                        |
|---------------|-------|-----------------------------------------------------------------|
| Classes 1–9   | 52    | Collection rate formula, net position, current-year obligations |
| Classes 10–11 | 22    | Historical year proxy (next-year opening arrears)               |
| Classes 12–14 | 29    | Per-unit arrears calculation, subtract_payments param           |

### `test_arrears_board.py` (18 tests) — Session 47

| Class                         | Tests | Coverage                                                          |
|-------------------------------|-------|-------------------------------------------------------------------|
| `TestArrearsObligationLogic`  | 5     | `obligations_so_far = opening + periods_past_grace × period_levy` |
| `TestArrearsExclusion`        | 4     | Future levy periods excluded; paid units excluded                 |
| `TestGracePeriod`             | 3     | 14-day grace window; days_overdue from grace deadline             |
| `TestArrearsBoardIntegration` | 6     | Full board output shape and values                                |

### `test_security.py` (62 tests) — Session 38

| Class                         | Tests | Coverage                                                           |
|-------------------------------|-------|--------------------------------------------------------------------|
| `TestGeoUtility`              | 16    | UA parsing, device fingerprint, geo lookup, IP extraction priority |
| `TestRiskScoring`             | 7     | Risk weights, score cap at 100, alert threshold at 50              |
| `TestLoginAuditDocument`      | 9     | Required fields, geo structure, ISO datetime                       |
| `TestSecurityModels`          | 9     | Pydantic model validation                                          |
| `TestSecurityRouterEndpoints` | 7     | Auth enforcement, super_admin restriction                          |
| `TestSeedCredentials`         | 6     | `administrator@eastgateresidences.com.au` credential validation    |
| `TestSecurityRouterImport`    | 4     | Module importability, route paths                                  |
| `TestLoginAuditIntegration`   | 4     | DB insert on success, no-raise on DB error                         |

### `test_agm_voting.py` (67 tests) — Session 31

| Class                      | Tests | Coverage                                              |
|----------------------------|-------|-------------------------------------------------------|
| `TestAGMStatusTransitions` | 8     | draft→open→in_progress→closed→archived                |
| `TestAGMAttendance`        | 10    | RSVP, proxy, GET list                                 |
| `TestAttendanceConfirm`    | 7     | Admin confirms; non-admin blocked                     |
| `TestAGMMotionVoting`      | 14    | For/against/abstain, weighted UOE, duplicate rejected |
| `TestVoteValidation`       | 10    | Invalid value, missing motion_id, voters list         |
| `TestAGMResults`           | 12    | Tallies, ordinary vs special resolution thresholds    |
| `TestProxyNomination`      | 6     | Proxy must be registered owner; self-proxy rejected   |

### `test_new_features.py` (83 tests) — Session 28

| Class                       | Tests      | Coverage                                          |
|-----------------------------|------------|---------------------------------------------------|
| `TestAssetRegister`         | 10         | CRUD, permissions                                 |
| `TestAGMFeature`            | 8          | Motion, attendance, agenda                        |
| `TestStrataRoll`            | 7          | Roll entry, unit linkage, search                  |
| `TestPetRegister`           | 7          | Registration, approval                            |
| `TestSinkingFundSeedData`   | 22         | Plan data validation, closing chain 2035=$562,383 |
| `TestSinkingFundEndpoint`   | (included) | API shape, capital year flags                     |
| `TestSinkingFundProjection` | 8          | annual_collection≈$106,763; 2029≈$242,018         |

### `test_rental_certificates.py` (27 tests) — Session 29

| Class                    | Tests | Coverage                                          |
|--------------------------|-------|---------------------------------------------------|
| `TestStatusWorkflow`     | 5     | Status constants, 5-year expiry                   |
| `TestPermissions`        | 11    | Owner/tenant blocked; EC/admin/chairman allowed   |
| `TestCertificateNumber`  | 3     | `RC-YYYY-NNNN`, sequential, current-year          |
| `TestDataAutoPopulation` | 3     | Async levy calc, arrears from ledger, EC snapshot |
| `TestPDFGeneration`      | 5     | bytes, >1KB, PDF magic, EC members, null-safe     |

---

## Key Patterns & Rules

### Import Style (CRITICAL)

```python
# ✓ CORRECT — conftest.py adds backend/ to sys.path
from routers.finance import verify_levy_payment
from services.anomaly_service import check_anomalies
from models.finance import LevyPaymentVerify
from utils.finance_helpers import compute_period_due_dates

# ✗ WRONG — causes ModuleNotFoundError
from backend.routers.finance import verify_levy_payment
```

### Patch Paths (CRITICAL)

```python
# ✓ CORRECT
@patch("routers.finance.db")
@patch("routers.finance._upsert_ledger_for_payment", new_callable=AsyncMock)

# ✗ WRONG
@patch("backend.routers.finance.db")
```

### asyncio.create_task + @patch (CRITICAL — Session 49b)

If the function under test fires `asyncio.create_task(fn(...))`, patch `fn` in every test that patches `db`:

```python
# ✓ CORRECT — background tasks patched, no DB leaks
@patch("routers.finance.db")
@patch("routers.finance._upsert_ledger_for_payment", new_callable=AsyncMock)
@patch("routers.finance._notify_payment_verified", new_callable=AsyncMock)
async def test_something(self, mock_notify, mock_upsert, mock_db):
    ...

# ✗ WRONG — create_task fires after @patch exits → real MongoDB write
@patch("routers.finance.db")
async def test_something(self, mock_db):
    await endpoint_that_calls_create_task(...)
```

### Financial Calculation Patterns

- **Arrears (current year)**: `obligations_so_far = opening_arrears + periods_past_grace × period_levy`;
  `true_arrears = max(0, obligations_so_far - confirmed_paid)`
- **Arrears (historical)**: Use next-year `admin_opening + sinking_opening` as closing arrears proxy
- **DEFT/BPAY**: External payments have `confirmed_paid=0` in portal. NEVER subtract confirmed_paid for historical
  years.
- **net_balance** is NOT the arrears indicator for current year (includes undue future levy)

### Multi-Tenant Patterns

- **building_id in tests**: Always use `"13195"` (East Gate). Never use `"eastgate_residences"` (old slug, migrated
  Session 66).
- **feature_toggles.building_id=None**: Global defaults are seeded with `building_id=None`. Test assertions for
  schema_sync must allow for this field being null on global rows.
- **Trust collections**: Use `_v2` suffix. `trust_accounts_v2.building_id` is unique — exactly one document per
  building. `trust_audit_logs` rows are never updated.

---

## Session History

| Session                    | Key Tests Added                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
|----------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Trust Phase 1 (2026-03-24) | `test_trust_accounting_phase1.py` — 63 tests: trust_accounts_v2 CRUD (building_id unique), trust_levy_schedules_v2 instalment config, trust_transactions_v2 double-entry, trust_audit_logs immutability, multi-building data isolation, Pydantic model validation; `test_whs_optimized_standalone.py` (2 tests fixed), `test_bolt_matching_optimization.py` (3 tests fixed), `test_bolt_whs_optimized.py` (1 test fixed); `test_schema_sync.py` updated for multi-tenant feature toggle schema (building_id=None for global defaults, compound unique index)      |
| 70 (2026-03-17)            | `test_owner_dashboard_balance.py` — `TestDueDatePriority` (8 tests): verifies `next_due_date` takes priority over `period_status.current_due_date` in `FinancialSummaryCard`; advance-payer shows Q4 date not Q2 building calendar date; `OwnerDashboard` banner fallback chain (ownerUnit→levyStatus.quarters→[3,6,9,12] "first" East Gate schedule); `FinancialHero` neutral defaults. `test_next_estimated_payment.py` — `compute_next_payment` helper updated to integer-cents arithmetic (`nc_cents // pl_cents`) to match Session 68 server-side fix        |
| 67 (2026-03-16)            | `test_fy2026_opening_balances.py` — 22 tests (credit→total_paid, arrears→opening_arrears, UOE values, aggregate totals ~$27,207 paid / ~$2,795 arrears / 6.2% collection rate); `test_building_id_field_consistency.py` — 6 new aggregate tests (`TestFY2026AggregateBalances`); corrected `LOT_BALANCE_DATA` in import script (87 units with real CSV values replacing placeholder zeros); fixed `finance_helpers.py` + `routers/finance.py` + `server.py` to use `unit_levy_ledger.total_paid` instead of `levy_payments` for collection rate and arrears board |
| 66 (2026-03-15)            | Building ID migration — `DEFAULT_BUILDING_ID` changed to `"13195"` (Unit Plan number); all test files updated: `"eastgate_residences"` → `"13195"`, `"sierra_gungahlin"` → `"16244"`; `test_building_switcher.py` test renamed `test_default_building_id_is_plan_number`; DB migration script `scripts/db/migrate_building_ids_to_plan_numbers.py` applied (3,400+ records across 30 collections)                                                                                                                                                                 |
| 65 (2026-03-15)            | `test_rbac_enhancements.py` — 43 tests (effective_role serialisation when elevation active, _check_owns_unit unit_number fallback for legacy Excel data, ROLE_PERMISSION_MAP coverage for all 10 roles incl. reception slugs fix, isManager/isECMember role-set validation, seed user role coverage)                                                                                                                                                                                                                                                              |
| 64 (2026-03-14)            | `test_staff_management.py` — 35 tests (staff user list/detail endpoints, assign/revoke building roles, cross-building data isolation, EC position sync, duplicate assignment rejection, building validation, access control enforcement)                                                                                                                                                                                                                                                                                                                          |
| 63 (2026-03-14)            | `test_permissions.py` — 43 tests (RBAC permission service: user_can, legacy slug mapping, ABAC contextual rules, DEFAULT_PERMISSIONS validation); `test_authorization_graph.py` — 41 tests (Zanzibar graph BFS traversal, relation→permission derivation, expiry, multi-building isolation, check_permission pipeline)                                                                                                                                                                                                                                            |
| 60 (2026-03-12)            | `test_owners_units_owner_role.py` — 8 tests (owner role access + year format); `test_utilities_lot_number.py` — 23 tests (lot_number parsing, _unit_to_lot helpers); `test_tenancy_service_audit.py` — 21 tests (audit log user_name arg, signature, call style)                                                                                                                                                                                                                                                                                                  |
| 58 (2026-03-09)            | `tests/frontend/unit/pages/dashboard/IntelligenceAssetsPage.test.tsx` — 9 tests for asset intelligence page (loading, back-nav, table render, 2dp formatting, dialog, API failure)                                                                                                                                                                                                                                                                                                                                                                             |
| 57 (2026-03-09)            | `test_levy_fairness.py` extensions — Levy Fairness Engine real-data path; virtual cost centre derivation from `building_assets`+`facilities`                                                                                                                                                                                                                                                                                                                                                                                                                      |
| 56 (2026-03-08)            | `test_sentinel_rate_limit_auth.py` — 32 tests; route registration, single source of truth, rate limit decorators, shared limiter                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| 55 (2026-03-07)            | Auto-approve cron, BCC email, admin approval notifications (tested via existing auth flows)                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| 54 (2026-03-07)            | `test_guest_registration_flows.py` — FRONTEND_URL subdomain fix                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 51 (2026-03-05)            | `test_levy_payment_workflow.py` — password audit trail (`_log_password_change` endpoint)                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| 50b (2026-03-05)           | `test_capital_works_planner.py` — 49 tests (9 classes: sinking fund plan, milestones, WO edit access, 10yr schedule)                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 50 (2026-03-05)            | `test_maintenance_intelligence.py` — risk score edge cases, levy stabilization (3 classes); `test_work_orders.py` — ECPosition sub-role class (4 tests); migrated `test_performance_optimizations.py`, `test_levy_calculator_perf.py`, `test_sentinel_xss_fix.py` from backend/tests/ with import fixes                                                                                                                                                                                                                                                           |
| 49b (2026-03-04)           | `test_levy_payment_workflow.py` — asyncio.create_task patch fix; property_taxes import fix                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| 49 (2026-03-04)            | Session 49 plan: idempotency, anomaly service, collection rate, OwnersUnits filter                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 48 (2026-03-04)            | 9-bug finance dashboard fixes (arrears total, council rates, levy history)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| 47 (2026-03-03)            | `test_arrears_board.py` — grace period obligation calc                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 46 (2026-03-03)            | `test_communication_auth_vulnerability.py` — Motor patch isolation fix                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 44b (2026-03-03)           | `test_collection_rate.py` — classes 12–14, subtract_payments param                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 44 (2026-03-03)            | Dashboard card detail pages, collection rate dialog                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 43 (2026-03-03)            | Historical KPIs — collection rate, units outstanding                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 42 (2026-03-02)            | `test_collection_rate.py` — 52 tests (8 classes)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| 41 (2026-03-02)            | `test_finance_levy_status.py` — 36 tests                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| 40 (2026-03-02)            | `test_levy_payment_workflow.py` — 28 tests                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| 38 (2026-03-02)            | `test_security.py` — 62 tests                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 35 (2026-03-01)            | Registration notification tests                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 33–34 (2026-03-01)         | `test_sessions_33_34.py` — 45 tests                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 32 (2026-02-28)            | `test_payments.py` (31), `test_property_taxes.py` (72)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 31 (2026-02-28)            | `test_agm_voting.py` — 67 tests                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 30 (2026-02-28)            | `test_budget_proposals.py` — 45 tests                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 29 (2026-02-27)            | `test_rental_certificates.py` — 27 tests                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| 28 (2026-02-23)            | `test_new_features.py` — 83 tests                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| 27 (2026-02-23)            | `test_report_service.py` — 12 tests                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 26 (2026-02-23)            | `test_finance_phase4.py` — 42 tests; `test_finance_intelligence.py` — 30 tests                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |

---

## Troubleshooting

### "No module named 'routers'" / "No module named 'backend'"

Check that you're running from the **project root** and that `conftest.py` is in `tests/backend/`:

```bash
cd /home/gagneet/strata-management
backend/venv/bin/pytest tests/backend/test_X.py -v
```

### MongoDB connection issues

```bash
# Check MongoDB is running
sudo systemctl status mongod

# Verify .env file
cat backend/.env | grep MONGO_URL
```

### asyncio.create_task task leaks

Symptom: real DB writes during test runs (e.g., TH017 total_paid accumulates).

Fix: patch ALL functions called via `create_task` in any test that patches `db`. See
`docs/fixes/session_49b_asyncio_task_leak_fix_2026-03-04.md`.

### Legacy HTTP tests need a running server

The legacy integration tests (e.g., `test_finance_endpoints.py`, `test_owners_units_endpoint.py`) make real HTTP calls.
They require the backend server to be running:

```bash
# Start backend for legacy tests
cd backend && venv/bin/uvicorn server:app --reload --port 8003

# Then run legacy tests
backend/venv/bin/pytest tests/backend/test_finance_endpoints.py -v
```
