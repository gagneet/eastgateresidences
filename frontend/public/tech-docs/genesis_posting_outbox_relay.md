# Genesis Posting & Outbox Relay Architecture

## Comprehensive Guide to Double-Entry Accounting and Event Delivery

### What is Genesis Posting?

Genesis posting is the translation of business transactions into standard double-entry accounting entries suitable for a General Ledger (GL). When a levy is created or payment received in your building, the system automatically creates two balanced entries: one debit and one credit.

**Example: Levy Created**
- **Debit**: Accounts Receivable (AR) — $1,000
- **Credit**: Levy Income — $1,000
- **Balance**: Net zero (fundamental accounting principle)

---

## 1. Genesis Posting Flow

### Where Genesis Happens

Genesis posting occurs at these transaction points:

| Transaction | GL Accounts | Example |
|---|---|---|
| Levy Created | Debit: AR (1200)<br/>Credit: Levy Income (4100) | $5,000 levy → 2 entries |
| Payment Received | Debit: Cash (1000)<br/>Credit: AR (1200) | $2,000 payment → 2 entries |
| Invoice Approved | Debit: Expense (5000)<br/>Credit: AP (2000) | $800 invoice → 2 entries |
| Trust Release | Debit: Trust Liability (3000)<br/>Credit: Cash (1000) | $10,000 release → 2 entries |

### Creating Postings Programmatically

```python
# backend/services/financial_service.py

async def post_levy_to_genesis(levy_id: str, building_id: str):
    """
    Create GL postings when levy is finalized.
    Called from: POST /finance/levies/{id}/finalize
    """
    
    # 1. Retrieve levy from MongoDB
    levy = await db.annual_levies.find_one(
        {"_id": levy_id, "building_id": building_id}
    )
    
    if not levy:
        raise HTTPException(status_code=404, detail="Levy not found")
    
    # 2. Get GL codes from your building settings
    settings = await get_general_settings(building_id)
    gl_ar = settings.get("gl_accounts", {}).get("ar", "1200")
    gl_income = settings.get("gl_accounts", {}).get("levy_income", "4100")
    
    # 3. Create first entry: Debit AR
    posting_1 = {
        "id": str(uuid4()),
        "building_id": building_id,
        "account_code": gl_ar,
        "debit_cents": levy["amount_cents"],
        "credit_cents": None,
        "date": levy["levied_date"],
        "sequence": 1,
        "origin_document": levy_id,
        "origin_type": "levy",
        "narrative": f"Levy {levy['levy_number']} - Your Building",
        "created_at": now()
    }
    
    # 4. Create second entry: Credit Income
    posting_2 = {
        "id": str(uuid4()),
        "building_id": building_id,
        "account_code": gl_income,
        "debit_cents": None,
        "credit_cents": levy["amount_cents"],
        "date": levy["levied_date"],
        "sequence": 2,
        "origin_document": levy_id,
        "origin_type": "levy",
        "narrative": f"Levy {levy['levy_number']} - Your Building",
        "created_at": now()
    }
    
    # 5. Write to Postgres (primary) and MongoDB (audit trail)
    try:
        # Both writes in transaction
        async with db_postgres.transaction():
            await db_postgres.postings.insert_one(posting_1)
            await db_postgres.postings.insert_one(posting_2)
            
            # Create outbox entry for event relay
            await db_postgres.outbox.insert_one({
                "id": str(uuid4()),
                "aggregate_id": levy_id,
                "event_type": "postings_created",
                "payload": {
                    "building_id": building_id,
                    "levy_id": levy_id,
                    "amount_cents": levy["amount_cents"],
                    "posting_ids": [posting_1["id"], posting_2["id"]]
                },
                "published": False,
                "created_at": now()
            })
        
        # Audit trail in MongoDB
        await db.posting_audit_log.insert_one({
            "building_id": building_id,
            "origin_id": levy_id,
            "posting_ids": [posting_1["id"], posting_2["id"]],
            "status": "created",
            "timestamp": now()
        })
        
        return {
            "status": "success",
            "posting_ids": [posting_1["id"], posting_2["id"]]
        }
        
    except Exception as e:
        logger.error(f"Genesis posting failed for levy {levy_id}: {str(e)}")
        # Event delivery to observers will retry via outbox
        raise HTTPException(status_code=500, detail="Posting creation failed")
```

### GL Chart of Accounts (Building-Specific)

Your building's GL codes are stored in settings:

