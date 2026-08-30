# Critical Rules — Survive Compaction

These rules MUST be followed regardless of what was discussed earlier in the session.
If context has been compressed, re-read this file before writing any code.

---

## Project identity

**StrataOS** — multi-tenant strata management SaaS. Every tenant is a building identified by `building_id`.
Cutover is per-domain AND per-building, never a single global switch.

**Verified live 2026-08-29: ALL EIGHT East Gate (`13195`) domains are `postgres_write` / `promoted`** —
`finance_ledger`, `governance`, `identity_core`, `occupancy`, `powerhouse_conversations`, `settings`,
`trust_ledger`, `trust_reconciliation`. This file previously said FOUR and that finance/trust/powerhouse
had no `core.domain_cutover_status` row; the rows were restored on or about 2026-08-28. Every other
building is still Mongo-served. A toggle says a PostgreSQL path EXISTS; the control plane says which
store actually SERVES. Re-run `backend/scripts/audits/cutover_readiness_snapshot.py` rather than
restating this from memory.

**The control plane is no longer the blocker — the routers are.** 10 of 132 router files consult it;
103 call `db.<collection>` directly. 177 of 230 PostgreSQL tables are empty, and 103 populated Mongo
collections have no Postgres target at all (including `unit_levy_ledger`); 25 map to a populated
table and 25 to an empty one. Mongo/PG agree on the unit LEDGER (87 units, $212,146.26 paid,
both sides) but arrears still differs by one lot / $190.00 — never state the first without the second. NOTE the committed
`router_datastore_map.json` is built DB-FREE (CI's `--check` compares DB-free, so commit with `--no-db`)
and under-reports Postgres: 89/18/5/20 committed vs 73/34/10/15 when run against a live database. "Promoted" does NOT mean the
router reads Postgres. New code goes through `services/store_router.py::resolve_store` — see CLAUDE.md
"One Dispatch Seam" and `docs/architecture/postgres_router_cutover_state_and_plan_2026-08-29.md`.

---

## Data / storage rules

1. NEVER query MongoDB without `building_id` — `TenantScopedDatabase` in `backend/database.py` injects it
   automatically, but if you bypass it you get cross-tenant data leaks.
2. NEVER hardcode `"13195"` (East Gate) in new logic. Use the authenticated user's `building_id`.
3. `plan_id` is a legacy alias for `building_id` — never write new code using `plan_id`.
4. `db.settings` (building-scoped) is for levy/financial config. `db.site_settings` (id=`"rate_limits"`)
   is for rate limits only. Mixing them is a silent bug.
5. NEVER bulk-enable `core.feature_toggles` rows without checking `backend/core/toggle_classification.py`.
   `data_source_primary` and `cutover_sensitive` toggles must stay `FALSE` until cutover gates pass.
6. Every record created by a test or perf script MUST include `"is_test_data": True`.
7. Buildings are NEVER hard-deleted — use the soft-archive pattern (`is_archived=True`).
8. Postgres is system of record; which store SERVES a read is per-domain and per-building —
   East Gate is promoted to Postgres, other buildings are still on Mongo. When both exist for a
   domain, dispatch through `domain_cutover_status`/`are_cutover_features_enabled()` — never
   hardcode one store, and fallback is directional (PG attempt → Mongo fallback), never reversed.
   Never assume "Mongo is live" from an older note: verify with the cutover snapshot script.
9. Every ledger-adjacent monetary value is integer CENTS, never float dollars. External-source
   amounts (bank feeds, Demo Bank, OCR, imports) convert to cents at ingestion, once, at the
   adapter boundary — never inside `backend/domain/` (float-banned there, test-enforced) and
   never re-derived ad hoc at each call site. `unit_levy_ledger`/`annual_levies` are a KNOWN
   current violation (stored as dollars) — verify each field, don't assume from a `_cents` name.
10. ARREARS is a per-unit obligation, NEVER netted across units — one owner's overpayment can
    never reduce another owner's arrears. Canonical formula:
    `domain/finance/formulas/arrears.py::unit_arrears_and_credit()`, called only via
    `utils/finance_helpers.py::get_arrears_metrics()`. `net_balance <= 0` = credit (display the
    real amount, never zeroed); `net_balance > 0` = arrears minus only the still-not-yet-overdue
    slice of the currently-charged period — never minus a whole quarter, never prior-year-only.
    Never reconstruct an obligation from `opening_balance + periods_overdue × period_levy` —
    `admin_opening`/`sinking_opening` are life-to-date cumulative, not prior-year-only, and this
    exact pattern produced East Gate's real "31 units / $1,469.49" bug (true: 14 / $11,359.73,
    2026-08-03) plus an earlier UA042 $963.31→$2,768 inflation. Trust `net_balance` directly.
