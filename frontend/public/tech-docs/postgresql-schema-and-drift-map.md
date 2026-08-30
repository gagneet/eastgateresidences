# PostgreSQL Schema & Mongo/Postgres Drift Map (Finance Domain)

**Generated:** 2026-08-05

> **CORRECTION (2026-08-05):** this document was originally written against
> `postgresql-live-data-inventory-2026-07-24.md`, a stale snapshot. A fresher
> one exists — `docs/architecture/database_live_inventory_2026-08-03.md`
> (15 schemas, 223 tables, Alembic head `0079`, matching the repo's current
> head exactly) — and two specific claims below were **wrong** as a result:
> `finance.receipt_allocations` is not under-populated (3,534 real allocation
> rows against 2,229 receipts as of 2026-08-03, not 334 against 2,221 as the
> 2026-07-24 snapshot showed), and `finance.expense_transactions` is no
> longer empty (238 real rows). See `tasks/GAP-FIN-053-...md` for the
> corrected findings and why the *real* receipt-allocation problem is worse
> than a completeness gap — it's an integrity bug already tracked in
> `tasks/GAP-FIN-046-pg-receipt-allocation-integrity.md`. This is left
> visible rather than silently rewritten, per this repo's own norm of
> flagging stale claims explicitly instead of leaving two documents quietly
> disagreeing (`tasks/FINANCE-CURRENT-STATE-2026-08-03.md`).
**Purpose:** Answer, with evidence from the live database and the actual
cutover-control code (not assumptions): what PostgreSQL schema exists today,
which financial pages are actually served from it vs. from MongoDB, where the
two databases are being actively compared (and disagreeing), and where the
two schemas duplicate each other in a way that should be consolidated.

This is the companion to `docs/finances/financial-data-consolidation-map.md`
(page → API → DB source) — that document maps *pages*; this one maps the
*database* side and the drift-measurement machinery underneath it, and
corrects two claims in the earlier pass (see the callout in that document's
§2a).

---

## 1. The drift risk, stated plainly

This platform runs two databases for the same financial facts, by design,
during migration: MongoDB (`eastgate_production`, the original operational
store) and PostgreSQL 16 (`core`/`finance`/`analytics`/... schemas, the
target store). The intent is "PostgreSQL primary, MongoDB fallback" — but
today, for the finance domain specifically, it is the **opposite**: **MongoDB
is primary for reads and writes on almost every finance route**, and
PostgreSQL receives a shadow copy that is compared for accuracy but not
served to any page, except for a small, explicitly-gated set of routes.

The platform's own instrumentation already proves drift is real and
measurable, not hypothetical:

- **`core.shadow_diffs`: 4,934 rows** — each row is a detected mismatch
  between a Mongo-computed value and its Postgres-computed counterpart for
  the same request.
- **`core.shadow_read_divergences`: 211 rows** — a second, more detailed
  divergence log (`postgres_result` vs `mongodb_result` per query, with
  `diverging_fields` listed).
- A named, still-open blocker, **GAP-FIN-031** (FY2026 bank-transaction-to-
  receipt matching), is the explicit reason `finance.unit_levy_ledger` and
  `finance.transactions` remain shadow-only. The code comment for
  `finance.unit_dashboard_overview` states plainly: *"promoting before that
  is done would repeat the exact wrong-balance near-miss this same session
  found and rolled back."* That is a real incident, not a theoretical risk.
- `finance.quarterly_budget` has **no Postgres query or shadow comparator at
  all** — meaning for that one route, drift isn't even being measured; it is
  Mongo-only and unaudited by the platform's own tooling.

So the practical read for "why do finance pages disagree or show broken
data": most of them were never wired to Postgres to begin with (see §7), and
of the ones that were, the platform itself has already found and recorded
thousands of value mismatches, and has deliberately kept the read source on
Mongo until those are resolved.

---

## 2. Full PostgreSQL schema inventory

Two dated, code-generated inspections exist in the repo already
(`frontend/public/tech-docs/postgresql-cutover-schema-walkthrough-2026-06-01.md`,
`.../postgresql-live-data-inventory-2026-07-24.md`). This section reconciles
them with the current repo state.