```python
# GET /finance/settings/gl-accounts
# Response:
{
    "gl_accounts": {
        "cash": "1000",
        "ar": "1200",
        "ap": "2000",
        "trust_liability": "3000",
        "levy_income": "4100",
        "interest_income": "4200",
        "operating_expense": "5000",
        "capital_expense": "5100"
    }
}
```

---

## 2. Outbox Pattern & Reliable Event Delivery

### Why Outbox?

Standard event publishing can fail:

```
Transaction success → Publish event → Network fails → Event lost
```

Outbox pattern ensures reliability:

```
Transaction success + Outbox entry → Worker retries → Event published
```

### Outbox Table Structure

```sql
CREATE TABLE outbox (
    id UUID PRIMARY KEY,
    aggregate_id TEXT NOT NULL,              -- References posting, levy, etc.
    event_type TEXT NOT NULL,                -- Event classification
    payload JSON NOT NULL,                   -- Event data
    published BOOLEAN DEFAULT FALSE,         -- Published flag
    attempt_count INT DEFAULT 0,             -- Retry counter
    last_attempt_at TIMESTAMP,               -- Last retry timestamp
    created_at TIMESTAMP DEFAULT NOW(),
    
    INDEX (published, created_at)            -- For efficient polling
);
```

### Outbox Entry Example

```json
{
    "id": "uuid-e8d4-4c3b-a1f2-7d2e9f3c5b1a",
    "aggregate_id": "levy_2026_001",
    "event_type": "postings_created",
    "payload": {
        "building_id": "13195",
        "levy_id": "levy_2026_001",
        "amount_cents": 500000,
        "posting_ids": ["posting-001", "posting-002"],
        "timestamp": "2026-05-04T10:30:00Z"
    },
    "published": false,
    "attempt_count": 0,
    "created_at": "2026-05-04T10:30:00Z"
}
```

### Outbox Relay Worker

The outbox relay is a background process that continuously publishes events:

```python
# backend/workers/outbox_relay.py

async def outbox_relay_worker():
    """
    Poll outbox table and publish events.
    Run: python -m workers.outbox_relay
    """
    
    poll_interval = 5  # seconds
    max_retries = 5
    
    while True:
        try:
            # 1. Fetch unpublished events (newest first)
            events = await db_postgres.outbox.query("""
                SELECT * FROM outbox
                WHERE published = FALSE
                AND attempt_count < %s
                ORDER BY created_at ASC
                LIMIT 100
            """, [max_retries])
            
            for event in events:
                await publish_event(event, max_retries)
            
            # 2. Dead-letter events that failed max retries
            await handle_dead_letters(max_retries)
            
            # 3. Sleep before next poll
            await asyncio.sleep(poll_interval)
            
        except Exception as e:
            logger.error(f"Outbox relay error: {str(e)}")
            await asyncio.sleep(poll_interval)


async def publish_event(event: dict, max_retries: int):
    """Publish single event to observers."""
    
    try:
        # Publish to analytics service
        await emit_to_analytics(event["payload"])
        
        # Publish to notification service
        await emit_to_notifications(event["payload"])
        
        # Mark as published
        await db_postgres.outbox.update(
            event["id"],
            {"published": True}
        )
        
        logger.info(f"Outbox event published: {event['id']}")
        
    except Exception as e:
        # Increment retry counter
        attempt = event.get("attempt_count", 0) + 1
        
        await db_postgres.outbox.update(
            event["id"],
            {
                "attempt_count": attempt,
                "last_attempt_at": now()
            }
        )
        
        if attempt >= max_retries:
            logger.warning(f"Event {event['id']} max retries exceeded")
            # Dead-letter process will handle it


async def handle_dead_letters(max_retries: int):
    """Move failed events to dead-letter queue."""
    
    dead_letters = await db_postgres.outbox.query("""
        SELECT * FROM outbox
        WHERE published = FALSE
        AND attempt_count >= %s
    """, [max_retries])
    
    for event in dead_letters:
        # Insert into dead-letter queue
        await db_postgres.dead_letter_queue.insert_one({
            "id": str(uuid4()),
            "outbox_id": event["id"],
            "event_type": event["event_type"],
            "payload": event["payload"],
            "last_error": event.get("last_error"),
            "attempts": event["attempt_count"],
            "created_at": now()
        })
        
        logger.error(f"Event {event['id']} dead-lettered after {event['attempt_count']} attempts")
        
        # Alert admin
        await send_alert({
            "severity": "high",
            "title": "Outbox Event Delivery Failed",
            "building_id": event.get("building_id"),
            "event_id": event["id"],
            "event_type": event["event_type"]
        })
```

---

## 3. Event Types

### Event Classification

Events are categorized by `event_type`:

