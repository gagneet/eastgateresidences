# PostgreSQL Cutover Schema Walkthrough — 2026-06-01

> **Historical reference only.** The current live PostgreSQL state was revalidated on 2026-07-24 at Alembic
> `0071_powerhouse_cmd_foundation`. East Gate has populated PostgreSQL identity and finance shadow data. Use
> [`postgresql-live-data-inventory-2026-07-24.md`](postgresql-live-data-inventory-2026-07-24.md) for current table
> counts, populated fields, and cutover modes.

Status: current read-only inspection and P0 gate record  
Scope: PostgreSQL schema, finance onboarding cutover readiness, and MongoDB/PostgreSQL source ownership before enabling PostgreSQL-primary data service.

## Inspection Summary

The configured PostgreSQL database was inspected read-only through `DATABASE_URL` using `backend/scripts/postgres_cutover_p0_readiness.py` and direct `information_schema` checks. No data was written.

Current database state:

| Item | State |
|---|---|
| PostgreSQL version | PostgreSQL 16.14 |
| Alembic version | `0044` |
| Total application tables | 147 |
| Finance onboarding schema | Present |
| East Gate PostgreSQL identity foundation | Not populated |
| East Gate PostgreSQL finance foundation | Not populated |
| Global cutover toggles | Enabled and require review before switch |
| PostgreSQL primary switch readiness | Not ready |

## Schema Inventory

| Schema | Table count | Purpose |
|---|---:|---|
| `access` | 8 | Access devices, issuance, request lifecycle |
| `ai_assist` | 7 | AI review/assessment support tables |
| `analytics` | 1 | Audit/analytics support |
| `communications` | 12 | Announcements, notices, campaign and communication records |
| `compliance` | 23 | Compliance registers, certificates, insurance, WHS/privacy/risk records |
| `core` | 29 | Tenants, schemes, users, lots, parties, ownership, feature toggles, outbox |
| `documents` | 6 | Document registry/folders/metadata |
| `finance` | 25 | Ledger, funds, GL accounts, levies, receipts, trust, reconciliation, evidence |
| `governance` | 8 | AGM, motions, votes, EC membership, decisions, by-laws |
| `modules` | 4 | Module registry and activation |
| `ops` | 18 | Cases, work orders, vendors, repairs, service requests |
| `sustainability` | 6 | Sustainability profile and project tables |

## Finance Onboarding Schema

Migration `0044` is present. The following cutover objects exist:

| Object | State | Role |
|---|---|---|
| `finance.evidence_documents` | Present, empty | Stores opening-balance evidence metadata, hash, uploader, declared total |
| `finance.financial_cutover_config` | Present, empty | Stores per-building cutover date and source-of-truth state |
| `finance.financial_onboarding_audit` | Present, empty | Immutable onboarding audit: approver, balances, evidence hash, journal IDs |
| `finance.journal_entries.evidence_document_id` | Present | Links genesis journals to evidence |
| `finance.journal_entries.approved_by` | Present | Links journal approval to `core.users.user_id` |
| `finance.journal_entries.posted_by` | Present | Links journal posting actor |
| `finance.journal_entries.prev_entry_hash` / `entry_hash` | Present | Hash-chain fields |

## Current Data Counts For Cutover-Critical Tables

| Table | Rows | Readiness implication |
|---|---:|---|
| `core.tenants` | 94 | Tenant rows exist, but not sufficient for East Gate switch |
| `core.schemes` | 0 | East Gate has no PostgreSQL scheme row |
| `core.users` | 0 | PostgreSQL-authenticated operational users are not populated |
| `core.lots` | 0 | No lot/unit foundation for owner finance reads |
| `core.parties` | 0 | No owner/vendor/party foundation |
| `core.ownership_periods` | 0 | No owner-to-lot bitemporal mapping |
| `finance.funds` | 0 | Funds not seeded for East Gate |
| `finance.gl_accounts` | 0 | GL accounts not seeded |
| `finance.journal_entries` | 0 | No genesis or post-cutover journals |
| `finance.journal_lines` | 0 | No ledger lines |
| `finance.evidence_documents` | 0 | No opening-balance evidence registered |
| `finance.financial_cutover_config` | 0 | No cutover date/source-of-truth config |
| `finance.financial_onboarding_audit` | 0 | No onboarding audit trail |