| Schema | Table count (2026-06-01) | Table count (2026-07-24) | Purpose |
|---|---:|---:|---|
| `access` | 8 | 8 | Access devices, issuance, request lifecycle |
| `ai_assist` | 7 | 7 | AI review/assessment support |
| `analytics` | 1 | 26 | BI/ETL fact & dimension tables — grew from 1→26 tables between the two snapshots (migration `0052_canonical_bi_schema.py`, 58 KB, added the bulk of this) |
| `communications` | 12 | 23 | Announcements, notices, campaigns, conversations |
| `compliance` | 23 | 23 | Compliance registers, certificates, insurance, WHS/privacy |
| `core` | 29 | 43 | Tenants, schemes, users, lots, parties, ownership, feature toggles, outbox, **cutover control tables** |
| `documents` | 6 | 6 | Document registry/folders/metadata |
| `finance` | 25 | 30 | Ledger, funds, GL accounts, levies, receipts, trust, reconciliation, evidence |
| `governance` | 8 | 8 | AGM, motions, votes, EC membership, decisions, by-laws |
| `modules` | 4 | 4 | Module registry and activation |
| `ops` | 18 | 18 | Cases, work orders, vendors, repairs, service requests |
| `sustainability` | 6 | 6 | Sustainability profile and project tables |
| **Total** | **147** | **202** | |

**Staleness warning:** the 2026-07-24 snapshot was taken at Alembic head
`0071_powerhouse_cmd_foundation`. This repo's current Alembic head is
**`0079_opening_balance_evidence`** — 8 migrations newer
(`0072_recovered_fees_gl_account`, `0073_accounting_period_integrity`,
`0074_harden_recon_batches`, `0075_user_email_aliases`,
`0076_levy_charge_uniqueness`, `0077_arrears_grace_meta`,
`0078_levy_grace_insert_default`, `0079_opening_balance_evidence`). `0079`
(additive) adds `'opening_balance'` to the `finance.evidence_documents.document_type`
CHECK constraint — no new table/column, so counts are unchanged. The `finance.arrears_status_snapshot`
table (from `0077`) and the `levy_runs.grace_deadline_date` /
`levy_items.grace_deadline_date` /`is_past_grace`/`days_overdue` columns
(also `0077`) post-date the live inventory and are **not reflected in its
table/column list**. Re-run `backend/scripts/postgres_cutover_p0_readiness.py`
or the underlying `information_schema` inspection to get a current snapshot
before relying on exact row/column counts.

---

## 3. Finance schema: table-by-table status (East Gate `13195`, 2026-07-24)

### 3a. Populated and in active use

| Table | Rows | Backing ORM model | Consumed by |
|---|---:|---|---|
| `finance.funds` | 3 | `PgFund` | Fund cash position, budget vs actual |
| `finance.trust_accounts` | 2 | `PgTrustAccount` | Trust accounting pages (promoted to postgres_write for 13195) |
| `finance.gl_accounts` | 16 | `PgGlAccount` | Journal postings, chart of accounts |
| `finance.accounting_periods` | 7 | `PgAccountingPeriod` | Period locking, journal posting eligibility |
| `finance.journal_entries` | 3,198 | `PgJournalEntry` | Double-entry ledger — the ultimate source of truth in the target design |
| `finance.journal_lines` | 6,396 | `PgJournalLine` | Per-account debit/credit lines |
| `finance.levy_runs` | 6 | `PgLevyRun` | Levy issuance runs (annual/quarterly charge batches) |
| `finance.levy_items` | 957 | `PgLevyItem` | Per-lot levy charges — the Postgres equivalent of Mongo's `unit_levy_ledger` rows |
| `finance.receipts` | 2,221 | `PgReceipt` | Payment receipts — Postgres equivalent of Mongo's `levy_payments` |
| `finance.receipt_allocations` | 3,534 (2026-08-03; superseded the 334 figure from the 2026-07-24 snapshot used in the first draft of this table) | `PgReceiptAllocation` | Receipt-to-charge allocation — **not a completeness gap** (3,534 allocations against 2,229 receipts is a normal multi-allocation ratio). The real, root-caused problem is an **integrity bug**: reversed receipts' allocations are never un-linked, so `finance.levy_items.paid_cents` counts money from receipts that no longer exist — tracked in `tasks/GAP-FIN-046-pg-receipt-allocation-integrity.md` ("Bug A — receipt reversal does not cascade to allocations"), status `understood`, not yet `implemented`. |
| `finance.evidence_documents` | 10 | `PgEvidenceDocument` | Opening-balance/cutover evidence |
| `finance.financial_cutover_config` | 1 | (raw SQL, no ORM model found) | Per-building cutover date/config |
| `finance.financial_onboarding_audit` | 1 | (raw SQL, no ORM model found) | Onboarding audit trail |
| `finance.bank_transactions` | 2,855 | (no ORM model in `financial_core/adapters/db_postgres/models.py`; separate reconciliation pathway) | Bank feed import / reconciliation matching |

