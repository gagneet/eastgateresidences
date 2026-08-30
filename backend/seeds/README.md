# Database Seed Files

Seed files populate the MongoDB database with initial data. There are two types: **snapshot seeds** (generated from live
production data) and **idempotent creator seeds** (safe to run on any environment).

## Quick Reference

```bash
# Run all seeds for a fresh environment
cd backend && source venv/bin/activate
python3 seeds/seed_all.py

# Run individual seed
python3 seeds/seed_sierra.py

# DESTRUCTIVE: Clear all data then reseed (development only)
python3 seed_database.py --clear
```

---

## Type A — Snapshot Seeds (Generated from Production)

These files are generated from live database state. **Do not edit manually** — regenerate them using the snapshot
scripts in `scripts/db/`.

| File                             | What It Captures                                               | Collections            |
|----------------------------------|----------------------------------------------------------------|------------------------|
| `snapshot_buildings.py`          | 3 buildings (13195, 16244, harbourside_view) with trust_config | buildings              |
| `snapshot_feature_toggles.py`    | 40+ feature toggles, global + per-building overrides           | feature_toggles        |
| `snapshot_settings.py`           | Per-building settings documents                                | settings               |
| `snapshot_annual_levies.py`      | FY2021–2026 levy summaries for building 13195                  | annual_levies          |
| `snapshot_budgets.py`            | Budget records current state                                   | budgets                |
| `snapshot_chat_groups.py`        | System chat group definitions                                  | chat_groups            |
| `snapshot_compliance_items.py`   | Compliance register items                                      | compliance_items       |
| `snapshot_document_folders.py`   | Default folder tree                                            | document_folders       |
| `snapshot_ec_members.py`         | EC member profiles                                             | ec_members             |
| `snapshot_emergency_services.py` | Emergency contact records                                      | emergency_services     |
| `snapshot_navigation_configs.py` | Navigation menu configs                                        | navigation_configs     |
| `units_east_gate.py`             | Complete East Gate unit ledger (87 units, 2026-04-01)          | units                  |
| `finance_history.py`             | SP 13195 FY2021–2026 levy/budget history                       | finance, annual_levies |
| `levy_ledger_east_gate.py`       | Per-unit ledger entries for all years (289KB)                  | unit_levy_ledger       |

> ⚠️ **FY 2026 READ-ONLY**: `snapshot_annual_levies.py`, `levy_ledger_east_gate.py`, and `units_east_gate.py` contain
> real production financial data for building 13195. Never modify these records — they are the source of truth for levy
> calculations and owner statements.

### Regenerating Snapshots

```bash
# Regenerate all snapshots from live database
python3 scripts/db/snapshot_all.py

# Regenerate a specific collection
python3 scripts/db/snapshot_all.py --collection feature_toggles
```

---

## Type B — Idempotent Creator Seeds (Safe to Re-run)

These scripts use upsert logic (insert if not exists). Safe to run on any environment including fresh installs.

### Building Seeds

| File                    | Purpose                                   | Target Building         |
|-------------------------|-------------------------------------------|-------------------------|
| `seed_sierra.py`        | Creates Sierra (16244) + demo users       | 16244 (demo)            |
| `seed_harbourview.py`   | Creates Harbourview + demo users          | harbourside_view (demo) |
| `seed_demo_building.py` | Creates East Gate demo shell              | 13195 (demo shell)      |
| `seed_mega_complex.py`  | 120-lot complex for levy fairness testing | test_complex            |
| `buildings.py`          | Generic multi-tenant building creator     | all                     |

### User & Unit Seeds

| File                     | Purpose                                            | Notes                                 |
|--------------------------|----------------------------------------------------|---------------------------------------|
| `users.py`               | Demo user set (8 users, all roles)                 | Uses eastgateresidences.com.au emails |
| `multi_user.py`          | User-unit relationship (user_units) + by-laws ack  | Creates occupancy records             |
| `units_2026.py`          | Re-seeds East Gate units from Excel file           | Parses Excel source data              |
| `units_comprehensive.py` | ⚠️ DEPRECATED — superseded by `units_east_gate.py` | Do not use                            |
| `units.py`               | ⚠️ DEPRECATED — superseded by `units_east_gate.py` | Do not use                            |

### Financial Seeds