11. COLLECTION RATE, FUND HEALTH, and COLLECTED-IN-ADVANCE are three distinct metrics — never
    computed as one formula, never share a label. (1) Due-date Collection Rate = allocated ÷ due
    as-of-today, no not-yet-due amount on either side, per-unit-clamped like rule 10's arrears
    formula. (2) Fund Health / Full-Year Levy Coverage = `current_year_collection_rate()` in
    `domain/finance/formulas/collection.py` — arithmetic is correct as-is (denominator is the FULL
    annual levy, legitimately includes future instalments) but must NEVER be labelled "Collection
    Rate." (3) Collected in Advance = unapplied credit + receipts for not-yet-due periods, always its
    own surfaced figure. `levied − Σ(signed net_balance)` re-derivations for metric 1 are unsafe —
    they collapse to an unclamped `Σ paid_i` the instant one unit pays ahead. Found live in
    `/finance/kpi-contract`, `/finance/summary`, `/finance/building-overview` (2026-08-03). Metric 1
    now SHIPPED as `domain/finance/formulas/collection.py::due_date_collection_rate()` fed by
    `utils/finance_helpers.py::get_collection_rate_metrics()` — per unit `due_to_date = total_levied`
    (YTD charged-to-date, GAP-FIN-033 B1), `collected_to_date = due_to_date − max(net_balance, 0)`,
    `collected_in_advance = max(−net_balance, 0)`; per-unit-clamped, also per-fund. `/finance/kpi-contract`
    returns it in a `collection_rate` block. Route every "Collection Rate" label through that, NOT
    `fund_health`. `/finance/summary` and `/finance/building-overview` are still on the old unsafe
    pattern — migrate them. See `tasks/GAP-FIN-035-collection-rate-2026-parity-expense-pipeline.md`.
12. Historical reconstruction must be itemized the same way for EVERY year, including current YTD —
    never downgrade one year to a single back-solved reconciliation figure while other years carry
    real itemized payment/receipt records. A live bank-feed/portal-scrape is a verification signal
    against the stored itemized total, never a substitute source for it.
13. Category expense actuals (PDF-imported or live-scraped) must post through the SAME Demo Bank →
    GL pipeline as levy income — never a separate bespoke script reading a different source
    document straight into the GL. East Gate real incident: two disconnected 2021-2025 expense
    totals ($415,031.21 Demo-Bank-staged vs $1,502,451.24 already GL-posted via a hardcoded
    one-off script) diverged 3.6x with neither pipeline ever cross-checking the other.
