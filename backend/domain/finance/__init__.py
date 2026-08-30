"""
backend/domain/finance — Canonical financial-KPI formula layer.

# @featuretrace:financial-metrics-canonical — canonical financial metric formulas.
# Layer: domain
# Data flow: services/finance_metrics/facade.py → domain/finance/formulas/*.py (pure) → MetricResult (global).
# Related: backend/services/finance_metrics/facade.py
#          backend/services/finance_metrics/mongo_adapter.py
#          docs/architecture/financial_metrics_registry.md
#          docs/architecture/financial-calculations-deep-dive.md

Extends the existing backend/domain/ package (Cents type, no-float rule — see
backend/domain/__init__.py and tests/backend/test_no_floats_in_domain.py) with
KPI-facing formulas: collection rate, arrears, status distribution.

Consistent with the rest of backend/domain/: every value in this subtree is
`Cents` (int) or `Decimal` for ratios/percentages. Converting a raw Mongo
dollar-float into Cents happens ONLY in backend/services/finance_metrics/
(the adapter layer), never here.
"""