### 3b. Table exists, has an ORM model, but is **empty** — schema built ahead of data

| Table | ORM model | Implication |
|---|---|---|
| `finance.expense_transactions` | `PgExpenseTransaction` | **Expenses are not flowing into Postgres at all.** Budget-vs-Actual and Spending Categories pages (§2b/§7) read `financial_transactions`/`levy_categories` from Mongo exclusively — there is no Postgres side to even shadow-compare against for expenses today. |
| `finance.reconstruction_execution_batches` / `finance.reconstruction_execution_batch_items` | `PgReconstructionExecutionBatch(Item)` | The historical-reconstruction admin tool (`HistoricalReconstructionPage`, `historical_levy_reconstruction_service.py`, `east_gate_levy_income_reconstruction.py`) has a **fully-built Postgres batch/approval schema** but the actual reconstruction output for East Gate lives in generated Mongo docs / CSV/xlsx artifacts under `docs/finances/`. The Postgres batch tables and the Mongo/file-based reconstruction pipeline appear to be **two parallel implementations of the same "rebuild historical ledger" concept** — worth confirming which one is authoritative before more reconstruction work happens in either. |

### 3c. Table exists (per the 2026-06-01/07-24 migration count) but not yet ORM-mapped or empty with no model found

Per the live inventory's "Empty Tables" list, the following `finance` schema
tables exist but hold zero rows and were not found to have a corresponding
class in `db_postgres/models_finance.py` or
`financial_core/adapters/db_postgres/models.py`: `bank_statement_imports`,
`council_rates`, `expense_evidence_links`, `levy_rules`,
`owner_credit_balances`, `payment_batch_items`, `payment_batches`,
`payment_plan_installments`, `payment_plans`, `reconciliation_runs`,
`trust_interest_postings`, `utility_anomalies`, `utility_bills`,
`utility_usage_readings`, `water_bills`. Several of these
(`council_rates`, `utility_bills`, `water_bills`) directly correspond to
frontend pages (`/financials/council-rates`, `/financials/water-bills`) that
today read exclusively from Mongo (`council_rates`, `water_bills`
collections) — the Postgres schema was built for these but never populated
or wired to a read path. `payment_plans`/`payment_plan_installments` are the
Postgres shadow of the `payment_plans.py` router, also currently Mongo-only.

---

## 4. Cutover control status (verbatim, 2026-07-24, East Gate `13195`)

| Domain | Mode | Read source | Write source | Readiness |
|---|---|---|---|---|
| `finance_ledger` | `postgres_shadow` | `mongo` | `mongo` | `shadow_active` — **not promoted** |
| `governance` | `postgres_write` | `postgres` | `postgres` | `promoted` (partial — `governance.decisions` still empty) |
| `identity_core` | `postgres_write` | `postgres` | `postgres` | `promoted` |
| `occupancy` | `postgres_write` | `postgres` | `postgres` | `promoted` (needs proof of PG snapshot-refresh write contract) |
| `settings` | `postgres_write` | `postgres` | `postgres` | `promoted` |
| `trust_ledger` | `postgres_write` | `postgres` | `postgres` | `promoted` (trust evidence still needs retargeting to live V2 trust system) |
| `trust_reconciliation` | `postgres_write` | `postgres` | `postgres` | `promoted` |

