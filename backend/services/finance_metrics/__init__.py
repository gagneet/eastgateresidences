"""
backend/services/finance_metrics — canonical financial-KPI orchestration layer.

# @featuretrace:financial-metrics-canonical — facade + adapter for canonical KPIs.
# Layer: service
# Data flow: routers (server.py, finance.py) → facade.py → domain/finance/formulas (pure) → MetricResult (building-scoped).
# Related: backend/domain/finance/
#          backend/services/financial_read_service.py (the Postgres-native sibling for the
#          eventual cutover seam — see docs/architecture/financial-calculations-deep-dive.md
#          "Recommended code structure")

Phase 1 scope: this package wraps the collection-rate formulas already extracted
to backend/domain/finance/formulas/collection.py. It does NOT yet consolidate
database fetching (that's Phase 2's "request-scoped facts snapshot") — callers
still fetch their own facts (e.g. via utils.finance_helpers.get_unit_ledger_stats)
and pass already-fetched dollar values in. mongo_adapter.py's only job right now
is the dollars→cents boundary conversion.
"""
