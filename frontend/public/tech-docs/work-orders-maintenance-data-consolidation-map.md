# Work Orders / Maintenance Data Consolidation Map — Page → API → Service → Database

**Generated:** 2026-08-05
**Scope:** the **work orders / maintenance** dashboard domain — the third domain
group traced under the "same page→router→service→DB method, rest of the app"
programme (after identity/ownership and occupancy/BI). Same evidence bar as
`docs/identity/identity-ownership-data-consolidation-map.md`.

---

## 0. Headline: fully Mongo-operational, with a *disconnected* Postgres-native replacement being built

Unlike identity (promoted) or occupancy (promoted) or BI (governed dual-path
waiting on data), the classic maintenance/work-order records have **no Postgres
cutover in progress at all**:

- **6 of 9 routers are CONFIRMED MONGO-ONLY**, zero Postgres path: `maintenance.py`,
  `work_orders.py`, `tenant_maintenance.py`, `defects_register.py`, `ppm.py`,
  `request_catalogue.py`. The legacy collections (`maintenance_requests`,
  `work_orders`, `defects`, `maintenance_schedules`, `purchase_orders`,
  `invoices`, `work_order_quotes/approvals/invoices`) are Mongo, full stop.
- **There is NO `maintenance`/`work_order`/`ops` cutover-status domain.** `grep` of
  `resolve_read_source`/`require_domain_source` finds nothing for this domain —
  so there is no governed dual-path, no shadow, no promotion lever. This is
  *not* a domain waiting to be flipped; it has never been migrated.
- **A parallel Postgres-native `ops.*` schema exists** (`ops_cases.py`,
  `ops_repairs.py` → `ops.cases`, `ops.vendors`, `ops.service_requests`,
  `ops.vendor_assignments`, …). It is Postgres from inception (SQLAlchemy +
  `set_tenant` RLS), **not** a cutover of the Mongo collections — and it is
  **disconnected** from the legacy maintenance data. `workflow_requests.py` is the
  only bridge: it can read `ops.cases` from Postgres, but only behind the
  `GOVERNANCE_READ_PG_ENABLED` `data_source_primary` toggle (kept OFF pre-cutover),
  always falling back to Mongo `workflow_requests`.

**So the path to 🟢 for maintenance is not a promotion or a data backfill — it is
completing the `ops.*` rewrite and migrating the legacy Mongo records into it.**
That is a build effort, categorically different from identity/occupancy (flip) or
BI/finance (data). `analytics.fact_work_order` (empty, 0 rows) is only a downstream
BI mirror written by `bi_etl_service.py` and read by `bi_service.py` — **no
operational maintenance router reads it.**

---

## 1. Page inventory (10 routes)

| Page (route) | Component | Key endpoints | Router | Store | Verdict |
|---|---|---|---|---|---|
| `/maintenance` (+ PPM tab) | `MaintenancePage.tsx` | `/maintenance`, `/work-orders`, `/ppm*`, `/contractors`, `/purchase-orders`, `/invoices`, `/workflow-requests` | `maintenance.py`, `work_orders.py`, `ppm.py` | Mongo (`maintenance_requests`, `work_orders`, `maintenance_schedules`, `contractors`, `purchase_orders`, `invoices`) | 🔴 Mongo-only |
| `/maintenance/[id]` | inline | `/maintenance/{id}` | `maintenance.py` | Mongo `maintenance_requests` | 🔴 Mongo-only |
| `/maintenance/work-order/[wo_id]` | `WorkOrderDetailsPage.jsx` | `/work-orders/{id}`, `/quotes`, `/approvals`, `/invoices`, `/timeline`, `/communications` | `work_orders.py` | Mongo (`work_orders`, `work_order_quotes/approvals/invoices`, `committee_resolutions`) | 🔴 Mongo-only |
| `/maintenance/defects` | `DefectsPage.jsx` | `/defects` | `defects_register.py` | Mongo `defects` | 🔴 Mongo-only |
| `/requests` (+ tabs) | `RequestsPage.jsx` + tabs | `/request-catalogue`, `/requests/*`, `/work-orders`, `/workflow-requests` | `request_catalogue.py`, `workflow_requests.py`, `requests/*` routers | Mongo; `workflow_requests` optionally reads `ops.cases` (gated OFF) | 🔴 Mongo-primary (1 gated PG read) |
| `/requests/[id]` | `RequestStatusPage.jsx` | `/engagement/requests/{id}/status`, `/workflow-requests/{id}/status` | `workflow_requests.py`, `engagement.py` | Mongo `workflow_requests` | 🔴 Mongo-primary |
| `/requests/new` | `SmartRequestPage.jsx` | `/workflow-requests/*`, `/workflow-requests/smart` | `workflow_requests.py` | Mongo `workflow_requests` | 🔴 Mongo-primary |
| `/requests/my-approvals` **and** `/dashboard/approvals` | `MyApprovalsPage.jsx` | `/work-orders?status=pending_approval`, `/work-orders/invoices/{id}/approve` | `work_orders.py` | Mongo | 🔴 Mongo-only — **duplicate route** (both render the same component) |
| `/governance/todos` | `TodosPage.jsx` | `/todos` | (todos router) | Mongo `todos` | 🔴 Mongo-only |
| `/maintenance/schedule` | `SchedulePage.jsx` | `/schedule` | (schedule router) | Mongo `schedule` | 🔴 Mongo-only |
| `/maintenance/tenant` | `TenantMaintenancePage.jsx` | `/tenant/maintenance`, `/tenant/maintenance/{id}/messages` | `tenant_maintenance.py` | Mongo (`tenant_maintenance_requests`, `tenancies`, `maintenance_request_messages`) | 🔴 Mongo-only |

