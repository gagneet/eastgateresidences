from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.responses import Response


def _pg_allowed_decision():
    return MagicMock(postgres_allowed=True)


def test_legacy_trust_v1_deprecation_headers_point_to_v2_successor():
    from routers import trust_accounting

    response = Response()
    trust_accounting._attach_v1_deprecation_headers(response)

    assert response.headers["Deprecation"] == "true"
    assert response.headers["Sunset"]
    assert 'rel="successor-version"' in response.headers["Link"]
    assert "/api/trust/v2" in response.headers["Link"]
    assert "Legacy /trust API is deprecated" in response.headers["Warning"]


def test_legacy_trust_v1_deprecation_headers_are_added_to_http_exceptions():
    from routers import trust_accounting

    exc = HTTPException(status_code=404, detail="missing", headers={"X-Existing": "kept"})
    trust_accounting._attach_v1_deprecation_headers_to_exception(exc)

    assert exc.headers["X-Existing"] == "kept"
    assert exc.headers["Deprecation"] == "true"
    assert exc.headers["Sunset"]
    assert 'rel="successor-version"' in exc.headers["Link"]
    assert "/api/trust/v2" in exc.headers["Link"]
    assert "Legacy /trust API is deprecated" in exc.headers["Warning"]


@pytest.mark.asyncio
async def test_list_trust_accounts_uses_postgres_read_service_when_cutover_enabled_and_domain_ready():
    from routers import trust_accounting

    pg_accounts = [
        {
            "id": "acct-1",
            "building_id": "13195",
            "fund_type": "admin_fund",
            "account_code": "ADM",
            "account_name": "Admin Trust",
        }
    ]

    with (
        patch(
            "routers.trust_accounting.are_cutover_features_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "routers.trust_accounting.require_domain_source",
            new=AsyncMock(return_value=_pg_allowed_decision()),
        ),
        patch(
            "services.shadow_read_service.get_building_domain_shadow_readiness",
            new=AsyncMock(return_value={"total_critical_count": 0}),
        ),
        patch.object(
            trust_accounting._trust_read_service,
            "list_trust_accounts",
            new=AsyncMock(return_value=pg_accounts),
        ) as mock_pg,
    ):
        result = await trust_accounting.list_trust_accounts(
            building_id="13195",
            fund_type=None,
            current_user={"id": "u1", "role": "strata_manager"},
        )

    mock_pg.assert_awaited_once_with(building_id="13195", fund_type=None)
    assert result == {"data": pg_accounts, "count": 1}


@pytest.mark.asyncio
async def test_list_trust_accounts_falls_back_when_toggle_enabled_but_domain_blocked():
    from routers import trust_accounting

    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[])
    mock_db = MagicMock()
    mock_db.trust_accounts.find.return_value = cursor

    with (
        patch(
            "routers.trust_accounting.are_cutover_features_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "routers.trust_accounting.require_domain_source",
            new=AsyncMock(return_value=MagicMock(postgres_allowed=False)),
        ),
        patch.object(
            trust_accounting._trust_read_service,
            "list_trust_accounts",
            new=AsyncMock(return_value=[]),
        ) as mock_pg,
        patch("routers.trust_accounting._get_db", return_value=mock_db),
    ):
        result = await trust_accounting.list_trust_accounts(
            building_id="13195",
            fund_type=None,
            current_user={"id": "u1", "role": "strata_manager"},
        )

    mock_pg.assert_not_awaited()
    assert result == {"data": [], "count": 0}


