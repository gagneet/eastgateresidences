# Trust Accounting Module — Phase 1 Technical Documentation

## 1. Multi-Tenant Architecture

The platform serves multiple buildings (strata corporations) from a single codebase. Each building
has completely independent financial configuration stored in `building.trust_config` in MongoDB.

**The golden rule:** No per-building financial value may appear in application code, environment
variables, or hardcoded constants. Everything configurable is in MongoDB.

```
Platform (one codebase)
  ├─ East Gate Residences      (building_id: "13195")
  │    └─ trust_config: admin budget $340,870, UOE scale 82–161, CBA biller MOCK-EG-452301
  ├─ Sierra Gungahlin          (building_id: "16244")
  │    └─ trust_config: demo values, full seed (levy schedules + sample transactions)
  └─ Harbourview Residences          (building_id: "18932")
       └─ trust_config: demo values, full seed (levy schedules + sample transactions)
```

Same levy calculation function. Same CRN generation function. Different config → different results.

### Seed Strategy

| Building               | building_id | Seed approach                                                                                                                                                                                            |
|------------------------|-------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| East Gate Residences   | `"13195"`   | Trust config only — **no levy schedules or transactions seeded**. All financial data is real and imported from the Excel roll. The trust config is "sacred" and must not be overwritten by demo reseeds. |
| Sierra Gungahlin       | `"16244"`   | Full demo seed — trust config + levy schedules for current quarter + sample transactions. Safe to reseed at any time.                                                                                    |
| Harbourview Residences | `"18932"`   | Full demo seed — same as Sierra.                                                                                                                                                                         |

To reseed demo buildings without touching East Gate:

```bash
cd backend && venv/bin/python3 seeds/trust_accounting.py --buildings 16244 18932
```

To reseed East Gate trust config only (no schedules):

```bash
cd backend && venv/bin/python3 seeds/trust_accounting.py --buildings 13195 --config-only
```

---

## 2. BuildingTrustConfig Subdocument

Embedded in the `buildings` MongoDB collection as `trust_config`:

| Field                              | Type      | Description                                                    |
|------------------------------------|-----------|----------------------------------------------------------------|
| `current_financial_year`           | string    | e.g. "2026-27"                                                 |
| `admin_fund_annual_budget_cents`   | int       | Admin fund annual budget in integer cents                      |
| `sinking_fund_annual_budget_cents` | int       | Sinking fund annual budget in integer cents                    |
| `quarterly_due_dates`              | string[4] | 4 ISO date strings for quarterly due dates                     |
| `grace_period_days`                | int       | Days after due date before levy is overdue (default 14)        |
| `arrears_interest_rate`            | float     | Annual interest rate (decimal: 0.10 = 10% pa)                  |
| `total_uoe`                        | int       | Cached sum of all active unit UOE values                       |
| `deft_biller_code_admin`           | string    | DEFT biller code for admin fund                                |
| `deft_biller_code_sinking`         | string    | DEFT biller code for sinking fund                              |
| `bank_name`                        | string    | Primary bank name                                              |
| `arrears_reminder_days`            | int       | Days past grace period for first reminder (default 14)         |
| `arrears_formal_notice_days`       | int       | Days past grace period for formal notice (default 21)          |
| `arrears_interest_charge_days`     | int       | Days past grace period to start charging interest (default 30) |
| `arrears_legal_flag_days`          | int       | Days past grace period to flag legal action (default 60)       |
| `manager_alert_email`              | string    | Email for internal alerts                                      |
| `is_trust_configured`              | bool      | Set to true when trust has been fully configured               |

Who sets these values: The Strata Manager via the Trust Config API (`PUT /api/trust/v2/config/{building_id}`).
These values are NEVER set via environment variables.

---

## 2a. Two-Tier Feature Toggle Integration

Trust Accounting visibility is gated by the `trust_accounting` feature toggle, which follows the platform's two-tier
model introduced in Session 71:

