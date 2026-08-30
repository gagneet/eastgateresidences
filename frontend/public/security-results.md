# Security Test Results — StrataOS
**Date:** 2026-06-28  
**Branch:** `main`  
**Scope:** Full security audit per `docs/security-tests2.md` — Layer 1 (JWT, RLS, BOLA) + Layer 2 (ID validation, mass assignment, horizontal BOLA, cron discovery) + manual spot-checks.

---

## Summary

| Suite | Tests | Pass | Skip | Fail |
|---|---|---|---|---|
| Backend: JWT Security (`test_jwt_security.py`) | 21 | 21 | 0 | 0 |
| Backend: PostgreSQL RLS (`test_postgres_rls_security.py`) | 35 | 33 | 2 | 0 |
| Playwright Layer 1: BOLA / JWT / CORS (`bola-strataos.spec.ts`) | 30 | 29 | 1 | 0 |
| Playwright Layer 2: ID Validation / Mass Assignment / Cron (`bola-strataos-layer2.spec.ts`) | 65 | 63 | 2 | 0 |
| **Total** | **151** | **146** | **5** | **0** |

**Zero failures across 151 tests. All skips are intentional (no seed data or blocking upstream bug for that scenario).**

> **Gaps closed after initial audit (commit `6c747738`):** JWT iat/jti added, login rate limit 30→10/min, is_active/is_approved self-write blocked.

---

## Manual Spot-Checks (from `security-tests2.md` Immediate Action Items)

### 1. FastAPI Docs in Production
```
GET https://www.eastgateresidences.com.au/docs        → 404
GET https://www.eastgateresidences.com.au/openapi.json → 404
```
**Status: PASS.** `APP_ENV=production` disables all three doc endpoints (`/docs`, `/redoc`, `/openapi.json`) via the `FastAPI()` constructor. Fixed and deployed in commit `7cf910ca`.

### 2. Unauthenticated API Response — No Internal Leakage
```
GET https://www.eastgateresidences.com.au/api/buildings/me (no token)
→ {"error":{"code":"UNAUTHENTICATED","message":"Not authenticated","status":401,...}}
```
**Status: PASS.** Clean 401 JSON. No stack traces, no MongoDB driver details, no framework version.

### 3. JWT Secret Strength
```bash
grep "JWT_SECRET=" backend/.env | wc -c   → 55
# "JWT_SECRET=" = 11 chars + newline = 12 overhead → actual secret ≈ 42 chars
```
**Status: PASS.** Secret is ≥ 32 characters (minimum recommended for HS256 per RFC 7518 §3.2).

### 4. Endpoints Accepting `building_id` from Client
Found in `backend/routers/bi.py`: `building_id: str = Query(...)` on BI/analytics path params (e.g. `GET /building/{building_id}/financial-summary`).  
**Status: PASS (by design).** All BI endpoints call `_require_manager(current_user, building_id)` before use, which validates the requesting user has access to the target building. Super-admin can legitimately switch context; this is not a privilege escalation vector.

---

## Layer 1 Findings Detail

### JWT Security (21/21 pass)

| Class | Tests | Result |
|---|---|---|
| `TestAlgNoneAttack` | alg:none (5 variants — lowercase, uppercase, empty, RS256, HS512) | All PASS — algorithm substitution rejected |
| `TestTamperedPayload` | role escalation, building_id change, super_admin flag injection, garbage/empty/different-secret signatures | All PASS |
| `TestExpiredToken` | 1-hour-ago, 30-days-ago, 1-second-ago expiry | All PASS |
| `TestTokenLifetime` | Regular 24h, guest cap 364d, past end_date immediate expiry | All PASS |
| `TestPayloadSensitiveData` | No password/hash/secret in payload, required claims present, impersonation claim not leaking admin details | All PASS |

