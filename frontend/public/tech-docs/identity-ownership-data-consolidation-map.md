# Identity & Ownership Data Consolidation Map — Page → API → Service → Database

**Generated:** 2026-08-05
**Scope:** the **non-financial identity & ownership** dashboard cluster (resident
directory, strata roll, user management, EC membership, profile/passport,
occupancy) — the first domain traced under the "same method, rest of the app"
programme kicked off in
`docs/SESSION-HANDOFF-2026-08-05-financial-audit-and-next-phase.md` Part E.
**Method:** the exact evidence bar used in
`docs/finances/financial-data-consolidation-map.md` — trace each page to the
endpoint(s) it calls, the router/`server.py` handler, the service, and the
actual store (MongoDB collection vs PostgreSQL `schema.table`), classify with
import/call-site evidence, **guess nothing, fix nothing.**

> **See also:** `docs/finances/financial-data-consolidation-map.md` (the finance
> sibling of this document — the *financial* view of ownership, i.e. owner-hub,
> is traced there and tracked under `GAP-FIN-051`, not here) and
> `docs/architecture/database_live_inventory_2026-08-03.md` (the live row-count
> source every count below is cited from).

---

## 0. The one-paragraph headline

**Identity & ownership is the first domain in this app that is *promoted* — for
East Gate (`13195`), `core.domain_cutover_status.identity_core` is
`postgres_write` / `readiness_status=promoted` (verified live 2026-07-24 and
2026-08-02; see `GAP-CUTOVER-001`).** That single fact changes what a
"Mongo-only page" *means* here versus in finance. In finance, every domain is
still `mongo_primary`/`postgres_shadow`, so a Mongo-only page is a harmless
**dark hole** (Postgres just isn't consulted). In identity, the promoted read
path (`/auth/me`, `owner_service.py`, `owner_read_service.py`) now genuinely
resolves owner/role/membership data from `core.lots` / `core.parties` /
`core.ownership_periods` / `core.user_role_assignments`. The question this
document set out to answer is whether the legacy per-unit write UIs keep those
promoted tables in sync.

> **Empirical answer (live diff, 2026-08-05, East Gate `13195` — see §2a):**
> **owner data has 0 drift (87/87 units), and user roles have 0 mismatch on
> every shared user.** The routine owner-edit path is sound *by design* — the
> Strata Roll blocks owner-identity edits and delegates them to the
> ownership-transfer workflow, which dual-writes Postgres. So the "write
> split-brain" is **narrower and currently latent**, not the broad live bug an
> earlier draft of this doc claimed. The genuinely un-propagated writes that
> remain are: **(1)** `PUT /users/{id}` role/`ec_position` edits (Mongo-only —
> latent only because East Gate's roles are stable), and **(2)** the rare manual
> `create_owner_unit`/`delete_owner_unit` paths. Postgres is also the **more
> complete** store here (95 real owners it holds that the Mongo building-scoped
> user query does not) — which makes it the correct primary, not the risky one.

---

## 1. What is genuinely on Postgres already (the part that works)

Unlike finance, identity/ownership has real, governed, *live* Postgres paths.
Cataloguing them first is important so the split-brain finding in §4 is read as
"the write UIs never caught up," not "Postgres isn't ready."

| Live PG path | Where | Store | Evidence |
|---|---|---|---|
| **Login / session / building-switch** reads | `routers/auth.py` (login `:1620-1756`), `utils/auth.py` (`get_current_user`, `get_current_building`, 10 call sites) | `core.users`, `core.user_role_assignments`, `core.schemes` (Mongo fallback only if no `tenant_id` claim) | `docs/migration/identity_auth_pg_call_audit.md` — PG-first "for a majority of real sessions … for over two months," tracked as `GAP-IDENTITY-LOGIN-001` |
| **Canonical owner resolution** (governed dual-path — the *reference* pattern) | `services/owner_service.py` → `services/owner_read_service.py` | `core.lots` + `core.parties` + `core.ownership_periods` + `core.users`, gated by `OWNER_READ_PG_ENABLED` **and** `require_domain_source(domain="identity_core", operation="read")` | `owner_service.py:65-93`; `owner_read_service.py:25-26,209-212` |
| **Occupancy** reads (positive example) | `routers/occupancy.py` | `analytics.fact_occupancy_snapshot` (PG, ~174 rows) via `async_session_context` | `occupancy.py:35,134-155,199-212` |
| **All back-office identity *writes*** — new schemes, invitations, org/agency hierarchy, joint-owner review, access devices, onboarding lots/parties/ownership | `management_hierarchy.py`, `sm_organisations.py`, `admin_invitations.py`, `joint_owner_review.py`, `access_lifecycle.py`, `onboarding.py` | `core.*` (tenants/schemes/lots/parties/ownership_periods/user_invitations/user_role_assignments), `access.*` | traced router-by-router in §3 |

**The domain is bifurcated:** the *new-building / back-office* half writes
Postgres `core.*` authoritatively; the *legacy per-unit dashboard CRUD* half
still writes MongoDB. Both feed one promoted domain. That seam is where §4 lives.

---

### 2a. Empirical diff result (live, read-only, 2026-08-05)

Run: `backend/scripts/audits/identity_mongo_pg_diff.py --building-id 13195`
(read-only; no writes). Result for East Gate:

| Compare | Compared | Field mismatches | Only in Mongo | Only in Postgres |
|---|---|---|---|---|
| **Owners** (`units.owner_*` vs `core.ownership_periods`) | 87 | **0** | 0 | 0 |
| **User roles** (`users.role` vs `core.user_role_assignments`) | 14 | **0** | 2 (both dead **test accounts**, intentionally not migrated) | **95** (real owners PG holds that the Mongo building-scoped `users` query does not) |

> **Provenance of the 0 (reconcile with the ticket's "Session note"):** this
> 0-owner-drift is the *post-repair* state. A concurrent session (2026-08-05,
> commit `62b6e63`) found and fixed 6 owner-email mismatches — a
> primary/secondary swap left by an earlier repair — via
> `backend/scripts/data_repair/sync_identity_mongo_to_pg_20260805.py`, and
> created PG identities for 2 of the 4 then-Mongo-only users. So the owner path
> did accrue drift historically; "sound by design" means it won't *re-accrue*
> owner-name/email drift going forward (edits are blocked/delegated), not that it
> never diverged. The 2 remaining Mongo-only accounts are the intentional
> exclusions (a `merged_alias` and an archived account).

**Reading:** identity_core's promoted PG store is now **consistent with, and a
superset of,** Mongo for East Gate. There is **no realized divergence
remaining** to repair before cutover — the ongoing split-brain (§4 category (d))
is latent for the un-delegated write paths. The
remaining work to make Mongo droppable for identity is (1) propagate future
`PUT /users/{id}` role edits to `core.user_role_assignments`, and (2) move
`GET /users` from a Mongo∪PG union to PG-primary (which cleanly drops the 2 test
accounts and keeps all 109 real users). **Re-run the diff per active building**
before a global identity cutover — 0-drift is verified for `13195` only.

## 2. Full page inventory (identity & ownership cluster)

`Verdict` uses this document's taxonomy (§4): **(a)** PG schema exists but
empty; **(b)** PG schema populated + promoted but the page reads Mongo;
**(c)** no PG equivalent exists; **(d)** **write split-brain** — page writes
Mongo, promoted PG read path serves `core.*`, no propagation. A page can carry
more than one.

| Page (route) | Component | Endpoints (verbatim) | Handler | Store (actual) | Verdict |
|---|---|---|---|---|---|
| `/admin/owners-units` (**Strata Roll**) | `OwnersUnitsPage.jsx` | `GET/PUT/DELETE /owners-units[/{unit}]`, `POST /owners-units/import-from-pdf`, `GET /building/assets/strata-roll/pdf` | `server.py` `create_owner_unit` (`:14549`), `update_owner_unit` (`:15705`), `delete_owner_unit` (`:15870`) | **Mongo `db.units`** for unit metadata. **Owner identity edits are BLOCKED here** (`update_owner_unit:15722-15731` rejects `owner_name`/`owner_email` and routes them to the ownership-transfer workflow, which **does** dual-write PG via `_write_postgres_ownership_period`, `server.py:11036`). | **CORRECTED (2026-08-05, empirical).** The routine owner-edit path is **sound by design** — the live diff confirms **0 owner drift, 87/87 units** (§2a below). Residual (d) is narrow: only the rare **manual `create_owner_unit`/`delete_owner_unit`** edge paths write Mongo `db.units` without a `core.*` counterpart. (An earlier draft over-labelled the whole PUT path "highest severity split-brain" — that was wrong: it missed the identity-edit rejection guard.) |
| `/admin/users` (**User & role management**) | `UsersPage.jsx` | `GET /users`, `PUT /users/{id}` (role / `ec_position` / `is_active` / `is_approved` / edit), `POST /users/{id}/elevate`, `DELETE .../elevate`, `POST .../archive`, `.../owner-decision`, `.../request-info`, `.../request-profile-info` | `server.py` `get_users` (`:2480`, the **union** route), `update_user` (`:2773`), elevate (`:3364/:3413`), archive (`:3922`) | **READ:** Mongo+PG **union** (`identity_repo.list_active_users_for_scheme`). **WRITE:** Mongo `db.users`/`db.user_roles`; PG (`find_user_by_id_for_admin`, `:2855`) used **only** for an IP-protection lookup | READ tracked by **`GAP-IDENTITY-USERS-LIST-001`**. WRITE = **(d)** — role/`ec_position`/status edits are Mongo-only; promoted `core.user_role_assignments` (~115 rows) not updated. The union READ then "lets Mongo win silently" (that ticket §3), masking the divergence. |
| `/community/directory` (**Resident directory**) | `ResidentDirectoryPage.tsx` | `GET /directory`, `PUT /directory/settings`, `POST /conversations` | `server.py` `:12103` (GET), `:12194` (PUT) — **live**. `community.py:477/573` is the **DEAD** duplicate (router not wired, per root `CLAUDE.md`) | Mongo (directory composed from `units`/`users`/`user_units`) | **(b)** read of identity data whose promoted PG source (`core.parties`/`core.lots`) is unused. **Latent hazard:** a second, dead `/directory` handler exists in `community.py` — do not wire it without reconciling. |
| `/profile` | `ProfilePage.tsx` (+ `AuthContext`) | `GET /units`, `GET /change-requests/me/pending`, `POST /auth/change-password`, `PUT /users/{id}` (via `updateProfile`), `POST /auth/email-preference`, `GET /analytics/tax-summary/{unit}` | `server.py` `update_user` (`:2773`) via context; `/auth/*` | Mongo write (same `update_user` path as `/admin/users`); `/auth/me` reads PG | **(d)** self-service edits land in Mongo; promoted PG read path serves `core.*`. Same split as `/admin/users`, owner-facing. |
| `/governance/ec-members` | `ECDashboard.jsx` | `GET /stats/building-kpis`, `/finance/summary`, `/analytics/*`, `/agm`, `/announcements`, `/documents/important` | analytics/finance handlers | Mongo (analytics aggregates) | **Not a roster CRUD page** — it is a committee *dashboard*. The EC-membership *fact* is edited via `/admin/users` (`PUT /users/{id}` `ec_position`) → **(d)** (written Mongo, read from PG `core.user_role_assignments.ec_position` by `/auth/me`). |
| `/profile/passport` | self-contained `page.tsx` | `GET /residency/my-passport`, `GET /residency/my-passport/download` | `residency.py:20,53` | Mongo (`units`, `unit_levy_ledger`, `workflow_requests`, `volunteer_events`); PDF/token = no DB | **(b)** reads `units` (promoted PG `core.lots` source unused) **+ (c)** the residency *score* itself has no PG equivalent. |
| `/intelligence/occupancy` | `OccupancyIntelligencePage.jsx` | `GET /occupancy/summary\|lots\|trends`, `POST /occupancy/recompute` | `occupancy.py` | **PG `analytics.fact_occupancy_snapshot`** (~174) via `async_session_context` + some Mongo | **MIXED — positive example.** Reads the populated PG snapshot. But `core.tenancy_periods` (the transactional tenancy table) is **empty (0 rows)** → the snapshot is analytics-derived, not from a live tenancy ledger → **(a)** underneath. |
| `/owner-hub/classic` (legacy, still wired) | `OwnerDashboard.tsx` | `/finance/unit-dashboard-overview`, `/owner-hub/unit-tco`, many `/analytics/*` | owner-hub + analytics | Mongo | CONFIRMED MONGO — overlaps **`GAP-FIN-051`** (owner-hub). Legacy "Classic Owner View," reachable — flag for a deprecation decision, out of scope here. |

---

## 3. Router-level DB source evidence (identity/ownership routers)

Traced **through** the services/repos each router imports, not just the router
file. Counts are distinct-collection/table hits with a cited call site.

### 3a. Postgres-authoritative writers (the back-office half)

| Router | Verdict | Postgres `core.*` / other | Mongo |
|---|---|---|---|
| `management_hierarchy.py` | POSTGRES-ONLY | `management_entities`, `scheme_management_assignments`, `scheme_manager_appointments`, `schemes`, `agencies`, `agency_memberships`, `outbox` (via `management_hierarchy_service.py`, `async_session_context`) | none |
| `sm_organisations.py` | POSTGRES-ONLY (self-declared) | `tenants`, `schemes`, `user_invitations`, `user_role_assignments`, `users` (raw SQL, `:229-758`) | none |
| `admin_invitations.py` | POSTGRES-ONLY (self-declared) | `user_invitations`, `users`, `user_role_assignments` (via `identity_repo`) | none |
| `joint_owner_review.py` | POSTGRES-ONLY | `joint_owner_review`, `ownership_periods`, `lots`, `parties`, `outbox` (via `joint_owner_service.py`) | none |
| `access_lifecycle.py` | POSTGRES-ONLY | `access.*` (7 tables), `ops.cases` (gated by `access_device_lifecycle_pg_enabled`) | none |
| `onboarding.py` | **MIXED** | `tenants`, `schemes`, `lots`, `parties`, `ownership_periods`, `onboarding_sessions` (identity is PG-authoritative) | `historical_*` staging collections (financial CSV import only, write-only, ADR-022 isolated-staging — not read by dashboards) |

### 3b. Mongo-authoritative (the legacy per-unit half)

| Router | Verdict | Mongo collections | Postgres |
|---|---|---|---|
| `organisations.py` | MONGO-ONLY | `organisations`, `organisation_members`, `organisation_buildings`, `buildings` | none |
| `scheme_classes.py` | MONGO-ONLY | `units`, `scheme_classes`, `scheme_class_history`, `class_category_allocations`, `levy_categories` (service `scheme_levy_service.py` is pure compute) | none |
| `rbac.py` | MONGO-ONLY | `permissions`, `roles`, `user_roles`, `users`, `relationship_tuples`, `role_permissions`, `permissions_cache`, `memberships` (via `permission_service.py`, `authorization_engine.py`) | none — RBAC storage is entirely Mongo despite being the identity core |
| `residency.py` | MONGO-ONLY | `units`, `users`, `unit_levy_ledger`, `workflow_requests`, `volunteer_events` | none |
| `staff_management.py` | MONGO-ONLY | `users`, `memberships`, `user_roles`, `buildings`, `roles` | none — comments (`:434,:502`) mark PG `identity_repo.update_user_role()` as a **future "Phase G"** task |
| `users.py` | (DEAD CODE — never wired; live `/users` is in `server.py`) | — | — |

> **`server.py` note:** the *live* per-unit identity CRUD (`/owners-units`,
> `/users`, `/directory`) lives in `server.py`, not in the `routers/` files —
> which is why the grep-by-router pass alone under-counts the split-brain.
> `server.py`'s owner/user CRUD writes Mongo; its `/auth/*` and the owner-transfer
> **approval** path are the only server.py identity code that touches `core.*`.

---

## 4. The classification taxonomy — and why category (d) is new

The finance map used three dark-hole categories. Identity needs a fourth,
because identity is the first **promoted** domain:

| Cat | Meaning | Remediation cost | Identity examples |
|---|---|---|---|
| (a) | PG schema exists, **empty**, no write path | schema built ahead of data — needs a writer | `core.tenancy_periods` (0), `core.party_roles` (0), and the entire empty management/agency tier (`agencies`, `management_entities`, `strata_manager_profiles`, … all 0) |
| (b) | PG schema **populated + promoted**, page reads Mongo | service rewire onto governed read | `directory`, `my-passport` read `units` while `core.lots`/`core.parties` (87/252) sit promoted-and-unused |
| (c) | No PG schema equivalent | product/schema decision | residency score; EC analytics aggregates |
| **(d)** | **WRITE SPLIT-BRAIN** — page writes Mongo; promoted PG read path serves `core.*`; **no propagation** | **dual-write or route the write through the promoted store** | **`owners-units` CRUD; `users` role/status edits; `profile` self-edits; staff role assignments** |

**Why (d) is the severe one.** In finance, a Mongo-only page is invisible to
Postgres because Postgres is never read (`mongo_primary`). In identity,
Postgres **is** read live (`identity_core` is `postgres_write`), so a Mongo-only
**write** doesn't vanish — it creates a durable disagreement: an admin changes an
owner on the Strata Roll (Mongo `db.units`), and `/auth/me` / `owner_read_service`
keep serving the *previous* owner from `core.ownership_periods` until the next
bulk backfill. The cutover control plane already hints at this: `core.lots` "has
had no writes since the 2026-05-04 bulk seed (no live sync path yet) — an
explicit, informed risk acceptance, not a data-integrity guarantee"
(`cutover_status_service.py:1081-1086`), and `_write_postgres_ownership_period`
is explicitly labelled "Phase G prep … non-fatal" (`server.py:10531-10538`).

**Scope of the divergence today (static-source view — needs live confirmation):**
category (d) is bounded by "which identity facts get written by a legacy Mongo
UI but read from promoted PG." Confirmed write paths that do **not** propagate:
owner name/email/unit on the Strata Roll; user role / `ec_position` / active /
approved / archive status; profile self-edits. Confirmed write paths that **do**
propagate: the formal owner-transfer *approval* workflow (dual-write) and all the
§3a back-office writers. Everything else is unverified from static sources — a
per-field live diff (Mongo `units`/`users` vs `core.parties`/`ownership_periods`/
`user_role_assignments` for `13195`) is the correct first evidence step, **not** a
rewire.

---

## 5. Cross-references — what is already tracked (do not rediscover)

| Existing ticket | Covers | This document adds |
|---|---|---|
| `GAP-CUTOVER-001` | identity_core promoted to `postgres_write` for East Gate | the **consumer-side** consequence: legacy write UIs never caught up to the promotion |
| `GAP-IDENTITY-USERS-LIST-001` | `GET /users` Mongo+PG **union** *read*, `merge_conflict_count=0` stub | the *write* side — why the union sees divergence at all (Mongo-only role writes) |
| `GAP-IDENTITY-LOGIN-001` | unmanaged PG-first *auth read* | context for why PG reads are already live (so a Mongo-only write now diverges) |
| `GAP-FIN-051` | owner-hub (the *financial* view of ownership) | the *non-financial* identity/ownership pages the finance map left out |
| `GAP-ONBOARD-002` | onboarding levy-plan endpoint/UI parity | onboarding's identity writes (`core.lots/parties/ownership_periods`) as the PG-authoritative counter-example |

---

## 6. Recommended follow-up (report, not action — sign-off required per programme rule)

1. **Confirm the divergence with a live read-only diff** before any rewire: for
   `13195`, compare Mongo `units.owner_name/owner_email` and `users.role/ec_position`
   against `core.parties` / `core.ownership_periods` / `core.user_role_assignments`.
   If they already disagree, category (d) is a *current* production bug, not a
   latent one. (Same discipline as `financial-data-consolidation-map.md` §4 —
   diff first, fix as a scoped follow-up.)
2. **The fix shape is dual-write, not a new read path** — route `owners-units`
   and `users` writes through the promoted store (`ownership_repo` /
   `identity_repo.add_role_assignment`), mirroring the owner-transfer approval
   flow's `_write_postgres_ownership_period`, so the promoted read path stays
   consistent. This is the identity analog of the finance rule "one intake path."
3. **`staff_management.py`'s flagged "Phase G" PG role write** (`:434,:502`) is
   the same gap for a second surface — fold it into the same fix, don't solve it
   separately.
4. **Do not wire `community.py`'s dead `/directory`** without reconciling it
   against the live `server.py` handler.
5. Anything touching a write path is **sign-off gated** — this document is a
   trace-and-report deliverable only.
