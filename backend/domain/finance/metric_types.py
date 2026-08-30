"""
backend/domain/finance/metric_types.py — typed result envelope for canonical metrics.

# @featuretrace:financial-metrics-canonical — MetricResult provenance envelope.
# Layer: domain
# Data flow: services/finance_metrics/facade.py → MetricResult(...) → router response (global).
# Related: backend/domain/finance/metric_registry.py
#          backend/services/finance_metrics/facade.py
#          docs/architecture/financial-calculations-deep-dive.md ("Data quality and provenance contract")

Every canonical metric the facade returns is wrapped in MetricResult so a
consumer (router, page, report) can always answer: what is this number, as of
when, calculated from which records, and should it be trusted.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Union

from pydantic import BaseModel, ConfigDict

from domain import Cents

MetricValue = Union[Cents, Decimal, int]


class MetricQuality(BaseModel):
    """Data-quality state travelling with a metric value — never silently dropped."""

    model_config = ConfigDict(frozen=True)

    is_complete: bool = True
    warnings: list[str] = []


class MetricResult(BaseModel):
    """Canonical response envelope for a single financial KPI.

    metric_id is a versioned identifier from metric_registry.py (e.g.
    "levy.collection.current_year.v1") — never a bare formula name, so a
    breaking formula change ships as a new version rather than silently
    changing what an existing consumer receives.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    metric_id: str
    value: MetricValue
    scope: str  # building_id this value is scoped to
    financial_year: str
    as_of: datetime
    source: str  # e.g. "mongo.unit_levy_ledger"
    quality: MetricQuality = MetricQuality()