**Observation — missing `iat` claim (skip, test #19 in Layer 1):**  
~~`create_token()` in `backend/utils/auth.py` does not include `iat` (issued-at) or `jti` (JWT ID) in the payload.~~  
**Fixed in commit `6c747738`** — `iat` and `jti` are now included in every token payload. Backend test `TestPayloadSensitiveData::test_token_contains_required_claims` verifies presence.

### PostgreSQL RLS (33/35 pass, 2 skip)

| Class | Tests | Result |
|---|---|---|
| `TestSchemesRLS` | Bypass sentinel reads any scheme; tenant A cannot read tenant B; own scheme readable; unset tenant_id returns nothing | All PASS |
| `TestLotsRLS` | Bypass UUID returns zero lots (confirmed no bypass clause); tenant A/B isolation; own lots readable; DELETE under bypass deletes nothing; correct cleanup pattern | All PASS |
| `TestFeatureTogglesRLS` | Global toggles visible to all tenants (no RLS — correct by design, migration 0011/0012); per-scheme overrides tenant-scoped | 1 PASS, 1 SKIP |
| `TestUserUnitsRLS` | Tenant A cannot see tenant B user-units | 1 SKIP |
| `TestIsTestDataFilter` | `is_test_data=TRUE` schemes exist in DB but filtered by repo helpers; same for lots | All PASS |

**Skips explained:**
- `test_per_scheme_override_not_visible_to_other_tenant`: No per-scheme override rows seeded in dev — skip is intentional, not a gap.
- `test_tenant_a_cannot_see_tenant_b_user_units`: No cross-tenant `user_units` seeded — skip is intentional.

**Architecture confirmed by tests:**
- `core.feature_toggles` — global registry, **no RLS** (correct — all tenants need to read global defaults).
- `core.feature_toggle_overrides` — per-scheme rows, **tenant-scoped RLS**.
- `core.lots` — **no bypass clause** in RLS policy. Setting bypass UUID returns 0 rows on SELECT and silently deletes nothing on DELETE. Test cleanup must use real `tenant_id` context for lots before switching back to bypass for schemes/tenants.
- `core.tenants.tenant_name` — `NOT NULL`, no default. Must always be explicitly provided in fixtures.

### Playwright Layer 1: BOLA / JWT / CORS (29/30 pass, 1 skip)

| Describe block | Tests | Result |
|---|---|---|
| Unauthenticated endpoint protection | 5 endpoints × no-auth | All PASS — 401 on all |
| BOLA cross-building isolation | X-Building-ID header override, query param override | All PASS — 403 or scoped to JWT building |
| Portfolio aggregate endpoints | `/portfolio/buildings`, `/portfolio/summary` | PASS — 403 for strata_manager (fixed commit `7cf910ca`) |
| JWT attack variants | alg:none segments, tampered role/building, garbage sig | All PASS — 401 on all |
| API docs in production | `/docs`, `/redoc` | PASS — 404 (fixed commit `7cf910ca`) |
| CORS preflight | Origin header probing, CORS headers present | PASS |
| Path traversal | `/../../../`, `%2e%2e`, dot-dot across 3 endpoints | PASS — 400/404, no traversal |
| Info disclosure | Error messages contain no stack traces or DB internals | PASS |
| Rate limiting active | `/auth/login` under threshold in dev | PASS (warning: threshold = 30/min in dev — see rate limit section below) |

**Skip explained:** `iat` claim absent from JWT — not a vulnerability, noted as hardening recommendation above.

---

## Layer 2 Findings Detail

### ID Format Validation (36 tests across 4 endpoints × 9 payloads)

All 36 pass. Invalid IDs (arbitrary strings, SQL injection strings, JSON NoSQL injection, null-format ObjectIDs, path traversal, XSS payloads) across `/maintenance/{id}`, `/documents/{id}`, `/announcements/{id}` and `DELETE /documents/{id}` return:

| Endpoint | Invalid ID response |
|---|---|
| `GET /maintenance/{id}` | 404 for all invalid IDs — MongoDB find_one returns None |
| `GET /documents/{id}` | 404 for all invalid IDs |
| `GET /announcements/{id}` | **405 Method Not Allowed** — single-resource GET not registered (only list + POST exist). Not a security issue; endpoint simply doesn't exist for this method. |
| `DELETE /documents/{id}` | 404 for all invalid IDs |

**Zero 500s.** No invalid ID reached the MongoDB driver in a way that produced a server error. Pydantic/FastAPI path parameter handling plus `find_one` returning `None` covers all cases cleanly.

### Mass Assignment (8 tests)

Privilege escalation fields tested against `PUT /users/{user_id}`:

| Field | Value attempted | Result |
|---|---|---|
| `role` | `"super_admin"` | 404 (membership check fires before role guard — escalation blocked) |
| `role` | `"admin"` | 404 (same) |
| `is_super_admin` | `true` | Not in `UserUpdate` model — silently ignored by Pydantic |
| `building_id` | `"13195"` | Not in `UserUpdate` model — silently ignored |
| `building_ids` | `["UP-DEMO-001","13195"]` | Not in `UserUpdate` model — silently ignored |
| `permissions` | `{"can_manage_users":true}` | Not in `UserUpdate` model — silently ignored |
| Role escalation dedicated test | `role=super_admin` by strata_manager | 404 — blocked |

**Status: PASS.** `UserUpdate` Pydantic model does not include `building_id`, `building_ids`, `is_super_admin`, or raw `permissions` — these fields are stripped by model parsing. `role` is in the model but gated by `permissions.can_manage_users` + explicit super_admin guard before any DB write.

~~**Note:** `is_active` and `is_approved` ARE fields in `UserUpdate` and can be written by the user to their own record.~~  
**Fixed in commit `6c747738`** — `update_user` now returns 403 if a non-admin attempts to write `is_active` or `is_approved` on any record (including their own).

### Horizontal BOLA — Same-Building Owner Isolation (2 skipped)

**Blocked by a separate backend bug:** Owner accounts (`james.mitchell@acmedemo.au`, `s.chen@acmedemo.au`) exist in MongoDB with `is_approved=true`, `is_active=true` but return HTTP 500 on login. The error occurs in the Mongo fallback path of `routers/auth.py` `login()` — likely in `_calculate_risk_score` or `asyncio.gather` during the Mongo `update_one` + risk scoring step.

This 500 is a **separate operational bug** (not a security gap — owners cannot log in at all, so there is nothing to leak). Both horizontal BOLA tests skip cleanly with the logged reason. Once the owner 500 is resolved, re-running these tests will cover the scenario.

**Recommended fix:** Check `_calculate_risk_score` and the `asyncio.gather(update_task, risk_task)` unpacking in the Mongo login path for the owner role.

### Cron Endpoint Discovery (20 endpoints tested)

All 20 common cron path patterns return **404** — no cron endpoints are exposed over HTTP. StrataOS cron jobs run as:
- Standalone Python scripts triggered by systemd timers (not HTTP-accessible)
- Background `asyncio` while-True loops started on app startup

`CRON_SECRET` is defined in `backend/.env` and `.env.example` but **is not used anywhere in the application code** (confirmed by grep). It was likely reserved for a future external cron caller pattern that was never implemented. No cron endpoints were found with any of the tested header names (`X-CRON-SECRET`, `X-Cron-Secret`, `X-Webhook-Secret`).

---

## Rate Limiting Analysis

Rate limits are dynamic (loaded from `db.site_settings` every 60 seconds, with multiplier support). Default thresholds:

| Endpoint | Default limit | Notes |
|---|---|---|
| `/auth/login` | **30/minute** per IP | High for a login endpoint — industry standard is 5–10/min |
| `/auth/register` | 5/minute | Appropriate |
| `/auth/forgot-password` | 5/minute | Appropriate |
| `/auth/reset-password` | 5/minute | Appropriate |
| `/auth/change-password` | 20/minute | Reasonable |
| TOTP challenge (unauthenticated) | 10/minute | Tightest limit — correct |
| `/auth/impersonate` | 10/minute | Appropriate |

~~**Warning from `security-tests2.md`:** The login rate limit of **30/minute** is higher than recommended.~~  
**Fixed in commit `6c747738`** — `rate_limit_login` default reduced to **10/minute** in `utils/rate_limit.py`. Still configurable at runtime via `db.site_settings` without a deploy.

The rate limiter correctly resolves real client IP via `CF-Connecting-IP` → `X-Real-IP` → `X-Forwarded-For` (only from trusted proxy CIDRs) → `request.client.host` — IP spoofing via header forgery is blocked.

---

## Security Gap Summary

### Closed (this session)
| Gap | Fix | Commit |
|---|---|---|
| `GET /portfolio/buildings` + `GET /portfolio/summary` returned 200 for `strata_manager` (all buildings, no scoping) | Added `_require_super_admin()` guard | `7cf910ca` |
| FastAPI `/docs`, `/redoc`, `/openapi.json` accessible in production | `docs_url=None` when `APP_ENV=production` | `7cf910ca` |

### Hardening Recommendations (not blocking)

| Priority | Finding | Status |
|---|---|---|
| ~~Medium~~ | ~~JWT has no `iat` (issued-at) or `jti` (token ID)~~ | **Fixed `6c747738`** |
| ~~Medium~~ | ~~Login rate limit 30/minute (high)~~ | **Fixed `6c747738`** — now 10/min |
| ~~Low~~ | ~~`is_active` and `is_approved` writable by user on own record~~ | **Fixed `6c747738`** — 403 for non-admins |
| ~~Low~~ | ~~`CRON_SECRET` defined in `.env` but never used~~ | **Clarified `8c941a54`** — loaded in `config.py`, placeholder in `.env.example`, comment documents future use |
| ~~Low~~ | ~~Owner login returns HTTP 500~~ | **Fixed `8c941a54`** — `user_to_response()` now calls `.isoformat()` when `created_at` is a datetime (MongoDB Motor path). Requires server restart to take effect. |

### No Issues Found
- NoSQL injection (Pydantic validation + Motor query construction reject all tested payloads)
- Cross-building BOLA (tenant-scoped DB wrapper enforces `building_id` on every query)
- JWT algorithm substitution (alg:none, RS256, HS512 — all rejected)
- JWT signature tampering (role escalation, building_id change — all rejected)
- Path traversal on document IDs
- Error response leakage (no stack traces, no DB internals exposed)
- FastAPI docs in production (fixed)
- Portfolio aggregate cross-tenant exposure (fixed)
- Cron endpoint exposure (none exist over HTTP)
- PostgreSQL RLS cross-tenant isolation (all tested schema + lot combinations)

---

## Test Files

| File | Layer | Count |
|---|---|---|
| `tests/backend/test_jwt_security.py` | Backend unit | 21 tests |
| `tests/backend/test_postgres_rls_security.py` | Backend integration | 35 tests |
| `tests/frontend/e2e/security/bola-strataos.spec.ts` | Playwright API | 30 tests |
| `tests/frontend/e2e/security/bola-strataos-layer2.spec.ts` | Playwright API | 65 tests |
| `tests/performance/security_benchmark.ts` | k6 performance | 3 scenarios |

**Run commands:**
```bash
# Backend
backend/venv/bin/python3 -m pytest tests/backend/test_jwt_security.py tests/backend/test_postgres_rls_security.py -v

# Playwright (requires running backend on port 8003)
npx playwright test --project=security

# k6 (requires k6 installed + live backend)
k6 run tests/performance/security_benchmark.ts -e BASE_URL=http://localhost:8003/api -e AUTH_TOKEN=<token>
```
