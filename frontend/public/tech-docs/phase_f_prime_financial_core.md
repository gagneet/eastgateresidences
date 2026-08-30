# Phase F-Prime: Financial Core Architecture

## PostgreSQL-Based Ledger Migration & Genesis Posting

### Overview

> **2026-06-01 correction:** This page is retained as historical Phase F-prime background. The current clean-slate strategy does **not** replay East Gate MongoDB financial history into PostgreSQL. Current schema head is Alembic `0044`, with `finance.evidence_documents`, `finance.financial_cutover_config`, `finance.financial_onboarding_audit`, and journal evidence/approval columns. Use `backend/scripts/postgres_cutover_p0_readiness.py --building-id 13195` and `docs/migration/tasks-to-postgres.md` for the active P0 switch gates.


Phase F-Prime is the multi-phase migration from MongoDB-centric financial tracking to a PostgreSQL-based ledger system with proper General Ledger (GL) accounting. This document describes the architecture, data flow, and operational procedures.

**Key Milestones**:
1. **Validation Phase** (Days 0-N) — Shadow reads, parity checking
2. **Genesis Phase** — Initial posting creation from historical data
3. **7-Day Parity Gate** — Continuous monitoring for divergence acceptance
4. **Go-Live** — Switch primary read source to PostgreSQL
5. **Cleanup** — Archive old data, delete test records

---

## 1. PostgreSQL Schema

### Core Tables

#### `postings` table
Core double-entry accounting table.

```sql
CREATE TABLE postings (
    id UUID PRIMARY KEY,
    building_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    account_code TEXT NOT NULL,        -- GL code (e.g., "1000-001")
    debit_cents INT,                    -- Amount debited (NULL if credit)
    credit_cents INT,                   -- Amount credited (NULL if debit)
    date DATE NOT NULL,                 -- Transaction date
    sequence INT,                       -- Sort order for same date
    narrative TEXT,                     -- Description
    origin_document TEXT,               -- Originating doc (levy_id, request_id)
    origin_type TEXT,                   -- Type (levy, invoice, payment)
    shadow_read BOOLEAN DEFAULT FALSE,  -- Validation mode flag
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP,
    
    FOREIGN KEY (building_id) REFERENCES core.tenants(id),
    INDEX (building_id, date),
    INDEX (account_code, date)
);
```

#### `reconciliations` table
Running balance snapshots.