| Tier                  | building_id                | Effect                                                      |
|-----------------------|----------------------------|-------------------------------------------------------------|
| Global default        | `None`                     | Applies to all buildings that have no per-building override |
| Per-building override | `"13195"`, `"16244"`, etc. | Overrides the global default for that building only         |

The effective toggle for a given request is: `per_building_override ?? global_default`.

Super-admins can view and edit toggles for any building via the Feature Toggles admin page. To reset a per-building
override back to the global default, call:

```
DELETE /api/feature-toggles/{toggle_key}?building_id={building_id}
```

This removes the override row and restores global-default behaviour for that building without affecting other buildings.

---

## 2b. Current Cutover Surface

The legacy V1 API in `routers/trust_accounting.py` still serves `/api/trust/*` for backwards
compatibility, but it is deprecated. Routed V1 responses include `Deprecation`, `Sunset`, `Link`,
and `Warning` headers pointing callers to `/api/trust/v2`, and the backend records hashed usage
telemetry for the removal window in the `trust_v1_usage_telemetry` collection (global, 90-day TTL —
see section 4.6). An earlier version of this telemetry only wrote to the application log via
`extra={...}`, which `logging.basicConfig`'s format string silently drops from the rendered text;
fixed 2026-07-16 to also write the durable collection, since journald retention on the production
host (~5 days) is shorter than the 14-30 day review window this needs anyway. Run
`scripts/validation/trust_v1_usage_report.py --days 30` for a route/actor/building breakdown once
deployed. Production access logs (for callers outside this application, e.g. a stale integration)
still need separate review before V1 writes are disabled — the Mongo telemetry only covers traffic
that actually reached this backend.

One V1 area has no V2 equivalent yet: `/api/trust/batches*` ABA batch approval. Treat that as a
legacy compatibility workflow and a Stage 2 blocker until a replacement or retirement decision is
recorded.

The live Phase 1 API is `routers/trust_phase1.py` under `/api/trust/v2/*`. Account-list,
account-balance, and transaction-list reads share one backend read service. That service computes
balances from `opening_balance_cents` plus immutable `trust_transactions_v2` entries; it does not
trust a denormalised `current_balance_cents` value as the running-balance anchor.

The first cutover shadow canary is `trust_v2.accounts.summary`, which compares V2 account count and
total computed balance cents with the PostgreSQL trust-account summary. Wider transaction,
reconciliation, account metadata, and interest-posting parity still need to pass before trust can be
promoted to PostgreSQL reads.

---

## 3. Financial Precision — Integer Cents

**The problem with floats:**

```
866.53 + 252.95 = 1119.4800000000001  ← wrong
86653  + 25295  = 111948              ← correct
```

**The rule:** All monetary values stored as integer cents. Dollar display only at UI boundary.

**Key functions** (`frontend/src/lib/trust/money.ts`):

```typescript
dollarsToCents("$9,187.44") → 918744   // parsing at UI input boundary
centsToDollars(918744)      → 9187.44  // conversion for display only
formatAUD(918744)           → "$9,187.44"  // display formatting
proportionalSplit(100, [1,1,1]) → [34, 33, 33]  // sums EXACTLY to 100
levyForUnit(8521750, 111, 10916) → 86653  // per-unit levy from building config
interestAccrued(100000, 0.10, from, to)  // uses building rate, not global constant
```

**API responses:** Every monetary field has two variants:

```json
{ "current_balance_cents": 918744, "current_balance_display": "$9,187.44" }
```

---

## 4. MongoDB Collections

All collections use `building_id` (string, matches the plan number: `"13195"`, `"16244"`, etc.) as the master isolation
key. Every query in every trust route must include this field.

### 4.1 trust_accounts_v2

One document per building × account type. Stores account metadata, an opening balance, and
last-reconciled statement fields. Display balances are computed on read from the opening balance plus
immutable transactions.

Key fields: `building_id`, `account_type` (admin_fund|sinking_fund|special_purpose),
`opening_balance_cents`, `last_reconciled_at`, `last_reconciled_balance_cents`.

**Index:** `{ building_id: 1, account_type: 1 }` unique

### 4.2 trust_levy_schedules_v2