---

## 2. Router-level DB source evidence

| Router | Verdict | Store (file:line) |
|---|---|---|
| `maintenance.py` | MONGO-ONLY | `maintenance_requests`/`contractors`/`purchase_orders`/`invoices` (`from database import db`, l.21) |
| `work_orders.py` | MONGO-ONLY | `work_orders`/`work_order_quotes/approvals/invoices`/`committee_resolutions`/`financial_transactions` |
| `tenant_maintenance.py` | MONGO-ONLY | `tenant_maintenance_requests`; via `tenancy_service`: `tenancies`, `maintenance_request_messages` |
| `defects_register.py` | MONGO-ONLY | `defects` (l.89) |
| `ppm.py` | MONGO-ONLY | `maintenance_schedules`, `events` (l.27) |
| `request_catalogue.py` | MONGO-ONLY | via `request_catalogue_service`: `user_units`, `units` |
| `workflow_requests.py` | **MIXED** | Mongo `workflow_requests` primary; **gated** PG read of `ops.cases` + `core.lots` behind `GOVERNANCE_READ_PG_ENABLED` (l.104-186), directional PG→Mongo fallback |
| `ops_cases.py` | **POSTGRES-NATIVE** | `ops.cases`, `ops.case_events/links`, `ops.task_*`, `core.approval_requests` — SQLAlchemy + `set_tenant`; **no cutover gate** (never a Mongo collection) |
| `ops_repairs.py` | **POSTGRES-NATIVE** | `ops.vendors`, `ops.service_requests`, `ops.vendor_assignments`, `ops.recurring_task_templates`, `core.parties` — **no cutover gate** |

---

## 3. Flagged, NOT fixed (need sign-off / verification)

1. **[P1 — multi-tenant governance bug] `work_orders.py:533` MAJORITY quorum counts EC
   members across ALL buildings.** `db.users.count_documents({"role": EC_MEMBER,
   "is_active": True})` has **no `building_id` filter**, and `db.users` is a global
   (tenant-bypass) collection — so a single building's MAJORITY work-order approval
   denominator is inflated by every other building's EC members (rule #1 violation).
   **Recommended fix** (matches this file's own l.413 pattern and
   `notifications.py:281`): count building-scoped active EC *memberships* —
   `db.memberships.count_documents({"building_id": building_id, "role":
   UserRole.EC_MEMBER, "is_active": True})`.
   **Why flagged, not shipped:** the current bug inflates the denominator
   (approvals *too hard* — safe-ish). A naive switch to `memberships` is only correct
   if this building's EC members are actually stored as `role="ec_member"`
   memberships; if they are not, the count could drop toward 0 → `required_votes =
   (0//2)+1 = 1` → MAJORITY passes on a *single* vote — a governance-*weakening*
   regression. **Verify live** that `memberships` holds `ec_member` rows for the
   building (count > 0 and equals the real EC size) BEFORE applying. Untested here
   (no DB); this is EC approval quorum — do not change blind.
2. **[P3] Duplicate route.** `/dashboard/approvals` and `/requests/my-approvals`
   render the identical `MyApprovalsPage.jsx` with the same feature guard and
   endpoints. Consolidate to one (redirect the other) to avoid drift.
3. **[P3] Endpoint-string inconsistency.** `workflowRequestsApi.list` calls
   `'/workflow-requests/'` (trailing slash) while `MaintenancePage`/`MyRequestsTab`
   call `'/workflow-requests'` (no slash). Same router today, but a latent 307/404
   hazard if redirect behavior changes.

---

## 4. Recommended next steps (report only)

- Maintenance is **not** a cutover-flip candidate — there is no domain to promote
  and no ETL to populate. Its Postgres future is the **`ops.*` rewrite**; the
  decision to make is whether/when to migrate the legacy `maintenance_requests`/
  `work_orders`/`defects` collections onto `ops.*` (a build, tracked separately),
  not a control-plane action.
- The `work_orders.py:533` quorum bug is the one **concrete correctness fix** this
  trace surfaced — fix it after the live `memberships` verification above.
