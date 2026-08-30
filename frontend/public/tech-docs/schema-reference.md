# Community OS Schema Reference

**Database:** `eastgate_production` (MongoDB 6.0+)  
**All Community OS collections** are tenant-scoped: every document contains a `building_id` field that is automatically
injected by the API middleware.

---

## PostgreSQL Live Data Snapshot - 2026-07-24

PostgreSQL is deployed and populated, but it is not yet the primary runtime store for finance. The current live
inspection is published at
[`postgresql-live-data-inventory-2026-07-24.md`](postgresql-live-data-inventory-2026-07-24.md).

Key verified facts:

- Alembic head: `0071_powerhouse_cmd_foundation`.
- Application base tables inspected: 202 across 12 application schemas.
- Non-empty application tables: 43.
- East Gate `identity_core` is `postgres_write`.
- East Gate `trust_ledger`, `trust_reconciliation`, `governance`, `occupancy`, and `settings` are
  `postgres_read`.
- East Gate `finance_ledger` remains `postgres_shadow`.
- Finance tables with data: `finance.accounting_periods`, `finance.bank_transactions`,
  `finance.evidence_documents`, `finance.financial_cutover_config`, `finance.financial_onboarding_audit`,
  `finance.funds`, `finance.gl_accounts`, `finance.journal_entries`, `finance.journal_lines`,
  `finance.levy_items`, `finance.levy_runs`, `finance.receipt_allocations`, `finance.receipts`, and
  `finance.trust_accounts`.

The inventory lists every non-empty PostgreSQL table and the columns that have at least one non-null value. It is
metadata-only and intentionally excludes row values, owner PII, credentials, and sampled payloads.

---

## `workflow_requests`

Smart Request submissions from residents.