## P0 Gate Results

Run command:

```bash
backend/venv/bin/python3 backend/scripts/postgres_cutover_p0_readiness.py --building-id 13195
```

Latest result: `fail`.

| P0 ID | State | Meaning | Next action |
|---|---|---|---|
| P0-01 | Pass | Migration `0044` schema is present | Keep Alembic upgrade/downgrade test in CI/staging |
| P0-02 | Operator pending | Rollback has not been proven in this local inspection | Run downgrade/upgrade cycle in disposable staging database |
| P0-03 | Partially addressed | `financial_onboarding.py` now accepts scheme UUID and plan number context | Audit remaining finance/trust routers for raw `building_id` comparisons |
| P0-04 | Warn | Global PostgreSQL cutover toggles are enabled | Disable global defaults or prove per-scheme overrides are authoritative before switch |
| P0-05 | Fail | East Gate scheme/users/lots/parties/ownership are missing in PostgreSQL | Run identity, lot, party, and ownership import/bootstrap |
| P0-06 | Fail | Opening-balance evidence is missing | Upload/register approved bank/reconciliation evidence |
| P0-07 | Fail | Genesis onboarding has not posted journals | Run clean-slate onboarding only after evidence and approval are ready |
| P0-08 | Fail | Onboarding audit trail is missing | Created by successful onboarding run |
| P0-09 | Code-ready, data pending | Archive endpoint is read-only and scoped after identifier normalisation | Smoke test with authenticated finance admin against real East Gate context |
| P0-10 | In progress | Focused onboarding tests pass; full backend suite not rerun here | Run full `tests/backend` before release |

## Router Source Ownership Before Switch

| Router/domain | Current safe source | PostgreSQL target | Switch blocker |
|---|---|---|---|
| `financial_onboarding.py` | PostgreSQL schema plus Mongo archive bridge | PostgreSQL onboarding/cutover state | Evidence, East Gate scheme/users, and genesis journals missing |
| `finance.py` | MongoDB for current production finance | PostgreSQL ledger/read models for post-cutover data | Current read/write routes still require shadow-read and write-path hardening |
| `owner_finance.py` | MongoDB owner finance data | PostgreSQL lots, ownership, receipts, levies | Core owner/lot foundation empty |
| `trust_accounting.py` | Mixed/PostgreSQL-capable reads with legacy fallback | PostgreSQL trust ledger | Trust accounts, GL accounts, and journals empty |
| `trust_reconciliation.py` | PostgreSQL-capable but unpopulated | PostgreSQL reconciliation tables | Bank import/reconciliation rows absent |
| `analytics.py` | Mixed Mongo/PostgreSQL adapters | PostgreSQL-derived finance analytics | Read models and finance data absent |
| `users.py` | PostgreSQL-capable, but empty PG identity data | PostgreSQL `core.users` | Users/memberships/import absent |
| `settings.py` | Mixed config adapters | PostgreSQL feature/settings config | Global toggle safety unresolved |

## Correct Path From Here

1. Freeze broad PostgreSQL switching until P0 checker returns `pass` or only approved `warn` states.
2. Disable unsafe global cutover toggles or document why per-scheme overrides prevent accidental activation.
3. Populate East Gate `core.schemes`, users, lots, parties, and ownership periods.
4. Seed finance funds, GL accounts, trust accounts, and open accounting period through the onboarding/bootstrap flow.
5. Register authoritative evidence documents with SHA-256 hashes and declared totals.
6. Run clean-slate genesis onboarding for approved opening balances only.
7. Verify hash chain, trial balance, evidence links, approval metadata, and onboarding audit rows.
8. Only then enable shadow reads for selected routes; do not enable PostgreSQL-primary reads globally.