**Finance Cutover Toggles (2026-07-24):**

| Toggle | Global default | Building `13195` override |
|---|---|---|
| `financial_pg_reads_enabled` | `False` | `True` |
| `financial_pg_writes_enabled` | `False` | **`False`** — writes stay Mongo-only for finance even for the pilot building |
| `financial_shadow_reads_enabled` | `False` | `True` |
| `financial_integration_layer_v2` | `False` | `True` |
| `trust_pg_ledger_enabled` | `False` | `True` |
| `trust_reconciliation_pg_enabled` | `False` | `True` |
| `bank_integration_abstraction_enabled` | `False` | `True` |
| `external_api_finance_pg_enabled` | `False` | `False` |
| `onboarding_current_balance_adapters_enabled` | `False` | `False` |

**Reading this correctly:** `financial_pg_reads_enabled=True` for 13195 does
**not** mean finance reads come from Postgres — it's a necessary-but-not-
sufficient gate. The actual per-route decision goes through
`finance_route_cutover_service.get_finance_route_runtime_state()`, which
additionally requires the specific route to have
`postgres_read_supported=True`, `route_readiness.status == "shadow_pass"`,
and zero critical shadow diffs (see §5). Only 2 of the 8 registered finance
routes clear that bar today.

---

## 5. Route-level tiers (`finance_route_cutover_service._ROUTE_POLICIES`)

| Route key | Path | Tier | `shadow_supported` | `postgres_read_supported` | Why |
|---|---|---|---|---|---|
| `finance.building_overview` | `GET /finance/building-overview` | **A — promotable** | ✅ | ✅ | "Promotable when route readiness passes." |
| `finance.unit_dashboard_overview` | `GET /finance/unit-dashboard-overview/{unit_number}` | **A — promotable** | ✅ | ✅ | Promotable, but history includes a rolled-back wrong-balance near-miss — treat promotion here as needing extra scrutiny, not routine |
| `finance.summary` | `GET /finance/summary` | **B — shadow-only** | ✅ | ❌ | "PG response-shape parity incomplete; remains Mongo-primary" |
| `finance.levy_kpi` | `GET /finance/levy-kpi` | **B — shadow-only** | ✅ | ❌ | "PG read-model parity deferred" |
| `finance.arrears_detail` | `GET /arrears/detail` | **B — shadow-only** | ✅ | ❌ | "PG read-model parity deferred" |
| `finance.unit_levy_ledger` | `GET /unit-levy-ledger` | **B — shadow-only, explicitly blocked** | ✅ | ❌ | Blocked on GAP-FIN-031 (FY2026 receipt matching); do not promote until verified complete |
| `finance.transactions` | `GET /expense-transactions,/income-transactions` | **B — shadow-only, explicitly blocked** | ✅ | ❌ | Same GAP-FIN-031 dependency |
| `finance.quarterly_budget` | `GET /finance/quarterly-budget` | **C — unmeasured** | ❌ | ❌ | "Honestly Mongo-only" — no Postgres query or shadow comparator exists yet, despite `finance.levy_runs`/`finance.levy_items` already holding the data this route needs |

**Every other finance-adjacent route** used by the 48 pages in
`financial-data-consolidation-map.md` — trust (separately governed, see §4),
levy fairness/scenarios/stability, capital funding, investor intelligence,
BI, owner-hub, savings, spending categories, insurance, council rates, water
bills, GST/BAS, AP, reconciliation, owner-finance — **is outside this policy
table entirely**. That means no shadow comparison runs for them at all: not
"safely on Mongo," but literally unmeasured for Postgres/Mongo agreement one
way or the other.

---

## 6. Mongo ↔ Postgres schema equivalence (where duplication lives)

This is the concrete "where you see duplication which can be consolidated"
answer, at the schema level:

