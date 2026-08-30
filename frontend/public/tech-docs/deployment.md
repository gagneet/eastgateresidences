# Community OS Deployment Guide

This guide covers the prerequisites, environment variables, database setup, and operational steps required to deploy the
Community OS tracks (Tracks 2 & 3) of the EastGate platform.

---

## Prerequisites

| Component      | Minimum version  | Notes                                                                                                                          |
|----------------|------------------|--------------------------------------------------------------------------------------------------------------------------------|
| **MongoDB**    | 6.0+ replica set | Required for transactions (volunteer credits) and change streams (future). Single-node RS acceptable for dev: `rs.initiate()`. |
| **Redis**      | 7.0+             | Required for Community OS event streams. Platform starts without it but events are silently dropped (GAP-011).                 |
| **Node.js**    | 20 LTS           | Frontend build.                                                                                                                |
| **Python**     | 3.11+            | Backend runtime.                                                                                                               |
| **pip / venv** | Latest           | Backend dependencies.                                                                                                          |
| **yarn**       | 1.22+            | Frontend dependencies.                                                                                                         |

---

## New Environment Variables

Add the following to your `.env` (backend) and verify they are set before starting the backend server.

```env
# Redis — required for Community OS event emitter
REDIS_URL=redis://localhost:6379/0

# Maximum number of messages retained per Redis stream
# Default: 1000 (adjust for higher-throughput environments)
REDIS_STREAM_MAXLEN=1000

# Community OS feature flags (can also be managed via Feature Toggles UI)
FEATURE_PROPOSALS=true
FEATURE_SAVINGS_LEDGER=true
FEATURE_VOLUNTEER=true
FEATURE_BUILDING_HEALTH_SCORE=true
FEATURE_SMART_REQUESTS=true
```

> All other environment variables (MongoDB URI, JWT secret, Stripe keys, etc.) are unchanged. See `DEPLOYMENT.md` for
> the full base configuration.

---

## MongoDB Setup

### 1. Verify replica set

```bash
mongosh --port 27018 eastgate_production --eval "rs.status()"
```

If `rs.status()` returns `{"ok": 0, ...}` the node is not in a replica set. Initialise it:

```bash
mongosh --port 27018 --eval "rs.initiate({_id: 'rs0', members: [{_id: 0, host: 'localhost:27018'}]})"
```

Wait ~10 seconds and verify `rs.status().myState === 1` (PRIMARY).

### 2. Run the idempotent collection migration

This script creates all Community OS collections with their indexes. It is safe to run multiple times — existing
collections and indexes are not modified.

```bash
cd backend
source venv/bin/activate
python scripts/db/community_os_collections.py
```

Expected output:

```
Creating collection: workflow_requests ... OK (indexes: 4)
Creating collection: proposals         ... OK (indexes: 3)
Creating collection: savings_events    ... OK (indexes: 2)
Creating collection: volunteer_events  ... OK (indexes: 2)
Creating collection: volunteer_registrations ... OK (indexes: 3)
Creating collection: building_summaries     ... OK (indexes: 1)
Creating collection: lot_accounts      ... OK (indexes: 3)
Creating collection: journal_entries   ... OK (indexes: 3)
Migration complete.
```

### 3. Seed feature toggles

```bash
python seeds/feature_toggles.py
```

This is also idempotent. The 5 new Community OS toggles (`proposals`, `savings_ledger`, `volunteer`,
`building_health_score`, `smart_requests`) are seeded as **enabled by default**.

---

## Redis Setup

### Install and start Redis

```bash
# Ubuntu / Debian
sudo apt install redis-server
sudo systemctl enable redis-server
sudo systemctl start redis-server

# Verify
redis-cli ping
# Expected: PONG
```

### Verify connection from backend

```bash
cd backend && source venv/bin/activate
python -c "import redis; r = redis.from_url('redis://localhost:6379/0'); print(r.ping())"
# Expected: True
```

---

## Backend

### Install new dependencies

```bash
cd backend && source venv/bin/activate
pip install -r requirements.txt
```

Key new dependencies added for Community OS:

- `redis>=5.0.0` — Redis Streams event emitter
- `motor>=3.3.1` — already present; transactions require replica set

### Start backend

```bash
uvicorn server:app --host 127.0.0.1 --port 8003
# or via systemd:
sudo systemctl restart strataos-backend
```

Check logs for the startup line:

```
INFO: Community OS event emitter initialised (Redis: connected)
```

If Redis is unavailable you will see:

```
WARNING: Community OS event emitter: Redis unavailable — events will be silently dropped (GAP-011)
```

---

## Frontend

### Install dependencies and build

```bash
cd frontend
yarn install
yarn build
```

### Start production server

```bash
# via systemd (recommended):
sudo systemctl restart strataos-frontend

# or directly:
yarn start
```

---

## Worker Processes

Community OS does not currently require dedicated worker processes. All operations are request-scoped.

**Future workers** (Track 4):

- `building_summary_worker.py` — change stream listener for incremental recomputes (GAP-012)
- `sla_breach_notifier.py` — polls `workflow_requests` for overdue SLAs and sends alerts

---

## Health Checks

After deployment, verify the new endpoints respond correctly:

```bash
BASE=https://eastgateresidences.com.au/api
TOKEN="<valid JWT>"

# Health score
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/community-dashboard/health-score" | python -m json.tool

# Building summary
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/community-dashboard/building-summary" | python -m json.tool

# Proposals list
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/proposals/" | python -m json.tool
```

---

## Test Commands

```bash
# Backend unit tests (all)
cd backend && source venv/bin/activate
python -m pytest tests/ -v

# Backend Community OS tests only
python -m pytest tests/test_community_os.py -v

# Frontend unit tests
cd frontend && yarn test

# E2E (Playwright) — requires running backend + frontend
yarn test
```

Expected: 42 backend unit tests passing for Community OS.

---

## Rollback

If rollback is required:

1. `sudo systemctl restart strataos-backend` (reverts to previous deployment if using blue-green)
2. Community OS collections can be left in place — they have no foreign key constraints on existing collections.
3. Feature toggles can be disabled via **Admin → Feature Toggles** without redeploying.

---

## Known Issues at Deployment

See `GAPS_AND_FUTURE.md` for the full gap list. Critical deployment notes:

- **GAP-011:** Redis is not required for startup but events will be dropped if unavailable. Ensure Redis is running
  before backend start.
- **GAP-013:** Volunteer credit application will fail on non-replica-set MongoDB. Run `rs.initiate()` if transactions
  fail.
- **GAP-012:** Building summaries are not auto-refreshed. Trigger an initial recompute after deployment:
  `POST /api/community-dashboard/recompute`.