One document per unit per quarter. Generated by `POST /api/trust/v2/levies/generate`.

Key fields: `building_id`, `unit_id`, `quarter`, `deft_crn`,
`admin_fund_cents`, `sinking_fund_cents`, `total_cents`,
`paid_cents`, `outstanding_cents`, `status`, `drb_stage`.

Snapshot fields capture state at generation time: `uoe_snapshot`, `total_uoe_snapshot`,
`admin_budget_cents_snapshot`, `sinking_budget_cents_snapshot`.

**Index:** `{ building_id: 1, quarter: 1 }`, `{ deft_crn: 1 }` unique

### 4.3 trust_transactions_v2

Immutable ledger entries. Never deleted. Reversals create a new REVERSAL entry.

Key fields: `building_id`, `trust_account_id`, `type`
(receipt|disbursement|bank_charge|interest|reversal|adjustment),
`amount_cents`, `is_reversed`, `reversal_of_id`.

**Index:** `{ building_id: 1, created_at: -1 }`, `{ trust_account_id: 1, created_at: -1 }`

### 4.4 trust_audit_logs

Immutable. Retained 7 years. Written for every financial mutation.

Actions: `trust_config_updated`, `trust_account_created`, `transaction_created`,
`transaction_reversed`, `levy_schedule_generated`, `levy_marked_paid`,
`arrears_escalated`, `reconciliation_linked`.

**Index:** `{ building_id: 1, timestamp: -1 }`, `{ entity_id: 1 }`

### 4.5 deft_notifications

Raw DEFT webhook payloads stored before processing. Unique on `deft_transaction_id`.

Statuses: `received → processing → matched|unmatched|duplicate|error`

### 4.6 trust_v1_usage_telemetry (global, not building-scoped)

Written by `routers/trust_accounting.py`'s `_emit_v1_usage()` on every routed legacy
`/api/trust/*` (V1) request — success or `HTTPException` alike. Deliberately global
(not `TenantScopedDatabase`-injected) because the Stage 2 retirement review needs
cross-building visibility, not one building at a time.

Fields: `event`, `route`, `method`, `building_id` (optional — absent if the auth
dependency chain short-circuited before capture ran), `actor_hash` (SHA-256, truncated
16 chars — never a raw user id), `actor_role` (optional), `request_id` (optional),
`timestamp` (str), `response_status` (int), `created_at` (native BSON date — the TTL
anchor).

**Indexes:** `ttl_90d` on `created_at` (`expireAfterSeconds=7776000`);
`route_created_desc` on `(route, created_at desc)`; `bid_created_desc` on
`(building_id, created_at desc)`.

Read via `scripts/validation/trust_v1_usage_report.py`, never queried directly by any
route handler.

---

## 5. DEFT CRN — Luhn Algorithm

### CRN Format

```
{billerCode}-{lotPadded4}-{quarterCode}-{checkDigit}
Example: "MOCK-EG-452301-0018-2026Q1-7"
```

### Luhn Modulo-10 Algorithm

1. Strip non-digits from base string (billerCode + lotPadded + quarterCode)
2. From right to left, double every second digit; if doubled > 9, subtract 9
3. Sum all digits
4. Check digit = (10 - (sum % 10)) % 10

### Multi-Tenant Security

Two buildings with the same lot number have DIFFERENT CRNs because their biller codes differ:

```
East Gate (13195) lot 1:  generateDeftCrn(1, "2026-Q1", "MOCK-EG-452301") → "MOCK-EG-452301-0001-2026Q1-X"
Sierra Gungahlin (16244) lot 1: generateDeftCrn(1, "2026-Q1", "MOCK-SG-162440") → "MOCK-SG-162440-0001-2026Q1-Y"
```

This prevents cross-building payment misapplication at the DEFT level.

---

## 6. Levy Calculation

### Algorithm

```
quarterly_budget = annual_budget / 4   (integer division from building.trust_config)
uoe_list = units.map(u => u.uoe)       (from units collection, never hardcoded)
splits = proportionalSplit(quarterly_budget, uoe_list)  (largest-remainder, exact sum)
per_unit_levy = splits[unit_index]
```