@pytest.mark.asyncio
async def test_list_journal_entries_falls_back_to_legacy_when_cutover_disabled():
    from routers import trust_accounting

    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.skip.return_value = cursor
    cursor.limit.return_value = cursor

    cursor.__aiter__.return_value = iter(
        [
            {
                "_id": "mongo-entry-1",
                "building_id": "13195",
                "status": "posted",
                "date": "2026-05-01",
            }
        ]
    )

    mock_db = MagicMock()
    mock_db.trust_ledger_entries.count_documents = AsyncMock(return_value=1)
    mock_db.trust_ledger_entries.find.return_value = cursor

    with (
        patch(
            "routers.trust_accounting.are_cutover_features_enabled",
            new=AsyncMock(return_value=False),
        ),
        patch("routers.trust_accounting._get_db", return_value=mock_db),
    ):
        result = await trust_accounting.list_journal_entries(
            building_id="13195",
            fund_type=None,
            from_date=None,
            to_date=None,
            status=None,
            page=1,
            page_size=50,
            current_user={"id": "u1", "role": "strata_manager"},
        )

    assert result["total"] == 1
    assert result["data"][0]["id"] == "mongo-entry-1"


@pytest.mark.asyncio
async def test_get_fund_balance_uses_postgres_read_service_when_cutover_enabled_and_domain_ready():
    from routers import trust_accounting

    pg_balance = {
        "building_id": "13195",
        "as_at": "2026-05-07T00:00:00+00:00",
        "balances": [{"fund_type": "admin_fund", "account_type": "asset", "total_balance": 1200.0}],
    }

    with (
        patch(
            "routers.trust_accounting.are_cutover_features_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "routers.trust_accounting.require_domain_source",
            new=AsyncMock(return_value=_pg_allowed_decision()),
        ),
        patch(
            "services.shadow_read_service.get_building_domain_shadow_readiness",
            new=AsyncMock(return_value={"total_critical_count": 0}),
        ),
        patch.object(
            trust_accounting._trust_read_service,
            "get_fund_balance",
            new=AsyncMock(return_value=pg_balance),
        ) as mock_pg,
    ):
        result = await trust_accounting.get_fund_balance(
            building_id="13195",
            fund_type=None,
            current_user={"id": "u1", "role": "strata_manager"},
        )

    mock_pg.assert_awaited_once_with(building_id="13195", fund_type=None)
    assert result == pg_balance


@pytest.mark.asyncio
async def test_reconciliation_summary_uses_postgres_read_service_when_cutover_enabled_and_domain_ready():
    from routers import trust_reconciliation

    pg_summary = {
        "run_id": "run-1",
        "building_id": "13195",
        "status": "draft",
        "matched_count": 2,
        "unmatched_bank_count": 1,
        "reconciliation_confidence": 0.75,
    }

    with (
        patch(
            "routers.trust_reconciliation.are_cutover_features_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "routers.trust_reconciliation.require_domain_source",
            new=AsyncMock(return_value=_pg_allowed_decision()),
        ),
        patch.object(
            trust_reconciliation._trust_read_service,
            "get_reconciliation_summary",
            new=AsyncMock(return_value=pg_summary),
        ) as mock_pg,
    ):
        result = await trust_reconciliation.get_reconciliation_summary(
            run_id="run-1",
            current_user={"email": "manager@example.com", "role": "strata_manager", "building_id": "13195"},
        )

    mock_pg.assert_awaited_once_with(building_id="13195", run_id="run-1")
    assert result == pg_summary


