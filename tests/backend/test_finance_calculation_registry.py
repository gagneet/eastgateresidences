# @featuretrace:financial-ui-calculation-register — Backend tests for registry source fields, cache metadata, and route headers.
# Data flow: pytest -> finance registry service/route -> in-memory TTL cache (scope param: building|global).
# Related: backend/services/finance_calculation_registry.py, backend/routers/finance.py
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from starlette.responses import Response

from services.finance_calculation_registry import (
    get_finance_calculation_registry,
    reset_finance_calculation_registry_cache,
)


def test_registry_consolidates_common_labels_with_source_fields():
    reset_finance_calculation_registry_cache()

    payload = get_finance_calculation_registry(building_id="13195")

    groups = {group["canonical_key"]: group for group in payload["common_label_groups"]}
    assert "levy.annual_commitment" in groups
    assert "Total Annual" in groups["levy.annual_commitment"]["similar_labels"]
    assert "annual_levies.proposed_admin_expenses" in groups["levy.annual_commitment"]["sources"]["mongodb"]
    assert "finance.levy_items.principal_cents" in groups["levy.annual_commitment"]["sources"]["postgres"]
    assert groups["levy.ledger_paid"]["canonical_label"] == "Ledger Payments Recorded"
    assert "finance.receipt_allocations.allocated_cents" in groups["levy.ledger_paid"]["sources"]["postgres"]
    assert payload["merge_path"][0]["owner"] == "backend"
    assert payload["status"] == "contract_cached_values_not_materialised"
    assert "Heavy financial values are not materialised or shared from a backend value cache yet." in (
        payload["implementation_scope"]["not_completed"]
    )
    assert "receipt_verified" in payload["cache_invalidation_events_for_future_value_cache"]
    assert "information_schema" in payload["verification"]["postgres_metadata"]


def test_registry_marks_code_supported_sources_that_were_not_live_observed():
    reset_finance_calculation_registry_cache()

    payload = get_finance_calculation_registry(building_id="13195")
    annual = next(
        group for group in payload["common_label_groups"]
        if group["canonical_key"] == "levy.annual_commitment"
    )

    assert any("not observed in sampled live building docs" in field for field in annual["sources"]["mongodb"])


def test_registry_reports_cache_miss_then_hit():
    reset_finance_calculation_registry_cache()

    first = get_finance_calculation_registry(building_id="13195")
    second = get_finance_calculation_registry(building_id="13195")

    assert first["metadata"]["cache"]["hit"] is False
    assert second["metadata"]["cache"]["hit"] is True
    assert first["metadata"]["cache"]["key"] == second["metadata"]["cache"]["key"]


@pytest.mark.asyncio
async def test_finance_registry_route_sets_cache_headers():
    from routers.finance import get_finance_ui_calculation_registry

    reset_finance_calculation_registry_cache()
    response = Response()
    user = {"id": "u1", "role": "strata_manager"}

    with patch(
        "routers.finance.get_user_permissions",
        return_value=SimpleNamespace(can_view_finances=True),
    ):
        payload = await get_finance_ui_calculation_registry(
            response=response,
            current_user=user,
            building_id="13195",
        )

    assert payload["metadata"]["building_id"] == "13195"
    assert response.headers["Cache-Control"] == "private, max-age=300"
    assert response.headers["X-Finance-Calculation-Registry-Version"] == payload["version"]
    assert response.headers["X-Finance-Calculation-Registry-Cache"] == "miss"