```python
class EventType(str, Enum):
    # Posting events
    POSTINGS_CREATED = "postings_created"
    POSTINGS_REVERSED = "postings_reversed"
    
    # Reconciliation events
    RECONCILIATION_COMPLETED = "reconciliation_completed"
    RECONCILIATION_FAILED = "reconciliation_failed"
    
    # Validation events
    PARITY_CHECK_PASSED = "parity_check_passed"
    PARITY_CHECK_FAILED = "parity_check_failed"
```

### Event Payload Structure

```python
# Example: postings_created
{
    "event_type": "postings_created",
    "building_id": "13195",
    "aggregate_id": "levy_2026_001",
    "payload": {
        "posting_ids": ["posting-001", "posting-002"],
        "total_debit": 500000,
        "total_credit": 500000,
        "date": "2026-05-04",
        "origin_document": "levy_2026_001"
    }
}
```

---

## 4. Dead-Letter Queue (DLQ) Management

### What Goes to Dead-Letter Queue?

Events that fail after 5 retry attempts:

```python
# Example: Failed event
{
    "id": "dlq-uuid-001",
    "outbox_id": "outbox-uuid-xyz",
    "event_type": "postings_created",
    "payload": {...},
    "attempts": 5,
    "last_error": "Service analytics_worker unavailable",
    "created_at": "2026-05-04T12:00:00Z"
}
```

### Reviewing Dead Letters (Admin Feature)

```python
# Endpoint: GET /admin/dead-letters?building_id=13195&days=7
# Response:
{
    "total": 3,
    "building_id": "13195",
    "dead_letters": [
        {
            "id": "dlq-001",
            "event_type": "postings_created",
            "attempts": 5,
            "last_error": "Network timeout",
            "created_at": "2026-05-04T12:00:00Z",
            "action_url": "/admin/dead-letters/dlq-001/retry"
        }
    ]
}
```

### Manually Retrying Dead Letters

```bash
# Admin can retry a specific dead-letter event
POST /admin/dead-letters/{dlq_id}/retry
Authorization: Bearer admin-token
X-Building-ID: 13195

# Response:
{
    "status": "retry_initiated",
    "dlq_id": "dlq-001",
    "message": "Event moved back to outbox for retry"
}
```

### Cleanup

After manual retry succeeds, DLQ entry is deleted:

```python
# In outbox relay worker, after successful delivery
if was_in_dead_letter:
    await db_postgres.dead_letter_queue.delete_one({"_id": dlq_id})
    logger.info(f"Deleted DLQ entry {dlq_id}")
```

---

## 5. Retry Strategy & Backoff

### Exponential Backoff Schedule

```python
# Retry timing
RETRY_DELAYS = {
    1: 5,       # After 1st failure: 5 seconds
    2: 30,      # After 2nd failure: 30 seconds
    3: 300,     # After 3rd failure: 5 minutes
    4: 3600,    # After 4th failure: 1 hour
    5: 86400    # After 5th failure: 24 hours → then dead-letter
}

# Implementation
async def publish_event_with_backoff(event: dict):
    attempt = event["attempt_count"]
    
    if attempt > 0:
        delay = RETRY_DELAYS.get(attempt, 86400)
        await asyncio.sleep(delay)
    
    # Attempt publish...
```

### Idempotent Delivery

Events are idempotent — duplicate deliveries are safe:

```python
# Observers should check: does this posting already exist?
async def emit_to_analytics(payload):
    # Check if posting_id already processed
    existing = await analytics_db.postings.find_one({
        "posting_id": payload["posting_ids"][0]
    })
    
    if existing:
        logger.info("Event already processed, skipping")
        return  # Safe to skip duplicate
    
    # Process new event
    await analytics_db.postings.insert_many(...)
```

---

## 6. Configuration & Monitoring

### Environment Variables

```bash
# .env configuration

# Outbox Relay
OUTBOX_POLL_INTERVAL=5                 # seconds between polls
OUTBOX_MAX_RETRY_ATTEMPTS=5            # retries before dead-letter
OUTBOX_RELAY_ENABLED=True              # Enable/disable worker

# Event Publishing
ANALYTICS_SERVICE_URL=http://analytics:5000
NOTIFICATION_SERVICE_URL=http://notifications:5001
EVENT_PUBLISH_TIMEOUT=10               # seconds per publish attempt
```

### Monitoring Dashboard

```python
# Endpoint: GET /admin/outbox-stats
# Response:
{
    "buildings": [
        {
            "building_id": "13195",
            "pending_events": 12,
            "total_published": 4257,
            "dead_lettered": 3,
            "avg_delivery_time_ms": 245,
            "last_poll": "2026-05-04T13:45:30Z"
        }
    ]
}
```