### proportionalSplit Guarantee

The array sums EXACTLY to `totalCents`. No rounding drift.
For 87 East Gate units with unequal UOE values and quarterly budget of $85,217.50,
all 87 values sum to exactly $85,217.50 (8521750 cents).

### Snapshot Fields

At generation time, the schedule captures:

- `uoe_snapshot` — unit's UOE at time of generation
- `total_uoe_snapshot` — building's total UOE at generation
- `admin_budget_cents_snapshot` — admin quarterly budget at generation
- `sinking_budget_cents_snapshot` — sinking quarterly budget at generation

These snapshots are immutable. They allow historical levy calculation auditing
even if the building's config changes in a subsequent quarter.

---

## 7. API Routes

### Trust Config

| Method | Path                                 | Permission   | Description               |
|--------|--------------------------------------|--------------|---------------------------|
| GET    | `/api/trust/v2/config/{building_id}` | trust.view   | Get building trust config |
| PUT    | `/api/trust/v2/config/{building_id}` | trust.manage | Update trust config       |

### Trust Accounts

| Method | Path                                  | Permission   | Description                  |
|--------|---------------------------------------|--------------|------------------------------|
| GET    | `/api/trust/v2/accounts`              | trust.view   | List accounts (JWT building) |
| POST   | `/api/trust/v2/accounts`              | trust.manage | Create trust account         |
| GET    | `/api/trust/v2/accounts/{id}/balance` | trust.view   | Balance summary              |

### Transactions

| Method | Path                                      | Permission   | Description                           |
|--------|-------------------------------------------|--------------|---------------------------------------|
| GET    | `/api/trust/v2/transactions`              | trust.view   | Paginated ledger with running balance |
| POST   | `/api/trust/v2/transactions`              | trust.manage | Create transaction                    |
| POST   | `/api/trust/v2/transactions/{id}/reverse` | trust.manage | Reverse transaction                   |

### Levies

| Method | Path                                         | Permission   | Description                       |
|--------|----------------------------------------------|--------------|-----------------------------------|
| POST   | `/api/trust/v2/levies/generate`              | trust.manage | Generate quarterly levy schedules |
| GET    | `/api/trust/v2/levies/{buildingId}/schedule` | trust.view   | Get levy schedule                 |
| POST   | `/api/trust/v2/levies/{scheduleId}/pay`      | trust.manage | Record manual payment             |

### Special

| Method | Path                              | Auth         | Description                  |
|--------|-----------------------------------|--------------|------------------------------|
| POST   | `/api/trust/v2/deft/webhook`      | HMAC         | DEFT payment notification    |
| POST   | `/api/trust/v2/arrears/escalate`  | trust.manage | Trigger arrears escalation   |
| GET    | `/api/trust/v2/financial-summary` | trust.view   | Aggregated financial summary |

---

## 8. DEFT Webhook — Sequence Diagram

```
DEFT → POST /api/trust/v2/deft/webhook
  │
  ├─ 1. Validate HMAC-SHA256 signature (skip in mock mode)
  │
  ├─ 2. Store raw payload as DeftNotification (status: "received") ← ALWAYS FIRST
  │
  ├─ 3. Check deft_transaction_id uniqueness
  │      duplicate? → update status: "duplicate", return 200
  │
  ├─ 4. Extract CRN from payload
  │
  ├─ 5. Lookup TrustLevySchedule by deft_crn
  │      not found? → status: "unmatched", internal alert, return 200
  │
  ├─ 6. Create TrustTransaction (type: receipt)
  │
  ├─ 7. Update TrustLevySchedule (paid_cents, outstanding_cents, status)
  │
  ├─ 8. Update DeftNotification (status: "matched", linked IDs)
  │
  └─ 9. Write TrustAuditLog → return 200 { received: true }
```

**ALWAYS returns HTTP 200.** Never return non-200 to DEFT — they will retry, which breaks deduplication.

