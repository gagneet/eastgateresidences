# Occupancy & BI Data Consolidation Map — Page → API → Service → Database

**Generated:** 2026-08-05
**Scope:** the **occupancy** and **business-intelligence (BI)** dashboard
clusters — the second domain group traced under the "same page→router→service→DB
method, rest of the app" programme (after identity/ownership). Same evidence bar
as `docs/identity/identity-ownership-data-consolidation-map.md` and
`docs/finances/financial-data-consolidation-map.md`.

> **See also:** `tasks/GAP-FIN-050-bi-portfolio-analytics-live-canonical-sourcing.md`
> (the owning ticket for BI's data-sourcing — read it before touching any BI
> figure), `GAP-CUTOVER-001` (cutover control plane),
> `docs/deployment/bi_pg_primary_toggle_verification_2026-06-11.md`.

---

## 0. Headline: neither cluster needs a code "flip" — and only one is flip-*able*

Both clusters are **already governed dual-paths** — the Postgres read code
already exists and is selected per-building by the cutover control plane. So
unlike identity's `GET /users` (which genuinely still read Mongo unconditionally
and needed a code gate added), there is **no page here to rewire**. The blocker
is entirely **data readiness + a governed promotion decision**:

> **CORRECTION (2026-08-05, against the live-verified control plane):** an
> earlier draft of this document said occupancy was at `postgres_shadow` and
> needed promoting — that was built on stale sources (`postgresql_cutover_status.md`
> 2026-07-01 = `mongo_primary`; `recent_pg_cutover_reaudit_2026-07-24.md` body =
> `postgres_shadow`). Both are **superseded**. `GAP-CUTOVER-001` (refreshed
> 2026-08-02, **verified live from `core.domain_cutover_status` + `core.cutover_audit_log`**,
> lines 85, 122) states **occupancy is `postgres_write` for East Gate** — reads
> *and* writes already resolve to Postgres. So occupancy is **already flipped**,
> not a pending candidate. (The ticket flags one open caveat: "write contract
> still needs follow-up audit" — the control-plane row is `postgres_write` but the
> `recompute` endpoint still 409s in PG mode, so the write *code* lags the
> promoted state; reads are genuinely PG.)

| Cluster | Code state | Data state | "Flip" = | Verdict (live 2026-08-02) |
|---|---|---|---|---|
| **Occupancy** | governed dual-path, every read gated on `resolve_read_source("occupancy")`; PG code complete | `analytics.fact_occupancy_snapshot` **populated** (174 rows; 87 for East Gate) | already done — domain is `postgres_write` | ✅ **already flipped** (reads on PG). Write code lags the promoted row — see caveat above. |
| **BI** | governed dual-path, every `bi_service.get_*` gated on `bi_pg_primary_enabled` per building; PG code reads `analytics.fact_*` | **14 of 16 `fact_*` tables EMPTY (0 rows)**; only `fact_occupancy_snapshot` has data | flipping the toggle would show **blanks**, not Mongo (fallback is exception-only) | ❌ **not flip-able** — and the toggle path is **not the sanctioned target** anyway (see §2) |

**The one-sentence answer to "why are we still on Mongo here":** for occupancy we
are **not** — it's already `postgres_write` (reads on PG), the earlier
"pending promotion" framing was stale; for BI, Postgres has **no data** (the ETL
never backfilled the fact layer) **and** the standing architectural decision is
that BI should compute *live from canonical helpers*, not read the ETL snapshots
the toggle points at.

---

## 1. Occupancy — ready to promote (control-plane, not code)

**Chain:** `/intelligence/occupancy` → `OccupancyIntelligencePage.jsx`
(`FeatureGuard featureKey="occupancy_intelligence"`) → `routers/occupancy.py`.

| Endpoint | Handler | Gate | PG branch | Mongo branch |
|---|---|---|---|---|
| `GET /occupancy/summary` | `get_occupancy_summary` (`:294`) | `resolve_read_source(bid,"occupancy")==postgres` (`:301`) | `_pg_occupancy_summary` → `analytics.fact_occupancy_snapshot` | `db.occupancy_status` |
| `GET /occupancy/lots` | `get_occupancy_lots` (`:351`) | gate `:358` | `_pg_occupancy_lots` → `fact_occupancy_snapshot` | `db.occupancy_status` |
| `GET /occupancy/trends` | `get_occupancy_trends` (`:373`) | gate `:384` | `_pg_occupancy_trends` → `fact_occupancy_snapshot` | `db.occupancy_snapshots` / `db.occupancy_status` |
| `POST /occupancy/recompute` | `trigger_recompute` (`:441`) | gate `:451` | **409** — refuses, defers to `bootstrap_postgres_occupancy_snapshot.py` | writes `db.occupancy_status` + `db.occupancy_snapshots` |

- **Every read is gated** on the same `resolve_read_source("occupancy")` check —
  no read is unconditionally either store. `resolve_read_source` (`cutover_status_service.py:735`)
  returns `get_or_default_cutover_status(bid,"occupancy").read_source`.
- **Current state (static sources disagree, both → Mongo reads today):**
  `postgresql_cutover_status.md:63` says `mongo_primary/not_started`; the newer
  `recent_pg_cutover_reaudit_2026-07-24.md:25` says `postgres_shadow`. Both modes
  map to `read_source=mongo` (`cutover_status_service.py:57-58`), so the PG branch
  is **dormant today**. Live control-plane value is **unverified from static
  sources** (needs a DB read).