---

## 7. Multi-Tenant Considerations

### Building-Scoped Events

Every event includes `building_id`:

```python
{
    "event_type": "postings_created",
    "building_id": "13195",              # ← Always present
    "payload": {...}
}
```

### No Cross-Building Event Leakage

Event observers filter by building:

```python
# Analytics service receiving event
async def process_posting_event(event):
    building_id = event["building_id"]
    
    # Only process for this building's analytics DB
    analytics_db = get_analytics_db(building_id)
    
    await analytics_db.postings.insert_many(...)
```

---

## 8. Troubleshooting

### Event Stuck in Outbox

**Symptom**: Same event in outbox for hours, not advancing

**Check**:
```sql
SELECT * FROM outbox WHERE published = FALSE
ORDER BY created_at DESC LIMIT 5;
```

**Investigation**:
1. Is outbox relay worker running? `ps aux | grep outbox_relay`
2. Are services healthy? Check `ANALYTICS_SERVICE_URL` connectivity
3. Check logs: `docker logs strataos-backend | grep outbox`

**Fix**:
1. Restart worker: `systemctl restart strataos-backend`
2. Or manually retry via admin API

### High Dead-Letter Rate

**Symptom**: Many events in DLQ, postings not propagating

**Investigation**:
```sql
SELECT event_type, COUNT(*) as count, MAX(last_error)
FROM dead_letter_queue
GROUP BY event_type
ORDER BY count DESC;
```

**Fix**:
1. Review error messages
2. Fix root cause (service outage, bad data, etc.)
3. Mass-retry from admin UI

### Duplicate Events in Analytics

**Symptom**: Postings appear twice in analytics dashboard

**Cause**: Non-idempotent observer, or replay during recovery

**Fix**: Ensure observers check for duplicates before inserting (see section 5)

---

## 9. Testing Genesis & Outbox

### Unit Test: Posting Creation

```python
# tests/backend/test_genesis_posting.py

async def test_levy_creates_genesis_postings(setup_db):
    """Verify levy creation triggers posting generation."""
    
    building_id = "13195"
    set_ctx_building_id(building_id)
    
    # Create levy
    levy = await create_test_levy(building_id, amount_cents=500000)
    
    # Finalize to trigger genesis
    await post_levy_to_genesis(levy["_id"], building_id)
    
    # Verify postings created
    postings = await db_postgres.postings.query("""
        SELECT * FROM postings
        WHERE building_id = %s AND origin_document = %s
    """, [building_id, levy["_id"]])
    
    assert len(postings) == 2
    assert postings[0]["debit_cents"] == 500000
    assert postings[1]["credit_cents"] == 500000
    assert postings[0]["debit_cents"] == postings[1]["credit_cents"]


async def test_outbox_event_created(setup_db):
    """Verify outbox entry created alongside posting."""
    
    levy = await create_test_levy("13195", amount_cents=500000)
    await post_levy_to_genesis(levy["_id"], "13195")
    
    # Check outbox
    outbox_event = await db_postgres.outbox.find_one({
        "aggregate_id": levy["_id"]
    })
    
    assert outbox_event is not None
    assert outbox_event["event_type"] == "postings_created"
    assert outbox_event["published"] == False
```

### Integration Test: Outbox Delivery

```python
async def test_outbox_relay_publishes_events(setup_db, mock_analytics):
    """Verify relay publishes events to observers."""
    
    # Create event in outbox
    await db_postgres.outbox.insert_one({
        "id": "test-uuid",
        "aggregate_id": "levy-001",
        "event_type": "postings_created",
        "payload": {"posting_ids": ["p1", "p2"]},
        "published": False
    })
    
    # Run relay
    await outbox_relay_worker()
    
    # Verify published
    updated = await db_postgres.outbox.find_one({"id": "test-uuid"})
    assert updated["published"] == True
    
    # Verify analytics received event
    mock_analytics.assert_called_once()
```

---

## 10. Related Documentation

- **[phase_f_prime_financial_core.md](phase_f_prime_financial_core.md)** — Overview and architecture
- **[17_phase_f_prime_cutover.md](mindmaps/17_phase_f_prime_cutover.md)** — Visual workflow
- **[tests/README.md](../../tests/README.md)** — Test commands
- **[backend/services/financial_service.py](../../backend/services/financial_service.py)** — Implementation

---

**Last Updated**: May 2026
**Status**: Active
**Author**: Strata Engineering Team