| File                  | Purpose                                            |
|-----------------------|----------------------------------------------------|
| `finance.py`          | Demo finance transactions                          |
| `budget.py`           | Budget entries (uses existing financial year data) |
| `trust_accounting.py` | Trust account defaults (chart of accounts)         |

### Configuration Seeds

| File                    | Purpose                                                                                                  |
|-------------------------|----------------------------------------------------------------------------------------------------------|
| `feature_toggles.py`    | Default toggle set (~39 toggles). ⚠️ NOTE: `snapshot_feature_toggles.py` is authoritative for production |
| `phase1_ops_foundations.py` | Demo-safe PostgreSQL reference data for Phase 1 ops/access/comms/AI/sustainability tables |
| `navigation_configs.py` | Navigation menu configs. ⚠️ NOTE: `snapshot_navigation_configs.py` is authoritative                      |
| `settings.py`           | Default settings. ⚠️ NOTE: `snapshot_settings.py` is authoritative                                       |
| `rbac.py`               | Role definitions and permission maps                                                                     |

### Content Seeds

| File                     | Purpose                                     |
|--------------------------|---------------------------------------------|
| `blog.py`                | Demo blog posts                             |
| `listings.py`            | Demo marketplace listings                   |
| `by_laws.py`             | By-laws reference data                      |
| `compliance_items.py`    | Default compliance items                    |
| `compliance_demo.py`     | Demo compliance register                    |
| `service_provider.py`    | Demo contractors/service providers          |
| `organisations.py`       | Organisation records                        |
| `document_folders.py`    | Default document folder tree                |
| `chat_groups.py`         | Chat group definitions                      |
| `initialize_defaults.py` | Document folders + chat groups (idempotent) |

### Intelligence & Analytics Seeds

| File                                | Purpose                                            |
|-------------------------------------|----------------------------------------------------|
| `ppm_schedule.py`                   | PPM schedule entries for assets                    |
| `seed_asset_templates.py`           | PPM asset template definitions (lifts, HVAC, etc.) |
| `seed_building_summaries.py`        | Pre-computed building health summaries             |
| `seed_demo_enrichment.py`           | Demo intelligence enrichment data                  |
| `seed_demo_finance.py`              | Demo finance transactions                          |
| `seed_demo_intelligence_dataset.py` | Maintenance intelligence demo dataset              |
| `seed_demo_workorders.py`           | Demo work order data                               |
| `phase2_seed.py`                    | Trust, reconciliation, stress score demo data      |

---

## Environment Matrix

| Environment                    | Seeds to Run                                                                  |
|--------------------------------|-------------------------------------------------------------------------------|
| **Local dev (fresh)**          | `seed_all.py` then `seed_sierra.py`, `seed_harbourview.py`                    |
| **Local dev (data restore)**   | Restore from MongoDB backup, then run snapshots to ensure consistency         |
| **Staging**                    | `seed_all.py --skip-production` (skips East Gate financial data)              |
| **Production (first install)** | `seed_all.py` then restore from backup — NEVER reseed financial collections   |
| **Production (config update)** | Run individual snapshot seeds (feature_toggles, settings, navigation_configs) |
| **CI/CD tests**                | `seed_sierra.py` + `seed_harbourview.py` (use demo buildings only)            |

---

## Redundant / Deprecated Files

These files are kept for reference but should not be run in new environments:

| File                     | Status     | Replaced By                                  |
|--------------------------|------------|----------------------------------------------|
| `units.py`               | DEPRECATED | `units_east_gate.py`                         |
| `units_comprehensive.py` | DEPRECATED | `units_east_gate.py`                         |
| `feature_toggles.py`     | SUPERSEDED | `snapshot_feature_toggles.py`                |
| `navigation_configs.py`  | SUPERSEDED | `snapshot_navigation_configs.py`             |
| `enhanced_dashboard.py`  | SNAPSHOT   | Regenerate with `scripts/db/snapshot_all.py` |

---

## Production Data Protection

The following collections contain **real production financial data** for East Gate (13195). They must never be
overwritten or modified outside of the normal transaction/payment workflow:

- `unit_levy_ledger` — per-unit per-year financial ledger
- `annual_levies` — FY2021–2026 levy schedules
- `levy_payments` — payment records
- `finance` — financial transactions
- `trust_ledger_entries` — double-entry journal

If you accidentally overwrite production data, restore from the latest MongoDB backup at `~/backups/mongodb/`.