```sql
CREATE TABLE reconciliations (
    id UUID PRIMARY KEY,
    building_id TEXT NOT NULL,
    account_code TEXT NOT NULL,
    balance_cents INT NOT NULL,
    period_end DATE NOT NULL,
    source TEXT,                      -- 'manual_entry', 'auto_computed'
    created_by TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### `genesis_validations` table
Parity gate tracking.

```sql
CREATE TABLE genesis_validations (
    id UUID PRIMARY KEY,
    building_id TEXT NOT NULL,
    validation_date DATE NOT NULL,
    posting_count_mongo INT,
    posting_count_postgres INT,
    total_debit_mongo INT,
    total_debit_postgres INT,
    total_credit_mongo INT,
    total_credit_postgres INT,
    variance_percent DECIMAL(5,2),     -- Allowed: < 0.01%
    status TEXT,                        -- 'pass', 'fail', 'pending'
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### `shadow_reads` table
Validation mode audit trail.

```sql
CREATE TABLE shadow_reads (
    id UUID PRIMARY KEY,
    query_fingerprint TEXT,
    mongo_result JSON,
    postgres_result JSON,
    divergence_found BOOLEAN,
    divergence_details TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### `outbox` table
Reliable event delivery.

```sql
CREATE TABLE outbox (
    id UUID PRIMARY KEY,
    aggregate_id TEXT NOT NULL,       -- posting_id
    event_type TEXT NOT NULL,         -- 'posting_created'
    payload JSON NOT NULL,
    published BOOLEAN DEFAULT FALSE,
    attempt_count INT DEFAULT 0,
    last_attempt_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 2. Genesis Posting Flow

### Creating Initial Postings

When a levy is created or payment received in your building, the backend generates postings:

```python
# Example: Levy Creation Genesis
async def create_genesis_postings(levy_id: str, building_id: str):
    """
    POST /finance/levies/{id}/genesis
    Create double-entry postings from levy data.
    """
    levy = await db.annual_levies.find_one({"_id": levy_id, "building_id": building_id})
    
    # Posting 1: Debit AR (Accounts Receivable)
    await db_postgres.postings.insert({
        "account_code": "1200",          # AR GL code
        "debit_cents": levy["amount_cents"],
        "date": levy["levied_date"],
        "origin_document": levy_id,
        "origin_type": "levy",
        "building_id": building_id,
        "shadow_read": False              # During validation: True
    })
    
    # Posting 2: Credit Levy Income
    await db_postgres.postings.insert({
        "account_code": "4100",          # Income GL code
        "credit_cents": levy["amount_cents"],
        "date": levy["levied_date"],
        "origin_document": levy_id,
        "origin_type": "levy",
        "building_id": building_id,
        "shadow_read": False
    })
    
    # Create outbox entry for observers
    await db_postgres.outbox.insert({
        "aggregate_id": levy_id,
        "event_type": "posting_created",
        "payload": {...}
    })
```

### GL Codes (General Ledger Chart of Accounts)

Every posting references a GL code from your building's chart of accounts:

| GL Code | Description | Type | Example |
|---------|-------------|------|---------|
| 1000-001 | Cash - Operating | Asset | Bank account |
| 1200 | Accounts Receivable | Asset | Unpaid levies |
| 2000 | Accounts Payable | Liability | Unpaid invoices |
| 3000 | Trust Account Liability | Liability | Held funds |
| 4100 | Levy Income | Income | Levies collected |
| 4200 | Interest Income | Income | Interest earned |
| 5000 | Operating Expenses | Expense | Repairs, utilities |
| 5100 | Capital Expenditure | Expense | Major projects |

---

## 3. Shadow Read Mode (Validation Phase)

### What is Shadow Reading?

During validation, every posting is written to **both** MongoDB and PostgreSQL. All **reads** come from MongoDB, but PostgreSQL gets a copy for validation.

```python
# Configuration
SHADOW_READ_MODE = True  # Enable in .env
FINANCIAL_CORE_READ_FROM_POSTGRES = False  # Still reading Mongo
```

### Data Synchronization

Every transaction during shadow mode:

```
Write request → 
  1. Insert into MongoDB (primary)
  2. Insert into PostgreSQL (shadow copy)
  3. Create outbox entry
  4. Return response to client
```

### Parity Monitoring

A scheduled job runs daily during validation:

```python
async def daily_parity_check():
    """Compare totals between Mongo and Postgres."""
    for building_id in all_buildings():
        mongo_total = await db.postings.aggregate([
            {"$match": {"building_id": building_id}},
            {"$group": {"_id": None, "total": {"$sum": "$debit_cents"}}}
        ]).to_list(1)
        
        postgres_total = await db_postgres.postings.query("""
            SELECT SUM(debit_cents) as total
            FROM postings
            WHERE building_id = %s
        """, [building_id])
        
        variance = abs(mongo_total - postgres_total) / postgres_total * 100
        
        if variance > 0.01:  # Alert on > 0.01% divergence
            await alert_ops("Parity failure", building_id)
            await dead_letter_queue.add({
                "building_id": building_id,
                "variance": variance,
                "details": {...}
            })
```

---

## 4. Outbox Relay & Dead-Letter Queue

### Reliable Event Delivery

The outbox pattern ensures events are delivered even if downstream services fail.

```python
async def outbox_relay_worker():
    """Poll outbox and deliver events."""
    while True:
        # Get unpublished events
        events = await db_postgres.outbox.query(
            "SELECT * FROM outbox WHERE published = FALSE LIMIT 100"
        )
        
        for event in events:
            try:
                # Attempt delivery (e.g., to analytics service)
                await emit_event(event)
                
                # Mark as published
                await db_postgres.outbox.update(
                    event["id"],
                    {"published": True}
                )
            except Exception as e:
                # Increment retry counter
                await db_postgres.outbox.update(
                    event["id"],
                    {
                        "attempt_count": event["attempt_count"] + 1,
                        "last_attempt_at": now()
                    }
                )
                
                # Dead-letter after 5 attempts
                if event["attempt_count"] >= 5:
                    await dead_letter_queue.add(event)
                    logger.error(f"Outbox event {event['id']} dead-lettered")
```

### Dead-Letter Review (Admin Feature)

Administrators can review and manually retry dead-lettered events:

```python
# Endpoint: GET /admin/dead-letters
# Response:
{
    "dead_letters": [
        {
            "id": "uuid-1",
            "event_type": "posting_created",
            "building_id": "13195",
            "error": "Service unavailable",
            "attempts": 5,
            "created_at": "2026-05-04T10:30:00Z"
        }
    ]
}

# Endpoint: POST /admin/dead-letters/{id}/retry
# Manually retry a failed event
```

---

## 5. 7-Day Parity Gate

### Gate Criteria

For go-live approval, all criteria must pass for 7 consecutive days:

| Criterion | Threshold | Measurement |
|-----------|-----------|-------------|
| **Posting Count Variance** | < 0.01% | (Postgres - Mongo) / Mongo |
| **Balance Variance** | < 0.01% | Debit/Credit totals |
| **AR Aging Match** | < 0.01% | Receivables by age bracket |
| **Dead-Letter Count** | 0 | No unresolved divergences |
| **Uptime** | 100% | No validation service outages |

### Daily Report Example

```
2026-05-04 Parity Report
=========================
Building: 13195 (East Gate)

Postings:
  Mongo Count:      2,847
  Postgres Count:   2,847
  Variance:         0.00% ✓

Totals:
  Debit (Mongo):    $2,450,320.48
  Debit (Postgres): $2,450,320.48
  Variance:         0.00% ✓

Dead Letters:      0 ✓
Status:            PASS (Day 5/7)
```

### Gate Result: PASS

Once 7 consecutive days pass:

```bash
# 1. Admin approval via UI
POST /admin/genesis/approve-gate
{
  "building_id": "13195",
  "approved_by": "admin@building.com"
}

# 2. System response
{
  "status": "go_live_authorized",
  "go_live_date": "2026-05-12T00:00:00Z"
}
```

### Gate Result: FAIL

If divergence is detected:

```bash
# System detects variance > 0.01%
# 1. Alert fires
POST /admin/notifications/alert
{
  "severity": "critical",
  "message": "Parity gate failed for building 13195",
  "variance": "0.15%",
  "divergence_details": {...}
}

# 2. Investigation & Fix
# - Review dead-letter queue
# - Fix logic/code
# - Extend validation +7 days
# - Monitor new code

# 3. Retry gate (reset day counter)
```

---

## 6. Go-Live Procedure

### Before Go-Live

1. ✅ Parity gate passed (7 days)
2. ✅ Dead-letter queue empty
3. ✅ Stakeholder approval
4. ✅ Rollback plan documented

### Go-Live Steps

```bash
# Step 1: Set environment flag (requires sudo)
sudo vim /backend/.env
# Set: FINANCIAL_CORE_READ_FROM_POSTGRES=True

# Step 2: Restart backend
sudo systemctl restart strataos-backend

# Step 3: Monitor for 2 hours
# - Watch API response times
# - Check error logs
# - Verify balance queries return correctly

# Step 4: Verify reads switched
GET /finance/summary
# Should now read from Postgres (faster, if indexed)
```

### 48-Hour Monitoring

After go-live, monitor continuously:

```python
async def post_go_live_monitoring():
    """Monitor first 48 hours for anomalies."""
    thresholds = {
        "api_response_time": 2000,  # ms
        "error_rate": 0.01,         # 1%
        "posting_divergence": 0.00  # No new divergences
    }
    
    for hour in range(48):
        metrics = await collect_metrics(hour)
        
        if metrics["error_rate"] > thresholds["error_rate"]:
            await rollback()
        
        if metrics["api_response_time"] > thresholds["api_response_time"]:
            logger.warning("API slowdown detected")
```

---

## 7. Cleanup Phase

### After 7 Days of Successful Production

```python
async def cleanup_phase():
    """Remove test data and archive old records."""
    
    # Step 1: Delete test-marked records
    await db_postgres.postings.delete_many({
        "is_test_data": True
    })
    
    # Step 2: Archive old MongoDB financial records
    # (Keep for 7 years compliance, but move to archive storage)
    archived = await db.annual_levies.find({
        "created_at": {"$lt": date(2026, 5, 4) - timedelta(days=7)}
    }).to_list(None)
    
    await db.archived_financial.insert_many(archived)
    await db.annual_levies.delete_many({
        "_id": {"$in": [r["_id"] for r in archived]}
    })
    
    # Step 3: Drop shadow_read artifacts
    await db_postgres.execute("""
        ALTER TABLE postings DROP COLUMN IF EXISTS shadow_read;
    """)
    
    # Step 4: Finalize
    logger.info("Phase F-Prime cleanup complete")
```

---

## 8. Configuration Reference

### Environment Variables

```bash
# Phase F-Prime Configuration (.env)

# Validation/Shadow Mode
SHADOW_READ_MODE=True                    # Enable shadow reading
PARITY_CHECK_SCHEDULE="0 2 * * *"        # 2 AM daily check
PARITY_VARIANCE_THRESHOLD=0.01           # 0.01% acceptance

# Go-Live Flag
FINANCIAL_CORE_READ_FROM_POSTGRES=False  # Pre-go-live
FINANCIAL_CORE_READ_FROM_POSTGRES=True   # Post-go-live

# Outbox Processing
OUTBOX_POLL_INTERVAL=5                   # seconds
OUTBOX_MAX_RETRY_ATTEMPTS=5
OUTBOX_DEAD_LETTER_THRESHOLD=5           # retries

# Postgres Connection (separate from read replica if any)
POSTGRES_FINANCIAL_URL=postgresql://...
POSTGRES_FINANCIAL_DB=financial_core
```

### Feature Toggles

```python
# Enabled via feature_toggles collection

{
    "building_id": "13195",
    "feature_key": "genesis_posting",
    "enabled": True,
    "metadata": {
        "phase": "f_prime",
        "validation_status": "parity_gate_passed",
        "go_live_date": "2026-05-12"
    }
}
```

---

## 9. Troubleshooting

### Divergence Detected in Shadow Mode

**Symptom**: Parity report shows variance > 0.01%

**Investigation**:
1. Check dead-letter queue: `GET /admin/dead-letters`
2. Review daily reconciliation: `GET /admin/parity-reports`
3. Search for posting mismatches: `POST /admin/divergence-search`

**Fix**:
1. Identify root cause (query logic, rounding, etc.)
2. Create fix in code
3. Extend validation phase +7 days
4. Monitor new code

### API Slowdown After Go-Live

**Symptom**: Response times increase, timeouts occur

**Investigation**:
1. Check Postgres connection pool: `SHOW max_connections;`
2. Review query plans: `EXPLAIN ANALYZE SELECT ...`
3. Check for missing indexes: `SELECT * FROM pg_stat_user_indexes;`

**Fix**:
1. Add indexes: `CREATE INDEX idx_building_date ON postings(building_id, date);`
2. Optimize queries
3. Monitor and verify improvement

### Can't Revert to Mongo After Go-Live

**Scenario**: Need to rollback to MongoDB reads

**Steps**:
1. Set env: `FINANCIAL_CORE_READ_FROM_POSTGRES=False`
2. Restart backend
3. Verify reads are from Mongo
4. Investigate what went wrong
5. Don't do cleanup until issue resolved

---

## 10. Multi-Tenant Considerations

### Building-Scoped Validation

Each building goes through its own parity gate independently:

```
Building 13195: Day 4/7 validation
Building 16244: Go-live approved, reading Postgres
Building demo:  Skipped (test data)
```

### Tenant ID in Postgres

The `tenant_id` field tracks cross-tenant references:

```python
# Example: AR linked to user
posting = {
    "building_id": "13195",
    "tenant_id": "user_12345",              # User record ID
    "account_code": "1200",
    "debit_cents": 50000,
    "origin_document": "levy_2026_001"
}
```

### No Cross-Building Queries

All postings queries include building_id:

```python
# ✅ Correct
await db_postgres.postings.query(
    "SELECT * FROM postings WHERE building_id = %s AND date = %s",
    [building_id, date]
)

# ❌ Wrong (would leak data)
await db_postgres.postings.query(
    "SELECT * FROM postings WHERE date = %s",
    [date]
)
```

---

## 11. Related Documentation

- **[17_phase_f_prime_cutover.md](mindmaps/17_phase_f_prime_cutover.md)** — Visual workflow
- **[tests/README.md](../../tests/README.md)** — Phase F-prime test suites
- **[backend/database.py](../../backend/database.py)** — TenantScopedDatabase wrapper
- **[Architecture ADRs](../architecture/adr/)** — Decision records

---

**Last Updated**: May 2026
**Status**: Active
**Author**: Strata Engineering Team