**Cross-building protection:** A CRN from Building B can only match a levy schedule in Building B
(because it contains Building B's unique biller code). A webhook with Building B's CRN will be
"unmatched" if sent to the Building A endpoint.

---

## 9. Arrears Engine

### Configuration (per-building, from trust_config)

```python
BuildingArrearsConfig:
  grace_period_days: 14          # days after due date before overdue
  reminder_days: 14              # days past grace for reminder
  formal_notice_days: 21         # days past grace for formal notice
  interest_charge_days: 30       # days past grace for interest
  legal_flag_days: 60            # days past grace for legal action
  annual_interest_rate: 0.10     # per-annum decimal, from trust_config
```

### Escalation Stages

| Stage             | Trigger         | Action                                                              |
|-------------------|-----------------|---------------------------------------------------------------------|
| `reminder`        | grace + 14 days | Send email reminder                                                 |
| `formal_notice`   | grace + 21 days | Generate PDF notice, send email, set `drb_stage = "monitor"`        |
| `interest_charge` | grace + 30 days | Create INTEREST TrustTransaction                                    |
| `legal`           | grace + 60 days | Set `status = "legal"`, `drb_stage = "dca_eligible"`, alert manager |

### Interest Formula

```
interest = Math.round(principalCents × annualRate × daysOverdue / 365)
```

Rate comes from `building.trust_config.arrears_interest_rate` — never a global constant.

**Example:** $1,000 overdue at East Gate (10% pa) for 30 days → $8.22 interest
**Example:** $1,000 overdue at Riverside (8% pa) for 30 days → $6.58 interest

### DRB Integration

The arrears engine sets `drb_stage` on `TrustLevySchedule` which feeds into the
existing Debt Recovery Board (DRB) system:

- Stage `formal_notice` → `drb_stage = "monitor"`
- Stage `legal` → `drb_stage = "dca_eligible"`

---

## 10. Multi-Tenant Security

### building_id Isolation (RULE 4)

**Every MongoDB query in every trust route includes `{ building_id: jwtBuildingId }`.**

The `building_id` is extracted from the JWT token, never from the request body or URL parameters. Since Session 66
building IDs are plan-number strings (`"13195"`, `"16244"`, etc.) — they are **not** MongoDB ObjectIds.

```python
# CORRECT — building_id from JWT (string, not ObjectId)
jwt_bid = user.get("building_id")   # e.g. "13195"
query = {"building_id": jwt_bid, ...}

# WRONG — never do this
# jwt_bid = body.get("building_id")  ← data leakage vulnerability
# query = {"building_id": ObjectId(jwt_bid), ...}  ← building_ids are strings, not ObjectIds
```

### Isolation Test Expectations

Required isolation coverage should verify these behaviours before trust routes are changed:

- Building `"13195"` transactions never appear in building `"16244"` queries
- Building `"13195"` levy schedules are invisible to building `"16244"` queries
- A strata manager with building `"13195"` JWT gets 403 when requesting building `"16244"` data
- DEFT webhook with building `"16244"` CRN does not affect building `"13195"` schedules
- CRNs for the same lot in different buildings are different (different biller codes)

### Permission Roles

| Role           | trust.view        | trust.manage |
|----------------|-------------------|--------------|
| super_admin    | ✓                 | ✓            |
| strata_manager | ✓                 | ✓            |
| chairman       | ✓                 | ✗            |
| ec_member      | ✓                 | ✗            |
| owner          | ✗ (own levy only) | ✗            |
| tenant         | ✗ (own levy only) | ✗            |
| guest          | ✗                 | ✗            |

---

## 11. Adding a New Building

1. Create building document in MongoDB (via existing Building API)
2. Call `PUT /api/trust/v2/config/{building_id}` with:
    - Annual budgets in cents
    - DEFT biller codes (register with bank first)
    - Quarterly due dates
    - Grace period and arrears thresholds
    - Interest rate (state-specific)
3. Create trust accounts via `POST /api/trust/v2/accounts`
4. Seed units with UOE values
5. Call `POST /api/trust/v2/levies/generate` for the current quarter
6. The same platform code serves this building with its own config

---

## 12. Environment Variables

Only platform-level globals belong in `.env`. Per-building values live in MongoDB.

```env
MOCK_EXTERNAL_SERVICES=true        # "true" = mock all external services
DEFT_API_KEY=                      # platform-wide DEFT credentials
DEFT_API_BASE_URL=https://api.deft.com.au/v2
DEFT_WEBHOOK_SECRET=               # HMAC signing secret
R2_ACCOUNT_ID=                     # R2/S3 storage credentials
R2_BUCKET_NAME=silverfox-trust-documents
```

**Never put in .env:**

- Biller codes (per-building: `building.trust_config.deft_biller_code_admin`)
- Interest rates (per-building: `building.trust_config.arrears_interest_rate`)
- Grace periods (per-building: `building.trust_config.grace_period_days`)
- Manager emails (per-building: `building.trust_config.manager_alert_email`)

---

## 13. Activating Real DEFT

1. Obtain DEFT API credentials from your bank (CBA, NAB, or Westpac)
2. Set `DEFT_API_KEY` and `DEFT_API_BASE_URL` in `.env`
3. Register biller codes per-building via bank portal
4. Store biller codes in `building.trust_config.deft_biller_code_admin` via trust config API
5. Set `MOCK_EXTERNAL_SERVICES=false`
6. Configure `DEFT_WEBHOOK_SECRET` and point DEFT to your webhook URL
7. Note: biller codes are PER-BUILDING in MongoDB — not global env vars

---

## 14. Phase 2 Changes and Corrections

Phase 2 (Production Hardening) introduced `backend/services/trust_posting_service.py`,
`backend/services/reconciliation_matching_service.py`, and hardened the MRI migration
pipeline. Several logic corrections were made during Phase 2 implementation.

### 14.1 Reconciliation Matching — `THRESHOLD_EXACT` Correction

The original architecture doc stated `score >= 0.90` triggers an `exact` match type.
This was revised in the actual implementation.

**Why:** The maximum achievable score in `score_pair()` is **0.80**, not 1.0. This is
because `exact_amount_match` (0.40) and `amount_tolerance_match` (0.20) are mutually
exclusive. The maximum possible total is:

```
exact_amount (0.40) + same_date (0.20) + reference_match (0.15) + description_overlap (0.05) = 0.80
```

A date-proximity score of 1.0 contributes `0.20 × 1.0 = 0.20`; tolerance signals cannot
stack on top of exact amount. Therefore:

```python
THRESHOLD_EXACT = 0.80   # a perfect 4-signal match is correctly typed "exact"
THRESHOLD_LIKELY = 0.70
THRESHOLD_WEAK   = 0.50
```

**Consequence for tests:** `test_exact_match_same_day_high_score` asserts `score >= 0.80`
(not `>= 0.90`). Any test that checks `confidence_score >= 0.90` for an exact match will
fail — the correct lower bound is `0.80`.

### 14.2 Reconciliation Matching — `score_pair()` Building Isolation Guard

`score_pair()` in `MatchingEngine` enforces building-level data isolation at the pair
level. When `bank_line.building_id` and `internal_tx.building_id` are both present and
differ, the method **immediately returns** without computing any signals:

```python
if bl_building and tx_building and bl_building != tx_building:
    return ReconciliationMatch(
        confidence_score=0.0,
        match_type="none",
        match_reasons=["cross_building_mismatch"],
        suggested_action="reject",
    )
```

This is a second line of defence beyond the endpoint-level `building_id` filter. It
guarantees that even if the candidate generation loop is called with mixed-building data,
no cross-building pair can receive a non-zero score.

### 14.3 Investor Grade — Grade D Evaluated Before Grade C

The `compute_investor_grade()` function in `backend/routers/investor_intelligence.py`
evaluates Grade D **before** Grade C. The ordering is significant:

```python
# D checked BEFORE C — prevents severely under-funded reserves (< 50%)
# from being masked by the broader "reserve < 75%" C condition.
elif (stress_score >= 75 or reserve_adequacy_pct < 50 or arrears_rate_pct > 20):
    grade = "D"
elif (stress_score < 75 and (reserve_adequacy_pct < 75 or 10 <= arrears_rate_pct <= 20)):
    grade = "C"
```

Without this ordering, a building with `reserve_adequacy_pct = 45%` would satisfy the C
condition (`reserve < 75%`) and receive a C grade instead of the correct D.

**Grade thresholds in full:**

| Grade | Condition                                                                    |
|-------|------------------------------------------------------------------------------|
| A     | `stress < 25` AND `reserve > 100%` AND `arrears < 5%` AND `compliance > 90%` |
| B     | `stress < 50` AND `reserve > 75%` AND `arrears < 10%`                        |
| D     | `stress >= 75` OR `reserve < 50%` OR `arrears > 20%` (evaluated before C)    |
| C     | `stress < 75` AND (`reserve < 75%` OR `10% <= arrears <= 20%`)               |

### 14.4 `_compute_idempotency_key` — Function Signature

Located in `backend/services/trust_posting_service.py`. Takes **5 positional arguments**:

```python
def _compute_idempotency_key(
    building_id: str,
    unit_number: str,
    amount_cents: int,
    period: str,
    source_reference: str,
) -> str:
```

All arguments are lowercased and stripped before hashing. The SHA-256 payload is:

```
building_id | unit_number | amount_cents | period | source_reference
```

(pipe-separated, all lowercase, stripped of whitespace)

The `building_id` field ensures that the same posting for two different buildings
produces different idempotency keys — multi-tenant safety is baked in.

**Note:** This is a module-level function, not a method. It is called internally by
`TrustPostingService.post()` and should not be called directly from routers.

### 14.5 `_assert_transition` — Function Signature

Located in `backend/routers/mri_migration.py`. Takes **3 positional arguments**:

```python
def _assert_transition(current_status: str, target_status: str, batch_id: str) -> None:
```

Raises `HTTP 400` (not a custom exception) if the transition is not in
`_VALID_TRANSITIONS[current_status]`. The `batch_id` is included in the error message
for traceability. The architecture doc stub showing 2 args is superseded by this
3-argument implementation.

### 14.6 `MigrationBatchStatus` — Full Enum and Initial State

The complete `MigrationBatchStatus` enum (defined in `backend/models/mri_migration.py`):

| Value                | String                 | Description                                            |
|----------------------|------------------------|--------------------------------------------------------|
| `PENDING`            | `"pending"`            | **Initial state** — set when batch document is created |
| `VALIDATING`         | `"validating"`         | Validation in progress                                 |
| `VALIDATED`          | `"validated"`          | Validation passed, can proceed to dry-run              |
| `VALIDATION_FAILED`  | `"validation_failed"`  | Terminal — blocking errors found                       |
| `DRY_RUN`            | `"dry_run"`            | Dry-run in progress                                    |
| `DRY_RUN_COMPLETE`   | `"dry_run_complete"`   | Dry-run preview available                              |
| `READY_TO_COMMIT`    | `"ready_to_commit"`    | Human sign-off given, awaiting commit                  |
| `COMMITTING`         | `"committing"`         | Commit in progress                                     |
| `COMMITTED`          | `"committed"`          | Successfully imported                                  |
| `ROLLBACK_AVAILABLE` | `"rollback_available"` | Committed, within 30-day rollback window               |
| `FAILED`             | `"failed"`             | Terminal — commit failed, manual recovery needed       |
| `ROLLED_BACK`        | `"rolled_back"`        | Terminal — batch reverted                              |

The **initial state is `PENDING`** (not `created` as described in some earlier notes).
A newly created batch document has `status = MigrationBatchStatus.PENDING.value` set
by the router before the document is inserted.

The state machine in `_VALID_TRANSITIONS` includes `COMMITTING` as an intermediate
state (not shown in the simplified mermaid diagram in `mri-migration-state-machine.md`):
`VALIDATED` → `DRY_RUN` → `DRY_RUN_COMPLETE` → `READY_TO_COMMIT` → `COMMITTING`
→ `COMMITTED`.