| Field             | Type         | Description                                          |
|-------------------|--------------|------------------------------------------------------|
| `_id`             | ObjectId     | Primary key                                          |
| `building_id`     | string       | Tenant scope                                         |
| `title`           | string       | Short summary of the request                         |
| `description`     | string       | Full description from resident                       |
| `category`        | string       | Auto-resolved or manually set category               |
| `priority`        | enum         | `critical` \| `high` \| `medium` \| `low`            |
| `status`          | enum         | `pending` \| `in_progress` \| `resolved` \| `closed` |
| `submitted_by`    | string       | User ID of submitter                                 |
| `assigned_to`     | string\|null | User ID of assigned manager                          |
| `unit_id`         | string\|null | Linked unit (from submitter's profile)               |
| `sla_due_at`      | datetime     | Computed from priority + created_at                  |
| `resolution_note` | string\|null | Manager's resolution summary                         |
| `activity_log`    | array        | Array of `{timestamp, user_id, action, note}`        |
| `created_at`      | datetime     |                                                      |
| `updated_at`      | datetime     |                                                      |

**Indexes:**

- `{building_id: 1, status: 1, created_at: -1}` (compound, primary query)
- `{building_id: 1, submitted_by: 1}` (resident's own requests)
- `{building_id: 1, assigned_to: 1, status: 1}` (manager workqueue)
- `{sla_due_at: 1}` (SLA breach monitoring)

---

## `proposals`

OC motions and votes.

| Field                 | Type           | Description                                                 |
|-----------------------|----------------|-------------------------------------------------------------|
| `_id`                 | ObjectId       | Primary key                                                 |
| `building_id`         | string         | Tenant scope                                                |
| `title`               | string         | Proposal title                                              |
| `description`         | string         | Full motion text                                            |
| `category`            | enum           | `capital_works` \| `by_law_change` \| `budget` \| `general` |
| `resolution_type`     | enum           | `simple` \| `special` \| `unanimous`                        |
| `status`              | enum           | `draft` \| `open` \| `closed`                               |
| `amount`              | number\|null   | Dollar value (for capital works proposals)                  |
| `levy_impact_per_lot` | object\|null   | Per-unit impact dict (see GAP-014)                          |
| `documents`           | array          | Array of document URLs                                      |
| `created_by`          | string         | User ID                                                     |
| `voting_opens_at`     | datetime\|null |                                                             |
| `voting_closes_at`    | datetime\|null |                                                             |
| `outcome`             | enum\|null     | `passed` \| `failed` \| `deferred`                          |
| `resolution_note`     | string\|null   | Final resolution note                                       |
| `vote_counts`         | object         | `{for: N, against: N, abstain: N}`                          |
| `votes`               | array          | Array of `{user_id, vote, voted_at, uoe_weight}`            |
| `created_at`          | datetime       |                                                             |
| `updated_at`          | datetime       |                                                             |

**Indexes:**

- `{building_id: 1, status: 1, voting_closes_at: -1}` (active proposal list)
- `{building_id: 1, created_by: 1}` (manager's proposals)
- `{building_id: 1, "votes.user_id": 1}` (duplicate vote check)

---

## `proposal_votes`

> **Storage note:** Individual votes are stored as embedded sub-documents within `proposals.votes` rather than as a
> separate top-level collection. This section documents the sub-document schema for reference and for any future
> migration
> to a dedicated collection.

| Field        | Type     | Description                                     |
|--------------|----------|-------------------------------------------------|
| `user_id`    | string   | Voter's user ID                                 |
| `lot_id`     | string   | Lot/unit identifier of the voting owner         |
| `vote`       | enum     | `for` \| `against` \| `abstain`                 |
| `voted_at`   | datetime | Timestamp of vote submission                    |
| `uoe_weight` | number   | Unit-of-entitlement weight applied to this vote |

**Access pattern:** Queried via `proposals` collection using `{building_id: 1, "votes.user_id": 1}` index (see
`proposals` section above).

**Duplicate-vote guard:** The API enforces uniqueness at the application layer — if `votes.user_id` already exists in
the array, `POST /proposals/{id}/vote` returns `400`.

---

## `savings_events`

Recorded OC savings (negotiated discounts, rebates, bulk-buy savings).

| Field            | Type         | Description                                                         |
|------------------|--------------|---------------------------------------------------------------------|
| `_id`            | ObjectId     | Primary key                                                         |
| `building_id`    | string       | Tenant scope                                                        |
| `title`          | string       | Short label                                                         |
| `description`    | string       | Detailed explanation                                                |
| `category`       | enum         | `maintenance` \| `insurance` \| `utilities` \| `admin` \| `capital` |
| `amount_saved`   | number       | AUD amount saved                                                    |
| `date`           | datetime     | Date saving was realised                                            |
| `financial_year` | string       | e.g. `"2025-2026"`                                                  |
| `evidence_url`   | string\|null | Link to supporting document                                         |
| `recorded_by`    | string       | User ID of recording manager                                        |
| `created_at`     | datetime     |                                                                     |

**Indexes:**

- `{building_id: 1, financial_year: 1, date: -1}` (YTD aggregation)
- `{building_id: 1, category: 1}` (category breakdown)

---

## `volunteer_events`

Community volunteer events with levy credit incentives.

| Field                | Type     | Description                               |
|----------------------|----------|-------------------------------------------|
| `_id`                | ObjectId | Primary key                               |
| `building_id`        | string   | Tenant scope                              |
| `title`              | string   | Event name                                |
| `description`        | string   |                                           |
| `date`               | datetime | Event date and time                       |
| `location`           | string   |                                           |
| `max_volunteers`     | number   | Maximum registrations allowed             |
| `levy_credit_amount` | number   | AUD credited per attendee                 |
| `status`             | enum     | `upcoming` \| `completed` \| `cancelled`  |
| `created_by`         | string   | User ID                                   |
| `attended_user_ids`  | array    | User IDs marked as attended on completion |
| `created_at`         | datetime |                                           |
| `updated_at`         | datetime |                                           |

**Indexes:**

- `{building_id: 1, status: 1, date: 1}` (upcoming events list)
- `{building_id: 1, created_by: 1}` (manager's events)

---

## `volunteer_registrations`

Individual resident registrations for volunteer events.

| Field               | Type           | Description                          |
|---------------------|----------------|--------------------------------------|
| `_id`               | ObjectId       | Primary key                          |
| `building_id`       | string         | Tenant scope                         |
| `event_id`          | ObjectId       | Reference to `volunteer_events._id`  |
| `user_id`           | string         | Registered user                      |
| `unit_id`           | string         | User's unit at time of registration  |
| `registered_at`     | datetime       |                                      |
| `credit_applied`    | boolean        | Whether levy credit has been applied |
| `credit_applied_at` | datetime\|null |                                      |

**Indexes:**

- `{event_id: 1, user_id: 1}` (unique — prevents duplicate registration)
- `{building_id: 1, user_id: 1, registered_at: -1}` (user's registration history)
- `{event_id: 1, credit_applied: 1}` (credit application sweep)

---

## `building_summaries`

Cached building-level KPI snapshot. Rebuilt on demand or scheduled.

| Field                         | Type     | Description                            |
|-------------------------------|----------|----------------------------------------|
| `_id`                         | ObjectId | Primary key                            |
| `building_id`                 | string   | Tenant scope (unique per building)     |
| `health_score`                | number   | 0–100 composite score                  |
| `health_tier`                 | enum     | `healthy` \| `attention` \| `critical` |
| `dimensions`                  | object   | Per-dimension scores (5 keys)          |
| `savings_ytd`                 | number   | AUD savings this financial year        |
| `active_proposals`            | number   | Open proposals count                   |
| `open_maintenance_requests`   | number   |                                        |
| `volunteer_events_this_month` | number   |                                        |
| `arrears_rate`                | number   | 0.0–1.0 fraction of lots in arrears    |
| `computed_at`                 | datetime | Timestamp of last recompute            |

**Indexes:**

- `{building_id: 1}` (unique — one summary per building)

---

## `lot_accounts`

Financial ledger accounts per lot (unit). Used for levy credits and residency scoring.

| Field               | Type           | Description                                  |
|---------------------|----------------|----------------------------------------------|
| `_id`               | ObjectId       | Primary key                                  |
| `building_id`       | string         | Tenant scope                                 |
| `unit_id`           | string         | Reference to `units` collection              |
| `owner_user_id`     | string         | Primary owner user ID                        |
| `ledger_balance`    | number         | AUD credit/debit balance (positive = credit) |
| `residency_score`   | number         | 0–100 score (see GAP-006)                    |
| `arrears_days`      | number         | Consecutive days in arrears                  |
| `last_payment_date` | datetime\|null |                                              |
| `created_at`        | datetime       |                                              |
| `updated_at`        | datetime       |                                              |

**Indexes:**

- `{building_id: 1, unit_id: 1}` (unique — one account per unit per building)
- `{building_id: 1, owner_user_id: 1}` (owner lookup)
- `{building_id: 1, arrears_days: -1}` (arrears report)

---

## `journal_entries`

Immutable double-entry ledger records for volunteer credits and other lot account adjustments.

| Field            | Type         | Description                                                    |
|------------------|--------------|----------------------------------------------------------------|
| `_id`            | ObjectId     | Primary key                                                    |
| `building_id`    | string       | Tenant scope                                                   |
| `lot_account_id` | ObjectId     | Reference to `lot_accounts._id`                                |
| `unit_id`        | string       | Denormalised for query convenience                             |
| `entry_type`     | enum         | `volunteer_credit` \| `levy_adjustment` \| `manual_correction` |
| `amount`         | number       | AUD (positive = credit to lot, negative = debit)               |
| `description`    | string       | Human-readable description                                     |
| `reference_id`   | string\|null | Source document ID (e.g., volunteer event ID)                  |
| `created_by`     | string       | User ID of creator                                             |
| `created_at`     | datetime     | Immutable — no updates allowed                                 |

**Indexes:**

- `{building_id: 1, lot_account_id: 1, created_at: -1}` (account statement)
- `{building_id: 1, entry_type: 1, created_at: -1}` (type-filtered audit)
- `{reference_id: 1}` (lookup all entries for a source event)