| Concept | MongoDB (current primary) | PostgreSQL (target) | Status |
|---|---|---|---|
| Per-lot levy charge/ledger | `unit_levy_ledger` (collection) | `finance.levy_items` + `finance.levy_runs` | **Both populated and actively used** (957 PG rows vs. live Mongo collection) — this is the highest-value consolidation target; shadow-compared on 3 routes already, with 4,934 recorded diffs |
| Payment receipts | `levy_payments` | `finance.receipts` + `finance.receipt_allocations` | Both fully populated (2,229 receipts, 3,534 allocations as of 2026-08-03) — the gap is not row count, it's an **integrity bug** (reversal doesn't cascade to allocations, per GAP-FIN-046) |
| Annual levy / budget header | `annual_levies` | `finance.levy_runs` (financial_year/quarter_no) + `finance.funds` (opening_balance_cents) | Partially equivalent — `annual_levies` carries budget/proposed-expense fields with no direct Postgres column found in `PgLevyRun`/`PgFund`; a straight table swap would lose data unless those fields are added |
| Expense/transaction categorisation | `financial_transactions`, `levy_categories` | `finance.expense_transactions` (238 rows as of 2026-08-03, up from 0 on 2026-07-24 — GAP-FIN-018's Step 5 posting is in progress, 163 of ~634 rows posted per that ticket) | **Partially populated, in progress** — not yet complete enough to shadow-compare against Budget vs Actual / Spending Categories, but no longer a "schema-ahead-of-data" gap |
| Trust accounts/ledger | Mongo trust collections | `finance.trust_accounts`, plus journal/GL tables | **Postgres-primary for 13195** (promoted) — the one part of finance genuinely ahead of Mongo already |
| Bank feed transactions | (Mongo bank feed docs, `bank_feeds.py`) | `finance.bank_transactions` (2,855 rows) | Populated on both sides; reconciliation matching (`financial_matching.py`, `reconciliation_matching_service.py`) needs to confirm which side it actually reads |
| Arrears status | Computed on-request from `unit_levy_ledger` (Mongo) via `_compute_grace_aware_arrears()` in `finance.py` | `finance.arrears_status_snapshot` (new in migration `0077`, precomputed nightly) | **Not yet wired to any route** — this table exists specifically to replace the runtime Mongo aggregation with an O(1) precomputed lookup, per its own docstring, but nothing in the 48-page inventory reads it yet. This is a near-term consolidation opportunity: once populated/refreshed, `finance.summary`/`finance.levy_kpi`/Arrears Recovery Board could all read one snapshot instead of three separate Mongo aggregations. |
| Council rates / water bills / utilities | `council_rates`, `water_bills` collections | `finance.council_rates` / `finance.water_bills` / `finance.utility_bills` (tables exist, **empty**) | Schema built, never populated — same pattern as expense_transactions |
| Historical reconstruction | Generated Mongo docs + `docs/finances/*.csv/xlsx` artifacts | `finance.reconstruction_execution_batches`/`_items` (built, empty) | Two parallel "rebuild the ledger" mechanisms — needs a decision on which is authoritative going forward |

---

## 7. Pages confirmed NOT on PostgreSQL (definitive, this pass)

Cross-referencing §4/§5 against the 48-page inventory in
`financial-data-consolidation-map.md`, these pages are **confirmed Mongo-only
today**, with no Postgres read path even in shadow mode (i.e., not just
"held back," but entirely outside the cutover machinery):

- `/financials/my-finances` (owner_finance.py — not in `_ROUTE_POLICIES`)
- `/financials/intelligence`, `/financials/capital-funding`, `/financials/capital-funding/elections`
- `/intelligence/levy-fairness` (has its own bespoke inline fallback, not the shared policy engine — see finding #3 in the consolidation map)
- `/intelligence/levy-scenarios`, `/intelligence/levy-stability`
- `/financials/fund-collections-by-type`, `/financials/projections`
- `/financials/savings`, `/financials/savings`, `/financials/spending-categories`
- `/financials/reconciliation`, `/financials/matching`, `/financials/ap-approval`
- `/intelligence/building-stress`, `/investor`
- `/owner-hub/*` (all subpages)
- `/financials/council-rates`, `/financials/water-bills`, `/insurance-claims`, `/insurance-lending`, `/intelligence/market`
- `/financials` and `/financials/collection-rate` — **served from Mongo**, even though 5 of their underlying routes ARE shadow-compared (so drift is measured, but never corrected by promotion)

**Confirmed on PostgreSQL (13195 only):** the trust cluster
(`/financials/trust`, `/financials/trust-bank-accounts`,
`/financials/trust-reconciliation`).

**Confirmed Postgres-native by design (not part of finance cutover, built
Postgres-first):** the BI/analytics cluster (`/intelligence/bi*` only —
`bi.py`/`bi_service.py` have 59 references to `analytics.*`/`db_postgres`/
`postgres` combined) — `analytics.*` fact tables, though the ETL freshness
relative to the live Mongo ledger has not been checked in this pass.

> **Correction (Phase 1c, 2026-08-05):** this section previously grouped
> `/admin*` into the Postgres-native cluster alongside
> `/intelligence/bi*`, by proximity/naming, not by tracing the code. Direct
> trace of `portfolio.py` shows it imports `from database import db` (Mongo)
> with 45 Mongo call sites and **zero** references to `analytics`,
> `db_postgres`, `postgres`, or `bi_service`/`bi_etl_service` — it never
> calls the BI service. `/admin*` is Mongo-only and is now
> listed as a confirmed dark hole in
> `financial-data-consolidation-map.md` §6. See `GAP-FIN-053` finding 4 (folded into `GAP-FIN-048`'s consolidation plan and `GAP-FIN-050`).

**Everything else in the 48-page inventory:** as of Phase 1c (2026-08-05),
every remaining page has been traced at router/service grep level — see
`financial-data-consolidation-map.md` §6 for the definitive per-page tally
(21 confirmed Mongo-only dark holes across categories (a) empty-PG-table,
(b) populated-but-unused-PG-table, (c) no-PG-equivalent; 3 confirmed mixed;
1 confirmed Postgres-forward cluster). Two pages remain genuinely
out of scope (`voting.py`-routed elections sub-page, and
`/requests/outstanding-issues` whose router wasn't identified).

---

## 8. Consolidation recommendations (schema-focused, this session's scope)

1. **Stop building Postgres schema ahead of any read/write path.** `finance.expense_transactions`, `finance.council_rates`, `finance.water_bills`, `finance.payment_plans`, and the reconstruction batch tables are fully modelled and migrated but hold zero rows — each represents completed schema work with no consolidation benefit until a write path populates it. Prioritise wiring existing empty-but-modelled tables over adding new ones.
2. **`finance.arrears_status_snapshot` is the highest-leverage unused asset.** It exists specifically to replace runtime Mongo aggregation for arrears across `finance.summary`, `finance.levy_kpi`, and the Arrears Recovery Board — three currently-separate Mongo computations that could become one precomputed read once a refresh job populates it.
3. **Resolve GAP-FIN-031 before touching `finance.unit_levy_ledger`/`finance.transactions` promotion** — this is already the platform's own stated blocker; don't route around it.
4. **`finance.receipt_allocations` is not a row-count problem — it's the reversal-cascade integrity bug in GAP-FIN-046.** Fix that ticket before treating `finance.levy_items.paid_cents` as trustworthy for any promotion decision.
5. **Unify `intelligence.py`'s inline levy-fairness Postgres fallback into `finance_route_cutover_service`**, or explicitly document why levy-fairness needs a separate pattern — right now it's a third, uncoordinated way of doing the same "try Postgres, fall back to Mongo" thing (see companion doc §4 finding #3).
6. **Decide the canonical historical-reconstruction path** — Postgres batch tables vs. the Mongo/file-based pipeline currently used for East Gate — before more reconstruction work deepens either side.

---

*Companion documents: `docs/finances/financial-data-consolidation-map.md`
(page-level view), `docs/finances/financial-pages-mindmap.md` (target
architecture), `frontend/public/tech-docs/postgresql-live-data-inventory-2026-07-24.md`,
`frontend/public/tech-docs/postgresql-cutover-schema-walkthrough-2026-06-01.md`,
`backend/services/financial_core/adapters/db_postgres/models.py` (ORM source
of truth), `backend/services/finance_route_cutover_service.py` (route policy
source of truth).*