@pytest.mark.asyncio
async def test_trust_accounting_guard_receives_audit_context():
    from routers import trust_accounting

    guard = AsyncMock(return_value=_pg_allowed_decision())
    with (
        patch(
            "routers.trust_accounting.are_cutover_features_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch("routers.trust_accounting.require_domain_source", new=guard),
        patch(
            "services.shadow_read_service.get_building_domain_shadow_readiness",
            new=AsyncMock(return_value={"total_critical_count": 0}),
        ),
    ):
        enabled = await trust_accounting._trust_pg_read_enabled(
            "13195",
            "trust_pg_ledger_enabled",
            "trust",
            route="GET /trust/balance",
            current_user={"id": "u1", "role": "strata_manager"},
        )

    assert enabled is True
    context = guard.await_args.kwargs["audit_context"]
    assert context.actor_user_id == "u1"
    assert context.actor_role == "strata_manager"
    assert context.route == "GET /trust/balance"
    assert context.source_service == "trust_accounting_router"
    assert context.feature_toggle_key == "trust_pg_ledger_enabled"


@pytest.mark.asyncio
async def test_trust_reconciliation_guard_receives_audit_context():
    from routers import trust_reconciliation

    guard = AsyncMock(return_value=_pg_allowed_decision())
    with (
        patch(
            "routers.trust_reconciliation.are_cutover_features_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch("routers.trust_reconciliation.require_domain_source", new=guard),
    ):
        enabled = await trust_reconciliation._trust_pg_read_enabled(
            "13195",
            "trust_reconciliation_pg_enabled",
            "trust_reconciliation",
            route="GET /trust/reconciliation/runs/{run_id}/summary",
            current_user={"id": "u1", "effective_role": "strata_manager", "role": "owner"},
        )

    assert enabled is True
    context = guard.await_args.kwargs["audit_context"]
    assert context.actor_user_id == "u1"
    assert context.actor_role == "strata_manager"
    assert context.route == "GET /trust/reconciliation/runs/{run_id}/summary"
    assert context.source_service == "trust_reconciliation_router"
    assert context.feature_toggle_key == "trust_reconciliation_pg_enabled"


# ─── GAP-FIN-049: critical-diff HARD gate on the trust PG read path ───────────

@pytest.mark.asyncio
async def test_trust_pg_refused_when_domain_has_critical_shadow_diff():
    """Even with the toggle ON and the domain-source decision allowing PG, a recorded
    critical trust shadow diff for the domain must force the read back to Mongo
    (mirrors finance_route_cutover_service's never-waivable critical gate)."""
    from routers import trust_accounting

    with (
        patch(
            "routers.trust_accounting.are_cutover_features_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "routers.trust_accounting.require_domain_source",
            new=AsyncMock(return_value=_pg_allowed_decision()),
        ),
        patch(
            "services.shadow_read_service.get_building_domain_shadow_readiness",
            new=AsyncMock(return_value={"total_critical_count": 2, "overall_status": "shadow_fail"}),
        ),
    ):
        enabled = await trust_accounting._trust_pg_read_enabled(
            "13195", "trust_pg_ledger_enabled", "trust",
            route="GET /trust/accounts", current_user={"id": "u1", "role": "strata_manager"},
        )
    assert enabled is False


@pytest.mark.asyncio
async def test_trust_pg_allowed_when_no_critical_shadow_diff():
    """Clean shadow state (zero critical) leaves the existing allow decision intact."""
    from routers import trust_accounting

    with (
        patch(
            "routers.trust_accounting.are_cutover_features_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "routers.trust_accounting.require_domain_source",
            new=AsyncMock(return_value=_pg_allowed_decision()),
        ),
        patch(
            "services.shadow_read_service.get_building_domain_shadow_readiness",
            new=AsyncMock(return_value={"total_critical_count": 0, "overall_status": "shadow_pass"}),
        ),
    ):
        enabled = await trust_accounting._trust_pg_read_enabled(
            "13195", "trust_pg_ledger_enabled", "trust",
            route="GET /trust/accounts", current_user={"id": "u1", "role": "strata_manager"},
        )
    assert enabled is True


@pytest.mark.asyncio
async def test_trust_reconciliation_pg_refused_on_critical_shadow_diff():
    """Same HARD gate on the reconciliation router's copy of the read guard."""
    from routers import trust_reconciliation

    with (
        patch(
            "routers.trust_reconciliation.are_cutover_features_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "routers.trust_reconciliation.require_domain_source",
            new=AsyncMock(return_value=_pg_allowed_decision()),
        ),
        patch(
            "services.shadow_read_service.get_building_domain_shadow_readiness",
            new=AsyncMock(return_value={"total_critical_count": 1, "overall_status": "shadow_fail"}),
        ),
    ):
        enabled = await trust_reconciliation._trust_pg_read_enabled(
            "13195", "trust_reconciliation_pg_enabled", "trust_reconciliation",
            route="GET /trust/reconciliation/runs", current_user={"id": "u1", "role": "strata_manager"},
        )
    assert enabled is False


@pytest.mark.asyncio
async def test_trust_pg_gate_fails_open_when_readiness_query_errors():
    """A readiness-query failure must NOT block PG (the domain-source decision stays the
    primary control and Mongo is always the fallback) — the gate only blocks on an
    affirmative critical count."""
    from routers import trust_accounting

    with (
        patch(
            "routers.trust_accounting.are_cutover_features_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "routers.trust_accounting.require_domain_source",
            new=AsyncMock(return_value=_pg_allowed_decision()),
        ),
        patch(
            "services.shadow_read_service.get_building_domain_shadow_readiness",
            new=AsyncMock(side_effect=RuntimeError("shadow_diffs unavailable")),
        ),
    ):
        enabled = await trust_accounting._trust_pg_read_enabled(
            "13195", "trust_pg_ledger_enabled", "trust",
            route="GET /trust/accounts", current_user={"id": "u1", "role": "strata_manager"},
        )
    assert enabled is True


# ─── Legacy V1 write-block when trust reads are PostgreSQL-primary ────────────
# These V1 endpoints write ONLY to Mongo (db.trust_ledger_*). Once
# trust_pg_ledger_enabled is on for a building its trust reads come from
# Postgres, so a Mongo-only write here would be orphaned/invisible. The guard
# _block_v1_write_if_pg_primary must refuse the write (409) in that state and
# no-op otherwise.

@pytest.mark.asyncio
async def test_block_v1_write_is_noop_when_toggle_disabled():
    from routers import trust_accounting

    with patch(
        "routers.trust_accounting.are_cutover_features_enabled",
        new=AsyncMock(return_value=False),
    ):
        # Returns None (does not raise) — legacy Mongo write path stays live.
        result = await trust_accounting._block_v1_write_if_pg_primary(
            "13195", route="POST /trust/accounts",
        )
    assert result is None


@pytest.mark.asyncio
async def test_block_v1_write_raises_409_with_deprecation_headers_when_pg_primary():
    from routers import trust_accounting

    with patch(
        "routers.trust_accounting.are_cutover_features_enabled",
        new=AsyncMock(return_value=True),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await trust_accounting._block_v1_write_if_pg_primary(
                "13195", route="POST /trust/accounts",
            )

    exc = exc_info.value
    assert exc.status_code == 409
    assert "PostgreSQL" in exc.detail and "/trust/v2" in exc.detail
    # V1 deprecation headers must ride along on the refusal.
    assert exc.headers["Deprecation"] == "true"
    assert 'rel="successor-version"' in exc.headers["Link"]


@pytest.mark.asyncio
async def test_create_trust_account_blocked_before_mongo_write_when_pg_primary():
    """The guard must fire before any Mongo write, so a PG-primary building never
    gets an orphaned trust_ledger_accounts document from the legacy V1 endpoint."""
    from routers import trust_accounting
    from models.trust_accounting import TrustAccountCreate

    payload = TrustAccountCreate(
        building_id="13195",
        fund_type="admin_fund",
        account_type="asset",
        account_code="ADM",
        account_name="Admin Trust",
    )
    mock_db = MagicMock()
    mock_db.trust_ledger_accounts.insert_one = AsyncMock()

    with (
        patch(
            "routers.trust_accounting.are_cutover_features_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch("routers.trust_accounting._get_db", return_value=mock_db),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await trust_accounting.create_trust_account(
                payload=payload,
                building_id="13195",
                current_user={"id": "u1", "full_name": "Mgr", "role": "strata_manager"},
            )

    assert exc_info.value.status_code == 409
    mock_db.trust_ledger_accounts.insert_one.assert_not_awaited()