- **Writes stay Mongo by design.** `recompute` 409s in PG mode; the PG snapshot
  is refreshed **out-of-band** by `bootstrap_postgres_occupancy_snapshot.py`. No
  runtime write propagates to Postgres. The reaudit explicitly says **do not**
  promote occupancy to `postgres_write` until a real occupancy write path exists.

**The flip (documented, gated — do NOT run blind):** promote the `occupancy`
domain `postgres_shadow → postgres_read` via the cutover control plane. This
activates the PG read branch with **zero code change**. Precondition: the shadow
comparison must confirm `fact_occupancy_snapshot` parity with Mongo
`occupancy_status` for the target building(s) — verifiable only against the live
DB, which this pass did not have. Do not promote to `postgres_write`.

---

## 2. BI — blocked on data, and the toggle is not the sanctioned target

**Pages:** `/intelligence/bi` (`BIAnalyticsPage.tsx`), `/bi/platform`
(`PlatformBIAnalyticsPage.tsx`, super_admin), `/bi/manager/[id]`, `/bi/agency/[id]`.
All are thin wrappers; agency/manager/platform rollups delegate to the same
per-building `bi_service.get_*` functions.

**Gating mechanism (per-building, `bi_service.py:40`):** every `get_*` calls
`_toggle_on(building_id)` → `is_cutover_feature_enabled(bid,"bi_pg_primary_enabled")`,
then `if _toggle_on: try PG(analytics.fact_*) except Exception: Mongo`. Two
properties matter:
- **Fallback is exception-only.** An empty-but-valid PG result returns `[]`/zeros
  with no exception → the UI shows **blanks**, it does not revert to Mongo
  (`bi_service.py:302-307`).
- `get_financial_summary` sources **arrears + collection rate always live from
  Mongo helpers** regardless of the toggle; only fund balances use `fact_financial_balance`.

**Readiness — `analytics.fact_*` row counts** (`database_live_inventory_2026-08-03.md`):

| Populated | Empty (0 rows) — would render as blanks if flipped |
|---|---|
| `fact_occupancy_snapshot` (174) | `fact_levy_charge`, `fact_levy_payment`, `fact_arrears_snapshot`, `fact_financial_balance`, `fact_capex_plan`, `fact_capex_actual`, `fact_work_order`, `fact_compliance_event`, `fact_utility_bill`, `fact_smart_request`, `fact_asset_condition_snapshot`, `fact_true_cost_ownership`, `fact_investor_yield_snapshot`, `fact_ownership_transfer` |
| `fact_building_health_snapshot` (**1** — effectively empty) | + all `dim_*` and `bridge_lot_owner` = 0 |

`bi_etl_runs` = 11 (ETL has executed) but the fact layer is unpopulated — so the
gap is **ETL, not a toggle**. `bi_pg_primary_enabled` is classified
`DATA_SOURCE_PRIMARY` (`toggle_classification.py:88`), global seed default
`False`; live per-building overrides are **unverified from static sources**.

**Why the toggle is the wrong lever even after ETL runs (the important part):**
`GAP-FIN-050` (executing the `GAP-FIN-040` §8 decision) has already decided BI
must compute **live from the canonical helpers** (`get_arrears_metrics()` /
`get_collection_rate_metrics()`), with the ETL `analytics.fact_*` layer demoted
to a **downstream materialization, never the read path for a figure presented as
current.** Pointing BI reads at `fact_*` via `bi_pg_primary_enabled` is the
*opposite* of that decision — it reads stale ETL snapshots. So this cluster's
real work is `GAP-FIN-050`'s live-canonical rewrite (P1, depends on `GAP-FIN-040`
B1/B2 landing first), **not** flipping the toggle. The empty fact tables are a
second, independent reason not to flip it today.

> This is a live instance of the P0.3 incident class: on 2026-06-09 a bulk
> enable-all flipped `bi_pg_primary_enabled` to TRUE before the fact layer was
> ready — exactly the failure this readiness table exists to prevent. Do not
> enable it as part of a "make Postgres primary" sweep.

---

## 3. Cross-references (do not rediscover)

| Ticket / doc | Owns |
|---|---|
| `GAP-FIN-050` | BI / portfolio / investor live-canonical sourcing — the real BI work |
| `GAP-FIN-040` §8 | the decision that BI computes live from canonical helpers, ETL is downstream |
| `GAP-FIN-044` | sibling audit for the intelligence/forecast cluster (same disease) |
| `GAP-CUTOVER-001` | cutover control plane; identity_core promotion precedent |
| `docs/deployment/bi_pg_primary_toggle_verification_2026-06-11.md` | the toggle's verification history |

---

## 4. Recommended next steps (report only — control-plane/data actions, sign-off gated)

1. **Occupancy is the flip candidate.** Verify `fact_occupancy_snapshot` shadow
   parity for the target building(s) against live DB, then promote the
   `occupancy` domain `postgres_shadow → postgres_read` via the cutover service.
   No code change. Keep writes on Mongo (do not promote to `postgres_write`).
2. **BI: do not flip the toggle.** Route BI's real work through `GAP-FIN-050`
   (live-canonical sourcing) — the toggle points at an empty, architecturally-
   superseded ETL layer.
3. **Code-quality note (not fixed — a design call for the BI owner):** the
   exception-only fallback (`bi_service.py:302-307`) means even a *partially*
   populated fact layer shows blanks rather than falling back. Whether an empty
   PG result should fall back to Mongo is a real decision (an empty result can be
   legitimately empty), so flagged, not changed.
4. Done in this pass: deduplicated `UserRole.EC_MEMBER` in `occupancy.py`'s
   view-role set (the `chairman→EC_MEMBER` rename artifact CLAUDE.md flags).