14. The onboarding financial pipeline is ONE path: sources → Financial Evidence Gateway ("Demo
    Bank", typed evidence) → GL posting → PostgreSQL `finance.*` ledger → read services → UI. The
    portal scrape gives only the CURRENT net per unit (no transaction history), so historical levy
    charges/payments are RECONSTRUCTED (`reconstructed_historical`, superseded later by a real
    feed); the portal is a verification signal, NEVER a journal source. `total_levied` is
    YTD-charged-to-date, not the full annual levy (the "halved!" false alarm — do not chase it).
    Code is building-agnostic; per-Strata-Manager scrapers are input ADAPTERS, not logic. Full
    narrative + gap register + "settled, do not re-litigate" list:
    `docs/architecture/onboarding/04_onboarding_financial_data_flow.md`. Read it before reopening
    "is the onboarding approach right?".

15. **DEMO BANK IS THE ONLY DOOR INTO FINANCE.** Every financial input, without
    exception, must **materialise rows in Demo Bank's own collections** before anything
    downstream sees it. Real feeds (Basiq, Frollo, direct bank, trust account) land as
    actual transactions; anything not already bank-shaped — CSV/PDF import, portal
    scrape, reconstructed history, manual entry, an adjustment — is **converted to a
    bank-like transaction first**, then enters through the same door. Only then does the
    GL pull from Demo Bank and build the ledger per **building → unit → owner**, under
    manual or automated approval.

    Provider integrations are **input ADAPTERS to Demo Bank, not parallel paths into the
    GL**. A Basiq connector's job ends when the transaction exists in
    `demo_bank_transactions`; it never posts to `finance.*` itself, and neither does any
    importer, scraper or one-off script. If a code path can create a financial fact and
    does not go through Demo Bank, that path is the bug — not the reconciliation that
    later disagrees with it.

    This is stricter than "one intake path" in
    `docs/architecture/financial-summary-analysis-of-issues.md`, and it is deliberate:
    that rule said evidence must be typed and authorised, this one names the single
    store every input must physically pass through. Operator decision, 2026-08-27.

    The failure this prevents has already happened twice. East Gate ended up with two
    disconnected 2021-2025 expense totals ($415,031.21 staged in Demo Bank vs
    $1,502,451.24 already posted to the GL by a hardcoded one-off script) that diverged
    3.6x because neither pipeline ever compared itself to the other. And
    `disable_strata_sync_direct_write` was found guarding only the API push endpoint
    while the scraper's own direct-write path had no check at all.

16. **`core.lots` has TWO identifiers and mixing them fails SILENTLY.**
    `lot_number` is the plan lot number (`"79"`); `unit_number` is the addressable unit
    (`"TH079"`). A query filtered on the wrong one returns ZERO ROWS — not an error —
    which reads exactly like "this lot has no owner" or "the restore did not work".
    Anything a user, CSV, levy notice or URL refers to is the **unit** number; use
    `unit_number` unless you specifically mean the plan lot. On 2026-08-27 this produced
    a confident report that six lots had no ownership periods at all; every one of them
    had an owner. **When a lot lookup returns zero rows, check the column before
    concluding the data is missing.** Mongo has the mirror of this: `units.unit_number`
    is the unit, and `unit_number` is stored as a STRING that sorts lexicographically
    (footgun #6).

---

## Secrets rules

1. Live secrets live ONLY in `backend/.env` / `frontend/.env.local` (gitignored) and the deploy
   secret store. Never in a committed file, a doc, an inline shell command, or a
   `.claude/settings*.json` permission rule.
2. `*.env.example` files are TEMPLATES — every value a placeholder. Never create one by copying a
   live `.env`. Reference style: `deploy/env/backend.env.example` (`<PASSWORD>`, `<YOUR_DOMAIN>`).
3. Never bake a credential into a permission rule (`Bash(PGPASSWORD=<real> psql *)`) — it persists
   in plaintext and buys nothing; the generic `Bash(psql *)` / `Bash(mongosh *)` rules already cover
   those commands. Export the credential in the shell instead.
4. Anything ever committed is compromised — rotate it; deleting it from the working tree does not
   clear git history. `ENCRYPTION_KEY` is the exception to a plain rotation: it is the Fernet key
   for PII at rest, so changing it requires re-encrypting existing data.
5. Real incident (2026-08-26): `backend/.env.example` was a byte-identical copy of production
   `.env` for 51/58 keys (JWT_SECRET, ENCRYPTION_KEY, CRON_SECRET, Mongo + Postgres creds, Stripe,
   Migadu, Mindee, Serper, reCAPTCHA, Anthropic key), committed and pushed; nine
   `.claude/settings.local.json` rules held the same DB credentials in plaintext.

---

## Code rules

1. ALWAYS use `_effective_role(user)` (or inline equivalent) for role guards — never `user["role"]` alone.
   Raw role is `"owner"` for elevated users, causing silent 403s.
2. `chairman` is NOT a top-level role — it is `ECPosition.CHAIRMAN` on a user whose `role` is `ec_member`.
   Do not use `UserRole.CHAIRMAN` (it doesn't exist) or the string `"chairman"` in role guards.
3. Use `UserRole.*` constants from `models/user.py`, not bare string literals in role-guard contexts.
4. Frontend Axios calls from `useAuth()` target `${NEXT_PUBLIC_BACKEND_URL}/api` — NEVER prefix with `/api/`.
5. Every statement in `frontend/src/auth.ts` jwt/session callbacks MUST end with a semicolon — the minifier
   breaks silently without them, causing login failure in production builds.
6. Auth loading guard: check `if (loading) return` BEFORE any `isAdmin()` / `isManager()` guard in
   client components, or the guard fires before session resolves and redirects every user.
7. Do not call integration providers directly — always go through `backend/integrations/registry.py`.
8. All new SQLAlchemy ORM models for Postgres MUST inherit from `backend/db_postgres/base.py:Base`.

---

## Known footguns

1. Router import errors are silently swallowed in `server.py` — a syntax error in a router file will NOT
   crash the server; the endpoint just returns 404. Check logs first.
2. `.to_list(200)` on `financial_forecasts` is wrong — the collection exceeds 200 docs. Use `aggregate`.
3. `get_latest_levy_year()` returns a **string**, not int. Cast before numeric comparison.
4. `asyncio.gather()` requires all coroutines to be `AsyncMock` in tests — a plain `MagicMock` inside
   gather raises `TypeError` silently.
5. `$setOnInsert` upserts are permanent on the unique key — validate input BEFORE the upsert, not after.
6. MongoDB sorts `unit_number` lexicographically — always post-sort in Python for user-facing display.
7. Postgres RLS: `core.lots` has NO bypass clause. `SET app.tenant_id = '00000000-...'` does NOT let you
   delete lots — switch to the actual tenant_id first, delete lots, then switch back for schemes/tenants.
8. Any raw DB connection that never runs `SET app.tenant_id = '<uuid>'` gets silent `count(*) = 0` on
   RLS-protected tables — not an error. Do not conclude data is missing/deleted from a bare `count(*)`;
   check `pg_stat_user_tables.n_live_tup` or set tenant context first. The bypass sentinel
   `'00000000-0000-0000-0000-000000000000'` only unlocks tables with an explicit bypass clause
   (`core.schemes`, `core.tenants`, `core.users`, `core.user_invitations`). `core.feature_toggle_overrides`
   and `core.building_settings` have NO bypass clause (strict `tenant_id = current_tenant_id()` from
   migration `0012`) — under the sentinel they return 0 rows even when populated, so any script reading
   per-building toggle overrides must set the REAL tenant UUID (real 2026-08-11 incident: a snapshot
   reported "0 overrides" while 13 rows existed).
9b. A toggle/flag meant to gate a write (`disable_strata_sync_direct_write`,
    `disable_auto_allocation`, etc.) must be checked at EVERY code path capable of that write, not
    just the primary entry point — `disable_strata_sync_direct_write` was found guarding only the
    API push-endpoint while the scraper's own direct-write path had zero check (2026-08-03).
9. **Every Alembic `revision` string MUST be ≤32 characters** — `core.alembic_version.version_num` is
   `VARCHAR(32)` (set in `env.py`'s target schema). A longer revision ID applies its DDL successfully
   and only fails on the final `UPDATE core.alembic_version SET version_num=...` step
   (`StringDataRightTruncationError`) — Postgres then rolls back the ENTIRE migration transaction
   (DDL included), so this fails safe, not silently-partial, but it still wastes a deploy attempt and
   produces a confusing error far from its root cause. Before writing a new migration file: count the
   `revision = "..."` string (and match the filename to it, e.g. `0076_levy_charge_uniqueness.py`, not
   a verbose multi-word description) — keep it short (a 4-digit sequence number + a few underscored
   words, under 32 chars total), never a full sentence. Real incident: `0076_historical_levy_run_fund_uniqueness`
   (41 chars) failed this way 2026-07-31; renamed to `0076_levy_charge_uniqueness` (27 chars) and it
   applied cleanly. Verify with `python3 -c "print(len('your_revision_string'))"` before running
   `alembic upgrade head`.
11. `core.users`' bypass clause (footgun #8) only fires when the session is actually under the
    `00000000-…` sentinel — it does NOT help resolve a cross-tenant actor while the session is set to
    a real, specific tenant (e.g. after correctly switching to a building's own tenant to read
    `core.feature_toggle_overrides`/`core.building_settings` per footgun #8). Under a real tenant
    context, `core.users`' policies both evaluate to "deny" for any row belonging to a *different*
    tenant — including a super_admin's own user row, since super_admins live in the platform tenant,
    not the building's tenant. A report that resolves an actor-UUID column (e.g. `set_by`) to a
    human-readable name by joining `core.users` under that same real-tenant session will silently
    drop every cross-tenant actor from the join — indistinguishable from the UUID column itself being
    NULL, but it isn't. Resolve actor identity via a SEPARATE `core.users` lookup under the bypass
    sentinel, never in the same query/session as the row-owning tenant's context (see
    `backend/scripts/data_repair/backfill_feature_toggle_override_set_by.py::_resolve_actor_uuid` for
    the correct pattern). Real incident: a 2026-08-11 go-live readiness review reported "8 of 13
    `feature_toggle_overrides` rows have `set_by = NULL`" this way; `set_by` is `NOT NULL` at the
    schema level and always has been (migration `0011`) — all 8 had real, non-null values pointing to
    two super_admins whose own `core.users` rows were invisible under East Gate's tenant context.
    Corrected 2026-08-12; see `docs/architecture/go_live_readiness_2026-09-01.md` §0c.

12. **Never probe a live mutation endpoint with a real person's data.** `/auth/register`
    has a "claim" branch: posting an existing owner's email + unit does NOT 409, it takes
    over the account and sets a password. A 2026-08-27 diagnostic did exactly this to two
    real East Gate owners (one real name overwritten with "Test Person"). Read the branch
    logic, or probe with an address that cannot belong to anyone
    (`probe+x@example.invalid`).
13. **A `building_id`-filtered sweep cannot support a platform-wide "nothing remains"
    claim** — global collections (`email_sent_log`, `login_audit_logs`, `audit_logs`,
    `core.trial_requests`) carry no `building_id` and are skipped entirely. On the
    Postgres side, filtering `information_schema.columns` by
    `data_type IN ('text','character varying')` silently skips `core.users.email`, because
    **`citext` reports as `USER-DEFINED`** — match `udt_name` as well. Both mistakes were
    made on 2026-08-27 and both produced a confident, wrong "clean" result.
14. **`.get(key, default)` does not default a key that exists and holds `None`.** A live
    HTTP 500 on `/owner-finance/health-explanation` came from
    `summary.get("health_score", 0)` returning `None`, then `None >= 85`. Guard with `or`
    or an explicit `is None` branch — and for a score, "unmeasured" must render as `None` /
    "N/A", never as `0`, which grades a building "D" on data nobody has.
15. **The API error envelope is global.** `backend/utils/error_response.py` runs as
    `@app.exception_handler(HTTPException)` and rewraps every structured `detail` dict into
    `{error: {code, message, metadata}}`. Frontend code reading `data.detail.code` gets
    `undefined` and silently degrades to a generic message. Read errors via
    `getApiErrorDetail()` in `frontend/src/lib/api-error.ts` (it accepts both shapes). ~12
    files still read the old shape, incl. `frontend/src/auth.ts`'s login pending-approval.
16. **Never derive a `tenant_id`; resolve it from `core.schemes`.** Registration used
    `uuid5(NAMESPACE_DNS, f"building-{building_id}")`, which is NOT the real tenant, so
    `users_tenant_id_fkey` rejected every insert and a `try/except` swallowed it — no
    registration ever created a `core.users` row. A `try/except` that logs and continues
    converts a hard failure into a permanent silent one; if a write must happen, assert it.
17. **`core.domain_cutover_status` missing rows default to MONGO for that domain**
    (documented in `services/domain_source_guard.py`). Emptying that table silently
    de-promotes every domain. Restore a domain's row only alongside the data it routes to.
    Beware the asymmetry: **login is unconditionally Postgres-first with a Mongo fallback**
    and never consults the table, so auth can look healthy while every
    `require_domain_source` consumer has quietly reverted to Mongo.

---

18. **`core.ownership_periods` and `core.user_units` both answer "who owns this lot", and
    the user list reads the SECOND one.** `list_active_users_for_scheme` (behind GET /users
    for a promoted building) resolves membership from `core.user_units` OR
    `core.user_role_assignments` — never from `ownership_periods`. Until 2026-08-27 no code
    path closed a `user_units` link on transfer, so sellers stayed listed as current owners
    forever while `ownership_periods` was correct (East Gate TH078). Fixed by
    `server.py::_sync_postgres_user_units_for_transfer`. Because membership is link OR role
    assignment, closing the link alone is NOT enough — retire the resident
    (`owner`/`tenant`/`guest`) role assignment with it, and never the elevated ones (EC seat,
    manager, admin staff), which are standing appointments.

19. **Identity is the `core.parties` link, never a name or email string.** Two accounts with
    the same `party_id` are one person (merge; survivor = holds a role assignment → has
    login history → oldest). Two accounts sharing an EMAIL may be two different people —
    co-owner pairs share a household mailbox (documented 2026-08-19; four such pairs exist
    in East Gate and deduplicating by email would delete four real owners). A `full_name`
    that disagrees with its own party's `legal_name` means the NAME is wrong, not the party:
    a transfer repair wrote the buyer's name over the seller's account (TH078).

20. **`is_test_data` defends nothing unless something sets it.** The conftest sweep and the
    `APP_ENV=production` login gate both key off the flag, so an UNFLAGGED test row is
    invisible to both — a live credential, not clutter. A test that mocks a handler's Mongo
    collections but not its Postgres writes creates exactly that: `test_multi_unit_ownership.py`
    left three active password-bearing rows in East Gate's production tenant (2026-08-27).
    `test_no_unflagged_test_users.py` cannot catch it (it scans `tests/` for direct writes;
    here the CODE UNDER TEST writes). Backstop: `identity_repo.create_user` /
    `create_user_for_registration` OR in `_under_pytest()`. When mocking a handler, mock
    EVERY store it writes to.

21. **A repair touching Mongo and Postgres has no shared transaction — order the writes.**
    Collect Mongo writes and replay them only AFTER `session.commit()`; mirroring inside the
    open transaction leaves Mongo holding what a rollback erased. The durable form is the
    outbox (`core.outbox` + `workers/outbox_relay.py`). Also: `asyncpg` encodes a DATE bind
    param via `date.toordinal()` — pass a real `datetime.date`, never an ISO string, and
    `CAST(:d AS DATE)` will not rescue one.

22. **Suppress service accounts by their reserved LOCAL PART (`system-`), not their domain.**
    A 2026-08-26 bulk email-neutralisation rewrote every address onto the building domain with
    no service-account exemption, so the finance cutover actor left
    `@system.strataos.local` and surfaced in /admin/users as a strata manager. Any bulk
    rewrite needs an exemption list for non-human identities. To recover such an address,
    PROVE it: `genesis.py` derives the user_id as a uuid5 over the email, so only a candidate
    that reproduces the row's actual user_id is correct.

23. **`core.users.status` is `core.record_status` (`draft|active|inactive|archived`).**
    There is no `core.user_status`; casting to it raised inside `update_user_profile`'s
    catch-all, which logged "non-fatal" and returned False — every status change was
    Mongo-only, so archiving a user never removed them from a promoted building's list.
    A `try/except` that logs and continues needs a test asserting the write landed.

24. **Mongo and Postgres rows for the same person can have DIFFERENT ids.** A mirror keyed
    only on `{"id": pg_user_id}` silently matches nothing and `update_one` still reports
    success — `tenant@`/`guest@` stayed active in Mongo after being archived in Postgres
    (2026-08-27). Match on `id` OR `email` (email is the only shared identifier), then
    resolve the Mongo row's own `id` before updating `user_units`/`memberships`, which are
    keyed by it. Assert the post-condition; "no exception" is not "changed a row".
    Note many restored accounts are Postgres-ONLY, where a no-op mirror is correct — so a
    zero match is not by itself evidence of a bug either. Check which case you are in.

25. **A portal/source snapshot beats BOTH stores — and an unallocated receipt is
    CREDIT, not a fabrication.** On 2026-08-28 the operator's strata-portal position
    (87 lots, life-to-date) overturned an adjudication built by comparing PostgreSQL
    against MongoDB. Two derived stores cannot tell you which is right and **both can
    be wrong**: portal said 11 lots / $4,195.40 owing + $35,675.42 credit; PG said
    14 / $8,041.30 with no credit concept in `levy_items`; Mongo said 13 / $7,851.30.
    Neither matched. Never adjudicate a store-vs-store diff without the source document.
    Two specific traps from that incident: **`bank_transaction_id IS NULL` proves
    nothing** — 2,232 of East Gate's 2,233 receipts have it null, including every
    legitimate reconstructed-historical one, because 2021-2025 had no feed to link to;
    and **"never allocated" is not evidence of a manufactured payment** — an unallocated
    receipt is precisely how unapplied credit (paid beyond what has been levied) is
    represented. Acting on those two signals retired 14 real credit receipts and had to
    be rolled back the same day. To identify a genuine back-solve, check DECLARED
    provenance plus whether the amount restates the lot's own lifetime
    `levy_items.paid_cents` (13 of 17 did, 7 to the cent).

26. **A scrape alone changes nothing — the chain has three steps and only the first is
    automatic.** `run_scraper.py` writes `strata_owners`/`bank_accounts`/
    `building_summaries` but does **NOT** stage `staging_strata_web_snapshots` (that
    happens only via `POST /settings/strata-web-portal/sync`), and the delta inference
    that turns two snapshots into Demo Bank candidates is always manual — its own
    endpoint says "not wired into any scheduler". So a scrape can leave fresh data in
    Mongo and produce ZERO candidates with nothing raising. Run
    `backend/scripts/ingest/strata_web_post_scrape_pipeline.py` after any scrape; a
    balance delta needs TWO snapshots of the same NORMALISED financial year
    (`utils.finance_helpers.normalise_financial_year` — the collection stores whatever
    label the caller passed, and East Gate holds "2025", "2026" and "2026-2027").


## Capability index — one concept, one owner

**Before writing a shared helper, check `docs/architecture/canonical_owners.yaml`.
If the concept has an owner, call it. If it does not and the concept will be
reused, add an entry in the same PR.** This is one consolidated policy registry
for backend Python and frontend TypeScript/JavaScript; do not create a second
frontend registry.

`python3 scripts/validation/generate_canonical_owner_registry.py --check`

FeatureTrace, the mindmap and any call graph answer *what calls what*. **None can
answer whether a concept already has an owner** — a re-implementation creates no
edge to the original, so every map renders it as healthy new code. That is not a
coverage gap; uniqueness is not derivable from reachability. lot->unit resolution
was rebuilt FIVE times this way, and two functions named `dollars_to_cents` shipped
side by side returning different money (`"10.005"` -> rejected by one, `1001` from
the other).

`known_violations` in that file is debt being paid down, **not an escape hatch** —
fix a failing check by calling the owner, never by appending to the list. An entry
whose detector cannot separate correct from incorrect use carries `detect: null`
deliberately (see `per-unit-arrears`); a check that cries wolf gets allowlisted
into uselessness. Enforced by `tests/backend/test_canonical_owners.py` and
`scripts/validation/generate_canonical_owner_registry.py --check` in deploy
preflight, with per-language debt ceilings. Full rationale:
`tasks/P0-CANONICAL-OWNER-REGISTRY.md`.

---

## Verification rules

1. NEVER mark a task complete without running the relevant test suite (`make test` or scoped subset).
2. A phase is not done until code, tech docs (`frontend/public/tech-docs/`), and architecture docs
   (`docs/architecture/`) all reflect the shipped implementation.
3. After any feature work, regenerate FeatureTrace maps:
   `python3 scripts/validation/generate_featuretrace_map.py <tag> --format all --out docs/architecture/mindmap/featuretrace/`
4. NEVER treat one store's (Mongo or Postgres) stored/computed financial value as ground truth when
   reconciling two implementations of the same metric — both were derived from CSV/PDF imports and
   can independently drift. Before citing a test as justifying a formula "fix," verify which code
   path it actually exercises (not just its docstring). Before changing any financial formula,
   search the whole `docs/` tree for other documents describing the same field — a disagreement may
   mean two legitimately distinct, separately-documented rate families, not a bug. "Live-verified"
   means a regression check (new code == old code given the same input), never proof the stored
   data matches the original source documents.
