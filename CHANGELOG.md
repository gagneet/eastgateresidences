# Changelog — Strata Management Platform

All notable changes are documented here. Dates are AEST.

---

## [2026-03-27] — Community OS: Tracks 2 & 3 — Schemas, APIs & Frontend Foundation

### Added

- 6 new MongoDB collections with indexes: `workflow_requests`, `proposals`, `savings_events`, `volunteer_events`,
  `building_summaries`, `lot_accounts`
- Building Health Score algorithm (composite 0–100 score across 5 dimensions: financial compliance, governance activity,
  maintenance response, community participation, savings performance)
- Proposals router with full voting lifecycle (create → open → vote → close → outcome)
- Savings Ledger router with category breakdown and YTD/all-time aggregation
- Volunteer events router with registration and credit application
- Smart Request (workflow requests) router with role-based triage and SLA tracking
- Community Dashboard router serving building summary and health score
- Redis Streams event emitter (graceful no-op fallback when Redis unavailable)
- Volunteer credits service with MongoDB transaction support
- Permission matrix for all new endpoints
- 5 new feature toggles: `proposals`, `savings_ledger`, `volunteer`, `building_health_score`, `smart_requests`
- Frontend: Proposals page with voting UI and proposal creation
- Frontend: Savings Ledger page with category breakdown
- Frontend: Building Health Score page with animated score ring
- Frontend: Volunteer events page with registration
- Frontend: Smart Request page with 3-step intake flow
- Frontend: `BuildingHealthScoreCard`, `SavingsYTDCard`, `ActiveProposalsCard` dashboard components
- Community OS API client layer (`frontend/src/lib/api/community-os/index.ts`)
- 42 backend unit tests (all passing)
- `GAPS_AND_FUTURE.md` with 15 tracked gaps

### Technical

- All new collections added to `TENANT_SCOPED_COLLECTIONS` for automatic `building_id` scoping
- All financial operations (volunteer credits) use MongoDB multi-document transactions
- Idempotent collection migration script at `backend/scripts/db/community_os_collections.py`
- API reference, schema reference, permissions matrix, and deployment guide added to `frontend/public/tech-docs/`
- User guides (owner, tenant, manager, guest) added to `frontend/public/user-guides/`
