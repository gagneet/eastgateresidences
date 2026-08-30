"""
backend/services/finance_metrics/facade.py — canonical KPI facade (Phase 1: collection rate only).

# @featuretrace:financial-metrics-canonical — orchestrates fact conversion + domain formulas.
# Layer: service
# Data flow: server.py get_building_kpis() → facade.get_current_year_collection_rate()/get_historical_year_collection_rate() → MetricResult (building-scoped).
# Related: backend/domain/finance/formulas/collection.py
#          backend/server.py get_building_kpis (the endpoint this facade now backs)

Callers pass already-fetched dollar values (see module docstring in
services/finance_metrics/__init__.py for why DB-fetch consolidation is out of
scope for Phase 1). This module converts to cents, calls the pure domain
formula, and wraps the result in a MetricResult with provenance.
"""
from __future__ import annotations

from datetime import datetime, timezone

from domain.finance.formulas.collection import (
    current_year_collection_rate,
    historical_year_collection_rate,
)
from domain.finance.metric_types import MetricResult
from services.finance_metrics.mongo_adapter import dollars_to_cents


def get_current_year_collection_rate(
    *,
    building_id: str,
    financial_year: str,
    opening_arrears_dollars: float,
    levied_dollars: float,
    outstanding_dollars: float,
) -> MetricResult:
    """levy.collection.current_year.v1 — see domain/finance/formulas/collection.py for the formula."""
    result = current_year_collection_rate(
        opening_arrears_cents=dollars_to_cents(opening_arrears_dollars),
        levied_cents=dollars_to_cents(levied_dollars),
        outstanding_cents=dollars_to_cents(outstanding_dollars),
    )
    return MetricResult(
        metric_id="levy.collection.current_year.v1",
        value=result.collection_rate_pct,
        scope=building_id,
        financial_year=financial_year,
        as_of=datetime.now(timezone.utc),
        source="mongo.unit_levy_ledger",
    )


def get_historical_year_collection_rate(
    *,
    building_id: str,
    financial_year: str,
    levied_dollars: float,
    closing_arrears_dollars: float,
) -> MetricResult:
    """levy.collection.historical_year.v1 — see domain/finance/formulas/collection.py for the formula."""
    rate = historical_year_collection_rate(
        levied_cents=dollars_to_cents(levied_dollars),
        closing_arrears_cents=dollars_to_cents(closing_arrears_dollars),
    )
    return MetricResult(
        metric_id="levy.collection.historical_year.v1",
        value=rate,
        scope=building_id,
        financial_year=financial_year,
        as_of=datetime.now(timezone.utc),
        source="mongo.unit_levy_ledger",
    )
