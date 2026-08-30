# Backend-specific guidance

This file loads when working with files under `backend/`. See the project-root `CLAUDE.md` for
repo-wide conventions.

## 🧪 Backend Test Patterns

### Mock DB — AsyncMock Is Required

Motor DB methods are coroutines. Use `AsyncMock` (not `MagicMock`) for every DB call:

```python
from unittest.mock import AsyncMock, MagicMock, patch

mock_db = MagicMock()
mock_db.maintenance_requests.find_one = AsyncMock(return_value={"id": "x", "building_id": "13195", ...})
mock_db.maintenance_requests.insert_one = AsyncMock(return_value=MagicMock(inserted_id="x"))

# Cursor for find() — return an object with .to_list()
cursor = MagicMock()
cursor.to_list = AsyncMock(return_value=[...])
cursor.sort = MagicMock(return_value=cursor)
mock_db.collection.find.return_value = cursor
```

If a DB method is called inside `asyncio.gather()`, it **must** be `AsyncMock` — `MagicMock` will cause
the gather to fail silently.

### Multi-Tenant Test Rules

1. Every mock document MUST include `building_id`.
2. Assert cross-building isolation: data seeded for `"13195"` must not appear in results for `"16244"`.
3. Call `set_ctx_building_id(bid)` before any function that touches the DB wrapper.
4. Use `admin_staff` as the role name (not `reception`, which is a UI alias only).

### Mocking Permissions

```python
from utils.permissions import Permission
with patch('utils.permissions.get_user_permissions') as mock_perms:
    mock_perms.return_value = Permission(can_manage_requests=True, ...)
    result = await my_endpoint(...)
```

### Rate-Limited Endpoints

Functions decorated with `@limiter.limit(...)` require a real `Request` object:

```python
from starlette.requests import Request as StarletteRequest
scope = {
    "type": "http", "method": "POST", "path": "/api/auth/login",
    "query_string": b"", "headers": [], "client": ("127.0.0.1", 12345)
}
req = StarletteRequest(scope)
result = await login(request=req, email="x@y.com", password="...")
```

### `asyncio.gather` — Three Gotchas

**1. Missing `import asyncio`** — When adding `asyncio.gather` to a file that previously only used
`asyncio.create_task`, verify `import asyncio` is present at module level. `NameError` is swallowed silently
inside `try/except Exception`. Audit after any perf commit:
```bash
grep -rn "asyncio\.gather\|asyncio\.wait" backend/routers/ | xargs grep -L "^import asyncio"
```

**2. Patching the `asyncio` module breaks `gather`** — `patch("routers.x.asyncio")` replaces the entire module.
If production code uses `asyncio.gather`, the patched mock must restore it:
```python
mock_async.gather = asyncio.gather  # pass through real gather
```

**3. All gathered coroutines must be `AsyncMock`** — A plain `MagicMock` inside `asyncio.gather()` raises
`TypeError: object MagicMock can't be used in 'await'`. After any "parallelize with gather" refactor, grep
the test file for `MagicMock(return_value=…)` on DB methods and convert them to `AsyncMock`.

### `$setOnInsert` — Validate Before Upsert

For upserts using `$setOnInsert`, a malformed value written once permanently occupies the unique key — all
subsequent writes with the same key are silent no-ops. **Always validate input before the upsert**, not after.
```python
# AGM vote bug: invalid vote string "maybe" permanently blocked the lot
# because the upsert fired first, creating status="maybe",
# and re-sends were idempotent no-ops thereafter.
if vote_value not in ALLOWED_VOTES:
    raise HTTPException(400, ...)
await db.votes.update_one(filter, {"$setOnInsert": {...}}, upsert=True)
```

### Mocking Routers vs. server.py

Auth functions moved to `routers/auth.py` in Session 56. Patch with:

- `patch("routers.auth.send_email_async")` — NOT `patch("server.send_email_async")`
- `patch("routers.auth._calculate_risk_score")` — NOT `patch("server._calculate_risk_score")`

Functions still in `server.py` (shared utilities): `create_token`, `get_current_user`, `hash_password`,
`verify_password`, `send_email_async` (server's own version).
